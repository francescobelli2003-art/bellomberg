"""Persisted source notifications never acquire sources or call a proposer."""

from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace

import pytest

from test_valuation_automation import TICKER, automation as _automation_fixture


@pytest.fixture
def automation(tmp_path, monkeypatch):
    return _automation_fixture.__wrapped__(tmp_path, monkeypatch)


def _track_and_migrate(manager):
    from bellomberg.storage.filing_store import ensure_schema

    with manager.versions.db._conn() as conn:
        ensure_schema(conn)
        conn.execute("INSERT INTO positions(ticker,quantita,is_active) VALUES(?,?,1)", (TICKER, 3))
        conn.execute("INSERT INTO favorite_companies(ticker,name) VALUES(?,?)", (TICKER, "Synthetic"))


def _filing(manager, raw=b"Synthetic annual filing A", *, filed_date="2026-02-10",
            latest_unverified=False, ambiguous=False, broken_path=False, unverified_newer=False,
            run_status="parziale"):
    archive = manager.runtime.archive_root
    archive.mkdir(parents=True, exist_ok=True)
    path = archive / (sha256(raw).hexdigest() + ".txt")
    path.write_bytes(raw)
    candidate = {
        "fonte": "IR", "url": "https://issuer.example/filing.html", "stato": "verificato",
        "filed_date": filed_date, "path": str(path if not broken_path else archive.parent / "outside.txt"),
        "sha256": sha256(raw).hexdigest(),
        "metadati": {"emittente_id": "EMITTENTE:SYNTH", "lingua": "en",
                     "tipo": "annuale", "perimetro": "consolidated",
                     "periodo_inizio": "2025-01-01", "periodo_fine": "2025-12-31"},
    }
    result = {"ticker": TICKER, "src": "filing_pipeline", "stato": "parziale",
              "ultimo_non_verificato": latest_unverified, "candidati": [candidate]}
    if ambiguous:
        result["candidati"].append({"stato": "versione_ambigua", "report_date": "2025-12-31"})
    if unverified_newer:
        result["candidati"].append({"stato": "non_verificato", "report_date": "2026-06-30"})
    profile = {key: candidate["metadati"][key] for key in
               ("emittente_id", "lingua", "tipo", "perimetro")}
    with manager.versions.db._conn() as conn:
        conn.execute("""INSERT INTO filing_runs
            (ticker,profile_version,profile_sha256,profile_json,qualitative_enabled,
             judgment_language,trigger,started_at,finished_at,status,result_json)
             VALUES(?,?,?,?,0,'en','fixture','2026-02-10','2026-02-10',?,?)""",
             (TICKER, 1, "synthetic-profile", json.dumps(profile), run_status, json.dumps(result)))
    return result


def _enable_guidance(policy_path):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["triggers"].append("guidance")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")


def _guidance(manager, value=0.12, *, source_doc="Synthetic issuer FY outlook"):
    with manager.versions.db._conn() as conn:
        return conn.execute("""INSERT INTO company_guidance
            (ticker,metric,period,value_low,value_mid,value_high,unit,source_doc,
             source_date,effective_date,valid_until,valid_until_source,status,note,created_at)
             VALUES(?,'revenue_growth','FY2027',?,?,?,?,?,'2026-09-01',
                    '2026-09-01','2026-12-31','issuer calendar','active','registration note','2026-09-01')""",
             (TICKER, value - .01, value, value + .01, "pct", source_doc)).lastrowid


def _outcome(report, trigger):
    return next(row for row in report["outcomes"] if row["ticker"] == TICKER
                and row["trigger"] == trigger)


def test_filing_event_identity_uses_verified_content_not_run_path_or_alias(automation, tmp_path):
    from bellomberg.valuation.valuation_source_events import event_key, reconcile_source_events

    manager, observed, _, _ = automation
    _track_and_migrate(manager)
    first_result = _filing(manager)
    first = _outcome(reconcile_source_events(manager), "filing_diff")
    assert first["status"] == "queued" and first["reused"] is False
    assert manager.jobs.get(first["id"])["request"]["filing_results"] == [first_result]
    same = _outcome(reconcile_source_events(manager), "filing_diff")
    assert same["id"] == first["id"] and same["reused"] is True

    alias = deepcopy(first_result)
    alias["candidati"][0]["url"] = "https://issuer.example/alias.html"
    alias["candidati"][0]["path"] = str(tmp_path / "moved" / "filing.txt")
    alias["candidati"].append({**alias["candidati"][0], "stato": "duplicato"})
    assert event_key(alias) == event_key(first_result)
    _filing(manager)  # A later run with unchanged bytes still addresses the same event.
    assert _outcome(reconcile_source_events(manager), "filing_diff")["id"] == first["id"]

    _filing(manager, b"Synthetic annual filing B")
    changed = _outcome(reconcile_source_events(manager), "filing_diff")
    assert changed["status"] == "queued" and changed["id"] != first["id"]
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]


def test_guidance_active_set_changes_key_but_registration_log_does_not(automation):
    from bellomberg.valuation.valuation_source_events import reconcile_source_events

    manager, observed, policy_path, _ = automation
    _track_and_migrate(manager)
    _enable_guidance(policy_path)
    old_id = _guidance(manager)
    first = _outcome(reconcile_source_events(manager), "guidance")
    assert first["status"] == "queued"
    with manager.versions.db._conn() as conn:
        conn.execute("UPDATE company_guidance SET note=?,created_at=? WHERE id=?",
                     ("Edited registration log", "2026-09-09", old_id))
    unchanged = _outcome(reconcile_source_events(manager), "guidance")
    assert unchanged["id"] == first["id"] and unchanged["reused"] is True
    with manager.versions.db._conn() as conn:
        conn.execute("UPDATE company_guidance SET status='superseded' WHERE id=?", (old_id,))
    _guidance(manager, .15)
    second = _outcome(reconcile_source_events(manager), "guidance")
    assert second["id"] != first["id"]
    assert manager.jobs.get(second["id"])["request"]["filing_results"] == []
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]


def test_failed_filing_run_with_documented_verified_result_remains_eligible(automation):
    from bellomberg.valuation.valuation_source_events import reconcile_source_events

    manager, _, _, _ = automation
    _track_and_migrate(manager)
    _filing(manager, run_status="errore")  # Later indexing failure; pipeline result survives.
    event = _outcome(reconcile_source_events(manager), "filing_diff")
    assert event["status"] == "queued"


def test_policy_off_does_not_open_missing_database(tmp_path):
    from bellomberg.valuation.valuation_source_events import reconcile_source_events

    db_path = tmp_path / "not-created.db"
    manager = SimpleNamespace(
        runtime=SimpleNamespace(status=lambda: {"status": "disabled", "reason": "configuration_absent"}),
        jobs=SimpleNamespace(db_path=db_path))
    assert reconcile_source_events(manager)["status"] == "disabled"
    assert not db_path.exists()


@pytest.mark.parametrize("problem", ["latest_unverified", "unverified_newer_unflagged",
                                    "ambiguous", "missing_date", "bad_path", "tampered_bytes"])
def test_filing_gap_blocks_event_and_is_declared(automation, problem):
    from bellomberg.valuation.valuation_source_events import reconcile_source_events

    manager, observed, _, _ = automation
    _track_and_migrate(manager)
    result = _filing(manager, filed_date="" if problem == "missing_date" else "2026-02-10",
                     latest_unverified=problem == "latest_unverified", ambiguous=problem == "ambiguous",
                     broken_path=problem == "bad_path",
                     unverified_newer=problem == "unverified_newer_unflagged")
    if problem == "tampered_bytes":
        from pathlib import Path
        Path(result["candidati"][0]["path"]).write_bytes(b"Different archived bytes")
    report = reconcile_source_events(manager)
    assert report["status"] == "partial"
    filing = _outcome(report, "filing_diff")
    assert filing["status"] == "blocked" and filing["reason"]
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]
    with sqlite3.connect(manager.jobs.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM valuation_jobs").fetchone()[0] == 0


def test_missing_source_schema_and_malformed_guidance_are_visible(automation):
    from bellomberg.valuation.valuation_source_events import reconcile_source_events

    manager, _, policy_path, _ = automation
    with manager.versions.db._conn() as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,is_active) VALUES(?,?,1)", (TICKER, 1))
        conn.execute("DROP TABLE filing_runs")  # Deliberately incompatible disposable DB.
    _enable_guidance(policy_path)
    _guidance(manager, source_doc="")
    report = reconcile_source_events(manager)
    assert report["status"] == "partial"
    assert any(row["source"] == "filing_diff" for row in report["sources"]), report
    assert _outcome(report, "guidance")["status"] == "blocked"


def test_conflicting_active_guidance_and_missing_guidance_schema_fail_closed(automation):
    from bellomberg.valuation.valuation_source_events import reconcile_source_events

    manager, observed, policy_path, _ = automation
    _track_and_migrate(manager)
    _enable_guidance(policy_path)
    _guidance(manager, .12)
    _guidance(manager, .15)
    conflicted = reconcile_source_events(manager)
    assert _outcome(conflicted, "guidance")["status"] == "blocked"
    assert "conflicting active guidance" in _outcome(conflicted, "guidance")["reason"]
    with manager.versions.db._conn() as conn:
        conn.execute("DROP TABLE company_guidance")  # Only this disposable test DB.
    missing = reconcile_source_events(manager)
    assert missing["status"] == "partial"
    assert any(row["source"] == "guidance" for row in missing["sources"])
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]


@pytest.mark.parametrize("later_status", ["errore", "skipped", "non_disponibile"])
def test_latest_finished_filing_run_without_result_blocks_older_proof(automation, later_status):
    from bellomberg.valuation.valuation_source_events import reconcile_source_events

    manager, observed, _, _ = automation
    _track_and_migrate(manager)
    _filing(manager)
    first = _outcome(reconcile_source_events(manager), "filing_diff")
    assert first["status"] == "queued"
    with manager.versions.db._conn() as conn:
        conn.execute("""INSERT INTO filing_runs
            (ticker,profile_version,profile_sha256,profile_json,qualitative_enabled,
             judgment_language,trigger,started_at,finished_at,status,result_json)
             VALUES(?,?,?,?,0,'en','fixture','2026-03-10','2026-03-10',?,NULL)""",
             (TICKER, 1, "synthetic-profile", "{}", later_status))
    later = _outcome(reconcile_source_events(manager), "filing_diff")
    assert later["status"] == "blocked" and "without result" in later["reason"]
    with sqlite3.connect(manager.jobs.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM valuation_jobs").fetchone()[0] == 1
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]


def test_expired_active_guidance_blocks_refresh(automation):
    from bellomberg.valuation.valuation_source_events import reconcile_source_events

    manager, observed, policy_path, _ = automation
    _track_and_migrate(manager)
    _enable_guidance(policy_path)
    row_id = _guidance(manager)
    with manager.versions.db._conn() as conn:
        conn.execute("UPDATE company_guidance SET valid_until='2026-09-09' WHERE id=?", (row_id,))
    result = reconcile_source_events(manager)
    guidance = _outcome(result, "guidance")
    assert guidance["status"] == "blocked" and "stale" in guidance["reason"]
    assert result["status"] == "partial"
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]
