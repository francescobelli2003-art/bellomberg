"""Managed-care records -> existing earnings/capital engines -> common result.

No ticker rules, provider refetch, numerical defaults or private-case imports.
Records carry the S2 metadata plus scenario/driver/kind/rationale/valid_until.
Each driver is bound to its value, entity, period, unit and accounting basis.
The workbook is a dated snapshot of these engines, not a second calculator.
"""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import re

from .dcf_quality import SCENARIOS, _date, _finite, _text, _evidence_issues, normalize_valuation_payload
from .managed_care import ANNUAL_INPUTS, METHOD, project_managed_care
from .distributable_equity import CAPITAL_FIELDS, SUB_FIELDS, CASH_SIGNS
from .method_registry import get_method_requirements


def _descriptor(driver):
    """Exact ownership of fields actually read by the two existing calculators."""
    actuals = {'premium': 'premium_revenue', 'medical_costs': 'medical_costs',
               'gna': 'operating_expenses', 'net_income': 'accounting_bridge'}
    if driver.startswith('actuals.') and driver[8:] in actuals:
        return actuals[driver[8:]], 'money', 'GAAP', 'actuals'
    if re.fullmatch(r'segments\.[^.]+\.(premium|mcr)', driver):
        return ('premium_revenue', 'money', 'GAAP', 'annual') if driver.endswith('.premium') else (
            'medical_costs', 'ratio', 'GAAP', 'annual')
    if driver in ANNUAL_INPUTS:
        field = {'other_premium': 'premium_revenue', 'other_medical_costs': 'medical_costs',
                 'diluted_shares_m': 'diluted_shares'}.get(driver, 'operating_expenses')
        unit = 'million shares' if driver == 'diluted_shares_m' else 'ratio' if driver in ('gna_ratio', 'investment_yield') else 'money'
        return field, unit, 'GAAP', 'annual'
    if re.fullmatch(r'adjustments_after_tax\.[^.]+', driver):
        return 'accounting_bridge', 'money', 'GAAP', 'annual'
    sub = re.fullmatch(r'capital\.subsidiaries\.(0|[1-9][0-9]*)\.([a-z_]+)', driver)
    if sub and sub[2] in SUB_FIELDS - {'id'}:
        key = sub[2]
        return ('legal_entity_liquidity' if key in ('liquidity_before_transfers', 'minimum_liquidity') else 'legal_entity_capital',
                'money', 'GAAP' if key in ('opening_gaap_equity', 'gaap_net_income') else 'statutory',
                'opening' if key.startswith('opening_') else 'future')
    if driver.startswith('capital.parent_cash_flows.') and driver.split('.')[-1] in CASH_SIGNS:
        return 'parent_ledger', 'money', 'cash', 'future'
    if driver.startswith('capital.') and driver[8:] in CAPITAL_FIELDS - {'subsidiaries', 'parent_cash_flows'}:
        key = driver[8:]
        field = {'ke': 'discount_rate_inputs', 'shares_m': 'diluted_shares', 'discount_periods': 'forecast_periods'}.get(key, 'parent_ledger')
        unit = 'text' if key in ('distribution_policy', 'reconciliation_basis', 'upstream_approval_basis', 'terminal_basis') else {
            'ke': 'ratio', 'shares_m': 'million shares', 'discount_periods': 'years'}.get(key, 'money')
        basis = 'GAAP' if key in ('parent_gaap_net_income', 'consolidation_adjustments') else 'cash' if field == 'parent_ledger' else 'valuation'
        timing = 'opening' if key in ('parent_opening_cash', 'parent_opening_debt', 'ke', 'shares_m') else 'terminal' if key.startswith('terminal_') else 'future'
        return field, unit, basis, timing
    return None


def _put(owner, path, value):
    keys = path.split('.')
    for key in keys[:-1]:
        if isinstance(owner, list):
            owner = owner[int(key)]
        else:
            owner = owner.setdefault(key, {})
    if isinstance(owner, list):
        owner[int(keys[-1])] = deepcopy(value)
    else:
        owner[keys[-1]] = deepcopy(value)


def prepare_managed_care_case(bundle):
    """Bind acquired records once. Unmapped/missing data cannot be certified by name."""
    raw, context = bundle['case'], bundle['analysis_context']
    records, problems, consumed, evidence = raw['records'], [], [], []
    cutoff = _date(raw['as_of'])

    def problem(field, reason):
        problems.append({'field': field, 'status': 'data_missing', 'reason': reason})

    if raw['assumptions']:
        problem('assumptions', 'Assunzioni non consumate: fornire record di driver documentati, non parametri operating')
    if not isinstance(context, dict) or set(context) - {'scenario_rationale', 'revisions'}:
        problem('analysis_context', 'Campi non consumati: usare scenario_rationale/revisions; prove derivate dai record acquisiti')
        context = context if isinstance(context, dict) else {}
    structures = {}
    expected_structures = {'perimeter': ('valuation_perimeter', 'scope'),
                           'calendar': ('forecast_periods', 'calendar'),
                           'quotation': ('quotation_units', 'quotation')}
    for driver, (field, basis) in expected_structures.items():
        matches = [r for r in records if r.get('driver') == driver and r.get('scenario') == 'model']
        if len(matches) != 1 or not isinstance(matches[0].get('value'), dict):
            problem(field, driver + ': serve un contratto univoco completo'); continue
        structures[driver] = matches[0]['value']
    perimeter, calendar, quotation = (structures.get(key, {}) for key in expected_structures)
    perimeter_keys = {'currency', 'consolidated_entity', 'parent_entity', 'subsidiaries', 'share_class', 'share_basis'}
    if set(perimeter) != perimeter_keys or any(not _text(perimeter.get(k)) for k in perimeter_keys - {'subsidiaries'}):
        problem('valuation_perimeter', 'Perimetro, valuta, parent e classe/base azioni espliciti obbligatori')
    currency = perimeter.get('currency')
    if not isinstance(currency, str) or not re.fullmatch('[A-Z]{3}', currency):
        problem('valuation_perimeter', 'Valuta di bilancio ISO non valida')
    subsidiaries = perimeter.get('subsidiaries')
    valid_subs = isinstance(subsidiaries, list) and bool(subsidiaries) and all(
        isinstance(s, dict) and set(s) == {'id', 'regime'} and _text(s.get('id')) and _text(s.get('regime')) for s in subsidiaries)
    if not valid_subs:
        problem('valuation_perimeter', 'Ogni entita legale richiede ID e regime documentati'); subsidiaries = []
    ids = [s['id'] for s in subsidiaries]
    identities = ids + [perimeter.get('parent_entity'), perimeter.get('consolidated_entity')]
    if (any(not isinstance(identity, str) or identity != identity.strip() for identity in identities)
            or len(set(identities)) != len(identities)):
        problem('valuation_perimeter', 'Entita duplicate o parent non distinto dal consolidato')
    calendar_keys = {'valuation_date', 'day_count', 'actuals_kind', 'actuals_start', 'fiscal_periods', 'years', 'actuals_year'}
    if set(calendar) != calendar_keys:
        problem('forecast_periods', 'Calendario esplicito completo richiesto; nessuna ipotesi legacy nell adapter')
    years, periods = calendar.get('years'), calendar.get('fiscal_periods')
    if (not isinstance(years, list) or not years or any(type(y) is not int for y in years)
            or not isinstance(periods, list) or len(periods) != len(years)
            or any(not isinstance(p, dict) or not _date(p.get('start')) or not _date(p.get('end')) for p in periods)
            or not _date(calendar.get('actuals_start')) or not _date(calendar.get('valuation_date'))):
        problem('forecast_periods', 'Anni e date fiscali mancanti/non validi')
    quote_keys = {'financial_currency', 'quote_currency', 'quote_unit', 'quote_units_per_currency',
                  'financial_to_quote_rate', 'shares_per_quote', 'share_class', 'price', 'price_as_of'}
    if set(quotation) != quote_keys:
        problem('quotation_units', 'Contratto quotazione/FX/azioni incompleto o con campi non consumati')
    for key in ('price', 'financial_to_quote_rate', 'quote_units_per_currency', 'shares_per_quote'):
        if not _finite(quotation.get(key)) or quotation[key] <= 0:
            problem('quotation_units', key + ': numero positivo esplicito richiesto')
    quote_currency = quotation.get('quote_currency')
    if not isinstance(quote_currency, str) or not re.fullmatch('[A-Z]{3}', quote_currency):
        problem('quotation_units', 'Valuta quotazione ISO non valida')
    if quotation.get('financial_currency') != currency or quotation.get('share_class') != perimeter.get('share_class'):
        problem('quotation_units', 'Bilancio e classe azioni non riconciliati al perimetro')
    if quotation.get('price_as_of') != calendar.get('valuation_date'):
        problem('quotation_units', 'Prezzo di data diversa dal ledger: acquisire rollforward o prezzo omogeneo')
    if quote_currency == currency and quotation.get('financial_to_quote_rate') != 1:
        problem('quotation_units', 'FX sulla stessa valuta diverso da uno')
    expected_unit = ('GBP', 100) if quotation.get('quote_unit') in ('GBX', 'GBp') else (quote_currency, 1)
    if quotation.get('quote_unit') not in (quote_currency, 'GBX', 'GBp') or (quote_currency, quotation.get('quote_units_per_currency')) != expected_unit:
        problem('quotation_units', 'Unita quotazione non riconciliata alla valuta; conversione non supportata')
    if problems:
        return None, {}, quotation, problems, []
    value_date = calendar['valuation_date']
    future_start = (_date(value_date) + timedelta(days=1)).isoformat()
    spans = {'annual': '|'.join(p['start'] + '/' + p['end'] for p in periods),
             'future': '|'.join((future_start if i == 0 else p['start']) + '/' + p['end'] for i, p in enumerate(periods)),
             'opening': value_date, 'terminal': periods[-1]['end'],
             'actuals': calendar['actuals_start'] + '/' + value_date}
    case = {'ticker': raw['ticker'], 'currency': currency, 'years': years,
            'calendar': {k: deepcopy(v) for k, v in calendar.items() if k not in ('years', 'actuals_year')},
            'actuals': {'year': calendar['actuals_year'], 'period_end': value_date},
            'scenarios': {s: {'adjustments_after_tax': {}, 'capital': {'subsidiaries': [{'id': i} for i in ids]}} for s in SCENARIOS}}
    seen, numeric = set(), []
    for index, row in enumerate(records):
        driver, scenario = row.get('driver'), row.get('scenario')
        identity = (scenario, driver) if isinstance(scenario, str) and isinstance(driver, str) else None
        errors = list(row.get('validation_issues') or [])
        allowed_keys = {'field', 'driver', 'scenario', 'value', 'entity', 'period', 'unit', 'accounting_basis',
                        'source_id', 'as_of', 'valid_until', 'kind', 'rationale', 'status', 'provider', 'validation_issues', 'source_locator'}
        if set(row) - allowed_keys:
            errors.append('record: campi non consumati')
        proof = {'scenario': scenario, 'driver': driver, 'values': deepcopy(row.get('value')),
                 'kind': row.get('kind'), 'source': row.get('source_id'), 'source_date': row.get('as_of'),
                 'valid_until': row.get('valid_until'), 'metric': driver, 'basis': row.get('accounting_basis'),
                 'rationale': row.get('rationale'), 'source_locator': row.get('source_locator'),
                 'entity': row.get('entity'), 'period': row.get('period'), 'unit': row.get('unit')}
        errors.extend(_evidence_issues(proof, cutoff))
        if identity is None or identity in seen:
            errors.append('driver/scenario assente, non testuale o duplicato')
        else:
            seen.add(identity)
        descriptor = _descriptor(driver) if isinstance(driver, str) else None
        if isinstance(driver, str) and driver in expected_structures and scenario == 'model':
            field, basis = expected_structures[driver]
            descriptor = (field, 'contract', basis, 'opening' if driver == 'quotation' else 'annual')
        elif descriptor is None or (scenario != 'model' if driver.startswith('actuals.') else scenario not in SCENARIOS):
            errors.append('input non consumato: driver/scenario estraneo al metodo')
        if descriptor:
            field, unit, basis, timing = descriptor
            entity = perimeter['consolidated_entity']
            match = re.fullmatch(r'capital\.subsidiaries\.([0-9]+)\.[a-z_]+', driver)
            if match:
                entity = ids[int(match[1])] if int(match[1]) < len(ids) else None
            elif field == 'parent_ledger':
                entity = perimeter['parent_entity']
            expected = {'field': field, 'unit': currency + ' million' if unit == 'money' else unit,
                        'accounting_basis': basis, 'entity': entity, 'period': spans[timing]}
            errors.extend(key + ': non riconciliato al driver consumato' for key, value in expected.items() if row.get(key) != value)
            if unit != 'contract' and unit != 'text':
                observed = _date(row.get('as_of'))
                if timing == 'actuals' and row.get('kind') != 'historical':
                    errors.append('consuntivo: serve una osservazione historical, non una stima')
                if timing in ('annual', 'future', 'terminal') and row.get('kind') == 'historical':
                    errors.append('forecast: dati o trasferimenti historical non diventano percorsi futuri')
                if timing in ('actuals', 'opening') and row.get('kind') == 'historical' and observed and observed < _date(value_date):
                    errors.append('fonte historical precedente alla chiusura del periodo osservato')
        if errors:
            problem(str(row.get('field') or driver), str(driver) + ': ' + '; '.join(errors)); continue
        consumed.append({'record_index': index, 'field': row['field'], 'scenario': scenario, 'driver': driver,
                         'entity': row['entity'], 'period': row['period'], 'source_id': row['source_id']})
        if driver not in expected_structures:
            _put(case if scenario == 'model' else case['scenarios'][scenario], driver, row['value'])
            if not isinstance(row['value'], str):
                numeric.append(proof)
        evidence.append({'scenario': scenario, 'driver': driver, 'values': deepcopy(row['value']), 'evidence': proof, 'issues': []})
    expected = {('model', 'actuals.' + key) for key in ('premium', 'medical_costs', 'gna', 'net_income')}
    for scenario in SCENARIOS:
        expected.update((scenario, key) for key in ANNUAL_INPUTS)
        expected.update((scenario, 'capital.' + key) for key in CAPITAL_FIELDS - {'subsidiaries', 'parent_cash_flows'})
        expected.update((scenario, 'capital.parent_cash_flows.' + key) for key in CASH_SIGNS)
        expected.update((scenario, f'capital.subsidiaries.{i}.' + key) for i in range(len(ids)) for key in SUB_FIELDS - {'id'})
        segment_names = {r['driver'].split('.')[1] for r in consumed if r['scenario'] == scenario and r['driver'].startswith('segments.')}
        if not segment_names:
            problem('premium_revenue', scenario + ': segmenti premi/MCR mancanti')
        for name in segment_names:
            expected.update((scenario, f'segments.{name}.' + key) for key in ('premium', 'mcr'))
        if not any(r['scenario'] == scenario and r['driver'].startswith('adjustments_after_tax.') for r in consumed):
            problem('accounting_bridge', scenario + ': riconciliazione adjusted esplicita richiesta, anche se nulla')
    for scenario, driver in sorted(expected - {(r['scenario'], r['driver']) for r in consumed}):
        problem(_descriptor(driver)[0], scenario + '/' + driver + ': dato richiesto non consumato')
    analysis = {'as_of': raw['as_of'], 'forecast_years': years, 'assumptions': numeric,
                'scenario_rationale': deepcopy(context.get('scenario_rationale')), 'revisions': deepcopy(context.get('revisions', []))}
    return case, analysis, quotation, problems, (consumed, evidence)


def generate_managed_care(bundle, *, output_dir, metadata):
    """Run the registered adapter. Calculations stay in the existing pure engines."""
    from .dcf_engine import sanity_check, _write_payload_sidecar
    case, analysis, quotation, problems, binding = prepare_managed_care_case(bundle)
    consumed, evidence = binding if binding else ([], [])
    projection = project_managed_care(case, analysis, today=bundle['case']['as_of']) if case is not None else {
        'status': 'n.d.', 'scenarios': {}, 'analytical_quality': {'status': 'INCOMPLETA', 'issues': [], 'rows': []}}
    quality = projection.pop('analytical_quality')
    quality['method_id'] = METHOD
    quality['rows'] = evidence or quality.get('rows', [])
    problems.extend({'field': 'managed_care', 'status': 'data_missing', 'reason': issue} for issue in quality.get('issues', []))
    quality['issues'] = list(dict.fromkeys(quality.get('issues', []) + [p['reason'] for p in problems]))
    quality['status'] = 'INCOMPLETA' if quality['issues'] else quality['status']
    required = [f['field'] for f in get_method_requirements(METHOD)['fields']]
    complete = not problems and len(consumed) == len(bundle['case']['records'])
    consumption = {'status': 'complete' if complete else 'incomplete', 'consumed_records': consumed,
                   'consumed_fields': sorted({r['field'] for r in consumed}),
                   'unconsumed_fields': sorted(set(required) - {r['field'] for r in consumed})}
    if problems:
        consumption['unconsumed_fields'] = sorted(set(consumption['unconsumed_fields']) | {p['field'] for p in problems})
    result = {**metadata, 'ok': complete, 'ticker': bundle['case']['ticker'], 'engine': 'managed_care',
              'method': METHOD, 'managed_care': projection, 'analytical_quality': quality,
              'input_consumption': consumption, 'currency': quotation.get('quote_unit'),
              'financial_currency': case['currency'] if case else None,
              'price': quotation.get('price'), 'valuation_date': projection.get('valuation_date'),
              'valuation_basis': projection.get('valuation_basis'),
              'acquisition_tasks': deepcopy(bundle['acquisition_tasks']) + problems}
    if quality['status'] == 'DOCUMENTATA' and complete:
        factor = quotation['financial_to_quote_rate'] * quotation['quote_units_per_currency'] * quotation['shares_per_quote']
        for scenario in SCENARIOS:
            value = projection['scenarios'][scenario]['fair_value_per_share']
            result['fair_value_' + scenario] = value * factor if _finite(value) else None
        checks = {s: sanity_check(result['fair_value_' + s], quotation['price']) for s in SCENARIOS}
        for scenario, check in checks.items():
            if check.get('status') != 'ok' or result['fair_value_' + scenario] is None or result['fair_value_' + scenario] <= 0:
                check.update(status='incomplete', severity='BLOCK', exclude_from_action_table=True,
                             headline='Sanity scenario ' + scenario + ' non disponibile: FV n.d.')
        severity = max((c['severity'] for c in checks.values()), key={'OK': 0, 'WARN': 1, 'BLOCK': 2}.get)
        result['sanity'] = {**checks['base'], 'severity': severity, 'method_id': METHOD,
                            'exclude_from_action_table': severity == 'BLOCK', 'scenario_checks': checks}
        # Price and FV share the ledger cutoff, not an implicitly current quote.
        result['upside_pct'] = checks['base'].get('upside_pct')
    else:
        result['sanity'] = {'status': 'incomplete', 'severity': 'BLOCK', 'method_id': METHOD,
                            'exclude_from_action_table': True, 'headline': 'Capitale, dati o documentazione incompleti: FV n.d.'}
    result = normalize_valuation_payload(result, expected_decision=bundle['decision'], as_of=bundle['case']['as_of'])
    if not result['valuation_usability']['usable']:
        result['ok'] = False
        result['error'] = 'Managed care: FV n.d.; ' + '; '.join(result['valuation_usability']['reasons'])
        result['acquisition_tasks'].append({'field': 'valuation_review', 'status': 'blocked',
                                           'reason': result['error']})
    if case is None:
        return result  # Retain the incomplete snapshot without inventing a workbook.
    if output_dir is None:
        from .dcf_engine import REPORT_DIR
        output_dir = str(REPORT_DIR)
    result['path'] = build_managed_care_workbook(result, output_dir)
    return _write_payload_sidecar(result)


def build_managed_care_workbook(payload, output_dir):
    """Persist a new snapshot; do not overwrite or move any previous workbook."""
    from openpyxl import Workbook
    from .dcf_quality_sheet import append_quality_sheet, append_sector_quality_sheet
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # Ticker is a label. Only safe characters participate in an artifact filename.
    symbol = re.sub(r'[^A-Za-z0-9_-]', '_', payload['ticker'])
    path = directory / ('VAL_' + symbol + '_' + payload['generation_id'] + '.xlsx')
    wb = Workbook()
    ws = wb.active
    ws.title = 'Managed care'
    rows = [['MANAGED CARE | SNAPSHOT', payload['ticker']], ['Fair value Base', payload.get('fair_value_base')],
            ['Data dei flussi / valore', payload.get('valuation_date')], ['Base informativa', payload['acquisition_snapshot']['case']['as_of']],
            ['Base valutazione', payload.get('valuation_basis')], ['Modifica ipotesi', 'Rigenerare il bundle e i controlli; questo file conserva il calcolo alla generazione.']]
    for row in rows:
        ws.append(row)
    for scenario, data in payload['managed_care'].get('scenarios', {}).items():
        ws.append([scenario, payload.get('fair_value_' + scenario)])
        for label, table in (('Conto economico', data.get('rows', [])), ('Capitale e parent', data.get('capital', {}).get('rows', []))):
            sheet = wb.create_sheet(label[:19] + ' ' + scenario)
            flat_rows = []
            def flatten(value, prefix=''):
                if isinstance(value, dict):
                    for key, item in value.items():
                        yield from flatten(item, prefix + '.' + key if prefix else key)
                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        yield from flatten(item, prefix + '.' + str(i))
                else:
                    yield prefix, value
            flat_rows = [dict(flatten(row)) for row in table]
            keys = list(dict.fromkeys(key for row in flat_rows for key in row))
            sheet.append(['Dato'] + [str(row.get('year')) for row in table])
            for key in keys:
                sheet.append([key] + [row.get(key) for row in flat_rows])
    append_quality_sheet(wb, payload['analytical_quality'])
    append_sector_quality_sheet(wb, payload)
    for sheet in wb:
        sheet.freeze_panes = 'B2'
        sheet.column_dimensions['A'].width = 48
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        # There are no duplicated valuation formulas or hidden live-input claims.
    wb.save(path)
    wb.close()
    return str(path)
