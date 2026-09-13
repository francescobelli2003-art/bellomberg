"""Acquire a dated economic case once, independently of portfolio membership.

Providers accept ``ticker, as_of=...`` and return an envelope with status, data
and optional method-input records. Raw provider data never certifies the
registry's reconciliations or an analyst's forecast. Missing adapters stay
visible as acquisition tasks; no second resolver or numerical defaults live here.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import math

from bellomberg.valuation.method_registry import get_method_requirements

PROVIDERS = ("profile", "financials", "filings", "guidance", "consensus", "method_inputs")
STATUSES = {"ok", "data_missing", "credentials_missing", "source_error", "stale", "unavailable"}


def method_records_schema():
    """Shared public tool argument; evidence is validated, never auto-certified."""
    from .managed_care import ANNUAL_INPUTS
    from .distributable_equity import CAPITAL_FIELDS, SUB_FIELDS, CASH_SIGNS
    from .managed_care_adapter import _descriptor
    drivers = (list(ANNUAL_INPUTS) + ['actuals.' + key for key in ('premium', 'medical_costs', 'gna', 'net_income')]
        + ['segments.SEGMENT.premium', 'segments.SEGMENT.mcr', 'adjustments_after_tax.ADJUSTMENT']
        + ['capital.' + key for key in sorted(CAPITAL_FIELDS - {'subsidiaries', 'parent_cash_flows'})]
        + ['capital.parent_cash_flows.' + key for key in CASH_SIGNS]
        + ['capital.subsidiaries.0.' + key for key in sorted(SUB_FIELDS - {'id'})])
    mapping = {driver: _descriptor(driver) for driver in drivers}
    from .operating_adapter import SCHEMA as operating_schema, REVENUE_BASES
    from .bank_adapter import SCHEMA as bank_schema
    from .rab_adapter import SCHEMA as rab_schema
    from .nav_adapter import FUND as fund_schema,DIGITAL as digital_schema
    from .insurance_adapter import SCHEMA as insurance_schema,PC_SCHEMA,LIFE_SCHEMA
    from .real_estate_adapter import SCHEMA as property_schema
    from .development_adapter import SCHEMA as development_schema
    from .sotp_adapter import SCHEMA as sotp_schema
    from .resources_adapter import SCHEMA as resources_schema
    from .property_development_adapter import SCHEMA as property_development_schema
    return {"type": "array", "description": "Record documentati del metodo selezionato. Ogni nuovo set sostituisce il precedente set esplicito dell'analista, conservandolo come storia; i record di provider indipendenti restano e i conflitti sono KO. Managed care: model per perimeter/calendar/quotation/actuals, bear/base/bull per tutti gli altri driver. Riusa filing/guidance gia acquisiti; nessun forecast implicito da dati storici. Ogni scalare/path richiede un record. Schema e mapping: docs/managed-care-inputs.md. Record assenti o non consumati mantengono FV n.d.",
            "items": {"type": "object", "properties": {
                **{k: {"type": "string"} for k in ("field", "driver", "entity", "period", "unit", "accounting_basis", "source_id", "as_of", "valid_until", "rationale", "source_locator")},
                "driver": {"type": "string", "description": "Managed care driver -> [field, unit, accounting_basis, timing]; SEGMENT/ADJUSTMENT sono nomi dichiarati, indice subsidiary segue perimeter. money significa '<currency> million'. " + json.dumps(mapping)
                    + " Operating FCFF driver -> [field, unit, basis, timing, shape, scope]: " + json.dumps(operating_schema)
                    + " Bank/credit driver schema: " + json.dumps(bank_schema)
                    + " RAB driver schema: " + json.dumps(rab_schema)
                    + " Fund NAV snapshot schema: " + json.dumps(fund_schema)
                    + " Digital-asset NAV snapshot schema: " + json.dumps(digital_schema)
                    + " Development schema: " + json.dumps(development_schema)
                    + " SOTP schema: " + json.dumps(sotp_schema)
                    + " Resources schema: " + json.dumps(resources_schema)
                    + " Property development schema: " + json.dumps(property_development_schema)
                    + " Property NAV schema: " + json.dumps(property_schema)
                    + " Insurance common schema: " + json.dumps(insurance_schema)
                    + " Life entity drivers insurance.<legal index>.<driver>: " + json.dumps(LIFE_SCHEMA)
                    + " PC entity drivers insurance.<legal index>.<driver>: " + json.dumps(PC_SCHEMA)
                    + " Bank legal capital drivers: capital.<CAPITAL_FIELDS>, capital.parent_cash_flows.<CASH_SIGNS>, capital.subsidiaries.<index>.<SUB_FIELDS except id>; exact contracts in docs/guide/sector-valuations.md. No nested field may be omitted."
                    + " FCFF richiede anni interi, schema/perimetro/calendario diversi dal managed care: docs/guide/sector-valuations.md. Revenue_build basi per profilo: " + json.dumps(REVENUE_BASES)},
                "period": {"type": "string", "description": "annual = intervalli start/end dei fiscal_periods uniti da |; future = stesso elenco con primo start=valuation_date+1 giorno; opening=valuation_date; terminal=ultimo end; actuals=actuals_start/valuation_date. Entita: consolidato per driver economici/ke/shares/discount; parent per parent_ledger; ID legale per subsidiaries."},
                "scenario": {"type": "string", "enum": ["model", "bear", "base", "bull"]},
                "kind": {"type": "string", "enum": ["historical", "company_guidance", "analyst_estimate", "proxy"]},
                "value": {"description": "Valore consumato, non solo prova. Path annuali/futuri: array numerico lungo years; opening/terminal/ke/shares scalari; policy/basi stringhe. Strutture model: perimeter [field=valuation_perimeter,unit=contract,basis=scope,period=annual] con currency ISO, consolidated_entity,parent_entity,subsidiaries=[{id,regime}],share_class,share_basis. calendar [forecast_periods,contract,calendar,annual] con years,actuals_year,valuation_date,actuals_start,actuals_kind FY|interim,day_count ACT/365F,fiscal_periods=[{year,start,end,payment_date=end}]. quotation [quotation_units,contract,quotation,opening] con financial_currency,quote_currency,quote_unit,quote_units_per_currency (1;100 per GBX/GBP),financial_to_quote_rate,shares_per_quote,share_class,price,price_as_of=valuation_date. Valuation_date coincide con cutoff actuals e saldi opening. Nessun rollforward implicito."}},
                "required": ["field", "driver", "scenario", "value", "entity", "period", "unit", "accounting_basis", "source_id", "as_of", "valid_until", "kind", "rationale"],
                "additionalProperties": False}}


def _json_value(value):
    if hasattr(value, "as_dict"):
        return _json_value(value.as_dict())
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Dato numerico non finito nel provider")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_dict") and hasattr(value, "columns"):
        cells = value.values.tolist()
        missing = sum(isinstance(v, float) and not math.isfinite(v) for row in cells for v in row)
        cells = [[None if isinstance(v, float) and not math.isfinite(v) else v for v in row] for row in cells]
        return {"table": True, "columns": [str(c) for c in value.columns],
                "index": [str(i) for i in value.index],
                "data": _json_value(cells), "missing_cells": missing}
    raise ValueError("Provider returned a non-serializable value: " + type(value).__name__)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _iso(value):
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except (TypeError, ValueError):
        return None


def _acquire(name, ticker, as_of, providers):
    provider = providers.get(name)
    if provider is None:
        return {"status": "unavailable", "source_id": name,
                "message": "Provider non collegato: " + name, "data": None, "records": []}
    try:
        result = provider(ticker, as_of=as_of)
        if not isinstance(result, dict) or result.get("status") not in STATUSES:
            raise ValueError("Envelope provider privo di status riconosciuto")
        result = _json_value(deepcopy(result))
        if result.get("status") == "ok":
            observed, expiry = _iso(result.get("as_of")), _iso(result.get("valid_until"))
            if not result.get("source_id") or observed is None or observed > _iso(as_of):
                raise ValueError("Fonte/data provider assente o successiva al cutoff")
            if result.get("valid_until") and expiry is None:
                raise ValueError("Scadenza provider non valida")
            if expiry is not None and expiry < _iso(as_of):
                result.update(status="stale", message="Fonte scaduta al cutoff")
        result.setdefault("records", [])
        if result["status"] == "ok" and result.get("data") in (None, {}, []) and not result["records"]:
            result.update(status="data_missing", message="Fonte senza dati o record")
        return result
    except PermissionError as exc:
        return {"status": "credentials_missing", "source_id": name, "data": None,
                "records": [], "message": str(exc)}
    except Exception as exc:
        return {"status": "source_error", "source_id": name, "data": None,
                "records": [], "message": type(exc).__name__ + ": " + str(exc)}


def prepare_sector_analysis(ticker, *, as_of, providers, user_context=None):
    """Build a JSON snapshot. Injected providers never fall through to live I/O.

Record fields follow the S1 registry. User context can carry explicit
``assumptions`` and ``analysis_context``; mandate/holdings/side preferences
are deliberately outside the economic snapshot.
"""
    from bellomberg.valuation.dcf_engine import decidi_percorso
    from bellomberg.valuation.valuation_profile import evidence_from_info

    if not isinstance(ticker, str) or not ticker.strip() or _iso(as_of) is None:
        raise ValueError("Ticker e cutoff ISO espliciti sono obbligatori")
    if not isinstance(providers, dict):
        raise ValueError("providers deve essere una mappa esplicita")
    context = user_context or {}
    if not isinstance(context, dict):
        raise ValueError("user_context deve essere un oggetto")
    ticker = ticker.strip().upper()
    sources = {"profile": _acquire("profile", ticker, as_of, providers)}
    p = sources["profile"]
    pdata = p.get("data") if isinstance(p.get("data"), dict) else {}
    info = pdata.get("info", {})
    evidence = pdata.get("evidence")
    if evidence is None:
        evidence = evidence_from_info(info, as_of=p.get("as_of", as_of))
    registry = pdata.get("vehicle_registry")
    if p["status"] != "ok":
        evidence = [{"field": "instrument", "value": None, "source_id": p.get("source_id", "profile"),
                     "as_of": as_of, "status": "stale" if p["status"] == "stale" else "error"}]
    route = decidi_percorso(ticker, info, negozio=registry, evidence=evidence, as_of=as_of)
    decision = deepcopy(route["valuation_decision"])
    # There is no useful sector acquisition before an economic identity exists.
    if decision["decision_status"] == "resolved":
        sources.update({name: _acquire(name, ticker, as_of, providers) for name in PROVIDERS[1:]})
    if context.get("method_records") is not None:
        if not isinstance(context["method_records"], list):
            raise ValueError("method_records deve essere una lista di record documentati")
        previous = deepcopy(sources.get("method_inputs", {}))
        previous_records = previous.get("records", []) if previous.get("status") == "ok" else []
        if previous.get("source_id") == "explicit_method_records":
            previous_records = previous.get("data", {}).get("provider_records")
            if not isinstance(previous_records, list):
                raise ValueError("Revisione record: provenienza dei dati provider non separata dal set analista")
        sources["method_inputs"] = {"status": "ok", "source_id": "explicit_method_records", "as_of": as_of,
            "data": {"previous_acquisition": previous, "provider_records": deepcopy(previous_records)},
            "message": "Record espliciti dell'analista; fonte e data di ciascun dato da validare, acquisizione precedente conservata",
            "records": _json_value(previous_records + context["method_records"])}
    requirements = get_method_requirements(decision["method_id"]) if decision["method_id"] else {}
    fields = {f["field"] for f in requirements.get("fields", []) if f.get("required")}
    metadata = requirements.get("record_metadata", [])
    records, issues, tasks = [], [], []
    for name, source in sources.items():
        if source["status"] != "ok":
            tasks.append({"field": name, "status": source["status"], "source_id": source.get("source_id"),
                          "reason": source.get("message") or "Dati non disponibili"})
            continue
        rows = source.get("records")
        if not isinstance(rows, list):
            issues.append({"field": name, "code": "invalid_records", "blocking": True,
                           "message": "records deve essere una lista"})
            continue
        for raw in rows:
            record = deepcopy(raw) if isinstance(raw, dict) else {"value": raw}
            errors = []
            field = record.get("field")
            if not isinstance(field, str) or field not in fields:
                errors.append("Campo non previsto dal metodo: input non consumabile")
                field = name
                record["field"] = field
            for key in metadata:
                if not isinstance(record.get(key), str) or not record[key].strip():
                    errors.append(key + " assente o non testuale")
            if record.get("value") in (None, "", [], {}):
                errors.append("value assente")
            observed, expiry = _iso(record.get("as_of")), _iso(record.get("valid_until"))
            if observed is None or observed > _iso(as_of):
                errors.append("as_of non valido o futuro")
            if record.get("valid_until") is not None and (expiry is None or expiry < _iso(as_of)):
                errors.append("Fonte scaduta o scadenza non valida")
            if record.get("status", "ok") != "ok":
                errors.append("Record non corrente: " + str(record.get("status")))
            record["validation_issues"] = errors
            record["provider"] = name
            records.append(record)
            if errors:
                issues.append({"field": field or name, "code": "invalid_input", "blocking": True,
                               "message": "; ".join(errors)})
    valid_fields = {r["field"] for r in records if not r["validation_issues"]}
    # Contradictory duplicate facts are not silently resolved by provider order.
    for field in valid_fields.copy():
        same = [r for r in records if r.get("field") == field and not r["validation_issues"]]
        groups = {}
        for r in same:
            key = tuple(str(r.get(k)) for k in ("entity", "period", "unit", "accounting_basis", "scenario", "driver"))
            groups.setdefault(key, set()).add(_hash(r["value"]))
        if any(len(values) > 1 for values in groups.values()):
            valid_fields.remove(field)
            issues.append({"field": field, "code": "conflicting_input", "blocking": True,
                           "message": "Fonti discordanti sullo stesso campo/perimetro/periodo"})
    missing = sorted(fields - valid_fields)
    if decision["decision_status"] != "resolved":
        missing = decision.get("missing_fields", [])
    for field in missing:
        definition = next((f for f in requirements.get("fields", []) if f["field"] == field), {})
        tasks.append({"field": field, "status": "data_missing", "reason": definition.get("description", "Evidenza da acquisire"),
                      "required_metadata": metadata})
    decision.update(missing_fields=missing,
                    requirements_status="complete" if not missing and not issues and fields else "incomplete")
    decision["issues"].extend(issues)
    decision["profile_fingerprint"] = decision["input_fingerprint"]
    assumptions = _json_value(context.get("assumptions", {}))
    analysis_context = _json_value(context.get("analysis_context", {}))
    case = {"ticker": ticker, "as_of": as_of, "info": _json_value(info),
            "vehicle_registry": _json_value(registry), "sources": sources,
            "records": records, "assumptions": assumptions,
            "route": route["percorso"], "route_reason": route["motivo"],
            "routing": _json_value(route)}
    decision["acquisition_fingerprint"] = _hash({"profile": decision["profile_fingerprint"],
                                           "sources": sources, "assumptions": assumptions,
                                           "analysis_context": analysis_context})
    bundle = {"decision": decision, "case": case, "analysis_context": analysis_context,
              "acquisition_tasks": tasks}
    bundle["snapshot_id"] = _hash(bundle)
    return bundle


def validate_bundle(bundle, ticker):
    if not isinstance(bundle, dict):
        raise ValueError("Bundle settoriale assente")
    content = {k: v for k, v in bundle.items() if k != "snapshot_id"}
    if bundle.get("snapshot_id") != _hash(content):
        raise ValueError("Bundle modificato dopo l'acquisizione")
    if bundle.get("case", {}).get("ticker") != ticker.strip().upper():
        raise ValueError("Bundle di un altro ticker")
    return deepcopy(bundle)


def revise_sector_analysis(bundle, *, assumptions=None, analysis_context=None, method_records=None):
    """Apply an explicit analyst revision to acquired facts, without new I/O."""
    result = validate_bundle(bundle, bundle["case"]["ticker"])
    if assumptions is not None:
        result["case"]["assumptions"] = _json_value(assumptions)
    if analysis_context is not None:
        result["analysis_context"] = _json_value(analysis_context)
    if method_records is not None:
        # Replay only the acquired envelopes through the existing validation;
        # a new record set is a new snapshot, never a mutation of old evidence.
        providers = {name: (lambda ticker, *, as_of, value=source: deepcopy(value))
                     for name, source in result["case"]["sources"].items()}
        return prepare_sector_analysis(result["case"]["ticker"], as_of=result["case"]["as_of"], providers=providers,
            user_context={"assumptions": result["case"]["assumptions"], "analysis_context": result["analysis_context"],
                          "method_records": method_records})
    result["decision"]["acquisition_fingerprint"] = _hash({
        "profile": result["decision"]["profile_fingerprint"], "sources": result["case"]["sources"],
        "assumptions": result["case"]["assumptions"], "analysis_context": result["analysis_context"]})
    result["snapshot_id"] = _hash({k: v for k, v in result.items() if k != "snapshot_id"})
    return result


def sector_analysis_summary(bundle):
    decision = bundle["decision"]
    requirements = get_method_requirements(decision["method_id"]) if decision.get("method_id") else {}
    return ("Metodo: " + str(decision.get("method_id") or "n.d.")
            + " | decisione: " + decision["decision_status"]
            + " | supporto: " + str(decision.get("support_status") or "n.d.")
            + " | requisiti: " + decision["requirements_status"]
            + " | snapshot: " + bundle["snapshot_id"]
            + "\nMotivo: " + str(decision.get("rationale") or bundle["case"]["route_reason"])
            + "\nDa acquisire: " + "; ".join(str(t["field"]) + " (" + t["status"] + "): " + t["reason"]
                                            for t in bundle["acquisition_tasks"])
            + "\nControlli del metodo: " + "; ".join(requirements.get("reconciliations", []))
            + "\nLa disponibilita' di un adapter non certifica il FV. Riporta valuation_usability e i limiti.")


def valuation_results_block(results):
    """One small committee view; no independent selection or invented FV."""
    from bellomberg.valuation.dcf_quality import normalize_valuation_payload
    if not results:
        return ""
    rows = ["=== VALUTAZIONI SETTORIALI [src: get_valuation] ===",
            "Usa solo FV con valuation_usability.usable=true. Target esterni distinti dal FV.",
            "RESEARCH -> BUY/ADD deve citare snapshot e limiti; calcolabile non significa tesi valida."]
    for ticker, payload in sorted(results.items()):
        result = normalize_valuation_payload(payload)
        decision, usability = result.get("valuation_decision") or {}, result["valuation_usability"]
        rows.append(json.dumps({"ticker": ticker, "method_id": decision.get("method_id"),
            "snapshot_id": result.get("snapshot_id"), "generation_id": result.get("generation_id"),
            "valuation_usability": usability, "valuation_date": result.get("valuation_date"),
            "information_cutoff": decision.get("as_of"),
            "valuation_basis": result.get("valuation_basis"), "fair_value": next((result[k] for k in
                ("fair_value_final", "fair_value_weighted", "fair_value_blend", "fair_value_base", "fair_value_nav")
                if result.get(k) is not None), None)}, ensure_ascii=False))
    return "\n".join(rows)


def default_sector_providers(*, fetch_info=None, vehicle_registry=None):
    """Adapters over existing data tools. Construction performs no I/O."""
    cache = {}

    def envelope(data, source, as_of):
        if data is None or data == {}:
            return {"status": "data_missing", "source_id": source, "message": "Fonte senza dati", "data": data}
        if isinstance(data, dict) and data.get("error"):
            return {"status": "source_error", "source_id": source, "message": str(data["error"]), "data": data}
        # A live fetch can record its observation time, never backdate it to a
        # historical request. Publication/period dates remain in the raw tool data.
        return {"status": "ok", "source_id": source, "as_of": date.today().isoformat(), "data": data}

    def profile(ticker, *, as_of):
        from bellomberg.storage import classificazione
        if fetch_info is None:
            import yfinance as yf
            cache["ticker"] = yf.Ticker(ticker)
            info = cache["ticker"].info
        else:
            info = fetch_info(ticker)
        cache["info"] = info
        registry = vehicle_registry if vehicle_registry is not None else classificazione.carica_veicoli()
        return envelope({"info": info, "vehicle_registry": registry}, "provider_info", as_of)

    def financials(ticker, *, as_of):
        import yfinance as yf
        tk = cache.setdefault("ticker", yf.Ticker(ticker))
        values, errors = {}, {}
        for field in ("income_stmt", "balance_sheet", "cashflow"):
            try:
                values[field] = _json_value(getattr(tk, field))
            except Exception as exc:
                errors[field] = type(exc).__name__ + ": " + str(exc)
        values["errors"] = errors
        result = envelope(values, "yfinance statements", as_of)
        if errors:
            result.update(status="source_error", message="Bilanci incompleti: " + "; ".join(
                field + ": " + error for field, error in errors.items()))
        return result

    def filings(ticker, *, as_of):
        from bellomberg.valuation.dcf_engine import _fetch_history
        return envelope(_fetch_history(ticker, cache.get("info") or {}), "financial_history SEC/ESEF", as_of)

    def guidance(ticker, *, as_of):
        from bellomberg.valuation.dcf_engine import _fetch_guidance
        data, note = _fetch_guidance(ticker)
        result = envelope(data, "guidance registry", as_of)
        if note:
            result["message"] = note
        return result

    def consensus(ticker, *, as_of):
        from bellomberg.market_data.consensus_estimates import get_consensus
        return envelope(get_consensus(ticker), "consensus estimates", as_of)

    return {"profile": profile, "financials": financials, "filings": filings,
            "guidance": guidance, "consensus": consensus}


class AcquiredTicker:
    """Read-only statement view for legacy calculators, with no network fallback."""
    def __init__(self, bundle):
        self.info = deepcopy(bundle["case"]["info"])
        self._data = bundle["case"]["sources"].get("financials", {}).get("data") or {}

    def __getattr__(self, name):
        raw = self._data.get(name)
        if not isinstance(raw, dict) or not raw.get("table"):
            raise ValueError("Dato non acquisito nel bundle: " + name)
        import pandas as pd
        return pd.DataFrame(raw["data"], index=raw["index"], columns=pd.to_datetime(raw["columns"]))


def acquired_data(bundle, provider):
    source = bundle["case"]["sources"].get(provider, {})
    if source.get("status") != "ok":
        return {"error": source.get("message") or source.get("status", "Dato non acquisito")}
    return deepcopy(source.get("data"))
