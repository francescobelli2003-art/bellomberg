"""Explicit option data requests and an offline strategy calculator."""
from bellomberg.core.language import text as _ui_text
from bellomberg.core.api_presentation import PresentationJSONResponse
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from typing import Literal


def create_options_router(require_session):
    router = APIRouter(prefix="/options", tags=["options-lab"], dependencies=[Depends(require_session)],
                       default_response_class=PresentationJSONResponse)

    @router.get("/expiry_catalog/{ticker}")
    def expiry_catalog(ticker: str, after: str | None = None,
                       request_budget: int = Query(8, ge=1, le=8)):
        from bellomberg.portfolio.vol_surface import get_expiry_catalog
        try:
            return get_expiry_catalog(ticker, after=after, request_budget=request_budget)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/chain_detail/{ticker}")
    def chain_detail(ticker: str, expiry: str, cursor: str | None = None):
        from bellomberg.portfolio.vol_surface import get_chain_detail
        try:
            return get_chain_detail(ticker, expiry, cursor=cursor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    def download_call(method, *args, **kwargs):
        from bellomberg.portfolio.options_download import downloads
        try:
            return getattr(downloads, method)(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc

    @router.post("/download/{ticker}")
    def start_download(ticker: str, body: dict | None = Body(None)):
        body = body or {}
        if set(body) - {"expiries"}:
            raise HTTPException(422, _ui_text('campi download non riconosciuti', 'Unrecognized download fields'))
        return download_call("start", ticker, body.get("expiries"))

    @router.get("/download/{job_id}/status")
    def download_status(job_id: str):
        return download_call("status", job_id)

    @router.post("/download/{job_id}/pause")
    def pause_download(job_id: str):
        return download_call("pause", job_id)

    @router.post("/download/{job_id}/resume")
    def resume_download(job_id: str):
        return download_call("resume", job_id)

    @router.get("/download/{job_id}/chain")
    def downloaded_chain(job_id: str, expiry: str, offset: int = Query(0, ge=0),
                         limit: int = Query(250, ge=1, le=1000),
                         side: Literal["all", "call", "put"] = "all", strike: str = Query("", max_length=80)):
        return download_call("chain", job_id, expiry, offset=offset, limit=limit, side=side, strike=strike)

    @router.get("/download/{job_id}/surface")
    def downloaded_surface(job_id: str):
        return download_call("surface", job_id)

    # 10/10 (Opus 5.5): istantanee archiviate su file a ogni download completato
    # (bellomberg.portfolio.options_snapshots): il confronto ΔIV non dipende piu' dal browser.
    @router.get("/snapshots")
    def list_snapshots(ticker: str | None = Query(None, max_length=25)):
        from bellomberg.portfolio import options_snapshots as archive
        from bellomberg.portfolio.options_download import downloads
        try:
            return archive.list_snapshots(ticker, downloads.archive_root)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/snapshots/{snapshot_id}")
    def load_snapshot(snapshot_id: str):
        from bellomberg.portfolio import options_snapshots as archive
        from bellomberg.portfolio.options_download import downloads
        try:
            return archive.load(snapshot_id, downloads.archive_root)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except archive.SnapshotBusy as exc:
            raise HTTPException(503, _ui_text(f'istantanea in uso o in rimozione ({exc}): riprova fra poco', f'Snapshot in use or being removed ({exc}): retry shortly')) from exc
        except archive.SnapshotUnreadable as exc:
            raise HTTPException(500, _ui_text(f'istantanea illeggibile su disco ({exc})', f'Snapshot unreadable on disk ({exc})')) from exc

    @router.get("/strategy/rate")
    def strategy_rate():
        """09/10 (Opus 5.5, audit M4): short USD rate for the laboratory, with its source and date.

        FRED DGS3MO (3-month Treasury constant maturity, percent). A missing or failed series is
        declared (`status: error`, `value: null`): the client asks the user, never a silent 0.
        """
        source = "FRED DGS3MO"  # 3-month US Treasury constant maturity (H.15)
        try:
            from bellomberg.market_data.macro_rates import MAX_STALE_DAILY, _fred_curve, _is_stale
            points, gaps, as_of = _fred_curve({"3M": "DGS3MO"}, 10)
        except Exception as exc:  # provider/import failure is reported, not replaced
            return {"value": None, "percent": None, "date": None, "source": source, "status": "error",
                    "error": f"{type(exc).__name__}: {exc}"}
        point = next((p for p in points if p.get("tenor") == "3M"), None)
        value = point.get("value") if point else None
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not -100 < value < 100:
            reason = (gaps[0].get("reason") if gaps else None) or _ui_text('nessuna osservazione', 'no observation')
            return {"value": None, "percent": None, "date": None, "source": source, "status": "error", "error": str(reason)}
        return {"value": value / 100, "percent": value, "date": point.get("date"), "source": source,
                "status": "stale" if _is_stale(point.get("date"), MAX_STALE_DAILY) else "solid", "error": None}

    @router.get("/download/{job_id}/context")
    def downloaded_context(job_id: str):
        # A4 audit Vol Deck (09/10): contesto sull'istantanea del job, senza refetch delle chain.
        return download_call("context", job_id)

    @router.post("/strategy/simulate")
    def simulate(body: dict = Body(...)):
        from bellomberg.portfolio.options_strategy import simulate_strategy
        try:
            return simulate_strategy(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
