"""Authenticated preferences, separate from portfolio and historical research."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bellomberg.core.language import text
from bellomberg.storage import preferences


class PreferencesIn(BaseModel):
    model_config = {"strict": True, "extra": "forbid"}
    language: str
    repair_fingerprint: str | None = None


def create_language_router(require_session):
    router = APIRouter()

    @router.get("/preferences", dependencies=[Depends(require_session)])
    def get_preferences():
        try:
            return preferences.read_preferences()
        except preferences.PreferenceError as exc:
            raise HTTPException(503, {"code": "language_preference_unavailable",
                                      "fingerprint": exc.fingerprint,
                                      "message": "Preferenze illeggibili / Saved preferences unreadable."}) from exc

    @router.put("/preferences", dependencies=[Depends(require_session)])
    def put_preferences(body: PreferencesIn):
        try:
            return preferences.set_language_preference(body.language, repair_fingerprint=body.repair_fingerprint)
        except ValueError as exc:
            raise HTTPException(422, {"code": "unsupported_language", "message": text(
                "Lingua non supportata: scegli it o en.", "Unsupported language: choose it or en.")}) from exc
        except preferences.PreferenceConflict as exc:
            raise HTTPException(409, {"code": "language_preference_changed", "message": text(
                "Preferenze cambiate: rileggi prima del ripristino.",
                "Preferences changed: read them again before repairing.")}) from exc
        except preferences.PreferenceError as exc:
            raise HTTPException(503, {"code": "language_preference_unavailable",
                                      "fingerprint": exc.fingerprint,
                                      "message": "Preferenze non salvate / Preferences not saved."}) from exc
    return router
