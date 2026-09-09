"""
Multi-source news fetcher.

Aggrega news da fonti diverse per non dipendere da una sola API.
Tutte le fonti sono free tier (100 req/day cad).

Fonti:
- NewsAPI (chiamata direttamente da news_aggregator; il modulo v1 fetcher.py e' in attic)
- Yahoo Finance (via yfinance.Ticker.news, no API key, free real-time)
- Marketaux (focus finance, real-time, 100/day)
- TheNewsAPI (general purpose, real-time, 100/day)
- GNews (basato su Google News, real-time, 100/day)

Ogni fetcher e' robusto a errori: se l'API e' down o senza key, ritorna [] — ma
DICHIARA il motivo in `last_status()` (Opus 4.8 15/07): una lista vuota da sola non
distingue "nessuna notizia" da "sono cieco", e la seconda va detta, non dedotta.
"""
import threading
from typing import Dict

import requests

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

from bellomberg.core.config import (
    MARKETAUX_API_KEY,
    THENEWSAPI_API_KEY,
    GNEWS_API_KEY,
    USE_YFINANCE_NEWS,
)


def fetch_yfinance_news(ticker, max_news=10):
    """
    News da Yahoo Finance via yfinance.Ticker.news.
    GRATIS, no API key, real-time. Funziona per ticker US e EU principali.
    """
    if not USE_YFINANCE_NEWS or not YFINANCE_AVAILABLE:
        _record_status("yfinance", "DISABLED" if not USE_YFINANCE_NEWS else "NO_LIB")
        return []

    risultati = []
    _ok = False
    try:
        tk = yf.Ticker(ticker)
        news_list = tk.news or []
        for item in news_list[:max_news]:
            # yfinance restituisce un dict con struttura variabile a seconda della versione
            # Cerchiamo i campi comuni
            content = item.get("content", item) if isinstance(item.get("content"), dict) else item

            titolo = content.get("title", item.get("title", ""))
            descrizione = content.get("summary", content.get("description", item.get("summary", "")))
            url = content.get("canonicalUrl", {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else item.get("link", "")
            publisher = content.get("provider", {}).get("displayName") if isinstance(content.get("provider"), dict) else item.get("publisher", "Yahoo Finance")
            pub_date = content.get("pubDate", item.get("providerPublishTime", ""))

            if titolo:
                risultati.append({
                    "ticker_associato": ticker,
                    "titolo": str(titolo),
                    "descrizione": str(descrizione)[:500],
                    "fonte": "Yahoo Finance (" + str(publisher) + ")",
                    "url": str(url) if url else "",
                    "data": str(pub_date),
                })
        _ok = True
    except Exception as e:
        print("  [!] Errore yfinance news " + ticker + ": " + str(e))

    # Opus 4.8 15/07: il return e' FUORI dal try, quindi un'eccezione a meta' giro
    # restituisce comunque i risultati parziali gia' raccolti. Comportamento invariato
    # (non e' questo il perimetro), ma ora e' DICHIARATO: 'ERROR' anche con lista piena.
    _record_status("yfinance", "live" if _ok else "ERROR")
    return risultati


def _rate_limiter():
    """Budget/cooldown condivisi con news_aggregator (#172). audit/11 §4: questi fetcher
    (usati dagli agenti via agent_tools) bruciavano la quota SENZA passare dal limiter.
    Fallback no-op se news_aggregator non e' importabile."""
    try:
        from bellomberg.market_data.news_aggregator import _provider_allowed, _provider_record
        return _provider_allowed, _provider_record
    except Exception:
        return (lambda p, q: True), (lambda p, q, got_429=False: None)


# --- Opus 4.8 15/07 (P0 skip dichiarato): registro esiti per-fonte -------------------
# I fetcher tornano [] per motivi diversi e indistinguibili (quota, HTTP error, chiave
# assente, zero risultati veri): il chiamante non poteva sapere quale, e "nessuna
# notizia" e "sono cieco" collassavano nello stesso valore. Il tipo di ritorno NON si
# puo' cambiare (i call site fanno .extend() sulla lista), quindi l'esito viaggia su un
# canale separato. thread-local: la run gira con 4 specialisti in parallelo e un dict
# globale verrebbe sporcato tra thread.
_status_tls = threading.local()


def reset_status() -> None:
    """Azzera il registro PRIMA di un giro di fetch (chiamare dal consumatore)."""
    _status_tls.esiti = {}


def last_status() -> Dict[str, str]:
    """Esiti dell'ultimo giro di fetch in QUESTO thread: {fonte: stato}.
    Una fonte ASSENTE dal dict non e' "andata bene": e' "mai interrogata o esito ignoto"
    — il consumatore deve dichiararla tale, mai assumerla sana."""
    return dict(getattr(_status_tls, "esiti", {}) or {})


def _record_status(fonte: str, stato: str) -> None:
    if not hasattr(_status_tls, "esiti") or _status_tls.esiti is None:
        _status_tls.esiti = {}
    _status_tls.esiti[fonte] = stato


def _provider_skip_reason(provider: str, query: str):
    """Motivo del blocco dal limiter condiviso (SKIP_BUDGET/SKIP_COOLDOWN/SKIP_DISABLED),
    o None se la chiamata puo' partire.
    ImportError -> None: si prova la chiamata, com'e' sempre stato (_rate_limiter fa lo
    stesso). Ogni ALTRO errore (es. stato su file con tipi corrotti) -> il limiter e'
    saltato: la chiamata parte comunque (comportamento invariato) ma l'anomalia va a log,
    perche' un limiter muto NON conta la quota. Non si registra qui uno stato-fonte: lo
    sovrascriverebbe il fetcher subito dopo, e una dichiarazione che sparisce inganna piu'
    del silenzio. L'esito della FONTE resta comunque vero (live = la chiamata e' riuscita).
    """
    try:
        from bellomberg.market_data.news_aggregator import provider_status
        return provider_status(provider, query)
    except ImportError:
        return None
    except Exception as e:
        print("  [!] limiter non interrogabile per " + provider + " (quota NON contata): " + str(e))
        return None


def fetch_marketaux(ticker=None, query=None, max_news=10):
    """
    News da Marketaux. API gratuita 100/day, focus finance.
    https://www.marketaux.com/documentation
    """
    if not MARKETAUX_API_KEY:
        _record_status("marketaux", "NO_KEY")
        return []
    _q = str(ticker or query or "")
    # NB: 'marketaux' NON e' in NEWS_PROVIDER_LIMITS, quindi questo skip non scatta MAI
    # (provider_status -> None per i provider non mappati: fail-open, nessuno lo conta).
    # Tenuto per simmetria e perche' diventerebbe vivo il giorno in cui lo si mappa.
    _skip = _provider_skip_reason("marketaux", _q)
    if _skip:
        _record_status("marketaux", _skip)
        return []

    url = "https://api.marketaux.com/v1/news/all"
    params = {
        "api_token": MARKETAUX_API_KEY,
        "language": "en",
        "limit": min(max_news, 3),  # free tier max 3 per request
    }

    # Marketaux supporta filtro per symbols (es. "AAPL,MSFT") o search (testo libero)
    if ticker and ticker.replace(".", "").replace("-", "").isalnum():
        # Estrai parte base del ticker (rimuovi suffisso .MI, .DE, .L, ecc.)
        base_ticker = ticker.split(".")[0]
        params["symbols"] = base_ticker
    elif query:
        params["search"] = query

    _, _record = _rate_limiter()
    try:
        r = requests.get(url, params=params, timeout=10)
        _record("marketaux", _q, got_429=r.status_code in (402, 429))
        if r.status_code != 200:
            print("  [!] Marketaux HTTP " + str(r.status_code))
            _record_status("marketaux", "HTTP_" + str(r.status_code))
            return []
        data = r.json()
        articoli = data.get("data", [])
        risultati = []
        for a in articoli:
            risultati.append({
                "ticker_associato": ticker or "macro",
                "titolo": a.get("title", "") or "",
                "descrizione": (a.get("description", "") or a.get("snippet", "") or "")[:500],
                "fonte": "Marketaux (" + (a.get("source", "Unknown")) + ")",
                "url": a.get("url", ""),
                "data": a.get("published_at", ""),
            })
        _record_status("marketaux", "live")
        return risultati
    except Exception as e:
        print("  [!] Errore Marketaux: " + str(e))
        _record_status("marketaux", "ERROR")
        return []


def fetch_thenewsapi(ticker=None, query=None, max_news=10):
    """
    News da TheNewsAPI. 100/day gratis, real-time, general purpose.
    https://www.thenewsapi.com/documentation
    """
    if not THENEWSAPI_API_KEY:
        _record_status("thenewsapi", "NO_KEY")
        return []
    _q = str(ticker or query or "")
    _skip = _provider_skip_reason("thenewsapi", _q)
    if _skip:
        _record_status("thenewsapi", _skip)
        return []

    url = "https://api.thenewsapi.com/v1/news/all"
    params = {
        "api_token": THENEWSAPI_API_KEY,
        "language": "en",
        "limit": min(max_news, 3),  # free tier 3 per req
    }

    if query:
        params["search"] = query
    elif ticker:
        params["search"] = ticker

    _, _record = _rate_limiter()
    try:
        r = requests.get(url, params=params, timeout=10)
        _record("thenewsapi", _q, got_429=r.status_code in (402, 429))
        if r.status_code != 200:
            print("  [!] TheNewsAPI HTTP " + str(r.status_code))
            _record_status("thenewsapi", "HTTP_" + str(r.status_code))
            return []
        data = r.json()
        articoli = data.get("data", [])
        risultati = []
        for a in articoli:
            risultati.append({
                "ticker_associato": ticker or "macro",
                "titolo": a.get("title", "") or "",
                "descrizione": (a.get("description", "") or a.get("snippet", "") or "")[:500],
                "fonte": "TheNewsAPI (" + (a.get("source", "Unknown")) + ")",
                "url": a.get("url", ""),
                "data": a.get("published_at", ""),
            })
        _record_status("thenewsapi", "live")
        return risultati
    except Exception as e:
        print("  [!] Errore TheNewsAPI: " + str(e))
        _record_status("thenewsapi", "ERROR")
        return []


def gnews_safe_query(query):
    """
    Il parser di GNews v4 rifiuta con 400 "query syntax error" i token con
    caratteri speciali (trattino '8-K'/'risk-off', dollaro '$467', punto
    'ALFA.MI') — diagnosi live 15/07: era la causa dei 400 sistematici.
    Quotare il token lo rende valido E lo mantiene nella ricerca
    ('"risk-off"' -> 135 risultati). I segmenti gia' quotati restano intatti.
    """
    import re
    q = str(query or "").strip()
    if not q:
        return q
    out = []
    for m in re.finditer(r'"[^"]*"|\S+', q):
        tok = m.group(0)
        if not any(ch.isalnum() for ch in tok):
            # 21/07: token di SOLA punteggiatura ('(', '&', '-') -> 400 anche
            # quotato (misurato: '"("' rifiutato dal parser). Si scarta.
            continue
        if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            out.append(tok)  # frase gia' quotata dallo specialista
        elif all(ch.isalnum() for ch in tok):
            out.append(tok)
        else:
            out.append('"' + tok.replace('"', '') + '"')
    return " ".join(out)


def fetch_gnews(query, max_news=10):
    """
    News da GNews (Google News API). 100/day gratis, real-time.
    https://gnews.io/docs/v4
    """
    if not GNEWS_API_KEY:
        _record_status("gnews", "NO_KEY")
        return []
    _q = str(query or "")
    _skip = _provider_skip_reason("gnews", _q)
    if _skip:
        _record_status("gnews", _skip)
        return []

    url = "https://gnews.io/api/v4/search"
    params = {
        "q": gnews_safe_query(query),
        "lang": "en",
        "max": min(max_news, 25),  # Opus 4.8 16/07: piano Essential (free era 10)
        "apikey": GNEWS_API_KEY,
    }

    _, _record = _rate_limiter()
    try:
        r = requests.get(url, params=params, timeout=10)
        _record("gnews", _q, got_429=r.status_code in (403, 429))
        if r.status_code != 200:
            # 21/07 (run #46: 14x "HTTP 400" NUDI nel log, causa non diagnosticabile):
            # il log deve bastare da solo — query inviata + body (o "vuoto"), solo
            # separatori ASCII (niente em-dash: sospetto troncamento in redirect).
            print("  [!] GNews HTTP " + str(r.status_code)
                  + " | q=" + repr(params.get("q", ""))[:120]
                  + " | body=" + ((r.text[:200].replace("\n", " ")) if r.text else "(vuoto)"))
            _record_status("gnews", "HTTP_" + str(r.status_code))
            return []
        data = r.json()
        articoli = data.get("articles", [])
        risultati = []
        for a in articoli:
            risultati.append({
                "ticker_associato": query,
                "titolo": a.get("title", "") or "",
                "descrizione": (a.get("description", "") or a.get("content", "") or "")[:500],
                "fonte": "GNews (" + (a.get("source", {}).get("name", "Unknown")) + ")",
                "url": a.get("url", ""),
                "data": a.get("publishedAt", ""),
            })
        _record_status("gnews", "live")
        return risultati
    except Exception as e:
        print("  [!] Errore GNews: " + str(e))
        _record_status("gnews", "ERROR")
        return []


def fetch_news_multifonte_stock(ticker, nome_azienda, temi=None):
    """
    Aggrega news da TUTTE le fonti disponibili per uno stock.
    Strategia: parallelo su Yahoo Finance + Marketaux + TheNewsAPI + GNews.
    NewsAPI viene chiamata separatamente da news_aggregator (il v1 fetcher.py e' in attic).
    """
    risultati = []

    # 1. Yahoo Finance (free, no key, real-time)
    risultati.extend(fetch_yfinance_news(ticker, max_news=10))

    # 2. Marketaux (key required)
    risultati.extend(fetch_marketaux(ticker=ticker, max_news=5))

    # 3. TheNewsAPI - usa nome azienda
    query = '"' + nome_azienda + '"'
    risultati.extend(fetch_thenewsapi(query=query, max_news=5))

    # 4. GNews - usa nome azienda
    risultati.extend(fetch_gnews(query=nome_azienda, max_news=5))

    return risultati


def fetch_news_multifonte_tema(temi, etichetta="tema"):
    """
    Aggrega news da fonti tematiche per ETF/macro.
    Per ETF temi e' una lista di keyword tipo ["Hang Seng Tech", "Alibaba"].
    Usiamo solo Marketaux/TheNewsAPI/GNews qui (yfinance e' per ticker).
    """
    risultati = []
    if not temi:
        return risultati

    # Combina i temi in una OR query
    query = " OR ".join(['"' + t + '"' if " " in t else t for t in temi])

    # 1. Marketaux con search
    risultati.extend(fetch_marketaux(query=query, max_news=5))

    # 2. TheNewsAPI con search
    risultati.extend(fetch_thenewsapi(query=query, max_news=5))

    # 3. GNews - prendiamo solo il primo tema per non sforare query length
    primo_tema = temi[0] if temi else ""
    if primo_tema:
        risultati.extend(fetch_gnews(query=primo_tema, max_news=5))

    # Riassocia tutti i risultati alla etichetta del gruppo
    for r in risultati:
        r["ticker_associato"] = etichetta

    return risultati
