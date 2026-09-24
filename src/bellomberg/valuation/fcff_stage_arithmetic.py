"""Read-only FCFF arithmetic on complete forecast paths; never infer a driver."""
from .dcf_quality import _finite


PATHS = ('revenue_growth', 'gross_margin', 'rnd_pct', 'sga_pct', 'capdev_pct',
         'da_tan_pct', 'tax_rate', 'capex_pct', 'nwc_pct', 'opening_intangible_amortization')
TERMINAL_KEYS = {'normalized_ebit', 'capitalized_research_adjustment', 'cycle_adjustment',
                 'expiring_product_loss', 'replacement_product_income', 'other_adjustment'}

SEMANTICS = {
    'version': 1,
    'da_tan_pct_denominator': 'current-period revenue',
    'ratio_denominators': 'gross_margin, rnd_pct, sga_pct, capdev_pct, da_tan_pct, capex_pct and nwc_pct multiply current-period revenue; tax_rate multiplies positive EBIT. Never supply a PPE-based depreciation rate as da_tan_pct.',
    'ebitda': 'revenue * (gross_margin - rnd_pct - sga_pct + capdev_pct)',
    'ebit': 'EBITDA - revenue * da_tan_pct - research_amortization',
    'classification': 'Reconcile reported accounting to these engine buckets. Reported gross profit may already deduct depreciation or research, and SG&A may contain depreciation: remove only sourced overlaps before putting them in separate engine rows. Reconcile operating leases and non-research amortization explicitly. Do not double-count expenses, invent a reclassification or change the engine convention.',
    'fcff': 'EBIT - max(EBIT,0)*tax_rate + depreciation + research_amortization - capex - capitalized_development - change_in_working_capital',
    'terminal': 'Use final-year engine EBIT plus the six-field terminal bridge; research_normalization_adjustment is computed. Cycle/expiry/replacement/other adjustments are sourced judgments, never balancing plugs. Continuing reinvestment = normalized EBIT*(1-tax_rate)*g/RONIC; reconcile the explicit-to-continuing transition and state unsupported gaps.',
}


def with_engine_guidance(dossier, contract):
    """Explain existing calculations on a prompt copy without revising any input."""
    from copy import deepcopy
    if ((contract.get('preparation_stage') or {}).get('scope') not in ('bear', 'base', 'bull')
            or not isinstance(dossier.get('completed_plan'), dict)):
        return dossier
    projected = deepcopy(dossier)
    projected['fcff_engine_semantics'] = deepcopy(SEMANTICS)
    arithmetic = forecast_arithmetic(dossier['completed_plan'])
    if arithmetic:
        projected['derived_fcff_forecasts'] = arithmetic
    return projected


def forecast_arithmetic(plan):
    """Called after ordinary driver compilation, including before resuming a seed."""
    from .dcf_buyside_v3 import _scenario_numbers
    from .operating_adapter import operating_revenue_errors, operating_revenue_path, terminal_bridge_errors
    opening = plan['model']
    needed = {'historical_revenue', 'opening_nwc', 'capdev_amortization_years', 'calendar'}
    if not needed <= opening.keys():
        return {}
    values = {name: opening[name]['value'] for name in needed}
    count = len(values['calendar']['periods'])
    result = {}
    for scope, drivers in plan['scenarios'].items():
        if not set(PATHS) <= drivers.keys():
            continue
        scenario = {name: item['value'] for name, item in drivers.items()}
        if any(not isinstance(scenario[name], list) or len(scenario[name]) != count
               or any(not _finite(value) for value in scenario[name]) for name in PATHS):
            raise ValueError('FCFF forecast arithmetic: incomplete or nonfinite paths in ' + scope)
        revenue = scenario.get('revenue_build')
        if revenue is not None and (not isinstance(revenue, dict) or revenue.get('basis') != 'segment_guidance'):
            issues = operating_revenue_errors(revenue, values['historical_revenue'], scenario['revenue_growth'])
            if issues:
                raise ValueError('FCFF forecast arithmetic: ' + scope + ': revenue_build: ' + '; '.join(issues))
        numbers = _scenario_numbers({'documented_inputs': True,
            'capdev_amortization_years': values['capdev_amortization_years']}, scenario,
            values['historical_revenue'], nwc0=values['opening_nwc'],
            revenue_path=operating_revenue_path(revenue) if revenue is not None else None)
        if any(not _finite(value) for row in numbers.values() for value in row):
            raise ValueError('FCFF forecast arithmetic: nonfinite calculation in ' + scope)
        if 'terminal_bridge' in scenario:
            terminal = scenario['terminal_bridge']
            if (not isinstance(terminal, dict) or set(terminal) != TERMINAL_KEYS
                    or any(not _finite(value) for value in terminal.values())
                    or terminal['expiring_product_loss'] < 0 or terminal['replacement_product_income'] < 0):
                raise ValueError('FCFF forecast arithmetic: invalid terminal bridge in ' + scope)
            issues = terminal_bridge_errors(numbers, scenario)
            if issues:
                raise ValueError('FCFF forecast arithmetic: ' + scope + ': ' + '; '.join(issues))
        result[scope] = {'final_year': {name: row[-1] for name, row in numbers.items()},
            'research_normalization_adjustment': numbers['research_amortization'][-1]
                - numbers['revenue'][-1] * scenario['capdev_pct'][-1]}
    return result
