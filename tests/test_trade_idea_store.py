"""Trade Idea decisions and cost/delivery receipts use only temporary SQLite."""
import hashlib
import json
import sqlite3

import pytest

from bellomberg.storage.trade_idea_store import (
    BudgetBlocked, IdempotencyConflict, RunConflict, TradeIdeaStore,
)
from tools.migrations import migra_trade_idea


BASE_SCHEMA = """
CREATE TABLE positions(ticker TEXT PRIMARY KEY, quantita REAL NOT NULL,
                       prezzo_medio REAL, valuta TEXT, is_active INTEGER NOT NULL,
                       last_updated TEXT);
CREATE TABLE position_prices(id INTEGER PRIMARY KEY, ticker TEXT NOT NULL,
                             prezzo REAL NOT NULL, valuta TEXT, source TEXT, timestamp TEXT);
CREATE TABLE cash_state(singleton_id INTEGER PRIMARY KEY, balance_cents INTEGER,
                        version INTEGER);
INSERT INTO cash_state(singleton_id,balance_cents,version) VALUES(1,1000000,1);
CREATE TABLE trade_history(id INTEGER PRIMARY KEY, ticker TEXT, action TEXT,
                           quantita REAL, prezzo REAL, valuta TEXT, data TEXT,
                           linked_decision_id INTEGER, pm_rationale TEXT, note TEXT);
CREATE TABLE memos(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, title TEXT,
                   full_markdown TEXT, pdf_path TEXT, appendix_path TEXT, dcf_files TEXT,
                   notes TEXT, output_language TEXT);
CREATE TABLE decisions(id INTEGER PRIMARY KEY, memo_id INTEGER, timestamp TEXT NOT NULL,
                       action TEXT NOT NULL, ticker TEXT, eur_amount REAL, timing TEXT,
                       confidence TEXT, rationale TEXT, status TEXT DEFAULT 'PENDING',
                       pm_feedback TEXT, veto INTEGER DEFAULT 0, veto_reason TEXT,
                       veto_at TEXT, veto_revoked_at TEXT, outcome_pct REAL,
                       outcome_eur REAL, outcome_notes TEXT, closed_at TEXT,
                       archive_override INTEGER);
CREATE TABLE decision_notes(id INTEGER PRIMARY KEY, decision_id INTEGER, autore TEXT,
                            testo TEXT, timestamp TEXT);
CREATE TABLE pm_feedback(id INTEGER PRIMARY KEY, decision_id INTEGER,
                         feedback_text TEXT, sentiment TEXT);
CREATE TABLE valuation_theses(id INTEGER PRIMARY KEY, ticker TEXT, date TEXT,
                             price_at_thesis REAL, fair_value REAL, growth_path TEXT,
                             ebitda_margin_target REAL, terminal_growth REAL,
                             variant_view TEXT, engine TEXT, subsector TEXT, memo_id INTEGER);
INSERT INTO decisions(id,timestamp,action,ticker,rationale,status)
VALUES(1,'2026-09-01T10:00:00','RESEARCH','OTHER','legacy untouched','PENDING');
"""


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "isolated.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(BASE_SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
    return path


@pytest.fixture
def migrated(db_path, monkeypatch):
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    receipt = migra_trade_idea.migra(db_path, apply=True)
    assert receipt["applicazione"]["preesistente_invariato"]
    from bellomberg.storage.memory_db import VALUATION_SNAPSHOT_STATEMENTS
    with sqlite3.connect(db_path) as conn:
        for statement in VALUATION_SNAPSHOT_STATEMENTS:
            conn.execute(statement)
    return db_path


def store(path):
    return TradeIdeaStore(path, mandate_loader=lambda: "a" * 64)


@pytest.mark.parametrize("state", ["db_missing", "schema_absent", "schema_partial", "schema_incompatible"])
def test_storage_diagnosis_is_read_only_and_distinguishes_upgrade_from_wrong_path(db_path, state):
    from bellomberg.storage.trade_idea_store import ensure_schema
    from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
    target = db_path.with_name("missing.db") if state == "db_missing" else db_path
    with sqlite3.connect(db_path) as conn:
        if state == "schema_partial":
            ensure_schema(conn)
            conn.execute("DROP INDEX idx_trade_idea_one_active")
        elif state == "schema_incompatible":
            ensure_schema(conn)
            conn.execute("ALTER TABLE trade_idea_costs ADD COLUMN unexpected TEXT")
        before = fingerprint(conn), schema_fingerprint(conn)
    with pytest.raises((RuntimeError, FileNotFoundError)) as caught:
        store(target)
    diagnostic = getattr(caught.value, "storage", {})
    assert diagnostic.get("status") == state
    assert diagnostic["update_required"] is (state in ("schema_absent", "schema_partial"))
    assert diagnostic["documentation"] == "docs/TRADE_IDEA.md#aggiornamento-storage"
    assert diagnostic["action"] == ("run_explicit_migration" if state in
        ("schema_absent", "schema_partial") else "check_database_path" if state == "db_missing"
        else "inspect_schema")
    assert not db_path.with_name("missing.db").exists()
    with sqlite3.connect(db_path) as conn:
        assert (fingerprint(conn), schema_fingerprint(conn)) == before


def test_storage_current_schema_needs_no_watch_migration(migrated):
    assert store(migrated).list_runs()["runs"] == []


@pytest.mark.parametrize("foreign_table", [False, True])
def test_storage_empty_or_foreign_database_is_not_an_upgrade(tmp_path, foreign_table):
    path = tmp_path / "wrong.db"
    with sqlite3.connect(path) as conn:
        if foreign_table:
            conn.execute("CREATE TABLE unrelated(id INTEGER)")
    with pytest.raises(RuntimeError) as caught:
        store(path)
    assert caught.value.storage["status"] == "schema_incompatible"
    assert caught.value.storage["update_required"] is False
    assert caught.value.storage["action"] == "inspect_schema"


def test_storage_current_schema_accepts_watch_and_extra_indexes(migrated):
    from bellomberg.storage.trade_idea_watch_store import ensure_schema
    with sqlite3.connect(migrated) as conn:
        ensure_schema(conn)
        conn.execute("CREATE INDEX synthetic_extra ON trade_idea_runs(currency)")
    assert store(migrated).list_runs()["total"] == 0


@pytest.mark.parametrize("fault", ["corrupt", "access"])
def test_storage_unreadable_is_not_an_upgrade(tmp_path, monkeypatch, fault):
    path = tmp_path / "unreadable.db"
    path.write_bytes(b"synthetic invalid SQLite")
    if fault == "access":
        def denied(*args, **kwargs):
            raise PermissionError("synthetic denied path")
        monkeypatch.setattr(TradeIdeaStore, "_connect", denied)
    with pytest.raises(RuntimeError) as caught:
        store(path)
    assert caught.value.storage["status"] == "db_unreadable"
    assert caught.value.storage["update_required"] is False
    assert "synthetic denied path" not in str(caught.value)
    assert path.read_bytes() == b"synthetic invalid SQLite"


def test_partial_tables_upgrade_and_second_apply_preserve_existing_records(db_path, monkeypatch):
    from bellomberg.storage.trade_idea_store import SCHEMA
    from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
    with sqlite3.connect(db_path) as conn:
        conn.execute(SCHEMA[0])
        before = fingerprint(conn), schema_fingerprint(conn)
    with pytest.raises(RuntimeError) as caught:
        store(db_path)
    assert caught.value.storage["status"] == "schema_partial"
    assert migra_trade_idea.migra(db_path)["sorgente_invariata"] is True
    with sqlite3.connect(db_path) as conn:
        assert (fingerprint(conn), schema_fingerprint(conn)) == before
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    first = migra_trade_idea.migra(db_path, apply=True)
    with sqlite3.connect(db_path) as conn:
        after = fingerprint(conn), schema_fingerprint(conn)
    second = migra_trade_idea.migra(db_path, apply=True)
    assert first["backup"] != second["backup"]
    assert second["tabelle_mancanti"] == []
    assert second["applicazione"]["righe_cambiate"] == 0
    assert second["rilettura"]["preesistente_invariato"] is True
    with sqlite3.connect(db_path) as conn:
        assert (fingerprint(conn), schema_fingerprint(conn)) == after
    assert store(db_path).list_runs()["total"] == 0


def test_migration_refuses_live_backend_and_incompatible_schema_without_changes(db_path, monkeypatch):
    from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: True)
    with sqlite3.connect(db_path) as conn:
        before = fingerprint(conn), schema_fingerprint(conn)
    with pytest.raises(RuntimeError, match="8765"):
        migra_trade_idea.migra(db_path, apply=True)
    with sqlite3.connect(db_path) as conn:
        assert (fingerprint(conn), schema_fingerprint(conn)) == before
        conn.execute("CREATE TABLE trade_idea_runs(id TEXT)")
        before = fingerprint(conn), schema_fingerprint(conn)
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    for apply in (False, True):
        with pytest.raises(ValueError, match="incompatibile"):
            migra_trade_idea.migra(db_path, apply=apply)
    with sqlite3.connect(db_path) as conn:
        assert (fingerprint(conn), schema_fingerprint(conn)) == before
    assert not list(db_path.parent.glob("*.bak"))


def request(ticker="TEST"):
    return {"ticker": ticker, "company_name": "Synthetic Company", "exchange": "XNAS",
            "currency": "USD", "language": "it", "view_text": "My thesis to challenge",
            "view_origin": "manual",
            "models": {"specialist": {"model": "meta/muse-spark-1.3", "reasoning_effort": "max"},
                       "red_team": {"model": "meta/muse-spark-1.3", "reasoning_effort": "max"},
                       "capo": {"model": "anthropic/claude-opus-5.5", "reasoning_effort": "max"},
                       "aux": {"model": "meta/muse-spark-1.3", "reasoning_effort": "max"}},
            "catalog_snapshot": {"checked_at": "2026-09-27T20:00:00Z", "source": "fixture",
                                 "models": {"specialist": {"id": "meta/muse-spark-1.3"},
                                            "red_team": {"id": "meta/muse-spark-1.3"},
                                            "capo": {"id": "anthropic/claude-opus-5.5"},
                                            "aux": {"id": "meta/muse-spark-1.3"}}},
            "budget_limit_usd": "5.00", "cost_acknowledged": True,
            "source_qualification": {"status": "qualified", "fingerprint": "b" * 64,
                "method_id": "operating_fcff", "reasons": [], "coverage": {}},
            "authorization": {"accepted": True, "source_fingerprint": "b" * 64,
                "activities": ["model_preparation", "committee", "model_revision"],
                "max_revision_rounds": 1}}


def result(ticker="TEST", *, judgment="favorable", proposal=True, history=()):
    return {"ticker": ticker, "judgment": judgment,
            "summary": "Synthetic conclusion with evidence [src: fixture]",
            "pm_view_response": "The synthetic evidence challenges the PM view [src: fixture]",
            "pros": [], "cons": [], "risks": [], "catalysts": [], "invalidation": [],
            "data_gaps": [], "review_conditions": ["Review next primary filing"],
            "scenarios": [],
            "objections": [{"objection": "Synthetic risk", "response": "Synthetic answer",
                            "resolved": True, "evidence_ids": ["e1"]}],
            "evidence": [{"id": "e1", "source": "fixture", "as_of": "2026-09-27",
                          "summary": "Synthetic observation"}],
            "dossier": [{"key": "executive", "title": "Executive", "paragraphs": ["Synthetic analysis"],
                         "evidence_ids": ["e1"], "tables": [], "charts": []}],
            "proposal": ({"ticker": ticker, "action": "BUY", "eur_amount": 1000.0,
                          "timing": "After PM review", "confidence": "MEDIA",
                          "rationale": "Synthetic measured proposal [src: fixture]",
                          "sizing_source": "synthetic sizing fixture"} if proposal else None),
            "history_review": list(history)}


def all_checks():
    return {name: True for name in (
        "identity_verified", "evidence_sufficient", "red_team_complete", "capo_valid",
        "mandate_valid", "sizing_valid", "valuation_checked", "history_context_sent")}


def finish(s, run_id, payload, status="completed"):
    token = s.claim_run(run_id)
    return s.finish_run(run_id, token, payload, status)


def _save_resume_checkpoint(current, run_id, token):
    from bellomberg.storage.trade_idea_store import _digest
    checkpoint = {"version": 1, "contract": {"ticker": "TEST"},
                  "data": {"macro": {"0": "Paid report kept verbatim"}},
                  "tool_receipts": [{"tool": "fictional", "output": {"value": 12}}]}
    current.update_progress(run_id, token, "recon", {
        "checkpoint": checkpoint, "checkpoint_sha256": _digest(checkpoint)})
    return checkpoint


def test_native_continuation_is_single_successor_and_aggregates_original_costs(migrated):
    from concurrent.futures import ThreadPoolExecutor
    current = store(migrated)
    parent_id = current.create_run(request(), idempotency_key="native-parent")["run"]["id"]
    token = current.claim_run(parent_id)
    checkpoint = _save_resume_checkpoint(current, parent_id, token)
    current.reserve_cost(parent_id, "received-before-crash", "specialist:macro", "meta/muse-spark-1.3", "1")
    current.reconcile_cost(parent_id, "received-before-crash", charged_usd="0.40",
        usage={"cost_usd": "0.40"}, receipt={"response_id": "provider-1", "model": "meta/muse-spark-1.3"})
    current.interrupt_run(parent_id, reason="Synthetic process terminated after paid checkpoint")
    before = current.get_run(parent_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        children = list(pool.map(lambda key: current.create_continuation(parent_id,
            idempotency_key=key, authorize_new_requests=True), ("resume-a", "resume-b")))
    assert children[0]["run"]["id"] == children[1]["run"]["id"]
    assert sum(child["created"] for child in children) == 1
    child_id = children[0]["run"]["id"]
    assert current.get_run(child_id)["progress"]["checkpoint"] == checkpoint
    assert current.get_run(child_id)["cost"]["charged_usd"] == "0.40"
    assert current.get_run(child_id)["cost"]["requests"] == 1
    child_token = current.claim_run(child_id)
    with pytest.raises(RunConflict, match="worker claim"):
        current.update_progress(child_id, token, "wrong-owner", {})
    with pytest.raises(RunConflict, match="worker claim"):
        current.reserve_cost(child_id, "wrong-owner-request", "specialist:macro", "meta/muse-spark-1.3", "0.1",
                             worker_token=token)
    with pytest.raises(BudgetBlocked, match="limit"):
        current.reserve_cost(child_id, "too-expensive", "specialist:macro", "meta/muse-spark-1.3", "4.70")
    current.reserve_cost(child_id, "child-paid", "specialist:macro", "meta/muse-spark-1.3", "0.5")
    current.reconcile_cost(child_id, "child-paid", charged_usd="0.10", usage={"cost_usd": "0.10"},
                           receipt={"response_id": "provider-2", "model": "meta/muse-spark-1.3"})
    current.finish_run(child_id, child_token, None, "incomplete", reason="Next unfinished stage")
    second = current.create_continuation(child_id, idempotency_key="second-continuation", authorize_new_requests=True)
    assert second["cost"]["requests"] == 2
    assert second["cost"]["charged_usd"] == "0.50"
    assert second["cost"]["budget_limit_usd"] == "5.00"
    after = current.get_run(parent_id)
    for key in ("run", "progress", "result", "cost"):
        assert after[key] == before[key]
    with sqlite3.connect(migrated) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trade_idea_costs").fetchone()[0] == 2


def test_native_continuation_preserves_unknown_hold_and_refuses_dispatch(migrated):
    current = store(migrated)
    parent = current.create_run(request(), idempotency_key="unknown-parent")["run"]["id"]
    token = current.claim_run(parent)
    _save_resume_checkpoint(current, parent, token)
    current.reserve_cost(parent, "disconnected", "specialist:macro", "meta/muse-spark-1.3", "1.25")
    current.interrupt_run(parent, reason="Connection lost after dispatch")
    detail = current.get_run(parent)
    assert detail["cost"]["unknown_requests"] == 1
    assert detail["cost"]["unknown_reserved_usd"] == "1.25"
    assert detail["cost"]["remaining_known_usd"] is None
    assert detail["cost"]["remaining_after_holds_usd"] == "3.75"
    with pytest.raises(BudgetBlocked, match="unresolved"):
        current.create_continuation(parent, idempotency_key="blocked-resume", authorize_new_requests=True)
    assert current.list_runs()["total"] == 1


@pytest.mark.parametrize("state,block", [({"status": "truncated"}, "incomplete_response_review_required"),
                                        ({"inflight_tools": {"tool-1": {"name": "retrieve"}}}, "tool_outcome_unresolved")])
def test_native_continuation_exposes_unresolved_checkpoint_before_creating_child(migrated, state, block):
    from bellomberg.storage.trade_idea_store import _digest
    current = store(migrated)
    parent = current.create_run(request(), idempotency_key="checkpoint-block")["run"]["id"]
    token = current.claim_run(parent)
    checkpoint = _save_resume_checkpoint(current, parent, token)
    checkpoint["specialist_checkpoints"] = {"macro:R0": state}
    current.update_progress(parent, token, "checkpoint", {"checkpoint": checkpoint, "checkpoint_sha256": _digest(checkpoint)})
    current.finish_run(parent, token, None, "incomplete", reason="Explicit review required")
    assert current.get_run(parent)["recovery"]["blocked_reason"] == block
    assert current.get_run(parent)["recovery"]["can_continue"] is False
    with pytest.raises(RunConflict, match=block):
        current.create_continuation(parent, idempotency_key="must-not-create", authorize_new_requests=True)
    assert current.list_runs()["total"] == 1


def test_corrupt_stored_manifest_cannot_restore_files_or_authorize_smtp(migrated, tmp_path):
    from bellomberg.agents.trade_idea import deliver_trade_idea
    current = store(migrated)
    run_id = current.create_run(request(), idempotency_key="corrupted-delivery")["run"]["id"]
    finish(current, run_id, result(judgment="watch", proposal=False))
    unexpected = tmp_path / "unexpected-restored.pdf"
    manifest = {"run_id": run_id, "ticker": "TEST", "artifacts": [],
        "exact_artifact_receipts": [{"path": str(unexpected), "sha256": "a" * 64, "kind": "pdf"}]}
    with sqlite3.connect(migrated) as conn:
        conn.execute("UPDATE trade_idea_delivery SET manifest_json=?,manifest_sha256=? WHERE run_id=?",
            (json.dumps(manifest), "0" * 64, run_id))
    detail = current.get_run(run_id)
    assert detail["manifest_integrity"] is False
    assert detail["states"]["artifacts"] == "unavailable"
    assert detail["recovery"]["can_recover_delivery"] is False
    with pytest.raises(RuntimeError, match="checksum"):
        deliver_trade_idea(current, run_id, output_dir=tmp_path / "delivery", send_email=False)
    with pytest.raises(RunConflict, match="checksum"):
        current.claim_email(run_id)
    assert not unexpected.exists() and current.get_run(run_id)["email"]["attempts"] == 0


@pytest.mark.parametrize("mutation", ["checkpoint", "book", "feedback", "authorization"])
def test_native_continuation_rejects_changed_dependency_or_missing_authorization(migrated, mutation):
    current = store(migrated)
    parent = current.create_run(request(), idempotency_key="changed-parent")["run"]["id"]
    token = current.claim_run(parent)
    _save_resume_checkpoint(current, parent, token)
    current.finish_run(parent, token, None, "incomplete", reason="Stop")
    with sqlite3.connect(migrated) as conn:
        if mutation == "checkpoint":
            progress = current.get_run(parent)["progress"]
            progress["checkpoint"]["data"]["macro"]["0"] = "tampered"
            conn.execute("UPDATE trade_idea_runs SET progress_json=? WHERE id=?", (json.dumps(progress), parent))
        elif mutation == "book":
            conn.execute("UPDATE cash_state SET version=version+1")
        elif mutation == "feedback":
            conn.execute("INSERT INTO decisions(timestamp,ticker,action,status,pm_feedback) VALUES('now','TEST','RESEARCH','PENDING','New PM decision')")
    with pytest.raises((RunConflict, ValueError)):
        current.create_continuation(parent, idempotency_key="changed-resume",
                                    authorize_new_requests=mutation != "authorization")
    assert current.list_runs()["total"] == 1


def test_primary_failure_survives_progress_and_completion_and_unknown_never_completes(migrated):
    current = store(migrated)
    run_id = current.create_run(request(), idempotency_key="first-cause")["run"]["id"]
    token = current.claim_run(run_id)
    current.update_progress(run_id, token, "starting", {"primary_failure": None})
    current.record_failure(run_id, token, {"message": "HTTP 504 provider timeout", "desk": "crypto",
        "round": 2, "request_id": "provider-request"})
    current.record_failure(run_id, token, {"message": "material objections without replies"})
    current.update_progress(run_id, token, "review", {"reports": []})
    detail = current.finish_run(run_id, token, result(), "completed", reason="downstream consequence")
    assert detail["run"]["technical_status"] == "incomplete"
    assert detail["run"]["reason"].startswith("HTTP 504 provider timeout")
    assert detail["progress"]["primary_failure"]["request_id"] == "provider-request"
    assert detail["states"]["analysis"] == "incomplete"


def test_migration_dry_run_is_read_only_and_apply_keeps_legacy(db_path, monkeypatch):
    before = db_path.read_bytes()
    dry = migra_trade_idea.migra(db_path)
    assert dry["modo"] == "dry-run"
    assert dry["scritture_sorgente"]["scritture"] == 0
    assert dry["prova"]["schema_completo"]
    assert db_path.read_bytes() == before
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    applied = migra_trade_idea.migra(db_path, apply=True)
    assert applied["rilettura"]["preesistente_invariato"]
    with sqlite3.connect(applied["backup"]) as conn:
        assert conn.execute("SELECT rationale FROM decisions WHERE id=1").fetchone()[0] == "legacy untouched"
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='trade_idea_runs'").fetchone() is None


def test_migration_accepts_fresh_canonical_memory_db(tmp_path, monkeypatch):
    from bellomberg.storage.memory_db import MemoryDB
    path = tmp_path / "fresh.db"
    MemoryDB(db_path=str(path), chroma_path=str(tmp_path / "chroma"))
    monkeypatch.setattr(migra_trade_idea, "backend_alive", lambda: False)
    receipt = migra_trade_idea.migra(path, apply=True)
    assert receipt["rilettura"]["schema_completo"]
    assert store(path).list_runs() == {"runs": [], "total": 0}


def test_idempotent_acceptance_and_single_active_run(migrated):
    s = store(migrated)
    first = s.create_run(request(), idempotency_key="same-browser-click")
    assert first["created"] is True
    assert first["run"]["language"] == "it"
    assert first["run"]["view_text"] == "My thesis to challenge"
    assert s.create_run(request(), idempotency_key="same-browser-click") == {
        **s.get_run(first["run"]["id"]), "created": False}
    assert s.get_by_idempotency_key("same-browser-click") == s.get_run(first["run"]["id"])
    assert s.get_by_idempotency_key("never-created") is None
    with pytest.raises(ValueError):
        s.get_by_idempotency_key("   ")
    missing_language = request()
    missing_language.pop("language")
    with pytest.raises(ValueError, match="language"):
        s.create_run(missing_language, idempotency_key="missing-language")
    changed = request()
    changed["view_text"] = "Different thesis"
    with pytest.raises(IdempotencyConflict):
        s.create_run(changed, idempotency_key="same-browser-click")
    with pytest.raises(RunConflict):
        s.create_run(request("SECOND"), idempotency_key="next-click")
    assert s.request_stop(first["run"]["id"])
    second = s.create_run(request("SECOND"), idempotency_key="next-click")
    assert second["run"]["ticker"] == "SECOND"


def test_cost_reservation_is_not_replayed_and_unknown_blocks_more_spend(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="cost-run")["run"]["id"]
    s.claim_run(run_id)
    assert s.reserve_cost(run_id, "req-1", "aux", "meta/muse-spark-1.3", "3.00")
    assert not s.reserve_cost(run_id, "req-1", "aux", "meta/muse-spark-1.3", "3.00")
    with pytest.raises(BudgetBlocked):
        s.reserve_cost(run_id, "req-2", "aux", "meta/muse-spark-1.3", "2.01")
    s.mark_cost_unknown(run_id, "req-1", reason="provider timeout after dispatch")
    with pytest.raises(BudgetBlocked):
        s.reserve_cost(run_id, "req-3", "aux", "meta/muse-spark-1.3", "0.01")
    assert s.get_run(run_id)["cost"]["unknown_requests"] == 1


def test_provider_overrun_is_measured_but_blocks_spend_and_dcn(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="overrun-run")["run"]["id"]
    token = s.claim_run(run_id)
    s.reserve_cost(run_id, "overrun-1", "aux", "meta/muse-spark-1.3", "1.00")
    s.reconcile_cost(run_id, "overrun-1", charged_usd="1.25", usage={"cost_usd": "1.25"},
                     receipt={"response_id": "fixture-overrun", "model": "meta/muse-spark-1.3"})
    cost = s.get_run(run_id)["cost"]
    assert cost["charged_usd"] == "1.25"
    assert cost["overrun"] is True
    assert cost["overrun_request_ids"] == ["overrun-1"]
    with pytest.raises(BudgetBlocked, match="overrun"):
        s.reserve_cost(run_id, "overrun-2", "aux", "meta/muse-spark-1.3", "0.01")
    s.finish_run(run_id, token, result(), "completed")
    destination = s.route_result(run_id, all_checks())
    assert destination["kind"] == "research"
    assert "overrun" in destination["reason"].lower()


def test_total_budget_overrun_remains_visible(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="budget-overrun")["run"]["id"]
    s.claim_run(run_id)
    s.reserve_cost(run_id, "overrun-total", "aux", "meta/muse-spark-1.3", "5.00")
    s.reconcile_cost(run_id, "overrun-total", charged_usd="5.25",
                     usage={"cost_usd": "5.25"},
                     receipt={"response_id": "fixture-total", "model": "meta/muse-spark-1.3"})
    cost = s.get_run(run_id)["cost"]
    assert cost["charged_usd"] == "5.25"
    assert cost["budget_exceeded"] is True
    assert cost["remaining_known_usd"] == "-0.25"


def test_late_provider_receipt_reconciles_unknown_without_replaying_request(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="late-receipt")["run"]["id"]
    s.claim_run(run_id)
    s.reserve_cost(run_id, "late-req", "aux", "meta/muse-spark-1.3", "1.00")
    s.mark_cost_unknown(run_id, "late-req", reason="timeout after dispatch")
    receipt = {"response_id": "provider-generation-17", "model": "meta/muse-spark-1.3"}
    usage = {"cost_usd": "0.25", "input_tokens": 90, "output_tokens": 20}
    with pytest.raises(ValueError, match="provider response"):
        s.reconcile_cost(run_id, "late-req", charged_usd="0.25", usage=usage, receipt={})
    assert s.reconcile_cost(run_id, "late-req", charged_usd="0.25", usage=usage, receipt=receipt)
    assert not s.reconcile_cost(run_id, "late-req", charged_usd="0.25", usage=usage, receipt=receipt)
    assert not s.reserve_cost(run_id, "late-req", "aux", "meta/muse-spark-1.3", "1.00")
    cost = s.get_run(run_id)["cost"]
    assert cost["unknown_requests"] == 0 and cost["charged_usd"] == "0.25"
    assert cost["remaining_known_usd"] == "4.75"
    with pytest.raises(RunConflict):
        s.reconcile_cost(run_id, "late-req", charged_usd="0.25", usage=usage,
                         receipt={**receipt, "response_id": "different-generation"})


@pytest.mark.parametrize("usage,receipt", [
    ({"cost_usd": "0.25"}, {}),
    ({"cost_usd": "0.25"}, {"response_id": "r", "model": "wrong-model"}),
    ({"cost_usd": "0.50"}, {"response_id": "r", "model": "meta/muse-spark-1.3"}),
    ({"cost_usd": True}, {"response_id": "r", "model": "meta/muse-spark-1.3"}),
])
def test_first_cost_settlement_requires_matching_provider_identity_and_usage(migrated, usage, receipt):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="first-receipt")["run"]["id"]
    s.claim_run(run_id)
    s.reserve_cost(run_id, "request", "aux", "meta/muse-spark-1.3", "1.00")
    with pytest.raises(ValueError):
        s.reconcile_cost(run_id, "request", charged_usd="0.25", usage=usage, receipt=receipt)
    assert s.get_run(run_id)["cost"]["charged_usd"] == "0"
    assert s.reconcile_cost(run_id, "request", charged_usd="0.250",
        usage={"cost_usd": 0.25}, receipt={"response_id": "r", "model": "meta/muse-spark-1.3"})


def test_unknown_cost_release_requires_explicit_matching_provider_nonbilling_receipt(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="late-rejection")["run"]["id"]
    s.claim_run(run_id)
    s.reserve_cost(run_id, "unbilled-req", "aux", "meta/muse-spark-1.3", "1.00")
    s.mark_cost_unknown(run_id, "unbilled-req", reason="connection outcome unknown")
    with pytest.raises(ValueError, match="nonbilling"):
        s.release_cost(run_id, "unbilled-req", reason="No receipt, only an assumption")
    receipt = {"response_id": "provider-rejection-18", "request_id": "unbilled-req",
               "billable": False, "cost_usd": "0", "provider": "openrouter"}
    with pytest.raises(ValueError, match="nonbilling"):
        s.release_cost(run_id, "unbilled-req", reason="Wrong request",
                       receipt={**receipt, "request_id": "another-request"})
    assert s.release_cost(run_id, "unbilled-req", reason="Provider confirmed no charge", receipt=receipt)
    assert not s.release_cost(run_id, "unbilled-req", reason="Provider confirmed no charge", receipt=receipt)
    assert s.get_run(run_id)["cost"]["unknown_requests"] == 0
    assert not s.reserve_cost(run_id, "unbilled-req", "aux", "meta/muse-spark-1.3", "1.00")


def test_favorable_held_stock_routes_once_to_add_without_touching_feedback(migrated):
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,is_active) VALUES('TEST',10,1)")
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="positive-run")["run"]["id"]
    finish(s, run_id, result())
    destination = s.route_result(run_id, all_checks())
    assert destination["kind"] == "dcn", destination
    assert s.route_result(run_id, {}) == destination
    assert not s.mark_routing_failure(run_id, "late duplicate failure")
    with sqlite3.connect(migrated) as conn:
        row = conn.execute("SELECT action,status,eur_amount,pm_feedback,memo_id FROM decisions WHERE id=?",
                           (destination["decision_id"],)).fetchone()
        assert row[:4] == ("ADD", "PENDING", 1000.0, None)
        memo_id = row[4]
        assert memo_id == s.get_run(run_id)["run"]["memo_id"]
        memo = conn.execute("SELECT notes,title,full_markdown,output_language FROM memos WHERE id=?",
                            (memo_id,)).fetchone()
        assert memo[0] == "trade_idea:" + run_id
        assert "Trade Idea" in memo[1] and "Synthetic conclusion" in memo[2]
        assert memo[3] == "it"
        assert conn.execute("SELECT COUNT(*) FROM memos WHERE notes=?", (memo[0],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2
    assert s.lookup_decision(destination["decision_id"]) == {
        "origin": "trade_idea", "run_id": run_id, "destination_kind": "dcn",
        "ticker": "TEST", "memo_id": memo_id,
        "technical_status": "completed", "destination_reason": destination["reason"],
        "artifacts_ready": False}
    assert s.lookup_decisions([1, destination["decision_id"]]) == {
        destination["decision_id"]: s.lookup_decision(destination["decision_id"])}


def test_rejection_goes_to_research_with_explicit_judgment(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="negative-run")["run"]["id"]
    finish(s, run_id, result(judgment="rejected", proposal=False))
    destination = s.route_result(run_id, {})
    assert destination["kind"] == "research"
    with sqlite3.connect(migrated) as conn:
        action, rationale, memo_id = conn.execute(
            "SELECT action,rationale,memo_id FROM decisions WHERE id=?",
            (destination["decision_id"],)).fetchone()
    assert action == "RESEARCH" and "rejected" in rationale
    assert memo_id == destination["memo_id"] == s.get_run(run_id)["run"]["memo_id"]


def test_english_language_snapshot_reaches_memo(migrated):
    s = store(migrated)
    candidate = {**request(), "language": "en"}
    run_id = s.create_run(candidate, idempotency_key="english-run")["run"]["id"]
    finish(s, run_id, result(judgment="rejected", proposal=False))
    destination = s.route_result(run_id, {})
    with sqlite3.connect(migrated) as conn:
        memo = conn.execute("SELECT output_language,full_markdown FROM memos WHERE id=?",
                            (destination["memo_id"],)).fetchone()
    assert memo[0] == "en" and "## Conclusion" in memo[1]


def test_research_rationale_keeps_missing_and_revisit_after_long_summary(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="long-research")["run"]["id"]
    payload = result(judgment="rejected", proposal=False)
    payload["summary"] = "S" * 11900
    payload["data_gaps"] = ["Audited cash bridge is missing [src: fixture]"]
    payload["review_conditions"] = ["Revisit after signed primary filing [src: fixture]"]
    finish(s, run_id, payload)
    destination = s.route_result(run_id, {})
    with sqlite3.connect(migrated) as conn:
        rationale = conn.execute("SELECT rationale FROM decisions WHERE id=?",
                                 (destination["decision_id"],)).fetchone()[0]
    assert "Audited cash bridge is missing" in rationale
    assert "Revisit after signed primary filing" in rationale
    assert "Routing:" in rationale


@pytest.mark.parametrize("new_price,same_timestamp,expected", [
    (101, False, "dcn"), (102, False, "research"), (102, True, "research"),
    # PM option A (04/10/2026): a move within 0.5% keeps the proposal operative.
    (101.4, False, "dcn"), (101.6, False, "research")])
def test_price_refresh_compares_economic_values_and_breaks_timestamp_ties(
        migrated, new_price, same_timestamp, expected):
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,prezzo_medio,valuta,is_active) "
                     "VALUES('OTHER',10,100,'EUR',1)")
        conn.execute("INSERT INTO position_prices(ticker,prezzo,valuta,source,timestamp) "
                     "VALUES('OTHER',101,'EUR','fixture','2026-09-27T19:00:00')")
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="price-changed")["run"]["id"]
    finish(s, run_id, result())
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO position_prices(ticker,prezzo,valuta,source,timestamp) "
                     "VALUES('OTHER',?,'EUR','fixture',?)",
                     (new_price, "2026-09-27T19:00:00" if same_timestamp else "2026-09-27T19:15:00"))
    destination = s.route_result(run_id, all_checks())
    assert destination["kind"] == expected
    if expected == "research":
        assert "book" in destination["reason"].lower()


def test_non_eur_book_requires_server_fx_revalidation(migrated):
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,prezzo_medio,valuta,is_active) "
                     "VALUES('TEST',10,100,'USD',1)")
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="fx-check")["run"]["id"]
    finish(s, run_id, result())
    destination = s.route_result(run_id, all_checks())
    assert destination["kind"] == "research"
    assert "fx" in destination["reason"].lower()


def test_non_eur_book_can_route_with_server_fx_revalidation(migrated):
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO positions(ticker,quantita,prezzo_medio,valuta,is_active) "
                     "VALUES('TEST',10,100,'USD',1)")
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="fx-verified")["run"]["id"]
    finish(s, run_id, result())
    destination = s.route_result(run_id, {**all_checks(), "fx_revalidated": True})
    assert destination["kind"] == "dcn"


def test_feedback_change_and_unanswered_history_prevent_promotion(migrated):
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO decisions(id,timestamp,action,ticker,status,pm_feedback) "
                     "VALUES(2,'2026-09-26T10:00:00','RESEARCH','TEST','PENDING','Investigate debt')")
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="feedback-run")["run"]["id"]
    assert s.decision_context(run_id)["required"] == [{"kind": "decision", "id": 2}]
    finish(s, run_id, result())
    with sqlite3.connect(migrated) as conn:
        conn.execute("UPDATE decisions SET pm_feedback='Different instruction' WHERE id=2")
    destination = s.route_result(run_id, all_checks())
    assert destination["kind"] == "research"
    assert "feedback" in destination["reason"].lower()


def test_exact_pm_history_review_allows_route_but_active_veto_blocks(migrated):
    with sqlite3.connect(migrated) as conn:
        conn.execute("INSERT INTO decisions(id,timestamp,action,ticker,status,pm_feedback) "
                     "VALUES(2,'2026-09-26T10:00:00','RESEARCH','TEST','PENDING','Investigate debt')")
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="review-run")["run"]["id"]
    assert s.decision_context(run_id)["required"] == [{"kind": "decision", "id": 2}]
    finish(s, run_id, result(history=[{"kind": "decision", "id": 2,
                                       "response": "Debt question answered from primary source [src: fixture]"}]))
    reviewed_destination = s.route_result(run_id, all_checks())
    assert reviewed_destination["kind"] == "dcn", reviewed_destination

    next_run = s.create_run(request(), idempotency_key="veto-run")["run"]["id"]
    required = s.decision_context(next_run)["required"]
    with sqlite3.connect(migrated) as conn:
        conn.execute("UPDATE decisions SET veto=1,veto_reason='No new long exposure' WHERE id=2")
    reviews = [{**entry, "response": "Reviewed against the PM instruction [src: fixture]"}
               for entry in required]
    finish(s, next_run, result(history=reviews))
    destination = s.route_result(next_run, all_checks())
    assert destination["kind"] == "research"
    assert "veto" in destination["reason"].lower()


def test_routing_insert_failure_rolls_back_primary_link(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="rollback-run")["run"]["id"]
    finish(s, run_id, result())
    with sqlite3.connect(migrated) as conn:
        conn.execute("CREATE TRIGGER fixture_reject_decision BEFORE INSERT ON decisions "
                     "BEGIN SELECT RAISE(ABORT,'fixture decision unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        s.route_result(run_id, all_checks())
    assert s.get_run(run_id)["run"]["destination"]["kind"] == "none"
    with sqlite3.connect(migrated) as conn:
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memos").fetchone()[0] == 0


@pytest.mark.parametrize("initial_status", ["completed", "incomplete"])
def test_routing_failure_marks_run_incomplete_without_rewriting_result(migrated, initial_status):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="failed-route")["run"]["id"]
    finish(s, run_id, result(), status=initial_status)
    original = s.get_run(run_id)["result"]
    with sqlite3.connect(migrated) as conn:
        conn.execute("CREATE TRIGGER fixture_reject_decision_2 BEFORE INSERT ON decisions "
                     "BEGIN SELECT RAISE(ABORT,'fixture routing failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        s.route_result(run_id, all_checks())
    assert s.mark_routing_failure(run_id, "Decision persistence failed")
    assert not s.mark_routing_failure(run_id, "Decision persistence failed")
    current = s.get_run(run_id)
    assert current["run"]["technical_status"] == "incomplete"
    assert current["run"]["phase"] == "routing_failed"
    assert current["run"]["destination"]["kind"] == "none"
    assert current["result"] == original


def test_final_report_failure_demotes_only_untouched_dcn_same_id_and_memo(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="report-demotion")["run"]["id"]
    finish(s, run_id, result())
    promoted = s.route_result(run_id, all_checks())
    assert promoted["kind"] == "dcn"
    reason = "Final PDF incomplete: verified workbook changed after preview"
    demoted = s.demote_unreviewed_destination(run_id, reason)
    assert demoted["kind"] == "research"
    assert demoted["decision_id"] == promoted["decision_id"]
    assert demoted["memo_id"] == promoted["memo_id"]
    assert s.demote_unreviewed_destination(run_id, reason) == demoted
    detail = s.get_run(run_id)
    assert detail["run"]["technical_status"] == "incomplete"
    assert detail["run"]["phase"] == "report_incomplete"
    assert reason in detail["run"]["reason"]
    with sqlite3.connect(migrated) as conn:
        row = conn.execute("SELECT action,status,eur_amount,timing,confidence,rationale,memo_id "
                           "FROM decisions WHERE id=?", (promoted["decision_id"],)).fetchone()
        assert row[:5] == ("RESEARCH", "PENDING", None, None, "BASSA")
        assert reason in row[5] and row[6] == promoted["memo_id"]
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM memos").fetchone()[0] == 1
        assert conn.execute("SELECT rationale FROM decisions WHERE id=1").fetchone()[0] == "legacy untouched"


def test_final_report_failure_marks_existing_research_incomplete_without_editing_decision(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="research-report-failure")["run"]["id"]
    finish(s, run_id, result(judgment="rejected", proposal=False))
    original = s.route_result(run_id, {})
    with sqlite3.connect(migrated) as conn:
        before = conn.execute("SELECT * FROM decisions WHERE id=?", (original["decision_id"],)).fetchone()
    changed = s.demote_unreviewed_destination(run_id, "Final PDF incomplete")
    assert changed["kind"] == "research" and changed["decision_id"] == original["decision_id"]
    assert "Final PDF incomplete" in changed["reason"]
    assert s.demote_unreviewed_destination(run_id, "Final PDF incomplete") == changed
    assert s.get_run(run_id)["run"]["technical_status"] == "incomplete"
    with sqlite3.connect(migrated) as conn:
        assert conn.execute("SELECT * FROM decisions WHERE id=?", (original["decision_id"],)).fetchone() == before


@pytest.mark.parametrize("pm_touch", ["status", "feedback", "note", "veto", "trade", "archive"])
def test_final_report_failure_never_overwrites_pm_touched_decision(migrated, pm_touch):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="report-touched-" + pm_touch)["run"]["id"]
    finish(s, run_id, result())
    destination = s.route_result(run_id, all_checks())
    decision_id = destination["decision_id"]
    with sqlite3.connect(migrated) as conn:
        if pm_touch == "status":
            conn.execute("UPDATE decisions SET status='SKIPPED' WHERE id=?", (decision_id,))
        elif pm_touch == "feedback":
            conn.execute("INSERT INTO pm_feedback(decision_id,feedback_text,sentiment) "
                         "VALUES(?,'PM declined','NEGATIVE')", (decision_id,))
        elif pm_touch == "note":
            conn.execute("INSERT INTO decision_notes(decision_id,autore,testo,timestamp) "
                         "VALUES(?,'PM','Need clarification','2026-09-27T20:00:00')", (decision_id,))
        elif pm_touch == "veto":
            conn.execute("UPDATE decisions SET veto=1,veto_reason='Do not trade' WHERE id=?",
                         (decision_id,))
        elif pm_touch == "trade":
            conn.execute("INSERT INTO trade_history(ticker,action,quantita,prezzo,valuta,data,linked_decision_id) "
                         "VALUES('TEST','BUY',1,100,'EUR','2026-09-27',?)", (decision_id,))
        else:
            conn.execute("UPDATE decisions SET archive_override=1 WHERE id=?", (decision_id,))
        touched = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    with pytest.raises(RunConflict, match="PM"):
        s.demote_unreviewed_destination(run_id, "Final PDF incomplete")
    with sqlite3.connect(migrated) as conn:
        assert conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone() == touched
    detail = s.get_run(run_id)["run"]
    assert detail["technical_status"] == "incomplete"
    assert detail["phase"] == "report_demote_blocked"
    assert detail["destination"]["kind"] == "dcn"
    assert "PM" in detail["reason"]
    provenance = s.lookup_decision(decision_id)
    assert provenance["technical_status"] == "incomplete"
    assert "PM" in provenance["destination_reason"]
    with pytest.raises(RunConflict, match="PM"):
        s.demote_unreviewed_destination(run_id, "Final PDF incomplete")
    assert s.get_run(run_id)["run"]["reason"] == detail["reason"]


def test_email_ambiguous_restart_requires_explicit_retry(migrated):
    s = store(migrated)
    run_id = s.create_run(request(), idempotency_key="email-run")["run"]["id"]
    finish(s, run_id, result())
    pdf = migrated.with_name("synthetic.pdf")
    pdf.write_bytes(b"%PDF-1.4 synthetic fixture")
    path = str(pdf)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    persisted_result = s.get_run(run_id)["result"]
    result_digest = hashlib.sha256(json.dumps(
        persisted_result, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    manifest = {"schema_version": 1, "run_type": "trade_idea", "run_id": run_id,
                "ticker": "TEST", "judgment": "favorable", "language": "it",
                "result_sha256": result_digest,
                "attachments": [path], "expected_hashes": {path: digest},
                "artifacts": [{"id": "pdf", "kind": "pdf", "path": path,
                               "sha256": digest, "status": "ready"}]}
    with pytest.raises(ValueError, match="language"):
        s.save_manifest(run_id, {**manifest, "language": "en"})
    with pytest.raises(ValueError, match="destination"):
        s.save_manifest(run_id, manifest)
    destination = s.route_result(run_id, all_checks())
    manifest["destination"] = destination
    with pytest.raises(ValueError, match="not complete"):
        s.save_manifest(run_id, manifest)
    excel = migrated.with_name("synthetic.xlsx")
    excel.write_bytes(b"sealed synthetic workbook for SMTP CAS protocol only")
    excel_path = str(excel)
    excel_digest = hashlib.sha256(excel.read_bytes()).hexdigest()
    manifest["attachments"].append(excel_path)
    manifest["expected_hashes"][excel_path] = excel_digest
    manifest["artifacts"].append({"id": "excel", "kind": "xlsx", "path": excel_path,
                                  "sha256": excel_digest, "status": "ready"})
    manifest.update(model_status="ready", pdf_quality={"status": "ready"})
    first_hash = s.save_manifest(run_id, manifest)
    assert s.save_manifest(run_id, manifest) == first_hash
    # A delivered report must use the same durable memo as its decision.
    with sqlite3.connect(migrated) as conn:
        memo_id = conn.execute("SELECT memo_id FROM decisions WHERE id=?",
                               (destination["decision_id"],)).fetchone()[0]
        assert conn.execute("SELECT pdf_path FROM memos WHERE id=?",
                            (memo_id,)).fetchone()[0] == path
    attempt = s.claim_email(run_id)
    assert s.recover_sending(run_id)
    assert s.get_run(run_id)["email"]["status"] == "uncertain"
    with pytest.raises(RunConflict):
        s.claim_email(run_id, retry=True)
    second = s.claim_email(run_id, retry=True, acknowledge_uncertain=True)
    assert second != attempt
    s.record_email_outcome(run_id, second, "accepted", receipt={"smtp": "accepted by fixture"})
    assert s.get_run(run_id)["email"]["status"] == "accepted"
    with pytest.raises(RunConflict):
        s.claim_email(run_id, retry=True, acknowledge_uncertain=True)
