"""Single-candidate committee. The weekly orchestrator and its side effects stay separate."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
import json
import logging
import os
import re
import threading
import uuid
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from bellomberg.core.llm_client import OpenRouterClient, costruisci_corpo
from bellomberg.core.unbilled import provably_unbilled as _core_provably_unbilled
from bellomberg.core.research_analysis import (RESEARCH_ANALYSIS_MODE, is_research_mode,
    seal_research_thesis, research_reference, research_context)
from bellomberg.core.trade_idea_policy import (RESEARCH_POLICIES, EXECUTION_POLICY_V3, EXECUTION_POLICY_V4, execution_policy,
    role_effort, role_thinking, output_cap as policy_output_cap,
    report_quality_sufficient as _quality_sufficient)


# DECISIONE PM 06/10 (MOD-TI, Opus 5.5): i modelli della Trade Idea si scelgono nel .env
# (MODEL_ENV sotto) come quelli della run settimanale. Questi sono i DEFAULT quando la
# variabile e' assente o vuota. La scelta si CONGELA nel contratto della run all'accettazione
# (models_json + catalog_snapshot_json): durante l'esecuzione e alla ripresa vale SEMPRE il
# contratto, mai il .env corrente (v. model_for_role con `contract`).
MODEL_IDS = {
    "specialist": "meta/muse-spark-1.3",
    "red_team": "meta/muse-spark-1.3",
    "capo": "anthropic/claude-opus-5.5",
    "aux": "meta/muse-spark-1.3",
}
# Slug OpenRouter «fornitore/modello» (variante `:exacto`/`:free` ammessa).
_MODEL_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*(?::[a-z0-9._-]+)?\Z")
MODEL_ENV = {
    "specialist": "TRADE_IDEA_SPECIALIST_MODEL",
    "red_team": "TRADE_IDEA_RED_TEAM_MODEL",
    "capo": "TRADE_IDEA_CAPO_MODEL",
    "aux": "TRADE_IDEA_AUX_MODEL",
}
# Letture col nome LETTERALE (test_env_example censisce le getenv con la stringa scritta): stesse chiavi di MODEL_ENV.
_MODEL_ENV_READ = {
    "specialist": lambda: os.getenv("TRADE_IDEA_SPECIALIST_MODEL"),
    "red_team": lambda: os.getenv("TRADE_IDEA_RED_TEAM_MODEL"),
    "capo": lambda: os.getenv("TRADE_IDEA_CAPO_MODEL"),
    "aux": lambda: os.getenv("TRADE_IDEA_AUX_MODEL"),
}
MODEL_EFFORT = {role: "max" for role in MODEL_IDS}
VIEW_SOURCES = frozenset(("manual", "market", "favorite_note", "reused_run"))
TRADE_IDEA_DESKS = ("macro", "eventdesk", "crypto", "fundamentals", "quant", "options")
MODEL_CONSULTATION_LIMIT = 10
_TICKER_RE = re.compile(r"[A-Z0-9^][A-Z0-9.^=_/-]{0,39}\Z")
_CATALOG_URL = "https://openrouter.ai/api/v1/models"
_CATALOG_LOCK = threading.Lock()
_CATALOG_CACHE = (0.0, None)


class PaidRunBusy(RuntimeError):
    pass


@contextmanager
def exclusive_paid_run(path=None):
    """One OS-held byte lock for weekly and Trade Idea paid committee processes."""
    from bellomberg.core.paths import DATA_DIR, SQLITE_PATH
    from pathlib import Path
    target = Path(path) if path is not None else DATA_DIR / "committee_paid_run.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise PaidRunBusy("un'altra run pagata e' attiva") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise PaidRunBusy("un'altra run pagata e' attiva") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def paid_run_exclusive(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with exclusive_paid_run():
            return fn(*args, **kwargs)
    return wrapped


def paid_run_is_active():
    from bellomberg.core.paths import DATA_DIR
    from pathlib import Path
    target = Path(DATA_DIR) / "committee_paid_run.lock"
    if not target.is_file():
        return False
    try:
        with open(target, "rb") as handle:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    return True
                finally:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return True
                finally:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
            return False
    except OSError:
        # An unreadable existing lock cannot certify that a paid run is absent.
        return True


class ModelChoiceError(ValueError):
    """R-MOD punto 4 (06/10): la scelta dei modelli nel .env non e' utilizzabile.

    `problems` elenca TUTTI i ruoli che non vanno, ognuno con la variabile da cambiare e
    cosa fare: chi scarica la repo li legge cosi' come sono nel preflight."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


ROLE_LABELS = {"specialist": "desk specialisti", "red_team": "Red Team",
               "capo": "Capo", "aux": "ausiliario"}
# Varianti di instradamento OpenRouter: non sono righe del catalogo e il fornitore risponde col
# nome BASE, che il controllo d'identita' della risposta (modello del contratto) rifiuterebbe.
_ROUTING_VARIANTS = frozenset({"exacto", "nitro", "floor"})


def _choice_problem(role, slug, text):
    return (MODEL_ENV[role] + "=" + str(slug) + " (ruolo " + ROLE_LABELS[role] + "): " + text)


def configured_model_for_role(role: str) -> str:
    """La scelta CORRENTE del .env (solo prima dell'accettazione: preflight e catalogo).

    Variabile assente o vuota = default di MODEL_IDS. Un valore che non e' uno slug
    OpenRouter e' un errore col nome della variabile, mai un ripiego zitto sul default."""
    if role not in MODEL_IDS:
        raise ValueError("Trade Idea model role sconosciuto: " + str(role))
    value = (_MODEL_ENV_READ[role]() or "").strip()
    if not value:
        return MODEL_IDS[role]
    if not _MODEL_SLUG_RE.fullmatch(value):
        raise ModelChoiceError([_choice_problem(role, repr(value[:80]),
            "non e' uno slug OpenRouter valido (atteso fornitore/modello, come in openrouter.ai/models). "
            "Correggi la variabile o lasciala vuota per il default " + MODEL_IDS[role] + ".")])
    base, _, variant = value.partition(":")
    if variant in _ROUTING_VARIANTS:
        raise ModelChoiceError([_choice_problem(role, value,
            "la variante di instradamento :" + variant + " non e' ammessa nella Trade Idea (non e' nel "
            "catalogo OpenRouter e il fornitore risponde col nome base, che il controllo d'identita' "
            "della risposta rifiuta). Usa lo slug base " + base + ".")])
    return value


def configured_models() -> dict:
    chosen, problems = {}, []
    for role in MODEL_IDS:
        try:
            chosen[role] = configured_model_for_role(role)
        except ModelChoiceError as exc:
            problems.extend(exc.problems)
    if problems:
        raise ModelChoiceError(problems)
    return chosen


def contract_models(contract) -> dict:
    """Ruolo -> slug dal CONTRATTO accettato della run.

    `contract` puo' essere: la blackboard della Trade Idea (via il suo budget gate), il
    TradeIdeaBudgetGate, la run (``models`` = {ruolo: {"model": ...}}) o il catalog snapshot
    accettato (``models`` = {ruolo: {"id": ...}}). Un contratto incompleto e' un errore."""
    gate = getattr(contract, "budget_gate", None)
    if gate is not None:
        contract = gate
    if not isinstance(contract, dict):
        # TradeIdeaBudgetGate (o un gate che ne porta lo snapshot accettato)
        contract = getattr(contract, "catalog_snapshot", None)
    models = contract.get("models") if isinstance(contract, dict) else None
    if not isinstance(models, dict):
        raise ValueError("contratto modelli Trade Idea assente: la run non dichiara i suoi modelli")
    selected = {}
    for role in MODEL_IDS:
        row = models.get(role)
        slug = (row.get("model", row.get("id")) if isinstance(row, dict) else None)
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError("contratto modelli Trade Idea senza il ruolo " + role)
        selected[role] = slug
    return selected


def model_for_role(role: str, contract=None) -> str:
    """Senza `contract`: la scelta del .env (solo preflight, prima dell'accettazione).
    Con `contract`: il modello CONGELATO nella run, qualunque cosa dica oggi il .env."""
    if role not in MODEL_IDS:
        raise ValueError("Trade Idea model role sconosciuto: " + str(role))
    if contract is None:
        return configured_model_for_role(role)
    return contract_models(contract)[role]


def _effort_sent_natively(model) -> bool:
    """True se llm_client non invia l'effort a questo slug (z-ai/: ragionamento nativo)."""
    from bellomberg.core.llm_client import PREFISSI_RAGIONAMENTO_NATIVO
    return str(model).startswith(PREFISSI_RAGIONAMENTO_NATIVO)


def model_selection_drift(contract) -> list:
    """Ruoli dove il .env di OGGI sceglie un modello diverso dal contratto della run.

    Serve a DICHIARARE alla ripresa che il cambio non si applica; un .env illeggibile e'
    dichiarato anch'esso (la run resta sul contratto)."""
    accepted = contract_models(contract)
    drift = []
    for role in MODEL_IDS:
        try:
            current, error = configured_model_for_role(role), None
        except ValueError as exc:
            current, error = None, str(exc)[:200]
        if current != accepted[role]:
            drift.append({"role": role, "variable": MODEL_ENV[role], "contract_model": accepted[role],
                          "env_model": current, **({"env_error": error} if error else {})})
    return drift


def normalize_ticker(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("ticker mancante")
    ticker = value.strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise ValueError("ticker non valido o ambiguo")
    return ticker


def normalize_budget(value) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("limite di spesa Trade Idea mancante")
    try:
        budget = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("limite di spesa Trade Idea non numerico") from exc
    if not budget.is_finite() or budget <= 0 or budget.as_tuple().exponent < -6:
        raise ValueError("limite di spesa Trade Idea deve essere positivo, finito e preciso a 6 decimali")
    return str(budget)


def resolve_ticker_identity(ticker: str, *, opener=urlopen) -> dict:
    """Select an exact symbol/type, then pin literal identity fields from its chart."""
    ticker = normalize_ticker(ticker)
    query = urlencode({"q": ticker, "quotesCount": 20, "newsCount": 0})
    request = Request("https://query2.finance.yahoo.com/v1/finance/search?" + query,
                      headers={"User-Agent": "Mozilla/5.0"})
    try:
        with opener(request, timeout=8) as response:
            rows = json.load(response).get("quotes") or []
    except Exception as exc:
        return {"ticker": ticker, "name": None, "exchange": None, "currency": None,
                "status": "unverified", "reason": "ricerca identita' indisponibile: " + type(exc).__name__}
    exact = [row for row in rows if isinstance(row, dict)
             and str(row.get("symbol") or "").upper() == ticker
             and str(row.get("quoteType") or "").upper() in {"EQUITY", "ETF"}]
    instrument_types = {str(row.get("quoteType") or "").upper() for row in exact}
    if len(instrument_types) != 1:
        return {"ticker": ticker, "name": None, "exchange": None, "currency": None,
                "status": "ambiguous" if instrument_types else "unverified",
                "reason": "simbolo dello strumento esatto non risolto in modo univoco"}
    def quoted_currency(raw):
        if raw == "GBp":
            return "GBX"  # Yahoo's lowercase p means pence, not pounds.
        raw = str(raw or "").upper()
        return raw if re.fullmatch(r"[A-Z]{3}", raw) else None

    try:
        chart_url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
                     + quote(ticker, safe="") + "?range=5d&interval=1d")
        with opener(Request(chart_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=8) as response:
            raw = response.read()
        raw = raw.encode("utf-8") if isinstance(raw, str) else raw
        chart = json.loads(raw)
        packet = chart.get("chart")
        if not isinstance(packet, dict) or packet.get("error") is not None:
            raise ValueError("risposta chart indisponibile")
        results = packet.get("result")
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise ValueError("risultato chart esatto non univoco")
        meta = results[0].get("meta")
        if not isinstance(meta, dict):
            raise ValueError("metadata chart assenti")
        if str(meta.get("symbol") or "").upper() != ticker:
            raise ValueError("chart ticker diverso da quello richiesto")
        if str(meta.get("instrumentType") or "").upper() != next(iter(instrument_types)):
            raise ValueError("tipo strumento chart diverso dalla ricerca esatta")
        name_field = "longName" if meta.get("longName") else "shortName"
        exchange_field = "fullExchangeName" if meta.get("fullExchangeName") else "exchange"
        name, exchange = meta.get(name_field), meta.get(exchange_field)
        if (not isinstance(name, str) or not name.strip()
                or not isinstance(exchange, str) or not exchange.strip()):
            raise ValueError("nome legale o borsa del chart esatto non disponibili")
        name, exchange = name.strip(), exchange.strip()
        currency = quoted_currency(meta.get("currency"))
        if currency is None:
            raise ValueError("valuta quotazione assente")
        from hashlib import sha256
        source = {"contract": "exact_chart_identity/1", "url": chart_url,
                  "sha256": sha256(raw).hexdigest(), "observed_at": datetime.now(timezone.utc).isoformat(),
                  "fields": {"ticker": "symbol", "name": name_field, "exchange": exchange_field,
                             "currency": "currency", "instrument_type": "instrumentType"}}
    except Exception as exc:
        return {"ticker": ticker, "name": None, "exchange": None, "currency": None,
                "status": "unverified", "reason": "identita' del chart esatto non verificata: " + type(exc).__name__}
    return {"ticker": ticker, "name": name, "exchange": exchange, "currency": currency,
            "status": "confirmed", "reason": None, "identity_source": source}


def fetch_model_catalog(*, opener=urlopen, use_cache=True, models=None) -> dict:
    """Public GET only: exact IDs, reasoning/tools and nonnegative tariff snapshot.

    `models` (ruolo -> slug): i modelli del CONTRATTO di una run accettata; assente = la
    scelta corrente del .env (preflight). La cache vale solo per la stessa scelta."""
    global _CATALOG_CACHE
    import time
    if models is None:
        models = configured_models()
    if not isinstance(models, dict) or set(models) != set(MODEL_IDS):
        raise ValueError("modelli Trade Idea per il catalogo incompleti")
    cache_key = tuple(sorted(models.items()))
    with _CATALOG_LOCK:
        stamp, cached = _CATALOG_CACHE
        if (use_cache and cached is not None and time.monotonic() - stamp < 60
                and cached.get("_selection") == cache_key):
            return {k: v for k, v in cached.items() if k != "_selection"}
    with opener(Request(_CATALOG_URL, headers={"Accept": "application/json"}), timeout=20) as response:
        document = json.load(response)
    by_id = {row.get("id"): row for row in document.get("data", []) if isinstance(row, dict)}
    selected, problems = {}, []
    for role in MODEL_IDS:
        slug = models[role]
        row = by_id.get(slug)
        # R-MOD punto 4: un modello inadatto al ruolo e' un problema della SCELTA (variabile +
        # cosa fare), raccolto per tutti i ruoli; il catalogo malformato resta un ValueError.
        if row is None:
            problems.append(_choice_problem(role, slug, "modello assente dal catalogo OpenRouter "
                "(openrouter.ai/models). Controlla lo slug o scegli un altro modello per questo ruolo."))
            continue
        params = row.get("supported_parameters") or []
        efforts = (row.get("reasoning") or {}).get("supported_efforts") or []
        required = ("reasoning", "reasoning_effort", "max_tokens")
        # Each accepted policy checks its own effort against supported_efforts; "max" is
        # required only by the historical contract (see reasoning_effort below).
        missing = []
        if not efforts or any(item not in params for item in required):
            missing.append("ragionamento con effort")
        if role in ("specialist", "red_team") and any(item not in params for item in ("tools", "tool_choice")):
            missing.append("tool calling")
        if role in ("capo", "red_team", "aux") and "response_format" not in params:
            missing.append("output JSON strutturato")
        if missing:
            problems.append(_choice_problem(role, slug, "il catalogo OpenRouter non conferma "
                + ", ".join(missing) + ", necessari a questo ruolo. Scegli un altro modello per questo ruolo."))
            continue
        context = row.get("context_length")
        max_completion = (row.get("top_provider") or {}).get("max_completion_tokens")
        if type(context) is not int or context <= 0 or type(max_completion) is not int or max_completion <= 0:
            raise ValueError("contesto/output non confermati nel catalogo: " + slug)
        prices = {}
        for key in ("prompt", "completion"):
            try:
                val = Decimal(str((row.get("pricing") or {})[key]))
            except (KeyError, InvalidOperation) as exc:
                raise ValueError("prezzo non disponibile per " + slug + ": " + key) from exc
            if not val.is_finite() or val < 0:
                raise ValueError("prezzo non valido per " + slug + ": " + key)
            prices[key] = str(val)
        selected[role] = {"id": slug, "context_length": context,
                          "max_completion_tokens": max_completion, "pricing": prices,
                          "reasoning_effort": "max" if "max" in efforts else None,
                          "tools": role in ("specialist", "red_team"),
                          "supported_efforts": list(efforts),
                          "structured_output": "response_format" in params}
    if problems:
        raise ModelChoiceError(problems)
    snapshot = {"checked_at": datetime.now(timezone.utc).isoformat(), "models": selected,
                "source": _CATALOG_URL, "account_access_verified": False}
    with _CATALOG_LOCK:
        _CATALOG_CACHE = (time.monotonic(), {**snapshot, "_selection": cache_key})
    return snapshot


def qualify_trade_idea_sources(ticker, identity, as_of, *, archive_root=None,
                               document_sources=(), accepted_document_receipt=None, analysis_mode=None):
    from bellomberg.core.paths import DATA_DIR
    from bellomberg.valuation.trade_idea_model import research_admission
    return research_admission(ticker, identity, as_of,
        archive_root=archive_root or (DATA_DIR / "filing_archive"),
        document_sources=document_sources, accepted_document_receipt=accepted_document_receipt,
        analysis_mode=analysis_mode)


def source_qualification_summary(qualified, *, execution_status=None):
    """No private documents, filesystem paths or model input payload in preflight UI."""
    public = {key: qualified.get(key) for key in
              ("status", "reasons", "checklist", "method_id", "fingerprint", "analysis_mode")}
    if execution_status is not None:
        public["execution_status"] = execution_status
    public["coverage"] = {key: value for key, value in (qualified.get("coverage") or {}).items()
        if key in ("method_id", "method_version", "source_requirements", "sources", "preparer",
                   "economic_qualification", "engine")}
    from bellomberg.agents.trade_idea_sources import document_receipt_summary
    public["document_sources"] = document_receipt_summary(qualified.get("document_receipt"))
    return public


def preflight_trade_idea(ticker, pm_view="", view_source="manual", budget_limit_usd=None,
                         *, catalog_fetcher=fetch_model_catalog,
                         identity_resolver=resolve_ticker_identity,
                         active_checker=None, mandate_loader=None,
                         valuation_runtime_factory=None, key_checker=None,
                         source_qualifier=None, archive_root=None,
                          document_sources=(), analysis_mode=None, execution_policy=None) -> dict:
    """No LLM request and no DB writes; a failed check is visible before spending."""
    reasons = []
    storage = None
    if analysis_mode not in (None, RESEARCH_ANALYSIS_MODE):
        reasons.append('Modalita di analisi non supportata')
    try:
        from bellomberg.agents.trade_idea_sources import normalize_sources
        document_sources = normalize_sources(document_sources)
    except ValueError as exc:
        document_sources = []
        reasons.append("fonti documentali PM non valide: " + str(exc)[:400])
    try:
        ticker = normalize_ticker(ticker)
    except ValueError as exc:
        ticker = ""
        reasons.append(str(exc))
    if not isinstance(pm_view, str) or len(pm_view) > 20000:
        reasons.append("view PM deve essere testo di al massimo 20000 caratteri")
    if view_source not in VIEW_SOURCES:
        reasons.append("origine view non valida")
    try:
        budget = normalize_budget(budget_limit_usd)
        budget_reason = None
    except ValueError as exc:
        budget, budget_reason = None, str(exc)
        reasons.append(budget_reason)
    identity = ({"ticker": ticker, "name": None, "exchange": None, "currency": None,
                 "status": "invalid", "reason": reasons[0]} if not ticker else identity_resolver(ticker))
    if identity["status"] != "confirmed":
        reasons.append(identity.get("reason") or "identita' non confermata")
    try:
        catalog = catalog_fetcher()
        policy = {'analysis_mode': analysis_mode, 'execution_policy': execution_policy}
        models = [{"role": role, "model": catalog["models"][role]["id"],
                   "reasoning_effort": role_effort(policy, role)} for role in MODEL_IDS]
        if execution_policy is not None:
            effort_problems = []
            for row in models:
                quoted_row = catalog['models'][row['role']]
                if _effort_sent_natively(row['model']):
                    # MOD-TI 06/10: su z-ai/ llm_client NON invia l'effort (misurato 05/09: ogni
                    # effort esplicito azzera il ragionamento): il catalogo non lo vincola, lo si dichiara.
                    row['reason'] = 'effort non inviato: ragionamento nativo del fornitore'
                elif row['reasoning_effort'] not in quoted_row.get('supported_efforts', []):
                    # R-MOD punto 4: TUTTI i ruoli, con variabile, effort ammessi e cosa fare.
                    wanted = str(row['reasoning_effort'])
                    effort_problems.append(_choice_problem(row['role'], row['model'],
                        "la policy della run chiede effort '" + wanted + "' (lo fissa la policy, non si "
                        "configura); questo modello accetta solo "
                        + (', '.join(map(str, quoted_row.get('supported_efforts') or [])) or 'nessun effort')
                        + ". Scegli per questo ruolo un modello che accetti '" + wanted
                        + "' (es. il default " + MODEL_IDS[row['role']] + ")."))
            if effort_problems:
                raise ModelChoiceError(effort_problems)
            # Checked before any spending: a Capo cap the model cannot hold would otherwise
            # surface only at the Capo call, after desks and Red Team are paid.
            # MOD-CAP (06/10, decisione PM): sopra il massimo di uscita del provider il cap si
            # ADATTA al tetto del catalogo (gate, dichiarato alla chiamata); resta un rifiuto
            # solo un tetto non dichiarato dal catalogo o non contenuto nel contesto.
            capo_cap = policy_output_cap(policy, 'capo', 0)
            capo_row = catalog['models']['capo']
            if (not isinstance(capo_row.get('max_completion_tokens'), int)
                    or capo_row['max_completion_tokens'] <= 0
                    or min(capo_cap, capo_row['max_completion_tokens'])
                    >= int(capo_row.get('context_length') or 0)):
                raise ValueError('Tetto di output del Capo non supportato dal catalogo: '
                                 + str(capo_cap) + ' (tetto ' + str(capo_row.get('max_completion_tokens'))
                                 + ', contesto ' + str(capo_row.get('context_length')) + ')')
        prices = {role: catalog["models"][role]["pricing"] for role in MODEL_IDS}
    except ModelChoiceError as exc:
        # Il catalogo risponde: e' la SCELTA dei modelli nel .env che non va (ogni ruolo elencato).
        catalog, models, prices = None, [], {}
        reasons.extend("modello non utilizzabile: " + problem for problem in exc.problems)
    except Exception as exc:
        catalog, models, prices = None, [], {}
        reasons.append("catalogo modelli non verificabile: " + str(exc))
    if key_checker is None:
        from bellomberg.core.llm_client import chiave_api
        key_checker = chiave_api
    try:
        key_checker()
    except Exception as exc:
        reasons.append("accesso OpenRouter non configurato: " + type(exc).__name__)
    if mandate_loader is None:
        from bellomberg.core.mandato_pm import carica
        mandate_loader = carica
    try:
        mandate_loader()
    except Exception as exc:
        reasons.append("mandato PM non disponibile: " + str(exc)[:250])
    if active_checker is not None:
        try:
            if active_checker():
                reasons.append("un'altra run pagata e' attiva")
        except Exception as exc:
            reasons.append("stato run pagate non verificabile: " + str(exc)[:200])
            storage = getattr(exc, "storage", None)
    qualification_execution = "not_run"
    qualified = {"status": "blocked", "reasons": [],
                 "fingerprint": None, "method_id": None, "coverage": {}, "checklist": []}
    try:
        if identity.get("status") == "confirmed" and not reasons:
            document_options = {"document_sources": document_sources} if document_sources else {}
            if source_qualifier is None and analysis_mode is not None:
                document_options['analysis_mode'] = analysis_mode
            qualified = (source_qualifier or qualify_trade_idea_sources)(
                ticker, identity, datetime.now(timezone.utc).date().isoformat(),
                archive_root=archive_root, **document_options)
            qualification_execution = "completed"
        if qualification_execution == "completed" and qualified.get("status") not in (
                "qualified", "preparation_required", "research_required"):
            reasons.extend("fonti non qualificate: " + str(reason)
                           for reason in qualified.get("reasons") or ["qualification not demonstrated"])
    except Exception as exc:
        qualification_execution = "failed"
        qualified = {"status": "blocked", "reasons": [type(exc).__name__ + ": " + str(exc)[:400]],
                     "fingerprint": None, "method_id": None, "coverage": {}, "checklist": []}
        reasons.append("fonti non qualificabili: " + qualified["reasons"][0])
    from bellomberg.valuation.input_preparation import has_approved_inputs
    required = qualified.get('status') in ('preparation_required', 'research_required') or not (
        isinstance(qualified.get("bundle"), dict) and has_approved_inputs(qualified["bundle"]))
    preparation = {"required": required, "paid": required,
                   "status": "research_required" if qualified.get('status') == 'research_required' else
                       "historical_required" if qualified.get('status') == 'preparation_required' else
                       "blocked" if qualified.get("status") != "qualified" else "required" if required else "ready"}
    if analysis_mode == RESEARCH_ANALYSIS_MODE:
        if qualification_execution == "completed" and not is_research_mode(qualified):
            reasons.append('La qualificazione non corrisponde alla modalita di ricerca accettata')
        preparation = {'required': False, 'paid': False, 'status': 'not_required'}
    val_state = {"status": "per_run", "candidate_status": qualified.get("status"),
                 "candidate_reason": "; ".join(qualified.get("reasons") or []) or None}
    qualification_summary = source_qualification_summary(qualified,
        execution_status=qualification_execution)
    return {"ok": not reasons, "identity": identity, "models": models,
            "authorization": {"status": "ready" if not reasons else "blocked",
                              "reason": "; ".join(reasons) if reasons else "account access not paid-tested"},
            "budget": {"status": "ready" if budget else "missing", "reason": budget_reason,
                       "limit_usd": budget, "estimated_cost_usd": None, "model_prices": prices,
                       "completion_guaranteed": False, "remaining_cost_status": "unknown",
                       "remaining_cost_reason": "Prompts, tool iterations and future output are not yet known; each dispatch requires a durable reservation within the original ceiling"},
            "valuation": val_state, "source_qualification": qualification_summary,
            "document_sources": qualification_summary["document_sources"],
            **({"storage": storage} if storage is not None else {}),
            "preparation": preparation, "analysis_mode": analysis_mode,
            **({'execution_policy': execution_policy} if execution_policy is not None else {}),
            "reasons": reasons, "catalog_snapshot": catalog,
            "_source_qualification": qualified}


def _stable_provider_messages(messages):
    """Keep tool argument JSON byte-stable across sorted durable checkpoints."""
    from bellomberg.agents.specialists.base import _checkpoint_json
    return json.loads(json.dumps(_checkpoint_json(messages), sort_keys=True,
                                 ensure_ascii=False, allow_nan=False))


def _model_author_request_matches(request, expected_sha256):
    """Attest a whole historical wire body, including legacy argument ordering.

    Historical storage sorted object keys but the provider serialized tool
    arguments as strings. A schema-ordered candidate recovers some legacy
    bodies. It is accepted only by their original full wire hash, never by a
    relaxed comparison; arbitrary unrecorded orders remain unrecoverable.
    """
    if _plan_digest(costruisci_corpo(**request)) == expected_sha256:
        return True
    definitions = {tool["name"]: tool.get("input_schema", {}) for tool in request.get("tools") or ()}

    def ordered(value, schema):
        if isinstance(value, list):
            return [ordered(item, schema.get("items", {})) for item in value]
        if not isinstance(value, dict):
            return value
        properties = schema.get("properties") or {}
        names = [name for name in properties if name in value]
        names.extend(name for name in value if name not in properties)
        additional = schema.get("additionalProperties")
        return {name: ordered(value[name], properties.get(name) or
                (additional if isinstance(additional, dict) else {})) for name in names}

    from bellomberg.agents.specialists.base import _checkpoint_json
    messages = _checkpoint_json(request["messages"])
    for message in messages:
        if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if block.get("type") == "tool_use":
                block["input"] = ordered(block["input"], definitions.get(block["name"], {}))
    return _plan_digest(costruisci_corpo(**{**request, "messages": messages})) == expected_sha256


class TradeIdeaBudgetGate:
    """One durable reservation per OpenRouter call; unknown billing blocks later calls."""

    def __init__(self, store, run_id, worker_token, catalog_snapshot, *, catalog_fetcher=None):
        self.store = store
        self.run_id = run_id
        self.worker_token = worker_token
        self.catalog_snapshot = catalog_snapshot
        # One live catalog read per minute at most; the accepted snapshot prices every reservation.
        # MOD-TI 06/10: il catalogo live si chiede per i modelli del CONTRATTO, non del .env di oggi.
        self.catalog_fetcher = catalog_fetcher or (lambda: fetch_model_catalog(
            use_cache=True, models=self.contract_models))
        self.catalog_notices = []
        self.model_contract_notice = None
        self._requests = {}

    @property
    def contract_models(self):
        """Ruolo -> slug dal catalog snapshot ACCETTATO (create_run lo ha verificato = models_json)."""
        return contract_models(self.catalog_snapshot)

    def declare_model_selection(self):
        """Alla presa in carico: un .env cambiato dopo l'accettazione e' DICHIARATO, non applicato."""
        drift = model_selection_drift(self.catalog_snapshot)
        self.model_contract_notice = {
            "status": "env_differs" if drift else "env_matches",
            "models": self.contract_models, "drift": drift,
            "notice": ("Il .env sceglie oggi modelli diversi da quelli accettati: la run usa il "
                       "contratto. " + "; ".join(
                           row["variable"] + "=" + str(row["env_model"]) + " non applicato, contratto "
                           + row["contract_model"] for row in drift)) if drift else None}
        if drift:
            print("[TRADE_IDEA] " + self.model_contract_notice["notice"])
        return self.model_contract_notice

    def wrap_client(self, client, *, role):
        return _BudgetedClient(client, self, role)

    def reuse_capo_response(self):
        payload = self.store.reusable_capo_response(self.run_id, self.worker_token)
        return _restore_provider_message(payload) if payload is not None else None

    def _cap_al_tetto(self, role, cap):
        """MOD-CAP (06/10): max_tokens = min(cap, tetto di uscita del modello CONGELATO nel
        contratto della run: catalog_snapshot). La Trade Idea ha contabilita' propria (il client
        non tocca il suo corpo, llm_client._corpo_al_tetto): l'adattamento avviene qui, prima di
        _reuse/_reserve, dichiarato una volta per (ruolo, modello). Senza tetto nel contratto il
        cap resta quello richiesto e _reserve decide (rifiuto dichiarato, nessuna spesa)."""
        expected_role = ("specialist" if role.startswith("specialist:") else
                         "aux" if role.startswith("aux:") else role)
        row = ((getattr(self, "catalog_snapshot", None) or {}).get("models") or {}).get(expected_role) or {}
        tetto = row.get("max_completion_tokens")
        if type(cap) is not int or cap <= 0 or type(tetto) is not int or tetto <= 0 or cap <= tetto:
            return cap
        from bellomberg.core.llm_client import tetto_uscita
        return tetto_uscita(row.get("id"), cap, ruolo="trade_idea:" + role, tetto_provider=tetto)

    def _al_tetto(self, kwargs, role):
        cap = self._cap_al_tetto(role, kwargs.get("max_tokens"))
        return kwargs if cap == kwargs.get("max_tokens") else {**kwargs, "max_tokens": cap}

    def capo_finalization(self):
        resolve = getattr(self.store, "accepted_capo_finalization", None)
        if callable(resolve):
            return resolve(self.run_id)
        continuation = self.store.get_run(self.run_id)["run"].get("continuation") or {}
        if continuation.get("capo_finalization") is None:
            return None
        raise ValueError("Capo finalization authorization cannot be verified")

    def _project_model_authoring_request(self, kwargs, receipt, role):
        """Pin an explicit request view while the saved transcript stays intact."""
        from bellomberg.agents.model_authoring_context import project_model_authoring_messages

        board = getattr(self, "blackboard", None)
        descriptor = (getattr(board, "data", {}) or {}).get("_model_authoring_completion") or {}
        if (role != "specialist:fundamentals" or board is None
                or getattr(board, "run_scope", None) != "trade_idea"
                or getattr(board, "model_phase", None) != "building"
                or getattr(board, "current_round", None) != 1
                or descriptor.get("version") != 1
                or descriptor.get("mode") != "model_authoring_completion"):
            raise ValueError("Model authoring context projection outside its native task")
        raw = _stable_provider_messages(kwargs["messages"])
        projected, verified = project_model_authoring_messages(raw)
        if not isinstance(receipt, dict) or receipt != verified:
            raise ValueError("Model authoring context projection receipt differs")
        prices = self.catalog_snapshot["models"]["specialist"]["pricing"]
        price_cap = {"prompt": float(Decimal(prices["prompt"]) * 10**6),
                     "completion": float(Decimal(prices["completion"]) * 10**6), "request": 0.0}
        planned = {**kwargs, "messages": projected}
        pin = {"contract": "model_authoring_dispatch_projection/1", "role": role,
               "receipt": verified,
               "original_wire_sha256": _plan_digest(costruisci_corpo(
                   **{**kwargs, "messages": raw, "provider_max_price": price_cap})),
               "projected_wire_sha256": _plan_digest(costruisci_corpo(
                   **{**planned, "provider_max_price": price_cap}))}
        pins = board.data.get("_model_authoring_context_projections", [])
        if not isinstance(pins, list) or any(not isinstance(item, dict) for item in pins):
            raise ValueError("Invalid saved model authoring context projection journal")
        matches = [item for item in pins if item.get("original_wire_sha256") == pin["original_wire_sha256"]]
        if matches:
            if len(matches) != 1 or matches[0] != pin:
                raise ValueError("Saved model authoring context projection differs")
        else:
            board.data["_model_authoring_context_projections"] = [*pins, pin]
            try:
                board.persist_run_checkpoint("model_authoring_context_projection")
            except BaseException:
                board.data["_model_authoring_context_projections"] = pins
                raise
        return planned

    def validate_response_recovery(self, descriptor, kwargs, *, role):
        """Verify the old wire body without reserving or sending a new request."""
        continuation = self.store.get_run(self.run_id)["run"].get("continuation") or {}
        accepted = continuation.get("specialist_response_recovery")
        if descriptor != accepted and isinstance(descriptor, dict) and descriptor.get("mode") == "report_only":
            saved = (getattr(getattr(self, "blackboard", None), "specialist_checkpoints", {})
                     .get(descriptor.get("checkpoint_key")) or {})
            if (saved.get("status") == "complete" and saved.get("response_recovery") == descriptor
                    and self.store.accepted_response_recovery(self.run_id, descriptor)):
                accepted = descriptor
        if isinstance(accepted, dict) and accepted.get("kind") == "failed_model_authoring":
            return self._validate_failed_author_response_recovery(descriptor, accepted, kwargs, role)
        if (not isinstance(accepted, dict) or descriptor != accepted
                or role != "specialist:" + str(accepted.get("desk"))
                or kwargs.get("model") != model_for_role("specialist", self)
                or kwargs.get("thinking") != {"type": "effort", "effort": "max"}
                or kwargs.get("tool_choice") != {"type": "none"}
                or kwargs.get("max_tokens") not in (16000, 64000, 65536)
                or accepted.get("replacement_max_tokens") != 128000):
            raise ValueError("report completion differs from its accepted authorization")
        pricing = self.catalog_snapshot["models"]["specialist"]["pricing"]
        prices = {"prompt": float(Decimal(pricing["prompt"]) * 10**6),
                  "completion": float(Decimal(pricing["completion"]) * 10**6), "request": 0.0}
        # R-MOD F3 (06/10): la richiesta originale e' partita al cap ADATTATO al tetto del contratto
        # quando il modello ne accetta meno: si prova lo sha di cio' che e' stato inviato davvero.
        if accepted.get("request_sha256") not in {
                _plan_digest(costruisci_corpo(**{**variant, "provider_max_price": prices}))
                for variant in (kwargs, self._al_tetto(kwargs, role))}:
            raise ValueError("original report request wire contract differs")

    def _validate_failed_author_response_recovery(self, descriptor, accepted, kwargs, role):
        """Reprove the exact failed dispatch, including its already saved projection."""
        from bellomberg.agents.model_authoring_context import project_model_authoring_messages

        board = getattr(self, "blackboard", None)
        task = (getattr(board, "data", {}) or {}).get("_model_authoring_completion") or {}
        iteration = accepted.get("original_iteration")
        if (descriptor != accepted or accepted.get("version") != 1
                or accepted.get("mode") != "model_authoring_error_retry"
                or accepted.get("desk") != "fundamentals" or accepted.get("round_n") != 1
                or role != "specialist:fundamentals" or board is None
                or getattr(board, "run_scope", None) != "trade_idea"
                or getattr(board, "model_phase", None) != "building"
                or getattr(board, "current_round", None) != 1
                or task.get("version") != 1 or task.get("mode") != "model_authoring_completion"
                or kwargs.get("model") != model_for_role("specialist", self)
                or kwargs.get("thinking") != {"type": "effort", "effort": "max"}
                or kwargs.get("max_tokens") != 128000 or kwargs.get("tool_choice") is not None
                or not kwargs.get("tools")
                or accepted.get("original_max_tokens") != 128000
                or accepted.get("replacement_max_tokens") != 128000
                or accepted.get("max_tool_iters") != 30
                or type(iteration) is not int or not 1 <= iteration < 30):
            raise ValueError("failed author recovery differs from its accepted authorization")
        pricing = self.catalog_snapshot["models"]["specialist"]["pricing"]
        prices = {"prompt": float(Decimal(pricing["prompt"]) * 10**6),
                  "completion": float(Decimal(pricing["completion"]) * 10**6), "request": 0.0}
        raw = _stable_provider_messages(kwargs["messages"])
        projected, receipt = project_model_authoring_messages(raw)
        expected = accepted.get("request_sha256")
        pins = board.data.get("_model_authoring_context_projections", [])
        if not isinstance(pins, list) or any(not isinstance(row, dict) for row in pins):
            raise ValueError("original failed author request or saved projection differs")
        # R-MOD F3 (06/10): il dispatch fallito e' partito al cap richiesto (128000) o, col modello
        # del contratto a tetto minore, al cap ADATTATO (il wrapper adatta prima della proiezione).
        for variant in (kwargs, self._al_tetto(kwargs, role)):
            original = {**variant, "messages": raw, "provider_max_price": prices}
            original_sha = _plan_digest(costruisci_corpo(**original))
            if original_sha == expected:
                return
            projected_sha = _plan_digest(costruisci_corpo(**{**original, "messages": projected}))
            pin = {"contract": "model_authoring_dispatch_projection/1", "role": role,
                   "receipt": receipt, "original_wire_sha256": original_sha,
                   "projected_wire_sha256": projected_sha}
            if (projected_sha == expected
                    and [row for row in pins if row.get("original_wire_sha256") == original_sha] == [pin]):
                return
        raise ValueError("original failed author request or saved projection differs")

    def _reserve(self, kwargs, role):
        blackboard = getattr(self, "blackboard", None)
        if blackboard is not None:
            blackboard.raise_if_run_blocked()
        finalization = self.capo_finalization()
        if finalization is not None and (role != "capo"
                or kwargs.get("max_tokens") not in (finalization["max_tokens"],
                                                     self._cap_al_tetto(role, finalization["max_tokens"]))):
            raise ValueError("Authorized finalization permits only its exact Capo request")
        model = kwargs.get("model")
        expected_role = ("specialist" if role.startswith("specialist:") else
                         "aux" if role.startswith("aux:") else role)
        # Anti-replay sul CONTRATTO della run (MOD-TI 06/10): mai sul .env corrente.
        expected = model_for_role(expected_role, self)
        if model != expected:
            raise ValueError("modello fuori dal contratto della run Trade Idea: " + str(model))
        try:
            live = self.catalog_fetcher()
        except OSError as exc:
            from urllib.error import HTTPError
            if isinstance(exc, HTTPError):
                raise  # the catalog answered: a withdrawn or changed model must be visible
            # A network blip on the free catalog must not end a paid run: the accepted
            # snapshot is the binding contract (price, effort, caps) and is declared here.
            # A changed catalog content (ValueError) still blocks.
            live = self.catalog_snapshot
            self.catalog_notices.append({"at": datetime.now(timezone.utc).isoformat(), "role": role,
                "notice": "catalogo live non raggiungibile, usato lo snapshot accettato: "
                          + type(exc).__name__ + ": " + str(exc)[:200]})
        quoted = self.catalog_snapshot["models"][expected_role]
        live_model = live["models"][expected_role]
        if live_model["id"] != model:
            raise ValueError("catalogo modello cambiato prima della chiamata")
        accepted_run = self.store.get_run(self.run_id)['run']
        if model_for_role(expected_role, accepted_run) != model:
            raise ValueError("modello diverso da models_json della run accettata: " + str(model))
        effort = role_effort(accepted_run, expected_role)
        if execution_policy(accepted_run) is not None:
            if _effort_sent_natively(model):
                pass  # z-ai/: llm_client non invia l'effort (dichiarato nel preflight)
            elif any(effort not in row.get('supported_efforts', []) for row in (quoted, live_model)):
                raise ValueError('capability reasoning richiesta non confermata prima della chiamata')
            if (accepted_run['models'][expected_role].get('reasoning_effort') != effort):
                raise ValueError('Effort differs from the accepted execution policy')
        elif quoted.get("reasoning_effort") != "max" or live_model.get("reasoning_effort") != "max":
            raise ValueError("capability reasoning max non confermata prima della chiamata")
        pricing = quoted["pricing"]
        context = quoted["context_length"]
        cap = kwargs.get("max_tokens")
        if finalization is not None:
            # An explicit finalization grant carries its own authorized cap.
            if cap not in (finalization.get("max_tokens"),
                           self._cap_al_tetto(role, finalization.get("max_tokens"))):
                raise ValueError('Capo output cap differs from its explicit finalization grant')
        elif (execution_policy(accepted_run) in RESEARCH_POLICIES and expected_role == 'capo'
                and cap not in (policy_output_cap(accepted_run, 'capo', 128000),
                                self._cap_al_tetto(role, policy_output_cap(accepted_run, 'capo', 128000)))):
            # MOD-CAP: il cap della policy, o lo stesso adattato al tetto del contratto.
            raise ValueError('Capo output cap differs from the accepted execution policy')
        if (type(cap) is not int or cap <= 0
                or cap > min(quoted["max_completion_tokens"], live_model["max_completion_tokens"])):
            raise ValueError("max_tokens incompatibile col modello Trade Idea")
        expected_thinking = (finalization["thinking"] if finalization is not None
                             else role_thinking(accepted_run, expected_role))
        if kwargs.get("thinking") != expected_thinking:
            raise ValueError("Capo reasoning differs from its explicit authorization" if finalization is not None
                             else "reasoning effort differs from the accepted Trade Idea policy")
        quote = {
            "prompt": float(Decimal(pricing["prompt"]) * 10**6),
            "completion": float(Decimal(pricing["completion"]) * 10**6),
            "request": 0.0,
        }
        safe_kwargs = {**kwargs, "provider_max_price": quote,
                       "messages": _stable_provider_messages(kwargs["messages"])}
        body = costruisci_corpo(**safe_kwargs)
        strict_context = kwargs.get('require_full_context', False)
        if strict_context and (role not in ('aux:preparation', 'aux:revision')
                or body.get('plugins') != [{'id': 'context-compression', 'enabled': False}]):
            raise ValueError('contesto integrale consentito solo alla preparazione/revisione senza compressione')
        input_upper_bound = max(
            len(json.dumps(body, ensure_ascii=False).encode("utf-8")),
            len(json.dumps(body, ensure_ascii=True).encode("utf-8")))
        context_limit = min(context, live_model['context_length'])
        if cap >= context_limit or not strict_context and input_upper_bound + cap > context_limit:
            raise ValueError("prompt oltre il contesto del modello")
        # Strict provider validation accepts the complete input or rejects it.
        # Reserve the entire model window, never an estimated token count.
        reserved_input = context_limit if strict_context else input_upper_bound
        reserve = (Decimal(reserved_input) * Decimal(pricing["prompt"])
                   + Decimal(cap) * Decimal(pricing["completion"]))
        reserve = reserve.quantize(Decimal("0.000000001"), rounding=ROUND_CEILING)
        request_id = uuid.uuid4().hex
        recorded_role = (role + ":model_revision" if getattr(self, "phase", None) == "model_revision"
                         and role.startswith("specialist:") else role)
        fingerprint = _plan_digest(body)
        accepted = self.store.reserve_cost(self.run_id, request_id, recorded_role, model, str(reserve),
            request_sha256=fingerprint, worker_token=self.worker_token,
            **({'capo_request_body': body} if recorded_role == 'capo'
               and execution_policy(accepted_run) in RESEARCH_POLICIES else {}))
        if accepted is not True:
            raise RuntimeError("prenotazione LLM duplicata o rifiutata: nessuna richiesta inviata")
        self._requests[request_id] = {"role": role, "request_sha256": fingerprint,
                                     "round_n": getattr(blackboard, "current_round", None)}
        return request_id, safe_kwargs

    def _reuse(self, kwargs, role):
        # Only immutable ancestor receipts qualify. Current-run calls never hit
        # this path, so repeated identical requests are not silently suppressed.
        if not self.store.get_run(self.run_id)["run"].get("continuation"):
            return None
        quoted = self.catalog_snapshot["models"]["specialist" if role.startswith("specialist:") else
                    "aux" if role.startswith("aux:") else role]
        pricing = quoted["pricing"]
        body = costruisci_corpo(**{**kwargs, "provider_max_price": {
            "prompt": float(Decimal(pricing["prompt"]) * 10**6),
            "completion": float(Decimal(pricing["completion"]) * 10**6), "request": 0.0}})
        recorded_role = (role + ":model_revision" if getattr(self, "phase", None) == "model_revision"
                         and role.startswith("specialist:") else role)
        stable_body = costruisci_corpo(**{**kwargs,
            "messages": _stable_provider_messages(kwargs["messages"]),
            "provider_max_price": {"prompt": float(Decimal(pricing["prompt"]) * 10**6),
                "completion": float(Decimal(pricing["completion"]) * 10**6), "request": 0.0}})
        candidates = [body] if stable_body == body else [body, stable_body]
        if kwargs.get("max_tokens") == 128000:
            # The output policy increased on 02/10. Recognize an already paid
            # ancestor only by its complete original wire hash. All source,
            # prompt, model, effort, tool and price fields remain identical.
            policy_role = ("specialist" if role.startswith("specialist:") else
                           "aux" if role.startswith("aux:") else role)
            legacy_caps = {"specialist": (16000, 64000, 65536),
                           "capo": (64000,), "red_team": (65536,),
                           "aux": (65536,)}.get(policy_role, ())
            candidates.extend({**candidate, "max_tokens": cap}
                              for candidate in list(candidates) for cap in legacy_caps)
        for original_body in candidates:
            payload = self.store.reusable_response(self.run_id, self.worker_token,
                role=recorded_role, request_sha256=_plan_digest(original_body))
            if payload is not None:
                return _restore_provider_message(payload)
        return None

    def _durable(self, write, *args, **kwargs):
        """Persist a settlement of an already-paid response; retry the WRITE, never the call.

        A busy SQLite (parallel desks) must not turn a received paid response into
        an unrecoverable unknown. Only lock/busy errors are retried.
        """
        import sqlite3
        import time
        for attempt in range(6):
            try:
                return write(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                text = str(exc).lower()
                if attempt == 5 or not ("locked" in text or "busy" in text):
                    if "locked" in text or "busy" in text:
                        # Soldi: la ricevuta di una risposta pagata non e' scritta.
                        # Mai lacuna di un desk: resta un guasto della run.
                        exc.pagata_non_registrata = True
                    raise
                time.sleep(0.5 * 2 ** attempt)

    def _reconcile(self, request_id, response, expected_model):
        response.request_id = request_id
        usage = getattr(response, "usage", None)
        if usage is not None:
            usage.request_id = request_id
        payload = _provider_message_payload(response)
        receipt = {"response_id": getattr(response, "id", None), "model": getattr(response, "model", None),
                   "stop_reason": getattr(response, "stop_reason", None),
                   "request_sha256": self._requests.get(request_id, {}).get("request_sha256"),
                   "response": payload, "response_sha256": _plan_digest(payload)}
        if (not isinstance(getattr(response, "id", None), str)
                or not response.id.strip() or getattr(response, "model", None) != expected_model):
            self._durable(self.store.mark_cost_unknown, self.run_id, request_id,
                reason="identita' risposta provider assente o modello diverso da quello accettato", receipt=receipt)
            raise RuntimeError("identita' modello/risposta provider non attestabile")
        actual = getattr(usage, "cost_usd", None)
        if isinstance(actual, bool) or actual is None:
            self._durable(self.store.mark_cost_unknown, self.run_id, request_id,
                                         reason="provider usage.cost_usd assente", receipt=receipt)
            raise RuntimeError("costo provider non disponibile; nuove richieste Trade Idea bloccate")
        try:
            amount = Decimal(str(actual))
        except InvalidOperation as exc:
            self._durable(self.store.mark_cost_unknown, self.run_id, request_id,
                                         reason="provider usage.cost_usd invalido", receipt=receipt)
            raise RuntimeError("costo provider invalido") from exc
        if not amount.is_finite() or amount < 0:
            self._durable(self.store.mark_cost_unknown, self.run_id, request_id,
                                         reason="provider usage.cost_usd non finito o negativo", receipt=receipt)
            raise RuntimeError("costo provider invalido")
        details = usage.to_dict() if hasattr(usage, "to_dict") else {"cost_usd": str(amount)}
        self._durable(self.store.reconcile_cost, self.run_id, request_id, charged_usd=str(amount),
                                  usage=details, receipt=receipt)

    def _unknown(self, request_id, exc):
        import sqlite3
        partial = _partial_provider_evidence(getattr(exc, "partial_response", None))
        from bellomberg.core.generation_lookup import generation_id_from
        receipt = {"request_sha256": self._requests.get(request_id, {}).get("request_sha256"),
                   "complete": False, "partial_response": partial,
                   "partial_response_sha256": _plan_digest(partial),
                   "response_id": partial.get("id"), "model": partial.get("model"),
                   "status_code": getattr(exc, "status_code", None),
                   "generation_id": generation_id_from({"partial_response": partial}, exc)}
        self._durable(self.store.mark_cost_unknown, self.run_id, request_id,
                                     reason=type(exc).__name__ + ": " + str(exc)[:250], receipt=receipt)
        request = self._requests.get(request_id, {})
        failure = {"message": type(exc).__name__ + ": " + str(exc)[:2800],
                   "exception_type": type(exc).__name__, "request_id": request_id,
                   "desk": request.get("role"), "round": request.get("round_n"),
                   "phase": getattr(self, "phase", "committee")}
        try:
            self.store.record_failure(self.run_id, self.worker_token, failure)
        except sqlite3.OperationalError as lock_exc:
            # Costo ignoto gia' scritto (mark_cost_unknown): il guasto resta della run.
            lock_exc.pagata_non_registrata = True
            raise
        blackboard = getattr(self, "blackboard", None)
        if blackboard is not None and not blackboard.data.get("_primary_failure"):
            blackboard.data["_primary_failure"] = failure
        try:
            exc.request_id = request_id
        except (AttributeError, TypeError):
            pass


def _provider_message_payload(response):
    """Whitelisted provider output; never headers, credentials or SDK internals."""
    blocks = []
    for block in getattr(response, "content", ()):
        value = block if isinstance(block, dict) else vars(block)
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        names = {"text": ("text",), "tool_use": ("id", "name", "input"),
                 "thinking": ("thinking",)}.get(kind)
        if names is None:
            raise ValueError("provider block cannot be durably recorded: " + str(kind))
        blocks.append({"type": kind, **{name: value.get(name) for name in names}})
    usage = getattr(response, "usage", None)
    details = getattr(response, "stop_details", None)
    return _diagnostic_json_value({"id": getattr(response, "id", None), "model": getattr(response, "model", None), "content": blocks,
        "request_id": getattr(response, "request_id", None),
        "stop_reason": getattr(response, "stop_reason", None),
        "stop_details": ({"category": getattr(details, "category", None),
                          "explanation": getattr(details, "explanation", None)} if details else None),
        "usage": usage.to_dict() if hasattr(usage, "to_dict") else {},
        "provider": getattr(response, "provider", None), "finish_reason": getattr(response, "finish_reason", None)})


def _diagnostic_json_value(value):
    """Keep invalid scalar evidence explicit without serializing SDK objects."""
    import math
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"invalid_numeric": repr(value)}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_diagnostic_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _diagnostic_json_value(item) for key, item in value.items() if isinstance(key, str)}
    return {"unsupported_value_type": type(value).__name__}


def _partial_provider_evidence(value):
    """Keep known output after transport loss, excluding SDK/header secrets."""
    if not isinstance(value, dict):
        return {}
    fields = {key: value[key] for key in ("id", "model", "provider", "created", "object")
              if key in value and (isinstance(value[key], (str, int, float, bool)) or value[key] is None)}
    usage = value.get("usage")
    if isinstance(usage, dict):
        names = ("prompt_tokens", "completion_tokens", "total_tokens", "cost", "cost_usd",
                 "input_tokens", "output_tokens", "reasoning_tokens", "cache_read_input_tokens")
        fields["usage"] = {key: usage[key] for key in names if key in usage}
    fields["choices"] = []
    for choice in value.get("choices") or ():
        if not isinstance(choice, dict):
            continue
        item = {key: choice[key] for key in ("index", "finish_reason", "native_finish_reason") if key in choice}
        for kind in ("message", "delta"):
            message = choice.get(kind)
            if isinstance(message, dict):
                item[kind] = {key: message[key] for key in ("role", "content", "reasoning", "refusal") if key in message}
                if isinstance(message.get("tool_calls"), list):
                    item[kind]["tool_calls"] = []
                    for row in message["tool_calls"]:
                        if not isinstance(row, dict):
                            continue
                        function = row.get("function")
                        call = {key: row[key] for key in ("id", "type", "index") if key in row}
                        if isinstance(function, dict):
                            call["function"] = {key: function[key] for key in ("name", "arguments") if key in function}
                        else:
                            call["malformed_function_type"] = type(function).__name__
                        item[kind]["tool_calls"].append(call)
        fields["choices"].append(item)
    return _diagnostic_json_value(fields)


def _restore_provider_message(payload):
    from bellomberg.core.llm_client import Messaggio, Usage, TextBlock, ToolUseBlock, ThinkingBlock, StopDetails
    types = {"text": TextBlock, "tool_use": ToolUseBlock, "thinking": ThinkingBlock}
    blocks = [types[row["type"]](**{key: value for key, value in row.items() if key != "type"})
              for row in payload["content"]]
    details = StopDetails(**payload["stop_details"]) if payload.get("stop_details") else None
    usage = Usage(**{key: value for key, value in payload["usage"].items() if key != "request_ids"})
    response = Messaggio(payload["id"], payload["model"], blocks, payload["stop_reason"],
        stop_details=details, usage=usage, provider=payload.get("provider"),
        finish_reason=payload.get("finish_reason"))
    response.request_id = payload.get("request_id")
    usage.request_id = response.request_id
    return response


def _provably_unbilled(exc):
    """Seconds to wait before ONE retry when the failure provably never reached a model, else None.

    KA (05/10, main): the rule lives in core/unbilled.provably_unbilled, shared with the
    weekly path, so the two can no longer diverge. Stricter than before: an error carrying
    a provider generation id, or a 402 whose HTTP status was not measured as 402 by
    llm_client._invia (http_status), is NOT provably unbilled and stays unknown.
    TI's inner client is llm_client.OpenRouterClient, so create() and stream().__enter__
    failures come from _invia with http_status measured; a missing status is unknown.
    """
    return _core_provably_unbilled(exc)


def _settle_unbilled_or_unknown(gate, request_id, exc):
    """True when the reservation was released as provably unbilled (caller may retry once)."""
    wait = _provably_unbilled(exc)
    if wait is None:
        gate._unknown(request_id, exc)
        return None
    gate._durable(gate.store.release_cost, gate.run_id, request_id,
                  reason="provably unbilled pre-provider failure: " + type(exc).__name__ + ": " + str(exc)[:300],
                  receipt={"request_sha256": gate._requests.get(request_id, {}).get("request_sha256"),
                           "billable": False, "unbilled_evidence": type(exc).__name__ + ": " + str(exc)[:600],
                           "transport_phase": getattr(exc, "transport_phase", None)})
    return wait


class _BudgetedClient:
    def __init__(self, inner, gate, role):
        self.messages = _BudgetedMessages(inner.messages, gate, role)


class _BudgetedMessages:
    def __init__(self, inner, gate, role):
        self.inner, self.gate, self.role = inner, gate, role

    def create(self, **kwargs):
        projection = kwargs.pop("model_authoring_context_projection", None)
        recovered = self.gate._reuse(kwargs, self.role)
        if recovered is not None:
            return recovered
        # MOD-CAP: un antenato pagato al cap richiesto e' gia' stato cercato sopra (corpo originale);
        # il lavoro nuovo parte al tetto di uscita del contratto. R-MOD F3: l'adattamento avviene
        # PRIMA della proiezione, cosi' il pin della proiezione e il corpo inviato coincidono.
        adattati = self.gate._al_tetto(kwargs, self.role)
        if adattati is not kwargs:
            recovered = self.gate._reuse(adattati, self.role)
            if recovered is not None:
                return recovered
            kwargs = adattati
        if projection is not None:
            kwargs = self.gate._project_model_authoring_request(kwargs, projection, self.role)
            recovered = self.gate._reuse(kwargs, self.role)
            if recovered is not None:
                return recovered
        request_id, kw = self.gate._reserve(kwargs, self.role)
        retried = False
        while True:
            try:
                result = self.inner.create(**kw)
                break
            except Exception as exc:
                # One new reservation and retry only after a provably unbilled failure.
                wait = _settle_unbilled_or_unknown(self.gate, request_id, exc)
                if wait is None or retried:
                    raise
                retried = True
                import time
                time.sleep(wait)
                request_id, kw = self.gate._reserve(kwargs, self.role)
        try:
            self.gate._reconcile(request_id, result, kwargs["model"])
        except Exception as exc:
            exc.request_id = request_id
            raise
        return result

    def stream(self, **kwargs):
        return _BudgetedStream(self.inner, self.gate, self.role, kwargs)


class _BudgetedStream:
    def __init__(self, inner, gate, role, kwargs):
        self.inner, self.gate, self.role, self.kwargs = inner, gate, role, kwargs
        self.request_id = None
        self.reconciled = False
        self.unknown_marked = False

    def __enter__(self):
        self.recovered = self.gate._reuse(self.kwargs, self.role)
        if self.recovered is not None:
            self.reconciled = True
            return self
        adattati = self.gate._al_tetto(self.kwargs, self.role)   # MOD-CAP (vedi create)
        if adattati is not self.kwargs:
            self.recovered = self.gate._reuse(adattati, self.role)
            if self.recovered is not None:
                self.reconciled = True
                return self
            self.kwargs = adattati
        self.request_id, kw = self.gate._reserve(self.kwargs, self.role)
        retried = False
        while True:
            try:
                self.context = self.inner.stream(**kw)
                self.stream = self.context.__enter__()
                break
            except Exception as exc:
                wait = _settle_unbilled_or_unknown(self.gate, self.request_id, exc)
                if wait is None or retried:
                    self.unknown_marked = wait is None
                    self.reconciled = wait is not None  # released: nothing left to settle on exit
                    raise
                retried = True
                import time
                time.sleep(wait)
                self.request_id, kw = self.gate._reserve(self.kwargs, self.role)
        return self

    def get_final_message(self):
        if self.recovered is not None:
            return self.recovered
        try:
            response = self.stream.get_final_message()
        except Exception as exc:
            self.gate._unknown(self.request_id, exc)
            self.unknown_marked = True
            raise
        try:
            self.gate._reconcile(self.request_id, response, self.kwargs["model"])
        except Exception as exc:
            self.unknown_marked = True
            exc.request_id = self.request_id
            raise
        self.reconciled = True
        return response

    def __exit__(self, exc_type, exc, tb):
        if self.recovered is not None:
            return False
        try:
            return self.context.__exit__(exc_type, exc, tb)
        finally:
            if self.request_id and not self.reconciled and not self.unknown_marked:
                self.gate._unknown(self.request_id, exc or RuntimeError("stream senza receipt"))


def _verified_candidate_valuations(blackboard):
    """Only exact candidate generations whose registered workbook bytes still match."""
    if is_research_mode(blackboard):
        return []
    from bellomberg.core.paths import MODELS_DIR, REPORT_DIR
    from bellomberg.reporting.trade_idea_delivery import _candidate_workbooks

    ticker = blackboard.target_ticker
    generations = getattr(blackboard, "valuation_generations", ())
    current = (getattr(blackboard, "valuation_results", {}) or {}).get(ticker) or {}
    if current.get("generation_id"):
        generations = [row for row in generations if isinstance(row, dict)
                       and row.get("generation_id") == current["generation_id"]]
    provisional = [{"snapshot_id": row.get("snapshot_id"),
                    "generation_id": row.get("generation_id"),
                    "valuation_date": row.get("valuation_date"),
                    "interpretation": "Artifact verified before Capo review"}
                   for row in generations if isinstance(row, dict)
                   and row.get("ticker") == ticker and row.get("generation_id")]
    checked = _candidate_workbooks(ticker, generations,
        getattr(blackboard, "valuation_attempts", ()),
        [MODELS_DIR, REPORT_DIR, *getattr(blackboard, "model_roots", ())], provisional)
    return [{key: row[key] for key in
             ("snapshot_id", "generation_id", "valuation_date", "workbook_sha256", "model_values", "model_exhibits")}
            for row in checked["valuations"] if row.get("status") == "ready"
            and row.get("valuation_date")]


def _valuation_refs_match(result, verified):
    expected = {(row["snapshot_id"], row["generation_id"], row["valuation_date"])
                for row in verified}
    supplied = result.get("valuation_refs") or []
    actual = {(row.get("snapshot_id"), row.get("generation_id"), row.get("valuation_date"))
              for row in supplied}
    return len(actual) == len(supplied) and actual == expected


def _capo_finalization_policy(blackboard):
    gate = getattr(blackboard, "budget_gate", None)
    resolve = getattr(gate, "capo_finalization", None)
    policy = resolve() if callable(resolve) else None
    if policy is not None and not is_research_mode(blackboard):
        raise ValueError("Research Capo finalization cannot change a legacy run")
    return policy


def _finalization_red_team_block(blackboard, report):
    """Omit only a duplicate serialization, after exact content equality."""
    original = json.loads(report)
    ledger = blackboard.data.get("_research_review") or {}
    if (not isinstance(original, dict)
            or set(original) != {"objections", "decisive_questions"}
            or original["objections"] != [row["objection"] for row in ledger.get("objections", [])]
            or original["decisive_questions"] != blackboard.data.get("_decisive_questions")):
        raise ValueError("Red Team cannot be deduplicated without losing its exact content")
    return ("Red Team: every original objection is included verbatim once in the authoritative ledger; "
            "the unchanged decisive questions are supplied separately.")


def _parse_capo_json(content):
    """Accept a whole JSON response, optionally in one Markdown code fence.

    Preserve the original provider text in the journal/blackboard. Only the
    presentation wrapper is removed; prose, incomplete fences and malformed
    JSON are never repaired or searched for an embedded result.
    """
    from bellomberg.core.trade_idea_contract import parse_capo_json
    return parse_capo_json(content)


def _accept_capo_response(blackboard, response, verified_valuations, incomplete_reasons):
    """Apply the identical public-output contract to a new or exactly replayed response."""
    from bellomberg.core.trade_idea_contract import validate_result
    content = "\n".join(str(block.text) for block in (getattr(response, "content", None) or [])
                        if getattr(block, "type", None) == "text" and getattr(block, "text", None))
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ValueError("Capo Trade Idea troncato al limite di output")
    if not content.strip():
        raise ValueError("Capo Trade Idea senza output JSON")
    blackboard.write("_capo", 3, content)
    wire_result = _parse_capo_json(content)
    result = validate_result(wire_result, run_id=blackboard.run_id,
                             ticker=blackboard.target_ticker, pm_view=blackboard.pm_view,
                             execution_policy=execution_policy(blackboard))
    # Provider JSON mode does not enforce wire keys; reject reader defaults here.
    if wire_result != {key: value for key, value in result.items()
                               if key not in {"run_id", "run_type", "pm_view"}}:
        raise ValueError("Capo Trade Idea omitted explicit fields from the complete JSON contract")
    if not _valuation_refs_match(result, verified_valuations):
        raise ValueError("Capo non ha recensito esattamente le generazioni Excel verificate")
    if incomplete_reasons and result["judgment"] != "incomplete":
        raise ValueError("Capo non ha segnalato la run incompleta: "
                         + "; ".join(incomplete_reasons))
    return result


def run_trade_idea_capo(blackboard, *, portfolio, mandate, decision_context=None,
                        sizing=None, client=None):
    """One structured Capo judgment, using the existing selected desk reports."""
    from bellomberg.agents.capo import scegli_report_specialisti
    from bellomberg.core.trade_idea_contract import (
        CAPO_TRADE_IDEA_INSTRUCTIONS, CAPO_RESEARCH_INSTRUCTIONS, CAPO_RESEARCH_INSTRUCTIONS_V2, CAPO_RESEARCH_INSTRUCTIONS_V3,
        CAPO_RESEARCH_INSTRUCTIONS_V4, trade_idea_result_schema, validate_result)
    from bellomberg.valuation.sector_analysis import valuation_results_block
    from bellomberg.core.language import output_language_instruction
    from bellomberg.core import mandato_pm
    text_policy = mandato_pm.text_policy_for_board(blackboard)
    verified_valuations = _require_final_desk_models(blackboard)

    chosen = scegli_report_specialisti(
        blackboard.data, orari=getattr(blackboard, "orari_report", None))
    # Final Trade Idea decisions cannot silently select an older round.
    gaps = (blackboard.data.get("_desk_gaps") or {}) if is_research_mode(blackboard) else {}
    for name in TRADE_IDEA_DESKS:
        if name in gaps:
            sealed_r1 = ((blackboard.data.get("_research_thesis") or {}).get("reports") or {}).get(name)
            chosen[name] = {"round": 1 if sealed_r1 else None, "no_report": False, "report":
                ("[DECLARED GAP] Final desk report unavailable: " + str(gaps[name].get("message"))[:600]
                 + ("\nSealed round-1 report follows; it was not revised after the Red Team.\n\n" + sealed_r1
                    if sealed_r1 else "\nNo sealed report exists for this desk."))}
            continue
        chosen[name] = {**chosen.get(name, {}), "report": blackboard.read(name, 2), "round": 2}
    missing = [name for name in ("macro", "eventdesk", "crypto", "fundamentals", "quant", "options")
               if name not in chosen or chosen[name].get("no_report")]
    red = blackboard.get_latest("red_team")
    from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
    reason = (motivo_critica_non_utilizzabile(red.get("report"))
              if red else "Red Team obbligatorio assente")
    incomplete_reasons = list(getattr(blackboard, "required_failure_reasons", ()) or ())
    if missing:
        incomplete_reasons.append("report desk mancanti: " + ", ".join(missing))
    if reason or (red and "CRITICA TRONCATA" in red.get("report", "")):
        incomplete_reasons.append("Red Team non utilizzabile: " + (reason or "critica troncata"))
    gate = getattr(blackboard, "budget_gate", None)
    if gate is None:
        raise ValueError("Trade Idea Capo senza budget gate")
    if execution_policy(blackboard) in RESEARCH_POLICIES:
        # This runs before rebuilding history, book text or any dynamic prompt.
        # The store verifies the saved wire request, paid receipt and economics.
        response = gate.reuse_capo_response()
        if response is not None:
            blackboard.mark_specialist_start("capo", 3)
            try:
                result = _accept_capo_response(blackboard, response, verified_valuations, incomplete_reasons)
                blackboard.record_usage("capo", 3, model_for_role("capo", blackboard), {},
                    duration_s=0, api_calls=0, cache_ttl=None, status="reused")
                blackboard.mark_specialist_done("capo")
                return result
            except Exception as exc:
                blackboard.mark_specialist_error("capo", str(exc))
                raise
    from bellomberg.core import mandato_pm
    research = is_research_mode(blackboard)
    finalization = _capo_finalization_policy(blackboard)
    policy = execution_policy(blackboard)
    policy_v2 = policy in RESEARCH_POLICIES
    policy_v4 = policy == EXECUTION_POLICY_V4
    research_prompt = (CAPO_RESEARCH_INSTRUCTIONS_V4 if policy_v4
                       else CAPO_RESEARCH_INSTRUCTIONS_V3 if policy == EXECUTION_POLICY_V3
                       else CAPO_RESEARCH_INSTRUCTIONS_V2 if policy_v2 else CAPO_RESEARCH_INSTRUCTIONS)
    evidence_rule = ("\n\nPer ogni Evidence.source usa il formato esatto '[src: nome_tool] fonte'. "
                     "Il nome_tool deve essere una chiamata riuscita nei report della run; "
                     "Evidence.as_of deve essere la data osservata nella risposta del tool, "
                     "non la data di oggi inferita. Mantieni unità, valuta e URL presenti nel tool.")
    # /2-/3 keep their byte-identical inline-citation rule (paid resumes compare the
    # request sha256); /4 forbids tags in prose and binds numbers through evidence_ids.
    citation_rule = (("\nNella prosa NON scrivere [src: ...], [evidence: ...] o altri marcatori di fonte. "
                      "La tracciabilita' sta nei campi: ogni cifra di un campo, pilastro, scenario, riga "
                      "o sezione deve comparire nell'output delle Evidence elencate nei suoi evidence_ids "
                      "(per le sezioni del dossier: gli evidence_ids della sezione). Metti negli evidence_ids "
                      "di ogni campo tutte le Evidence da cui ne prendi i numeri. probability_pct e "
                      "price_target degli scenari e variant_view.committee sono stime del comitato: "
                      "dichiarale come stime, con il metodo e gli evidence_ids degli input, e scrivile solo "
                      "nei campi della stima stessa (analysis e method dello scenario, rationale della riga "
                      "variant_view): altrove nel memo ogni numero va attestato da una Evidence. "
                      "Ogni numero mantiene segno e scala della fonte (milioni, miliardi, %).")
                     if policy_v4 else
                     ("\nOgni frase con una cifra quantitativa e ogni tabella deve citare "
                      "nello stesso contesto [src: nome_tool] oppure [evidence: id] "
                      "collegato a una Evidence realmente verificabile. I valori osservati "
                      "devono comparire nel tool citato. Per una derivazione ipotetica "
                      "usa [assumption] o [ipotesi], formula esplicita e input citati; "
                      "non presentarla come dato osservato."))
    from bellomberg.core.language import text as language_text
    minimum_action_label = ("posizione minima" if text_policy is None else language_text(
        "soglia minima d'azione del mandato (su NAV)", "minimum action amount under the mandate (on NAV)"))
    system = ((research_prompt if research else CAPO_TRADE_IDEA_INSTRUCTIONS)
              + "\n\n" + output_language_instruction(blackboard.language)
              + evidence_rule + citation_rule
              + "\n\n" + mandato_pm.blocco_prompt(mandate, **({"text_policy": text_policy} if text_policy is not None else {}))
              + ("\n\nTAGLIA DELLA PROPOSTA: la SIZING BAND del server applica le regole di questo "
                 "mandato (size di una nuova posizione, peso massimo per posizione, " + minimum_action_label + ", "
                 "cassa minima), limitate dai limiti misurati del motore di sizing; azione e importo si "
                 "scelgono solo dentro la fascia di quell'azione." if policy_v4 else ""))
    red_notes = _red_inadmissible_gaps(blackboard) if research else []
    if red_notes:
        system += ("\n\nRED TEAM, OBIEZIONI DICHIARATE NON AMMISSIBILI DAL SERVER: " + " ".join(red_notes)
                   + " Non trattarle come obiezioni discusse; se la critica del Red Team e' una lacuna, "
                     "la convinzione non puo' essere ALTA.")
    if incomplete_reasons:
        system += ("\n\nQuesta run ha componenti obbligatori incompleti: "
                   + "; ".join(incomplete_reasons)
                   + ". Produce SOLO judgment=incomplete e proposal=null, "
                     "con dossier parziale e buchi espliciti.")
    system += ("\n\nFINAL RESEARCH TASK: conclude from the sealed dossier, six final desk reports and "
               "the actual objection/reply ledger. Retain citations, dissent, assumptions and missing data. "
               "No workbook compilation, model reference or mandatory fair value. Emit the complete JSON now."
               if research else "\n\nFINAL COMPILATION TASK: use the supplied server-verified exact candidate model, "
               "six final desk reports and authoritative objection/reply ledger to compile "
               "the final investment judgment now. Keep answered and unresolved issues "
               "distinct. Do not reopen "
               "research, rebuild the model, or repeat the complete driver tables in output. "
               "Preserve the full required dossier, every material view and dissent, source "
               "citations, dates, units, currency, scenarios and exact model references. "
               "Follow the complete JSON schema; keep the authoritative model-review ledger "
               "and express missing evidence explicitly. This task changes no financial "
               "assumption or acceptance condition.")
    try:
        from bellomberg.agents.chat_tools import _compatta_portfolio_live
        book_context = _compatta_portfolio_live(portfolio) if portfolio else {"status": "book_unavailable"}
    except Exception as exc:
        book_context = {"status": "book_unavailable", "reason": type(exc).__name__ + ": " + str(exc)[:150]}
    parts = [
        "TRADE IDEA: candidato unico " + str(blackboard.target_ticker),
        "Cutoff delle osservazioni raccolte per questa analisi (UTC): "
            + str(blackboard.data.get("_data_cutoff") or "non disponibile"),
        "View PM originale (tesi da verificare):\n" + (blackboard.pm_view or "(assente)"),
        "Storico del candidato:\n" + (blackboard.candidate_history or "(non disponibile)"),
        "Book reale per compatibilita'/sizing, non lista di nuovi candidati:\n"
            + json.dumps(book_context, ensure_ascii=False, default=str, separators=(",", ":")),
        "Decisioni, feedback e trade recenti dello stesso ticker. Copri gli ID in history_review; "
            "se non disponibili, dichiara il buco:\n"
            + json.dumps(decision_context or {}, ensure_ascii=False, default=str, separators=(",", ":")),
        "Report specialisti selezionati:\n" + json.dumps(chosen, ensure_ascii=False, default=str, separators=(",", ":")),
        "Red Team obbligatorio, obiezioni integrali:\n"
            + str(red["report"] if red else "[KO: Red Team assente]"),
        ('Modalita: ricerca societaria documentata; Excel non previsto.' if research else
            "Valutazioni e tentativi del SOLO candidato:\n" + valuation_results_block(blackboard.valuation_results)),
        ("Sealed research thesis, source inventory and exact hash references:\n" if research else
            "Driver, assumptions, evidence and cells of the exact final model:\n")
            + json.dumps(candidate_model_context(blackboard, purpose="committee"), ensure_ascii=False, default=str, separators=(",", ":")),
        "Authoritative review and addressed objection ledger. Preserve concessions and "
            "unresolved dissent; the ledger is authoritative and is copied by the server:\n"
            + json.dumps(blackboard.data.get('_research_review') if research else _model_audit(blackboard), ensure_ascii=False, default=str, separators=(",", ":")),
        "Decisive committee questions:\n" + json.dumps(blackboard.data.get("_decisive_questions") or [], separators=(",", ":")),
        ("Nessun workbook previsto: valuation_refs=[] e model_review=null.\n" if research else
        "Generazioni candidate con workbook e sidecar verificati (insieme esaustivo). "
            "Copia OGNI snapshot_id, generation_id e valuation_date esattamente in valuation_refs, "
            "con interpretazione delle basi e dei limiti; se vuoto usa []. Non citare come "
            "verificate generazioni fuori elenco:\n")
            + json.dumps(verified_valuations, ensure_ascii=False, default=str, separators=(",", ":")),
        "Sizing deterministico; non proporre importi oltre i limiti verificati:\n"
            + json.dumps(sizing or {"status": "unavailable"}, ensure_ascii=False, default=str, separators=(",", ":")),
        "Quotazione candidato ricampionata prima del giudizio. E' daily close, non intraday; "
            "mantieni fonte, data e valuta senza assumere aggiornamenti non osservati:\n"
            + json.dumps(blackboard.data.get("_candidate_quote_initial") or
                         {"status": "unavailable"}, ensure_ascii=False, default=str, separators=(",", ":")),
        "Required complete JSON schema. Include every declared object field, including "
            "nullable fields as null and empty arrays explicitly. Return only the JSON object:\n"
            + json.dumps(trade_idea_result_schema(policy), ensure_ascii=False, separators=(",", ":")),
        "Produci adesso il risultato JSON. Una proposta operativa richiede dati, "
            "mandato, sizing e feedback verificabili; se mancano, proposal=null.",
    ]
    if finalization is not None:
        if finalization.get("context_projection") != "sealed_all_rounds_red_team_dedup_v1":
            raise ValueError("Unknown authorized Capo input projection")
        parts[7] = _finalization_red_team_block(blackboard, red["report"])
    elif policy_v2:
        # Drop only an exactly duplicated Red Team serialization. A richer or
        # different report remains integral rather than losing an interpretation.
        try:
            parts[7] = _finalization_red_team_block(blackboard, red['report'])
        except (ValueError, TypeError, KeyError):
            pass
    material = blackboard.data.get("_capo_finalization_material")
    if research and material is not None:
        raise ValueError('Legacy model finalization material cannot replace sealed research')
    if material is not None:
        # A draft is an analyst input, never a substitute for the provider verdict.
        # Bind it to the already reviewed reports, objections and exact workbook.
        import hashlib
        if (not isinstance(material, dict)
                or material.get("kind") != "capo_finalization_material_v1"
                or material.get("source_fingerprint") !=
                    (blackboard.source_qualification or {}).get("fingerprint")
                or material.get("reports_sha256") != {
                    name: hashlib.sha256(blackboard.read(name, 2).encode("utf-8")).hexdigest()
                    for name in TRADE_IDEA_DESKS}
                or material.get("red_sha256") != hashlib.sha256(
                    str(red["report"] if red else "").encode("utf-8")).hexdigest()
                or material.get("objections_sha256") !=
                    _plan_digest(blackboard.data.get("_objections") or [])
                or material.get("verified_model_refs") != [
                    {key: row[key] for key in ("snapshot_id", "generation_id",
                                             "valuation_date", "workbook_sha256")}
                    for row in verified_valuations]):
            raise ValueError("Capo finalization draft differs from the current reviewed checkpoint")
        draft = validate_result(material.get("draft"), run_id=blackboard.run_id,
                                ticker=blackboard.target_ticker, pm_view=blackboard.pm_view,
                                execution_policy=policy)
        if (material["draft"] != {key: value for key, value in draft.items()
                                  if key not in {"run_id", "run_type", "pm_view"}}
                or not _valuation_refs_match(draft, verified_valuations)
                or not isinstance(material.get("economic_context"), dict)
                or not isinstance(material.get("source_evidence_catalog"), list)):
            raise ValueError("Capo finalization draft is incomplete or references another model")
        system += ("\n\nFINAL DRAFT VERIFICATION: the labelled orchestrator draft is unapproved "
                   "analyst material. Verify its economic conclusion against the supplied "
                   "dated evidence, exact model values, material desk views and full "
                   "objection/reply ledger. Correct errors and preserve material dissent. "
                   "Perform this final verification once and then emit the complete final "
                   "JSON, including all eleven substantive dossier sections. Reserve enough "
                   "of the response for the public dossier. Do not restart the research or "
                   "reconstruct the already compiled workbook. The provider remains solely "
                   "responsible for the final judgment; this draft grants no acceptance. "
                   "model_review may be null: the server supplies the authoritative ledger.")
        parts = [*parts[:6],
            "UNAPPROVED ORCHESTRATOR DRAFT; verify and correct, then return the complete result:\n"
                + json.dumps(material["draft"], ensure_ascii=False, separators=(",", ":")),
            "Exact model inputs and material economic desk views; full originals remain in the run audit:\n"
                + json.dumps(material["economic_context"], ensure_ascii=False, separators=(",", ":")),
            "Actual successful source/evidence catalog; preserve observation dates and units:\n"
                + json.dumps(material["source_evidence_catalog"], ensure_ascii=False, separators=(",", ":")),
            parts[10], parts[11],
            "Exhaustive verified model references and exact calculated values:\n"
                + json.dumps([{key: row[key] for key in ("snapshot_id", "generation_id",
                    "valuation_date", "workbook_sha256", "model_values")}
                    for row in verified_valuations], ensure_ascii=False, separators=(",", ":")),
            *parts[13:]]
    if policy_v4:
        # Inserted after every positional rewrite of parts (indices 7, 10-13 above):
        # the /2-/3 bodies stay byte-identical. Placed right after the sizing block.
        parts.insert(len(parts) - 3, _sizing_band_block(sizing, portfolio, blackboard.target_ticker, mandate, text_policy=text_policy))
    gate = getattr(blackboard, "budget_gate", None)
    if gate is None:
        raise ValueError("Trade Idea Capo senza budget gate")
    from bellomberg.agents.specialists.base import timeout_specialisti
    output_cap = _remaining_stage_token_cap(blackboard, "capo")
    raw = client or OpenRouterClient(timeout=timeout_specialisti(output_cap), max_retries=0)
    budgeted = gate.wrap_client(raw, role="capo")
    blackboard.mark_specialist_start("capo", 3)
    import time
    start = time.perf_counter()
    try:
        with budgeted.messages.stream(
            model=model_for_role("capo", blackboard), max_tokens=output_cap,
            thinking=(finalization["thinking"] if finalization is not None
                      else role_thinking(blackboard, 'capo')), system=system,
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
            response_format={"type": "json_object"},
        ) as stream:
            response = stream.get_final_message()
        result = _accept_capo_response(blackboard, response, verified_valuations, incomplete_reasons)
        usage = getattr(response, "usage", None)
        blackboard.record_usage("capo", 3, model_for_role("capo", blackboard),
                                usage.to_dict() if hasattr(usage, "to_dict") else {},
                                duration_s=time.perf_counter() - start, api_calls=1,
                                cache_ttl=None, status="ok")
        blackboard.mark_specialist_done("capo")
        return result
    except Exception as exc:
        if "response" in locals():
            usage = getattr(response, "usage", None)
            blackboard.record_usage("capo", 3, model_for_role("capo", blackboard),
                                    usage.to_dict() if hasattr(usage, "to_dict") else {},
                                    duration_s=time.perf_counter() - start, api_calls=1,
                                    cache_ttl=None, status="api_error")
        blackboard.mark_specialist_error("capo", str(exc))
        raise


def bind_trade_idea_preparer(gate, ticker, *, output_dir=None, phase="preparation"):
    """An isolated per-run proposer; no legacy policy, trigger or journal is read."""
    from pathlib import Path
    from bellomberg.core.paths import DATA_DIR
    from bellomberg.valuation.preparation_ai import BudgetedProposer, live_metadata
    run = gate.store.get_run(gate.run_id)["run"]
    if run["ticker"] != ticker:
        raise ValueError("preparation ticker differs from authorized run")
    activity = "model_revision" if phase == "revision" else "model_preparation"
    if activity not in (run.get("authorization") or {}).get("activities", ()):
        return None, {"status": "disabled", "reason": "run authorization does not include " + activity}
    directory = Path(output_dir or (DATA_DIR / "trade_ideas" / gate.run_id))

    def call(**request):
        from bellomberg.agents.specialists.base import timeout_specialisti
        client = OpenRouterClient(timeout=timeout_specialisti(request["max_tokens"]), max_retries=0)
        try:
            return gate.wrap_client(client, role="aux:" + phase).messages.create(**request)
        finally:
            client._http.close()

    proposer = BudgetedProposer(directory / ("model-" + phase + ".sqlite"),
        authorized_usd=run["budget_limit_usd"], model=model_for_role("aux", gate),
        max_tokens=gate._cap_al_tetto("aux:" + phase, 128000),   # MOD-CAP: tetto del contratto
        thinking={"type": "effort", "effort": "max"}, metadata=live_metadata,
        call=call, automatic_sections=True, provider_context_check=True)
    return proposer, {"status": "enabled", "authorization": "single_run",
                      "model": model_for_role("aux", gate), "phase": phase}


def _candidate_history(store, ticker, current_id):
    page = store.list_runs(ticker=ticker, limit=21)
    rows = page["runs"]
    history = []
    for row in rows:
        if row["id"] == current_id:
            continue
        previous = store.get_run(row["id"])
        result = previous.get("result") or {}
        if len(history) >= 20:
            break
        history.append({"run_id": row["id"], "at": row["created_at"],
                        "technical_status": row["technical_status"],
                        "judgment": result.get("judgment"), "summary": result.get("summary"),
                        "destination": row.get("destination")})
    total_prior = max(0, page["total"] - (1 if any(row["id"] == current_id for row in rows) else 0))
    return {"total_prior": total_prior, "included": len(history),
            "omitted": max(0, total_prior - len(history)), "runs": history}


_DESK_LOCAL_MESSAGES = ("specialist response truncated", "specialist response failed",
                        " response incomplete: ", "Desk final report did not discuss the supplied sealed research")


def _desk_local_failure(error):
    """Explicit allow-list of failures confined to one desk's own answer.

    Truncation, refusal or an unusable final report, and provider errors on that
    desk's request (their cost state stays guarded by the store: unknown blocks all
    spending, released is provably unbilled). Everything else is run-level.
    """
    from bellomberg.core.llm_client import APIError
    if isinstance(error, APIError):
        return True
    text = str(error)
    return isinstance(error, RuntimeError) and any(marker in text for marker in _DESK_LOCAL_MESSAGES)


def _memo_facts(checkpoint, cutoff):
    """Numbers for the memo's tables and charts, read by Python from the tool receipts.
    A failed extraction is a declared gap in the PDF, never a memo with invented numbers."""
    import logging
    from bellomberg.reporting.trade_idea_facts import extract_facts
    try:
        return extract_facts(checkpoint, cutoff=cutoff)
    except Exception as exc:
        # extract_facts never raises by design: this is a bug, kept visible in the log and the PDF.
        logging.getLogger(__name__).exception("Trade Idea memo facts extraction failed")
        return {"gaps": ["Estrazione dei dati numerici fallita: " + type(exc).__name__ + ": " + str(exc)[:300]]}


def _desk_annex(data):
    """Complete final desk reports, Red Team review and objection ledger for the memo annex.

    Built from the same checkpoint data the Capo read (blackboard.data or the persisted
    checkpoint), so a recovered package renders the identical annex. Nothing is cut;
    a missing report is declared, never replaced by an earlier round in silence.
    """
    from bellomberg.agents.capo import scegli_report_specialisti
    if not isinstance(data, dict) or not data:
        return {"version": 1, "unavailable": "checkpoint della run assente o illeggibile"}
    selected = scegli_report_specialisti(data)
    desks = []
    gaps = data.get("_desk_gaps") or {}
    sealed = ((data.get("_research_thesis") or {}).get("reports") or {}) if isinstance(
        data.get("_research_thesis"), dict) else {}
    for name in TRADE_IDEA_DESKS:
        if name in gaps:
            note = ("[LACUNA DICHIARATA] Report finale non disponibile: "
                    + str(gaps[name].get("message") or "")[:600])
            if sealed.get(name):
                desks.append({"desk": name, "round": 1, "status": "ready",
                              "text": note + "\n\nSegue il round 1 sigillato, non rivisto dopo il Red Team.\n\n"
                              + str(sealed[name])})
            else:
                desks.append({"desk": name, "round": None, "status": "missing", "text": ""})
            continue
        row = selected.get(name) or {}
        text = str(row.get("report") or "")
        if not row or row.get("no_report") or not text.strip():
            desks.append({"desk": name, "round": None, "status": "missing", "text": ""})
        else:
            desks.append({"desk": name, "round": row.get("round"), "status": "ready", "text": text})
    red_rounds = data.get("_red_team") or data.get("red_team") or {}
    red = ""
    if isinstance(red_rounds, dict) and red_rounds:
        latest = max(red_rounds, key=lambda key: int(key) if str(key).lstrip("-").isdigit() else -1)
        red = str(red_rounds[latest] or "")
    ledger = []
    for row in data.get("_objections") or []:
        objection = row.get("objection") if isinstance(row, dict) else None
        if not isinstance(objection, dict):
            continue
        ledger.append({key: objection.get(key) for key in ("id", "desk", "category", "material")}
                      | {"objection": str(objection.get("objection") or objection.get("text") or ""),
                         "requested_change": str(objection.get("requested_change") or ""),
                         "state": row.get("state"), "response": str(row.get("response") or "")})
    for row in data.get("_red_inadmissible_objections") or []:  # 06/10: dichiarate, non discusse
        objection = row.get("objection") if isinstance(row, dict) else None
        if not isinstance(objection, dict):
            continue
        ledger.append({key: objection.get(key) for key in ("id", "desk", "category", "material")}
                      | {"objection": str(objection.get("objection") or ""),
                         "requested_change": str(objection.get("requested_change") or ""),
                         "state": "inammissibile",
                         "response": (str(row.get("reason") or RED_INADMISSIBLE_REASON) + ": "
                                      + ", ".join(str(ref) for ref in row.get("outside_refs") or []))})
    return {"version": 1, "desks": desks, "red_team": red, "ledger": ledger,
            "decisive_questions": [str(item) for item in data.get("_decisive_questions") or []]}


def _reports(blackboard):
    from bellomberg.agents.capo import scegli_report_specialisti
    selected = scegli_report_specialisti(blackboard.data, orari=blackboard.orari_report)
    return [{"specialist": name, "round": row["round"], "text": row["report"],
             "status": "unavailable" if row["no_report"] else "ready"}
            for name, row in selected.items()
            if name in ("macro", "eventdesk", "crypto", "fundamentals", "quant", "options")]


def _progress(blackboard, phase, *, events=()):
    checkpoint = _run_checkpoint(blackboard)
    return {"phase": phase, "updated_at": datetime.now(timezone.utc).isoformat(),
            **({'analysis_mode': RESEARCH_ANALYSIS_MODE,
                'research_review': deepcopy(blackboard.data.get('_research_review'))}
               if is_research_mode(blackboard) else {}),
            "checkpoint": checkpoint, "checkpoint_sha256": _plan_digest(checkpoint),
            "primary_failure": blackboard.data.get("_primary_failure"),
            "desk_gaps": deepcopy(blackboard.data.get("_desk_gaps") or {}),
            "catalog_notices": list(getattr(getattr(blackboard, "budget_gate", None), "catalog_notices", None) or [])[-20:],
            "model_contract": deepcopy(getattr(getattr(blackboard, "budget_gate", None), "model_contract_notice", None)),
            "remaining_work": _remaining_work(blackboard),
            "specialists": [{"name": name, "round": blackboard.current_round,
                             "status": status} for name, status in blackboard.specialist_status.items()],
            "tools": [{"name": row.get("tool", "?"), "specialist": row.get("specialist"),
                       "status": "recorded", "at": row.get("time")}
                      for row in blackboard.tool_log[-50:]],
            "events": [{"at": datetime.now(timezone.utc).isoformat(), "message": str(event)}
                       for event in events],
            "reports": _reports(blackboard),
            "red_team": blackboard.get_latest("red_team"),
            "source_qualification": source_qualification_summary(getattr(blackboard, "source_qualification", {})),
            "model_review": blackboard.data.get("_model_review"),
            "model_build_checkpoint": {"source_fingerprint": getattr(blackboard, "source_qualification", {}).get("fingerprint"),
                "input_basis": deepcopy(blackboard.data.get("_model_input_basis")),
                "draft": deepcopy(blackboard.data.get("_model_input_draft")),
                "draft_history": deepcopy(blackboard.data.get("_model_draft_history", [])),
                "last_plan_error": deepcopy(blackboard.data.get("_model_plan_last_error")),
                "plan_input_transports": deepcopy(blackboard.data.get("_model_plan_input_transports", [])),
                "compilation_attempts": deepcopy(blackboard.data.get("_model_compilation_attempts", [])),
                "completion_error": blackboard.data.get("_model_completion_error"),
                "consultations": deepcopy(blackboard.data.get("_model_consultations", [])),
                "fundamentals_received_consultations": deepcopy(blackboard.data.get("_fundamentals_received_consultations", {})),
                "input_driver_reads": deepcopy(blackboard.data.get("_input_driver_reads", {}))},
            "revision_requests": blackboard.data.get("_revision_requests", []),
            "objections": blackboard.data.get("_objections", []),
            "objection_history": blackboard.data.get("_objection_history", []),
            "decisive_questions": blackboard.data.get("_decisive_questions", []),
            "round_dependencies": ({'research_dossier_after_round': 1,
                'independent_analysis': [0, 1], 'review_desks': sorted(blackboard.r2_specialists)}
                if is_research_mode(blackboard) else {"initial_model_before_round": None,
                "model_built_during_round": 1, "model_author": "fundamentals",
                "independent_analysis": [0, 1], "review_desks": sorted(blackboard.r2_specialists)}),
            "review_history": blackboard.data.get("_review_history", []),
            "valuation_results": blackboard.valuation_results,
            "valuation_generations": blackboard.valuation_generations,
            "valuation_attempts": blackboard.valuation_attempts}


def _checkpoint_contract(blackboard):
    run = blackboard.budget_gate.store.get_run(blackboard.run_id)["run"]
    from bellomberg.core.mandato_pm import MANDATE_TEXT_POLICY_KEY, text_policy_from_context
    text_policy = text_policy_from_context(run)
    return {"version": 1, "ticker": run["ticker"], "language": run["language"],
        **({MANDATE_TEXT_POLICY_KEY: text_policy} if MANDATE_TEXT_POLICY_KEY in run else {}),
        **({'analysis_mode': RESEARCH_ANALYSIS_MODE} if is_research_mode(run) else {}),
        **({'execution_policy': execution_policy(run)} if execution_policy(run) is not None else {}),
        "view_text": run["view_text"], "models": run["models"],
        "source_fingerprint": (getattr(blackboard, "source_admission", None) or
                               getattr(blackboard, "source_qualification", {})).get("fingerprint")}


def _capo_input_fingerprint(blackboard, audit, mandate):
    inputs = {"reports": {name: blackboard.read(name, 2) for name in TRADE_IDEA_DESKS},
        "red": blackboard.get_latest("red_team"), "model_review": audit,
        **({'research_review': blackboard.data.get('_research_review')} if is_research_mode(blackboard) else {}),
        "contract": _checkpoint_contract(blackboard)}
    if execution_policy(blackboard) in RESEARCH_POLICIES:
        # Completed public JSON is reusable only on the same economic basis.
        # Dynamic candidate history and technical failures are deliberately absent.
        inputs['economic_inputs'] = {key: blackboard.data.get(key) for key in (
            '_research_thesis', '_sizing', '_decision_context', '_candidate_quote_initial',
            '_data_cutoff', '_portfolio_context', '_objections', '_objection_history',
            '_decisive_questions', '_identity')}
        book = inputs['economic_inputs']['_portfolio_context']
        if isinstance(book, dict):
            # MemoryDB stamps each rendering; quote/source dates remain binding.
            inputs['economic_inputs']['_portfolio_context'] = {
                key: value for key, value in book.items() if key != 'timestamp'}
        inputs['independent_reports'] = {desk: blackboard.read(desk, 1) for desk in TRADE_IDEA_DESKS}
        inputs['mandate'] = mandate
    return _plan_digest(inputs)


def _run_checkpoint(blackboard):
    with blackboard._lock:
        gate = getattr(blackboard, "budget_gate", None)
        contract = (_checkpoint_contract(blackboard) if isinstance(gate, TradeIdeaBudgetGate) else {
            "version": 1, "ticker": getattr(blackboard, "target_ticker", None),
            "source_fingerprint": getattr(blackboard, "source_qualification", {}).get("fingerprint")})
        return {"version": 1, "contract": contract, "data": deepcopy(blackboard.data),
            "orari_report": deepcopy(blackboard.orari_report), "current_round": blackboard.current_round,
            "tool_log": deepcopy(blackboard.tool_log), "tool_receipts": deepcopy(blackboard.tool_receipts),
            "usage_log": deepcopy(blackboard.usage_log),
            "valuation_results": deepcopy(blackboard.valuation_results),
            "valuation_generations": deepcopy(blackboard.valuation_generations),
            "valuation_attempts": deepcopy(blackboard.valuation_attempts),
            "specialist_checkpoints": deepcopy(getattr(blackboard, "specialist_checkpoints", {})),
            "model_roots": [os.fspath(path) for path in getattr(blackboard, "model_roots", ())]}


def _remaining_work(blackboard):
    completed = blackboard.data.get("_completed_stages", {})
    stages = [{"stage": f"{name}:R{round_n}", "complete": f"{name}:{round_n}" in completed}
              for round_n in range(3) for name in TRADE_IDEA_DESKS]
    stages.append({'stage': 'research_dossier', 'complete': bool(blackboard.data.get('_research_thesis'))}
        if is_research_mode(blackboard) else
        {"stage": "model_authoring", "complete": bool(_verified_candidate_valuations(blackboard))})
    stages.append({"stage": "red_team", "complete": bool(blackboard.data.get(
        '_red_research_review' if is_research_mode(blackboard) else "_red_model_review"))})
    missing_replies = _missing_research_replies(blackboard) if is_research_mode(blackboard) else []
    reply_tasks = blackboard.data.get('_research_reply_completions', {})
    pending_desks = {row['objection']['desk'] for row in missing_replies}
    pending_desks.update(desk for desk, task in reply_tasks.items() if not task.get('complete'))
    stages.extend({'stage': desk + ':objection_replies', 'complete': False}
                  for desk in TRADE_IDEA_DESKS if desk in pending_desks)
    stages.append({"stage": "capo", "complete": bool(blackboard.data.get("_capo_completed"))})
    output = {"stages": stages, "remaining": [item["stage"] for item in stages if not item["complete"]],
        "estimated_remaining_usd": None,
        "feasibility": "blocked" if blackboard.data.get("_primary_failure") else "not_guaranteed",
        "reason": "Future prompts, tool iterations and provider usage are not yet known; the accepted ceiling is unchanged",
        "missing_material_objections": [{'id': row['objection']['id'], 'desk': row['objection']['desk']}
                                        for row in missing_replies]}
    gate = getattr(blackboard, "budget_gate", None)
    if isinstance(gate, TradeIdeaBudgetGate):
        # These are reservation floors for a single request, not a forecast of
        # spend and not additive: settled requests release their unused reserve.
        floor_rows = []
        for stage in output["remaining"]:
            if stage == 'research_dossier':
                continue  # Local evidence seal, not another paid analytical request.
            checkpoint_key = "red_team:R1" if stage == "red_team" else stage
            if stage.endswith(':objection_replies'):
                from bellomberg.agents.specialists.base import _checkpoint_digest
                desk = stage.split(':', 1)[0]
                task = reply_tasks.get(desk, {}).get('task')
                checkpoint_key = desk + ':R2:' + _checkpoint_digest(task) if task else stage
            saved_stage = any(key == checkpoint_key or key.startswith(checkpoint_key + ":")
                for key in getattr(blackboard, "specialist_checkpoints", {}))
            if saved_stage:
                # The sealed contract is validated by the native resume path.
                # Its historical cap (or paid result) cannot be replaced here
                # with the new-stage policy or assumed to require a new call.
                floor_rows.append({"stage": stage, "max_output_tokens": None,
                    "output_reservation_floor_usd": None,
                    "basis": "saved_contract_pending_validation"})
                continue
            role = "capo" if stage == "capo" else "red_team" if stage == "red_team" else "specialist"
            cap = _remaining_stage_token_cap(blackboard, stage)
            price = Decimal(gate.catalog_snapshot["models"][role]["pricing"]["completion"])
            floor_rows.append({"stage": stage, "max_output_tokens": cap,
                "output_reservation_floor_usd": str(Decimal(cap) * price),
                "basis": "new_stage_policy"})
        largest = max((Decimal(row["output_reservation_floor_usd"]) for row in floor_rows
                       if row["output_reservation_floor_usd"] is not None),
                      default=None if floor_rows else Decimal(0))
        cost = gate.store.get_run(gate.run_id)["cost"]
        output.update(reservation_floors=floor_rows,
            largest_output_reservation_floor_usd=str(largest) if largest is not None else None,
            cost={key: cost[key] for key in ("charged_usd", "reserved_usd", "unknown_requests",
                "unknown_reserved_usd", "remaining_known_usd", "remaining_after_holds_usd", "budget_limit_usd")},
            completion_guaranteed=False, input_cost_included=False)
        if cost.get("unacknowledged_unknown_requests", cost["unknown_requests"]):
            output.update(feasibility="blocked", reason="Provider cost unresolved; reservations remain held")
        elif largest is not None and Decimal(cost["remaining_after_holds_usd"]) < largest:
            output.update(feasibility="insufficient_even_before_input",
                reason="The remaining ceiling cannot reserve at least one required native request even before its input cost; future spend is not inferred")
        elif not output["remaining"]:
            output.update(feasibility="analysis_recorded", reason="No new analytical request is required; artifact/delivery state remains separate")
        elif any(row["max_output_tokens"] is None for row in floor_rows) and output["feasibility"] == "not_guaranteed":
            output["reason"] = ("Saved-stage contracts require native validation before an output reservation can be stated; "
                                "known floors concern new stages only. The accepted ceiling is unchanged")
    return output


def _remaining_stage_token_cap(blackboard, stage):
    """Read the native policy without constructing a provider or mutating scope."""
    if stage == "capo":
        finalization = _capo_finalization_policy(blackboard)
        if finalization is not None:
            return finalization["max_tokens"]
        from bellomberg.agents.capo import CAPO_MAX_TOKENS
        return policy_output_cap(blackboard, 'capo', CAPO_MAX_TOKENS)
    if stage == "red_team":
        from bellomberg.agents.red_team import TRADE_IDEA_RED_MAX_TOKENS
        return TRADE_IDEA_RED_MAX_TOKENS
    from types import SimpleNamespace
    from bellomberg.agents.specialists.base import Specialist
    if stage == "model_authoring":
        stage = "fundamentals:R1"
    if stage.endswith(':objection_replies'):
        stage = stage.split(':', 1)[0] + ':R2'
    desk, raw_round = stage.split(":R")
    round_n = int(raw_round)
    reader = object.__new__(Specialist)
    reader.name = desk
    scope = dict(vars(blackboard))
    if round_n != 2:
        scope.pop("_trade_idea_r2_completion_limits", None)
    reader.blackboard = SimpleNamespace(**scope)
    if desk == "fundamentals" and round_n == 1:
        reader.blackboard.model_phase = "building"
    task = {"purpose": "final_completion"} if hasattr(reader.blackboard, "_trade_idea_r2_completion_limits") else None
    return reader._max_tokens_for_round(round_n, task_context=task)


def _configure_native_recovery(blackboard, store, token, *, inherited=None):
    """Install common stage hooks before the first provider or tool call."""
    gate = blackboard.budget_gate
    gate.blackboard = blackboard
    continuation = store.get_run(blackboard.run_id)["run"].get("continuation") or {}
    blackboard.specialist_response_recovery = deepcopy(continuation.get("specialist_response_recovery"))
    if inherited is not None:
        checkpoint = inherited.get("checkpoint")
        if (not isinstance(checkpoint, dict) or inherited.get("checkpoint_sha256") != _plan_digest(checkpoint)
                or checkpoint.get("contract") != _checkpoint_contract(blackboard)):
            raise RuntimeError("Native checkpoint contract/source integrity differs")
        current_context = {key: blackboard.data.get(key) for key in
                           ("_portfolio_context", "_identity")}
        saved = deepcopy(checkpoint["data"])
        for name in (*TRADE_IDEA_DESKS, "red_team", "_red_team", "_capo"):
            if isinstance(saved.get(name), dict):
                saved[name] = {int(key): value for key, value in saved[name].items()}
        historical_failure = saved.pop("_primary_failure", None)
        if historical_failure:
            saved.setdefault("_historical_failures", []).append(historical_failure)
        if saved.get("_desk_gaps") and saved.get("_research_thesis") is None:
            saved.setdefault("_historical_desk_gaps", []).append(saved.pop("_desk_gaps"))
        blackboard.data = {**saved, **current_context}
        blackboard.orari_report = {name: {int(key): value for key, value in rows.items()}
                                   for name, rows in checkpoint["orari_report"].items()}
        for name in ("tool_log", "tool_receipts", "usage_log", "valuation_results", "valuation_generations",
                     "valuation_attempts", "specialist_checkpoints"):
            setattr(blackboard, name, deepcopy(checkpoint.get(name, {} if name.endswith("results") or
                name == "specialist_checkpoints" else [])))
        blackboard.model_roots = list(dict.fromkeys([*checkpoint.get("model_roots", ()),
            *[os.fspath(path) for path in blackboard.model_roots]]))
        archive = blackboard.data.get("_exact_model_archive")
        if is_research_mode(blackboard) and (blackboard.valuation_results or archive
                or blackboard.valuation_generations or blackboard.valuation_attempts):
            raise RuntimeError('Research-only checkpoint contains unexpected model artifacts or activity')
        if archive:
            from bellomberg.core.paths import MODELS_DIR, REPORT_DIR
            from bellomberg.reporting.exact_artifacts import restore_exact_artifacts
            restore_exact_artifacts(archive["receipts"], archive["vault_dir"],
                                    allowed_roots=[MODELS_DIR, REPORT_DIR, *blackboard.model_roots])
        if blackboard.valuation_results and not _verified_candidate_valuations(blackboard):
            raise RuntimeError("Saved exact workbook/hash/generation is no longer verifiable")

    from bellomberg.storage.trade_idea_store import errore_di_lock, log_lock
    # Scritture rimandate perche' il DB era occupato oltre il tetto dello store
    # (06/10). Il checkpoint e' solo un aiuto alla ripresa: il successivo contiene
    # tutto lo stato, quindi rimandarlo non perde nulla; lo si dichiara nel log e
    # nella prima scrittura riuscita. Un guasto non registrato si riscrive appena
    # il DB e' libero, prima del checkpoint.
    deferred = {"failure": None, "checkpoints": []}

    def persist(event="checkpoint", payload=None):
        try:
            pending = deferred["failure"]
            if pending is not None:
                store.record_failure(blackboard.run_id, token, pending)
                deferred["failure"] = None
                log_lock("guasto rimandato ora registrato: " + str(pending.get("message"))[:200])
            events = (*(f"checkpoint rimandato (DB occupato): {row}" for row in deferred["checkpoints"]),
                      event)
            store.update_progress(blackboard.run_id, token, "checkpoint",
                _progress(blackboard, "checkpoint", events=events))
            if deferred["checkpoints"]:
                log_lock(f"checkpoint scritto dopo {len(deferred['checkpoints'])} rimandati")
                deferred["checkpoints"].clear()
        except Exception as exc:
            if not errore_di_lock(exc):
                raise
            deferred["checkpoints"].append(str(event)[:200])
            log_lock("checkpoint rimandato, DB occupato oltre il tetto: " + str(event)[:200]
                     + " (lo stato completo va nel prossimo checkpoint)")

    def failure(error, *, desk=None, round_n=None, request_id=None):
        item = {"message": type(error).__name__ + ": " + str(error)[:2800],
            "exception_type": type(error).__name__, "desk": desk, "round": round_n,
            "request_id": request_id or getattr(error, "request_id", None),
            "phase": getattr(blackboard, "model_phase", "committee")}
        # 06/10: un DB occupato oltre il tetto dei tentativi su UN desk e' locale a quel
        # desk (lacuna dichiarata), non la fine della run. Una risposta pagata non
        # registrata (pagata_non_registrata) resta invece bloccante.
        lock_locale = (desk in TRADE_IDEA_DESKS and errore_di_lock(error)
                       and not getattr(error, "pagata_non_registrata", False))
        if lock_locale:
            item["message"] = ("Database occupato oltre il tetto di attesa: lacuna dichiarata del desk, "
                               "run proseguita. " + item["message"])[:2900]
            log_lock(f"desk={desk} R{round_n}: lacuna dichiarata per DB occupato")
        if (is_research_mode(blackboard) and desk is not None
                and desk == getattr(blackboard, "reply_completion_desk", None)
                and (_desk_local_failure(error) or lock_locale)):
            persist("reply_completion_failure:" + desk)  # its objections are flagged unanswered
            return
        if (is_research_mode(blackboard) and desk in TRADE_IDEA_DESKS
                and (_desk_local_failure(error) or lock_locale)):
            # PM 03/10/2026: one desk that truncates, refuses or writes an unusable report
            # is a declared gap, not the end of an already paid committee. Money, budget,
            # integrity and PM stops below stay run-blocking.
            with blackboard._lock:
                blackboard.data.setdefault("_desk_gaps", {}).setdefault(desk, item)  # vale la prima causa
            persist("desk_gap:" + desk)
            return
        with blackboard._lock:
            try:
                first = store.record_failure(blackboard.run_id, token, item)
            except Exception as exc:
                if not errore_di_lock(exc):
                    raise
                # Il gestore non cade sullo stesso lock che sta gestendo: il guasto resta
                # in memoria (blocca comunque la run) e si scrive al primo DB libero.
                first = {**item, "registrazione": "rimandata: database occupato"}
                if deferred["failure"] is None:
                    deferred["failure"] = item
                log_lock("guasto non registrabile ora (DB occupato), rimandato: " + item["message"][:200])
            if not blackboard.data.get("_primary_failure"):
                blackboard.data["_primary_failure"] = first
        persist("execution_failure")

    def blocked():
        first = blackboard.data.get("_primary_failure")
        if first:
            raise RuntimeError(first["message"])
        detail = store.get_run(blackboard.run_id)
        if detail["run"]["stop_requested"]:
            raise RuntimeError("Trade Idea stopped by PM")
        if detail["cost"].get("unacknowledged_unknown_requests", detail["cost"]["unknown_requests"]):
            raise RuntimeError("Provider cost unresolved; no new request permitted")

    def should_run(name, round_n):
        if is_research_mode(blackboard) and name in (blackboard.data.get("_desk_gaps") or {}):
            return False
        row = blackboard.data.get("_completed_stages", {}).get(f"{name}:{round_n}")
        if not row:
            return True
        report = blackboard.read(name, round_n)
        if row.get("report_sha256") != _plan_digest(report):
            raise RuntimeError("Saved report integrity differs: " + name)
        if round_n == 2:
            if is_research_mode(blackboard):
                return row.get('research_ref') != research_reference(blackboard)
            current = _verified_candidate_valuations(blackboard)
            if len(current) != 1 or row.get("model_ref") != {key: current[0][key] for key in
                    ("snapshot_id", "generation_id", "workbook_sha256")}:
                return True
        return False

    def complete(name, round_n, report, result_status):
        if result_status != "complete":
            failure(RuntimeError(f"{name} R{round_n} response incomplete: {result_status}"),
                    desk=name, round_n=round_n)
            return
        with blackboard._lock:
            attestation = {"report_sha256": _plan_digest(report), "status": "complete"}
            if round_n == 2:
                if is_research_mode(blackboard):
                    attestation['research_ref'] = deepcopy(blackboard.data.get('_desk_research_reviews', {}).get(name, {}).get('research_ref'))
                else:
                    attestation["model_ref"] = deepcopy(blackboard.data.get("_desk_model_reviews", {}).get(name, {}).get("model_ref"))
            blackboard.data.setdefault("_completed_stages", {})[f"{name}:{round_n}"] = attestation
        persist(f"{name}:R{round_n} complete")

    blackboard.persist_run_checkpoint = persist
    blackboard.record_run_failure = failure
    blackboard.raise_if_run_blocked = blocked
    blackboard.should_run_specialist = should_run
    blackboard.record_specialist_completion = complete
    blackboard.model_authoring_progress = lambda **kwargs: _model_authoring_progress(blackboard, **kwargs)
    def recovery_evidence(_descriptor):
        from bellomberg.agents.model_authoring_evidence import acquire_board_statements
        result = acquire_board_statements(blackboard)
        # Pure installed contracts are supplied with the recovery evidence so
        # the remaining tool turn can save/compile without rediscovering them.
        result['compiler_contracts'] = {}
        for section in ('common', 'evidence', 'shares'):
            offset, fragments = 0, []
            while True:
                page = _candidate_contract_page(blackboard, {
                    'scope': 'model', 'contract_section': section, 'offset': offset})
                if not page.get('ok'):
                    raise ValueError('Recovery compiler contract unavailable: ' + str(page))
                fragments.append(page['text'])
                if page['complete']:
                    break
                if page['next_offset'] <= offset:
                    raise ValueError('Recovery compiler contract pagination did not advance')
                offset = page['next_offset']
            result['compiler_contracts'][section] = ''.join(fragments)
        result['instruction'] = ('These are the installed compiler contracts and observed facts, not approved '
            'financial choices. Preserve saved decisions, explicitly finish supported missing inputs and '
            'invoke the normal compiler in the available native tool turn. The final turn has tools disabled.')
        return result
    blackboard.model_authoring_recovery_evidence = recovery_evidence


def _model_authoring_progress(blackboard, *, remaining_turns=None, messages=None):
    """Inform the author using the same contract and checks as the compiler."""
    from bellomberg.agents.model_authoring_progress import build_model_authoring_progress
    current = {row["id"]: row for row in _current_model_consultations(blackboard)}
    checks = {}
    for row in blackboard.data.get("_model_consultations") or ():
        if not isinstance(row, dict) or not row.get("id"):
            continue
        received = row["id"] in current and _consultation_received(blackboard, current[row["id"]])
        checks[row["id"]] = {"current": row["id"] in current, "received": received,
            "decided": bool(received and _valid_fundamentals_decision(row, row.get("fundamentals_decision")))}
    contract_error = None
    try:
        contract = _candidate_input_contract(blackboard)
    except (KeyError, TypeError, ValueError) as exc:
        # Before primary documents are admitted there may be no financial
        # contract yet. This informational view declares that prerequisite;
        # source admission and compilation keep their own mandatory checks.
        contract, contract_error = {}, type(exc).__name__ + ": " + str(exc)
    ledger = build_model_authoring_progress(blackboard, contract=contract,
        remaining_turns=remaining_turns, messages=messages,
        required_consultation_desks=set(TRADE_IDEA_DESKS) - {"fundamentals"}, consultation_checks=checks)
    if contract_error:
        ledger["contract_error"] = contract_error
    return ledger


def _declared_excerpt(text, english, limit=42000, chunk=14000):
    """Partial-package excerpt; a cut is stated with both lengths, never silent."""
    parts = [text[i:i + chunk] for i in range(0, min(len(text), limit), chunk)]
    if len(text) > limit:
        parts.append((f"[Excerpt: first {limit} of {len(text)} characters; the complete report "
                      "remains in the run's saved desk reports.]") if english else
                     (f"[Estratto: primi {limit} di {len(text)} caratteri; il report integrale "
                      "resta nei report desk salvati della run.]"))
    return parts


def _incomplete_capo_result(run, blackboard, reason):
    """Preserve actual desk material when the Capo has no valid economic verdict."""
    from bellomberg.core.trade_idea_contract import validate_result

    english = run.get("language") == "en"
    cause = str(reason)[:3000]
    reports = _reports(blackboard)
    dossier = [{"key": "executive", "title": "Technical conclusion" if english else "Esito tecnico",
        "paragraphs": [("The Capo produced no valid structured conclusion. No investment "
                        "judgment or operational proposal can be attributed to this run. " if english else
                        "Il Capo non ha prodotto una conclusione strutturata valida. A questa "
                        "run non si possono attribuire un giudizio economico o una proposta operativa. ")
                       + cause], "evidence_ids": [], "tables": [], "charts": []}]
    for report in reports:
        if report.get("status") != "ready" or not str(report.get("text") or "").strip():
            continue
        text = str(report["text"])
        dossier.append({"key": "desk_" + report["specialist"],
            "title": ("Unreviewed desk material: " if english else
                      "Materiale desk non sintetizzato: ") + report["specialist"],
            "paragraphs": _declared_excerpt(text, english),
            "evidence_ids": [], "tables": [], "charts": []})
    red = blackboard.get_latest("red_team")
    if red and str(red.get("report") or "").strip():
        text = str(red["report"])
        dossier.append({"key": "red_team", "title": "Unreviewed Red Team" if english else
                        "Red Team non sintetizzato", "paragraphs": _declared_excerpt(text, english),
                        "evidence_ids": [], "tables": [], "charts": []})
    gap = (("Capo output invalid or unavailable: " if english else
            "Output Capo non valido o non disponibile: ") + cause)
    payload = {"ticker": run["ticker"], "judgment": "incomplete",
        "summary": gap, "pm_view_response": ("The PM view was not adjudicated. " if english else
                                         "La view del PM non e' stata giudicata. ") + gap,
        "pros": [], "cons": [], "risks": [], "catalysts": [], "invalidation": [],
        # Voce 9: anche il ripiego del Capo caduto dichiara la run senza filing (la via di
        # salvataggio non passa dal ciclo che la aggiunge dopo il Capo; li' resta deduplicata).
        "data_gaps": [*official_documents_gaps(blackboard), gap], "review_conditions": [
            "A new explicitly authorized analysis is required to obtain a valid judgment." if english
            else "Serve una nuova analisi esplicitamente autorizzata per ottenere un giudizio valido."],
        "scenarios": [], "objections": [{"objection": gap,
            "response": ("No economic response can be asserted from the incomplete Capo output."
                         if english else "Nessuna risposta economica e' attestabile dall'output Capo incompleto."),
            "resolved": False, "evidence_ids": []}],
        "history_review": [], "valuation_refs": [], "evidence": [],
        "dossier": dossier, "proposal": None}
    if execution_policy(run) == EXECUTION_POLICY_V4:
        # /4: the memo model cannot hold a package without a Capo verdict (no
        # pillars, scenarios, conviction). It keeps the historical shape and is
        # declared by origin; store and resume accept it only as incomplete.
        from bellomberg.storage.trade_idea_store import SERVER_INCOMPLETE_ORIGIN, validate_run_result
        return validate_run_result({**payload, "result_origin": SERVER_INCOMPLETE_ORIGIN},
            run_id=run["id"], ticker=run["ticker"], pm_view=run["view_text"],
            policy=EXECUTION_POLICY_V4)
    return validate_result(payload, run_id=run["id"], ticker=run["ticker"],
                           pm_view=run["view_text"])


@contextmanager
def _isolated_source_scope(tool_dispatcher, facts_loader, mandate_loader):
    """Alternative-DB replays cannot reach global portfolio tools or facts."""
    from unittest.mock import patch
    from bellomberg.agents import chat_tools
    from bellomberg.core import current_facts, mandato_pm
    with (patch.object(chat_tools, "dispatch", tool_dispatcher),
          patch.object(current_facts, "current_facts_block", facts_loader),
          patch.object(mandato_pm, "carica", mandate_loader)):
        yield


def _evaluate_candidate(blackboard):
    """Common get_valuation once for the accepted ticker if no desk attempted it."""
    ticker = blackboard.target_ticker
    if ticker in blackboard.valuation_results or any(
            item.get("ticker") == ticker for item in blackboard.valuation_attempts):
        return blackboard.valuation_results.get(ticker)
    from bellomberg.agents import chat_tools
    envelope = None
    try:
        envelope = chat_tools.dispatch("get_valuation", {"ticker": ticker},
            caller="trade-idea-orchestrator", valuation_preparer=blackboard.valuation_preparer)
        payload = envelope.get("data", envelope) if isinstance(envelope, dict) else envelope
        if not isinstance(payload, dict):
            raise ValueError("get_valuation non ha restituito un oggetto")
    except Exception as exc:
        payload = {"ok": False, "error": type(exc).__name__ + ": " + str(exc),
                   "exclude_from_action_table": True}
    payload = {**payload, "request_origin": "trade-idea-orchestrator"}
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    from bellomberg.agents.specialists.base import _trade_idea_tool_receipt_success
    blackboard.tool_receipts.append({"tool": "get_valuation", "input": {"ticker": ticker},
        "source": envelope.get("_source") if isinstance(envelope, dict) else "get_valuation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": _trade_idea_tool_receipt_success(
            payload, "get_valuation", truncated=len(encoded) > 200000),
        "output": encoded[:200000], "truncated": len(encoded) > 200000})
    from bellomberg.valuation.trade_idea_model import candidate_model_usability
    if blackboard.valuation_preparer is None and not candidate_model_usability(payload)["usable"]:
        state = blackboard.data.get("_valuation_preparation") or {}
        payload["preparation"] = {"status": "disabled",
                                  "reason": state.get("reason") or "not_authorized"}
    blackboard.record_valuation(ticker, payload, "trade-idea-orchestrator")
    return payload


def _candidate_plan_schema():
    """Describe the native transport shape; the compiler owns economic validation."""
    text = {"type": "string", "minLength": 1}
    envelope = {"type": "object", "properties": {
        "value": {}, "kind": {"type": "string", "enum": ["historical", "company_guidance", "analyst_estimate"]},
        "evidence_ids": {"type": "array", "items": text, "minItems": 1}, "rationale": text,
        "valid_until": text, "valid_until_basis": {"type": ["object", "string"]},
        **{key: {} for key in ("evidence_quote", "quoted_value", "quoted_unit", "period_quote", "facts",
            "evidence_pointer", "calculation", "record_pointer", "dilution_estimate")}},
        "required": ["value", "kind", "evidence_ids", "rationale", "valid_until", "valid_until_basis"],
        "additionalProperties": False}
    drivers = {"type": "object", "additionalProperties": envelope,
        "description": "Map exact native driver names from get_candidate_model_inputs to complete driver envelopes."}
    return {"type": "object", "properties": {
        "model": drivers,
        "scenarios": {"type": "object", "properties": {name: drivers for name in ("bear", "base", "bull")},
                      "additionalProperties": False},
        "scenario_rationale": {"type": "object", "properties": {name: text for name in ("bear", "base", "bull")},
                               "additionalProperties": False},
        "analysis_rationale": text}, "additionalProperties": False}


def trade_idea_review_tools(desk):
    tools = [{"name": "respond_trade_idea_objection",
        "description": "Answer an exact objection addressed to your desk; preserve concessions and open questions.",
        "input_schema": {"type": "object", "properties": {
            "objection_id": {"type": "string"}, "response": {"type": "string"},
            "state": {"type": "string", "enum": ["answered", "conceded", "open"]},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "model_revision_id": {"type": ["string", "null"]}},
            "required": ["objection_id", "response", "state", "evidence_refs"]}}]
    tools.extend([
            {"name": "read_candidate_source",
             "description": "Read exact text from one already qualified primary document, without acquisition or AI. Use an exact qualified document ID. Optional literal query locates its next occurrence at or after offset; continue from next_offset without query for adjacent text. Offsets refer to the archived text, not pages or financial periods.",
             "input_schema": {"type": "object", "properties": {
                 "document_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                 "query": {"type": "string", "minLength": 1, "maxLength": 200}},
                 "required": ["document_id"], "additionalProperties": False}}])
    if desk == "fundamentals":
        tools.extend([
            {"name": "read_candidate_model_consultation",
             "description": "Read a recorded live answer in consecutive pages without another model call. Complete all pages before deciding on an oversized answer.",
             "input_schema": {"type": "object", "properties": {
                 "consultation_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}},
                 "required": ["consultation_id", "offset"]}},
            {"name": "get_candidate_model_inputs",
             "description": "Read the native driver contract and a declared input basis, not an existing workbook for this run. Read contract_section method/evidence before authoring: these contain the exact nested value and proof formats. For FCFF also read accounting/shares/opening_nwc. Continue each contract page using next_offset until complete. Omit drivers for a compact manifest, then request named drivers for complete evidence. An oversized single driver returns consecutive pages of its exact JSON input; keep requesting that driver with next_offset until complete and reconstruct the full input. Historical facts and archived estimates retain their provenance. draft_validation separates prepared input proofs from revenue/forecast arithmetic; inspect both and explicitly author any calculated option.",
             "input_schema": {"type": "object", "properties": {
                 "scope": {"type": "string", "enum": ["model", "bear", "base", "bull"]},
                 "drivers": {"type": "array", "items": {"type": "string"}},
                 "contract_section": {"type": "string", "enum": ["method", "common", "evidence", "accounting", "shares", "opening_nwc", "draft_validation"]},
                 "offset": {"type": "integer", "minimum": 0}},
                 "required": ["scope"]}},
            {"name": "submit_candidate_model_plan",
             "description": "Save explicit native driver groups and decisions on received consultations. plan.model maps driver names to envelopes; plan.scenarios maps bear/base/bull to driver maps; plan.scenario_rationale maps those names to motivations. Partial submissions accumulate. The final authored plan is compiled automatically after R1, without another AI call; get_valuation(ticker) can compile it earlier when final. No missing values are filled.",
             "input_schema": {"type": "object", "properties": {
                 "plan": _candidate_plan_schema(),
                 "reuse": {"type": "array", "items": {"type": "object", "properties": {
                     "scope": {"type": "string"}, "driver": {"type": "string"},
                     "basis_plan_sha256": {"type": "string"}, "driver_sha256": {"type": "string"}},
                     "required": ["scope", "driver", "basis_plan_sha256", "driver_sha256"]}},
                 "consultation_decisions": {"type": "array", "items": {"type": "object", "properties": {
                     "consultation_id": {"type": "string"},
                     "decision": {"type": "string", "enum": ["incorporated", "disagreed", "not_relevant"]},
                     "rationale": {"type": "string"}},
                     "required": ["consultation_id", "decision", "rationale"]}},
                 "rationale": {"type": "string", "description": "Optional audit note; driver and scenario motivations remain required."}},
                 "anyOf": [{"required": [key]} for key in ("plan", "reuse", "consultation_decisions")],
                 "additionalProperties": False}}])
        tools.append({"name": "review_candidate_model",
            "description": "Review the current exact model. Queue a motivated revision or explicitly retain it; this tool never regenerates a workbook.",
            "input_schema": {"type": "object", "properties": {
                "generation_id": {"type": "string"},
                "action": {"type": "string", "enum": ["retain", "revise"]},
                "rationale": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "changes": {"type": "object", "properties": {
                    "assumptions": {"type": "object"}, "analysis_context": {"type": "object"},
                    "method_records": {"type": "array", "items": {"type": "object"}}}},
                "needs_paid_preparation": {"type": "boolean"}},
                "required": ["generation_id", "action", "rationale", "evidence_refs", "changes", "needs_paid_preparation"]}})
    return tools


def _plan_digest(value):
    from hashlib import sha256
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _qualified_model_input_basis(qualification):
    """Expose only an authentic sealed seed, with its absence and limits declared."""
    from bellomberg.valuation.trade_idea_model import source_fingerprint
    if qualification.get("fingerprint") != source_fingerprint(qualification):
        raise ValueError("Qualified input basis fingerprint differs")
    report = qualification.get("source_report") or {}
    for field in ("source_plan", "historical_preparation_plan"):
        plan = report.get(field)
        if plan is not None and not isinstance(plan, dict):
            raise ValueError("Qualified input basis is not an object: " + field)
        if plan:
            _plan_digest(plan)
            return {"plan": deepcopy(plan), "basis_field": "source_report." + field,
                "source_fingerprint": qualification["fingerprint"],
                "provenance": "Exact qualified " + field + "; archived facts and analyst conventions retain their own kind/proofs. Not a current workbook, forecast completion or PM approval. Fundamentals must explicitly author or reference each driver and supply its scenario rationale."}
    return {"plan": {}, "basis_field": "none", "source_fingerprint": qualification["fingerprint"],
        "provenance": "No archived input plan is present in the qualified snapshot. Author missing drivers from admitted evidence; no values have been supplied."}


def _bind_trade_idea_source_research(blackboard, *, archive_root, artifact_dir, session_factory=None):
    """Bind the original research grant to its durable, shared source journal."""
    from pathlib import Path
    from threading import RLock
    from bellomberg.agents.company_source_research import ResearchSession
    from bellomberg.valuation.trade_idea_model import (research_source_qualification,
        qualify_authored_research, validate_research_admission)
    from bellomberg.agents.model_authoring_evidence import statement_receipt
    admission = blackboard.source_admission
    validate_research_admission(admission)
    run = blackboard.budget_gate.store.get_run(blackboard.run_id)['run']
    original_id = (run.get('continuation') or {}).get('root_run_id') or blackboard.run_id
    previous = deepcopy(blackboard.data.get('_source_research') or {})
    root = Path(archive_root).resolve()
    directory = Path(previous.get('run_dir') or artifact_dir).resolve()
    if previous and (previous.get('run_id') != original_id
            or previous.get('parent_grant_fingerprint') != admission['fingerprint']
            or Path(previous.get('archive_root', '')).resolve() != root
            or not any(directory.is_relative_to(Path(item).resolve()) for item in blackboard.model_roots)):
        raise ValueError('Saved research session differs from its original run/grant/roots')
    session = (session_factory or ResearchSession)(run_id=original_id, ticker=admission['ticker'],
        identity=admission['identity'], as_of=admission['as_of'], admission_fingerprint=admission['fingerprint'],
        archive_root=root, run_dir=directory,
        issuer_website=(admission['bundle']['case'].get('info') or {}).get('website'))
    research_lock = RLock()
    blackboard._source_research_lock = research_lock

    def session_for(ticker):
        if ticker != admission['ticker']:
            raise ValueError('Company research is authorized only for the accepted candidate')
        return session

    def apply_snapshot(snapshot, *, persist):
        current = research_source_qualification(admission, snapshot)
        fingerprints = list(previous.get('evidence_fingerprints') or [])
        fingerprints.extend((blackboard.data.get('_source_research') or {}).get('evidence_fingerprints') or [])
        fingerprints.append(_consultation_source_fingerprint(blackboard))
        blackboard.source_qualification = current
        fingerprints.append(_consultation_source_fingerprint(blackboard))
        state = {'run_id': original_id, 'run_dir': str(directory), 'archive_root': str(root),
            'parent_grant_fingerprint': admission['fingerprint'],
            'revision_id': snapshot.get('revision_id'), 'revision_sha256': snapshot.get('revision_sha256'),
            'evidence_fingerprints': list(dict.fromkeys(value for value in fingerprints if value))}
        blackboard.data['_source_research'] = state
        basis = blackboard.data.get('_model_input_basis') or {}
        if not is_research_mode(blackboard) and not basis.get('plan'):
            blackboard.data['_model_input_basis'] = _qualified_model_input_basis(current)
        if persist:
            blackboard.persist_run_checkpoint('company_sources_admitted')
        return state

    def changed(active_session, snapshot):
        with research_lock:
            if (active_session is not session or blackboard.current_round not in (0, 1)
                    or blackboard.valuation_results.get(admission['ticker'])
                    or blackboard.data.get('_research_thesis') is not None):
                raise ValueError('Research sources cannot change after model creation/review; acquired bytes remain unadopted')
            # A callback may arrive after another desk has already admitted a
            # newer source. Read the journal under the board's revision lock;
            # never restore the stale snapshot carried by that callback.
            state = blackboard.data.get('_source_research') or {}
            latest = session.snapshot(expected_revision_id=state.get('revision_id'))
            apply_snapshot(latest, persist=True)

    def qualify(plan):
        snapshot = session.snapshot()
        state = blackboard.data['_source_research']
        if (snapshot.get('revision_id') != state.get('revision_id')
                or snapshot.get('revision_sha256') != state.get('revision_sha256')):
            raise ValueError('Unacknowledged source revision; reread the current dossier before compilation')
        quotation = blackboard.data.get('_author_quotation') or {}
        receipt = next((row for row in quotation.get('receipts', [])
            if row.get('sha256') == quotation.get('active_sha256')), None)
        if quotation.get('active_sha256') and receipt is None:
            raise ValueError('Saved author quotation receipt is missing')
        qualified = qualify_authored_research(admission, snapshot, plan, archive_root=root,
            **({'quotation_receipt': receipt} if receipt is not None else {}),
            **({'statement_receipt': statement_receipt(blackboard)}
               if statement_receipt(blackboard) is not None else {}))
        blackboard.source_qualification = qualified
        state.update(qualified_plan_sha256=_plan_digest(plan), qualified_fingerprint=qualified['fingerprint'])
        blackboard.persist_run_checkpoint('research_historical_proofs_qualified')
        return qualified

    snapshot = session.snapshot(expected_revision_id=previous.get('revision_id'))
    if previous.get('qualified_fingerprint'):
        plan = blackboard.data.get('_model_input_draft')
        if (not isinstance(plan, dict) or _plan_digest(plan) != previous.get('qualified_plan_sha256')
                or snapshot.get('revision_id') != previous.get('revision_id')):
            raise ValueError('Saved qualified author plan/source revision differs')
        quotation = blackboard.data.get('_author_quotation') or {}
        receipt = next((row for row in quotation.get('receipts', [])
            if row.get('sha256') == quotation.get('active_sha256')), None)
        if quotation.get('active_sha256') and receipt is None:
            raise ValueError('Saved author quotation receipt is missing')
        qualified = qualify_authored_research(admission, snapshot, plan, archive_root=root,
            **({'quotation_receipt': receipt} if receipt is not None else {}),
            **({'statement_receipt': statement_receipt(blackboard)}
               if statement_receipt(blackboard) is not None else {}))
        if qualified['fingerprint'] != previous['qualified_fingerprint']:
            raise ValueError('Saved economic source qualification cannot be reverified')
        blackboard.source_qualification = qualified
    else:
        apply_snapshot(snapshot, persist=False)
    blackboard.company_source_session = session_for
    blackboard.on_company_sources_changed = changed
    blackboard.qualify_research_model = qualify


def _decode_candidate_plan(value):
    """Decode one transport layer only; do not coerce or repair financial values."""
    if not isinstance(value, str):
        return deepcopy(value), None
    from hashlib import sha256
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = item
        return result
    def reject_constant(value):
        raise ValueError("Non-finite JSON constant: " + value)
    plan = json.loads(value, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(plan, dict):
        raise ValueError("Serialized plan must decode to one JSON object")
    return plan, {"encoding": "strict_json_object_string", "input_sha256": sha256(value.encode("utf-8")).hexdigest(),
                  "decoded_plan_sha256": _plan_digest(plan), "financial_values_changed": False}


def _read_candidate_source(blackboard, desk, input_):
    """Page the sealed in-memory primary text; no path, URL or provider dispatch."""
    from bellomberg.valuation.company_dossier import read_qualified_document
    from bellomberg.agents.specialists.base import _tetto_tool_result
    from bellomberg.agents.model_authoring_evidence import board_author_evidence_view
    try:
        qualification = board_author_evidence_view(blackboard)
    except (ValueError, KeyError, OSError) as exc:
        return {"ok": False, "error": "Derived quotation evidence unavailable: " + str(exc)}
    return read_qualified_document(qualification, input_, max_chars=_tetto_tool_result())


def _persist_model_build(blackboard):
    writer = getattr(blackboard, "model_checkpoint_writer", None)
    if callable(writer):
        writer(_progress(blackboard, "model_building"))


def _consultation_answer_identity(row):
    return {"consultation_id": row["id"], "desk": row["desk"],
            "answer_sha256": _plan_digest(row["response"]),
            "draft_sha256": row["draft_sha256"], "source_fingerprint": row["source_fingerprint"]}


def _consultation_received(blackboard, row):
    receipt = blackboard.data.get("_fundamentals_received_consultations", {}).get(row.get("id"))
    return (row.get("status") == "complete" and row.get("author_view_complete") is True
            and isinstance(row.get("response"), str) and bool(row["response"].strip())
            and isinstance(receipt, dict) and isinstance(receipt.get("response_id"), str)
            and bool(receipt["response_id"].strip())
            and all(receipt.get(key) == value for key, value in _consultation_answer_identity(row).items()))


def _recovery_intent(row, context):
    return _plan_digest({"question": row["question"], "draft_assumptions": row["draft_assumptions"],
                         "evidence_refs": row["evidence_refs"], "after_answers": context})


def _economic_question(question):
    # A new request ID/nonce does not make a failed question a new economic question.
    question = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "", question, flags=re.I)
    question = re.sub(r"\b(?:nonce|request[_ -]?id|timestamp)\s*[:=]\s*\S+", "", question, flags=re.I)
    return " ".join(re.findall(r"\w+", question.casefold()))


def _recoverable_model_consultation(row):
    if row.get("status") == "failed":
        return True
    terminal = row.get("terminal_response")
    if (row.get("status") != "truncated" or not isinstance(terminal, dict)
            or terminal.get("stop_reason") != "max_tokens"
            or not all(isinstance(terminal.get(key), str) and terminal[key].strip()
                       for key in ("response_id", "model"))
            or not isinstance(row.get("response"), str) or not row["response"].strip()
            or row["response"].startswith("[ERROR")
            or terminal.get("report_sha256") != _plan_digest(row["response"])):
        return False
    usage, total, entries = terminal.get("usage"), terminal.get("run_usage"), row.get("usage")
    fields = {"in": "input_tokens", "out": "output_tokens",
              "cache_read": "cache_read_input_tokens", "cache_write": "cache_creation_input_tokens"}
    if (not isinstance(usage, dict) or not isinstance(total, dict)
            or not isinstance(entries, list) or not entries
            or total.get("tokens_status") != "completo" or total.get("tokens_missing") != []
            or any(type(values.get("cost_usd")) not in (int, float)
                   or not Decimal(str(values["cost_usd"])).is_finite() or values["cost_usd"] < 0
                   for values in (usage, total))
            or total["cost_usd"] < usage["cost_usd"]
            or any(type(usage.get(long)) is not int or usage[long] < 0
                   or type(total.get(short)) is not int or total[short] < usage[long]
                   for short, long in fields.items())
            or usage["output_tokens"] <= 0
            or (usage.get("reasoning_tokens") is not None and
                (type(usage["reasoning_tokens"]) is not int or not 0 <= usage["reasoning_tokens"] <= usage["output_tokens"]))):
        return False
    if any(not isinstance(entry, dict) or entry.get("agent") != row.get("desk")
           or entry.get("round") != 1 or entry.get("model") != terminal["model"]
           or entry.get("status") in ("api_error", "usage_unknown")
           or entry.get("tokens_status") != "completo" or entry.get("tokens_missing") != []
           or any(type(entry.get(key)) is not int or entry[key] < 0 for key in fields) for entry in entries):
        return False
    return all(sum(entry[key] for entry in entries) == total[key] for key in fields)


def _native_consultation_terminal_response(response, report, run_usage, usage_entry, desk):
    if not any(str(getattr(block, "text", "") or "").strip() for block in getattr(response, "content", [])):
        return None
    usage = getattr(response, "usage", None)
    terminal = {"response_id": getattr(response, "id", None), "model": getattr(response, "model", None),
                "stop_reason": getattr(response, "stop_reason", None), "report_sha256": _plan_digest(report),
                "usage": {key: getattr(usage, key, None) for key in ("input_tokens", "output_tokens",
                    "cache_read_input_tokens", "cache_creation_input_tokens", "reasoning_tokens", "cost_usd")},
                "run_usage": deepcopy(run_usage)}
    row = {"status": "truncated", "desk": desk, "response": report,
           "terminal_response": terminal, "usage": [usage_entry]}
    return terminal if _recoverable_model_consultation(row) else None


def _current_model_consultations(blackboard, *, rows=None):
    """Resolve explicit failed/known-truncated links, retaining every raw audit row."""
    rows = blackboard.data.get("_model_consultations", []) if rows is None else rows
    if not isinstance(rows, list):
        raise ValueError("Consultation audit must be a list")
    seen, heads = {}, {}
    fingerprint = _consultation_source_fingerprint(blackboard)
    prior_fingerprints = set((blackboard.data.get('_source_research') or {}).get('evidence_fingerprints') or [])
    evidence = _base_review_source_ids(blackboard)
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
                or row["id"] in seen or row.get("requester") != "fundamentals"
                or row.get("desk") not in set(TRADE_IDEA_DESKS) - {"fundamentals"}
                or not fingerprint or row.get("source_fingerprint") not in {fingerprint, *prior_fingerprints}
                or not isinstance(row.get("question"), str) or not row["question"].strip()
                or not isinstance(row.get("draft_assumptions"), dict) or not row["draft_assumptions"]
                or row.get("draft_sha256") != _plan_digest(row["draft_assumptions"])
                or not isinstance(row.get("evidence_refs"), list)
                or any(not isinstance(ref, str) for ref in row["evidence_refs"])
                or set(row["evidence_refs"]) - evidence):
            raise ValueError("Consultation identity, source, draft or evidence changed")
        if row['source_fingerprint'] != fingerprint:
            # An added primary source makes prior advice historical. Preserve
            # its answer and the author's decision; only exact-current advice
            # may satisfy the compilation gate for the new documentary basis.
            seen[row['id']] = row
            continue
        link = row.get("supersedes_failed")
        previous = heads.get(row["desk"])
        if link is None:
            if previous is not None or "recovery_context" in row:
                raise ValueError("Duplicate desk consultation requires an explicit failed predecessor")
        else:
            if (not isinstance(link, dict) or set(link) != {
                    "consultation_id", "consultation_row_sha256", "rationale", "after_consultation_ids"}
                    or previous is None or link.get("consultation_id") != previous["id"]
                    or not _recoverable_model_consultation(previous)
                    or link.get("consultation_row_sha256") != _plan_digest(previous)
                    or not isinstance(link.get("rationale"), str) or not link["rationale"].strip()
                    or _economic_question(row["question"]) == _economic_question(previous["question"])):
                raise ValueError("Invalid failed/known-truncated predecessor or unchanged economic question")
            after = link.get("after_consultation_ids")
            context = row.get("recovery_context")
            if (not isinstance(after, list) or len(after) < 3 or any(not isinstance(i, str) for i in after)
                    or len(set(after)) != len(after) or not isinstance(context, list)
                    or len(context) != len(after)):
                raise ValueError("Recovery requires at least three received current peer answers")
            for identity, consultation_id in zip(context, after):
                peer = seen.get(consultation_id)
                if (peer is None or heads.get(peer["desk"]) is not peer or peer["desk"] == row["desk"]
                        or peer.get("status") != "complete" or peer.get("author_view_complete") is not True
                        or not isinstance(peer.get("response"), str) or not peer["response"].strip()
                        or not isinstance(identity, dict) or not isinstance(identity.get("response_id"), str)
                        or not identity["response_id"].strip()
                        or set(identity) != set(_consultation_answer_identity(peer)) | {"response_id"}
                        or any(identity.get(key) != value for key, value in _consultation_answer_identity(peer).items())):
                    raise ValueError("Recovery peer answer provenance differs from the preserved audit")
            if row.get("economic_intent_sha256") != _recovery_intent(row, context):
                raise ValueError("Recovery economic question or context changed")
        seen[row["id"]] = row
        heads[row["desk"]] = row
    return list(heads.values())


def _consultation_source_fingerprint(blackboard):
    qualification = getattr(blackboard, 'source_qualification', None) or {}
    revision = qualification.get('research_revision') or {}
    if revision.get('revision_id') is not None:
        return _plan_digest(revision)
    return (getattr(blackboard, 'source_admission', None) or qualification).get('fingerprint')


def _model_consultation_read_snapshot(blackboard):
    """Called before dispatch; tool pages generated in that response are excluded."""
    try:
        rows = _current_model_consultations(blackboard)
    except (ValueError, KeyError, TypeError):
        return {}
    return {row["id"]: _consultation_answer_identity(row) for row in rows
            if row.get("status") == "complete" and row.get("author_view_complete") is True
            and isinstance(row.get("response"), str) and row["response"].strip()}


def _record_fundamentals_received_consultations(blackboard, snapshot, response):
    """Only the native loop can acknowledge its pre-turn view, after known usage."""
    usage = getattr(response, "usage", None)
    response_id = getattr(response, "id", None)
    cost = getattr(usage, "cost_usd", None)
    if (not snapshot or not isinstance(response_id, str) or not response_id.strip()
            or type(cost) not in (int, float) or not Decimal(str(cost)).is_finite() or cost < 0
            or any(type(getattr(usage, key, None)) is not int or getattr(usage, key) < 0
                   for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))):
        return
    receipts = blackboard.data.setdefault("_fundamentals_received_consultations", {})
    for consultation_id, identity in snapshot.items():
        receipts[consultation_id] = {**deepcopy(identity), "response_id": response_id}
    _persist_model_build(blackboard)


def _valid_fundamentals_decision(row, decision):
    return (isinstance(decision, dict) and decision.get("consultation_id") == row["id"]
            and decision.get("decision") in ("incorporated", "disagreed", "not_relevant")
            and isinstance(decision.get("rationale"), str) and bool(decision["rationale"].strip()))


def _candidate_input_contract(blackboard):
    from bellomberg.valuation.preparation_methods import method_schema, method_requirements, horizon
    qualification = blackboard.source_qualification
    seed = deepcopy((blackboard.data.get("_model_input_basis") or
                     _qualified_model_input_basis(qualification))["plan"])
    draft = blackboard.data.get("_model_input_draft")
    if isinstance(draft, dict):
        seed = deepcopy(seed or {})
        seed.setdefault("model", {}).update(deepcopy(draft.get("model") or {}))
        for scope, values in (draft.get("scenarios") or {}).items():
            seed.setdefault("scenarios", {}).setdefault(scope, {}).update(deepcopy(values))
    method = qualification["method_id"]
    schema, _ = method_schema(method, seed)
    if method == "bank_residual_income" and isinstance(seed, dict):
        from bellomberg.valuation.input_preparation import _bank_schema
        perimeter = ((seed.get("model") or {}).get("perimeter") or {}).get("value")
        if isinstance(perimeter, dict) and (seed.get("model") or {}).get("legal_structure"):
            schema, _, issues = _bank_schema(seed, perimeter)
            if issues:
                raise ValueError("Native bank structure invalid: " + "; ".join(row["reason"] for row in issues))
    extra = {}
    if method == "operating_fcff":
        from bellomberg.valuation.operating_adapter import REVENUE_BASES, REVENUE_ALTERNATIVE_BASES
        profile = qualification["bundle"]["decision"]["profile_id"]
        extra = {"revenue_build_basis": REVENUE_BASES.get(profile),
                 "revenue_build_alternatives": list(REVENUE_ALTERNATIVE_BASES.get(profile, ()))}
    return {"method_id": method, "schema": schema, **extra,
            "requirements": method_requirements(method, seed), "horizon": horizon(method),
            "as_of": qualification["as_of"],
            "driver_envelope": "value, kind, evidence_ids, rationale, valid_until, valid_until_basis; historical/guidance require exact native fact proofs. No missing-value defaults.",
            "scenarios": [] if method == "exposure_analysis" else ["bear", "base", "bull"]}


def _basis_driver_catalog(schema, source, values, names, *, full_inputs):
    result = {}
    for key in names:
        item = source.get(key) or {}
        row = {"contract": schema[key], "basis_kind": item.get("kind"),
               "basis_driver_sha256": _plan_digest(item) if key in source else None,
               "draft_present": key in values}
        if key not in source:
            row["value_view"] = "No archived basis input for this driver; author it from admitted evidence"
            result[key] = row
            continue
        encoded = json.dumps(item.get("value"), ensure_ascii=False, allow_nan=False)
        if not full_inputs and len(encoded) <= 1200:
            row["basis_value"] = deepcopy(item.get("value"))
        else:
            row["value_view"] = ("Exact value is in basis.drivers.input" if full_inputs else
                                 "Value omitted from manifest; request this named driver")
            row["value_json_chars"] = len(encoded)
        result[key] = row
    return result


def _candidate_contract_page(blackboard, input_):
    """Expose the installed compiler documentation verbatim, within the native limit."""
    from bellomberg.core.paths import PROJECT_ROOT
    from bellomberg.valuation.preparation_methods import method_guide
    from bellomberg.agents.specialists.base import _tetto_tool_result
    section, offset = input_.get("contract_section"), input_.get("offset", 0)
    if input_.get("drivers") or type(offset) is not int or offset < 0:
        return {"ok": False, "error": "Contract pages require a nonnegative integer offset and no drivers"}
    method = blackboard.source_qualification["method_id"]
    try:
        author_qualification = blackboard.source_qualification
        if method == 'operating_fcff' and section in {'shares', 'opening_nwc', 'draft_validation'}:
            from bellomberg.agents.model_authoring_evidence import acquire_board_statements, board_author_evidence_view
            acquire_board_statements(blackboard)
            author_qualification = board_author_evidence_view(blackboard)
        if section in {"method", "common"}:
            guide = (PROJECT_ROOT / "docs/guide/sector-valuations.md").read_text(encoding="utf-8")
            text = (method_guide(method, guide) if section == "method" else
                    guide[guide.index("## Common record contract"):guide.index("## Operating FCFF")])
        elif section == "evidence":
            from bellomberg.valuation.preparation_ai import SYSTEM
            text = SYSTEM
        elif method == "operating_fcff" and section == "accounting":
            from bellomberg.valuation.fcff_stage_arithmetic import SEMANTICS
            text = json.dumps(SEMANTICS, ensure_ascii=False, sort_keys=True)
        elif method == "operating_fcff" and section == "shares":
            from bellomberg.valuation.diluted_share_estimate import diluted_share_policy
            from bellomberg.valuation.statement_shares_evidence import NORMALIZER
            from bellomberg.valuation.preparation_ai import response_format
            report = author_qualification["source_report"]
            selection = report.get("selection") or {}
            sources = []
            for document in report["documents"]:
                metadata = document.get("metadata") or {}
                if (metadata.get("normalizer") != NORMALIZER
                        or metadata.get("source_document_id") != selection.get("selected_document_id")
                        or metadata.get("report_date") != selection.get("opening_date")):
                    continue
                body = json.loads(document["text"])
                comparison = body["tag_comparison"]
                basis = ("primary_inline_without_same_date_tag" if comparison["status"] == "missing_same_date_tag"
                         else "primary_statement_over_conflicting_tags" if comparison["conflicts"]
                         else "primary_statement_consistent_with_tags")
                sources.append({"document_id": document["id"],
                    "calculation": {"type": "statement_shares", "fact_index": 0,
                        "selection_basis": basis, "acknowledged_conflicts": deepcopy(comparison["conflicts"])},
                    "reported_precision": body.get("reported_precision"),
                    "proof": "Historical envelope, evidence_ids contains only this normalized document. No pointer, quote or facts. Scale the actual count to millions; preserve its share class and date. This is an observed count, not proof of point-in-time dilution. Disclose remaining dilution and exchangeable claims; never replace it silently with an EPS weighted average."})
            contract = _candidate_input_contract(blackboard)
            contract["preparation_stage"] = {"drivers": ["shares"], "diluted_denominator_policy": diluted_share_policy()}
            text = json.dumps({"diluted_estimate": diluted_share_policy(), "reported_share_sources": sources,
                "estimated_share_schema": response_format(contract)["json_schema"]["schema"]["properties"]["drivers"]["properties"]["shares"]},
                ensure_ascii=False, sort_keys=True)
        elif method == "operating_fcff" and section == "opening_nwc":
            from bellomberg.valuation.balance_working_capital import balance_nwc_policy
            policy = balance_nwc_policy(author_qualification["source_report"]["documents"])
            if policy is None:
                return {"ok": False, "error": "No reported-balance NWC contract is available for these documents"}
            text = json.dumps(policy, ensure_ascii=False, sort_keys=True)
        elif section == "draft_validation":
            from bellomberg.valuation.input_preparation import prepare_method_inputs
            from bellomberg.valuation.author_quotation import acquire_board_quotation
            from bellomberg.agents.model_authoring_evidence import board_author_evidence_view
            draft = deepcopy(blackboard.data.get("_model_input_draft"))
            if not isinstance(draft, dict):
                return {"ok": False, "error": "No saved author draft to validate"}
            quotation = acquire_board_quotation(blackboard)
            qualification = board_author_evidence_view(blackboard)
            checked = prepare_method_inputs(qualification["bundle"],
                documents=qualification["source_report"]["documents"], propose=lambda *_: deepcopy(draft),
                source_report=qualification["source_report"])
            diagnostic = {"status": checked["status"], "issues": checked.get("issues", []),
                "plan_sha256": _plan_digest(draft), "workbook_created": False,
                "quotation_basis": {key: deepcopy(quotation[key]) for key in ("status", "issues", "instruction")
                    if key in quotation},
                "quotation_next_action": "Request scope=model, drivers=[quotation] to read the exact observed driver and document_ids; explicitly submit or reuse it. Validation never fills the saved draft.",
                "instruction": "Prepared means input proofs passed, not forecast arithmetic, final Excel usability or approval. Calculated options use only your saved estimates; no economic choice or input change is applied."}
            if method == "operating_fcff":
                from bellomberg.valuation.growth_thesis_arithmetic import prove_growth_revenues, GrowthArithmeticError
                from bellomberg.valuation.fcff_stage_arithmetic import forecast_arithmetic
                diagnostic["revenue_arithmetic"] = {"status": "not_checked", "issues": {}}
                diagnostic["forecast_arithmetic"] = {"status": "not_checked"}
                if checked["status"] == "prepared":
                    thesis = {"scenarios": {scope: {"anchors": {name: deepcopy(drivers[name])
                        for name in ("revenue_build", "revenue_growth")}}
                        for scope, drivers in draft["scenarios"].items()}}
                    try:
                        prove_growth_revenues(thesis, draft)
                    except GrowthArithmeticError as exc:
                        diagnostic["revenue_arithmetic"] = {"status": "blocked", "issues": deepcopy(exc.issues)}
                    else:
                        diagnostic["revenue_arithmetic"]["status"] = "ready"
                        try:
                            arithmetic = forecast_arithmetic(draft)
                        except ValueError as exc:
                            diagnostic["forecast_arithmetic"] = {"status": "blocked", "reason": str(exc)}
                        else:
                            diagnostic["forecast_arithmetic"] = {"status": "ready", "derived_fcff_forecasts": arithmetic}
            text = json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
        else:
            return {"ok": False, "error": "Contract section unavailable for this method"}
    except (OSError, ValueError, KeyError) as exc:
        return {"ok": False, "error": "Installed compiler contract unavailable: " + str(exc)}
    if offset >= len(text):
        return {"ok": False, "error": "Contract offset exceeds the available text"}
    def page(end):
        return {"ok": True, "status": "compiler_contract_only", "section": section,
                "method_id": method, "sha256": _plan_digest(text), "offset": offset,
                "next_offset": end, "total_chars": len(text), "complete": end == len(text),
                "text": text[offset:end]}
    low, high = offset, min(len(text), offset + _tetto_tool_result())
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(page(middle), ensure_ascii=False)) + 256 <= _tetto_tool_result():
            low = middle
        else:
            high = middle - 1
    if low == offset:
        return {"ok": False, "error": "Compiler contract cannot fit the current tool-result limit"}
    return page(low)


def _handle_candidate_plan_tool(blackboard, desk, name, input_):
    if (desk != "fundamentals" or blackboard.current_round != 1
            or getattr(blackboard, "model_phase", None) != "building"):
        return {"ok": False, "error": "Only Fundamentals authors inputs during the model-building phase"}
    if blackboard.valuation_results.get(blackboard.target_ticker):
        return {"ok": False, "error": "Model already compiled; use an explicit exact-generation review"}
    contract = _candidate_input_contract(blackboard)
    scopes = ["model", *contract["scenarios"]]
    basis = blackboard.data.get("_model_input_basis") or {}
    basis_plan = basis.get("plan") or {}
    draft = deepcopy(blackboard.data.get("_model_input_draft") or {
        "model": {}, "scenarios": {scope: {} for scope in contract["scenarios"]},
        **({"scenario_rationale": {}} if contract["scenarios"] else {})})
    if name == "get_candidate_model_inputs":
        scope = input_.get("scope")
        if scope not in scopes:
            return {"ok": False, "error": "Unknown native model scope"}
        if "contract_section" in input_:
            return _candidate_contract_page(blackboard, input_)
        schema = contract["schema"]
        names = [key for key, desc in schema.items()
                 if desc[-1] == ("model" if scope == "model" else "scenario")]
        requested = input_.get("drivers") or []
        if not isinstance(requested, list) or any(key not in names for key in requested):
            return {"ok": False, "error": "Unknown native driver requested"}
        quotation = None
        statements = None
        qualification = blackboard.source_qualification
        if contract['method_id'] == 'operating_fcff' and (not requested or
                set(requested) & {'historical_revenue', 'opening_nwc', 'shares', 'net_debt', 'equity_adjustments'}):
            from bellomberg.agents.model_authoring_evidence import acquire_board_statements, board_author_evidence_view
            statements = acquire_board_statements(blackboard)
            qualification = board_author_evidence_view(blackboard)
        if scope == "model" and "quotation" in names and (not requested or "quotation" in requested):
            from bellomberg.valuation.author_quotation import acquire_board_quotation
            from bellomberg.agents.model_authoring_evidence import board_author_evidence_view
            quotation = acquire_board_quotation(blackboard)
            if quotation["status"] != "incomplete":
                try:
                    qualification = board_author_evidence_view(blackboard)
                except (ValueError, KeyError, OSError) as exc:
                    return {"ok": False, "error": "Derived quotation evidence unavailable: " + str(exc)}
            basis = blackboard.data.get("_model_input_basis") or {}
            basis_plan = basis.get("plan") or {}
        source = basis_plan.get("model", {}) if scope == "model" else (basis_plan.get("scenarios") or {}).get(scope, {})
        if quotation and quotation["status"] == "incomplete" and basis.get("quotation_receipt_sha256"):
            source = {key: value for key, value in source.items() if key != "quotation"}
        values = draft["model"] if scope == "model" else draft["scenarios"][scope]
        result = {"ok": True, "status": "inputs_only_no_current_workbook", "scope": scope,
                "contract_sections": ["method", "common", "evidence", "draft_validation"] +
                    (["accounting", "shares", "opening_nwc"] if contract["method_id"] == "operating_fcff" else []),
                "view": "full_requested_drivers" if requested else "manifest_not_full_driver_evidence",
                "contract": {**contract, "schema": {key: schema[key] for key in requested},
                    "requirements": {key: value for key, value in contract["requirements"].items() if key != "fields"},
                    "field_list_view": "requirements.fields omitted from this compact view; the compiler enforces the full native requirements"},
                "driver_catalog": _basis_driver_catalog(schema, source, values, requested or names,
                                                        full_inputs=bool(requested)),
                "basis": {"provenance": basis.get("provenance"), "plan_sha256": _plan_digest(basis_plan),
                          "approval": "Not an approval of this run or its assumptions",
                          "drivers": {key: {"input": deepcopy(source[key]), "driver_sha256": _plan_digest(source[key])}
                                      for key in requested if key in source},
                          "rationale_view": {"scope": scope, "full_text_in_this_view": False,
                              "sha256": _plan_digest((basis_plan.get("scenario_rationale") or {}).get(scope)
                                  if contract["scenarios"] else basis_plan.get("analysis_rationale")),
                              "excerpt": str((basis_plan.get("scenario_rationale") or {}).get(scope)
                                  if contract["scenarios"] else basis_plan.get("analysis_rationale") or "")[:800],
                              "instruction": "Archived rationale excerpt only. Fundamentals must supply its own complete scenario/analysis rationale; this excerpt is not an approved replacement."}},
                "draft": {key: deepcopy(values[key]) for key in requested if key in values},
                "missing_drivers": [key for key in names if key not in values],
                "qualified_document_ids": sorted(doc["id"] for doc in
                    qualification["source_report"]["documents"])}
        if quotation is not None:
            result["quotation_basis"] = {key: deepcopy(quotation[key]) for key in ("status", "issues", "instruction")
                if key in quotation}
        if statements is not None:
            result['statement_evidence'] = statements
        from bellomberg.agents.specialists.base import _tetto_tool_result
        oversized = len(json.dumps(result, ensure_ascii=False, default=str)) + 256 > _tetto_tool_result()
        if (oversized or "offset" in input_) and len(requested) == 1 and requested[0] in source:
            driver = requested[0]
            basis_sha = _plan_digest(basis_plan)
            encoded_input = json.dumps(source[driver], sort_keys=True, ensure_ascii=False,
                                       allow_nan=False, separators=(",", ":"))
            state_key = basis_sha + ":" + scope + ":" + driver
            reads = blackboard.data.setdefault("_input_driver_reads", {})
            offset = input_.get("offset", 0)
            if type(offset) is not int or offset < 0 or offset != reads.get(state_key, 0):
                return {"ok": False, "error": "Read this exact input in consecutive pages"}
            def input_page(end):
                return {"ok": True, "status": "input_driver_page", "scope": scope, "driver": driver,
                        "basis_plan_sha256": basis_sha, "driver_sha256": _plan_digest(source[driver]),
                        "offset": offset, "next_offset": end, "total_chars": len(encoded_input),
                        "complete": end == len(encoded_input), "input_json_fragment": encoded_input[offset:end]}
            low, high = offset, min(len(encoded_input), offset + _tetto_tool_result())
            while low < high:
                midpoint = (low + high + 1) // 2
                if len(json.dumps(input_page(midpoint), ensure_ascii=False)) + 256 <= _tetto_tool_result():
                    low = midpoint
                else:
                    high = midpoint - 1
            if low == offset:
                return {"ok": False, "error": "No input fragment fits the existing tool-result limit"}
            reads[state_key] = low
            return input_page(low)
        if statements is not None and (oversized or "offset" in input_) and len(requested) == 1:
            # These are observed facts, not an adopted reusable financial driver.
            encoded = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False)
            digest = _plan_digest(result)
            reads = blackboard.data.setdefault('_input_statement_reads', {})
            offset = input_.get('offset', 0)
            if type(offset) is not int or offset < 0 or offset != reads.get(digest, 0):
                return {'ok': False, 'error': 'Read this exact statement input view in consecutive pages'}
            def statement_page(end):
                return {'ok': True, 'status': 'statement_input_page', 'scope': scope,
                        'driver': requested[0], 'sha256': digest, 'offset': offset,
                        'next_offset': end, 'total_chars': len(encoded), 'complete': end == len(encoded),
                        'economic_decisions_applied': False, 'input_json_fragment': encoded[offset:end]}
            low, high = offset, min(len(encoded), offset + _tetto_tool_result())
            while low < high:
                middle = (low + high + 1) // 2
                if len(json.dumps(statement_page(middle), ensure_ascii=False)) + 256 <= _tetto_tool_result():
                    low = middle
                else:
                    high = middle - 1
            if low == offset:
                return {'ok': False, 'error': 'No statement input fragment available at this offset'}
            reads[digest] = low
            return statement_page(low)
        if "offset" in input_:
            return {"ok": False, "error": "An input offset requires one available named driver"}
        if oversized:
            return {"ok": False, "status": "input_view_too_large", "scope": scope,
                    "error": "Requested native input view exceeds the unchanged tool-result limit; request fewer named drivers",
                    "driver_names": names, "basis_plan_sha256": _plan_digest(basis_plan)}
        return result
    rationale = input_.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        return {"ok": False, "error": "rationale: expected an optional string audit note"}
    try:
        delta, transport = _decode_candidate_plan(input_.get("plan", {}))
    except (ValueError, TypeError, OverflowError) as exc:
        return {"ok": False, "error": "plan: invalid JSON object transport: " + str(exc),
                "correction": "Supply a plan object or its exact valid JSON object encoding. No data were saved."}
    if transport is not None:
        blackboard.data.setdefault("_model_plan_input_transports", []).append(transport)
    if not isinstance(delta, dict):
        return {"ok": False, "error": "plan: expected object, received " + type(delta).__name__}
    if set(delta) - {"model", "scenarios", "scenario_rationale", "analysis_rationale"}:
        return {"ok": False, "error": "Unsupported plan fields"}
    issues = [{"field": "plan." + key, "expected": "object", "received": type(delta[key]).__name__}
        for key in ("model", "scenarios", "scenario_rationale") if key in delta and not isinstance(delta[key], dict)]
    if issues:
        return {"ok": False, "error": "; ".join(row["field"] + ": expected object, received " + row["received"] for row in issues),
                "issues": issues, "correction": "Keep model as a driver map, scenarios as bear/base/bull driver maps, and scenario_rationale as bear/base/bull text. No data were saved."}
    reuse = input_.get("reuse", [])
    decisions = input_.get("consultation_decisions", [])
    if not isinstance(reuse, list) or not isinstance(decisions, list):
        return {"ok": False, "error": "Reuse and consultation decisions must be lists"}
    if not any(delta.values()) and not reuse and not decisions:
        return {"ok": False, "error": "Empty submission: supply plan driver groups, exact reuse references or consultation decisions. No data were saved."}
    try:
        json.dumps(input_, allow_nan=False)
        for ref in reuse:
            scope, driver = ref["scope"], ref["driver"]
            if scope not in scopes or ref["basis_plan_sha256"] != _plan_digest(basis_plan):
                raise ValueError("Input basis identity changed")
            source = basis_plan.get("model", {}) if scope == "model" else basis_plan["scenarios"][scope]
            item = source[driver]
            if ref["driver_sha256"] != _plan_digest(item):
                raise ValueError("Reused driver identity changed")
            target = delta.setdefault("model", {}) if scope == "model" else delta.setdefault("scenarios", {}).setdefault(scope, {})
            if driver in target:
                raise ValueError("Driver both replaced and reused")
            target[driver] = deepcopy(item)
        for scope, values in [("model", delta.get("model", {})), *delta.get("scenarios", {}).items()]:
            if scope not in scopes or not isinstance(values, dict):
                raise ValueError("Unknown or invalid driver scope")
            target = draft["model"] if scope == "model" else draft["scenarios"][scope]
            for driver, item in values.items():
                descriptor = contract["schema"].get(driver)
                if (descriptor is None or descriptor[-1] != ("model" if scope == "model" else "scenario")
                        or not isinstance(item, dict) or not {"value", "kind", "evidence_ids", "rationale", "valid_until", "valid_until_basis"} <= set(item)):
                    raise ValueError("Unknown driver or incomplete native envelope: " + scope + "." + driver)
                target[driver] = deepcopy(item)
        if "scenario_rationale" in delta:
            if any(key not in contract["scenarios"] or not isinstance(value, str) or not value.strip()
                   for key, value in delta["scenario_rationale"].items()):
                raise ValueError("Invalid scenario rationale")
            draft.setdefault("scenario_rationale", {}).update(delta["scenario_rationale"])
        if "analysis_rationale" in delta:
            if contract["scenarios"] or not isinstance(delta["analysis_rationale"], str) or not delta["analysis_rationale"].strip():
                raise ValueError("Analysis rationale requires the native exposure contract")
            draft["analysis_rationale"] = delta["analysis_rationale"]
        conversations = deepcopy(blackboard.data.get("_model_consultations") or [])
        heads = {row["id"]: row for row in _current_model_consultations(blackboard, rows=conversations)}
        for decision in decisions:
            row = heads.get(decision.get("consultation_id")) if isinstance(decision, dict) else None
            if row is None or not _consultation_received(blackboard, row) or not _valid_fundamentals_decision(row, decision):
                raise ValueError("Consultation outcome requires a current answer received in a prior native Fundamentals turn and rationale")
            row["fundamentals_decision"] = deepcopy(decision)
    except (ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    blackboard.data.setdefault("_model_draft_history", []).append({
        "before_sha256": _plan_digest(blackboard.data.get("_model_input_draft") or {}),
        "after_sha256": _plan_digest(draft), "rationale": rationale,
        "explicit_delta": deepcopy(delta), "reused_driver_refs": deepcopy(reuse)})
    blackboard.data["_model_input_draft"] = draft
    blackboard.data["_model_consultations"] = conversations
    return {"ok": True, "status": "draft_only", "plan_sha256": _plan_digest(draft),
            "model_created": False, "scopes": scopes}


def _consult_model_desk(blackboard, requester, target, question, draft_assumptions, evidence_refs, supersedes_failed=None):
    if (requester != "fundamentals" or blackboard.current_round != 1
            or getattr(blackboard, "model_phase", None) != "building"
            or target not in set(TRADE_IDEA_DESKS) - {"fundamentals"}):
        return {"ok": False, "error": "Live consultation requires the Fundamentals model-building phase and another desk"}
    if (not isinstance(question, str) or not question.strip() or not isinstance(draft_assumptions, dict)
            or not draft_assumptions or not isinstance(evidence_refs, list)
            or any(not isinstance(ref, str) for ref in evidence_refs)
            or set(evidence_refs) - _review_source_ids(blackboard)):
        return {"ok": False, "error": "A question, explicit draft assumptions and retrieved evidence IDs are required"}
    rows = blackboard.data.setdefault("_model_consultations", [])
    if getattr(blackboard, "_consultation_active", False) or len(rows) >= MODEL_CONSULTATION_LIMIT:
        return {"ok": False, "error": "Recursive consultation or model consultation limit reached"}
    row = {"id": "model-consultation-" + uuid.uuid4().hex, "requester": requester, "desk": target,
           "question": question, "draft_assumptions": deepcopy(draft_assumptions),
           "draft_sha256": _plan_digest(draft_assumptions), "evidence_refs": list(evidence_refs),
           "source_fingerprint": _consultation_source_fingerprint(blackboard), "status": "started"}
    try:
        heads = _current_model_consultations(blackboard)
        if supersedes_failed is not None:
            row["supersedes_failed"] = deepcopy(supersedes_failed)
            after = supersedes_failed.get("after_consultation_ids") if isinstance(supersedes_failed, dict) else None
            if not isinstance(after, list):
                raise ValueError("Explicit recovery context required")
            current = {peer["id"]: peer for peer in heads}
            context = []
            for consultation_id in after:
                peer = current.get(consultation_id) if isinstance(consultation_id, str) else None
                if peer is None or not _consultation_received(blackboard, peer):
                    raise ValueError("Recovery requires peer answers received before this native Fundamentals response")
                receipt = blackboard.data["_fundamentals_received_consultations"][consultation_id]
                context.append({**_consultation_answer_identity(peer), "response_id": receipt["response_id"]})
            row["recovery_context"] = context
            row["economic_intent_sha256"] = _recovery_intent(row, context)
        _current_model_consultations(blackboard, rows=[*rows, row])
    except (ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    from bellomberg.agents.consigliere_multi import SPECIALIST_ORDER
    cls = next(cls for cls in SPECIALIST_ORDER if cls.name == target)
    rows.append(row)
    _persist_model_build(blackboard)
    before_receipts, before_usage = len(blackboard.tool_receipts), len(blackboard.usage_log)
    blackboard._consultation_active = True
    try:
        specialist = cls(blackboard)
        response = specialist.run(1, task_context={"kind": "model_consultation", "consultation": True, **deepcopy(row),
            "instruction": "Answer this actual question about the draft. State driver mechanisms, sourced evidence and dissent or non-relevance. Do not write the common model or request another consultation."}, publish_report=False)
        row.update(response=response, status=getattr(specialist, "consultation_result_status", "failed"),
                   tool_receipts=deepcopy(blackboard.tool_receipts[before_receipts:]),
                   usage=deepcopy(blackboard.usage_log[before_usage:]))
        terminal = getattr(specialist, "consultation_terminal_response", None)
        if terminal is not None:
            row["terminal_response"] = deepcopy(terminal)
        if not isinstance(response, str) or not response.strip() or response.startswith("[ERROR") or not row["usage"]:
            row["status"] = "failed"
    except Exception as exc:
        row.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
    finally:
        blackboard._consultation_active = False
        blackboard.mark_specialist_start("fundamentals", 1)
    view = {key: deepcopy(row.get(key)) for key in
            ("id", "desk", "question", "draft_sha256", "source_fingerprint", "evidence_refs", "status", "response", "error")}
    from bellomberg.agents.specialists.base import _tetto_tool_result
    row["author_view_complete"] = len(json.dumps(view, ensure_ascii=False)) + 256 <= _tetto_tool_result()
    if not row["author_view_complete"]:
        view.pop("response", None)
        view.update(status="answer_requires_paged_read", response_chars=len(row.get("response") or ""),
                    instruction="Read the completed answer with read_candidate_model_consultation before declaring its outcome")
    _persist_model_build(blackboard)
    return {"ok": row["status"] == "complete" and row["author_view_complete"], "consultation": view}


def _build_fundamentals_candidate(blackboard, input_, output_dir, *, evaluator=None):
    lock = getattr(blackboard, '_source_research_lock', None)
    if lock is not None:
        # Source callbacks must not replace the qualified dossier while the
        # local compiler creates and registers its exact immutable generation.
        with lock:
            return _compile_fundamentals_candidate(blackboard, input_, output_dir, evaluator=evaluator)
    return _compile_fundamentals_candidate(blackboard, input_, output_dir, evaluator=evaluator)


def _clear_verified_model_authoring_failure(blackboard):
    """Clear an obsolete error only after the caller verified the exact workbook."""
    failed = blackboard.data.pop("_model_authoring_completion_failed", None)
    previous_error = blackboard.data.pop("_model_completion_error", None)
    if previous_error:
        blackboard.data.setdefault("_historical_model_completion_errors", []).append(previous_error)
    if failed is not None or previous_error:
        _persist_model_build(blackboard)


def _complete_saved_model_authoring(blackboard):
    """Finish a missing authored model using the native, separately checkpointed task.

    The research round and its paid conversations remain intact. This task may
    read admitted evidence and persist/compile the author's plan; it cannot
    purchase the completed peer consultations again.
    """
    from bellomberg.agents.specialists.base import _checkpoint_digest
    from bellomberg.agents.specialists.fundamentals import FundamentalsSpecialist

    if _verified_candidate_valuations(blackboard):
        _clear_verified_model_authoring_failure(blackboard)
        return True
    gate = getattr(blackboard, "budget_gate", None)
    if not isinstance(gate, TradeIdeaBudgetGate):
        return False
    run = gate.store.get_run(blackboard.run_id)
    if "model_preparation" not in (run["run"].get("authorization") or {}).get("activities", ()):
        raise RuntimeError("Model authoring is not included in the accepted authorization")
    blackboard.raise_if_run_blocked()
    origin = deepcopy(getattr(blackboard, "specialist_checkpoints", {}).get("fundamentals:R1"))
    if not isinstance(origin, dict):
        return False
    seal = origin.pop("sha256", None)
    if (seal != _checkpoint_digest(origin) or origin.get("status") != "complete"
            or origin.get("pending_tools") or origin.get("inflight_tools")
            or origin.get("usage_unknown") is not False
            or origin.get("thinking") != {"type": "effort", "effort": "max"}
            or origin.get("thinking_prima") is not None):
        raise RuntimeError("Model authoring origin is incomplete or its checkpoint differs")
    proofs = gate.store.specialist_response_receipts(blackboard.run_id, "fundamentals", origin["usage"])
    response = proofs[-1]["response"]
    visible = "\n".join(block.get("text", "") for block in response.get("content", ())
                        if block.get("type") == "text" and block.get("text"))
    if (response.get("stop_reason") != "end_turn" or not visible
            or not isinstance(origin.get("report"), str) or not origin["report"].endswith(visible)
            or blackboard.read("fundamentals", 1) != origin["report"]):
        raise RuntimeError("Model authoring report differs from the paid response")
    rows = _current_model_consultations(blackboard)
    if ({row["desk"] for row in rows} != set(TRADE_IDEA_DESKS) - {"fundamentals"}
            or any(not _consultation_received(blackboard, row) for row in rows)):
        # Missing peer evidence remains an explicit prerequisite. No repeated
        # consultation or fabricated author decision is authorized by recovery.
        return False
    descriptor = blackboard.data.get("_model_authoring_completion")
    if descriptor is None:
        descriptor = {"version": 1, "mode": "model_authoring_completion",
            "origin_run_id": blackboard.run_id, "origin_checkpoint_sha256": seal,
            "origin_report_sha256": _plan_digest(origin.get("report")),
            "consultations": [_consultation_answer_identity(row) for row in rows],
            "source_fingerprint": _consultation_source_fingerprint(blackboard)}
        blackboard.data["_model_authoring_completion"] = deepcopy(descriptor)
        blackboard.data["_model_authoring_origin"] = {"checkpoint": {**deepcopy(origin), "sha256": seal},
            "last_receipt": deepcopy(proofs[-1])}
        blackboard.persist_run_checkpoint("model_authoring_completion_created")
    archived = blackboard.data.get("_model_authoring_origin") or {}
    if (descriptor.get("version") != 1 or descriptor.get("mode") != "model_authoring_completion"
            or descriptor.get("origin_run_id") not in run["cost"]["chain_run_ids"]
            or descriptor.get("origin_checkpoint_sha256") != seal
            or descriptor.get("origin_report_sha256") != _plan_digest(origin.get("report"))
            or descriptor.get("consultations") != [_consultation_answer_identity(row) for row in rows]
            or descriptor.get("source_fingerprint") != _consultation_source_fingerprint(blackboard)
            or archived.get("checkpoint") != {**origin, "sha256": seal}
            or archived.get("last_receipt") != proofs[-1]):
        raise RuntimeError("Model authoring recovery origin or peer evidence changed")
    task = {"kind": "model_authoring_completion", "version": 1, "origin_checkpoint_sha256": seal}
    key = "fundamentals:R1:" + _checkpoint_digest(task)
    prior = getattr(blackboard, "specialist_checkpoints", {}).get(key)
    if prior and prior.get("status") in ("complete", "author_incomplete", "truncated", "failed"):
        recovery = getattr(blackboard, "specialist_response_recovery", None) or {}
        reviewed_failure = (prior.get("status") == "failed"
            and recovery.get("kind") == "failed_model_authoring"
            and recovery.get("mode") == "model_authoring_error_retry"
            and recovery.get("checkpoint_key") == key
            and recovery.get("specialist_checkpoint_sha256") == prior.get("sha256"))
        if not reviewed_failure:
            raise RuntimeError("Saved model-authoring task ended without a verified workbook; explicit review required")
    messages = deepcopy(origin["messages"])
    messages.append({"role": "assistant", "content": deepcopy(response["content"])})
    messages.append({"role": "user", "content": (
            "Continue only the unfinished model construction. The preceding research and peer answers are paid "
            "historical work, not a request to repeat them. Save consultation_decisions and evidenced drivers "
            "incrementally using submit_candidate_model_plan, then compile with get_valuation. An absent "
            "archived input means author that driver from the admitted documents, not repeatedly reread an empty "
        "basis. Preserve every unavailable datum explicitly; never invent a value to pass validation.")})
    history = {"genuine_history": True, "native_model_completion": True,
        "origin": {**deepcopy(origin), "sha256": seal}, "last_receipt": deepcopy(proofs[-1]),
        "source_fingerprint": blackboard.source_qualification.get("fingerprint"),
        "messages": messages, "messages_sha256": _plan_digest(messages)}
    blackboard.current_round, blackboard.model_phase = 1, "building"
    blackboard.independent_round = 0
    actor = FundamentalsSpecialist(blackboard)
    actor._trade_idea_author_history = history
    actor.run(1, task_context=task, publish_report=False)
    refs = _verified_candidate_valuations(blackboard)
    completion_error = None
    if not refs and getattr(actor, "run_result_status", None) == "complete":
        completed = deepcopy(blackboard.specialist_checkpoints.get(key) or {})
        completed_sha = completed.pop("sha256", None)
        if (completed_sha != _checkpoint_digest(completed) or completed.get("status") != "complete"
                or completed.get("pending_tools") or completed.get("inflight_tools")
                or completed.get("usage_unknown") is not False):
            raise RuntimeError("Completed model-authoring task is not attested for local compilation")
        final = gate.store.specialist_response_receipts(
            blackboard.run_id, "fundamentals", completed["usage"])[-1]["response"]
        text = "\n".join(block.get("text", "") for block in final.get("content", ())
                         if block.get("type") == "text" and block.get("text"))
        if (final.get("stop_reason") != "end_turn" or not text
                or not isinstance(completed.get("report"), str) or not completed["report"].endswith(text)):
            raise RuntimeError("Completed model-authoring report differs from its paid response")
        # Saving the last driver group may consume the final tool turn. Run
        # the existing local compiler once; its financial gates and exact-plan
        # attempt journal remain authoritative and no provider call is added.
        completion = blackboard.build_candidate_model({"ticker": blackboard.target_ticker})
        if not completion.get("ok"):
            completion_error = completion.get("error") or "Model compilation incomplete"
        refs = _verified_candidate_valuations(blackboard)
    if not refs:
        blackboard.data["_model_authoring_completion_failed"] = True
        blackboard.data["_model_completion_error"] = completion_error or "Model authoring ended without a usable verified workbook"
        _persist_model_build(blackboard)
        return False
    _clear_verified_model_authoring_failure(blackboard)
    _persist_model_build(blackboard)
    return True


def _compile_fundamentals_candidate(blackboard, input_, output_dir, *, evaluator=None):
    if set(input_) != {"ticker"} or input_["ticker"].upper() != blackboard.target_ticker:
        return {"ok": False, "error": "Compile only the explicit drafted plan for the selected ticker"}
    if getattr(blackboard, "_consultation_active", False) or blackboard.model_phase != "building":
        return {"ok": False, "error": "Common model compilation is unavailable in this phase"}
    audit_rows = blackboard.data.get("_model_consultations") or []
    try:
        rows = _current_model_consultations(blackboard)
    except (ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    if ({row["desk"] for row in rows} != set(TRADE_IDEA_DESKS) - {"fundamentals"}
            or any(not _consultation_received(blackboard, row)
                   or not _valid_fundamentals_decision(row, row.get("fundamentals_decision")) for row in rows)):
        return {"ok": False, "error": "Every other desk must answer a live model question and Fundamentals must record its outcome"}
    plan = blackboard.data.get("_model_input_draft")
    if not isinstance(plan, dict):
        return {"ok": False, "error": "Explicit complete Fundamentals plan is absent"}
    gate = getattr(blackboard, "budget_gate", None)
    if gate is not None:
        grant = gate.store.get_run(gate.run_id)["run"].get("authorization") or {}
        if "model_preparation" not in (grant.get("activities") or []):
            return {"ok": False, "error": "Model construction is not included in this run authorization"}
    refs = _verified_candidate_valuations(blackboard)
    if refs:
        return {"ok": True, "data": {**blackboard.valuation_results[blackboard.target_ticker], "reused_in_run": True},
                "_source": "get_valuation: exact Trade Idea candidate generation"}
    if blackboard.valuation_results.get(blackboard.target_ticker) or blackboard.valuation_generations:
        return {"ok": False, "error": "Recorded candidate generation is not verifiable; automatic regeneration is disabled"}
    qualify_research = getattr(blackboard, 'qualify_research_model', None)
    if callable(qualify_research):
        try:
            qualify_research(plan)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            blackboard.data['_model_plan_last_error'] = {'error': type(exc).__name__ + ': ' + str(exc),
                'status': 'historical_proofs_incomplete', 'plan_sha256': _plan_digest(plan)}
            _persist_model_build(blackboard)
            return {'ok': False, **blackboard.data['_model_plan_last_error']}
    identity = {"source_fingerprint": blackboard.source_qualification.get("fingerprint"),
                "plan_sha256": _plan_digest(plan)}
    attempts = blackboard.data.setdefault("_model_compilation_attempts", [])
    previous = next((row for row in reversed(attempts) if all(row.get(key) == value for key, value in identity.items())), None)
    if previous is not None:
        return {"ok": False, "error": previous.get("error") or "This exact plan compilation already started; no automatic retry",
                "status": previous["status"], "plan_sha256": identity["plan_sha256"]}
    attempt = {**identity, "status": "started", "at": datetime.now(timezone.utc).isoformat()}
    attempts.append(attempt)
    _persist_model_build(blackboard)
    try:
        if evaluator is not None:
            evaluator(blackboard)
            payload = blackboard.valuation_results.get(blackboard.target_ticker)
        else:
            from bellomberg.valuation.trade_idea_model import build_from_plan
            payload = build_from_plan(blackboard.source_qualification, plan, output_dir,
                author_context={"actor": "fundamentals", "human_approved": False,
                                "plan_sha256": identity["plan_sha256"], "consultations": deepcopy(audit_rows),
                                "current_consultation_ids": {row["desk"]: row["id"] for row in rows},
                                "fundamentals_received_consultations": deepcopy(blackboard.data.get("_fundamentals_received_consultations", {})),
                                "draft_history": deepcopy(blackboard.data.get("_model_draft_history") or []),
                                "input_basis": {key: value for key, value in
                                    (blackboard.data.get("_model_input_basis") or {}).items() if key != "plan"}})
            payload = _record_candidate_model(blackboard, payload, specialist="fundamentals-model-build")
    except Exception as exc:
        attempt.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        _persist_model_build(blackboard)
        return {"ok": False, "error": attempt["error"], "status": "compilation_failed"}
    refs = _verified_candidate_valuations(blackboard)
    blackboard.data["_model_review"] = {"status": "initial_ready" if refs else "blocked",
        "initial_refs": refs, "final_refs": refs, "revision_log": [], "objections": []}
    attempt.update(status="succeeded" if refs else "failed")
    if not refs:
        attempt["error"] = "Compilation did not register a usable verified candidate workbook"
    _persist_model_build(blackboard)
    return {"ok": bool(refs), "data": payload, "_source": "get_valuation: compiled Fundamentals authored plan"}


# E7 (04/10/2026, Opus 5.5): vincoli e mandato del PM per Red Team e desk della Trade Idea.
# Il Red Team e la Blackboard girano con memory_db=None (commit 1326312): la run non deve
# scrivere sul DB e la ripresa deve rileggere SOLO il checkpoint. Effetto collaterale: il
# blocco «parole vincolanti del PM» (feedback e veti) restava vuoto e lo diceva solo il log.
# Cura: una lettura in SOLA LETTURA all'avvio (connessione sqlite mode=ro, nessuna DDL,
# nessuno store vettoriale), fotografata in blackboard.data e quindi nel checkpoint; Red Team
# e desk ricevono TESTO, mai il DB. Un buco e' una frase dichiarata, mai una stringa vuota.
PM_CONSTRAINTS_KEY = "_pm_constraints"


def _pm_binding_reader(db_path):
    """Un MemoryDB che sa solo LEGGERE: stessi metodi del Consigliere
    (`build_pm_binding_block`), connessione `mode=ro`. Una scrittura solleva."""
    import sqlite3
    from pathlib import Path
    from bellomberg.storage.memory_db import MemoryDB

    class _SolaLettura(MemoryDB):
        def __init__(self, path):  # niente _init_sqlite/_init_chroma: zero scritture
            self.db_path = path
            self.chroma_path = None
            self.chroma_client = self.col_memos = self.col_decisions = self.col_feedback = None

        @contextmanager
        def _conn(self):
            if not Path(self.db_path).is_file():
                raise FileNotFoundError("database assente: " + Path(self.db_path).name)
            conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA busy_timeout=5000")
                yield conn
            finally:
                conn.close()

    return _SolaLettura(db_path)


def _pm_constraints_snapshot(db_path, mandate, *, origin, text_policy=None):
    from bellomberg.agents.specialists.base import blocco_vincoli_pm, stato_vincoli_pm
    from bellomberg.core import mandato_pm
    mandato_pm._validate_text_policy(text_policy)
    try:
        text = blocco_vincoli_pm(_pm_binding_reader(db_path))
        reason = None
        if not text.strip():
            reason = "blocco vuoto dal registro"
        elif "NON DISPONIBILI" in text.splitlines()[0]:
            found = re.search(r"\(motivo: (.*?)\)\. NON dedurre", text, re.S)
            reason = "registro non ha risposto (" + (found.group(1) if found else "motivo non letto") + ")"
    except Exception as exc:
        text, reason = "", type(exc).__name__ + ": " + str(exc)[:300]
    if reason is not None:
        text = ("=== PAROLE DIRETTE DEL PM — NON DISPONIBILI in questa run ===\n"
                "vincoli del PM non disponibili: " + reason + ". NON dedurre che non ci siano "
                "vincoli: se un'idea somiglia a qualcosa che il PM puo' aver gia' rifiutato o "
                "vietato, dichiaralo invece di riproporla come nuova.\n\n")
    binding = {"status": "unavailable" if reason else "available", "text": text,
               "state": stato_vincoli_pm(text)[0], "reason": reason}
    try:
        if not isinstance(mandate, dict):
            raise ValueError("mandato non caricato")
        mandate_row = {"status": "available", "text": mandato_pm.blocco_prompt(mandate, **({"text_policy": text_policy} if text_policy is not None else {})),
                       "fingerprint": mandato_pm.impronta(mandate), "reason": None}
    except Exception as exc:
        reason = type(exc).__name__ + ": " + str(exc)[:300]
        mandate_row = {"status": "unavailable", "fingerprint": None, "reason": reason,
                       "text": mandato_pm.riga_senza_mandato() + "\n[MANDATO n.d.] " + reason}
    return {"version": 1, "origin": origin, "read_at": datetime.now(timezone.utc).isoformat(),
            "binding": binding, "mandate": mandate_row}


def _bind_pm_constraints(blackboard, db_path, mandate, *, resumed=False):
    """Fotografia unica per run. Alla ripresa vale quella del checkpoint; un checkpoint
    precedente alla cura (chiave assente) riceve una lettura NUOVA, dichiarata come tale."""
    from bellomberg.core import mandato_pm
    text_policy = mandato_pm.text_policy_for_board(blackboard)
    saved = blackboard.data.get(PM_CONSTRAINTS_KEY)
    if isinstance(saved, dict) and saved.get("version") == 1:
        return saved
    origin = "letti alla ripresa: il checkpoint precedente non li conteneva" if resumed else "run_start"
    snapshot = _pm_constraints_snapshot(db_path, mandate, origin=origin, text_policy=text_policy)
    blackboard.data[PM_CONSTRAINTS_KEY] = snapshot
    print("[TRADE_IDEA] vincoli PM: " + snapshot["binding"]["state"]
          + " | mandato: " + snapshot["mandate"]["status"] + " | origine: " + origin)
    return snapshot


def pm_constraints_text(blackboard):
    """(vincoli, mandato) come testo per i prompt; mai stringhe vuote."""
    snapshot = (getattr(blackboard, "data", None) or {}).get(PM_CONSTRAINTS_KEY)
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        from bellomberg.core import mandato_pm
        why = "fotografia dei vincoli assente dalla blackboard di questa run"
        return (("=== PAROLE DIRETTE DEL PM — NON DISPONIBILI in questa run ===\n"
                 "vincoli del PM non disponibili: " + why + ". NON dedurre che non ci siano vincoli.\n\n"),
                mandato_pm.riga_senza_mandato() + "\n[MANDATO n.d.] " + why)
    return snapshot["binding"]["text"], snapshot["mandate"]["text"]


def pm_constraints_gaps(blackboard):
    """Le frasi per data_gaps quando vincoli o mandato mancano; [] se entrambi presenti."""
    snapshot = (getattr(blackboard, "data", None) or {}).get(PM_CONSTRAINTS_KEY)
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        return ["vincoli del PM non disponibili: fotografia assente dalla run",
                "mandato del PM non disponibile al Red Team: fotografia assente dalla run"]
    gaps = []
    if snapshot["binding"]["status"] != "available":
        gaps.append("vincoli del PM non disponibili: " + str(snapshot["binding"]["reason"]))
    if snapshot["mandate"]["status"] != "available":
        gaps.append("mandato del PM non disponibile al Red Team: " + str(snapshot["mandate"]["reason"]))
    if blackboard.data.get("_red_team_system_pre_vincoli"):
        # Review RV-E7 (P1): critica gia' pagata riusata col system originale, senza mandato.
        gaps.append("critica del Red Team ripresa da checkpoint precedente: "
                    "non conteneva il mandato del PM")
    if snapshot.get("origin") != "run_start":
        # Review RV-E7 (P2): ripresa da un checkpoint senza fotografia. I vincoli sono quelli
        # di ADESSO (possono contenere veti nuovi) e i round chiusi prima non li avevano.
        gaps.append("vincoli del PM letti alla ripresa (" + str(snapshot.get("read_at"))[:16]
                    + " UTC): i round completati prima della ripresa non li avevano ricevuti")
    return gaps


def official_documents_gaps(blackboard):
    """Il buco «nessun bilancio» dichiarato dal SERVER, non lasciato al testo del Capo.

    Una Trade Idea di ricerca parte anche senza filing (preflight e ricerca R0 li
    cercano, ma possono non trovarne): se il dossier sigillato del titolo non ha
    alcun documento ufficiale ammesso, il risultato lo dice sempre. [] altrimenti.
    """
    if not is_research_mode(blackboard):
        return []
    ticker = getattr(blackboard, "target_ticker", None)
    sealed = (getattr(blackboard, "data", None) or {}).get("_research_thesis")
    dossier = ((sealed.get("dossiers") or {}).get(ticker) if isinstance(sealed, dict) else None)
    if not isinstance(dossier, dict):
        return ["copertura documentale non verificabile: dossier del titolo assente dal sigillo della ricerca"]
    if dossier.get("documents"):
        # APERTO-TI: documenti ammessi ma tutti «non verificati» (PM 06/10): si leggono, non sono filing verificati.
        if all(((doc.get("metadata") or {}).get("filing_bridge") or {}).get("verifica") == "non_verificato"
               for doc in dossier["documents"]):
            return ["solo documenti dell'emittente NON verificati nel dossier (ammessi con etichetta): l'analisi "
                    "li legge, ma non poggia su filing primari verificati"]
        return []
    return ["nessun bilancio o documento ufficiale dell'emittente ammesso alla ricerca "
            "(verifica fonti e ricerca R0 senza esito): l'analisi non poggia su filing primari verificati"]


def _run_research_red_team(blackboard, portfolio, runner):
    from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
    reference = research_reference(blackboard)
    with blackboard._lock:
        previous = blackboard.get_latest('red_team')
        if previous:
            blackboard.data.setdefault('_red_review_history', []).append({
                'review': deepcopy(previous), 'attestation': deepcopy(blackboard.data.get('_red_research_review'))})
        for key in ('red_team', '_red_team', '_red_research_review', '_red_team_native_terminal',
                    '_red_team_citation_correction'):
            blackboard.data.pop(key, None)
    # E7: memory_db resta None (nessun DB al Red Team); vincoli e mandato del PM arrivano
    # come testo dalla fotografia PM_CONSTRAINTS_KEY (v. _bind_pm_constraints).
    returned = runner(blackboard, portfolio_data=portfolio, memory_db=None)
    report = (blackboard.get_latest('red_team') or {}).get('report')
    terminal = blackboard.data.get('_red_team_native_terminal')
    if terminal is not None and (not isinstance(terminal, dict)
            or terminal.get('stop_reason') != 'end_turn' or terminal.get('text_present') is not True):
        raise RuntimeError('Research Red Team native response incomplete')
    if (not isinstance(returned, str) or returned != report or not report
            or motivo_critica_non_utilizzabile(report) or research_reference(blackboard) != reference):
        raise RuntimeError('A complete Red Team critique of the sealed research thesis is required')
    blackboard.data['_red_research_review'] = {'research_ref': reference, 'report_sha256': _plan_digest(report)}
    return report


def _require_final_research(blackboard):
    if blackboard.valuation_results or blackboard.valuation_generations or blackboard.valuation_attempts:
        raise RuntimeError('Research-only run contains unexpected workbook generations')
    reference = research_reference(blackboard)
    sealed = blackboard.data['_research_thesis']
    gaps = blackboard.data.get('_desk_gaps') or {}
    for desk in TRADE_IDEA_DESKS:
        if desk not in sealed['reports']:
            if desk not in (sealed.get('missing_reports') or {}):
                raise RuntimeError('Desk neither sealed nor declared missing: ' + desk)
            continue
        if desk in gaps:  # final round failed after the seal: declared, its sealed R1 remains
            continue
        row = (blackboard.data.get('_desk_research_reviews') or {}).get(desk) or {}
        report = blackboard.read(desk, 2)
        if (row.get('round') != 2 or row.get('research_ref') != reference
                or not isinstance(report, str) or not report.strip() or report.startswith('[ERROR')
                or row.get('report_sha256') != _plan_digest(report)):
            raise RuntimeError('Final exact-research discussion is missing or stale: ' + desk)
    red = blackboard.get_latest('red_team')
    attestation = blackboard.data.get('_red_research_review') or {}
    if (not red or attestation.get('research_ref') != reference
            or attestation.get('report_sha256') != _plan_digest(red['report'])):
        raise RuntimeError('Final exact-research Red Team discussion is missing or stale')
    return reference


def _missing_research_replies(blackboard):
    return [row for row in blackboard.data.get('_objections', [])
            if row['objection']['material'] and not row.get('response') and not row.get('unanswered')]


def _complete_research_objection_replies(blackboard):
    """Close only missing desk replies; native task checkpoints preserve paid R2."""
    if not is_research_mode(blackboard):
        return
    from bellomberg.agents.specialists import ALL_SPECIALISTS
    from bellomberg.agents.specialists.base import _checkpoint_digest
    reference = _require_final_research(blackboard)
    tasks = blackboard.data.setdefault('_research_reply_completions', {})
    for actor_type in ALL_SPECIALISTS:
        desk = actor_type.name
        missing = [row for row in _missing_research_replies(blackboard) if row['objection']['desk'] == desk]
        descriptor = tasks.get(desk)
        if desk in (blackboard.data.get('_desk_gaps') or {}):
            _flag_unanswered(blackboard, missing, "addressed desk unavailable (declared gap)")
            continue
        if descriptor is not None and descriptor.get('unanswered'):
            _flag_unanswered(blackboard, missing, "desk reply completion did not complete")
            continue
        if not missing and descriptor is None:
            continue
        blackboard.raise_if_run_blocked()
        report_hash = _plan_digest(blackboard.read(desk, 2))
        if descriptor is None:
            task = {'kind': 'research_objection_completion', 'version': 1, 'desk': desk,
                'research_ref': deepcopy(reference), 'origin_report_sha256': report_hash,
                'objection_ids': [row['objection']['id'] for row in missing]}
            descriptor = {'task': task, 'objections': [deepcopy(row['objection']) for row in missing],
                          'complete': False}
            tasks[desk] = descriptor
            blackboard.persist_run_checkpoint('research_objection_completion_created')
        task = descriptor['task']
        actual_objections = {row['objection']['id']: row['objection']
                             for row in blackboard.data['_objections']}
        if (task.get('research_ref') != reference or task.get('origin_report_sha256') != report_hash
                or task.get('desk') != desk or task.get('version') != 1
                or task.get('kind') != 'research_objection_completion'
                or task.get('objection_ids') != [row['id'] for row in descriptor['objections']]
                or any(actual_objections.get(row['id']) != row for row in descriptor['objections'])
                or any(row['objection']['id'] not in task['objection_ids'] for row in missing)):
            raise RuntimeError('Research reply completion context or original report differs: ' + desk)
        key = (desk + ':R2:' + _checkpoint_digest(task) + ':model:' + _checkpoint_digest(
            {name: None for name in ('snapshot_id', 'generation_id', 'workbook_sha256')}))
        prior = blackboard.specialist_checkpoints.get(key)
        if descriptor.get('complete'):
            saved = deepcopy(prior or {})
            seal = saved.pop('sha256', None)
            if (missing or seal != _checkpoint_digest(saved) or saved.get('status') != 'complete'
                    or descriptor.get('checkpoint_sha256') != seal):
                raise RuntimeError('Completed research reply checkpoint or ledger differs: ' + desk)
            continue
        blackboard.current_round = 2
        actor = actor_type(blackboard)
        blackboard.reply_completion_desk = desk
        try:
            actor.run(2, task_context=deepcopy(task), publish_report=False)
        finally:
            blackboard.reply_completion_desk = None
        blackboard.raise_if_run_blocked()
        if getattr(actor, 'run_result_status', None) != 'complete':
            _flag_unanswered(blackboard, [row for row in _missing_research_replies(blackboard)
                                          if row['objection']['desk'] == desk],
                             "desk reply completion did not complete")
            descriptor.update(complete=False, unanswered=True)
            blackboard.persist_run_checkpoint('research_objection_completion_unanswered')
            continue
        _flag_unanswered(blackboard, [row for row in _missing_research_replies(blackboard)
                                      if row['objection']['desk'] == desk],
                         "no desk reply after the completion turn")
        saved = deepcopy(blackboard.specialist_checkpoints.get(key) or {})
        seal = saved.pop('sha256', None)
        if (seal != _checkpoint_digest(saved) or saved.get('status') != 'complete'
                or saved.get('pending_tools') or saved.get('inflight_tools')
                or saved.get('usage_unknown') is not False):
            raise RuntimeError('Research reply completion checkpoint is not complete: ' + desk)
        descriptor.update(complete=True, checkpoint_sha256=seal)
        blackboard.persist_run_checkpoint('research_objection_completion_complete')


RED_INADMISSIBLE_REASON = "obiezione inammissibile: fonte fuori dal dossier sigillato"


def _admit_red_objections(blackboard, review):
    """06/10 (run Trade Idea del PM): un riferimento fuori dal dossier non uccide piu' la run.

    Le obiezioni che citano un id assente dal catalogo sigillato diventano obiezioni
    INAMMISSIBILI dichiarate (memo, Capo, controlli); si prosegue con le ammissibili.
    Se nessuna e' ammissibile la critica del Red Team e' una lacuna dichiarata: il
    controllo red_team_complete resta falso, quindi niente proposta operativa.
    """
    allowed = _review_source_ids(blackboard)
    admissible, inadmissible = [], []
    for row in review['objections']:
        outside = sorted(set(row['evidence_refs']) - allowed)
        if outside:
            inadmissible.append({'objection': deepcopy(row), 'outside_refs': outside,
                                 'reason': RED_INADMISSIBLE_REASON})
        else:
            admissible.append(row)
    blackboard.data.pop('_red_inadmissible_objections', None)
    blackboard.data.pop('_red_team_gap', None)
    if inadmissible:
        blackboard.data['_red_inadmissible_objections'] = inadmissible
        print("  [RED_TEAM] " + str(len(inadmissible)) + " obiezioni inammissibili (fonte fuori dal dossier "
              "sigillato): " + ", ".join(str(item['objection'].get('id')) + " <- " + ", ".join(item['outside_refs'])
                                         for item in inadmissible)[:1500], flush=True)
    if review['objections'] and not admissible:
        blackboard.data['_red_team_gap'] = ("Critica del Red Team non utilizzabile: tutte le "
            + str(len(inadmissible)) + " obiezioni citano fonti fuori dal dossier sigillato")
    return admissible


def _red_inadmissible_gaps(blackboard):
    """Le frasi per data_gaps del memo: obiezioni inammissibili e lacuna Red Team."""
    rows = blackboard.data.get('_red_inadmissible_objections') or []
    gaps = []
    if blackboard.data.get('_red_team_gap'):
        gaps.append(str(blackboard.data['_red_team_gap']) + ": nessuna proposta operativa.")
    if rows:
        gaps.append("Red Team, " + str(len(rows)) + " " + ("obiezione inammissibile" if len(rows) == 1
            else "obiezioni inammissibili") + " (fonte fuori dal dossier sigillato, non discusse dai desk): "
            + "; ".join(str(item['objection'].get('id')) + " cita " + ", ".join(item['outside_refs'])
                        for item in rows)[:900] + ".")
    return gaps


def _committee_quorum(blackboard):
    """Present desks and declared gaps; Fundamentals plus four of six desks are required."""
    gaps = blackboard.data.get("_desk_gaps") or {}
    missing = {}
    for desk in TRADE_IDEA_DESKS:
        report = blackboard.read(desk, 1)
        if desk in gaps:
            missing[desk] = gaps[desk].get("message") or "desk failure"
        elif not isinstance(report, str) or not report.strip() or report.startswith("[ERROR"):
            missing[desk] = "R1 report unavailable"
    present = [desk for desk in TRADE_IDEA_DESKS if desk not in missing]
    if "fundamentals" in missing or len(present) < 4:
        raise RuntimeError("Committee below quorum (Fundamentals and at least 4 of 6 desks required); "
                           "missing: " + ", ".join(f"{desk} ({reason[:160]})" for desk, reason in missing.items()))
    return present, missing


def _quorum_still_reachable(blackboard):
    """Stop before paying further rounds when the committee can no longer reach quorum."""
    gaps = blackboard.data.get("_desk_gaps") or {}
    if "fundamentals" in gaps or len(TRADE_IDEA_DESKS) - len(gaps) < 4:
        raise RuntimeError("Committee below quorum (Fundamentals and at least 4 of 6 desks required); "
                           "declared gaps: " + ", ".join(sorted(gaps)))


def _flag_unanswered(blackboard, rows, reason):
    for row in rows:
        row["state"] = "open"
        row["unanswered"] = reason


def _run_research_committee(blackboard, portfolio, runner, red_runner, check_stop, persist):
    from bellomberg.core.trade_idea_contract import validate_committee_review
    present, missing = _committee_quorum(blackboard) if blackboard.data.get("_research_thesis") is None else (None, None)
    seal_research_thesis(blackboard, desks=present or TRADE_IDEA_DESKS, missing=missing)
    blackboard.model_phase = 'review'
    persist('research_dossier')
    check_stop()
    reference = research_reference(blackboard)
    red = blackboard.get_latest('red_team')
    attestation = blackboard.data.get('_red_research_review') or {}
    reused = bool(red and attestation.get('research_ref') == reference
        and attestation.get('report_sha256') == _plan_digest(red['report']))
    if not reused:
        _run_research_red_team(blackboard, portfolio, red_runner)
    review = validate_committee_review(blackboard.get_latest('red_team')['report'])
    admissible = _admit_red_objections(blackboard, review)
    if not reused or '_objections' not in blackboard.data:
        blackboard.data['_objections'] = [{'objection': row, 'response': None,
            'evidence_refs': [], 'state': 'open', 'model_revision_id': None} for row in admissible]
    blackboard.data['_decisive_questions'] = review['decisive_questions']
    persist('red_team')
    check_stop()
    runner(blackboard, 2)
    check_stop()
    _complete_research_objection_replies(blackboard)
    check_stop()
    _flag_unanswered(blackboard, _missing_research_replies(blackboard),
                     "no desk reply after the completion turn")
    _require_final_research(blackboard)
    blackboard.data['_research_review'] = {'research_ref': reference,
        'red_team': deepcopy(blackboard.data['_red_research_review']),
        'desks': deepcopy(blackboard.data.get('_desk_research_reviews') or {}),
        'objections': deepcopy(blackboard.data['_objections']),
        **({'desk_gaps': deepcopy(blackboard.data['_desk_gaps'])} if blackboard.data.get('_desk_gaps') else {})}
    persist('research_review')


def _run_exact_model_red_team(blackboard, portfolio, runner):
    """A changed generation cannot inherit a critique from its predecessor."""
    from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
    verified = _verified_candidate_valuations(blackboard)
    if len(verified) != 1:
        raise RuntimeError("Red Team requires a single verified candidate workbook")
    fields = ("snapshot_id", "generation_id", "workbook_sha256")
    reference = {key: verified[0][key] for key in fields}
    with blackboard._lock:
        previous = blackboard.get_latest("red_team")
        if previous:
            blackboard.data.setdefault("_red_review_history", []).append({
                "review": deepcopy(previous), "attestation": deepcopy(blackboard.data.get("_red_model_review"))})
        for key in ("red_team", "_red_team", "_red_model_review", "_red_team_native_terminal", "_red_team_citation_correction"):
            blackboard.data.pop(key, None)
    # E7: come sopra, testo dalla fotografia PM_CONSTRAINTS_KEY, mai il DB.
    returned = runner(blackboard, portfolio_data=portfolio, memory_db=None)
    published = blackboard.get_latest("red_team")
    report = (published or {}).get("report")
    current = _verified_candidate_valuations(blackboard)
    terminal = blackboard.data.get("_red_team_native_terminal")
    if terminal is not None and (not isinstance(terminal, dict)
            or terminal.get("stop_reason") == "max_tokens" or terminal.get("text_present") is not True):
        raise RuntimeError("Red Team native response incomplete: stop_reason="
                           + str(terminal.get("stop_reason") if isinstance(terminal, dict) else "invalid")
                           + "; text_present=" + str(terminal.get("text_present") if isinstance(terminal, dict) else "invalid"))
    if (not isinstance(returned, str) or returned != report or not report
            or motivo_critica_non_utilizzabile(report) or len(current) != 1
            or {key: current[0][key] for key in fields} != reference):
        raise RuntimeError("A fresh complete Red Team critique of the current exact workbook is required")
    blackboard.data["_red_model_review"] = {"model_ref": reference, "report_sha256": _plan_digest(report)}
    return report


def _require_final_desk_models(blackboard):
    if is_research_mode(blackboard):
        _require_final_research(blackboard)
        return []
    verified = _verified_candidate_valuations(blackboard)
    if len(verified) != 1:
        raise RuntimeError("A single usable exact final workbook is required before Capo")
    fields = ("snapshot_id", "generation_id", "workbook_sha256")
    reference = {key: verified[0][key] for key in fields}
    rows = blackboard.data.get("_desk_model_reviews") or {}
    for desk in TRADE_IDEA_DESKS:
        row = rows.get(desk) or {}
        report = blackboard.read(desk, 2)
        if (row.get("round") != 2 or row.get("model_ref") != reference
                or not isinstance(report, str) or not report.strip() or report.startswith("[ERROR")
                or row.get("report_sha256") != _plan_digest(report)):
            raise RuntimeError("Final exact-model discussion is missing or stale: " + desk)
    red = blackboard.get_latest("red_team")
    red_attestation = blackboard.data.get("_red_model_review") or {}
    if (not red or red_attestation.get("model_ref") != reference
            or red_attestation.get("report_sha256") != _plan_digest(red["report"])):
        raise RuntimeError("Final exact-model Red Team discussion is missing or stale")
    return verified


def _base_review_source_ids(blackboard):
    ids = {row.get("tool") for row in blackboard.tool_receipts if row.get("success") is True}
    documents = (getattr(blackboard, "source_qualification", {}).get("source_report") or {}).get("documents") or []
    ids.update(doc.get("id") for doc in documents if isinstance(doc, dict))
    return ids - {None}


def review_evidence_catalog(blackboard):
    """Exact current references; desk opinions and derivations are not primary facts."""
    qualification = getattr(blackboard, "source_qualification", {}) or {}
    fingerprint = qualification.get("fingerprint")
    documents = (qualification.get("source_report") or {}).get("documents") or []
    catalog = [{"id": tool, "kind": "retrieved_tool"} for tool in sorted({
        row.get("tool") for row in blackboard.tool_receipts
        if row.get("success") is True and isinstance(row.get("tool"), str)})]
    catalog.extend({"id": doc["id"], "kind": "admitted_document", "title": doc.get("title"),
                    "sha256": doc.get("sha256"), "source_fingerprint": fingerprint}
                   for doc in documents if isinstance(doc, dict) and isinstance(doc.get("id"), str))
    if is_research_mode(blackboard):
        # APERTO-TI: i documenti del ponte Filing sono nel dossier sigillato, quindi citabili; il non
        # verificato porta la sua etichetta e non e' fonte primaria verificata.
        from bellomberg.agents.ponte_filing_dossier import documenti_ponte
        bridge = documenti_ponte(blackboard, getattr(blackboard, "target_ticker", None))
        known = {row["id"] for row in catalog}
        for doc in (bridge or ((), None))[0]:
            fb = doc["metadata"]["filing_bridge"]
            if doc["id"] not in known:
                catalog.append({"id": doc["id"], "kind": "admitted_document", "origin": "filing_archive",
                    "form": doc["metadata"].get("form"), "period": fb.get("periodo"), "sha256": doc.get("sha256"),
                    "source_fingerprint": fingerprint,
                    **({"verified": False, "label": fb.get("etichetta_verifica"),
                        "scope": "Unverified archived issuer document: readable and citable, not verified primary evidence"}
                       if fb.get("verifica") == "non_verificato" else {"verified": True})})
        if blackboard.data.get('_research_thesis') is not None:
            reference = research_reference(blackboard)
            catalog.append({'id': 'research-' + reference['thesis_sha256'], 'kind': 'research_thesis',
                'research_ref': reference, 'scope': 'Independent desk interpretation and assumptions, not primary evidence'})
            catalog.extend({'id': 'desk-' + name + '-' + _plan_digest(report),
                'kind': 'desk_opinion', 'desk': name, 'research_ref': reference,
                'scope': 'Complete R1 desk opinion, not primary financial evidence'}
                for name, report in blackboard.data['_research_thesis']['reports'].items())
        return catalog
    verified = _verified_candidate_valuations(blackboard)
    if len(verified) != 1 or not isinstance(fingerprint, str) or not fingerprint:
        return catalog
    reference = {key: verified[0][key] for key in ("snapshot_id", "generation_id", "workbook_sha256")}
    catalog.append({"id": "model-" + _plan_digest(reference), "kind": "derived_model",
                    "model_ref": reference, "source_fingerprint": fingerprint,
                    "scope": "Current model assumptions, software calculations, exhibits and quality; not primary financial evidence"})
    rows = _current_model_consultations(blackboard)
    for row in rows:
        if (_consultation_received(blackboard, row)
                and _valid_fundamentals_decision(row, row.get("fundamentals_decision"))):
            catalog.append({"id": row["id"], "kind": "desk_opinion", "desk": row["desk"],
                "source_fingerprint": fingerprint, "consultation_row_sha256": _plan_digest(row),
                "answer_sha256": _plan_digest(row["response"]), "model_ref": reference,
                "scope": "Current complete peer answer received by Fundamentals with its explicit decision; analyst opinion, not primary financial evidence"})
    return catalog


def _review_source_ids(blackboard):
    return {row["id"] for row in review_evidence_catalog(blackboard)}


def handle_trade_idea_review_tool(blackboard, desk, name, input_):
    """Append review facts only; engine mutation belongs to the bounded orchestrator."""
    if not isinstance(input_, dict):
        return {"ok": False, "error": "review input must be an object"}
    if is_research_mode(blackboard) and name not in ('read_candidate_source', 'respond_trade_idea_objection'):
        return {'ok': False, 'error': 'Workbook authoring and revision are disabled for this research analysis'}
    if name == "read_candidate_source":
        return _read_candidate_source(blackboard, desk, input_)
    if name == "read_candidate_model_consultation":
        if desk != "fundamentals" or getattr(blackboard, "model_phase", None) != "building":
            return {"ok": False, "error": "Consultation pages belong to Fundamentals model construction"}
        row = next((row for row in blackboard.data.get("_model_consultations", [])
                    if row["id"] == input_.get("consultation_id")), None)
        offset = input_.get("offset")
        if (row is None or row.get("status") != "complete" or type(offset) is not int or offset < 0
                or offset != row.get("answer_read_offset", 0)):
            return {"ok": False, "error": "A completed answer must be read in exact consecutive pages"}
        from bellomberg.agents.specialists.base import _tetto_tool_result
        response = row["response"]
        stop = min(len(response), offset + max(1, _tetto_tool_result() - 1500))
        def answer_page(end):
            return {"ok": True, "consultation_id": row["id"], "offset": offset,
                    "next_offset": end, "total_chars": len(response), "complete": end == len(response),
                    "answer_sha256": _plan_digest(response), "response": response[offset:end]}
        # Quotes/newlines can expand the JSON transport beyond text length.
        low, high = offset, stop
        while low < high:
            midpoint = (low + high + 1) // 2
            if len(json.dumps(answer_page(midpoint), ensure_ascii=False)) + 256 <= _tetto_tool_result():
                low = midpoint
            else:
                high = midpoint - 1
        stop = low
        if stop == offset and offset < len(response):
            return {"ok": False, "error": "Native answer page envelope cannot fit the tool-result limit"}
        row["answer_read_offset"] = stop
        row["author_view_complete"] = stop == len(response)
        _persist_model_build(blackboard)
        return answer_page(stop)
    if name in {"get_candidate_model_inputs", "submit_candidate_model_plan"}:
        result = _handle_candidate_plan_tool(blackboard, desk, name, input_)
        if name == "submit_candidate_model_plan":
            if result.get("ok"):
                blackboard.data.pop("_model_plan_last_error", None)
            else:
                blackboard.data["_model_plan_last_error"] = {
                    key: deepcopy(result[key]) for key in ("error", "issues", "correction") if key in result}
        if result.get("ok") or name == "submit_candidate_model_plan":
            _persist_model_build(blackboard)
        return result
    refs = input_.get("evidence_refs", [])
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        return {"ok": False, "error": "evidence_refs must list retrieved source or tool IDs"}
    unknown = set(refs) - _review_source_ids(blackboard)
    if unknown:
        return {"ok": False, "error": "evidence_refs not retrieved: " + ", ".join(sorted(unknown))}
    with blackboard._lock:
        if name == "respond_trade_idea_objection":
            if blackboard.current_round < 2:
                return {"ok": False, "error": "objection replies require the explicit review round"}
            rows = blackboard.data.get("_objections") or []
            row = next((item for item in rows if item["objection"]["id"] == input_.get("objection_id")), None)
            if row is None or row["objection"]["desk"] != desk:
                return {"ok": False, "error": "objection is absent or addressed to another desk"}
            response, state = input_.get("response"), input_.get("state")
            if not isinstance(response, str) or not response.strip() or state not in ("answered", "conceded", "open"):
                return {"ok": False, "error": "response and declared resolution state required"}
            if state == "answered" and not refs:
                return {"ok": False, "error": "an answered objection requires retrieved evidence"}
            revision_id = input_.get("model_revision_id")
            revision_log = (blackboard.data.get("_model_review") or {}).get("revision_log") or []
            if revision_id is not None and (not isinstance(revision_id, str) or not any(
                    revision.get("id") == revision_id for revision in revision_log)):
                return {"ok": False, "error": "model_revision_id is not an actual explicit model review"}
            history = blackboard.data.setdefault("_objection_history", [])
            history.append({"id": row["objection"]["id"], "previous": dict(row),
                            "round": blackboard.current_round})
            row.update(response=response, evidence_refs=refs, state=state,
                       model_revision_id=revision_id)
            return {"ok": True, "objection_id": row["objection"]["id"], "state": state}
        if name != "review_candidate_model" or desk != "fundamentals":
            return {"ok": False, "error": "only Fundamentals can own an economic model review"}
        if blackboard.current_round < 1:
            return {"ok": False, "error": "model review follows independent initial reconnaissance"}
        current = blackboard.valuation_results.get(blackboard.target_ticker) or {}
        if input_.get("generation_id") != current.get("generation_id"):
            return {"ok": False, "error": "model generation differs from the current candidate"}
        action, rationale = input_.get("action"), input_.get("rationale")
        if action not in ("retain", "revise") or not isinstance(rationale, str) or not rationale.strip():
            return {"ok": False, "error": "model review action and rationale required"}
        changes = input_.get("changes") or {}
        if not isinstance(changes, dict) or set(changes) - {"assumptions", "analysis_context", "method_records"}:
            return {"ok": False, "error": "unsupported model change fields"}
        if action == "revise" and (not changes or not refs):
            return {"ok": False, "error": "a material revision requires changes and retrieved evidence"}
        documents = (getattr(blackboard, "source_qualification", {}).get("source_report") or {}).get("documents") or []
        document_ids = {doc.get("id") for doc in documents if isinstance(doc, dict)}
        if action == "revise" and not set(refs) <= document_ids:
            return {"ok": False, "error": "model revisions require exact qualified document IDs"}
        if type(input_.get("needs_paid_preparation")) is not bool:
            return {"ok": False, "error": "needs_paid_preparation must be an explicit boolean"}
        if action == "retain" and (changes or input_.get("needs_paid_preparation")):
            return {"ok": False, "error": "retain cannot request model mutation or paid preparation"}
        review_id = "model-review-" + uuid.uuid4().hex
        audit = blackboard.data.setdefault("_model_review", {"revision_log": []})
        audit.setdefault("revision_log", []).append({"id": review_id, "actor": desk,
            "action": action, "rationale": rationale,
            "before_generation_id": current.get("generation_id"), "after_generation_id": None,
            "status": "retained" if action == "retain" else "proposed"})
        blackboard.data.setdefault("_revision_requests", []).append({"id": review_id,
            "changes": changes, "evidence_refs": refs,
            "needs_paid_preparation": input_.get("needs_paid_preparation") is True})
        return {"ok": True, "model_revision_id": review_id,
                "status": "retained" if action == "retain" else "queued", "model_changed": False}


def _model_audit(blackboard):
    raw = blackboard.data.get("_model_review") or {}
    fields = ("snapshot_id", "generation_id", "valuation_date", "workbook_sha256")
    return {"initial_refs": [{key: row[key] for key in fields} for row in raw.get("initial_refs", [])],
            "final_refs": [{key: row[key] for key in fields} for row in raw.get("final_refs", [])],
            "revision_log": raw.get("revision_log", []),
            "objections": blackboard.data.get("_objections", [])}


def _finalize_model_review(blackboard, gate, *, output_dir, model_reviser=None):
    from bellomberg.valuation.trade_idea_model import candidate_model_usability
    audit = blackboard.data.get("_model_review") or {}
    log = audit.get("revision_log") or []
    if not log:
        raise RuntimeError("Fundamentals did not explicitly review the initial exact model")
    proposed = [row for row in log if row["status"] == "proposed"]
    for row in proposed:
        later = log[log.index(row) + 1:]
        superseding = [item for item in later if item["before_generation_id"] == row["before_generation_id"]
                       and item["status"] in ("proposed", "retained")]
        if superseding:
            row["status"] = "rejected"
            row["rationale"] += " [Superseded by explicit review " + superseding[-1]["id"] + "]"
    proposed = [row for row in proposed if row["status"] == "proposed"]
    grant = gate.store.get_run(gate.run_id)["run"].get("authorization") or {}
    applied = sum(row["status"] == "applied" for row in log)
    if proposed and "model_revision" not in (grant.get("activities") or []):
        raise RuntimeError("model revision activity was not authorized for this run")
    if applied + len(proposed) > grant.get("max_revision_rounds", 0):
        raise RuntimeError("model revisions exceed the explicit per-run iteration limit")
    for item in proposed:
        request = next(row for row in blackboard.data.get("_revision_requests", []) if row["id"] == item["id"])
        current = blackboard.valuation_results[blackboard.target_ticker]
        if current.get("generation_id") != item["before_generation_id"]:
            item["status"] = "rejected"
            raise RuntimeError("revision refers to a superseded generation; a new explicit review is required")
        proposer = None
        if request["needs_paid_preparation"]:
            proposer, _ = bind_trade_idea_preparer(gate, blackboard.target_ticker, output_dir=output_dir, phase="revision")
            if proposer is None:
                item["status"] = "blocked"
                raise RuntimeError("paid model revision is not authorized")
        if model_reviser is None:
            from bellomberg.valuation.trade_idea_model import revise
            model_reviser = revise
        try:
            revised = model_reviser(current, {**request["changes"], "rationale": item["rationale"],
                "evidence_ids": request["evidence_refs"]}, qualification=blackboard.source_qualification,
                propose=proposer, output_dir=output_dir)
            if revised.get("generation_id") == current.get("generation_id"):
                raise ValueError("a material model revision did not create a generation")
            if candidate_model_usability(revised)["usable"] is not True:
                raise ValueError("revised economic model is incomplete")
            _record_candidate_model(blackboard, revised, specialist="trade-idea-model-revision")
            if not _verified_candidate_valuations(blackboard):
                raise ValueError("revised candidate workbook is not usable")
        except Exception:
            item["status"] = "blocked"
            raise
        item.update(status="applied", after_generation_id=revised["generation_id"])
    audit["final_refs"] = _verified_candidate_valuations(blackboard)
    audit["status"] = "final_ready"
    audit["objections"] = blackboard.data.get("_objections", [])
    return _model_audit(blackboard)


def _committee_model_context(context, current):
    """Read-only review projection; raw author/audit data remains authoritative."""
    view = deepcopy(context)
    def archive_reference(value, reference):
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False,
                               allow_nan=False, separators=(",", ":"))
        return {"status": "archived_reference", "reference": reference,
                "canonical_sha256": _plan_digest(value), "canonical_chars": len(canonical)}
    basis = view["input_basis"]
    if "archived_growth_thesis_history" in basis:
        basis["archived_growth_thesis_history"] = archive_reference(
            basis["archived_growth_thesis_history"], "model_input_basis.archived_growth_thesis_history")
    view["consultation_audit_refs"] = [
        {key: deepcopy(value) for key, value in row.items() if key in (
            "id", "desk", "requester", "status", "source_fingerprint", "draft_sha256",
            "economic_intent_sha256", "consultation_row_sha256", "current",
            "fundamentals_received", "supersedes_failed", "answer_sha256")}
        for row in view["consultations"]]
    view["consultations"] = [row for row in view["consultations"] if row["current"] is True]
    for row in view["consultations"]:
        for key in ("historical_provenance", "recovery_context"):
            if key in row:
                row[key] = archive_reference(row[key], "model_consultations/" + row["id"] + "/" + key)
    for key in ("analytical_quality", "sanity"):
        if key in current:
            view[key] = deepcopy(current[key])
    quality = view.get("analytical_quality")
    if isinstance(quality, dict) and isinstance(quality.get("rows"), list):
        aliases = {"scenario": "scenario", "driver": "driver", "values": "value", "kind": "kind",
                   "source": "source_id", "source_date": "as_of", "valid_until": "valid_until",
                   "metric": "driver", "basis": "accounting_basis", "rationale": "rationale",
                   "entity": "entity", "period": "period", "unit": "unit"}
        records = view.get("method_records") or []
        for row in quality["rows"]:
            if not isinstance(row, dict) or not isinstance(row.get("evidence"), dict):
                continue
            matches = [(index, record) for index, record in enumerate(records)
                       if isinstance(record, dict) and record.get("driver") == row.get("driver")
                       and record.get("scenario") == row.get("scenario")]
            if len(matches) != 1:
                continue
            index, record = matches[0]
            retained = {key: record.get(source) for key, source in aliases.items()}
            if _plan_digest(retained) == _plan_digest(row["evidence"]):
                row["evidence"] = archive_reference(row["evidence"], "method_records[" + str(index) + "]")
                row["evidence"]["status"] = "retained_equivalent_reference"
                quality["evidence_record_field_aliases"] = aliases
    return view


def candidate_model_context(blackboard, *, purpose="author"):
    """Expose consumed drivers and exact exhibits without private filesystem paths."""
    if is_research_mode(blackboard):
        if blackboard.data.get('_research_thesis') is not None:
            view = research_context(blackboard)
        else:
            view = {'analysis_mode': RESEARCH_ANALYSIS_MODE,
                'ticker': blackboard.target_ticker, 'status': 'research_in_progress',
                'instruction': 'Acquire official sources and develop independent assumptions; no workbook is required.'}
        view['review_evidence_catalog'] = review_evidence_catalog(blackboard)
        view['evidence_refs_contract'] = 'Use only literal IDs from review_evidence_catalog; desk opinions are not primary facts.'
        return view
    current = (getattr(blackboard, "valuation_results", {}) or {}).get(blackboard.target_ticker) or {}
    snapshot = current.get("acquisition_snapshot") or {}
    case = snapshot.get("case") or {}
    documents = (getattr(blackboard, "source_qualification", {}).get("source_report") or {}).get("documents") or []
    from bellomberg.valuation.trade_idea_model import candidate_model_usability
    prepared = current.get('preparation') or {}
    annotations = (current.get('analytical_quality') or {}).get('independent_review_annotations')
    qualification = getattr(blackboard, 'source_qualification', {}) or {}
    growth = prepared.get('growth_thesis') if (
        prepared.get('growth_thesis_generation_id') == current.get('generation_id')
        and current.get('generation_id')
        and prepared.get('growth_thesis_source_fingerprint') == qualification.get('fingerprint')
        and qualification.get('fingerprint')) else None
    consultations = []
    try:
        current_ids = {row["id"] for row in _current_model_consultations(blackboard)}
        consultation_error = None
    except (ValueError, KeyError, TypeError) as exc:
        current_ids, consultation_error = set(), str(exc)
    for row in blackboard.data.get("_model_consultations", []):
        view = {key: deepcopy(value) for key, value in row.items()
                if key not in ("tool_receipts", "usage")}
        view.update(consultation_row_sha256=_plan_digest(row), current=row["id"] in current_ids,
                    fundamentals_received=_consultation_received(blackboard, row))
        if row.get("author_view_complete") is not True and "response" in view:
            response = view.pop("response")
            view.update(response_chars=len(response), answer_sha256=_plan_digest(response),
                        instruction="Read the remaining consecutive pages before deciding on this answer")
        consultations.append(view)
    context = {"ticker": blackboard.target_ticker,
        "model_status": "created" if current else "not_created_during_research",
        "model_phase": getattr(blackboard, "model_phase", None),
        "input_basis": {key: deepcopy(value) for key, value in
            (blackboard.data.get("_model_input_basis") or {}).items() if key != "plan"},
        "model_draft_sha256": _plan_digest(blackboard.data.get("_model_input_draft") or {}),
        "consultations": consultations,
        "consultation_error": consultation_error,
        "snapshot_id": current.get("snapshot_id"),
        "generation_id": current.get("generation_id"), "valuation_date": current.get("valuation_date"),
        "method_id": (current.get("valuation_decision") or {}).get("method_id"),
        "model_usability": candidate_model_usability(current),
        "assumptions": case.get("assumptions"), "method_records": case.get("records"),
        "analysis_context": snapshot.get("analysis_context"),
        "growth_thesis": deepcopy(growth),
        "growth_thesis_status": 'current_generation' if growth else prepared.get('growth_thesis_status', 'unavailable'),
        "independent_review_annotations": {
            "basis": "Separate annotations. Statuses inside archived reviews describe their earlier checkpoint; current model_usability remains authoritative.",
            "status": ('unavailable' if annotations in (None, []) else
                'separate_review_annotations_not_PM_economic_approval' if isinstance(annotations, list)
                and all(isinstance(item, dict) for item in annotations) else 'invalid'),
            "items": deepcopy(annotations) if annotations is not None else []},
        "qualified_documents": [{key: document.get(key) for key in
            ("id", "title", "url", "published_at", "sha256")} for document in documents],
        "verified_models": _verified_candidate_valuations(blackboard)}
    if purpose == "committee":
        view = _committee_model_context(context, current)
        view["review_evidence_catalog"] = review_evidence_catalog(blackboard)
        view["evidence_refs_contract"] = ("Use only exact IDs from review_evidence_catalog in evidence_refs. "
            "Do not append descriptions, dates or values to an ID. Keep the evidence kind distinct: "
            "derived_model is a software/assumption reference and desk_opinion is not primary financial evidence. "
            "Unsupported citations remain an empty list, with the gap declared in the analysis.")
        return view
    return context


def _record_candidate_model(blackboard, payload, *, specialist="trade-idea-orchestrator"):
    if not isinstance(payload, dict):
        raise ValueError("common model preparation did not return an object")
    payload = {**payload, "ticker": blackboard.target_ticker}
    registry = getattr(blackboard, "model_registry", None)
    if registry is not None and payload.get("snapshot_id") and payload.get("generation_id"):
        payload = _register_candidate_model(registry, payload)
    payload["request_origin"] = specialist
    from bellomberg.valuation.trade_idea_model import candidate_model_usability
    receipt = {key: payload.get(key) for key in
        ("ticker", "snapshot_id", "generation_id", "valuation_date", "fair_value_base",
         "fair_value_bear", "fair_value_bull", "current_price", "currency", "valuation_usability",
         "analysis_usability", "exposure_analysis", "method",
         "model_values", "model_exhibits", "valuation_decision", "error")}
    blackboard.tool_receipts.append({"tool": "get_valuation", "input": {"ticker": blackboard.target_ticker},
        "source": "common economic engine", "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": candidate_model_usability(payload)["usable"] is True,
        "output": json.dumps(receipt, ensure_ascii=False, default=str), "truncated": False})
    blackboard.record_valuation(blackboard.target_ticker, payload, specialist)
    vault = getattr(blackboard, "artifact_vault_dir", None)
    if vault is not None and candidate_model_usability(payload)["usable"] is True:
        from hashlib import sha256
        from pathlib import Path
        from bellomberg.core.paths import MODELS_DIR, REPORT_DIR
        from bellomberg.reporting.exact_artifacts import preserve_exact_artifacts
        workbook = Path(payload["path"])
        sidecar = workbook.with_suffix(".payload.json")
        rows = [*((blackboard.data.get("_exact_model_archive") or {}).get("receipts") or []),
            {"path": str(workbook), "sha256": payload["workbook_sha256"], "kind": "xlsx"},
            {"path": str(sidecar), "sha256": sha256(sidecar.read_bytes()).hexdigest(), "kind": "model_payload"}]
        preserved = preserve_exact_artifacts(rows, vault,
            allowed_roots=[MODELS_DIR, REPORT_DIR, *blackboard.model_roots])
        blackboard.data["_exact_model_archive"] = {"vault_dir": str(vault), "receipts": preserved}
    return payload


def _model_registry(db_path):
    """Use the already migrated run SQLite; no schema init, Chroma or default DB."""
    from bellomberg.storage.memory_db import MemoryDB
    registry = object.__new__(MemoryDB)
    registry.db_path = str(db_path)
    return registry


def _register_candidate_model(registry, payload):
    # These transport fields are added after immutable economic registration.
    source = {key: value for key, value in payload.items()
              if key not in ("_thesis_saved", "request_origin")}
    thesis_id = registry.save_valuation_thesis(source["ticker"],
        variant_view="Trade Idea common model; explicit desk review follows",
        price=source.get("price"), engine=source.get("engine"),
        valuation_payload=source, reuse_generation=True)
    if type(thesis_id) is not int or thesis_id < 1:
        raise ValueError("candidate model registration failed; immutable snapshot not confirmed")
    saved = registry.get_valuation_snapshot(source["snapshot_id"],
        generation_id=source["generation_id"])
    with registry._conn() as conn:
        link = conn.execute("SELECT 1 FROM valuation_snapshot_links WHERE snapshot_id=? "
            "AND generation_id=? AND thesis_id=?", (source["snapshot_id"],
            source["generation_id"], thesis_id)).fetchone()
    if not saved or not link or any(saved.get(key) != source.get(key) for key in
            ("ticker", "snapshot_id", "generation_id", "workbook_sha256")):
        raise ValueError("candidate model registration does not match its exact generation")
    return {**source, "_thesis_saved": {"thesis_id": thesis_id,
                                      "snapshot_id": source["snapshot_id"]}}


def _prepare_initial_candidate_model(blackboard, qualification, output_dir):
    from bellomberg.valuation.trade_idea_model import prepare
    def no_paid_preparation(*_args, **_kwargs):
        raise RuntimeError("model preparation is not included in the run authorization")
    payload = prepare(qualification, blackboard.valuation_preparer or no_paid_preparation,
                      output_dir=output_dir)
    return _record_candidate_model(blackboard, payload)


def _candidate_risk_metrics(portfolio, ticker, currency):
    """Measure candidate EUR volatility/correlation from exact price and FX series."""
    if not currency:
        return {"status": "unavailable", "reason": "valuta candidato non verificata"}
    try:
        import math
        import pandas as pd
        import yfinance as yf
        from bellomberg.cli.price_updater import data_ticker
        from bellomberg.portfolio.portfolio_risk import historical_eur_returns
        held = [p for p in (portfolio or {}).get("positions", [])
                if p.get("ticker") and p.get("ticker") != ticker]
        if not held:
            raise ValueError("nessuna posizione comparabile nel book")
        tickers = [ticker, *[p["ticker"] for p in held]]
        aliases = {t: data_ticker(t) for t in tickers}
        if len(set(aliases.values())) != len(aliases):
            raise ValueError("alias prezzi non univoci")
        currencies = {ticker: currency}
        for position in held:
            code = position.get("valuta")
            if not isinstance(code, str) or not code.strip():
                raise ValueError("valuta book non disponibile: " + position["ticker"])
            currencies[position["ticker"]] = code
        raw = yf.download(list(aliases.values()), period="1y", progress=False,
                          auto_adjust=True, threads=True)
        closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        if isinstance(closes, pd.Series):
            closes = closes.to_frame()
        prices = pd.DataFrame({symbol: closes[alias] for symbol, alias in aliases.items()
                               if alias in closes.columns})
        if set(prices.columns) != set(tickers):
            raise ValueError("serie prezzo candidato/book mancanti")
        prices = prices.apply(pd.to_numeric, errors="coerce").where(lambda frame: frame > 0)
        candidate_close = prices[ticker].dropna()
        if len(candidate_close) < 101:
            raise ValueError("meno di 101 prezzi candidato validi")
        last_day = pd.Timestamp(candidate_close.index[-1]).date()
        if (datetime.now(timezone.utc).date() - last_day).days > 7:
            raise ValueError("prezzo candidato stale")
        converted, fx_meta = historical_eur_returns(prices, currencies)
        if fx_meta.get("qualified") is not True:
            raise ValueError("FX storico EUR non qualificato: " + str(fx_meta.get("reasons") or fx_meta))
        target_returns = converted[ticker].dropna()
        if len(target_returns) < 100:
            raise ValueError("rendimenti candidato insufficienti")
        vol = float(target_returns.std() * math.sqrt(252) * 100)
        correlations = []
        for position in held:
            peer = position["ticker"]
            pair = converted[[ticker, peer]].dropna()
            if len(pair) < 80:
                raise ValueError("sovrapposizione storica insufficiente: " + peer)
            corr = float(pair[ticker].corr(pair[peer]))
            if not math.isfinite(corr):
                raise ValueError("correlazione non finita: " + peer)
            correlations.append(corr)
        if not math.isfinite(vol) or vol <= 0:
            raise ValueError("volatilita' candidato non valida")
        return {"status": "measured", "vol_annual_pct": round(vol, 3),
                "avg_corr": round(sum(correlations) / len(correlations), 4),
                "as_of": last_day.isoformat(), "observations": len(target_returns),
                "compared_tickers": [p["ticker"] for p in held],
                "source": "yfinance adjusted Close 1y; FX convertito in EUR per serie",
                "fx_conversion": fx_meta}
    except Exception as exc:
        return {"status": "unavailable", "reason": type(exc).__name__ + ": " + str(exc)[:500]}


def _compute_sizing(portfolio, ticker, mandate, *, currency=None,
                    risk_data=None, stress_data=None, candidate_metrics=None):
    from bellomberg.portfolio.sizing_engine import compute_sizing, _settore_di_policy
    from bellomberg.storage.classificazione import carica_veicoli
    from bellomberg.core import mandato_pm
    parameters = mandato_pm.sizing_params(mandate)
    positions = (portfolio or {}).get("positions") or []
    foreign_book = any(str(p.get("valuta") or "").upper() != "EUR" for p in positions)
    held = any(p.get("ticker") == ticker and float(p.get("quantita") or 0) > 0 for p in positions)
    metrics = (candidate_metrics if candidate_metrics is not None else
               {"status": "not_applicable"} if held else _candidate_risk_metrics(portfolio, ticker, currency))
    candidate = {"ticker": ticker}
    if metrics.get("status") == "measured":
        candidate.update(vol_annual_pct=metrics["vol_annual_pct"], avg_corr=metrics["avg_corr"])
    sizing = compute_sizing(portfolio, risk_data=risk_data,
                            stress_data=stress_data, candidates=[] if held else [candidate],
                            parametri=parameters)
    if sizing.get("error"):
        return sizing
    risk_ok = (isinstance(risk_data, dict) and not risk_data.get("error")
               and isinstance(risk_data.get("per_asset"), dict)
               and not (risk_data.get("fx_conversion") or {}).get("local_declared")
               and not risk_data.get("skipped_tickers")
               and (not foreign_book or (risk_data.get("fx_conversion") or {}).get("qualified") is True))
    stress_meta = (stress_data or {}).get("stress_meta") or {}
    stress_fx_valid = (not foreign_book or (
        ((stress_data or {}).get("fx_conversion") or {}).get("qualified") is True
        and (stress_meta.get("fx_conversion") or {}).get("qualified") is True))
    stress_ok = (isinstance(stress_data, dict) and not stress_data.get("error")
                 and stress_data.get("stress_scenario") == "gfc_2008"
                 and stress_data.get("stress_fallback") is False
                 and stress_meta.get("window_loss_pct") is not None
                 and not stress_meta.get("proxied") and not stress_meta.get("zero_filled_days")
                 and stress_fx_valid)
    measurements = {"risk_status": "measured" if risk_ok else "unavailable",
                    "stress_status": "measured" if stress_ok else "unavailable",
                    "stress_reason": ("stress storico del book in valuta locale, FX storico non convertito"
                                      if not stress_fx_valid else None),
                    "candidate_status": metrics.get("status"),
                    "candidate_metrics": metrics}
    if not held and sizing.get("candidates"):
        sector, sector_source = _settore_di_policy(ticker, carica_veicoli())
        row = sizing["candidates"][0]
        row.update(sector_policy=sector, sector_source=sector_source)
        if sector and row.get("class") == "single":
            used = sum(float(p.get("current_eur") or 0) for p in sizing["positions"]
                       if p.get("class") == "single" and p.get("sector_policy") == sector)
            invested = float(sizing["summary"]["invested_capital_eur"])
            row["sector_remaining_eur"] = round(max(0.0, parameters["cap_settore"] * invested - used), 0)
        else:
            row["sector_remaining_eur"] = None
    sizing["_trade_idea_measurements"] = measurements
    return sizing


def _buy_limits(sizing, portfolio, ticker, reasons):
    """Measured BUY/ADD limits of the common engine, or None with the reason.

    Shared by the historical check and the /4 band: estimated volatility,
    correlation or class, unmeasured candidate metrics, a missing sector residual
    or stress budget never become a limit.
    """
    measurements = sizing.get("_trade_idea_measurements") or {}
    positions = [row for row in sizing.get("positions", []) if row.get("ticker") == ticker]
    candidates = [row for row in sizing.get("candidates", []) if row.get("ticker") == ticker]
    capacity = (positions[0].get("remaining_capacity_eur") if positions else
                candidates[0].get("max_add_eur") if candidates else None)
    if capacity is None:
        reasons.append("il motore non ha una capacita' per il ticker")
        return None
    row = positions[0] if positions else candidates[0]
    if row.get("vol_estimated") or row.get("corr_estimated") or row.get("class_fallback"):
        reasons.append("metriche stimate dal motore (volatilita', correlazione o classe di ripiego), non misurate")
        return None
    if not positions:
        metrics = measurements.get("candidate_metrics") or {}
        compared = {p.get("ticker") for p in (portfolio or {}).get("positions") or []
                    if p.get("ticker") != ticker}
        if (measurements.get("candidate_status") != "measured"
                or set(metrics.get("compared_tickers") or []) != compared):
            reasons.append("metriche del candidato non misurate sul book corrente")
            return None
    sector = None
    if not positions and row.get("class") == "single":
        if not row.get("sector_policy") or row.get("sector_remaining_eur") is None:
            reasons.append("settore di policy o residuo di settore non disponibili")
            return None
        sector = Decimal(str(row["sector_remaining_eur"]))
    stress_budget = (sizing["summary"].get("stress_var_budget") or {}).get("additional_capacity_eur")
    if stress_budget is None:
        reasons.append("budget di stress non disponibile")
        return None
    return {"held": bool(positions), "row": row,
            "cash": Decimal(str(sizing["summary"].get("cash_buffer_eur") or 0)),
            "capacity": Decimal(str(capacity)), "sector": sector, "stress": Decimal(str(stress_budget))}


def _mandate_number(value):
    """A finite Decimal from a real number, or None (bool and text are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = Decimal(str(value))
    return number if number.is_finite() else None


def _mandate_pair(value):
    """A mandate interval [min, max] of numbers (mandato_pm "intervallo_pct"), else None."""
    pair = ([_mandate_number(item) for item in value]
            if isinstance(value, (list, tuple)) and len(value) == 2 else [None])
    return None if None in pair or not (0 <= pair[0] <= pair[1] <= 100) else pair


_CAP_FIELD_BY_CLASS = {"single": "cap_single_pct", "veicolo": "cap_veicolo_pct"}


def _mandate_band_rules(mandate, reasons, action, size_class=None):
    """The /4 band rules of one action, read from the CURRENT mandate (never constants).

    Bases as declared in mandato_pm: size_nuova_posizione_pct and cassa_minima_pct are
    "% del capitale" (NAV), posizione_minima_pct "% del NAV", cap_single/cap_veicolo and
    top3_max_pct "% dell'investito", taglio_* "% della posizione". Required fields missing
    or unreadable -> None with the reason; the OPTIONAL fields max_posizioni and
    top3_max_pct may be absent: the rule is then declared not applicable, never defaulted.
    """
    if not isinstance(mandate, dict):
        reasons.append("mandato del PM non disponibile")
        return None
    sizing_rules = mandate.get("sizing") if isinstance(mandate.get("sizing"), dict) else {}
    cash_rules = mandate.get("cassa") if isinstance(mandate.get("cassa"), dict) else {}
    discipline = mandate.get("disciplina") if isinstance(mandate.get("disciplina"), dict) else {}
    rules = {"not_applicable": []}
    if action == "BUY":
        size = sizing_rules.get("size_nuova_posizione_pct")
        if size is None:
            reasons.append("il mandato non definisce la size di una nuova posizione")
            return None
        pair = _mandate_pair(size)
        if pair is None or pair[0] <= 0:
            reasons.append("size nuova posizione del mandato illeggibile: " + repr(size)[:80])
            return None
        rules.update(new_min_pct=pair[0], new_max_pct=pair[1])
        raw = sizing_rules.get("max_posizioni")
        if raw is None:
            rules["not_applicable"].append("max_posizioni (non definito nel mandato)")
        elif isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            reasons.append("max_posizioni del mandato illeggibile: " + repr(raw)[:40])
            return None
        else:
            rules["max_positions"] = raw
    if action in ("BUY", "ADD"):
        raw_minimum = cash_rules.get("cassa_minima_pct")
        if raw_minimum is None:
            reasons.append("il mandato non definisce la cassa minima")
            return None
        cash_minimum = _mandate_number(raw_minimum)
        if cash_minimum is None or not (0 <= cash_minimum <= 100):
            reasons.append("cassa minima del mandato illeggibile: " + repr(raw_minimum)[:80])
            return None
        rules["cash_minimum_pct"] = cash_minimum
        raw = sizing_rules.get("top3_max_pct")
        if raw is None:
            rules["not_applicable"].append("top3_max_pct (non definito nel mandato)")
        else:
            top3 = _mandate_number(raw)
            if top3 is None or not (0 < top3 < 100):
                reasons.append("top3_max_pct del mandato illeggibile: " + repr(raw)[:40])
                return None
            rules["top3_pct"] = top3
    if action == "ADD":
        field = _CAP_FIELD_BY_CLASS.get(size_class)
        if field is None:
            reasons.append("classe di sizing della posizione non riconosciuta: " + repr(size_class)[:40])
            return None
        cap = _mandate_number(sizing_rules.get(field))
        if cap is None or not (0 < cap < 100):
            reasons.append("peso massimo per posizione (" + field + ") del mandato assente o illeggibile")
            return None
        rules["position_cap_pct"] = cap
    if action in ("TRIM", "SELL"):
        free = _mandate_pair(discipline.get("taglio_max_senza_condizioni_pct"))
        switches = discipline.get("condizioni_taglio_oltre")
        if free is None or not isinstance(switches, dict) or any(
                not isinstance(value, bool) for value in switches.values()):
            reasons.append("regole di taglio del mandato (taglio_max_senza_condizioni_pct, "
                           "condizioni_taglio_oltre) assenti o illeggibili")
            return None
        rules["free_cut_pct"] = free[1]
        rules["cut_conditions"] = sorted(name for name, on in switches.items() if on)
    raw_position = sizing_rules.get("posizione_minima_pct")
    position_minimum = _mandate_number(raw_position)
    if position_minimum is None or not (0 <= position_minimum <= 100):
        reasons.append("posizione minima del mandato assente o illeggibile: " + repr(raw_position)[:80])
        return None
    rules["position_minimum_pct"] = position_minimum
    return rules


def _book_value(row):
    """EUR value of a book row (MemoryDB.get_portfolio_summary shape), or None.

    Non-EUR rows carry valore_mercato_eur; EUR rows carry only valore_mercato (the
    summary leaves valore_mercato_eur None), which is then already in EUR.
    """
    value = row.get("valore_mercato_eur")
    if value is None and str(row.get("valuta") or "").upper() == "EUR":
        value = row.get("valore_mercato")
    return _mandate_number(value)


def _held_value(portfolio, ticker, reasons):
    """Current EUR value of a held position, only at a qualified price and FX."""
    rows = [row for row in (portfolio or {}).get("positions") or [] if row.get("ticker") == ticker]
    if len(rows) != 1:
        reasons.append("posizione non trovata (o duplicata) nel book")
        return None
    row = rows[0]
    value = _book_value(row)
    currency = str(row.get("valuta") or "").upper()
    fx = _mandate_number(row.get("fx_to_eur"))
    if (row.get("price_stale") or value is None or value <= 0 or not currency
            or (currency != "EUR" and (row.get("fx_source") != "live" or fx is None or fx <= 0))):
        reasons.append("posizione non valorizzata a prezzo corrente e FX qualificati")
        return None
    return value


def _top3_limits(portfolio, ticker, current, invested, top3_pct, reasons):
    """(minimum, maximum) addition keeping the three largest weights within top3_max_pct.

    Weights on invested capital (mandate base): after adding d to the ticker,
    top3 = max(S3, S2 + current + d) over invested + d, where S3/S2 are the sums of the
    three/two largest OTHER positions. Both pieces are linear: the bound is exact.
    """
    others = []
    for row in (portfolio or {}).get("positions") or []:
        if row.get("ticker") == ticker:
            continue
        value = _book_value(row)
        if value is None:
            reasons.append("valore di una posizione del book non disponibile per la regola top3")
            return None
        others.append(value)
    others.sort(reverse=True)
    share = top3_pct / Decimal(100)
    s3, s2 = sum(others[:3], Decimal(0)), sum(others[:2], Decimal(0))
    low = max(Decimal(0), (s3 - share * invested) / share)
    high = (share * invested - s2 - current) / (1 - share)
    return low, high


def _sizing_band(sizing, portfolio, ticker, reasons=None, *, mandate, action="BUY"):
    """/4 server band for one action on the accepted ticker (PM 04/10: every mandate rule).

    NAV = engine invested capital + book cash, as in the engine's tail budgets.
    BUY (new position; the mandate wins): band_min = mandate new-position minimum x NAV;
      band_max = min(new-position maximum x NAV, cash above the mandate's minimum cash
      (rounded down), engine capacity, sector residual, stress budget, top3 room). None
      when the book already holds max_posizioni names.
    ADD (held): band_min = mandate minimum position x NAV; band_max = min(class cap room on
      invested capital, engine remaining capacity, cash above the minimum, stress, top3).
    TRIM: band_min = minimum position x NAV; band_max = taglio_max_senza_condizioni (upper)
      x current value, unless no cut condition is switched on in the mandate.
    SELL: the whole current value. With cut conditions switched on, the band carries
      conditions_required: the server cannot verify them, the proposal goes to research.
    The engine starter is information only. An empty band (min > max) is returned as
    such. None, with the reason in ``reasons``, when a measure or a required mandate
    field is missing, or when a BUY/ADD maximum is below the mandate's minimum position.
    """
    from decimal import ROUND_CEILING as _UP, ROUND_FLOOR as _DOWN
    reasons = [] if reasons is None else reasons
    if action not in ("BUY", "ADD", "TRIM", "SELL"):
        reasons.append("nessuna fascia per l'azione " + str(action))
        return None
    if not isinstance(sizing, dict) or sizing.get("error"):
        reasons.append("motore di sizing in errore o assente")
        return None
    if (portfolio or {}).get("fx_incomplete") or (portfolio or {}).get("stale_positions"):
        reasons.append("book con FX incompleto o posizioni stale")
        return None
    measurements = sizing.get("_trade_idea_measurements") or {}
    if measurements.get("risk_status") != "measured" or measurements.get("stress_status") != "measured":
        reasons.append("rischio o stress del book non misurati")
        return None
    buying = action in ("BUY", "ADD")
    if buying and (portfolio or {}).get("cash_source") is None:
        reasons.append("cassa operativa senza fonte nel DB")
        return None
    held_rows = [row for row in sizing.get("positions", []) if row.get("ticker") == ticker]
    if action == "BUY" and held_rows:
        reasons.append("posizione gia' in portafoglio: la fascia di acquisto e' quella ADD")
        return None
    if action != "BUY" and not held_rows:
        reasons.append("l'azione " + action + " richiede una posizione esistente")
        return None
    candidates = [row for row in sizing.get("candidates", []) if row.get("ticker") == ticker]
    row = held_rows[0] if held_rows else (candidates[0] if candidates else {})
    summary = sizing.get("summary") if isinstance(sizing.get("summary"), dict) else {}
    # Engine numbers must be numbers: a string ("4000") is refused, never parsed.
    raw = {"cash_eur": (portfolio or {}).get("cash_disponibile_eur"),
           "invested_capital_eur": summary.get("invested_capital_eur")}
    if buying:
        raw["capacity_eur"] = row.get("remaining_capacity_eur") if held_rows else row.get("max_add_eur")
        raw["stress_budget_eur"] = (summary.get("stress_var_budget") or {}).get("additional_capacity_eur") \
            if isinstance(summary.get("stress_var_budget"), dict) else None
        if not held_rows and row.get("class") == "single":
            raw["sector_remaining_eur"] = row.get("sector_remaining_eur")
    broken = sorted(name for name, value in raw.items() if _mandate_number(value) is None)
    if broken:
        reasons.append("componenti non finiti o assenti: " + ", ".join(broken))
        return None
    try:
        if buying:
            limits = _buy_limits(sizing, portfolio, ticker, reasons)
            if limits is None:
                return None
        elif row.get("vol_estimated") or row.get("corr_estimated") or row.get("class_fallback"):
            reasons.append("metriche stimate dal motore (volatilita', correlazione o classe di ripiego), non misurate")
            return None
    except (InvalidOperation, TypeError, ValueError, KeyError, AttributeError) as exc:
        reasons.append("componenti del motore non numerici: " + type(exc).__name__)
        return None
    rules = _mandate_band_rules(mandate, reasons, action, row.get("class"))
    if rules is None:
        return None
    values = {name: _mandate_number(value) for name, value in raw.items()}
    raw_cash, invested = values["cash_eur"], values["invested_capital_eur"]
    position_value = Decimal(0)
    if action != "BUY":
        position_value = _held_value(portfolio, ticker, reasons)
        if position_value is None:
            return None
    nav = invested + max(Decimal(0), raw_cash)
    if nav <= 0 or invested <= 0:
        reasons.append("capitale (NAV o investito) non positivo")
        return None
    hundred = Decimal(100)
    position_minimum = nav * rules["position_minimum_pct"] / hundred
    components = {"action": action, "nav_eur": float(nav.quantize(Decimal(1))),
                  "position_minimum_eur": float(position_minimum.quantize(Decimal("0.01")))}
    if rules["not_applicable"]:
        components["rules_not_applicable"] = list(rules["not_applicable"])
    if action in ("TRIM", "SELL"):
        value = position_value.quantize(Decimal("0.01"))
        components["position_value_eur"] = float(value)
        conditions = rules["cut_conditions"]
        if action == "TRIM":
            band_min = position_minimum.to_integral_value(rounding=_UP)
            band_max = (value if not conditions else
                        (value * rules["free_cut_pct"] / hundred).quantize(Decimal("0.01"), rounding=_DOWN))
            components["free_cut_pct"] = float(rules["free_cut_pct"]) if conditions else None
        else:
            band_min, band_max = position_minimum.to_integral_value(rounding=_UP), value
        band = {"band_min_eur": float(band_min), "band_max_eur": float(band_max),
                "empty": band_min > band_max, "components": components}
        if conditions and action == "SELL":
            # A cut beyond the free limit needs EVERY switched-on mandate condition.
            band["conditions_required"] = conditions
        return band
    cash_minimum = nav * rules["cash_minimum_pct"] / hundred
    caps = {"cash_above_minimum_eur": (raw_cash - cash_minimum).to_integral_value(rounding=_DOWN),
            "capacity_eur": values["capacity_eur"].to_integral_value(rounding=_DOWN),
            "stress_budget_eur": values["stress_budget_eur"].to_integral_value(rounding=_DOWN)}
    if "sector_remaining_eur" in values:
        caps["sector_remaining_eur"] = values["sector_remaining_eur"].to_integral_value(rounding=_DOWN)
    if action == "BUY":
        held_names = sum(1 for item in (portfolio or {}).get("positions") or []
                         if (_mandate_number(item.get("quantita")) or 0) > 0)
        if "max_positions" in rules and held_names >= rules["max_positions"]:
            reasons.append("il book ha gia' " + str(held_names) + " posizioni, il massimo del mandato ("
                           + str(rules["max_positions"]) + "): nessun nuovo nome")
            return None
        band_min = (nav * rules["new_min_pct"] / hundred).to_integral_value(rounding=_UP)
        caps["mandate_max_eur"] = (nav * rules["new_max_pct"] / hundred).to_integral_value(rounding=_DOWN)
        components["mandate_min_eur"] = float(band_min)
    else:
        band_min = position_minimum.to_integral_value(rounding=_UP)
        share = rules["position_cap_pct"] / hundred
        # Class cap on invested capital after the addition: (v + d) / (I + d) <= cap.
        caps["mandate_room_eur"] = ((share * invested - position_value) / (1 - share)
                                    ).to_integral_value(rounding=_DOWN)
        components["position_value_eur"] = float(position_value.quantize(Decimal("0.01")))
    if "top3_pct" in rules:
        top3 = _top3_limits(portfolio, ticker, position_value, invested, rules["top3_pct"], reasons)
        if top3 is None:
            return None
        low, high = top3
        caps["top3_room_eur"] = high.to_integral_value(rounding=_DOWN)
        if low > 0:
            band_min = max(band_min, low.to_integral_value(rounding=_UP))
            components["top3_min_eur"] = float(low.to_integral_value(rounding=_UP))
    band_max = min(caps.values())
    if band_max <= 0:
        binding = min(caps, key=caps.get)
        reasons.append("fascia nulla: il limite " + binding + " vale " + str(band_max) + " EUR")
        return None
    if band_max < position_minimum:
        reasons.append("massimo della fascia " + str(band_max) + " EUR sotto la posizione minima del mandato ("
                       + str(position_minimum.quantize(Decimal(1))) + " EUR)")
        return None
    starter = _mandate_number(row.get("suggested_starter_eur"))
    components.update({key: float(value) for key, value in caps.items()})
    components.update(cash_eur=float(raw_cash),
                      cash_minimum_eur=float(cash_minimum.quantize(Decimal("0.01"))),
                      engine_starter_eur_info=float(starter) if starter is not None else None)
    if "sector_remaining_eur" not in caps:
        components["sector_remaining_eur"] = None
    return {"band_min_eur": float(band_min), "band_max_eur": float(band_max),
            "empty": band_min > band_max, "components": components}


SIZING_BAND_TOLERANCE_EUR = Decimal("0.01")


def _band_actions(sizing, ticker):
    held = any(row.get("ticker") == ticker for row in (sizing or {}).get("positions", []) or []) \
        if isinstance(sizing, dict) else False
    return ("ADD", "TRIM", "SELL") if held else ("BUY",)


def _sizing_band_block(sizing, portfolio, ticker, mandate, *, text_policy=None):
    """The /4 Capo message block: the band of each admissible action, or its absence."""
    from bellomberg.core import mandato_pm
    mandato_pm._validate_text_policy(text_policy)
    from bellomberg.core.language import text as language_text
    action_label = language_text("soglia minima d'azione del mandato (su NAV)",
                                 "minimum action amount under the mandate (on NAV)")
    bands, missing = {}, {}
    for action in _band_actions(sizing, ticker):
        reasons = []
        band = _sizing_band(sizing, portfolio, ticker, reasons, mandate=mandate, action=action)
        if band is None:
            missing[action] = "; ".join(reasons)
            if text_policy is not None:
                missing[action] = missing[action].replace("posizione minima del mandato", action_label)
        else:
            bands[action] = band
    if not bands:
        text = "; ".join(missing.values()) if len(missing) == 1 else "; ".join(
            action + ": " + reason for action, reason in missing.items())
        return ("SIZING BAND: fascia non disponibile (" + text + "). "
                "Proposta operativa non ammessa: proposal=null, e spiega nella decisione "
                "quale misura manca.")
    notes = ["la fascia vale SOLO per l'azione indicata come chiave: scegli l'azione e copia "
             "band_min_eur e band_max_eur di quell'azione esattamente in proposal.sizing; "
             "components sono informativi"]
    if "BUY" in bands:
        notes.append("BUY: size nuova posizione e cassa minima del mandato, limitate dai limiti misurati "
                     "del motore di sizing e dal peso delle prime tre posizioni")
        if bands["BUY"]["empty"]:
            notes.append("fascia BUY VUOTA: band_min_eur > band_max_eur, l'unica proposta ammessa e' "
                         "band_max_eur, sotto il minimo del mandato, con below_starter=true dichiarato in basis")
    if "ADD" in bands:
        notes.append("ADD: dalla posizione minima del mandato al minimo fra spazio sotto il peso massimo del "
                     "mandato (sull'investito), capacita' del motore, cassa sopra la minima, budget di stress "
                     "e peso delle prime tre posizioni")
    if "TRIM" in bands:
        notes.append("TRIM: importo da band_min_eur a meno del valore della posizione, entro il taglio "
                     "massimo senza condizioni del mandato")
    if "SELL" in bands:
        notes.append("SELL: l'intera posizione, amount_eur = band_max_eur (below_starter=true solo se la "
                     "fascia e' vuota)" + ("; richiede TUTTE le condizioni del mandato " + ", ".join(
                         bands["SELL"]["conditions_required"]) + ", che il server non verifica: una SELL va "
                         "in ricerca, dichiarale nel basis" if bands["SELL"].get("conditions_required") else ""))
    if text_policy is not None:
        notes = [note.replace("posizione minima del mandato", action_label) for note in notes]
    not_applicable = sorted({rule for band in bands.values()
                             for rule in band["components"].get("rules_not_applicable", ())})
    if not_applicable:
        notes.append("regole del mandato non applicabili: " + "; ".join(not_applicable))
    if missing:
        notes.append("non ammesse: " + "; ".join(action + " (" + reason + ")" for action, reason in missing.items()))
    return ("SIZING BAND (server; " + "; ".join(notes) + "):\n"
            + json.dumps(bands, ensure_ascii=False, separators=(",", ":")))


def _proposal_band_action(proposal, sizing):
    """The action whose band applies: a BUY on a held position is an ADD (as routing does)."""
    action = proposal.get("action")
    held = "ADD" in _band_actions(sizing, proposal.get("ticker"))
    return "ADD" if action == "BUY" and held else action


def _sizing_band_reason(result, sizing, portfolio, mandate, accepted_band=None):
    """None when a /4 proposal matches the server band of its action; else the reason.

    accepted_band (price refresh): the copy is compared with the band accepted by the
    Capo, while the amount must stay inside the band re-measured now; the PM's 0.5%
    price tolerance (option A) applies only to the amounts tied to a measured value:
    the minimum of the band and the amounts equal to its maximum (SELL, empty band).
    """
    from bellomberg.core.trade_idea_policy import PRICE_TOLERANCE
    proposal = result.get("proposal")
    if not proposal:
        return "nessuna proposta"
    copied = proposal.get("sizing")
    if not isinstance(copied, dict):
        return "proposta /4 senza fascia copiata dal motore"
    action = _proposal_band_action(proposal, sizing)
    if action not in ("BUY", "ADD", "TRIM", "SELL"):
        return "nessuna fascia del motore per l'azione " + str(action)
    reasons = []
    band = _sizing_band(sizing, portfolio, proposal.get("ticker"), reasons, mandate=mandate, action=action)
    if band is None:
        return "fascia del motore non disponibile per " + action + ": " + "; ".join(reasons)
    if band.get("conditions_required"):
        return ("vendita totale oltre il taglio senza condizioni del mandato: richiede le condizioni "
                + ", ".join(band["conditions_required"]) + ", non verificabili dal server")
    reference = accepted_band if accepted_band is not None else band
    try:
        values = [Decimal(str(proposal.get("eur_amount"))), Decimal(str(copied.get("amount_eur"))),
                  Decimal(str(copied.get("band_min_eur"))), Decimal(str(copied.get("band_max_eur"))),
                  Decimal(str(reference["band_min_eur"])), Decimal(str(reference["band_max_eur"]))]
    except (TypeError, InvalidOperation, KeyError):
        return "importo o fascia copiata non numerici"
    if any(isinstance(item, bool) for item in (proposal.get("eur_amount"), copied.get("amount_eur"))) \
            or not all(value.is_finite() for value in values):
        return "importo o fascia copiata non numerici"
    amount, copied_amount, copied_min, copied_max, reference_min, reference_max = values
    if (abs(copied_min - reference_min) > SIZING_BAND_TOLERANCE_EUR
            or abs(copied_max - reference_max) > SIZING_BAND_TOLERANCE_EUR):
        return ("fascia copiata diversa da quella " + ("accettata" if accepted_band is not None else "del motore")
                + " (copiata " + str(copied_min) + "-" + str(copied_max) + " EUR, server "
                + str(reference_min) + "-" + str(reference_max) + " EUR)")
    band_min, band_max = Decimal(str(band["band_min_eur"])), Decimal(str(band["band_max_eur"]))
    refresh = accepted_band is not None
    tolerance = PRICE_TOLERANCE if refresh else Decimal(0)
    empty = reference["empty"] if refresh else band["empty"]

    def equal_to_max():
        return abs(amount - band_max) <= max(SIZING_BAND_TOLERANCE_EUR, band_max * tolerance)
    below = copied.get("below_starter")
    if below not in (True, False) or amount != copied_amount or amount <= 0:
        return "importo fuori dalla fascia del motore"
    if action == "SELL":
        # The whole position, at the measured value; below_starter only for an empty band.
        if not equal_to_max() or below is not empty:
            return "importo fuori dalla fascia del motore"
        return None
    if below is True:
        if action != "BUY" or not empty or not equal_to_max():
            return "importo fuori dalla fascia del motore"
        return None
    # The tolerance never widens the maximum of an ordinary amount.
    if amount > band_max or (action == "TRIM" and amount >= Decimal(str(
            band["components"]["position_value_eur"]))):
        return "importo fuori dalla fascia del motore"
    if amount < band_min * (1 - tolerance):
        return "importo fuori dalla fascia del motore"
    return None


def _sizing_valid(result, sizing, portfolio, *, policy=None, mandate=None, accepted_band=None):
    proposal = result.get("proposal")
    if not proposal:
        return False
    if policy == EXECUTION_POLICY_V4 or "sizing" in proposal:
        # Only the /4 strict proposal has "sizing": the band check is the whole check
        # (it contains the measured BUY/ADD limits below).
        return _sizing_band_reason(result, sizing, portfolio, mandate, accepted_band) is None
    if (not isinstance(sizing, dict) or sizing.get("error")
            or (portfolio or {}).get("fx_incomplete") or (portfolio or {}).get("stale_positions")):
        return False
    measurements = sizing.get("_trade_idea_measurements") or {}
    if measurements.get("risk_status") != "measured" or measurements.get("stress_status") != "measured":
        return False
    if (portfolio or {}).get("cash_source") is None and proposal["action"] in ("BUY", "ADD"):
        return False
    try:
        amount = Decimal(str(proposal.get("eur_amount")))
    except (TypeError, InvalidOperation):
        return False
    if not amount.is_finite() or amount <= 0:
        return False
    ticker, action = proposal["ticker"], proposal["action"]
    positions = [row for row in sizing.get("positions", []) if row.get("ticker") == ticker]
    cash = Decimal(str(sizing["summary"].get("cash_buffer_eur") or 0))
    if action in ("BUY", "ADD"):
        limits = _buy_limits(sizing, portfolio, ticker, [])
        if limits is None:
            return False
        capacity = limits["capacity"]
        if limits["sector"] is not None:
            capacity = min(capacity, limits["sector"])
        capacity = min(capacity, limits["stress"])
        return amount <= min(cash, capacity)
    if action in ("TRIM", "SELL") and positions:
        if positions[0].get("vol_estimated") or positions[0].get("corr_estimated") or positions[0].get("class_fallback"):
            return False
        return amount <= Decimal(str(positions[0].get("current_eur") or 0))
    return False


_NON_VERIFICATO = re.compile(r'"fonte_primaria_verificata"\s*:\s*false')
_ANNO = re.compile(r"-?(?:19|20)\d\d")


def _numero_intero(n):
    """Il numero n come token intero: ne' cifre/segno prima, ne' cifre (o decimali) dopo.
    R-CASCATA 3a verifica: senza il «-» una perdita -123.45 attestava «Utile 123.45»."""
    return re.compile(r"(?<![\d.,-])" + re.escape(n) + r"(?![\d]|[.,]\d)")


# R-CASCATA: tool la cui ricevuta lega una data solo ai numeri del sotto-oggetto che la contiene
_TOOL_BLOCCO_DATATO = frozenset({"get_fundamentals", "get_financial_history"})


def _bound_evidence_details(result, blackboard, ticker, cutoff):
    """Return exact candidate receipts backing declared evidence IDs."""
    economic_date_keys = {"as_of", "price_asof", "valuation_date", "fiscal_date",
                          "period_end", "reported_at", "filing_date", "observed_at"}

    def economic_dates(output, tool=None):
        """{data economica: frammenti JSON in cui cercare i numeri dell'Evidence}."""
        try:
            parsed = json.loads(output)
        except (TypeError, ValueError):
            return {}
        payload = parsed.get("data", parsed) if isinstance(parsed, dict) else parsed
        if tool in _TOOL_BLOCCO_DATATO:
            # R-CASCATA (07/10/2026): per questi tool una data vale SOLO per i numeri del sotto-oggetto
            # che la contiene (blocco ultimo_periodo_pubblicato, solo se aggiornato; latest_period SEC;
            # un gruppo ESEF datato). Il TTM di yfinance o i ricavi di un anno vecchio della stessa
            # ricevuta non si legano alla data dell'ultimo trimestre. Una data al livello principale data
            # l'intero payload (contratto di prima; le ricevute vere di questi tool non ne hanno).
            gruppi = {}

            def gruppo(node, nome, depth):
                if depth > 6:
                    return
                if isinstance(node, dict):
                    if not (nome == "ultimo_periodo_pubblicato" and node.get("stato") != "aggiornato"):
                        testo = None
                        for key in economic_date_keys:
                            if isinstance(node.get(key), str):
                                testo = testo or (output if depth == 0 else json.dumps(node, ensure_ascii=False))
                                gruppi.setdefault(node[key][:10], []).append(testo)
                    for key, value in node.items():
                        if isinstance(value, (dict, list)):
                            gruppo(value, key, depth + 1)
                elif isinstance(node, list):
                    for value in node[:100]:
                        gruppo(value, nome, depth + 1)

            gruppo(payload, None, 0)
            return gruppi
        dates = set()

        def visit(node, depth):
            if depth > 6:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in economic_date_keys and isinstance(value, str):
                        dates.add(value[:10])
                    elif isinstance(value, (dict, list)):
                        visit(value, depth + 1)
            elif isinstance(node, list):
                for value in node[:100]:
                    visit(value, depth + 1)

        visit(payload, 0)
        return {day: [output] for day in dates}

    used = {ident for section in result.get("dossier", [])
            for ident in section.get("evidence_ids", [])}
    if _is_v4_memo(result, blackboard):
        used |= _v4_field_evidence_ids(result)
    receipts = [row for row in blackboard.tool_receipts if row.get("success")
                and not row.get("truncated")
                and isinstance(row.get("input"), dict)
                and row["input"].get("ticker") == ticker
                # APERTO-TI: la lettura di un documento NON verificato non attesta numeri operativi.
                and not _NON_VERIFICATO.search(str(row.get("output") or ""))]
    model_generations = {reference.get("generation_id") for reference in result.get("valuation_refs") or []}
    if model_generations:
        current_receipts = []
        for receipt in receipts:
            if receipt.get("tool") == "get_valuation":
                try:
                    payload = json.loads(receipt.get("output") or "")
                    payload = payload.get("data", payload)
                    if payload.get("generation_id") not in model_generations:
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
            current_receipts.append(receipt)
        receipts = current_receipts
    bound, numeric = {}, set()
    for evidence in result.get("evidence") or []:
        if evidence["id"] not in used:
            continue
        match = re.match(r"^\[src: ([a-z][a-z0-9_]*)\](?:\s|$)", evidence["source"])
        if not match:
            continue
        tool, observed = match.group(1), str(evidence.get("as_of") or "")[:10]
        try:
            day = datetime.fromisoformat(observed).date()
            if day > datetime.fromisoformat(cutoff.replace("Z", "+00:00")).date():
                continue
        except (ValueError, AttributeError):
            continue
        for receipt in receipts:
            output = receipt.get("output") or ""
            scopes = economic_dates(output, tool) if receipt.get("tool") == tool else {}
            if observed not in scopes:
                continue
            url = evidence.get("url")
            if url and url not in output:
                continue
            numbers = re.findall(r"(?<!\w)-?\d+(?:[.,]\d+)?", evidence.get("summary") or "")
            numbers = [n for n in numbers if len(n) >= 3 or "." in n or "," in n]
            # R-CASCATA 2a verifica: un anno (1900-2100) non e' una misura (sta in ogni period_end) e un
            # numero si confronta INTERO, non come sottostringa («23.45» non e' dentro «123.45»).
            numbers = [n for n in numbers if not _ANNO.fullmatch(n)]
            measured = bool(numbers and any(_numero_intero(n).search(fragment)
                            for n in numbers for fragment in scopes[observed])
                            and re.search(r"(?:%|€|\$|\b(?:EUR|USD|GBP|GBX|bps|pp|azioni|shares)\b)",
                                          evidence["summary"], re.I))
            bound[evidence["id"]] = {"tool": tool, "output": output,
                                     # Keep the raw receipt for historical contracts; /4
                                     # must not re-admit numbers from excluded periods.
                                     # JSON preserves field scales across multiple groups.
                                     "economic_output": json.dumps([json.loads(fragment)
                                         for fragment in scopes[observed]], ensure_ascii=False),
                                     "summary": evidence.get("summary") or ""}
            if measured:
                numeric.add(evidence["id"])
            break
    return bound, numeric


def _bound_evidence(result, blackboard, ticker, cutoff):
    """Conservative receipt binding; this does not assert independent truth."""
    bound, numeric = _bound_evidence_details(result, blackboard, ticker, cutoff)
    tools = {row["tool"] for row in bound.values()}
    numeric_tools = {bound[ident]["tool"] for ident in numeric}
    return (len(bound) >= 3
            and len(numeric) >= 2
            and bool(tools & {"get_price_live", "get_valuation"})
            and bool(tools & {"get_fundamentals", "get_financial_history", "get_valuation"})
            and len(numeric_tools) >= 2)


_QUANTITY = re.compile(
    r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?:\s?(?:%|bps|pp|€|\$|EUR|USD|GBP|GBX|bn|billion|million|m|k))?(?![\w])",
    re.I)



def _is_v4_memo(result, blackboard):
    """A /4 run's Capo memo; the server incomplete package keeps the historical gate."""
    return (execution_policy(blackboard) == EXECUTION_POLICY_V4
            and result.get("result_origin") is None)


def _v4_field_evidence_ids(result):
    ids = set(result.get("summary_evidence_ids") or ()) | set(result.get("pm_view_evidence_ids") or ())
    for key in ("pillars", "variant_view", "risk_exits", "scenarios", "objections"):
        for item in result.get(key) or ():
            ids.update(item.get("evidence_ids") or ())
    return ids


# /4 prose follows the output-language convention (Italian: 1.234,5 and 12,5%), so the
# token keeps every group separator and its sign instead of splitting "1.234,5" into
# two numbers; a multiple ("12,9x") or a scale word is part of the token.
from bellomberg.core.memo_numbers import (
    _MONTHS,
    _QUANTITY_LOCAL,
    _UNIT_SCALE,
    _FIELD_SCALE,
    _OUTPUT_TEXT_NUMBER,
    _TEXT_SCALE,
    _local_number_values,
    _field_scale,
    _output_numbers,
    _number_attested,
    _YEAR_UNITS_V4,
    _quantity_spans_v4)


_CLAUSE_START = re.compile(r"[;:!?\n]|\.\s")
_FILLER = r"(?:\s+(?:a|ad|di|del|dello|della|dei|degli|delle|il|lo|la|i|gli|le|l'|al|allo|alla|ai|agli|alle|" \
          r"quota|livello|soglia|target|obiettivo|prezzo|price|the|of|to|than|a|an|level))*\s*"
# PM 04/10: inside committee criteria only a THRESHOLD or TARGET is exempt.
_THRESHOLD_BEFORE = re.compile(
    r"(?:\b(?:sotto|sopra|oltre|al di sotto|al di sopra|fino|entro|almeno|non oltre|meno|piu'|piu|più|"
    r"super\w*'?|below|above|under|over|at least|up to|beyond|exceed\w*|less|more)|<=|>=|≤|≥|<|>)"
    + _FILLER + r"$", re.I)
_EXIT_IF = re.compile(r"\b(?:uscire|esci|ridurre|riduci|rivedere|rivedi|vendere|vendi|exit|reduce|review|sell)\b"
                      r"[^;:!?\n]*\b(?:se|if|quando|when)\b[^,;:!?\n]*$", re.I)
_TIME_AFTER = re.compile(
    r"\s*(?:giorn[oi]|gg|settiman[ae]|mes[ei]|trimestr[ei]|semestr[ei]|ann[oi]|sedut[ae]|tranche|rat[ae]|"
    r"days?|weeks?|months?|quarters?|years?|sessions?|tranches?|instal+ments?)\b", re.I)
_ACTION_PCT_BEFORE = re.compile(
    r"\b(?:ridurre|riduci|vendere|vendi|comprare|compra|acquistare|acquista|aumentare|aumenta|incrementare|"
    r"eseguire|esegui|tagliare|taglia|reduce|sell|buy|add|trim|increase|execute|cut)\b(?:\s+\S+){0,3}?\s+"
    r"(?:del|dello|della|di|per|by|of)\s*$", re.I)


def _clause_before(text, start):
    head = text[:start]
    cut = 0
    for match in _CLAUSE_START.finditer(head):
        cut = match.end()
    return head[cut:]


def _is_threshold(text, start):
    before = _clause_before(text, start)
    return bool(_THRESHOLD_BEFORE.search(before) or _EXIT_IF.search(before))


def _is_plan(text, start, end, token):
    """A plan figure: a duration, a number of tranches, or the share of an action."""
    if _TIME_AFTER.match(text, end):
        return True
    return token.endswith("%") and bool(_ACTION_PCT_BEFORE.search(_clause_before(text, start)))


def _horizon_exempt(label, months):
    """Spans of horizon.label numbers coherent with horizon.months (18 mesi, 12-24 mesi, 1,5 anni)."""
    exempt = set()
    if not isinstance(months, int) or isinstance(months, bool):
        return exempt

    def in_months(raw, unit):
        try:
            value = Decimal(raw.replace(",", "."))
        except InvalidOperation:
            return None
        return value * 12 if unit.lower().startswith(("ann", "year")) else value
    unit = r"(mes[ei]|months?|ann[oi]|years?)\b"
    for match in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:-|–|a|to)\s*(\d+(?:[.,]\d+)?)\s*" + unit, label, re.I):
        low, high = in_months(match.group(1), match.group(3)), in_months(match.group(2), match.group(3))
        if low is not None and high is not None and low <= months <= high:
            exempt.update((match.start(1), match.start(2)))
    for match in re.finditer(r"(\d+(?:[.,]\d+)?)\s*" + unit, label, re.I):
        if in_months(match.group(1), match.group(2)) == months:
            exempt.add(match.start(1))
    return exempt


def _quantities(text, pattern=None):
    """Detect material numeric tokens, excluding dates and identifiers."""
    scrubbed = re.sub(r"https?://\S+", " ", str(text or ""))
    scrubbed = re.sub(r"\b(?:19|20)\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?\b", " ", scrubbed)
    scrubbed = re.sub(r"\b\d{1,2}\s+(?:" + _MONTHS + r")[a-z]*\s+(?:19|20)\d{2}\b",
                      " ", scrubbed, flags=re.I)
    scrubbed = re.sub(r"\b(?:FY|CY|Q)[ -]?\d{1,4}\b", " ", scrubbed, flags=re.I)
    def year_or_amount(match):
        before = scrubbed[max(0, match.start() - 8):match.start()]
        after = scrubbed[match.end():match.end() + 12]
        if (re.search(r"(?:[$€]|\b(?:USD|EUR|GBP|GBX))\s*$", before, re.I)
                or re.match(r"\s*%|\s*(?:bps|pp|USD|EUR|GBP|GBX|million|billion|bn)\b", after, re.I)):
            return match.group()
        return " "
    scrubbed = re.sub(r"\b(?:19|20)\d{2}\b", year_or_amount, scrubbed)
    scrubbed = re.sub(r"^\s*\d+(?:\.\d+)*[.)]\s+", " ", scrubbed)
    return [match.group().strip() for match in (pattern or _QUANTITY).finditer(scrubbed)]


_PROPOSAL_ROW_FIELDS = ("vol_annual_pct", "avg_corr", "avg_corr_assumed", "avg_corr_book", "beta",
                        "max_position_pct", "current_pct", "current_eur", "max_add_eur",
                        "remaining_capacity_eur", "suggested_starter_eur")


def _proposal_numbers(result, sizing):
    """Numbers a proposal may quote: its own amount and copied band, the server band
    components of its action (when the mandate is known) and the labelled engine
    metrics of THE CANDIDATE only - never the whole engine JSON or other positions."""
    proposal = result.get("proposal") or {}
    copied = proposal.get("sizing") or {}
    numbers = [(value, 0) for value in (_mandate_number(proposal.get("eur_amount")),
               _mandate_number(copied.get("amount_eur")), _mandate_number(copied.get("band_min_eur")),
               _mandate_number(copied.get("band_max_eur"))) if value is not None]
    if not isinstance(sizing, dict) or sizing.get("error"):
        return numbers
    ticker = proposal.get("ticker")
    for row in [*(sizing.get("positions") or []), *(sizing.get("candidates") or [])]:
        if isinstance(row, dict) and row.get("ticker") == ticker:
            numbers.extend((value, 0) for value in (_mandate_number(row.get(name))
                                                    for name in _PROPOSAL_ROW_FIELDS) if value is not None)
    metrics = ((sizing.get("_trade_idea_measurements") or {}).get("candidate_metrics") or {})
    numbers.extend((value, 0) for value in (_mandate_number(metrics.get(name))
                                            for name in ("vol_annual_pct", "avg_corr")) if value is not None)
    return numbers


def _proposal_band_numbers(result, sizing, portfolio, mandate):
    """The numeric components of the server band of the proposal's action, if measurable."""
    proposal = result.get("proposal") or {}
    if not proposal or mandate is None:
        return []
    band = _sizing_band(sizing, portfolio, proposal.get("ticker"), [], mandate=mandate,
                        action=_proposal_band_action(proposal, sizing))
    if band is None:
        return []
    values = [band["band_min_eur"], band["band_max_eur"], *band["components"].values()]
    return [(number, 0) for number in (_mandate_number(value) for value in values) if number is not None]


def _numeric_claim_gaps_v4(result, bound, sizing, language, band_numbers=()):
    """/4 memo: prose carries no tags; each field is bound through its evidence_ids.

    Field ids plus those of its memo section; numbers normalized from the output
    language, with sign and declared scale. Committee estimates are exempt ONLY in
    the fields of the estimate itself (a scenario's analysis/method for its own
    probability and target, a variant_view rationale for its own committee value).
    Criteria (falsifiers, exit thresholds, trigger conditions): only a threshold or
    target ("sotto il 40%", "uscire se ... 600") is exempt; any other number is a claim.
    Plan fields (proposal.timing, horizon.label, decisive_questions, review_triggers.what,
    data_gaps) also admit durations, tranches, the share of an action ("ridurre del 30%")
    and horizon months coherent with horizon.months. review_triggers.price_level is a
    structured committee choice, not a claim.
    """
    sections = {section.get("key"): section for section in result.get("dossier") or []}
    numbers_by_id = {ident: _output_numbers(row["economic_output"]) for ident, row in bound.items()}

    def own(*values):
        return [(Decimal(str(value)), 0) for value in values
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                and Decimal(str(value)).is_finite()]
    gaps = []

    def numbers_for(ids, section=None):
        ids = list(ids or ()) + list((sections.get(section) or {}).get("evidence_ids") or ())
        return [number for ident in ids for number in numbers_by_id.get(ident, ())]

    def check(text, location, ids, section=None, extra=(), mode=None, exempt_starts=()):
        text = str(text or "")
        values = []
        for token, start, end in _quantity_spans_v4(text):
            if start in exempt_starts:
                continue
            if mode in ("criteria", "plan") and _is_threshold(text, start):
                continue
            if mode == "plan" and _is_plan(text, start, end, token):
                continue
            values.append(token)
        if not values:
            return
        numbers = numbers_for(ids, section)
        if not numbers and not extra:
            gaps.append(location + ": cifre senza EvidenceID verificati nel campo o nella sezione")
            return
        pool = [*numbers, *extra]
        for value in values:
            if not _number_attested(value, language, pool):
                gaps.append(location + ": valore " + value + " non attestato dalle evidence_ids del campo")

    check(result.get("summary"), "summary", result.get("summary_evidence_ids"), "executive")
    check(result.get("pm_view_response"), "pm_view_response", result.get("pm_view_evidence_ids"), "pm_view")
    horizon = result.get("horizon") or {}
    label = str(horizon.get("label") or "")
    # Durations in the label are admitted only when coherent with horizon.months.
    check(label, "horizon.label", (), "executive", mode="criteria",
          exempt_starts=_horizon_exempt(label, horizon.get("months")))
    for key, section, mode in (("catalysts", "catalysts", None), ("data_gaps", "decision", "plan"),
                               ("review_conditions", "decision", None),
                               ("decisive_questions", "decision", "plan")):
        for index, value in enumerate(result.get(key) or []):
            check(value, key + "[" + str(index) + "]", (), section, mode=mode)
    for index, item in enumerate(result.get("pillars") or []):
        for field in ("title", "thesis", "evidence", "risk"):
            check(item.get(field), "pillars[" + str(index) + "]." + field, item.get("evidence_ids"), "executive")
    for index, item in enumerate(result.get("variant_view") or []):
        location = "variant_view[" + str(index) + "]"
        if not item.get("evidence_ids"):
            gaps.append(location + ": stima del comitato senza evidence_ids")
        for field in ("metric", "period", "unit"):
            check(item.get(field), location + "." + field, item.get("evidence_ids"), "valuation")
        check(item.get("rationale"), location + ".rationale", item.get("evidence_ids"), "valuation",
              extra=own(item.get("committee")))
        consensus = item.get("consensus")
        if consensus is not None:
            text = format(Decimal(str(consensus)), "f")
            if language != "en":
                text = text.replace(".", ",")
            percent = bool(re.search(r"%|percent|pct|per cento", str(item.get("unit") or ""), re.I))
            if not _number_attested(text + ("%" if percent else ""), language,
                                    numbers_for(item.get("evidence_ids"), "valuation")):
                gaps.append(location + ".consensus: valore " + str(consensus)
                            + " non attestato dalle evidence_ids della riga")
    for index, item in enumerate(result.get("scenarios") or []):
        location = "scenarios[" + str(index) + "]"
        if not item.get("evidence_ids") or not str(item.get("method") or "").strip():
            gaps.append(location + ": stima del comitato senza metodo o evidence_ids")
        estimates = own(item.get("price_target"), item.get("probability_pct"))
        for field in ("analysis", "method"):
            check(item.get(field), location + "." + field, item.get("evidence_ids"), "scenarios", extra=estimates)
        for position, value in enumerate(item.get("drivers") or []):
            check(value, location + ".drivers[" + str(position) + "]", item.get("evidence_ids"), "scenarios")
        for position, value in enumerate(item.get("falsifiers") or []):
            check(value, location + ".falsifiers[" + str(position) + "]", item.get("evidence_ids"),
                  "scenarios", mode="criteria")
    for index, item in enumerate(result.get("risk_exits") or []):
        location = "risk_exits[" + str(index) + "]"
        check(item.get("risk"), location + ".risk", item.get("evidence_ids"), "portfolio_risk")
        check(item.get("threshold"), location + ".threshold", item.get("evidence_ids"), "portfolio_risk",
              mode="criteria")
    for index, item in enumerate(result.get("review_triggers") or []):
        location = "review_triggers[" + str(index) + "]"
        check(item.get("what"), location + ".what", (), "decision", mode="plan")
        check(item.get("condition"), location + ".condition", (), "decision", mode="criteria")
    for index, item in enumerate(result.get("objections") or []):
        for field in ("objection", "response"):
            check(item.get(field), "objections[" + str(index) + "]." + field,
                  item.get("evidence_ids"), "red_team")
    for key, section in sections.items():
        check(section.get("title"), "dossier." + str(key) + ".title", (), key)
        for index, paragraph in enumerate(section.get("paragraphs") or []):
            check(paragraph, "dossier." + str(key) + "[" + str(index) + "]", (), key)
        for index, table in enumerate(section.get("tables") or []):
            location = "dossier." + str(key) + ".table[" + str(index) + "]"
            check(table.get("title"), location + ".title", (), key)
            for column in table.get("columns") or []:
                check(str(column), location + ".columns", (), key)
            for row in table.get("rows") or []:
                for cell in row:
                    check(str(cell), location, (), key)
    proposal = result.get("proposal") or {}
    if proposal:
        # Only the numbers pertinent to this proposal (N5), never other positions.
        extra = [*_proposal_numbers(result, sizing), *band_numbers]
        check(proposal.get("rationale"), "proposal.rationale", (), "decision", extra=extra)
        check(proposal.get("timing"), "proposal.timing", (), "decision", extra=extra, mode="plan")
        check((proposal.get("sizing") or {}).get("basis"), "proposal.sizing.basis", (), "decision", extra=extra)
    return gaps[:20]


def _numeric_claim_gaps(result, blackboard, ticker, cutoff, sizing, *, portfolio=None, mandate=None):
    """Syntactic source gate for DCN; economic/source truth still needs review."""
    bound, _ = _bound_evidence_details(result, blackboard, ticker, cutoff)
    if _is_v4_memo(result, blackboard):
        return _numeric_claim_gaps_v4(result, bound, sizing, getattr(blackboard, "language", "it"),
                                      _proposal_band_numbers(result, sizing, portfolio, mandate))
    by_tool = {}
    for row in bound.values():
        by_tool.setdefault(row["tool"], []).append(row["output"])
    if isinstance(sizing, dict) and not sizing.get("error"):
        by_tool["sizing_engine"] = [json.dumps(sizing, ensure_ascii=False, default=str)]

    def cited_outputs(value):
        outputs = []
        for tool in re.findall(r"\[src:\s*([a-z][a-z0-9_]*)\]", value, re.I):
            outputs.extend(by_tool.get(tool, ()))
        for ident in re.findall(r"\[evidence:\s*([A-Za-z0-9_.-]+)\]", value, re.I):
            row = bound.get(ident)
            if row:
                outputs.append(row["output"])
        return outputs

    def value_seen(value, outputs):
        number = re.match(r"[-+]?\d+(?:[.,]\d+)?", value)
        return bool(number and any(re.search(r"(?<!\d)" + re.escape(number.group()) + r"(?!\d)", output)
                                   for output in outputs))

    def check(text, location, *, table=False):
        units = [text] if table else re.split(r"(?<=[.!?;])\s+|\n+", str(text or ""))
        for unit in units:
            values = _quantities(unit)
            if not values and not table:
                continue
            outputs = cited_outputs(unit)
            if not outputs:
                gaps.append(location + ": fonte numerica/tabella senza receipt o EvidenceID verificati")
                continue
            derived = (bool(re.search(r"\[(?:assumption|ipotesi)\]", unit, re.I))
                       and bool(re.search(r"(?:=|/|\*|\b(?:formula|calcol|derived)\b)", unit, re.I))
                       and any(value_seen(value, outputs) for value in values))
            for value in values:
                if not value_seen(value, outputs) and not derived:
                    gaps.append(location + ": valore " + value + " non attestato dalla fonte citata")

    gaps = []
    for key in ("summary", "pm_view_response"):
        check(result.get(key), key)
    for key in ("pros", "cons", "risks", "catalysts", "invalidation", "data_gaps",
                "review_conditions"):
        for index, value in enumerate(result.get(key) or []):
            check(value, key + "[" + str(index) + "]")
    for index, item in enumerate(result.get("scenarios") or []):
        check(item.get("analysis"), "scenarios[" + str(index) + "]")
    for index, item in enumerate(result.get("objections") or []):
        check(item.get("objection"), "objections[" + str(index) + "].objection")
        check(item.get("response"), "objections[" + str(index) + "].response")
    for section in result.get("dossier") or []:
        for index, paragraph in enumerate(section.get("paragraphs") or []):
            check(paragraph, "dossier." + section["key"] + "[" + str(index) + "]")
        for index, table in enumerate(section.get("tables") or []):
            location = "dossier." + section["key"] + ".table[" + str(index) + "]"
            context = str(table.get("source") or "")
            if not cited_outputs(context):
                gaps.append(location + ": source senza receipt o EvidenceID verificati")
            for row in table.get("rows") or []:
                for cell in row:
                    if _quantities(cell):
                        check(str(cell) + " " + context, location, table=True)
    proposal = result.get("proposal") or {}
    if proposal:
        check(proposal.get("rationale"), "proposal.rationale")
    return gaps[:20]


def _load_market_pack(ref):
    """Riferimento del checkpoint -> pacchetto verificato (None per le run senza pacchetto)."""
    from bellomberg.market_data.trade_idea_market_pack import load_market_pack
    return load_market_pack(ref)


def _preview_quality(run, result, directory, blackboard):
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    from bellomberg.reporting.trade_idea_delivery import _candidate_workbooks
    from bellomberg.core.paths import MODELS_DIR, REPORT_DIR
    from pathlib import Path
    path = Path(directory) / "routing-quality-check.pdf"
    report_run = {**run, "identity": {"ticker": run["ticker"],
        "name": run.get("company_name"), "exchange": run.get("exchange"),
        "currency": run.get("currency")},
        "cutoff": blackboard.data.get("_data_cutoff") or run.get("started_at"),
        "desk_annex": _desk_annex(blackboard.data) if is_research_mode(blackboard) else None,
        "facts": (_memo_facts({"tool_receipts": blackboard.tool_receipts, "data": blackboard.data},
                              blackboard.data.get("_data_cutoff")) if is_research_mode(blackboard) else None),
        # Lotto 3: pacchetto di mercato riletto dal file della run (sha verificato)
        "market_pack": (_load_market_pack(blackboard.data.get("_market_pack"))
                        if is_research_mode(blackboard) else None)}
    checked = {'valuations': []} if is_research_mode(blackboard) else _candidate_workbooks(run["ticker"], blackboard.valuation_generations,
        blackboard.valuation_attempts, [MODELS_DIR, REPORT_DIR, *getattr(blackboard, "model_roots", ())],
        result.get("valuation_refs") or ())
    return build_trade_idea_report(report_run, result, output_path=path,
                                   valuations=checked["valuations"],
                                   language=run["language"])["quality"]


def _fx_receipt(portfolio):
    """A sampled FX observation; the source API does not expose market quote as_of."""
    observed = []
    valid = not (portfolio or {}).get("fx_incomplete") and not (portfolio or {}).get("stale_positions")
    for item in (portfolio or {}).get("positions") or []:
        ticker = item.get("ticker")
        currency = item.get("valuta")
        if not ticker or not currency:
            valid = False
            continue
        if currency.upper() == "EUR":
            continue
        try:
            rate = Decimal(str(item.get("fx_to_eur")))
        except (TypeError, InvalidOperation):
            valid = False
            continue
        source = item.get("fx_source")
        if not rate.is_finite() or rate <= 0 or source != "live":
            valid = False
        observed.append({"ticker": ticker, "currency": currency.upper(),
                         "rate": str(rate), "source": source})
    return {"sampled_at": datetime.now(timezone.utc).isoformat(),
            "market_as_of": None, "observations": sorted(observed,
                key=lambda row: (row["ticker"], row["currency"])), "valid": valid}


def _candidate_quote_receipt(blackboard, ticker):
    """Free read of the common daily-close tool, applying its valuation freshness rule."""
    from bellomberg.agents import chat_tools
    from bellomberg.agents.specialists.base import _trade_idea_tool_receipt_success
    from bellomberg.valuation.market_quote import _freshness

    observed_at = datetime.now(timezone.utc).isoformat()
    envelope = None
    try:
        currency = (blackboard.data.get("_identity") or {}).get("currency")
        if not currency:
            raise ValueError("valuta quotazione non verificata nell'identita' accettata")
        envelope = chat_tools.dispatch("get_price_live", {"ticker": ticker},
                                      caller="trade-idea-quote-revalidation")
        payload = envelope.get("data", envelope) if isinstance(envelope, dict) else None
        if not _trade_idea_tool_receipt_success(envelope, "get_price_live"):
            raise ValueError("tool prezzo non riuscito o incompleto")
        if not isinstance(payload, dict) or payload.get("ticker") != ticker:
            raise ValueError("identita' ticker quotazione non verificata")
        price = Decimal(str(payload.get("px")))
        if not price.is_finite() or price <= 0:
            raise ValueError("prezzo candidato non finito o non positivo")
        day = str(payload.get("price_asof") or "")[:10]
        if _freshness(day, datetime.now(timezone.utc).date().isoformat()) != "ok":
            raise ValueError("quotazione daily close non fresca secondo market_quote")
        source = str(payload.get("source") or "").strip()
        if source != "yfinance daily Close (not an intraday quote)":
            raise ValueError("fonte daily close non attestata")
        receipt = {"status": "ready", "ticker": ticker, "price": str(price),
                   "price_asof": day, "source": source, "sampled_at": observed_at,
                   "currency": currency, "currency_basis": "accepted_identity"}
    except Exception as exc:
        receipt = {"status": "unavailable", "ticker": ticker, "sampled_at": observed_at,
                   "reason": type(exc).__name__ + ": " + str(exc)[:350]}
    output = json.dumps(envelope, ensure_ascii=False, default=str) if envelope is not None else ""
    blackboard.tool_receipts.append({"tool": "get_price_live", "input": {"ticker": ticker},
        "source": envelope.get("_source") if isinstance(envelope, dict) else None,
        "timestamp": observed_at, "success": receipt["status"] == "ready",
        "output": output[:200000], "truncated": len(output) > 200000})
    blackboard.tool_log.append({"specialist": "trade-idea-orchestrator", "round": 3,
        "tool": "get_price_live", "input": ticker, "time": observed_at,
        "output": output[:400], "output_tappato": len(output) > 400,
        "output_chars": len(output)})
    return receipt


def _candidate_quote_matches(initial, final):
    # Identity, source and currency are exact; the observed price may move within
    # the PM's 0.5% tolerance (a later quote then carries its own as-of).
    from bellomberg.core.trade_idea_policy import within_price_tolerance
    return bool(initial and final and initial.get("status") == "ready"
                and final.get("status") == "ready"
                and all(initial.get(key) == final.get(key)
                        for key in ("ticker", "source", "currency", "currency_basis"))
                and (initial.get("price") == final.get("price") and initial.get("price_asof") == final.get("price_asof")
                     or within_price_tolerance(initial.get("price"), final.get("price"))))


def _fx_observations_match(initial, final):
    from bellomberg.core.trade_idea_policy import within_price_tolerance
    old, new = list(initial or []), list(final or [])
    return len(old) == len(new) and all(
        a.get("ticker") == b.get("ticker") and a.get("currency") == b.get("currency")
        and a.get("source") == b.get("source") and within_price_tolerance(a.get("rate"), b.get("rate"))
        for a, b in zip(old, new))


def _measure_operational_risk(portfolio, *, risk_loader=None, stress_loader=None,
                              default_risk_db_matches=False):
    """Existing strict EUR risk/stress path, shared by initial and final measurements."""
    try:
        if risk_loader is None:
            if not default_risk_db_matches:
                raise RuntimeError("rischio standard usa il DB globale; loader isolato richiesto")
            from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
            risk_data = compute_portfolio_risk(force=True, strict_eur=True)
        else:
            risk_data = risk_loader()
    except Exception as exc:
        risk_data = {"error": type(exc).__name__ + ": " + str(exc)}
    try:
        if stress_loader is None:
            if not default_risk_db_matches:
                raise RuntimeError("stress standard usa il DB globale; loader isolato richiesto")
            from bellomberg.portfolio.portfolio_montecarlo import run_monte_carlo
            currencies, values = {}, {}
            for position in portfolio.get("positions") or []:
                ticker = position.get("ticker")
                currency = position.get("valuta")
                value = position.get("valore_mercato_eur")
                if not ticker or not currency or value is None or isinstance(value, bool):
                    raise ValueError("stress Trade Idea: exact ticker, currency and EUR value required")
                currencies[ticker] = currency
                values[ticker] = float(value)
            invested = sum(values.values())
            if invested <= 0 or any(value < 0 for value in values.values()):
                raise ValueError("stress Trade Idea: positive verified book NAV required")
            stress_data = run_monte_carlo(horizon_days=252, n_sims=1000,
                method="block_bootstrap", stress_scenario="gfc_2008", seed=7,
                force_refresh=True, return_currencies=currencies,
                _override_weights={ticker: value / invested for ticker, value in values.items()},
                _override_nav=invested)
        else:
            stress_data = stress_loader()
    except Exception as exc:
        stress_data = {"error": type(exc).__name__ + ": " + str(exc)}
    return risk_data, stress_data


def _refresh_accepted_band(run, result, sizing, portfolio, mandate):
    """/4: the band the Capo copied for its action, measured on the accepted inputs."""
    proposal = result.get("proposal") or {}
    if execution_policy(run) != EXECUTION_POLICY_V4 or not proposal:
        return None
    return _sizing_band(sizing, portfolio, run["ticker"], [], mandate=mandate,
                        action=_proposal_band_action(proposal, sizing))


def _final_price_refresh_verification(store, run_id, result, *, portfolio_loader=None,
        risk_loader=None, stress_loader=None, mandate, candidate_metrics_loader=None,
        candidate_quote, default_risk_db_matches=False, accepted_band=None):
    """Reassess the unchanged proposal on current prices after the Capo's conclusion.

    Historical research and the accepted context stay immutable. Failed or
    racing observations remain explicit and cannot authorize Decisions.
    """
    measurements = {key: False for key in ("portfolio_reloaded", "risk_recomputed",
        "stress_recomputed", "sizing_recomputed", "candidate_price_verified", "fx_verified", "proposal_valid")}
    receipt = {"version": 1, "run_id": run_id, "before": None, "after": None,
               "measurements": measurements, "evidence": {}}
    try:
        receipt["before"] = store.price_refresh_context(run_id)
        if portfolio_loader is None:
            if not default_risk_db_matches:
                raise RuntimeError("DB alternativo: loader portafoglio isolato richiesto")
            from bellomberg.storage.memory_db import MemoryDB
            portfolio = MemoryDB(db_path=store.db_path).get_portfolio_summary()
        else:
            portfolio = portfolio_loader()
        measurements["portfolio_reloaded"] = (isinstance(portfolio, dict)
            and bool(portfolio.get("positions")) and not portfolio.get("error")
            and not portfolio.get("stale_positions") and not portfolio.get("fx_incomplete"))
        risk, stress = _measure_operational_risk(portfolio, risk_loader=risk_loader,
            stress_loader=stress_loader, default_risk_db_matches=default_risk_db_matches)
        currency = (candidate_quote or {}).get("currency")
        metrics = (candidate_metrics_loader(portfolio, result["ticker"], currency)
                   if candidate_metrics_loader is not None else None)
        sizing = _compute_sizing(portfolio, result["ticker"], mandate, currency=currency,
                                risk_data=risk, stress_data=stress, candidate_metrics=metrics)
        measured = sizing.get("_trade_idea_measurements") or {}
        measurements["risk_recomputed"] = measured.get("risk_status") == "measured"
        measurements["stress_recomputed"] = measured.get("stress_status") == "measured"
        measurements["sizing_recomputed"] = not sizing.get("error") and bool(measured)
        # /2-/3 call unchanged; a /4 proposal needs the mandate and the accepted band.
        v4_proposal = "sizing" in (result.get("proposal") or {})
        measurements["proposal_valid"] = _sizing_valid(result, sizing, portfolio, **(
            {"mandate": mandate, "accepted_band": accepted_band} if v4_proposal else {}))
        measurements["candidate_price_verified"] = bool(candidate_quote
            and candidate_quote.get("status") == "ready" and candidate_quote.get("ticker") == result["ticker"])
        fx = _fx_receipt(portfolio)
        measurements["fx_verified"] = fx["valid"] is True
        receipt["evidence"] = deepcopy({"portfolio": portfolio, "risk": risk, "stress": stress,
            "sizing": sizing, "candidate_quote": candidate_quote, "fx_receipt": fx,
            "proposal": result.get("proposal")})
        receipt["after"] = store.price_refresh_context(run_id)
        fields = ("accepted_context_sha256", "current_context_sha256", "price_inputs_sha256")
        measurements["context_stable"] = all(receipt["before"].get(key) == receipt["after"].get(key) for key in fields)
        if not measurements["context_stable"]:
            receipt["reason"] = "Portfolio inputs changed during final price/risk/sizing verification"
        # Reject unrepresentable measurements rather than substitute invented values.
        receipt["evidence_sha256"] = _plan_digest(receipt["evidence"])
    except Exception as exc:
        measurements["context_stable"] = False
        receipt["reason"] = type(exc).__name__ + ": " + str(exc)[:1000]
        receipt["evidence"] = _diagnostic_json_value(receipt["evidence"])
        receipt["evidence_sha256"] = _plan_digest(receipt["evidence"])
    return receipt


def _sizing_band_receipt(result, sizing, portfolio, mandate):
    """/4 routing receipt: ok, or the reason the proposal is not operational."""
    if not result.get("proposal"):
        return {"status": "not_applicable", "reason": "nessuna proposta operativa"}
    reason = _sizing_band_reason(result, sizing, portfolio, mandate)
    return {"status": "ok", "reason": None} if reason is None else {"status": "rejected", "reason": reason}


def _operational_checks(run, result, blackboard, sizing, portfolio, mandate,
                        decision_context, report_quality, *, fx_initial=None, fx_final=None):
    from bellomberg.core import mandato_pm
    from bellomberg.core.trade_idea_contract import DOSSIER_KEYS
    from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
    verified = _verified_candidate_valuations(blackboard)
    research_verified = (bool(_require_final_research(blackboard)) and not result.get('valuation_refs')) if is_research_mode(blackboard) else False
    red = blackboard.get_latest("red_team")
    try:
        mandate_valid = mandato_pm.impronta(mandato_pm.carica()) == mandato_pm.impronta(mandate)
    except Exception:
        mandate_valid = False
    return {
        "identity_verified": bool(run.get("company_name") and run.get("exchange") and run["ticker"] == result["ticker"]),
        "evidence_sufficient": (_quality_sufficient(report_quality)
            and not blackboard.data.get("_desk_gaps")
            # PM 06/10: contano solo le inammissibili MATERIALI; le non materiali restano dichiarate.
            and not any((row.get("objection") or {}).get("material")
                        for row in blackboard.data.get("_red_inadmissible_objections") or [])
            and not any(row.get("unanswered") and row["objection"].get("material")
                        for row in blackboard.data.get("_objections") or [])
            and {section["key"] for section in result.get("dossier", [])} >= set(DOSSIER_KEYS)
            and not blackboard.data.get("_numeric_claim_gaps")
            and _candidate_quote_matches(blackboard.data.get("_candidate_quote_initial"),
                                         blackboard.data.get("_candidate_quote_final"))
            and _bound_evidence(result, blackboard, run["ticker"],
                                blackboard.data.get("_data_cutoff") or run["started_at"])),
        "red_team_complete": bool(red and not motivo_critica_non_utilizzabile(red.get("report"))
                                  and "CRITICA TRONCATA" not in red.get("report", "")
                                  and not blackboard.data.get("_red_team_gap")),
        "capo_valid": True,
        "mandate_valid": mandate_valid,
        "sizing_valid": _sizing_valid(result, sizing, portfolio, policy=execution_policy(run), mandate=mandate),
        **({"sizing_band": _sizing_band_receipt(result, sizing, portfolio, mandate)}
           if execution_policy(run) == EXECUTION_POLICY_V4 else {}),
        **({'research_reviewed': research_verified} if is_research_mode(blackboard) else
           {"valuation_checked": bool(verified) and _valuation_refs_match(result, verified)}),
        "history_context_sent": bool(decision_context and decision_context.get("ticker") == run["ticker"]),
        "candidate_price_revalidated": _candidate_quote_matches(
            blackboard.data.get("_candidate_quote_initial"),
            blackboard.data.get("_candidate_quote_final")),
        "fx_revalidated": bool(fx_initial and fx_final and fx_initial["valid"]
                               and fx_final["valid"]
                               and _fx_observations_match(fx_initial["observations"], fx_final["observations"])),
    }


def deliver_trade_idea(store, run_id, *, valuation_results=None, valuation_attempts=(),
                       output_dir=None, prepare=None, send=None, send_email=True):
    """Serialize native artifact recovery across workers; never call an LLM."""
    from pathlib import Path
    from bellomberg.core.paths import REPORT_DIR
    from bellomberg.storage.trade_idea_store import RunConflict
    directory = Path(output_dir) if output_dir is not None else REPORT_DIR / "trade_ideas" / run_id
    try:
        with exclusive_paid_run(directory / ".delivery.lock"):
            return _deliver_trade_idea(store, run_id, valuation_results=valuation_results,
                valuation_attempts=valuation_attempts, output_dir=directory,
                prepare=prepare, send=send, send_email=send_email)
    except PaidRunBusy as exc:
        raise RunConflict("Trade Idea delivery recovery already owned by another worker") from exc


def _deliver_trade_idea(store, run_id, *, valuation_results=None, valuation_attempts=(),
                        output_dir=None, prepare=None, send=None, send_email=True):
    """Recover package, persist its hash, claim SMTP once, then persist outcome."""
    from hashlib import sha256
    from pathlib import Path
    from bellomberg.core.paths import MODELS_DIR, REPORT_DIR
    from bellomberg.reporting.trade_idea_delivery import (
        prepare_trade_idea_delivery, recover_trade_idea_delivery,
        send_trade_idea_delivery, assess_trade_idea_completion, preserve_trade_idea_artifacts,
        verify_exact_artifact_receipts, validate_research_package_scope)
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    from bellomberg.reporting.valuation_delivery import save_manifest as save_file_manifest
    from bellomberg.reporting.exact_artifacts import restore_exact_artifacts
    detail = store.get_run(run_id)
    if detail.get("manifest_integrity") is False:
        raise RuntimeError("Stored delivery manifest checksum differs; no artifact recovery attempted")
    run, result = detail["run"], detail["result"]
    if run.get("phase") == "review_required":
        raise RuntimeError("research refresh requires a new review; the historical package cannot be delivered as current")
    if result is None:
        store.block_delivery(run_id, reason="Nessun risultato strutturato: materiali parziali nel progresso")
        return store.get_run(run_id)["email"]
    directory = Path(output_dir) if output_dir is not None else REPORT_DIR / "trade_ideas" / run_id
    run_for_report = {**run, "identity": {"ticker": run["ticker"],
        "name": run.get("company_name"), "exchange": run.get("exchange"),
        "currency": run.get("currency")},
        "cutoff": (detail.get("progress") or {}).get("data_cutoff") or run.get("started_at"),
        "desk_annex": (_desk_annex((((detail.get("progress") or {}).get("checkpoint") or {}).get("data")))
                       if run.get("analysis_mode") == RESEARCH_ANALYSIS_MODE else None),
        "facts": (_memo_facts(((detail.get("progress") or {}).get("checkpoint") or {}),
                              (detail.get("progress") or {}).get("data_cutoff"))
                  if run.get("analysis_mode") == RESEARCH_ANALYSIS_MODE else None),
        "market_pack": (_load_market_pack(((((detail.get("progress") or {}).get("checkpoint") or {})
                                             .get("data") or {}).get("_market_pack")))
                        if run.get("analysis_mode") == RESEARCH_ANALYSIS_MODE else None)}
    persisted_manifest = detail.get("artifacts") is not None
    if valuation_results is None:
        progress = detail.get("progress") or {}
        valuation_results = progress.get("valuation_generations") or progress.get("valuation_results")
        if isinstance(valuation_results, dict):
            valuation_results = list(valuation_results.values())
        if not valuation_attempts:
            valuation_attempts = progress.get("valuation_attempts") or ()
    if is_research_mode(run) and (valuation_results or valuation_attempts or result.get('valuation_refs')):
        raise ValueError('Research-only delivery contains unexpected model activity')
    if isinstance(valuation_results, list):
        final_generations = {row["generation_id"] for row in result.get("valuation_refs") or []}
        valuation_results = [row for row in valuation_results if row.get("generation_id") in final_generations]
    recovery_roots = [MODELS_DIR, REPORT_DIR, directory,
                      *((detail.get("progress") or {}).get("checkpoint") or {}).get("model_roots", ())]
    def restore_package(candidate):
        if is_research_mode(run) != is_research_mode(candidate):
            raise ValueError('Delivery analysis mode differs from the accepted run')
        if execution_policy(run) != execution_policy(candidate):
            raise ValueError('Delivery policy differs from the accepted run')
        if is_research_mode(run):
            validate_research_package_scope(candidate)
        rows = candidate.get("exact_artifact_receipts")
        if rows:
            verify_exact_artifact_receipts(candidate)
            if (candidate.get("run_id") != run_id or candidate.get("ticker") != run["ticker"]
                    or candidate.get("result_sha256") != sha256(json.dumps(
                        result, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()):
                raise ValueError("Exact artifact recovery belongs to another analysis")
            restore_exact_artifacts(rows, directory / ".exact-artifacts", allowed_roots=recovery_roots)
    if persisted_manifest:
        manifest = detail["artifacts"]
        restore_package(manifest)
    elif (directory / "delivery.json").exists():
        candidate = json.loads((directory / "delivery.json").read_text(encoding="utf-8"))
        restore_package(candidate)
        manifest = recover_trade_idea_delivery(directory / "delivery.json",
            run_id=run_id, ticker=run["ticker"], result=result)
    else:
        manifest = (prepare or prepare_trade_idea_delivery)(
            run_for_report, result, valuation_results=valuation_results,
            valuation_attempts=valuation_attempts, output_dir=directory,
            model_roots=[MODELS_DIR, REPORT_DIR, directory], language=run["language"])
    expected_refs = {(row["snapshot_id"], row["generation_id"], row["valuation_date"])
                     for row in result.get("valuation_refs") or []}
    attached_refs = {(row.get("snapshot_id"), row.get("generation_id"), row.get("valuation_date"))
                     for row in manifest.get("valuations") or [] if row.get("status") == "ready"}
    quality = manifest.get("pdf_quality") or {}
    completion = assess_trade_idea_completion(manifest)
    if is_research_mode(run) != is_research_mode(manifest):
        raise ValueError('Delivery analysis mode differs from the accepted run')
    if execution_policy(run) != execution_policy(manifest):
        raise ValueError('Delivery policy differs from the accepted run')
    delivery_failure = ("PDF finale non qualificato: " + "; ".join(quality.get("reasons") or
                        [quality.get("reason") or "misurazione assente"])
                        if quality.get("status") != "ready" else
                        "Excel riferiti dal Capo non attestati nel pacchetto finale"
                        if not is_research_mode(run) and (expected_refs != attached_refs or not expected_refs) else
                        "Pacchetto PDF/Excel incompleto: " + "; ".join(completion["reasons"])
                        if completion["status"] != "ready" else None)
    if delivery_failure:
        if persisted_manifest:
            if run["destination"]["kind"] == "dcn":
                store.block_delivery(run_id, reason="Manifest gia' persistito con artefatto incompleto: "
                                     + delivery_failure)
                raise RuntimeError("manifest persistito non rigenerabile: " + delivery_failure)
            # A partial research package already sealed in SQLite is immutable.
            # Its failed/uncertain SMTP state is handled below, never rebuilt.
            delivery_failure = None
    if delivery_failure:
        try:
            store.demote_unreviewed_destination(run_id, delivery_failure)
        except Exception as exc:
            store.block_delivery(run_id, reason="Demotion PDF/Excel impedita: " + str(exc)[:3500])
            raise
        current = store.get_run(run_id)["run"]
        pdf_artifact = next(a for a in manifest["artifacts"] if a.get("kind") == "pdf")
        if (current["destination"]["kind"] == "research"
                and current["phase"] == "report_incomplete"
                and pdf_artifact.get("name") != "trade-idea-research.pdf"):
            # Keep the draft manifest and its referenced PDF intact until the
            # new research PDF is fully rendered. A crash before the atomic
            # manifest replacement can safely replay this branch.
            report_run = {**run_for_report, "destination": current["destination"]}
            previous_path = pdf_artifact["path"]
            pdf = build_trade_idea_report(report_run, result,
                output_path=directory / "trade-idea-research.pdf",
                valuations=manifest["valuations"],
                language=manifest["language"])
            pdf_artifact.update(path=pdf["path"], name="trade-idea-research.pdf",
                                sha256=pdf["sha256"], status=pdf["status"], reason=pdf["reason"])
            manifest["attachments"] = [pdf["path"] if path == previous_path else path
                                       for path in manifest["attachments"]]
            manifest["expected_hashes"].pop(previous_path)
            manifest["expected_hashes"][pdf["path"]] = pdf["sha256"]
            manifest["pdf_quality"] = pdf["quality"]
            manifest["artifact_status"] = "ready" if pdf["status"] == "ready" and completion["status"] == "ready" else "partial"
            manifest["destination"] = dict(current["destination"])
            if "exact_artifact_receipts" in manifest:
                preserve_trade_idea_artifacts(manifest, allowed_roots=recovery_roots)
            save_file_manifest(manifest["manifest_path"], manifest)
    store.save_manifest(run_id, manifest)
    detail = store.get_run(run_id)
    if not send_email:
        return detail["email"]
    if detail["email"]["status"] in ("accepted", "failed", "uncertain", "blocked"):
        return detail["email"]
    attempt_id = store.claim_email(run_id)
    try:
        outcome = (send or send_trade_idea_delivery)(manifest, language=manifest["language"])
        status = outcome.get("email_status") or outcome.get("status")
        if status not in ("accepted", "failed", "uncertain", "blocked"):
            status = "uncertain"
            outcome = {**outcome, "error": "Esito invio non riconosciuto"}
    except Exception as exc:
        status = "uncertain"
        outcome = {"error": type(exc).__name__ + ": " + str(exc)}
    store.record_email_outcome(run_id, attempt_id, status,
        receipt=outcome if status == "accepted" else None,
        error=None if status == "accepted" else str(outcome.get("email_error") or outcome.get("error")
                                                  or "Invio non accettato")[:4000])
    return store.get_run(run_id)["email"]


# Tetto di _numeric_claim_gaps / _numeric_claim_gaps_v4 (`return gaps[:20]`): oltre il
# tetto il totale vero non e' noto e la frase lo dichiara ("almeno").
_NUMERIC_GAPS_CAP = 20
_NUMERIC_GAP_NAMES = {
    "summary": "sintesi", "pm_view_response": "risposta alla tua tesi", "horizon": "orizzonte",
    "proposal": "proposta operativa", "pros": "pro", "cons": "contro", "risks": "rischio",
    "catalysts": "catalizzatore", "invalidation": "condizione di invalidazione",
    "data_gaps": "dato mancante", "review_conditions": "condizione di revisione",
    "decisive_questions": "domanda decisiva", "pillars": "pilastro",
    "variant_view": "stima divergente dal consenso", "scenarios": "scenario",
    "risk_exits": "rischio e uscita", "review_triggers": "evento di revisione", "objections": "obiezione",
}


def _numeric_gaps_phrase(numeric_gaps, result):
    """Una sola voce leggibile per data_gaps dalla lista tecnica dei numeric_claim_gaps.

    La lista ripete la stessa posizione (una riga per frase o per valore): qui si
    raggruppa per posizione leggibile, si conta e si dichiara il totale vero della
    lista (se la lista e' al tetto, "almeno"). Una posizione che non si sa tradurre
    resta visibile con la sua chiave tecnica, dichiarata come non riconosciuta.
    """
    titles = {str(section.get("key")): str(section.get("title") or "").strip()
              for section in (result or {}).get("dossier") or [] if isinstance(section, dict)}
    counts = {}
    for gap in numeric_gaps:
        location = str(gap).split(": ", 1)[0].strip()
        dossier = re.match(r"dossier\.([A-Za-z0-9_]+)", location)
        item = re.fullmatch(r"([a-z_]+)(?:\[(\d+)\])?(?:\..*)?", location)
        if dossier:
            key = dossier.group(1)
            name = "sezione «" + (titles.get(key) or key) + "»"
        elif item and item.group(1) in _NUMERIC_GAP_NAMES:
            name = _NUMERIC_GAP_NAMES[item.group(1)]
            if item.group(2) is not None:
                name += " " + str(int(item.group(2)) + 1)
        else:
            name = "posizione non riconosciuta «" + location + "»"
        counts[name] = counts.get(name, 0) + 1
    total = len(numeric_gaps)
    capped = total >= _NUMERIC_GAPS_CAP
    return ("Numeri non attestati da una fonte verificata, quindi non usabili per l'operativita': "
            + ("almeno " if capped else "") + str(total)
            + " (" + ", ".join(name + " " + str(count) for name, count in counts.items()) + ")"
            + ("; il conteggio si ferma al tetto di " + str(_NUMERIC_GAPS_CAP) + " dell'elenco tecnico"
               if capped else "") + ".")


def _filing_isolato_assente():
    raise RuntimeError("DB alternativo: archivio Filing isolato non fornito (filing_service_factory)")


_log_fase = logging.getLogger(__name__ + ".fase_documenti")


def _servizio_non_importabile(exc):
    def fabbrica():
        raise RuntimeError("archivio Filing predefinito non importabile: " + type(exc).__name__)
    return fabbrica


class TempoPonteScaduto(RuntimeError):
    """R-FASE B1: il ponte (rilettura e riverifica dei byte) ha superato il suo tetto dichiarato."""


def _riga_fase(messaggio):
    # R-FASE B4: le righe della fase documenti (profili creati compresi) vanno nel log della run, non solo nel logger.
    print("[TRADE_IDEA]" + messaggio, flush=True)
    _log_fase.info(messaggio)


def _ponte_col_tetto(blackboard, ponte, fase, *, as_of, servizio_factory, record):
    """Ponte su una lavagna d'appoggio in un thread col tetto SEPARATO fase.TEMPO_MAX_PONTE_S: la ricevuta entra
    nella run solo se il ponte finisce in tempo (un thread tardivo non scrive mai nella lavagna vera)."""
    import time
    from types import SimpleNamespace
    with blackboard._lock:
        dati = {k: deepcopy(blackboard.data[k]) for k in (ponte.CHIAVE_BOARD, "_research_thesis")
                if k in blackboard.data}
    ombra = SimpleNamespace(data=dati, _lock=threading.RLock(),
                            target_ticker=getattr(blackboard, "target_ticker", None))
    esito = {}

    def corpo():
        try:
            ponte.ammetti_per_trade_idea(ombra, as_of=as_of, servizio_factory=servizio_factory,
                aggiornamento=fase.aggiornamento_per_ponte(record), registro_factory=fase.registro_con_tipo(record))
        except BaseException as exc:
            esito["eccezione"] = exc
    inizio = time.monotonic()
    filo = threading.Thread(target=corpo, name="trade-idea-ponte", daemon=True)
    filo.start()
    filo.join(fase.TEMPO_MAX_PONTE_S)
    durata = round(time.monotonic() - inizio, 1)
    with blackboard._lock:
        blackboard.data["_filing_ponte_misura"] = {"durata_s": durata, "tetto_s": fase.TEMPO_MAX_PONTE_S,
            "regola": "tetto separato del ponte (rilettura e riverifica dei byte), dichiarato; la fase ha il suo",
            "esito": "tempo_scaduto" if filo.is_alive() else ("errore" if "eccezione" in esito else "ok")}
    if filo.is_alive():
        raise TempoPonteScaduto("ponte oltre %s s" % fase.TEMPO_MAX_PONTE_S)
    if "eccezione" in esito:
        raise esito["eccezione"]
    if ponte.CHIAVE_BOARD in ombra.data:
        with blackboard._lock:
            blackboard.data[ponte.CHIAVE_BOARD] = deepcopy(ombra.data[ponte.CHIAVE_BOARD])


def _fase_documenti_e_ponte(blackboard, *, as_of, servizio_factory, nome, preferenze_predefinite, stop_fn=None):
    """APERTO-NUOVI (PM 06/10): fase documenti pre-R0 (profilo Filing attivato per un titolo nuovo, run
    Filing del solo titolo, passi extra) e poi il ponte. Vincolo PM: MAI bloccare la run. Tempo massimo
    duro dentro la fase; qualsiasi guasto qui = lacuna dichiarata (tipo e motivo), mai un'eccezione alla run."""
    from bellomberg.agents import ponte_filing_dossier as ponte
    from bellomberg.market_data import filing_titolo_nuovo as fase
    record = None
    aperto = []
    if servizio_factory is None:
        # PRODUZIONE (revisione R-FASE 07/10): execute_trade_idea passa None col DB predefinito. Senza questa
        # risoluzione servizio_una_volta chiamava None() -> nessuna attivazione e nessun documento (in silenzio).
        try:
            from bellomberg.api.filing_routes import default_service as servizio_factory
        except Exception as exc:
            _log_fase.warning("archivio Filing predefinito non importabile: %s", type(exc).__name__)
            servizio_factory = _servizio_non_importabile(exc)

    def servizio_una_volta():
        # Fase e ponte usano lo STESSO archivio Filing (aperto una volta; un guasto si ripete uguale).
        if not aperto:
            try:
                aperto.append(("ok", servizio_factory()))
            except Exception as exc:
                aperto.append(("errore", exc))
        esito, valore = aperto[0]
        if esito == "errore":
            raise valore
        return valore
    try:
        record = fase.fase_documenti_trade_idea(blackboard, as_of=as_of, servizio_factory=servizio_una_volta,
            nome=nome, preferenze_predefinite=preferenze_predefinite, log=_riga_fase, stop_fn=stop_fn,
            # DB alternativo (isolato): niente fonti vive implicite nei passi extra, lacuna dichiarata.
            passi_extra=None if preferenze_predefinite else fase.passi_isolati())
    except Exception as exc:
        _log_fase.warning("fase documenti non eseguita: %s", type(exc).__name__)
        record = {"stato": "non_aggiornato", "motivo": "fase documenti non eseguita: " + type(exc).__name__
                  + ": " + str(exc)[:200]}
    try:
        with blackboard._lock:
            ripresa = (ponte.CHIAVE_BOARD in blackboard.data or blackboard.data.get("_research_thesis") is not None)
        if ripresa:
            # R-FASE 07/10: ricevuta gia' presente o tesi gia' sigillata = si riusa, nessun thread ne' tetto
            # (un tetto scaduto qui cancellava una ricevuta valida dopo il sigillo).
            ponte.ammetti_per_trade_idea(blackboard, as_of=as_of, servizio_factory=servizio_una_volta,
                aggiornamento=fase.aggiornamento_per_ponte(record), registro_factory=fase.registro_con_tipo(record))
        else:
            _ponte_col_tetto(blackboard, ponte, fase, as_of=as_of, servizio_factory=servizio_una_volta, record=record)
    except Exception as exc:
        _log_fase.warning("ponte Filing non eseguito: %s", type(exc).__name__)
        try:
            ticker = getattr(blackboard, "target_ticker", None)
            ponte.ponte_non_eseguito(blackboard, exc, as_of=as_of, mantieni_esistente=True,
                                     tickers=[ticker] if isinstance(ticker, str) and ticker else [])
        except Exception as exc2:
            _log_fase.warning("lacuna del ponte non registrata: %s", type(exc2).__name__)


def execute_trade_idea(run_id, *, db_path=None, store=None, lock_path=None,
                       output_dir=None, portfolio_loader=None, mandate_loader=None,
                       preparer_binder=None, round_runner=None, red_runner=None,
                       capo_runner=None, valuation_evaluator=None, risk_loader=None,
                       stress_loader=None, candidate_metrics_loader=None,
                       delivery_preparer=None, delivery_sender=None,
                       catalog_fetcher=None, isolated_tool_dispatcher=None,
                       isolated_facts_loader=None, source_qualifier=None,
                       model_reviser=None, document_archive_root=None, model_input_loader=None,
                       source_session_factory=None, market_pack_loader=None, filing_service_factory=None):
    """Claim one accepted candidate and run R0/R1/Red/R2/Capo exactly once."""
    from bellomberg.storage.trade_idea_store import TradeIdeaStore
    from bellomberg.core import mandato_pm
    from bellomberg.agents.specialists import Blackboard
    from bellomberg.agents.consigliere_multi import run_round
    from bellomberg.agents.red_team import run_red_team, motivo_critica_non_utilizzabile
    from bellomberg.core.language import language_context
    from bellomberg.core.paths import DATA_DIR, REPORT_DIR, SQLITE_PATH
    from pathlib import Path
    from bellomberg.storage.trade_idea_store import WORKER_LOCK_BUSY_S, WORKER_LOCK_WAIT_S
    store = store or TradeIdeaStore(db_path, lock_wait_s=WORKER_LOCK_WAIT_S,
                                    lock_busy_s=WORKER_LOCK_BUSY_S)
    initial_detail = store.get_run(run_id)
    run = initial_detail["run"]
    if not is_research_mode(run):
        from bellomberg.storage.trade_idea_store import RunConflict
        raise RunConflict('Run Excel in archivio: nuova ricerca o prosecuzione richiede un incarico separato')
    artifact_dir = Path(output_dir) if output_dir is not None else REPORT_DIR / "trade_ideas" / run_id
    default_risk_db_matches = Path(store.db_path).resolve() == Path(SQLITE_PATH).resolve()
    if not default_risk_db_matches and output_dir is None:
        raise ValueError("DB alternativo: output_dir isolato richiesto prima di ogni lavoro")
    token = None
    blackboard = None
    source_validation = None
    result = None
    try:
        from contextlib import nullcontext
        source_scope = (nullcontext() if default_risk_db_matches else
            _isolated_source_scope(isolated_tool_dispatcher, isolated_facts_loader,
                                   mandate_loader))
        with language_context(run["language"]), exclusive_paid_run(lock_path), source_scope:
            token = store.claim_run(run_id)
            try:
                if not default_risk_db_matches and (risk_loader is None or stress_loader is None):
                    raise RuntimeError("DB alternativo: loader rischio e stress isolati richiesti prima delle chiamate pagate")
                if not default_risk_db_matches and not all(callable(item) for item in
                        (isolated_tool_dispatcher, isolated_facts_loader, mandate_loader,
                         candidate_metrics_loader, source_qualifier,
                         *((preparer_binder,) if not is_research_mode(run) else ()))):
                    raise RuntimeError("DB alternativo: tool, fatti, mandato, preparatore e metriche isolati richiesti prima delle chiamate pagate")
                run = store.get_run(run_id)["run"]
                mandate = (mandate_loader or mandato_pm.carica)()
                if portfolio_loader is None:
                    from bellomberg.storage.memory_db import MemoryDB
                    portfolio = MemoryDB(db_path=store.db_path).get_portfolio_summary()
                else:
                    portfolio = portfolio_loader()
                fx_initial = _fx_receipt(portfolio)
                context = store.decision_context(run_id)
                from bellomberg.storage.trade_idea_workspace import WorkspaceEvents
                context["pm_objections"] = WorkspaceEvents(store).pm_history(run["ticker"])
                history = _candidate_history(store, run["ticker"], run_id)
                gate = TradeIdeaBudgetGate(store, run_id, token, run["catalog_snapshot"],
                                           catalog_fetcher=catalog_fetcher)
                # MOD-TI 06/10: i modelli sono quelli del contratto; un .env cambiato dopo
                # l'accettazione si dichiara qui (progresso + log) e non si applica.
                if contract_models(run) != gate.contract_models:
                    raise ValueError("models_json e catalog snapshot della run non coincidono")
                model_contract = gate.declare_model_selection()
                identity = {"ticker": run["ticker"], "name": run.get("company_name"),
                    "exchange": run.get("exchange"), "currency": run.get("currency"), "status": "confirmed"}
                accepted_request = store.get_accepted_request(run_id)
                accepted_sources = accepted_request.get("source_qualification") or {}
                research_native = accepted_sources.get('status') == 'research_required'
                if research_native and not default_risk_db_matches and source_session_factory is None:
                    raise RuntimeError('DB alternativo: sessione ricerca fonti isolata richiesta prima delle chiamate pagate')
                source_as_of = accepted_sources.get("as_of")
                accepted_receipt = accepted_sources.get("document_receipt")
                document_options = {}
                if accepted_receipt is not None:
                    if not default_risk_db_matches and document_archive_root is None:
                        raise RuntimeError("DB alternativo: archivio fonti PM isolato richiesto prima delle chiamate pagate")
                    document_options["accepted_document_receipt"] = accepted_receipt
                qualification_archive = (document_archive_root if document_archive_root is not None else
                    (Path(output_dir) / "source-archive") if output_dir is not None else None)
                from bellomberg.valuation.trade_idea_model import recheck_accepted_sources, source_validation_receipt
                from bellomberg.core.trade_idea_contract import validate_run_authorization
                checked_sources = None
                sources_validated = False
                source_mode = 'explicit_injected_qualifier' if source_qualifier is not None else 'accepted_snapshot'
                validation_path = Path(output_dir or (DATA_DIR / "trade_ideas" / run_id)) / 'source-validation.json'
                try:
                    validate_run_authorization(run.get("authorization"), accepted_sources)
                    checked_sources = (source_qualifier(run["ticker"], identity, source_as_of,
                        archive_root=qualification_archive, **document_options) if source_qualifier is not None else
                        recheck_accepted_sources(accepted_sources, run["ticker"], identity,
                            archive_root=document_archive_root or (DATA_DIR / "filing_archive"),
                            allow_historical=bool(run.get("continuation"))))
                    validate_run_authorization(run.get("authorization"), checked_sources)
                    sources_validated = True
                finally:
                    source_validation = source_validation_receipt(accepted_sources, checked_sources,
                        mode=source_mode, status='verified' if sources_validated else 'blocked')
                    validation_path.parent.mkdir(parents=True, exist_ok=True)
                    from tempfile import NamedTemporaryFile
                    with NamedTemporaryFile('w', encoding='utf-8', dir=validation_path.parent,
                            prefix='.source-validation-', suffix='.tmp', delete=False) as receipt_file:
                        receipt_tmp = Path(receipt_file.name)
                        json.dump(source_validation, receipt_file, ensure_ascii=False, allow_nan=False, sort_keys=True)
                        receipt_file.flush()
                        os.fsync(receipt_file.fileno())
                    os.replace(receipt_tmp, validation_path)
                store.update_progress(run_id, token, 'source_validation', {'source_validation': source_validation,
                                                                            'model_contract': model_contract})
                qualification = deepcopy(accepted_sources) if source_qualifier is None else checked_sources
                if is_research_mode(run):
                    preparer, prep_state = None, {'status': 'not_required',
                        'reason': 'Documented research without workbook generation'}
                elif preparer_binder is not None:
                    preparer, prep_state = preparer_binder(gate, run["ticker"])
                else:
                    preparer, prep_state = None, {"status": "fundamentals_authored_inputs",
                        "automatic_forecast_preparation": False,
                        "reason": "Fundamentals supplies explicit inputs; common compilation does not call an auxiliary analyst"}
                hb_path = Path(output_dir or (DATA_DIR / "trade_ideas" / run_id)) / "heartbeat.json"
                blackboard = Blackboard(memory_db=None, memo_id=None,
                    valuation_preparer=preparer, heartbeat_path=hb_path,
                    run_scope="trade_idea", run_id=run_id, target_ticker=run["ticker"],
                    pm_view=run["view_text"], candidate_history=json.dumps(history, ensure_ascii=False),
                    budget_gate=gate)
                blackboard.r2_specialists = set(TRADE_IDEA_DESKS)
                text_policy = mandato_pm.text_policy_from_context(run)
                if mandato_pm.MANDATE_TEXT_POLICY_KEY in run:
                    blackboard.mandate_text_policy = text_policy
                blackboard.analysis_mode = run.get('analysis_mode')
                blackboard.execution_policy = execution_policy(run)
                blackboard.independent_round = 1
                blackboard.model_phase = "research"
                blackboard.source_qualification = qualification
                if research_native:
                    blackboard.source_admission = deepcopy(accepted_sources)
                blackboard.model_registry = None if is_research_mode(run) else _model_registry(store.db_path)
                blackboard.model_roots = [artifact_dir]
                blackboard.artifact_vault_dir = artifact_dir / ".exact-artifacts"
                blackboard.expected_reports = 18
                blackboard.data["_valuation_preparation"] = prep_state
                blackboard.data["_decision_context"] = context
                try:
                    from bellomberg.agents.chat_tools import _compatta_portfolio_live
                    blackboard.data["_portfolio_context"] = _compatta_portfolio_live(portfolio)
                except Exception as exc:
                    blackboard.data["_portfolio_context"] = {
                        "status": "unavailable", "reason": type(exc).__name__ + ": " + str(exc)[:350]}
                blackboard.data["_identity"] = {"ticker": run["ticker"],
                    "name": run.get("company_name"), "exchange": run.get("exchange"),
                    "currency": run.get("currency")}
                if not is_research_mode(run):
                    input_basis = (model_input_loader(qualification) if model_input_loader is not None else
                                   _qualified_model_input_basis(qualification))
                    if not isinstance(input_basis, dict) or not isinstance(input_basis.get("plan"), dict):
                        raise ValueError("Declared model input basis must contain an explicit plan")
                    _plan_digest(input_basis)
                    blackboard.data["_model_input_basis"] = deepcopy(input_basis)
                    blackboard.model_checkpoint_writer = lambda progress: store.update_progress(
                        run_id, token, "model_building", progress)
                    blackboard.consult_specialist = lambda requester, target, question, draft_assumptions, evidence_refs, supersedes_failed=None: _consult_model_desk(
                        blackboard, requester, target, question, draft_assumptions, evidence_refs, supersedes_failed)
                    blackboard.build_candidate_model = lambda inputs: _build_fundamentals_candidate(
                        blackboard, inputs, artifact_dir / "model", evaluator=valuation_evaluator)
                _configure_native_recovery(blackboard, store, token,
                    inherited=initial_detail["progress"] if run.get("continuation") else None)
                # E7: DOPO la ripresa (che sostituisce blackboard.data con il checkpoint);
                # sola lettura, nessun memory_db consegnato a Red Team o desk.
                _bind_pm_constraints(blackboard, store.db_path, mandate,
                                     resumed=bool(run.get("continuation")))
                if research_native:
                    _bind_trade_idea_source_research(blackboard,
                        archive_root=document_archive_root or (DATA_DIR / 'filing_archive'),
                        artifact_dir=artifact_dir, session_factory=source_session_factory)
                if is_research_mode(run):
                    # APERTO-TI (PM 06/10): documenti dell'archivio Filing nel dossier PRIMA dei desk, con la
                    # ricevuta del ponte nei dati della run (la ripresa la riusa). DB alternativo senza
                    # archivio isolato = lacuna dichiarata, mai l'archivio vero.
                    _fase_documenti_e_ponte(blackboard, as_of=qualification.get("as_of") or source_as_of,
                        servizio_factory=(filing_service_factory if filing_service_factory is not None
                                          or default_risk_db_matches else _filing_isolato_assente),
                        nome=run.get("company_name"), preferenze_predefinite=default_risk_db_matches,
                        # R-FASE B4: lo stop del PM e' ascoltato anche durante la fase documenti
                        stop_fn=lambda: bool(store.get_run(run_id)["run"]["stop_requested"]))
                store.update_progress(run_id, token, "preparation", _progress(blackboard, "preparation"))

                def check_stop():
                    blackboard.raise_if_run_blocked()
                    if store.get_run(run_id)["run"]["stop_requested"]:
                        raise RuntimeError("Trade Idea fermata dal PM")

                runner = round_runner or run_round
                check_stop()
                if not is_research_mode(run):
                    blackboard.data.setdefault("_model_review", {"status": "not_created",
                        "initial_refs": [], "final_refs": [], "revision_log": [], "objections": []})
                for round_n, phase in ((0, "recon"), (1, "analysis")):
                    check_stop()
                    runner(blackboard, round_n)
                    check_stop()
                    if is_research_mode(blackboard):
                        _quorum_still_reachable(blackboard)
                    store.update_progress(run_id, token, phase,
                                          _progress(blackboard, phase, events=(f"R{round_n} concluso",)))
                if is_research_mode(blackboard):
                    _run_research_committee(blackboard, portfolio, runner, red_runner or run_red_team,
                        check_stop, lambda phase: store.update_progress(run_id, token, phase, _progress(blackboard, phase)))
                    audit = None
                else:
                    initial_refs = _verified_candidate_valuations(blackboard)
                    if not initial_refs:
                        # Restored research stages may all be complete. The model
                        # has its own phase even when run_round skips Fundamentals.
                        blackboard.model_phase = "building"
                        # Compile the final authored draft once; no auxiliary AI or
                        # inferred plan is introduced when Fundamentals omits get_valuation.
                        completion = blackboard.build_candidate_model({"ticker": run["ticker"]})
                        if not completion.get("ok"):
                            blackboard.data["_model_completion_error"] = completion.get("error") or "Model compilation incomplete"
                        initial_refs = _verified_candidate_valuations(blackboard)
                    if not initial_refs and _complete_saved_model_authoring(blackboard):
                        initial_refs = _verified_candidate_valuations(blackboard)
                    if initial_refs:
                        _clear_verified_model_authoring_failure(blackboard)
                    store.update_progress(run_id, token, "model", _progress(blackboard, "model"))
                    if not initial_refs:
                        reason = "; ".join(dict.fromkeys(value for value in (
                            blackboard.data.get("_model_completion_error"),
                            (blackboard.data.get("_model_plan_last_error") or {}).get("error")) if value))
                        raise RuntimeError("Fundamentals did not build a usable verified Excel during the analysis round"
                                           + (": " + reason if reason else ""))
                    if (blackboard.valuation_results[run["ticker"]].get("request_origin") != "fundamentals-model-build"
                            and valuation_evaluator is None):
                        raise RuntimeError("The run model was not built by Fundamentals")
                    blackboard.model_phase = "review"
                    check_stop()
                    red_attestation = blackboard.data.get("_red_model_review") or {}
                    previous_red = blackboard.get_latest("red_team")
                    red_reused = (red_attestation.get("model_ref") == {key: initial_refs[0][key] for key in
                        ("snapshot_id", "generation_id", "workbook_sha256")}
                        and previous_red and red_attestation.get("report_sha256") == _plan_digest(previous_red["report"]))
                    if not red_reused:
                        _run_exact_model_red_team(blackboard, portfolio, red_runner or run_red_team)
                    from bellomberg.core.trade_idea_contract import validate_committee_review
                    red_review = validate_committee_review(blackboard.get_latest("red_team")["report"])
                    if any(set(row["evidence_refs"]) - _review_source_ids(blackboard) for row in red_review["objections"]):
                        raise ValueError("Red Team cites evidence not retrieved for this exact candidate")
                    if not red_reused or "_objections" not in blackboard.data:
                        blackboard.data["_objections"] = [{"objection": row, "response": None,
                            "evidence_refs": [], "state": "open", "model_revision_id": None}
                            for row in red_review["objections"]]
                    blackboard.data["_decisive_questions"] = red_review["decisive_questions"]
                    blackboard.r2_specialists = set(TRADE_IDEA_DESKS)
                    blackboard.expected_reports = 18
                    store.update_progress(run_id, token, "red_team", _progress(blackboard, "red_team"))
                    check_stop()
                    runner(blackboard, 2)
                    check_stop()
                    store.update_progress(run_id, token, "review", _progress(blackboard, "review"))
                    check_stop()
                    audit = _finalize_model_review(blackboard, gate,
                        output_dir=artifact_dir / "model",
                        model_reviser=model_reviser)
                    # Each changed Excel receives actual desk scrutiny before the
                    # Capo can see it. Paid follow-ups share this run's one ceiling.
                    reviewed_generation = initial_refs[0]["generation_id"]
                    while blackboard.valuation_results[run["ticker"]]["generation_id"] != reviewed_generation:
                        reviewed_generation = blackboard.valuation_results[run["ticker"]]["generation_id"]
                        blackboard.data.setdefault("_review_history", []).append({
                            "generation_id": reviewed_generation, "previous_reports": {
                                name: blackboard.read(name, 2) for name in blackboard.r2_specialists},
                            "objections": json.loads(json.dumps(blackboard.data["_objections"]))})
                        blackboard.r2_specialists = set(TRADE_IDEA_DESKS)
                        blackboard.data["_desk_model_reviews"] = {}
                        blackboard.expected_reports = 18
                        for reply in blackboard.data["_objections"]:
                            reply.update(response=None, evidence_refs=[], state="open", model_revision_id=None)
                        check_stop()
                        gate.phase = "model_revision"
                        try:
                            # The old critique remains historical; attack the changed
                            # exact generation before its renewed all-desk discussion.
                            _run_exact_model_red_team(blackboard, portfolio, red_runner or run_red_team)
                            red_review = validate_committee_review(blackboard.get_latest("red_team")["report"])
                            if any(set(row["evidence_refs"]) - _review_source_ids(blackboard) for row in red_review["objections"]):
                                raise ValueError("Revised-model Red Team cites evidence not retrieved")
                            blackboard.data["_objections"] = [{"objection": row, "response": None,
                                "evidence_refs": [], "state": "open", "model_revision_id": None}
                                for row in red_review["objections"]]
                            runner(blackboard, 2)
                            check_stop()
                        finally:
                            gate.phase = "committee"
                        audit = _finalize_model_review(blackboard, gate,
                            output_dir=artifact_dir / "model", model_reviser=model_reviser)
                        store.update_progress(run_id, token, "model_revision_review", _progress(blackboard, "model_revision_review"))
                    if not any(row["status"] == "retained" and row["before_generation_id"] == reviewed_generation
                               for row in audit["revision_log"]):
                        raise RuntimeError("the final exact model lacks an explicit Fundamentals retention review")
                    missing_replies = [row["objection"]["id"] for row in blackboard.data["_objections"]
                                       if row["objection"]["material"] and not row.get("response")]
                    if missing_replies:
                        raise RuntimeError("material objections without addressed desk reply: " + ", ".join(missing_replies))
                    _require_final_desk_models(blackboard)
                    store.update_progress(run_id, token, "model_review", _progress(blackboard, "model_review"))
                risk_data, stress_data = _measure_operational_risk(portfolio,
                    risk_loader=risk_loader, stress_loader=stress_loader,
                    default_risk_db_matches=default_risk_db_matches)
                metrics = (candidate_metrics_loader(portfolio, run["ticker"], run.get("currency"))
                           if candidate_metrics_loader is not None else None)
                sizing = _compute_sizing(portfolio, run["ticker"], mandate,
                    currency=run.get("currency"), risk_data=risk_data, stress_data=stress_data,
                    candidate_metrics=metrics)
                blackboard.data["_sizing"] = sizing
                blackboard.data.setdefault("_candidate_quote_initial", _candidate_quote_receipt(
                    blackboard, run["ticker"]))
                blackboard.data.setdefault("_data_cutoff", datetime.now(timezone.utc).isoformat())
                if is_research_mode(blackboard) and "_market_pack" not in blackboard.data:
                    # Lotto 3 (L1, Opus 5.5): pacchetto di mercato su FILE accanto alla run, nel
                    # checkpoint solo il riferimento {path, sha256, status, as_of}; in ripresa il
                    # riferimento sopravvive e non si riscarica. DB alternativo senza loader: niente rete.
                    from bellomberg.market_data.trade_idea_market_pack import PACK_FILENAME, run_market_pack
                    blackboard.data["_market_pack"] = run_market_pack(
                        run=run, cutoff=blackboard.data["_data_cutoff"],
                        path=Path(output_dir or (DATA_DIR / "trade_ideas" / run_id)) / PACK_FILENAME,
                        loader=market_pack_loader, network_allowed=default_risk_db_matches)
                failures = []
                declared_gaps = (blackboard.data.get("_desk_gaps") or {}) if is_research_mode(blackboard) else {}
                for name in ("macro", "eventdesk", "crypto", "fundamentals", "quant", "options"):
                    if name in declared_gaps:
                        continue  # sealed as a declared gap: context for the Capo, not a hidden failure
                    for round_n in ((0, 1, 2) if name in blackboard.r2_specialists else (0, 1)):
                        report = blackboard.read(name, round_n)
                        if not isinstance(report, str) or not report.strip() or report.startswith("[ERROR"):
                            failures.append(f"{name} R{round_n} non disponibile")
                red = blackboard.get_latest("red_team")
                if (not red or motivo_critica_non_utilizzabile(red.get("report"))
                        or "CRITICA TRONCATA" in red.get("report", "")):
                    failures.append("Red Team non utilizzabile")
                verified_valuations = _verified_candidate_valuations(blackboard)
                model_gaps = ([] if is_research_mode(blackboard) or verified_valuations else [
                    "workbook Excel candidato non verificabile; "
                    "nessuna generazione riutilizzabile o autorizzata e riuscita"])
                blackboard.required_failure_reasons = failures
                store.update_progress(run_id, token, "capo",
                                      _progress(blackboard, "capo", events=[*failures, *model_gaps]))
                capo_fallback = False
                try:
                    capo_input = _capo_input_fingerprint(blackboard, audit, mandate)
                    completed_capo = blackboard.data.get("_capo_completed")
                    if completed_capo is not None:
                        if completed_capo.get("input_sha256") != capo_input:
                            raise RuntimeError("Saved Capo conclusion dependencies differ; targeted review required")
                        from bellomberg.core.trade_idea_contract import validate_result
                        payload = {key: value for key, value in completed_capo["result"].items()
                                   if key not in ("run_id", "run_type", "pm_view")}
                        result = validate_result(payload, run_id=run_id, ticker=run["ticker"], pm_view=run["view_text"],
                                                 execution_policy=execution_policy(run))
                    else:
                        result = (capo_runner or run_trade_idea_capo)(
                            blackboard, portfolio=portfolio, mandate=mandate,
                            decision_context=blackboard.data["_decision_context"], sizing=sizing)
                        blackboard.data["_capo_completed"] = {"input_sha256": capo_input, "result": deepcopy(result)}
                        blackboard.persist_run_checkpoint("capo_complete")
                except Exception as exc:
                    check_stop()
                    capo_fallback = True
                    reason = "Capo Trade Idea non valido: " + type(exc).__name__ + ": " + str(exc)[:2800]
                    blackboard.record_run_failure(exc, desk="capo", round_n=3)
                    failures.append(reason)
                    result = _incomplete_capo_result(run, blackboard, reason)
                result["model_review"] = audit
                result["decisive_questions"] = blackboard.data.get("_decisive_questions", [])
                if failures and result["judgment"] != "incomplete":
                    raise ValueError("Capo non dichiara i componenti obbligatori mancanti")
                failures.extend(model_gaps)
                numeric_gaps = _numeric_claim_gaps(result, blackboard, run["ticker"],
                    blackboard.data["_data_cutoff"], sizing,
                    **({"portfolio": portfolio, "mandate": mandate}
                       if execution_policy(run) == EXECUTION_POLICY_V4 else {}))
                blackboard.data["_numeric_claim_gaps"] = numeric_gaps
                if numeric_gaps and len(result["data_gaps"]) < 80:
                    # Una voce leggibile col totale vero; la lista tecnica resta nel blackboard.
                    result["data_gaps"].append(_numeric_gaps_phrase(numeric_gaps, result))
                for pm_gap in pm_constraints_gaps(blackboard):  # E7: vincoli/mandato PM mancanti
                    if pm_gap not in result["data_gaps"] and len(result["data_gaps"]) < 80:
                        result["data_gaps"].append(pm_gap)
                for red_gap in reversed(_red_inadmissible_gaps(blackboard)):  # 06/10: Red Team fuori dossier
                    if red_gap not in result["data_gaps"]:
                        result["data_gaps"] = [red_gap, *result["data_gaps"]][:80]
                for doc_gap in official_documents_gaps(blackboard):  # PM 05/10: run senza filing, buco dichiarato
                    if doc_gap not in result["data_gaps"]:  # in testa: col tetto 80 non e' lui a cadere
                        result["data_gaps"] = [doc_gap, *result["data_gaps"]][:80]
                if numeric_gaps and result["judgment"] == "favorable" and result.get("proposal"):
                    failures.append("claim quantitativi senza binding alle ricevute dei tool")
                try:
                    if portfolio_loader is None:
                        from bellomberg.storage.memory_db import MemoryDB
                        portfolio_final = MemoryDB(db_path=store.db_path).get_portfolio_summary()
                    else:
                        portfolio_final = portfolio_loader()
                    fx_final = _fx_receipt(portfolio_final)
                except Exception as exc:
                    fx_final = {"sampled_at": datetime.now(timezone.utc).isoformat(),
                                "market_as_of": None, "valid": False, "observations": [],
                                "reason": type(exc).__name__ + ": " + str(exc)[:350]}
                blackboard.data["_candidate_quote_final"] = _candidate_quote_receipt(
                    blackboard, run["ticker"])
                if not _candidate_quote_matches(
                        blackboard.data["_candidate_quote_initial"],
                        blackboard.data["_candidate_quote_final"]):
                    quote_gap = "quotazione candidato cambiata, stale o non verificabile tra Capo e routing"
                    if len(result["data_gaps"]) < 80:
                        result["data_gaps"].append(quote_gap)
                    if result["judgment"] == "favorable" and result.get("proposal"):
                        failures.append(quote_gap)
                try:
                    from bellomberg.core.paths import REPORT_DIR
                    quality = _preview_quality(run, result,
                        output_dir or (REPORT_DIR / "trade_ideas" / run_id), blackboard)
                except Exception as exc:
                    quality = {"status": "error", "reason": type(exc).__name__ + ": " + str(exc)}
                checks = _operational_checks(run, result, blackboard, sizing, portfolio,
                                             mandate, context, quality,
                                             fx_initial=fx_initial, fx_final=fx_final)
                if (run.get("continuation") or {}).get("price_refresh"):
                    # Recompute after the AI conclusion, keeping that proposal
                    # unchanged and the original research/context historical.
                    refreshed = _final_price_refresh_verification(store, run_id, result,
                        portfolio_loader=portfolio_loader, risk_loader=risk_loader,
                        stress_loader=stress_loader, mandate=mandate,
                        candidate_metrics_loader=candidate_metrics_loader,
                        candidate_quote=blackboard.data["_candidate_quote_final"],
                        default_risk_db_matches=default_risk_db_matches,
                        accepted_band=_refresh_accepted_band(run, result, sizing, portfolio, mandate))
                    checks["price_refresh_verification"] = refreshed
                    blackboard.data["_price_refresh_verification"] = deepcopy(refreshed)
                    checks["sizing_valid"] = (checks["sizing_valid"] is True
                        and all(value is True for value in refreshed["measurements"].values()))
                if capo_fallback:
                    checks["capo_valid"] = False
                result_progress = _progress(blackboard, "result", events=failures)
                result_progress["report_quality"] = quality
                result_progress["routing_checks"] = checks
                result_progress["data_cutoff"] = blackboard.data["_data_cutoff"]
                result_progress["fx_receipts"] = {"initial": fx_initial, "final": fx_final}
                result_progress["candidate_quote_receipts"] = {
                    "initial": blackboard.data["_candidate_quote_initial"],
                    "final": blackboard.data["_candidate_quote_final"]}
                store.update_progress(run_id, token, "result", result_progress)
                if quality.get("status") != "ready":
                    failures.append("dossier PDF non qualificato: " + "; ".join(quality.get("reasons") or
                                    [quality.get("reason") or "misurazione non disponibile"]))
                status = "incomplete" if failures or result["judgment"] == "incomplete" else "completed"
                store.finish_run(run_id, token, result, status,
                                 reason="; ".join(failures) if failures else None)
                try:
                    store.route_result(run_id, checks)
                except Exception as exc:
                    reason = "Routing finale KO: " + type(exc).__name__ + ": " + str(exc)[:3500]
                    store.mark_routing_failure(run_id, reason)
                    try:
                        # Never re-promote after a failed operational transaction.
                        # A successful fallback records a research destination and
                        # permits delivery of the labelled partial package.
                        store.route_result(run_id, {})
                    except Exception:
                        store.block_delivery(run_id, reason=reason)
                        raise
            except Exception as exc:
                if token and store.get_run(run_id)["run"]["technical_status"] == "running":
                    reason = type(exc).__name__ + ": " + str(exc)[:3800]
                    if blackboard is not None and hasattr(blackboard, "record_run_failure"):
                        blackboard.record_run_failure(exc, desk=None,
                                                     round_n=blackboard.current_round)
                    progress = (_progress(blackboard, "incomplete", events=(reason,))
                                if blackboard else {"phase": "failed", "events": [{"message": reason}]})
                    if source_validation is not None:
                        progress['source_validation'] = source_validation
                    store.update_progress(run_id, token, progress["phase"], progress)
                    stopped = store.get_run(run_id)["run"]["stop_requested"]
                    # Paid material must not vanish with the exception: a validated Capo
                    # result, or the desk/Red Team reports, close the run as a labelled
                    # partial package routed to research (never an operational proposal).
                    candidates = []
                    if not stopped and blackboard is not None:
                        # A Capo verdict whose post-checks crashed is never delivered as a
                        # judgment: it keeps its analysis but becomes incomplete, without a
                        # proposal, with the cause as a data gap (email stays blocked).
                        if isinstance(result, dict):
                            unchecked = deepcopy(result)
                            unchecked.update(judgment="incomplete", proposal=None)
                            gap = ("Controlli successivi al Capo non completati: " + reason)[:1500]
                            unchecked["data_gaps"] = [gap, *(unchecked.get("data_gaps") or [])][:80]
                            candidates.append(unchecked)
                        try:
                            if any(report.get("status") == "ready" for report in _reports(blackboard)):
                                candidates.append(lambda: _incomplete_capo_result(run, blackboard, reason))
                        except Exception as salvage_exc:
                            reason += ("; materiale parziale non ricostruibile: "
                                       + type(salvage_exc).__name__)[:3990 - len(reason)]
                    status = "cancelled" if stopped else "incomplete" if blackboard else "failed"
                    salvage = None
                    for candidate in candidates:
                        try:
                            salvage = candidate() if callable(candidate) else candidate
                            store.finish_run(run_id, token, salvage, status, reason=reason)
                            break
                        except Exception:
                            salvage = None
                            if store.get_run(run_id)["run"]["technical_status"] != "running":
                                break
                    if store.get_run(run_id)["run"]["technical_status"] == "running":
                        store.finish_run(run_id, token, None, status, reason=reason)
                    if salvage is not None:
                        try:
                            store.route_result(run_id, {})
                        except Exception as route_exc:
                            route_reason = ("Routing parziale KO: " + type(route_exc).__name__ + ": "
                                            + str(route_exc)[:3000])
                            try:
                                store.mark_routing_failure(run_id, route_reason)
                            finally:
                                store.block_delivery(run_id, reason=route_reason)
            finally:
                if blackboard is not None:
                    terminal = store.get_run(run_id)["run"]
                    blackboard.mark_run_complete(technical_status=terminal["technical_status"],
                                                 reason=terminal["reason"])
    except PaidRunBusy as exc:
        token = store.claim_run(run_id)
        store.finish_run(run_id, token, None, "failed", reason=str(exc))
    final = store.get_run(run_id)
    if (final["run"]["technical_status"] in ("completed", "incomplete")
            and final["email"]["status"] != "blocked"):
        try:
            progress = final.get("progress") or {}
            deliver_trade_idea(store, run_id,
                valuation_results=progress.get("valuation_generations") or progress.get("valuation_results"),
                valuation_attempts=progress.get("valuation_attempts") or (),
                output_dir=output_dir, prepare=delivery_preparer, send=delivery_sender)
        except Exception as exc:
            try:
                store.block_delivery(run_id, reason="Consegna KO: " + type(exc).__name__ + ": " + str(exc)[:3800])
            except Exception:
                pass
    return store.get_run(run_id)


def main(argv=None):
    import argparse
    from bellomberg.storage.memory_db import SQLITE_PATH
    parser = argparse.ArgumentParser(description="Run one accepted Trade Idea candidate")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-path", default=str(SQLITE_PATH))
    options = parser.parse_args(argv)
    detail = execute_trade_idea(options.run_id, db_path=options.db_path)
    print(json.dumps({"run_id": options.run_id,
                      "technical_status": detail["run"]["technical_status"],
                      "destination": detail["run"]["destination"],
                      "email_status": detail["email"]["status"],
                      "cost": detail["cost"]}, ensure_ascii=False))
    return 0 if detail["run"]["technical_status"] in ("completed", "incomplete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
