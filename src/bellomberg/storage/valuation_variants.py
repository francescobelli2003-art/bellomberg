"""Personal workbook copies with immutable lineage and no official-cell import."""
from hashlib import sha256
from pathlib import Path
import os
from uuid import UUID


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS valuation_variants (
      id TEXT PRIMARY KEY, ticker TEXT NOT NULL, source_generation TEXT NOT NULL,
      label TEXT NOT NULL, path TEXT NOT NULL UNIQUE, initial_sha256 TEXT NOT NULL,
      created_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','ready','blocked')),
      error TEXT, FOREIGN KEY(source_generation) REFERENCES valuation_publications(generation_id))""",
    """CREATE TRIGGER IF NOT EXISTS valuation_variant_origin_immutable
      BEFORE UPDATE OF id,ticker,source_generation,label,path,initial_sha256,created_at ON valuation_variants
      BEGIN SELECT RAISE(ABORT,'variant origin immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS valuation_variant_no_delete BEFORE DELETE ON valuation_variants
      BEGIN SELECT RAISE(ABORT,'variant history immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS valuation_variant_no_replace BEFORE INSERT ON valuation_variants
      WHEN EXISTS(SELECT 1 FROM valuation_variants WHERE id=NEW.id OR path=NEW.path)
      BEGIN SELECT RAISE(ABORT,'variant origin immutable'); END""",
)


def ensure_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)


class PersonalVariants:
    def __init__(self, versions, root):
        self.versions, self.root = versions, Path(root).resolve()

    def get(self, variant_id):
        with self.versions._conn(read_only=True) as conn:
            row = conn.execute("SELECT * FROM valuation_variants WHERE id=?", (variant_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        path = Path(row["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("personal variant outside configured root")
        try:
            digest = sha256(path.read_bytes()).hexdigest()
            result.update(available=row["status"] == "ready", modified=digest != row["initial_sha256"],
                          current_sha256=digest)
        except OSError as exc:
            result.update(available=False, modified=None, read_error=type(exc).__name__ + ": " + str(exc))
        return result

    def create(self, ticker, label, *, request_id, expected_generation=None):
        """Reserve lineage first; write exclusively; recover without overwrites.

        Clients retain request_id to recover after a timeout. A partially written
        or edited pending file is blocked and preserved for inspection. Once
        ready, personal edits are permitted and never copied into the model.
        """
        if not isinstance(label, str) or not label.strip() or len(label) > 120:
            raise ValueError("personal variant label required, up to 120 characters")
        if not isinstance(request_id, str):
            raise ValueError("client request_id UUID required for recoverable personal copies")
        variant_id = str(UUID(request_id))
        with self.versions._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM valuation_variants WHERE id=?", (variant_id,)).fetchone()
            if existing:
                if existing["ticker"] != ticker or existing["label"] != label:
                    raise ValueError("variant request identity mismatch")
                if expected_generation is not None and existing["source_generation"] != expected_generation:
                    raise ValueError("variant source generation differs from original request")
            else:
                view = self.versions.current(ticker)
                if not view or not view["artifact"]["available"]:
                    raise ValueError("verified current workbook required for a personal copy")
                payload = view["current"]
                if expected_generation is not None and payload["generation_id"] != expected_generation:
                    raise ValueError("current generation changed before personal copy")
                target = self.root / ("PERSONAL_" + variant_id + ".xlsx")
                conn.execute("INSERT INTO valuation_variants VALUES(?,?,?,?,?,?,?,'pending',NULL)",
                    (variant_id, ticker, payload["generation_id"], label, str(target), payload["workbook_sha256"],
                     self.versions.clock().isoformat()))
                if target.exists():
                    conn.execute("UPDATE valuation_variants SET status='blocked',error=? WHERE id=?",
                                 ("Target already existed before reservation; preserved without adoption", variant_id))
        # Serializing the short copy step prevents another recovery from
        # inspecting an in-progress write and misclassifying it as a partial file.
        with self.versions._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM valuation_variants WHERE id=?", (variant_id,)).fetchone()
            if existing["status"] == "pending":
                target = Path(existing["path"]).resolve()
                if not target.is_relative_to(self.root):
                    raise ValueError("personal variant outside configured root")
                try:
                    if not target.exists():
                        row = conn.execute("SELECT snapshot_id FROM valuation_publications WHERE generation_id=?",
                                           (existing["source_generation"],)).fetchone()
                        payload = self.versions._payload(conn, row[0], existing["source_generation"])
                        if not self.versions._artifact(conn, payload)["available"]:
                            raise ValueError("source workbook no longer matches its registered generation")
                        raw = Path(payload["path"]).read_bytes()
                        if sha256(raw).hexdigest() != existing["initial_sha256"]:
                            raise ValueError("source workbook changed while copying")
                        self.root.mkdir(parents=True, exist_ok=True)
                        with target.open("xb") as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                    # A crash after fsync and before the DB update needs no second copy.
                    matches = sha256(target.read_bytes()).hexdigest() == existing["initial_sha256"]
                    status, error = ("ready", None) if matches else (
                        "blocked", "Pending personal file differs from the original; preserved without overwrite")
                except (OSError, ValueError) as exc:
                    status, error = "pending", type(exc).__name__ + ": " + str(exc)
                conn.execute("UPDATE valuation_variants SET status=?,error=? WHERE id=? AND status='pending'",
                             (status, error, variant_id))
        return self.get(variant_id)
