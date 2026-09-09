"""Resumable in-memory option downloads. No portfolio/DB access or trading."""
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock, Semaphore, Thread
from time import monotonic
from uuid import uuid4

from bellomberg.portfolio import vol_surface as vol


def _now():
    return datetime.now(timezone.utc).isoformat()


class OptionsDownloadManager:
    """A serial worker per job; bounded workers, unlimited dates/contracts.

    Pause takes effect after the current provider request; its response is kept.
    Failed pages keep their cursor and require an explicit resume (also on 429).
    Snapshots are fresh for 120s and retained for one hour of inactivity. They
    live only in this backend process; a restart requires a new download.
    """

    def __init__(self, max_concurrent=3, max_jobs=32):
        self._lock = RLock()
        self._slots = Semaphore(max_concurrent)
        self._jobs = {}
        self._max_jobs = max_jobs
        self.cache_ttl_seconds = vol.CHAIN_CACHE_SECONDS
        self.retention_seconds = 3600

    def _job(self, job_id):
        job = self._jobs.get(job_id)
        if job is not None and not job["_worker"] and monotonic() - job["_access"] > self.retention_seconds:
            del self._jobs[job_id]
            job = None
        if job is None:
            raise KeyError("download assente o scaduto: avvia un nuovo download")
        job["_access"] = monotonic()
        return job

    def _status(self, job):
        rows = [{"expiry": expiry, "n_contracts": len(row["data"]), "complete": row["complete"],
                 "error": row["error"], "duplicates": sum(p["duplicates"] for p in row["pages"].values()),
                 "malformed_contracts": sum(p["malformed"] for p in row["pages"].values()),
                 "superseded_contracts": row["superseded_contracts"], "page_revisions": len(row["revisions"])}
                for expiry, row in job["_rows"].items()]
        out = {key: deepcopy(value) for key, value in job.items() if not key.startswith("_")}
        out.update(rows=rows, n_contracts=sum(row["n_contracts"] for row in rows),
                   completed_expiries=sum(r["complete"] for r in rows),
                   completed_dates=[r["expiry"] for r in rows if r["complete"]],
                   duplicates=sum(r["duplicates"] for r in rows),
                   malformed_contracts=sum(r["malformed_contracts"] for r in rows),
                   superseded_contracts=sum(r["superseded_contracts"] for r in rows),
                   page_revisions=sum(r["page_revisions"] for r in rows),
                   pages_received=sum(len(row["pages"]) for row in job["_rows"].values()),
                   cache_ttl_seconds=self.cache_ttl_seconds, retention_seconds=self.retention_seconds,
                   stale=monotonic() - job["_created"] > self.cache_ttl_seconds,
                   pause_requested=job["_pause"])
        return out

    def status(self, job_id):
        with self._lock:
            return self._status(self._job(job_id))

    def _add_expiry(self, job, expiry):
        if expiry not in job["_rows"]:
            job["expirations"].append(expiry)
            job["_rows"][expiry] = {"data": {}, "cursor": None, "seen": set(),
                                     "complete": False, "error": None, "pages": {},
                                     "revisions": [], "superseded_contracts": 0}

    def start(self, ticker, expiries=None):
        ticker = vol._symbol(ticker)
        if expiries is not None:
            if not isinstance(expiries, list) or not expiries:
                raise ValueError("expiries deve contenere almeno una scadenza")
            for expiry in expiries:
                vol._expiry(expiry)
            if len(set(expiries)) != len(expiries):
                raise ValueError("le scadenze devono essere distinte")
            expiries = sorted(expiries)
        key = (ticker, tuple(expiries) if expiries is not None else None)
        with self._lock:
            now = monotonic()
            expired = [jid for jid, job in self._jobs.items()
                       if not job["_worker"] and now - job["_access"] > self.retention_seconds]
            for jid in expired:
                del self._jobs[jid]
            for job in self._jobs.values():
                if job["_key"] == key and job["state"] == "complete" and now - job["_created"] < self.cache_ttl_seconds:
                    job["_access"] = now
                    return {**self._status(job), "cached": True}
            if len(self._jobs) >= self._max_jobs:
                raise RuntimeError("troppi download conservati: riprendi un download esistente o attendi la scadenza")
            job = {"id": uuid4().hex, "ticker": ticker, "scope": "all" if expiries is None else "selected",
                   "state": "queued", "phase": "catalog" if expiries is None else "chain",
                   "expirations": [], "catalog_complete": expiries is not None,
                   "current_expiry": None, "download_complete": False, "error": None, "retryable": False,
                   "started_at": _now(), "updated_at": _now(), "snapshot_at": None,
                   "spot": None, "spot_source": None, "spot_error": None, "spot_timestamp": None,
                   "spot_timeframe": None, "spot_timestamp_ns": None,
                   "_rows": {}, "_catalog_after": None, "_key": key, "_worker": False,
                   "_pause": False, "_restart": False, "_created": now, "_access": now, "_spot_attempted": False}
            for expiry in expiries or []:
                self._add_expiry(job, expiry)
            self._jobs[job["id"]] = job
            self._launch(job)
            return self._status(job)

    def _launch(self, job):
        if job["_worker"]:
            return
        job.update(state="queued", error=None, retryable=False, updated_at=_now())
        job["_worker"] = True
        Thread(target=self._run, args=(job,), daemon=True, name=f"options-{job['id'][:8]}").start()

    def pause(self, job_id):
        with self._lock:
            job = self._job(job_id)
            if job["state"] != "complete":
                job["_pause"] = True
                job["_restart"] = False
                if not job["_worker"]:
                    job["state"] = "paused"
                job["updated_at"] = _now()
            return self._status(job)

    def resume(self, job_id):
        with self._lock:
            job = self._job(job_id)
            job["_pause"] = False
            if job["state"] != "complete":
                if job["_worker"] and job["state"] == "error":
                    job["_restart"] = True
                    job.update(state="queued", error=None, retryable=False)
                else:
                    self._launch(job)
            return self._status(job)

    def _fail(self, job, error):
        job.update(state="error", error=str(error), retryable=True, updated_at=_now(), download_complete=False)
        if job["current_expiry"] is not None:
            row = job["_rows"][job["current_expiry"]]
            if row["error"] is None:
                row["error"] = str(error)

    def _run(self, job):
        acquired = False
        try:
            while not acquired:
                with self._lock:
                    if job["_pause"]:
                        return
                acquired = self._slots.acquire(timeout=.1)
            with self._lock:
                if job["_pause"]:
                    return
                job.update(state="running", updated_at=_now())
            self._observe_spot(job)
            while True:
                with self._lock:
                    if job["_pause"]:
                        return
                    catalog = not job["catalog_complete"]
                    after = job["_catalog_after"]
                    pending = next(((e, r) for e, r in job["_rows"].items() if not r["complete"]), None)
                    if not catalog and pending:
                        expiry, row = pending
                        cursor = row["cursor"]
                        job.update(phase="chain", current_expiry=expiry)
                if catalog:
                    page = vol.get_expiry_catalog(job["ticker"], after=after, request_budget=1)
                    with self._lock:
                        for expiry in page["expirations"]:
                            self._add_expiry(job, expiry)
                        job["_catalog_after"] = page["next_after"]
                        job["catalog_complete"] = page["complete"]
                        job["updated_at"] = _now()
                        if page.get("error"):
                            self._fail(job, page["error"])
                            return
                    continue
                if pending is None:
                    with self._lock:
                        if job["_pause"]:
                            return
                        job.update(state="complete", phase="done", current_expiry=None,
                                   download_complete=True, snapshot_at=_now(), updated_at=_now(), error=None)
                    return
                page = vol.get_chain_detail(job["ticker"], expiry, cursor)
                with self._lock:
                    self._accept_page(job, row, cursor, page)
                    if row["error"]:
                        self._fail(job, f"{expiry}: {row['error']}")
                        return
        except Exception as exc:
            with self._lock:
                # Avoid exposing provider exception URLs/credentials in status.
                self._fail(job, f"download interrotto ({type(exc).__name__}); riprendi per ritentare")
        finally:
            if acquired:
                self._slots.release()
            with self._lock:
                job["_worker"] = False
                if job["_pause"] and job["state"] != "complete":
                    job["state"] = "paused"
                job["updated_at"] = _now()
                # Resume may arrive after the worker observed a pause but before
                # this finalizer. A runnable state must retain an actual worker.
                if not job["_pause"] and (job["_restart"] or job["state"] in ("running", "queued")):
                    job["_restart"] = False
                    self._launch(job)

    def _accept_page(self, job, row, cursor, page):
        if page.get("_timestamp"):
            received = datetime.fromisoformat(page["_timestamp"])
            age = max(0, (datetime.now(timezone.utc) - received).total_seconds())
            job["_created"] = min(job["_created"], monotonic() - age)
        if page.get("chain") or page.get("complete") or page.get("malformed_contracts"):
            contracts = page.get("chain") or []
            current = {contract["contract"]: contract for contract in contracts}
            previous = row["pages"].get(cursor)
            if previous is not None:
                # Preserve the superseded receipt outside the authoritative
                # dataset. A repaired cursor replaces its entire contribution.
                row["revisions"].append({"cursor": cursor, **deepcopy(previous)})
            old_keys = set(row["data"])
            row["pages"][cursor] = {"data": current, "within_duplicates": len(contracts) - len(current),
                                    "duplicates": 0, "malformed": page.get("malformed_contracts", 0),
                                    "error": page.get("error"), "received_at": page.get("_timestamp")}
            merged = {}
            for receipt in row["pages"].values():
                receipt["duplicates"] = receipt["within_duplicates"] + len(merged.keys() & receipt["data"].keys())
                merged.update(receipt["data"])
            row["data"] = merged
            if previous is not None:
                row["superseded_contracts"] += len(old_keys - merged.keys())
        if job["spot"] is None and vol._finite(page.get("spot"), positive=True) is not None:
            job.update(spot=page["spot"], spot_source="Polygon underlying snapshot",
                       spot_timestamp=page.get("_timestamp"), spot_timeframe=page.get("spot_timeframe"),
                       spot_timestamp_ns=page.get("spot_timestamp_ns"), spot_error=None)
        error = page.get("error")
        nxt = page.get("next_cursor")
        if not error and not page.get("complete") and (not nxt or nxt == cursor or nxt in row["seen"]):
            error = "ciclo o cursore chain non avanzante"
            vol._forget_chain_page(job["ticker"], job["current_expiry"], cursor)
        row["error"] = error
        if not error:
            row["seen"].add(cursor)
            row["complete"] = bool(page.get("complete"))
            row["cursor"] = nxt
        job["updated_at"] = _now()

    def _observe_spot(self, job):
        with self._lock:
            if job["spot"] is not None or job["_spot_attempted"] or job["_pause"]:
                return
            job["_spot_attempted"] = True
        try:
            import yfinance as yf
            observed = yf.Ticker(job["ticker"]).fast_info["lastPrice"]
            spot = vol._finite(observed, positive=True)
            if spot is None:
                raise ValueError("prezzo assente o non finito")
            with self._lock:
                job.update(spot=spot, spot_source="yfinance lastPrice", spot_timestamp=_now())
        except Exception as exc:
            with self._lock:
                job["spot_error"] = f"spot yfinance assente ({type(exc).__name__}); serve un prezzo osservato nelle pagine Polygon"

    def chain(self, job_id, expiry, offset=0, limit=250, side="all", strike=""):
        vol._expiry(expiry)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset non valido")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit deve essere da 1 a 1000")
        if side not in ("all", "call", "put") or not isinstance(strike, str) or len(strike) > 80:
            raise ValueError("filtro chain non valido")
        strike = strike.strip().replace(",", ".")
        with self._lock:
            job = self._job(job_id)
            row = job["_rows"].get(expiry)
            if row is None:
                raise ValueError("scadenza non presente nel download")
            selected = [c for c in row["data"].values() if (side == "all" or c["type"] == side)
                        and (not strike or strike in str(c["strike"]))]
            selected.sort(key=lambda c: (c["strike"], c["type"], c["contract"]))
            end = offset + limit
            return {"id": job_id, "ticker": job["ticker"], "expiry": expiry,
                    "chain": deepcopy(selected[offset:end]), "offset": offset, "limit": limit,
                    "n_contracts": len(row["data"]), "filtered_contracts": len(selected),
                    "chain_complete": row["complete"], "download_complete": job["download_complete"],
                    "has_more": end < len(selected), "next_offset": end if end < len(selected) else None,
                    "spot": job["spot"], "spot_source": job["spot_source"], "spot_timeframe": job["spot_timeframe"],
                    "spot_timestamp_ns": job["spot_timestamp_ns"], "spot_timestamp": job["spot_timestamp"],
                    "snapshot_at": job["snapshot_at"], "cache_ttl_seconds": self.cache_ttl_seconds,
                    "stale": monotonic() - job["_created"] > self.cache_ttl_seconds, "error": row["error"],
                    "duplicates": sum(p["duplicates"] for p in row["pages"].values()),
                    "malformed_contracts": sum(p["malformed"] for p in row["pages"].values()),
                    "superseded_contracts": row["superseded_contracts"], "page_revisions": len(row["revisions"]),
                    "_timestamp": job["updated_at"], "_source": "Polygon option-chain snapshot (memoria)"}

    def surface(self, job_id):
        with self._lock:
            job = self._job(job_id)
            if not job["expirations"]:
                return {"error": "nessuna scadenza disponibile nel download", "ticker": job["ticker"],
                        "slices": [], "term_structure": [], "n_expiries": 0, "moneyness_grid": vol.MONEYNESS_GRID,
                        "coverage": {"complete": False, "download_complete": job["download_complete"], "rows": [],
                                     "requested": [], "loaded": [], "excluded": [], "errors": [],
                                     "selection_mode": "explicit", "catalog_note": "Catalogo del download in memoria"}}
            snapshot = {"spot": job["spot"], "spot_source": job["spot_source"],
                        "download_complete": job["download_complete"], "chains": {
                            expiry: {"chain": deepcopy(list(row["data"].values())), "complete": row["complete"],
                                     "continuation_error": row["error"]}
                            for expiry, row in job["_rows"].items()}}
            ticker, expiries, stamp = job["ticker"], list(job["expirations"]), job["snapshot_at"] or job["updated_at"]
        out = vol.build_vol_surface(ticker, expiries=expiries, include_context=False, _snapshot=snapshot)
        out.update(download_id=job_id, snapshot_at=stamp, _timestamp=stamp)
        return out


downloads = OptionsDownloadManager()
