"""S2 append-only snapshots and references, exclusively on temporary SQLite."""
import json
import sqlite3
from copy import deepcopy

import pytest

from bellomberg.storage import memory_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    return memory_db.MemoryDB(str(tmp_path / "test.db"), str(tmp_path / "chroma"))


def payload():
    return {"ticker": "SYNTH.X", "snapshot_id": "a" * 64,
            "generation_id": "c1a47890-8b24-4ec8-86ae-d29ecdc23062",
            "valuation_decision": {"decision_status": "unknown", "method_id": None,
                                   "requirements_status": "incomplete"},
            "fair_value_base": 991.0, "sanity": {"severity": "BLOCK"},
            "acquisition_tasks": [{"field": "business_model", "status": "missing"}]}


def test_incomplete_snapshot_roundtrip_preserves_raw_but_returns_no_fair_value(db):
    source = payload()
    before = deepcopy(source)
    ref = db.save_valuation_snapshot("SYNTH.X", source)
    assert source == before
    saved = db.get_valuation_snapshot(ref, generation_id=source["generation_id"])
    assert saved["valuation_decision"] == source["valuation_decision"]
    assert saved["acquisition_tasks"] == source["acquisition_tasks"]
    assert saved["fair_value_base"] is None
    assert saved["valuation_usability"]["usable"] is False
    with db._conn() as conn:
        raw = json.loads(conn.execute("SELECT payload_json FROM valuation_snapshots").fetchone()[0])
    assert raw == source


def test_snapshot_is_immutable_idempotent_and_generation_specific(db):
    source = payload()
    ref = db.save_valuation_snapshot("SYNTH.X", source)
    assert db.save_valuation_snapshot("SYNTH.X", source) == ref
    altered = dict(source, fair_value_base=1001)
    with pytest.raises(ValueError, match="immut"):
        db.save_valuation_snapshot("SYNTH.X", altered)
    with db._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE valuation_snapshots SET payload_json='{}'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM valuation_snapshots")
    newer = dict(source, generation_id="8011949f-a0e4-44f4-b883-c2ec59b16113")
    db.save_valuation_snapshot("SYNTH.X", newer)
    with pytest.raises(ValueError, match="generation_id"):
        db.get_valuation_snapshot(ref)


def test_snapshot_links_thesis_and_decision_without_editing_legacy(db):
    legacy_id = db.save_valuation_thesis("SYNTH.X", fair_value=321, sanity_severity="OK")
    with db._conn() as conn:
        legacy = tuple(conn.execute("SELECT * FROM valuation_theses WHERE id=?", (legacy_id,)).fetchone())
        decision_id = conn.execute("INSERT INTO decisions(timestamp,action,ticker) VALUES ('2026-09-10','RESEARCH','SYNTH.X')").lastrowid
    source = payload()
    thesis_id = db.save_valuation_thesis("SYNTH.X", valuation_payload=source, fair_value=991,
                                        sanity_severity="BLOCK")
    assert thesis_id is not None
    db.link_valuation_snapshot(source["snapshot_id"], generation_id=source["generation_id"],
                               decision_id=decision_id)
    history = db.get_valuation_history("SYNTH.X")
    assert history[0]["valuation_snapshot_id"] == source["snapshot_id"]
    assert history[0]["fair_value"] is None
    assert history[1]["valuation_usability"]["usable"] is False
    with db._conn() as conn:
        assert tuple(conn.execute("SELECT * FROM valuation_theses WHERE id=?", (legacy_id,)).fetchone()) == legacy
        links = conn.execute("SELECT thesis_id,decision_id FROM valuation_snapshot_links").fetchall()
    assert {(r[0], r[1]) for r in links} == {(thesis_id, None), (None, decision_id)}


def test_reference_cannot_cross_tickers(db):
    source = payload()
    db.save_valuation_snapshot("SYNTH.X", source)
    wrong = db.save_valuation_thesis("OTHER", fair_value=1)
    with pytest.raises(ValueError, match="ticker"):
        db.link_valuation_snapshot(source["snapshot_id"], generation_id=source["generation_id"], thesis_id=wrong)


def test_failed_thesis_rolls_back_snapshot_and_thesis_atomically(db, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("synthetic link failure")
    monkeypatch.setattr(db, "_link_valuation_snapshot", fail)
    assert db.save_valuation_thesis("SYNTH.X", valuation_payload=payload()) is None
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM valuation_theses").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM valuation_snapshots").fetchone()[0] == 0


def test_legacy_db_initialization_does_not_implicitly_migrate_snapshots(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE legacy_fixture(id INTEGER)")
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    legacy_db = memory_db.MemoryDB(str(path), str(tmp_path / "chroma"))
    assert legacy_db.save_valuation_thesis("SYNTH.X", valuation_payload=payload()) is None
    with legacy_db._conn() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='valuation_snapshots'").fetchone()
        assert conn.execute("SELECT count(*) FROM valuation_theses").fetchone()[0] == 0
