"""Current-pointer safety uses real generated models and temporary SQLite."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone
import sqlite3
import pytest

from test_input_preparation import _bundle, _documents, _propose_operating


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    from bellomberg.storage.valuation_versions import ValuationVersions, ensure_schema
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    db = memory_db.MemoryDB(str(tmp_path / "versions.db"), str(tmp_path / "chroma"))
    with db._conn() as conn:
        ensure_schema(conn)
    return db, ValuationVersions(db, roots=[tmp_path],
        clock=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc)), tmp_path


def _generate(db, directory, *, incomplete=False):
    from bellomberg.valuation.preparation_service import prepare_and_generate
    result = prepare_and_generate(_bundle(), documents=[] if incomplete else _documents(),
        propose=_propose_operating, output_dir=directory)
    thesis_id = db.save_valuation_thesis(result["ticker"], valuation_payload=result)
    assert thesis_id is not None
    return result


def _publish(store, result, current=None):
    return store.publish(result["snapshot_id"], result["generation_id"], expected_current_generation=current)


def test_failed_refresh_keeps_last_valid_generation_and_records_reason(setup):
    db, store, directory = setup
    first = _generate(db, directory)
    published = _publish(store, first)
    assert published["status"] == "published"
    failed = _generate(db, directory, incomplete=True)
    assert _publish(store, failed, first["generation_id"])["status"] == "incomplete"
    after = store.current(first["ticker"])
    assert after["current"]["generation_id"] == first["generation_id"]
    assert after["current_published_at"] == published["created_at"]
    assert after["latest_attempt"]["generation_id"] == failed["generation_id"]
    assert after["latest_attempt"]["reason"]
    assert after["artifact"]["available"]


def test_delayed_publication_cannot_promote_expired_automatic_inputs(setup):
    db, store, directory = setup
    first = _generate(db, directory)
    assert _publish(store, first)["status"] == "published"
    late = _generate(db, directory)
    store.clock = lambda: datetime(2026, 9, 11, tzinfo=timezone.utc)
    rejected = _publish(store, late, first["generation_id"])
    assert rejected["status"] == "incomplete"
    assert "scadut" in rejected["reason"]
    view = store.current(first["ticker"])
    assert view["current_generation"] == first["generation_id"]
    assert view["latest_attempt"]["generation_id"] == late["generation_id"]
    assert view["artifact"]["available"], "historical current file must remain accessible"


def test_input_expiring_during_artifact_check_cannot_become_current(setup, monkeypatch):
    db, store, directory = setup
    first = _generate(db, directory)
    assert _publish(store, first)["status"] == "published"
    candidate = _generate(db, directory)
    now = [datetime(2026, 9, 10, tzinfo=timezone.utc)]
    store.clock = lambda: now[0]
    original_artifact = store._artifact

    def cross_midnight(conn, payload):
        result = original_artifact(conn, payload)
        now[0] = datetime(2026, 9, 11, tzinfo=timezone.utc)
        return result

    monkeypatch.setattr(store, "_artifact", cross_midnight)
    attempt = _publish(store, candidate, first["generation_id"])
    assert attempt["status"] == "incomplete"
    assert "scadut" in attempt["reason"]
    assert attempt["created_at"].startswith("2026-09-11")
    view = store.current(first["ticker"])
    assert view["current_generation"] == first["generation_id"]
    assert view["latest_attempt"]["generation_id"] == candidate["generation_id"]


def test_compare_and_swap_prevents_late_worker_from_replacing_new_current(setup):
    db, store, directory = setup
    a, b = _generate(db, directory), _generate(db, directory)
    assert _publish(store, a)["status"] == "published"
    assert _publish(store, b)["status"] == "superseded"
    assert store.current(a["ticker"])["current"]["generation_id"] == a["generation_id"]
    assert _publish(store, a)["status"] == "published"  # idempotent replay
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM valuation_publications").fetchone()[0] == 2


def test_locked_and_locally_modified_files_are_preserved(setup):
    db, store, directory = setup
    a = _generate(db, directory)
    _publish(store, a)
    store.set_locked(a["ticker"], True)
    before = Path(a["path"]).read_bytes()
    b = _generate(db, directory)
    assert _publish(store, b, a["generation_id"])["status"] == "held"
    assert Path(a["path"]).read_bytes() == before
    Path(a["path"]).write_bytes(before + b"personal edit")
    view = store.current(a["ticker"])
    assert not view["artifact"]["available"] and "modific" in view["artifact"]["reason"]
    assert Path(a["path"]).read_bytes().endswith(b"personal edit")


def test_unregistered_or_corrupt_artifacts_cannot_become_current(setup):
    db, store, directory = setup
    from bellomberg.valuation.preparation_service import prepare_and_generate
    unregistered = prepare_and_generate(_bundle(), documents=_documents(), propose=_propose_operating, output_dir=directory)
    with pytest.raises(ValueError, match="registr"):
        _publish(store, unregistered)
    registered = _generate(db, directory)
    Path(registered["path"]).with_suffix(".payload.json").write_text("{}", encoding="utf-8")
    assert _publish(store, registered)["status"] == "artifact_failed"
    assert store.current(registered["ticker"])["current"] is None


def test_schema_is_explicit_and_history_is_immutable(setup):
    db, store, directory = setup
    result = _generate(db, directory)
    _publish(store, result)
    with db._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM valuation_publications")


def _approved_bundle(db, *, valid_until=None):
    from bellomberg.storage import method_records_store as archive
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from bellomberg.valuation.method_records_archive import SET_KEYS
    from test_sector_analysis import providers_for, DAY
    prepared = prepare_method_inputs(_bundle(), documents=_documents(), propose=_propose_operating)
    proposal = prepared["proposal"]
    records = deepcopy(proposal["method_records"])
    if valid_until is not None:
        for record in records:
            record["valid_until"] = valid_until
    with db._conn() as conn:
        set_id = archive.proponi_set(conn, ticker="SYNTH-EXT", method_id=proposal["method_id"],
            method_version=proposal["method_version"], records=records,
            scenario_rationale=proposal["analysis_context"]["scenario_rationale"],
            provenance="Synthetic fixture", prepared_by="Synthetic proposer", prepared_at=DAY)
        archive.rivedi_set(conn, set_id=set_id, decision="approvato", reviewer="Synthetic PM", reviewed_at=DAY)
        row = archive.leggi_approvati(conn, "SYNTH-EXT", DAY)[0]
    source = {"source_id": "method_records_archive", "status": "ok", "as_of": DAY,
              "data": {"sets": {row["method_id"]: {"set_id": row["id"],
                  **{key: row[key] for key in SET_KEYS}, "records": row["records"], "review": row["review"]}}}}
    providers = providers_for()
    providers["method_inputs"] = lambda *_args, **_kwargs: deepcopy(source)
    return prepare_sector_analysis("SYNTH-EXT", as_of=DAY, providers=providers), set_id


def _from_bundle(db, directory, bundle):
    from bellomberg.valuation.dcf_engine import generate_valuation
    result = generate_valuation("SYNTH-EXT", prepared_bundle=bundle, output_dir=directory)
    assert result["valuation_usability"]["usable"], result.get("acquisition_tasks")
    assert db.save_valuation_thesis(result["ticker"], valuation_payload=result) is not None
    return result


def test_approval_is_bound_to_actual_consumed_records(setup):
    from bellomberg.valuation.sector_analysis import _hash
    db, store, directory = setup
    bundle, _ = _approved_bundle(db)
    # Genuine archive envelope A cannot certify different, otherwise valid B.
    next(row for row in bundle["case"]["records"]
         if row["driver"] == "wacc" and row["scenario"] == "base")["value"] = .12
    bundle["snapshot_id"] = _hash({k: v for k, v in bundle.items() if k != "snapshot_id"})
    result = _from_bundle(db, directory, bundle)
    with pytest.raises(ValueError, match="approvazione"):
        _publish(store, result)
    assert store.current(result["ticker"]) is None


def test_active_approval_has_precedence_but_withdrawal_releases_it(setup):
    from bellomberg.storage import method_records_store as archive
    from test_sector_analysis import DAY
    db, store, directory = setup
    bundle, set_id = _approved_bundle(db)
    approved = _from_bundle(db, directory, bundle)
    assert _publish(store, approved)["origin"] == "approved"
    automatic = _generate(db, directory)
    assert _publish(store, automatic, approved["generation_id"])["status"] == "held"
    assert store.current(approved["ticker"])["current"]["generation_id"] == approved["generation_id"]
    with db._conn() as conn:
        archive.rivedi_set(conn, set_id=set_id, decision="ritirato", reviewer="Synthetic PM", reviewed_at=DAY)
    assert store.current(approved["ticker"])["approval"] == {"publication_origin": "approved", "active": False}
    next_automatic = _generate(db, directory)
    assert _publish(store, next_automatic, approved["generation_id"])["status"] == "published"
    assert Path(approved["path"]).is_file()


def test_expired_archived_approval_records_incomplete_attempt(setup):
    db, store, directory = setup
    bundle, _ = _approved_bundle(db)
    current = _from_bundle(db, directory, bundle)
    assert _publish(store, current)["status"] == "published"
    candidate = _from_bundle(db, directory, bundle)
    store.clock = lambda: datetime(2026, 9, 11, tzinfo=timezone.utc)
    attempt = _publish(store, candidate, current["generation_id"])
    assert attempt["status"] == "incomplete"
    assert attempt["origin"] == "approved"  # Genuine archived lineage, no active approval claim.
    assert "scadut" in attempt["reason"].lower() and "approvazione" in attempt["reason"].lower()
    view = store.current(current["ticker"])
    assert view["current_generation"] == current["generation_id"]
    assert view["latest_attempt"]["generation_id"] == candidate["generation_id"]
    assert view["approval"]["active"] is False


def test_withdrawn_archived_approval_records_incomplete_attempt(setup):
    from bellomberg.storage import method_records_store as archive
    db, store, directory = setup
    bundle, set_id = _approved_bundle(db, valid_until="2026-09-12")
    current = _from_bundle(db, directory, bundle)
    assert _publish(store, current)["status"] == "published"
    candidate = _from_bundle(db, directory, bundle)
    with db._conn() as conn:
        archive.rivedi_set(conn, set_id=set_id, decision="ritirato",
            reviewer="Synthetic PM", reviewed_at="2026-09-11")
    store.clock = lambda: datetime(2026, 9, 11, tzinfo=timezone.utc)
    attempt = _publish(store, candidate, current["generation_id"])
    assert attempt["status"] == "incomplete"
    assert attempt["origin"] == "approved"
    assert "approvazione" in attempt["reason"].lower()
    view = store.current(current["ticker"])
    assert view["current_generation"] == current["generation_id"]
    assert view["latest_attempt"]["generation_id"] == candidate["generation_id"]
    assert view["approval"]["active"] is False


def test_approval_withdrawn_during_artifact_check_blocks_promotion(setup, monkeypatch):
    from bellomberg.storage import method_records_store as archive
    db, store, directory = setup
    bundle, set_id = _approved_bundle(db, valid_until="2026-09-12")
    current = _from_bundle(db, directory, bundle)
    assert _publish(store, current)["status"] == "published"
    candidate = _from_bundle(db, directory, bundle)
    now = [datetime(2026, 9, 10, tzinfo=timezone.utc)]
    store.clock = lambda: now[0]
    original_artifact = store._artifact

    def revoke_during_hash(conn, payload):
        result = original_artifact(conn, payload)
        archive.rivedi_set(conn, set_id=set_id, decision="ritirato",
            reviewer="Synthetic PM", reviewed_at="2026-09-11")
        now[0] = datetime(2026, 9, 11, tzinfo=timezone.utc)
        return result

    monkeypatch.setattr(store, "_artifact", revoke_during_hash)
    attempt = _publish(store, candidate, current["generation_id"])
    assert attempt["status"] == "incomplete" and "approvazione" in attempt["reason"].lower()
    assert attempt["created_at"].startswith("2026-09-11")
    monkeypatch.setattr(store, "_artifact", original_artifact)
    view = store.current(current["ticker"])
    assert view["current_generation"] == current["generation_id"]
    assert view["latest_attempt"]["generation_id"] == candidate["generation_id"]


def test_missing_database_is_never_recreated(setup):
    db, store, directory = setup
    Path(db.db_path).unlink()  # Disposable test database only.
    for action in (lambda: store.current("SYNTH-EXT"),
                   lambda: store.set_locked("SYNTH-EXT", True),
                   lambda: store.publish("missing", "missing", expected_current_generation=None)):
        with pytest.raises(FileNotFoundError):
            action()
        assert not Path(db.db_path).exists()


def test_unmigrated_read_preserves_schema_and_contents(setup):
    from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
    db, store, _ = setup
    with db._conn() as conn:
        conn.execute("DROP TRIGGER valuation_publications_no_update")
        before = fingerprint(conn), schema_fingerprint(conn)
    with pytest.raises(RuntimeError, match="schema"):
        store.current("SYNTH-EXT")
    with db._conn() as conn:
        assert (fingerprint(conn), schema_fingerprint(conn)) == before


def test_lease_expiring_during_artifact_check_rolls_back_publication(setup, monkeypatch):
    from bellomberg.storage.valuation_jobs import ValuationJobStore, ensure_schema, assert_lease, LeaseLost
    from test_valuation_jobs import Clock
    db, versions, directory = setup
    with db._conn() as conn:
        ensure_schema(conn)
    clock = Clock()
    jobs = ValuationJobStore(db.db_path, clock=clock)
    queued = jobs.enqueue("SYNTH-EXT", "prepare", "fixture-evidence", {})
    active = jobs.claim("worker", lease_seconds=10)
    original = _generate(db, directory)
    _publish(versions, original)
    candidate = _generate(db, directory)
    before_check = versions._artifact
    def slow_check(conn, payload):
        result = before_check(conn, payload)
        clock.advance(10)
        return result
    monkeypatch.setattr(versions, "_artifact", slow_check)
    def guard(conn):
        assert_lease(conn, queued["id"], "worker", active["lease_token"], now=clock.value)
    with pytest.raises(LeaseLost):
        versions.publish(candidate["snapshot_id"], candidate["generation_id"],
            expected_current_generation=original["generation_id"], guard=guard)
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM valuation_publications").fetchone()[0] == 1
        assert conn.execute("SELECT current_generation,latest_attempt FROM valuation_model_heads").fetchone()[:] == (
            original["generation_id"], original["generation_id"])
    assert Path(original["path"]).is_file() and Path(candidate["path"]).is_file()
