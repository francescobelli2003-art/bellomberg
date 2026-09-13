"""
BELLOMBERG - Portfolio Analytics Engine (TIER 1)

Calcoli PM-grade per Performance page (/performance, F-key dedicato):
  - NAV history (ricostruzione vera da trade_history + prezzi yfinance daily)
  - Benchmark comparison (ACWI default)
  - Top-5 drawdowns con duration + recovery time
  - Liquidity score per ticker (days-to-liquidate)
  - Concentration metrics (HHI by ticker / region / currency)
  - VaR contribution (Component VaR, Jorion 2006)

Tutto puramente locale: numpy + yfinance + SQLite. ZERO chiamate Claude.
"""
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple

try:
    import numpy as np
    import pandas as pd
    NUMPY_OK = True
except Exception:
    NUMPY_OK = False

try:
    import yfinance as yf
    YF_OK = True
except Exception:
    YF_OK = False

from bellomberg.storage.memory_db import MemoryDB
from bellomberg.core.presentation import message as _message, render_payload, join_messages, error_text

# Cache risultati pesanti per evitare ricalcoli (NAV history + drawdowns)
_ANALYTICS_CACHE: Dict[str, Any] = {}
CACHE_TTL_SEC = 600  # 10 min

# Soglie liquidity (giorni necessari per liquidare l'intera posizione
# a 20% del volume medio giornaliero)
LIQ_GREEN_DAYS  = 1.0
LIQ_YELLOW_DAYS = 5.0
LIQ_VOL_FRACTION = 0.20  # presupposto: puoi vendere max 20% volume daily senza muovere il prezzo

# I simboli senza serie prezzi utile su yfinance stanno nel NEGOZIO PRIVATO dei prezzi speciali
# (negozi_privati.carica_prezzi_speciali: data/prezzi_speciali.json, forma in
# prezzi_speciali.example.json), RILETTO A OGNI CHIAMATA: una modifica a mano si vede senza
# riavviare e le prove possono puntare PERCORSO_PREZZI altrove. Negozio assente = insieme VUOTO,
# che NON e' «niente da saltare»: senza la lista il perimetro cambia e i numeri in euro con lui,
# quindi chi scala sul perimetro (NAV, VaR-contribution) si FERMA con ko_negozio_prezzi().
def prezzi_speciali() -> Dict[str, Any]:
    """L'esito INTERO del negozio: {"prezzi": {...}, "origine": ..., "motivo": ...}."""
    from bellomberg.storage.negozi_privati import carica_prezzi_speciali
    return carica_prezzi_speciali()


def salta_prezzi() -> frozenset:
    """I simboli da NON scaricare da yfinance, riletti dal negozio a ogni chiamata.
    Accessorio pubblico per chi vuole SOLO l'insieme: chi deve anche DICHIARARE il buco
    usa prezzi_speciali(), che porta origine e motivo. Nessun chiamante di produzione oggi
    (misurato 05/09): i sei consumatori leggono l'esito intero perche' tutti dichiarano."""
    return prezzi_speciali()["prezzi"]["senza_yfinance"]


def ko_negozio_prezzi(esito: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """L'errore DICHIARATO se il negozio non si legge, altrimenti None (regola PM 14/07:
    mai un ripiego muto). Va chiamata PRIMA di toccare il DB, cosi' il KO non dipende da
    quanto e' arrivato lontano il calcolo."""
    if esito["origine"] in ("assente", "illeggibile"):
        return {"error": _message("negozio dei prezzi speciali {origin}: {reason} — nessun simbolo da saltare, e senza quella lista il perimetro (e i numeri in euro) cambierebbe", "Special price store {origin}: {reason} — no symbols to skip, and without that list the scope (and EUR values) would change", origin=esito["origine"], reason=esito["motivo"]),
                "negozio_prezzi": {"origine": esito["origine"], "motivo": esito["motivo"]},
                "timestamp": datetime.now().isoformat()}
    return None

# Region map by suffix
_REGION_BY_SUFFIX = {
    ".L": "UK", ".MI": "EU", ".DE": "EU", ".PA": "EU", ".AS": "EU",
    ".VI": "EU", ".SW": "EU", ".BR": "EU", ".MC": "EU", ".LS": "EU",
    ".CO": "EU", ".ST": "EU", ".HE": "EU", ".OL": "EU",
    ".HK": "ASIA", ".T": "ASIA", ".TW": "ASIA", ".KS": "ASIA", ".KQ": "ASIA",
    ".SS": "CN", ".SZ": "CN",
    ".NS": "INDIA", ".BO": "INDIA",
    ".AX": "AUS",
    ".TO": "CAN", ".V": "CAN",
    ".SA": "BRAZIL", ".MX": "MEX",
}

def _region_of(ticker: str) -> str:
    t = (ticker or "").upper()
    for suf, reg in _REGION_BY_SUFFIX.items():
        if t.endswith(suf):
            return reg
    return "US"  # nessun suffisso = US

def _log(msg: str):
    # fix 13/07: stdout puo' essere una pipe morta (backend orfano spawnato da
    # Electron) -> print solleva OSError [Errno 22] e fa fallire il CALCOLO per
    # colpa di un log. Il log non deve mai abbattere l'endpoint.
    try:
        print(f"[ANALYTICS] {msg}", flush=True)
    except OSError:
        pass


# ============================================================
# NAV HISTORY RECONSTRUCTION (from trade_history)
# ============================================================

def _trade_history() -> List[Dict[str, Any]]:
    """Returns all BUY/SELL/ADD/TRIM trades, sorted by date."""
    db = MemoryDB()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT ticker, action, quantita, prezzo, valuta, data "
            "FROM trade_history ORDER BY data ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def _build_position_timeline(trades: List[Dict[str, Any]]) -> Dict[str, List[Tuple[str, float]]]:
    """Per ogni ticker, costruisci la timeline delle quantita: [(date, qty_cumulative), ...].
    Action mapping: BUY/ADD adds qty, SELL/TRIM removes qty.
    DIVIDEND records are skipped (they don't change holding qty).
    """
    timeline: Dict[str, List[Tuple[str, float]]] = {}
    running: Dict[str, float] = {}
    for t in trades:
        action = (t.get("action") or "BUY").upper()
        if action == "DIVIDEND":
            continue  # dividend events don't change holdings
        ticker = t["ticker"]
        qty = float(t.get("quantita") or 0)
        if action in ("BUY", "ADD"):
            signed = qty
        elif action in ("SELL", "TRIM"):
            signed = -qty
        else:
            continue  # unknown action, skip
        running[ticker] = running.get(ticker, 0) + signed
        d = (t["data"] or "")[:10]
        timeline.setdefault(ticker, []).append((d, running[ticker]))
    return timeline


def _qty_at(timeline_for_ticker: List[Tuple[str, float]], iso_date: str) -> float:
    """Quantita detenuta a iso_date (la quantita risultante dall'ultimo trade prima/uguale a iso_date)."""
    qty = 0.0
    for d, q in timeline_for_ticker:
        if d <= iso_date:
            qty = q
        else:
            break
    return qty


def _ticker_cost_value_at(trades: List[Dict[str, Any]], ticker: str, iso: str, fx_lookup) -> float:
    """205-fix: valore di carico (avg cost) di un ticker a una data, in EUR.
    Usato come fallback quando NESSUN prezzo e' disponibile: la posizione vale
    il suo costo (P&L 0 su quel nome) invece di SPARIRE dal NAV (tuffo finto)."""
    qty = 0.0
    cost = 0.0
    for t in trades:
        if t.get("ticker") != ticker:
            continue
        a = (t.get("action") or "BUY").upper()
        if a == "DIVIDEND":
            continue
        d = (t.get("data") or "")[:10]
        if d > iso:
            continue
        q = float(t.get("quantita") or 0)
        px = float(t.get("prezzo") or 0)
        etichetta_valuta = _currency_label(ticker, t.get("valuta"))
        ccy = etichetta_valuta.valore
        if ccy is None:
            raise ValueError(etichetta_valuta.dichiarazione)
        fxr = fx_lookup(d, ccy)
        if fxr is None:
            raise ValueError(_message("FX {ccy} n.d. per {ticker} al {date}", "FX {ccy} unavailable for {ticker} on {date}", ccy=ccy, ticker=ticker, date=d))
        if a in ("BUY", "ADD"):
            cost += q * px * fxr
            qty += q
        elif a in ("SELL", "TRIM") and qty > 0:
            avg = cost / qty
            cost -= min(q, qty) * avg
            qty -= min(q, qty)
    return max(cost, 0.0)


def _download_prices_for_history(tickers: List[str], start: str, end: str,
                                 salta: frozenset) -> Optional[pd.DataFrame]:
    """Download daily closes per la lista ticker per il range start-end (YYYY-MM-DD).
    `salta` viene dal negozio dei prezzi speciali e arriva dal chiamante, letto UNA volta
    per giro: senza default, cosi' un punto di chiamata dimenticato e' un TypeError subito
    e non un insieme vuoto zitto (lezione del 05/09: gli usi sostituiti e l'assegnazione no)."""
    yf_tickers = [t for t in tickers if t not in salta]
    if not yf_tickers:
        return None
    # Alias broker->dati congelati per l'intera operazione: download e rename usano
    # la stessa coppia anche se il negozio cambia mentre Yahoo risponde.
    try:
        from bellomberg.cli.price_updater import AliasFontiError, data_ticker_map
        dl_map = data_ticker_map(yf_tickers)
    except AliasFontiError:
        raise
    except Exception as e:
        _log(_message("alias yfinance non risolvibile: {error}", "Cannot resolve yfinance alias: {error}", error=error_text(e)))
        return None
    dl_list = sorted(set(dl_map.values()))
    try:
        raw = yf.download(dl_list, start=start, end=end, progress=False,
                           auto_adjust=True, threads=True)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"]
        else:
            close = raw[["Close"]] if "Close" in raw.columns else raw
            if isinstance(close, pd.Series):
                close = close.to_frame(dl_list[0])
        close = close.rename(columns={v: k for k, v in dl_map.items() if v in close.columns and v != k})
        # ffill: buchi infrasettimanali; bfill: giorni PRIMA della prima candela di un nome
        # appena comprato (innocuo: prima dell'acquisto qty=0, il prezzo non entra nel NAV).
        return close.ffill().bfill()
    except Exception as e:
        _log(f"price download failed: {e}")
        return None


def _build_fx_history(currencies: List[str], start: str, end: str) -> pd.DataFrame:
    """Per ogni currency in CCY!=EUR scarica EURCCY=X e calcola CCY->EUR rate giornaliero.
    Returns DataFrame indexed by date, columns = currencies, values = ccy_to_eur.
    GBX = GBP / 100.
    """
    out = pd.DataFrame()
    # Always include EUR=1
    pairs_needed = [c for c in currencies if c not in ("EUR", "GBX")]
    has_gbx = "GBX" in currencies
    if has_gbx and "GBP" not in pairs_needed:
        pairs_needed.append("GBP")

    for ccy in pairs_needed:
        try:
            df = yf.download(f"EUR{ccy}=X", start=start, end=end, progress=False,
                              auto_adjust=True, threads=False)
            if df is None or df.empty:
                continue
            close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            # rate is EURccy=X = ccy per 1 EUR; we want ccy->eur = 1/rate
            out[ccy] = 1.0 / close
        except Exception as e:
            _log(f"FX {ccy} failed: {e}")
            continue

    # EUR = 1.0
    if not out.empty:
        out["EUR"] = 1.0
        if has_gbx and "GBP" in out.columns:
            out["GBX"] = out["GBP"] / 100.0
    return out


def _currency_label(ticker: str, valuta_posizione=None, negozio=None):
    """Classificazione unica; il negozio viene riletto dalla funzione a ogni call."""
    from bellomberg.storage.classificazione import valuta
    return valuta(ticker, negozio=negozio, valuta_posizione=valuta_posizione)


def _infer_currency(ticker: str, valuta_posizione=None) -> Optional[str]:
    """Compatibilita' interna: None significa valuta non determinabile, mai USD implicito."""
    return _currency_label(ticker, valuta_posizione).valore


def _currency_labels_for_tickers(tickers, records, negozio=None):
    """Etichetta ogni ticker preferendo l'ultima valuta esplicita salvata nel DB."""
    labels = {}
    for ticker in tickers:
        esplicita = next((r.get("valuta") for r in reversed(records)
                          if r.get("ticker") == ticker and r.get("valuta") is not None), None)
        labels[ticker] = _currency_label(ticker, esplicita, negozio=negozio)
    return labels


def _basis_and_realized_at(trades: List[Dict[str, Any]], iso_date: str,
                            fx_lookup, use_current_fx: bool = False) -> Tuple[float, float]:
    """Cost basis cumulato + realized P/L da vendite a iso_date, in EUR.

    Metodo COSTO MEDIO (average cost) — bugfix #164:
      - BUY/ADD: aggiornano qty e prezzo medio del ticker; CB += qty * prezzo * FX
      - SELL/TRIM: CB -= qty_venduta * prezzo_MEDIO * FX (non prezzo di vendita)
                   realized += qty_venduta * (prezzo_vendita - prezzo_medio) * FX
      - DIVIDEND: ignorati qui (tracciati in _dividend_income_at)
    Vendite senza posizione o oltre la qty posseduta (es. ticker errato):
    fallback legacy CB -= excess * prezzo_vendita * FX, nessun realized.

    Args:
      use_current_fx: False (default dal 23/07, F-CONT-1 riallineamento audit/20) =
                       FX STORICO del giorno del trade via fx_lookup: la serie
                       storica CB/P&L%/realized e' STABILE run-to-run (prima il
                       default True la ricalcolava OGNI giorno con l'FX corrente:
                       la storia cambiava retroattivamente col cambio di oggi).
                       True = FX corrente (allinea col P/L LIVE del Dashboard che
                       usa qty*prezzo_medio*FX_oggi — solo per confronti live).
    Returns:
      (cost_basis_eur, realized_sales_eur)
    """
    # When using current FX, fetch each currency's rate once via get_fx_to_eur (cached)
    if use_current_fx:
        try:
            from bellomberg.cli.price_updater import get_fx_to_eur
        except Exception:
            get_fx_to_eur = None
        fx_cache_current: Dict[str, float] = {}

    holdings: Dict[str, Dict[str, float]] = {}  # ticker -> {"qty": .., "avg": ..}
    cost_basis = 0.0
    realized_total = 0.0
    for t in trades:
        d = (t["data"] or "")[:10]
        if d > iso_date:
            break
        action = (t.get("action") or "BUY").upper()
        if action == "DIVIDEND":
            continue
        ticker = t.get("ticker") or "?"
        qty = float(t.get("quantita") or 0)
        price = float(t.get("prezzo") or 0)
        etichetta_valuta = _currency_label(ticker, t.get("valuta"))
        ccy = etichetta_valuta.valore
        if ccy is None:
            raise ValueError(etichetta_valuta.dichiarazione)

        if use_current_fx and get_fx_to_eur:
            if ccy not in fx_cache_current:
                fx_cache_current[ccy] = get_fx_to_eur(ccy)
            fx = fx_cache_current[ccy]
        else:
            fx = fx_lookup(d, ccy)
        if fx is None:
            raise ValueError(_message("FX {ccy} n.d. per {ticker} al {date}", "FX {ccy} unavailable for {ticker} on {date}", ccy=ccy, ticker=ticker, date=d))

        h = holdings.setdefault(ticker, {"qty": 0.0, "avg": 0.0})
        if action in ("BUY", "ADD"):
            new_qty = h["qty"] + qty
            if new_qty > 0:
                h["avg"] = (h["qty"] * h["avg"] + qty * price) / new_qty
            h["qty"] = new_qty
            cost_basis += qty * price * fx
        elif action in ("SELL", "TRIM"):
            sell_qty = min(qty, h["qty"])
            if sell_qty > 0:
                cost_basis -= sell_qty * h["avg"] * fx
                realized_total += sell_qty * (price - h["avg"]) * fx
                h["qty"] -= sell_qty
            excess = qty - sell_qty
            if excess > 0:  # vendita "fantasma": comportamento legacy, no realized
                cost_basis -= excess * price * fx
    return cost_basis, realized_total


def _cost_basis_at(trades: List[Dict[str, Any]], iso_date: str,
                    fx_lookup, use_current_fx: bool = False) -> float:
    """Backward-compat wrapper (zero chiamanti vivi): ritorna solo il cost basis.
    Default allineato a _basis_and_realized_at (FX storico, F-CONT-1 23/07)."""
    return _basis_and_realized_at(trades, iso_date, fx_lookup, use_current_fx)[0]


# ---------------------------------------------------------------------------
# pl_eur_fx (28/08, decisione PM): il P&L per posizione col cambio STORICO del
# costo. `pl_eur` di /portfolio converte ANCHE il costo al cambio di oggi (niente
# guadagno/perdita sulla valuta). Qui il costo di
# ogni posizione e' un POOL in EUR: ogni BUY entra a qty × prezzo × FX del giorno
# del trade, le vendite tolgono la quota pro-rata del pool. Differenza dichiarata
# rispetto a `_basis_and_realized_at` (F-CONT-1), che alla vendita toglie il costo
# al cambio del giorno della VENDITA: sui titoli esteri venduti in parte le due
# somme divergono di qualche decina di euro.
# ---------------------------------------------------------------------------
import threading as _threading
# review 28/08: in cache va la parte CARA (la serie FX daily, immutabile per
# giorno), non il risultato — gli script di riparazione cambiano quantita'/prezzo/
# data di trade esistenti senza toccare id ne' conteggio, e un risultato in cache
# resterebbe stantio fino a mezzanotte. Il replay su ~80 trade costa microsecondi.
_FX_DF_CACHE: Dict[Tuple[Any, ...], Any] = {}
_FX_DF_LOCK = _threading.Lock()


def _fx_lookup_storico(fx_df):
    """Ritorna lookup(giorno_iso, valuta) -> (rate_ccy_to_eur | None, giorno_usato | None, nota | None).
    Giorno mancante -> ultima osservazione PRIMA (dichiarata); trade prima della
    prima osservazione -> la prima (F-CONT-1, dichiarata); valuta senza serie -> None.
    EUR non ha bisogno di serie."""
    def lookup(d_iso: str, ccy: str):
        if ccy is None or not str(ccy).strip():
            return None, None, _message('valuta n.d.: costo storico n.d.', 'Currency unavailable: historical cost unavailable')
        ccy = str(ccy).strip().upper()
        if ccy == "EUR":
            return 1.0, d_iso, None
        if fx_df is None or fx_df.empty or ccy not in fx_df.columns:
            return None, None, _message('FX {v0} non disponibile: costo storico n.d.', 'FX {v0} unavailable: historical cost unavailable', v0=ccy)
        sub = fx_df[ccy].dropna()
        if sub.empty:
            return None, None, _message('FX {v0} senza osservazioni: costo storico n.d.', 'FX {v0} has no observations: historical cost unavailable', v0=ccy)
        try:
            d_ts = pd.Timestamp(d_iso)
        except (ValueError, TypeError):
            # review 28/08: una data illeggibile mandava a n.d. TUTTO il book
            return None, None, _message('data del trade illeggibile ({v0!r}): FX n.d. per quel lotto', 'Unreadable trade date ({v0!r}): FX unavailable for that lot', v0=d_iso)
        older = sub[sub.index <= d_ts]
        if len(older):
            giorno = older.index[-1].strftime("%Y-%m-%d")
            nota = None if giorno == d_iso else _message('FX {v0} del {v1} usato per il trade del {v2}', 'FX {v0} from {v1} used for trade on {v2}', v0=ccy, v1=giorno, v2=d_iso)
            return float(older.iloc[-1]), giorno, nota
        giorno = sub.index[0].strftime("%Y-%m-%d")
        return float(sub.iloc[0]), giorno, (_message('FX {v0}: trade del {v1} prima della prima osservazione ({v2}), usata quella', 'FX {v0}: trade on {v1} precedes the first observation ({v2}), which was used', v0=ccy, v1=d_iso, v2=giorno))
    return lookup


def costo_storico_per_ticker(trades: List[Dict[str, Any]], fx_lookup,
                             openings=None) -> Dict[str, Dict[str, Any]]:
    """Replay dei trade a POOL: {ticker: {"qty", "costo_eur_storico" (pool in EUR al
    FX del giorno di ogni acquisto; None se un FX manca), "costo_nativo" (stesso
    pool in valuta di quotazione: serve a isolare la componente cambio), "note":
    [..]}}. DIVIDEND ignorati; vendita oltre la quantita' = fantasma dichiarato
    (pool a zero); note deduplicate."""
    out: Dict[str, Dict[str, Any]] = {}
    for opening in openings or []:
        native = float(opening["quantita"]) * float(opening["prezzo_medio"])
        eur = opening["valuta"] == "EUR"
        out[opening["ticker"]] = {
            "qty": float(opening["quantita"]), "pool": native if eur else 0.0,
            "pool_nat": native, "fx_mancante": not eur,
            "note": [], "opening_fx_unknown": not eur,
            "opening_as_of": opening["as_of"]}
    ordinati = sorted(trades, key=lambda t: (str(t.get("data") or ""), t.get("id") or 0))
    for t in ordinati:
        action = (t.get("action") or "BUY").upper()
        if action == "DIVIDEND":
            continue
        ticker = t.get("ticker") or "?"
        qty = float(t.get("quantita") or 0)
        prezzo = float(t.get("prezzo") or 0)
        d = str(t.get("data") or "")[:10]
        h = out.setdefault(ticker, {"qty": 0.0, "pool": 0.0, "pool_nat": 0.0, "note": [], "fx_mancante": False})
        if action in ("BUY", "ADD"):
            etichetta_valuta = _currency_label(ticker, t.get("valuta"))
            ccy = etichetta_valuta.valore
            if ccy is None:
                h["fx_mancante"] = True
                h["note"].append(etichetta_valuta.dichiarazione)
                h["qty"] += qty
                h["pool_nat"] += qty * prezzo
                continue
            rate, _giorno, nota = fx_lookup(d, ccy)
            if nota:
                h["note"].append(nota)
            h["qty"] += qty
            h["pool_nat"] += qty * prezzo
            if rate is None:
                h["fx_mancante"] = True
                continue
            h["pool"] += qty * prezzo * rate
        elif action in ("SELL", "TRIM"):
            venduta = min(qty, h["qty"])
            if venduta > 0 and h["qty"] > 0:
                resta = 1.0 - venduta / h["qty"]
                h["pool"] *= resta
                h["pool_nat"] *= resta
                h["qty"] -= venduta
            if qty - venduta > 1e-9:
                h["note"].append(_message("vendita di {v0:g} oltre la quantita' in carico (fantasma di {v1:g}): pool azzerato", 'Sale of {v0:g} exceeds held quantity (unbacked quantity {v1:g}): pool reset to zero', v0=qty, v1=qty - venduta))
                h["pool"] = 0.0
                h["pool_nat"] = 0.0
                h["qty"] = 0.0
            if h["qty"] <= 1e-9 and "opening_as_of" in h:
                # An unknown acquisition FX belongs to the closed pool only.
                h["fx_mancante"] = False
                h["opening_fx_unknown"] = False
    for h in out.values():
        if h.get("opening_fx_unknown"):
            h["note"].append(_message('Saldo iniziale documentato: data e FX di acquisto ignoti; costo storico EUR n.d.', 'Documented opening balance: acquisition date and FX unknown; historical EUR cost unavailable.'))
        h["costo_eur_storico"] = None if h["fx_mancante"] else round(h["pool"], 2)
        h["costo_nativo"] = round(h["pool_nat"], 6)
        h["qty"] = round(h["qty"], 6)
        h["note"] = list(dict.fromkeys(h["note"]))
        del h["pool"], h["pool_nat"]
    return out


def pl_fx_per_posizione(trades: List[Dict[str, Any]], oggi_iso: str, openings=None) -> Dict[str, Any]:
    """Costo storico per ticker. La SERIE FX (yfinance daily, immutabile per giorno)
    sta in cache per (valute, primo trade, giorno) — /portfolio e' interrogato ogni
    pochi secondi —; il replay si rifa' SEMPRE (microsecondi), cosi' un trade nuovo
    o corretto entra subito. `end` = oggi ESCLUSO: la riga di oggi sarebbe l'intraday
    del primo scarico, congelata; un trade datato oggi prende l'ultima chiusura, con
    nota, e si assesta domani. Errore = {"error": ...} NON cacheato (si riprova),
    mai il cambio di oggi spacciato per storico."""
    if not trades and not openings:
        return {"error": _message('trade_history vuota: costo storico n.d.', 'Empty trade_history: historical cost unavailable')}
    labels_from = list(trades) + list(openings or [])
    tickers = sorted({t.get("ticker") or "?" for t in labels_from})
    currency_labels = _currency_labels_for_tickers(tickers, labels_from)
    non_determinate = [x for x in currency_labels.values() if x.valore is None]
    if non_determinate:
        return {"error": _message("valuta non determinabile: {reasons}", "Cannot determine currency: {reasons}", reasons=join_messages("; ", (x.dichiarazione for x in non_determinate))),
                "currency_labels": {k: v.as_dict() for k, v in currency_labels.items()}}
    valute = sorted({x.valore for x in currency_labels.values()})
    # A balance date is not an acquisition date: no historical FX lookup for it.
    purchase_currencies = {_currency_label(t.get("ticker"), t.get("valuta")).valore
                           for t in trades if t.get("action") in ("BUY", "ADD")}
    estere = [c for c in valute if c != "EUR" and (not openings or c in purchase_currencies)]
    fx_df = None
    fx_error = None
    start = end = None
    if estere:
        giorni = sorted(str(t.get("data") or "")[:10] for t in trades if _valid_iso(str(t.get("data") or "")[:10]))
        start = giorni[0] if giorni else oggi_iso
        end = oggi_iso
        chiave = (tuple(valute), start, end)
        fx_df = _FX_DF_CACHE.get(chiave)
        if fx_df is None:
            with _FX_DF_LOCK:
                fx_df = _FX_DF_CACHE.get(chiave)
                if fx_df is None:
                    try:
                        fx_df = _build_fx_history(valute, start, end)
                    except Exception as e:
                        fx_error = _message('serie FX storica non disponibile ({v0}: {v1}): costo storico n.d.', 'Historical FX series unavailable ({v0}: {v1}): historical cost unavailable', v0=type(e).__name__, v1=e)
                    if fx_df is None or fx_df.empty:
                        fx_error = fx_error or _message('serie FX storica non disponibile (yfinance): costo storico n.d.', 'Historical FX series unavailable (yfinance): historical cost unavailable')
                    if fx_error and not openings:
                        return {"error": fx_error}
                    if not fx_error:
                        _FX_DF_CACHE[chiave] = fx_df
    base_lookup = _fx_lookup_storico(fx_df)
    def lookup(day, currency):
        if fx_error and currency != "EUR":
            return None, None, fx_error
        return base_lookup(day, currency)
    return {"per_ticker": costo_storico_per_ticker(trades, lookup, openings),
            "currency_labels": {k: v.as_dict() for k, v in currency_labels.items()},
            "fx_basis": (_message("costo = pool in EUR al FX daily yfinance (EURCCY=X, auto_adjust) del giorno di ogni acquisto, vendite pro-quota; componente cambio = pool in valuta x FX di oggi - pool in EUR. Differisce da nav_history.final_cost_basis_eur (la' le vendite tolgono il costo al FX del giorno della vendita e le posizioni chiuse restano nel costo)", 'Cost = EUR pool at daily yfinance FX (EURCCY=X, auto_adjust) on each acquisition date, sales deducted pro rata; FX component = native currency pool x current FX - EUR pool. Differs from nav_history.final_cost_basis_eur (there sales remove cost at sale-date FX and closed positions remain in cost)')),
            "fx_serie": {"start": start, "end_escluso": end} if estere else None}


def _compute_irr(trades: List[Dict[str, Any]], current_nav_eur: float,
                  fx_lookup, today_iso: str) -> Optional[float]:
    """Money-Weighted Return (annualized IRR) via Newton-Raphson / bisection.
    Treats:
      - BUY/ADD as outflows (negative cashflow at trade date)
      - SELL/TRIM as inflows (positive cashflow at trade date)
      - DIVIDEND as inflow (positive cashflow at trade date)
      - current NAV as inflow at t=0 (today) = "if I liquidated today"
    Returns annualized IRR (e.g. 0.105 = +10.5% per anno).
    """
    if not trades:
        return None
    try:
        from scipy.optimize import brentq
    except Exception:
        return None

    # Build cashflows: (years_back_from_today, eur_amount)
    today = datetime.fromisoformat(today_iso) if "T" in today_iso else datetime.strptime(today_iso, "%Y-%m-%d")
    cfs: List[Tuple[float, float]] = []
    for t in trades:
        d_str = (t["data"] or "")[:10]
        if not d_str:
            continue
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d")
        except Exception:
            continue
        years_back = (today - d).days / 365.25
        action = (t.get("action") or "").upper()
        qty = float(t.get("quantita") or 0)
        price = float(t.get("prezzo") or 0)
        ticker = t.get("ticker") or "?"
        etichetta_valuta = _currency_label(ticker, t.get("valuta"))
        ccy = etichetta_valuta.valore
        if ccy is None:
            return None
        fx = fx_lookup(d_str, ccy)
        if fx is None:
            return None
        amount_eur = qty * price * fx
        if action in ("BUY", "ADD"):
            cfs.append((years_back, -amount_eur))  # outflow
        elif action in ("SELL", "TRIM"):
            cfs.append((years_back, amount_eur))   # inflow
        elif action == "DIVIDEND":
            cfs.append((years_back, amount_eur))   # inflow (already EUR by convention)

    if not cfs:
        return None

    def npv(rate: float) -> float:
        total = current_nav_eur  # liquidation today (t=0)
        for years_back, cf in cfs:
            total += cf / (1.0 + rate) ** years_back
        return total

    try:
        # Bisection over reasonable range
        irr = brentq(npv, -0.95, 10.0, xtol=1e-6, maxiter=200)
        if not np.isfinite(irr):
            return None
        return float(irr)
    except (ValueError, RuntimeError):
        return None


def _dividend_income_at(trades: List[Dict[str, Any]], iso_date: str) -> float:
    """Cumulative dividend income EUR up to iso_date.
    Convention: DIVIDEND records have qty=shares, prezzo=EUR_per_share, valuta=EUR."""
    total = 0.0
    for t in trades:
        d = (t["data"] or "")[:10]
        if d > iso_date:
            break
        if (t.get("action") or "").upper() != "DIVIDEND":
            continue
        qty = float(t.get("quantita") or 0)
        per_share = float(t.get("prezzo") or 0)
        total += qty * per_share  # already EUR by convention
    return total


def _valid_iso(s) -> bool:
    """True se s inizia con una data valida YYYY-MM-DD."""
    try:
        datetime.strptime((s or "")[:10], "%Y-%m-%d")
        return True
    except Exception:
        return False


def _iso_safe(ts) -> str:
    """Formatta come 'YYYY-MM-DD' SENZA C-strftime (che su Windows lancia OSError
    [Errno 22] per date fuori range, es. pre-1970). Robusto per Timestamp/datetime/NaT."""
    try:
        y = ts.year
        if y != y:  # NaT/NaN
            return ""
        return "%04d-%02d-%02d" % (int(y), int(ts.month), int(ts.day))
    except Exception:
        s = str(ts)
        return s[:10] if (s and s[0:1].isdigit()) else ""


def _opening_positions():
    """Explicit baseline dependency, separate from the trade-only history reader."""
    return MemoryDB().get_opening_positions()


def compute_nav_history(start_date: Optional[str] = None,
                         end_date: Optional[str] = None,
                         force: bool = False) -> Dict[str, Any]:
    """Ricostruisce il NAV history dal primo trade in trade_history ad oggi.

    Returns:
      {
        "dates": [iso strings],
        "nav_eur": [floats],          # market value EUR (qty * close * FX)
        "cost_basis_eur": [floats],   # cumulativo capitale investito EUR
        "pnl_eur": [floats],          # nav - cost_basis (unrealized P/L)
        "cash_eur": float,             # cash costante (da portfolio.json)
        "cash_source": "portfolio.json" | None,   # F43(1): None = lo 0 e' un buco di lettura
        "cash_source_note": str | None,           # il motivo del buco
        "nav_total_eur": [floats],     # market + cash (info only)
        "first_trade_date": "YYYY-MM-DD",
        "tickers": [...],
        "valorizzati_al_costo": [{ticker, n_giorni, primo, ultimo}] | None,
                                  # F43(4): punti-proxy (nessun prezzo -> la
                                  # posizione vale il COSTO); ultimo==dates[-1]
                                  # = anche il punto di oggi e' proxy
        "n_days": int,
      }
    """
    openings = _opening_positions()
    if openings:
        return {"error_code": "OPENING_HISTORY_INCOMPLETE",
                "error": _message('Saldi iniziali documentati senza storia degli acquisti: NAV passato non ricostruibile dai soli trade. La performance usa gli snapshot successivi alla registrazione dei saldi.', 'Documented opening balances without acquisition history: past NAV cannot be reconstructed from trades alone. Performance uses snapshots after the balances were registered.'),
                "position_openings": openings,
                "baseline_added_at": max(row["created_at"] for row in openings),
                "timestamp": datetime.now().isoformat()}
    if not (NUMPY_OK and YF_OK):
        return {"error": _message('numpy/yfinance non disponibili', 'numpy/yfinance not available'),
                "timestamp": datetime.now().isoformat()}

    # Il negozio PRIMA della cache e del DB: la serie del NAV scala sul perimetro scaricato,
    # e con `ffill().bfill()` una sola candela di un simbolo che non doveva essere scaricato
    # si propagherebbe su TUTTA la storia. Meglio fermarsi che rendere un NAV diverso.
    _prezzi = prezzi_speciali()
    _ko = ko_negozio_prezzi(_prezzi)
    if _ko:
        return _ko
    salta = _prezzi["prezzi"]["senza_yfinance"]

    cache_key = f"nav_history:{start_date}:{end_date}"
    if not force and cache_key in _ANALYTICS_CACHE:
        entry = _ANALYTICS_CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return render_payload(entry["data"])

    trades = _trade_history()
    if not trades:
        return {"error": _message('trade_history empty: importa i trade (pagina Cassa/Trade, o tools/ops/importa_trade_csv.py).', 'Empty trade_history: import trades (Cash/Trade page, or tools/ops/importa_trade_csv.py).'),
                "timestamp": datetime.now().isoformat()}

    timeline = _build_position_timeline(trades)
    tickers = sorted(timeline.keys())
    currency_labels = _currency_labels_for_tickers(tickers, trades)
    non_determinate = [x for x in currency_labels.values() if x.valore is None]
    if non_determinate:
        return {"error": _message("valuta non determinabile: {reasons}", "Cannot determine currency: {reasons}", reasons=join_messages("; ", (x.dichiarazione for x in non_determinate))),
                "currency_labels": {k: v.as_dict() for k, v in currency_labels.items()},
                "timestamp": datetime.now().isoformat()}
    ccy_of = {ticker: label.valore for ticker, label in currency_labels.items()}
    if start_date is None:
        start_date = (trades[0].get("data") or "")[:10]
    # 205-B: un 'data' malformato nel primo trade -> start vuoto -> yfinance ancora all'epoca
    # 1970 -> date assurde -> crash a valle. Se non valido, prendi la prima data buona.
    if not _valid_iso(start_date):
        start_date = next(((t.get("data") or "")[:10] for t in trades
                           if _valid_iso(t.get("data"))), None)
        if not start_date:
            return {"error": _message('nessuna data trade valida (YYYY-MM-DD) in trade_history', 'No valid trade date (YYYY-MM-DD) in trade_history'),
                    "timestamp": datetime.now().isoformat()}
    if end_date is None:
        end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    _log(f"NAV history: {len(tickers)} tickers, {start_date} -> {end_date}")

    try:
        prices = _download_prices_for_history(tickers, start_date, end_date, salta)
    except Exception as e:
        return {"error": _message("alias yfinance non risolvibile: {error}", "Cannot resolve yfinance alias: {error}", error=error_text(e)),
                "timestamp": datetime.now().isoformat()}
    if prices is None or prices.empty:
        return {"error": _message('download prezzi fallito', 'failed to download prices'),
                "timestamp": datetime.now().isoformat()}

    # FX history
    currencies = sorted(set(ccy_of.values()))
    fx = _build_fx_history(currencies, start_date, end_date)
    # Un cambio di oggi (o il fallback statico) non misura il cambio alla data
    # storica. Se manca la serie di una valuta, l'intera ricostruzione e' n.d.
    _fx_missing = [
        c for c in currencies if c != "EUR" and
        (fx is None or fx.empty or c not in fx.columns or fx[c].dropna().empty)
    ]
    if _fx_missing:
        return {"error": _message("FX storico n.d. per: {currencies}", "Historical FX unavailable for: {currencies}", currencies=", ".join(_fx_missing)),
                "timestamp": datetime.now().isoformat()}

    # Helper: FX lookup at date d for currency ccy
    def fx_lookup(d_iso: str, ccy: str) -> float:
        ccy = ccy.upper()
        if ccy == "EUR":
            return 1.0
        if fx is None or fx.empty or ccy not in fx.columns:
            return None
        # Find closest date <= d_iso
        try:
            d_ts = pd.Timestamp(d_iso)
            sub = fx[ccy].dropna()
            older = sub[sub.index <= d_ts]
            if len(older):
                return float(older.iloc[-1])
            # F-CONT-1 (riallineamento 23/07, audit/20): data PRIMA della prima
            # osservazione FX — caso REALE: i trade di apertura del book sono di
            # un giorno non negoziato, mentre la prima osservazione FX e' il giorno
            # lavorativo seguente: il costo cadeva ZITTO sull'FX corrente e variava
            # col cambio di oggi. Ora: PRIMA osservazione disponibile —
            # deterministico e stabile run-to-run.
            newer = sub[sub.index > d_ts]
            if len(newer):
                return float(newer.iloc[0])
        except Exception:
            pass
        return None

    # Build timeline from BUY/SELL/ADD/TRIM only (ignore DIVIDEND for position qty)
    non_div_trades = [t for t in trades if (t.get("action") or "").upper() != "DIVIDEND"]
    timeline = _build_position_timeline(non_div_trades)
    tickers = sorted(timeline.keys())

    # Per ogni giorno calcola NAV market value + cost basis cumulato + pnl + dividend income
    date_index = prices.index
    # 205-B-fix: scarta NaT e date spurie pre-primo-trade. Bastava UNA data fuori range
    # (es. epoca 1970 da un download anomalo) per far crashare strftime su Windows con
    # [Errno 22], azzerando TUTTA la ricostruzione (Perf+Dash senza storico).
    try:
        _floor = pd.Timestamp(start_date)
        date_index = date_index[date_index.notna() & (date_index >= _floor)]
    except Exception:
        try:
            date_index = date_index[date_index.notna()]
        except Exception:
            pass
    if len(date_index) == 0:
        return {"error": _message('nessuna data valida nel range (prezzi/date anomali)', 'No valid date in range (anomalous prices/dates)'),
                "timestamp": datetime.now().isoformat()}
    nav_series: List[float] = []
    cost_basis_series: List[float] = []
    pnl_series: List[float] = []
    dividend_series: List[float] = []   # cumulative dividends EUR
    realized_sales_series: List[float] = []  # cumulative realized P/L da vendite (bugfix #164)
    total_return_series: List[float] = []  # (nav - cb + divs + realized) EUR
    # F43(4) 31/08: registro dei punti-proxy — quando il 205-fix valorizza un
    # ticker al COSTO (nessun prezzo quel giorno), qui si annota chi e quando;
    # esce nel payload come `valorizzati_al_costo` (pattern `stale_positions`)
    al_costo: Dict[str, Dict[str, Any]] = {}
    for ts in date_index:
        iso = _iso_safe(ts)
        nav_day = 0.0
        for t in tickers:
            tline = timeline.get(t, [])
            qty = _qty_at(tline, iso)
            if qty <= 0:
                continue
            px_ok = False
            px = None
            if t in prices.columns:
                try:
                    px = float(prices.at[ts, t])
                    px_ok = bool(np.isfinite(px) and px > 0)
                except Exception:
                    px_ok = False
            if px_ok:
                ccy = ccy_of[t]
                fx_rate = fx_lookup(iso, ccy)
                if fx_rate is None or not np.isfinite(fx_rate) or fx_rate <= 0:
                    return {"error": _message("FX {ccy} n.d. per {ticker} al {date}: NAV storico n.d.", "FX {ccy} unavailable for {ticker} on {date}: historical NAV unavailable", ccy=ccy, ticker=t, date=iso),
                            "currency_labels": {k: v.as_dict()
                                                for k, v in currency_labels.items()},
                            "timestamp": datetime.now().isoformat()}
                nav_day += qty * px * fx_rate
            else:
                # 205-fix: nessun prezzo per questo nome -> vale il suo COSTO (P&L 0),
                # non zero: niente piu' tuffi finti che avvelenano TWR e MaxDD.
                try:
                    nav_day += _ticker_cost_value_at(trades, t, iso, fx_lookup)
                except ValueError as e:
                    return {"error": _message("{error}: NAV storico n.d.", "{error}: historical NAV unavailable", error=error_text(e)),
                            "currency_labels": {k: v.as_dict()
                                                for k, v in currency_labels.items()},
                            "timestamp": datetime.now().isoformat()}
                _reg = al_costo.setdefault(t, {"ticker": t, "n_giorni": 0,
                                               "primo": iso, "ultimo": iso})
                _reg["n_giorni"] += 1
                _reg["ultimo"] = iso
        # F-CONT-1 (23/07): FX STORICO esplicito — la serie ufficiale non balla
        # piu' col cambio del giorno (use_current_fx resta solo per confronti live).
        try:
            cost_basis_day, realized_day = _basis_and_realized_at(
                trades, iso, fx_lookup, use_current_fx=False)
        except ValueError as e:
            return {"error": _message("{error}: NAV storico n.d.", "{error}: historical NAV unavailable", error=error_text(e)),
                    "currency_labels": {k: v.as_dict()
                                        for k, v in currency_labels.items()},
                    "timestamp": datetime.now().isoformat()}
        div_day = _dividend_income_at(trades, iso)
        pnl_day = nav_day - cost_basis_day
        total_return_day = pnl_day + div_day + realized_day
        nav_series.append(round(nav_day, 2))
        cost_basis_series.append(round(cost_basis_day, 2))
        pnl_series.append(round(pnl_day, 2))
        dividend_series.append(round(div_day, 2))
        realized_sales_series.append(round(realized_day, 2))
        total_return_series.append(round(total_return_day, 2))

    # Cash from portfolio.json — F43(1) 27/08: lettore unico ancorato alla
    # radice del repo (prima: path relativo + except pass → cassa 0 zitta e
    # nav_total_eur == nav_eur senza dirlo; descritto dalla chat frontend
    # leggendo queste righe, ponte F43 (1))
    from bellomberg.storage.memory_db import leggi_cassa_portfolio
    _cassa = leggi_cassa_portfolio()
    cash_eur = _cassa["cash_eur"]

    nav_total = [round(n + cash_eur, 2) for n in nav_series]
    dates_iso = [_iso_safe(ts) for ts in date_index]

    # Total P/L summary
    final_nav = nav_series[-1] if nav_series else 0
    final_cb  = cost_basis_series[-1] if cost_basis_series else 0
    final_pnl = pnl_series[-1] if pnl_series else 0
    final_div = dividend_series[-1] if dividend_series else 0
    final_realized = realized_sales_series[-1] if realized_sales_series else 0
    final_tr  = total_return_series[-1] if total_return_series else 0
    pnl_pct_total = (final_pnl / final_cb * 100) if final_cb > 0 else 0
    total_return_pct = (final_tr / final_cb * 100) if final_cb > 0 else 0

    # MWR / IRR (annualized)
    last_iso = dates_iso[-1] if dates_iso else datetime.now().strftime("%Y-%m-%d")
    irr_annual = _compute_irr(trades, final_nav, fx_lookup, last_iso)

    result = {
        "dates": dates_iso,
        "nav_eur": nav_series,
        "cost_basis_eur": cost_basis_series,
        "pnl_eur": pnl_series,
        "dividend_income_eur": dividend_series,
        "realized_sales_eur": realized_sales_series,
        "total_return_eur": total_return_series,
        "cash_eur": cash_eur,
        "cash_source": _cassa["cash_source"],   # F43(1): "portfolio.json" | None (0 = buco)
        "cash_source_note": _cassa["cash_source_note"],
        "nav_total_eur": nav_total,
        "first_trade_date": trades[0]["data"][:10],
        "tickers": tickers,
        "currency_labels": {k: v.as_dict() for k, v in currency_labels.items()},
        # F43(4): i punti-proxy dichiarati — `ultimo` == dates[-1] significa che
        # anche il punto di OGGI e' al costo, non a mercato. None = serie pulita.
        "valorizzati_al_costo": (sorted(al_costo.values(), key=lambda r: r["ticker"])
                                 or None),
        "n_days": len(dates_iso),
        "final_nav_eur": round(final_nav, 2),
        "final_cost_basis_eur": round(final_cb, 2),
        "final_pnl_eur": round(final_pnl, 2),
        "final_pnl_pct": round(pnl_pct_total, 2),
        "final_dividend_income_eur": round(final_div, 2),
        "final_realized_sales_eur": round(final_realized, 2),
        "final_total_return_eur": round(final_tr, 2),
        "final_total_return_pct": round(total_return_pct, 2),
        "irr_annual_pct": round(irr_annual * 100, 2) if irr_annual is not None else None,
        "timestamp": datetime.now().isoformat(),
    }
    _ANALYTICS_CACHE[cache_key] = {"ts": time.time(), "data": result}
    _log(f"NAV history done: {len(dates_iso)} days, "
         f"final NAV = EUR {nav_total[-1]:,.0f}")
    return render_payload(result)


# ============================================================
# DRAWDOWN ANALYTICS
# ============================================================

def compute_drawdowns(force: bool = False) -> Dict[str, Any]:
    """Top-5 drawdowns storici basati sul P/L mark-to-market ratio (cumulative TWR proxy).
    Usa pnl_eur/cost_basis_eur per ogni giorno -> drawdown ESCLUDE l'effetto degli apporti
    di capitale (che gonfierebbero artificialmente il NAV total).

    Esempio: se il 20 marzo P/L era -€13k su cost basis €131k -> ratio -10.22% (vero DD).
    Senza questa correzione il DD apparirebbe -2% perche' il NAV total (con cash + nuovi
    apporti) salirebbe comunque mascherando le perdite.
    """
    nav_hist = compute_nav_history(force=force)
    if nav_hist.get("error"):
        return nav_hist

    dates = nav_hist["dates"]
    pnl_series = nav_hist.get("pnl_eur") or []
    cb_series  = nav_hist.get("cost_basis_eur") or []
    if not pnl_series or not cb_series:
        return {"error": _message('serie pnl/cost_basis assenti', 'missing pnl/cost_basis series'), "n_days": 0}

    # Cumulative return ratio = 1 + (pnl / cost_basis). At t=0 con CB=0 usiamo 1.0.
    # Questo e' il "growth of EUR1" che esclude l'effetto cashflow.
    ratio = np.array([
        (1.0 + (pnl_series[i] / cb_series[i])) if cb_series[i] > 0 else 1.0
        for i in range(len(pnl_series))
    ], dtype=float)
    nav = ratio  # use ratio in place of NAV for DD calculation
    if len(nav) < 5:
        return {"error": _message('storico insufficiente (servono >=5 giorni)', 'insufficient history (need >=5 days)'),
                "n_days": len(nav)}

    # Running max
    running_max = np.maximum.accumulate(nav)
    dd_pct = (nav - running_max) / running_max  # negative or zero

    # Detect drawdown episodes: start when nav drops below running max, end when nav reaches new max
    episodes: List[Dict[str, Any]] = []
    in_dd = False
    peak_idx = 0
    trough_idx = 0
    trough_val = 0.0
    for i in range(1, len(nav)):
        if not in_dd:
            if nav[i] < running_max[i]:
                in_dd = True
                peak_idx = i - 1  # peak was day before
                trough_idx = i
                trough_val = nav[i]
        else:
            if nav[i] < trough_val:
                trough_idx = i
                trough_val = nav[i]
            if nav[i] >= running_max[peak_idx]:
                # Recovery
                episodes.append({
                    "peak_date": dates[peak_idx],
                    "peak_nav": float(running_max[peak_idx]),
                    "trough_date": dates[trough_idx],
                    "trough_nav": float(trough_val),
                    "recovery_date": dates[i],
                    "depth_pct": round((trough_val - running_max[peak_idx]) / running_max[peak_idx] * 100, 2),
                    "duration_to_trough_days": trough_idx - peak_idx,
                    "recovery_days": i - trough_idx,
                    "total_days": i - peak_idx,
                    "recovered": True,
                })
                in_dd = False

    # If still in drawdown at end of series
    current_dd = None
    if in_dd:
        current_dd = {
            "peak_date": dates[peak_idx],
            "peak_nav": float(running_max[peak_idx]),
            "trough_date": dates[trough_idx],
            "trough_nav": float(trough_val),
            "current_date": dates[-1],
            "current_nav": float(nav[-1]),
            "depth_from_peak_pct": round((nav[-1] - running_max[peak_idx]) / running_max[peak_idx] * 100, 2),
            "max_depth_pct": round((trough_val - running_max[peak_idx]) / running_max[peak_idx] * 100, 2),
            "days_since_peak": len(nav) - 1 - peak_idx,
            "days_in_dd": len(nav) - 1 - trough_idx,
            "recovered": False,
        }

    # Sort historical episodes by depth (most negative first)
    episodes.sort(key=lambda e: e["depth_pct"])
    top5 = episodes[:5]

    return {
        "n_episodes_total": len(episodes),
        "top_5_drawdowns": top5,
        "current_drawdown": current_dd,
        "max_drawdown_pct": round(float(dd_pct.min()) * 100, 2),
        "avg_drawdown_pct": round(float(dd_pct[dd_pct < 0].mean()) * 100, 2) if (dd_pct < 0).any() else 0.0,
        "pain_index": round(float(-dd_pct.mean()) * 100, 4),  # Becker's pain index (avg DD over series)
        "n_days_analyzed": len(nav),
        "first_date": dates[0],
        "last_date": dates[-1],
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# LIQUIDITY SCORES
# ============================================================

def compute_liquidity_scores() -> Dict[str, Any]:
    """Per ogni posizione: avg daily volume (20d) * price -> daily $ volume.
    days_to_liquidate = position_eur / (LIQ_VOL_FRACTION * daily_eur_volume).
    Green ≤1d, yellow ≤5d, red >5d.
    """
    if not YF_OK:
        return {"error": _message('yfinance non disponibile', 'yfinance not available')}

    # Qui il negozio assente NON sposta un numero in euro (lo skip e' un'ETICHETTA per
    # posizione): si calcola e lo si DICHIARA nel payload, invece di fermare la pagina.
    _prezzi = prezzi_speciali()
    salta = _prezzi["prezzi"]["senza_yfinance"]

    db = MemoryDB()
    snap = db.get_portfolio_summary()
    positions = snap.get("positions", [])
    if not positions:
        return {"error": _message("nessuna posizione", "no positions")}

    from bellomberg.cli.price_updater import get_fx_to_eur

    items = []
    for p in positions:
        ticker = p["ticker"]
        etichetta_valuta = _currency_label(ticker, p.get("valuta"))
        if etichetta_valuta.valore is None:
            items.append({
                "ticker": ticker,
                "days_to_liquidate": None,
                "score": "unknown",
                "reason": etichetta_valuta.dichiarazione,
                "currency": None,
                "currency_label": etichetta_valuta.as_dict(),
                "position_eur": p.get("valore_mercato"),
            })
            continue
        if ticker in salta:
            items.append({
                "ticker": ticker,
                "days_to_liquidate": None,
                "score": "skip",
                "reason": _message('non disponibile su yfinance (crypto personalizzata)', 'not on yfinance (crypto custom)'),
                "position_eur": p.get("valore_mercato"),
            })
            continue
        try:
            from bellomberg.cli.price_updater import data_ticker as _dt
            hist = yf.Ticker(_dt(ticker)).history(period="1mo")
            if hist.empty:
                items.append({
                    "ticker": ticker,
                    "days_to_liquidate": None,
                    "score": "unknown",
                    "reason": _message("storico yfinance assente", "no yfinance history"),
                    "position_eur": p.get("valore_mercato"),
                })
                continue
            avg_vol = float(hist["Volume"].mean())  # shares
            avg_close = float(hist["Close"].mean())  # native currency
            ccy = etichetta_valuta.valore
            fx = get_fx_to_eur(ccy)
            if fx is None:
                items.append({
                    "ticker": ticker,
                    "days_to_liquidate": None,
                    "score": "unknown",
                    "reason": _message("FX {ccy} n.d.: liquidita' in EUR n.d.", "FX {ccy} unavailable: EUR liquidity unavailable", ccy=ccy),
                    "currency": ccy,
                    "currency_label": etichetta_valuta.as_dict(),
                    "position_eur": p.get("valore_mercato"),
                })
                continue
            daily_eur_vol = avg_vol * avg_close * fx
            position_eur = float(p.get("valore_mercato") or 0)
            if daily_eur_vol <= 0:
                days = None
                score = "unknown"
            else:
                days = position_eur / (LIQ_VOL_FRACTION * daily_eur_vol)
                if days <= LIQ_GREEN_DAYS:
                    score = "green"
                elif days <= LIQ_YELLOW_DAYS:
                    score = "yellow"
                else:
                    score = "red"
            items.append({
                "ticker": ticker,
                "position_eur": round(position_eur, 0),
                "avg_daily_volume_shares": round(avg_vol, 0),
                "avg_daily_volume_eur": round(daily_eur_vol, 0),
                "days_to_liquidate": round(days, 2) if days is not None else None,
                "score": score,
                "currency": ccy,
                "currency_label": etichetta_valuta.as_dict(),
            })
        except Exception as e:
            items.append({
                "ticker": ticker,
                "days_to_liquidate": None,
                "score": "error",
                "reason": str(e)[:80],
                "position_eur": p.get("valore_mercato"),
            })

    # Sort by days descending (worst liquidity first)
    items.sort(key=lambda x: (x.get("days_to_liquidate") or -1), reverse=True)

    n_red = sum(1 for it in items if it["score"] == "red")
    n_yellow = sum(1 for it in items if it["score"] == "yellow")
    n_green = sum(1 for it in items if it["score"] == "green")

    return {
        "items": items,
        "n_green": n_green,
        "n_yellow": n_yellow,
        "n_red": n_red,
        "threshold_green_days": LIQ_GREEN_DAYS,
        "threshold_yellow_days": LIQ_YELLOW_DAYS,
        "assumption_pct_of_volume": LIQ_VOL_FRACTION,
        "note": _message("I giorni assumono vendite massime del 20% del volume medio giornaliero a 20 giorni. Gli ETF .MI possono risultare sottostimati per la liquidità dei market maker esterna a yfinance.{warning}", "Days assume you sell max 20% of avg 20d daily volume. ETF .MI may be understated due to market-maker liquidity outside yfinance.{warning}", warning="" if _prezzi["origine"] not in ("assente", "illeggibile") else _message(" ATTENZIONE: negozio dei prezzi speciali {origin} ({reason}): nessuna posizione e' stata etichettata «skip», anche se ce ne fossero da saltare.", " WARNING: special price store {origin} ({reason}): no position was labeled skip, even if some should be skipped.", origin=_prezzi["origine"], reason=_prezzi["motivo"])),
        "negozio_prezzi": {"origine": _prezzi["origine"], "motivo": _prezzi["motivo"]},
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# CONCENTRATION METRICS (HHI)
# ============================================================

def compute_concentration() -> Dict[str, Any]:
    """HHI by ticker, region, currency. Top-5 concentration % e Effective N."""
    db = MemoryDB()
    snap = db.get_portfolio_summary()
    positions = snap.get("positions", [])
    if not positions:
        return {"error": _message("nessuna posizione", "no positions")}

    total = sum(p.get("valore_mercato") or 0 for p in positions)
    if total <= 0:
        return {"error": _message("NAV nullo", "zero NAV")}

    # By ticker
    weights = [(p["ticker"], (p.get("valore_mercato") or 0) / total) for p in positions]
    weights.sort(key=lambda x: x[1], reverse=True)

    hhi_ticker = sum(w * w for _, w in weights) * 10000  # in basis points (max 10000)
    top5_concentration_pct = sum(w for _, w in weights[:5]) * 100
    effective_n = 1.0 / sum(w * w for _, w in weights) if weights else 0

    # By region
    region_weights: Dict[str, float] = {}
    for ticker, w in weights:
        r = _region_of(ticker)
        region_weights[r] = region_weights.get(r, 0) + w
    hhi_region = sum(w * w for w in region_weights.values()) * 10000

    # By currency
    ccy_weights: Dict[str, float] = {}
    currency_labels = {}
    positions_by_ticker = {p["ticker"]: p for p in positions}
    for ticker, w in weights:
        etichetta_valuta = _currency_label(ticker, positions_by_ticker[ticker].get("valuta"))
        currency_labels[ticker] = etichetta_valuta
        c = etichetta_valuta.valore or "n.d."
        ccy_weights[c] = ccy_weights.get(c, 0) + w
    hhi_currency = sum(w * w for w in ccy_weights.values()) * 10000

    def classify_hhi(hhi):
        if hhi < 1500: return "diversified"
        if hhi < 2500: return "moderate"
        return "concentrated"

    return {
        "by_ticker": {
            "hhi": round(hhi_ticker, 0),
            "classification": classify_hhi(hhi_ticker),
            "effective_n": round(effective_n, 2),
            "top_5_pct": round(top5_concentration_pct, 1),
            "top_holdings": [{"ticker": t, "weight_pct": round(w * 100, 2)}
                              for t, w in weights[:10]],
        },
        "by_region": {
            "hhi": round(hhi_region, 0),
            "classification": classify_hhi(hhi_region),
            "weights_pct": {r: round(w * 100, 1) for r, w in
                             sorted(region_weights.items(), key=lambda kv: -kv[1])},
        },
        "by_currency": {
            "hhi": round(hhi_currency, 0),
            "classification": classify_hhi(hhi_currency),
            "weights_pct": {c: round(w * 100, 1) for c, w in
                             sorted(ccy_weights.items(), key=lambda kv: -kv[1])},
        },
        "currency_labels": {k: v.as_dict() for k, v in currency_labels.items()},
        "interpretation": {
            "diversified": _message('HHI < 1500 = ben diversificato', 'HHI < 1500 = well diversified'),
            "moderate": _message('1500 ≤ HHI < 2500 = concentrazione moderata', '1500 ≤ HHI < 2500 = moderate concentration'),
            "concentrated": _message('HHI >= 2500 = concentrato', 'HHI >= 2500 = concentrated'),
        },
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# VAR CONTRIBUTION (Component VaR, Jorion 2006)
# ============================================================

def compute_var_contribution(lookback_days: int = 252,
                              confidence: float = 0.05) -> Dict[str, Any]:
    """Component VaR per ticker. Sum of components = portfolio VaR.
    Methodology:
      VaR_p = z_alpha * sigma_p * NAV
      Component VaR_i = w_i * cov(r_i, r_p) / sigma_p * z_alpha * NAV
    where cov(r_i, r_p) = sum_j w_j * Cov[r_i, r_j]
    """
    if not (NUMPY_OK and YF_OK):
        return {"error": _message('numpy/yfinance non disponibili', 'numpy/yfinance not available')}

    # Il negozio PRIMA del DB: i numeri in euro scalano sul NAV COPERTO (`total_covered`) e
    # un simbolo che rientra nel panel con una serie parziale tronca il campione di TUTTI
    # (il dropna(how="any") piu' sotto), fino a spegnere l'endpoint per l'intero book.
    _prezzi = prezzi_speciali()
    _ko = ko_negozio_prezzi(_prezzi)
    if _ko:
        return _ko
    salta = _prezzi["prezzi"]["senza_yfinance"]

    db = MemoryDB()
    snap = db.get_portfolio_summary()
    positions = snap.get("positions", [])
    if not positions:
        return {"error": _message("nessuna posizione", "no positions")}

    total = sum(p.get("valore_mercato") or 0 for p in positions)
    if total <= 0:
        return {"error": _message("NAV nullo", "zero NAV")}

    tickers = [p["ticker"] for p in positions if p["ticker"] not in salta]
    weights_map = {p["ticker"]: (p.get("valore_mercato") or 0) / total for p in positions}

    if len(tickers) < 2:
        return {"error": _message('servono almeno 2 ticker per il contributo VaR', 'need at least 2 tickers for VaR contribution')}

    period = f"{lookback_days}d"
    try:
        # audit/11 §4: stesso alias broker->dati degli altri downloader del file (prima
        # un ticker solo-alias scaricava vuoto e il dropna(how='any') svuotava il panel)
        try:
            from bellomberg.cli.price_updater import data_ticker as _dt_alias
            dl_map = {t: _dt_alias(t) for t in tickers}
        except Exception:
            dl_map = {t: t for t in tickers}
        raw = yf.download(list(dl_map.values()), period=period, progress=False,
                          auto_adjust=True, threads=True)
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"]
        else:
            close = raw["Close"] if "Close" in raw.columns else raw
            if isinstance(close, pd.Series):
                close = close.to_frame(list(dl_map.values())[0])
        close = close.rename(columns={v: k for k, v in dl_map.items()})
        close = close.dropna(axis=1, how="all")  # una colonna morta non svuota il panel
        returns = close.pct_change().dropna(how="any")
        if len(returns) < 60:
            return {"error": _message('storico insufficiente: {v0} osservazioni', 'insufficient history: {v0} obs', v0=len(returns))}
    except Exception as e:
        return {"error": _message('Download fallito: {v0}', 'download failed: {v0}', v0=e)}

    available = [t for t in tickers if t in returns.columns]
    R = returns[available].values
    w = np.array([weights_map[t] for t in available])
    w = w / w.sum()
    # review quant 22/07 (E8): perimetro e campione DICHIARATI; i numeri EUR
    # scalano sul NAV COPERTO dai ticker modellati, non sul totale del book
    # (prima il pct del sotto-portafoglio veniva applicato al NAV pieno).
    excluded = [t for t in tickers if t not in available]
    skipped_pos = [p["ticker"] for p in positions if p["ticker"] in salta]
    coverage = float(sum(weights_map[t] for t in available))
    total_covered = coverage * total
    Sigma = np.cov(R.T) * 252
    var_p = float(w @ Sigma @ w)
    sigma_p = float(np.sqrt(var_p))
    if sigma_p <= 0:
        return {"error": _message('volatilità di portafoglio nulla', 'zero portfolio volatility')}
    from scipy.stats import norm
    z_alpha = float(-norm.ppf(confidence))
    sigma_p_daily = sigma_p / np.sqrt(252)
    VaR_p_pct = z_alpha * sigma_p_daily * 100
    VaR_p_eur = VaR_p_pct / 100 * total_covered
    marginal_annual = Sigma @ w / sigma_p
    component_annual = w * marginal_annual
    component_daily_pct = z_alpha * component_annual / np.sqrt(252) * 100
    component_eur = component_daily_pct / 100 * total_covered

    items = []
    for i, t in enumerate(available):
        items.append({
            "ticker": t,
            "weight_pct": round(float(w[i]) * 100, 2),
            "component_var_pct": round(float(component_daily_pct[i]), 4),
            "component_var_eur": round(float(component_eur[i]), 2),
            "contribution_pct_of_total_var": round(
                float(component_daily_pct[i]) / VaR_p_pct * 100, 2) if VaR_p_pct > 0 else 0.0,
            "marginal_var_pct_per_1pct_weight": round(
                float(marginal_annual[i]) * z_alpha / np.sqrt(252), 4),
        })
    items.sort(key=lambda x: x["contribution_pct_of_total_var"], reverse=True)

    return {
        "portfolio_var_pct_daily": round(VaR_p_pct, 2),
        "portfolio_var_eur_daily": round(VaR_p_eur, 0),
        "portfolio_vol_annual_pct": round(sigma_p * 100, 2),
        "confidence_level": confidence,
        "z_alpha": round(z_alpha, 3),
        "lookback_days": lookback_days,
        "n_assets": len(available),
        # review quant 22/07 (E8): campione e perimetro DICHIARATI (prima il
        # dropna(how='any') poteva tagliare il panel alla storia piu' corta
        # senza lasciare traccia nel payload)
        "n_obs_effective": int(len(returns)),
        "date_range": [str(returns.index[0].date()), str(returns.index[-1].date())],
        "excluded_tickers": excluded,
        "skipped_positions": skipped_pos,
        "coverage_nav_pct": round(coverage * 100, 1),
        "nav_covered_eur": round(total_covered, 0),
        "items": items,
        "methodology": (_message('Component VaR (Jorion 2006), gaussiano-parametrico su rendimenti in valuta LOCALE (FX escluso, dichiarato); numeri EUR sul NAV coperto dai ticker modellati. sum(component_var) = portfolio VaR.', 'Component VaR (Jorion 2006), Gaussian-parametric on LOCAL currency returns (FX excluded, as declared); EUR figures use NAV covered by modeled tickers. sum(component_var) = portfolio VaR.')),
        "timestamp": datetime.now().isoformat(),
    }


def invalidate_cache():
    _ANALYTICS_CACHE.clear()
