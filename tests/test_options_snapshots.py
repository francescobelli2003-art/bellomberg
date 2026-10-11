"""Archivio su file delle istantanee opzioni (10/10/2026, Opus 5.5).

Pagine Polygon SINTETICHE (ticker ZZ*), mai la rete, mai il DB del portafoglio. La data
dir e' sempre una cartella di prova (`tmp_path` + BELLOMBERG_DATA_DIR di prova). Oracolo
scritto qui: nomi dei file, conteggi, campi; non riletto dal codice sotto test.
"""
from datetime import date, datetime, timedelta, timezone
import gzip
import json
import logging
import os
import sys
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from bellomberg.market_data import polygon_data as provider
from bellomberg.portfolio import options_snapshots as arch
from bellomberg.portfolio import vol_surface as vol

SPOT = 113


def exp(days):
    return (date.today() + timedelta(days=days)).isoformat()


def contracts(expiry, iv=.23, count=14):
    return [{"details": {"ticker": f"O:ZZQ-{expiry}-{i}", "expiration_date": expiry,
                         "contract_type": "put" if i % 2 else "call", "strike_price": 89 + (i // 2) * 7,
                         "shares_per_contract": 100},
             "implied_volatility": iv, "open_interest": 17, "day": {"volume": 3},
             "last_quote": {"bid": 1.3, "ask": 1.7, "timeframe": "DELAYED"},
             "underlying_asset": {"price": SPOT}} for i in range(count)]


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("BELLOMBERG_DATA_DIR", str(tmp_path / "dati_prova"))
    monkeypatch.delenv(arch.RETENTION_ENV, raising=False)
    monkeypatch.delenv(arch.MAX_PER_TICKER_ENV, raising=False)
    monkeypatch.setattr(provider, "polygon_available", lambda: True)
    monkeypatch.setattr(provider, "_get", lambda *a: pytest.fail("unexpected provider request"))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda symbol: SimpleNamespace(fast_info={"lastPrice": SPOT})))
    from datetime import time as _time
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(vol, "_ny_now", lambda: datetime.combine(date.today(), _time(12), ZoneInfo("America/New_York")))
    vol._CHAIN_CACHE.clear()


def provider_pages(monkeypatch, iv=.23):
    def get(path, params):
        return {"results": contracts(params["expiration_date"], iv)}
    monkeypatch.setattr(provider, "_get", get)


def settled(mgr, job_id):
    end = monotonic() + 10
    while monotonic() < end:
        status = mgr.status(job_id)
        if status["state"] in {"error", "complete", "paused"}:
            return status
        sleep(.005)
    pytest.fail(f"download did not settle: {status}")


def download(mgr, ticker="ZZQA", expiries=None):
    job = mgr.start(ticker, expiries or [exp(23), exp(51)])
    return settled(mgr, job["id"])


def manager(**kw):
    from bellomberg.portfolio.options_download import OptionsDownloadManager
    return OptionsDownloadManager(**kw)


def files(root, ticker="ZZQA"):
    folder = root / ticker
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def default_root(tmp_path):
    return tmp_path / "dati_prova" / "options_snapshots"


# ------------------------------------------------------------------ percorso
def test_root_comes_from_data_dir_not_cwd(tmp_path, monkeypatch):
    elsewhere = tmp_path / "cwd_altrove"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert arch.archive_root() == (tmp_path / "dati_prova").resolve() / "options_snapshots"
    # relativa: ancorata alla radice del PROGETTO (regola di core.paths), non alla cwd
    monkeypatch.setenv("BELLOMBERG_DATA_DIR", "dati_relativi_zz")
    from bellomberg.core.paths import PROJECT_ROOT
    assert arch.archive_root() == (PROJECT_ROOT / "dati_relativi_zz").resolve() / "options_snapshots"
    assert not (elsewhere / "dati_relativi_zz").exists()


def test_completed_download_is_archived_under_data_dir(tmp_path, monkeypatch):
    provider_pages(monkeypatch)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    mgr = manager()
    status = download(mgr)
    assert status["state"] == "complete" and status["archive"]["status"] == "saved", status
    names = files(default_root(tmp_path))
    assert len(names) == 2 and names[0].endswith(".json.gz") and names[1].endswith(".meta.json")
    assert status["archive"]["id"] == "ZZQA~" + names[0][:-len(".json.gz")]
    assert list(cwd.iterdir()) == []          # niente nella cwd
    assert not any(p.name.endswith(".tmp") for p in (default_root(tmp_path) / "ZZQA").iterdir())
    # la superficie servita dichiara lo stesso esito
    assert mgr.surface(status["id"])["archive"]["id"] == status["archive"]["id"]


def test_record_content_surface_chains_and_provenance(tmp_path, monkeypatch):
    provider_pages(monkeypatch)
    status = download(manager())
    rec = arch.load(status["archive"]["id"])
    assert rec["ticker"] == "ZZQA" and rec["snapshot_at"] == status["snapshot_at"]
    surf = rec["surface"]
    assert surf["spot_est"] == SPOT and surf["moneyness_grid"] and len(surf["slices"]) == 2
    s0 = surf["slices"][0]
    for key in ("iv_grid", "t_years", "expiry", "atm_iv"):
        assert key in s0
    assert "spot_qualified" in surf and "iv_grid_qualified" in surf
    assert set(rec["chains"]) == {exp(23), exp(51)} and len(rec["chains"][exp(23)]) == 14
    c = rec["chains"][exp(23)][0]
    assert set(c) == set(arch.CHAIN_FIELDS)
    assert c["bid"] == 1.3 and c["ask"] == 1.7 and c["oi"] == 17 and c["volume"] == 3 and c["multiplier"] == 100
    prov = rec["provenance"]
    assert prov["source"] == arch.SOURCE and prov["data_delay"] == "DELAYED"
    assert prov["quote_timeframes"] == {"DELAYED": 28} and prov["spot_source"] == "Polygon underlying snapshot"
    # i testi d'autore tornano come messaggi bilingui (rilettura nella lingua di chi legge)
    from bellomberg.core.presentation import _PresentationText
    assert isinstance(surf["smoothing"], _PresentationText) and surf["smoothing"]._english.startswith("3-point")


# ------------------------------------------------------------------ deduplica
def test_same_content_redownloaded_is_not_a_second_snapshot(tmp_path, monkeypatch):
    provider_pages(monkeypatch)
    first = download(manager())
    vol._CHAIN_CACHE.clear()
    second = download(manager())      # nuovo job, nuovo snapshot_at, stesso contenuto
    assert second["id"] != first["id"] and second["snapshot_at"] != first["snapshot_at"]
    assert second["archive"]["status"] == "duplicate"
    assert second["archive"]["id"] == first["archive"]["id"]
    assert len(files(default_root(tmp_path))) == 2
    vol._CHAIN_CACHE.clear()
    provider_pages(monkeypatch, iv=.29)
    third = download(manager())
    assert third["archive"]["status"] == "saved" and len(files(default_root(tmp_path))) == 4


# ------------------------------------------------------------------ errori dichiarati
def test_write_error_does_not_break_download_and_is_declared(tmp_path, monkeypatch, caplog):
    provider_pages(monkeypatch)
    blocker = tmp_path / "non_una_cartella"
    blocker.write_text("x", encoding="utf-8")
    mgr = manager(archive_root=blocker)
    with caplog.at_level(logging.WARNING, logger="bellomberg.portfolio.options_download"):
        status = download(mgr)
    assert status["state"] == "complete" and status["download_complete"] is True
    assert status["archive"]["status"] == "error" and status["archive"]["id"] is None
    assert "archiviata" in status["archive"]["error"] or "archived" in status["archive"]["error"]
    surface = mgr.surface(status["id"])
    assert surface["archive"]["status"] == "error" and surface["slices"]
    assert any("NON archiviata" in r.getMessage() for r in caplog.records)


def test_atomic_write_leaves_no_partial_file(tmp_path, monkeypatch):
    provider_pages(monkeypatch)
    status = download(manager())
    rec = arch.load(status["archive"]["id"])
    rec["id"] = rec["id"].replace(rec["content_hash"], "0123456789abcdef")
    rec["presentation_texts"] = []
    root = tmp_path / "atomico"
    calls = []
    real = os.replace

    def refuse(src, dst):
        calls.append(dst)
        raise PermissionError("bersaglio aperto da un lettore")
    monkeypatch.setattr(arch.time, "sleep", lambda s: None)
    monkeypatch.setattr(arch.os, "replace", refuse)
    with pytest.raises(PermissionError):
        arch.save(rec, root)
    assert len(calls) == len(arch._REPLACE_PAUSES)          # ritenta, poi si arrende
    assert files(root) == []                                 # ne' bersaglio ne' temporaneo

    flaky = {"n": 0}

    def once(src, dst):
        flaky["n"] += 1
        if flaky["n"] == 1:
            raise PermissionError("lettore")
        return real(src, dst)
    monkeypatch.setattr(arch.os, "replace", once)
    out = arch.save(rec, root)
    assert out["status"] == "saved" and len(files(root)) == 2
    assert not any(n.endswith(".tmp") for n in files(root))


# ------------------------------------------------------------------ retention
def _fake(root, ticker, stamp, digest, snapshot_at):
    rec = {"format": arch.FORMAT, "id": f"{ticker}~{stamp}_{digest}", "ticker": ticker,
           "snapshot_at": snapshot_at, "saved_at": snapshot_at, "content_hash": digest,
           "provenance": {"source": arch.SOURCE, "download_complete": True},
           "summary": {"spot": 47, "expiries": ["2031-03-21"], "surface_expiries": ["2031-03-21"],
                       "n_contracts": 3, "n_slices": 1},
           "surface": {"slices": []}, "presentation_texts": [], "chains": {}}
    return arch.save(rec, root)


def test_retention_days_and_max_per_ticker_are_declared(tmp_path, monkeypatch):
    root = tmp_path / "ret"
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=97)
    old_stamp = old.strftime("%Y%m%dT%H%M%SZ")
    out = _fake(root, "ZZRT", old_stamp, "a1b2c3d4e5f60718", old.isoformat())
    # salvata e subito rimossa dalla retention (97 > 90 giorni): l'esito lo DICHIARA
    assert out["pruned"]["removed"] == [f"ZZRT~{old_stamp}_a1b2c3d4e5f60718"] and files(root, "ZZRT") == []
    monkeypatch.setenv(arch.MAX_PER_TICKER_ENV, "3")
    ids = []
    for i in range(5):
        t = now - timedelta(hours=7 * (5 - i))
        r = _fake(root, "ZZRT", t.strftime("%Y%m%dT%H%M%SZ"), f"{i:016x}", t.isoformat())
        ids.append(r["id"])
    assert len(files(root, "ZZRT")) == 6                    # 3 gz + 3 meta
    listed = arch.list_snapshots("ZZRT", root)
    assert [m["id"] for m in listed["snapshots"]] == ids[:1:-1]
    assert {k: listed["retention"][k] for k in ("days", "max_per_ticker", "error")} == {"days": 90, "max_per_ticker": 3, "error": None}


def test_invalid_retention_config_deletes_nothing_and_says_so(tmp_path, monkeypatch):
    root = tmp_path / "ret2"
    monkeypatch.setenv(arch.RETENTION_ENV, "novanta")
    old = datetime.now(timezone.utc) - timedelta(days=311)
    out = _fake(root, "ZZRU", old.strftime("%Y%m%dT%H%M%SZ"), "00000000000000a7", old.isoformat())
    assert out["pruned"]["removed"] == [] and out["pruned"]["error"]
    assert len(files(root, "ZZRU")) == 2


# ------------------------------------------------------------------ elenco / filtro
def test_list_filters_by_ticker_and_rebuilds_missing_index(tmp_path, monkeypatch):
    root = tmp_path / "lista"
    t = datetime.now(timezone.utc) - timedelta(hours=3)
    st = t.strftime("%Y%m%dT%H%M%SZ")
    _fake(root, "ZZLA", st, "00000000000000b1", t.isoformat())
    _fake(root, "ZZLB", st, "00000000000000b2", t.isoformat())
    only_a = arch.list_snapshots("zzla", root)
    assert [m["ticker"] for m in only_a["snapshots"]] == ["ZZLA"] and only_a["count"] == 1
    assert only_a["errors"] == []             # l'altro ticker non e' nemmeno guardato
    assert {m["ticker"] for m in arch.list_snapshots(None, root)["snapshots"]} == {"ZZLA", "ZZLB"}
    os.remove(root / "ZZLA" / f"{st}_00000000000000b1.meta.json")
    rebuilt = arch.list_snapshots("ZZLA", root)["snapshots"][0]
    assert rebuilt["index"] == "rebuilt_from_archive" and rebuilt["spot"] == 47
    (root / "ZZLA" / f"{st}_00000000000000c3.json.gz").write_bytes(b"non gzip")
    listed = arch.list_snapshots("ZZLA", root)
    assert listed["count"] == 1 and listed["errors"][0]["id"] == f"ZZLA~{st}_00000000000000c3"


def test_reserved_windows_name_gets_a_safe_folder(tmp_path):
    root = tmp_path / "riservati"
    t = datetime.now(timezone.utc)
    _fake(root, "CON", t.strftime("%Y%m%dT%H%M%SZ"), "00000000000000d9", t.isoformat())
    assert (root / "_CON").is_dir()
    assert arch.list_snapshots("CON", root)["count"] == 1


# ------------------------------------------------------------------ endpoint
@pytest.fixture
def client():
    from bellomberg.api.options_routes import create_options_router

    def require_session(request: Request):
        if request.headers.get("Authorization") != "Bearer synthetic":
            raise HTTPException(401, "session required")
    app = FastAPI()
    app.include_router(create_options_router(require_session))
    return TestClient(app)


H = {"Authorization": "Bearer synthetic", "X-BB-Language": "en"}


def test_endpoints_list_and_load(client, tmp_path, monkeypatch):
    provider_pages(monkeypatch)
    from bellomberg.portfolio import options_download as od
    monkeypatch.setattr(od, "downloads", manager())
    status = download(od.downloads, "ZZEA")
    vol._CHAIN_CACHE.clear()
    download(od.downloads, "ZZEB")
    assert client.get("/options/snapshots?ticker=ZZEA").status_code == 401
    assert client.get("/options/snapshots/x").status_code == 401
    listed = client.get("/options/snapshots?ticker=ZZEA", headers=H).json()
    assert [m["ticker"] for m in listed["snapshots"]] == ["ZZEA"]
    item = listed["snapshots"][0]
    assert item["id"] == status["archive"]["id"] and item["spot"] == SPOT
    assert item["expiries"] == [exp(23), exp(51)] and item["size_bytes"] > 0
    assert item["snapshot_at"] == status["snapshot_at"] and item["data_delay"] == "DELAYED"
    full = client.get(f"/options/snapshots/{item['id']}", headers=H)
    assert full.status_code == 200
    body = full.json()
    assert body["surface"]["slices"] and body["chains"][exp(23)]
    assert body["_presentation_v1"]["texts"]           # testi bilingui ricostruiti
    assert client.get("/options/snapshots/..%2F..%2Fconsigliere.db", headers=H).status_code in (404, 422)
    assert client.get("/options/snapshots/ZZEA~20990101T000000Z_zz", headers=H).status_code == 422
    assert client.get("/options/snapshots/ZZEA~20990101T000000Z_00000000000000e5", headers=H).status_code == 404
    gz = default_root(tmp_path) / "ZZEA" / (item["id"].split("~")[1] + ".json.gz")
    gz.write_bytes(gzip.compress(b"{rotto"))
    assert client.get(f"/options/snapshots/{item['id']}", headers=H).status_code == 500
    assert client.get("/options/snapshots?ticker=..%2Fx", headers=H).status_code == 422


# ------------------------------------------------------------------ t_years
def test_t_years_relative_to_snapshot_at_not_call_time(monkeypatch):
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
    snap_ny = datetime(2031, 3, 4, 11, 17, tzinfo=NY)
    e = "2031-04-17"
    chain = [c for c in (vol._contract_row(r) for r in contracts(e))]
    snapshot = {"spot": SPOT, "spot_source": "Polygon underlying snapshot", "spot_timestamp": None,
                "spot_timeframe": None, "spot_qualified": True, "spot_fallback": False, "spot_alignment": None,
                "download_complete": True, "snapshot_at": snap_ny.astimezone(timezone.utc).isoformat(),
                "chains": {e: {"chain": chain, "complete": True, "continuation_error": None}}}
    # l'ora della CHIAMATA e' 9 giorni dopo l'istantanea
    monkeypatch.setattr(vol, "_ny_now", lambda: datetime(2031, 3, 13, 15, 41, tzinfo=NY))
    out = vol.build_vol_surface("ZZTY", expiries=[e], include_context=False, _snapshot=dict(snapshot))
    s = out["slices"][0]
    expected = (datetime(2031, 4, 17, 16, tzinfo=NY).astimezone(timezone.utc)
                - snap_ny.astimezone(timezone.utc)).total_seconds() / (365 * 86400)
    assert s["t_years"] == pytest.approx(expected, abs=1e-6)
    assert s["days"] == 44 and out["term_structure"][0]["days"] == 44
    assert out["t_reference"] == {"basis": "snapshot_at", "at": snapshot["snapshot_at"]}
    # senza snapshot_at: dall'ora del calcolo, DICHIARATO
    snapshot.pop("snapshot_at")
    out2 = vol.build_vol_surface("ZZTY", expiries=[e], include_context=False, _snapshot=snapshot)
    assert out2["slices"][0]["days"] == 35 and out2["t_reference"]["basis"] == "computation_time"
    assert out2["t_reference"]["note"]


def test_manager_surface_passes_snapshot_at(monkeypatch):
    provider_pages(monkeypatch)
    mgr = manager(archive_root=None)
    status = download(mgr)
    from zoneinfo import ZoneInfo
    later = datetime.now(ZoneInfo("America/New_York")) + timedelta(days=6)
    monkeypatch.setattr(vol, "_ny_now", lambda: later)
    out = mgr.surface(status["id"])
    assert out["t_reference"]["basis"] == "snapshot_at"
    assert out["slices"][0]["days"] == (date.fromisoformat(exp(23)) - datetime.fromisoformat(status["snapshot_at"]).astimezone(ZoneInfo("America/New_York")).date()).days


def test_concurrent_saves_of_same_content_write_one_snapshot(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    root = tmp_path / "concorrenza"
    t = datetime.now(timezone.utc) - timedelta(minutes=13)
    stamp = t.strftime("%Y%m%dT%H%M%SZ")
    with ThreadPoolExecutor(6) as pool:
        outs = list(pool.map(lambda _: _fake(root, "ZZCC", stamp, "00000000000000f3", t.isoformat()), range(6)))
    assert sorted(o["status"] for o in outs) == ["duplicate"] * 5 + ["saved"]
    assert len(files(root, "ZZCC")) == 2


# ------------------------------------------------------------------ v2 (review di 7a70e6a)
def _stem(r):
    return r["id"].split("~")[1]


from contextlib import contextmanager


@contextmanager
def _locked(monkeypatch, target):
    """«File tenuto aperto da un lettore» in modo PORTABILE (fix CI 11/10, Opus 5.5).

    Su Windows un file aperto senza FILE_SHARE_DELETE non si cancella (PermissionError); su
    Linux/macOS os.remove/os.unlink riescono anche col file aperto, quindi aprire il file non
    simula nulla. Qui la rimozione di QUEL percorso (os.remove, os.unlink, Path.unlink: quelli
    che la pulizia potrebbe usare) solleva PermissionError come su Windows; ogni altro percorso
    passa all'originale. Prima si apre e chiude davvero il file: su Linux e' il caso «nessun
    blocco», e il blocco resta solo quello simulato, identico sulle due piattaforme.
    """
    import pathlib
    with open(target, "rb"):
        pass
    want = os.path.normcase(os.path.abspath(target))
    hit = {"n": 0}

    def guard(original):
        def wrapper(path, *a, **k):
            if os.path.normcase(os.path.abspath(os.fspath(path))) == want:
                hit["n"] += 1
                raise PermissionError(13, "file in uso da un altro processo (simulato)", os.fspath(path))
            return original(path, *a, **k)
        return wrapper

    with monkeypatch.context() as m:
        m.setattr(os, "remove", guard(os.remove))
        m.setattr(os, "unlink", guard(os.unlink))
        original_unlink = pathlib.Path.unlink
        m.setattr(pathlib.Path, "unlink", lambda self, *a, **k: guard(lambda q, *b, **c: original_unlink(pathlib.Path(q), *b, **c))(self, *a, **k))
        yield hit


def test_prune_meta_held_open_keeps_the_snapshot_whole_and_says_which_file(tmp_path, monkeypatch):
    root = tmp_path / "aperto"
    t0 = datetime.now(timezone.utc) - timedelta(hours=29)
    old = _fake(root, "ZZHO", t0.strftime("%Y%m%dT%H%M%SZ"), "00000000000000e1", t0.isoformat())
    meta = root / "ZZHO" / f"{_stem(old)}.meta.json"
    gz = root / "ZZHO" / f"{_stem(old)}.json.gz"
    monkeypatch.setenv(arch.MAX_PER_TICKER_ENV, "1")
    t1 = t0 + timedelta(hours=3)
    with _locked(monkeypatch, meta) as hit:   # un lettore tiene aperto l'indice (simulato, portabile)
        new = _fake(root, "ZZHO", t1.strftime("%Y%m%dT%H%M%SZ"), "00000000000000e2", t1.isoformat())
    assert hit["n"] == 1                      # il blocco e' scattato davvero, una volta
    failed = new["pruned"]["failed"]
    assert new["status"] == "saved" and len(failed) == 1
    assert failed[0]["id"] == old["id"] and failed[0]["step"] == "meta"
    assert sorted(failed[0]["remaining"]) == sorted([meta.name, gz.name])
    assert gz.exists() and meta.exists()                  # intera: indice E dati
    # chiuso il lettore, la pulizia successiva la toglie (prima l'indice, poi il gz)
    out = arch.prune_all(root)
    assert out["removed"] == [old["id"]] and not gz.exists() and not meta.exists()


def test_prune_gz_held_open_leaves_a_readable_gz_without_index(tmp_path, monkeypatch):
    root = tmp_path / "gzaperto"
    t0 = datetime.now(timezone.utc) - timedelta(hours=31)
    old = _fake(root, "ZZHG", t0.strftime("%Y%m%dT%H%M%SZ"), "00000000000000e3", t0.isoformat())
    gz = root / "ZZHG" / f"{_stem(old)}.json.gz"
    monkeypatch.setenv(arch.MAX_PER_TICKER_ENV, "1")
    t1 = t0 + timedelta(hours=5)
    with _locked(monkeypatch, gz) as hit:
        new = _fake(root, "ZZHG", t1.strftime("%Y%m%dT%H%M%SZ"), "00000000000000e4", t1.isoformat())
        assert hit["n"] == 1
        f = new["pruned"]["failed"][0]
        assert f["step"] == "gz" and f["remaining"] == [gz.name]
        listed = arch.list_snapshots("ZZHG", root)
        assert {m["id"]: m["index"] for m in listed["snapshots"]}[old["id"]] == "rebuilt_from_archive"


def test_orphan_index_is_swept_and_declared(tmp_path):
    root = tmp_path / "orfani"
    t0 = datetime.now(timezone.utc) - timedelta(hours=7)
    r = _fake(root, "ZZOR", t0.strftime("%Y%m%dT%H%M%SZ"), "00000000000000e5", t0.isoformat())
    os.remove(root / "ZZOR" / f"{_stem(r)}.json.gz")
    out = arch.prune_all(root)
    assert out["removed"] == [f"{_stem(r)}.meta.json"] and files(root, "ZZOR") == []


def test_cleanup_failure_after_a_good_write_is_declared_not_an_error(tmp_path, monkeypatch):
    def broken(root=None, **kw):
        raise OSError("disco")
    monkeypatch.setattr(arch, "prune_all", broken)
    t0 = datetime.now(timezone.utc)
    out = _fake(tmp_path / "pul", "ZZPF", t0.strftime("%Y%m%dT%H%M%SZ"), "00000000000000e6", t0.isoformat())
    assert out["status"] == "saved" and out["pruned"]["error"] and len(files(tmp_path / "pul", "ZZPF")) == 2


def test_retention_runs_on_every_ticker_not_only_the_one_saved(tmp_path, monkeypatch):
    root = tmp_path / "tutti"
    monkeypatch.setenv(arch.RETENTION_ENV, "1000")
    t_old = datetime.now(timezone.utc) - timedelta(days=211)
    stale = _fake(root, "ZZOLD", t_old.strftime("%Y%m%dT%H%M%SZ"), "00000000000000e7", t_old.isoformat())
    assert len(files(root, "ZZOLD")) == 2
    monkeypatch.delenv(arch.RETENTION_ENV)
    t = datetime.now(timezone.utc)
    out = _fake(root, "ZZNEW", t.strftime("%Y%m%dT%H%M%SZ"), "00000000000000e8", t.isoformat())
    assert stale["id"] in out["pruned"]["removed"] and files(root, "ZZOLD") == []
    assert arch.list_snapshots(None, root)["retention"]["applied"]


def test_retention_boundary_is_inclusive(tmp_path, monkeypatch):
    root = tmp_path / "confine"
    now = datetime(2031, 6, 17, 13, 41, 23, tzinfo=timezone.utc)
    monkeypatch.setenv(arch.RETENTION_ENV, "100000")
    edge = now - timedelta(days=90)
    older = edge - timedelta(seconds=1)
    keep = _fake(root, "ZZBD", edge.strftime("%Y%m%dT%H%M%SZ"), "00000000000000e9", edge.isoformat())
    gone = _fake(root, "ZZBD", older.strftime("%Y%m%dT%H%M%SZ"), "00000000000000ea", older.isoformat())
    monkeypatch.delenv(arch.RETENTION_ENV)
    out = arch.prune("ZZBD", root, now=now)
    assert out["removed"] == [gone["id"]]
    assert [m["id"] for m in arch.list_snapshots("ZZBD", root)["snapshots"]] == [keep["id"]]


def test_index_with_another_id_is_not_trusted(tmp_path):
    root = tmp_path / "indice"
    t = datetime.now(timezone.utc) - timedelta(hours=2)
    r = _fake(root, "ZZIX", t.strftime("%Y%m%dT%H%M%SZ"), "00000000000000eb", t.isoformat())
    meta = root / "ZZIX" / f"{_stem(r)}.meta.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    meta.write_text(json.dumps({**data, "id": "ZZIX~20990101T000000Z_00000000000000ff", "spot": 9973}), encoding="utf-8")
    item = arch.list_snapshots("ZZIX", root)["snapshots"][0]
    assert item["index"] == "rebuilt_from_archive" and item["spot"] == 47 and item["id"] == r["id"]


def test_download_without_contracts_is_skipped_not_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "_get", lambda path, params: {"results": []})
    status = download(manager())
    assert status["state"] == "complete" and status["archive"]["status"] == "skipped"
    assert status["archive"]["id"] is None and not default_root(tmp_path).exists()


def test_naive_snapshot_at_falls_back_to_computation_time_declared(monkeypatch):
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
    e = "2031-04-17"
    chain = [vol._contract_row(r) for r in contracts(e)]
    monkeypatch.setattr(vol, "_ny_now", lambda: datetime(2031, 3, 13, 15, 41, tzinfo=NY))
    snap = {"spot": SPOT, "spot_source": "Polygon underlying snapshot", "spot_qualified": True,
            "download_complete": True, "snapshot_at": "2031-03-04T16:17:00",
            "chains": {e: {"chain": chain, "complete": True, "continuation_error": None}}}
    out = vol.build_vol_surface("ZZNV", expiries=[e], include_context=False, _snapshot=snap)
    assert out["t_reference"]["basis"] == "computation_time" and out["t_reference"]["note"]
    assert out["slices"][0]["days"] == 35


def test_load_refuses_a_file_whose_record_has_another_id(tmp_path, client):
    root = default_root(tmp_path)
    t = datetime.now(timezone.utc) - timedelta(hours=4)
    r = _fake(root, "ZZID", t.strftime("%Y%m%dT%H%M%SZ"), "00000000000000ec", t.isoformat())
    other = "ZZID~" + t.strftime("%Y%m%dT%H%M%SZ") + "_00000000000000ed"
    import shutil
    shutil.copy(root / "ZZID" / f"{_stem(r)}.json.gz", root / "ZZID" / (other.split("~")[1] + ".json.gz"))
    with pytest.raises(arch.SnapshotUnreadable):
        arch.load(other, root)
    assert client.get(f"/options/snapshots/{other}", headers=H).status_code == 500
    assert client.get(f"/options/snapshots/{r['id']}", headers=H).status_code == 200


def test_reserved_name_with_suffix_gets_a_safe_folder(tmp_path):
    root = tmp_path / "riservati2"
    t = datetime.now(timezone.utc)
    r = _fake(root, "CON.B", t.strftime("%Y%m%dT%H%M%SZ"), "00000000000000ee", t.isoformat())
    assert (root / "_CON.B").is_dir()
    assert [m["id"] for m in arch.list_snapshots("CON.B", root)["snapshots"]] == [r["id"]]
    assert arch._ticker_dirname("CON.B") == "_CON.B" and arch._ticker_dirname("ZZCONB") == "ZZCONB"


def test_trailing_dot_ticker_is_refused(client):
    for bad in ("ZZTD.", "zztd."):
        with pytest.raises(ValueError):
            arch._ticker(bad)
    with pytest.raises(ValueError):
        arch.parse_id("ZZTD.~20310101T000000Z_00000000000000ef")
    assert client.get("/options/snapshots?ticker=ZZTD.", headers=H).status_code == 422
    assert client.get("/options/snapshots/ZZTD.~20310101T000000Z_00000000000000ef", headers=H).status_code == 422


def test_load_busy_is_transient_503_and_missing_at_stat_is_404(tmp_path, monkeypatch, client):
    root = default_root(tmp_path)
    t = datetime.now(timezone.utc) - timedelta(hours=6)
    r = _fake(root, "ZZBY", t.strftime("%Y%m%dT%H%M%SZ"), "00000000000000f1", t.isoformat())
    gz = root / "ZZBY" / f"{_stem(r)}.json.gz"
    record = arch._read_gz(gz)

    def busy(*a, **k):
        raise PermissionError("in rimozione")
    with monkeypatch.context() as m:
        m.setattr(arch.gzip, "open", busy)
        assert client.get(f"/options/snapshots/{r['id']}", headers=H).status_code == 503
        with pytest.raises(arch.SnapshotBusy):
            arch.load(r["id"], root)
    os.remove(gz)                                   # sparito fra lettura e stat
    monkeypatch.setattr(arch, "_read_gz", lambda path: dict(record))
    with pytest.raises(KeyError):
        arch.load(r["id"], root)
    assert client.get(f"/options/snapshots/{r['id']}", headers=H).status_code == 404
