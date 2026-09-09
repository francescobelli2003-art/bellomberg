"""
quiver_data.py — Provider Quiver Quantitative (task #173, stack API a pagamento)

Richiede in .env:  QUIVER_API_KEY=...   (piano Hobbyist, $30/mo — Tier 1)
Docs: https://api.quiverquant.com/

Alt-data per l'agente Politics: congressional trading, lobbying, contratti governativi.
"""
import os
from typing import Dict, Any, Optional
from datetime import datetime

from bellomberg.core.paths import PROJECT_ROOT

try:  # carica .env anche in esecuzione standalone
    from dotenv import load_dotenv as _ld
    _ld(PROJECT_ROOT / ".env")
except Exception:
    pass

try:
    import requests
    REQ_OK = True
except ImportError:
    REQ_OK = False

QUIVER_KEY = os.environ.get("QUIVER_API_KEY", "")
BASE = "https://api.quiverquant.com/beta"


def quiver_available() -> bool:
    return bool(REQ_OK and QUIVER_KEY)


def _log_riga(testo: str) -> None:
    """La riga e' ADDITIVA: se stdout e' morto (pipe chiusa, autopsia (40)) o
    rifiuta l'encoding (`ValueError`/`UnicodeEncodeError`, review 27/08) si
    perde LEI, non il contratto HTTP verso il chiamante."""
    try:
        print("  [QUIVER] " + testo, flush=True)
    except (OSError, ValueError):
        pass


def _log_http(status: int, path: str, body: str) -> None:
    """Riga di log per OGNI risposta non-200 (27/08, run V9 punto «rate-limit»).

    Prima il 429 tornava al chiamante come `{"error"}` e basta: il log della
    run non poteva mostrarlo, quindi «zero righe 429 nel log» NON era una
    misura. Forma `[QUIVER] HTTP <code> on <path>: <corpo>`, analoga alla riga
    generica di `finnhub_news._api_get` (che pero' sul 429 stampa `429 rate
    limited (...)` senza HTTP ne' path). Corpo su una riga sola (chi conta i
    429 fa grep di riga), 120 char. La chiave Quiver viaggia nell'header
    Authorization: ne' `r.text` ne' `str(e)` possono portarla (review 27/08).
    """
    corpo = " ".join((body or "")[:120].split())
    _log_riga(f"HTTP {status} on {path}: {corpo}")


def _log_eccezione(e: Exception, path: str) -> None:
    """Timeout / rete giu': TIPO e path. Cosi' anche «zero guasti Quiver nel
    log» e' una misura, non solo «zero 429»."""
    _log_riga(f"{type(e).__name__} on {path}")


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    if not quiver_available():
        return {"error": "QUIVER_API_KEY mancante"}
    try:
        r = requests.get(f"{BASE}{path}", params=params or {},
                         headers={"Authorization": f"Bearer {QUIVER_KEY}",
                                  "Accept": "application/json"},
                         timeout=15)
        if r.status_code != 200:
            _log_http(r.status_code, path, r.text)
            return {"error": f"HTTP {r.status_code}", "_body": r.text[:200]}
        return r.json()
    except Exception as e:
        _log_eccezione(e, path)
        return {"error": str(e)}


def get_congress_trades(ticker: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    """Trade dei membri del Congresso USA (Senato + Camera), piu' recenti prima.

    Migrazione Quiver 07/2026: i path per-ticker /live/... sono 404, il
    per-ticker vive su /historical/...; il feed generale resta su /live/.
    """
    path = f"/historical/congresstrading/{ticker.upper()}" if ticker else "/live/congresstrading"
    data = _get(path)
    if isinstance(data, dict) and data.get("error"):
        return {**data, "_source": f"quiver {path}"}
    rows = data[:limit] if isinstance(data, list) else []
    return {"ticker": (ticker or "ALL").upper(), "n": len(rows), "trades": rows,
            "_source": f"quiver {path}",
            "_timestamp": datetime.now().isoformat()}


def get_lobbying(ticker: str, limit: int = 30) -> Dict[str, Any]:
    """Spese di lobbying registrate per una societa'."""
    data = _get(f"/historical/lobbying/{ticker.upper()}")
    if isinstance(data, dict) and data.get("error"):
        return {**data, "_source": "quiver /historical/lobbying"}
    rows = data[:limit] if isinstance(data, list) else []
    return {"ticker": ticker.upper(), "n": len(rows), "filings": rows,
            "_source": "quiver /historical/lobbying", "_timestamp": datetime.now().isoformat()}


def get_gov_contracts(ticker: str, limit: int = 30) -> Dict[str, Any]:
    """Contratti governativi USA assegnati a una societa'."""
    data = _get(f"/historical/govcontractsall/{ticker.upper()}")
    if isinstance(data, dict) and data.get("error"):
        return {**data, "_source": "quiver /historical/govcontractsall"}
    rows = data[:limit] if isinstance(data, list) else []
    return {"ticker": ticker.upper(), "n": len(rows), "contracts": rows,
            "_source": "quiver /historical/govcontractsall", "_timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    # smoke test: python quiver_data.py
    if not quiver_available():
        print("QUIVER_API_KEY mancante in .env")
    else:
        r = get_congress_trades(limit=5)
        print("congress trades:", r.get("n"), "| esempio:", (r.get("trades") or [{}])[0])
