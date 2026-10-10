"""
BELLOMBERG - News Aggregator

Pulla news multi-fonte per ticker singolo, lista ticker portfolio, o query libera:
- NewsAPI (newsapi.org)
- Marketaux (marketaux.com)
- TheNewsAPI (thenewsapi.com)
- GNews (gnews.io)
- yfinance news (per ticker)
- Finnhub company news (per simbolo fonte risolto)
- RSS feeds (Bloomberg, FT, Reuters, WSJ)

PRINCIPI:
1. Cache 15 min per (query, sources) per ridurre API calls.
2. Deduplica articoli per URL + title hash.
3. Sentiment classification opzionale via Claude Haiku (compact + cheap).
4. Output strutturato: title, source, url, published_at, snippet, ticker_mentioned, sentiment.

API:
  search_news_for_ticker(ticker, days=3, max_per_source=5) -> list[dict]
  search_news_global(query, days=3, max_per_source=5) -> list[dict]
  search_portfolio_news(days=2, max_per_ticker=3) -> dict {ticker: [news]}
  get_top_catalysts(days=7, limit=20) -> list[dict]

TERMINI DI RICERCA (04/09, criterio (1)): vivono nel negozio PRIVATO
data/news_search_terms.json (v. carica_termini); lo schema tracciato e'
news_search_terms.example.json. Una voce assente usa il nome emittente Yahoo; per i
provider a simbolo un alias Finnhub esplicito vince sulla scoperta USA univoca per nome.
Nessun simbolo del book vive in questo sorgente.
"""
from bellomberg.core.paths import DATA_DIR, PROJECT_ROOT
from bellomberg.core.language import capture_language, scoped_language, text as _lt
from bellomberg.core.presentation import error_text, message, render_payload
from pathlib import Path
import tempfile
import threading
import time
import re
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
import os
import json

try:
    import requests
    REQ_OK = True
except Exception:
    REQ_OK = False

try:
    import feedparser
    FEEDPARSER_OK = True
except Exception:
    FEEDPARSER_OK = False

try:
    import yfinance as yf
    YF_OK = True
except Exception:
    YF_OK = False

from bellomberg.storage.memory_db import MemoryDB, connect_sqlite, DB_DIR

CACHE_TTL_SEC = 900  # 15 min
_CACHE: Dict[str, Any] = {}
# Errori dei provider non gestiti dal limiter giornaliero, per provider e simbolo:
# {provider: {target: (timestamp, motivo)}}. Scadono insieme alla cache delle news.
_RUNTIME_PROVIDER_FAILURES: Dict[str, Dict[str, tuple[float, str]]] = {}

# 202-A: il modulo congela le chiavi all'import -> garantisci il .env caricato QUI,
# qualunque sia l'ordine di import del processo (API, .bat, script standalone).
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

NEWSAPI_KEY = os.environ.get("NEWS_API_KEY", "")
MARKETAUX_KEY = os.environ.get("MARKETAUX_API_KEY", "")
THENEWSAPI_KEY = os.environ.get("THENEWSAPI_API_KEY", "")
GNEWS_KEY = os.environ.get("GNEWS_API_KEY", "")

# RSS feeds: Bloomberg + FT + Reuters + WSJ + CNBC (core) + extra da news_topics
RSS_FEEDS = {
    # Core finance
    "Bloomberg Markets": "https://feeds.bloomberg.com/markets/news.rss",
    "FT Markets":        "https://www.ft.com/markets?format=rss",
    "ANSA Economia":     "https://www.ansa.it/sito/notizie/economia/economia_rss.xml",  # 199e: feeds.reuters.com e' morto; ANSA copre la stampa economica italiana
    # 25/07/26 (41): Dow Jones ha chiuso gli RSS pubblici — Barron's =
    # feeds.dowjones.io NXDOMAIN (dominio sparito), WSJ Markets = 200 ma
    # CONGELATO al 27/01/25 (probe col fetch vero: 20 item, max gen 2025 —
    # "funzionava" e contribuiva 0 news in silenzio). Sostituiti con 2 fonti
    # probate fresche in giornata (SA Currents max 15:30, Yahoo max 15:02).
    "CNBC Top News":     "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    # Extra markets (free, no key)
    "MarketWatch Top":   "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Investing.com":     "https://www.investing.com/rss/news.rss",
    "SA Market Currents": "https://seekingalpha.com/market_currents.xml",
    "Yahoo Finance":     "https://finance.yahoo.com/news/rssindex",
    # Crypto
    "CoinDesk":          "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph":     "https://cointelegraph.com/rss",
    # Macro / EU institutional
    "ECB Press":         "https://www.ecb.europa.eu/rss/press.html",
    # Italia
    "Sole24Ore Mercati": "https://www.ilsole24ore.com/rss/mercati.xml",
    "Repubblica Econ":   "https://www.repubblica.it/rss/economia/rss2.0.xml",
    # Geopolitica
    "Al Jazeera":        "https://www.aljazeera.com/xml/rss/all.xml",
}


def _eccezione_sicura(exc: BaseException) -> str:
    """G3 (04/10): nei log e negli stati via API solo tipo + stato HTTP. Il messaggio grezzo
    di requests contiene l'URL con la querystring, cioe' la chiave API (apiKey/api_token/token)."""
    from bellomberg.core.errori_sicuri import descrivi_eccezione
    return descrivi_eccezione(exc)


def _log(msg: str):
    # pipe stdout morta (backend zombie, autopsia (40)): log perso, mai eccezione
    try:
        print(f"[NEWS] {msg}", flush=True)
    except OSError:
        pass


# --- deduplica per TITOLO (04/10, B2 — rilievo RV-N P2-2, via libera main) ---
# _dedupe usa l'url: gli url Finnhub sono redirect finnhub.io (misura M1: 3.466/3.466),
# quindi lo stesso articolo da Tiingo/yfinance (url diretto) e da Finnhub passava due
# volte -> doppia classificazione Haiku e doppia notifica desktop (M1: 165/333 notifiche
# Tiingo per-ticker con un gemello Finnhub). Titolo normalizzato = minuscole, punteggiatura
# tolta, spazi compressi; stesso titolo entro FINESTRA_DOPPIONI_S = doppione. Si tiene
# l'item con l'URL DIRETTO. Data illeggibile: il confronto vale sul solo titolo (dichiarato).
FINESTRA_DOPPIONI_S = 24 * 3600
_RE_NON_ALFANUM = re.compile(r"[\W_]+", re.UNICODE)
_DOPPIONI_GIRO: Dict[str, int] = {"titolo": 0, "titolo_db": 0}


def _titolo_norm(titolo: str) -> str:
    return " ".join(_RE_NON_ALFANUM.sub(" ", (titolo or "").lower()).split())


def _url_redirect(url: str) -> bool:
    """URL che non identificano l'articolo (redirect del fornitore): la dedupe per url li manca."""
    return "finnhub.io" in (url or "").lower()


def _dedupe_titoli(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Toglie i doppioni per titolo normalizzato entro FINESTRA_DOPPIONI_S, tenendo l'item con
    URL diretto (a parita', il primo). L'ordine degli item tenuti resta quello d'ingresso.
    Gli scarti si contano in _DOPPIONI_GIRO["titolo"]."""
    gruppi: Dict[str, List[int]] = {}
    for i, it in enumerate(items):
        t = _titolo_norm(it.get("title", ""))
        if t:
            gruppi.setdefault(t, []).append(i)
    via = set()
    for idx in gruppi.values():
        if len(idx) < 2:
            continue
        # i diretti prima: ognuno «assorbe» i doppioni nella finestra
        ordine = sorted(idx, key=lambda i: (_url_redirect(items[i].get("url", "")), i))
        tenuti: List[int] = []
        for i in ordine:
            ts_i = _parse_date_ts(items[i].get("published_at", ""))
            gemello = False
            for k in tenuti:
                ts_k = _parse_date_ts(items[k].get("published_at", ""))
                if not ts_i or not ts_k or abs(ts_i - ts_k) <= FINESTRA_DOPPIONI_S:
                    gemello = True
                    break
            if gemello:
                via.add(i)
            else:
                tenuti.append(i)
    if via:
        with _ESITI_LOCK:
            _DOPPIONI_GIRO["titolo"] += len(via)
    return [it for i, it in enumerate(items) if i not in via]


def _azzera_doppioni_giro() -> None:
    with _ESITI_LOCK:
        _DOPPIONI_GIRO["titolo"] = 0
        _DOPPIONI_GIRO["titolo_db"] = 0


def _doppioni_giro() -> Dict[str, int]:
    with _ESITI_LOCK:
        return dict(_DOPPIONI_GIRO)


def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplica per url + hash title normalizzato."""
    seen = set()
    out = []
    for it in items:
        key = it.get("url") or hashlib.md5((it.get("title", "") or "").lower().strip().encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# --- Rate limiter GENERICO per i provider news free-tier 100 req/day ---
# Estende il fix #172 (Marketaux) a NewsAPI, TheNewsAPI e GNews: budget
# giornaliero, cooldown per query, auto-disable su quota esaurita.
# Stato su file: gli scheduler sono processi separati ogni 15 minuti.
_NEWS_RATE_PATH = str(DATA_DIR / "news_rate_state.json")
# 02/10/2026 (dosaggio): con i giri ogni 15 minuti NewsAPI e TheNewsAPI finivano il budget
# in serata e restavano mute fino a mezzanotte. "pace" = finestra oraria locale (inizio, fine)
# in cui il budget si sblocca a rate: una piccola scorta all'inizio, poi cresce lineare fino
# al tetto alla fine della finestra. Oltre la quota del momento: SKIP_PACING (rimandata).
NEWS_PACE_BURST = 0.12   # quota disponibile subito all'inizio della finestra (12% del tetto)
NEWS_PROVIDER_LIMITS = {
    "newsapi":    {"daily": 99, "cooldown": 6 * 3600, "disable": 12 * 3600, "pace": (6, 23)},
    "thenewsapi": {"daily": 80, "cooldown": 6 * 3600, "disable": 12 * 3600, "pace": (6, 23)},
    # Opus 4.8 16/07: GNews su piano ESSENTIAL (verificato live: max=25 ok, news di 12
    # min fa, storico attivo, stessa key). Tetto 80->800 (20% sotto il cap di piano 1000):
    # GNews non si spegne piu' a meta' mattina. Cooldown 6h->2h per SFRUTTARE il real-time:
    # ogni query si aggiorna ogni 2h invece di 6h (~360 chiamate/g stimate, sotto 800).
    # NewsAPI sale a 99 (margine di 1 sul piano Developer da 100); TheNewsAPI resta a 80.
    "gnews":      {"daily": 800, "cooldown": 2 * 3600, "disable": 12 * 3600},
}

# Opus 4.8 16/07: Essential concede 25 articoli/richiesta (free: 10). GNews e' real-time,
# quindi i suoi articoli sono i piu' recenti e vincono l'ordinamento per data (:588) nel
# feed: gli chiediamo un bacino ampio SOLO a lui, senza gonfiare le altre fonti (tiingo,
# 87% del rumore, resta a max_per_source). Cap del piano: 25.
GNEWS_MAX_ART = 25

# 10/10 (Opus 5.5): il registro dei cooldown per-query teneva solo le 60 query piu' recenti.
# Misura del 10/10 (data/news_rate_state.json): gnews aveva ESATTAMENTE 60 voci, cioe' il
# giro (posizioni + preferiti + .MI in italiano + 8 temi) gia' toccava il tetto. Ogni query
# oltre la 60-esima spingeva fuori la piu' vecchia, che al giro dopo (15 min) non risultava
# piu' in cooldown e si ripagava: il cooldown di 2h diventava 15 min per le query sfrattate.
# Con le query GNews nuove di questa correzione (.DE in tedesco, top-headlines) si sarebbe
# sfondato: tetto a 200 (il file resta di pochi KB).
MAX_QUERY_COOLDOWN = 200


def _news_rate_state() -> Dict[str, Any]:
    try:
        import json as _json
        with open(_NEWS_RATE_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {}


def _news_rate_save(st: Dict[str, Any]) -> None:
    try:
        import json as _json
        os.makedirs(os.path.dirname(_NEWS_RATE_PATH), exist_ok=True)
        with open(_NEWS_RATE_PATH, "w", encoding="utf-8") as f:
            _json.dump(st, f)
    except Exception:
        pass


def _adesso() -> datetime:
    """Ora locale del limiter (punto unico, cosi' le prove possono fissarla)."""
    return datetime.now()


def _pace_bounds(lim: Dict[str, Any], when: datetime):
    start_h, end_h = lim["pace"]
    start = when.replace(hour=start_h, minute=0, second=0, microsecond=0)
    end = when.replace(hour=end_h, minute=0, second=0, microsecond=0) if end_h < 24 else \
        when.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return start, end


def pacing_allowance(lim: Dict[str, Any], when: datetime) -> Optional[int]:
    """Chiamate consentite DA INIZIO GIORNATA fino a `when`, per un provider con "pace";
    None se il provider non e' dosato. Prima della finestra vale la sola scorta iniziale,
    dopo la fine il tetto giornaliero."""
    if not lim.get("pace"):
        return None
    daily = int(lim["daily"])
    burst = max(1, int(round(daily * NEWS_PACE_BURST)))
    start, end = _pace_bounds(lim, when)
    frac = (when - start).total_seconds() / max(1.0, (end - start).total_seconds())
    frac = min(1.0, max(0.0, frac))
    return min(daily, int(burst + (daily - burst) * frac))


def pacing_next_slot(lim: Dict[str, Any], used: int, when: datetime) -> Optional[datetime]:
    """Quando la quota del momento supera `used` (la prossima chiamata possibile), oppure
    None se e' gia' possibile o se il tetto giornaliero e' raggiunto."""
    allowed = pacing_allowance(lim, when)
    daily = int(lim["daily"])
    if allowed is None or used < allowed or used >= daily:
        return None
    burst = max(1, int(round(daily * NEWS_PACE_BURST)))
    start, end = _pace_bounds(lim, when)
    frac = (used + 1 - burst) / max(1, daily - burst)
    return start + (end - start) * min(1.0, max(0.0, frac))


def provider_status(provider: str, query: str) -> Optional[str]:
    """Opus 4.8 15/07 (P0 skip dichiarato): il MOTIVO per cui il provider e' bloccato,
    o None se la chiamata puo' partire. Il bool di _provider_allowed collassava 3 cause
    diverse in un unico False, e il chiamante restituiva [] muto: 'zero notizie' e 'sono
    cieco' erano lo stesso valore. Vocabolario CHIUSO (come llm_pricing 'live|fallback|
    n.d.'), niente 'ok' inventato: qui si dichiara solo cio' che il limiter SA.
      SKIP_DISABLED — auto-disable dopo un 429/402/403 (:175-177), scade da solo
      SKIP_BUDGET   — budget giornaliero esaurito (il ramo che scatta oggi: 99/99)
      SKIP_PACING   — quota del momento esaurita (dosaggio 02/10/2026): il budget si sblocca
                      a rate nella finestra "pace"; riparte da solo, non spegne il provider
      SKIP_COOLDOWN — questa QUERY e' gia' stata fatta da meno di `cooldown` (per-query,
                      non per-provider: per questo la firma prende anche `query`)
    None NON significa "andra' bene": significa solo "il limiter non blocca". L'esito vero
    della chiamata lo sa solo il fetcher (vedi news_sources.last_status). E i provider
    fuori da NEWS_PROVIDER_LIMITS (marketaux, tiingo, yfinance, rss) tornano None perche'
    nessuno li conta: fail-open, non "sano".
    """
    lim = NEWS_PROVIDER_LIMITS.get(provider)
    if not lim:
        return None
    st = _news_rate_state().get(provider, {})
    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    if now < float(st.get("disabled_until", 0)):
        return "SKIP_DISABLED"
    if st.get("day") == today and int(st.get("count", 0)) >= lim["daily"]:
        return "SKIP_BUDGET"
    allowed = pacing_allowance(lim, _adesso())
    if allowed is not None and st.get("day") == today and int(st.get("count", 0)) >= allowed:
        return "SKIP_PACING"
    q_ts = float(st.get("per_query", {}).get(query.lower().strip()[:80], 0))
    if now - q_ts < lim["cooldown"]:
        return "SKIP_COOLDOWN"
    return None


def providers_budget() -> Dict[str, Dict[str, Any]]:
    """Consumo del budget per i provider contingentati, solo dallo stato su file (zero rete):
    usate oggi, tetto, quota del momento se dosato, e quando si libera la prossima chiamata."""
    state = _news_rate_state()
    now = _adesso()
    today = now.strftime("%Y-%m-%d")
    out: Dict[str, Dict[str, Any]] = {}
    for p, lim in NEWS_PROVIDER_LIMITS.items():
        st = state.get(p, {})
        used = int(st.get("count", 0)) if st.get("day") == today else 0
        allowed = pacing_allowance(lim, now)
        nxt = pacing_next_slot(lim, used, now) if allowed is not None else None
        out[p] = {"used": used, "daily": int(lim["daily"]), "allowed_now": allowed,
                  "pace": list(lim["pace"]) if lim.get("pace") else None,
                  "next_call_at": nxt.isoformat(timespec="minutes") if nxt else None}
    return out


def _provider_allowed(provider: str, query: str) -> bool:
    # Firma invariata di proposito: 5 call site vivi la usano come bool (:185, :315,
    # :347 + news_sources via _rate_limiter). Il motivo si chiede a provider_status().
    return provider_status(provider, query) is None


def _runtime_failure_record(provider: str, target: str, motivo: str) -> None:
    _RUNTIME_PROVIDER_FAILURES.setdefault(provider, {})[target] = (time.time(), motivo)


def _runtime_failure_clear(provider: str, target: str) -> None:
    per_target = _RUNTIME_PROVIDER_FAILURES.get(provider)
    if not per_target:
        return
    per_target.pop(target, None)
    if not per_target:
        _RUNTIME_PROVIDER_FAILURES.pop(provider, None)


def _runtime_failures_correnti() -> Dict[str, str]:
    """Guasti recenti aggregati senza far cancellare un ticker dal successo di un altro."""
    now = time.time()
    fuori: Dict[str, str] = {}
    for provider, per_target in list(_RUNTIME_PROVIDER_FAILURES.items()):
        for target, (ts, _motivo) in list(per_target.items()):
            if now - ts > CACHE_TTL_SEC:
                per_target.pop(target, None)
        if not per_target:
            _RUNTIME_PROVIDER_FAILURES.pop(provider, None)
            continue
        fuori[provider] = "; ".join(
            f"{target}: {motivo}" for target, (_ts, motivo) in sorted(per_target.items()))
    return fuori


def providers_blocked() -> Dict[str, str]:
    """Opus 4.8 15/07 (P0): i provider spenti per una causa GLOBALE (budget esaurito o
    auto-disable), cioe' che non risponderanno per NESSUNA query. Serve al log del feed,
    che oggi scrive 'OK fetched=76' mentre 3 provider su 3 sono muti dalle 08:36 e i 76
    item arrivano tutti da RSS/yfinance/tiingo.
    Il cooldown per-query resta fuori di proposito: e' per-query, non spegne il provider.
    """
    fuori = {}
    _probe = "__probe_stato_provider__"  # query mai usata: neutralizza il ramo cooldown
    chiavi = _chiavi_provider()
    for p in NEWS_PROVIDER_LIMITS:
        # 05/09 (chat ba, trovato da b6 sul clone senza chiavi): un provider SENZA CHIAVE non
        # rispondera' per nessuna query, esattamente come uno a budget esaurito — ma era
        # dichiarato solo nel log, e le rotte rispondevano «0 news, fonti_mute null». Il motivo
        # nomina la variabile del .env: chi clona sa cosa mettere. La chiave assente vince sul
        # budget (senza chiave non ha mai consumato nulla).
        var, valore = chiavi.get(p, (None, "presente"))
        if var and not valore:
            # 13/09: il codice resta in testa (i lettori fanno startswith), la frase ha le due
            # lingue fino al confine HTTP: prima era una str italiana anche con la UI in inglese
            fuori[p] = message("SENZA_CHIAVE: {var} assente nel .env", "SENZA_CHIAVE: {var} missing from .env",
                               var=var)
            continue
        st = provider_status(p, _probe)
        if st in ("SKIP_BUDGET", "SKIP_DISABLED"):
            fuori[p] = st
    fuori.update(_runtime_failures_correnti())
    # Tiingo: chiave dedicata, fuori dai limiti giornalieri (tiingo_news.tiingo_available)
    # 04/10 (B2, Opus 5.5): con la chiave presente si guarda anche l'ESITO dell'ultima
    # chiamata (tiingo_news.last_status): un 401 di abbonamento scaduto era invisibile
    # (la parola «tiingo» compariva 0 volte nel news_feed.log). «Mai interrogata» = nessuna voce.
    tiingo_ultimo = None
    tiingo_spenta = False
    try:
        from bellomberg.market_data.tiingo_news import tiingo_available, last_status, tiingo_spenta as _spenta
        tiingo_spenta = _spenta()
        if tiingo_spenta:
            pass  # 10/10: SPENTA per decisione PM -> fonti_spente(), non una fonte muta/guasta
        elif not tiingo_available():
            fuori["tiingo"] = message("SENZA_CHIAVE: TIINGO_API_KEY assente nel .env (o requests non importabile)",
                                      "SENZA_CHIAVE: TIINGO_API_KEY missing from .env (or requests cannot be imported)")
        else:
            tiingo_ultimo = last_status()
    except Exception as e:
        fuori["tiingo"] = message("MODULO_ASSENTE: tiingo_news non importabile ({kind})",
                                  "MODULO_ASSENTE: tiingo_news cannot be imported ({kind})", kind=type(e).__name__)
    if "tiingo" not in fuori and not tiingo_spenta:
        muta = _esito_muto_recente("tiingo", "Tiingo", tiingo_ultimo)
        if muta:
            fuori["tiingo"] = muta
    muta = _esito_muto_recente("finnhub", "Finnhub")
    if muta:
        fuori["finnhub"] = muta
    # il negozio dei termini: senza, i nomi europei si cercano col ticker nudo (dichiarato nel
    # log, ma il payload diceva solo «0 news»): e' una fonte muta del giro, e qui lo dice.
    # 13/09: niente % — riduceva a str il motivo (e con lui le sue due lingue)
    c = carica_termini()
    if c["origine"] in ("assente", "illeggibile"):
        fuori[TERMINI_MUTI] = message("NEGOZIO_{origine}: {motivo}", "NEGOZIO_{origine}: {motivo}",
                                      origine=c["origine"].upper(), motivo=c["motivo"])
    # il negozio dei titoli per tema: senza, NESSUNA notizia porta i titoli che tocca, e il
    # payload direbbe solo «ecco le news» — il modello leggerebbe l'assenza di legami come
    # «nessuno dei tuoi titoli e' toccato», che e' una frase diversa e falsa
    # l'import e' PROTETTO come quello di tiingo qui sopra, e non per simmetria: questa
    # funzione la chiamano `chat_tools` e cinque endpoint dell'API, quindi un'eccezione qui
    # trasformerebbe «dichiara il buco» in un 500 — il ripiego muto dentro il codice scritto
    # per impedirlo. Un modulo che non si importa e' a sua volta una fonte muta, e si dice.
    try:
        from bellomberg.storage.negozi_privati import carica_temi_titoli
        tt = carica_temi_titoli()
        if tt["origine"] in ("assente", "illeggibile"):
            fuori[TEMI_TITOLI_MUTI] = message("NEGOZIO_{origine}: {motivo}", "NEGOZIO_{origine}: {motivo}",
                                              origine=tt["origine"].upper(), motivo=tt["motivo"])
    except Exception as e:
        fuori[TEMI_TITOLI_MUTI] = message("MODULO_ASSENTE: negozi_privati non importabile ({kind}: {cause})",
                                          "MODULO_ASSENTE: negozi_privati cannot be imported ({kind}: {cause})",
                                          kind=type(e).__name__, cause=error_text(e))
    return fuori


# ── Esiti delle fonti NON contingentate (04/10, B2 — Opus 5.5) ──────────────────
# Tiingo e Finnhub non passano dal rate-limiter su file: il loro guasto (401, 429,
# timeout) era inghiottito da `except Exception: pass`. Qui si tiene l'esito
# dell'ULTIMA chiamata per fonte, in QUESTO processo (il feed gira dentro il backend:
# POST /news/feed/refresh, quindi le rotte vedono lo stesso stato). L'eta' si misura su
# time.monotonic (immune ai salti NTP); `quando` e' solo un'etichetta. Un esito non-live
# piu' vecchio di TTL_ESITO_FONTE_S non spegne piu' la fonte: nessuno l'ha riprovata, e
# dichiararla muta per sempre sarebbe un'altra bugia. Limite: un processo nuovo parte
# da «mai interrogata» (nessuna voce) fino alla prima chiamata.
TTL_ESITO_FONTE_S = 3600
_ESITI_LOCK = threading.Lock()
_ESITI_FONTI: Dict[str, Dict[str, Any]] = {}
# la chiave di quasi tutti i provider viaggia in querystring: mai nei log ne' nei payload.
# Integrazione 04/10: UN solo ripulitore, core/errori_sicuri.senza_segreti (G3): toglie la
# querystring degli URL, ogni parametro-chiave (token/apikey/api_key/api_token/key/...) e i
# valori delle chiavi presenti nell'ambiente.
def _maschera_chiavi(testo: str) -> str:
    from bellomberg.core.errori_sicuri import senza_segreti
    return senza_segreti(testo or "")


# 04/10 (B2, rilievo RV-N P2-1): l'ultimo esito da solo NASCONDE il guasto parziale (429 su
# 2 ticker su 3, poi un 200: «live»). Nel giro si contano TUTTE le chiamate per fonte.
# Azzerato a inizio auto_pull_feed; limite: conta anche le chiamate di altre rotte dello
# stesso processo che cadono durante il giro (non distinguibili senza un id di giro).
_ESITI_GIRO: Dict[str, Counter] = {}


def _conta_nel_giro(provider: str, stato: str) -> None:
    with _ESITI_LOCK:
        _ESITI_GIRO.setdefault(provider, Counter())[stato] += 1


def _azzera_esiti_giro() -> None:
    with _ESITI_LOCK:
        _ESITI_GIRO.clear()


def _registra_esito(provider: str, stato: str, motivo: str = "") -> None:
    """Esito dell'ultima chiamata a `provider` (anche `live`: un 200 dopo un 401 guarisce),
    e conteggio della chiamata nel giro corrente."""
    voce = {"stato": stato, "motivo": _maschera_chiavi(motivo)[:200],
            "quando": datetime.now().isoformat(timespec="seconds"), "mono": time.monotonic()}
    with _ESITI_LOCK:
        _ESITI_FONTI[provider] = voce
    _conta_nel_giro(provider, stato)


def esiti_fonti() -> Dict[str, Dict[str, Any]]:
    """{provider: {stato, motivo, quando, mono}} — COPIA; provider assente = mai interrogato."""
    with _ESITI_LOCK:
        return {p: dict(v) for p, v in _ESITI_FONTI.items()}


def reset_esiti_fonti() -> None:
    """Riporta tutte le fonti a «mai interrogata» (per i test)."""
    with _ESITI_LOCK:
        _ESITI_FONTI.clear()


def fonti_spente() -> Dict[str, str]:
    """Fonti tolte PER DECISIONE (non guaste): {fonte: motivo}, codice SPENTA in testa.
    Il codice non le interroga piu' in nessun punto; si dichiarano perche' chi legge il
    giro o le rotte non scambi la loro assenza per «zero notizie».
    10/10 (Opus 5.5): Tiingo News spenta per decisione PM (abbonamento non rinnovato) finche'
    `tiingo_news.FONTE_SPENTA` e' vero; riaccesa (TIINGO_NEWS_ENABLED=1) esce da qui e torna
    misurata come le altre. Modulo non importabile: lo dichiara providers_blocked()."""
    out = {"reddit": message("SPENTA: Reddit tolto dalle fonti (decisione PM 04/10)",
                             "SPENTA: Reddit removed from sources (PM decision 04/10)")}
    try:
        from bellomberg.market_data import tiingo_news
        if tiingo_news.tiingo_spenta():
            out["tiingo"] = message("SPENTA: Tiingo News — {motivo}", "SPENTA: Tiingo News — {motivo_en}",
                                    motivo=tiingo_news.MOTIVO_SPENTA, motivo_en=tiingo_news.MOTIVO_SPENTA_EN)
    except Exception:
        pass  # MODULO_ASSENTE: dichiarato da providers_blocked(), non qui
    return out


def _esito_piu_recente(provider: str, esterno: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Il piu' recente fra l'esito del modulo della fonte (es. tiingo_news.last_status) e
    quello registrato qui dal wrapper (guasti che il modulo non vede: import, eccezioni)."""
    voci = [v for v in (esterno, esiti_fonti().get(provider))
            if isinstance(v, dict) and isinstance(v.get("mono"), (int, float))]
    return max(voci, key=lambda v: v["mono"]) if voci else None


def _esito_muto_recente(provider: str, nome: str, esterno: Optional[Dict[str, Any]] = None,
                        ora_mono: Optional[float] = None) -> Optional[str]:
    """Motivo (codice IN TESTA: i lettori fanno startswith) se l'ultimo esito e' non-live e
    piu' giovane di TTL_ESITO_FONTE_S; None se mai interrogata, live, o esito scaduto."""
    v = _esito_piu_recente(provider, esterno)
    if v is None or v.get("stato") == "live":
        return None
    eta = (time.monotonic() if ora_mono is None else ora_mono) - float(v["mono"])
    if eta >= TTL_ESITO_FONTE_S:
        return None
    dettaglio = v.get("motivo") or ""
    if not dettaglio and v.get("http") and str(v.get("stato") or "").startswith("HTTP_"):
        dettaglio = "HTTP %s on %s" % (v.get("http"), v.get("path") or "")
    ora = str(v.get("quando") or "n.d.")
    ora = ora[11:16] if len(ora) >= 16 else ora
    if v.get("stato") == "VUOTO_SOSPETTO":
        # tiingo_news (B1): N chiamate generali consecutive a 200 con 0 articoli — risposta
        # riuscita ma sospetta (abbonamento/piano scaduto?), non un errore di rete
        return message("VUOTO_SOSPETTO: {nome} risponde 200 ma senza articoli sul feed generale "
                       "(ultima alle {ora}, {min} min fa): abbonamento/piano da verificare",
                       "VUOTO_SOSPETTO: {nome} answers 200 but with no articles on the general feed "
                       "(last at {ora}, {min} min ago): check the subscription/plan",
                       nome=nome, ora=ora, min=int(max(eta, 0) // 60))
    return message("{stato}: ultima chiamata {nome} non riuscita alle {ora} ({min} min fa){det}",
                   "{stato}: last {nome} call failed at {ora} ({min} min ago){det}",
                   stato=str(v.get("stato") or "ERRORE"), nome=nome, ora=ora,
                   min=int(max(eta, 0) // 60), det=(": " + dettaglio) if dettaglio else "")


def _esito_nel_giro(provider: str) -> str:
    """Stato della fonte in QUESTO giro, su TUTTE le sue chiamate (contatore azzerato a inizio
    giro): nessuna -> non_interrogata; tutte uguali -> quello stato; miste -> «PARZIALE: live
    1/3, HTTP_429 2/3» (codice in testa, conta come guasto per `degraded`)."""
    with _ESITI_LOCK:
        c = Counter(_ESITI_GIRO.get(provider) or {})
    tot = sum(c.values())
    if not tot:
        return "non_interrogata"
    if len(c) == 1:
        return next(iter(c))
    return "PARZIALE: " + ", ".join("%s %d/%d" % (st, n, tot)
                                    for st, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def _tiingo_ultimo_stato():
    """(last_status() | None, stato di guasto dell'import | None) — mai un'eccezione."""
    try:
        from bellomberg.market_data.tiingo_news import last_status
        return last_status(), None
    except Exception as e:
        return None, "MODULO_ASSENTE_" + type(e).__name__


def _chiavi_provider() -> Dict[str, tuple]:
    """provider contingentato -> (variabile del .env, valore letto ORA dal modulo): letto a
    ogni chiamata, cosi' le prove possono svuotare una chiave e misurare la dichiarazione."""
    return {"newsapi": ("NEWS_API_KEY", NEWSAPI_KEY),
            "thenewsapi": ("THENEWSAPI_API_KEY", THENEWSAPI_KEY),
            "gnews": ("GNEWS_API_KEY", GNEWS_KEY)}


def _provider_record(provider: str, query: str, got_429: bool = False) -> None:
    lim = NEWS_PROVIDER_LIMITS.get(provider)
    if not lim:
        return
    full = _news_rate_state()
    st = full.get(provider, {})
    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"] = today
        st["count"] = 0
    st["count"] = int(st.get("count", 0)) + 1
    pq = st.get("per_query", {})
    pq[query.lower().strip()[:80]] = now
    if len(pq) > MAX_QUERY_COOLDOWN:
        pq = dict(sorted(pq.items(), key=lambda kv: kv[1], reverse=True)[:MAX_QUERY_COOLDOWN])
    st["per_query"] = pq
    if got_429:
        st["disabled_until"] = now + lim["disable"]
        _log(f"{provider} quota/rate limit — disabilitato 12h")
    full[provider] = st
    _news_rate_save(full)


def _fetch_newsapi(query: str, days: int = 3, max_results: int = 10) -> List[Dict[str, Any]]:
    if not (REQ_OK and NEWSAPI_KEY):
        return []
    if not _provider_allowed("newsapi", query):
        return []
    try:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query,
            "from": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"),
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": max_results,
            "apiKey": NEWSAPI_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        _provider_record("newsapi", query, got_429=r.status_code in (402, 426, 429))
        if r.status_code != 200:
            return []
        data = r.json()
        return [{
            "title": a.get("title", ""),
            "source": a.get("source", {}).get("name", "NewsAPI"),
            "url": a.get("url", ""),
            "published_at": a.get("publishedAt", ""),
            "snippet": (a.get("description") or a.get("content") or "")[:300],  # audit/11: NewsAPI manda content=null ('[Removed]') -> None[:300] uccideva l'intero batch
            "provider": "newsapi",
        } for a in data.get("articles", [])[:max_results]]
    except Exception as e:
        _log(f"newsapi failed: {_eccezione_sicura(e)}")
        return []


# --- bugfix #172: Marketaux rate limiter (free tier 100 req/day) ---
# Stato persistito su file: gli scheduler girano come processi separati,
# quindi un limiter in-memory si resetterebbe ad ogni run.
_MARKETAUX_STATE_PATH = str(DATA_DIR / "marketaux_state.json")
MARKETAUX_QUERY_COOLDOWN_SEC = 6 * 3600   # stessa query max 1 volta ogni 6h
MARKETAUX_DAILY_BUDGET = 80               # cap richieste/giorno (margine vs limite 100)
MARKETAUX_DISABLE_429_SEC = 12 * 3600     # dopo 429/402: disabilita 12h


def _marketaux_state() -> Dict[str, Any]:
    try:
        import json as _json
        with open(_MARKETAUX_STATE_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {}


def _marketaux_save(st: Dict[str, Any]) -> None:
    try:
        import json as _json
        os.makedirs(os.path.dirname(_MARKETAUX_STATE_PATH), exist_ok=True)
        with open(_MARKETAUX_STATE_PATH, "w", encoding="utf-8") as f:
            _json.dump(st, f)
    except Exception:
        pass


def _marketaux_allowed(query: str) -> bool:
    st = _marketaux_state()
    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    if now < float(st.get("disabled_until", 0)):
        return False
    if st.get("day") == today and int(st.get("count", 0)) >= MARKETAUX_DAILY_BUDGET:
        return False
    q_ts = float(st.get("per_query", {}).get(query.lower().strip()[:80], 0))
    if now - q_ts < MARKETAUX_QUERY_COOLDOWN_SEC:
        return False
    return True


def _marketaux_record(query: str, got_429: bool = False) -> None:
    st = _marketaux_state()
    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"] = today
        st["count"] = 0
    st["count"] = int(st.get("count", 0)) + 1
    pq = st.get("per_query", {})
    pq[query.lower().strip()[:80]] = now
    if len(pq) > 60:  # non far crescere il file all'infinito
        pq = dict(sorted(pq.items(), key=lambda kv: kv[1], reverse=True)[:60])
    st["per_query"] = pq
    if got_429:
        st["disabled_until"] = now + MARKETAUX_DISABLE_429_SEC
    _marketaux_save(st)


def _fetch_marketaux(query: str, days: int = 3, max_results: int = 10) -> List[Dict[str, Any]]:
    if not (REQ_OK and MARKETAUX_KEY):
        return []
    if not _marketaux_allowed(query):
        return []  # rate limited (bugfix #172) — silenzioso, usa le altre fonti
    try:
        url = "https://api.marketaux.com/v1/news/all"
        params = {
            "search": query,
            "filter_entities": "true",
            "published_after": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M"),
            "language": "en",
            "limit": max_results,
            "api_token": MARKETAUX_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        _marketaux_record(query, got_429=r.status_code in (402, 429))
        if r.status_code in (402, 429):
            _log(f"marketaux quota/rate limit HTTP {r.status_code} — disabilitato 12h (#172)")
            return []
        if r.status_code != 200:
            return []
        data = r.json()
        return [{
            "title": a.get("title", ""),
            "source": a.get("source", "Marketaux"),
            "url": a.get("url", ""),
            "published_at": a.get("published_at", ""),
            "snippet": a.get("description") or a.get("snippet", ""),
            "provider": "marketaux",
        } for a in data.get("data", [])[:max_results]]
    except Exception as e:
        _log(f"marketaux failed: {_eccezione_sicura(e)}")
        return []


def _fetch_thenewsapi(query: str, days: int = 3, max_results: int = 10) -> List[Dict[str, Any]]:
    if not (REQ_OK and THENEWSAPI_KEY):
        return []
    if not _provider_allowed("thenewsapi", query):
        return []
    try:
        url = "https://api.thenewsapi.com/v1/news/all"
        params = {
            "search": query,
            "published_after": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"),
            "language": "en",
            "limit": max_results,
            "api_token": THENEWSAPI_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        _provider_record("thenewsapi", query, got_429=r.status_code in (402, 429))
        if r.status_code != 200:
            return []
        data = r.json()
        return [{
            "title": a.get("title", ""),
            "source": a.get("source", "TheNewsAPI"),
            "url": a.get("url", ""),
            "published_at": a.get("published_at", ""),
            "snippet": a.get("description", ""),
            "provider": "thenewsapi",
        } for a in data.get("data", [])[:max_results]]
    except Exception as e:
        _log(f"thenewsapi failed: {_eccezione_sicura(e)}")
        return []


def _chiave_limiter_gnews(query: str, lang: str = "en") -> str:
    """Chiave di cooldown/record GNews. 04/10 (B2): senza la lingua, la chiamata `it` per i
    .MI usava la chiave della `en` appena registrata -> SKIP_COOLDOWN sempre: la stampa
    italiana via GNews non partiva MAI. La lingua va IN TESTA: provider_status taglia la
    query a 80 caratteri e in coda sparirebbe. `en` resta la query nuda (stessa chiave di
    news_sources, che chiama solo in inglese)."""
    return query if lang == "en" else f"[lang={lang}] {query}"


def _mappa_gnews(articoli: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{
        "title": a.get("title", ""),
        "source": (a.get("source") or {}).get("name", "GNews"),
        "url": a.get("url", ""),
        "published_at": a.get("publishedAt", ""),
        "snippet": a.get("description", ""),
        "provider": "gnews",
    } for a in articoli]


def _gnews_da_cache(query: str, days: int, max_results: int, lang: str) -> List[Dict[str, Any]]:
    """Query in cooldown: gli articoli dell'ultima risposta vera (gnews_cache, condivisa con
    news_sources) invece di un [] muto. Cache assente/scaduta/illeggibile: dichiarato nel log."""
    try:
        from bellomberg.market_data import gnews_cache
        voce = gnews_cache.leggi(query, lang)
    except Exception as e:
        _log(f"gnews SKIP_COOLDOWN e cache MODULO_ASSENTE ({type(e).__name__}): 0 articoli GNews per questa query")
        return []
    if not voce:
        err = None
        try:
            err = gnews_cache.stato_ultimo_errore()
        except Exception:
            err = "stato_ultimo_errore non leggibile"
        _log("gnews SKIP_COOLDOWN e nessuna cache valida%s: 0 articoli GNews per questa query"
             % (f" (cache: {err})" if err else ""))
        return []
    soglia = datetime.now(timezone.utc) - timedelta(days=days)
    tenuti = []
    for a in voce.get("articles") or []:
        try:
            pub = datetime.fromisoformat(str(a.get("publishedAt") or "").replace("Z", "+00:00"))
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            if pub < soglia:
                continue
        except ValueError:
            pass  # data illeggibile: l'articolo resta, il taglio per data non si puo' misurare
        tenuti.append(a)
    out = _mappa_gnews(tenuti[:max_results])
    for it in out:
        it["_gnews_cache_eta_s"] = voce.get("eta_s")
    return out


def _fetch_gnews(query: str, days: int = 3, max_results: int = 10, lang: str = "en") -> List[Dict[str, Any]]:
    if not (REQ_OK and GNEWS_KEY):
        return []
    q_lim = _chiave_limiter_gnews(query, lang)
    blocco = provider_status("gnews", q_lim)
    if blocco == "SKIP_COOLDOWN":
        return _gnews_da_cache(query, days, max_results, lang)
    if blocco is not None:
        return []  # SKIP_BUDGET/SKIP_DISABLED: globali, dichiarati da providers_blocked()
    try:
        from bellomberg.market_data.news_sources import gnews_safe_query  # quotatura token con -/$/. (400 syntax, diagnosi 15/07)
        url = "https://gnews.io/api/v4/search"
        params = {
            "q": gnews_safe_query(query),
            "from": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lang": lang,
            # 10/10 (probe live, docs.gnews.io/openapi.yaml): campi e ordine ESPLICITI. Sono i
            # default dell'API oggi, ma il ranking a valle presume «i piu' recenti nei campi
            # che il lettore vede»: un cambio di default non deve cambiarci il feed in silenzio.
            # publishedAt e non relevance: con cooldown 2h e ~3 articoli/h sul nome piu' coperto
            # (il nome piu' coperto del book: 204 in 3 giorni) i 25 piu' recenti coprono TUTTA
            # la finestra; relevance (stesso insieme, +4 punti di qualita') ne salterebbe di freschi.
            "in": "title,description",
            "sortby": "publishedAt",
            "max": max_results,
            "apikey": GNEWS_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        _provider_record("gnews", q_lim, got_429=r.status_code in (403, 429))
        if r.status_code != 200:
            _log(f"gnews HTTP {r.status_code}: {_maschera_chiavi(r.text[:200])}")
            return []
        data = r.json()
        articoli = data.get("articles", []) or []
        try:
            from bellomberg.market_data import gnews_cache
            gnews_cache.scrivi(query, lang, articoli)
        except Exception as e:
            _log(f"gnews cache NON scritta (MODULO_ASSENTE o guasto: {type(e).__name__}): "
                 f"il prossimo cooldown di questa query restera' senza articoli")
        return _mappa_gnews(articoli[:max_results])
    except Exception as e:
        _log(f"gnews failed: {_eccezione_sicura(e)}")
        return []


# 10/10 (Opus 5.5, ordine PM «notizie importanti per tutto»): il feed generale di mercato
# era Tiingo (spento). Al suo posto le TOP HEADLINES business di GNews: la classifica di
# Google News delle notizie del momento (probe live 10/10: 25 articoli, CNBC/Yahoo/CBS/NBC/
# TechCrunch...). country=us e lang=en sono i parametri MISURATI nella probe. Stesso limiter
# (cooldown 2h -> 12 chiamate/giorno al massimo) e stessa cache condivisa delle ricerche.
GNEWS_TOP_CATEGORIA = "business"
GNEWS_TOP_PAESE = "us"
GNEWS_TOP_LINGUA = "en"


def _chiave_gnews_top(categoria: str = GNEWS_TOP_CATEGORIA, paese: str = GNEWS_TOP_PAESE) -> str:
    """Chiave di limiter/cache: tra parentesi quadre, non collide con nessuna query di ricerca."""
    return f"[top-headlines category={categoria} country={paese}]"


def _fetch_gnews_top(max_results: int = 10, days: int = 1) -> List[Dict[str, Any]]:
    """Top headlines business GNews. Stessa disciplina di _fetch_gnews: chiave assente o
    budget/disable -> [] dichiarati da providers_blocked(); cooldown -> cache dichiarata."""
    if not (REQ_OK and GNEWS_KEY):
        return []
    q_lim = _chiave_gnews_top()
    blocco = provider_status("gnews", q_lim)
    if blocco == "SKIP_COOLDOWN":
        return _gnews_da_cache(q_lim, days, max_results, GNEWS_TOP_LINGUA)
    if blocco is not None:
        return []  # SKIP_BUDGET/SKIP_DISABLED/SKIP_PACING: globali, dichiarati da providers_blocked()
    try:
        params = {
            "category": GNEWS_TOP_CATEGORIA,
            "lang": GNEWS_TOP_LINGUA,
            "country": GNEWS_TOP_PAESE,
            "max": max_results,
            "apikey": GNEWS_KEY,
        }
        r = requests.get("https://gnews.io/api/v4/top-headlines", params=params, timeout=10)
        _provider_record("gnews", q_lim, got_429=r.status_code in (403, 429))
        if r.status_code != 200:
            _log(f"gnews top-headlines HTTP {r.status_code}: {_maschera_chiavi(r.text[:200])}")
            return []
        articoli = (r.json() or {}).get("articles", []) or []
        try:
            from bellomberg.market_data import gnews_cache
            gnews_cache.scrivi(q_lim, GNEWS_TOP_LINGUA, articoli)
        except Exception as e:
            _log(f"gnews cache NON scritta (MODULO_ASSENTE o guasto: {type(e).__name__}): "
                 f"il prossimo cooldown delle top-headlines restera' senza articoli")
        return _mappa_gnews(articoli[:max_results])
    except Exception as e:
        _log(f"gnews top-headlines failed: {_eccezione_sicura(e)}")
        return []


def _simbolo_usa(ticker: str) -> bool:
    """Tiingo e Finnhub company-news taggano bene solo i simboli US: niente suffisso di
    borsa («.MI», «.DE») ne' indici («^N225», che prima arrivava a Tiingo come `^n225`);
    niente coppie/futures/crypto (`-USD`, `=F`, `/USDT`, rilievo RV-N P3): un 200 vuoto «live»
    su un simbolo che il fornitore non conosce coprirebbe un guasto vero."""
    t = (ticker or "").strip()
    return bool(t) and not any(c in t for c in ".^-=/")


def _fetch_tiingo(tickers: Optional[List[str]], days: int, limit: int) -> List[Dict[str, Any]]:
    """Wrapper stubbabile di tiingo_news.fetch_tiingo_news (04/10, B2): prima stava in due
    `try: ... except Exception: pass`. Il guasto HTTP lo registra tiingo_news.last_status
    (riga [TIINGO] nel log); qui si DICHIARANO i guasti che il modulo non puo' vedere."""
    try:
        from bellomberg.market_data.tiingo_news import fetch_tiingo_news, tiingo_available
    except Exception as e:
        _log(f"tiingo MODULO_ASSENTE ({type(e).__name__}): 0 articoli Tiingo")
        _registra_esito("tiingo", "MODULO_ASSENTE", type(e).__name__)
        return []
    try:
        from bellomberg.market_data.tiingo_news import tiingo_spenta
        if tiingo_spenta():
            return []  # 10/10: SPENTA per decisione PM, nessuna rete; dichiarata da fonti_spente()
        if not tiingo_available():
            return []  # chiave assente: dichiarata da providers_blocked() (SENZA_CHIAVE)
        out = list(fetch_tiingo_news(tickers, days=days, limit=limit) or [])
    except Exception as e:
        _log(f"tiingo failed: {type(e).__name__}")
        _registra_esito("tiingo", "ERRORE_" + type(e).__name__, type(e).__name__)
        return []
    # l'esito HTTP lo sa tiingo_news: si CONTA nel giro (P2-1), senza duplicarlo negli esiti
    st, guasto = _tiingo_ultimo_stato()
    _conta_nel_giro("tiingo", guasto or (st or {}).get("stato") or "ignoto")
    return out


def _stato_finnhub(testo: str) -> str:
    """Codice dal motivo di finnhub_news._muto (vocabolario allineato a tiingo_news)."""
    t = testo or ""
    m = re.match(r"(\d{3})\b", t) or re.search(r"\bHTTP (\d{3})\b", t)
    if m:
        return "HTTP_" + m.group(1)
    if "FINNHUB_API_KEY" in t:
        return "SENZA_CHIAVE"
    if t.startswith("fuori piano"):
        return "FUORI_PIANO"
    if "requests" in t and "non disponibile" in t:
        return "ERRORE_ImportError"
    if "risposta inattesa" in t:
        return "RISPOSTA_INATTESA"
    return "ERRORE"


def _utc_z(valore: str) -> str:
    """published_at in UTC con Z, come Tiingo/GNews: il sort per stringa di
    search_news_for_ticker mescolava male l'ora LOCALE senza fuso di Finnhub."""
    if not valore:
        return ""
    try:
        dt = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return str(valore)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive = ora locale (fetch_company_news usa fromtimestamp)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Misure M1 (04/10): Finnhub taglia a 250 item per chiamata (sui nomi grandi 50-220
# articoli/giorno: 7 gg sono poche ore) -> il taglio si DICHIARA. E un tetto NOSTRO per
# ticker per chiamata: senza, ~2.440 articoli nuovi in 10 gg gonfierebbero le notifiche.
FINNHUB_TETTO_FORNITORE = 250
FINNHUB_MAX_PER_TICKER = 30
# tagli accumulati nel giro corrente di auto_pull_feed (azzerati a inizio giro)
_FINNHUB_TAGLI_GIRO: Dict[str, Any] = {"scartati": 0, "troncati_dal_fornitore": []}


def _azzera_tagli_finnhub() -> None:
    with _ESITI_LOCK:
        _FINNHUB_TAGLI_GIRO["scartati"] = 0
        _FINNHUB_TAGLI_GIRO["troncati_dal_fornitore"] = []


def _tagli_finnhub() -> Dict[str, Any]:
    with _ESITI_LOCK:
        return {"scartati": _FINNHUB_TAGLI_GIRO["scartati"],
                "troncati_dal_fornitore": list(_FINNHUB_TAGLI_GIRO["troncati_dal_fornitore"])}


def _fetch_finnhub_news(ticker: str, days: int, max_results: int) -> List[Dict[str, Any]]:
    """Finnhub company-news nell'aggregatore (04/10, B2). Wrapper stubbabile: senza, un test
    che arriva qui con la chiave vera del .env farebbe rete vera. Normalizza QUI (non in
    fetch_company_news, che chat_tools usa con la sua forma): provider «finnhub» minuscolo,
    published_at UTC con Z. Ogni esito (anche live) va in esiti_fonti()."""
    motivo: List[str] = []
    try:
        from bellomberg.market_data.finnhub_news import fetch_company_news
    except Exception as e:
        _log(f"finnhub MODULO_ASSENTE ({type(e).__name__}): 0 articoli Finnhub")
        _registra_esito("finnhub", "MODULO_ASSENTE", type(e).__name__)
        return []
    try:
        # si chiede TUTTO cio' che Finnhub manda (max_items alto) per poter MISURARE il
        # taglio del fornitore; il tetto nostro si applica dopo, sui piu' recenti
        grezzi = fetch_company_news(ticker, days=days, max_items=10 * FINNHUB_TETTO_FORNITORE,
                                    motivo=motivo) or []
    except Exception as e:
        _log(f"finnhub company news {ticker} failed: {_eccezione_sicura(e)}")
        _registra_esito("finnhub", "ERRORE_" + type(e).__name__, _eccezione_sicura(e))
        return []
    if motivo:
        _registra_esito("finnhub", _stato_finnhub(motivo[0]), motivo[0])
    else:
        _registra_esito("finnhub", "live")
    out = []
    senza_epoch = riconvertiti = 0
    for a in grezzi:
        it = {k: a.get(k) for k in ("title", "snippet", "url", "source")}
        it["provider"] = "finnhub"
        # 04/10 (B3, rilievo RV-N P3): l'epoch UTC originale di Finnhub, quando c'e'. La
        # riconversione dell'ora locale e' ambigua nell'ora ripetuta del cambio d'ora.
        ep = a.get("published_epoch")
        if isinstance(ep, (int, float)) and not isinstance(ep, bool):
            it["published_at"] = datetime.fromtimestamp(ep, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif "published_epoch" in a:
            senza_epoch += 1          # Finnhub non ha dato la data: resta vuota, dichiarato
            it["published_at"] = ""
        else:
            riconvertiti += 1         # finnhub_news senza il campo: PROXY dall'ora locale
            it["published_at"] = _utc_z(a.get("published_at") or "")
        out.append(it)
    if senza_epoch:
        _log(f"finnhub {ticker}: {senza_epoch} articoli senza data da Finnhub (published_at vuoto)")
    if riconvertiti:
        _log(f"finnhub {ticker}: {riconvertiti} date PROXY (ora locale riconvertita in UTC, "
             f"ambigua nell'ora del cambio d'ora): published_epoch assente")
    # tutti in UTC con Z: l'ordine per stringa e' l'ordine per tempo
    out.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    tetto = max(0, min(int(max_results), FINNHUB_MAX_PER_TICKER))
    tenuti, scartati = out[:tetto], len(out) - min(len(out), tetto)
    troncato = len(grezzi) >= FINNHUB_TETTO_FORNITORE
    with _ESITI_LOCK:
        _FINNHUB_TAGLI_GIRO["scartati"] += scartati
        if troncato:
            _FINNHUB_TAGLI_GIRO["troncati_dal_fornitore"].append(ticker)
    if troncato:
        _log(f"finnhub {ticker}: {len(grezzi)} item = tetto del fornitore ({FINNHUB_TETTO_FORNITORE}): "
             f"risposta TRONCATA dal fornitore, la finestra di {days} gg non e' coperta per intero")
    if scartati:
        _log(f"finnhub {ticker}: tenuti i {len(tenuti)} piu' recenti, scartati {scartati} (tetto per ticker)")
    return tenuti


def _fetch_yfinance_news(ticker: str, max_results: int = 10) -> List[Dict[str, Any]]:
    if not YF_OK:
        return []
    try:
        t = yf.Ticker(ticker)
        items = t.news or []
        out = []
        for a in items[:max_results]:
            # yfinance schema can vary, normalize
            title = a.get("title") or a.get("content", {}).get("title", "")
            url = a.get("link") or a.get("content", {}).get("canonicalUrl", {}).get("url", "")
            ts = a.get("providerPublishTime") or a.get("content", {}).get("pubDate", 0)
            if isinstance(ts, (int, float)):
                pub = datetime.fromtimestamp(ts).isoformat()
            else:
                pub = str(ts)
            out.append({
                "title": title,
                "source": a.get("publisher") or a.get("content", {}).get("provider", {}).get("displayName", "Yahoo Finance"),
                "url": url,
                "published_at": pub,
                "snippet": (a.get("summary") or (a.get("content") or {}).get("summary") or "")[:300],
                "provider": "yfinance",
            })
        return out
    except Exception as e:
        _log(f"yfinance news {ticker} failed: {e}")
        return []


def _fetch_rss(feed_name: str, feed_url: str, max_results: int = 10) -> List[Dict[str, Any]]:
    if not FEEDPARSER_OK:
        return []
    try:
        # audit/11 §2: feedparser.parse(url) scarica via urllib SENZA timeout — un feed
        # che accetta la connessione e non risponde appende lo scheduler per sempre.
        # Scarico con requests (timeout=10 come il resto del modulo) e parso i byte.
        _ua = getattr(feedparser, "USER_AGENT", "feedparser")
        resp = requests.get(feed_url, timeout=10, headers={"User-Agent": _ua})
        parsed = feedparser.parse(resp.content)
        out = []
        for entry in parsed.entries[:max_results]:
            pub = entry.get("published") or entry.get("updated") or ""
            out.append({
                "title": entry.get("title", ""),
                "source": feed_name,
                "url": entry.get("link", ""),
                "published_at": pub,
                "snippet": (entry.get("summary") or "")[:300],
                "provider": "rss",
            })
        return out
    except Exception as e:
        _log(f"RSS {feed_name} failed: {e}")
        return []


# --- bugfix #160/#171: ricerca per NOME AZIENDA, non bare ticker ---
# I ticker EU (.MI/.L/.VI) come keyword non trovano nulla nelle news API;
# i ticker corti matchano qualsiasi omonimo.
#
# 04/09 (lotto 1 del criterio (1), Fable 5.1): i termini NON vivono piu' qui. Il
# dizionario cablato era il book del PM scritto nel sorgente, e chi clonava il repo
# cercava le news delle SUE societa' col ticker nudo, in silenzio (stesso difetto di
# fonti_guidance, trovato dal PM il 03/09). Il negozio e' PRIVATO:
#   data/news_search_terms.json    (ignorato da git, fuori dal perimetro pubblico)
#   news_search_terms.example.json (tracciato: la FORMA, con simboli inventati)
# Forma: {"TICKER": ["termine", ...]} oppure {"TICKER": null} = «nessuna ricerca news
# per questo simbolo», esclusione DICHIARATA (prima era un set letterale col simbolo
# scritto nel codice, ripetuto quattro volte). Le chiavi che iniziano con `_` sono note.
# Regola PM 14/07: negozio assente/illeggibile = mappa VUOTA con origine e motivo
# scritti; voce assente = nome Yahoo e poi ticker nudo, DICHIARATI nel log; mai un
# ripiego muto.

PERCORSO_TERMINI = os.path.join(DB_DIR, "news_search_terms.json")
_ESEMPIO_TERMINI = "news_search_terms.example.json"
# la chiave con cui providers_blocked() dichiara il negozio assente/illeggibile fra le fonti
# mute del giro: NewsPage la rende come un canale a 0 col suo motivo (il buco si vede)
TERMINI_MUTI = "termini_news (negozio)"
_VOCE_ASSENTE = object()

# 06/09 (lotto (b) del criterio (1), MASTER §9-unnonagies): la mappa TEMA -> TITOLI. Stessa
# famiglia di guasto dei termini, stessa via di dichiarazione — `providers_blocked()` la
# porta fra le fonti mute del giro, quindi il MODELLO dei desk la vede accanto alle notizie
# (chat_tools la rende in `fonti_mute`) e la NewsPage la rende come canale a 0 col motivo.
# Senza, «non ci sono notizie che toccano i tuoi titoli» e «non so quali siano i tuoi
# titoli» sarebbero la stessa frase dentro un messaggio che alloca soldi veri.
TEMI_TITOLI_MUTI = "temi_titoli (negozio)"


def _titoli_del_giro(contesto: str) -> Dict[str, Any]:
    """L'esito del negozio dei titoli per tema per UN giro, dichiarato nel log se rotto.
    Si rende l'esito INTERO e non la sola mappa: chi chiama deve poter distinguere «nessun
    tema muove titoli» da «non so quali titoli muovano», e con la sola mappa le due
    avrebbero la stessa forma. La dichiarazione scatta sull'ORIGINE, non sul motivo:
    `motivo` e' la conseguenza del guasto, `origine` e' il guasto, e una guardia appesa al
    motivo sparirebbe zitta se un ramo futuro dimenticasse di riempirlo."""
    try:
        from bellomberg.storage.negozi_privati import carica_temi_titoli
        e = carica_temi_titoli()
    except Exception as exc:
        # stesso trattamento del negozio illeggibile: forma delle voci INVARIATA (mappa
        # vuota), guasto DICHIARATO. Chi chiama non deve distinguere un tipo diverso, e il
        # giro non muore per un import.
        e = {"temi": {}, "origine": "illeggibile",
             "motivo": "negozi_privati non importabile (%s: %s)" % (type(exc).__name__, exc)}
    if e["origine"] in ("assente", "illeggibile"):
        _log(f"negozio dei titoli per tema {e['origine']} ({e['motivo']}): nessun titolo "
             f"attaccato alle notizie in {contesto}")
    return e


def carica_termini(path: str = None) -> Dict[str, Any]:
    """Legge il negozio privato dei termini di ricerca.
    Torna {"termini": {ticker: [str, ...] | None}, "origine": path | 'assente' |
    'illeggibile', "motivo": str | None}. Una sola voce malformata rende illeggibile
    il negozio INTERO: mezzo negozio caricato e' un ripiego muto."""
    p = path or PERCORSO_TERMINI
    # 13/09: i motivi sono message() (la chiave TERMINI_MUTI li porta al banner della NewsPage):
    # il testo italiano e' quello di prima, l'inglese e' la variante nuova
    if not os.path.exists(p):
        return {"termini": {}, "origine": "assente",
                "motivo": message("negozio non trovato: {path} (copia {esempio} in data/ e mettici le TUE societa')",
                                  "Store not found: {path} (copy {esempio} into data/ and enter YOUR companies)",
                                  path=p, esempio=_ESEMPIO_TERMINI)}
    try:
        with open(p, encoding="utf-8") as fh:
            grezzo = json.load(fh)
    except Exception as e:
        return {"termini": {}, "origine": "illeggibile",
                "motivo": "%s: %s" % (type(e).__name__, e)}
    if not isinstance(grezzo, dict):
        return {"termini": {}, "origine": "illeggibile",
                "motivo": message("il negozio non e' un oggetto JSON ma {kind}", "Store is not a JSON object but {kind}",
                                  kind=type(grezzo).__name__)}
    termini: Dict[str, Any] = {}
    for k, v in grezzo.items():
        if k.startswith("_"):
            continue
        kk = k.strip().upper()
        if kk != k or kk in termini:
            # ogni lookup fa .upper() sul ticker: una chiave minuscola o con spazi non
            # verrebbe MAI trovata e il log direbbe «voce assente», vero per il codice e
            # falso per chi ha appena scritto la voce (review 04/09)
            return {"termini": {}, "origine": "illeggibile",
                    "motivo": message("chiave {chiave!r} non canonica o doppia: scrivila MAIUSCOLA, senza "
                                      "spazi e una volta sola ({canonica!r})",
                                      "Noncanonical or duplicate key {chiave!r}: write it UPPERCASE, without "
                                      "spaces, once only ({canonica!r})", chiave=k, canonica=kk)}
        if v is None:
            termini[k] = None
            continue
        if (not isinstance(v, list) or not v
                or not all(isinstance(x, str) and x.strip() for x in v)):
            return {"termini": {}, "origine": "illeggibile",
                    "motivo": message("voce {chiave!r} malformata: serve una lista non vuota di termini "
                                      "(stringhe), oppure null per escludere il simbolo dalle news",
                                      "Malformed entry {chiave!r}: a nonempty list of terms (strings) is "
                                      "required, or null to exclude the symbol from news", chiave=k)}
        termini[k] = list(v)
    return {"termini": termini, "origine": p, "motivo": None}


def termini_correnti() -> Dict[str, Any]:
    """Le voci del negozio, rilette a ogni chiamata (come fonti_guidance.fonti_correnti):
    una modifica a mano del negozio si vede senza riavviare il processo."""
    return carica_termini()["termini"]


def _termini_del_giro(contesto: str) -> Dict[str, Any]:
    """Le voci per un GIRO (portafoglio, eventi SEC, auto_pull_feed): come termini_correnti,
    ma se il negozio e' assente o illeggibile lo DICHIARA nel log, una volta, con origine e
    motivo. Senza, un negozio rotto da una virgola in piu' azzerava le esclusioni in
    silenzio e il simbolo con voce null finiva alla SEC (review 04/09, tre lenti su tre):
    `termini_correnti` butta via l'esito, esattamente come faceva `fonti_correnti`
    (guidance_watch.py) prima della sua cura."""
    c = carica_termini()
    if c["origine"] in ("assente", "illeggibile"):
        _log(f"negozio dei termini news {c['origine']} ({c['motivo']}): nessun termine e "
             f"nessuna esclusione applicati in {contesto}")
    return c["termini"]


# L'esito dell'ULTIMO CARICAMENTO ALL'IMPORT, e solo quello (come ORIGINE_FONTI): dove il
# negozio manca dice 'assente' col suo motivo. Chi vuole i termini chiama termini_correnti().
_CARICATO_TERMINI = carica_termini()
ORIGINE_TERMINI = {"origine": _CARICATO_TERMINI["origine"], "motivo": _CARICATO_TERMINI["motivo"]}
del _CARICATO_TERMINI


def _voce_termini(ticker: str):
    """(voce, stato, caricato): stato in 'negozio' | 'escluso' | 'assente'."""
    caricato = carica_termini()
    voce = caricato["termini"].get((ticker or "").upper(), _VOCE_ASSENTE)
    if voce is _VOCE_ASSENTE:
        return None, "assente", caricato
    if voce is None:
        return None, "escluso", caricato
    return voce, "negozio", caricato


def escluso_dalle_news(ticker: str, termini: Optional[Dict[str, Any]] = None) -> bool:
    """True SOLO per la voce `null` del negozio: un simbolo assente NON e' escluso,
    si cerca per nome Yahoo e infine col ticker nudo (dichiarato). `termini` gia'
    caricati = una lettura sola."""
    if termini is None:
        termini = termini_correnti()
    return termini.get((ticker or "").upper(), _VOCE_ASSENTE) is None


def giro_news(positions: List[Dict[str, Any]]):
    """(ticker del giro, contesto «TICKER (peso%)» per il classificatore): gli esclusi
    dal negozio non entrano ne' nell'uno ne' nell'altro. Il formato del contesto e'
    quello che _classify_with_haiku riceve (v. tests/test_news_prompt_book_intero.py)."""
    termini = _termini_del_giro("giro_news")
    tickers: List[str] = []
    ctx: List[str] = []
    for p in positions:
        tk = p.get("ticker")
        if not tk or escluso_dalle_news(tk, termini):
            continue
        tickers.append(tk)
        try:
            w = float(p.get("peso_pct") or p.get("weight_pct") or 0)
        except (TypeError, ValueError):
            w = 0.0
        ctx.append(f"{tk} ({w:.1f}%)")
    return tickers, ctx


def _pat_come_scritto(t: str):
    """Confronto che RISPETTA le maiuscole: come scritto o tutto maiuscolo, a parola intera."""
    return re.compile(r"\b(?:" + re.escape(t) + "|" + re.escape(t.upper()) + r")\b")


def _filter_items_by_terms(items: List[Dict[str, Any]],
                            terms: List[str],
                            contesto: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Post-filter word-boundary (#160): tiene solo gli item che menzionano
    davvero uno dei terms in titolo o snippet. yfinance e' symbol-based: fidato.
    `contesto` (10/10 v2, nome Yahoo di una parola sola): l'item deve avere il nome (come
    scritto o MAIUSCOLO) E almeno un termine di contesto (il simbolo, maiuscolo; le parole di
    borsa, senza distinzione di maiuscole) nella stessa riga titolo + descrizione."""
    # 10/10: i termini corti si confrontano RISPETTANDO le maiuscole (come scritto o tutto
    # maiuscolo): la sigla di una banca scritta 'Abc'/'ABC' non e' la parola inglese 'ABc'.
    if contesto:
        pats = [_pat_come_scritto(t) for t in terms]
        nomi = {t.lower() for t in terms}
        pats_ctx = [_pat_come_scritto(c) if c.isupper()
                    else (_SHARES_BORSA if c.lower() == "shares"
                          else re.compile(r"\b" + re.escape(c) + r"\b", re.IGNORECASE))
                    for c in contesto if c.lower() not in nomi]
    else:
        pats = [_pat_come_scritto(t) if _termine_corto(t)
                else re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE) for t in terms]
        pats_ctx = []
    out = []
    for it in items:
        if (it.get("provider") or "").lower() in ("yfinance", "tiingo", "finnhub"):
            out.append(it)  # fonti symbol-based/taggate: fidate
            continue
        text = (it.get("title", "") or "") + " " + (it.get("snippet", "") or "")
        if any(p.search(text) for p in pats) and (not contesto or any(p.search(text) for p in pats_ctx)):
            out.append(it)
    return out


def _nome_emittente_yahoo(ticker: str) -> str:
    """Nome dell'emittente della quotazione, usato solo come termine news.

    Il ticker del portafoglio resta quello del listino posseduto.  Per una quotazione
    europea senza voce nel negozio dei termini, il nome Yahoo e' un fallback piu'
    affidabile del ticker nudo; un errore di metadata lascia decidere al chiamante il
    fallback dichiarato precedente.
    """
    if not YF_OK:
        return ""
    try:
        info = yf.Ticker((ticker or "").upper().strip()).get_info() or {}
        return str(info.get("longName") or info.get("shortName") or "").strip()
    except Exception as exc:
        _log(f"identita' Yahoo {ticker} non disponibile: {type(exc).__name__}: {exc}")
        return ""


_SUFFISSI_LEGALI_NOME = {
    "ag", "corp", "corporation", "inc", "incorporated", "limited", "ltd", "nv",
    "plc", "sa", "se", "spa",
}
_YAHOO_EXCHANGE_US = {
    "ASE", "BTS", "NCM", "NGM", "NMS", "NYQ", "OQB", "OQX", "PCX", "PNK",
    "NASDAQ", "NASDAQCM", "NASDAQGM", "NASDAQGS", "NYSE", "NYSEARCA",
    "NYSEAMERICAN", "OTC MARKETS", "OTCQB", "OTCQX",
}


def _nome_emittente_normalizzato(nome: str) -> str:
    """Forma prudente per confrontare lo stesso emittente fra due quotazioni Yahoo."""
    parole = re.findall(r"[a-z0-9]+", (nome or "").lower())
    while parole and parole[-1] in _SUFFISSI_LEGALI_NOME:
        parole.pop()
    return " ".join(parole)


def _ticker_us_yahoo_univoco(ticker: str, nome: str) -> Optional[str]:
    """Ticker US plain dello stesso emittente, solo se il match per nome e' univoco.

    Nessuna regola sintattica `.DE -> base`: i codici Xetra non codificano il simbolo USA.
    Ambiguita' (per esempio due classi azionarie) o rete assente restituiscono ``None``.
    """
    t = (ticker or "").upper().strip()
    nome_norm = _nome_emittente_normalizzato(nome)
    if not REQ_OK or "." not in t or not nome_norm:
        return None
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": nome, "quotesCount": 20, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        r.raise_for_status()
        candidati = set()
        for voce in (r.json().get("quotes") or []):
            simbolo = str(voce.get("symbol") or "").upper().strip()
            tipo = str(voce.get("quoteType") or "").upper().strip()
            exchange = str(voce.get("exchange") or "").upper().strip()
            exchange_esteso = str(voce.get("exchDisp") or "").upper().strip()
            nome_voce = voce.get("longname") or voce.get("shortname") or ""
            if (simbolo and "." not in simbolo and tipo == "EQUITY"
                    and (exchange in _YAHOO_EXCHANGE_US
                         or exchange_esteso in _YAHOO_EXCHANGE_US)
                    and _nome_emittente_normalizzato(nome_voce) == nome_norm):
                candidati.add(simbolo)
        return next(iter(candidati)) if len(candidati) == 1 else None
    except Exception as exc:
        _log(f"ricerca alias USA Yahoo per {t} non disponibile: {type(exc).__name__}: {exc}")
        return None


def _simbolo_news(ticker: str, nome: str) -> tuple[str, str]:
    """Simbolo per provider news symbol-based; torna anche la provenienza."""
    t = (ticker or "").upper().strip()
    if not t or "." not in t:
        return t, "ticker_portafoglio"
    try:
        from bellomberg.storage.negozi_privati import carica_alias
        alias = carica_alias()
        if alias["origine"] not in ("assente", "illeggibile"):
            esplicito = alias["alias"]["finnhub"].get(t)
            if esplicito:
                return esplicito, "alias_fonti:finnhub"
        else:
            _log(f"{t}: alias_fonti {alias['origine']}: "
                 f"{alias.get('motivo') or 'causa non dichiarata'}; provo la scoperta Yahoo")
    except Exception as exc:
        _log(f"{t}: alias_fonti illeggibile: {type(exc).__name__}: {exc}; "
             "provo la scoperta Yahoo")
    automatico = _ticker_us_yahoo_univoco(t, nome)
    if automatico:
        return automatico, "yahoo:nome_univoco"
    return t, "ticker_portafoglio"


# --- 199e: filtro qualita' - fonti clickbait/SEO fuori dal feed (e quindi dal briefing) ---
_JUNK_SOURCE_RE = re.compile(
    r"fool\.com|motley\s*fool|zacks|marketbeat|benzinga|investorplace|insider\s*monkey|"
    r"247wallst|24/7\s*wall|tipranks|simplywall|gurufocus|stockstory|smartasset", re.IGNORECASE)
_JUNK_TITLE_RE = re.compile(
    r"\bShould You Buy\b|\bIs .{1,50} a Buy\b|\b\d+ (Best |Top )?Stocks? to Buy\b|"
    r"\bcould make you (rich|a millionaire)\b|\bNo-Brainer\b", re.IGNORECASE)


def _drop_junk(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """199e: butta le fonti spazzatura e i titoli listicle PRIMA che entrino nel feed."""
    out = []
    for it in items:
        blob = ((it.get("source") or "") + " " + (it.get("url") or ""))
        if _JUNK_SOURCE_RE.search(blob):
            continue
        if _JUNK_TITLE_RE.search(it.get("title") or ""):
            continue
        out.append(it)
    return out


# ── Qualita' della testata (10/10, Opus 5.5 — ordine PM «GNews al top») ──────────────────
# Prima l'ordine nel feed era SOLO per data: a parita' di giorno, un quotidiano generalista
# e un'agenzia finanziaria valevano uguale. Ora l'ordine e' (fascia di freschezza, peso
# testata, data): dentro la stessa fascia vincono le testate importanti. Nessuna testata si
# SCARTA qui: le basse finiscono in coda e, se il tetto le taglia, auto_pull_feed lo DICHIARA
# (`ranking_testate` nel giro + log).
# Liste motivate da due misure del 10/10 (news_feed, 30 giorni, sola lettura): rilevanza
# media data dal classificatore per testata, e la probe live GNews (C:/dev/_news/probe.json).
#   3 primaria    — agenzie e quotidiani finanziari di riferimento (Reuters, Bloomberg, FT, WSJ,
#                   CNBC rel 5,5, Barron's 6,0, IBD 6,3, MarketWatch, Nikkei, Handelsblatt, Sole 24 Ore,
#                   e per la stampa locale del book MF/Milano Finanza, Boersen-Zeitung, FAZ, ANSA,
#                   Radiocor, AP)
#   2 finanziaria — stampa finanziaria/settoriale specializzata (Yahoo Finance 6,2 / «Yahoo» di
#                   Finnhub 6,1, Seeking Alpha 7,2, MarketScreener 5,8 — ripubblica agenzie —,
#                   Markets/Business Insider, Investing.com, Morningstar, Teleborsa, Der Aktionaer,
#                   finanzen.net, testate di settore)
#   1 standard    — tutto il resto (default: una testata ignota NON e' bassa)
#   0 bassa       — generalisti misurati a rilevanza bassa sul book: quotidiani nazionali
#                   indiani (Times of India 2,7, The Tribune 2,4, New Indian Express 2,9,
#                   Hindustan Times 3,0, India Today 3,3, ThePrint), aggregatori SEO (EUROPE SAYS,
#                   Devdiscourse), tabloid (NY Post 1,6, Express 1,5). Depriorizzati, MAI scartati.
# v2 (riserva 4 del revisore): niente piu' SOTTOSTRINGHE su «nome + url». Due confronti sole:
#   - nome della testata ESATTO (minuscolo, spazi compattati): «CNBC TV18», «Bloomberg Quint»,
#     «Fortune India», «Forbes India» non sono CNBC/Bloomberg/Fortune/Forbes;
#   - DOMINIO (netloc dell'url, o il nome se e' esso stesso un dominio): uguale o sottodominio.
#     Il PATH dell'url non conta (misura del revisore: un «/reuters/» nel path promuoveva).
# Piu' corrispondenze (es. Yahoo che ripubblica un'agenzia col nome dell'agenzia): vale la piu' alta.
_TESTATE_PESO = {
    3: {"nomi": {
            "reuters", "bloomberg", "bloomberg.com", "financial times", "ft", "the wall street journal",
            "wall street journal", "wsj", "cnbc", "barron's", "barrons", "marketwatch", "nikkei",
            "nikkei asia", "the economist", "economist", "associated press", "the associated press",
            "ap", "ap news", "investor's business daily", "investors business daily", "handelsblatt",
            "il sole 24 ore", "il sole 24ore", "sole 24 ore", "radiocor", "il sole 24 ore radiocor",
            "les echos", "frankfurter allgemeine zeitung", "frankfurter allgemeine", "faz", "f.a.z.",
            "dow jones", "dow jones newswires", "mt newswires", "mf", "milano finanza",
            "mf milano finanza", "mf-dow jones", "börsen-zeitung", "boersen-zeitung", "ansa"},
        "domini": {
            "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com", "barrons.com",
            "marketwatch.com", "nikkei.com", "economist.com", "apnews.com", "investors.com",
            "handelsblatt.com", "ilsole24ore.com", "lesechos.fr", "faz.net", "dowjones.com",
            "mtnewswires.com", "milanofinanza.it", "boersen-zeitung.de", "ansa.it"}},
    2: {"nomi": {
            "yahoo", "yahoo finance", "yahoo finance video", "seeking alpha", "seekingalpha",
            "sa market currents", "marketscreener", "markets insider", "business insider",
            "investing.com", "morningstar", "fortune", "forbes", "axios", "the information",
            "borsa italiana", "money.it", "finanzen.net", "stat", "stat news", "fierce pharma",
            "fiercepharma", "endpoints news", "defense news", "breaking defense", "coindesk",
            "the block", "electrek", "techcrunch", "teleborsa", "der aktionär", "der aktionaer"},
        "domini": {
            "finance.yahoo.com", "seekingalpha.com", "marketscreener.com", "businessinsider.com",
            "investing.com", "morningstar.com", "fortune.com", "forbes.com", "axios.com",
            "theinformation.com", "borsaitaliana.it", "money.it", "finanzen.net", "statnews.com",
            "fiercepharma.com", "endpts.com", "defensenews.com", "breakingdefense.com",
            "coindesk.com", "theblock.co", "electrek.co", "techcrunch.com", "teleborsa.it",
            "deraktionaer.de"}},
    0: {"nomi": {
            "times of india", "the times of india", "the tribune", "the new indian express",
            "new indian express", "hindustan times", "india today", "theprint", "news18",
            "deccan chronicle", "times now", "europe says", "devdiscourse", "new york post",
            "ny post", "daily express", "express"},
        "domini": {
            "timesofindia.indiatimes.com", "tribuneindia.com", "newindianexpress.com",
            "hindustantimes.com", "indiatoday.in", "theprint.in", "news18.com",
            "deccanchronicle.com", "timesnownews.com", "europesays.com", "devdiscourse.com",
            "nypost.com", "express.co.uk"}},
}
QUALITA_ETICHETTE = {3: "primaria", 2: "finanziaria", 1: "standard", 0: "bassa"}


def _dominio(s: str) -> str:
    """netloc minuscolo senza «www.» e porta; '' se `s` non e' un url/dominio."""
    s = (s or "").strip().lower()
    if not s:
        return ""
    if "://" not in s:
        if " " in s or "." not in s:
            return ""
        s = "//" + s
    from urllib.parse import urlparse
    try:
        host = urlparse(s).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _nel_dominio(host: str, domini) -> bool:
    return bool(host) and any(host == d or host.endswith("." + d) for d in domini)


def peso_testata(it: Dict[str, Any]) -> int:
    """Peso della testata di un item (3 primaria .. 0 bassa; 1 se ignota): nome ESATTO o
    DOMINIO (v. _TESTATE_PESO); fra piu' corrispondenze vale la piu' alta."""
    nome = " ".join((it.get("source") or "").lower().split())
    host_url = _dominio(it.get("url") or "")
    host_nome = _dominio(nome)
    trovati = [peso for peso, liste in _TESTATE_PESO.items()
               if nome in liste["nomi"] or _nel_dominio(host_url, liste["domini"])
               or _nel_dominio(host_nome, liste["domini"])]
    return max(trovati) if trovati else 1


# Fasce di freschezza (riserva 1 del revisore): la qualita' della testata ordina DENTRO una
# fascia, mai fra fasce. Prima (peso, data) faceva vincere un articolo Reuters di 20 ore fa su
# una notizia standard di 10 minuti fa per tutto il giorno. Fasce: <6h, <24h, oltre. Un
# articolo SENZA data sta in fondo all'ultima fascia: mai in testa, mai «fresco» per difetto.
FASCE_FRESCHEZZA_S = (6 * 3600, 24 * 3600)
FASCE_ETICHETTE = ("<6h", "<24h", "oltre")


def _adesso_ts() -> float:
    return time.time()


def fascia_freschezza(it: Dict[str, Any], ora: Optional[float] = None) -> int:
    """0 = <6h, 1 = <24h, 2 = oltre o senza data."""
    ts = _parse_date_ts(it.get("published_at", ""))
    if not ts:
        return len(FASCE_FRESCHEZZA_S)
    eta = (_adesso_ts() if ora is None else ora) - ts
    for i, limite in enumerate(FASCE_FRESCHEZZA_S):
        if eta < limite:
            return i
    return len(FASCE_FRESCHEZZA_S)


def ordina_per_qualita(items: List[Dict[str, Any]], ora: Optional[float] = None) -> List[Dict[str, Any]]:
    """(fascia di freschezza, con data prima di senza data, peso testata desc, data desc),
    stabile. Ogni item porta `qualita_testata` (etichetta leggibile): la depriorizzazione e'
    visibile su ogni riga, non dedotta dall'ordine."""
    ora = _adesso_ts() if ora is None else ora
    for it in items:
        it["qualita_testata"] = QUALITA_ETICHETTE[peso_testata(it)]

    def chiave(x):
        ts = _parse_date_ts(x.get("published_at", ""))
        return (fascia_freschezza(x, ora), 0 if ts else 1, -peso_testata(x), -ts)
    return sorted(items, key=chiave)


def conta_tagli_qualita(items: List[Dict[str, Any]], tetto: int) -> Dict[str, int]:
    """Cosa taglia il tetto per ticker/tema su una lista GIA' ordinata e GIA' ripulita da cio'
    che il feed ha (candidati_nuovi): `oltre_tetto` conta solo notizie NUOVE perse."""
    fuori = items[tetto:]
    return {"oltre_tetto": len(fuori),
            "bassa_oltre_tetto": sum(1 for it in fuori if peso_testata(it) == 0),
            "bassa_tenute": sum(1 for it in items[:tetto] if peso_testata(it) == 0)}


def _chiave_url(it: Dict[str, Any]) -> str:
    """La chiave con cui la riga entra (e si cerca) in news_feed.url: l'url, o per gli item
    senza url «nourl:» + md5 del titolo (stessa formula del salvataggio)."""
    url = (it.get("url") or "").strip()
    return url or "nourl:" + hashlib.md5((it.get("title", "") or "").encode()).hexdigest()


def _gia_nel_feed(cur, it: Dict[str, Any], titoli_feed) -> Optional[str]:
    """'url' se la chiave url e' gia' in news_feed, 'titolo' se il titolo normalizzato e' fra
    quelli del feed recente (48h), None se e' una notizia nuova. Stessa query del salvataggio."""
    cur.execute("SELECT 1 FROM news_feed WHERE url = ?", (_chiave_url(it),))
    if cur.fetchone():
        return "url"
    t = _titolo_norm(it.get("title", ""))
    if t and t in titoli_feed:
        return "titolo"
    return None


def candidati_nuovi(items: List[Dict[str, Any]], cur, titoli_feed, presi, days: int,
                    conta: Dict[str, int], ora: Optional[float] = None) -> List[Dict[str, Any]]:
    """Riserva 1 del revisore (10/10 v2): cio' che concorre al tetto per ticker/tema sono le
    sole notizie NUOVE e nella finestra. Prima il tetto si applicava a tutto e i doppioni si
    toglievano DOPO: un articolo di qualita' gia' salvato occupava il posto a ogni giro (24h)
    e le notizie nuove non entravano (simulazione del revisore sul feed vero: 1053 -> 577 sul
    titolo piu' coperto). Fuori, CONTATI in `conta`:
      - oltre_eta: pubblicati prima di `days` giorni fa (le fonti senza filtro data: RSS, yfinance);
      - gia_nel_feed: url (o titolo normalizzato, 48h) gia' in news_feed;
      - gia_nel_giro: gia' preso da un ticker/tema precedente di questo giro (`presi`).
    Senza data: restano (l'eta' non si misura) e si contano in senza_data; l'ordine li mette
    in fondo all'ultima fascia. Ritorna la lista riordinata con lo stesso `ora`."""
    ora = _adesso_ts() if ora is None else ora
    limite = ora - days * 86400
    out = []
    for it in items:
        ts = _parse_date_ts(it.get("published_at", ""))
        if ts and ts < limite:
            conta["oltre_eta"] = conta.get("oltre_eta", 0) + 1
            continue
        if _chiave_url(it) in presi:
            conta["gia_nel_giro"] = conta.get("gia_nel_giro", 0) + 1
            continue
        try:
            motivo = _gia_nel_feed(cur, it, titoli_feed)
        except Exception:
            # lettura del feed fallita: la notizia concorre (il salvataggio la ricontrolla), ma
            # il buco si CONTA e il giro lo scrive nel log (mai un «nuovo» presunto in silenzio)
            conta["feed_non_controllato"] = conta.get("feed_non_controllato", 0) + 1
            motivo = None
        if motivo:
            conta["gia_nel_feed"] = conta.get("gia_nel_feed", 0) + 1
            if motivo == "titolo":
                with _ESITI_LOCK:
                    _DOPPIONI_GIRO["titolo_db"] += 1
            continue
        if not ts:
            conta["senza_data"] = conta.get("senza_data", 0) + 1
        out.append(it)
    return ordina_per_qualita(out, ora)


# ── Termini -> query (10/10, Opus 5.5) ─────────────────────────────────────────────────
# Misure del 10/10 (probe GNews 3 giorni + news_feed in sola lettura; dettaglio nel rapporto,
# non qui: i nomi del book non stanno nel codice pubblico). (a) Il nome Yahoo entrava INTERO e
# tra virgolette ('"Zeta Micro Devices, Inc."'): la frase col suffisso legale non compare quasi
# mai nei titoli. Sui 4 nomi del book provati: 0-1 articoli col nome intero, 11-25 (su un totale
# fino a 204) col nome pulito; nel DB, 3 giorni di GNews per ticker US: 0-3 righe. Il filtro a
# valle usava lo stesso termine, quindi cadevano anche gli articoli giusti delle altre fonti
# testuali. (b) Un termine corto del negozio (sigla di 3 lettere di un titolo italiano) entrava
# nella query INGLESE: GNews non distingue maiuscole e la sigla e' anche una parola della
# politica indiana -> 174 righe in 3 giorni a rilevanza media 2,2, da ThePrint, The Tribune,
# Times of India... E' da li' che veniva la «stampa indiana» del feed.
_SUFFISSI_LEGALI_QUERY = {
    "ag", "as", "asa", "ab", "aktiengesellschaft", "corp", "corporation", "inc", "incorporated",
    "kgaa", "limited", "llc", "ltd", "nv", "oyj", "plc", "sa", "sab", "se", "spa",
}
# v2 (riserva 3 del revisore): parole GENERICHE della ragione sociale che nei titoli non si
# scrivono («Zeta Technology» -> «Zeta»). Si tolgono solo in CODA, come i suffissi legali.
_PAROLE_GENERICHE_QUERY = {
    "co", "company", "group", "holding", "holdings", "technology", "technologies", "systems",
}
TERMINE_CORTO_MAX = 3
# Lingua della stampa locale per suffisso di borsa: .MI c'era (199e); .DE misurato il 10/10
# (un titolo .DE del book: 3 articoli in inglese, 25 su 45 in tedesco — finanzen.net, FAZ,
# Boerse Express).
LINGUE_LOCALI = {".MI": "it", ".DE": "de"}


def nome_per_ricerca(nome: str) -> str:
    """Nome Yahoo come lo scrivono i titoli. Regole, in quest'ordine:
      1. «(The)» ovunque e «The» iniziale via;
      2. la classe azionaria ovunque via («Class A», «Class B», «Cl A»);
      3. dalla CODA, finche' resta piu' di una parola: suffissi legali (Inc, Corp, AG, S.p.A.,
         A/S...), parole generiche (Holdings, Group, Technology, Systems, Company, Co) e token
         senza lettere ne' cifre («&», «-»).
    Maiuscole conservate; almeno una parola resta sempre.
    'Zeta Micro Devices, Inc.' -> 'Zeta Micro Devices'; 'Zeta Inc. Class A' -> 'Zeta';
    'Zeta-Cola Company (The)' -> 'Zeta-Cola'."""
    parole = (nome or "").replace(",", " ").split()
    parole = [p for p in parole if p.lower() != "(the)"]
    if len(parole) > 1 and parole[0].lower() == "the":
        parole = parole[1:]
    pulite: List[str] = []
    i = 0
    while i < len(parole):
        if (parole[i].lower().rstrip(".") in ("class", "cl") and i + 1 < len(parole)
                and re.fullmatch(r"[A-Za-z]\.?", parole[i + 1])):
            i += 2
            continue
        pulite.append(parole[i])
        i += 1
    parole = pulite or parole

    def _via(p: str) -> bool:
        nudo = re.sub(r"[^a-z0-9]", "", p.lower())
        return not nudo or nudo in _SUFFISSI_LEGALI_QUERY or nudo in _PAROLE_GENERICHE_QUERY
    while len(parole) > 1 and _via(parole[-1]):
        parole.pop()
    return " ".join(parole).strip()


# Riserva 3: i fondi non si cercano per nome (il nome del fondo non fa notizia: «Zeta MSCI
# World UCITS ETF» porta solo pagine di prodotto). Senza voce nel negozio -> nessuna query per
# nome, dichiarato: restano le fonti a simbolo (yfinance). La voce col sottostante/tema si
# scrive nel negozio dei termini (il codice non la deduce).
_RE_NOME_FONDO = re.compile(r"\b(?:UCITS|ETF|ETC|ETN|iShares|Xtrackers)\b", re.IGNORECASE)


def nome_e_un_fondo(nome: str) -> bool:
    return bool(_RE_NOME_FONDO.search(nome or ""))


def _termine_corto(t: str) -> bool:
    return " " not in t.strip() and len(t.strip()) <= TERMINE_CORTO_MAX


def query_da_termini(terms: List[str], lingua: str = "en") -> str:
    """Query OR dei termini (frasi tra virgolette). In INGLESE i termini corti (<=3 caratteri,
    una parola: le sigle) escono se resta almeno un termine lungo: l'API non distingue le
    maiuscole e in inglese sono parole comuni. Nella lingua locale restano (la stampa italiana
    scrive la sigla)."""
    usati = list(terms)
    if lingua == "en" and any(not _termine_corto(t) for t in terms):
        usati = [t for t in terms if not _termine_corto(t)]
    return " OR ".join(f'"{t}"' if " " in t else t for t in usati)


# Riserva 2 del revisore: un nome Yahoo che, pulito, e' UNA parola sola e' spesso una parola
# comune o un nome proprio («Target», «Visa», «Shell»): da solo porta l'attore, la squadra,
# «l'obiettivo d'inflazione». Allora la query chiede anche il CONTESTO finanziario (il simbolo
# o una parola di borsa nella lingua della ricerca) e il filtro a valle vuole il nome E il
# contesto nella stessa riga titolo + descrizione. Le voci del negozio NON passano di qui:
# sono scelte a mano.
# 10/10 (Opus 5.5, probe live GNews): «shares» da solo e' anche il verbo «condivide»
# («Leonardo DiCaprio Shares Political Message»): in inglese il contesto e' una locuzione di borsa.
PAROLE_CONTESTO = {"en": ("stock", "shares", "share price"), "it": ("azioni", "titolo"), "de": ("Aktie", "Aktien")}
# Nel FILTRO «shares» vale solo in senso di borsa: seguito da un verbo/termine di mercato o
# preceduto da un possessivo («the group's shares»), mai come verbo «condivide».
_SHARES_BORSA = re.compile(
    r"(?:\b\w+'s\s+shares\b|\bshares\s+(?:of|in|rose|rise|rises|rising|fell|fall|falls|falling|"
    r"slid|slide|slides|sliding|jump|jumped|jumps|drop|dropped|drops|gain|gained|gains|surge|surged|"
    r"surges|plunge|plunged|plunges|tumble|tumbled|tumbles|climb|climbed|climbs|sink|sank|sinks|soar|"
    r"soared|soars|edge|edged|edges|rally|rallied|rallies|slump|slumped|slumps|trade|traded|trading|"
    r"hit|hits|are|were|have|has|closed|close|ended|end|outperform|underperform|extend|extended)\b)",
    re.IGNORECASE)


def _simbolo_base(ticker: str) -> str:
    return (ticker or "").upper().strip().split(".")[0]


def contesto_nome_singolo(ticker: str) -> List[str]:
    """Simbolo base + parole di borsa di TUTTE le lingue (per il filtro: un articolo inglese
    su un titolo italiano scrive «shares»)."""
    parole = [_simbolo_base(ticker)]
    for lista in PAROLE_CONTESTO.values():
        parole.extend(p for p in lista if p not in parole)
    return [p for p in parole if p]


def query_nome_con_contesto(nome: str, ticker: str, lingua: str = "en") -> str:
    """'"Nome" AND ( SIMBOLO OR stock OR shares )' nella lingua della ricerca (GNews: OR
    precede AND, parentesi ammesse — docs.gnews.io/openapi.yaml)."""
    alternative = [_simbolo_base(ticker)] + list(PAROLE_CONTESTO.get(lingua, PAROLE_CONTESTO["en"]))
    alternative = [f'"{a}"' if " " in a else a for a in alternative if a]
    return f'"{nome}" AND ( ' + " OR ".join(alternative) + " )"


def _all_rss_cached(max_per_feed: int = 10) -> List[Dict[str, Any]]:
    """199e: tutti i feed RSS in un colpo, cache 10 min (search_news_for_ticker gira
    per ~19 ticker a refresh: senza cache sarebbero ~300 fetch RSS)."""
    key = "rss_all_items"
    if key in _CACHE and (time.time() - _CACHE[key]["ts"]) < 600:
        return _CACHE[key]["data"]
    items: List[Dict[str, Any]] = []
    for name, url in RSS_FEEDS.items():
        items.extend(_fetch_rss(name, url, max_per_feed))
    _CACHE[key] = {"ts": time.time(), "data": items}
    return items


def search_news_for_ticker(ticker: str, days: int = 3, max_per_source: int = 5) -> List[Dict[str, Any]]:
    """Cerca news per un ticker specifico aggregando tutte le fonti.
    Usa i termini del negozio (nomi azienda) al posto del bare ticker (#160/#171).
    Voce assente = nome Yahoo, con ticker nudo come ultimo fallback dichiarato. Voce null
    = fuori dal GIRO automatico (search_portfolio_news, eventi SEC, auto_pull_feed), ma la
    ricerca DIRETTA procede: un [] muto qui sarebbe identico a «zero notizie» per chi
    chiama (review 04/09)."""
    cache_key = f"ticker:{ticker}:{days}:{max_per_source}"
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return entry["data"]

    voce, stato, caricato = _voce_termini(ticker)
    contesto: List[str] = []   # 10/10 v2: nome Yahoo di una parola -> query e filtro col contesto
    fondo = False              # 10/10 v2: nome Yahoo di un fondo -> niente ricerca per nome
    if stato == "escluso":
        _log(f"{ticker}: voce null nel negozio ({caricato['origine']}): fuori dal giro "
             f"automatico, ma la ricerca diretta procede col ticker nudo")
        terms = [ticker]
    elif stato == "assente":
        motivo = f"; motivo: {caricato['motivo']}" if caricato["motivo"] else ""
        nome = _nome_emittente_yahoo(ticker)
        if nome:
            pulito = nome_per_ricerca(nome)
            _log(f"{ticker}: voce assente nel negozio dei termini (origine: "
                 f"{caricato['origine']}{motivo}): cerco il nome emittente Yahoo {nome!r}"
                 + (f" senza suffisso legale: {pulito!r}" if pulito != nome else ""))
            terms = [pulito]
            if nome_e_un_fondo(nome):
                fondo = True
                _log(f"{ticker}: {nome!r} e' un fondo: nessuna ricerca testuale per nome (il nome "
                     f"del fondo non fa notizia), restano le fonti a simbolo; le notizie del "
                     f"sottostante/tema vogliono una voce nel negozio dei termini")
            elif " " not in pulito:
                contesto = contesto_nome_singolo(ticker)
                _log(f"{ticker}: nome di una parola sola ({pulito!r}): query e filtro chiedono "
                     f"anche il contesto di borsa ({', '.join(contesto)})")
        else:
            _log(f"{ticker}: voce assente nel negozio dei termini (origine: "
                 f"{caricato['origine']}{motivo}): identita' Yahoo non disponibile, cerco "
                 f"il ticker nudo, che per i simboli europei le API non trovano")
            terms = [ticker]  # ultimo fallback, dichiarato sopra
    else:
        terms = voce
    lingua_locale = next((lg for suff, lg in LINGUE_LOCALI.items() if ticker.upper().endswith(suff)), None)
    if contesto:
        query = query_nome_con_contesto(terms[0], ticker, "en")
        query_locale = query_nome_con_contesto(terms[0], ticker, lingua_locale) if lingua_locale else ""
    else:
        query = query_da_termini(terms, "en")
        query_locale = query_da_termini(terms, lingua_locale) if lingua_locale else ""
    nome_identita = terms[0] if terms and terms[0] != ticker else ""
    simbolo_news, origine_simbolo = _simbolo_news(ticker, nome_identita)
    if simbolo_news != ticker:
        _log(f"{ticker}: provider news a simbolo risolti su {simbolo_news} "
             f"({origine_simbolo}); gli item restano attribuiti a {ticker}")

    items: List[Dict[str, Any]] = []
    if not fondo:  # 10/10 v2: un fondo senza voce non si cerca per nome (dichiarato sopra)
        items.extend(_fetch_newsapi(query, days, max_per_source))
        # items.extend(_fetch_marketaux(terms[0], days, max_per_source))  # 199d: Marketaux sganciato (402/ridondante con Tiingo); riattivabile decommentando
        items.extend(_fetch_thenewsapi(query, days, max_per_source))
        items.extend(_fetch_gnews(query, days, GNEWS_MAX_ART))  # Essential: bacino ampio, i freschi vincono il sort :599
        if lingua_locale:  # 199e .MI -> it; 10/10 .DE -> de (LINGUE_LOCALI, misurato)
            items.extend(_fetch_gnews(query_locale, days, GNEWS_MAX_ART, lang=lingua_locale))
    items.extend(_fetch_yfinance_news(simbolo_news, max_per_source))
    # Tiingo (#173) e Finnhub (04/10): tagging affidabile per simboli US. Integrazione: sul
    # simbolo RISOLTO da G3 (ADR/ricerca Yahoo), coi wrapper stubbabili dell'altra sessione.
    if _simbolo_usa(simbolo_news):
        items.extend(_fetch_tiingo([simbolo_news], days, max_per_source))
        with _ESITI_LOCK:
            esito_prima = _ESITI_FONTI.get("finnhub")
        items.extend(_fetch_finnhub_news(simbolo_news, days, max_per_source))
        with _ESITI_LOCK:
            esito_dopo = _ESITI_FONTI.get("finnhub")
        # G3: il guasto resta PER TICKER (un successo su un altro titolo non lo cancella) e
        # scade con la cache 15 min di questa ricerca; l'esito lo scrive il wrapper (nuova voce).
        if esito_dopo is not None and esito_dopo is not esito_prima:
            if esito_dopo.get("stato") == "live":
                _runtime_failure_clear("finnhub", simbolo_news)
            else:
                _runtime_failure_record("finnhub", simbolo_news,
                                        f"{esito_dopo.get('stato')}: {esito_dopo.get('motivo') or ''}")

    items.extend(_all_rss_cached())  # 199e: copertura EU - gli RSS vengono filtrati dai terms qui sotto
    items = _drop_junk(items)
    items = _filter_items_by_terms(items, terms, contesto)
    for it in items:
        it["ticker_mentioned"] = ticker
    items = _dedupe_titoli(_dedupe(items))  # P2-2: anche i redirect Finnhub
    # 10/10: (fascia di freschezza, peso testata, data) invece della sola data (v2: le fasce)
    items = ordina_per_qualita(items)

    _CACHE[cache_key] = {"ts": time.time(), "data": items}
    return items


def search_news_global(query: str, days: int = 3, max_per_source: int = 5) -> List[Dict[str, Any]]:
    """Cerca news per query libera (no ticker specifico). Include RSS feeds."""
    cache_key = f"global:{query}:{days}:{max_per_source}"
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return entry["data"]

    items: List[Dict[str, Any]] = []
    items.extend(_fetch_newsapi(query, days, max_per_source))
    # items.extend(_fetch_marketaux(query, days, max_per_source))  # 199d: Marketaux sganciato
    items.extend(_fetch_thenewsapi(query, days, max_per_source))
    items.extend(_fetch_gnews(query, days, max_per_source))

    # RSS solo filtrato per query (in title o snippet)
    rss_items: List[Dict[str, Any]] = []
    for name, url in RSS_FEEDS.items():
        rss_items.extend(_fetch_rss(name, url, max_per_source))
    q_lower = query.lower()
    rss_filtered = [it for it in rss_items
                    if q_lower in (it.get("title", "") + " " + it.get("snippet", "")).lower()]
    items.extend(rss_filtered)

    items = ordina_per_qualita(_dedupe(items))  # 10/10: (fascia, peso testata, data)

    _CACHE[cache_key] = {"ts": time.time(), "data": items}
    return items


def search_portfolio_news(days: int = 2, max_per_ticker: int = 3) -> Dict[str, List[Dict[str, Any]]]:
    """Per ogni holding del portfolio, fetcha news. Output: {ticker: [news]}"""
    db = MemoryDB()
    snap = db.get_portfolio_summary()
    positions = snap.get("positions", [])
    result: Dict[str, List[Dict[str, Any]]] = {}
    termini = _termini_del_giro("search_portfolio_news")
    for p in positions:
        ticker = p["ticker"]
        if escluso_dalle_news(ticker, termini):   # voce null del negozio: esclusione dichiarata
            continue
        news = search_news_for_ticker(ticker, days=days, max_per_source=max_per_ticker)
        # residuo muto #4 (Lotto C 23/07, regola 14/07): un ticker a 0 news NON
        # sparisce piu' dal dict — "assente" e "zero risultati" erano indistinguibili.
        result[ticker] = news[:max_per_ticker * 2]
    return result


def get_top_catalysts(days: int = 7, limit: int = 20) -> List[Dict[str, Any]]:
    """Top catalyst dell'ultimo periodo, mixed sources + RSS."""
    cache_key = f"top_catalysts:{days}:{limit}"
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return entry["data"]

    items: List[Dict[str, Any]] = []
    catalysts_queries = ["Fed rate decision", "earnings beat miss", "M&A deal",
                          "regulatory FDA approval", "geopolitical risk", "central bank"]
    for q in catalysts_queries:
        items.extend(_fetch_newsapi(q, days=days, max_results=5))
    # RSS feeds
    for name, url in RSS_FEEDS.items():
        items.extend(_fetch_rss(name, url, max_results=10))

    items = _drop_junk(_dedupe(items))
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    items = items[:limit]

    _CACHE[cache_key] = {"ts": time.time(), "data": items}
    return items


def fetch_macro_news(categories: Optional[List[str]] = None,
                      min_importance: int = 3,
                      days: int = 2,
                      max_per_topic: int = 4,
                      include_reddit: bool = True) -> List[Dict[str, Any]]:
    """Pulla news macro/geo/politica/EM/crypto per categorie selezionate.
    Default: tutte le categorie con importance >= 3.

    Returns lista unificata ordinata per importance desc + recency.
    """
    try:
        from bellomberg.market_data.news_topics import TOPICS, CATEGORIES
    except Exception as e:
        _log(f"news_topics import failed: {e}")
        return []

    # 06/09 (lotto (b) del criterio (1)): quali TITOLI muove un tema viene dal negozio
    # privato, non dal sorgente — UNA lettura per giro, non una per notizia. Lo stato del
    # negozio entra nella CHIAVE DI CACHE: senza, un negozio sparito a meta' TTL avrebbe
    # lasciato per 900 s notizie con i titoli attaccati accanto alla dichiarazione «negozio
    # assente», cioe' il dato e la sua smentita nello stesso payload.
    _esito_temi = _titoli_del_giro("fetch_macro_news")
    titoli = _esito_temi["temi"]
    cache_key = (f"macro:{','.join(categories or [])}:{min_importance}:{days}:{max_per_topic}"
                 f":{_esito_temi['origine'] if _esito_temi['origine'] in ('assente', 'illeggibile') else 'ok'}")
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return render_payload(entry["data"])

    selected_cats = set(categories) if categories else set(CATEGORIES.keys())
    items: List[Dict[str, Any]] = []
    for t in TOPICS:
        if t["category"] not in selected_cats:
            continue
        if t.get("importance", 3) < min_importance:
            continue
        q = t.get("query", "")
        if not q:
            continue
        # NewsAPI + Marketaux con la query del topic
        fetched = _fetch_newsapi(q, days=days, max_results=max_per_topic)
        # fetched += _fetch_marketaux(q, days=days, max_results=max_per_topic)  # 199d: Marketaux sganciato
        fetched += _fetch_gnews(q, days=days, max_results=GNEWS_MAX_ART)  # Essential: bacino ampio
        for it in fetched:
            it["topic_id"] = t["id"]
            it["topic_label"] = t["label"]
            it["topic_category"] = t["category"]
            it["topic_importance"] = t.get("importance", 3)
            it["tickers_affected"] = list(titoli.get(t["id"], ()))
        items.extend(fetched)

    # RSS sweep filtrato per le query selezionate
    rss_items: List[Dict[str, Any]] = []
    for name, url in RSS_FEEDS.items():
        rss_items.extend(_fetch_rss(name, url, max_results=8))
    # Match RSS items contro topic queries
    all_keywords = []
    for t in TOPICS:
        if t["category"] not in selected_cats:
            continue
        if t.get("importance", 3) < min_importance:
            continue
        # Spezza la query in keyword (split by OR)
        for kw in re.split(r"\s+OR\s+|\s*\|\s*", t.get("query", "")):
            kw = kw.strip().strip('"').lower()
            if len(kw) >= 4:
                all_keywords.append((kw, t))
    for r_it in rss_items:
        text_low = (r_it.get("title", "") + " " + r_it.get("snippet", "")).lower()
        for kw, topic in all_keywords:
            if kw in text_low:
                r_it["topic_id"] = topic["id"]
                r_it["topic_label"] = topic["label"]
                r_it["topic_category"] = topic["category"]
                r_it["topic_importance"] = topic.get("importance", 3)
                r_it["tickers_affected"] = list(titoli.get(topic["id"], ()))
                items.append(r_it)
                break

    # Reddit: TOLTO dalle fonti (decisione PM 04/10; misura F7: HTTP 403 su 5/5 subreddit).
    # Non si interroga piu'; `include_reddit` resta nella firma per i chiamanti (chat_tools,
    # /news/macro) e la richiesta si DICHIARA invece di produrre zero post in silenzio.
    if include_reddit:
        _log("reddit " + str(fonti_spente()["reddit"]) + ": nessuna chiamata")

    items = _dedupe(items)
    # Sort: importance desc, poi (10/10) fascia di freschezza, peso della testata, recency
    items = ordina_per_qualita(items)
    items.sort(key=lambda x: -(x.get("topic_importance", 3)))

    _CACHE[cache_key] = {"ts": time.time(), "data": items}
    return render_payload(items)


def fetch_corporate_events(days: int = 14, max_items: int = 30) -> List[Dict[str, Any]]:
    """Eventi societari high-impact dal portfolio: 8-K + Form 4 insider via SEC EDGAR
    + news M&A/earnings via NewsAPI.
    """
    cache_key = f"corp_events:{days}:{max_items}"
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return render_payload(entry["data"])

    items: List[Dict[str, Any]] = []

    # 1. SEC EDGAR per ticker US del portfolio
    try:
        from bellomberg.market_data.sec_edgar import (get_corporate_events_for_portfolio,
                                                      ticker_ambiguo_per_cik)
        db = MemoryDB()
        snap = db.get_portfolio_summary()
        positions = snap.get("positions", [])
        # Ticker US diretti + quotazioni estere con alias SEC verificato. Passare una base
        # ricavata togliendo il suffisso puo' agganciare un omonimo americano; la guardia
        # di sec_edgar ammette il suffisso solo quando alias_fonti lo rende non ambiguo.
        termini = _termini_del_giro("fetch_corporate_events")
        us_tickers = [p["ticker"] for p in positions
                      if p.get("ticker") and ticker_ambiguo_per_cik(p["ticker"]) is None
                      and not escluso_dalle_news(p["ticker"], termini)]  # voce null = fuori dal giro
        sec_events = get_corporate_events_for_portfolio(us_tickers, days=days)
        for e in sec_events:
            items.append({
                "title": e.get("title", ""),
                "snippet": e.get("snippet", ""),
                "url": e.get("url", ""),
                "provider": f"SEC EDGAR ({e.get('type', '')})",
                "published_at": e.get("date", ""),
                "ticker_mentioned": e.get("ticker", ""),
                "topic_importance": e.get("importance", 3),
                "event_type": e.get("type", ""),
                "source_type": "sec",
                "metadata": e.get("metadata", {}),
                "title_origin": e.get("title_origin"),
                "snippet_origin": e.get("snippet_origin"),
                "presentation_languages": e.get("presentation_languages", []),
            })
    except Exception as e:
        _log(f"SEC corporate events failed: {e}")

    # 2. News M&A/earnings/downgrades via NewsAPI (per ticker portfolio)
    try:
        from bellomberg.market_data.news_topics import get_category_topics
        for t in get_category_topics("corporate"):
            fetched = _fetch_newsapi(t["query"], days=days, max_results=5)
            for f in fetched:
                f["topic_id"] = t["id"]
                f["topic_label"] = t["label"]
                f["topic_importance"] = t.get("importance", 3)
                f["source_type"] = "news"
            items.extend(fetched)
    except Exception as e:
        _log(f"corporate topic news failed: {e}")

    items = _dedupe(items)
    items = _dedupe(items)
    items.sort(key=lambda x: (
        -(x.get("topic_importance", 3)),
        -(_parse_date_ts(x.get("published_at", ""))),
    ))
    items = items[:max_items]

    _CACHE[cache_key] = {"ts": time.time(), "data": items}
    return render_payload(items)


def get_top_global(limit: int = 15) -> List[Dict[str, Any]]:
    """Top 10-15 news globali del giorno - mix di RSS + news API mainstream."""
    cache_key = f"top_global:{limit}"
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return entry["data"]

    items: List[Dict[str, Any]] = []
    # audit/11 §5: 'Reuters Business' non esiste piu' in RSS_FEEDS (sostituito da ANSA
    # Economia col 199e): il feed veniva saltato in silenzio -> 4 fonti core su 5.
    # 25/07/26 (41): WSJ Markets congelato a gen 2025 (RSS DJ chiusi) -> Yahoo
    # Finance come quinta fonte core (probata fresca in giornata, volume alto).
    core_feeds = ["Bloomberg Markets", "FT Markets", "ANSA Economia",
                   "Yahoo Finance", "CNBC Top News"]
    for name in core_feeds:
        url = RSS_FEEDS.get(name)
        if url:
            items.extend(_fetch_rss(name, url, max_results=5))
        else:
            _log(f"get_top_global: feed core '{name}' assente da RSS_FEEDS, saltato")
    try:
        if REQ_OK and NEWSAPI_KEY:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                "category": "business",
                "language": "en",
                "pageSize": 10,
                "apiKey": NEWSAPI_KEY,
            }
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                for a in data.get("articles", []):
                    items.append({
                        "title": a.get("title", ""),
                        "snippet": (a.get("description", "") or "")[:300],
                        "url": a.get("url", ""),
                        "provider": "NewsAPI",
                        "source": a.get("source", {}).get("name", ""),
                        "published_at": a.get("publishedAt", ""),
                    })
            else:
                # 04/10 (B2, rilievo RV-N): il non-200 scartava in silenzio
                _log(f"top_global newsapi HTTP {r.status_code}: 0 articoli NewsAPI nel top globale")
    except Exception as e:
        _log(f"top_global newsapi failed: {_eccezione_sicura(e)}")

    items = _drop_junk(_dedupe(items))
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    items = items[:limit]

    _CACHE[cache_key] = {"ts": time.time(), "data": items}
    return items


def _parse_date_ts(s: str) -> float:
    """Parse vari formati datetime, ritorna timestamp float (0 se fail).
    audit/11 §5: la timezone NON si butta piu' — le date dei feed (e pulled_at di
    SQLite datetime('now')) sono UTC: strippare 'Z'/offset e parsare come ora LOCALE
    sfasava la freshness di ~2h (boost fresh e cutoff feed sbagliati)."""
    if not s:
        return 0
    from datetime import timezone as _tz
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_tz.utc)
        return d.timestamp()
    except Exception:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d"):
        try:
            d = datetime.strptime(s.split(".")[0], fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=_tz.utc)
            return d.timestamp()
        except Exception:
            continue
    return 0


_W_CACHE = {"map": {}, "ts": 0.0}


_FAVT_CACHE = {"set": None, "ts": 0.0}


def _favorites_tickers() -> set:
    """T4-3: ticker delle favorite companies del PM (cache 10 min; set vuoto se tabella assente)."""
    if _FAVT_CACHE["set"] is not None and (time.time() - _FAVT_CACHE["ts"]) < 600:
        return _FAVT_CACHE["set"]
    out = set()
    try:
        conn = connect_sqlite(MemoryDB().db_path)  # hardening #32: WAL + busy_timeout
        out = {str(r[0]).upper() for r in conn.execute("SELECT ticker FROM favorite_companies").fetchall()}
        conn.close()
    except Exception:
        out = set()
    _FAVT_CACHE["set"] = out
    _FAVT_CACHE["ts"] = time.time()
    return out


def _portfolio_weights_cached() -> Dict[str, float]:
    """201-C: pesi % delle posizioni dal DB, cache 10 min (per la materialita')."""
    if _W_CACHE["map"] and (time.time() - _W_CACHE["ts"]) < 600:
        return _W_CACHE["map"]
    out: Dict[str, float] = {}
    try:
        snap = MemoryDB().get_portfolio_summary()
        for p_ in snap.get("positions", []):
            try:
                out[str(p_.get("ticker", "")).upper()] = float(p_.get("peso_pct") or p_.get("weight_pct") or 0)
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    _W_CACHE["map"] = out
    _W_CACHE["ts"] = time.time()
    return out


def _summary_identity(item):
    """Bind generated prose to the exact stored original, not merely a reused URL."""
    original = {key: item.get(key + "_original", item.get(key, "")) or ""
                for key in ("title", "snippet")}
    original["url"] = item.get("url") or ""
    return hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _summary_path(db_path, item, language):
    selected = capture_language(language)
    # DB-specific directory also isolates test/copy databases in the same folder.
    db_file = Path(db_path).resolve()
    return db_file.parent / (db_file.name + ".news_summaries_v1") / selected / (_summary_identity(item) + ".json")


def _save_summary(db_path, item, summary):
    """Atomic, versioned generated cache. Original article and legacy IT fields stay put."""
    language = capture_language(summary["language"])
    if not summary.get("headline") and not summary.get("why_matters"):
        return
    path = _summary_path(db_path, item, language)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "language": language, "source_hash": _summary_identity(item),
               "headline": summary.get("headline", ""), "why_matters": summary.get("why_matters", "")}
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _present_summary(db_path, row):
    language = capture_language()
    row["title_original"] = row.get("title")
    row["snippet_original"] = row.get("snippet")
    path = _summary_path(db_path, row, language)
    summary = None
    status = "unavailable"
    if path.exists():
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
            if (summary.get("version") != 1 or summary.get("language") != language
                    or summary.get("source_hash") != _summary_identity(row)
                    or not all(isinstance(summary.get(key), str) for key in ("headline", "why_matters"))):
                raise ValueError("invalid generated summary metadata")
            status = "available"
        except Exception as exc:
            summary = None
            status = "invalid"
            _log(f"generated summary cache invalid: {exc}")
    elif language == "it" and (row.get("headline_it") or row.get("why_matters")):
        summary = {"headline": row.get("headline_it"), "why_matters": row.get("why_matters")}
        status = "legacy"
    if summary:
        row["title"] = summary.get("headline") or row["title_original"]
        row["snippet"] = summary.get("why_matters") or row["snippet_original"]
    row["summary_language"] = language if summary else None
    row["summary_status"] = status
    row["summary_note"] = "" if summary else _lt(
        "Sintesi nella lingua selezionata non disponibile: viene mostrato il testo originale.",
        "Summary in the selected language is not available: original text is shown.")


@scoped_language
def get_feed(limit: int = 50, min_relevance: int = 0,
              ticker: Optional[str] = None,
              sentiment: Optional[str] = None) -> List[Dict[str, Any]]:
    """Legge news dalla tabella news_feed (popolata da auto_pull_feed).
    Filtri opzionali: min_relevance (1-10), ticker (mentioned), sentiment.
    """
    import sqlite3
    from datetime import datetime as _dt
    try:
        db = MemoryDB()
        conn = connect_sqlite(db.db_path)  # hardening #32: WAL + busy_timeout
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        where = ["1=1"]
        params: List[Any] = []
        if min_relevance > 0:
            # Decisione PM (04/10): le non classificate (relevance NULL) restano nel filtro,
            # marcate da classification_status, senza una rilevanza inventata.
            where.append("(relevance >= ? OR relevance IS NULL)")
            params.append(min_relevance)
        if ticker:
            where.append("ticker_mentioned = ?")
            params.append(ticker.upper())
        if sentiment:
            where.append("sentiment = ?")
            params.append(sentiment.lower())
        extra_cols = ", headline_it, why_matters, classified"
        sql = f"""
            SELECT id, title, snippet, source, url, published_at, pulled_at,
                   ticker_mentioned, theme, provider, sentiment, sentiment_score, relevance{extra_cols}
            FROM news_feed
            WHERE {' AND '.join(where)}
            ORDER BY pulled_at DESC
            LIMIT ?
        """
        params.append(max(int(limit) * 3, 150))  # prefetch: il sort vero e' in Python
        try:
            cur.execute(sql, params)
        except sqlite3.OperationalError:
            # pre-migrazione: prima senza `classified` (G3, righe «unknown»), poi legacy 201
            try:
                cur.execute(sql.replace(extra_cols, ", headline_it, why_matters"), params)
            except sqlite3.OperationalError:
                cur.execute(sql.replace(extra_cols, ""), params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        # 201-C: MATERIALITA' = relevance + peso posizione + freschezza (il book comanda)
        weights = _portfolio_weights_cached()
        favs = _favorites_tickers()  # T4-3: boost preferiti nel ranking (non nasconde il resto)
        now_ts = _dt.now().timestamp()
        for r in rows:
            # G3 (04/10): classified 1/0 dichiarato; NULL = riga di prima, quando un
            # fallimento entrava come neutral/5 indistinguibile (stato «unknown»).
            r["classification_status"] = {1: "classified", 0: "not_classified"}.get(
                r.pop("classified", None), "unknown")
            # non classificata = nessun punteggio di rilevanza (non un 5 finto)
            rel = float(r["relevance"]) if r.get("relevance") is not None else 0.0
            w = float(weights.get((r.get("ticker_mentioned") or "").upper(), 0.0))
            age_h = max(0.0, (now_ts - _parse_date_ts(r.get("pulled_at") or "")) / 3600.0) if r.get("pulled_at") else 48.0
            fresh = 6.0 if age_h <= 6 else (3.0 if age_h <= 24 else 0.0)
            fav_b = 6.0 if (r.get("ticker_mentioned") or "").upper() in favs else 0.0
            r["_materiality"] = round(rel * 2.0 + min(w, 25.0) * 0.4 + fresh + fav_b, 2)
            _present_summary(db.db_path, r)
        rows.sort(key=lambda x: (-(x.get("_materiality") or 0), x.get("pulled_at") or ""), )
        return rows[: int(limit)]
    except Exception as e:
        _log(f"get_feed failed: {e}")
        return []


def invalidate_cache():
    _CACHE.clear()
    _RUNTIME_PROVIDER_FAILURES.clear()


# ============================================================
# AUTO-FEED PERSISTENT (scheduler-compatible)
# ============================================================
# Canale «news/sentiment» del decimo controllo del cancello (04/09, criterio (1)/(2)):
# TUTTO il testo statico spedito a Haiku sta qui, in una costante che il cancello rende
# e misura; la chiamata la COMPONE coi campi vivi (titolo, snippet, tag, book coi pesi).
# Gli esempi sono INVENTATI: fino al 04/09 nominavano due posizioni del PM, e Haiku li
# riceveva a ogni notizia, centinaia di volte al giorno, fuori dal corpus del cancello.
NEWS_SENTIMENT_PROMPT = (
    "News title: {title}\n"
    "Snippet: {snippet}\n"
    "Ticker taggato: {tk_tag} | tema: {theme_tag}\n"
    "PM portfolio (ticker e peso): {tickers_str}\n\n"
    "Output JSON: {{\"sentiment\": \"bullish|bearish|neutral\", "
    "\"sentiment_score\": -1.0..1.0, \"relevance\": 1..10, "
    "\"headline_it\": \"...\", \"why_matters\": \"...\"}}\n"
    "Relevance: 1=irrelevante per il portfolio, 10=critico catalyst.\n"
    "Sentiment: rispetto al portfolio tickers (bullish per holder long).\n"
    "headline_it (#201): riscrivi il titolo in ITALIANO stile agenzia Bloomberg/Reuters, "
    "max 90 caratteri, FATTI non clickbait, numeri se presenti (es. 'Acme lancia offerta su Beta, titolo +6%').\n"
    "why_matters (#201): UNA frase italiana max 140 caratteri che collega la notizia al book "
    "(se il ticker e' in portafoglio cita il peso, es. 'ACME.MI 4,1% del book: offerta = possibile re-rating'); "
    "se non c'entra col book, l'impatto macro. Asciutto, da desk.\n"
    "REGOLA: la lista 'PM portfolio' sopra e' COMPLETA. VIETATO scrivere "
    "che un ticker 'non in portafoglio' / non e' una posizione: se il ticker "
    "non compare nella lista, scrivi solo l'impatto macro, senza negazioni sul book.\n"
    "Rispondi SOLO il JSON."
)


_CLASSIFICATION_DEFAULT = {
    "sentiment": "neutral", "sentiment_score": 0.0, "relevance": 5,
    "headline_it": "", "why_matters": "",
}


CLASSIFIER_TIMEOUT_S = 60.0   # per tentativo (REV_G3 R3): non i 600 s di default ritentati


def _classification_result(status: str, error: Optional[str] = None) -> Dict[str, Any]:
    result = dict(_CLASSIFICATION_DEFAULT)
    result["_classification_status"] = status
    if error:
        result["_classification_error"] = error[:240]
    return result


def _textual_llm_content(message: Any) -> str:
    """Return only text blocks; reasoning/tool blocks never enter JSON parsing."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict):
            kind = block.get("type")
            text = block.get("text")
        else:
            kind = getattr(block, "type", "text")
            text = getattr(block, "text", None)
        if kind in (None, "text", "output_text") and isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _extract_first_json_value(text: str) -> Any:
    """Extract the first valid JSON object or array from text without ``eval``."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text or ""):
        if char not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
    raise ValueError("nessun oggetto o array JSON valido nella risposta testuale")


@scoped_language
def _classify_with_haiku(item: Dict[str, Any], context_tickers: List[str]) -> Dict[str, Any]:
    """Classifica con Haiku: sentiment + relevance score 1-10.
    Retry: SOLO quelli del client OpenRouter (REV_G3 R3); campi assenti = fallita (R5).
    """
    try:
        # 05/09 (ordine PM): OpenRouter, modello dal .env (NEWS_CLASSIFIER_MODEL). Chiave o
        # variabile assente: la causa va nel log col NOME e la notizia resta "neutral"
        # come prima (il ripiego neutro preesiste, 8c ne e' proprietaria: qui solo la chiamata).
        from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm, ConfigurazioneLLMMancante
        try:
            _modello_classificatore = _modello_llm("news_classifier")
            client = OpenRouterClient(timeout=CLASSIFIER_TIMEOUT_S)
        except ConfigurazioneLLMMancante as e:
            _log(f"classify SKIPPED ({e}) - news defaults to neutral")
            return _classification_result("failed", str(e))
        title = item.get("title", "")[:200]
        snippet = item.get("snippet", "")[:300]
        # Fix 7 §9-quattuortrigies (ok PM 03/08): NIENTE [:20] — su un book piu' lungo
        # di 20 voci la lista arrivava MONCA delle posizioni oltre la ventesima e Haiku, coerente
        # con cio' che vede, negava chi manca (248 casi in DB, changelog (63)).
        # poche decine di voci ~ 400 char: irrisorie; un taglio zitto su un dato dato in pasto
        # a un LLM e' la classe vietata dalla regola 14/07.
        tickers_str = ", ".join(context_tickers)
        tk_tag = item.get("ticker_mentioned", "") or ""
        theme_tag = item.get("theme", "") or ""
        template = NEWS_SENTIMENT_PROMPT
        if capture_language() == "en":
            # Only the authored template changes; item text/quotes/book are inserted afterwards.
            template = template.replace("in ITALIANO", "in ENGLISH").replace("UNA frase italiana", "ONE English sentence")
        prompt = template.format(title=title, snippet=snippet, tk_tag=tk_tag,
                                              theme_tag=theme_tag, tickers_str=tickers_str)

        # REV_G3 R3: un solo livello di retry, quello del client (max_retries su 408/409/429/
        # 5xx e rete, timeout CLASSIFIER_TIMEOUT_S): prima un ciclo qui sopra = fino a 12 POST.
        msg = client.messages.create(
            model=_modello_classificatore,
            max_tokens=1200,
            # 05/09 (sonda): senza questo il classificatore spende ~250 dei 320 token a
            # ragionare e il JSON esce troncato (-> neutral zitto); «spento» su un
            # modello che lo rifiuta diventa effort minimal, dichiarato da llm_client.
            thinking={"type": "disabled"},
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )

        data = _extract_first_json_value(_textual_llm_content(msg))
        if isinstance(data, list):
            data = next((entry for entry in data if isinstance(entry, dict)), None)
        if not isinstance(data, dict):
            raise ValueError("JSON classificatore non e' un oggetto")
        # REV_G3 R5: campi assenti o fuori dominio = classificazione FALLITA, mai neutral/5
        sentiment = str(data.get("sentiment") or "").strip().lower()
        if sentiment not in ("bullish", "bearish", "neutral"):
            return _classification_result("failed", "campo sentiment assente o non valido: %r"
                                          % (data.get("sentiment"),))
        try:
            relevance = int(data.get("relevance"))
        except (TypeError, ValueError):
            relevance = None
        if relevance is None or not 1 <= relevance <= 10:
            return _classification_result("failed", "campo relevance assente o non valido: %r"
                                          % (data.get("relevance"),))
        try:
            sentiment_score = float(data["sentiment_score"]) if data.get("sentiment_score") is not None else None
        except (TypeError, ValueError):
            sentiment_score = None   # punteggio illeggibile = assente (None), non uno 0 inventato
        return {
            "sentiment": sentiment,
            "sentiment_score": sentiment_score,
            "relevance": relevance,
            "language": capture_language(),
            "headline": str(data.get("headline_it", "") or "")[:120],
            "headline_it": str(data.get("headline_it", "") or "")[:120] if capture_language() == "it" else "",
            "why_matters": str(data.get("why_matters", "") or "")[:180],
            "_classification_status": "classified",
        }
    except Exception as e:
        err_str = str(e)
        if "529" in err_str or "overloaded" in err_str.lower():
            _log(f"haiku classify SKIPPED (Anthropic overloaded after retries) - news defaults to neutral")
        else:
            _log(f"haiku classify failed: {e}")
        return _classification_result("failed", err_str)


NEWS_FEED_STATUS_PATH = os.path.join(DB_DIR, "news_feed_status.json")   # B4 (02/09): era relativo alla cwd


def _scrivi_stato_giro(out: Dict[str, Any], path: Optional[str] = None) -> None:
    """P2 (12/08): persiste ora+esito dell'ultimo giro del feed in un JSON.
    Scrittura ATOMICA (tmp + os.replace): mai un file mezzo scritto in lettura.
    Niente DB apposta: una tabella sarebbe una migrazione, e le migrazioni
    hanno il gate della lettera A/B del PM. Se il giro crasha prima di
    arrivare qui il file resta il precedente: stato_ultimo_giro() ne
    dichiara l'eta', non inventa freschezza."""
    import json as _json
    path = path or NEWS_FEED_STATUS_PATH
    stato = {
        "timestamp": datetime.now().isoformat(),
        "esito": "degradato" if out.get("degraded") else "ok",
        "fetched": out.get("fetched"),
        "classified": out.get("classified"),
        "classification_attempted": out.get("classification_attempted"),
        "classification_failed": out.get("classification_failed"),
        "saved": out.get("saved"),
        "not_classified": out.get("not_classified"),
        "interrupted": out.get("interrupted"),
        "skipped_duplicates": out.get("skipped_duplicates"),
        "providers_blocked": out.get("providers_blocked") or {},
        # 04/10 (B2): chiavi additive; un giro che non le ha misurate scrive n.d., non {}
        "provenienza": out.get("provenienza", "n.d."),
        "fonti_esito": out.get("fonti_esito", "n.d."),
        "finnhub_tagli": out.get("finnhub_tagli", "n.d."),
        "doppioni_titolo": out.get("doppioni_titolo", "n.d."),
        "ranking_testate": out.get("ranking_testate", "n.d."),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(stato, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def stato_ultimo_giro(path: Optional[str] = None,
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """Lettore puro dello stato ultimo giro (pattern 25: chiave sempre
    presente, mai un default zitto). `stato` vale ok | degradato | n.d. |
    illeggibile; `now` iniettabile per collaudi deterministici (come il
    `today` di guidance_watch). Zero rete, zero DB."""
    import json as _json
    path = path or NEWS_FEED_STATUS_PATH
    now = now or datetime.now()
    if not os.path.exists(path):
        return {"stato": "n.d.",
                "motivo": ("nessun giro registrato: il campo nasce il 12/08 "
                           "e si riempie dal primo giro del feed col codice nuovo")}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = _json.load(f)
        esito = raw.get("esito")
        if esito not in ("ok", "degradato"):
            return {"stato": "illeggibile",
                    "motivo": "esito mancante o sconosciuto: %r" % (esito,)}
        ts = datetime.fromisoformat(raw["timestamp"])
        age_min = round((now - ts).total_seconds() / 60.0, 1)
        return {"stato": esito,
                "timestamp": raw.get("timestamp"),
                "age_minutes": age_min,
                "fetched": raw.get("fetched"),
                "classified": raw.get("classified"),
                "classification_attempted": raw.get("classification_attempted"),
                "classification_failed": raw.get("classification_failed"),
                "saved": raw.get("saved"),
                "not_classified": raw.get("not_classified"),
                "interrupted": raw.get("interrupted"),
                "skipped_duplicates": raw.get("skipped_duplicates"),
                "providers_blocked": raw.get("providers_blocked") or {},
                # 04/10 (B2): file scritti prima di oggi non hanno le chiavi -> n.d. dichiarato
                "provenienza": raw.get("provenienza", "n.d."),
                "fonti_esito": raw.get("fonti_esito", "n.d."),
                "finnhub_tagli": raw.get("finnhub_tagli", "n.d."),
                "doppioni_titolo": raw.get("doppioni_titolo", "n.d."),
                "ranking_testate": raw.get("ranking_testate", "n.d.")}
    except Exception as e:
        return {"stato": "illeggibile", "motivo": f"{type(e).__name__}: {e}"}


def _somma_tagli(tot: Dict[str, int], parz: Dict[str, int]) -> None:
    for k, v in parz.items():
        tot[k] = tot.get(k, 0) + int(v)


@scoped_language
def auto_pull_feed(days: int = 1, classify: bool = True,
                    max_per_ticker: int = 5, max_per_theme: int = 5,
                    should_stop=None) -> Dict[str, Any]:
    # Opus 4.8 16/07: 3->5 (feed tiene i top max_per_ticker*2 = 10/ticker per data, era 6),
    # cosi' piu' news fresche di GNews Essential raggiungono il DB. tiingo sale solo 3->5
    # (bacino ampio 25 e' SOLO per gnews), quindi il rumore cresce poco; tunabile nel trial.
    # NB: solo gli articoli NUOVI (url non gia' in DB) vengono classificati da Haiku, quindi
    # il costo scala col flusso di news vere, non con questo numero.
    """Pull news per ogni holding + temi macro, classifica con Haiku, salva in DB.
    Designed to run as scheduled task (Lun-Ven ogni 15 min market hours).

    Returns dict con counts: {fetched, classified, saved, skipped_duplicates}.
    """
    import sqlite3
    _azzera_tagli_finnhub()
    _azzera_esiti_giro()
    _azzera_doppioni_giro()
    db = MemoryDB()
    snap = db.get_portfolio_summary()
    positions = snap.get("positions", [])
    # ticker del giro + contesto con pesi per headline/why_matters ("TICKER (4.1%)"):
    # gli esclusi dal negozio (voce null) non entrano in nessuno dei due
    tickers, ctx_weighted = giro_news(positions)
    # T4-3: anche le favorite companies entrano nel giro news (max 10 extra, dopo il book)
    termini = _termini_del_giro("auto_pull_feed (preferiti)")
    fav_extra = [t for t in sorted(_favorites_tickers())
                 if t not in set(tickers) and not escluso_dalle_news(t, termini)][:10]
    if fav_extra:
        tickers = tickers + fav_extra
        _log(f"auto_pull_feed: +{len(fav_extra)} favorites nel giro news: {fav_extra}")

    all_items: List[Dict[str, Any]] = []
    # 10/10: cosa taglia il tetto per ticker/tema dopo l'ordine per qualita' (dichiarato nel giro).
    # v2: il tetto si applica alle sole notizie NUOVE (candidati_nuovi); oltre_tetto conta solo
    # quelle; gia_nel_feed / gia_nel_giro / oltre_eta / senza_data dicono cosa e' uscito prima.
    tagli_qualita = {"oltre_tetto": 0, "bassa_oltre_tetto": 0, "bassa_tenute": 0,
                     "gia_nel_feed": 0, "gia_nel_giro": 0, "oltre_eta": 0, "senza_data": 0,
                     "feed_non_controllato": 0}
    # G3 (04/10): arresto del backend -> il giro si ferma al prossimo passo e lo dichiara
    interrotto: List[bool] = []

    def fermato() -> bool:
        if should_stop is not None and should_stop():
            interrotto.append(True)
            return True
        return False

    # 10/10 v2 (riserva 1 del revisore): il feed si legge PRIMA del tetto, con la stessa
    # connessione e la stessa query del controllo doppioni del salvataggio. Titoli delle 48h
    # letti UNA volta per giro (P2-2).
    conn = connect_sqlite(db.db_path)  # hardening #32: WAL + busy_timeout
    cur = conn.cursor()
    titoli_feed = set()
    try:
        cur.execute("SELECT title FROM news_feed WHERE pulled_at >= datetime('now', '-2 days')")
        titoli_feed = {_titolo_norm(r[0]) for r in cur.fetchall() if r[0]}
    except Exception as e:
        _log(f"doppioni per titolo contro il feed NON controllati ({type(e).__name__}): "
             f"possibili doppioni e doppie notifiche in questo giro")
    presi: set = set()
    ora_giro = _adesso_ts()

    def _nuovi(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return candidati_nuovi(items, cur, titoli_feed, presi, days, tagli_qualita, ora_giro)

    def _prendi(it: Dict[str, Any]) -> None:
        presi.add(_chiave_url(it))
        all_items.append(it)

    # 1) Per ogni ticker portfolio
    for tk in tickers:
        if fermato():
            break
        items = _nuovi(search_news_for_ticker(tk, days=days, max_per_source=max_per_ticker))
        _somma_tagli(tagli_qualita, conta_tagli_qualita(items, max_per_ticker * 2))
        for it in items[:max_per_ticker * 2]:
            it["ticker_mentioned"] = tk
            it["theme"] = ""
            _prendi(it)

    # 2) Per ogni tema macro chiave
    macro_themes = [
        ("fed", "Federal Reserve OR FOMC OR Powell"),
        ("ecb", "ECB OR Lagarde OR euro rates"),
        ("ukraine", "Ukraine Russia war"),
        ("middle_east", "Israel Iran OR Gaza OR Houthi"),
        ("cpi", "US CPI OR core PCE inflation"),
        ("btc_etf", "Bitcoin ETF OR IBIT OR spot BTC"),
        ("china", "China economy OR Taiwan tariffs"),
        ("italy", "Italy budget OR BTP spread OR Meloni"),
    ]
    for theme_id, q in macro_themes:
        if fermato():
            break
        items = _nuovi(search_news_global(q, days=days, max_per_source=max_per_theme))
        _somma_tagli(tagli_qualita, conta_tagli_qualita(items, max_per_theme))
        for it in items[:max_per_theme]:
            it["ticker_mentioned"] = ""
            it["theme"] = theme_id
            _prendi(it)

    # 2.4) Top headlines business GNews (10/10): il feed generale di mercato, al posto di
    # Tiingo spento. Un fermo del backend lo salta come gli altri passi.
    if not fermato():
        for it in _nuovi(_fetch_gnews_top(10, days)):
            it.setdefault("ticker_mentioned", "")
            it.setdefault("theme", "")
            _prendi(it)

    # 2.5) Feed generale Tiingo (#173). 10/10: SPENTA per decisione PM -> _fetch_tiingo non fa
    # rete e torna []; resta qui per la riattivazione a interruttore (TIINGO_NEWS_ENABLED=1).
    # 04/10 (B2): era `except Exception: pass`; ora il guasto e' dichiarato (_fetch_tiingo)
    for it in _nuovi(_fetch_tiingo(None, days, 10)):
        it.setdefault("ticker_mentioned", "")
        it.setdefault("theme", "")
        _prendi(it)

    # 3) Deduplica + filtro qualita' (199e)
    all_items = _drop_junk(_dedupe_titoli(_dedupe(all_items)))

    # 4) Classifica + salva (connessione e titoli del feed aperti/letti prima del tetto)
    # P2-2: un item con URL-redirect (Finnhub) il cui titolo e' gia' nel feed delle ultime 48h
    # e' lo stesso articolo arrivato in un giro precedente da un'altra fonte: il controllo per
    # url non lo vede. v2: lo toglie gia' candidati_nuovi; qui resta la cintura.
    titoli_recenti = titoli_feed
    saved = 0
    classified = 0
    classification_attempted = 0
    classification_failed = 0
    # v2: i doppioni tolti prima del tetto (url/titolo gia' nel feed) sono doppioni saltati
    skipped = tagli_qualita["gia_nel_feed"]
    not_classified = 0
    summary_errors = []
    for it in all_items:
        if fermato():
            break
        url = (it.get("url") or "").strip()
        if not url:
            # genera fake url-key da hash title
            url = "nourl:" + hashlib.md5((it.get("title", "") or "").encode()).hexdigest()
        # check duplicate
        cur.execute("SELECT 1 FROM news_feed WHERE url = ?", (url,))
        if cur.fetchone():
            skipped += 1
            continue
        if _url_redirect(url) and _titolo_norm(it.get("title", "")) in titoli_recenti:
            skipped += 1
            with _ESITI_LOCK:
                _DOPPIONI_GIRO["titolo_db"] += 1
            continue
        # G3 (04/10): senza classificazione riuscita la riga entra con classified=0 e
        # sentiment/score/relevance NULL, mai il «neutral/5» finto di prima.
        cls = {"sentiment": None, "sentiment_score": None, "relevance": None, "headline_it": "", "why_matters": ""}
        if classify:
            classification_attempted += 1
            try:
                cls = _classify_with_haiku(it, ctx_weighted or tickers)
                if cls.get("_classification_status") == "classified":
                    classified += 1
                else:
                    classification_failed += 1
            except Exception:
                classification_failed += 1
        ok_cls = cls.get("_classification_status") == "classified"
        try:
            cur.execute("""
                INSERT INTO news_feed
                  (title, snippet, source, url, published_at, ticker_mentioned, theme,
                   provider, sentiment, sentiment_score, relevance, headline_it, why_matters,
                   classified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                (it.get("title") or "")[:500],
                (it.get("snippet") or "")[:1000],
                (it.get("source") or "")[:200],
                url[:500],
                (it.get("published_at") or "")[:50],
                (it.get("ticker_mentioned") or "")[:30],
                (it.get("theme") or "")[:30],
                (it.get("provider") or "")[:50],
                str(cls.get("sentiment") or "")[:16] if ok_cls else None,
                cls.get("sentiment_score") if ok_cls else None,
                int(cls["relevance"]) if ok_cls else None,
                (cls.get("headline_it") or "")[:120] if capture_language() == "it" else "",
                (cls.get("why_matters") or "")[:180] if capture_language() == "it" else "",
                1 if ok_cls else 0,
            ))
            saved += 1
            if not ok_cls:
                not_classified += 1
            # audit/11 §2: commit per-INSERT — prima la transazione di scrittura restava
            # aperta per TUTTE le chiamate Haiku successive (1-15s l'una): ogni altro
            # writer (price updater, /trade, heartbeat) andava in database-is-locked.
            conn.commit()
            try:
                stored_original = {"title": (it.get("title") or "")[:500],
                                   "snippet": (it.get("snippet") or "")[:1000], "url": url[:500]}
                _save_summary(db.db_path, stored_original, {**cls, "language": capture_language(),
                              "headline": cls.get("headline", cls.get("headline_it", ""))})
            except Exception as exc:
                summary_errors.append(str(exc))
                _log(f"generated summary cache failed: {exc}")
        except Exception as e:
            _log(f"insert failed: {e}")
            continue
    conn.commit()
    conn.close()

    # Opus 4.8 15/07 (P0): stato provider misurato DOPO il giro (il budget puo' esaurirsi
    # a meta'). Senza questo il task scriveva "OK fetched=76" mentre i 3 provider erano
    # muti da ore e i 76 item venivano tutti da RSS/yfinance/tiingo: un log che dichiara
    # OK su un giro cieco e' peggio di un log assente.
    fuori = providers_blocked()
    # 04/10 (B2): la provenienza ora si MISURA (ogni item porta `provider`); gli articoli
    # GNews serviti dalla cache del cooldown si contano a parte, non come chiamate vere
    provenienza = dict(Counter(
        ((it.get("provider") or "ignoto") + (" (cache)" if "_gnews_cache_eta_s" in it else ""))
        for it in all_items))
    fonti_esito = {"tiingo": _esito_nel_giro("tiingo"), "finnhub": _esito_nel_giro("finnhub")}
    # fonti tolte per decisione: nel giro col loro codice (SPENTA), ma non «guaste»
    for p, motivo in fonti_spente().items():
        fonti_esito[p] = str(motivo).split(":", 1)[0]
    esiti_muti = {p: s for p, s in fonti_esito.items() if s not in ("live", "non_interrogata", "SPENTA")}
    out = {
        "fetched": len(all_items),
        "classified": classified,
        "classification_attempted": classification_attempted,
        "classification_failed": classification_failed,
        "saved": saved,
        "not_classified": not_classified,
        "skipped_duplicates": skipped,
        "interrupted": bool(interrotto),
        "language": capture_language(),
        "summary_errors": summary_errors,
        "providers_blocked": fuori,
        "provenienza": provenienza,
        "fonti_esito": fonti_esito,
        # M1 (04/10): articoli Finnhub scartati dal tetto per ticker e ticker troncati dal
        # fornitore (250). Conta solo le chiamate vere: un ticker servito dalla cache 15 min
        # di search_news_for_ticker non richiama Finnhub e non si ri-conta.
        "finnhub_tagli": _tagli_finnhub(),
        # P2-2: doppioni tolti per titolo nel giro (fra fonti) e contro il feed delle 48h
        "doppioni_titolo": _doppioni_giro(),
        # 10/10: ordine per qualita' della testata prima del tetto per ticker/tema. Le testate
        # «bassa» non si scartano: se il tetto le taglia, qui si contano (e nel log).
        "ranking_testate": tagli_qualita,
        "degraded": bool(fuori or summary_errors or interrotto or esiti_muti) or classification_failed > 0
                    or not_classified > 0,
    }
    if tagli_qualita["oltre_tetto"]:
        _log("ranking testate: %d notizie NUOVE oltre il tetto per ticker/tema (di cui %d da testate "
             "a bassa qualita', depriorizzate e non scartate a monte); testate basse tenute: %d"
             % (tagli_qualita["oltre_tetto"], tagli_qualita["bassa_oltre_tetto"], tagli_qualita["bassa_tenute"]))
    if tagli_qualita["oltre_eta"] or tagli_qualita["feed_non_controllato"]:
        _log("candidati del giro: %d articoli piu' vecchi di %d giorni fuori dal ranking; %d senza "
             "data (in coda); %d non controllati contro il feed (errore di lettura)"
             % (tagli_qualita["oltre_eta"], days, tagli_qualita["senza_data"],
                tagli_qualita["feed_non_controllato"]))
    if out["finnhub_tagli"]["scartati"] or out["finnhub_tagli"]["troncati_dal_fornitore"]:
        _log("finnhub nel giro: scartati %d articoli (tetto %d per ticker); troncati dal fornitore: %s"
             % (out["finnhub_tagli"]["scartati"], FINNHUB_MAX_PER_TICKER,
                ", ".join(out["finnhub_tagli"]["troncati_dal_fornitore"]) or "nessuno"))
    if fuori or esiti_muti:
        contingentati_muti = [p for p in NEWS_PROVIDER_LIMITS if p in fuori]
        _log("DEGRADATO: %d/%d provider contingentati muti; fonti mute: %s; esiti del giro: %s. "
             "Provenienza dei %d item (misurata): %s"
             % (len(contingentati_muti), len(NEWS_PROVIDER_LIMITS),
                ", ".join("%s=%s" % kv for kv in sorted(fuori.items())) or "nessuna",
                ", ".join("%s=%s" % kv for kv in sorted(fonti_esito.items())),
                len(all_items),
                ", ".join("%s=%d" % kv for kv in sorted(provenienza.items())) or "nessun item"))
    # P2 (12/08): ora+esito del giro diventano un fatto leggibile da
    # GET /news/providers — un feed fermo non ha piu' la stessa faccia di
    # un feed sano. Il fallimento della scrittura NON uccide il giro
    # (le news sono gia' salvate): si dichiara nel log e basta.
    try:
        _scrivi_stato_giro(out)
    except Exception as e:
        _log(f"stato giro NON scritto (dichiarato): {type(e).__name__}: {e}")
    return out
