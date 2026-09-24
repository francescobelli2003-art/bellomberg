"""COT routing and arithmetic, with synthetic CFTC responses only."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from bellomberg.portfolio import positioning_tools as pt


def _rows(code, name, prefix, count=12, start=None):
    start = start or datetime.now().date()
    return [{
        "cftc_contract_market_code": code,
        "contract_market_name": name,
        "report_date_as_yyyy_mm_dd": (start - timedelta(days=7 * i)).isoformat(),
        "open_interest_all": "1000",
        f"{prefix}_long_all": str(120 - i),
        f"{prefix}_short_all": "20",
    } for i in range(count)]


@pytest.mark.parametrize("market,code,name", [
    ("WTI", "067651", "WTI-PHYSICAL"),
    ("CL", "067651", "WTI-PHYSICAL"),
    ("GOLD", "088691", "GOLD"),
    ("GC", "088691", "GOLD"),
])
def test_commodity_uses_disaggregated_exact_contract_and_managed_money(monkeypatch, market, code, name):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        rows = _rows(code, name, "m_money_positions")
        for row in rows:
            row.update({"prod_merc_positions_long": "30", "prod_merc_positions_short": "20",
                        "swap_positions_long_all": "40", "swap__positions_short_all": "20",
                        "other_rept_positions_long": "15", "other_rept_positions_short": "20"})
        return SimpleNamespace(status_code=200, json=lambda: rows)

    monkeypatch.setattr(pt.requests, "get", fake_get)
    result = pt.get_cot_positioning(market)
    assert len(calls) == 1
    assert calls[0][0].endswith("/72hh-3qpy.json")
    assert code in calls[0][1]["$where"]
    assert result["cftc_contract_market_code"] == code
    assert result["contract_market_name"] == name
    assert result["net_positions"]["managed_money"] == 100
    assert result["net_positions"]["producer_merchant"] == 10
    assert result["net_positions"]["swap_dealers"] == 20
    assert result["net_positions"]["other_reportables"] == -5
    assert result["wow_change"]["managed_money"] == 1
    assert result["managed_money_net_percentile_1y"] == 100
    assert "Disaggregated" in result["_source"]


@pytest.mark.parametrize("market", ["OIL", "CRUDE OIL"])
def test_generic_oil_is_explicitly_ambiguous_without_network(monkeypatch, market):
    monkeypatch.setattr(pt.requests, "get", lambda *a, **kw: pytest.fail("unexpected network"))
    result = pt.get_cot_positioning(market)
    assert "error" in result
    assert "WTI" in result["error"]
    assert "067651" in result["error"]


def test_commodity_rejects_wrong_contract_and_missing_category(monkeypatch):
    def fake_get(url, params, timeout):
        return SimpleNamespace(status_code=200, json=lambda: _rows("067411", "WTI ICE", "m_money_positions"))

    monkeypatch.setattr(pt.requests, "get", fake_get)
    assert "error" in pt.get_cot_positioning("WTI")

    def missing_get(url, params, timeout):
        rows = _rows("067651", "WTI-PHYSICAL", "m_money_positions")
        for row in rows:
            row.pop("m_money_positions_short_all")
        return SimpleNamespace(status_code=200, json=lambda: rows)

    monkeypatch.setattr(pt.requests, "get", missing_get)
    result = pt.get_cot_positioning("WTI")
    assert result["net_positions"]["managed_money"] is None
    assert result["managed_money_net_percentile_1y"] is None


def test_stale_and_short_history_are_declared(monkeypatch):
    rows = _rows("067651", "WTI-PHYSICAL", "m_money_positions", count=2,
                 start=datetime.now().date() - timedelta(days=30))
    monkeypatch.setattr(pt.requests, "get", lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: rows))
    result = pt.get_cot_positioning("WTI")
    assert result["freshness"]["status"] == "STALE"
    assert result["managed_money_net_percentile_1y"] is None
    assert result["reading"] is None


def test_financial_market_keeps_tff_categories(monkeypatch):
    rows = _rows("13874A", "SYNTH FUTURE", "lev_money")
    monkeypatch.setattr(pt.requests, "get", lambda url, params, timeout: SimpleNamespace(
        status_code=200, json=lambda: rows if url.endswith("/gpe5-46if.json") else pytest.fail(url)))
    result = pt.get_cot_positioning("SYNTH FUTURE")
    assert result["net_positions"]["leveraged_funds"] == 100
    assert result["leveraged_funds_net_percentile_1y"] == 100
    assert "TFF" in result["_source"]


def test_dispatch_uses_actual_cftc_report_source(monkeypatch):
    from bellomberg.agents import chat_tools

    monkeypatch.setattr(pt, "get_cot_positioning", lambda market: {
        "market_query": market, "_source": "CFTC publicreporting.cftc.gov (Disaggregated futures-only)"})
    result = chat_tools.dispatch("get_cot_positioning", {"market": "WTI"})
    assert "Disaggregated" in result["_source"]
    assert result["data"]["market_query"] == "WTI"
