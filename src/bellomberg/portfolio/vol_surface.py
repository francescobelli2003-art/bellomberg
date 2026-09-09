"""
vol_surface.py — Volatility Surface builder (#179, pagina F12)

Costruisce la superficie di volatilità implicita da chain Polygon multi-expiry:
  - griglia moneyness (K/S 0.80–1.20) × scadenza, IV interpolata per slice
  - composite OTM standard: put per K<S, call per K>S (più liquidi)
  - term structure ATM + 25Δ risk reversal + 25Δ butterfly per expiry
  - selezione expiry: prime 3 ravvicinate + diradamento settimanale fino a max_days

v1: superficie raw interpolata (np.interp per slice). Il fit SVI (Gatheral) con
check no-arbitrage butterfly/calendar è il raffinamento previsto in v2.
"""
from datetime import date, datetime, timezone
from copy import deepcopy
from concurrent.futures import Future
import math
import re
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlparse
from typing import Dict, Any, List, Optional

try:
    import numpy as np
    NP_OK = True
except ImportError:
    NP_OK = False

MONEYNESS_GRID = [round(0.80 + i * 0.025, 3) for i in range(17)]  # 0.80 .. 1.20

# Pages are shared by concurrent readers; downloads retain their own snapshots.
_CHAIN_CACHE = {}
_CHAIN_INFLIGHT = {}
_CHAIN_LOCK = Lock()
CHAIN_CACHE_SECONDS = 120


def _finite(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _symbol(ticker):
    value = str(ticker).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,24}", value):
        raise ValueError("ticker non valido")
    return value


def _expiry(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("scadenza richiesta nel formato YYYY-MM-DD")
    return date.fromisoformat(value)


def get_expiry_catalog(ticker: str, after: Optional[str] = None,
                       request_budget: int = 8) -> Dict[str, Any]:
    """Discover distinct dates without downloading chains or sampling months.

    A one-contract reference query identifies the next date; `gt` skips all
    contracts on it. The caller can resume from next_after until complete.
    A partial/error page is never labelled a complete exchange calendar.
    """
    from bellomberg.market_data import polygon_data as provider
    ticker = _symbol(ticker)
    if isinstance(request_budget, bool) or not isinstance(request_budget, int) or not 1 <= request_budget <= 12:
        raise ValueError("budget catalogo da 1 a 12 richieste")
    if after is not None:
        _expiry(after)
    result = {"ticker": ticker, "expirations": [], "complete": False,
              "next_after": after, "requests_used": 0, "request_budget": request_budget,
              "error": None, "_source": "Polygon options contract reference",
              "_timestamp": datetime.now(timezone.utc).isoformat()}
    if not provider.polygon_available():
        return {**result, "error": "POLYGON_API_KEY mancante o non attiva"}
    cursor = after
    for _ in range(request_budget):
        params = {"underlying_ticker": ticker, "limit": 1,
                  "sort": "expiration_date", "order": "asc", "expired": "false"}
        params["expiration_date.gt" if cursor else "expiration_date.gte"] = cursor or date.today().isoformat()
        page = provider._get("/v3/reference/options/contracts", params)
        result["requests_used"] += 1
        if not isinstance(page, dict) or page.get("error"):
            result["error"] = (page or {}).get("error", "catalogo non leggibile") if isinstance(page, dict) else "catalogo non leggibile"
            break
        rows = page.get("results")
        if not isinstance(rows, list):
            result["error"] = "risposta catalogo senza lista results"
            break
        if not rows:
            result["complete"] = True
            break
        candidate = rows[0].get("expiration_date") if isinstance(rows[0], dict) else None
        try:
            _expiry(candidate)
        except (TypeError, ValueError):
            result["error"] = "data scadenza non valida nel catalogo provider"
            break
        if (cursor and candidate <= cursor) or candidate < date.today().isoformat():
            result["error"] = "catalogo non avanza: filtro scadenza ignorato dal provider"
            break
        result["expirations"].append(candidate)
        cursor = candidate
        result["next_after"] = cursor
    return result


def _contract_row(raw):
    mapping = lambda value: value if isinstance(value, dict) else {}
    det, greek = mapping(raw.get("details")), mapping(raw.get("greeks"))
    quote, day = mapping(raw.get("last_quote")), mapping(raw.get("day"))
    bid, ask = _finite(quote.get("bid")), _finite(quote.get("ask"))
    bid = bid if bid is not None and bid >= 0 else None
    ask = ask if ask is not None and ask >= 0 else None
    stamp = _finite(quote.get("last_updated"), positive=True)
    quote_time = None
    if stamp is not None:
        try:
            quote_time = datetime.fromtimestamp(stamp / 1e9, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    warnings = []
    if bid is None or ask is None:
        warnings.append("bid/ask mancanti: permessi provider o quota non disponibile")
    elif ask < bid:
        warnings.append("bid superiore ad ask: quota incrociata")
    elif bid == ask == 0:
        warnings.append("bid e ask entrambi zero: nessuna quota negoziabile")
    quote_age = None
    if quote_time is None:
        warnings.append("timestamp quota assente: freschezza non verificabile")
    else:
        quote_age = (datetime.now(timezone.utc) - datetime.fromisoformat(quote_time)).total_seconds()
        if quote_age > 900:
            warnings.append("quota precedente al download di oltre 15 minuti; verificare timeframe e mercato")
        elif quote_age < -5:
            warnings.append("timestamp quota nel futuro: sincronizzazione orologi da verificare")
    iv = _finite(raw.get("implied_volatility"), positive=True)
    if iv is None:
        warnings.append("IV assente")
    greeks = {k: _finite(greek.get(k)) for k in ("delta", "gamma", "theta", "vega", "rho")}
    missing = [k for k, v in greeks.items() if v is None]
    if missing:
        warnings.append("greche assenti: " + ", ".join(missing))
    multiplier = _finite(det.get("shares_per_contract"), positive=True)
    if multiplier is None:
        warnings.append("moltiplicatore contratto assente")
    adjusted = bool(det.get("additional_underlyings"))
    if adjusted:
        warnings.append("deliverable aggiuntivi: contratto rettificato non rappresentabile dal simulatore standard")
    return {"contract": det.get("ticker"), "type": det.get("contract_type"),
            "strike": _finite(det.get("strike_price"), positive=True),
            "expiry": det.get("expiration_date"), "iv": iv, **greeks,
            "bid": bid, "ask": ask,
            "mid": (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid and ask > 0 else None,
            "oi": _finite(raw.get("open_interest")), "volume": _finite(day.get("volume")),
            "close": _finite(day.get("close")), "multiplier": multiplier,
            "exercise_style": det.get("exercise_style"),
            "adjusted": adjusted, "quote_age_seconds": quote_age,
            "quote_timestamp": quote_time, "quote_timeframe": quote.get("timeframe"),
            "quality": warnings, "_source": "Polygon option-chain snapshot"}


def get_chain_detail(ticker: str, expiry: str, cursor: Optional[str] = None) -> Dict[str, Any]:
    """One snapshot page (250 contracts); user-driven continuation, cached 120s.

    Only the opaque cursor is accepted, never a client-supplied URL. Provider
    timestamps survive separately from download time. Missing fields are null.
    """
    ticker = _symbol(ticker)
    _expiry(expiry)
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 4096 or not re.fullmatch(r"[A-Za-z0-9_+/=\-]+", cursor)):
        raise ValueError("cursore chain non valido")
    key = (ticker, expiry, cursor)
    with _CHAIN_LOCK:
        cached = _CHAIN_CACHE.get(key)
        if cached and monotonic() - cached[0] < CHAIN_CACHE_SECONDS:
            return {**deepcopy(cached[1]), "cached": True}
        pending = _CHAIN_INFLIGHT.get(key)
        leader = pending is None
        if leader:
            pending = _CHAIN_INFLIGHT[key] = Future()
    if not leader:
        return {**deepcopy(pending.result()), "cached": True}
    try:
        out = _fetch_chain_detail(ticker, expiry, cursor)
        with _CHAIN_LOCK:
            if not out.get("error"):
                if len(_CHAIN_CACHE) >= 128:
                    _CHAIN_CACHE.pop(next(iter(_CHAIN_CACHE)))
                _CHAIN_CACHE[key] = (monotonic(), deepcopy(out))
            pending.set_result(deepcopy(out))
        return out
    except BaseException as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _CHAIN_LOCK:
            _CHAIN_INFLIGHT.pop(key, None)


def _fetch_chain_detail(ticker, expiry, cursor):
    from bellomberg.market_data import polygon_data as provider
    base = {"ticker": ticker, "expiry": expiry, "chain": [], "complete": False,
            "next_cursor": None, "requests_used": 0, "page_limit": 250,
            "cached": False, "cache_ttl_seconds": CHAIN_CACHE_SECONDS,
            "_source": "Polygon option-chain snapshot", "_timestamp": datetime.now(timezone.utc).isoformat()}
    if not provider.polygon_available():
        return {**base, "error": "POLYGON_API_KEY mancante o non attiva"}
    params = {"expiration_date": expiry, "limit": 250, "sort": "strike_price", "order": "asc"}
    if cursor:
        params = {"cursor": cursor}
    page = provider._get(f"/v3/snapshot/options/{ticker}", params)
    base["requests_used"] = 1
    if not isinstance(page, dict) or page.get("error"):
        return {**base, "error": page.get("error", "chain non leggibile") if isinstance(page, dict) else "chain non leggibile"}
    if not isinstance(page.get("results"), list):
        return {**base, "error": "chain senza lista results"}
    rows = page["results"]
    parsed = [_contract_row(x) for x in rows if isinstance(x, dict)]
    # Reject cursor replay for another expiry instead of blending chains.
    wrong = [c for c in parsed if c["expiry"] is not None and c["expiry"] != expiry]
    if wrong:
        return {**base, "error": "chain restituita per una scadenza diversa da quella richiesta"}
    clean = [c for c in parsed if isinstance(c["contract"], str) and c["contract"].strip()
             and c["expiry"] == expiry and c["type"] in ("call", "put") and c["strike"] is not None]
    malformed = len(rows) - len(clean)
    nxt = page.get("next_url")
    token = parse_qs(urlparse(str(nxt)).query).get("cursor", [None])[0] if nxt else None
    error = None
    if nxt and (not token or token == cursor or len(token) > 4096 or not re.fullmatch(r"[A-Za-z0-9_+/=\-]+", token)):
        error = "continuazione provider assente o non avanzante"
    if malformed:
        error = f"{malformed} contratti malformati nella pagina provider"
    underlying = next((x["underlying_asset"] for x in rows
                       if isinstance(x, dict) and isinstance(x.get("underlying_asset"), dict)
                       and _finite(x["underlying_asset"].get("price"), positive=True) is not None), {})
    out = {**base, "chain": clean, "complete": not bool(nxt) and not error, "next_cursor": token,
           "n_contracts": len(clean), "spot": _finite(underlying.get("price"), positive=True),
           "spot_timeframe": underlying.get("timeframe"),
           "spot_timestamp_ns": _finite(underlying.get("last_updated")),
           "malformed_contracts": malformed, "error": error}
    return out


def _complete_chain(ticker, expiry):
    """Legacy explicit caller: exhaust pages, retain data on a failed continuation."""
    merged, seen, cursor, out = {}, set(), None, {}
    duplicates = malformed = 0
    while True:
        page = get_chain_detail(ticker, expiry, cursor)
        for row in page.get("chain") or []:
            key = row.get("contract") or (row.get("expiry"), row.get("type"), row.get("strike"))
            duplicates += key in merged
            merged[key] = row
        malformed += page.get("malformed_contracts", 0)
        spot = out.get("spot") or page.get("spot")
        out = {**page, "spot": spot, "chain": list(merged.values()),
               "duplicates": duplicates, "malformed_contracts": malformed}
        if page.get("error"):
            out.update(error=None if merged else page["error"], continuation_error=page["error"], complete=False)
            break
        seen.add(cursor)
        nxt = page.get("next_cursor")
        if page.get("complete"):
            break
        if not nxt or nxt in seen:
            _forget_chain_page(ticker, expiry, cursor)
            out.update(complete=False, continuation_error="ciclo o cursore chain non avanzante")
            break
        cursor = nxt
    return out


def _forget_chain_page(ticker, expiry, cursor):
    """A cross-page validation failure must be retried against the provider."""
    with _CHAIN_LOCK:
        _CHAIN_CACHE.pop((ticker, expiry, cursor), None)


def _select_expiries(expirations: List[str], max_expiries: int,
                     max_days: int) -> List[Dict[str, Any]]:
    """Selezione v2 (fix 22/07, segnalazione PM smile/plot): la v1 prendeva "le
    prime 3" — che sui nomi con scadenze GIORNALIERE (SPY/QQQ) erano 0/1/2 DTE —
    e il tetto si esauriva a ~37 giorni: mai una scadenza a 60-120g, term
    structure sempre monca (misurato sul probe SPY 22/07). Ora: 0-1 DTE ESCLUSI
    a monte (microstruttura), una scadenza per settimana ISO fino a ~6 settimane,
    una al mese oltre; se il tetto stringe si DIRADANO le settimanali centrali —
    la coda lunga non si sacrifica mai."""
    today = datetime.now().date()
    rows = []
    for e in expirations:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except Exception:
            continue
        days = (d - today).days
        if 2 <= days <= max_days:
            rows.append({"expiry": e, "days": days,
                         "week": d.isocalendar()[:2]})
    rows.sort(key=lambda r: r["days"])
    weekly: List[Dict[str, Any]] = []
    monthly: List[Dict[str, Any]] = []
    seen_weeks = set()
    seen_months = set()
    for r in rows:
        if r["days"] <= 42:
            if r["week"] not in seen_weeks:
                weekly.append(r)
                seen_weeks.add(r["week"])
        else:
            month = r["expiry"][:7]
            if month not in seen_months:
                monthly.append(r)
                seen_months.add(month)
    # tetto: si tolgono settimanali CENTRALI (front e ultima restano), mai le mensili
    while len(weekly) + len(monthly) > max_expiries and len(weekly) > 2:
        del weekly[len(weekly) // 2]
    chosen = weekly + monthly
    return chosen[:max_expiries]


def _slice_metrics(chain: List[Dict[str, Any]], spot: float) -> Optional[Dict[str, Any]]:
    """Metriche di uno slice: ATM IV, 25Δ RR/BF + curva composite OTM su griglia.
    Fix 22/07: filtro liquidità (OI=0 E volume=0 = quota stantia, esclusa e
    CONTATA) + dedup (tipo,strike) tenendo l'OI maggiore."""
    usable = [c for c in chain if _finite(c.get("iv"), positive=True) is not None
              and _finite(c.get("strike"), positive=True) is not None]
    liq = [c for c in usable if (c.get("oi") or 0) > 0 or (c.get("volume") or 0) > 0]
    n_illiq = len(usable) - len(liq)
    best: Dict[Any, Dict[str, Any]] = {}
    for c in liq:
        k = (c.get("type"), c["strike"])
        if k not in best or (c.get("oi") or 0) > (best[k].get("oi") or 0):
            best[k] = c
    liq = list(best.values())
    calls = [c for c in liq if c.get("type") == "call"]
    puts = [c for c in liq if c.get("type") == "put"]
    if len(calls) < 3 or len(puts) < 3:
        return None

    def nearest(rows, keyfn):
        return min(rows, key=keyfn)

    atm_c = nearest(calls, lambda c: abs(c["strike"] - spot))
    atm_p = nearest(puts, lambda c: abs(c["strike"] - spot))
    atm_iv = float(np.mean([atm_c["iv"], atm_p["iv"]]))

    rr25 = bf25 = None
    c_d = [c for c in calls if c.get("delta") is not None]
    p_d = [c for c in puts if c.get("delta") is not None]
    if c_d and p_d:
        c25 = nearest(c_d, lambda c: abs(c["delta"] - 0.25))
        p25 = nearest(p_d, lambda c: abs(c["delta"] + 0.25))
        if abs(c25["delta"] - 0.25) < 0.12 and abs(p25["delta"] + 0.25) < 0.12:
            rr25 = float(c25["iv"] - p25["iv"])
            bf25 = float(0.5 * (c25["iv"] + p25["iv"]) - atm_iv)

    # Composite OTM: put sotto spot, call sopra (convenzione standard di superficie)
    pts = ([(p["strike"] / spot, float(p["iv"])) for p in puts if p["strike"] <= spot]
           + [(c["strike"] / spot, float(c["iv"])) for c in calls if c["strike"] > spot])
    pts = sorted((m, iv) for m, iv in pts if 0.5 < m < 2.0 and 0.005 < iv < 5.0)
    if len(pts) < 5:
        return None
    xs = np.array([m for m, _ in pts])
    ys = np.array([iv for _, iv in pts])
    # Fix 22/07: mediana mobile a 3 punti sulle IV raw (estremi intatti) — sul
    # probe SPY lo smile raw aveva 15 inversioni di direzione su 180 punti
    # (pulito = 1-2): il rumore di microstruttura non è segnale. Il fit SVI con
    # no-arbitrage resta il v2 previsto; questa è una v1.5 dichiarata nel payload.
    if len(ys) >= 5:
        ys_s = ys.copy()
        for i in range(1, len(ys) - 1):
            ys_s[i] = np.median(ys[i - 1:i + 2])
        ys = ys_s
    grid_iv = np.interp(MONEYNESS_GRID, xs, ys, left=float("nan"), right=float("nan"))
    # fuori dal range osservato -> NaN -> il frontend li salta
    lo, hi = xs.min(), xs.max()
    grid = [round(float(v), 4) if (lo <= m <= hi and np.isfinite(v)) else None
            for m, v in zip(MONEYNESS_GRID, grid_iv)]
    call_oi = sum(int(c.get("oi") or 0) for c in calls)
    put_oi = sum(int(c.get("oi") or 0) for c in puts)
    return {"atm_iv": round(atm_iv, 4),
            "rr25": round(rr25, 4) if rr25 is not None else None,
            "bf25": round(bf25, 4) if bf25 is not None else None,
            "iv_grid": grid,
            "call_oi": call_oi, "put_oi": put_oi,
            "pc_oi_ratio": round(put_oi / call_oi, 3) if call_oi else None,
            "n_calls": len(calls), "n_puts": len(puts),
            "n_illiquidi_esclusi": n_illiq}


def build_vol_surface(ticker: str, max_expiries: int = 4,
                      max_days: int = 120, expiries: Optional[List[str]] = None,
                      include_context: bool = True, *, _snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    src = "polygon chains multi-expiry -> IV surface (composite OTM)"
    ticker = _symbol(ticker)
    coverage = {"selection_mode": "explicit" if expiries is not None else "sampled",
                "requested": [], "loaded": [], "excluded": [], "errors": [],
                "rows": [], "complete": False,
                "catalog_note": "Le scadenze della superficie sono una selezione; il catalogo completo è separato."}
    if expiries is not None:
        if not isinstance(expiries, list) or not expiries:
            raise ValueError("scegli almeno una scadenza distinta per la superficie")
        for value in expiries:
            _expiry(value)
        if len(set(expiries)) != len(expiries):
            raise ValueError("scegli scadenze distinte per la superficie")
    if not NP_OK:
        return {"error": "numpy non disponibile", "_source": src}
    try:
        from bellomberg.market_data.polygon_data import polygon_available, get_option_expirations, get_options_chain
        if _snapshot is None and not polygon_available():
            return {"error": "POLYGON_API_KEY mancante o non attiva", "_source": src}

        if expiries is None:
            exp = get_option_expirations(ticker)
            if exp.get("error"):
                return {"error": f"expirations: {exp['error']}", "_source": src, "coverage": coverage}
            chosen = _select_expiries(exp.get("expirations", []), max_expiries, max_days)
        else:
            chosen = [{"expiry": e, "days": (_expiry(e) - date.today()).days} for e in sorted(expiries)]
        coverage["requested"] = [r["expiry"] for r in chosen]
        if not chosen:
            return {"error": f"nessuna expiry entro {max_days} giorni", "_source": src}

        # Fix 22/07: spot dal SOTTOSTANTE vero (yfinance) — il proxy "strike della
        # call a delta~0.5" sbagliava di -0,30% su SPY (misurato) e fino a uno
        # strike intero sui nomi con passo 5$: spostava asse moneyness e giunzione
        # put/call. Il proxy resta SOLO come fallback, dichiarato in spot_source.
        spot = _snapshot.get("spot") if _snapshot is not None else None
        spot_source = _snapshot.get("spot_source") if _snapshot is not None else None
        try:
            if _snapshot is not None:
                raise RuntimeError("spot dallo snapshot in memoria")
            import yfinance as _yf
            # OPRA snapshots can include only the underlying ticker, without a
            # price. Read the observed spot once, also for explicit selections.
            _px = _yf.Ticker(ticker).fast_info["lastPrice"]
            if _finite(_px, positive=True) is not None:
                spot = float(_px)
                spot_source = "yfinance lastPrice"
        except Exception:
            pass
        slices = []
        for row in chosen:
            status = {"expiry": row["expiry"], "days": row["days"], "status": "error", "reason": None}
            coverage["rows"].append(status)
            if _snapshot is not None:
                status["chain_complete"] = bool(_snapshot["chains"].get(row["expiry"], {}).get("complete"))
            if row["days"] < 2:
                status.update(status="excluded", reason="0–1 DTE o scadenza passata: consulta la chain, non il mesh interpolato")
                coverage["excluded"].append(row["expiry"])
                continue
            if _snapshot is not None:
                ch = _snapshot["chains"].get(row["expiry"], {"chain": [], "complete": False, "error": "chain non ancora scaricata"})
            elif expiries is None:
                ch = get_options_chain(ticker, row["expiry"], max_contracts=400)
                status["chain_complete"] = None
            else:
                ch = _complete_chain(ticker, row["expiry"])
                status["chain_complete"] = bool(ch.get("complete")) and not ch.get("continuation_error")
                if spot is None and _finite(ch.get("spot"), positive=True) is not None:
                    spot, spot_source = ch["spot"], "Polygon underlying snapshot"
            if ch.get("error"):
                status["reason"] = ch["error"]
                coverage["errors"].append(row["expiry"])
                continue
            chain = ch.get("chain", [])
            if spot is None and expiries is None:
                cands = [c for c in chain
                         if c.get("type") == "call" and c.get("delta") is not None
                         and abs(c["delta"] - 0.5) < 0.08 and c.get("strike")]
                if cands:
                    spot = float(min(cands, key=lambda c: abs(c["delta"] - 0.5))["strike"])
                    spot_source = "PROXY strike call delta~0.5 (yfinance ko — dichiarato)"
            if spot is None:
                status["reason"] = "spot osservato assente: nessuna superficie costruita da uno strike proxy"
                coverage["errors"].append(row["expiry"])
                continue
            m = _slice_metrics(chain, spot)
            if m:
                slices.append({"expiry": row["expiry"], "days": row["days"], **m})
                partial = status["chain_complete"] is False
                status.update(status="partial" if partial else "loaded",
                              reason=(ch.get("continuation_error") or "download chain incompleto; sono rappresentati solo i contratti ricevuti") if partial else None,
                              n_contracts=len(chain))
                coverage["loaded"].append(row["expiry"])
            else:
                status.update(status="excluded", reason="IV/liquidità insufficienti per uno smile: almeno tre call e tre put liquide e cinque punti OTM")
                coverage["excluded"].append(row["expiry"])

        coverage["complete"] = bool(coverage["rows"]) and all(r["status"] == "loaded" and r.get("chain_complete") is True for r in coverage["rows"])
        coverage["download_complete"] = (bool(_snapshot.get("download_complete")) if _snapshot is not None else
                                         bool(coverage["rows"]) and all(r.get("chain_complete") is True for r in coverage["rows"]))

        if not slices:
            return {"error": "nessuno slice con dati IV sufficienti", "_source": src,
                    "ticker": ticker, "coverage": coverage, "slices": [],
                    "term_structure": [], "moneyness_grid": MONEYNESS_GRID, "n_expiries": 0}

        # interpretazione skew dalla prima slice con RR valido
        skew_note = None
        for s in slices:
            if s.get("rr25") is not None:
                skew_note = ("RR25 negativo: put più care delle call — domanda di protezione "
                             "al ribasso (skew classico equity)" if s["rr25"] < 0 else
                             "RR25 positivo: call più care delle put — domanda di upside "
                             "(insolito per equity: caccia al rialzo o squeeze atteso)")
                break

        # Realized vol 30g + percentile 1y + prossimi earnings (per lettura accurata)
        rv30 = None
        rv_pct_1y = None
        next_earnings = None
        try:
            if not include_context:
                raise RuntimeError("contesto aggiuntivo non richiesto")
            import yfinance as yf
            tk_obj = yf.Ticker(ticker)
            h = tk_obj.history(period="1y")["Close"].pct_change().dropna()
            if len(h) >= 30:
                rv_series = (h.rolling(22).std().dropna() * (252 ** 0.5))
                if len(rv_series):
                    rv30 = float(rv_series.iloc[-1])
                    rv_pct_1y = round(100.0 * float((rv_series <= rv30).mean()), 0)
            try:
                ed = tk_obj.earnings_dates
                if ed is not None and len(ed):
                    today_d = datetime.now().date()
                    fut = []
                    for d in ed.index:
                        try:
                            dd = d.tz_localize(None).date() if d.tzinfo else d.date()
                        except Exception:
                            continue
                        if dd >= today_d:
                            fut.append(dd)
                    if fut:
                        next_earnings = str(min(fut))
            except Exception:
                pass
        except Exception:
            pass

        # Movimento atteso ±1σ alla scadenza ~30 giorni (da ATM IV)
        expected_move = None
        exp_move_days = None
        mo = min(slices, key=lambda s: abs(s["days"] - 30))
        if mo and mo.get("atm_iv") and mo["days"] > 0:
            # audit/11 §4: 'days' sono giorni di CALENDARIO e la IV e' annualizzata
            # ACT/365 — dividere per 252 gonfiava il +/-1 sigma di ~20%.
            expected_move = round(mo["atm_iv"] * (mo["days"] / 365.0) ** 0.5 * 100, 1)
            exp_move_days = mo["days"]

        # Front per l'INTERPRETAZIONE: prima scadenza >= 2 giorni — gli 0DTE
        # hanno smile/OI distorti dalla microstruttura e falsano la lettura
        front = next((s for s in slices if s["days"] >= 2), slices[0])
        back = next((s for s in slices if s["days"] >= 45), slices[-1])
        term_slope = (round(back["atm_iv"] - front["atm_iv"], 4)
                      if back is not front and back["days"] > front["days"] + 10 else None)
        ivrv = (round(front["atm_iv"] - rv30, 4)
                if (rv30 and front.get("atm_iv")) else None)

        interpretation = _interpret(ticker, [front] + [s for s in slices if s is not front],
                                    term_slope, rv30, ivrv,
                                    expected_move=expected_move,
                                    exp_move_days=exp_move_days,
                                    rv_pct_1y=rv_pct_1y,
                                    next_earnings=next_earnings,
                                    back_days=back["days"] if back is not front else None)

        return {
            "ticker": ticker.upper(),
            "spot_est": spot,
            "spot_source": spot_source,
            "smoothing": "mediana mobile 3 punti su IV raw (v1.5, 22/07) — fit SVI no-arbitrage resta v2",
            "moneyness_grid": MONEYNESS_GRID,
            "slices": slices,
            "term_structure": [{"expiry": s["expiry"], "days": s["days"],
                                "atm_iv": s["atm_iv"], "rr25": s["rr25"],
                                "bf25": s["bf25"], "call_oi": s.get("call_oi"),
                                "put_oi": s.get("put_oi"),
                                "pc_oi_ratio": s.get("pc_oi_ratio")} for s in slices],
            "skew_note": skew_note,
            "term_slope_front_to_60d": term_slope,
            "realized_vol_30d": round(rv30, 4) if rv30 else None,
            "rv_percentile_1y": rv_pct_1y,
            "iv_rv_spread_front": ivrv,
            "expected_move_pct": expected_move,
            "expected_move_days": exp_move_days,
            "next_earnings": next_earnings,
            "interpretation": interpretation,
            "n_expiries": len(slices),
            "coverage": coverage,
            "context_requested": include_context,
            "_source": src,
            "_timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "_source": src}


def _interpret(ticker: str, slices: List[Dict[str, Any]],
               term_slope: Optional[float], rv30: Optional[float],
               ivrv: Optional[float],
               expected_move: Optional[float] = None,
               exp_move_days: Optional[int] = None,
               rv_pct_1y: Optional[float] = None,
               next_earnings: Optional[str] = None,
               back_days: Optional[int] = None) -> str:
    """Lettura della superficie in italiano professionale (rule-based, no AI)."""
    front = slices[0]
    parts: List[str] = []

    # 0. Movimento atteso (la sintesi che un PM vuole per prima)
    if expected_move is not None:
        parts.append(
            f"Il mercato delle opzioni prezza per {ticker.upper()} un movimento atteso di "
            f"±{expected_move:.1f}% entro la scadenza a {exp_move_days} giorni (1 deviazione "
            f"standard, da ATM IV). Sopra/sotto questo range il mercato è 'sorpreso'.")

    # 1. Term structure (+ contestualizzazione earnings se imminenti)
    atm_f = front["atm_iv"] * 100
    earn_note = ""
    if next_earnings:
        earn_note = f" Prossimi earnings attesi il {next_earnings}: se cadono prima della " \
                    f"scadenza lunga, parte della vol front è event premium fisiologico."
    back_label = f"a {back_days} giorni" if back_days else "sulle scadenze lunghe"
    if term_slope is not None:
        atm_b = (front["atm_iv"] + term_slope) * 100
        if term_slope > 0.01:
            parts.append(
                f"Term structure ascendente (contango): ATM {atm_f:.1f}% sul front contro "
                f"{atm_b:.1f}% {back_label}. Il mercato non prezza stress immediato su "
                f"{ticker.upper()}; l'incertezza è caricata sulle scadenze lunghe — regime ordinato."
                + earn_note)
        elif term_slope < -0.01:
            parts.append(
                f"Term structure INVERTITA: il front ({atm_f:.1f}%) tratta sopra il livello "
                f"{back_label} ({atm_b:.1f}%). Il mercato prezza un evento ravvicinato (earnings, "
                f"macro, catalyst): attenzione a vendere opzioni corte qui." + earn_note)
        else:
            parts.append(f"Term structure piatta intorno a {atm_f:.1f}% ATM: nessun evento "
                         f"specifico prezzato, vol uniforme sulle scadenze." + earn_note)
    else:
        parts.append(f"ATM front a {atm_f:.1f}% (curva corta disponibile)." + earn_note)

    # 1bis. Regime di volatilità realizzata (percentile 1 anno)
    if rv_pct_1y is not None:
        regime_rv = ("regime di movimento COMPRESSO" if rv_pct_1y <= 25 else
                     "regime di movimento ELEVATO" if rv_pct_1y >= 75 else
                     "regime di movimento nella media")
        parts.append(f"La realized vol 30 giorni è al {rv_pct_1y:.0f}° percentile dell'ultimo "
                     f"anno: {regime_rv}. Gli estremi tendono a rientrare (mean reversion "
                     f"della volatilità).")

    # 2. Skew (RR25)
    rr = front.get("rr25")
    if rr is not None:
        rr_pt = rr * 100
        if rr_pt < -3:
            parts.append(f"Skew put MARCATO (RR25 {rr_pt:.1f}pt): la protezione al ribasso è "
                         f"cara — domanda di hedge consistente; chi compra put qui paga premio pieno, "
                         f"chi le vende viene pagato bene per il rischio.")
        elif rr_pt < -0.5:
            parts.append(f"Skew put nella norma equity (RR25 {rr_pt:.1f}pt): fisiologica "
                         f"domanda di protezione, nessun allarme.")
        elif rr_pt > 0.5:
            parts.append(f"Skew INVERTITO a favore delle call (RR25 +{rr_pt:.1f}pt): il mercato "
                         f"paga per l'upside — tipico di squeeze attesi, M&A o retail chase. "
                         f"Covered call ben remunerate.")
        else:
            parts.append(f"Skew neutro (RR25 {rr_pt:.1f}pt): smile simmetrico.")

    # 3. Curvatura (BF25)
    bf = front.get("bf25")
    if bf is not None and bf * 100 > 1.5:
        parts.append(f"Butterfly 25Δ elevato ({bf*100:.1f}pt): le code sono care in entrambe "
                     f"le direzioni — il mercato paga i tail scenario, attesa di movimento ampio.")

    # 4. IV vs RV (vol risk premium)
    if ivrv is not None and rv30 is not None:
        sp = ivrv * 100
        if sp > 3:
            parts.append(f"IV front {atm_f:.1f}% contro realized 30g {rv30*100:.1f}%: premio di "
                         f"{sp:.1f}pt — le opzioni sono CARE rispetto al movimento effettivo. "
                         f"Contesto favorevole a strategie di vendita di premio coperta "
                         f"(covered call), sfavorevole all'acquisto di protezione.")
        elif sp < -3:
            parts.append(f"IV front {atm_f:.1f}% SOTTO la realized 30g ({rv30*100:.1f}%): opzioni "
                         f"a sconto rispetto al movimento reale — l'hedge in put costa poco, "
                         f"vendere premio qui è mal pagato.")
        else:
            parts.append(f"IV front {atm_f:.1f}% allineata alla realized 30g ({rv30*100:.1f}%): "
                         f"vol risk premium nella norma.")

    # 5. Posizionamento OI
    pc = front.get("pc_oi_ratio")
    if pc is not None:
        if pc > 1.3:
            parts.append(f"Open interest sbilanciato sulle put (P/C {pc:.2f}): posizionamento "
                         f"difensivo già costruito sul front.")
        elif pc < 0.6:
            parts.append(f"Open interest sbilanciato sulle call (P/C {pc:.2f}): posizionamento "
                         f"speculativo rialzista sul front.")

    return "\n\n".join(parts)


if __name__ == "__main__":
    import json
    r = build_vol_surface("SPY", max_expiries=6)
    out = {k: v for k, v in r.items() if k != "slices"}
    print(json.dumps(out, indent=1)[:2500])
    if r.get("slices"):
        s0 = r["slices"][0]
        print("\nprima slice:", s0["expiry"], "atm", s0["atm_iv"],
              "rr25", s0["rr25"], "bf25", s0["bf25"])
