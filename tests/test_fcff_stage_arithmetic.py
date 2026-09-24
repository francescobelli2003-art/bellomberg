"""Reject an inconsistent FCFF terminal before spending on another scenario."""
from copy import deepcopy

import pytest

from test_input_preparation import _bundle, _documents, _operating_plan


def test_partial_forecast_is_not_completed_with_defaults():
    from bellomberg.valuation.fcff_stage_arithmetic import forecast_arithmetic
    plan = _operating_plan()
    for scenario in plan['scenarios'].values():
        del scenario['da_tan_pct']
    before = deepcopy(plan)
    assert forecast_arithmetic(plan) == {}
    assert plan == before


def test_arithmetic_exposes_revenue_denominator_and_preserves_source_plan():
    from bellomberg.valuation.fcff_stage_arithmetic import forecast_arithmetic
    plan = _operating_plan()
    before = deepcopy(plan)
    result = forecast_arithmetic(plan)
    assert result['bear']['final_year']['tangible_depreciation'] == 5
    assert result['bear']['final_year']['ebit'] == 15
    assert result['bear']['research_normalization_adjustment'] == 0
    assert plan == before


def test_bad_terminal_blocks_before_next_scenario():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    plan = _operating_plan()
    plan['scenarios']['bear']['terminal_bridge']['value']['normalized_ebit'] = 20
    calls = []
    def proposed(dossier, contract):
        stage = contract['preparation_stage']
        calls.append(stage['scope'])
        values = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(values[name]) for name in stage['drivers']},
                'rationale': 'Synthetic sourced scenario.'}
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=StagedProposer(proposed))
    assert result['status'] == 'incomplete'
    assert 'base' not in calls and 'bull' not in calls
    assert 'FCFF forecast arithmetic' in str(result['issues'])


def test_invalid_saved_terminal_blocks_before_any_request():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.preparation_seed import make_seed
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    captured = {}
    def capture(dossier, contract):
        captured.update(dossier=deepcopy(dossier), contract=deepcopy(contract))
        return _operating_plan()
    prepare_method_inputs(_bundle(), documents=_documents(), propose=capture)
    plan = _operating_plan()
    plan['scenarios']['bear']['terminal_bridge']['value']['normalized_ebit'] = 20
    seed = make_seed(captured['dossier'], captured['contract'], plan)
    with pytest.raises(ValueError, match='FCFF forecast arithmetic'):
        StagedProposer(lambda *args: pytest.fail('invalid prior work must not spend'), seed=seed)(
            captured['dossier'], captured['contract'])


def _context():
    from bellomberg.valuation.operating_adapter import SCHEMA
    plan = _operating_plan()
    for scope in plan['scenarios']:
        del plan['scenarios'][scope]['terminal_bridge']
    return ({'method_id': 'operating_fcff', 'completed_plan': plan},
            {'schema': {'terminal_bridge': SCHEMA['terminal_bridge']},
             'preparation_stage': {'scope': 'bear', 'drivers': ['terminal_bridge']}})


def test_new_prompt_explains_engine_denominators_and_reuses_after_restart(tmp_path):
    from test_preparation_ai import _proposer
    dossier, contract = _context()
    original = deepcopy(dossier)
    p = _proposer(tmp_path)
    view = p.prepare_context(dossier, contract, source_dossier=dossier)
    assert view['fcff_engine_semantics']['da_tan_pct_denominator'] == 'current-period revenue'
    assert view['derived_fcff_forecasts']['bear']['final_year']['ebit'] == 15
    answer = p(view, contract)
    restarted = _proposer(tmp_path, call=lambda **_: pytest.fail('duplicate paid request'))
    restarted.metadata = lambda _: pytest.fail('cached answer must not need live pricing')
    replay = restarted.prepare_context(dossier, contract, source_dossier=dossier)
    assert replay == view and restarted(replay, contract) == answer
    assert dossier == original and restarted.summary()['requests'] == 1


def test_original_paid_prompt_remains_exact_and_is_not_repaid(tmp_path):
    from test_preparation_ai import _proposer
    dossier, contract = _context()
    p = _proposer(tmp_path)
    answer = p(dossier, contract)  # Literal pre-guidance request.
    p.call = lambda **_: pytest.fail('legacy paid request repeated')
    p.metadata = lambda _: pytest.fail('legacy paid request repriced')
    view = p.prepare_context(dossier, contract, source_dossier=dossier)
    assert view == dossier and p(view, contract) == answer
    assert p.summary()['requests'] == 1


def test_inconsistent_operating_revenue_blocks_before_terminal_or_next_scenario():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    plan = _operating_plan()
    plan['scenarios']['bear']['revenue_build']['value']['unit_price'][0] *= 1.000000002
    calls = []
    def proposed(dossier, contract):
        stage = contract['preparation_stage']
        calls.append((stage['scope'], list(stage['drivers'])))
        values = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: deepcopy(values[name]) for name in stage['drivers']},
                'rationale': 'Synthetic operating assumptions.'}
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=StagedProposer(proposed))
    assert result['status'] == 'incomplete'
    assert not any(scope in ('base','bull') or names == ['terminal_bridge'] for scope,names in calls)
    assert 'revenue_build' in str(result['issues'])


def test_revenue_check_preserves_engine_tolerance_and_rejects_a_saved_mismatch():
    from bellomberg.valuation.fcff_stage_arithmetic import forecast_arithmetic
    plan = _operating_plan()
    plan['scenarios']['base']['revenue_build']['value']['unit_price'][0] *= 1.0000000005
    assert forecast_arithmetic(plan)['base']['final_year']['revenue'] == 100
    plan['scenarios']['base']['revenue_build']['value']['unit_price'][0] *= 1.000000002
    before = deepcopy(plan)
    with pytest.raises(ValueError, match='base.*revenue_build'):
        forecast_arithmetic(plan)
    assert plan == before
