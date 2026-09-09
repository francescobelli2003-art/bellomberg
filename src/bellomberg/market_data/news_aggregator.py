"""
BELLOMBERG - News Aggregator

Pulla news multi-fonte per ticker singolo, lista ticker portfolio, o query libera:
- NewsAPI (newsapi.org)
- Marketaux (marketaux.com)
- TheNewsAPI (thenewsapi.com)
- GNews (gnews.io)
- yfinance news (per ticker)
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
news_search_terms.example.json. Nessun simbolo del book vive in questo sorgente.
"""
from bellomberg.core.paths import DATA_DIR, PROJECT_ROOT
import time
import re
import hashlib
from datetime import datetime, timedelta
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


def _log(msg: str):
    # pipe stdout morta (backend zombie, autopsia (40)): log perso, mai eccezione
    try:
        print(f"[NEWS] {msg}", flush=True)
    except OSError:
        pass


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
NEWS_PROVIDER_LIMITS = {
    "newsapi":    {"daily": 80, "cooldown": 6 * 3600, "disable": 12 * 3600},
    "thenewsapi": {"daily": 80, "cooldown": 6 * 3600, "disable": 12 * 3600},
    # Opus 4.8 16/07: GNews su piano ESSENTIAL (verificato live: max=25 ok, news di 12
    # min fa, storico attivo, stessa key). Tetto 80->800 (20% sotto il cap di piano 1000):
    # GNews non si spegne piu' a meta' mattina. Cooldown 6h->2h per SFRUTTARE il real-time:
    # ogni query si aggiorna ogni 2h invece di 6h (~360 chiamate/g stimate, sotto 800).
    # newsapi/thenewsapi restano a 80 (piano free): l'upgrade copre 1 fonte su 3.
    "gnews":      {"daily": 800, "cooldown": 2 * 3600, "disable": 12 * 3600},
}

# Opus 4.8 16/07: Essential concede 25 articoli/richiesta (free: 10). GNews e' real-time,
# quindi i suoi articoli sono i piu' recenti e vincono l'ordinamento per data (:588) nel
# feed: gli chiediamo un bacino ampio SOLO a lui, senza gonfiare le altre fonti (tiingo,
# 87% del rumore, resta a max_per_source). Cap del piano: 25.
GNEWS_MAX_ART = 25


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


def provider_status(provider: str, query: str) -> Optional[str]:
    """Opus 4.8 15/07 (P0 skip dichiarato): il MOTIVO per cui il provider e' bloccato,
    o None se la chiamata puo' partire. Il bool di _provider_allowed collassava 3 cause
    diverse in un unico False, e il chiamante restituiva [] muto: 'zero notizie' e 'sono
    cieco' erano lo stesso valore. Vocabolario CHIUSO (come llm_pricing 'live|fallback|
    n.d.'), niente 'ok' inventato: qui si dichiara solo cio' che il limiter SA.
      SKIP_DISABLED — auto-disable dopo un 429/402/403 (:175-177), scade da solo
      SKIP_BUDGET   — budget giornaliero esaurito (il ramo che scatta oggi: 80/80)
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
    q_ts = float(st.get("per_query", {}).get(query.lower().strip()[:80], 0))
    if now - q_ts < lim["cooldown"]:
        return "SKIP_COOLDOWN"
    return None


def _provider_allowed(provider: str, query: str) -> bool:
    # Firma invariata di proposito: 5 call site vivi la usano come bool (:185, :315,
    # :347 + news_sources via _rate_limiter). Il motivo si chiede a provider_status().
    return provider_status(provider, query) is None


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
            fuori[p] = "SENZA_CHIAVE: %s assente nel .env" % var
            continue
        st = provider_status(p, _probe)
        if st in ("SKIP_BUDGET", "SKIP_DISABLED"):
            fuori[p] = st
    # Tiingo: chiave dedicata, fuori dai limiti giornalieri (tiingo_news.tiingo_available)
    try:
        from bellomberg.market_data.tiingo_news import tiingo_available
        if not tiingo_available():
            fuori["tiingo"] = "SENZA_CHIAVE: TIINGO_API_KEY assente nel .env (o requests non importabile)"
    except Exception as e:
        fuori["tiingo"] = "MODULO_ASSENTE: tiingo_news non importabile (%s)" % type(e).__name__
    # il negozio dei termini: senza, i nomi europei si cercano col ticker nudo (dichiarato nel
    # log, ma il payload diceva solo «0 news»): e' una fonte muta del giro, e qui lo dice
    c = carica_termini()
    if c["origine"] in ("assente", "illeggibile"):
        fuori[TERMINI_MUTI] = "NEGOZIO_%s: %s" % (c["origine"].upper(), c["motivo"])
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
            fuori[TEMI_TITOLI_MUTI] = "NEGOZIO_%s: %s" % (tt["origine"].upper(), tt["motivo"])
    except Exception as e:
        fuori[TEMI_TITOLI_MUTI] = ("MODULO_ASSENTE: negozi_privati non importabile (%s: %s)"
                                   % (type(e).__name__, e))
    return fuori


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
    if len(pq) > 60:
        pq = dict(sorted(pq.items(), key=lambda kv: kv[1], reverse=True)[:60])
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
        _log(f"newsapi failed: {e}")
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
        _log(f"marketaux failed: {e}")
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
        _log(f"thenewsapi failed: {e}")
        return []


def _fetch_gnews(query: str, days: int = 3, max_results: int = 10, lang: str = "en") -> List[Dict[str, Any]]:
    if not (REQ_OK and GNEWS_KEY):
        return []
    if not _provider_allowed("gnews", query):
        return []
    try:
        from bellomberg.market_data.news_sources import gnews_safe_query  # quotatura token con -/$/. (400 syntax, diagnosi 15/07)
        url = "https://gnews.io/api/v4/search"
        params = {
            "q": gnews_safe_query(query),
            "from": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lang": lang,
            "max": max_results,
            "apikey": GNEWS_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        _provider_record("gnews", query, got_429=r.status_code in (403, 429))
        if r.status_code != 200:
            _log(f"gnews HTTP {r.status_code}: {r.text[:200]}")
            return []
        data = r.json()
        return [{
            "title": a.get("title", ""),
            "source": a.get("source", {}).get("name", "GNews"),
            "url": a.get("url", ""),
            "published_at": a.get("publishedAt", ""),
            "snippet": a.get("description", ""),
            "provider": "gnews",
        } for a in data.get("articles", [])[:max_results]]
    except Exception as e:
        _log(f"gnews failed: {e}")
        return []


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
# scritti; voce assente = ticker nudo, DICHIARATO nel log; mai un ripiego muto.

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
    if not os.path.exists(p):
        return {"termini": {}, "origine": "assente",
                "motivo": "negozio non trovato: %s (copia %s in data/ e mettici le TUE societa')"
                          % (p, _ESEMPIO_TERMINI)}
    try:
        with open(p, encoding="utf-8") as fh:
            grezzo = json.load(fh)
    except Exception as e:
        return {"termini": {}, "origine": "illeggibile",
                "motivo": "%s: %s" % (type(e).__name__, e)}
    if not isinstance(grezzo, dict):
        return {"termini": {}, "origine": "illeggibile",
                "motivo": "il negozio non e' un oggetto JSON ma %s" % type(grezzo).__name__}
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
                    "motivo": ("chiave %r non canonica o doppia: scrivila MAIUSCOLA, senza "
                               "spazi e una volta sola (%r)" % (k, kk))}
        if v is None:
            termini[k] = None
            continue
        if (not isinstance(v, list) or not v
                or not all(isinstance(x, str) and x.strip() for x in v)):
            return {"termini": {}, "origine": "illeggibile",
                    "motivo": ("voce %r malformata: serve una lista non vuota di termini "
                               "(stringhe), oppure null per escludere il simbolo dalle news" % k)}
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
    si cerca col ticker nudo (dichiarato). `termini` gia' caricati = una lettura sola."""
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


def _filter_items_by_terms(items: List[Dict[str, Any]],
                            terms: List[str]) -> List[Dict[str, Any]]:
    """Post-filter word-boundary (#160): tiene solo gli item che menzionano
    davvero uno dei terms in titolo o snippet. yfinance e' symbol-based: fidato."""
    pats = [re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE) for t in terms]
    out = []
    for it in items:
        if (it.get("provider") or "") in ("yfinance", "tiingo"):
            out.append(it)  # fonti symbol-based/taggate: fidate
            continue
        text = (it.get("title", "") or "") + " " + (it.get("snippet", "") or "")
        if any(p.search(text) for p in pats):
            out.append(it)
    return out


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
    Voce assente = ticker nudo, dichiarato nel log. Voce null = fuori dal GIRO automatico
    (search_portfolio_news, eventi SEC, auto_pull_feed), ma la ricerca DIRETTA procede col
    ticker nudo come a HEAD: un [] muto qui sarebbe identico a «zero notizie» per chi
    chiama (review 04/09)."""
    cache_key = f"ticker:{ticker}:{days}:{max_per_source}"
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return entry["data"]

    voce, stato, caricato = _voce_termini(ticker)
    if stato == "escluso":
        _log(f"{ticker}: voce null nel negozio ({caricato['origine']}): fuori dal giro "
             f"automatico, ma la ricerca diretta procede col ticker nudo")
        terms = [ticker]
    elif stato == "assente":
        motivo = f"; motivo: {caricato['motivo']}" if caricato["motivo"] else ""
        _log(f"{ticker}: voce assente nel negozio dei termini (origine: "
             f"{caricato['origine']}{motivo}): cerco il ticker nudo, che per i "
             f"simboli europei le API non trovano")
        terms = [ticker]  # il ticker nudo, dichiarato sopra
    else:
        terms = voce
    query = " OR ".join(f'"{t}"' if " " in t else t for t in terms)

    items: List[Dict[str, Any]] = []
    items.extend(_fetch_newsapi(query, days, max_per_source))
    # items.extend(_fetch_marketaux(terms[0], days, max_per_source))  # 199d: Marketaux sganciato (402/ridondante con Tiingo); riattivabile decommentando
    items.extend(_fetch_thenewsapi(query, days, max_per_source))
    items.extend(_fetch_gnews(query, days, GNEWS_MAX_ART))  # Essential: bacino ampio, i freschi vincono il sort :599
    if ticker.upper().endswith(".MI"):  # 199e: stampa italiana per i nomi italiani
        items.extend(_fetch_gnews(query, days, GNEWS_MAX_ART, lang="it"))
    items.extend(_fetch_yfinance_news(ticker, max_per_source))
    if "." not in ticker:  # Tiingo (#173): tagging affidabile per simboli US
        try:
            from bellomberg.market_data.tiingo_news import fetch_tiingo_news, tiingo_available
            if tiingo_available():
                items.extend(fetch_tiingo_news([ticker], days=days, limit=max_per_source))
        except Exception:
            pass

    items.extend(_all_rss_cached())  # 199e: copertura EU - gli RSS vengono filtrati dai terms qui sotto
    items = _drop_junk(items)
    items = _filter_items_by_terms(items, terms)
    for it in items:
        it["ticker_mentioned"] = ticker
    items = _dedupe(items)
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)

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

    items = _dedupe(items)
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)

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
            return entry["data"]

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

    # Reddit (top of the day, filtered by topic keywords)
    if include_reddit:
        try:
            from bellomberg.market_data.reddit_news import fetch_reddit_top
            reddit_posts = fetch_reddit_top(listing="top", t="day")
            for r in reddit_posts[:25]:
                # Match topic if title/snippet contains keyword
                text_low = (r.get("title", "") + " " + r.get("snippet", "")).lower()
                for kw, topic in all_keywords:
                    if kw in text_low:
                        r["topic_id"] = topic["id"]
                        r["topic_label"] = topic["label"]
                        r["topic_category"] = topic["category"]
                        r["topic_importance"] = topic.get("importance", 3)
                        r["tickers_affected"] = list(titoli.get(topic["id"], ()))
                        items.append(r)
                        break
        except Exception as e:
            _log(f"reddit aggregation failed: {e}")

    items = _dedupe(items)
    # Sort: importance desc, then recency desc
    items.sort(key=lambda x: (
        -(x.get("topic_importance", 3)),
        -(_parse_date_ts(x.get("published_at", ""))),
    ))

    _CACHE[cache_key] = {"ts": time.time(), "data": items}
    return items


def fetch_corporate_events(days: int = 14, max_items: int = 30) -> List[Dict[str, Any]]:
    """Eventi societari high-impact dal portfolio: 8-K + Form 4 insider via SEC EDGAR
    + news M&A/earnings via NewsAPI.
    """
    cache_key = f"corp_events:{days}:{max_items}"
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return entry["data"]

    items: List[Dict[str, Any]] = []

    # 1. SEC EDGAR per ticker US del portfolio
    try:
        from bellomberg.market_data.sec_edgar import get_corporate_events_for_portfolio
        db = MemoryDB()
        snap = db.get_portfolio_summary()
        positions = snap.get("positions", [])
        # Solo ticker US (no suffix .MI, .L, .DE)
        termini = _termini_del_giro("fetch_corporate_events")
        us_tickers = [p["ticker"] for p in positions
                      if p.get("ticker") and "." not in p.get("ticker", "")
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
    return items


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
    except Exception as e:
        _log(f"top_global newsapi failed: {e}")

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
            where.append("COALESCE(relevance, 0) >= ?")
            params.append(min_relevance)
        if ticker:
            where.append("ticker_mentioned = ?")
            params.append(ticker.upper())
        if sentiment:
            where.append("sentiment = ?")
            params.append(sentiment.lower())
        extra_cols = ", headline_it, why_matters"
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
            # pre-migrazione (colonne 201 assenti): query legacy
            sql = sql.replace(extra_cols, "")
            cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        # 201-C: MATERIALITA' = relevance + peso posizione + freschezza (il book comanda)
        weights = _portfolio_weights_cached()
        favs = _favorites_tickers()  # T4-3: boost preferiti nel ranking (non nasconde il resto)
        now_ts = _dt.now().timestamp()
        for r in rows:
            rel = float(r.get("relevance") or 5)
            w = float(weights.get((r.get("ticker_mentioned") or "").upper(), 0.0))
            age_h = max(0.0, (now_ts - _parse_date_ts(r.get("pulled_at") or "")) / 3600.0) if r.get("pulled_at") else 48.0
            fresh = 6.0 if age_h <= 6 else (3.0 if age_h <= 24 else 0.0)
            fav_b = 6.0 if (r.get("ticker_mentioned") or "").upper() in favs else 0.0
            r["_materiality"] = round(rel * 2.0 + min(w, 25.0) * 0.4 + fresh + fav_b, 2)
            # 201-A: il pannello mostra la headline da desk; originali conservati
            if r.get("headline_it"):
                r["title_original"] = r.get("title")
                r["title"] = r["headline_it"]
            if r.get("why_matters"):
                r["snippet_original"] = r.get("snippet")
                r["snippet"] = r["why_matters"]
        rows.sort(key=lambda x: (-(x.get("_materiality") or 0), x.get("pulled_at") or ""), )
        return rows[: int(limit)]
    except Exception as e:
        _log(f"get_feed failed: {e}")
        return []


def invalidate_cache():
    _CACHE.clear()


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


def _classify_with_haiku(item: Dict[str, Any], context_tickers: List[str]) -> Dict[str, Any]:
    """Classifica con Haiku: sentiment + relevance score 1-10.
    Retry policy: 3 retries con backoff esponenziale per 529/429/5xx.
    """
    import time as _time
    import random
    try:
        # 05/09 (ordine PM): OpenRouter, modello dal .env (NEWS_CLASSIFIER_MODEL). Chiave o
        # variabile assente: la causa va nel log col NOME e la notizia resta "neutral"
        # come prima (il ripiego neutro preesiste, 8c ne e' proprietaria: qui solo la chiamata).
        from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm, ConfigurazioneLLMMancante
        try:
            _modello_classificatore = _modello_llm("news_classifier")
            client = OpenRouterClient()
        except ConfigurazioneLLMMancante as e:
            _log(f"classify SKIPPED ({e}) - news defaults to neutral")
            return {"sentiment": "neutral", "sentiment_score": 0.0, "relevance": 5, "headline_it": "", "why_matters": ""}
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
        prompt = NEWS_SENTIMENT_PROMPT.format(title=title, snippet=snippet, tk_tag=tk_tag,
                                              theme_tag=theme_tag, tickers_str=tickers_str)

        MAX_RETRIES = 3
        BASE_DELAY = 2.0
        last_err = None
        msg = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                msg = client.messages.create(
                    model=_modello_classificatore,
                    max_tokens=320,
                    # 05/09 (sonda): senza questo il classificatore spende ~250 dei 320 token a
                    # ragionare e il JSON esce troncato (-> neutral zitto); «spento» su un
                    # modello che lo rifiuta diventa effort minimal, dichiarato da llm_client.
                    thinking={"type": "disabled"},
                    messages=[{"role": "user", "content": prompt}],
                )
                break
            except Exception as e:
                last_err = e
                err_str = str(e)
                status = getattr(e, "status_code", None)
                if status is None:
                    if "529" in err_str or "overloaded" in err_str.lower():
                        status = 529
                    elif "429" in err_str or "rate_limit" in err_str.lower():
                        status = 429
                retriable = status in (429, 529) or (status is not None and 500 <= status < 600)
                if not retriable or attempt == MAX_RETRIES:
                    raise
                delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                _log(f"haiku classify {status} - retry {attempt+1}/{MAX_RETRIES} in {delay:.1f}s")
                _time.sleep(delay)

        if msg is None:
            if last_err:
                raise last_err
            return {"sentiment": "neutral", "sentiment_score": 0.0, "relevance": 5, "headline_it": "", "why_matters": ""}

        txt = msg.content[0].text.strip()
        if txt.startswith("```"):
            txt = txt.split("```")[1]
            if txt.startswith("json"):
                txt = txt[4:]
        import json as _json
        data = _json.loads(txt.strip())
        return {
            "sentiment": str(data.get("sentiment", "neutral"))[:16],
            "sentiment_score": float(data.get("sentiment_score", 0)),
            "relevance": int(data.get("relevance", 5)),
            "headline_it": str(data.get("headline_it", "") or "")[:120],
            "why_matters": str(data.get("why_matters", "") or "")[:180],
        }
    except Exception as e:
        err_str = str(e)
        if "529" in err_str or "overloaded" in err_str.lower():
            _log(f"haiku classify SKIPPED (Anthropic overloaded after retries) - news defaults to neutral")
        else:
            _log(f"haiku classify failed: {e}")
        return {"sentiment": "neutral", "sentiment_score": 0.0, "relevance": 5, "headline_it": "", "why_matters": ""}


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
        "saved": out.get("saved"),
        "skipped_duplicates": out.get("skipped_duplicates"),
        "providers_blocked": out.get("providers_blocked") or {},
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
                "saved": raw.get("saved"),
                "skipped_duplicates": raw.get("skipped_duplicates"),
                "providers_blocked": raw.get("providers_blocked") or {}}
    except Exception as e:
        return {"stato": "illeggibile", "motivo": f"{type(e).__name__}: {e}"}


def auto_pull_feed(days: int = 1, classify: bool = True,
                    max_per_ticker: int = 5, max_per_theme: int = 5) -> Dict[str, Any]:
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

    # 1) Per ogni ticker portfolio
    for tk in tickers:
        items = search_news_for_ticker(tk, days=days, max_per_source=max_per_ticker)
        for it in items[:max_per_ticker * 2]:
            it["ticker_mentioned"] = tk
            it["theme"] = ""
            all_items.append(it)

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
        items = search_news_global(q, days=days, max_per_source=max_per_theme)
        for it in items[:max_per_theme]:
            it["ticker_mentioned"] = ""
            it["theme"] = theme_id
            all_items.append(it)

    # 2.5) Feed generale Tiingo (#173) — fonte premium, top news mercato
    try:
        from bellomberg.market_data.tiingo_news import fetch_tiingo_news, tiingo_available
        if tiingo_available():
            for it in fetch_tiingo_news(None, days=days, limit=10):
                it.setdefault("ticker_mentioned", "")
                it.setdefault("theme", "")
                all_items.append(it)
    except Exception:
        pass

    # 3) Deduplica + filtro qualita' (199e)
    all_items = _drop_junk(_dedupe(all_items))

    # 4) Classifica + salva
    conn = connect_sqlite(db.db_path)  # hardening #32: WAL + busy_timeout
    cur = conn.cursor()
    saved = 0
    classified = 0
    skipped = 0
    for it in all_items:
        url = (it.get("url") or "").strip()
        if not url:
            # genera fake url-key da hash title
            url = "nourl:" + hashlib.md5((it.get("title", "") or "").encode()).hexdigest()
        # check duplicate
        cur.execute("SELECT 1 FROM news_feed WHERE url = ?", (url,))
        if cur.fetchone():
            skipped += 1
            continue
        cls = {"sentiment": "neutral", "sentiment_score": 0.0, "relevance": 5, "headline_it": "", "why_matters": ""}
        if classify:
            try:
                cls = _classify_with_haiku(it, ctx_weighted or tickers)
                classified += 1
            except Exception:
                pass
        try:
            cur.execute("""
                INSERT INTO news_feed
                  (title, snippet, source, url, published_at, ticker_mentioned, theme,
                   provider, sentiment, sentiment_score, relevance, headline_it, why_matters)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                (it.get("title") or "")[:500],
                (it.get("snippet") or "")[:1000],
                (it.get("source") or "")[:200],
                url[:500],
                (it.get("published_at") or "")[:50],
                (it.get("ticker_mentioned") or "")[:30],
                (it.get("theme") or "")[:30],
                (it.get("provider") or "")[:50],
                cls.get("sentiment", "neutral")[:16],
                float(cls.get("sentiment_score", 0)),
                int(cls.get("relevance", 5)),
                (cls.get("headline_it") or "")[:120],
                (cls.get("why_matters") or "")[:180],
            ))
            saved += 1
            # audit/11 §2: commit per-INSERT — prima la transazione di scrittura restava
            # aperta per TUTTE le chiamate Haiku successive (1-15s l'una): ogni altro
            # writer (price updater, /trade, heartbeat) andava in database-is-locked.
            conn.commit()
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
    out = {
        "fetched": len(all_items),
        "classified": classified,
        "saved": saved,
        "skipped_duplicates": skipped,
        "providers_blocked": fuori,
        "degraded": bool(fuori),
    }
    if fuori:
        # NB: gli item NON sono taggati per provenienza (search_news_for_ticker mescola
        # provider contingentati e non in un'unica lista), quindi "da dove vengono i 76"
        # il codice NON lo sa. Si dichiara solo il fatto misurato: chi era muto. Con un
        # blocco PARZIALE gli altri provider hanno contribuito e dire "vengono tutti da
        # rss/yfinance" sarebbe inventare uno stato — cioe' il bug che questa voce chiude.
        tutti = len(fuori) == len(NEWS_PROVIDER_LIMITS)
        _log("DEGRADATO: %d/%d provider contingentati muti (%s). %s"
             % (len(fuori), len(NEWS_PROVIDER_LIMITS),
                ", ".join("%s=%s" % kv for kv in sorted(fuori.items())),
                ("Nessuno dei %d item puo' venire da loro: restano le sole fonti non "
                 "contingentate (rss/yfinance/tiingo)." % len(all_items)) if tutti else
                ("I %d item sono un PARZIALE: provenienza per-fonte non misurata."
                 % len(all_items))))
    # P2 (12/08): ora+esito del giro diventano un fatto leggibile da
    # GET /news/providers — un feed fermo non ha piu' la stessa faccia di
    # un feed sano. Il fallimento della scrittura NON uccide il giro
    # (le news sono gia' salvate): si dichiara nel log e basta.
    try:
        _scrivi_stato_giro(out)
    except Exception as e:
        _log(f"stato giro NON scritto (dichiarato): {type(e).__name__}: {e}")
    return out
