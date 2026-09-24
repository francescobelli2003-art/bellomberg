"""A rejected terminal result may receive one explicit, narrowly scoped review."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from test_preparation_refresh import _case, _compact_answer


def _runner(tmp_path, *, enabled=True, fault=None, compact=True):
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    from bellomberg.valuation.preparation_refresh import RefreshProposer
    old, current, seed, review = _case()
    name = 'capital.terminal_equity'
    driver = deepcopy(seed['plan']['scenarios']['base'][name])
    driver.update(valid_until=current['dossier']['as_of'], valid_until_basis=current['contract']['expiry_policy'])
    driver['value'] += .0001
    review['reviews']['base'][name] = {'action': 'replace', 'driver': driver}
    calls, requests = [], []
    def provider(**request):
        spec = json.loads(request['messages'][0]['content'])
        stage = spec['contract']['preparation_refresh']
        scope = stage['scope']; repair = stage.get('arithmetic_repair')
        calls.append(scope + ('-repair' if repair else ''))
        requests.append(deepcopy(spec))
        answer = {'reviews': deepcopy(review['reviews'][scope]),
                  'rationale': review['scenario_rationale'].get(scope, 'Opening reviewed.')}
        if repair:
            assert set(stage['driver_hashes']) == {name}
            assert repair['attempt'] == 1
            assert repair['targets'][name]['declared'] == driver['value']
            fixed = deepcopy(driver)
            fixed['value'] = repair['targets'][name]['calculated']
            fixed['rationale'] += ' Explicit reconciliation to the deterministic cash engine.'
            answer['reviews'] = {name: {'action': 'replace', 'driver': fixed}}
            if fault == 'unchanged': fixed['value'] = driver['value']
            elif fault == 'extra': answer['reviews']['capital.ke'] = deepcopy(review['reviews']['base']['capital.ke'])
            elif fault == 'missing': answer['reviews'] = {}
            elif fault == 'source': fixed['evidence_ids'] = ['unseen']
            elif fault == 'expiry': fixed['valid_until'] = old['dossier']['as_of']
            elif fault == 'reuse': answer['reviews'][name] = deepcopy(review['reviews']['base']['capital.ke'])
            elif fault == 'gap': answer['reviews'][name] = {'action': 'unavailable', 'reason': 'No reconciliation.'}
            elif fault == 'rationale': answer['rationale'] = ''
        if 'compact_wire' in stage:
            answer = _compact_answer(answer, stage['compact_wire'])
        return SimpleNamespace(id='synthetic-' + calls[-1], model=request['model'], provider='synthetic',
            stop_reason='end_turn', usage=SimpleNamespace(cost_usd=.01),
            content=[SimpleNamespace(type='text', text=json.dumps(answer))])
    paid = BudgetedProposer(tmp_path / 'journal.db', authorized_usd=3, model='synthetic/model',
        max_tokens=16000, thinking={'type': 'adaptive'},
        metadata=lambda _: {'id': 'synthetic/model', 'context_length': 1000000,
                            'pricing': {'prompt': '.000001', 'completion': '.000001'}}, call=provider)
    options = {'compact_scopes': ['base', 'bull'] if compact else [], 'repair_terminal': enabled}
    def make(proposer=paid):
        return RefreshProposer(proposer, seed, old['dossier'], old['contract'], **options)
    return make, paid, calls, requests, current, seed


@pytest.mark.parametrize('compact', [False, True])
def test_single_field_review_keeps_paid_prefix_and_survives_interruption(tmp_path, compact):
    make, paid, calls, requests, current, seed = _runner(tmp_path, compact=compact)
    def interrupt(dossier, contract):
        if contract['preparation_refresh']['scope'] == 'bull':
            raise InterruptedError('after repair, before bull')
        return paid(dossier, contract)
    with pytest.raises(InterruptedError):
        make(interrupt)(current['dossier'], current['contract'])
    assert calls == ['model', 'bear', 'base', 'base-repair']
    reviewer = make()
    plan = reviewer(current['dossier'], current['contract'])
    assert calls == ['model', 'bear', 'base', 'base-repair', 'bull']
    assert paid.summary()['spent_usd'] == pytest.approx(.05)
    assert plan['scenarios']['base']['capital.terminal_equity']['value'] == pytest.approx(
        seed['plan']['scenarios']['base']['capital.terminal_equity']['value'])
    assert all(value['value'] == seed['plan']['scenarios']['base'][name]['value']
               for name, value in plan['scenarios']['base'].items() if name != 'capital.terminal_equity')
    proof = reviewer.refresh_lineage['requests'][2]['arithmetic_repair']
    assert proof['invalid_wire_response'] != proof['wire_response']
    assert proof['unchanged_decisions_verified'] is True
    assert proof['human_approved'] is False
    assert set(proof['targets']) == {'capital.terminal_equity'}


def test_terminal_repair_is_opt_in(tmp_path):
    make, _, calls, _, current, _ = _runner(tmp_path, enabled=False)
    with pytest.raises(ValueError, match='terminal equity/debt'):
        make()(current['dossier'], current['contract'])
    assert calls == ['model', 'bear', 'base']


@pytest.mark.parametrize('fault', ['unchanged', 'extra', 'missing', 'source', 'expiry', 'reuse', 'gap', 'rationale'])
def test_invalid_repair_stops_without_second_repair_or_bull(tmp_path, fault):
    make, _, calls, _, current, _ = _runner(tmp_path, fault=fault, compact=False)
    with pytest.raises(ValueError):
        make()(current['dossier'], current['contract'])
    assert calls == ['model', 'bear', 'base', 'base-repair']


@pytest.mark.parametrize('field', ['capital.terminal_equity', 'capital.terminal_debt'])
def test_terminal_diagnostic_is_structured_and_never_changes_proposal(field):
    from bellomberg.valuation.bank_stage_arithmetic import forecast_arithmetic, BankTerminalMismatch
    from test_bank_stage_arithmetic import _complete_plan
    plan = _complete_plan()
    expected = plan['scenarios']['base'][field]['value']
    plan['scenarios']['base'][field]['value'] += .0001
    before = deepcopy(plan)
    with pytest.raises(BankTerminalMismatch) as error:
        forecast_arithmetic(plan)
    assert error.value.scope == 'base'
    assert set(error.value.targets) == {field}
    assert error.value.targets[field]['calculated'] == pytest.approx(expected)
    assert plan == before
