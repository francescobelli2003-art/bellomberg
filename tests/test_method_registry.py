"""Economic method selection does not certify data readiness or a fair value."""

from copy import deepcopy
import importlib
import json

import pytest


def _registry():
    return importlib.import_module("bellomberg.valuation.method_registry")


def _profile(profile_id, **changes):
    profile = {
        "profile_id": profile_id,
        "profile_version": "1",
        "decision_status": "resolved",
        "instrument": "equity",
        "sector": "synthetic economic sector",
        "subsector": profile_id,
        "economic_features": {},
        "segments": [],
        "evidence_ids": ["fixture:activity"],
        "evidence": [{"field": "business_model", "value": profile_id,
                      "source_id": "fixture:activity", "as_of": "2026-09-10"}],
        "issues": [],
        "missing_fields": [],
        "as_of": "2026-09-10",
        "input_fingerprint": "synthetic-evidence-fingerprint",
        "rationale": "Activity is documented by synthetic source.",
        "excluded_alternatives": [],
    }
    profile.update(changes)
    return profile


@pytest.mark.parametrize("profile_id,method_id,support", [
    ("bank", "bank_residual_income", "integrated"),
    ("balance_sheet_lender", "bank_residual_income", "integrated"),
    ("mortgage_lender", "bank_residual_income", "integrated"),
    ("managed_care", "managed_care_distributable_equity", "integrated"),
    ("insurance_pc", "insurance_pc_distributable_equity", "integrated"),
    ("insurance_life", "insurance_life_distributable_equity", "integrated"),
    ("payment_network", "operating_fcff", "integrated"),
    ("payment_processor", "operating_fcff", "integrated"),
    ("fee_asset_manager", "operating_fcff", "integrated"),
    ("hospital", "operating_fcff", "integrated"),
    ("semiconductors", "operating_fcff", "integrated"),
    ("merchant_generation", "operating_fcff", "integrated"),
    ("regulated_network", "regulated_rab", "integrated"),
    ("property_owner", "property_nav", "integrated"),
    ("property_developer", "property_development_fcff", "integrated"),
    ("resources", "resources_asset_dcf", "integrated"),
    ("development_asset", "development_rnpv", "integrated"),
    ("mixed_business", "mixed_business_sotp", "integrated"),
    ("cef", "fund_nav", "integrated"),
    ("investment_holding", "fund_nav", "integrated"),
    ("dat", "digital_asset_nav", "integrated"),
    ("etf", "exposure_analysis", "integrated"),
    ("etn", "exposure_analysis", "integrated"),
    ("commodity", "exposure_analysis", "integrated"),
    ("crypto", "exposure_analysis", "integrated"),
])
def test_economics_select_method_and_actual_adapter_readiness(profile_id, method_id, support):
    decision = _registry().select_valuation_method(_profile(profile_id))
    assert decision["method_id"] == method_id
    assert decision["decision_status"] == "resolved"
    assert decision["support_status"] == support
    assert decision["requirements_status"] == "not_assessed"
    assert decision["missing_fields"]
    assert "fair_value" not in decision
    if support != "integrated":
        assert decision["legacy_route"] is None


@pytest.mark.parametrize("status", ["ambiguous", "unknown", "source_error"])
def test_unresolved_profile_never_selects_method_even_with_known_profile_id(status):
    decision = _registry().select_valuation_method(_profile("bank", decision_status=status,
        missing_fields=["business_model"],
        issues=[{"code": "activity_conflict", "message": "Conflicting source.",
                 "field": "business_model", "blocking": True}]))
    assert decision["decision_status"] == status
    assert decision["method_id"] is None
    assert decision["support_status"] is None
    assert decision["legacy_route"] is None
    assert decision["missing_fields"] == ["business_model"]
    assert decision["issues"][0]["code"] == "activity_conflict"


def test_unmapped_profile_declares_gap_without_default_fcff():
    decision = _registry().select_valuation_method(_profile("new_business_not_in_catalog"))
    assert decision["decision_status"] == "unknown"
    assert decision["method_id"] is None
    assert decision["legacy_route"] is None
    assert "profile_id" in decision["missing_fields"]
    assert any(issue["code"] == "unmapped_profile" for issue in decision["issues"])


def test_profile_with_blocking_issue_cannot_claim_resolved():
    decision = _registry().select_valuation_method(_profile("software", issues=[{
        "code": "invalid_evidence", "message": "Source has no date.",
        "field": "as_of", "blocking": True}]))
    assert decision["decision_status"] == "source_error"
    assert decision["method_id"] is None


def test_evidence_and_versions_survive_serialization_without_aliasing_inputs():
    profile = _profile("managed_care", excluded_alternatives=[{
        "profile_id": "hospital", "reason": "Medical risk retained by the health plan."}])
    original = deepcopy(profile)
    decision = _registry().select_valuation_method(profile)
    payload = json.loads(json.dumps(decision))
    assert payload["evidence_ids"] == ["fixture:activity"]
    assert payload["evidence"] == original["evidence"]
    assert payload["as_of"] == "2026-09-10"
    assert payload["input_fingerprint"] == "synthetic-evidence-fingerprint"
    assert payload["profile_id"] == "managed_care"
    assert payload["profile_version"] == "1"
    assert payload["method_version"] and payload["registry_version"] and payload["contract_version"]
    decision["evidence"][0]["value"] = "modified outside"
    decision["excluded_alternatives"].clear()
    assert profile == original


def test_ticker_and_portfolio_policy_never_change_economic_method():
    a = _profile("payment_network", ticker="SYNTH_A", settore_policy="private_bucket")
    b = _profile("payment_network", ticker="SYNTH_B", settore_policy="other_bucket",
                 portfolio_member=True, user_stance="short")
    assert _registry().select_valuation_method(a) == _registry().select_valuation_method(b)


@pytest.mark.parametrize("method_id,needed,forbidden", [
    ("operating_fcff", {"revenue_drivers", "reinvestment", "enterprise_equity_bridge"},
     {"legal_entity_capital", "recognized_regulatory_base"}),
    ("bank_residual_income", {"tangible_book_equity", "regulatory_capital", "equity_return_path"},
     {"enterprise_equity_bridge", "working_capital"}),
    ("managed_care_distributable_equity", {"premium_revenue", "medical_costs", "legal_entity_capital", "parent_ledger"},
     {"enterprise_equity_bridge", "working_capital"}),
    ("regulated_rab", {"regulatory_regime", "recognized_regulatory_base", "allowed_return"},
     {"legal_entity_capital"}),
    ("fund_nav", {"nav_source", "nav_per_share", "quotation_units"},
     {"working_capital", "revenue_drivers"}),
    ("development_rnpv", {"asset_stages", "conditional_probabilities", "development_funding"},
     {"recognized_regulatory_base"}),
])
def test_requirements_describe_economic_inputs_with_metadata(method_id, needed, forbidden):
    requirements = _registry().get_method_requirements(method_id)
    fields = {record["field"] for record in requirements["fields"] if record["required"]}
    assert needed <= fields
    assert not fields.intersection(forbidden)
    assert requirements["periods"] and requirements["sources"]
    assert requirements["reconciliations"] and requirements["drivers"]
    assert {"entity", "period", "unit", "accounting_basis", "source_id", "as_of"} <= set(requirements["record_metadata"])
    assert all("default" not in record for record in requirements["fields"])


def test_complete_looking_evidence_does_not_satisfy_data_requirements_in_s1():
    profile = _profile("bank", economic_features={"tangible_book_equity": 100,
        "regulatory_capital": 30, "equity_return_path": [0.1]}, missing_fields=[])
    decision = _registry().select_valuation_method(profile)
    assert decision["requirements_status"] == "not_assessed"
    assert "tangible_book_equity" in decision["missing_fields"]
    assert "regulatory_capital" in decision["missing_fields"]


def test_requirements_are_isolated_and_unknown_method_fails_explicitly():
    first = _registry().get_method_requirements("fund_nav")
    first["fields"].clear()
    second = _registry().get_method_requirements("fund_nav")
    assert any(item["field"] == "nav_source" for item in second["fields"])
    with pytest.raises(ValueError, match="unknown method"):
        _registry().get_method_requirements("unavailable_method")


@pytest.mark.parametrize("profile_id", ["broker_fee", "services", "telecom", "media",
    "consumer_discretionary", "consumer_staples", "contracted_generation",
    "semiconductor_fabless", "semiconductor_foundry", "semiconductor_equipment"])
def test_documented_fcff_adapter_still_requires_assessed_case_inputs(profile_id):
    decision = _registry().select_valuation_method(_profile(profile_id))
    assert decision["method_id"] == "operating_fcff"
    assert decision["support_status"] == "integrated"
    assert decision["legacy_route"] == "operating"
    assert decision["requirements_status"] == "not_assessed"
    assert decision["missing_fields"]


@pytest.mark.parametrize("changes", [
    {"profile_id": []}, {"decision_status": []}, {"issues": ["invalid issue"]},
    {"issues": {"code": "not_an_issue_list"}}, {"missing_fields": "business_model"},
    {"missing_fields": [[]]}, {"rationale": []}, {"evidence": [object()]},
    {"evidence_ids": [object()]}, {"economic_features": {"bad": float("nan")}},
])
def test_malformed_profile_is_source_error_without_method_or_exception(changes):
    profile = _profile("software")
    profile.update(changes)
    decision = _registry().select_valuation_method(profile)
    assert decision["decision_status"] == "source_error"
    assert decision["method_id"] is None
    assert decision["legacy_route"] is None
    assert any(issue["code"] == "invalid_profile" for issue in decision["issues"])
    json.dumps(decision, allow_nan=False)


def test_holding_requires_documented_nav_adapter_and_unassessed_inputs():
    decision = _registry().select_valuation_method(_profile("investment_holding"))
    assert decision["support_status"] == "integrated"
    assert decision["adapter"] == 'bellomberg.valuation.nav_adapter:generate_nav'
    assert decision['requirements_status']=='not_assessed'
