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
from bellomberg.core.presentation import message as _surface_text, render_payload, join_messages, error_text


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

# 09/10 (audit Vol Deck, Opus 5.5) — convenzioni DICHIARATE nel payload.
# M3: soglie di qualita' della quota per entrare nello smile.
MAX_REL_SPREAD = 1.0            # (ask-bid)/mid > 1  <=>  ask > 3*bid: quota non negoziabile
# M-8 (review v2, 10/10): su opzioni da pochi centesimi uno spread di 2-3 tick supera
# la soglia relativa ed escludeva proprio i 25Δ dei single name a basso prezzo.
# Spread <= MIN_SPREAD_TICKS * TICK_REFERENCE e' AMMESSO anche se relativo > soglia.
# Tick di riferimento 0,05 $ = il piu' largo sotto 3 $ (classe non-penny); il tick
# vero per contratto non e' nel payload Polygon (dichiarato in quality_filters).
TICK_REFERENCE = 0.05
MIN_SPREAD_TICKS = 3
STALE_QUOTE_SECONDS = 900       # quota piu' vecchia di 15' del riferimento dello slice
# M-4 (review v2): riferimento = 75° percentile (nearest-rank) dei timestamp dello
# slice, non il massimo: un solo contratto con l'orologio 20' avanti non deve
# rendere «stantie» tutte le altre quote.
STALE_REFERENCE_PERCENTILE = 75
# rr25 (review v2): tasso e dividendo per il delta BS ricalcolato. Il payload
# Polygon non li porta: 0 DICHIARATO nel payload (rr25_rate_r / rr25_dividend_q).
RR25_RATE_R = 0.0
RR25_DIVIDEND_Q = 0.0
# BASSI (review v2): festivi NYSE a giornata intera, anni 2025-2028 (fonte: calendario
# pubblicato NYSE; chiusure anticipate alle 13:00 NON modellate). Fuori da questi
# anni i festivi non sono modellati e la nota lo dichiara.
NYSE_HOLIDAYS = frozenset({
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19", "2028-07-04",
    "2028-09-04", "2028-11-23", "2028-12-25",
})
NYSE_HOLIDAY_YEARS = (2025, 2028)
IV_RANGE = (0.005, 5.0)         # IV fuori range = valore del provider non usabile
# A4: una richiesta HTTP non riscarica piu' di tante scadenze (ogni scadenza = chain completa).
MAX_FETCH_EXPIRIES = 12
# M8/B2: una sola RV "a un mese" in casa: 21 sedute (= finestra del cono).
RV_WINDOW_SESSIONS = 21
TENOR_DAYS = 30                 # IV a scadenza costante (stessa di iv_history.TENOR_DAYS)
BACK_TENOR_DAYS = 60            # B1: la pendenza "front_to_60d" misura davvero 60 giorni
_NY = "America/New_York"


def _ny_now():
    """Ora di New York (B3): giorni a scadenza e seduta si contano sul calendario
    del mercato, non sull'orologio locale italiano. Separata per i test."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(_NY))


def _days_to_expiry(expiry: str, now_ny=None) -> int:
    """Giorni di CALENDARIO fra la data di New York di adesso e la scadenza."""
    now_ny = now_ny or _ny_now()
    return (_expiry(expiry) - now_ny.date()).days


def _t_years(expiry: str, now_ny=None) -> float:
    """Tempo alla CHIUSURA della scadenza (16:00 New York), ACT/365, mai negativo.
    BASSI review v2: differenza calcolata in UTC — fra due datetime con lo STESSO
    tzinfo Python sottrae l'ora di parete e perdeva l'ora del cambio d'ora."""
    from zoneinfo import ZoneInfo
    now_ny = now_ny or _ny_now()
    d = _expiry(expiry)
    close = datetime(d.year, d.month, d.day, 16, 0, tzinfo=ZoneInfo(_NY))
    elapsed = close.astimezone(timezone.utc) - now_ny.astimezone(timezone.utc)
    return max(0.0, elapsed.total_seconds()) / (365.0 * 86400.0)


def _market_session(now_ny=None) -> Dict[str, Any]:
    """M6: seduta di riferimento dei dati. Lun-ven 09:30-16:00 NY = aperto, salvo
    festivi NYSE (NYSE_HOLIDAYS, anni NYSE_HOLIDAY_YEARS); fuori orario, nel
    weekend o in un festivo i dati sono quelli dell'ULTIMA seduta (data dichiarata).
    Chiusure anticipate (13:00) non modellate; fuori dagli anni coperti i festivi
    non sono modellati: tutto dichiarato in `note`/`holidays_basis`."""
    from datetime import timedelta
    now_ny = now_ny or _ny_now()
    minutes = now_ny.hour * 60 + now_ny.minute
    trading = lambda d: d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS
    today = now_ny.date()
    is_open = trading(today) and 570 <= minutes < 960
    session = today
    if not trading(today) or minutes < 570:
        session -= timedelta(days=1)
        while not trading(session):
            session -= timedelta(days=1)
    lo, hi = NYSE_HOLIDAY_YEARS
    covered = lo <= today.year <= hi
    holiday_note = (_surface_text(f'festivi NYSE {lo}-{hi} modellati (chiusure anticipate no)', f'NYSE holidays {lo}-{hi} modeled (early closes not)')
                    if covered else
                    _surface_text(f'festivi di borsa non modellati fuori dal {lo}-{hi}', f'Exchange holidays not modeled outside {lo}-{hi}'))
    return {"asof_utc": now_ny.astimezone(timezone.utc).isoformat(),
            "asof_new_york": now_ny.isoformat(),
            "market_open": is_open, "session_date": session.isoformat(),
            "is_exchange_holiday": today.isoformat() in NYSE_HOLIDAYS,
            "holidays_basis": holiday_note,
            "note": ((_surface_text('mercato aperto (orario regolare 09:30-16:00 New York)', 'Market open (regular hours 09:30-16:00 New York)')
                      if covered else
                      join_messages("; ", [_surface_text('mercato aperto (orario regolare 09:30-16:00 New York)', 'Market open (regular hours 09:30-16:00 New York)'), holiday_note]))
                     if is_open else
                     join_messages("; ", [_surface_text(f'mercato chiuso: dati della seduta del {session.isoformat()} o precedenti', f'Market closed: data from the {session.isoformat()} session or earlier'), holiday_note]))}


def _finite(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _symbol(ticker):
    value = str(ticker).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,24}", value):
        raise ValueError(_surface_text('ticker non valido', 'Invalid ticker'))
    return value


def _expiry(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(_surface_text('scadenza richiesta nel formato YYYY-MM-DD', 'Expiry required in YYYY-MM-DD format'))
    return date.fromisoformat(value)


def _opzioni_non_coperte(ticker, base):
    """Le due funzioni sotto chiamano `polygon_data._get` DIRETTAMENTE, quindi la
    guardia delle funzioni pubbliche del provider non le copre (P1, 04/10, Opus 5.5):
    un `XXX.MI` interrogava OPRA a vuoto e tornava un errore generico. Ora
    l'astensione dichiarata arriva PRIMA della rete, con `requests_used` 0.
    Solo `non_coperto`: un suffisso fuori registro (classe di azioni col punto)
    passa intatto, v. polygon_data._opzioni_non_coperte."""
    from bellomberg.market_data.copertura import copertura_opzioni, risposta_non_coperta
    esito = copertura_opzioni(ticker)
    if esito["stato"] != "non_coperto":
        return None
    return {**base, **risposta_non_coperta(esito, base["_source"])}


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
        raise ValueError(_surface_text('budget catalogo da 1 a 12 richieste', 'Catalog budget must be 1 to 12 requests'))
    if after is not None:
        _expiry(after)
    result = {"ticker": ticker, "expirations": [], "complete": False,
              "next_after": after, "requests_used": 0, "request_budget": request_budget,
              "error": None, "_source": "Polygon options contract reference",
              "_timestamp": datetime.now(timezone.utc).isoformat()}
    fuori = _opzioni_non_coperte(ticker, result)
    if fuori:
        return fuori
    if not provider.polygon_available():
        return {**result, "error": _surface_text('POLYGON_API_KEY mancante o non attiva', 'POLYGON_API_KEY missing or inactive')}
    cursor = after
    for _ in range(request_budget):
        params = {"underlying_ticker": ticker, "limit": 1,
                  "sort": "expiration_date", "order": "asc", "expired": "false"}
        params["expiration_date.gt" if cursor else "expiration_date.gte"] = cursor or date.today().isoformat()
        page = provider._get("/v3/reference/options/contracts", params)
        result["requests_used"] += 1
        if not isinstance(page, dict) or page.get("error"):
            result["error"] = (page or {}).get("error", _surface_text('catalogo non leggibile', 'Unreadable catalog')) if isinstance(page, dict) else _surface_text('catalogo non leggibile', 'Unreadable catalog')
            break
        rows = page.get("results")
        if not isinstance(rows, list):
            result["error"] = _surface_text('risposta catalogo senza lista results', 'Catalog response has no results list')
            break
        if not rows:
            result["complete"] = True
            break
        candidate = rows[0].get("expiration_date") if isinstance(rows[0], dict) else None
        try:
            _expiry(candidate)
        except (TypeError, ValueError):
            result["error"] = _surface_text('data scadenza non valida nel catalogo provider', 'Invalid expiry date in provider catalog')
            break
        if (cursor and candidate <= cursor) or candidate < date.today().isoformat():
            result["error"] = _surface_text('catalogo non avanza: filtro scadenza ignorato dal provider', 'Catalog does not advance: provider ignored the expiry filter')
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
        warnings.append(_surface_text('bid/ask mancanti: permessi provider o quota non disponibile', 'Missing bid/ask: provider permissions or quote unavailable'))
    elif ask < bid:
        warnings.append(_surface_text('bid superiore ad ask: quota incrociata', 'Bid above ask: crossed quote'))
    elif bid == ask == 0:
        warnings.append(_surface_text('bid e ask entrambi zero: nessuna quota negoziabile', 'Bid and ask both zero: no tradable quote'))
    quote_age = None
    if quote_time is None:
        warnings.append(_surface_text('timestamp quota assente: freschezza non verificabile', 'Quote timestamp missing: freshness cannot be verified'))
    else:
        quote_age = (datetime.now(timezone.utc) - datetime.fromisoformat(quote_time)).total_seconds()
        if quote_age > 900:
            warnings.append(_surface_text('quota precedente al download di oltre 15 minuti; verificare timeframe e mercato', 'Quote predates download by over 15 minutes; check timeframe and market'))
        elif quote_age < -5:
            warnings.append(_surface_text('timestamp quota nel futuro: sincronizzazione orologi da verificare', 'Quote timestamp is in the future: check clock synchronization'))
    iv = _finite(raw.get("implied_volatility"), positive=True)
    if iv is None:
        warnings.append(_surface_text('IV assente', 'Missing IV'))
    greeks = {k: _finite(greek.get(k)) for k in ("delta", "gamma", "theta", "vega", "rho")}
    missing = [k for k, v in greeks.items() if v is None]
    if missing:
        warnings.append(_surface_text(f"greche assenti: {', '.join(missing)}", f"Missing Greeks: {', '.join(missing)}"))
    multiplier = _finite(det.get("shares_per_contract"), positive=True)
    if multiplier is None:
        warnings.append(_surface_text('moltiplicatore contratto assente', 'Missing contract multiplier'))
    adjusted = bool(det.get("additional_underlyings"))
    if adjusted:
        warnings.append(_surface_text('deliverable aggiuntivi: contratto rettificato non rappresentabile dal simulatore standard', 'Additional deliverables: adjusted contract cannot be represented by the standard simulator'))
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
        raise ValueError(_surface_text('cursore chain non valido', 'Invalid chain cursor'))
    key = (ticker, expiry, cursor)
    with _CHAIN_LOCK:
        cached = _CHAIN_CACHE.get(key)
        if cached and monotonic() - cached[0] < CHAIN_CACHE_SECONDS:
            return {**render_payload(cached[1]), "cached": True}
        pending = _CHAIN_INFLIGHT.get(key)
        leader = pending is None
        if leader:
            pending = _CHAIN_INFLIGHT[key] = Future()
    if not leader:
        return {**render_payload(pending.result()), "cached": True}
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
    fuori = _opzioni_non_coperte(ticker, base)
    if fuori:
        return fuori
    if not provider.polygon_available():
        return {**base, "error": _surface_text('POLYGON_API_KEY mancante o non attiva', 'POLYGON_API_KEY missing or inactive')}
    params = {"expiration_date": expiry, "limit": 250, "sort": "strike_price", "order": "asc"}
    if cursor:
        params = {"cursor": cursor}
    page = provider._get(f"/v3/snapshot/options/{ticker}", params)
    base["requests_used"] = 1
    if not isinstance(page, dict) or page.get("error"):
        return {**base, "error": page.get("error", _surface_text('chain non leggibile', 'Unreadable chain')) if isinstance(page, dict) else _surface_text('chain non leggibile', 'Unreadable chain')}
    if not isinstance(page.get("results"), list):
        return {**base, "error": _surface_text('chain senza lista results', 'Chain has no results list')}
    rows = page["results"]
    parsed = [_contract_row(x) for x in rows if isinstance(x, dict)]
    # Reject cursor replay for another expiry instead of blending chains.
    wrong = [c for c in parsed if c["expiry"] is not None and c["expiry"] != expiry]
    if wrong:
        return {**base, "error": _surface_text('chain restituita per una scadenza diversa da quella richiesta', 'Returned chain has a different expiry from the request')}
    clean = [c for c in parsed if isinstance(c["contract"], str) and c["contract"].strip()
             and c["expiry"] == expiry and c["type"] in ("call", "put") and c["strike"] is not None]
    malformed = len(rows) - len(clean)
    nxt = page.get("next_url")
    token = parse_qs(urlparse(str(nxt)).query).get("cursor", [None])[0] if nxt else None
    error = None
    if nxt and (not token or token == cursor or len(token) > 4096 or not re.fullmatch(r"[A-Za-z0-9_+/=\-]+", token)):
        error = _surface_text('continuazione provider assente o non avanzante', 'Provider continuation missing or not advancing')
    if malformed:
        error = _surface_text(f'{malformed} contratti malformati nella pagina provider', f'{malformed} malformed contracts in the provider page')
    underlying = next((x["underlying_asset"] for x in rows
                       if isinstance(x, dict) and isinstance(x.get("underlying_asset"), dict)
                       and _finite(x["underlying_asset"].get("price"), positive=True) is not None), {})
    out = {**base, "chain": clean, "complete": not bool(nxt) and not error, "next_cursor": token,
           "n_contracts": len(clean), "spot": _finite(underlying.get("price"), positive=True),
           "spot_timeframe": underlying.get("timeframe"),
           "spot_timestamp_ns": _finite(underlying.get("last_updated")),
           "malformed_contracts": malformed, "error": error}
    return out


def _is_rate_limited(error) -> bool:
    return error is not None and "429" in str(error)


def _complete_chain(ticker, expiry):
    """Legacy explicit caller: exhaust pages, retain data on a failed continuation.

    v2 review 10/10 (Opus 5.5): (a) prezzo, orario e timeframe del sottostante
    vengono dalla STESSA pagina (l'osservazione con last_updated piu' recente),
    mai il prezzo della prima pagina con l'orario dell'ultima; (b) un HTTP 429 su
    una pagina di continuazione si PROPAGA (`rate_limited`), cosi' il chiamante
    smette di interrogare il provider; i contratti gia' ricevuti restano, parziali."""
    merged, seen, cursor, out = {}, set(), None, {}
    duplicates = malformed = 0
    under = None   # (timestamp_ns, spot, timeframe) della stessa pagina
    while True:
        page = get_chain_detail(ticker, expiry, cursor)
        for row in page.get("chain") or []:
            key = row.get("contract") or (row.get("expiry"), row.get("type"), row.get("strike"))
            duplicates += key in merged
            merged[key] = row
        malformed += page.get("malformed_contracts", 0)
        if _finite(page.get("spot"), positive=True) is not None:
            ns = _finite(page.get("spot_timestamp_ns"), positive=True)
            obs = (ns if ns is not None else -1.0, page["spot"], page.get("spot_timeframe"), ns)
            if under is None or obs[0] > under[0]:
                under = obs
        out = {**page, "chain": list(merged.values()),
               "spot": under[1] if under else None,
               "spot_timeframe": under[2] if under else None,
               "spot_timestamp_ns": under[3] if under else None,
               "duplicates": duplicates, "malformed_contracts": malformed}
        if page.get("error"):
            out.update(error=None if merged else page["error"], continuation_error=page["error"], complete=False,
                       rate_limited=_is_rate_limited(page["error"]))
            break
        seen.add(cursor)
        nxt = page.get("next_cursor")
        if page.get("complete"):
            break
        if not nxt or nxt in seen:
            _forget_chain_page(ticker, expiry, cursor)
            out.update(complete=False, continuation_error=_surface_text('ciclo o cursore chain non avanzante', 'Chain cursor cycle or no progress'))
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
    la coda lunga non si sacrifica mai.

    v3 09/10 (M7 audit Vol Deck, Opus 5.5): la v2 tagliava con
    `chosen[:max_expiries]` proprio la coda (SPY, tetto 4: [3, 38, 45, 56] giorni,
    84 e 119 persi). Ora: settimanale = il venerdi' della settimana ISO (se c'e');
    mensile = la standard del terzo venerdi' (la piu' liquida), altrimenti la prima
    del mese (kind dichiarato). Protette: front, la piu' lunga e le due scadenze a
    cavallo dei 30 giorni (servono all'IV a scadenza costante). Al tetto si tolgono
    prima le settimanali non protette, poi le mensili non protette, sempre la
    CENTRALE; mai la front ne' la piu' lunga."""
    now_ny = _ny_now()
    rows, seen = [], set()
    for e in expirations:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except Exception:
            continue
        days = (d - now_ny.date()).days
        if 2 <= days <= max_days and e not in seen:
            seen.add(e)
            rows.append({"expiry": e, "days": days, "_date": d})
    rows.sort(key=lambda r: r["days"])
    weekly: Dict[Any, Dict[str, Any]] = {}
    monthly: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = r["_date"]
        if r["days"] <= 42:
            key = d.isocalendar()[:2]
            best = weekly.get(key)
            if best is None or (d.weekday() == 4 and best["_date"].weekday() != 4):
                weekly[key] = {**r, "kind": "weekly_friday" if d.weekday() == 4 else "weekly"}
        else:
            third_friday = d.weekday() == 4 and 15 <= d.day <= 21
            kind = "monthly_standard" if third_friday else "monthly_friday" if d.weekday() == 4 else "monthly_first"
            rank = {"monthly_standard": 0, "monthly_friday": 1, "monthly_first": 2}
            best = monthly.get(r["expiry"][:7])
            if best is None or rank[kind] < rank[best["kind"]]:
                monthly[r["expiry"][:7]] = {**r, "kind": kind}
    chosen = sorted(list(weekly.values()) + list(monthly.values()), key=lambda r: r["days"])
    # BASSI review v2 (10/10): una mensile NON standard (venerdi' qualsiasi o primo
    # giornaliero del mese) a meno di MIN_GAP_DAYS da un'altra scadenza scelta e'
    # un quasi-doppione (es. il giornaliero del 43° giorno accanto al venerdi' del
    # 42°): si scarta. Se e' la piu' lunga si scarta la vicina, mai la front.
    MIN_GAP_DAYS = 5
    i = 1
    while i < len(chosen):
        prev, cur = chosen[i - 1], chosen[i]
        if cur["days"] - prev["days"] < MIN_GAP_DAYS:
            weak = lambda r: r["kind"] in ("monthly_first", "monthly_friday")
            if weak(cur) and i < len(chosen) - 1:
                del chosen[i]
                continue
            if (weak(cur) or weak(prev)) and i - 1 > 0:
                del chosen[i - 1]
                continue
        i += 1
    if not chosen:
        return []
    below = [r for r in chosen if r["days"] <= TENOR_DAYS]
    above = [r for r in chosen if r["days"] >= TENOR_DAYS]
    protected = {chosen[0]["expiry"], chosen[-1]["expiry"]}
    protected |= {below[-1]["expiry"]} if below else set()
    protected |= {above[0]["expiry"]} if above else set()
    while len(chosen) > max(1, max_expiries):
        idx = [i for i, r in enumerate(chosen) if r["expiry"] not in protected and r["kind"].startswith("weekly")]
        idx = idx or [i for i, r in enumerate(chosen) if r["expiry"] not in protected]
        idx = idx or list(range(1, len(chosen) - 1)) or [len(chosen) - 1]
        del chosen[idx[len(idx) // 2]]
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in chosen]


def _quote_defect(c: Dict[str, Any], newest: Optional[datetime]) -> Optional[str]:
    """M3: motivo per cui una quota NON entra nello smile, o None. Bid/ask ASSENTI
    (piano provider senza quote) non sono un difetto: la riga resta e viene contata
    come non verificabile dal chiamante — escluderla svuoterebbe ogni smile."""
    iv = _finite(c.get("iv"))
    if iv is None or not IV_RANGE[0] < iv < IV_RANGE[1]:
        return "iv_out_of_range"
    bid, ask = _finite(c.get("bid")), _finite(c.get("ask"))
    if bid is not None and ask is not None:
        if ask < bid:
            return "crossed"
        if bid <= 0:
            return "no_bid"
        # M-8: spread di pochi tick ammesso anche se relativo > soglia (+1e-9: float)
        if (ask - bid) / ((ask + bid) / 2) > MAX_REL_SPREAD and ask - bid > MIN_SPREAD_TICKS * TICK_REFERENCE + 1e-9:
            return "wide_spread"
    stamp = _quote_time(c)
    if stamp is not None and newest is not None and (newest - stamp).total_seconds() > STALE_QUOTE_SECONDS:
        return "stale"
    return None


def _quote_time(c: Dict[str, Any]) -> Optional[datetime]:
    value = c.get("quote_timestamp")
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _bs_delta(S: float, K: float, T: float, sigma: float, typ: str,
              r: float = RR25_RATE_R, q: float = RR25_DIVIDEND_Q) -> float:
    """Delta Black-Scholes-Merton spot (europeo), r e q continui."""
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    n = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    return math.exp(-q * T) * (n if typ == "call" else n - 1.0)


def _iv_at_delta(rows: List[Dict[str, Any]], target: float, spot: Optional[float] = None,
                 t_years: Optional[float] = None, r: float = RR25_RATE_R, q: float = RR25_DIVIDEND_Q):
    """IV al delta `target` (+0,25 call, -0,25 put). v2 (review 10/10, Opus 5.5):
    smile LINEARE IN STRIKE sui contratti del tipo, e K(Δ=target) risolto per
    BISEZIONE col delta BS RICALCOLATO sull'IV interpolata (r, q dichiarati). La
    v1.6 interpolava linearmente in delta del provider: il delta non e' lineare
    in K e sbagliava di 0,5-5 punti di vol sugli smile ripidi a strike radi.
    Nessun cambio di segno nell'intervallo degli strike osservati = None (mai
    estrapolato, mai il "delta piu' vicino").
    Ritorna (iv, [k_basso, k_alto], [delta_basso, delta_alto], K_risolto)."""
    if spot is None or not t_years or t_years <= 0:
        return None, None, None, None
    pts = sorted({float(c["strike"]): float(c["iv"]) for c in rows}.items())
    if len(pts) < 2:
        return None, None, None, None
    typ = "call" if target > 0 else "put"

    def siv(K):
        for (k1, v1), (k2, v2) in zip(pts, pts[1:]):
            if k1 <= K <= k2:
                return v1 + (K - k1) * (v2 - v1) / (k2 - k1)
        return None

    f = lambda K: _bs_delta(spot, K, t_years, siv(K), typ, r, q) - target
    lo, hi = pts[0][0], pts[-1][0]
    flo, fhi = f(lo), f(hi)
    if flo == 0:
        K = lo
    elif fhi == 0:
        K = hi
    elif (flo > 0) == (fhi > 0):
        return None, None, None, None
    else:
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            fm = f(mid)
            if (fm > 0) == (flo > 0):
                lo, flo = mid, fm
            else:
                hi = mid
        K = 0.5 * (lo + hi)
    iv = siv(K)
    k1, k2 = next(((a, b) for (a, _), (b, _) in zip(pts, pts[1:]) if a <= K <= b), (K, K))
    deltas = [round(_bs_delta(spot, k, t_years, dict(pts)[k], typ, r, q), 4) for k in (k1, k2)]
    return iv, [k1, k2], deltas, K


def _median3(ys):
    out = ys.copy()
    if len(ys) >= 3:
        for i in range(1, len(ys) - 1):
            out[i] = np.median(ys[i - 1:i + 2])
    return out


def _slice_t_years(chain: List[Dict[str, Any]]) -> Optional[float]:
    """Tempo a scadenza dalla scadenza dichiarata dai contratti (unica), o None."""
    exps = {c.get("expiry") for c in chain if isinstance(c.get("expiry"), str)}
    if len(exps) != 1:
        return None
    try:
        return _t_years(exps.pop())
    except ValueError:
        return None


def _stale_reference(stamps: List[datetime]) -> Optional[datetime]:
    """M-4: 75° percentile nearest-rank (lato basso) dei timestamp, non il massimo."""
    if not stamps:
        return None
    vals = sorted(stamps)
    return vals[max(0, math.ceil(STALE_REFERENCE_PERCENTILE / 100.0 * len(vals)) - 1)]


def _slice_detail(chain: List[Dict[str, Any]], spot: float, t_years: Optional[float] = None):
    """(metriche | None, motivo | None). v2 09/10 (audit Vol Deck, Opus 5.5):
    - A3: contratti rettificati (deliverable aggiuntivi o moltiplicatore != 100)
      ESCLUSI e contati: la loro IV calcolata come standard non ha senso;
    - M3: quote non negoziabili (bid<=0, incrociate, spread relativo > MAX_REL_SPREAD,
      piu' vecchie di STALE_QUOTE_SECONDS della piu' recente, IV fuori IV_RANGE)
      escluse e contate per motivo;
    - M2: UNA sola ATM = IV della curva composita OTM interpolata a K/S = 1 (la
      stessa curva di iv_grid, quindi iv_grid[1.0] == atm_iv);
    - M1 (v2 10/10): 25Δ = smile lineare in strike + K(Δ=0,25) risolto col delta BS
      ricalcolato (r, q dichiarati), n.d. se il delta non e' raggiunto negli strike;
    - B6: mediana a 3 punti applicata SEPARATAMENTE alle due ali (put / call),
      mai attraverso la giunzione.
    Fix 22/07 invariato: OI=0 E volume=0 = esclusa e contata; dedup (tipo,strike)
    tenendo l'OI maggiore."""
    usable = [c for c in chain if _finite(c.get("iv"), positive=True) is not None
              and _finite(c.get("strike"), positive=True) is not None]
    adjusted = [c for c in usable if c.get("adjusted")
                or (_finite(c.get("multiplier"), positive=True) is not None and c.get("multiplier") != 100)]
    standard = [c for c in usable if not any(c is a for a in adjusted)]
    liq = [c for c in standard if (c.get("oi") or 0) > 0 or (c.get("volume") or 0) > 0]
    n_illiq = len(standard) - len(liq)
    stamps = [s for s in (_quote_time(c) for c in liq) if s is not None]
    newest = _stale_reference(stamps)
    discards: Dict[str, int] = {}
    clean = []
    for c in liq:
        why = _quote_defect(c, newest)
        if why:
            discards[why] = discards.get(why, 0) + 1
        else:
            clean.append(c)
    unverifiable = sum(1 for c in clean if _finite(c.get("bid")) is None or _finite(c.get("ask")) is None)
    best: Dict[Any, Dict[str, Any]] = {}
    for c in clean:
        k = (c.get("type"), c["strike"])
        if k not in best or (c.get("oi") or 0) > (best[k].get("oi") or 0):
            best[k] = c
    liq = list(best.values())
    calls = [c for c in liq if c.get("type") == "call"]
    puts = [c for c in liq if c.get("type") == "put"]
    counts = {"n_illiquidi_esclusi": n_illiq, "n_rettificati_esclusi": len(adjusted),
              "n_quote_scartate": sum(discards.values()), "quote_discards": discards,
              "n_quote_non_verificabili": unverifiable}
    if len(calls) < 3 or len(puts) < 3:
        return None, _surface_text(f'IV/liquidità insufficienti per uno smile: {len(calls)} call e {len(puts)} put utilizzabili (minimo 3+3); scartate: {counts}', f'Insufficient IV/liquidity for a smile: {len(calls)} calls and {len(puts)} puts usable (minimum 3+3); discarded: {counts}')

    # Composite OTM: put sotto/allo spot, call sopra (convenzione standard di superficie)
    wing_p = sorted((p["strike"] / spot, float(p["iv"]), p["strike"]) for p in puts if p["strike"] <= spot)
    wing_c = sorted((c["strike"] / spot, float(c["iv"]), c["strike"]) for c in calls if c["strike"] > spot)
    wing_p = [x for x in wing_p if 0.5 < x[0] < 2.0]
    wing_c = [x for x in wing_c if 0.5 < x[0] < 2.0]
    if len(wing_p) + len(wing_c) < 5:
        return None, _surface_text(f'meno di cinque punti OTM ({len(wing_p)} put, {len(wing_c)} call) per uno smile', f'Fewer than five OTM points ({len(wing_p)} puts, {len(wing_c)} calls) for a smile')
    # Fix 22/07: mediana mobile a 3 punti sulle IV raw (estremi intatti) — sul
    # probe SPY lo smile raw aveva 15 inversioni di direzione su 180 punti
    # (pulito = 1-2). B6: per ala, mai attraverso la giunzione put/call.
    xs = np.array([m for m, _, _ in wing_p] + [m for m, _, _ in wing_c])
    ys = np.concatenate([_median3(np.array([v for _, v, _ in wing_p])),
                         _median3(np.array([v for _, v, _ in wing_c]))])
    lo, hi = xs.min(), xs.max()
    if not lo <= 1.0 <= hi:
        return None, _surface_text(f'spot fuori dagli strike OTM osservati (K/S {lo:.3f}–{hi:.3f}): ATM non interpolabile, mai estrapolata', f'Spot outside the observed OTM strikes (K/S {lo:.3f}–{hi:.3f}): ATM cannot be interpolated and is never extrapolated')
    atm_iv = float(np.interp(1.0, xs, ys))
    below = [k for _, _, k in wing_p]
    above = [k for _, _, k in wing_c]
    atm_strikes = [below[-1] if below else None, above[0] if above else None]
    grid_iv = np.interp(MONEYNESS_GRID, xs, ys, left=float("nan"), right=float("nan"))
    # fuori dal range osservato -> null -> il frontend li salta
    grid = [round(float(v), 4) if (lo <= m <= hi and np.isfinite(v)) else None
            for m, v in zip(MONEYNESS_GRID, grid_iv)]

    if t_years is None:
        t_years = _slice_t_years(chain)
    c25, c25_k, c25_d, c25_K = _iv_at_delta(calls, 0.25, spot, t_years)
    p25, p25_k, p25_d, p25_K = _iv_at_delta(puts, -0.25, spot, t_years)
    rr25 = bf25 = None
    rr25_reason = None
    if c25 is not None and p25 is not None:
        rr25 = c25 - p25
        bf25 = 0.5 * (c25 + p25) - atm_iv
    elif not t_years or t_years <= 0:
        rr25_reason = _surface_text('25Δ n.d.: tempo a scadenza non disponibile per ricalcolare il delta', '25Δ n/a: time to expiry unavailable to recompute delta')
    else:
        lati = [n for n, v in (("call +0,25", c25), ("put -0,25", p25)) if v is None]
        rr25_reason = _surface_text(f"25Δ n.d.: delta {', '.join(lati)} non raggiunto dentro gli strike osservati (mai estrapolato)", f"25Δ n/a: delta {', '.join(lati)} not reached within the observed strikes (never extrapolated)")
    used = calls + puts
    used_stamps = [s for s in (_quote_time(c) for c in used) if s is not None]
    timeframes = sorted({str(c.get("quote_timeframe")) for c in used if c.get("quote_timeframe")})
    call_oi = sum(int(c.get("oi") or 0) for c in calls)
    put_oi = sum(int(c.get("oi") or 0) for c in puts)
    r4 = lambda v: round(float(v), 4) if v is not None else None
    return {"atm_iv": round(atm_iv, 4),
            "atm_strikes_used": atm_strikes,
            "rr25": r4(rr25), "bf25": r4(bf25),
            "iv_25d_call": r4(c25), "iv_25d_put": r4(p25),
            "rr25_call_strikes": c25_k, "rr25_put_strikes": p25_k,
            "rr25_call_deltas": c25_d, "rr25_put_deltas": p25_d,
            "k25_call": r4(c25_K), "k25_put": r4(p25_K),
            "rr25_reason": rr25_reason,
            "iv_grid": grid,
            "call_oi": call_oi, "put_oi": put_oi,
            "pc_oi_ratio": round(put_oi / call_oi, 3) if call_oi else None,
            "n_calls": len(calls), "n_puts": len(puts),
            **counts,
            "quote_time_min": min(used_stamps).isoformat() if used_stamps else None,
            "quote_time_max": max(used_stamps).isoformat() if used_stamps else None,
            "n_quote_senza_timestamp": len(used) - len(used_stamps),
            "quote_timeframes": timeframes}, None


def _slice_metrics(chain: List[Dict[str, Any]], spot: float, t_years: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Metriche di uno slice (v. `_slice_detail`); None se lo smile non si forma."""
    return _slice_detail(chain, spot, t_years)[0]


def build_vol_surface(ticker: str, max_expiries: int = 6,
                      max_days: int = 120, expiries: Optional[List[str]] = None,
                      include_context: bool = True, *, _snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    src = "polygon chains multi-expiry -> IV surface (composite OTM)"
    ticker = _symbol(ticker)
    coverage = {"selection_mode": "explicit" if expiries is not None else "sampled",
                "requested": [], "loaded": [], "excluded": [], "errors": [],
                "rows": [], "complete": False,
                "catalog_note": _surface_text('Le scadenze della superficie sono una selezione; il catalogo completo è separato.', 'Surface expiries are a selection; the complete catalog is separate.')}
    if expiries is not None:
        if not isinstance(expiries, list) or not expiries:
            raise ValueError(_surface_text('scegli almeno una scadenza distinta per la superficie', 'Select at least one distinct expiry for the surface'))
        for value in expiries:
            _expiry(value)
        if len(set(expiries)) != len(expiries):
            raise ValueError(_surface_text('scegli scadenze distinte per la superficie', 'Select distinct expiries for the surface'))
        # A4 (09/10): ogni scadenza esplicita = chain COMPLETA riscaricata dentro la
        # richiesta HTTP. Oltre il tetto si rifiuta (422 dalla rotta), dichiarato:
        # il contesto di un download si chiede su /options/download/{id}/context.
        if _snapshot is None and len(expiries) > MAX_FETCH_EXPIRIES:
            raise ValueError(_surface_text(f'troppe scadenze da riscaricare ({len(expiries)}, massimo {MAX_FETCH_EXPIRIES}): per il contesto di un download usa /options/download/{{id}}/context, che lavora sulla sua istantanea', f'Too many expiries to re-download ({len(expiries)}, maximum {MAX_FETCH_EXPIRIES}): for a download context use /options/download/{{id}}/context, which works on its snapshot'))
    if not NP_OK:
        return {"error": _surface_text('numpy non disponibile', 'numpy unavailable'), "_source": src}
    try:
        from bellomberg.market_data.polygon_data import polygon_available, get_option_expirations, get_options_chain
        if _snapshot is None and not polygon_available():
            return {"error": _surface_text('POLYGON_API_KEY mancante o non attiva', 'POLYGON_API_KEY missing or inactive'), "_source": src}

        if expiries is None:
            exp = get_option_expirations(ticker)
            if exp.get("error"):
                return {"error": f"expirations: {exp['error']}", "_source": src, "coverage": coverage}
            chosen = _select_expiries(exp.get("expirations", []), max_expiries, max_days)
        else:
            chosen = [{"expiry": e, "days": _days_to_expiry(e)} for e in sorted(expiries)]
        coverage["requested"] = [r["expiry"] for r in chosen]
        if not chosen:
            return {"error": _surface_text(f'nessuna expiry entro {max_days} giorni', f'No expiry within {max_days} days'), "_source": src}

        # SPOT (ordine PM 10/10, «per le opzioni uso Polygon», Opus 5.5): primario =
        # prezzo del SOTTOSTANTE Polygon dalla stessa istantanea delle chain, con il
        # suo last_updated; yfinance solo RIPIEGO dichiarato (spot_fallback, non
        # qualificato: niente orario della quota, istante diverso dalle chain); il
        # proxy-strike (solo ramo campionato) lascia ATM/RR/BF e movimento atteso
        # n.d. — contratto R02. Prima: yfinance PRIMA di Polygon anche con Polygon
        # disponibile. Fix 22/07 invariato: mai uno strike al posto di uno spot in
        # silenzio (il proxy sbagliava di -0,30% su SPY e fino a uno strike intero).
        spot = _snapshot.get("spot") if _snapshot is not None else None
        spot_source = _snapshot.get("spot_source") if _snapshot is not None else None
        # M6 (09/10): l'orario dello spot, con il suo tipo.
        spot_timestamp = _snapshot.get("spot_timestamp") if _snapshot is not None else None
        spot_timestamp_kind = (_surface_text('orario registrato dal download', 'Time recorded by the download') if _snapshot is not None and spot_timestamp else None)
        spot_timeframe = _snapshot.get("spot_timeframe") if _snapshot is not None else None
        spot_qualified = bool(_snapshot.get("spot_qualified")) if _snapshot is not None else False
        spot_fallback = bool(_snapshot.get("spot_fallback")) if _snapshot is not None else False
        spot_proxy = None  # R02-b: codice macchina quando lo spot e' uno strike
        spot_alignment = _snapshot.get("spot_alignment") if _snapshot is not None else None   # MA-1 (review v2)
        spot_reason = None
        now_ny = _ny_now()
        slices = []
        stop_reason = None
        fetched = []   # (row, status, ch) — fase 1: tutte le chain, poi lo spot
        for row in chosen:
            status = {"expiry": row["expiry"], "days": row["days"], "status": "error", "reason": None}
            if row.get("kind"):
                status["selection_kind"] = row["kind"]
            coverage["rows"].append(status)
            if _snapshot is not None:
                status["chain_complete"] = bool(_snapshot["chains"].get(row["expiry"], {}).get("complete"))
            if row["days"] < 2:
                status.update(status="excluded", reason=_surface_text('0–1 DTE o scadenza passata: consulta la chain, non il mesh interpolato', '0–1 DTE or expired: consult the chain, not the interpolated mesh'))
                coverage["excluded"].append(row["expiry"])
                continue
            if stop_reason is not None:
                # B5 (09/10): dopo un 429 non si martella il provider; le scadenze
                # restanti NON vengono richieste e lo si dichiara riga per riga.
                status.update(reason=stop_reason, chain_complete=False)
                coverage["errors"].append(row["expiry"])
                continue
            if _snapshot is not None:
                ch = _snapshot["chains"].get(row["expiry"], {"chain": [], "complete": False, "error": _surface_text('chain non ancora scaricata', 'Chain not downloaded yet')})
            else:
                # A1 (09/10): anche il ramo campionato (cono, IV rank, signal_engine)
                # legge la chain COMPLETA per strike, come il ramo esplicito; prima
                # `get_options_chain(max_contracts=400)` teneva i 400 strike piu'
                # bassi e marcava la riga «loaded».
                ch = _complete_chain(ticker, row["expiry"])
                status["chain_complete"] = bool(ch.get("complete")) and not ch.get("continuation_error")
            rate_limited = _snapshot is None and (_is_rate_limited(ch.get("error")) or bool(ch.get("rate_limited")))
            if rate_limited:
                # M-6 (review v2): anche un 429 su una pagina di CONTINUAZIONE ferma il
                # ciclo; i contratti gia' ricevuti di questa scadenza restano, parziali.
                stop_reason = _surface_text(f'non richiesta: stop dopo HTTP 429 sulla scadenza {row["expiry"]} (rate limit del provider; riprova più tardi)', f'Not requested: stopped after HTTP 429 on expiry {row["expiry"]} (provider rate limit; retry later)')
            if ch.get("error"):
                status["reason"] = ch["error"]
                coverage["errors"].append(row["expiry"])
                continue
            fetched.append((row, status, ch))

        if _snapshot is None:
            # MA-1 (review v2, 10/10, Opus 5.5): sul piano del PM `underlying_asset`
            # porta SOLO il ticker, quindi lo spot Polygon non scattava mai e ogni
            # superficie usciva con yfinance lastPrice (tempo reale) contro quote di
            # 15' prima. Ordine in spot_alignment.resolve_spot (pubblica, riusabile
            # da compute_gex): 1) prezzo Polygon della pagina piu' recente; 2) barra
            # yfinance 1m all'ora delle quote (qualificata entro tolleranza);
            # 3) lastPrice NON qualificato; 4) None -> proxy R02 sotto.
            from bellomberg.market_data.spot_alignment import quote_anchor, resolve_spot
            obs = [((_finite(ch.get("spot_timestamp_ns"), positive=True) or -1.0), ch) for _, _, ch in fetched
                   if _finite(ch.get("spot"), positive=True) is not None]
            underlying = None
            if obs:
                _, best = max(obs, key=lambda x: x[0])
                underlying = {"price": best["spot"], "timestamp_ns": best.get("spot_timestamp_ns"),
                              "timeframe": best.get("spot_timeframe")}
            anchor = quote_anchor((c.get("quote_timestamp") for _, _, ch in fetched for c in ch.get("chain") or []),
                                  future_tolerance_seconds=STALE_QUOTE_SECONDS)
            res = resolve_spot(ticker, underlying=underlying, anchor=anchor)
            if res["spot"] is not None:
                spot = float(res["spot"])
                spot_source, spot_timestamp = res["spot_source"], res["spot_timestamp"]
                spot_timestamp_kind, spot_timeframe = res["spot_timestamp_kind"], res["spot_timeframe"]
                spot_qualified, spot_fallback = bool(res["spot_qualified"]), bool(res["spot_fallback"])
            spot_alignment, spot_reason = res["spot_alignment"], res["spot_reason"]

        for row, status, ch in fetched:
            chain = ch.get("chain", [])
            if not chain:
                # B4 (09/10): una scadenza senza contratti non e' «IV insufficiente».
                status.update(status="excluded", reason=_surface_text('nessun contratto listato per questa scadenza', 'No contracts listed for this expiry'), n_contracts=0)
                coverage["excluded"].append(row["expiry"])
                continue
            if spot is None and expiries is None:
                cands = [c for c in chain
                         if c.get("type") == "call" and c.get("delta") is not None
                         and abs(c["delta"] - 0.5) < 0.08 and c.get("strike")]
                if cands:
                    spot = float(min(cands, key=lambda c: abs(c["delta"] - 0.5))["strike"])
                    spot_proxy = "proxy_strike_delta50"
                    spot_source = _surface_text('PROXY strike call delta~0.5 (Polygon e yfinance senza prezzo — dichiarato)', 'PROXY call strike at delta~0.5 (no Polygon or yfinance price — disclosed)')
            if spot is None:
                status["reason"] = _surface_text('spot osservato assente: nessuna superficie costruita da uno strike proxy', 'Observed spot missing: no surface built from a strike proxy')
                coverage["errors"].append(row["expiry"])
                continue
            m, why = _slice_detail(chain, spot, _t_years(row["expiry"], now_ny))
            if m:
                partial = status["chain_complete"] is False
                slices.append({"expiry": row["expiry"], "days": row["days"],
                               "t_years": round(_t_years(row["expiry"], now_ny), 6),
                               "chain_complete": status["chain_complete"], **m})
                status.update(status="partial" if partial else "loaded",
                              reason=(ch.get("continuation_error") or _surface_text('download chain incompleto; sono rappresentati solo i contratti ricevuti', 'Incomplete chain download; only received contracts are represented')) if partial else None,
                              n_contracts=len(chain))
                coverage["loaded"].append(row["expiry"])
            else:
                status.update(status="excluded", reason=why, n_contracts=len(chain))
                coverage["excluded"].append(row["expiry"])

        grid_qualified = bool(spot_qualified and not spot_proxy)
        for s in slices:
            # M-5 (review v2): iv_grid e' costruita su K/S dello spot usato: con spot
            # non qualificato la griglia (e iv_grid[1.0] = ATM) non e' qualificata.
            s["iv_grid_qualified"] = grid_qualified
        if spot_proxy:
            # R02 punto 4: su uno spot che e' uno strike, ATM/RR/BF non sono misure di
            # mercato: n.d. nei campi buoni, valore grezzo solo in *_unqualified.
            # M-5: anche la griglia (K/S del proxy) esce null; grezza in iv_grid_unqualified.
            for s in slices:
                for key in ("atm_iv", "rr25", "bf25", "iv_25d_call", "iv_25d_put"):
                    s[key + "_unqualified"] = s[key]
                    s[key] = None
                s["iv_grid_unqualified"] = s["iv_grid"]
                s["iv_grid"] = [None] * len(s["iv_grid"])
                s["metrics_qualified"] = False

        coverage["complete"] = bool(coverage["rows"]) and all(r["status"] == "loaded" and r.get("chain_complete") is True for r in coverage["rows"])
        coverage["download_complete"] = (bool(_snapshot.get("download_complete")) if _snapshot is not None else
                                         bool(coverage["rows"]) and all(r.get("chain_complete") is True for r in coverage["rows"]))
        coverage["partial_expiries"] = [r["expiry"] for r in coverage["rows"] if r["status"] == "partial"]
        coverage["stopped_after_rate_limit"] = stop_reason is not None

        if not slices:
            return {"error": _surface_text('nessuno slice con dati IV sufficienti', 'No slice with sufficient IV data'), "_source": src,
                    "ticker": ticker, "coverage": coverage, "slices": [],
                    "spot_est": spot, "spot_source": spot_source, "spot_timestamp": spot_timestamp,
                    "spot_qualified": bool(spot_qualified and not spot_proxy), "spot_fallback": bool(spot_fallback),
                    "spot_alignment": spot_alignment, "spot_reason": spot_reason,
                    "term_structure": [], "moneyness_grid": MONEYNESS_GRID, "n_expiries": 0}

        # interpretazione skew dalla prima slice con RR valido
        skew_note = None
        for s in slices:
            if s.get("rr25") is not None:
                skew_note = (_surface_text('RR25 negativo: put più care delle call — domanda di protezione al ribasso (skew classico equity)', 'Negative RR25: puts more expensive than calls — demand for downside protection (typical equity skew)') if s["rr25"] < 0 else
                             _surface_text('RR25 positivo: call più care delle put — domanda di upside (insolito per equity: caccia al rialzo o squeeze atteso)', 'Positive RR25: calls more expensive than puts — upside demand (unusual for equities: upside chasing or an expected squeeze)'))
                break

        # Realized vol a un mese + percentile 1y + prossimi earnings (per lettura accurata)
        # B2 (09/10): 21 sedute, la STESSA finestra del cono (prima 22: due «RV 30g»).
        rv30 = None
        rv_pct_1y = None
        rv_asof = None
        next_earnings = None
        try:
            if not include_context:
                raise RuntimeError("contesto aggiuntivo non richiesto")
            import yfinance as yf
            tk_obj = yf.Ticker(ticker)
            h = tk_obj.history(period="1y")["Close"].pct_change().dropna()
            if len(h) >= 30:
                rv_series = (h.rolling(RV_WINDOW_SESSIONS).std().dropna() * (252 ** 0.5))
                if len(rv_series):
                    rv30 = float(rv_series.iloc[-1])
                    rv_pct_1y = round(100.0 * float((rv_series <= rv30).mean()), 0)
                    try:
                        rv_asof = str(h.index[-1].date())
                    except Exception:
                        rv_asof = None
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

        # A2/M8/B1 (09/10): IV a SCADENZA COSTANTE in varianza totale (stessa
        # funzione di iv_history), mai estrapolata. 30g = orizzonte di IV-RV e del
        # movimento atteso; 60g = back della pendenza (il nome ora dice il vero).
        from bellomberg.market_data.iv_history import constant_maturity_iv
        term_pts = [(s["days"], s["atm_iv"]) for s in slices]
        cm30 = constant_maturity_iv(term_pts, TENOR_DAYS)
        cm60 = constant_maturity_iv(term_pts, BACK_TENOR_DAYS)
        iv30 = round(cm30["iv"], 4) if cm30["iv"] is not None else None
        iv60 = round(cm60["iv"], 4) if cm60["iv"] is not None else None

        # Movimento atteso ±1σ a 30 giorni: dall'IV 30g costante; se non c'e',
        # dalla scadenza piu' vicina a 30g col tempo alla chiusura 16:00 NY (B3),
        # e la base lo DICHIARA.
        expected_move = None
        exp_move_days = None
        if iv30 is not None:
            # audit/11 §4: giorni di CALENDARIO e IV annualizzata ACT/365.
            expected_move = round(iv30 * (TENOR_DAYS / 365.0) ** 0.5 * 100, 1)
            exp_move_days = TENOR_DAYS
            exp_move_basis = _surface_text(f'IV a scadenza costante {TENOR_DAYS}g × √({TENOR_DAYS}/365)', f'{TENOR_DAYS}-day constant-maturity IV × √({TENOR_DAYS}/365)')
        else:
            mo = min(slices, key=lambda s: abs(s["days"] - TENOR_DAYS))
            if mo.get("atm_iv") and mo["t_years"] > 0:
                expected_move = round(mo["atm_iv"] * mo["t_years"] ** 0.5 * 100, 1)
                exp_move_days = mo["days"]
            exp_move_basis = _surface_text(f"ATM della scadenza {mo['expiry']} × √T alla chiusura 16:00 New York (IV {TENOR_DAYS}g n.d.: {cm30['reason']})", f"ATM of the {mo['expiry']} expiry × √T to the 16:00 New York close ({TENOR_DAYS}-day IV n/a: {cm30['reason']})")

        # Front per l'INTERPRETAZIONE: prima scadenza >= 2 giorni — gli 0DTE
        # hanno smile/OI distorti dalla microstruttura e falsano la lettura
        front = next((s for s in slices if s["days"] >= 2), slices[0])
        term_slope = (round(iv60 - front["atm_iv"], 4)
                      if iv60 is not None and front["atm_iv"] is not None and front["days"] < BACK_TENOR_DAYS else None)
        term_slope_reason = None if term_slope is not None else (
            cm60["reason"] or _surface_text(f'front già a {front["days"]}g, oltre {BACK_TENOR_DAYS}g', f'Front already at {front["days"]}d, beyond {BACK_TENOR_DAYS}d'))
        ivrv = round(iv30 - rv30, 4) if (rv30 and iv30 is not None) else None

        if spot_proxy:
            # R02 punto 4: nessuna lettura costruita su uno strike usato come spot
            interpretation = _surface_text('Spot non osservato (strike proxy): ATM, skew 25Δ, IV 30g e movimento atteso n.d. — nessuna lettura di volatilità costruita su uno strike.', 'Spot not observed (strike proxy): ATM, 25Δ skew, 30-day IV and expected move n/a — no volatility reading built on a strike.')
            term_slope_reason = term_slope_reason or _surface_text('spot proxy', 'Proxy spot')
        else:
            interpretation = _interpret(ticker, [front] + [s for s in slices if s is not front],
                                        term_slope, rv30, ivrv,
                                        expected_move=expected_move,
                                        exp_move_days=exp_move_days,
                                        rv_pct_1y=rv_pct_1y,
                                        next_earnings=next_earnings,
                                        back_days=BACK_TENOR_DAYS if term_slope is not None else None,
                                        iv30=iv30)

        timeframes = sorted({tf for s in slices for tf in s.get("quote_timeframes") or []})
        q_min = [s["quote_time_min"] for s in slices if s.get("quote_time_min")]
        q_max = [s["quote_time_max"] for s in slices if s.get("quote_time_max")]
        rv_value = round(rv30, 4) if rv30 else None
        now_utc = datetime.now(timezone.utc).isoformat()
        return {
            "ticker": ticker.upper(),
            "spot_est": spot,
            "spot_source": spot_source,
            "spot_timestamp": spot_timestamp,
            "spot_timestamp_kind": spot_timestamp_kind,
            "spot_timeframe": spot_timeframe,
            # R02-b (09/10, Opus 5.5): etichetta macchina accanto al testo PROXY
            # (che la UI legge). 10/10 (ordine PM, Opus 5.5): qualificato SOLO lo
            # spot del sottostante Polygon con orario, della stessa istantanea delle
            # chain; yfinance = ripiego (spot_fallback), proxy = mai qualificato.
            "spot_proxy": spot_proxy,
            "spot_qualified": bool(spot_qualified and not spot_proxy),
            "spot_fallback": bool(spot_fallback),
            "spot_alignment": spot_alignment,
            "spot_reason": spot_reason,
            "spot_basis": _surface_text('1) prezzo del sottostante Polygon nella stessa istantanea delle chain (last_updated; sul piano Starter assente); 2) chiusura della barra yfinance 1m allineata all\'ora delle quote (qualificata entro la tolleranza di spot_alignment); 3) yfinance lastPrice NON qualificato (spot_fallback); 4) proxy-strike R02 con metriche n.d. Valuta non attestata dall\'endpoint', '1) Polygon underlying price in the same snapshot as the chains (last_updated; absent on the Starter plan); 2) close of the yfinance 1m bar aligned to the quote time (qualified within the spot_alignment tolerance); 3) yfinance lastPrice NOT qualified (spot_fallback); 4) R02 strike proxy with metrics n/a. Currency not attested by the endpoint'),
            "metrics_qualified": not spot_proxy,
            "iv_grid_qualified": grid_qualified,
            "smoothing": _surface_text('mediana mobile 3 punti su IV raw, separata per ala put/call (v1.6, 09/10) — fit SVI no-arbitrage resta v2', '3-point moving median on raw IV, separately per put/call wing (v1.6, 09/10) — arbitrage-free SVI fitting remains v2'),
            "moneyness_grid": MONEYNESS_GRID,
            "moneyness_basis": _surface_text('K/S spot: tasso e dividendo non disponibili, forward non calcolabile (sulle scadenze lunghe la giunzione OTM a K=S lascia ITM le call fra S e F)', 'K/S spot: rate and dividend unavailable, forward cannot be computed (at long expiries the OTM junction at K=S leaves calls between S and F in the money)'),
            "atm_method": _surface_text('IV della curva composita OTM (put K≤S, call K>S) interpolata linearmente a K/S = 1: la stessa curva di iv_grid', 'IV of the composite OTM curve (puts K≤S, calls K>S) linearly interpolated at K/S = 1: the same curve as iv_grid'),
            "rr25_method": _surface_text('smile lineare in strike per tipo (call/put); K(Δ=±0,25) risolto per bisezione col delta Black-Scholes-Merton ricalcolato sull\'IV interpolata, T alla chiusura 16:00 New York, r e q dichiarati (rr25_rate_r, rr25_dividend_q); n.d. se il delta non è raggiunto dentro gli strike osservati', 'Linear-in-strike smile per type (call/put); K(Δ=±0.25) solved by bisection with the Black-Scholes-Merton delta recomputed on the interpolated IV, T to the 16:00 New York close, declared r and q (rr25_rate_r, rr25_dividend_q); n/a if the delta is not reached within the observed strikes'),
            "rr25_rate_r": RR25_RATE_R,
            "rr25_dividend_q": RR25_DIVIDEND_Q,
            "rr25_rate_note": _surface_text('r e q non forniti dal provider: 0 dichiarato', 'r and q not supplied by the provider: 0 declared'),
            "quality_filters": {"max_rel_spread": MAX_REL_SPREAD, "stale_quote_seconds": STALE_QUOTE_SECONDS,
                                "stale_reference_percentile": STALE_REFERENCE_PERCENTILE,
                                "tick_reference": TICK_REFERENCE, "min_spread_ticks": MIN_SPREAD_TICKS,
                                "iv_range": list(IV_RANGE),
                                "rules": _surface_text(f'fuori dallo smile: contratti rettificati (deliverable aggiuntivi o moltiplicatore ≠ 100), OI=0 e volume=0, bid ≤ 0, quota incrociata, (ask−bid)/mid oltre la soglia salvo spread ≤ {MIN_SPREAD_TICKS} tick da {TICK_REFERENCE} $ (tick non-penny, il vero tick per contratto non è nel payload), quota più vecchia della soglia rispetto al {STALE_REFERENCE_PERCENTILE}° percentile dei timestamp dello slice, IV fuori intervallo; bid/ask assenti = non verificabile ma tenuto e contato', f'Excluded from the smile: adjusted contracts (additional deliverables or multiplier ≠ 100), OI=0 and volume=0, bid ≤ 0, crossed quote, (ask−bid)/mid above the threshold unless the spread is ≤ {MIN_SPREAD_TICKS} ticks of ${TICK_REFERENCE} (non-penny tick; the true per-contract tick is not in the payload), quote older than the threshold relative to the {STALE_REFERENCE_PERCENTILE}th percentile of slice timestamps, IV out of range; missing bid/ask = unverifiable but kept and counted')},
            "iv_model_note": _surface_text('IV e greche sono quelle del provider (Polygon/Massive): modello, esercizio, dividendi, tasso e prezzo usato non sono documentati', 'IV and Greeks are the provider values (Polygon/Massive): model, exercise style, dividends, rate and price used are not documented'),
            "slices": slices,
            "term_structure": [{"expiry": s["expiry"], "days": s["days"], "t_years": s["t_years"],
                                "atm_iv": s["atm_iv"], "rr25": s["rr25"],
                                "bf25": s["bf25"], "call_oi": s.get("call_oi"),
                                "put_oi": s.get("put_oi"),
                                "pc_oi_ratio": s.get("pc_oi_ratio"),
                                "chain_complete": s.get("chain_complete")} for s in slices],
            "skew_note": skew_note,
            "iv_30d": iv30,
            "iv_30d_bracket_days": [cm30["days_low"], cm30["days_high"]] if iv30 is not None else None,
            "iv_30d_reason": cm30["reason"],
            "iv_60d": iv60,
            "iv_constant_maturity_method": _surface_text('varianza totale σ²T lineare in T fra le due scadenze a cavallo (giorni di calendario), mai estrapolata', 'Total variance σ²T linear in T between the two bracketing expiries (calendar days), never extrapolated'),
            "term_slope_front_to_60d": term_slope,
            "term_slope_front_days": front["days"] if term_slope is not None else None,
            "term_slope_reason": term_slope_reason,
            "realized_vol_21d": rv_value,
            "realized_vol_30d": rv_value,
            "realized_vol_window_sessions": RV_WINDOW_SESSIONS,
            "realized_vol_asof": rv_asof,
            "rv_percentile_1y": rv_pct_1y,
            "iv_rv_spread_30d": ivrv,
            "iv_rv_spread_front": ivrv,
            "expected_move_pct": expected_move,
            "expected_move_days": exp_move_days,
            "expected_move_basis": exp_move_basis,
            "next_earnings": next_earnings,
            "interpretation": interpretation,
            "n_expiries": len(slices),
            "coverage": coverage,
            "partial": not coverage["complete"],
            "quote_time_min": min(q_min) if q_min else None,
            "quote_time_max": max(q_max) if q_max else None,
            "quote_timeframes": timeframes,
            "data_delay": ("DELAYED" if timeframes == ["DELAYED"] else "REAL-TIME" if timeframes == ["REAL-TIME"]
                           else "MIXED" if timeframes else None),
            "data_delay_note": (_surface_text('quote ritardate dal piano del provider (~15 min); timeframe dichiarato dal provider per contratto', 'Quotes delayed by the provider plan (~15 min); timeframe declared by the provider per contract') if "DELAYED" in timeframes
                                else _surface_text('timeframe delle quote non dichiarato dal provider: ritardo non verificabile', 'Quote timeframe not declared by the provider: delay cannot be verified') if not timeframes else None),
            "market_session": _market_session(now_ny),
            "snapshot_kind": "download_snapshot" if _snapshot is not None else "new_fetch",
            "snapshot_at": now_utc,
            "deprecated_fields": {
                "realized_vol_30d": _surface_text('alias di realized_vol_21d (finestra 21 sedute dal 09/10)', 'Alias of realized_vol_21d (21-session window since 09/10)'),
                "iv_rv_spread_front": _surface_text('alias di iv_rv_spread_30d: dal 09/10 vale IV 30g costante − RV 21 sedute, non più ATM front − RV', 'Alias of iv_rv_spread_30d: since 09/10 it is 30-day constant-maturity IV − 21-session RV, no longer front ATM − RV')},
            "context_requested": include_context,
            "_source": src,
            "_timestamp": now_utc,
        }
    except Exception as e:
        # 05/10 (Opus 5.5): i messaggi SCRITTI dalla casa (message()) passano;
        # il testo di un'eccezione esterna no (requests porta l'URL con apiKey).
        testo = error_text(e)
        return {"error": testo if hasattr(testo, "_italian") else type(e).__name__,
                "_source": src}


_TOOL_TERM_KEYS = ("expiry", "days", "atm_iv", "rr25", "bf25", "pc_oi_ratio", "chain_complete")
_TOOL_KEYS = ("ticker", "spot_est", "spot_source", "spot_timestamp", "spot_qualified", "spot_fallback",
              "spot_proxy", "metrics_qualified", "iv_grid_qualified", "partial", "data_delay",
              "quote_time_max", "skew_note", "iv_30d", "iv_30d_reason", "iv_60d",
              "term_slope_front_to_60d", "term_slope_reason", "realized_vol_21d", "rv_percentile_1y",
              "iv_rv_spread_30d", "expected_move_pct", "expected_move_days", "next_earnings",
              "interpretation", "n_expiries", "_source", "_timestamp")
_IV_CTX_KEYS = ("iv_30d_current", "iv_percentile", "iv_min", "iv_max", "n_obs", "min_obs", "young",
                "history_from", "last_snap_date", "excluded_days", "current_partial", "iv_front_current",
                "iv_front_days", "error")


def compact_iv_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """IV rank per il contesto agente: numeri e stati, senza i testi di metodo."""
    if not isinstance(ctx, dict):
        return ctx
    return {k: ctx[k] for k in _IV_CTX_KEYS if k in ctx}


def tool_summary(r: Dict[str, Any]) -> Dict[str, Any]:
    """M-7 (review v2, 10/10, Opus 5.5): payload COMPATTO per il tool del comitato
    `get_vol_surface_summary` (chat_tools). Tiene numeri e stati (spot_qualified,
    partial, data_delay, session_date, coverage) e l'interpretazione; lascia fuori
    griglie, slice, testi metodologici lunghi (spot_basis, *_method, quality_filters,
    iv_model_note, ...) e gli alias deprecati (realized_vol_30d, iv_rv_spread_front).
    Il payload completo resta sulla rotta /options/vol_surface."""
    if not isinstance(r, dict) or r.get("error"):
        return r
    out = {k: r[k] for k in _TOOL_KEYS if k in r and not (k.endswith("_reason") and r[k] is None)}
    session = r.get("market_session") or {}
    out["session_date"] = session.get("session_date")
    out["market_open"] = session.get("market_open")
    out["term_structure"] = [{k: t.get(k) for k in _TOOL_TERM_KEYS} for t in r.get("term_structure") or []]
    cov = r.get("coverage") or {}
    out["coverage"] = {"loaded": list(cov.get("loaded") or []),
                       "partial_expiries": list(cov.get("partial_expiries") or []),
                       "excluded": list(cov.get("excluded") or []),
                       "errors": list(cov.get("errors") or []),
                       "complete": cov.get("complete"),
                       "stopped_after_rate_limit": bool(cov.get("stopped_after_rate_limit"))}
    return out


def context_panels(ticker: str) -> Dict[str, Any]:
    """Pannelli di contesto comuni alle due rotte (vol_surface e download/{id}/context):
    IV rank dallo storico e GEX. Il GEX (positioning_tools.compute_gex, altro
    perimetro) fa il PROPRIO fetch Polygon: non e' l'istantanea della superficie e
    `context_sources` lo dichiara con l'ora del fetch (A4, 09/10)."""
    out: Dict[str, Any] = {}
    try:
        from bellomberg.market_data.iv_history import get_iv_context
        out["iv_history_context"] = get_iv_context(ticker)
    except Exception as e:
        out["iv_history_context"] = {"error": str(e)}
    # GEX dealer per il pannello F12 (richiesta frontend 25/07 sera, ponte sanato
    # in (44)): riuso PURO di compute_gex (#177), solo rinomina chiavi verso il
    # contratto UI. Campo additivo, errore dichiarato: F12 non si rompe mai.
    gex_fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        from bellomberg.portfolio.positioning_tools import compute_gex
        g = compute_gex(ticker)
        if g.get("error"):
            out["gex"] = {"error": g["error"]}
        else:
            out["gex"] = {
                "by_strike": [{"strike": row.get("strike"),
                               "gex_1pct_usd": row.get("net_gex_usd"),
                               "call_oi": row.get("call_oi"),
                               "put_oi": row.get("put_oi")}
                              for row in g.get("top_strikes", [])],
                "flip_strike": g.get("gamma_flip_strike"),
                "net_gex_1pct_usd": g.get("net_gex_usd_per_1pct"),
                "spot_est": g.get("spot_est"),
                # fix 09/10 (residuo R02, campi ADDITIVI, lotto vol/quant): lo spot dice se
                # e' una quotazione o uno strike proxy; la posizione rispetto allo zero-gamma
                # e' «non valutata» su uno spot proxy; copertura parziale dichiarata
                "spot_source": g.get("spot_source"),
                "spot_qualified": g.get("spot_qualified"),
                "spot_note": g.get("spot_note"),
                "spot_vs_flip": g.get("spot_vs_flip"),
                "flip_method": g.get("gamma_flip_method"),
                "flip_note": g.get("gamma_flip_note"),
                "partial": g.get("partial"),
                "partial_note": g.get("partial_note"),
                "expiries_failed": g.get("expiries_failed"),
                "coverage": g.get("coverage"),
                "regime": g.get("regime"),
                "basis": ("SqueezeMetrics conv. (dealer long call / short put Γ) · "
                          f"{len(g.get('expiries_used', []))} expiry 2-45g lette"
                          + (f" ({len(g.get('expiries_failed') or [])} in errore)" if g.get("expiries_failed") else "")
                          + " · "
                          "12 strike top |GEX| · OI da snapshot delayed, "
                          "deep-OTM senza greeks esclusi"),
            }
    except Exception as e:
        out["gex"] = {"error": str(e)}
    out["context_sources"] = {
        "iv_history_context": _surface_text('DB iv_history (foto giornaliere del collector)', 'iv_history DB (daily collector snapshots)'),
        "gex": _surface_text('nuovo fetch Polygon di compute_gex: NON è l\'istantanea della superficie', 'New Polygon fetch by compute_gex: NOT the surface snapshot'),
        "gex_fetched_at": gex_fetched_at,
        "realized_vol": _surface_text('chiusure yfinance 1y lette al momento della richiesta', 'yfinance 1y closes read at request time'),
    }
    return out


def _interpret(ticker: str, slices: List[Dict[str, Any]],
               term_slope: Optional[float], rv30: Optional[float],
               ivrv: Optional[float],
               expected_move: Optional[float] = None,
               exp_move_days: Optional[int] = None,
               rv_pct_1y: Optional[float] = None,
               next_earnings: Optional[str] = None,
               back_days: Optional[int] = None,
               iv30: Optional[float] = None) -> str:
    """Lettura della superficie in italiano professionale (rule-based, no AI)."""
    front = slices[0]
    parts: List[str] = []

    # 0. Movimento atteso (la sintesi che un PM vuole per prima)
    if expected_move is not None:
        parts.append(
            _surface_text(f"Il mercato delle opzioni prezza per {ticker.upper()} un movimento atteso di ±{expected_move:.1f}% entro la scadenza a {exp_move_days} giorni (1 deviazione standard, da ATM IV). Sopra/sotto questo range il mercato è 'sorpreso'.", f"The options market prices for {ticker.upper()} an expected move of ±{expected_move:.1f}% by the expiry in {exp_move_days} days (1 standard deviation, from ATM IV). Beyond this range, the market is 'surprised'."))

    # 1. Term structure (+ contestualizzazione earnings se imminenti)
    atm_f = front["atm_iv"] * 100
    earn_note = ""
    if next_earnings:
        earn_note = _surface_text(f' Prossimi earnings attesi il {next_earnings}: se cadono prima della scadenza lunga, parte della vol front è event premium fisiologico.', f' Next earnings expected on {next_earnings}: if before the longer expiry, some front volatility is normal event premium.')
    back_label = _surface_text(f'a {back_days} giorni', f'at {back_days} days') if back_days else _surface_text('sulle scadenze lunghe', 'at longer expiries')
    if term_slope is not None:
        atm_b = (front["atm_iv"] + term_slope) * 100
        if term_slope > 0.01:
            parts.append(
                join_messages("", [_surface_text(f"Term structure ascendente (contango): ATM {atm_f:.1f}% sul front contro {atm_b:.1f}% {{back_label}}. Il mercato non prezza stress immediato su {ticker.upper()}; l'incertezza è caricata sulle scadenze lunghe — regime ordinato.", f'Upward term structure (contango): ATM {atm_f:.1f}% at the front versus {atm_b:.1f}% {{back_label}}. The market does not price immediate stress for {ticker.upper()}; uncertainty is concentrated at longer expiries — an orderly regime.', back_label=back_label), earn_note]))
        elif term_slope < -0.01:
            parts.append(
                join_messages("", [_surface_text(f'Term structure INVERTITA: il front ({atm_f:.1f}%) tratta sopra il livello {{back_label}} ({atm_b:.1f}%). Il mercato prezza un evento ravvicinato (earnings, macro, catalyst): attenzione a vendere opzioni corte qui.', f'INVERTED term structure: the front ({atm_f:.1f}%) trades above the level {{back_label}} ({atm_b:.1f}%). The market prices a near-term event (earnings, macro, catalyst): take care when selling short-dated options here.', back_label=back_label), earn_note]))
        else:
            parts.append(join_messages("", [_surface_text(f'Term structure piatta intorno a {atm_f:.1f}% ATM: nessun evento specifico prezzato, vol uniforme sulle scadenze.', f'Flat term structure around {atm_f:.1f}% ATM: no specific event priced, volatility is uniform across expiries.'), earn_note]))
    else:
        parts.append(join_messages("", [_surface_text(f'ATM front a {atm_f:.1f}% (curva corta disponibile).', f'Front ATM at {atm_f:.1f}% (short curve available).'), earn_note]))

    # 1bis. Regime di volatilità realizzata (percentile 1 anno)
    if rv_pct_1y is not None:
        regime_rv = (_surface_text('regime di movimento COMPRESSO', 'COMPRESSED movement regime') if rv_pct_1y <= 25 else
                     _surface_text('regime di movimento ELEVATO', 'ELEVATED movement regime') if rv_pct_1y >= 75 else
                     _surface_text('regime di movimento nella media', 'average movement regime'))
        parts.append(_surface_text(f"La realized vol a 21 sedute è al {rv_pct_1y:.0f}° percentile dell'ultimo anno: {{regime_rv}}. Gli estremi tendono a rientrare (mean reversion della volatilità).", f'21-session realized volatility is at the {rv_pct_1y:.0f}th percentile of the past year: {{regime_rv}}. Extremes tend to revert (volatility mean reversion).', regime_rv=regime_rv))

    # 2. Skew (RR25)
    rr = front.get("rr25")
    if rr is not None:
        rr_pt = rr * 100
        if rr_pt < -3:
            parts.append(_surface_text(f'Skew put MARCATO (RR25 {rr_pt:.1f}pt): la protezione al ribasso è cara — domanda di hedge consistente; chi compra put qui paga premio pieno, chi le vende viene pagato bene per il rischio.', f'PRONOUNCED put skew (RR25 {rr_pt:.1f}pt): downside protection is expensive — substantial hedging demand; put buyers pay a full premium and sellers are well compensated for the risk.'))
        elif rr_pt < -0.5:
            parts.append(_surface_text(f'Skew put nella norma equity (RR25 {rr_pt:.1f}pt): fisiologica domanda di protezione, nessun allarme.', f'Typical equity put skew (RR25 {rr_pt:.1f}pt): normal demand for protection, no alarm.'))
        elif rr_pt > 0.5:
            parts.append(_surface_text(f"Skew INVERTITO a favore delle call (RR25 +{rr_pt:.1f}pt): il mercato paga per l'upside — tipico di squeeze attesi, M&A o retail chase. Covered call ben remunerate.", f'INVERTED skew favoring calls (RR25 +{rr_pt:.1f}pt): the market pays for upside — typical of expected squeezes, M&A or retail chasing. Covered calls are well compensated.'))
        else:
            parts.append(_surface_text(f'Skew neutro (RR25 {rr_pt:.1f}pt): smile simmetrico.', f'Neutral skew (RR25 {rr_pt:.1f}pt): symmetric smile.'))

    # 3. Curvatura (BF25)
    bf = front.get("bf25")
    if bf is not None and bf * 100 > 1.5:
        parts.append(_surface_text(f'Butterfly 25Δ elevato ({bf * 100:.1f}pt): le code sono care in entrambe le direzioni — il mercato paga i tail scenario, attesa di movimento ampio.', f'Elevated 25Δ butterfly ({bf * 100:.1f}pt): tails are expensive in both directions — the market pays for tail scenarios and expects a large move.'))

    # 4. IV vs RV (vol risk premium). M8 (09/10): stesso orizzonte — IV a scadenza
    # costante 30g contro RV 21 sedute; prima era l'ATM del front (anche 2-9 giorni,
    # con eventi dentro) contro la RV 22 sedute.
    if ivrv is not None and rv30 is not None and iv30 is not None:
        sp = ivrv * 100
        iv_pt = iv30 * 100
        if sp > 3:
            parts.append(_surface_text(f"IV 30g {iv_pt:.1f}% contro realizzata 21 sedute {rv30 * 100:.1f}%: premio di {sp:.1f}pt — le opzioni sono CARE rispetto al movimento effettivo. Contesto favorevole a strategie di vendita di premio coperta (covered call), sfavorevole all'acquisto di protezione.", f'30-day IV {iv_pt:.1f}% versus 21-session realized volatility of {rv30 * 100:.1f}%: premium of {sp:.1f}pt — options are EXPENSIVE relative to observed movement. This favors covered premium selling (covered calls) and disfavors buying protection.'))
        elif sp < -3:
            parts.append(_surface_text(f"IV 30g {iv_pt:.1f}% SOTTO la realizzata 21 sedute ({rv30 * 100:.1f}%): opzioni a sconto rispetto al movimento reale — l'hedge in put costa poco, vendere premio qui è mal pagato.", f'30-day IV {iv_pt:.1f}% BELOW 21-session realized volatility ({rv30 * 100:.1f}%): options are discounted relative to observed movement — put hedging is inexpensive and premium selling is poorly compensated.'))
        else:
            parts.append(_surface_text(f'IV 30g {iv_pt:.1f}% allineata alla realizzata 21 sedute ({rv30 * 100:.1f}%): vol risk premium nella norma.', f'30-day IV {iv_pt:.1f}% aligned with 21-session realized volatility ({rv30 * 100:.1f}%): normal volatility risk premium.'))

    # 5. Posizionamento OI
    pc = front.get("pc_oi_ratio")
    if pc is not None:
        if pc > 1.3:
            parts.append(_surface_text(f'Open interest sbilanciato sulle put (P/C {pc:.2f}): posizionamento difensivo già costruito sul front.', f'Open interest skewed toward puts (P/C {pc:.2f}): defensive positioning already built at the front.'))
        elif pc < 0.6:
            parts.append(_surface_text(f'Open interest sbilanciato sulle call (P/C {pc:.2f}): posizionamento speculativo rialzista sul front.', f'Open interest skewed toward calls (P/C {pc:.2f}): speculative bullish positioning at the front.'))

    return join_messages("\n\n", parts)


if __name__ == "__main__":
    import json
    r = build_vol_surface("SPY", max_expiries=6)
    out = {k: v for k, v in r.items() if k != "slices"}
    print(json.dumps(out, indent=1)[:2500])
    if r.get("slices"):
        s0 = r["slices"][0]
        print("\nprima slice:", s0["expiry"], "atm", s0["atm_iv"],
              "rr25", s0["rr25"], "bf25", s0["bf25"])
