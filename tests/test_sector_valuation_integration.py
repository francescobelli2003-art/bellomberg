"""Same acquired case at public tool and cache boundaries, with synthetic data only."""
from copy import deepcopy
from hashlib import sha256
import json
import socket
import sys
from types import SimpleNamespace

import pytest

from bellomberg.valuation.method_registry import get_method_requirements, select_valuation_method
from bellomberg.valuation.sector_analysis import prepare_sector_analysis, revise_sector_analysis
from bellomberg.valuation.valuation_profile import resolve_valuation_profile


DAY = "2026-09-10"
SYMBOL = "SYNTH_OUTSIDE_PORTFOLIO"


def synthetic_bundle(*, model="software", day=DAY, complete=True, context=None):
    """These records exercise acquisition contracts, not economic-source verification."""
    evidence = [{"field": key, "value": value, "source_id": "synthetic-issuer", "as_of": day}
                for key, value in (("instrument", "equity"), ("business_model", model))]
    decision = select_valuation_method(resolve_valuation_profile(
        SYMBOL, evidence=evidence, vehicle_registry=None, as_of=day))
    records = [{"field": r["field"], "entity": SYMBOL, "period": "FY2025", "unit": "synthetic-unit",
                "accounting_basis": "synthetic-basis", "source_id": "synthetic-issuer", "as_of": day,
                "value": {"synthetic_record": True}}
               for r in get_method_requirements(decision["method_id"])["fields"]]
    providers = {"profile": lambda *args, **kwargs: {
        "status": "ok", "source_id": "synthetic-profile", "as_of": day,
        "data": {"info": {"currency": "USD"}, "evidence": evidence, "vehicle_registry": None}}}
    if complete:
        providers["method_inputs"] = lambda *args, **kwargs: {
            "status": "ok", "source_id": "synthetic-method-inputs", "as_of": day,
            "data": {"synthetic": True}, "records": records}
    return prepare_sector_analysis(SYMBOL, as_of=day, providers=providers,
                                   user_context={"analysis_context": context or {}})


def documented_payload(bundle, path, *, generation="synthetic-generation"):
    """An explicitly synthetic future adapter result; real S2 legacy remains draft."""
    method = bundle["decision"]["method_id"]
    return {"ok": True, "ticker": SYMBOL, "engine": bundle["case"]["route"], "path": str(path),
            "price": 100.0, "fair_value_weighted": 120.0, "upside_pct": 20.0,
            "valuation_decision": deepcopy(bundle["decision"]), "snapshot_id": bundle["snapshot_id"],
            "generation_id": generation, "acquisition_snapshot": deepcopy(bundle),
            "workbook_sha256": sha256(path.read_bytes()).hexdigest(),
            "analytical_quality": {"status": "DOCUMENTATA", "method_id": method, "issues": [],
                                   "snapshot": {"as_of": bundle["case"]["as_of"]}},
            "sanity": {"severity": "OK", "status": "ok", "method_id": method},
            "input_consumption": {"status": "complete", "unconsumed_fields": [],
                                  "consumed_fields": [r["field"] for r in bundle["case"]["records"]],
                                  "consumed_records": [{"record_index":i, **{key:r.get(key) for key in
                                      ("field","scenario","driver","entity","period","source_id")}}
                                      for i,r in enumerate(bundle["case"]["records"])]}}


@pytest.fixture
def isolated_tools(monkeypatch, tmp_path):
    from bellomberg.agents import agent_tools, chat_tools
    from bellomberg.valuation import dcf_engine
    import yfinance

    def forbidden(*args, **kwargs):
        pytest.fail("Sector integration attempted a real provider/network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(yfinance, "Ticker", forbidden)
    monkeypatch.setattr(agent_tools, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(chat_tools, "REPORT_DIR", tmp_path)
    memory = SimpleNamespace(history=[], saved=[], reads=0)

    def history(ticker, n=1):
        memory.reads += 1
        return deepcopy(memory.history)

    def save(**kwargs):
        memory.saved.append(deepcopy(kwargs))
        return len(memory.saved)

    monkeypatch.setitem(sys.modules, "bellomberg.storage.memory_db", SimpleNamespace(MemoryDB=lambda:
        SimpleNamespace(get_valuation_history=history, save_valuation_thesis=save)))
    return SimpleNamespace(chat=chat_tools, build=agent_tools.tool_build_dcf_model,
                           engine=dcf_engine, memory=memory, directory=tmp_path)


@pytest.mark.parametrize("model", ["software", "bank", "cef"])
def test_same_prepared_bundle_reaches_chat_and_build_with_identical_draft_contract(isolated_tools, monkeypatch, model):
    tools = isolated_tools
    bundle = synthetic_bundle(model=model, complete=False)
    before = deepcopy(bundle)

    def legacy(ticker, *, sector_metadata, **kwargs):
        return {"ok": True, "ticker": ticker, "path": str(tools.directory / "draft.xlsx"),
                "engine": bundle["case"]["route"], "fair_value_weighted": 120.0,
                "analytical_quality": {"status": "INCOMPLETA", "method_id": bundle["decision"]["method_id"],
                                       "issues": ["Synthetic missing source"]},
                "sanity": {"severity": "OK", "method_id": bundle["decision"]["method_id"]}, **sector_metadata}

    monkeypatch.setattr(tools.engine, "_generate_valuation_legacy", legacy)
    chat = tools.chat.dispatch("get_valuation", {"ticker": SYMBOL}, prepared_bundle=bundle)["data"]
    build = tools.build(SYMBOL, prepared_bundle=bundle, as_of=DAY)
    for key in ("valuation_decision", "snapshot_id", "analytical_quality", "input_consumption", "fair_value_weighted"):
        assert chat[key] == build[key], key
    assert chat["valuation_decision"]["evidence"] == bundle["decision"]["evidence"]
    assert chat["valuation_usability"]["usable"] is build["valuation_usability"]["usable"] is False
    assert chat["fair_value_weighted"] is None
    assert tools.memory.saved[0]["fair_value"] is None
    assert tools.memory.saved[0]["valuation_payload"]["snapshot_id"] == bundle["snapshot_id"]
    assert bundle == before


def test_acquired_records_are_not_treated_as_consumed_legacy_inputs(isolated_tools, monkeypatch):
    tools = isolated_tools
    bundle = synthetic_bundle()
    monkeypatch.setattr(tools.engine, "_generate_valuation_legacy", lambda *args, **kwargs:
                        pytest.fail("Legacy adapter must not claim it consumed new method records"))
    chat = tools.chat.dispatch("get_valuation", {"ticker": SYMBOL}, prepared_bundle=bundle)["data"]
    build = tools.build(SYMBOL, prepared_bundle=bundle, as_of=DAY)
    for result in (chat, build):
        assert result["valuation_decision"]["requirements_status"] == "complete"
        assert result["input_consumption"]["status"] == "incomplete"
        assert result["valuation_usability"]["usable"] is False
        assert result["snapshot_id"] == bundle["snapshot_id"]
        assert not result.get("fair_value_weighted")


def test_build_uses_cutoff_of_prepared_historical_bundle(isolated_tools, monkeypatch):
    tools = isolated_tools
    bundle = synthetic_bundle(day="2024-04-17")
    path = tools.directory / "synthetic.xlsx"
    path.write_bytes(b"synthetic oracle workbook bytes")
    payload = documented_payload(bundle, path)
    monkeypatch.setattr(tools.engine, "generate_valuation", lambda *args, **kwargs: deepcopy(payload))
    chat = tools.chat.dispatch("get_valuation", {"ticker": SYMBOL}, prepared_bundle=bundle)["data"]
    build = tools.build(SYMBOL, prepared_bundle=bundle)
    assert chat["valuation_usability"]["usable"] is True
    assert build["valuation_usability"] == chat["valuation_usability"]
    assert build["fair_value_weighted"] == chat["fair_value_weighted"] == 120.0


def test_exposure_result_preserves_identity_and_missing_fields_on_both_tools(isolated_tools):
    tools = isolated_tools
    bundle = synthetic_bundle(model="etf", complete=False)
    chat = tools.chat.dispatch("get_valuation", {"ticker": SYMBOL}, prepared_bundle=bundle)["data"]
    build = tools.build(SYMBOL, prepared_bundle=bundle, as_of=DAY)
    assert build["engine"] == chat["engine"] == "etf_passive"
    for key in ("valuation_decision", "snapshot_id", "valuation_usability"):
        assert build[key] == chat[key]
    assert build["valuation_decision"]["missing_fields"]


def seed_cache(tools, bundle):
    path = tools.directory / "VAL_SYNTH.xlsx"
    path.write_bytes(b"synthetic cached workbook bytes")
    payload = documented_payload(bundle, path)
    sidecar = path.with_suffix(".payload.json")
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    tools.memory.history = [{"valuation_payload": deepcopy(payload), "generation_id": payload["generation_id"]}]
    return payload, path, sidecar


def test_current_cache_requires_same_bundle_generation_sidecar_and_workbook(isolated_tools, monkeypatch):
    tools = isolated_tools
    bundle = synthetic_bundle()
    payload, path, sidecar = seed_cache(tools, bundle)
    monkeypatch.setattr(tools.engine, "generate_valuation", lambda *args, **kwargs:
                        pytest.fail("A verified current cache must be reused"))
    result = tools.chat.dispatch("get_valuation", {"ticker": SYMBOL}, prepared_bundle=bundle)["data"]
    assert result["reused"] is True
    assert result["fair_value_weighted"] == payload["fair_value_weighted"] == 120
    assert result["valuation_usability"]["usable"] is True
    assert result["snapshot_id"] == bundle["snapshot_id"]
    assert tools.memory.saved == []


@pytest.mark.parametrize("change", ["method", "cutoff", "acquisition", "assumptions", "legacy",
                                   "snapshot", "generation", "workbook", "quality", "missing_sidecar"])
def test_cache_changes_force_new_result_and_never_return_old_number(isolated_tools, monkeypatch, change):
    tools = isolated_tools
    bundle = synthetic_bundle()
    old, path, sidecar = seed_cache(tools, bundle)
    tool_input = {"ticker": SYMBOL}
    if change == "method":
        bundle = synthetic_bundle(model="bank")
    elif change == "cutoff":
        bundle = synthetic_bundle(day="2026-09-11")
    elif change == "acquisition":
        bundle = revise_sector_analysis(bundle, analysis_context={"new_evidence": "synthetic update"})
    elif change == "assumptions":
        tool_input["variant_view"] = "new synthetic analyst view"
    elif change == "legacy":
        tools.memory.history = [{"fair_value": 999, "sanity_severity": "OK"}]
    elif change in ("snapshot", "generation", "quality"):
        changed = deepcopy(old)
        if change == "quality":
            changed["analytical_quality"]["status"] = "INCOMPLETA"
        else:
            changed[change + "_id"] = "different-sidecar"
        sidecar.write_text(json.dumps(changed), encoding="utf-8")
    elif change == "workbook":
        path.write_bytes(b"workbook manually changed after the sidecar was written")
    else:
        sidecar.rename(sidecar.with_suffix(".preserved"))

    def generate(ticker, *, prepared_bundle, **kwargs):
        return {"ok": False, "ticker": ticker, "error": "new generation requires data",
                "valuation_decision": deepcopy(prepared_bundle["decision"]),
                "snapshot_id": prepared_bundle["snapshot_id"], "generation_id": "new-output",
                "fair_value_weighted": 999, "analytical_quality": {"status": "INCOMPLETA", "issues": []}}

    monkeypatch.setattr(tools.engine, "generate_valuation", generate)
    result = tools.chat.dispatch("get_valuation", tool_input, prepared_bundle=bundle)["data"]
    assert not result.get("reused")
    assert result["generation_id"] == "new-output"
    assert result["fair_value_weighted"] is None
    assert result["valuation_usability"]["usable"] is False
    assert result["cache_note"].split(":")[-1].strip()
    assert tools.memory.saved[0]["fair_value"] is None
    if change == "assumptions":
        assert tools.memory.reads == 0
        assert result["snapshot_id"] != old["snapshot_id"]
        assert result["valuation_decision"]["acquisition_fingerprint"] != old["valuation_decision"]["acquisition_fingerprint"]
    if change == "workbook":
        assert "hash" in result["cache_note"].lower()
