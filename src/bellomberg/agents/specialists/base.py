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
from datetime import datetime
from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm, somma_usage as _somma_usage
from bellomberg.storage.memory_db import DB_DIR   # B4 (02/09): heartbeat e rescue sotto la cartella dati
from bellomberg.core.llm_refusal import refusal_reason as _refusal_reason
from bellomberg.core.language import capture_language, prompt_for_language, scoped_language
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
# 12.000 -> 16.000 il 27/08 (decisione PM su 2 punti dati: quant R2 TRONCATA su
# max_tokens in V8 e in V9). In V9 il testo salvato della call troncata era 9.578
# char: a 2,1 char/token (misure di casa con count_tokens, 26/07) ~4,6k token di
# testo → STIMA: circa 2/3 del cap era ragionamento adattivo, che conta nel cap
# («max_tokens is a hard cap on thinking plus response text», skill claude-api,
# migrazione a Opus 5; il numero vero lo stampa il WARN qui sotto). 16.000 e' il
# valore degli esempi della skill e la soglia oltre cui chiede lo streaming: la
# chiamata resta NON in streaming, quindi questo e' il bordo. E' un tetto, non
# una spesa: costa solo quando usato (<= 4k token in piu' per call troncata =
# 0,10 $ su Opus 5, 0,04 $ su Sonnet 5 in R0: `llm_pricing.PRICING_USD_PER_MTOK`,
# listino 24/06/2026). Caso peggiore assoluto (ogni call della run tronca):
# ~+6 € su una run da 14 € di desk; storico 4 troncature in 8 run → ~+0,05 $/run.
MAX_TOKENS_SPECIALIST = 16000
# audit 11/09: testa dell'esito di ogni tool nel tool_log (heartbeat + blackboard archiviata),
# per l'audit; il modello riceve l'esito intero come prima (TETTO_TOOL_RESULT di chat_tools)
TOOL_LOG_OUTPUT_MAX = 400

# Il timeout del client SEGUE il cap (review 27/08, finding ALTO per costi/API):
# a 65-80 tok/s misurati in V9 (la call troncata da 12k e' durata <= 203 s), a
# 16k una call vale 200-250 s — contro i 240 s di #196 (giugno: «una chiamata
# appesa fallisce e la run prosegue»), e sul timeout l'SDK ritenta una volta e
# il desk perde il report INTERO, non un pezzo. L'SDK stesso, per il
# non-streaming, stima 3600 x max_tokens / 128000 (= 450 s a 16k): lo stesso
# numero, con il pavimento di #196. Legame MECCANICO in una FUNZIONE provata su
# piu' cap (tests/test_tetto_specialisti_16k.py): a 16k «450 fisso» sarebbe
# indistinguibile, sono il pavimento e la formula sugli altri cap a misurarlo.
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
# Resilienza R0/R1 (fix 1 §9-sextrigies, ok PM 03/08). Due guasti misurati:
# V7 03/08 fundamentals morto alla PRIMA chiamata su HTTP 529 senza retry;
# V6 28/07 eventdesk end_turn con 113 char di annuncio e 0 tool promosso a
# report. Il 529 e' transiente per definizione (overloaded): si ritenta con
# pause dichiarate; l'annuncio-senza-tool riceve UN nudge e, se ricade, esce
# col marcatore dichiarato (mai un "done (113 chars)" zitto - regola 14/07).
RETRY_529_BACKOFF_S = (20.0, 60.0)   # pause dei tentativi extra sul solo 529
SOGLIA_COLLASSO_ANNUNCIO = 800       # end_turn con 0 tool sotto questa soglia = annuncio
# Testa dei due marcatori che dichiarano «questo round non ha usato i tool». Costante e
# non letterale copiato: il marcatore generico (round senza tool, 12/09) NON si impila
# sopra quello del collasso, che dice lo stesso fatto con piu' dettaglio — e il predicato
# che li tiene insieme dev'essere uno solo (lezione: i letterali copiati si scollegano).
MARCATORE_COLLASSO = "[COLLASSO ANNUNCIO-SENZA-TOOL"


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

    def __init__(self, memory_db=None, memo_id=None):
        self.language = capture_language()
        self.data = {}
        self.current_round = 0
        self.tool_log = []
        self.memory_db = memory_db
        self.valuation_results = {}
        self.memo_id = memo_id
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

    def record_usage(self, agent, round_n, model, usage, duration_s=None,
                     api_calls=0, cache_ttl=None, status="ok", retry_vuoto=0):
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
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
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
        Chiamare col lock gia' preso (lo fanno heartbeat e mark_run_complete)."""
        by = {}
        for e in self.usage_log:
            a = e.get("agent") or "?"
            d = by.setdefault(a, {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0,
                                  "cost_eur": None, "partial": False, "duration_s": None,
                                  "api_calls": 0, "retry_vuoto": 0, "status": "ok",
                                  "_priced": 0, "_unpriced": 0})
            for k in ("in", "out", "cache_read", "cache_write"):
                d[k] = d[k] + e[k] if d[k] is not None and e.get(k) is not None else None
            d["api_calls"] += int(e.get("api_calls") or 0)
            # 12/09: quanti round del desk hanno ritentato una call a 0 char (-> heartbeat/UI)
            d["retry_vuoto"] += int(e.get("retry_vuoto") or 0)
            if e.get("duration_s") is not None:
                d["duration_s"] = (d["duration_s"] or 0.0) + float(e["duration_s"])
            if e.get("cost_eur") is None:
                # round non prezzabile: NON azzera ne' cancella i fratelli prezzati,
                # segna solo che la cifra dell'agente e' un minimo (partial sotto)
                d["_unpriced"] += 1
            else:
                d["cost_eur"] = (d["cost_eur"] or 0.0) + float(e["cost_eur"])
                d["_priced"] += 1
            # status dell'agente = il PEGGIORE dei suoi round (ok < usage_unknown <
            # model_unknown < pricing_unavailable < api_error)
            d["status"] = _worse_status(d["status"], e.get("status") or "ok")
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
                 "unpriced_agents": [], "error_agents": []}
        # Il totale si somma per ENTRY (non per agente): e' l'unico modo di non
        # perdere i round prezzati di un agente che ha anche round ignoti. Coincide
        # con la somma dei per-agente non-None -> invariante testata.
        _priced_total = 0.0
        _any_priced = False
        for e in self.usage_log:
            if e.get("cost_eur") is None:
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
            if d["status"] in ("api_error", "usage_unknown"):
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
                        "error": "aggregazione usage fallita: " + str(e)}

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
                "language": self.language,
                "running": True,
                "start_time": self.start_time,
                "current_round": self.current_round,
                "current_specialist": self.current_specialist,
                "specialist_status": self.specialist_status,
                # 27/08 (N8 di F44): le ULTIME N chiamate + il totale VERO + il
                # tappo dichiarato; prima F4 leggeva "50 su 50" per tutta la run.
                "tool_log": self.tool_log[-self.HEARTBEAT_TOOL_LOG_MAX:],
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

    def mark_run_complete(self):
        self.current_specialist = None
        self._lock.acquire()  # stesso file del heartbeat: mai scritture sovrapposte
        try:
            import os
            os.makedirs(os.path.dirname(self.HEARTBEAT_PATH), exist_ok=True)
            # stesse chiavi usage del heartbeat: senza, il conto della run sparirebbe
            # dalla UI proprio quando serve (a run finita)
            _usage_by, _usage_tot = self._usage_state()
            state = {
                "running": False,
                "start_time": self.start_time,
                "completed_at": datetime.now().isoformat(timespec="seconds"),
                "specialist_status": self.specialist_status,
                "tool_log": self.tool_log,
                "n_tool_calls": len(self.tool_log),
                "tool_log_tappato": False,  # a run finita il log e' intero
                "memo_id": self.memo_id,
                "usage_by_specialist": _usage_by,
                "usage_total": _usage_tot,
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
        _placeholder = isinstance(report, str) and (
            report.startswith("[ERROR") or "No output produced in round" in report[:120])
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
        self.client = client or OpenRouterClient(timeout=TIMEOUT_SPECIALIST_S, max_retries=1)

    def _model_for_round(self, round_n):
        """Ripipeline 15/07 (ok PM): R0 recon sul modello leggero, analisi R1 e
        replica R2 sul modello pieno del desk. 05/09: entrambi dal .env
        (CONSIGLIERE_R0_MODEL; CONSIGLIERE_<DESK>_MODEL o CONSIGLIERE_MODEL)."""
        return _modello_llm("consigliere", self.name, round_n)

    def _build_tools_schema(self):
        # #195: REGISTRO RICCO delle chat (36 tool, paid data) col subset per-agente.
        # I nomi specialista (quant/macro/options/eventdesk/crypto/fundamentals)
        # coincidono con gli agent_id delle chat: stesso arsenale del Capo interattivo.
        try:
            from bellomberg.agents import chat_tools
            filtered = list(chat_tools.get_tools_for_agent(self.name))
        except Exception as e:
            # P1 26/07: ripiego sul registro LEGACY (21 nomi contro i 51 vivi) e
            # filtrato per `tools_used`, che e' documentazione stantia (fundamentals
            # ne dichiara 13 e ne riceve 24). Prima era muto: ora e' dichiarato su
            # log + registro + prompt del modello (v. _arsenale_degradato).
            filtered = [t for t in agent_tools.TOOLS_SCHEMA if t["name"] in self.tools_used]
            self._arsenale_degradato = _dichiara_fallback(
                "_build_tools_schema[" + self.name + "]", e,
                "registro LEGACY agent_tools al posto di chat_tools: " +
                str(len(filtered)) + " tool invece del subset vivo")
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
        filtered.append({
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
        })
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
        return filtered

    def _execute_meta_tool(self, name, input_):
        if name == "ask_specialist":
            target = input_.get("specialist", "").lower()
            q = input_.get("question", "")
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
                    result = chat_tools.dispatch(name, input_, caller="specialista-run:" + self.name,
                        prepared_bundle=getattr(self, "_sector_bundles", {}).get(ticker))
                    payload = result.get("data") if isinstance(result.get("data"), dict) else result
                    if payload.get("acquisition_snapshot"):
                        self._sector_bundles[ticker] = payload["acquisition_snapshot"]
                    if hasattr(self.blackboard, "valuation_results"):
                        with self.blackboard._lock:
                            self.blackboard.valuation_results[ticker] = payload
                    db = getattr(self.blackboard, "memory_db", None)
                    decision_id = getattr(self, "_research_decision_links", {}).get(ticker)
                    if db is not None and decision_id and payload.get("snapshot_id"):
                        try:
                            db.link_valuation_snapshot(payload["snapshot_id"],
                                generation_id=payload["generation_id"], decision_id=decision_id)
                        except Exception as exc:
                            payload["research_snapshot_error"] = type(exc).__name__ + ": " + str(exc)
                    return result
                return chat_tools.dispatch(name, input_, caller="specialista-run:" + self.name)
            except Exception as e:
                if name == "get_valuation":
                    return {"ok": False, "error": "Acquisizione/valutazione settoriale KO: " + str(e),
                            "exclude_from_action_table": True}
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
        if not self.blackboard.memory_db:
            return ""
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
                self.name, max_chars=MAX_CHAR_MEMORIA_SPECIALISTA)
        except Exception as e:
            return "\n\n[MEMORY ERROR: " + str(e) + "]"

    def compute_score(self):
        """Override nei subclass: ritorna uno score deterministico (dict) o None.
        Pattern #186: il numero sta nel codice (rubric a punti), l'LLM lo NARRA."""
        return None

    def _build_round_context(self, round_n):
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
                "BLACKBOARD:\n"
                + _blocco_blackboard(others)
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
                "\nBLACKBOARD:\n"
                + _blocco_blackboard(others)
            )
        else:
            preamble = "Round " + str(round_n)
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
    def run(self, round_n):
        print("\n[" + self.name.upper() + "] Round " + str(round_n) + " start")
        # Una fotografia per round; la prossima chiamata vede eventuali modifiche.
        try:
            from bellomberg.core import mandato_pm
            try:
                mandato = mandato_pm.carica()
                errore_mandato = ""
            except mandato_pm.MandatoMancante as e:
                mandato, errore_mandato = None, str(e)
            system_round = mandato_pm.compila_o_dichiara(self.system_prompt, mandato)
            if "{MANDATO:" not in self.system_prompt:
                system_round += "\n\n" + (mandato_pm.blocco_prompt(mandato) if mandato is not None
                                            else mandato_pm.riga_senza_mandato())
            if errore_mandato:
                system_round += "\n[MANDATO n.d.] " + errore_mandato
        except Exception as e:
            import re
            causa = _dichiara_fallback("mandato[" + self.name + "]", e)
            system_round = re.sub(r"\{MANDATO:[^}]+\}", "(mandato n.d.)", self.system_prompt)
            system_round += "\n[MANDATO n.d.] " + causa
        # collaudo #44: durata per specialista (prima non esisteva alcun timer)
        _t0 = time.perf_counter()
        self.blackboard.mark_specialist_start(self.name, round_n)
        # Fase 3 "fatti volatili": current_facts (TTL 1h) e favorites (TTL 600s)
        # vivono QUI, nel primo messaggio user, CONGELATI per il round — non piu'
        # nel system cachato, dove un refresh a run in corso (>60 min) cambiava
        # il system a meta' conversazione e invalidava l'intera cache dell'agente.
        _ctx = self._build_round_context(round_n)
        blocchi = []
        for nome in ("current_facts_block", "favorites_block", "pm_theses_block"):
            try:
                from bellomberg.core import current_facts
                blocchi.append(getattr(current_facts, nome)())
            except Exception as e:
                causa = _dichiara_fallback(nome + "[" + self.name + "]", e)
                blocchi.append("\n\n[CONTESTO n.d.] " + causa
                              + ". Dichiara il buco nel report.")
        _ctx = "".join(blocchi) + "\n\n" + _ctx
        # C4 16/07 (richiesta PM): pipeline RESEARCH SOLO a fundamentals e SOLO in R1/R2
        # (R0 e' gia' saturo: 10/10 iterazioni nella run #45 — aggiungere lavoro li'
        # significherebbe recon troncata, non solo costo).
        if self.name == "fundamentals" and round_n >= 1:
            try:
                from bellomberg.core.current_facts import research_block
                _rb = research_block(sector_bundles=self._sector_bundles,
                                     decision_links=self._research_decision_links)
                if _rb:
                    _ctx = _rb.strip() + "\n\n" + _ctx
            except Exception as e:
                causa = _dichiara_fallback("research_block[fundamentals]", e)
                _ctx = "[CONTESTO n.d.] " + causa + "\n\n" + _ctx
        messages = [{"role": "user", "content": _ctx}]
        tools_schema = self._build_tools_schema()

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
        _thinking = {"type": "adaptive"}
        # 12/09 (Fable 5.1, prerequisito 3 del mandato): il ragionamento spento dal
        # ritentativo si RIPRISTINA dopo quella call — prima restava spento per il resto
        # del round (recon 12/09 par. 2.3). None = nessun ripristino in sospeso.
        _thinking_prima = None

        while iteration < MAX_TOOL_ITERS_SPECIALIST:
            iteration += 1
            # §9-bis n.5 (ok PM 21/07): l'ULTIMA iterazione e' SEMPRE il report —
            # tool_choice none + nudge dichiarato. Prima il 10o giro poteva essere un
            # tool_use i cui risultati venivano eseguiti ma MAI riletti dal modello
            # (giro buttato) e il round moriva in "[No output produced]". Budget
            # effettivo: 9 giri tool + report garantito, stesso costo di oggi.
            _final_forced = (iteration == MAX_TOOL_ITERS_SPECIALIST)
            if _final_forced:
                _forced_report = True
                _nudge = ("LIMITE ITERAZIONI TOOL RAGGIUNTO (" + str(MAX_TOOL_ITERS_SPECIALIST)
                          + "): in questa risposta i tool sono DISABILITATI. Scrivi ORA il "
                          "report finale con le informazioni gia' raccolte. Dichiara "
                          "esplicitamente cio' che NON hai potuto verificare (n.d.), "
                          "senza inventare numeri.")
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
                _sys = system_round + "\n\n" + prompt_for_language(SPECIALIST_STYLE_RULES)
                # P1 26/07: se l'arsenale e' degradato il MODELLO deve saperlo e
                # dirlo, altrimenti il PM legge un report che tace un buco. Nel
                # caso sano questa riga non aggiunge nulla e il system resta
                # IDENTICO per tutta la run (cache stabile, v. commento sopra).
                if self._arsenale_degradato:
                    _sys += ("\n\n[!! ARSENALE DEGRADATO - DICHIARALO NEL REPORT] "
                             + self._arsenale_degradato +
                             " -> stai lavorando con MENO TOOL del previsto: apri il "
                             "report con questa riga, elenca cosa NON hai potuto "
                             "verificare e NON colmare i buchi a memoria.")
                # 200a: cache_control su tools (ultimo elemento = tutta la lista) e system.
                # NB tools_schema[-1] e' il meta-tool read_blackboard creato fresco per call: safe da decorare.
                _kw = {}
                if _final_forced:
                    _kw["tool_choice"] = {"type": "none"}
                if USE_PROMPT_CACHING:
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
                # Fix 1a §9-sextrigies: retry SOLO sul 529 (overloaded, transiente
                # per definizione), pause dichiarate, tetto per round; ogni altro
                # errore ri-esce subito verso il ramo di dichiarazione qui sotto.
                while True:
                    try:
                        _t_call = time.perf_counter()  # 27/08: durata della call nel WARN TRONCATA
                        response = self.client.messages.create(
                            model=self._model_for_round(round_n),
                            max_tokens=MAX_TOKENS_SPECIALIST,
                            thinking=_thinking,
                            system=_sys,
                            tools=tools_schema,
                            messages=messages,
                            **_kw,
                        )
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
                        if (getattr(response, "stop_reason", None) == "max_tokens"
                                and _retry_vuoto < 1
                                and not any(str(getattr(b, "text", "") or "").strip()
                                            for b in (getattr(response, "content", None) or []))):
                            _retry_vuoto += 1
                            _u0 = getattr(response, "usage", None)
                            _out0 = getattr(_u0, "output_tokens", None)
                            print("  [" + self.name + "] WARN: risposta TRONCATA (stop_reason="
                                  "max_tokens, cap " + str(MAX_TOKENS_SPECIALIST) + " token) con "
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
                            _t_call = time.perf_counter()
                            continue
                        break
                    except Exception as _e_api:
                        _sc = getattr(_e_api, "status_code", None)
                        if _sc is None and ("529" in str(_e_api)
                                            or "overloaded" in str(_e_api).lower()):
                            _sc = 529  # idioma di news_aggregator._classify_with_haiku
                        if _sc == 529 and _retry_529 < len(RETRY_529_BACKOFF_S):
                            _pausa = RETRY_529_BACKOFF_S[_retry_529]
                            _retry_529 += 1
                            print("[" + self.name + "] API 529 overloaded (iter "
                                  + str(iteration) + "): retry " + str(_retry_529)
                                  + "/" + str(len(RETRY_529_BACKOFF_S)) + " fra "
                                  + str(_pausa) + "s")
                            time.sleep(_pausa)
                            continue
                        raise
                # 12/09: il ritentativo e' finito (in un verso o nell'altro): le iterazioni
                # successive del round tornano al ragionamento di prima.
                if _thinking_prima is not None:
                    _thinking = _thinking_prima
                    _thinking_prima = None
            except Exception as e:
                print("[" + self.name + "] API error: " + str(e))
                err_txt = "[ERROR " + self.name + " round " + str(round_n) + "]: " + str(e)
                try:
                    self.blackboard.write(self.name, round_n, err_txt)
                    self.blackboard.mark_specialist_error(self.name, str(e))
                except Exception:
                    pass
                # collaudo #44: qui si usciva BUTTANDO i token gia' spesi nelle
                # iterazioni precedenti (errore al 5o giro = 4 chiamate pagate e
                # sparite dal conto). Un agente fallito che non compare nei costi e'
                # il fallback silenzioso vietato dal PM il 14/07: lo dichiariamo.
                try:
                    self.blackboard.record_usage(
                        self.name, round_n, self._model_for_round(round_n), _usage,
                        duration_s=round(time.perf_counter() - _t0, 2),
                        api_calls=iteration + _retry_529 + _retry_vuoto,
                        cache_ttl=CACHE_TTL if USE_PROMPT_CACHING else None,
                        status="api_error", retry_vuoto=_retry_vuoto)
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
                             and not _final_forced)
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
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        _tool_calls_round += 1  # fix 1b: 0 a fine round = annuncio
                        tname = block.name
                        tinput = block.input
                        print("  [" + self.name + "] -> " + tname + "(" + str(tinput)[:80] + ")")
                        result = self._execute_meta_tool(tname, tinput)
                        # Audit 11/09 (Fable 5.1): il tool_log registrava SOLO l'input; per
                        # ricostruire cosa un desk avesse letto (il «35%» dal web, il «count
                        # 0» di Polymarket) non c'era nulla. Si conserva la testa dell'esito
                        # (tetto fisso, dichiarato con `output_tappato`): e' una misura
                        # per l'audit, non un secondo canale per il modello.
                        try:
                            _out_s = json.dumps(result, default=str, ensure_ascii=False)
                        except Exception:
                            _out_s = str(result)
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
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": block.id, "content": result_str,
                        })
                messages.append({"role": "user", "content": tool_results})
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
                          "max_tokens, cap " + str(MAX_TOKENS_SPECIALIST) + " token): "
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

        # §9-bis n.5: il report forzato si DICHIARA in testa (Capo/memo/DB lo vedono)
        if final_text and _forced_report:
            # Voce 6 §9-quattuortrigies (01/08): il marcatore stava SOLO nel testo
            # del report (DB) — grep 'REPORT FORZATO' sul log dava 0 e chi legge il
            # log concludeva che non era successo. Si dichiara anche su stdout.
            print("  [" + self.name + "] REPORT FORZATO AL LIMITE ITERAZIONI ("
                  + str(MAX_TOOL_ITERS_SPECIALIST) + "): verifiche tool esaurite")
            final_text = ("[REPORT FORZATO AL LIMITE ITERAZIONI (" + str(MAX_TOOL_ITERS_SPECIALIST)
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
        if final_text and round_n >= 1 and _tool_calls_round == 0 \
                and not final_text.startswith(MARCATORE_COLLASSO):
            _marc_nt = ("[ROUND " + str(round_n) + " SENZA TOOL: nessuna chiamata tool in "
                        "questo round; i numeri non sono verificati con i tool]")
            print("  [" + self.name + "] " + _marc_nt)
            final_text = _marc_nt + "\n\n" + final_text
        if not final_text:
            # residuo possibile solo se anche il giro forzato non produce testo
            # (es. API error gestito sopra): resta l'ultimo paracadute dichiarato
            final_text = "[" + self.name + "] No output produced in round " + str(round_n)
            if _retry_vuoto:
                final_text += (" (2 tentativi: anche il ritentativo senza ragionamento e' "
                               "uscito con 0 char di testo)")

        self.blackboard.write(self.name, round_n, final_text)
        self.blackboard.mark_specialist_done(self.name)
        print("[" + self.name + "] Round " + str(round_n) + " done (" + str(len(final_text)) + " chars)")
        # collaudo #44: l'usage non si butta piu' col return — va nel usage_log del
        # blackboard (-> heartbeat -> UI). Modello EFFETTIVO del round (R0 = Sonnet,
        # R1/R2 = Opus): con self.model fisso il costo di R0 sarebbe ~5x quello vero.
        _duration_s = round(time.perf_counter() - _t0, 2)
        _entry = None
        try:
            _entry = self.blackboard.record_usage(
                self.name, round_n, self._model_for_round(round_n), _usage,
                duration_s=_duration_s, api_calls=iteration + _retry_529 + _retry_vuoto,
                cache_ttl=CACHE_TTL if USE_PROMPT_CACHING else None,
                status="usage_unknown" if _usage_unknown else "ok",
                retry_vuoto=_retry_vuoto)
        except Exception as ue:
            print("[" + self.name + "] WARN usage non registrato: " + str(ue))
        print(f"  [{self.name}] usage R{round_n}: in={_usage['in'] if _usage['in'] is not None else 'n.d.'} out={_usage['out'] if _usage['out'] is not None else 'n.d.'} "
              f"cache_read={_usage['cache_read'] if _usage['cache_read'] is not None else 'n.d.'} cache_write={_usage['cache_write'] if _usage['cache_write'] is not None else 'n.d.'} "
              f"({iteration + _retry_529 + _retry_vuoto} call API, {_duration_s}s, costo {_fmt_cost((_entry or {}).get('cost_eur'))})")
        return final_text
