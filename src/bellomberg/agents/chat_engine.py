"""
BELLOMBERG - Chat Engine v3 (Anthropic native tool_use)

Architettura tool-use loop:
1. Cliente manda messaggio user
2. Anthropic risponde con tool_use blocks
3. Noi eseguiamo i tool (chat_tools.dispatch) e re-iniettamo i result come tool_result
4. Anthropic continua il reasoning, eventualmente chiama altri tool
5. Quando smette di chiamare tool, ritorna text finale

Streaming: emettiamo eventi SSE in real-time durante il loop:
- meta            : sessione/agent/model info iniziale
- tool_use_start  : agente sta per chiamare un tool (con nome+input)
- tool_result     : risultato del tool (con _source e _timestamp)
- delta           : token text streaming dell'agente
- done            : sessione completata (tokens_in/out/cost_estimate)
- error           : errore fatale

ANTI-HALLUCINATION:
- System prompt impone "cita la fonte: chiama il tool, mai stimare"
- Ogni tool result include _source e _timestamp (l'agente li puo' citare)
- Se un tool ritorna error, l'agente DEVE dichiararlo nella risposta finale

MODELLO:
- Default Sonnet 4.6 per chat veloce
- Override per Capo: Sonnet 4.6 (per chat) - Opus resta per consigliere settimanale
"""
import asyncio
import json
import traceback
from datetime import datetime
from typing import AsyncGenerator, Optional, List, Dict, Any

from bellomberg.core.llm_client import (AsyncOpenRouterClient, modello as _modello_llm,
                        chat_max_tokens as _chat_max_tokens, somma_costo as _somma_costo,
                        ConfigurazioneLLMMancante)
from bellomberg.core.config import OPENROUTER_API_KEY, PM_DESC
from bellomberg.core.language import capture_language, prompt_for_language, scoped_language, text
from bellomberg.storage.memory_db import MemoryDB
from bellomberg.agents.chat_tools import get_tools_for_agent, dispatch

# 05/09 (ordine PM): modello e tetto della chat vivono nel .env — CHAT_<AGENTE>_MODEL con
# precedenza su CHAT_MODEL, e CHAT_MAX_TOKENS (tetto per chiamata, ragionamento incluso) —
# risolti a ogni messaggio da llm_client.modello("chat", agent_id) / chat_max_tokens();
# variabile assente = errore dichiarato al PM nello stream, mai un default qui.
# Storia: CHAT_MODEL claude-sonnet-5 dal 26/07; CHAT_MAX_TOKENS 5600 a HEAD (26/07, tarato
# su Sonnet 5) e 16000 in una riga non attribuita del tree; oggi il PM ha scritto 12000.
MAX_TOOL_ITERATIONS = 8  # cap loop tool_use per evitare run away cost

# TTL del prompt caching della chat. Costante UNICA di proposito: la usa sia la
# richiesta ad Anthropic (`_cc`, sotto) sia il calcolo del costo sull'evento
# `done`. Erano due letterali separati: se un domani qualcuno porta la richiesta
# a "5m" e dimentica il costo, il prezzo della cache_write cambia (2,00x -> 1,25x
# dell'input) e il numero in pagina mente SENZA accorgersene. Un test lo inchioda.
CHAT_CACHE_TTL = "1h"

# Anteprima della prima domanda del PM nella lista sessioni (richiesta n.3 del
# frontend, ponte 26/07 sera). Misurato sul DB vero: mediana 49 char, p90 128,
# max 358 -> a 300 se ne tronca UNA su 105. Il taglio non e' mai zitto: il
# payload porta anche `first_user_message_len`, cioe' la lunghezza VERA, quindi
# il frontend sa da solo se sta guardando un troncamento (regola PM 14/07).
FIRST_MSG_PREVIEW_CHARS = 300

# Tetto del titolo di una sessione (richiesta n.1 del frontend: 400 oltre questo).
# NB: i titoli generati da Haiku stanno sotto i 46 char per prompt; il tetto qui
# e' il contratto dell'endpoint, non la regola del generatore.
MAX_TITLE_CHARS = 120


# ============================================================
# SYSTEM PROMPTS (anti-hallucination + ruoli analitici Citadel-tier)
# ============================================================

_ANTI_HALLUCINATION = """
=== REGOLE DURE - SEMPRE RISPETTATE ===

1. **NON STIMARE NUMERI A MEMORIA.** Per OGNI cifra (prezzo, vol, VaR, beta, fundamentals, news,
   probabilita') DEVI chiamare il tool appropriato e citare la fonte.

2. **CITATION FORMAT.** Quando citi un dato da un tool, usa tag esplicito:
    "<ticker richiesto> @ <prezzo misurato> [src: get_price_live]" o "VaR 99% 1d = <misura> [src: get_portfolio_risk]"

3. **SE UN TOOL FALLISCE:** dichiaralo: "il tool X ha ritornato error: <msg>, non posso fornire
   il dato. Posso provare alternativa Y, oppure rispondere qualitativamente flaggandolo come
   non verificato".

4. **NIENTE FAKE PRECISION.** Se il dato e' stale o incerto, dillo. Mai inventare decimali.

5. **TICKER INTEGRITY.** Usa SOLO i ticker che get_portfolio_live restituisce, nel suo formato
   esatto (col suffisso di borsa): se non sei sicuro, chiamalo. Mai sostituire il titolo detenuto
   con un proxy - l'ETF americano al posto dell'UCITS, il ticker US al posto della quotazione
   locale; se usi un proxy per i DATI, dichiaralo.

6. **INTERAGIRE = OK, INVENTARE = NO.** Puoi e DEVI usare search_news/tavily_search/get_polymarket
   per trovare info di contesto qualitativo. Ma quando converti contesto in numeri, serve sempre
   il tool quantitativo dedicato.

7. **MAX TOOL ITERATIONS = 8 per sessione.** Pianifica le chiamate.

=== REGOLE DI FORMATTING MARKDOWN (CRITICHE per rendering UI) ===

L'output viene renderizzato in markdown nell'app Bellomberg. Segui questi vincoli:

A. **TABELLE COMPATTE:** Max 3-4 colonne, max 6 righe. Mai tabelle con piu' di 4 colonne.
   Sintassi corretta OBBLIGATORIA:
   ```
   | Metric | Value | Note |
   |---|---|---|
   | VaR 99% 1d | -3.2% | EUR -3,247 |
   | Sharpe | 1.12 | 6mo |
   ```
   La riga separatore `|---|---|---|` DEVE avere esattamente lo stesso numero di pipe dei header.

B. **CITATION TAG `[src: nome_tool]` SOLO ACCANTO AI NUMERI.** MAI dentro un header markdown.
   ESEMPIO CORRETTO:
   ```
    ## DATI DEL MERCATO
   Mark price $62.66 [src: get_hyperliquid_intel]
   ```
   ESEMPIO **SBAGLIATO** (causa rendering rotto):
   ```
    ## DATI DEL MERCATO [src: get_hyperliquid_intel]   <-- NO, tag dentro header
   ```

C. **NIENTE EM-DASH O EN-DASH dentro le celle.** Sostituisci '—' o '–' con '-' (trattino normale).

D. **PIPE `|` SOLO PER TABELLE.** Mai usare `|` dentro testo libero.

E. **HEADERS STRUTTURATI:** Usa `## TITOLO` per sezioni principali, `### Sottosezione` per dettagli.
   Mai header con bold tipo `**TITOLO**` - usa veri header markdown.

F. **NUMERI ALLINEATI:** Sempre 2 decimali per percentuali, sempre k/M per migliaia.

G. **NO RIPETIZIONI - REGOLA CRITICA.** UNA SOLA volta per risposta:
   - UNA sezione `## DATI` con UNA tabella (non due tabelle simili)
   - UNA sezione `## INTERPRETAZIONE` (non duplicare)
   - UNA sezione `## CAVEAT` (non duplicare)
   - UNA singola riga per metrica nella tabella (non due righe con commenti diversi)
   Se hai 2 commenti diversi per la stessa metrica, fondili in uno solo.

H. **PROFONDITA' DELLA RISPOSTA:** Adegua la lunghezza alla domanda. Una richiesta
   di analisi approfondita merita lo sviluppo completo di tesi, dati, ipotesi,
   controtesi, scenari e condizioni che cambierebbero la conclusione. Nessun tetto
   artificiale di parole o caratteri: rispetta il budget token configurato, senza
   riempitivi. Risposte brevi quando la domanda e' semplice o il PM le richiede.

I. **STRUTTURA LEGGIBILE:**
   Apri con la conclusione principale. Organizza poi dati e interpretazione in
   sezioni pertinenti al tuo dominio. Aggiungi approfondimenti, scenari e fonti
   quando servono; conserva ipotesi e limiti accanto ai risultati. Tabelle e
   paragrafi devono aiutare la lettura, senza duplicare metriche o argomentazioni.

J. **GOLDMAN SACHS GRADE - STILE OBBLIGATORIO.**
   Scrivi come un Senior Research Analyst di Goldman Sachs / Morgan Stanley / JP Morgan.
   Italiano professionale, chiaro, tecnico, con periodare ricco e connettivi logici.
   Mai telegrafico, mai anglicizzato, mai abbreviato.

   ESEMPIO DI STILE SINTETICO (simbolo, cifre e date inventati, non dati da riutilizzare):

   "Il titolo XX01.XY, a $163.91, tratta al di sotto del livello di max pain a $165 (+0.67%),
   con i dealer in posizione di long gamma vista la concentrazione di open interest sulle call
   (217k contro 144k OI put). Il setup tecnico suggerisce un pinning del prezzo intorno a $165
   nei prossimi giorni di trading, con range atteso compreso tra $160 e $168 fino alla
   scadenza del 29 maggio.

   Sulla volatilità implicita riteniamo che il pricing sia eccessivo. La IV ATM call al 73.2%,
   confrontata con una previsione GARCH del 17.5% annualizzato sullo stesso orizzonte, implica un ratio di 4.2x:
   tale spread non trova giustificazione in catalyst specifici nel prossimo orizzonte
   settimanale. Di conseguenza, lo scenario operativo preferito è la vendita di premio
   (covered call sul 170 strike, scadenza 5 giugno), non l'acquisto di optionality direzionale.

   La scelta operativa deve rispettare le strategie consentite dal mandato corrente e
   le condizioni del catalyst. Se il mandato non è disponibile, la compatibilità della
   strategia con le preferenze del PM resta non verificata [src: get_options_data]."

   ESEMPI DI OUTPUT SBAGLIATO (da evitare):

   - SBAGLIATO (telegrafico, anglicizzato):
     "XX01.XY -> below max pain 165. Dealer long gamma. IV 73% vs HV 17% = SELL premium scenario.
     Bellomberg discipline: NO long call this week."

   - SBAGLIATO (italiano scorretto, frasi spezzate):
     "XX01.XY sotto max pain. Setup pinning. IV alta. Sell premium si compra premium no.
     Niente long call."

   - SBAGLIATO (duplicazione di concetti):
     "La IV è al 73.2%. La IV ATM call è del 73.2%. Questo significa volatilità implicita alta.
     La volatilità implicita è elevata."

   REGOLE STRUTTURALI:
   - Periodi completi con soggetto + verbo + complemento, mai frasi nominali brevi
   - Connettivi logici espliciti: "tuttavia", "di conseguenza", "in particolare", "viceversa",
     "il setup suggerisce", "ne consegue che", "in tale contesto"
   - Termini tecnici inglesi restano in inglese SOLO se sono jargon di mercato consolidato:
     long, short, call, put, premium, gamma, IV, HV, OI, RSI, NAV, P/L, max pain, pinning,
     covered call, strike, breakeven, drawdown, Sharpe, beta, alpha, VaR
   - I VERBI e i CONNETTIVI sono SEMPRE in italiano ("vendita del premio", non "SELL premium";
     "scenario di acquisto", non "BUY setup")
   - Mai abbreviazioni: scrivi "annualizzato" non "ann.", "rispetto a" non "vs" nel testo libero
     (vs resta accettabile dentro tabelle)
   - Mai all-caps gratuito mid-sentence (SELL, BUY, NO) - usa il maiuscolo solo per ACTION tag
     finali tipo ACTION: BUY/SELL/HOLD
   - Mai connettori grafici tipo "->" o "=>" nel testo: usa "implica", "comporta", "porta a"
   - Tono confident-but-measured: "riteniamo che", "il setup suggerisce", "lo scenario preferito",
     non "DEVI", "OBBLIGATORIO", "SEMPRE"
   - Ogni raccomandazione operativa cita ALMENO una fonte tool: [src: get_options_data],
     [src: get_portfolio_garch], etc.
   - Ricchezza di punteggiatura: virgole per inciso, due punti per esplicitare, punti e virgola
     per coordinare clausole lunghe
""".strip()


_FRAMEWORK_CAPO = """
RUOLO: Senior Analyst PM-style (Citadel/Millennium school). Sei il Capo del team Bellomberg.

ANALYTICAL FRAMEWORK:
- Inizia con check del portfolio live (get_portfolio_live) e pending decisions (get_pending_decisions)
- Per qualunque opinione su rischio del portfolio: get_portfolio_risk + get_portfolio_garch
- Per allocation/factor view: get_portfolio_factors
- Per macro context: get_macro_dashboard
- Sintetizza CON DATI: ogni claim deve avere citation tag

OUTPUT STRUCTURE quando il PM chiede decisione:
ACTION: BUY/SELL/TRIM/ADD/HOLD
TICKER: <ticker_reale>
EUR_AMOUNT: <numero> EUR (o 'TBD if size-dependent')
TIMING: 'immediate' | 'this week' | 'next earnings' | 'on dip to X'
CONFIDENCE: high/med/low + rationale 1-line
RATIONALE: bullet 3-5 punti con citation tags

NON eseguire trade (il PM li esegue).
""".strip()


_FRAMEWORK_MACRO = """
RUOLO: Macro Strategist - scuola Soros/Druckenmiller/Tepper.

ANALYTICAL FRAMEWORK:
- "Get the macro right, then ride the tape" (Druckenmiller)
- Reflexivity: il prezzo influenza i fondamentali (Soros)
- Pensa in regimi: risk-on/risk-off, growth/recession, inflation/disinflation
- Per qualunque view macro chiama get_macro_dashboard PRIMA di parlare
- Per single indicators: get_macro_indicator
- Cita rapidamente livelli, non parlare di "alta inflazione" senza CPI YoY %

CRITICAL DATA POINTS che monitori sempre: Fed Funds, 10y, 2-10 spread (yield curve),
Broad Dollar Index (dal tool e' il FRED ~120, NON il DXY ICE ~100: etichettalo
"Broad Dollar Index (FRED)"), CPI YoY, unemployment, real rates (10y - CPI), VIX, gold, oil.
POSITIONING: get_cot_positioning (CFTC futures, estremi=contrarian) e
get_vix_term_structure (contango/backwardation) per leggere il regime di mercato.
""".strip()


_FRAMEWORK_QUANT = """
RUOLO: Quant - scuola Citadel/Millennium/Renaissance.

ANALYTICAL FRAMEWORK:
- Numeri prima, narrative dopo
- Tutte le stime con confidence interval o p-value
- Mai dire "alta correlazione" - cita il valore numerico
- Validatore del portfolio: ogni nuova idea passa per quant filter

CRITICAL TOOLS:
- get_portfolio_risk: VaR, Sharpe, Beta SPY, Max DD (DEFAULT prima call su ogni domanda)
- get_portfolio_garch: vol forecast con CI 95%
- get_portfolio_factors: alpha/beta Fama-French 5+Mom REGIONALE con t-stat
- quant_compute: VaR custom, Monte Carlo, Kelly
- compare_assets: tra ticker
- compute_gex: dealer gamma exposure + flip point (SPY/QQQ per il mercato, o single name)
- get_cot_positioning: CFTC net positioning futures con percentile 1y (estremi = contrarian)
- get_vix_term_structure: contango/backwardation come regime detector

REGOLE:
- Se Sharpe < 0 in un'analisi, segnalalo come red flag
- Confronta VaR 99% con il limite dichiarato nel mandato corrente; senza mandato, limite n.d.
- Beta SPY > 1.3 = leveraged long, flag esposizione
- Correlation > 0.85 tra 2 holdings = concentration risk
- GEX negativo + VIX in backwardation = regime momentum/stress: segnala di ridurre size
- COT leveraged funds oltre il 90° percentile = crowded trade, rischio squeeze
""".strip()


_FRAMEWORK_OPTIONS = """
RUOLO: Options Flow Specialist - scuola Citadel Equities Derivatives.

ANALYTICAL FRAMEWORK:
- Confronta IV con HV e previsione GARCH sulla stessa finestra, includendo catalyst e prezzo del rischio.
- Acquisto o vendita di premio richiedono un confronto con il fair value della volatilita'; IV alta o bassa da sola non basta.
- Dealer gamma positioning: short gamma = volatility amplification, long gamma = pinning
- Put/call ratio: <0.7 = bullish positioning, >1.2 = bearish/hedge demand
- Max pain: tendenza dei dealer a pinnare strike con max OI

DISCIPLINE BELLOMBERG:
{MANDATO:opzioni}
- Sempre cita: strike, scadenza, premio, breakeven, max loss

TOOLS PRIMARI (in quest'ordine):
1. get_option_expirations_polygon -> scegli la scadenza giusta (MAI accontentarti della nearest)
2. get_options_chain_polygon -> fonte PREMIUM: chain completa con IV, delta/gamma/theta/vega,
   open interest e volume per ogni strike. Cita sempre IV e OI degli strike che proponi.
3. get_options_data (fallback IBKR/yfinance se Polygon non risponde; ora accetta expiry)
4. get_price_live, get_portfolio_garch (per confronto IV implicita vs realized vol GARCH)
""".strip()


_FRAMEWORK_FUNDAMENTALS = """
RUOLO: Fundamentals Analyst - scuola Buffett/Ackman/Burry/Greenblatt.

ANALYTICAL FRAMEWORK:
- Owner-earnings (Buffett): FCF normalizzato - maintenance capex - SBC vero
- Moat assessment: pricing power, switching costs, network effect, scale economies
- Capital allocation track record: ROIC > WACC?, buyback vs dividends vs M&A
- Margin of safety: FV vs prezzo corrente, range non punto stima

CRITICAL TOOLS:
- get_fundamentals: PE, FCF, ROE, target, debt/equity, margins
- get_price_live: prezzo corrente + drawdown 52w
- compare_assets: vs peer set
- search_news/tavily_search: catalyst pending, management changes, accounting concerns

STILE: long-term view, intrinsic value, mai chase momentum.
""".strip()


_FRAMEWORK_CRYPTO = """
RUOLO: Crypto Specialist - scuola DEX-native + macro overlay.

ANALYTICAL FRAMEWORK:
- BTC come macro asset: correlation con DXY, real rates, gold
- Perp funding: positive = long crowded, negative = short crowded (contrarian signal)
- Open Interest spikes + funding asymmetry = squeeze setup
 - DAT dichiarate nel registro: identifica il sottostante e cita mNAV, premium/discount dai tool
 - Mercato del tool get_hyperliquid_intel: monitor on-chain flows, TVL, funding; non presumere posizioni nel token o nelle tesorerie

 TOOLS PRIMARI: get_hyperliquid_intel, get_price_live (simboli richiesti o presenti nei dati),
 search_news (flussi ETF e notizie sugli emittenti analizzati), get_macro_indicator (DGS10, DTWEXBGS).
""".strip()


_FRAMEWORK_POLITICS = """
RUOLO: Geopolitics Specialist - Polymarket-driven probabilistic.

ANALYTICAL FRAMEWORK:
- Polymarket implied probability come SOURCE OF TRUTH (mercato > opinione)
- Cross-reference con search_news per narrative shifts
- Quando implied prob diverge da consensus media -> opportunita' contrarian
- Mai dire "probabile" senza citare odds %

TOOLS PRIMARI: get_polymarket_events (FIRST CALL su ogni domanda),
get_congress_trades (Quiver: trade dichiarati dei parlamentari USA — segnale smart money
su settori/titoli, cita Representative + size + data), tavily_search per context,
search_news per catalyst recenti.

REGOLE:
- Quote sempre come "Polymarket prices X at YY% [src: get_polymarket_events]"
- Se Polymarket non ha mercato, dichiaralo: "no Polymarket coverage, fall back to qualitative"
""".strip()


_FRAMEWORK_NEWS = """
RUOLO: News/Catalyst Specialist.

ANALYTICAL FRAMEWORK:
- Catalysts ranked per Expected Value = probability x magnitude x portfolio_impact
- Timeline orientata: cosa next 24h, next week, next month?
- Cross-pollination obbligatoria con Politics agent quando rilevante (elezioni/policy)
- Filter signal vs noise: skip articoli con score sentiment debole

TOOLS PRIMARI: search_news (multi-source FIRST CALL), tavily_search,
get_polymarket_events per probabilita', get_pending_decisions per linkare a azioni Capo.

OUTPUT FORMAT:
- Top 3 catalyst questa settimana ranked
- Per ogni catalyst: TICKER impattati, direction expected, magnitude, source link
""".strip()


_FRAMEWORK_EVENTDESK = """
RUOLO: Event Desk (news + politica + prediction markets; fusione News+Politics 15/07).

ANALYTICAL FRAMEWORK:
- Catalysts ranked per Expected Value = probability x magnitude x portfolio_impact
- Probabilita' di mercato, non opinioni: Polymarket col testo della domanda e delta 7/30gg
- Edge Quiver: congress trades, lobbying, gov contracts sui nomi del book
- Timeline orientata: next 24h, next week, next month; filter signal vs noise
- Il gap tra headline e prezzo del prediction market E' il trade

TOOLS PRIMARI: search_news (multi-source FIRST CALL), get_polymarket_events,
get_congress_trades, get_earnings_calendar, tavily_search,
get_pending_decisions per linkare a azioni Capo.

OUTPUT FORMAT:
- Top 3 catalyst della settimana ranked (politici e non, UNA classifica)
- Per ogni catalyst: TICKER impattati, direction, magnitude, probabilita' se il mercato esiste, source
""".strip()


SYSTEM_PROMPTS_BASE = {
    "capo": _FRAMEWORK_CAPO,
    "macro": _FRAMEWORK_MACRO,
    "quant": _FRAMEWORK_QUANT,
    "options": _FRAMEWORK_OPTIONS,
    "fundamentals": _FRAMEWORK_FUNDAMENTALS,
    "crypto": _FRAMEWORK_CRYPTO,
    "eventdesk": _FRAMEWORK_EVENTDESK,
    # legacy pre-fusione (thread chat vecchi con agent_id politics/news):
    "politics": _FRAMEWORK_POLITICS,
    "news": _FRAMEWORK_NEWS,
}
# Alias retro-compat per import esterni (bellomberg_api.py)
SYSTEM_PROMPTS = SYSTEM_PROMPTS_BASE


AGENT_DISPLAY_NAMES = {
    "capo": "Capo", "macro": "Macro", "options": "Options Flow", "quant": "Quant",
    "fundamentals": "Fundamentals", "crypto": "Crypto", "eventdesk": "Event Desk",
    # legacy pre-fusione: risolvibili per nome (titoli di thread vecchi, tool log
    # di run passate) ma ESCLUSI dall'enumerazione UI via LEGACY_AGENT_IDS.
    "politics": "Politics (legacy)", "news": "News (legacy)",
}
LEGACY_AGENT_IDS = {"politics", "news"}


def _log(msg: str):
    # pipe stdout morta (backend zombie, autopsia (40)): log perso, mai eccezione
    try:
        print(f"[CHAT] {msg}", flush=True)
    except OSError:
        pass


_MANDATO_DA_LEGGERE = object()


def _leggi_mandato_chat():
    try:
        from bellomberg.core import mandato_pm
        return mandato_pm.carica(), None
    except Exception as e:
        return None, type(e).__name__ + ": " + str(e)


def _build_system(agent_id: str, *, mandato=_MANDATO_DA_LEGGERE, errore_mandato=None) -> str:
    """System prompt finale: framework specifico + regole anti-hallucination + linguaggio."""
    base = prompt_for_language(SYSTEM_PROMPTS_BASE.get(agent_id, ""))
    if mandato is _MANDATO_DA_LEGGERE:
        mandato, errore_mandato = _leggi_mandato_chat()
    from bellomberg.core import mandato_pm
    base = mandato_pm.compila_o_dichiara(base, mandato)
    politica = (mandato_pm.blocco_prompt(mandato) if mandato is not None
                else mandato_pm.riga_senza_mandato())
    if errore_mandato:
        politica += "\n[MANDATO n.d.] " + errore_mandato
    try:
        from bellomberg.core.current_facts import current_facts_block
        facts = current_facts_block()
    except Exception as e:
        facts = "[CONTESTO n.d.] current_facts: " + type(e).__name__ + ": " + str(e)
    return (
        f"Sei un membro del team Bellomberg, hedge fund AI. Il tuo interlocutore e' {PM_DESC}.\n"
        + text("Rispondi in italiano (jargon EN OK per termini tecnici). Sii diretto, conciso, no fluff.\n\n",
               "Answer in English. Be direct, concise, and precise.\n\n")
        + f"{facts}\n\n"
        f"{base}\n\n"
        f"{politica}\n\n"
        f"{prompt_for_language(_ANTI_HALLUCINATION)}"
    )


# ============================================================
# SESSION MGMT (invariati da v2)
# ============================================================

def list_sessions(agent_id: str, limit: int = 30) -> list[dict]:
    """Lista sessioni di un agente.

    Richiesta n.3 del frontend (ponte 26/07 sera): `first_user_message` come
    RETE per l'archivio — con quello ogni chat ha un nome sensato anche senza
    LLM e anche prima che la titolatura semantica giri. Due chiavi additive:

      first_user_message      : primi FIRST_MSG_PREVIEW_CHARS char della PRIMA
                                domanda del PM; **None** se la sessione non ha
                                messaggi user (le 12 vuote in DB) — None = "non
                                c'e' domanda", dichiarato, non stringa vuota.
      first_user_message_len  : lunghezza VERA in char, per riconoscere il taglio.

    Le due subquery sono ancorate a `role='user'` + `ORDER BY m.id ASC`: la
    prima domanda del PM, non la prima riga qualunque (le assistant sono
    interleaved sulla stessa sessione).
    """
    db = MemoryDB()
    with db._conn() as conn:
        rows = conn.execute(
            """SELECT s.id, s.specialist, s.title, s.started_at, s.last_activity, s.output_language,
                      (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) as msg_count,
                      (SELECT substr(m.content, 1, ?) FROM chat_messages m
                         WHERE m.session_id=s.id AND m.role='user'
                         ORDER BY m.id ASC LIMIT 1) as first_user_message,
                      (SELECT length(m.content) FROM chat_messages m
                         WHERE m.session_id=s.id AND m.role='user'
                         ORDER BY m.id ASC LIMIT 1) as first_user_message_len
               FROM chat_sessions s
               WHERE s.specialist=?
               ORDER BY COALESCE(s.last_activity, s.started_at) DESC
               LIMIT ?""",
            (FIRST_MSG_PREVIEW_CHARS, agent_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def create_session(agent_id: str, title: Optional[str] = None) -> int:
    from bellomberg.core.language import capture_language
    selected = capture_language()
    if agent_id not in SYSTEM_PROMPTS_BASE:
        raise ValueError(f"Unknown agent: {agent_id}")
    db = MemoryDB()
    title = title or text(f"Chat con {AGENT_DISPLAY_NAMES.get(agent_id, agent_id)}",
                          f"Chat with {AGENT_DISPLAY_NAMES.get(agent_id, agent_id)}", language=selected)
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT INTO chat_sessions(specialist, title, started_at, last_activity, output_language) "
            "VALUES (?, ?, datetime('now'), datetime('now'), ?)",
            (agent_id, title, selected)
        )
        return cur.lastrowid


def normalizza_titolo(title: str) -> str:
    """Regola UNICA per un titolo di sessione. Alza ValueError se non passa.

    Sta qui da sola perche' ha DUE chiamanti che devono non poter divergere:
    l'endpoint `PATCH /chat/sessions/{id}` e lo script di retro-titolatura
    (`retro_title_chats.py`). Se la regola vivesse in un solo posto, la
    retro-titolatura delle 74 chat storiche potrebbe scrivere in DB titoli che
    l'endpoint rifiuterebbe — e il PM se ne accorgerebbe solo rinominandone uno
    a mano, mesi dopo.
    """
    t = " ".join((title or "").split())
    if not t:
        raise ValueError(text("title vuoto: serve un titolo con almeno un carattere non-spazio",
                              "Empty title: at least one non-space character is required"))
    if len(t) > MAX_TITLE_CHARS:
        raise ValueError(
            text(f"title troppo lungo: {len(t)} caratteri, massimo {MAX_TITLE_CHARS}",
                 f"Title too long: {len(t)} characters, maximum {MAX_TITLE_CHARS}"))
    return t


def update_session_title(session_id: int, title: str) -> Optional[dict]:
    """Rinomina una sessione di chat. Richiesta n.1 del frontend, DECISA dal PM.

    Serve sia alla titolatura semantica (Haiku, al primo scambio) sia alla
    rinomina a mano dall'archivio. Verificato 26/07 sera-6 che prima non
    esisteva: `UPDATE chat_sessions SET title` = ZERO occorrenze in tutto il
    repo (l'unico UPDATE su quella tabella e' quello di `last_activity` in
    `_save_message`). Nessuna migrazione: la colonna `title` c'e' dal principio.

    Ritorna None se la sessione non esiste (l'endpoint ne fa un 404); alza
    ValueError su titolo vuoto o troppo lungo (400).

    Due scelte che vale la pena dichiarare:

    1. **NON tocca `last_activity`.** Rinominare non e' attivita'. `list_sessions`
       ordina per `COALESCE(last_activity, started_at) DESC`: se la rinomina
       bussasse la data, la retro-titolatura delle 74 chat storiche
       rimescolerebbe l'INTERO archivio del PM in un colpo solo, portando in
       cima le piu' vecchie. Il titolo cambia, il posto in lista no.
    2. **Normalizza gli spazi** (newline e tab -> spazio singolo). Un titolo e'
       una riga: un `\\n` arrivato da un LLM romperebbe la resa in lista, e il
       conteggio dei caratteri contro il tetto sarebbe fatto su roba invisibile.
    """
    t = normalizza_titolo(title)
    db = MemoryDB()
    with db._conn() as conn:
        cur = conn.execute("UPDATE chat_sessions SET title=? WHERE id=?", (t, session_id))
        if cur.rowcount == 0:
            return None
    return {"ok": True, "id": session_id, "title": t}


def get_messages(session_id: int) -> list[dict]:
    """Messaggi di una sessione, in ordine.

    Richiesta n.5 del frontend (ponte 26/07 sera): `tokens_in`/`tokens_out`
    erano gia' in tabella e valorizzati su TUTTE le righe assistant (verificato
    26/07 sera-6: 100 su 100), ma la SELECT non li restituiva e la telemetria di
    una risposta d'archivio era costretta a dire "non consegnati".

    ⚠️ SEMANTICA DI `tokens_in`, da dichiarare a chi lo rende: e' il valore
    `usage.input_tokens` dell'API, che con il prompt caching ATTIVO (e in chat
    lo e' sempre, v. CHAT_CACHE_TTL) conta **i soli token NON cachati**. I token
    serviti dalla cache e quelli scritti in cache stanno in due campi separati
    che questa tabella NON ha (sarebbe una migrazione, e le migrazioni girano
    nell'init di MemoryDB). Quindi `tokens_in` e' il RESTO non cachato, non
    l'input totale: input_totale = tokens_in + cache_read + cache_write.
    Il costo VERO della risposta viaggia sull'evento `done` (v. sotto), dove i
    due campi di cache ci sono. Chi somma i `tokens_in` d'archivio ottiene un
    SOTTO-conteggio dell'input, ed e' scritto nel payload dell'endpoint.
    """
    db = MemoryDB()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, tokens_in, tokens_out, timestamp, output_language FROM chat_messages "
            "WHERE session_id=? ORDER BY id ASC",
            (session_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_session(session_id: int) -> None:
    db = MemoryDB()
    with db._conn() as conn:
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))


def get_session_info(session_id: int) -> Optional[dict]:
    db = MemoryDB()
    with db._conn() as conn:
        r = conn.execute(
            "SELECT id, specialist, title, started_at, last_activity, output_language FROM chat_sessions WHERE id=?",
            (session_id,)
        ).fetchone()
        return dict(r) if r else None


def _save_message(session_id: int, role: str, content: str,
                   tokens_in: Optional[int] = None, tokens_out: Optional[int] = None) -> None:
    from bellomberg.core.language import capture_language
    selected = capture_language() if role == "assistant" else None
    db = MemoryDB()
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages(session_id, role, content, tokens_in, tokens_out, timestamp, output_language) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'), ?)",
            (session_id, role, content, tokens_in, tokens_out, selected)
        )
        conn.execute(
            "UPDATE chat_sessions SET last_activity=datetime('now') WHERE id=?",
            (session_id,)
        )


def _costo_risposta(model: Optional[str], tokens_in: int, tokens_out: int,
                    cache_read: Optional[int], cache_write: Optional[int],
                    cost_usd: Optional[float] = None) -> dict:
    """Costo in EUR di una risposta di chat, o il BUCO dichiarato (regola 14/07).

    Pass-through fedele di `llm_pricing.cost_eur` (chiavi cost/status/model/
    breakdown/fx_rate/fx_source, come chiesto dal frontend nella n.6), piu' tre
    chiavi nostre che dicono quanto fidarsi del numero:

      cache_ttl    : il TTL con cui il costo e' stato prezzato (= quello della
                     richiesta, per costruzione: CHAT_CACHE_TTL e' una sola).
      cache_fields : "ok" se l'API ha consegnato cache_read/cache_write su OGNI
                     iterazione del tool loop, "assenti" altrimenti.
      nota         : perche' il numero e' incompleto, quando lo e'.

    ⚠️ Con `cache_fields="assenti"` il costo e' un **SOTTO**-conteggio, non un
    tetto: `usage.input_tokens` esclude gia' i token cachati, quindi mancano sia
    la lettura di cache (0,10x l'input) sia — soprattutto — la SCRITTURA di
    cache, che a TTL 1h si paga **2,00x**. Misurato su una risposta vera
    (12.936 in / 1.751 out, Sonnet 5): 0,038 € senza i due campi contro
    0,050 € con. Il frontend, nella richiesta n.6, lo aveva letto al contrario
    ("il numero e' un tetto"): e' scritto nel ponte.
    """
    base = {"cost": None, "status": None, "model": model, "breakdown": None,
            "fx_rate": None, "fx_source": None, "cache_ttl": CHAT_CACHE_TTL,
            "cache_fields": "ok" if (cache_read is not None and cache_write is not None)
                            else "assenti",
            "nota": None}
    if not model:
        base["status"] = "non_calcolato: modello non risolto (uscita anticipata)"
        return base
    try:
        from bellomberg.core.llm_pricing import cost_eur
        usage = {"in": tokens_in, "out": tokens_out,
                 "cache_read": cache_read, "cache_write": cache_write}
        if cost_usd is not None:
            usage["cost_usd"] = float(cost_usd)   # 05/09: la misura di OpenRouter, se consegnata
        if cache_read is not None and cache_write is not None:
            usage["cache_read"] = int(cache_read)
            usage["cache_write"] = int(cache_write)
        else:
            base["nota"] = ("cache_read/cache_write non consegnati su almeno "
                            "un'iterazione: token parziali. Costo utilizzabile solo "
                            "se consegnato dal provider per tutte le chiamate.")
        c = cost_eur(model, usage, cache_ttl=CHAT_CACHE_TTL)
        base.update({k: c.get(k) for k in
                     ("cost", "status", "model", "breakdown", "fx_rate", "fx_source")})
        # Review pre-commit 26/07 sera-6 (finding ALTA): `cost_eur` lascia
        # status="ok" anche quando l'FX USD->EUR non si risolve e il costo esce
        # None (llm_pricing.py:291-296, fx_source="n.d."). Un frontend che fa
        # `if (status === 'ok') fmt(cost)` va in errore il primo giorno che
        # Yahoo e' giu'. "ok" sopra un buco e' esattamente il fallback zitto
        # che la regola 14/07 vieta: qui l'etichetta si corregge.
        if base["cost"] is None and base["status"] == "ok":
            base["status"] = (f"non_calcolato: cambio USD->EUR non disponibile "
                              f"(fx_source={base.get('fx_source')!r})")
    except Exception as e:
        # buco DICHIARATO: mai un costo 0,00 spacciato per misura
        base["status"] = f"errore: {type(e).__name__}: {e}"
    return base


def _done_payload(session_id: int, *, ok: bool, model: Optional[str] = None,
                  tokens_in: int = 0, tokens_out: int = 0,
                  cache_read: Optional[int] = None, cache_write: Optional[int] = None,
                  iterations: int = 0, usage_visto: bool = False,
                  stream_failed: bool = False, cost_usd: Optional[float] = None) -> dict:
    """Forma UNICA dell'evento `done`, uguale su tutte le uscite.

    Prima ce n'erano due: le tre uscite anticipate (agente sconosciuto, API key
    mancante, errore DB in scrittura) emettevano `{tokens_in, tokens_out,
    session_id}` — **senza `ok`** — e quella normale `{ok, tokens_in,
    tokens_out, iterations}` — senza `session_id`. Cioe' proprio nei tre casi in
    cui qualcosa e' andato storto il frontend non trovava la chiave che dice se
    e' andato storto, e `cost_eur` sarebbe sparito li'. Chiave sempre presente,
    valore null dichiarato (pattern (25)).

    `usage_visto` distingue TRE stati che altrimenti collassavano tutti su
    "0,00 € misurati" (finding MEDIA della review pre-commit 26/07 sera-6):

      usage_visto=True   -> l'API ha consegnato le metriche: costo VERO.
      True + stream_failed -> B.12 dossier 24 (ALTA, review 01/08): il flag
                            e' STICKY, quindi una morte a iterazione >=2
                            arrivava qui col ramo "costo VERO" e i totali
                            fermi alle sole iterazioni COMPLETE — status
                            'ok' su un minimo, e il contratto frontend
                            (`if status==='ok'`) lo benediva come misura.
                            Ora: il costo si calcola lo stesso (la spesa
                            nota e gia' avvenuta non si cancella) ma esce
                            `status: parziale…` con la nota che lo spiega.
      False, iterations=0 -> non abbiamo mai contattato il modello (agente
                            sconosciuto, API key assente, errore DB in
                            scrittura): lo zero e' un fatto, ma si etichetta
                            `nessuna_chiamata` invece di spacciarlo per misura
                            — e cosi' non si tocca nemmeno la rete per l'FX.
      False, iterations>0 -> lo stream e' morto DOPO aver bruciato token ma
                            prima di `get_final_message()`: i token consumati
                            NON sono noti. Qui i totali escono `None` e il
                            costo e' un buco dichiarato. Prima usciva
                            `0,00 € status:"ok" cache_fields:"ok"` su una
                            risposta che al PM era gia' costata.
    """
    if usage_visto:
        costo = _costo_risposta(model, tokens_in, tokens_out, cache_read, cache_write, cost_usd)
        if stream_failed:
            if costo.get("status") == "ok":
                costo["status"] = (f"parziale: stream interrotto (iterazione "
                                   f"{iterations}) — costo e token contano solo "
                                   f"le iterazioni complete")
            _sp = ("i token dell'iterazione morta non sono contati: "
                   "numeri = MINIMO, non misura")
            costo["nota"] = _sp if not costo.get("nota") else f"{costo['nota']} · {_sp}"
    else:
        costo = {"cost": None, "status": None, "model": model, "breakdown": None,
                 "fx_rate": None, "fx_source": None, "cache_ttl": CHAT_CACHE_TTL,
                 "cache_fields": "assenti", "nota": None}
        if iterations == 0:
            costo["cost"] = 0.0
            costo["status"] = "nessuna_chiamata: uscita prima di contattare il modello"
        else:
            tokens_in = tokens_out = None
            cache_read = cache_write = None
            costo["status"] = ("non_calcolato: stream interrotto prima delle metriche "
                               "di usage — i token consumati NON sono noti")
    return {
        "ok": ok,
        "session_id": session_id,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "iterations": iterations,
        "tokens_missing": [k for k, v in (("in", tokens_in), ("out", tokens_out),
                                            ("cache_read", cache_read), ("cache_write", cache_write)) if v is None],
        "tokens_status": "parziale" if any(v is None for v in (tokens_in, tokens_out, cache_read, cache_write)) else "completo",
        "cost_eur": costo,
    }


def _tokens_da_archiviare(usage_visto: bool, stream_failed: bool,
                          tokens_in: int, tokens_out: int):
    """In colonna vanno MISURE, non minimi (B.12 dossier 24): su stream morto
    a iterazione >=2 i totali contano solo le iterazioni complete e in
    chat_messages non c'e' una colonna-marcatore che li distingua da una
    misura piena — quindi NULL (= n.d. dichiarato), la stessa convenzione
    gia' certificata per la morte pre-prima-iterazione. Il minimo resta
    visibile nell'evento done, etichettato `parziale`."""
    if usage_visto and not stream_failed:
        return tokens_in, tokens_out
    return None, None


def _format_sse(event: str, data) -> str:
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False, default=str)
    if "\n" in data:
        data_lines = "\n".join(f"data: {line}" for line in data.split("\n"))
        return f"event: {event}\n{data_lines}\n\n"
    return f"event: {event}\ndata: {data}\n\n"


# ============================================================
# STREAMING CHAT con tool_use loop
# ============================================================

@scoped_language
async def stream_chat(session_id: int, agent_id: str, user_message: str) -> AsyncGenerator[str, None]:
    """Stream chat con tool_use loop nativo Anthropic.

    Eventi SSE emessi: meta, tool_use_start, tool_result, delta, done, error
    """
    _log(f"START session={session_id} agent={agent_id} msg={user_message[:60]!r}")

    # Padding iniziale anti-buffering Electron
    yield ":" + (" " * 2048) + "\n\n"

    if agent_id not in SYSTEM_PROMPTS_BASE:
        yield _format_sse("error", {"message": text(f"Agente sconosciuto {agent_id}", f"Unknown agent {agent_id}")})
        yield _format_sse("done", _done_payload(session_id, ok=False))
        return

    if not OPENROUTER_API_KEY:
        _log("OPENROUTER_API_KEY missing")
        yield _format_sse("error", {"message": text("OPENROUTER_API_KEY mancante nel .env", "OPENROUTER_API_KEY missing in .env")})
        yield _format_sse("done", _done_payload(session_id, ok=False))
        return

    try:
        model_name = _modello_llm("chat", agent_id)
        _max_tokens_chat = _chat_max_tokens()
    except ConfigurazioneLLMMancante as e:
        _log(str(e))
        yield _format_sse("error", {"message": str(e)})
        yield _format_sse("done", _done_payload(session_id, ok=False))
        return

    # Save user msg
    try:
        _save_message(session_id, "user", user_message)
    except Exception as e:
        yield _format_sse("error", {"message": f"DB error: {e}"})
        yield _format_sse("done", _done_payload(session_id, ok=False, model=model_name))
        return

    # History
    msgs_db = get_messages(session_id)
    # api_messages preserva alternanza user/assistant
    api_messages: List[Dict[str, Any]] = []
    for m in msgs_db:
        if m["role"] in ("user", "assistant") and m.get("content"):
            api_messages.append({"role": m["role"], "content": m["content"]})
    if not api_messages or api_messages[-1]["role"] != "user":
        api_messages.append({"role": "user", "content": user_message})

    mandato_chat, errore_mandato = _leggi_mandato_chat()
    system_text = _build_system(agent_id, mandato=mandato_chat, errore_mandato=errore_mandato)
    tools = get_tools_for_agent(agent_id)

    _log(f"calling OpenRouter model={model_name} msgs={len(api_messages)} "
         f"sys_len={len(system_text)} tools_available={len(tools)}")

    # Meta
    yield _format_sse("meta", {
        "output_language": capture_language(),
        "agent_id": agent_id,
        "session_id": session_id,
        "model": model_name,
        "n_tools_available": len(tools),
        "max_tool_iterations": MAX_TOOL_ITERATIONS,
        "mandato": ({"impronta": __import__('bellomberg.core.mandato_pm', fromlist=('mandato_pm',)).impronta(mandato_chat),
                      "dichiarato_il": mandato_chat.get("dichiarato_il"),
                      "origine": mandato_chat.get("origine")} if mandato_chat is not None else None),
    })

    client = AsyncOpenRouterClient()
    final_text_parts: List[str] = []
    tokens_in_total = 0
    tokens_out_total = 0
    # 05/09: costo consegnato da OpenRouter, sommato sulle iterazioni; None se una
    # iterazione non lo porta (buco dichiarato: llm_pricing ricade sul listino di casa).
    cost_usd_total = 0.0
    # n.6 (ponte 26/07 sera): senza questi due il costo NON e' calcolabile.
    # `cache_ok` scende a False alla PRIMA iterazione che non li consegna e non
    # risale piu': un totale meta' vero sarebbe peggio di un buco dichiarato.
    cache_read_total = 0
    cache_write_total = 0
    cache_ok = True
    # alzato SOLO dopo `get_final_message()`: finche' e' False i totali qui
    # sopra sono zeri iniziali, non una misura (v. _done_payload).
    usage_visto = False
    iteration = 0
    tool_calls_made = 0
    stream_failed = False  # audit/11 §4: una run morta a meta' non deve chiudersi ok:true
    response_incomplete = False

    try:
        while iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            _log(f"loop iter={iteration} api_messages_len={len(api_messages)}")

            # Streaming call
            assistant_blocks: List[Dict[str, Any]] = []
            current_text_buffer = ""
            current_tool_use: Optional[Dict[str, Any]] = None
            current_tool_json_buffer = ""

            # 200a: prompt caching - in chat system+tools vengono re-inviati a OGNI messaggio
            # e a ogni iterazione tool: cache read 0.1x. Stessa convenzione di specialists/base.py.
            _cc = {"type": "ephemeral", "ttl": CHAT_CACHE_TTL}
            _ctools = list(tools)
            if _ctools:
                _ctools[-1] = {**_ctools[-1], "cache_control": _cc}
            _csystem = [{"type": "text", "text": system_text, "cache_control": _cc}]
            final_iteration = iteration == MAX_TOOL_ITERATIONS
            final_options = {"tool_choice": {"type": "none"}} if final_iteration else {}
            if final_iteration:
                api_messages.append({"role": "user", "content":
                    "Limite verifiche raggiunto: tool disabilitati. Rispondi ora coi dati raccolti e dichiara quelli mancanti."})
            async with client.messages.stream(
                model=model_name,
                max_tokens=_max_tokens_chat,
                # 05/09 (ordine PM «accendiamo anche il ragionamento»): ACCESO. Prima
                # (26/07) era SPENTO esplicito per preservare il tetto; la sonda dal vivo
                # ha misurato che GLM-5.3-flash e Gemini RIFIUTANO lo spento (HTTP 400
                # «Reasoning is mandatory»). llm_client traduce: su z-ai/ nessun parametro
                # (l'unico modo di accenderlo), altrove effort medium; i token di
                # ragionamento contano in CHAT_MAX_TOKENS (.env) e in usage.reasoning_tokens.
                thinking={"type": "adaptive"},
                system=_csystem,
                tools=_ctools,
                messages=api_messages,
                extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
                **final_options,
            ) as stream:
                async for event in stream:
                    et = getattr(event, "type", None)

                    if et == "content_block_start":
                        block = event.content_block
                        if block.type == "text":
                            current_text_buffer = ""
                        elif block.type == "tool_use":
                            current_tool_use = {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": {},
                            }
                            current_tool_json_buffer = ""
                            tool_calls_made += 1
                            yield _format_sse("tool_use_start", {
                                "tool_name": block.name,
                                "tool_id": block.id,
                                "iteration": iteration,
                            })

                    elif et == "content_block_delta":
                        delta = event.delta
                        dt = getattr(delta, "type", None)
                        if dt == "text_delta":
                            current_text_buffer += delta.text
                            final_text_parts.append(delta.text)
                            yield _format_sse("delta", {"text": delta.text})
                        elif dt == "input_json_delta":
                            current_tool_json_buffer += delta.partial_json

                    elif et == "content_block_stop":
                        # Finalize current block
                        if current_tool_use is not None:
                            try:
                                current_tool_use["input"] = (
                                    json.loads(current_tool_json_buffer) if current_tool_json_buffer else {}
                                )
                            except Exception:
                                # audit/11 §5: input illeggibile marcato — il dispatch NON
                                # deve girare coi default (ES, MSTR...) di un'altra richiesta
                                current_tool_use["input"] = {"__input_parse_error__": True}
                            assistant_blocks.append(current_tool_use)
                            current_tool_use = None
                            current_tool_json_buffer = ""
                        elif current_text_buffer:
                            assistant_blocks.append({
                                "type": "text",
                                "text": current_text_buffer,
                            })
                            current_text_buffer = ""

                    # audit/11 §4: niente accumulo su message_delta (usage.output_tokens
                    # e' CUMULATIVO nel messaggio: il += moltiplicava il conteggio, e poi
                    # veniva comunque sovrascritto col valore della SOLA ultima iterazione)

                # Get final message metadata
                final = await stream.get_final_message()
                # OpenRouter can interleave tool deltas. The adapter's final message
                # reconstructs each tool by index; the display buffers above cannot.
                assistant_blocks = []
                for block in final.content:
                    if block.type == "text":
                        assistant_blocks.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_blocks.append({"type": "tool_use", "id": block.id,
                                                 "name": block.name, "input": block.input})
                usage_visto = True
                from bellomberg.core.llm_client import somma_usage
                _tot = somma_usage({"in": tokens_in_total, "out": tokens_out_total,
                                    "cache_read": cache_read_total, "cache_write": cache_write_total,
                                    "cost_usd": cost_usd_total}, getattr(final, "usage", None))
                tokens_in_total, tokens_out_total = _tot["in"], _tot["out"]
                cache_read_total, cache_write_total = _tot["cache_read"], _tot["cache_write"]
                cache_ok = cache_read_total is not None and cache_write_total is not None
                cost_usd_total = _tot["cost_usd"]
                stop_reason = final.stop_reason

            _log(f"iter={iteration} stop_reason={stop_reason} blocks={len(assistant_blocks)} "
                 f"tool_calls_made_total={tool_calls_made}")

            # Append assistant message to history
            api_messages.append({"role": "assistant", "content": assistant_blocks})

            # If no tool_use, we're done
            if stop_reason != "tool_use":
                _log(f"FINAL response received, stop_reason={stop_reason}")
                if stop_reason != "end_turn":
                    from bellomberg.core.llm_refusal import refusal_reason
                    reason = refusal_reason(final, "CHAT") or (
                        text("RISPOSTA TRONCATA: raggiunto il limite token", "TRUNCATED RESPONSE: token limit reached") if stop_reason == "max_tokens"
                        else text("RISPOSTA INCOMPLETA: stop_reason=", "INCOMPLETE RESPONSE: stop_reason=") + str(stop_reason))
                    response_incomplete = True
                    marker = "\n\n[" + reason + "]"
                    final_text_parts.append(marker)
                    yield _format_sse("delta", {"text": marker})
                    yield _format_sse("error", {"message": reason})
                break
            if final_iteration:
                response_incomplete = True
                reason = text("Limite iterazioni raggiunto senza risposta finale; ulteriori tool non eseguiti",
                              "Iteration limit reached without a final response; no further tools executed")
                marker = "\n\n[" + reason + "]"
                final_text_parts.append(marker)
                yield _format_sse("delta", {"text": marker})
                yield _format_sse("error", {"message": reason})
                break

            # Otherwise: execute tools and re-inject results
            tool_results_blocks: List[Dict[str, Any]] = []
            for block in assistant_blocks:
                if block.get("type") != "tool_use":
                    continue
                tname = block["name"]
                tinput = block["input"]
                tid = block["id"]
                _log(f"  dispatching tool {tname} with input keys={list(tinput.keys())}")
                if isinstance(tinput, dict) and tinput.get("__input_parse_error__"):
                    # audit/11 §5: input illeggibile -> NIENTE dispatch coi default
                    # (get_cot_positioning 'ES', get_dat_metrics 'MSTR', MC senza input)
                    result = {
                        "_source": f"dispatcher.{tname}",
                        "_timestamp": datetime.now().isoformat(timespec="seconds"),
                        "error": "input JSON del tool_use malformato/troncato: chiamata "
                                 "NON eseguita coi default, riprova specificando i parametri",
                    }
                else:
                    try:
                        # V6 Lotto 3 (review B4): attribuzione fine nel registro
                        # guidance — la chat e' il PM che parla col desk
                        result = await asyncio.to_thread(dispatch, tname, tinput, caller="chat:" + agent_id)
                    except Exception as e:
                        result = {
                            "_source": f"dispatcher.{tname}",
                            "_timestamp": datetime.now().isoformat(timespec="seconds"),
                            "error": f"dispatch exception {type(e).__name__}: {e}",
                        }
                # Stream tool_result event al frontend (formato compatto - solo size info)
                result_str = json.dumps(result, default=str, ensure_ascii=False)
                # Il payload timbrato mette data prima di error: i primi 300 caratteri
                # nascondevano la causa del KO Polymarket dietro query/note/risultati.
                preview_str = result_str
                if "error" in result:
                    preview_str = json.dumps({"_source": result.get("_source"),
                                              "error": result["error"]}, ensure_ascii=False, default=str)
                elif isinstance(result.get("data"), dict) and result["data"].get("fetch_warnings"):
                    preview_str = json.dumps({"_source": result.get("_source"), "status": "partial",
                                              "fetch_warnings": result["data"]["fetch_warnings"]},
                                             ensure_ascii=False, default=str)
                preview = preview_str[:300] + ("..." if len(preview_str) > 300 else "")
                yield _format_sse("tool_result", {
                    "tool_name": tname,
                    "tool_id": tid,
                    "ok": "error" not in result,
                    "result_size_bytes": len(result_str),
                    "result_preview": preview,
                })
                # Truncate huge tool results to keep context manageable
                result_for_llm = result
                if len(result_str) > 12000:
                    result_for_llm = {
                        "_source": result.get("_source"),
                        "_timestamp": result.get("_timestamp"),
                        "truncated": True,
                        "preview": result_str[:8000],
                        "note": f"Output truncated from {len(result_str)} bytes.",
                    }
                llm_content = json.dumps(result_for_llm, default=str, ensure_ascii=False)
                if tname == "get_valuation" and isinstance(result, dict):
                    # Come nel comitato: il dossier non deve nascondere FV, gate
                    # e buchi al modello. Preview SSE e payload persistito invariati.
                    from bellomberg.valuation.sector_analysis import valuation_results_block
                    payload = result.get("data") if isinstance(result.get("data"), dict) else result
                    llm_content = (valuation_results_block({str(tinput.get("ticker") or "n.d."): payload})
                        + "\n\nDettaglio sotto, soggetto al tetto tool_result; "
                          "snapshot integrale nel sidecar se generato:\n" + llm_content)
                tool_results_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tid,
                    # audit/11 §5: stessi parametri della misura (ensure_ascii=False) —
                    # prima gli accenti diventavano \uXXXX e il taglio era incoerente
                    "content": llm_content[:12000],
                })
            api_messages.append({"role": "user", "content": tool_results_blocks})

    except Exception as e:
        err_msg = str(e)
        _log(f"stream_chat error: {err_msg}")
        _log(traceback.format_exc())
        stream_failed = True
        yield _format_sse("error", {"message": err_msg})

    full_text = "".join(final_text_parts)
    if stream_failed and full_text.strip():
        # audit/11 §4: il testo parziale non deve entrare in history come analisi completa
        full_text += text("\n\n[RISPOSTA INTERROTTA DA ERRORE — analisi potenzialmente incompleta]",
                          "\n\n[RESPONSE INTERRUPTED BY ERROR — analysis may be incomplete]")
    if full_text.strip():
        try:
            # se l'usage non e' mai arrivato — o e' PARZIALE per stream morto
            # a iterazione >=2 (B.12) — in colonna va NULL (= "non consegnati")
            # e non un numero: la n.5 ora QUEL numero lo mostra al PM, e un
            # parziale salvato sarebbe una misura falsa per sempre in archivio.
            _tin, _tout = _tokens_da_archiviare(usage_visto, stream_failed,
                                                tokens_in_total, tokens_out_total)
            _save_message(session_id, "assistant", full_text,
                           tokens_in=_tin, tokens_out=_tout)
        except Exception as e:
            _log(f"save_message failed: {e}")

    # `_done_payload` -> llm_pricing.cost_eur -> price_updater.get_fx_to_eur ->
    # yfinance: rete SINCRONA. Qui siamo dentro un async generator servito da
    # StreamingResponse, cioe' sull'EVENT LOOP: eseguirla inline congela l'INTERO
    # backend per la durata della chiamata (misurato 0,55 s a freddo dalla review
    # pre-commit 26/07 sera-6 — e quanto dura un hang di Yahoo, se Yahoo appende).
    # to_thread la sposta fuori dal loop.
    payload = await asyncio.to_thread(
        _done_payload,
        session_id,
        ok=not (stream_failed or response_incomplete),
        model=model_name,
        tokens_in=tokens_in_total,
        tokens_out=tokens_out_total,
        cache_read=cache_read_total if cache_ok else None,
        cache_write=cache_write_total if cache_ok else None,
        iterations=iteration,
        usage_visto=usage_visto,
        stream_failed=stream_failed,
        cost_usd=cost_usd_total,
    )
    yield _format_sse("done", payload)
