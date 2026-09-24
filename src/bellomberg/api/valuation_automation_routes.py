"""Session-protected current valuation state, downloads and explicit user actions.

Mount explicitly with a path-returning ``db_provider`` and publication roots.
No MemoryDB construction, schema migration, acquisition or AI call occurs here.
Refresh only enqueues through an explicitly supplied running automation service.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from bellomberg.core.api_presentation import PresentationJSONResponse


_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,29}\Z")
_GENERATION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _ticker(value):
    if not _TICKER.fullmatch(value):
        raise HTTPException(422, {"code": "invalid_ticker", "message": "Ticker non valido"})
    return value


def _unavailable(exc):
    message = str(exc).lower()
    migration = any(marker in message for marker in ("schema", "migrate", "migration", "no such table", "no such column"))
    return HTTPException(503, {"code": "not_migrated" if migration else "valuation_store_unavailable",
                               "message": "Schema valutazioni non migrato" if migration else "Archivio valutazioni non disponibile"})


def _job_summary(row, *, price=False, payload=None):
    if row is None:
        return None
    result = row.get("result") if price else None
    status, reason = row['status'], row.get('reason')
    if price and status == 'succeeded' and isinstance(result, dict):
        from bellomberg.valuation.market_quote import FX_CONTRACT, attest_repriced_job
        quote = result.get('market_quote')
        if isinstance(quote, dict) and quote.get('contract') == FX_CONTRACT and not attest_repriced_job(row, payload):
            status, reason = 'incomplete', 'Confronto prezzo/FX non riproducibile dalle acquisizioni e dal modello corrente'
    return {"id": row["id"], "status": status, "reason": reason,
            "attempts": row["attempts"], "updated_at": row["updated_at"],
            **({"market_quote": result.get("market_quote") if status == "succeeded"
                and isinstance(result, dict) else None} if price else {})}


def _read_store_state(db_path, roots, ticker):
    """Use only existing tables and SQLite read-only connections."""
    from bellomberg.storage.valuation_jobs import ValuationJobStore
    from bellomberg.storage.valuation_versions import ValuationVersions
    if not isinstance(db_path, (str, os.PathLike)):
        raise TypeError("db_provider must return a path, not a MemoryDB instance")
    path = Path(db_path).resolve()
    jobs = ValuationJobStore(path)
    versions = ValuationVersions(SimpleNamespace(db_path=path), roots=roots)
    view = versions.current(ticker)
    with jobs._connect(read_only=True) as conn:
        row = conn.execute("""SELECT id,status,reason,attempts,updated_at
            FROM valuation_jobs WHERE ticker=? AND kind='prepare' ORDER BY id DESC LIMIT 1""",
            (ticker,)).fetchone()
    prepare_job = _job_summary(dict(row) if row is not None else None)
    generation = (view or {}).get("current_generation")
    price_job = _job_summary(jobs.latest_price_result(ticker, generation), price=True,
                             payload=(view or {}).get('current')) if generation else None
    return view, prepare_job, price_job


def _present_state(ticker, view, prepare_job, price_job):
    payload = (view or {}).get("current")
    artifact = (view or {}).get("artifact") or {"available": False, "reason": "Nessuna versione pubblicata"}
    current = None if payload is None else {
        "snapshot_id": payload["snapshot_id"], "generation_id": payload["generation_id"],
        "revision": view.get("current_revision"),
        "published_at": view.get("current_published_at"),
        "method_id": (payload.get("valuation_decision") or {}).get("method_id"),
        "valuation_date": payload.get("valuation_date"),
        "as_of": ((payload.get("acquisition_snapshot") or {}).get("case") or {}).get("as_of"),
        "fair_value_bear": payload.get("fair_value_bear"),
        "fair_value_base": payload.get("fair_value_base"),
        "fair_value_bull": payload.get("fair_value_bull"),
        "display_usable": artifact["available"] is True
            and (payload.get("valuation_usability") or {}).get("usable") is True}
    latest = (view or {}).get("latest_attempt")
    return {"ticker": ticker, "status": "current" if current else "no_current",
            "model_id": (view or {}).get("model_id"), "locked": bool(view["locked"]) if view else None,
            "current": current, "artifact": artifact,
            "latest_publication_attempt": ({key: latest[key] for key in
                ("snapshot_id", "generation_id", "revision", "status", "reason", "origin", "created_at")}
                if latest else None),
            "latest_prepare_job": prepare_job, "latest_price_job": price_job,
            "approval": (view or {}).get("approval")}


def current_model_state(db_path, *, roots, ticker):
    """Reusable F17 read helper; no HTTP, migration, or MemoryDB side effects."""
    if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker):
        raise ValueError("invalid ticker")
    view, prepare_job, price_job = _read_store_state(db_path, roots, ticker)
    return _present_state(ticker, view, prepare_job, price_job)


def model_catalog_state(db_path, *, roots):
    """Read explicit heads for the existing model list, including failed attempts."""
    from bellomberg.storage.valuation_versions import ValuationVersions, _require_schema
    versions = ValuationVersions(SimpleNamespace(db_path=db_path), roots=roots)
    with versions._conn(read_only=True) as conn:
        _require_schema(conn)
        rows = conn.execute("""SELECT ticker FROM valuation_model_heads UNION SELECT ticker FROM valuation_snapshots
            UNION SELECT ticker FROM valuation_jobs""").fetchall()
    catalog = {}
    for row in rows:
        view, prepare_job, price_job = _read_store_state(db_path, roots, row["ticker"])
        payload = (view or {}).get("current")
        thesis = None
        if payload:
            with versions._conn(read_only=True) as conn:
                thesis = conn.execute("""SELECT t.date,t.variant_view FROM valuation_snapshot_links l
                    JOIN valuation_theses t ON t.id=l.thesis_id WHERE l.snapshot_id=? AND l.generation_id=?
                    ORDER BY l.id LIMIT 1""", (payload["snapshot_id"], payload["generation_id"])).fetchone()
        catalog[row["ticker"]] = {"state": _present_state(row["ticker"], view, prepare_job, price_job),
                                 "payload": payload, "thesis": dict(thesis) if thesis else {},
                                 "created_at": (view or {}).get("current_published_at")}
    return catalog


def create_valuation_automation_router(require_session, *, db_provider, roots,
                                     automation_provider=None, variants_root=None):
    """Providers are server-owned; no request supplies a path or AI configuration.

    ``roots`` are the trusted workbook roots also used by ValuationVersions.
    A missing/unmigrated database fails without creating either files or schema.
    """
    if not callable(db_provider):
        raise TypeError("db_provider must supply the existing SQLite path")
    allowed_roots = tuple(Path(root).resolve() for root in roots)
    if not allowed_roots:
        raise ValueError("trusted model roots required")
    router = APIRouter(prefix="/valuation/models", tags=["valuation-models"],
                       dependencies=[Depends(require_session)],
                       default_response_class=PresentationJSONResponse)

    def read(ticker):
        try:
            return _read_store_state(db_provider(), allowed_roots, ticker)
        except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError,
                TypeError, ValueError, OSError) as exc:
            raise _unavailable(exc) from exc

    def identifier(value, field):
        try:
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError("canonical UUID required")
        except (ValueError, AttributeError) as exc:
            raise HTTPException(422, {"code": "invalid_" + field, "message": "Identita non valida"}) from exc
        return value

    def fields(body, expected):
        if set(body) != set(expected):
            raise HTTPException(422, {"code": "invalid_action", "message": "Campi della richiesta non validi"})

    def versions():
        from bellomberg.storage.valuation_versions import ValuationVersions
        return ValuationVersions(SimpleNamespace(db_path=db_provider()), roots=allowed_roots)

    def variant_store():
        from bellomberg.storage.valuation_variants import PersonalVariants
        if variants_root is None:
            raise HTTPException(503, {"code": "variants_not_configured", "message": "Archivio varianti non configurato"})
        return PersonalVariants(versions(), variants_root)

    def variant_summary(row):
        return {key: row[key] for key in ("id", "ticker", "source_generation", "label", "created_at",
            "status", "error", "available", "modified", "current_sha256", "read_error") if key in row}

    @router.post("/{ticker}/refresh", status_code=202)
    def refresh(ticker: str, body: dict):
        ticker = _ticker(ticker)
        fields(body, ("request_id",))
        request_id = identifier(body["request_id"], "request_id")
        if automation_provider is None:
            raise HTTPException(503, {"code": "worker_unavailable", "message": "Automazione non avviata"})
        try:
            manager = automation_provider()
            row = manager.enqueue_refresh(ticker, "manual_refresh", "manual-request-v1:" + request_id)
            return {key: row[key] for key in ("id", "status", "reason", "reused") if key in row}
        except PermissionError as exc:
            raise HTTPException(403, {"code": "refresh_not_authorized", "message": str(exc)}) from exc
        except HTTPException:
            raise
        except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
            raise _unavailable(exc) from exc

    @router.post("/{ticker}/lock")
    def lock(ticker: str, body: dict):
        ticker = _ticker(ticker)
        fields(body, ("locked", "generation_id"))
        generation = identifier(body["generation_id"], "generation_id")
        if type(body["locked"]) is not bool:
            raise HTTPException(422, {"code": "invalid_lock", "message": "Stato di blocco booleano richiesto"})
        try:
            versions().set_locked(ticker, body["locked"], expected_current_generation=generation)
        except ValueError as exc:
            raise HTTPException(409, {"code": "current_changed", "message": str(exc)}) from exc
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            raise _unavailable(exc) from exc
        return {"ticker": ticker, "generation_id": generation, "locked": body["locked"]}

    @router.post("/{ticker}/variants", status_code=201)
    def create_variant(ticker: str, body: dict):
        ticker = _ticker(ticker)
        fields(body, ("request_id", "generation_id", "label"))
        request_id = identifier(body["request_id"], "request_id")
        generation = identifier(body["generation_id"], "generation_id")
        try:
            return variant_summary(variant_store().create(ticker, body["label"], request_id=request_id,
                                                          expected_generation=generation))
        except ValueError as exc:
            raise HTTPException(409, {"code": "variant_conflict", "message": str(exc)}) from exc
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            raise _unavailable(exc) from exc

    @router.get("/{ticker}/variants")
    def list_variants(ticker: str, response: Response):
        ticker = _ticker(ticker)
        try:
            store = variant_store()
            with store.versions._conn(read_only=True) as conn:
                ids = [row[0] for row in conn.execute("SELECT id FROM valuation_variants WHERE ticker=? ORDER BY created_at,id", (ticker,))]
            rows = [variant_summary(store.get(variant_id)) for variant_id in ids]
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            raise _unavailable(exc) from exc
        response.headers["Cache-Control"] = "no-store"
        return {"ticker": ticker, "variants": rows}

    @router.get("/{ticker}/variants/{variant_id}/workbook")
    def download_variant(ticker: str, variant_id: str):
        ticker = _ticker(ticker)
        identifier(variant_id, "variant_id")
        try:
            row = variant_store().get(variant_id)
            if row is None or row["ticker"] != ticker:
                raise HTTPException(404, {"code": "variant_absent", "message": "Variante non trovata"})
            if not row["available"]:
                raise HTTPException(409, {"code": "variant_unavailable", "message": "File personale non disponibile"})
            contents = Path(row["path"]).read_bytes()
            if sha256(contents).hexdigest() != row["current_sha256"]:
                raise HTTPException(409, {"code": "variant_changed", "message": "File personale cambiato durante la lettura"})
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            raise _unavailable(exc) from exc
        return Response(contents, media_type=_XLSX, headers={"Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="PERSONAL_' + variant_id + '.xlsx"',
            "X-Valuation-Generation": row["source_generation"]})

    @router.get("/{ticker}")
    def current_state(ticker: str, response: Response):
        ticker = _ticker(ticker)
        view, prepare_job, price_job = read(ticker)
        response.headers["Cache-Control"] = "no-store"
        return _present_state(ticker, view, prepare_job, price_job)

    @router.get("/{ticker}/generations/{generation_id}/workbook")
    def download_current_workbook(ticker: str, generation_id: str):
        ticker = _ticker(ticker)
        if not _GENERATION.fullmatch(generation_id):
            raise HTTPException(422, {"code": "invalid_generation", "message": "Generazione non valida"})
        view, _, _ = read(ticker)
        payload = (view or {}).get("current")
        if payload is None:
            raise HTTPException(404, {"code": "current_absent", "message": "Nessun modello corrente"})
        if payload["generation_id"] != generation_id:
            raise HTTPException(409, {"code": "not_current_generation", "message": "Generazione non corrente"})
        if not view["artifact"]["available"]:
            raise HTTPException(409, {"code": "artifact_unavailable", "message": view["artifact"]["reason"]})
        try:
            path = Path(payload["path"]).resolve(strict=True)
            if (path.suffix.lower() != ".xlsx" or not path.is_file()
                    or not any(path.is_relative_to(root) for root in allowed_roots)):
                raise ValueError("workbook outside trusted roots or wrong file type")
            contents = path.read_bytes()
            if sha256(contents).hexdigest() != payload.get("workbook_sha256"):
                raise ValueError("workbook bytes changed after artifact verification")
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(409, {"code": "artifact_unavailable",
                                      "message": "Workbook corrente non più verificabile"}) from exc
        filename = "VAL_" + ticker.replace(".", "_") + "_" + generation_id + ".xlsx"
        return Response(content=contents, media_type=_XLSX,
                        headers={"Cache-Control": "no-store",
                                 "Content-Disposition": 'attachment; filename="' + filename + '"',
                                 "X-Valuation-Generation": generation_id})

    return router
