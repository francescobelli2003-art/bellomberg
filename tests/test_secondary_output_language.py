"""Actual secondary response builders; synthetic sources and measured cache reuse."""
from copy import deepcopy

import pytest

from bellomberg.core.language import language_context


def test_vol_cone_cache_switches_authored_text_without_fetching_or_changing_metrics(monkeypatch):
    from bellomberg.portfolio import vol_cone as cone, vol_surface
    from bellomberg.market_data import iv_history
    cone._CACHE.clear()
    calls = {"closes": 0, "implied": 0}
    def closes(_):
        calls["closes"] += 1
        return [100.] * 12
    def implied(_):
        calls["implied"] += 1
        return {"slices": [{"expiry": "2001-03-01", "days": 45, "atm_iv": .2}]}
    monkeypatch.setattr(iv_history, "stato_iv_tickers", lambda: {"origine": "locale", "tickers": ["SYNTH"]})
    monkeypatch.setattr(cone, "_fetch_closes", closes)
    monkeypatch.setattr(vol_surface, "build_vol_surface", implied)
    try:
        with language_context("it"):
            italian = cone.compute_vol_cone("SYNTH")
        cache = deepcopy(cone._CACHE)
        with language_context("en"):
            english = cone.compute_vol_cone("SYNTH")
        with language_context("it"):
            again = cone.compute_vol_cone("SYNTH")
        assert calls == {"closes": 1, "implied": 1}
        assert "daily simple returns" in english["basis"]
        assert "insufficient data" in english["confronto"][0]["error"]
        assert "insufficient data" in english["realized"]["windows"][2]["error"]
        assert english["realized"]["windows"][0] == italian["realized"]["windows"][0]
        assert english["realized"]["windows"][0]["n_obs"] == 7
        assert english["realized"]["windows"][0]["current"] == 0
        for key in ("ticker", "asof", "period", "implied", "src"):
            assert english[key] == italian[key]
        assert again == italian
        assert cone._CACHE == cache
        assert english is not italian
    finally:
        cone._CACHE.clear()


@pytest.mark.parametrize("closes,expected", [([], "insufficient closing prices"), ([100, None, 100], "invalid closing prices")])
def test_vol_cone_invalid_input_is_explicit_in_both_languages(closes, expected):
    from bellomberg.portfolio.vol_cone import build_cone
    with language_context("it"):
        italian = build_cone(closes)
    with language_context("en"):
        english = build_cone(closes)
    assert "error" in italian
    assert expected in english["error"]


@pytest.mark.parametrize("risk_free_fails", [False, True])
def test_advanced_metrics_language_preserves_beta_and_declares_risk_free_origin(monkeypatch, risk_free_fails):
    from bellomberg.portfolio import advanced_metrics as am, twr_engine
    from bellomberg.market_data import benchmark_series, market_inputs
    returns = [.02, -.01] * 6
    dates = [f"2001-01-{i:02d}" for i in range(1, 14)]
    index = [100.]
    for daily in returns:
        index.append(index[-1] * (1 + daily))
    monkeypatch.setattr(twr_engine, "compute_twr_payload", lambda: {"dates": dates, "twr_index": index})
    monkeypatch.setattr(benchmark_series, "compute_benchmark_series", lambda **kw: {
        "dates": dates, "ret_daily": returns, "carried_flags": [False] * 13, "carried_days": 0})
    calls = []
    def risk_free(currency):
        calls.append(currency)
        if risk_free_fails:
            raise OSError("synthetic source unavailable")
        return .025
    monkeypatch.setattr(market_inputs, "get_risk_free", risk_free)
    with language_context("it"):
        italian = am.portfolio_metrics()
    with language_context("en"):
        english = am.portfolio_metrics()
    assert calls == ["EUR", "EUR"]
    assert english["benchmark"]["beta"] == italian["benchmark"]["beta"] == 1.
    assert english["benchmark"]["alpha_annual_pct"] == italian["benchmark"]["alpha_annual_pct"] == 0.
    assert english["risk_free_used"] == italian["risk_free_used"] == (.03 if risk_free_fails else .025)
    assert "12 common days" in english["benchmark_alignment"]
    assert "official twr_index" in english["_source"]
    if risk_free_fails:
        assert english["risk_free_status"] == italian["risk_free_status"] == "fallback"
        assert "3%" in english["risk_free_note"] and "fallback" in english["risk_free_note"]
        assert "synthetic source unavailable" in english["risk_free_note"]
    else:
        assert english["risk_free_status"] == italian["risk_free_status"] == "source_metadata_unavailable"
        assert "source freshness" in english["risk_free_note"]
    for key in italian.keys() - {"_source", "benchmark_alignment", "risk_free_note"}:
        assert english[key] == italian[key], key


def test_beta_guard_same_verdict_threshold_and_failures_in_both_languages(monkeypatch):
    from bellomberg.portfolio import advanced_metrics as am, portfolio_risk, portfolio_factors
    monkeypatch.setattr(am, "portfolio_metrics", lambda: {"benchmark": {"beta": .5}})
    monkeypatch.setattr(portfolio_risk, "compute_portfolio_risk", lambda: {"portfolio": {"beta_vs_spy": 1.}})
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors", lambda: {})
    with language_context("it"):
        italian = am.reconcile_betas()
    with language_context("en"):
        english = am.reconcile_betas()
    assert english["verdict"] == italian["verdict"] == "UNRELIABLE"
    assert english["threshold"] == italian["threshold"] == .35
    assert english["max_spread"] == italian["max_spread"] == .5
    assert english["betas"] == italian["betas"]
    assert "DO NOT use beta" in english["note"]
    assert english["sources_failed"] == {"factor_model_mkt": "beta_market missing"}
    assert "official TWR series" in english["definitions"]["advanced_metrics_twr"]


@pytest.mark.parametrize("returns,expected", [([], "series too short"), ([.1], "series too short")])
def test_metric_error_language_is_explicit(returns, expected):
    from bellomberg.portfolio.advanced_metrics import compute_metrics
    with language_context("en"):
        assert compute_metrics(returns)["error"] == expected


@pytest.mark.parametrize("has_atm", [False, True])
def test_gex_labels_and_spot_proxy_declared_without_changing_gamma_math(monkeypatch, has_atm):
    from datetime import date, timedelta
    from bellomberg.portfolio import positioning_tools as pt
    from bellomberg.market_data import polygon_data
    expiry = (date.today() + timedelta(days=10)).isoformat()
    monkeypatch.setattr(polygon_data, "polygon_available", lambda: True)
    monkeypatch.setattr(polygon_data, "get_option_expirations", lambda _: {"expirations": [expiry]})
    calls = []
    def chain(*args, **kw):
        calls.append(args)
        return {"chain": [
            {"gamma": .02, "oi": 100, "strike": 100, "type": "call", "delta": .5 if has_atm else .8},
            {"gamma": .01, "oi": 50, "strike": 100, "type": "put", "delta": -.5}]}
    monkeypatch.setattr(polygon_data, "get_options_chain", chain)
    with language_context("it"):
        italian = pt.compute_gex("SYNTH")
    with language_context("en"):
        english = pt.compute_gex("SYNTH")
    assert len(calls) == 2
    assert english["spot_est"] == italian["spot_est"] == 100
    assert english["net_gex_usd_per_1pct"] == italian["net_gex_usd_per_1pct"] == 15000
    assert english["call_gex_usd"] == italian["call_gex_usd"] == 20000
    assert english["put_gex_usd"] == italian["put_gex_usd"] == -5000
    assert english["gamma_flip_strike"] is None
    assert "net GEX" in english["regime"] and "above the gamma flip" not in english["regime"]
    assert "snapshot" in english["note"] and "valid greeks" in english["note"]
    assert "proxy" in english["spot_note"]
    assert english["spot_method"] == italian["spot_method"] == ("atm_call_strike" if has_atm else "median_strike")
    assert ("call strike" if has_atm else "median strike") in english["spot_note"]
    for key in italian.keys() - {"note", "regime", "spot_note", "_timestamp"}:
        assert english[key] == italian[key], key


def test_cot_original_contract_and_hand_calculated_nets_unchanged_by_language(monkeypatch):
    from types import SimpleNamespace
    from bellomberg.portfolio import positioning_tools as pt
    rows = [{"contract_market_name": "SYNTH FUTURE", "report_date_as_yyyy_mm_dd": f"2001-01-{i:02d}",
             "lev_money_long": i + 10, "lev_money_short": 10} for i in range(12, 0, -1)]
    calls = []
    def response(*args, **kw):
        calls.append(kw)
        return SimpleNamespace(status_code=200, json=lambda: deepcopy(rows))
    monkeypatch.setattr(pt.requests, "get", response)
    with language_context("it"):
        italian = pt.get_cot_positioning("SYNTH FUTURE")
    with language_context("en"):
        english = pt.get_cot_positioning("SYNTH FUTURE")
    assert len(calls) == 2 and calls[0] == calls[1]
    assert english["contract_market_name"] == "SYNTH FUTURE"
    assert english["net_positions"]["leveraged_funds"] == 12
    assert english["wow_change"]["leveraged_funds"] == 1
    assert english["leveraged_funds_net_percentile_1y"] == 100
    assert "EXTREME LONG" in english["reading"]
    for key in italian.keys() - {"reading", "_timestamp"}:
        assert english[key] == italian[key], key


@pytest.mark.parametrize("vix,expected", [(18., "CONTANGO (normal)"), (22., "BACKWARDATION (stress)"), (20., "FLAT: regime transition")])
def test_vix_term_structure_regime_math_same_both_languages(monkeypatch, vix, expected):
    import pandas as pd
    import yfinance
    from types import SimpleNamespace
    from bellomberg.portfolio import positioning_tools as pt
    monkeypatch.setattr(yfinance, "Ticker", lambda ticker: SimpleNamespace(
        history=lambda **kw: pd.DataFrame({"Close": [vix if ticker == "^VIX" else 20.]})))
    with language_context("it"):
        italian = pt.get_vix_term_structure()
    with language_context("en"):
        english = pt.get_vix_term_structure()
    assert expected in english["term_structure"]
    assert english["vix_vix3m_ratio"] == italian["vix_vix3m_ratio"] == vix / 20
    assert english["term_structure"].split(" ")[0].rstrip(":") == italian["term_structure"].split(" ")[0].rstrip(":")


def test_signal_cache_uses_same_detectors_and_scores_when_language_changes(monkeypatch):
    from types import SimpleNamespace
    from bellomberg.portfolio import signal_engine as se, vol_surface, positioning_tools, portfolio_factors
    from bellomberg.storage import memory_db
    from bellomberg.market_data import quiver_data
    monkeypatch.setattr(memory_db, "MemoryDB", lambda: SimpleNamespace(get_portfolio_summary=lambda: {"positions": [{"ticker": "SYNTH"}]}))
    monkeypatch.setattr(se.time, "monotonic", lambda: 123.)
    calls = {"vol": 0, "gex": 0, "factors": 0}
    def vol(*args, **kw):
        calls["vol"] += 1
        return {"iv_rv_spread_front": .1, "rv_percentile_1y": 90, "expected_move_pct": 14, "expected_move_days": 30}
    def gex(*args, **kw):
        calls["gex"] += 1
        return {"gamma_flip_strike": 100, "spot_est": 90, "net_gex_usd_per_1pct": -10000}
    def factors():
        calls["factors"] += 1
        return {"per_holding": {"SYNTH": {"beta_market": 1.5, "alpha_tstat": 3.}}}
    monkeypatch.setattr(vol_surface, "build_vol_surface", vol)
    monkeypatch.setattr(positioning_tools, "compute_gex", gex)
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors", factors)
    monkeypatch.setattr(quiver_data, "quiver_available", lambda: False)
    def price(ticker, esiti=None):
        if esiti is not None:
            esiti["z-score di prezzo"] = "interrogato"
        return []
    monkeypatch.setattr(se, "sig_price_zscore", price)
    se.clear_scan_cache()
    try:
        with language_context("it"):
            italian = se.scan_portfolio(min_strength=0)
        cache = deepcopy(se._SCAN_CACHE)
        with language_context("en"):
            english = se.scan_portfolio(min_strength=0)
        with language_context("it"):
            again = se.scan_portfolio(min_strength=0)
        assert calls == {"vol": 1, "gex": 1, "factors": 1}
        assert english["generated"] == italian["generated"]
        assert english["cache"]["servita_da_cache"] and english["cache"]["ttl_s"] == 300 and english["cache"]["eta_s"] == 0
        assert "DEGRADED scan" in english["cache"]["ttl_motivo"]
        assert "Scanned 1 positions out of 1" in english["copertura"]["nota"]
        assert "Quiver unavailable" in english["copertura"]["scansione_degradata"]["SYNTH"]
        assert "MUTED" in english["copertura"]["scansione_degradata"]["SYNTH"]
        assert "MUTO" not in english["copertura"]["scansione_degradata"]["SYNTH"]
        assert english["n_signals_total"] == italian["n_signals_total"] == 6
        invariant = lambda payload: [(s["ticker"], s["category"], s["direction"], s["strength"], s["source"]) for s in payload["signals"]]
        assert invariant(english) == invariant(italian) == invariant(again)
        assert sorted(s["strength"] for s in english["signals"]) == [30, 36, 70, 80, 80, 90]
        assert "EXPENSIVE" in next(s for s in english["signals"] if s["source"] == "vol_surface IV-RV")["reading"]
        assert "dear" not in str(english["signals"])
        assert se._SCAN_CACHE == cache
        assert again["signals"] == italian["signals"]
    finally:
        se.clear_scan_cache()


def test_signal_diagnostic_truncation_declares_count_in_selected_language():
    from bellomberg.core.presentation import message, render_payload
    from bellomberg.portfolio.signal_engine import _motivo_corto
    with language_context("it"):
        reason = _motivo_corto(message("errore " + "x" * 350, "failure " + "x" * 350))
    english = render_payload(reason, language="en")
    assert english.startswith("failure ") and "58 characters omitted" in english
    assert "57 char non riportati" in reason
