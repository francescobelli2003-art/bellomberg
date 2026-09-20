"""Session protected filing archive and explicit refresh endpoints."""
import re
import sqlite3
import json

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException

from bellomberg.core.api_presentation import PresentationJSONResponse
from bellomberg.core.language import text as _text


def default_service():
    from bellomberg.core.paths import DATA_DIR, SQLITE_PATH
    from bellomberg.market_data.filing_service import FilingService
    from bellomberg.storage.filing_store import FilingStore
    return FilingService(FilingStore(SQLITE_PATH), DATA_DIR / "filing_archive")


def _service(factory):
    try:
        return factory()
    except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        raise HTTPException(503, _text(
            "Archivio filing non disponibile: " + str(exc),
            "Filing archive unavailable: " + str(exc))) from exc


def _ticker(value):
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,29}", value):
        raise HTTPException(422, _text("Ticker non valido", "Invalid ticker"))
    return value


def create_filing_router(require_session, service_factory=default_service):
    router = APIRouter(prefix="/filings", tags=["filings"],
                       dependencies=[Depends(require_session)],
                       default_response_class=PresentationJSONResponse)

    @router.get("/runs/{run_id}")
    def detail(run_id: int):
        if run_id < 1:
            raise HTTPException(422, _text("ID run non valido", "Invalid run ID"))
        try:
            run = _service(service_factory).detail(run_id)
        except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            raise HTTPException(503, _text("Archivio filing non leggibile: " + str(exc),
                                            "Filing archive cannot be read: " + str(exc))) from exc
        if run is None:
            raise HTTPException(404, _text("Run filing assente", "Filing run not found"))
        return run

    @router.get("/{ticker}")
    def status(ticker: str):
        ticker = _ticker(ticker)
        try:
            return _service(service_factory).status(ticker)
        except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            raise HTTPException(503, _text("Archivio filing non leggibile: " + str(exc),
                                            "Filing archive cannot be read: " + str(exc))) from exc

    @router.post("/{ticker}/refresh", status_code=202)
    def refresh(ticker: str, background_tasks: BackgroundTasks):
        service = _service(service_factory)
        try:
            queued = service.queue(_ticker(ticker), trigger="manual")
        except ValueError as exc:
            raise HTTPException(422, _text("Richiesta filing non valida: " + str(exc),
                                            "Invalid filing request (technical detail): " + str(exc))) from exc
        except RuntimeError as exc:
            if "run gi" in str(exc) and "attivo" in str(exc):
                raise HTTPException(409, _text("Run filing gia attivo", "Filing run already active")) from exc
            raise HTTPException(503, _text("Archivio filing non disponibile: " + str(exc),
                                            "Filing archive unavailable: " + str(exc))) from exc
        except (FileNotFoundError, sqlite3.DatabaseError) as exc:
            raise HTTPException(503, _text("Archivio filing non disponibile: " + str(exc),
                                            "Filing archive unavailable: " + str(exc))) from exc
        background_tasks.add_task(service.execute, queued["id"])
        return {"run_id": queued["id"], "status": "queued"}

    @router.put("/{ticker}/profile")
    def profile(ticker: str, body: dict = Body(...)):
        ticker = _ticker(ticker)
        expected = {"profile", "enabled", "interval_hours", "qualitative_enabled"}
        if set(body) != expected:
            raise HTTPException(422, _text("Campi del profilo incompleti o sconosciuti",
                                                "Incomplete or unknown profile fields"))
        service = _service(service_factory)
        try:
            return service.store.set_profile(ticker, body["profile"],
                                             enabled=body["enabled"],
                                             interval_hours=body["interval_hours"],
                                             qualitative_enabled=body["qualitative_enabled"])
        except ValueError as exc:
            raise HTTPException(422, _text("Profilo filing non valido: " + str(exc),
                                            "Invalid filing profile (technical detail): " + str(exc))) from exc
        except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError) as exc:
            raise HTTPException(503, _text("Archivio filing non disponibile: " + str(exc),
                                            "Filing archive unavailable: " + str(exc))) from exc

    return router
