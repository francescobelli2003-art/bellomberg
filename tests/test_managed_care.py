"""Independent accounting checks for a company scenario, never a live valuation."""
from copy import deepcopy
import pytest


def case():
    scenario = {
        'segments': {'Health': {'premium': [100.] * 5, 'mcr': [.8] * 5}},
        'other_premium': [2.] * 5, 'other_medical_costs': [1.] * 5,
        'premium_tax_revenue': [5.] * 5, 'premium_tax_expense': [5.] * 5,
        'average_invested_assets': [50.] * 5, 'investment_yield': [.04] * 5,
        'other_revenue': [1.] * 5, 'gna_ratio': [.1] * 5,
        'da': [2.] * 5, 'other_operating_costs': [1.] * 5,
        'interest_expense': [1.] * 5, 'tax_expense': [2.] * 5,
        'diluted_shares_m': [10.] * 5,
        'adjustments_after_tax': {'specified': [1.] * 5},
        'capital': None,
    }
    return {'ticker': 'SYNTH.TEST', 'currency': 'USD', 'years': [2026, 2027, 2028, 2029, 2030],
            'actuals': {'year': 2026, 'period_end': '2026-06-30', 'premium': 45.,
                        'medical_costs': 35., 'gna': 5., 'net_income': 2.},
            'scenarios': {s: deepcopy(scenario) for s in ('bear', 'base', 'bull')}}


def project(value=None, context=None):
    try:
        from bellomberg.valuation.managed_care import project_managed_care
    except ImportError:
        pytest.fail('Company economics bridge not implemented')
    return project_managed_care(value or case(), context, today='2026-09-10')


def test_premium_mcr_and_total_revenue_gna_have_different_denominators():
    row = project()['scenarios']['base']['rows'][0]
    assert row['premium'] == 102
    assert row['medical_costs'] == 81
    assert row['investment_income'] == 2
    assert row['total_revenue'] == 110
    assert row['gna'] == 11
    assert row['pretax_income'] == 9
    assert row['net_income'] == 7
    assert row['adjusted_net_income'] == 8
    assert row['adjusted_eps'] == .8
    assert row['remaining_premium'] == 57
    assert row['remaining_medical_costs'] == 46
    assert row['remaining_net_income'] == 5


def test_missing_capital_never_becomes_earnings_as_distributions():
    result = project()
    assert result['scenarios']['base']['capital']['status'] == 'n.d.'
    assert result['scenarios']['base']['fair_value_per_share'] is None
    assert result['analytical_quality']['status'] == 'INCOMPLETA'
    assert result['analytical_quality']['revision_bridge']['status'] == 'n.d.'


@pytest.mark.parametrize('value', [None, True, float('nan'), float('inf'), [1., 2.]])
def test_invalid_required_driver_is_nd_not_zero(value):
    data = case(); data['scenarios']['base']['interest_expense'] = value
    result = project(data)['scenarios']['base']
    assert result['status'] == 'n.d.'
    assert result['rows'] == []
    assert any('interest_expense' in i for i in result['issues'])


def test_zero_is_valid_but_mcr_can_exceed_one_in_a_loss_scenario():
    data = case()
    for value in data['scenarios'].values():
        value['segments']['Health']['mcr'] = [1.1] * 5
        value['interest_expense'] = [0.] * 5
        value['tax_expense'] = [0.] * 5
    row = project(data)['scenarios']['bear']['rows'][0]
    assert row['net_income'] == pytest.approx(-20)


def test_forecast_cannot_erase_realized_nonnegative_costs():
    data = case(); data['actuals']['medical_costs'] = 100
    assert project(data)['scenarios']['base']['status'] == 'n.d.'


def test_periods_and_actual_dates_must_match():
    data = case(); data['actuals']['period_end'] = '2025-06-30'
    assert project(data)['status'] == 'n.d.'


def test_input_immutable_and_irrelevant_extra_fields_rejected():
    data = case(); before = deepcopy(data)
    project(data)
    assert data == before
    data['scenarios']['base']['net_debt'] = -1000
    assert project(data)['scenarios']['base']['status'] == 'n.d.'


def test_evidence_uses_existing_contract_and_stale_remains_visible():
    data = case()
    context = {'as_of': '2026-09-10', 'forecast_years': data['years'],
               'scenario_rationale': {s: 'Specific scenario' for s in data['scenarios']},
               'assumptions': [{'scenario': 'base', 'driver': 'segments.Health.mcr',
                   'kind': 'analyst_estimate', 'source': 'https://example.org/report',
                   'source_date': '2026-08-01', 'valid_until': '2026-09-01',
                   'metric': 'MCR', 'basis': 'medical costs / premium revenue',
                   'rationale': 'Specified loss severity'}], 'revisions': []}
    result = project(data, context)
    row = next(r for r in result['analytical_quality']['rows']
               if r['scenario'] == 'base' and r['driver'] == 'segments.Health.mcr')
    assert any('STALE' in issue for issue in row['issues'])


def test_missing_premium_taxes_are_not_assumed_to_cancel():
    data = case(); del data['scenarios']['base']['premium_tax_expense']
    assert project(data)['scenarios']['base']['status'] == 'n.d.'


def test_malformed_evidence_identifiers_do_not_crash():
    context = {'as_of': '2026-09-10', 'assumptions': [{'scenario': 'base', 'driver': []}]}
    assert project(context=context)['analytical_quality']['status'] == 'INCOMPLETA'


def test_unknown_actual_input_cannot_acquire_documented_status():
    data = case(); data['actuals']['future_premium_growth'] = [.99] * 5
    assert project(data)['status'] == 'n.d.'


def test_rationale_and_revision_must_have_real_structure():
    context = {'as_of': '2026-09-10', 'forecast_years': case()['years'],
               'scenario_rationale': {s: True for s in ('bear', 'base', 'bull')},
               'assumptions': [], 'revisions': ['not a revision object']}
    quality = project(context=context)['analytical_quality']
    assert any('motivazione' in i for i in quality['issues'])
    assert quality['revision_rows'][0]['issues']


def documented_case():
    """Explicit synthetic legal-entity cash ledger and independently enumerated evidence."""
    data = case()
    capital = {
        'distribution_policy': 'full_sweep_after_buffers',
        'reconciliation_basis': 'Synthetic entity bridge including fees and tax remittances',
        'upstream_approval_basis': 'Synthetic conditional forecast, approvals not certified',
        'terminal_basis': 'Synthetic equity after final distribution, including closing debt and buffers',
        'parent_opening_cash': 20., 'parent_cash_minimum': [10.] * 5,
        'parent_opening_debt': 5., 'terminal_debt': 5.,
        'parent_gaap_net_income': [2.] * 5, 'consolidation_adjustments': [0.] * 5,
        'parent_cash_flows': {
            'admin_fees_received': [3.] * 5, 'tax_transfers_received': [2.] * 5,
            'investment_cash_income': [1.] * 5, 'other_cash_receipts': [0.] * 5,
            'holding_cash_costs': [2.] * 5, 'external_taxes': [2.] * 5,
            'interest_paid': [1.] * 5, 'capex': [1.] * 5, 'acquisitions': [0.] * 5,
            'debt_issued': [0.] * 5, 'debt_repaid': [0.] * 5,
        },
        'subsidiaries': [{
            'id': 'SYNTH-LEGAL-A', 'opening_gaap_equity': 95.,
            'opening_gaap_to_statutory_equity': 5., 'opening_statutory_capital': 100.,
            'gaap_net_income': [3., 5., 5., 5., 5.], 'gaap_to_statutory_income': [0.] * 5,
            'other_statutory_movements': [0.] * 5, 'required_statutory_capital': [100.] * 5,
            'liquidity_before_transfers': [20.] * 5, 'minimum_liquidity': [10.] * 5,
            'permitted_distribution': [6.] * 5,
            'proposed_distribution': [3., 5., 5., 5., 5.], 'proposed_contribution': [0.] * 5,
        }],
        'ke': .1, 'shares_m': 10., 'discount_periods': [.5, 1.5, 2.5, 3.5, 4.5],
        'terminal_equity': 100.,
    }
    records = []

    def record(scenario, driver, value):
        records.append({'scenario': scenario, 'driver': driver, 'values': deepcopy(value),
                        'kind': 'analyst_estimate', 'source': 'https://example.org/synthetic',
                        'source_date': '2026-08-01', 'valid_until': '2026-12-31',
                        'metric': driver, 'basis': 'Declared synthetic accounting basis',
                        'rationale': 'Explicit synthetic forecast, not an issuer estimate'})

    for key in ('premium', 'medical_costs', 'gna', 'net_income'):
        record('model', 'actuals.' + key, data['actuals'][key])
    for name, scenario in data['scenarios'].items():
        scenario['capital'] = deepcopy(capital)
        for key, values in scenario.items():
            if key not in ('segments', 'adjustments_after_tax', 'capital'):
                record(name, key, values)
        for key in ('premium', 'mcr'):
            record(name, 'segments.Health.' + key, scenario['segments']['Health'][key])
        record(name, 'adjustments_after_tax.specified', scenario['adjustments_after_tax']['specified'])
        for key in ('parent_opening_cash', 'parent_cash_minimum', 'parent_opening_debt',
                    'terminal_debt', 'parent_gaap_net_income', 'consolidation_adjustments',
                    'ke', 'shares_m', 'discount_periods', 'terminal_equity'):
            record(name, 'capital.' + key, capital[key])
        for key, values in capital['parent_cash_flows'].items():
            record(name, 'capital.parent_cash_flows.' + key, values)
        for key, values in capital['subsidiaries'][0].items():
            if key != 'id':
                record(name, 'capital.subsidiaries.0.' + key, values)
    context = {'as_of': '2026-09-10', 'forecast_years': data['years'],
               'scenario_rationale': {name: 'Synthetic explicit scenario' for name in data['scenarios']},
               'assumptions': records, 'revisions': []}
    return data, context


def test_complete_documented_company_and_capital_paths_expose_only_the_equity_valuation():
    data, context = documented_case()
    result = project(data, context)
    assert result['analytical_quality']['status'] == 'DOCUMENTATA'
    base = result['scenarios']['base']
    assert base['capital']['status'] == 'CALCOLABILE'
    assert [r['consolidated_net_income'] for r in base['capital']['rows']] == [5., 7., 7., 7., 7.]
    assert [r['shareholder_net_distribution'] for r in base['capital']['rows']] == [13., 5., 5., 5., 5.]
    # Independent half-year discount calculation: 13/sqrt(1.1), then 5 annually,
    # and 100 equity at 4.5 years; no extra NI, cash or net-debt adjustment.
    assert base['fair_value_per_share'] == pytest.approx(9.262953200457027)
    assert base['capital']['fair_value_per_share'] == base['fair_value_per_share']
    assert result['cashflow_cutoff'] == '2026-06-30'
    assert result['analytical_quality']['revision_bridge']['status'] == 'n.d.'


@pytest.mark.parametrize('failure', ['missing', 'stale', 'mismatched'])
def test_capital_evidence_gates_headline_and_nested_equity_values(failure):
    data, context = documented_case()
    evidence = next(r for r in context['assumptions'] if r['scenario'] == 'base'
                    and r['driver'] == 'capital.subsidiaries.0.permitted_distribution')
    if failure == 'missing':
        context['assumptions'].remove(evidence)
    elif failure == 'stale':
        evidence['valid_until'] = '2026-09-01'
    else:
        evidence['values'] = [600.] * 5
    result = project(data, context)
    assert result['analytical_quality']['status'] == 'INCOMPLETA'
    for scenario in result['scenarios'].values():
        assert scenario['fair_value_per_share'] is None
        assert scenario['capital']['fair_value_per_share'] is None
        assert scenario['capital']['equity_value'] is None
        assert scenario['capital']['rows']  # Arithmetic remains reviewable.


@pytest.mark.parametrize('failure', ['missing_capital', 'illiquid_subsidiary', 'annual_not_remaining_income'])
def test_complete_evidence_cannot_override_an_incomplete_or_infeasible_capital_ledger(failure):
    data, context = documented_case()
    capital = data['scenarios']['base']['capital']
    if failure == 'missing_capital':
        data['scenarios']['base']['capital'] = None
    else:
        if failure == 'illiquid_subsidiary':
            field, values = 'liquidity_before_transfers', [12., 20., 20., 20., 20.]
        else:
            field, values = 'gaap_net_income', [5.] * 5  # With parent NI 2: FY 7, not remaining 5.
        capital['subsidiaries'][0][field] = values
        evidence = next(r for r in context['assumptions'] if r['scenario'] == 'base'
                        and r['driver'] == 'capital.subsidiaries.0.' + field)
        evidence['values'] = list(values)
    result = project(data, context)
    assert result['analytical_quality']['status'] == 'INCOMPLETA'
    assert result['scenarios']['base']['capital']['status'] == 'n.d.'
    assert all(r['fair_value_per_share'] is None for r in result['scenarios'].values())


def test_documented_input_and_snapshot_are_independent_objects():
    data, context = documented_case()
    before = deepcopy((data, context))
    result = project(data, context)
    assert (data, context) == before
    result['analytical_quality']['snapshot']['scenarios']['base']['capital']['parent_cash_minimum'][0] = 99.
    assert (data, context) == before
