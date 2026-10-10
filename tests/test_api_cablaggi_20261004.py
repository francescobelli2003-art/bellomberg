"""Cablaggi API del 04/10/2026 (W2, Opus 5.5): provenienza dei trigger watch in
GET /decisions, guardia di copertura su insider e /options/vol_*, fonti spente nelle
rotte news. DB in tmp_path, ticker e numeri inventati, nessuna rete: ogni fonte e'
sostituita da una funzione che CONTA le chiamate (o fallisce se non deve essere chiamata)."""
import socket
import sqlite3

import pytest
from fastapi.testclient import TestClient

from test_persistence import db  # noqa: F401  (fixture: MemoryDB in tmp_path, senza chroma)
from test_trade_idea_watch_store import FIELDS, TODAY, add_run, store
from tools.migrations import migra_trade_idea, migra_trade_idea_watch

import bellomberg.api.bellomberg_api as api


@pytest.fixture(autouse=True)
def _niente_rete(monkeypatch):
    vero = socket.socket.connect

    def vietato(self, addr, *a, **k):
        # il loop asyncio di Windows apre una socketpair su loopback: solo quella passa
        if isinstance(addr, tuple) and addr and addr[0] in ("127.0.0.1", "::1"):
            return vero(self, addr, *a, **k)
        raise AssertionError("rete vietata nei test dei cablaggi")
    monkeypatch.setattr(socket.socket, "connect", vietato)


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(api, "get_db", lambda: db)
    api.app.dependency_overrides[api.require_session] = lambda: None
    try:
        yield TestClient(api.app, base_url="http://127.0.0.1")
    finally:
        api.app.dependency_overrides.clear()


@pytest.fixture
def migra(db, monkeypatch):
    """Esegue le migrazioni vere sul DB temporaneo (backend_alive stubbato: nessun backend serve tmp)."""
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    monkeypatch.setattr(migra_trade_idea_watch, "backend_alive", lambda: False)

    def _run(watch=True):
        migra_trade_idea.migra(db.db_path, apply=True)
        if watch:
            migra_trade_idea_watch.migra(db.db_path, apply=True)
    return _run


# ── GET /decisions: provenienza dei trigger ─────────────────────────────────

def test_decisions_senza_schema_watch_non_cade_e_dichiara(db, client, migra):
    migra(watch=False)
    origin = add_run(db.db_path, "run-a")
    r = client.get("/decisions?limit=10")
    assert r.status_code == 200
    out = r.json()
    assert "schema watch assente" in out["watch_provenance_error"]
    assert "migra_trade_idea_watch.py" in out["watch_provenance_error"]
    by = {d["id"]: d for d in out["decisions"]}
    # la provenienza Trade Idea esistente resta
    assert by[origin]["trade_idea"]["origin"] == "trade_idea"


def test_decisions_db_senza_trade_idea_dichiara_comunque(db, client):
    out = client.get("/decisions").json()
    assert out["watch_provenance_error"] and "schema watch assente" in out["watch_provenance_error"]


def test_decisions_unisce_la_provenienza_dei_trigger(db, client, migra):
    migra()
    origin = add_run(db.db_path, "run-a")
    s = store(db.db_path)
    s.ingest_pending(today=TODAY)
    fired = s.fire("run-a", [1, 3], FIELDS, {})
    out = client.get("/decisions?limit=10").json()
    assert out["watch_provenance_error"] is None
    by = {d["id"]: d for d in out["decisions"]}
    t = by[fired]["trade_idea"]
    assert t["origin"] == "trade_idea_trigger" and t["run_id"] == "run-a"
    assert t["source_decision_id"] == origin and t["trigger_ids"] == [1, 3]
    assert by[origin]["trade_idea"]["origin"] == "trade_idea"


def test_decisions_lotti_da_mille(db, client, migra, monkeypatch):
    migra()
    with sqlite3.connect(db.db_path) as conn:
        conn.executemany("INSERT INTO decisions(timestamp,action,ticker,status) VALUES(?,?,?,?)",
                         [("2031-01-01T00:00:00", "BUY", "ZZTEST", "PENDING")] * 1201)
    from bellomberg.storage.trade_idea_watch_store import TradeIdeaWatchStore
    lotti = []
    vero = TradeIdeaWatchStore.lookup_decisions

    def spia(self, ids):
        lotti.append(len(ids))
        return vero(self, ids)
    monkeypatch.setattr(TradeIdeaWatchStore, "lookup_decisions", spia)
    out = client.get("/decisions?limit=1201").json()
    assert out["watch_provenance_error"] is None
    assert lotti == [1000, 201]


def test_decisions_guasto_generico_solo_il_tipo(db, client, migra, monkeypatch):
    migra()
    add_run(db.db_path, "run-a")
    from bellomberg.storage.trade_idea_watch_store import TradeIdeaWatchStore

    def rotto(self, ids):
        raise sqlite3.OperationalError("QQSEGRETO C:/percorso/vero.db")
    monkeypatch.setattr(TradeIdeaWatchStore, "lookup_decisions", rotto)
    r = client.get("/decisions")
    assert r.status_code == 200
    err = r.json()["watch_provenance_error"]
    assert "OperationalError" in err and "QQSEGRETO" not in err


def test_decisions_provenienza_doppia_dichiarata(db, client, migra, monkeypatch):
    migra()
    origin = add_run(db.db_path, "run-a")
    from bellomberg.storage.trade_idea_watch_store import TradeIdeaWatchStore
    monkeypatch.setattr(TradeIdeaWatchStore, "lookup_decisions",
                        lambda self, ids: {origin: {"origin": "trade_idea_trigger"}} if origin in ids else {})
    out = client.get("/decisions").json()
    assert f"#{origin}" in out["watch_provenance_error"]
    by = {d["id"]: d for d in out["decisions"]}
    assert by[origin]["trade_idea"]["origin"] == "trade_idea"


# ── GET /news/insider-trades: guardia copertura + .MI eMarket ───────────────

@pytest.fixture
def fonti_insider(monkeypatch):
    from bellomberg.market_data import finnhub_news, sdir, sec_edgar
    conta = {"finnhub": 0, "sec": 0, "emarket": 0}
    risposta_it = {"stato": "KO"}

    finnhub_modo = {"modo": "ok"}

    def fn(ticker, days=30, motivo=None):
        conta["finnhub"] += 1
        if finnhub_modo["modo"] == "eccezione":
            raise RuntimeError("QQSEGRETO https://finnhub.invalid/?token=QQSEGRETO")
        if finnhub_modo["modo"] == "muto":
            motivo.append("429 rate limited (60 req/min free tier)")
            return []
        return [{"ticker": ticker, "owner": "Zeno Fittizio", "share_change": 10}]

    def sec(ticker, days=30, max_items=30, motivo=None):
        conta["sec"] += 1
        if finnhub_modo.get("sec_muto"):
            motivo.append("SEC QQMUTA")
        return []

    def it(ticker, *, giorni=180, nome=None):
        conta["emarket"] += 1
        conta["giorni"] = giorni
        base = {"ticker": ticker.strip().upper(), "comunicazioni": [], "stato": risposta_it["stato"],
                "errore": None, "motivo": None, "sdir": "eMarket SDIR",
                "instradamento": {"regola": "finta", "scelta": "emarket"}}
        if risposta_it["stato"] == "KO":
            base.update(errore="negozio_assente", motivo="negozio ISIN assente: QQMOTIVO")
        elif risposta_it["stato"] == "ok":
            base["comunicazioni"] = [{"data": "2031-01-02", "operazioni": []}]
        return base
    monkeypatch.setattr(finnhub_news, "fetch_insider_trades", fn)
    monkeypatch.setattr(sec_edgar, "get_insider_trades", sec)
    # handoff-3 (W1, ok main 05/10): l'internal dealing passa dall'instradatore sdir.py
    monkeypatch.setattr(sdir, "get_internal_dealing", it)
    conta["risposta_it"] = risposta_it
    conta["finnhub_modo"] = finnhub_modo
    return conta


def test_insider_mi_va_su_emarket_e_dichiara_il_ko(client, fonti_insider):
    out = client.get("/news/insider-trades/qqsyn.mi?days=40").json()
    assert out["source"] == "sdir" and out["stato"] == "KO"
    assert out["sdir"] == "eMarket SDIR" and out["instradamento"]["scelta"] == "emarket"
    assert "QQMOTIVO" in out["error"] and out["count"] == 0
    assert out["internal_dealing"]["errore"] == "negozio_assente"
    assert (fonti_insider["finnhub"], fonti_insider["sec"], fonti_insider["emarket"]) == (0, 0, 1)
    assert fonti_insider["giorni"] == 40


def test_insider_mi_ok_senza_errore(client, fonti_insider):
    fonti_insider["risposta_it"]["stato"] = "ok"
    out = client.get("/news/insider-trades/QQSYN.MI").json()
    assert out["stato"] == "ok" and out["error"] is None and out["count"] == 1


@pytest.mark.parametrize("ticker,valuta,stato", [
    ("QQSYN.DE", None, "non_coperto"),
    ("QQSYN.L", None, "non_coperto"),
    ("BTC-USD", None, "non_coperto"),
    ("ZZTEST", "EUR", "indeterminato"),
    ("ZZTEST.ZZ", None, "non_coperto"),     # fuori registro, 2+ lettere (RV-C P3a)
    ("ZZTEST.B", None, "indeterminato"),    # una lettera: classe di azioni?
])
def test_insider_non_usa_nessuna_chiamata_e_dichiarato(client, fonti_insider, ticker, valuta, stato):
    url = f"/news/insider-trades/{ticker}" + (f"?valuta={valuta}" if valuta else "")
    r = client.get(url)
    assert r.status_code == 200
    out = r.json()
    assert out["copertura"] == stato and out["error"]
    assert out["ticker"] == ticker.upper()
    assert (fonti_insider["finnhub"], fonti_insider["sec"], fonti_insider["emarket"]) == (0, 0, 0)


def test_insider_usa_passa_a_finnhub(client, fonti_insider):
    out = client.get("/news/insider-trades/ZZTEST?valuta=USD").json()
    assert out["source"] == "finnhub" and out["count"] == 1
    assert fonti_insider["finnhub"] == 1 and fonti_insider["emarket"] == 0


def _posizione(db, ticker, valuta):
    with db._conn() as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,valuta,is_active) VALUES(?,?,?,1)",
                     (ticker, 12.5, valuta))


def test_insider_valuta_dal_book_eur_e_indeterminato(db, client, fonti_insider):
    _posizione(db, "ZZBOOK", "EUR")
    out = client.get("/news/insider-trades/zzbook").json()
    assert out["copertura"] == "indeterminato"
    assert "EUR" in out["copertura_nota"]
    assert (fonti_insider["finnhub"], fonti_insider["sec"]) == (0, 0)


def test_insider_valuta_dal_book_usd_passa_e_lo_dice(db, client, fonti_insider):
    _posizione(db, "ZZBOOK", "USD")
    out = client.get("/news/insider-trades/ZZBOOK").json()
    assert out["source"] == "finnhub" and "USD" in out["copertura_nota"]
    assert out["fonti_mute"] is None


def test_insider_fuori_dal_book_presunzione_dichiarata(client, fonti_insider):
    out = client.get("/news/insider-trades/ZZNEW").json()
    assert out["source"] == "finnhub"
    assert "presunto" in out["copertura_nota"] or "presumed" in out["copertura_nota"]


def test_insider_valuta_esplicita_nessuna_nota(client, fonti_insider):
    assert client.get("/news/insider-trades/ZZNEW?valuta=USD").json()["copertura_nota"] is None


def test_insider_db_illeggibile_dichiarato(client, fonti_insider, monkeypatch, tmp_path):
    rotto = tmp_path / "QQSEGRETO_rotto.db"
    rotto.write_bytes(b"non e' un database sqlite " * 100)

    class Rotto:
        db_path = str(rotto)
    monkeypatch.setattr(api, "get_db", lambda: Rotto())
    out = client.get("/news/insider-trades/ZZNEW").json()
    assert "DatabaseError" in out["copertura_nota"] and "QQSEGRETO" not in out["copertura_nota"]
    assert out["source"] == "finnhub"


def test_insider_finnhub_eccezione_dichiarata_solo_tipo(client, fonti_insider):
    fonti_insider["finnhub_modo"]["modo"] = "eccezione"
    r = client.get("/news/insider-trades/ZZNEW")
    out = r.json()
    assert out["source"] == "sec_edgar" and out["fonti_mute"]["finnhub"] == "RuntimeError"
    assert "QQSEGRETO" not in r.text
    assert fonti_insider["sec"] == 1


def test_insider_finnhub_muto_dichiarato_e_sec_muta(client, fonti_insider):
    fonti_insider["finnhub_modo"].update(modo="muto", sec_muto=True)
    out = client.get("/news/insider-trades/ZZNEW").json()
    assert out["source"] == "sec_edgar" and "429" in out["fonti_mute"]["finnhub"]
    assert "QQMUTA" in out["fonti_mute"]["sec_edgar"]


# ── /options/vol_surface e /options/vol_cone: copertura opzioni ─────────────

@pytest.fixture
def superficie(monkeypatch):
    from bellomberg.market_data import polygon_data
    from bellomberg.portfolio import vol_cone, vol_surface
    conta = {"build": 0, "polygon": 0}

    def build(ticker, **kw):
        conta["build"] += 1
        return {"ticker": ticker, "slices": [], "error": None, "coverage": {"complete": True}}

    def polygon(*a, **k):
        conta["polygon"] += 1
        raise AssertionError("Polygon interrogato")

    def cone(ticker, force=False):
        return {"ticker": ticker.upper(), "realized": {"windows": [{"window": 21, "rv": 0.25}]},
                "implied": {"error": "vol_surface: POLYGON_API_KEY mancante o non attiva"}}
    monkeypatch.setattr(vol_surface, "build_vol_surface", build)
    monkeypatch.setattr(polygon_data, "_get", polygon)
    monkeypatch.setattr(vol_cone, "compute_vol_cone", cone)
    return conta


def test_vol_surface_idem_dichiarato_senza_contesto(client, superficie):
    r = client.get("/options/vol_surface/QQSYN.MI?include_context=false")
    assert r.status_code == 200
    out = r.json()
    assert out["copertura"] == "non_coperto" and "IDEM" in out["error"]
    assert superficie == {"build": 0, "polygon": 0}


def test_vol_surface_idem_col_contesto_usa_il_502_della_rotta(client, superficie):
    r = client.get("/options/vol_surface/QQSYN.DE")
    assert r.status_code == 502
    assert "IBKR" in r.json()["detail"]
    assert superficie == {"build": 0, "polygon": 0}


def test_vol_surface_usa_e_classe_col_punto_passano(client, superficie):
    assert "copertura" not in client.get("/options/vol_surface/ZZTEST?include_context=false").json()
    assert "copertura" not in client.get("/options/vol_surface/DEMO.X?include_context=false").json()
    assert superficie["build"] == 2


def test_vol_cone_estero_realized_resta_implied_non_coperto(client, superficie):
    out = client.get("/options/vol_cone/QQSYN.MI").json()
    assert out["realized"]["windows"][0]["window"] == 21
    assert out["implied"]["copertura"] == "non_coperto"
    assert "IDEM" in out["implied"]["error"] and "POLYGON_API_KEY" not in out["implied"]["error"]


def test_vol_cone_usa_invariato(client, superficie):
    out = client.get("/options/vol_cone/ZZTEST").json()
    assert out["implied"] == {"error": "vol_surface: POLYGON_API_KEY mancante o non attiva"}


def test_router_opzioni_estero_fermo_prima_di_polygon(client, monkeypatch):
    """Il router /options/expiry_catalog e chain_detail eredita la guardia di vol_surface (C1)."""
    from bellomberg.market_data import polygon_data
    chiamate = []
    monkeypatch.setattr(polygon_data, "_get", lambda *a, **k: chiamate.append(a))
    out = client.get("/options/expiry_catalog/QQSYN.MI").json()
    assert out["copertura"] == "non_coperto" and out["requests_used"] == 0
    out = client.get("/options/chain_detail/QQSYN.MI?expiry=2031-01-17").json()
    assert out["copertura"] == "non_coperto"
    assert chiamate == []


# ── /news/providers e /news/macro: fonti spente ─────────────────────────────

@pytest.fixture
def news(tmp_path, monkeypatch):
    import bellomberg.market_data.news_aggregator as na
    p = tmp_path / "news_rate_state.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(na, "_NEWS_RATE_PATH", str(p))
    chiamate = []

    def macro(**kw):
        chiamate.append(kw)
        return [{"title": "ZZ titolo", "url": "http://zz.invalid/1"}]
    monkeypatch.setattr(na, "fetch_macro_news", macro)
    return chiamate


def test_providers_dichiara_fonti_spente(client, news):
    out = client.get("/news/providers").json()
    assert "SPENTA" in out["fonti_spente"]["reddit"]
    assert "fonti_mute" in out   # le spente non sono un guasto: chiave separata


def test_providers_tiingo_spenta_non_e_fonte_muta(client, news, monkeypatch):
    """10/10 (Opus 5.5): Tiingo News spenta per decisione PM. La UI legge `fonti_mute`
    (= providers_blocked) e `ultimo_giro.stato`: Tiingo sta in `fonti_spente`, anche se
    l'ultimo esito registrato in questo processo era un 403."""
    from bellomberg.market_data import tiingo_news
    monkeypatch.setattr(tiingo_news, "FONTE_SPENTA", True)
    monkeypatch.setattr(tiingo_news, "TIINGO_KEY", "chiave-finta-tiingo")
    tiingo_news._segna_stato("HTTP_403", http=403)
    try:
        out = client.get("/news/providers").json()
    finally:
        tiingo_news.reset_status()
    assert "tiingo" not in (out["fonti_mute"] or {}), out["fonti_mute"]
    assert out["fonti_spente"]["tiingo"].startswith("SPENTA: Tiingo News")


def test_macro_reddit_mai_chiesto_e_spenta_dichiarata(client, news):
    out = client.get("/news/macro?include_reddit=true").json()
    assert out["count"] == 1
    assert "SPENTA" in out["fonti_spente"]["reddit"]
    assert news and news[0]["include_reddit"] is False
