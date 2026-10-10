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
from bellomberg.agents import action_validator as REAL_ACTION_VALIDATOR
from bellomberg.agents.specialists import base
from bellomberg.core import llm_client, llm_pricing
from bellomberg.core import current_facts as REAL_CURRENT_FACTS
from bellomberg.reporting import pdf_institutional, charts_institutional
from bellomberg.storage import memory_db
from bellomberg.valuation import dcf_engine, preparation_ai

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
    monkeypatch.setenv("BELLOMBERG_REPORT_DIR", str(tmp_path / "report"))
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
    # run_offline replaces this module with shallow no-op stubs; this replay must
    # exercise the actual local sanity reader, risk assessment and hard apply.
    monkeypatch.setitem(sys.modules, "bellomberg.agents.action_validator", REAL_ACTION_VALIDATOR)
    monkeypatch.setattr(db, "extract_and_save_decisions", REAL_EXTRACT.__get__(db))
    # Snapshot-only DB fixture skips Chroma initialization; committee needs its
    # explicit unavailable state, while SQLite remains the real implementation.
    db.col_memos = db.col_decisions = db.col_feedback = None
    monkeypatch.setattr(scorekeeper, "get_track_record_for_specialist", lambda *a, **k: "")
    monkeypatch.setattr(scorekeeper, "get_track_record_for_capo", lambda *a, **k: "")
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (.9, "synthetic replay FX"))
    llm_pricing.reset_fx_memo()
    state = SimpleNamespace(db=db, clients={}, news=[], prepared=[], capo_calls=[],
                            side_calls=[], extractor_calls=[], network=attempted_network,
                            smtp=run_offline.inviati, loop=replay_loop, catalog_reads=[])
    # Models API (listino/limiti OpenRouter): dato di rete come il cambio FX, sintetico nel
    # replay. Il Red Team lo legge dal registro richieste della run (V0-REDTEAM 05/10).
    monkeypatch.setattr(preparation_ai, "live_metadata", lambda model: state.catalog_reads.append(model) or {
        "id": model, "context_length": 1_000_000, "top_provider": {"max_completion_tokens": 65536},
        "pricing": {"prompt": "0.000001", "completion": "0.000002"}})

    def research_block(*, sector_bundles=None, decision_links=None, notes_context=None, legacy=False):
        if notes_context is not None:
            # T1 (df317cf): new runs deliver the accepted frozen notes, rendered by the
            # real function the delivery guard also uses (never a replay paraphrase).
            return REAL_CURRENT_FACTS.research_block(notes_context=notes_context)
        # Actual prepare_sector_analysis via the shared fixture, on explicit synthetic
        # provider records. Only the research queue that requests it is simulated.
        bundle = bundle_for(profile="manufacturing")
        sector_bundles["SYNTH-EXT"] = bundle
        state.prepared.append(bundle["snapshot_id"])
        return "SYNTHETIC RESEARCH QUEUE: evaluate SYNTH-EXT from the prepared documented records."

    facts = _modulo_finto(monkeypatch, "bellomberg.core.current_facts",
        current_facts_block=lambda: "SYNTHETIC REPLAY: empty portfolio; no live facts.",
        favorites_block=lambda: "", pm_theses_block=lambda: "", research_block=research_block,
        # T1 research notes: the real freeze/receipt/reply code on the replay's synthetic DB.
        **{name: getattr(REAL_CURRENT_FACTS, name) for name in (
            "research_notes_enabled", "freeze_research_notes", "research_notes_for_board",
            "check_research_notes_delivery", "record_research_notes_delivery",
            "recover_frozen_research_reply", "add_frozen_research_reply")})
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
            "|---|---|---|---|---|\n| BUY | SYNTH-EXT | 250 | At review | HIGH |\n")
    state.memo = memo
    (tmp_path / "report" / "VAL_SYNTH-EXT_FLAGGED.payload.json").write_text(json.dumps({
        "_timestamp": "2026-09-21T10:41:00", "sanity": {
            "severity": "BLOCK", "headline": "Synthetic sanity block",
            "reason": "synthetic issuer identity mismatch",
        },
    }), encoding="utf-8")

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
                state.extractor_calls.append(deepcopy(kwargs))
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
    assert client.calls[2]["thinking"] == {"type": "effort", "effort": "high"}
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




# Contratto ATTUALE (ZR 05/10, Z1). I 4 test del replay settimanale LEGACY con valutazione/workbook
# (recupero desk + get_valuation, gate prima del troncamento, orchestratore PDF+XLSX+MIME, stream chat che
# preserva gate/cutoff/FV) sono in archive/private/attic/tests_excel_archiviato_20261005/test_pipeline_replay_artifacts_legacy.py:
# dal 1326312 get_valuation risponde excel_archived e una weekly NUOVA legacy e' rifiutata
# (consigliere_multi.py:1250-1261); la weekly viva di ricerca e' in tests/test_weekly_research_native.py.
# Le fixture replay/replay_loop restano: tests/test_language_agent_outputs.py le importa.
def test_chat_stream_get_valuation_reaches_the_model_as_the_archived_contract(replay_loop, monkeypatch):
    from bellomberg.agents import chat_engine
    from _contratto_excel_archiviato import ARCHIVIATO, blinda_ramo_archiviato
    first = [_chunk({"tool_calls": [{"index": 0, "id": "v", "function": {
        "name": "get_valuation", "arguments": '{"ticker":"SYNTH-EXT"}'}}]}, finish="tool_calls")]
    run, _, _, requests = _chat(monkeypatch, [first, [_chunk({"content": "Synthetic response"}, finish="stop")]])
    results = []

    def dispatch(name, args, **kwargs):
        result = chat_tools.dispatch(name, args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(chat_engine, "dispatch", dispatch)
    monkeypatch.setattr(chat_engine, "get_tools_for_agent", lambda _: chat_tools.get_tools_for_agent("fundamentals"))
    chiamate = blinda_ramo_archiviato(monkeypatch)
    events = replay_loop.run_until_complete(run())
    assert chiamate == []
    assert len(requests) == 2
    content = next(message["content"] for message in requests[1]["messages"] if message["role"] == "tool")
    # Il modello legge il buco dichiarato, non un fair value ne' un silenzio.
    assert "excel_archived" in content and ARCHIVIATO["error"] in content
    summary = json.loads(next(line for line in content.splitlines() if line.startswith("{")))
    assert summary["fair_value"] is None and summary["valuation_usability"]["usable"] is False
    assert summary["error"] == ARCHIVIATO["error"] and "14.13" not in content
    assert results == [ARCHIVIATO]
    assert any(event.startswith("event: tool_result") for event in events)
