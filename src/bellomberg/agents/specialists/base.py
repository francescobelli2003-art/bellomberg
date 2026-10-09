"""
BELLOMBERG - Base class per gli specialisti del multi-agent consigliere.

NEW IN PHASE 1: Persistent memory integration.
Ogni specialista riceve nel context:
- I suoi ultimi 3 report (round 2 finalizzati)
- Le 5 raccomandazioni più recenti del Capo con status/outcome
- I 5 feedback più recenti del PM (filtrati per il proprio dominio quando rilevante)

Salvataggio:
- Ogni round 2 finalizzato viene salvato in specialist_reports
- Permette evoluzione tesi week-on-week
"""
import json
import os
import threading
import time
from hashlib import sha256
from copy import deepcopy
from functools import wraps
from datetime import datetime, timezone
from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm, somma_usage as _somma_usage
# Classe reale catturata all'import: i test che sostituiscono OpenRouterClient con una
# factory non devono rompere il controllo isinstance di _chiama_modello.
_OPENROUTER_CLIENT_CLASS = OpenRouterClient
from bellomberg.core.llm_client import thinking_consigliere, request_scope, ConfigurazioneLLMMancante
from bellomberg.core.trade_idea_policy import role_thinking
from bellomberg.market_data.freschezza_trimestrale import as_of_freschezza as _as_of_freschezza  # cutoff della run (R-CASCATA 07/10)
from bellomberg.storage.memory_db import DB_DIR   # B4 (02/09): heartbeat e rescue sotto la cartella dati
from bellomberg.core.llm_refusal import REFUSAL_TAG, refusal_reason as _refusal_reason
from bellomberg.core.language import capture_language, prompt_for_language, scoped_language
from bellomberg.core.research_analysis import is_research_mode, research_context
from bellomberg.agents import agent_tools


# 05/09 (ordine PM): i modelli dei desk vivono nel .env e si risolvono PER DESK E PER ROUND
# con llm_client.modello("consigliere", desk, round_n): R0 = CONSIGLIERE_R0_MODEL (ripipeline
# 15/07, ok PM: il round 0 e' pura raccolta dati), R1/R2 = CONSIGLIERE_<DESK>_MODEL se
# presente, altrimenti CONSIGLIERE_MODEL. Variabile assente = errore col nome, non piu' il
# ripiego su claude-sonnet-5 che c'era qui. Storia: DEFAULT_MODEL claude-opus-5 dal 26/07.
# 200a: PROMPT CACHING - tools+system sono identici per ~30 chiamate per agente a run:
# cache write 2x (ttl 1h), read 0.1x -> input ripetuto ~-90%. Kill-switch qui sotto.
USE_PROMPT_CACHING = True
CACHE_TTL = "1h"   # "1h" richiede il beta header; "5m" e' il default senza header
CACHE_BETA_HEADER = {"anthropic-beta": "extended-cache-ttl-2025-04-11"}
# PM 02/10: increase every specialist phase, including R0 and consultations.
# Quant R1 exhausted 16k (15,997 reasoning tokens) without a visible report.
# This is an output ceiling, not a spending grant; model, effort, per-request
# budget checks and tool-iteration limits retain their existing contracts.
MAX_TOKENS_SPECIALIST = 128000
MAX_TOKENS_TRADE_IDEA_AUTHOR = 128000
MAX_TOKENS_FUNDAMENTALS_ANALYSIS = 128000
# audit 11/09: testa dell'esito di ogni tool nel tool_log (heartbeat + blackboard archiviata),
# per l'audit; il modello riceve l'esito intero come prima (TETTO_TOOL_RESULT di chat_tools)
TOOL_LOG_OUTPUT_MAX = 400


def _trade_idea_tool_receipt_success(result, tool_name, *, truncated=False):
    """Only complete, affirmative tool envelopes can back operative evidence."""
    if not isinstance(result, dict) or truncated:
        return False
    payload = result.get("data", result)
    if not isinstance(payload, dict) or not payload:
        return False
    negative = {"error", "failed", "failure", "unavailable", "stale", "ko",
                "partial", "incomplete", "blocked", "not_found"}
    for row in (result, payload):
        if (row.get("ok") is False or row.get("error")
                or row.get("stale") is True or row.get("truncated") is True
                or row.get("partial") is True
                or str(row.get("status") or "").lower() in negative):
            return False
    if tool_name == "get_valuation":
        from bellomberg.valuation.trade_idea_model import candidate_model_usability
        return candidate_model_usability(payload)["usable"] is True
    return True

# Il timeout del client SEGUE il cap (review 27/08, finding ALTO per costi/API):
# a 65-80 tok/s misurati in V9 (la call troncata da 12k e' durata <= 203 s), a
# 16k una call vale 200-250 s — contro i 240 s di #196 (giugno: «una chiamata
# appesa fallisce e la run prosegue»), e sul timeout l'SDK ritenta una volta e
# il desk perde il report INTERO, non un pezzo. L'SDK stesso, per il
# non-streaming, stima 3600 x max_tokens / 128000 (= 450 s a 16k): lo stesso
# numero, con il pavimento di #196. Legame MECCANICO in una FUNZIONE provata su
# piu' cap (tests/test_tetto_specialisti_16k.py): a 16k «450 fisso» sarebbe
# indistinguibile, sono il pavimento e la formula sugli altri cap a misurarlo.
def _checkpoint_json(value):
    """Lossless SDK blocks for a native specialist continuation."""
    def block(item):
        kind = getattr(item, "type", None)
        if kind == "text":
            return {"type": kind, "text": item.text}
        if kind == "tool_use":
            return {"type": kind, "id": item.id, "name": item.name, "input": item.input}
        if kind == "thinking":
            return {"type": kind, "thinking": item.thinking}
        raise TypeError("unsupported checkpoint object: " + type(item).__name__)
    return json.loads(json.dumps(value, default=block, ensure_ascii=False, allow_nan=False))


def _checkpoint_digest(value):
    return sha256(json.dumps(_checkpoint_json(value), sort_keys=True,
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _checkpoint_output_contract(fields, saved):
    """Recognize only a complete historical contract, including its original cap.

    A policy upgrade applies to new stages. A saved stage keeps the exact body
    needed to recover its paid response; no other contract difference is waived.
    """
    cap = fields["max_tokens"]
    digest = _checkpoint_digest(fields)
    if saved is None:
        return cap, digest
    candidates = (cap, 16000, 64000, 65536)
    pinned = saved.get("max_tokens")
    for candidate in dict.fromkeys(candidates):
        if pinned is not None and (type(pinned) is not int or pinned != candidate):
            continue
        candidate_digest = _checkpoint_digest({**fields, "max_tokens": candidate})
        if saved.get("contract") == candidate_digest:
            return candidate, candidate_digest
    raise ValueError("specialist checkpoint identity, sources or contract differs")


def _forced_report_nudge(max_tool_iters):
    return ("LIMITE ITERAZIONI TOOL RAGGIUNTO (" + str(max_tool_iters)
            + "): in questa risposta i tool sono DISABILITATI. Scrivi ORA il "
            "report finale con le informazioni gia' raccolte. Dichiara "
            "esplicitamente cio' che NON hai potuto verificare (n.d.), "
            "senza inventare numeri.")


def _persist_specialist_checkpoint(blackboard, key, payload, event):
    callback = getattr(blackboard, "persist_run_checkpoint", None)
    if not callable(callback):
        return
    state = _checkpoint_json(payload)
    state["sha256"] = _checkpoint_digest(state)
    with blackboard._lock:
        blackboard.specialist_checkpoints[key] = state
        # A failed checkpoint is a failed prerequisite for the next paid call.
        callback(event, {"desk_key": key, "checkpoint_sha256": state["sha256"]})


def _record_run_failure(blackboard, error, desk, round_n):
    callback = getattr(blackboard, "record_run_failure", None)
    if callable(callback):
        callback(error, desk=desk, round_n=round_n,
                 request_id=getattr(error, "request_id", None))


def timeout_specialisti(cap: int) -> float:
    """Timeout (s) del client per una call NON in streaming con `max_tokens=cap`:
    la stima dell'SDK (3600 x cap / 128000), mai sotto i 240 s di #196."""
    return max(240.0, 3600.0 * cap / 128000)


TIMEOUT_SPECIALIST_S = timeout_specialisti(MAX_TOKENS_SPECIALIST)

# Cap del blocco YOUR MEMORY. 8.000 -> 12.000 il 21/08: era un letterale nudo, e la
# cura alle parole del PM dello stesso giorno l'aveva reso VINCOLANTE senza che
# nessuno lo notasse. Misurato dopo la cura: il blocco pesa 6.543 char, quindi con
# 8.000 restavano 1.457 — MENO di un singolo commento a policy piena
# (memory_db.MAX_CHAR_FEEDBACK_PM = 2.000). Non tagliava oggi, ma sarebbe bastato il
# 5o commento nuovo da 312 char (o il 12o da 87) per far cadere il TRACK RECORD.
# E' esattamente la lezione del 20/08 — «alzare un limite senza il cap sposta solo
# il punto del taglio» — ripetuta dalla review avversariale su questa stessa cura.
# Il legame fra i due numeri e' MECCANICO: tests/test_parole_pm_intere.py.
MAX_CHAR_MEMORIA_SPECIALISTA = 12000

# Finestra della BLACKBOARD nel preambolo R1/R2 — i report degli ALTRI desk.
# Era il letterale 9.000 in DUE righe, e il commento che lo descriveva
# (summary_for_specialist) ne citava un TERZO, 6.000, rimasto dal 15/07.
# Misurato sulla run vera (memo #50, audit/25): il contenuto pesa 57.084-67.312
# char per richiedente, quindi passava il 13-16% — MUTO. Fundamentals (19.031),
# quant e options non comparivano MAI nel preambolo di nessuno, nemmeno col nome
# della chiave: i tre desk che decidono nomi, size e strutture si scrivevano
# addosso senza leggersi, e la barriera di pipeline del 15/07 esisteva nel codice
# ma non nel prompt. Chi sopravviveva lo decideva l'ordine di FINE del round 0
# (4 worker in parallelo): una corsa fra thread sceglieva cosa legge chi alloca.
# PM 21/08, rosa (i)/(ii): scelta (i) PIENO — i report arrivano interi.
# COSTO, e il primo numero che ho dato al PM era SBAGLIATO di 3,5x: +496.238 char
# per run sui 9 preamboli (stima per ECCESSO — in R1 la blackboard porta i R0, che
# sono piu' corti dei R2 usati per la misura, e i R0 non sono persistiti a DB).
# Quei caratteri NON si pagano una volta sola: il blocco sta nel PRIMO messaggio
# user, che viene rispedito a OGNI giro del tool-loop. Alla 1a chiamata e' input
# pieno (1x); alla 2a `_move_cache_breakpoint` sposta il breakpoint sull'ultimo
# tool_result e il prefisso viene scritto in cache (2x); dalla 3a in poi e'
# cache_read (0,1x). Moltiplicatore = 1 + 2 + 0,1*(N-2), con N = chiamate API per
# agente-round: 3,40x a N=6, 3,80x a N=10. Quindi il costo vero e' 1,94-2,17 EUR
# a run, il 17-19% di una run da 11,23 — non 0,57 EUR e non il 5%.
# Riduzione possibile SENZA toccare la scelta del PM, da valutare a parte: mettere
# un cache_control anche sul primo messaggio user quando e' una stringa (il blocco
# diventerebbe 2x una volta + 0,1x per il resto).
# Il tetto resta come CINTURA contro un report fuori scala; quando morde, la
# quota si divide fra i desk e il taglio si dichiara per desk (v. _blocco_blackboard).
MAX_CHAR_BLACKBOARD = 120000
# (MAX_TOKENS_CAPO rimossa 26/07, quick-win audit/21 §6 n.3: era morta — il
# Capo usa il suo CAPO_MAX_TOKENS in capo.py — 64000 dal 01/08, voce 1 —, questa non era letta da nessuno)
MAX_TOOL_ITERS_SPECIALIST = 10
# Autore Trade Idea: spazio per fonti, salvataggi e correzioni; ultima chiamata report.
MAX_TOOL_ITERS_TRADE_IDEA_AUTHOR = 30
MODEL_AUTHORING_LOCAL_TOOLS = frozenset({
    "read_company_dossier", "read_candidate_source", "get_candidate_model_inputs",
    "read_candidate_model_consultation", "submit_candidate_model_plan", "get_valuation",
    "read_blackboard",
})
RESEARCH_UNAVAILABLE_TOOLS = frozenset({
    "get_valuation", "build_dcf_model", "get_candidate_model_inputs", "submit_candidate_model_plan",
    "review_candidate_model", "read_candidate_model_consultation",
})
RESEARCH_REPLY_LOCAL_TOOLS = frozenset({
    'respond_trade_idea_objection', 'read_candidate_source', 'read_company_dossier', 'ask_specialist',
})
# Resilienza R0/R1 (fix 1 §9-sextrigies, ok PM 03/08). Due guasti misurati:
# V7 03/08 fundamentals morto alla PRIMA chiamata su HTTP 529 senza retry;
# V6 28/07 eventdesk end_turn con 113 char di annuncio e 0 tool promosso a
# report. L'annuncio-senza-tool riceve UN nudge e, se ricade, esce col marcatore
# dichiarato (mai un "done (113 chars)" zitto - regola 14/07). Il vecchio retry
# del desk sul 529 (RETRY_529_BACKOFF_S) e' stato tolto (G7, 04/10): vedi sotto.
# Il contatore _retry_529 resta solo perche' i checkpoint dei desk lo portano
# (riprese di run vecchie): nel codice di oggi vale sempre 0.
# Rete (decisione maintainer, integrazione 04/10): NESSUN retry di rete al desk. L'unico
# strato di retry e' core/llm_client (_RetryBudget: STATUS_RETRY + errori di connessione
# prima della risposta, BACKOFF_S; mai a stream iniziato) e sotto un request_scope (journal)
# e' spento: una
# richiesta che puo' essere stata pagata non parte due volte. Lo streaming del desk
# (_chiama_modello) resta la cura della connessione muta delle run 28/09 e 01/10.
SOGLIA_COLLASSO_ANNUNCIO = 800       # end_turn con 0 tool sotto questa soglia = annuncio
# Testa dei due marcatori che dichiarano «questo round non ha usato i tool». Costante e
# non letterale copiato: il marcatore generico (round senza tool, 12/09) NON si impila
# sopra quello del collasso, che dice lo stesso fatto con piu' dettaglio — e il predicato
# che li tiene insieme dev'essere uno solo (lezione: i letterali copiati si scollegano).
MARCATORE_COLLASSO = "[COLLASSO ANNUNCIO-SENZA-TOOL"


def report_specialista_utilizzabile(report):
    """True solo per contenuto che puo' valere come report o materiale grezzo.

    Un solo criterio condiviso evita che Blackboard persista una risposta che il
    Capo considera fallita (o, viceversa, che il Capo recuperi un rifiuto come se
    fosse ricerca preliminare).
    """
    testo = str(report or "")
    pulito = testo.strip()
    testa = pulito[:200]
    return bool(pulito) and not (
        testa.startswith("[ERROR")
        or "No output produced in round" in testa
        or testa.startswith("[COLLASSO ANNUNCIO-SENZA-TOOL")
        or REFUSAL_TAG in testa
    )


# Gravita' degli status di usage (semantica buchi, PM 15/07): l'aggregato per-agente
# e il totale prendono SEMPRE il caso peggiore tra i round. "ok" e' l'unico status
# che autorizza a presentare un costo come affidabile.
USAGE_STATUS_SEVERITY = {
    "ok": 0,
    "usage_unknown": 1,       # token IGNOTI (l'API non ha esposto usage): != zero
    "model_unknown": 2,       # modello fuori listino -> costo non calcolabile
    "pricing_unavailable": 3, # llm_pricing non importabile -> costo non calcolabile
    "api_error": 4,           # l'agente e' proprio morto
}


def _worse_status(a, b):
    """Il PEGGIORE dei due status. Uno status ignoto e' trattato come il piu' grave
    noto meno uno: mai silenziato a 'ok'."""
    sa = USAGE_STATUS_SEVERITY.get(a, 3)
    sb = USAGE_STATUS_SEVERITY.get(b, 3)
    return a if sa >= sb else b


def _fmt_cost(v):
    """Costo in euro per le righe di log del PM. Delega a llm_pricing.format_eur;
    se il modulo manca NON inventa uno zero: stampa il valore grezzo o 'n.d.'."""
    try:
        from bellomberg.core.llm_pricing import format_eur
        return format_eur(v)
    except Exception:
        return "n.d." if v is None else str(v)


# =============================================================================
# ESITO VERO DELLA RUN per la pagina Agenti in diretta (handoff-4 voce 2, 05/10, Opus 5.5)
# =============================================================================
# La pagina deduceva «Completata · il comitato ha consegnato il memo» da due segnali
# che NON sono l'esito: `running` passato da true a false e `memo_id` presente. Ma il
# memo_id esiste dal primo secondo della run (la riga memos nasce '[IN PROGRESS]'),
# quindi una run FERMA (operational_status blocked) si leggeva come consegnata.
# `esito_run` legge l'esito dai campi che lo dicono davvero, in quest'ordine:
#   1. running=true                          -> in_corso / in_corso_senza_segnale
#   2. registro della run settimanale        -> status + operational_status + analytical/artifact
#      (li fonde nel heartbeat _weekly_terminal_heartbeat di consigliere_multi)
#   3. heartbeat di crash (`status: failed`) -> fallita
#   4. technical_status di mark_run_complete -> Trade Idea e chiusure senza registro
#   5. nient'altro                           -> sconosciuta, DICHIARATA (mai «completata» di ripiego)
# e lo incrocia col memo nel DB: un memo ancora '[IN PROGRESS]' non e' mai «consegnato».
ESITI_RUN = ("in_corso", "in_corso_senza_segnale", "completata", "incompleta", "bloccata",
             "fallita", "annullata", "interrotta", "nessuna_run", "sconosciuta")
STATI_MEMO_DB = ("scritto", "in_corso", "assente", "illeggibile", "non_letto")


def stato_memo_nel_db(memo_id, db_path):
    """Stato della riga `memos` della run, letto in SOLA LETTURA.
    'in_corso' = full_markdown ancora '[IN PROGRESS]' (create_run lo scrive cosi' e solo il
    Capo lo sostituisce); 'scritto' = qualunque altro testo; 'assente' = nessuna riga;
    'illeggibile' = DB non apribile; 'non_letto' = nessun memo_id da cercare."""
    if memo_id is None or isinstance(memo_id, bool):
        return "non_letto"
    try:
        import sqlite3
        from pathlib import Path
        from contextlib import closing
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=2)) as cx:
            row = cx.execute("SELECT substr(COALESCE(full_markdown,''),1,40) FROM memos WHERE id=?",
                             (int(memo_id),)).fetchone()
    except Exception:
        return "illeggibile"
    if row is None:
        return "assente"
    return "in_corso" if str(row[0]).startswith("[IN PROGRESS]") else "scritto"


def _testo_errore(v):
    if isinstance(v, dict):
        v = v.get("message")
    return str(v)[:500] if v else None


def esito_run(state, stato_memo="non_letto", processo=None):
    """Esito di pagina della run dal heartbeat `state` (dict) e dallo stato del memo nel DB.
    `processo` (R-2 F3/F4, 06/10): fatti sul processo misurati dalla API, tutti opzionali —
      heartbeat_precede_processo: True se un processo della run avviato da questo backend e'
        vivo e il file e' piu' vecchio di lui (run in avvio/ripresa, primo heartbeat non ancora
        scritto: il file e' della run PRECEDENTE) -> in_corso, fonte 'processo_vivo';
      pid_vivo: True/False/None per il pid dichiarato nel heartbeat;
      processi_backend_vivi: quanti processi consigliere vivi conosce questo backend;
      annullata_da_utente: l'ultima run avviata da questo backend e' stata fermata (STOP).
    Ritorna {"stato", "memo_consegnato", "motivo", "ripresa_disponibile", "ripresa", "fonte",
    "stato_memo_db"}: `stato` sempre in ESITI_RUN, `memo_consegnato` True/False/None (None =
    non misurabile, dichiarato). Pura: nessuna lettura, nessuna scrittura."""
    out = {"stato": "sconosciuta", "memo_consegnato": None, "motivo": None,
           "ripresa_disponibile": None, "ripresa": None, "fonte": "nessuna",
           "stato_memo_db": stato_memo}
    if not isinstance(state, dict):
        out["motivo"] = "heartbeat non e' un oggetto JSON"
        return out
    rounds = state.get("round_esiti")
    if isinstance(rounds, dict):
        stati = [v.get("stato") for v in rounds.values() if isinstance(v, dict)]
        out["ripresa"] = any(s in ("ripreso", "misto") for s in stati)
    pr = processo if isinstance(processo, dict) else {}
    if pr.get("heartbeat_precede_processo") is True:
        out.update(fonte="processo_vivo", stato="in_corso", memo_consegnato=False, ripresa=None,
                   motivo="run avviata, primo heartbeat non ancora scritto: gli altri dati della "
                          "pagina sono della run precedente")
        return out
    if state.get("running") is True and (pr.get("pid_vivo") is False or (
            pr.get("pid_vivo") is None and pr.get("processi_backend_vivi") == 0)):
        # nessuno conferma il processo: o e' morto (pid sparito), o non e' verificabile
        if pr.get("annullata_da_utente") is True:
            out.update(fonte="processo_fermato", stato="annullata", memo_consegnato=False,
                       motivo="run fermata dal PM (STOP): il processo non ha scritto un esito")
        elif pr.get("pid_vivo") is False:
            out.update(fonte="processo_morto", stato="interrotta", memo_consegnato=False,
                       motivo="il processo della run (pid " + str(state.get("pid")) + ") non esiste "
                              "piu' e non ha scritto un esito")
        else:
            out.update(fonte="processo_non_verificabile", stato="in_corso_senza_segnale",
                       memo_consegnato=False,
                       motivo="heartbeat senza pid e nessun processo della run conosciuto da questo "
                              "backend: non e' confermato che la run stia lavorando")
        return out
    if state.get("running") is True:
        out.update(fonte="heartbeat_vivo", memo_consegnato=False,
                   stato="in_corso_senza_segnale" if state.get("stale_warning") is True else "in_corso")
        if out["stato"] == "in_corso_senza_segnale":
            out["motivo"] = ("heartbeat fermo da " + str(state.get("stale_seconds")) + " s: "
                             "nessuno conferma che la run stia ancora lavorando")
        return out
    if "operational_status" in state:
        status, operativo = state.get("status"), state.get("operational_status")
        out["fonte"] = "registro_run_settimanale"
        rd = state.get("resume_available")
        out["ripresa_disponibile"] = rd if isinstance(rd, bool) else None
        out["memo_consegnato"] = bool(state.get("analytical_status") == "complete"
                                      and state.get("artifact_status") == "available")
        errore = _testo_errore(state.get("last_error")) or _testo_errore(state.get("first_error"))
        if status == "completed" and operativo != "blocked":
            out["stato"] = "completata"
            if not out["memo_consegnato"]:
                # completata dal registro ma senza analisi/artefatti verificati: contraddizione,
                # non si sceglie la versione piu' comoda
                out.update(stato="sconosciuta", motivo="registro 'completed' ma analisi="
                           + str(state.get("analytical_status")) + " artefatti="
                           + str(state.get("artifact_status")))
        elif status in ("incomplete", "failed"):
            out["stato"] = "bloccata" if operativo == "blocked" else "incompleta"
            out["motivo"] = (errore or _testo_errore(state.get("blocked_reason"))
                             or _testo_errore(state.get("reason")) or _testo_errore(state.get("message")))
        else:
            out["motivo"] = "stato del registro non terminale: " + str(status)
    elif state.get("status") == "failed":
        out.update(fonte="heartbeat_crash", stato="fallita", memo_consegnato=False,
                   motivo=_testo_errore(state.get("message")))
    elif state.get("technical_status") is not None:
        tecnico = state.get("technical_status")
        out["fonte"] = "esito_tecnico_run"
        mappa = {"completed": "completata", "incomplete": "incompleta", "failed": "fallita",
                 "cancelled": "annullata", "interrupted": "annullata"}
        out["stato"] = mappa.get(tecnico, "sconosciuta")
        out["memo_consegnato"] = tecnico == "completed"
        out["motivo"] = (_testo_errore(state.get("reason")) if tecnico in mappa
                         else "technical_status fuori contratto: " + str(tecnico))
    else:
        out["motivo"] = "heartbeat chiuso senza esito dichiarato (ne' registro ne' technical_status)"
    # il memo nel DB ha l'ultima parola sul «consegnato»
    if stato_memo == "in_corso":
        out["memo_consegnato"] = False
        if out["stato"] == "completata":
            out.update(stato="sconosciuta", motivo="la run risulta completata ma il memo #"
                       + str(state.get("memo_id")) + " nel DB e' ancora [IN PROGRESS]")
    elif stato_memo in ("assente", "illeggibile") and out["memo_consegnato"]:
        out["memo_consegnato"] = None
        out["motivo"] = (out["motivo"] + " | " if out["motivo"] else "") + (
            "memo #" + str(state.get("memo_id")) + " nel DB: " + stato_memo)
    return out


def round_esiti(usage_log, start_time):
    """Per i round 0/1/2 dei desk: chi ha lavorato IN QUESTO TENTATIVO e chi e' stato
    ripreso da un checkpoint di un tentativo precedente. La misura e' `ts` di ogni riga di
    usage contro `start_time` della Blackboard (stesso formato ISO locale, al secondo).
    stato: 'eseguito' | 'ripreso' | 'misto' | 'assente' | 'n.d.' (righe senza ts leggibile).
    Una riga ripresa che rigioca richieste gia' nel registro conta come 'eseguito': il
    round e' girato di nuovo, anche se non e' stato ripagato."""
    out = {}
    for r in (0, 1, 2):
        nuovi, ripresi, ignoti = set(), set(), set()
        for e in usage_log or []:
            a = str(e.get("agent") or "")
            if e.get("round") != r or not a or a.startswith("_") or a == "capo":
                continue
            ts = e.get("ts")
            if not isinstance(ts, str) or not isinstance(start_time, str) or len(ts) < 19:
                ignoti.add(a)
            elif ts[:19] < start_time[:19]:
                ripresi.add(a)
            else:
                nuovi.add(a)
        stato = ("n.d." if ignoti else "assente" if not (nuovi or ripresi)
                 else "misto" if nuovi and ripresi else "eseguito" if nuovi else "ripreso")
        out[str(r)] = {"stato": stato, "desk_eseguiti": sorted(nuovi),
                       "desk_ripresi": sorted(ripresi - nuovi), "desk_senza_orario": sorted(ignoti)}
    return out


def leggi_richieste_journal(path):
    """Righe native del registro richieste (request_journal) in SOLA LETTURA:
    [{"request_id", "state", "cost", "agent", "round_n"}]. Solleva se il file non si apre:
    il chiamante dichiara il buco, non lo tratta come «nessuna richiesta»."""
    import sqlite3
    from pathlib import Path
    from contextlib import closing
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(str(p))
    with closing(sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=2)) as cx:
        rows = cx.execute("SELECT request_id,state,cost,scope FROM requests").fetchall()
    out = []
    for request_id, state, cost, scope in rows:
        s = json.loads(scope) if scope else {}
        out.append({"request_id": request_id, "state": state, "cost": cost,
                    "agent": s.get("agent"), "round_n": s.get("round_n")})
    return out


# =============================================================================
# FALLBACK DICHIARATI (regola PM 14/07 applicata ai ripieghi di questo modulo)
# =============================================================================
# Voce MASTER "Il fallback del registro tool e' SILENZIOSO" (P1, 26/07 sera-4):
# i ripieghi di _build_tools_schema e _execute_meta_tool scattavano ZITTI. Se
# l'import di chat_tools fallisse, il comitato girerebbe con un arsenale
# mutilato — degrado MISURATO: quant 22->3 tool, options 15->3, fundamentals
# 24->6, macro 13->8 — e `agent_tools.execute_tool` risponderebbe "Tool
# sconosciuto" per 30 dei 51 nomi che il modello ha nello schema. Il memo
# usciva comunque, senza un rigo che lo dicesse: e' esattamente il pattern che
# la regola 14/07 vieta.
# Qui NON si toglie il ripiego (una run mutilata e' meglio di nessuna run): si
# toglie il SILENZIO, su tre canali — log del PM, registro leggibile, e il
# prompt del modello, che deve dichiararlo nel report. Il modello dello stile e'
# il ramo `[MEMORY ERROR: ...]` di _build_memory_block, gia' in casa.
FALLBACK_DICHIARATI = []


def _dichiara_fallback(dove, err, dettaglio=""):
    """Registra e URLA un ripiego. Ritorna la stringa dichiarata.

    Un logger non deve MAI poter rompere il chiamante (residuo dichiarato della
    review (50)): print e append sono entrambi blindati — su stdout morto un
    print alza OSError, e qui siamo dentro un `except`.
    """
    testo = ("FALLBACK DICHIARATO in " + str(dove) + ": " + type(err).__name__ +
             ": " + str(err)[:200] + ((" | " + dettaglio) if dettaglio else ""))
    try:
        FALLBACK_DICHIARATI.append(testo)
    except Exception:
        pass
    try:
        print("[!!] " + testo, flush=True)
    except Exception:
        pass
    return testo


def _move_cache_breakpoint(messages, cc):
    """200a-bis (Fase 3, "il buco grande"): breakpoint di cache MOBILE sui messages.
    Toglie il marker dall'iterazione precedente (max 4 breakpoint per richiesta:
    tools + system + questo = 3) e decora l'ultimo blocco dell'ultimo messaggio
    user (i tool_result appena accumulati) -> l'intera storia del round fin li'
    viene cachata invece di essere ripagata a prezzo pieno a ogni iterazione.
    No-op alla prima iterazione (il primo messaggio user e' una stringa)."""
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)
    last = messages[-1]
    if last.get("role") == "user" and isinstance(last.get("content"), list) and last["content"]:
        blk = last["content"][-1]
        if isinstance(blk, dict):
            last["content"][-1] = {**blk, "cache_control": cc}


class Blackboard:
    """Memoria condivisa tra specialisti per il run corrente. Round-aware.
    Scrive heartbeat in data/current_run.json per la pagina Agents Live."""
    # Ancorato al PROGETTO, non alla CWD (bug PM 15/07: il backend spawnato
    # dall'app Electron con cwd su app\ scriveva/leggeva app\data\current_run.json
    # -> heartbeat fantasma "running: true" a ogni apertura di Agents Live.
    # Stessa classe della lezione "DB vuoto = path sbagliato").
    HEARTBEAT_PATH = os.path.normpath(os.path.join(DB_DIR, "current_run.json"))
    # 27/08 (run V9, N8 di F44): il heartbeat VIVO porta le ultime N tool call.
    # Il numero vive QUI e in nessun altro posto: il payload dichiara il totale
    # (`n_tool_calls`) e se ha tagliato (`tool_log_tappato`), e il frontend
    # rendera' "ultime 50 di N" leggendo entrambi (lotto D di F44, meta'
    # frontend: oggi AgentsLive.tsx legge solo `n_tool_calls`, nella testata
    # `state.n_tool_calls ?? P.calls.length`).
    HEARTBEAT_TOOL_LOG_MAX = 50

    def __init__(self, memory_db=None, memo_id=None, *, valuation_preparer=None,
                 heartbeat_path=None, run_scope="weekly", run_id=None,
                 target_ticker=None, pm_view="", candidate_history="", budget_gate=None):
        self.language = capture_language()
        self.data = {}
        self.current_round = 0
        self.tool_log = []
        # Trade Idea and research weekly retain bounded tool receipts for the
        # source seal. The visible tool_log is only an audit preview.
        self.tool_receipts = []
        self.memory_db = memory_db
        self.valuation_results = {}
        self.valuation_attempts = []
        self.valuation_generations = []
        # Injected only by the application's explicit valuation budget policy.
        self.valuation_preparer = valuation_preparer
        if callable(valuation_preparer):
            # The shared preparer already owns an authorization + paid journal.
            # Retain that owner instead of charging its request twice to weekly.
            @wraps(valuation_preparer)
            def separately_journaled_preparer(*args, **kwargs):
                with request_scope(None, phase="valuation_preparer"):
                    return valuation_preparer(*args, **kwargs)
            self.valuation_preparer = separately_journaled_preparer
        self.memo_id = memo_id
        self.run_scope = run_scope
        self.run_id = run_id
        self.target_ticker = target_ticker
        self.pm_view = pm_view
        self.candidate_history = candidate_history
        self.budget_gate = budget_gate
        self.specialist_checkpoints = {}
        if heartbeat_path is not None:
            self.HEARTBEAT_PATH = os.fspath(heartbeat_path)
        self.start_time = datetime.now().isoformat(timespec="seconds")
        self.specialist_status = {}  # {name: "idle" | "running" | "done" | "error"}
        self.current_specialist = None
        # Fase 3 parallelizzazione: con piu' specialisti in thread, letture (heartbeat,
        # summary) e scritture su data/status DEVONO essere serializzate — iterare un
        # dict mentre un altro thread lo muta = RuntimeError + heartbeat corrotto.
        # RLock perche' write() -> _write_heartbeat() riacquisisce lo stesso lock.
        self._lock = threading.RLock()
        # Contabilita' token/costo della run (voce collaudo #44): una entry per
        # agente-round, alimentata da record_usage. DEVE esistere PRIMA del primo
        # heartbeat, che ora la legge per usage_by_specialist/usage_total.
        self.usage_log = []
        # 12/09 (Fable 5.1, prerequisito 3 del mandato): QUANDO ogni (desk, round) e' stato
        # scritto, con fuso dichiarato — il Capo lo legge nel prefisso «[ROUND N SENZA
        # REPORT ... Round M, scritto il ...]» (capo.scegli_report_specialisti). Prima la
        # blackboard salvava la sola stringa e l'unica data che il Capo vedeva era quella
        # che il MODELLO scriveva da se' nel titolo. {desk: {round: iso con offset}}.
        self.orari_report = {}
        self._write_heartbeat()

    def record_valuation(self, ticker, payload, specialist):
        from bellomberg.reporting.valuation_delivery import describe_result
        with self._lock:
            if self.run_scope == "trade_idea":
                from bellomberg.valuation.trade_idea_model import candidate_model_usability
                generation = {**payload, "ticker": ticker} if isinstance(payload, dict) else {
                    "ticker": ticker, "error": "valuation payload non oggetto"}
                self.valuation_generations.append(generation)
                previous = self.valuation_results.get(ticker) or {}
                previous_usable = candidate_model_usability(previous)["usable"] is True
                current_usable = candidate_model_usability(generation)["usable"] is True
                if not previous_usable or current_usable:
                    self.valuation_results[ticker] = generation
            else:
                self.valuation_results[ticker] = payload
            self.valuation_attempts.append({**describe_result(ticker, payload),
                "specialist": specialist, "round": self.current_round,
                "attempt": len(self.valuation_attempts) + 1})
            self._write_heartbeat()

    def record_usage(self, agent, round_n, model, usage, duration_s=None,
                     api_calls=0, cache_ttl=None, status="ok", retry_vuoto=0, lavoro=None):
        """Registra token, durata e costo di UN agente-round (voce collaudo #44).

        Prima di oggi i token si stampavano e basta: il costo della run era
        verificabile solo dalla console web Anthropic. Ritorna la entry scritta
        (comoda per la riga di log del chiamante).
        Thread-safe: gli specialisti girano in thread paralleli (v. commento Fase 3
        in __init__), l'append e l'heartbeat vanno sotto lock.
        """
        u = usage or {}

        def _i(*keys):
            # accetta sia lo schema degli specialisti (in/out) sia quello del Capo
            # (input_tokens/output_tokens); chiave mancante = None
            for k in keys:
                if u.get(k) is not None:
                    try:
                        n = int(u[k])
                        return n if n >= 0 and not isinstance(u[k], bool) and str(n) == str(u[k]) else None
                    except Exception:
                        return None
            return None

        norm = {
            "in": _i("in", "input_tokens"),
            "out": _i("out", "output_tokens"),
            "cache_read": _i("cache_read", "cache_read_input_tokens"),
            "cache_write": _i("cache_write", "cache_creation_input_tokens"),
        }
        # Costo: se llm_pricing manca la run NON deve morire, ma il buco si DICHIARA
        # (cost None + status pricing_unavailable) — mai uno 0,00 di comodo.
        cost, fx_rate, fx_source = None, None, "n.d."
        cost_status = "pricing_unavailable"
        try:
            from bellomberg.core.llm_pricing import cost_eur
            # 05/09: se l'agente porta il costo consegnato da OpenRouter (cost_usd) e' quello
            # la misura; senza, llm_pricing usa il listino di casa o dichiara model_unknown.
            _r = cost_eur(model, dict(norm, cost_usd=u.get("cost_usd")), cache_ttl) or {}
            cost = _r.get("cost")
            fx_rate = _r.get("fx_rate")
            fx_source = _r.get("fx_source", "n.d.")
            cost_status = _r.get("status", "model_unknown")
        except Exception as e:
            print("[Blackboard] WARN pricing non disponibile (" + str(agent) + "): " + str(e))
        if "cost_usd" in u and u["cost_usd"] is None:
            # An absent provider bill is not the historical list-price estimate.
            cost, cost_status = None, "usage_unknown"
        # "api_error" in ingresso ha la PRECEDENZA: un agente fallito resta
        # dichiarato fallito, anche se i suoi token erano prezzabili.
        if status == "api_error":
            final_status = "api_error"
        elif cost_status != "ok":
            final_status = cost_status
        else:
            final_status = status
        # Semantica dei buchi (PM 15/07). Due casi in cui il costo NON e' misurabile
        # anche se il modello e' a listino:
        #  - api_error con token TUTTI a zero: capo.py:352-354 ritorna 0/0 come
        #    SEGNAPOSTO, non come misura -> 0 * tariffa = 0,00 EUR sarebbe la bugia
        #    "l'agente fallito e' costato zero". I token veri sono IGNOTI -> None.
        #    Se invece i token NON sono a zero, quella spesa e' REALE e gia' avvenuta
        #    (uno specialista morto all'iterazione 5 ha bruciato 4 chiamate): si prezza,
        #    ma l'agente resta marcato api_error.
        #  - usage_unknown: la risposta API non ha esposto usage -> token ignoti.
        _no_tokens = not any(norm[k] for k in ("in", "out", "cache_read", "cache_write"))
        if u.get("cost_usd") is None and (final_status == "usage_unknown"
                                        or (final_status == "api_error" and _no_tokens)):
            cost = None
        mancanti = [k for k in norm if norm[k] is None]
        entry = {
            "language": self.language,
            "cost_usd": u.get("cost_usd"),
            "known_cost_usd": u.get("known_cost_usd", u.get("cost_usd")),
            "request_ids": list(u.get("request_ids") or []),
            "agent": agent, "round": round_n, "model": model,
            "in": norm["in"], "out": norm["out"],
            "cache_read": norm["cache_read"], "cache_write": norm["cache_write"],
            "api_calls": int(api_calls or 0),
            "duration_s": duration_s,
            "cost_eur": cost, "fx_rate": fx_rate, "fx_source": fx_source,
            "status": final_status,
            "tokens_status": "parziale" if mancanti else "completo",
            "tokens_missing": mancanti,
            # Finding 3: memory_db.save_llm_usage legge e.get("cache_ttl") per la
            # colonna llm_usage.cache_ttl, ma la entry non l'ha mai contenuta ->
            # colonna NULL su tutte le righe. E' l'UNICO campo che dice se il
            # cache_write e' stato pagato 1.25x (5m) o 2.00x (1h).
            "cache_ttl": cache_ttl,
            # 12/09 (Fable 5.1): 1 se il round ha ritentato una call uscita a max_tokens con
            # 0 char di testo. `api_calls` somma iterazioni + retry 529 + questo: una riga
            # con api_calls=2 era identica per un 529 e per un ritentativo a 0 char.
            # Campo ADDITIVO: memory_db.save_llm_usage lo ignora finche' la colonna
            # llm_usage.retry_vuoto non esiste (richiesta a parte, memory_db non e' qui).
            "retry_vuoto": int(retry_vuoto or 0),
            # R-2 F5 (06/10): QUALE lavoro del desk in questo round (una consultazione Trade Idea
            # ha il suo id): «fallito poi riuscito» vale solo fra tentativi dello STESSO lavoro.
            # Campo ADDITIVO, None = lavoro unico del round (run settimanale).
            "lavoro": lavoro,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            if entry["request_ids"]:
                ids = set(entry["request_ids"])
                for index, previous in enumerate(self.usage_log):
                    if (previous.get("agent") == agent and previous.get("round") == round_n
                            and previous.get("model") == model and previous.get("request_ids")
                            and set(previous["request_ids"]) <= ids):
                        self.usage_log[index] = entry
                        self._write_heartbeat()
                        return entry
            self.usage_log.append(entry)
            self._write_heartbeat()
        return entry

    def _usage_aggregates(self):
        """Aggrega usage_log per agente + totale run (chiavi del payload UI).
        PUNTO UNICO DI VERITA' del costo della run: la chiama l'heartbeat, la chiama
        mark_run_complete e la chiama consigliere_multi per la riga di log finale.
        Pura e senza effetti collaterali: sicura anche a run finita.

        SEMANTICA DEI BUCHI v2 (PM 15/07), tre principi:
         (1) mai nascondere spesa gia' avvenuta e NOTA;
         (2) mai presentare un totale parziale come completo;
         (3) mai un numero al posto di un buco (0 = "costato zero", None = "non lo so").
        La v1 violava il (1): bastava UN round None e l'intero agente diventava None,
        cancellando i round gia' pagati (misurato: quant morto in R2 -> 0,58 EUR a
        schermo contro 1,12 EUR reali nel log, quasi 2x). Ora:
         - per agente: cost_eur = somma dei SOLI round prezzati; None SOLO se NESSUN
           round e' prezzato; partial=True se la cifra e' un MINIMO (almeno un round
           senza costo e almeno uno con);
         - totale: somma per ENTRY, che e' identica alla somma dei per-agente
           non-None (invariante verificata numericamente).
        Chiamare col lock gia' preso (lo fanno heartbeat e mark_run_complete).

        TENTATIVI SENZA RICHIESTE (handoff-4 voce 2, 05/10, Opus 5.5): un tentativo
        fallito PRIMA di mandare qualunque richiesta (es. cap del Red Team rifiutato in
        quotazione) lasciava una riga api_error con costo None e rendeva PARZIALE un
        totale completo. La misura si fa sul REGISTRO RICHIESTE, non sulla riga di usage:
        v. _tentativi_senza_richieste. Quelle righe valgono 0 (misurato: nessuna
        richiesta) e sono ELENCATE in total["tentativi_senza_richieste"]."""
        _esenti, _misura, _misura_err = self._tentativi_senza_richieste()
        by = {}
        _ultimi = {}   # {agente: {round: [status dei tentativi, in ordine]}}
        _ko = ("api_error", "usage_unknown")
        _poi_riusciti = []
        for e in self.usage_log:
            a = e.get("agent") or "?"
            _zero = id(e) in _esenti
            d = by.setdefault(a, {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0,
                                  "cost_eur": None, "partial": False, "duration_s": None,
                                  "api_calls": 0, "retry_vuoto": 0, "status": "ok",
                                  "tentativi_senza_richieste": 0,
                                  "_priced": 0, "_unpriced": 0})
            if _zero:
                d["tentativi_senza_richieste"] += 1
            for k in ("in", "out", "cache_read", "cache_write"):
                if _zero and e.get(k) is None:
                    continue   # nessuna richiesta nel registro: zero token MISURATI, non ignoti
                d[k] = d[k] + e[k] if d[k] is not None and e.get(k) is not None else None
            d["api_calls"] += int(e.get("api_calls") or 0)
            # 12/09: quanti round del desk hanno ritentato una call a 0 char (-> heartbeat/UI)
            d["retry_vuoto"] += int(e.get("retry_vuoto") or 0)
            if e.get("duration_s") is not None:
                d["duration_s"] = (d["duration_s"] or 0.0) + float(e["duration_s"])
            if _zero:
                d["cost_eur"] = (d["cost_eur"] or 0.0) + 0.0
                d["_priced"] += 1
            elif e.get("cost_eur") is None:
                # round non prezzabile: NON azzera ne' cancella i fratelli prezzati,
                # segna solo che la cifra dell'agente e' un minimo (partial sotto)
                d["_unpriced"] += 1
            else:
                d["cost_eur"] = (d["cost_eur"] or 0.0) + float(e["cost_eur"])
                d["_priced"] += 1
            # status dell'agente = il PEGGIORE dei suoi round (ok < usage_unknown <
            # model_unknown < pricing_unavailable < api_error) — e' la STORIA, non l'esito
            d["status"] = _worse_status(d["status"], e.get("status") or "ok")
            # seguito V2 (PM 05/10, Opus 5.5): per (agente, round) conta l'ULTIMO tentativo
            # (ordine di usage_log = ordine di registrazione, i ripresi da checkpoint prima)
            _ultimi.setdefault(a, {}).setdefault((e.get("round"), e.get("lavoro")), []).append(
                e.get("status") or "ok")
        for a in sorted(by):
            d = by[a]
            # esito FINALE dell'agente = il peggiore fra gli ULTIMI tentativi dei suoi round:
            # fallito e poi riuscito nello stesso round non e' un KO, ma resta dichiarato
            d["status_finale"] = "ok"
            d["tentativi_falliti_poi_riusciti"] = 0
            for (r, lavoro), stati in sorted(_ultimi.get(a, {}).items(), key=lambda kv: str(kv[0])):
                # R-2 F5 (06/10): «fallito poi riuscito» vale solo per i RITENTATIVI dello
                # stesso lavoro: tutti i falliti PRIMA del primo successo. Un fallimento DOPO
                # un successo e' un altro lavoro nello stesso round (consultazioni Trade Idea)
                # e resta KO; fuori dalla run settimanale (un lavoro per desk e round) la
                # regola non si applica: vale il peggiore, come prima.
                primo_ok = next((i for i, x in enumerate(stati) if x not in _ko), None)
                ritentativi = ((self.run_scope == "weekly" or lavoro is not None) and primo_ok is not None
                               # tutti ok dal primo successo in poi (ultimo compreso)
                               and all(x not in _ko for x in stati[primo_ok:]))
                finale = stati[-1] if ritentativi else max(stati, key=lambda x: USAGE_STATUS_SEVERITY.get(x, 3))
                d["status_finale"] = _worse_status(d["status_finale"], finale)
                falliti = sum(1 for x in stati[:primo_ok or 0] if x in _ko)
                if ritentativi and falliti:
                    d["tentativi_falliti_poi_riusciti"] += 1
                    _poi_riusciti.append({"agent": a, "round": r, "tentativi_falliti": falliti,
                                          **({"lavoro": lavoro} if lavoro is not None else {})})
        for d in by.values():
            d["tokens_missing"] = [k for k in ("in", "out", "cache_read", "cache_write") if d[k] is None]
            d["tokens_status"] = "parziale" if d["tokens_missing"] else "completo"
            # partial = "quello che vedi e' un MINIMO": c'e' spesa nota E spesa ignota.
            # Se NESSUN round e' prezzato cost_eur resta None (buco pieno, non parziale).
            d["partial"] = bool(d["_priced"] and d["_unpriced"])
            d.pop("_priced", None)
            d.pop("_unpriced", None)
        total = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0,
                 "cost_eur": None, "partial": False, "fx_source": None,
                 "unpriced_agents": [], "error_agents": [],
                 # versione del SIGNIFICATO di error_agents (06/10): KO = esito FINALE del lavoro;
                 # heartbeat e snapshot di score_history piu' vecchi non lo portano (= «almeno
                 # un tentativo KO»)
                 "error_agents_semantica": "esito_finale_v2",
                 # storia dichiarata: round in cui un tentativo e' fallito e l'ultimo e' riuscito
                 "tentativi_falliti_poi_riusciti": _poi_riusciti,
                 # come e' stata misurata l'assenza di richieste dei tentativi falliti:
                 # 'journal' | 'non_necessaria' | 'assente' | 'illeggibile' (gli ultimi due
                 # lasciano il totale PARZIALE, come prima: il buco si dichiara)
                 "misura_richieste": _misura,
                 "tentativi_senza_richieste": [
                     {"agent": e.get("agent"), "round": e.get("round"), "status": e.get("status"),
                      "ts": e.get("ts")} for e in self.usage_log if id(e) in _esenti]}
        if _misura_err:
            total["misura_richieste_errore"] = _misura_err
        # Il totale si somma per ENTRY (non per agente): e' l'unico modo di non
        # perdere i round prezzati di un agente che ha anche round ignoti. Coincide
        # con la somma dei per-agente non-None -> invariante testata.
        _priced_total = 0.0
        _any_priced = False
        for e in self.usage_log:
            if id(e) in _esenti:
                _any_priced = True   # 0 misurato sul registro richieste
            elif e.get("cost_eur") is None:
                total["partial"] = True  # almeno una entry non prezzabile: totale = MINIMO
            else:
                _priced_total += float(e["cost_eur"])
                _any_priced = True
        for a in sorted(by):
            d = by[a]
            for k in ("in", "out", "cache_read", "cache_write"):
                total[k] = total[k] + d[k] if total[k] is not None and d[k] is not None else None
            if d["cost_eur"] is None or d["partial"]:
                # chi ha ANCHE un solo round non prezzabile e' NOMINATO qui: il totale
                # e' parziale e si dichiara, non si tace (principio 2)
                total["unpriced_agents"].append(a)
            # error_agents e' una lista SEPARATA da unpriced_agents: "non so quanto e'
            # costato" e "e' andato KO" sono due buchi diversi (un agente puo' stare in
            # entrambe: KO senza token noti). La UI li deve dire con parole diverse.
            # Seguito V2 (PM 05/10): KO = ESITO FINALE (ultimo tentativo di un round), non
            # «almeno un tentativo»; i falliti-poi-riusciti stanno in tentativi_falliti_poi_riusciti.
            if d["status_finale"] in _ko:
                total["error_agents"].append(a)
        # Finding 1/8 (semantica PM 15/07): il totale NON parte da 0.0. Con FX giu',
        # llm_pricing esploso o usage_log ancora vuoto (primi minuti di OGNI run) lo
        # zero significherebbe "e' costato zero" su una run da ~10 EUR -> resta None,
        # che significa "non lo so". Somma solo se c'e' ALMENO una entry prezzata.
        total["cost_eur"] = _priced_total if _any_priced else None
        # fx_source della run = il caso PEGGIORE (n.d. > fallback > live) tra i costi
        # EFFETTIVAMENTE calcolati: se anche un solo euro e' passato dal cambio di
        # ripiego, il PM lo deve sapere. Si guardano solo le entry prezzate perche'
        # un modello fuori listino torna fx_source "n.d." pur con l'FX live (verificato
        # 15/07): contarlo direbbe "FX n.d." su un totale calcolato a cambio vero —
        # dichiarazione FALSA. Chi non e' prezzabile e' gia' in unpriced_agents.
        # Se NESSUNA entry e' prezzabile si guarda tutto: un FX davvero giu' resta n.d.
        _priced = [e for e in self.usage_log if e.get("cost_eur") is not None]
        _srcs = {e.get("fx_source") for e in (_priced or self.usage_log)}
        for s in ("n.d.", "fallback", "live"):
            if s in _srcs:
                total["fx_source"] = s
                break
        else:
            # usage_log vuoto (primi minuti della run): nessun cambio e' stato
            # chiesto. "n.d." e' l'unico valore onesto in contratto — None faceva
            # cadere la UI sul suo default e affermare un FX mai interrogato.
            total["fx_source"] = "n.d."
        total["tokens_missing"] = [k for k in ("in", "out", "cache_read", "cache_write") if total[k] is None]
        total["tokens_status"] = "parziale" if total["tokens_missing"] else "completo"
        return by, total

    def _tentativi_senza_richieste(self):
        """(id delle righe esenti, misura, errore). Esente = riga di usage api_error, costo
        None, NESSUN request_id, di un agente che nel registro richieste della run
        (self.request_journal, letto in sola lettura) non ha alcuna richiesta SCOPERTA,
        cioe' non attribuita a nessuna riga di usage e con costo diverso da 0. Basta una
        richiesta scoperta NELLA RUN (di qualunque agente: fallita a meta', esito ignoto,
        spesa nota mai registrata) e nessuna riga e' esente: il totale resta PARZIALE (regola PM 05/10:
        il costo incerto si dichiara). Senza registro o con registro illeggibile non si
        esenta niente e lo si dice."""
        candidati = [e for e in self.usage_log
                     if e.get("cost_eur") is None and e.get("status") == "api_error"
                     and not e.get("request_ids")]
        if not candidati:
            return set(), "non_necessaria", None
        journal = getattr(self, "request_journal", None)
        path = getattr(journal, "path", None)
        if path is None:
            return set(), "assente", None
        try:
            righe = leggi_richieste_journal(path)
        except Exception as exc:
            return set(), "illeggibile", type(exc).__name__
        # R-2 F1/F2 (06/10): la copertura si misura sull'INTERA run, non per agente. Le
        # etichette del registro e dell'usage non coincidono sempre (sonda: registro
        # "_probe", usage "_probe:<slug>"; scope senza agente): una richiesta scoperta di
        # QUALUNQUE agente e' un costo incerto della run e nessun tentativo si esenta.
        coperte = set()
        for e in self.usage_log:
            coperte.update(e.get("request_ids") or [])
        scoperte = [r for r in righe if r["request_id"] not in coperte and r["cost"] != 0]
        if scoperte:
            return set(), "journal", None
        return ({id(e) for e in candidati}, "journal", None)

    def _usage_state(self):
        """Le due chiavi usage per lo 'state' di heartbeat e mark_run_complete.
        Mai far saltare la scrittura per un errore di aggregazione: il buco si
        dichiara nel payload."""
        try:
            by, total = self._usage_aggregates()
            return by, total
        except Exception as e:
            # Prima si ritornava {"error": ...} e basta: un totale FUORI CONTRATTO.
            # La UI non trovava cost_eur/fx_source, e finiva per affermare un FX live
            # mai richiesto mentre il motivo del buco veniva ingoiato. Ora il totale
            # e' conforme e dichiara il buco: costo ignoto, FX ignoto, e l'errore
            # visibile sia in "error" sia tra gli unpriced.
            return {}, {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0,
                        "cost_eur": None, "partial": True, "fx_source": "n.d.",
                        "unpriced_agents": ["(aggregazione fallita)"],
                        "error_agents": ["(aggregazione fallita)"],
                        "misura_richieste": "illeggibile", "tentativi_senza_richieste": [],
                        "tentativi_falliti_poi_riusciti": [],
                        "error_agents_semantica": "esito_finale_v2",
                        "error": "aggregazione usage fallita: " + str(e)}

    def _round_esiti_sicuri(self):
        """round_esiti per il heartbeat: un guasto qui non deve far saltare la scrittura,
        ma si dichiara (stato 'n.d.' con l'errore), mai un dizionario vuoto zitto."""
        try:
            return round_esiti(self.usage_log, self.start_time)
        except Exception as exc:
            return {str(r): {"stato": "n.d.", "errore": type(exc).__name__} for r in (0, 1, 2)}

    def _scrivi_heartbeat_atomico(self, payload):
        """Scrive `payload` su HEARTBEAT_PATH senza che il lettore possa mai
        vederlo a meta' (27/08, run V9, N7 di F44): `scrivi_file_atomico`
        (temporaneo accanto + `os.replace`) piu' il warning di sempre.

        Prima: `open("w")` diretto -> il file veniva TRONCATO e poi riempito,
        e `GET /agents/live` (bellomberg_api) nel mezzo leggeva vuoto o a meta'
        e rispondeva `{"running": false, "message": "read error"}`: F4 perdeva
        stato e orologio per un giro e cancellava `activeTaskId` (visto dal
        vivo dalla chat frontend alle 18:42:51 del 26/08; in un sondaggio
        successivo di 48 poll in 20 s non e' ricapitato: raro, non teorico).
        Ora il lettore vede il vecchio o il nuovo, mai un pezzo (review 27/08:
        0 letture a meta' su 630 replace / 62.173 poll a tre cadenze). Finestra
        RESIDUA, stimata ~0,01-0,3 ms per replace secondo la cadenza, in cui
        una `open("r")` concorrente prende PermissionError — la copre il
        LETTORE, che ritenta (`get_agents_live` in bellomberg_api). Qui: se il replace cade tre
        volte (0,15 + 0,35 s sotto l'RLock; `_write_heartbeat` prima aspettava
        0,5 s una volta, `mark_run_complete` non ritentava affatto) il file
        precedente resta intatto e si logga il warning, al piu' uno al minuto
        (FIX 13/07: prima falliva in silenzio). Ritorna True se il file nuovo
        e' a posto."""
        import time as _t
        ok, ultimo = scrivi_file_atomico(self.HEARTBEAT_PATH, payload)
        if ok:
            return True
        if _t.time() - getattr(self, "_hb_warn_ts", 0) > 60:
            self._hb_warn_ts = _t.time()
            print("[Blackboard] WARN heartbeat write fallita: " + str(ultimo))
        return False

    def _write_heartbeat(self):
        """Scrive lo stato corrente del run su file per il frontend live."""
        self._lock.acquire()  # snapshot consistente: data/status possono mutare da altri thread
        try:
            import os
            os.makedirs(os.path.dirname(self.HEARTBEAT_PATH), exist_ok=True)
            _usage_by, _usage_tot = self._usage_state()
            state = {
                "run_scope": self.run_scope,
                "run_id": self.run_id,
                "target_ticker": self.target_ticker,
                "language": self.language,
                "running": True,
                # R-2 F4: chi scrive, per la verifica di vita del processo nella API
                "pid": os.getpid(),
                "start_time": self.start_time,
                "current_round": self.current_round,
                "current_specialist": self.current_specialist,
                "specialist_status": self.specialist_status,
                # 27/08 (N8 di F44): le ULTIME N chiamate + il totale VERO + il
                # tappo dichiarato; prima F4 leggeva "50 su 50" per tutta la run.
                "tool_log": self.tool_log[-self.HEARTBEAT_TOOL_LOG_MAX:],
                "valuation_attempts": self.valuation_attempts,
                **({"valuation_generations": len(self.valuation_generations)}
                   if self.run_scope == "trade_idea" else {}),
                "n_tool_calls": len(self.tool_log),  # totale VERO, non il tappo
                "tool_log_tappato": len(self.tool_log) > self.HEARTBEAT_TOOL_LOG_MAX,
                "reports_by_specialist": {
                    name: {round_n: (text[:500] + "...") if len(text) > 500 else text
                            for round_n, text in rounds.items()}
                    for name, rounds in self.data.items() if not name.startswith("_")
                },
                "memo_id": self.memo_id,
                # ripipeline 15/07: totale report atteso della run (6 R0 + 6 R1 + 3 R2 = 15);
                # la UI lo usa come denominatore invece di indovinare roster x round
                "expected_reports": getattr(self, "expected_reports", None),
                # chi replica in R2 (per il contatore ROUNDS per-agente della UI: 3 vs 2)
                "r2_specialists": sorted(getattr(self, "r2_specialists", []) or []),
                # collaudo #44: token/costo per agente e totale run, live nella UI
                "usage_by_specialist": _usage_by,
                "usage_total": _usage_tot,
                # handoff-4 voce 2: chi ha lavorato in QUESTO tentativo e chi e' ripreso
                # da checkpoint (la pagina non lo indovina dagli orari HH:MM:SS del tool_log)
                "round_esiti": self._round_esiti_sicuri(),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._scrivi_heartbeat_atomico(json.dumps(state, default=str))
        except Exception:
            pass  # mai bloccare il run per il heartbeat
        finally:
            self._lock.release()

    def mark_specialist_start(self, name, round_n):
        """Chiamato all'inizio di un round di uno specialista."""
        with self._lock:
            self.current_specialist = name  # con parallelismo = ultimo partito
            self.current_round = round_n
            self.specialist_status[name] = "running"
            self._write_heartbeat()

    def mark_specialist_done(self, name):
        with self._lock:
            self.specialist_status[name] = "done"
            if self.current_specialist == name:
                self.current_specialist = None
            self._write_heartbeat()

    def mark_specialist_error(self, name, error):
        with self._lock:
            self.specialist_status[name] = "error"
            self._write_heartbeat()

    def mark_run_complete(self, *, technical_status="completed", reason=None):
        self.current_specialist = None
        self._lock.acquire()  # stesso file del heartbeat: mai scritture sovrapposte
        try:
            import os
            os.makedirs(os.path.dirname(self.HEARTBEAT_PATH), exist_ok=True)
            # stesse chiavi usage del heartbeat: senza, il conto della run sparirebbe
            # dalla UI proprio quando serve (a run finita)
            _usage_by, _usage_tot = self._usage_state()
            state = {
                "run_scope": self.run_scope,
                "run_id": self.run_id,
                "target_ticker": self.target_ticker,
                "running": False,
                "pid": os.getpid(),
                "start_time": self.start_time,
                "technical_status": technical_status,
                "reason": reason,
                "completed_at": (datetime.now().isoformat(timespec="seconds")
                                 if technical_status == "completed" else None),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "specialist_status": self.specialist_status,
                "tool_log": self.tool_log,
                "valuation_attempts": self.valuation_attempts,
                "n_tool_calls": len(self.tool_log),
                "tool_log_tappato": False,  # a run finita il log e' intero
                "memo_id": self.memo_id,
                "usage_by_specialist": _usage_by,
                "usage_total": _usage_tot,
                "round_esiti": self._round_esiti_sicuri(),
            }
            self._scrivi_heartbeat_atomico(json.dumps(state, default=str))
        except Exception:
            pass
        finally:
            self._lock.release()

    def write(self, specialist_name, round_n, report):
        with self._lock:  # il save DB sotto resta FUORI dal lock (retry con sleep 2-6s)
            self.data.setdefault(specialist_name, {})[round_n] = report
            # 12/09: orario LOCALE della macchina con offset esplicito (il DB usa
            # datetime('now') = UTC: due orologi diversi, qui il fuso viaggia col dato)
            self.orari_report.setdefault(specialist_name, {})[round_n] = (
                datetime.now().astimezone().isoformat(timespec="seconds"))
        # Persist dei report FINALI su DB (semantica storica: round 2 = finalizzato).
        # Il red team gira TRA R1 e R2 (round 1) ma DEVE persistere comunque (voce P1).
        # Ripipeline 15/07: R2 e' SELETTIVO — per chi NON replica in R2 il report
        # finale e' quello di R1 e va persistito come finale (round 2 nel DB),
        # altrimenti memoria per-specialista e regenerate_memo lo PERDONO.
        # bb.r2_specialists la setta consigliere_multi; assente = comportamento storico.
        _r2_set = getattr(self, "r2_specialists", None)
        _is_spec = not str(specialist_name).startswith("_")
        # Review 15/07: MAI promuovere a finale un placeholder d'errore — verrebbe
        # iniettato per settimane nella memoria del desk come "round 2 final" e il
        # Capo lo leggerebbe come report. Senza riga su DB il dominio risulta
        # SCOPERTO e la regola dei domini scoperti fa il suo lavoro (onesto).
        # Voce 2 §9-quattuortrigies (01/08): la guardia ora copre ANCHE il ramo
        # round_n==2 — prima un errore VERO in R2 passava dritto (misurato: memo 48,
        # quant/options = "[ERROR ... 400]" letti da memory_db come memoria del desk).
        # Il red team resta INCONDIZIONATO di proposito: regenerate_memo lo richiede.
        _placeholder = not report_specialista_utilizzabile(report)
        _final = ((round_n == 2 and not _placeholder) or specialist_name == "_red_team"
                  or (round_n == 1 and _r2_set is not None and _is_spec
                      and specialist_name not in _r2_set and not _placeholder))
        # Review 15/07: la bozza R1 di chi REPLICA in R2 va su DB come round 1 —
        # materiale di recupero per regenerate se la run muore tra red team e R2
        # (la memoria legge solo round 2: nessun doppione).
        _draft_backup = (round_n == 1 and _r2_set is not None and _is_spec
                         and specialist_name in _r2_set and not _placeholder)
        if ((_final or _draft_backup) and self.memory_db and self.memo_id
                and specialist_name not in ("_portfolio_priming",)):
            _persist_round = 2 if (_final and specialist_name != "_red_team") else round_n
            saved = False
            last_err = None
            import time as _t
            for attempt in (1, 2, 3):
                try:
                    self.memory_db.save_specialist_report(self.memo_id, specialist_name, _persist_round, report)
                    saved = True
                    break
                except Exception as e:
                    last_err = e
                    _t.sleep(2 * attempt)  # 2s/4s/6s: lock transitori (OneDrive/scheduler)
            if not saved:
                # FIX 13/07: run 09/07 e 13/07 PERSE perche' i save fallivano in
                # silenzio -> regenerate_memo senza materiale. Ora: errore visibile
                # + RESCUE su file cosi' la run e' sempre recuperabile.
                print("[Blackboard] ERRORE DB save report " + specialist_name
                      + " (memo " + str(self.memo_id) + "): " + str(last_err))
                try:
                    import os as _os
                    _os.makedirs(_os.path.join(DB_DIR, "rescue_reports"), exist_ok=True)
                    _p = (_os.path.join(DB_DIR, "rescue_reports", "memo" + str(self.memo_id) + "_"
                          + specialist_name + "_r" + str(round_n) + ".md"))
                    with open(_p, "w", encoding="utf-8") as f:
                        f.write(report)
                    print("[Blackboard] report di riserva salvato in " + _p)
                except Exception as e2:
                    print("[Blackboard] anche il rescue file e' fallito: " + str(e2))
                _record_run_failure(self, last_err, specialist_name, round_n)
                if callable(getattr(self, "persist_run_checkpoint", None)):
                    raise RuntimeError("report persistence failed: " + str(last_err)) from last_err
        self._write_heartbeat()

    def read(self, specialist_name=None, round_n=None):
        with self._lock:
            if specialist_name and round_n is not None:
                return self.data.get(specialist_name, {}).get(round_n)
            if specialist_name:
                return self.data.get(specialist_name, {})
            return self.data

    def get_latest(self, specialist_name):
        with self._lock:
            # Voce B 25/08: `red_team` e' il nome PUBBLICO — summary_for_specialist
            # rinomina cosi' la chiave grezza `_red_team`, quindi e' il nome che i
            # lettori conoscono e chiedono. La chiave nuda non esiste mai in data
            # (il red team scrive `_red_team`): prima l'alias non risolveva e
            # ask_specialist("red_team") rispondeva «Not yet available» con la
            # critica a registro.
            if specialist_name == "red_team" and not self.data.get("red_team"):
                specialist_name = "_red_team"
            if specialist_name not in self.data:
                return None
            rounds = self.data[specialist_name]
            if not rounds:
                return None
            latest_round = max(rounds.keys())
            return {"round": latest_round, "report": rounds[latest_round]}

    def summary_for_specialist(self, requesting_specialist):
        with self._lock:
            independent = getattr(self, "independent_round", None)
            building_owner = (requesting_specialist == "fundamentals" and self.current_round == 1
                              and getattr(self, "model_phase", None) == "building")
            if (self.run_scope == "trade_idea" and independent in (0, 1)
                    and self.current_round <= independent and not building_owner):
                return {}
            out = {}
            # RED TEAM visibile agli specialisti (voce P1 "invisibile per costruzione":
            # il filtro "_" sotto lo tagliava sempre). Whitelist esplicita, messa PRIMA
            # nel dict.
            # ⚠️ 21/08: qui il commento diceva "il chiamante tronca il JSON a 6000
            # char" — un TERZO numero, rimasto dal 15/07 mentre il codice tagliava a
            # 9.000. E il cap a 3.500 sulla critica esisteva PER QUELLA FINESTRA (lo
            # diceva il suo stesso marcatore): sulla run vera la critica pesa 5.122
            # char e ne perdeva il 32%, incluso il capitolo "I 3 RISCHI CHE IL CAPO
            # NON DEVE IGNORARE". Ora la finestra e' MAX_CHAR_BLACKBOARD e il riparto
            # e' equo (_blocco_blackboard): il red team non puo' piu' mangiarsi la
            # quota dei colleghi stando in testa al dict, quindi il cap non serve.
            # Quando cambi un limite, cerca cio' che si giustificava con quel limite.
            rt = self.data.get("_red_team")
            if rt:
                latest_rt = max(rt.keys())
                out["red_team"] = {"round": latest_rt, "report": rt[latest_rt]}
            for name, rounds in self.data.items():
                if name == requesting_specialist or name.startswith("_"):
                    continue
                latest = self.get_latest(name)
                if latest:
                    out[name] = {"round": latest["round"], "report": latest["report"]}
            return out


def _tetto_tool_result():
    """Il tetto dei tool_result, LETTO da dove vive (chat_tools) e mai ricopiato.
    Serve a dire la verita' sulla via di recupero: anche ask_specialist ci passa."""
    try:
        from bellomberg.agents.chat_tools import TETTO_TOOL_RESULT
        return int(TETTO_TOOL_RESULT)
    except Exception:
        return 6000   # il valore prudente di prima, dichiarato (v. ramo tool_result)


# La via di recupero DIPENDE DA CHI LEGGE, e sbagliarla e' la lezione (iii) del
# 21/08 ("se indirizzi a una via di recupero, ESEGUILA e conta i caratteri").
# I desk hanno `ask_specialist` e possono richiedere un report; il RED TEAM no:
# ha 3 soli tool READ-ONLY sui numeri del portafoglio (red_team.py, RED_TEAM_TOOLS)
# e nessun modo di chiedere un desk. Offrirgli quella porta sarebbe prometterne una
# che non esiste — e la sua reazione naturale al vuoto, se nessuno gli dice come
# stanno le cose, e' dedurre che una proposta non c'e'.
RECUPERO_ASK_SPECIALIST = "ask_specialist"
RECUPERO_NESSUNO = "nessuno"


def blocco_vincoli_pm(memory_db):
    """Le PAROLE VINCOLANTI del PM (feedback sulle decisioni passate + veti) per
    i round DOPO lo zero e per il red team (22/08 sera-2, voce (2b), ok PM).

    Tre stati, tutti DICHIARATI (regola 14/07 — un silenzio qui direbbe
    «nessun vincolo» anche quando il registro e' rotto):
      - c'e' qualcosa a registro -> il blocco BINDING, lo stesso TESTO che
        alimenta YOUR MEMORY di R0 (`memory_db.build_pm_binding_block`) — che
        pero' R0 puo' aver ricevuto troncato dal suo cap o perso su MEMORY
        ERROR: qui arriva sempre intero, senza cap (verificato: sul preambolo
        non esiste un tetto totale);
      - non c'e' nulla            -> una riga che lo dice: e' uno zero misurato;
      - il registro non risponde  -> una riga che dice che NON e' misurato, e
        di non dedurne l'assenza di vincoli.
    `memory_db=None` (test, prove senza DB) -> "" com'era: nessun blocco.
    """
    if memory_db is None:
        return ""
    try:
        b = memory_db.build_pm_binding_block()
    except Exception as e:
        # motivo LIMITATO (stessa regola di `_motivo_corto` in signal_engine:
        # una stringa d'errore chilometrica non deve mangiarsi il prompt), e
        # NIENTE riferimenti al Round 0: ogni round e' una conversazione NUOVA
        # e il red team un Round 0 non ce l'ha — indicare «quelli gia' visti»
        # sarebbe una via di recupero che per chi legge non esiste (review
        # 22/08 sera-2).
        msg = "%s: %s" % (type(e).__name__, e)
        if len(msg) > 300:
            msg = msg[:300] + "...[%d char non riportati]" % (len(msg) - 300)
        return ("=== PAROLE DIRETTE DEL PM — NON DISPONIBILI in questo round ===\n"
                "Il registro dei feedback e dei veti del PM non ha risposto (motivo: "
                "%s). NON dedurre che non ci siano vincoli: se un'idea somiglia a "
                "qualcosa che il PM puo' aver gia' rifiutato o vietato, dichiaralo "
                "invece di riproporla come nuova.\n\n" % msg)
    if not (b or "").strip():
        return ("=== PAROLE DIRETTE DEL PM ===\n"
                "Nessun feedback ne' veto del PM a registro (misurato adesso, non "
                "supposto).\n\n")
    # header senza promesse su cio' che «R0 ha gia' visto»: R0 puo' aver avuto
    # il blocco troncato dal suo cap o perso su MEMORY ERROR — qui arriva
    # sempre intero, ed e' l'unica cosa che si puo' affermare in ogni stato.
    return ("=== PAROLE DIRETTE DEL PM — VINCOLANTI in questo round come in ogni altro ===\n"
            + b.strip() + "\n\n")


def stato_vincoli_pm(blocco):
    """Lo STATO del blocco di `blocco_vincoli_pm`, letto dalla sua intestazione,
    per la riga di log di R1/R2 e del red team (27/08, run V9: il punto 3 della
    checklist non era misurabile perche' il blocco entrava nel prompt senza
    lasciare traccia). Non tocca la firma di `blocco_vincoli_pm`: le ancore del
    banco `prova_mutazioni_vincoli_pm.py` la citano.
    Ritorna (stato, n_righe): n_righe = decisioni rese (righe della forma
    `#<id> <azione> <ticker> | ...`), 0 negli stati senza righe."""
    b = (blocco or "").strip()
    if not b:
        return "assenti (nessun registro: memory_db None)", 0
    testa = b.splitlines()[0]
    if "NON DISPONIBILI" in testa:
        return "NON DISPONIBILI (registro guasto, dichiarato nel prompt)", 0
    if "VINCOLANTI" in testa:
        # una riga per decisione resa: si conta il MARCATORE della riga
        # (`#<id> ... | PM: ` oppure `| <simbolo> VETO ATTIVO`), non i token —
        # a registro esistono azioni a piu' parole (`ADD (new)`, `HEDGE - BUY
        # PUT`) e ticker vuoti (review 27/08, seconda passata: 22 azioni con
        # spazi e 24 ticker vuoti nel DB vero), e le parole verbatim del PM
        # possono andare a capo con una riga che inizia per `#`.
        import re as _re
        return "VINCOLANTI", sum(1 for r in b.splitlines()
                                 if _re.match(r"^#\d+ .+? \| (PM: |\S+ VETO ATTIVO)", r))
    if "Nessun feedback" in b:
        return "nessun feedback ne' veto a registro (zero misurato)", 0
    return "forma non riconosciuta (dichiarato)", 0


def riga_log_vincoli_pm(blocco):
    """Il pezzo comune della riga di log: stato + (righe, char) solo se ci sono righe."""
    stato, n = stato_vincoli_pm(blocco)
    return stato + ((" (%d righe, %d char)" % (n, len(blocco))) if n else "")


def scrivi_file_atomico(path, payload, pause=(0.15, 0.35, None)):
    """Scrive `payload` (testo) su `path` via temporaneo ACCANTO (stesso volume)
    + `os.replace`: chi legge vede il vecchio o il nuovo, mai un pezzo (27/08,
    heartbeat di F4; usata da `Blackboard` e dal ramo crash di
    consigliere_multi). Su Windows il replace cade con PermissionError se un
    altro processo tiene il bersaglio aperto in quell'istante: un tentativo per
    ogni voce di `pause` (l'ultima None = nessuna attesa dopo). Il temporaneo
    viene rimosso a ogni fallimento GESTITO; puo' restare su kill fra write e
    replace o se qualcuno lo tiene aperto, e la scrittura successiva lo tronca.
    Ritorna (ok, ultimo_errore)."""
    import os
    import time as _t
    tmp = path + ".tmp"
    ultimo = None
    for pausa in pause:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)
            return True, None
        except Exception as e:
            ultimo = e
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            if pausa is not None:
                _t.sleep(pausa)
    return False, ultimo


def _blocco_blackboard(altri, budget=None, recupero=RECUPERO_ASK_SPECIALIST):
    """I report degli altri desk. Usata dal preambolo R1/R2, da read_blackboard e
    (dal 21/08, finding D di audit/25) dal preambolo del RED TEAM.

    Tre garanzie, tutte nate da difetti misurati il 21/08 (audit/25):
      1. NESSUN DESK SPARISCE SENZA NOME. Prima il `[:9000]` sul JSON faceva
         evaporare quattro desk su cinque insieme alla loro chiave: un desk
         assente era indistinguibile da un desk che non aveva scritto nulla.
      2. LA QUOTA SI DIVIDE, non se la prende chi arriva primo. L'ordine delle
         chiavi e' l'ordine di FINE del round 0, cioe' una corsa fra thread; e il
         red team, che sta in testa per costruzione, si mangiava il 43% della
         finestra. Qui il budget e' diviso in parti uguali, e cio' che avanza dai
         report corti viene ridistribuito a quelli lunghi (piu' giri, finche' si
         redistribuisce qualcosa).
      3. SE TAGLIA, LO DICE COI NUMERI E DICE COME RECUPERARE — senza mentire
         sulla via di recupero. La prima stesura scriveva che `ask_specialist`
         «restituisce il report INTERO»: FALSO, perche' anche quel risultato passa
         dal tetto dei tool_result (12.000) e su fundamentals (19.031 char) ne
         arriva il 62%. Era la stessa malattia appena curata su read_blackboard,
         reintrodotta sul tool a cui questa funzione manda il traffico: ora il
         tetto vero e' NOMINATO, e viene letto da chat_tools, non ricopiato.
         E la via di recupero e' un PARAMETRO (`recupero`), perche' dipende da chi
         legge: col red team come secondo consumatore, la riga «chiedi con
         ask_specialist» sarebbe stata la stessa bugia una terza volta, su un
         agente che quel tool non ce l'ha.
    """
    # Un valore sconosciuto cadeva ZITTO sul ramo dei desk, cioe' si sentiva
    # promettere `ask_specialist`: il parametro nato per non offrire una porta
    # inesistente la offriva a chiunque sbagliasse una maiuscola (review 21/08).
    if recupero not in (RECUPERO_ASK_SPECIALIST, RECUPERO_NESSUNO):
        raise ValueError("recupero sconosciuto: %r (attesi %r o %r)"
                         % (recupero, RECUPERO_ASK_SPECIALIST, RECUPERO_NESSUNO))
    if budget is None:
        budget = MAX_CHAR_BLACKBOARD
    voci = []
    for k, v in (altri or {}).items():
        # difensivo: il blackboard e' scritto da piu' thread e da codice vecchio.
        # Un valore non-dict qui faceva alzare AttributeError e la run moriva nel
        # preambolo, cioe' prima di qualunque analisi (trovato dalla review 21/08
        # con un blackboard {"ok": True}).
        if isinstance(v, dict):
            rnd, testo = v.get("round"), v.get("report")
        else:
            rnd, testo = None, v
        voci.append((k, "n.d." if rnd is None else rnd,
                     "" if testo is None else str(testo)))
    if not voci:
        return "(nessun altro desk ha ancora scritto)"

    interi = sum(len(t) for _, _, t in voci)
    quote = {k: len(t) for k, _, t in voci}
    if interi > budget:
        # riparto equo con redistribuzione dell'avanzo dai report che ci stanno
        quote = {k: 0 for k, _, _ in voci}
        residuo = budget
        aperti = [k for k, _, _ in voci]
        lung = {k: len(t) for k, _, t in voci}
        while aperti and residuo > 0:
            fetta = residuo // len(aperti)
            if fetta <= 0:
                break
            chiusi = []
            for k in aperti:
                dare = min(fetta, lung[k] - quote[k])
                quote[k] += dare
                residuo -= dare
                if quote[k] >= lung[k]:
                    chiusi.append(k)
            if not chiusi:
                break
            aperti = [k for k in aperti if k not in chiusi]

    tetto_tool = _tetto_tool_result()
    pezzi = []
    tagliati = []
    for k, rnd, testo in voci:
        q = quote.get(k, len(testo))
        if not testo:
            # "report INTERO, 0 caratteri" affermerebbe il falso su un buco
            pezzi.append("--- %s (Round %s) — NESSUN TESTO: il desk non ha scritto "
                         "nulla in questo round (non e' un taglio) ---" % (k.upper(), rnd))
        elif q >= len(testo):
            pezzi.append("--- %s (Round %s) — report INTERO, %d caratteri ---\n%s"
                         % (k.upper(), rnd, len(testo), testo))
        elif recupero == RECUPERO_NESSUNO:
            tagliati.append("%s (%d su %d)" % (k, q, len(testo)))
            pezzi.append(
                "--- %s (Round %s) — ⚠️ NE LEGGI %d SU %d CARATTERI: il resto NON e' "
                "qui e NON e' recuperabile (nessuno dei tool di cui disponi "
                "restituisce il report di un desk). NON dedurre che cio' che non "
                "vedi non esista: lavora su cio' che leggi ---\n%s"
                % (k.upper(), rnd, q, len(testo), testo[:q]))
        else:
            # (Voce B 25/08: qui c'era un ramo dedicato a `red_team` — «l'unica
            # voce NON richiedibile con ask_specialist» — vero finche' il tool
            # cercava la chiave grezza `_red_team`. Ora get_latest risolve
            # l'alias pubblico: la via di recupero vale anche per lui, e la
            # riga dedicata sarebbe diventata una bugia.)
            tagliati.append("%s (%d su %d)" % (k, q, len(testo)))
            pezzi.append(
                "--- %s (Round %s) — ⚠️ NE LEGGI %d SU %d CARATTERI: il resto NON e' "
                "qui. Puoi chiedere questo desk da solo con ask_specialist(\"%s\", ...), "
                "ma ATTENZIONE: anche quel risultato passa dal tetto dei tool_result "
                "(%d caratteri) e sopra quella soglia esce con [truncated] in coda ---"
                "\n%s"
                % (k.upper(), rnd, q, len(testo), k, tetto_tool, testo[:q]))

    testa = ""
    if tagliati and recupero == RECUPERO_NESSUNO:
        # ⚠️ la frase diceva «i tuoi tool sono read-only sui numeri del portafoglio»:
        # falsa nello stato in cui il registro tool non carica (red_team.py mette
        # `tools_schema = []` e prosegue), e comunque composta PRIMA che tools_schema
        # esista. La clausola qui sotto e' vera anche con zero tool.
        testa = ("⚠️ La finestra dei report (%d caratteri) non basta per %d report "
                 "su %d: %s. Nessun desk e' stato tolto — sono tagliati, e ognuno "
                 "dice di quanto. Il resto NON e' recuperabile: nessuno dei tool di "
                 "cui disponi restituisce il report di un desk. Quindi NON dedurre "
                 "l'assenza di una tesi o di una proposta dal fatto di non vederla: "
                 "dove il testo si interrompe, dillo invece di concludere.\n\n"
                 % (budget, len(tagliati), len(voci), "; ".join(tagliati)))
    elif tagliati:
        # (Voce B 25/08: qui c'era una `_eccezione` per `red_team` — «NON e'
        # richiedibile con ask_specialist» — flippata insieme alla cura
        # dell'alias: ora la riga generica e' vera anche per lui.)
        testa = ("⚠️ La finestra della blackboard (%d caratteri) non basta per %d "
                 "report su %d: %s. Nessun desk e' stato tolto — sono tagliati, e "
                 "ognuno dice di quanto. Se una decisione dipende da una parte che "
                 "non vedi, chiedi quel desk con ask_specialist prima di concludere "
                 "(tenendo presente il tetto di %d caratteri dei tool_result).\n\n"
                 % (budget, len(tagliati), len(voci), "; ".join(tagliati), tetto_tool))
    return testa + "\n\n".join(pezzi)


# Regole di lingua e stile comuni a TUTTI gli specialisti (#180). Iniettate in
# coda al system_prompt di ogni agente: un punto solo, allinea tutti e 7.
SPECIALIST_STYLE_RULES = """
# ============================================================
# LINGUA E STILE (vale per il tuo report al Capo)
# ============================================================
- Scrivi in ITALIANO professionale e scorrevole, in prosa. MAI stile telegrafico a trattini, MAI inglese a meta' frase.
- I termini tecnici inglesi consolidati sono ammessi come jargon (long, short, call, put, hedge, spread, premium, beta, Sharpe, IV, skew, gamma), ma la frase che li contiene e' in italiano.
- Alla PRIMA menzione di OGNI titolo scrivi il NOME ESTESO col ticker tra parentesi: "Deutsche Bank (DBK.DE)", "Salesforce (CRM)". Mai sigle nude non introdotte.
- Ogni numero che citi DEVE venire da un tool che hai chiamato. Niente cifre inventate. Spiega in una riga cosa significa la metrica (es. "Sharpe -1,2: distrugge valore corretto per il rischio").
- Sii denso e operativo: il Capo legge il tuo report per decidere. Dagli tesi con numeri e una conclusione chiara, non un tema di scuola.

# ============================================================
# ARSENALE TOOL (#195) - USALI, non limitarti ai soliti 2-3
# ============================================================
- Hai accesso a MOLTI tool, inclusi DATI A PAGAMENTO costosi. Chiamali in modo AGGRESSIVO quando rilevanti per la tesi: non chiamarli e' spreco di alpha e di soldi.
- Nel TUO dominio, oltre ai tool base, usa SEMPRE quelli pertinenti tra:
  * QUANT: get_advanced_metrics, get_edge_scan, get_portfolio_montecarlo, get_portfolio_factors, get_cot_positioning, get_vix_term_structure, get_position_doctor, compute_gex.
  * OPTIONS: compute_gex (dealer gamma exposure), get_vol_surface_summary, get_options_chain_polygon, get_portfolio_garch - NON solo get_options_data.
  * FUNDAMENTALS: get_valuation (il TUO DCF a sub-settori buy-side, usalo per il fair value invece di stimare a occhio), get_consensus_estimates (cosa si aspetta GIA' il mercato: stime, revisioni 30/90gg, target price — una view uguale al consensus non e' un edge), get_insider_trades (Form 4), get_gov_contracts, get_congress_trades, get_13f_holdings, get_earnings_calendar, get_corporate_events_for_ticker.
  * EVENTDESK: get_congress_trades (smart money del Congresso USA), get_lobbying (esposizione regolatoria), get_gov_contracts (segnale duro di ricavi), get_corporate_events_for_ticker, get_insider_trades, get_13f_holdings - NON solo Polymarket e news feed.
  * MACRO: get_cot_positioning (posizionamento futures CFTC), get_vix_term_structure, get_news_briefing, get_macro_news_by_topic.
  * CRYPTO: get_hyperliquid_intel (funding, OI, premio perp).
- Regola: se un tool del tuo set e' rilevante per quello che stai analizzando e non l'hai chiamato, stai lasciando alpha sul tavolo. Chiamalo.
""".strip()


def _blocco_round_precedente(board, desk, round_n, others):
    """Round proprio esatto e colleghi condividono il budget gia' esistente.

    Priorita' al proprio lavoro, senza cercare un latest o un'altra run.
    Il budget conta i caratteri dei report, come _blocco_blackboard; le
    intestazioni diagnostiche sono extra, non una garanzia sui token LLM.
    """
    previous = round_n - 1
    report = board.read(desk, previous)
    heading = "OWN PREVIOUS ROUND (same run, %s, R%d):\n" % (desk, previous)
    if not isinstance(report, str) or not report.strip():
        own = heading + "unavailable: exact previous report absent or invalid. Do not reconstruct it."
        used = 0
    else:
        used = min(len(report), MAX_CHAR_BLACKBOARD)
        if used == len(report):
            own = heading + "report INTERO, %d caratteri\n" % len(report) + report
        else:
            # Equivalente a RECUPERO_NESSUNO, ma per il ROUND ESATTO:
            # ask_specialist legge latest e non garantisce quel contenuto.
            own = heading + (
                "NE LEGGI %d SU %d CARATTERI: il resto NON e' qui; "
                "nessun tool garantisce questo round esatto; latest non e' un sostituto. "
                "Dichiara il limite, non dedurre l'assenza di tesi o proposte "
                "dalla parte non visibile.\n" % (used, len(report))) + report[:used]
    return own + "\n\nBLACKBOARD:\n" + _blocco_blackboard(
        others, budget=MAX_CHAR_BLACKBOARD - used)


class Specialist:
    """Base class. Subclass deve definire: name, system_prompt, tools_used."""
    name = "BASE"
    role = "Base specialist"
    system_prompt = "You are a specialist. Override this in subclass."
    tools_used = []
    model = None   # 05/09: per desk e per round, risolto in _model_for_round dal .env
    # P1 26/07: valorizzato SOLO se il ripiego sul registro legacy scatta. Attributo
    # di classe = c'e' sempre, anche su istanze costruite da codice vecchio.
    _arsenale_degradato = None

    def __init__(self, blackboard, client=None):
        self.blackboard = blackboard
        self._sector_bundles = {}
        self._research_decision_links = {}
        # #196: timeout duro -> una chiamata appesa fallisce e la run prosegue.
        # 27/08: il numero segue il cap di output (TIMEOUT_SPECIALIST_S, 450 s a 16k):
        # a 240 s una call da 16k a 65-80 tok/s sarebbe finita in timeout + retry.
        if getattr(blackboard, "run_scope", "weekly") == "trade_idea":
            raw_client = client or OpenRouterClient(timeout=TIMEOUT_SPECIALIST_S, max_retries=0)
            self._owned_trade_idea_client = raw_client if client is None else None
            gate = getattr(blackboard, "budget_gate", None)
            if gate is None:
                raise ValueError("Trade Idea requires a per-run LLM budget gate")
            self.client = gate.wrap_client(raw_client, role="specialist:" + self.name)
        else:
            self.client = client or OpenRouterClient(timeout=TIMEOUT_SPECIALIST_S, max_retries=1)
            self._owned_weekly_client = self.client if client is None else None

    def _model_for_round(self, round_n):
        """Ripipeline 15/07 (ok PM): R0 recon sul modello leggero, analisi R1 e
        replica R2 sul modello pieno del desk. 05/09: entrambi dal .env
        (CONSIGLIERE_R0_MODEL; CONSIGLIERE_<DESK>_MODEL o CONSIGLIERE_MODEL)."""
        if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea":
            from bellomberg.agents.trade_idea import model_for_role
            return model_for_role("specialist", self.blackboard)  # MOD-TI 06/10: contratto della run
        return _modello_llm("consigliere", self.name, round_n)

    def _chiama_modello(self, **kw):
        """Una call del desk. 02/10: col client vero si va in STREAMING — una call
        non-streaming resta muta finche' il modello non ha finito di ragionare, e le
        run 28/09 e 01/10 hanno perso fundamentals R1 su «Server disconnected
        without sending a response» dopo ~200 s di silenzio. get_final_message()
        restituisce lo stesso Messaggio di create(). I client finti dei test (solo
        create) restano sulla via di prima."""
        # Trade Idea resta sulla call non-streaming del PM: il suo budget gate
        # (_BudgetedClient) prenota e riconcilia solo messages.create.
        if (getattr(self.blackboard, "run_scope", "weekly") != "trade_idea"
                and isinstance(self.client, _OPENROUTER_CLIENT_CLASS)):
            with self.client.messages.stream(**kw) as _s:
                return _s.get_final_message()
        return self.client.messages.create(**kw)

    def _build_tools_schema(self):
        if (getattr(self, '_task_context', None) or {}).get('kind') == 'research_objection_completion':
            from bellomberg.agents.trade_idea import trade_idea_review_tools
            from bellomberg.valuation.company_dossier import company_dossier_tool_schema
            return [tool for tool in trade_idea_review_tools(self.name)
                    if tool['name'] in RESEARCH_REPLY_LOCAL_TOOLS] + [company_dossier_tool_schema(), {
                'name': 'ask_specialist', 'description': 'Read an already paid desk report; no new consultation.',
                'input_schema': {'type': 'object', 'properties': {
                    'specialist': {'type': 'string'}, 'question': {'type': 'string'}},
                    'required': ['specialist', 'question']}}]
        # #195: REGISTRO RICCO delle chat (36 tool, paid data) col subset per-agente.
        # I nomi specialista (quant/macro/options/eventdesk/crypto/fundamentals)
        # coincidono con gli agent_id delle chat: stesso arsenale del Capo interattivo.
        try:
            from bellomberg.agents import chat_tools
            filtered = list(chat_tools.get_tools_for_agent(self.name))
        except Exception as e:
            if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea":
                raise RuntimeError("Trade Idea tool registry unavailable") from e
            # P1 26/07: ripiego sul registro LEGACY (21 nomi contro i 51 vivi) e
            # filtrato per `tools_used`, che e' documentazione stantia (fundamentals
            # ne dichiara 13 e ne riceve 24). Prima era muto: ora e' dichiarato su
            # log + registro + prompt del modello (v. _arsenale_degradato).
            filtered = [t for t in agent_tools.TOOLS_SCHEMA if t["name"] in self.tools_used]
            self._arsenale_degradato = _dichiara_fallback(
                "_build_tools_schema[" + self.name + "]", e,
                "registro LEGACY agent_tools al posto di chat_tools: " +
                str(len(filtered)) + " tool invece del subset vivo")
        _notes_context = None
        if (getattr(self.blackboard, 'weekly_store', None) is not None
                and 'research_notes_policy' in self.blackboard.weekly_store.context.get('contract', {})):
            from bellomberg.core.current_facts import research_notes_for_board
            _notes_context = research_notes_for_board(self.blackboard)
        if _notes_context is not None:
            from copy import deepcopy
            filtered = deepcopy(filtered)
            for tool in filtered:
                if tool['name'] == 'add_research_note':
                    tool['input_schema']['properties']['note_ids'] = {
                        'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1,
                        'description': 'Delivered original PM question IDs, all on the given decision_id.'}
                    tool['input_schema']['required'] = ['decision_id', 'note', 'note_ids']
        from bellomberg.valuation.company_dossier import company_dossier_tool_schema
        filtered.append(company_dossier_tool_schema())
        if callable(getattr(self.blackboard, "company_source_session", None)):
            from bellomberg.agents.company_research_tools import company_source_tools
            filtered.extend(company_source_tools())
        if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea":
            # A Trade Idea is research on one accepted candidate, never a channel
            # for writing guidance or research notes before the result is routed.
            filtered = [tool for tool in filtered
                        if tool["name"] not in {"add_guidance", "add_research_note"}]
            if (self.name == "fundamentals" and self.blackboard.current_round == 1
                    and getattr(self.blackboard, "model_phase", None) == "building"):
                from copy import deepcopy
                for index, tool in enumerate(filtered):
                    if tool["name"] == "get_valuation":
                        tool = deepcopy(tool)
                        tool["description"] = (
                            "Put scenario motivations in submit_candidate_model_plan.plan.scenario_rationale "
                            "in the explicit plan before compilation. The final plan is compiled automatically after R1. "
                            "Use get_valuation(ticker) only to compile it earlier when final.")
                        tool["input_schema"] = {
                            "type": "object", "properties": {"ticker": tool["input_schema"]["properties"]["ticker"]},
                            "required": ["ticker"], "additionalProperties": False}
                        filtered[index] = tool
                        break
            from bellomberg.agents.trade_idea import trade_idea_review_tools
            filtered.extend(trade_idea_review_tools(self.name))
        # review 15/07: i nomi dei colleghi derivano dal roster VERO, non da una
        # lista hardcoded (la fusione 7->6 ha mostrato quante copie ne giravano)
        try:
            from bellomberg.agents.specialists import ALL_SPECIALISTS as _ALL_SP
            _peers = ", ".join(s.name for s in _ALL_SP if s.name != self.name)
        except Exception as e:
            # la lista hardcoded e' la stessa che la fusione 7->6 ha reso stantia:
            # se torna in uso il modello puo' interrogare un collega che non esiste.
            _peers = "macro, options, eventdesk, fundamentals, crypto, quant"
            _dichiara_fallback("_build_tools_schema[" + self.name + "].peers", e,
                               "roster HARDCODED (puo' essere stantio) al posto di "
                               "ALL_SPECIALISTS")
        ask_tool = {
            "name": "ask_specialist",
            "description": ("Read the latest report from another specialist on the team. "
                            "Available: " + _peers + " - plus 'red_team' (the adversarial "
                            "critique, available once it has run, between Round 1 and "
                            "Round 2)."),
            "input_schema": {
                "type": "object",
                "properties": {
                    "specialist": {"type": "string", "description": "Name: " + _peers + ", red_team"},
                    "question": {"type": "string"}
                },
                "required": ["specialist", "question"]
            }
        }
        if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea":
            ask_tool["description"] = (
                "R1: Fundamentals, while building the model, sends a real targeted question to a peer. "
                "Supply the explicit draft assumptions and evidence IDs. R2: read the peer's latest report; "
                "no new consultation is dispatched. R0: unavailable. Available: " + _peers)
            ask_tool["input_schema"]["properties"].update({
                "draft_assumptions": {"type": "object"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
            })
            ask_tool["input_schema"]["required"].extend(["draft_assumptions", "evidence_refs"])
            if self.name == "fundamentals" and getattr(self.blackboard, "model_phase", None) == "building":
                ask_tool["input_schema"]["properties"]["supersedes_failed"] = {
                    "type": "object", "additionalProperties": False,
                    "description": "Replace only a failed current consultation or a known-cost native max_tokens partial with a new economic question after receiving at least three complete peer answers in a prior native turn; preserve its audit.",
                    "properties": {"consultation_id": {"type": "string"},
                        "consultation_row_sha256": {"type": "string"}, "rationale": {"type": "string"},
                        "after_consultation_ids": {"type": "array", "items": {"type": "string"}}},
                    "required": ["consultation_id", "consultation_row_sha256", "rationale", "after_consultation_ids"]}
        filtered.append(ask_tool)
        filtered.append({
            "name": "read_blackboard",
            # 21/08: diceva "Read the full blackboard - all specialists' latest
            # reports", ma il risultato passa dal tetto dei tool_result (12.000
            # char) e "full" era falso: il payload misurato per quant pesa 67.312 char
            # e ne arrivavano 12.000, con gli stessi due desk visibili di
            # prima. La description la legge
            # l'agente PRIMA di decidere se chiamare il tool.
            "description": ("Latest reports of all specialists. ATTENZIONE: il "
                            "risultato passa dal tetto dei tool_result, quindi i "
                            "report lunghi arrivano TAGLIATI — ma ogni desk dice "
                            "quanto ne stai leggendo e nessuno sparisce. Per un "
                            "desk solo usa ask_specialist: ne ricevi di piu', non "
                            "necessariamente tutto (anche quello passa dallo "
                            "stesso tetto e sopra la soglia esce con [truncated])."),
            "input_schema": {"type": "object", "properties": {}}
        })
        if is_research_mode(self.blackboard):
            filtered = [tool for tool in filtered if tool['name'] not in RESEARCH_UNAVAILABLE_TOOLS]
            if getattr(self.blackboard, 'run_scope', 'weekly') == 'trade_idea':
                ask_tool['description'] = (
                    'Read a colleague\'s actual research report; preserve its evidence and dissent. '
                    'R0 is independent. R1 Fundamentals can compare the completed peer reports. '
                    'R2 addresses the common sealed research thesis and Red Team objections.')
                ask_tool['input_schema']['required'] = ['specialist', 'question']
                ask_tool['input_schema']['properties'].pop('supersedes_failed', None)
        return filtered

    def _execute_meta_tool(self, name, input_):
        task = getattr(self, '_task_context', None) or {}
        if task.get('kind') == 'research_objection_completion':
            if name not in RESEARCH_REPLY_LOCAL_TOOLS:
                return {'ok': False, 'error': 'Reply completion only reads acquired evidence and records missing replies.'}
            if name == 'respond_trade_idea_objection':
                ident = input_.get('objection_id') if isinstance(input_, dict) else None
                row = next((row for row in self.blackboard.data.get('_objections', [])
                            if row['objection']['id'] == ident), None)
                if (ident in task['objection_ids'] and row is not None and row.get('response')
                        and row['objection']['desk'] == self.name
                        and all(row.get(key) == input_.get(key) for key in
                                ('response', 'state', 'evidence_refs', 'model_revision_id'))):
                    return {'ok': True, 'objection_id': ident, 'state': row['state']}
                if ident not in task['objection_ids'] or row is None or row.get('response'):
                    return {'ok': False, 'error': 'Only the exact still-missing replies in this task may be recorded.'}
        if is_research_mode(self.blackboard) and name in RESEARCH_UNAVAILABLE_TOOLS:
            return {'ok': False, 'status': 'not_available_in_research_mode',
                    'reason': 'This run produces company research and memo/PDF; workbook operations are disabled.'}
        if ((getattr(self, "_task_context", None) or {}).get("kind") == "model_authoring_completion"
                and name not in MODEL_AUTHORING_LOCAL_TOOLS):
            return {"ok": False, "error": "This model-completion phase only reads admitted evidence and authors/compiles the plan. Paid peer consultations and external research are preserved, not repeated."}
        if name == "get_valuation":
            from bellomberg.agents.company_research_tools import source_research_guard
            # Include compilation AND record_valuation, so another desk cannot
            # admit a source between choosing the dossier and sealing its model.
            with source_research_guard(self.blackboard):
                return self._execute_meta_tool_unlocked(name, input_)
        return self._execute_meta_tool_unlocked(name, input_)

    def _execute_meta_tool_unlocked(self, name, input_):
        if (name == "search_past_memos" and getattr(self.blackboard, "run_scope", None) == "weekly"
                and "semantic_memory_policy" in (getattr(getattr(self.blackboard, "weekly_store", None),
                                                        "context", {}).get("contract") or {})):
            policy = self.blackboard.weekly_store.context["contract"]["semantic_memory_policy"]
            if policy != "weekly-published-chunks/1":
                return {"status": "unavailable", "reason": "unsupported_policy",
                        "memory_scope": "weekly_operational", "count": 0, "memos": []}
            # Internal run policy, never a model-supplied flag. Keep tinput/journal
            # untouched; pending paid tool outputs replay before reaching this node.
            try:
                from bellomberg.agents.agent_tools import tool_search_past_memos
                from bellomberg.agents.chat_tools import _stamp
                result = tool_search_past_memos(query=input_["query"],
                    n_results=input_.get("n_results", 5), operational_only=True)
                return _stamp(result, "published weekly memo chunks: exact SQLite provenance")
            except Exception:
                # Do not fall through to the unfiltered archive dispatcher.
                return {"status": "unavailable", "reason": "operational_search_unavailable",
                        "memory_scope": "weekly_operational", "count": 0, "memos": []}
        from bellomberg.agents.company_research_tools import TOOL_NAMES, dispatch_company_source
        if name in TOOL_NAMES:
            return dispatch_company_source(self.blackboard, name, input_, max_chars=_tetto_tool_result(),
                consultation=(getattr(self, "_task_context", None) or {}).get("consultation") is True)
        if name == "read_company_dossier":
            from bellomberg.valuation.company_dossier import read_company_dossier
            return read_company_dossier(self.blackboard, input_, max_chars=_tetto_tool_result())
        consultation = (getattr(self, "_task_context", None) or {}).get("consultation") is True
        plain_valuation = (name == "get_valuation" and input_.get("method_records") is None
            and not input_.get("analysis_context") and not any(
                value not in (None, "", [], {}) for key, value in input_.items()
                if key not in ("ticker", "analysis_context", "method_records")))
        if consultation and (name in {"ask_specialist", "review_candidate_model",
                "submit_candidate_model_plan", "respond_trade_idea_objection",
                "read_candidate_model_consultation", "add_guidance", "add_research_note"}
                or name == "get_valuation" and (not plain_valuation
                    or getattr(self.blackboard, "run_scope", "weekly") != "trade_idea")):
            return {"ok": False, "status": "blocked_consultation",
                    "reason": "A targeted consultation cannot recurse or mutate the common model."}
        if (getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
                and name in {"review_candidate_model", "respond_trade_idea_objection",
                             "get_candidate_model_inputs", "submit_candidate_model_plan",
                             "read_candidate_model_consultation", "read_candidate_source"}):
            from bellomberg.agents.trade_idea import handle_trade_idea_review_tool
            return handle_trade_idea_review_tool(self.blackboard, self.name, name, input_)
        research = is_research_mode(self.blackboard)
        building_owner = (self.name == "fundamentals" and self.blackboard.current_round == 1
                          and (research or getattr(self.blackboard, "model_phase", None) == "building"))
        independent = getattr(self.blackboard, "independent_round", None)
        if (getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
                and (self.blackboard.current_round == 0 or independent in (0, 1)
                     and self.blackboard.current_round <= independent and not building_owner)
                and name in {"ask_specialist", "read_blackboard"}):
            return {"status": "blocked_independent_round", "ok": False,
                    "reason": "Complete the independent first analysis before reading another desk."}
        if (getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
                and name in {"add_guidance", "add_research_note"}):
            return {"ok": False, "error": "Trade Idea: tool di scrittura disabilitato"}
        if (name == "add_research_note" and getattr(self.blackboard, 'weekly_store', None) is not None
                and 'research_notes_policy' in self.blackboard.weekly_store.context.get('contract', {})):
            from bellomberg.core.current_facts import research_notes_for_board, add_frozen_research_reply
            if research_notes_for_board(self.blackboard) is not None:
                return add_frozen_research_reply(self.blackboard, input_.get('decision_id'),
                                                 input_.get('note'), input_.get('note_ids'),
                                                 event_id=getattr(self, '_research_note_event_id', None))
        if name == "ask_specialist":
            target = input_.get("specialist", "").lower()
            q = input_.get("question", "")
            if (getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
                    and building_owner and not research):
                draft, refs = input_.get("draft_assumptions"), input_.get("evidence_refs")
                if (not isinstance(q, str) or not q.strip() or not isinstance(draft, dict)
                        or not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs)):
                    return {"ok": False, "status": "invalid_consultation",
                            "reason": "Question, draft_assumptions and evidence_refs are required."}
                callback = getattr(self.blackboard, "consult_specialist", None)
                if not callable(callback):
                    return {"ok": False, "status": "consultation_unavailable",
                            "reason": "No live consultation callback is bound to this run."}
                recovery = {"supersedes_failed": input_["supersedes_failed"]} if "supersedes_failed" in input_ else {}
                return callback(requester=self.name, target=target, question=q,
                                draft_assumptions=draft, evidence_refs=refs, **recovery)
            latest = self.blackboard.get_latest(target)
            if not latest:
                if target in ("news", "politics"):
                    return {"specialist": target, "note": "RETIRED: merged into 'eventdesk' on 15/07/2026. Ask 'eventdesk' instead."}
                if target in ("red_team", "_red_team"):
                    # Voce B 25/08: il generico «Not yet available ... YET»
                    # promette un arrivo. Ma la frase dev'essere vera anche in
                    # R0/R1 di una run sana, dove il red team girera' fra R1 e
                    # R2 (review 25/08: «in this run: it did not run» li' era
                    # falsa): condizionali, mai asserzioni sul futuro.
                    return {"specialist": "red_team", "note":
                            "No critique on the blackboard at this point: the "
                            "red team runs ONCE, between Round 1 and Round 2. "
                            "If it already ran, it produced nothing usable - "
                            "and the theses were NOT adversarially attacked: "
                            "do not infer they were validated."}
                return {"specialist": target, "note": "Not yet available - they have not produced output in any round yet."}
            out = {"specialist": target, "round": latest["round"], "question_asked": q, "report": latest["report"]}
            if target in ("red_team", "_red_team"):
                # Il registro si consegna SEMPRE com'e'; ma un segnaposto/rifiuto
                # arriva a un desk che il preambolo R2 ha appena avvisato — la
                # `note` e' la stessa verita' nel tool_result, cosi' le due
                # cornici non si contraddicono (review 25/08, I2).
                try:
                    from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
                    _cnu = motivo_critica_non_utilizzabile(latest["report"])
                except Exception:
                    _cnu = None
                if _cnu:
                    out["note"] = ("This is NOT a usable critique: the register "
                                   "holds a declared placeholder (%s). There are "
                                   "no objections to reply to - do not infer the "
                                   "theses were validated." % _cnu)
            return out
        elif name == "read_blackboard":
            # 21/08: tornava il dict grezzo, che a valle veniva serializzato e
            # tagliato al tetto tool_result -> uscivano DUE desk su sei e gli altri
            # sparivano senza nome, lo stesso difetto del preambolo. E togliere il
            # cap 3.500 al red team lo aveva PEGGIORATO del 19%. Ora passa dalla
            # stessa funzione del preambolo, col budget del tetto tool_result meno
            # un margine per la busta JSON: nessun desk sparisce, e cio' che non
            # entra e' dichiarato per desk.
            _t = _tetto_tool_result()
            return {"blackboard": _blocco_blackboard(
                self.blackboard.summary_for_specialist(self.name),
                budget=int(_t * 0.75))}
        else:
            # #195: esegui dal dispatcher RICCO delle chat (gestisce tutti i tool paid).
            try:
                from bellomberg.agents import chat_tools
                # V6 Lotto 3 (review B4): il registro guidance sa CHI ha scritto
                if name == "get_valuation":
                    ticker = str(input_.get("ticker") or "").upper()
                    target = getattr(self.blackboard, "target_ticker", None)
                    if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea" and ticker != target:
                        return {"ok": False, "error": "Trade Idea valuation ticker fuori scope: " + ticker,
                                "exclude_from_action_table": True}
                    existing = self.blackboard.valuation_results.get(ticker) or {}
                    plain_request = (input_.get("method_records") is None and not input_.get("analysis_context")
                        and not any(v not in (None, "", [], {}) for k, v in input_.items()
                                    if k not in ("ticker", "analysis_context", "method_records")))
                    if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea":
                        if building_owner and not consultation:
                            builder = getattr(self.blackboard, "build_candidate_model", None)
                            if not callable(builder):
                                return {"ok": False, "error": "Trade Idea model builder is not bound to this run."}
                            return builder(input_)
                        if not plain_request:
                            return {"ok": False, "error": "Trade Idea model is read-only; use review_candidate_model for a motivated revision."}
                        if not existing:
                            return {"ok": False, "error": "Verified initial candidate model is absent; research cannot create it."}
                        return {"ok": True, "data": {**existing, "reused_in_run": True},
                                "_source": "get_valuation: exact Trade Idea candidate generation"}
                    if (plain_request
                            and existing.get("request_origin") == "committee-orchestrator"):
                        result = chat_tools._stamp({**existing, "reused_in_run": True},
                                                  "get_valuation: same committee run")
                    else:
                        result = chat_tools.dispatch(name, input_, caller="specialista-run:" + self.name,
                            prepared_bundle=getattr(self, "_sector_bundles", {}).get(ticker),
                            valuation_preparer=getattr(self.blackboard, "valuation_preparer", None))
                    payload = result.get("data") if isinstance(result.get("data"), dict) else result
                    if payload.get("acquisition_snapshot"):
                        self._sector_bundles[ticker] = payload["acquisition_snapshot"]
                    self.blackboard.record_valuation(ticker, payload, self.name)
                    db = getattr(self.blackboard, "memory_db", None)
                    decision_id = getattr(self, "_research_decision_links", {}).get(ticker)
                    if db is not None and decision_id and payload.get("snapshot_id"):
                        try:
                            db.link_valuation_snapshot(payload["snapshot_id"],
                                generation_id=payload["generation_id"], decision_id=decision_id)
                        except Exception as exc:
                            payload["research_snapshot_error"] = type(exc).__name__ + ": " + str(exc)
                    return result
                # R-CASCATA (07/10): l'ultimo periodo pubblicato si calcola al cutoff della run, non a «oggi»
                return chat_tools.dispatch(name, input_, caller="specialista-run:" + self.name,
                                           **_as_of_freschezza(name, self.blackboard))
            except Exception as e:
                if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea":
                    return {"ok": False, "error": "Trade Idea tool unavailable: "
                            + type(e).__name__ + ": " + str(e)[:180]}
                if name == "get_valuation":
                    failure = {"ok": False, "error": "Acquisizione/valutazione settoriale KO: " + str(e),
                               "exclude_from_action_table": True}
                    self.blackboard.record_valuation(str(input_.get("ticker") or "").upper(), failure, self.name)
                    return failure
                # P1 26/07: il ripiego copre 21 nomi su 51 -> per gli altri 30
                # `execute_tool` risponde "Tool sconosciuto", ma lo SCAMBIO di
                # dispatcher era muto. Ora e' dichiarato anche AL MODELLO, che
                # deve citarlo invece di far finta che il tool non esista.
                dichiarato = _dichiara_fallback(
                    "_execute_meta_tool[" + self.name + "->" + str(name) + "]", e,
                    "dispatcher LEGACY agent_tools.execute_tool")
                ripiego = agent_tools.execute_tool(name, input_)
                if isinstance(ripiego, dict):
                    return {"_fallback_dichiarato": dichiarato, **ripiego}
                return {"_fallback_dichiarato": dichiarato, "result": ripiego}

    def _build_memory_block(self):
        """Costruisce blocco YOUR MEMORY da iniettare nel prompt round 0.
        Include ultimi 3 report tuoi, feedback PM specifici, decisioni recenti."""
        from bellomberg.core.reflection_policy import board_enabled, scorecard_for
        _reflection36 = board_enabled(self.blackboard)
        if not self.blackboard.memory_db:
            return ""
        _memory_kwargs = ({'track_record_snapshot': scorecard_for(self.blackboard.weekly_store)}
                          if _reflection36 else {})
        try:
            # 4100 -> 8000 il 20/08 (ok PM "aumentiamo i tetti"): la memoria vera
            # misurava 3.874 char, cioe' il 94% del cap — un margine di 226 char su
            # un blocco che contiene i FEEDBACK VINCOLANTI e i veti del PM. Bastava
            # un commento in piu' del PM e la coda cadeva.
            # ⚠️ RETTIFICA 21/08 (audit/25): qui c'era scritto che "i commenti del PM
            # arrivano fino a 2.000 caratteri l'uno (prima 120-200)". Era FALSO per
            # QUESTO blocco. Il 20/08 (commit de623ac) ho curato l'altro canale —
            # current_facts.pm_theses_block, dove tesi e pm_rationale dei trade sono
            # passati da 260/200 a interi — e memory_db.py non e' mai stato nel diff:
            # i feedback VINCOLANTI restavano tagliati a 120. Ho alzato il tetto
            # giusto lasciando acceso il taglio vero, e ho scritto la frase come se
            # valesse per entrambi. Curato il 21/08: memory_db.MAX_CHAR_FEEDBACK_PM.
            return "\n\n" + self.blackboard.memory_db.build_specialist_memory_context(
                self.name, max_chars=MAX_CHAR_MEMORIA_SPECIALISTA, **_memory_kwargs)
        except Exception as e:
            return "\n\n[MEMORY ERROR: " + str(e) + "]"

    def compute_score(self):
        """Override nei subclass: ritorna uno score deterministico (dict) o None.
        Pattern #186: il numero sta nel codice (rubric a punti), l'LLM lo NARRA."""
        return None

    def _build_round_context(self, round_n):
        task = getattr(self, '_task_context', None) or {}
        if task.get('kind') == 'research_objection_completion':
            from bellomberg.agents.trade_idea import review_evidence_catalog
            descriptor = self.blackboard.data['_research_reply_completions'][self.name]
            return ('RESEARCH OBJECTION COMPLETION: preserve the paid reports and their uncertainty. '
                'Register a genuine response to EVERY supplied material objection using '
                'respond_trade_idea_objection and its exact ID before finishing. An unresolved question '
                'can remain state=open, or a justified concession state=conceded, with a substantive '
                'explanation; do not invent evidence or force closure. Use state=answered only when '
                'the supplied exact evidence IDs support the reply. Do not repeat broad research. '
                'All tools are restricted to already acquired evidence and missing replies.\n'
                + json.dumps({'task': task, 'objections': descriptor['objections'],
                    'original_R2_report': self.blackboard.read(self.name, 2),
                    'sealed_research': research_context(self.blackboard),
                    'exact_evidence_ids': review_evidence_catalog(self.blackboard)}, ensure_ascii=False))
        if is_research_mode(self.blackboard) and getattr(self.blackboard, 'run_scope', 'weekly') == 'trade_idea':
            board = self.blackboard
            consultation = (getattr(self, '_task_context', None) or {}).get('consultation') is True
            facts = {'target_ticker': board.target_ticker, 'pm_view': getattr(board, 'pm_view', None),
                'candidate_history': getattr(board, 'candidate_history', None),
                'decision_context': board.data.get('_decision_context'),
                'portfolio_context': board.data.get('_portfolio_context'),
                'own_recon': board.read(self.name, 0) if round_n else None}
            if round_n == 2:
                facts['shared_research'] = research_context(board)
                facts['objections'] = [row for row in board.data.get('_objections', [])
                                       if row.get('objection', {}).get('desk') == self.name]
            if round_n and (round_n == 2 or self.name == 'fundamentals'):
                facts['peer_reports'] = board.summary_for_specialist(self.name)
            instruction = ('Answer the targeted research question with evidence and uncertainty.' if consultation else
                'R0: independently collect and read primary evidence, observed data and coverage gaps.' if round_n == 0 else
                'R1: write your independent domain analysis. Fundamentals must read the statements, compare actual '
                'peer reports and make assumptions and falsification conditions explicit.' if round_n == 1 else
                'R2: answer the material Red Team objections on this exact sealed dossier and R1 thesis; '
                'state concessions or maintained judgement with evidence and preserve source references.')
            # E7 (04/10/2026, Opus 5.5): questo return veniva PRIMA del blocco vincoli dei
            # round settimanali: in Trade Idea i desk non vedevano feedback e veti del PM.
            # Testo dalla fotografia in sola lettura della run (mai il DB); buco dichiarato.
            from bellomberg.agents.trade_idea import pm_constraints_text
            vincoli_ti = pm_constraints_text(board)[0]
            print("  [" + self.name + "] vincoli PM R" + str(round_n) + " (trade idea): "
                  + riga_log_vincoli_pm(vincoli_ti))
            return ('TRADE IDEA: research on the exact accepted candidate only. PM view is a thesis to test. '
                'Use [src: tool] with dates, units and currency. Missing documents/consensus are explicit gaps; '
                'no workbook or mandatory AI fair value. Mandate, prices, risk and sizing controls remain binding.\n'
                + vincoli_ti
                + json.dumps(facts, ensure_ascii=False, default=str) + '\n' + instruction)
        if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea":
            target = self.blackboard.target_ticker
            view = self.blackboard.pm_view or "(nessuna view fornita)"
            history = self.blackboard.candidate_history or "(storico candidato non disponibile: dichiarare il limite)"
            decision_context = self.blackboard.data.get("_decision_context")
            book_context = self.blackboard.data.get("_portfolio_context")
            peers = self.blackboard.summary_for_specialist(self.name) if round_n else {}
            other = (_blocco_round_precedente(self.blackboard, self.name, round_n, peers)
                     if round_n == 2 else _blocco_blackboard(peers) if round_n else "")
            red = self.blackboard.get_latest("red_team") if round_n == 2 else None
            from bellomberg.valuation.sector_analysis import valuation_results_block
            from bellomberg.agents.trade_idea import candidate_model_context
            own_recon = self.blackboard.read(self.name, 0) if round_n else None
            addressed = [row for row in self.blackboard.data.get("_objections", [])
                         if row["objection"]["desk"] == self.name]
            building_owner = (self.name == "fundamentals" and round_n == 1
                              and getattr(self.blackboard, "model_phase", None) == "building")
            model_heading = ("Candidate model draft (not_created until get_valuation compiles it):\n"
                             if building_owner else "Exact read-only common model:\n")
            round_instruction = (
                "R1 targeted consultation: answer only the specific question using the supplied "
                "draft assumptions and retrieved evidence, in the requested format. "
                "This consultation is exempt from the generic complete R1 domain report; "
                "retain evidence gaps, dissent and all existing constraints. "
                if round_n == 1 and (getattr(self, "_task_context", None) or {}).get("consultation") is True else
                "R1 Fundamentals: costruisci le assunzioni economiche esplicite. Usa "
                "get_candidate_model_inputs, poi submit_candidate_model_plan per salvare i driver. "
                "Salva via via i gruppi di driver appena sono pronti, prima di altre letture: "
                "non aspettare il report finale per rendere persistenti le assunzioni. "
                "Prima leggi contract_section=method/evidence dello stesso tool: sono i formati "
                "esatti del compilatore, non solo i nomi dei driver. Per FCFF leggi anche "
                "accounting/shares/opening_nwc. Usa contract_section=draft_validation per "
                "controllare gratis la bozza e correggere gli errori prima di generare Excel. "
                "La base dichiarata espone gli input gia' presenti nelle fonti qualificate, "
                "con provenienza e kind originali: non sono assunzioni approvate. "
                "read_candidate_source legge il testo esatto dei documenti gia' ammessi: "
                "usa document_id dal catalogo e query letterale per trovare le prove. "
                "Non acquisire di nuovo un documento gia' disponibile. "
                "plan.model e' una mappa nome-driver -> envelope; plan.scenarios contiene "
                "bear/base/bull, ciascuno come mappa di driver; plan.scenario_rationale contiene "
                "bear/base/bull come testi motivati. Per exposure usa solo model, scenarios={} "
                "e analysis_rationale come testo motivato. "
                "Gli scopi sono model/bear/base/bull (solo model per exposure), mai il ticker. "
                "Raggruppa i quattro manifest in UNA risposta con piu' tool call; usa solo "
                "i nomi driver effettivi del manifest. Non ripetere una lettura gia' completa. "
                "Passa plan come OGGETTO, senza serializzarlo in una stringa. Salva "
                "gruppi di driver in piu' submit parziali: si accumulano nel piano finale. "
                "Usa offset per una contract_section paginata oppure un singolo driver disponibile; "
                "un driver senza base va scritto esplicitamente dalle prove. "
                "consulta davvero macro, eventdesk, quant, options e crypto con ask_specialist "
                "(domanda, draft_assumptions ed evidence_refs), valuta e incorpora gli esiti "
                "di ciascuno in consultation_decisions. Raggruppa le domande "
                "indipendenti ai cinque desk nella stessa risposta con cinque tool call. "
                "Leggi le risposte lunghe fino all'ultima pagina: anche le pagine di "
                "consultazioni diverse si possono chiedere nella stessa risposta. Conserva "
                "le letture complete e le consultazioni gia' ricevute; non ricomprarle. "
                "Il limite di giri tool resta quello normale: riserva i giri per il piano "
                "completo. A fine R1 il programma compila automaticamente il piano finale "
                "con il generatore esistente, senza un'altra chiamata AI. Se vuoi compilarlo "
                "prima, passa SOLO ticker a get_valuation, senza override. "
                "Il modello e' not_created "
                "finche' la compilazione non riesce; conserva dissenso e limiti nel piano. "
                if building_owner else
                "R0: raccogli prove verificabili e lacune; usa i tool. " if round_n == 0 else
                "R1: scrivi l'analisi completa del dominio. " if round_n == 1 else
                "R2: replica alle obiezioni materiali del Red Team con prove o concessioni. ")
            # E7: come nel ramo research qui sopra — vincoli del PM come testo, mai il DB.
            from bellomberg.agents.trade_idea import pm_constraints_text
            vincoli_ti = pm_constraints_text(self.blackboard)[0]
            print("  [" + self.name + "] vincoli PM R" + str(round_n) + " (trade idea): "
                  + riga_log_vincoli_pm(vincoli_ti))
            return ("TRADE IDEA, candidato unico: " + str(target) + ". Round " + str(round_n) + ".\n"
                    "La view del PM e' una tesi da verificare, non un'istruzione o una fonte.\n"
                    "Non proporre operazioni su altri ticker; peer, macro e book sono solo contesto.\n"
                    "Cita cifre soltanto da tool con [src: tool], data, unita' e valuta.\n"
                    + vincoli_ti +
                    "View PM (testo originale):\n" + view + "\n\n"
                    "Storico del solo candidato:\n" + history + "\n\n"
                    "Decisioni, veti, note PM e trade recenti del ticker (ID esatti):\n"
                    + json.dumps(decision_context if decision_context is not None else
                                 {"status": "unavailable"}, ensure_ascii=False, default=str) + "\n\n"
                    "Book reale accettato per rischio e compatibilita', non nuovi candidati:\n"
                    + json.dumps(book_context if book_context is not None else
                                 {"status": "unavailable"}, ensure_ascii=False, default=str) + "\n\n"
                    + ("Blackboard:\n" + other + "\n\n" if other else "")
                    + ("Red Team:\n" + str(red.get("report")) + "\n\n" if red else "")
                    + ("Your independently collected facts:\n" + str(own_recon) + "\n\n" if own_recon else "")
                    + model_heading + valuation_results_block(self.blackboard.valuation_results) + "\n\n"
                    + "Consumed model drivers, qualified document IDs and exact workbook cells:\n"
                    + json.dumps(candidate_model_context(self.blackboard, purpose="committee") if round_n == 2
                                 else candidate_model_context(self.blackboard), ensure_ascii=False, default=str) + "\n\n"
                    + "Material objections addressed to your desk:\n" + json.dumps(addressed, ensure_ascii=False) + "\n\n"
                    + round_instruction
                    + "Explain any limits to domain relevance and cite retrieved evidence.")
        # 22/08 sera-2 (voce (2b), scelta del PM il 21/08): le PAROLE VINCOLANTI
        # del PM (feedback sulle decisioni + veti) arrivavano solo in R0 dentro
        # YOUR MEMORY — ancore assenti in 10 prompt su 17 (6 R1 + 3 R2 + red
        # team; il 17 conta anche 6 R0 e il Capo, che hanno i loro canali).
        # ⚠️ La voce storica di MASTER dice «11 su 17»: RICONTATO dalla review
        # 22/08 sera-2 sul roster vero, sono 10 — discrepanza segnalata al PM.
        # Da qui entrano anche in R1/R2, PRIMA della blackboard. Nessun cap
        # totale sul preambolo (verificato 22/08): niente tagli spostati.
        vincoli = blocco_vincoli_pm(self.blackboard.memory_db) if round_n in (1, 2) else ""
        if round_n in (1, 2):
            # 27/08 (run V9): una riga per desk-round, cosi' a run viva si vede
            # nel log CHE il blocco e' entrato e in quale stato (punto 3 della
            # checklist: prima era invisibile).
            print("  [" + self.name + "] vincoli PM R" + str(round_n) + ": "
                  + riga_log_vincoli_pm(vincoli))
        if round_n == 0:
            memory_block = self._build_memory_block()
            preamble = (
                "ROUND 0 - RECONNAISSANCE.\n"
                "First round of this week's run. Below is YOUR MEMORY from previous weeks.\n"
                "Use it to MAINTAIN CONTINUITY: if you had a thesis last week, evolve it.\n"
                "If the PM pushed back on something, address it explicitly.\n"
                "Your task this round: call YOUR tools to gather raw data + output concise RAW_DATA report.\n"
                + memory_block
            )
        elif round_n == 1:
            others = self.blackboard.summary_for_specialist(self.name)
            # Ripipeline 15/07: per chi NON replica in R2 questo e' il round FINALE —
            # dirglielo esplicitamente, o i deliverable "da Round 2" del suo workflow
            # vengono rimandati a un round che non arrivera' mai (review 15/07).
            _r2 = getattr(self.blackboard, "r2_specialists", None)
            _is_final_round = (_r2 is not None and self.name not in _r2)
            _header = (
                "ROUND 1 - YOUR FINAL ANALYSIS (your desk does NOT run Round 2 this run).\n"
                "Deliver your COMPLETE final report now, INCLUDING every deliverable your\n"
                "workflow lists under 'Round 2'. Defer NOTHING to a later round.\n"
                if _is_final_round else
                "ROUND 1 - FIRST DRAFT WITH ANALYSIS.\n")
            preamble = (
                _header
                + vincoli
                + "Other specialists' latest reports are on the blackboard below (Round 0 raw data;\n"
                "specialists earlier in the pipeline may already show Round 1 drafts - use them).\n"
                "Produce your full analytical report (800-1500 words).\n"
                "Cross-reference others where relevant. Use ask_specialist for targeted questions.\n\n"
                + _blocco_round_precedente(self.blackboard, self.name, round_n, others)
            )
        elif round_n == 2:
            others = self.blackboard.summary_for_specialist(self.name)
            # replica al red team (#181 debate): solo se la critica esiste davvero —
            # e «esiste» lo decide il classificatore condiviso (review 25/08, voce
            # B): su segnaposto/rifiuto/[ERROR] questa cornice diceva «A RED TEAM
            # attacked» e ordinava di REPLICARE a obiezioni mai scritte; le
            # repliche facevano poi dichiarare al Capo (via la frase condizionale
            # della voce A) un contraddittorio mai avvenuto. Se l'import fallisce
            # in-run la chiave non puo' esistere (la scrive red_team stesso):
            # eredita' possibile solo nei rescue, comportamento vecchio.
            rt_line = ""
            if "red_team" in others:
                try:
                    from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
                    _cnu = motivo_critica_non_utilizzabile(
                        (others.get("red_team") or {}).get("report"))
                except Exception:
                    _cnu = None
                if _cnu is None:
                    rt_line = (
                        "A RED TEAM attacked the Round 1 theses (blackboard key 'red_team').\n"
                        "REPLY to the objections that touch YOUR domain: concede and adjust,\n"
                        "or rebut with data from your tools. Do NOT ignore them.\n")
                else:
                    rt_line = (
                        "The red team ran WITHOUT producing a usable critique: the "
                        "blackboard holds a declared placeholder, not objections "
                        "(motivo: " + _cnu + ").\n"
                        "There is NOTHING to reply to - do not invent or answer "
                        "objections that are not written, and do not infer the "
                        "theses were validated.\n")
            preamble = (
                "ROUND 2 - CROSS-REVIEW AND REFINEMENT.\n"
                "Others completed Round 1 drafts. REFINE your output: resolve contradictions,\n"
                "add cross-pollination insights, sharpen views.\n"
                + rt_line
                + vincoli +
                ("\nBLACKBOARD:\n" + _blocco_blackboard(others) if is_research_mode(self.blackboard) else
                 _blocco_round_precedente(self.blackboard, self.name, round_n, others))
            )
        else:
            preamble = "Round " + str(round_n)
        filing_context = self.blackboard.data.get("_filing_context")
        if filing_context:
            preamble += "\n\n" + filing_context
        preparation_state = self.blackboard.data.get("_valuation_preparation")
        if preparation_state is not None and not is_research_mode(self.blackboard):
            from bellomberg.valuation.preparation_runtime import preparation_status_text
            preamble += "\n\n" + preparation_status_text(preparation_state, language=self.blackboard.language)
        if round_n == 2 and is_research_mode(self.blackboard):
            preamble += '\n\nSEALED COMPANY RESEARCH:\n' + json.dumps(
                research_context(self.blackboard), ensure_ascii=False, default=str)
        elif round_n == 2 and self.blackboard.valuation_results:
            from bellomberg.valuation.sector_analysis import valuation_results_block
            preamble += "\n\n" + valuation_results_block(self.blackboard.valuation_results)
        # SCORE DETERMINISTICO (#186): ancora numerica calcolata in codice, l'LLM narra.
        # Cache per-run nel blackboard: lo scorer (anche pesante, es. DCF) gira UNA volta.
        if round_n in (0, 1):
            errori = self.blackboard.data.setdefault("_score_errors", {})
            cache = self.blackboard.data.setdefault("_score_cache", {})
            try:
                if self.name in cache:
                    sc = cache[self.name]
                else:
                    sc = self.compute_score()
                    cache[self.name] = sc
                if sc:
                    from bellomberg.agents.specialist_scores import format_score_block
                    preamble = preamble + "\n\n" + format_score_block(sc)
            except Exception as e:
                cache[self.name] = None
                errori[self.name] = _dichiara_fallback("score[" + self.name + "]", e)
            if self.name in errori:
                preamble += ("\n\n[SCORE n.d.] " + errori[self.name]
                             + ". Dichiara la misura mancante; non ricostruirla a memoria.")
        return preamble + "\n\nGo. Use tools. Then write your report."

    @scoped_language
    def _lavoro_corrente(self):
        """Chiave del lavoro per la contabilita' dei tentativi (R-2 F5): la consultazione col
        suo id, altrimenti il tipo di compito; None = il lavoro unico del round."""
        ctx = getattr(self, "_task_context", None)
        if not isinstance(ctx, dict):
            return None
        if ctx.get("consultation") is True:
            return "consultazione:" + str(ctx.get("id") or ctx.get("consultation_id") or "?")
        return str(ctx["kind"]) if ctx.get("kind") else None

    def run(self, round_n, *, task_context=None, publish_report=True):
        consultation = isinstance(task_context, dict) and task_context.get("consultation") is True
        trade_idea = getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
        self.run_result_status = "failed"
        if consultation:
            self.consultation_result_status = "failed"
            self.consultation_terminal_response = None
        if task_context is not None and not isinstance(task_context, dict):
            raise ValueError("task_context must be an explicit object")
        context = json.loads(json.dumps(task_context, allow_nan=False)) if task_context is not None else None
        previous_context = getattr(self, "_task_context", None)
        self._task_context = context
        try:
            with request_scope(getattr(self.blackboard, "request_journal", None),
                    phase=("consultation" if consultation else "specialist"),
                    agent=self.name, round_n=round_n):
                return self._run_loop(round_n, task_context=context, publish_report=publish_report)
        except Exception as exc:
            self.run_result_status = "failed"
            _record_run_failure(self.blackboard, exc, self.name, round_n)
            if consultation:
                self.consultation_result_status = "failed"
                self.consultation_terminal_response = None
            if isinstance(exc, ConfigurazioneLLMMancante):
                message = "[ERROR " + self.name + " round " + str(round_n) + "]: " + str(exc)
                if publish_report:
                    self.blackboard.write(self.name, round_n, message)
                    self.blackboard.mark_specialist_error(self.name, str(exc))
                return message
            raise
        finally:
            self._task_context = previous_context

    def _is_trade_idea_model_author(self, round_n, task_context=None):
        return (self.name == "fundamentals" and round_n == 1
                and not is_research_mode(self.blackboard)
                and getattr(self.blackboard, "run_scope", None) == "trade_idea"
                and getattr(self.blackboard, "model_phase", None) == "building"
                and (task_context or {}).get("consultation") is not True)

    def _max_tokens_for_round(self, round_n, task_context=None):
        """Approved output space for every desk; explicit legacy R2 contracts stay pinned."""
        if (is_research_mode(self.blackboard) and getattr(self.blackboard, 'run_scope', None) == 'trade_idea'
                and self.name == 'fundamentals' and round_n == 1
                and (task_context or {}).get('consultation') is not True):
            return MAX_TOKENS_TRADE_IDEA_AUTHOR
        if (getattr(self.blackboard, "run_scope", "weekly") == "weekly"
                and self.name == "fundamentals" and round_n in (1, 2)):
            return MAX_TOKENS_FUNDAMENTALS_ANALYSIS
        flag = "_trade_idea_r2_completion_limits"
        if not hasattr(self.blackboard, flag):
            return (MAX_TOKENS_TRADE_IDEA_AUTHOR if self._is_trade_idea_model_author(round_n, task_context)
                    else MAX_TOKENS_SPECIALIST)
        limits = getattr(self.blackboard, flag)
        if (not isinstance(limits, dict)
                or set(limits) != {"scope", "round", "model_ref", "max_tokens_by_desk"}
                or limits.get("scope") != "trade_idea_r2_final_completion_v1"
                or type(limits.get("round")) is not int or limits["round"] != 2
                or getattr(self.blackboard, "run_scope", None) != "trade_idea" or round_n != 2
                or limits.get("max_tokens_by_desk") != {"macro": 65536, "crypto": 65536}
                or any(type(value) is not int for value in limits["max_tokens_by_desk"].values())):
            raise ValueError("Invalid private Trade Idea R2 completion token scope")
        from bellomberg.agents.trade_idea import _verified_candidate_valuations
        verified = _verified_candidate_valuations(self.blackboard)
        fields = ("snapshot_id", "generation_id", "workbook_sha256")
        if (len(verified) != 1 or not isinstance(limits["model_ref"], dict)
                or set(limits["model_ref"]) != set(fields)
                or limits["model_ref"] != {key: verified[0][key] for key in fields}):
            raise ValueError("Private R2 completion requires the current exact verified workbook")
        if self.name not in limits["max_tokens_by_desk"]:
            return MAX_TOKENS_SPECIALIST
        if not isinstance(task_context, dict) or task_context.get("purpose") != "final_completion":
            raise ValueError("Private R2 token scope requires an explicit final-completion task")
        return limits["max_tokens_by_desk"][self.name]

    def _max_tool_iterations_for_round(self, round_n, task_context=None):
        """Extra tool turns only for the native Trade Idea model author, never consultations."""
        if (is_research_mode(self.blackboard) and getattr(self.blackboard, 'run_scope', None) == 'trade_idea'
                and self.name == 'fundamentals' and round_n == 1
                and (task_context or {}).get('consultation') is not True):
            return MAX_TOOL_ITERS_TRADE_IDEA_AUTHOR
        if self._is_trade_idea_model_author(round_n, task_context):
            return MAX_TOOL_ITERS_TRADE_IDEA_AUTHOR
        return MAX_TOOL_ITERS_SPECIALIST

    def _request_system(self, system_round):
        style = SPECIALIST_STYLE_RULES
        if is_research_mode(self.blackboard):
            style = style.replace('get_valuation (il TUO DCF a sub-settori buy-side, usalo per il fair value invece di stimare a occhio), ', '')
        system = system_round + "\n\n" + prompt_for_language(style)
        if self._arsenale_degradato:
            system += ("\n\n[!! ARSENALE DEGRADATO - DICHIARALO NEL REPORT] "
                       + self._arsenale_degradato +
                       " -> stai lavorando con MENO TOOL del previsto: apri il "
                       "report con questa riga, elenca cosa NON hai potuto "
                       "verificare e NON colmare i buchi a memoria.")
        return system

    def _prepare_report_completion(self, fields, key, saved):
        """Derive one explicitly authorized final response from a verified paid turn."""
        descriptor = getattr(self.blackboard, "specialist_response_recovery", None)
        if not descriptor or descriptor.get("checkpoint_key") != key:
            historical = (saved or {}).get("response_recovery")
            if (not historical or saved.get("status") != "complete"
                    or historical.get("checkpoint_key") != key):
                return fields, saved
            gate = getattr(self.blackboard, "budget_gate", None)
            if gate is None or not gate.store.accepted_response_recovery(self.blackboard.run_id, historical):
                raise ValueError("completed response recovery no longer has its accepted historical grant")
            descriptor = historical
        if descriptor.get("kind") == "failed_model_authoring":
            return self._prepare_failed_model_completion(fields, key, saved, descriptor)
        if (saved is None or fields["run_scope"] != "trade_idea"
                or fields["round"] not in (0, 1) or fields["task_context"] is not None
                or fields["model_phase"] != "research" or fields["review_model_ref"] is not None
                or descriptor.get("desk") != self.name or descriptor.get("round_n") != fields["round"]
                or fields["max_tokens"] != descriptor.get("replacement_max_tokens")):
            raise ValueError("report completion scope differs from its authorization")
        history = self.blackboard.data.get("_specialist_response_recovery_history", [])
        if not isinstance(history, list):
            raise ValueError("report completion history is malformed")
        previous = [item for item in history if item.get("request_id") == descriptor["request_id"]]
        derived = saved.get("response_recovery") is not None
        if derived:
            if (saved["response_recovery"] != descriptor or len(previous) != 1
                    or previous[0].get("descriptor") != descriptor):
                raise ValueError("report completion authorization or history differs")
            original = deepcopy(previous[0]["checkpoint"])
            seal = original.pop("sha256", None)
        else:
            if previous:
                raise ValueError("report completion history exists without its derived checkpoint")
            original = deepcopy(saved)
            seal = _checkpoint_digest(original)
        if (seal != descriptor["specialist_checkpoint_sha256"] or seal != _checkpoint_digest(original)
                or original.get("contract") != descriptor["original_contract"]
                or original.get("status") != "truncated" or original.get("forced_report") is not True
                or original.get("iteration") != fields["max_tool_iters"]
                or original.get("pending_tools") or original.get("inflight_tools")
                or original.get("usage_unknown") is not False
                or original.get("thinking") != {"type": "effort", "effort": "max"}
                or original.get("thinking_prima") is not None):
            raise ValueError("original report completion evidence differs")
        old_cap, _ = _checkpoint_output_contract(fields, original)
        if (old_cap >= fields["max_tokens"] or old_cap not in (16000, 64000, 65536)
                or descriptor.get("original_max_tokens") not in (None, old_cap)):
            raise ValueError("report completion output contract differs")
        self.blackboard.budget_gate.validate_response_recovery(descriptor, {
            "model": fields["model"], "max_tokens": old_cap, "thinking": original["thinking"],
            "system": self._request_system(fields["system"]), "tools": fields["tools"],
            "messages": original["messages"], "tool_choice": {"type": "none"},
        }, role="specialist:" + self.name)
        messages = deepcopy(original["messages"])
        nudge = ("[AUTHORIZED REPORT COMPLETION] The previous final response reached its output limit. "
                 "Complete the report now using only the evidence already present in this conversation. "
                 "Tools remain disabled. Keep unverifiable facts explicit as unavailable; do not invent data.")
        if isinstance(messages[-1]["content"], list):
            messages[-1]["content"].append({"type": "text", "text": nudge})
        else:
            messages[-1]["content"] += "\n\n" + nudge
        fields = {**fields, "response_recovery": deepcopy(descriptor)}
        if derived:
            # Check the derivation as well as the historical proof. A valid
            # old receipt cannot authorize substituted context on a second resume.
            final_iteration = fields["max_tool_iters"]
            at_final = saved.get("iteration") == final_iteration
            if (saved.get("response_recovery_call_offset") != 1
                    or saved.get("iteration") not in (final_iteration - 1, final_iteration)
                    or any(saved.get(name) != original.get(name) for name in
                        ("version", "initial_context", "forced_report", "tool_calls", "nudge_collasso",
                         "retry_529", "retry_vuoto", "thinking", "thinking_prima", "pending_tools", "inflight_tools"))):
                raise ValueError("derived report completion state differs")
            if at_final:
                if isinstance(messages[-1]["content"], list):
                    messages[-1]["content"].append({"type": "text", "text": _forced_report_nudge(final_iteration)})
                else:
                    messages[-1]["content"] += "\n\n" + _forced_report_nudge(final_iteration)
                if saved.get("status") not in ("complete", "truncated", "failed"):
                    raise ValueError("derived report terminal status requires receipt review")
                usage = saved.get("usage") or {}
                ids, original_ids = usage.get("request_ids"), original["usage"].get("request_ids")
                if (not isinstance(ids, list) or len(ids) != len(original_ids) + 1
                        or ids[:-1] != original_ids or saved.get("usage_unknown") is not False):
                    raise ValueError("derived report completion request accounting differs")
                self.blackboard.budget_gate.store.validate_specialist_usage(
                    self.blackboard.run_id, self.name, usage)
            elif (saved.get("status") != "ready" or saved.get("usage") != original.get("usage")
                    or saved.get("usage_unknown") != original.get("usage_unknown")
                    or saved.get("report") is not None):
                raise ValueError("derived report completion usage or boundary differs")
            if saved.get("messages") != messages:
                raise ValueError("derived report completion messages differ")
            return fields, saved
        original_sealed = {**deepcopy(original), "sha256": seal}
        replacement = {**deepcopy(original), "contract": _checkpoint_digest(fields),
            "max_tokens": fields["max_tokens"], "messages": messages,
            "iteration": fields["max_tool_iters"] - 1, "status": "ready", "report": None,
            "response_recovery": deepcopy(descriptor), "response_recovery_call_offset": 1}
        self.blackboard.data.setdefault("_specialist_response_recovery_history", []).append({
            "request_id": descriptor["request_id"], "descriptor": deepcopy(descriptor),
            "checkpoint": original_sealed})
        return fields, replacement

    def _prepare_failed_model_completion(self, fields, key, saved, descriptor):
        """Retry the selected error once, retaining the native author turn count."""
        if (saved is None or fields["run_scope"] != "trade_idea" or self.name != "fundamentals"
                or fields["round"] != 1 or fields["model_phase"] != "building"
                or fields["review_model_ref"] is not None
                or fields["task_context"] != descriptor.get("task_context")
                or (fields["task_context"] or {}).get("kind") != "model_authoring_completion"
                or fields["max_tokens"] != 128000 or fields["max_tool_iters"] != 30
                or descriptor.get("mode") != "model_authoring_error_retry"
                or descriptor.get("original_max_tokens") != fields["max_tokens"]
                or descriptor.get("replacement_max_tokens") != fields["max_tokens"]):
            raise ValueError("failed model recovery scope differs from its authorization")
        history = self.blackboard.data.get("_specialist_response_recovery_history", [])
        if not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
            raise ValueError("failed model recovery history is malformed")
        previous = [row for row in history if row.get("request_id") == descriptor["request_id"]]
        derived = saved.get("response_recovery") is not None
        if derived:
            if (saved["response_recovery"] != descriptor or len(previous) != 1
                    or previous[0].get("descriptor") != descriptor):
                raise ValueError("failed model recovery authorization or history differs")
            original = deepcopy(previous[0]["checkpoint"])
            seal = original.pop("sha256", None)
        else:
            if previous:
                raise ValueError("failed model recovery history exists without its derived checkpoint")
            original = deepcopy(saved)
            seal = _checkpoint_digest(original)
        iteration = descriptor.get("original_iteration")
        if (seal != descriptor["specialist_checkpoint_sha256"] or seal != _checkpoint_digest(original)
                or original.get("contract") != descriptor["original_contract"]
                or original.get("status") != "failed" or original.get("forced_report") is not False
                or type(iteration) is not int or not 1 <= iteration < fields["max_tool_iters"]
                or original.get("iteration") != iteration or original.get("pending_tools")
                or original.get("inflight_tools") or original.get("usage_unknown") is not False
                or original.get("thinking") != {"type": "effort", "effort": "max"}
                or original.get("thinking_prima") is not None or original.get("response_recovery") is not None):
            raise ValueError("original failed model evidence differs")
        old_cap, _ = _checkpoint_output_contract(fields, original)
        if old_cap != fields["max_tokens"]:
            raise ValueError("failed model recovery output contract differs")
        self.blackboard.budget_gate.validate_response_recovery(descriptor, {
            "model": fields["model"], "max_tokens": old_cap, "thinking": original["thinking"],
            "system": self._request_system(fields["system"]), "tools": fields["tools"],
            "messages": original["messages"],
        }, role="specialist:" + self.name)
        if derived:
            nudge = previous[0].get("recovery_nudge")
            if (not isinstance(nudge, str) or not nudge
                    or previous[0].get("recovery_nudge_sha256") != _checkpoint_digest(nudge)):
                raise ValueError("failed model recovery instruction seal differs")
        else:
            nudge = ("[EXPLICIT FAILED MODEL RESPONSE RECOVERY: " + descriptor["request_id"] + "] "
                "The selected provider response ended with error and produced no executable tool or report. "
                "Its original receipt and token accounting remain preserved. Continue the existing saved model "
                "construction, preserving all author decisions, evidence and consultation outcomes. "
                "The turn counter, model, effort and output limit are unchanged: next turn is "
                + str(iteration + 1) + " of " + str(fields["max_tool_iters"]) + ". "
                "Only the existing local author tools are available; do not repeat research or consultations. "
                "Save supported inputs and compile through the normal financial gates; do not invent missing data.")
            evidence_hook = getattr(self.blackboard, "model_authoring_recovery_evidence", None)
            if callable(evidence_hook):
                evidence = _checkpoint_json(evidence_hook(deepcopy(descriptor)))
                if evidence is not None:
                    nudge += "\n\nVERIFIED RECOVERY EVIDENCE (informational; author decisions remain required):\n" + json.dumps(
                        evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)
        messages = deepcopy(original["messages"])
        if isinstance(messages[-1]["content"], list):
            messages[-1]["content"].append({"type": "text", "text": nudge})
        else:
            messages[-1]["content"] += "\n\n" + nudge
        fields = {**fields, "response_recovery": deepcopy(descriptor)}
        if derived:
            saved_iteration = saved.get("iteration")
            usage = saved.get("usage") or {}
            ids, old_ids = usage.get("request_ids"), original["usage"]["request_ids"]
            if (type(saved_iteration) is not int or not iteration <= saved_iteration <= fields["max_tool_iters"]
                    or saved.get("response_recovery_call_offset") != 0
                    or saved.get("usage_unknown") is not False
                    or not isinstance(ids, list) or ids[:len(old_ids)] != old_ids
                    or len(ids) != len(old_ids) + saved_iteration - iteration
                    or any(saved.get(name) != original.get(name) for name in
                        ("version", "initial_context", "nudge_collasso", "retry_529", "retry_vuoto",
                         "thinking", "thinking_prima", "author_progress_version"))):
                raise ValueError("derived failed model counter or accounting differs")
            proofs = self.blackboard.budget_gate.store.specialist_response_receipts(
                self.blackboard.run_id, self.name, usage)
            current_messages = saved.get("messages")
            if not isinstance(current_messages, list):
                raise ValueError("derived failed model conversation is missing")
            # The entire accepted transcript and pinned instruction remain an
            # exact prefix. Only native paid tool turns may extend it.
            expected_prefix = deepcopy(messages)
            if saved_iteration == fields["max_tool_iters"] and len(current_messages) == len(messages):
                if isinstance(expected_prefix[-1]["content"], list):
                    expected_prefix[-1]["content"].append({"type": "text", "text": _forced_report_nudge(saved_iteration)})
                else:
                    expected_prefix[-1]["content"] += "\n\n" + _forced_report_nudge(saved_iteration)
            if current_messages[:len(messages)] != expected_prefix:
                raise ValueError("derived failed model original conversation differs")
            added = proofs[len(old_ids):]
            tool_responses = [proof["response"] for proof in added if proof["response"].get("stop_reason") == "tool_use"]
            tail = current_messages[len(messages):]
            if (len(tail) != 2 * len(tool_responses)
                    or any(tail[2 * pos] != {"role": "assistant", "content": response["content"]}
                        or tail[2 * pos + 1].get("role") != "user" for pos, response in enumerate(tool_responses))):
                raise ValueError("derived failed model paid tool transcript differs")
            if saved_iteration == iteration and (saved.get("status") != "ready"
                    or usage != original["usage"] or saved.get("report") is not None
                    or saved.get("tool_calls") != original.get("tool_calls")):
                raise ValueError("derived failed model initial boundary differs")
            if saved.get("status") in ("complete", "truncated", "failed"):
                if not added or added[-1]["response"].get("stop_reason") == "tool_use":
                    raise ValueError("derived failed model terminal receipt is missing")
                terminal = added[-1]["response"]
                visible = "\n".join(block["text"] for block in terminal.get("content", ())
                    if block.get("type") == "text" and block.get("text"))
                if saved.get("status") == "complete":
                    expected_report = visible
                    if saved.get("forced_report") is True:
                        expected_report = ("[REPORT FORZATO AL LIMITE ITERAZIONI ("
                            + str(fields["max_tool_iters"])
                            + "): verifiche tool esaurite, buchi dichiarati nel testo]\n\n" + visible)
                    if (terminal.get("stop_reason") != "end_turn" or not visible
                            or saved.get("report") != expected_report
                            or saved.get("forced_report") is not (saved_iteration == fields["max_tool_iters"])):
                        raise ValueError("derived failed model report differs from its paid response")
                # The final paid wire attests every newly delivered tool result
                # and informational ledger, not just their assistant counterparts.
                self._attest_recovered_model_wire(fields, saved, current_messages, added[-1])
            elif added:
                # A ready boundary follows a paid tool response. Its request
                # attests the preceding transcript; verify the newest result
                # separately against the durable native tool receipts.
                self._attest_recovered_model_wire(fields, saved, current_messages[:-2], added[-1])
                self._attest_recovered_tool_results(tool_responses[-1], tail[-1])
            return fields, saved
        replacement = {**deepcopy(original), "contract": _checkpoint_digest(fields),
            "messages": messages, "status": "ready", "report": None,
            "response_recovery": deepcopy(descriptor), "response_recovery_call_offset": 0}
        self.blackboard.data.setdefault("_specialist_response_recovery_history", []).append({
            "request_id": descriptor["request_id"], "descriptor": deepcopy(descriptor),
            "checkpoint": {**deepcopy(original), "sha256": seal}, "recovery_nudge": nudge,
            "recovery_nudge_sha256": _checkpoint_digest(nudge)})
        return fields, replacement

    def _attest_recovered_model_wire(self, fields, saved, messages, proof):
        from decimal import Decimal
        from bellomberg.agents.trade_idea import _model_author_request_matches, _stable_provider_messages
        from bellomberg.agents.model_authoring_context import project_model_authoring_messages
        pricing = self.blackboard.budget_gate.catalog_snapshot["models"]["specialist"]["pricing"]
        request = {"model": fields["model"], "max_tokens": fields["max_tokens"],
            "thinking": saved["thinking"], "system": self._request_system(fields["system"]),
            "tools": fields["tools"], "messages": _stable_provider_messages(messages),
            "provider_max_price": {"prompt": float(Decimal(pricing["prompt"]) * 10**6),
                "completion": float(Decimal(pricing["completion"]) * 10**6), "request": 0.0}}
        if saved.get("forced_report"):
            request["tool_choice"] = {"type": "none"}
        if _model_author_request_matches(request, proof.get("request_sha256")):
            return
        projected, _ = project_model_authoring_messages(request["messages"])
        if not _model_author_request_matches({**request, "messages": projected}, proof.get("request_sha256")):
            raise ValueError("derived failed model paid request wire differs")

    def _attest_recovered_tool_results(self, response, message):
        from bellomberg.agents.chat_tools import TETTO_TOOL_RESULT
        from bellomberg.agents.model_authoring_context import PROGRESS_MARKER
        calls = [block for block in response["content"] if block.get("type") == "tool_use"]
        content = message.get("content")
        if not isinstance(content, list) or len(content) not in (len(calls), len(calls) + 1):
            raise ValueError("derived failed model tool results are malformed")
        for call, delivered in zip(calls, content):
            if delivered.get("type") != "tool_result" or delivered.get("tool_use_id") != call["id"]:
                raise ValueError("derived failed model tool result identity differs")
            valid = False
            for receipt in self.blackboard.tool_receipts:
                if (receipt.get("tool") != call["name"] or receipt.get("input") != call["input"]
                        or receipt.get("truncated") is not False):
                    continue
                output = receipt.get("output")
                if not isinstance(output, str):
                    continue
                result = json.loads(output)
                if call["name"] == "get_valuation" and isinstance(result, dict):
                    from bellomberg.valuation.sector_analysis import valuation_results_block
                    payload = result.get("data") if isinstance(result.get("data"), dict) else result
                    output = (valuation_results_block({str(call["input"].get("ticker") or "n.d."): payload})
                        + "\n\nDettaglio sotto, soggetto al tetto tool_result; "
                          "snapshot integrale nel sidecar se generato:\n" + output)
                if len(output) > TETTO_TOOL_RESULT:
                    output = output[:TETTO_TOOL_RESULT] + "...[truncated]"
                valid = valid or delivered.get("content") == output
            if not valid:
                raise ValueError("derived failed model tool result differs from its native receipt")
        if len(content) > len(calls):
            progress = content[-1]
            text = progress.get("text")
            if progress.get("type") != "text" or not isinstance(text, str) or not text.startswith(PROGRESS_MARKER):
                raise ValueError("derived failed model unexpected user instruction")
            ledger = json.loads(text[len(PROGRESS_MARKER):])
            if ledger.get("contract") != "model_authoring_progress/1" or ledger.get("kind") != "informational_not_approval":
                raise ValueError("derived failed model informational progress differs")

    def _run_loop(self, round_n, *, task_context=None, publish_report=True):
        reply_completion = (task_context or {}).get('kind') == 'research_objection_completion'
        if reply_completion:
            from bellomberg.core.research_analysis import research_reference
            from bellomberg.agents.trade_idea import _plan_digest
            descriptor = self.blackboard.data.get('_research_reply_completions', {}).get(self.name) or {}
            if (not is_research_mode(self.blackboard) or self.blackboard.run_scope != 'trade_idea'
                    or round_n != 2 or publish_report or descriptor.get('task') != task_context
                    or task_context.get('desk') != self.name
                    or task_context.get('research_ref') != research_reference(self.blackboard)
                    or task_context.get('origin_report_sha256') != _plan_digest(self.blackboard.read(self.name, 2))):
                raise ValueError('Research reply completion requires the original sealed desk report and exact task')
        review_model_ref = None
        if getattr(self.blackboard, "run_scope", None) == "trade_idea" and round_n == 2:
            current_model = self.blackboard.valuation_results.get(self.blackboard.target_ticker) or {}
            review_model_ref = {key: current_model.get(key) for key in
                                ("snapshot_id", "generation_id", "workbook_sha256")}
        checkpoint_key = (self.name + ":R" + str(round_n)
            + (":" + _checkpoint_digest(task_context) if task_context is not None else "")
            + (":model:" + _checkpoint_digest(review_model_ref) if review_model_ref is not None else ""))
        saved_checkpoint = deepcopy(getattr(self.blackboard, "specialist_checkpoints", {}).get(checkpoint_key))
        if saved_checkpoint is not None:
            digest = saved_checkpoint.pop("sha256", None)
            if digest != _checkpoint_digest(saved_checkpoint):
                raise ValueError("specialist checkpoint checksum differs")
        max_tool_iters = self._max_tool_iterations_for_round(round_n, task_context)
        max_tokens = self._max_tokens_for_round(round_n, task_context)
        print("\n[" + self.name.upper() + "] Round " + str(round_n) + " start")
        # Una fotografia per round; la prossima chiamata vede eventuali modifiche.
        from bellomberg.core.evidence_prompt_policy import select_template, diagnostic_block
        evidence_template = select_template(self.blackboard, self.name, self.system_prompt)
        from bellomberg.core.evidence_followup_policy import board_enabled as _followup_enabled, instructions as _followup_instructions, followup_block, no_acquisition_notice
        _evidence_followup = _followup_enabled(self.blackboard)
        evidence_template += _followup_instructions(self.blackboard)
        mandato = None
        from bellomberg.core import mandato_pm
        text_policy = mandato_pm.text_policy_for_board(self.blackboard)
        try:
            try:
                mandato = mandato_pm.carica()
                errore_mandato = ""
            except mandato_pm.MandatoMancante as e:
                mandato, errore_mandato = None, str(e)
            system_round = mandato_pm.compila_o_dichiara(evidence_template, mandato,
                **({"text_policy": text_policy} if text_policy is not None else {}))
            if "{MANDATO:" not in evidence_template:
                system_round += "\n\n" + (mandato_pm.blocco_prompt(mandato, **({"text_policy": text_policy} if text_policy is not None else {})) if mandato is not None
                                            else mandato_pm.riga_senza_mandato())
            if errore_mandato:
                system_round += "\n[MANDATO n.d.] " + errore_mandato
        except Exception as e:
            import re
            causa = _dichiara_fallback("mandato[" + self.name + "]", e)
            system_round = re.sub(r"\{MANDATO:[^}]+\}", "(mandato n.d.)", evidence_template)
            system_round += "\n[MANDATO n.d.] " + causa
        if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea":
            from bellomberg.core.trade_idea_contract import trade_idea_specialist_instructions
            system_round = (trade_idea_specialist_instructions(self.name,
                analysis_mode=self.blackboard.analysis_mode) if is_research_mode(self.blackboard)
                else trade_idea_specialist_instructions(self.name))
            system_round += "\nSELECTED CANDIDATE: " + str(self.blackboard.target_ticker)
            system_round += "\n" + (mandato_pm.blocco_prompt(mandato, **({"text_policy": text_policy} if text_policy is not None else {})) if mandato is not None else mandato_pm.riga_senza_mandato())
        system_round += ("\nBefore acquiring company data again, use read_company_dossier to reuse "
                         "the verified company evidence shared by this run. Preserve its source references; "
                         "declare partial or unavailable facts explicitly and never replace them silently.")
        if callable(getattr(self.blackboard, "company_source_session", None)):
            from bellomberg.agents.company_research_tools import RESEARCH_INSTRUCTIONS, RESEARCH_ONLY_INSTRUCTIONS
            system_round += RESEARCH_ONLY_INSTRUCTIONS if is_research_mode(self.blackboard) else RESEARCH_INSTRUCTIONS
            if (getattr(self.blackboard, "run_scope", "weekly") == "weekly"
                    and callable(getattr(self.blackboard, "valuation_preparer", None))):
                system_round += ("\nFor a new weekly model with automatic preparation enabled, acquire the official "
                    "statements first, then call get_valuation with only the ticker: the authorized preparer consumes "
                    "this run's verified dossier and builds sourced assumptions through the common compiler. "
                    "Read and critique the returned assumptions and scenario rationales. Do not bypass preparation "
                    "with a partial or invented method_records list; explicit complete records retain their separate contract.")
        # collaudo #44: durata per specialista (prima non esisteva alcun timer)
        _t0 = time.perf_counter()
        if publish_report:
            self.blackboard.mark_specialist_start(self.name, round_n)
        # Fase 3 "fatti volatili": current_facts (TTL 1h) e favorites (TTL 600s)
        # vivono QUI, nel primo messaggio user, CONGELATI per il round — non piu'
        # nel system cachato, dove un refresh a run in corso (>60 min) cambiava
        # il system a meta' conversazione e invalidava l'intera cache dell'agente.
        _ctx = (saved_checkpoint["initial_context"] if saved_checkpoint is not None
                else self._build_round_context(round_n))
        blocchi = []
        for nome in (() if saved_checkpoint is not None or reply_completion else
                ("current_facts_block",) if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
                else ("current_facts_block", "favorites_block", "pm_theses_block")):
            try:
                from bellomberg.core import current_facts
                blocchi.append(getattr(current_facts, nome)())
            except Exception as e:
                causa = _dichiara_fallback(nome + "[" + self.name + "]", e)
                blocchi.append("\n\n[CONTESTO n.d.] " + causa
                              + ". Dichiara il buco nel report.")
        if saved_checkpoint is None:
            _ctx = "".join(blocchi) + "\n\n" + _ctx
        if saved_checkpoint is None and self.name == 'quant' and round_n == 2:
            _ctx += diagnostic_block(self.blackboard)
        if saved_checkpoint is None and _evidence_followup:
            _ctx += followup_block(self.blackboard)
        if task_context is not None and saved_checkpoint is None:
            task_heading = (
                "\n\nTARGETED TASK: answer the explicit question below using the supplied draft and "
                "retrieved evidence. A consultation does not replace your official report and cannot "
                "ask another specialist or mutate the common model.\n"
                if task_context.get("consultation") is True else
                "\n\nEXPLICIT TASK CONTEXT: follow the task below within your current role and phase. "
                "All source and model authorization constraints remain in force.\n")
            _ctx += task_heading + json.dumps(task_context, ensure_ascii=False, allow_nan=False)
        # C4 16/07 (richiesta PM): pipeline RESEARCH SOLO a fundamentals e SOLO in R1/R2
        # (R0 e' gia' saturo: 10/10 iterazioni nella run #45 — aggiungere lavoro li'
        # significherebbe recon troncata, non solo costo).
        _frozen_notes = None
        if (getattr(self.blackboard, 'weekly_store', None) is not None
                and 'research_notes_policy' in self.blackboard.weekly_store.context.get('contract', {})):
            from bellomberg.core.current_facts import research_notes_for_board
            _frozen_notes = research_notes_for_board(self.blackboard)
        if (saved_checkpoint is None and self.name == "fundamentals" and round_n >= 1
                and getattr(self.blackboard, "run_scope", "weekly") != "trade_idea"):
            try:
                from bellomberg.core.current_facts import research_block
                # New runs use the accepted snapshot; historical rounds keep their legacy request.
                if _frozen_notes is not None:
                    _rb = research_block(notes_context=_frozen_notes)
                else:
                    _research = is_research_mode(self.blackboard)
                    _rb = research_block(sector_bundles=None if _research else self._sector_bundles,
                                         decision_links=None if _research else self._research_decision_links,
                                         legacy=True)
                if _rb:
                    _ctx = _rb.strip() + "\n\n" + _ctx
            except Exception as e:
                causa = _dichiara_fallback("research_block[fundamentals]", e)
                _ctx = "[CONTESTO n.d.] " + causa + "\n\n" + _ctx
        messages = [{"role": "user", "content": _ctx}]
        author_history = getattr(self, "_trade_idea_author_history", None)
        if author_history is not None:
            from bellomberg.agents.trade_idea import _plan_digest
            if (self.name != "fundamentals" or round_n != 1
                    or (task_context or {}).get("consultation") is True
                    or getattr(self.blackboard, "run_scope", None) != "trade_idea"
                    or getattr(self.blackboard, "model_phase", None) != "building"
                    or not isinstance(author_history, dict)
                    or author_history.get("genuine_history") is not True
                    or author_history.get("source_fingerprint") != self.blackboard.source_qualification.get("fingerprint")):
                raise ValueError("Historical author messages require the same qualified Fundamentals building scope")
            historical_messages = author_history.get("messages")
            if (not isinstance(historical_messages, list) or not historical_messages
                    or any(not isinstance(message, dict) or message.get("role") not in ("user", "assistant")
                           or not isinstance(message.get("content"), (str, list)) for message in historical_messages)
                    or historical_messages[0]["role"] != "user" or historical_messages[-1]["role"] != "user"
                    or _plan_digest(historical_messages) != author_history.get("messages_sha256")):
                raise ValueError("Historical author message seal or native SDK shape differs")
            if saved_checkpoint is None:
                messages = json.loads(json.dumps(historical_messages, ensure_ascii=False, allow_nan=False))
                if (task_context or {}).get("kind") == "model_authoring_completion":
                    # This phase attests the same sources and paid conversation.
                    # Preserve that whole history once; do not append a second
                    # copy of all peer reports and the accepted portfolio.
                    current_context = ("\n\nNATIVE MODEL COMPLETION. The full preceding research remains the "
                        "accepted historical context, with its original dates, PM view and constraints. "
                        "Use the recorded progress below for the current saved draft and missing work. "
                        "Complete supported driver groups and consultation outcomes, then compile the model; "
                        "do not repeat research or replace the model with prose. The original total budget and "
                        "all financial checks remain in force. Final prices, sizing and risk are verified "
                        "separately before Decisions. Task: " + json.dumps(task_context, ensure_ascii=False))
                else:
                    current_context = "\n\nNEW AUTHOR PHASE / CURRENT OPERATING CONTEXT. Earlier messages are authentic historical reads; their usage belongs to the prior child. Current source authorization and all current constraints follow:\n" + _ctx
                if isinstance(messages[-1]["content"], list):
                    messages[-1]["content"].append({"type": "text", "text": current_context})
                else:
                    messages[-1]["content"] += current_context
        tools_schema = self._build_tools_schema()
        original_tools_schema = deepcopy(tools_schema)
        model_completion = (task_context or {}).get("kind") == "model_authoring_completion"
        if model_completion:
            if (not self._is_trade_idea_model_author(round_n, task_context)
                    or not author_history or author_history.get("native_model_completion") is not True):
                raise ValueError("Model completion requires the native attested author history")
            tools_schema = [tool for tool in tools_schema if tool["name"] in MODEL_AUTHORING_LOCAL_TOOLS]
        consultation = (task_context or {}).get("consultation") is True
        if consultation:
            tools_schema = [tool for tool in tools_schema if tool["name"] not in {
                "ask_specialist", "review_candidate_model", "submit_candidate_model_plan",
                "respond_trade_idea_objection", "read_candidate_model_consultation",
                "add_guidance", "add_research_note", "acquire_company_source"}]

        contract_fields = {"version": 1, "desk": self.name, "round": round_n,
            "model": self._model_for_round(round_n), "system": system_round,
            "tools": tools_schema, "max_tokens": max_tokens, "max_tool_iters": max_tool_iters,
            "run_scope": getattr(self.blackboard, "run_scope", None), "task_context": task_context,
            "target_ticker": getattr(self.blackboard, "target_ticker", None),
            "source_fingerprint": (getattr(self.blackboard, "source_admission", None)
                or getattr(self.blackboard, "source_qualification", None) or {}).get("fingerprint"),
            "model_phase": getattr(self.blackboard, "model_phase", None),
            "review_model_ref": review_model_ref}
        if model_completion:
            original = deepcopy(author_history["origin"])
            seal = original.pop("sha256", None)
            if (seal != _checkpoint_digest(original)
                    or seal != task_context.get("origin_checkpoint_sha256")):
                raise ValueError("Model completion origin checkpoint differs")
            original_fields = {**contract_fields, "task_context": None, "tools": original_tools_schema}
            old_cap, _ = _checkpoint_output_contract(original_fields, original)
            from bellomberg.agents.trade_idea import _plan_digest, _model_author_request_matches
            pricing = self.blackboard.budget_gate.catalog_snapshot["models"]["specialist"]["pricing"]
            from decimal import Decimal
            old_messages = original["messages"]
            receipt = author_history["last_receipt"]
            old_request = {"model": original_fields["model"], "max_tokens": old_cap,
                "thinking": original["thinking"], "system": self._request_system(system_round),
                "tools": original_tools_schema, "messages": old_messages,
                "provider_max_price": {"prompt": float(Decimal(pricing["prompt"]) * 10**6),
                    "completion": float(Decimal(pricing["completion"]) * 10**6), "request": 0.0}}
            if original.get("forced_report"):
                old_request["tool_choice"] = {"type": "none"}
            if not _model_author_request_matches(old_request, receipt.get("request_sha256")):
                raise ValueError("Original model-authoring wire differs from the paid request")
            author_history = {**author_history, "system_sha256": _plan_digest(self._request_system(system_round))}
        contract_fields, saved_checkpoint = self._prepare_report_completion(
            contract_fields, checkpoint_key, saved_checkpoint)
        max_tokens, checkpoint_contract = _checkpoint_output_contract(contract_fields, saved_checkpoint)
        if saved_checkpoint is not None:
            if saved_checkpoint.get("status") == "complete":
                self.run_result_status = "complete"
                if consultation:
                    self.consultation_result_status = "complete"
                return saved_checkpoint["report"]
            if saved_checkpoint.get("status") in ("truncated", "failed"):
                raise RuntimeError("preserved specialist response is incomplete; explicit review required before a new request")
            messages = saved_checkpoint["messages"]

        owned_client = (getattr(self, "_owned_trade_idea_client", None)
                        or getattr(self, "_owned_weekly_client", None))
        if owned_client is not None:
            # New and historical stages use their effective output cap. Keep
            # connect/retry settings and externally injected clients unchanged.
            timeout = timeout_specialisti(max_tokens)
            http_timeout = owned_client._http.timeout
            owned_client._http.timeout = type(http_timeout)(timeout, connect=http_timeout.connect)
            owned_client.timeout = timeout

        iteration = 0
        final_text = None
        # Usage per specialista (collaudo #44): senza, il risparmio del caching
        # 200a/200a-bis e' verificabile solo dalla console web Anthropic.
        _usage = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0}
        # True se ALMENO una risposta del round non ha esposto usage: il totale
        # dell'agente e' allora una sottostima di entita' ignota -> status
        # "usage_unknown", mai "ok" (semantica dei buchi, PM 15/07).
        _usage_unknown = False

        # §9-bis n.5 (robustezza run, 21/07): True se il report e' uscito dal giro
        # forzato senza tool -> prefisso dichiarato nel testo (il Capo lo vede).
        _forced_report = False

        # Fix 1 §9-sextrigies: tool eseguiti nel round (0 = annuncio-senza-lavoro),
        # nudge anti-collasso gia' speso, chiamate extra dei retry 529 (contate
        # nell'usage: api_calls e' una misura, non il numero di iterazioni).
        _tool_calls_round = 0
        _nudge_collasso_dato = False
        _retry_529 = 0
        # Audit 11/09 (Fable 5.1, run 10/09 memo #53): UN ritentativo dichiarato quando la
        # call esce a max_tokens con ZERO char di testo (tutto il tetto speso in ragionamento,
        # 4 report persi in una run). La troncatura CON testo resta dichiarata e non ritenta.
        _retry_vuoto = 0
        _thinking = None  # risoluzione nel try: configurazione mancante dichiarata nel round
        # 12/09 (Fable 5.1, prerequisito 3 del mandato): il ragionamento spento dal
        # ritentativo si RIPRISTINA dopo quella call — prima restava spento per il resto
        # del round (recon 12/09 par. 2.3). None = nessun ripristino in sospeso.
        _thinking_prima = None

        if saved_checkpoint is not None:
            iteration = saved_checkpoint["iteration"]
            _usage = saved_checkpoint["usage"]
            _usage_unknown = saved_checkpoint["usage_unknown"]
            _forced_report = saved_checkpoint["forced_report"]
            _tool_calls_round = saved_checkpoint["tool_calls"]
            _nudge_collasso_dato = saved_checkpoint["nudge_collasso"]
            _retry_529, _retry_vuoto = saved_checkpoint["retry_529"], saved_checkpoint["retry_vuoto"]
            _thinking, _thinking_prima = saved_checkpoint["thinking"], saved_checkpoint["thinking_prima"]
        _pending_tools = deepcopy((saved_checkpoint or {}).get("pending_tools", {}))
        _inflight_tools = deepcopy((saved_checkpoint or {}).get("inflight_tools", {}))
        _response_recovery = (saved_checkpoint or {}).get("response_recovery")
        _recovery_call_offset = (saved_checkpoint or {}).get("response_recovery_call_offset", 0)
        progress_callback = getattr(self.blackboard, "model_authoring_progress", None)
        author_progress_version = ((saved_checkpoint or {}).get("author_progress_version")
            if saved_checkpoint is not None else 1 if self._is_trade_idea_model_author(round_n, task_context)
            and callable(progress_callback) else None)

        def append_author_progress():
            if author_progress_version != 1:
                return
            if not callable(progress_callback):
                raise RuntimeError("Saved model author progress callback is unavailable")
            ledger = progress_callback(remaining_turns=max(0, max_tool_iters - iteration - 1),
                                       messages=_checkpoint_json(messages))
            text = "\n\nRECORDED MODEL WORK / MISSING AUTHOR ACTIONS:\n" + json.dumps(
                ledger, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if isinstance(messages[-1]["content"], list):
                messages[-1]["content"].append({"type": "text", "text": text})
            else:
                messages[-1]["content"] += text

        # New stages receive the ledger once per tool boundary. Saved messages
        # already contain it: replay never rebuilds a prompt from changed state.
        if saved_checkpoint is None:
            append_author_progress()

        def save_checkpoint(event, state=None, *, complete=False):
            state = state or {"version": 1, "contract": checkpoint_contract, "max_tokens": max_tokens,
                "initial_context": _ctx, "messages": messages, "iteration": iteration,
                "usage": _usage, "usage_unknown": _usage_unknown, "forced_report": _forced_report,
                "tool_calls": _tool_calls_round, "nudge_collasso": _nudge_collasso_dato,
                "retry_529": _retry_529, "retry_vuoto": _retry_vuoto,
                "thinking": _thinking, "thinking_prima": _thinking_prima,
                "pending_tools": _pending_tools, "inflight_tools": _inflight_tools,
                "status": "complete" if complete else "ready",
                "report": final_text if complete else None}
            state = _checkpoint_json(state)
            if author_progress_version is not None:
                state["author_progress_version"] = author_progress_version
            if _response_recovery:
                state.update(response_recovery=deepcopy(_response_recovery),
                             response_recovery_call_offset=_recovery_call_offset)
            _persist_specialist_checkpoint(self.blackboard, checkpoint_key, state, event)
            return state

        safe_snapshot = save_checkpoint("specialist_ready")

        while iteration < max_tool_iters:
            iteration += 1
            # §9-bis n.5 (ok PM 21/07): l'ULTIMA iterazione e' SEMPRE il report —
            # tool_choice none + nudge dichiarato. Il limite locale concede
            # max_tool_iters - 1 passaggi tool e riserva l'ultimo al report.
            # Il gate costi per chiamata resta indipendente e invariato.
            _final_forced = (iteration == max_tool_iters)
            if _final_forced:
                _forced_report = True
                _nudge = _forced_report_nudge(max_tool_iters)
                _lastm = messages[-1]
                if isinstance(_lastm.get("content"), list):
                    # dopo un giro tool: il nudge va IN CODA ai tool_result dello
                    # stesso messaggio user (due user consecutivi = errore API)
                    _lastm["content"].append({"type": "text", "text": _nudge})
                else:
                    _lastm["content"] = str(_lastm.get("content") or "") + "\n\n" + _nudge
            try:
                # fatti volatili spostati nel primo messaggio user (v. run()):
                # il system resta IDENTICO per tutta la run -> cache stabile.
                if _thinking is None:
                    _thinking = (role_thinking(self.blackboard, 'specialist')
                                 if getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
                                 else thinking_consigliere(self._model_for_round(round_n),
                                                           agente=self.name, round_n=round_n))
                _sys = self._request_system(system_round)
                # P1 26/07: se l'arsenale e' degradato il MODELLO deve saperlo e
                # dirlo, altrimenti il PM legge un report che tace un buco. Nel
                # caso sano questa riga non aggiunge nulla e il system resta
                # IDENTICO per tutta la run (cache stabile, v. commento sopra).
                if author_history is not None and _plan_digest(_sys) != author_history.get("system_sha256"):
                    raise ValueError("Historical author system differs from the current native system")
                # 200a: cache_control su tools (ultimo elemento = tutta la lista) e system.
                # NB tools_schema[-1] e' il meta-tool read_blackboard creato fresco per call: safe da decorare.
                _kw = {}
                if _final_forced:
                    _kw["tool_choice"] = {"type": "none"}
                if USE_PROMPT_CACHING and getattr(self.blackboard, "run_scope", "weekly") != "trade_idea":
                    _cc = {"type": "ephemeral"}
                    if CACHE_TTL == "1h":
                        _cc["ttl"] = "1h"
                        _kw["extra_headers"] = CACHE_BETA_HEADER
                    tools_schema = list(tools_schema)
                    tools_schema[-1] = {**tools_schema[-1], "cache_control": _cc}
                    _sys = [{"type": "text", "text": _sys, "cache_control": _cc}]
                    # 200a-bis: breakpoint mobile sull'ultimo tool_result (il 60-70%
                    # dell'input era la storia del round, ripagata piena ogni volta)
                    _move_cache_breakpoint(messages, _cc)
                # Nessun retry di rete qui (G7, 04/10: il vecchio retry sul 529 e'
                # tolto): il giro si ripete solo per il ritentativo «0 char» dichiarato;
                # ogni errore esce subito verso il ramo di dichiarazione qui sotto.
                _response_da_retry_vuoto = False
                while True:
                    try:
                        check_blocked = getattr(self.blackboard, "raise_if_run_blocked", None)
                        if callable(check_blocked):
                            check_blocked()
                        _t_call = time.perf_counter()  # 27/08: durata della call nel WARN TRONCATA
                        _author_snapshot = None
                        if (self.name == "fundamentals" and round_n == 1 and not consultation
                                and getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
                                and getattr(self.blackboard, "model_phase", None) == "building"):
                            from bellomberg.agents.trade_idea import _model_consultation_read_snapshot
                            _author_snapshot = _model_consultation_read_snapshot(self.blackboard)
                        if model_completion:
                            # Keep the complete raw conversation in the native
                            # checkpoint. The gate tries its paid wire first,
                            # validates this reproducible view and pins any new
                            # projected request before reservation/dispatch.
                            from bellomberg.agents.model_authoring_context import project_model_authoring_messages
                            _, _kw["model_authoring_context_projection"] = project_model_authoring_messages(
                                _checkpoint_json(messages))
                        response = self._chiama_modello(
                            model=self._model_for_round(round_n),
                            max_tokens=max_tokens,
                            thinking=_thinking,
                            system=_sys,
                            tools=tools_schema,
                            messages=messages,
                            **_kw,
                        )
                        if self.name == 'fundamentals' and round_n in (1, 2) and _frozen_notes is not None:
                            from bellomberg.core.current_facts import record_research_notes_delivery
                            record_research_notes_delivery(self.blackboard, 'fundamentals:' + str(round_n),
                                                           messages[0]['content'])
                        # Audit 11/09: stop max_tokens e NESSUN testo visibile = la risposta
                        # e' tutta ragionamento. Si ritenta UNA volta la STESSA call (stessi
                        # messaggi) con thinking disabled. La call pagata entra nel conto
                        # (usage e api_calls); il ritentativo si dichiara a log.
                        # 12/09 (Fable 5.1, via A del mandato): i TOOL RESTANO DISPONIBILI
                        # nel ritentativo — con tool_choice=none (com'era) il round ritentato
                        # era un riassunto a memoria: in R2 senza il proprio R1 in vista,
                        # per Fundamentals senza get_valuation e quindi senza Excel (recon
                        # 12/09 par. 2.1). Il modello riceve un nudge DICHIARATO in coda ai
                        # messaggi (prima rimandava gli stessi messaggi senza una parola).
                        # Sull'ultima iterazione (_final_forced) i tool restano spenti per
                        # la regola del report garantito, e il nudge lo dice.
                        if (getattr(self.blackboard, "run_scope", "weekly") != "trade_idea"
                                and getattr(response, "stop_reason", None) == "max_tokens"
                                and getattr(getattr(response, "usage", None), "cost_usd", None) is not None
                                and _retry_vuoto < 1
                                and not any(str(getattr(b, "text", "") or "").strip()
                                            for b in (getattr(response, "content", None) or []))):
                            _retry_vuoto += 1
                            _u0 = getattr(response, "usage", None)
                            _out0 = getattr(_u0, "output_tokens", None)
                            print("  [" + self.name + "] WARN: risposta TRONCATA (stop_reason="
                                  "max_tokens, cap " + str(max_tokens) + " token) con "
                                  "0 char di testo visibile (output_tokens="
                                  + (str(_out0) if _out0 is not None else "n.d.")
                                  + ", tutto ragionamento, call di %.1f s): RITENTO una volta "
                                    "la stessa call SENZA ragionamento, "
                                  % (time.perf_counter() - _t_call)
                                  + ("tool DISABILITATI (limite iterazioni)" if _final_forced
                                     else "tool disponibili")
                                  + ", con nudge dichiarato in coda ai messaggi")
                            try:
                                _usage = _somma_usage(_usage, _u0)
                                if _usage.get("tokens_status") == "parziale":
                                    _usage_unknown = True
                            except Exception as _ue:
                                _usage_unknown = True
                                print("[" + self.name + "] WARN usage non esposto dalla call "
                                      "troncata: " + str(_ue))
                            _thinking_prima = _thinking
                            _thinking = {"type": "disabled"}
                            _nudge_rv = (
                                "[RITENTATIVO DICHIARATO] Il primo tentativo di questa risposta "
                                "non ha prodotto testo: e' uscito al limite di output con tutto "
                                "il budget speso in ragionamento. Scrivi ORA il report. "
                                + ("I tool restano DISABILITATI (limite iterazioni raggiunto): "
                                   "usa i dati gia' raccolti e dichiara n.d. cio' che non hai "
                                   "potuto verificare."
                                   if _final_forced else
                                   "Usa i tool se ti servono numeri: un numero senza [src: tool] "
                                   "non vale. Se un dato non arriva, dichiaralo n.d."))
                            _lastm = messages[-1]
                            if isinstance(_lastm.get("content"), list):
                                # dopo un giro tool: IN CODA ai tool_result dello stesso
                                # messaggio user (due user consecutivi = errore API)
                                _lastm["content"].append({"type": "text", "text": _nudge_rv})
                            else:
                                _lastm["content"] = (str(_lastm.get("content") or "")
                                                     + "\n\n" + _nudge_rv)
                            _response_da_retry_vuoto = True
                            _t_call = time.perf_counter()
                            continue
                        break
                    except Exception:
                        # No outer retry from a status code alone: the request
                        # journal retains the original uncertain reservation.
                        raise
                # 12/09: il ritentativo e' finito (in un verso o nell'altro): le iterazioni
                # successive del round tornano al ragionamento di prima.
                if _thinking_prima is not None:
                    _thinking = _thinking_prima
                    _thinking_prima = None
            except Exception as e:
                _record_run_failure(self.blackboard, e, self.name, round_n)
                print("[" + self.name + "] API error: " + str(e))
                err_txt = "[ERROR " + self.name + " round " + str(round_n) + "]: " + str(e)
                try:
                    if publish_report:
                        self.blackboard.write(self.name, round_n, err_txt)
                        self.blackboard.mark_specialist_error(self.name, str(e))
                except Exception:
                    pass
                # collaudo #44: qui si usciva BUTTANDO i token gia' spesi nelle
                # iterazioni precedenti (errore al 5o giro = 4 chiamate pagate e
                # sparite dal conto). Un agente fallito che non compare nei costi e'
                # il fallback silenzioso vietato dal PM il 14/07: lo dichiariamo.
                try:
                    prior_known_cost = _usage.get("cost_usd")
                    _usage = _somma_usage(_usage, None)
                    _usage["known_cost_usd"] = prior_known_cost
                    self.blackboard.record_usage(
                        self.name, round_n, self._model_for_round(round_n), _usage,
                        duration_s=round(time.perf_counter() - _t0, 2),
                        api_calls=iteration + _retry_529 + _retry_vuoto + _recovery_call_offset,
                        cache_ttl=CACHE_TTL if USE_PROMPT_CACHING else None,
                        status="api_error", retry_vuoto=_retry_vuoto, lavoro=self._lavoro_corrente())
                except Exception as ue:
                    print("[" + self.name + "] WARN usage non registrato: " + str(ue))
                return err_txt

            # Se la risposta non espone usage i token NON diventano zero in silenzio:
            # zero direbbe "questa chiamata non e' costata nulla" su un agente che ha
            # girato 10 minuti. Il buco si segna e a fine round lo status diventa
            # "usage_unknown" (token IGNOTI) invece di "ok".
            try:
                _usage = _somma_usage(_usage, getattr(response, "usage", None))
                _usage_unknown = _usage["tokens_status"] == "parziale"
            except Exception as e:
                _usage_unknown = True
                print("[" + self.name + "] WARN usage non esposto dalla risposta "
                      "(iter " + str(iteration) + "): " + str(e))

            save_checkpoint("specialist_response", {**safe_snapshot,
                "pending_tools": _pending_tools, "inflight_tools": _inflight_tools,
                "last_response_id": getattr(response, "id", None),
                "last_request_id": getattr(response, "request_id", None)})

            if _author_snapshot is not None:
                from bellomberg.agents.trade_idea import _record_fundamentals_received_consultations
                _record_fundamentals_received_consultations(self.blackboard, _author_snapshot, response)

            stop = response.stop_reason
            if stop == "end_turn":
                # audit/11 §2: concatena TUTTI i blocchi text (fix del Capo 25/06 mai
                # portato qui: col thinking il primo blocco puo' non essere l'analisi)
                _parts = [b.text for b in response.content if hasattr(b, "text")]
                final_text = "\n".join(p for p in _parts if p)
                # Fix 1b §9-sextrigies (voce 4 §9-quattuortrigies, ok PM 03/08):
                # end_turn con ZERO tool e testo-annuncio sotto soglia in R0/R1 e'
                # il collasso announce-then-stop dell'eventdesk V6 (113 char).
                # UN retry col nudge; se ricade, marcatore dichiarato nel testo e
                # nel log. R2 fuori perimetro (mai misurato li'; la revisione ha
                # il blackboard in pancia e testi corti possono essere legittimi).
                _collasso = (round_n <= 1 and _tool_calls_round == 0
                             and len(final_text.strip()) < SOGLIA_COLLASSO_ANNUNCIO
                             and not _final_forced and not _response_da_retry_vuoto)
                if _collasso and (getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
                                  or _usage.get("cost_usd") is None):
                    final_text = ("[ERROR " + self.name + " round " + str(round_n)
                                  + "]: report troppo breve senza tool; nessun retry pagato. "
                                  + "Testo ricevuto: " + final_text)
                    break
                if _collasso and not _nudge_collasso_dato:
                    _nudge_collasso_dato = True
                    print("  [" + self.name + "] end_turn SENZA tool con "
                          + str(len(final_text.strip())) + " char (round "
                          + str(round_n) + "): annuncio-senza-lavoro, UN retry col nudge")
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": (
                        "Hai chiuso il turno ANNUNCIANDO il lavoro invece di farlo: "
                        "nessun tool chiamato e poche righe di testo. Ora esegui "
                        "davvero: chiama i tool che servono e scrivi il report "
                        "completo. Se un dato non arriva, dichiaralo n.d. — ma il "
                        "report va scritto, non annunciato.")})
                    continue
                if _collasso and _nudge_collasso_dato:
                    _marcatore = (MARCATORE_COLLASSO + " (round " + str(round_n)
                                  + "): end_turn con 0 tool e "
                                  + str(len(final_text.strip()))
                                  + " char anche dopo il retry — quanto segue e' un "
                                  "annuncio, NON un'analisi]")
                    print("  [" + self.name + "] " + _marcatore)
                    final_text = _marcatore + "\n\n" + final_text
                break
            elif stop == "tool_use":
                if _response_recovery and (_response_recovery.get("mode") == "report_only" or _final_forced):
                    final_text = "[ERROR " + self.name + " round " + str(round_n) + "]: report-only completion returned tools; no tool dispatched."
                    break
                if _usage.get("cost_usd") is None:
                    final_text = "[ERROR " + self.name + " round " + str(round_n) + "]: provider cost unknown; pending tools preserved without dispatch."
                    break
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        _tool_calls_round += 1  # fix 1b: 0 a fine round = annuncio
                        tname = block.name
                        tinput = block.input
                        print("  [" + self.name + "] -> " + tname + "(" + str(tinput)[:80] + ")")
                        tool_key = _checkpoint_digest({"iteration": iteration,
                            "response_id": getattr(response, "id", None),
                            "tool_id": block.id, "name": tname, "input": tinput})
                        replayed_tool = tool_key in _pending_tools
                        recovered_note = None
                        if not replayed_tool and tool_key in _inflight_tools:
                            from bellomberg.agents.company_research_tools import TOOL_NAMES
                            # These three run-native tools journal the GET intent
                            # and verified bytes before returning. Their service
                            # replays durable receipts and rejects an unknown GET;
                            # no other tool receives automatic replay authority.
                            local_reply_replay = reply_completion and tname in RESEARCH_REPLY_LOCAL_TOOLS
                            note_store = getattr(self.blackboard, 'weekly_store', None)
                            if (tname == 'add_research_note' and note_store is not None
                                    and 'research_notes_policy' in note_store.context['contract']):
                                from bellomberg.core.current_facts import recover_frozen_research_reply
                                recovered_note = recover_frozen_research_reply(
                                    note_store, checkpoint_key, tool_key, tinput)
                            if recovered_note is None and not local_reply_replay and (tname not in TOOL_NAMES or not callable(
                                    getattr(self.blackboard, "company_source_session", None))):
                                raise RuntimeError("tool dispatch outcome unknown; automatic replay blocked: " + tname)
                        if not replayed_tool:
                            check_blocked = getattr(self.blackboard, "raise_if_run_blocked", None)
                            if callable(check_blocked):
                                check_blocked()
                            _inflight_tools[tool_key] = {"name": tname, "input": tinput, "tool_id": block.id}
                            save_checkpoint("specialist_tool_dispatch", {**safe_snapshot,
                                "pending_tools": _pending_tools, "inflight_tools": _inflight_tools})
                        self._research_note_event_id = checkpoint_key + ':' + tool_key
                        result = (json.loads(_pending_tools[tool_key]["output_json"]) if replayed_tool
                                  else recovered_note if recovered_note is not None
                                  else self._execute_meta_tool(tname, tinput))
                        # Audit 11/09 (Fable 5.1): il tool_log registrava SOLO l'input; per
                        # ricostruire cosa un desk avesse letto (il «35%» dal web, il «count
                        # 0» di Polymarket) non c'era nulla. Si conserva la testa dell'esito
                        # (tetto fisso, dichiarato con `output_tappato`): e' una misura
                        # per l'audit, non un secondo canale per il modello.
                        try:
                            _out_s = json.dumps(result, default=str, ensure_ascii=False)
                        except Exception:
                            _out_s = str(result)
                        # Diagnostica passiva separata: stessi byte al modello/receipt/journal.
                        from bellomberg.core.source_health import capture_health
                        capture_health(self.blackboard, checkpoint_key + ":" + tool_key, tname, result,
                                       desk=self.name, round_n=round_n, output_json=_out_s, input_values=tinput)
                        if not replayed_tool and (getattr(self.blackboard, "run_scope", "weekly") == "trade_idea"
                                                  or is_research_mode(self.blackboard) or _evidence_followup):
                            _receipt_truncated = len(_out_s) > 200000
                            self.blackboard.tool_receipts.append({
                                "tool": tname, "input": tinput,
                                "source": result.get("_source") if isinstance(result, dict) else None,
                                "timestamp": result.get("_timestamp") if isinstance(result, dict) else None,
                                "success": _trade_idea_tool_receipt_success(
                                    result, tname, truncated=_receipt_truncated),
                                "output": _out_s[:200000], "truncated": _receipt_truncated,
                                **({"run_id": self.blackboard.weekly_store.run_id, "round": round_n,
                                    "desk": self.name, "observed_at": datetime.now(timezone.utc).isoformat()}
                                   if _evidence_followup else {}),
                            })
                        if not replayed_tool:
                            self.blackboard.tool_log.append({
                            "specialist": self.name, "round": round_n, "tool": tname,
                            "input": str(tinput)[:200], "time": datetime.now().strftime("%H:%M:%S"),
                            "output": _out_s[:TOOL_LOG_OUTPUT_MAX],
                            "output_tappato": len(_out_s) > TOOL_LOG_OUTPUT_MAX,
                            "output_chars": len(_out_s),
                            })
                        self.blackboard._write_heartbeat()
                        result_str = json.dumps(result, default=str, ensure_ascii=False)
                        if tname == "get_valuation" and isinstance(result, dict):
                            # Il dossier acquisito puo' precedere il FV e superare il
                            # tetto da solo. La vista gia' usata da Capo/red team mette
                            # esito, gate, buchi e riferimenti prima del dettaglio.
                            # Blackboard/DB/sidecar conservano il payload integrale.
                            from bellomberg.valuation.sector_analysis import valuation_results_block
                            _vp = result.get("data") if isinstance(result.get("data"), dict) else result
                            result_str = (valuation_results_block({str(tinput.get("ticker") or "n.d."): _vp})
                                + "\n\nDettaglio sotto, soggetto al tetto tool_result; "
                                  "snapshot integrale nel sidecar se generato:\n" + result_str)
                        # 20/08 (ok PM): il tetto vive in UN posto solo. Prima era un
                        # letterale qui, uno in red_team.py e la costante in chat_tools
                        # che le viste compatte leggono per decidere se DEGRADARE:
                        # alzare la costante lasciando i letterali avrebbe fatto smettere
                        # di degradare le viste e tagliato i payload in coda, dove stanno
                        # le dichiarazioni. Stessa classe delle fonti prezzi scritte in
                        # due posti (19/08). Import fallito = tetto prudente dichiarato.
                        try:
                            from bellomberg.agents.chat_tools import TETTO_TOOL_RESULT as _TETTO
                        except Exception as _te:
                            _TETTO = 6000
                            print("[" + self.name.upper() + "] WARN tetto tool_result non "
                                  "importabile da chat_tools (" + type(_te).__name__
                                  + "): uso 6000, il valore prudente di prima (dichiarato)")
                        if len(result_str) > _TETTO:
                            result_str = result_str[:_TETTO] + "...[truncated]"
                        if replayed_tool:
                            result_str = _pending_tools[tool_key]["delivered_text"]
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": block.id, "content": result_str,
                        })
                        _pending_tools[tool_key] = {"output_json": _out_s, "tool": tname,
                                                   "input": tinput, "delivered_text": result_str}
                        _inflight_tools.pop(tool_key, None)
                        save_checkpoint("specialist_tool", {**safe_snapshot,
                            "pending_tools": _pending_tools, "inflight_tools": _inflight_tools})
                messages.append({"role": "user", "content": tool_results})
                _pending_tools = {}
                append_author_progress()
                safe_snapshot = save_checkpoint("specialist_turn")
            else:
                _parts = [b.text for b in response.content if hasattr(b, "text")]
                final_text = "\n".join(p for p in _parts if p)
                if stop == "max_tokens":
                    # 27/08 (run V8+V9): la troncatura si diagnostica DAL LOG — cap,
                    # output_tokens (ragionamento adattivo + testo) e char del testo
                    # visibile: la differenza e' quanto pensiero ha mangiato il cap.
                    # Usage assente = n.d., mai 0 (regola 14/07). Il prefisso
                    # «risposta TRONCATA (stop_reason=max_tokens» resta: chi legge
                    # il log lo cerca cosi' (analisi delle run V8/V9).
                    _out_tok = getattr(getattr(response, "usage", None), "output_tokens", None)
                    # audit 11/09: OpenRouter espone anche la quota di ragionamento
                    # (usage.reasoning_tokens): quando c'e', il log la stampa invece di stimarla
                    _rt_tok = getattr(getattr(response, "usage", None), "reasoning_tokens", None)
                    print("  [" + self.name + "] WARN: risposta TRONCATA (stop_reason="
                          "max_tokens, cap " + str(max_tokens) + " token): "
                          "output_tokens=" + (str(_out_tok) if _out_tok is not None else "n.d.")
                          + " (ragionamento adattivo + testo), testo visibile "
                          + str(len(final_text))
                          + (" char, call di %.1f s" % (time.perf_counter() - _t_call))
                          + (", reasoning_tokens=" + str(_rt_tok) if _rt_tok is not None else "")
                          + " — l'analisi potrebbe essere incompleta")
                # 26/07 (Opus 5, pre-V6): stop_reason="refusal" entra QUI (non e'
                # end_turn ne' tool_use) con content vuoto -> senza dichiarazione il
                # blackboard riceveva "No output produced" e il Capo leggeva un
                # silenzio senza causa. Il rifiuto NON e' un errore API: si dichiara.
                # NB: NON si chiama mark_specialist_error qui — lo status verrebbe
                # riscritto a "done" 20 righe sotto (sfarfallio nella UI, zero
                # segnale durevole). La dichiarazione che resta e' il testo, che
                # e' cio' che leggono Capo, memo e DB.
                _rif = _refusal_reason(response, self.name)
                if _rif:
                    print("  [" + self.name + "] " + _rif[:220])
                    final_text = (_rif + "\n\n" + final_text) if final_text.strip() else _rif
                break

        raw_final_text = final_text
        # §9-bis n.5: il report forzato si DICHIARA in testa (Capo/memo/DB lo vedono)
        if str(final_text or "").strip() and _forced_report:
            # Voce 6 §9-quattuortrigies (01/08): il marcatore stava SOLO nel testo
            # del report (DB) — grep 'REPORT FORZATO' sul log dava 0 e chi legge il
            # log concludeva che non era successo. Si dichiara anche su stdout.
            print("  [" + self.name + "] REPORT FORZATO AL LIMITE ITERAZIONI ("
                  + str(max_tool_iters) + "): verifiche tool esaurite")
            final_text = ("[REPORT FORZATO AL LIMITE ITERAZIONI (" + str(max_tool_iters)
                          + "): verifiche tool esaurite, buchi dichiarati nel testo]\n\n" + final_text)
        # 12/09 (Fable 5.1, prerequisito 3 del mandato): un round >= 1 chiuso con ZERO
        # chiamate tool si DICHIARA in testa al testo, nella stessa forma dei marcatori
        # qui sopra — il testo e' cio' che leggono blackboard, DB, memoria del desk e
        # Capo (che ha la sua dottrina sul prefisso). Vale per tutti i desk (decisione
        # orchestratore 12/09). R0 e' ricognizione (fuori perimetro, come il collasso); il
        # segnaposto «No output produced» non ha numeri da verificare e il marcatore in
        # testa lo travestirebbe da report per _e_segnaposto (primi 120 char).
        # Se il testo si apre GIA' con un marcatore che dice la stessa cosa in modo piu'
        # preciso (il collasso annuncio-senza-tool nomina il round, i caratteri e il retry),
        # non se ne impila un secondo: due marcatori sullo stesso fatto sono rumore in testa
        # al report, e la testa e' cio' che il Capo e _e_segnaposto guardano per primo.
        _output_finale_mancante = not str(final_text or "").strip()
        if not _output_finale_mancante and round_n >= 1 and _tool_calls_round == 0 \
                and not final_text.startswith(MARCATORE_COLLASSO):
            _marc_nt = ("[ROUND " + str(round_n) + " SENZA TOOL: nessuna chiamata tool in "
                        "questo round; i numeri non sono verificati con i tool]")
            if _evidence_followup:
                _marc_nt = no_acquisition_notice(self.blackboard, round_n)
            print("  [" + self.name + "] " + _marc_nt)
            final_text = _marc_nt + "\n\n" + final_text
        if _output_finale_mancante:
            final_text = ("[ERROR " + self.name + " round " + str(round_n)
                          + "]: No output produced in round " + str(round_n))
            if _retry_vuoto:
                final_text += (" dopo max_tokens con zero testo (2 tentativi: anche il "
                               "ritentativo senza ragionamento e' "
                               "uscito con 0 char di testo)")

        visible = any(str(getattr(block, "text", "") or "").strip() for block in response.content)
        result_status = ("truncated" if stop == "max_tokens" else
            "complete" if stop == "end_turn" and visible and not _usage_unknown
                and _usage.get("cost_usd") is not None
                and not raw_final_text.startswith("[ERROR")
                and not _refusal_reason(response, self.name) else "failed")
        self.run_result_status = result_status
        if consultation:
            self.consultation_result_status = result_status
        if result_status != "complete":
            error = RuntimeError("specialist response " + result_status + ": stop_reason=" + str(stop))
            error.request_id = getattr(response, "request_id", None)
            _record_run_failure(self.blackboard, error, self.name, round_n)
        if publish_report:
            self.blackboard.write(self.name, round_n, final_text)
            # Andrea 02/10: nessun testo finale = errore dichiarato, mai "done".
            if result_status == "complete" and not _output_finale_mancante:
                self.blackboard.mark_specialist_done(self.name)
            else:
                self.blackboard.mark_specialist_error(
                    self.name, final_text if _output_finale_mancante else str(stop))
        print("[" + self.name + "] Round " + str(round_n) + " done (" + str(len(final_text)) + " chars)")
        # collaudo #44: l'usage non si butta piu' col return — va nel usage_log del
        # blackboard (-> heartbeat -> UI). Modello EFFETTIVO del round (R0 = Sonnet,
        # R1/R2 = Opus): con self.model fisso il costo di R0 sarebbe ~5x quello vero.
        _duration_s = round(time.perf_counter() - _t0, 2)
        _entry = None
        try:
            _entry = self.blackboard.record_usage(
                self.name, round_n, self._model_for_round(round_n), _usage,
                duration_s=_duration_s, api_calls=iteration + _retry_529 + _retry_vuoto + _recovery_call_offset,
                cache_ttl=CACHE_TTL if USE_PROMPT_CACHING else None,
                status=("api_error" if _output_finale_mancante else
                        ("usage_unknown" if _usage_unknown else "ok")),
                retry_vuoto=_retry_vuoto, lavoro=self._lavoro_corrente())
        except Exception as ue:
            print("[" + self.name + "] WARN usage non registrato: " + str(ue))
        if consultation and self.consultation_result_status == "truncated":
            from bellomberg.agents.trade_idea import _native_consultation_terminal_response
            self.consultation_terminal_response = _native_consultation_terminal_response(
                response, final_text, _usage, _entry, self.name)
        terminal_checkpoint = save_checkpoint("specialist_report", complete=result_status == "complete")
        if result_status != "complete":
            save_checkpoint("specialist_incomplete", {**terminal_checkpoint,
                "status": result_status, "report": final_text})
        print(f"  [{self.name}] usage R{round_n}: in={_usage['in'] if _usage['in'] is not None else 'n.d.'} out={_usage['out'] if _usage['out'] is not None else 'n.d.'} "
              f"cache_read={_usage['cache_read'] if _usage['cache_read'] is not None else 'n.d.'} cache_write={_usage['cache_write'] if _usage['cache_write'] is not None else 'n.d.'} "
              f"({iteration + _retry_529 + _retry_vuoto + _recovery_call_offset} call API, {_duration_s}s, costo {_fmt_cost((_entry or {}).get('cost_eur'))})")
        return final_text
