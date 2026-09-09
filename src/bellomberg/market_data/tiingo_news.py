"""
tiingo_news.py — Provider Tiingo News API (task #173, stack API a pagamento)

Richiede in .env:  TIINGO_API_KEY=...   (piano Power, $30/mo)
Docs: https://www.tiingo.com/documentation/news

Output normalizzato allo schema item di news_aggregator:
  {title, source, url, published_at, snippet, provider, _tickers}

NOTA: il tagging ticker di Tiingo e' US-centrico. Per i ticker EU (.MI/.L/.VI)
restano attivi i termini per NOME del negozio di news_aggregator
(data/news_search_terms.json); Tiingo si AGGIUNGE
come fonte di qualita' per US + feed mercato generale, non sostituisce tutto.
"""
from bellomberg.core.paths import PROJECT_ROOT
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

try:
    import requests
    REQ_OK = True
except ImportError:
    REQ_OK = False

try:  # carica .env anche in esecuzione standalone
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

TIINGO_KEY = os.environ.get("TIINGO_API_KEY", "")
BASE = "https://api.tiingo.com"


def tiingo_available() -> bool:
    return bool(REQ_OK and TIINGO_KEY)


def fetch_tiingo_news(tickers: Optional[List[str]] = None,
                      days: int = 2, limit: int = 30) -> List[Dict[str, Any]]:
    """News taggate per ticker. tickers=None -> feed generale di mercato.

    I ticker vanno passati nel formato Tiingo (US plain, minuscolo: 'acme', 'beta').
    """
    if not tiingo_available():
        return []
    try:
        params = {
            "token": TIINGO_KEY,
            "limit": max(1, min(limit, 100)),
            "startDate": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"),
            "sortBy": "publishedDate",
        }
        if tickers:
            params["tickers"] = ",".join(t.lower().strip() for t in tickers if t)
        r = requests.get(f"{BASE}/tiingo/news", params=params, timeout=12)
        if r.status_code != 200:
            return []
        out: List[Dict[str, Any]] = []
        for a in r.json():
            out.append({
                "title": a.get("title", ""),
                "source": a.get("source", "Tiingo"),
                "url": a.get("url", ""),
                "published_at": a.get("publishedDate", ""),
                "snippet": (a.get("description") or "")[:500],
                "provider": "tiingo",
                "_tickers": [t.upper() for t in (a.get("tickers") or [])],
            })
        return out
    except Exception:
        return []


if __name__ == "__main__":
    # smoke test: python tiingo_news.py [TICKER ...]
    import sys
    if not tiingo_available():
        print("TIINGO_API_KEY mancante in .env (o requests non installato)")
    else:
        # 04/09 (criterio (1)): i simboli di prova arrivano da riga di comando, non dal
        # sorgente (qui c'erano tre posizioni del PM). Default: un simbolo US qualsiasi.
        items = fetch_tiingo_news(sys.argv[1:] or ["AAPL"], days=2, limit=10)
        print(f"{len(items)} news")
        for it in items[:10]:
            print("-", it["published_at"][:16], "|", ",".join(it["_tickers"][:4]), "|", it["title"][:70])
