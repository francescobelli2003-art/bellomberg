"""Resume validated proposal stages without editing or repaying their responses."""
from copy import deepcopy
from hashlib import sha256

import pytest

from test_input_preparation_bank import _documents, _propose, DAY, providers_for, prepare_sector_analysis


def _bundle():
    return prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))


def _paused():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer
    captured = {}
    def capture(dossier, contract):
        captured.update(dossier=deepcopy(dossier), contract=deepcopy(contract))
    prepare_method_inputs(_bundle(), documents=_documents(), propose=capture)
    full = _propose(captured['dossier'], captured['contract'])
    calls = []
    def paid(dossier, contract):
        if len(calls) == 2:
            captured['plan'] = deepcopy(dossier['completed_plan'])
            raise RuntimeError('stop after acquired stages')
        stage = contract['preparation_stage']; calls.append(stage)
        values = full['model'] if stage['scope'] == 'model' else full['scenarios'][stage['scope']]
        return {'drivers': {key: deepcopy(values[key]) for key in stage['drivers']}, 'rationale': 'Sourced scenario'}
    with pytest.raises(RuntimeError, match='stop after acquired'):
        StagedProposer(paid, drivers_per_stage=12)(captured['dossier'], captured['contract'])
    return captured, full, calls


def test_seed_resumes_remaining_stages_and_current_compiler_accepts_complete_model():
    from bellomberg.valuation.preparation_seed import make_seed
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    captured, full, old_calls = _paused()
    seed = make_seed(captured['dossier'], captured['contract'], captured['plan']); original = deepcopy(seed)
    calls = []
    def remaining(dossier, contract):
        stage = contract['preparation_stage']; calls.append(stage)
        values = full['scenarios'][stage['scope']]
        return {'drivers': {key: deepcopy(values[key]) for key in stage['drivers']}, 'rationale': 'Sourced scenario'}
    result = prepare_method_inputs(_bundle(), documents=_documents(),
        propose=StagedProposer(remaining, drivers_per_stage=12, seed=seed))
    assert result['status'] == 'prepared', result['issues']
    assert result['proposal']['approval_status'] == 'automatic_non_approved'
    assert not any(c['scope'] == old['scope'] and c['drivers'] == old['drivers'] for c in calls for old in old_calls)
    assert result['proposal']['plan']['model'] == full['model']
    assert result['proposal']['plan']['scenarios'] == full['scenarios']
    assert seed == original


@pytest.mark.parametrize('problem', ['changed_source', 'changed_contract', 'changed_plan', 'bad_fact',
                                  'unknown_driver', 'partial_stage', 'out_of_order', 'approval_field',
                                  'opening_gap', 'unknown_citation', 'expired_estimate'])
def test_invalid_seed_fails_before_any_new_paid_callback(problem):
    from bellomberg.valuation.preparation_seed import make_seed
    from bellomberg.valuation.preparation_ai import StagedProposer
    captured, full, _ = _paused()
    dossier, contract, plan = (deepcopy(captured[k]) for k in ('dossier', 'contract', 'plan'))
    if problem == 'bad_fact': plan['model']['opening_common_equity']['value'] += 2
    elif problem == 'unknown_driver': plan['scenarios']['bear']['made_up'] = deepcopy(plan['scenarios']['bear']['taxes'])
    elif problem == 'partial_stage': plan['scenarios']['bear'].pop('taxes')
    elif problem == 'out_of_order':
        plan['scenarios']['bull'] = deepcopy(full['scenarios']['bull'])
        plan['scenario_rationale']['bull'] = 'Sourced scenario'
    elif problem == 'approval_field': plan['human_approved'] = True
    elif problem == 'opening_gap': plan['model'].pop('opening_parent_equity')
    elif problem == 'unknown_citation': plan['scenarios']['bear']['taxes']['evidence_ids'] = ['not-acquired']
    elif problem == 'expired_estimate': plan['scenarios']['bear']['taxes']['valid_until'] = '2020-01-01'
    seed = make_seed(dossier, contract, plan)
    if problem == 'changed_source':
        dossier['documents'][0]['text'] += '\nNew observation'
        dossier['documents'][0]['sha256'] = sha256(dossier['documents'][0]['text'].encode()).hexdigest()
    elif problem == 'changed_contract': contract['method_contract'] += '\nChanged requirement'
    elif problem == 'changed_plan': seed['plan']['scenarios']['bear']['taxes']['value'][0] += 2
    with pytest.raises(ValueError):
        StagedProposer(lambda *args: pytest.fail('invalid seed must not spend'),
                       drivers_per_stage=12, seed=seed)(dossier, contract)


def test_seed_request_boundaries_must_match_and_constructor_copies_seed():
    from bellomberg.valuation.preparation_seed import make_seed
    from bellomberg.valuation.preparation_ai import StagedProposer
    captured, _, _ = _paused(); seed = make_seed(captured['dossier'], captured['contract'], captured['plan'])
    staged = StagedProposer(lambda *args: pytest.fail('changed partition must not spend'), drivers_per_stage=5, seed=seed)
    seed['plan'].clear()
    with pytest.raises(ValueError, match='seed.*stage'):
        staged(captured['dossier'], captured['contract'])


@pytest.mark.parametrize('invalid_ledger', [False, True])
def test_complete_seed_is_rechecked_without_callbacks(invalid_ledger):
    from bellomberg.valuation.preparation_seed import make_seed
    from bellomberg.valuation.preparation_ai import StagedProposer
    captured, full, _ = _paused()
    if invalid_ledger:
        full['scenarios']['base']['capital.consolidation_adjustments']['value'][0] += 1
    seeded = StagedProposer(lambda *args: pytest.fail('complete seed must not spend'),
        drivers_per_stage=12, seed=make_seed(captured['dossier'], captured['contract'], full))
    if invalid_ledger:
        with pytest.raises(ValueError, match='bank forecast arithmetic'):
            seeded(captured['dossier'], captured['contract'])
    else:
        assert seeded(captured['dossier'], captured['contract']) == full
