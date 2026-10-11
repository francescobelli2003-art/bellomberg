"""
trade_idea_market_pack.py — "pacchetto dati di mercato" del memo Trade Idea
(Lotto 3, costruttore L1, Opus 5.5, 04/10/2026).

Scarica in modo DETERMINISTICO (codice, non LLM) le serie giornaliere del
candidato, del benchmark, del VIX e dei peer, le converte in EUR e raccoglie i
multipli correnti (candidato + peer) e storici (candidato). Il risultato e' il
dizionario che le statistiche pure (reporting/trade_idea_market_stats.py, L2)
e i grafici (L3) leggono: lo SCHEMA e' fissato in testa a quel modulo.

Decisioni PM 04/10 sera:
- benchmark UNICO S&P 500 in versione TOTAL RETURN (^SP500TR, dividendi reinvestiti:
  omogeneo al candidato in Adj Close); se manca, ^GSPC DICHIARATO come indice di prezzo
  senza dividendi (PROXY, decisione PM 04/10 sera);
- rendimenti in EUR: ogni prezzo in altra valuta e' diviso per il cambio
  EURxxx=X della STESSA data (Close Yahoo, unita' "xxx per EUR"), senza fill;
  la data senza cambio diventa un buco (None) contato e dichiarato;
- finestra 5 anni [today - 5 anni, today): il giorno di oggi e' ESCLUSO (barra
  intraday non chiusa); sotto 3 anni coperti la serie e' dichiarata
  "storia insufficiente" (la statistica a valle non si calcola);
- VIX incluso come LIVELLO in punti (nessuna conversione, nessun "adjusted");
- peer = lista del PM se presente, altrimenti valuation/peer_comps.
  select_peer_comps con la sua nota (scarti inclusi) riportata tal quale.

Modello: valuation/beta_reference_evidence.collect_beta_reference (campo prezzo
esplicito "Adj Close", valuta dal metadata del fornitore e mai dedotta, buchi
None conservati, sha256 delle osservazioni normalizzate).

Regole (PM 14/07, niente fallback silenziosi): ogni serie ha "status" e,
se non ok, "reason"; ogni multiplo assente e' None con il motivo nella lista
"missing"; ogni approssimazione e' scritta in "approximations". Nessuna rete
nei test: fetch, fx_fetch, info_fetch, history_loader, peer_selector e
resolve_symbols sono iniettabili. build_market_pack non solleva mai: un errore
imprevisto diventa {"status": "unavailable", "reason": ...}.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

PACK_VERSION = 1                     # = trade_idea_market_stats.PACK_VERSION
BASE_CURRENCY = "EUR"
BENCHMARK_TICKER = "^SP500TR"        # S&P 500 Total Return (verificato dal vivo 04/10: USD)
BENCHMARK_FALLBACK = "^GSPC"         # S&P 500 di prezzo: solo se il TR manca, dichiarato
VIX_TICKER = "^VIX"
MIN_YEARS = 3                        # = trade_idea_market_stats.MIN_YEARS
START_SLACK_DAYS = 7                 # = trade_idea_market_stats.START_SLACK_DAYS
STALE_BDAYS = 3                      # = market_inputs._STALE_BDAYS
LONG_GAP_BDAYS = 5                   # come beta_reference_evidence: buco lungo dichiarato
MAX_PEERS = 6                        # peer dal selettore
MAX_PEERS_REQUEST = 8                # peer del PM ammessi nella richiesta della run

# valute "minori" quotate da Yahoo: (valuta maggiore, divisore)
_MINOR_UNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0),
                "ILA": ("ILS", 100.0)}

_FX_METHOD = ("prezzo in valuta del listino diviso per il cambio {pair} (Close Yahoo, "
              "{ccy} per EUR) della STESSA data; nessun fill: data senza cambio = buco. "
              "Chiusure di titolo e cambio a orari diversi (etichetta di seduta, non istante "
              "sincronizzato): effetto cambio incluso nel rendimento")
_EV_APPROX = ("EV = capitalizzazione (Close a fine esercizio x azioni diluite medie) + debito a "
              "lungo termine - cassa: SENZA debito a breve, leasing, minoranze (approssimazione)")
_EBITDA_APPROX = "EBITDA = risultato operativo (EBIT) + ammortamenti (approssimazione)"
_FYE_APPROX = ("fine esercizio assunta al 31/12 (le fonti XBRL qui non restituiscono la data "
               "di chiusura): per esercizi non solari il prezzo e' sfasato")
_EPS_NOTE = ("EPS diluito 'come depositato': dopo uno split puo' non essere riesposto, "
             "mentre il Close Yahoo e' rettificato per gli split")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _years_back(end: date, years: int) -> date:
    try:
        return end.replace(year=end.year - years)
    except ValueError:              # 29 febbraio
        return end.replace(year=end.year - years, day=28)


def _bdays(d0: date, d1: date) -> int:
    from bellomberg.market_data.market_inputs import _bdays_between
    return _bdays_between(d0, d1)


def _num(v: Any) -> Optional[float]:
    """float finito o None (NaN, bool, stringhe -> None)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _ccy_norm(unit: Any) -> Optional[str]:
    """'iso4217:EUR/shares' -> 'EUR'; vuoto -> None."""
    s = str(unit or "").replace("iso4217:", "").split("/")[0].strip()
    return s.upper() or None


# ---------------------------------------------------------------- fornitori (rete)

def _yf_fetch(symbol: str, start: str, end: str) -> Dict[str, Any]:
    """Storia giornaliera Yahoo con campi ESPLICITI (auto_adjust=False)."""
    import yfinance as yf
    inst = yf.Ticker(symbol)
    frame = inst.history(start=start, end=end, auto_adjust=False, actions=False)
    meta = inst.history_metadata or {}
    if frame is None or frame.empty or "Close" not in frame:
        raise ValueError("storia vuota dal fornitore")
    adj = frame["Adj Close"] if "Adj Close" in frame else None
    rows = []
    for stamp, close in frame["Close"].items():
        a = adj.loc[stamp] if adj is not None else None
        rows.append({"date": stamp.date().isoformat(),
                     "close": None if close != close else float(close),
                     "adj_close": None if (a is None or a != a) else float(a)})
    return {"symbol": meta.get("symbol"), "currency": meta.get("currency"),
            "timezone": meta.get("exchangeTimezoneName"), "observations": rows}


def _yf_info(symbol: str) -> Dict[str, Any]:
    import yfinance as yf
    return yf.Ticker(symbol).info or {}


def _cutoff_iso(today: Any) -> Optional[str]:
    """Data del cutoff della run in ISO (date, datetime o "YYYY-MM-DD..."); None se assente."""
    if isinstance(today, (date, datetime)):
        return today.isoformat()[:10]
    return str(today)[:10] if today else None


def _default_history(ticker: str, name: Optional[str], fino_al: Optional[str] = None) -> Dict[str, Any]:
    """SEC XBRL, poi ESEF (catena di agents/chat_tools): il ripiego e' DICHIARATO. `fino_al` = cutoff della run
    (GENERALITA' UE, Opus 5.5): nessun deposito successivo, ne' SEC ne' ESEF."""
    from bellomberg.market_data.sec_xbrl import get_financial_history
    cutoff = {"fino_al": str(fino_al)[:10]} if fino_al else {}
    h = get_financial_history(ticker, years=10, **cutoff)
    if not h.get("error"):
        return h
    from bellomberg.market_data.esef import get_esef_history
    e = get_esef_history(ticker, years=10, company_name=name, **cutoff)
    if e.get("error"):
        return {"error": "SEC: %s | ESEF: %s" % (h["error"], e["error"])}
    e = dict(e)
    e["_chain_note"] = "SEC non disponibile (%s): storico da ESEF" % h["error"]
    return e


def _default_selector(ticker: str, info: Dict[str, Any]) -> Tuple[List[dict], str]:
    from bellomberg.market_data.sector_taxonomy import classify
    from bellomberg.valuation.peer_comps import select_peer_comps
    seed = classify(info.get("industry") or "", info.get("sector") or "", ticker).get("peers") or []
    return select_peer_comps(ticker, info, seed, max_peers=MAX_PEERS)


def _default_resolve(tickers: List[str]) -> Dict[str, str]:
    from bellomberg.cli.price_updater import data_ticker_map
    return data_ticker_map(tickers)


# ---------------------------------------------------------------- serie

class _Fx:
    """Cambi EURxxx=X scaricati una volta per coppia."""

    def __init__(self, fx_fetch, start: str, end: str):
        self.fetch, self.start, self.end = fx_fetch, start, end
        self.cache: Dict[str, Tuple[Optional[Dict[date, float]], Optional[str]]] = {}

    def rates(self, ccy: str) -> Tuple[Optional[Dict[date, float]], Optional[str], str]:
        pair = "EUR%s=X" % ccy
        if pair not in self.cache:
            try:
                raw = self.fetch(pair, self.start, self.end)
                if not isinstance(raw, dict) or raw.get("symbol") != pair:
                    raise ValueError("identita' del cambio diversa dalla richiesta")
                if raw.get("currency") != ccy:
                    raise ValueError("valuta del cambio %s, attesa %s" % (raw.get("currency"), ccy))
                out = {}
                for row in raw.get("observations") or []:
                    v = _num(row.get("close"))
                    if v is not None and v > 0:
                        out[date.fromisoformat(str(row["date"])[:10])] = v
                if not out:
                    raise ValueError("nessun cambio valido nella finestra")
                self.cache[pair] = (out, None)
            except Exception as exc:  # noqa: BLE001 — dichiarato nella serie
                self.cache[pair] = (None, "cambio %s non disponibile (%s)" % (pair, type(exc).__name__))
        rates, why = self.cache[pair]
        return rates, why, pair


def _series(symbol: str, raw: Any, *, role_label: str, price_field: str, start: date,
            end_excl: date, fx: Optional[_Fx]) -> Dict[str, Any]:
    """Una SeriesEntry dallo snapshot del fornitore (schema in trade_idea_market_stats)."""
    field_src = {"adj_close": "Adj Close", "level": "Close (livello)", "close": "Close"}[price_field]
    entry: Dict[str, Any] = {"ticker": symbol, "currency": None, "native_currency": None,
                             "price_field": price_field, "source": "yfinance " + field_src,
                             "status": "ok", "reason": None, "dates": [], "values": [],
                             "conversion": None, "close_native": []}

    def fail(status: str, why: str) -> Dict[str, Any]:
        entry.update(status=status, reason="%s (%s): %s" % (role_label, symbol, why),
                     dates=[], values=[], close_native=[])
        return entry

    if not isinstance(raw, dict):
        return fail("error", "risposta del fornitore non leggibile")
    if raw.get("symbol") != symbol:
        return fail("error", "il fornitore ha restituito un altro simbolo (%s)" % raw.get("symbol"))
    native = raw.get("currency")
    if not native:
        return fail("error", "valuta non dichiarata dal fornitore (non si deduce)")
    entry["native_currency"] = native
    rows = raw.get("observations")
    if not isinstance(rows, list):
        return fail("error", "osservazioni assenti")
    key = "close" if price_field == "level" else price_field
    dates: List[date] = []
    vals: List[Optional[float]] = []
    closes: List[Optional[float]] = []
    invalid = 0
    for row in rows:
        try:
            d = date.fromisoformat(str(row["date"])[:10])
        except (KeyError, TypeError, ValueError):
            return fail("error", "data non leggibile nelle osservazioni")
        if not (start <= d < end_excl):
            continue
        if dates and d <= dates[-1]:
            return fail("error", "date duplicate o non crescenti")
        v, c = _num(row.get(key)), _num(row.get("close"))
        if row.get(key) is not None and (v is None or v <= 0):
            invalid += 1
        dates.append(d)
        vals.append(v if (v is not None and v > 0) else None)
        closes.append(c if (c is not None and c > 0) else None)
    notes: List[str] = []
    if invalid:
        notes.append("%d valori non finiti o non positivi trattati come buchi" % invalid)

    # conversione in EUR (il VIX e' un livello: niente conversione)
    if price_field == "level":
        entry["currency"] = native
        entry["conversion"] = {"method": "none_level", "fx_ticker": None, "n_dropped_no_fx": 0,
                               "note": "livello dell'indice in punti, nessuna conversione"}
    elif native == BASE_CURRENCY:
        entry["currency"] = BASE_CURRENCY
        entry["conversion"] = {"method": "native", "fx_ticker": None, "n_dropped_no_fx": 0,
                               "note": "gia' in EUR, nessuna conversione"}
    else:
        major, divisor = _MINOR_UNITS.get(native, (native, 1.0))
        if fx is None:
            return fail("error", "conversione in EUR non configurata")
        rates, why, pair = fx.rates(major)
        if rates is None:
            return fail("error", why + ": serie non convertibile in EUR (nessun ripiego in valuta locale)")
        dropped = 0
        conv: List[Optional[float]] = []
        for d, v in zip(dates, vals):
            r = rates.get(d)
            if v is None:
                conv.append(None)
            elif r is None:
                conv.append(None)
                dropped += 1
            else:
                conv.append(v / divisor / r)
        vals = conv
        method = _FX_METHOD.format(pair=pair, ccy=major)
        if divisor != 1.0:
            method += "; quotazione in %s (unita' minore) divisa per %g" % (native, divisor)
        entry["currency"] = BASE_CURRENCY
        entry["conversion"] = {"method": "fx_same_date", "fx_ticker": pair, "fx_field": "Close",
                               "rate_unit": "%s per EUR" % major,
                               "operation": "prezzo nativo / cambio stesso giorno, senza fill",
                               "n_dropped_no_fx": dropped, "note": method}
        if dropped:
            notes.append("%d date senza cambio %s: buchi, nessun fill" % (dropped, pair))

    entry["dates"] = [d.isoformat() for d in dates]
    entry["values"] = vals
    entry["close_native"] = closes
    valid = [d for d, v in zip(dates, vals) if v is not None]
    n_missing = sum(1 for v in vals if v is None)
    long_gaps = sum(1 for a, b in zip(valid, valid[1:]) if _bdays(a, b) > LONG_GAP_BDAYS)
    years = round((valid[-1] - valid[0]).days / 365.25, 2) if valid else 0.0
    entry["coverage"] = {"first": valid[0].isoformat() if valid else None,
                         "last": valid[-1].isoformat() if valid else None,
                         "years_observed": years, "n_prices": len(valid), "n_missing": n_missing,
                         "n_long_gaps": long_gaps, "requested_start": start.isoformat(),
                         "end_exclusive": end_excl.isoformat(), "sufficient": False}
    entry["sha256"] = _sha({"dates": entry["dates"], "values": vals})
    if n_missing:
        notes.append("%d chiusure mancanti nel calendario del listino (buchi dichiarati)" % n_missing)
    if long_gaps:
        notes.append("%d interruzioni oltre %d giorni lavorativi" % (long_gaps, LONG_GAP_BDAYS))
    if not valid:
        return fail("missing", "nessun prezzo valido nella finestra richiesta")
    last_day = end_excl - timedelta(days=1)
    stale = _bdays(valid[-1], last_day) > STALE_BDAYS
    min_start = _years_back(valid[-1], MIN_YEARS) + timedelta(days=START_SLACK_DAYS)
    sufficient = valid[0] <= min_start
    entry["coverage"]["sufficient"] = sufficient
    if not sufficient:
        notes.insert(0, "storia insufficiente: %.2f anni coperti dal %s (servono almeno %d anni)"
                     % (years, valid[0].strftime("%d/%m/%Y"), MIN_YEARS))
    elif valid[0] > start + timedelta(days=START_SLACK_DAYS):
        notes.append("storia piu' corta della finestra richiesta: %.2f anni dal %s"
                     % (years, valid[0].strftime("%d/%m/%Y")))
    if stale:
        entry["status"] = "stale"
        notes.insert(0, "ultimo prezzo al %s, oltre %d giorni lavorativi prima del %s"
                     % (valid[-1].strftime("%d/%m/%Y"), STALE_BDAYS, last_day.strftime("%d/%m/%Y")))
    if notes:
        entry["reason"] = "%s (%s): %s" % (role_label, symbol, "; ".join(notes))
    return entry


# ---------------------------------------------------------------- multipli

def _current_multiples(symbol: str, role: str, info_fetch, as_of: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ticker": symbol, "role": role, "ev_sales": None, "ev_ebitda": None,
                           "pe_trailing": None, "pe_forward": None, "price_to_book": None,
                           "status": "ok", "reason": None, "missing": [],
                           "source": "yfinance .info (trailingPE e forwardPE separati, mai mescolati)",
                           "as_of": as_of}
    try:
        info = info_fetch(symbol)
        if not isinstance(info, dict) or not info:
            raise ValueError("risposta vuota")
    except Exception as exc:  # noqa: BLE001 — dichiarato
        out.update(status="error", reason="%s: multipli non scaricati (%s)" % (symbol, type(exc).__name__))
        return out
    miss = out["missing"]
    ev, sales, ebitda = (_num(info.get(k)) for k in ("enterpriseValue", "totalRevenue", "ebitda"))
    trade_ccy, fin_ccy = info.get("currency"), info.get("financialCurrency")
    same_ccy = not (trade_ccy and fin_ccy and _MINOR_UNITS.get(trade_ccy, (trade_ccy,))[0] != fin_ccy)
    if not same_ccy:
        miss.append("EV/ricavi ed EV/EBITDA n.d.: EV in %s e bilancio in %s (rapporto fra valute "
                    "diverse non calcolato)" % (trade_ccy, fin_ccy))
    elif ev is None or ev <= 0:
        miss.append("EV/ricavi ed EV/EBITDA n.d.: enterprise value assente o non positivo")
    else:
        if sales and sales > 0:
            out["ev_sales"] = round(ev / sales, 4)
        else:
            miss.append("EV/ricavi n.d.: ricavi assenti o non positivi")
        if ebitda and ebitda > 0:
            out["ev_ebitda"] = round(ev / ebitda, 4)
        else:
            miss.append("EV/EBITDA n.d.: EBITDA assente o non positivo")
    for k_out, k_in, lab in (("pe_trailing", "trailingPE", "P/E trailing"),
                             ("pe_forward", "forwardPE", "P/E forward"),
                             ("price_to_book", "priceToBook", "P/B")):
        v = _num(info.get(k_in))
        if v is not None and v > 0:
            out[k_out] = round(v, 4)
        else:
            miss.append("%s n.d.: assente o non positivo nel fornitore" % lab)
    if all(out[k] is None for k in ("ev_sales", "ev_ebitda", "pe_trailing", "pe_forward",
                                    "price_to_book")):
        out.update(status="missing", reason="%s: nessun multiplo disponibile" % symbol)
    return out


def _fy_close(dates: List[str], closes: List[Optional[float]], fy: int) -> Tuple[Optional[str], Optional[float]]:
    """Ultima chiusura valida dell'anno solare fy (Close, non rettificato per dividendi)."""
    best = (None, None)
    for ds, c in zip(dates, closes):
        if ds[:4] == str(fy) and c is not None:
            best = (ds, c)
    return best


def _history_multiples(symbol: str, name: Optional[str], cand: Dict[str, Any],
                       history_loader) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ticker": symbol, "status": "ok", "reason": None, "source": None,
                           "currency": None, "years": [],
                           "approximations": [_EV_APPROX, _EBITDA_APPROX, _FYE_APPROX, _EPS_NOTE],
                           "price_field": "Close (rettificato per split, non per dividendi), valuta del listino"}
    if cand.get("status") in ("error", "missing") or not cand.get("close_native"):
        out.update(status="missing", reason="multipli storici n.d.: serie prezzi del candidato non disponibile")
        return out
    try:
        h = history_loader(symbol, name)
    except Exception as exc:  # noqa: BLE001
        h = {"error": type(exc).__name__}
    if not isinstance(h, dict) or h.get("error"):
        out.update(status="missing", reason="multipli storici n.d.: storico di bilancio non disponibile (%s)"
                   % ((h or {}).get("error") if isinstance(h, dict) else "risposta non leggibile"))
        return out
    out["source"] = h.get("_source") or "fonte non dichiarata"
    if h.get("_chain_note"):
        out["source"] += " — " + h["_chain_note"]
    items, units = h.get("items") or {}, h.get("units") or {}
    native = cand.get("native_currency")
    major, divisor = _MINOR_UNITS.get(native, (native, 1.0))
    fin_ccys = {_ccy_norm(units.get(k)) for k in ("revenue", "eps_diluted", "equity")
                if units.get(k)} - {None}
    if len(fin_ccys) != 1 or major not in fin_ccys:
        out.update(status="missing", reason="multipli storici n.d.: valuta del bilancio (%s) diversa da quella "
                   "del listino (%s), nessuna conversione applicata" % (", ".join(sorted(fin_ccys)) or "n.d.", native))
        return out
    out["currency"] = major

    def g(item, fy):
        ser = items.get(item) or {}
        return _num(ser.get(fy, ser.get(str(fy))))

    fys = sorted({int(y) for s in items.values() if isinstance(s, dict) for y in s})
    for fy in fys:
        day, close = _fy_close(cand["dates"], cand["close_native"], fy)
        if close is None:
            continue                       # esercizio fuori dalla finestra prezzi: non e' un buco
        close = close / divisor
        row: Dict[str, Any] = {"fy": fy, "fy_end_assumed": "%d-12-31" % fy, "close_date": day,
                               "close_fy_end": round(close, 6), "pe": None, "ev_sales": None,
                               "ev_ebitda": None, "p_b": None, "missing": []}
        miss = row["missing"]
        eps, shares = g("eps_diluted", fy), g("shares_diluted", fy)
        rev, ebit, da = g("revenue", fy), g("operating_income", fy), g("dep_amort", fy)
        cash, ltd, eq = g("cash", fy), g("lt_debt", fy), g("equity", fy)
        if eps is not None and eps > 0:
            row["pe"] = round(close / eps, 4)
        else:
            miss.append("P/E n.d.: EPS diluito %s" % ("assente" if eps is None else "non positivo"))
        mcap = close * shares if (shares and shares > 0) else None
        if mcap is None:
            miss.append("capitalizzazione n.d.: azioni diluite assenti (EV e P/B n.d.)")
        ev = None
        if mcap is not None:
            if ltd is None or cash is None:
                miss.append("EV n.d.: %s assente" % ("debito a lungo termine" if ltd is None else "cassa"))
            else:
                ev = mcap + ltd - cash
                row["ev"] = round(ev, 2)
            if eq is not None and eq > 0:
                row["p_b"] = round(mcap / eq, 4)
            else:
                miss.append("P/B n.d.: patrimonio netto %s" % ("assente" if eq is None else "non positivo"))
        if ev is not None and ev > 0:
            if rev and rev > 0:
                row["ev_sales"] = round(ev / rev, 4)
            else:
                miss.append("EV/ricavi n.d.: ricavi assenti o non positivi")
            if ebit is None or da is None:
                miss.append("EV/EBITDA n.d.: %s assente" % ("risultato operativo" if ebit is None
                                                             else "ammortamenti"))
            elif ebit + da > 0:
                row["ebitda"] = round(ebit + da, 2)
                row["ev_ebitda"] = round(ev / (ebit + da), 4)
            else:
                miss.append("EV/EBITDA n.d.: EBITDA approssimato non positivo")
        elif ev is not None:
            miss.append("EV/ricavi ed EV/EBITDA n.d.: EV approssimato non positivo")
        out["years"].append(row)
    if not out["years"]:
        out.update(status="missing", reason="multipli storici n.d.: nessun esercizio cade nella finestra prezzi")
    return out


def _parse_rejected(note: str) -> List[Dict[str, str]]:
    """Scarti dalla nota di select_peer_comps (la nota resta la fonte di verita')."""
    m = re.search(r"scartati: (.*?)(?:\.\.\.)?(?:\. NESSUN|$)", note or "")
    if not m:
        return []
    return [{"ticker": t, "reason": r} for t, r in re.findall(r"([^\s,]+) \(([^)]*)\)", m.group(1))]


def _comp_ticker(comp: Dict[str, Any]) -> Optional[str]:
    """select_peer_comps porta il ticker solo dentro 'name' = 'Nome (TICKER)'."""
    m = re.search(r"\(([^()]+)\)\s*$", str(comp.get("name") or ""))
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- pubblica

def build_market_pack(*, candidate, peers, today, window_years: int = 5,
                      fetch: Optional[Callable] = None, fx_fetch: Optional[Callable] = None,
                      info_fetch: Optional[Callable] = None,
                      history_loader: Optional[Callable] = None,
                      peer_selector: Optional[Callable] = None,
                      resolve_symbols: Optional[Callable] = None,
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """Pacchetto di mercato (schema in reporting/trade_idea_market_stats.py).

    candidate: ticker (str) o dict {"ticker", "name"?}; peers: lista del PM o
    None/vuota (-> selettore deterministico); today: date o "YYYY-MM-DD" (data
    del cutoff, ESCLUSA dalla finestra). Mai solleva."""
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    try:
        return _build(candidate, peers, today, window_years, fetch or _yf_fetch,
                      fx_fetch or _yf_fetch, info_fetch or _yf_info,
                      history_loader or functools.partial(_default_history, fino_al=_cutoff_iso(today)),
                      peer_selector or _default_selector,
                      resolve_symbols or _default_resolve, stamp)
    except Exception as exc:  # noqa: BLE001 — il lavoro della run non cade per i dati di mercato
        return {"version": PACK_VERSION, "status": "unavailable", "base_currency": BASE_CURRENCY,
                "reason": "pacchetto di mercato non costruito (%s)" % type(exc).__name__,
                "fetched_at": stamp, "series": {}, "peers": [], "issues": []}


def _build(candidate, peers, today, window_years, fetch, fx_fetch, info_fetch, history_loader,
           peer_selector, resolve_symbols, stamp) -> Dict[str, Any]:
    import importlib.metadata as _md
    if isinstance(window_years, bool) or not isinstance(window_years, int) or window_years < MIN_YEARS:
        raise ValueError("window_years deve essere un intero >= %d" % MIN_YEARS)
    end_excl = today if isinstance(today, date) else date.fromisoformat(str(today)[:10])
    if isinstance(end_excl, datetime):
        end_excl = end_excl.date()
    start = _years_back(end_excl, window_years)
    s_iso, e_iso = start.isoformat(), end_excl.isoformat()
    if isinstance(candidate, dict):
        cand_tk, cand_name = str(candidate.get("ticker") or "").strip().upper(), candidate.get("name")
    else:
        cand_tk, cand_name = str(candidate or "").strip().upper(), None
    if not cand_tk:
        raise ValueError("candidato senza ticker")
    try:
        provider = "yfinance " + _md.version("yfinance")
    except Exception:  # noqa: BLE001
        provider = "yfinance (versione n.d.)"
    issues: List[Dict[str, str]] = []
    _info_cache: Dict[str, Any] = {}
    _raw_info = info_fetch

    def info_fetch(sym):  # una sola richiesta .info per simbolo (controllo emittente + multipli)
        if sym not in _info_cache:
            try:
                _info_cache[sym] = _raw_info(sym)
            except Exception as exc:  # noqa: BLE001 — risollevata a chi la usa
                _info_cache[sym] = exc
        hit = _info_cache[sym]
        if isinstance(hit, Exception):
            raise hit
        return hit
    fx = _Fx(fx_fetch, s_iso, e_iso)

    def resolve(tks: List[str]) -> Dict[str, str]:
        try:
            m = resolve_symbols(tks)
            return {t: m.get(t, t) for t in tks}
        except Exception as exc:  # noqa: BLE001 — dichiarato
            issues.append({"symbol": ",".join(tks), "reason": "alias Yahoo non risolti (%s): usato il "
                           "ticker cosi' com'e'" % type(exc).__name__})
            return {t: t for t in tks}

    def series(tk: str, sym: str, role_label: str, field: str) -> Dict[str, Any]:
        try:
            raw = fetch(sym, s_iso, e_iso)
        except Exception as exc:  # noqa: BLE001 — dichiarato nella serie
            raw = exc
        if isinstance(raw, Exception):
            e = _series(sym, None, role_label=role_label, price_field=field, start=start,
                        end_excl=end_excl, fx=fx)
            e["reason"] = "%s (%s): download fallito (%s)" % (role_label, sym, type(raw).__name__)
        else:
            e = _series(sym, raw, role_label=role_label, price_field=field, start=start,
                        end_excl=end_excl, fx=fx)
        if sym != tk:
            e["ticker_internal"] = tk
        if e.get("reason"):
            issues.append({"symbol": sym, "reason": e["reason"]})
        return e

    sym_c = resolve([cand_tk])[cand_tk]
    out: Dict[str, Any] = {"version": PACK_VERSION, "status": "ready", "reason": None,
                           "fetched_at": stamp, "provider": provider, "base_currency": BASE_CURRENCY,
                           "benchmark_ticker": BENCHMARK_TICKER,
                           "window": {"start": s_iso, "end_exclusive": e_iso, "years": window_years,
                                      "min_years": MIN_YEARS},
                           "series": {}, "peers": [], "issues": issues}
    cand = series(cand_tk, sym_c, "Candidato", "adj_close")
    out["series"]["candidate"] = cand
    bench = series(BENCHMARK_TICKER, BENCHMARK_TICKER, "Benchmark S&P 500 Total Return", "adj_close")
    if bench["status"] in ("error", "missing"):
        why_tr = bench.get("reason") or "serie non disponibile"
        bench = series(BENCHMARK_FALLBACK, BENCHMARK_FALLBACK, "Benchmark S&P 500 di prezzo", "adj_close")
        nota = ("PROXY: S&P 500 indice di PREZZO senza dividendi, mentre il candidato (Adj Close) include "
                "i dividendi; il total return %s non era disponibile (%s)" % (BENCHMARK_TICKER, why_tr))
        bench["source"] += " (" + nota + ")"
        bench["proxy"] = nota
        bench["index_type"] = "price"
        if bench["status"] in ("error", "missing"):
            bench["reason"] = "%s; total return %s: %s" % (bench.get("reason"), BENCHMARK_TICKER, why_tr)
        issues.append({"symbol": BENCHMARK_FALLBACK, "reason": nota})
    else:
        bench["source"] += " (S&P 500 Total Return, dividendi reinvestiti)"
        bench["index_type"] = "total_return"
    out["benchmark_ticker"] = bench["ticker"]
    out["series"]["benchmark"] = bench
    out["series"]["vix"] = series(VIX_TICKER, VIX_TICKER, "VIX", "level")

    # peer: lista del PM, altrimenti selettore deterministico
    pm_list = [str(p).strip().upper() for p in (peers or []) if str(p or "").strip()]
    pm_list = [p for p in dict.fromkeys(pm_list) if p != cand_tk]
    if pm_list:
        sel = {"method": "pm", "note": "peer indicati dal PM nella richiesta della run (%d)" % len(pm_list),
               "rejected": []}
        peer_tks = pm_list[:MAX_PEERS_REQUEST]
        if len(pm_list) > MAX_PEERS_REQUEST:
            sel["rejected"] = [{"ticker": p, "reason": "oltre il massimo di %d peer" % MAX_PEERS_REQUEST}
                               for p in pm_list[MAX_PEERS_REQUEST:]]
    else:
        try:
            info_c = info_fetch(sym_c) or {}
            comps, note = peer_selector(sym_c, info_c)
            peer_tks, unreadable = [], 0
            for c in comps or []:
                t = _comp_ticker(c)
                if t:
                    peer_tks.append(t.upper())
                else:
                    unreadable += 1
            rejected = _parse_rejected(str(note))
            note = ("nessuna lista del PM nella richiesta: selettore deterministico "
                    "select_peer_comps — " + str(note))
            if unreadable:
                note += "; %d peer senza ticker leggibile nel nome (esclusi, dichiarati)" % unreadable
            note += ("; il P/E del selettore (trailing, altrimenti forward) NON e' usato: P/E "
                     "trailing e forward riletti separatamente")
            sel = {"method": "select_peer_comps", "note": note, "rejected": rejected}
        except Exception as exc:  # noqa: BLE001
            peer_tks = []
            sel = {"method": "select_peer_comps", "rejected": [],
                   "note": "selettore peer fallito (%s): nessun peer" % type(exc).__name__}
        if not peer_tks:
            issues.append({"symbol": cand_tk, "reason": "nessun peer: " + sel["note"]})
    out["peer_selection"] = sel
    peer_map = resolve(peer_tks) if peer_tks else {}
    # dopo la risoluzione degli alias: un peer che E' il candidato (stesso simbolo Yahoo) o un
    # doppione di un altro peer si scarta DICHIARATO (review RV-L3: candidato peer di se stesso)
    tenuti, visti = [], {sym_c}
    for p in peer_tks:
        sym = peer_map[p]
        if sym in visti:
            why = ("stesso simbolo Yahoo del candidato (%s)" % sym if sym == sym_c
                   else "doppione: stesso simbolo Yahoo di un altro peer (%s)" % sym)
            sel["rejected"].append({"ticker": p, "reason": why})
            issues.append({"symbol": p, "reason": "peer scartato: " + why})
            continue
        visti.add(sym)
        tenuti.append(p)
    peer_tks = tenuti
    # stesso EMITTENTE su un altro listino o via ISIN (simbolo diverso, societa' uguale):
    # nome normalizzato come il dedup cross-listing di peer_comps (_norm_issuer)
    if peer_tks:
        from bellomberg.valuation.peer_comps import _norm_issuer

        def _issuer(sym):
            try:
                inf = info_fetch(sym) or {}
            except Exception:  # noqa: BLE001 — senza .info il controllo non si fa: dichiarato sotto
                return None
            return _norm_issuer(inf.get("longName") or inf.get("shortName")) or None

        iss_c = _issuer(sym_c) or _norm_issuer(cand_name) or None
        tenuti = []
        for p in peer_tks:
            iss_p = _issuer(peer_map[p])
            if iss_c and iss_p and iss_p == iss_c:
                why = "stesso emittente del candidato su un altro listino (%s)" % peer_map[p]
                sel["rejected"].append({"ticker": p, "reason": why})
                issues.append({"symbol": p, "reason": "peer scartato: " + why})
                continue
            if not (iss_c and iss_p):
                issues.append({"symbol": p, "reason": "controllo 'stesso emittente del candidato' non "
                               "eseguito: nome societa' n.d. nel fornitore"})
            tenuti.append(p)
        peer_tks = tenuti
    out["peers"] = [series(p, peer_map[p], "Peer", "adj_close") for p in peer_tks]

    # multipli correnti (candidato + peer) e storici (candidato)
    cur = [_current_multiples(sym_c, "candidate", info_fetch, e_iso)]
    cur += [_current_multiples(peer_map[p], "peer", info_fetch, e_iso) for p in peer_tks]
    hist = _history_multiples(sym_c, cand_name, cand, history_loader)
    out["multiples"] = {"current": cur, "history": hist, "peer_selection": sel}
    # il Close nativo serve solo ai multipli storici del candidato: tolto dagli altri (peso checkpoint)
    for e in [bench, out["series"]["vix"], *out["peers"]]:
        e.pop("close_native", None)
    for m in cur + [hist]:
        if m.get("status") != "ok":
            issues.append({"symbol": m["ticker"], "reason": m["reason"]})

    # stato complessivo
    if cand["status"] in ("error", "missing"):
        out["status"] = "unavailable"
        out["reason"] = cand["reason"]
    else:
        clean = (all(e["status"] == "ok" and e["coverage"].get("sufficient")
                     for e in [cand, bench, out["series"]["vix"], *out["peers"]] if e.get("coverage"))
                 and all(e["status"] == "ok" for e in [bench, out["series"]["vix"], *out["peers"]])
                 and not issues)
        if not clean:
            out["status"] = "partial"
            out["reason"] = "; ".join(i["reason"] for i in issues[:6]) + (
                " (+%d)" % (len(issues) - 6) if len(issues) > 6 else "")
    out["as_of"] = cand.get("coverage", {}).get("last")
    out["sha256"] = _sha({k: v for k, v in out.items() if k not in ("sha256", "fetched_at")})
    return out


# ---------------------------------------------------------------- file accanto alla run

PACK_FILENAME = "market-pack.json"


def unavailable_pack(reason: str) -> Dict[str, Any]:
    """Pacchetto dichiarato non disponibile (forma minima letta da L2/L3)."""
    return {"version": PACK_VERSION, "status": "unavailable", "reason": reason,
            "base_currency": BASE_CURRENCY, "series": {}, "peers": [], "issues": []}


def save_market_pack(pack: Dict[str, Any], path) -> Dict[str, Any]:
    """Scrive il pacchetto su file (temporaneo + fsync + os.replace) e restituisce il
    RIFERIMENTO da tenere nel checkpoint: {version, path, sha256, status, as_of}.
    Lo sha256 e' quello dei BYTE scritti (verificato alla rilettura). Solleva su I/O."""
    import os
    import tempfile
    from pathlib import Path
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = _json(pack).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".market-pack-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"version": PACK_VERSION, "path": str(target), "sha256": hashlib.sha256(raw).hexdigest(),
            "status": pack.get("status"), "as_of": pack.get("as_of"), "bytes": len(raw)}


def load_market_pack(ref: Any) -> Optional[Dict[str, Any]]:
    """Rilegge il pacchetto dal riferimento del checkpoint, VERIFICANDO lo sha256.
    None se la run non ha riferimento (run precedenti al Lotto 3). File assente,
    illeggibile o con sha diverso = pacchetto 'unavailable' DICHIARATO: mai
    ricostruito in silenzio. Non solleva."""
    from pathlib import Path
    if ref is None:
        return None
    if isinstance(ref, dict) and ref.get("path") is None and ref.get("status") == "unavailable":
        return unavailable_pack(str(ref.get("reason") or "pacchetto di mercato non disponibile"))
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", str(ref.get("sha256") or "")):
        return unavailable_pack("riferimento al pacchetto di mercato non valido nel checkpoint")
    try:
        raw = Path(ref["path"]).read_bytes()
    except OSError as exc:
        return unavailable_pack("file del pacchetto di mercato non leggibile (%s)" % type(exc).__name__)
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        return unavailable_pack("file del pacchetto di mercato diverso da quello salvato dalla run "
                                "(sha256 non corrisponde): non usato e non ricostruito")
    try:
        pack = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return unavailable_pack("file del pacchetto di mercato non decodificabile (%s)" % type(exc).__name__)
    if not isinstance(pack, dict):
        return unavailable_pack("file del pacchetto di mercato con forma inattesa")
    return pack


def run_market_pack(*, run: Dict[str, Any], cutoff: Any, path, loader: Optional[Callable] = None,
                    network_allowed: bool = True) -> Dict[str, Any]:
    """Costruisce, salva e restituisce il RIFERIMENTO per una run Trade Idea. Mai solleva.
    loader iniettato > build_market_pack (solo se network_allowed, cioe' DB vero) >
    riferimento 'unavailable' dichiarato senza rete."""
    try:
        today = str(cutoff or "")[:10]
        date.fromisoformat(today)
    except ValueError:
        return {"version": PACK_VERSION, "status": "unavailable", "path": None, "sha256": None,
                "as_of": None, "reason": "data di riferimento della run non leggibile: pacchetto non costruito"}
    if loader is None and not network_allowed:
        return {"version": PACK_VERSION, "status": "unavailable", "path": None, "sha256": None, "as_of": None,
                "reason": "DB alternativo senza loader del pacchetto di mercato: nessun download"}
    try:
        build = loader or build_market_pack
        pack = build(candidate={"ticker": run.get("ticker"), "name": run.get("company_name")},
                     peers=list(run.get("peers") or []), today=today)
        if not isinstance(pack, dict):
            raise TypeError("pacchetto non dict")
        return save_market_pack(pack, path)
    except Exception as exc:  # noqa: BLE001 — il lavoro pagato non cade per i dati di mercato
        return {"version": PACK_VERSION, "status": "unavailable", "path": None, "sha256": None, "as_of": None,
                "reason": "pacchetto di mercato non costruito o non salvato (%s)" % type(exc).__name__}


def normalize_request_peers(value: Any, *, ticker: str) -> List[str]:
    """Peer del PM nella richiesta: lista di ticker (max MAX_PEERS_REQUEST), maiuscoli,
    senza doppioni ne' il candidato. Solleva ValueError con un messaggio leggibile."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("peers deve essere una lista di ticker")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("peers: ogni peer deve essere un ticker testuale")
        t = item.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=_:/-]{0,39}", t):
            raise ValueError("peers: ticker non valido (%s)" % item[:40])
        if t == str(ticker or "").strip().upper():
            raise ValueError("peers: il candidato non puo' essere peer di se stesso")
        if t in out:
            raise ValueError("peers: ticker ripetuto (%s)" % t)
        out.append(t)
    if len(out) > MAX_PEERS_REQUEST:
        raise ValueError("peers: al massimo %d ticker" % MAX_PEERS_REQUEST)
    return out
