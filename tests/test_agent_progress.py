"""Progress ledger on synthetic SQLite only; no scoring fetch or LLM calls."""
import copy
import json
import sqlite3
from datetime import datetime

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from bellomberg.agents import score_history as history
from bellomberg.api.agent_progress_routes import create_agent_progress_router


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "progress.sqlite"
    with sqlite3.connect(path) as conn:
        for sql in history.SCORE_HISTORY_MIGRATION[2]:
            conn.execute(sql)
    return path


def score(rate=50, ident=1):
    return {"computed_at": datetime.now().isoformat(timespec="seconds"),
            "window_days": 270, "method_note": "synthetic method", "degraded": False,
            "overall": {"n": 2, "hits": int(rate / 50), "hit_rate_pct": rate, "avg_edge_pct": 1},
            "by_specialist": {"quant": {"n": 2, "hits": int(rate / 50), "hit_rate_pct": rate,
                                          "avg_edge_pct": 1}},
            "n_directional_candidates": 2, "n_unmeasurable": 0, "n_fetch_fail": 0,
            "details": [{"id": ident, "horizon_used": "4w", "direction": "long", "specialists": ["quant"]},
                        {"id": 2, "horizon_used": "4w", "direction": "long", "specialists": ["quant"]}]}


def save(db, run="memo:1", sc=None, **kwargs):
    return history.save_run_snapshot(db, run_id=run, memo_id=int(run.split(":")[1]),
                                     started_at=datetime.now().isoformat(), completed_at=datetime.now().isoformat(),
                                     scorecard=score() if sc is None else sc, **kwargs)


def test_duplicate_run_is_immutable(db):
    first = score()
    before = copy.deepcopy(first)
    assert save(db, sc=first)["saved"] is True
    assert first == before
    assert save(db, sc=score(70), lesson="changed")["saved"] is False
    rows = history.read_history(db)
    assert len(rows) == 1
    assert rows[0]["scorecard"]["overall"]["hit_rate_pct"] == 50
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE agent_score_history SET payload_json = '{}' WHERE run_id='memo:1'")


def test_absent_schema_get_does_not_create_it(tmp_path):
    path = tmp_path / "empty.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sentinel (value TEXT)")
    with pytest.raises(history.HistoryUnavailable, match="schema"):
        history.read_history(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("sentinel",)]
    missing = tmp_path / "absent.sqlite"
    with pytest.raises(history.HistoryUnavailable):
        history.read_history(missing)
    assert not missing.exists()


def test_missing_and_nonfinite_scores_are_not_zero(db):
    sc = score()
    sc["by_specialist"]["quant"]["hit_rate_pct"] = float("nan")
    save(db, sc=sc)
    payload = history.progress_payload(history.read_history(db))
    quant = next(a for a in payload["agents"] if a["id"] == "quant")
    options = next(a for a in payload["agents"] if a["id"] == "options")
    assert quant["latest"]["hit_rate_pct"] is None
    assert options["latest"]["n"] is None
    assert options["latest"]["hit_rate_pct"] is None
    assert "invalid" in quant["latest"]["quality"]
    json.dumps(payload, allow_nan=False)


def test_deltas_require_same_cohort_and_method(db):
    save(db)
    save(db, "memo:2", score(100))
    payload = history.progress_payload(history.read_history(db))
    quant = next(a for a in payload["agents"] if a["id"] == "quant")
    assert quant["delta"]["available"] is True
    assert quant["delta"]["hit_rate_pp"] == 50
    save(db, "memo:3", score(100, ident=3))
    quant = next(a for a in history.progress_payload(history.read_history(db))["agents"] if a["id"] == "quant")
    assert quant["delta"]["available"] is False
    assert quant["delta"]["hit_rate_pp"] is None


def test_degraded_snapshot_preserves_hole_and_lesson_not_performance(db):
    sc = score()
    sc["degraded"] = True
    save(db, sc=sc, lesson="Verificare le ipotesi", reflection_status="generated")
    p = history.progress_payload(history.read_history(db))
    assert p["runs"][0]["reflection"]["kind"] == "suggestion"
    assert p["runs"][0]["reflection"]["implementation_verified"] is False
    assert p["agents"][0]["latest"]["hit_rate_pct"] is None


def test_endpoint_auth_and_no_paid_or_write_actions(db):
    save(db)
    def auth(request: Request):
        if request.headers.get("X-BB-Token") != "test":
            raise HTTPException(401, "login")
    app = FastAPI()
    app.include_router(create_agent_progress_router(auth, db_path=db))
    with TestClient(app) as client:
        assert client.get("/agents/progress").status_code == 401
        response = client.get("/agents/progress", headers={"X-BB-Token": "test"})
        assert response.status_code == 200
        assert response.json()["history"]["count"] == 1
        assert response.json()["paid_analysis"] is False
        assert client.post("/agents/progress", headers={"X-BB-Token": "test"}).status_code == 405


@pytest.mark.parametrize("change,expected", [
    ({"computed_at": "2001-01-01T00:00:00"}, "stale"),
    ({"computed_at": "2099-01-01T00:00:00"}, "time_unknown"),
    ({"computed_at": None}, "time_unknown"),
    ({"n_fetch_fail": None}, "quality_unknown"),
    ({"n_fetch_fail": 1}, "partial"),
    ({"details": []}, "cohort_unverified"),
])
def test_incomparable_quality_is_explicit(db, change, expected):
    save(db)
    changed = score(100)
    changed.update(change)
    save(db, "memo:2", changed)
    agent = history.progress_payload(history.read_history(db))["agents"][0]
    assert expected in agent["latest"]["quality"]
    assert agent["delta"]["available"] is False


def test_inconsistent_hit_rate_is_not_shown(db):
    sc = score()
    sc["overall"]["hit_rate_pct"] = 90
    save(db, sc=sc)
    agent = history.progress_payload(history.read_history(db))["agents"][0]
    assert agent["latest"]["hit_rate_pct"] is None
    assert "invalid" in agent["latest"]["quality"]


def test_schema_is_versioned_and_save_does_not_create_it(tmp_path):
    path = tmp_path / "missing-schema.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sentinel (value TEXT)")
    with pytest.raises(history.HistoryUnavailable, match="schema"):
        save(path)
    assert history.SCORE_HISTORY_MIGRATION[0] == 9
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("sentinel",)]


def test_no_history_does_not_backdate_current_snapshot(db):
    current = score()
    current["available"] = True
    current["maturation_days"] = 7
    p = history.progress_payload(history.read_history(db), current_scorecard=current)
    assert p["history"]["count"] == 0
    assert p["runs"] == []
    assert p["agents"][0]["latest"]["run_id"] is None
    assert p["agents"][0]["latest"]["hit_rate_pct"] == 50
    assert p["agents"][0]["delta"]["available"] is False


def test_completion_adapter_consumes_evidence_without_recalculation(db, monkeypatch):
    import threading
    from types import SimpleNamespace
    from bellomberg.agents import scorekeeper
    def forbidden(*args, **kwargs):
        pytest.fail("Completion must not recalculate or fetch scores")
    monkeypatch.setattr(scorekeeper, "compute_scorecard", forbidden)
    bb = SimpleNamespace(memo_id=4, _lock=threading.RLock(), start_time=datetime.now().isoformat(),
                         specialist_status={"quant": "done"}, usage_log=[{"agent": "quant", "model": "synthetic/model"}],
                         _usage_aggregates=lambda: ({"quant": {"status": "ok", "api_calls": 2}}, {"cost_eur": None}))
    assert history.record_completed_run(SimpleNamespace(db_path=db), bb, scorecard=score(), lesson="Test lesson")["saved"]
    rows = history.read_history(db)
    assert rows[0]["run_id"] == "memo:4"
    assert rows[0]["scorecard"]["overall"]["n"] == 2
    assert rows[0]["operational"]["models"] == {"quant": ["synthetic/model"]}
    assert rows[0]["reflection"]["implementation_verified"] is False
