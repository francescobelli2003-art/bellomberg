"""Public analysis standard shared by acquisition, tools and workbook readers.

A target horizon is a research requirement, never a source of forecast numbers.
Existing approved records are diagnosed, not extended or silently replaced.
"""
from copy import deepcopy


CONTINUING = frozenset(('operating_fcff', 'bank_residual_income',
    'insurance_pc_distributable_equity', 'insurance_life_distributable_equity',
    'managed_care_distributable_equity', 'regulated_rab'))
FINITE = frozenset(('resources_asset_dcf', 'property_development_fcff', 'development_rnpv'))
SNAPSHOT = frozenset(('fund_nav', 'digital_asset_nav', 'property_nav', 'exposure_analysis'))


def forecast_standard(method):
    """Describe the same research policy for every ticker and repository user."""
    mode = ('continuing_business' if method in CONTINUING else 'documented_asset_life'
            if method in FINITE else 'dated_snapshot' if method in SNAPSHOT else
            'component_specific' if method == 'mixed_business_sotp' else 'unsupported')
    return {'version': 'analysis-standard/1', 'horizon_basis': mode,
        'target_annual_periods': 10 if method in CONTINUING else None,
        'scenarios': [] if method == 'exposure_analysis' else ['bear', 'base', 'bull'],
        'assumption_policy': 'company_specific_dated_sources_and_explicit_analyst_judgment',
        'horizon_policy': 'Ten annual periods for continuing businesses; explain deviations in the calendar record rationale. '
            'Finite assets follow documented life; NAV uses dated assets and claims; SOTP follows each component.',
        'terminal_policy': 'Reconcile final explicit operations, normalized earnings, reinvestment, returns and continuing cash. '
            'Do not extend expired guidance or generate growth rates from the sector label.',
        'approved_records_policy': 'Immutable; propose and review a new set when the horizon or material assumptions change.'}


def assess_standard(payload):
    """Report coverage without confusing arithmetic validity with research quality."""
    method = payload.get('method')
    policy = forecast_standard(method)
    rows = payload.get('analytical_quality', {}).get('rows', [])
    record = next((r for r in rows if r.get('driver') == 'calendar' and r.get('scenario') == 'model'), {})
    calendar = record.get('values') or {}
    periods = calendar.get('periods')
    years = payload.get('analytical_quality', {}).get('snapshot', {}).get('forecast_years')
    count = len(periods) if isinstance(periods, list) else len(years) if isinstance(years, list) else None
    target = policy['target_annual_periods']
    horizon = ('aligned' if count == target else 'review_required') if target else 'method_specific'
    return {**deepcopy(policy), 'actual_annual_periods': count, 'horizon_status': horizon,
            'horizon_rationale': record.get('evidence', {}).get('rationale'),
            'economic_quality_certified': False,
            'note': 'Horizon coverage does not certify assumptions. No input, fair value or approval is changed by this assessment.'}
