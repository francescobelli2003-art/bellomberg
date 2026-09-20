import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from bellomberg.api.filing_routes import create_filing_router
from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage.filing_store import FilingStore, ensure_schema


PROFILE = {"ticker": "TEST", "emittente_id": "CIK:123", "lingua": "en",
           "tipo": "annuale", "perimetro": "consolidato",
           "verifica": {"lingua": "English", "tipo": "annual", "perimetro": "consolidated"},
           "sezioni": {"risk": {"inizio": "Risk Factors", "fine": "End"}}}


def _client(tmp_path, *, migrated=True):
    path = tmp_path / "filing.sqlite"
    with sqlite3.connect(path) as conn:
        if migrated:
            ensure_schema(conn)
    service = FilingService(FilingStore(path), tmp_path / "archive",
                            pipeline=lambda *a, **k: {"stato": "non_disponibile", "motivi": ["fixture"],
                                                      "confronto_corrente": None, "confronto_storico": None}) if migrated else None

    def auth(request: Request):
        if request.headers.get("X-BB-Token") != "test":
            raise HTTPException(401, "sessione richiesta")

    app = FastAPI()
    app.include_router(create_filing_router(auth, service_factory=lambda: service if migrated else FilingStore(path)))
    return TestClient(app), path, service


def test_auth_and_missing_schema(tmp_path):
    client, _, _ = _client(tmp_path, migrated=False)
    assert client.get("/filings/TEST").status_code == 401
    assert client.get("/filings/runs/1").status_code == 401
    assert client.post("/filings/TEST/refresh").status_code == 401
    assert client.put("/filings/TEST/profile", json={}).status_code == 401
    assert client.get("/filings/TEST", headers={"X-BB-Token": "test"}).status_code == 503


def test_profile_refresh_detail_and_read_only(tmp_path):
    client, path, service = _client(tmp_path)
    headers = {"X-BB-Token": "test"}
    body = {"profile": PROFILE, "enabled": True, "interval_hours": 168,
            "qualitative_enabled": False}
    assert client.put("/filings/TEST/profile", headers=headers, json=body).status_code == 200
    status = client.get("/filings/TEST", headers=headers)
    assert status.status_code == 200 and status.json()["profile"]["version"] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM filing_runs").fetchone()[0] == 0
    assert client.get("/filings/runs/999", headers=headers).status_code == 404
    response = client.post("/filings/TEST/refresh", headers=headers)
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    detail = client.get(f"/filings/runs/{run_id}", headers=headers)
    assert detail.status_code == 200 and detail.json()["status"] == "non_disponibile"
    assert client.put("/filings/TEST/profile", headers=headers,
                      json={**body, "interval_hours": 0}).status_code == 422


def test_active_run_conflict(tmp_path):
    client, _, service = _client(tmp_path)
    headers = {"X-BB-Token": "test"}
    service.store.set_profile("TEST", PROFILE)
    service.queue("TEST")
    assert client.post("/filings/TEST/refresh", headers=headers).status_code == 409


def test_failed_background_job_is_persisted_and_visible(tmp_path):
    client, path, service = _client(tmp_path)
    service.store.set_profile("TEST", PROFILE)
    calls = []

    def failing_pipeline(*_args, **_kwargs):
        calls.append(1)
        raise OSError("offline fixture")

    service.pipeline = failing_pipeline
    headers = {"X-BB-Token": "test"}
    accepted = client.post("/filings/TEST/refresh", headers=headers)
    assert accepted.status_code == 202 and accepted.json()["status"] == "queued"
    assert len(calls) == 1
    run_id = accepted.json()["run_id"]
    detail = client.get(f"/filings/runs/{run_id}", headers=headers).json()
    assert detail["status"] == "errore" and "offline fixture" in detail["reason"]
    assert client.get("/filings/TEST", headers=headers).json()["status"] == "errore"
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT COUNT(*),SUM(LENGTH(COALESCE(result_json,''))) FROM filing_runs").fetchone()
    client.get("/filings/TEST", headers=headers)
    client.get(f"/filings/runs/{run_id}", headers=headers)
    with sqlite3.connect(path) as conn:
        after = conn.execute("SELECT COUNT(*),SUM(LENGTH(COALESCE(result_json,''))) FROM filing_runs").fetchone()
    assert after == before


def test_corrupt_archive_is_503_not_missing_or_conflict(tmp_path):
    client, _, service = _client(tmp_path)
    service.store.get_profile = lambda *_: (_ for _ in ()).throw(sqlite3.DatabaseError("malformed fixture"))
    headers = {"X-BB-Token": "test"}
    response = client.get("/filings/TEST", headers=headers)
    assert response.status_code == 503 and "malformed fixture" in response.text
