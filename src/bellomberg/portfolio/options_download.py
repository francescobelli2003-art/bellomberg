"""Resumable in-memory option downloads. No portfolio/DB access or trading."""
from bellomberg.core.presentation import message as _ui_text, render_payload
from bellomberg.core.language import capture_language, scoped_language
from copy import deepcopy
from datetime import datetime, timezone
import logging
from threading import RLock, Semaphore, Thread
from time import monotonic
from uuid import uuid4

from bellomberg.portfolio import vol_surface as vol

vol_log = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat()


class OptionsDownloadManager:
    """A serial worker per job; bounded workers, unlimited dates/contracts.

    Pause takes effect after the current provider request; its response is kept.
    Failed pages keep their cursor and require an explicit resume (also on 429).
    Snapshots are fresh for 120s and retained for one hour of inactivity. They
    live only in this backend process; a restart requires a new download. Since
    10/10 (Opus 5.5) every COMPLETED download is also archived to file
    (`options_snapshots`, under the data dir) before the job reads «complete»;
    the outcome (saved/duplicate/skipped/error) is the job's `archive` field.
    Paused or failed downloads are not archived.
    """

    def __init__(self, max_concurrent=3, max_jobs=32, archive_root=None):
        # 10/10 (Opus 5.5): archivio su file delle istantanee complete (options_snapshots);
        # None = <data dir>/options_snapshots risolta a ogni salvataggio.
        self.archive_root = archive_root
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
            raise KeyError(_ui_text('download assente o scaduto: avvia un nuovo download', 'Download missing or expired: start a new download'))
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
                raise ValueError(_ui_text('expiries deve contenere almeno una scadenza', 'expiries must contain at least one expiry'))
            for expiry in expiries:
                vol._expiry(expiry)
            if len(set(expiries)) != len(expiries):
                raise ValueError(_ui_text('le scadenze devono essere distinte', 'Expiries must be distinct'))
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
                raise RuntimeError(_ui_text('troppi download conservati: riprendi un download esistente o attendi la scadenza', 'Too many retained downloads: resume an existing download or wait for expiry'))
            job = {"id": uuid4().hex, "ticker": ticker, "scope": "all" if expiries is None else "selected",
                   "output_language": capture_language(),
                   "state": "queued", "phase": "catalog" if expiries is None else "chain",
                   "expirations": [], "catalog_complete": expiries is not None,
                   "current_expiry": None, "download_complete": False, "error": None, "retryable": False,
                   "started_at": _now(), "updated_at": _now(), "snapshot_at": None,
                   "spot": None, "spot_source": None, "spot_error": None, "spot_timestamp": None,
                   "spot_timeframe": None, "spot_timestamp_ns": None,
                   "spot_qualified": False, "spot_fallback": False, "spot_alignment": None,
                   "archive": None,
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
        Thread(target=self._run, args=(job,), kwargs={"language": job["output_language"]},
               daemon=True, name=f"options-{job['id'][:8]}").start()

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
        job.update(state="error", error=error if isinstance(error, str) else str(error), retryable=True, updated_at=_now(), download_complete=False)
        if job["current_expiry"] is not None:
            row = job["_rows"][job["current_expiry"]]
            if row["error"] is None:
                row["error"] = error if isinstance(error, str) else str(error)

    @scoped_language
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
            # 10/10 (ordine PM, Opus 5.5): niente yfinance PRIMA delle pagine Polygon:
            # lo spot primario e' il sottostante nelle pagine stesse (_accept_page);
            # yfinance e' il ripiego a fine download, solo se nessuna pagina lo porta.
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
                    self._observe_spot(job)   # ripiego dichiarato, solo se spot ancora assente
                    with self._lock:
                        if job["_pause"]:
                            return
                        job.update(phase="archive", current_expiry=None, download_complete=True,
                                   snapshot_at=_now(), updated_at=_now(), error=None)
                    # 10/10 (Opus 5.5): l'istantanea completa va su file PRIMA di dichiarare
                    # «complete», cosi' chi legge lo stato trova gia' l'esito dell'archivio.
                    outcome = self._archive(job)
                    with self._lock:
                        job.update(state="complete", phase="done", archive=outcome, updated_at=_now())
                    return
                page = vol.get_chain_detail(job["ticker"], expiry, cursor)
                with self._lock:
                    self._accept_page(job, row, cursor, page)
                    if row["error"]:
                        # An f-string would flatten the bilingual error: compose an authored message.
                        self._fail(job, _ui_text('{expiry}: {error}', '{expiry}: {error}',
                                                 expiry=expiry, error=row["error"]))
                        return
        except Exception as exc:
            with self._lock:
                # Avoid exposing provider exception URLs/credentials in status.
                self._fail(job, _ui_text(f'download interrotto ({type(exc).__name__}); riprendi per ritentare', f'Download interrupted ({type(exc).__name__}); resume to retry'))
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
        if (job["spot"] is None or job["spot_source"] != "Polygon underlying snapshot") \
                and vol._finite(page.get("spot"), positive=True) is not None:
            # Polygon primario: sostituisce anche un ripiego yfinance gia' letto.
            ns = vol._finite(page.get("spot_timestamp_ns"), positive=True)
            iso = None
            if ns is not None:
                try:
                    iso = datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()
                except (OverflowError, OSError, ValueError):
                    iso = None
            job.update(spot=page["spot"], spot_source="Polygon underlying snapshot",
                       spot_timestamp=iso or page.get("_timestamp"), spot_timeframe=page.get("spot_timeframe"),
                       spot_timestamp_ns=page.get("spot_timestamp_ns"), spot_error=None,
                       spot_qualified=iso is not None, spot_fallback=False, spot_alignment=None)
        error = page.get("error")
        nxt = page.get("next_cursor")
        if not error and not page.get("complete") and (not nxt or nxt == cursor or nxt in row["seen"]):
            error = _ui_text('ciclo o cursore chain non avanzante', 'Chain cursor cycle or no progress')
            vol._forget_chain_page(job["ticker"], job["current_expiry"], cursor)
        row["error"] = error
        if not error:
            row["seen"].add(cursor)
            row["complete"] = bool(page.get("complete"))
            row["cursor"] = nxt
        job["updated_at"] = _now()

    def _archive(self, job):
        """Salva su file l'istantanea completa. Un errore NON rompe il download: torna come
        esito dichiarato (`status: error`) nello stato, nella superficie e nel log."""
        from bellomberg.portfolio import options_snapshots as archive
        try:
            with self._lock:
                view = {key: deepcopy(job.get(key)) for key in (
                    "ticker", "scope", "snapshot_at", "download_complete", "output_language", "spot",
                    "spot_source", "spot_timestamp", "spot_timeframe", "spot_qualified", "spot_fallback",
                    "spot_alignment")}
                view["download_id"] = job["id"]
                view["rows"] = {expiry: {"contracts": deepcopy(list(row["data"].values())),
                                         "complete": row["complete"], "error": row["error"]}
                                for expiry, row in job["_rows"].items()}
            if not any(row["contracts"] for row in view["rows"].values()):
                return {"status": "skipped", "id": None, "error": None,
                        "note": _ui_text('nessun contratto scaricato: niente da archiviare', 'No contract downloaded: nothing to archive')}
            surface = self.surface(job["id"])
            return archive.save(archive.build_record(view, surface), self.archive_root)
        except Exception as exc:
            vol_log.warning("options_download: istantanea %s %s NON archiviata: %s: %s",
                            job.get("ticker"), job.get("id"), type(exc).__name__, exc)
            return {"status": "error", "id": None,
                    "error": _ui_text(f'istantanea non archiviata su file ({type(exc).__name__}): il download resta valido in memoria, ma il confronto storico non la vedrà',
                                      f'Snapshot not archived to file ({type(exc).__name__}): the download stays valid in memory, but historical comparison will not see it')}

    def _observe_spot(self, job, *, on_demand=False):
        with self._lock:
            if job["spot"] is not None or job["_spot_attempted"] or (job["_pause"] and not on_demand):
                return
            job["_spot_attempted"] = True
            stamps = [c.get("quote_timestamp") for row in job["_rows"].values() for c in row["data"].values()]
        # MA-1 (review v2, 10/10, Opus 5.5): sul piano del PM le pagine non portano il
        # prezzo del sottostante; prima del lastPrice (tempo reale, non qualificato)
        # si prova la barra yfinance 1m all'ora delle quote scaricate (qualificata
        # entro tolleranza). Stessa funzione pubblica della superficie.
        try:
            from bellomberg.market_data.spot_alignment import quote_anchor, resolve_spot
            res = resolve_spot(job["ticker"], anchor=quote_anchor(stamps, future_tolerance_seconds=vol.STALE_QUOTE_SECONDS))
            if res["spot"] is None:
                raise ValueError(_ui_text('prezzo assente o non finito', 'Missing or non-finite price'))
            with self._lock:
                job.update(spot=float(res["spot"]), spot_source=res["spot_source"],
                           spot_timestamp=res["spot_timestamp"], spot_qualified=bool(res["spot_qualified"]),
                           spot_fallback=bool(res["spot_fallback"]), spot_alignment=res["spot_alignment"])
        except Exception as exc:
            with self._lock:
                job["spot_error"] = _ui_text(f'spot yfinance assente ({type(exc).__name__}); serve un prezzo osservato nelle pagine Polygon', f'Missing yfinance spot ({type(exc).__name__}); an observed price in the Polygon pages is required')

    def chain(self, job_id, expiry, offset=0, limit=250, side="all", strike=""):
        vol._expiry(expiry)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError(_ui_text('offset non valido', 'Invalid offset'))
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError(_ui_text('limit deve essere da 1 a 1000', 'limit must be from 1 to 1000'))
        if side not in ("all", "call", "put") or not isinstance(strike, str) or len(strike) > 80:
            raise ValueError(_ui_text('filtro chain non valido', 'Invalid chain filter'))
        strike = strike.strip().replace(",", ".")
        with self._lock:
            job = self._job(job_id)
            row = job["_rows"].get(expiry)
            if row is None:
                raise ValueError(_ui_text('scadenza non presente nel download', 'Expiry not present in the download'))
            selected = [c for c in row["data"].values() if (side == "all" or c["type"] == side)
                        and (not strike or strike in str(c["strike"]))]
            selected.sort(key=lambda c: (c["strike"], c["type"], c["contract"]))
            end = offset + limit
            return {"id": job_id, "ticker": job["ticker"], "expiry": expiry,
                    "chain": render_payload(selected[offset:end]), "offset": offset, "limit": limit,
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
                    "_timestamp": job["updated_at"], "_source": _ui_text("Polygon option-chain snapshot (memoria)", "Polygon option-chain snapshot (in memory)")}

    def surface(self, job_id, *, include_context=False):
        with self._lock:
            job = self._job(job_id)
            need_fallback = job["spot"] is None and not job.get("_spot_attempted") and not job["_worker"]
        if need_fallback:
            # download parziale/in pausa senza prezzo nelle pagine: ripiego dichiarato
            self._observe_spot(job, on_demand=True)
        with self._lock:
            job = self._job(job_id)
            if not job["expirations"]:
                return {"error": _ui_text('nessuna scadenza disponibile nel download', 'No expiry available in the download'), "ticker": job["ticker"],
                        "slices": [], "term_structure": [], "n_expiries": 0, "moneyness_grid": vol.MONEYNESS_GRID,
                        "coverage": {"complete": False, "download_complete": job["download_complete"], "rows": [],
                                     "requested": [], "loaded": [], "excluded": [], "errors": [],
                                     "selection_mode": "explicit", "catalog_note": _ui_text('Catalogo del download in memoria', 'In-memory download catalog')}}
            snapshot = {"spot": job["spot"], "spot_source": job["spot_source"],
                        "spot_timestamp": job["spot_timestamp"], "spot_timeframe": job["spot_timeframe"],
                        "spot_qualified": job.get("spot_qualified"), "spot_fallback": job.get("spot_fallback"),
                        "spot_alignment": job.get("spot_alignment"),
                        "download_complete": job["download_complete"], "chains": {
                            expiry: {"chain": deepcopy(list(row["data"].values())), "complete": row["complete"],
                                     "continuation_error": row["error"]}
                            for expiry, row in job["_rows"].items()}}
            ticker, expiries, stamp = job["ticker"], list(job["expirations"]), job["snapshot_at"] or job["updated_at"]
            # 10/10 (revisore, Opus 5.5): t_years/giorni relativi all'istantanea, non all'ora della chiamata
            snapshot["snapshot_at"] = stamp
            archived = deepcopy(job.get("archive"))
        out = vol.build_vol_surface(ticker, expiries=expiries, include_context=include_context, _snapshot=snapshot)
        out.update(download_id=job_id, snapshot_at=stamp, _timestamp=stamp, archive=archived)
        return out

    def context(self, job_id):
        """A4 audit Vol Deck (09/10, Opus 5.5): superficie + contesto calcolati
        sull'ISTANTANEA del job — nessuna chain riscaricata, stesso snapshot_at del
        laboratorio. RV (chiusure yfinance) e IV rank (DB) non sono chain; il GEX
        fa il suo fetch e `context_sources` lo dichiara con l'ora."""
        out = self.surface(job_id, include_context=True)
        if out.get("error"):
            return out
        out.update(vol.context_panels(out.get("ticker") or self._job(job_id)["ticker"]))
        out["context_sources"]["surface"] = _ui_text('istantanea del download in memoria: nessuna chain riscaricata', 'In-memory download snapshot: no chain re-downloaded')
        return out


downloads = OptionsDownloadManager()
