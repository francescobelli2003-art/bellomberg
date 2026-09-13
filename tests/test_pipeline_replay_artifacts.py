"""Offline replay through real desk recovery, committee, SQLite, PDF, XLSX and MIME.

Reuses the existing orchestration sandbox and the documented SYNTH-EXT source fixture.
LLM responses, research queue, news provider, risk/market services, reflection and
quant appendix are simulated. This proves local wiring, NOT live model quality,
issuer economics, email delivery or the still-open real_issuer_follow_up research case.
"""
from copy import deepcopy
import asyncio
from email import policy
from email.parser import BytesParser
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from test_cablaggio_consigliere_multi import run_offline, _modulo_finto
from test_desk_recupero_lavoro import _ClientCheRispettaIlContratto, _ToolUseBlock, _StreamFinto, _prepara_capo
from test_tetto_specialisti_16k import _Resp, _TextBlock, _Usage
from test_sector_operating_drivers import bundle_for
from test_valuation_snapshot_persistence import db
from test_core_audit_regressions import _chat, _chunk

from bellomberg.agents import chat_tools, consigliere_multi as cm, capo, red_team, agent_tools, scorekeeper
from bellomberg.agents.specialists import base
from bellomberg.core import llm_client, llm_pricing
from bellomberg.reporting import pdf_institutional, charts_institutional
from bellomberg.storage import memory_db
from bellomberg.valuation import dcf_engine

# Capture real functions before the reused fixture replaces their module bindings.
REAL_ROSTER = tuple(cm.SPECIALIST_ORDER)
REAL_RED_TEAM = red_team.run_red_team
REAL_EXTRACT = memory_db.MemoryDB.extract_and_save_decisions


@pytest.fixture
def replay_loop():
    # Windows asyncio creates an internal loopback socketpair. Initialize that
    # self-pipe before installing the tripwire for the code under test.
    loop = asyncio.new_event_loop()
    yield loop
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.run_until_complete(loop.shutdown_default_executor())
    loop.close()


@pytest.fixture
def replay(run_offline, db, tmp_path, monkeypatch, replay_loop):
    monkeypatch.setenv("BELLOMBERG_DATA_DIR", str(tmp_path))
    attempted_network = []

    def forbidden(*args, **kwargs):
        attempted_network.append(str(args[:1]))
        pytest.fail("Replay attempted a real network connection or legacy valuation")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(dcf_engine, "_generate_valuation_legacy", forbidden)
    monkeypatch.setattr(dcf_engine, "REPORT_DIR", tmp_path / "report")
    monkeypatch.setattr(chat_tools, "REPORT_DIR", tmp_path / "report")
    monkeypatch.setattr(cm, "MemoryDB", lambda: db)
    monkeypatch.setattr(memory_db, "MemoryDB", lambda: db)
    monkeypatch.setattr(db, "extract_and_save_decisions", REAL_EXTRACT.__get__(db))
    # Snapshot-only DB fixture skips Chroma initialization; committee needs its
    # explicit unavailable state, while SQLite remains the real implementation.
    db.col_memos = db.col_decisions = db.col_feedback = None
    monkeypatch.setattr(scorekeeper, "get_track_record_for_specialist", lambda *a, **k: "")
    monkeypatch.setattr(scorekeeper, "get_track_record_for_capo", lambda *a, **k: "")
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (.9, "synthetic replay FX"))
    llm_pricing.reset_fx_memo()
    state = SimpleNamespace(db=db, clients={}, news=[], prepared=[], capo_calls=[],
                            side_calls=[], network=attempted_network, smtp=run_offline.inviati, loop=replay_loop)

    def research_block(*, sector_bundles, decision_links):
        # Actual prepare_sector_analysis via the shared fixture, on explicit synthetic
        # provider records. Only the research queue that requests it is simulated.
        bundle = bundle_for(profile="manufacturing")
        sector_bundles["SYNTH-EXT"] = bundle
        state.prepared.append(bundle["snapshot_id"])
        return "SYNTHETIC RESEARCH QUEUE: evaluate SYNTH-EXT from the prepared documented records."

    facts = _modulo_finto(monkeypatch, "bellomberg.core.current_facts",
        current_facts_block=lambda: "SYNTHETIC REPLAY: empty portfolio; no live facts.",
        favorites_block=lambda: "", pm_theses_block=lambda: "", research_block=research_block)
    import bellomberg.core as core_package
    monkeypatch.setattr(core_package, "current_facts", facts, raising=False)

    def news(query, max_results=10):
        state.news.append(query)
        return {"source": "synthetic replay", "query": query, "count": 1,
                "articles": [{"title": "SYNTHETIC disclosed operating inputs",
                              "url": "https://example.org/synthetic/dated-case"}]}

    monkeypatch.setattr(agent_tools, "tool_search_news", news)

    def replay_class(real_class):
        class ReplayDesk(real_class):
            # The actual class/prompt and Specialist.run are inherited unchanged.
            def __init__(self, blackboard):
                state.blackboard = blackboard
                round_n = blackboard.current_round
                recover = self.name == "fundamentals" and round_n == 1
                valuation = self.name == "fundamentals" and round_n >= 1

                def script(n, kwargs):
                    if recover and n == 1:
                        return _Resp("max_tokens", [], _Usage(out=16000))
                    step = n - int(recover)
                    if step == 1:
                        return _Resp("tool_use", [_ToolUseBlock("search_news", {"query": "SYNTH-EXT synthetic replay"})], _Usage())
                    if valuation and step == 2:
                        return _Resp("tool_use", [_ToolUseBlock("get_valuation", {"ticker": "SYNTH-EXT"})], _Usage())
                    return _Resp("end_turn", [_TextBlock(
                        "SYNTHETIC REPLAY REPORT " + self.name + " R" + str(round_n) + "\n"
                        + "Dati sintetici dai tool; nessuna conclusione su emittenti reali. " * 20)], _Usage())

                client = _ClientCheRispettaIlContratto(script)
                state.clients[(self.name, round_n)] = client
                super().__init__(blackboard, client=client)
        return ReplayDesk

    state.roster = [replay_class(cls) for cls in REAL_ROSTER]
    monkeypatch.setattr(cm, "SPECIALIST_ORDER", state.roster)
    # Reuse the existing Capo's offline context boundaries, replace only responses.
    _prepara_capo(monkeypatch)
    memo = ("## BLUF\nSYNTHETIC REPLAY: prova locale, nessuna run LLM dal vivo.\n\n"
            "## VALUTAZIONE\nSYNTH-EXT usa esclusivamente assunzioni sintetiche. "
            "Fair value 14.13 EUR [src: get_valuation].\n\n"
            + "La prova verifica trasporto e persistenza degli artefatti sintetici. " * 80
            + "\n\n## ACTION TABLE\n| Action | Ticker | Size EUR | Timing | Confidence |\n"
            "|---|---|---|---|---|\n| WATCH | SYNTH-EXT | 0 | Replay only | LOW |\n")
    state.memo = memo

    class CapoClient:
        def __init__(self, **kwargs): self.messages = self
        def stream(self, **kwargs):
            state.capo_calls.append(deepcopy(kwargs))
            return _StreamFinto(_Resp("end_turn", [_TextBlock(memo)], _Usage()))

    monkeypatch.setattr(capo, "OpenRouterClient", CapoClient)
    monkeypatch.setattr(cm, "run_capo", capo.run_capo)
    monkeypatch.setattr(red_team, "run_red_team", REAL_RED_TEAM)

    class SideClient:
        def __init__(self, **kwargs): self.messages = self
        def create(self, **kwargs):
            state.side_calls.append(deepcopy(kwargs))
            if kwargs.get("tool_choice", {}).get("name") == "emit_action_table":
                return _Resp("tool_use", [_ToolUseBlock("emit_action_table", {"table_found": True, "rows": [
                    {"action": "WATCH", "ticker": "SYNTH-EXT", "size_raw": "0",
                     "timing": "Replay only", "confidence": "LOW"}]})], _Usage())
            return _Resp("end_turn", [_TextBlock("SYNTHETIC REPLAY CRITIQUE: dati inventati, "
                "nessuna validazione economica degli emittenti. " * 15)], _Usage())

    monkeypatch.setattr(llm_client, "OpenRouterClient", SideClient)
    monkeypatch.setitem(sys.modules, "bellomberg.reporting.pdf_institutional", pdf_institutional)
    monkeypatch.setattr(pdf_institutional, "REPORT_DIR", str(tmp_path / "report"))
    # Real cover chart code may create images even for an empty portfolio.
    monkeypatch.setattr(charts_institutional, "DIR", str(tmp_path / "report" / "charts"))
    yield state
    llm_pricing.reset_fx_memo()


def _assert_recovered(state, blackboard):
    client = state.clients[("fundamentals", 1)]
    assert len(client.calls) == 4
    assert client.calls[1].get("tool_choice") != {"type": "none"}
    assert client.calls[1]["thinking"] == {"type": "disabled"}
    assert client.calls[2]["thinking"] == {"type": "adaptive"}
    assert state.news and state.prepared
    serialized_results = json.dumps(client.messaggi_per_call[-1], default=str)
    assert "SYNTHETIC disclosed operating inputs" in serialized_results
    assert "generation_id" in serialized_results and "14.13" in serialized_results
    tool_text = client.messaggi_per_call[-1][-1]["content"][0]["content"]
    summary = json.loads(next(line for line in tool_text.splitlines() if line.startswith("{")))
    assert summary["information_cutoff"] == "2026-09-10"
    assert summary["valuation_usability"]["usable"] is True
    usage = next(row for row in blackboard.usage_log if row["agent"] == "fundamentals" and row["round"] == 1)
    assert usage["retry_vuoto"] == 1 and usage["api_calls"] == 4
    value = blackboard.valuation_results["SYNTH-EXT"]
    assert value["valuation_usability"]["usable"], value
    assert value["fair_value_base"] == pytest.approx(14.13)
    workbook = Path(value["path"])
    wb = load_workbook(workbook, data_only=True)
    assert wb["Valuation"]["B2"].value == pytest.approx(value["fair_value_base"])
    wb.close()
    sidecar = json.loads(workbook.with_suffix(".payload.json").read_text(encoding="utf-8"))
    assert sidecar["workbook_sha256"] == sha256(workbook.read_bytes()).hexdigest()
    assert not state.network
    return value


def test_recovery_executes_research_prepare_real_valuation_and_workbook(replay, tmp_path, capsys):
    memo_id = replay.db.save_memo("SYNTHETIC recovery test")
    bb = base.Blackboard(memory_db=replay.db, memo_id=memo_id)
    bb.r2_specialists = cm.R2_SPECIALISTS
    bb.current_round = 1
    cls = next(cls for cls in replay.roster if cls.name == "fundamentals")
    cls(bb).run(1)
    value = _assert_recovered(replay, bb)
    assert "RITENTO" in capsys.readouterr().out
    assert replay.db.get_valuation_snapshot(value["snapshot_id"], generation_id=value["generation_id"])
    with replay.db._conn() as conn:
        reports = conn.execute("SELECT content FROM specialist_reports WHERE memo_id=?", (memo_id,)).fetchall()
    assert any("SYNTHETIC REPLAY REPORT fundamentals R1" in row[0] for row in reports)


def test_recovered_desk_sees_blocked_gate_missing_fields_and_cutoff(replay, monkeypatch):
    source = bundle_for(profile="manufacturing")
    incomplete = [row for row in source["case"]["records"] if row["driver"] != "shares"]
    real_bundle_for = bundle_for
    monkeypatch.setattr(sys.modules[__name__], "bundle_for",
                        lambda **kwargs: real_bundle_for(records=incomplete, **kwargs))
    bb = base.Blackboard(memory_db=replay.db)
    bb.current_round = 1
    cls = next(cls for cls in replay.roster if cls.name == "fundamentals")
    cls(bb).run(1)
    client = replay.clients[("fundamentals", 1)]
    tool_text = client.messaggi_per_call[-1][-1]["content"][0]["content"]
    summary = json.loads(next(line for line in tool_text.splitlines() if line.startswith("{")))
    payload = bb.valuation_results["SYNTH-EXT"]
    assert summary["valuation_usability"] == payload["valuation_usability"]
    assert summary["valuation_usability"]["usable"] is False
    assert summary["valuation_usability"]["missing_fields"]
    assert summary["fair_value"] is None
    assert summary["information_cutoff"] == "2026-09-10"
    assert summary["snapshot_id"] == payload["snapshot_id"]
    assert "...[truncated]" in tool_text
    assert not replay.network


def test_full_orchestrator_replay_persists_and_captures_real_pdf_and_excel(replay, tmp_path, capsys):
    cm.run_multi_agent()
    log = capsys.readouterr().out
    assert "RITENTO" in log
    expected = {(cls.name, round_n) for cls in REAL_ROSTER for round_n in (0, 1)}
    expected |= {(name, 2) for name in cm.R2_SPECIALISTS}
    assert set(replay.clients) == expected and len(expected) == 15
    assert len(replay.capo_calls) == 1 and len(replay.side_calls) == 2
    assert "SYNTHETIC REPLAY CRITIQUE" in str(replay.capo_calls[0])
    assert "14.13" in str(replay.capo_calls[0])
    archive = next((tmp_path / "research_notes").glob("*_blackboard.json"))
    archived = json.loads(archive.read_text(encoding="utf-8"))
    assert "get_valuation" in str(archived["tool_log"])
    heartbeat = json.loads(Path(base.Blackboard.HEARTBEAT_PATH).read_text(encoding="utf-8"))
    assert heartbeat["running"] is False
    with replay.db._conn() as conn:
        memo = conn.execute("SELECT id,full_markdown,pdf_path,dcf_files FROM memos").fetchone()
        usage = conn.execute("SELECT agent,api_calls FROM llm_usage WHERE memo_id=?", (memo[0],)).fetchall()
        reports = conn.execute("SELECT specialist,round_n FROM specialist_reports WHERE memo_id=?", (memo[0],)).fetchall()
        decisions = conn.execute("SELECT id,ticker FROM decisions WHERE memo_id=?", (memo[0],)).fetchall()
        links = conn.execute("SELECT decision_id,generation_id FROM valuation_snapshot_links WHERE decision_id IS NOT NULL").fetchall()
    assert memo[1].endswith(replay.memo)
    assert len(reports) >= 6 and len(usage) == 18, (reports, usage)
    assert len(decisions) == 1 and decisions[0][1] == "SYNTH-EXT"
    latest = replay.db.get_latest_valuation_snapshots()["SYNTH-EXT"]["payload"]
    assert (decisions[0][0], latest["generation_id"]) in [tuple(row) for row in links]
    pdf = Path(memo[2])
    assert pdf.read_bytes().startswith(b"%PDF-") and pdf.stat().st_size > 5000
    from pypdf import PdfReader
    document = PdfReader(pdf)
    assert len(document.pages) >= 2
    assert "SYNTHETIC REPLAY" in "".join(page.extract_text() for page in document.pages)
    xlsx = Path(latest["path"])
    assert json.loads(memo[3]) == [str(xlsx)]
    assert len(replay.smtp) == 1
    mime_bytes = replay.smtp[0].as_bytes()
    mime = BytesParser(policy=policy.default).parsebytes(mime_bytes)
    attachments = {part.get_filename(): part.get_payload(decode=True) for part in mime.iter_attachments()}
    assert attachments == {pdf.name: pdf.read_bytes(), xlsx.name: xlsx.read_bytes()}
    assert "14.13" in mime.get_body(preferencelist=("html",)).get_content()
    assert not replay.network
    _assert_recovered(replay, replay.blackboard)
    (tmp_path / "synthetic-committee.eml").write_bytes(mime_bytes)
    (tmp_path / "synthetic-committee.log").write_text(log, encoding="utf-8")
    (tmp_path / "synthetic-committee-receipt.json").write_text(json.dumps({
        "scope": "Full orchestrator offline replay; synthetic LLM/provider responses; no live run or delivery",
        "desk_rounds": len(replay.clients), "capo_calls": len(replay.capo_calls),
        "red_team_and_extraction_calls": len(replay.side_calls), "usage_rows": len(usage),
        "persisted_reports": len(reports), "memo_id": memo[0], "decision_id": decisions[0][0],
        "snapshot_id": latest["snapshot_id"], "generation_id": latest["generation_id"],
        "pdf": pdf.name, "workbook": xlsx.name,
        "attachment_sha256": {name: sha256(content).hexdigest() for name, content in attachments.items()},
        "smtp_captured": len(replay.smtp), "network_attempts": len(replay.network),
        "simulated_boundaries": ["LLM responses", "research queue", "news provider", "empty portfolio",
            "risk/market services", "reflection", "quant appendix", "SMTP"],
        "real_issuer_follow_up": "APERTA: this synthetic replay does not resolve the real issuer case",
    }, indent=2), encoding="utf-8")


@pytest.mark.parametrize("usable", [True, False])
def test_chat_stream_preserves_valuation_gate_cutoff_and_fv_before_truncation(replay, monkeypatch, usable):
    from bellomberg.agents import chat_engine
    source = bundle_for(profile="manufacturing")
    if not usable:
        source = bundle_for(profile="manufacturing", records=[
            row for row in source["case"]["records"] if row["driver"] != "shares"])
    first = [_chunk({"tool_calls": [{"index": 0, "id": "v", "function": {
        "name": "get_valuation", "arguments": '{"ticker":"SYNTH-EXT"}'}}]}, finish="tool_calls")]
    run, _, _, requests = _chat(monkeypatch, [first, [_chunk({"content": "Synthetic response"}, finish="stop")]])
    results = []

    def dispatch(name, args, **kwargs):
        result = chat_tools.dispatch(name, args, prepared_bundle=source, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(chat_engine, "dispatch", dispatch)
    monkeypatch.setattr(chat_engine, "get_tools_for_agent", lambda _: chat_tools.get_tools_for_agent("fundamentals"))
    events = replay.loop.run_until_complete(run())
    assert len(requests) == 2 and len(results) == 1
    content = next(message["content"] for message in requests[1]["messages"] if message["role"] == "tool")
    summary = json.loads(next(line for line in content.splitlines() if line.startswith("{")))
    value = results[0]["data"]
    assert summary["valuation_usability"] == value["valuation_usability"]
    assert summary["valuation_usability"]["usable"] is usable
    assert summary["fair_value"] == (pytest.approx(14.13) if usable else None)
    assert summary["information_cutoff"] == "2026-09-10"
    assert summary["snapshot_id"] == value["snapshot_id"]
    assert len(content) <= 12000
    if not usable:
        assert summary["valuation_usability"]["missing_fields"]
    sse = next(json.loads(event.split("data: ", 1)[1]) for event in events if event.startswith("event: tool_result"))
    original = json.dumps(results[0], ensure_ascii=False, default=str)
    assert sse["result_size_bytes"] == len(original)
    if usable:
        assert sse["result_preview"] == original[:300] + "..."
    else:
        assert '"error": "FV n.d.' in sse["result_preview"]
        assert "shares: model: input documentato mancante" in sse["result_preview"]
    assert not replay.network
