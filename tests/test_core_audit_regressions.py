"""Real LLM/core flows, mocked provider transport and storage, no paid calls."""
import asyncio
import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace as NS

import httpx
import pytest


def _usage(**overrides):
    return NS(input_tokens=10, output_tokens=20, cache_read_input_tokens=0,
              cache_creation_input_tokens=0, cost_usd=.03, **overrides)


def test_action_extractor_keeps_missing_usage(monkeypatch):
    import bellomberg.agents.action_table_extract as ae
    import bellomberg.core.llm_client as lc
    usage = NS(input_tokens=None, output_tokens=20, cache_read_input_tokens=None,
               cache_creation_input_tokens=None, cost_usd=.03)
    response = NS(usage=usage, stop_reason="tool_use", content=[NS(type="tool_use",
        name="emit_action_table", input={"rows": [], "table_found": False})])
    monkeypatch.setattr(lc, "OpenRouterClient", lambda **kw: NS(messages=NS(create=lambda **k: response)))
    recorded = {}
    ae.extract_rows_structured("## ACTION TABLE\nNo actions", usage_out=recorded)
    assert recorded["in"] is None
    assert recorded["status"] == "usage_unknown"


def test_reflection_keeps_missing_usage_and_does_not_save_truncation(monkeypatch):
    import bellomberg.agents.reflection as rf
    import bellomberg.core.llm_client as lc
    import bellomberg.agents.scorekeeper as sk
    monkeypatch.setattr(sk, "compute_scorecard", lambda: {"overall": {"n": 30}})
    monkeypatch.setattr(sk, "format_track_record_for_capo", lambda *a, **kw: "synthetic measured track")
    monkeypatch.setattr(rf, "_load_lessons", lambda: [])
    saved = []
    monkeypatch.setattr(rf, "_save_lessons", lambda value: saved.append(value))
    response = NS(usage=NS(input_tokens=None, output_tokens=20, cost_usd=.03),
                  stop_reason="max_tokens", content=[NS(text="An incomplete lesson because")])
    monkeypatch.setattr(lc, "OpenRouterClient", lambda **kw: NS(messages=NS(create=lambda **k: response)))
    recorded = {}
    rf.generate_lesson("synthetic memo", usage_out=recorded)
    assert recorded["in"] is None
    assert not saved, "a truncated lesson must not be injected as a complete future instruction"


def test_capo_counts_both_successful_calls_after_collapsed_retry(monkeypatch):
    from bellomberg.agents import capo
    import bellomberg.core.mandato_pm as mp
    import bellomberg.core.current_facts as cf
    monkeypatch.setattr(mp, "carica", lambda: {})
    monkeypatch.setattr(mp, "compila", lambda text, mandate: "synthetic system")
    monkeypatch.setattr(mp, "impronta", lambda mandate: "synthetic")
    monkeypatch.setattr(cf, "current_facts_block", lambda: "")
    monkeypatch.setattr(cf, "pm_theses_block", lambda: "")
    monkeypatch.setitem(sys.modules, 'bellomberg.valuation.cef_lookthrough', NS(capo_block=lambda: ""))
    monkeypatch.setitem(sys.modules, 'bellomberg.portfolio.signal_engine', NS(scan_portfolio=lambda **kw: {"signals": []}))
    responses = iter([NS(content=[NS(type="text", text="short")], usage=_usage(), stop_reason="end_turn"),
                     NS(content=[NS(type="text", text="complete " * 1000)], usage=_usage(), stop_reason="end_turn")])
    def stream(**kwargs):
        response = next(responses)
        return nullcontext(NS(get_final_message=lambda: response))
    monkeypatch.setattr(capo, "OpenRouterClient", lambda **kw: NS(messages=NS(stream=stream)))
    _, usage = capo.run_capo(NS(data={}))
    assert usage["input_tokens"] == 20
    assert usage["output_tokens"] == 40
    assert usage["cost_usd"] == pytest.approx(.06)
    assert usage["api_calls"] == 2
    import bellomberg.agents.consigliere_multi as cm
    recorded = []
    cm._record_capo_usage(NS(record_usage=lambda *a, **kw: recorded.append((a, kw))), usage, 1.0)
    assert recorded[0][1]["api_calls"] == 2


def _chat(monkeypatch, replies):
    import bellomberg.agents.chat_engine as ce
    import bellomberg.core.llm_client as lc
    saved, dispatched, requests = [], [], []
    monkeypatch.setattr(ce, "OPENROUTER_API_KEY", "synthetic")
    monkeypatch.setattr(ce, "_save_message", lambda *a, **kw: saved.append((a, kw)))
    monkeypatch.setattr(ce, "get_messages", lambda _: [{"role": "user", "content": "synthetic"}])
    monkeypatch.setattr(ce, "_leggi_mandato_chat", lambda: (None, "synthetic"))
    monkeypatch.setattr(ce, "_build_system", lambda *a, **kw: "synthetic system")
    monkeypatch.setattr(ce, "get_tools_for_agent", lambda _: [])
    monkeypatch.setattr(ce, "_log", lambda _: None)
    monkeypatch.setattr(ce, "_done_payload", lambda session_id, **kwargs: dict(session_id=session_id, **kwargs))
    monkeypatch.setattr(ce, "dispatch", lambda name, args, **kw: dispatched.append((name, args)) or {"ok": True})
    reply_iter = iter(replies)
    def handle(request):
        requests.append(json.loads(request.content))
        data = next(reply_iter)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(row) + "\n\n" for row in data) + "data: [DONE]\n\n")
    client = lc.AsyncOpenRouterClient(api_key="synthetic", trasporto=httpx.MockTransport(handle))
    monkeypatch.setattr(ce, "AsyncOpenRouterClient", lambda: client)
    async def run():
        try:
            return [s async for s in ce.stream_chat(1, "quant", "synthetic")]
        finally:
            await client._http.aclose()
    return run, saved, dispatched, requests


def _chunk(delta=None, finish=None):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_chat_noncomplete_stop_is_not_success(monkeypatch, finish):
    run, saved, _, _ = _chat(monkeypatch, [[_chunk({"content": "partial answer"}), _chunk(finish=finish)]])
    events = asyncio.run(run())
    done = next(json.loads(s.split("data: ")[1]) for s in events if s.startswith("event: done"))
    assert done["ok"] is False


def test_chat_interleaved_tool_stream_preserves_both_arguments(monkeypatch):
    first = [
        _chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "tool_a", "arguments": '{"ticker":'}}]}),
        _chunk({"tool_calls": [{"index": 1, "id": "b", "function": {"name": "tool_b", "arguments": '{"ticker":'}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"AAA"}'}}]}),
        _chunk({"tool_calls": [{"index": 1, "function": {"arguments": '"BBB"}'}}]}),
        _chunk(finish="tool_calls"),
    ]
    run, _, dispatched, _ = _chat(monkeypatch, [first, [_chunk({"content": "complete"}, finish="stop")]])
    asyncio.run(run())
    assert dispatched == [("tool_a", {"ticker": "AAA"}), ("tool_b", {"ticker": "BBB"})]


def test_chat_does_not_dispatch_after_last_iteration(monkeypatch):
    import bellomberg.agents.chat_engine as ce
    monkeypatch.setattr(ce, "MAX_TOOL_ITERATIONS", 1)
    first = [_chunk({"tool_calls": [{"index": 0, "id": "a", "function": {
        "name": "tool_a", "arguments": '{"ticker":"AAA"}'}}]}, finish="tool_calls")]
    run, _, dispatched, requests = _chat(monkeypatch, [first])
    events = asyncio.run(run())
    assert dispatched == []
    assert requests[0]["tool_choice"] == "none"
    done = next(json.loads(s.split("data: ")[1]) for s in events if s.startswith("event: done"))
    assert done["ok"] is False


def test_chat_dispatch_runs_outside_the_event_loop(monkeypatch):
    import threading
    import bellomberg.agents.chat_engine as ce
    main_thread = threading.get_ident()
    first = [_chunk({"tool_calls": [{"index": 0, "id": "a", "function": {
        "name": "tool_a", "arguments": '{}'}}]}, finish="tool_calls")]
    run, _, _, _ = _chat(monkeypatch, [first, [_chunk({"content": "done"}, finish="stop")]])
    threads = []
    monkeypatch.setattr(ce, "dispatch", lambda *a, **kw: threads.append(threading.get_ident()) or {})
    asyncio.run(run())
    assert threads and all(t != main_thread for t in threads)


def test_chat_wrapped_tool_error_is_not_success(monkeypatch):
    import bellomberg.agents.chat_engine as ce
    import bellomberg.agents.chat_tools as ct
    first = [_chunk({"tool_calls": [{"index": 0, "id": "a", "function": {
        "name": "tool_a", "arguments": '{}'}}]}, finish="tool_calls")]
    run, _, _, _ = _chat(monkeypatch, [first, [_chunk({"content": "done"}, finish="stop")]])
    monkeypatch.setattr(ce, "dispatch", lambda *a, **kw: ct._stamp({"error": "synthetic provider failed"}, "fixture"))
    events = asyncio.run(run())
    result = next(json.loads(s.split("data: ")[1]) for s in events if s.startswith("event: tool_result"))
    assert result["ok"] is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "20"])
def test_scorers_do_not_turn_invalid_metric_into_risk_points(value):
    import bellomberg.agents.specialist_scores as ss
    assert ss.quant_score(risk_data={"portfolio": {"vol_annual_pct": value}}) is None
    assert ss.macro_score({"indicators": {"vix_close": {"value": value}}}) is None
    assert ss.options_score(options_data={"atm_iv_call_pct": value}) is None
    assert ss.crypto_score({"highest_funding_long_pressure": [{"funding_annualized_pct": value}]}) is None
    assert ss.politics_score({"synthetic topic": value}) is None


@pytest.mark.parametrize("force", [True, False])
def test_scorekeeper_recalculation_reloads_market_history(monkeypatch, tmp_path, force):
    import pandas as pd
    import bellomberg.agents.scorekeeper as sk
    from datetime import datetime, timedelta
    now = datetime.now()
    date = (now - timedelta(days=40)).strftime("%Y-%m-%d")
    row = dict(id=1, memo_id=1, timestamp=date, action="BUY", ticker="SYNTH", eur_amount=1,
               confidence="ALTA", status="PENDING")
    conn = NS(execute=lambda sql, *a: NS(fetchall=lambda: [row] if "FROM decisions" in sql else []))
    db = NS(_conn=lambda: nullcontext(conn))
    monkeypatch.setattr(sk, "SNAP_PATH", str(tmp_path / "score.json"))
    monkeypatch.setattr(sk, "_MEM_CACHE", {})
    calls = []
    def download(ticker, start, **kw):
        calls.append(ticker)
        index = pd.date_range(start, now.strftime("%Y-%m-%d"))
        values = [100 if (d - pd.Timestamp(date)).days < 7 else 100 + 10 * len(calls) for d in index]
        return pd.DataFrame({"Close": values}, index=index)
    monkeypatch.setitem(sys.modules, "yfinance", NS(download=download))
    monkeypatch.setitem(sys.modules, 'bellomberg.cli.price_updater', NS(data_ticker=lambda t: t))
    first = sk.compute_scorecard(db, force=True)
    if not force:
        snapshot = json.loads((tmp_path / "score.json").read_text())
        snapshot["computed_at"] = (now - timedelta(hours=sk.SNAP_TTL_H + 1)).isoformat()
        (tmp_path / "score.json").write_text(json.dumps(snapshot))
    second = sk.compute_scorecard(db, force=force)
    assert len(calls) == 2
    assert first["details"][0]["edge_pct"] == 10
    assert second["details"][0]["edge_pct"] == 20


def _ibkr(monkeypatch):
    import bellomberg.agents.agent_tools as at
    modes, expiries = [], []
    class Contract:
        def __init__(self, ticker, expiry=None, strike=None, right=None, *a):
            self.symbol, self.secType, self.conId = ticker, "STK", 1
            self.strike, self.right = strike, right
            if right:
                expiries.append(expiry)
    class IB:
        def connect(self, *a, **kw): pass
        def disconnect(self): pass
        def reqMarketDataType(self, mode): modes.append(mode)
        def qualifyContracts(self, *a): pass
        def sleep(self, *a): pass
        def reqSecDefOptParams(self, *a):
            return [NS(exchange="SMART", expirations={"20261001", "20261101"}, strikes={100})]
        def reqMktData(self, *a, **kw):
            return NS(marketPrice=lambda: 100, modelGreeks=NS(impliedVol=.2, delta=.5, gamma=.1,
                vega=.1, theta=-.1), callOpenInterest=10, putOpenInterest=10, bid=1, ask=2, last=1.5)
    monkeypatch.setitem(sys.modules, "ib_async", NS(IB=IB, Stock=Contract, Option=Contract))
    return at, modes, expiries


def test_ibkr_delayed_frozen_is_not_labeled_realtime(monkeypatch):
    at, modes, _ = _ibkr(monkeypatch)
    result = at._get_ibkr_options("SYNTH")
    assert modes == [4]
    assert "realtime" not in result["data_source"].lower()
    assert "delayed" in result["data_source"].lower()


def test_ibkr_respects_requested_expiry(monkeypatch):
    at, _, expiries = _ibkr(monkeypatch)
    monkeypatch.setitem(sys.modules, 'bellomberg.market_data.polygon_data', NS(polygon_available=lambda: False,
                                                       get_options_summary_polygon=lambda *a, **k: None))
    result = at.tool_get_options_data("SYNTH", expiry="2026-11-01")
    assert expiries == ["20261101", "20261101"]
    assert result["nearest_expiry"].replace("-", "") == "20261101"


def test_options_unavailable_expiry_never_queries_another_contract(monkeypatch):
    at, _, expiries = _ibkr(monkeypatch)
    monkeypatch.setitem(sys.modules, 'bellomberg.market_data.polygon_data', NS(polygon_available=lambda: False,
                                                       get_options_summary_polygon=lambda *a, **k: None))
    queried = []
    monkeypatch.setattr(at, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(at, "yf", NS(Ticker=lambda t: NS(options=["2026-10-01", "2026-11-01"],
        option_chain=lambda expiry: queried.append(expiry))))
    result = at.tool_get_options_data("SYNTH", expiry="2026-12-01")
    assert "2026-12-01" in result["error"]
    assert expiries == queried == []


def test_hybrid_oi_keeps_the_ibkr_expiry(monkeypatch):
    import bellomberg.agents.agent_tools as at
    import pandas as pd
    monkeypatch.setitem(sys.modules, 'bellomberg.market_data.polygon_data', NS(polygon_available=lambda: False,
                                                       get_options_summary_polygon=lambda *a, **k: None))
    monkeypatch.setattr(at, "_get_ibkr_options", lambda *a, **k: dict(
        data_source="IBKR_TWS_requested_delayed_frozen", nearest_expiry="20261101",
        total_call_oi=0, total_put_oi=0, spot=100))
    queried = []
    def chain(expiry):
        queried.append(expiry)
        return NS(calls=pd.DataFrame({"strike": [100], "openInterest": [10]}),
                  puts=pd.DataFrame({"strike": [100], "openInterest": [20]}))
    monkeypatch.setattr(at, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(at, "yf", NS(Ticker=lambda t: NS(
        options=["2026-10-01", "2026-11-01"], option_chain=chain)))
    result = at.tool_get_options_data("SYNTH", expiry="2026-11-01")
    assert queried == ["2026-11-01"]
    assert result["put_call_oi_ratio"] == 2
    assert "realtime" not in result["data_source"]


def test_scorers_keep_real_zero_and_disclose_an_invalid_partial_metric():
    import bellomberg.agents.specialist_scores as ss
    result = ss.quant_score(risk_data={"portfolio": {"vol_annual_pct": float("nan"), "sharpe": 0}})
    assert result["metrics"]["vol_annual_pct"] is None
    assert result["metrics"]["sharpe"] == 0
    assert result["max_score"] == 3
    result = ss.crypto_score({"highest_funding_long_pressure": [
        {"funding_annualized_pct": float("nan")}, {"funding_annualized_pct": 0}]})
    assert result["metrics"]["funding_ann_pct"] == 0
    assert result["score"] == 0


def test_politics_reads_the_actual_polymarket_tool_contract(monkeypatch):
    import bellomberg.agents.agent_tools as at
    import bellomberg.agents.specialist_scores as ss
    monkeypatch.setattr(at, "_poly_fetch", lambda url, params, **kw: {
        "events": [{"title": "synthetic event", "slug": "synthetic-event", "markets": [{
            "question": "synthetic tail?", "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.8","0.2"]'}]}]} if url.endswith("public-search") else [])
    monkeypatch.setattr(ss, "_POLI_TOPICS", {"Synthetic": "synthetic"})
    result = ss.politics_score()
    assert result["metrics"]["topics"] == {"Synthetic": .8}
    assert result["score"] == 3


@pytest.mark.parametrize("assumption", ["terminal_growth", "wacc_delta_bp"])
def test_valuation_explicit_zero_is_a_new_assumption(monkeypatch, assumption):
    import os
    import bellomberg.agents.chat_tools as ct
    from datetime import datetime
    calls = []
    monkeypatch.setitem(sys.modules, 'bellomberg.storage.memory_db', NS(MemoryDB=lambda: NS(
        get_valuation_history=lambda *a, **k: [dict(date=datetime.now().isoformat(),
            fair_value=100, sanity_severity="OK")])) )
    monkeypatch.setitem(sys.modules, 'bellomberg.valuation.dcf_engine', NS(_count_uncached_formulas=lambda p: 0,
        generate_valuation=lambda *a, **kw: calls.append(kw) or {}))
    exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: True if str(p).endswith("VAL_SYNTH.xlsx") else exists(p))
    result = ct.dispatch("get_valuation", {"ticker": "SYNTH", assumption: 0})
    assert len(calls) == 1
    assert calls[0][assumption] == 0
    assert result.get("data", {}).get("reused") is not True


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), None, True, "2"])
def test_series_summary_does_not_invent_the_latest_observation(invalid):
    import bellomberg.agents.chat_tools as ct
    result = ct._riassumi_serie([0.0, 2.0, invalid])
    assert result["ultimo"] is None
    assert result["medio"] == 1
    assert result["n"] == 2
    assert result["n_nd"] == 1


@pytest.mark.parametrize("configured, sent", [(False, False), (True, False), (True, True)])
def test_weekly_email_reports_actual_send_outcome(monkeypatch, configured, sent):
    import bellomberg.agents.consigliere_multi as cm
    calls, logs = [], []
    monkeypatch.setattr(cm, "email_configurata", lambda: configured)
    monkeypatch.setattr(cm, "_log", logs.append)
    monkeypatch.setitem(sys.modules, 'bellomberg.reporting.email_sender', NS(invia_email_multi_allegati=
        lambda **kw: calls.append(kw) or sent))
    result = cm._send_weekly_email(["synthetic.pdf"])
    assert result is (configured and sent)
    assert len(calls) == int(configured)
    assert any("Email sent" in line for line in logs) is (configured and sent)
    if configured and not sent:
        assert any("NON inviata" in line for line in logs)
    if not configured:
        assert any("not configured" in line for line in logs)
