from datetime import date, timedelta
import sys
from types import SimpleNamespace
import pytest
from bellomberg.portfolio import vol_surface as vol
from bellomberg.market_data import polygon_data as provider


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(provider, "polygon_available", lambda: True)
    monkeypatch.setattr(provider, "_get", lambda *a, **k: pytest.fail("unexpected provider request"))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda ticker: SimpleNamespace(fast_info={"lastPrice": None})))
    vol._CHAIN_CACHE.clear()


def expiry(days):
    return (date.today() + timedelta(days=days)).isoformat()


def test_catalog_exposes_budget_and_resumes_without_skipping_expiries(monkeypatch):
    dates = [expiry(0), expiry(1), expiry(7), expiry(150), expiry(400)]
    queries = []
    def get(path, params):
        queries.append(params)
        remaining = [x for x in dates if x > params["expiration_date.gt"]] if "expiration_date.gt" in params else dates
        return {"results": [{"expiration_date": remaining[0]}] if remaining else []}
    monkeypatch.setattr(provider, "_get", get)
    first = vol.get_expiry_catalog("XX01", request_budget=3)
    assert first["expirations"] == dates[:3] and not first["complete"]
    second = vol.get_expiry_catalog("XX01", after=first["next_after"], request_budget=3)
    assert second["expirations"] == dates[3:] and second["complete"]
    assert all(x["limit"] == 1 for x in queries)
    assert first["requests_used"] == second["requests_used"] == 3


def test_catalog_partial_error_does_not_erase_discovered_dates(monkeypatch):
    replies = iter([{"results": [{"expiration_date": expiry(7)}]}, {"error": "HTTP 429"}])
    monkeypatch.setattr(provider, "_get", lambda *a: next(replies))
    out = vol.get_expiry_catalog("XX01")
    assert out["expirations"] == [expiry(7)] and not out["complete"]
    assert out["error"] == "HTTP 429" and out["requests_used"] == 2


def test_chain_preserves_zero_greeks_missing_quotes_and_pagination(monkeypatch):
    raw = {"details": {"ticker": "O:XX010001", "contract_type": "put", "strike_price": 100, "expiration_date": expiry(7)},
           "greeks": {"delta": 0, "gamma": 0}, "implied_volatility": .2}
    calls = []
    def get(path, params):
        calls.append(params)
        return {"results": [raw], "next_url": "https://api.polygon.io/v3/snapshot/options/XX01?cursor=next-page"}
    monkeypatch.setattr(provider, "_get", get)
    out = vol.get_chain_detail("XX01", expiry(7))
    row = out["chain"][0]
    assert row["delta"] == row["gamma"] == 0
    assert row["vega"] is None and row["bid"] is None and row["mid"] is None
    assert row["multiplier"] is None and row["quality"]
    assert out["next_cursor"] == "next-page" and not out["complete"]
    assert vol.get_chain_detail("XX01", expiry(7))["cached"]
    assert len(calls) == 1


def test_chain_wrong_expiry_cursor_and_zero_quotes_are_not_valid_data(monkeypatch):
    raw = {"details": {"expiration_date": expiry(8)}, "last_quote": {"bid": 0, "ask": 0}}
    assert vol._contract_row(raw)["mid"] is None
    monkeypatch.setattr(provider, "_get", lambda *a: {"results": [raw]})
    assert "diversa" in vol.get_chain_detail("XX01", expiry(7))["error"]
    with pytest.raises(ValueError, match="cursore"):
        vol.get_chain_detail("XX01", expiry(7), "https://example.com")


def test_malformed_nested_fields_stay_missing_and_adjusted_contract_is_flagged():
    row = vol._contract_row({"details": [], "greeks": "invalid", "last_quote": 1, "day": None})
    assert row["strike"] is None and row["delta"] is None and row["bid"] is None
    adjusted = vol._contract_row({"details": {"additional_underlyings": [{"ticker": "DEMO"}]}})
    assert adjusted["adjusted"]
    assert any("rettificato" in s for s in adjusted["quality"])


def test_surface_records_every_selected_expiry_and_does_not_fetch_short_expiry(monkeypatch):
    dates = [expiry(0), expiry(7), expiry(14), expiry(21)]
    fetched = []
    def chain(ticker, exp, cursor=None):
        fetched.append(exp)
        if exp == dates[1]:
            return {"error": "HTTP 403", "chain": []}
        if exp == dates[2]:
            return {"chain": [], "spot": 100, "complete": True}
        rows = [{"type": kind, "strike": k, "iv": .2, "oi": 10, "volume": 1}
                for kind in ("call", "put") for k in (80, 90, 95, 100, 105, 110, 120)]
        return {"chain": rows, "spot": 100, "complete": False}
    monkeypatch.setattr(vol, "get_chain_detail", chain)
    out = vol.build_vol_surface("XX01", expiries=dates, include_context=False)
    assert fetched == dates[1:]
    assert len(out["coverage"]["rows"]) == len(dates)
    assert [r["status"] for r in out["coverage"]["rows"]] == ["excluded", "error", "excluded", "partial"]
    assert "403" in out["coverage"]["rows"][1]["reason"]
    assert out["n_expiries"] == 1 and not out["coverage"]["complete"]


def test_surface_all_failed_retains_coverage_and_rejects_duplicate_expiries(monkeypatch):
    monkeypatch.setattr(vol, "get_chain_detail", lambda *a: {"error": "HTTP 429"})
    out = vol.build_vol_surface("XX01", expiries=[expiry(7)], include_context=False)
    assert out["error"] and out["coverage"]["errors"] == [expiry(7)]
    with pytest.raises(ValueError):
        vol.build_vol_surface("XX01", expiries=[expiry(7), expiry(7)])


def _opra_snapshot(exp):
    return [{"details": {"ticker": f"O:XX01-{exp}-{kind}-{strike}",
                         "contract_type": kind, "strike_price": strike,
                         "expiration_date": exp},
             "underlying_asset": {"ticker": "XX01"},
             "greeks": {"delta": .5 if kind == "call" else -.5},
             "implied_volatility": .2, "open_interest": 10,
             "day": {"volume": 1}}
            for kind in ("call", "put") for strike in (80, 90, 95, 100, 105, 110, 120)]


def test_explicit_surface_opra_without_spot_reads_yfinance_once_without_context(monkeypatch):
    dates = [expiry(days) for days in (7, 14, 21, 28)]
    yf_calls, chain_calls = [], []

    def ticker(symbol):
        yf_calls.append(symbol)
        return SimpleNamespace(fast_info={"lastPrice": 100.0})

    def snapshot(path, params):
        chain_calls.append((path, params["expiration_date"]))
        return {"results": _opra_snapshot(params["expiration_date"])}

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=ticker))
    monkeypatch.setattr(provider, "_get", snapshot)
    out = vol.build_vol_surface("XX01", expiries=dates, include_context=False)

    assert not out.get("error"), out
    assert out["spot_est"] == 100.0 and out["spot_source"] == "yfinance lastPrice"
    assert out["coverage"]["complete"] and out["coverage"]["loaded"] == dates
    assert out["n_expiries"] == 4 and len(out["slices"]) == 4
    assert all(row["iv_grid"][8] == .2 for row in out["slices"])
    assert out["context_requested"] is False and out["realized_vol_30d"] is None
    assert yf_calls == ["XX01"]
    assert chain_calls == [("/v3/snapshot/options/XX01", exp) for exp in dates]


@pytest.mark.parametrize("raises", [False, True])
def test_explicit_surface_without_either_observed_spot_never_uses_strike_proxy(monkeypatch, raises):
    dates = [expiry(7), expiry(14)]
    yf_calls = []

    def ticker(symbol):
        yf_calls.append(symbol)
        if raises:
            raise RuntimeError("synthetic quote unavailable")
        return SimpleNamespace(fast_info={"lastPrice": None})

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=ticker))
    monkeypatch.setattr(provider, "_get", lambda path, params: {
        "results": _opra_snapshot(params["expiration_date"])})
    out = vol.build_vol_surface("XX01", expiries=dates, include_context=False)

    assert out["error"] and out["slices"] == []
    assert out["coverage"]["errors"] == dates and out["coverage"]["loaded"] == []
    assert all("spot osservato assente" in row["reason"] for row in out["coverage"]["rows"])
    assert out.get("spot_est") is None and not str(out.get("spot_source", "")).startswith("PROXY")
    assert yf_calls == ["XX01"]


@pytest.mark.parametrize("first", [{}, {"ticker": "XX01"}, {"price": 0}, {"price": float("nan")}])
def test_chain_finds_later_valid_underlying_price_with_its_own_provenance(monkeypatch, first):
    exp = expiry(7)
    rows = _opra_snapshot(exp)
    rows[0]["underlying_asset"] = first
    rows[1]["underlying_asset"] = {"ticker": "XX01", "price": 100.0,
                                     "timeframe": "DELAYED", "last_updated": 1700000000000000000}
    monkeypatch.setattr(provider, "_get", lambda *args: {"results": rows})

    out = vol.get_chain_detail("XX01", exp)

    assert out["spot"] == 100.0
    assert out["spot_timeframe"] == "DELAYED"
    assert out["spot_timestamp_ns"] == 1700000000000000000
