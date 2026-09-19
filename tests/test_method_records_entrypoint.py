"""Archived rationale enriches a new acquisition without changing a frozen bundle.

Synthetic records and a temporary SQLite archive only; no live data or API calls.
"""
from contextlib import closing
from copy import deepcopy

import pytest

from bellomberg.storage import memory_db, method_records_store
from bellomberg.valuation import dcf_engine, sector_analysis
from bellomberg.valuation.method_records_archive import method_inputs
from bellomberg.valuation.method_registry import get_method_requirements
from test_sector_operating_drivers import DAY, operating_records


TICKER = "SYNTH-EXT"
RATIONALE = {scenario: "Synthetic archived scenario " + scenario
             for scenario in ("bear", "base", "bull")}


@pytest.fixture
def approved_providers(tmp_path, monkeypatch):
    path = str(tmp_path / "method-records-entrypoint.db")
    monkeypatch.setattr(memory_db, "SQLITE_PATH", path)
    with closing(memory_db.connect_sqlite(path)) as conn:
        method_records_store.crea_tabelle(conn)
        conn.commit()
        set_id = method_records_store.proponi_set(
            conn, ticker=TICKER, method_id="operating_fcff",
            method_version=get_method_requirements("operating_fcff")["method_version"],
            records=operating_records(), scenario_rationale=RATIONALE,
            provenance="Synthetic documented fixture", prepared_by="Synthetic preparer",
            prepared_at=DAY)
        method_records_store.rivedi_set(
            conn, set_id=set_id, decision="approvato", reviewer="Synthetic PM", reviewed_at=DAY)

    def profile(*args, **kwargs):
        return {"status": "ok", "source_id": "synthetic", "as_of": DAY,
                "data": {"info": {}, "evidence": [
                    {"field": field, "value": value, "source_id": "synthetic", "as_of": DAY}
                    for field, value in (("instrument", "equity"), ("business_model", "manufacturing"))]}}

    return {"profile": profile, "method_inputs": method_inputs}


@pytest.mark.parametrize("kwargs", [
    pytest.param({}, id="omitted"),
    pytest.param({"analysis_context": {}}, id="empty"),
    pytest.param({"analysis_context": {"revisions": []}}, id="revisions-only"),
    pytest.param({"analysis_context": {"scenario_rationale": RATIONALE}}, id="full"),
])
def test_new_acquisition_accepts_context_completed_from_archive(approved_providers, tmp_path, kwargs):
    before = deepcopy(kwargs)
    result = dcf_engine.generate_valuation(
        TICKER, providers=approved_providers, as_of=DAY, output_dir=str(tmp_path), **kwargs)
    assert result["valuation_usability"]["usable"], result["valuation_usability"]
    assert result["fair_value_base"] == pytest.approx(14.13)
    snapshot = result["acquisition_snapshot"]
    assert snapshot["analysis_context"]["scenario_rationale"] == RATIONALE
    expected_origin = ("analysis_context" if "scenario_rationale" in kwargs.get("analysis_context", {})
                       else "archivio approvato (set 1)")
    assert snapshot["case"]["scenario_rationale_origin"] == expected_origin
    assert snapshot["analysis_context"].get("revisions") == kwargs.get("analysis_context", {}).get("revisions")
    assert kwargs == before


@pytest.mark.parametrize("context", [[], "", 0, False], ids=["list", "text", "number", "boolean"])
def test_invalid_context_is_not_replaced_by_approved_rationale(approved_providers, tmp_path, context):
    with pytest.raises(ValueError, match="analysis_context"):
        dcf_engine.generate_valuation(
            TICKER, providers=approved_providers, as_of=DAY,
            analysis_context=context, output_dir=str(tmp_path))


@pytest.mark.parametrize("context", [
    pytest.param({}, id="empty"),
    pytest.param({"revisions": []}, id="revisions-only"),
    pytest.param({"scenario_rationale": {s: "Synthetic different rationale " + s for s in RATIONALE}},
                 id="different-rationale"),
])
def test_frozen_bundle_rejects_implicit_context_change(approved_providers, tmp_path, context):
    bundle = sector_analysis.prepare_sector_analysis(TICKER, as_of=DAY, providers=approved_providers)
    before = deepcopy(bundle)
    with pytest.raises(ValueError, match="analysis_context diverso dal bundle"):
        dcf_engine.generate_valuation(
            TICKER, prepared_bundle=bundle, analysis_context=context, output_dir=str(tmp_path))
    assert bundle == before


def test_frozen_bundle_accepts_identical_context_without_mutation(approved_providers, tmp_path):
    bundle = sector_analysis.prepare_sector_analysis(TICKER, as_of=DAY, providers=approved_providers)
    before = deepcopy(bundle)
    result = dcf_engine.generate_valuation(
        TICKER, prepared_bundle=bundle, analysis_context=deepcopy(bundle["analysis_context"]),
        output_dir=str(tmp_path))
    assert result["valuation_usability"]["usable"], result["valuation_usability"]
    assert result["snapshot_id"] == before["snapshot_id"]
    assert bundle == before


@pytest.mark.parametrize("frozen", [False, True], ids=["new-acquisition", "frozen-bundle"])
def test_explicit_records_without_rationale_do_not_inherit_approved_one(approved_providers, tmp_path, frozen):
    bundle = (sector_analysis.prepare_sector_analysis(TICKER, as_of=DAY, providers=approved_providers)
              if frozen else None)
    before = deepcopy(bundle)
    result = dcf_engine.generate_valuation(
        TICKER, prepared_bundle=bundle, providers=approved_providers, as_of=DAY,
        method_records=operating_records(), analysis_context={}, output_dir=str(tmp_path))
    snapshot = result["acquisition_snapshot"]
    assert snapshot["case"]["sources"]["method_inputs"]["source_id"] == "explicit_method_records"
    assert snapshot["case"]["scenario_rationale_origin"] == "assente"
    assert "scenario_rationale" not in snapshot["analysis_context"]
    assert result["valuation_usability"]["usable"] is False
    assert result.get("fair_value_base") is None
    assert bundle == before
