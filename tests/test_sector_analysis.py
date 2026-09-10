"""Synthetic acquisition: never requires a portfolio, credential or network."""
from copy import deepcopy
import json

import pytest

from bellomberg.valuation import sector_analysis as sector
from bellomberg.valuation.method_registry import get_method_requirements

DAY = "2026-09-10"


def providers_for(business="software", *, records=None, calls=None):
    def profile(ticker, *, as_of):
        if calls is not None:
            calls.append((ticker, "profile"))
        return {"status": "ok", "source_id": "synthetic-profile", "as_of": DAY,
                "data": {"info": {"quoteType": "EQUITY", "industry": "Software - Application",
                                    "sector": "Technology", "shortName": "Synthetic issuer"},
                         "vehicle_registry": None,
                         "evidence": [{"field": "instrument", "value": "equity", "source_id": "fixture", "as_of": DAY},
                                      {"field": "business_model", "value": business, "source_id": "fixture", "as_of": DAY}]}}
    def inputs(ticker, *, as_of):
        if calls is not None:
            calls.append((ticker, "inputs"))
        return {"status": "ok", "source_id": "synthetic-filing", "as_of": DAY,
                "data": {"note": "synthetic input"}, "records": deepcopy(records or [])}
    return {"profile": profile, "method_inputs": inputs}


def complete_records(method="operating_fcff"):
    return [{"field": f["field"], "value": {"synthetic_measure": 10}, "entity": "issuer",
             "period": "FY2026", "unit": "explicit synthetic units", "accounting_basis": "IFRS",
             "source_id": "fixture", "as_of": DAY}
            for f in get_method_requirements(method)["fields"]]


def test_symbol_and_policy_do_not_select_method_or_assumptions():
    a = sector.prepare_sector_analysis("EXTERNAL.A", as_of=DAY, providers=providers_for(),
                                      user_context={"holdings": ["A"], "mandate": "long"})
    b = sector.prepare_sector_analysis("RENAMED.B", as_of=DAY, providers=providers_for(),
                                      user_context={"holdings": [], "mandate": "short"})
    assert a["decision"] == b["decision"]
    assert a["case"]["assumptions"] == b["case"]["assumptions"] == {}
    assert a["snapshot_id"] != b["snapshot_id"]  # identity belongs to the snapshot
    json.dumps(a, allow_nan=False)


def test_all_required_records_are_assessed_without_claiming_fv_or_consumption():
    bundle = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for(records=complete_records()))
    assert bundle["decision"]["requirements_status"] == "complete"
    assert bundle["decision"]["missing_fields"] == []
    assert "fair_value" not in bundle and "input_consumption" not in bundle
    assert bundle["case"]["records"][0]["entity"] == "issuer"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {"nested": float("nan")}])
def test_nonfinite_input_is_source_error_and_requirement_remains_missing(bad):
    records = complete_records()
    records[0]["value"] = bad
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for(records=records))
    assert b["case"]["sources"]["method_inputs"]["status"] == "source_error"
    assert b["decision"]["requirements_status"] == "incomplete"
    assert b["acquisition_tasks"]


@pytest.mark.parametrize("mutation", [{"field": []}, {"entity": 1}, {"as_of": "2099-01-01"},
    {"valid_until": "2026-09-09"}, {"status": "stale"}, {"accounting_basis": {"bad": True}}])
def test_invalid_input_is_declared_and_not_complete(mutation):
    records = complete_records()
    records[0].update(mutation)
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for(records=records))
    assert b["decision"]["requirements_status"] == "incomplete"
    assert any(i["blocking"] for i in b["decision"]["issues"])


def test_conflict_is_not_resolved_by_provider_order():
    records = complete_records()
    records += [{**records[0], "value": 999, "source_id": "different"}]
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for(records=records))
    assert records[0]["field"] in b["decision"]["missing_fields"]
    assert any(i["code"] == "conflicting_input" for i in b["decision"]["issues"])


def test_credentials_missing_differs_from_absent_data():
    providers = providers_for()
    def denied(*args, **kwargs):
        raise PermissionError("Credential unavailable")
    providers["guidance"] = denied
    providers["consensus"] = lambda *a, **kw: {"status": "ok", "source_id": "fixture", "as_of": DAY, "data": None}
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers)
    assert b["case"]["sources"]["guidance"]["status"] == "credentials_missing"
    assert b["case"]["sources"]["consensus"]["status"] == "data_missing"
    assert sector.acquired_data(b, "consensus")["error"]


def test_missing_profile_does_not_trigger_live_or_guess_method():
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers={})
    assert b["decision"]["decision_status"] == "source_error"
    assert b["decision"]["method_id"] is None


def test_analyst_revision_reuses_facts_and_changes_snapshot():
    calls = []
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for(calls=calls))
    revised = sector.revise_sector_analysis(b, assumptions={"growth_override": [0.1]})
    assert len(calls) == 2
    assert revised["snapshot_id"] != b["snapshot_id"]
    assert revised["case"]["sources"] == b["case"]["sources"]
    assert revised["decision"]["input_fingerprint"] == b["decision"]["input_fingerprint"]
    assert revised["decision"]["acquisition_fingerprint"] != b["decision"]["acquisition_fingerprint"]
    sector.validate_bundle(revised, "OUTSIDE")


def test_mutated_or_wrong_symbol_bundle_is_rejected_before_io():
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for())
    with pytest.raises(ValueError, match="altro ticker"):
        sector.validate_bundle(b, "ELSEWHERE")
    b["case"]["info"]["currentPrice"] = 999
    with pytest.raises(ValueError, match="modificato"):
        sector.validate_bundle(b, "OUTSIDE")


def test_prepared_method_input_is_not_silently_ignored_by_legacy(monkeypatch):
    from bellomberg.valuation import dcf_engine
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for(records=complete_records()))
    monkeypatch.setattr(dcf_engine, "_generate_valuation_legacy", lambda *a, **k: pytest.fail("Unconsumed records"))
    result = dcf_engine.generate_valuation("OUTSIDE", prepared_bundle=b)
    assert result["snapshot_id"] == b["snapshot_id"]
    assert not result["valuation_usability"]["usable"]
    assert result["input_consumption"]["unconsumed_fields"]


@pytest.mark.parametrize("kwargs", [{"as_of": "2026-09-11"}, {"analysis_context": {"note": "changed"}},
                                  {"growth_override": [0.9]}])
def test_explicit_inputs_cannot_be_ignored_with_prepared_bundle(monkeypatch, kwargs):
    from bellomberg.valuation import dcf_engine
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for())
    monkeypatch.setattr(dcf_engine, "_generate_valuation_legacy", lambda *a, **k: pytest.fail("Mismatched input reached engine"))
    with pytest.raises(ValueError):
        dcf_engine.generate_valuation("OUTSIDE", prepared_bundle=b, **kwargs)


def test_acquired_statements_have_no_network_fallback():
    import pandas as pd
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for())
    frame = pd.DataFrame([[10.0, float("nan")]], index=["Revenue"], columns=pd.to_datetime(["2025-12-31", "2024-12-31"]))
    b["case"]["sources"]["financials"] = {"status": "ok", "data": {"income_stmt": sector._json_value(frame)}}
    tk = sector.AcquiredTicker(b)
    assert tk.income_stmt.iloc[0, 0] == 10
    assert b["case"]["sources"]["financials"]["data"]["income_stmt"]["missing_cells"] == 1
    with pytest.raises(ValueError, match="non acquisito"):
        _ = tk.balance_sheet


def test_live_profile_cannot_be_backdated_to_a_historical_cutoff():
    providers = sector.default_sector_providers(fetch_info=lambda _: {"quoteType": "EQUITY", "industry": "Software - Application"},
        vehicle_registry={"origine": "fixture", "veicoli": {}, "motivo": None})
    b = sector.prepare_sector_analysis("OUTSIDE", as_of="2000-01-01", providers=providers)
    assert b["case"]["sources"]["profile"]["status"] == "source_error"
    assert b["decision"]["decision_status"] == "source_error"


def test_live_financial_failure_is_not_an_ok_empty_envelope(monkeypatch):
    import yfinance as yf
    from types import SimpleNamespace
    monkeypatch.setattr(yf, "Ticker", lambda _: SimpleNamespace(info={}))
    provider = sector.default_sector_providers()["financials"]
    result = provider("OUTSIDE", as_of=DAY)
    assert result["status"] == "source_error"
    assert set(result["data"]["errors"]) == {"income_stmt", "balance_sheet", "cashflow"}


def test_operating_adapter_rejects_bank_inputs_instead_of_ignoring(monkeypatch):
    from bellomberg.valuation import dcf_engine
    b = sector.prepare_sector_analysis("OUTSIDE", as_of=DAY, providers=providers_for(),
                                      user_context={"assumptions": {"roe_path": [0.1]}})
    assert b["case"]["route"] == "operating"
    monkeypatch.setattr(dcf_engine, "_generate_valuation_legacy", lambda *a, **kw: pytest.fail("Inapplicable inputs"))
    result = dcf_engine.generate_valuation("OUTSIDE", prepared_bundle=b)
    assert "roe_path" in result["error"]
    assert "roe_path" in result["input_consumption"]["unconsumed_fields"]
