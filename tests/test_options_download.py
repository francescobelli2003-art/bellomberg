"""Complete downloads use synthetic Polygon pages; never the network or DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import sys
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from bellomberg.market_data import polygon_data as provider
from bellomberg.portfolio import vol_surface as vol


def exp(days=7):
    return (date.today() + timedelta(days=days)).isoformat()


def contracts(expiry, page=0, count=14):
    return [{"details": {"ticker": f"O:TEST-{expiry}-{page}-{i}", "expiration_date": expiry,
                         "contract_type": "put" if i % 2 else "call", "strike_price": 80 + (i // 2) * 7},
             "implied_volatility": .2, "open_interest": 10, "day": {"volume": 1},
             "underlying_asset": {"price": 100}} for i in range(count)]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(provider, "polygon_available", lambda: True)
    monkeypatch.setattr(provider, "_get", lambda *a: pytest.fail("unexpected provider request"))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda symbol: SimpleNamespace(fast_info={"lastPrice": 100})))
    vol._CHAIN_CACHE.clear()


def manager():
    from bellomberg.portfolio.options_download import OptionsDownloadManager
    return OptionsDownloadManager()


def settled(mgr, job_id, state=None):
    end = monotonic() + 5
    while monotonic() < end:
        status = mgr.status(job_id)
        if (state and status["state"] == state) or (not state and status["state"] in {"error", "complete", "paused"}):
            return status
        sleep(.005)
    pytest.fail(f"download did not settle: {status}")


def test_explicit_surface_loads_more_than_eight_dates_and_two_pages(monkeypatch):
    dates = [exp(i + 7) for i in range(10)]
    requests = []
    def get(path, params):
        cursor = params.get("cursor")
        expiry, page = cursor.split("_") if cursor else (params["expiration_date"], "0")
        page = int(page)
        requests.append((expiry, page))
        return {"results": contracts(expiry, page), **({"next_url": f"https://api.polygon.io/x?cursor={expiry}_{page+1}"} if page < 3 else {})}
    monkeypatch.setattr(provider, "_get", get)
    out = vol.build_vol_surface("TEST", expiries=dates, include_context=False)
    assert out["coverage"]["complete"], out
    assert out["n_expiries"] == 10
    assert len(requests) == 40 and all(r["n_contracts"] == 56 for r in out["coverage"]["rows"])


def test_job_exhausts_catalog_including_zero_day_and_far_dates(monkeypatch):
    dates = [exp(i) for i in range(10)] + [exp(1500)]
    def get(path, params):
        if "reference" in path:
            remaining = [e for e in dates if e > params["expiration_date.gt"]] if "expiration_date.gt" in params else dates
            return {"results": [{"expiration_date": remaining[0]}] if remaining else []}
        return {"results": contracts(params["expiration_date"])}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("test")
    status = settled(mgr, job["id"])
    assert status["download_complete"] and status["catalog_complete"]
    assert status["expirations"] == dates and status["n_contracts"] == 154
    assert mgr.chain(job["id"], dates[0])["n_contracts"] == 14
    surface = mgr.surface(job["id"])
    assert surface["coverage"]["download_complete"]
    assert not surface["coverage"]["complete"]  # 0/1 DTE are data, not interpolated curves.
    assert surface["n_expiries"] == 9


def test_pause_preserves_inflight_page_and_resume_keeps_cursor(monkeypatch):
    entered, release = Event(), Event(); calls = []
    def get(path, params):
        cursor = params.get("cursor"); calls.append(cursor)
        if not cursor:
            entered.set(); assert release.wait(3)
        rows = contracts(exp(), int(cursor or 0))
        for row in rows: row.pop("underlying_asset")
        return {"results": rows, **({"next_url": "https://api.polygon.io/x?cursor=1"} if not cursor else {})}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("TEST", [exp()]); assert entered.wait(3)
    mgr.pause(job["id"]); release.set()
    paused = settled(mgr, job["id"], "paused")
    assert paused["n_contracts"] == 14 and not paused["download_complete"]
    assert mgr.surface(job["id"])["spot_est"] == 100
    mgr.resume(job["id"]); done = settled(mgr, job["id"])
    assert done["download_complete"] and done["n_contracts"] == 28 and calls == [None, "1"]


def test_intermediate_429_keeps_data_and_retries_failed_cursor_only(monkeypatch):
    failed = False; calls = []
    def get(path, params):
        nonlocal failed
        cursor = params.get("cursor"); calls.append(cursor)
        if cursor == "1" and not failed:
            failed = True
            return {"error": "HTTP 429"}
        return {"results": contracts(exp(), int(cursor or 0)), **({"next_url": "https://api.polygon.io/x?cursor=1"} if not cursor else {})}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("TEST", [exp()]); bad = settled(mgr, job["id"])
    assert bad["state"] == "error" and "429" in bad["error"] and bad["retryable"]
    assert mgr.chain(job["id"], exp())["n_contracts"] == 14
    mgr.resume(job["id"]); done = settled(mgr, job["id"])
    assert done["download_complete"] and calls == [None, "1", "1"]


def test_duplicate_pages_are_deduplicated_and_cursor_cycle_is_declared(monkeypatch):
    def get(path, params):
        cursor = params.get("cursor")
        nxt = {None: "1", "1": "2", "2": "1"}[cursor]
        return {"results": contracts(exp()), "next_url": f"https://api.polygon.io/x?cursor={nxt}"}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("TEST", [exp()]); bad = settled(mgr, job["id"])
    assert bad["state"] == "error" and not bad["download_complete"]
    assert "ciclo" in bad["error"] and bad["n_contracts"] == 14 and bad["duplicates"] == 28
    assert mgr.chain(job["id"], exp())["duplicates"] == 28


def test_malformed_page_never_claims_complete_and_keeps_valid_rows(monkeypatch):
    monkeypatch.setattr(provider, "_get", lambda *a: {"results": contracts(exp()) + [None, {"details": {}}]})
    mgr = manager(); job = mgr.start("TEST", [exp()]); status = settled(mgr, job["id"])
    assert status["state"] == "error" and not status["download_complete"]
    assert status["malformed_contracts"] == 2 and status["n_contracts"] == 14
    assert mgr.chain(job["id"], exp())["malformed_contracts"] == 2


def test_chain_filter_and_pagination_cover_entire_download_and_missing_greeks(monkeypatch):
    monkeypatch.setattr(provider, "_get", lambda *a: {"results": contracts(exp(), count=600)})
    mgr = manager(); job = mgr.start("TEST", [exp()]); settled(mgr, job["id"])
    view = mgr.chain(job["id"], exp(), side="put", strike="2173", limit=1)
    assert view["n_contracts"] == 600 and view["filtered_contracts"] == 1
    assert view["chain"][0]["strike"] == 2173 and view["chain"][0]["delta"] is None
    assert view["chain_complete"] and not view["has_more"]
    first = mgr.chain(job["id"], exp(), limit=250)
    assert first["has_more"] and first["next_offset"] == 250
    assert len(mgr.chain(job["id"], exp(), offset=500)["chain"]) == 100


def test_surface_reuses_opra_spot_and_never_refetches(monkeypatch):
    quotes = []
    def ticker(symbol):
        quotes.append(symbol); return SimpleNamespace(fast_info={"lastPrice": 123})
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=ticker))
    rows = contracts(exp())
    for row in rows: row.pop("underlying_asset")
    monkeypatch.setattr(provider, "_get", lambda *a: {"results": rows})
    mgr = manager(); job = mgr.start("TEST", [exp()]); status = settled(mgr, job["id"])
    monkeypatch.setattr(provider, "_get", lambda *a: pytest.fail("surface refetched chain"))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda *a: pytest.fail("surface refetched spot")))
    out = mgr.surface(job["id"])
    assert out["spot_est"] == 123 and out["spot_source"] == "yfinance lastPrice"
    assert quotes == ["TEST"] and status["spot_timestamp"] and status["snapshot_at"]


def test_same_page_concurrent_read_is_singleflight_and_cache_isolated(monkeypatch):
    entered, release = Event(), Event(); calls = []
    def get(path, params):
        calls.append(params); entered.set(); assert release.wait(3)
        return {"results": contracts(exp())}
    monkeypatch.setattr(provider, "_get", get)
    with ThreadPoolExecutor(2) as executor:
        a = executor.submit(vol.get_chain_detail, "TEST", exp()); assert entered.wait(3)
        b = executor.submit(vol.get_chain_detail, "TEST", exp()); sleep(.02); release.set()
        left, right = a.result(), b.result()
    assert len(calls) == 1 and left["complete"] and right["complete"]
    left["chain"][0]["strike"] = 999
    assert vol.get_chain_detail("TEST", exp())["chain"][0]["strike"] == 80


def test_asymmetric_chain_is_downloaded_but_has_no_surface(monkeypatch):
    rows = contracts(exp())
    for row in rows: row["details"]["contract_type"] = "call"
    monkeypatch.setattr(provider, "_get", lambda *a: {"results": rows})
    mgr = manager(); job = mgr.start("TEST", [exp()]); done = settled(mgr, job["id"])
    out = mgr.surface(job["id"])
    assert done["download_complete"] and out["coverage"]["download_complete"]
    assert not out["coverage"]["complete"] and out["slices"] == []
    assert "IV/liquid" in out["coverage"]["rows"][0]["reason"]


def test_resume_rechecks_cyclic_page_after_provider_is_repaired(monkeypatch):
    repaired = False; calls = []
    def get(path, params):
        cursor = params.get("cursor"); calls.append(cursor)
        nxt = {None:"1", "1":"2", "2":"1"}[cursor]
        return {"results": contracts(exp(), int(cursor or 0)),
                **({} if repaired and cursor == "2" else {"next_url": f"https://api.polygon.io/x?cursor={nxt}"})}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("TEST", [exp()]); assert settled(mgr, job["id"])["state"] == "error"
    repaired = True
    mgr.resume(job["id"]); done = settled(mgr, job["id"])
    assert done["download_complete"] and done["n_contracts"] == 42
    assert calls == [None, "1", "2", "2"]


def test_catalog_error_resume_does_not_restart_discovery(monkeypatch):
    calls = []; failed = False
    def get(path, params):
        nonlocal failed
        if "reference" not in path:
            return {"results": contracts(params["expiration_date"])}
        after = params.get("expiration_date.gt"); calls.append(after)
        if after == exp() and not failed:
            failed = True
            return {"error":"HTTP 429"}
        return {"results": [{"expiration_date": exp()}] if after is None else []}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("TEST"); bad = settled(mgr, job["id"])
    assert bad["expirations"] == [exp()] and not bad["catalog_complete"]
    mgr.resume(job["id"]); done = settled(mgr, job["id"])
    assert done["download_complete"] and calls == [None, exp(), exp()]


def test_malformed_retry_replaces_error_count_and_keeps_received_contracts(monkeypatch):
    repaired = False
    def get(*args):
        return {"results": contracts(exp()) + ([] if repaired else [None])}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("TEST", [exp()]); bad = settled(mgr, job["id"])
    assert bad["n_contracts"] == 14 and bad["malformed_contracts"] == 1
    repaired = True
    mgr.resume(job["id"]); done = settled(mgr, job["id"])
    assert done["download_complete"] and done["n_contracts"] == 14 and done["duplicates"] == 0
    assert done["malformed_contracts"] == 0


def test_worker_limit_and_queued_pause_do_not_fetch_another_ticker(monkeypatch):
    from bellomberg.portfolio.options_download import OptionsDownloadManager
    entered, release = Event(), Event(); tickers = []
    def get(path, params):
        tickers.append(path.rsplit("/", 1)[-1]); entered.set(); assert release.wait(3)
        return {"results": contracts(exp())}
    monkeypatch.setattr(provider, "_get", get)
    mgr = OptionsDownloadManager(max_concurrent=1)
    first = mgr.start("FIRST", [exp()]); assert entered.wait(3)
    second = mgr.start("SECOND", [exp()]); mgr.pause(second["id"])
    assert mgr.status(second["id"])["state"] in ("queued", "paused") and tickers == ["FIRST"]
    release.set(); assert settled(mgr, first["id"])["download_complete"]
    assert settled(mgr, second["id"])["state"] == "paused" and tickers == ["FIRST"]
    mgr.resume(second["id"]); assert settled(mgr, second["id"])["ticker"] == "SECOND"
    assert tickers == ["FIRST", "SECOND"]


def test_cached_snapshot_is_reused_then_inactive_job_expires(monkeypatch):
    from bellomberg.portfolio import options_download
    monkeypatch.setattr(provider, "_get", lambda *a: {"results": contracts(exp())})
    mgr = manager(); job = mgr.start("TEST", [exp()]); settled(mgr, job["id"])
    monkeypatch.setattr(provider, "_get", lambda *a: pytest.fail("fresh snapshot refetched"))
    assert mgr.start("TEST", [exp()])["id"] == job["id"]
    now = monotonic()
    monkeypatch.setattr(options_download, "monotonic", lambda: now + 3601)
    with pytest.raises(KeyError, match="scaduto"):
        mgr.status(job["id"])


def test_provider_exception_is_visible_without_leaking_exception_details(monkeypatch):
    def get(*a):
        raise RuntimeError("https://provider.invalid?apiKey=SYNTHETIC_SECRET")
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("TEST", [exp()]); status = settled(mgr, job["id"])
    assert status["state"] == "error" and status["retryable"] and not status["download_complete"]
    assert "RuntimeError" in status["error"] and "SYNTHETIC_SECRET" not in str(status)


def test_old_cached_pages_do_not_gain_freshness_from_a_new_job(monkeypatch):
    from datetime import datetime, timezone
    from bellomberg.portfolio import options_download
    monkeypatch.setattr(provider, "_get", lambda *a: {"results": contracts(exp())})
    vol.get_chain_detail("TEST", exp())
    cached = vol._CHAIN_CACHE[("TEST", exp(), None)]
    cached[1]["_timestamp"] = (datetime.now(timezone.utc) - timedelta(seconds=110)).isoformat()
    mgr = manager(); job = mgr.start("TEST", [exp()]); settled(mgr, job["id"])
    now = monotonic()
    monkeypatch.setattr(options_download, "monotonic", lambda: now + 20)
    assert mgr.status(job["id"])["stale"]


def test_resume_during_worker_error_unwind_is_not_lost(monkeypatch):
    attempts = []
    def get(*args):
        attempts.append(1)
        return {"error":"HTTP 429"} if len(attempts) == 1 else {"results":contracts(exp())}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); original = mgr._fail
    def fail(job, error):
        original(job, error)
        # Trigger a reentrant user retry at the boundary before the worker exits.
        mgr.resume(job["id"])
    monkeypatch.setattr(mgr, "_fail", fail)
    job = mgr.start("TEST", [exp()]); done = settled(mgr, job["id"])
    assert done["download_complete"] and len(attempts) == 2


def test_empty_catalog_surface_retains_full_coverage_contract(monkeypatch):
    monkeypatch.setattr(provider, "_get", lambda *a: {"results": []})
    mgr = manager(); job = mgr.start("TEST"); assert settled(mgr, job["id"])["download_complete"]
    out = mgr.surface(job["id"])
    assert out["coverage"]["download_complete"] and not out["coverage"]["complete"]
    assert all(out["coverage"][key] == [] for key in ("requested", "loaded", "excluded", "errors", "rows"))


def test_ticker_jobs_and_page_caches_are_independent_including_dotted_symbol(monkeypatch):
    requests = []
    def get(path, params):
        symbol = path.rsplit("/", 1)[-1]; requests.append(symbol)
        rows = contracts(exp(), count=14 if symbol == "TESTB" else 20)
        for row in rows:
            row["details"]["ticker"] = row["details"]["ticker"].replace("TEST-", f"{symbol}-")
            if symbol == "DEMO.X":
                row["details"]["strike_price"] += 1000
        return {"results": rows}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager()
    first = mgr.start("TESTB", [exp()]); second = mgr.start("demo.x", [exp()])
    assert settled(mgr, first["id"])["download_complete"]
    assert settled(mgr, second["id"])["download_complete"]
    a = mgr.chain(first["id"], exp()); b = mgr.chain(second["id"], exp())
    assert a["ticker"] == "TESTB" and a["n_contracts"] == 14 and a["chain"][0]["strike"] == 80
    assert b["ticker"] == "DEMO.X" and b["n_contracts"] == 20 and b["chain"][0]["strike"] == 1080
    assert a["chain"][0]["contract"].startswith("O:TESTB-")
    assert b["chain"][0]["contract"].startswith("O:DEMO.X-")
    assert vol.get_chain_detail("TESTB", exp())["n_contracts"] == 14
    assert vol.get_chain_detail("DEMO.X", exp())["n_contracts"] == 20
    assert sorted(requests) == ["DEMO.X", "TESTB"]


def test_retry_replaces_rejected_page_without_mixing_old_contracts(monkeypatch):
    repaired = False
    def get(path, params):
        return {"results": contracts(exp(), page=2 if repaired else 1, count=1) + ([] if repaired else [None])}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("TEST", [exp()]); assert settled(mgr, job["id"])["state"] == "error"
    assert mgr.chain(job["id"], exp())["chain"][0]["contract"].endswith("-1-0")
    repaired = True
    mgr.resume(job["id"]); done = settled(mgr, job["id"])
    rows = mgr.chain(job["id"], exp())["chain"]
    assert done["download_complete"] and done["n_contracts"] == 1 and done["malformed_contracts"] == 0
    assert [row["contract"] for row in rows] == [f"O:TEST-{exp()}-2-0"]
    assert done["superseded_contracts"] == 1


def test_repaired_page_retains_contract_from_another_accepted_page(monkeypatch):
    repaired = False
    def get(path, params):
        cursor = params.get("cursor")
        if not cursor:
            return {"results": contracts(exp(), page=0, count=1), "next_url":"https://api.polygon.io/x?cursor=1"}
        old = contracts(exp(), page=0, count=1) + contracts(exp(), page=1, count=1) + [None]
        return {"results": contracts(exp(), page=2, count=1) if repaired else old}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); job = mgr.start("TEST", [exp()]); bad = settled(mgr, job["id"])
    assert bad["state"] == "error" and bad["duplicates"] == 1
    repaired = True
    mgr.resume(job["id"]); done = settled(mgr, job["id"])
    contracts_received = {row["contract"] for row in mgr.chain(job["id"], exp())["chain"]}
    assert done["download_complete"] and done["n_contracts"] == 2 and done["duplicates"] == 0
    assert contracts_received == {f"O:TEST-{exp()}-0-0", f"O:TEST-{exp()}-2-0"}


def test_resume_after_worker_observed_pause_before_finally_restarts(monkeypatch):
    def get(path, params):
        cursor = params.get("cursor")
        return {"results": contracts(exp(), page=1 if cursor else 0, count=1),
                **({} if cursor else {"next_url":"https://api.polygon.io/x?cursor=1"})}
    monkeypatch.setattr(provider, "_get", get)
    mgr = manager(); accept, release = mgr._accept_page, mgr._slots.release
    resumed = Event()
    def accept_and_pause(job, row, cursor, page):
        accept(job, row, cursor, page)
        if cursor is None:
            mgr.pause(job["id"])
    def release_and_resume(*args, **kwargs):
        if not resumed.is_set():
            resumed.set()
            job_id = next(iter(mgr._jobs))
            mgr.resume(job_id)
        return release(*args, **kwargs)
    monkeypatch.setattr(mgr, "_accept_page", accept_and_pause)
    monkeypatch.setattr(mgr._slots, "release", release_and_resume)
    job = mgr.start("TEST", [exp()]); done = settled(mgr, job["id"])
    assert resumed.is_set() and done["download_complete"] and done["n_contracts"] == 2
