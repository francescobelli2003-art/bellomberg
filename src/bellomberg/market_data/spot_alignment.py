"""
spot_alignment.py — spot del sottostante ALLINEATO all'ora delle quote delle chain
(MA-1 review vol surface v2, 10/10, Opus 5.5).

Il problema misurato: sul piano Polygon/Massive del PM (Starter, chain ritardate
15 minuti) `underlying_asset` della chain contiene SOLO il ticker, senza prezzo
(docs/private/audits/42_PRODUCT_COMPLETION.md:87, prova reale 09/09). Lo «spot
primario Polygon» quindi non scattava mai e ogni superficie usciva col ripiego
yfinance `lastPrice` (quasi tempo reale) contro quote di 15 minuti prima: K/S,
ATM e 25Δ calcolati su due istanti diversi.

Ordine DICHIARATO di `resolve_spot` (funzione pubblica, riusabile da
`positioning_tools.compute_gex` e da chiunque costruisca metriche su una chain):
  1. prezzo del sottostante nella chain Polygon (`underlying_asset.price`), se c'e':
     qualificato solo se porta il suo `last_updated`;
  2. chiusura della barra yfinance a 1 minuto ALLINEATA all'ora delle quote
     (`anchor`): la barra che contiene l'ancora, altrimenti la piu' vicina entro
     `tolerance_seconds` (default 120 s = ±2 barre); qualificato = allineamento
     entro tolleranza; `spot_timestamp` = inizio della barra (UTC);
     con l'ancora fra le 16:00 e le 16:15 ET (opzioni su ETF d'indice ancora aperte,
     azionario chiuso) e nessuna barra entro tolleranza vale l'ULTIMA barra della seduta
     dello stesso giorno, qualificata e dichiarata (`spot_alignment.session_close`);
  3. fuori tolleranza o barre assenti: yfinance `lastPrice`, NON qualificato e
     marcato `spot_fallback` (istante diverso dalle quote), col motivo;
  4. niente di tutto questo: spot None con motivo — il chiamante decide (la
     superficie usa il proxy-strike R02, dichiarato e mai qualificato).

Nessun fallback silenzioso: ogni esito porta `spot_source`, `spot_qualified`,
`spot_fallback` e `spot_alignment` (ancora, barra, scarto, tolleranza, regola).
"""
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Callable, Dict, Iterable, Optional

from bellomberg.core.presentation import message as _t

ALIGN_TOLERANCE_SECONDS = 120
# Le opzioni su SPY (e sugli altri ETF d'indice) trattano fino alle 16:15 ET, l'azionario
# chiude alle 16:00: una quota fra 16:00 e 16:15 ET non ha una barra a 1 minuto entro la
# tolleranza. In quella finestra vale l'ULTIMA barra della seduta dello stesso giorno
# (chiusura delle 16:00), dichiarata (regola della v2 vol/quant 10/10, riserva 9, portata
# qui in integrazione perche' superficie e GEX seguano la STESSA regola).
SESSION_CLOSE_ET = (16, 0)
OPTIONS_CLOSE_ET = (16, 15)
SOURCE_POLYGON = "Polygon underlying snapshot"
SOURCE_BAR = "yfinance 1m bar aligned to quote time"
SOURCE_LAST = "yfinance lastPrice"


def align_rule(tolerance_seconds: float = ALIGN_TOLERANCE_SECONDS):
    """La regola di allineamento, dichiarata nel payload."""
    return _t(f"chiusura della barra yfinance a 1 minuto che contiene l'ora di riferimento delle quote "
              f"(ancora); altrimenti la barra più vicina con |inizio barra − minuto dell'ancora| ≤ "
              f"{tolerance_seconds:.0f}s (a parità la precedente); qualificato solo entro tolleranza",
              f"Close of the 1-minute yfinance bar containing the quote reference time (anchor); "
              f"otherwise the nearest bar with |bar start − anchor minute| ≤ {tolerance_seconds:.0f}s "
              f"(ties: the earlier); qualified only within tolerance")


def _et(stamp: datetime):
    """`stamp` nel fuso di New York; None se il fuso non e' disponibile (mai indovinato)."""
    try:
        from zoneinfo import ZoneInfo
        return stamp.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return None


def _in_post_close_window(anchor: datetime) -> bool:
    et = _et(anchor)
    return (et is not None and SESSION_CLOSE_ET <= (et.hour, et.minute)
            and (et.hour, et.minute, et.second) <= OPTIONS_CLOSE_ET + (0,))


def _finite_positive(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def _as_utc(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def quote_anchor(stamps: Iterable[Any], future_tolerance_seconds: float = 900.0) -> Optional[datetime]:
    """Ora di riferimento delle quote = quote_time_max ROBUSTO: il massimo dei
    timestamp che non superano di oltre `future_tolerance_seconds` il 75°
    percentile (nearest-rank, lato basso). Un solo contratto con l'orologio 20'
    avanti non sposta l'ancora (stesso riferimento del filtro stale, M-4)."""
    vals = sorted(s for s in (_as_utc(x) for x in stamps) if s is not None)
    if not vals:
        return None
    ref = vals[max(0, math.ceil(0.75 * len(vals)) - 1)]
    kept = [s for s in vals if (s - ref).total_seconds() <= future_tolerance_seconds]
    return kept[-1] if kept else ref


def _bars_from_yfinance(ticker: str, start: datetime, end: datetime):
    import yfinance as yf
    return yf.Ticker(ticker).history(start=start, end=end, interval="1m",
                                     prepost=False, auto_adjust=False)


def _last_price_from_yfinance(ticker: str):
    import yfinance as yf
    return yf.Ticker(ticker).fast_info["lastPrice"]


def aligned_bar_spot(ticker: str, anchor, *, tolerance_seconds: float = ALIGN_TOLERANCE_SECONDS,
                     bars_loader: Optional[Callable] = None) -> Dict[str, Any]:
    """Chiusura della barra a 1 minuto allineata ad `anchor`. Ritorna sempre un
    dict: `spot` None + `reason` quando l'allineamento non e' possibile."""
    out: Dict[str, Any] = {"spot": None, "bar_start": None, "offset_seconds": None,
                           "tolerance_seconds": tolerance_seconds, "rule": align_rule(tolerance_seconds),
                           "anchor_quote_time": None, "reason": None, "session_close": False}
    anchor = _as_utc(anchor)
    if anchor is None:
        out["reason"] = _t("ora delle quote assente: barra non allineabile", "Quote time missing: bar cannot be aligned")
        return out
    out["anchor_quote_time"] = anchor.isoformat()
    minute = anchor.replace(second=0, microsecond=0)
    post_close = _in_post_close_window(anchor)
    loader = bars_loader or _bars_from_yfinance
    try:
        # nella finestra 16:00-16:15 ET la finestra di lettura parte 30' prima, cosi' da
        # comprendere l'ultima barra della seduta (15:59 ET) anche con un'ancora alle 16:15
        bars = loader(ticker, minute - timedelta(minutes=30 if post_close else 15), minute + timedelta(minutes=15))
    except Exception as exc:  # il TIPO, mai il testo (puo' portare URL)
        out["reason"] = _t(f"barre 1m non lette ({type(exc).__name__})", f"1m bars not read ({type(exc).__name__})")
        return out
    best = None
    session_last = None
    anchor_et = _et(anchor) if post_close else None
    try:
        index, closes = list(bars.index), list(bars["Close"])
    except Exception as exc:
        out["reason"] = _t(f"barre 1m senza colonna Close ({type(exc).__name__})", f"1m bars without a Close column ({type(exc).__name__})")
        return out
    for stamp, close in zip(index, closes):
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            continue    # senza fuso la barra non e' collocabile: mai indovinato
        start = stamp.astimezone(timezone.utc)
        px = _finite_positive(close)
        if px is None:
            continue
        offset = abs((start - minute).total_seconds())
        key = (offset, start)            # a parita' di scarto la barra precedente
        if best is None or key < best[0]:
            best = (key, start, px)
        if anchor_et is not None and start <= anchor:
            start_et = _et(start)
            if (start_et is not None and start_et.date() == anchor_et.date()
                    and (start_et.hour, start_et.minute) < SESSION_CLOSE_ET
                    and (session_last is None or start > session_last[0])):
                session_last = (start, px)
    if best is None:
        out["reason"] = _t("nessuna barra 1m con fuso e chiusura valida intorno all'ora delle quote", "No 1m bar with time zone and valid close around the quote time")
        return out
    (offset, _), start, px = best
    out.update(bar_start=start.isoformat(), offset_seconds=offset)
    if offset > tolerance_seconds and session_last is not None:
        # quota fra le 16:00 e le 16:15 ET: ultima barra della seduta, dichiarata
        start, px = session_last
        out.update(spot=px, bar_start=start.isoformat(), session_close=True,
                   offset_seconds=abs((start - minute).total_seconds()),
                   reason=None)
        return out
    if offset > tolerance_seconds:
        out["reason"] = _t(f"barra più vicina a {offset:.0f}s dall'ora delle quote, oltre la tolleranza di {tolerance_seconds:.0f}s",
                           f"Nearest bar {offset:.0f}s from the quote time, beyond the {tolerance_seconds:.0f}s tolerance")
        return out
    out["spot"] = px
    return out


def resolve_spot(ticker: str, *, underlying: Optional[Dict[str, Any]] = None, anchor=None,
                 tolerance_seconds: float = ALIGN_TOLERANCE_SECONDS,
                 bars_loader: Optional[Callable] = None,
                 last_price_loader: Optional[Callable] = None) -> Dict[str, Any]:
    """Spot per metriche costruite su una chain (ordine nel docstring del modulo).

    underlying: {"price", "timestamp" (ISO) | "timestamp_ns", "timeframe"} dalla
    stessa pagina Polygon (prezzo e orario della STESSA osservazione), o None.
    anchor: ora di riferimento delle quote (v. `quote_anchor`)."""
    res: Dict[str, Any] = {"spot": None, "spot_source": None, "spot_timestamp": None,
                           "spot_timestamp_kind": None, "spot_timeframe": None,
                           "spot_qualified": False, "spot_fallback": False,
                           "spot_alignment": None, "spot_reason": None}
    u = underlying or {}
    price = _finite_positive(u.get("price"))
    if price is not None:
        iso = u.get("timestamp")
        ns = _finite_positive(u.get("timestamp_ns"))
        if iso is None and ns is not None:
            try:
                iso = datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                iso = None
        res.update(spot=price, spot_source=SOURCE_POLYGON, spot_timestamp=iso,
                   spot_timestamp_kind=_t("last_updated del sottostante Polygon", "Polygon underlying last_updated") if iso else None,
                   spot_timeframe=u.get("timeframe"), spot_qualified=iso is not None)
        if iso is None:
            res["spot_reason"] = _t("prezzo Polygon senza last_updated: non qualificato", "Polygon price without last_updated: not qualified")
        return res
    bar = aligned_bar_spot(ticker, anchor, tolerance_seconds=tolerance_seconds, bars_loader=bars_loader)
    res["spot_alignment"] = {k: bar[k] for k in ("anchor_quote_time", "bar_start", "offset_seconds",
                                                  "tolerance_seconds", "rule", "reason", "session_close")}
    if bar["spot"] is not None:
        kind = (_t("inizio dell'ultima barra 1m yfinance della seduta (16:00 ET): quote fra le 16:00 e le 16:15 ET",
                   "Start of the session's last yfinance 1m bar (16:00 ET): quotes between 16:00 and 16:15 ET")
                if bar.get("session_close") else
                _t("inizio della barra 1m yfinance allineata all'ora delle quote", "Start of the yfinance 1m bar aligned to the quote time"))
        res.update(spot=bar["spot"], spot_source=SOURCE_BAR, spot_timestamp=bar["bar_start"],
                   spot_timestamp_kind=kind, spot_qualified=True, spot_fallback=False)
        return res
    try:
        px = _finite_positive((last_price_loader or _last_price_from_yfinance)(ticker))
    except Exception as exc:
        px = None
        res["spot_reason"] = _t("{reason}; lastPrice yfinance non letto ({kind})", "{reason}; yfinance lastPrice not read ({kind})", reason=bar["reason"], kind=type(exc).__name__)
    if px is not None:
        res.update(spot=px, spot_source=SOURCE_LAST,
                   spot_timestamp=datetime.now(timezone.utc).isoformat(),
                   spot_timestamp_kind=_t("ora di lettura (yfinance non fornisce l'orario della quota)", "Read time (yfinance does not provide the quote time)"),
                   spot_qualified=False, spot_fallback=True,
                   spot_reason=_t("ripiego non qualificato: {reason}", "Unqualified fallback: {reason}", reason=bar["reason"]))
    elif res["spot_reason"] is None:
        res["spot_reason"] = _t("{reason}; lastPrice yfinance assente", "{reason}; yfinance lastPrice missing", reason=bar["reason"])
    return res
