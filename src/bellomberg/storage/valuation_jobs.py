"""Durable, explicitly migrated SQLite queue for valuation preparation.

The queue stores inputs and receipts; it does not run a worker, call a provider,
publish a valuation, or decide a spending budget. For ``prepare`` requests the
optional top-level ``price`` is retained in the first immutable request but
excluded from research identity. A changed quote reuses that job and requires
a separate ``reprice`` job. Historical prices inside source evidence remain
part of the research identity. ``reprice`` fingerprints its complete request.
"""
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS valuation_jobs (
      id INTEGER PRIMARY KEY,
      ticker TEXT NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('prepare','reprice')),
      evidence_key TEXT NOT NULL,
      content_key TEXT NOT NULL,
      request_json TEXT NOT NULL,
      request_sha256 TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN
        ('queued','running','interrupted','succeeded','incomplete','failed')),
      attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
      max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
      not_before TEXT NOT NULL,
      lease_owner TEXT,
      lease_token TEXT,
      lease_deadline TEXT,
      checkpoint_json TEXT,
      checkpoint_at TEXT,
      result_json TEXT,
      reason TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      finished_at TEXT,
      UNIQUE(ticker,kind,content_key),
      CHECK(attempts <= max_attempts),
      CHECK((status = 'running' AND lease_owner IS NOT NULL AND
             lease_token IS NOT NULL AND lease_deadline IS NOT NULL) OR
            (status != 'running' AND lease_owner IS NULL AND
             lease_token IS NULL AND lease_deadline IS NULL)),
      CHECK((status IN ('succeeded','incomplete','failed')) =
            (finished_at IS NOT NULL))
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_valuation_job_global_running "
    "ON valuation_jobs((1)) WHERE status='running'",
    "CREATE INDEX IF NOT EXISTS idx_valuation_job_due "
    "ON valuation_jobs(status,not_before,id)",
    """CREATE TABLE IF NOT EXISTS valuation_job_events (
      id INTEGER PRIMARY KEY,
      job_id INTEGER NOT NULL REFERENCES valuation_jobs(id),
      action TEXT NOT NULL,
      at TEXT NOT NULL,
      detail_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_valuation_job_events ON valuation_job_events(job_id,id)",
    """CREATE TRIGGER IF NOT EXISTS valuation_job_input_immutable
      BEFORE UPDATE OF ticker,kind,evidence_key,content_key,request_json,
                       request_sha256,max_attempts ON valuation_jobs
      BEGIN SELECT RAISE(ABORT,'valuation job input immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS valuation_job_final_immutable
      BEFORE UPDATE ON valuation_jobs
      WHEN OLD.status IN ('succeeded','incomplete','failed')
      BEGIN SELECT RAISE(ABORT,'valuation job final immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS valuation_job_no_delete
      BEFORE DELETE ON valuation_jobs
      BEGIN SELECT RAISE(ABORT,'valuation job immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS valuation_job_event_immutable
      BEFORE UPDATE ON valuation_job_events
      BEGIN SELECT RAISE(ABORT,'valuation job event immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS valuation_job_event_no_delete
      BEFORE DELETE ON valuation_job_events
      BEGIN SELECT RAISE(ABORT,'valuation job event immutable'); END""",
)
TABLES = ("valuation_jobs", "valuation_job_events")
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.^=_:/-]{0,31}\Z")
_FINAL = frozenset(("succeeded", "incomplete", "failed"))


class LeaseLost(RuntimeError):
    """The supplied worker lease is absent, superseded, or expired."""


class RetryDenied(RuntimeError):
    """A caller did not authorize or cannot safely perform another attempt."""


def ensure_schema(conn):
    """Run only from an explicit migration or a disposable test database."""
    for statement in SCHEMA:
        conn.execute(statement)


def _signature(conn):
    from bellomberg.storage.sqlite_checks import schema_fingerprint

    keys = {kind + ":" + name for kind, name, table in conn.execute(
        "SELECT type,name,tbl_name FROM sqlite_master") if table in TABLES}
    return {key: value for key, value in schema_fingerprint(conn).items() if key in keys}


def _expected_signature():
    with sqlite3.connect(":memory:") as conn:
        # Compare against an isolated reference without invoking the explicit
        # migration entry point from the store constructor.
        for statement in SCHEMA:
            conn.execute(statement)
        return _signature(conn)


def _json(value):
    def check(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            for child in item.values():
                check(child)
        elif isinstance(item, (tuple, set)):
            raise ValueError("request requires plain JSON values")
        elif isinstance(item, list):
            for child in item:
                check(child)

    check(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("finite JSON value required") from exc


def _when(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def assert_lease(conn, job_id, owner, token, *, now=None):
    """Attest an unexpired lease in the caller's existing SQLite transaction.

    The publisher must use its own ``BEGIN IMMEDIATE`` connection to the same
    database and call this guard before publishing. This function only reads
    ``main.valuation_jobs``: it never expires a job, commits, opens another
    connection, or installs schema. A deadline equal to ``now`` is expired.
    """
    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        raise RuntimeError("active caller SQLite transaction required for lease guard")
    instant = _when(now if now is not None else datetime.now(timezone.utc))
    if not conn.execute("SELECT 1 FROM main.sqlite_master "
                        "WHERE type='table' AND name='valuation_jobs'").fetchone():
        raise RuntimeError("valuation jobs schema missing: migrate explicitly")
    if (type(job_id) is not int or job_id < 1 or not isinstance(owner, str) or not owner
            or not isinstance(token, str) or not token):
        raise LeaseLost("lease absent, expired, or superseded")
    try:
        valid = conn.execute("""SELECT 1 FROM main.valuation_jobs WHERE id=?
            AND status='running' AND lease_owner=? AND lease_token=? AND lease_deadline>?""",
            (job_id, owner, token, instant)).fetchone()
    except sqlite3.OperationalError as exc:
        raise RuntimeError("valuation jobs schema incompatible: migrate explicitly") from exc
    if valid is None:
        raise LeaseLost("lease absent, expired, or superseded")


def _text(value, label, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{label} must be nonempty text (max {limit})")
    return value.strip()


def _reconciliation(value, *, price_only=False):
    if (price_only and isinstance(value, dict) and set(value) == {"reference", "ai_spend"}
            and value["ai_spend"] == "not_applicable_price_only"):
        return {"reference": _text(value["reference"], "cost reference", 300),
                "ai_spend": "not_applicable_price_only"}
    if not isinstance(value, dict) or set(value) != {"reference", "charged_usd"}:
        raise RetryDenied("cost reconciliation needs reference and charged_usd")
    try:
        reference = _text(value["reference"], "cost reference", 300)
        charge = value["charged_usd"]
        if not isinstance(charge, str):
            raise ValueError("charged_usd must be a decimal string")
        amount = Decimal(charge)
        if not amount.is_finite() or amount < 0:
            raise ValueError("charged_usd must be finite and nonnegative")
    except (ValueError, InvalidOperation) as exc:
        raise RetryDenied("invalid cost reconciliation") from exc
    return {"reference": reference, "charged_usd": str(amount)}


class ValuationJobStore:
    """SQLite store with one global leased worker and explicit recovery."""

    def __init__(self, db_path, *, clock=None):
        self.db_path = os.fspath(db_path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError(f"valuation DB absent: {self.db_path}")
        with self._connect(read_only=True) as conn:
            expected, actual = _expected_signature(), _signature(conn)
            if any(actual.get(key) != digest for key, digest in expected.items()):
                raise RuntimeError("valuation jobs schema absent or incompatible: migrate explicitly")

    @contextmanager
    def _connect(self, *, read_only=False):
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError(f"valuation DB absent: {self.db_path}")
        uri = Path(self.db_path).resolve().as_uri() + ("?mode=ro" if read_only else "?mode=rw")
        conn = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()

    def _now(self):
        return _when(self.clock())

    @staticmethod
    def _event(conn, job_id, action, at, detail):
        conn.execute("INSERT INTO valuation_job_events(job_id,action,at,detail_json) VALUES(?,?,?,?)",
                     (job_id, action, at, _json(detail)))

    @staticmethod
    def _row(row):
        if row is None:
            return None
        item = dict(row)
        item["request"] = json.loads(item.pop("request_json"))
        item["result"] = json.loads(item.pop("result_json")) if item["result_json"] else None
        item["checkpoint"] = json.loads(item.pop("checkpoint_json")) if item["checkpoint_json"] else None
        return item

    @classmethod
    def _get(cls, conn, job_id):
        return cls._row(conn.execute("SELECT * FROM valuation_jobs WHERE id=?", (job_id,)).fetchone())

    def get(self, job_id):
        with self._connect(read_only=True) as conn:
            return self._get(conn, job_id)

    def events(self, job_id):
        with self._connect(read_only=True) as conn:
            rows = conn.execute("SELECT * FROM valuation_job_events WHERE job_id=? ORDER BY id", (job_id,))
            return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]

    def latest_price_result(self, ticker, generation_id):
        """Latest price attempt for this exact model, including pending work.

        A failed price observation must stay visible rather than silently using
        an older successful quote. This never changes the model's input snapshot.
        """
        with self._connect(read_only=True) as conn:
            row = conn.execute("""SELECT * FROM valuation_jobs WHERE ticker=? AND kind='reprice'
                AND json_extract(request_json,'$.generation_id')=?
                ORDER BY id DESC LIMIT 1""", (ticker, generation_id)).fetchone()
            return self._row(row)

    def enqueue(self, ticker, kind, evidence_key, request, *, max_attempts=1, event_identity=None,
                coalesce_tracking=False):
        """Persist exact inputs; trusted event callers may deduplicate a source revision.

        event_identity must include the authorization and source revision. It is
        never an LLM/API input. The first immutable request (including its cutoff
        and captured current generation) wins even if a later notification differs.
        """
        if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker):
            raise ValueError("invalid ticker")
        if kind not in ("prepare", "reprice"):
            raise ValueError("invalid job kind")
        evidence_key = _text(evidence_key, "evidence_key", 256)
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        if coalesce_tracking and (kind != "prepare" or type(request.get("refresh")) is not bool
                                  or not isinstance(request.get("authorization"), dict)):
            raise ValueError("tracking coalescence requires an authorized preparation intent")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        canonical_request = dict(request)
        if "_event_identity" in canonical_request:
            raise ValueError("reserved queue event identity field")
        if event_identity is not None:
            canonical_request["_event_identity"] = _text(event_identity, "event identity", 256)
        identity_request = dict(canonical_request)
        if kind == "prepare":
            identity_request.pop("price", None)
        if event_identity is not None:
            identity_request = {"_event_identity": canonical_request["_event_identity"]}
        request_json = _json(canonical_request)
        request_sha = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        content_key = hashlib.sha256(_json({"evidence_key": evidence_key,
                                           "request": identity_request}).encode("utf-8")).hexdigest()
        now = self._now()
        with self._write() as conn:
            pending = []
            if coalesce_tracking:
                pending = [row for row in conn.execute("SELECT * FROM valuation_jobs WHERE ticker=? "
                    "AND kind='prepare' AND status IN ('queued','running') ORDER BY id", (ticker,))
                    if json.loads(row["request_json"]).get("authorization") == request["authorization"]]
                if not request["refresh"] and pending:
                    return {**self._row(pending[-1]), "reused": True,
                            "reuse_note": "tracking_reuses_pending_preparation"}
            cursor = conn.execute("""INSERT INTO valuation_jobs
                (ticker,kind,evidence_key,content_key,request_json,request_sha256,
                 status,max_attempts,not_before,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'queued',?,?,?,?)
                ON CONFLICT(ticker,kind,content_key) DO NOTHING""",
                (ticker, kind, evidence_key, content_key, request_json, request_sha,
                 max_attempts, now, now, now))
            row = conn.execute("SELECT * FROM valuation_jobs WHERE ticker=? AND kind=? AND content_key=?",
                               (ticker, kind, content_key)).fetchone()
            if cursor.rowcount:
                self._event(conn, row["id"], "enqueued", now, {"max_attempts": max_attempts})
                if coalesce_tracking and request["refresh"]:
                    for initial in pending:
                        if (initial["status"] == "queued" and initial["attempts"] == 0
                                and json.loads(initial["request_json"]).get("refresh") is False):
                            reason = "Initial tracking replaced before claim by source event job:" + str(row["id"])
                            conn.execute("UPDATE valuation_jobs SET status='failed',reason=?,updated_at=?,finished_at=? WHERE id=?",
                                         (reason, now, now, initial["id"]))
                            self._event(conn, initial["id"], "superseded_before_start", now,
                                        {"replacement_job_id": row["id"], "observed_attempts": initial["attempts"]})
            item = self._row(row)
            item["reused"] = not bool(cursor.rowcount)
            item["reuse_note"] = (("source_event_reused_original_request_preserved" if event_identity is not None
                                   else "price_only_ignored_for_research_identity")
                                  if item["reused"] and row["request_json"] != request_json
                                  else None)
            return item

    @classmethod
    def _expire(cls, conn, now):
        rows = conn.execute("SELECT id,lease_owner,lease_token FROM valuation_jobs "
                            "WHERE status='running' AND lease_deadline<=?", (now,)).fetchall()
        for row in rows:
            conn.execute("""UPDATE valuation_jobs SET status='interrupted',
                lease_owner=NULL,lease_token=NULL,lease_deadline=NULL,
                reason='lease_expired',updated_at=? WHERE id=?""", (now, row["id"]))
            cls._event(conn, row["id"], "interrupted", now,
                       {"reason": "lease_expired", "former_owner": row["lease_owner"]})
        return [row["id"] for row in rows]

    def expire_leases(self):
        now = self._now()
        with self._write() as conn:
            return self._expire(conn, now)

    def claim(self, owner, *, lease_seconds):
        owner = _text(owner, "lease owner", 200)
        if type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("positive lease_seconds required")
        now = self._now()
        deadline = _when(datetime.fromisoformat(now) + timedelta(seconds=lease_seconds))
        with self._write() as conn:
            self._expire(conn, now)
            if conn.execute("SELECT 1 FROM valuation_jobs WHERE status='running'").fetchone():
                return None
            row = conn.execute("""SELECT j.id FROM valuation_jobs j
                LEFT JOIN valuation_jobs parent ON parent.id=json_extract(j.request_json,'$.approval_bootstrap_job_id')
                    AND parent.ticker=j.ticker AND parent.status='succeeded'
                    AND json_extract(parent.checkpoint_json,'$.payload.source_refresh_after_approval')=1
                WHERE j.status='queued' AND j.not_before<=? AND j.attempts<j.max_attempts
                ORDER BY COALESCE(parent.not_before,j.not_before),COALESCE(parent.id,j.id),j.id LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            conn.execute("""UPDATE valuation_jobs SET status='running',attempts=attempts+1,
                lease_owner=?,lease_token=?,lease_deadline=?,updated_at=? WHERE id=?""",
                (owner, token, deadline, now, row["id"]))
            self._event(conn, row["id"], "claimed", now, {"owner": owner})
            return self._get(conn, row["id"])

    @staticmethod
    def _leased(conn, job_id, owner, token, now):
        return conn.execute("""SELECT * FROM valuation_jobs WHERE id=? AND status='running'
            AND lease_owner=? AND lease_token=? AND lease_deadline>?""",
            (job_id, owner, token, now)).fetchone()

    def heartbeat(self, job_id, owner, token, *, lease_seconds):
        if type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("positive lease_seconds required")
        now = self._now()
        deadline = _when(datetime.fromisoformat(now) + timedelta(seconds=lease_seconds))
        with self._write() as conn:
            self._expire(conn, now)
            leased = self._leased(conn, job_id, owner, token, now)
            if leased:
                conn.execute("UPDATE valuation_jobs SET lease_deadline=?,updated_at=? WHERE id=?",
                             (deadline, now, job_id))
                self._event(conn, job_id, "heartbeat", now, {"owner": owner, "deadline": deadline})
                result = self._get(conn, job_id)
            else:
                result = None
        if result is None:
            raise LeaseLost("lease expired or superseded")
        return result

    def checkpoint(self, job_id, owner, token, *, stage, payload):
        stage = _text(stage, "checkpoint stage", 100)
        if not isinstance(payload, dict):
            raise ValueError("checkpoint payload must be a JSON object")
        checkpoint_json = _json({"stage": stage, "payload": payload})
        now = self._now()
        with self._write() as conn:
            self._expire(conn, now)
            if self._leased(conn, job_id, owner, token, now):
                conn.execute("UPDATE valuation_jobs SET checkpoint_json=?,checkpoint_at=?,updated_at=? WHERE id=?",
                             (checkpoint_json, now, now, job_id))
                self._event(conn, job_id, "checkpoint", now, {"stage": stage,
                            "sha256": hashlib.sha256(checkpoint_json.encode("utf-8")).hexdigest()})
                result = self._get(conn, job_id)
            else:
                result = None
        if result is None:
            raise LeaseLost("lease expired or superseded")
        return result

    def finish(self, job_id, owner, token, *, status, result=None, reason=None):
        if status not in _FINAL:
            raise ValueError("invalid final status")
        result_json = _json(result) if result is not None else None
        if reason is not None:
            reason = _text(reason, "reason", 1000)
        now = self._now()
        with self._write() as conn:
            self._expire(conn, now)
            if self._leased(conn, job_id, owner, token, now):
                conn.execute("""UPDATE valuation_jobs SET status=?,result_json=?,reason=?,
                    lease_owner=NULL,lease_token=NULL,lease_deadline=NULL,
                    updated_at=?,finished_at=? WHERE id=?""",
                    (status, result_json, reason, now, now, job_id))
                self._event(conn, job_id, "finished", now, {"status": status, "reason": reason})
                finished = self._get(conn, job_id)
            else:
                finished = None
        if finished is None:
            raise LeaseLost("lease expired or superseded")
        return finished

    def interrupt(self, job_id, owner, token, *, reason):
        """Preserve a transient failure for explicit, cost-reconciled recovery."""
        reason = _text(reason, "interruption reason", 1000)
        now = self._now()
        with self._write() as conn:
            self._expire(conn, now)
            if self._leased(conn, job_id, owner, token, now) is None:
                result = None
            else:
                conn.execute("""UPDATE valuation_jobs SET status='interrupted',reason=?,
                    lease_owner=NULL,lease_token=NULL,lease_deadline=NULL,updated_at=? WHERE id=?""",
                    (reason, now, job_id))
                self._event(conn, job_id, "interrupted", now, {"reason": reason, "former_owner": owner})
                result = self._get(conn, job_id)
        if result is None:
            raise LeaseLost("lease expired or superseded")
        return result

    def retry_temporary(self, job_id, owner, token, *, reason, not_before, safe_to_retry):
        if safe_to_retry is not True:
            raise RetryDenied("trusted caller must explicitly mark safe_to_retry=True")
        reason = _text(reason, "retry reason", 1000)
        due = _when(not_before)
        now = self._now()
        denied = None
        with self._write() as conn:
            self._expire(conn, now)
            leased = self._leased(conn, job_id, owner, token, now)
            if leased is None:
                denied = LeaseLost("lease expired or superseded")
            elif leased["attempts"] >= leased["max_attempts"]:
                denied = RetryDenied("attempt limit reached")
            else:
                conn.execute("""UPDATE valuation_jobs SET status='queued',not_before=?,
                    lease_owner=NULL,lease_token=NULL,lease_deadline=NULL,
                    reason=?,updated_at=? WHERE id=?""", (due, reason, now, job_id))
                self._event(conn, job_id, "retry_scheduled", now,
                            {"reason": reason, "not_before": due})
                result = self._get(conn, job_id)
        if denied:
            raise denied
        return result

    def recover_interrupted(self, job_id, *, reason, cost_reconciliation,
                            safe_to_retry, not_before=None):
        if safe_to_retry is not True:
            raise RetryDenied("trusted caller must explicitly mark safe_to_retry=True")
        try:
            reason = _text(reason, "recovery reason", 1000)
        except ValueError as exc:
            raise RetryDenied("recovery reason required") from exc
        now = self._now()
        due = _when(not_before) if not_before is not None else now
        with self._write() as conn:
            self._expire(conn, now)
            row = conn.execute("SELECT status,attempts,max_attempts,kind FROM valuation_jobs WHERE id=?",
                               (job_id,)).fetchone()
            reconciliation = _reconciliation(cost_reconciliation, price_only=row is not None and row["kind"] == "reprice")
            if row is None or row["status"] != "interrupted":
                denied = RetryDenied("job is not interrupted")
            elif row["attempts"] >= row["max_attempts"]:
                denied = RetryDenied("attempt limit reached")
            else:
                denied = None
                conn.execute("""UPDATE valuation_jobs SET status='queued',not_before=?,
                    reason=?,updated_at=? WHERE id=?""", (due, reason, now, job_id))
                self._event(conn, job_id, "recovered", now,
                            {"reason": reason, "cost_reconciliation": reconciliation,
                             "not_before": due})
                result = self._get(conn, job_id)
        if denied:
            raise denied
        return result
