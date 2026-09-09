"""
benchmark_series.py — SERIE BENCHMARK UFFICIALE in EUR sul calendario TWR.

Voce §9-novodecies n.2 (ok PM 25/07): F2 v3.1 aveva un SPY-EUR client-side
dichiarato PROVVISORIO (commit f4e43c6); questa e' la serie UFFICIALE, unica
per il grafico F2 e per beta/alpha (advanced_metrics la riusa — prima erano
due implementazioni duplicate e DIVERSE: F2 price-only, advanced_metrics
total-return).

Scelte PM (25/07):
- benchmark ufficiale = SPY convertito in EUR; PARAMETRICO sul ticker, cosi'
  la decisione aperta Brinson-vs-benchmark (quale benchmark per questo book)
  restera' un parametro, non un rework;
- TOTAL-RETURN (Close auto_adjust=True, dividendi inclusi): coerente col book
  TWR total-return (F-CONT-3). Il salto vs la provvisoria F2 price-only e'
  dichiarato dal campo total_return nel payload.

Convenzioni DICHIARATE (regola no-fallback 14/07):
- calendario = date della serie TWR ufficiale (twr_engine.compute_twr_payload):
  stesso asse del book, mai un calendario terzo;
- allineamento per DATA; buco (festivita' USA vs borsa EU) = carry-forward
  dell'ultima chiusura, CONTATO in carried_days e coverage_pct, mai zitto;
- niente carry-forward in TESTA: i giorni TWR precedenti al primo dato
  benchmark disponibile sono esclusi e contati in leading_dropped;
- rebase indice base 100 sul primo giorno allineato (base_date);
- valuta di quotazione: USD convertito via EURUSD=X (USD per 1 EUR), EUR
  passa-through; altre valute = errore DICHIARATO, niente proxy;
- yfinance vuoto / TWR in errore = campo error valorizzato, mai [] zitto.
"""
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_CACHE: Dict[str, Any] = {}
CACHE_TTL_SEC = 600           # 10 min, come twr_engine/attribution/tearsheet
_FETCH_BUFFER_DAYS = 7        # margine prima della base TWR (weekend/festivi)
MIN_ALIGNED_DAYS = 2          # sotto, la serie non esiste: errore dichiarato

SUPPORTED_QUOTE_CCY = ("USD", "EUR")


def _log(msg: str):
    try:
        print(f"[BENCH] {msg}", flush=True)
    except OSError:
        pass


def _fetch_close(ticker: str, start: str, auto_adjust: bool) -> Dict[str, float]:
    """Chiusure da yfinance per data 'YYYY-MM-DD' (>0). Separata per i test."""
    import yfinance as yf
    px = yf.Ticker(ticker).history(start=start, auto_adjust=auto_adjust)["Close"]
    out: Dict[str, float] = {}
    for d, v in px.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == f and f > 0:  # NaN/zero guard
            out[d.strftime("%Y-%m-%d")] = f
    return out


def build_series(twr_dates: List[str],
                 px_by_date: Dict[str, float],
                 fx_usd_per_eur_by_date: Optional[Dict[str, float]],
                 ticker: str,
                 quote_currency: str = "USD",
                 total_return: bool = True) -> Dict[str, Any]:
    """Core PURO (testabile offline): converte in EUR, allinea al calendario
    TWR con carry-forward dichiarato, rebase 100. px/fx per data 'YYYY-MM-DD'."""
    base = {"ticker": ticker, "currency": "EUR", "quote_currency": quote_currency,
            "total_return": total_return, "aligned_to": "twr_index (twr_engine)"}
    if quote_currency not in SUPPORTED_QUOTE_CCY:
        return {**base, "error": "valuta di quotazione non supportata: "
                + str(quote_currency) + " (supportate: " + "/".join(SUPPORTED_QUOTE_CCY) + ")"}
    if not twr_dates:
        return {**base, "error": "calendario TWR vuoto"}
    if not px_by_date:
        return {**base, "error": "nessuna chiusura benchmark dal provider"}

    # 1) Conversione in EUR per DATA (solo giorni con px E fx entrambi presenti)
    if quote_currency == "EUR":
        eur_close = dict(px_by_date)
    else:
        fx = fx_usd_per_eur_by_date or {}
        eur_close = {d: px_by_date[d] / fx[d]
                     for d in px_by_date if d in fx and fx[d] > 0}
        if not eur_close:
            return {**base, "error": "nessun giorno con benchmark E cambio EURUSD=X"}

    # 2) Allineamento al calendario TWR: nativo dove c'e', carry-forward contato
    # (review B2: ordina e deduplica in difesa — oggi twr_engine garantisce gia')
    twr_dates = sorted(dict.fromkeys(str(d)[:10] for d in twr_dates))
    pre = [d for d in eur_close if d < twr_dates[0]]
    last: Optional[float] = eur_close[max(pre)] if pre else None
    aligned_dates: List[str] = []
    closes: List[float] = []
    carried_flags: List[bool] = []    # True = carry: il consumer beta li esclude
    carried_days, native_days, leading_dropped = 0, 0, 0
    for d in twr_dates:
        if d in eur_close:
            last = eur_close[d]
            native = True
        else:
            native = False
        if last is None:
            leading_dropped += 1      # prima del primo dato: escluso, non inventato
            continue
        aligned_dates.append(d)
        closes.append(last)
        carried_flags.append(not native)
        if native:
            native_days += 1
        else:
            carried_days += 1
    if len(aligned_dates) < MIN_ALIGNED_DAYS:
        return {**base, "error": "giorni allineati insufficienti ("
                + str(len(aligned_dates)) + " su " + str(len(twr_dates))
                + " del calendario TWR)", "leading_dropped": leading_dropped}

    # 3) Rebase 100 + rendimenti daily
    b0 = closes[0]
    index = [round(c / b0 * 100.0, 4) for c in closes]
    rets = [round(closes[i] / closes[i - 1] - 1.0, 8) for i in range(1, len(closes))]

    return {**base,
            "base_date": aligned_dates[0],
            "dates": aligned_dates,
            "close_eur": [round(c, 6) for c in closes],
            "index": index,
            "ret_daily": rets,
            "n": len(aligned_dates),
            "native_days": native_days,
            "carried_days": carried_days,
            "carried_flags": carried_flags,
            "leading_dropped": leading_dropped,
            "coverage_pct": round(native_days / len(aligned_dates) * 100.0, 1),
            "error": None}


def compute_benchmark_series(ticker: str = "SPY", force: bool = False,
                             twr_payload: Optional[Dict[str, Any]] = None,
                             quote_currency: str = "USD") -> Dict[str, Any]:
    """Serie benchmark ufficiale EUR total-return sul calendario TWR.
    Cache 10 min per ticker; twr_payload iniettabile per i test."""
    ticker = (ticker or "").strip().upper()
    key = ticker + "|" + quote_currency
    if not force and key in _CACHE:
        entry = _CACHE[key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return entry["data"]

    # 1) Calendario dal TWR ufficiale (stesso asse del book)
    if twr_payload is None:
        try:
            from bellomberg.portfolio.twr_engine import compute_twr_payload
            twr_payload = compute_twr_payload()
        except Exception as e:
            return {"ticker": ticker, "error": "serie TWR non disponibile: " + str(e)}
    if twr_payload.get("error"):
        return {"ticker": ticker,
                "error": "serie TWR in errore: " + str(twr_payload.get("error"))}
    twr_dates = [str(d)[:10] for d in (twr_payload.get("dates") or [])]
    if not twr_dates:
        return {"ticker": ticker, "error": "serie TWR senza date"}

    # 2) Dati provider: benchmark total-return + cambio, con buffer in testa
    start = (datetime.strptime(twr_dates[0], "%Y-%m-%d")
             - timedelta(days=_FETCH_BUFFER_DAYS)).strftime("%Y-%m-%d")
    try:
        px = _fetch_close(ticker, start, auto_adjust=True)
        fx = (_fetch_close("EURUSD=X", start, auto_adjust=False)
              if quote_currency == "USD" else None)
    except Exception as e:
        return {"ticker": ticker, "error": "provider non disponibile: " + str(e)}

    out = build_series(twr_dates, px, fx, ticker,
                       quote_currency=quote_currency, total_return=True)
    if not out.get("error"):
        # review B3: src/_computed_at solo sui payload riusciti (un errore
        # pre-fetch non deve implicare un fetch mai avvenuto)
        out["src"] = ("yfinance Close auto_adjust=True (total-return)"
                      + (" x EURUSD=X" if quote_currency == "USD" else ""))
        out["_computed_at"] = datetime.now().isoformat(timespec="seconds")
        _CACHE[key] = {"ts": time.time(), "data": out}
        _log(ticker + ": " + str(out["n"]) + " giorni allineati, coverage "
             + str(out["coverage_pct"]) + "%, carry " + str(out["carried_days"]) + "g")
    else:
        _log(ticker + " ERRORE: " + str(out["error"]))
    return out


def clear_cache():
    _CACHE.clear()
