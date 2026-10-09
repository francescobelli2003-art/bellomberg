"""
BELLOMBERG - FastAPI Backend
Localhost:8765 - serve l'app Electron frontend.

Endpoints:
  GET  /health                 - health check
  GET  /portfolio              - portafoglio live (qty, prezzi, P/L, NAV)
  GET  /portfolio/positions    - solo lista posizioni
  GET  /fx                     - cambi FX live
  GET  /memos                  - lista memo storici (paginated)
  GET  /memos/{id}             - dettaglio memo (markdown + paths)
  GET  /memos/search?q=...     - semantic search sui memo
  GET  /decisions              - decisioni (filter: status=pending|executed|skipped)
  POST /decisions/{id}/update  - update status/feedback/outcome
  POST /trade                  - log trade (buy/sell/trim/add)
  POST /feedback               - PM feedback
  GET  /macro                  - macro dashboard FRED
  POST /consigliere/run        - lancia run async (returns task_id)
  GET  /consigliere/status/{task_id} - status di un run
  GET  /tasks/scheduled        - lista task Windows Task Scheduler
  POST /prices/update          - force update prezzi

Run:
    python bellomberg_api.py
    (uvicorn bellomberg_api:app --host 127.0.0.1 --port 8765 --reload)
"""
import os
import re
import sys

from bellomberg.core import config  # noqa: F401  # 202-A: carica .env SUBITO - senza, i moduli che congelano chiavi all'import (news_aggregator) partono senza API key
from bellomberg.core.paths import MODELS_DIR, PACKAGE_ROOT, PROJECT_ROOT, REPORT_DIR

import json
import threading
import time
import secrets
import subprocess
import traceback
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from contextlib import asynccontextmanager

from bellomberg.api.news_refresh_manager import NewsRefreshManager
from bellomberg.market_data.filing_refresh import FilingRefreshManager
from bellomberg.reporting.email_sender import EmailOutboxWorker

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# --- Log a prova di pipe morta (autopsia changelog (40), ok PM 25/07 sera) ---
# Backend zombie = spawner morto -> stdout/stderr orfani -> su Windows ogni
# print lancia OSError. Il caso peggiore era print_exc DENTRO l'except di un
# endpoint: la seconda eccezione scappava dal handler e usciva un 500 muto.
# Qui il log si perde solo se la pipe e' gia' morta (si perdeva comunque).
def _safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except OSError:
        pass


def _safe_trace() -> None:
    try:
        traceback.print_exc()
    except OSError:
        pass


# --- Helper 500 unico (quick-win n.12, audit/21 §6; ok PM 26/07 sera) ---
# Prima: 64 rami `raise HTTPException(500, str(e))` di cui solo 5 lasciavano
# traccia nel log -> un endpoint moriva con UNA riga secca e zero materiale per
# l'autopsia (costo pagato davvero sul 500 di /news/macro, changelog (40)).
# In piu' `str(e)` puo' essere VUOTO (es. KeyError senza argomenti): un 500 con
# detail "" e' un buco NON dichiarato in casa nostra (regola PM 14/07).
# Qui: tipo dell'eccezione sempre presente + traceback sempre loggato.
# `detail` resta una STRINGA: il frontend la rende verbatim in 14 punti
# (err.response.data.detail) e un dict stamperebbe "[object Object]".
def _err500(e: Exception, where: str, hint: str = "") -> "HTTPException":
    _safe_trace()
    msg = str(e).strip()
    detail = f"{type(e).__name__}: {msg}" if msg else type(e).__name__
    if hint:
        detail = f"{hint} — {detail}"
    _safe_print(f"[500] {datetime.now().strftime('%H:%M:%S')} {where}: {detail}")
    return HTTPException(500, detail)


# --- bugfix #158: pulizia __pycache__ ad ogni avvio (previene bug da pyc stale) ---
def _cleanup_pycache() -> None:
    if sys.dont_write_bytecode:
        return  # Read-only/offline interpreters must not mutate the installed code tree.
    import shutil
    base = str(PACKAGE_ROOT)
    removed = 0
    for dirpath, dirnames, _ in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git", "data", "app")]
        if "__pycache__" in dirnames:
            try:
                shutil.rmtree(os.path.join(dirpath, "__pycache__"))
                removed += 1
            except Exception:
                pass
            dirnames.remove("__pycache__")
    if removed:
        print(f"[init] __pycache__ cleanup: {removed} dir rimosse (bugfix #158)")


_cleanup_pycache()

try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request, Body
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.responses import StreamingResponse, FileResponse, JSONResponse, RedirectResponse
    from bellomberg.core.api_presentation import PresentationJSONResponse
    from pydantic import BaseModel
    import uvicorn
    FASTAPI_OK = True
except ImportError:
    FASTAPI_OK = False
    print("[!] Install: pip install fastapi uvicorn")

from bellomberg.storage.memory_db import MemoryDB, connect_sqlite, DB_DIR, SQLITE_PATH
from bellomberg.core.language import text as _api_text
from bellomberg.agents.bellomberg import BRAND_NAME, VERSION


# ============================================================
# RUN TRACKING (in-memory per consigliere async)
# ============================================================

class RunState:
    def __init__(self):
        self.runs = {}  # {task_id: {status, started, finished, pid, log_path}}

    def start(self, task_id):
        self.runs[task_id] = {
            "task_id": task_id,
            "status": "running",
            "started": datetime.now().isoformat(),
            "log_path": os.path.join(DB_DIR, f"run_{task_id}.log"),
        }

    def finish(self, task_id, success=True, error=None):
        if task_id in self.runs:
            self.runs[task_id]["status"] = "completed" if success else "failed"
            self.runs[task_id]["finished"] = datetime.now().isoformat()
            if error:
                self.runs[task_id]["error"] = error

    def get(self, task_id):
        return self.runs.get(task_id)


run_state = RunState()


# ============================================================
# PYDANTIC MODELS
# ============================================================

class TradeIdentityIn(BaseModel):
    """ISIN verification confirmed together with the trade (the proposed ticker
    comes from the decision on the server, never from the client)."""
    isin: str
    source: str
    verified_at: str
    reason: str


class TradeDivergenceIn(BaseModel):
    decision_id: int
    reason: str


class TradeIn(BaseModel):
    ticker: str
    action: str  # BUY/SELL/TRIM/ADD/DIVIDEND
    quantita: float
    prezzo: float
    valuta: str = "EUR"
    note: Optional[str] = None
    pm_rationale: Optional[str] = None
    linked_decision_id: Optional[int] = None
    data: Optional[str] = None
    senza_decisione: bool = False
    preview_id: Optional[str] = None
    # Written in the SAME transaction as the trade, only after the PM confirms
    # the preview: the two tables are append-only, a cancelled preview writes nothing.
    instrument_identity: Optional[TradeIdentityIn] = None
    manual_divergence: Optional[TradeDivergenceIn] = None


class ManualTradeDivergenceIn(BaseModel):
    trade_id: int
    reason: str


class OpeningPositionIn(BaseModel):
    model_config = {"strict": True, "extra": "forbid"}
    ticker: str
    quantita: float
    prezzo_medio: float
    valuta: str
    as_of: str  # Balance known at this date; never the acquisition date.
    provenienza: str
    nome: Optional[str] = None
    nota: Optional[str] = None
    preview_id: Optional[str] = None


def _fonti_prezzi_dichiarate():
    """Elenco delle fonti prezzi PRESE DAL CODICE che le usa, non riscritto a mano.
    Fino al 20/08 `price_basis` di GET /portfolio diceva 'polygon/ibkr/yfinance/
    coingecko' mentre polygon era fuori dal 19/08: una dichiarazione che si scrive
    due volte diverge, prima o poi. Import fallito = dichiarato, mai un elenco
    inventato (regola 14/07)."""
    try:
        from bellomberg.cli.price_updater import FONTI_PREZZI
        return "/".join(FONTI_PREZZI) + " (in ordine di priorita'; polygon resta per opzioni/IV)"
    except Exception as e:
        return _api_text('n.d. (elenco fonti non leggibile: %s)', 'n/a (source list unreadable: %s)') % type(e).__name__


class TesiIn(BaseModel):
    # richiesta PM 20/08: "vorrei vedere e modificare le tesi dall'app"
    tesi: str
    conferma: bool = False       # scavalca la guardia svuotamento/accorciamento
    autore: Optional[str] = None  # chi ha scritto (default: 'app')


class CashMovementIn(BaseModel):
    tipo: str  # DEPOSIT/WITHDRAWAL (voce PM 12/08: versamenti "come un trade")
    importo_eur: float
    data: Optional[str] = None   # YYYY-MM-DD; assente = oggi
    nota: Optional[str] = None
    conferma: bool = False       # ALIAS: scavalca ENTRAMBE le guardie (com'era)
    # F43(5) 31/08: una chiave per guardia — confermare la soglia non disarma
    # piu' il doppio-click (F37 del frontend, decisione PM 25/08)
    conferma_soglia: bool = False     # scavalca SOLO la soglia importo
    conferma_duplicato: bool = False  # scavalca SOLO la guardia doppio-click


class FeedbackIn(BaseModel):
    feedback_text: str
    sentiment: str = "NEUTRAL"  # POSITIVE/NEGATIVE/NEUTRAL/SUGGESTION
    specialist: Optional[str] = None
    memo_id: Optional[int] = None
    decision_id: Optional[int] = None


class DecisionUpdateIn(BaseModel):
    status: Optional[str] = None
    pm_feedback: Optional[str] = None
    outcome_pct: Optional[float] = None
    outcome_eur: Optional[float] = None
    outcome_notes: Optional[str] = None


class ChatSessionCreate(BaseModel):
    agent_id: str
    title: Optional[str] = None


class ChatMessageIn(BaseModel):
    message: str


class LoginIn(BaseModel):
    pin: str


# A-M1 (audit 04 M1): NIENTE fallback silenzioso al PIN debole.
# PIN assente/vuoto o == '1234' => sistema MISCONFIGURATO => login fail-closed.
# (regola PM "no fallback silenziosi": il buco viene DICHIARATO, non tappato con un default.)
_WEAK_PIN = "1234"
def _get_configured_pin() -> Optional[str]:
    """PIN da BELLOMBERG_PIN nel .env. None se non impostato (nessun ripiego su 1234)."""
    pin = os.environ.get("BELLOMBERG_PIN")
    return pin.strip() if pin else None
def _pin_misconfigured() -> bool:
    """True se il PIN non coincide col contratto UI: quattro cifre ASCII, non 1234."""
    pin = _get_configured_pin()
    return (pin is None or re.fullmatch(r"[0-9]{4}", pin) is None
            or pin == _WEAK_PIN)


# ============================================================
# HARDENING #32 (audit 04 C1/C2/M3): sessioni in-memory + lockout
# ============================================================
_SESSION_TTL_S = 12 * 3600          # coerente con TTL 12h gia' usata dal LoginGate
_SESSIONS: Dict[str, float] = {}    # {token: expiry epoch}
_LOGIN_FAILS: List[float] = []      # timestamp dei tentativi PIN falliti (sliding window)
_LOCKOUT_WINDOW_S = 300             # 5 minuti
_LOCKOUT_MAX_FAILS = 5              # >5 falliti/5min -> 429

# CALLER INTERNI CENSITI (Task Scheduler, senza token): run_db_backup.bat,
# run_news_feed.bat, run_briefing_v2.bat chiamano questi 3 POST via PowerShell
# Invoke-RestMethod su 127.0.0.1. Esenzione SOLO per questi path, SOLO da loopback
# e SOLO con User-Agent non-browser (PowerShell/python-requests/curl).
# TRADEOFF dichiarato: un browser NON puo' forgiare lo User-Agent (forbidden header,
# quindi il drive-by localhost via pagina web resta bloccato); un altro processo
# locale potrebbe spoofarlo, ma un processo locale dello stesso utente puo' gia'
# leggere il PIN dal .env - accettato per non toccare i .bat schedulati.
_SCHEDULER_EXEMPT_PATHS = {"/db/backup", "/news/feed/refresh", "/news/briefing/refresh"}
_SCHEDULER_UA_MARKERS = ("PowerShell", "python-requests", "python-urllib", "curl/")


def _purge_expired_sessions() -> None:
    now = time.time()
    for t, exp in list(_SESSIONS.items()):
        if exp < now:
            _SESSIONS.pop(t, None)


def _is_scheduler_call(request: "Request") -> bool:
    """True se la richiesta e' un task schedulato interno censito:
    path in allowlist + loopback + User-Agent non-browser."""
    ua = request.headers.get("user-agent") or ""
    client_ip = request.client.host if request.client else ""
    return (request.url.path in _SCHEDULER_EXEMPT_PATHS
            and client_ip in ("127.0.0.1", "::1")
            and any(m in ua for m in _SCHEDULER_UA_MARKERS))


def require_session(request: "Request"):
    """Dependency FastAPI: richiede X-BB-Token valido sugli endpoint MUTANTI
    (POST/DELETE). I GET restano liberi (bind 127.0.0.1)."""
    token = request.headers.get("X-BB-Token") or ""
    if token:
        exp = _SESSIONS.get(token)
        if exp and exp > time.time():
            return token
        raise HTTPException(401, _api_text('Sessione scaduta o token non valido: rifare il login', 'Session expired or invalid token: log in again'))
    # Nessun token: esenzione per i soli scheduled task interni censiti (vedi sopra)
    if _is_scheduler_call(request):
        return "__scheduler__"
    raise HTTPException(401, _api_text('X-BB-Token mancante: login richiesto', 'X-BB-Token missing: login required'))


# ============================================================
# A-M3 (audit 04 M3): throttle in-memory sugli endpoint onerosi/distruttivi.
# Frena i loop che bruciano budget LLM (/consigliere/run) o quota provider
# (/prices/update, /news/feed/refresh) e i doppi-click (/db/backup).
# Esenti i task schedulati interni censiti (stessa regola di require_session).
# ============================================================
# Intervallo minimo (secondi) tra due chiamate allo STESSO endpoint.
_THROTTLE_S = {
    "/consigliere/run": 60,     # run a pagamento (~10-15$): ha gia' l'anti-duplicate guard
    "/prices/update": 30,       # quota provider (Polygon/Tiingo)
    "/news/feed/refresh": 30,   # quota provider (Marketaux/Tiingo)
    "/db/backup": 30,           # distruttivo-adjacent: stop al doppio-click
    # 02/10: sintesi AI di un articolo (crediti OpenRouter): stop al doppio-click. Chiave =
    # template della rotta, quindi un intervallo unico per tutte le notizie.
    "/news/{news_id}/article-summary": 3,
    "/market/news/translate": 2,
}
_LAST_CALL: Dict[str, float] = {}   # {path: epoch ultima chiamata ammessa}


def _throttle_key(request: "Request") -> str:
    """Path concreto; per le rotte con parametri, il loro template se e' in _THROTTLE_S."""
    path = request.url.path
    if path in _THROTTLE_S:
        return path
    scope = getattr(request, "scope", None) or {}
    route_path = getattr(scope.get("route"), "path", None)
    return route_path if route_path in _THROTTLE_S else path


def throttle(request: "Request"):
    """Dependency A-M3: 429+Retry-After se lo stesso path oneroso viene
    richiamato entro _THROTTLE_S[path]. Esente lo scheduler interno censito."""
    key = _throttle_key(request)
    min_interval_s = _THROTTLE_S.get(key)
    if min_interval_s is None or _is_scheduler_call(request):
        return
    now = time.time()
    last = _LAST_CALL.get(key)
    if last is not None and (now - last) < min_interval_s:
        retry_in = max(1, int(min_interval_s - (now - last)) + 1)
        raise HTTPException(
            429,
            _api_text(f'Richiesta troppo frequente su {request.url.path}: riprova tra {retry_in}s (throttle anti-abuso A-M3).', f'Request too frequent for {request.url.path}: try again in {retry_in}s (A-M3 abuse prevention).'),
            headers={"Retry-After": str(retry_in)},
        )
    _LAST_CALL[key] = now
    request.state.throttle_timestamp = now


def _refund_throttle(request: "Request"):
    """Rimuove solo il consumo fatto da questa richiesta, mai quello di una successiva."""
    path = _throttle_key(request)
    proprio = getattr(request.state, "throttle_timestamp", None)
    if proprio is not None and _LAST_CALL.get(path) == proprio:
        _LAST_CALL.pop(path, None)


def _require_mandato_run(request: "Request"):
    """Cancello economico della run: nessun throttle consumato se il mandato non e' pronto."""
    import bellomberg.core.mandato_pm as _mp
    try:
        return _mp.carica()
    except _mp.MandatoMancante as e:
        _refund_throttle(request)
        status = 428 if e.causa in ("assente", "incompleto") else 503
        raise HTTPException(status, _api_text('mandato non pronto (%s): %s', 'Mandate not ready (%s): %s') % (e.causa, str(e)))
    except Exception as e:
        _refund_throttle(request)
        raise _err500(e, "_require_mandato_run", _api_text('verifica del mandato prima della run', 'Checking the mandate before the run'))


# ============================================================
# APP LIFESPAN
# ============================================================

def _notify_model_tracking(db_path, ticker, trigger):
    """Post-commit model state never asks the caller to repeat a financial write."""
    try:
        from bellomberg.valuation.valuation_automation_installation import notify_tracking
        return notify_tracking(db_path, ticker, trigger)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Model tracking notification failed: %s", type(exc).__name__)
        return {"status": "error", "reason": type(exc).__name__ + ": " + str(exc)[:500]}


@asynccontextmanager
async def lifespan(app):
    print("=" * 60)
    print(f"{BRAND_NAME} API v{VERSION}")
    print(f"Listening on http://127.0.0.1:{api_port()}")
    print(f"Docs     DISATTIVATE (quick-win n.10, ok PM 26/07): /docs /redoc /openapi.json = 404")
    print("=" * 60)
    if _pin_misconfigured():
        print("!" * 60)
        print("[SECURITY][A-M1] BELLOMBERG_PIN assente o == '1234': LOGIN BLOCCATO (fail-closed).")
        print("  Imposta un PIN forte nel .env  ->  BELLOMBERG_PIN=<il-tuo-pin>  e riavvia.")
        print("!" * 60)
    try:
        from bellomberg.valuation.valuation_automation_installation import start_installation
        app.state.valuation_automation = start_installation(SQLITE_PATH)
        from bellomberg.api.trade_idea_routes import recover_orphan_runs
        try:
            app.state.trade_idea_recovery = recover_orphan_runs(SQLITE_PATH)
        except Exception as exc:
            app.state.trade_idea_recovery = {"status": "error",
                "reason": type(exc).__name__ + ": " + str(exc)[:350]}
        if app.state.trade_idea_recovery.get("status") != "ready" or any(
                app.state.trade_idea_recovery.get(key) for key in
                ("interrupted", "recovered", "unknown_liveness", "errors")):
            print("[trade-idea recovery] " + str(app.state.trade_idea_recovery), flush=True)
        from bellomberg.market_data.fund_market_worker import start_market_updates
        app.state.fund_market_worker = start_market_updates(SQLITE_PATH, os.path.join(DB_DIR, 'consensus_cache'))
        news_refresh_manager.start()   # G3: nessun giro all'avvio, pausa notte/weekend (PM 04/10)
        # Interruttore della pagina Filing: scelta salvata (env esplicitamente falsa vince).
        # Primo giro FILING_AUTO_REFRESH_DELAY_S dopo l'avvio (default 10 min, decisione PM 04/10).
        filing_refresh_manager.applica_preferenza()
        filing_refresh_manager.start()   # G2b: primo giro ritardato, non immediato
        # Una variabile EMAIL_* non valida non ferma il backend: il worker resta spento
        # e lo dichiara (log + app.state.email_outbox, con il nome della variabile).
        app.state.email_outbox = email_outbox_worker.start()
        yield
    finally:
        fund_market_worker = getattr(app.state, "fund_market_worker", None)
        if fund_market_worker is not None:
            fund_market_worker.stop()
        automation = getattr(app.state, "valuation_automation", None)
        runner = automation.get("runner") if isinstance(automation, dict) else None
        if runner is not None:
            runner.stop()
        email_outbox_worker.stop()
        filing_refresh_manager.stop()
        news_refresh_manager.stop()


# One backend-owned coordinator for both scheduled and manual News pulls.  It is
# deliberately outside NewsPage: the feed keeps refreshing while the user navigates.
news_refresh_manager = NewsRefreshManager()
# Controllo orario dei profili filing scaduti + recupero run orfani (thread backend).
filing_refresh_manager = FilingRefreshManager()
email_outbox_worker = EmailOutboxWorker()


def _hardcoded_economic_calendar(today, days_ahead: int):
    """Calendar baseline degli eventi macro chiave nei prossimi N giorni.
    Eventi: NFP, CPI, PPI, retail sales, jobless claims (weekly), PMI, GDP,
    FOMC + ECB + BoE + BoJ meetings, OPEC, IFO.
    """
    from datetime import date as _date, timedelta as _td
    end = today + _td(days=days_ahead)
    events = []

    # FOMC/BCE: date di DECISIONE derivate dal calendario UNICO in current_facts
    # (voce 13 §9-quattuortrigies: qui viveva una copia divergente — 04/11 e
    # 16/12 non esistono sul calendario Fed, le decisioni sono 28/10 e 09/12).
    from bellomberg.core.current_facts import (
        FOMC_2026 as _FOMC_PAIRS, ECB_2026 as _ECB_DECISIONI, CPI_US_2026, _rome_time)
    FOMC_2026 = [e.isoformat() for _s, e in _FOMC_PAIRS]
    for d in FOMC_2026:
        dt = _date.fromisoformat(d)
        if today <= dt <= end:
            events.append({"date": d, "time": _rome_time(dt, 14), "type": "Central Bank",
                           "title": _api_text('Decisione tassi FOMC + conferenza stampa', 'FOMC Rate Decision + Press Conference'),
                           "importance": 5, "country": "US"})

    # ECB 2026: dal calendario unico (le 4 date H1 che vivevano qui divergevano
    # da current_facts ed erano comunque passate; le decisioni restanti 2026 —
    # 10/09, 29/10, 17/12 — combaciano con ecb.europa.eu, verificato 01/08).
    ECB_2026 = [d.isoformat() for d in _ECB_DECISIONI]
    for d in ECB_2026:
        dt = _date.fromisoformat(d)
        if today <= dt <= end:
            events.append({"date": d, "time": "14:15 CET", "type": "Central Bank",
                           "title": _api_text('Decisione tassi BCE + conferenza stampa Lagarde', 'ECB Rate Decision + Lagarde Presser'),
                           "importance": 5, "country": "EU"})

    # BoE 2026 (8 meetings)
    BOE_2026 = ["2026-02-05", "2026-03-19", "2026-05-07", "2026-06-18",
                 "2026-08-06", "2026-09-17", "2026-11-05", "2026-12-17"]
    for d in BOE_2026:
        dt = _date.fromisoformat(d)
        if today <= dt <= end:
            events.append({"date": d, "time": "13:00 CET", "type": "Central Bank",
                           "title": _api_text('Decisione tassi BoE', 'BoE Rate Decision'),
                           "importance": 4, "country": "UK"})

    # BoJ 2026
    BOJ_2026 = ["2026-01-23", "2026-03-19", "2026-04-28", "2026-06-17",
                 "2026-07-31", "2026-09-18", "2026-10-30", "2026-12-18"]
    for d in BOJ_2026:
        dt = _date.fromisoformat(d)
        if today <= dt <= end:
            events.append({"date": d, "time": "06:00 CET", "type": "Central Bank",
                           "title": _api_text('Decisione tassi BoJ', 'BoJ Rate Decision'),
                           "importance": 4, "country": "JP"})

    # OPEC+ 2026 (typically monthly JMMC + quarterly full meeting)
    OPEC_2026 = ["2026-06-01", "2026-09-07", "2026-12-01"]
    for d in OPEC_2026:
        dt = _date.fromisoformat(d)
        if today <= dt <= end:
            events.append({"date": d, "time": "13:00 CET", "type": "Commodities",
                           "title": _api_text('Riunione OPEC+ (decisione produzione)', 'OPEC+ Meeting (production decision)'),
                           "importance": 4, "country": "OPEC"})

    # Release a data ESATTA: NFP primo venerdi', claims ogni giovedi', PMI Cina il 1.
    cursor = today
    while cursor <= end:
        wd = cursor.weekday()  # 0=Mon
        d_iso = cursor.isoformat()
        # NFP: 1st Friday
        if wd == 4 and cursor.day <= 7:
            events.append({"date": d_iso, "time": "14:30 CET", "type": "US Macro",
                           "title": _api_text('USA: occupazione non agricola + tasso di disoccupazione', 'US Nonfarm Payrolls + Unemployment Rate'),
                           "importance": 5, "country": "US"})
        # Initial Jobless Claims: ogni giovedi
        if wd == 3:
            events.append({"date": d_iso, "time": "14:30 CET", "type": "US Macro",
                           "title": _api_text('USA: nuove richieste sussidio disoccupazione (settimanali)', 'US Initial Jobless Claims (weekly)'),
                           "importance": 3, "country": "US"})
        # China PMI: 1st of month
        if cursor.day == 1:
            events.append({"date": d_iso, "time": "02:30 CET", "type": "China Macro",
                           "title": _api_text('Cina: PMI manifatturiero NBS', 'China NBS Manufacturing PMI'),
                           "importance": 4, "country": "CN"})
        cursor += _td(days=1)

    # Release a FINESTRA (02/10/2026): la data vera non e' cablata, solo una
    # regola plausibile sul mese. Prima ogni giorno della finestra emetteva
    # l'evento (CPI il 13, 14 e 15/10). Ora UNA occorrenza per mese, calcolata
    # sul mese intero — se gia' passata non esce, invece di slittare a oggi —
    # e dichiarata `date_estimated`. Festivita' non modellate.
    def _primo(mese, regola):
        """Primo giorno del mese che soddisfa la regola, o None."""
        giorno = mese
        while giorno.month == mese.month:
            if regola(giorno):
                return giorno
            giorno += _td(days=1)
        return None

    def _lavorativo(mese, n):
        """N-esimo giorno lavorativo (lun-ven) del mese."""
        giorno = mese - _td(days=1)
        while n:
            giorno += _td(days=1)
            if giorno.weekday() < 5:
                n -= 1
        return giorno

    def _cpi(mese):
        # CPI US: ~10-15 del mese, tue-thu
        return _primo(mese, lambda d: 10 <= d.day <= 15 and d.weekday() in (1, 2, 3))

    def _ppi(mese):
        # PPI US: il giorno lavorativo dopo il CPI (prima: finestra propria, stesso giorno del CPI)
        cpi = _cpi(mese)
        if cpi is None:
            return None
        giorno = cpi + _td(days=1)
        while giorno.weekday() >= 5:
            giorno += _td(days=1)
        return giorno

    def _gdp_title(d):
        q = (d.month - 1) // 3 or 4
        return _api_text(f"USA: stima preliminare PIL Q{q}", f"US GDP Q{q} Advance Estimate")

    def _fisso(testo_it, testo_en):
        return lambda d: _api_text(testo_it, testo_en)

    finestre = [
        # (data(mese) -> date|None, ora, tipo, titolo(d), importanza, paese)
        (_cpi, "14:30 CET", "US Macro",
         _fisso('USA: pubblicazione CPI / CPI core', 'US CPI / Core CPI Release'), 5, "US"),
        (_ppi, "14:30 CET", "US Macro",
         _fisso('USA: pubblicazione PPI', 'US PPI Release'), 3, "US"),
        # Retail Sales: mid-month
        (lambda m: _primo(m, lambda d: 14 <= d.day <= 17 and d.weekday() in (1, 2, 3, 4)), "14:30 CET", "US Macro",
         _fisso('USA: vendite al dettaglio mensili', 'US Retail Sales MoM'), 4, "US"),
        # ISM Manufacturing PMI: 1st business day (prima: solo lun/mar dei giorni 1-3, saltava mesi interi)
        (lambda m: _lavorativo(m, 1), "16:00 CET", "US Macro",
         _fisso('PMI manifatturiero ISM', 'ISM Manufacturing PMI'), 4, "US"),
        # ISM Services PMI: 3rd business day (prima: solo mar-gio dei giorni 3-5)
        (lambda m: _lavorativo(m, 3), "16:00 CET", "US Macro",
         _fisso('PMI servizi ISM', 'ISM Services PMI'), 4, "US"),
        # EU HICP Flash: end of month
        (lambda m: _primo(m, lambda d: d.day >= 28 and d.weekday() in (1, 2, 3, 4)), "11:00 CET", "EU Macro",
         _fisso('Eurozona: stima preliminare inflazione IPCA', 'Eurozone HICP Flash Estimate'), 4, "EU"),
        # Germany IFO: ~25 del mese
        (lambda m: _primo(m, lambda d: 24 <= d.day <= 26 and d.weekday() < 5), "10:00 CET", "EU Macro",
         _fisso('Germania: clima economico IFO', 'Germany IFO Business Climate'), 3, "DE"),
        # US GDP advance: end of January, April, July, October
        (lambda m: _primo(m, lambda d: d.month in (1, 4, 7, 10) and 26 <= d.day <= 30 and d.weekday() in (1, 2, 3)),
         "14:30 CET", "US Macro", _gdp_title, 5, "US"),
    ]
    mese = today.replace(day=1)
    while mese <= end:
        for data, ora, tipo, titolo, importanza, paese in finestre:
            giorno = data(mese)
            confirmed_cpi = False
            if data is _cpi:
                official = [d for d in CPI_US_2026 if (d.year, d.month) == (mese.year, mese.month)]
                if official:
                    giorno = official[0]
                    confirmed_cpi = True
                if giorno is not None:
                    ora = _rome_time(giorno, 8, 30)
            if giorno is not None and today <= giorno <= end:
                event = {"date": giorno.isoformat(), "time": ora, "type": tipo,
                               "title": titolo(giorno), "importance": importanza,
                               "country": paese, "date_estimated": not confirmed_cpi}
                if confirmed_cpi:
                    event["source"] = "https://www.bls.gov/schedule/news_release/cpi.htm"
                events.append(event)
        mese = (mese + _td(days=32)).replace(day=1)

    # Deduplica e sort by date+time
    seen = set()
    deduped = []
    for e in events:
        k = (e["date"], e["title"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(e)
    deduped.sort(key=lambda x: (x["date"], x.get("time", "")))
    return deduped


_TIPI_CON_EARNINGS = {"operating", "bank", "dat", "holding"}


def _tickers_earnings_da_negozio(positions, negozio):
    """Ticker Finnhub con natura societaria dichiarata; non deduce natura o alias."""
    origine = negozio.get("origine")
    if origine in {"assente", "illeggibile"}:
        return [], "negozio dei veicoli %s: %s" % (
            origine, negozio.get("motivo") or "causa non dichiarata")
    try:
        from bellomberg.storage.negozi_privati import carica_alias
        esito_alias = carica_alias()
        alias_finnhub = (esito_alias["alias"]["finnhub"]
                         if esito_alias["origine"] not in {"assente", "illeggibile"}
                         else {})
    except Exception as exc:
        esito_alias = {"origine": "illeggibile",
                       "motivo": "%s: %s" % (type(exc).__name__, exc)}
        alias_finnhub = {}
    tickers = []
    senza_natura = []
    senza_alias = []
    voci = negozio.get("veicoli", {})
    for posizione in positions:
        ticker = (posizione.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        voce = voci.get(ticker)
        tipo = voce.get("tipo") if isinstance(voce, dict) else None
        if tipo in _TIPI_CON_EARNINGS:
            if "." in ticker and ticker not in alias_finnhub:
                senza_alias.append(ticker)
            else:
                tickers.append(ticker)
        elif tipo in {"cef", "etf", "etn", "commodity", "crypto"}:
            continue
        else:
            senza_natura.append(ticker)
    note = []
    if senza_alias:
        stato_alias = esito_alias.get("origine")
        prefisso = ""
        if stato_alias in {"assente", "illeggibile"}:
            prefisso = "alias_fonti %s: %s; " % (
                stato_alias, esito_alias.get("motivo") or "causa non dichiarata")
        note.append(
            prefisso + "alias Finnhub mancante per %d ticker esteri (%s): esclusi dal "
            "calendario senza dedurre il simbolo USA" %
            (len(senza_alias), ", ".join(senza_alias)))
    if senza_natura:
        note.append(_api_text(
            "natura non dichiarata per %d ticker: esclusi dal calendario earnings "
            "senza inventare il tipo",
            "Nature not declared for %d tickers: excluded from the earnings calendar "
            "without inventing their type",
        ) % len(senza_natura))
    return tickers, "; ".join(note) or None


def _tickers_earnings_con_riserva(positions, negozio):
    """Come `_tickers_earnings_da_negozio`, ma i titoli societari che Finnhub gratuito non
    copre (simbolo non USA: alias assente o alias con suffisso di borsa, che risponde 403)
    non vengono scartati: vanno alla riserva yfinance. Torna (finnhub, yfinance, nota)."""
    finnhub, nota = _tickers_earnings_da_negozio(positions, negozio)
    alias_rotto = None
    try:
        from bellomberg.storage.negozi_privati import carica_alias
        esito_alias = carica_alias()
        alias = (esito_alias["alias"]
                 if esito_alias["origine"] not in {"assente", "illeggibile"} else {})
    except Exception as exc:  # G8: dichiarato in nota, non ingoiato
        alias = {}
        alias_rotto = "alias_fonti illeggibile: %s: %s" % (type(exc).__name__, exc)
    alias_finnhub = alias.get("finnhub", {}) if isinstance(alias, dict) else {}
    voci = negozio.get("veicoli", {}) if negozio.get("origine") not in {"assente", "illeggibile"} else {}
    riserva = []
    for posizione in positions:
        ticker = (posizione.get("ticker") or "").strip().upper()
        voce = voci.get(ticker)
        if not ticker or not isinstance(voce, dict) or voce.get("tipo") not in _TIPI_CON_EARNINGS:
            continue
        simbolo_fh = alias_finnhub.get(ticker) or (ticker if "." not in ticker else None)
        if simbolo_fh is None or "." in simbolo_fh:
            if ticker not in riserva:
                riserva.append(ticker)
    finnhub = [t for t in finnhub if t not in riserva]
    # la nota «alias Finnhub mancante» non vale per chi ha la riserva: resta solo il resto.
    # G8 (04/10/2026, Opus 5.5): lo stato «alias_fonti assente/illeggibile» invece RESTA:
    # con il file rotto tutti i titoli esteri vanno a yfinance col simbolo grezzo, e il PM
    # deve saperlo. Il titolo che yfinance non data e' dichiarato dall'endpoint.
    if nota:
        parti = [p for p in nota.split("; ") if "alias Finnhub mancante" not in p]
        nota = "; ".join(parti) or None
    if alias_rotto and not (nota and "alias_fonti " in nota):
        nota = "; ".join(x for x in (nota, alias_rotto) if x)
    return finnhub, riserva, nota


def _avviso_provider_bloccati(muti, coda_it, coda_en):
    """L'`avviso` delle rotte news quando il limiter dichiara provider bloccati.

    13/09 (Claude Opus 5): il prefisso era scritto solo in italiano in cinque rotte
    (/news/providers, /news/ticker, /news/search, /news/portfolio, /news/macro) e con
    X-BB-Language: en la frase usciva mezza italiana. Una definizione sola, la stessa
    dichiarazione di prima: nomi dei provider in ordine alfabetico, poi la coda della
    rotta, nella lingua della richiesta. Nessun provider bloccato = None, come prima."""
    if not muti:
        return None
    names = {
        "termini_news (negozio)": _api_text("termini di ricerca news (negozio)", "news search terms (store)"),
        "temi_titoli (negozio)": _api_text("titoli per tema (negozio)", "securities by topic (store)"),
    }
    return ((_api_text('copertura PARZIALE: fonti bloccate %s', 'PARTIAL coverage: blocked sources %s')
             % ", ".join(names.get(key, key) for key in sorted(muti))) + _api_text(coda_it, coda_en))


# quick-win n.10 (decisione PM 26/07 "procediamo con tutto"): Swagger/ReDoc/OpenAPI
# SPENTI. /openapi.json serviva la mappa completa dell'API a chiunque, e la CORS
# ammette Origin "null" (tradeoff dichiarato per Electron prod, audit/21 §4 n.6):
# una pagina qualsiasi poteva enumerare tutte le route. Il PM non e' sviluppatore
# e le route si leggono dal sorgente -> superficie in meno a costo zero.
app = FastAPI(
    title=f"{BRAND_NAME} API", version=VERSION, lifespan=lifespan,
    default_response_class=PresentationJSONResponse,
    docs_url=None, redoc_url=None, openapi_url=None,
) if FASTAPI_OK else None

if app:
    class _CodedHTTPException(HTTPException):
        def __init__(self, status_code, detail, code):
            super().__init__(status_code, detail)
            self.code = code

    @app.exception_handler(_CodedHTTPException)
    async def coded_error_response(request, exc):
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.detail, "code": exc.code},
                            headers=exc.headers or {})

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"],
    )

    @app.middleware("http")
    async def authenticate_null_origin(request: Request, call_next):
        """A sandboxed web iframe has Origin:null: require the Electron session."""
        public_paths = {"/health", "/auth/login", "/auth/status"}
        if (request.headers.get("origin") == "null"
                and request.method != "OPTIONS"
                and request.url.path not in public_paths):
            try:
                require_session(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code,
                                    content={"detail": exc.detail},
                                    headers=exc.headers or {})
        return await call_next(request)

    from bellomberg.api.language_middleware import LanguageMiddleware
    app.add_middleware(LanguageMiddleware)

    # CORS also covers language/bootstrap failures before the application runs.
    # CORS (hardening #32, audit 04 C2): niente wildcard+credentials.
    # Allowlist: Vite dev server (electron/main.ts usa http://localhost:5173 in dev)
    # e "null" = origin che Chromium invia dalle pagine file:// (Electron prod usa
    # loadFile su dist/index.html). Credentials disabilitate (auth via header X-BB-Token).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "null"],
        allow_credentials=False,
        # 06/09 (criterio (5), lotto B — ordine PM): PUT aggiunto per `PUT /mandato`,
        # il salvataggio della pagina Mandato. Senza, il browser rifiuta in PREFLIGHT e
        # l'handler non viene mai chiamato: la suite resta verde e la pagina non funziona.
        # PATCH aggiunto nello stesso edit perche' il difetto era gia' qui e piu' vecchio
        # del mandato: `@app.put("/positions/{ticker}/tesi")` e
        # `@app.patch("/chat/sessions/{id})` sono esposti da HEAD e il CORS li rifiuta.
        # Non e' mai esploso solo perche' nessuna pagina li chiama ancora — cioe' il
        # difetto era LATENTE, non assente. `tests/test_mandato_endpoint_put.py` pretende
        # ora che OGNI verbo esposto dall'API stia in questa lista.
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-BB-Token", "X-BB-Language", "Accept", "Cache-Control", "X-Requested-With"],
        expose_headers=["Content-Language", "X-BB-Language-Source", "X-BB-Language-Warning", "X-Valuation-Generation"],
    )



def get_db():
    return MemoryDB()


# ============================================================
# ENDPOINTS
# ============================================================

if FASTAPI_OK:

    from bellomberg.api.journal_routes import create_journal_router
    from bellomberg.api.agent_progress_routes import create_agent_progress_router
    from bellomberg.api.options_routes import create_options_router
    from bellomberg.api.language_routes import create_language_router
    from bellomberg.api.filing_routes import create_filing_router
    from bellomberg.api.valuation_automation_routes import create_valuation_automation_router
    from bellomberg.api.fundamentals_routes import create_fundamentals_router
    from bellomberg.core.paths import SQLITE_PATH as valuation_db_path

    app.include_router(create_journal_router(get_db, require_session))
    app.include_router(create_agent_progress_router(require_session))
    app.include_router(create_options_router(require_session))
    app.include_router(create_language_router(require_session))
    app.include_router(create_filing_router(
        require_session, avvia_aggiornamento=filing_refresh_manager.trigger,
        stato_aggiornamento=filing_refresh_manager.status, imposta_auto=filing_refresh_manager.imposta_auto))
    def _fund_market_refresh_state():
        runner = getattr(app.state, 'fund_market_worker', None)
        return runner.status() if runner is not None else {'status': 'stopped',
            'last_completed_at': None, 'last_result': None, 'error': 'backend_lifespan_not_started'}

    app.include_router(create_fundamentals_router(require_session,
        db_provider=lambda: SQLITE_PATH, cache_dir=os.path.join(DB_DIR, "consensus_cache"),
        roots=(REPORT_DIR, MODELS_DIR), refresh_provider=_fund_market_refresh_state))
    def _active_valuation_automation():
        binding = getattr(app.state, "valuation_automation", None)
        runner = binding.get("runner") if binding else None
        if runner is None or runner.status().get("status") not in ("running", "idle"):
            raise HTTPException(503, {"code": "worker_unavailable", "message": "Automazione non avviata"})
        return runner.manager

    from bellomberg.core.paths import DATA_DIR as valuation_data_root
    app.include_router(create_valuation_automation_router(require_session,
        db_provider=lambda: valuation_db_path, roots=[REPORT_DIR, MODELS_DIR],
        automation_provider=_active_valuation_automation,
        variants_root=valuation_data_root / "valuation_variants"))

    @app.get("/health")
    def health():
        # Review G5 R7: un worker email spento (es. variabile EMAIL_* non valida) si vede qui,
        # non solo nel log: running + reason col nome della variabile.
        outbox = getattr(app.state, "email_outbox", None) or {"running": False, "reason": "not_started"}
        return {"status": "ok", "brand": BRAND_NAME, "version": VERSION,
                "timestamp": datetime.now().isoformat(),
                "email_outbox": {"running": bool(outbox.get("running")), "reason": outbox.get("reason")}}

    @app.get("/valuation/automation/status", dependencies=[Depends(require_session)])
    def valuation_automation_status():
        binding = getattr(app.state, "valuation_automation", None)
        if binding is None:
            return {"status": "not_started", "reason": "backend_lifespan_not_started"}
        runner = binding["runner"]
        from bellomberg.valuation.valuation_automation_installation import archive_state
        configuration = archive_state()
        return {"configuration": configuration, "worker": runner.status() if runner else binding["state"]}

    # ===== DATABASE BACKUP =====
    @app.post("/db/backup", dependencies=[Depends(require_session), Depends(throttle)])
    def db_backup():
        """Crea backup zip di tutti i DB + JSON files in data/. Salvato in data/backups/."""
        try:
            import zipfile, glob
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = os.path.join(DB_DIR, "backups")
            os.makedirs(backup_dir, exist_ok=True)
            zip_path = os.path.join(backup_dir, f"bellomberg_backup_{ts}.zip")
            files_added = []
            db_checks = {}  # DB igiene §9-bis n.6: esito quick_check per DB, dichiarato
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # audit/11 §2: i DB girano in WAL — zippare il file vivo salta le transazioni
                # non checkpointate (il -wal non matcha il glob). Snapshot consistente con la
                # sqlite3 backup API su file temporaneo, poi zip dello snapshot.
                import sqlite3, tempfile
                for pattern in [os.path.join(DB_DIR, "*.db"), os.path.join(DB_DIR, "*.sqlite")]:
                    for f in glob.glob(pattern):
                        tmp = None
                        try:
                            fd, tmp = tempfile.mkstemp(suffix=".db")
                            os.close(fd)
                            src = sqlite3.connect(f, timeout=15)
                            dst = sqlite3.connect(tmp)
                            try:
                                src.backup(dst)
                                # DB igiene §9-bis n.6 (21/07): quick_check sullo SNAPSHOT
                                # prima di zipparlo — uno snapshot corrotto non finisce
                                # zitto nel backup "riuscito": si dichiara KO e si salta.
                                _qc = dst.execute("PRAGMA quick_check").fetchone()
                                _qc_ok = bool(_qc) and str(_qc[0]).lower() == "ok"
                            finally:
                                dst.close()
                                src.close()
                            db_checks[os.path.basename(f)] = "ok" if _qc_ok else (
                                "KO: " + (str(_qc[0])[:200] if _qc else "quick_check senza esito"))
                            if not _qc_ok:
                                _safe_print(f"[BACKUP] {f}: quick_check FALLITO sullo snapshot — "
                                            "NON incluso nel backup (dichiarato)")
                                continue
                            zf.write(tmp, os.path.basename(f))
                            files_added.append(os.path.basename(f))
                        except Exception as e:
                            db_checks[os.path.basename(f)] = f"KO: {type(e).__name__}: {e}"
                            _safe_print(f"[BACKUP] skip {f}: {e}")
                        finally:
                            if tmp and os.path.exists(tmp):
                                try:
                                    os.remove(tmp)
                                except Exception:
                                    pass
                # JSON/CSV: file normali, copia diretta
                for pattern in [os.path.join(DB_DIR, "*.json"), os.path.join(DB_DIR, "*.csv")]:
                    for f in glob.glob(pattern):
                        try:
                            zf.write(f, os.path.basename(f))
                            files_added.append(os.path.basename(f))
                        except Exception as e:
                            _safe_print(f"[BACKUP] skip {f}: {e}")
                # Anche ChromaDB se esiste. Fix 12/08: la cartella vera e'
                # data/chroma — "data/chroma_memos" non e' mai esistita, quindi
                # gli embedding non sono MAI finiti in uno zip e l'if senza
                # else taceva. Copia diretta dei file (embedding rigenerabili:
                # uno snapshot non transazionale qui e' accettabile e dichiarato).
                chroma_dir = os.path.join(DB_DIR, "chroma")
                if os.path.isdir(chroma_dir):
                    chroma_files = 0
                    chroma_skipped = 0
                    for root, _, fns in os.walk(chroma_dir):
                        for fn in fns:
                            full = os.path.join(root, fn)
                            arcname = os.path.relpath(full, DB_DIR)
                            try:
                                zf.write(full, arcname)
                                files_added.append(arcname)
                                chroma_files += 1
                            except Exception:
                                chroma_skipped += 1
                    chroma_note = _api_text(f'incluso: {chroma_files} file', f'Included: {chroma_files} files') + (
                        f", {chroma_skipped} saltati per errore di lettura (dichiarato)"
                        if chroma_skipped else "")
                else:
                    chroma_note = _api_text('assente: data/chroma non trovata (embedding NON nel backup)', 'missing: data/chroma not found (embeddings NOT in the backup)')
            size_mb = os.path.getsize(zip_path) / 1024 / 1024
            _all_ok = all(v == "ok" for v in db_checks.values()) if db_checks else False
            return {
                # ok=False se un DB manca dal backup per quick_check/errore: il
                # chiamante (task .bat compreso) non deve leggere "riuscito" a meta'
                "ok": _all_ok,
                "backup_path": zip_path,
                "size_mb": round(size_mb, 2),
                "files_count": len(files_added),
                "files": files_added[:50],
                "db_quick_check": db_checks,  # §9-bis n.6: esito per DB, dichiarato
                "chroma": chroma_note,  # fix 12/08: incluso/assente, mai zitto
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            raise _err500(e, "db_backup")

    @app.get("/db/backups")
    def db_list_backups():
        """Lista dei backup esistenti in data/backups/."""
        try:
            backup_dir = os.path.join(DB_DIR, "backups")
            if not os.path.isdir(backup_dir):
                return {"count": 0, "backups": []}
            items = []
            for f in sorted(os.listdir(backup_dir), reverse=True):
                full = os.path.join(backup_dir, f)
                if os.path.isfile(full):
                    items.append({
                        "filename": f,
                        "path": full,
                        "size_mb": round(os.path.getsize(full) / 1024 / 1024, 2),
                        "created": datetime.fromtimestamp(os.path.getmtime(full)).isoformat(),
                    })
            return {"count": len(items), "backups": items[:50]}
        except Exception as e:
            raise _err500(e, "db_list_backups")

    @app.delete("/db/backups/{filename}", dependencies=[Depends(require_session)])
    def db_delete_backup(filename: str):
        """Elimina un singolo backup file (security: solo file in data/backups/)."""
        try:
            # security: no path traversal
            if "/" in filename or "\\" in filename or ".." in filename:
                raise HTTPException(400, _api_text('Nome file non valido', 'Invalid filename'))
            path = os.path.join(DB_DIR, "backups", filename)
            if not os.path.exists(path):
                raise HTTPException(404, _api_text('Backup non trovato', 'Backup not found'))
            os.remove(path)
            return {"ok": True, "deleted": filename}
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "db_delete_backup")

    @app.post("/auth/login")
    def auth_login(body: LoginIn):
        """Login con PIN da .env BELLOMBERG_PIN (obbligatorio, != '1234').
        A-M1 fail-closed: se il PIN e' assente o == '1234' l'accesso e' BLOCCATO (503),
        niente ripiego silenzioso sul default.
        Constant-time comparison per evitare timing attack (anche se locale).
        Hardening #32: emette token di sessione (12h, in-memory) + lockout
        >5 tentativi falliti/5min -> 429 con Retry-After.
        """
        import hmac
        # A-M1: config errata => fail-closed, prima di lockout/confronto (non consuma tentativi)
        if _pin_misconfigured():
            raise HTTPException(
                503,
                _api_text("PIN non configurato in modo sicuro: imposta BELLOMBERG_PIN (!= '1234') nel file .env e riavvia il backend. Accesso bloccato (fail-closed).", "PIN is not securely configured: set BELLOMBERG_PIN (!= '1234') in .env and restart the backend. Access is blocked (fail-closed)."),
            )
        now = time.time()
        recent_fails = [t for t in _LOGIN_FAILS if (now - t) < _LOCKOUT_WINDOW_S]
        _LOGIN_FAILS[:] = recent_fails
        if len(recent_fails) >= _LOCKOUT_MAX_FAILS:
            retry_in = max(1, int(_LOCKOUT_WINDOW_S - (now - recent_fails[0])) + 1)
            raise HTTPException(
                429,
                _api_text(f'Troppi tentativi PIN falliti: riprova tra {retry_in}s', f'Too many failed PIN attempts: try again in {retry_in}s'),
                headers={"Retry-After": str(retry_in)},
            )
        expected = _get_configured_pin()
        if not hmac.compare_digest(body.pin or "", expected):
            _LOGIN_FAILS.append(now)
            raise HTTPException(401, _api_text('PIN non valido', 'Invalid PIN'))
        _LOGIN_FAILS.clear()
        _purge_expired_sessions()
        token = secrets.token_urlsafe(32)
        _SESSIONS[token] = now + _SESSION_TTL_S
        # "token" e' campo NUOVO additivo: i client che leggono solo "ok" restano compatibili
        return {"ok": True, "user": "pm", "brand": BRAND_NAME,   # 03/09: era il nome del PM, cablato
                "token": token, "expires_in_s": _SESSION_TTL_S}

    @app.get("/auth/status")
    def auth_status():
        """Indica se il PIN configurato e' assente/debole (security warning).
        A-M1: 'misconfigured' true => login fail-closed (503)."""
        misconf = _pin_misconfigured()
        return {
            # retro-compat LoginGate.tsx (mostra il warning su 'default_pin'):
            "default_pin": misconf,
            "configured": not misconf,
            "misconfigured": misconf,   # NUOVO: PIN assente o == '1234' => login bloccato
        }

    @app.get("/portfolio")
    def get_portfolio():
        try:
            db = get_db()
            snap = db.get_portfolio_summary()
            # fix #30 (audit 02 F5): as_of esplicito - timestamp + basi prezzi della valorizzazione
            try:
                with db._conn() as conn:
                    row = conn.execute(
                        "SELECT MIN(t.ts) AS oldest, MAX(t.ts) AS newest, COUNT(*) AS n FROM ("
                        " SELECT pp.ticker, MAX(pp.timestamp) AS ts FROM position_prices pp"
                        " JOIN positions p ON p.ticker = pp.ticker AND p.is_active = 1"
                        " GROUP BY pp.ticker) t").fetchone()
                snap["as_of"] = {
                    "valuation_time": snap.get("timestamp"),
                    "price_snapshots_oldest": row["oldest"] if row else None,
                    "price_snapshots_newest": row["newest"] if row else None,
                    "n_priced_tickers": row["n"] if row else 0,
                    # 20/08: qui c'era scritto "fonti polygon/ibkr/yfinance/coingecko",
                    # ma polygon e' USCITO dalle fonti prezzi il 19/08 (serviva aggregati
                    # daily come live: -4.054 € di NAV, changelog (73)). Una frase che
                    # afferma il falso vale un numero sbagliato. Ora l'elenco si DERIVA
                    # da price_updater.FONTI_PREZZI: non puo' piu' divergere da solo, e
                    # se l'import fallisce lo dichiara invece di indovinare.
                    "price_basis": "position_prices (ultimo snapshot per ticker; fonti: %s)" % _fonti_prezzi_dichiarate(),
                    "fx_basis": _api_text('FX live get_fx_to_eur (cache sessione, svuotata ogni 5 min via /fx)', 'Live FX get_fx_to_eur (session cache, cleared every 5 minutes through /fx)'),
                }
            except Exception:
                pass
            return snap
        except Exception as e:
            raise _err500(e, "get_portfolio")

    @app.get("/portfolio/positions")
    def list_positions():
        db = get_db()
        return db.get_portfolio()

    _FX_LAST_CLEAR = {"ts": 0.0}

    @app.get("/fx")
    def get_fx_rates():
        try:
            import time as _t
            from bellomberg.cli.price_updater import get_fx_to_eur_con_fonte, _FX_CACHE
            # 202-C: il ribbon polla ogni 60s; svuotare la cache a OGNI chiamata = ~8.600
            # download yfinance/giorno. TTL 5 min: i cambi non si muovono al secondo.
            if (_t.time() - _FX_LAST_CLEAR["ts"]) > 300:
                _FX_CACHE.clear()
                _FX_LAST_CLEAR["ts"] = _t.time()
            # Blocco cassa/valute 03/08 (regola 14/07): la fonte va dichiarata
            # accanto al tasso — 'fallback' = statico di maggio 2026, non un
            # cambio vivo. Catturata PER VALUTA dentro il giro (review 03/08:
            # a fine giro una richiesta concorrente puo' averla sovrascritta).
            # Additivo: rates non cambia forma.
            rates, sources = {}, {}
            for cur in ["USD", "GBP", "GBX", "CHF", "JPY", "HKD"]:
                fx, fonte = get_fx_to_eur_con_fonte(cur)
                if fx:
                    rates[cur] = round(fx, 6)
                sources[cur] = fonte
            return {"timestamp": datetime.now().isoformat(), "rates": rates,
                    "sources": sources}
        except Exception as e:
            raise _err500(e, "get_fx_rates")

    _OHLC_CACHE: Dict[str, Tuple[float, dict]] = {}

    @app.get("/market/ohlc")
    def market_ohlc(ticker: str, period: str = "1y", interval: str = "1d"):
        """UI v3 T2: OHLCV per i grafici a candele del terminale.
        Alias broker->Yahoo via price_updater.data_ticker (fonte unica), cache 5 min per chiave."""
        try:
            t = (ticker or "").strip().upper()
            if not t or len(t) > 16:
                raise HTTPException(400, _api_text('ticker non valido', 'Invalid ticker'))
            if period not in {"5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}:
                raise HTTPException(400, _api_text('period non valido', 'Invalid period'))
            if interval not in {"1d", "1wk", "1h", "30m"}:
                raise HTTPException(400, _api_text('interval non valido', 'Invalid interval'))
            key = t + "|" + period + "|" + interval
            now_ts = time.time()
            hit = _OHLC_CACHE.get(key)
            if hit and (now_ts - hit[0]) < 300:
                return hit[1]
            import yfinance as yf
            from bellomberg.cli.price_updater import data_ticker
            df = yf.Ticker(data_ticker(t)).history(period=period, interval=interval, auto_adjust=False)
            if df is None or df.empty:
                return {"ticker": t, "period": period, "interval": interval, "bars": [],
                        "error": _api_text('nessun dato da yfinance per ', 'No yfinance data for ') + t}
            bars = []
            for idx, row in df.iterrows():
                try:
                    o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
                except Exception:
                    continue
                if not (o == o and h == h and l == l and c == c):  # NaN guard
                    continue
                try:
                    v = row.get("Volume")
                    v = int(v) if v is not None and v == v else 0
                except Exception:
                    v = 0
                bars.append({"t": int(idx.timestamp()), "o": round(o, 6), "h": round(h, 6),
                             "l": round(l, 6), "c": round(c, 6), "v": v})
            out = {"ticker": t, "period": period, "interval": interval, "bars": bars}
            if bars:
                _OHLC_CACHE[key] = (now_ts, out)
            return out
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "market_ohlc")

    # ===== T4 FAVORITES: societa' preferite del PM (tabella propria, idempotente) =====
    def _fav_db():
        # B4 (02/09): il percorso e' UNO (memory_db.SQLITE_PATH); prima questo join
        # proprio creava un DB fantasma nel repo con BELLOMBERG_DATA_DIR altrove
        cx = connect_sqlite(SQLITE_PATH)  # hardening #32: WAL + busy_timeout (audit 01 F-01)
        cx.execute("""CREATE TABLE IF NOT EXISTS favorite_companies (
            ticker TEXT PRIMARY KEY, name TEXT, sector TEXT, industry TEXT,
            note TEXT, added_at TEXT DEFAULT (datetime('now')))""")
        try:
            cx.execute("ALTER TABLE favorite_companies ADD COLUMN note TEXT")  # 25/06: self-migrate nota PM
        except Exception:
            pass  # colonna gia' presente
        return cx

    @app.get("/favorites")
    def favorites_list():
        try:
            cx = _fav_db()
            rows = cx.execute("SELECT ticker, name, sector, industry, note, added_at FROM favorite_companies ORDER BY added_at DESC").fetchall()
            cx.close()
            return {"favorites": [{"ticker": r[0], "name": r[1], "sector": r[2], "industry": r[3], "note": r[4] or "", "added_at": r[5]} for r in rows]}
        except Exception as e:
            raise _err500(e, "favorites_list")

    @app.post("/favorites", dependencies=[Depends(require_session)])
    def favorites_add(ticker: str, name: str = "", sector: str = "", industry: str = "", note: str = ""):
        try:
            t = (ticker or "").strip().upper()
            if not t or len(t) > 16:
                raise HTTPException(400, _api_text('ticker non valido', 'Invalid ticker'))
            cx = _fav_db()
            # ON CONFLICT NON tocca 'note': ri-aggiungere un preferito non cancella il commento del PM
            cx.execute("INSERT INTO favorite_companies(ticker, name, sector, industry, note) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, sector=excluded.sector, industry=excluded.industry",
                       (t, (name or "")[:120], (sector or "")[:60], (industry or "")[:80], (note or "")[:1000]))
            cx.commit(); cx.close()
            return {"ok": True, "ticker": t,
                    "valuation_automation": _notify_model_tracking(SQLITE_PATH, t, "watchlist")}
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "favorites_add")

    @app.post("/favorites/{ticker}/note", dependencies=[Depends(require_session)])
    def favorites_set_note(ticker: str, note: str = ""):
        """25/06: commento/motivazione del PM su un preferito (letto dal consigliere)."""
        try:
            t = (ticker or "").strip().upper()
            cx = _fav_db()
            cur = cx.execute("UPDATE favorite_companies SET note=? WHERE ticker=?", ((note or "")[:1000], t))
            cx.commit(); changed = cur.rowcount; cx.close()
            if not changed:
                raise HTTPException(404, _api_text('preferito non trovato', 'Favorite not found'))
            return {"ok": True, "ticker": t}
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "favorites_set_note")

    @app.delete("/favorites/{ticker}", dependencies=[Depends(require_session)])
    def favorites_del(ticker: str):
        try:
            t = (ticker or "").strip().upper()
            cx = _fav_db()
            cx.execute("DELETE FROM favorite_companies WHERE ticker=?", (t,))
            cx.commit(); cx.close()
            return {"ok": True, "ticker": t}
        except Exception as e:
            raise _err500(e, "favorites_del")

    _MKT_CACHE: Dict[str, Tuple[float, dict]] = {}

    def _mkt_cached(key: str, ttl: float):
        hit = _MKT_CACHE.get(key)
        if hit and (time.time() - hit[0]) < ttl:
            return hit[1]
        return None

    @app.get("/market/search")
    def market_search(q: str):
        """UI v3 T3: ricerca GLOBALE titoli (endpoint search Yahoo, nessuna chiave, cache 10 min)."""
        try:
            qq = (q or "").strip()
            if not qq or len(qq) > 40:
                return {"results": []}
            key = "s|" + qq.lower()
            c = _mkt_cached(key, 600)
            if c:
                return c
            import requests
            r = requests.get("https://query2.finance.yahoo.com/v1/finance/search",
                             params={"q": qq, "quotesCount": 12, "newsCount": 0},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            r.raise_for_status()
            out = []
            for it in (r.json().get("quotes") or []):
                sym = it.get("symbol")
                if not sym:
                    continue
                out.append({"symbol": sym,
                            "name": it.get("shortname") or it.get("longname") or "",
                            "exchange": it.get("exchDisp") or it.get("exchange") or "",
                            "type": it.get("quoteType") or ""})
            res = {"results": out}
            _MKT_CACHE[key] = (time.time(), res)
            return res
        except Exception as e:
            raise _err500(e, "market_search")

    @app.get("/market/logos")
    def market_logos(tickers: str = ""):
        """Loghi dei titoli come `data:` URL (la CSP non ammette immagini da 127.0.0.1).
        Risolti a runtime dal profilo dell'emittente (Finnhub, poi icona del suo sito),
        scaricati una volta e tenuti in data/loghi/. Risposta: `logos` {T: url|null},
        `motivi` {T: perche' manca} (la UI mostra l'iniziale), `fonti` {T: origine}."""
        try:
            from bellomberg.market_data.loghi_titoli import loghi
            richiesti = [t for t in tickers.split(",") if t.strip()][:40]
            return loghi(richiesti)
        except Exception as e:
            raise _err500(e, "market_logos")

    @app.get("/market/quote")
    def market_quote(ticker: str):
        """UI v3 T3: scheda titolo per QUALSIASI ticker globale (yfinance info+fast_info, cache 5 min)."""
        try:
            t = (ticker or "").strip().upper()
            if not t or len(t) > 16:
                raise HTTPException(400, _api_text('ticker non valido', 'Invalid ticker'))
            key = "q|" + t
            c = _mkt_cached(key, 300)
            if c:
                return c
            import yfinance as yf
            from bellomberg.cli.price_updater import data_ticker
            tk = yf.Ticker(data_ticker(t))
            try:
                info = tk.info or {}
            except Exception:
                info = {}
            try:
                fi = dict(tk.fast_info) if tk.fast_info else {}
            except Exception:
                fi = {}

            def g(*names):
                for n in names:
                    v = info.get(n)
                    if v is None:
                        v = fi.get(n)
                    if v is not None:
                        return v
                return None

            out = {"ticker": t,
                   "name": g("longName", "shortName") or t,
                   "exchange": g("fullExchangeName", "exchange"),
                   "currency": g("currency"),
                   "price": g("currentPrice", "regularMarketPrice", "last_price", "lastPrice"),
                   "prev_close": g("previousClose", "regularMarketPreviousClose", "previous_close"),
                   "market_cap": g("marketCap", "market_cap"),
                   "pe": g("trailingPE"), "fwd_pe": g("forwardPE"), "eps": g("trailingEps"),
                   "div_yield": g("dividendYield"), "beta": g("beta"),
                   "high_52w": g("fiftyTwoWeekHigh", "year_high"), "low_52w": g("fiftyTwoWeekLow", "year_low"),
                   "volume": g("volume", "regularMarketVolume", "last_volume"),
                   "avg_volume": g("averageVolume", "three_month_average_volume"),
                   "sector": g("sector"), "industry": g("industry"),
                   "target_mean": g("targetMeanPrice"), "recommendation": g("recommendationKey"),
                   "ev": g("enterpriseValue"), "ev_ebitda": g("enterpriseToEbitda"),
                   "ev_sales": g("enterpriseToRevenue"), "peg": g("pegRatio", "trailingPegRatio"),
                   "pb": g("priceToBook"), "fcf": g("freeCashflow"),
                   "short_pct_float": g("shortPercentOfFloat"),
                   "summary": (g("longBusinessSummary") or "")[:700]}
            _MKT_CACHE[key] = (time.time(), out)
            return out
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "market_quote")

    # Simboli del cruscotto Mercati globali (Yahoo). Indici: 10 per area, nell'ordine
    # Americhe, Europa, Asia e Pacifico (il frontend li raggruppa per simbolo).
    # Materie prime: metalli, energia, agricoli. Azioni: dallo screener, per paese.
    OVERVIEW_GLOBAL = {
        "indici": [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow Jones"), ("^RUT", "Russell 2000"),
                   ("^NYA", "NYSE Composite"), ("^GSPTSE", "S&P/TSX"), ("^BVSP", "Bovespa"), ("^MXX", "IPC Messico"),
                   ("^MERV", "Merval"), ("^VIX", "VIX"),
                   ("^STOXX50E", "Euro Stoxx 50"), ("^STOXX", "Stoxx 600"), ("^FTSE", "FTSE 100"), ("^GDAXI", "DAX"),
                   ("^FCHI", "CAC 40"), ("FTSEMIB.MI", "FTSE MIB"), ("^IBEX", "IBEX 35"), ("^AEX", "AEX"),
                   ("^SSMI", "SMI"), ("^BFX", "BEL 20"),
                   ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"), ("000001.SS", "Shanghai"), ("399001.SZ", "Shenzhen"),
                   ("^BSESN", "Sensex"), ("^KS11", "KOSPI"), ("^TWII", "Taiwan"),
                   ("^AXJO", "ASX 200"), ("^STI", "Straits Times")],
        "commodities": [("GC=F", "Oro"), ("SI=F", "Argento"), ("PL=F", "Platino"), ("PA=F", "Palladio"),
                        ("HG=F", "Rame"), ("ALI=F", "Alluminio"),
                        ("CL=F", "Petrolio WTI"), ("BZ=F", "Brent"), ("NG=F", "Gas Naturale"),
                        ("HO=F", "Gasolio"), ("RB=F", "Benzina"),
                        ("ZC=F", "Mais"), ("ZW=F", "Frumento"), ("ZS=F", "Soia"), ("KC=F", "Caffè"),
                        ("CC=F", "Cacao"), ("SB=F", "Zucchero"), ("CT=F", "Cotone"), ("LE=F", "Bestiame")],
        "valute": [("EURUSD=X", "EUR/USD"), ("GBPUSD=X", "GBP/USD"), ("USDJPY=X", "USD/JPY"),
                   ("EURCHF=X", "EUR/CHF"), ("EURGBP=X", "EUR/GBP"), ("BTC-USD", "Bitcoin"),
                   ("ETH-USD", "Ethereum")],
        "obbligazioni": [("^TNX", "US Treasury 10Y"), ("^TYX", "US Treasury 30Y"),
                         ("^FVX", "US Treasury 5Y"), ("^IRX", "US T-Bill 13W")],
        "futures": [("ES=F", "S&P 500 Future"), ("NQ=F", "Nasdaq 100 Future"),
                    ("YM=F", "Dow Future"), ("RTY=F", "Russell 2000 Future")],
    }
    # Le azioni per paese NON sono un elenco nel codice (04/10/2026, decisione del
    # maintainer: un elenco fisso di titoli puo' far intuire il portafoglio): arrivano a
    # runtime dallo screener del provider, vedi market_data/universo_mercati.py.

    def _ultime_chiusure(syms):
        """Ultimo prezzo e variazione sulla chiusura precedente per piu' simboli:
        UNA chiamata yfinance batch (5 giorni). I simboli senza dati restano fuori."""
        quotes = {}
        try:
            import yfinance as yf
            raw = yf.download(syms, period="5d", progress=False, threads=True, group_by="ticker")
            lvl0 = set(raw.columns.get_level_values(0)) if hasattr(raw.columns, "get_level_values") else set()
            for s in syms:
                try:
                    if s not in lvl0:
                        continue
                    col = raw[s]["Close"].dropna()
                    if len(col) == 0:
                        continue
                    last = float(col.iloc[-1])
                    # G8 (04/10/2026, Opus 5.5): una sola chiusura (o la precedente a zero)
                    # non da' una variazione: None con motivo, mai uno 0.0 inventato.
                    prev = float(col.iloc[-2]) if len(col) >= 2 else None
                    if not prev:
                        quotes[s] = {"price": round(last, 4), "change_pct": None,
                                     "motivo": _api_text(
                                         "variazione n.d.: nessuna chiusura precedente valida nei 5 giorni",
                                         "change n/a: no valid previous close in the last 5 days")}
                        continue
                    chg = ((last / prev) - 1) * 100
                    quotes[s] = {"price": round(last, 4), "change_pct": round(chg, 2)}
                except Exception:
                    continue
        except Exception as e:
            # audit/11 §4: il vecchio guard `if "_log" in dir()` era SEMPRE falso
            # (_log non esiste nel modulo): errore yf sparito senza traccia.
            # G8: solo il tipo (il testo puo' portare l'URL con la querystring)
            _safe_print(f"[overview] yf error: {type(e).__name__}")
        return quotes

    @app.get("/market/overview")
    def market_overview(country: str = "US"):
        """Cruscotto di mercato stile investing.com: indici, azioni per paese, commodities,
        valute, obbligazioni, futures. UNA sola chiamata yfinance batch (cache 120s)."""
        try:
            from bellomberg.market_data import universo_mercati
            cc = (country or "US").strip().upper()
            if cc not in universo_mercati.REGIONI:
                cc = "US"
            key = "ovw|" + cc
            c = _mkt_cached(key, 120)
            if c:
                return c
            cats = dict(OVERVIEW_GLOBAL)
            sym_name = {}
            for lst in cats.values():
                for s, n in lst:
                    sym_name[s] = n
            syms = list(sym_name.keys())
            quotes = _ultime_chiusure(syms)
            azioni = universo_mercati.azioni_paese(cc)
            def _build(lst):
                righe = []
                for s, n in lst:
                    riga = {"ticker": s, "name": n,
                            "price": quotes.get(s, {}).get("price"),
                            "change_pct": quotes.get(s, {}).get("change_pct")}
                    if quotes.get(s, {}).get("motivo"):
                        riga["motivo"] = quotes[s]["motivo"]
                    righe.append(riga)
                return righe
            out = {"country": cc, "countries": sorted(universo_mercati.REGIONI),
                   "indici": _build(cats["indici"]), "azioni": azioni["azioni"],
                   "azioni_fonte": {"stato": azioni["stato"], "motivo": azioni["motivo"],
                                    "fonte": azioni["fonte"]},
                   "commodities": _build(cats["commodities"]), "valute": _build(cats["valute"]),
                   "obbligazioni": _build(cats["obbligazioni"]), "futures": _build(cats["futures"]),
                   "_timestamp": datetime.now().isoformat(timespec="seconds")}
            # audit/11 §4: mai cachare 120s un overview tutto-None da yf fallito, ne' uno
            # senza azioni (screener giu'): al prossimo giro si riprova.
            if quotes and azioni["stato"] == "ok":
                _MKT_CACHE[key] = (time.time(), out)
            return out
        except Exception as e:
            raise _err500(e, "market_overview")

    @app.get("/market/movers")
    def market_movers():
        """Mercati globali: variazione di oggi delle azioni di TUTTI i paesi del cruscotto,
        per la classifica dei titoli piu' mossi. L'insieme arriva a runtime dallo screener
        del provider (una richiesta per paese); un paese che non risponde e' dichiarato in
        `paesi`, e se non risponde nessuno `azioni` e' vuoto con `motivo`. Cache 120s."""
        try:
            c = _mkt_cached("movers", 120)
            if c:
                return c
            from bellomberg.market_data import universo_mercati
            esito = universo_mercati.universo()
            out = {"azioni": esito["azioni"], "paesi": esito["paesi"], "fonte": esito["fonte"],
                   "motivo": esito["motivo"],
                   "_timestamp": datetime.now().isoformat(timespec="seconds")}
            # come l'overview: mai cachare un risultato senza variazioni
            if any(r.get("change_pct") is not None for r in out["azioni"]):
                _MKT_CACHE["movers"] = (time.time(), out)
            return out
        except Exception as e:
            raise _err500(e, "market_movers")

    @app.get("/market/news")
    def market_news(ticker: str):
        """UI v3 T3: news per QUALSIASI ticker (Yahoo via yfinance.news, cache 10 min)."""
        try:
            t = (ticker or "").strip().upper()
            if not t or len(t) > 16:
                raise HTTPException(400, _api_text('ticker non valido', 'Invalid ticker'))
            key = "n|" + t
            c = _mkt_cached(key, 600)
            if c:
                return c
            import yfinance as yf
            from bellomberg.cli.price_updater import data_ticker
            # G8 (04/10/2026, Opus 5.5): ogni via che cade e' DICHIARATA in `errori`, e ogni
            # item dice da dove viene (`source`) e come e' stato agganciato (`match`):
            # prima tre vie finivano nello stesso array e un errore era un elenco vuoto.
            errori, vie_cadute = [], 0
            try:
                sym = data_ticker(t)
            except Exception as exc:  # alias non leggibile: si cerca col ticker com'e', DICHIARATO
                sym = t
                errori.append(_api_text(
                    "alias yfinance di %s non leggibile (%s: %s): ricerca col ticker com'e'",
                    "yfinance alias for %s unreadable (%s: %s): searched with the raw ticker")
                    % (t, type(exc).__name__, exc))
            try:
                raw = yf.Ticker(sym).news or []
            except Exception as exc:
                raw, vie_cadute = [], vie_cadute + 1
                # solo il tipo: il testo di un'eccezione di rete porta l'URL con la querystring
                errori.append(_api_text("notizie Yahoo del simbolo %s: %s",
                                        "Yahoo news for symbol %s: %s") % (sym, type(exc).__name__))
            items = []
            for it in raw[:20]:
                content = it.get("content") if isinstance(it.get("content"), dict) else it
                title = (content or {}).get("title") or it.get("title")
                if not title:
                    continue
                link = None
                cu = (content or {}).get("canonicalUrl") or (content or {}).get("clickThroughUrl")
                if isinstance(cu, dict):
                    link = cu.get("url")
                link = link or it.get("link")
                prov = (content or {}).get("provider")
                pub = prov.get("displayName") if isinstance(prov, dict) else it.get("publisher")
                ts = (content or {}).get("pubDate") or it.get("providerPublishTime")
                items.append({"title": title, "link": link, "publisher": pub, "published": ts,
                              "source": "yahoo_ticker_news", "match": "simbolo"})
            if not items:
                # Da ottobre 2026 yf.Ticker(...).news torna vuoto per ogni titolo; la ricerca
                # di Yahoo restituisce ancora le notizie collegate al simbolo. Per i titoli
                # fuori dagli USA (es. ACME.MI, 9999.T) il simbolo non porta notizie: si cerca
                # col nome della societa' che la stessa ricerca restituisce.
                # La ricerca per NOME puo' agganciare un'altra societa' dal nome simile:
                # quegli item portano `match: "nome"` e `match_query`, e la UI li marca.
                found, match, name = [], "simbolo", None
                try:
                    found_search = yf.Search(sym, news_count=15, max_results=1)
                    found = found_search.news or []
                    if not found:
                        quotes = getattr(found_search, "quotes", None) or []
                        name = (quotes[0].get("longname") or quotes[0].get("shortname")) if quotes else None
                        if name:
                            match = "nome"
                            found = yf.Search(name, news_count=15, max_results=1).news or []
                except Exception as exc:
                    found, vie_cadute = [], vie_cadute + 1
                    errori.append(_api_text("ricerca Yahoo (%s): %s", "Yahoo search (%s): %s")
                                  % (name if match == "nome" else sym, type(exc).__name__))
                for it in found:
                    if it.get("title"):
                        voce = {"title": it.get("title"), "link": it.get("link"),
                                "publisher": it.get("publisher"), "published": it.get("providerPublishTime"),
                                "source": "yahoo_search", "match": match}
                        if match == "nome":
                            voce["match_query"] = name
                        items.append(voce)
            res = {"ticker": t, "symbol": sym, "items": items, "errori": errori or None}
            # un elenco vuoto o con una via di rete caduta non si tiene in cache: al prossimo
            # giro si riprova (l'alias illeggibile resta dichiarato anche dalla cache)
            if items and not vie_cadute:
                _MKT_CACHE[key] = (time.time(), res)
            return res
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "market_news")

    @app.post("/market/news/translate", dependencies=[Depends(require_session), Depends(throttle)])
    def market_news_translate(request: Request, corpo=Body(...)):
        """Traduce nella lingua dell'app i titoli delle notizie di un titolo. SOLO dal pulsante
        della scheda (costa una chiamata al modello delle sintesi); i titoli gia' tradotti
        restano in memoria e non si ripagano."""
        titles = corpo.get("titles") if isinstance(corpo, dict) else None
        if not isinstance(titles, list) or not titles or not all(isinstance(t, str) for t in titles):
            _refund_throttle(request)
            raise HTTPException(400, _api_text("Servono i titoli da tradurre", "Titles to translate are required"))
        try:
            from bellomberg.market_data.headline_translation import translate_headlines
            result = translate_headlines(titles)
        except Exception as e:
            _refund_throttle(request)
            raise _err500(e, "market_news_translate")
        if result.get("cached") or result.get("status") == "not_configured":
            _refund_throttle(request)
        return result

    @app.get("/market/financials")
    def market_financials(ticker: str):
        """T4-2: bilanci storici annuali per QUALSIASI titolo (yfinance, ~4-5 anni, cache 1h).
        TODO futuro: profondita' 10y via sec_xbrl per US/ADR."""
        try:
            t = (ticker or "").strip().upper()
            if not t or len(t) > 16:
                raise HTTPException(400, _api_text('ticker non valido', 'Invalid ticker'))
            key = "f|" + t
            c = _mkt_cached(key, 3600)
            if c:
                return c
            import yfinance as yf
            from bellomberg.cli.price_updater import data_ticker
            tkr = yf.Ticker(data_ticker(t))

            def block(df, wanted):
                if df is None or getattr(df, "empty", True):
                    return {"years": [], "rows": {}}
                cols = list(df.columns)[::-1]  # cronologico
                years = []
                for ccol in cols:
                    try:
                        years.append(int(getattr(ccol, "year", 0)) or str(ccol)[:4])
                    except Exception:
                        years.append(str(ccol)[:4])
                rows = {}
                for label, names in wanted:
                    serie = None
                    for n in names:
                        if n in df.index:
                            vals = df.loc[n]
                            if hasattr(vals, "iloc") and getattr(vals, "ndim", 1) > 1:
                                vals = vals.iloc[0]
                            out = []
                            for ccol in cols:
                                try:
                                    v = float(vals[ccol])
                                    out.append(None if v != v else v)
                                except Exception:
                                    out.append(None)
                            serie = out
                            break
                    if serie is not None and any(x is not None for x in serie):
                        rows[label] = serie
                return {"years": years, "rows": rows}

            IS = [("Revenue", ["Total Revenue", "Operating Revenue"]),
                  ("Gross Profit", ["Gross Profit"]),
                  ("EBITDA", ["EBITDA", "Normalized EBITDA"]),
                  ("EBIT", ["EBIT", "Operating Income"]),
                  ("Net Income", ["Net Income", "Net Income Common Stockholders"]),
                  ("EPS Diluted", ["Diluted EPS"])]
            BS = [("Cash", ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]),
                  ("Total Debt", ["Total Debt"]),
                  ("Equity", ["Stockholders Equity", "Common Stock Equity"]),
                  ("Total Assets", ["Total Assets"])]
            CF = [("Operating CF", ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]),
                  ("Capex", ["Capital Expenditure"]),
                  ("Free Cash Flow", ["Free Cash Flow"]),
                  ("Dividends Paid", ["Cash Dividends Paid", "Common Stock Dividend Paid"]),
                  ("Buybacks", ["Repurchase Of Capital Stock", "Common Stock Payments"])]
            try: inc = block(tkr.income_stmt, IS)
            except Exception: inc = {"years": [], "rows": {}}
            try: bal = block(tkr.balance_sheet, BS)
            except Exception: bal = {"years": [], "rows": {}}
            try: cfs = block(tkr.cashflow, CF)
            except Exception: cfs = {"years": [], "rows": {}}
            out = {"ticker": t, "statements": {"income": inc, "balance": bal, "cashflow": cfs}}
            if inc["rows"] or bal["rows"] or cfs["rows"]:
                _MKT_CACHE[key] = (time.time(), out)
            else:
                out["error"] = _api_text('bilanci non disponibili da yfinance per ', 'Financial statements unavailable from yfinance for ') + t
            return out
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "market_financials")

    @app.get("/market/holders")
    def market_holders(ticker: str):
        """T4-2: ownership (major + institutional top 10) per qualsiasi titolo, cache 1h."""
        try:
            t = (ticker or "").strip().upper()
            if not t or len(t) > 16:
                raise HTTPException(400, _api_text('ticker non valido', 'Invalid ticker'))
            key = "h|" + t
            c = _mkt_cached(key, 3600)
            if c:
                return c
            import yfinance as yf
            from bellomberg.cli.price_updater import data_ticker
            tkr = yf.Ticker(data_ticker(t))

            def clean(v):
                try:
                    if v is None or (isinstance(v, float) and v != v):
                        return None
                    if hasattr(v, "isoformat"):
                        return str(v)[:10]
                    if isinstance(v, (int, float)):
                        return float(v)
                    return str(v)
                except Exception:
                    return None

            major = []
            try:
                mdf = tkr.major_holders
                if mdf is not None and not mdf.empty:
                    if "Value" in mdf.columns:
                        for idx, row in mdf.iterrows():
                            major.append({"label": str(idx), "value": clean(row["Value"])})
                    else:
                        for _, row in mdf.iterrows():
                            vals = [clean(x) for x in row.tolist()]
                            if len(vals) >= 2:
                                major.append({"label": str(vals[1]), "value": vals[0]})
            except Exception:
                pass
            inst = []
            try:
                idf = tkr.institutional_holders
                if idf is not None and not idf.empty:
                    for _, row in idf.head(10).iterrows():
                        rec = {}
                        for col in idf.columns:
                            rec[str(col).strip().lower().replace(" ", "_").replace("%", "pct")] = clean(row[col])
                        inst.append(rec)
            except Exception:
                pass
            out = {"ticker": t, "major": major, "institutional": inst}
            if major or inst:
                _MKT_CACHE[key] = (time.time(), out)
            return out
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "market_holders")

    def _resolve_memo_path(p: Optional[str]) -> Optional[str]:
        """Path PDF nel DB sono relativi alla radice del repo (es. 'report\\weekly_x.pdf')."""
        if not p:
            return None
        if os.path.isabs(p):
            return p
        return str(PROJECT_ROOT / p)

    @app.get("/memos")
    def list_memos(limit: int = 20, include_empty: bool = False, include_trade_ideas: bool = True):
        """Lista memo. Bugfix #169: aggiunge pdf_available/appendix_available
        (path valorizzato E file esistente su disco) e nasconde i memo vuoti
        (run falliti/test, markdown < 100 char) salvo include_empty=true.
        Archivio (decisione PM 04/10): i memo Trade Idea sono inclusi di default con
        kind/label/trade_idea_run_id (campi ADDITIVI); include_trade_ideas=false
        restituisce la lista di prima (solo Consigliere)."""
        db = get_db()
        out = []
        for m in db.get_archive_memos(limit=limit, include_trade_ideas=include_trade_ideas):
            md_len = len(m.get("full_markdown") or "")
            pdf_abs = _resolve_memo_path(m.get("pdf_path"))
            app_abs = _resolve_memo_path(m.get("appendix_path"))
            m["has_content"] = md_len > 100
            m["pdf_available"] = bool(pdf_abs and os.path.exists(pdf_abs))
            m["appendix_available"] = bool(app_abs and os.path.exists(app_abs))
            # 202-C: la lista non trasporta i markdown integrali (MB inutili: il FE
            # mostra titolo+data+link; il testo completo vive su GET /memos/{id})
            m.pop("full_markdown", None)
            if include_empty or m["has_content"] or m["pdf_available"]:
                out.append(m)
        return {"memos": out}

    @app.get("/memos/{memo_id}/pdf")
    def download_memo_pdf(memo_id: int):
        """Serve il PDF del memo (bugfix #169 — i link file:/// con path relativi non funzionavano)."""
        db = get_db()
        with db._conn() as conn:
            row = conn.execute("SELECT pdf_path,notes FROM memos WHERE id=?", (memo_id,)).fetchone()
        if row and str(row[1] or "").startswith("trade_idea:"):
            run_id = str(row[1]).removeprefix("trade_idea:")
            import uuid
            try:
                run_id = str(uuid.UUID(run_id))
            except ValueError as exc:
                raise HTTPException(409, "Provenienza memo Trade Idea non valida") from exc
            # The authenticated Trade Idea route verifies the saved manifest hash
            # and returns exactly those verified bytes.
            return RedirectResponse(
                url=f"/trade-ideas/runs/{run_id}/artifacts/pdf", status_code=307)
        path = _resolve_memo_path(row[0] if row else None)
        if not path or not os.path.exists(path):
            raise HTTPException(404, _api_text(f'PDF non disponibile per memo {memo_id}', f'PDF unavailable for memo {memo_id}'))
        return FileResponse(path, media_type="application/pdf",
                            filename=os.path.basename(path))

    @app.get("/memos/{memo_id}/appendix")
    def download_memo_appendix(memo_id: int):
        db = get_db()
        with db._conn() as conn:
            row = conn.execute("SELECT appendix_path FROM memos WHERE id=?", (memo_id,)).fetchone()
        path = _resolve_memo_path(row[0] if row else None)
        if not path or not os.path.exists(path):
            raise HTTPException(404, _api_text(f'Appendix non disponibile per memo {memo_id}', f'Appendix unavailable for memo {memo_id}'))
        return FileResponse(path, media_type="application/pdf",
                            filename=os.path.basename(path))

    # ---- FUNDAMENTALS (F17, richiesta PM 16/07): indice+download degli Excel di valutazione ----
    # C1-v2: il nome CANONICO e' VAL_{TICKER}.xlsx senza timestamp (un modello per stock,
    # la revisione sovrascrive); lo stamp resta accettato per i file legacy in transizione.
    import re as _re_val
    _VAL_NAME = _re_val.compile(r"^(VAL|DCF)_(.+?)(?:_(\d{8}(?:_\d{4})?))?(_FLAGGED)?\.xlsx$")

    def _val_dirs():
        return [str(REPORT_DIR), str(MODELS_DIR)]

    def _known_tickers(db) -> set:
        try:
            with db._conn() as conn:
                a = {r[0].upper() for r in conn.execute("SELECT DISTINCT ticker FROM positions") if r[0]}
                b = {r[0].upper() for r in conn.execute("SELECT DISTINCT ticker FROM trade_history") if r[0]}
            return a | b
        except Exception:
            return set()

    @app.get("/fundamentals/models")
    def list_valuation_models():
        """Canonical analysis identity, shared usability and incomplete research for F17."""
        import hashlib
        import math
        import re
        from bellomberg.core.paths import PROJECT_ROOT as valuation_root
        from bellomberg.valuation.dcf_quality import normalize_valuation_payload
        from bellomberg.valuation.method_registry import method_presentation
        db = get_db()
        known = _known_tickers(db)
        notices = []
        try:
            snapshots = db.get_latest_valuation_snapshots()
        except Exception as exc:
            snapshots = {}
            notices.append(_api_text('snapshot valutazioni non disponibili: ', 'Valuation snapshots unavailable: ') + str(exc))

        # A failed/held attempt never displaces the explicitly published head.
        # Legacy installations stay readable, with the missing registry declared.
        from bellomberg.api.valuation_automation_routes import model_catalog_state
        catalog = {}
        automation_status = "available"
        try:
            catalog = model_catalog_state(db.db_path, roots=_val_dirs())
            snapshots = dict(snapshots)
            for ticker, item in catalog.items():
                if item["payload"]:
                    snapshots[ticker] = {"payload": item["payload"], "created_at": item["created_at"]}
        except Exception as exc:
            automation_status = "unavailable"
            notices.append(_api_text("Registro versioni correnti non disponibile: ",
                                     "Current version registry unavailable: ") + str(exc))

        def generation_key(payload):
            if not all(isinstance(payload.get(key), str) and payload[key]
                       for key in ("snapshot_id", "generation_id")):
                return None
            return (str(payload.get("ticker") or "").upper(), payload["snapshot_id"], payload["generation_id"])

        def artifact_path(path):
            if not isinstance(path, str) or not path.strip():
                return None
            absolute = path if os.path.isabs(path) else os.path.join(str(valuation_root), path)
            return os.path.normcase(os.path.realpath(absolute))

        # A DB copy of a result is not an escape from a failed artifact check.
        # An explicit stored path also identifies its generation when the sidecar
        # is absent/corrupt; unrelated generations without a workbook stay separate.
        snapshots_by_path, artifact_failures = {}, {}
        for snapshot in snapshots.values():
            payload = snapshot["payload"]
            key, path = generation_key(payload), artifact_path(payload.get("path"))
            if key and path:
                snapshots_by_path.setdefault(path, set()).add(key)

        def block_artifact(payload, reasons):
            sanity = payload.get("sanity") if isinstance(payload.get("sanity"), dict) else {}
            warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
            return {**payload, "sanity": {**sanity, "severity": "BLOCK", "headline": "; ".join(reasons)},
                    "warnings": list(dict.fromkeys(str(warning) for warning in warnings + reasons))}

        def model_fields(payload, expected_decision=None):
            from bellomberg.valuation.market_quote import market_quote_view
            detail = normalize_valuation_payload(payload, expected_decision=expected_decision)
            usable = detail["valuation_usability"]["usable"]
            fv = next((detail[key] for key in ("fair_value_final", "fair_value_weighted", "fair_value_blend",
                       "fair_value_base", "fair_value_nav", "fair_value") if detail.get(key) is not None), None) if usable else None
            price = detail.get("price")
            upside = detail.get("upside_pct") if usable else None
            if (upside is None and fv is not None and isinstance(price, (int, float))
                    and not isinstance(price, bool) and math.isfinite(price) and price > 0):
                upside = round((fv / price - 1) * 100, 1)
            # Full acquisition evidence remains in immutable storage, not every table row.
            quote = market_quote_view(detail.get("market_quote"), usable=usable)
            detail.pop("acquisition_snapshot", None)
            sanity = detail.get("sanity") if isinstance(detail.get("sanity"), dict) else {}
            return {"detail": detail, "fair_value": fv, "price_at_thesis": price, "upside_pct": upside,
                    "price_model_as_of": detail.get("valuation_date"), "market_quote": quote,
                    "upside_today_pct": quote.get("upside_base_pct"),
                    "presentation": method_presentation(detail.get("valuation_decision")),
                    "valuation_decision": detail.get("valuation_decision"),
                    "valuation_usability": detail["valuation_usability"],
                    "analytical_quality": detail.get("analytical_quality"),
                    "acquisition_tasks": detail.get("acquisition_tasks", []),
                    "snapshot_id": detail.get("snapshot_id"), "generation_id": detail.get("generation_id"),
                    "sanity_severity": sanity.get("severity"), "sanity_headline": sanity.get("headline")}
        # basename -> memo_id (dai dcf_files dei memo: lega il file alla run che l'ha creato)
        memo_of = {}
        try:
            with db._conn() as conn:
                for mid, dj in conn.execute("SELECT id, dcf_files FROM memos WHERE dcf_files IS NOT NULL"):
                    try:
                        for p in json.loads(dj) or []:
                            memo_of[os.path.basename(str(p))] = mid
                    except Exception:
                        continue
        except Exception:
            pass
        # ultima tesi di valutazione per ticker (fair value, prezzo alla tesi, motivo
        # revisione): e' cio' che la pagina mostra accanto al modello canonico
        theses = {}
        try:
            import sqlite3  # locale: il modulo non e' importato a livello file
            with db._conn() as conn:
                # audit/12 V0.4: anche severity/headline della sanity (colonne nuove);
                # su DB non ancora migrato si ripiega DICHIARANDO, senza rompere la pagina
                _q_base = ("SELECT t.ticker, t.date, t.price_at_thesis, t.fair_value, t.variant_view, t.engine%s "
                           "FROM valuation_theses t JOIN (SELECT ticker, MAX(id) AS mid FROM valuation_theses "
                           "GROUP BY ticker) m ON t.ticker = m.ticker AND t.id = m.mid")
                try:
                    rows = list(conn.execute(_q_base % ", t.sanity_severity, t.sanity_headline"))
                    has_sanity = True
                except sqlite3.OperationalError:
                    rows = list(conn.execute(_q_base % ""))
                    has_sanity = False
                for r in rows:
                    theses[str(r[0]).upper()] = {
                        "thesis_date": r[1], "price_at_thesis": r[2], "fair_value": r[3],
                        "variant_view": ((str(r[4])[:240] + ("..." if len(str(r[4])) > 240 else ""))
                                         if r[4] else None), "engine": r[5],
                        "sanity_severity": (r[6] if has_sanity else None),
                        "sanity_headline": (r[7] if has_sanity else None)}
        except Exception:
            pass
        models, missing_dirs, seen_generations = [], [], set()
        for d in _val_dirs():
            if not os.path.isdir(d):
                missing_dirs.append(os.path.basename(d))
                continue
            for fn in os.listdir(d):
                if not fn.lower().endswith(".xlsx"):
                    continue
                m = _VAL_NAME.match(fn)
                if m:
                    engine, nome, stamp, flagged = m.group(1), m.group(2), m.group(3), bool(m.group(4))
                    if stamp and len(stamp) >= 13:
                        gen = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}"
                    elif stamp:
                        gen = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}"
                    else:
                        # canonico senza timestamp: la data di revisione e' l'mtime del file
                        try:
                            gen = datetime.fromtimestamp(os.path.getmtime(os.path.join(d, fn))).strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            gen = None
                    canonical = stamp is None
                else:
                    # file legacy fuori pattern (es. nomi estesi con spazi): dichiarato, non scartato
                    engine, nome, flagged, canonical = "?", os.path.splitext(fn)[0], "_FLAGGED" in fn, False
                    try:
                        gen = datetime.fromtimestamp(os.path.getmtime(os.path.join(d, fn))).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        gen = None
                cand = nome.upper()
                ticker, matched = None, False
                for c in (cand, cand.replace("_", ".")):
                    if c in known:
                        ticker, matched = c, True
                        break
                tk_label = ticker or cand
                # F17 opzione B (PM 17/07): dettaglio dal sidecar VAL_X.payload.json
                # scritto da generate_valuation — metodi/peer/IRR per il pannello destro.
                # Assente = detail None DICHIARATO (arriva alla prossima rigenerazione).
                detail = None
                identity_status = "legacy_unverified"
                workbook_verified = False
                try:
                    _sc_path = os.path.join(d, os.path.splitext(fn)[0] + ".payload.json")
                    if os.path.exists(_sc_path):
                        with open(_sc_path, "r", encoding="utf-8") as _scf:
                            detail = json.load(_scf)
                        if not isinstance(detail, dict):
                            raise ValueError(_api_text("sidecar non e' un oggetto", 'Sidecar is not an object'))
                        canonical_ticker = detail.get("ticker")
                        if isinstance(canonical_ticker, str) and re.fullmatch(r"[A-Za-z0-9^][A-Za-z0-9.^=_/-]*", canonical_ticker.strip()):
                            tk_label = canonical_ticker.strip().upper()
                            matched = tk_label in known
                            identity_status = "canonical"
                        with open(os.path.join(d, fn), "rb") as workbook:
                            actual_hash = hashlib.sha256(workbook.read()).hexdigest()
                        errors = []
                        expected_names = {tk_label, tk_label.replace(".", "_")}
                        generation = detail.get("generation_id")
                        from bellomberg.valuation.method_registry import is_record_method
                        if (is_record_method(detail.get('valuation_decision')) and isinstance(generation, str)
                                and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", generation)):
                            expected_names.add((re.sub(r"[^A-Za-z0-9_-]", "_", tk_label) + "_" + generation).upper())
                        if identity_status != "canonical":
                            errors.append(_api_text("Identita' canonica assente: file legacy non verificato.", 'Canonical identity missing: unverified legacy file.'))
                        elif nome.upper() not in expected_names:
                            errors.append(_api_text("Identita' del sidecar discordante dal nome del modello.", 'Sidecar identity does not match the model filename.'))
                        if detail.get("workbook_sha256") != actual_hash:
                            errors.append(_api_text('Generazione Excel/sidecar non verificata: impronta assente o discordante.', 'Excel/sidecar generation unverified: fingerprint missing or mismatched.'))
                        workbook_verified = not errors
                        if errors or flagged:
                            detail["sanity"] = {**(detail.get("sanity") or {}), "severity": "BLOCK",
                                                "headline": "; ".join(errors) or "File FLAGGED"}
                            detail["warnings"] = list(detail.get("warnings") or []) + errors
                    else:
                        detail = {"ticker": tk_label, "warnings": [_api_text('sidecar assente: generazione non verificabile', 'Sidecar missing: generation cannot be verified')],
                                  "sanity": {"severity": "BLOCK"}}
                except Exception as exc:
                    detail = {"ticker": tk_label, "warnings": [_api_text('sidecar non leggibile: ', 'Unreadable sidecar: ') + str(exc)],
                              "sanity": {"severity": "BLOCK"}}
                th = theses.get(tk_label.upper()) or {}
                latest = snapshots.get(tk_label.upper())
                expected = None
                current_generation = workbook_verified
                if latest:
                    latest_payload = latest["payload"]
                    latest_decision = latest_payload.get("valuation_decision")
                    expected = {**(latest_decision if isinstance(latest_decision, dict) else {}),
                                "snapshot_id": latest_payload.get("snapshot_id"),
                                "generation_id": latest_payload.get("generation_id"),
                                "ticker": latest_payload.get("ticker")}
                    current_generation = (workbook_verified
                        and (detail or {}).get("snapshot_id") == expected["snapshot_id"]
                        and (detail or {}).get("generation_id") == expected["generation_id"]
                        and (detail or {}).get("valuation_decision") == latest_decision)
                    if current_generation:
                        gen = latest["created_at"].replace("T", " ")
                fields = model_fields(detail or {"ticker": tk_label}, expected_decision=expected)
                if not fields["valuation_usability"]["usable"]:
                    affected = set(snapshots_by_path.get(artifact_path(os.path.join(d, fn)), set()))
                    key = generation_key(fields["detail"])
                    if key:
                        affected.add(key)
                    warnings = fields["detail"].get("warnings")
                    issues = (warnings if isinstance(warnings, list) else []) + fields["valuation_usability"]["reasons"]
                    reason = _api_text('Artefatto {filename} non verificabile: {issues}', 'Artifact {filename} cannot be verified: {issues}').format(filename=fn, issues="; ".join(str(issue) for issue in issues))
                    for key in affected:
                        artifact_failures.setdefault(key, []).append(reason)
                if current_generation:
                    seen_generations.add((fields["snapshot_id"], fields["generation_id"]))
                models.append({
                    "file": fn, "dir": os.path.basename(d), "engine": engine,
                    "_artifact_path": artifact_path(os.path.join(d, fn)),
                    "ticker": tk_label,
                    "identity_status": identity_status,
                    "matched": matched,     # False = nome file non riconducibile a un titolo del book
                    # F17 already sorts canonical before mtime: a touched old workbook
                    # cannot displace the latest immutable analysis, including incomplete.
                    "canonical": canonical and current_generation,
                    "current_generation": current_generation,
                    "generated_at": gen, "flagged": flagged,
                    "memo_id": memo_of.get(fn),
                    "thesis_date": th.get("thesis_date"),
                    "variant_view": (detail or {}).get("variant_view") or th.get("variant_view"),
                    **fields,
                })
        for ticker, snapshot in snapshots.items():
            payload = snapshot["payload"]
            failures = artifact_failures.get(generation_key(payload), [])
            fields = model_fields(block_artifact(payload, failures) if failures else payload)
            if (fields["snapshot_id"], fields["generation_id"]) in seen_generations:
                continue
            models.append({"file": "", "dir": "snapshot", "engine": fields["detail"].get("engine") or "n.d.",
                           "ticker": ticker, "identity_status": "canonical", "matched": ticker in known,
                           "canonical": True, "current_generation": True,
                           "generated_at": snapshot["created_at"].replace("T", " "),
                           "flagged": fields["sanity_severity"] == "BLOCK", **fields})
        represented = {model["ticker"] for model in models}
        for ticker, item in catalog.items():
            job = item["state"].get("latest_prepare_job")
            if ticker in represented or not job:
                continue
            reason = _api_text("Modello non disponibile; preparazione: ",
                               "Model unavailable; preparation: ") + str(job.get("reason") or job["status"])
            fields = model_fields({"ticker": ticker, "status": "incomplete", "warnings": [reason],
                                   "sanity": {"severity": "BLOCK", "headline": reason}})
            models.append({"file": "", "dir": "queue", "engine": "n.d.", "ticker": ticker,
                           "identity_status": "canonical", "matched": ticker in known,
                           "canonical": False, "current_generation": False,
                           "generated_at": None, "flagged": True, **fields})
        # Apply the same KO to any duplicate file rows of this generation as well.
        for model in models:
            failures = artifact_failures.get(generation_key(model["detail"]), [])
            if failures:
                model.update(model_fields(block_artifact(model["detail"], failures)))
                model["flagged"] = True
            item = catalog.get(model["ticker"])
            if not item:
                item = {"payload": None, "state": {"ticker": model["ticker"],
                    "status": "no_current" if automation_status == "available" else "unavailable",
                    "current": None, "locked": None, "artifact": {"available": False}}}
            if item:
                state = item["state"]
                current = state["current"]
                matches = bool(current and model.get("generation_id") == current["generation_id"]
                               and model.get("snapshot_id") == current["snapshot_id"]
                               and (not model["file"] or model.get("_artifact_path") == artifact_path(item["payload"].get("path"))))
                model["current_generation"] = matches
                if automation_status == "available":
                    model["canonical"] = matches
                model["automation"] = state
                if matches:
                    # Current figures and prose come from immutable storage, never a copied sidecar.
                    model.update(model_fields(item["payload"]))
                    model["flagged"] = model["sanity_severity"] == "BLOCK"
                    thesis = item.get("thesis") or {}
                    model["thesis_date"] = thesis.get("date")
                    model["variant_view"] = item["payload"].get("variant_view") or thesis.get("variant_view")
                    price_job = state.get("latest_price_job")
                    if price_job:
                        from bellomberg.valuation.market_quote import market_quote_view, CONTRACT, FRESHNESS_POLICY
                        quote = price_job.get("market_quote") or {"contract": CONTRACT,
                            "freshness_policy": FRESHNESS_POLICY, "status": "source_unavailable",
                            "message": str(price_job.get("reason") or price_job["status"])}
                        model["market_quote"] = market_quote_view(quote, usable=model["valuation_usability"]["usable"])
                        model["upside_today_pct"] = model["market_quote"].get("upside_base_pct")
                        model["detail"]["market_quote"] = model["market_quote"]
                elif automation_status == "available":
                    reason = _api_text("Revisione non pubblicata come corrente; candidato o copia conservati.",
                                       "Revision not published as current; candidate or copy preserved.")
                    model.update(model_fields(block_artifact(model["detail"], [reason])))
                    model["flagged"] = True
                if matches and not state["artifact"]["available"]:
                    model.update(model_fields(block_artifact(model["detail"], [state["artifact"]["reason"]])))
                    model["flagged"] = True
                if matches and state["artifact"]["available"]:
                    model["current_download"] = ("/valuation/models/" + model["ticker"] + "/generations/"
                                                 + model["generation_id"] + "/workbook")
            model["historical_download"] = bool(model.get("file") and
                "preparation" not in (model.get("detail") or {}) and not re.search(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", model["file"], re.I))
            model.pop("_artifact_path", None)
        models.sort(key=lambda x: (x.get("current_generation"), x.get("canonical"),
                                   x.get("generated_at") or ""), reverse=True)
        out = {"count": len(models), "models": models, "automation_status": automation_status}
        if missing_dirs:
            notices.append(_api_text("cartelle runtime assenti (dichiarato, non e' 'zero file'): ", "Missing runtime directories (disclosed; this does not mean 'zero files'): ") + ", ".join(missing_dirs))
        if notices:
            out["nota"] = "; ".join(notices)
        return out

    @app.get("/fundamentals/models/{name}/download", dependencies=[Depends(require_session)])
    def download_valuation_model(name: str):
        """Dated legacy copy only; versioned workbooks use the verified registry route."""
        from hashlib import sha256
        from pathlib import Path
        from fastapi import Response
        from urllib.parse import quote
        # review 16/07: anche ':' bloccato — 'C:file.xlsx' e' drive-relative e os.path.join
        # lo risolverebbe FUORI dalle cartelle whitelisted.
        if os.sep in name or "/" in name or ".." in name or ":" in name or not name.lower().endswith(".xlsx"):
            raise HTTPException(400, _api_text('nome file non valido', 'Invalid file name'))
        for d in _val_dirs():
            p = os.path.join(d, name)
            if os.path.isfile(p):
                path = Path(p).resolve()
                if not path.is_relative_to(Path(d).resolve()):
                    raise HTTPException(400, "Workbook outside trusted directory")
                sidecar = path.with_suffix(".payload.json")
                try:
                    payload = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
                    if not isinstance(payload, dict):
                        raise ValueError("invalid workbook metadata")
                    versioned = ("preparation" in payload or re.search(
                        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", name, re.I))
                    if versioned:
                        raise HTTPException(409, "Versioned workbook requires the verified current-generation route")
                    contents = path.read_bytes()
                    digest = payload.get("workbook_sha256")
                    if digest and sha256(contents).hexdigest() != digest:
                        raise ValueError("workbook differs from recorded hash")
                except (OSError, ValueError) as exc:
                    raise HTTPException(409, "Legacy workbook unavailable: " + str(exc)) from exc
                return Response(contents, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Cache-Control": "no-store", "X-Valuation-Copy": "historical",
                             "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})
        raise HTTPException(404, _api_text(f"file '{name}' non trovato in report/ o models/", f"File '{name}' not found in report/ or models/"))

    @app.get("/memos/{memo_id}")
    def get_memo(memo_id: int):
        db = get_db()
        with db._conn() as conn:
            row = conn.execute("SELECT * FROM memos WHERE id=?", (memo_id,)).fetchone()
            if not row:
                raise HTTPException(404, _api_text(f'Memo {memo_id} non trovato', f'Memo {memo_id} not found'))
            return dict(row)

    @app.get("/memos/search/{query}")
    def search_memos(query: str, n: int = 5):
        db = get_db()
        return {"query": query, "results": db.search_memos_semantic(query, n_results=n)}

    @app.get("/decisions")
    def list_decisions(status: Optional[str] = None, limit: int = 30):
        db = get_db()
        # 16/07 (review M3): l'auto-archivio (>7g) girava SOLO a inizio run del
        # consigliere — e il task e' disabilitato dal 03/07: "dopo una settimana"
        # sarebbe stato "mai". Agganciato anche qui: idempotente, best-effort.
        try:
            db.auto_expire_stale_decisions(days=7)
        except Exception:
            pass
        rows = db.get_recent_decisions(n=limit, status_filter=status.upper() if status else None)
        # F10 v3 (mockup A approvato dal PM): ogni riga porta 'archived' calcolato —
        # OPERATIVE: in archivio dopo 7 giorni QUALUNQUE sia l'esito (le PENDING >7g
        # sono gia' EXPIRED dall'auto-archivio qui sopra); RESEARCH: in archivio quando
        # non e' piu' PENDING (bottone PM, promozione della run o auto-30g). Tutto
        # resta nel DB: 'archived' e' solo il gruppo di visualizzazione.
        # Le RESEARCH portano anche il thread note PM<->AI (F10 v3 c).
        from datetime import datetime as _dt, timedelta as _td
        _cutoff7 = (_dt.now() - _td(days=7)).isoformat(timespec="seconds")
        for d in rows:
            _is_res = str(d.get("action") or "").upper() == "RESEARCH"
            if _is_res:
                d["archived"] = str(d.get("status") or "").upper() != "PENDING"
                try:
                    d["notes"] = db.get_decision_notes(d["id"])
                    d["notes_status"] = "available"
                    d["notes_error"] = None
                except Exception:
                    d["notes"] = []
                    d["notes_status"] = "unavailable"
                    d["notes_error"] = "decision_notes_unavailable"
            else:
                d["archived"] = str(d.get("timestamp") or "9999") < _cutoff7
            # F10-C (PM 17/07): l'override MANUALE vince sempre sull'automatico —
            # "dall'archivio posso riportarle in pagina di mia spontanea volonta'"
            if d.get("archive_override") is not None:
                d["archived"] = bool(d["archive_override"])
        executions = db.esecuzioni_delle_decisioni(rows)
        # Optional until migration; unreadable provenance must not hide a block.
        with db._conn() as conn:
            has_trade_ideas = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_idea_runs'"
            ).fetchone() is not None
        provenance = {}
        if has_trade_ideas:
            from bellomberg.storage.trade_idea_store import TradeIdeaStore
            idea_store = TradeIdeaStore(db.db_path)
            for offset in range(0, len(rows), 1000):
                provenance.update(idea_store.lookup_decisions(
                    [row["id"] for row in rows[offset:offset + 1000]]))
        # 04/10 (W2, Opus 5.5): voci RESEARCH nate da un trigger watch Trade Idea
        # (origin 'trade_idea_trigger'), unite nella stessa chiave 'trade_idea'. Schema
        # watch assente o illeggibile = provenienza trigger n.d. DICHIARATA, mai 500.
        watch_error = None
        try:
            from bellomberg.storage.trade_idea_watch_store import TradeIdeaWatchStore
            watch_store = TradeIdeaWatchStore(db.db_path)
            for offset in range(0, len(rows), 1000):
                for _id, _item in watch_store.lookup_decisions(
                        [row["id"] for row in rows[offset:offset + 1000]]).items():
                    if _id in provenance:   # due origini per una voce: non si sceglie in silenzio
                        watch_error = _api_text('provenienza doppia per la voce #%s: tenuta quella Trade Idea', 'Duplicate provenance for entry #%s: Trade Idea one kept') % _id
                    else:
                        provenance[_id] = _item
        except Exception as e:
            # il solo testo dichiarato dallo store (SCHEMA_MISSING); altro = solo il tipo
            _det = str(e) if isinstance(e, RuntimeError) and str(e).startswith("schema watch assente") else type(e).__name__
            watch_error = _api_text('provenienza trigger n.d.: %s', 'Trigger provenance n/a: %s') % _det
        for d in rows:
            d["esecuzione"] = executions.get(d["id"])
            d["trade_idea"] = provenance.get(d["id"])
            d["manual_divergences"] = db.get_manual_trade_divergences(d["id"])
        return {"decisions": rows, "watch_provenance_error": watch_error}

    @app.get("/decisions/{decision_id}/events")
    def decision_events(decision_id: int):
        db = get_db()
        with db._conn() as conn:
            if not conn.execute("SELECT 1 FROM decisions WHERE id=?", (decision_id,)).fetchone():
                raise HTTPException(404, _api_text(f'Decisione {decision_id} non trovata', f'Decision {decision_id} not found'))
            events = [dict(row) for row in conn.execute(
                "SELECT * FROM decision_events WHERE decision_id=? ORDER BY id", (decision_id,))]
        return {"events": events}

    # POST /instrument-identities/verify RIMOSSO (REV_G9a R2, decisione del
    # coordinatore): scriveva una riga append-only fuori da ogni trade e da quel
    # momento abilitava legami cross-ticker senza riconferma del PM. La verifica
    # ISIN viaggia ora solo col trade (TradeIn.instrument_identity) e si scrive
    # nella sua transazione, dopo la conferma.

    @app.post("/decisions/{decision_id}/manual-divergence", dependencies=[Depends(require_session)])
    def record_manual_trade_divergence(decision_id: int, body: ManualTradeDivergenceIn):
        db = get_db()
        try:
            event = db.record_manual_trade_divergence(
                decision_id, body.trade_id, body.reason, actor="PM")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True, "event": event}

    @app.post("/decisions/{decision_id}/archive", dependencies=[Depends(require_session)])
    def set_decision_archive(decision_id: int, body: dict):
        """F10-C: archivia (true) / riporta in pagina (false) / torna all'automatico
        (null). Tocca SOLO la vista archivio: status e storia restano intatti."""
        b = body or {}
        if "archived" not in b:
            raise HTTPException(400, _api_text("campo 'archived' mancante (true/false/null)", "Missing 'archived' field (true/false/null)"))
        val = b.get("archived")
        if val is not None and not isinstance(val, bool):
            raise HTTPException(400, _api_text("'archived' deve essere true, false o null", "'archived' must be true, false or null"))
        db = get_db()
        try:
            ok = db.set_decision_archive(decision_id, val)
        except Exception as _ae:
            # review 17/07 F5: colonna non migrata (ALTER fallito a DB lockato) — 503
            # DICHIARATO invece del 500 nudo
            raise HTTPException(503, _api_text('archive_override non disponibile (%s): riavviare il backend a DB libero per completare la migrazione', 'archive_override unavailable (%s): restart the backend with the database idle to complete migration')
                                     % type(_ae).__name__)
        if not ok:
            raise HTTPException(404, _api_text(f'decisione {decision_id} inesistente', f'Decision {decision_id} not found'))
        return {"ok": True, "decision_id": decision_id, "archive_override": val}

    @app.post("/decisions/{decision_id}/veto", dependencies=[Depends(require_session)])
    def set_decision_veto(decision_id: int, body: dict):
        """F10 opzione A (PM 22/07): VETO ETERNO — motivo OBBLIGATORIO. Imposta
        SKIPPED + flag veto; resta nel canale che la run legge per sempre, finche'
        non viene revocato dal bottone REVOCA."""
        reason = str((body or {}).get("reason") or "").strip()
        if not reason:
            raise HTTPException(400, _api_text('motivo obbligatorio per il veto', 'A reason is required for a veto'))
        db = get_db()
        try:
            row = db.set_decision_veto(decision_id, reason)
        except Exception as _ve:
            # review 23/07 (finding MEDIA-5): 503 SOLO per colonna mancante; qualsiasi
            # altro errore (locked/disk) propaga col messaggio VERO, mai etichettato
            # "migrazione mancante" a caso
            if "no such column" in str(_ve).lower():
                raise HTTPException(503, _api_text('colonna veto non disponibile: riavviare il backend a DB libero per completare la migrazione 2', 'Veto column unavailable: restart the backend with the database idle to complete migration 2'))
            raise
        if not row:
            raise HTTPException(404, _api_text(f'decisione {decision_id} inesistente', f'Decision {decision_id} not found'))
        return {"ok": True, "decision": row}

    @app.post("/decisions/{decision_id}/veto/revoke", dependencies=[Depends(require_session)])
    def revoke_decision_veto(decision_id: int):
        """Revoca del veto (la UI chiede conferma prima di chiamare): flag spento,
        motivo e date restano in storia."""
        db = get_db()
        try:
            row = db.revoke_decision_veto(decision_id)
        except Exception as _ve:
            if "no such column" in str(_ve).lower():
                raise HTTPException(503, _api_text('colonna veto non disponibile: riavviare il backend a DB libero per completare la migrazione 2', 'Veto column unavailable: restart the backend with the database idle to complete migration 2'))
            raise
        if not row:
            raise HTTPException(404, _api_text(f'decisione {decision_id} inesistente o senza veto attivo', f'Decision {decision_id} not found or without an active veto'))
        return {"ok": True, "decision": row}

    @app.post("/decisions/{decision_id}/note", dependencies=[Depends(require_session)])
    def add_decision_note(decision_id: int, body: dict):
        """F10 v3: nota del PM sul filo di una decisione RESEARCH — la run la legge
        (blocco research) e risponde con add_research_note."""
        testo = str((body or {}).get("testo") or "").strip()
        if not testo:
            raise HTTPException(400, _api_text('testo mancante', 'Missing text'))
        db = get_db()
        note_id = db.add_decision_note(decision_id, "PM", testo)
        if not note_id:
            raise HTTPException(400, _api_text(f'nota rifiutata: decisione {decision_id} inesistente, non-RESEARCH o tabella non migrata (tools/migrations/migra_decision_notes.py)', f'Note rejected: decision {decision_id} missing, not RESEARCH, or table not migrated (tools/migrations/migra_decision_notes.py)'))
        return {"ok": True, "note_id": note_id}

    @app.post("/decisions/{decision_id}/update", dependencies=[Depends(require_session)])
    def update_decision(decision_id: int, body: DecisionUpdateIn):
        db = get_db()
        kwargs = body.dict(exclude_none=True)
        if not kwargs:
            raise HTTPException(400, _api_text('Nessun campo da aggiornare', 'Nothing to update'))
        try:
            ok = db.update_decision(decision_id, **kwargs)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if not ok:
            raise HTTPException(404, _api_text(f'Decisione {decision_id} non trovata o nessuna modifica', f'Decision {decision_id} not found or no changes'))
        return {"ok": True, "decision_id": decision_id}

    @app.get("/trades")
    def list_trades(limit: int = 60):
        """Storico movimenti del PM (BUY/SELL/TRIM/ADD/DIVIDEND) con note e rationale."""
        db = get_db()
        return {"trades": db.get_recent_trades(n=limit)}

    def _trade_request(body):
        request = body.model_dump(exclude={"preview_id"})
        for key in ("ticker", "action", "valuta"):
            request[key] = request[key].strip().upper()
        return request

    def _trade_fx(day, currency):
        """Historical observations never use a date after the operation."""
        from datetime import date, timedelta
        from decimal import Decimal
        from bellomberg.cli.price_updater import get_fx_to_eur_con_fonte
        historical = day < date.today().isoformat()
        missing = None
        if historical and currency != "EUR":
            try:
                from bellomberg.portfolio import portfolio_analytics as pa
                when = date.fromisoformat(day)
                series = pa._build_fx_history([currency], (when - timedelta(days=7)).isoformat(),
                                              (when + timedelta(days=1)).isoformat())
                if series is not None and not series.empty:
                    series = series[series.index <= pa.pd.Timestamp(day)]
                rate, observed, note = pa._fx_lookup_storico(series)(day, currency)
                if rate is not None and Decimal(str(rate)).is_finite() and rate > 0:
                    return {"tasso": rate, "fonte": "storico", "data": observed, "nota": note}
                missing = note or "serie storica senza un cambio valido"
            except Exception as exc:
                missing = _api_text(f'serie storica non disponibile ({type(exc).__name__})', f'Historical series unavailable ({type(exc).__name__})')
        rate, source = get_fx_to_eur_con_fonte(currency)
        if rate is None:
            raise HTTPException(503, _api_text(f'FX {currency}->EUR non disponibile', f'FX {currency}->EUR unavailable'))
        if source not in ("live", "identity"):
            raise HTTPException(503, _api_text(f'FX {currency}->EUR non utilizzabile per la cassa: fonte {source}', f'FX {currency}->EUR unavailable for cash: source {source}'))
        number = Decimal(str(rate))
        if not number.is_finite() or number <= 0:
            raise HTTPException(422, _api_text(f'FX {currency}->EUR non positivo o non finito', f'FX {currency}->EUR not positive or not finite'))
        return {"tasso": float(number), "fonte": "identity" if currency == "EUR" else "corrente",
                "data": date.today().isoformat(),
                "nota": (_api_text(f'FX storico {currency} del {day} n.d.: {missing}; usato il cambio corrente per il saldo corrente.', f'Historical FX {currency} for {day} unavailable: {missing}; current FX is used for the current cash balance.') if missing else None)}

    def _prepare_trade(body: TradeIn):
        db = get_db()
        from bellomberg.storage.memory_db import normalizza_data_trade, CashNotInitialized
        from datetime import date
        trade_date, conventional = normalizza_data_trade(body.data)
        historical = trade_date[:10] < date.today().isoformat()
        action = body.action.strip().upper()
        ticker = body.ticker.upper().strip()
        if not ticker:
            raise ValueError(_api_text('ticker vuoto', 'Empty ticker'))
        if action not in ("BUY", "SELL", "ADD", "TRIM", "DIVIDEND"):
            raise ValueError(_api_text('action deve essere BUY/SELL/TRIM/ADD/DIVIDEND', 'action must be BUY/SELL/TRIM/ADD/DIVIDEND'))
        if body.senza_decisione and body.linked_decision_id is not None:
            raise ValueError(_api_text('decisione esplicita e senza decisione insieme: scegli un solo legame', 'Explicit decision and no decision conflict: choose only one link'))
        origin = "explicit" if body.linked_decision_id is not None else "none" if body.senza_decisione else "unknown"
        trade = {"ticker": ticker, "action": action, "quantita": body.quantita, "prezzo": body.prezzo,
                 "valuta": body.valuta.strip().upper(), "note": body.note, "pm_rationale": body.pm_rationale,
                 "linked_decision_id": body.linked_decision_id, "data": trade_date,
                 "ora_convenzionale": conventional, "link_origin": origin}
        # Verifica d'identita' e divergenza: qui SOLO lettura e validazione.
        # Si scrivono in execute_trade, nella transazione del trade confermato.
        if body.manual_divergence is not None and origin != "none":
            raise ValueError(_api_text('la divergenza manuale richiede un trade senza decisione collegata', 'A manual divergence requires a trade without a linked decision'))
        identity_target = None
        target_id = (body.linked_decision_id if body.linked_decision_id is not None
                     else body.manual_divergence.decision_id if body.manual_divergence is not None else None)
        if target_id is not None:
            with db._conn() as conn:
                target = conn.execute("SELECT ticker, proposal_ticker FROM decisions WHERE id=?",
                                      (target_id,)).fetchone()
            if target is not None:
                identity_target = (target["ticker"] if body.linked_decision_id is not None
                                   else target["proposal_ticker"] or target["ticker"])
                identity_target = str(identity_target or "").strip().upper() or None
        pending_identity = None
        if body.instrument_identity is not None:
            if identity_target is None or identity_target == ticker:
                raise ValueError(_api_text('verifica ISIN non richiesta: il ticker eseguito coincide con quello della proposta o manca la proposta', 'ISIN verification not required: the executed ticker matches the proposal or there is no proposal'))
            pending_identity = db._normalized_identity(
                identity_target, ticker, body.instrument_identity.isin, body.instrument_identity.source,
                body.instrument_identity.verified_at, body.instrument_identity.reason, verified_by="PM")
        context = db.trade_context(ticker, body.linked_decision_id, pending_identity=pending_identity)
        db._assert_trade_after_opening(trade, context.get("opening"))
        retroactive = any(r["data"] > trade_date for r in context["trades"])
        if context["cash"] is None:
            raise CashNotInitialized(_api_text('saldo cassa non inizializzato: registra prima il saldo iniziale', 'Cash balance not initialized: register the opening cash balance first'))
        decision = db._validate_trade_decision(trade, context)
        divergence, divergence_view = None, None
        if body.manual_divergence is not None:
            reason = (body.manual_divergence.reason or "").strip()
            if not reason:
                raise ValueError(_api_text('il motivo della divergenza manuale è obbligatorio', 'The manual divergence reason is required'))
            with db._conn() as conn:
                details = db.manual_divergence_details(conn, body.manual_divergence.decision_id,
                                                       trade, pending_identity)
            divergence = {"decision_id": body.manual_divergence.decision_id, "reason": reason}
            divergence_view = {**divergence, **details}
        # Validazione ticker (bugfix #164, trade fantasma PSHP):
        # SELL/TRIM/ADD/DIVIDEND richiedono una posizione attiva esistente.
        # BUY resta libero (apre nuove posizioni). Dal 01/08 la lettura serve a
        # TUTTE le azioni (guardia prezzi: valuta di quotazione + ultimo prezzo).
        pos_row, ultimo_prezzo = None, None
        try:
            with db._conn() as conn:
                pos_row = conn.execute(
                    "SELECT quantita, valuta FROM positions "
                    "WHERE UPPER(ticker)=? AND is_active=1 AND quantita > 0",
                    (ticker,),
                ).fetchone()
                _lp = conn.execute(
                    "SELECT prezzo, timestamp FROM position_prices WHERE UPPER(ticker)=? "
                    + ("AND substr(timestamp,1,10)=? " if historical else "")
                    + "ORDER BY timestamp DESC LIMIT 1",
                    (ticker, trade_date[:10]) if historical else (ticker,)).fetchone()
                ultimo_prezzo = _lp[0] if _lp else None
                ultimo_prezzo_ts = _lp[1] if _lp else None
        except Exception as _pe:
            # audit/11 §4: fail-CLOSED — col vecchio fail-open un DB occupato faceva
            # passare SELL su ticker inesistenti o oltre il posseduto senza check.
            # Dal 01/08 vale anche per BUY: senza valuta/ultimo prezzo la guardia
            # sarebbe cieca (e log_trade fallirebbe comunque sul DB occupato).
            raise HTTPException(503, _api_text(f'verifica posizione non riuscita (DB occupato): {action} non eseguito, riprova. ({_pe})', f'Position check failed (database busy): {action} not executed; try again. ({_pe})'))
        if action in ("SELL", "TRIM", "ADD", "DIVIDEND") and not retroactive:
            if pos_row is None:
                raise HTTPException(
                    400,
                    _api_text(f"Ticker '{ticker}' non presente tra le posizioni attive: {action} rifiutato. Usa BUY per aprire una nuova posizione.", f"Ticker '{ticker}' is not among active positions: {action} rejected. Use BUY to open a new position."),
                )
            held = pos_row[0]
            if action in ("SELL", "TRIM") and held is not None and body.quantita > float(held):
                raise HTTPException(
                    400,
                    _api_text(f'Quantita {body.quantita:g} superiore a quella posseduta ({float(held):g} {ticker}): {action} rifiutato.', f'Quantity {body.quantita:g} exceeds the holding ({float(held):g} {ticker}): {action} rejected.'),
                )
        # GUARDIA PREZZI (specifica decisa dal PM 01/08, §9-quattuortrigies coda §3):
        # valuta discorde -> 422 · oltre x3/:3 dall'ultimo prezzo noto -> 422 col
        # confronto · fra +-30% e x3 -> avviso in risposta (la scrittura passa) ·
        # positivita' server-side. DIVIDEND esente da valuta/scala (dichiarato in
        # guardia_prezzi.py). STA FUORI dal try qui sotto: il suo 422 non deve
        # farsi riscrivere in 400 dall'except generico.
        # Review 01/08: il confronto di scala SOLO con posizione ATTIVA — su una
        # riapertura lo snapshot e' orfano (l'updater gira solo sulle attive) e
        # il rifiuto indicherebbe un rimedio non eseguibile. Non e' un cambio di
        # soglia: e' lo scoping del riferimento a un riferimento valido.
        from bellomberg.cli.guardia_prezzi import controlla_trade
        _g = controlla_trade(prezzo=body.prezzo, quantita=body.quantita,
                             valuta_trade=body.valuta,
                             valuta_posizione=(pos_row[1] if pos_row else None),
                             ultimo_prezzo=(ultimo_prezzo if pos_row else None),
                             azione=action,
                             ultimo_prezzo_data=(ultimo_prezzo_ts if pos_row else None))
        if _g["esito"] == "rifiuto":
            raise HTTPException(422, _api_text('GUARDIA PREZZI: ', 'PRICE GUARD: ') + _g["motivo"])
        guardia_note = _g["motivo"] if _g["esito"] == "avviso" else None
        if historical and ultimo_prezzo is None:
            guardia_note = _api_text('Prezzo storico di confronto n.d.; verificati importo e valuta, confronto di scala non disponibile.', 'Historical comparison price unavailable; amount and currency checked, scale comparison unavailable.')
        try:
            from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
            try:
                fx = _trade_fx(trade_date[:10], trade["valuta"])
                fx_dec = Decimal(str(fx["tasso"]))
                amount = (Decimal(str(body.quantita)) * Decimal(str(body.prezzo))
                          * fx_dec)
                if not amount.is_finite():
                    raise HTTPException(422, _api_text('controvalore del trade non finito', 'Trade amount is not finite'))
                amount_cents = int((amount * 100).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP))
            except (InvalidOperation, ValueError, TypeError) as e:
                raise HTTPException(503, _api_text(f'trade non eseguito: {e}', f'Trade not executed: {e}'))
            rounding_note = (_api_text('controvalore inferiore a mezzo centesimo: delta cassa arrotondato esplicitamente a 0,00 EUR', 'Amount below half a cent: cash delta explicitly rounded to EUR 0.00')
                             if amount_cents == 0 else None)
            delta_cents = (-amount_cents if action in ("BUY", "ADD")
                           else amount_cents)
            trade["fx_fonte"] = fx["fonte"]
            realized_rate = fx["tasso"] if not historical or fx["fonte"] in ("identity", "storico") else None
            trade["_realized_fx"] = realized_rate
            rates = {(trade_date[:10], trade["valuta"]): realized_rate}
            for row in context["trades"]:
                key = (row["data"][:10], row["valuta"])
                if row["action"] in ("SELL", "TRIM") and row["data"] > trade_date and key not in rates:
                    try:
                        observed = _trade_fx(*key)
                        rates[key] = observed["tasso"] if observed["fonte"] in ("identity", "storico") else None
                    except Exception:
                        rates[key] = None  # disclosed in replay notes, never a numeric proxy
            plan = db._trade_replay_plan(trade, context, rates)
            if plan:
                plan.pop("updates")
            cash_note = (_api_text(f"Registrazione sul saldo corrente con FX {fx['fonte']}; gli snapshot NAV passati non vengono riscritti.", f"Recorded against current cash with FX {fx['fonte']}; past NAV snapshots are not rewritten."))
            response = {"ok": True, "cash_disponibile_eur": (context["cash"]["balance_cents"] + delta_cents) / 100,
                    "cash_delta_eur": delta_cents / 100,
                    "cash_source": "sqlite:cash_state", "cash_note": rounding_note,
                    "guardia_note": guardia_note, "data": trade_date, "ora_convenzionale": bool(conventional),
                    "link_origin": origin, "decisione": decision, "fx": fx, "ricalcolo": plan,
                    "cassa_nota": cash_note, "instrument_identity": pending_identity,
                    "manual_divergence": divergence_view}
            return {"trade": trade, "response": response, "expected_context": context["fingerprint"],
                    "instrument_identity": pending_identity, "manual_divergence": divergence,
                    "cash_delta_cents": delta_cents, "realized_fx": rates, "db_path": os.path.realpath(db.db_path)}
        except HTTPException:
            raise
        except Exception as e:
            _trade_error(e)

    _TRADE_PREVIEW_TTL = 120
    _TRADE_PREVIEWS = {}
    _TRADE_PREVIEW_LOCK = threading.Lock()

    def _db_busy(exc):
        import sqlite3
        text = str(exc).lower()
        return isinstance(exc, sqlite3.OperationalError) and ("locked" in text or "busy" in text)

    def _trade_error(exc):
        from bellomberg.storage.memory_db import CashNotInitialized, RicalcoloImpossibile
        if isinstance(exc, HTTPException):
            raise exc
        if _db_busy(exc):
            # Lock transitorio, nulla scritto (rollback): 503, non un errore del client.
            raise HTTPException(503, _api_text("archivio occupato da un'altra scrittura: nulla registrato, riprova la conferma fra qualche secondo", 'Database busy with another write: nothing recorded, retry the confirmation in a few seconds'))
        raise HTTPException(503 if isinstance(exc, CashNotInitialized) else
                            409 if isinstance(exc, RicalcoloImpossibile) else 400, str(exc))

    @app.post("/trade/preview", dependencies=[Depends(require_session)])
    def preview_trade(body: TradeIn):
        """Read-only preparation: no INSERT/UPDATE/rollback simulation on the ledger."""
        try:
            prepared = _prepare_trade(body)
            token, now = secrets.token_urlsafe(32), time.monotonic()
            with _TRADE_PREVIEW_LOCK:
                expired = [key for key, value in _TRADE_PREVIEWS.items() if value["expires"] <= now]
                for key in expired:
                    del _TRADE_PREVIEWS[key]
                if len(_TRADE_PREVIEWS) >= 256:
                    del _TRADE_PREVIEWS[next(iter(_TRADE_PREVIEWS))]
                _TRADE_PREVIEWS[token] = {"expires": now + _TRADE_PREVIEW_TTL,
                                          "request": _trade_request(body), "prepared": prepared}
            return {**prepared["response"], "preview_id": token, "expires_in_seconds": _TRADE_PREVIEW_TTL}
        except Exception as exc:
            _trade_error(exc)

    @app.post("/trade", dependencies=[Depends(require_session)])
    def log_trade(body: TradeIn):
        try:
            db = get_db()
            if body.preview_id is not None:
                with _TRADE_PREVIEW_LOCK:
                    preview = _TRADE_PREVIEWS.pop(body.preview_id, None)
                if (preview is None or preview["expires"] <= time.monotonic()
                        or preview["request"] != _trade_request(body)):
                    raise HTTPException(409, _api_text('anteprima scaduta, gia usata o corpo modificato: ripeti la conferma', 'Preview expired, already used or request changed: preview and confirm again'))
                prepared = preview["prepared"]
                if prepared["db_path"] != os.path.realpath(db.db_path):
                    raise HTTPException(409, _api_text("archivio cambiato: ripeti l'anteprima", 'Database changed: request a new preview'))
            else:
                prepared = _prepare_trade(body)
            try:
                result = db.execute_trade(cash_delta_cents=prepared["cash_delta_cents"],
                                          expected_context=prepared["expected_context"],
                                          realized_fx=prepared["realized_fx"],
                                          instrument_identity=prepared["instrument_identity"],
                                          manual_divergence=prepared["manual_divergence"],
                                          **prepared["trade"])
            except Exception as exc:
                # DB occupato: rollback, nulla scritto -> l'anteprima resta valida
                # (stessa scadenza) e il PM puo' riconfermare senza rifarla.
                if body.preview_id is not None and _db_busy(exc):
                    with _TRADE_PREVIEW_LOCK:
                        _TRADE_PREVIEWS[body.preview_id] = preview
                raise
            committed_divergence = None
            if result.get("manual_divergence") is not None:
                committed_divergence = {**(prepared["response"].get("manual_divergence") or {}),
                                        "event_id": result["manual_divergence"]["id"]}
            performance_note = None
            try:
                from bellomberg.portfolio import twr_engine
                twr_engine.invalidate_cache()
            except Exception as exc:
                # The ledger is already committed: returning an HTTP failure
                # here would invite a duplicate execution on the next retry.
                performance_note = _api_text(f'Trade registrato; cache performance non aggiornata ({type(exc).__name__}).', f'Trade recorded; performance cache not updated ({type(exc).__name__}).')
            return {**prepared["response"], "trade_id": result["trade_id"],
                    "cash_disponibile_eur": result["cash_eur"], "ricalcolo": result["ricalcolo"],
                    "instrument_identity": result.get("instrument_identity"),
                    "manual_divergence": committed_divergence,
                    "performance_note": performance_note,
                    "valuation_automation": _notify_model_tracking(db.db_path, prepared["trade"]["ticker"], "portfolio")}
        except Exception as exc:
            _trade_error(exc)

    _OPENING_PREVIEWS = {}
    _OPENING_PREVIEW_LOCK = threading.Lock()

    def _opening_request(body):
        request = body.model_dump(exclude={"preview_id"})
        for key in ("ticker", "valuta"):
            request[key] = request[key].strip().upper()
        return request

    @app.post("/positions/opening/preview", dependencies=[Depends(require_session)])
    def preview_opening_position(body: OpeningPositionIn):
        try:
            db = get_db()
            request = _opening_request(body)
            prepared = db.prepare_position_opening(**request)
            token, now = secrets.token_urlsafe(32), time.monotonic()
            with _OPENING_PREVIEW_LOCK:
                for key in list(_OPENING_PREVIEWS):
                    if _OPENING_PREVIEWS[key]["expires"] <= now:
                        del _OPENING_PREVIEWS[key]
                if len(_OPENING_PREVIEWS) >= 256:
                    del _OPENING_PREVIEWS[next(iter(_OPENING_PREVIEWS))]
                _OPENING_PREVIEWS[token] = {
                    "request": request, "prepared": prepared,
                    "db_path": os.path.realpath(db.db_path), "expires": now + _TRADE_PREVIEW_TTL}
            return {"ok": True, **{k: v for k, v in prepared.items() if k != "expected_context"},
                    "preview_id": token, "expires_in_seconds": _TRADE_PREVIEW_TTL}
        except Exception as exc:
            _trade_error(exc)

    @app.post("/positions/opening", dependencies=[Depends(require_session)])
    def create_opening_position(body: OpeningPositionIn):
        try:
            db = get_db()
            with _OPENING_PREVIEW_LOCK:
                preview = _OPENING_PREVIEWS.pop(body.preview_id, None)
            if (preview is None or preview["expires"] <= time.monotonic()
                    or preview["request"] != _opening_request(body)
                    or preview["db_path"] != os.path.realpath(db.db_path)):
                raise HTTPException(409, _api_text('anteprima saldo scaduta, gia usata o modificata: ripeti la conferma', 'Balance preview expired, already used or changed: preview and confirm again'))
            prepared = preview["prepared"]
            opening = db.create_position_opening(expected_context=prepared["expected_context"],
                                                 **preview["request"])
            note = prepared["performance_note"]
            try:
                from bellomberg.portfolio import twr_engine, portfolio_analytics
                twr_engine.invalidate_cache()
                portfolio_analytics._ANALYTICS_CACHE.clear()
            except Exception as exc:
                # Already committed: never invite duplicate creation with an HTTP error.
                note += _api_text(f' Cache performance non aggiornata ({type(exc).__name__}).', f' Performance cache not updated ({type(exc).__name__}).')
            return {"ok": True, "opening": opening, "position": prepared["position"],
                    "cash_delta_eur": 0, "cash_disponibile_eur": prepared["cash_disponibile_eur"],
                    "performance_note": note,
                    "valuation_automation": _notify_model_tracking(db.db_path, preview["request"]["ticker"], "portfolio")}
        except Exception as exc:
            _trade_error(exc)

    @app.get("/positions/opening")
    def list_opening_positions():
        return {"openings": get_db().get_opening_positions()}

    @app.get("/positions/opening/{ticker}")
    def get_opening_position(ticker: str):
        rows = get_db().get_opening_positions(ticker.strip().upper())
        if not rows:
            raise HTTPException(404, _api_text('saldo iniziale documentato non trovato', 'Documented opening balance not found'))
        return {"opening": rows[0]}

    @app.get("/positions/{ticker}/tesi")
    def get_tesi_posizione(ticker: str):
        """Tesi corrente + storico delle versioni precedenti (piu' recente prima).
        Richiesta PM 20/08: le tesi si vedono e si modificano dall'app. Lo storico
        vive in `tesi_history.json` accanto al DB — NON in una tabella nuova, che
        sarebbe una migrazione (lettera A/B del PM ancora aperta)."""
        r = get_db().get_tesi(ticker)
        if "error" in r:
            raise HTTPException(404, r["error"])
        return r

    @app.put("/positions/{ticker}/tesi", dependencies=[Depends(require_session)])
    def put_tesi_posizione(ticker: str, body: TesiIn):
        """Riscrive la tesi di una posizione, e SOLO quella.

        Due guardie che tornano 422 col motivo verbatim e ZERO scritture: svuotare
        una tesi che c'era, e accorciarla sotto meta' (il salvataggio distratto che
        cancella un addendum). Entrambe passano rimandando `conferma: true`. La
        versione precedente finisce SEMPRE nello storico prima di essere sostituita,
        quindi nulla e' irreversibile. DB occupato -> 503, mai un 500 anonimo."""
        import sqlite3 as _sqlite3
        try:
            r = get_db().update_tesi(ticker, body.tesi, conferma=body.conferma,
                                     autore=body.autore or "app")
        except _sqlite3.Error as e:
            raise HTTPException(503, _api_text(f'posizioni non scrivibili ({type(e).__name__}: {e}): riprovare', f'Positions cannot be written ({type(e).__name__}: {e}): try again'))
        if "error" in r:
            msg = r["error"]
            # posizione inesistente = 404; guardie e input malformato = 422
            code = r.get("code")
            status = 404 if code == "thesis_position_missing" else 422
            if code:
                raise _CodedHTTPException(status, msg, code)
            raise HTTPException(status, msg)
        return r

    @app.post("/cash/movement", dependencies=[Depends(require_session)])
    def post_cash_movement(body: CashMovementIn):
        """Versamento/prelievo cassa (voce PM 12/08: "come se fosse un trade").
        Registro cash_movements e saldo cash_state nella stessa transazione
        SQLite: la cassa E' l'operazione, senza successi parziali.
        Guardie in memory_db.log_cash_movement (tipo/positivita'/prelievo
        oltre cassa/data ISO) -> 422 col motivo verbatim."""
        import sqlite3 as _sqlite3
        db = get_db()
        try:
            result = db.apply_cash_movement(
                tipo=body.tipo, importo_eur=body.importo_eur,
                data=body.data, nota=body.nota, conferma=body.conferma,
                conferma_soglia=body.conferma_soglia,
                conferma_duplicato=body.conferma_duplicato)
        except ValueError as e:
            from bellomberg.storage.memory_db import CashConfirmationRequired
            if isinstance(e, CashConfirmationRequired):
                raise _CodedHTTPException(422, str(e), e.code) from e
            raise HTTPException(422, str(e))
        except _sqlite3.Error as e:
            raise HTTPException(503, _api_text(f'registro cassa non scrivibile ({type(e).__name__}: {e}): riprovare', f'Cash ledger cannot be written ({type(e).__name__}: {e}): try again'))
        except Exception as e:
            from bellomberg.storage.memory_db import CashNotInitialized
            if isinstance(e, CashNotInitialized):
                raise HTTPException(503, str(e))
            raise
        tipo = (body.tipo or "").strip().upper()
        return {"ok": True, "movement_id": result["movement_id"], "tipo": tipo,
                "importo_eur": round(float(body.importo_eur), 2),
                "cash_disponibile_eur": result["cash_eur"], "cash_note": None,
                "cash_source": "sqlite:cash_state"}

    @app.get("/cash/movements")
    def get_cash_movements_api(limit: int = 100):
        """Registro movimenti di cassa (DEPOSIT/WITHDRAWAL), piu' recenti prima."""
        try:
            rows = get_db().get_cash_movements(limit=limit)
            return {"count": len(rows), "movements": rows}
        except Exception as e:
            raise _err500(e, "get_cash_movements_api")

    @app.post("/feedback", dependencies=[Depends(require_session)])
    def add_feedback(body: FeedbackIn):
        db = get_db()
        fb_id = db.add_pm_feedback(
            feedback_text=body.feedback_text,
            sentiment=body.sentiment.upper(),
            specialist=body.specialist,
            memo_id=body.memo_id,
            decision_id=body.decision_id,
        )
        return {"ok": True, "feedback_id": fb_id}

    @app.get("/tasks/scheduled")
    def get_scheduled_tasks():
        """Lista i task Bellomberg-* dal Windows Task Scheduler (bugfix #156)."""
        ps = (
            "$ts = Get-ScheduledTask -TaskName 'Bellomberg-*' -ErrorAction SilentlyContinue | "
            "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
            "[PSCustomObject]@{ TaskName=$_.TaskName; State=[string]$_.State; "
            "LastRunTime=[string]$i.LastRunTime; NextRunTime=[string]$i.NextRunTime; "
            "LastTaskResult=$i.LastTaskResult } }; "
            "ConvertTo-Json -InputObject @($ts)"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=20,
            )
            raw = (out.stdout or "").strip()
            tasks = json.loads(raw) if raw else []
            if isinstance(tasks, dict):
                tasks = [tasks]
            tasks = [t for t in tasks if t]  # filtra eventuali null
            return {"tasks": tasks, "_source": "Get-ScheduledTask Bellomberg-*",
                    "_timestamp": datetime.now().isoformat()}
        except Exception as e:
            return {"tasks": [], "error": str(e),
                    "_source": "Get-ScheduledTask Bellomberg-*",
                    "_timestamp": datetime.now().isoformat()}

    @app.get("/portfolio/metrics/advanced")
    def get_advanced_metrics(benchmark: str = "SPY"):
        """Metriche di performance istituzionali (Sharpe/Sortino/Calmar/Omega/CVaR/...)."""
        try:
            from bellomberg.portfolio.advanced_metrics import portfolio_metrics
            r = portfolio_metrics(benchmark_ticker=benchmark)
            if r.get("error"):
                raise HTTPException(502, r["error"])
            return r
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "get_advanced_metrics")

    @app.get("/portfolio/metrics/beta_reconcile")
    def get_beta_reconcile(threshold: float = 0.35):
        """Guardrail 13/07: confronta il beta del book dai 3 motori (advanced/risk/factors);
        verdetto UNRELIABLE se divergono oltre soglia — il beta non va usato per decidere.
        BG-ROTTA 06/10: campo di primo livello `beta_guardrail` = stesso codice stabile di
        quant_score (specialist_scores.codice_guardrail_beta); un guasto di reconcile_betas
        e' NON_DISPONIBILE con `error` nel payload, mai NON_CALCOLATO."""
        try:
            from bellomberg.agents.specialist_scores import payload_rotta_beta_reconcile
            return payload_rotta_beta_reconcile(threshold=threshold)
        except Exception as e:
            raise _err500(e, "get_beta_reconcile")

    @app.get("/signals/edge_scan")
    def get_edge_scan_endpoint(min_strength: int = 45, force: bool = False):
        """Edge Scanner: segnali quantitativi oggettivi del portafoglio, ranked (#181).

        22/08 sera (decisione (a) del PM): la scansione e' servita da una cache
        in-process (TTL 1h; 5 min se la scansione era DEGRADATA — decisione (g),
        `cache.ttl_motivo` lo dice); `force=true` la re-interroga davvero. Il parametro
        e' additivo, ma sui HIT il payload cambia in TRE punti, non uno:
        campo nuovo `cache` (attiva/servita_da_cache/ttl_s/eta_s/
        scansione_delle), la frase d'eta' in coda a `copertura.nota`, e
        `generated` che resta l'ora della SCANSIONE (fino a ~1h fa), non della
        risposta — l'ora della risposta si ricava da eta_s. ⚠️ Una richiesta
        che arriva mentre una scansione e' in corso ATTENDE sul lock (fino a
        ~6,4 min a processo freddo) e poi esce dalla cache; due `force=true`
        concorrenti sono DUE scansioni serializzate. Il thread del worker
        resta occupato anche se il client tronca prima (axios, oggi 420s).

        25/08 (decisione S3 del PM, rosa in situ — F43): il GUASTO dello
        scanner (ramo `{"error"}`) risponde **503 col motivo verbatim nel
        detail**, fail-closed come la cassa (changelog (70)). Prima usciva
        HTTP 200 col corpo error e F13 rendeva «Nessun segnale sopra la
        soglia» su un DB lockato (audit/23, ALTO). Distinzione voluta:
        guasto DICHIARATO dallo scanner = 503; eccezione non gestita = bug =
        500 via `_err500`. Il ramo `{"error"}` di `scan_portfolio` RESTA per
        i chiamanti non-HTTP (tool degli agenti, Capo): qui si traduce."""
        try:
            from bellomberg.portfolio.signal_engine import scan_portfolio
            r = scan_portfolio(min_strength=min_strength, force=force)
        except Exception as e:
            raise _err500(e, "get_edge_scan_endpoint")
        if isinstance(r, dict) and r.get("error"):
            _ts = r.get("_timestamp")
            raise HTTPException(503, _api_text('edge scan non disponibile: %s%s', 'Edge scan unavailable: %s%s')
                                % (r["error"],
                                   _api_text(' (guasto rilevato alle %s)', ' (failure detected at %s)') % _ts if _ts else ""))
        return r

    @app.get("/signals/position_doctor/{ticker}")
    def get_position_doctor_endpoint(ticker: str):
        """Position Doctor: diagnosi quantitativa di una posizione con verdetto (#181)."""
        try:
            from bellomberg.portfolio.signal_engine import position_doctor
            return position_doctor(ticker)
        except Exception as e:
            raise _err500(e, "get_position_doctor_endpoint")

    @app.get("/options/vol_surface/{ticker}")
    def get_vol_surface(ticker: str, max_expiries: int = 8, max_days: int = 120,
                        expiries: Optional[str] = None, include_context: bool = True):
        """Volatility surface da chain Polygon (#179, pagina F12). Dal 25/07
        porta anche iv_history_context (IV Rank dallo storico raccolto — n_obs
        e young dichiarati, error dichiarato finche' la storia non c'e')."""
        try:
            from bellomberg.portfolio.vol_surface import build_vol_surface
            if not 1 <= max_expiries <= 8 or not 1 <= max_days <= 3650:
                raise HTTPException(422, _api_text('Scadenze richieste: da 1 a 8; orizzonte: da 1 a 3650 giorni', 'Request 1 to 8 expiries; horizon: 1 to 3650 days'))
            selected = [part.strip() for part in expiries.split(",")] if expiries is not None else None
            if selected is not None and not all(selected):
                raise HTTPException(422, _api_text('Seleziona almeno una scadenza valida', 'Select at least one valid expiry'))
            # 04/10 (W2, Opus 5.5): opzioni non USA (es. IDEM) = errore dichiarato PRIMA
            # di Polygon, con la convenzione di questa rotta per l'errore (200 senza
            # contesto, 502 col contesto); senza chiave diceva «chiave mancante».
            from bellomberg.market_data.copertura import copertura_opzioni, risposta_non_coperta
            _cop = copertura_opzioni(ticker)
            if _cop["stato"] == "non_coperto":
                r = risposta_non_coperta(_cop, "polygon chains multi-expiry -> IV surface (composite OTM)")
            else:
                r = build_vol_surface(ticker, max_expiries=max_expiries, max_days=max_days,
                                      expiries=selected, include_context=include_context)
            if not include_context:
                return r  # Coverage remains visible even when no slice could be loaded.
            if r.get("error"):
                raise HTTPException(502, r["error"])
            try:
                from bellomberg.market_data.iv_history import get_iv_context
                r["iv_history_context"] = get_iv_context(ticker)
            except Exception as e:
                r["iv_history_context"] = {"error": str(e)}
            # GEX dealer per il pannello F12 (richiesta frontend 25/07 sera,
            # ponte sanato in (44)): riuso PURO di compute_gex (#177), solo
            # rinomina chiavi verso il contratto UI. Campo additivo, errore
            # dichiarato: F12 non si rompe mai. NB: chain rifetchata da
            # Polygon (fetch separato da vol_surface) — dichiarato in (44).
            try:
                from bellomberg.portfolio.positioning_tools import compute_gex
                g = compute_gex(ticker)
                if g.get("error"):
                    r["gex"] = {"error": g["error"]}
                else:
                    r["gex"] = {
                        "by_strike": [{"strike": row.get("strike"),
                                       "gex_1pct_usd": row.get("net_gex_usd"),
                                       "call_oi": row.get("call_oi"),
                                       "put_oi": row.get("put_oi")}
                                      for row in g.get("top_strikes", [])],
                        "flip_strike": g.get("gamma_flip_strike"),
                        "net_gex_1pct_usd": g.get("net_gex_usd_per_1pct"),
                        "spot_est": g.get("spot_est"),
                        "regime": g.get("regime"),
                        "basis": ("SqueezeMetrics conv. (dealer long call / short put Γ) · "
                                  f"{len(g.get('expiries_used', []))} expiry ≤45g · "
                                  "12 strike top |GEX| · OI da snapshot delayed, "
                                  "deep-OTM senza greeks esclusi"),
                    }
            except Exception as e:
                r["gex"] = {"error": str(e)}
            return r
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:
            raise _err500(e, "get_vol_surface")

    @app.get("/options/vol_cone/{ticker}")
    def get_vol_cone(ticker: str, force: bool = False):
        """Vol cone (lotto c 25/07): realized vol per orizzonte (5/10/21/63g,
        percentili 1y) vs term structure ATM IV corrente (riuso vol_surface).
        Problemi-dato = error dichiarato in 200 (pattern benchmark), eccezioni
        vere = 500. Ticker della lista IV_TICKERS ((39), estendibile)."""
        try:
            from bellomberg.portfolio.vol_cone import compute_vol_cone
            r = compute_vol_cone(ticker, force=force)
            # 04/10 (W2, Opus 5.5): la parte realized resta (chiusure, non opzioni); la
            # implied di un'opzione non USA e' «non coperto», non un guasto Polygon.
            from bellomberg.market_data.copertura import copertura_opzioni, risposta_non_coperta
            _cop = copertura_opzioni(ticker)
            if _cop["stato"] == "non_coperto" and isinstance(r, dict) and isinstance(r.get("implied"), dict):
                r["implied"] = risposta_non_coperta(_cop, "vol_surface (Polygon chains)")
            return r
        except Exception as e:
            raise _err500(e, "get_vol_cone")

    @app.get("/macro")
    def get_macro():
        try:
            from bellomberg.agents.agent_tools import tool_get_macro_dashboard
            return tool_get_macro_dashboard()
        except Exception as e:
            raise _err500(e, "get_macro")

    @app.get("/portfolio/risk")
    def get_portfolio_risk(force: bool = False):
        """Quantitative risk metrics: VaR, Sharpe, Beta, Max DD, correlation, alerts."""
        try:
            from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
            return compute_portfolio_risk(force=force)
        except Exception as e:
            raise _err500(e, "get_portfolio_risk")

    @app.get("/portfolio/gap_days")
    def get_portfolio_gap_days(force: bool = False):
        """Scomposizione della finestra multi-seduta per seduta di borsa.

        Sedute chiuse: chiusure ufficiali Yahoo NON rettificate; seduta in
        formazione: live snapshot vs ultima chiusura. Vedi gap_days.py."""
        try:
            from bellomberg.portfolio.gap_days import reconstruct
            return reconstruct(force=force)
        except Exception as e:
            raise _err500(e, "get_portfolio_gap_days")

    @app.get("/portfolio/factors")
    def get_portfolio_factors(period: str = "1y", force: bool = False):
        """Fama-French 5-factor + Momentum decomposition.
        Per ogni holding: alpha, beta_market, beta_smb, beta_hml, beta_rmw, beta_cma, beta_mom + t-stat + R^2.
        Stima OLS con Newey-West HAC standard errors. Dati K. French Data Library.
        """
        try:
            from bellomberg.portfolio.portfolio_factors import compute_portfolio_factors
            return compute_portfolio_factors(period=period, force=force)
        except Exception as e:
            raise _err500(e, "get_portfolio_factors")

    @app.get("/portfolio/garch")
    def get_portfolio_garch(force: bool = False):
        """GARCH(1,1) / GJR-GARCH(1,1) volatility forecasting.
        Model selection automatica. Diagnostics: Ljung-Box, ARCH-LM, ADF, persistence.
        Forecast vol h-step ahead 1d/5d/22d con CI 95%.
        """
        try:
            from bellomberg.portfolio.portfolio_garch import compute_portfolio_garch
            return compute_portfolio_garch(force=force)
        except Exception as e:
            raise _err500(e, "get_portfolio_garch")

    @app.get("/news/providers")
    def get_news_providers():
        """Stato provider news contingentati (voce (25) riusabile, richiesta ponte F8).
        Zero rete: legge solo lo stato del rate-limiter. Dichiara SOLO cause globali
        (SKIP_BUDGET/SKIP_DISABLED); cooldown per-query e provider non contingentati
        (marketaux/tiingo/yfinance/rss) restano fuori misura, dichiarato in `nota`.
        """
        try:
            from bellomberg.market_data.news_aggregator import providers_blocked, providers_budget, NEWS_PROVIDER_LIMITS, stato_ultimo_giro, fonti_spente
            _muti = providers_blocked()
            return {"fonti_mute": _muti or None,
                    # 04/10 (W2, Opus 5.5): fonti tolte PER DECISIONE (Reddit), non guaste
                    "fonti_spente": fonti_spente(),
                    "avviso": _avviso_provider_bloccati(_muti, ' — poche/zero news NON significano quiete', ' — few/no news items do NOT imply calm'),
                    "providers_contingentati": sorted(NEWS_PROVIDER_LIMITS),
                    # P2 (12/08): freschezza del feed — chiave SEMPRE presente,
                    # n.d./illeggibile dichiarati (pattern 25). La "prossima
                    # esecuzione" NON sta qui: e' NextRunTime di /tasks/scheduled.
                    "ultimo_giro": stato_ultimo_giro(),
                    "refresh_job": news_refresh_manager.status(),
                    # 02/10/2026: consumo del budget e dosaggio, dallo stato del limiter (zero rete)
                    "budget": providers_budget(),
                    "nota": (_api_text('solo cause globali del limiter; esito reale delle chiamate e provider non contingentati non misurati qui', 'Global limiter causes only; actual call outcomes and providers without a quota are not measured here')),
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            raise _err500(e, "get_news_providers")

    @app.get("/news/ticker/{ticker}")
    def get_news_ticker(ticker: str, days: int = 3, max_per_source: int = 5):
        """News multi-fonte per un ticker specifico (NewsAPI+Marketaux+TheNewsAPI+GNews+yfinance)."""
        try:
            from bellomberg.market_data.news_aggregator import search_news_for_ticker, providers_blocked
            items = search_news_for_ticker(ticker.upper(), days=days, max_per_source=max_per_source)
            # residuo muto #3 (Lotto C verita' dei numeri, 23/07): provider fuori
            # per budget/disable DICHIARATI — "0 news" e "sono cieco" non sono
            # piu' lo stesso valore (regola 14/07).
            _muti = providers_blocked()
            return {"ticker": ticker.upper(), "count": len(items), "items": items,
                    "fonti_mute": _muti or None,
                    "avviso": _avviso_provider_bloccati(_muti, ' — poche/zero news qui NON significa quiete', ' — few/no news items here do NOT imply calm'),
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            raise _err500(e, "get_news_ticker")

    @app.get("/news/search")
    def get_news_search(q: str, days: int = 3, max_per_source: int = 5):
        """News globali per query libera. Include RSS Bloomberg/FT/ANSA/Yahoo/SA Currents."""
        try:
            from bellomberg.market_data.news_aggregator import search_news_global, providers_blocked
            items = search_news_global(q, days=days, max_per_source=max_per_source)
            _muti = providers_blocked()   # residuo muto #3 (Lotto C 23/07)
            return {"query": q, "count": len(items), "items": items,
                    "fonti_mute": _muti or None,
                    "avviso": _avviso_provider_bloccati(_muti, ' — poche/zero news qui NON significa quiete', ' — few/no news items here do NOT imply calm'),
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            raise _err500(e, "get_news_search")

    @app.get("/news/portfolio")
    def get_news_portfolio(days: int = 2, max_per_ticker: int = 3):
        """News per ogni ticker del portfolio. Output {ticker: [news]}."""
        try:
            from bellomberg.market_data.news_aggregator import search_portfolio_news, providers_blocked
            data = search_portfolio_news(days=days, max_per_ticker=max_per_ticker)
            _muti = providers_blocked()   # residuo muto #3-4 (Lotto C 23/07)
            return {"by_ticker": data, "n_tickers": len(data),
                    "fonti_mute": _muti or None,
                    "avviso": _avviso_provider_bloccati(_muti, " — un ticker a 0 news puo' essere cecita', non quiete", ' — zero news for a ticker may indicate lack of visibility, not calm'),
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            raise _err500(e, "get_news_portfolio")

    @app.get("/news/feed")
    def get_news_feed_endpoint(limit: int = 50, min_relevance: int = 0,
                                ticker: Optional[str] = None,
                                sentiment: Optional[str] = None):
        """Auto-feed news persistente (popolato da scheduled task auto_pull_feed).
        Filtri: min_relevance (1-10), ticker, sentiment (bullish/bearish/neutral).
        """
        try:
            from bellomberg.market_data.news_aggregator import get_feed
            items = get_feed(limit=limit, min_relevance=min_relevance,
                              ticker=ticker, sentiment=sentiment)
            # G3 (04/10): notizie salvate senza classificazione (sentiment/relevance null)
            return {"count": len(items), "items": items,
                    "not_classified": sum(1 for i in items if i.get("classification_status") == "not_classified"),
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            raise _err500(e, "get_news_feed_endpoint")

    @app.post("/news/feed/refresh", dependencies=[Depends(require_session), Depends(throttle)])
    def post_news_feed_refresh(days: int = 1, classify: bool = True):
        """Accetta un refresh manuale e osserva il job condiviso col timer."""
        # Keep the historical query parameters for backwards compatibility.  The
        # coordinator owns the canonical pull settings (one-day lookback + classify).
        # G3: durante un giro NON ne parte un secondo: torna lo stesso job con
        # accepted=false e reason=already_running; l'esito si legge col GET sotto.
        del days, classify
        return news_refresh_manager.trigger("manual")

    @app.get("/news/feed/refresh")
    def get_news_feed_refresh(job_id: Optional[str] = None):
        """Stato di un giro notizie (G3, 04/10): senza job_id l'ultimo, con job_id quello
        (ultimi 20). Esito in job.result (saved, fetched, classification_failed, degraded...),
        job.error e job.duration_s; scheduler = configurazione e pausa notte/weekend."""
        stato = news_refresh_manager.status()
        if job_id is None:
            job = {k: v for k, v in stato.items() if k != "scheduler"}
        else:
            job = news_refresh_manager.job(job_id)
            if job is None:
                raise HTTPException(404, _api_text(
                    'giro notizie %s sconosciuto (si conservano gli ultimi 20)',
                    'unknown news refresh %s (the last 20 are kept)') % job_id)
        return {"job": job, "scheduler": stato.get("scheduler"),
                "timestamp": datetime.now().isoformat()}

    # 02/10: sintesi AI di UN articolo, solo su richiesta esplicita (costa crediti).
    # GET legge la cache della lingua della richiesta (mai rete, mai modello);
    # POST scarica l'articolo e chiama NEWS_SUMMARY_MODEL (contratto in article_summary.py).
    @app.get("/news/{news_id}/article-summary")
    def get_news_article_summary(news_id: int):
        try:
            from bellomberg.market_data.article_summary import read_cached_article_summary
            result = read_cached_article_summary(news_id)
        except Exception as e:
            raise _err500(e, "get_news_article_summary")
        if result.get("status") == "missing":
            raise HTTPException(404, _api_text(f"Notizia {news_id} non trovata", f"News item {news_id} not found"))
        return result

    @app.post("/news/{news_id}/article-summary",
              dependencies=[Depends(require_session), Depends(throttle)])
    def post_news_article_summary(news_id: int, request: Request, regenerate: bool = False):
        try:
            from bellomberg.market_data.article_summary import summarize_news_article
            result = summarize_news_article(news_id, regenerate=regenerate)
        except Exception as e:
            _refund_throttle(request)
            raise _err500(e, "post_news_article_summary")
        if result.get("status") == "missing":
            _refund_throttle(request)
            raise HTTPException(404, _api_text(f"Notizia {news_id} non trovata", f"News item {news_id} not found"))
        # Nessuna spesa (cache, non configurato, articolo illeggibile): il throttle si restituisce.
        if result.get("cached") or result.get("status") in ("not_configured", "unreadable"):
            _refund_throttle(request)
        return result

    @app.get("/news/catalysts")
    def get_news_catalysts(days: int = 7, limit: int = 20):
        """Top catalyst di mercato del periodo (Fed, earnings, M&A, FDA, geopolitica)."""
        try:
            from bellomberg.market_data.news_aggregator import get_top_catalysts
            items = get_top_catalysts(days=days, limit=limit)
            return {"count": len(items), "items": items,
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            raise _err500(e, "get_news_catalysts")

    # ===== NEW: Bloomberg-style news terminal endpoints =====

    @app.get("/news/macro")
    def get_news_macro(categories: Optional[str] = None,
                        min_importance: int = 3,
                        days: int = 2,
                        max_per_topic: int = 4):
        """News macro/geo/politica/EM/crypto aggregate per topic.
        - categories: CSV di {rates,inflation,geopolitics,politics,em,commodities,crypto,corporate}
                      (vuoto = tutte)
        - min_importance: 1-5 (default 3)
        - days: lookback in giorni
        """
        try:
            from bellomberg.market_data.news_aggregator import fetch_macro_news, providers_blocked, fonti_spente
            cats = [c.strip() for c in categories.split(",")] if categories else None
            items = fetch_macro_news(
                categories=cats,
                min_importance=min_importance,
                days=days,
                max_per_topic=max_per_topic,
                include_reddit=False,   # 04/10 (W2): Reddit SPENTA, dichiarata in fonti_spente
            )
            _muti = providers_blocked()   # pattern voce (25) — richiesta ponte F8 (F5)
            return {"count": len(items), "items": items,
                    "categories_requested": cats,
                    "fonti_mute": _muti or None,
                    "fonti_spente": fonti_spente(),
                    "avviso": _avviso_provider_bloccati(_muti, ' — pochi/zero item macro NON significano quiete', ' — few/no macro items do NOT imply calm'),
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            raise _err500(e, "get_news_macro")

    @app.get("/news/corporate-events")
    def get_news_corporate_events(days: int = 14, max_items: int = 30):
        """Eventi societari high-impact: 8-K + Form 4 insider trades + news M&A."""
        try:
            from bellomberg.market_data.news_aggregator import fetch_corporate_events
            items = fetch_corporate_events(days=days, max_items=max_items)
            return {"count": len(items), "items": items,
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            raise _err500(e, "get_news_corporate_events")

    @app.get("/news/top-global")
    def get_news_top_global(limit: int = 15):
        """Top 15 news globali del giorno (Bloomberg/FT/ANSA/Yahoo/CNBC + NewsAPI top headlines)."""
        try:
            from bellomberg.market_data.news_aggregator import get_top_global
            items = get_top_global(limit=limit)
            return {"count": len(items), "items": items,
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            raise _err500(e, "get_news_top_global")

    @app.get("/news/briefing/current")
    def get_briefing_current():
        """Ritorna l'ultimo briefing salvato (4 paragrafi: tone, macro, portfolio, watch)."""
        try:
            from bellomberg.cli.briefing_engine import get_current_briefing
            return get_current_briefing()
        except Exception as e:
            raise _err500(e, "get_briefing_current")

    @app.post("/news/briefing/refresh", dependencies=[Depends(require_session)])
    def post_briefing_refresh(period: Optional[str] = None):
        """Genera un nuovo briefing per lo slot indicato (morning|midday|afternoon|evening)
        o quello corrente in base all'ora.
        """
        try:
            from bellomberg.cli.briefing_engine import generate_briefing
            return generate_briefing(period)
        except Exception as e:
            raise _err500(e, "post_briefing_refresh")

    @app.get("/news/economic-calendar")
    def get_economic_calendar(days_ahead: int = 14, earnings_days_ahead: Optional[int] = None):
        """Calendar eventi macro + earnings calendar via Finnhub (se key configurata).
        Combina: hardcoded baseline (FOMC/ECB/NFP/CPI) + Finnhub economic calendar
        (con previous/estimate/actual) + Finnhub earnings for portfolio tickers.
        """
        try:
            from datetime import datetime as _dt, timedelta as _td
            today = _dt.now().date()
            events = _hardcoded_economic_calendar(today, days_ahead)
            # 27/08 (seconda meta' Finnhub, C1 del ponte): ogni fonte Finnhub
            # deposita il motivo del proprio silenzio e il payload lo dichiara
            # con `fonti_mute` {fonte: motivo} + `avviso` (la forma di /news/macro,
            # che NewsPage consuma con takeFonti). Prima: /calendar/economic e'
            # FUORI PIANO (403 per sempre sul gratuito) e il calendario Finnhub
            # arrivava vuoto come se fosse quiete — la baseline cablata restava
            # sola senza dirlo.
            _mute = {"/calendar/economic": [], "/calendar/earnings": []}
            _mute_yf: list = []
            # le trimestrali del portafoglio possono guardare piu' avanti dei dati macro
            giorni_earnings = max(days_ahead, earnings_days_ahead or 0)
            # Aggiungi economic calendar Finnhub con prev/est/actual
            try:
                from bellomberg.market_data.finnhub_news import fetch_economic_calendar
                fh_econ = fetch_economic_calendar(days_back=1, days_ahead=days_ahead,
                                                  motivo=_mute["/calendar/economic"])
                for e in fh_econ:
                    events.append({
                        "date": e.get("date", ""),
                        "time": e.get("time", "") + " CET" if e.get("time") else "TBD",
                        "type": "Macro Release",
                        "title": e.get("title", ""),
                        "importance": e.get("importance", 2),
                        "country": e.get("country", "")[:3].upper() if e.get("country") else "?",
                        "previous": e.get("previous"),
                        "estimate": e.get("estimate"),
                        "actual": e.get("actual"),
                        "unit": e.get("unit", ""),
                    })
            except Exception as fx:
                _safe_print(f"[CAL] finnhub economic skipped: {fx}")
                _mute["/calendar/economic"].append(f"eccezione: {type(fx).__name__}: {fx}")
            # Aggiungi earnings calendar da Finnhub (filtro portfolio tickers)
            try:
                from bellomberg.market_data.earnings_yf import paese_da_suffisso
                from bellomberg.market_data.finnhub_news import fetch_earnings_for_portfolio
                from bellomberg.storage.memory_db import MemoryDB
                from bellomberg.storage.classificazione import carica_veicoli
                db = MemoryDB()
                snap = db.get_portfolio_summary()
                negozio = carica_veicoli()
                tickers, riserva_yf, nota_natura = _tickers_earnings_con_riserva(
                    snap.get("positions", []), negozio)
                if nota_natura:
                    _mute["/calendar/earnings"].append(nota_natura)
                if negozio.get("origine") in {"assente", "illeggibile"}:
                    earnings = []
                elif not tickers:
                    earnings = []
                else:
                    earnings = fetch_earnings_for_portfolio(
                        tickers, days_ahead=giorni_earnings,
                        motivo=_mute["/calendar/earnings"])
                for e in earnings:
                    hour_label = {"bmo": "Pre-market", "amc": "After-close", "dmh": "During market"}.get(
                        e.get("hour", ""), e.get("hour", "")
                    )
                    est_label = f"EPS est ${e.get('eps_estimate')}" if e.get("eps_estimate") else ""
                    events.append({
                        "date": e.get("date", ""),
                        "time": hour_label or "TBD",
                        "type": "Earnings",
                        "title": f"{e.get('symbol', '')} Earnings Q{e.get('quarter','?')} {e.get('year','')} {est_label}".strip(),
                        "importance": 4,
                        "country": paese_da_suffisso(e.get("symbol", "")) if e.get("symbol") else "?",
                        "ticker": e.get("symbol", ""),
                        "source": "finnhub",
                    })
                # riserva yfinance per i titoli europei senza ADR (Finnhub gratuito: 403)
                if riserva_yf:
                    from bellomberg.market_data.earnings_yf import prossima_trimestrale
                    try:
                        from bellomberg.storage.negozi_privati import carica_alias
                        _ea = carica_alias()
                        alias_yf = (_ea["alias"].get("yfinance", {})
                                    if _ea["origine"] not in {"assente", "illeggibile"} else {})
                        if _ea["origine"] == "illeggibile":
                            _mute_yf.append(_api_text(
                                "alias_fonti illeggibile (%s): simboli yfinance usati come sono",
                                "alias_fonti unreadable (%s): yfinance symbols used as they are")
                                % (_ea.get("motivo") or "causa non dichiarata"))
                    except Exception as exc:  # G8: dichiarato, non ingoiato
                        alias_yf = {}
                        _mute_yf.append(_api_text(
                            "alias_fonti illeggibile (%s: %s): simboli yfinance usati come sono",
                            "alias_fonti unreadable (%s: %s): yfinance symbols used as they are")
                            % (type(exc).__name__, exc))
                    limite = (today + _td(days=giorni_earnings)).isoformat()
                    for ticker in riserva_yf:
                        simbolo_yf = alias_yf.get(ticker, ticker)
                        prima = len(_mute_yf)
                        trovata = prossima_trimestrale(simbolo_yf, today, motivo=_mute_yf)
                        if not trovata and len(_mute_yf) == prima:
                            # G8: Finnhub non interrogato (nessun simbolo USA) e yfinance senza
                            # data: prima il titolo spariva dal calendario senza alcuna voce
                            _mute_yf.append(_api_text(
                                "%s: nessuna prossima trimestrale da yfinance (simbolo %s) e nessun "
                                "simbolo USA per Finnhub: assente dal calendario",
                                "%s: no upcoming earnings date from yfinance (symbol %s) and no US "
                                "symbol for Finnhub: missing from the calendar") % (ticker, simbolo_yf))
                        if not trovata or trovata["date"] > limite:
                            continue
                        events.append({
                            "date": trovata["date"], "time": "TBD", "type": "Earnings",
                            "title": f"{ticker} Earnings", "importance": 4,
                            "country": paese_da_suffisso(ticker),
                            "ticker": ticker, "source": "yfinance",
                            "date_estimated": trovata["stimata"],
                        })
            except Exception as fx:
                _safe_print(f"[CAL] finnhub earnings skipped: {fx}")
                _mute["/calendar/earnings"].append(f"eccezione: {type(fx).__name__}: {fx}")
            # Sort by date
            events.sort(key=lambda x: (x.get("date", ""), x.get("time", "")))
            fonti_mute = {"finnhub " + k: "; ".join(v) for k, v in _mute.items() if v}
            if _mute_yf:
                fonti_mute["yfinance earnings"] = "; ".join(_mute_yf)
            return {"count": len(events), "items": events,
                    "timestamp": _dt.now().isoformat(),
                    "fonti_mute": fonti_mute or None,
                    "avviso": ((_api_text("calendario Finnhub PARZIALE: la baseline cablata (FOMC/ECB/NFP/CPI) c'e'; manca: %s — un calendario corto NON significa settimana vuota", 'PARTIAL Finnhub calendar: the embedded baseline (FOMC/ECB/NFP/CPI) is present; missing: %s — a short calendar does NOT mean an empty week'))
                               % ", ".join(fonti_mute)) if fonti_mute else None}
        except Exception as e:
            raise _err500(e, "get_economic_calendar")

    @app.get("/news/finnhub-intel/{ticker}")
    def get_finnhub_intel(ticker: str):
        """Pacchetto completo Finnhub per un ticker: news + earnings + insider + sentiment."""
        try:
            from bellomberg.market_data.finnhub_news import fetch_finnhub_intel
            return fetch_finnhub_intel(ticker)
        except Exception as e:
            raise _err500(e, "get_finnhub_intel")

    @app.get("/news/insider-trades/{ticker}")
    def get_insider_trades_endpoint(ticker: str, days: int = 30, valuta: Optional[str] = None):
        """Insider Form 4 real-time per un ticker (via Finnhub, fallback SEC EDGAR).
        04/10 (W2, Opus 5.5): Form 4 copre solo emittenti USA. `.MI` -> internal dealing
        eMarket SDIR con lo stato della fonte (KO/STALE/non_coperto dichiarati); altri
        mercati, crypto o copertura indeterminata -> errore dichiarato in 200, nessuna
        chiamata (mai il suffisso tolto: aggancerebbe un omonimo USA).
        handoff-3 (W1): `.MI` -> SDIR scelto da sdir.py (eMarket o 1INFO); ISIN assente dalle
        tabelle = risolto su Borsa Italiana (`risoluzione_isin`). LATENZA DICHIARATA: la PRIMA
        richiesta per un .MI senza voce puo' fare fino a ~30 richieste HTTP verso Borsa (pausa
        1 s, timeout 25 s: tetto teorico ~13 min) + yfinance senza timeout, poi la rete dello
        SDIR; dalla seconda niente (negozio automatico o memoria negativa). `def`: threadpool."""
        try:
            from bellomberg.market_data.copertura import copertura_usa, risposta_non_coperta
            if str(ticker or "").strip().upper().endswith(".MI"):
                # W1 handoff-3 (05/10): voce ISIN mancante -> risolta su Borsa Italiana col nome
                # della posizione (DB di questa API) o del fornitore prezzi, dichiarata; poi lo
                # SDIR giusto (instradatore sdir.py: eMarket SDIR o 1INFO-SDIR, `sdir` lo dice)
                from bellomberg.market_data import sdir as _sdir
                from bellomberg.market_data.isin_automatico import assicura_isin_it, con_risoluzione, nome_noto
                _dbp = get_db().db_path
                from bellomberg.market_data.isin_automatico import giorni_validi
                # days non valido = lo sdir lo respinge per parametro: niente rete di risoluzione
                ris = assicura_isin_it(ticker, db_path=_dbp) if giorni_validi(days) else None
                idl = _sdir.get_internal_dealing(ticker, giorni=days, nome=nome_noto(ticker, ris, db_path=_dbp))
                return con_risoluzione(
                    {"ticker": idl.get("ticker"), "source": "sdir", "sdir": idl.get("sdir"),
                     "instradamento": idl.get("instradamento"),
                     "stato": idl.get("stato"),
                     "error": None if idl.get("stato") in ("ok", "vuoto_misurato") else (idl.get("motivo") or idl.get("errore") or idl.get("stato")),
                     "count": len(idl.get("comunicazioni") or []), "internal_dealing": idl}, ris)
            # senza ?valuta= la valuta e' quella della posizione nel book (sola lettura);
            # fuori dal book la presunzione USA dal simbolo e' DICHIARATA in copertura_nota
            _nota = None
            if valuta is None:   # helper comune (W1, copertura.py): una logica sola
                from bellomberg.market_data.copertura import valuta_dal_book
                _vb = valuta_dal_book(ticker, db_path=get_db().db_path)
                valuta, _nota = _vb["valuta"], _vb["nota"]
            _cop = copertura_usa(ticker, "insider Form 4 (Finnhub/SEC)", valuta)
            if _cop["stato"] != "coperto":
                return {**risposta_non_coperta(_cop, "insider Form 4 (Finnhub/SEC)"), "copertura_nota": _nota}
            _muti, _mot = {}, []
            try:
                from bellomberg.market_data.finnhub_news import fetch_insider_trades as fn_insider
                trades = fn_insider(ticker, days=days, motivo=_mot)
                if trades:
                    return {"ticker": ticker, "source": "finnhub", "count": len(trades), "trades": trades,
                            "fonti_mute": None, "copertura_nota": _nota}
                if _mot:
                    _muti["finnhub"] = "; ".join(str(m) for m in _mot)
            except Exception as e:   # dichiarato prima di passare a SEC: solo il tipo
                _muti["finnhub"] = type(e).__name__
            from bellomberg.market_data.sec_edgar import get_insider_trades as sec_insider
            _mot = []
            trades = sec_insider(ticker, days=days, motivo=_mot)
            if _mot:
                _muti["sec_edgar"] = "; ".join(str(m) for m in _mot)
            return {"ticker": ticker, "source": "sec_edgar", "count": len(trades), "trades": trades,
                    "fonti_mute": _muti or None, "copertura_nota": _nota}
        except Exception as e:
            raise _err500(e, "get_insider_trades_endpoint")

    @app.get("/news/ipo-calendar")
    def get_ipo_calendar(days_ahead: int = 30):
        """IPO calendar prossimi N giorni (via Finnhub)."""
        try:
            from bellomberg.market_data.finnhub_news import fetch_ipo_calendar
            items = fetch_ipo_calendar(days_ahead)
            return {"count": len(items), "items": items}
        except Exception as e:
            raise _err500(e, "get_ipo_calendar")

    @app.get("/news/alerts/unnotified")
    def get_news_alerts(min_relevance: int = 8, limit: int = 10):
        """Ritorna news critiche (relevance >= 8) non ancora marcate come notified.
        Usato dal frontend per push notifications desktop.
        """
        try:
            db = MemoryDB()
            conn = connect_sqlite(db.db_path)  # hardening #32: WAL + busy_timeout
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, snippet, url, provider, ticker_mentioned, sentiment,
                       relevance, published_at, pulled_at
                FROM news_feed
                WHERE notified = 0 AND relevance >= ?
                ORDER BY relevance DESC, pulled_at DESC
                LIMIT ?
            """, (min_relevance, limit))
            rows = cur.fetchall()
            conn.close()
            items = [{
                "id": r[0], "title": r[1], "snippet": r[2], "url": r[3],
                "provider": r[4], "ticker": r[5], "sentiment": r[6],
                "relevance": r[7], "published_at": r[8], "pulled_at": r[9],
            } for r in rows]
            return {"count": len(items), "items": items}
        except Exception as e:
            raise _err500(e, "get_news_alerts")

    @app.post("/news/alerts/mark-notified", dependencies=[Depends(require_session)])
    def mark_alerts_notified(ids: List[int]):
        """Marca le news come notified=1 (chiamato dopo che il frontend ha mostrato la notifica)."""
        if not ids:
            return {"updated": 0}  # audit/11 §5: 'IN ()' e' SQL invalido -> 500 su una no-op
        conn = None
        try:
            db = MemoryDB()
            conn = connect_sqlite(db.db_path)  # hardening #32: WAL + busy_timeout
            cur = conn.cursor()
            placeholders = ",".join("?" * len(ids))
            cur.execute(f"UPDATE news_feed SET notified=1 WHERE id IN ({placeholders})", ids)
            conn.commit()
            return {"updated": cur.rowcount}
        except Exception as e:
            raise _err500(e, "mark_alerts_notified")
        finally:
            if conn is not None:
                try:
                    conn.close()  # audit/11 §5: anche nei path d'errore (WAL sul DB vivo)
                except Exception:
                    pass

    @app.get("/news/topics")
    def get_news_topics_list():
        """Ritorna la lista completa dei topic disponibili per il news terminal."""
        try:
            from bellomberg.market_data.news_topics import TOPICS, CATEGORIES
            return {"categories": CATEGORIES, "topics": TOPICS}
        except Exception as e:
            raise _err500(e, "get_news_topics_list")

    @app.get("/portfolio/montecarlo")
    def get_portfolio_montecarlo(
        horizon_days: int = 252,
        n_sims: int = 10000,
        lookback_years: int = 5,
        method: str = "fhs",
        drift_mode: str = "zero",
        stress: str = "none",
        add: Optional[str] = None,
        remove: Optional[str] = None,
        force: bool = False,
        sample_paths_n: int = 200,
    ):
        """Monte Carlo v2 bank-grade.
        Args:
          horizon_days: 5..1260 (default 252 = 1y)
          n_sims: 1000..50000 (default 10000)
          lookback_years: 1, 2, 5 (default), 10
          method: 'parametric_t' (legacy) | 'fhs' (default, bank-grade) | 'block_bootstrap'
          drift_mode: 'zero' (default) | 'shrinkage' (0.3x historical) | 'historical' (biased)
          stress: 'none' (default) | 'gfc_2008' | 'covid_2020' | 'shock_3sigma'
          add: ticker comma-separated da aggiungere per what-if
          remove: ticker comma-separated da rimuovere per what-if
          force: bypass cache
          sample_paths_n: quante traiettorie campione spedire, 1..500 (default 200
            per F5, richiesta PM 26/07). Il motore ne produce n_sims: queste sono
            solo quante se ne DISEGNANO. Il default del motore resta 10 perche' lo
            stesso payload alimenta il tool degli agenti e il fan chart del PDF —
            qui si alza per la UI, che il contesto di una run non ce l'ha.
        """
        try:
            from bellomberg.portfolio.portfolio_montecarlo import run_monte_carlo
            add_list = [t.strip().upper() for t in add.split(",")] if add else None
            remove_list = [t.strip().upper() for t in remove.split(",")] if remove else None
            return run_monte_carlo(
                horizon_days=horizon_days,
                n_sims=n_sims,
                lookback_years=lookback_years,
                method=method,
                drift_mode=drift_mode,
                stress_scenario=stress,
                add_tickers=add_list,
                remove_tickers=remove_list,
                force_refresh=force,
                sample_paths_n=sample_paths_n,
            )
        except Exception as e:
            raise _err500(e, "get_portfolio_montecarlo")

    @app.post("/portfolio/montecarlo/v3", dependencies=[Depends(require_session)])
    def post_portfolio_montecarlo_v3(body: dict):
        """Monte Carlo v3 con modifiche strutturate (sizing EUR reale).

        Body:
        {
          "horizon_days": 252,
          "n_sims": 10000,
          "lookback_years": 5,
          "method": "fhs",
          "drift_mode": "zero",
          "stress": "none",
          "modifications": [
             {"action": "add",    "ticker": "ALFA",  "amount_eur": 25000},
             {"action": "remove", "ticker": "AAPL",  "amount_eur": 10000},
             {"action": "trim",   "ticker": "BETA.L", "amount_pct": 30}
          ],
          "force": false
        }
        """
        try:
            from bellomberg.portfolio.portfolio_montecarlo import run_monte_carlo_v3
            body = body or {}
            return run_monte_carlo_v3(
                horizon_days=int(body.get("horizon_days", 252)),
                n_sims=int(body.get("n_sims", 10000)),
                lookback_years=int(body.get("lookback_years", 5)),
                method=body.get("method", "fhs"),
                drift_mode=body.get("drift_mode", "zero"),
                stress_scenario=body.get("stress", "none"),
                modifications=body.get("modifications") or [],
                force_refresh=bool(body.get("force", False)),
                # come il GET: la UI del what-if merita lo stesso cono denso.
                # `or 200` e non `get(..., 200)`: un `null` esplicito nel JSON
                # deve valere "non l'ho chiesto", non un TypeError su int(None)
                sample_paths_n=int(body.get("sample_paths_n") or 200),
            )
        except Exception as e:
            raise _err500(e, "post_portfolio_montecarlo_v3")

    # === Ticker validation (used by MC v3 what-if + Trade Entry autocomplete) ===
    _TICKER_VALIDATION_CACHE: Dict[str, Tuple[float, dict]] = {}
    _TICKER_TTL_S = 300  # 5 minutes

    @app.get("/portfolio/validate_ticker")
    def validate_ticker(symbol: str):
        """Validate a ticker by probing yfinance. Returns name/currency/last_price.
        Used to confirm a ticker exists before adding it to a Monte Carlo what-if.
        Cached in-memory 5 minutes per symbol.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return {"ok": False, "symbol": "", "error": _api_text('Simbolo vuoto', 'Empty symbol')}

        # Cache hit
        now = time.time()
        cached = _TICKER_VALIDATION_CACHE.get(sym)
        if cached and (now - cached[0]) < _TICKER_TTL_S:
            return cached[1]

        try:
            import yfinance as yf
            tk = yf.Ticker(sym)
            # fast_info is much faster than .info and rarely throws
            try:
                fast = tk.fast_info
                last_price = float(fast.get("last_price") or fast.get("lastPrice") or 0)
                currency = fast.get("currency") or "USD"
                exchange = fast.get("exchange") or ""
            except Exception:
                last_price, currency, exchange = 0.0, "USD", ""

            # Get full name (slower but only on cache miss)
            name = sym
            try:
                info = tk.info or {}
                name = info.get("longName") or info.get("shortName") or sym
                if not currency or currency == "USD":
                    currency = info.get("currency") or currency
                if not exchange:
                    exchange = info.get("exchange") or ""
            except Exception:
                pass

            # If still no last_price, try history
            if not last_price or last_price <= 0:
                try:
                    h = tk.history(period="5d")
                    if not h.empty:
                        last_price = float(h["Close"].iloc[-1])
                except Exception:
                    pass

            ok = bool(last_price and last_price > 0)
            result = {
                "ok": ok,
                "symbol": sym,
                "name": name if ok else None,
                "currency": currency if ok else None,
                "last_price": last_price if ok else None,
                "exchange": exchange if ok else None,
                "error": None if ok else _api_text(f"Nessun prezzo per '{sym}'. Verifica il ticker (serve un suffisso come .L, .MI, .DE, .HK?)", f"No price data found for '{sym}'. Check ticker (need suffix like .L, .MI, .DE, .HK?)"),
                "timestamp": datetime.now().isoformat(),
            }
            _TICKER_VALIDATION_CACHE[sym] = (now, result)
            return result
        except Exception as e:
            err = {"ok": False, "symbol": sym, "error": _api_text(f'Errore yfinance: {str(e)[:200]}', f'yfinance error: {str(e)[:200]}'),
                   "timestamp": datetime.now().isoformat()}
            # audit/11 §5: NIENTE cache negativa per errori transitori di rete — un
            # timeout Yahoo marcava un ticker valido come non validabile per 5 minuti
            return err

    # ============================================================
    # PORTFOLIO ANALYTICS (TIER 1: NAV history, DD, liquidity, HHI, VaR contrib)
    # ============================================================

    @app.get("/portfolio/sectors")
    def get_portfolio_sectors():
        """Esposizione settoriale del book (Quant fase 1a, 23/07): pesi EUR per
        settore, HHI, override dichiarati per ETF/veicoli/DAT, bucket n.d."""
        try:
            from bellomberg.portfolio.portfolio_sectors import compute_sector_exposure
            return compute_sector_exposure()
        except Exception as e:
            raise _err500(e, "get_portfolio_sectors")

    @app.get("/portfolio/attribution")
    def get_portfolio_attribution(period: str = "YTD", force: bool = False):
        """Contribution attribution (Quant fase 1b, 23/07): contributi al rendimento
        del capitale investito per posizione/bucket economico/valuta, linking Carino,
        riconciliazione dichiarata vs TWR ufficiale. period: MTD|YTD|30D|INCEPTION."""
        try:
            from bellomberg.portfolio.portfolio_attribution import compute_attribution
            return compute_attribution(period=(period or "YTD").upper(), force=force)
        except Exception as e:
            raise _err500(e, "get_portfolio_attribution")

    @app.get("/portfolio/tearsheet")
    def get_portfolio_tearsheet(force: bool = False):
        """Tearsheet sul TWR ufficiale (Quant fase 1c, 23/07): mensili, episodi di
        drawdown, rolling vol/Sharpe, metriche advanced_metrics riusate. Cache 10min."""
        try:
            from bellomberg.portfolio.portfolio_tearsheet import compute_tearsheet
            return compute_tearsheet(force=force)
        except Exception as e:
            raise _err500(e, "get_portfolio_tearsheet")

    @app.get("/portfolio/analytics/nav_history")
    def get_nav_history(force: bool = False):
        """Ricostruzione NAV history da trade_history + yfinance prices + FX live."""
        try:
            from bellomberg.portfolio.portfolio_analytics import compute_nav_history
            return compute_nav_history(force=force)
        except Exception as e:
            raise _err500(e, "get_nav_history")

    @app.get("/portfolio/analytics/twr")
    def get_twr(force: bool = False):
        """TWR GIPS flow-adjusted (fix #30, audit 02 par.3): indice base 100,
        drawdown/vol/Sharpe sulla serie TWR, IRR money-weighted, regime per
        tratto (official da nav_snapshots / reconstructed da chiusure) e
        riconciliazione NAV live vs ultimo snapshot. Cache 10 min in twr_engine."""
        try:
            from bellomberg.portfolio.twr_engine import compute_twr_payload
            return compute_twr_payload(force=force)
        except Exception as e:
            raise _err500(e, "get_twr")

    @app.get("/portfolio/analytics/benchmark")
    def get_benchmark(ticker: str = "SPY", force: bool = False):
        """Serie benchmark UFFICIALE in EUR total-return sul calendario TWR
        (voce §9-novodecies n.2, ok PM 25/07): sostituisce lo SPY-EUR
        client-side PROVVISORIO di F2 v3.1. Carry-forward/coverage dichiarati
        nel payload; stessa serie riusata da advanced_metrics per beta/alpha.
        Cache 10 min in benchmark_series."""
        try:
            t = (ticker or "").strip().upper()
            if not t or len(t) > 16:
                raise HTTPException(400, _api_text('ticker non valido', 'Invalid ticker'))
            from bellomberg.market_data.benchmark_series import compute_benchmark_series
            return compute_benchmark_series(ticker=t, force=force)
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "get_benchmark")

    @app.get("/portfolio/analytics/drawdowns")
    def get_drawdowns(force: bool = False):
        """Top-5 drawdowns + current DD (if any)."""
        try:
            from bellomberg.portfolio.portfolio_analytics import compute_drawdowns
            return compute_drawdowns(force=force)
        except Exception as e:
            raise _err500(e, "get_drawdowns")

    @app.get("/portfolio/analytics/liquidity")
    def get_liquidity():
        """Liquidity scores per posizione: days_to_liquidate + green/yellow/red."""
        try:
            from bellomberg.portfolio.portfolio_analytics import compute_liquidity_scores
            return compute_liquidity_scores()
        except Exception as e:
            raise _err500(e, "get_liquidity")

    @app.get("/portfolio/analytics/concentration")
    def get_concentration():
        """HHI by ticker / region / currency + Effective N + top-5."""
        try:
            from bellomberg.portfolio.portfolio_analytics import compute_concentration
            return compute_concentration()
        except Exception as e:
            raise _err500(e, "get_concentration")

    @app.get("/portfolio/analytics/var_contribution")
    def get_var_contribution(lookback_days: int = 252, confidence: float = 0.05):
        """Component VaR per ticker (Jorion 2006). Sum = portfolio VaR."""
        try:
            from bellomberg.portfolio.portfolio_analytics import compute_var_contribution
            return compute_var_contribution(lookback_days=lookback_days, confidence=confidence)
        except Exception as e:
            raise _err500(e, "get_var_contribution")

    @app.post("/prices/update", dependencies=[Depends(require_session), Depends(throttle)])
    def update_prices_endpoint():
        try:
            from bellomberg.cli.price_updater import AUTOMATIC_PRICE_SOURCES, update_all_prices
            db = get_db()
            result = update_all_prices(db, source_order=AUTOMATIC_PRICE_SOURCES,
                                         verbose=False)
            return result
        except Exception as e:
            raise _err500(e, "update_prices_endpoint")

    # Store live subprocess handles so we can cancel them (consigliere_multi runs)
    _CONSIGLIERE_PROCS: Dict[str, subprocess.Popen] = {}
    from bellomberg.api.trade_idea_routes import install_trade_idea_routes, active_paid_reason
    from bellomberg.storage.trade_idea_store import TradeIdeaStore
    install_trade_idea_routes(app, require_session, db_path=valuation_db_path,
        weekly_active=lambda: bool(_CONSIGLIERE_PROCS or any(
            st.get("status") == "running" for st in run_state.runs.values())))
    from bellomberg.api.trade_idea_workspace_routes import install_trade_idea_workspace_routes
    install_trade_idea_workspace_routes(app, require_session, db_path=valuation_db_path)
    from bellomberg.api.weekly_recovery_routes import (
        WeeklyRunOptions, read_worker_outcome, install_weekly_recovery_routes)
    install_weekly_recovery_routes(app, require_session, lambda: get_db())

    @app.post("/consigliere/run", dependencies=[Depends(require_session)])
    def trigger_consigliere(background_tasks: BackgroundTasks, request: Request,
                           options: WeeklyRunOptions | None = None):
        """Lancia un run consigliere in background. Ritorna subito task_id.
        Rifiuta se ce n'e' gia' uno running (anti-duplicate-trigger guard).
        """
        options = options or WeeklyRunOptions(send_email=True)
        if options.resume_memo_id is not None and not options.delivery_only and not options.authorize_new_ai:
            raise HTTPException(428, "La ripresa analitica richiede conferma esplicita delle nuove richieste AI")
        # Anti-duplicate guard: refuse if there's already a running task
        active_running = [tid for tid, st in run_state.runs.items()
                           if st.get("status") == "running"]
        if active_running or _CONSIGLIERE_PROCS:
            existing = active_running[0] if active_running else next(iter(_CONSIGLIERE_PROCS))
            raise HTTPException(
                409,
                _api_text(f'Una run consigliere e gia in corso (task_id={existing}). Fermala prima con POST /consigliere/cancel_all o il pulsante STOP.', f'A consigliere run is already in progress (task_id={existing}). Cancel it first via POST /consigliere/cancel_all or STOP button.')
            )
        try:
            trade_idea_store = TradeIdeaStore(valuation_db_path)
        except (FileNotFoundError, RuntimeError):
            trade_idea_store = None
        paid_reason = active_paid_reason(trade_idea_store)
        if paid_reason:
            raise HTTPException(409, paid_reason)
        if options.resume_memo_id is not None:
            from bellomberg.storage.weekly_run_store import get_weekly_run_status, WeeklyRunBlocked
            try:
                selected = get_weekly_run_status(get_db(), options.resume_memo_id)
            except WeeklyRunBlocked as exc:
                raise HTTPException(404, str(exc)) from exc
            available = selected.get('delivery_recovery_available' if options.delivery_only else 'resume_available')
            if not available:
                raise HTTPException(409, selected.get('blocked_reason') or
                    'La run selezionata non ha fasi mancanti recuperabili in questa modalita')
        # Verifica prima del throttle: un 428/503 non penalizza il click corretto
        # successivo. Il 409 sopra conserva la precedenza storica.
        if not options.delivery_only:
            _require_mandato_run(request)
        throttle(request)
        task_id = "run_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(4)
        run_state.start(task_id)

        def _bg_run():
            log_f = None
            try:
                # Seconda lettura immediatamente prima dello spawn: il file puo' essere
                # cambiato dopo l'accettazione HTTP. In quel caso la task fallisce dichiarata
                # e nessun processo a pagamento viene creato.
                import bellomberg.core.mandato_pm as _mp
                try:
                    if not options.delivery_only:
                        _mp.carica()
                except _mp.MandatoMancante:
                    _refund_throttle(request)
                    raise
                # FIX 13/07: stdout/stderr della run su FILE (prima: PIPE mai letta
                # -> ogni errore della run era INVISIBILE). Append = OneDrive-safe.
                run_log_path = os.path.join(DB_DIR, "consigliere_run.log")
                try:
                    log_f = open(run_log_path, "a", encoding="utf-8",
                                 errors="replace", buffering=1)
                    log_f.write("\n" + "=" * 70 + "\n[" + task_id + "] START "
                                + datetime.now().isoformat(timespec="seconds") + "\n")
                except Exception:
                    log_f = None  # senza log si prosegue comunque
                # Popen (non-blocking) so we can kill it via /cancel
                outcome_path = os.path.join(DB_DIR, 'weekly_outcomes', task_id + '.json')
                os.makedirs(os.path.dirname(outcome_path), exist_ok=True)
                argv = [sys.executable, "-u", "-m", "bellomberg.agents.consigliere_multi",
                        '--outcome', outcome_path, '--task-id', task_id]
                if options.resume_memo_id is not None:
                    argv += ['--resume-memo-id', str(options.resume_memo_id)]
                if options.delivery_only:
                    argv.append('--delivery-only')
                if options.authorize_new_ai:
                    argv.append('--authorize-new-ai')
                if not options.send_email:
                    argv.append('--no-email')
                if getattr(options, 'acknowledge_uncertain_email', False):
                    # Conferma esplicita del PM: re-invio di un'email dall'esito incerto (Opus 5.5, 05/10).
                    argv.append('--acknowledge-uncertain-email')
                proc = subprocess.Popen(
                    argv,
                    stdout=(log_f if log_f else subprocess.DEVNULL),
                    stderr=subprocess.STDOUT, text=True,
                    cwd=str(PROJECT_ROOT), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                _CONSIGLIERE_PROCS[task_id] = proc
                try:
                    # FIX 13/07: era timeout=3600 -> la API AMMAZZAVA ogni run
                    # oltre i 60 min (le run post-25/06 durano 75-90 min:
                    # run 09/07 e 13/07 uccise a meta' R2). Ora 4 ore.
                    proc.wait(timeout=14400)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                rc = proc.returncode
                _CONSIGLIERE_PROCS.pop(task_id, None)
                if log_f:
                    try:
                        log_f.write("[" + task_id + "] END rc=" + str(rc) + " "
                                    + datetime.now().isoformat(timespec="seconds") + "\n")
                        log_f.close()
                    except Exception:
                        pass
                # If we were cancelled, status was already set to 'cancelled' by /cancel
                cur = run_state.get(task_id) or {}
                if cur.get("status") == "cancelled":
                    return
                outcome = read_worker_outcome(outcome_path, task_id, rc)
                cause = outcome.get('first_error') or outcome.get('last_error') or outcome.get('error')
                message = cause.get('message') if isinstance(cause, dict) else cause
                run_state.finish(task_id, success=outcome['status'] == 'completed', error=message)
                run_state.runs[task_id].update(status=outcome['status'], outcome=outcome)
            except Exception as e:
                _CONSIGLIERE_PROCS.pop(task_id, None)
                try:
                    if log_f:
                        log_f.write("[" + task_id + "] EXC " + str(e) + "\n")
                        log_f.close()
                except Exception:
                    pass
                run_state.finish(task_id, success=False, error=str(e))

        background_tasks.add_task(_bg_run)
        return {"task_id": task_id, "status": "running",
                "message": _api_text('Run consigliere avviata. Stato: /consigliere/status/', 'Consigliere run started. Check /consigliere/status/') + task_id}

    @app.post("/consigliere/cancel/{task_id}", dependencies=[Depends(require_session)])
    def cancel_consigliere(task_id: str):
        """Termina un run consigliere in corso. Killa il subprocess + marca lo status."""
        proc = _CONSIGLIERE_PROCS.get(task_id)
        if not proc:
            # Maybe it's not in our handle map but state exists; mark cancelled anyway
            state = run_state.get(task_id)
            if not state:
                raise HTTPException(404, _api_text(f'Task {task_id} non trovato', f'Task {task_id} not found'))
            if state.get("status") in ("completed", "failed", "cancelled"):
                return {"task_id": task_id, "status": state.get("status"),
                        "message": _api_text('Gia terminata', 'Already terminated')}
            # No proc handle but state exists - just mark cancelled
            state["status"] = "cancelled"
            state["finished"] = datetime.now().isoformat()
            state["error"] = "cancelled by user (no proc handle)"
            return {"task_id": task_id, "status": "cancelled",
                    "message": _api_text('Segnata come annullata (nessun processo attivo trovato)', 'Marked cancelled (no live proc handle found)')}

        try:
            proc.kill()  # SIGKILL on POSIX, TerminateProcess on Windows
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: pass
        except Exception as e:
            raise _err500(e, "cancel_consigliere", _api_text('Impossibile terminare il processo', 'Failed to kill process'))
        finally:
            _CONSIGLIERE_PROCS.pop(task_id, None)

        state = run_state.get(task_id) or {}
        state["status"] = "cancelled"
        state["finished"] = datetime.now().isoformat()
        state["error"] = "cancelled by user"
        run_state.runs[task_id] = state
        return {"task_id": task_id, "status": "cancelled",
                "message": _api_text('Processo terminato, run conclusa.', 'Subprocess killed, run terminated.')}

    @app.get("/consigliere/status/{task_id}")
    def consigliere_status(task_id: str):
        state = run_state.get(task_id)
        if not state:
            raise HTTPException(404, _api_text(f'Task {task_id} non trovato', f'Task {task_id} not found'))
        return state

    @app.get("/consigliere/active")
    def consigliere_active():
        """Returns the most recently started run that is still 'running'.
        Used by frontend STOP button when localStorage has no task_id (after restart).
        """
        active = [(tid, st) for tid, st in run_state.runs.items()
                   if st.get("status") == "running"]
        if not active:
            return {"active": False}
        # Most recent first
        active.sort(key=lambda kv: kv[1].get("started", ""), reverse=True)
        tid, st = active[0]
        return {
            "active": True,
            "task_id": tid,
            "started": st.get("started"),
            "has_proc_handle": tid in _CONSIGLIERE_PROCS,
        }

    @app.post("/consigliere/cancel_all", dependencies=[Depends(require_session)])
    def cancel_all_consigliere():
        """Kill all currently running consigliere subprocesses + orphans.

        Tries two strategies:
        1. Kill subprocesses we have handles for (this session's runs)
        2. SCAN all python.exe processes via psutil to find orphans running
           consigliere_multi.py (from previous backend sessions, lost on restart)
        """
        killed = []
        errors = []
        # Strategy 1: subprocess handles we own
        for tid, proc in list(_CONSIGLIERE_PROCS.items()):
            try:
                proc.kill()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: pass
                killed.append({"task_id": tid, "source": "handle"})
            except Exception as e:
                errors.append({"task_id": tid, "error": str(e)})
            finally:
                _CONSIGLIERE_PROCS.pop(tid, None)
        # Mark all running states as cancelled
        for tid, st in run_state.runs.items():
            if st.get("status") == "running":
                st["status"] = "cancelled"
                st["finished"] = datetime.now().isoformat()
                st["error"] = "cancelled by user (cancel_all)"

        # Strategy 2: scan orphan processes via psutil
        try:
            import psutil
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    # R-8 C6 (06/10, Opus 5.5): riconoscimento sulla FORMA del comando. Prima
                    # cercava «consigliere_multi.py» nei soli processi python, ma la run parte
                    # con `-m bellomberg.agents.consigliere_multi` (qui sopra, /consigliere/run):
                    # dopo un riavvio del backend l'orfano non veniva MAI trovato e lo STOP non
                    # fermava la run; veniva ucciso invece un py_compile/pytest che nominava il file.
                    from bellomberg.core.processi_run import e_run_consigliere
                    argv = p.info.get("cmdline") or []
                    cmdline = " ".join(argv)
                    if e_run_consigliere(argv):
                        p.kill()
                        killed.append({"pid": p.info["pid"], "source": "psutil_scan",
                                       "cmdline": cmdline[:120]})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                except Exception as e:
                    errors.append({"pid": getattr(p, "pid", "?"), "error": str(e)})
        except ImportError:
            errors.append({"warning": _api_text('psutil non installato: ricerca processi orfani non disponibile. Installare psutil per abilitarla.', 'psutil not installed - cannot scan orphans. pip install psutil to enable.')})
        return {"killed": killed, "errors": errors, "n_killed": len(killed)}

    # ===== AGENTS =====
    @app.get("/agents/list")
    def get_agents_list():
        """Lista degli agenti disponibili (capo + 6 specialist; Event Desk = fusione News+Politics 15/07)."""
        try:
            from bellomberg.agents.chat_engine import AGENT_DISPLAY_NAMES, LEGACY_AGENT_IDS
        except Exception:
            AGENT_DISPLAY_NAMES = {
                "capo": "Capo", "macro": "Macro", "options": "Options Flow", "quant": "Quant",
                "fundamentals": "Fundamentals", "crypto": "Crypto", "eventdesk": "Event Desk",
            }
            LEGACY_AGENT_IDS = set()
        # modello chat DERIVATO dalla sorgente vera (26/07: erano 10 hardcode sonnet-4-6 —
        # la stessa classe di drift che l'audit ha beccato sul footer). 05/09 (ordine PM):
        # la sorgente e' il .env, UNO PER AGENTE (CHAT_<AGENTE>_MODEL con precedenza su
        # CHAT_MODEL); variabile assente = «n.d. (VARIABILE assente)», mai un nome inventato.
        try:
            from bellomberg.core.llm_client import modello_o_buco as _mob
        except Exception as e:
            _mob = lambda *a, _cause=str(e), **k: _api_text('n.d. (llm_client non importabile: ', 'n/a (llm_client cannot be imported: ') + _cause + ")"
        from bellomberg.core.presentation import message
        AGENT_META = {
            "capo":         {"role": message("Analista senior PM", "Senior Analyst PM"), "color": "#ff9500"},
            "macro":        {"role": message("Stratega macroeconomico", "Macro Strategist"), "color": "#ffc760"},
            "quant":        {"role": message("Analista quantitativo", "Quant"), "color": "#00e5ff"},
            "options":      {"role": message("Flussi opzioni", "Options Flow"), "color": "#a78bfa"},
            "fundamentals": {"role": message("Analista fondamentale", "Fundamentals Analyst"), "color": "#00ff95"},
            "crypto":       {"role": message("Specialista cripto", "Crypto Specialist"), "color": "#fbbf24"},
            "eventdesk":    {"role": message("Eventi (Notizie+Geopolitica)", "Event Desk (News+Geo)"), "color": "#f472b6"},
            # legacy pre-fusione (lookup per thread/tool-log vecchi; MAI enumerati):
            "politics":     {"role": message("Specialista geopolitico", "Geopolitics Specialist"), "color": "#f472b6"},
            "news":         {"role": message("Notizie / Catalizzatori", "News / Catalyst"), "color": "#94a3b8"},
        }
        agents = []
        for aid, name in AGENT_DISPLAY_NAMES.items():
            if aid in LEGACY_AGENT_IDS:
                continue  # fusione 15/07: i desk ritirati non compaiono nella rail
            meta = AGENT_META.get(aid, {"role": message("Specialista", "Specialist"), "color": "#8aa0b6"})
            agents.append({"id": aid, "name": name, **meta, "model": _mob("chat", aid)})
        # ENGINES additivo (26/07, audit/21 App. E): il footer ENGINE derivava il
        # motore dal "model" per-agente — che e' il modello della CHAT (Sonnet),
        # mentre il comitato gira su Opus. Qui le costanti VERE importate dai
        # moduli proprietari (mai hardcodate: se il PM cambia modello, questo
        # campo dice la verita' da solo). Import falliti = error dichiarato.
        # 05/09 (ordine PM): ogni funzione ha il SUO modello nel .env; qui i valori risolti
        # da llm_client (stessa funzione che usano i call site) o «n.d. (VARIABILE assente)».
        # Chiavi storiche conservate per il frontend (chat, committee_r1_r2, committee_r0,
        # capo, red_team, synthesizer, news_classifier) + chiavi nuove additive: un modello
        # per desk (committee_<desk>), reflection, action_extractor, briefing.
        engines = {}
        try:
            from bellomberg.core.llm_client import modello_o_buco as _mob, DESK as _DESK
            engines["chat"] = _mob("chat")
            engines["committee_r1_r2"] = _mob("consigliere")            # la base; per desk sotto
            engines["committee_r0"] = _mob("consigliere", round_n=0)
            for _d in _DESK:
                engines["committee_" + _d] = _mob("consigliere", _d)
            engines["capo"] = _mob("capo")
            engines["red_team"] = _mob("red_team")
            engines["reflection"] = _mob("reflection")
            engines["action_extractor"] = _mob("action_extractor")
            # chiave storica (era MODEL_SYNTHESIZER, condiviso da red team/reflection/
            # estrattore): oggi ognuno ha il suo; qui = reflection, i tre sopra dicono il vero
            engines["synthesizer"] = engines["reflection"]
            engines["briefing"] = _mob("briefing")
            engines["news_classifier"] = _mob("news_classifier")
        except Exception as e:
            engines["engines_error"] = str(e)
        return {"agents": agents, "engines": engines}

    # Ancorato al file, non alla CWD (bug PM 15/07: backend avviato con cwd
    # diversa leggeva un heartbeat fantasma in un'altra data\ -> run inesistente
    # mostrata su Agents Live). DEVE combaciare con Blackboard.HEARTBEAT_PATH.
    AGENTS_LIVE_PATH = os.path.join(DB_DIR, "current_run.json")   # B4: stessa costante del heartbeat

    def _fatti_processo(state):
        """R-2 F3/F4 (06/10, Opus 5.5): fatti sul processo della run per `esito_run`.
        - heartbeat_precede_processo: un processo consigliere avviato da questo backend e'
          vivo e il file e' piu' vecchio di lui (mtime < avvio del processo; avvio da psutil,
          o dal run_state se il pid non c'e'; se non si sa quando e' partito si assume che il
          file sia della run precedente, salvo che il file dica running=true);
        - pid_vivo: il pid scritto nel heartbeat esiste ED e' nato prima dell'avvio dichiarato
          della run (un pid riusato da un altro processo non conta come vivo); None se il
          heartbeat non dichiara il pid o psutil non risponde;
        - processi_backend_vivi, annullata_da_utente (ultima run di questo backend fermata)."""
        out = {"heartbeat_precede_processo": None, "pid_vivo": None,
               "processi_backend_vivi": None, "annullata_da_utente": False}
        try:
            vivi = [(tid, p) for tid, p in list(_CONSIGLIERE_PROCS.items()) if p.poll() is None]
            out["processi_backend_vivi"] = len(vivi)
            if vivi:
                try:
                    mtime = os.path.getmtime(AGENTS_LIVE_PATH)
                except OSError:
                    mtime = None
                avvii = []
                for tid, p in vivi:
                    t0 = None
                    try:
                        import psutil
                        if not isinstance(getattr(p, "pid", None), int):
                            raise ValueError("pid assente")   # psutil.Process(None) = SE STESSO
                        t0 = psutil.Process(p.pid).create_time()
                    except Exception:
                        st = run_state.get(tid) or {}
                        try:
                            t0 = datetime.fromisoformat(str(st.get("started"))).timestamp()
                        except Exception:
                            t0 = None
                    avvii.append(t0)
                if any(t0 is None for t0 in avvii):
                    out["heartbeat_precede_processo"] = state.get("running") is not True
                else:
                    out["heartbeat_precede_processo"] = mtime is None or mtime < max(avvii)
            runs = sorted(run_state.runs.values(), key=lambda st: str(st.get("started") or ""))
            out["annullata_da_utente"] = bool(runs) and runs[-1].get("status") == "cancelled"
            pid = state.get("pid")
            if isinstance(pid, int) and not isinstance(pid, bool):
                try:
                    import psutil
                    if not psutil.pid_exists(pid):
                        out["pid_vivo"] = False
                    else:
                        nato = psutil.Process(pid).create_time()
                        avvio = datetime.fromisoformat(str(state.get("start_time"))).timestamp()
                        out["pid_vivo"] = nato <= avvio + 5
                except Exception:
                    out["pid_vivo"] = None
        except Exception:
            pass
        return out

    @app.get("/agents/live")
    def get_agents_live():
        """Stato live del consigliere multi-agent (specialist_status, tool_log, reports).
        Hardening #32 (watchdog): aggiunge stale_seconds + stale_warning se l'heartbeat
        (updated_at scritto da specialists/base.py) e' fermo da >600s con run running.

        handoff-4 voce 2 (05/10, Opus 5.5): ogni risposta porta `esito_run`, l'esito VERO
        della run per la pagina (specialists/base.py: esito_run, valori in ESITI_RUN), letto
        dal registro della run / technical_status e incrociato col memo nel DB in sola
        lettura. La pagina NON deve piu' dedurre «completata» da running=false + memo_id."""
        from bellomberg.agents.specialists.base import esito_run as _esito_run, stato_memo_nel_db as _stato_memo
        _nessuna = {"stato": "nessuna_run", "memo_consegnato": False, "motivo": None,
                    "ripresa_disponibile": None, "ripresa": None, "fonte": "nessun_heartbeat",
                    "stato_memo_db": "non_letto"}
        try:
            import json as _json
            if not os.path.exists(AGENTS_LIVE_PATH):
                return {"running": False, "message": _api_text('Nessuna run attiva.', 'No active run.'),
                        "esito_run": _nessuna}
            # 27/08 (review del lotto heartbeat, changelog (90)): il consigliere
            # scrive il file con temporaneo + os.replace, e su Windows nella
            # finestra del replace (stimata ~0,01-0,3 ms secondo la cadenza) una
            # open("r") concorrente prende PermissionError: prima usciva come
            # «read error» e F4 perdeva stato e orologio per un poll. Tre letture
            # con pausa breve (20 + 30 ms) prima di dichiarare il guasto; se resta
            # illeggibile lo dice col suo nome (`heartbeat: "illeggibile"`),
            # distinguibile da «nessuna run». ValueError copre il JSONDecodeError
            # di un file a meta'. Misura della review (lettore in un altro
            # processo, 62.173 poll a tre cadenze): 0 «illeggibile», 26
            # PermissionError tutte recuperate alla prima rilettura.
            state = None
            _ultimo = None
            for _pausa in (0.02, 0.03, None):
                try:
                    with open(AGENTS_LIVE_PATH, "r", encoding="utf-8") as f:
                        state = _json.load(f)
                    break
                except (PermissionError, FileNotFoundError, ValueError) as e:
                    _ultimo = e
                    if _pausa is not None:
                        time.sleep(_pausa)
            if state is None:
                if isinstance(_ultimo, FileNotFoundError) and not os.path.exists(AGENTS_LIVE_PATH):
                    # sparito fra exists() e open(): un reset legittimo
                    # (POST /agents/live/reset), non un guasto
                    return {"running": False, "message": _api_text('Nessuna run attiva.', 'No active run.'),
                            "esito_run": _nessuna}
                return {"running": False, "heartbeat": "illeggibile",
                        "message": _api_text('errore lettura: heartbeat illeggibile dopo 3 letture (%s: %s)', 'Read error: heartbeat unreadable after 3 reads (%s: %s)')
                                   % (type(_ultimo).__name__, str(_ultimo)[:160]),
                        "esito_run": {**_esito_run(None), "fonte": "heartbeat_illeggibile",
                                      "motivo": "heartbeat illeggibile dopo 3 letture (" + type(_ultimo).__name__ + ")"}}
            if not isinstance(state, dict):
                # JSON valido ma non un oggetto: prima usciva com'era (il try
                # sotto inghiottiva l'AttributeError) e il frontend riceveva una
                # lista al posto dello stato (review 27/08, seconda passata)
                return {"running": False, "heartbeat": "illeggibile",
                        "message": _api_text("errore lettura: heartbeat non e' un oggetto JSON (%s)", 'Read error: heartbeat is not a JSON object (%s)')
                                   % type(state).__name__,
                        "esito_run": {**_esito_run(state), "fonte": "heartbeat_illeggibile"}}
            try:
                upd = state.get("updated_at")
                if upd:
                    stale_s = max(0, int((datetime.now() - datetime.fromisoformat(str(upd))).total_seconds()))
                    state["stale_seconds"] = stale_s
                    state["stale_warning"] = bool(state.get("running")) and stale_s > 600
            except Exception:
                pass
            # Esito vero (dopo stale_warning, che lo informa). Il memo si legge nel DB solo
            # a run chiusa: a run viva e' '[IN PROGRESS]' per definizione.
            try:
                _memo = ("non_letto" if state.get("running") is True
                         else _stato_memo(state.get("memo_id"), SQLITE_PATH))
                state["esito_run"] = _esito_run(state, _memo, _fatti_processo(state))
            except Exception as _e:
                state["esito_run"] = {**_esito_run(None), "fonte": "errore_calcolo",
                                      "motivo": "esito non calcolabile: " + type(_e).__name__}
            return state
        except Exception as e:
            # qualunque altro guasto (es. un OSError fuori dalla tupla del
            # retry): stessa chiave dichiarata, cosi' il frontend distingue
            # sempre «illeggibile» da «nessuna run» (review 27/08, seconda
            # passata; raggiunto dal test con OSError(22))
            return {"running": False, "heartbeat": "illeggibile",
                    "message": _api_text(f'Errore lettura: {e}', f'read error: {e}'),
                    "esito_run": {**_esito_run(None), "fonte": "heartbeat_illeggibile",
                                  "motivo": "heartbeat illeggibile (" + type(e).__name__ + ")"}}

    # ============================================================
    # MANDATO DEL PM (criterio (5), lotto B) — la pagina Mandato e Diario (F18).
    # ============================================================
    @app.get("/mandato", dependencies=[Depends(require_session)])
    def get_mandato():
        """Il mandato del PM come lo consuma la pagina Mandato e Diario (F18).

        IL TOKEN QUI E' UNA DEROGA DICHIARATA alla regola di casa «i GET restano
        liberi (bind 127.0.0.1)» (decisione PM 06/09): questo e' l'unico GET che
        rende il PROFILO DI RISCHIO PRIVATO del PM, non dati di mercato. Chi la
        togliesse per coerenza con gli altri GET aprirebbe quel profilo a chiunque
        arrivi sulla porta — c'e' un test che cade apposta.

        Tre esiti, e la differenza fra il secondo e il terzo e' quella che conta:
          - mandato valido           -> 200 coi valori, l'impronta e lo schema;
          - mai compilato / a meta'  -> 200 con `dichiarato: false` e la CAUSA: sono
            stati che la pagina deve DISEGNARE (modulo vuoto, campi mancanti), non
            errori;
          - file in uso / illeggibile / esempio del repo rotto -> 503 con la causa.
            «In uso» vuol dire che il file C'E' ED E' PIENO: se lo appiattissimo su
            `dichiarato: false` la pagina aprirebbe il primo accesso a schermo intero
            SOPRA un mandato buono e il PM crederebbe di aver perso tutto.

        Il mandato si legge DAL DISCO dentro l'handler, a ogni richiesta: mai a
        import-time (guardia AST in tests/test_mandato_pm.py).
        """
        import bellomberg.core.mandato_pm as _mp
        from bellomberg.core.presentation import error_text as _mandato_error_text
        try:
            corpo, non_leggibile = _mp.stato_per_api()
        except Exception as e:
            # L'API non ha exception_handler: un guasto che sfugge diventa un 500 muto
            # che scavalca anche la riga di log. Ogni ramo cattura per conto suo.
            raise _err500(e, "get_mandato", _api_text('lettura del mandato del PM', 'Reading the PM mandate'))
        if non_leggibile:
            raise HTTPException(503, _api_text('mandato non leggibile (%s): %s', 'Mandate unreadable (%s): %s')
                                     % (non_leggibile, corpo.get("dettaglio") or ""))
        return corpo

    @app.put("/mandato", dependencies=[Depends(require_session)])
    def put_mandato(corpo=Body(...)):
        """Riscrive il mandato del PM dalla pagina Mandato, e SOLO lui.

        E' il primo codice di produzione che scrive sul profilo di rischio su cui gira
        il comitato. Tre cose lo rendono sicuro, e sono tutte e tre misurate:

          - **la risposta e' la RILETTURA DAL DISCO**, nella stessa forma del GET: se la
            scrittura fosse andata storta a meta', il PM lo vede subito invece di credere
            di aver salvato. Una forma sola per i due verbi vuol dire che la pagina
            ridisegna lo stesso stato dopo aver letto e dopo aver salvato;
          - **un modulo non valido NON TOCCA IL FILE**: `valida()` gira prima di qualunque
            scrittura, e il 422 elenca OGNI campo sbagliato — una riga `campo: motivo` per
            campo, separate da `\n`, cosi' la pagina le mette accanto alle caselle giuste;
          - **`origine` non si accetta dal client**: la MISURA il backend confrontando i
            valori col profilo di esempio. Accettarla vorrebbe dire lasciar spegnere dal
            browser il banner «profilo di esempio, non personalizzato» su un mandato che
            di esempio lo e' davvero.

        `detail` resta una STRINGA (v. `_err500` a riga 74): il frontend la rende verbatim
        e un dict stamperebbe «[object Object]» al posto del motivo.

        DUE LIMITI DICHIARATI, che non curo qui:
          - un corpo che non e' JSON valido non arriva a questa funzione: lo rifiuta
            FastAPI col suo 422, il cui `detail` e' una LISTA. Vale per ogni endpoint
            dell'app (non c'e' exception_handler) e non e' un difetto del mandato;
          - `dichiarato_il` viene validato da `valida()` e poi RISCRITTO da `salva()` con
            la data di oggi. Un client che mandasse una data in un altro formato prende un
            422 su un campo che il server avrebbe comunque riscritto. La pagina manda i
            sette blocchi e basta, quindi non ci passa.
        """
        import bellomberg.core.mandato_pm as _mp
        from bellomberg.core.presentation import error_text as _mandato_error_text
        if isinstance(corpo, dict) and corpo.get("origine") is not None:
            raise HTTPException(422, _api_text('origine: non si dichiara dal client — la misura il backend confrontando i valori col profilo di esempio. Togli `origine` dal corpo e manda i sette blocchi.', 'origin is measured by the backend by comparing values with the example profile; the client cannot declare it. Remove `origine` from the request and send the seven sections.'))
        _m, errori = _mp.valida(corpo)
        if errori:
            # Una riga per campo: la pagina divide su `\n`. Non un dict {campo: motivo},
            # che perderebbe un errore — due coerenze diverse possono nominare lo STESSO
            # campo nella stessa risposta (`var99_1g_pct` contro drawdown e contro stress).
            raise HTTPException(422, "\n".join(errori))
        try:
            _mp.salva(corpo)
        except ValueError as e:
            # `salva` rivalida per conto suo: se arriva qui vuol dire che il mandato e'
            # diventato invalido fra le due chiamate. Resta un rifiuto, non un guasto.
            raise HTTPException(422, _mandato_error_text(e))
        except _mp.MandatoMancante as e:
            # Il messaggio nomina la CAUSA e MAI un percorso: con causa «esempio» il
            # `.percorso` di questa eccezione e' il file DELL'ESEMPIO del repo (guasto
            # dell'installazione), non il mandato del PM — scriverlo darebbe la colpa
            # al file sbagliato.
            raise HTTPException(503, _api_text('mandato non salvato (%s): %s', 'Mandate not saved (%s): %s')
                                     % (e.causa, e.dettaglio or _api_text('riprovare fra un istante', 'try again shortly')))
        except (PermissionError, OSError) as e:
            # `salva()` rilegge il file precedente per farne il backup SENZA la ritenta a
            # 3 tentativi che protegge `carica()`: su Windows quel punto solleva un
            # PermissionError nudo mentre un'altra scrittura e' in corso. E' un «riprova».
            raise HTTPException(503, _api_text('mandato in uso (%s: %s): riprovare fra un istante', 'Mandate in use (%s: %s): try again shortly')
                                     % (type(e).__name__, _mandato_error_text(e)[:160]))
        except Exception as e:
            raise _err500(e, "put_mandato", _api_text('scrittura del mandato del PM', 'Saving the PM mandate'))
        try:
            nuovo, non_leggibile_dopo = _mp.stato_per_api()
        except Exception as e:
            raise _err500(e, "put_mandato", _api_text('rilettura del mandato dopo il salvataggio', 'Reading back the saved mandate'))
        if non_leggibile_dopo:
            # Salvato ma non rileggibile: lo si DICE, invece di rendere un corpo vuoto che
            # la pagina disegnerebbe come «mandato perso».
            raise HTTPException(503, _api_text('mandato SALVATO ma non rileggibile subito dopo (%s): %s', 'Mandate SAVED but immediate readback failed (%s): %s')
                                     % (non_leggibile_dopo, nuovo.get("dettaglio") or ""))
        return nuovo

    @app.get("/mandato/anteprima", dependencies=[Depends(require_session)])
    def get_mandato_anteprima():
        """Testo esatto che i modelli ricevono dal mandato salvato."""
        import bellomberg.core.mandato_pm as _mp
        from bellomberg.core.presentation import error_text as _mandato_error_text
        try:
            return _mp.anteprima(_mp.carica())
        except _mp.MandatoMancante as e:
            status = 428 if e.causa in ("assente", "incompleto") else 503
            raise HTTPException(status, _api_text('mandato non pronto (%s): %s', 'Mandate not ready (%s): %s') % (e.causa, _mandato_error_text(e)))
        except ValueError as e:
            raise HTTPException(422, _mandato_error_text(e))
        except Exception as e:
            raise _err500(e, "get_mandato_anteprima", _api_text('anteprima del mandato salvato', 'Previewing the saved mandate'))

    @app.post("/mandato/anteprima", dependencies=[Depends(require_session)])
    def post_mandato_anteprima(corpo=Body(...)):
        """Anteprima di valori non salvati: valida prima di costruire qualunque prompt."""
        import bellomberg.core.mandato_pm as _mp
        from bellomberg.core.presentation import error_text as _mandato_error_text
        if isinstance(corpo, dict) and corpo.get("origine") is not None:
            raise HTTPException(422, _api_text('origine: non si dichiara dal client - la misura il backend', 'origin is measured by the backend, not declared by the client'))
        try:
            return _mp.anteprima(corpo)
        except ValueError as e:
            raise HTTPException(422, _mandato_error_text(e))
        except _mp.MandatoMancante as e:
            raise HTTPException(503, _api_text('mandato non leggibile (%s): %s', 'Mandate unreadable (%s): %s') % (e.causa, _mandato_error_text(e)))
        except Exception as e:
            raise _err500(e, "post_mandato_anteprima", _api_text('anteprima del mandato non salvato', 'Previewing the unsaved mandate'))

    @app.get("/agents/scorecard")
    def get_agents_scorecard():
        """TRACK RECORD del comitato (scorekeeper #190/#211) — voce I-3.

        Apre al PM una misura che finora viveva SOLO dentro i prompt degli
        agenti (`grep scorekeeper` su questo file e su app/src dava 0): esito di
        mercato delle call passate, per azione / confidence / specialista, con
        IC 95% su ogni aggregato.

        NON RICALCOLA MAI — e non e' pigrizia: il calcolo apre 32 serie prezzi
        (un endpoint HTTP non lo fa) e fatto dall'ambiente sbagliato ha gia'
        avvelenato la misura due volte. Il ricalcolo e' del processo della run
        (consigliere_multi.py, force=True). Qui si legge lo snapshot e si
        DICHIARA quanto e' vecchio, se e' degradato o se non c'e'.
        """
        try:
            from bellomberg.agents.scorekeeper import scorecard_for_api
            return scorecard_for_api()
        except Exception as e:
            raise _err500(e, "get_agents_scorecard",
                          _api_text('lettura snapshot track record', 'Reading the track record snapshot'))

    @app.post("/agents/live/reset", dependencies=[Depends(require_session)])
    def reset_agents_live():
        """Wipe heartbeat file - usato da STOP button per ripristinare lo stato."""
        try:
            if os.path.exists(AGENTS_LIVE_PATH):
                os.remove(AGENTS_LIVE_PATH)
                return {"ok": True, "message": _api_text('heartbeat azzerato', 'heartbeat cleared')}
            return {"ok": True, "message": _api_text('nessun heartbeat da azzerare', 'no heartbeat to clear')}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/chat/{agent_id}/sessions")
    def list_chat_sessions(agent_id: str, limit: int = 30):
        try:
            from bellomberg.agents.chat_engine import list_sessions
            return {"sessions": list_sessions(agent_id, limit=limit)}
        except Exception as e:
            raise _err500(e, "list_chat_sessions")

    class _CreateChatBody(BaseModel):
        agent_id: str
        title: Optional[str] = None

    @app.post("/chat/sessions", dependencies=[Depends(require_session)])
    def create_chat_session(body: _CreateChatBody):
        try:
            from bellomberg.agents.chat_engine import create_session, AGENT_DISPLAY_NAMES
            sid = create_session(body.agent_id, body.title)
            return {
                "session_id": sid,
                "agent_id": body.agent_id,
                "agent_name": AGENT_DISPLAY_NAMES.get(body.agent_id, body.agent_id),
                "title": body.title or _api_text(f'Chat con {AGENT_DISPLAY_NAMES.get(body.agent_id, body.agent_id)}', f'Chat with {AGENT_DISPLAY_NAMES.get(body.agent_id, body.agent_id)}'),
            }
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise _err500(e, "create_chat_session")

    @app.get("/chat/sessions/{session_id}")
    def get_chat_session(session_id: int):
        try:
            from bellomberg.agents.chat_engine import get_session_info, get_messages
            info = get_session_info(session_id)
            if not info:
                raise HTTPException(404, _api_text(f'Sessione {session_id} non trovato', f'Session {session_id} not found'))
            msgs = get_messages(session_id)
            return {
                **info,
                "messages": msgs,
                # n.5 del ponte (26/07 sera): tokens_in/tokens_out ORA arrivano
                # (erano in tabella e valorizzati su tutte le righe assistant, ma
                # la SELECT non li restituiva). Il caveat viaggia col dato, una
                # volta sola, invece che per riga: `tokens_in` e' il RESTO non
                # cachato (usage.input_tokens con il prompt caching acceso), non
                # l'input totale. Da rendere se si mostra il numero.
                "tokens_semantica": {
                    "tokens_in": (_api_text("token di input NON cachati (usage.input_tokens): in chat il prompt caching e' sempre attivo, quindi l'input TOTALE e' tokens_in + cache_read + cache_write", 'NON-cached input tokens (usage.input_tokens): chat always uses prompt caching; TOTAL input is tokens_in + cache_read + cache_write')),
                    "tokens_out": _api_text('token di output, somma sulle iterazioni del tool loop', 'Output tokens, summed across tool loop iterations'),
                    "cache_read_cache_write": (_api_text("NON in tabella (sarebbe una migrazione): viaggiano sull'evento `done` dello stream, insieme al costo in EUR", 'NOT stored in this table: returned in the stream `done` event together with EUR cost')),
                    "costo": (_api_text("il costo VERO di una risposta e' su `done.cost_eur`; sommare i tokens_in d'archivio da' un SOTTO-conteggio", 'Actual response cost is in `done.cost_eur`; summing archived tokens_in UNDERCOUNTS input')),
                },
            }
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "get_chat_session")

    class _PatchChatBody(BaseModel):
        title: str

    @app.patch("/chat/sessions/{session_id}", dependencies=[Depends(require_session)])
    def patch_chat_session(session_id: int, body: _PatchChatBody):
        """Rinomina una sessione (richiesta n.1 del frontend, decisa dal PM).

        Contratto come da ponte: `{title}` -> `{ok, id, title}`; 400 su titolo
        vuoto o > 120 char, 404 su sessione inesistente. Il titolo torna
        NORMALIZZATO (spazi collassati): il frontend deve renderizzare quello,
        non quello che ha spedito.
        """
        try:
            from bellomberg.agents.chat_engine import update_session_title
            res = update_session_title(session_id, body.title)
            if res is None:
                raise HTTPException(404, _api_text(f'Sessione {session_id} non trovato', f'Session {session_id} not found'))
            return res
        except ValueError as e:
            raise HTTPException(400, str(e))
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "patch_chat_session")

    @app.delete("/chat/sessions/{session_id}", dependencies=[Depends(require_session)])
    def delete_chat_session(session_id: int):
        try:
            from bellomberg.agents.chat_engine import delete_session
            delete_session(session_id)
            return {"ok": True, "session_id": session_id}
        except Exception as e:
            raise _err500(e, "delete_chat_session")

    class _ChatStreamBody(BaseModel):
        message: str

    @app.post("/chat/sessions/{session_id}/stream", dependencies=[Depends(require_session)])
    async def stream_chat_endpoint(session_id: int, body: _ChatStreamBody):
        try:
            from bellomberg.agents.chat_engine import stream_chat, get_session_info
            info = get_session_info(session_id)
            if not info:
                raise HTTPException(404, _api_text(f'Sessione {session_id} non trovato', f'Session {session_id} not found'))
            agent_id = info.get("specialist") or info.get("agent_id")
            if not agent_id:
                raise HTTPException(400, _api_text('Sessione priva di agent_id', 'Session missing agent_id'))
            return StreamingResponse(
                stream_chat(session_id, agent_id, body.message),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
            )
        except HTTPException:
            raise
        except Exception as e:
            raise _err500(e, "stream_chat_endpoint")


def api_port(env=None):
    """Porta backend configurabile; valori invalidi falliscono prima del listen."""
    raw = (os.environ if env is None else env).get("BELLOMBERG_API_PORT", "8765")
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(_api_text('BELLOMBERG_API_PORT deve essere un intero tra 1024 e 65535', 'BELLOMBERG_API_PORT must be an integer between 1024 and 65535')) from exc
    if not 1024 <= port <= 65535:
        raise ValueError(_api_text('BELLOMBERG_API_PORT deve essere tra 1024 e 65535', 'BELLOMBERG_API_PORT must be between 1024 and 65535'))
    return port


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bellomberg.api.bellomberg_api:app", host="127.0.0.1", port=api_port(),
                log_level="info", reload=False)
