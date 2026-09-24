"""Opt-in application lifecycle and post-commit tracking notifications.

No import starts work. The process owns only a daemon thread, while SQLite owns
the global lease and durable queue. A missing policy does not open the database.
"""
from contextlib import contextmanager
from copy import deepcopy
import logging
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4

from .preparation_runtime import installation_runtime
from .valuation_price_events import reconcile_price_events
from .valuation_source_events import reconcile_source_events
from .publication_discovery import scan_publications
from .configured_filing_discovery import scan_configured_filings

log = logging.getLogger(__name__)


@contextmanager
def _read_db(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


def build_manager(runtime, db_path):
    """Reuse persistence methods without running MemoryDB's constructor/DDL."""
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.storage.valuation_jobs import ValuationJobStore
    from bellomberg.storage.valuation_versions import ValuationVersions
    from bellomberg.core.paths import MODELS_DIR
    from .valuation_automation import ValuationAutomation
    jobs = ValuationJobStore(db_path)

    class ExistingValuationDB(MemoryDB):
        def __init__(self):
            self.db_path = str(Path(db_path).resolve())

        @contextmanager
        def _conn(self):
            # mode=rw prevents a replacement empty database if storage vanishes.
            # Reuse the original thesis/snapshot code with a normal transaction.
            with jobs._connect() as conn:
                conn.isolation_level = "DEFERRED"
                try:
                    yield conn
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    versions = ValuationVersions(ExistingValuationDB(), roots=[runtime.output_dir, MODELS_DIR])
    # Validate the publication schema now, before claiming or starting a thread.
    from bellomberg.storage.valuation_versions import _require_schema
    with versions._conn(read_only=True) as conn:
        _require_schema(conn)
    return ValuationAutomation(runtime, jobs, versions)


def _error(exc):
    return {"status": "error", "reason": type(exc).__name__ + ": " + str(exc)[:500]}


def notify_tracking(db_path, ticker, trigger):
    """Never turn an already committed trade/favorite into a retryable HTTP error."""
    try:
        runtime = installation_runtime()
        state = runtime.status()
        if state["status"] == "disabled":
            return state
        if trigger not in state["triggers"]:
            return {"status": "disabled", "reason": "trigger_not_authorized"}
        if trigger not in ("portfolio", "watchlist"):
            raise ValueError("unsupported tracking notification")
        ticker = ticker.strip().upper()
        # A retroactive sale/purchase may have changed today's actual position.
        query = ("SELECT 1 FROM positions WHERE ticker=? AND quantita>0 AND is_active=1" if trigger == "portfolio" else
                 "SELECT 1 FROM favorite_companies WHERE ticker=?")
        with _read_db(db_path) as conn:
            if conn.execute(query, (ticker,)).fetchone() is None:
                return {"status": "not_tracked", "reason": "no_active_" + trigger}
        row = build_manager(runtime, db_path).enqueue_initial(ticker, trigger)
        return {key: row[key] for key in ("id", "status", "reason", "reused", "generation_id") if key in row}
    except Exception as exc:
        state = _error(exc)
        log.warning("Model notification failed after committed %s: %s", trigger, state["reason"])
        return state


def reconcile_tracking(manager):
    """Recover missing tracking events from SQLite; each channel declares gaps."""
    state = manager.runtime.status()
    if state["status"] == "disabled" or "startup" not in state["triggers"]:
        return {"status": "disabled", "reason": "startup_trigger_not_authorized", "outcomes": []}
    tickers, errors = set(), []
    with _read_db(manager.jobs.db_path) as conn:
        for source, query in (
            ("portfolio", "SELECT DISTINCT ticker FROM positions WHERE quantita>0 AND is_active=1"),
            ("watchlist", "SELECT DISTINCT ticker FROM favorite_companies"),
        ):
            try:
                for (ticker,) in conn.execute(query):
                    if not isinstance(ticker, str) or not ticker.strip():
                        raise ValueError("tracked row has no ticker")
                    tickers.add(ticker.strip().upper())
            except Exception as exc:
                errors.append({"source": source, **_error(exc)})
    outcomes = []
    for ticker in sorted(tickers):
        try:
            row = manager.enqueue_initial(ticker, "startup")
            outcomes.append({"ticker": ticker, **{key: row[key] for key in
                ("id", "status", "reason", "reused", "generation_id") if key in row}})
        except Exception as exc:
            outcomes.append({"ticker": ticker, **_error(exc)})
    return {"status": "partial" if errors or any(r["status"] == "error" for r in outcomes) else "ok",
            "sources": errors, "outcomes": outcomes}


class AutomationRunner:
    """One backend-owned worker; additional processes still share the SQL lease."""
    def __init__(self, manager, *, poll_seconds=5, reconcile_seconds=300, discovery_poll_seconds=60):
        if poll_seconds <= 0 or reconcile_seconds < poll_seconds or discovery_poll_seconds <= 0:
            raise ValueError("positive polling and reconciliation interval required")
        self.manager = manager
        self.poll_seconds, self.reconcile_seconds = poll_seconds, reconcile_seconds
        self.discovery_poll_seconds = discovery_poll_seconds
        self.owner = "backend-" + str(uuid4())
        self._stop, self._lock = Event(), Lock()
        self._thread = None
        self._discovery_thread = None
        self._state = {"status": "stopped"}

    def status(self):
        with self._lock:
            state = deepcopy(self._state)
        if self._stop.is_set():
            state['status'] = 'stopping' if any(t is not None and t.is_alive() for t in
                                               (self._thread, self._discovery_thread)) else 'stopped'
            return state
        if (self._thread is not None and self._thread.ident is not None
                and not self._thread.is_alive() and state["status"] not in ("stopped", "stopping")):
            state.update(status="error", reason="worker_thread_exited_unexpectedly")
        return state

    def _update(self, **values):
        with self._lock:
            self._state.update(values)

    def start(self):
        if self._thread is not None:
            raise RuntimeError("valuation worker already started")
        self._update(status="running")
        self._thread = Thread(target=self._run, daemon=True, name="valuation-automation")
        self._thread.start()
        self._discovery_thread = Thread(target=self._discover, daemon=True, name="valuation-source-discovery")
        self._discovery_thread.start()

    def _discover(self):
        while not self._stop.is_set():
            for name, scan in (('discovery', scan_publications), ('configured_filings', scan_configured_filings)):
                if self._stop.is_set():
                    break
                try:
                    self._update(**{name: scan(self.manager)})
                except Exception as exc:
                    self._update(**{name: _error(exc)})
                    log.exception("Valuation source scan failed: %s", name)
            self._stop.wait(self.discovery_poll_seconds)

    def _run(self):
        next_reconcile = 0
        while not self._stop.is_set():
            try:
                if monotonic() >= next_reconcile:
                    self._update(recovery=self.manager.recover(), sources=reconcile_source_events(self.manager),
                                 tracking=reconcile_tracking(self.manager),
                                 prices=reconcile_price_events(self.manager))
                    next_reconcile = monotonic() + self.reconcile_seconds
                if self._stop.is_set():
                    break
                job = self.manager.run_one(owner=self.owner)
                if job:
                    self._update(last_job={key: job[key] for key in ("id", "ticker", "kind", "status", "reason")})
            except Exception as exc:
                self._update(last_error=_error(exc))
                log.exception("Valuation background work failed")
            self._stop.wait(self.poll_seconds)
        self._update(status="stopped")

    def stop(self, timeout=2):
        self._stop.set()
        deadline = monotonic() + timeout
        for thread in (self._thread, self._discovery_thread):
            if thread is not None:
                thread.join(timeout=max(0, deadline - monotonic()))
            if thread is not None and thread.is_alive():
                # Do not release a live request's lease or permit a second paid
                # attempt. Its heartbeat/checkpoint survives until process exit.
                self._update(status="stopping", reason="active_work_or_source_scan_retains_lease_until_completion")
        return self.status()


def start_installation(db_path):
    """Called by backend lifespan only, never by a GET or a model mention."""
    try:
        runtime = installation_runtime()
        state = runtime.status()
        if state["status"] == "disabled":
            return {"runner": None, "state": state}
        runner = AutomationRunner(build_manager(runtime, db_path))
        runner.start()
        return {"runner": runner, "state": state}
    except Exception as exc:
        state = _error(exc)
        log.warning("Valuation worker unavailable: %s", state["reason"])
        return {"runner": None, "state": state}
