"""
var_backtest.py — Backtest del VaR storico (P1 audit: "VaR mai backtestato").

Valida il VaR UFFICIALE (historical 95/99 1-day, portfolio_risk) con i test
standard di letteratura:
  - Kupiec (1995) POF: il numero di eccezioni e' coerente con la copertura?
  - Christoffersen (1998) independence: le eccezioni arrivano a grappoli?
  - Christoffersen conditional coverage: i due combinati (chi2 a 2 gdl).

Metodo: VaR rolling a finestra fissa (default 252 obs) sul rendimento di
portafoglio del giorno DOPO; serie costruita con gli stessi helper del risk
engine (rendimenti EUR, media pesata per-giorno sui nomi disponibili).

DICHIARATO: il backtest gira sul book CORRENTE proiettato all'indietro (pesi
di oggi), non sulla storia reale del conto — misura la qualita' del MODELLO
di VaR su questo book, non la P&L storica del PM. VaR99 su 252 obs = 2-3
osservazioni di coda: test poco potente, verdetto da leggere con cautela.

Voce 7 b (04/10): serie vuote/insufficienti ESCLUSE e dichiarate col loro peso
(`excluded_detail`, `excluded_weight_pct`; oltre SOGLIA_PESO_ESCLUSO_PCT il verdetto e'
"NON AFFIDABILE"); verdetto su due assi nominati (`coverage`, `independence`) con
`verdict_detail` che dice quale asse fallisce; p esatti quando le eccezioni attese sono poche.
"""
import math
from datetime import datetime
from typing import Any, Dict, Optional

try:
    import numpy as np
    from scipy import stats as _st
    SCIPY_OK = True
except Exception:
    SCIPY_OK = False


def _log(msg: str):
    try:
        print(f"[VAR-BT] {msg}", flush=True)
    except OSError:
        pass


def _xlogy(x: float, y: float) -> float:
    """x*ln(y) con la convenzione 0*ln(0) = 0 (limite della log-likelihood)."""
    if x == 0:
        return 0.0
    if y <= 0:
        return float("-inf")
    return x * math.log(y)


def kupiec_pof(n_obs: int, n_exceptions: int, coverage_p: float) -> Dict[str, Any]:
    """Kupiec (1995) proportion-of-failures: LR ~ chi2(1) sotto H0 (copertura giusta)."""
    T, x, p = int(n_obs), int(n_exceptions), float(coverage_p)
    if T <= 0:
        return {"error": "campione vuoto"}
    pi = x / T
    ll_h0 = _xlogy(T - x, 1 - p) + _xlogy(x, p)
    ll_h1 = _xlogy(T - x, 1 - pi) + _xlogy(x, pi)
    LR = max(0.0, -2.0 * (ll_h0 - ll_h1))
    pval = float(1 - _st.chi2.cdf(LR, df=1))
    return {"LR": round(LR, 3), "p_value": round(pval, 4),
            "pass_5pct": bool(pval >= 0.05),
            "exceptions": x, "expected": round(T * p, 1), "obs": T,
            "exception_rate_pct": round(pi * 100, 2)}


def christoffersen_independence(exceptions: "np.ndarray") -> Dict[str, Any]:
    """Christoffersen (1998): le eccezioni sono indipendenti nel tempo?
    LR_ind ~ chi2(1) sotto H0 (niente clustering)."""
    e = np.asarray(exceptions, dtype=int)
    if len(e) < 2:
        return {"error": "serie troppo corta"}
    prev, cur = e[:-1], e[1:]
    n00 = int(((prev == 0) & (cur == 0)).sum())
    n01 = int(((prev == 0) & (cur == 1)).sum())
    n10 = int(((prev == 1) & (cur == 0)).sum())
    n11 = int(((prev == 1) & (cur == 1)).sum())
    pi01 = n01 / (n00 + n01) if (n00 + n01) > 0 else 0.0
    pi11 = n11 / (n10 + n11) if (n10 + n11) > 0 else 0.0
    pi = (n01 + n11) / (n00 + n01 + n10 + n11)
    ll_h0 = _xlogy(n00 + n10, 1 - pi) + _xlogy(n01 + n11, pi)
    ll_h1 = (_xlogy(n00, 1 - pi01) + _xlogy(n01, pi01)
             + _xlogy(n10, 1 - pi11) + _xlogy(n11, pi11))
    LR = max(0.0, -2.0 * (ll_h0 - ll_h1))
    pval = float(1 - _st.chi2.cdf(LR, df=1))
    return {"LR": round(LR, 3), "p_value": round(pval, 4),
            "pass_5pct": bool(pval >= 0.05),
            "transitions": {"n00": n00, "n01": n01, "n10": n10, "n11": n11},
            "consecutive_exceptions": n11}


# voce 7 b (04/10, Opus 5.5): perimetro DICHIARATO e verdetto a due assi.
# - Una colonna presente ma vuota (download fallito) NON e' un nome coperto: prima contava
#   come presente ed `excluded_tickers` usciva [] su un backtest girato su 18 nomi su 27.
# - Soglia di osservazioni valide = la stessa del risk engine (portfolio_risk: `len(r) < 20`
#   salta il nome), cosi' il backtest valida lo STESSO perimetro del VaR ufficiale.
MIN_OSS_SERIE = 20
# Oltre questa quota del perimetro (per peso) esclusa, il verdetto PASS/FAIL non parla del
# book: esce "NON AFFIDABILE" dichiarato invece di un PASS/FAIL su un altro portafoglio.
SOGLIA_PESO_ESCLUSO_PCT = 10.0
# Sotto queste eccezioni attese il chi2 asintotico e' fragile: si usano i p esatti e si
# dichiara la bassa potenza (VaR99 su ~500 giorni = 5 eccezioni attese).
POCHE_ECCEZIONI_ATTESE = 10.0


def serie_utilizzabili(returns: "Any", syms, weights, min_obs: int = MIN_OSS_SERIE):
    """Separa i simboli con serie utilizzabile da quelli esclusi, con MOTIVO e PESO.

    `weights` e' allineato a `syms` (quote del perimetro, somma 1). Una colonna assente,
    tutta vuota o con meno di `min_obs` rendimenti validi e' ESCLUSA e dichiarata.
    Ritorna (cols, esclusi) con esclusi = [{ticker, motivo, obs_valide, peso_pct}].
    """
    cols, esclusi = [], []
    for s, wi in zip(syms, weights):
        if s not in returns.columns:
            n_ok, motivo = 0, "serie assente nel download"
        else:
            n_ok = int(returns[s].notna().sum())
            if n_ok == 0:
                motivo = "serie vuota (download fallito)"
            elif n_ok < min_obs:
                motivo = f"storia insufficiente ({n_ok} obs < {min_obs})"
            else:
                cols.append(s)
                continue
        esclusi.append({"ticker": s, "motivo": motivo, "obs_valide": n_ok,
                        "peso_pct": round(float(wi) * 100, 2)})
    return cols, esclusi


def binomiale_esatto(n_obs: int, n_exceptions: int, coverage_p: float) -> Optional[float]:
    """p-value ESATTO (binomiale, bilaterale) del numero di eccezioni: il gemello di Kupiec
    senza l'approssimazione chi2, che con poche eccezioni attese e' fragile."""
    if n_obs <= 0:
        return None
    return float(_st.binomtest(int(n_exceptions), int(n_obs), float(coverage_p),
                               alternative="two-sided").pvalue)


def indipendenza_esatta(n_obs: int, n_exceptions: int, n11: int) -> Optional[float]:
    """P(coppie consecutive >= n11 | n_exceptions eccezioni in n_obs giorni), ESATTO.

    Sotto H0 (nessun grappolo), date x eccezioni, ogni disposizione e' equiprobabile.
    Con k "blocchi" di eccezioni consecutive n11 = x - k, e le disposizioni con k blocchi
    sono C(x-1, k-1) * C(n-x+1, k) su C(n, x). Test a una coda (il rischio e' il grappolo).
    """
    n, x, obs = int(n_obs), int(n_exceptions), int(n11)
    if n <= 0 or x <= 0 or x >= n:
        return None
    tot = math.comb(n, x)
    k_max = x - obs
    if k_max < 1:  # n11 >= x e' impossibile (al massimo x-1 coppie)
        return 0.0
    favorevoli = sum(math.comb(x - 1, k - 1) * math.comb(n - x + 1, k)
                     for k in range(1, min(k_max, n - x + 1) + 1))
    return float(favorevoli) / float(tot)


def verdetto_assi(kup: Dict[str, Any], ind: Dict[str, Any], alpha: float,
                  inaffidabile: Optional[str] = None) -> Dict[str, Any]:
    """Verdetto su DUE assi nominati (copertura / indipendenza) + combinato spiegato.

    Con poche eccezioni attese l'asse usa il p esatto (dichiarato in `test`); il chi2
    resta riportato. `inaffidabile` (motivo) sostituisce il combinato con NON AFFIDABILE.
    """
    T, x = kup.get("obs") or 0, kup.get("exceptions") or 0
    attese = T * alpha
    poca_potenza = attese < POCHE_ECCEZIONI_ATTESE
    cov = {"name": "copertura (numero di eccezioni)", "test": "Kupiec POF (chi2 1 gdl)",
           "p_value_chi2": kup.get("p_value")}
    # review RV-R (P3): il verdetto si decide sul p GREZZO — `kup["p_value"]` e' gia'
    # arrotondato a 4 decimali (p 0.049968 -> 0.05 = PASS falso); per il chi2 vale
    # `pass_5pct`, calcolato da kupiec_pof prima dell'arrotondamento.
    p_cov, ok_cov = kup.get("p_value"), kup.get("pass_5pct")
    if poca_potenza and T > 0:
        p_ex = binomiale_esatto(T, x, alpha)
        cov["p_value_binomial_exact"] = round(p_ex, 4)
        cov["test"] = "binomiale esatto (poche eccezioni attese: chi2 non affidabile)"
        p_cov, ok_cov = p_ex, p_ex >= 0.05
    cov["p_value"] = None if p_cov is None else round(p_cov, 4)
    cov["verdict"] = ("n.d." if (p_cov is None or ok_cov is None)
                      else ("PASS" if ok_cov else "FAIL"))

    indep = {"name": "indipendenza (eccezioni a grappoli)",
             "test": "Christoffersen (chi2 1 gdl)", "p_value_chi2": ind.get("p_value"),
             "consecutive_exceptions": ind.get("consecutive_exceptions")}
    p_ind, ok_ind = ind.get("p_value"), ind.get("pass_5pct")
    if poca_potenza and "transitions" in ind:
        p_ex = indipendenza_esatta(T, x, ind["consecutive_exceptions"])
        if p_ex is not None:
            indep["p_value_exact_conditional"] = round(p_ex, 4)
            indep["test"] = ("esatto condizionato al numero di eccezioni "
                             "(poche eccezioni attese: chi2 non affidabile)")
            p_ind, ok_ind = p_ex, p_ex >= 0.05
    indep["p_value"] = None if p_ind is None else round(p_ind, 4)
    indep["verdict"] = ("n.d." if (p_ind is None or ok_ind is None)
                        else ("PASS" if ok_ind else "FAIL"))

    assi = (("copertura", cov), ("indipendenza", indep))
    falliti = [nome for nome, a in assi if a["verdict"] == "FAIL"]
    # review RV-R (P3): un asse n.d. (test in errore) non e' un FAIL ne' un PASS
    nd = [nome for nome, a in assi if a["verdict"] == "n.d."]
    if inaffidabile:
        verdict = "NON AFFIDABILE"
        dettaglio = "NON AFFIDABILE: " + inaffidabile
    elif not falliti and nd:
        verdict = "n.d."
        dettaglio = ("n.d.: asse " + " e ".join(nd) + " non calcolabile — " + "; ".join(
            f"{nome} {a['verdict']} (p {a['p_value']})" for nome, a in assi))
    elif not falliti:
        verdict, dettaglio = "PASS", "PASS su entrambi gli assi (copertura e indipendenza)"
    else:
        verdict = "FAIL"
        parti = []
        for nome, a in (("copertura", cov), ("indipendenza", indep)):
            parti.append(f"{nome} {a['verdict']} (p {a['p_value']})")
        spiega = ""
        if falliti == ["indipendenza"] and not nd:
            spiega = (": il numero di eccezioni e' coerente, ma arrivano a grappoli "
                      "(limite noto del VaR storico, non si adatta ai regimi di volatilita')")
        elif falliti == ["copertura"] and not nd:
            spiega = ": il numero di eccezioni non e' coerente con il livello di confidenza"
        dettaglio = "FAIL sull'asse " + " e ".join(falliti) + spiega + " — " + "; ".join(parti)
    if poca_potenza:
        dettaglio += (f" — bassa potenza: {round(attese, 1)} eccezioni attese "
                      f"(< {POCHE_ECCEZIONI_ATTESE:g}), verdetto indicativo")
    return {"coverage": cov, "independence": indep, "verdict": verdict,
            "verdict_detail": dettaglio, "expected_exceptions": round(attese, 1),
            "low_power": bool(poca_potenza)}


def backtest_var(window: int = 252, period: str = "3y",
                 force_refresh: bool = False) -> Dict[str, Any]:
    """Backtest completo del VaR storico 95/99 1d sul book corrente.

    Per ogni giorno t (dopo il warm-up): VaR = percentile della finestra dei
    'window' giorni PRECEDENTI; eccezione se il rendimento di t sfonda il VaR.
    """
    if not SCIPY_OK:
        return {"error": "numpy/scipy non installati", "timestamp": datetime.now().isoformat()}
    try:
        import pandas as pd
        import yfinance as yf
        from bellomberg.storage.memory_db import MemoryDB
        from bellomberg.portfolio.portfolio_risk import (_yf_ticker, _convert_returns_to_eur,
                                     _weighted_portfolio_returns, prezzi_speciali,
                                     ko_negozio_prezzi)
        from bellomberg.cli.price_updater import data_ticker_map
    except Exception as e:
        return {"error": "import falliti: " + str(e), "timestamp": datetime.now().isoformat()}

    # Stesso perimetro (e stessa base EUR) di compute_portfolio_risk: il negozio dei prezzi
    # speciali si legge PRIMA del DB e la sua assenza ferma il backtest invece di cambiargli
    # il campione in silenzio (lotto 6 criterio (1), 05/09).
    _prezzi = prezzi_speciali()
    _ko = ko_negozio_prezzi(_prezzi)
    if _ko:
        return _ko
    salta = _prezzi["prezzi"]["senza_yfinance"]

    db = MemoryDB()
    snap = db.get_portfolio_summary()
    if snap.get("fx_incomplete"):
        return {"error": "FX incompleto: pesi VaR EUR n.d. (" +
                ", ".join(snap["fx_incomplete"]) + ")",
                "timestamp": datetime.now().isoformat()}
    positions = snap.get("positions", []) or []
    if not positions:
        return {"error": "no positions", "timestamp": datetime.now().isoformat()}

    yf_syms, weights, cur_of = [], [], {}
    skipped_positions = []  # review 22/07 (E9): perimetro dichiarato nel payload
    for p in positions:
        sym = _yf_ticker(p["ticker"], salta)
        v = float(p.get("valore_mercato", 0) or 0)
        if not sym or v <= 0:
            skipped_positions.append(f"{p['ticker']} ({'SKIP list' if not sym else 'valore <= 0'})")
            continue
        yf_syms.append(sym)
        weights.append(v)
        cur_of[sym] = (p.get("valuta") or "EUR")
    if len(yf_syms) < 2:
        return {"error": "meno di 2 ticker analizzabili", "timestamp": datetime.now().isoformat()}
    w = np.array(weights, dtype=float)
    w = w / w.sum()

    try:
        dl_map = data_ticker_map(yf_syms)
        raw = yf.download(list(dl_map.values()), period=period, progress=False,
                          auto_adjust=True, threads=True)
        _ren = {dato: reale for reale, dato in dl_map.items() if dato != reale}
        if _ren:
            raw = (raw.rename(columns=_ren, level=-1)
                   if isinstance(raw.columns, pd.MultiIndex) else raw.rename(columns=_ren))
        prices = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        returns = prices.pct_change().dropna(how="all")
    except Exception as e:
        return {"error": "yfinance: " + str(e)[:120], "timestamp": datetime.now().isoformat()}

    # review 14/07: il period dell'FX deve coprire lo stesso orizzonte dei prezzi
    returns, fx_meta = _convert_returns_to_eur(returns, cur_of, period=period)
    # voce 7 b: una colonna presente ma VUOTA (download fallito) non e' un nome coperto
    cols, esclusi = serie_utilizzabili(returns, yf_syms, w)
    peso_escluso_pct = round(sum(e["peso_pct"] for e in esclusi), 2)
    if esclusi:
        _log("serie escluse dal backtest: " + ", ".join(
            f"{e['ticker']} ({e['motivo']}, peso {e['peso_pct']}%)" for e in esclusi))
    if len(cols) < 2:
        return {"error": "serie insufficienti dopo il download", "excluded_tickers": [e["ticker"] for e in esclusi],
                "excluded_detail": esclusi, "excluded_weight_pct": peso_escluso_pct,
                "timestamp": datetime.now().isoformat()}
    w2 = np.array([w[yf_syms.index(c)] for c in cols])
    w2 = w2 / w2.sum()
    port_r, sample_meta = _weighted_portfolio_returns(returns, w2, cols)

    # review RV-R (P2): `serie_utilizzabili` guarda la serie INTERA; un nome con poche
    # quotazioni (o fermo a meta' periodo) supera min_obs ma manca nei giorni testati e
    # _weighted_portfolio_returns rinormalizza zitto. Si misura il peso del perimetro
    # MANCANTE in media nei giorni testati (pesi del perimetro, esclusi = sempre mancanti;
    # include le festivita' locali dei singoli mercati, dichiarato).
    giorni_testati = port_r.index[window:]
    w_perim = pd.Series(w, index=yf_syms)
    presenti = returns.reindex(index=giorni_testati, columns=cols).notna()
    peso_presente = presenti.mul(w_perim[cols], axis=1).sum(axis=1)
    peso_mancante_pct = (round(float((1.0 - peso_presente.mean()) * 100), 2)
                         if len(giorni_testati) else None)
    copertura_nomi = {c: f"{int(presenti[c].sum())}/{len(giorni_testati)}" for c in cols
                      if len(giorni_testati) and presenti[c].mean() < 0.9}
    inaffidabile = None
    if peso_escluso_pct > SOGLIA_PESO_ESCLUSO_PCT:
        inaffidabile = (f"{len(esclusi)} nomi esclusi per serie mancanti/insufficienti pesano il "
                        f"{peso_escluso_pct}% del perimetro (soglia {SOGLIA_PESO_ESCLUSO_PCT:g}%): "
                        "il backtest non valida il VaR di QUESTO book")
    elif peso_mancante_pct is not None and peso_mancante_pct > SOGLIA_PESO_ESCLUSO_PCT:
        inaffidabile = (f"nei giorni testati manca in media il {peso_mancante_pct}% del perimetro "
                        f"(soglia {SOGLIA_PESO_ESCLUSO_PCT:g}%; nomi con copertura < 90% dei giorni "
                        f"testati: " + (", ".join(f"{k} {v}" for k, v in copertura_nomi.items())
                                        or "nessuno") +
                        "): il backtest non valida il VaR di QUESTO book")

    if len(port_r) < window + 60:
        return {"error": f"campione troppo corto per il backtest: {len(port_r)} obs "
                         f"(servono >= {window + 60})",
                "error_code": "INSUFFICIENT_HISTORY", "calculation": "var_backtest",
                "observations": int(len(port_r)), "minimum_observations": int(window + 60),
                "observation_basis": "portfolio_daily_returns_including_warmup",
                "sample_meta": sample_meta, "timestamp": datetime.now().isoformat()}

    out: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "window": int(window),
        "period": period,
        "n_obs_series": int(len(port_r)),
        "n_obs_tested": int(len(port_r) - window),
        "returns_basis": "EUR" if fx_meta.get("converted") else "valuta locale",
        "fx_local_declared": fx_meta.get("local_declared") or [],
        "sample_meta": sample_meta,
        # review 22/07 (E9): ticker senza dati esclusi e pesi rinormalizzati —
        # prima in silenzio, ora DICHIARATI (il backtest valida il perimetro
        # elencato qui, non necessariamente il book intero)
        "excluded_tickers": [e["ticker"] for e in esclusi],
        # voce 7 b: motivo, osservazioni valide e peso di ogni escluso; peso escluso totale
        # sul perimetro analizzabile (posizioni SKIP fuori, come nel VaR ufficiale)
        "excluded_detail": esclusi,
        "excluded_weight_pct": peso_escluso_pct,
        "excluded_weight_threshold_pct": SOGLIA_PESO_ESCLUSO_PCT,
        # review RV-R (P2): peso del perimetro mancante in media nei giorni TESTATI (esclusi
        # compresi) e nomi presenti in meno del 90% di quei giorni ("presenti/testati")
        "missing_weight_tested_window_pct": peso_mancante_pct,
        "partial_coverage_tested_window": copertura_nomi,
        "min_obs_per_series": MIN_OSS_SERIE,
        "n_tickers_tested": len(cols),
        "n_tickers_perimeter": len(yf_syms),
        "reliable": inaffidabile is None,
        "skipped_positions": skipped_positions,
        "declared_scope": ("backtest sul book CORRENTE proiettato all'indietro (pesi di oggi): "
                           "valida il MODELLO di VaR su questo book, non la P&L storica del conto"),
    }

    for conf, alpha in (("95", 0.05), ("99", 0.01)):
        var_series = port_r.rolling(window).quantile(alpha).shift(1)
        test = port_r[var_series.notna()]
        var_t = var_series.dropna()
        exc = (test < var_t).astype(int).values
        kup = kupiec_pof(len(exc), int(exc.sum()), alpha)
        ind = christoffersen_independence(exc)
        cc = None
        if "LR" in kup and "LR" in ind:
            lr_cc = kup["LR"] + ind["LR"]
            p_cc = float(1 - _st.chi2.cdf(lr_cc, df=2))
            cc = {"LR": round(lr_cc, 3), "p_value": round(p_cc, 4),
                  "pass_5pct": bool(p_cc >= 0.05)}
        assi = verdetto_assi(kup, ind, alpha, inaffidabile)
        out["var" + conf] = {
            "kupiec_pof": kup,
            "christoffersen_ind": ind,
            "conditional_coverage": cc,
            # voce 7 b: combinato = PASS solo se ENTRAMBI gli assi passano; `verdict_detail`
            # dice quale asse fallisce, `coverage`/`independence` portano i p (esatti se poche
            # eccezioni attese); NON AFFIDABILE se il perimetro escluso supera la soglia
            **assi,
        }
    out["var99"]["note"] = ("code a ~1%: con questa finestra le eccezioni attese sono poche, "
                            "il test ha bassa potenza — verdetto indicativo")
    out["official_var"] = "historical_95_1d (portfolio_risk) — questo backtest lo valida"
    _log(f"backtest: {out['n_obs_tested']} giorni testati | "
         f"VaR95 {out['var95']['verdict']} ({out['var95']['kupiec_pof'].get('exceptions')}/"
         f"{out['var95']['kupiec_pof'].get('expected')} attese) | "
         f"VaR99 {out['var99']['verdict']} | perimetro {len(cols)}/{len(yf_syms)} nomi, "
         f"peso escluso {peso_escluso_pct}%")
    return out


if __name__ == "__main__":
    import json
    r = backtest_var()
    print(json.dumps(r, indent=1, default=str))
