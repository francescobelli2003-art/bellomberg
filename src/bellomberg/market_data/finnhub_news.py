"""
BELLOMBERG - Finnhub Integration

API gratuita 60 req/min. Docs: https://finnhub.io/docs/api
Setup: aggiungere FINNHUB_API_KEY=... in .env

Endpoint usati (piano GRATUITO, confermato dal PM il 22/08):
  - /company-news      : news per ticker (1 settimana)
  - /calendar/earnings : earnings calendar globale
  - /calendar/ipo      : IPO calendar
  - /stock/insider-transactions : Form 4 insider trades (real-time, faster di SEC)
  FUORI PIANO (403 sul gratuito, misurato il 22/08 e il 27/08 sull'API vera —
  registro `ENDPOINT_FUORI_PIANO` datato da `ULTIMA_MISURA_FUORI_PIANO`, non
  vengono interrogati; `prova_finnhub_piano.py --vero` li rimisura):
  - /news-sentiment    : pre-computed sentiment per ticker
  - /press-releases    : press releases ufficiali aziendali
  - /calendar/economic : calendario macro

API publica:
  fetch_company_news(ticker, days=7, motivo=None) -> list
  fetch_earnings_calendar(days_ahead=14, motivo=None) -> list
  fetch_ipo_calendar(days_ahead=30) -> list
  fetch_insider_trades(ticker, days=30, motivo=None) -> list
  fetch_press_releases(ticker, days=30, motivo=None) -> list   (fuori piano: [] col motivo)
  fetch_news_sentiment(ticker, motivo=None) -> dict            (fuori piano: {} col motivo)
  fetch_economic_calendar(days_back=1, days_ahead=14, motivo=None) -> list (fuori piano)
  fetch_finnhub_intel(ticker) -> dict (aggregator: news + earnings + insider + sentiment
      + press; con `fonti_mute` {path: motivo} e `avviso` — chiave dedicata, C1 del ponte)
"""
import os
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional

try:
    import requests
    REQ_OK = True
except Exception:
    REQ_OK = False

BASE_URL = "https://finnhub.io/api/v1"

# Endpoint che sul piano GRATUITO rispondono 403 (misurato il 22/08 e il 27/08
# chiamando l'API vera, ponte (78); piano gratuito confermato dal PM il 22/08).
# `_api_get` NON li interroga (prima: una HTTP sprecata a chiamata, un 403 nel
# log e un vuoto muto al chiamante) e deposita il motivo in `motivo`.
# E' una MISURA DATATA, non una legge: `ULTIMA_MISURA_FUORI_PIANO` e' il giorno
# dell'ultima sonda (`prova_finnhub_piano.py --vero` la rifa' e, senza rete,
# avverte quando la misura ha piu' di 60 giorni); se un endpoint risponde 200
# si toglie la riga e la chiamata riparte da sola (test: «svuotando il
# registro l'HTTP riparte»). La data sta anche nel testo che arriva al chiamante.
ULTIMA_MISURA_FUORI_PIANO = date(2026, 8, 27)
_PERCHE_FUORI_PIANO = ("risponde 403 sul piano gratuito (misurato 22/08 e %s): non interrogato"
                       % ULTIMA_MISURA_FUORI_PIANO.strftime("%d/%m"))
ENDPOINT_FUORI_PIANO: Dict[str, str] = {
    "/press-releases": _PERCHE_FUORI_PIANO,
    "/news-sentiment": _PERCHE_FUORI_PIANO,
    "/calendar/economic": _PERCHE_FUORI_PIANO,
}


def _log(msg: str):
    # pipe stdout morta (backend zombie, autopsia (40)): log perso, mai eccezione
    # review 27/08: `ValueError` («I/O operation on closed file», encoding)
    # scappava da qui DENTRO l'except di `_api_get` → seconda eccezione dal
    # gestore, la stessa classe di guasto dell'autopsia (40). Il log e'
    # additivo: se non si puo' scrivere si perde la riga, non il motivo.
    try:
        print(f"[FINNHUB] {msg}", flush=True)
    except (OSError, ValueError):
        pass


def _get_key() -> Optional[str]:
    try:
        from bellomberg.core.config import FINNHUB_API_KEY
        return FINNHUB_API_KEY if FINNHUB_API_KEY else None
    except Exception:
        return os.environ.get("FINNHUB_API_KEY")


def _muto(motivo: Optional[List[str]], testo: str) -> None:
    """Il motivo per cui questa fonte NON ha risposto, detto a CHI CHIAMA.

    Voce E (22/08): fino a oggi ogni ramo di questa funzione tornava `None` e
    le funzioni pubbliche lo traducevano in lista vuota, quindi «zero eventi» e
    «chiave morta / 429 / rete giu'» erano indistinguibili per il chiamante —
    il motivo esisteva solo su stdout. Chi chiama passa una lista e ci legge
    dentro; chi non la passa ha esattamente il comportamento di prima.
    """
    _log(testo)
    if motivo is not None:
        motivo.append(testo)


def _api_get(path: str, params: Optional[Dict[str, Any]] = None,
              timeout: int = 12,
              motivo: Optional[List[str]] = None) -> Optional[Any]:
    """GET helper con auth automatica + error handling.

    `motivo`: lista opzionale in cui viene depositata la ragione di un ritorno
    `None` (v. `_muto`). Omettila e il comportamento e' quello storico.
    """
    if path in ENDPOINT_FUORI_PIANO:
        _muto(motivo, f"fuori piano: {path} {ENDPOINT_FUORI_PIANO[path]}")
        return None
    if not REQ_OK:
        _muto(motivo, "modulo requests non disponibile: nessuna chiamata possibile")
        return None
    key = _get_key()
    if not key:
        _muto(motivo, "missing FINNHUB_API_KEY in .env")
        return None
    params = dict(params or {})
    params["token"] = key
    try:
        r = requests.get(f"{BASE_URL}{path}", params=params, timeout=timeout)
        if r.status_code == 401:
            _muto(motivo, "401 unauthorized - check FINNHUB_API_KEY validity")
            return None
        if r.status_code == 429:
            _muto(motivo, "429 rate limited (60 req/min free tier)")
            return None
        if r.status_code != 200:
            _muto(motivo, f"HTTP {r.status_code} on {path}: {r.text[:120]}")
            return None
        return r.json()
    except Exception as e:
        _muto(motivo, f"error on {path}: {e}")
        return None


def _norm_ticker(ticker: str) -> str:
    """Simbolo con cui Finnhub conosce l'emittente. Finnhub usa il ticker base senza suffisso
    paese, e per gli ADR un simbolo DIVERSO dalla base di listino: quelli stanno nel negozio
    privato degli alias (negozi_privati.carica_alias, sezione finnhub: simbolo COMPLETO ->
    simbolo Finnhub; data/alias_fonti.json, forma in alias_fonti.example.json), riletto a ogni
    chiamata. Un simbolo assente dal negozio si RICOSTRUISCE togliendo il suffisso (audit/11 §4:
    la ricostruzione puo' produrre simboli inesistenti, e il dispatcher la dichiara nel payload).
    I nomi solo-Italia su Finnhub free non hanno dati comunque."""
    t = ticker.upper().strip()
    from bellomberg.storage.negozi_privati import carica_alias
    alias = carica_alias()["alias"]["finnhub"]
    if t in alias:
        return alias[t]
    # Mapping speciali Italia/UK
    SUFFIX_MAP = {
        ".MI": "",   # Borsa Italiana
        ".DE": "",   # Xetra Germany
        ".L":  ".L", # London Stock Exchange (Finnhub mantiene .L)
        ".PA": ".PA",# Paris
    }
    for suffix, repl in SUFFIX_MAP.items():
        if t.endswith(suffix):
            t = t[:-len(suffix)] + repl
            break
    return t


# ============================================================
# COMPANY NEWS
# ============================================================
def fetch_company_news(ticker: str, days: int = 7,
                        max_items: int = 20,
                        motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """News stock-specifiche degli ultimi N giorni.

    `motivo`: v. `_muto`. Se resta vuota e la lista e' vuota, lo zero e' una
    MISURA (Finnhub ha risposto 200 con zero item), non una cecita'.
    """
    t = _norm_ticker(ticker)
    today = datetime.now().strftime("%Y-%m-%d")
    frm = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = _api_get("/company-news", {"symbol": t, "from": frm, "to": today},
                    motivo=motivo)
    if not isinstance(data, list):
        if data is not None:
            _muto(motivo, f"/company-news: risposta inattesa ({type(data).__name__})")
        return []
    out = []
    for n in data[:max_items]:
        ts = n.get("datetime", 0)
        out.append({
            "title": (n.get("headline") or "")[:200],
            "snippet": (n.get("summary") or "")[:500],
            "url": n.get("url") or "",
            "provider": "Finnhub",
            "source": n.get("source") or "",
            "published_at": datetime.fromtimestamp(ts).isoformat() if ts else "",
            "ticker_mentioned": ticker,
            "image": n.get("image") or "",
            "category": n.get("category") or "company",
        })
    return out


# ============================================================
# EARNINGS CALENDAR
# ============================================================
def fetch_earnings_calendar(days_ahead: int = 14,
                              days_back: int = 0,
                              symbols: Optional[List[str]] = None,
                              motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Earnings calendar globale per i prossimi N giorni.
    Se symbols=None, ritorna tutti (limit 100). Altrimenti filtra. `motivo`: v. `_muto`.
    """
    today = datetime.now().date()
    frm = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
    to = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    data = _api_get("/calendar/earnings", {"from": frm, "to": to}, motivo=motivo)
    if not isinstance(data, dict):
        if data is not None:
            _muto(motivo, f"/calendar/earnings: risposta inattesa ({type(data).__name__})")
        return []
    events = data.get("earningsCalendar", [])
    if symbols:
        sym_set = {_norm_ticker(s) for s in symbols}
        events = [e for e in events if e.get("symbol", "") in sym_set]
    out = []
    for e in events:
        out.append({
            "date": e.get("date", ""),
            "symbol": e.get("symbol", ""),
            "eps_actual": e.get("epsActual"),
            "eps_estimate": e.get("epsEstimate"),
            "revenue_actual": e.get("revenueActual"),
            "revenue_estimate": e.get("revenueEstimate"),
            "hour": e.get("hour", ""),  # bmo/amc/dmh
            "year": e.get("year"),
            "quarter": e.get("quarter"),
        })
    return out


def fetch_earnings_for_portfolio(portfolio_tickers: List[str],
                                    days_ahead: int = 14,
                                    motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Earnings dei prossimi N giorni filtrati per i ticker del portfolio.
    Senza ticker (book senza nomi US) NON si interroga: si dichiara (review 27/08)."""
    if not portfolio_tickers:
        _muto(motivo, "/calendar/earnings: nessun ticker da interrogare (book senza ticker US): "
                      "non interrogato")
        return []
    return fetch_earnings_calendar(days_ahead=days_ahead, symbols=portfolio_tickers,
                                   motivo=motivo)


def next_earnings_date(ticker: str, today: Optional[str] = None) -> Optional[str]:
    """V6 guidance D4: data (ISO) della PROSSIMA trimestrale STRETTAMENTE futura.
    Review V6 A1: il flusso tipico e' registrare la guidance IL GIORNO della release
    — quel giorno l'evento di OGGI e' gia' riportato: usarlo come scadenza rende
    STALE domani la guidance appena letta. Si prende solo `date > oggi`.
    None = calendar n.d. (il chiamante applica il fallback DICHIARATO)."""
    t = (today or datetime.now().date().isoformat())[:10]
    try:
        evs = sorted(e["date"] for e in fetch_earnings_for_portfolio([ticker], days_ahead=200)
                     if e.get("date") and str(e["date"])[:10] > t)
        return evs[0] if evs else None
    except Exception:
        return None


# ============================================================
# ECONOMIC CALENDAR (con previous/estimate/actual)
# ============================================================
def fetch_economic_calendar(days_back: int = 1, days_ahead: int = 14,
                            motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Calendar eventi macro mondiali via Finnhub — FUORI PIANO sul gratuito
    (v. `ENDPOINT_FUORI_PIANO`): oggi torna [] col motivo in `motivo`, senza HTTP.
    Output schema per evento: {event, country, time, actual, estimate, prev, impact, unit}.
    """
    today = datetime.now().date()
    frm = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
    to = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    data = _api_get("/calendar/economic", {"from": frm, "to": to}, motivo=motivo)
    if not isinstance(data, dict):
        if data is not None:
            _muto(motivo, f"/calendar/economic: risposta inattesa ({type(data).__name__})")
        return []
    raw = data.get("economicCalendar", [])
    if not isinstance(raw, list):
        return []
    out = []
    for e in raw:
        impact_raw = (e.get("impact") or "").lower()
        importance = {"high": 5, "medium": 3, "low": 1}.get(impact_raw, 2)
        out.append({
            "date": e.get("time", "")[:10] if e.get("time") else "",
            "time": e.get("time", "")[11:16] if e.get("time") and len(e.get("time", "")) >= 16 else "",
            "country": e.get("country", ""),
            "title": e.get("event", ""),
            "actual": e.get("actual"),
            "estimate": e.get("estimate"),
            "previous": e.get("prev"),
            "unit": e.get("unit", ""),
            "impact": impact_raw,
            "importance": importance,
            "type": "Macro Release",
        })
    return out


# ============================================================
# IPO CALENDAR
# ============================================================
def fetch_ipo_calendar(days_ahead: int = 30) -> List[Dict[str, Any]]:
    """IPO calendar prossimi N giorni."""
    today = datetime.now().date()
    frm = today.strftime("%Y-%m-%d")
    to = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    data = _api_get("/calendar/ipo", {"from": frm, "to": to})
    if not isinstance(data, dict):
        return []
    return data.get("ipoCalendar", [])


# ============================================================
# INSIDER TRANSACTIONS
# ============================================================
def fetch_insider_trades(ticker: str, days: int = 30,
                         motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Insider Form 4 real-time (Finnhub aggrega SEC EDGAR + normalizza). `motivo`: v. `_muto`."""
    t = _norm_ticker(ticker)
    today = datetime.now().strftime("%Y-%m-%d")
    frm = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = _api_get("/stock/insider-transactions",
                      {"symbol": t, "from": frm, "to": today}, motivo=motivo)
    if not isinstance(data, dict):
        if data is not None:
            _muto(motivo, f"/stock/insider-transactions: risposta inattesa ({type(data).__name__})")
        return []
    raw = data.get("data", [])
    out = []
    for tr in raw:
        change = tr.get("change", 0) or 0
        price = tr.get("transactionPrice", 0) or 0
        action = "BUY" if change > 0 else "SELL" if change < 0 else "?"
        out.append({
            "ticker": ticker,
            "owner": tr.get("name") or "",
            "share_change": change,
            "transaction_price": price,
            "value_usd": abs(change) * price,
            "filing_date": tr.get("filingDate", ""),
            "transaction_date": tr.get("transactionDate", ""),
            "action": action,
            "transaction_code": tr.get("transactionCode", ""),
        })
    # Sort by transaction date desc
    out.sort(key=lambda x: x.get("transaction_date", ""), reverse=True)
    return out


# ============================================================
# PRESS RELEASES — FUORI PIANO sul gratuito (403 per sempre, misurato 22/08;
# v. ENDPOINT_FUORI_PIANO): torna [] col motivo, senza HTTP
# ============================================================
def fetch_press_releases(ticker: str, days: int = 30,
                          motivo: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Press releases ufficiali aziendali. `motivo`: v. `_muto`."""
    t = _norm_ticker(ticker)
    today = datetime.now().strftime("%Y-%m-%d")
    frm = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = _api_get("/press-releases", {"symbol": t, "from": frm, "to": today},
                    motivo=motivo)
    if not isinstance(data, dict):
        if data is not None:
            _muto(motivo, f"/press-releases: risposta inattesa ({type(data).__name__})")
        return []
    return data.get("majorDevelopment", [])


# ============================================================
# NEWS SENTIMENT (pre-computed)
# ============================================================
def fetch_news_sentiment(ticker: str,
                         motivo: Optional[List[str]] = None) -> Dict[str, Any]:
    """Sentiment aggregato per ticker — FUORI PIANO sul gratuito (v.
    `ENDPOINT_FUORI_PIANO`): oggi torna {} col motivo in `motivo`, senza HTTP."""
    t = _norm_ticker(ticker)
    data = _api_get("/news-sentiment", {"symbol": t}, motivo=motivo)
    if not isinstance(data, dict):
        if data is not None:
            _muto(motivo, f"/news-sentiment: risposta inattesa ({type(data).__name__})")
        return {}
    return {
        "ticker": ticker,
        "buzz": data.get("buzz", {}),
        "company_news_score": data.get("companyNewsScore"),
        "sector_avg_bullish_pct": data.get("sectorAverageBullishPercent"),
        "sector_avg_news_score": data.get("sectorAverageNewsScore"),
        "sentiment": data.get("sentiment", {}),
    }


# ============================================================
# AGGREGATE INTEL (used by agents via tool dispatcher)
# ============================================================
def fetch_finnhub_intel(ticker: str, days_news: int = 7,
                         days_insider: int = 30) -> Dict[str, Any]:
    """Pacchetto completo per un ticker: news + earnings + insider + sentiment + press.

    27/08 (seconda meta' Finnhub, C1 del ponte): ogni fonte deposita il proprio
    motivo di silenzio e il payload lo DICHIARA con la chiave dedicata
    `fonti_mute` {path: motivo} + `avviso` — la forma di `/news/macro`. Prima
    `sentiment: {}` e `press_releases_30d: []` erano muti (e costavano due HTTP
    a 403 per ticker). I campi vecchi restano com'erano.
    """
    fonti = {
        "/company-news": [], "/calendar/earnings": [], "/stock/insider-transactions": [],
        "/news-sentiment": [], "/press-releases": [],
    }
    out = {
        "ticker": ticker,
        "_source": "finnhub.io",
        "_timestamp": datetime.now().isoformat(),
        "news_count_7d": None,
        "news_sample": fetch_company_news(ticker, days=days_news, max_items=10,
                                          motivo=fonti["/company-news"])[:10],
        "next_earnings": fetch_earnings_for_portfolio([ticker], days_ahead=90,
                                                      motivo=fonti["/calendar/earnings"])[:1],
        "insider_trades": fetch_insider_trades(ticker, days=days_insider,
                                               motivo=fonti["/stock/insider-transactions"])[:10],
        "sentiment": fetch_news_sentiment(ticker, motivo=fonti["/news-sentiment"]),
        "press_releases_30d": fetch_press_releases(ticker, days=30,
                                                   motivo=fonti["/press-releases"])[:5],
    }
    mute = {path: "; ".join(m) for path, m in fonti.items() if m}
    out["fonti_mute"] = mute or None
    out["avviso"] = (("copertura PARZIALE: %d fonti Finnhub mute (%s) — vuoto o zero "
                      "NON significano assenza di notizie/eventi")
                     % (len(mute), ", ".join(mute))) if mute else None
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "econ":
        print("=== Economic Calendar ===")
        events = fetch_economic_calendar(days_back=0, days_ahead=10)
        for e in events[:30]:
            prev = e.get("previous", "-")
            est = e.get("estimate", "-")
            act = e.get("actual", "-")
            print("  " + str(e.get("date","")) + " " + str(e.get("time","")) + " | " +
                  str(e.get("country",""))[:4] + " | imp=" + str(e.get("importance")) +
                  " | prev=" + str(prev) + " est=" + str(est) + " act=" + str(act) +
                  " | " + str(e.get("title",""))[:50])
    else:
        print("Usage: python finnhub_news.py [econ]")
