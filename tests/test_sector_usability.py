"""Synthetic cache/consumer contract tests; no provider, workbook or live DB."""
from copy import deepcopy

import pytest

from bellomberg.valuation import dcf_quality
from bellomberg.valuation.method_registry import get_method_requirements, select_valuation_method, is_record_method
from bellomberg.valuation.valuation_profile import resolve_valuation_profile


DAY = "2026-09-10"


def payload_for(model="software"):
    evidence = [{"field": field, "value": value, "source_id": "synthetic-issuer", "as_of": DAY}
                for field, value in (("instrument", "equity"), ("business_model", model))]
    decision = select_valuation_method(resolve_valuation_profile(
        "SYNTH", evidence=evidence, vehicle_registry=None, as_of=DAY))
    decision.update(requirements_status="complete", missing_fields=[])
    required = get_method_requirements(decision["method_id"])["fields"]
    payload = {"ticker": "SYNTH", "valuation_decision": decision,
            "snapshot_id": "synthetic-snapshot", "generation_id": "synthetic-generation",
            "fair_value_weighted": 120.0, "upside_pct": 20.0,
            "analytical_quality": {"status": "DOCUMENTATA", "method_id": decision["method_id"],
                                   "issues": [], "snapshot": {"as_of": DAY}},
            "sanity": {"severity": "OK", "method_id": decision["method_id"]},
            "input_consumption": {"status": "complete", "consumed_fields": [r["field"] for r in required],
                                  "unconsumed_fields": []}}
    # Managed-care's historical helper deliberately has no attestation (S3 negative test).
    if is_record_method(decision) and model != 'managed_care':
        from bellomberg.valuation.sector_analysis import prepare_sector_analysis
        records=[dict(field=r['field'],driver=r['field'],scenario='model',value=1.,entity='SYNTH',
                      period='FY2026',unit='synthetic',accounting_basis='synthetic',
                      source_id='synthetic-issuer',as_of=DAY) for r in required]
        bundle=prepare_sector_analysis('SYNTH',as_of=DAY,providers={
            'profile':lambda *a,**k: {'status':'ok','source_id':'synthetic','as_of':DAY,
                'data':{'evidence':evidence}},
            'method_inputs':lambda *a,**k: {'status':'ok','source_id':'synthetic','as_of':DAY,'records':records}})
        payload.update(valuation_decision=bundle['decision'],snapshot_id=bundle['snapshot_id'],acquisition_snapshot=bundle)
        payload['input_consumption']['consumed_records']=[dict(record_index=i,**{k:r[k] for k in
            ('field','scenario','driver','entity','period','source_id')}) for i,r in enumerate(records)]
    return payload


def assess(payload, **kwargs):
    assert hasattr(dcf_quality, "assess_valuation_usability"), "S2 common usability gate is missing"
    return dcf_quality.assess_valuation_usability(payload, as_of=DAY, **kwargs)


@pytest.mark.parametrize("model", ["software", "bank", "cef", "dat"])
def test_documented_method_specific_payload_is_usable_without_operating_requirements(model):
    original = payload_for(model)
    before = deepcopy(original)
    result = assess(original, expected_decision=original["valuation_decision"])
    assert result == {"usable": True, "reasons": [], "missing_fields": []}
    assert original == before
    if model != "software":
        assert "reinvestment" not in original["input_consumption"]["consumed_fields"]


def test_legacy_number_is_readable_but_never_usable():
    original = {"ticker": "SYNTH", "fair_value_base": 50, "sanity": {"severity": "OK"}}
    result = assess(original)
    assert result["usable"] is False
    assert "valuation_decision" in result["missing_fields"]
    assert original["fair_value_base"] == 50


@pytest.mark.parametrize("field,value", [
    ("contract_version", "0"), ("registry_version", "unknown"), ("profile_version", "0"),
    ("method_version", "0"), ("method_id", "invented"), ("profile_id", "bank"),
    ("support_status", "calculator_only"), ("decision_status", "ambiguous"),
    ("requirements_status", "not_assessed"), ("missing_fields", ["quotation_units"]),
    ("input_fingerprint", "forged"), ("as_of", "2026-09-09"), ("evidence", []),
])
def test_changed_or_unassessed_decision_cannot_reuse_a_number(field, value):
    payload = payload_for()
    payload["valuation_decision"][field] = value
    assert assess(payload)["usable"] is False


@pytest.mark.parametrize("model", ["managed_care", "insurance_pc", "investment_holding"])
def test_existing_number_without_attested_bundle_cannot_promote_a_method(model):
    payload=payload_for(model)
    payload.pop('acquisition_snapshot',None)
    assert assess(payload)["usable"] is False


@pytest.mark.parametrize("field,value", [("as_of", "2026-09-11"), ("valid_until", "2026-09-09"),
                                         ("source_id", ""), ("status", "stale")])
def test_invalid_classification_evidence_is_not_hidden_by_a_current_hash(field, value):
    payload = payload_for()
    payload["valuation_decision"]["evidence"][0][field] = value
    assert assess(payload)["usable"] is False


def test_changed_evidence_with_unmodified_fingerprint_is_rejected():
    payload = payload_for()
    payload["valuation_decision"]["evidence"][0]["source_id"] = "another-source"
    assert assess(payload)["usable"] is False


@pytest.mark.parametrize("field,value", [("input_fingerprint", "0" * 64), ("as_of", "2026-09-09"),
                                         ("method_version", "changed-version"), ("snapshot_id", "other"),
                                         ("generation_id", "other")])
def test_expected_current_identity_prevents_stale_cache_or_sidecar_reuse(field, value):
    payload = payload_for()
    expected = dict(payload["valuation_decision"], snapshot_id=payload["snapshot_id"],
                    generation_id=payload["generation_id"])
    expected[field] = value
    assert assess(payload, expected_decision=expected)["usable"] is False


@pytest.mark.parametrize("field", ["snapshot_id", "generation_id"])
def test_generation_and_snapshot_are_required_and_nested_copies_must_match(field):
    payload = payload_for()
    del payload[field]
    assert assess(payload)["usable"] is False
    payload = payload_for()
    payload["sidecar"] = {field: "another-output", "fair_value_base": 199}
    assert assess(payload)["usable"] is False


@pytest.mark.parametrize("key,change", [
    ("analytical_quality", {"status": "BOZZA_AUTOMATICA"}),
    ("analytical_quality", {"status": "INCOMPLETA"}),
    ("analytical_quality", {"issues": ["assumption source missing"]}),
    ("analytical_quality", {"method_id": "bank_residual_income"}),
    ("sanity", {"severity": None}), ("sanity", {"severity": "UNKNOWN"}),
    ("sanity", {"severity": "BLOCK"}), ("sanity", {"method_id": "fund_nav"}),
    ("input_consumption", {"status": "not_assessed"}),
    ("input_consumption", {"consumed_fields": []}),
    ("input_consumption", {"unconsumed_fields": ["arbitrary_assumption"]}),
])
def test_number_requires_documentation_sanity_and_consumed_family_inputs(key, change):
    payload = payload_for()
    payload[key].update(change)
    result = assess(payload)
    assert result["usable"] is False
    assert result["reasons"]


@pytest.mark.parametrize("number", [float("inf"), float("nan"), True, "120", None])
def test_primary_fair_value_must_be_a_finite_number(number):
    payload = payload_for()
    payload["fair_value_weighted"] = number
    assert assess(payload)["usable"] is False


@pytest.mark.parametrize("child", [
    {"sanity": {"severity": "BLOCK"}, "fair_value_nav": 77},
    {"analytical_quality": {"status": "INCOMPLETA"}, "fair_value_sotp": 80},
    {"usable": False, "fair_value_base": 90},
    {"status": "BLOCK", "fair_value_base": 95},
    {"fair_value_base": float("inf")},
])
def test_nested_secondary_valuations_cannot_escape_the_common_gate(child):
    payload = payload_for()
    payload["methods"] = [{"result": child}]
    assert assess(payload)["usable"] is False


def test_normalization_hides_nested_valuation_numbers_but_preserves_observed_data_and_gaps():
    payload = payload_for("cef")
    payload["sanity"]["severity"] = "BLOCK"
    payload.update(price=100, nav_per_share=123, missing_fields=["source"],
                   methods=[{"fair_value_nav": 88, "upside_pct": -12, "nav_target": 0.9,
                             "enterprise_value": 500, "equity_value": 450, "notes": "source incomplete"}])
    before = deepcopy(payload)
    assert hasattr(dcf_quality, "normalize_valuation_payload"), "S2 normalization is missing"
    result = dcf_quality.normalize_valuation_payload(payload, as_of=DAY)
    assert result["valuation_usability"]["usable"] is False
    assert result["fair_value_weighted"] is result["upside_pct"] is None
    for key in ("fair_value_nav", "upside_pct", "nav_target", "enterprise_value", "equity_value"):
        assert result["methods"][0][key] is None
    assert result["price"] == 100 and result["nav_per_share"] == 123
    assert result["methods"][0]["notes"] == "source incomplete"
    assert result["missing_fields"] == ["source"]
    assert result["valuation_decision"] == payload["valuation_decision"]
    assert payload == before


@pytest.mark.parametrize("change", [{"status": "n/d"}, {"exclude_from_action_table": True}])
def test_sanity_ok_label_does_not_hide_unavailable_or_excluded_result(change):
    payload = payload_for()
    payload["sanity"].update(change)
    assert assess(payload)["usable"] is False


def test_nested_incomplete_consumption_is_not_covered_by_parent_attestation():
    payload = payload_for()
    payload["secondary"] = {"fair_value_sotp": 111, "input_consumption": {
        "status": "incomplete", "consumed_fields": [], "unconsumed_fields": ["segments"]}}
    assert assess(payload)["usable"] is False


def test_documented_irr_container_is_accepted_and_unusable_numbers_hide_without_losing_notes():
    payload = payload_for()
    payload["holding_irr"] = {"by_scenario": {"bear": -0.1, "base": 0.05, "bull": 0.1},
                              "years": 3, "entry_price": 100, "note": "stated assumption"}
    assert assess(payload)["usable"] is True
    payload["sanity"]["severity"] = "BLOCK"
    normalized = dcf_quality.normalize_valuation_payload(payload, as_of=DAY)
    assert normalized["holding_irr"]["by_scenario"] == {"bear": None, "base": None, "bull": None}
    assert normalized["holding_irr"]["note"] == "stated assumption"
    assert normalized["holding_irr"]["entry_price"] == 100


def test_normalization_preserves_raw_case_source_records():
    payload = payload_for()
    payload["sanity"]["severity"] = "BLOCK"
    payload["case"] = {"records": [{"field": "historical_financials", "value": {
        "equity": 200, "enterprise_value": 180, "source": "synthetic-filing"}}]}
    normalized = dcf_quality.normalize_valuation_payload(payload, as_of=DAY)
    assert normalized["case"] == payload["case"]


def test_existing_refusal_is_not_promoted_by_another_consumer():
    payload = payload_for()
    payload["valuation_usability"] = {"usable": False, "reasons": ["sidecar belongs to another snapshot"],
                                      "missing_fields": ["generation_proof"]}
    normalized = dcf_quality.normalize_valuation_payload(payload, as_of=DAY)
    assert normalized["fair_value_weighted"] is None
    assert "sidecar belongs to another snapshot" in normalized["valuation_usability"]["reasons"]
    assert "generation_proof" in normalized["valuation_usability"]["missing_fields"]


def test_different_acquired_data_invalidates_cache_even_if_economic_profile_matches():
    payload = payload_for()
    payload["valuation_decision"]["acquisition_fingerprint"] = "synthetic-old-data"
    expected = dict(payload["valuation_decision"], acquisition_fingerprint="synthetic-new-data")
    assert assess(payload, expected_decision=expected)["usable"] is False


def test_documented_summary_does_not_override_row_level_gaps():
    payload = payload_for()
    payload["analytical_quality"]["rows"] = [{"driver": "any-family-driver", "issues": ["source stale"]}]
    assert assess(payload)["usable"] is False


def test_sotp_short_fair_value_alias_cannot_leak_after_normalization():
    payload = payload_for()
    payload["sanity"]["severity"] = "BLOCK"
    payload["sotp"] = {"fv_ps": 150, "note": "secondary model"}
    normalized = dcf_quality.normalize_valuation_payload(payload, as_of=DAY)
    assert normalized["sotp"]["fv_ps"] is None


@pytest.mark.parametrize("flag", ["exclude_from_action_table", "valuation_flagged"])
def test_explicit_top_level_exclusion_overrides_sanity_ok(flag):
    payload = payload_for()
    payload[flag] = True
    normalized = dcf_quality.normalize_valuation_payload(payload, as_of=DAY)
    assert normalized["valuation_usability"]["usable"] is False
    assert normalized["fair_value_weighted"] is None


@pytest.mark.parametrize("flag", ["exclude_from_action_table", "valuation_flagged"])
def test_nested_exclusion_cannot_be_overridden_by_parent_sanity(flag):
    payload = payload_for()
    payload["secondary"] = {flag: True, "fair_value_sotp": 150}
    normalized = dcf_quality.normalize_valuation_payload(payload, as_of=DAY)
    assert normalized["valuation_usability"]["usable"] is False
    assert normalized["secondary"]["fair_value_sotp"] is None
