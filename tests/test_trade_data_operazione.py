# -*- coding: utf-8 -*-
"""La DATA del trade la sceglie il PM, le posizioni si RICOSTRUISCONO per data e il legame
trade->decisione e' esplicito o dichiarato assente (lotto trade_date, 12/09/2026, Fable 5.1).

I fatti da cui parte il lotto (recon/trade_date.md, 12/09):
  - `POST /trade` non accettava una data: il blotter scriveva sempre l'orologio del server;
    i trade storici importati hanno l'ora `12:00:00` per convenzione e dal payload non si
    distingueva un mezzogiorno vero da uno finto;
  - `positions.prezzo_medio` si aggiorna nell'ordine di SCRITTURA: un lotto retrodatato
    dopo una vendita lasciava il realized di quella vendita al valore vecchio;
  - `linked_decision_id` era accettato senza controlli e mai inviato; NULL voleva dire sia
    «non so» sia «non esiste una decisione»; l'inferenza non era esclusiva (un trade poteva
    contare per due decisioni);
  - `GET /trades` e la memoria del Capo ordinavano per id, non per data;
  - la performance dichiarava «attendibile da» in quattro posti diversi e l'IRR ripiegava
    zitto a FX=1.0.

Simboli, importi e date INVENTATI; DB su tmp_path; nessun LLM; nessuna rete (FX live e serie
storica stubbati); nessuna scrittura fuori da tmp_path.
"""
import importlib.util
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from bellomberg.storage import memory_db
from bellomberg.storage.memory_db import MemoryDB


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    return MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                    chroma_path=str(tmp_path / "chroma"))


def _serie_fx_assente(*_a, **_k):
    raise RuntimeError("rete spenta nel test: nessuna serie FX storica")


@pytest.fixture
def api(db, monkeypatch):
    """L'endpoint vero con DB su tmp, FX live FINTO (USD 0.90) e serie storica ASSENTE."""
    import bellomberg.api.bellomberg_api as api
    from bellomberg.cli import price_updater
    import bellomberg.portfolio.portfolio_analytics as pa
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(price_updater, "get_fx_to_eur_con_fonte",
                        lambda c: (1.0, "identity") if str(c).upper() == "EUR" else (0.90, "live"))
    monkeypatch.setattr(price_updater, "get_fx_to_eur",
                        lambda c: 1.0 if str(c).upper() == "EUR" else 0.90)
    monkeypatch.setattr(pa, "_build_fx_history", _serie_fx_assente)
    pa._FX_DF_CACHE.clear()
    db.apply_cash_movement("DEPOSIT", 100000, data="2026-01-02")
    return api


def _post(api, **kw):
    body = dict(ticker="ALFA.MI", action="BUY", quantita=10, prezzo=10.0, valuta="EUR")
    body.update(kw)
    return api.log_trade(api.TradeIn(**body))


def _riga(db, tid):
    with db._conn() as conn:
        return dict(conn.execute("SELECT * FROM trade_history WHERE id=?", (tid,)).fetchone())


def _pos(db, ticker):
    with db._conn() as conn:
        r = conn.execute("SELECT quantita, prezzo_medio, data_apertura, is_active FROM positions "
                         "WHERE ticker=?", (ticker,)).fetchone()
    return dict(r) if r else None


def _stato(db):
    with db._conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]
        cash = conn.execute("SELECT balance_cents FROM cash_state WHERE singleton_id=1").fetchone()
        pos = [tuple(r) for r in conn.execute(
            "SELECT ticker, quantita, prezzo_medio, is_active FROM positions ORDER BY ticker")]
        real = [tuple(r) for r in conn.execute(
            "SELECT id, realized_local, realized_eur FROM trade_history ORDER BY id")]
    return n, (cash[0] if cash else None), pos, real


def _decisione(db, ticker="ALFA.MI", action="ADD", status="EXECUTED", ore_fa=2.0, eur=1000.0):
    ts = (datetime.now() - timedelta(hours=ore_fa)).isoformat(timespec="seconds")
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT INTO decisions (timestamp, action, ticker, eur_amount, timing, confidence, "
            "status) VALUES (?,?,?,?,?,?,?)", (ts, action, ticker, eur, "a mercato", "MEDIA", status))
        return cur.lastrowid


def _dec_row(db, did):
    with db._conn() as conn:
        return dict(conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone())


def _fx_df(righe):
    idx = pd.to_datetime([g for g, _ in righe])
    cols = sorted({c for _, r in righe for c in r})
    return pd.DataFrame({c: [r.get(c) for _, r in righe] for c in cols}, index=idx)


# ============================================================ 1. la data: validazione

def test_data_futura_400_e_non_scrive(api, db):
    domani = (date.today() + timedelta(days=1)).isoformat()
    prima = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, data=domani)
    assert exc.value.status_code == 400
    assert "futur" in exc.value.detail.lower()
    assert _stato(db) == prima


def test_data_prima_del_2000_400(api, db):
    prima = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, data="1999-12-31")
    assert exc.value.status_code == 400 and "2000" in exc.value.detail
    assert _stato(db) == prima


def test_data_non_iso_400(api, db):
    prima = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, data="12/09/2026")
    assert exc.value.status_code == 400 and "ISO" in exc.value.detail
    assert _stato(db) == prima


def test_sola_data_diventa_mezzogiorno_per_convenzione_e_lo_dice(api, db):
    out = _post(api, data="2026-09-01")
    r = _riga(db, out["trade_id"])
    assert r["data"] == "2026-09-01T12:00:00"
    assert r["ora_convenzionale"] == 1
    assert out["data"] == "2026-09-01T12:00:00" and out["ora_convenzionale"] is True
    assert r["created_at"]                      # registrata ADESSO, non il 01/09
    assert r["created_at"][:10] == datetime.utcnow().strftime("%Y-%m-%d")


def test_iso_con_ora_resta_intera_e_l_ora_e_misurata(api, db):
    out = _post(api, data="2026-09-01T09:31:07")
    r = _riga(db, out["trade_id"])
    assert r["data"] == "2026-09-01T09:31:07" and r["ora_convenzionale"] == 0
    assert out["ora_convenzionale"] is False


def test_senza_data_e_adesso_con_ora_misurata(api, db):
    out = _post(api)
    r = _riga(db, out["trade_id"])
    assert r["data"][:10] == date.today().isoformat() and len(r["data"]) == 19
    assert r["ora_convenzionale"] == 0
    assert out["ricalcolo"] is None            # non retrodatato: nessuna ricostruzione


def test_log_trade_parsa_sempre_anche_dagli_script(db):
    """La difesa sta all'INGRESSO, non a valle: nessuna stringa qualsiasi in `data`."""
    with pytest.raises(ValueError, match="ISO"):
        db.log_trade("ALFA.MI", "BUY", 1, 10.0, data="non-una-data")
    tid = db.log_trade("ALFA.MI", "BUY", 1, 10.0, data="2026-03-01")
    r = _riga(db, tid)
    assert r["data"] == "2026-03-01T12:00:00" and r["ora_convenzionale"] == 1


# ============================================================ 2. ordinamento

def test_get_trades_ordina_per_data_poi_id(api, db):
    a = db.log_trade("ALFA.MI", "BUY", 1, 10.0, data="2026-05-01T10:00:00")   # id 1
    b = db.log_trade("ALFA.MI", "BUY", 1, 10.0, data="2026-03-01T10:00:00")   # id 2
    c = db.log_trade("ALFA.MI", "BUY", 1, 10.0, data="2026-04-01T10:00:00")   # id 3
    d = db.log_trade("ALFA.MI", "BUY", 1, 10.0, data="2026-05-01T10:00:00")   # id 4, pari data
    assert [t["id"] for t in db.get_recent_trades(n=10)] == [d, a, c, b]
    rotta = api.list_trades(limit=10)["trades"]
    assert [t["id"] for t in rotta] == [d, a, c, b]
    # F14 legge «registrata il» e l'ora convenzionale: le colonne arrivano
    assert "created_at" in rotta[0] and "ora_convenzionale" in rotta[0]
    assert "link_origin" in rotta[0]


# ============================================================ 3. il legame esplicito

def test_link_ticker_diverso_400(api, db):
    did = _decisione(db, ticker="BETA.MI", action="ADD")
    prima = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, linked_decision_id=did)
    assert exc.value.status_code == 400 and "BETA.MI" in exc.value.detail
    assert _stato(db) == prima


def test_link_verso_incompatibile_400(api, db):
    did = _decisione(db, action="TRIM")          # SHORT, il trade e' un BUY (LONG)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, linked_decision_id=did)
    assert exc.value.status_code == 400 and "verso" in exc.value.detail.lower()


def test_link_id_inesistente_400(api, db):
    with pytest.raises(api.HTTPException) as exc:
        _post(api, linked_decision_id=9999)
    assert exc.value.status_code == 400 and "9999" in exc.value.detail


def test_link_stato_skipped_400(api, db):
    did = _decisione(db, status="SKIPPED")
    with pytest.raises(api.HTTPException) as exc:
        _post(api, linked_decision_id=did)
    assert exc.value.status_code == 400 and "SKIPPED" in exc.value.detail


def test_link_e_senza_decisione_insieme_400(api, db):
    did = _decisione(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, linked_decision_id=did, senza_decisione=True)
    assert exc.value.status_code == 400


def test_link_pending_accettato_con_nota_e_lo_stato_resta_al_pm(api, db):
    did = _decisione(db, status="PENDING", action="BUY")
    out = _post(api, linked_decision_id=did)
    r = _riga(db, out["trade_id"])
    assert r["linked_decision_id"] == did and r["link_origin"] == "explicit"
    assert out["decisione"]["id"] == did and out["decisione"]["status"] == "PENDING"
    assert "PENDING" in out["decisione"]["nota"]
    assert _dec_row(db, did)["status"] == "PENDING"     # nessun cambio automatico (F15)


def test_link_executed_accettato_senza_nota_pending(api, db):
    did = _decisione(db, status="EXECUTED", action="ADD")
    out = _post(api, linked_decision_id=did)
    assert out["decisione"]["status"] == "EXECUTED"
    assert "PENDING" not in (out["decisione"].get("nota") or "")


# ============================================================ 4. l'inferenza: none la spegne, ed e' esclusiva

def test_senza_decisione_scrive_none_e_spegne_l_inferenza(api, db):
    did = _decisione(db, action="ADD", status="EXECUTED", ore_fa=2)
    out = _post(api, senza_decisione=True)
    assert _riga(db, out["trade_id"])["link_origin"] == "none"
    assert out["link_origin"] == "none"
    assert db.esecuzioni_delle_decisioni([_dec_row(db, did)]) == {}


def test_non_so_scrive_unknown_e_l_inferenza_resta_ammessa(api, db):
    did = _decisione(db, action="ADD", status="EXECUTED", ore_fa=2)
    out = _post(api)
    assert _riga(db, out["trade_id"])["link_origin"] == "unknown"
    es = db.esecuzioni_delle_decisioni([_dec_row(db, did)])
    assert es[did]["inferito"] is True and es[did]["trade_ids"] == [out["trade_id"]]


def test_inferenza_esclusiva_un_trade_conta_per_una_sola_decisione(api, db):
    """Due ADD eseguiti sullo stesso titolo a 5 e a 2 ore fa, un trade a 1 ora fa: conta
    solo per la decisione PIU' VICINA nel tempo (2 ore fa). Un secondo trade a 4 ore fa
    va alla prima."""
    lontana = _decisione(db, action="ADD", status="EXECUTED", ore_fa=5, eur=1000)
    vicina = _decisione(db, action="ADD", status="EXECUTED", ore_fa=2, eur=1000)
    t1 = db.log_trade("ALFA.MI", "BUY", 10, 10.0,
                      data=(datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"))
    es = db.esecuzioni_delle_decisioni([_dec_row(db, lontana), _dec_row(db, vicina)])
    assert es[vicina]["trade_ids"] == [t1]
    assert lontana not in es
    t2 = db.log_trade("ALFA.MI", "BUY", 5, 10.0,
                      data=(datetime.now() - timedelta(hours=4)).isoformat(timespec="seconds"))
    es = db.esecuzioni_delle_decisioni([_dec_row(db, lontana), _dec_row(db, vicina)])
    assert es[vicina]["trade_ids"] == [t1] and es[lontana]["trade_ids"] == [t2]


# ============================================================ 5. retrodatazione: la posizione si ricostruisce

def test_retrodatazione_oltre_una_vendita_ricalcola_carico_e_realized(api, db):
    """Saldi attesi CALCOLATI A MANO (costo medio):
      BUY 10 @ 10 (01/03)  -> qty 10, avg 10
      SELL 5 @ 30 (01/05)  -> realized 5*(30-10) = 100, qty 5, avg 10         [stato PRIMA]
    poi entra RETRODATATO BUY 10 @ 20 al 01/04 (fra i due). Replay per data:
      BUY 10 @ 10 -> qty 10, avg 10
      BUY 10 @ 20 -> qty 20, avg (100+200)/20 = 15
      SELL 5 @ 30 -> realized 5*(30-15) = 75, qty 15, avg 15                   [stato DOPO]
    Cassa: 100000 - 100 + 150 - 200 = 99850 (scalata sul saldo corrente)."""
    _post(api, data="2026-03-01", quantita=10, prezzo=10.0)
    sell = _post(api, action="SELL", data="2026-05-01", quantita=5, prezzo=30.0)["trade_id"]
    assert _riga(db, sell)["realized_local"] == 100 and _pos(db, "ALFA.MI")["prezzo_medio"] == 10
    out = _post(api, data="2026-04-01", quantita=10, prezzo=20.0)
    rc = out["ricalcolo"]
    assert rc is not None
    assert rc["prima"]["quantita"] == 5 and rc["prima"]["prezzo_medio"] == 10
    assert rc["prima"]["realized"] == 100
    assert rc["dopo"]["quantita"] == 15 and rc["dopo"]["prezzo_medio"] == 15
    assert rc["dopo"]["realized"] == 75
    assert rc["trade_successivi"] == [sell]
    assert rc["prima"]["data_apertura"] == "2026-03-01T12:00:00"
    assert rc["dopo"]["data_apertura"] == "2026-03-01T12:00:00"
    p = _pos(db, "ALFA.MI")
    assert (p["quantita"], p["prezzo_medio"], p["is_active"]) == (15, 15, 1)
    r = _riga(db, sell)
    assert r["realized_local"] == 75 and r["realized_eur"] == 75
    assert out["cash_disponibile_eur"] == 99850.0
    assert "saldo corrente" in out["cassa_nota"]


def test_retrodatazione_prima_di_un_altro_acquisto_riporta_i_successivi(api, db):
    b2 = _post(api, data="2026-04-01", quantita=10, prezzo=20.0)["trade_id"]
    out = _post(api, data="2026-03-01", quantita=10, prezzo=10.0)
    rc = out["ricalcolo"]
    assert rc["trade_successivi"] == [b2]
    assert rc["dopo"]["quantita"] == 20 and rc["dopo"]["prezzo_medio"] == 15
    # la data di apertura torna al PRIMO acquisto della detenzione, non al piu' recente
    assert rc["prima"]["data_apertura"] == "2026-04-01T12:00:00"
    assert rc["dopo"]["data_apertura"] == "2026-03-01T12:00:00"
    assert _pos(db, "ALFA.MI")["data_apertura"] == "2026-03-01T12:00:00"


def test_retrodatazione_impossibile_409_e_non_scrive_nulla(api, db):
    """Una SELL retrodatata PRIMA di qualunque acquisto: il replay non torna (vendita
    fantasma). 409, e ne' il trade ne' la cassa ne' la posizione si muovono."""
    _post(api, data="2026-03-01", quantita=10, prezzo=10.0)
    prima = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, action="SELL", data="2026-02-01", quantita=5, prezzo=30.0)
    assert exc.value.status_code == 409
    assert "replay" in exc.value.detail.lower() or "ricostru" in exc.value.detail.lower()
    assert _stato(db) == prima


def test_trade_di_oggi_dopo_lo_storico_non_ricostruisce(api, db):
    _post(api, data="2026-03-01", quantita=10, prezzo=10.0)
    out = _post(api, quantita=10, prezzo=20.0)
    assert out["ricalcolo"] is None
    assert _pos(db, "ALFA.MI")["prezzo_medio"] == 15


# ============================================================ 6. fx_fonte dichiarata

def test_fx_storico_quando_la_serie_lo_ha(api, db, monkeypatch):
    import bellomberg.portfolio.portfolio_analytics as pa
    monkeypatch.setattr(pa, "_build_fx_history",
                        lambda c, s, e: _fx_df([("2026-06-01", {"USD": 0.80})]))
    out = _post(api, ticker="BETA", valuta="USD", data="2026-06-01", quantita=10, prezzo=100.0)
    r = _riga(db, out["trade_id"])
    assert r["fx_fonte"] == "storico"
    assert out["fx"]["fonte"] == "storico" and out["fx"]["tasso"] == pytest.approx(0.80)
    assert out["cash_disponibile_eur"] == pytest.approx(100000 - 800)     # 10 x 100 x 0.80
    assert "storico" in out["cassa_nota"] and "saldo corrente" in out["cassa_nota"]


def test_fx_corrente_etichettato_quando_la_serie_manca(api, db):
    out = _post(api, ticker="BETA", valuta="USD", data="2026-06-01", quantita=10, prezzo=100.0)
    r = _riga(db, out["trade_id"])
    assert r["fx_fonte"] == "corrente"
    assert out["fx"]["fonte"] == "corrente" and out["fx"]["tasso"] == pytest.approx(0.90)
    assert "storic" in out["fx"]["nota"].lower()          # dice PERCHE' non e' storico
    assert out["cash_disponibile_eur"] == pytest.approx(100000 - 900)


def test_trade_in_eur_ha_fx_identity_e_quello_di_oggi_in_valuta_e_corrente(api, db):
    a = _post(api)
    assert _riga(db, a["trade_id"])["fx_fonte"] == "identity"
    b = _post(api, ticker="BETA", valuta="USD", quantita=1, prezzo=100.0)
    assert _riga(db, b["trade_id"])["fx_fonte"] == "corrente"


# ============================================================ 7. la memoria del Capo

def test_la_memoria_del_capo_ordina_per_data_e_dichiara_il_senza_decisione(api, db, monkeypatch):
    from bellomberg.agents import scorekeeper, reflection
    monkeypatch.setattr(scorekeeper, "get_track_record_for_specialist", lambda *_a, **_k: "")
    monkeypatch.setattr(scorekeeper, "get_track_record_for_capo", lambda *_a, **_k: "")
    monkeypatch.setattr(reflection, "get_latest_lesson_block", lambda *_a, **_k: "")
    _post(api, ticker="ALFA.MI", data="2026-05-01", senza_decisione=True)
    _post(api, ticker="BETA.MI", data="2026-03-01")
    ctx = db.build_capo_memory_context()
    i_alfa, i_beta = ctx.index("[2026-05-01] BUY"), ctx.index("[2026-03-01] BUY")
    assert i_alfa < i_beta                              # per data, non per id
    riga = ctx[i_alfa: ctx.index("\n", i_alfa)]
    assert "senza decisione" in riga


# ============================================================ 8. copertura della performance (TWR)

class _DBPonte:
    """Il DB vero di tmp per `_conn`, un summary finto: `compute_twr_payload` chiede solo quello."""
    def __init__(self, db):
        self._db = db
        self.db_path = db.db_path

    def _conn(self):
        return self._db._conn()

    def get_portfolio_summary(self):
        return {"nav_total_eur": 101000.0, "totale_valore_mercato_eur": 90000.0}


def _snap(d, nav):
    return {"date": d, "nav_total_eur": nav, "invested_eur": nav, "cash_eur": 0.0,
            "source": "test", "created_at": d + "T17:00:00"}


def test_il_payload_twr_porta_la_copertura(db, monkeypatch):
    import bellomberg.portfolio.twr_engine as te
    db.log_trade("ALFA.MI", "BUY", 10, 10.0, data="2026-05-01T12:00:00")
    db.log_trade("ALFA.MI", "BUY", 10, 10.0, data="2026-06-10T12:00:00")
    db.log_trade("ALFA.MI", "BUY", 10, 10.0, data="2026-06-14T12:00:00")
    snaps = [_snap("2026-06-11", 100000.0), _snap("2026-06-12", 100500.0),
             _snap("2026-06-14", 101000.0), _snap("2026-06-15", 101000.0)]   # 13/06 manca
    ctx = {"dates": [s["date"] for s in snaps], "values_eur": [s["nav_total_eur"] for s in snaps],
           "flows_eur": [0.0] * 4, "regimes": ["official"] * 4, "notes": [],
           "snapshots": snaps, "official_since": "2026-06-11", "seamless_transition": False,
           "ledger": []}
    monkeypatch.setattr(te, "get_official_series", lambda: ctx)
    monkeypatch.setattr(te, "MemoryDB", lambda: _DBPonte(db))
    monkeypatch.setattr(te, "_build_irr_flows", lambda c, l: ([], 0.0, "stub test"))
    monkeypatch.setattr("bellomberg.market_data.market_inputs.get_risk_free", lambda c="EUR": 0.03)
    te._CACHE.clear()
    p = te.compute_twr_payload(force=True)
    c = p["copertura"]
    assert c["primo_trade"] == "2026-05-01"
    assert c["primo_snapshot"] == "2026-06-11" and c["official_since"] == "2026-06-11"
    assert c["n_trade_prima_del_primo_snapshot"] == 2
    assert c["giorni_senza_snapshot"] == 1 and c["ultimo_snapshot"] == "2026-06-15"
    assert "2026-06-11" in c["nota"] and "2026-05-01" in c["nota"]
    # additivo: i campi di prima non si toccano
    assert p["regime_summary"]["official_since"] == "2026-06-11"
    assert p["last_snapshot_created_at"] == "2026-06-15T17:00:00"


def test_irr_senza_fx_e_dichiarato_non_calcolabile(db, monkeypatch):
    """twr_engine.py `fx = get_fx_to_eur(ccy) or 1.0` era un ripiego zitto (regola 14/07)."""
    import bellomberg.portfolio.twr_engine as te
    from bellomberg.cli import price_updater
    db.log_trade("BETA", "BUY", 10, 100.0, valuta="USD", data="2026-05-01T12:00:00")
    monkeypatch.setattr(te, "MemoryDB", lambda: _DBPonte(db))
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda c: None)
    flows, terminal, basis = te._build_irr_flows({"snapshots": [], "ledger": []},
                                                 {"totale_valore_mercato_eur": 1000.0})
    assert flows == [] and "USD" in basis and "IRR non calcolabile" in basis


def test_preview_sola_lettura_congela_fx_e_si_consuma_una_sola_volta(api, db, monkeypatch):
    from bellomberg.cli import price_updater
    body = api.TradeIn(ticker="BETA", action="BUY", quantita=10, prezzo=100, valuta="USD",
                       data="2026-06-01")
    prima = _stato(db)
    preview = api.preview_trade(body)
    assert _stato(db) == prima
    assert preview["cash_delta_eur"] == -900
    assert preview["cash_disponibile_eur"] == 99100
    assert preview["fx"]["fonte"] == "corrente" and preview["fx"]["nota"]
    monkeypatch.setattr(price_updater, "get_fx_to_eur_con_fonte", lambda _: (0.50, "live"))
    committed = api.log_trade(api.TradeIn(**{**body.model_dump(), "preview_id": preview["preview_id"]}))
    assert committed["cash_disponibile_eur"] == 99100
    assert committed["fx"]["tasso"] == 0.90
    dopo = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        api.log_trade(api.TradeIn(**{**body.model_dump(), "preview_id": preview["preview_id"]}))
    assert exc.value.status_code == 409 and _stato(db) == dopo


@pytest.mark.parametrize("drift", ["cash", "position", "trade", "decision", "body", "expired"])
def test_preview_rifiuta_stato_o_corpo_cambiato_e_scadenza(api, db, monkeypatch, drift):
    did = _decisione(db, action="BUY")
    _post(api)
    body = dict(ticker="ALFA.MI", action="BUY", quantita=1, prezzo=10, valuta="EUR",
                linked_decision_id=did)
    preview = api.preview_trade(api.TradeIn(**body))
    if drift == "cash":
        db.apply_cash_movement("DEPOSIT", 10)
    elif drift in ("position", "trade", "decision"):
        sql = {"position": "UPDATE positions SET prezzo_medio=20",
               "trade": "UPDATE trade_history SET prezzo=11",
               "decision": "UPDATE decisions SET status='SKIPPED'"}[drift]
        with db._conn() as conn:
            conn.execute(sql)
    elif drift == "body":
        body["quantita"] = 2
    else:
        now = api.time.monotonic()
        monkeypatch.setattr(api.time, "monotonic", lambda: now + 3600)
    prima = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        api.log_trade(api.TradeIn(**body, preview_id=preview["preview_id"]))
    assert exc.value.status_code == 409 and _stato(db) == prima


def test_link_trade_prima_della_decisione_rifiutato(api, db):
    did = _decisione(db)
    prima = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, data="2026-03-01", linked_decision_id=did)
    assert exc.value.status_code == 400 and _stato(db) == prima


def test_inferenza_non_cambia_se_il_consumer_chiede_una_sola_decisione(api, db):
    lontana = _decisione(db, ore_fa=5)
    vicina = _decisione(db, ore_fa=2)
    _post(api)
    assert db.esecuzioni_delle_decisioni([_dec_row(db, lontana)]) == {}
    assert vicina in db.esecuzioni_delle_decisioni([_dec_row(db, vicina)])


def test_replay_valuta_senza_cambio_storico_dichiara_realized_eur_assente(api, db):
    _post(api, ticker="BETA", valuta="USD", data="2026-03-01", quantita=10, prezzo=10)
    sell = _post(api, ticker="BETA", valuta="USD", action="SELL", data="2026-05-01",
                 quantita=5, prezzo=30)["trade_id"]
    out = _post(api, ticker="BETA", valuta="USD", data="2026-04-01", quantita=10, prezzo=20)
    assert _riga(db, sell)["realized_local"] == 75
    assert _riga(db, sell)["realized_eur"] is None
    assert "FX" in " ".join(out["ricalcolo"]["note"])


def test_link_parziale_multiplo_cumulato_ed_esposto_senza_cambiare_stato(api, db):
    did = _decisione(db, status="PARTIAL", action="BUY", eur=500)
    first = _post(api, linked_decision_id=did, quantita=10, prezzo=10)
    second = _post(api, linked_decision_id=did, quantita=20, prezzo=10)
    listed = api.list_decisions(limit=10)["decisions"][0]
    assert listed["esecuzione"]["trade_ids"] == [first["trade_id"], second["trade_id"]]
    assert listed["esecuzione"]["eur"] == 300 and listed["esecuzione"]["pct"] == 60
    assert listed["status"] == "PARTIAL"


def test_irr_rifiuta_un_fx_numerico_di_fallback(db, monkeypatch):
    from bellomberg.portfolio import twr_engine as te
    from bellomberg.cli import price_updater
    db.log_trade("BETA", "BUY", 10, 100, valuta="USD", data="2026-05-01")
    monkeypatch.setattr(te, "MemoryDB", lambda: _DBPonte(db))
    monkeypatch.setattr(price_updater, "get_fx_to_eur_con_fonte", lambda _: (0.9, "fallback"))
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda _: 0.9)
    flows, terminal, basis = te._build_irr_flows({"snapshots": [], "ledger": []}, {})
    assert flows == [] and "fallback" in basis and "IRR non calcolabile" in basis


def test_errore_cache_dopo_commit_non_dichiara_trade_fallito(api, db, monkeypatch):
    from bellomberg.portfolio import twr_engine
    def fail():
        raise RuntimeError("cache occupata")
    monkeypatch.setattr(twr_engine, "invalidate_cache", fail)
    out = _post(api)
    assert out["ok"] is True and _riga(db, out["trade_id"])["quantita"] == 10
    assert "cache" in out["performance_note"].lower()


def test_rollback_se_update_realized_del_replay_fallisce(api, db):
    _post(api, data="2026-03-01")
    sell = _post(api, action="SELL", quantita=5, prezzo=30, data="2026-05-01")["trade_id"]
    with db._conn() as conn:
        conn.execute(f"CREATE TRIGGER fail_replay BEFORE UPDATE OF realized_local ON trade_history "
                     f"WHEN OLD.id={sell} BEGIN SELECT RAISE(ABORT, 'fault replay'); END")
    before = _stato(db)
    with pytest.raises(api.HTTPException):
        _post(api, data="2026-04-01", prezzo=20)
    assert _stato(db) == before


def test_trade_storico_non_confrontato_col_prezzo_di_oggi(api, db):
    _post(api, data="2026-03-01", prezzo=10)
    with db._conn() as conn:
        conn.execute("INSERT INTO position_prices(ticker,prezzo,valuta,timestamp) VALUES (?,?,?,?)",
                     ("ALFA.MI", 100, "EUR", datetime.now().isoformat(timespec="seconds")))
    out = _post(api, data="2026-04-01", prezzo=10)
    assert out["ok"] and "storico" in out["guardia_note"] and "n.d." in out["guardia_note"]


def test_replay_non_inventa_una_posizione_iniziale(api, db):
    _post(api, data="2026-04-01")
    with db._conn() as conn:
        conn.execute("UPDATE positions SET quantita=25 WHERE ticker='ALFA.MI'")
    before = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, data="2026-03-01")
    assert exc.value.status_code == 409 and "iniziale" in exc.value.detail
    assert _stato(db) == before


def test_replay_rifiuta_date_legacy_illeggibili(api, db):
    _post(api, data="2026-04-01")
    with db._conn() as conn:
        conn.execute("UPDATE trade_history SET data='data sconosciuta'")
    before = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, data="2026-03-01")
    assert exc.value.status_code == 409 and "data" in exc.value.detail
    assert _stato(db) == before


def test_current_realized_failure_rolls_back_trade_and_cash(api, db):
    _post(api)
    with db._conn() as conn:
        conn.execute("CREATE TRIGGER fail_realized BEFORE UPDATE OF realized_local ON trade_history "
                     "BEGIN SELECT RAISE(ABORT, 'fault realized'); END")
    before = _stato(db)
    with pytest.raises(api.HTTPException):
        _post(api, action="SELL", quantita=5, prezzo=30)
    assert _stato(db) == before


def test_retrodatazione_intraday_ricostruisce_e_non_usa_il_filtro_posizione_attiva(api, db):
    at = datetime.now() - timedelta(minutes=30)
    first = (at - timedelta(minutes=20)).isoformat(timespec="seconds")
    middle = (at - timedelta(minutes=10)).isoformat(timespec="seconds")
    last = at.isoformat(timespec="seconds")
    _post(api, data=first, quantita=10, prezzo=10)
    sell = _post(api, data=last, action="SELL", quantita=10, prezzo=30)["trade_id"]
    assert _pos(db, "ALFA.MI")["is_active"] == 0
    before = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, data=middle, action="SELL", quantita=1, prezzo=20)
    assert exc.value.status_code == 409 and "replay" in exc.value.detail
    assert _stato(db) == before
    out = _post(api, data=middle, action="ADD", quantita=10, prezzo=20)
    assert out["ricalcolo"]["trade_successivi"] == [sell]
    assert _pos(db, "ALFA.MI")["quantita"] == 10
    assert _pos(db, "ALFA.MI")["prezzo_medio"] == 15
    assert _riga(db, sell)["realized_local"] == 150


def test_replay_usa_fx_del_giorno_di_ogni_vendita_e_non_del_nuovo_acquisto(api, db, monkeypatch):
    from bellomberg.portfolio import portfolio_analytics as pa
    monkeypatch.setattr(pa, "_build_fx_history", lambda *args: _fx_df([
        ("2026-03-01", {"USD": 0.70}), ("2026-04-01", {"USD": 0.75}),
        ("2026-05-01", {"USD": 0.80})]))
    _post(api, ticker="BETA", valuta="USD", data="2026-03-01", prezzo=10)
    sell = _post(api, ticker="BETA", valuta="USD", data="2026-05-01", action="SELL",
                 quantita=5, prezzo=30)["trade_id"]
    out = _post(api, ticker="BETA", valuta="USD", data="2026-04-01", prezzo=20)
    assert _riga(db, sell)["realized_local"] == 75
    assert _riga(db, sell)["realized_eur"] == 60
    assert out["fx"]["tasso"] == 0.75
    assert out["cash_disponibile_eur"] == 99900
    assert out["ricalcolo"]["valuta"] == "USD"


def test_ora_convenzionale_non_decide_il_legame_intraday(api, db):
    first = _decisione(db)
    second = _decisione(db)
    with db._conn() as conn:
        conn.execute("UPDATE decisions SET timestamp='2026-03-01T10:00:00' WHERE id=?", (first,))
        conn.execute("UPDATE decisions SET timestamp='2026-03-01T14:00:00' WHERE id=?", (second,))
    _post(api, data="2026-03-01")
    assert db.esecuzioni_delle_decisioni([_dec_row(db, first), _dec_row(db, second)]) == {}


def test_inferenza_non_sceglie_un_id_se_due_decisioni_hanno_lo_stesso_istante(api, db):
    first = _decisione(db)
    second = _decisione(db)
    with db._conn() as conn:
        conn.execute("UPDATE decisions SET timestamp=(SELECT timestamp FROM decisions WHERE id=?) WHERE id=?",
                     (first, second))
    _post(api)
    assert db.esecuzioni_delle_decisioni([_dec_row(db, first), _dec_row(db, second)]) == {}


@pytest.mark.parametrize("change", [{"ticker": " "}, {"action": "invented"}])
def test_preview_non_approva_un_identificativo_che_la_scrittura_rifiuta(api, db, change):
    body = dict(ticker="ALFA.MI", action="BUY", quantita=10, prezzo=10, valuta="EUR")
    body.update(change)
    before = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        api.preview_trade(api.TradeIn(**body))
    assert exc.value.status_code == 400 and _stato(db) == before


@pytest.mark.parametrize("missing", ["", "   "])
def test_replay_non_sostituisce_la_data_legacy_assente_con_oggi(api, db, missing):
    _post(api, data="2026-03-01")
    sell = _post(api, data="2026-05-01", action="SELL", quantita=5, prezzo=30)["trade_id"]
    with db._conn() as conn:
        conn.execute("UPDATE trade_history SET data=? WHERE id=?", (missing, sell))
    before = _stato(db)
    with pytest.raises(api.HTTPException) as exc:
        _post(api, data="2026-04-01", prezzo=20)
    assert exc.value.status_code == 409 and "data" in exc.value.detail
    assert _stato(db) == before


# ============================================================ 9. migrazione e script

def test_migrazione_10_colonne_additive_e_idempotente(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    path = str(tmp_path / "data" / "m.db")
    MemoryDB(db_path=path, chroma_path=str(tmp_path / "chroma"))
    MemoryDB(db_path=path, chroma_path=str(tmp_path / "chroma"))
    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_history)")}
        versioni = [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")]
    assert {"ora_convenzionale", "link_origin", "fx_fonte"} <= cols
    assert versioni == [m[0] for m in memory_db.MIGRATIONS] and 10 in versioni
    assert len(versioni) == len(set(versioni))


def _script():
    path = Path(__file__).parents[1] / "tools" / "migrations" / "marca_ora_convenzionale.py"
    spec = importlib.util.spec_from_file_location("marca_ora_convenzionale", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_marca_le_righe_legacy_a_mezzogiorno_solo_con_apply(db, tmp_path, monkeypatch):
    """Le righe LEGACY (ora_convenzionale NULL) con `T12:00:00` esatte sono la convenzione
    dell'import storico: il dry-run le elenca senza scrivere; --apply le marca con backup;
    una riga gia' decisa (0 o 1) non si tocca; il ripristino dalla ricevuta le riporta a NULL."""
    m = _script()
    with db._conn() as conn:
        conn.execute("INSERT INTO trade_history (ticker, action, quantita, prezzo, valuta, data) "
                     "VALUES ('ALFA.MI','BUY',1,10,'EUR','2026-02-01T12:00:00')")           # legacy: candidata
        conn.execute("INSERT INTO trade_history (ticker, action, quantita, prezzo, valuta, data) "
                     "VALUES ('ALFA.MI','BUY',1,10,'EUR','2026-02-02T16:58:25')")           # legacy: ora vera
        conn.execute("INSERT INTO trade_history (ticker, action, quantita, prezzo, valuta, data, "
                     "ora_convenzionale) VALUES ('ALFA.MI','BUY',1,10,'EUR','2026-02-03T12:00:00',0)")  # decisa
    monkeypatch.setattr(m, "_porta_occupata", lambda: False)
    dry = m.run(db.db_path, apply=False, backup_dir=str(tmp_path / "bk"))
    assert dry["candidate"] == [1] and dry["scritte"] == 0
    with db._conn() as conn:
        assert [r[0] for r in conn.execute("SELECT ora_convenzionale FROM trade_history ORDER BY id")] == [None, None, 0]
    esito = m.run(db.db_path, apply=True, backup_dir=str(tmp_path / "bk"))
    assert esito["scritte"] == 1 and Path(esito["backup"]).exists() and Path(esito["ricevuta"]).exists()
    with db._conn() as conn:
        assert [r[0] for r in conn.execute("SELECT ora_convenzionale FROM trade_history ORDER BY id")] == [1, None, 0]
    di_nuovo = m.run(db.db_path, apply=True, backup_dir=str(tmp_path / "bk"))
    assert di_nuovo["scritte"] == 0                               # idempotente
    m.ripristina(db.db_path, esito["ricevuta"])
    with db._conn() as conn:
        assert [r[0] for r in conn.execute("SELECT ora_convenzionale FROM trade_history ORDER BY id")] == [None, None, 0]


def test_script_rifiuta_con_backend_acceso(db, tmp_path, monkeypatch):
    m = _script()
    monkeypatch.setattr(m, "_porta_occupata", lambda: True)
    with pytest.raises(RuntimeError, match="porta"):
        m.run(db.db_path, apply=True, backup_dir=str(tmp_path / "bk"))
