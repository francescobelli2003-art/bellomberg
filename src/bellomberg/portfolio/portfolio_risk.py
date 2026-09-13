"""
BELLOMBERG - Portfolio Risk Engine
Real-time quantitative risk metrics per il Dashboard.

Calcola:
- VaR 95% e 99% (historical, 1-day horizon) in % e EUR
- Volatility annualizzata
- Sharpe ratio annualizzato (rf=0)
- Beta vs SPY
- Max drawdown 1y
- Correlation matrix delle top 8 holdings
- Alerts automatici su soglie

Cache in-memory 5 min (yfinance call e' costoso).
"""
import time
import traceback
from datetime import datetime
from typing import Dict, Any, List, Optional

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

from bellomberg.storage.memory_db import MemoryDB
from bellomberg.core.presentation import message as _message, render_payload


CACHE_TTL_SEC = 300
_CACHE: Dict[str, Any] = {"ts": 0, "data": None}

# I ticker che yfinance non gestisce (crypto custom, etc.) stanno nel NEGOZIO PRIVATO dei
# prezzi speciali (negozi_privati.carica_prezzi_speciali: data/prezzi_speciali.json, forma in
# prezzi_speciali.example.json), riletto A OGNI CHIAMATA. Qui il negozio assente NON e' neutro:
# senza la lista il simbolo entra in weights_eur e quindi in total_eur, che e' la base su cui
# scalano TUTTI i VaR in euro (nav_basis) — percio' compute_portfolio_risk si FERMA e lo dice.
def prezzi_speciali() -> Dict[str, Any]:
    """L'esito INTERO del negozio: {"prezzi": {...}, "origine": ..., "motivo": ...}."""
    from bellomberg.storage.negozi_privati import carica_prezzi_speciali
    return carica_prezzi_speciali()


def ko_negozio_prezzi(esito: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """L'errore DICHIARATO se il negozio non si legge, altrimenti None (regola PM 14/07)."""
    if esito["origine"] in ("assente", "illeggibile"):
        return {"error": _message("negozio dei prezzi speciali {origin}: {reason} — senza quella lista la base NAV dei VaR in euro cambierebbe", "Special price store {origin}: {reason} — without that list, the NAV basis for EUR VaR would change", origin=esito["origine"], reason=esito["motivo"]),
                "negozio_prezzi": {"origine": esito["origine"], "motivo": esito["motivo"]},
                "timestamp": datetime.now().isoformat()}
    return None


def _log(msg: str):
    # audit/11 §4: stesso guard di portfolio_analytics (fix 13/07) — stdout puo' essere
    # una pipe morta (backend orfano da Electron): il log non deve abbattere il calcolo.
    try:
        print(f"[RISK] {msg}", flush=True)
    except OSError:
        pass


def _yf_ticker(ticker: str, salta: frozenset) -> Optional[str]:
    """Translate internal ticker to yfinance symbol. None if not supported.
    `salta` arriva dal chiamante (letto UNA volta per giro dal negozio): senza default,
    cosi' un punto di chiamata dimenticato e' un TypeError e non un insieme vuoto zitto."""
    t = ticker.strip().upper()
    if t in salta:
        return None
    # I simboli non esclusi sono gia' nel formato accettato dalla fonte dati.
    return t


def _norm_currency(c) -> str:
    """Normalizza il codice valuta del DB. GBX/GBp = pence: la SCALA non tocca i
    pct_change, ma il pair FX deve essere GBP (review 14/07: 'EURGBX=X' esiste su
    Yahoo con UNA sola osservazione e distruggeva in NaN l'intera serie)."""
    c = (c or "").strip().upper()
    if c in ("GBX", "GBP"):  # 'GBp'.upper() == 'GBP'
        return "GBP"
    return c


def _convert_returns_to_eur(returns: "pd.DataFrame", cur_of: Dict[str, str],
                              period: str = "1y"):
    """Converte i rendimenti in EUR: r_eur = (1+r_local)/(1+r_fx) - 1, con
    r_fx = pct_change di EUR<CUR>=X (fix 14/07: tutti i VaR giravano su
    rendimenti in valuta LOCALE, FX escluso).

    period DEVE coprire l'orizzonte dei rendimenti passati (review 14/07: era
    hardcoded '3y' e un backtest a 5y perdeva in silenzio i primi 2 anni dei
    nomi non-EUR).

    Regola no-fallback: FX non disponibile O copertura FX che DISTRUGGEREBBE la
    serie -> il ticker resta in valuta locale e viene DICHIARATO; la conversione
    non deve MAI ridurre materialmente le osservazioni valide di una serie.
    Ritorna (returns_eur, meta).
    """
    curs = sorted({_norm_currency(c) for c in cur_of.values()
                   if _norm_currency(c) not in ("EUR", "")})
    meta = {"converted": [], "local_declared": []}
    if not curs:
        return returns, meta
    fx_syms = {c: "EUR" + c + "=X" for c in curs}
    try:
        fx_raw = yf.download(list(fx_syms.values()), period=period, progress=False,
                             auto_adjust=True, threads=True)
        fx_prices = fx_raw["Close"] if isinstance(fx_raw.columns, pd.MultiIndex) else fx_raw
        if isinstance(fx_prices, pd.Series):
            fx_prices = fx_prices.to_frame(list(fx_syms.values())[0])
    except Exception as e:
        meta["fx_error"] = str(e)[:100]
        meta["local_declared"] = [f"{s} ({c})" for s, c in cur_of.items()
                                  if _norm_currency(c) not in ("EUR", "")]
        return returns, meta
    out = returns.copy()
    for sym, cur in cur_of.items():
        cur = _norm_currency(cur)
        if cur in ("", "EUR") or sym not in out.columns:
            continue
        pair = fx_syms.get(cur)
        if pair is None or pair not in fx_prices.columns or fx_prices[pair].dropna().empty:
            meta["local_declared"].append(_message('{v0} ({v1}: FX non disponibile, rendimenti in valuta locale)', '{v0} ({v1}: FX unavailable, returns in local currency)', v0=sym, v1=cur))
            continue
        fx_col = fx_prices[pair].reindex(out.index)
        # guardia FX-STANTIO (review quant 22/07): la guardia 0.9 sotto misura le
        # obs distrutte, non la freschezza — un FX con poche obs INIZIALI verrebbe
        # ffillato su tutto l'orizzonte e "convertito" con cambio piatto (rischio
        # FX azzerato in silenzio). Copertura FX reale < 80% dei giorni -> serie
        # tenuta in valuta locale e DICHIARATA.
        n_fx_real = int(fx_col.notna().sum())
        if n_fx_real < 0.8 * len(out.index):
            meta["local_declared"].append(
                _message('{v0} ({v1}: FX stantio/lacunoso, quota {v2}/{v3} giorni, serie in valuta locale)', '{v0} ({v1}: stale/incomplete FX, coverage {v2}/{v3} days, series in local currency)', v0=sym, v1=cur, v2=n_fx_real, v3=len(out.index)))
            continue
        fx_ret = fx_col.ffill().pct_change()
        conv = (1 + out[sym]) / (1 + fx_ret) - 1
        # guardia anti-distruzione (review 14/07): se l'FX copre male la serie,
        # la conversione NON si applica e il buco si dichiara — mai azzerare
        # osservazioni reali in silenzio.
        n_before = int(out[sym].notna().sum())
        n_after = int(conv.notna().sum())
        if n_before > 0 and n_after < 0.9 * n_before:
            meta["local_declared"].append(
                _message('{v0} ({v1}: FX copre solo {v2}/{v3} obs, serie tenuta in valuta locale)', '{v0} ({v1}: FX covers only {v2}/{v3} observations, series kept in local currency)', v0=sym, v1=cur, v2=n_after, v3=n_before))
            continue
        out[sym] = conv
        meta["converted"].append(sym)
    return out, meta


MIN_XSECTION_WEIGHT = 0.50   # quota minima del book (per peso) presente in un giorno


def _weighted_portfolio_returns(returns: "pd.DataFrame", weights: "np.ndarray",
                                  columns: List[str]):
    """Rendimento di portafoglio per-GIORNO sui soli nomi disponibili quel giorno,
    pesi rinormalizzati sul cross-section presente (fix 14/07: il dropna(how='any')
    tagliava il campione al ticker piu' giovane e il fallback fillna(0) SMORZAVA
    la vol -> VaR sistematicamente sottostimato).

    Giorni con meno di MIN_XSECTION_WEIGHT del book per peso vengono SCARTATI
    (es. festivita' in cui tratta solo una minoranza del book).
    Ritorna (port_r, meta).
    """
    R = returns[columns]
    wser = pd.Series(weights, index=columns)
    avail_w = R.notna().mul(wser, axis=1).sum(axis=1)
    num = R.mul(wser, axis=1).sum(axis=1, min_count=1)
    port_r = (num / avail_w)[avail_w >= MIN_XSECTION_WEIGHT].dropna()
    dropped = int((avail_w < MIN_XSECTION_WEIGHT).sum())
    meta = {"obs": int(len(port_r)), "days_dropped_thin_xsection": dropped,
            "method": _message('media pesata per-giorno sui nomi disponibili (pesi rinormalizzati)', 'Daily weighted mean of available holdings (renormalized weights)')}
    return port_r, meta


def _ledoit_wolf_cc(returns_matrix: "np.ndarray"):
    """Ledoit-Wolf shrinkage verso il target a CORRELAZIONE COSTANTE (preserva le
    varianze campionarie; 'Honey, I Shrunk the Sample Covariance Matrix', 2004).
    Implementazione VERIFICATA sul porting ufficiale del covCor.m di Ledoit
    (github WLM1ke/LedoitWolf, 14/07/2026). Ritorna (cov, avg_corr, delta).
    """
    x = np.asarray(returns_matrix, dtype=float).copy()
    t, n = x.shape
    if n < 2 or t < 10:
        return np.cov(x.T) if n > 1 else np.array([[x.var()]]), None, 0.0
    x -= x.mean(axis=0, keepdims=True)
    sample_cov = x.T @ x / t
    var = np.diag(sample_cov).reshape(-1, 1)
    sqrt_var = np.sqrt(np.maximum(var, 1e-18))
    unit_cor_var = sqrt_var * sqrt_var.T
    average_cor = ((sample_cov / unit_cor_var).sum() - n) / n / (n - 1)
    prior = average_cor * unit_cor_var
    np.fill_diagonal(prior, var.ravel())
    y = x ** 2
    phi_mat = (y.T @ y) / t - sample_cov ** 2
    phi = phi_mat.sum()
    theta_mat = ((x ** 3).T @ x) / t - var * sample_cov
    np.fill_diagonal(theta_mat, 0)
    rho = (np.diag(phi_mat).sum()
           + average_cor * (1 / sqrt_var @ sqrt_var.T * theta_mat).sum())
    gamma = np.linalg.norm(sample_cov - prior, "fro") ** 2
    if gamma <= 0:
        return sample_cov, float(average_cor), 0.0
    kappa = (phi - rho) / gamma
    shrink = max(0.0, min(1.0, kappa / t))
    sigma = shrink * prior + (1 - shrink) * sample_cov
    return sigma, float(average_cor), float(shrink)


def compute_portfolio_risk(force: bool = False) -> Dict[str, Any]:
    """Returns dict con risk metrics correnti. Cached 5 min."""
    # Il negozio PRIMA della cache (review 05/09): la chiave di cache non lo contiene, quindi
    # un risultato calcolato col negozio a posto verrebbe servito per altri 5 minuti dopo che
    # il negozio si e' rotto — VaR in euro veri accanto a un buco non dichiarato.
    _prezzi = prezzi_speciali()
    _ko = ko_negozio_prezzi(_prezzi)
    if _ko:
        return _ko
    salta = _prezzi["prezzi"]["senza_yfinance"]

    if not (NUMPY_OK and YF_OK):
        return {
            "error": _message('numpy/yfinance non installati', 'numpy/yfinance not installed'),
            "timestamp": datetime.now().isoformat(),
        }

    try:
        db = MemoryDB()
        snap = db.get_portfolio_summary()
    except Exception as e:
        return {"error": _message('portfolio fetch failed: {v0}', 'Portfolio fetch failed: {v0}', v0=e), "timestamp": datetime.now().isoformat()}
    if snap.get("fx_incomplete"):
        return {"error": _message("FX incompleto: pesi rischio EUR n.d. ({currencies})", "Incomplete FX: EUR risk weights unavailable ({currencies})", currencies=", ".join(snap["fx_incomplete"])),
                "timestamp": datetime.now().isoformat()}
    if not force and _CACHE["data"] and (time.time() - _CACHE["ts"] < CACHE_TTL_SEC):
        return render_payload(_CACHE["data"])

    positions = snap.get("positions", [])
    if not positions:
        return {"error": _message("nessuna posizione", "no positions"), "timestamp": datetime.now().isoformat()}

    # Sort by weight desc, take all with valid ticker
    positions = sorted(positions, key=lambda p: p.get("peso_pct", 0) or 0, reverse=True)

    # Build ticker list for yfinance + weights (+ valuta per la conversione EUR)
    yf_map = {}  # internal_ticker -> yf_symbol
    weights_eur: Dict[str, float] = {}
    cur_of: Dict[str, str] = {}  # yf_symbol -> valuta della quotazione
    for p in positions:
        sym = _yf_ticker(p["ticker"], salta)
        if not sym:
            continue
        yf_map[p["ticker"]] = sym
        weights_eur[p["ticker"]] = float(p.get("valore_mercato", 0) or 0)
        cur_of[sym] = (p.get("valuta") or "EUR")

    if not yf_map:
        return {"error": _message('no analyzable tickers', 'No analyzable tickers'), "timestamp": datetime.now().isoformat()}

    total_eur = sum(weights_eur.values())
    if total_eur <= 0:
        return {"error": _message('zero portfolio value', 'Zero portfolio value'), "timestamp": datetime.now().isoformat()}

    weights = {t: weights_eur[t] / total_eur for t in weights_eur}

    yf_symbols = list(yf_map.values())
    _log(f"downloading 1y prices for {len(yf_symbols)} tickers + SPY")

    # Download 1y prices
    try:
        # auto_adjust=True per adj close
        from bellomberg.cli.price_updater import data_ticker_map
        dl_map = data_ticker_map(yf_symbols, riservati=("SPY",))
        download_symbols = list(dict.fromkeys(list(dl_map.values()) + ["SPY"]))
        raw = yf.download(download_symbols, period="1y", progress=False,
                           auto_adjust=True, threads=True)
        _ren = {dato: reale for reale, dato in dl_map.items() if dato != reale}
        if _ren:
            raw = (raw.rename(columns=_ren, level=-1)
                   if isinstance(raw.columns, pd.MultiIndex) else raw.rename(columns=_ren))
        if isinstance(raw.columns, pd.MultiIndex):
            prices = raw["Close"]
        else:
            prices = raw[["Close"]] if "Close" in raw.columns else raw
            if len(yf_symbols) == 1:
                prices = prices.rename(columns={"Close": yf_symbols[0]})
    except Exception as e:
        _log(f"yf download FAILED: {e}")
        _log(traceback.format_exc())
        return {"error": _message('yfinance error: {v0}', 'yfinance error: {v0}', v0=e), "timestamp": datetime.now().isoformat()}

    if prices.empty:
        return {"error": _message("nessun dato prezzi", "no price data"), "timestamp": datetime.now().isoformat()}

    returns = prices.pct_change().dropna(how="all")

    # Conversione EUR (fix 14/07: prima TUTTI i VaR giravano su rendimenti in
    # valuta locale). FX mancante = ticker dichiarato in valuta locale.
    # fix review 22/07 (E4): anche SPY va in EUR — il beta era cov(port EUR,
    # SPY USD): numeratore con la componente FX, benchmark senza.
    cur_of.setdefault("SPY", "USD")
    returns, fx_meta = _convert_returns_to_eur(returns, cur_of, period="1y")
    if fx_meta.get("local_declared"):
        _log("FX: rendimenti in valuta LOCALE dichiarati per " + ", ".join(fx_meta["local_declared"]))

    # Per-asset metrics
    per_asset: Dict[str, Any] = {}
    valid_internal_tickers: List[str] = []
    for internal_t, yf_t in yf_map.items():
        if yf_t not in returns.columns:
            continue
        r = returns[yf_t].dropna()
        if len(r) < 20:
            continue
        valid_internal_tickers.append(internal_t)
        vol_ann = float(r.std() * np.sqrt(252) * 100)
        sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
        var95 = float(np.percentile(r, 5) * 100)
        cum = (1 + r).cumprod()
        rolling_max = cum.expanding().max()
        dd = (cum - rolling_max) / rolling_max
        max_dd_asset = float(dd.min() * 100) if len(dd) > 0 else 0.0
        per_asset[internal_t] = {
            "vol_annual_pct": round(vol_ann, 2),
            "sharpe": round(sharpe, 2),
            "var_95_1d_pct": round(var95, 2),
            "max_dd_pct": round(max_dd_asset, 2),
            "weight_pct": round(weights[internal_t] * 100, 2),
        }

    if not valid_internal_tickers:
        return {"error": _message('no valid return series', 'No valid return series'), "timestamp": datetime.now().isoformat()}

    # Portfolio metrics: weighted returns
    valid_yf = [yf_map[t] for t in valid_internal_tickers]
    valid_weights = np.array([weights[t] for t in valid_internal_tickers])
    valid_weights = valid_weights / valid_weights.sum()  # renormalize (skip-adjusted)

    # fix 14/07: niente piu' dropna(how='any') (tagliava al ticker piu' giovane)
    # ne' fillna(0) (smorzava la vol): media pesata per-giorno sui disponibili.
    port_r, sample_meta = _weighted_portfolio_returns(returns, valid_weights, valid_yf)
    if port_r.empty:
        return {"error": _message('serie di portafoglio vuota dopo il filtro cross-section', 'Portfolio series empty after cross-section filter'),
                "timestamp": datetime.now().isoformat()}

    port_vol_ann = float(port_r.std() * np.sqrt(252) * 100)
    port_sharpe = float(port_r.mean() / port_r.std() * np.sqrt(252)) if port_r.std() > 0 else 0.0
    port_var95_pct = float(np.percentile(port_r, 5) * 100)
    port_var99_pct = float(np.percentile(port_r, 1) * 100)
    port_var95_eur = float(port_var95_pct / 100 * total_eur)
    port_var99_eur = float(port_var99_pct / 100 * total_eur)

    # Max DD portfolio
    cum_port = (1 + port_r).cumprod()
    rolling_max_port = cum_port.expanding().max()
    dd_port = (cum_port - rolling_max_port) / rolling_max_port
    max_dd_port = float(dd_port.min() * 100) if len(dd_port) > 0 else 0.0

    # Beta vs SPY
    beta_spy = 0.0
    if "SPY" in returns.columns:
        spy_r = returns["SPY"].dropna()
        common_idx = port_r.index.intersection(spy_r.index)
        if len(common_idx) > 20:
            pr = port_r.loc[common_idx]
            sr = spy_r.loc[common_idx]
            cov = float(pr.cov(sr))
            var_spy = float(sr.var())
            beta_spy = cov / var_spy if var_spy > 0 else 0.0

    # Correlation matrix top 8 — con Ledoit-Wolf shrinkage (fix 14/07: la sample
    # cov 1y e' rumore; target a correlazione costante, varianze preservate)
    top8 = valid_internal_tickers[:8]
    top8_yf = [yf_map[t] for t in top8]
    corr_data = returns[top8_yf].dropna(how="any")
    corr_matrix: List[List[float]] = []
    corr_meta: Dict[str, Any] = {}
    if len(corr_data) > 20:
        try:
            lw_cov, lw_rbar, lw_delta = _ledoit_wolf_cc(corr_data.values)
            _sd = np.sqrt(np.maximum(np.diag(lw_cov), 1e-18))
            corr_np = np.clip(lw_cov / np.outer(_sd, _sd), -1.0, 1.0)
            corr_matrix = [[round(float(corr_np[i, j]), 2) for j in range(len(top8))]
                           for i in range(len(top8))]
            corr_meta = {"estimator": "ledoit_wolf_constant_correlation",
                         "shrinkage_delta": round(lw_delta, 3),
                         "avg_corr_target": round(lw_rbar, 3) if lw_rbar is not None else None,
                         "obs": int(len(corr_data))}
        except Exception as _le:
            corr_df = corr_data.corr()
            corr_matrix = [[round(float(corr_df.iloc[i, j]), 2) for j in range(len(top8))] for i in range(len(top8))]
            corr_meta = {"estimator": "sample (Ledoit-Wolf fallito: " + str(_le)[:60] + ")",
                         "note": _message("Ledoit-Wolf fallito: {error}; correlazione campionaria dichiarata", "Ledoit-Wolf failed: {error}; sample correlation declared", error=str(_le)[:60]),
                         "obs": int(len(corr_data))}

    # Alerts
    alerts: List[Dict[str, Any]] = []
    if abs(port_var99_pct) > 4:
        alerts.append({
            "level": "high", "metric": "VaR 99% 1d",
            "message": _message('VaR 99% 1d critico: {v0:.2f}% (EUR {v1:,.0f})', 'Critical 1-day VaR99: {v0:.2f}% (EUR {v1:,.0f})', v0=port_var99_pct, v1=port_var99_eur),
        })
    elif abs(port_var95_pct) > 3:
        alerts.append({
            "level": "med", "metric": "VaR 95% 1d",
            "message": _message('VaR 95% 1d elevato: {v0:.2f}%', 'High 1-day VaR95: {v0:.2f}%', v0=port_var95_pct),
        })
    if abs(beta_spy) > 1.3:
        alerts.append({
            "level": "med", "metric": "Beta",
            "message": _message('Beta vs SPY elevato: {v0:.2f}', 'High beta vs SPY: {v0:.2f}', v0=beta_spy),
        })
    if max_dd_port < -20:
        alerts.append({
            "level": "high", "metric": "Max Drawdown 1y",
            "message": _message('Drawdown 1y significativo: {v0:.1f}%', 'Significant 1-year drawdown: {v0:.1f}%', v0=max_dd_port),
        })
    if port_vol_ann > 30:
        alerts.append({
            "level": "med", "metric": "Volatility",
            "message": _message('Volatility annualizzata alta: {v0:.1f}%', 'High annualized volatility: {v0:.1f}%', v0=port_vol_ann),
        })
    if port_sharpe < 0:
        alerts.append({
            "level": "med", "metric": "Sharpe",
            "message": _message('Sharpe ratio negativo: {v0:.2f} - rivedere risk/reward', 'Negative Sharpe ratio: {v0:.2f} - review risk/reward', v0=port_sharpe),
        })

    # Concentration risk: top holding > 25%
    top_pos = positions[0] if positions else None
    if top_pos and (top_pos.get("peso_pct") or 0) > 25:
        alerts.append({
            "level": "med", "metric": "Concentration",
            "message": _message('Top holding {v0} = {v1:.1f}% (concentrazione elevata)', 'Top holding {v0} = {v1:.1f}% (high concentration)', v0=top_pos['ticker'], v1=top_pos['peso_pct']),
        })

    # Skipped tickers report
    skipped = [p["ticker"] for p in positions
               if p["ticker"] not in valid_internal_tickers and (p.get("peso_pct") or 0) > 0.5]

    result = {
        "timestamp": datetime.now().isoformat(),
        "nav_eur": total_eur,
        "portfolio": {
            "vol_annual_pct": round(port_vol_ann, 2),
            "sharpe": round(port_sharpe, 2),
            "var_95_1d_pct": round(port_var95_pct, 2),
            "var_99_1d_pct": round(port_var99_pct, 2),
            "var_95_1d_eur": round(port_var95_eur, 0),
            "var_99_1d_eur": round(port_var99_eur, 0),
            "beta_vs_spy": round(beta_spy, 2),
            "max_dd_1y_pct": round(max_dd_port, 2),
        },
        # review quant 22/07: convenzioni DICHIARATE accanto ai numeri
        "beta_basis": _message('portafoglio EUR vs SPY convertito in EUR (fix 22/07; prima SPY era in USD)', 'EUR portfolio vs SPY converted to EUR (fix 22/07; SPY was previously in USD)'),
        "risk_free_used": 0.0,
        "sharpe_note": (_message("Sharpe con rf=0 (non excess return); lo Sharpe ufficiale con rf live e' in advanced_metrics (risk_free_used dichiarato li')", 'Sharpe with rf=0 (not excess return); official Sharpe with live rf is in advanced_metrics (risk_free_used declared there)')),
        "nav_basis": (_message('perimetro ANALIZZATO (posizioni SKIP/non-yfinance escluse): i VaR EUR scalano su questa base', 'ANALYZED scope (SKIP/non-yfinance positions excluded): EUR VaR scales on this basis')),
        "nav_book_total_eur": float(snap.get("totale_valore_mercato_eur") or 0),
        "per_asset": per_asset,
        "correlation": {
            "tickers": top8,
            "matrix": corr_matrix,
            "meta": corr_meta,
        },
        "alerts": alerts,
        "n_assets_analyzed": len(valid_internal_tickers),
        "skipped_tickers": skipped,
        "lookback_days": int(len(port_r)),
        "returns_basis": _message("EUR (FX convertito per-serie)", "EUR (FX converted per series)") if fx_meta.get("converted") else _message("valuta locale", "local currency"),
        "fx_conversion": fx_meta,
        "sample_meta": sample_meta,
        # Gerarchia VaR DICHIARATA (fix 14/07: tre VaR scollegati senza gerarchia)
        "var_hierarchy": {
            "official": _message('historical_95_1d su rendimenti EUR (questo payload) — validato da var_backtest', 'historical_95_1d on EUR returns (this payload) — validated by var_backtest'),
            "var99_note": _message('VaR99 storico su ~252 obs = 2-3 osservazioni di coda: statisticamente debole, usare con cautela', 'Historical VaR99 over ~252 observations = 2-3 tail observations: statistically weak, use with caution'),
            "attribution": _message('component VaR parametrico Jorion (portfolio_analytics) — SOLO per attribution', 'Parametric Jorion component VaR (portfolio_analytics) — ONLY for attribution'),
            "scenarios": _message('MC FHS + replay storico (portfolio_montecarlo) — scenari e code simulate', 'MC FHS + historical replay (portfolio_montecarlo) — simulated scenarios and tails'),
        },
        "cached_for_sec": CACHE_TTL_SEC,
    }

    _CACHE["ts"] = time.time()
    _CACHE["data"] = result
    _log(f"computed risk: VaR95={port_var95_pct:.2f}% Sharpe={port_sharpe:.2f} Beta={beta_spy:.2f} DD={max_dd_port:.1f}% alerts={len(alerts)}")
    return render_payload(result)


def invalidate_cache():
    _CACHE["ts"] = 0
    _CACHE["data"] = None


if __name__ == "__main__":
    import json
    r = compute_portfolio_risk(force=True)
    print(json.dumps(r, indent=2, default=str))
