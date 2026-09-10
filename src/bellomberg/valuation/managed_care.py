"""Company managed-care scenarios, separate from automatic operating DCF routing.

All monetary inputs are in one declared currency, millions, annual accrual
amounts. Capital uses only the future period (remaining first year, then FY).
No provider, database, filesystem or implicit industry assumptions here.
"""
from copy import deepcopy
from datetime import date
from math import isclose

from .dcf_quality import SCENARIOS, NOTE, _finite, _date, _years, _text, _evidence_issues, _revision_rows, _portable


ANNUAL_INPUTS = (
    'other_premium', 'other_medical_costs', 'premium_tax_revenue', 'premium_tax_expense',
    'average_invested_assets', 'investment_yield', 'other_revenue', 'gna_ratio',
    'da', 'other_operating_costs', 'interest_expense', 'tax_expense', 'diluted_shares_m',
)
SIGNED_INPUTS = {'other_medical_costs', 'investment_yield', 'tax_expense'}
METHOD = 'managed_care_distributable_equity'


def _leaves(value, prefix=''):
    """The same driver IDs address the supplied evidence and the saved snapshot."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f'{prefix}.{key}' if prefix else str(key))
    elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        for i, item in enumerate(value):
            yield from _leaves(item, f'{prefix}.{i}')
    else:
        yield prefix, value


def _quality(case, context, results, today):
    context = context if isinstance(context, dict) else {}
    as_of = _date(context.get('as_of'))
    now = _date(today) if isinstance(today, str) else today or date.today()
    issues, rows = [], []
    if not isinstance(now, date):
        issues.append('today invalida: data di verifica non disponibile')
    if as_of is None or not isinstance(now, date) or as_of > now:
        issues.append('as_of assente/invalida o futura')
    if context.get('forecast_years') != case['years']:
        issues.append('forecast_years diversi dagli esercizi del caso')
    if as_of and _date(case['actuals']['period_end']) > as_of:
        issues.append('consuntivo successivo ad as_of')
    records = context.get('assumptions', [])
    if not isinstance(records, list):
        records = []; issues.append('assumptions deve essere una lista')
    expected = []
    for driver, value in _leaves(case['actuals']):
        if driver not in ('year', 'period_end'):
            expected.append(('model', 'actuals.' + driver, value))
    for scenario in SCENARIOS:
        if not isinstance(context.get('scenario_rationale'), dict) or not _text(context['scenario_rationale'].get(scenario)):
            issues.append(f'{scenario}: motivazione assente')
        for driver, value in _leaves(case['scenarios'][scenario]):
            # Structural labels describe policy; numerical drivers need dated evidence.
            if not isinstance(value, str):
                expected.append((scenario, driver, value))
        issues += [f'{scenario}: {i}' for i in results[scenario]['issues']]
        issues += [f'{scenario}: {i}' for i in results[scenario]['capital']['issues']]
    for scenario, driver, value in expected:
        evidence = [e for e in records if isinstance(e, dict)
                    and e.get('scenario') == scenario and e.get('driver') == driver]
        missing = []
        if len(evidence) != 1:
            missing.append('serve una prova univoca del driver')
        else:
            missing += _evidence_issues(evidence[0], as_of)
            if 'values' in evidence[0] and evidence[0]['values'] != value:
                missing.append('valori della prova diversi dagli input utilizzati')
        if value is None:
            missing.append('input mancante: n.d.')
        rows.append({'scenario': scenario, 'driver': driver, 'values': deepcopy(value),
                     'evidence': deepcopy(evidence[0]) if len(evidence) == 1 else None,
                     'issues': missing})
        issues += [f'{scenario}/{driver}: {i}' for i in missing]
    known = {(s, d) for s, d, _ in expected}
    if any(not isinstance(e, dict) or not isinstance(e.get('scenario'), str)
           or not isinstance(e.get('driver'), str)
           or (e.get('scenario'), e.get('driver')) not in known for e in records):
        issues.append('assumptions contiene prove di driver non consumati')
    revisions = _revision_rows(context.get('revisions', []), as_of, case['years'], {d for _, d in known})
    issues += ['revisione: ' + i for r in revisions for i in r['issues']]
    snapshot = {'version': 1, 'method': METHOD, 'ticker': case['ticker'],
                'currency': case['currency'], 'as_of': context.get('as_of'),
                'forecast_years': case['years'], 'actuals': deepcopy(case['actuals']),
                'calendar': deepcopy(case.get('calendar')),
                'scenarios': deepcopy(case['scenarios'])}
    return {'status': 'INCOMPLETA' if issues else 'DOCUMENTATA', 'note': NOTE,
            'issues': issues, 'rows': rows, 'snapshot': snapshot,
            'scenario_rationale': deepcopy(context.get('scenario_rationale', {})),
            'revision_rows': revisions,
            'revision_bridge': {'status': 'n.d.', 'steps': [],
                'reason': 'Metodo equity distinto dal vecchio operating: nessuna attribuzione del delta FV.',
                'note': 'Revisioni conservate come evidenze; non convertite automaticamente in flussi.'}}


def _scenario(raw, years, actuals, *, subtract_actuals=True):
    from .distributable_equity import project_distributable_equity
    issues, paths = [], {}
    if not isinstance(raw, dict):
        raw = {}; issues.append('scenario deve essere un oggetto')
    unexpected = set(raw) - set(ANNUAL_INPUTS) - {'segments', 'adjustments_after_tax', 'capital'}
    if unexpected:
        issues.append('input non consumati: ' + ', '.join(sorted(unexpected)))

    def path(name, values, signed=False, positive=False):
        if not isinstance(values, list) or len(values) != len(years) or not all(_finite(v) for v in values):
            issues.append(f'{name}: percorso numerico completo richiesto')
        elif (positive and any(v <= 0 for v in values)) or (not signed and any(v < 0 for v in values)):
            issues.append(f'{name}: segno non valido')
        else:
            paths[name] = list(values)

    segments = raw.get('segments')
    if not isinstance(segments, dict) or not segments:
        issues.append('segments: business mancanti'); segments = {}
    for name, segment in segments.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(segment, dict) or set(segment) != {'premium', 'mcr'}:
            issues.append('segments: nome/campi non validi'); continue
        for key in ('premium', 'mcr'):
            path(f'segments.{name}.{key}', segment.get(key))
    for key in ANNUAL_INPUTS:
        path(key, raw.get(key), signed=key in SIGNED_INPUTS, positive=key == 'diluted_shares_m')
    adjustments = raw.get('adjustments_after_tax')
    if not isinstance(adjustments, dict):
        issues.append('adjustments_after_tax: riconciliazione esplicita richiesta'); adjustments = {}
    for name, values in adjustments.items():
        path('adjustments_after_tax.' + str(name), values, signed=True)
    rows = []
    if not issues:
        for i, year in enumerate(years):
            p = {key: values[i] for key, values in paths.items()}
            detail = {name: {'premium': p[f'segments.{name}.premium'],
                            'medical_costs': p[f'segments.{name}.premium'] * p[f'segments.{name}.mcr']}
                      for name in segments}
            premium = sum(v['premium'] for v in detail.values()) + p['other_premium']
            medical = sum(v['medical_costs'] for v in detail.values()) + p['other_medical_costs']
            investment = p['average_invested_assets'] * p['investment_yield']
            revenue = premium + p['premium_tax_revenue'] + investment + p['other_revenue']
            gna = revenue * p['gna_ratio']
            pretax = revenue - medical - p['premium_tax_expense'] - gna - p['da'] - p['other_operating_costs'] - p['interest_expense']
            ni = pretax - p['tax_expense']
            adjusted = ni + sum(p['adjustments_after_tax.' + str(k)] for k in adjustments)
            row = {'year': year, 'segments': detail, 'premium': premium, 'medical_costs': medical,
                   'investment_income': investment, 'total_revenue': revenue, 'gna': gna,
                   'pretax_income': pretax, 'net_income': ni, 'adjusted_net_income': adjusted,
                   'eps': ni / p['diluted_shares_m'], 'adjusted_eps': adjusted / p['diluted_shares_m'],
                   'mcr': medical / premium if premium else None,
                   'premium_tax_difference': p['premium_tax_revenue'] - p['premium_tax_expense']}
            for metric in ('premium', 'medical_costs', 'gna', 'net_income'):
                residual = row[metric] - (actuals[metric] if i == 0 and subtract_actuals else 0.)
                row['remaining_' + metric] = residual
                if metric != 'net_income' and residual < -.01:
                    issues.append(f'{year}: {metric} annuo inferiore al consuntivo acquisito')
            if any(v is not None and isinstance(v, (float, int)) and not _finite(v) for v in row.values()):
                issues.append(f'{year}: risultato non finito')
            rows.append(row)
    if issues:
        rows = []
    capital = project_distributable_equity(raw.get('capital'),
                                          [r['remaining_net_income'] for r in rows], years)
    return {'status': 'n.d.' if issues else 'CALCOLABILE', 'issues': issues, 'rows': rows,
            'capital': capital, 'fair_value_per_share': None}


def _calendar(case):
    """Validate explicit fiscal intervals; legacy cases retain their original contract.

    A valuation at another date requires a new opening capital/cash ledger. We do
    not interpolate actuals or silently roll balances forward to a current price.
    ACT/365F is an explicit supported convention, not a fallback day count.
    """
    calendar, years, actuals = case.get('calendar'), case.get('years'), case.get('actuals') or {}
    issues, discounts = [], []
    if not isinstance(calendar, dict):
        return ['calendar: oggetto esplicito richiesto'], True
    if set(calendar) != {'valuation_date', 'day_count', 'actuals_kind', 'actuals_start', 'fiscal_periods'}:
        issues.append('calendar: campi assenti o non consumati')
    if calendar.get('day_count') != 'ACT/365F':
        issues.append('calendar.day_count: convenzione non supportata; specificare ACT/365F')
    cutoff, actual_end, actual_start = (_date(calendar.get('valuation_date')),
        _date(actuals.get('period_end')), _date(calendar.get('actuals_start')))
    if cutoff is None or cutoff != actual_end:
        issues.append('calendar.valuation_date: serve il cutoff del ledger; rollforward non fornito')
    if actual_start is None or actual_end is None or actual_start > actual_end:
        issues.append('calendar.actuals_start: periodo consuntivo invalido')
    kind = calendar.get('actuals_kind')
    if kind not in ('FY', 'interim'):
        issues.append('calendar.actuals_kind: serve FY o interim')
    periods = calendar.get('fiscal_periods')
    if not isinstance(periods, list) or len(periods) != len(years or []):
        return issues + ['calendar.fiscal_periods: un intervallo per esercizio richiesto'], kind == 'interim'
    previous_end = previous_payment = None
    for index, period in enumerate(periods):
        if not isinstance(period, dict) or set(period) != {'year', 'start', 'end', 'payment_date'}:
            issues.append('calendar.fiscal_periods: campi assenti o non consumati'); continue
        start, end, payment = (_date(period.get(key)) for key in ('start', 'end', 'payment_date'))
        if not start or not end or not payment or start > end:
            issues.append('calendar.fiscal_periods: date incompatibili'); continue
        if type(period['year']) is not int or period['year'] != years[index] or end.year != years[index]:
            issues.append('calendar.fiscal_periods: etichetta esercizio diversa dalla chiusura')
        if previous_end and (start - previous_end).days != 1:
            issues.append('calendar.fiscal_periods: intervalli sovrapposti o con buchi')
        if payment != end or not cutoff or payment <= cutoff or (previous_payment and payment <= previous_payment):
            issues.append('calendar.payment_date: supportata solo la chiusura FY; altre date richiedono ledger e terminale distinti')
        if index == 0 and actual_end:
            if kind == 'interim' and (actual_start != start or not start <= actual_end < end or actuals.get('year') != years[0]):
                issues.append('calendar: consuntivo interim incompatibile col primo esercizio')
            if kind == 'FY' and ((start - actual_end).days != 1 or actuals.get('year') != actual_end.year):
                issues.append('calendar: FY concluso non precede il primo esercizio previsto')
        if cutoff:
            discounts.append((payment - cutoff).days / 365.)
        previous_end, previous_payment = end, payment
    scenarios = case.get('scenarios')
    for name, scenario in (scenarios.items() if isinstance(scenarios, dict) else []):
        capital = scenario.get('capital') if isinstance(scenario, dict) else None
        if not isinstance(capital, dict):
            continue  # The existing ledger reports missing capital explicitly.
        supplied = capital.get('discount_periods')
        if (not isinstance(supplied, list) or len(supplied) != len(discounts)
                or any(not _finite(a) or not isclose(a, b, rel_tol=0., abs_tol=1e-10)
                       for a, b in zip(supplied, discounts))):
            issues.append(f'{name}: discount_periods non riconciliati a date e day_count')
    return issues, kind == 'interim'


def project_managed_care(case, analysis_context=None, *, today=None):
    """Return annual earnings, future capital ledger and documentary quality.

CALCOLABILE means arithmetic feasibility, never source verification. Fair
value is exposed only with complete evidence and a complete capital ledger.
It still needs independent economic review and the application's sanity gate.
"""
    issues = []
    if not isinstance(case, dict):
        case = {}
    explicit_calendar = 'calendar' in case
    years, actuals = _years(case.get('years')), case.get('actuals')
    if explicit_calendar:
        raw_years = case.get('years')
        years = list(raw_years) if (isinstance(raw_years, list) and raw_years
            and all(type(y) is int and 1 <= y <= 9999 for y in raw_years)
            and raw_years == list(range(raw_years[0], raw_years[0] + len(raw_years)))) else None
    if years is None:
        issues.append('years: esercizi consecutivi espliciti richiesti' if explicit_calendar else
                      'years: cinque esercizi consecutivi richiesti')
    if set(case) - {'ticker', 'currency', 'years', 'actuals', 'scenarios', 'calendar'}:
        issues.append('case: input non consumati')
    for key in ('ticker', 'currency'):
        if not isinstance(case.get(key), str) or not case[key].strip():
            issues.append(key + ' mancante')
    if not isinstance(actuals, dict):
        actuals = {}; issues.append('consuntivo infrannuale mancante')
    if set(actuals) - {'year', 'period_end', 'premium', 'medical_costs', 'gna', 'net_income'}:
        issues.append('actuals: input non consumati')
    end = _date(actuals.get('period_end'))
    if not explicit_calendar and (not years or actuals.get('year') != years[0] or end is None or end.year != years[0] or end.month == 12):
        issues.append('periodo consuntivo incompatibile col primo esercizio')
    for key in ('premium', 'medical_costs', 'gna', 'net_income'):
        if not _finite(actuals.get(key)) or (key != 'net_income' and actuals[key] < 0):
            issues.append('actuals.' + key + ' mancante/non valido')
    scenarios = case.get('scenarios')
    if not isinstance(scenarios, dict) or set(scenarios) != set(SCENARIOS):
        issues.append('servono esattamente bear/base/bull')
    subtract_actuals = True
    if explicit_calendar and years and isinstance(actuals, dict):
        calendar_issues, subtract_actuals = _calendar(case)
        issues.extend(calendar_issues)
    if issues:
        return {'status': 'n.d.', 'method': METHOD, 'issues': issues, 'scenarios': {},
                'analytical_quality': {'status': 'INCOMPLETA', 'issues': issues, 'note': NOTE}}
    results = {s: _scenario(scenarios[s], years, actuals, subtract_actuals=subtract_actuals) for s in SCENARIOS}
    quality = _quality(case, analysis_context, results, today)
    for value in results.values():
        if quality['status'] == 'DOCUMENTATA' and value['capital']['status'] == 'CALCOLABILE':
            value['fair_value_per_share'] = value['capital']['fair_value_per_share']
        else:
            # The pure ledger exposes arithmetic values; this documented wrapper
            # must gate nested valuation outputs as well as the scenario headline.
            value['capital']['fair_value_per_share'] = None
            value['capital']['equity_value'] = None
            value['capital']['valuation_gate_reason'] = (
                f"FV n.d.: qualita' {quality['status']}; capitale {value['capital']['status']}. "
                "Servono prove complete e capitale calcolabile; flussi aritmetici conservati per review.")
    return _portable({'status': 'CALCOLABILE' if all(r['status'] == 'CALCOLABILE' for r in results.values()) else 'n.d.',
                      'method': METHOD, 'ticker': case['ticker'], 'currency': case['currency'],
                      'years': years, 'cashflow_cutoff': actuals['period_end'],
                      'valuation_date': case.get('calendar', {}).get('valuation_date', actuals['period_end']),
                      'valuation_basis': 'Ricalcolo al cutoff contabile con informazioni as_of; non FV storico o upside al prezzo corrente.',
                      'issues': [], 'scenarios': results, 'analytical_quality': quality,
                      'note': 'Conti annuali e flussi futuri distinti. Nessun net debt aggiunto; nessuna attivazione del routing o sanity certificata.'})
