"""One explicit AI correction of terminal arithmetic, preserving other decisions."""
from copy import deepcopy

from .preparation_seed import _digest


REPAIR_SYSTEM = """
contract.preparation_refresh.arithmetic_repair selects one bounded correction.
The prior response failed deterministic terminal arithmetic. Review only the
requested drivers. Return complete replace decisions, or unavailable with the
precise reason; reuse is forbidden. The calculated targets are engine outputs
from the fixed proposed assumptions, not new facts or human approval. Preserve
their precision; do not invent or change another economic input to make them fit.
failed_review shows the current candidate values and the requested drivers in
full; omitted proof and rationale are not new visible evidence. All unrequested
decisions remain fixed. Return the complete current scenario rationale, correcting
any associated arithmetic explanation. The entire result will be recompiled.
"""


def request_terminal_repair(proposer, error, answer, wire_answer, plan, view, contract):
    from .preparation_ai import _model_dossier
    from .preparation_refresh import _expand_compact_review
    scope, targets = error.scope, deepcopy(error.targets)
    narrowed, selected = deepcopy(contract), deepcopy(view)
    stage = narrowed['preparation_refresh']
    if (scope != stage['scope'] or not targets
            or set(targets) - {'capital.terminal_equity', 'capital.terminal_debt'}):
        raise ValueError('terminal repair outside the requested scope')
    stage['driver_hashes'] = {name: stage['driver_hashes'][name] for name in sorted(targets)}
    if 'compact_wire' in stage:
        stage['compact_wire']['drivers'] = sorted(targets)
    stage['reuse_ineligible'] = {name: {'required_action': 'replace_or_unavailable',
        'reason': 'Declared amount differs from deterministic terminal arithmetic.'} for name in targets}
    stage['arithmetic_repair'] = {'attempt': 1, 'targets': targets,
        'invalid_answer_sha256': _digest(wire_answer), 'candidate_plan_sha256': _digest(plan)}
    drivers = plan['scenarios'][scope]
    selected['failed_review'] = {'scope': scope, 'candidate_plan_sha256': _digest(plan),
        'drivers': {name: deepcopy(item) if name in targets else
                    {'value': deepcopy(item['value']), 'kind': item['kind'], 'driver_sha256': _digest(item)}
                    for name, item in drivers.items()}, 'rationale': answer['rationale'],
        'unrequested_decisions': 'Fixed, value-only projections. Proof and rationale omitted; not new visible evidence.'}
    # The complete current candidate values supersede the old scope for this correction.
    selected['prior_plan']['scenarios'][scope] = {
        name: selected['prior_plan']['scenarios'][scope][name] for name in sorted(targets)}
    selected['plan_projection']['complete_prior_scope'] = None
    selected['plan_projection']['complete_prior_drivers'] = sorted(targets)
    selected['plan_projection']['repair_scope_omissions'] = (
        'Unrequested old drivers omitted. failed_review exposes all current values; only requested current envelopes are complete.')
    response = proposer(deepcopy(selected), deepcopy(narrowed))
    decoded = (_expand_compact_review(response, stage) if 'compact_wire' in stage else response)
    if (not isinstance(decoded, dict) or set(decoded) != {'reviews', 'rationale'}
            or not isinstance(decoded['reviews'], dict) or set(decoded['reviews']) != set(targets)
            or not isinstance(decoded['rationale'], str) or not decoded['rationale'].strip()):
        raise ValueError('terminal repair must explicitly replace exactly the requested drivers')
    for name, choice in decoded['reviews'].items():
        if isinstance(choice, dict) and choice.get('action') == 'unavailable':
            raise ValueError('terminal repair source gap ' + name + ': ' + str(choice.get('reason')))
        if (not isinstance(choice, dict) or set(choice) != {'action', 'driver'}
                or choice['action'] != 'replace'):
            raise ValueError('terminal repair requires a complete replacement: ' + name)
    merged = deepcopy(answer)
    merged['reviews'].update(deepcopy(decoded['reviews']))
    merged['rationale'] = decoded['rationale']
    unchanged = {name: choice for name, choice in answer['reviews'].items() if name not in targets}
    if any(merged['reviews'][name] != choice for name, choice in unchanged.items()):
        raise ValueError('terminal repair changed an unrequested decision')
    return merged, {'targets': targets, 'attempt': 1, 'invalid_wire_response': deepcopy(wire_answer),
        'invalid_answer_sha256': _digest(wire_answer), 'wire_response': deepcopy(response),
        'answer_sha256': _digest(response), 'dossier_sha256': _digest(_model_dossier(selected)),
        'contract_sha256': _digest(narrowed), 'unchanged_decisions_sha256': _digest(unchanged),
        'unchanged_decisions_verified': True, 'human_approved': False,
        **({'compact_wire': deepcopy(stage['compact_wire'])} if 'compact_wire' in stage else {})}
