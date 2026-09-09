"""
BELLOMBERG - Live Price Updater

Aggiorna i prezzi nel database SQLite per tutte le posizioni attive.
Fonti (in priorita):
1. IBKR TWS (se aperto, port 7496/7497) - real-time o delayed
2. yfinance (sempre disponibile, delayed 15min su US e EU)
3. CoinGecko per i simboli dichiarati nel negozio privato dei prezzi speciali
   (data/prezzi_speciali.json, sezione `coingecko`; forma in prezzi_speciali.example.json)

Uso standalone:
    python price_updater.py              # un singolo update
    python price_updater.py --loop 60    # loop infinito ogni 60 secondi

Schedulazione automatica:
    Crea task Windows Task Scheduler che lancia "python price_updater.py" ogni 15 minuti.
"""
import sys
import time
import argparse
import threading
import math
from collections.abc import Mapping
from datetime import datetime
from typing import Dict

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from bellomberg.storage.memory_db import MemoryDB

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False




# === FX CONVERSION CACHE ===
_FX_CACHE = {}
_FX_CACHE_AT = {}
_FX_CACHE_TTL_SECONDS = 300
_FX_LOCK = threading.RLock()
_FX_SOURCE_LAST: Dict[str, str] = {}  # 'live' | 'fallback' per currency, per audit

# Fallback statici se yfinance fallisce (Maggio 2026, rough approximations).
# Ultima rete di sicurezza per evitare che FX None rompa il NAV.
_FX_FALLBACK_TO_EUR = {
    "USD": 0.92,
    "GBP": 1.17,
    "GBX": 0.0117,
    "CHF": 1.03,
    "JPY": 0.0058,
    "HKD": 0.118,
    "CNY": 0.127,
    "BRL": 0.17,
}


def get_fx_to_eur(currency):
    """Return the CURRENCY->EUR rate with a five-minute live cache."""
    with _FX_LOCK:
        if currency is None or not str(currency).strip():
            return None
        currency = str(currency).strip().upper()
        if currency == "EUR":
            _FX_SOURCE_LAST["EUR"] = "identity"
            return 1.0
        if currency == "GBX":
            gbp_eur = get_fx_to_eur("GBP")
            return gbp_eur / 100.0 if gbp_eur else _FX_FALLBACK_TO_EUR.get("GBX")
        now = time.monotonic()
        cached = _FX_CACHE.get(currency)
        if (cached is not None and math.isfinite(cached) and cached > 0
                and now - _FX_CACHE_AT.get(currency, float("-inf")) <= _FX_CACHE_TTL_SECONDS):
            _FX_SOURCE_LAST[currency] = "live"
            return _FX_CACHE[currency]
        _FX_CACHE.pop(currency, None)
        _FX_CACHE_AT.pop(currency, None)
        if YFINANCE_AVAILABLE:
            try:
                hist = yf.Ticker("EUR" + currency + "=X").history(period="1d")
                if hist is not None and len(hist) > 0:
                    rate_eur_to_x = float(hist["Close"].iloc[-1])
                    if math.isfinite(rate_eur_to_x) and rate_eur_to_x > 0:
                        _FX_CACHE[currency] = 1.0 / rate_eur_to_x
                        _FX_CACHE_AT[currency] = now
                        _FX_SOURCE_LAST[currency] = "live"
                        return _FX_CACHE[currency]
            except Exception:
                pass
        fb = _FX_FALLBACK_TO_EUR.get(currency)
        if fb is not None:
            _FX_SOURCE_LAST[currency] = "fallback"
            print(f"[FX] WARN: using FALLBACK {currency}->EUR={fb} (yfinance unreachable, "
                  "non cachato: retry live al prossimo giro)", flush=True)
            return fb
        _FX_SOURCE_LAST.pop(currency, None)
        return None

def get_fx_sources():
    """Returns {currency: 'live'|'fallback'} for currencies fetched this session."""
    return dict(_FX_SOURCE_LAST)


def fx_sources_for(currencies, rates):
    """Fonte DICHIARATA per ogni valuta richiesta (blocco cassa/valute 03/08,
    regola 14/07 sul percorso della cassa): da rendere accanto ai tassi.
      'live'     = yfinance in sessione (freschezza governata dal chiamante)
      'fallback' = tasso statico di maggio 2026, NON un cambio vivo
      'assente'  = nessun tasso in risposta (buco dichiarato, non omesso)
      'n.d.'     = tasso presente ma fonte non tracciata (imprevisto:
                   dichiarato invece che indovinato)
    GBX deriva da GBP/100 (v. get_fx_to_eur) e ne eredita la fonte.
    """
    out = {}
    for cur in currencies:
        cur_u = (cur or "").upper()
        if cur_u not in rates:
            out[cur_u] = "assente"
            continue
        chiave = "GBP" if cur_u == "GBX" else cur_u
        out[cur_u] = _FX_SOURCE_LAST.get(chiave, "n.d.")
    return out


def get_fx_to_eur_con_fonte(currency):
    """Resolve rate and source atomically, including EUR and GBX identity/source."""
    with _FX_LOCK:
        if currency is None or not str(currency).strip():
            return None, "assente"
        cur = str(currency).strip().upper()
        rate = get_fx_to_eur(cur)
        rates = {cur: rate} if rate is not None else {}
        return rate, fx_sources_for([cur], rates)[cur]

def convert_to_eur(amount, currency):
    """Converte amount da currency a EUR."""
    fx = get_fx_to_eur(currency)
    if fx is None:
        return None
    return amount * fx


# Conversioni pubbliche di simboli canonici crypto. Gli alias personali broker->Yahoo
# vivono invece in data/alias_fonti.json, sezione `yfinance`.
_YFINANCE_CANONICI = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
}


class AliasFontiError(RuntimeError):
    """Il negozio alias non consente una risoluzione affidabile."""


def _alias_yfinance():
    from bellomberg.storage.negozi_privati import carica_alias
    esito = carica_alias()
    if esito["origine"] in ("assente", "illeggibile"):
        raise AliasFontiError("alias_fonti %s: %s" % (
            esito["origine"], esito["motivo"] or "motivo n.d."))
    return esito["alias"]["yfinance"]


def data_ticker(t):
    """Ticker Yahoo: canonici pubblici, poi alias privato riletto a ogni chiamata."""
    ticker = str(t or "").strip().upper()
    if not ticker:
        raise ValueError("ticker vuoto: alias yfinance non risolvibile")
    if ticker in _YFINANCE_CANONICI:
        return _YFINANCE_CANONICI[ticker]
    alias = _alias_yfinance()
    if ticker.endswith(".FRA") and ticker not in alias:
        raise AliasFontiError("alias yfinance mancante per %s in alias_fonti" % ticker)
    return alias.get(ticker, ticker)


def data_ticker_map(tickers, *, riservati=()):
    """Congela `{ticker reale: ticker Yahoo}` una volta per download e rinomina."""
    reali = [str(t or "").strip().upper() for t in tickers]
    if any(not t for t in reali):
        raise ValueError("ticker vuoto: alias yfinance non risolvibile")
    if not reali:
        return {}
    non_canonici = [t for t in reali if t not in _YFINANCE_CANONICI]
    alias = _alias_yfinance() if non_canonici else {}
    mancanti = [t for t in reali if t.endswith(".FRA") and t not in alias]
    if mancanti:
        raise AliasFontiError("alias yfinance mancante per %s in alias_fonti" % mancanti[0])
    out = {t: _YFINANCE_CANONICI.get(t, alias.get(t, t)) for t in reali}
    riservati_norm = {str(t).strip().upper() for t in riservati}
    for reale, dato in out.items():
        if reale != dato and (dato in riservati_norm or reale in riservati_norm):
            raise AliasFontiError(
                "alias yfinance non valido: %s risolve in %s e coinvolge un simbolo riservato" %
                (reale, dato))
    inversa = {}
    for reale, dato in out.items():
        if dato in inversa and inversa[dato] != reale:
            raise AliasFontiError("alias yfinance ambiguo: %s e %s risolvono entrambi in %s" %
                                  (inversa[dato], reale, dato))
        inversa[dato] = reale
    return out


class _YFinanceProxyMap(Mapping):
    """Compatibilita' per gli import legacy: vista Mapping viva, mai uno snapshot."""

    def __getitem__(self, key):
        ticker = str(key or "").strip().upper()
        if ticker in _YFINANCE_CANONICI:
            return _YFINANCE_CANONICI[ticker]
        alias = _alias_yfinance()
        if ticker not in alias:
            if ticker.endswith(".FRA"):
                raise AliasFontiError(
                    "alias yfinance mancante per %s in alias_fonti" % ticker)
            raise KeyError(key)
        return alias[ticker]

    def __iter__(self):
        return iter({**_alias_yfinance(), **_YFINANCE_CANONICI})

    def __len__(self):
        return len({**_alias_yfinance(), **_YFINANCE_CANONICI})


YFINANCE_PROXY_MAP = _YFinanceProxyMap()


# La mappa simbolo -> id CoinGecko sta nel NEGOZIO PRIVATO dei prezzi speciali
# (negozi_privati.carica_prezzi_speciali: data/prezzi_speciali.json, sezione `coingecko`,
# forma in prezzi_speciali.example.json), RILETTA A OGNI GIRO. Negozio assente = nessun id:
# non e' «nessun simbolo da prezzare cosi'», ed e' per questo che _run_once lo DICHIARA nel
# log a ogni giro, fuori da `if verbose` (il task schedulato gira con --quiet).
def prezzi_speciali():
    """L'esito INTERO del negozio: {"prezzi": {...}, "origine": ..., "motivo": ...}."""
    from bellomberg.storage.negozi_privati import carica_prezzi_speciali
    return carica_prezzi_speciali()


def _fetch_yfinance(ticker, valuta_posizione=None):
    """Tenta yfinance. Ritorna prezzo, valuta e relativa etichetta, oppure None."""
    if not YFINANCE_AVAILABLE:
        return None
    yf_ticker = data_ticker(ticker)
    try:
        tk = yf.Ticker(yf_ticker)
        hist = tk.history(period="1d")
        if hist is not None and len(hist) > 0:
            price = float(hist['Close'].iloc[-1])
            from bellomberg.storage.classificazione import valuta
            etichetta_valuta = valuta(ticker, valuta_posizione=valuta_posizione)
            return (price, etichetta_valuta.valore, etichetta_valuta.as_dict())
    except Exception:
        return None
    return None


def _fetch_coingecko(symbol, mappa):
    """CoinGecko per i simboli del negozio privato (sezione `coingecko`).
    `mappa` arriva dal chiamante, letta una volta per giro: senza default, cosi' un punto
    di chiamata dimenticato e' un TypeError e non «nessun id» zitto."""
    if not REQUESTS_AVAILABLE:
        return None
    cg_id = mappa.get(symbol.upper())
    if not cg_id:
        return None
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if cg_id in data and "usd" in data[cg_id]:
            return (float(data[cg_id]["usd"]), "USD")
    except Exception:
        return None
    return None


def _fetch_ibkr(ticker, port=7496):
    """IBKR TWS se aperto. Best-effort."""
    try:
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        from ib_async import IB, Stock
    except ImportError:
        return None
    ib = IB()
    try:
        ib.connect("127.0.0.1", port, clientId=99, timeout=3, readonly=True)
        try:
            ib.reqMarketDataType(4)  # delayed-frozen (free)
        except Exception:
            pass
        # Per ticker US semplici
        if "." not in ticker and len(ticker) <= 5:
            stock = Stock(ticker, "SMART", "USD")
            ib.qualifyContracts(stock)
            t = ib.reqMktData(stock, "", snapshot=True)
            ib.sleep(2)
            mp = t.marketPrice()
            if mp and mp == mp:
                return (float(mp), "USD")
    except Exception:
        return None
    finally:
        try: ib.disconnect()
        except Exception: pass
    return None


def _fetch_polygon(ticker):
    """DISATTIVATA come fonte di prezzi LIVE (19/08, Opus 5, ok PM).

    L'unica funzione polygon che abbiamo, `get_stock_daily`, rende AGGREGATI
    DAILY (`/v2/aggs/.../range/1/day/`): prenderne `bars[-1]["c"]` significa
    leggere la chiusura dell'ULTIMA BARRA CHIUSA, che a mercato aperto e'
    quella di IERI. Il confronto con quotazioni intraday ha confermato che gli
    aggregati daily ripetevano la chiusura precedente e falsavano NAV e P&L.

    Un prezzo vecchio spacciato per live e' PEGGIO di un buco dichiarato
    (regola PM 14/07): qui si restituisce None e la catena passa alla fonte
    successiva. Polygon resta in uso per OPZIONI/IV (`polygon_data`), dove
    l'endpoint e' quello giusto. Per riattivarla servirebbe un endpoint di
    last-trade/snapshot, non gli aggregati daily.
    """
    return None


# FONTI PREZZI — UN SOLO POSTO (19/08, Opus 5). "polygon" e' TOLTO: serviva la
# chiusura di ieri come prezzo live (v. _fetch_polygon); resta per opzioni/IV.
# ⚠️ Prima queste tuple erano DUE: il default della firma e una copia dentro
# main() (riga 384). Il task schedulato passa da main(), quindi correggere solo
# la firma non avrebbe cambiato NULLA in produzione — e un test sulla firma
# sarebbe stato verde su un sistema ancora rotto. Una fonte sola, qui.
FONTI_PREZZI = ("ibkr", "yfinance", "coingecko")
FONTI_PREZZI_NO_IBKR = ("yfinance", "coingecko")


def update_all_prices(db, source_order=FONTI_PREZZI,
                      verbose=True, ibkr_port=7496):
    """Aggiorna prezzi per tutte le posizioni attive.
    Il negozio dei prezzi speciali si legge UNA volta per giro e il suo stato finisce nel
    risultato (`negozio_prezzi`): a negozio assente la fonte coingecko non ha nessun id e
    i simboli che dipendono da lei finirebbero fra i `failed` senza dire perche'."""
    _prezzi = prezzi_speciali()
    _mappa_cg = _prezzi["prezzi"]["coingecko"]
    snap = db.get_portfolio_summary()
    positions = snap.get("positions", [])
    if not positions:
        if verbose:
            print("[price_updater] No active positions in DB")
        return {"updated": 0, "failed": 0, "details": [],
                "negozio_prezzi": {"origine": _prezzi["origine"], "motivo": _prezzi["motivo"]}}

    details = []
    updated = 0
    failed = 0

    for p in positions:
        ticker = p.get("ticker")
        if not ticker:
            continue

        from bellomberg.storage.classificazione import valuta
        etichetta_valuta = valuta(ticker, valuta_posizione=p.get("valuta"))

        price_data = None
        source_used = None
        source_errors = []

        for src in source_order:
            if src == "polygon":
                # Polygon paid: primario per ticker US (no suffisso)
                if "." not in ticker:
                    price_data = _fetch_polygon(ticker)
                    if price_data:
                        source_used = "polygon"
                        break
            elif src == "ibkr":
                # IBKR solo per ticker US senza punti
                if "." not in ticker and len(ticker) <= 5:
                    price_data = _fetch_ibkr(ticker, port=ibkr_port)
                    if price_data:
                        source_used = "ibkr"
                        break
            elif src == "yfinance":
                try:
                    price_data = _fetch_yfinance(ticker)
                except AliasFontiError as e:
                    source_errors.append(str(e))
                    # Il task schedulato usa --quiet: un alias irrisolto deve comparire
                    # comunque nel suo log, non soltanto nel payload di ritorno.
                    print("  [ALIAS YFINANCE KO] %s: %s" % (ticker, e))
                    price_data = None
                if price_data:
                    source_used = "yfinance"
                    break
            elif src == "coingecko":
                # solo i simboli dichiarati nella sezione `coingecko` del negozio
                price_data = _fetch_coingecko(ticker, _mappa_cg)
                if price_data:
                    source_used = "coingecko"
                    break

        if price_data:
            price = price_data[0]
            currency = etichetta_valuta.valore
            currency_label = etichetta_valuta.as_dict()
            if currency is None:
                failed += 1
                dichiarazione = ((currency_label or {}).get("dichiarazione")
                                  or "valuta n.d. (nessuna classificazione disponibile)")
                details.append({
                    "ticker": ticker, "price": price, "currency": None,
                    "currency_label": currency_label, "source": source_used,
                    "error": dichiarazione,
                })
                if verbose:
                    print("  [VALUTA N.D.] " + ticker + ": " + dichiarazione)
                continue
            try:
                db.update_price(ticker, price, valuta=currency, source=source_used)
                updated += 1
                pl_pct = None
                if p.get("prezzo_medio"):
                    pl_pct = (price - p["prezzo_medio"]) / p["prezzo_medio"] * 100
                detail = {
                    "ticker": ticker, "price": price, "currency": currency,
                    "source": source_used, "pl_pct": pl_pct,
                }
                if currency_label is not None:
                    detail["currency_label"] = currency_label
                if source_errors:
                    detail["source_warnings"] = source_errors
                details.append(detail)
                if verbose:
                    pl_str = "{:+.2f}%".format(pl_pct) if pl_pct is not None else "n/a"
                    print("  [OK] {:<15} {:<10} {:>12.4f} {} ({})".format(
                        ticker, currency, price, pl_str, source_used))
            except Exception as e:
                failed += 1
                if verbose:
                    print("  [DB FAIL] " + ticker + ": " + str(e))
        else:
            failed += 1
            if verbose:
                print("  [NO DATA] " + ticker)
            detail = {"ticker": ticker,
                      "error": " | ".join(source_errors) if source_errors
                               else "no data from any source"}
            if etichetta_valuta.valore is None:
                errore_dati = detail["error"]
                detail.update({"currency": None,
                               "currency_label": etichetta_valuta.as_dict(),
                               "error": etichetta_valuta.dichiarazione
                                        + " | " + errore_dati})
            details.append(detail)

    # fix #30 (motore contabile TWR): a fine giro prezzi riuscito persisti lo
    # snapshot NAV ufficiale del giorno (nav_snapshots). MAI bloccante:
    # twr_engine fa no-op con log se le tabelle non esistono ancora.
    if updated > 0:
        try:
            from bellomberg.portfolio.twr_engine import record_nav_snapshot
            record_nav_snapshot(db)
        except Exception as _twr_e:
            if verbose:
                print("  [TWR] snapshot NAV skipped: " + str(_twr_e))

    return {"updated": updated, "failed": failed, "details": details,
            "negozio_prezzi": {"origine": _prezzi["origine"], "motivo": _prezzi["motivo"]},
            "timestamp": datetime.now().isoformat(timespec="seconds")}


def main():
    parser = argparse.ArgumentParser(description="Bellomberg - Live Price Updater")
    parser.add_argument("--loop", type=int, default=0,
                         help="Loop infinito ogni N secondi (es. 60 = ogni minuto). 0 = single run")
    parser.add_argument("--ibkr-port", type=int, default=7496, help="IBKR TWS port (7496 live, 7497 paper)")
    parser.add_argument("--no-ibkr", action="store_true", help="Skip IBKR, usa solo yfinance/CoinGecko")
    parser.add_argument("--quiet", action="store_true", help="Output minimale")
    args = parser.parse_args()

    sources = FONTI_PREZZI_NO_IBKR if args.no_ibkr else FONTI_PREZZI

    db = MemoryDB()

    def _run_once():
        print("\n[" + datetime.now().strftime("%H:%M:%S") + "] BELLOMBERG | price update starting")
        result = update_all_prices(db, source_order=sources, verbose=not args.quiet)
        if not args.quiet:
            print(f"  updated: {result.get('updated', 0)}  failed: {result.get('failed', 0)}")
        # Il negozio dei prezzi speciali si DICHIARA a ogni giro, FUORI da `if not
        # args.quiet`: il task schedulato gira con --quiet e una dichiarazione dentro il
        # ramo verboso sarebbe muta in produzione per costruzione (misurato sul log vivo).
        _neg = result.get("negozio_prezzi") or {}
        # sull'ORIGINE, non sul motivo: v. il commento gemello in portfolio_attribution
        if _neg.get("origine") in ("assente", "illeggibile"):
            print("  [PREZZI] KO dichiarato: negozio dei prezzi speciali %s: %s"
                  % (_neg.get("origine"), _neg.get("motivo")))
        # IV History (voce quant P1, 25/07): snapshot giornaliero ATM IV/RR25 —
        # guard INTERNI al modulo (1 volta/giorno, weekend skip dichiarato);
        # un errore qui non deve MAI toccare l'aggiornamento prezzi.
        try:
            from bellomberg.market_data.iv_history import save_daily_snapshot
            iv = save_daily_snapshot()
            if iv.get("error"):
                # review M1: anche l'errore top-level (DB giu', data invalida)
                # va nel log — mai un buco muto in price_updater.log
                print("  [IV] KO dichiarato:", iv["error"])
            if iv.get("saved"):
                print(f"  iv_history: {iv['saved']}")
            for t, err in (iv.get("errors") or {}).items():
                print(f"  [IV] {t} KO dichiarato: {err}")
        except Exception as e:
            print("[IV] collector errore (dichiarato, prezzi non toccati):", str(e))
        return result

    if args.loop > 0:
        print(f"[BELLOMBERG] price loop every {args.loop}s. Ctrl+C to stop.")
        try:
            while True:
                _run_once()
                time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\n[BELLOMBERG] stopped.")
    else:
        _run_once()


if __name__ == "__main__":
    main()
