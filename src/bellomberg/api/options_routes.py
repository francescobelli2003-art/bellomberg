"""Explicit option data requests and an offline strategy calculator."""
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from typing import Literal


def create_options_router(require_session):
    router = APIRouter(prefix="/options", tags=["options-lab"], dependencies=[Depends(require_session)])

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
            raise HTTPException(422, "campi download non riconosciuti")
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

    @router.post("/strategy/simulate")
    def simulate(body: dict = Body(...)):
        from bellomberg.portfolio.options_strategy import simulate_strategy
        try:
            return simulate_strategy(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
