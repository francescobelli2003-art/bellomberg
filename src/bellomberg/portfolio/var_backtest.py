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
    cols = [c for c in yf_syms if c in returns.columns]
    if len(cols) < 2:
        return {"error": "serie insufficienti dopo il download", "timestamp": datetime.now().isoformat()}
    w2 = np.array([w[yf_syms.index(c)] for c in cols])
    w2 = w2 / w2.sum()
    port_r, sample_meta = _weighted_portfolio_returns(returns, w2, cols)

    if len(port_r) < window + 60:
        return {"error": f"campione troppo corto per il backtest: {len(port_r)} obs "
                         f"(servono >= {window + 60})",
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
        "excluded_tickers": [s for s in yf_syms if s not in cols],
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
        out["var" + conf] = {
            "kupiec_pof": kup,
            "christoffersen_ind": ind,
            "conditional_coverage": cc,
            "verdict": ("PASS" if (kup.get("pass_5pct") and ind.get("pass_5pct")) else "FAIL"),
        }
    out["var99"]["note"] = ("code a ~1%: con questa finestra le eccezioni attese sono poche, "
                            "il test ha bassa potenza — verdetto indicativo")
    out["official_var"] = "historical_95_1d (portfolio_risk) — questo backtest lo valida"
    _log(f"backtest: {out['n_obs_tested']} giorni testati | "
         f"VaR95 {out['var95']['verdict']} ({out['var95']['kupiec_pof'].get('exceptions')}/"
         f"{out['var95']['kupiec_pof'].get('expected')} attese) | "
         f"VaR99 {out['var99']['verdict']}")
    return out


if __name__ == "__main__":
    import json
    r = backtest_var()
    print(json.dumps(r, indent=1, default=str))
