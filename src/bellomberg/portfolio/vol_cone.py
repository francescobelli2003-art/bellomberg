"""
vol_cone.py — VOL CONE: realized vol per orizzonte vs term structure implied
(residuo dichiarato della voce (39) IV History; lotto c, ok PM 25/07 sera).

Il problema: l'IV Rank (iv_history) dice dove sta l'IV di oggi rispetto alla
SUA storia, ma non dice se l'implied PAGA rispetto alla vol realizzata. Il
cone classico: per ogni orizzonte (5/10/21/63 giorni di borsa) la distribuzione
della realized vol rolling sull'ultimo anno (min/p25/p50/p75/max + corrente),
affiancata alla term structure ATM IV corrente (riuso vol_surface, Polygon).

Convenzioni DICHIARATE (regola no-fallback 14/07):
- realized = rendimenti SEMPLICI giornalieri (pct_change), std CAMPIONARIA
  (ddof=1) sulla finestra, annualizzata sqrt(252): la STESSA convenzione della
  rv30 di vol_surface (che usa finestra 22g) — mai due definizioni di rv in casa;
- percentili con interpolazione lineare (formula numpy 'linear', implementata
  a mano e testata a valori a mano);
- confronto IV-vs-cone: i days delle expiry sono di CALENDARIO (vol_surface),
  le finestre di BORSA -> conversione days*252/365 PRIMA del matching (review
  M1 25/07: senza, la banda 43-60g calendario — incluso il 45DTE canonico —
  finiva sulla finestra trimestrale invece che mensile); poi finestra più
  vicina per |giorni borsa - window| (pareggio -> corta) e % di valori
  realized <= ATM IV (stessa formula percentile di iv_history);
- finestra senza abbastanza dati = error dichiarato PER FINESTRA, mai inventata;
- n_obs sempre nel payload; sotto YOUNG_THRESHOLD_OBS la finestra e' dichiarata
  young (poche osservazioni rolling: percentili poco affidabili, come iv_history);
- vol_surface giu' (Polygon) = implied.error dichiarato, la parte realized resta;
- ticker fuori dalla lista del negozio iv_tickers = error dichiarato (lista estendibile su
  richiesta PM, stessa scelta del collector iv_history);
- cache 10' SOLO su payload completi (top-level ok E implied ok): un giro con
  implied.error non si cacha, il prossimo tentativo ritenta Polygon.
"""
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from bellomberg.core.language import scoped_language
from bellomberg.core.presentation import message as _message, render_payload

from bellomberg.market_data.iv_history import YOUNG_THRESHOLD_OBS   # soglia dichiarata UNA volta (review B5)

CACHE_TTL_SEC = 600           # 10 min, come benchmark_series/twr_engine
WINDOWS: Sequence[int] = (5, 10, 21, 63)   # giorni di BORSA
ANNUALIZE_DAYS = 252
HISTORY_PERIOD = "1y"
_CACHE: Dict[str, Dict[str, Any]] = {}

BASIS = ("realized: rendimenti semplici giornalieri su chiusure yfinance "
         + HISTORY_PERIOD + ", std campionaria (ddof=1) rolling per finestra, "
         "annualizzata sqrt(252) — stessa convenzione della rv30 di vol_surface; "
         "percentili a interpolazione lineare; confronto = % dei valori realized "
         "<= ATM IV della expiry abbinata (days di calendario convertiti in "
         "borsa x252/365, poi finestra piu' vicina)")


def _log(msg: str):
    # pipe stdout morta (backend zombie, autopsia (40)): log perso, mai eccezione
    try:
        print(f"[VOLCONE] {msg}", flush=True)
    except OSError:
        pass


def _sample_std(vals: Sequence[float]) -> float:
    """Std campionaria (ddof=1). Richiede >=2 valori (il chiamante garantisce)."""
    n = len(vals)
    mean = sum(vals) / n
    return (sum((v - mean) ** 2 for v in vals) / (n - 1)) ** 0.5


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Percentile q in [0,100] su lista GIA' ordinata, interpolazione lineare
    (formula numpy 'linear'). Richiede lista non vuota."""
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = (n - 1) * (q / 100.0)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n:
        return float(sorted_vals[-1])
    return float(sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo]))


def _rolling_vol_series(rets: Sequence[float], window: int) -> List[float]:
    """Serie rolling della realized vol annualizzata per la finestra data.
    Vuota se i rendimenti non bastano (mai valori inventati)."""
    if window < 2 or len(rets) < window:
        return []
    out = []
    for i in range(window - 1, len(rets)):
        out.append(_sample_std(rets[i - window + 1:i + 1]) * (ANNUALIZE_DAYS ** 0.5))
    return out


@scoped_language
def build_cone(closes: Sequence[float],
               windows: Sequence[int] = WINDOWS) -> Dict[str, Any]:
    """CORE PURO (testabile a valori a mano): dalle chiusure (vecchio->nuovo)
    al cone per finestra. Nessuna rete, nessuna cache."""
    closes = list(closes or [])
    if len(closes) < 3:
        return {"error": _message("chiusure insufficienti (n={n}, servono >=3)",
                                  "insufficient closing prices (n={n}, at least 3 required)", n=len(closes))}
    if any((c is None) or (not isinstance(c, (int, float))) or (c != c)
           or (c <= 0) for c in closes):
        return {"error": _message("chiusure non valide (valori nulli, NaN, non numerici o <=0)",
                                  "invalid closing prices (null, NaN, nonnumeric or <=0 values)")}
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]

    out_windows: List[Dict[str, Any]] = []
    for w in windows:
        serie = _rolling_vol_series(rets, w)
        if not serie:
            out_windows.append({"window": int(w), "n_obs": 0,
                                "error": _message("dati insufficienti per la finestra {w}g (rendimenti={n}, servono >={w})",
                                                  "insufficient data for the {w}d window (returns={n}, at least {w} required)",
                                                  w=w, n=len(rets))})
            continue
        s = sorted(serie)
        out_windows.append({
            "window": int(w),
            "n_obs": len(serie),
            "young": len(serie) < YOUNG_THRESHOLD_OBS,
            "current": round(serie[-1], 4),
            "min": round(s[0], 4),
            "p25": round(_percentile(s, 25), 4),
            "p50": round(_percentile(s, 50), 4),
            "p75": round(_percentile(s, 75), 4),
            "max": round(s[-1], 4),
        })
    return {"windows": out_windows, "n_returns": len(rets)}


def _nearest_window(trading_days: int, windows: Sequence[int]) -> int:
    """Finestra realized abbinata alla expiry: |giorni di BORSA - window|
    minima, pareggio -> finestra corta (dichiarato in BASIS). Il chiamante
    converte i days di calendario di vol_surface PRIMA (review M1)."""
    return min(windows, key=lambda w: (abs(trading_days - w), w))


def _to_trading_days(calendar_days: int) -> int:
    """days di vol_surface (CALENDARIO) -> giorni di borsa (x252/365, review
    M1: senza conversione un 45DTE finiva sulla finestra trimestrale)."""
    return round(int(calendar_days) * 252 / 365)


def _fetch_closes(ticker: str) -> List[float]:
    """Chiusure yfinance (vecchio->nuovo). Separata per i test (pattern
    benchmark_series._fetch_close)."""
    import yfinance as yf
    h = yf.Ticker(ticker).history(period=HISTORY_PERIOD)["Close"].dropna()
    return [float(c) for c in h.tolist()]


@scoped_language
def compute_vol_cone(ticker: str, force: bool = False) -> Dict[str, Any]:
    """Wrapper con fetch + implied + cache. Errori mai cachati."""
    ticker = (ticker or "").upper().strip()
    from bellomberg.market_data.iv_history import stato_iv_tickers   # lazy: la lista dichiarata e' una sola
    st = stato_iv_tickers()
    if st["origine"] in ("assente", "illeggibile"):
        return {"ticker": ticker,
                "error": _message("negozio iv_tickers {state}: {reason} — nessuna lista vol dichiarata, nessun cono",
                                  "iv_tickers registry status {state}: {reason} — no declared volatility list, no cone",
                                  state=st['origine'], reason=st['motivo']),
                "src": "vol_cone"}
    if ticker not in st["tickers"]:
        return {"ticker": ticker,
                "error": _message("ticker fuori dalla lista vol dichiarata {tickers} (si estende nel negozio data/iv_tickers.json)",
                                  "ticker outside the declared volatility list {tickers} (extend it in data/iv_tickers.json)",
                                  tickers=list(st['tickers'])),
                "src": "vol_cone"}

    now = time.time()
    ck = f"cone:{ticker}"
    if not force and ck in _CACHE and now - _CACHE[ck]["ts"] < CACHE_TTL_SEC:
        return render_payload(_CACHE[ck]["data"])

    try:
        closes = _fetch_closes(ticker)
    except Exception as e:
        return {"ticker": ticker, "error": _message("chiusure non disponibili: {error}", "closing prices unavailable: {error}", error=str(e)),
                "src": "vol_cone (closes yfinance)"}

    cone = build_cone(closes)
    if cone.get("error"):
        return {"ticker": ticker, "error": cone["error"],
                "src": "vol_cone (closes yfinance)"}

    # Term structure implied corrente (riuso vol_surface, stessa fonte di (39))
    implied: Dict[str, Any]
    try:
        from bellomberg.portfolio.vol_surface import build_vol_surface
        vs = build_vol_surface(ticker)
        if vs.get("error"):
            implied = {"error": _message("vol_surface: {error}", "vol_surface: {error}", error=vs['error'])}
        else:
            implied = {"slices": [{"expiry": s.get("expiry"),
                                   "days": s.get("days"),
                                   "atm_iv": s.get("atm_iv")}
                                  for s in (vs.get("slices") or [])
                                  if s.get("atm_iv") is not None
                                  and s.get("days") is not None],
                       "src": "vol_surface (Polygon chains)"}
    except Exception as e:
        implied = {"error": f"vol_surface: {e}"}

    # Confronto IV vs cone: percentile dell'ATM IV nella distribuzione realized
    # della finestra abbinata (formula iv_history: % valori <= corrente).
    # rets e serie per finestra calcolati UNA volta (review B4: niente formula
    # duplicata per slice, niente divergenza futura).
    confronto: List[Dict[str, Any]] = []
    if not implied.get("error"):
        ok_windows = {w["window"] for w in cone["windows"] if not w.get("error")}
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
        serie_per_w: Dict[int, List[float]] = {}
        for sl in implied["slices"]:
            w = _nearest_window(_to_trading_days(sl["days"]), WINDOWS)
            if w not in ok_windows:
                confronto.append({**sl, "window": w,
                                  "error": _message("finestra {w}g senza cone (dati insufficienti)",
                                                    "no cone for the {w}d window (insufficient data)", w=w)})
                continue
            if w not in serie_per_w:
                serie_per_w[w] = _rolling_vol_series(rets, w)
            serie = serie_per_w[w]
            pct = round(100.0 * sum(1 for v in serie if v <= sl["atm_iv"]) / len(serie), 1)
            confronto.append({"expiry": sl["expiry"], "days": sl["days"],
                              "atm_iv": sl["atm_iv"], "window": w,
                              "pct_realized_leq_iv": pct})

    out = {
        "ticker": ticker,
        "asof": datetime.now().isoformat(),
        "period": HISTORY_PERIOD,
        "realized": {"windows": cone["windows"], "n_returns": cone["n_returns"]},
        "implied": implied,
        "confronto": confronto,
        "basis": _message(BASIS,
                          "realized: daily simple returns on yfinance closing prices " + HISTORY_PERIOD
                          + ", rolling sample standard deviation (ddof=1), annualized by sqrt(252) — "
                          "same convention as vol_surface rv30; linearly interpolated percentiles; "
                          "comparison = % of realized values <= matched expiry ATM IV "
                          "(calendar days converted to trading days x252/365, then nearest window)"),
        "src": "vol_cone (closes yfinance; implied vol_surface/Polygon)",
    }
    # cache SOLO se completo: con implied.error il prossimo giro ritenta Polygon
    if not implied.get("error"):
        _CACHE[ck] = {"ts": now, "data": out}
    else:
        _log(f"{ticker}: implied non disponibile ({implied['error']}) — non cachato")
    return render_payload(out)
