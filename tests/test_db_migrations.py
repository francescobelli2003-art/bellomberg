"""Migrazioni versionate (§9-bis n.6, collaudo 9/9 del 21/07 su copia del DB):
qui su DB TEMPORANEO creato da zero — init = schema + migrazione 1 + indici,
idempotenza, EXPLAIN usa l'indice. Chroma stubbato: zero dipendenze pesanti.
"""
import sqlite3

import pytest

from bellomberg.storage.memory_db import MIGRATIONS, MemoryDB

INDICI_MIGRAZIONE_1 = {"idx_trade_ticker_data", "idx_pm_feedback_decision",
                       "idx_pm_feedback_memo"}


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    return str(tmp_path / "data" / "consigliere_test.db")


def _chroma_dir(path):
    return path.replace("consigliere_test.db", "chroma")


def _indici(path):
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}


def test_init_db_vuoto_schema_e_migrazione_1(db_path):
    MemoryDB(db_path=db_path, chroma_path=_chroma_dir(db_path))
    with sqlite3.connect(db_path) as conn:
        tabelle = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"positions", "trade_history", "decisions", "pm_feedback",
                "valuation_theses", "schema_version"} <= tabelle
        versioni = [r[0] for r in conn.execute(
            "SELECT version FROM schema_version ORDER BY version")]
    assert versioni == [m[0] for m in MIGRATIONS]
    assert INDICI_MIGRAZIONE_1 <= _indici(db_path)


def test_migrazioni_idempotenti(db_path):
    MemoryDB(db_path=db_path, chroma_path=_chroma_dir(db_path))
    MemoryDB(db_path=db_path, chroma_path=_chroma_dir(db_path))  # 2° init: no-op
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert n == len(MIGRATIONS), "migrazione registrata due volte"
    assert INDICI_MIGRAZIONE_1 <= _indici(db_path)


def test_explain_usa_indice_trade_history(db_path):
    db = MemoryDB(db_path=db_path, chroma_path=_chroma_dir(db_path))
    db.log_trade("GAMMA.MI", "BUY", 100, 7.5, data="2026-07-01T10:00:00")
    db.log_trade("ALFA", "BUY", 10, 350.0, data="2026-07-02T10:00:00")
    with sqlite3.connect(db_path) as conn:
        plan = " ".join(str(r) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM trade_history "
            "WHERE ticker=? ORDER BY data", ("GAMMA.MI",)).fetchall())
    assert "idx_trade_ticker_data" in plan


# ============================================================
# Riallineamento 23/07 (audit/20) — Lotto A: migrazione 4 + FK
# ============================================================

def _colonne(path, tabella):
    with sqlite3.connect(path) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({tabella})")}


def test_db_fresco_ha_schema_completo_f04(db_path):
    """Un DB ricreato da zero NON rompe piu' il feed (F-04): colonne i18n
    nella CREATE, tabelle contabili e favorites nel canonico, indice alert."""
    MemoryDB(db_path=db_path, chroma_path=_chroma_dir(db_path))
    with sqlite3.connect(db_path) as conn:
        tabelle = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"favorite_companies", "cash_movements", "nav_snapshots"} <= tabelle
    assert {"headline_it", "why_matters"} <= _colonne(db_path, "news_feed")
    assert "idx_news_alert" in _indici(db_path)
    # il repro storico del drift: l'INSERT del feed con le colonne i18n
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO news_feed (title, url, headline_it, why_matters) "
            "VALUES (?,?,?,?)", ("t", "http://x", "titolo", "conta perche'"))


def test_db_vecchio_senza_i18n_viene_sanato(db_path):
    """DB pre-migrate_news_v3: news_feed esiste SENZA headline_it/why_matters.
    L'init deve aggiungerle via _migrate_news_i18n (guard su table_info) e
    l'INSERT del feed deve funzionare DOPO la sanatoria."""
    import os
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE news_feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            snippet TEXT, source TEXT, url TEXT UNIQUE, published_at TEXT,
            pulled_at TEXT, ticker_mentioned TEXT, theme TEXT, provider TEXT,
            sentiment TEXT, sentiment_score REAL, relevance INTEGER,
            notified INTEGER DEFAULT 0)""")
    MemoryDB(db_path=db_path, chroma_path=_chroma_dir(db_path))
    assert {"headline_it", "why_matters"} <= _colonne(db_path, "news_feed")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO news_feed (title, url, headline_it, why_matters) "
                     "VALUES (?,?,?,?)", ("t", "http://y", "titolo", "conta"))


def test_connect_sqlite_applica_foreign_keys(db_path):
    """Riallineamento: i writer raw via connect_sqlite ora hanno le FK attive
    (prima solo MemoryDB._conn le accendeva) — e le FK DEVONO mordere
    (IntegrityError su figlio orfano), non solo risultare accese."""
    from bellomberg.storage.memory_db import connect_sqlite
    MemoryDB(db_path=db_path, chroma_path=_chroma_dir(db_path))
    cx = connect_sqlite(db_path)
    try:
        assert cx.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            cx.execute("INSERT INTO chat_messages (session_id, role, content) "
                       "VALUES (99999, 'user', 'orfano')")
    finally:
        cx.close()


def test_recover_db_reinserisce_per_nome(db_path, monkeypatch):
    """Review Lotto A (F1): il DB vivo ha favorite_companies con `note` DOPO
    `added_at` (ALTER storico), il canonico la ha PRIMA. Il recovery posizionale
    scambiava le note del PM coi timestamp IN SILENZIO: ora reinserisce per NOME."""
    from bellomberg.cli import recover_db
    MemoryDB(db_path=db_path, chroma_path=_chroma_dir(db_path))
    recovered = {"favorite_companies": {
        # ordine colonne del DB VIVO (added_at prima di note)
        "cols": ["ticker", "name", "sector", "industry", "added_at", "note"],
        "rows": [("IOTA.L", "Iota Fund", "Financial", "CEF",
                  "2026-01-01T00:00:00", "nota vera del PM")],
    }}
    monkeypatch.setattr(recover_db, "DB_PATH", db_path)
    recover_db.step_5_reinsert(recovered)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT note, added_at FROM favorite_companies "
                           "WHERE ticker='IOTA.L'").fetchone()
    assert row is not None
    assert row[0] == "nota vera del PM", "la nota del PM e' finita nella colonna sbagliata"
    assert row[1] == "2026-01-01T00:00:00"


def test_migrazione_4_su_db_con_tabelle_twr_preesistenti(db_path):
    """DB dove setup_twr_tables.py era GIA' stato lanciato: la migrazione 4
    (CREATE IF NOT EXISTS) non deve fallire ne' toccare i dati."""
    import os
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE cash_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('DEPOSIT','WITHDRAWAL')),
            amount_eur REAL NOT NULL CHECK(amount_eur > 0), note TEXT,
            created_at TEXT DEFAULT (datetime('now')))""")
        conn.execute("INSERT INTO cash_movements (date, type, amount_eur) "
                     "VALUES ('2026-01-02','DEPOSIT',1000.0)")
    MemoryDB(db_path=db_path, chroma_path=_chroma_dir(db_path))
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM cash_movements").fetchone()[0]
        versioni = [r[0] for r in conn.execute(
            "SELECT version FROM schema_version ORDER BY version")]
    assert n == 1, "la migrazione ha toccato i dati preesistenti"
    assert versioni == [m[0] for m in MIGRATIONS]
