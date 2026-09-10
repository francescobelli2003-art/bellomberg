"""Pure, sourced economic classification shared by valuation and research.

No portfolio reads, numeric priors, market fetches or symbol-based rules.
Industry labels narrow the candidates; explicit economic facts refine them.
Dates are observations, not invented expiry policies: stale/status/valid_until
come from the caller. This contract does not certify source authenticity.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json

from bellomberg.storage import classificazione as cl
from bellomberg.market_data.sector_taxonomy import classify

PROFILE_VERSION = "1"

# Business descriptors, not a licensed sector dataset or assumptions table.
BUSINESS_MODELS = frozenset("""
bank balance_sheet_lender managed_care insurance_pc insurance_life payment_network
payment_processor fee_asset_manager broker_fee software hardware semiconductors
semiconductor_fabless semiconductor_foundry semiconductor_equipment manufacturing
services telecom media consumer_discretionary consumer_staples pharma_mature medtech
hospital energy_services merchant_generation contracted_generation regulated_network
property_owner property_developer mortgage_lender resources development_asset
mixed_business cef investment_holding dat etf etn commodity crypto
""".split())

_LEGACY_CANDIDATES = {
    "banks - regional": {"bank"}, "banks - diversified": {"bank"},
    "healthcare plans": {"managed_care"}, "software - application": {"software"},
    "software - infrastructure": {"software"}, "computer hardware": {"hardware"},
    "semiconductors": {"semiconductors", "semiconductor_fabless", "semiconductor_foundry",
                        "semiconductor_equipment"},
    "solar": {"manufacturing"}, "aerospace": {"manufacturing"}, "steel": {"manufacturing"},
    "drug manufacturers": {"pharma_mature"}, "medical devices": {"medtech"},
    "oil & gas equipment & services": {"energy_services"}, "oil & gas e&p": {"resources"},
    "insurance": {"insurance_pc", "insurance_life"},
    "credit services": {"balance_sheet_lender", "payment_network", "payment_processor"},
    "asset management": {"fee_asset_manager", "investment_holding", "cef", "broker_fee"},
    "utilities - regulated": {"regulated_network", "contracted_generation", "mixed_business"},
    "utilities - renewable": {"contracted_generation", "merchant_generation", "development_asset"},
    "utilities - diversified": {"mixed_business"},
    "biotechnology": {"pharma_mature", "development_asset"}, "etf": {"etf"},
}
_INDUSTRY_CANDIDATES = {
    "insurance - property & casualty": {"insurance_pc"}, "insurance - life": {"insurance_life"},
    "insurance - diversified": {"insurance_pc", "insurance_life", "mixed_business"},
    "insurance - reinsurance": {"insurance_pc", "insurance_life"},
    "insurance brokers": {"broker_fee"}, "medical care facilities": {"hospital"},
    "health information services": {"services"}, "payment networks": {"payment_network"},
    "payment processing": {"payment_processor"}, "mortgage finance": {"mortgage_lender"},
    "reit - mortgage": {"mortgage_lender"}, "real estate - development": {"property_developer"},
    "real estate services": {"services"},
    "utilities - independent power producers": {"merchant_generation", "contracted_generation"},
    "investment banking & brokerage": {"bank", "broker_fee", "mixed_business"},
    "capital markets": {"bank", "broker_fee", "mixed_business"},
    "telecom services": {"telecom"}, "closed-end fund": {"cef"},
    "investment holding": {"investment_holding"},
}
_INSTRUMENT_MODELS = {"cef": "cef", "holding": "investment_holding", "dat": "dat",
                      "etf": "etf", "etn": "etn", "crypto": "crypto", "commodity": "commodity"}
_QUOTE_INSTRUMENT = {"ETF": "etf", "MUTUALFUND": "etf", "MONEYMARKET": "etf",
                     "CRYPTOCURRENCY": "crypto", "FUTURE": "commodity",
                     "INDEX": "index", "CURRENCY": "currency"}
_FIELDS = frozenset({"instrument", "quote_type", "business_model", "industry", "sector", "segments",
                     "regulatory_model", "regulator", "maturity", "balance_sheet_lending",
                     "fund_markers", "currency", "listing_type", "accounting_basis"})
_ALIASES = {"quoteType": "quote_type"}


def evidence_from_info(info, *, as_of):
    """Adapt an acquired provider snapshot; as_of records its observation date.

    S2 will supply richer dated records directly. Neither a quote nor the
    presence of revenue is treated as proof of a company's business model.
    """
    if info is not None and not isinstance(info, dict):
        return None  # The resolver declares invalid_evidence; do not erase the fault.
    info = info or {}
    records = [{"field": _ALIASES.get(k, k), "value": v,
                "source_id": "provider_info", "as_of": info.get("_as_of", as_of)}
               for k, v in info.items() if _ALIASES.get(k, k) in _FIELDS and v is not None and v != ""]
    if any(info.get(k) for k in ("fundFamily", "navPrice", "category")):
        records.append({"field": "fund_markers", "value": True,
                        "source_id": "provider_info", "as_of": info.get("_as_of", as_of)})
    return records


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _date(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date must use YYYY-MM-DD")
    return parsed


def _industry_options(industry):
    ind = industry.strip().lower()
    # Reuse the existing taxonomy and provenance without carrying its priors.
    label = classify(industry, negozio={"origine": "evidence", "veicoli": {}, "motivo": None},
                     classification_only=True)
    options = _INDUSTRY_CANDIDATES.get(ind)
    if options is None and ind.startswith("reit - ") and ind != "reit - mortgage":
        options = {"property_owner"}
    if options is None:
        options = _LEGACY_CANDIDATES.get(label["_profile_key"], set())
    return set(options), label


def resolve_valuation_profile(ticker, *, evidence, vehicle_registry, as_of):
    """Return a JSON profile; None registry means explicit absence, never I/O.

    Evidence records require field/value/source_id/as_of. Optional status is
    ok/stale/missing/error; valid_until supplies a source-specific expiry.
    Conflicts are not resolved by ordering or by a private registry winning.
    """
    issues, missing, records = [], set(), []

    def issue(code, message, field=None, blocking=False):
        issues.append({"code": code, "message": message, "field": field, "blocking": blocking})

    try:
        cutoff = _date(as_of)
    except (ValueError, TypeError):
        cutoff = None
        issue("invalid_as_of", "Valuation as_of must be an ISO date", "as_of", True)
        as_of = None

    registry = (cl.valida_negozio(vehicle_registry) if vehicle_registry is not None else
                {"origine": "assente", "veicoli": {}, "motivo": "explicitly absent"})
    if registry["origine"] == "illeggibile":
        issue("registry_error", "Vehicle registry ILLEGGIBILE / fonte ROTTA; validate it before valuation",
              "vehicle_registry", True)
    elif registry["origine"] == "assente":
        issue("registry_absent", "Registro veicoli ASSENTE: decision uses available sourced evidence")
    entry = registry["veicoli"].get(str(ticker or "").strip().upper())
    raw = list(evidence) if isinstance(evidence, (list, tuple)) else []
    if not isinstance(evidence, (list, tuple)):
        issue("invalid_evidence", "Evidence must be a list of sourced records", "evidence", True)
    registry_records = []
    if entry and entry["provenienza"] != "sconosciuto":
        verified = entry["verificato_il"]
        if verified is None:
            issue("registry_undated", "Registry declaration is undated; freshness is not assessed")
        # Preserve undated legacy declarations, clearly marked; never manufacture a date.
        for field, value in (("instrument", entry["tipo"]),
                             ("industry", entry["profilo_valutazione"])):
            if value and value != "sconosciuto":
                registry_records.append({"field": field, "value": value, "source_id": "vehicle_registry",
                                         "as_of": verified, "provenance": entry["provenienza"]})
    raw.extend(registry_records)

    values = {}
    active_registry_fields = set()
    for item in raw:
        if not isinstance(item, dict):
            issue("invalid_evidence", "Evidence record must be an object", "evidence", True)
            continue
        if not isinstance(item.get("field"), str):
            issue("invalid_evidence", "Evidence field must be a string", "evidence", True)
            continue
        field = _ALIASES.get(item["field"], item["field"])
        if field not in _FIELDS:
            # Mandate, holdings and preferences have no economic selection role.
            continue
        record = deepcopy(item)
        record["field"] = field
        if not isinstance(record.get("source_id"), str) or not record["source_id"].strip():
            issue("invalid_source", "Evidence requires source_id", field, True)
            continue
        try:
            _canonical(record)
        except (ValueError, TypeError):
            issue("invalid_evidence", "Evidence must be finite JSON data", field, True)
            continue
        records.append(record)
        undated_registry = any(item is r for r in registry_records) and record.get("as_of") is None
        try:
            observed = None if undated_registry else _date(record.get("as_of"))
            expires = _date(record["valid_until"]) if record.get("valid_until") is not None else None
            if cutoff is not None and observed is not None and observed > cutoff:
                raise ValueError("future evidence")
            if expires and observed and expires < observed:
                raise ValueError("inverted validity")
        except (ValueError, TypeError):
            issue("invalid_evidence_date", "Evidence date invalid or later than valuation cutoff", field, True)
            continue
        status = record.get("status", "ok")
        if status not in ("ok", "stale", "missing", "error") or status == "error":
            issue("source_error", "Evidence source returned an error or invalid status", field, True)
            continue
        if status in ("stale", "missing") or (expires and cutoff and expires < cutoff):
            issue("stale_evidence" if status != "missing" else "missing_evidence",
                  "Evidence STALE or missing; not used for method selection", field)
            missing.add(field)
            continue
        value = record.get("value")
        valid = (isinstance(value, list) if field == "segments" else
                 isinstance(value, bool) if field in ("fund_markers", "balance_sheet_lending") else
                 isinstance(value, str) and bool(value.strip()))
        if not valid:
            issue("invalid_evidence_value", "Evidence value has invalid type or is empty", field, True)
            continue
        if isinstance(value, str):
            value = value.strip()
            if field not in ("regulator", "currency", "accounting_basis"):
                value = value.lower()
        values.setdefault(field, []).append(value)
        if any(item is r for r in registry_records):
            active_registry_fields.add(field)

    values = {key: [v for _, v in sorted({_canonical(v): v for v in vals}.items())]
              for key, vals in values.items()}
    missing.difference_update(values)
    for field, vals in values.items():
        if len(vals) > 1:
            issue("conflicting_evidence", "Conflicting values for " + field, field, True)

    def one(field):
        vals = values.get(field, [])
        return vals[0] if len(vals) == 1 else None

    industry = one("industry")
    candidates, taxonomy = _industry_options(industry) if industry else (set(), None)
    considered = set(candidates)
    model = one("business_model")
    if model and model not in BUSINESS_MODELS:
        issue("unknown_business_model", "Business model not covered by the economic catalog", "business_model", True)
        missing.add("business_model")
        model = None
    if not candidates and industry:
        issue("unmapped_industry", "Industry not covered; no generic numerical profile", "industry")
    # A coarse semiconductor label preserves its established family; refinements
    # still come from evidence, not a guess between fabless/foundry/equipment.
    if not model and candidates and "semiconductors" in candidates:
        candidates = {"semiconductors"}

    instrument = one("instrument")
    if instrument == "investment_holding":
        instrument = "holding"
    if instrument in ("operating", "bank"):
        if instrument == "bank":
            if model and model != "bank":
                issue("conflicting_evidence", "Registry bank conflicts with business model", "business_model", True)
            model = model or "bank"
        instrument = "equity"
    if instrument and instrument not in {"equity", "index", "currency", *_INSTRUMENT_MODELS}:
        issue("unknown_instrument", "Instrument type is not covered", "instrument", True)
    qt = (one("quote_type") or "").upper()
    quoted_instrument = _QUOTE_INSTRUMENT.get(qt)
    if quoted_instrument:
        if instrument and instrument != quoted_instrument:
            issue("conflicting_evidence", "Instrument and quote type conflict", "instrument", True)
        instrument = instrument or quoted_instrument
    if not instrument and qt == "EQUITY":
        instrument = "equity"  # A listing type, never proof of an operating business.
    vehicle_model = _INSTRUMENT_MODELS.get(instrument)
    if vehicle_model:
        if model and model != vehicle_model:
            issue("conflicting_evidence", "Vehicle identity conflicts with business model", "instrument", True)
        model = vehicle_model
    if model in _INSTRUMENT_MODELS.values() and instrument in (None, "equity"):
        instrument = next(k for k, v in _INSTRUMENT_MODELS.items() if v == model)
    if model and candidates and model not in candidates:
        issue("conflicting_evidence", "Business model conflicts with industry evidence", "business_model", True)
    elif model:
        candidates = {model}

    # Balance-sheet and maturity facts constrain a label; never silently discard
    # them when they contradict a supplied business model.
    lending = one("balance_sheet_lending")
    credit = {"bank", "balance_sheet_lender", "mortgage_lender"}
    fee = {"payment_network", "payment_processor", "fee_asset_manager", "broker_fee"}
    if lending is not None and candidates:
        compatible = candidates & credit if lending else candidates - credit
        if lending and not (candidates & (credit | fee)):
            compatible = candidates  # E.g. captive credit may need later segment evidence.
        if not compatible:
            issue("conflicting_evidence", "Lending characteristics conflict with business economics",
                  "balance_sheet_lending", True)
        else:
            candidates = compatible
    maturity = one("maturity")
    if maturity == "precommercial" and candidates & {"pharma_mature", "development_asset"}:
        if "development_asset" in candidates:
            candidates = {"development_asset"}
        else:
            issue("conflicting_evidence", "Precommercial status conflicts with mature operating economics",
                  "maturity", True)
    elif maturity in ("mature", "commercial") and candidates == {"pharma_mature", "development_asset"}:
        candidates = {"pharma_mature"}
    if "regulated_network" in candidates and one("regulatory_model") == "rab" and one("regulator"):
        candidates = {"regulated_network"}

    segments = one("segments") or []
    segment_models = set()
    for segment in segments:
        if (not isinstance(segment, dict) or not isinstance(segment.get("business_model"), str)
                or segment["business_model"] not in BUSINESS_MODELS):
            issue("invalid_segment", "Each segment requires a recognized business_model", "segments", True)
            continue
        segment_models.add(segment["business_model"])
    if len(segment_models) > 1:
        if model and model != "mixed_business":
            issue("conflicting_evidence", "Distinct segment economics require mixed analysis", "segments", True)
        candidates = {"mixed_business"}
    elif segment_models and not candidates:
        candidates = segment_models
    if one("fund_markers") and not vehicle_model and not (candidates and candidates <= {"cef", "etf"}):
        issue("conflicting_evidence", "Fund markers require identity evidence even for EQUITY quotes", "instrument", True)
    if instrument in ("index", "currency"):
        candidates = set()
        issue("unsupported_instrument", "Instrument is not an equity business or supported vehicle", "instrument")
    if not instrument:
        missing.add("instrument")
    if "regulated_network" in candidates:
        if one("regulatory_model") != "rab" or not one("regulator"):
            missing.update({"regulatory_model", "regulator"})
            issue("regime_unproven", "RAB requires a documented regime and regulator", "regulatory_model", True)
    if len(candidates) != 1:
        missing.add("business_model")

    errors = {i["code"] for i in issues if i["blocking"]}
    conflicts = {"conflicting_evidence", "regime_unproven"}
    unknowns = {"unknown_business_model", "unknown_instrument"}
    if errors - conflicts - unknowns:
        status = "source_error"
    elif errors & conflicts or len(candidates) > 1:
        status = "ambiguous"
    elif errors & unknowns or len(candidates) != 1 or not instrument:
        status = "unknown"
    else:
        status = "resolved"
    profile_id = next(iter(candidates)) if status == "resolved" else None
    inferred_vehicle = profile_id in _INSTRUMENT_MODELS.values() and instrument == "equity"
    if inferred_vehicle:
        instrument = next(k for k, v in _INSTRUMENT_MODELS.items() if v == profile_id)
    # No default economic value: only identifiers and evidence pass this boundary.
    ordered = sorted({_canonical(r): r for r in records}.values(), key=_canonical)
    fingerprint = hashlib.sha256(_canonical({"evidence": ordered, "as_of": as_of,
                                            "profile_version": PROFILE_VERSION, "status": status,
                                            "issues": sorted({(i["code"], i["field"] or "") for i in issues}),
                                            "registry_status": registry["origine"] if registry["origine"] in
                                            ("assente", "illeggibile") else "present"}).encode()).hexdigest()
    rationale = ("Economic profile selected from sourced business/instrument evidence" if profile_id else
                 "Economic profile unresolved: complete identity/business evidence or resolve conflicts")
    registry_source = "registro_pm" if entry and entry["provenienza"] == "dichiarato" else "quote_type"
    if one("business_model"):
        source_kind = "misura_dato"
    elif (vehicle_model or (entry and entry["tipo"] == "bank")) and "instrument" in active_registry_fields:
        source_kind = registry_source
    elif "industry" in active_registry_fields:
        source_kind = registry_source
    elif vehicle_model and one("instrument"):
        source_kind = "misura_dato"
    elif taxonomy:
        source_kind = taxonomy["_etichetta"].fonte
        if source_kind == "nessuna":  # New exact economic distinctions extend the legacy labels.
            source_kind = "tassonomia_esatta"
    else:
        source_kind = "quote_type" if quoted_instrument else "misura_dato"
    nature_value = ("bank" if entry and entry["tipo"] == "bank" else
                    "operating" if instrument == "equity" else instrument)
    source_summary = "; ".join(sorted({r["source_id"] for r in records})) or "evidence absent"
    if registry["origine"] == "assente":
        source_summary += " | registro veicoli ASSENTE"
    if status == "source_error":
        nature = cl.guasto("natura", "Fonte ROTTA: " + "; ".join(i["message"] for i in issues if i["blocking"]))
    elif nature_value:
        nature_source = (source_kind if inferred_vehicle else
                         registry_source if "instrument" in active_registry_fields else
                         "misura_dato" if one("instrument") or one("business_model") else
                         "quote_type" if qt else source_kind)
        nature = cl.etichetta("natura", nature_value, nature_source, source_summary)
    else:
        nature = cl.sconosciuto("natura", source_summary)
    label = (cl.etichetta("profilo_valutazione", profile_id, source_kind, rationale + " | " + source_summary)
             if profile_id else cl.sconosciuto("profilo_valutazione", rationale + " | " + source_summary))
    return {"profile_id": profile_id, "profile_version": PROFILE_VERSION, "decision_status": status,
            "instrument": instrument, "sector": one("sector"), "subsector": industry,
            "economic_features": {k: one(k) for k in ("business_model", "regulatory_model", "regulator",
                                                       "maturity", "balance_sheet_lending") if one(k) is not None},
            "segments": deepcopy(segments), "evidence": ordered,
            "evidence_ids": sorted({r["source_id"] for r in ordered}), "issues": issues,
            "missing_fields": sorted(missing), "as_of": as_of, "input_fingerprint": fingerprint,
            "rationale": rationale, "excluded_alternatives": sorted(considered - {profile_id}) if profile_id else [],
            "candidate_profiles": sorted(candidates),
            "legacy_profile_key": taxonomy["_profile_key"] if taxonomy else None,
            "classification": {"natura": nature.as_dict(), "profile": label.as_dict()}}
