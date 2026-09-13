"""Private read-only agent progress API; no schema creation or paid inference."""
from bellomberg.core.presentation import message as _ui_text, error_text
from bellomberg.core.api_presentation import PresentationJSONResponse
from fastapi import APIRouter, Depends, HTTPException, Query

from bellomberg.agents.score_history import HistoryUnavailable, progress_payload, read_history
from bellomberg.agents.scorekeeper import scorecard_for_api
from bellomberg.core.paths import SQLITE_PATH


def create_agent_progress_router(require_session, *, db_path=None, scorecard_path=None):
    router = APIRouter(prefix="/agents", tags=["agent-progress"], dependencies=[Depends(require_session)],
                      default_response_class=PresentationJSONResponse)

    @router.get("/progress")
    def get_progress(limit: int = Query(default=100, ge=1, le=250)):
        try:
            rows = read_history(db_path if db_path is not None else SQLITE_PATH, limit)
            return progress_payload(rows, current_scorecard=scorecard_for_api(scorecard_path))
        except HistoryUnavailable as exc:
            raise HTTPException(503, error_text(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, _ui_text('Lettura progressi non disponibile; verificare lo storico locale', 'Progress unavailable; check the local history')) from exc

    return router
