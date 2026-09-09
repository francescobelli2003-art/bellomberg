"""Explicit option data requests and an offline strategy calculator."""
from fastapi import APIRouter, Body, Depends, HTTPException, Query


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

    @router.post("/strategy/simulate")
    def simulate(body: dict = Body(...)):
        from bellomberg.portfolio.options_strategy import simulate_strategy
        try:
            return simulate_strategy(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
