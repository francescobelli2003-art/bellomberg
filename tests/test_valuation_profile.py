"""Economic routing uses sourced facts, never a symbol or portfolio mandate."""
from copy import deepcopy
import json

import pytest

from bellomberg.storage import classificazione as cl

DAY = "2026-09-10"


def facts(**values):
    return [{"field": k, "value": v, "source_id": "synthetic-filing", "as_of": DAY}
            for k, v in values.items()]


def resolve(evidence, registry=None, symbol="SYNTH_A"):
    from bellomberg.valuation.valuation_profile import resolve_valuation_profile
    return resolve_valuation_profile(symbol, evidence=evidence,
                                     vehicle_registry=registry, as_of=DAY)


@pytest.mark.parametrize("model", [
    "bank", "managed_care", "insurance_pc", "insurance_life", "payment_network",
    "payment_processor", "fee_asset_manager", "hospital", "property_owner",
    "mortgage_lender", "merchant_generation", "investment_holding", "mixed_business",
])
def test_explicit_business_model_is_independent_of_symbol_and_policy(model):
    original = facts(instrument="equity", business_model=model)
    a = resolve(original)
    b = resolve(original + facts(settore_policy="arbitrary", portfolio_weight=0.7,
                                preferred_stance="short"), symbol="SYNTH_B")
    assert a["decision_status"] == b["decision_status"] == "resolved"
    assert a["profile_id"] == b["profile_id"] == model
    assert a["input_fingerprint"] == b["input_fingerprint"]
    assert "growth" not in json.dumps(a)


@pytest.mark.parametrize("industry,want", [
    ("Banks - Regional", "bank"), ("Healthcare Plans", "managed_care"),
    ("Medical Care Facilities", "hospital"), ("Insurance - Property & Casualty", "insurance_pc"),
    ("Insurance - Life", "insurance_life"), ("Payment Networks", "payment_network"),
    ("REIT - Industrial", "property_owner"), ("REIT - Mortgage", "mortgage_lender"),
    ("Software - Application", "software"), ("Oil & Gas E&P", "resources"),
])
def test_industry_distinctions_precede_legacy_keyword_routing(industry, want):
    p = resolve(facts(quote_type="EQUITY", industry=industry))
    assert p["decision_status"] == "resolved"
    assert p["profile_id"] == want


@pytest.mark.parametrize("industry", ["Credit Services", "Asset Management", "Insurance",
                                       "Biotechnology", "Utilities - Regulated Electric",
                                       "Utilities - Independent Power Producers", "Investment Banking & Brokerage"])
def test_broad_industry_requires_economic_evidence(industry):
    p = resolve(facts(quote_type="EQUITY", industry=industry))
    assert p["decision_status"] == "ambiguous"
    assert p["profile_id"] is None and p["missing_fields"]


def test_regulated_network_requires_evidence_of_rab_regime():
    p = resolve(facts(instrument="equity", business_model="regulated_network"))
    assert p["decision_status"] == "ambiguous"
    q = resolve(facts(instrument="equity", business_model="regulated_network",
                      regulatory_model="rab", regulator="synthetic-regulator"))
    assert q["decision_status"] == "resolved" and q["profile_id"] == "regulated_network"


def test_equity_quote_type_does_not_override_fund_evidence():
    p = resolve(facts(quote_type="EQUITY", instrument="cef", industry="Asset Management"))
    assert p["profile_id"] == "cef"
    q = resolve(facts(quote_type="EQUITY", industry="Software - Application", fund_markers=True))
    assert q["decision_status"] == "ambiguous" and q["profile_id"] is None


@pytest.mark.parametrize("extra,status", [
    ({"status": "stale"}, "unknown"),
    ({"as_of": "2026-09-08", "valid_until": "2026-09-09"}, "unknown"),
    ({"as_of": "2026-09-11"}, "source_error"), ({"source_id": ""}, "source_error"),
    ({"as_of": "nonsense"}, "source_error"), ({"status": "error"}, "source_error"),
])
def test_unusable_business_source_never_selects_a_model(extra, status):
    ev = facts(instrument="equity", business_model="managed_care")
    ev[1].update(extra)
    p = resolve(ev)
    assert p["decision_status"] == status and p["profile_id"] is None
    assert p["issues"]


def test_conflicting_facts_are_not_resolved_by_input_order():
    ev = facts(instrument="equity", business_model="bank") + facts(business_model="payment_network")
    for seq in (ev, list(reversed(ev))):
        p = resolve(seq)
        assert p["decision_status"] == "ambiguous" and p["profile_id"] is None


def test_new_industry_and_equity_alone_have_no_numeric_default():
    for ev in (facts(quote_type="EQUITY"), facts(quote_type="EQUITY", industry="Unmapped new activity")):
        p = resolve(ev)
        assert p["decision_status"] == "unknown" and p["profile_id"] is None
        assert not {"growth", "beta_u", "peers", "ebitda_m"}.intersection(p)


def test_registry_absent_is_not_corruption_and_resolver_performs_no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Implicit registry I/O")
    monkeypatch.setattr(cl, "carica_veicoli", forbidden)
    p = resolve(facts(instrument="equity", business_model="managed_care"))
    assert p["decision_status"] == "resolved"
    assert any(i["code"] == "registry_absent" for i in p["issues"])


@pytest.mark.parametrize("registry", [
    {"origine": "illeggibile", "motivo": "broken JSON", "veicoli": {}},
    {}, {"origine": "synthetic", "motivo": None, "veicoli": {"BAD": {"tipo": "banana"}}},
])
def test_broken_registry_cannot_be_bypassed(registry):
    p = resolve(facts(instrument="equity", business_model="bank"), registry)
    assert p["decision_status"] == "source_error" and p["profile_id"] is None


def test_registry_conflicts_are_not_private_overrides(tmp_path):
    path = tmp_path / "vehicles.json"
    path.write_text(json.dumps({"SYNTH_A": {"tipo": "bank", "provenienza": "dichiarato",
                                           "verificato_il": DAY}}))
    p = resolve(facts(instrument="equity", business_model="payment_network"), cl.carica_veicoli(str(path)))
    assert p["decision_status"] == "ambiguous" and p["profile_id"] is None


def test_material_segments_require_mixed_analysis_without_revenue_thresholds():
    ev = facts(instrument="equity", segments=[{"business_model": "bank"},
                                              {"business_model": "hospital"}])
    p = resolve(ev)
    assert p["decision_status"] == "resolved" and p["profile_id"] == "mixed_business"
    assert len(p["segments"]) == 2


def test_result_is_detached_serializable_and_stable_under_reordering():
    ev = facts(instrument="equity", business_model="managed_care")
    before = deepcopy(ev)
    p = resolve(ev)
    q = resolve(list(reversed(ev)))
    assert p["input_fingerprint"] == q["input_fingerprint"]
    json.dumps(p, allow_nan=False)
    p["evidence"][0]["value"] = "changed"
    assert ev == before


def test_classification_only_does_not_leak_legacy_assumptions():
    from bellomberg.market_data.sector_taxonomy import classify
    n = {"origine": "synthetic", "motivo": None, "veicoli": {}}
    p = classify("Software - Application", negozio=n, classification_only=True)
    assert p["_profile_key"] == "software - application"
    assert isinstance(p["_etichetta"], cl.Etichetta)
    assert not {"growth", "peers", "beta_u", "notes", "engine"}.intersection(p)


@pytest.mark.parametrize("industry", ["Healthcare Plans", "Insurance - Life", "Credit Services",
                                       "Unmapped new activity", "REIT - Industrial"])
def test_facade_never_falls_back_to_operating_or_bank(industry):
    from bellomberg.valuation.dcf_engine import decidi_percorso
    d = decidi_percorso("SYNTH_A", {"quoteType": "EQUITY", "industry": industry},
                       negozio={"origine": "assente", "veicoli": {}, "motivo": "no registry"})
    dedicated={'Healthcare Plans':'managed_care','Insurance - Life':'insurance_life','REIT - Industrial':'property_nav'}
    assert d['percorso']==dedicated[industry] if industry in dedicated else d['percorso'].startswith('rifiuto_')
    assert d['percorso'] not in ('operating','bank')
    assert "valuation_decision" in d


def test_facade_and_research_input_share_the_same_decision():
    from bellomberg.valuation.dcf_engine import decidi_percorso
    from bellomberg.valuation.method_registry import select_valuation_method
    ev = facts(instrument="equity", business_model="managed_care")
    direct = select_valuation_method(resolve(ev))
    d = decidi_percorso("SYNTH_B", {}, negozio=None, evidence=ev, as_of=DAY)
    assert d["valuation_decision"] == direct


def test_generate_refusal_carries_decision_without_invoking_calibration(tmp_path, monkeypatch):
    from bellomberg.valuation import dcf_engine as de
    from bellomberg.valuation import dcf_calibration
    def forbidden(*args, **kwargs):
        raise AssertionError("Wrong legacy calculator")
    monkeypatch.setattr(dcf_calibration, "build_spec_from_ticker", forbidden)
    result = de.generate_valuation("SYNTH_A", output_dir=str(tmp_path),
                                  fetch_info=lambda _: {"quoteType": "EQUITY", "industry": "Healthcare Plans"},
                                  negozio={"origine": "assente", "veicoli": {}, "motivo": "absent"})
    assert result["ok"] is False
    assert result["valuation_decision"]["method_id"] == "managed_care_distributable_equity"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("field", [[], {}])
def test_malformed_field_is_an_error_not_an_exception(field):
    p = resolve([{"field": field, "value": "x", "source_id": "fixture", "as_of": DAY}])
    assert p["decision_status"] == "source_error"


def test_external_record_cannot_claim_the_internal_undated_registry_exception():
    ev = facts(instrument="equity", business_model="software")
    ev[1].update(source_id="vehicle_registry", as_of=None)
    assert resolve(ev)["decision_status"] == "source_error"


@pytest.mark.parametrize("values", [
    {"business_model": "bank", "balance_sheet_lending": False},
    {"business_model": "payment_network", "balance_sheet_lending": True},
    {"business_model": "pharma_mature", "maturity": "precommercial"},
])
def test_features_cannot_silently_contradict_the_business_model(values):
    p = resolve(facts(instrument="equity", **values))
    assert p["decision_status"] == "ambiguous" and p["profile_id"] is None


@pytest.mark.parametrize("values,want", [
    ({"industry": "Credit Services", "balance_sheet_lending": True}, "balance_sheet_lender"),
    ({"industry": "Biotechnology", "maturity": "precommercial"}, "development_asset"),
    ({"industry": "Biotechnology", "maturity": "mature"}, "pharma_mature"),
    ({"industry": "Utilities - Regulated Electric", "regulatory_model": "rab",
      "regulator": "Synthetic authority"}, "regulated_network"),
    ({"industry": "Utilities - Independent Power Producers", "business_model": "contracted_generation"},
     "contracted_generation"),
])
def test_economic_features_refine_broad_industries(values, want):
    p = resolve(facts(instrument="equity", **values))
    assert p["decision_status"] == "resolved" and p["profile_id"] == want


def test_invalid_cutoff_never_breaks_serialization():
    from bellomberg.valuation.valuation_profile import resolve_valuation_profile
    p = resolve_valuation_profile("SYNTH", evidence=facts(business_model="software"),
                                   vehicle_registry=None, as_of=object())
    assert p["decision_status"] == "source_error"
    json.dumps(p, allow_nan=False)


@pytest.mark.parametrize("info", [["broken"], {"industry": ["Software"]}, {"sector": {}},
                                  {"quoteType": ["EQUITY"]}, {"industry": "Software - Application",
                                                               "quoteType": "EQUITY", "sector": 7}])
def test_facade_invalid_provider_snapshot_is_a_declared_error(info):
    from bellomberg.valuation.dcf_engine import decidi_percorso
    d = decidi_percorso("SYNTH", info, as_of=DAY)
    assert d["valuation_decision"]["decision_status"] == "source_error"
    assert d["percorso"] == "rifiuto_guasto"


def test_refusal_profile_cannot_expose_legacy_numeric_priors():
    from bellomberg.valuation.dcf_engine import decidi_percorso
    for industry in ("Unknown new activity", "Healthcare Plans", "Insurance - Life"):
        d = decidi_percorso("SYNTH", {"industry": industry, "quoteType": "EQUITY"}, as_of=DAY)
        assert not {"growth", "ebitda_m", "beta_u", "peers", "growth_cap"}.intersection(d["profilo"])


def test_sidecar_preserves_the_common_decision(tmp_path):
    from bellomberg.valuation import dcf_engine as de
    from bellomberg.valuation.method_registry import select_valuation_method
    decision = select_valuation_method(resolve(facts(instrument="equity", business_model="software")))
    path = tmp_path / "synthetic.xlsx"
    path.touch()  # The sidecar writer requires an existing workbook path.
    payload = {"path": str(path), "ok": True, "valuation_decision": decision}
    result = de._write_payload_sidecar(payload)
    saved = json.loads(path.with_suffix(".payload.json").read_text(encoding="utf-8"))
    assert saved["valuation_decision"] == result["valuation_decision"] == decision


def test_unknown_explicit_model_cannot_fall_back_to_an_industry():
    p = resolve(facts(instrument="equity", business_model="unmapped_new_business",
                      industry="Software - Application"))
    assert p["decision_status"] != "resolved" and p["profile_id"] is None


def test_excluded_candidates_are_separate_from_unresolved_candidates():
    from bellomberg.valuation.method_registry import select_valuation_method
    broad = resolve(facts(instrument="equity", industry="Credit Services"))
    specific = resolve(facts(instrument="equity", industry="Credit Services", balance_sheet_lending=True))
    assert broad["excluded_alternatives"] == []
    assert set(select_valuation_method(broad)["candidate_profiles"]) == {
        "balance_sheet_lender", "payment_network", "payment_processor"}
    assert set(specific["excluded_alternatives"]) == {"payment_network", "payment_processor"}


def test_bad_segment_business_model_is_declared():
    assert resolve(facts(instrument="equity", segments=[{"business_model": []}]))["decision_status"] == "source_error"


def test_profile_inference_does_not_inherit_measured_instrument_confidence():
    p = resolve(facts(instrument="equity", industry="Aerospace & Defense"))
    assert p["classification"]["natura"]["confidenza"] == "misurata"
    assert p["classification"]["profile"]["fonte"] == "tassonomia_keyword"
    assert p["classification"]["profile"]["confidenza"] == "dedotta"


def test_stale_duplicate_does_not_report_a_current_field_as_missing():
    stale = facts(business_model="managed_care")[0]
    stale.update(status="stale", as_of="2026-09-01")
    p = resolve(facts(instrument="equity", business_model="managed_care") + [stale])
    assert p["decision_status"] == "resolved"
    assert "business_model" not in p["missing_fields"]
    assert any(i["code"] == "stale_evidence" for i in p["issues"])


@pytest.mark.parametrize("industry,want", [("Closed-End Fund", "cef"), ("Investment Holding", "holding")])
def test_fund_industry_refines_equity_listing_without_calling_it_operating(industry, want):
    p = resolve(facts(quote_type="EQUITY", industry=industry))
    assert p["instrument"] == want
    assert p["classification"]["natura"]["valore"] == want
    assert p["classification"]["natura"]["confidenza"] == "dedotta"


def test_generate_handles_malformed_provider_before_touching_output(tmp_path):
    from bellomberg.valuation import dcf_engine as de
    output = tmp_path / "not-created"
    r = de.generate_valuation("SYNTH", output_dir=str(output), fetch_info=lambda _: ["bad"],
                              negozio={"origine": "assente", "veicoli": {}, "motivo": "absent"})
    assert r["ok"] is False and r["valuation_decision"]["decision_status"] == "source_error"
    assert not output.exists()
