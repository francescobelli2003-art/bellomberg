"""Public upgrade API contract: synthetic SQLite and no legacy workbook fixture."""
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from test_trade_idea_store import BASE_SCHEMA


def test_storage_upgrade_api_preserves_detail_and_adds_action(tmp_path):
    from bellomberg.api import trade_idea_routes as routes
    path = tmp_path / "pre-trade-idea.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(BASE_SCHEMA)
        before = conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    app = FastAPI()
    routes.install_trade_idea_routes(app, lambda: "synthetic-session", db_path=path)
    with TestClient(app) as client:
        response = client.get("/trade-ideas/runs")
    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"] == "Trade Idea storage non pronto: Trade Idea schema absent: run explicit migration"
    assert payload.get("error_code") == "trade_idea_schema_absent"
    assert payload["storage"]["status"] == "schema_absent"
    assert payload["storage"]["action"] == "run_explicit_migration"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall() == before


@pytest.mark.parametrize("state", ["db_missing", "db_unreadable"])
def test_storage_api_does_not_offer_migration_for_database_path_or_integrity_failure(tmp_path, state):
    from bellomberg.api import trade_idea_routes as routes
    path = tmp_path / "private-location.db"
    if state == "db_unreadable":
        path.write_bytes(b"synthetic corrupt SQLite")
    app = FastAPI()
    routes.install_trade_idea_routes(app, lambda: "synthetic-session", db_path=path)
    with TestClient(app) as client:
        response = client.get("/trade-ideas/active")
    assert response.status_code == 503
    assert response.json()["storage"]["status"] == state
    assert response.json()["storage"]["update_required"] is False
    assert str(path) not in response.text
    assert path.exists() is (state == "db_unreadable")


def test_preflight_storage_block_does_not_call_source_qualification(tmp_path, monkeypatch):
    from functools import partial
    from bellomberg.agents import trade_idea
    from bellomberg.api import trade_idea_routes as routes
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(BASE_SCHEMA)
    catalogue = {"models": {role: {"id": model, "context_length": 500000,
        "max_completion_tokens": 128000, "supported_efforts": ["low", "medium", "high", "max"],
        "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
        for role, model in trade_idea.MODEL_IDS.items()}}
    def never_qualify(*args, **kwargs):
        pytest.fail("blocked storage must not acquire sources")
    monkeypatch.setattr(routes, "preflight_trade_idea", partial(trade_idea.preflight_trade_idea,
        catalog_fetcher=lambda: catalogue, key_checker=lambda: None, mandate_loader=lambda: {},
        identity_resolver=lambda ticker: {"ticker": ticker, "name": "Synthetic", "exchange": "XNAS",
            "currency": "USD", "status": "confirmed", "reason": None}, source_qualifier=never_qualify))
    monkeypatch.setattr(routes, "_spawn_worker", lambda *a, **k: pytest.fail("storage blocked worker start"))
    app = FastAPI()
    routes.install_trade_idea_routes(app, lambda: "synthetic-session", db_path=path,
                                    source_archive_root=tmp_path / "archive")
    with TestClient(app) as client:
        response = client.post("/trade-ideas/preflight", json={"ticker": "TEST", "budget_limit_usd": "5"})
        start = client.post("/trade-ideas/runs", json={"ticker": "TEST", "budget_limit_usd": "5",
            "idempotency_key": "synthetic-upgrade", "cost_acknowledged": True,
            "authorization": {"accepted": True, "source_fingerprint": "a" * 64,
                "activities": ["committee"], "max_revision_rounds": 0}})
    assert start.status_code == 503
    assert start.json()["storage"]["status"] == "schema_absent"
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["identity"]["status"] == "confirmed"
    assert body["storage"]["error_code"] == "trade_idea_schema_absent"
    assert body["source_qualification"]["execution_status"] == "not_run"
    assert body["source_qualification"]["reasons"] == []
    assert len(body["reasons"]) == 1 and "storage non pronto" in body["reasons"][0]
