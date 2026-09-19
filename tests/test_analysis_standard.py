"""The shared horizon mandate must never create forecasts or revise approvals."""
from copy import deepcopy
import pytest
from bellomberg.valuation.analysis_standard import forecast_standard, assess_standard, CONTINUING, FINITE, SNAPSHOT
from bellomberg.valuation.method_registry import get_method_requirements


@pytest.mark.parametrize('method', sorted(CONTINUING))
def test_continuing_methods_publish_ten_year_research_target(method):
    result = get_method_requirements(method)['analysis_standard']
    assert result['target_annual_periods'] == 10
    assert result['scenarios'] == ['bear', 'base', 'bull']
    result['scenarios'].clear()
    assert forecast_standard(method)['scenarios'] == ['bear', 'base', 'bull']


@pytest.mark.parametrize('method', sorted(FINITE | SNAPSHOT | {'mixed_business_sotp'}))
def test_non_continuing_methods_do_not_invent_ten_year_dcf(method):
    assert forecast_standard(method)['target_annual_periods'] is None


@pytest.mark.parametrize('periods,status', [(4, 'review_required'), (10, 'aligned'), (15, 'review_required')])
def test_existing_approved_calendar_and_value_are_not_extended(periods, status):
    payload = {'method': 'operating_fcff', 'fair_value_base': 123.45,
               'analytical_quality': {'rows': [{'driver': 'calendar', 'scenario': 'model',
                   'values': {'periods': [{'end': str(2027 + i)} for i in range(periods)]},
                   'evidence': {'rationale': 'Original approved calendar'}}]}}
    original = deepcopy(payload)
    result = assess_standard(payload)
    assert result['actual_annual_periods'] == periods
    assert result['horizon_status'] == status
    assert not result['economic_quality_certified']
    assert payload == original
