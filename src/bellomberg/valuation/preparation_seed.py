"""Explicit candidate checkpoints, never paid-cache aliases or human approvals.

Hashes bind a candidate to its source context and contract; they do not prove who
authored it. Callers retain original responses and revision provenance separately.
The staged preparer must recompile every supplied driver before continuing.
"""
from copy import deepcopy
from hashlib import sha256


def _digest(value):
    from .preparation_ai import _json
    return sha256(_json(value).encode('utf-8')).hexdigest()


def make_seed(dossier, contract, plan):
    """Package a candidate for later validation; this does not accept its values."""
    from .preparation_ai import _model_dossier
    return {'version': 1, 'dossier_sha256': _digest(_model_dossier(dossier)),
            'contract_sha256': _digest(contract), 'plan_sha256': _digest(plan),
            'plan': deepcopy(plan)}


def restore_seed(seed, dossier, contract):
    from .preparation_ai import _model_dossier, verify_visible_citations
    if (not isinstance(seed, dict) or set(seed) != {
            'version', 'dossier_sha256', 'contract_sha256', 'plan_sha256', 'plan'}
            or type(seed['version']) is not int or seed['version'] != 1):
        raise ValueError('invalid proposal seed envelope')
    for key, value in (('dossier', _model_dossier(dossier)), ('contract', contract), ('plan', seed['plan'])):
        if seed[key + '_sha256'] != _digest(value):
            raise ValueError('proposal seed ' + key + ' identity changed')
    plan = deepcopy(seed['plan'])
    scopes = set(contract['scenarios'])
    if (not isinstance(plan, dict) or set(plan) != {'model', 'scenarios', 'scenario_rationale'}
            or not isinstance(plan['model'], dict)
            or not isinstance(plan['scenarios'], dict) or set(plan['scenarios']) != scopes
            or any(not isinstance(value, dict) for value in plan['scenarios'].values())
            or not isinstance(plan['scenario_rationale'], dict)
            or set(plan['scenario_rationale']) != {scope for scope, values in plan['scenarios'].items() if values}
            or any(not isinstance(value, str) or not value.strip() for value in plan['scenario_rationale'].values())):
        raise ValueError('invalid proposal seed plan or rationale')
    for values in [plan['model'], *plan['scenarios'].values()]:
        if any(not isinstance(value, dict) or 'value' not in value for value in values.values()):
            raise ValueError('invalid proposal seed driver')
        verify_visible_citations(values, dossier, dossier)
    return plan


def verify_prefix(plan, stages):
    """Reject partial or out-of-order groups before any missing stage can spend."""
    gap = False
    for scope, names in stages:
        present = set(names) & plan['scenarios'][scope].keys()
        if present and (gap or present != set(names)):
            raise ValueError('proposal seed must end at a complete consecutive stage')
        gap = gap or not present


def apply_revision(seed, dossier, contract, revision, *, view):
    """Merge explicit scenario edits; return an unvalidated candidate and lineage.

    References address only the original seed, never edits in this revision.
    The caller must retain the AI response and revalidate with StagedProposer;
    this function neither approves a plan nor makes a paid-cache entry.
    """
    from .preparation_ai import verify_visible_citations
    original = restore_seed(seed, dossier, contract)
    if (not isinstance(revision, dict) or set(revision) != {
            'source_plan_sha256', 'replacements', 'reuse', 'scenario_rationale'}
            or revision['source_plan_sha256'] != seed['plan_sha256']
            or not isinstance(revision['replacements'], dict)
            or not isinstance(revision['reuse'], list)
            or not isinstance(revision['scenario_rationale'], dict)):
        raise ValueError('invalid proposal revision envelope or source identity')
    schema = contract['schema']
    if contract.get('bank_dynamic_capital'):
        from .input_preparation import _bank_schema
        perimeter = original['model'].get('perimeter', {}).get('value')
        if not isinstance(perimeter, dict):
            raise ValueError('proposal revision requires bank perimeter')
        schema, _, issues = _bank_schema(original, perimeter)
        if issues:
            raise ValueError('proposal revision requires valid bank legal structure')
    allowed = {name for name, descriptor in schema.items() if descriptor[-1] == 'scenario'}
    candidate = deepcopy(original)
    targets, replaced, reused = set(), [], []

    def valid_scope(scope):
        return isinstance(scope, str) and scope in original['scenarios']

    def assign(scope, name, item):
        if not valid_scope(scope) or not isinstance(name, str) or name not in allowed:
            raise ValueError('unknown revision scenario or driver')
        if (scope, name) in targets:
            raise ValueError('duplicate revision target')
        previous = original['scenarios'][scope].get(name)
        if previous is not None and previous.get('kind') == 'historical':
            raise ValueError('revision cannot overwrite an existing historical driver')
        if not isinstance(item, dict) or not {
                'value', 'kind', 'evidence_ids', 'rationale', 'valid_until', 'valid_until_basis'} <= set(item):
            raise ValueError('revision requires a complete driver envelope')
        verify_visible_citations({name: item}, view, dossier)
        targets.add((scope, name))
        candidate['scenarios'][scope][name] = deepcopy(item)
        return {'scope': scope, 'driver': name,
                'previous_driver_sha256': _digest(previous) if previous is not None else None,
                'driver_sha256': _digest(item)}

    for scope, values in revision['replacements'].items():
        if not valid_scope(scope) or not isinstance(values, dict):
            raise ValueError('invalid revision replacements')
        for name, item in values.items():
            replaced.append(assign(scope, name, item))
    for reference in revision['reuse']:
        if (not isinstance(reference, dict) or set(reference) != {
                'scope', 'source_scope', 'driver', 'source_driver_sha256', 'rationale'}
                or not valid_scope(reference['source_scope'])
                or not valid_scope(reference['scope'])
                or reference['scope'] == reference['source_scope']
                or not isinstance(reference['driver'], str)
                or reference['driver'] not in allowed
                or not isinstance(reference['rationale'], str) or not reference['rationale'].strip()):
            raise ValueError('invalid explicit driver reuse reference')
        source = original['scenarios'][reference['source_scope']].get(reference['driver'])
        if source is None or _digest(source) != reference['source_driver_sha256']:
            raise ValueError('reuse requires an unchanged driver in the original seed')
        item = deepcopy(source)
        item['rationale'] = reference['rationale']
        lineage = assign(reference['scope'], reference['driver'], item)
        reused.append({**lineage, 'source_scope': reference['source_scope'],
                       'source_driver_sha256': _digest(source)})
    affected = {scope for scope, _ in targets}
    rationales = revision['scenario_rationale']
    if (not affected or set(rationales) != affected
            or any(not isinstance(value, str) or not value.strip() for value in rationales.values())):
        raise ValueError('revision needs a complete rationale for exactly the affected scenarios')
    candidate['scenario_rationale'].update(deepcopy(rationales))
    result = make_seed(dossier, contract, candidate)
    return result, {'source_plan_sha256': seed['plan_sha256'], 'plan_sha256': result['plan_sha256'],
                    'revision_sha256': _digest(revision), 'replaced_drivers': replaced,
                    'reused_drivers': reused, 'validation_required': True, 'human_approved': False}
