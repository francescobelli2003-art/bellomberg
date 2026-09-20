"""Migrazione additiva misurata e worker isolato, senza rete ne' DB reale."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools.migrations import migra_filing as migration
from bellomberg.market_data import filing_worker
from bellomberg.storage.filing_store import FilingStore
from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / 'archive.db'
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE decisions (id INTEGER, note TEXT)')
        conn.execute('CREATE TABLE valuation_theses (id INTEGER, payload TEXT)')
        conn.execute("INSERT INTO decisions VALUES(1,'preserve')")
        conn.execute("INSERT INTO valuation_theses VALUES(2,'original')")
    return path


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return fingerprint(conn), schema_fingerprint(conn)


def test_migration_dry_run_then_apply_is_additive_and_idempotent(archive, monkeypatch):
    monkeypatch.setattr(migration, 'backend_alive', lambda: False)
    before = snapshot(archive)
    dry = migration.migra(archive)
    assert snapshot(archive) == before
    assert dry['scritture_sorgente_preflight'] == dry['righe_sorgente_preflight'] == 0
    assert dry['prova']['tabelle_preesistenti_invariate']
    with pytest.raises(RuntimeError, match='schema'):
        FilingStore(archive)
    applied = migration.migra(archive, apply=True)
    assert Path(applied['backup']).is_file()
    assert snapshot(applied['backup']) == before
    assert applied['rilettura']['schema_completo']
    after = snapshot(archive)
    for old, new in zip(before, after):
        assert all(new[k] == v for k, v in old.items())
    FilingStore(archive)
    migration.migra(archive, apply=True)
    assert snapshot(archive) == after


def test_migration_refuses_live_backend_and_schema_conflicts(archive, monkeypatch):
    before = snapshot(archive)
    monkeypatch.setattr(migration, 'backend_alive', lambda: True)
    with pytest.raises(RuntimeError, match='8765'):
        migration.migra(archive, apply=True)
    assert snapshot(archive) == before
    with sqlite3.connect(archive) as conn:
        conn.execute('CREATE TABLE filing_runs (id TEXT)')
    damaged = snapshot(archive)
    with pytest.raises(ValueError, match='incompatibile'):
        migration.migra(archive)
    assert snapshot(archive) == damaged


def test_migration_missing_file_never_creates_one(tmp_path):
    missing = tmp_path / 'absent.db'
    with pytest.raises(FileNotFoundError):
        migration.migra(missing)
    assert not missing.exists()


def test_worker_reports_partial_failure():
    service = Mock()
    service.run_due.return_value = [{'id': 1, 'status': 'parziale', 'reason': 'source missing'}]
    result = filing_worker.execute_due(service)
    assert result['status'] == 'attention'
    assert result['runs'][0]['reason'] == 'source missing'
    service.run_due.assert_called_once_with()


def test_worker_status_readonly_and_empty_schedule_no_network(archive, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(migration, 'backend_alive', lambda: False)
    migration.migra(archive, apply=True)
    before = snapshot(archive)
    log = tmp_path / 'worker.jsonl'
    args = ['--db', str(archive), '--archive', str(tmp_path / 'documents'), '--log', str(log)]
    assert filing_worker.main(args + ['--status']) == 0
    assert json.loads(capsys.readouterr().out)['profiles'] == []
    assert not log.exists()
    assert snapshot(archive) == before
    assert filing_worker.main(args + ['--due']) == 0
    assert json.loads(log.read_text(encoding='utf-8'))['runs'] == []
    assert snapshot(archive) == before


def test_worker_missing_schema_logs_error_without_creation(tmp_path, capsys):
    db, log = tmp_path / 'absent.db', tmp_path / 'errors.jsonl'
    assert filing_worker.main(['--db', str(db), '--log', str(log)]) == 1
    assert not db.exists()
    assert json.loads(log.read_text(encoding='utf-8'))['status'] == 'errore'
