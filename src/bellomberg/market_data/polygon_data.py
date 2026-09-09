"""
polygon_data.py — Provider Polygon.io (task #173, stack API a pagamento)

Richiede in .env:  POLYGON_API_KEY=...
  - Piano "Options Starter" ($29/mo)  -> get_option_expirations / get_options_chain
  - get_stock_daily (aggregati daily US): NB 16/07, correzione PM — il PM paga SOLO
    il piano opzioni; gli aggregati stock girano sul tier gratuito della stessa key
    (limiti free: pochi req/min, dati EOD). Se gli aggregati servissero di piu',
    e' una decisione di spesa del PM, non un dato di fatto.
La stessa key funziona per i prodotti a cui sei abbonato.
Docs: https://polygon.io/docs

Risolve anche il bug #162: la chain si puo' chiedere per QUALSIASI expiry,
non solo la nearest come yfinance.
"""
from bellomberg.core.paths import PROJECT_ROOT
import os
from typing import List, Dict, Any, Optional
from datetime import datetime

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

POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE = "https://api.polygon.io"


def polygon_available() -> bool:
    return bool(REQ_OK and POLYGON_KEY)


def _senza_chiave(testo: str) -> str:
    """La chiave API non entra ne' nel log ne' nel dict di errore. Review 27/08:
    `str(e)` di un ConnectTimeout di requests porta l'URL intero con
    `apiKey=` (misurato); un corpo di errore del WAF potrebbe riecheggiarla."""
    return testo.replace(POLYGON_KEY, "***") if POLYGON_KEY else testo


def _path_log(url: str) -> str:
    """Path SENZA query: la chiave viaggia nei params, ma un `next_url` di
    paginazione puo' portarla nella stringa — nel log non entra."""
    path = url.split("?", 1)[0]
    if path.startswith(BASE):
        path = path[len(BASE):]
    return path


def _log_riga(testo: str) -> None:
    """La riga e' ADDITIVA: se stdout e' morto (pipe chiusa, autopsia (40)) o
    rifiuta l'encoding (`ValueError`/`UnicodeEncodeError`, review 27/08) si
    perde LEI, non il contratto HTTP verso il chiamante."""
    try:
        print("  [POLYGON] " + testo, flush=True)
    except (OSError, ValueError):
        pass


def _log_http(status: int, url: str, body: str) -> None:
    """Riga di log per OGNI risposta non-200 (27/08, run V9 punto «rate-limit»).

    Prima il 429 tornava al chiamante come `{"error"}` e basta: il log della
    run non poteva mostrarlo, quindi «zero righe 429 nel log» NON era una
    misura. Forma `[POLYGON] HTTP <code> on <path>: <corpo>`, analoga alla riga
    generica di `finnhub_news._api_get` (che pero' sul 429 stampa `429 rate
    limited (...)` senza HTTP ne' path: un grep `HTTP 429` conta Polygon e
    Quiver, non Finnhub). Corpo su una riga sola (chi conta i 429 fa grep di
    riga), 120 char, senza chiave.
    """
    corpo = _senza_chiave(" ".join((body or "")[:120].split()))
    _log_riga(f"HTTP {status} on {_path_log(url)}: {corpo}")


def _log_eccezione(e: Exception, url: str) -> None:
    """Timeout / rete giu': TIPO e path, mai `str(e)` (v. `_senza_chiave`).
    Cosi' anche «zero guasti Polygon nel log» e' una misura, non solo «zero 429»."""
    _log_riga(f"{type(e).__name__} on {_path_log(url)}")


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not polygon_available():
        return None
    p = dict(params or {})
    p["apiKey"] = POLYGON_KEY
    # path può essere relativo o un next_url assoluto (paginazione)
    url = path if path.startswith("http") else f"{BASE}{path}"
    try:
        r = requests.get(url, params=p, timeout=15)
        if r.status_code != 200:
            _log_http(r.status_code, url, r.text)
            return {"error": f"HTTP {r.status_code}", "_body": r.text[:200]}
        return r.json()
    except Exception as e:
        _log_eccezione(e, url)
        return {"error": _senza_chiave(str(e))}


def get_option_expirations(underlying: str, limit: int = 60) -> Dict[str, Any]:
    """Lista expiry disponibili per un sottostante US.
    Paginata (#179 fix): 1000 contratti coprono solo le prime scadenze di un
    sottostante liquido — seguiamo next_url per la lista completa."""
    from datetime import timedelta
    exps: set = set()
    last = None

    def _scan(extra_params: Dict[str, Any], pages: int) -> None:
        nonlocal last
        url: Optional[str] = "/v3/reference/options/contracts"
        params: Optional[Dict[str, Any]] = {
            "underlying_ticker": underlying.upper(),
            "limit": 1000,
            "sort": "expiration_date",
            **extra_params,
        }
        for _ in range(pages):
            data = _get(url, params)
            last = data
            if not data or data.get("error"):
                return
            for c in data.get("results", []):
                if c.get("expiration_date"):
                    exps.add(c["expiration_date"])
            url = data.get("next_url")
            params = None
            if not url:
                return

    # Scansione base (scadenze vicine) + finestre future: i sottostanti liquidi
    # hanno decine di migliaia di contratti e la sola paginazione copre poche
    # settimane — saltiamo avanti con filtri data per coprire ~4 mesi.
    _scan({}, 4)
    today = datetime.now().date()
    for shift in (35, 70, 105):
        _scan({"expiration_date.gte": str(today + timedelta(days=shift))}, 1)

    if not exps:
        return {"error": (last or {}).get("error", "no data"),
                "_body": (last or {}).get("_body", ""),
                "_source": "polygon /v3/reference/options/contracts"}
    return {"underlying": underlying.upper(), "expirations": sorted(exps)[:limit],
            "_source": "polygon /v3/reference/options/contracts (paginated)",
            "_timestamp": datetime.now().isoformat()}


def get_options_chain(underlying: str, expiry: Optional[str] = None,
                      max_contracts: int = 250) -> Dict[str, Any]:
    """Chain snapshot con IV, greeks, OI, volume.
    expiry: 'YYYY-MM-DD' opzionale — se None Polygon ritorna tutte (cap max_contracts).
    """
    params: Dict[str, Any] = {"limit": 250}
    if expiry:
        params["expiration_date"] = expiry
    # Paginazione (#177 fix): i contratti sono ordinati per ticker e le Call
    # vengono prima delle Put — senza next_url si perdono le put.
    results: List[Dict[str, Any]] = []
    url: Optional[str] = f"/v3/snapshot/options/{underlying.upper()}"
    pages = 0
    data = None
    while url and pages < max(1, max_contracts // 250 + 2):
        data = _get(url, params if pages == 0 else None)
        if not data or data.get("error"):
            break
        results.extend(data.get("results", []))
        url = data.get("next_url")
        pages += 1
    if not results:
        return {"error": (data or {}).get("error", "no data"),
                "_body": (data or {}).get("_body", ""),
                "_source": "polygon /v3/snapshot/options"}
    rows = []
    for c in results:
        det = c.get("details", {}) or {}
        greeks = c.get("greeks", {}) or {}
        day = c.get("day", {}) or {}
        rows.append({
            "contract": det.get("ticker"),
            "type": det.get("contract_type"),          # call / put
            "strike": det.get("strike_price"),
            "expiry": det.get("expiration_date"),
            "iv": c.get("implied_volatility"),
            "delta": greeks.get("delta"),
            "gamma": greeks.get("gamma"),
            "theta": greeks.get("theta"),
            "vega": greeks.get("vega"),
            "oi": c.get("open_interest"),
            "volume": day.get("volume"),
            "close": day.get("close"),
        })
    rows.sort(key=lambda x: (x.get("expiry") or "", x.get("strike") or 0))
    rows = rows[:max_contracts]
    return {"underlying": underlying.upper(), "expiry_filter": expiry,
            "n_contracts": len(rows), "n_pages": pages, "chain": rows,
            "_source": "polygon /v3/snapshot/options (paginated)",
            "_timestamp": datetime.now().isoformat()}


def get_options_summary_polygon(ticker: str, expiry: Optional[str] = None) -> Dict[str, Any]:
    """Summary opzioni in formato tool_get_options_data (#163: Polygon al posto
    di IBKR come fonte primaria): spot, ATM IV call/put, P/C OI ratio, max pain.
    Se expiry e' None usa la prima scadenza >= 2 giorni (evita 0DTE distorti)."""
    src = "polygon options summary"
    try:
        if not polygon_available():
            return {"error": "POLYGON_API_KEY mancante", "_source": src}
        if not expiry:
            exp = get_option_expirations(ticker)
            if exp.get("error"):
                return {"error": exp["error"], "_source": src}
            today = datetime.now().date()
            expiry = None
            for e in exp.get("expirations", []):
                try:
                    d = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
                except Exception:
                    continue
                if d >= 2:
                    expiry = e
                    break
            if not expiry:
                return {"error": "nessuna expiry >= 2 giorni", "_source": src}

        ch = get_options_chain(ticker, expiry, max_contracts=1200)
        if ch.get("error"):
            return {"error": ch["error"], "_source": src}
        chain = ch.get("chain", [])
        calls = [c for c in chain if c.get("type") == "call" and c.get("strike")]
        puts = [c for c in chain if c.get("type") == "put" and c.get("strike")]
        if not calls or not puts:
            return {"error": "chain incompleta", "_source": src}

        # Spot: call con delta ~0.5
        spot = None
        cands = [c for c in calls if c.get("delta") is not None and abs(c["delta"] - 0.5) < 0.08]
        if cands:
            spot = float(min(cands, key=lambda c: abs(c["delta"] - 0.5))["strike"])
        if spot is None:
            ks = sorted(c["strike"] for c in calls)
            spot = ks[len(ks) // 2]

        atm_c = min(calls, key=lambda c: abs(c["strike"] - spot))
        atm_p = min(puts, key=lambda c: abs(c["strike"] - spot))
        total_call_oi = sum(int(c.get("oi") or 0) for c in calls)
        total_put_oi = sum(int(c.get("oi") or 0) for c in puts)
        pc = round(total_put_oi / total_call_oi, 3) if total_call_oi else None

        # Max pain: strike che minimizza il payout totale agli holder
        strikes = sorted({c["strike"] for c in chain if c.get("strike")})
        oi_call = {}
        oi_put = {}
        for c in calls:
            oi_call[c["strike"]] = oi_call.get(c["strike"], 0) + int(c.get("oi") or 0)
        for p in puts:
            oi_put[p["strike"]] = oi_put.get(p["strike"], 0) + int(p.get("oi") or 0)
        max_pain = None
        best = None
        for K in strikes:
            pain = (sum(oi * max(0.0, K - ks) for ks, oi in oi_call.items())
                    + sum(oi * max(0.0, ks - K) for ks, oi in oi_put.items()))
            if best is None or pain < best:
                best, max_pain = pain, K

        interp = ("P/C alto (>1.2): posizionamento difensivo/hedging prevalente"
                  if pc and pc > 1.2 else
                  "P/C basso (<0.7): posizionamento speculativo rialzista"
                  if pc and pc < 0.7 else "P/C neutrale")

        return {
            "ticker": ticker.upper(),
            "expiry_used": expiry,
            "spot": spot,
            "atm_strike": atm_c.get("strike"),
            "iv_atm_call": round(atm_c["iv"], 4) if atm_c.get("iv") else None,
            "iv_atm_put": round(atm_p["iv"], 4) if atm_p.get("iv") else None,
            "delta_atm_call": atm_c.get("delta"),
            "gamma_atm": atm_c.get("gamma"),
            "theta_atm": atm_c.get("theta"),
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "put_call_oi_ratio": pc,
            "interpretation_pc": interp,
            "max_pain_strike": max_pain,
            "max_pain_vs_spot_pct": (round((max_pain - spot) / spot * 100, 2)
                                     if (max_pain and spot) else None),
            "n_contracts": len(chain),
            "data_source": "polygon_options_starter (delayed 15min, greeks inclusi)",
            "_source": src,
            "_timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "_source": src}


def get_stock_daily(ticker: str, days: int = 120) -> Dict[str, Any]:
    """Aggregati daily US (richiede piano Stocks)."""
    from datetime import timedelta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = _get(f"/v2/aggs/ticker/{ticker.upper()}/range/1/day/{start}/{end}",
                {"adjusted": "true", "sort": "asc", "limit": 5000})
    if not data or data.get("error"):
        return {"error": (data or {}).get("error", "no data"),
                "_source": "polygon /v2/aggs"}
    bars = [{"date": datetime.utcfromtimestamp((b.get("t") or 0) / 1000).strftime("%Y-%m-%d"),
             "o": b.get("o"), "h": b.get("h"), "l": b.get("l"), "c": b.get("c"),
             "v": b.get("v")} for b in data.get("results", [])]
    return {"ticker": ticker.upper(), "n_bars": len(bars), "bars": bars,
            "_source": "polygon /v2/aggs", "_timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    # smoke test: python polygon_data.py
    if not polygon_available():
        print("POLYGON_API_KEY mancante in .env")
    else:
        ex = get_option_expirations("MSTR")
        if ex.get("error"):
            print("ERRORE:", ex["error"], "|", ex.get("_body", "")[:300])
        else:
            print("expirations MSTR:", ex.get("expirations", [])[:6])
        if ex.get("expirations"):
            ch = get_options_chain("MSTR", ex["expirations"][2] if len(ex["expirations"]) > 2 else ex["expirations"][0])
            print("contracts:", ch.get("n_contracts"), "| esempio:", (ch.get("chain") or [{}])[0])
