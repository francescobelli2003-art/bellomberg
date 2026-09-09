"""Pacchetto "verita' dei numeri" Lotto B (riallineamento 23/07, audit/20):
F-CONT-1 FX storico nella serie CB · F-CONT-4 staleness dichiarata ·
F6 realized persistito + backfill · F5 riconciliazione con soglia.
Tutto offline: DB tmp, FX mockati, zero rete.
"""
import os
import sqlite3
import sys

import pytest

from bellomberg.storage.memory_db import MIGRATIONS, MemoryDB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    path = str(tmp_path / "data" / "consigliere_test.db")
    return MemoryDB(db_path=path, chroma_path=str(tmp_path / "data" / "chroma"))


# ---------------- F-CONT-1: FX storico nella serie CB ----------------

def test_basis_default_fx_storico_stabile(monkeypatch):
    """Il default e' FX STORICO (use_current_fx=False): il risultato NON cambia
    se l'FX corrente cambia — prima la storia ballava col cambio di oggi."""
    from bellomberg.portfolio.portfolio_analytics import _basis_and_realized_at
    trades = [{"ticker": "ALFA", "action": "BUY", "quantita": 10,
               "prezzo": 100.0, "valuta": "USD", "data": "2026-01-10T10:00:00"}]

    def fx_storico(d_iso, ccy):
        assert ccy == "USD"
        return 0.90  # cambio del giorno del trade

    # FX corrente FINTO molto diverso: se qualcuno lo usasse, il CB cambierebbe
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda c: 5.0)

    cb, realized = _basis_and_realized_at(trades, "2026-02-01", fx_storico)
    assert cb == pytest.approx(10 * 100.0 * 0.90)
    assert realized == 0.0


def test_basis_use_current_fx_esplicito_resta_disponibile(monkeypatch):
    """Il ramo FX corrente resta per i confronti live, ma SOLO esplicito."""
    from bellomberg.portfolio.portfolio_analytics import _basis_and_realized_at
    trades = [{"ticker": "ALFA", "action": "BUY", "quantita": 10,
               "prezzo": 100.0, "valuta": "USD", "data": "2026-01-10T10:00:00"}]
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda c: 0.5)
    cb, _ = _basis_and_realized_at(trades, "2026-02-01",
                                   lambda d, c: 0.90, use_current_fx=True)
    assert cb == pytest.approx(10 * 100.0 * 0.5)


# ---------------- F-CONT-4: staleness dichiarata ----------------

def test_price_stale_dichiarato(db):
    db.add_or_update_position("SIGMA.MI", quantita=100, prezzo_medio=7.5, valuta="EUR")
    db.add_or_update_position("ALFA", quantita=10, prezzo_medio=300.0, valuta="USD")
    db.update_price("ALFA", 350.0, valuta="USD")  # solo ALFA ha lo snapshot

    positions = {p["ticker"]: p for p in db.get_portfolio()}
    assert positions["ALFA"]["price_stale"] is False
    assert positions["ALFA"]["price_source"] == "snapshot"
    assert positions["SIGMA.MI"]["price_stale"] is True
    assert "DICHIARATO" in positions["SIGMA.MI"]["price_source"]
    assert positions["SIGMA.MI"]["pl_eur"] is None  # P&L n.d., non zero finto

    summary = db.get_portfolio_summary()
    assert summary["stale_positions"] == ["SIGMA.MI"]


def test_stale_positions_none_quando_tutto_prezzato(db):
    db.add_or_update_position("ALFA", quantita=10, prezzo_medio=300.0, valuta="USD")
    db.update_price("ALFA", 350.0, valuta="USD")
    assert db.get_portfolio_summary()["stale_positions"] is None


# ---------------- F6: realized persistito ----------------

def _trade_row(db, tid):
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(
            "SELECT * FROM trade_history WHERE id=?", (tid,)).fetchone())


def test_migrazione_5_presente(db):
    with sqlite3.connect(db.db_path) as conn:
        versioni = [r[0] for r in conn.execute(
            "SELECT version FROM schema_version ORDER BY version")]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_history)")}
    assert versioni == [m[0] for m in MIGRATIONS]
    assert {"realized_local", "realized_eur"} <= cols


def test_realized_persistito_su_trim_eur(db):
    db.log_trade("SIGMA.MI", "BUY", 100, 10.0, valuta="EUR", data="2026-07-01T10:00:00")
    tid = db.log_trade("SIGMA.MI", "TRIM", 40, 12.0, valuta="EUR", data="2026-07-10T10:00:00")
    row = _trade_row(db, tid)
    assert row["realized_local"] == pytest.approx(40 * (12.0 - 10.0))
    assert row["realized_eur"] == pytest.approx(80.0)


def test_realized_non_eur_con_fx_e_senza(db, monkeypatch):
    from bellomberg.cli import price_updater
    db.log_trade("ALFA", "BUY", 10, 300.0, valuta="USD", data="2026-07-01T10:00:00")
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda c: 0.9)
    tid = db.log_trade("ALFA", "TRIM", 5, 350.0, valuta="USD", data="2026-07-10T10:00:00")
    row = _trade_row(db, tid)
    assert row["realized_local"] == pytest.approx(5 * 50.0)
    assert row["realized_eur"] == pytest.approx(250.0 * 0.9)
    # FX non disponibile -> realized_eur NULL = n.d. DICHIARATO, mai inventato
    db.log_trade("ALFA", "BUY", 5, 300.0, valuta="USD", data="2026-07-11T10:00:00")
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda c: None)
    tid2 = db.log_trade("ALFA", "TRIM", 2, 400.0, valuta="USD", data="2026-07-12T10:00:00")
    row2 = _trade_row(db, tid2)
    assert row2["realized_local"] is not None
    assert row2["realized_eur"] is None


def test_realized_null_su_buy_e_dividend(db):
    t1 = db.log_trade("SIGMA.MI", "BUY", 100, 10.0, valuta="EUR", data="2026-07-01T10:00:00")
    t2 = db.log_trade("SIGMA.MI", "DIVIDEND", 100, 0.25, valuta="EUR", data="2026-07-05T10:00:00")
    assert _trade_row(db, t1)["realized_local"] is None
    assert _trade_row(db, t2)["realized_local"] is None


# ---------------- prev_close per il P&L daily (chat frontend) ----------------

def test_prev_close_dal_giorno_precedente(db):
    """prev_close = ultimo snapshot del giorno di borsa PRECEDENTE all'ultimo
    giorno con prezzi; senza storia = None dichiarato (mai un GG% finto)."""
    db.add_or_update_position("ALFA", quantita=10, prezzo_medio=300.0, valuta="USD")
    db.add_or_update_position("SIGMA.MI", quantita=100, prezzo_medio=7.5, valuta="EUR")
    with sqlite3.connect(db.db_path) as conn:
        for ts, px in (("2026-07-22 10:00:00", 340.0), ("2026-07-22 17:30:00", 345.0),
                       ("2026-07-23 10:00:00", 350.0), ("2026-07-23 15:00:00", 352.0)):
            conn.execute("INSERT INTO position_prices (ticker, prezzo, valuta, source, timestamp) "
                         "VALUES ('ALFA', ?, 'USD', 'test', ?)", (px, ts))
    positions = {p["ticker"]: p for p in db.get_portfolio()}
    assert positions["ALFA"]["prezzo_live"] == 352.0
    assert positions["ALFA"]["prev_close"] == 345.0        # ULTIMO snapshot di ieri
    assert positions["ALFA"]["prev_close_ts"].startswith("2026-07-22")
    assert positions["SIGMA.MI"]["prev_close"] is None      # niente storia = n.d.


def test_fx_to_eur_esposto_per_posizione(db, monkeypatch):
    """fx_to_eur nel summary: EUR=1.0, non-EUR = cambio usato per la conversione
    (cosi' il P&L daily EUR lato UI usa LO STESSO cambio del NAV, mai un altro)."""
    from bellomberg.cli import price_updater
    db.add_or_update_position("ALFA", quantita=10, prezzo_medio=300.0, valuta="USD")
    db.add_or_update_position("SIGMA.MI", quantita=100, prezzo_medio=7.5, valuta="EUR")
    db.update_price("ALFA", 350.0, valuta="USD")
    db.update_price("SIGMA.MI", 8.0, valuta="EUR")
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda c: 0.88)
    positions = {p["ticker"]: p for p in db.get_portfolio_summary()["positions"]}
    assert positions["SIGMA.MI"]["fx_to_eur"] == 1.0
    assert positions["ALFA"]["fx_to_eur"] == pytest.approx(0.88)


# ---------------- F5: riconciliazione con soglia ----------------

def test_recon_note_breach_e_tolleranza():
    from bellomberg.portfolio.twr_engine import build_recon_note
    snaps = [{"date": "2026-07-22", "nav_total_eur": 100000.0, "created_at": "x"}]
    ok = build_recon_note(100500.0, snaps)          # +0,5% -> dentro
    assert ok["breach"] is False and ok["delta_pct"] == pytest.approx(0.5)
    ko = build_recon_note(103000.0, snaps)          # +3% -> fuori
    assert ko["breach"] is True and ko["tolerance_pct"] == 1.0
    assert build_recon_note(100000.0, []) is None   # senza snapshot: None, mai inventato
    zero = build_recon_note(100000.0, [{"date": "d", "nav_total_eur": 0}])
    assert zero["delta_pct"] is None and zero["breach"] is False


def test_fx_lookup_nearest_forward(monkeypatch):
    """F-CONT-1 bis (caso reale: trade di apertura di DOMENICA 01/02 vs prima
    oss FX lunedi' 02/02): data prima della prima osservazione -> PRIMA oss
    disponibile (deterministico), mai FX corrente zitto; serie assente -> None."""
    import pandas as pd
    from tools.migrations import backfill_realized as bf
    idx = pd.to_datetime(["2026-02-02", "2026-02-03"])
    frame = pd.DataFrame({"USD": [0.95, 0.96]}, index=idx)
    monkeypatch.setattr('bellomberg.portfolio.portfolio_analytics._build_fx_history',
                        lambda c, s, e: frame)
    lookup = bf._fx_lookup_factory(["USD"], "2026-02-01", "2026-02-03")
    assert lookup("2026-02-01", "USD") == pytest.approx(0.95)   # nearest-forward
    assert lookup("2026-02-03", "USD") == pytest.approx(0.96)   # ramo <= normale
    assert lookup("2026-02-01", "CHF") is None                  # assente = n.d.


# ---------------- F6: backfill storico ----------------

def test_backfill_dry_run_e_apply(db, monkeypatch, capsys):
    from tools.migrations import backfill_realized as bf
    # storia sintetica: la TRIM va backfillata (la scrivo raw, senza realized,
    # come i trade pre-migrazione 5)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("INSERT INTO trade_history (ticker, action, quantita, prezzo, valuta, data) "
                     "VALUES ('ALFA','BUY',10,300.0,'USD','2026-06-01T10:00:00')")
        conn.execute("INSERT INTO trade_history (ticker, action, quantita, prezzo, valuta, data) "
                     "VALUES ('ALFA','TRIM',4,350.0,'USD','2026-06-15T10:00:00')")
    monkeypatch.setattr(bf, "DB_PATH", db.db_path)
    monkeypatch.setattr(bf, "BACKUP_DIR", os.path.join(os.path.dirname(db.db_path), "backups"))
    monkeypatch.setattr(bf, "_fx_lookup_factory",
                        lambda ccys, s, e: (lambda d, c: 0.9))  # FX storico finto, offline

    monkeypatch.setattr(sys, "argv", ["backfill_realized.py"])   # dry-run
    bf.main()
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute("SELECT realized_local FROM trade_history "
                           "WHERE action='TRIM'").fetchone()
    assert row[0] is None, "il dry-run NON deve scrivere"

    monkeypatch.setattr(sys, "argv", ["backfill_realized.py", "--apply"])
    bf.main()
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute("SELECT realized_local, realized_eur FROM trade_history "
                           "WHERE action='TRIM'").fetchone()
    assert row[0] == pytest.approx(4 * 50.0)
    assert row[1] == pytest.approx(200.0 * 0.9)
    out = capsys.readouterr().out
    assert "backup" in out.lower()

    # idempotenza: secondo --apply non tocca la riga gia' compilata
    bf.main()
    out2 = capsys.readouterr().out
    assert "0 righe da compilare" in out2 or "non tocco" in out2
