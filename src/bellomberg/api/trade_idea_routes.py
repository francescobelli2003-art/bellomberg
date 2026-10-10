"""Authenticated Trade Idea API. No endpoint performs a paid LLM call inline."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
from threading import Lock
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from bellomberg.agents.trade_idea import (
    normalize_ticker, normalize_budget, paid_run_is_active, preflight_trade_idea,
    source_qualification_summary)
from bellomberg.agents.trade_idea_sources import PMDocumentSource, normalize_sources
from bellomberg.core.language import capture_language
from bellomberg.core.trade_idea_policy import CURRENT_EXECUTION_POLICY
from bellomberg.core.paths import DATA_DIR, MODELS_DIR, REPORT_DIR
from bellomberg.storage.memory_db import SQLITE_PATH
from bellomberg.storage.trade_idea_store import (
    TradeIdeaStore, IdempotencyConflict, RunConflict, BudgetBlocked, StorageNotReady)


class PreflightBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticker: str
    pm_view: str = ""
    view_source: str = "manual"
    budget_limit_usd: str | float | int | None = None
    document_sources: list[PMDocumentSource] = Field(default_factory=list, max_length=4)
    # Lotto 3 (L1, Opus 5.5): peer scelti dal PM per il pacchetto di mercato; vuota = selettore
    peers: list[str] = Field(default_factory=list, max_length=8)


def _body_peers(body):
    """Peer del PM normalizzati (maiuscoli, senza doppioni ne' candidato); ValueError se invalidi."""
    from bellomberg.market_data.trade_idea_market_pack import normalize_request_peers
    return normalize_request_peers(list(body.peers or []), ticker=normalize_ticker(body.ticker))


class StartBody(PreflightBody):
    idempotency_key: str = Field(min_length=1, max_length=128)
    cost_acknowledged: bool
    authorization: "RunAuthorization"


class CostReconcileBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    apply: bool = False


class EmailRetryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    acknowledge_uncertain: bool = False


class ResumeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idempotency_key: str = Field(min_length=1, max_length=128)
    cost_acknowledged: bool = False
    recover_truncated_request_id: str | None = Field(default=None, min_length=1, max_length=128)
    recover_failed_request_id: str | None = Field(default=None, min_length=1, max_length=128)
    authorize_price_refresh: bool = False
    budget_limit_usd: str | float | int | None = None
    resume_with_held_rejection_request_id: str | None = Field(default=None, min_length=1, max_length=128)
    capo_finalization_request_id: str | None = Field(default=None, min_length=1, max_length=128)


from bellomberg.core.trade_idea_contract import RunAuthorization
StartBody.model_rebuild()


_PROCS: dict[str, subprocess.Popen] = {}


class _PreflightSnapshots:
    """Private, bounded, immutable handoff; this cache grants no paid activity."""

    def __init__(self):
        self._entries = {}
        self._lock = Lock()
        self._max_bytes = 64 * 1024 * 1024
        self._max_entries = 16

    def _prune(self):
        today = datetime.now(timezone.utc).date().isoformat()
        for key, (_, _, cutoff) in list(self._entries.items()):
            if cutoff != today:
                del self._entries[key]

    @staticmethod
    def _decode(entry):
        encoded, digest, _ = entry
        if sha256(encoded).hexdigest() != digest:
            raise ValueError("Snapshot del preflight alterato; rifare Verifica fonti")
        return json.loads(encoded)

    def remember(self, key, checked):
        qualified = checked.get('_source_qualification')
        if not isinstance(qualified, dict):
            raise ValueError("Snapshot completo del preflight assente; rifare Verifica fonti")
        cutoff = qualified.get('as_of')
        if cutoff != datetime.now(timezone.utc).date().isoformat():
            raise ValueError("Snapshot del preflight scaduto; rifare Verifica fonti")
        with self._lock:
            self._prune()
            # The fingerprint omits retrieval clocks/paths. Keep the exact first
            # snapshot rather than replace it silently with an equivalent fetch.
            checked = deepcopy(checked)
            if key in self._entries:
                original = self._decode(self._entries[key])
                for name in ('_source_qualification', 'identity', 'preparation', 'valuation'):
                    checked[name] = original[name]
                summary = source_qualification_summary(checked['_source_qualification'],
                    execution_status=checked.get('source_qualification', {}).get('execution_status'))
                checked['source_qualification'] = summary
                checked['document_sources'] = summary['document_sources']
            # An explicit new preflight may refresh admission/catalog metadata,
            # while the source snapshot handed off remains byte-for-byte exact.
            encoded = json.dumps(checked, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False).encode('utf-8')
            if len(encoded) > self._max_bytes:
                raise ValueError("Snapshot del preflight oltre il limite di memoria")
            self._entries.pop(key, None)
            while (len(self._entries) >= self._max_entries or
                   sum(len(entry[0]) for entry in self._entries.values()) + len(encoded) > self._max_bytes):
                del self._entries[next(iter(self._entries))]
            self._entries[key] = (encoded, sha256(encoded).hexdigest(), cutoff)
            return self._decode(self._entries[key])

    def get(self, key):
        with self._lock:
            self._prune()
            entry = self._entries.get(key)
            if entry is None:
                raise ValueError("Snapshot del preflight assente o scaduto; rifare Verifica fonti")
            return self._decode(entry)

    def consume(self, key):
        with self._lock:
            self._entries.pop(key, None)


def _preflight_binding(body, session, *, db_path, language):
    if not isinstance(session, str) or not session:
        raise ValueError("Sessione del preflight non verificabile")
    bound = {'db_path': str(Path(db_path).resolve()),
        'session_sha256': sha256(session.encode('utf-8')).hexdigest(),
        'ticker': normalize_ticker(body.ticker), 'view_text': body.pm_view,
        'view_origin': body.view_source, 'language': language,
        'budget': str(Decimal(normalize_budget(body.budget_limit_usd)).normalize()),
        'documents': normalize_sources(body.document_sources)}
    peers = _body_peers(body)
    if peers:  # solo se presenti: i binding senza peer restano quelli di prima
        bound['peers'] = peers
    return sha256(json.dumps(bound, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _save_admission_source_receipt(db_path, receipt):
    """Private structural hashes only; preserve a counterproof even before a run."""
    directory = Path(db_path).resolve().parent / 'trade_idea_preflights' / 'diagnostics'
    path = directory / (uuid4().hex + '.json')
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                             delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError('Ricevuta di controverifica fonti non salvabile; avvio bloccato') from exc


def _run_absent(exc):
    """Vero solo per il KeyError con cui lo store dichiara la run assente (TradeIdeaStore._row)."""
    return (type(exc) is KeyError and bool(exc.args) and isinstance(exc.args[0], str)
            and exc.args[0].startswith("Trade Idea run absent: "))


def _keyerror_http(exc, operation):
    """KeyError dello store -> 404 SOLO per la run assente; ogni altra chiave mancante e' un
    record incoerente dichiarato (500), mai «non trovata». Solo il tipo, mai il testo."""
    if _run_absent(exc):
        return HTTPException(404, "Run Trade Idea non trovata")
    return HTTPException(500, "Record Trade Idea incoerente (" + type(exc).__name__
                         + "): operazione di " + operation + " bloccata")


class StorageUnavailable(HTTPException):
    def __init__(self, error):
        super().__init__(503, "Trade Idea storage non pronto: " + str(error))
        self.storage = error.storage


async def _storage_unavailable_response(_request, error):
    return JSONResponse(status_code=error.status_code, content={
        "detail": error.detail, "error_code": error.storage["error_code"],
        "storage": error.storage})


def _store(db_path=SQLITE_PATH):
    try:
        return TradeIdeaStore(db_path)
    except StorageNotReady as exc:
        raise StorageUnavailable(exc) from exc
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(503, "Trade Idea storage non pronto: " + str(exc)) from exc


def _active_id(store):
    for status in ("running", "accepted"):
        rows = store.list_runs(status=status, limit=1)["runs"]
        if rows:
            return rows[0]["id"]
    return None


def start_refusal_code(reasons):
    """409 SOLO per un conflitto con un'altra run (pagata o Trade Idea attiva); ogni altro
    preflight fallito e' una precondizione (428). R-MOD punto 4 (06/10): prima bastava la parola
    «Trade Idea» nel testo, e un modello del .env inadatto usciva 409 come una run in corso."""
    conflicts = ("un'altra run pagata e' attiva", "Trade Idea gia' accettata o in esecuzione")
    return 409 if any(any(item in str(reason) for item in conflicts) for reason in reasons) else 428


def active_paid_reason(store=None, *, weekly_active=False):
    """Read-only admission check shared with weekly start; OS lock covers CLI too."""
    if weekly_active or paid_run_is_active():
        return "un'altra run pagata e' attiva"
    if store is not None and _active_id(store):
        return "Trade Idea gia' accettata o in esecuzione"
    return None


def _public_run(run, cost=None):
    models = run.get("models") or {}
    model_list = ([{"role": role, **spec} for role, spec in models.items()]
                  if isinstance(models, dict) else models)
    output = {k: v for k, v in run.items()
              if k not in ("catalog_snapshot", "source_qualification_execution")}
    from bellomberg.agents.trade_idea import source_qualification_summary
    # 09/10 (B6, Opus 5.5): execution_status anche sulle run salvate; marcatore assente
    # (run accettate prima del 09/10) = "unknown_legacy" DICHIARATO, mai "completed" inventato.
    output["source_qualification"] = source_qualification_summary(
        run.get("source_qualification") or {},
        execution_status=run.get("source_qualification_execution") or "unknown_legacy")
    output["models"] = model_list
    output["identity"] = {"ticker": run["ticker"], "name": run.get("company_name"),
        "exchange": run.get("exchange"), "currency": run.get("currency"),
        "status": "confirmed" if run.get("company_name") and run.get("exchange") else "unverified"}
    output["error"] = run.get("reason")
    if cost is not None:
        unknown = bool(cost.get("unknown_requests") or Decimal(cost.get("reserved_usd") or "0") > 0)
        overrun = bool(cost.get("overrun"))
        output["usage"] = {"cost_usd": float(cost["charged_usd"]), "partial": unknown,
                           "status": "unknown" if unknown else "overrun" if overrun else "measured",
                           "unknown_requests": cost.get("unknown_requests", 0),
                           "reason": ("costo di una o piu' chiamate non riconciliato" if unknown else
                                      "costo provider oltre la prenotazione o il limite della run" if overrun else None)}
    return output


def _public_detail(detail, *, run_id):
    run = detail["run"]
    raw_progress = detail.get("progress") or {}
    progress = {key: raw_progress.get(key) for key in
                ("phase", "updated_at", "specialists", "tools", "events", "reports",
                 "source_qualification", "model_review", "revision_requests", "objections", "objection_history",
                 "decisive_questions", "round_dependencies", "review_history", "analysis_mode", "research_review")}
    red = raw_progress.get("red_team")
    if red:
        from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
        text = red.get("report") or ""
        reason = motivo_critica_non_utilizzabile(text)
        if "CRITICA TRONCATA" in text:
            reason = "critica troncata"
        progress["red_team"] = {"status": "incomplete" if reason else "ready",
                                "text": text, "reason": reason}
    result = detail.get("result")
    if result is not None:
        result = dict(result)
        result["destination"] = run.get("destination")
        result["reports"] = progress.get("reports") or []
        if progress.get("red_team"):
            result["red_team"] = progress["red_team"]
        valuation = (raw_progress.get("valuation_results") or {}).get(run["ticker"]) or {}
        from bellomberg.valuation.trade_idea_model import candidate_model_usability
        usability = candidate_model_usability(valuation)
        result["valuation"] = {"status": "usable" if usability["usable"] else "incomplete",
            "kind": usability["kind"], "objective": usability.get("objective"),
            "intrinsic_value_applicable": usability.get("intrinsic_value_applicable"),
            "method": (valuation.get("valuation_decision") or {}).get("method_id"),
            "summary": valuation.get("valuation_basis"), "reason": valuation.get("error")}
        from bellomberg.core.research_analysis import is_research_mode
        if is_research_mode(run):
            result['valuation'] = {'status': 'not_required', 'kind': 'company_research',
                'objective': 'Independent analysis with analyst consensus as a reference',
                'intrinsic_value_applicable': None, 'method': None,
                'summary': None, 'reason': 'Workbook and mandatory AI fair value are not part of this analysis'}
    manifest = (detail.get("artifacts") or {}) if detail.get("manifest_integrity") is not False else {}
    artifacts = []
    for item in manifest.get("artifacts") or []:
        artifact = {key: item.get(key) for key in ("id", "kind", "name", "status", "reason", "sha256")}
        artifact["download_url"] = f"/trade-ideas/runs/{run_id}/artifacts/{item['id']}"
        artifacts.append(artifact)
    email = detail.get("email") or {"status": "not_attempted"}
    from bellomberg.reporting.trade_idea_delivery import assess_trade_idea_completion
    completion = (assess_trade_idea_completion(manifest) if manifest else
                  {"status": "pending", "reasons": []})
    availability = detail.get('artifact_availability') or {}
    if availability.get('status') == 'unavailable':
        completion = {'status': 'incomplete', 'reasons': [*completion['reasons'], availability['reason']]}
    return {"run": _public_run(run, detail.get("cost")), "progress": progress,
            "result": result, "artifacts": artifacts,
            "completion": {**completion, "model_status": manifest.get("model_status"),
                           "pdf_status": (manifest.get("pdf_quality") or {}).get("status")},
            "email": {key: email.get(key) for key in ("status", "attempts", "error", "accepted_at")},
            "cost": detail.get("cost"), "states": detail.get("states"),
            "artifact_availability": availability,
            "recovery": detail.get("recovery"),
            "primary_failure": raw_progress.get("primary_failure")}


def _matching_retry(body, detail):
    run = detail["run"]
    try:
        return (run["ticker"] == normalize_ticker(body.ticker)
                and run["view_text"] == body.pm_view
                and run["view_origin"] == body.view_source
                and run["language"] == capture_language()
                and Decimal(run["budget_limit_usd"]) == Decimal(normalize_budget(body.budget_limit_usd))
                and run.get("authorization") == body.authorization.model_dump()
                and normalize_sources(body.document_sources) == normalize_sources([
                    row["request"] for row in ((run.get("source_qualification") or {})
                        .get("document_receipt") or {}).get("documents", [])])
                and (run.get("peers") or []) == _body_peers(body)
                and body.cost_acknowledged is True)
    except (ValueError, KeyError):
        return False


def _spawn_worker(run_id, store, *, db_path=SQLITE_PATH):
    directory = DATA_DIR / "trade_ideas" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "worker.log"
    try:
        with open(log_path, "a", encoding="utf-8", buffering=1) as log:
            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "bellomberg.agents.trade_idea",
                 "--run-id", run_id, "--db-path", str(db_path)],
                stdout=log, stderr=subprocess.STDOUT, text=True,
                cwd=str(Path(__file__).resolve().parents[3]),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        _PROCS[run_id] = proc
        (directory / "worker.pid").write_text(str(proc.pid), encoding="ascii")
    except Exception as exc:
        token = store.claim_run(run_id)
        store.finish_run(run_id, token, None, "failed",
                         reason="Worker non avviato: " + type(exc).__name__ + ": " + str(exc)[:3000])
        raise HTTPException(503, "Worker Trade Idea non avviato: " + str(exc)) from exc


def _worker_state(run_id):
    """Return (process, alive); None liveness means process death is unproven."""
    proc = _PROCS.get(run_id)
    if proc is not None:
        if proc.poll() is None:
            return proc, True
        _PROCS.pop(run_id, None)
        return None, False
    try:
        import psutil
    except ImportError:
        return None, None
    pid_path = DATA_DIR / "trade_ideas" / run_id / "worker.pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="ascii").strip())
            candidate = psutil.Process(pid)
            args = candidate.cmdline()
        except (psutil.NoSuchProcess, ValueError):
            return None, False
        except (psutil.AccessDenied, OSError):
            return None, None
        if ("bellomberg.agents.trade_idea" in args and "--run-id" in args
                and args.index("--run-id") + 1 < len(args)
                and args[args.index("--run-id") + 1] == run_id):
            return candidate, True
        return None, False
    uncertain = False
    for process in psutil.process_iter(["cmdline"]):
        try:
            args = process.info.get("cmdline") or []
            if ("bellomberg.agents.trade_idea" in args and "--run-id" in args
                    and args.index("--run-id") + 1 < len(args)
                    and args[args.index("--run-id") + 1] == run_id):
                return process, True
        except psutil.AccessDenied:
            uncertain = True
        except (psutil.NoSuchProcess, IndexError, ValueError):
            continue
    return None, None if uncertain else False


def _revalidate_orphan_route(current, detail, *, quote_sampler=None, portfolio_loader=None):
    """Recheck free, mutable inputs before reusing persisted routing attestations."""
    from bellomberg.agents.trade_idea import (
        _candidate_quote_matches, _candidate_quote_receipt, _fx_receipt, _fx_observations_match)
    from bellomberg.reporting.trade_idea_delivery import _candidate_workbooks
    from bellomberg.storage.memory_db import MemoryDB

    run, result = detail["run"], detail["result"]
    progress = detail.get("progress") or {}
    checks = progress.get("routing_checks")
    quality = progress.get("report_quality") or {}
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    from bellomberg.core.trade_idea_policy import report_quality_sufficient
    # Research runs attest the sealed research, not a workbook valuation.
    research = run.get("analysis_mode") == RESEARCH_ANALYSIS_MODE
    required = ("identity_verified", "evidence_sufficient", "red_team_complete",
                "capo_valid", "mandate_valid", "sizing_valid",
                "research_reviewed" if research else "valuation_checked",
                "history_context_sent", "candidate_price_revalidated", "fx_revalidated")
    if not isinstance(checks, dict) or any(checks.get(key) is not True for key in required):
        return None, "Attestazioni operative persistite assenti o incomplete"
    if not report_quality_sufficient(quality):
        return None, "Qualita' del dossier non attestata prima del crash"
    quotes = progress.get("candidate_quote_receipts") or {}
    initial, final = quotes.get("initial"), quotes.get("final")
    if not _candidate_quote_matches(initial, final):
        return None, "Quotazione candidato precedente non coerente"
    try:
        if quote_sampler is None:
            board = SimpleNamespace(data={"_identity": {"ticker": run["ticker"],
                    "currency": run.get("currency")}}, tool_receipts=[], tool_log=[])
            current_quote = _candidate_quote_receipt(board, run["ticker"])
        else:
            current_quote = quote_sampler(run)
    except Exception as exc:
        return None, "Rilettura quotazione candidato KO: " + type(exc).__name__
    if not _candidate_quote_matches(final, current_quote):
        return None, "Quotazione candidato cambiata, stale o assente al riavvio"

    fx = progress.get("fx_receipts") or {}
    fx_initial, fx_final = fx.get("initial"), fx.get("final")
    try:
        portfolio = (portfolio_loader() if portfolio_loader is not None else
                     MemoryDB(db_path=current.db_path).get_portfolio_summary())
        current_fx = _fx_receipt(portfolio)
    except Exception as exc:
        return None, "Rilettura FX/book KO: " + type(exc).__name__
    if not (fx_initial and fx_final and fx_initial.get("valid") is True
            and fx_final.get("valid") is True and current_fx["valid"]
            and _fx_observations_match(fx_initial.get("observations"), fx_final.get("observations"))
            and _fx_observations_match(fx_final.get("observations"), current_fx["observations"])):
        return None, "FX/book cambiato o non verificabile al riavvio"

    refs = result.get("valuation_refs") or []
    if research:
        if refs:
            return None, "Riferimenti di valutazione inattesi in una run di ricerca"
        return checks, None
    generations = progress.get("valuation_generations") or []
    try:
        checked = _candidate_workbooks(run["ticker"], generations,
            progress.get("valuation_attempts") or (), [MODELS_DIR, REPORT_DIR], refs)
    except Exception as exc:
        return None, "Workbook candidato non rileggibile: " + type(exc).__name__
    expected = {(row["snapshot_id"], row["generation_id"], row["valuation_date"])
                for row in refs}
    verified = {(row.get("snapshot_id"), row.get("generation_id"), row.get("valuation_date"))
                for row in checked["valuations"] if row.get("status") == "ready"}
    if not expected or expected != verified or len(expected) != len(refs):
        return None, "Workbook/sidecar non corrisponde ai riferimenti del Capo al riavvio"
    return checks, None


def recover_orphan_runs(db_path=SQLITE_PATH, *, delivery=None,
                        quote_sampler=None, portfolio_loader=None):
    """Reconcile dead workers and delivery windows; never resume paid analysis."""
    try:
        current = TradeIdeaStore(db_path)
    except (FileNotFoundError, RuntimeError):
        return {"status": "schema_unavailable", "interrupted": 0, "recovered": 0}
    from bellomberg.agents.trade_idea import deliver_trade_idea

    summary = {"status": "ready", "interrupted": 0, "recovered": 0,
               "unknown_liveness": 0, "errors": []}
    offset = 0
    while True:
        page = current.list_runs(limit=200, offset=offset)
        rows = page["runs"]
        for row in rows:
            run_id, state = row["id"], row["technical_status"]
            detail = current.get_run(run_id)
            email_status = detail["email"]["status"]
            if row.get("phase") == "review_required":
                if email_status == "sending":
                    current.recover_sending(run_id)
                    summary["recovered"] += 1
                continue
            pending = (state in ("accepted", "running") or
                       (state in ("completed", "incomplete") and detail["result"] and
                        (row["destination"]["kind"] == "none" or
                         email_status in ("not_attempted", "ready", "sending"))))
            if not pending:
                continue
            _, alive = _worker_state(run_id)
            if alive is True:
                continue
            if alive is None:
                summary["unknown_liveness"] += 1
                continue
            try:
                if state == "running":
                    current.interrupt_run(run_id, reason="Worker Trade Idea non piu' vivo al riavvio")
                    summary["interrupted"] += 1
                    continue
                if state == "accepted":
                    current.request_stop(run_id)
                    summary["interrupted"] += 1
                    continue
                if state == "completed" and row["destination"]["kind"] == "none":
                    if detail["result"]["judgment"] == "favorable":
                        checks, reason = _revalidate_orphan_route(current, detail,
                            quote_sampler=quote_sampler, portfolio_loader=portfolio_loader)
                    else:
                        checks, reason = {}, None
                    if reason:
                        current.mark_routing_failure(run_id,
                            "Routing prudenziale dopo riavvio: " + reason)
                    current.route_result(run_id, checks or {})
                    summary["recovered"] += 1
                if state == "incomplete" and row["destination"]["kind"] == "none":
                    current.route_result(run_id, {})
                    summary["recovered"] += 1
                if email_status == "sending":
                    current.recover_sending(run_id)
                    summary["recovered"] += 1
                elif email_status in ("not_attempted", "ready"):
                    progress = detail.get("progress") or {}
                    (delivery or deliver_trade_idea)(current, run_id,
                        valuation_results=(progress.get("valuation_generations")
                                           or progress.get("valuation_results")),
                        valuation_attempts=progress.get("valuation_attempts") or ())
                    summary["recovered"] += 1
            except Exception as exc:
                summary["errors"].append({"run_id": run_id,
                    "reason": type(exc).__name__ + ": " + str(exc)[:350]})
        offset += len(rows)
        if not rows or offset >= page["total"]:
            break
    return summary


def install_trade_idea_routes(app, require_session, *, db_path=SQLITE_PATH,
                              weekly_active=None, source_archive_root=None):
    app.add_exception_handler(StorageUnavailable, _storage_unavailable_response)
    router = APIRouter(prefix="/trade-ideas", dependencies=[Depends(require_session)])
    snapshots = _PreflightSnapshots()

    def archive_root():
        if source_archive_root is not None:
            return Path(source_archive_root).resolve()
        if Path(db_path).resolve() == Path(SQLITE_PATH).resolve():
            return DATA_DIR / 'filing_archive'
        raise HTTPException(503, "Archivio fonti esplicito richiesto per il DB alternativo")

    def store():
        return _store(db_path)

    def checker():
        current = store()
        return bool(active_paid_reason(current, weekly_active=bool(weekly_active and weekly_active())))

    @router.post("/preflight")
    def preflight(body: PreflightBody, session: str = Depends(require_session)):
        from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
        root = archive_root()
        try:
            _body_peers(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        checked = preflight_trade_idea(body.ticker, body.pm_view, body.view_source,
                                      body.budget_limit_usd, active_checker=checker,
                                      analysis_mode=RESEARCH_ANALYSIS_MODE,
                                      execution_policy=CURRENT_EXECUTION_POLICY,
                                      archive_root=root,
                                      **({"document_sources": body.document_sources} if body.document_sources else {}))
        if checked.get('ok'):
            try:
                binding = _preflight_binding(body, session, db_path=db_path, language=capture_language())
                qualified = checked.get('_source_qualification') or {}
                checked = snapshots.remember((binding, qualified.get('fingerprint')), checked)
            except ValueError as exc:
                raise HTTPException(428, str(exc)) from exc
        checked.pop("_source_qualification", None)
        return checked

    @router.post("/runs", status_code=202)
    def start(body: StartBody, session: str = Depends(require_session)):
        from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE, is_research_mode
        current = store()
        existing = current.get_by_idempotency_key(body.idempotency_key)
        if existing is not None:
            if not _matching_retry(body, existing):
                raise HTTPException(409, "Idempotency key riusata con payload diverso")
            return {"run_id": existing["run"]["id"],
                    "status": existing["run"]["technical_status"]}
        if body.cost_acknowledged is not True:
            raise HTTPException(428, "Conferma del limite di spesa richiesta")
        language = capture_language()
        try:
            binding = _preflight_binding(body, session, db_path=db_path, language=language)
            cache_key = (binding, body.authorization.source_fingerprint)
            cached = snapshots.get(cache_key)
        except ValueError as exc:
            raise HTTPException(428, str(exc)) from exc
        qualification = cached['_source_qualification']
        if not is_research_mode(qualification):
            raise HTTPException(428, 'Nuove analisi senza Excel: ripetere il preflight della ricerca')
        from bellomberg.core.trade_idea_contract import validate_run_authorization
        from bellomberg.valuation.trade_idea_model import (
            recheck_accepted_sources, source_validation_receipt)
        root = archive_root()
        source_verified = False

        def recheck(ticker, identity, as_of, **_options):
            nonlocal source_verified
            counterproof, verified = None, False
            try:
                validate_run_authorization(body.authorization.model_dump(), qualification)
                if as_of != qualification.get('as_of'):
                    raise ValueError('Snapshot del preflight scaduto; rifare Verifica fonti')
                identity_pin = {key: identity.get(key) for key in
                                ('ticker', 'name', 'exchange', 'currency', 'status')}
                counterproof = recheck_accepted_sources(qualification, ticker, identity_pin,
                                                       archive_root=root)
                validate_run_authorization(body.authorization.model_dump(), counterproof)
                verified = True
            except Exception:
                snapshots.consume(cache_key)
                raise
            finally:
                receipt = source_validation_receipt(qualification, counterproof,
                    mode='accepted_snapshot', status='verified' if verified else 'failed')
                receipt['stage'] = 'preflight_to_acceptance'
                _save_admission_source_receipt(db_path, receipt)
            source_verified = True
            return deepcopy(qualification)

        checked = preflight_trade_idea(body.ticker, body.pm_view, body.view_source,
                                       body.budget_limit_usd, active_checker=checker,
                                       analysis_mode=RESEARCH_ANALYSIS_MODE,
                                       execution_policy=CURRENT_EXECUTION_POLICY,
                                       identity_resolver=lambda _ticker: deepcopy(cached['identity']),
                                       source_qualifier=recheck, archive_root=root,
                                       **({"document_sources": body.document_sources} if body.document_sources else {}))
        if not checked["ok"]:
            raise HTTPException(start_refusal_code(checked["reasons"]), "; ".join(checked["reasons"]))
        # 10/10 (R14b M5, Opus 5.5): il marcatore salvato e' la MISURA del preflight
        # (execution_status), non un letterale; serve anche la controverifica tornata qui.
        measured_execution = (checked.get("source_qualification") or {}).get("execution_status")
        if not source_verified or measured_execution != "completed":
            raise HTTPException(428, "Controverifica delle fonti del preflight non eseguita "
                                     "(execution_status=" + str(measured_execution) + ")")
        if (checked.get('execution_policy') != CURRENT_EXECUTION_POLICY or
                cached.get('execution_policy') != CURRENT_EXECUTION_POLICY or
                checked['models'] != cached['models'] or
                (checked.get('catalog_snapshot') or {}).get('models') !=
                (cached.get('catalog_snapshot') or {}).get('models')):
            raise HTTPException(428, "Catalogo modelli o tariffe cambiati; rifare Verifica fonti")
        try:
            receipt = (qualification or {}).get("document_receipt") or {}
            if normalize_sources(body.document_sources) != normalize_sources([
                    row["request"] for row in receipt.get("documents", [])]):
                raise ValueError("Le fonti PM verificate non corrispondono alla richiesta di avvio")
            authorization = validate_run_authorization(body.authorization.model_dump(), qualification)
            if checked.get("preparation", {}).get("required") and "model_preparation" not in authorization["activities"]:
                raise ValueError("authorization must explicitly include model_preparation")
        except ValueError as exc:
            raise HTTPException(428, str(exc)) from exc
        identity = checked["identity"]
        request = {"ticker": identity["ticker"], "company_name": identity["name"],
                   "analysis_mode": RESEARCH_ANALYSIS_MODE,
                   "execution_policy": CURRENT_EXECUTION_POLICY,
                   "exchange": identity["exchange"], "currency": identity.get("currency"),
                   "view_text": body.pm_view, "view_origin": body.view_source,
                   "language": language,
                   "budget_limit_usd": checked["budget"]["limit_usd"],
                   "cost_acknowledged": True,
                   "authorization": authorization, "source_qualification": qualification,
                   # controverifica eseguita e tornata (guardia 428 sopra): la misura del preflight
                   "source_qualification_execution": measured_execution,
                   "models": {item["role"]: {"model": item["model"],
                                              "reasoning_effort": item["reasoning_effort"]}
                              for item in checked["models"]},
                   "catalog_snapshot": checked["catalog_snapshot"]}
        if _body_peers(body):
            request["peers"] = _body_peers(body)
        try:
            accepted = current.create_run(request, idempotency_key=body.idempotency_key)
        except (IdempotencyConflict, RunConflict) as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:  # richiesta invalida (es. peer = ticker risolto): 422, non 500
            raise HTTPException(422, str(exc)) from exc
        run_id = accepted["run"]["id"]
        if accepted.get("created", True):
            snapshots.consume(cache_key)
            _spawn_worker(run_id, current, db_path=db_path)
        return {"run_id": run_id, "status": "accepted"}

    @router.get("/active")
    def active():
        return {"run_id": _active_id(store())}

    @router.get("/runs")
    def runs(ticker: str | None = None, status: str | None = None,
             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        current = store()
        try:
            listed = current.list_runs(ticker=ticker, status=status, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"runs": [_public_run(row) for row in listed["runs"]], "total": listed["total"]}

    @router.get("/runs/{run_id}")
    def detail(run_id: str):
        try:
            return _public_detail(store().get_run(run_id), run_id=run_id)
        except KeyError as exc:
            raise _keyerror_http(exc, "lettura") from exc

    @router.post("/runs/{run_id}/stop")
    def stop(run_id: str):
        current = store()
        try:
            current.request_stop(run_id)
            state = current.get_run(run_id)["run"]["technical_status"]
        except KeyError as exc:
            raise _keyerror_http(exc, "arresto") from exc
        if state == "running":
            proc, alive = _worker_state(run_id)
            if alive is None:
                raise HTTPException(503, "Stop registrato; worker non verificabile: stato sospeso finché si accerta l'uscita")
            try:
                if proc is not None:
                    proc.kill()
                    proc.wait(timeout=10)
                current.cancel_run_after_stop(run_id, reason="Fermata dal PM; worker terminato")
            except Exception as exc:
                if current.get_run(run_id)["run"]["technical_status"] not in ("cancelled", "completed", "incomplete"):
                    raise HTTPException(503, "Stop registrato ma uscita worker non confermata: " + str(exc)) from exc
            _PROCS.pop(run_id, None)
        elif state == "cancelled":
            proc, alive = _worker_state(run_id)
            if alive is True:
                proc.kill()
                proc.wait(timeout=10)
                _PROCS.pop(run_id, None)
        return {"run_id": run_id, "status": current.get_run(run_id)["run"]["technical_status"]}

    @router.post("/runs/{run_id}/resume", status_code=202)
    def resume(run_id: str, body: ResumeBody):
        if not body.cost_acknowledged:
            raise HTTPException(428, "La continuazione richiede conferma delle nuove richieste nel tetto autorizzato")
        current = store()
        try:
            previous = current.get_run(run_id)
            from bellomberg.core.research_analysis import is_research_mode
            if not isinstance(previous, dict) or not isinstance(previous.get('run'), dict):
                # Z3b 05/10: un record senza 'run' non e' «non trovato» (era un KeyError -> 404).
                raise HTTPException(500, "Record Trade Idea malformato: dati della run assenti; prosecuzione bloccata")
            if not is_research_mode(previous['run']):
                raise RunConflict('Run Excel in archivio: prosecuzione analisi disattivata; restano consultazione e recupero dei risultati gia prodotti')
            successor = (previous.get('recovery') or {}).get('successor_run_id')
            if not successor and active_paid_reason(current, weekly_active=bool(weekly_active and weekly_active())):
                raise RunConflict("Un'altra run pagata e attiva")
            accepted = current.create_continuation(run_id, idempotency_key=body.idempotency_key,
                authorize_new_requests=True,
                recover_truncated_request_id=body.recover_truncated_request_id,
                **({'recover_failed_request_id': body.recover_failed_request_id}
                   if body.recover_failed_request_id is not None else {}),
                **({'authorize_price_refresh': True} if body.authorize_price_refresh else {}),
                **({'budget_limit_usd': body.budget_limit_usd} if body.budget_limit_usd is not None else {}),
                **({'resume_with_held_rejection_request_id': body.resume_with_held_rejection_request_id}
                   if body.resume_with_held_rejection_request_id is not None else {}),
                **({'capo_finalization_request_id': body.capo_finalization_request_id}
                   if body.capo_finalization_request_id is not None else {}))
        except KeyError as exc:
            # Solo l'assenza della run dichiarata dallo store e' un 404; ogni altra chiave
            # mancante (riserva di costo, consegna, campo del record) e' un errore dichiarato.
            raise _keyerror_http(exc, "prosecuzione") from exc
        except (IdempotencyConflict, RunConflict, BudgetBlocked, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        child = accepted['run']['id']
        if accepted.get('created'):
            _spawn_worker(child, current, db_path=db_path)
        return {'run_id': child, 'parent_run_id': run_id,
                'status': accepted['run']['technical_status'], 'created': accepted.get('created', False)}

    @router.post("/runs/{run_id}/delivery/recover")
    def recover_delivery(run_id: str):
        from bellomberg.agents.trade_idea import deliver_trade_idea
        current = store()
        try:
            detail = current.get_run(run_id)
            if not (detail.get('recovery') or {}).get('can_recover_delivery'):
                raise RunConflict("Nessun risultato terminale recuperabile per la sola consegna")
            deliver_trade_idea(current, run_id, output_dir=REPORT_DIR / 'trade_ideas' / run_id,
                              send_email=False)
            return _public_detail(current.get_run(run_id), run_id=run_id)
        except KeyError as exc:
            raise _keyerror_http(exc, "consegna") from exc
        except (RunConflict, ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/runs/{run_id}/costs/reconcile")
    def reconcile_costs(run_id: str, body: CostReconcileBody | None = None):
        """Uncertain costs: preview the provider's measured bills; record them only with apply=true.

        A free read-only OpenRouter lookup per uncertain request; no model is called and
        nothing is assumed free (missing id, 404 not yet indexed or model mismatch stay unknown).
        """
        from bellomberg.core.cost_reconciliation import reconcile_trade_idea_costs
        current = store()
        try:
            if current.get_run(run_id)["run"]["technical_status"] == "running":
                raise RunConflict("Run in corso: riconciliazione dei costi solo a run ferma")
            return reconcile_trade_idea_costs(current, run_id, apply=bool(body and body.apply))
        except KeyError as exc:
            raise _keyerror_http(exc, "riconciliazione") from exc
        except (RunConflict, ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/runs/{run_id}/artifacts/{artifact_id}")
    def artifact(run_id: str, artifact_id: str):
        try:
            detail = store().get_run(run_id)
        except KeyError as exc:
            raise _keyerror_http(exc, "download") from exc
        if detail.get("manifest_integrity") is False:
            raise HTTPException(409, "Manifest di consegna modificato; download bloccato")
        manifest = detail.get("artifacts") or {}
        rows = [row for row in manifest.get("artifacts") or [] if row.get("id") == artifact_id]
        if len(rows) != 1:
            raise HTTPException(404, "Artefatto non trovato")
        row = rows[0]
        path = Path(row["path"]).resolve()
        roots = [REPORT_DIR.resolve(), MODELS_DIR.resolve()]
        if not any(path.is_relative_to(root) for root in roots):
            raise HTTPException(409, "Percorso artefatto fuori perimetro")
        if not path.is_file():
            raise HTTPException(409, "Artefatto modificato o mancante")
        contents = path.read_bytes()
        if sha256(contents).hexdigest() != row.get("sha256"):
            raise HTTPException(409, "Artefatto modificato o mancante")
        mime = ("application/pdf" if row["kind"] == "pdf" else
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return Response(content=contents, media_type=mime, headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["name"], safe="")})

    @router.post("/runs/{run_id}/email/retry")
    def retry_email(run_id: str, body: EmailRetryBody | None = None):
        current = store()
        try:
            detail = current.get_run(run_id)
        except KeyError as exc:
            raise _keyerror_http(exc, "email") from exc
        if detail.get("manifest_integrity") is False:
            raise HTTPException(409, "Manifest di consegna modificato; invio bloccato")
        status_now = detail["email"]["status"]
        acknowledge_uncertain = bool(body and body.acknowledge_uncertain)
        if status_now not in ("failed", "uncertain") or (status_now == "uncertain" and not acknowledge_uncertain):
            raise HTTPException(409, "Retry email: failed oppure uncertain con conferma esplicita del possibile duplicato")
        manifest = detail.get("artifacts")
        if not manifest:
            raise HTTPException(409, "Manifest di consegna assente")
        from bellomberg.reporting.trade_idea_delivery import send_trade_idea_delivery
        try:
            attempt = current.claim_email(run_id, retry=True,
                                          acknowledge_uncertain=acknowledge_uncertain)
        except RunConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        try:
            outcome = send_trade_idea_delivery(manifest, language=manifest["language"])
            status = outcome.get("email_status") or outcome.get("status")
            if status not in ("accepted", "failed", "uncertain", "blocked"):
                status = "uncertain"
        except Exception as exc:
            status, outcome = "uncertain", {"error": type(exc).__name__ + ": " + str(exc)}
        current.record_email_outcome(run_id, attempt, status,
            receipt=outcome if status == "accepted" else None,
            error=None if status == "accepted" else str(outcome.get("email_error") or outcome.get("error")
                                                      or "Invio non accettato")[:4000])
        return current.get_run(run_id)["email"]

    app.include_router(router)
    return router
