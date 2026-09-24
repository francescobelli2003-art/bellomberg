"""Calculator results from proposed constraints, not new economic assumptions."""
from copy import deepcopy
import pytest


def _plan():
    constraints = {'BANK': {'basis': 'common_equity', 'constraints': [
        {'id': 'risk', 'exposure': [100., 110., 120.], 'ratio': [.1, .1, .1],
         'buffer': [1., 1., 1.], 'absolute_floor': [0., 0., 0.], 'terminal_requirement': 14.},
        {'id': 'floor', 'exposure': [20., 20., 20.], 'ratio': [.1, .1, .1],
         'buffer': [0., 0., 0.], 'absolute_floor': [12., 12., 12.], 'terminal_requirement': 12.}]}}
    return {'model': {'legal_structure': {'value': {'subsidiaries': [{'id': 'BANK'}]}},
        'perimeter': {'value': {'currency': 'USD'}}, 'calendar': {'value': {'periods': [1, 2, 3]}}},
        'scenarios': {'bear': {'capital_constraints': {'value': constraints}}, 'base': {}, 'bull': {}}}


def test_requirements_match_existing_engine_and_do_not_modify_completed_proposals():
    from bellomberg.valuation.bank_stage_arithmetic import constraint_arithmetic
    from bellomberg.valuation.capital_inputs import validate_constraints
    plan = _plan(); original = deepcopy(plan); output = constraint_arithmetic(plan)
    result = output['bear']['entities']['BANK']
    assert result['required_statutory_capital'] == [12., 12., 13.]
    assert result['by_constraint']['risk'] == [11., 12., 13.]
    assert result['terminal_requirement'] == 14.
    assert set(output) == {'bear'} and plan == original
    capital = {'discount_periods': [1, 2, 3], 'subsidiaries': [{'id': 'BANK',
        'required_statutory_capital': result['required_statutory_capital'],
        'liquidity_before_transfers': [20.]*3, 'proposed_contribution': [0.]*3, 'proposed_distribution': [0.]*3}],
        'parent_cash_flows': {'admin_fees_received': [0.]*3, 'tax_transfers_received': [0.]*3}}
    cash = {'BANK': {'opening_cash': 20., **{name: [0.]*3 for name in (
        'operating_cash', 'investing_cash', 'financing_cash', 'parent_fees_paid', 'parent_tax_paid')}}}
    issues=[]
    validate_constraints(capital,plan['scenarios']['bear']['capital_constraints']['value'],cash,lambda *row: issues.append(row))
    assert not issues


@pytest.mark.parametrize('problem', ['missing_path', 'short_path', 'duplicate_id', 'negative', 'nonfinite', 'wrong_entity', 'wrong_basis'])
def test_invalid_constraints_stop_before_another_forecast_stage(problem):
    from bellomberg.valuation.bank_stage_arithmetic import constraint_arithmetic
    plan = _plan(); item = plan['scenarios']['bear']['capital_constraints']['value']['BANK']; row=item['constraints'][0]
    if problem == 'missing_path': row.pop('ratio')
    elif problem == 'short_path': row['exposure'].pop()
    elif problem == 'duplicate_id': item['constraints'].append(deepcopy(row))
    elif problem == 'negative': row['buffer'][0] = -1
    elif problem == 'nonfinite': row['exposure'][0] = float('inf')
    elif problem == 'wrong_entity': plan['model']['legal_structure']['value']['subsidiaries'][0]['id']='OTHER'
    else: item['basis']='total_capital'
    with pytest.raises(ValueError, match='bank constraint arithmetic'):
        constraint_arithmetic(plan)


def test_missing_constraints_do_not_create_a_zero_or_change_preceding_requests():
    from bellomberg.valuation.bank_stage_arithmetic import constraint_arithmetic
    assert constraint_arithmetic({'scenarios': {'bear': {}, 'base': {}, 'bull': {}}}) == {}


def _complete_plan():
    from test_input_preparation_bank import _documents, _propose
    return _propose({'documents': _documents()}, {'method_id': 'bank_residual_income',
        'standard_horizon_years': 10, 'bank_dynamic_capital': True,
        'bank_capital_mapping': {'capital.parent_opening_cash': ['parent_ledger']}})


def test_forecast_calculator_provides_terminal_targets_from_existing_cash_engine():
    from bellomberg.valuation.bank_stage_arithmetic import forecast_arithmetic
    plan = _complete_plan(); original = deepcopy(plan)
    for row in plan['scenarios'].values():
        for key in ('terminal_ledger', 'capital.terminal_equity', 'capital.terminal_debt', 'capital.terminal_basis'):
            row.pop(key)
    before = deepcopy(plan); result = forecast_arithmetic(plan)
    base = result['base']
    assert base['closing_common_equity'] == pytest.approx(100.)
    assert base['continuing_common_income'] == pytest.approx(10.)
    assert base['continuing_shareholder_distribution'] == pytest.approx(10.)
    assert base['terminal_equity_target'] == pytest.approx(original['scenarios']['base']['capital.terminal_equity']['value'])
    assert base['closing_parent_cash'] == 10. and base['closing_parent_debt'] == 0.
    assert base['closing_subsidiaries'][0]['gaap_equity'] == 90.
    assert plan == before and 'terminal_ledger' not in base


@pytest.mark.parametrize('problem', ['cash_mismatch', 'insufficient_capital', 'wrong_income', 'invalid_terminal', 'opening_equity', 'discount_periods', 'extra_income_period'])
def test_bad_complete_forecast_stops_before_terminal_request(problem):
    from bellomberg.valuation.bank_stage_arithmetic import forecast_arithmetic
    plan = _complete_plan(); sc = plan['scenarios']['base']
    if problem == 'cash_mismatch': sc['capital.subsidiaries.0.liquidity_before_transfers']['value'][0] += 1
    elif problem == 'insufficient_capital': sc['capital.subsidiaries.0.required_statutory_capital']['value'][0] = 1000
    elif problem == 'wrong_income': sc['capital.parent_gaap_net_income']['value'][0] += 5
    elif problem == 'opening_equity': plan['model']['opening_common_equity']['value'] += 2
    elif problem == 'discount_periods': sc['capital.discount_periods']['value'] = [.5 + i for i in range(10)]
    elif problem == 'extra_income_period': sc['net_interest_income']['value'].append(1.)
    else: sc['terminal_growth']['value'] = 1.
    with pytest.raises(ValueError, match='bank forecast arithmetic'):
        forecast_arithmetic(plan)


def test_incomplete_forecast_does_not_infer_zero_or_change_previous_requests():
    from bellomberg.valuation.bank_stage_arithmetic import forecast_arithmetic
    plan = _complete_plan()
    for row in plan['scenarios'].values():
        row.pop('capital.subsidiaries.0.gaap_net_income')
    assert forecast_arithmetic(plan) == {}


@pytest.mark.parametrize('growth', [0., .02])
def test_continuing_balance_targets_are_only_arithmetic_on_completed_forecasts(growth):
    from bellomberg.valuation.bank_stage_arithmetic import forecast_arithmetic
    plan = _complete_plan()
    for sc in plan['scenarios'].values():
        sc['terminal_growth']['value'] = growth
        for key in ('terminal_ledger', 'capital.terminal_equity', 'capital.terminal_debt', 'capital.terminal_basis'):
            sc.pop(key)
    before = deepcopy(plan); base = forecast_arithmetic(plan)['base']
    targets = base['continuing_closing_balances']
    assert targets['parent_cash'] == pytest.approx(10. * (1+growth))
    assert targets['parent_debt'] == 0.
    assert targets['subsidiaries'][0]['statutory_capital'] == pytest.approx(90. * (1+growth))
    assert targets['subsidiaries'][0]['liquidity'] == pytest.approx(base['closing_subsidiaries'][0]['liquidity'] * (1+growth))
    assert plan == before


@pytest.mark.parametrize('problem', ['terminal_value', 'terminal_debt', 'continuing_cash'])
def test_completed_terminal_is_validated_before_spending_on_next_scenario(problem):
    from bellomberg.valuation.bank_stage_arithmetic import forecast_arithmetic
    plan = _complete_plan()
    assert forecast_arithmetic(plan)['base']['terminal_continuity_checked'] is True
    sc = plan['scenarios']['base']
    if problem == 'terminal_value': sc['capital.terminal_equity']['value'] += 1
    elif problem == 'terminal_debt': sc['capital.terminal_debt']['value'] += 1
    else: sc['terminal_ledger']['value']['capital']['parent_opening_cash'] += 1
    with pytest.raises(ValueError, match='bank forecast arithmetic'):
        forecast_arithmetic(plan)
