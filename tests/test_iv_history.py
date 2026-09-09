"""IV History + IV Rank (25/07) — offline: DB temporaneo con SCHEMA_SQL vero,
vol_surface moccata, percentili CALCOLATI A MANO. Convenzioni della voce:
weekend skip dichiarato, idempotenza per giorno, storia giovane dichiarata,
errori provider = nessuna riga inventata.
"""
import sqlite3

import pytest

import bellomberg.market_data.iv_history as ivh
from bellomberg.market_data.iv_history import get_iv_context, save_daily_snapshot


FRI, SAT = "2026-07-24", "2026-07-25"


@pytest.fixture()
def db(tmp_path):
    """DB temporaneo con lo schema VERO di casa (stessa CREATE della migrazione 6)."""
    from bellomberg.storage.memory_db import SCHEMA_SQL
    path = str(tmp_path / "test_iv.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.close()
    return path


def _fake_vs(t, max_expiries=6, max_days=120):
    return {"ticker": t, "spot_est": 100.0, "spot_source": "test",
            "term_structure": [
                {"expiry": "2026-07-31", "days": 7, "atm_iv": 0.22,
                 "rr25": -0.03, "bf25": 0.01},
                {"expiry": "2026-08-21", "days": 28, "atm_iv": 0.24,
                 "rr25": -0.02, "bf25": 0.01},
                {"expiry": "2026-09-18", "days": 56, "atm_iv": 0.25,
                 "rr25": None, "bf25": None},
            ]}


def _rows(db, t="SPY"):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT snap_date, expiry, days, atm_iv, rr25 FROM iv_history "
            "WHERE ticker=? ORDER BY snap_date, days", (t,)).fetchall()
    finally:
        conn.close()


def test_snapshot_salva_una_riga_per_expiry(db, monkeypatch):
    monkeypatch.setattr('bellomberg.portfolio.vol_surface.build_vol_surface', _fake_vs)
    out = save_daily_snapshot(db_path=db, tickers=("SPY",), snap_date=FRI)
    assert out["saved"] == {"SPY": 3}
    rows = _rows(db)
    assert len(rows) == 3
    assert rows[0] == (FRI, "2026-07-31", 7, 0.22, -0.03)
    assert rows[2][3] == 0.25 and rows[2][4] is None   # rr25 assente = NULL, non 0


def test_idempotenza_stesso_giorno_non_riscrive(db, monkeypatch):
    calls = {"n": 0}

    def counting_vs(t, **kw):
        calls["n"] += 1
        return _fake_vs(t)

    monkeypatch.setattr('bellomberg.portfolio.vol_surface.build_vol_surface', counting_vs)
    save_daily_snapshot(db_path=db, tickers=("SPY",), snap_date=FRI)
    out2 = save_daily_snapshot(db_path=db, tickers=("SPY",), snap_date=FRI)
    assert "SPY" in out2["skipped"]                    # guard PRIMA del fetch
    assert calls["n"] == 1                             # niente chiamata Polygon sprecata
    assert len(_rows(db)) == 3


def test_weekend_skip_dichiarato_e_force_per_collaudo(db, monkeypatch):
    monkeypatch.setattr('bellomberg.portfolio.vol_surface.build_vol_surface', _fake_vs)
    out = save_daily_snapshot(db_path=db, tickers=("SPY",), snap_date=SAT)
    assert "_weekend" in out["skipped"] and len(_rows(db)) == 0
    out_f = save_daily_snapshot(db_path=db, tickers=("SPY",), snap_date=SAT,
                                force=True)
    assert out_f["saved"] == {"SPY": 3}                # force = collaudo/backfill


def test_errore_provider_dichiarato_nessuna_riga(db, monkeypatch):
    monkeypatch.setattr('bellomberg.portfolio.vol_surface.build_vol_surface',
                        lambda t, **kw: {"error": "POLYGON_API_KEY mancante"})
    out = save_daily_snapshot(db_path=db, tickers=("SPY",), snap_date=FRI)
    assert "POLYGON" in out["errors"]["SPY"]
    assert len(_rows(db)) == 0                         # mai una riga inventata


def test_pre_open_rinviata_dichiarata(db, monkeypatch):
    """Review A1: prima delle 16 locali le chain sono la chiusura di IERI —
    la foto del giorno corrente viene RINVIATA, dichiarato, zero righe."""
    class _FakeDate:
        @staticmethod
        def today():
            import datetime as _dt
            return _dt.date(2026, 7, 24)               # venerdi' (feriale)

    monkeypatch.setattr(ivh, "date", _FakeDate)
    monkeypatch.setattr('bellomberg.portfolio.vol_surface.build_vol_surface', _fake_vs)
    monkeypatch.setattr(ivh, "_now_hour", lambda: 9)   # run notturna/mattutina
    out = save_daily_snapshot(db_path=db, tickers=("SPY",))
    assert "_pre_open" in out["skipped"] and len(_rows(db)) == 0
    monkeypatch.setattr(ivh, "_now_hour", lambda: 16)  # post-apertura US
    out2 = save_daily_snapshot(db_path=db, tickers=("SPY",))
    assert out2["saved"] == {"SPY": 3}
    assert _rows(db)[0][0] == FRI                      # etichetta = giorno VERO


def test_force_su_giorno_gia_presente_e_skip_non_errore(db, monkeypatch):
    """Review M2: rilancio force dello stesso seed = OR IGNORE a 0 righe ->
    skip dichiarato 'righe gia' presenti', MAI un finto errore provider."""
    monkeypatch.setattr('bellomberg.portfolio.vol_surface.build_vol_surface', _fake_vs)
    save_daily_snapshot(db_path=db, tickers=("SPY",), snap_date=FRI, force=True)
    out2 = save_daily_snapshot(db_path=db, tickers=("SPY",), snap_date=FRI,
                               force=True)
    assert out2["errors"] == {}
    assert "gia' presenti" in out2["skipped"]["SPY"]
    assert len(_rows(db)) == 3


def test_atm_iv_null_in_mezzo_salta_solo_quella(db, monkeypatch):
    def vs_con_buco(t, **kw):
        p = _fake_vs(t)
        p["term_structure"][1]["atm_iv"] = None        # buco del provider
        return p

    monkeypatch.setattr('bellomberg.portfolio.vol_surface.build_vol_surface', vs_con_buco)
    out = save_daily_snapshot(db_path=db, tickers=("SPY",), snap_date=FRI)
    assert out["saved"] == {"SPY": 2}                  # le 2 valide, non errore


def _seed(db, t, day, entries):
    conn = sqlite3.connect(db)
    for expiry, days, iv in entries:
        conn.execute("INSERT INTO iv_history (ticker, snap_date, expiry, days, "
                     "atm_iv, source) VALUES (?,?,?,?,?, 'test')",
                     (t, day, expiry, days, iv))
    conn.commit()
    conn.close()


def test_percentile_a_mano_min_max_e_giovinezza(db):
    """Storico front [0,20 · 0,25 · 0,35 · 0,30]: corrente 0,30 ->
    percentile 3/4 = 75%, min 0,20, max 0,35, n_obs 4, young True."""
    for day, iv in [("2026-07-01", 0.20), ("2026-07-02", 0.25),
                    ("2026-07-03", 0.35), ("2026-07-06", 0.30)]:
        _seed(db, "SPY", day, [("2026-08-21", 30, iv)])
    ctx = get_iv_context("SPY", db_path=db)
    assert ctx["error"] is None
    assert ctx["iv_front_current"] == pytest.approx(0.30)
    assert ctx["iv_percentile"] == pytest.approx(75.0)
    assert ctx["iv_min"] == pytest.approx(0.20)
    assert ctx["iv_max"] == pytest.approx(0.35)
    assert ctx["n_obs"] == 4
    assert ctx["young"] is True                        # 4 < 60: dichiarato
    assert ctx["history_from"] == "2026-07-01"


def test_front_scarta_0dte_ma_fa_fallback_se_solo_0dte(db):
    _seed(db, "SPY", "2026-07-01", [("2026-07-01", 0, 0.10),
                                    ("2026-07-08", 7, 0.20)])
    _seed(db, "SPY", "2026-07-02", [("2026-07-02", 0, 0.50),
                                    ("2026-07-08", 6, 0.40)])
    ctx = get_iv_context("SPY", db_path=db)
    # front per giorno = expiry >= 2g: [0,20 · 0,40], NON gli 0DTE
    assert ctx["iv_front_current"] == pytest.approx(0.40)
    assert ctx["iv_percentile"] == pytest.approx(100.0)
    _seed(db, "SPY", "2026-07-03", [("2026-07-03", 0, 0.05)])
    ctx2 = get_iv_context("SPY", db_path=db)
    assert ctx2["iv_front_current"] == pytest.approx(0.05)  # solo 0DTE: fallback


def test_storia_insufficiente_dichiarata(db):
    ctx0 = get_iv_context("SPY", db_path=db)
    assert "insufficiente" in ctx0["error"] and ctx0["n_obs"] == 0
    _seed(db, "SPY", "2026-07-01", [("2026-08-21", 30, 0.25)])
    ctx1 = get_iv_context("SPY", db_path=db)
    assert "insufficiente" in ctx1["error"] and ctx1["n_obs"] == 1


def test_tabella_assente_errore_dichiarato(tmp_path):
    path = str(tmp_path / "vuoto.db")
    sqlite3.connect(path).close()                      # DB senza schema
    ctx = get_iv_context("SPY", db_path=path)
    assert "iv_history non disponibile" in ctx["error"]


def test_migrazione_6_su_db_esistente_crea_la_tabella(tmp_path):
    """Il DB vivo prende la tabella dalla MIGRAZIONE (non da SCHEMA_SQL):
    stessi statement, verificati applicabili su un DB gia' migrato a 5."""
    from bellomberg.storage.memory_db import MIGRATIONS
    mig6 = [m for m in MIGRATIONS if m[0] == 6]
    assert len(mig6) == 1
    path = str(tmp_path / "vecchio.db")
    conn = sqlite3.connect(path)
    for stmt in mig6[0][2]:
        conn.execute(stmt)
        conn.execute(stmt)                             # idempotente per regola
    conn.execute("INSERT INTO iv_history (ticker, snap_date, expiry, days, "
                 "atm_iv, source) VALUES ('SPY','2026-07-24','e',7,0.2,'t')")
    conn.commit()
    conn.close()
