"""Durable Trade Idea runs, cost receipts, delivery state and decision routing.

The schema is installed only by the explicit migration (or an isolated test).
This module never calls an LLM, sends email, runs a valuation or edits a book.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE, is_research_mode
from bellomberg.core.trade_idea_policy import (EXECUTION_POLICY_V3, EXECUTION_POLICY_V4, RESEARCH_POLICIES,
    execution_policy, role_effort, output_cap)

# A /4 run whose Capo produced no valid verdict is closed by the server with the
# historical-shape incomplete package (desk excerpts, no memo fields). The origin is
# declared structurally on the result, never inferred from a failed /4 validation.
SERVER_INCOMPLETE_ORIGIN = "server_incomplete"


def validate_run_result(source, *, run_id, ticker, pm_view, policy):
    """The result contract of the run's accepted policy.

    /2-/3 and historical runs: validate_result unchanged (no origin key admitted).
    /4: the memo model, or - only when result_origin declares it - the server
    incomplete package on the historical model, which must stay judgment=incomplete
    without a proposal. A malformed /4 memo is never downgraded to the old model.
    """
    from bellomberg.core.trade_idea_contract import validate_result
    if isinstance(source, dict) and "result_origin" in source:
        if source["result_origin"] != SERVER_INCOMPLETE_ORIGIN or policy != EXECUTION_POLICY_V4:
            raise ValueError("Result origin is not admitted for this run policy")
        body = {key: value for key, value in source.items() if key != "result_origin"}
        normalized = validate_result(body, run_id=run_id, ticker=ticker, pm_view=pm_view)
        if normalized["judgment"] != "incomplete" or normalized["proposal"] is not None:
            raise ValueError("Server incomplete package must stay incomplete without a proposal")
        return {**normalized, "result_origin": SERVER_INCOMPLETE_ORIGIN}
    return validate_result(source, run_id=run_id, ticker=ticker, pm_view=pm_view,
                           execution_policy=policy)


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS trade_idea_runs (
      id TEXT PRIMARY KEY,
      idempotency_key TEXT NOT NULL UNIQUE,
      request_sha256 TEXT NOT NULL,
      request_json TEXT NOT NULL,
      ticker TEXT NOT NULL,
      company_name TEXT,
      exchange TEXT,
      currency TEXT,
      language TEXT NOT NULL CHECK(language IN ('it','en')),
      view_text TEXT NOT NULL,
      view_origin TEXT NOT NULL,
      models_json TEXT NOT NULL,
      catalog_snapshot_json TEXT NOT NULL,
      budget_limit_usd TEXT NOT NULL,
      context_json TEXT NOT NULL,
      technical_status TEXT NOT NULL CHECK(technical_status IN
        ('accepted','running','completed','incomplete','failed','cancelled','interrupted')),
      phase TEXT NOT NULL,
      progress_json TEXT NOT NULL,
      result_json TEXT,
      reason TEXT,
      worker_token TEXT,
      stop_requested INTEGER NOT NULL DEFAULT 0 CHECK(stop_requested IN (0,1)),
      destination_kind TEXT NOT NULL DEFAULT 'none'
        CHECK(destination_kind IN ('none','dcn','research')),
      destination_decision_id INTEGER UNIQUE REFERENCES decisions(id),
      memo_id INTEGER UNIQUE REFERENCES memos(id),
      destination_reason TEXT,
      created_at TEXT NOT NULL,
      started_at TEXT,
      updated_at TEXT NOT NULL,
      finished_at TEXT,
      CHECK((destination_kind='none' AND destination_decision_id IS NULL) OR
            (destination_kind!='none' AND destination_decision_id IS NOT NULL)),
      CHECK((technical_status='running' AND worker_token IS NOT NULL) OR
            (technical_status!='running' AND worker_token IS NULL))
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_idea_one_active "
    "ON trade_idea_runs((1)) WHERE technical_status IN ('accepted','running')",
    "CREATE INDEX IF NOT EXISTS idx_trade_idea_ticker_created "
    "ON trade_idea_runs(ticker,created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trade_idea_status_created "
    "ON trade_idea_runs(technical_status,created_at DESC)",
    """CREATE TABLE IF NOT EXISTS trade_idea_events (
      id INTEGER PRIMARY KEY,
      run_id TEXT NOT NULL REFERENCES trade_idea_runs(id),
      kind TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trade_idea_events_run "
    "ON trade_idea_events(run_id,id)",
    """CREATE TABLE IF NOT EXISTS trade_idea_costs (
      request_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL REFERENCES trade_idea_runs(id),
      role TEXT NOT NULL,
      model TEXT NOT NULL,
      reserved_usd TEXT NOT NULL,
      charged_usd TEXT,
      status TEXT NOT NULL CHECK(status IN ('reserved','charged','released','unknown')),
      usage_json TEXT,
      receipt_json TEXT,
      reason TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trade_idea_costs_run "
    "ON trade_idea_costs(run_id,created_at)",
    """CREATE TABLE IF NOT EXISTS trade_idea_delivery (
      run_id TEXT PRIMARY KEY REFERENCES trade_idea_runs(id),
      manifest_json TEXT,
      manifest_sha256 TEXT,
      email_status TEXT NOT NULL DEFAULT 'not_attempted' CHECK(email_status IN
        ('not_attempted','preparing','ready','sending','accepted','failed','uncertain','blocked')),
      email_attempt_id TEXT,
      email_attempts INTEGER NOT NULL DEFAULT 0 CHECK(email_attempts>=0),
      smtp_receipt_json TEXT,
      error TEXT,
      updated_at TEXT NOT NULL,
      accepted_at TEXT
    )""",
    """CREATE TRIGGER IF NOT EXISTS trade_idea_run_input_immutable
      BEFORE UPDATE OF request_sha256,request_json,ticker,company_name,exchange,
        currency,language,view_text,view_origin,models_json,catalog_snapshot_json,
        budget_limit_usd,context_json ON trade_idea_runs
      BEGIN SELECT RAISE(ABORT,'trade idea input immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS trade_idea_run_result_immutable
      BEFORE UPDATE OF result_json ON trade_idea_runs
      WHEN OLD.result_json IS NOT NULL AND NEW.result_json IS NOT OLD.result_json
      BEGIN SELECT RAISE(ABORT,'trade idea result immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS trade_idea_run_memo_immutable
      BEFORE UPDATE OF memo_id ON trade_idea_runs
      WHEN OLD.memo_id IS NOT NULL AND NEW.memo_id IS NOT OLD.memo_id
      BEGIN SELECT RAISE(ABORT,'trade idea memo immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS trade_idea_run_no_delete
      BEFORE DELETE ON trade_idea_runs
      BEGIN SELECT RAISE(ABORT,'trade idea run immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS trade_idea_event_immutable
      BEFORE UPDATE ON trade_idea_events
      BEGIN SELECT RAISE(ABORT,'trade idea event immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS trade_idea_event_no_delete
      BEFORE DELETE ON trade_idea_events
      BEGIN SELECT RAISE(ABORT,'trade idea event immutable'); END""",
)

TABLES = ("trade_idea_runs", "trade_idea_events", "trade_idea_costs", "trade_idea_delivery")
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.^=_:/-]{0,39}\Z")
_FINAL = frozenset(("completed", "incomplete", "failed", "cancelled", "interrupted"))
_EMAIL = frozenset(("not_attempted", "preparing", "ready", "sending", "accepted", "failed", "uncertain", "blocked"))
_OPERATIONAL_CHECKS = (
    "identity_verified", "evidence_sufficient", "red_team_complete",
    "capo_valid", "mandate_valid", "sizing_valid", "valuation_checked",
    "history_context_sent",
)


class IdempotencyConflict(ValueError):
    pass


class RunConflict(RuntimeError):
    pass


class BudgetBlocked(RuntimeError):
    pass


class StorageNotReady(RuntimeError):
    """Read-only diagnosis, safe to expose without filesystem paths or SQL."""

    def __init__(self, status, message):
        super().__init__(message)
        self.storage = {
            "status": status, "error_code": "trade_idea_" + status,
            "update_required": status in ("schema_absent", "schema_partial"),
            "action": ("run_explicit_migration" if status in ("schema_absent", "schema_partial")
                       else "check_database_path" if status == "db_missing"
                       else "retry" if status == "db_busy"
                       else "inspect_schema" if status == "schema_incompatible" else "check_database_access"),
            "documentation": "docs/TRADE_IDEA.md#aggiornamento-storage",
        }


class StorageDBMissing(StorageNotReady, FileNotFoundError):
    pass


def _db_occupato(exc):
    """10/10 (B4, Opus 5.5): lock passeggero (SQLITE_BUSY/SQLITE_LOCKED, anche estesi) distinto
    da DB illeggibile, dal CODICE d'errore SQLite (Python >= 3.11), mai dal testo: senza codice
    (Python 3.10) resta db_unreadable dichiarato. Diagnosi solo in lettura, nessuna scrittura."""
    code = getattr(exc, "sqlite_errorcode", None)
    return (isinstance(exc, sqlite3.OperationalError) and isinstance(code, int)
            and (code & 0xFF) in (5, 6))  # SQLITE_BUSY=5, SQLITE_LOCKED=6


def _check_storage_schema(conn):
    # Reuse the migration's DDL as the contract, without executing any DDL here.
    # Only required objects are compared: unrelated tables/watch and extra
    # indexes remain valid extensions. Missing safety indexes/triggers block too.
    expected = {}
    for statement in SCHEMA:
        sql = statement.replace(" IF NOT EXISTS", "", 1)
        match = re.match(r"CREATE (?:UNIQUE )?(TABLE|INDEX|TRIGGER) (\w+)", sql)
        expected[match.group(2)] = (match.group(1).lower(), " ".join(sql.split()))
    actual = {name: (kind, " ".join((sql or "").split())) for kind, name, sql in
              conn.execute("SELECT type,name,sql FROM sqlite_master")}
    if any(actual[name] != value for name, value in expected.items() if name in actual):
        raise StorageNotReady("schema_incompatible", "Trade Idea schema incompatible: inspect before migration")
    missing = set(expected) - set(actual)
    if missing:
        # These base anchors distinguish an upgrade from an empty/foreign DB.
        # The explicit migration still validates the complete legacy contract,
        # WAL mode and integrity before any change; this is not an apply permit.
        anchors = {"decisions": "id", "memos": "id", "positions": "ticker",
                   "cash_state": "singleton_id"}
        if any(column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
               for table, column in anchors.items()):
            raise StorageNotReady("schema_incompatible",
                                  "Trade Idea base schema absent or incompatible: verify database path and schema")
        if not set(TABLES).intersection(actual):
            raise StorageNotReady("schema_absent", "Trade Idea schema absent: run explicit migration")
        raise StorageNotReady("schema_partial", "Trade Idea schema partial: run explicit migration")


def ensure_schema(conn):
    """Only for the explicit migration and disposable test databases."""
    for statement in SCHEMA:
        conn.execute(statement)


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("plain finite JSON value required") from exc


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _native_checkpoint_contract(row, accepted):
    return _with_mandate_text_policy({"version": 1, "ticker": row["ticker"], "language": row["language"],
        "view_text": row["view_text"], "models": json.loads(row["models_json"]),
        "source_fingerprint": (accepted.get("source_qualification") or {}).get("fingerprint"),
        **({"analysis_mode": RESEARCH_ANALYSIS_MODE} if is_research_mode(accepted) else {}),
        **({"execution_policy": execution_policy(accepted)} if execution_policy(accepted) else {})},
        json.loads(row["context_json"]))


def _capo_request_bindings(row):
    """Bind paid research to its economic inputs, not subsequent run history."""
    accepted = json.loads(row["request_json"])
    if execution_policy(accepted) not in RESEARCH_POLICIES:
        raise RunConflict("Exact Capo request storage requires the accepted research execution policy")
    progress = json.loads(row["progress_json"])
    checkpoint = progress.get("checkpoint")
    if (not isinstance(checkpoint, dict) or checkpoint.get("version") != 1
            or progress.get("checkpoint_sha256") != _digest(checkpoint)
            or checkpoint.get("contract") != _native_checkpoint_contract(row, accepted)):
        raise RunConflict("Capo request checkpoint integrity or accepted contract differs")
    data = checkpoint.get("data")
    desks = ("macro", "eventdesk", "crypto", "fundamentals", "quant", "options")
    thesis = data.get("_research_thesis") if isinstance(data, dict) else None
    sealed_missing = set((thesis.get("missing_reports") or {}) if isinstance(thesis, dict) else ())
    declared_gaps = set((data.get("_desk_gaps") or {}) if isinstance(data, dict) else ())

    def desk_complete(desk):
        # A desk sealed as missing has no report; a gap declared after the seal keeps its R1.
        if desk in sealed_missing:
            return True
        rounds = (1,) if desk in declared_gaps else (1, 2)
        return isinstance(data.get(desk), dict) and all(
            isinstance(data[desk].get(str(round_n)), str) and data[desk][str(round_n)].strip()
            for round_n in rounds)
    if (not isinstance(data, dict) or not all(desk_complete(desk) for desk in desks)
            or any(not isinstance(data.get(key), dict) for key in
                   ("_research_thesis", "_research_review", "_sizing", "_decision_context",
                    "_candidate_quote_initial"))):
        raise RunConflict("Capo economic checkpoint is incomplete; targeted review required")
    fields = ("_research_thesis", "_research_review", "_desk_research_reviews", "_red_research_review",
              "_red_team", "_objections", "_objection_history", "_decisive_questions",
              "_research_reply_completions", "_sizing", "_decision_context", "_candidate_quote_initial",
              "_data_cutoff", "_identity", "_desk_gaps")
    times = checkpoint.get("orari_report") or {}
    bindings = {"contract": checkpoint["contract"], "accepted_context_sha256": _digest(json.loads(row["context_json"])),
        "reports": {desk: _digest(data[desk]) for desk in desks},
        "report_times": _digest({desk: times.get(desk) for desk in (*desks, "_red_team")}),
        "research": {key: _digest(data.get(key)) for key in fields}}
    return progress["checkpoint_sha256"], bindings


def _with_mandate_text_policy(current, accepted):
    """Re-attest exactly the accepted server policy, never upgrade historical rows."""
    from bellomberg.core.mandato_pm import MANDATE_TEXT_POLICY_KEY, text_policy_from_context
    policy = text_policy_from_context(accepted)
    if MANDATE_TEXT_POLICY_KEY in accepted:
        current[MANDATE_TEXT_POLICY_KEY] = policy
    return current


def _same_non_price_context(accepted, current):
    """Only the stored quote inputs may differ; everything else is exact."""
    def without_prices(context):
        return {**context, "book": {key: value for key, value in context["book"].items()
                                    if key not in ("price_inputs_sha256", "price_points")}}
    return without_prices(accepted) == without_prices(current)


def _book_within_tolerance(accepted, current):
    """PM option A: same positions/trades/cash/currencies, each latest price within 0.5%.

    Books accepted before price points were recorded keep the exact comparison.
    """
    from bellomberg.core.trade_idea_policy import within_price_tolerance
    if accepted == current:
        return True
    old, new = accepted.get("price_points"), current.get("price_points")
    if not isinstance(old, list) or not isinstance(new, list) or len(old) != len(new):
        return False
    strip = lambda book: {key: value for key, value in book.items()
                          if key not in ("price_inputs_sha256", "price_points")}
    return strip(accepted) == strip(current) and all(
        a[0] == b[0] and a[2] == b[2] and a[3] == b[3] and within_price_tolerance(a[1], b[1])
        for a, b in zip(old, new))


def _price_refresh_grant(request, context):
    grant = (request.get("continuation") or {}).get("price_refresh")
    if grant is None:
        return None
    fields = {"version", "scope", "accepted_context_sha256", "accepted_price_inputs_sha256",
              "observed_price_inputs_sha256", "observed_at", "authorize_price_refresh",
              "require_fresh_final_verification"}
    if (not isinstance(grant, dict) or set(grant) != fields
            or type(grant.get("version")) is not int or grant["version"] != 1
            or grant.get("scope") != "portfolio_price_inputs"
            or grant.get("authorize_price_refresh") is not True
            or grant.get("require_fresh_final_verification") is not True
            or grant.get("accepted_context_sha256") != _digest(context)
            or grant.get("accepted_price_inputs_sha256") != context["book"]["price_inputs_sha256"]
            or not isinstance(grant.get("observed_price_inputs_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", grant["observed_price_inputs_sha256"])
            or not isinstance(grant.get("observed_at"), str) or not grant["observed_at"]):
        raise RunConflict("price refresh authorization differs from the accepted context")
    return grant


def _price_refresh_verified(proof, *, run_id, accepted, current, proposal, checks):
    """Bind server measurements to the exact book observed inside routing's transaction."""
    names = {"portfolio_reloaded", "risk_recomputed", "stress_recomputed", "sizing_recomputed",
             "candidate_price_verified", "fx_verified", "proposal_valid"}
    evidence_names = {"portfolio", "risk", "stress", "sizing", "candidate_quote", "fx_receipt", "proposal"}
    if (not isinstance(proof, dict) or type(proof.get("version")) is not int or proof["version"] != 1
            or proof.get("run_id") != run_id or not isinstance(proposal, dict)
            or not isinstance(proof.get("measurements"), dict)
            or set(proof["measurements"]) not in (names, names | {"context_stable"})
            or any(proof["measurements"].get(name) is not True for name in names)
            or "context_stable" in proof["measurements"] and proof["measurements"]["context_stable"] is not True
            or checks.get("candidate_price_revalidated") is not True
            or checks.get("fx_revalidated") is not True):
        return False
    expected = {"accepted_context_sha256": _digest(accepted), "current_context_sha256": _digest(current),
                "price_inputs_sha256": current["book"]["price_inputs_sha256"]}
    for key in ("before", "after"):
        observation = proof.get(key)
        if (not isinstance(observation, dict) or set(observation) != {*expected, "observed_at"}
                or any(observation.get(name) != value for name, value in expected.items())
                or not isinstance(observation.get("observed_at"), str) or not observation["observed_at"]):
            return False
    evidence = proof.get("evidence")
    try:
        return bool(isinstance(evidence, dict) and set(evidence) == evidence_names
            and all(isinstance(evidence[name], dict) and evidence[name] for name in evidence_names)
            and proof.get("evidence_sha256") == _digest(evidence)
            and _digest(evidence["proposal"]) == _digest(proposal))
    except (ValueError, TypeError):
        return False


def _checkpoint_resume_block(checkpoint):
    data = checkpoint.get("data") or {}
    if data.get("_model_authoring_completion") and data.get("_model_authoring_completion_failed") is True:
        if any(key.startswith("fundamentals:R1:") and isinstance(row, dict)
               and row.get("status") == "complete"
               for key, row in (checkpoint.get("specialist_checkpoints") or {}).items()):
            return "model_authoring_review_required"
    sealed_missing = set(((data.get("_research_thesis") or {}).get("missing_reports") or {})
                         if isinstance(data.get("_research_thesis"), dict) else ())
    for key, row in (checkpoint.get("specialist_checkpoints") or {}).items():
        if str(key).split(":", 1)[0] in sealed_missing:
            continue  # declared gap sealed into the thesis: no recovery rewrites it
        if isinstance(row, dict) and row.get("inflight_tools"):
            # These three run-bound public-document tools have their own
            # durable dispatch/receipt journal. Re-entry only reads that
            # journal; an uncertain GET is never issued again by the service.
            research = (checkpoint.get('data') or {}).get('_source_research') or {}
            replayable = {'open_company_source', 'search_company_sources', 'acquire_company_source'}
            if (not research.get('run_id') or not isinstance(row['inflight_tools'], dict)
                    or not research.get('parent_grant_fingerprint')
                    or research['parent_grant_fingerprint'] != (checkpoint.get('contract') or {}).get('source_fingerprint')
                    or any(not isinstance(tool, dict) or tool.get('name') not in replayable
                           for tool in row['inflight_tools'].values())):
                return "tool_outcome_unresolved"
        if isinstance(row, dict) and row.get("status") in ("failed", "truncated"):
            return "incomplete_response_review_required"
    return None


def trade_idea_pdf_ready(manifest_json, manifest_sha256, *, run_id, ticker):
    """Read-only final PDF metadata and current bytes for trade/Decisioni links."""
    if not manifest_json or not manifest_sha256:
        return False
    manifest = json.loads(manifest_json)
    if not isinstance(manifest, dict) or _digest(manifest) != manifest_sha256:
        raise ValueError("Trade Idea manifest identity/hash invalid")
    if (manifest.get("schema_version") != 1 or manifest.get("run_type") != "trade_idea"
            or manifest.get("run_id") != run_id or manifest.get("ticker") != ticker):
        return False
    from bellomberg.reporting.trade_idea_delivery import assess_trade_idea_completion, verify_trade_idea_manifest
    if assess_trade_idea_completion(manifest)["status"] != "ready":
        return False
    try:
        verify_trade_idea_manifest(manifest, require_complete=True)
    except (ValueError, OSError, KeyError, TypeError):
        return False
    from bellomberg.core.trade_idea_policy import report_quality_sufficient
    if not report_quality_sufficient(manifest.get("pdf_quality") or {}):
        return False
    pdfs = [artifact for artifact in manifest.get("artifacts") or []
            if isinstance(artifact, dict) and artifact.get("kind") == "pdf"]
    if len(pdfs) != 1 or pdfs[0].get("status") != "ready":
        return False
    pdf = pdfs[0]
    path, digest = pdf.get("path"), pdf.get("sha256")
    metadata_ready = (isinstance(path, str) and bool(path) and isinstance(digest, str)
            and bool(re.fullmatch(r"[0-9a-f]{64}", digest))
            and (manifest.get("expected_hashes") or {}).get(path) == digest
            and path in (manifest.get("attachments") or []))
    if not metadata_ready:
        return False
    from bellomberg.core.paths import REPORT_DIR, MODELS_DIR
    try:
        target = Path(path).resolve(strict=True)
        if not target.is_file() or not any(target.is_relative_to(Path(root).resolve())
                                          for root in (REPORT_DIR, MODELS_DIR)):
            return False
        with target.open("rb") as contents:
            return hashlib.file_digest(contents, "sha256").hexdigest() == digest
    except OSError:
        return False


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{label} must be a decimal string")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label} invalid") from exc
    if not amount.is_finite() or (amount <= 0 if positive else amount < 0):
        raise ValueError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}")
    return str(amount)


def _text(value, label, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (not empty and not value.strip()):
        raise ValueError(f"{label} invalid or exceeds {limit} characters")
    return value


# Lock SQLite transitori (06/10: la run Trade Idea del PM e' morta per un «database is locked» di
# ~25 s durante il desk quant). Il worker costruisce lo store con lock_wait_s: ogni
# scrittura (BEGIN IMMEDIATE ... COMMIT) che trova il DB occupato si ritenta con
# attese crescenti fino al tetto complessivo, e ogni attesa oltre 1 s va nel log
# (chi, quale scrittura, quanto). Si ritenta SOLO se il COMMIT non e' avvenuto: la
# transazione e' stata annullata per intero, quindi ritentare non duplica nulla
# (una prenotazione costo ritentata non diventa mai due prenotazioni).
LOCK_LOG_SOGLIA_S = 1.0
WORKER_LOCK_WAIT_S = 120.0
WORKER_LOCK_BUSY_S = 30.0
_LOCK_STATO = threading.local()


def errore_di_lock(exc):
    """Vero solo per il DB occupato (locked/busy), mai per altri OperationalError."""
    return isinstance(exc, sqlite3.OperationalError) and any(
        word in str(exc).lower() for word in ("locked", "busy"))


def log_lock(message):
    """Una riga su stderr (il worker.log della run): la sola traccia per misurare il colpevole."""
    stamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    print(f"[LOCK] {stamp} pid={os.getpid()} {message}", file=sys.stderr, flush=True)


class _ConnessioneMisurata(sqlite3.Connection):
    """Misura l'attesa di BEGIN/COMMIT e annota se il COMMIT e' avvenuto."""

    def execute(self, sql, *args):
        verbo = sql.lstrip()[:6].upper() if isinstance(sql, str) else ""
        if verbo not in ("BEGIN ", "COMMIT"):
            return super().execute(sql, *args)
        inizio, esito = time.monotonic(), "ok"
        try:
            cursor = super().execute(sql, *args)
            if verbo == "COMMIT":
                _LOCK_STATO.commit = True
            return cursor
        except sqlite3.OperationalError as exc:
            esito = type(exc).__name__ + ": " + str(exc)[:80]
            raise
        finally:
            attesa = time.monotonic() - inizio
            if attesa > LOCK_LOG_SOGLIA_S:
                log_lock(f"scrittura={getattr(_LOCK_STATO, 'operazione', None) or '?'} "
                         f"{verbo.strip()} attesa={attesa:.1f}s esito={esito}")


def _scrittura_ritentata(method):
    """Ritenta la scrittura intera sul DB occupato, solo se lo store ha lock_wait_s."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        if getattr(_LOCK_STATO, "operazione", None) is not None:
            return method(self, *args, **kwargs)  # annidata: ritenta solo la piu' esterna
        totale = getattr(self, "lock_wait_s", None)
        nome = method.__name__
        _LOCK_STATO.operazione = nome
        try:
            inizio, tentativo = time.monotonic(), 0
            while True:
                _LOCK_STATO.commit = False
                try:
                    return method(self, *args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if not totale or not errore_di_lock(exc):
                        raise
                    trascorso = time.monotonic() - inizio
                    if _LOCK_STATO.commit:
                        log_lock(f"scrittura={nome} DB occupato DOPO il commit ({trascorso:.1f}s): "
                                 "non ritento, la scrittura e' gia' avvenuta")
                        raise
                    pausa = min(0.5 * 2 ** tentativo, 8.0)
                    if trascorso + pausa >= totale:
                        log_lock(f"scrittura={nome} DB occupato oltre il tetto di {totale:g}s "
                                 f"({tentativo + 1} tentativi, {trascorso:.1f}s): rinuncio, "
                                 "il chiamante dichiara la lacuna")
                        raise
                    tentativo += 1
                    log_lock(f"scrittura={nome} DB occupato (tentativo {tentativo}, "
                             f"{trascorso:.1f}s trascorsi): riprovo fra {pausa:g}s")
                    time.sleep(pausa)
        finally:
            _LOCK_STATO.operazione = None
    return wrapper


class TradeIdeaStore:
    """An accepted run is immutable input; paid work never resumes implicitly."""

    def __init__(self, db_path, *, mandate_loader=None, clock=None,
                 lock_wait_s=None, lock_busy_s=None):
        self.db_path = os.fspath(db_path)
        # None = comportamento storico (attesa SQLite 10 s, nessun nuovo tentativo).
        # Il worker Trade Idea passa WORKER_LOCK_WAIT_S/WORKER_LOCK_BUSY_S.
        self.lock_wait_s = lock_wait_s
        self.lock_busy_s = lock_busy_s
        self._mandate_loader = mandate_loader or self._load_mandate_hash
        self._clock = clock or _now
        if not os.path.isfile(self.db_path):
            raise StorageDBMissing("db_missing", "Trade Idea DB absent: verify configured database path")
        try:
            with self._connect(read_only=True) as conn:
                _check_storage_schema(conn)
        except (sqlite3.DatabaseError, OSError) as exc:
            if _db_occupato(exc):
                raise StorageNotReady("db_busy", "Trade Idea DB busy: another connection holds a lock, retry") from exc
            raise StorageNotReady("db_unreadable", "Trade Idea DB unreadable: verify access and integrity") from exc

    @staticmethod
    def _load_mandate_hash():
        from bellomberg.core import mandato_pm
        return mandato_pm.impronta(mandato_pm.carica())

    @contextmanager
    def _connect(self, *, read_only=False):
        path = Path(self.db_path).resolve(strict=True)
        busy = float(getattr(self, "lock_busy_s", None) or 10)
        conn = sqlite3.connect(path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"),
                               uri=True, timeout=busy, isolation_level=None,
                               factory=_ConnessioneMisurata)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={int(busy * 1000)}")
        try:
            yield conn
        finally:
            conn.close()

    def _at(self):
        value = self._clock()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("clock must be timezone-aware")
            return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        return _text(value, "clock", 40)

    @staticmethod
    def _context(conn, ticker, *, exclude_research_ids=()):
        """Same-ticker history and exact SQLite book/price inputs; no provider calls."""
        position = conn.execute(
            "SELECT ticker,quantita,is_active,last_updated FROM positions WHERE ticker=?", (ticker,)
        ).fetchone()
        trades = [tuple(r) for r in conn.execute(
            "SELECT id,action,quantita,data,linked_decision_id FROM trade_history "
            "WHERE ticker=? ORDER BY id", (ticker,))]
        decisions = [tuple(r) for r in conn.execute(
            "SELECT id,action,status,pm_feedback,veto,veto_reason,veto_at,veto_revoked_at "
            "FROM decisions WHERE ticker=? ORDER BY id", (ticker,))]
        # A continuation's own unreviewed RESEARCH placeholder is not a PM
        # decision. Any PM feedback/status/veto change still invalidates reuse.
        decisions = [row for row in decisions if not (
            row[0] in exclude_research_ids and row[1] == "RESEARCH"
            and row[2] == "PENDING" and not row[3] and not row[4]
            and not any(row[5:]))]
        notes = [tuple(r) for r in conn.execute(
            "SELECT n.id,n.decision_id,n.autore,n.testo,n.timestamp "
            "FROM decision_notes n JOIN decisions d ON d.id=n.decision_id "
            "WHERE d.ticker=? ORDER BY n.id", (ticker,))]
        pm_feedback = [tuple(r) for r in conn.execute(
            "SELECT f.id,f.decision_id,f.feedback_text,f.sentiment "
            "FROM pm_feedback f JOIN decisions d ON d.id=f.decision_id "
            "WHERE d.ticker=? ORDER BY f.id", (ticker,))]
        portfolio = [tuple(r) for r in conn.execute("SELECT * FROM positions ORDER BY ticker")]
        active = [r for r in conn.execute(
            "SELECT ticker,valuta FROM positions WHERE is_active=1 AND quantita>0 ORDER BY ticker")]
        price_inputs, price_points = [], []
        for item in active:
            symbol = item["ticker"]
            # Scheduler refreshes are append-only. A new row/time with identical
            # economic inputs does not change sizing. Freshness is separately
            # rechecked by the pipeline before routing; no price tolerance here.
            latest = conn.execute(
                "SELECT prezzo,valuta,source FROM position_prices WHERE ticker=? "
                "ORDER BY timestamp DESC,id DESC LIMIT 1", (symbol,)).fetchone()
            previous = conn.execute(
                "SELECT prezzo,valuta,source FROM position_prices WHERE ticker=? "
                "AND date(timestamp) < (SELECT date(MAX(timestamp)) FROM position_prices WHERE ticker=?) "
                "ORDER BY timestamp DESC,id DESC LIMIT 1", (symbol, symbol)).fetchone()
            price_inputs.append((symbol, tuple(latest) if latest else None,
                                 tuple(previous) if previous else None))
            price_points.append([symbol, latest["prezzo"] if latest else None,
                                 latest["valuta"] if latest else None, latest["source"] if latest else None])
        non_eur = sorted({(item["valuta"] or "EUR").upper() for item in active
                          if (item["valuta"] or "EUR").upper() != "EUR"})
        cash = conn.execute("SELECT balance_cents,version FROM cash_state WHERE singleton_id=1").fetchone()
        book = {"position": dict(position) if position else None,
                "trades_sha256": _digest(trades), "trades_count": len(trades),
                "portfolio_sha256": _digest(portfolio),
                "price_inputs_sha256": _digest(price_inputs),
                "price_points": price_points,
                "non_eur_active_currencies": non_eur,
                "cash_state": list(cash) if cash else None}
        feedback = {"decisions_sha256": _digest(decisions),
                     "notes_sha256": _digest(notes), "feedback_sha256": _digest(pm_feedback),
                     "decision_count": len(decisions), "active_veto_ids": [d[0] for d in decisions if d[4] == 1]}
        return book, feedback

    @staticmethod
    def _validate_price_refresh_units(conn):
        def unit(value):
            if not isinstance(value, str) or not value.strip():
                return None
            value = value.strip()
            # Same explicit pence alias as valuation.market_quote. Pounds
            # remain a different unit; this check never performs conversion.
            return "GBX" if value in ("GBX", "GBp") else value.upper()

        for position in conn.execute(
                "SELECT ticker,valuta FROM positions WHERE is_active=1 AND quantita>0 ORDER BY ticker"):
            symbol, expected = position["ticker"], unit(position["valuta"])
            latest = conn.execute("SELECT valuta FROM position_prices WHERE ticker=? "
                "ORDER BY timestamp DESC,id DESC LIMIT 1", (symbol,)).fetchone()
            previous = conn.execute("SELECT valuta FROM position_prices WHERE ticker=? "
                "AND date(timestamp) < (SELECT date(MAX(timestamp)) FROM position_prices WHERE ticker=?) "
                "ORDER BY timestamp DESC,id DESC LIMIT 1", (symbol, symbol)).fetchone()
            if expected is None or latest is None or unit(latest["valuta"]) != expected:
                raise RunConflict("price currency/unit is unavailable or differs from the position: " + symbol)
            if previous is not None and unit(previous["valuta"]) != expected:
                raise RunConflict("previous daily price currency/unit differs from the position: " + symbol)

    @staticmethod
    def _history_rows(conn, ticker, accepted_at):
        """Deterministic same-ticker PM history as it stood at run acceptance."""
        accepted = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
        cutoff = (accepted.astimezone().replace(tzinfo=None) - timedelta(days=7)).isoformat(
            timespec="seconds")
        decisions = [dict(r) for r in conn.execute(
            "SELECT id,action,status,pm_feedback,veto,veto_reason,veto_at,veto_revoked_at "
            "FROM decisions WHERE ticker=? AND ("
            "(pm_feedback IS NOT NULL AND trim(pm_feedback)!='') OR COALESCE(veto,0)=1 "
            "OR EXISTS(SELECT 1 FROM decision_notes n WHERE n.decision_id=decisions.id "
            "AND n.autore='PM') OR EXISTS(SELECT 1 FROM pm_feedback f "
            "WHERE f.decision_id=decisions.id)) ORDER BY id", (ticker,))]
        for item in decisions:
            item["notes"] = [dict(r) for r in conn.execute(
                "SELECT id,testo,timestamp FROM decision_notes "
                "WHERE decision_id=? AND autore='PM' ORDER BY id", (item["id"],))]
            item["feedback_rows"] = [dict(r) for r in conn.execute(
                "SELECT id,feedback_text,sentiment FROM pm_feedback "
                "WHERE decision_id=? ORDER BY id", (item["id"],))]
        trades = [dict(r) for r in conn.execute(
            "SELECT id,action,quantita,prezzo,valuta,data,pm_rationale,note "
            "FROM trade_history WHERE ticker=? AND data>=? AND action!='DIVIDEND' "
            "ORDER BY data DESC,id DESC", (ticker, cutoff))]
        return decisions, trades

    def decision_context(self, run_id: str):
        """Read-only prompt context and exact IDs that the final result must answer."""
        with self._connect(read_only=True) as conn:
            row = self._row(conn, run_id)
            decisions, trades = self._history_rows(conn, row["ticker"], row["created_at"])
            return {"ticker": row["ticker"], "as_of": row["created_at"],
                    "decisions": decisions, "recent_trades": trades,
                    "required": ([{"kind": "decision", "id": d["id"]} for d in decisions]
                                 + [{"kind": "trade", "id": t["id"]} for t in trades])}

    @staticmethod
    def _event(conn, run_id, kind, payload, at):
        conn.execute("INSERT INTO trade_idea_events(run_id,kind,payload_json,at) VALUES(?,?,?,?)",
                     (run_id, kind, _json(payload), at))

    @staticmethod
    def _row(conn, run_id):
        row = conn.execute("SELECT * FROM trade_idea_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Trade Idea run absent: {run_id}")
        return row

    def get_by_idempotency_key(self, idempotency_key: str):
        """Read-only retry lookup before any fresh preflight or paid dispatch."""
        key = _text(_text(idempotency_key, "idempotency_key", 200).strip(),
                    "idempotency_key", 200)
        with self._connect(read_only=True) as conn:
            row = conn.execute("SELECT id FROM trade_idea_runs WHERE idempotency_key=?",
                               (key,)).fetchone()
        return self.get_run(row["id"]) if row else None

    @_scrittura_ritentata
    def create_run(self, request: dict, *, idempotency_key: str):
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        if "continuation" in request:
            raise ValueError("continuation is server-owned; select the original run explicitly")
        key = _text(_text(idempotency_key, "idempotency_key", 200).strip(),
                    "idempotency_key", 200)
        ticker = _text(request.get("ticker"), "ticker", 40).strip().upper()
        if not _TICKER.fullmatch(ticker):
            raise ValueError("ticker invalid")
        view = _text(request.get("view_text", ""), "view_text", 20000, empty=True)
        origin = _text(request.get("view_origin", "manual"), "view_origin", 100)
        language = request.get("language")
        if language not in ("it", "en"):
            raise ValueError("language must be it or en")
        company = request.get("company_name")
        exchange = request.get("exchange")
        currency = request.get("currency")
        for label, value, limit in (("company_name", company, 250), ("exchange", exchange, 80),
                                     ("currency", currency, 20)):
            if value is not None:
                _text(value, label, limit)
        models = request.get("models")
        catalog = request.get("catalog_snapshot")
        execution_policy(request)
        if not isinstance(models, dict) or not isinstance(catalog, dict):
            raise ValueError("model and catalog snapshots required")
        for role in ("specialist", "red_team", "capo", "aux"):
            selected = models.get(role)
            quoted = (catalog.get("models") or {}).get(role)
            if (not isinstance(selected, dict) or not isinstance(quoted, dict)
                    or not isinstance(selected.get("model"), str)
                    or selected["model"] != quoted.get("id")
                    or selected.get("reasoning_effort") != role_effort(request, role)):
                raise ValueError(f"Trade Idea model/catalog snapshot invalid for {role}")
        if request.get("cost_acknowledged") is not True:
            raise ValueError("measured-cost acknowledgement required")
        from bellomberg.core.trade_idea_contract import validate_run_authorization
        authorization = validate_run_authorization(request.get("authorization"), request.get("source_qualification"))
        if request.get('analysis_mode') not in (None, RESEARCH_ANALYSIS_MODE):
            raise ValueError('Unsupported analysis mode')
        if is_research_mode(request) != is_research_mode(request.get('source_qualification')):
            raise ValueError('Analysis mode differs from the accepted source contract')
        # 09/10 (B6, Opus 5.5): marcatore d'esecuzione della controverifica fonti; una run
        # si accetta solo dopo una qualifica COMPLETATA, ogni altro valore e' un rifiuto.
        if request.get("source_qualification_execution", "completed") != "completed":
            raise ValueError("source qualification must be completed before acceptance")
        budget = _decimal(request.get("budget_limit_usd"), "budget_limit_usd", positive=True)
        canonical_request = {**request, "ticker": ticker, "budget_limit_usd": budget,
                             "authorization": authorization}
        if "peers" in request:
            # Lotto 3 (L1, Opus 5.5): peer del PM facoltativi; assenti = selettore dichiarato
            from bellomberg.market_data.trade_idea_market_pack import normalize_request_peers
            peers = normalize_request_peers(request["peers"], ticker=ticker)
            if not peers:
                raise ValueError("peers vuota: omettere il campo per usare il selettore")
            canonical_request["peers"] = peers
        encoded, digest = _json(canonical_request), _digest(canonical_request)
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = conn.execute("SELECT id,request_sha256 FROM trade_idea_runs "
                                     "WHERE idempotency_key=?", (key,)).fetchone()
                if prior:
                    if prior["request_sha256"] != digest:
                        raise IdempotencyConflict("idempotency key reused with different request")
                    conn.execute("COMMIT")
                    return {**self.get_run(prior["id"]), "created": False}
                mandate_hash = _text(self._mandate_loader(), "mandate_sha256", 64)
                if not re.fullmatch(r"[0-9a-f]{64}", mandate_hash):
                    raise ValueError("mandate fingerprint invalid")
                book, feedback = self._context(conn, ticker)
                from bellomberg.core.mandato_pm import MANDATE_TEXT_POLICY, MANDATE_TEXT_POLICY_KEY
                context = {"book": book, "feedback": feedback, "mandate_sha256": mandate_hash,
                           MANDATE_TEXT_POLICY_KEY: MANDATE_TEXT_POLICY}
                run_id = str(uuid.uuid4())
                try:
                    conn.execute("""INSERT INTO trade_idea_runs(
                      id,idempotency_key,request_sha256,request_json,ticker,company_name,exchange,currency,language,
                      view_text,view_origin,models_json,catalog_snapshot_json,budget_limit_usd,context_json,
                      technical_status,phase,progress_json,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'accepted','accepted','{}',?,?)""",
                                 (run_id, key, digest, encoded, ticker, company, exchange, currency, language,
                                  view, origin, _json(models), _json(catalog), budget, _json(context), now, now))
                except sqlite3.IntegrityError as exc:
                    if "idx_trade_idea_one_active" in str(exc) or "UNIQUE" in str(exc):
                        raise RunConflict("another Trade Idea run is active") from exc
                    raise
                conn.execute("INSERT INTO trade_idea_delivery(run_id,updated_at) VALUES(?,?)",
                             (run_id, now))
                self._event(conn, run_id, "accepted", {"ticker": ticker}, now)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return {**self.get_run(run_id), "created": True}

    def _ancestry(self, conn, run_id):
        """Validate the immutable chain; receipts stay in their original run."""
        rows, seen = [], set()
        while run_id:
            if run_id in seen or len(seen) >= 256:
                raise RunConflict("invalid Trade Idea continuation ancestry")
            seen.add(run_id)
            row = self._row(conn, run_id)
            request = json.loads(row["request_json"])
            if _digest(request) != row["request_sha256"]:
                raise RunConflict("accepted request integrity differs in continuation ancestry")
            if request.get("budget_limit_usd") != row["budget_limit_usd"]:
                raise RunConflict("accepted budget projection differs in continuation ancestry")
            price_grant = _price_refresh_grant(request, json.loads(row["context_json"]))
            rows.append(row)
            link = request.get("continuation") or {}
            finalization_events = [json.loads(item[0]) for item in conn.execute(
                "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='continuation_accepted' "
                "AND json_type(payload_json,'$.capo_finalization') IS NOT NULL", (row["id"],))]
            if finalization_events and (len(finalization_events) != 1 or finalization_events[0] != link):
                raise RunConflict("Capo finalization differs from its immutable acceptance event")
            run_id = link.get("parent_run_id")
            if run_id:
                parent = self._row(conn, run_id)
                if link.get("parent_request_sha256") != parent["request_sha256"]:
                    raise RunConflict("continuation parent identity differs")
                parent_request = json.loads(parent["request_json"])
                parent_finalization = (parent_request.get("continuation") or {}).get("capo_finalization")
                if parent_finalization is not None and link.get("capo_finalization") != parent_finalization:
                    raise RunConflict("continuation changed the accepted Capo finalization authorization")
                parent_grant = _price_refresh_grant(parent_request,
                                                   json.loads(parent["context_json"]))
                if parent_grant is not None and price_grant != parent_grant:
                    raise RunConflict("continuation changed the original price refresh authorization")
                amendment = link.get("budget_amendment")
                if amendment is not None:
                    expected = {"version": 1, "scope": "aggregate_chain_budget",
                        "parent_run_id": parent["id"], "parent_request_sha256": parent["request_sha256"],
                        "previous_budget_limit_usd": parent["budget_limit_usd"],
                        "budget_limit_usd": row["budget_limit_usd"],
                        "authorized_at": link.get("authorized_at"), "authorize_budget_increase": True}
                    if (not isinstance(amendment, dict) or amendment != expected
                            or type(amendment.get("version")) is not int
                            or amendment.get("authorize_budget_increase") is not True
                            or not isinstance(amendment.get("authorized_at"), str)
                            or not amendment["authorized_at"]
                            or Decimal(row["budget_limit_usd"]) <= Decimal(parent["budget_limit_usd"])):
                        raise RunConflict("continuation budget amendment differs from the accepted increase")
                elif row["budget_limit_usd"] != parent["budget_limit_usd"]:
                    raise RunConflict("continuation changed accepted budget_limit_usd without authorization")
                if ({key: value for key, value in request.items() if key not in ("continuation", "budget_limit_usd")}
                        != {key: value for key, value in parent_request.items() if key not in ("continuation", "budget_limit_usd")}):
                    raise RunConflict("continuation changed accepted request outside its budget amendment")
                for field in ("ticker", "language", "view_text", "models_json", "context_json"):
                    if row[field] != parent[field]:
                        raise RunConflict("continuation changed accepted " + field)
            elif link.get("budget_amendment") is not None:
                raise RunConflict("budget amendment requires an accepted parent")
        chain = list(reversed(rows))
        previous_ids = set()
        inherited_held = None
        for row in chain:
            held = (json.loads(row["request_json"]).get("continuation") or {}).get("held_rejection")
            if inherited_held is not None and held != inherited_held:
                raise RunConflict("continuation changed the accepted held-request authorization")
            if held is not None:
                self._validate_held_rejection(conn, held, previous_ids)
                inherited_held = held
            previous_ids.add(row["id"])
        return chain

    def _validate_held_rejection(self, conn, held, source_run_ids):
        """Re-attest the exact acknowledged unknown; this never settles a cost."""
        from bellomberg.core.preprovider_receipt import validate_preprovider_rejection_receipt
        if (not isinstance(held, dict) or set(held) != {"request_id", "reserved_usd", "proof", "original_cost_row"}
                or not isinstance(held.get("request_id"), str)):
            raise RunConflict("held rejection authorization is malformed")
        cost = conn.execute("SELECT * FROM trade_idea_costs WHERE request_id=?", (held["request_id"],)).fetchone()
        original = held.get("original_cost_row")
        if (cost is None or cost["run_id"] not in source_run_ids or not isinstance(original, dict)
                or held["reserved_usd"] != cost["reserved_usd"]
                or any(original.get(key) != cost[key] for key in
                       ("request_id", "run_id", "role", "model", "reserved_usd", "created_at"))
                or cost["status"] not in ("unknown", "charged", "released")
                or (cost["status"] == "unknown" and dict(cost) != original)):
            raise RunConflict("held rejection differs from its original reserved request")
        events = [dict(row) for row in conn.execute("SELECT * FROM trade_idea_events WHERE run_id=? ORDER BY id", (cost["run_id"],))]
        failure = json.loads(self._row(conn, cost["run_id"])["progress_json"]).get("primary_failure")
        if cost["status"] != "unknown":
            # A later native provider receipt can settle the acknowledged
            # request. Keep its original hold proof tied to the earlier
            # immutable events, without preventing that measured settlement.
            failure_hashes = set((held.get("proof") or {}).get("evidence", {}).get("failure_event_sha256", ()))
            proof_events = [event for event in events if _digest(event) in failure_hashes]
            settlements = [event for event in events if event["kind"] in ("cost_charged", "cost_released")
                and json.loads(event["payload_json"]).get("request_id") == cost["request_id"]]
            if (not proof_events or not settlements
                    or any(event["id"] <= max(item["id"] for item in proof_events) for event in settlements)
                    or settlements[-1]["kind"] != "cost_" + cost["status"]
                    or json.loads(settlements[-1]["payload_json"]).get("charged_usd") != cost["charged_usd"]):
                raise RunConflict("held rejection has no valid later settlement lineage")
            events = [event for event in events if event not in settlements]
        if not validate_preprovider_rejection_receipt(held["proof"], original, events, failure=failure):
            raise RunConflict("held rejection proof no longer matches durable provider evidence")
        return cost

    def _authorize_held_rejection(self, conn, chain, request_id):
        """Create a server-owned acknowledgement without rewriting its unknown ledger row."""
        from bellomberg.core.preprovider_receipt import build_preprovider_rejection_receipt
        cost = conn.execute("SELECT * FROM trade_idea_costs WHERE request_id=?", (request_id,)).fetchone()
        if cost is None or cost["run_id"] not in {row["id"] for row in chain} or cost["status"] != "unknown":
            raise RunConflict("selected request is not an unresolved member of this continuation chain")
        events = [dict(row) for row in conn.execute("SELECT * FROM trade_idea_events WHERE run_id=? ORDER BY id",
                                                   (cost["run_id"],))]
        source = next(row for row in chain if row["id"] == cost["run_id"])
        failure = json.loads(source["progress_json"]).get("primary_failure")
        proof = build_preprovider_rejection_receipt(dict(cost), events, failure=failure)
        return {"request_id": request_id, "reserved_usd": cost["reserved_usd"],
                "proof": proof, "original_cost_row": dict(cost)}

    def _current_run_context(self, conn, row, *, mandate_hash=None):
        chain = self._ancestry(conn, row["id"])
        book, feedback = self._context(conn, row["ticker"], exclude_research_ids=[
            item["destination_decision_id"] for item in chain])
        return _with_mandate_text_policy(
            {"book": book, "feedback": feedback,
             "mandate_sha256": self._mandate_loader() if mandate_hash is None else mandate_hash},
            json.loads(row["context_json"]))

    def price_refresh_context(self, run_id):
        """Read one consistent fingerprint; never replace the accepted research context."""
        with self._connect(read_only=True) as conn:
            conn.execute("BEGIN")
            row = self._row(conn, run_id)
            accepted = json.loads(row["context_json"])
            current = self._current_run_context(conn, row)
            if not _same_non_price_context(accepted, current):
                raise RunConflict("book, feedback or mandate changed beyond prices")
            self._validate_price_refresh_units(conn)
            return {"accepted_context_sha256": _digest(accepted), "current_context_sha256": _digest(current),
                    "price_inputs_sha256": current["book"]["price_inputs_sha256"], "observed_at": self._at()}

    def _validated_specialist_usage(self, conn, run_id, desk, usage):
        """Return the original charged rows after exact per-desk reconciliation."""
        def reject(reason):
            raise RunConflict("specialist usage is not attested: " + reason)

        if (not isinstance(usage, dict) or usage.get("tokens_status") != "completo"
                or usage.get("tokens_missing")):
            reject("usage is incomplete")
        ids = usage.get("request_ids")
        if (not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids)
                or len(ids) != len(set(ids))):
            reject("request identities are incomplete")
        chain = self._ancestry(conn, run_id)
        chain_ids = {row["id"] for row in chain}
        model = json.loads(chain[-1]["models_json"])["specialist"]["model"]
        token_fields = {"in": "input_tokens", "out": "output_tokens",
            "cache_read": "cache_read_input_tokens", "cache_write": "cache_creation_input_tokens"}
        token_totals = {name: 0 for name in token_fields}
        decimal_total, native_float_total, verified = Decimal(0), 0.0, []
        for ident in ids:
            cost = conn.execute("SELECT * FROM trade_idea_costs WHERE request_id=?", (ident,)).fetchone()
            if (cost is None or cost["run_id"] not in chain_ids or cost["status"] != "charged"
                    or cost["role"] != "specialist:" + desk or cost["model"] != model):
                reject("paid response ownership or state differs")
            proof = json.loads(cost["receipt_json"] or "{}")
            response = proof.get("response")
            measured = json.loads(cost["usage_json"] or "{}")
            if (not isinstance(response, dict) or proof.get("response_sha256") != _digest(response)
                    or not response.get("id") or response["id"] != proof.get("response_id")
                    or response.get("request_id") != ident or response.get("model") != cost["model"]
                    or proof.get("model") != cost["model"]):
                reject("paid response integrity or identity differs")
            for observed in ((response.get("usage") or {}).get("cost_usd"), measured.get("cost_usd")):
                if (observed is None or isinstance(observed, bool)
                        or Decimal(_decimal(str(observed), "response cost")) != Decimal(cost["charged_usd"])):
                    reject("paid response cost differs")
            paid = Decimal(cost["charged_usd"])
            decimal_total += paid
            # Existing specialist checkpoints accumulate floats in request
            # order. Accept that exact representation or the exact Decimal
            # sum, never an arbitrary tolerance or an unknown-to-zero fill.
            native_float_total += float(paid)
            for name, field in token_fields.items():
                amount = (response.get("usage") or {}).get(field)
                measured_amount = measured.get(field)
                if (type(amount) is not int or amount < 0 or type(measured_amount) is not int
                        or measured_amount != amount):
                    reject("paid token receipt is incomplete or differs")
                token_totals[name] += amount
            verified.append((cost, proof))
        recorded_total = usage.get("cost_usd")
        if (recorded_total is None or isinstance(recorded_total, bool)
                or Decimal(_decimal(str(recorded_total), "checkpoint cost"))
                    not in (decimal_total, Decimal(str(native_float_total)))
                or any(type(usage.get(name)) is not int or usage[name] != total
                       for name, total in token_totals.items())):
            reject("checkpoint aggregate usage differs from its paid receipts")
        return verified

    def validate_specialist_usage(self, run_id, desk, usage):
        """Read-only validation for both original and derived specialist states."""
        with self._connect(read_only=True) as conn:
            self._validated_specialist_usage(conn, run_id, desk, usage)
        return True

    def specialist_response_receipts(self, run_id, desk, usage):
        """Return only reconciled ancestor/current responses for native phase recovery."""
        with self._connect(read_only=True) as conn:
            return [json.loads(json.dumps(proof)) for _, proof in
                    self._validated_specialist_usage(conn, run_id, desk, usage)]

    def accepted_response_recovery(self, run_id, descriptor):
        """Read an exact historical grant; never create another recovery authority."""
        with self._connect(read_only=True) as conn:
            return any((json.loads(row["request_json"]).get("continuation") or {}).get(
                "specialist_response_recovery") == descriptor for row in self._ancestry(conn, run_id))

    def _capo_finalization_source(self, conn, chain, request_id):
        """Attest a paid, textless Capo truncation without changing its evidence."""
        def reject(reason):
            raise RunConflict("Capo finalization not eligible: " + reason)

        cost = conn.execute("SELECT * FROM trade_idea_costs WHERE request_id=?", (request_id,)).fetchone()
        sources = {row["id"]: row for row in chain}
        if (cost is None or cost["run_id"] not in sources or cost["status"] != "charged"
                or cost["role"] != "capo"):
            reject("a charged ancestor Capo request is required")
        source = sources[cost["run_id"]]
        accepted = json.loads(source["request_json"])
        models = json.loads(source["models_json"])
        model = (models.get("capo") or {}).get("model")
        if (not is_research_mode(accepted) or source["technical_status"] not in _FINAL
                or source["technical_status"] == "completed" or cost["model"] != model):
            reject("source mode, status or accepted model differs")
        # A stale paid failure cannot authorize bypassing a more recent Capo call.
        ids = list(sources)
        capo = conn.execute("SELECT request_id FROM trade_idea_costs WHERE run_id IN ("
            + ",".join("?" for _ in ids) + ") AND role='capo' ORDER BY created_at DESC", ids).fetchall()
        if not capo or capo[0]["request_id"] != request_id:
            reject("the selected receipt is not the last Capo request")
        progress = json.loads(source["progress_json"])
        checkpoint = progress.get("checkpoint")
        contract = _native_checkpoint_contract(source, accepted)
        if (not isinstance(checkpoint, dict) or checkpoint.get("version") != 1
                or progress.get("checkpoint_sha256") != _digest(checkpoint)
                or checkpoint.get("contract") != contract
                or (checkpoint.get("data") or {}).get("_capo_completed")):
            reject("saved checkpoint or accepted source contract differs")
        receipt = json.loads(cost["receipt_json"] or "{}")
        response = receipt.get("response")
        fingerprint = receipt.get("request_sha256")
        if (not isinstance(response, dict) or receipt.get("response_sha256") != _digest(response)
                or not isinstance(receipt.get("response_id"), str) or not receipt["response_id"]
                or response.get("id") != receipt["response_id"]
                or response.get("model") != receipt.get("model") or receipt.get("model") != model
                or receipt.get("stop_reason") != "max_tokens" or response.get("stop_reason") != "max_tokens"
                or not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
            reject("paid response identity, hash or truncation differs")
        content = response.get("content")
        if (not isinstance(content, list) or any(not isinstance(block, dict)
                or block.get("type") not in ("thinking", "redacted_thinking", "text")
                or (block.get("type") == "text" and (not isinstance(block.get("text"), str)
                    or block["text"].strip())) for block in content)):
            reject("the source contains public text or an unknown response block")
        usage, measured = response.get("usage") or {}, json.loads(cost["usage_json"] or "{}")
        try:
            valid_usage = (all(type(usage.get(key)) is int and usage[key] >= minimum
                and measured.get(key) == usage[key] for key, minimum in (("input_tokens", 0), ("output_tokens", 1)))
                and all(not isinstance(row.get("cost_usd"), bool) and row.get("cost_usd") is not None
                    and Decimal(_decimal(str(row["cost_usd"]), "Capo cost")) == Decimal(cost["charged_usd"])
                    for row in (usage, measured)))
        except (ValueError, TypeError, InvalidOperation):
            valid_usage = False
        if not valid_usage:
            reject("known usage and measured cost are required")
        for kind, expected in (
                ("cost_reserved", {"role": "capo", "model": model, "max_usd": cost["reserved_usd"],
                                   "request_sha256": fingerprint}),
                ("cost_charged", {"charged_usd": cost["charged_usd"]})):
            events = [json.loads(row[0]) for row in conn.execute(
                "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind=?", (source["id"], kind))]
            matching = [event for event in events if event.get("request_id") == request_id]
            if len(matching) != 1 or any(matching[0].get(key) != value for key, value in expected.items()):
                reject("original reservation or settlement proof differs")
        return {"kind": "research_capo_finalization_v1", "version": 1, "request_id": request_id,
            "source_run_id": source["id"], "source_request_sha256": source["request_sha256"],
            "source_checkpoint_sha256": progress["checkpoint_sha256"],
            "source_fingerprint": contract["source_fingerprint"], "model": model,
            # Never below the accepted Capo cap: a finalization with less room than the
            # truncated original would truncate again. /3 and /4 keep their accepted MEDIUM effort;
            # earlier contracts keep LOW so their already-accepted grants stay identical.
            "thinking": {"type": "effort", "effort": (role_effort(accepted, 'capo')
                         if execution_policy(accepted) in (EXECUTION_POLICY_V3, EXECUTION_POLICY_V4)
                         else "low")},
            "max_tokens": max(20000, output_cap(accepted, 'capo', 20000)),
            "context_projection": "sealed_all_rounds_red_team_dedup_v1",
            "request_sha256": fingerprint, "response_sha256": receipt["response_sha256"]}

    def _accepted_capo_finalization(self, conn, chain):
        grant = None
        for index, row in enumerate(chain):
            link = json.loads(row["request_json"]).get("continuation") or {}
            candidate = link.get("capo_finalization")
            if candidate is None and grant is None:
                continue
            if not isinstance(candidate, dict) or link.get("authorize_new_requests") is not True:
                raise RunConflict("Capo finalization authorization was removed or malformed")
            if grant is None:
                grant = self._capo_finalization_source(conn, chain[:index], candidate.get("request_id"))
            if (candidate != grant or type(candidate.get("version")) is not int
                    or link.get("budget_amendment") is not None):
                raise RunConflict("Capo finalization differs from its accepted scope")
            events = [json.loads(item[0]) for item in conn.execute(
                "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='continuation_accepted'", (row["id"],))]
            if events != [link]:
                raise RunConflict("Capo finalization has no matching acceptance proof")
        return grant

    def accepted_capo_finalization(self, run_id):
        """Read-only exact finalization grant; ordinary runs retain their defaults."""
        with self._connect(read_only=True) as conn:
            row = self._row(conn, run_id)
            if "capo_finalization" not in (json.loads(row["request_json"]).get("continuation") or {}):
                return None
            return self._accepted_capo_finalization(conn, self._ancestry(conn, run_id))

    def _truncated_response_recovery(self, conn, parent, progress, *, request_id=None):
        """Attest one paid final response; native code must still verify its wire contract."""
        def reject(reason):
            raise RunConflict("specialist report completion not eligible: " + reason)

        if parent["technical_status"] not in _FINAL or parent["technical_status"] == "completed":
            reject("run is not stopped incomplete")
        checkpoint = progress.get("checkpoint")
        if (not isinstance(checkpoint, dict) or checkpoint.get("version") != 1
                or progress.get("checkpoint_sha256") != _digest(checkpoint)):
            reject("native checkpoint integrity differs")
        accepted = json.loads(parent["request_json"])
        expected = _native_checkpoint_contract(parent, accepted)
        if checkpoint.get("contract") != expected:
            reject("accepted source or run contract differs")
        rows = checkpoint.get("specialist_checkpoints")
        if not isinstance(rows, dict):
            reject("specialist checkpoint missing")
        truncated = []
        for key, state in rows.items():
            if not isinstance(state, dict) or state.get("status") == "failed":
                reject("another specialist state is failed or malformed")
            if state.get("pending_tools") or state.get("inflight_tools"):
                reject("tool outcome unresolved")
            if state.get("status") == "truncated":
                truncated.append((key, state))
        if len(truncated) != 1:
            reject("exactly one truncated specialist is required")
        key, state = truncated[0]
        match = re.fullmatch(r"(macro|eventdesk|crypto|fundamentals|quant|options):R([01])", key)
        if match is None:
            reject("only an ordinary research round can complete its final report")
        desk, round_n = match.group(1), int(match.group(2))
        original = {name: value for name, value in state.items() if name != "sha256"}
        # This is base._checkpoint_digest's existing JSON format, not the
        # compact format used by this store for whole-run/receipt seals.
        native_digest = hashlib.sha256(json.dumps(original, sort_keys=True,
            ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        if state.get("sha256") != native_digest:
            reject("specialist checkpoint integrity differs")
        if (not isinstance(state.get("contract"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", state["contract"])
                or state.get("forced_report") is not True
                or type(state.get("iteration")) is not int or state["iteration"] <= 0
                or state.get("usage_unknown") is not False):
            reject("known final report boundary is not attested")
        original_cap = state.get("max_tokens")
        if original_cap is not None and (type(original_cap) is not int
                or original_cap not in (16000, 64000, 65536)):
            reject("no further attempt at the replacement output limit is authorized")
        messages, usage = state.get("messages"), state.get("usage")
        if (not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict)
                or messages[-1].get("role") != "user" or not isinstance(usage, dict)
                or usage.get("tokens_status") != "completo" or usage.get("tokens_missing")):
            reject("saved conversation or usage is incomplete")
        ids = usage.get("request_ids")
        if (not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids)
                or len(ids) != len(set(ids))):
            reject("request identities are incomplete")
        terminal_id = ids[-1]
        if request_id is not None and terminal_id != request_id:
            reject("authorized request is not the final response")
        failure = progress.get("primary_failure") or checkpoint.get("data", {}).get("_primary_failure")
        if (not isinstance(failure, dict) or failure.get("request_id") != terminal_id
                or failure.get("desk") != desk or type(failure.get("round")) is not int
                or failure["round"] != round_n or failure.get("phase") != "research"
                or "stop_reason=max_tokens" not in str(failure.get("message"))):
            reject("primary failure does not identify this truncated research response")
        if f"{desk}:{round_n}" in (checkpoint.get("data", {}).get("_completed_stages") or {}):
            reject("the same stage was already marked complete")
        terminal, receipt = self._validated_specialist_usage(conn, parent["id"], desk, usage)[-1]
        response = receipt["response"]
        if (receipt.get("stop_reason") != "max_tokens" or response.get("stop_reason") != "max_tokens"
                or not isinstance(response.get("content"), list)
                or any(not isinstance(block, dict) or block.get("type") == "tool_use"
                       for block in response["content"])):
            reject("response is not a terminal report truncation")
        for name in ("input_tokens", "output_tokens"):
            amount = (response.get("usage") or {}).get(name)
            if type(amount) is not int or amount < (1 if name == "output_tokens" else 0):
                reject("terminal token receipt is incomplete")
        fingerprint = receipt.get("request_sha256")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            reject("original wire digest is missing")
        events = [json.loads(row[0]) for row in conn.execute(
            "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='cost_reserved'",
            (terminal["run_id"],))]
        reserved = [event for event in events if event.get("request_id") == terminal_id]
        if (len(reserved) != 1 or reserved[0].get("request_sha256") != fingerprint
                or reserved[0].get("role") != terminal["role"]
                or reserved[0].get("model") != terminal["model"]
                or reserved[0].get("max_usd") != terminal["reserved_usd"]):
            reject("original reservation proof differs")
        settled = [json.loads(row[0]) for row in conn.execute(
            "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='cost_charged'",
            (terminal["run_id"],))]
        charged = [event for event in settled if event.get("request_id") == terminal_id]
        if len(charged) != 1 or charged[0].get("charged_usd") != terminal["charged_usd"]:
            reject("original settlement proof differs")
        return {"version": 1, "mode": "report_only", "checkpoint_key": key,
            "desk": desk, "round_n": round_n, "request_id": terminal_id,
            "source_run_id": terminal["run_id"], "specialist_checkpoint_sha256": state["sha256"],
            "original_contract": state["contract"], "response_sha256": receipt["response_sha256"],
            "request_sha256": fingerprint, "original_max_tokens": original_cap,
            "replacement_max_tokens": 128000, "native_validation_required": True}

    def _can_compile_saved_model(self, conn, parent, progress):
        """A completed paid author may leave one new deterministic compile step."""
        try:
            checkpoint = progress.get("checkpoint")
            if (parent["technical_status"] not in _FINAL or parent["technical_status"] == "completed"
                    or not isinstance(checkpoint, dict) or checkpoint.get("version") != 1
                    or progress.get("checkpoint_sha256") != _digest(checkpoint)):
                return False
            accepted = json.loads(parent["request_json"])
            expected = _native_checkpoint_contract(parent, accepted)
            if checkpoint.get("contract") != expected:
                return False
            data, rows = checkpoint.get("data") or {}, checkpoint.get("specialist_checkpoints")
            author, plan = data.get("_model_authoring_completion") or {}, data.get("_model_input_draft")
            if (not isinstance(rows, dict) or not isinstance(plan, dict) or not plan
                    or author.get("version") != 1 or author.get("mode") != "model_authoring_completion"):
                return False
            plan_sha = _digest(plan)
            last_error = data.get("_model_plan_last_error") or {}
            if not isinstance(last_error, dict) or last_error.get("plan_sha256") == plan_sha:
                return False
            attempts = data.get("_model_compilation_attempts", [])
            if (not isinstance(attempts, list) or any(not isinstance(item, dict)
                    or item.get("plan_sha256") == plan_sha for item in attempts)):
                return False
            if any(not isinstance(state, dict) or state.get("pending_tools") or state.get("inflight_tools")
                    or state.get("status") in ("failed", "truncated") for state in rows.values()):
                return False
            task = {"kind": "model_authoring_completion", "version": 1,
                    "origin_checkpoint_sha256": author["origin_checkpoint_sha256"]}
            native = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                allow_nan=False).encode("utf-8")).hexdigest()
            state = rows.get("fundamentals:R1:" + native(task)) or {}
            original = {name: value for name, value in state.items() if name != "sha256"}
            iteration = state.get("iteration")
            if (state.get("status") != "complete" or state.get("sha256") != native(original)
                    or type(iteration) is not int or not 1 <= iteration <= 30
                    or state.get("max_tokens") != 128000 or state.get("usage_unknown") is not False
                    or state.get("forced_report") is not (iteration == 30)):
                return False
            proofs = self._validated_specialist_usage(conn, parent["id"], "fundamentals", state["usage"])
            if len(proofs) != iteration:
                return False
            response = proofs[-1][1]["response"]
            text = "\n".join(block.get("text", "") for block in response.get("content", ())
                             if block.get("type") == "text" and block.get("text"))
            return bool(response.get("stop_reason") == "end_turn" and text
                        and isinstance(state.get("report"), str) and state["report"].endswith(text))
        except (RunConflict, ValueError, KeyError, TypeError, AttributeError, InvalidOperation):
            return False

    def _failed_model_response_recovery(self, conn, parent, progress, *, request_id=None):
        """Attest one explicit retry of a failed, charged model-authoring response."""
        def reject(reason):
            raise RunConflict("failed model response recovery not eligible: " + reason)

        def native_digest(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                allow_nan=False).encode("utf-8")).hexdigest()

        checkpoint = progress.get("checkpoint")
        if (parent["technical_status"] not in _FINAL or parent["technical_status"] == "completed"
                or not isinstance(checkpoint, dict) or checkpoint.get("version") != 1
                or progress.get("checkpoint_sha256") != _digest(checkpoint)):
            reject("stopped native checkpoint is not verified")
        accepted = json.loads(parent["request_json"])
        expected = _native_checkpoint_contract(parent, accepted)
        if checkpoint.get("contract") != expected:
            reject("accepted source or run contract differs")
        data = checkpoint.get("data") or {}
        author = data.get("_model_authoring_completion") or {}
        origin_sha = author.get("origin_checkpoint_sha256")
        if (author.get("version") != 1 or author.get("mode") != "model_authoring_completion"
                or not isinstance(origin_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", origin_sha)):
            reject("native model-authoring task is missing")
        task = {"kind": "model_authoring_completion", "version": 1,
                "origin_checkpoint_sha256": origin_sha}
        key = "fundamentals:R1:" + native_digest(task)
        rows = checkpoint.get("specialist_checkpoints")
        if not isinstance(rows, dict) or key not in rows:
            reject("native model-authoring checkpoint is missing")
        for name, other in rows.items():
            if (not isinstance(other, dict) or other.get("pending_tools") or other.get("inflight_tools")
                    or name != key and other.get("status") in ("failed", "truncated")):
                reject("another specialist or tool outcome is unresolved")
        state = rows[key]
        original = {name: value for name, value in state.items() if name != "sha256"}
        iteration = state.get("iteration")
        if (state.get("sha256") != native_digest(original)
                or not isinstance(state.get("contract"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", state["contract"])
                or state.get("status") != "failed" or state.get("forced_report") is not False
                or type(iteration) is not int or not 1 <= iteration < 30
                or state.get("max_tokens") != 128000 or state.get("usage_unknown") is not False
                or state.get("thinking") != {"type": "effort", "effort": "max"}
                or state.get("thinking_prima") is not None
                or state.get("retry_529") != 0 or state.get("retry_vuoto") != 0
                or state.get("response_recovery") is not None):
            reject("failed response boundary is not eligible for one explicit retry")
        messages, usage = state.get("messages"), state.get("usage")
        if (not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict)
                or messages[-1].get("role") != "user" or not isinstance(usage, dict)):
            reject("saved conversation or usage is missing")
        proofs = self._validated_specialist_usage(conn, parent["id"], "fundamentals", usage)
        if len(proofs) != iteration:
            reject("request count differs from the preserved turn counter")
        terminal, receipt = proofs[-1]
        terminal_id = terminal["request_id"]
        if request_id is not None and request_id != terminal_id:
            reject("authorized request is not the final failed response")
        failure = progress.get("primary_failure") or data.get("_primary_failure")
        if (not isinstance(failure, dict) or failure.get("request_id") != terminal_id
                or failure.get("desk") != "fundamentals" or type(failure.get("round")) is not int
                or failure["round"] != 1 or failure.get("phase") != "building"
                or "stop_reason=error" not in str(failure.get("message"))):
            reject("primary failure does not identify the selected model response")
        response = receipt["response"]
        content = response.get("content")
        if (receipt.get("stop_reason") != "error" or response.get("stop_reason") != "error"
                or response.get("finish_reason") != "error" or Decimal(terminal["charged_usd"]) != 0
                or not isinstance(content, list) or not content
                or any(not isinstance(block, dict) or block.get("type") != "thinking"
                       or not isinstance(block.get("thinking"), str) or not block["thinking"] for block in content)):
            reject("only an explicit zero-cost thinking-only provider error is supported")
        fingerprint = receipt.get("request_sha256")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            reject("original wire digest is missing")
        for kind in ("cost_reserved", "cost_charged"):
            events = [json.loads(row[0]) for row in conn.execute(
                "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind=?",
                (terminal["run_id"], kind))]
            matching = [event for event in events if event.get("request_id") == terminal_id]
            if len(matching) != 1:
                reject("original reservation or settlement proof is missing")
            event = matching[0]
            expected_event = ({"request_sha256": fingerprint, "role": terminal["role"],
                "model": terminal["model"], "max_usd": terminal["reserved_usd"]}
                if kind == "cost_reserved" else {"charged_usd": terminal["charged_usd"]})
            if any(event.get(name) != value for name, value in expected_event.items()):
                reject("original reservation or settlement proof differs")
        return {"version": 1, "kind": "failed_model_authoring", "mode": "model_authoring_error_retry",
            "checkpoint_key": key, "desk": "fundamentals", "round_n": 1,
            "request_id": terminal_id, "source_run_id": terminal["run_id"],
            "specialist_checkpoint_sha256": state["sha256"], "original_contract": state["contract"],
            "response_sha256": receipt["response_sha256"], "request_sha256": fingerprint,
            "original_max_tokens": 128000, "replacement_max_tokens": 128000,
            "original_iteration": iteration, "max_tool_iters": 30,
            "task_context": task, "native_validation_required": True}

    @_scrittura_ritentata
    def create_continuation(self, parent_run_id: str, *, idempotency_key: str,
                            authorize_new_requests: bool = False,
                            recover_truncated_request_id: str | None = None,
                            recover_failed_request_id: str | None = None,
                            authorize_price_refresh: bool = False,
                            budget_limit_usd=None,
                            resume_with_held_rejection_request_id: str | None = None,
                            capo_finalization_request_id: str | None = None):
        """One explicit successor; only a recorded explicit increase changes its ceiling."""
        if authorize_new_requests is not True:
            raise ValueError("explicit authorization for new requests is required")
        if type(authorize_price_refresh) is not bool:
            raise ValueError("authorize_price_refresh must be an explicit boolean")
        if capo_finalization_request_id is not None:
            capo_finalization_request_id = _text(capo_finalization_request_id,
                "capo_finalization_request_id", 200).strip()
            if not capo_finalization_request_id:
                raise ValueError("capo_finalization_request_id required when supplied")
            if (budget_limit_usd is not None or recover_truncated_request_id is not None
                    or recover_failed_request_id is not None or resume_with_held_rejection_request_id is not None):
                raise ValueError("Capo finalization scope cannot amend budget or other recovery authority")
        requested_budget = (None if budget_limit_usd is None else
                            _decimal(budget_limit_usd, "budget_limit_usd", positive=True))
        if resume_with_held_rejection_request_id is not None:
            resume_with_held_rejection_request_id = _text(resume_with_held_rejection_request_id,
                "resume_with_held_rejection_request_id", 200).strip()
            if not resume_with_held_rejection_request_id:
                raise ValueError("resume_with_held_rejection_request_id required when supplied")
        key = _text(idempotency_key, "idempotency_key", 200).strip()
        if not key:
            raise ValueError("idempotency_key required")
        if recover_truncated_request_id is not None:
            recover_truncated_request_id = _text(recover_truncated_request_id,
                                                 "recover_truncated_request_id", 200).strip()
            if not recover_truncated_request_id:
                raise ValueError("recover_truncated_request_id required when supplied")
        if recover_failed_request_id is not None:
            recover_failed_request_id = _text(recover_failed_request_id, "recover_failed_request_id", 200).strip()
            if not recover_failed_request_id:
                raise ValueError("recover_failed_request_id required when supplied")
            if recover_truncated_request_id is not None:
                raise ValueError("failed and truncated response recovery are mutually exclusive")
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                parent = self._row(conn, parent_run_id)
                budget = requested_budget if requested_budget is not None else parent["budget_limit_usd"]
                if requested_budget is not None and Decimal(budget) <= Decimal(parent["budget_limit_usd"]):
                    raise ValueError("budget_limit_usd must explicitly increase the accepted parent ceiling")
                prior = conn.execute("SELECT id,request_json FROM trade_idea_runs WHERE idempotency_key=?", (key,)).fetchone()
                if prior and (json.loads(prior["request_json"]).get("continuation") or {}).get("parent_run_id") != parent_run_id:
                    raise IdempotencyConflict("idempotency key belongs to another request")
                successor = conn.execute("SELECT id,request_json FROM trade_idea_runs WHERE json_extract(request_json,'$.continuation.parent_run_id')=?",
                                         (parent_run_id,)).fetchone()
                if successor:
                    successor_request = json.loads(successor["request_json"])
                    existing_finalization = (successor_request.get("continuation") or {}).get("capo_finalization")
                    if (capo_finalization_request_id is not None and
                            (existing_finalization or {}).get("request_id") != capo_finalization_request_id):
                        raise IdempotencyConflict("successor belongs to a different Capo finalization")
                    if existing_finalization is not None:
                        self._accepted_capo_finalization(conn, self._ancestry(conn, successor["id"]))
                    if (requested_budget is not None
                            and Decimal(successor_request["budget_limit_usd"]) != Decimal(requested_budget)):
                        raise IdempotencyConflict("successor belongs to a different budget amendment")
                    if (resume_with_held_rejection_request_id is not None and
                            ((successor_request.get("continuation") or {}).get("held_rejection") or {}).get("request_id")
                            != resume_with_held_rejection_request_id):
                        raise IdempotencyConflict("successor belongs to a different held rejection")
                    existing_recovery = (successor_request.get("continuation") or {}).get("specialist_response_recovery") or {}
                    if (recover_truncated_request_id is not None
                            and (existing_recovery.get("request_id") != recover_truncated_request_id
                                 or existing_recovery.get("mode") != "report_only")):
                        raise IdempotencyConflict("successor belongs to a different response recovery")
                    if (recover_failed_request_id is not None
                            and (existing_recovery.get("request_id") != recover_failed_request_id
                                 or existing_recovery.get("kind") != "failed_model_authoring")):
                        raise IdempotencyConflict("successor belongs to a different response recovery")
                    conn.execute("COMMIT")
                    return {**self.get_run(successor["id"]), "created": False}
                if parent["technical_status"] not in _FINAL or parent["technical_status"] == "completed":
                    raise RunConflict("only a stopped incomplete run can have a continuation")
                chain = self._ancestry(conn, parent_run_id)
                finalization = self._accepted_capo_finalization(conn, chain)
                if finalization is not None and requested_budget is not None:
                    raise ValueError("Capo finalization scope cannot amend budget")
                if capo_finalization_request_id is not None:
                    if finalization is None:
                        finalization = self._capo_finalization_source(conn, chain, capo_finalization_request_id)
                    elif finalization["request_id"] != capo_finalization_request_id:
                        raise RunConflict("Capo finalization already authorized for another request")
                parent_request = json.loads(parent["request_json"])
                held = (parent_request.get("continuation") or {}).get("held_rejection")
                if resume_with_held_rejection_request_id is not None:
                    if held is not None and held["request_id"] != resume_with_held_rejection_request_id:
                        raise RunConflict("a different unknown request is not covered by the accepted hold")
                    if held is None:
                        held = self._authorize_held_rejection(conn, chain, resume_with_held_rejection_request_id)
                summary = self._cost_summary(conn, parent_run_id, budget, pending_held_rejection=held)
                if finalization is not None and summary["unknown_requests"]:
                    raise BudgetBlocked("Capo finalization cannot bypass an unknown provider cost")
                if summary["unacknowledged_unknown_requests"] or Decimal(summary["reserved_usd"]):
                    raise BudgetBlocked("unresolved provider requests must be reconciled before continuation")
                if summary["overrun"] or Decimal(summary["remaining_after_holds_usd"]) <= 0:
                    budget_kind = "amended" if requested_budget is not None else "original"
                    raise BudgetBlocked(f"{budget_kind} aggregate budget has no available capacity")
                progress = json.loads(parent["progress_json"])
                checkpoint = progress.get("checkpoint")
                if (not isinstance(checkpoint, dict) or checkpoint.get("version") != 1
                        or progress.get("checkpoint_sha256") != _digest(checkpoint)):
                    raise RunConflict("verified native checkpoint unavailable; no paid work replayed")
                checkpoint_block = _checkpoint_resume_block(checkpoint)
                if checkpoint_block == "model_authoring_review_required" and self._can_compile_saved_model(conn, parent, progress):
                    checkpoint_block = None
                request = json.loads(parent["request_json"])
                recovery = (request.get("continuation") or {}).get("specialist_response_recovery")
                if recover_failed_request_id is not None or (recovery
                        and recovery.get("kind") == "failed_model_authoring"
                        and checkpoint_block == "incomplete_response_review_required"):
                    verified = self._failed_model_response_recovery(conn, parent, progress,
                        request_id=recover_failed_request_id or recovery.get("request_id"))
                    if (recovery is not None and recovery.get("kind") == "failed_model_authoring"
                            and verified != recovery):
                        raise RunConflict("previous failed response authorization differs")
                    recovery, checkpoint_block = verified, None
                elif recover_truncated_request_id is not None or (
                        recovery and checkpoint_block == "incomplete_response_review_required"):
                    verified = self._truncated_response_recovery(conn, parent, progress,
                        request_id=recover_truncated_request_id or recovery.get("request_id"))
                    if recovery is not None and verified != recovery:
                        raise RunConflict("previous report completion authorization differs")
                    recovery = verified
                    checkpoint_block = None
                if checkpoint_block:
                    raise RunConflict(checkpoint_block + ": no automatic continuation")
                context = json.loads(parent["context_json"])
                book, feedback = self._context(conn, parent["ticker"],
                    exclude_research_ids=[row["destination_decision_id"] for row in chain])
                current = _with_mandate_text_policy(
                    {"book": book, "feedback": feedback, "mandate_sha256": self._mandate_loader()}, context)
                price_grant = _price_refresh_grant(request, context)
                if not _same_non_price_context(context, current):
                    raise RunConflict("book, feedback or mandate changed: targeted review required before continuation")
                if current != context and price_grant is None and not authorize_price_refresh:
                    raise RunConflict("price inputs changed: explicit price refresh authorization required")
                if price_grant is not None or authorize_price_refresh:
                    self._validate_price_refresh_units(conn)
                if authorize_price_refresh and price_grant is None:
                    price_grant = {"version": 1, "scope": "portfolio_price_inputs",
                        "accepted_context_sha256": _digest(context),
                        "accepted_price_inputs_sha256": context["book"]["price_inputs_sha256"],
                        "observed_price_inputs_sha256": book["price_inputs_sha256"], "observed_at": now,
                        "authorize_price_refresh": True, "require_fresh_final_verification": True}
                request["continuation"] = {"parent_run_id": parent_run_id,
                    "root_run_id": chain[0]["id"], "parent_request_sha256": parent["request_sha256"],
                    "checkpoint_sha256": progress["checkpoint_sha256"],
                    "authorized_at": now, "authorize_new_requests": True}
                if requested_budget is not None:
                    request["continuation"]["budget_amendment"] = {"version": 1,
                        "scope": "aggregate_chain_budget", "parent_run_id": parent_run_id,
                        "parent_request_sha256": parent["request_sha256"],
                        "previous_budget_limit_usd": parent["budget_limit_usd"],
                        "budget_limit_usd": budget, "authorized_at": now,
                        "authorize_budget_increase": True}
                    request["budget_limit_usd"] = budget
                if recovery is not None:
                    request["continuation"]["specialist_response_recovery"] = recovery
                if finalization is not None:
                    request["continuation"]["capo_finalization"] = finalization
                if price_grant is not None:
                    request["continuation"]["price_refresh"] = price_grant
                if held is not None:
                    request["continuation"]["held_rejection"] = held
                run_id = str(uuid.uuid4())
                inherited = {"phase": "accepted", "checkpoint": checkpoint,
                    "checkpoint_sha256": progress["checkpoint_sha256"],
                    "recovery": {"parent_run_id": parent_run_id, "root_run_id": chain[0]["id"],
                        "status": "accepted", "historical_failure": progress.get("primary_failure")}}
                try:
                    conn.execute("""INSERT INTO trade_idea_runs(
                        id,idempotency_key,request_sha256,request_json,ticker,company_name,exchange,currency,language,
                        view_text,view_origin,models_json,catalog_snapshot_json,budget_limit_usd,context_json,
                        technical_status,phase,progress_json,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'accepted','accepted',?,?,?)""",
                        (run_id, key, _digest(request), _json(request), parent["ticker"], parent["company_name"],
                         parent["exchange"], parent["currency"], parent["language"], parent["view_text"],
                         parent["view_origin"], parent["models_json"], parent["catalog_snapshot_json"],
                         budget, parent["context_json"], _json(inherited), now, now))
                except sqlite3.IntegrityError as exc:
                    raise RunConflict("another Trade Idea run is active") from exc
                conn.execute("INSERT INTO trade_idea_delivery(run_id,updated_at) VALUES(?,?)", (run_id, now))
                self._event(conn, run_id, "continuation_accepted", request["continuation"], now)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return {**self.get_run(run_id), "created": True}

    @_scrittura_ritentata
    def record_failure(self, run_id, worker_token, failure):
        """First cause is durable; downstream errors cannot overwrite it."""
        if not isinstance(failure, dict) or not failure.get("message"):
            raise ValueError("structured failure required")
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["technical_status"] != "running" or row["worker_token"] != worker_token:
                    raise RunConflict("worker claim lost")
                progress = json.loads(row["progress_json"])
                if not progress.get("primary_failure"):
                    progress["primary_failure"] = {**failure, "at": now}
                first = progress["primary_failure"]
                conn.execute("UPDATE trade_idea_runs SET progress_json=?,updated_at=? WHERE id=?", (_json(progress), now, run_id))
                self._event(conn, run_id, "execution_failure", failure, now)
                conn.execute("COMMIT")
                return first
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def claim_run(self, run_id: str):
        """Claim accepted work exactly once; a running/interrupted run cannot replay."""
        token, now = str(uuid.uuid4()), self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["technical_status"] != "accepted" or row["stop_requested"]:
                    raise RunConflict("run already claimed, stopped or final")
                conn.execute("UPDATE trade_idea_runs SET technical_status='running',phase='starting',"
                             "worker_token=?,started_at=?,updated_at=? WHERE id=?",
                             (token, now, now, run_id))
                self._event(conn, run_id, "claimed", {}, now)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return token

    @_scrittura_ritentata
    def update_progress(self, run_id: str, worker_token: str, phase: str, progress: dict):
        phase = _text(phase, "phase", 120)
        if not isinstance(progress, dict):
            raise ValueError("progress must be a JSON object")
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["technical_status"] != "running" or row["worker_token"] != worker_token:
                    raise RunConflict("worker claim lost")
                previous = json.loads(row["progress_json"])
                progress = {**progress}
                for key in ("primary_failure", "recovery"):
                    if previous.get(key) is not None:
                        progress[key] = previous[key]
                # A phase-only update must not discard the last durable checkpoint.
                for key in ("checkpoint", "checkpoint_sha256"):
                    if key not in progress and key in previous:
                        progress[key] = previous[key]
                encoded = _json(progress)
                conn.execute("UPDATE trade_idea_runs SET phase=?,progress_json=?,updated_at=? WHERE id=?",
                             (phase, encoded, now, run_id))
                # The latest complete checkpoint is atomically in the run row;
                # provider receipts remain immutable cost rows. Do not multiply
                # every historical conversation into every audit event.
                event = ({"phase": phase, "checkpoint_sha256": progress["checkpoint_sha256"],
                          "progress_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                          "primary_failure": progress.get("primary_failure")}
                         if "checkpoint_sha256" in progress else {"phase": phase, "progress": progress})
                self._event(conn, run_id, "progress", event, now)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def request_stop(self, run_id: str):
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["technical_status"] in _FINAL:
                    conn.execute("COMMIT")
                    return False
                if row["technical_status"] == "accepted":
                    conn.execute("UPDATE trade_idea_runs SET stop_requested=1,technical_status='cancelled',"
                                 "phase='cancelled',finished_at=?,updated_at=?,reason='Stopped before work' "
                                 "WHERE id=?", (now, now, run_id))
                else:
                    conn.execute("UPDATE trade_idea_runs SET stop_requested=1,updated_at=? WHERE id=?",
                                 (now, run_id))
                self._event(conn, run_id, "stop_requested", {}, now)
                conn.execute("COMMIT")
                return True
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def finish_run(self, run_id: str, worker_token: str, result, technical_status: str,
                   reason: str | None = None):
        if technical_status not in _FINAL:
            raise ValueError("final technical status required")
        if reason is not None:
            reason = _text(reason, "reason", 4000, empty=True)
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["technical_status"] != "running" or row["worker_token"] != worker_token:
                    raise RunConflict("worker claim lost or run already final")
                normalized = None
                if result is not None:
                    source = result
                    if isinstance(source, dict):
                        for name, expected in (("run_id", run_id), ("run_type", "trade_idea"),
                                               ("pm_view", row["view_text"])):
                            if name in source and source[name] != expected:
                                raise ValueError(f"result {name} differs from accepted run")
                        source = {k: v for k, v in source.items()
                                  if k not in ("run_id", "run_type", "pm_view")}
                    normalized = validate_run_result(source, run_id=run_id, ticker=row["ticker"],
                                                     pm_view=row["view_text"],
                                                     policy=execution_policy(json.loads(row["request_json"])))
                if technical_status == "completed" and normalized is None:
                    raise ValueError("completed research requires a structured result")
                primary = json.loads(row["progress_json"]).get("primary_failure")
                if primary:
                    cause = str(primary.get("message") or "Recorded execution failure")
                    if reason and reason != cause:
                        cause += "; Consequence: " + reason
                    reason = cause[:4000]
                    if technical_status == "completed":
                        technical_status = "incomplete"
                conn.execute("UPDATE trade_idea_costs SET status='unknown',reason='Run ended before provider receipt',updated_at=? "
                             "WHERE run_id=? AND status='reserved'", (now, run_id))
                costs = self._cost_summary(conn, run_id, row["budget_limit_usd"])
                if technical_status == "completed" and (costs["unknown_requests"] or costs["overrun"]):
                    technical_status = "incomplete"
                    reason = (reason + "; " if reason else "") + "Provider costs unresolved or over budget"
                conn.execute("UPDATE trade_idea_runs SET technical_status=?,phase=?,result_json=?,reason=?,"
                             "worker_token=NULL,updated_at=?,finished_at=? WHERE id=?",
                             (technical_status, technical_status,
                              _json(normalized) if normalized is not None else None,
                              reason, now, now, run_id))
                self._event(conn, run_id, "finished", {"status": technical_status,
                                                     "has_result": normalized is not None,
                                                     "reason": reason}, now)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return self.get_run(run_id)

    @_scrittura_ritentata
    def interrupt_run(self, run_id: str, *, reason: str):
        """Explicit recovery after the owner process is known dead; never resumes it."""
        reason, now = _text(reason, "reason", 4000), self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["technical_status"] != "running":
                    conn.execute("COMMIT")
                    return False
                conn.execute("UPDATE trade_idea_runs SET technical_status='interrupted',"
                             "phase='interrupted',worker_token=NULL,reason=?,updated_at=?,finished_at=? "
                             "WHERE id=?", (reason, now, now, run_id))
                conn.execute("UPDATE trade_idea_costs SET status='unknown',"
                             "reason='Owner interrupted while reservation outstanding',updated_at=? "
                             "WHERE run_id=? AND status='reserved'", (now, run_id))
                self._event(conn, run_id, "interrupted", {"reason": reason}, now)
                conn.execute("COMMIT")
                return True
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def cancel_run_after_stop(self, run_id: str, *, reason: str):
        """Called by API only after it confirms the paid worker has stopped."""
        reason, now = _text(reason, "reason", 4000), self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["technical_status"] == "cancelled":
                    conn.execute("COMMIT")
                    return False
                if row["technical_status"] != "running" or not row["stop_requested"]:
                    raise RunConflict("cancellation requires a stopped claimed worker")
                conn.execute("UPDATE trade_idea_runs SET technical_status='cancelled',"
                             "phase='cancelled',worker_token=NULL,reason=?,updated_at=?,finished_at=? "
                             "WHERE id=?", (reason, now, now, run_id))
                conn.execute("UPDATE trade_idea_costs SET status='unknown',"
                             "reason='Worker cancelled while reservation outstanding',updated_at=? "
                             "WHERE run_id=? AND status='reserved'", (now, run_id))
                self._event(conn, run_id, "cancelled", {"reason": reason}, now)
                conn.execute("COMMIT")
                return True
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def _cost_summary(self, conn, run_id, budget, *, pending_held_rejection=None):
        ancestry = self._ancestry(conn, run_id)
        ids = [row["id"] for row in ancestry]
        held = (json.loads(ancestry[-1]["request_json"]).get("continuation") or {}).get("held_rejection")
        if pending_held_rejection is not None:
            self._validate_held_rejection(conn, pending_held_rejection, set(ids))
            held = pending_held_rejection
        rows = conn.execute("SELECT request_id,run_id,role,status,reserved_usd,charged_usd FROM trade_idea_costs "
                            "WHERE run_id IN (" + ",".join("?" for _ in ids) + ")", ids).fetchall()
        charged = sum((Decimal(r["charged_usd"]) for r in rows if r["status"] == "charged"), Decimal(0))
        reserved = sum((Decimal(r["reserved_usd"]) for r in rows if r["status"] == "reserved"), Decimal(0))
        unknown = sum(1 for r in rows if r["status"] == "unknown")
        acknowledged = sum(r["status"] == "unknown" and held is not None
                           and r["request_id"] == held["request_id"] for r in rows)
        unknown_reserved = sum((Decimal(r["reserved_usd"]) for r in rows if r["status"] == "unknown"), Decimal(0))
        overrun_ids = [r["request_id"] for r in rows if r["status"] == "charged"
                       and Decimal(r["charged_usd"]) > Decimal(r["reserved_usd"])]
        budget_exceeded = charged + reserved > Decimal(budget)
        by_phase = {}
        for phase in ("model_preparation", "committee", "model_revision"):
            selected = [row for row in rows if self._cost_phase(row["role"]) == phase]
            by_phase[phase] = {"requests": len(selected),
                "charged_usd": str(sum((Decimal(row["charged_usd"]) for row in selected
                    if row["status"] == "charged"), Decimal(0))),
                "reserved_usd": str(sum((Decimal(row["reserved_usd"]) for row in selected
                    if row["status"] == "reserved"), Decimal(0))),
                "unknown_requests": sum(row["status"] == "unknown" for row in selected)}
        return {"budget_limit_usd": budget, "charged_usd": str(charged),
                 "reserved_usd": str(reserved), "unknown_requests": unknown,
                 "held_unknown_requests": acknowledged,
                 "unacknowledged_unknown_requests": unknown - acknowledged,
                 "unknown_reserved_usd": str(unknown_reserved),
                 "root_run_id": ids[0], "chain_run_ids": ids,
                 "remaining_after_holds_usd": str(Decimal(budget) - charged - reserved - unknown_reserved),
                 "overrun": bool(overrun_ids or budget_exceeded),
                 "overrun_request_ids": sorted(overrun_ids),
                 "budget_exceeded": budget_exceeded,
                 "requests": len(rows), "by_phase": by_phase,
                 "remaining_known_usd": str(Decimal(budget) - charged - reserved)
                 if not unknown else None}

    @staticmethod
    def _cost_phase(role):
        return ("model_revision" if role == "aux:revision" or role.endswith(":model_revision") else
                "model_preparation" if role == "aux" or role.startswith("aux:") else "committee")

    @_scrittura_ritentata
    def reserve_cost(self, run_id: str, request_id: str, role: str, model: str, max_usd,
                     *, request_sha256=None, worker_token=None, capo_request_body=None):
        request_id = _text(request_id, "request_id", 200)
        role, model = _text(role, "role", 100), _text(model, "model", 200)
        reserved = _decimal(max_usd, "max_usd", positive=True)
        if request_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
            raise ValueError("request fingerprint invalid")
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if worker_token is not None and row["worker_token"] != worker_token:
                    raise RunConflict("worker claim lost before provider reservation")
                prior = conn.execute("SELECT * FROM trade_idea_costs WHERE request_id=?",
                                     (request_id,)).fetchone()
                if prior:
                    if (prior["run_id"], prior["role"], prior["model"], prior["reserved_usd"]) != (
                            run_id, role, model, reserved):
                        raise IdempotencyConflict("cost request id reused with different reservation")
                    conn.execute("COMMIT")
                    # A reservation can precede an unknown provider outcome.
                    # The same request id must never authorize a second call.
                    return False
                if row["technical_status"] != "running" or row["stop_requested"]:
                    raise BudgetBlocked("run is not active or stop was requested")
                policy = execution_policy(json.loads(row["request_json"]))
                saved_capo = None
                if role == "capo" and policy in RESEARCH_POLICIES:
                    if (not isinstance(capo_request_body, dict)
                            or _digest(capo_request_body) != request_sha256
                            or capo_request_body.get("model") != model
                            or not isinstance(capo_request_body.get("messages"), list)
                            or not capo_request_body["messages"]):
                        raise RunConflict("Exact Capo request body/model/fingerprint required before reservation")
                    checkpoint_sha, bindings = _capo_request_bindings(row)
                    saved_capo = {"version": 1, "request_id": request_id,
                        "request_sha256": request_sha256, "request_body": capo_request_body,
                        "checkpoint_sha256": checkpoint_sha, "economic_bindings": bindings,
                        "economic_bindings_sha256": _digest(bindings)}
                elif capo_request_body is not None:
                    raise RunConflict("Exact Capo request storage cannot change a legacy or other-role contract")
                if "capo_finalization" in (json.loads(row["request_json"]).get("continuation") or {}):
                    chain = self._ancestry(conn, run_id)
                    finalization = self._accepted_capo_finalization(conn, chain)
                    if role != "capo" or model != finalization["model"]:
                        raise BudgetBlocked("Capo finalization authorizes only its accepted Capo model")
                    granted_ids = [item["id"] for item in chain if
                        "capo_finalization" in (json.loads(item["request_json"]).get("continuation") or {})]
                    if conn.execute("SELECT 1 FROM trade_idea_costs WHERE run_id IN ("
                            + ",".join("?" for _ in granted_ids) + ") AND NOT (status='released' "
                            "AND json_extract(receipt_json,'$.billable')=0) LIMIT 1", granted_ids).fetchone():
                        raise BudgetBlocked("Capo finalization authorizes only one new request; authorization consumed")
                role_key = ("specialist" if role.startswith("specialist:") else
                            "aux" if role.startswith("aux:") else role)
                selected = json.loads(row["models_json"]).get(role_key)
                if not isinstance(selected, dict) or selected.get("model") != model:
                    raise BudgetBlocked("request model differs from accepted role snapshot")
                grant = json.loads(row["request_json"]).get("authorization") or {}
                activity = self._cost_phase(role)
                if grant.get("accepted") is not True or activity not in grant.get("activities", ()):
                    raise BudgetBlocked("run authorization does not allow activity " + activity)
                summary = self._cost_summary(conn, run_id, row["budget_limit_usd"])
                if summary["unacknowledged_unknown_requests"]:
                    raise BudgetBlocked("unknown provider cost: reconcile before more spending")
                if summary["overrun"]:
                    raise BudgetBlocked("measured provider cost overrun: no further spending")
                if Decimal(summary["remaining_after_holds_usd"]) < Decimal(reserved):
                    raise BudgetBlocked("per-run cost limit would be exceeded")
                conn.execute("INSERT INTO trade_idea_costs(request_id,run_id,role,model,reserved_usd,"
                             "status,created_at,updated_at) VALUES(?,?,?,?,?,'reserved',?,?)",
                             (request_id, run_id, role, model, reserved, now, now))
                self._event(conn, run_id, "cost_reserved", {"request_id": request_id,
                                                           "role": role, "model": model,
                                                           "max_usd": reserved,
                                                           "request_sha256": request_sha256}, now)
                if saved_capo is not None:
                    self._event(conn, run_id, "capo_request_saved", saved_capo, now)
                conn.execute("COMMIT")
                return True
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def reusable_capo_response(self, run_id, worker_token):
        """Replay one exact paid response without rebuilding its dynamic prompt.

        This accepts no replacement fingerprint and grants no new request. The
        ordinary parser, research quality and operational routing still run.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["technical_status"] != "running" or row["worker_token"] != worker_token:
                    raise RunConflict("worker claim lost")
                accepted = json.loads(row["request_json"])
                if execution_policy(accepted) not in RESEARCH_POLICIES:
                    conn.execute("COMMIT")
                    return None
                chain = self._ancestry(conn, run_id)
                ancestors = {item["id"]: item for item in chain[:-1]}
                if not ancestors:
                    conn.execute("COMMIT")
                    return None
                events = conn.execute("SELECT * FROM trade_idea_events WHERE run_id IN ("
                    + ",".join("?" for _ in ancestors) + ") AND kind='capo_request_saved' ORDER BY id DESC",
                    list(ancestors)).fetchall()
                def unbilled(event):
                    request_id = json.loads(event["payload_json"]).get("request_id")
                    row_cost = conn.execute("SELECT status, receipt_json FROM trade_idea_costs "
                        "WHERE request_id=? AND run_id=?", (request_id, event["run_id"])).fetchone()
                    return bool(row_cost and row_cost["status"] == "released"
                                and json.loads(row_cost["receipt_json"] or "{}").get("billable") is False)
                events = [event for event in events if not unbilled(event)]
                if not events:
                    conn.execute("COMMIT")
                    return None
                event = events[0]
                saved = json.loads(event["payload_json"])
                source = ancestors[event["run_id"]]
                _, current_bindings = _capo_request_bindings(row)
                _, source_bindings = _capo_request_bindings(source)
                if (saved.get("version") != 1 or saved.get("economic_bindings") != source_bindings
                        or saved.get("economic_bindings") != current_bindings
                        or saved.get("economic_bindings_sha256") != _digest(current_bindings)):
                    raise RunConflict("Paid Capo economic bindings changed; targeted review required")
                if (_price_refresh_grant(accepted, json.loads(row["context_json"])) is not None
                        or self._current_run_context(conn, row) != json.loads(row["context_json"])):
                    raise RunConflict("Paid Capo accepted context changed; targeted review required")
                cost = conn.execute("SELECT * FROM trade_idea_costs WHERE request_id=? AND run_id=?",
                                    (saved.get("request_id"), source["id"])).fetchone()
                body = saved.get("request_body")
                selected = json.loads(row["models_json"])["capo"]["model"]
                if (cost is None or cost["role"] != "capo" or cost["model"] != selected
                        or not isinstance(body, dict) or body.get("model") != selected
                        or saved.get("request_sha256") != _digest(body)):
                    raise RunConflict("Saved Capo request identity or fingerprint differs")
                reserved = [json.loads(item[0]) for item in conn.execute(
                    "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='cost_reserved' "
                    "AND json_extract(payload_json,'$.request_id')=?", (source["id"], cost["request_id"]))]
                checkpoint_event = conn.execute("SELECT 1 FROM trade_idea_events WHERE run_id=? AND kind='progress' "
                    "AND json_extract(payload_json,'$.checkpoint_sha256')=? AND id<? LIMIT 1",
                    (source["id"], saved.get("checkpoint_sha256"), event["id"])).fetchone()
                expected_reservation = {"request_id": cost["request_id"], "role": "capo", "model": selected,
                    "max_usd": cost["reserved_usd"], "request_sha256": saved["request_sha256"]}
                if reserved != [expected_reservation] or checkpoint_event is None:
                    raise RunConflict("Saved Capo request lacks its exact reservation or checkpoint receipt")
                if cost["status"] != "charged":
                    raise BudgetBlocked("Paid Capo cost is unresolved; no request replayed")
                receipt = json.loads(cost["receipt_json"] or "{}")
                response = receipt.get("response")
                usage = json.loads(cost["usage_json"] or "{}")
                if (receipt.get("complete") is False or not isinstance(response, dict)
                        or receipt.get("request_sha256") != saved["request_sha256"]
                        or receipt.get("response_sha256") != _digest(response)
                        or not isinstance(receipt.get("response_id"), str) or not receipt["response_id"]
                        or response.get("id") != receipt["response_id"]
                        or receipt.get("model") != response.get("model") or response.get("model") != selected
                        or response.get("stop_reason") != "end_turn"):
                    raise RunConflict("Paid Capo response integrity, identity or completion differs")
                try:
                    if usage != response.get("usage"):
                        raise ValueError("complete measured usage differs from the provider receipt")
                    measured = [usage.get("cost_usd"), (response.get("usage") or {}).get("cost_usd")]
                    if any(isinstance(value, bool) or value is None or
                           Decimal(_decimal(str(value), "paid Capo cost")) != Decimal(cost["charged_usd"])
                           for value in measured):
                        raise ValueError("usage cost differs")
                    from bellomberg.core.trade_idea_contract import parse_capo_json, validate_result
                    content = "\n".join(block["text"] for block in response.get("content", [])
                        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"))
                    parsed = parse_capo_json(content)
                    # A provider response is always the policy's own model, never the
                    # server incomplete package: validate_result, not validate_run_result.
                    result = validate_result(parsed, run_id=run_id, ticker=row["ticker"], pm_view=row["view_text"],
                                             execution_policy=execution_policy(accepted))
                    if parsed != {key: value for key, value in result.items()
                                  if key not in {"run_id", "run_type", "pm_view"}}:
                        raise ValueError("provider omitted complete contract fields")
                except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
                    raise RunConflict("Paid Capo public contract or measured usage differs; no request replayed") from exc
                proof = {"request_id": cost["request_id"], "source_run_id": source["id"],
                    "request_sha256": saved["request_sha256"], "response_sha256": receipt["response_sha256"],
                    "economic_bindings_sha256": saved["economic_bindings_sha256"], "kind": "exact_capo_request_v1"}
                prior = [json.loads(item[0]) for item in conn.execute(
                    "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='response_reused' "
                    "AND json_extract(payload_json,'$.request_id')=?", (run_id, cost["request_id"]))]
                if prior and prior != [proof]:
                    raise RunConflict("Paid Capo reuse receipt differs")
                if not prior:
                    self._event(conn, run_id, "response_reused", proof, self._at())
                conn.execute("COMMIT")
                return {**response, "request_id": cost["request_id"]}
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def reusable_response(self, run_id, worker_token, *, role, request_sha256):
        """Recover a received ancestor response once, never redispatch it."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["technical_status"] != "running" or row["worker_token"] != worker_token:
                    raise RunConflict("worker claim lost")
                ids = [item["id"] for item in self._ancestry(conn, run_id)[:-1]]
                response = None
                if ids:
                    costs = conn.execute("SELECT * FROM trade_idea_costs WHERE run_id IN ("
                        + ",".join("?" for _ in ids) + ") AND role=? AND status='charged' ORDER BY created_at DESC",
                        [*ids, role]).fetchall()
                    consumed = {json.loads(item[0]).get("request_id") for item in conn.execute(
                        "SELECT payload_json FROM trade_idea_events WHERE run_id=? AND kind='response_reused'", (run_id,))}
                    for cost in costs:
                        receipt = json.loads(cost["receipt_json"] or "{}")
                        if (receipt.get("request_sha256") == request_sha256
                                and cost["request_id"] not in consumed):
                            saved = receipt.get("response")
                            if receipt.get("complete") is False:
                                # Cost reconciliation does not turn a lost stream
                                # into a complete analytical response. Keep its
                                # fingerprint and bill; a successor reserves its
                                # own request through the ordinary budget gate.
                                partial = receipt.get("partial_response")
                                try:
                                    measured = json.loads(cost["usage_json"] or "{}").get("cost_usd")
                                    valid_partial = (saved is None and isinstance(partial, dict)
                                        and receipt.get("partial_response_sha256") == _digest(partial)
                                        and isinstance(receipt.get("response_id"), str) and bool(receipt["response_id"])
                                        and partial.get("id") == receipt["response_id"]
                                        and partial.get("model") == receipt.get("model") == cost["model"]
                                        and not isinstance(measured, bool) and measured is not None
                                        and Decimal(_decimal(str(measured), "partial response cost"))
                                            == Decimal(cost["charged_usd"]))
                                except (ValueError, TypeError, InvalidOperation):
                                    valid_partial = False
                                if not valid_partial:
                                    raise RunConflict("incomplete paid response integrity or identity differs; no request replayed")
                                continue
                            if (not isinstance(saved, dict) or receipt.get("response_sha256") != _digest(saved)
                                    or saved.get("id") != receipt.get("response_id")
                                    or saved.get("model") != cost["model"]):
                                raise RunConflict("saved paid response integrity or identity differs; no request replayed")
                            try:
                                observed = (saved.get("usage") or {}).get("cost_usd")
                                valid_cost = (not isinstance(observed, bool) and observed is not None
                                    and Decimal(_decimal(str(observed), "saved response cost")) == Decimal(cost["charged_usd"]))
                            except (ValueError, InvalidOperation, TypeError):
                                valid_cost = False
                            if not valid_cost:
                                raise RunConflict("saved paid response cost differs; no request replayed")
                            response = {**receipt["response"], "request_id": cost["request_id"]}
                            self._event(conn, run_id, "response_reused", {"request_id": cost["request_id"],
                                "source_run_id": cost["run_id"], "request_sha256": request_sha256}, self._at())
                            break
                conn.execute("COMMIT")
                return response
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def _settle_cost(self, run_id, request_id, status, *, charged=None, usage=None,
                     receipt=None, reason=None):
        if status not in ("charged", "released", "unknown"):
            raise ValueError("invalid cost settlement")
        if status == "charged":
            charged = _decimal(charged, "charged_usd")
            if not isinstance(usage, dict) or not isinstance(receipt, dict):
                raise ValueError("measured usage and provider receipt required")
        elif not reason:
            raise ValueError("release/unknown reason required")
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cost = conn.execute("SELECT * FROM trade_idea_costs WHERE request_id=? AND run_id=?",
                                    (request_id, run_id)).fetchone()
                if cost is None:
                    raise KeyError("cost reservation absent")
                # A delayed provider receipt can resolve an unknown outcome;
                # it never authorizes dispatching the same request again.
                if status == "charged":
                    if not isinstance(receipt.get("response_id"), str) or not receipt["response_id"].strip():
                        raise ValueError("settlement requires a provider response id")
                    if receipt.get("model") != cost["model"]:
                        raise ValueError("provider response model differs from reservation")
                    observed = usage.get("cost_usd")
                    if isinstance(observed, bool) or not isinstance(observed, (str, int, float, Decimal)):
                        raise ValueError("provider usage.cost_usd missing or invalid")
                    if Decimal(_decimal(str(observed), "usage.cost_usd")) != Decimal(charged):
                        raise ValueError("provider usage differs from charged amount")
                if cost["status"] == "unknown" and status == "released":
                    # Internal callers must obtain this receipt from the provider,
                    # not infer a free request from a timeout or absent response.
                    proof = receipt if isinstance(receipt, dict) else {}
                    if (proof.get("request_id") != request_id or proof.get("billable") is not False
                            or not isinstance(proof.get("response_id"), str) or not proof["response_id"].strip()
                            or proof.get("provider") != "openrouter"
                            or str(proof.get("cost_usd")) not in ("0", "0.0", "0.00")):
                        raise ValueError("unknown cost requires matching provider nonbilling receipt")
                if cost["status"] != "reserved":
                    if (cost["status"] == status and cost["charged_usd"] == charged
                            and cost["usage_json"] == (_json(usage) if usage is not None else None)
                            and cost["receipt_json"] == (_json(receipt) if receipt is not None else None)):
                        conn.execute("COMMIT")
                        return False
                    if cost["status"] != "unknown" or status not in ("charged", "released"):
                        raise RunConflict("cost reservation already settled differently")
                conn.execute("UPDATE trade_idea_costs SET status=?,charged_usd=?,usage_json=?,"
                             "receipt_json=?,reason=?,updated_at=? WHERE request_id=?",
                             (status, charged, _json(usage) if usage is not None else None,
                              _json(receipt) if receipt is not None else None, reason, now, request_id))
                self._event(conn, run_id, "cost_" + status,
                            {"request_id": request_id, "charged_usd": charged, "reason": reason}, now)
                if status == "charged":
                    run = self._row(conn, run_id)
                    summary = self._cost_summary(conn, run_id, run["budget_limit_usd"])
                    if summary["overrun"]:
                        self._event(conn, run_id, "cost_overrun",
                                    {"request_id": request_id,
                                     "charged_usd": charged,
                                     "reserved_usd": cost["reserved_usd"],
                                     "total_charged_usd": summary["charged_usd"],
                                     "budget_limit_usd": run["budget_limit_usd"]}, now)
                conn.execute("COMMIT")
                return True
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def unknown_costs(self, run_id):
        """Read-only: every unknown-cost request in this run's continuation chain."""
        with self._connect() as conn:
            chain = self._ancestry(conn, run_id)
            ids = [row["id"] for row in chain]
            rows = conn.execute("SELECT * FROM trade_idea_costs WHERE status='unknown' AND run_id IN ("
                                + ",".join("?" for _ in ids) + ") ORDER BY rowid", ids).fetchall()
        return [dict(row) for row in rows]

    def reconcile_cost(self, run_id, request_id, *, charged_usd, usage, receipt):
        return self._settle_cost(run_id, request_id, "charged", charged=charged_usd,
                                 usage=usage, receipt=receipt)

    def release_cost(self, run_id, request_id, *, reason, receipt=None):
        return self._settle_cost(run_id, request_id, "released", reason=reason, receipt=receipt)

    def mark_cost_unknown(self, run_id, request_id, *, reason, receipt=None):
        return self._settle_cost(run_id, request_id, "unknown", reason=reason, receipt=receipt)

    @staticmethod
    def _memo_markdown_v4(result, label):
        """/4 memo fields (only results that carry pillars); /2-/3 text is unchanged."""
        horizon = result.get("horizon") or {}
        lines = [f"## {label('Convinzione e orizzonte', 'Conviction and horizon')}", "",
                 f"{label('Convinzione', 'Conviction')}: {result.get('conviction')} | "
                 f"{label('Orizzonte', 'Horizon')}: {horizon.get('months')} {label('mesi', 'months')} "
                 f"({horizon.get('label')})", "",
                 f"## {label('Pilastri della tesi', 'Investment pillars')}", ""]
        for item in result["pillars"]:
            lines.extend((f"### {item['title']}", "", item["thesis"], "",
                          f"{label('Evidenza', 'Evidence')}: {item['evidence']}", "",
                          f"{label('Rischio', 'Risk')}: {item['risk']}", ""))
        if result.get("variant_view"):
            lines.extend((f"## {label('Stime del comitato contro il consenso', 'Committee estimates versus consensus')}", ""))
            for item in result["variant_view"]:
                consensus = (item["consensus"] if item["consensus"] is not None
                             else label("consenso non disponibile", "consensus unavailable"))
                lines.extend((f"- {item['metric']} ({item['period']}, {item['unit']}): "
                              f"{label('consenso', 'consensus')} {consensus}; "
                              f"{label('stima del comitato', 'committee estimate')} {item['committee']}. "
                              f"{item['rationale']}", ""))
        if result.get("risk_exits"):
            lines.extend((f"## {label('Rischi, soglie e azioni', 'Risks, thresholds and actions')}", ""))
            actions = {"exit": label("uscire", "exit"), "reduce": label("ridurre", "reduce"),
                       "review": label("rivedere", "review")}
            for item in result["risk_exits"]:
                lines.extend((f"- {item['risk']} | {label('Soglia', 'Threshold')}: {item['threshold']} | "
                              f"{label('Azione', 'Action')}: {actions.get(item['action'], item['action'])}", ""))
        if result.get("review_triggers"):
            lines.extend((f"## {label('Trigger di revisione', 'Review triggers')}", ""))
            for item in result["review_triggers"]:
                when = item.get("date") or item.get("price_level") or item.get("condition")
                lines.extend((f"- {item['kind']}: {when} — {item['what']}", ""))
        return lines

    @staticmethod
    def _memo_markdown(row, result):
        """Deterministic research text; result_json retains the full structured record."""
        def label(italian, english):
            return italian if row["language"] == "it" else english

        lines = [f"# Trade Idea — {row['ticker']}", "",
                 f"Run: {row['id']}", f"{label('Accettata', 'Accepted')}: {row['created_at']}",
                 f"{label('Giudizio', 'Judgment')}: {result['judgment']}", "",
                 f"## {label('Conclusione', 'Conclusion')}", "",
                 result["summary"], "",
                 f"## {label('View PM e risposta del comitato', 'PM view and committee response')}", "",
                 result.get("pm_view") or label("Nessuna view PM fornita.", "No PM view supplied."), "",
                 result["pm_view_response"], ""]
        if result.get("pillars"):
            lines.extend(TradeIdeaStore._memo_markdown_v4(result, label))
        for section in result.get("dossier") or []:
            lines.extend((f"## {section['title']}", ""))
            for paragraph in section["paragraphs"]:
                lines.extend((paragraph, ""))
            for table in section.get("tables") or []:
                lines.extend((f"### {table['title']}", "",
                              f"{label('Fonte', 'Source')}: {table['source']} | "
                              f"{label('Unità', 'Unit')}: {table['unit']} | "
                              f"{label('Periodo', 'Period')}: {table['period']}",
                              "", "```json", _json({"columns": table["columns"],
                                                     "rows": table["rows"]}), "```", ""))
        for key, heading in (("pros", label("Evidenze a favore", "Supporting evidence")),
                             ("cons", label("Evidenze contrarie", "Contrary evidence")),
                             ("risks", label("Rischi", "Risks")),
                             ("catalysts", label("Catalizzatori", "Catalysts")),
                             ("invalidation", label("Invalidazione", "Invalidation")),
                             ("data_gaps", label("Dati mancanti", "Data gaps")),
                             ("review_conditions", label("Condizioni di rivalutazione", "Conditions to revisit"))):
            values = result.get(key) or []
            if values:
                lines.extend((f"## {heading}", ""))
                lines.extend(f"- {value}" for value in values)
                lines.append("")
        if result.get("scenarios"):
            lines.extend((f"## {label('Scenari', 'Scenarios')}", ""))
            names = {"bear": label("Pessimistico", "Bear"), "base": label("Base", "Base"),
                     "bull": label("Ottimistico", "Bull")}
            for item in result["scenarios"]:
                if "probability_pct" not in item:
                    lines.extend((f"### {item['name']}", "", item["analysis"], ""))
                    continue
                # /4: committee estimates, labelled as such.
                lines.extend((f"### {names.get(item['name'], item['name'])}", "",
                              f"{label('Probabilita stimata dal comitato', 'Committee probability estimate')}: "
                              f"{item['probability_pct']}% | {label('Prezzo obiettivo stimato', 'Estimated price target')}: "
                              f"{item['price_target']} {item['currency']} | {label('Metodo', 'Method')}: {item['method']}",
                              "", item["analysis"], ""))
                lines.extend(f"- {label('Driver', 'Driver')}: {value}" for value in item["drivers"])
                lines.extend(f"- {label('Falsificatore', 'Falsifier')}: {value}" for value in item["falsifiers"])
                lines.append("")
        if result.get("objections"):
            lines.extend((f"## {label('Obiezioni Red Team', 'Red Team objections')}", ""))
            for item in result["objections"]:
                lines.extend((f"- {label('Obiezione', 'Objection')}: {item['objection']}",
                              f"  {label('Risposta', 'Response')}: {item['response']}",
                              f"  {label('Risolta', 'Resolved')}: {item['resolved']}", ""))
        if result.get("history_review"):
            lines.extend((f"## {label('Revisione dello storico PM', 'Prior PM history review')}", ""))
            for item in result["history_review"]:
                lines.extend((f"- {item['kind']} #{item['id']}: {item['response']}", ""))
        if result.get("valuation_refs"):
            lines.extend((f"## {label('Modelli di valutazione esaminati', 'Reviewed valuation workbooks')}", ""))
            for item in result["valuation_refs"]:
                lines.extend((f"- Snapshot {item['snapshot_id']}, generation {item['generation_id']}, "
                              f"date {item['valuation_date']}: {item['interpretation']}", ""))
        if result.get("proposal"):
            lines.extend((f"## {label('Proposta condizionata', 'Conditional proposal')}", "", "```json",
                          _json(result["proposal"]), "```", ""))
        if result.get("evidence"):
            lines.extend((f"## {label('Fonti', 'Sources')}", ""))
            for item in result["evidence"]:
                lines.extend((f"- [{item['id']}] {item['source']} ({item['as_of']}): {item['summary']}"
                              + (f" {item['url']}" if item.get("url") else ""), ""))
        return "\n".join(lines).rstrip() + "\n"

    def _ensure_memo(self, conn, row, result, now):
        """Create one typed memo inside the caller's routing or manifest transaction."""
        if row["memo_id"] is not None:
            return row["memo_id"]
        memo = conn.execute("INSERT INTO memos(timestamp,title,full_markdown,notes,output_language) "
                            "VALUES(?,?,?,?,?)",
                            (now, f"Trade Idea — {row['ticker']} — {result['judgment']}",
                             self._memo_markdown(row, result), f"trade_idea:{row['id']}",
                             row["language"]))
        memo_id = memo.lastrowid
        conn.execute("UPDATE trade_idea_runs SET memo_id=?,updated_at=? WHERE id=?",
                     (memo_id, now, row["id"]))
        self._event(conn, row["id"], "memo_created", {"memo_id": memo_id}, now)
        return memo_id

    def _destination(self, row):
        return {"kind": row["destination_kind"], "decision_id": row["destination_decision_id"],
                "memo_id": row["memo_id"], "reason": row["destination_reason"]}

    @_scrittura_ritentata
    def route_result(self, run_id: str, checks: dict):
        """One primary destination, decided and inserted in one SQLite transaction.

        ``checks`` are receipts computed by the application, never LLM fields.
        Missing checks fail closed for operational promotion.
        """
        if not isinstance(checks, dict):
            raise ValueError("server checks required")
        now = self._at()
        mandate_error = None
        try:
            mandate_hash = self._mandate_loader()
        except Exception as exc:
            mandate_hash = None
            mandate_error = type(exc).__name__
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["destination_kind"] != "none":
                    destination = self._destination(row)
                    conn.execute("COMMIT")
                    return destination
                if row["technical_status"] not in _FINAL:
                    raise RunConflict("run has not finished")
                result = json.loads(row["result_json"]) if row["result_json"] else None
                if result is None:
                    conn.execute("UPDATE trade_idea_runs SET destination_reason=?,updated_at=? WHERE id=?",
                                 (row["reason"] or "No persistible structured result", now, run_id))
                    conn.execute("COMMIT")
                    return {"kind": "none", "decision_id": None,
                            "reason": row["reason"] or "No persistible structured result"}
                if result.get("run_id") != run_id or result.get("ticker") != row["ticker"]:
                    raise ValueError("persisted result identity mismatch")
                context = json.loads(row["context_json"])
                book, feedback = self._context(conn, row["ticker"], exclude_research_ids=[
                    item["destination_decision_id"] for item in self._ancestry(conn, run_id)[:-1]])
                current_context = _with_mandate_text_policy(
                    {"book": book, "feedback": feedback, "mandate_sha256": mandate_hash}, context)
                price_grant = _price_refresh_grant(json.loads(row["request_json"]), context)
                reasons = []
                proposal = result.get("proposal")
                operative = row["technical_status"] == "completed" and result.get("judgment") == "favorable" and isinstance(proposal, dict)
                if not operative:
                    reasons.append("Judgment or technical status is not an actionable favorable result")
                if operative:
                    required_checks = [('research_reviewed' if key == 'valuation_checked' else key)
                        for key in _OPERATIONAL_CHECKS] if is_research_mode(json.loads(row['request_json'])) else _OPERATIONAL_CHECKS
                    missing_checks = [key for key in required_checks if checks.get(key) is not True]
                    if missing_checks:
                        operative = False
                        reasons.append("Operational checks absent or failed: " + ", ".join(missing_checks))
                    if execution_policy(json.loads(row["request_json"])) == EXECUTION_POLICY_V4:
                        # /4: the Capo copies the server sizing band; the application
                        # receipt says whether amount and band match the engine.
                        band = checks.get("sizing_band")
                        if not isinstance(band, dict) or band.get("status") != "ok":
                            operative = False
                            reasons.append("Sizing /4: " + (str(band.get("reason"))[:500]
                                if isinstance(band, dict) and band.get("reason")
                                else "verifica della fascia del motore assente"))
                if mandate_hash is None or mandate_hash != context["mandate_sha256"]:
                    operative = False
                    reasons.append("PM mandate changed or unavailable" + (f" ({mandate_error})" if mandate_error else ""))
                book_matches = (_book_within_tolerance(context["book"], book) and feedback == context["feedback"])
                if price_grant is not None:
                    price_verified = _price_refresh_verified(checks.get("price_refresh_verification"),
                        run_id=run_id, accepted=context, current=current_context,
                        proposal=proposal, checks=checks)
                    try:
                        self._validate_price_refresh_units(conn)
                    except RunConflict as exc:
                        price_verified = False
                        reasons.append(str(exc))
                    book_matches = _same_non_price_context(context, current_context) and price_verified
                    if not price_verified:
                        operative = False
                        reasons.append("Authorized price refresh requires exact current portfolio, risk, stress, sizing, candidate and FX verification")
                if not book_matches:
                    operative = False
                    reasons.append("Book, price, trade or PM feedback changed since run acceptance")
                if book["non_eur_active_currencies"] and checks.get("fx_revalidated") is not True:
                    operative = False
                    reasons.append("FX rates/source/as-of not revalidated against the accepted book")
                if book["cash_state"] is None:
                    operative = False
                    reasons.append("Operational cash balance absent from SQLite")
                if feedback["active_veto_ids"]:
                    operative = False
                    reasons.append("Active PM veto on the selected ticker")
                prior_decisions, prior_trades = self._history_rows(
                    conn, row["ticker"], row["created_at"])
                required_history = ({("decision", item["id"]) for item in prior_decisions}
                                    | {("trade", item["id"]) for item in prior_trades})
                supplied = result.get("history_review") or []
                supplied_history = {(item.get("kind"), item.get("id")) for item in supplied}
                if (len(supplied_history) != len(supplied) or supplied_history != required_history
                        or any(not str(item.get("response") or "").strip() for item in supplied)):
                    operative = False
                    reasons.append("PM feedback/recent trade history was not answered by exact IDs")
                if row["stop_requested"]:
                    operative = False
                    reasons.append("Stop was requested")
                cost = self._cost_summary(conn, run_id, row["budget_limit_usd"])
                if cost["unknown_requests"] or Decimal(cost["reserved_usd"]) > 0:
                    operative = False
                    reasons.append("Provider cost is not fully reconciled")
                if cost["overrun"]:
                    operative = False
                    reasons.append("Measured provider cost overrun")
                action = None
                amount = None
                if proposal:
                    action = proposal.get("action")
                    amount = proposal.get("eur_amount")
                    held = bool(book["position"] and book["position"]["is_active"] == 1
                                and book["position"]["quantita"] > 0)
                    if action not in ("BUY", "ADD", "TRIM", "SELL"):
                        operative = False
                        reasons.append("Proposal action has no executable DCN path")
                    elif action == "BUY" and held:
                        action = "ADD"
                        reasons.append("BUY normalized to ADD for an existing position")
                    elif action == "ADD" and not held:
                        operative = False
                        reasons.append("ADD requires an existing position")
                    elif action in ("TRIM", "SELL") and not held:
                        operative = False
                        reasons.append("Sale or trim requires an existing position")
                    # /4: the source of the size is the server band itself; an "ok" band
                    # receipt replaces the free-text sizing_source of /2-/3.
                    band_source = (execution_policy(json.loads(row["request_json"])) == EXECUTION_POLICY_V4
                                   and isinstance(checks.get("sizing_band"), dict)
                                   and checks["sizing_band"].get("status") == "ok")
                    if (not isinstance(amount, (int, float)) or isinstance(amount, bool)
                            or not math.isfinite(amount) or amount <= 0
                            or not (proposal.get("sizing_source") or band_source)):
                        operative = False
                        reasons.append("Positive measured EUR size and sizing source required")
                    if proposal.get("ticker") != row["ticker"]:
                        operative = False
                        reasons.append("Proposal ticker differs from accepted ticker")
                kind = "dcn" if operative else "research"
                reason = "; ".join(reasons) if reasons else "Validated Trade Idea proposal"
                if kind == "research":
                    judgment = result["judgment"]
                    gaps = "; ".join(result.get("data_gaps") or [])
                    revisit = "; ".join(result.get("review_conditions") or [])
                    rationale = (f"Trade Idea {judgment}: {result['summary']}"
                                 + (f" Missing: {gaps}" if gaps else "")
                                 + (f" Revisit when: {revisit}" if revisit else "")
                                 + f" Routing: {reason}")
                    action, amount = "RESEARCH", None
                    timing = revisit[:2000] if revisit else None
                    confidence = "BASSA"
                else:
                    rationale = proposal["rationale"]
                    if action != proposal["action"]:
                        rationale += " [BUY normalized to ADD: existing position]"
                    timing = proposal["timing"]
                    confidence = proposal["confidence"]
                memo_id = self._ensure_memo(conn, row, result, now)
                cursor = conn.execute("""INSERT INTO decisions(
                    memo_id,timestamp,action,ticker,eur_amount,timing,confidence,rationale,status)
                    VALUES(?,?,?,?,?,?,?,?,'PENDING')""",
                    (memo_id, now, action, row["ticker"], amount, timing, confidence, rationale))
                decision_id = cursor.lastrowid
                conn.execute("UPDATE trade_idea_runs SET destination_kind=?,destination_decision_id=?,"
                             "destination_reason=?,updated_at=? WHERE id=?",
                             (kind, decision_id, reason, now, run_id))
                self._event(conn, run_id, "routed", {"kind": kind, "decision_id": decision_id,
                                                     "memo_id": memo_id, "reason": reason}, now)
                conn.execute("COMMIT")
                return {"kind": kind, "decision_id": decision_id,
                        "memo_id": memo_id, "reason": reason}
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def mark_routing_failure(self, run_id: str, reason: str):
        """A persisted result without a destination is technically incomplete."""
        reason, now = _text(reason, "routing failure", 4000), self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if (row["destination_kind"] != "none"
                        or row["technical_status"] not in ("completed", "incomplete")
                        or row["phase"] == "routing_failed"):
                    conn.execute("COMMIT")
                    return False
                combined = (row["reason"] + "; " + reason) if row["reason"] else reason
                conn.execute("UPDATE trade_idea_runs SET technical_status='incomplete',"
                             "phase='routing_failed',reason=?,updated_at=? WHERE id=?",
                             (combined, now, run_id))
                self._event(conn, run_id, "routing_failed", {"reason": reason}, now)
                conn.execute("COMMIT")
                return True
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def demote_unreviewed_destination(self, run_id: str, reason: str):
        """Withdraw only this run's untouched PENDING DCN after final report failure.

        PM feedback, vetoes, notes, archival choices and linked trades are never
        overwritten. Even when demotion is blocked, the run records a durable
        technical failure so the positive result cannot appear complete.
        """
        reason, now = _text(reason, "final report failure", 4000), self._at()
        blocked = None
        destination = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(conn, run_id)
                if row["destination_kind"] == "research" and row["phase"] == "report_incomplete":
                    destination = self._destination(row)
                    conn.execute("COMMIT")
                    return destination
                if row["destination_kind"] == "research":
                    if row["technical_status"] not in _FINAL:
                        raise RunConflict("research destination is not final")
                    combined = ((row["destination_reason"] + "; ")
                                if row["destination_reason"] else "") + reason
                    run_reason = ((row["reason"] + "; ") if row["reason"] else "") + reason
                    conn.execute("UPDATE trade_idea_runs SET technical_status='incomplete',"
                                 "phase='report_incomplete',destination_reason=?,reason=?,updated_at=? "
                                 "WHERE id=?", (combined, run_reason, now, run_id))
                    self._event(conn, run_id, "report_incomplete",
                                {"decision_id": row["destination_decision_id"],
                                 "reason": reason}, now)
                    destination = {"kind": "research", "decision_id": row["destination_decision_id"],
                                   "memo_id": row["memo_id"], "reason": combined}
                    conn.execute("COMMIT")
                    return destination
                if row["destination_kind"] == "dcn" and row["phase"] == "report_demote_blocked":
                    raise RunConflict(row["reason"] or "previous DCN demotion blocked by PM intervention")
                if (row["destination_kind"] != "dcn" or row["destination_decision_id"] is None
                        or row["memo_id"] is None
                        or row["technical_status"] not in ("completed", "incomplete")):
                    raise RunConflict("no completed Trade Idea DCN to demote")
                decision_id = row["destination_decision_id"]
                decision = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
                touched = []
                if (decision is None or decision["memo_id"] != row["memo_id"]
                        or decision["ticker"] != row["ticker"]):
                    touched.append("decision provenance")
                else:
                    if decision["status"] != "PENDING":
                        touched.append("status")
                    if decision["pm_feedback"] is not None:
                        touched.append("PM feedback")
                    if (decision["veto"] or decision["veto_reason"] is not None
                            or decision["veto_at"] is not None
                            or decision["veto_revoked_at"] is not None):
                        touched.append("PM veto")
                    if any(decision[key] is not None for key in
                           ("outcome_pct", "outcome_eur", "outcome_notes", "closed_at",
                            "archive_override")):
                        touched.append("PM outcome/archive")
                    if conn.execute("SELECT 1 FROM decision_notes WHERE decision_id=? LIMIT 1",
                                    (decision_id,)).fetchone():
                        touched.append("notes")
                    if conn.execute("SELECT 1 FROM pm_feedback WHERE decision_id=? LIMIT 1",
                                    (decision_id,)).fetchone():
                        touched.append("PM feedback rows")
                    if conn.execute("SELECT 1 FROM trade_history WHERE linked_decision_id=? LIMIT 1",
                                    (decision_id,)).fetchone():
                        touched.append("linked trade")
                if touched:
                    blocked = ("Final report failed; PM intervention prevents safe DCN demotion "
                               f"({', '.join(touched)}): {reason}")
                    conn.execute("UPDATE trade_idea_runs SET technical_status='incomplete',"
                                 "phase='report_demote_blocked',reason=?,destination_reason=?,updated_at=? "
                                 "WHERE id=?", (blocked, blocked, now, run_id))
                    self._event(conn, run_id, "report_demote_blocked",
                                {"decision_id": decision_id, "reason": blocked}, now)
                else:
                    rationale = (f"Trade Idea {json.loads(row['result_json'])['judgment']}; "
                                 f"operational proposal withdrawn because final report failed: {reason}. "
                                 f"Original proposal (superseded): {decision['rationale']}")
                    updated = conn.execute("UPDATE decisions SET action='RESEARCH',eur_amount=NULL,"
                                           "timing=NULL,confidence='BASSA',rationale=? "
                                           "WHERE id=? AND memo_id=? AND status='PENDING'",
                                           (rationale, decision_id, row["memo_id"]))
                    if updated.rowcount != 1:
                        raise RunConflict("Trade Idea decision changed during report demotion")
                    conn.execute("UPDATE trade_idea_runs SET technical_status='incomplete',"
                                 "phase='report_incomplete',destination_kind='research',"
                                 "destination_reason=?,reason=?,updated_at=? WHERE id=?",
                                 (reason, reason, now, run_id))
                    self._event(conn, run_id, "report_demoted",
                                {"decision_id": decision_id, "memo_id": row["memo_id"],
                                 "reason": reason}, now)
                    destination = {"kind": "research", "decision_id": decision_id,
                                   "memo_id": row["memo_id"], "reason": reason}
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if blocked:
            raise RunConflict(blocked)
        return destination

    def lookup_decision(self, decision_id: int):
        """Read-only Decisioni decoration; never changes feedback or expiry."""
        return self.lookup_decisions([decision_id]).get(decision_id)

    def lookup_decisions(self, decision_ids):
        """Batch provenance for the existing Decisions page; no history mutation."""
        ids = list(decision_ids)
        if any(type(value) is not int or value < 1 for value in ids) or len(ids) > 1000:
            raise ValueError("decision IDs invalid or batch too large")
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect(read_only=True) as conn:
            rows = conn.execute("SELECT id,ticker,memo_id,technical_status,destination_kind,"
                                "destination_decision_id,destination_reason "
                                f"FROM trade_idea_runs WHERE destination_decision_id IN ({placeholders})",
                                ids).fetchall()
            output = {}
            for row in rows:
                delivery = conn.execute("SELECT manifest_json,manifest_sha256 "
                                        "FROM trade_idea_delivery WHERE run_id=?",
                                        (row["id"],)).fetchone()
                output[row["destination_decision_id"]] = {
                    "origin": "trade_idea", "run_id": row["id"],
                    "destination_kind": row["destination_kind"], "ticker": row["ticker"],
                    "memo_id": row["memo_id"],
                    "technical_status": row["technical_status"],
                    "destination_reason": row["destination_reason"],
                    "artifacts_ready": bool(delivery and trade_idea_pdf_ready(
                        delivery["manifest_json"], delivery["manifest_sha256"],
                        run_id=row["id"], ticker=row["ticker"]))}
        return output

    @_scrittura_ritentata
    def save_manifest(self, run_id: str, manifest: dict):
        if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
            raise ValueError("manifest must identify this run")
        from bellomberg.reporting.trade_idea_delivery import verify_trade_idea_manifest, assess_trade_idea_completion
        verify_trade_idea_manifest(manifest)
        completion = assess_trade_idea_completion(manifest)
        email_status = 'ready' if completion['status'] == 'ready' else 'blocked'
        email_error = None if email_status == 'ready' else 'Final memo not deliverable: ' + '; '.join(completion['reasons'])
        encoded, digest, now = _json(manifest), _digest(manifest), self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                run = self._row(conn, run_id)
                if run["result_json"] is None or manifest.get("ticker") != run["ticker"]:
                    raise ValueError("manifest has no completed research result or ticker differs")
                if manifest.get("language") != run["language"]:
                    raise ValueError("manifest language differs from accepted run")
                result = json.loads(run["result_json"])
                if manifest.get("judgment") != result["judgment"]:
                    raise ValueError("manifest judgment differs from persisted result")
                result_digest = hashlib.sha256(json.dumps(
                    result, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
                if manifest.get("result_sha256") != result_digest:
                    raise ValueError("manifest result hash differs from persisted result")
                if (run["destination_kind"] not in ("dcn", "research")
                        or manifest.get("destination") != self._destination(run)):
                    raise ValueError("manifest destination differs from persisted routing")
                if run["technical_status"] == "completed":
                    verify_trade_idea_manifest(manifest, require_complete=True)
                delivery = conn.execute("SELECT * FROM trade_idea_delivery WHERE run_id=?",
                                        (run_id,)).fetchone()
                if delivery["manifest_sha256"]:
                    if delivery["manifest_sha256"] != digest:
                        raise RunConflict("delivery manifest immutable: artifacts changed")
                    conn.execute("COMMIT")
                    return digest
                pdfs = [item for item in manifest["artifacts"] if item.get("kind") == "pdf"]
                if len(pdfs) != 1:
                    raise ValueError("Trade Idea manifest must contain exactly one PDF")
                pdf_path = pdfs[0]["path"]
                workbooks = [item["path"] for item in manifest["artifacts"]
                             if item.get("kind") == "xlsx" and item.get("status") == "ready"]
                memo_id = self._ensure_memo(conn, run, result, now)
                conn.execute("UPDATE memos SET pdf_path=?,dcf_files=? WHERE id=?",
                             (pdf_path, _json(workbooks), memo_id))
                conn.execute("UPDATE trade_idea_delivery SET manifest_json=?,manifest_sha256=?,"
                             "email_status=?,error=?,updated_at=? WHERE run_id=?",
                             (encoded, digest, email_status, email_error, now, run_id))
                self._event(conn, run_id, "manifest_ready", {"sha256": digest,
                                                               "memo_id": memo_id}, now)
                conn.execute("COMMIT")
                return digest
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def claim_email(self, run_id: str, *, retry=False, acknowledge_uncertain=False):
        """CAS delivery claim. An ambiguous SMTP attempt never retries blindly."""
        attempt_id, now = str(uuid.uuid4()), self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                delivery = conn.execute("SELECT * FROM trade_idea_delivery WHERE run_id=?",
                                        (run_id,)).fetchone()
                if delivery is None or delivery["manifest_json"] is None:
                    raise RunConflict("validated manifest absent")
                run = self._row(conn, run_id)
                manifest = json.loads(delivery["manifest_json"])
                if _digest(manifest) != delivery["manifest_sha256"]:
                    raise RunConflict("Stored delivery manifest checksum differs")
                if run["phase"] == "review_required":
                    raise RunConflict("refreshed research requires explicit new committee review")
                from bellomberg.reporting.trade_idea_delivery import verify_trade_idea_manifest
                verify_trade_idea_manifest(manifest, require_complete=True)
                if manifest.get("destination") != self._destination(run):
                    raise RunConflict("email destination differs from current routing")
                status = delivery["email_status"]
                if not (status == "ready" or (status == "failed" and retry)
                        or (status == "uncertain" and retry and acknowledge_uncertain)):
                    raise RunConflict("email not claimable without explicit recovery")
                conn.execute("UPDATE trade_idea_delivery SET email_status='sending',"
                             "email_attempt_id=?,email_attempts=email_attempts+1,error=NULL,updated_at=? "
                             "WHERE run_id=?", (attempt_id, now, run_id))
                self._event(conn, run_id, "email_claimed", {"attempt_id": attempt_id}, now)
                conn.execute("COMMIT")
                return attempt_id
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def block_delivery(self, run_id: str, *, reason: str):
        """Record a package failure before dispatch without altering the verdict."""
        reason, now = _text(reason, "delivery reason", 4000), self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT email_status FROM trade_idea_delivery WHERE run_id=?",
                                   (run_id,)).fetchone()
                if row is None:
                    raise KeyError("delivery row absent")
                if row["email_status"] in ("sending", "accepted", "uncertain"):
                    raise RunConflict("cannot replace an SMTP outcome with package failure")
                conn.execute("UPDATE trade_idea_delivery SET email_status='blocked',error=?,updated_at=? "
                             "WHERE run_id=?", (reason, now, run_id))
                self._event(conn, run_id, "email_blocked", {"reason": reason}, now)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def record_email_outcome(self, run_id, attempt_id, status, *, receipt=None, error=None):
        if status not in ("accepted", "failed", "uncertain", "blocked"):
            raise ValueError("invalid email outcome")
        if status == "accepted" and not isinstance(receipt, dict):
            raise ValueError("SMTP acceptance receipt required")
        if status != "accepted" and not error:
            raise ValueError("delivery failure reason required")
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM trade_idea_delivery WHERE run_id=?",
                                   (run_id,)).fetchone()
                if row is None or row["email_status"] != "sending" or row["email_attempt_id"] != attempt_id:
                    raise RunConflict("email attempt is no longer current")
                conn.execute("UPDATE trade_idea_delivery SET email_status=?,smtp_receipt_json=?,"
                             "error=?,updated_at=?,accepted_at=? WHERE run_id=?",
                             (status, _json(receipt) if receipt is not None else None,
                              error, now, now if status == "accepted" else None, run_id))
                self._event(conn, run_id, "email_" + status,
                            {"attempt_id": attempt_id, "error": error}, now)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @_scrittura_ritentata
    def recover_sending(self, run_id: str):
        """A restart after SMTP dispatch is ambiguous, not an automatic retry."""
        now = self._at()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT email_status FROM trade_idea_delivery WHERE run_id=?",
                                   (run_id,)).fetchone()
                if row is None or row["email_status"] != "sending":
                    conn.execute("COMMIT")
                    return False
                conn.execute("UPDATE trade_idea_delivery SET email_status='uncertain',"
                             "error='SMTP outcome unknown after restart',updated_at=? WHERE run_id=?",
                             (now, run_id))
                self._event(conn, run_id, "email_uncertain", {"reason": "restart"}, now)
                conn.execute("COMMIT")
                return True
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _public_run(row):
        return {**_with_mandate_text_policy({}, json.loads(row["context_json"])),
                 "id": row["id"], "ticker": row["ticker"],
                 "company_name": row["company_name"], "exchange": row["exchange"],
                 "currency": row["currency"], "language": row["language"],
                 "view_text": row["view_text"],
                "view_origin": row["view_origin"], "models": json.loads(row["models_json"]),
                "catalog_snapshot": json.loads(row["catalog_snapshot_json"]),
                "budget_limit_usd": row["budget_limit_usd"],
                "authorization": json.loads(row["request_json"]).get("authorization"),
                "analysis_mode": json.loads(row['request_json']).get('analysis_mode'),
                "execution_policy": json.loads(row['request_json']).get('execution_policy'),
                "continuation": json.loads(row["request_json"]).get("continuation"),
                "source_qualification": json.loads(row["request_json"]).get("source_qualification"),
                "source_qualification_execution": json.loads(row["request_json"]).get("source_qualification_execution"),
                "peers": json.loads(row["request_json"]).get("peers"),
                "technical_status": row["technical_status"], "phase": row["phase"],
                "reason": row["reason"], "stop_requested": bool(row["stop_requested"]),
                "created_at": row["created_at"], "started_at": row["started_at"],
                 "updated_at": row["updated_at"], "finished_at": row["finished_at"],
                 "memo_id": row["memo_id"],
                 "destination": {"kind": row["destination_kind"],
                                 "decision_id": row["destination_decision_id"],
                                 "memo_id": row["memo_id"],
                                 "reason": row["destination_reason"]}}

    def get_accepted_request(self, run_id: str):
        """Read and verify the complete immutable request before source reuse."""
        with self._connect(read_only=True) as conn:
            row = self._row(conn, run_id)
            request = json.loads(row["request_json"])
            if not isinstance(request, dict) or _digest(request) != row["request_sha256"]:
                raise ValueError("Accepted Trade Idea request integrity check failed")
            for field in ("ticker", "company_name", "exchange", "currency", "language",
                          "view_text", "view_origin", "budget_limit_usd"):
                default = "" if field == "view_text" else "manual" if field == "view_origin" else None
                if request.get(field, default) != row[field]:
                    raise ValueError("Accepted Trade Idea request projection differs: " + field)
            for field, column in (("models", "models_json"), ("catalog_snapshot", "catalog_snapshot_json")):
                if request.get(field) != json.loads(row[column]):
                    raise ValueError("Accepted Trade Idea request projection differs: " + field)
            return request

    def get_run(self, run_id: str):
        with self._connect(read_only=True) as conn:
            row = self._row(conn, run_id)
            delivery = conn.execute("SELECT * FROM trade_idea_delivery WHERE run_id=?",
                                    (run_id,)).fetchone()
            manifest = json.loads(delivery["manifest_json"]) if delivery and delivery["manifest_json"] else None
            try:
                manifest_integrity = manifest is None or _digest(manifest) == delivery["manifest_sha256"]
            except ValueError:
                manifest_integrity = False
            progress = json.loads(row["progress_json"])
            result = json.loads(row["result_json"]) if row["result_json"] else None
            cost = self._cost_summary(conn, run_id, row["budget_limit_usd"])
            child = conn.execute("SELECT id FROM trade_idea_runs WHERE json_extract(request_json,'$.continuation.parent_run_id')=?",
                                 (run_id,)).fetchone()
            checkpoint = progress.get("checkpoint")
            checkpoint_valid = (isinstance(checkpoint, dict) and checkpoint.get("version") == 1
                                and progress.get("checkpoint_sha256") == _digest(checkpoint))
            unresolved = bool(cost["unacknowledged_unknown_requests"] or Decimal(cost["reserved_usd"]))
            resume_block = ("provider_cost_unresolved" if unresolved else "original_budget_exhausted" if
                cost["overrun"] or Decimal(cost["remaining_after_holds_usd"]) <= 0 else
                "native_checkpoint_unavailable" if not checkpoint_valid else
                _checkpoint_resume_block(checkpoint) if _checkpoint_resume_block(checkpoint) else
                "run_not_stopped_incomplete" if row["technical_status"] not in _FINAL or row["technical_status"] == "completed" else None)
            if resume_block == "model_authoring_review_required" and self._can_compile_saved_model(conn, row, progress):
                resume_block = None
            truncated_recovery = None
            failed_recovery = None
            if resume_block == "incomplete_response_review_required":
                try:
                    truncated_recovery = self._truncated_response_recovery(conn, row, progress)
                except (RunConflict, ValueError, KeyError, TypeError, InvalidOperation):
                    # The ordinary continuation remains blocked. A candidate
                    # is exposed only when its paid provenance is verifiable.
                    pass
                try:
                    failed_recovery = self._failed_model_response_recovery(conn, row, progress)
                except (RunConflict, ValueError, KeyError, TypeError, InvalidOperation):
                    pass
            price_refresh_required = False
            can_continue_with_price_refresh = False
            context_block = None
            try:
                accepted_context = json.loads(row["context_json"])
                current_context = self._current_run_context(conn, row)
                price_grant = _price_refresh_grant(json.loads(row["request_json"]), accepted_context)
                if not _same_non_price_context(accepted_context, current_context):
                    context_block = "accepted_context_changed"
                    truncated_recovery = None
                    failed_recovery = None
                elif current_context != accepted_context or price_grant is not None:
                    self._validate_price_refresh_units(conn)
                    if price_grant is None:
                        price_refresh_required = True
                        can_continue_with_price_refresh = child is None and (
                            resume_block is None or truncated_recovery is not None or failed_recovery is not None)
                        context_block = "price_refresh_authorization_required"
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                context_block = "context_revalidation_unavailable"
                truncated_recovery = None
                failed_recovery = None
            if resume_block is None:
                resume_block = context_block
            analytical_complete = bool(result and result.get("judgment") != "incomplete"
                and not progress.get("primary_failure") and (row["technical_status"] == "completed"
                    or isinstance(checkpoint, dict) and checkpoint.get("data", {}).get("_capo_completed")))
            artifact_availability = {"status": "pending", "reason": None}
            if manifest:
                from bellomberg.reporting.trade_idea_delivery import verify_trade_idea_manifest
                try:
                    if not manifest_integrity:
                        raise ValueError("Stored delivery manifest checksum differs")
                    verify_trade_idea_manifest(manifest)
                    artifact_availability["status"] = ("ready" if
                        manifest.get("complete_package_status") == "ready" else "partial")
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    artifact_availability = {"status": "unavailable", "error_type": type(exc).__name__,
                        "reason": (str(exc) if isinstance(exc, ValueError) else
                                   "An attested artifact is missing, unreadable or the manifest is malformed")}
            return {"run": self._public_run(row),
                    "progress": progress,
                    "result": result,
                    "states": {"analysis": "complete" if analytical_complete else "incomplete",
                        "artifacts": artifact_availability["status"],
                        "delivery": delivery["email_status"] if delivery else "not_attempted",
                        "operational": "pending_pm" if row["destination_kind"] == "dcn" else "research"},
                    "recovery": {"successor_run_id": child["id"] if child else None,
                        "checkpoint_available": checkpoint_valid,
                        "can_continue": resume_block is None and child is None,
                        "price_refresh_required": price_refresh_required,
                        "can_continue_with_price_refresh": can_continue_with_price_refresh,
                        "requires_explicit_authorization": True, "blocked_reason": resume_block,
                        "truncated_response_recovery": truncated_recovery,
                        "can_complete_truncated_response": truncated_recovery is not None and child is None,
                        "requires_truncated_response_authorization": True,
                        "failed_response_recovery": failed_recovery,
                        "can_retry_failed_response": failed_recovery is not None and child is None,
                        "requires_failed_response_authorization": True,
                        "can_recover_delivery": manifest_integrity and result is not None and row["technical_status"] in _FINAL
                            and row["phase"] != "review_required",
                        "remaining_work": progress.get("remaining_work"),
                        "source_revalidation_required": True},
                    "artifacts": manifest,
                    "manifest_integrity": manifest_integrity,
                    "artifact_availability": artifact_availability,
                    "email": {"status": delivery["email_status"],
                              "attempts": delivery["email_attempts"],
                              "error": delivery["error"],
                              "accepted_at": delivery["accepted_at"],
                              "receipt": json.loads(delivery["smtp_receipt_json"])
                              if delivery["smtp_receipt_json"] else None}
                    if delivery else {"status": "not_attempted"},
                    "cost": cost}

    def list_runs(self, *, ticker=None, status=None, limit=50, offset=0):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("limit must be 1..200")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be nonnegative")
        where, values = [], []
        if ticker:
            ticker = _text(ticker, "ticker", 40).strip().upper()
            if not _TICKER.fullmatch(ticker):
                raise ValueError("ticker invalid")
            where.append("ticker=?")
            values.append(ticker)
        if status:
            if status not in {"accepted", "running", *_FINAL}:
                raise ValueError("status invalid")
            where.append("technical_status=?")
            values.append(status)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self._connect(read_only=True) as conn:
            total = conn.execute("SELECT COUNT(*) FROM trade_idea_runs" + clause, values).fetchone()[0]
            rows = conn.execute("SELECT * FROM trade_idea_runs" + clause +
                                " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                                [*values, limit, offset]).fetchall()
            return {"runs": [self._public_run(row) for row in rows], "total": total}
