"""Opening balances must not become missing/negative positions in attribution."""
from copy import deepcopy
import time

import pandas as pd
import pytest

from bellomberg.core.language import language_context
from bellomberg.core.presentation import render_payload
from bellomberg.portfolio import portfolio_analytics as analytics
from bellomberg.portfolio import portfolio_attribution as attribution
from bellomberg.storage.memory_db import MemoryDB


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self: None)
    db = MemoryDB(db_path=str(tmp_path / "attribution.db"), chroma_path=str(tmp_path / "chroma"))
    monkeypatch.setattr(analytics, "MemoryDB", lambda: db)
    monkeypatch.setattr(attribution, "_CACHE", {})
    return db


def opening(db, ticker="OPEN.MI", as_of="2024-01-31"):
    values = dict(ticker=ticker, quantita=10, prezzo_medio=20, valuta="EUR",
                  as_of=as_of, provenienza="Estratto broker sintetico", nota="Acquisti ignoti")
    preview = db.prepare_position_opening(**values)
    return db.create_position_opening(expected_context=preview["expected_context"], **values)


def dump(db):
    with db._conn() as conn:
        return list(conn.iterdump())


@pytest.mark.parametrize("sold", [0, 4, 10])
@pytest.mark.parametrize("language", ["it", "en"])
def test_opening_balance_is_guarded_before_any_market_work(ledger, monkeypatch, sold, language):
    saved = opening(ledger)
    if sold:
        ledger.log_trade("OPEN.MI", "SELL", sold, 30, valuta="EUR", data="2024-02-02")
    # A separate, fully documented holding must not silently become the whole book.
    ledger.log_trade("TRADE.MI", "BUY", 1, 100, valuta="EUR", data="2024-01-15")
    with ledger._conn() as conn:
        assert conn.execute("SELECT quantita FROM positions WHERE ticker='OPEN.MI'").fetchone()[0] == 10 - sold
        assert conn.execute("SELECT COUNT(*) FROM trade_history WHERE ticker='OPEN.MI' AND action='BUY'").fetchone()[0] == 0
    calls = []
    def prices(*args):
        calls.append(args)
        return pd.DataFrame({"OPEN.MI": [20., 22., 24.], "TRADE.MI": [100., 110., 121.]},
                            index=pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"]))
    monkeypatch.setattr(attribution, "_download_prices_for_history", prices)
    before = dump(ledger)
    with language_context(language):
        result = attribution.compute_attribution("MTD", "2024-02-02", fx=pd.DataFrame(),
            official_series={"error": "Synthetic unavailable"}, fetch=lambda _: {})
    assert result.get("error_code") == "OPENING_ATTRIBUTION_UNSUPPORTED"
    assert result["position_openings"] == [saved]
    assert result["baseline_added_at"] == saved["created_at"]
    assert "portfolio_return_pct" not in result
    assert "by_position" not in result and "excluded" not in result
    assert calls == []
    assert dump(ledger) == before
    assert ("opening balances" if language == "en" else "saldi iniziali") in result["error"]


def test_openings_are_checked_before_a_preexisting_success_cache(ledger):
    key = "attrib:MTD:2024-02-02"
    cached = {"portfolio_return_pct": 10., "by_position": [{"ticker": "OLD.MI"}]}
    attribution._CACHE[key] = {"ts": time.time(), "data": cached}
    assert attribution.compute_attribution("MTD", "2024-02-02") == cached
    first = opening(ledger)
    second = opening(ledger, "SECOND.MI", "2024-01-15")
    with ledger._conn() as conn:
        conn.execute("UPDATE position_openings SET created_at='2024-02-03T10:00:00+00:00' WHERE ticker='OPEN.MI'")
        conn.execute("UPDATE position_openings SET created_at='2024-02-05T10:00:00+00:00' WHERE ticker='SECOND.MI'")
    before = dump(ledger)
    with language_context("it"):
        italian = attribution.compute_attribution("MTD", "2024-02-02")
    with language_context("en"):
        english = attribution.compute_attribution("MTD", "2024-02-02")
    assert italian.get("error_code") == english.get("error_code") == "OPENING_ATTRIBUTION_UNSUPPORTED"
    assert english == render_payload(italian, language="en")
    assert english["baseline_added_at"] == "2024-02-05T10:00:00+00:00"
    assert [row["as_of"] for row in english["position_openings"]] == [first["as_of"], second["as_of"]]
    assert attribution._CACHE[key]["data"] == cached
    assert dump(ledger) == before


def test_opening_read_failure_cannot_serve_cached_success(ledger, monkeypatch):
    attribution._CACHE["attrib:MTD:2024-02-02"] = {"ts": time.time(), "data": {"portfolio_return_pct": 10.}}
    def fail():
        raise RuntimeError("synthetic baseline read failure")
    monkeypatch.setattr(ledger, "get_opening_positions", fail)
    before = dump(ledger)
    result = attribution.compute_attribution("MTD", "2024-02-02")
    assert result.get("error_code") == "OPENING_ATTRIBUTION_UNAVAILABLE"
    assert "synthetic baseline read failure" in result["error"]
    assert "portfolio_return_pct" not in result
    assert dump(ledger) == before


def test_explicit_synthetic_openings_are_copied_without_db_or_market(monkeypatch):
    def forbidden():
        pytest.fail("Injected ledger must not read the runtime DB")
    monkeypatch.setattr(analytics, "MemoryDB", forbidden)
    rows = [{"ticker": "SYNTH.MI", "quantita": 10, "as_of": "2024-01-31",
             "created_at": "2024-02-05T10:00:00+00:00", "provenienza": "Original source"}]
    before = deepcopy(rows)
    result = attribution.compute_attribution(trades=[], openings=rows)
    assert result["error_code"] == "OPENING_ATTRIBUTION_UNSUPPORTED"
    result["position_openings"][0]["provenienza"] = "Mutated display"
    assert rows == before


@pytest.mark.parametrize("change", ["added", "read_failed"])
def test_baseline_change_during_calculation_never_publishes_or_caches_a_return(ledger, monkeypatch, change):
    ledger.log_trade("TRADE.MI", "BUY", 1, 100, valuta="EUR", data="2024-01-15")
    calls = []
    def prices(*args):
        calls.append(args)
        if change == "added":
            opening(ledger)
        else:
            def fail():
                raise RuntimeError("baseline changed while calculating")
            monkeypatch.setattr(ledger, "get_opening_positions", fail)
        return pd.DataFrame({"TRADE.MI": [100., 110., 121.]},
                            index=pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"]))
    monkeypatch.setattr(attribution, "_download_prices_for_history", prices)
    result = attribution.compute_attribution("MTD", "2024-02-02", fx=pd.DataFrame(),
        official_series={"error": "Synthetic unavailable"}, fetch=lambda _: {})
    expected = "OPENING_ATTRIBUTION_UNSUPPORTED" if change == "added" else "OPENING_ATTRIBUTION_UNAVAILABLE"
    assert result.get("error_code") == expected
    assert "portfolio_return_pct" not in result
    assert attribution._CACHE == {}
    assert len(calls) == 1


def test_injected_trade_only_contract_keeps_hand_calculated_return(monkeypatch):
    def forbidden():
        pytest.fail("Injected trade ledger must not read the runtime DB")
    monkeypatch.setattr(analytics, "MemoryDB", forbidden)
    trades = [{"ticker": "SYNTH.MI", "action": "BUY", "quantita": 10, "prezzo": 100.,
               "valuta": "EUR", "data": "2024-01-15"}]
    prices = pd.DataFrame({"SYNTH.MI": [100., 110., 121.]},
                         index=pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"]))
    result = attribution.compute_attribution("MTD", "2024-02-02", trades=trades,
        prices=prices, fx=pd.DataFrame(), fetch=lambda _: {})
    assert result.get("error") is None
    assert result["portfolio_return_pct"] == 21.0
    assert result["by_position"][0]["contribution_pct"] == 21.0
