"""Linked formula sheets for documented fund and digital-asset NAV snapshots."""
from math import isfinite

from openpyxl.comments import Comment
from openpyxl.styles import Font

from bellomberg.core.language import text as tr
from .documented_formulas import _formula
from .documented_presentation import _finish, _header, _line, _put, _sheet, AMOUNT, PRICE, PERCENT

BLUE = "0000FF"
NAV_METHODS = {"fund_nav", "digital_asset_nav"}
SCENARIOS = ("bear", "base", "bull")


def _records(payload):
    out = {}
    for record in (payload.get("acquisition_snapshot", {}).get("case", {}).get("records", []) or []):
        driver = record.get("driver")
        if driver in {"nav_target", "target_basis"}:
            out.setdefault(driver, {})[record.get("scenario")] = record.get("value")
        else:
            out[driver] = record.get("value")
    return out


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _source(records, driver):
    for row in (records.get("__rows__") or []):
        if row.get("driver") == driver:
            return str({k: row.get(k) for k in ('source_id', 'source_locator', 'as_of', 'kind', 'period', 'unit', 'rationale')})
    return "record source n.d."


def _inputs_sheet(wb, payload, records, digital):
    ws = _sheet(wb, "NAV Inputs", tr("Input NAV documentati", "Documented NAV inputs"), 8)
    _line(ws, 4, tr("Blu: input modificabili per simulazione; le formule non approvano nuove ipotesi.",
                     "Blue: editable simulation inputs; formulas do not approve new assumptions."), 8, height=34)
    _header(ws, 7, [tr("Input", "Input"), tr("Unità", "Unit"), "Value", tr("Fonte", "Source"), "", "", ""])
    row = 8
    refs = {}

    def put(label, unit, value, key, fmt=AMOUNT, comment=None):
        nonlocal row
        _put(ws, row, 2, label)
        _put(ws, row, 3, unit)
        cell = _put(ws, row, 4, value, fmt)
        cell.font = Font(name="Arial", size=10, color=BLUE)
        cell.comment = Comment(comment or _source(records, key), "Bellomberg")
        refs[key] = f"'NAV Inputs'!D{row}"
        row += 1
        return row - 1

    q = records.get("quotation") or {}
    put(tr("Azioni originali", "Original shares"), "mln", records.get("shares"), "shares", PRICE)
    put(tr("Prezzo quotato", "Quoted price"), q.get("quote_unit", "n.d."), q.get("price"), "quotation_price", PRICE, comment=_source(records, "quotation"))
    put(tr("Fattore valuta finanziaria→quotata", "Financial→quoted FX"), "ratio", q.get("financial_to_quote_rate"), "quote_fx", PERCENT, comment=_source(records, "quotation"))
    put(tr("Unità quotate per valuta", "Quoted units per currency"), "ratio", q.get("quote_units_per_currency"), "quote_units", PRICE, comment=_source(records, "quotation"))
    put(tr("Azioni per quota", "Shares per quote"), "ratio", q.get("shares_per_quote"), "quote_shares", PRICE, comment=_source(records, "quotation"))
    if digital:
        components = records.get("components") or {}
        for key in ("cash", "debt", "preferred", "other_liabilities", "accrued_fees", "distributions_payable", "tax", "equity_adjustments", "operating_assets"):
            put(key, tr("mln valuta", "currency million"), components.get(key), key, comment=_source(records, "components"))
        cap = records.get("capitalization") or {}
        put(tr("Azioni base", "Basic shares"), "mln", cap.get("basic_shares"), "basic_shares", PRICE, comment=_source(records, "capitalization"))
        refs["conversion_rows"] = []
        for idx, claim in enumerate(cap.get("conversions", []), 1):
            face = put(f"conversion {idx} face", "mln valuta", claim.get("face_value"), "capitalization")
            shares = put(f"conversion {idx} shares", "mln", claim.get("shares"), "capitalization", PRICE)
            refs["conversion_rows"].append((face, shares))
        refs["warrant_rows"] = []
        for idx, claim in enumerate(cap.get("warrants", []), 1):
            strike = put(f"warrant {idx} strike", "valuta", claim.get("strike"), "capitalization", PRICE)
            shares = put(f"warrant {idx} shares", "mln", claim.get("shares"), "capitalization", PRICE)
            refs["warrant_rows"].append((strike, shares))
        refs["asset_rows"] = {}
        for asset, holding in (records.get("holdings") or {}).items():
            price = (records.get("asset_prices") or {}).get(asset) or {}
            quantity = put(f"{asset} quantity", "mln", holding.get("quantity_millions"), "holdings", PRICE)
            ownership = put(f"{asset} ownership", "ratio", holding.get("ownership_fraction"), "holdings", PERCENT)
            price_row = put(f"{asset} price", price.get("currency", "n.d."), price.get("price"), "asset_prices", PRICE)
            fx = put(f"{asset} FX", "ratio", price.get("fx_to_financial"), "asset_prices", PRICE)
            refs["asset_rows"][asset] = (quantity, ownership, price_row, fx)
    else:
        for key in ("gross_assets", "cash", "debt", "preferred", "other_liabilities", "accrued_fees", "distributions_payable", "tax", "equity_adjustments"):
            put(key, tr("mln valuta", "currency million"), (records.get("components") or {}).get(key), key, comment=_source(records, "components"))
    targets = {}
    for scenario in SCENARIOS:
        target_row = put(f"target {scenario}", "ratio", (records.get("nav_target") or {}).get(scenario), "nav_target", PRICE)
        targets[scenario] = f"'NAV Inputs'!D{target_row}"
    refs["target_rows"] = targets
    _finish(ws, row + 1, 8)
    return ws, refs


def _checks(wb, payload, refs, digital):
    ws = _sheet(wb, "Model Checks", tr("Controlli NAV", "NAV model checks"), 6)
    _header(ws, 7, [tr("Controllo", "Check"), tr("Esito", "Result"), tr("Nota", "Note"), "", "", ""])
    records = _records(payload)
    checks = [
        (tr("Input numerici", "Numeric inputs"), f"COUNT('NAV Inputs'!D8:D{refs['last_row']})={refs['last_row']-7}", tr("nessun proxy", "no proxy")),
        (tr("Azioni > 0", "Shares > 0"), f"'NAV Inputs'!D8>0", tr("denominatore valido", "valid denominator")),
        (tr("FX, prezzo e scale > 0", "FX, price and scales > 0"), f"AND({refs['quotation_price']}>0,{refs['quote_fx']}>0,{refs['quote_units']}>0,{refs['quote_shares']}>0)", tr("quote scale", "quote scale")),
    ]
    q = records['quotation']
    checks.append(('Quote unit scale', f"{refs['quote_units']}={100 if q['quote_unit'] in ('GBX','GBp') else 1}", ''))
    if q['financial_currency'] == q['quote_currency']:
        checks.append(('Same currency FX', refs['quote_fx']+'=1', ''))
    for key in ('cash', 'debt', 'operating_assets' if digital else 'gross_assets'):
        checks.append((key, refs[key]+'>=0', ''))
    for scenario in SCENARIOS:
        checks.append(('Target '+scenario, refs['target_rows'][scenario]+'>0', ''))
    claim_keys = ("preferred", "other_liabilities", "accrued_fees", "distributions_payable", "tax")
    checks.append((tr("Claims >= 0", "Claims >= 0"), "AND(" + ",".join(f"{refs[k]}>=0" for k in claim_keys) + ")", tr("equity adjustments signed", "equity adjustments signed")))
    if digital:
        checks.append(('Basic shares', refs['basic_shares']+'>0', ''))
        for asset, (quantity, ownership, price, fx) in refs.get("asset_rows", {}).items():
            checks.append((f"{asset} ownership/FX", f"AND('NAV Inputs'!D{quantity}>=0,'NAV Inputs'!D{ownership}>0,'NAV Inputs'!D{ownership}<=1,'NAV Inputs'!D{price}>0,'NAV Inputs'!D{fx}>0)", tr("ownership e FX validi", "valid ownership and FX")))
            if records['asset_prices'][asset]['currency'] == q['financial_currency']:
                checks.append((asset+' same currency FX', f"'NAV Inputs'!D{fx}=1", ''))
        quote = f"({refs['quotation_price']}/({refs['quote_fx']}*{refs['quote_units']}*{refs['quote_shares']}))"
        for face, shares in refs['conversion_rows']:
            checks.append(('Conversion ITM', f"AND('NAV Inputs'!D{face}>=0,'NAV Inputs'!D{shares}>0,'NAV Inputs'!D{face}<{quote}*'NAV Inputs'!D{shares})", ''))
        for strike, shares in refs['warrant_rows']:
            checks.append(('Warrant ITM', f"AND('NAV Inputs'!D{strike}>=0,'NAV Inputs'!D{shares}>0,'NAV Inputs'!D{strike}<{quote})", ''))
        release = '+'.join(f"'NAV Inputs'!D{face}" for face, _ in refs['conversion_rows']) or '0'
        checks.append(('Debt released <= debt', '('+release+')<='+refs['debt'], ''))
    for row, (label, expression, note) in enumerate(checks, 8):
        _put(ws, row, 2, label)
        _formula(ws, row, 3, f'IFERROR(IF({expression},"OK","KO"),"KO")')
        _put(ws, row, 4, note)
    _finish(ws, 8 + len(checks), 6)
    refs['guard'] = f"COUNTIF('Model Checks'!C8:C{7+len(checks)},\"KO\")=0"
    refs['baseline_row'] = 11 + len(checks)
    return ws


def _model_sheet(wb, payload, records, refs, digital, ready):
    ws = _sheet(wb, "NAV Model", tr("Modello NAV collegato", "Linked NAV model"), 6)
    _line(ws, 4, tr("Simulazione formula; i fogli raw e Valuation restano invariati.",
                     "Formula simulation; raw scenario sheets and Valuation remain unchanged."), 6, height=34)
    _header(ws, 7, [tr("Voce", "Item"), tr("Unità", "Unit"), *SCENARIOS])
    rows = {"gross": 8, "cash": 9, "debt": 10, "preferred": 11, "claims": 12,
            "adjustments": 13, "shares": 14, "equity": 15, "navps": 16, "target": 17, "fv": 18, "quoted": 19}
    labels = {
        "gross": ("Attività lorde", "Gross assets"), "cash": ("Liquidità", "Cash"),
        "debt": ("Debito", "Debt"), "preferred": ("Azioni privilegiate", "Preferred"),
        "claims": ("Altre pretese", "Other claims"), "adjustments": ("Rettifiche equity", "Equity adjustments"),
        "shares": ("Azioni FD", "FD shares"), "equity": ("NAV equity", "Equity NAV"),
        "navps": ("NAV per azione", "NAV per share"), "target": ("Target", "Target"),
        "fv": ("Fair value per azione", "Fair value per share"), "quoted": ("Fair value quotato", "Quoted fair value"),
    }
    for key, row in rows.items():
        _put(ws, row, 2, tr(*labels[key]), bold=key in {"equity", "fv", "quoted"})
        _put(ws, row, 3, "mln" if key in {"gross", "cash", "debt", "preferred", "claims", "adjustments", "equity"} else ("mln shares" if key == "shares" else ("ratio" if key == "target" else payload.get("currency" if key == "quoted" else "financial_currency", "n.d."))))
    if ready:
        if digital:
            asset_terms = [f"'NAV Inputs'!D{q}*'NAV Inputs'!D{o}*'NAV Inputs'!D{p}*'NAV Inputs'!D{x}"
                           for q, o, p, x in refs.get("asset_rows", {}).values()]
            conversion_shares = [f"'NAV Inputs'!D{s}" for _, s in refs.get("conversion_rows", [])]
            warrant_shares = [f"'NAV Inputs'!D{s}" for _, s in refs.get("warrant_rows", [])]
            warrant_cash = [f"'NAV Inputs'!D{strike}*'NAV Inputs'!D{s}" for strike, s in refs.get("warrant_rows", [])]
            debt_release = [f"'NAV Inputs'!D{face}" for face, _ in refs.get("conversion_rows", [])]
            refs.update({"asset_expr": "+".join(asset_terms) or "0",
                         "conversion_shares_expr": "+".join(conversion_shares) or "0",
                         "warrant_shares_expr": "+".join(warrant_shares) or "0",
                         "warrant_cash_expr": "+".join(warrant_cash) or "0",
                         "debt_release_expr": "+".join(debt_release) or "0"})
        for col_idx, scenario in enumerate(SCENARIOS, 4):
            c = chr(64 + col_idx)
            if digital:
                _formula(ws, rows['preferred'], col_idx, refs['preferred'])
                _formula(ws, rows['adjustments'], col_idx, refs['equity_adjustments'])
                _formula(ws, rows["gross"], col_idx, f"{refs['asset_expr']}+{refs['operating_assets']}")
                _formula(ws, rows["cash"], col_idx, f"{refs['cash']}+({refs['warrant_cash_expr']})")
                _formula(ws, rows["debt"], col_idx, f"{refs['debt']}-({refs['debt_release_expr']})")
                _formula(ws, rows["shares"], col_idx, f"{refs['basic_shares']}+({refs['conversion_shares_expr']})+({refs['warrant_shares_expr']})", PRICE)
            else:
                for key, input_key in (("gross", "gross_assets"), ("cash", "cash"), ("debt", "debt"), ("preferred", "preferred"), ("adjustments", "equity_adjustments"), ("shares", "shares")):
                    _formula(ws, rows[key], col_idx, refs[input_key], PRICE if key == "shares" else AMOUNT)
            claim_keys = ("preferred", "other_liabilities", "accrued_fees", "distributions_payable", "tax")
            claim_expr = "+".join(refs[key] for key in claim_keys)
            _formula(ws, rows["claims"], col_idx, claim_expr)
            _formula(ws, rows["equity"], col_idx, f"{c}{rows['gross']}+{c}{rows['cash']}-{c}{rows['debt']}-{c}{rows['claims']}+{c}{rows['adjustments']}")
            _formula(ws, rows["navps"], col_idx, f'IFERROR(IF({refs["guard"]},{c}{rows["equity"]}/{c}{rows["shares"]},"n.d."),"n.d.")', PRICE)
            _formula(ws, rows["target"], col_idx, refs["target_rows"][scenario], PRICE)
            expression = (f"{c}15*{c}17/{c}14" if records['target_basis'][scenario] == 'equity_nav' else
                          f"({c}8*{c}17+{c}9-{c}10-{c}12+{c}13)/{c}14")
            _formula(ws, rows['fv'], col_idx, f'IFERROR(IF({refs["guard"]},{expression},"n.d."),"n.d.")', PRICE)
            _formula(ws, rows["quoted"], col_idx, f'IFERROR(ROUND({c}{rows["fv"]}*{refs["quote_fx"]}*{refs["quote_units"]}*{refs["quote_shares"]},2),"n.d.")', PRICE)
        if digital:
            # FD shares are computed once; the original denominator remains in the raw baseline.
            _formula(wb['NAV Inputs'], 8, 4, "'NAV Model'!D14", PRICE)
    else:
        for row in rows.values():
            for col_idx in range(4, 7):
                _put(ws, row, col_idx, "n.d.")
    _finish(ws, 20, 6)
    for row in ws:
        for cell in row:
            if cell.data_type == 'f' and not cell.value.startswith('=IFERROR('):
                cell.value = '=IFERROR(' + cell.value[1:] + ',"n.d.")'
    return ws


def apply_nav_formulas(wb, payload):
    """Build linked NAV sheets and return whether the payload passed the readiness gate."""
    method = payload.get("method")
    if method not in NAV_METHODS:
        return False
    records = _records(payload)
    rows = payload.get("acquisition_snapshot", {}).get("case", {}).get("records", []) or []
    records["__rows__"] = rows
    digital = method == "digital_asset_nav"
    ready = bool(payload.get("valuation_usability", {}).get("usable"))
    scenarios = payload.get("calculation_details", {}).get("scenarios", {})
    ready = ready and all(_finite(scenarios.get(s, {}).get("fair_value_per_share")) for s in SCENARIOS)
    required = ("components", "shares", "quotation", "nav_target")
    ready = ready and all(key in records for key in required)
    if digital:
        ready = ready and all(key in records for key in ("holdings", "asset_prices", "capitalization"))
    if not ready:
        return False
    for name in ("Summary", "NAV Model", "NAV Inputs", "Model Checks"):
        if name in wb:
            del wb[name]
    inputs, refs = _inputs_sheet(wb, payload, records, digital)
    refs["last_row"] = inputs.max_row
    if digital:
        refs.update({"warrant_cash": None, "debt_release": None, "conversion_shares": None, "warrant_shares": None})
    checks = _checks(wb, payload, refs, digital)
    _model_sheet(wb, payload, records, refs, digital, ready)
    _header(checks, refs['baseline_row'], ['Scenario', tr('Motore', 'Engine'), 'Excel', tr('Differenza', 'Difference'), tr('Stato', 'State')])
    for i, scenario in enumerate(SCENARIOS, refs['baseline_row']+1):
        column = chr(68 + i - refs['baseline_row'] - 1)
        _put(checks, i, 2, scenario)
        _put(checks, i, 3, scenarios[scenario]['fair_value_per_share'], PRICE)
        _formula(checks, i, 4, f"'NAV Model'!{column}18", PRICE)
        _formula(checks, i, 5, f'IFERROR(D{i}-C{i},"n.d.")', PRICE)
        _formula(checks, i, 6, f'IFERROR(IF(ABS(E{i})<0.0000001*MAX(1,ABS(C{i})),"OK","SIMULAZIONE"),"KO")', 'General')
    _finish(checks, refs['baseline_row']+5, 6)
    summary = _sheet(wb, "Summary", tr("Sintesi NAV", "NAV summary"), 6)
    _header(summary, 7, [tr("Scenario", "Scenario"), tr("Fair value", "Fair value"), tr("Unità", "Unit"), "", "", ""])
    for row, scenario in enumerate(SCENARIOS, 8):
        _put(summary, row, 2, scenario)
        if ready:
            _formula(summary, row, 3, f"'NAV Model'!{chr(64 + row - 4)}19", PRICE)
        else:
            _put(summary, row, 3, "n.d.")
        _put(summary, row, 4, payload.get("currency", "n.d."))
    _line(summary, 13, tr("Input modificabili = simulazione; record approvati e Valuation raw restano la fonte.",
                          "Editable inputs are simulations; approved records and raw Valuation remain the source."), 6, height=36)
    _finish(summary, 14, 6)
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    wb.move_sheet(summary, -wb.index(summary))
    link_refs = {('model','components',k): refs[k] for k in records['components'] if k in refs}
    link_refs[('model','shares')] = "'NAV Model'!D14"
    if digital:
        link_refs[('model','capitalization','basic_shares')] = refs['basic_shares']
    wb._model_link = {'refs': link_refs,
        'raw': {s: f"'NAV Model'!{chr(68+i)}18" for i,s in enumerate(SCENARIOS)},
        'shares': {s: f"'NAV Model'!{chr(68+i)}14" for i,s in enumerate(SCENARIOS)},
        'guards': {s: f'IF({refs["guard"]},"OK","KO")' for s in SCENARIOS},
        'calls': {s: [] for s in SCENARIOS}}
    return ready
