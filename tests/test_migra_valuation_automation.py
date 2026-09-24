"""Migration rehearsal and apply use disposable SQLite databases only."""
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from bellomberg.storage.memory_db import VALUATION_SNAPSHOT_STATEMENTS
from bellomberg.storage.method_records_store import crea_tabelle
from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
from tools.migrations import migra_valuation_automation as migration


@pytest.fixture
def existing_db(tmp_path):
    path = tmp_path / "existing.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""CREATE TABLE valuation_theses(
            id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, date TEXT NOT NULL,
            price_at_thesis REAL, fair_value REAL, growth_path TEXT,
            ebitda_margin_target REAL, terminal_growth REAL, variant_view TEXT,
            engine TEXT, subsector TEXT, memo_id INTEGER)""")
        conn.execute("CREATE TABLE decisions(id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE positions(id INTEGER PRIMARY KEY, amount INTEGER)")
        for statement in VALUATION_SNAPSHOT_STATEMENTS:
            conn.execute(statement)
        crea_tabelle(conn)
        conn.execute("INSERT INTO valuation_theses(id,ticker,date) VALUES(1,'SYNTH-A','2026-09-23')")
        conn.execute("INSERT INTO positions VALUES(1,17)")
        conn.execute("INSERT INTO valuation_snapshots VALUES(?,?,?,?,?,?,?)",
                     ("snapshot-synthetic", "generation-synthetic", "SYNTH-A", 1, "{}", "a" * 64,
                      "2026-09-23T00:00:00+00:00"))
        conn.execute("INSERT INTO valuation_snapshot_links(snapshot_id,generation_id,thesis_id,created_at) "
                     "VALUES(?,?,?,?)", ("snapshot-synthetic", "generation-synthetic", 1,
                                      "2026-09-23T00:00:00+00:00"))
        conn.commit()
    return path


def _state(path):
    with closing(sqlite3.connect(path)) as conn:
        return fingerprint(conn), schema_fingerprint(conn)


def test_dry_run_rehearses_on_copy_and_measures_no_source_writes(existing_db):
    before = _state(existing_db)
    result = migration.migra(existing_db)
    assert result["modo"] == "dry-run" and result["backup"] is None
    assert result["sorgente_invariata"] is True
    assert result["scritture_sorgente"]["scritture"] == 0
    assert result["scritture_sorgente"]["total_changes"] == 0
    assert result["prova"]["schema_completo"] is True
    assert result["prova"]["tabelle_preesistenti_invariate"] is True
    assert result["prova"]["scritture"]["scritture"] > 0
    assert result["prima"]["positions"]["rows"] == 1
    assert _state(existing_db) == before
    assert "valuation_jobs" not in before[0]


def test_apply_is_additive_idempotent_and_keeps_verified_backup(existing_db, monkeypatch):
    monkeypatch.setattr(migration, "backend_alive", lambda: False)
    before = _state(existing_db)
    first = migration.migra(existing_db, apply=True)
    assert first["modo"] == "apply" and first["rilettura"]["schema_completo"]
    assert first["applicazione"]["tabelle_preesistenti_invariate"]
    assert _state(first["backup"]) == before
    after_data, after_schema = _state(existing_db)
    assert {name: after_data[name] for name in before[0]} == before[0]
    assert {name: after_schema[name] for name in before[1]} == before[1]
    assert {"valuation_jobs", "valuation_job_events", "valuation_model_heads",
            "valuation_publications", "valuation_variants"} <= set(after_data)
    second = migration.migra(existing_db, apply=True)
    assert second["tabelle_mancanti"] == []
    assert second["applicazione"]["tabelle_preesistenti_invariate"]
    assert _state(existing_db) == (after_data, after_schema)


def test_busy_port_refuses_apply_before_backup_or_schema_change(existing_db, monkeypatch):
    monkeypatch.setattr(migration, "backend_alive", lambda: True)
    before = _state(existing_db)
    with pytest.raises(RuntimeError, match="8765"):
        migration.migra(existing_db, apply=True)
    assert _state(existing_db) == before
    assert not list(existing_db.parent.glob("*.bak"))


@pytest.mark.parametrize("damage", ["new_target", "required_archive", "thesis_column"])
def test_incompatible_schema_is_refused_without_apply(existing_db, monkeypatch, damage):
    monkeypatch.setattr(migration, "backend_alive", lambda: False)
    with closing(sqlite3.connect(existing_db)) as conn:
        if damage == "new_target":
            conn.execute("CREATE TABLE valuation_jobs(id INTEGER PRIMARY KEY, incorrect TEXT)")
        elif damage == "required_archive":
            conn.execute("DROP TRIGGER method_record_sets_update_immutable")
            conn.execute("CREATE TRIGGER method_record_sets_update_immutable BEFORE UPDATE ON method_record_sets "
                         "BEGIN SELECT 1; END")
        else:
            conn.execute("ALTER TABLE valuation_theses DROP COLUMN fair_value")
        conn.commit()
    before = _state(existing_db)
    with pytest.raises(ValueError, match="incompatib|schema"):
        migration.migra(existing_db, apply=True)
    assert _state(existing_db) == before
    assert not list(existing_db.parent.glob("*.bak"))


def test_preflight_drift_aborts_apply_without_new_schema(existing_db, monkeypatch):
    calls = 0

    def probe_and_change():
        nonlocal calls
        calls += 1
        if calls == 2:
            with closing(sqlite3.connect(existing_db)) as conn:
                conn.execute("UPDATE positions SET amount=18 WHERE id=1")
                conn.commit()
        return False

    monkeypatch.setattr(migration, "backend_alive", probe_and_change)
    with pytest.raises(ValueError, match="variat|cambiat|drift"):
        migration.migra(existing_db, apply=True)
    with closing(sqlite3.connect(existing_db)) as conn:
        assert conn.execute("SELECT amount FROM positions").fetchone()[0] == 18
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='valuation_jobs'").fetchone() is None
    assert calls == 2


def test_failure_after_ddl_rolls_back_source_schema_and_rows(existing_db, monkeypatch):
    monkeypatch.setattr(migration, "backend_alive", lambda: False)
    before = _state(existing_db)
    original_install = migration._install
    installed_paths = []
    failed_path = None

    def fail_only_at_apply(conn):
        nonlocal failed_path
        path = Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve()
        installed_paths.append(path)
        original_install(conn)
        if path == existing_db.resolve():
            failed_path = path
            conn.execute("UPDATE positions SET amount=99 WHERE id=1")
            raise RuntimeError("synthetic failure after DDL")

    monkeypatch.setattr(migration, "_install", fail_only_at_apply)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        migration.migra(existing_db, apply=True)
    assert len(installed_paths) == 2
    assert installed_paths[0] != existing_db.resolve()  # rehearsal copy completed first
    assert installed_paths[1] == failed_path == existing_db.resolve()
    assert _state(existing_db) == before


def test_missing_file_or_prerequisite_is_not_created(tmp_path, existing_db):
    absent = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError):
        migration.migra(absent)
    assert not absent.exists()
    with closing(sqlite3.connect(existing_db)) as conn:
        conn.execute("DROP TABLE method_record_reviews")
        conn.commit()
    with pytest.raises(ValueError, match="richiest|incompatib|schema"):
        migration.migra(existing_db)
