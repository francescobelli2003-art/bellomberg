"""Router Diario: ogni lettura e scrittura richiede la sessione dell'app."""
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from bellomberg.storage.journal import (
    JournalConflict, JournalInvalid, JournalMissing, JournalStore, JournalUnavailable,
)

_LOG = logging.getLogger(__name__)
Version = Annotated[int, Field(strict=True, ge=1)]


class JournalContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["thesis", "macro"]
    ticker: Annotated[str | None, Field(max_length=32)] = None
    title: Annotated[str, Field(strict=True, min_length=1, max_length=160)]
    body: Annotated[str, Field(strict=True, min_length=1, max_length=30000)]


class JournalUpdate(JournalContent):
    expected_version: Version


class JournalArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: Version
    archived: StrictBool


def create_journal_router(get_db, require_session):
    router = APIRouter(prefix="/journal", tags=["journal"], dependencies=[Depends(require_session)])

    def call(method, *args, **kwargs):
        try:
            return getattr(JournalStore(get_db()), method)(*args, **kwargs)
        except JournalConflict as exc:
            raise HTTPException(409, {"code": "journal_version_conflict", "message": str(exc),
                                      "current_version": exc.current_version}) from exc
        except JournalMissing as exc:
            raise HTTPException(404, {"code": "journal_not_found", "message": str(exc)}) from exc
        except JournalInvalid as exc:
            raise HTTPException(422, {"code": "journal_invalid", "message": str(exc)}) from exc
        except JournalUnavailable as exc:
            _LOG.warning("Diario: %s", exc.code)
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc
        except Exception as exc:
            # Nessun percorso, testo privato o credenziale nel payload o nel log.
            _LOG.warning("Diario non disponibile (%s)", type(exc).__name__)
            raise HTTPException(503, {"code": "journal_unavailable", "message":
                                     "Diario non disponibile: accesso al database non riuscito."}) from exc

    @router.get("")
    def list_entries(status: Literal["active", "archived", "all"] = "active",
                     kind: Literal["thesis", "macro"] | None = None,
                     ticker: str | None = Query(None, max_length=32),
                     query: str = Query("", max_length=200), limit: int = Query(50, ge=1, le=100),
                     offset: int = Query(0, ge=0)):
        return call("list_entries", status=status, kind=kind, ticker=ticker, query=query, limit=limit, offset=offset)

    @router.post("", status_code=201)
    def create(body: JournalContent):
        return call("create", **body.model_dump())

    @router.get("/{entry_id}")
    def get(entry_id: int):
        return call("get", entry_id)

    @router.put("/{entry_id}")
    def update(entry_id: int, body: JournalUpdate):
        return call("update", entry_id, **body.model_dump())

    @router.post("/{entry_id}/archive")
    def archive(entry_id: int, body: JournalArchive):
        return call("set_archived", entry_id, **body.model_dump())

    @router.get("/{entry_id}/versions")
    def history(entry_id: int, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return call("history", entry_id, limit=limit, offset=offset)

    return router
