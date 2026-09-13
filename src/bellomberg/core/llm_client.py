"""
llm_client.py — OpenRouter con la SUPERFICIE dell'SDK Anthropic che Bellomberg usa.

PERCHE' ESISTE (05/09/2026, ordine del PM, Fable 5.1 chat backend `bellomberg-d8`)
    Il PM ha ordinato di usare la chiave di OpenRouter al posto di quella Anthropic, con un
    modello DIVERSO per ogni funzione (chat per agente, desk R0/R1/R2, Capo, red team,
    reflection, estrattore, briefing, classificatore news) scritto nel `.env`.
    Dieci call site del prodotto chiamavano `anthropic.Anthropic(...).messages.create/stream`
    e leggevano la risposta nella forma dell'SDK (`content[]` a blocchi, `stop_reason`,
    `usage.input_tokens`...). Riscriverli nel formato OpenAI avrebbe voluto dire rifare i
    tool loop dei desk e della chat e invalidare i client finti di 16 file di test: questo
    modulo parla con OpenRouter (chat/completions) ma ESPONE quella stessa superficie, cosi'
    i call site cambiano tre righe e i finti dei test restano validi.
    Spec: docs/superpowers/specs/2026-09-05-openrouter-migrazione-design.md.

COSA TRADUCE (richiesta)
    system (stringa o blocchi)      -> messaggio role=system
    messages a blocchi Anthropic    -> assistant.tool_calls + role=tool + user
    tools {name, input_schema}      -> {"type":"function","function":{name, parameters}}
    tool_choice {"type": none/any/tool} -> "none" / "required" / {"type":"function",...}
    thinking adaptive / disabled    -> reasoning REASONING_ADAPTIVE / REASONING_DISABLED
    cache_control                   -> conservato SOLO per gli slug in PREFISSI_CACHE_CONTROL
                                       (gli altri provider cachano da soli); sui tools non ha
                                       equivalente e si toglie sempre; l'header beta Anthropic
                                       non parte mai (OpenRouter legge il ttl dal cache_control).

COSA TRADUCE (risposta)
    message.content / tool_calls / reasoning -> blocchi text / tool_use / thinking
    finish_reason stop/length/tool_calls     -> end_turn / max_tokens / tool_use
    content_filter o message.refusal         -> "refusal" + stop_details (llm_refusal lo legge)
    usage: input_tokens = prompt_tokens - cached - cache_write (Anthropic li conta A PARTE e i
    call site lo assumono), cache_read/cache_write dai prompt_tokens_details, output_tokens =
    completion_tokens (ragionamento incluso, com'era), cost_usd = usage.cost di OpenRouter.
    Un campo assente resta None, mai 0 (regola 14/07: i call site distinguono «assente» da zero).

STREAMING
    `with client.messages.stream(...) as s:` (sync, il Capo: solo get_final_message()) e
    `async with ...: async for ev in s` (la chat): gli eventi hanno la forma SDK
    content_block_start / content_block_delta (text_delta, input_json_delta) / content_block_stop,
    ricomposti dai delta OpenAI da `_Ricomposizione` (pura, senza I/O, provata a parte).
    Un chunk con `error` a HTTP 200 (OpenRouter lo fa) solleva; uno stream chiuso senza
    finish_reason solleva APIConnectionError (mai un memo a meta' spacciato per intero).

ERRORI E RETRY
    HTTP >= 400 -> APIStatusError con .status_code (i retry a mano di news/briefing e la logica
    529 dei desk leggono quell'attributo); rete/timeout -> APIConnectionError/APITimeoutError.
    Retry interni come l'SDK: `max_retries` su STATUS_RETRY e sugli errori di connessione, con
    BACKOFF_S; poi si rilancia con la causa verbatim (error.message + metadata di OpenRouter).

MODELLI DAL .ENV (precedenza scritta dal PM)
    chat:        CHAT_<AGENTE>_MODEL se presente, altrimenti CHAT_MODEL
    consigliere: round 0 = CONSIGLIERE_R0_MODEL; R1/R2 = CONSIGLIERE_<DESK>_MODEL o CONSIGLIERE_MODEL
    capo / red_team / reflection / action_extractor / briefing / news_classifier: una variabile
    Variabile BASE assente o vuota = ConfigurazioneLLMMancante col NOME della variabile, al
    momento della chiamata: nessun default nel codice (regola 14/07; le MODEL STRINGS del
    CLAUDE.md vivono nel .env dal 05/09 su ordine del PM).
"""
from bellomberg.core.paths import PROJECT_ROOT
import json
import os
import time
import math
import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

BASE_URL = "https://openrouter.ai/api/v1"
URL_CHAT = BASE_URL + "/chat/completions"
X_TITLE = "Bellomberg"
HTTP_REFERER = "https://bellomberg.local"
TIMEOUT_DEFAULT_S = 600.0
MAX_RETRIES_DEFAULT = 2
# Status che si ritentano (come l'SDK Anthropic: 408/409/429/5xx). 402 (crediti finiti),
# 401 e 400 NON si ritentano: sarebbero lo stesso errore ripetuto a pagamento di tempo.
STATUS_RETRY = (408, 409, 429, 500, 502, 503, 504)
BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 8.0)

# thinking Anthropic -> reasoning OpenRouter. «adaptive» non esiste su OpenRouter: medium e'
# il livello che `{"enabled": true}` accende di default (doc reasoning-tokens, 05/09); per
# Anthropic via OpenRouter vale budget = 0,5 x max_tokens, per Gemini thinkingLevel medium.
REASONING_ADAPTIVE = {"effort": "medium"}
REASONING_DISABLED = {"enabled": False}
# MISURATO 05/09 (sonda, tetto 800, prompt tipo briefing):
#   z-ai/glm-5.3-flash : senza parametro 522 token di ragionamento; QUALUNQUE effort (minimal,
#                        low, medium) o reasoning.max_tokens = 0 token; {"enabled": false} = HTTP 400
#                        «Reasoning is mandatory for this endpoint and cannot be disabled».
#   google/gemini-3.8-flash: senza parametro 764 (tetto raggiunto); minimal/low 0; medium 529; spento = 400.
#   deepseek/deepseek-v4-flash: senza 497; enabled false 0; minimal 469 (!); low 156; medium 85.
# Quindi: «adaptive» su uno slug z-ai/ = NESSUN parametro (e' l'unico modo di accenderlo, il tetto
# resta max_tokens); «disabled» su chi lo rifiuta = `effort: minimal` (0 token misurati), con un
# retry dichiarato la prima volta e memoria del modello per le chiamate dopo.
REASONING_MINIMO = {"effort": "minimal"}
PREFISSI_RAGIONAMENTO_NATIVO = ("z-ai/",)
RAGIONAMENTO_OBBLIGATORIO = set()   # slug che hanno risposto 400 a {"enabled": false} in questo processo
_MSG_RAGIONAMENTO_OBBLIGATORIO = "reasoning is mandatory"
# cache_control conservato solo qui: gli altri provider (Z.ai, DeepSeek, Google) cachano da
# soli e riportano cached_tokens; mandare loro il campo non aggiunge nulla e puo' essere rifiutato.
PREFISSI_CACHE_CONTROL = ("anthropic/",)

VARIABILI_BASE = (
    "OPENROUTER_API_KEY", "CHAT_MODEL", "CHAT_MAX_TOKENS", "CONSIGLIERE_MODEL",
    "CONSIGLIERE_R0_MODEL", "CAPO_MODEL", "RED_TEAM_MODEL", "REFLECTION_MODEL",
    "ACTION_EXTRACTOR_MODEL", "BRIEFING_MODEL", "NEWS_CLASSIFIER_MODEL",
)
_FUNZIONI_SINGOLE = {
    "capo": "CAPO_MODEL",
    "red_team": "RED_TEAM_MODEL",
    "reflection": "REFLECTION_MODEL",
    "action_extractor": "ACTION_EXTRACTOR_MODEL",
    "briefing": "BRIEFING_MODEL",
    "news_classifier": "NEWS_CLASSIFIER_MODEL",
}
# Gli id degli agenti chat (chat_engine.SYSTEM_PROMPTS_BASE) e dei desk (specialists/*.name):
# servono solo all'endpoint `engines` per elencare i modelli risolti, non alla risoluzione.
AGENTI_CHAT = ("capo", "macro", "quant", "options", "fundamentals", "crypto", "eventdesk",
               "politics", "news")
DESK = ("macro", "quant", "options", "fundamentals", "crypto", "eventdesk")


# ============================================================================ errori
class ConfigurazioneLLMMancante(RuntimeError):
    """Variabile del .env assente o vuota: si dichiara col nome, non si ripiega (14/07)."""

    def __init__(self, variabile, dettaglio=None):
        self.variabile = variabile
        msg = (variabile + " assente o vuota nel .env: nessun default nel codice (regola 14/07). "
               "Aggiungi la riga " + variabile + "=... al .env (tabella in .env.example).")
        if dettaglio:
            msg = variabile + ": " + dettaglio
        super().__init__(msg)


class APIError(Exception):
    """Base degli errori del client."""


class APIStatusError(APIError):
    """Risposta HTTP >= 400, o `error` nel corpo (anche a HTTP 200 nello streaming)."""

    def __init__(self, status_code, message, body=None):
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__("HTTP " + str(status_code) + ": " + str(message))


class APIConnectionError(APIError):
    """Rete, stream interrotto, corpo illeggibile."""


class APITimeoutError(APIConnectionError):
    """Timeout del client."""


# ============================================================================ .env
def _letture():
    """OGNI variabile letta, col nome scritto per esteso in una `os.getenv("...")`: e' cosi'
    che tests/test_env_example.py (B1, 02/09) verifica che .env.example documenti ESATTAMENTE
    cio' che il prodotto legge, nei due versi. Una lettura a nome composto sarebbe invisibile."""
    return {
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY"),
        "CHAT_MODEL": os.getenv("CHAT_MODEL"),
        "CHAT_MAX_TOKENS": os.getenv("CHAT_MAX_TOKENS"),
        "CHAT_CAPO_MODEL": os.getenv("CHAT_CAPO_MODEL"),
        "CHAT_MACRO_MODEL": os.getenv("CHAT_MACRO_MODEL"),
        "CHAT_QUANT_MODEL": os.getenv("CHAT_QUANT_MODEL"),
        "CHAT_OPTIONS_MODEL": os.getenv("CHAT_OPTIONS_MODEL"),
        "CHAT_FUNDAMENTALS_MODEL": os.getenv("CHAT_FUNDAMENTALS_MODEL"),
        "CHAT_CRYPTO_MODEL": os.getenv("CHAT_CRYPTO_MODEL"),
        "CHAT_EVENTDESK_MODEL": os.getenv("CHAT_EVENTDESK_MODEL"),
        "CHAT_POLITICS_MODEL": os.getenv("CHAT_POLITICS_MODEL"),
        "CHAT_NEWS_MODEL": os.getenv("CHAT_NEWS_MODEL"),
        "CONSIGLIERE_MODEL": os.getenv("CONSIGLIERE_MODEL"),
        "CONSIGLIERE_R0_MODEL": os.getenv("CONSIGLIERE_R0_MODEL"),
        "CONSIGLIERE_MACRO_MODEL": os.getenv("CONSIGLIERE_MACRO_MODEL"),
        "CONSIGLIERE_QUANT_MODEL": os.getenv("CONSIGLIERE_QUANT_MODEL"),
        "CONSIGLIERE_OPTIONS_MODEL": os.getenv("CONSIGLIERE_OPTIONS_MODEL"),
        "CONSIGLIERE_FUNDAMENTALS_MODEL": os.getenv("CONSIGLIERE_FUNDAMENTALS_MODEL"),
        "CONSIGLIERE_CRYPTO_MODEL": os.getenv("CONSIGLIERE_CRYPTO_MODEL"),
        "CONSIGLIERE_EVENTDESK_MODEL": os.getenv("CONSIGLIERE_EVENTDESK_MODEL"),
        "CAPO_MODEL": os.getenv("CAPO_MODEL"),
        "RED_TEAM_MODEL": os.getenv("RED_TEAM_MODEL"),
        "REFLECTION_MODEL": os.getenv("REFLECTION_MODEL"),
        "ACTION_EXTRACTOR_MODEL": os.getenv("ACTION_EXTRACTOR_MODEL"),
        "BRIEFING_MODEL": os.getenv("BRIEFING_MODEL"),
        "NEWS_CLASSIFIER_MODEL": os.getenv("NEWS_CLASSIFIER_MODEL"),
    }


def _env(nome):
    letture = _letture()
    v = letture[nome] if nome in letture else os.environ.get(nome)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _richiesta(nome):
    v = _env(nome)
    if v is None:
        raise ConfigurazioneLLMMancante(nome)
    return v


def chiave_api():
    return _richiesta("OPENROUTER_API_KEY")


def modello(funzione, agente=None, round_n=None):
    """Slug OpenRouter per la funzione, con la precedenza scritta dal PM (v. docstring)."""
    if funzione == "chat":
        if agente:
            o = _env("CHAT_" + str(agente).upper() + "_MODEL")
            if o:
                return o
        return _richiesta("CHAT_MODEL")
    if funzione == "consigliere":
        if round_n == 0:
            return _richiesta("CONSIGLIERE_R0_MODEL")
        if agente:
            o = _env("CONSIGLIERE_" + str(agente).upper() + "_MODEL")
            if o:
                return o
        return _richiesta("CONSIGLIERE_MODEL")
    var = _FUNZIONI_SINGOLE.get(funzione)
    if var is None:
        raise ValueError("funzione LLM sconosciuta: " + repr(funzione)
                         + " (attese: chat, consigliere, " + ", ".join(_FUNZIONI_SINGOLE) + ")")
    return _richiesta(var)


def chat_max_tokens():
    v = _richiesta("CHAT_MAX_TOKENS")
    try:
        tokens = int(v)
    except ValueError:
        raise ConfigurazioneLLMMancante(
            "CHAT_MAX_TOKENS", "'" + v + "' non e' un intero (tetto per chiamata chat, ragionamento incluso)")
    if tokens <= 0:
        raise ConfigurazioneLLMMancante(
            "CHAT_MAX_TOKENS", "'" + v + "' deve essere un intero positivo (tetto per chiamata chat, ragionamento incluso)")
    return tokens


def variabili_mancanti():
    """Le variabili BASE assenti o vuote, nell'ordine di VARIABILI_BASE (per verifica_config/engines)."""
    return [n for n in VARIABILI_BASE if _env(n) is None]


def somma_costo(precedente, usage):
    """Accumula `usage.cost_usd` di una risposta su un totale per agente/tool-loop.
    None se il totale o la risposta non portano il costo: un totale a meta' spacciato per
    intero e' il fallback silenzioso vietato (14/07); il buco resta dichiarato (None)."""
    c = getattr(usage, "cost_usd", None) if usage is not None else None
    if precedente is None or c is None or isinstance(precedente, bool) or isinstance(c, bool):
        return None
    try:
        valori = (float(precedente), float(c))
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(v) and v >= 0 for v in valori):
        return None
    return sum(valori)


def modello_o_buco(funzione, agente=None, round_n=None):
    """Per l'endpoint `engines`: lo slug risolto, oppure «n.d. (VARIABILE assente)»."""
    try:
        return modello(funzione, agente, round_n)
    except ConfigurazioneLLMMancante as e:
        return "n.d. (" + e.variabile + " assente)"


def somma_usage(precedente, usage):
    """Somma per campo: un buco rimane None anche dopo risposte complete.

    Costo consegnato e completezza dei token sono misure indipendenti.
    None come precedente indica il primo messaggio, non una misura a zero.
    """
    campi = {"in": "input_tokens", "out": "output_tokens",
             "cache_read": "cache_read_input_tokens",
             "cache_write": "cache_creation_input_tokens"}
    totale = {}
    for breve, nome in campi.items():
        n = getattr(usage, nome, None)
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            n = None
        prima = precedente.get(breve) if precedente is not None else 0
        totale[breve] = prima + n if prima is not None and n is not None else None
    totale["cost_usd"] = somma_costo(
        precedente.get("cost_usd") if precedente is not None else 0.0, usage)
    totale["tokens_missing"] = [k for k in campi if totale[k] is None]
    totale["tokens_status"] = "parziale" if totale["tokens_missing"] else "completo"
    return totale


# ============================================================================ oggetti risposta
class TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return "TextBlock(" + repr(self.text[:60]) + ")"


class ToolUseBlock:
    type = "tool_use"

    def __init__(self, id, name, input):
        self.id = id
        self.name = name
        self.input = input

    def __repr__(self):
        return "ToolUseBlock(" + repr(self.id) + ", " + repr(self.name) + ")"


class ThinkingBlock:
    type = "thinking"

    def __init__(self, thinking):
        self.thinking = thinking

    def __repr__(self):
        return "ThinkingBlock(" + repr(self.thinking[:40]) + ")"


class StopDetails:
    type = "refusal"

    def __init__(self, category, explanation=None):
        self.category = category
        self.explanation = explanation


class Usage:
    """Token e costo nella semantica Anthropic (input SENZA la cache), piu' i campi OpenRouter."""

    def __init__(self, input_tokens=None, output_tokens=None, cache_read_input_tokens=None,
                 cache_creation_input_tokens=None, reasoning_tokens=None, cost_usd=None,
                 prompt_tokens=None, completion_tokens=None, reasoning_forzato=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.reasoning_tokens = reasoning_tokens
        self.cost_usd = cost_usd
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        # "minimal" se il chiamante voleva il ragionamento SPENTO ma il modello lo rifiuta
        # (400 «Reasoning is mandatory») e la chiamata e' partita con effort minimal: dichiarato.
        self.reasoning_forzato = reasoning_forzato

    def to_dict(self):
        return {
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "reasoning_tokens": self.reasoning_tokens, "cost_usd": self.cost_usd,
            "reasoning_forzato": self.reasoning_forzato,
        }

    def __repr__(self):
        return "Usage(" + json.dumps(self.to_dict()) + ")"


class Messaggio:
    """La forma che i call site leggono: content[], stop_reason, stop_details, usage."""

    role = "assistant"

    def __init__(self, id, model, content, stop_reason, stop_details=None, usage=None,
                 provider=None, finish_reason=None):
        self.id = id
        self.model = model
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self.usage = usage
        self.provider = provider
        self.finish_reason = finish_reason

    def __repr__(self):
        return ("Messaggio(stop_reason=" + repr(self.stop_reason) + ", blocchi="
                + str(len(self.content)) + ", model=" + repr(self.model) + ")")


# ============================================================================ blocchi (dict o oggetto)
def _tipo(b):
    return b.get("type") if isinstance(b, dict) else getattr(b, "type", None)


def _campo(b, k, default=None):
    if isinstance(b, dict):
        return b.get(k, default)
    return getattr(b, k, default)


def _testo_di(content):
    """Testo di un content Anthropic: stringa, lista di blocchi text, dict o None."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "\n".join(str(_campo(b, "text") or "") for b in content if _tipo(b) == "text")
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


# ============================================================================ richiesta
def _conserva_cache(model):
    return str(model or "").startswith(PREFISSI_CACHE_CONTROL)


def _parte_testo(b, conserva):
    p = {"type": "text", "text": _campo(b, "text") or ""}
    cc = _campo(b, "cache_control")
    if conserva and cc:
        p["cache_control"] = cc
    return p


def _system_openai(system, conserva):
    if system is None:
        return None
    if isinstance(system, str):
        return {"role": "system", "content": system}
    parti = [_parte_testo(b, conserva) for b in system if _tipo(b) == "text"]
    if conserva and any("cache_control" in p for p in parti):
        return {"role": "system", "content": parti}
    return {"role": "system", "content": "\n".join(p["text"] for p in parti)}


def _messaggi_openai(messages, conserva):
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str) or content is None:
            out.append({"role": role, "content": content or ""})
            continue
        if role == "assistant":
            testi, tool_calls = [], []
            for b in content:
                t = _tipo(b)
                if t == "text":
                    testi.append(_campo(b, "text") or "")
                elif t == "tool_use":
                    tool_calls.append({
                        "id": _campo(b, "id"), "type": "function",
                        "function": {"name": _campo(b, "name"),
                                     "arguments": json.dumps(_campo(b, "input") or {},
                                                             ensure_ascii=False, default=str)},
                    })
                # thinking: non si rispedisce (OpenRouter lo conserva via reasoning_details
                # solo nel suo formato; i call site non ne hanno bisogno)
            msg = {"role": "assistant", "content": "\n".join(testi) if testi else None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if msg["content"] is None and not tool_calls:
                msg["content"] = ""
            out.append(msg)
            continue
        # user a blocchi: i tool_result diventano messaggi role=tool (subito dopo l'assistant
        # che li ha chiesti), il testo che segue (nudge) un messaggio user a parte.
        tool_msgs, parti = [], []
        for b in content:
            t = _tipo(b)
            if t == "tool_result":
                tm = {"role": "tool", "tool_call_id": _campo(b, "tool_use_id"),
                      "content": _testo_di(_campo(b, "content"))}
                cc = _campo(b, "cache_control")
                if conserva and cc:
                    tm["content"] = [{"type": "text", "text": tm["content"], "cache_control": cc}]
                tool_msgs.append(tm)
            elif t == "text":
                parti.append(_parte_testo(b, conserva))
        out.extend(tool_msgs)
        if parti:
            if conserva and any("cache_control" in p for p in parti):
                out.append({"role": role, "content": parti})
            else:
                out.append({"role": role, "content": "\n".join(p["text"] for p in parti)})
    return out


def _tools_openai(tools):
    out = []
    for t in tools or []:
        out.append({"type": "function", "function": {
            "name": t.get("name"),
            "description": t.get("description", "") or "",
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }})
    return out


def _tool_choice_openai(tc):
    if tc is None:
        return None
    if isinstance(tc, str):
        return tc
    tipo = tc.get("type")
    if tipo == "none":
        return "none"
    if tipo == "auto":
        return "auto"
    if tipo == "any":
        return "required"
    if tipo == "tool":
        return {"type": "function", "function": {"name": tc.get("name")}}
    raise ValueError("tool_choice non riconosciuto: " + repr(tc))


def _reasoning_openai(thinking, model=""):
    if thinking is None:
        return None
    tipo = thinking.get("type")
    if tipo == "adaptive":
        if str(model).startswith(PREFISSI_RAGIONAMENTO_NATIVO):
            return None   # misurato: su GLM ogni effort azzera il ragionamento; omesso = acceso
        return dict(REASONING_ADAPTIVE)
    if tipo == "disabled":
        if model in RAGIONAMENTO_OBBLIGATORIO:
            return dict(REASONING_MINIMO)   # gia' rifiutato una volta: minimal (0 token misurati)
        return dict(REASONING_DISABLED)
    if tipo == "enabled":
        b = thinking.get("budget_tokens")
        return {"max_tokens": int(b)} if b else {"enabled": True}
    raise ValueError("thinking non riconosciuto: " + repr(thinking))


def costruisci_corpo(model, max_tokens, messages, system=None, tools=None, tool_choice=None,
                     thinking=None, stream=False, **ignorati):
    """Il corpo JSON per chat/completions a partire dai kwargs Anthropic dei call site.
    `extra_headers` e altri kwargs Anthropic-only finiscono in `ignorati` (non partono)."""
    conserva = _conserva_cache(model)
    msgs = []
    s = _system_openai(system, conserva)
    if s is not None:
        msgs.append(s)
    msgs.extend(_messaggi_openai(messages, conserva))
    corpo = {"model": model, "messages": msgs, "max_tokens": int(max_tokens)}
    if tools:
        corpo["tools"] = _tools_openai(tools)
    tc = _tool_choice_openai(tool_choice)
    if tc is not None:
        corpo["tool_choice"] = tc
    r = _reasoning_openai(thinking, model)
    if r is not None:
        corpo["reasoning"] = r
    if stream:
        corpo["stream"] = True
    return corpo


def _voleva_spento(kw):
    return (kw.get("thinking") or {}).get("type") == "disabled"


def _e_ragionamento_obbligatorio(errore, corpo):
    """True se il 400 e' «Reasoning is mandatory…» su una richiesta partita SPENTA."""
    return (isinstance(errore, APIStatusError) and errore.status_code == 400
            and _MSG_RAGIONAMENTO_OBBLIGATORIO in str(errore.message).lower()
            and corpo.get("reasoning") == REASONING_DISABLED)


def _forza_minimal(corpo):
    """Segna il modello e riscrive la richiesta con effort minimal, dichiarandolo nel log."""
    RAGIONAMENTO_OBBLIGATORIO.add(corpo.get("model"))
    corpo["reasoning"] = dict(REASONING_MINIMO)
    print("[llm_client] " + str(corpo.get("model")) + ": il ragionamento non si puo' spegnere "
          "(HTTP 400 «Reasoning is mandatory»): riprovo con effort minimal (0 token misurati "
          "il 05/09) — dichiarato in usage.reasoning_forzato")


def _dichiara_forzato(messaggio, forzato):
    if forzato and messaggio is not None and messaggio.usage is not None:
        messaggio.usage.reasoning_forzato = "minimal"
    return messaggio


# ============================================================================ risposta
def _input_da_arguments(args):
    if isinstance(args, dict):
        return args
    if args is None or args == "":
        return {}
    try:
        v = json.loads(args)
        return v if isinstance(v, dict) else {"__input_parse_error__": True, "__valore__": v}
    except Exception:
        # lo stesso marcatore che chat_engine gestisce: niente dispatch coi default
        return {"__input_parse_error__": True, "__raw__": str(args)[:200]}


def _stop_da_finish(finish, refusal, ha_tool_use):
    if refusal:
        return "refusal", StopDetails("refusal", str(refusal))
    if finish == "content_filter":
        return "refusal", StopDetails("content_filter", None)
    if finish == "length":
        return "max_tokens", None
    if finish == "tool_calls" or (ha_tool_use and finish in (None, "stop")):
        return "tool_use", None
    if finish in (None, "stop"):
        return "end_turn", None
    # valore ignoto: passa verbatim, dichiarato (i call site lo trattano come «altro»)
    return str(finish), None


def _usage_da_json(u):
    if not isinstance(u, dict):
        return None
    ptd = u.get("prompt_tokens_details") or {}
    ctd = u.get("completion_tokens_details") or {}
    pt = u.get("prompt_tokens")
    cached = ptd.get("cached_tokens")
    cw = ptd.get("cache_write_tokens")
    input_tokens = None
    if all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (pt, cached, cw)):
        residuo = pt - cached - cw
        input_tokens = residuo if residuo >= 0 else None
    return Usage(
        input_tokens=input_tokens,
        output_tokens=u.get("completion_tokens"),
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=cw,
        reasoning_tokens=ctd.get("reasoning_tokens"),
        cost_usd=u.get("cost"),
        prompt_tokens=pt,
        completion_tokens=u.get("completion_tokens"),
    )


def _errore_da_corpo(status, corpo):
    """APIStatusError dal JSON d'errore OpenRouter {"error": {"code","message","metadata"}}."""
    err = corpo.get("error") if isinstance(corpo, dict) else None
    if isinstance(err, dict):
        msg = str(err.get("message") or err)
        meta = err.get("metadata")
        if meta:
            msg += " | metadata: " + json.dumps(meta, ensure_ascii=False, default=str)[:400]
        code = err.get("code")
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = status
        return APIStatusError(code if code else status, msg, corpo)
    return APIStatusError(status, str(corpo)[:400] if corpo else "risposta senza corpo", corpo)


def messaggio_da_json(data):
    """Messaggio (forma SDK) da una risposta chat/completions non in streaming."""
    if isinstance(data, dict) and data.get("error") and not data.get("choices"):
        raise _errore_da_corpo(200, data)
    scelte = data.get("choices") or []
    if not scelte:
        raise APIConnectionError("risposta senza choices: " + json.dumps(data, default=str)[:300])
    scelta = scelte[0]
    msg = scelta.get("message") or {}
    blocchi = []
    r = msg.get("reasoning")
    if r:
        blocchi.append(ThinkingBlock(str(r)))
    c = msg.get("content")
    if isinstance(c, str):
        if c:
            blocchi.append(TextBlock(c))
    elif isinstance(c, list):
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text"):
                blocchi.append(TextBlock(p["text"]))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        blocchi.append(ToolUseBlock(id=tc.get("id"), name=fn.get("name"),
                                    input=_input_da_arguments(fn.get("arguments"))))
    ha_tool = any(b.type == "tool_use" for b in blocchi)
    stop, det = _stop_da_finish(scelta.get("finish_reason"), msg.get("refusal"), ha_tool)
    return Messaggio(id=data.get("id"), model=data.get("model"), content=blocchi,
                     stop_reason=stop, stop_details=det, usage=_usage_da_json(data.get("usage")),
                     provider=data.get("provider"), finish_reason=scelta.get("finish_reason"))


# ============================================================================ streaming
def _evento(tipo, **campi):
    return SimpleNamespace(type=tipo, **campi)


class _Ricomposizione:
    """Stato dello stream: ricompone i blocchi Anthropic dai delta OpenAI ed emette gli
    eventi SDK (content_block_start/delta/stop). Pura: riceve chunk gia' decodificati."""

    def __init__(self):
        self.blocchi = []          # dict: {"type": "text", "text"} | {"type": "tool_use", id, name, args}
        self.reasoning = []
        self.refusal = None
        self.finish = None
        self.usage = None
        self.id = None
        self.model = None
        self.provider = None
        self._corrente = None      # indice del blocco aperto
        self._tool_idx = {}        # index OpenAI -> indice blocco
        self._eventi = []

    def _chiudi(self):
        if self._corrente is not None:
            self._eventi.append(_evento("content_block_stop", index=self._corrente))
            self._corrente = None

    def _apri_testo(self):
        self.blocchi.append({"type": "text", "text": ""})
        i = len(self.blocchi) - 1
        self._corrente = i
        self._eventi.append(_evento("content_block_start", index=i,
                                    content_block=SimpleNamespace(type="text", text="")))

    def alimenta(self, chunk):
        """Un chunk JSON dello stream -> lista di eventi SDK. Solleva su `error`."""
        if isinstance(chunk, dict) and chunk.get("error"):
            raise _errore_da_corpo(200, chunk)
        self.id = chunk.get("id") or self.id
        self.model = chunk.get("model") or self.model
        self.provider = chunk.get("provider") or self.provider
        if isinstance(chunk.get("usage"), dict):
            self.usage = chunk["usage"]
        for scelta in chunk.get("choices") or []:
            delta = scelta.get("delta") or {}
            r = delta.get("reasoning")
            if r:
                self.reasoning.append(str(r))
            c = delta.get("content")
            if c:
                if self._corrente is None or self.blocchi[self._corrente]["type"] != "text":
                    self._chiudi()
                    self._apri_testo()
                self.blocchi[self._corrente]["text"] += c
                self._eventi.append(_evento("content_block_delta", index=self._corrente,
                                            delta=SimpleNamespace(type="text_delta", text=c)))
            rf = delta.get("refusal")
            if rf:
                self.refusal = (self.refusal or "") + str(rf)
            for tc in delta.get("tool_calls") or []:
                oi = tc.get("index", 0)
                fn = tc.get("function") or {}
                if oi not in self._tool_idx:
                    self._chiudi()
                    self.blocchi.append({"type": "tool_use", "id": tc.get("id") or "",
                                         "name": fn.get("name") or "", "args": ""})
                    bi = len(self.blocchi) - 1
                    self._tool_idx[oi] = bi
                    self._corrente = bi
                    self._eventi.append(_evento(
                        "content_block_start", index=bi,
                        content_block=SimpleNamespace(type="tool_use", id=self.blocchi[bi]["id"],
                                                      name=self.blocchi[bi]["name"], input={})))
                bi = self._tool_idx[oi]
                if self._corrente != bi:
                    self._chiudi()
                    self._corrente = bi
                if fn.get("name") and not self.blocchi[bi]["name"]:
                    self.blocchi[bi]["name"] = fn["name"]
                if tc.get("id") and not self.blocchi[bi]["id"]:
                    self.blocchi[bi]["id"] = tc["id"]
                frag = fn.get("arguments")
                if frag:
                    self.blocchi[bi]["args"] += frag
                    self._eventi.append(_evento(
                        "content_block_delta", index=bi,
                        delta=SimpleNamespace(type="input_json_delta", partial_json=frag)))
            fr = scelta.get("finish_reason")
            if fr:
                self.finish = fr
                self._chiudi()
        ev, self._eventi = self._eventi, []
        return ev

    def fine(self):
        self._chiudi()
        ev, self._eventi = self._eventi, []
        return ev

    def messaggio(self):
        if self.finish is None:
            raise APIConnectionError("stream chiuso senza finish_reason: risposta INCOMPLETA "
                                     "(blocchi ricevuti: " + str(len(self.blocchi)) + ")")
        blocchi = []
        if self.reasoning:
            blocchi.append(ThinkingBlock("".join(self.reasoning)))
        for b in self.blocchi:
            if b["type"] == "text":
                if b["text"]:
                    blocchi.append(TextBlock(b["text"]))
            else:
                blocchi.append(ToolUseBlock(id=b["id"], name=b["name"],
                                            input=_input_da_arguments(b["args"])))
        ha_tool = any(b.type == "tool_use" for b in blocchi)
        stop, det = _stop_da_finish(self.finish, self.refusal, ha_tool)
        return Messaggio(id=self.id, model=self.model, content=blocchi, stop_reason=stop,
                         stop_details=det, usage=_usage_da_json(self.usage),
                         provider=self.provider, finish_reason=self.finish)


def _payload_sse(riga):
    """La riga SSE -> chunk JSON, None se da ignorare, "[DONE]" alla fine."""
    if not riga:
        return None
    riga = riga.strip()
    if not riga or riga.startswith(":"):
        return None
    if not riga.startswith("data:"):
        return None
    payload = riga[5:].strip()
    if payload == "[DONE]":
        return "[DONE]"
    try:
        return json.loads(payload)
    except Exception:
        raise APIConnectionError("chunk SSE illeggibile: " + payload[:200])


# ============================================================================ HTTP
def _intestazioni(chiave):
    return {"Authorization": "Bearer " + chiave, "Content-Type": "application/json",
            "HTTP-Referer": HTTP_REFERER, "X-Title": X_TITLE}


def _nuovo_client_http(timeout, trasporto):
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0), transport=trasporto)


def _nuovo_client_http_async(timeout, trasporto):
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0), transport=trasporto)


def _ritentabile(status):
    return status in STATUS_RETRY


def _pausa(tentativo):
    return BACKOFF_S[min(tentativo, len(BACKOFF_S) - 1)]


def _decodifica(resp):
    try:
        return resp.json()
    except Exception:
        return None


class _Messages:
    def __init__(self, client):
        self._c = client

    def create(self, **kw):
        corpo = costruisci_corpo(stream=False, **kw)
        forzato = _voleva_spento(kw) and corpo.get("reasoning") == REASONING_MINIMO
        try:
            data = self._c._post_json(corpo)
        except APIStatusError as e:
            if not _e_ragionamento_obbligatorio(e, corpo):
                raise
            _forza_minimal(corpo)
            forzato = True
            data = self._c._post_json(corpo)
        return _dichiara_forzato(messaggio_da_json(data), forzato)

    def stream(self, **kw):
        corpo = costruisci_corpo(stream=True, **kw)
        return _StreamSync(self._c, corpo, forzato=_voleva_spento(kw)
                           and corpo.get("reasoning") == REASONING_MINIMO)


class OpenRouterClient:
    """Client SINCRONO. Stessa firma d'uso dell'SDK: `.messages.create(**kw)` / `.messages.stream(**kw)`.
    `trasporto` serve SOLO ai test (httpx.MockTransport): in produzione resta None."""

    def __init__(self, api_key=None, timeout=TIMEOUT_DEFAULT_S, max_retries=MAX_RETRIES_DEFAULT,
                 trasporto=None):
        self.api_key = api_key or chiave_api()
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self._http = _nuovo_client_http(self.timeout, trasporto)
        self.messages = _Messages(self)

    # -- una POST con retry sugli status/errori transitori; ritorna la Response (status < 400)
    def _invia(self, corpo, stream=False):
        ultimo = None
        for tentativo in range(self.max_retries + 1):
            try:
                req = self._http.build_request("POST", URL_CHAT, json=corpo,
                                               headers=_intestazioni(self.api_key))
                resp = self._http.send(req, stream=stream)
            except httpx.TimeoutException as e:
                ultimo = APITimeoutError("timeout dopo " + str(self.timeout) + " s: " + str(e))
            except httpx.HTTPError as e:
                ultimo = APIConnectionError(type(e).__name__ + ": " + str(e))
            else:
                if resp.status_code < 400:
                    return resp
                if stream:
                    resp.read()
                ultimo = _errore_da_corpo(resp.status_code, _decodifica(resp))
                resp.close()
                if not _ritentabile(resp.status_code):
                    raise ultimo
            if tentativo < self.max_retries:
                time.sleep(_pausa(tentativo))
        raise ultimo

    def _post_json(self, corpo):
        resp = self._invia(corpo, stream=False)
        data = _decodifica(resp)
        if data is None:
            raise APIConnectionError("risposta non JSON (HTTP " + str(resp.status_code) + "): "
                                     + resp.text[:200])
        return data


class _StreamSync:
    def __init__(self, client, corpo, forzato=False):
        self._c = client
        self._corpo = corpo
        self._forzato = forzato
        self._resp = None
        self._ric = _Ricomposizione()
        self._esaurito = False

    def __enter__(self):
        try:
            self._resp = self._c._invia(self._corpo, stream=True)
        except APIStatusError as e:
            if not _e_ragionamento_obbligatorio(e, self._corpo):
                raise
            _forza_minimal(self._corpo)
            self._forzato = True
            self._resp = self._c._invia(self._corpo, stream=True)
        return self

    def __exit__(self, *a):
        if self._resp is not None:
            self._resp.close()
        return False

    def __iter__(self):
        if self._resp is None:
            raise RuntimeError("usa `with client.messages.stream(...) as s:`")
        if self._esaurito:
            return
        try:
            for riga in self._resp.iter_lines():
                chunk = _payload_sse(riga)
                if chunk is None:
                    continue
                if chunk == "[DONE]":
                    break
                for ev in self._ric.alimenta(chunk):
                    yield ev
        except httpx.HTTPError as e:
            raise APIConnectionError("stream interrotto: " + type(e).__name__ + ": " + str(e))
        finally:
            self._esaurito = True
        for ev in self._ric.fine():
            yield ev

    def get_final_message(self):
        for _ in self:
            pass
        return _dichiara_forzato(self._ric.messaggio(), self._forzato)


class _MessagesAsync:
    def __init__(self, client):
        self._c = client

    async def create(self, **kw):
        corpo = costruisci_corpo(stream=False, **kw)
        forzato = _voleva_spento(kw) and corpo.get("reasoning") == REASONING_MINIMO
        try:
            data = await self._c._post_json(corpo)
        except APIStatusError as e:
            if not _e_ragionamento_obbligatorio(e, corpo):
                raise
            _forza_minimal(corpo)
            forzato = True
            data = await self._c._post_json(corpo)
        return _dichiara_forzato(messaggio_da_json(data), forzato)

    def stream(self, **kw):
        corpo = costruisci_corpo(stream=True, **kw)
        return _StreamAsync(self._c, corpo, forzato=_voleva_spento(kw)
                            and corpo.get("reasoning") == REASONING_MINIMO)


class AsyncOpenRouterClient:
    """Client ASINCRONO (la chat SSE): `await .messages.create(**kw)` /
    `async with .messages.stream(**kw) as s: async for ev in s`."""

    def __init__(self, api_key=None, timeout=TIMEOUT_DEFAULT_S, max_retries=MAX_RETRIES_DEFAULT,
                 trasporto=None):
        self.api_key = api_key or chiave_api()
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self._http = _nuovo_client_http_async(self.timeout, trasporto)
        self.messages = _MessagesAsync(self)

    async def _invia(self, corpo, stream=False):
        ultimo = None
        for tentativo in range(self.max_retries + 1):
            try:
                req = self._http.build_request("POST", URL_CHAT, json=corpo,
                                               headers=_intestazioni(self.api_key))
                resp = await self._http.send(req, stream=stream)
            except httpx.TimeoutException as e:
                ultimo = APITimeoutError("timeout dopo " + str(self.timeout) + " s: " + str(e))
            except httpx.HTTPError as e:
                ultimo = APIConnectionError(type(e).__name__ + ": " + str(e))
            else:
                if resp.status_code < 400:
                    return resp
                if stream:
                    await resp.aread()
                ultimo = _errore_da_corpo(resp.status_code, _decodifica(resp))
                await resp.aclose()
                if not _ritentabile(resp.status_code):
                    raise ultimo
            if tentativo < self.max_retries:
                await asyncio.sleep(_pausa(tentativo))
        raise ultimo

    async def _post_json(self, corpo):
        resp = await self._invia(corpo, stream=False)
        data = _decodifica(resp)
        if data is None:
            raise APIConnectionError("risposta non JSON (HTTP " + str(resp.status_code) + "): "
                                     + resp.text[:200])
        return data


class _StreamAsync:
    def __init__(self, client, corpo, forzato=False):
        self._c = client
        self._corpo = corpo
        self._forzato = forzato
        self._resp = None
        self._ric = _Ricomposizione()
        self._esaurito = False

    async def __aenter__(self):
        try:
            self._resp = await self._c._invia(self._corpo, stream=True)
        except APIStatusError as e:
            if not _e_ragionamento_obbligatorio(e, self._corpo):
                raise
            _forza_minimal(self._corpo)
            self._forzato = True
            self._resp = await self._c._invia(self._corpo, stream=True)
        return self

    async def __aexit__(self, *a):
        if self._resp is not None:
            await self._resp.aclose()
        return False

    async def __aiter__(self):
        if self._resp is None:
            raise RuntimeError("usa `async with client.messages.stream(...) as s:`")
        if self._esaurito:
            return
        try:
            async for riga in self._resp.aiter_lines():
                chunk = _payload_sse(riga)
                if chunk is None:
                    continue
                if chunk == "[DONE]":
                    break
                for ev in self._ric.alimenta(chunk):
                    yield ev
        except httpx.HTTPError as e:
            raise APIConnectionError("stream interrotto: " + type(e).__name__ + ": " + str(e))
        finally:
            self._esaurito = True
        for ev in self._ric.fine():
            yield ev

    async def get_final_message(self):
        async for _ in self:
            pass
        return _dichiara_forzato(self._ric.messaggio(), self._forzato)


# ---------------------------------------------------------------------------
# SONDA DEI MODELLI (audit 11/09, Fable 5.1 — run 10/09 memo #53): il modello del red
# team era respinto da OpenRouter (HTTP 403, attestazione 18+) e lo si e' scoperto a
# meta' run, a Round 0 e 1 gia' pagati. Una call da pochi token per slug distinto,
# PRIMA del Round 0; esito dichiarato per slug, nessuna eccezione propagata, nessun
# ritentativo (un 403 e' lo stesso errore ripetuto). Non cambia nessuna model string.
# ---------------------------------------------------------------------------

def sonda_modelli(slugs, client=None, max_tokens=5, timeout_s=45.0):
    """{slug: {"ok": bool, "motivo": None|str, "durata_s": float}} per ogni slug distinto
    (ordine di prima apparizione). `client` finto nei test; in produzione OpenRouterClient
    senza retry: la sonda misura, non insiste."""
    esiti = {}
    distinti = []
    for s in slugs or []:
        s = str(s or "").strip()
        if s and s not in distinti:
            distinti.append(s)
    if not distinti:
        return esiti
    if client is None:
        client = OpenRouterClient(timeout=timeout_s, max_retries=0)
    for s in distinti:
        t0 = time.perf_counter()
        try:
            client.messages.create(model=s, max_tokens=int(max_tokens),
                                   messages=[{"role": "user", "content": "ping"}],
                                   thinking={"type": "disabled"})
            esiti[s] = {"ok": True, "motivo": None, "durata_s": round(time.perf_counter() - t0, 2)}
        except Exception as e:
            esiti[s] = {"ok": False,
                        "motivo": (type(e).__name__ + ": " + str(e))[:300],
                        "durata_s": round(time.perf_counter() - t0, 2)}
    return esiti


def righe_log_sonda(esiti):
    """Righe di log '[OK] slug (0.8s)' / '[KO] slug: motivo', una per slug."""
    righe = []
    for s, e in (esiti or {}).items():
        if e.get("ok"):
            righe.append("[OK] modello %s (%ss)" % (s, e.get("durata_s", "?")))
        else:
            righe.append("[KO] modello %s: %s" % (s, e.get("motivo") or "causa n.d."))
    return righe
