"""Calendar checks use only the existing synthetic accounting fixture."""
from copy import deepcopy
from datetime import date

import pytest

from test_managed_care import documented_case
from bellomberg.valuation.managed_care import project_managed_care


def calendar_case(kind="interim", non_solar=False, count=3):
    data, context = documented_case()
    data['years'] = list(range(2026, 2026 + count))
    cutoff = ('2025-09-30' if non_solar else '2025-12-31') if kind == 'FY' else (
        '2026-03-31' if non_solar else '2026-06-30')
    data['actuals'].update(year=2025 if kind == 'FY' else 2026, period_end=cutoff)
    periods = [{'year': year, 'start': f'{year-1}-10-01' if non_solar else f'{year}-01-01',
                'end': f'{year}-09-30' if non_solar else f'{year}-12-31',
                'payment_date': f'{year}-09-30' if non_solar else f'{year}-12-31'}
               for year in data['years']]
    data['calendar'] = {'valuation_date': cutoff, 'day_count': 'ACT/365F',
        'actuals_kind': kind, 'actuals_start': ('2024-10-01' if non_solar else '2025-01-01') if kind == 'FY' else periods[0]['start'],
        'fiscal_periods': periods}
    def trim(value):
        if isinstance(value, dict):
            return {k: trim(v) for k, v in value.items()}
        if isinstance(value, list):
            return [trim(v) for v in value] if value and isinstance(value[0], dict) else (value * 2)[:count]
        return value
    data['scenarios'] = trim(data['scenarios'])
    for scenario in data['scenarios'].values():
        capital = scenario['capital']
        capital['discount_periods'] = [(date.fromisoformat(p['payment_date']) - date.fromisoformat(cutoff)).days / 365 for p in periods]
        if kind == 'FY':
            capital['subsidiaries'][0]['gaap_net_income'] = [5.] * count
            capital['subsidiaries'][0]['proposed_distribution'] = [5.] * count
    context['forecast_years'] = data['years']
    for record in context['assumptions']:
        owner = data['actuals'] if record['scenario'] == 'model' else data['scenarios'][record['scenario']]
        keys = record['driver'].split('.')[1:] if record['scenario'] == 'model' else record['driver'].split('.')
        for key in keys:
            owner = owner[int(key)] if isinstance(owner, list) else owner[key]
        record['values'] = deepcopy(owner)
    return data, context


@pytest.mark.parametrize('kind,non_solar,count', [('FY',False,1), ('interim',False,3), ('FY',True,6), ('interim',True,2)])
def test_explicit_calendar_does_not_double_count_actuals(kind, non_solar, count):
    data, context = calendar_case(kind, non_solar, count)
    result = project_managed_care(data, context, today=context['as_of'])
    assert result['analytical_quality']['status'] == 'DOCUMENTATA', result
    rows = result['scenarios']['base']['rows']
    assert len(rows) == count
    assert rows[0]['remaining_net_income'] == (7 if kind == 'FY' else 5)
    capital = result['scenarios']['base']['capital']
    flows = [15 if kind == 'FY' else 13] + [5] * (count-1)
    periods = data['scenarios']['base']['capital']['discount_periods']
    expected = (sum(flow / 1.1 ** period for flow, period in zip(flows, periods)) + 100 / 1.1 ** periods[-1]) / 10
    assert capital['fair_value_per_share'] == pytest.approx(expected)
    assert result['valuation_date'] == data['calendar']['valuation_date']


@pytest.mark.parametrize('failure', ['overlap', 'gap', 'wrong_actuals_start', 'cutoff', 'payment', 'delayed_payment', 'discount', 'unknown', 'year'])
def test_incompatible_dates_cannot_expose_a_value(failure):
    data, context = calendar_case()
    if failure == 'overlap': data['calendar']['fiscal_periods'][1]['start'] = '2026-12-31'
    if failure == 'gap': data['calendar']['fiscal_periods'][1]['start'] = '2027-01-02'
    if failure == 'wrong_actuals_start': data['calendar']['actuals_start'] = '2026-02-01'
    if failure == 'cutoff': data['calendar']['valuation_date'] = '2026-07-01'
    if failure == 'payment': data['calendar']['fiscal_periods'][0]['payment_date'] = '2026-05-31'
    if failure == 'delayed_payment':
        data['calendar']['fiscal_periods'][-1]['payment_date'] = '2029-02-01'
        for scenario in data['scenarios'].values():
            scenario['capital']['discount_periods'][-1] = (date(2029,2,1)-date(2026,6,30)).days/365
        for record in context['assumptions']:
            if record['driver'] == 'capital.discount_periods':
                record['values'] = deepcopy(data['scenarios'][record['scenario']]['capital']['discount_periods'])
    if failure == 'discount': data['scenarios']['base']['capital']['discount_periods'][0] += .1
    if failure == 'unknown': data['calendar']['invented_basis'] = 'no'
    if failure == 'year': data['calendar']['fiscal_periods'][0]['year'] = 2025
    result = project_managed_care(data, context, today=context['as_of'])
    assert result['analytical_quality']['status'] == 'INCOMPLETA'
    assert all(row.get('fair_value_per_share') is None for row in result['scenarios'].values())
