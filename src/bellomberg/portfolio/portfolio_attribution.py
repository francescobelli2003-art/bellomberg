"""
portfolio_attribution.py — Quant fase 1b LOTTO 2: CONTRIBUTION attribution.
Da dove viene il rendimento di periodo: posizione / bucket economico / valuta.

NON e' una Brinson benchmark-relative (allocazione vs selezione): quella
richiede un benchmark con pesi settoriali giornalieri (decisione PM futura).
Qui: contributi ASSOLUTI al rendimento del capitale investito, sulla base
ricostruita per-ticker (STESSI mattoni del NAV history: timeline trade,
chiusure yfinance auto-adjusted, FX storico), con LINKING CARINO: la somma
dei contributi = rendimento composto ESATTO del periodo, non un'approssimazione.

Convenzioni DICHIARATE (regola no-fallback 14/07):
- pesi a INIZIO giorno (qty e prezzi del giorno precedente): coerente con la
  convenzione fine-giornata del TWR (w=0); il P&L same-day di un BUY non e'
  attribuito e le qty vendute in giornata contano fino a fine giornata;
- r EUR scomposto per nome: (1+r_loc)*(1+r_fx)-1 -> contributo LOCALE + FX +
  CROSS residuo dichiarato a parte, mai spalmato su altri;
- cash FUORI dal perimetro (attribution del capitale investito, come la vista
  settoriale); il TWR ufficiale include il cash: la differenza di perimetro e
  di base prezzi (chiusure yfinance vs snapshot price_updater) e' DICHIARATA
  nel blocco reconciliation, mai nascosta;
- ticker detenuto ma SENZA prezzi scaricabili (i simboli dichiarati nel negozio
  privato dei prezzi speciali): ESCLUSO e DICHIARATO in excluded, mai spalmato
  ne' taciuto; se il negozio manca non ne viene escluso NESSUNO e la nota lo dice;
- FX mancante per una valuta: nomi esclusi e dichiarati (mai valuta nativa
  sommata come EUR);
- bucket economico = portfolio_sectors.econ_bucket_for: la STESSA funzione
  dell'esposizione (un solo asse, mai divergenze zitte).
"""
import math
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import bellomberg.storage.classificazione as cl

# mattoni riusati dalla ricostruzione NAV (zero duplicazione)
from bellomberg.portfolio.portfolio_analytics import (
    NUMPY_OK, YF_OK, prezzi_speciali,
    _trade_history, _build_position_timeline, _qty_at, _valid_iso,
    _download_prices_for_history, _build_fx_history, _currency_labels_for_tickers,
)
from bellomberg.portfolio.portfolio_sectors import get_sector_map, econ_bucket_for, nota_negozio

_CACHE: Dict[str, Any] = {}
CACHE_TTL_SEC = 600  # 10 min, come le analytics

PERIODS = ("MTD", "YTD", "30D", "INCEPTION")


def _log(msg: str):
    try:
        print(f"[ATTRIB] {msg}", flush=True)
    except OSError:
        pass


def _iso(ts) -> str:
    s = str(ts)
    return s[:10]


def _period_start(period: str, end_iso: str, first_trade_iso: str) -> str:
    """PRIMO giorno della finestra di misura (il suo rendimento CONTA: la base
    pesi e' l'ultimo giorno di borsa STRETTAMENTE PRIMA di questa data — se no
    l'MTD perderebbe il rendimento del giorno 1 del mese, bug trovato al
    collaudo live: base_day usciva 01/07 e il +/- del 1° luglio spariva)."""
    y, m = int(end_iso[:4]), int(end_iso[5:7])
    if period == "MTD":
        return f"{y:04d}-{m:02d}-01"
    if period == "YTD":
        return f"{y:04d}-01-01"
    if period == "30D":
        d = datetime.strptime(end_iso, "%Y-%m-%d") - timedelta(days=29)
        return d.strftime("%Y-%m-%d")   # finestra di 30 giorni di calendario
    return first_trade_iso  # INCEPTION (same-day del primo BUY non attribuito)


def _fx_at(fx, ccy: str, ts) -> Optional[float]:
    """FX ccy->EUR alla data ts: ultima osservazione <= ts; se la data precede
    la PRIMA osservazione, la prima disponibile (convenzione F-CONT-1,
    deterministica). None = FX davvero indisponibile, dichiarato dal chiamante."""
    if ccy == "EUR":
        return 1.0
    if fx is None or getattr(fx, "empty", True) or ccy not in fx.columns:
        return None
    sub = fx[ccy].dropna()
    if not len(sub):
        return None
    older = sub[sub.index <= ts]
    if len(older):
        return float(older.iloc[-1])
    return float(sub.iloc[0])


def _carino_k(r: float) -> float:
    """Fattore di linking Carino ln(1+r)/r, limite 1 per r->0."""
    if abs(r) < 1e-12:
        return 1.0
    return math.log1p(r) / r


def compute_attribution(period: str = "YTD",
                        end_date: Optional[str] = None,
                        trades: Optional[List[Dict[str, Any]]] = None,
                        prices=None, fx=None,
                        official_series: Optional[Dict[str, Any]] = None,
                        fetch=None,
                        force: bool = False) -> Dict[str, Any]:
    """Contribution attribution del periodo. trades/prices/fx/official_series/fetch
    iniettabili per i test (default: DB + yfinance + twr_engine)."""
    if period not in PERIODS:
        return {"error": f"period '{period}' non valido (validi: {', '.join(PERIODS)})"}
    injected = trades is not None

    # review 1b-L2 (BASSA-3): con end_date=None la chiave include la data di OGGI,
    # se no a cavallo di mezzanotte/fine mese si serve l'MTD del giorno vecchio.
    end_iso = end_date or datetime.now().strftime("%Y-%m-%d")
    cache_key = f"attrib:{period}:{end_iso}"
    if not injected and not force and cache_key in _CACHE:
        ent = _CACHE[cache_key]
        if time.time() - ent["ts"] < CACHE_TTL_SEC:
            return ent["data"]

    if not injected and not (NUMPY_OK and YF_OK):
        return {"error": "numpy/yfinance not available"}

    if trades is None:
        trades = _trade_history()
    if not trades:
        return {"error": "trade_history vuota"}
    timeline = _build_position_timeline(trades)
    # review 1b-L2 (MEDIA-1): la data va VALIDATA (lezione 205-B: date spurie sono
    # successe davvero) — se no INCEPTION crasha su strptime invece di dichiarare.
    first_trade = next(((t.get("data") or "")[:10] for t in trades
                        if _valid_iso((t.get("data") or "")[:10])), None)
    if not first_trade:
        return {"error": "nessuna data trade valida (YYYY-MM-DD) in trade_history"}
    start_iso = _period_start(period, end_iso, first_trade)
    # buffer per avere il giorno-base (ultimo giorno di borsa <= start)
    dl_start = (datetime.strptime(start_iso, "%Y-%m-%d")
                - timedelta(days=12)).strftime("%Y-%m-%d")

    tickers = sorted(timeline.keys())
    # negozio dei prezzi speciali: UNA lettura per giro, usata sia per il download sia per
    # l'esclusione piu' sotto, e dichiarata nelle note (lotto 6 criterio (1), 05/09)
    _prezzi = prezzi_speciali()
    salta = _prezzi["prezzi"]["senza_yfinance"]
    if prices is None:
        dl_end = (datetime.strptime(end_iso, "%Y-%m-%d")
                  + timedelta(days=1)).strftime("%Y-%m-%d")
        prices = _download_prices_for_history(tickers, dl_start, dl_end, salta)
    if prices is None or prices.empty:
        return {"error": "prezzi storici non disponibili"}

    negozio_veicoli = cl.carica_veicoli()
    currency_labels = _currency_labels_for_tickers(tickers, trades, negozio_veicoli)
    ccy_of = {tk: label.valore for tk, label in currency_labels.items()
              if label.valore is not None}
    if not ccy_of:
        return {"error": "valuta non determinabile per tutti i ticker: " + "; ".join(
                    label.dichiarazione for label in currency_labels.values()),
                "currency_labels": {k: v.as_dict() for k, v in currency_labels.items()}}
    if fx is None:
        fx = _build_fx_history(sorted(set(ccy_of.values())), dl_start, end_iso)

    # griglia dei giorni di borsa: base = ultimo giorno STRETTAMENTE PRIMA del
    # primo giorno della finestra (il rendimento di start_iso deve contare)
    all_days = [ts for ts in prices.index if _iso(ts) <= end_iso]
    base_days = [ts for ts in all_days if _iso(ts) < start_iso]
    fallback_base = False
    if not base_days:
        # finestra che parte prima della prima candela (atteso per INCEPTION):
        # base = primo giorno disponibile, r del giorno 1 non misurabile.
        # review 1b-L2 (BASSA-2): per gli altri periodi il caso va DICHIARATO.
        base_days = all_days[:1]
        fallback_base = True
    if not base_days:
        return {"error": "nessun giorno di borsa nel periodo"}
    day0 = base_days[-1]
    days = [day0] + [ts for ts in all_days if ts > day0]
    if len(days) < 2:
        return {"error": f"periodo {period} senza giorni di borsa completati"}

    # --- contributi giornalieri ---------------------------------------------
    # review 1b-L2 (MEDIA-2): l'esclusione e' PER GIORNO, non per nome — si
    # accumula {ticker: {motivo: n_giorni}} per dichiarare anche l'ESTENSIONE
    # del buco (un nome puo' essere escluso solo in parte del periodo).
    excluded: Dict[str, Dict[str, int]] = {}

    def _exclude(tk: str, reason: str):
        m = excluded.setdefault(tk, {})
        m[reason] = m.get(reason, 0) + 1
    contrib: Dict[str, Dict[str, float]] = {}   # ticker -> {tot, loc, fxc, cross} linkati (Carino num.)
    avg_w: Dict[str, float] = {}
    day_returns: List[float] = []
    empty_days = 0

    def _px(tk: str, ts) -> Optional[float]:
        if tk not in prices.columns:
            return None
        v = prices[tk].loc[:ts]
        if not len(v):
            return None
        val = float(v.iloc[-1])
        return val if val == val and val > 0 else None

    daily_rows = []   # (ts, {tk: (w, r_tot, r_loc, r_fx)})
    for i in range(1, len(days)):
        d_prev, d = days[i - 1], days[i]
        iso_prev = _iso(d_prev)
        row = {}
        v_prev = 0.0
        for tk in tickers:
            qty = _qty_at(timeline[tk], iso_prev)
            if qty < 0:
                # review 1b-L2 (BASSA-1): il corrotto (oversell/ticker errato,
                # caso gia' visto nel book) NON e' il legittimo qty=0: dichiarato.
                _exclude(tk, "qty negativa: trade corrotti, nome escluso")
                continue
            if qty == 0:
                continue
            # due CAUSE diverse, due frasi (lotto 6, 05/09): finche' la lista era una
            # costante le due erano indistinguibili e andava bene; ora che viene da un
            # negozio che puo' MANCARE, «e' nella lista» e «la colonna non c'e'» non
            # sono la stessa cosa — e a negozio assente resta vera solo la seconda.
            if tk in salta:
                _exclude(tk, "prezzi storici n.d. (dichiarato nel negozio dei prezzi speciali)")
                continue
            if tk not in prices.columns:
                _exclude(tk, "prezzi storici n.d. (colonna assente nel download)")
                continue
            if tk not in ccy_of:
                _exclude(tk, currency_labels[tk].dichiarazione)
                continue
            fxr_prev = _fx_at(fx, ccy_of[tk], d_prev)
            fxr_d = _fx_at(fx, ccy_of[tk], d)
            if fxr_prev is None or fxr_d is None:
                _exclude(tk, f"FX {ccy_of[tk]} n.d.")
                continue
            px_prev, px_d = _px(tk, d_prev), _px(tk, d)
            if px_prev is None or px_d is None:
                _exclude(tk, "prezzo n.d. nel periodo")
                continue
            mv_prev = qty * px_prev * fxr_prev
            r_loc = px_d / px_prev - 1.0
            r_fx = fxr_d / fxr_prev - 1.0
            r_tot = (1.0 + r_loc) * (1.0 + r_fx) - 1.0
            row[tk] = (mv_prev, r_tot, r_loc, r_fx)
            v_prev += mv_prev
        if v_prev <= 0:
            empty_days += 1
            continue
        r_day = 0.0
        norm = {}
        for tk, (mv, r_tot, r_loc, r_fx) in row.items():
            w = mv / v_prev
            norm[tk] = (w, r_tot, r_loc, r_fx)
            r_day += w * r_tot
        if r_day <= -1.0:
            return {"error": f"rendimento giorno {_iso(d)} <= -100%: base dati corrotta"}
        day_returns.append(r_day)
        daily_rows.append((d, norm))

    if not day_returns:
        return {"error": f"periodo {period}: nessun giorno misurabile (capitale investito 0)"}

    # --- linking Carino ------------------------------------------------------
    log_total = sum(math.log1p(r) for r in day_returns)
    r_period = math.expm1(log_total)
    k_big = _carino_k(r_period)
    n_days = len(day_returns)
    for j, (d, norm) in enumerate(daily_rows):
        k = _carino_k(day_returns[j]) / k_big
        for tk, (w, r_tot, r_loc, r_fx) in norm.items():
            c = contrib.setdefault(tk, {"tot": 0.0, "loc": 0.0, "fxc": 0.0, "cross": 0.0})
            c["tot"] += k * w * r_tot
            c["loc"] += k * w * r_loc
            c["fxc"] += k * w * r_fx
            c["cross"] += k * w * r_loc * r_fx
            avg_w[tk] = avg_w.get(tk, 0.0) + w / n_days

    # --- aggregazioni: posizione / bucket economico / valuta -----------------
    smap = get_sector_map(sorted(contrib.keys()), fetch=fetch, negozio=negozio_veicoli)
    notes: List[str] = []
    if nota_negozio(negozio_veicoli):
        notes.append(nota_negozio(negozio_veicoli))
    # il predicato e' l'ORIGINE (la causa), non il motivo (la conseguenza): un ramo di guasto
    # futuro senza motivo farebbe sparire la frase in silenzio (osservazione di e3, 05/09)
    if _prezzi["origine"] in ("assente", "illeggibile"):   # UNA frase, come per i veicoli
        notes.append("negozio dei prezzi speciali %s (%s): l'insieme dei simboli da NON "
                     "scaricare e' VUOTO perche' il negozio manca, non perche' non ci sia "
                     "niente da saltare — nessun nome e' stato escluso per questo motivo "
                     "(dichiarato, regola 14/07)"
                     % (_prezzi["origine"].upper(), _prezzi["motivo"]))
    by_position = []
    for tk, c in contrib.items():
        by_position.append({
            "ticker": tk,
            "contribution_pct": round(c["tot"] * 100.0, 3),
            "local_pct": round(c["loc"] * 100.0, 3),
            "fx_pct": round(c["fxc"] * 100.0, 3),
            "cross_pct": round(c["cross"] * 100.0, 3),
            "avg_weight_pct": round(avg_w[tk] * 100.0, 2),
            "currency": ccy_of[tk],
            "currency_source": str(currency_labels[tk]),
        })
    by_position.sort(key=lambda x: -x["contribution_pct"])

    buckets: Dict[str, Dict[str, Any]] = {}
    for tk, c in contrib.items():
        eb, anom = econ_bucket_for(tk, smap.get(tk), negozio=negozio_veicoli)
        if anom == "no_bucket":
            notes.append(f"asse unico: {tk} senza bucket economico -> n.d. dichiarato "
                         "(dichiarare bucket_economico nella voce del negozio dei veicoli)")
        elif anom and anom.startswith("shadowed:"):
            notes.append(f"asse unico: {tk} voce manuale '{eb}' maschera GICS "
                         f"'{anom.split(':', 1)[1]}' — divergenza dichiarata (classe F-16)")
        b = buckets.setdefault(eb, {"contribution": 0.0, "tickers": []})
        b["contribution"] += c["tot"]
        b["tickers"].append(tk)
    by_bucket = [{"bucket": eb, "contribution_pct": round(b["contribution"] * 100.0, 3),
                  "tickers": sorted(b["tickers"])} for eb, b in buckets.items()]
    by_bucket.sort(key=lambda x: -x["contribution_pct"])

    currencies: Dict[str, Dict[str, Any]] = {}
    for tk, c in contrib.items():
        cc = currencies.setdefault(ccy_of[tk], {"contribution": 0.0, "fxc": 0.0, "tickers": []})
        cc["contribution"] += c["tot"]
        cc["fxc"] += c["fxc"]
        cc["tickers"].append(tk)
    by_currency = [{"currency": k, "contribution_pct": round(v["contribution"] * 100.0, 3),
                    "fx_contribution_pct": round(v["fxc"] * 100.0, 3),
                    "tickers": sorted(v["tickers"])} for k, v in currencies.items()]
    by_currency.sort(key=lambda x: -x["contribution_pct"])

    # dichiarazione dell'ESTENSIONE dei buchi (review 1b-L2 MEDIA-2/BASSA-2)
    excluded_out = []
    for tk, rmap in sorted(excluded.items()):
        days_exc = sum(rmap.values())
        partial = tk in contrib
        excluded_out.append({"ticker": tk, "reasons": rmap,
                             "days_excluded": days_exc, "days_total": n_days,
                             "partial": partial})
        if partial:
            notes.append(f"esclusione PARZIALE per {tk} ({days_exc}/{n_days} giorni): "
                         "contributo SOTTOSTIMATO e denominatore variabile — dichiarato")
    if fallback_base and period != "INCEPTION":
        notes.append(f"base pre-{start_iso} non disponibile nelle candele scaricate: "
                     "il rendimento del primo giorno della finestra NON e' misurato — dichiarato")

    check_sum = sum(c["tot"] for c in contrib.values())
    if abs(check_sum - r_period) > 1e-9:
        # per costruzione Carino chiude esatto: se non chiude e' un BUG, dichiarato
        notes.append(f"CHECK FALLITO: somma contributi {check_sum:.6%} != rendimento "
                     f"{r_period:.6%} — non fidarsi di questa vista")

    # --- riconciliazione DICHIARATA vs serie TWR ufficiale -------------------
    # Gli r ufficiali vengono da compute_twr_payload (twr_index), che gestisce la
    # TRANSIZIONE di regime ricostruito->official (r=0 sul salto, o base = NAV
    # dello snapshot d0). Ricalcolare compute_twr sulla serie cucita grezza
    # contava il cambio di perimetro (la cassa che entra) come rendimento:
    # +64% finto il 12/06, trovato al collaudo YTD (delta -63,9pp dichiarato).
    if official_series is None and not injected:
        try:
            from bellomberg.portfolio.twr_engine import compute_twr_payload
            tp = compute_twr_payload()
            if tp.get("error"):
                official_series = {"error": tp["error"]}
            else:
                idx = tp.get("twr_index") or []
                official_series = {
                    "dates": tp.get("dates") or [],
                    "_r": ([idx[j] / idx[j - 1] - 1.0 for j in range(1, len(idx))]
                           if len(idx) > 1 else []),
                }
        except Exception as e:
            official_series = {"error": str(e)}
    if official_series and not official_series.get("error") and official_series.get("dates"):
        od = official_series["dates"]
        orr = official_series.get("_r")
        if orr is None:
            # serie iniettata senza r precalcolati: MAI ripiegare su 0% zitto
            try:
                from bellomberg.portfolio.twr_engine import compute_twr as _ctwr
                orr = _ctwr(official_series.get("values_eur") or [],
                            official_series.get("flows_eur") or [])
            except Exception:
                orr = None
        if not orr:
            reconciliation = {"error": "r ufficiali non calcolabili",
                              "note": "riconciliazione NON disponibile: dichiarato"}
        else:
            start_bound = _iso(day0)
            lg = sum(math.log1p(r) for dstr, r in zip(od[1:], orr)
                     if start_bound < dstr <= end_iso)
            r_official = math.expm1(lg)
            reconciliation = {
                "recon_return_pct": round(r_period * 100.0, 3),
                "official_twr_pct": round(r_official * 100.0, 3),
                "delta_pp": round((r_period - r_official) * 100.0, 3),
                "note": ("basi DIVERSE dichiarate: attribution = capitale investito, "
                         "chiusure yfinance auto-adjusted; TWR ufficiale = NAV totale "
                         "(cash incluso), snapshot price_updater nel tratto official. "
                         "Un delta ampio va capito, non nascosto."),
            }
    else:
        reconciliation = {"error": (official_series or {}).get("error") or "serie ufficiale n.d.",
                          "note": "riconciliazione NON disponibile: dichiarato"}

    out = {
        "period": {"label": period, "base_day": _iso(day0), "end": _iso(days[-1]),
                   "n_trading_days": n_days},
        "portfolio_return_pct": round(r_period * 100.0, 3),
        "by_position": by_position,
        "by_bucket": by_bucket,
        "by_currency": by_currency,
        "currency_labels": {k: v.as_dict() for k, v in currency_labels.items()},
        "totals": {
            "local_pct": round(sum(c["loc"] for c in contrib.values()) * 100.0, 3),
            "fx_pct": round(sum(c["fxc"] for c in contrib.values()) * 100.0, 3),
            "cross_pct": round(sum(c["cross"] for c in contrib.values()) * 100.0, 3),
        },
        "excluded": excluded_out,
        "reconciliation": reconciliation,
        "notes": notes,
        "basis": ("CONTRIBUTION assoluta (NON Brinson vs benchmark): contributi al "
                  "rendimento del capitale INVESTITO (cash escluso), pesi a inizio "
                  "giorno, chiusure yfinance auto-adjusted (dividendi nel prezzo), "
                  "linking Carino (somma contributi = rendimento composto esatto); "
                  "scomposizione locale+FX+cross residuo dichiarato; bucket = asse "
                  "economico unico di portfolio_sectors (fase 1b)"
                  + (f"; {empty_days} giorni a capitale 0 esclusi" if empty_days else "")),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "_source": "portfolio_attribution.compute_attribution",
    }
    if not injected:
        _CACHE[cache_key] = {"ts": time.time(), "data": out}
    return out


if __name__ == "__main__":
    import json as _json
    for p in ("MTD", "YTD"):
        o = compute_attribution(period=p)
        print(f"\n===== {p} =====")
        print(_json.dumps(o, ensure_ascii=False, indent=1)[:3500])
