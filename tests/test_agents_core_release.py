"""Core release regressions: real functions, synthetic providers, no network or data."""
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


def _market(monkeypatch, history):
    import bellomberg.agents.agent_tools as at
    import bellomberg.cli.price_updater as pu
    monkeypatch.setattr(pu, "data_ticker", lambda ticker: ticker)
    monkeypatch.setattr(at, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(at, "yf", SimpleNamespace(Ticker=lambda _: SimpleNamespace(history=history)))
    return at


def _history(prices, start="2026-01-01"):
    return pd.DataFrame({"Close": prices, "Volume": 100.0},
                        index=pd.date_range(start, periods=len(prices)))


def test_market_52week_failure_is_not_three_month_max(monkeypatch):
    def history(period):
        if period == "1y":
            raise RuntimeError("synthetic 1y unavailable")
        return _history(np.linspace(100, 120, 70))
    result = _market(monkeypatch, history).tool_get_market_data("SYNTH")
    assert result["dist_max_pct"] is None
    assert "synthetic 1y unavailable" in result["dist_max_note"]


def test_market_52week_uses_one_year_even_for_long_request(monkeypatch):
    periods = []
    def history(period):
        periods.append(period)
        return _history([1000.0] + [100.0] * 500) if period == "2y" else _history([200.0] + [100.0] * 364)
    result = _market(monkeypatch, history).tool_get_market_data("SYNTH", period="2y")
    assert "1y" in periods
    assert result["dist_max_pct"] == -50.0


def _hyper(monkeypatch, context):
    import requests
    import bellomberg.agents.agent_tools as at
    payload = [{"universe": [{"name": "SYNTH"}]}, [context]]
    monkeypatch.setattr(requests, "post", lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: payload))
    return at.tool_get_hyperliquid_intel("SYNTH", builder_dexs=False)["focus_asset_detail"]


def test_hyperliquid_missing_values_are_not_zero(monkeypatch):
    result = _hyper(monkeypatch, {"markPx": "100", "prevDayPx": "100"})
    assert result["funding_hourly_pct"] is None
    assert result["oi_usd_m"] is None
    assert result["vol_24h_usd_m"] is None
    assert result["premium_basis_pts"] is None


def test_hyperliquid_measured_zero_is_kept(monkeypatch):
    result = _hyper(monkeypatch, {"markPx": "100", "prevDayPx": "100", "funding": "0",
                                  "openInterest": "0", "dayNtlVlm": "0", "premium": "0"})
    assert result["var_24h_pct"] == 0
    assert result["funding_hourly_pct"] == 0


def _quant(monkeypatch, frame, rf=0.05):
    import bellomberg.agents.agent_tools as at
    import bellomberg.cli.price_updater as pu
    import bellomberg.market_data.market_inputs as mi
    monkeypatch.setattr(pu, "data_ticker", lambda ticker: ticker)
    monkeypatch.setattr(at, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(at, "yf", SimpleNamespace(download=lambda *a, **k: frame,
        Ticker=lambda ticker: SimpleNamespace(fast_info={"currency": "USD"})))
    monkeypatch.setattr(mi, "get_risk_free_ex", lambda currency: {
        "value": rf, "source": "synthetic measured RF", "asof": "2026-09-09", "status": "OK"})
    return at


def test_quant_keeps_equity_native_returns_in_mixed_calendar(monkeypatch):
    index = pd.date_range("2026-01-01", periods=60)
    equity = pd.Series(np.where(index.weekday < 5, 100 + np.arange(60) + np.sin(np.arange(60)), np.nan), index=index)
    crypto = pd.Series(100 + np.arange(60) * .4 + np.cos(np.arange(60)), index=index)
    frame = pd.DataFrame({"SYNTH": equity, "COIN-USD": crypto, "SPY": equity})
    result = _quant(monkeypatch, frame).tool_quant_compute("sharpe", ["SYNTH", "COIN-USD"])
    actual = equity.dropna().pct_change(fill_method=None).dropna()
    assert result["SYNTH"]["vol_annualized_pct"] == round(actual.std() * math.sqrt(252) * 100, 2)
    assert result["COIN-USD"]["annualization_days"] == 365
    assert result["SYNTH"]["risk_free"]["source"] == "synthetic measured RF"


def test_quant_no_risk_free_produces_declared_gap(monkeypatch):
    frame = pd.DataFrame({"SYNTH": np.linspace(100, 120, 50)}, index=pd.bdate_range("2026-01-01", periods=50))
    result = _quant(monkeypatch, frame, rf=None).tool_quant_compute("sharpe", ["SYNTH"])
    assert result["SYNTH"]["sharpe_annualized"] is None
    assert "risk_free_note" in result["SYNTH"]


def test_quant_all_nan_ticker_is_declared_without_nan_output(monkeypatch):
    frame = pd.DataFrame({"SYNTH": [np.nan] * 50, "SPY": np.linspace(100, 120, 50)},
                         index=pd.bdate_range("2026-01-01", periods=50))
    result = _quant(monkeypatch, frame).tool_quant_compute("var_cvar", ["SYNTH"])
    assert "error" in result["SYNTH"]


@pytest.mark.parametrize("confidence", [0, 1, float("nan"), True])
def test_quant_rejects_invalid_confidence_before_network(monkeypatch, confidence):
    frame = pd.DataFrame({"SYNTH": np.linspace(100, 120, 50)}, index=pd.bdate_range("2026-01-01", periods=50))
    at = _quant(monkeypatch, frame)
    monkeypatch.setattr(at.yf, "download", lambda *a, **k: pytest.fail("invalid confidence reached provider"))
    result = at.tool_quant_compute("var_cvar", ["SYNTH"], confidence=confidence)
    assert "error" in result


def test_quant_pairs_use_equal_return_intervals(monkeypatch):
    index = pd.date_range("2026-01-01", periods=120)
    common = 100 + np.arange(120) * .3 + np.sin(np.arange(120))
    equity = np.where(index.weekday < 5, common, np.nan)
    frame = pd.DataFrame({"SYNTH": equity, "COIN-USD": common}, index=index)
    result = _quant(monkeypatch, frame).tool_quant_compute("beta", ["COIN-USD"], benchmark="SYNTH")
    assert result["COIN-USD"]["beta_vs_SYNTH"] == pytest.approx(1)
    corr = _quant(monkeypatch, frame).tool_quant_compute("correlation_matrix", ["SYNTH", "COIN-USD"], benchmark="SYNTH")
    assert corr["matrix"]["SYNTH"]["COIN-USD"] == 1


def test_quant_crypto_week_has_seven_observations(monkeypatch):
    frame = pd.DataFrame({"COIN-USD": 100 + np.sin(np.arange(60))},
                         index=pd.date_range("2026-01-01", periods=60))
    out = _quant(monkeypatch, frame).tool_quant_compute("var_cvar", ["COIN-USD"])["COIN-USD"]
    returns = frame["COIN-USD"].pct_change(fill_method=None).dropna()
    expected = np.percentile(returns, 5) * math.sqrt(7) * 100
    assert out["week_var_pct"] == pytest.approx(round(expected, 3))


def test_quant_flat_correlation_is_not_stable_regime(monkeypatch):
    frame = pd.DataFrame({"AAA": np.ones(120) * 100, "BBB": np.arange(120) + 100},
                         index=pd.bdate_range("2026-01-01", periods=120))
    out = _quant(monkeypatch, frame).tool_quant_compute("rolling_corr_break", ["AAA", "BBB"])
    assert "error" in out


def test_quant_applies_exact_download_alias_mapping(monkeypatch):
    import bellomberg.cli.price_updater as pu
    frame = pd.DataFrame({"SOURCE": np.arange(50) + 100, "SPY": np.arange(50) + 100},
                         index=pd.bdate_range("2026-01-01", periods=50))
    at = _quant(monkeypatch, frame)
    monkeypatch.setattr(pu, "data_ticker", lambda ticker: "SOURCE" if ticker == "ALIAS" else ticker)
    out = at.tool_quant_compute("max_drawdown", ["ALIAS"])
    assert out["ALIAS"]["current_dd_pct"] == 0
