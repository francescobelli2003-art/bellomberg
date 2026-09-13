"""Separate migration is additive, measured and safe on legacy fixtures."""
import sqlite3

import pytest

from tools.migrations import migra_valuation_metadata as migration


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE valuation_theses(id INTEGER PRIMARY KEY,ticker TEXT,fair_value REAL);"
                           "CREATE TABLE decisions(id INTEGER PRIMARY KEY,ticker TEXT,action TEXT);"
                           "INSERT INTO valuation_theses VALUES(1,'SYNTH',99);"
                           "INSERT INTO decisions VALUES(1,'SYNTH','RESEARCH');")
    return path


def test_dry_run_preserves_entire_db_bytes(legacy):
    before = legacy.read_bytes()
    result = migration.migrate(legacy)
    assert result["mode"] == "dry-run" and result["backup"] is None
    assert result["before"]["valuation_theses"]["rows"] == 1
    assert legacy.read_bytes() == before


def test_apply_is_additive_and_idempotent_and_backup_has_original_rows(legacy, monkeypatch):
    monkeypatch.setattr(migration, "backend_alive", lambda: False)
    result = migration.migrate(legacy, apply=True)
    assert result["legacy_rows_unchanged"] and result["before"] == result["after"]
    with sqlite3.connect(result["backup"]) as conn:
        assert conn.execute("SELECT fair_value FROM valuation_theses").fetchone()[0] == 99
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='valuation_snapshots'").fetchone()
    again = migration.migrate(legacy, apply=True)
    assert again["missing_tables"] == [] and again["legacy_rows_unchanged"]
    with sqlite3.connect(legacy) as conn:
        assert conn.execute("SELECT count(*) FROM valuation_snapshots").fetchone()[0] == 0


def test_apply_refuses_running_backend_without_writing(legacy, monkeypatch):
    monkeypatch.setattr(migration, "backend_alive", lambda: True)
    before = legacy.read_bytes()
    with pytest.raises(RuntimeError, match="8765"):
        migration.migrate(legacy, apply=True)
    assert legacy.read_bytes() == before


@pytest.mark.parametrize('apply',[False,True])
def test_incompatible_immutable_trigger_is_refused_before_any_change(legacy,monkeypatch,apply):
    monkeypatch.setattr(migration,'backend_alive',lambda:False)
    with sqlite3.connect(legacy) as conn:
        for statement in migration.VALUATION_SNAPSHOT_STATEMENTS:conn.execute(statement)
        conn.execute('DROP TRIGGER valuation_snapshots_update_immutable')
        conn.execute('CREATE TRIGGER valuation_snapshots_update_immutable BEFORE UPDATE ON valuation_snapshots BEGIN SELECT 1; END')
    before=legacy.read_bytes()
    with pytest.raises(ValueError,match='schema'):
        migration.migrate(legacy,apply=apply)
    assert legacy.read_bytes()==before


def test_metadata_orphans_are_refused(legacy,monkeypatch):
    monkeypatch.setattr(migration,'backend_alive',lambda:False)
    with sqlite3.connect(legacy) as conn:
        for statement in migration.VALUATION_SNAPSHOT_STATEMENTS:conn.execute(statement)
        conn.execute("INSERT INTO valuation_snapshot_links(snapshot_id,generation_id,thesis_id,created_at) VALUES('absent','absent',999,'synthetic')")
    before=legacy.read_bytes()
    with pytest.raises(ValueError,match='foreign'):
        migration.migrate(legacy,apply=True)
    assert legacy.read_bytes()==before


def test_full_snapshot_drift_in_wal_blocks_before_migration(legacy,monkeypatch):
    with sqlite3.connect(legacy) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE positions(id INTEGER PRIMARY KEY, amount INTEGER, proof BLOB)')
        conn.execute("INSERT INTO positions VALUES(1,17,x'010203')")
    def concurrent_writer():
        with sqlite3.connect(legacy) as conn:conn.execute('UPDATE positions SET amount=18')
        return False
    monkeypatch.setattr(migration,'backend_alive',concurrent_writer)
    with pytest.raises(ValueError,match='changed|variat|drift'):
        migration.migrate(legacy,apply=True)
    with sqlite3.connect(legacy) as conn:
        assert conn.execute('SELECT amount,proof FROM positions').fetchone()==(18,b'\x01\x02\x03')
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='valuation_snapshots'").fetchone()


def test_dry_run_rehearses_real_ddl_and_measures_read_only_source(legacy):
    result=migration.migrate(legacy)
    assert result['source_statements']['observed']>0
    assert result['source_statements']['writes']==0
    assert result['rehearsal']['statements']['writes']>0
    assert result['rehearsal']['row_changes']==0
    assert result['rehearsal']['preexisting_tables_unchanged']


def test_mutation_of_unrelated_table_rolls_back_and_is_never_claimed_unchanged(legacy,monkeypatch):
    monkeypatch.setattr(migration,'backend_alive',lambda:False)
    with sqlite3.connect(legacy) as conn:
        conn.execute('CREATE TABLE positions(id INTEGER PRIMARY KEY,amount INTEGER)')
        conn.execute('INSERT INTO positions VALUES(1,17)')
    before=legacy.read_bytes();install=migration._install
    def faulty(conn):
        install(conn);conn.execute('UPDATE positions SET amount=18')
    monkeypatch.setattr(migration,'_install',faulty)
    with pytest.raises(ValueError,match='preesistenti variati'):
        migration.migrate(legacy,apply=True)
    assert legacy.read_bytes()==before


def test_failure_during_actual_apply_rolls_back_all_tables_and_schema(legacy,monkeypatch):
    from bellomberg.storage.sqlite_checks import fingerprint,schema_fingerprint
    monkeypatch.setattr(migration,'backend_alive',lambda:False)
    with sqlite3.connect(legacy) as conn:before=(fingerprint(conn),schema_fingerprint(conn))
    install=migration._install;calls=[]
    def fail_actual(conn):
        install(conn);calls.append(True)
        if len(calls)==2:
            conn.execute('UPDATE valuation_theses SET fair_value=123')
            raise RuntimeError('synthetic fault after DDL and DML')
    monkeypatch.setattr(migration,'_install',fail_actual)
    with pytest.raises(RuntimeError,match='synthetic fault'):
        migration.migrate(legacy,apply=True)
    assert len(calls)==2
    with sqlite3.connect(legacy) as conn:assert (fingerprint(conn),schema_fingerprint(conn))==before


def test_unknown_port_state_is_not_treated_as_a_free_port(monkeypatch):
    def timeout(*a,**k):raise TimeoutError('synthetic unknown probe')
    monkeypatch.setattr(migration.socket,'create_connection',timeout)
    with pytest.raises(RuntimeError,match='non verificabile'):migration.backend_alive()


def test_delayed_windows_refusal_is_observed_for_both_address_families(monkeypatch):
    observed=[]
    def delayed_refusal(address, *, timeout):
        if timeout < 3:raise TimeoutError('Windows refusal has not arrived yet')
        observed.append(address)
        raise ConnectionRefusedError(10061,'Windows closed port')
    monkeypatch.setattr(migration.socket,'create_connection',delayed_refusal)
    assert migration.backend_alive() is False
    assert observed==[('127.0.0.1',8765),('::1',8765)]


def test_listening_backend_is_still_detected(monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(migration.socket,'create_connection',lambda *a,**k:nullcontext())
    assert migration.backend_alive() is True
