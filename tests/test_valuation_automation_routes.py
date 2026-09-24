"""Read-only current-model API against disposable, explicitly migrated SQLite."""
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import json

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


def _client(path, roots, **actions):
    from bellomberg.api.valuation_automation_routes import create_valuation_automation_router
    app = FastAPI()
    def require_session(request: Request):
        if request.headers.get("x-test-session") != "allowed":
            raise HTTPException(401, "session required")
    app.include_router(create_valuation_automation_router(
        require_session, db_provider=lambda: path, roots=roots, **actions))
    return TestClient(app)


def _get(client, url):
    return client.get(url, headers={"x-test-session": "allowed"})


def _post(client, url, body):
    return client.post(url, json=body, headers={"x-test-session": "allowed"})


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    from bellomberg.storage.valuation_jobs import ValuationJobStore, ensure_schema as jobs_schema
    from bellomberg.storage.valuation_versions import ValuationVersions, ensure_schema as versions_schema
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    db = memory_db.MemoryDB(str(tmp_path / "synthetic.db"), str(tmp_path / "chroma"))
    with db._conn() as conn:
        jobs_schema(conn)
        versions_schema(conn)
    root = tmp_path / "models"
    root.mkdir()
    return db, ValuationVersions(db, roots=[root], clock=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc)), ValuationJobStore(db.db_path), root, _client(db.db_path, [root])


def _generated(db, root, *, incomplete=False):
    from bellomberg.valuation.preparation_service import prepare_and_generate
    from test_input_preparation import _bundle, _documents, _propose_operating
    result = prepare_and_generate(_bundle(), documents=[] if incomplete else _documents(),
                                  propose=_propose_operating, output_dir=root)
    assert db.save_valuation_thesis(result["ticker"], valuation_payload=result) is not None
    return result


def _finished_job(jobs, ticker, kind, evidence_key, request, status, result=None):
    queued = jobs.enqueue(ticker, kind, evidence_key, request)
    claimed = jobs.claim("synthetic-worker", lease_seconds=60)
    assert claimed["id"] == queued["id"]
    return jobs.finish(queued["id"], "synthetic-worker", claimed["lease_token"],
                       status=status, reason="synthetic " + status, result=result)


def test_actions_require_session_and_read_does_not_create_variant_directory(environment):
    db, _, _, root, _ = environment
    personal = root / "personal"
    client = _client(db.db_path, [root], variants_root=personal)
    for suffix in ("refresh", "lock", "variants"):
        assert client.post("/valuation/models/SYNTH-EXT/" + suffix, json={}).status_code == 401
    assert _get(client, "/valuation/models/SYNTH-EXT").status_code == 200
    assert not personal.exists()
    assert _get(client, "/valuation/models/SYNTH-EXT/variants").json()["detail"]["code"] == "not_migrated"


def test_legacy_download_cannot_bypass_session_version_route_or_hash(environment):
    import ast
    import os
    import re
    db, _, _, root, _ = environment
    source = Path("src/bellomberg/api/bellomberg_api.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)
                and n.name == "download_valuation_model")
    assert "Depends(require_session)" in ast.unparse(node.decorator_list[0])
    node.decorator_list = []
    space = {"os": os, "re": re, "json": json, "HTTPException": HTTPException,
             "_val_dirs": lambda: [str(root)], "_api_text": lambda italian, english: italian}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "legacy-model-download", "exec"), space)
    app = FastAPI()
    def require(request: Request):
        if request.headers.get("x-test-session") != "allowed":
            raise HTTPException(401, "session required")
    from fastapi import Depends
    app.get("/legacy/{name}", dependencies=[Depends(require)])(space["download_valuation_model"])
    client = TestClient(app)
    path = root / "VAL_OLD.xlsx"
    path.write_bytes(b"synthetic historical copy")
    assert client.get("/legacy/VAL_OLD.xlsx").status_code == 401
    historical = _get(client, "/legacy/VAL_OLD.xlsx")
    assert historical.content == path.read_bytes() and historical.headers["x-valuation-copy"] == "historical"
    path.with_suffix(".payload.json").write_text(json.dumps({"workbook_sha256": "0" * 64}), encoding="utf-8")
    assert _get(client, "/legacy/VAL_OLD.xlsx").status_code == 409
    current = _generated(db, root)
    assert _get(client, "/legacy/" + Path(current["path"]).name).status_code == 409
    Path(current["path"]).with_suffix(".payload.json").unlink()
    assert _get(client, "/legacy/" + Path(current["path"]).name).status_code == 409


def test_manual_refresh_is_authorized_and_idempotent_without_inline_research(tmp_path, monkeypatch):
    from test_valuation_automation import automation as automation_fixture, TICKER
    manager, observed, policy_path, _ = automation_fixture.__wrapped__(tmp_path, monkeypatch)
    client = _client(manager.jobs.db_path, manager.versions.roots, automation_provider=lambda: manager)
    body = {"request_id": "12345678-1234-4234-8234-123456789012"}
    url = "/valuation/models/" + TICKER + "/refresh"
    assert _post(client, url, body).status_code == 403
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["triggers"].append("manual_refresh")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    first = _post(client, url, body)
    repeated = _post(client, url, body)
    assert first.status_code == repeated.status_code == 202
    assert first.json()["id"] == repeated.json()["id"]
    assert repeated.json()["reused"] is True
    assert not observed["paid"] and not observed["acquire"] and not observed["collect"]
    assert _post(client, url, {**body, "max_tokens": 1}).status_code == 422
    assert _post(client, url, {"request_id": "invalid"}).status_code == 422


def test_lock_rejects_stale_generation_and_holds_next_publication(environment):
    db, versions, _, root, client = environment
    current = _generated(db, root)
    versions.publish(current["snapshot_id"], current["generation_id"], expected_current_generation=None)
    url = "/valuation/models/SYNTH-EXT/lock"
    old = "12345678-1234-4234-8234-123456789012"
    assert _post(client, url, {"locked": True, "generation_id": old}).status_code == 409
    assert versions.current("SYNTH-EXT")["locked"] == 0
    body = {"locked": True, "generation_id": current["generation_id"]}
    assert _post(client, url, {**body, "locked": "true"}).status_code == 422
    assert _post(client, url, body).status_code == 200
    following = _generated(db, root)
    assert versions.publish(following["snapshot_id"], following["generation_id"],
        expected_current_generation=current["generation_id"])["status"] == "held"
    assert _post(client, url, {**body, "locked": False}).status_code == 200
    assert versions.current("SYNTH-EXT")["locked"] == 0


def test_personal_variant_keeps_lineage_edits_and_original_separate(environment):
    from bellomberg.storage.valuation_variants import ensure_schema, PersonalVariants
    db, versions, _, root, _ = environment
    with db._conn() as conn:
        ensure_schema(conn)
    current = _generated(db, root)
    versions.publish(current["snapshot_id"], current["generation_id"], expected_current_generation=None)
    personal = root / "personal"
    client = _client(db.db_path, [root], variants_root=personal)
    url = "/valuation/models/SYNTH-EXT/variants"
    body = {"request_id": "12345678-1234-4234-8234-123456789012",
            "generation_id": current["generation_id"], "label": "Synthetic personal case"}
    first = _post(client, url, body)
    assert first.status_code == 201, first.text
    row = first.json()
    assert row["status"] == "ready" and row["source_generation"] == current["generation_id"]
    assert "path" not in row
    saved = PersonalVariants(versions, personal).get(row["id"])
    path = Path(saved["path"])
    source = Path(current["path"]).read_bytes()
    with path.open("ab") as stream:
        stream.write(b"synthetic-personal-change")
    repeated = _post(client, url, body)
    assert repeated.json()["modified"] is True
    assert path.read_bytes() == source + b"synthetic-personal-change"
    assert Path(current["path"]).read_bytes() == source
    listing = _get(client, url).json()
    assert listing["variants"][0]["modified"] is True and len(listing["variants"]) == 1
    download = _get(client, url + "/" + row["id"] + "/workbook")
    assert download.content == path.read_bytes()
    assert download.headers["x-valuation-generation"] == current["generation_id"]
    assert _get(client, url.replace("SYNTH-EXT", "WRONG") + "/" + row["id"] + "/workbook").status_code == 404
    stale = {**body, "generation_id": "12345678-1234-4234-8234-123456789013"}
    assert _post(client, url, stale).status_code == 409
    assert _post(client, url, {**body, "path": str(root)}).status_code == 422


def test_f17_keeps_published_model_when_newer_snapshot_fails(environment):
    from test_sector_valuation_api import endpoint
    db, versions, jobs, root, client = environment
    current = _generated(db, root)
    versions.publish(current["snapshot_id"], current["generation_id"], expected_current_generation=None)
    failed = _generated(db, root, incomplete=True)
    versions.publish(failed["snapshot_id"], failed["generation_id"],
                     expected_current_generation=current["generation_id"])
    listing = endpoint(root, model_db=db)
    shown = listing["models"][0]
    assert shown["current_generation"] and shown["canonical"]
    assert shown["generation_id"] == current["generation_id"]
    assert shown["fair_value"] == current["fair_value_base"]
    assert shown["automation"]["latest_publication_attempt"]["status"] == "incomplete"
    response = _get(client, shown["current_download"])
    assert response.status_code == 200
    assert response.content == Path(current["path"]).read_bytes()


def test_current_state_keeps_valid_version_and_separates_failed_refresh_and_price(environment):
    from bellomberg.api.valuation_automation_routes import current_model_state
    db, versions, jobs, root, client = environment
    current = _generated(db, root)
    assert versions.publish(current["snapshot_id"], current["generation_id"],
                            expected_current_generation=None)["status"] == "published"
    failed = _generated(db, root, incomplete=True)
    assert versions.publish(failed["snapshot_id"], failed["generation_id"],
                            expected_current_generation=current["generation_id"])["status"] == "incomplete"
    _finished_job(jobs, current["ticker"], "prepare", "failed-refresh", {}, "failed")
    _finished_job(jobs, current["ticker"], "reprice", "old-price",
                  {"generation_id": current["generation_id"], "as_of": "2026-09-10"},
                  "succeeded", {"market_quote": {"status": "ok", "price": 10}})
    _finished_job(jobs, current["ticker"], "reprice", "new-price",
                  {"generation_id": current["generation_id"], "as_of": "2026-09-11"}, "failed")

    response = _get(client, "/valuation/models/SYNTH-EXT")
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["current"]["generation_id"] == current["generation_id"]
    assert state["artifact"]["available"] is True
    assert state["latest_publication_attempt"]["generation_id"] == failed["generation_id"]
    assert state["latest_publication_attempt"]["status"] == "incomplete"
    assert state["latest_prepare_job"]["status"] == "failed"
    assert state["latest_price_job"]["status"] == "failed"
    assert state["latest_price_job"].get("market_quote") is None
    assert "request" not in str(state) and "checkpoint" not in str(state)
    assert current_model_state(db.db_path, roots=[root], ticker="SYNTH-EXT") == state
    assert _get(client, "/valuation/models/SYNTH-EXT/generations/" + failed["generation_id"]
                + "/workbook").status_code == 409
    download = _get(client, "/valuation/models/SYNTH-EXT/generations/"
                    + current["generation_id"] + "/workbook")
    assert download.status_code == 200
    assert download.content == Path(current["path"]).read_bytes()
    assert sha256(download.content).hexdigest() == current["workbook_sha256"]
    assert download.headers["cache-control"] == "no-store"
    assert _get(client, "/valuation/models/SYNTH-EXT").json()["current"]["generation_id"] == current["generation_id"]


@pytest.mark.parametrize("damage", ["changed", "missing"])
def test_changed_or_missing_current_workbook_is_not_downloaded(environment, damage):
    db, versions, _, root, client = environment
    current = _generated(db, root)
    versions.publish(current["snapshot_id"], current["generation_id"], expected_current_generation=None)
    path = Path(current["path"])
    if damage == "changed":
        path.write_bytes(path.read_bytes() + b"synthetic personal edit")
    else:
        path.unlink()
    state = _get(client, "/valuation/models/SYNTH-EXT")
    assert state.status_code == 200 and state.json()["artifact"]["available"] is False
    assert state.json()["current"]["generation_id"] == current["generation_id"]
    response = _get(client, "/valuation/models/SYNTH-EXT/generations/"
                    + current["generation_id"] + "/workbook")
    assert response.status_code == 409


def test_superseded_generation_link_cannot_download_the_old_workbook(environment):
    db, versions, _, root, client = environment
    original = _generated(db, root)
    versions.publish(original["snapshot_id"], original["generation_id"],
                     expected_current_generation=None)
    replacement = _generated(db, root)
    assert versions.publish(replacement["snapshot_id"], replacement["generation_id"],
                            expected_current_generation=original["generation_id"])["status"] == "published"
    assert Path(original["path"]).is_file()  # history remains on disk
    assert _get(client, "/valuation/models/SYNTH-EXT/generations/"
                + original["generation_id"] + "/workbook").status_code == 409
    assert _get(client, "/valuation/models/SYNTH-EXT/generations/"
                + replacement["generation_id"] + "/workbook").status_code == 200


def test_workbook_changed_after_manifest_check_is_still_refused(environment, monkeypatch):
    from bellomberg.storage.valuation_versions import ValuationVersions
    db, versions, _, root, client = environment
    current = _generated(db, root)
    versions.publish(current["snapshot_id"], current["generation_id"],
                     expected_current_generation=None)
    original = ValuationVersions.current
    def change_after_check(self, ticker):
        view = original(self, ticker)
        if view and view["artifact"]["available"]:
            path = Path(view["current"]["path"])
            path.write_bytes(path.read_bytes() + b"synthetic concurrent change")
        return view
    monkeypatch.setattr(ValuationVersions, "current", change_after_check)
    response = _get(client, "/valuation/models/SYNTH-EXT/generations/"
                    + current["generation_id"] + "/workbook")
    assert response.status_code == 409


def test_unmigrated_or_missing_database_is_declared_without_side_effects(tmp_path):
    database = tmp_path / "unmigrated.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE sentinel(value TEXT)")
        conn.execute("INSERT INTO sentinel VALUES('unchanged')")
    before = database.read_bytes()
    client = _client(database, [tmp_path])
    response = _get(client, "/valuation/models/SYNTH-EXT")
    assert response.status_code == 503 and response.json()["detail"]["code"] == "not_migrated"
    assert database.read_bytes() == before
    missing = tmp_path / "absent.db"
    response = _get(_client(missing, [tmp_path]), "/valuation/models/SYNTH-EXT")
    assert response.status_code == 503 and not missing.exists()


def test_unpublished_or_outside_root_artifact_cannot_be_downloaded(environment, tmp_path):
    db, versions, _, root, client = environment
    outside = tmp_path / "outside"
    candidate = _generated(db, outside)
    assert versions.publish(candidate["snapshot_id"], candidate["generation_id"],
                            expected_current_generation=None)["status"] == "artifact_failed"
    state = _get(client, "/valuation/models/SYNTH-EXT")
    assert state.status_code == 200 and state.json()["status"] == "no_current"
    assert state.json()["current"] is None
    response = _get(client, "/valuation/models/SYNTH-EXT/generations/"
                    + candidate["generation_id"] + "/workbook")
    assert response.status_code == 404
    assert _get(client, "/valuation/models/../").status_code in (404, 422)
    assert client.get("/valuation/models/SYNTH-EXT").status_code == 401
