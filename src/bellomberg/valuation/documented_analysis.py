"""Common reader-facing contract summary for documented valuation payloads."""

from math import ceil
from urllib.parse import urlsplit
from openpyxl.styles import Alignment
from .documented_presentation import _finish, _header, _line, _put, _sheet
from bellomberg.core.language import text as tr


def _evidence(payload):
    return payload.get("analytical_quality", {}).get("rows", [])


def _first(rows, driver):
    return next((row for row in rows if row.get("scenario") == "model" and row.get("driver") == driver), None)


def _family(method):
    labels = {
        "operating_fcff": ("FCFF: crescita, capacit\u00e0, reinvestimento e stabilizzazione terminale", "FCFF: growth, capacity, reinvestment and terminal stabilisation"),
        "fund_nav": ("Attivit\u00e0, claims, diluizione e target NAV", "Assets, claims, dilution and target NAV"),
        "digital_asset_nav": ("Attivit\u00e0, claims, diluizione e target NAV", "Assets, claims, dilution and target NAV"),
        "property_nav": ("Attivit\u00e0, claims, diluizione e target NAV", "Assets, claims, dilution and target NAV"),
        "exposure_analysis": ("ETF/exposure: holdings, costi, replica e rischi", "ETF/exposure: holdings, costs, replication and risks"),
    }
    if method in labels:
        return tr(*labels[method])
    if method in {"bank_residual_income", "managed_care_distributable_equity", "insurance_pc_distributable_equity", "insurance_life_distributable_equity"}:
        return tr("Capitale regolamentare, redditivit\u00e0 e distribuzioni", "Regulatory capital, profitability and distributions")
    if method in {"resources_asset_dcf", "property_development_fcff", "development_rnpv"}:
        return tr("Vita dell'asset, costi, finanziamento e chiusura", "Asset life, costs, financing and closure")
    if method == "mixed_business_sotp":
        return tr("Riconciliazione segmenti, ownership, claims e costi parent", "Segment, ownership, claims and parent-cost reconciliation")
    if method == "regulated_rab":
        return tr("Base regolatoria, capex, ritorno ammesso e distribuzioni", "Regulatory base, capex, allowed return and distributions")
    return tr("Driver economici specifici del metodo: n.d.", "Method-specific economic drivers: n.a.")


def _calendar(values, years):
    periods = values.get("periods") or []
    dates = (str(periods[0].get('start', 'n.d.')) + ' → ' + str(periods[-1].get('end', 'n.d.'))
             if periods else ', '.join(map(str, years)))
    count = len(periods) if periods or values.get("discount_convention") == "snapshot" else "n.d."
    return (f"{values.get('valuation_date', 'n.d.')} | n={count} | "
            f"{values.get('discount_convention', 'n.d.')} | {dates or 'n.d.'}")


def _merge_value(ws, row, value):
    ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=6)
    cell = _put(ws, row, 3, value)
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    lines = sum(max(1, ceil(len(line)/60)) for line in str(value).split('\n'))
    ws.row_dimensions[row].height = max(30, min(409, 18 + 15 * lines))


def _source(value):
    value = str(value or "n.d.")
    if value.startswith(('https://', 'http://')):
        return urlsplit(value).netloc + tr(' — fonte completa in Qualita e revisioni', ' — full source in Qualita e revisioni')
    return value if len(value) <= 180 else tr("Qualità e revisioni (fonte completa)", "Quality and revisions (full source)")


def _rationale_chunks(value, width=900):
    value = str(value or "n.d.")
    chunks = []
    while value:
        cut = min(width, len(value))
        newlines = [i for i, c in enumerate(value[:cut]) if c == '\n']
        if len(newlines) >= 10:
            cut = newlines[9] + 1
        chunks.append(value[:cut]); value = value[cut:]
    return chunks


def present_analysis(wb, payload):
    """Append a concise, non-authoritative analysis contract sheet."""
    if "Analysis" in wb:
        del wb["Analysis"]
    years = (payload.get("analytical_quality", {}).get("snapshot", {})
             .get("forecast_years") or [])
    ws = _sheet(wb, "Analysis", payload.get("ticker", "") + " — " + tr("Analisi e limiti", "Analysis and limitations"), 6)
    target = 1 if "Valuation" in wb or "Summary" in wb or payload.get("method") == "managed_care_distributable_equity" else 0
    wb.move_sheet(ws, target - wb.index(ws))
    _line(ws, 4, tr("Sintesi del perimetro documentato; non approva nuove ipotesi.",
                     "Documented perimeter summary; it does not approve new assumptions."), 6, height=34)
    _header(ws, 7, [tr("Voce", "Item"), tr("Valore / dettaglio", "Value / detail")])
    ws.merge_cells(start_row=7, start_column=3, end_row=7, end_column=6)
    rows = _evidence(payload)
    perimeter = _first(rows, "perimeter")
    calendar = _first(rows, "calendar")
    from .analysis_standard import assess_standard
    standard = assess_standard(payload)
    horizon = (tr('Obiettivo: 10 anni per bear/base/bull. Periodi presenti: ', 'Target: 10 years in bear/base/bull. Supplied periods: ')
               + str(standard['actual_annual_periods']) + ' | ' + standard['horizon_status']
               if standard['target_annual_periods'] else
               tr('Orizzonte specifico del metodo: ', 'Method-specific horizon: ') + standard['horizon_basis'])
    values = [
        (tr("Metodo / famiglia", "Method / family"), payload.get("method", "n.d."), _family(payload.get("method"))),
        (tr("Completezza degli input", "Input completeness"), payload.get("input_consumption", {}).get("status", "n.d."), payload.get("analytical_quality", {}).get("status", "n.d."), tr("La presenza di tutti gli input non certifica la solidita delle ipotesi economiche.", "Having all inputs does not certify the strength of economic assumptions.")),
        (tr("Perimetro", "Perimeter"), "; ".join(str(v) for v in ((perimeter or {}).get("values") or {}).values()), _source((perimeter or {}).get("evidence", {}).get("source"))),
        (tr("Calendario", "Calendar"), _calendar((calendar or {}).get("values") or {}, years), _source((calendar or {}).get("evidence", {}).get("source"))),
        (tr("Data dei valori / cutoff informativo", "Value date / information cutoff"), payload.get("valuation_date", "n.d."), (payload.get('acquisition_snapshot') or {}).get('case', {}).get('as_of', 'n.d.')),
        (tr("Storico pluriennale / comparabili", "Multi-year history / comps"),
         tr('Storico, guidance e consensus acquisiti: fogli Source, con dati mancanti dichiarati. Omogeneita dei periodi e multipli dei comparabili non certificati.',
            'Acquired history, guidance and consensus: Source sheets, with missing data declared. Period comparability and peer multiples are not certified.')
         if 'Source History' in wb else tr("n.d. — serie omogenee e confronto dei multipli non inclusi in questo modello.", "n.a. — comparable historical series and peer multiples are not included in this model.")),
        (tr("Verifica economica richiesta", "Required economic review"), tr("Motivare durata della crescita, driver e scenario avverso. Verificare il capitale necessario e il passaggio al regime stabile o alla fine della vita dell asset, secondo il metodo.", "Justify growth duration, drivers and the downside case. Verify capital needs and the transition to steady operations or asset expiry, according to the method.")),
        (tr('Standard comune di previsione', 'Common forecast standard'), horizon, standard.get('horizon_rationale')),
    ]
    for row, values_row in enumerate(values, 8):
        _put(ws, row, 2, values_row[0])
        detail = "\n".join((str(value) for value in values_row[1:] if value not in (None, "")))
        _merge_value(ws, row, detail or tr("n.d.", "n.a."))
    context = ((payload.get("acquisition_snapshot") or {}).get("analysis_context") or {})
    rationale = context.get("scenario_rationale") or {}
    _line(ws, 17, tr("Motivazioni degli scenari", "Scenario rationales"), 6, True)
    row = 19
    for scenario in ("bear", "base", "bull"):
        chunks = _rationale_chunks(rationale.get(scenario, "n.d."))
        for index, chunk in enumerate(chunks):
            _put(ws, row, 2, "SCENARIO " + scenario if index == 0 else "")
            _merge_value(ws, row, chunk)
            row += 1
    _line(ws, row + 1, tr("Driver materiali: fonti, kind, unità, periodo e motivazioni complete restano in Qualita e revisioni.",
                          "Material drivers: full sources, kind, units, periods and rationales remain in Qualita e revisioni."), 6, height=32)
    row += 3
    _line(ws, row, tr("Stato del modello", "Model state"), 6, True)
    row += 2
    state = (tr("Formule collegate: verificare Model Checks dopo ogni modifica.", "Linked formulas: review Model Checks after every edit.")
             if "Model Checks" in wb else
             tr("Snapshot statico: rigenerare per cambiare ipotesi.", "Static snapshot: regenerate to change assumptions."))
    _merge_value(ws, row, state)
    _finish(ws, row + 2, 6)
    return ws
