"""
BELLOMBERG - GARCH Volatility Forecasting Engine

Stima modelli GARCH(1,1) e GJR-GARCH(1,1) sui rendimenti del portfolio aggregato.
Forecast conditional volatility a 1d / 5d / 22d con intervalli di confidenza.

PRINCIPI ANTI-ALLUCINAZIONE:
1. Tutti i parametri stimati via maximum likelihood (libreria 'arch' di K. Sheppard).
2. Diagnostics OBBLIGATORI riportati:
   - Ljung-Box test su residui standardizzati (autocorrelation residua)
   - ARCH-LM test (presenza di ARCH effects nei residui)
   - Persistence = alpha + beta (deve essere < 1 per stazionarieta')
   - AIC / BIC / log-likelihood
3. Model selection automatica GARCH vs GJR-GARCH:
   - Si stima GJR; se gamma (asymmetry term) e' significativo a 5% si usa GJR,
     altrimenti GARCH(1,1) base.
4. Sample size minimo: 250 osservazioni (1 anno trading).
5. Distribution: Student-t (df stimato) per accomodare fat-tails empiriche.
6. Refit window: ultimi 500 trading days max per evitare regime shift troppo lontani.

References:
- Bollerslev (1986) "Generalized autoregressive conditional heteroskedasticity", JoE 31(3)
- Glosten, Jagannathan, Runkle (1993) "On the Relation between the Expected Value
  and the Volatility of the Nominal Excess Return on Stocks", JoF 48(5)
- Engle (1982) ARCH foundational paper
- Sheppard, K. "arch" library docs: https://arch.readthedocs.io

API:
  compute_portfolio_garch(force=False) -> dict
"""
import time
import traceback
from datetime import datetime
from typing import Dict, Any, Optional

try:
    import numpy as np
    import pandas as pd
    NUMPY_OK = True
except ImportError:
    NUMPY_OK = False

try:
    import yfinance as yf
    YF_OK = True
except ImportError:
    YF_OK = False

try:
    from arch import arch_model
    from arch.unitroot import ADF
    import statsmodels.api as sm
    ARCH_OK = True
except ImportError:
    ARCH_OK = False

from bellomberg.storage.memory_db import MemoryDB
from bellomberg.core.presentation import message as _message, render_payload


CACHE_TTL_SEC = 1800  # 30 min
_CACHE: Dict[str, Any] = {"ts": 0, "data": None}

MIN_OBSERVATIONS = 250
MAX_OBSERVATIONS = 500


def _log(msg: str):
    # audit/11 §4: stesso guard di portfolio_analytics (fix 13/07, pipe morta Electron)
    try:
        print(f"[GARCH] {msg}", flush=True)
    except OSError:
        pass


# I simboli senza serie prezzi utile stanno nel NEGOZIO PRIVATO dei prezzi speciali
# (negozi_privati.carica_prezzi_speciali: data/prezzi_speciali.json, forma in
# prezzi_speciali.example.json), riletto A OGNI CHIAMATA. Qui il negozio assente NON ferma
# il calcolo: il perimetro si rinormalizza da solo e il simbolo non saltato finisce in
# `excluded_tickers` — ma «escluso perche' senza storia» non e' «escluso perche' non si
# scarica», quindi l'origine del negozio va DICHIARATA nel payload accanto alla lista.
def prezzi_speciali() -> Dict[str, Any]:
    """L'esito INTERO del negozio: {"prezzi": {...}, "origine": ..., "motivo": ...}."""
    from bellomberg.storage.negozi_privati import carica_prezzi_speciali
    return carica_prezzi_speciali()


def _yf_ticker(ticker: str, salta: frozenset) -> Optional[str]:
    """`salta` arriva dal chiamante (letto UNA volta per giro): senza default, cosi' un
    punto di chiamata dimenticato e' un TypeError e non un insieme vuoto zitto."""
    t = ticker.strip().upper()
    if t in salta:
        return None
    return t


def _get_portfolio_returns(lookback_days: int = MAX_OBSERVATIONS, *, salta: frozenset = None,
                           snap=None):
    """Scarica prezzi delle holdings e computa portfolio returns ponderati.
    Ritorna (Series, meta): meta dichiara base valutaria e campione (review quant
    22/07: la vol GARCH era su rendimenti in valuta LOCALE, base mai dichiarata,
    mentre il VaR ufficiale e' in EUR).
    `salta` e' obbligatorio (keyword): viene dal negozio dei prezzi speciali, letto una
    volta per giro dal chiamante. None = chiamata senza cablaggio, ed e' un errore subito,
    non un insieme vuoto zitto."""
    if salta is None:
        raise TypeError(_message('_get_portfolio_returns: manca `salta` (il negozio dei prezzi speciali va letto dal chiamante e passato qui)', '_get_portfolio_returns: missing `salta` (the caller must read and pass the special price store)'))
    if snap is None:
        db = MemoryDB()
        snap = db.get_portfolio_summary()
    if snap.get("fx_incomplete"):
        return None, {"error": _message("FX incompleto: pesi GARCH EUR n.d. ({currencies})", "Incomplete FX: EUR GARCH weights unavailable ({currencies})", currencies=", ".join(snap["fx_incomplete"]))}
    positions = snap.get("positions", [])
    if not positions:
        return None, {}

    total_eur = sum((p.get("valore_mercato") or 0) for p in positions)
    if total_eur <= 0:
        return None, {}

    yf_to_weight: Dict[str, float] = {}
    cur_of: Dict[str, str] = {}
    for p in positions:
        sym = _yf_ticker(p["ticker"], salta)
        if not sym:
            continue
        w = (p.get("valore_mercato") or 0) / total_eur
        if w <= 0:
            continue
        yf_to_weight[sym] = w
        cur_of[sym] = (p.get("valuta") or "EUR")

    if not yf_to_weight:
        return None, {}

    symbols = list(yf_to_weight.keys())
    period_str = "2y" if lookback_days > 252 else "1y"
    try:
        from bellomberg.cli.price_updater import AliasFontiError, data_ticker_map
        dl_map = data_ticker_map(symbols)
        raw = yf.download(list(dl_map.values()), period=period_str, progress=False,
                           auto_adjust=True, threads=True)
        _ren = {dato: reale for reale, dato in dl_map.items() if dato != reale}
        if _ren:
            raw = (raw.rename(columns=_ren, level=-1)
                   if isinstance(raw.columns, pd.MultiIndex) else raw.rename(columns=_ren))
        prices = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        if isinstance(prices, pd.Series):
            prices = prices.to_frame(symbols[0])
    except AliasFontiError:
        raise
    except Exception as e:
        _log(f"yf download failed: {e}")
        return None, {}

    returns = prices.pct_change().dropna(how="all")
    if returns.empty:
        return None, {}

    # fix review quant 22/07: stessa conversione EUR del VaR ufficiale
    # (portfolio_risk), FX mancante = serie dichiarata in valuta locale.
    from bellomberg.portfolio.portfolio_risk import _weighted_portfolio_returns, _convert_returns_to_eur
    returns, fx_meta = _convert_returns_to_eur(returns, cur_of, period=period_str)

    # Renormalize weights over actually downloaded symbols
    available = [s for s in symbols if s in returns.columns]
    if not available:
        return None, {}
    w_arr = np.array([yf_to_weight[s] for s in available])
    w_arr = w_arr / w_arr.sum()

    # fix 14/07 (stesso vizio del risk engine): fillna(0) SMORZAVA la vol del
    # portafoglio -> media pesata per-giorno sui soli nomi disponibili
    port_ret, sample_meta = _weighted_portfolio_returns(returns, w_arr, available)
    if len(port_ret) > lookback_days:
        port_ret = port_ret.iloc[-lookback_days:]
    meta = {
        "returns_basis": (_message('EUR (FX convertito per-serie)', 'EUR (FX converted per series)') if fx_meta.get("converted")
                          else _message("valuta locale", "local currency")),
        "fx_conversion": fx_meta,
        "sample_meta": sample_meta,
        "excluded_tickers": [s for s in symbols if s not in available],
    }
    return port_ret.dropna(), meta


def _diagnostic_tests(std_resid: pd.Series) -> Dict[str, Any]:
    """Ljung-Box su residui standardizzati + ARCH-LM test."""
    out = {}
    try:
        # Ljung-Box per autocorrelazione residua (lag 10)
        lb = sm.stats.acorr_ljungbox(std_resid, lags=[10], return_df=True)
        out["ljung_box_pvalue"] = round(float(lb["lb_pvalue"].iloc[0]), 4)
        out["ljung_box_interpretation"] = (
            _message('OK: residui non autocorrelati', 'OK: residuals are not autocorrelated') if out["ljung_box_pvalue"] > 0.05
            else _message('WARN: residui ancora autocorrelati - modello potenzialmente mal specificato', 'WARN: residuals remain autocorrelated - model may be misspecified')
        )
    except Exception:
        out["ljung_box_pvalue"] = None

    try:
        # ARCH-LM test sui residui quadrati
        arch_lm = sm.stats.diagnostic.het_arch(std_resid, nlags=10)
        out["arch_lm_pvalue"] = round(float(arch_lm[1]), 4)
        out["arch_lm_interpretation"] = (
            _message("OK: no ARCH effects residui (modello cattura volatilita')", 'OK: no residual ARCH effects (model captures volatility)') if out["arch_lm_pvalue"] > 0.05
            else _message("WARN: ARCH effects residui - servirebbe ordine GARCH piu' alto", 'WARN: residual ARCH effects - a higher GARCH order may be needed')
        )
    except Exception:
        out["arch_lm_pvalue"] = None
    return out


def compute_portfolio_garch(force: bool = False) -> Dict[str, Any]:
    """Stima GARCH(1,1) e GJR-GARCH(1,1) sul portfolio, ritorna model selection + forecast."""
    try:
        _guard_snap = MemoryDB().get_portfolio_summary()
    except Exception as e:
        return {"error": _message('portfolio fetch failed: {v0}', 'Portfolio fetch failed: {v0}', v0=e),
                "timestamp": datetime.now().isoformat()}
    if _guard_snap.get("fx_incomplete"):
        return {"error": _message("FX incompleto: pesi GARCH EUR n.d. ({currencies})", "Incomplete FX: EUR GARCH weights unavailable ({currencies})", currencies=", ".join(_guard_snap["fx_incomplete"])),
                "timestamp": datetime.now().isoformat()}
    if not force and _CACHE["data"] and (time.time() - _CACHE["ts"] < CACHE_TTL_SEC):
        return render_payload(_CACHE["data"])

    if not (NUMPY_OK and YF_OK and ARCH_OK):
        return {"error": _message('librerie mancanti (arch/statsmodels/yfinance)', 'Missing libraries (arch/statsmodels/yfinance)'),
                "timestamp": datetime.now().isoformat()}

    # Il negozio si legge QUI, una volta per giro, e si passa giu': se lo leggesse
    # _get_portfolio_returns, le prove che la sostituiscono salterebbero il cablaggio.
    _prezzi = prezzi_speciali()

    try:
        port_ret, ret_meta = _get_portfolio_returns(salta=_prezzi["prezzi"]["senza_yfinance"])
    except Exception as e:
        return {"error": _message('portfolio returns failed: {v0}', 'Portfolio returns failed: {v0}', v0=e),
                "timestamp": datetime.now().isoformat()}

    if port_ret is None or len(port_ret) < MIN_OBSERVATIONS:
        return {"error": _message('sample insufficiente (need {v0}, got {v1})', 'Insufficient sample (need {v0}, got {v1})', v0=MIN_OBSERVATIONS, v1=0 if port_ret is None else len(port_ret)),
                "timestamp": datetime.now().isoformat()}

    # arch lib lavora con returns in % (per stabilita' numerica)
    returns_pct = port_ret * 100

    diagnostics_pre = {}
    try:
        # ADF stationarity check sui returns (devono essere stazionari per GARCH)
        adf = ADF(returns_pct)
        diagnostics_pre["adf_pvalue"] = round(float(adf.pvalue), 4)
        diagnostics_pre["adf_stationary"] = adf.pvalue < 0.05
    except Exception:
        pass

    # 1) Fit GJR-GARCH(1,1,1) con distribuzione t (fat tails)
    try:
        gjr_model = arch_model(returns_pct, mean="Constant",
                                vol="GARCH", p=1, o=1, q=1, dist="t")
        gjr_res = gjr_model.fit(disp="off", show_warning=False)
        gjr_params = gjr_res.params
        gjr_pvalues = gjr_res.pvalues
        gjr_aic = float(gjr_res.aic)
        gjr_loglik = float(gjr_res.loglikelihood)
        gjr_gamma_pvalue = float(gjr_pvalues.get("gamma[1]", 1.0))
        gjr_persistence = float(gjr_params.get("alpha[1]", 0)
                                + 0.5 * gjr_params.get("gamma[1]", 0)
                                + gjr_params.get("beta[1]", 0))
    except Exception as e:
        _log(f"GJR fit failed: {e}")
        return {"error": _message('Stima GJR-GARCH fallita: {v0}', 'GJR-GARCH fit failed: {v0}', v0=e),
                "timestamp": datetime.now().isoformat(),
                "traceback": traceback.format_exc()[:500]}

    # 2) Fit GARCH(1,1) base per confronto
    try:
        garch_model = arch_model(returns_pct, mean="Constant",
                                  vol="GARCH", p=1, o=0, q=1, dist="t")
        garch_res = garch_model.fit(disp="off", show_warning=False)
        garch_aic = float(garch_res.aic)
        garch_loglik = float(garch_res.loglikelihood)
        garch_persistence = float(garch_res.params.get("alpha[1]", 0)
                                   + garch_res.params.get("beta[1]", 0))
    except Exception as e:
        _log(_message('GARCH base fit failed: {v0}', 'GARCH fit failed: {v0}', v0=e))
        return {"error": _message('Stima GARCH fallita: {v0}', 'GARCH fit failed: {v0}', v0=e),
                "timestamp": datetime.now().isoformat()}

    # fix review quant 22/07: convergenza MLE mai verificata = fallback silenzioso.
    # flag 0 = convergiuto (arch); un fit non convergiuto non puo' vincere la selezione.
    gjr_conv = int(getattr(gjr_res, "convergence_flag", 0) or 0)
    garch_conv = int(getattr(garch_res, "convergence_flag", 0) or 0)
    if gjr_conv != 0 and garch_conv != 0:
        return {"error": _message('MLE non convergiuta (GJR flag={v0}, GARCH flag={v1}): parametri non pubblicabili', 'MLE did not converge (GJR flag={v0}, GARCH flag={v1}): parameters cannot be published', v0=gjr_conv, v1=garch_conv),
                "timestamp": datetime.now().isoformat()}

    # Model selection: GJR vince se gamma significativo a 5% E AIC inferiore
    use_gjr = (gjr_gamma_pvalue < 0.05) and (gjr_aic < garch_aic)
    convergence_note = None
    if use_gjr and gjr_conv != 0:
        use_gjr = False
        convergence_note = _message('GJR scartato: MLE non convergiuta (flag={v0})', 'GJR excluded: MLE did not converge (flag={v0})', v0=gjr_conv)
    elif (not use_gjr) and garch_conv != 0:
        use_gjr = True
        convergence_note = _message('GARCH base scartato: MLE non convergiuta (flag={v0})', 'Base GARCH excluded: MLE did not converge (flag={v0})', v0=garch_conv)
    chosen = "GJR-GARCH(1,1,1)" if use_gjr else "GARCH(1,1)"
    chosen_res = gjr_res if use_gjr else garch_res
    chosen_aic = gjr_aic if use_gjr else garch_aic
    chosen_loglik = gjr_loglik if use_gjr else garch_loglik
    chosen_persistence = gjr_persistence if use_gjr else garch_persistence

    chosen_params = {k: round(float(v), 6) for k, v in chosen_res.params.items()}
    chosen_pvalues = {k: round(float(v), 4) for k, v in chosen_res.pvalues.items()}

    # Standardized residuals diagnostics
    std_resid = chosen_res.resid / chosen_res.conditional_volatility
    diag_post = _diagnostic_tests(pd.Series(std_resid))

    # Current vol (in %, NB returns_pct era in %): per ritornare alla scala
    # decimale del return originale divido per 100
    current_vol_pct_daily = float(chosen_res.conditional_volatility.iloc[-1])  # in % daily
    current_vol_annual_pct = round(current_vol_pct_daily * np.sqrt(252), 2)

    # Forecast h-step ahead — fix review quant 22/07: (a) i "CI 95%" erano
    # vol*0.85/vol*1.15 HARDCODED spacciati per intervallo; ora sono i quantili
    # 2.5/97.5 della vol condizionata SIMULATA dal modello (method='simulation');
    # (b) vol_annualized_pct e' la vol del SOLO giorno t+h (semantica arch):
    # per confrontare con una IV a h giorni si aggiunge vol_horizon_ann_pct =
    # sqrt(media delle varianze dei giorni 1..h) annualizzata.
    forecasts = {}
    sqrt252 = float(np.sqrt(252))
    try:
        fc_point = chosen_res.forecast(horizon=22, reindex=False)  # analitico
        var_point = fc_point.variance.iloc[-1].to_numpy(dtype=float)  # (22,) in pct^2
        fc_sim = chosen_res.forecast(horizon=22, method="simulation",
                                     simulations=2000, reindex=False)
        var_paths = np.asarray(fc_sim.simulations.residual_variances, dtype=float)[-1]  # (nsim, 22)
        for horizon in [1, 5, 22]:
            vol_h_daily_pct = float(np.sqrt(var_point[horizon - 1]))
            vol_h_annual_pct = vol_h_daily_pct * sqrt252
            vol_horizon_ann = float(np.sqrt(var_point[:horizon].mean())) * sqrt252
            lo_var, hi_var = np.percentile(var_paths[:, horizon - 1], [2.5, 97.5])
            forecasts[f"{horizon}d"] = {
                "vol_daily_pct": round(vol_h_daily_pct, 3),
                "vol_annualized_pct": round(vol_h_annual_pct, 2),   # vol del giorno t+h
                "vol_horizon_ann_pct": round(vol_horizon_ann, 2),   # vol media 1..h (vs IV)
                "ci95_low_pct": round(float(np.sqrt(max(lo_var, 0.0))) * sqrt252, 2),
                "ci95_high_pct": round(float(np.sqrt(hi_var)) * sqrt252, 2),
            }
    except Exception as e:
        for horizon in [1, 5, 22]:
            forecasts[f"{horizon}d"] = {"error": str(e)}

    result = {
        "timestamp": datetime.now().isoformat(),
        "model_chosen": chosen,
        "selection_rationale": (
            _message("GJR-GARCH selezionato: gamma p-value={p:.4f} (<0.05), AIC {gjr:.1f} < GARCH AIC {garch:.1f}", "GJR-GARCH wins: gamma p-value={p:.4f} (<0.05), AIC {gjr:.1f} < GARCH AIC {garch:.1f}", p=gjr_gamma_pvalue, gjr=gjr_aic, garch=garch_aic)
            if use_gjr else
            _message("GARCH(1,1) selezionato: GJR gamma p-value={p:.4f} non significativo, o AIC {garch:.1f} <= GJR {gjr:.1f}", "GARCH(1,1) wins: GJR gamma p-value={p:.4f} not significant, or AIC {garch:.1f} <= GJR {gjr:.1f}", p=gjr_gamma_pvalue, gjr=gjr_aic, garch=garch_aic)
        ),
        "n_obs": int(len(returns_pct)),
        "distribution": _message('Student-t (code pesanti)', 'Student-t (fat-tails)'),
        "parameters": chosen_params,
        "pvalues": chosen_pvalues,
        "persistence": round(chosen_persistence, 4),
        "persistence_interpretation": (
            _message('OK (<1): processo stazionario, shock decay', 'OK (<1): stationary process, shocks decay') if chosen_persistence < 1
            else _message('WARN: near-unit-root o esplosivo - rivedere modello', 'WARN: near-unit-root or explosive - review model')
        ),
        "log_likelihood": round(chosen_loglik, 2),
        "aic": round(chosen_aic, 2),
        "current_vol_annual_pct": current_vol_annual_pct,
        "forecast_vol": forecasts,
        "forecast_semantics": (_message("vol_annualized_pct = vol del SOLO giorno t+h; vol_horizon_ann_pct = vol media sui giorni 1..h (quella confrontabile con una IV a h giorni); CI 95% = quantili 2.5/97.5 della vol condizionata simulata dal modello (a 1g la varianza GARCH e' quasi deterministica dati i parametri: CI stretto atteso; incertezza parametri non modellata, dichiarato)", 'vol_annualized_pct = volatility for ONLY day t+h; vol_horizon_ann_pct = mean volatility over days 1..h (comparable to h-day IV); 95% CI = 2.5/97.5 quantiles of model-simulated conditional volatility (at 1 day, GARCH variance is almost deterministic given the parameters: a narrow CI is expected; parameter uncertainty is not modeled, as declared)')),
        # review quant 22/07: base valutaria e campione DICHIARATI (prima la vol
        # GARCH era in valuta locale, zitta, accanto a un VaR ufficiale in EUR)
        "returns_basis": ret_meta.get("returns_basis", _message("n.d.", "n/a")),
        "fx_conversion": ret_meta.get("fx_conversion", {}),
        "sample_meta": ret_meta.get("sample_meta", {}),
        "excluded_tickers": ret_meta.get("excluded_tickers", []),
        # lotto 6 criterio (1): senza il negozio nessun simbolo e' saltato, e chi legge
        # `excluded_tickers` deve poter distinguere «senza storia» da «negozio assente».
        "negozio_prezzi": {"origine": _prezzi["origine"], "motivo": _prezzi["motivo"]},
        "convergence": {"gjr_flag": gjr_conv, "garch_flag": garch_conv,
                        "chosen_converged": True, "note": convergence_note},
        "diagnostics_pre_fit": diagnostics_pre,
        "diagnostics_post_fit": diag_post,
        "comparison": {
            "garch_aic": round(garch_aic, 2),
            "gjr_aic": round(gjr_aic, 2),
            "gjr_gamma_pvalue": round(gjr_gamma_pvalue, 4),
        },
    }

    _CACHE["ts"] = time.time()
    _CACHE["data"] = result
    _log(f"computed {chosen}: vol_ann_current={current_vol_annual_pct:.2f}%, "
         f"persistence={chosen_persistence:.3f}, AIC={chosen_aic:.1f}")
    return render_payload(result)


def invalidate_cache():
    _CACHE["ts"] = 0
    _CACHE["data"] = None


if __name__ == "__main__":
    import json
    r = compute_portfolio_garch(force=True)
    print(json.dumps(r, indent=2, default=str))
