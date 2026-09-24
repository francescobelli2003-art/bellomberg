"""Explicit current pointers over the existing immutable valuation snapshots.

Files and snapshots must already exist. Failed attempts remain visible while the
last valid pointer survives. This module neither generates nor overwrites files,
and installing its schema is an explicit migration step.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


TABLES = ("valuation_model_heads", "valuation_publications")
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS valuation_model_heads (
      ticker TEXT PRIMARY KEY, model_id TEXT NOT NULL UNIQUE,
      current_generation TEXT, latest_attempt TEXT, locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
      FOREIGN KEY(current_generation) REFERENCES valuation_publications(generation_id),
      FOREIGN KEY(latest_attempt) REFERENCES valuation_publications(generation_id))""",
    """CREATE TABLE IF NOT EXISTS valuation_publications (
      generation_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, ticker TEXT NOT NULL,
      revision INTEGER NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
      origin TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(ticker,revision),
      FOREIGN KEY(ticker) REFERENCES valuation_model_heads(ticker),
      FOREIGN KEY(snapshot_id,generation_id) REFERENCES valuation_snapshots(snapshot_id,generation_id))""",
    "CREATE TRIGGER IF NOT EXISTS valuation_publications_no_update BEFORE UPDATE ON valuation_publications BEGIN SELECT RAISE(ABORT,'publication immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS valuation_publications_no_delete BEFORE DELETE ON valuation_publications BEGIN SELECT RAISE(ABORT,'publication immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS valuation_publications_no_replace BEFORE INSERT ON valuation_publications WHEN EXISTS(SELECT 1 FROM valuation_publications WHERE generation_id=NEW.generation_id) BEGIN SELECT RAISE(ABORT,'publication immutable'); END",
)


def ensure_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)


def _require_schema(conn):
    from .sqlite_checks import schema_fingerprint
    with sqlite3.connect(":memory:") as reference:
        for statement in SCHEMA:
            reference.execute(statement)
        expected = schema_fingerprint(reference)
    actual = schema_fingerprint(conn)
    if any(actual.get(key) != digest for key, digest in expected.items()):
        raise RuntimeError("valuation publication schema missing or incompatible: explicit migration required")


class ValuationVersions:
    def __init__(self, db, *, roots, clock=None):
        self.db = db
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.roots = [Path(root).resolve() for root in roots]
        if not self.roots:
            raise ValueError("model roots required")

    @contextmanager
    def _conn(self, *, read_only=False):
        # mode=rw also closes the exists/connect race: a vanished DB is never
        # recreated. Reads cannot mutate schema or PRAGMAs on disk.
        path = Path(self.db.db_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"valuation DB absent: {path}")
        conn = sqlite3.connect(path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"),
                               uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _payload(conn, snapshot_id, generation_id):
        row = conn.execute("SELECT payload_json,payload_sha256 FROM valuation_snapshots WHERE snapshot_id=? AND generation_id=?",
                           (snapshot_id, generation_id)).fetchone()
        if not row:
            raise ValueError("snapshot non registrato")
        if sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
            raise ValueError("snapshot integrity mismatch")
        return json.loads(row[0])

    def _artifact(self, conn, payload):
        from bellomberg.reporting.valuation_delivery import build_manifest
        link = conn.execute("SELECT thesis_id FROM valuation_snapshot_links WHERE snapshot_id=? AND generation_id=? AND thesis_id IS NOT NULL",
                            (payload["snapshot_id"], payload["generation_id"])).fetchone()
        marked = {**payload, "_thesis_saved": {"thesis_id": link[0] if link else None}}
        manifest = build_manifest({payload["ticker"]: marked}, roots=self.roots)
        row = manifest["valuations"][0]
        return {"available": row["status"] == "ready", "status": row["status"], "reason": row["reason"]}

    @staticmethod
    def _active_approval(conn, payload, cutoff):
        from .method_records_store import leggi_approvati
        case = payload["acquisition_snapshot"]["case"]
        data = ((case.get("sources") or {}).get("method_inputs") or {}).get("data") or {}
        chosen = (data.get("sets") or {}).get(data.get("method_id")) or {}
        return next((row for row in leggi_approvati(conn, payload["ticker"], cutoff)
                     if row["id"] == chosen.get("set_id")
                     and row["earliest_valid_until"] >= cutoff), None)

    @classmethod
    def _origin(cls, conn, payload):
        bundle = payload.get("acquisition_snapshot") or {}
        case = bundle.get("case") or {}
        source = (case.get("sources") or {}).get("method_inputs") or {}
        if source.get("source_id") == "method_records_archive" and source.get("status") == "ok":
            data = source.get("data") or {}
            chosen = (data.get("sets") or {}).get(data.get("method_id")) or {}
            real = cls._active_approval(conn, payload, case["as_of"])
            consumed = [{k: v for k, v in row.items() if k not in ("provider", "validation_issues")}
                        for row in case.get("records", [])]
            if (real is None or real["records"] != chosen.get("records")
                    or real["records"] != source.get("records") or real["records"] != consumed
                    or real["method_id"] != (bundle.get("decision") or {}).get("method_id")
                    or real["method_version"] != chosen.get("method_version")
                    or real["method_version"] != (bundle.get("decision") or {}).get("method_version")
                    or real["scenario_rationale"] != (bundle.get("analysis_context") or {}).get("scenario_rationale")):
                raise ValueError("approvazione non attestata nell'archivio PM")
            # This proves the archived lineage at acquisition time. Whether that
            # approval still permits publication is a separate, dated gate.
            return "approved"
        if (payload.get("preparation") or {}).get("provenance", {}).get("origin") == "automatic_non_approved":
            return "automatic_non_approved"
        return "explicit_non_approved"

    @classmethod
    def _publication_issues(cls, conn, payload, origin, publication_day):
        from bellomberg.valuation.dcf_quality import _date
        case = payload["acquisition_snapshot"]["case"]
        day = _date(publication_day)
        expired = [row.get("driver", "?") for row in case.get("records", [])
                   if _date(row.get("valid_until")) is None or _date(row["valid_until"]) < day]
        issues = (["Input documentati scaduti alla pubblicazione: "
                   + ", ".join(sorted(set(expired)))] if expired else [])
        if _date(case.get("as_of")) is None or _date(case["as_of"]) > day:
            issues.append("Cutoff delle fonti successivo alla pubblicazione")
        if origin == "approved" and cls._active_approval(conn, payload, publication_day) is None:
            issues.append("Approvazione PM non piu' attiva alla pubblicazione")
        return issues

    def publish(self, snapshot_id, generation_id, *, expected_current_generation, guard=None):
        """Idempotent compare-and-swap after registration and artifact validation.

        `guard(conn)` lets the worker attest its lease in this same transaction.
        Approval is checked against actual archived PM reviews, never a caller flag.
        """
        from bellomberg.valuation.dcf_quality import assess_valuation_usability
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _require_schema(conn)
            if guard is not None:
                guard(conn)
            existing = conn.execute("SELECT * FROM valuation_publications WHERE generation_id=?", (generation_id,)).fetchone()
            if existing:
                if existing["snapshot_id"] != snapshot_id:
                    raise ValueError("generation/snapshot identity mismatch")
                return dict(existing)
            payload = self._payload(conn, snapshot_id, generation_id)
            ticker = payload["ticker"]
            conn.execute("INSERT OR IGNORE INTO valuation_model_heads(ticker,model_id) VALUES(?,?)", (ticker, str(uuid4())))
            head = conn.execute("SELECT * FROM valuation_model_heads WHERE ticker=?", (ticker,)).fetchone()
            check = assess_valuation_usability(payload)
            origin = self._origin(conn, payload)
            artifact = self._artifact(conn, payload) if check["usable"] else None
            if guard is not None:
                guard(conn)  # Hashing an artifact can outlive the original lease.
            # Use the time after artifact hashing and lease validation. Neither
            # an expired input nor a withdrawn approval may acquire the pointer.
            publication_time = self.clock()
            publication_day = publication_time.date().isoformat()
            status, reason = "incomplete", "; ".join(check["reasons"])
            if check["usable"]:
                issues = self._publication_issues(conn, payload, origin, publication_day)
                if issues:
                    reason = "; ".join(issues)
                else:
                    status, reason = "artifact_failed", artifact["reason"]
                if not issues and artifact["available"]:
                    if head["current_generation"] != expected_current_generation:
                        status, reason = "superseded", "Versione corrente cambiata durante il lavoro; candidato conservato"
                    elif head["locked"]:
                        status, reason = "held", "Modello bloccato dall'utente; candidato conservato separatamente"
                    else:
                        current = conn.execute("SELECT origin,snapshot_id,generation_id FROM valuation_publications WHERE generation_id=?",
                                               (head["current_generation"],)).fetchone()
                        protected = (current and current["origin"] == "approved" and
                                     self._active_approval(conn, self._payload(conn, current["snapshot_id"],
                                                                             current["generation_id"]), publication_day))
                        if protected and origin != "approved":
                            status, reason = "held", "La versione approvata conserva precedenza sul candidato automatico"
                        else:
                            status, reason = "published", "Snapshot registrato e workbook verificato"
            revision = conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM valuation_publications WHERE ticker=?", (ticker,)).fetchone()[0]
            conn.execute("INSERT INTO valuation_publications VALUES(?,?,?,?,?,?,?,?)",
                (generation_id, snapshot_id, ticker, revision, status, reason, origin, publication_time.isoformat()))
            conn.execute("UPDATE valuation_model_heads SET latest_attempt=? WHERE ticker=?", (generation_id, ticker))
            if status == "published":
                conn.execute("UPDATE valuation_model_heads SET current_generation=? WHERE ticker=?", (generation_id, ticker))
            return dict(conn.execute("SELECT * FROM valuation_publications WHERE generation_id=?", (generation_id,)).fetchone())

    def current(self, ticker):
        with self._conn(read_only=True) as conn:
            _require_schema(conn)
            head = conn.execute("SELECT * FROM valuation_model_heads WHERE ticker=?", (ticker,)).fetchone()
            if head is None:
                return None
            result = {**dict(head), "current": None, "latest_attempt": None,
                      "artifact": {"available": False, "reason": "Nessuna versione pubblicata"}}
            if head["latest_attempt"]:
                result["latest_attempt"] = dict(conn.execute("SELECT * FROM valuation_publications WHERE generation_id=?",
                                                            (head["latest_attempt"],)).fetchone())
            if head["current_generation"]:
                row = conn.execute("SELECT * FROM valuation_publications WHERE generation_id=?", (head["current_generation"],)).fetchone()
                result["current"] = self._payload(conn, row["snapshot_id"], row["generation_id"])
                result["current_revision"] = row["revision"]
                result["current_published_at"] = row["created_at"]
                result["artifact"] = self._artifact(conn, result["current"])
                result["approval"] = {"publication_origin": row["origin"], "active": bool(
                    row["origin"] == "approved" and self._active_approval(
                        conn, result["current"], self.clock().date().isoformat()))}
            return result

    def set_locked(self, ticker, locked, *, expected_current_generation=None):
        if type(locked) is not bool:
            raise ValueError("explicit boolean lock required")
        with self._conn() as conn:
            _require_schema(conn)
            if expected_current_generation is None:
                row = conn.execute("UPDATE valuation_model_heads SET locked=? WHERE ticker=?", (int(locked), ticker))
            else:
                row = conn.execute("UPDATE valuation_model_heads SET locked=? WHERE ticker=? AND current_generation=?",
                                   (int(locked), ticker, expected_current_generation))
            if row.rowcount != 1:
                raise ValueError("model not registered or current generation changed")
