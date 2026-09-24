"""Offline contract for the durable valuation preparation queue."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from bellomberg.storage.valuation_jobs import (
    LeaseLost, RetryDenied, ValuationJobStore, assert_lease, ensure_schema,
)
from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 23, 10, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def queue(tmp_path):
    path = tmp_path / "jobs.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        conn.execute("INSERT INTO existing_data VALUES ('preserve')")
        before = fingerprint(conn)["existing_data"]
        ensure_schema(conn)
        ensure_schema(conn)
        assert fingerprint(conn)["existing_data"] == before
    clock = Clock()
    return path, clock, ValuationJobStore(path, clock=clock)


def test_constructor_and_reads_never_create_file_or_schema(tmp_path, monkeypatch):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        ValuationJobStore(missing)
    assert not missing.exists()

    empty = tmp_path / "empty.db"
    with sqlite3.connect(empty) as conn:
        conn.execute("CREATE TABLE existing_data (value TEXT)")
        before = fingerprint(conn), schema_fingerprint(conn)
    with pytest.raises(RuntimeError, match="schema"):
        ValuationJobStore(empty)
    with sqlite3.connect(empty) as conn:
        assert (fingerprint(conn), schema_fingerprint(conn)) == before

    migrated = tmp_path / "migrated.db"
    with sqlite3.connect(migrated) as conn:
        ensure_schema(conn)
    monkeypatch.setattr("bellomberg.storage.valuation_jobs.ensure_schema",
                        lambda conn: pytest.fail("store invoked migration"))
    assert ValuationJobStore(migrated).get(999) is None


def test_restart_deduplicates_research_without_live_price_and_keeps_request(queue):
    path, clock, first = queue
    request = {"case": {"as_of": "2026-09-23"}, "price": 101.0, "sources": ["sha-a"]}
    job = first.enqueue("SYNTH-A", "prepare", "source-sha", request)
    request["sources"].append("mutated")
    restarted = ValuationJobStore(path, clock=clock)
    same = restarted.enqueue("SYNTH-A", "prepare", "source-sha",
                             {"sources": ["sha-a"], "price": 105.0,
                              "case": {"as_of": "2026-09-23"}})
    assert same["id"] == job["id"]
    assert job["reused"] is False
    assert same["reused"] is True
    assert same["reuse_note"] == "price_only_ignored_for_research_identity"
    assert same["request"] == {"case": {"as_of": "2026-09-23"},
                               "price": 101.0, "sources": ["sha-a"]}
    assert restarted.get(job["id"])["request"] == same["request"]
    assert same["request_sha256"] == job["request_sha256"]
    assert restarted.enqueue("SYNTH-A", "reprice", "source-sha", {"price": 101.0})["id"] != \
        restarted.enqueue("SYNTH-A", "reprice", "source-sha", {"price": 105.0})["id"]
    assert restarted.enqueue("SYNTH-A", "prepare", "new-evidence", same["request"])["id"] != job["id"]


def test_two_connections_claim_only_one_global_job(queue):
    path, clock, first = queue
    first.enqueue("SYNTH-A", "prepare", "e1", {"data": 1})
    first.enqueue("SYNTH-B", "prepare", "e2", {"data": 2})
    second = ValuationJobStore(path, clock=clock)
    barrier = Barrier(2)

    def claim(store, owner):
        barrier.wait()
        return store.claim(owner, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(claim, first, "worker-a")
        b = pool.submit(claim, second, "worker-b")
        results = [a.result(), b.result()]
    assert sum(item is not None for item in results) == 1
    active = next(item for item in results if item)
    assert active["status"] == "running"
    assert active["attempts"] == 1
    assert first.claim("worker-c", lease_seconds=30) is None


def test_expired_lease_interrupts_and_old_token_cannot_publish(queue):
    _, clock, store = queue
    job = store.enqueue("SYNTH-A", "prepare", "e1", {"data": 1}, max_attempts=2)
    active = store.claim("worker-a", lease_seconds=10)
    assert active["id"] == job["id"]
    clock.advance(10)
    with pytest.raises(LeaseLost):
        store.finish(job["id"], "worker-a", active["lease_token"],
                     status="succeeded", result={"published": True})
    assert store.get(job["id"])["status"] == "interrupted"
    assert store.claim("worker-b", lease_seconds=10) is None
    with pytest.raises(LeaseLost):
        store.heartbeat(job["id"], "worker-a", active["lease_token"], lease_seconds=10)
    with pytest.raises(RetryDenied):
        store.recover_interrupted(job["id"], reason="checked receipt",
                                  cost_reconciliation={"reference": "receipt-1", "charged_usd": "0"},
                                  safe_to_retry=False)
    assert store.get(job["id"])["status"] == "interrupted"
    store.recover_interrupted(job["id"], reason="provider charge reconciled",
                              cost_reconciliation={"reference": "receipt-1", "charged_usd": "0"},
                              safe_to_retry=True)
    retried = store.claim("worker-b", lease_seconds=10)
    assert retried["id"] == job["id"] and retried["attempts"] == 2
    assert retried["lease_token"] != active["lease_token"]
    with pytest.raises(LeaseLost):
        store.finish(job["id"], "worker-a", active["lease_token"], status="failed", result={})
    assert store.get(job["id"])["status"] == "running"
    assert [event["action"] for event in store.events(job["id"])] == [
        "enqueued", "claimed", "interrupted", "recovered", "claimed"]


def test_retry_requires_trust_respects_not_before_and_attempt_cap(queue):
    _, clock, store = queue
    job = store.enqueue("SYNTH-B", "prepare", "e2", {"data": 2}, max_attempts=2)
    active = store.claim("worker-a", lease_seconds=60)
    with pytest.raises(RetryDenied):
        store.retry_temporary(job["id"], "worker-a", active["lease_token"],
                              reason="transient", not_before=clock.value + timedelta(minutes=5),
                              safe_to_retry=False)
    due = clock.value + timedelta(minutes=5)
    store.retry_temporary(job["id"], "worker-a", active["lease_token"],
                          reason="transient connection error", not_before=due,
                          safe_to_retry=True)
    assert store.claim("worker-b", lease_seconds=60) is None
    clock.advance(300)
    again = store.claim("worker-b", lease_seconds=60)
    assert again["attempts"] == 2
    with pytest.raises(RetryDenied, match="attempt"):
        store.retry_temporary(job["id"], "worker-b", again["lease_token"],
                              reason="another transient error", not_before=clock.value,
                              safe_to_retry=True)
    store.finish(job["id"], "worker-b", again["lease_token"],
                 status="incomplete", result={"issues": ["missing source"]})
    assert store.get(job["id"])["status"] == "incomplete"
    with pytest.raises(LeaseLost):
        store.finish(job["id"], "worker-b", again["lease_token"],
                     status="succeeded", result={"wrong": True})
    with sqlite3.connect(store.db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE valuation_jobs SET request_json='{}' WHERE id=?", (job["id"],))


def test_recovery_requires_auditable_cost_and_never_resumes_at_attempt_cap(queue):
    _, clock, store = queue
    job = store.enqueue("SYNTH-A", "prepare", "e3", {"data": 3})
    active = store.claim("worker-a", lease_seconds=1)
    clock.advance(2)
    store.expire_leases()
    with pytest.raises(RetryDenied):
        store.recover_interrupted(job["id"], reason="checked",
                                  cost_reconciliation={"reference": "", "charged_usd": "0"},
                                  safe_to_retry=True)
    with pytest.raises(RetryDenied, match="attempt"):
        store.recover_interrupted(job["id"], reason="provider charge reconciled",
                                  cost_reconciliation={"reference": "receipt-2", "charged_usd": "0"},
                                  safe_to_retry=True)
    assert store.get(job["id"])["status"] == "interrupted"
    with pytest.raises(LeaseLost):
        store.heartbeat(job["id"], "worker-a", active["lease_token"], lease_seconds=10)


def test_checkpoint_survives_restart_but_expired_token_cannot_replace_it(queue):
    path, clock, store = queue
    job = store.enqueue("SYNTH-A", "prepare", "e4", {"documents": ["source-a"]},
                        max_attempts=2)
    active = store.claim("worker-a", lease_seconds=10)
    checkpoint = store.checkpoint(job["id"], "worker-a", active["lease_token"],
                                  stage="workbook_generated",
                                  payload={"artifact_id": "synthetic-artifact-1"})
    assert checkpoint["checkpoint"] == {
        "stage": "workbook_generated", "payload": {"artifact_id": "synthetic-artifact-1"}}
    with pytest.raises(ValueError, match="finite JSON"):
        store.checkpoint(job["id"], "worker-a", active["lease_token"],
                         stage="invalid", payload={"cost": float("nan")})
    restarted = ValuationJobStore(path, clock=clock)
    assert restarted.get(job["id"])["checkpoint"] == checkpoint["checkpoint"]
    clock.advance(10)
    with pytest.raises(LeaseLost):
        restarted.checkpoint(job["id"], "worker-a", active["lease_token"],
                             stage="published", payload={"artifact_id": "wrong"})
    assert restarted.get(job["id"])["status"] == "interrupted"
    assert restarted.get(job["id"])["checkpoint"] == checkpoint["checkpoint"]
    restarted.recover_interrupted(job["id"], reason="provider receipt reviewed",
                                  cost_reconciliation={"reference": "receipt-3", "charged_usd": "0.01"},
                                  safe_to_retry=True)
    resumed = restarted.claim("worker-b", lease_seconds=10)
    assert resumed["checkpoint"] == checkpoint["checkpoint"]
    with pytest.raises(LeaseLost):
        restarted.checkpoint(job["id"], "worker-a", active["lease_token"],
                             stage="published", payload={"artifact_id": "wrong"})
    assert restarted.get(job["id"])["checkpoint"] == checkpoint["checkpoint"]


def test_public_lease_guard_reads_inside_caller_transaction_without_committing(queue):
    path, clock, store = queue
    job = store.enqueue("SYNTH-A", "prepare", "guard-evidence", {"data": 1})
    active = store.claim("worker-a", lease_seconds=30)
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = conn.total_changes
        assert assert_lease(conn, job["id"], "worker-a", active["lease_token"], now=clock.value) is None
        assert conn.in_transaction and conn.total_changes == before
        conn.execute("INSERT INTO existing_data VALUES ('caller may roll back')")
        conn.rollback()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM existing_data").fetchone()[0] == 1
    assert store.get(job["id"])["status"] == "running"


def test_public_guard_rejects_wrong_identity_and_exact_expiry_without_writes(queue):
    path, clock, store = queue
    job = store.enqueue("SYNTH-B", "prepare", "guard-evidence", {"data": 1})
    active = store.claim("worker-a", lease_seconds=10)
    old_events = store.events(job["id"])
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = conn.total_changes
        for owner, token, instant in (
            ("worker-b", active["lease_token"], clock.value),
            ("worker-a", "obsolete-token", clock.value),
            ("worker-a", active["lease_token"], clock.value + timedelta(seconds=10)),
        ):
            with pytest.raises(LeaseLost):
                assert_lease(conn, job["id"], owner, token, now=instant)
            assert conn.in_transaction and conn.total_changes == before
        conn.rollback()
    assert store.get(job["id"])["status"] == "running"
    assert store.events(job["id"]) == old_events
    store.finish(job["id"], "worker-a", active["lease_token"], status="failed", result={})
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(LeaseLost):
            assert_lease(conn, job["id"], "worker-a", active["lease_token"], now=clock.value)
        conn.rollback()


def test_public_guard_requires_existing_schema_and_active_transaction(queue, tmp_path):
    path, clock, store = queue
    job = store.enqueue("SYNTH-A", "prepare", "guard-evidence", {"data": 1})
    active = store.claim("worker-a", lease_seconds=10)
    with sqlite3.connect(path, isolation_level=None) as conn:
        with pytest.raises(RuntimeError, match="transaction"):
            assert_lease(conn, job["id"], "worker-a", active["lease_token"], now=clock.value)
    unmigrated = tmp_path / "unmigrated.db"
    with sqlite3.connect(unmigrated, isolation_level=None) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
        before = schema_fingerprint(conn)
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="schema"):
            assert_lease(conn, job["id"], "worker-a", active["lease_token"], now=clock.value)
        assert conn.in_transaction and conn.total_changes == 0
        conn.rollback()
        assert schema_fingerprint(conn) == before
