"""Private discovery journal. No portfolio migration, network or paid work.

Only an explicitly configured caller opens this separate database. A global
lease bounds concurrent scans; pending deliveries survive the enqueue window.
"""
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class DiscoveryStore:
    def __init__(self, path, clock):
        self.path, self.clock = Path(path).resolve(), clock

    def _now(self):
        from datetime import timezone
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("discovery clock must be timezone aware")
        return now.astimezone(timezone.utc)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise ValueError("unsupported discovery journal version")
                if version == 0:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                        raise ValueError("nonempty database is not a discovery journal")
                    db.execute("CREATE TABLE scans(ticker TEXT PRIMARY KEY, next_due TEXT NOT NULL, initialized INTEGER NOT NULL DEFAULT 0, token TEXT, deadline TEXT, result_json TEXT)")
                    db.execute("CREATE UNIQUE INDEX single_scan ON scans((1)) WHERE token IS NOT NULL")
                    db.execute("CREATE TABLE seen(ticker TEXT NOT NULL, identity TEXT NOT NULL, body_json TEXT NOT NULL, PRIMARY KEY(ticker,identity))")
                    db.execute("CREATE TABLE pending(identity TEXT PRIMARY KEY, ticker TEXT NOT NULL, trigger TEXT NOT NULL, body_json TEXT NOT NULL, job_id INTEGER)")
                    db.execute("PRAGMA user_version=1")
                yield db
        finally:
            db.close()

    def _guard(self, db, claim):
        if not db.execute("SELECT 1 FROM scans WHERE ticker=? AND token=? AND deadline>?",
                          (claim['ticker'], claim['token'], self._now().isoformat())).fetchone():
            raise RuntimeError("discovery lease lost")

    def claim(self, tickers):
        now = self._now()
        with self._db() as db:
            db.execute("UPDATE scans SET token=NULL,deadline=NULL WHERE deadline<=?", (now.isoformat(),))
            if db.execute("SELECT 1 FROM scans WHERE token IS NOT NULL").fetchone():
                return None
            for ticker in tickers:
                db.execute("INSERT OR IGNORE INTO scans(ticker,next_due) VALUES(?,?)", (ticker, now.isoformat()))
            rows = db.execute("SELECT * FROM scans WHERE next_due<=? ORDER BY next_due,ticker", (now.isoformat(),))
            row = next((dict(r) for r in rows if r['ticker'] in tickers), None)
            if row is None:
                return None
            row['token'] = str(uuid4())
            db.execute("UPDATE scans SET token=?,deadline=? WHERE ticker=?",
                       (row['token'], (now + timedelta(minutes=5)).isoformat(), row['ticker']))
            return row

    def observe(self, claim, entries, initial_new):
        """Persist the event before delivery, never lose it after a crash."""
        with self._db() as db:
            self._guard(db, claim)
            seen = {r[0] for r in db.execute("SELECT identity FROM seen WHERE ticker=?", (claim['ticker'],))}
            new = [e for e in entries if e['identity'] not in seen
                   and (claim['initialized'] or e['identity'] in initial_new)]
            if new:
                body = encoded(sorted(new, key=lambda e: e['identity']))
                key = sha256((claim['ticker'] + ':' + body).encode()).hexdigest()
                trigger = 'filing_diff' if any(e['trigger'] == 'filing_diff' for e in new) else 'guidance'
                db.execute("INSERT OR IGNORE INTO pending(identity,ticker,trigger,body_json) VALUES(?,?,?,?)",
                           (key, claim['ticker'], trigger, body))
            for entry in entries:
                db.execute("INSERT OR IGNORE INTO seen VALUES(?,?,?)", (claim['ticker'], entry['identity'], encoded(entry)))
            db.execute("UPDATE scans SET initialized=1 WHERE ticker=?", (claim['ticker'],))
        return len(new)

    def pending(self, claim):
        with self._db() as db:
            self._guard(db, claim)
            return [dict(r) for r in db.execute("SELECT * FROM pending WHERE ticker=? AND job_id IS NULL ORDER BY rowid", (claim['ticker'],))]

    def delivered(self, claim, key, job_id):
        if type(job_id) is not int or job_id < 1:
            raise ValueError("persisted valuation job ID required")
        with self._db() as db:
            self._guard(db, claim)
            db.execute("UPDATE pending SET job_id=? WHERE identity=? AND ticker=? AND job_id IS NULL", (job_id, key, claim['ticker']))

    def finish(self, claim, result):
        delay = timedelta(hours=6 if result['status'] in ('ok', 'partial') else 1)
        with self._db() as db:
            self._guard(db, claim)
            db.execute("UPDATE scans SET next_due=?,token=NULL,deadline=NULL,result_json=? WHERE ticker=?",
                       ((self._now() + delay).isoformat(), encoded(result), claim['ticker']))
