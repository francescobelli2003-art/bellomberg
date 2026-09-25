"""Explicit current-context review of a previously compiled economic plan.

No driver is renewed by default. A reuse decision retains its sourced values,
observation dates and facts; only an explicitly reviewed same-day policy advances.
Source-stated expiry dates cannot be extended by this protocol.
"""
from copy import deepcopy
from collections import Counter
import json

from .preparation_seed import _digest, make_seed


REFRESH_SYSTEM = """
This request is an explicit refresh review, identified by contract.preparation_refresh.
For this mode return ONLY {"reviews":{requested_driver:decision},"rationale":"..."}.
Reassess the prior_plan against ALL relevant current and retained dated evidence.
For each requested driver choose reuse, replace, or unavailable. Reuse requires the
exact supplied source_driver_sha256, a current review_rationale and evidence_ids
supporting that review. Reuse affirms the COMPLETE prior driver, including its
economic rationale, values and fact proofs. If any of those need to change, return
replace with a complete new driver following the ordinary economic contract.
An unchanged source is not proof that its dependent judgment is still appropriate.
Consider new discount-rate, business, guidance and capital evidence explicitly;
reconcile affected forecasts, continuing cash flows, terminal values and narratives.
Never relabel old observations as newly acquired facts. Reuse only advances an
explicitly reviewed same_day policy; it cannot extend a source-stated expiry.
Unavailable requires its precise missing source/reconciliation in reason and stops
the review. The perimeter, calendar and legal structure must remain identical;
changing them requires fresh preparation, not relabelling old forecast periods.
reviewed_plan contains completed current scopes and must remain consistent.
plan_projection declares value-only views of other scopes: their omitted proof
and driver rationale are not new visible evidence. The requested prior scope and
the opening model remain complete (the latter moves to reviewed_plan after its
review). Completed prior scopes are omitted once their reviewed values are shown.
Review all scenario narratives and values for
cross-scenario coherence; never infer that an omitted rationale was reaffirmed.
Scenario rationale is a complete current explanation of that scenario, not merely
a list of changes. A review is automatic and never a human approval.
"""


COMPACT_REFRESH_SYSTEM = """
contract.preparation_refresh.compact_wire selects the compact review protocol.
Return a reviews OBJECT keyed by the decimal zero-based driver reference, e.g.
"0", "1", "2", using every index in drivers exactly once. Each key selects the
corresponding name AND exact prior hash in driver_hashes; it is not a new value
or an unspecified default. For reuse return action, review_rationale and
evidence_refs (zero-based indices into
sources). Keep a concise, driver-specific explanation of the current review;
do not reprint the old rationale or source text. Source references identify only
the existing documents: they do not make omitted text visible evidence.
For replace return action and the complete ordinary driver envelope, with full
source IDs and all required proof. For unavailable return action and reason.
No duplicate, aliased or omitted driver/source references. Return the
complete current scenario rationale once. All economic controls still apply.
"""


def _compact_refs(stage):
    refs = stage['compact_wire']
    if (not isinstance(refs, dict) or set(refs) != {'version', 'drivers', 'sources'}
            or type(refs['version']) is not int or refs['version'] != 1
            or refs['drivers'] != sorted(stage['driver_hashes'])
            or not isinstance(refs['sources'], list) or not refs['sources']
            or any(not isinstance(ident, str) or not ident for ident in refs['sources'])
            or len(set(refs['sources'])) != len(refs['sources'])):
        raise ValueError('invalid compact refresh reference tables')
    return refs


def _expand_compact_review(answer, stage):
    """Decode only explicit references; never fill a missing review or evidence."""
    refs = _compact_refs(stage)
    if (not isinstance(answer, dict) or set(answer) != {'reviews', 'rationale'}
            or not isinstance(answer['reviews'], dict)
            or set(answer['reviews']) != {str(i) for i in range(len(refs['drivers']))}):
        raise ValueError('compact refresh requires exactly one decision per driver')
    choices = {}
    for reference, item in answer['reviews'].items():
        if not isinstance(item, dict):
            raise ValueError('compact driver decision must be a structured object')
        name = refs['drivers'][int(reference)]
        action = item.get('action')
        if action == 'reuse' and set(item) == {'action', 'review_rationale', 'evidence_refs'}:
            ids = item['evidence_refs']
            if (not isinstance(ids, list) or not ids
                    or any(type(i) is not int or not 0 <= i < len(refs['sources']) for i in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError('compact evidence references missing, duplicated or outside request table')
            choices[name] = {'action': action, 'source_driver_sha256': stage['driver_hashes'][name],
                             'review_rationale': item['review_rationale'],
                             'evidence_ids': [refs['sources'][i] for i in ids]}
        elif action == 'replace' and set(item) == {'action', 'driver'}:
            choices[name] = {'action': action, 'driver': deepcopy(item['driver'])}
        elif action == 'unavailable' and set(item) == {'action', 'reason'}:
            choices[name] = {'action': action, 'reason': item['reason']}
        else:
            raise ValueError('unknown or incomplete compact refresh decision')
    return {'reviews': choices, 'rationale': answer['rationale']}


def refresh_response_format(contract):
    from .preparation_ai import response_format
    stage = contract['preparation_refresh']
    narrowed = deepcopy(contract)
    narrowed.pop('preparation_refresh')
    narrowed['preparation_stage'] = {'scope': stage['scope'], 'drivers': list(stage['driver_hashes']),
                                    'terminal_projection_version': 2}
    originals = response_format(narrowed)['json_schema']['schema']['properties']['drivers']['properties']
    def obj(fields):
        return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}
    text = {'type': 'string', 'minLength': 1}
    refs = _compact_refs(stage) if 'compact_wire' in stage else None
    choices = {}
    for name, digest in stage['driver_hashes'].items():
        reuse = obj({'action': {'const': 'reuse'},
            **({'review_rationale': text, 'evidence_refs': {'type': 'array', 'minItems': 1,
                'items': {'type': 'integer', 'minimum': 0, 'maximum': len(refs['sources']) - 1}}} if refs else
               {'source_driver_sha256': {'const': digest}, 'review_rationale': text,
                'evidence_ids': {'type': 'array', 'minItems': 1, 'items': text}})})
        key = str(refs['drivers'].index(name)) if refs else name
        choices[key] = {'anyOf': [*([] if name in stage.get('reuse_ineligible', {}) else [reuse]),
            obj({'action': {'const': 'replace'}, 'driver': originals[name]}),
            obj({'action': {'const': 'unavailable'}, 'reason': text})]}
    reviews = obj(choices)
    return {'type': 'json_schema', 'json_schema': {'name': 'valuation_refresh_scope', 'strict': False,
            'schema': _shared_schema(obj({'reviews': reviews, 'rationale': text}))}}


def _shared_schema(schema):
    """Factor identical schema nodes without removing a single constraint.

    Only JSON-Schema child positions are traversed; property maps and ordinary
    values are never replaced by references. Acyclic $defs/$ref are documented:
    https://dev.meta.ai/docs/structured-output#stay-within-schema-constraints
    """
    from .preparation_ai import _json
    counts = Counter()
    def children(node):
        yield from node.get('properties', {}).values()
        for key in ('items', 'additionalProperties'):
            if isinstance(node.get(key), dict):
                yield node[key]
        yield from node.get('anyOf', [])
    def collect(node):
        if '$defs' in node or '$ref' in node:
            raise ValueError('schema sharing requires an unreferenced finite input schema')
        encoded = _json(node)
        if len(encoded) >= 128:
            counts[encoded] += 1
        for child in children(node):
            collect(child)
    collect(schema)
    names = {encoded: 's' + str(i) for i, encoded in enumerate(sorted(k for k, count in counts.items() if count > 1))}
    def rewrite(node, *, definition=False):
        encoded = _json(node)
        if not definition and encoded in names:
            return {'$ref': '#/$defs/' + names[encoded]}
        result = deepcopy(node)
        if 'properties' in result:
            result['properties'] = {key: rewrite(value) for key, value in node['properties'].items()}
        for key in ('items', 'additionalProperties'):
            if isinstance(node.get(key), dict):
                result[key] = rewrite(node[key])
        if 'anyOf' in result:
            result['anyOf'] = [rewrite(value) for value in node['anyOf']]
        return result
    result = rewrite(schema, definition=True)
    if names:
        result['$defs'] = {name: rewrite(json.loads(encoded), definition=True) for encoded, name in names.items()}
    return result


def _compiled(seed, dossier, contract):
    from .preparation_ai import StagedProposer
    def missing(*_args):
        raise ValueError('refresh requires a complete previously compiled plan')
    return StagedProposer(missing, seed=seed)(dossier, contract)


def _without_review_day(contract, cutoff):
    result = deepcopy(contract)
    expected = {'policy': 'same_day', 'as_of': cutoff}
    envelope = result.get('driver_envelope', {})
    if (result.get('expiry_policy') != expected or envelope.get('valid_until') != cutoff
            or envelope.get('valid_until_basis') != expected):
        raise ValueError('refresh requires the exact common same-day review contract')
    result['expiry_policy'] = None
    envelope['valid_until'] = envelope['valid_until_basis'] = None
    return result


def refresh_context(seed, previous_dossier, previous_contract, dossier, contract):
    """Validate both identities before any review request may spend money."""
    from .preparation_ai import _model_dossier
    from .input_preparation import _catalog, _day
    old_day, day = _day(previous_dossier.get('as_of')), _day(dossier.get('as_of'))
    if old_day is None or day is None or day < old_day:
        raise ValueError('refresh cutoff cannot precede the original review')
    for key in ('ticker', 'method_id'):
        if not dossier.get(key) or dossier[key] != previous_dossier.get(key):
            raise ValueError('refresh issuer or method changed')
    for key in ('profile_id', 'profile_version', 'method_version', 'instrument'):
        if dossier.get('decision', {}).get(key) != previous_dossier.get('decision', {}).get(key):
            raise ValueError('refresh economic profile changed')
    if _digest(_without_review_day(contract, day.isoformat())) != _digest(
            _without_review_day(previous_contract, old_day.isoformat())):
        raise ValueError('refresh economic contract changed; fresh preparation required')
    original = _compiled(seed, previous_dossier, previous_contract)
    old_catalog, old_issues, _ = _catalog(previous_dossier.get('documents'), old_day)
    catalog, issues, _ = _catalog(dossier.get('documents'), day)
    if old_issues or issues:
        raise ValueError('refresh source catalog invalid')
    return {'plan': original, 'catalog': catalog, 'previous_catalog': old_catalog,
            'previous_dossier': previous_dossier,
            'identity': {'source_plan_sha256': seed['plan_sha256'],
                         'current_dossier_sha256': _digest(_model_dossier(dossier)),
                         'current_contract_sha256': _digest(contract)}}


def apply_refresh_review(seed, previous_dossier, previous_contract, dossier, contract, review, *, view):
    """Recompile every explicitly reviewed driver; never approve or publish it.

    The caller retains the actual AI response and this lineage in the recorded
    generation. Hashes establish identity, not authorship or an economic opinion.
    A changed perimeter/calendar/legal structure requires fresh preparation: old
    values are never silently relabelled onto new entities or forecast periods.
    """
    context = refresh_context(seed, previous_dossier, previous_contract, dossier, contract)
    identity = context['identity']
    if (not isinstance(review, dict) or set(review) != set(identity) | {'reviews', 'scenario_rationale'}
            or any(review[key] != value for key, value in identity.items())):
        raise ValueError('invalid refresh review envelope or context identity')
    original = context['plan']
    scopes = ['model', *contract['scenarios']]
    decisions = review['reviews']
    rationales = review['scenario_rationale']
    if (not isinstance(decisions, dict) or set(decisions) != set(scopes)
            or not isinstance(rationales, dict) or set(rationales) != set(contract['scenarios'])
            or any(not isinstance(value, str) or not value.strip() for value in rationales.values())):
        raise ValueError('refresh requires every scope and current scenario rationale')
    candidate = {'model': {}, 'scenarios': {scope: {} for scope in contract['scenarios']},
                 'scenario_rationale': deepcopy(rationales)}
    reused, replaced = [], []
    cutoff = dossier['as_of']
    for scope in scopes:
        target, kept, changed = _review_scope(context, scope, decisions[scope], dossier, contract, view)
        if scope == 'model':
            candidate['model'] = target
        else:
            candidate['scenarios'][scope] = target
        reused.extend(kept)
        replaced.extend(changed)
    for name in ('perimeter', 'calendar', 'legal_structure'):
        if candidate['model'].get(name, {}).get('value') != original['model'].get(name, {}).get('value'):
            raise ValueError('refresh perimeter/calendar/legal structure changed; fresh preparation required')
    renewed = make_seed(dossier, contract, candidate)
    _compiled(renewed, dossier, contract)
    return renewed, {**identity, 'plan_sha256': renewed['plan_sha256'], 'review_sha256': _digest(review),
                     'reviewed_as_of': cutoff, 'reused_drivers': reused, 'replaced_drivers': replaced,
                     'method_compiler_verified': True, 'valuation_engine_required': True, 'human_approved': False}


def _changed_sources(context, driver):
    """Apply the same source-identity rule before and after a paid decision."""
    def cited(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'evidence_ids':
                    yield from child
                else:
                    yield from cited(child)
        elif isinstance(value, list):
            for child in value:
                yield from cited(child)
    keys = ('url', 'sha256', 'document_sha256', 'published_at',
            'available_at', 'availability_basis', 'text')
    changed = {}
    for ident in sorted(set(cited(driver))):
        old, current = context['previous_catalog'][ident], context['catalog'].get(ident)
        fields = ['missing_source'] if current is None else [key for key in keys if old.get(key) != current.get(key)]
        if fields:
            changed[ident] = fields
    return changed


def _review_scope(context, scope, choices, dossier, contract, view):
    from .input_preparation import _day
    from .preparation_ai import verify_visible_citations
    original = context['plan']
    previous = original['model'] if scope == 'model' else original['scenarios'][scope]
    previous_dossier = context['previous_dossier']
    target, reused, replaced = {}, [], []
    cutoff = dossier['as_of']
    if not isinstance(choices, dict) or set(choices) != set(previous):
        raise ValueError('refresh must explicitly review every driver in ' + scope)
    for name, choice in choices.items():
        if not isinstance(choice, dict):
            raise ValueError('refresh decision must be a structured object')
        prior = previous[name]
        if choice.get('action') == 'reuse':
            if (set(choice) != {'action', 'source_driver_sha256', 'review_rationale', 'evidence_ids'}
                    or choice['source_driver_sha256'] != _digest(prior)
                    or not isinstance(choice['review_rationale'], str) or not choice['review_rationale'].strip()):
                raise ValueError('explicit refresh reuse identity and rationale required')
            ids = choice['evidence_ids']
            if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
                    or len(set(ids)) != len(ids) or any(i not in context['catalog'] for i in ids)):
                raise ValueError('refresh review cites missing or ambiguous evidence')
            verify_visible_citations({'review': {'evidence_ids': ids}}, view, dossier)
            changes = _changed_sources(context, prior)
            if changes:
                raise ValueError('reused driver source changed or disappeared; explicit replacement required: '
                                 + scope + '.' + name + ' ' + str(changes))
            item = deepcopy(prior)
            policy = item.get('valid_until_basis')
            if policy == {'policy': 'same_day', 'as_of': previous_dossier['as_of']}:
                item.update(valid_until=cutoff, valid_until_basis=deepcopy(contract['expiry_policy']))
            elif _day(item.get('valid_until')) is None or _day(item['valid_until']) < _day(cutoff):
                raise ValueError('source-stated expiry cannot be renewed by analyst review')
            item['rationale'] += '\nReview as of ' + cutoff + ': ' + choice['review_rationale']
            reused.append({'scope': scope, 'driver': name, 'source_driver_sha256': _digest(prior),
                           'driver_sha256': _digest(item), 'review_rationale': choice['review_rationale'],
                           'review_evidence_ids': deepcopy(ids)})
        elif choice.get('action') == 'replace' and set(choice) == {'action', 'driver'}:
            item = deepcopy(choice['driver'])
            if not isinstance(item, dict) or 'value' not in item:
                raise ValueError('refresh replacement requires a complete driver')
            replaced.append({'scope': scope, 'driver': name, 'source_driver_sha256': _digest(prior),
                             'driver_sha256': _digest(item)})
        else:
            raise ValueError('unknown or incomplete refresh decision')
        verify_visible_citations({name: item}, view, dossier)
        target[name] = item
    return target, reused, replaced


def _validate_reviewed_prefix(plan, dossier, contract):
    """Use the existing staged compiler before the next scope may spend."""
    from .preparation_ai import StagedProposer
    class PendingReview(Exception):
        pass
    def pending(*_args):
        raise PendingReview()
    try:
        StagedProposer(pending, seed=make_seed(dossier, contract, plan))(dossier, contract)
    except PendingReview:
        pass


def _plan_scope_view(plan, complete_scope=None):
    """Preserve opening and scenario rationales, with other driver values pinned."""
    result = deepcopy(plan)
    for name, drivers in plan['scenarios'].items():
        if name != complete_scope:
            result['scenarios'][name] = {key: {'value': deepcopy(item['value']), 'kind': item['kind'],
                'source_driver_sha256': _digest(item)} for key, item in drivers.items()}
    return result


def _completed_plan_view(context, contract):
    """Reuse the review projection without changing the caller's compiled plan."""
    scope = (contract.get('preparation_stage') or {}).get('scope')
    plan = context.get('completed_plan')
    if scope not in ('bear', 'base', 'bull') or not isinstance(plan, dict) or not isinstance(plan.get('scenarios'), dict):
        return context
    view = deepcopy(context)
    view['completed_plan'] = _plan_scope_view(plan, scope)
    view.setdefault('stage_view', {})['completed_plan_projection'] = {'original_plan_sha256': _digest(plan), 'complete_scope': scope,
        'limitation': 'Opening, current scope and all scenario rationales remain complete. Other completed scenario drivers retain values/kinds/hashes; their proofs and driver rationales are omitted from this view, not deleted or recertified. Original compiled plan remains binding.'}
    return view


def _plan_views(original, reviewed, scope):
    """Keep requested drivers complete; declare other-scope value projections."""
    prior_view = _plan_scope_view(original, scope)
    completed = [name for name, values in reviewed['scenarios'].items() if values]
    for name in completed:
        prior_view['scenarios'][name] = {}
        prior_view['scenario_rationale'].pop(name)
    if scope != 'model':
        prior_view['model'] = {}
    return {'prior_plan': prior_view, 'reviewed_plan': _plan_scope_view(reviewed),
            'plan_projection': {'prior_plan_sha256': _digest(original), 'reviewed_plan_sha256': _digest(reviewed),
                'complete_prior_scope': scope, 'opening_model_complete': True,
                'opening_model_location': 'prior_plan.model' if scope == 'model' else 'reviewed_plan.model',
                'completed_prior_scopes_omitted': completed,
                'other_scenario_drivers': 'Values, kinds and exact driver hashes only. Driver proof, source IDs, validity and rationale are omitted from these projections; original immutable plans remain intact. Complete scenario rationales are retained. Omitted fields cannot support new claims.'}}


def _review_view(dossier, excerpt_manifest):
    """Retain documents and gaps; declare omitted provider/acquisition payloads."""
    from .preparation_view import select_stage_view
    return _acquisition_payload_view(select_stage_view(dossier, 'forecast', excerpt_manifest=excerpt_manifest))


def _acquisition_payload_view(context):
    """Copy a prompt view, retaining source text, coverage and prior omissions."""
    from .preparation_ai import _json
    view = deepcopy(context)
    omissions = view.get('review_view_omissions', [])
    for name in ('profile', 'financials', 'filings'):
        source = view.get('acquired_sources', {}).get(name)
        if isinstance(source, dict) and 'data' in source:
            omitted = source.pop('data')
            omissions.append({'field': 'acquired_sources.' + name + '.data', 'sha256': _digest(omitted),
                'utf8_bytes': len(_json(omitted).encode()), 'reason': 'Raw provider payload omitted from review view; status/date/source ID retained. It cannot support new claims; use the visible authenticated documents.'})
    report = view.get('document_acquisition')
    if isinstance(report, dict) and 'acquired_document_index' in report:
        omitted = report.pop('acquired_document_index')
        omissions.append({'field': 'document_acquisition.acquired_document_index', 'sha256': _digest(omitted),
            'utf8_bytes': len(_json(omitted).encode()), 'reason': 'Acquisition index omitted from review view; source documents, overall coverage and issues retained. Index-only observations are not visible evidence.'})
    view['review_view_omissions'] = omissions
    return view


class StructuralRefreshRequired(ValueError):
    """A reviewed opening changed structure; old scenario paths cannot follow it."""
    def __init__(self, model, review):
        self.model, self.review = deepcopy((model, review))
        super().__init__('refresh structural change requires fresh preparation')


class RefreshProposer:
    """Four explicit economic scopes over the caller's existing paid journal."""
    def __init__(self, proposer, seed, previous_dossier, previous_contract, *, excerpt_manifest=None,
                 compact_scopes=(), repair_terminal=False):
        if type(repair_terminal) is not bool:
            raise ValueError('repair_terminal must be an explicit boolean')
        if (not isinstance(compact_scopes, (list, tuple))
                or any(not isinstance(scope, str) for scope in compact_scopes)
                or len(set(compact_scopes)) != len(compact_scopes)):
            raise ValueError('compact scopes require an explicit unique list')
        self.proposer = proposer
        self.seed, self.previous_dossier, self.previous_contract = deepcopy((seed, previous_dossier, previous_contract))
        self.excerpt_manifest = deepcopy(excerpt_manifest)
        self.compact_scopes = tuple(compact_scopes)
        self.repair_terminal = repair_terminal
        self.refresh_lineage = None

    def __call__(self, dossier, contract):
        from .preparation_ai import _model_dossier
        from .input_preparation import _bank_schema
        from .bank_stage_arithmetic import BankTerminalMismatch
        self.refresh_lineage = None
        if set(self.compact_scopes) - {'model', *contract['scenarios']}:
            raise ValueError('unknown compact refresh scope')
        context = refresh_context(self.seed, self.previous_dossier, self.previous_contract, dossier, contract)
        original = context['plan']
        schema = contract['schema']
        if contract.get('bank_dynamic_capital'):
            schema, _, issues = _bank_schema(original, original['model']['perimeter']['value'])
            if issues:
                raise ValueError('refresh bank schema incomplete')
        plan = {'model': {}, 'scenarios': {scope: {} for scope in contract['scenarios']}, 'scenario_rationale': {}}
        review = {**context['identity'], 'reviews': {}, 'scenario_rationale': {}}
        requests = []
        for scope in ['model', *contract['scenarios']]:
            drivers = original['model'] if scope == 'model' else original['scenarios'][scope]
            narrowed = deepcopy(contract)
            narrowed['schema'] = deepcopy(schema)
            narrowed['preparation_refresh'] = {**context['identity'], 'scope': scope,
                                               'driver_hashes': {name: _digest(item) for name, item in drivers.items()}}
            if scope in self.compact_scopes:
                narrowed['preparation_refresh']['compact_wire'] = {
                    'version': 1, 'drivers': sorted(drivers), 'sources': sorted(context['catalog'])}
            ineligible = {name: {'required_action': 'replace_or_unavailable', 'changed_sources': changes}
                          for name, item in drivers.items() if (changes := _changed_sources(context, item))}
            if ineligible:
                narrowed['preparation_refresh']['reuse_ineligible'] = ineligible
                narrowed['preparation_refresh']['coordination_notice'] = (
                    'These drivers cannot use reuse because cited source identity fields changed. '
                    'Return complete replacements reflecting current evidence, even if an economic value '
                    'is retained after review, or declare unavailable. Other pending scenarios in prior_plan '
                    'are not immutable: their subsequent reviews must reconcile shared assumptions. '
                    'Do not defer a necessary source-driven update solely to match a pending scenario. '
                    'Already completed scopes in reviewed_plan remain binding.')
            view = _review_view(dossier, self.excerpt_manifest)
            view.update(_plan_views(original, plan, scope))
            project_context = getattr(self.proposer, 'prepare_context', None)
            if callable(project_context):
                view = project_context(view, deepcopy(narrowed), source_dossier=dossier,
                                       allow_selection=self.excerpt_manifest is None)
            wire_answer = self.proposer(deepcopy(view), deepcopy(narrowed))
            answer = (_expand_compact_review(wire_answer, narrowed['preparation_refresh'])
                      if scope in self.compact_scopes else wire_answer)
            if (not isinstance(answer, dict) or set(answer) != {'reviews', 'rationale'}
                    or not isinstance(answer['rationale'], str) or not answer['rationale'].strip()):
                raise ValueError('incomplete refresh scope response: ' + scope)
            choices = answer['reviews']
            if isinstance(choices, dict):
                for name, choice in choices.items():
                    if isinstance(choice, dict) and choice.get('action') == 'unavailable':
                        raise ValueError('refresh source gap ' + scope + '.' + name + ': ' + str(choice.get('reason')))
            values, _, _ = _review_scope(context, scope, choices, dossier, contract, view)
            if scope == 'model':
                plan['model'] = values
                for name in ('perimeter', 'calendar', 'legal_structure'):
                    if values.get(name, {}).get('value') != original['model'].get(name, {}).get('value'):
                        raise StructuralRefreshRequired(values, {'wire_response': deepcopy(wire_answer),
                            'answer_sha256': _digest(wire_answer), 'dossier_sha256': _digest(_model_dossier(view)),
                            'contract_sha256': _digest(narrowed),
                            **({'compact_wire': deepcopy(narrowed['preparation_refresh']['compact_wire'])}
                               if scope in self.compact_scopes else {})})
            else:
                plan['scenarios'][scope] = values
                plan['scenario_rationale'][scope] = answer['rationale']
                review['scenario_rationale'][scope] = answer['rationale']
            repair = None
            try:
                _validate_reviewed_prefix(plan, dossier, contract)
            except BankTerminalMismatch as error:
                if not self.repair_terminal or error.scope != scope:
                    raise
                from .preparation_refresh_repair import request_terminal_repair
                answer, repair = request_terminal_repair(self.proposer, error, answer, wire_answer,
                                                        plan, view, narrowed)
                choices = answer['reviews']
                values, _, _ = _review_scope(context, scope, choices, dossier, contract, view)
                plan['scenarios'][scope] = values
                plan['scenario_rationale'][scope] = answer['rationale']
                review['scenario_rationale'][scope] = answer['rationale']
                _validate_reviewed_prefix(plan, dossier, contract)
            review['reviews'][scope] = deepcopy(choices)
            requests.append({'scope': scope, 'dossier_sha256': _digest(_model_dossier(view)),
                             'contract_sha256': _digest(narrowed), 'answer_sha256': _digest(wire_answer),
                             **({'arithmetic_repair': repair} if repair else {}),
                             **({'compact_wire': deepcopy(narrowed['preparation_refresh']['compact_wire']),
                                 'wire_response': deepcopy(wire_answer)} if scope in self.compact_scopes else {})})
        renewed, lineage = apply_refresh_review(self.seed, self.previous_dossier, self.previous_contract,
                                                dossier, contract, review, view=dossier)
        self.refresh_lineage = {**lineage, 'requests': requests, 'review': deepcopy(review)}
        return renewed['plan']
