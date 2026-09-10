"""Documentary quality of resolved operating DCF inputs; no I/O or new valuation engine.

Evidence is supplied, not independently verified. The bridge reprices previous
driver paths with current anchors and must never be called historical attribution.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
from math import isfinite
import re
from urllib.parse import urlparse


SCENARIOS = ("bear", "base", "bull")
# The two o/w personnel/services splits do not change UFCF.
MATERIAL_DRIVERS = ("revenue_growth", "gross_margin", "rnd_pct", "sga_pct", "capdev_pct",
                    "nwc_pct", "capex_pct", "tax_rate", "da_tan_pct")
MODEL_DRIVERS = ("last_revenue", "nwc0", "wacc", "terminal_g", "shares_m", "net_debt", "ronic")
CONTROL_KEYS = ("wacc_used", "terminal_g_used", "ronic_used", "terminal_method", "terminal_warnings",
                "mid_year", "wacc_delta_bp_used", "wacc_bear", "wacc_bull", "equity_adjustments_used",
                "equity_adjustments_total", "method_weights_used")
NOTE = ("Completezza documentale delle assunzioni: non certifica la verifica delle fonti, "
        "la correttezza semantica o economica, ne' il calcolo Excel; la sanity resta separata.")
BRIDGE_NOTE = ("Controfattuale: driver precedenti ricalcolati con ancore correnti "
               "(spec, ricavi iniziali e NWC iniziale). Ordine dei driver dichiarato e dipendente "
               "dalle interazioni; non e' attribuzione della variazione del FV storico.")


def _finite(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _url(value):
    try:
        parsed = urlparse(value) if isinstance(value, str) else None
        return bool(parsed and parsed.scheme in ("http", "https") and parsed.hostname)
    except ValueError:
        return False


def _path(value):
    return ([round(float(v), 4) for v in value]
            if isinstance(value, list) and len(value) == 5 and all(_finite(v) for v in value) else None)


def _years(value):
    if (isinstance(value, list) and len(value) == 5
            and all(type(v) is int and 1 <= v <= 9999 for v in value)
            and value == list(range(value[0], value[0] + 5))):
        return list(value)
    return None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _portable(value):
    """Keep malformed nonfinite input visible without writing invalid JSON tokens."""
    if isinstance(value, float) and not isfinite(value):
        return "n.d. (input non finito: %s)" % value
    if isinstance(value, dict):
        return {k: _portable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_portable(v) for v in value]
    return value


def _evidence_issues(record, as_of):
    issues = []
    if record.get("kind") not in ("company_guidance", "historical", "analyst_estimate", "proxy"):
        issues.append("kind assente o non riconosciuto")
    elif record.get("kind") == "proxy":
        issues.append("PROXY dichiarato: assunzione da validare con dato societario")
    for key in ("metric", "basis", "rationale"):
        if not _text(record.get(key)):
            issues.append(key + " assente")
    if not _url(record.get("source")):
        issues.append("source: URL assente o non valida")
    source_date, expiry = _date(record.get("source_date")), _date(record.get("valid_until"))
    if source_date is None or as_of is None or source_date > as_of:
        issues.append("source_date assente, non ISO o successiva ad as_of")
    if expiry is None or as_of is None or expiry < as_of:
        issues.append("valid_until assente/non ISO o fonte STALE")
    if source_date and expiry and expiry < source_date:
        issues.append("valid_until precedente a source_date")
    return issues


def _revision_rows(value, as_of, years, material_drivers=MATERIAL_DRIVERS):
    rows = []
    if not isinstance(value, list):
        return [{"input": deepcopy(value), "issues": ["revisions deve essere una lista"]}]
    for item in value:
        row = deepcopy(item) if isinstance(item, dict) else {"input": deepcopy(item)}
        issues = []
        for key in ("metric", "period", "basis", "unit", "rationale"):
            if not _text(row.get(key)):
                issues.append(key + " assente")
        if row.get("period") not in ["FY%s" % year for year in (years or [])]:
            issues.append("period fuori dall'orizzonte FY effettivo: effetto FV n.d.")
        for key in ("previous_value", "current_value"):
            if not _finite(row.get(key)):
                issues.append(key + " non numerico finito")
        if row.get("value_type") not in ("point", "minimum", "maximum"):
            issues.append("value_type assente: distinguere punto, minimo e massimo")
        for key in ("previous_source", "source"):
            if not _url(row.get(key)):
                issues.append(key + ": URL assente o non valida")
        before, after = _date(row.get("previous_date")), _date(row.get("source_date"))
        if not (before and after and as_of and before < after <= as_of):
            issues.append("date revisione: serve previous_date < source_date <= as_of")
        drivers = row.get("drivers")
        if not isinstance(drivers, list) or not drivers:
            issues.append("mappatura driver assente: effetto FV n.d.")
        elif any(not isinstance(d, str) or d not in material_drivers for d in drivers):
            issues.append("mappatura contiene driver sconosciuti: effetto FV n.d.")
        row["issues"] = issues
        rows.append(row)
    return rows


def _bridge(spec, current, previous, revisions, last_rev, nwc0, compute_fv):
    nd = {"status": "n.d.", "currency": current.get("currency"), "steps": [], "note": BRIDGE_NOTE}

    def unavailable(reason):
        return dict(nd, reason=reason)

    if not isinstance(previous, dict):
        return unavailable("snapshot precedente assente o malformato")
    for key in ("ticker", "currency", "forecast_years"):
        if not current.get(key) or previous.get(key) != current[key]:
            return unavailable("snapshot non confrontabili: " + key)
    if type(previous.get("version")) is not int or previous["version"] != 1:
        return unavailable("versione snapshot precedente non supportata")
    before, after = _date(previous.get("as_of")), _date(current.get("as_of"))
    if not (before and after and before <= after):
        return unavailable("date snapshot assenti o non confrontabili")
    old, new = {}, current["scenarios"]
    for scenario in SCENARIOS:
        source = _mapping(_mapping(previous.get("scenarios")).get(scenario))
        old[scenario] = {d: _path(source.get(d)) for d in MATERIAL_DRIVERS}
        if any(old[scenario][d] is None or _path(new[scenario].get(d)) is None for d in MATERIAL_DRIVERS):
            return unavailable("snapshot senza percorsi numerici completi: " + scenario)
    changed = [d for d in MATERIAL_DRIVERS if any(old[s][d] != new[s][d] for s in SCENARIOS)]
    evidence = {}
    for driver in changed:
        evidence[driver] = [deepcopy(r) for r in revisions if not r["issues"] and driver in r["drivers"]
                            and _date(r["previous_date"]) <= before < _date(r["source_date"])]
        if not evidence[driver]:
            return unavailable("nuova evidenza applicabile assente per " + driver)
    if not _finite(last_rev) or not _finite(nwc0):
        return unavailable("ancore correnti ricavi/NWC non numeriche")

    def value(paths):
        result = compute_fv(deepcopy(spec), deepcopy(paths), last_rev, nwc0=nwc0)
        fv = result.get("fair_value_weighted") if isinstance(result, dict) else None
        if not _finite(fv):
            raise ValueError("fair_value_weighted non numerico finito")
        return float(fv)

    try:
        running = deepcopy(old)
        start = previous_fv = value(running)
        steps = []
        for driver in changed:
            for scenario in SCENARIOS:
                running[scenario][driver] = list(new[scenario][driver])
            current_fv = value(running)
            steps.append({"driver": driver, "previous": previous_fv, "current": current_fv,
                          "delta": current_fv - previous_fv, "evidence": evidence[driver]})
            previous_fv = current_fv
        end = value(new)
        residual = end - start - sum(step["delta"] for step in steps)
        if not all(_finite(v) for v in (end - start, residual)):
            raise ValueError("delta o residuo non finito")
        return {"status": "CALCOLATO", "previous_rebased": start, "current": end,
                "delta": end - start, "steps": steps, "residual": residual,
                "currency": current["currency"], "order": list(MATERIAL_DRIVERS), "note": BRIDGE_NOTE}
    except Exception as exc:
        return unavailable("calcolo controfattuale non disponibile: " + type(exc).__name__ + ": " + str(exc))


def assess_quality(spec, raw_scenarios, scenarios, analysis_context=None, previous_snapshot=None,
                   *, last_rev, nwc0, compute_fv, today=None):
    """Return documentary gaps, resolved snapshot and an optional measured counterfactual.

    All objects are treated as caller-owned. Optional source_locator/guidance_id
    and any supplied provenance remain in each row's evidence, without validation claims.
    """
    spec, resolved, context = _mapping(spec), _mapping(scenarios), _mapping(analysis_context)
    today = today or date.today()
    today = _date(today) if isinstance(today, str) else today
    issues, rows, index = [], [], {}
    as_of = _date(context.get("as_of"))
    if as_of is None or not isinstance(today, date) or as_of > today:
        issues.append("as_of assente/non ISO o successiva a oggi")
        as_of = None
    years = _years(context.get("forecast_years"))
    if years is None:
        issues.append("forecast_years: servono cinque anni espliciti consecutivi")
    elif "_forecast_years" in spec and years != _years(spec["_forecast_years"]):
        issues.append("forecast_years diversi dagli anni effettivi del workbook")
        years = None
    if not _text(spec.get("ticker")) or not _text(spec.get("currency")):
        issues.append("identita' ticker/valuta del modello assente")
    records = context.get("assumptions", [])
    if not isinstance(records, list):
        issues.append("assumptions deve essere una lista")
        records = []
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            issues.append("assumption %s malformata: %r" % (i, record))
            continue
        scenario, driver = record.get("scenario"), record.get("driver")
        allowed = MODEL_DRIVERS if scenario == "model" else MATERIAL_DRIVERS
        if not isinstance(scenario, str) or scenario not in (*SCENARIOS, "model") or driver not in allowed:
            issues.append("assumption %s: scenario/driver sconosciuto" % i)
            continue
        index.setdefault((scenario, driver), []).append(record)

    def row_for(scenario, driver, values, row_issues=None):
        found = index.get((scenario, driver), [])
        row_issues = list(row_issues or [])
        if len(found) != 1:
            row_issues.append("serve una sola evidenza per scenario/driver (trovate %s)" % len(found))
        record = deepcopy(found[0]) if len(found) == 1 else {}
        row_issues.extend(_evidence_issues(record, as_of))
        provenance = _mapping(_mapping(resolved.get(scenario)).get("_driver_fonti")).get(driver)
        row = {"scenario": scenario, "driver": driver, "values": deepcopy(values),
               "evidence": record, "provenance": provenance, "issues": row_issues}
        if len(found) > 1:
            row["supplied_evidence"] = deepcopy(found)
        rows.append(row)
        issues.extend(scenario + "/" + driver + ": " + issue for issue in row_issues)

    snapshot_scenarios = {}
    for scenario in SCENARIOS:
        raw = _mapping(_mapping(raw_scenarios).get(scenario))
        current = _mapping(resolved.get(scenario))
        snapshot_scenarios[scenario] = {}
        if not _text(_mapping(context.get("scenario_rationale")).get(scenario)):
            issues.append(scenario + ": razionale dello scenario assente")
        for driver in MATERIAL_DRIVERS:
            explicit, actual = _path(raw.get(driver)), _path(current.get(driver))
            row_issues = []
            if explicit is None:
                row_issues.append("path esplicito di cinque numeri finiti assente (scalari/estensioni automatiche esclusi)")
            if actual is None:
                row_issues.append("path risolto non valido")
            elif explicit is not None and explicit != actual:
                row_issues.append("path fornito diverso dal path effettivamente risolto")
            snapshot_scenarios[scenario][driver] = actual
            row_for(scenario, driver, actual, row_issues)
    wi = _mapping(spec.get("wacc_inputs"))
    # Match explicit engine precedence, but do not manufacture missing default anchors.
    capm, spec_wacc = wi.get("wacc"), spec.get("wacc")
    used_wacc = capm if _finite(capm) and capm else spec_wacc if _finite(spec_wacc) and spec_wacc else None
    anchors = {"last_revenue": last_rev, "nwc0": nwc0, "wacc": used_wacc,
               "terminal_g": spec.get("terminal_g"),
               "shares_m": spec.get("diluted_shares_m") or spec.get("shares_m"),
               "net_debt": spec.get("net_debt")}
    if _finite(anchors["terminal_g"]) and _finite(wi.get("rf")) and wi["rf"]:
        anchors["terminal_g"] = min(anchors["terminal_g"], wi["rf"])
    else:
        anchors["terminal_g"] = None  # missing real g/cap would invoke an engine default
    ra = _mapping(spec.get("ronic_anchor"))
    anchors["ronic"] = ra.get("value")
    actual_controls = "_valuation_controls" in spec
    if actual_controls:
        supplied = _mapping(spec["_valuation_controls"])
        model_controls = {key: deepcopy(supplied.get(key)) for key in CONTROL_KEYS}
        for driver in ("wacc", "terminal_g", "ronic"):
            anchors[driver] = model_controls[driver + "_used"]
        method = model_controls.get("terminal_method")
        if not _text(method) or re.search(r"\bKO\b|fallback", method, re.IGNORECASE):
            issues.append("terminal_method assente o fallback KO: qualita' terminale incompleta")
        if model_controls.get("terminal_warnings"):
            issues.append("terminal_warnings del motore: " + str(model_controls["terminal_warnings"]))
        if type(model_controls.get("mid_year")) is not bool:
            issues.append("mid_year: convenzione temporale effettiva non disponibile")
        model_controls["origin"] = "output del motore (controlli effettivi)"
    else:
        model_controls = {driver + "_used": anchors[driver] for driver in ("wacc", "terminal_g", "ronic")}
        model_controls.update(mid_year=bool(spec.get("mid_year", True)),
                              origin="spec: controlli effettivi del motore non forniti",
                              wacc_delta_bp_used=deepcopy(spec.get("wacc_delta_bp")),
                              equity_adjustments_used=deepcopy(spec.get("equity_adjustments")),
                              method_weights_used=deepcopy(spec.get("method_weights")))
    model_controls["note"] = ("mid_year e' una convenzione temporale esposta, non attestata dalla motivazione. "
                              "Controlli aggiuntivi non coperti dalle prove sono dichiarati incompleti; fonti non verificate.")
    deltas = model_controls.get("wacc_delta_bp_used")
    if deltas and (not isinstance(deltas, dict) or any(not _finite(v) or v != 0 for v in deltas.values())):
        issues.append("wacc_delta_bp_used: prove dei tassi di scenario non coperte dal contratto")
    if model_controls.get("equity_adjustments_used") or model_controls.get("equity_adjustments_total"):
        issues.append("equity_adjustments_used: prove delle rettifiche equity non coperte dal contratto")
    weights = model_controls.get("method_weights_used")
    if weights and (not isinstance(weights, dict) or set(weights) != {"dcf", "comps"}
                    or not all(_finite(v) for v in weights.values()) or weights != {"dcf": 1, "comps": 0}):
        issues.append("method_weights_used: prove della ponderazione metodi non coperte dal contratto")
    for driver, value in anchors.items():
        row_issues = [] if _finite(value) else ["ancora corrente non numerica finita"]
        if driver == "ronic" and (not _finite(ra.get("value")) or not _text(ra.get("source"))
                                   or re.search(r"proxy|n\.d\.", str(ra.get("source")), re.IGNORECASE)):
            row_issues.append("RONIC: ancora/fonte assente o PROXY, numero effettivo non sufficiente")
        row_for("model", driver, value, row_issues)
    revisions = _revision_rows(context.get("revisions", []), as_of, years)
    issues.extend("revisione %s: %s" % (i, issue) for i, r in enumerate(revisions) for issue in r["issues"])
    snapshot = {"version": 1, "ticker": spec.get("ticker"), "currency": spec.get("currency"),
                "as_of": as_of.isoformat() if as_of else None, "forecast_years": years,
                "scenarios": snapshot_scenarios}
    if any(not _finite(value) for value in anchors.values()):
        bridge = {"status": "n.d.", "reason": "ancore correnti incomplete: nessun default implicito nel confronto",
                  "currency": snapshot["currency"], "steps": [], "note": BRIDGE_NOTE}
    else:
        bridge = _bridge(spec, snapshot, previous_snapshot, revisions, last_rev, nwc0, compute_fv)
    automatic = raw_scenarios is None or raw_scenarios == {}
    status = "BOZZA_AUTOMATICA" if automatic else "INCOMPLETA" if issues else "DOCUMENTATA"
    return _portable({"status": status, "note": NOTE, "issues": issues, "rows": rows,
                      "scenario_rationale": deepcopy(_mapping(context.get("scenario_rationale"))),
                      "model_controls": model_controls, "revision_rows": revisions,
                      "snapshot": snapshot, "revision_bridge": bridge})


_DECISION_IDENTITY = ("contract_version", "registry_version", "profile_id", "profile_version",
                      "method_id", "method_version", "instrument", "sector", "subsector",
                      "economic_features", "segments", "input_fingerprint", "evidence_ids",
                      "evidence", "as_of", "decision_status", "support_status")
_PRIMARY_FAIR_VALUES = ("fair_value_final", "fair_value_weighted", "fair_value_blend",
                        "fair_value_nav", "fair_value_base", "fair_value")
_DERIVED_VALUATION_KEYS = frozenset({"fv", "fv_ps", "upside", "downside", "target_price", "price_target",
                                  "nav_target", "enterprise_value", "equity_value", "ev", "equity",
                                  "mnav_equity", "mnav_ev", "sotp_ev_total", "irr",
                                  "raw_equity_value", "owned_equity_value"})
_SOURCE_BRANCHES = frozenset({"case", "analysis_context", "valuation_decision", "evidence",
                              "acquisition_tasks", "input_consumption"})
_CALCULATED_VALUATION_KEYS = frozenset({'common_equity_nav','nav_per_share',
    'residual_income_value','cash_equity_value','terminal_common_equity_value',
    'value_contribution','asset_value','gross_assets','terminal_equity','terminal_value',
    'tax_shield_pv','central_cost_pv'})


def _calculated_valuation_key(key, path):
    # Observed NAV and book/cash ledgers remain evidence, even when FV is blocked.
    return '.calculation_details' in path and (key in _CALCULATED_VALUATION_KEYS or
        key == 'equity_adjustments' and path.endswith('.nav.components'))


def _valuation_number_key(key):
    return isinstance(key, str) and (key.startswith(("fair_value", "upside_", "downside_", "irr_"))
                                    or key in _DERIVED_VALUATION_KEYS)


def _decision_usability_issues(decision, cutoff):
    """Validate S1 identity using its existing selector, without resolving again or I/O."""
    from bellomberg.valuation.method_registry import (
        CONTRACT_VERSION, REGISTRY_VERSION, get_method_requirements, select_valuation_method,
    )
    from bellomberg.valuation.valuation_profile import PROFILE_VERSION

    reasons, missing = [], []
    if not decision:
        return ["valuation_decision assente: risultato legacy non utilizzabile"], ["valuation_decision"], []
    for key, version in (("contract_version", CONTRACT_VERSION), ("registry_version", REGISTRY_VERSION),
                         ("profile_version", PROFILE_VERSION)):
        if decision.get(key) != version:
            reasons.append(key + ": versione assente o non corrente")
    selected = select_valuation_method(decision)
    for key in ("profile_id", "method_id", "method_version", "support_status", "legacy_route", "adapter"):
        if decision.get(key) != selected.get(key):
            reasons.append(key + ": non corrisponde al catalogo corrente")
    if decision.get("decision_status") != "resolved" or selected.get("decision_status") != "resolved":
        reasons.append("profilo economico non risolto")
    if decision.get("support_status") != "integrated":
        reasons.append("metodo non integrato")
    if decision.get("requirements_status") != "complete":
        reasons.append("requisiti di acquisizione non completi")
    if decision.get("missing_fields") != []:
        reasons.append("requisiti mancanti o dichiarazione missing_fields assente/malformata")
        if isinstance(decision.get("missing_fields"), list):
            missing.extend(k for k in decision["missing_fields"] if isinstance(k, str))
    as_of = _date(decision.get("as_of"))
    if as_of is None or cutoff is None or as_of != cutoff:
        reasons.append("as_of: cutoff assente, non valido o diverso da quello richiesto")
    evidence = decision.get("evidence")
    valid_evidence = isinstance(evidence, list) and bool(evidence)
    if not valid_evidence:
        reasons.append("evidenze del profilo assenti")
        missing.append("evidence")
    else:
        for record in evidence:
            if not isinstance(record, dict):
                reasons.append("evidenza del profilo malformata")
                continue
            observed, expires = _date(record.get("as_of")), _date(record.get("valid_until"))
            if (not _text(record.get("field")) or not _text(record.get("source_id"))
                    or record.get("value") is None or observed is None or as_of is None or observed > as_of
                    or record.get("status", "ok") != "ok"
                    or (record.get("valid_until") is not None and (expires is None or expires < as_of
                                                                                or expires < observed))):
                reasons.append("evidenza del profilo mancante, STALE o non valida")
        if decision.get("evidence_ids") != sorted({r.get("source_id") for r in evidence
                                                  if isinstance(r, dict) and _text(r.get("source_id"))}):
            reasons.append("evidence_ids non corrisponde alle evidenze")
    fingerprint = decision.get("input_fingerprint")
    issues = decision.get("issues")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        reasons.append("input_fingerprint assente o non valido")
    elif valid_evidence and isinstance(issues, list):
        try:
            canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=True,
                                                 separators=(",", ":"), allow_nan=False)
            # This is the S1 fingerprint contract, including explicit registry absence.
            ordered = sorted({canonical(r): r for r in evidence}.values(), key=canonical)
            digest = hashlib.sha256(canonical({
                "evidence": ordered, "as_of": decision.get("as_of"), "profile_version": PROFILE_VERSION,
                "status": decision.get("decision_status"),
                "issues": sorted({(i["code"], i.get("field") or "") for i in issues}),
                "registry_status": ("assente" if any(i["code"] == "registry_absent" for i in issues)
                                    else "illeggibile" if any(i["code"] == "registry_error" for i in issues)
                                    else "present"),
            }).encode()).hexdigest()
            if fingerprint != digest:
                reasons.append("input_fingerprint non corrisponde alle evidenze/cutoff")
        except (KeyError, TypeError, ValueError, OverflowError):
            reasons.append("evidenze/fingerprint non serializzabili secondo il contratto")
    try:
        required = [r["field"] for r in get_method_requirements(decision.get("method_id"))["fields"]
                    if r["required"]]
    except ValueError:
        required = []
        reasons.append("method_id assente o non registrato")
    return reasons, missing, required


def assess_valuation_usability(payload, *, expected_decision=None, as_of=None):
    """Fail closed at cache/consumer boundaries; numeric, documented and usable differ.

    S1 identity is retained. Acquisition completeness does not attest consumption:
    the adapter must name the method inputs it consumed. Quality/sanity reports
    belong to that method, never implicitly to operating. This validates supplied
    reports and metadata, not the authenticity of sources or calculator correctness.
    Legacy objects remain readable but cannot supply a usable fair value.
    """
    payload = _mapping(payload)
    decision = _mapping(payload.get("valuation_decision"))
    cutoff = date.today() if as_of is None else _date(as_of) if isinstance(as_of, str) else as_of
    cutoff = cutoff if type(cutoff) is date else None
    reasons, missing, required = _decision_usability_issues(decision, cutoff)
    method = decision.get("method_id")
    for key in ("snapshot_id", "generation_id"):
        if not _text(payload.get(key)):
            reasons.append(key + " assente: provenienza della generazione non verificabile")
            missing.append(key)
    if expected_decision is not None:
        if not isinstance(expected_decision, dict):
            reasons.append("expected_decision malformata")
        else:
            for key in _DECISION_IDENTITY:
                if decision.get(key) != expected_decision.get(key):
                    reasons.append("cache/decisione corrente diversa: " + key)
            for key in ("snapshot_id", "generation_id", "ticker"):
                if key in expected_decision and payload.get(key) != expected_decision[key]:
                    reasons.append("cache/sidecar di generazione diversa: " + key)
            if ("acquisition_fingerprint" in expected_decision
                    and decision.get("acquisition_fingerprint") != expected_decision["acquisition_fingerprint"]):
                reasons.append("cache/acquisizione corrente diversa: acquisition_fingerprint")

    def consumption_checks(report, path):
        consumption = _mapping(report)
        consumed = consumption.get("consumed_fields")
        if consumption.get("status") != "complete" or consumption.get("unconsumed_fields") != []:
            reasons.append(path + " incompleto: input forniti non consumati o consumo non attestato")
        if not isinstance(consumed, list) or any(not _text(field) for field in consumed):
            consumed = []
            reasons.append(path + ".consumed_fields assente o malformato")
        unconsumed = [field for field in required if field not in consumed]
        if unconsumed:
            reasons.append(path + ": requisiti del metodo non consumati: " + ", ".join(unconsumed))
            missing.extend(unconsumed)

    consumption_checks(payload.get("input_consumption"), "input_consumption")
    from bellomberg.valuation.method_registry import is_record_method
    if is_record_method(decision):
        # The first integrated record adapter must attest actual records, not
        # merely repeat a list of requirement names supplied by a caller.
        from bellomberg.valuation.sector_analysis import validate_bundle
        try:
            bundle = validate_bundle(payload.get("acquisition_snapshot"), payload.get("ticker"))
            if bundle["snapshot_id"] != payload.get("snapshot_id") or bundle["decision"] != decision:
                raise ValueError("snapshot/decisione diversi dai dati acquisiti")
            records = bundle["case"]["records"]
            bindings = _mapping(payload.get("input_consumption")).get("consumed_records")
            if not isinstance(bindings, list) or not records or len(bindings) != len(records):
                raise ValueError("consumed_records: attestazione per record assente/incompleta")
            for index, (binding, record) in enumerate(zip(bindings, records)):
                if (not isinstance(binding, dict) or binding.get("record_index") != index
                        or any(binding.get(key) != record.get(key) for key in
                               ("field", "scenario", "driver", "entity", "period", "source_id"))):
                    raise ValueError("consumed_records: identita diversa dai record acquisiti")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            reasons.append("input_consumption: " + str(exc))

    def quality_checks(report, path):
        report = _mapping(report)
        if report.get("status") != "DOCUMENTATA" or report.get("issues") != []:
            reasons.append(path + ": documentazione incompleta o non attestata")
        for key in ("rows", "revision_rows"):
            if key in report and (not isinstance(report[key], list)
                                  or any(not isinstance(row, dict) or row.get("issues") != []
                                         for row in report[key])):
                reasons.append(path + "." + key + ": buchi documentali nelle evidenze")
        if not method or report.get("method_id") != method:
            reasons.append(path + ": controlli non attribuiti al metodo selezionato")

    def sanity_checks(report, path):
        report = _mapping(report)
        if report.get("severity") not in ("OK", "WARN"):
            reasons.append(path + ": sanity non disponibile o bloccante")
        if (report.get("status", "ok") not in ("ok", "OK")
                or report.get("exclude_from_action_table") is True):
            reasons.append(path + ": controllo non disponibile o risultato escluso")
        if not method or report.get("method_id") != method:
            reasons.append(path + ": sanity non attribuita al metodo selezionato")

    quality_checks(payload.get("analytical_quality"), "analytical_quality")
    sanity_checks(payload.get("sanity"), "sanity")
    primary = next((payload[key] for key in _PRIMARY_FAIR_VALUES if payload.get(key) is not None), None)
    if not _finite(primary):
        reasons.append("fair_value primario assente o non numerico finito")
        missing.append("fair_value")

    child_values = {}
    child_input_values = None
    if method == 'mixed_business_sotp':
        try:
            child_rows = [r for r in payload['acquisition_snapshot']['case']['records']
                          if r.get('driver') == 'children' and r.get('scenario') == 'model']
            if len(child_rows) != 1:
                raise ValueError('Record children univoco richiesto')
            child_input_values = child_rows[0]['value']
            child_values = payload.get('child_valuations')
            if not isinstance(child_values, dict) or set(child_values) != set(child_input_values):
                raise ValueError('Valutazioni child complete richieste')
            for name, child in child_values.items():
                expected = child_input_values[name]
                if (not isinstance(child, dict) or child.get('method') == 'mixed_business_sotp'
                        or child.get('acquisition_snapshot') != expected):
                    raise ValueError(str(name)+': snapshot child diverso o SOTP ricorsiva')
                check = assess_valuation_usability(child, expected_decision=expected['decision'], as_of=as_of)
                if not check['usable']:
                    raise ValueError(str(name)+': child non utilizzabile; '+'; '.join(check['reasons']))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            reasons.append('child_valuations: '+str(exc))
            child_values = {}

    def inspect(value, path="payload"):
        if isinstance(value, list):
            for index, child in enumerate(value):
                inspect(child, path + "[%s]" % index)
        elif isinstance(value, dict):
            if method == 'mixed_business_sotp' and path == 'payload.child_valuations':
                return  # Independently gated above against the actual acquired child bundles.
            if (method == 'mixed_business_sotp' and path.startswith('payload.analytical_quality.rows[')
                    and value.get('driver') == 'children'):
                if value.get('values') != child_input_values:
                    reasons.append(path+': evidenza children diversa dallo snapshot')
                return  # Original source bundles, not parent generation attestations.
            segment_child = None
            if (method == 'mixed_business_sotp' and path.startswith('payload.calculation_details.scenarios.')
                    and '.segments[' in path and path.endswith(']')):
                segment_child = child_values.get(value.get('segment'))
                if not isinstance(segment_child, dict):
                    reasons.append(path+': segmento senza valutazione child verificata')
            def valuation_key(key):
                return _valuation_number_key(key) or _calculated_valuation_key(key, path)
            has_value = any(valuation_key(key) and child is not None for key, child in value.items())
            if value.get("exclude_from_action_table") is True or value.get("valuation_flagged") is True:
                reasons.append(path + ": risultato esplicitamente escluso dalla valutazione utilizzabile")
            if value.get("usable") is False:
                reasons.append(path + ": risultato dichiarato non utilizzabile")
                if isinstance(value.get("reasons"), list):
                    reasons.extend(reason for reason in value["reasons"] if _text(reason))
                if isinstance(value.get("missing_fields"), list):
                    missing.extend(field for field in value["missing_fields"] if _text(field))
            if has_value and (value.get("status") in ("BLOCK", "KO", "error", "blocked", "INCOMPLETA")
                              or value.get("flagged") is True):
                reasons.append(path + ": risultato annidato bloccato")
            for key, child in value.items():
                child_path = path + "." + str(key)
                if key in ("snapshot_id", "generation_id") and child != (segment_child or payload).get(key):
                    reasons.append(child_path + ": provenienza diversa dalla generazione corrente")
                if key == "valuation_decision" and path != "payload" and child != decision:
                    reasons.append(child_path + ": decisione annidata differente")
                if key == "analytical_quality" and path != "payload":
                    quality_checks(child, child_path)
                if key == "sanity" and path != "payload":
                    sanity_checks(child, child_path)
                if key == "input_consumption" and path != "payload":
                    consumption_checks(child, child_path)
                if key == "by_scenario" and path.endswith(".holding_irr") and isinstance(child, dict):
                    if any(number is not None and not _finite(number) for number in child.values()):
                        reasons.append(child_path + ": IRR non numerico finito")
                if valuation_key(key) and child is not None and not _finite(child):
                    reasons.append(child_path + ": risultato numerico non finito o malformato")
                # Acquired cases retain original S1 routing and raw observations;
                # these are provenance, not a competing result/attestation.
                if key not in _SOURCE_BRANCHES:
                    inspect(child, child_path)

    inspect(payload)
    snapshot = _mapping(_mapping(payload.get("analytical_quality")).get("snapshot"))
    if "as_of" in snapshot and snapshot["as_of"] != decision.get("as_of"):
        reasons.append("analytical_quality.snapshot.as_of diverso dal cutoff della decisione")
    return {"usable": not reasons, "reasons": list(dict.fromkeys(reasons)),
            "missing_fields": list(dict.fromkeys(missing))}


def normalize_valuation_payload(payload, *, expected_decision=None, as_of=None):
    """Preserve evidence/observed price/NAV; hide unusable valuation outputs recursively."""
    result = deepcopy(_mapping(payload))
    usability = assess_valuation_usability(result, expected_decision=expected_decision, as_of=as_of)
    if not usability["usable"]:
        def hide(value, *, irr=False, path='payload'):
            if isinstance(value, dict):
                cleaned = {}
                for key, child in value.items():
                    if key in _SOURCE_BRANCHES:
                        cleaned[key] = deepcopy(child)
                    elif _valuation_number_key(key) or _calculated_valuation_key(key, path):
                        cleaned[key] = None
                    elif key == "holding_irr":
                        cleaned[key] = hide(child, irr=True,path=path+'.'+str(key)) if isinstance(child, dict) else None
                    elif irr and key not in ("years", "entry_price", "entry_pb"):
                        cleaned[key] = hide(child, irr=True,path=path+'.'+str(key)) if isinstance(child, (dict, list)) else (
                            None if isinstance(child, (int, float)) else child)
                    else:
                        cleaned[key] = hide(child,path=path+'.'+str(key))
                return cleaned
            if isinstance(value, list):
                return [hide(child, irr=irr,path=path+'[%s]'%i) for i,child in enumerate(value)]
            return None if irr and isinstance(value, (int, float)) else value
        result = hide(result)
    result["valuation_usability"] = usability
    return result
