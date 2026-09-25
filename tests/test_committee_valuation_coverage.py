"""Deterministic portfolio coverage with real tool/storage and synthetic sources."""
from copy import deepcopy
import json
import socket

import pytest

from bellomberg.agents import chat_tools, consigliere_multi as cm
from bellomberg.agents.specialists.base import Blackboard, Specialist
from bellomberg.storage import memory_db
from bellomberg.valuation import preparation_runtime, sector_analysis
from test_input_preparation import _bundle
from test_preparation_runtime import _policy
from test_sector_operating_drivers import bundle_for


@pytest.fixture
def coverage_env(monkeypatch, tmp_path):
    def forbidden(*a, **k):
        pytest.fail("Coverage attempted a real network connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(tmp_path / "coverage.db"))
    monkeypatch.setattr(chat_tools, "REPORT_DIR", tmp_path)
    from bellomberg.valuation import dcf_engine
    monkeypatch.setattr(dcf_engine, "REPORT_DIR", tmp_path)
    from bellomberg.storage.valuation_versions import ensure_schema
    db = memory_db.MemoryDB()
    with db._conn() as conn:
        ensure_schema(conn)
    bb = Blackboard(memory_db=db)
    bb.current_round = 1
    bb.data["_valuation_preparation"] = {"status": "disabled", "reason": "configuration_absent"}
    return bb


def _portfolio(*tickers):
    return {"n_positions": len(tickers), "positions": [{"ticker": t} for t in tickers]}


@pytest.mark.parametrize("state", ["absent", "disabled", "invalid", "trigger", "ticker"])
def test_missing_authorization_never_invokes_ai_and_delivers_reason(coverage_env, monkeypatch, tmp_path, state):
    bb = coverage_env
    policy = tmp_path / "policy.json"
    configs = {"disabled": {"version": 1, "enabled": False}, "invalid": {"enabled": True},
               "trigger": _policy(triggers=["portfolio"]), "ticker": _policy(tickers=["OTHER"])}
    if state in configs:
        policy.write_text(json.dumps(configs[state]), encoding="utf-8")
    def forbidden(*a, **k):
        pytest.fail("Unapproved AI preparation")
    runtime = preparation_runtime.PreparationRuntime(policy, data_root=tmp_path,
        archive_root=tmp_path / "sources", output_dir=tmp_path, proposer_factory=lambda *a, **k: forbidden)
    monkeypatch.setattr(preparation_runtime, "installation_runtime", lambda: runtime)
    binding = preparation_runtime.bind_installation_preparer("committee")
    bb.valuation_preparer = binding["preparer"]
    bb.data["_valuation_preparation"] = binding["state"]
    bundle = _bundle()
    monkeypatch.setattr(sector_analysis, "prepare_sector_analysis", lambda *a, **k: deepcopy(bundle))
    cm._ensure_portfolio_valuations(bb, _portfolio("SYNTH-EXT", "SYNTH-EXT"))
    from bellomberg.reporting.valuation_delivery import build_manifest
    from bellomberg.reporting.email_sender import corpo_valutazioni
    result = bb.valuation_results["SYNTH-EXT"]
    reason = "ticker_not_authorized" if state == "ticker" else binding["state"]["reason"]
    assert result["preparation"] == {"status": "disabled", "reason": reason}
    assert result["valuation_usability"]["usable"] is False
    assert reason in result["error"]
    receipt = build_manifest(bb.valuation_results, roots=[tmp_path], attempts=bb.valuation_attempts)
    assert receipt["attachments"] == [] and len(receipt["attempts"]) == 1
    assert reason in corpo_valutazioni(bb.valuation_results, [], delivery=receipt)
    assert not (tmp_path / "valuation_ai_budgets").exists()


def test_previous_success_failure_and_attempt_only_are_not_retried(coverage_env, monkeypatch):
    bb = coverage_env
    bb.record_valuation("DONE", {"ok": True}, "fundamentals")
    bb.record_valuation("FAILED", {"ok": False, "error": "budget exhausted"}, "fundamentals")
    bb.valuation_attempts.append({"ticker": "ATTEMPTED"})
    before = deepcopy(bb.valuation_attempts)
    monkeypatch.setattr(chat_tools, "dispatch", lambda *a, **k: pytest.fail("Implicit retry"))
    cm._ensure_portfolio_valuations(bb, _portfolio("DONE", "FAILED", "ATTEMPTED", "DONE"))
    assert bb.valuation_attempts == before
    assert bb.data["_valuation_coverage"]["already_requested"] == ["DONE", "FAILED", "ATTEMPTED"]
    assert bb.tool_log == []


def test_dispatch_exception_is_recorded_and_does_not_skip_next_holding(coverage_env, monkeypatch):
    bb = coverage_env
    original = chat_tools.dispatch
    bundle = _bundle()
    def dispatch(name, arguments, **kwargs):
        if arguments["ticker"] == "BROKEN":
            raise RuntimeError("Synthetic acquisition failure")
        return original(name, arguments, **kwargs)
    monkeypatch.setattr(chat_tools, "dispatch", dispatch)
    monkeypatch.setattr(sector_analysis, "prepare_sector_analysis", lambda *a, **k: deepcopy(bundle))
    cm._ensure_portfolio_valuations(bb, _portfolio("BROKEN", "SYNTH-EXT"))
    assert "Synthetic acquisition failure" in bb.valuation_results["BROKEN"]["error"]
    assert bb.valuation_results["BROKEN"]["exclude_from_action_table"] is True
    assert bb.valuation_results["SYNTH-EXT"]["acquisition_snapshot"]["snapshot_id"] == bundle["snapshot_id"]
    assert len(bb.valuation_attempts) == len(bb.tool_log) == 2


def test_existing_verified_workbook_is_reused_without_preparer(coverage_env, monkeypatch, tmp_path):
    bb = coverage_env
    bundle = bundle_for()
    monkeypatch.setattr(sector_analysis, "prepare_sector_analysis", lambda *a, **k: deepcopy(bundle))
    first = chat_tools.dispatch("get_valuation", {"ticker": "SYNTH-EXT"}, prepared_bundle=bundle)["data"]
    assert first["valuation_usability"]["usable"], first.get("error")
    from pathlib import Path
    original_bytes = Path(first["path"]).read_bytes()
    cm._ensure_portfolio_valuations(bb, _portfolio("SYNTH-EXT"))
    result = bb.valuation_results["SYNTH-EXT"]
    assert result["reused"] is True and result["generation_id"] == first["generation_id"]
    assert Path(result["path"]).read_bytes() == original_bytes
    from bellomberg.reporting.valuation_delivery import build_manifest
    assert build_manifest(bb.valuation_results, roots=[tmp_path])["attachments"] == [result["path"]]


@pytest.mark.parametrize("empty_options", [{}, {"analysis_context": {}}, {"method_records": None},
                                         {"growth_path": [], "variant_view": ""}])
def test_bare_r2_reuses_automatic_failure_but_explicit_revision_dispatches(coverage_env, monkeypatch, empty_options):
    bb = coverage_env
    original = {"ok": False, "error": "Source incomplete", "request_origin": "committee-orchestrator"}
    bb.record_valuation("SYNTH-EXT", original, "committee-orchestrator")
    calls = []
    def revised(name, arguments, **kwargs):
        calls.append((name, arguments))
        return {"data": {"ok": False, "error": "Explicit revised result"}}
    monkeypatch.setattr(chat_tools, "dispatch", revised)
    bb.current_round = 2
    desk = Specialist(bb, client=object())
    reused = desk._execute_meta_tool("get_valuation", {"ticker": "SYNTH-EXT", **empty_options})["data"]
    assert reused["reused_in_run"] is True and reused["error"] == "Source incomplete"
    assert calls == []
    changed = desk._execute_meta_tool("get_valuation", {"ticker": "SYNTH-EXT", "method_records": []})["data"]
    assert changed["error"] == "Explicit revised result" and len(calls) == 1
    assert "request_origin" not in changed


def test_exact_failure_and_preparation_reason_reach_committee_context(coverage_env):
    bb = coverage_env
    bb.record_valuation("SYNTH-EXT", {"ok": False, "error": "Synthetic source refusal",
        "preparation": {"status": "disabled", "reason": "ticker_not_authorized"}}, "committee-orchestrator")
    shared = sector_analysis.valuation_results_block(bb.valuation_results)
    r2 = Specialist(bb, client=object())._build_round_context(2)
    for block in (shared, r2):
        assert "Synthetic source refusal" in block and "ticker_not_authorized" in block


def test_empty_portfolio_never_selects_authorized_or_watchlist_tickers(coverage_env, monkeypatch):
    bb = coverage_env
    bb.data["_valuation_preparation"] = {"status": "enabled", "tickers": ["AUTHORIZED"]}
    monkeypatch.setattr(chat_tools, "dispatch", lambda *a, **k: pytest.fail("No DB holding"))
    cm._ensure_portfolio_valuations(bb, _portfolio())
    assert not bb.valuation_results and not bb.valuation_attempts
    assert bb.data["_valuation_coverage"]["reason"] == "portfolio_empty_or_unavailable"


@pytest.mark.parametrize("malformed", [{"valuation_usability": "broken"},
                                      {"valuation_usability": {"usable": "false"}}, []])
def test_invalid_tool_shape_is_a_declared_failure_not_a_run_abort(coverage_env, monkeypatch, malformed):
    bb = coverage_env
    def dispatch(name, arguments, **kwargs):
        return {"data": malformed if arguments["ticker"] == "BROKEN" else {"error": "Source unavailable"}}
    monkeypatch.setattr(chat_tools, "dispatch", dispatch)
    cm._ensure_portfolio_valuations(bb, _portfolio("BROKEN", "NEXT"))
    assert "get_valuation returned invalid" in bb.valuation_results["BROKEN"]["error"]
    assert bb.valuation_results["BROKEN"]["exclude_from_action_table"] is True
    assert "Source unavailable" in bb.valuation_results["NEXT"]["error"]


def test_malformed_preparation_is_explicit_in_shared_context():
    block = sector_analysis.valuation_results_block({"SYNTH-EXT": {"preparation": "broken"}})
    assert "invalid_preparation_metadata" in block
