"""
advanced_metrics.py - Metriche di performance e rischio ISTITUZIONALI (numpy puro).

Scelta di design (robustezza > comodita'): nessuna dipendenza esterna fragile
(no quantstats, no pandas obbligatorio). Solo numpy. Ogni metrica e' guarded contro
gli edge case (serie vuote, deviazione standard nulla, drawdown assente): non solleva
MAI un'eccezione, ritorna None dove non calcolabile.

Input: rendimenti GIORNALIERI (lista/array di float, es. 0.012 = +1,2%).
Riferimenti: Sharpe (1966), Sortino (1994), Calmar (Young 1991),
Omega (Keating-Shadwick 2002), Ulcer Index (Martin 1989), Cornish-Fisher (1937).
"""

try:
    import numpy as np
    NP_OK = True
except ImportError:
    NP_OK = False

TRADING_DAYS = 252


def _clean(returns):
    a = np.asarray([r for r in returns if r is not None], dtype=float)
    return a[np.isfinite(a)]


def _safe(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _round(v, n=4):
    v = _safe(v)
    return round(v, n) if v is not None else None


def compute_metrics(returns, rf_annual=0.0, benchmark=None):
    """Pacchetto completo di metriche da rendimenti giornalieri."""
    if not NP_OK:
        return {"error": "numpy non disponibile"}
    r = _clean(returns)
    n = len(r)
    if n < 5:
        return {"error": "serie troppo corta", "n_obs": int(n)}

    rf_daily = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    excess = r - rf_daily
    mean_d = float(r.mean())
    std_d = float(r.std(ddof=1)) if n > 1 else 0.0

    equity = np.cumprod(1 + r)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    max_dd = float(dd.min())
    max_dd_dur = _max_consecutive(dd < 0)
    avg_dd = float(dd[dd < 0].mean()) if np.any(dd < 0) else 0.0
    ulcer = float(np.sqrt(np.mean(np.square(dd * 100))))

    total_return = float(equity[-1] - 1)
    years = n / TRADING_DAYS
    cagr = float(equity[-1] ** (1 / years) - 1) if years > 0 and equity[-1] > 0 else None
    vol_ann = std_d * np.sqrt(TRADING_DAYS)

    sharpe = (float(excess.mean() / excess.std(ddof=1)) * np.sqrt(TRADING_DAYS)
              if n > 1 and excess.std(ddof=1) > 0 else None)
    downside = r[r < 0]
    downside_dev = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
    sortino = (float(excess.mean() / downside_dev) * np.sqrt(TRADING_DAYS)
               if downside_dev > 0 else None)
    calmar = float(cagr / abs(max_dd)) if (cagr is not None and max_dd < 0) else None
    sterling = float(cagr / abs(avg_dd)) if (cagr is not None and avg_dd < 0) else None
    gains = r[r > 0].sum()
    losses = -r[r < 0].sum()
    omega = float(gains / losses) if losses > 0 else None

    skew = _skew(r)
    kurt = _kurtosis(r)
    var95 = float(np.percentile(r, 5))
    var99 = float(np.percentile(r, 1))
    cf_var95 = _cornish_fisher_var(r, 0.05, skew, kurt)
    cvar95 = float(r[r <= var95].mean()) if np.any(r <= var95) else None
    cvar99 = float(r[r <= var99].mean()) if np.any(r <= var99) else None
    p95, p5 = float(np.percentile(r, 95)), float(np.percentile(r, 5))
    tail_ratio = float(abs(p95) / abs(p5)) if p5 != 0 else None

    wins = r[r > 0]
    loss = r[r < 0]
    win_rate = float(len(wins) / n)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(loss.mean()) if len(loss) else 0.0
    payoff = float(avg_win / abs(avg_loss)) if avg_loss < 0 else None
    profit_factor = float(wins.sum() / abs(loss.sum())) if loss.sum() < 0 else None
    best_day = float(r.max())
    worst_day = float(r.min())
    max_win_streak = _max_consecutive(r > 0)
    max_loss_streak = _max_consecutive(r < 0)
    recovery = float(total_return / abs(max_dd)) if max_dd < 0 else None
    kelly = _kelly(win_rate, avg_win, avg_loss)

    out = {
        "n_obs": int(n),
        "years": _round(years, 2),
        "total_return_pct": _round(total_return * 100, 2),
        "cagr_pct": _round(cagr * 100, 2) if cagr is not None else None,
        "vol_annual_pct": _round(vol_ann * 100, 2),
        "sharpe": _round(sharpe, 2),
        "sortino": _round(sortino, 2),
        "calmar": _round(calmar, 2),
        "sterling": _round(sterling, 2),
        "omega": _round(omega, 2),
        "max_drawdown_pct": _round(max_dd * 100, 2),
        "max_dd_duration_days": int(max_dd_dur),
        "avg_drawdown_pct": _round(avg_dd * 100, 2),
        "ulcer_index": _round(ulcer, 2),
        "recovery_factor": _round(recovery, 2),
        "skewness": _round(skew, 2),
        "excess_kurtosis": _round(kurt, 2),
        "var_95_1d_pct": _round(var95 * 100, 2),
        "var_99_1d_pct": _round(var99 * 100, 2),
        "cornish_fisher_var_95_pct": _round(cf_var95 * 100, 2) if cf_var95 is not None else None,
        "cvar_95_1d_pct": _round(cvar95 * 100, 2) if cvar95 is not None else None,
        "cvar_99_1d_pct": _round(cvar99 * 100, 2) if cvar99 is not None else None,
        "tail_ratio": _round(tail_ratio, 2),
        "win_rate_pct": _round(win_rate * 100, 1),
        "payoff_ratio": _round(payoff, 2),
        "profit_factor": _round(profit_factor, 2),
        "best_day_pct": _round(best_day * 100, 2),
        "worst_day_pct": _round(worst_day * 100, 2),
        "max_win_streak": int(max_win_streak),
        "max_loss_streak": int(max_loss_streak),
        "kelly_fraction_pct": _round(kelly * 100, 1) if kelly is not None else None,
    }

    if benchmark is not None:
        b = _clean(benchmark)
        m = min(len(r), len(b))
        if m >= 10:
            rr, bb = r[-m:], b[-m:]
            cov = float(np.cov(rr, bb, ddof=1)[0, 1])
            var_b = float(np.var(bb, ddof=1))
            beta = cov / var_b if var_b > 0 else None
            corr = float(np.corrcoef(rr, bb)[0, 1]) if rr.std() > 0 and bb.std() > 0 else None
            alpha_d = float(rr.mean() - (beta or 0) * bb.mean()) if beta is not None else None
            active = rr - bb
            info_ratio = (float(active.mean() / active.std(ddof=1)) * np.sqrt(TRADING_DAYS)
                          if active.std(ddof=1) > 0 else None)
            treynor = (float(excess.mean() * TRADING_DAYS / beta) if beta else None)
            out["benchmark"] = {
                "beta": _round(beta, 2),
                "alpha_annual_pct": _round(alpha_d * TRADING_DAYS * 100, 2) if alpha_d is not None else None,
                "correlation": _round(corr, 2),
                "information_ratio": _round(info_ratio, 2),
                "treynor_ratio": _round(treynor, 2),
            }
    return out


def _skew(a):
    n = len(a); s = a.std(ddof=0)
    if n < 3 or s == 0:
        return None
    return float(np.mean(((a - a.mean()) / s) ** 3))


def _kurtosis(a):
    n = len(a); s = a.std(ddof=0)
    if n < 4 or s == 0:
        return None
    return float(np.mean(((a - a.mean()) / s) ** 4) - 3.0)


def _cornish_fisher_var(a, alpha, skew, kurt):
    if skew is None or kurt is None:
        return None
    z = _norm_ppf(alpha)
    zcf = (z + (z**2 - 1) * skew / 6 + (z**3 - 3*z) * kurt / 24
           - (2*z**3 - 5*z) * (skew**2) / 36)
    return float(a.mean() + zcf * a.std(ddof=1))


def _norm_ppf(p):
    from math import sqrt, log
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = sqrt(-2 * log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = sqrt(-2 * log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5; rr = q * q
    return (((((a[0]*rr+a[1])*rr+a[2])*rr+a[3])*rr+a[4])*rr+a[5])*q / (((((b[0]*rr+b[1])*rr+b[2])*rr+b[3])*rr+b[4])*rr+1)


def _max_consecutive(mask):
    best = cur = 0
    for x in mask:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def _kelly(win_rate, avg_win, avg_loss):
    if avg_loss >= 0 or avg_win <= 0:
        return None
    payoff = avg_win / abs(avg_loss)
    return win_rate - (1 - win_rate) / payoff


def portfolio_metrics(benchmark_ticker="SPY"):
    """Metriche del portafoglio reale dalla serie TWR UFFICIALE (twr_engine, GIPS
    flow-adjusted). Fix 13/07 dei difetti a registro (b)+(g): prima la serie era il
    cumulative P/L ratio contaminato dai flussi e il benchmark (USD) era allineato
    per conteggio invece che per data -> beta artefatto (0,04 nel memo #42).
    Dal 25/07 il benchmark viene da benchmark_series (serie UFFICIALE EUR
    total-return sul calendario TWR, la stessa di F2): fonte unica, niente
    builder duplicato. Fallback legacy dichiarato in _source se il motore TWR
    non risponde."""
    if not NP_OK:
        return {"error": "numpy non disponibile"}

    # 1) Serie ufficiale: indice TWR flow-adjusted (F3), con le date per l'allineamento
    rets, ret_dates, serie_src, twr_p = None, None, None, None
    try:
        from bellomberg.portfolio.twr_engine import compute_twr_payload
        p = compute_twr_payload()
        idx = p.get("twr_index") or []
        dts = p.get("dates") or []
        if not p.get("error") and len(idx) >= 11 and len(dts) == len(idx):
            twr_p = p
            arr = np.asarray(idx, dtype=float)
            rets = np.diff(arr) / arr[:-1]
            ret_dates = [str(d)[:10] for d in dts[1:]]
            serie_src = "twr_index ufficiale (twr_engine, GIPS flow-adjusted)"
    except Exception:
        rets = None
    if rets is None:
        # Fallback legacy: serie contaminata dai flussi (difetto b) — dichiarato in output
        try:
            from bellomberg.portfolio.portfolio_analytics import compute_nav_history
            nav = compute_nav_history()
        except Exception as e:
            return {"error": "nav history: " + str(e)}
        # Un errore DICHIARATO dal NAV (p.es. negozio dei prezzi speciali assente) va
        # propagato COM'E': tradurlo in «storico insufficiente» sarebbe un motivo falso —
        # vero per il numero di punti, falso sulla causa (review 05/09, lotto 6).
        if isinstance(nav, dict) and nav.get("error"):
            return {"error": "nav history: " + str(nav["error"]),
                    "negozio_prezzi": nav.get("negozio_prezzi")}
        pnl = nav.get("pnl_eur") or []
        cb = nav.get("cost_basis_eur") or []
        if len(pnl) < 10:
            return {"error": "storico NAV insufficiente", "n": len(pnl)}
        ratio = np.array([1 + (p_ / c if c else 0) for p_, c in zip(pnl, cb)], dtype=float)
        rets = np.diff(ratio) / ratio[:-1]
        d_ = nav.get("dates") or []
        ret_dates = [str(x)[:10] for x in d_[1:]] if len(d_) == len(ratio) else None
        serie_src = "LEGACY cumulative P/L ratio (CONTAMINATA dai flussi: twr_engine non disponibile)"
    finite = np.isfinite(rets)
    if ret_dates is not None and len(ret_dates) == len(rets):
        ret_dates = [d for d, ok in zip(ret_dates, finite) if ok]
    else:
        ret_dates = None
    rets = np.asarray(rets, dtype=float)[finite]

    # 2) Benchmark UFFICIALE in EUR total-return allineato per DATA sul calendario
    # TWR (25/07, fonte unica: benchmark_series — la STESSA serie che consuma F2;
    # prima qui c'era un builder yfinance duplicato e diverso da quello di F2)
    bench_pair, bench_note = None, "benchmark non disponibile"
    try:
        from bellomberg.market_data.benchmark_series import compute_benchmark_series
        bs = compute_benchmark_series(ticker=benchmark_ticker, twr_payload=twr_p)
        if bs.get("error"):
            bench_note = "benchmark non disponibile: " + str(bs["error"])
        else:
            # i giorni CARRY (benchmark fermo, book che si muove: weekend crypto,
            # festivi US) sono esclusi dal pairing beta — stessa semantica del
            # builder pre-25/07, che vedeva solo i giorni nativi del benchmark;
            # la serie PIENA col carry resta quella giusta per grafico/mensili
            carried = {d for d, c in zip(bs["dates"], bs.get("carried_flags") or [])
                       if c}
            b_rets = {d: r for d, r in zip(bs["dates"][1:], bs["ret_daily"])
                      if d not in carried}
            if ret_dates:
                common = [d for d in ret_dates if d in b_rets]
                if len(common) >= 10:
                    keep = set(common)
                    r_al = np.array([r for r, d in zip(rets, ret_dates) if d in keep], dtype=float)
                    b_al = np.array([b_rets[d] for d in ret_dates if d in keep], dtype=float)
                    bench_pair = (r_al, b_al)
                    bench_note = ("allineato per DATA (" + str(len(common))
                                  + " giorni comuni), benchmark ufficiale EUR total-return"
                                  + (", " + str(bs["carried_days"])
                                     + "g carry-forward esclusi dal pairing"
                                     if bs.get("carried_days") else ""))
                else:
                    # review B1: sovrapposizione corta NON e' "date mancanti" —
                    # niente tail-align posizionale (classe "beta artefatto 0,04")
                    bench_note = ("sovrapposizione insufficiente col benchmark: "
                                  + str(len(common)) + " giorni comuni (minimo 10)")
            elif b_rets:
                b_arr = np.array(bs["ret_daily"], dtype=float)
                bench_pair = (rets, b_arr)
                bench_note = ("date portafoglio non disponibili: tail-align legacy "
                              "(benchmark ufficiale EUR total-return)")
    except Exception as e:
        bench_pair, bench_note = None, "benchmark non disponibile: " + str(e)

    try:
        from bellomberg.market_data.market_inputs import get_risk_free
        _rf = get_risk_free("EUR")
    except Exception:
        _rf = 0.03
    # Metriche headline sulla serie ufficiale PIENA; blocco benchmark sulla coppia allineata
    m = compute_metrics(rets, rf_annual=_rf)
    if bench_pair is not None:
        mb = compute_metrics(bench_pair[0], rf_annual=_rf, benchmark=bench_pair[1])
        if isinstance(mb, dict) and mb.get("benchmark"):
            m["benchmark"] = mb["benchmark"]
    m["risk_free_used"] = _rf
    m["_source"] = "advanced_metrics.portfolio_metrics — serie: " + serie_src
    m["benchmark_ticker"] = benchmark_ticker
    m["benchmark_alignment"] = bench_note
    return m


def reconcile_betas(threshold=0.35):
    """Guardrail di riconciliazione (voce 13/07, da audit memo #42): confronta il
    beta del book dai 3 motori — advanced_metrics (serie TWR vs SPY in EUR),
    portfolio_risk (beta_vs_spy) e factor model (beta_market FF regionale).
    NB: non sono lo stesso stimatore (finestre e benchmark diversi), una
    divergenza moderata e' fisiologica; oltre soglia il verdetto e' UNRELIABLE
    e il beta NON va usato come argomento decisionale (nel memo #42 un beta
    artefatto 0,04 ha deciso da solo il "no hedge"). Ogni fonte e' opzionale:
    chi fallisce finisce in sources_failed, non abbatte il guardrail."""
    if not NP_OK:
        return {"error": "numpy non disponibile"}
    betas, failed = {}, {}
    try:
        m = portfolio_metrics()
        b = (m.get("benchmark") or {}).get("beta") if isinstance(m, dict) else None
        if b is not None:
            betas["advanced_metrics_twr"] = float(b)
        else:
            failed["advanced_metrics_twr"] = str((m or {}).get("error")
                                                 or "beta assente (benchmark non disponibile)")
    except Exception as e:
        failed["advanced_metrics_twr"] = str(e)
    try:
        from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
        r = compute_portfolio_risk()
        b = (r.get("portfolio") or {}).get("beta_vs_spy") if isinstance(r, dict) else None
        if b is not None:
            betas["portfolio_risk_spy"] = float(b)
        else:
            failed["portfolio_risk_spy"] = str((r or {}).get("error") or "beta_vs_spy assente")
    except Exception as e:
        failed["portfolio_risk_spy"] = str(e)
    try:
        from bellomberg.portfolio.portfolio_factors import compute_portfolio_factors
        f = compute_portfolio_factors()
        b = (f.get("portfolio_aggregate") or {}).get("beta_market") if isinstance(f, dict) else None
        if b is not None:
            betas["factor_model_mkt"] = float(b)
        else:
            failed["factor_model_mkt"] = str((f or {}).get("error") or "beta_market assente")
    except Exception as e:
        failed["factor_model_mkt"] = str(e)

    out = {"betas": {k: round(v, 3) for k, v in betas.items()},
           "sources_failed": failed,
           "threshold": threshold,
           "definitions": {
               "advanced_metrics_twr": "serie TWR ufficiale vs benchmark ufficiale EUR total-return (benchmark_series), allineati per data",
               "portfolio_risk_spy": "rendimenti book in EUR vs SPY convertito in EUR, ~1y (fix E4 22/07)",
               "factor_model_mkt": "loading Mkt-RF composito FF regionale, ~3y",
           },
           "_source": "advanced_metrics.reconcile_betas (guardrail 13/07)"}
    if len(betas) < 2:
        out["verdict"] = "INSUFFICIENT_SOURCES"
        out["note"] = "servono almeno 2 motori per riconciliare"
        return out
    vals = list(betas.values())
    out["max_spread"] = round(max(vals) - min(vals), 3)
    if out["max_spread"] > threshold:
        out["verdict"] = "UNRELIABLE"
        out["note"] = ("i beta divergono oltre soglia: NON usare il beta come argomento "
                       "decisionale finche' non riconciliato")
    else:
        out["verdict"] = "RECONCILED"
        out["beta_consensus"] = round(float(np.median(vals)), 2)
    return out


if __name__ == "__main__":
    import json
    rng = np.random.default_rng(1)
    rets = rng.normal(0.0006, 0.01, 400)
    bench = rng.normal(0.0004, 0.009, 400)
    m = compute_metrics(rets, rf_annual=0.03, benchmark=bench)
    print(json.dumps(m, indent=1))
