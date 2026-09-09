"""
BELLOMBERG - Monte Carlo Risk Engine v2 (Bank-Grade)

Tre metodi di simulazione + drift shrinkage + Expected Shortfall + stress scenarios.

REFERENCES:
- Barone-Adesi, Giannopoulos, Vosper (1999) "VaR without correlations for portfolios of
  derivative securities", J. of Futures Markets. -> Filtered Historical Simulation
- Politis & Romano (1994) "The stationary bootstrap", JASA 89. -> Block bootstrap
- Christoffersen (2012) "Elements of Financial Risk Management" Ch.8 -> FHS implementation
- Acerbi & Tasche (2002) "On the coherence of expected shortfall", JBF -> CVaR
- Black & Litterman (1992) -> Drift shrinkage Bayesian
- Jorion (1986) "Bayes-Stein estimation for portfolio analysis", JFQA -> shrinkage
- Cornish & Fisher (1938) -> VaR with skew/kurt expansion
- Marcos Lopez de Prado (2018) "Advances in Financial ML" -> anti-overfitting
- Glasserman (2003) "Monte Carlo Methods in Financial Engineering" -> MC foundations

METHODS:
- "parametric_t": multivariate Student-t (LEGACY, risk-naive, useful for sanity check only)
- "fhs": Filtered Historical Simulation. Fit GARCH per asset, bootstrap residuals
  standardized (preserva fat tails reali), riscalare con sigma forecast. Standard bank-grade.
- "block_bootstrap": Politis-Romano stationary bootstrap. Block size = n^(1/3).
  Preserva volatility clustering e autocorrelation. Risk-neutral by construction.

DRIFT MODES:
- "zero": mu = 0 (risk-neutral, conservative, raccomandato per long horizons)
- "shrinkage": mu = 0.3 * mu_historical (Jorion-style, scarta 70% historical drift)
- "historical": mu = mu_historical (LEGACY, OVERESTIMATES in bullish samples)

STRESS SCENARIOS (fix 14/07: replay VERO con download dedicato della finestra):
- "none": no stress
- "gfc_2008": replay Sep 2008 - Mar 2009 (download dedicato; nomi senza storia
  nella finestra -> proxy DICHIARATO beta x SPY stimato sui rendimenti recenti)
- "covid_2020": replay Feb 20 - Apr 30 2020 (stessa meccanica)
- "shock_3sigma": -3σ simultaneo su tutti gli asset al giorno 0 (correlazione 1
  per costruzione quel giorno; le correlazioni dei giorni successivi NON sono
  stressate — dichiarato, la "cov stress" promessa in passato non esisteva)
Se il replay non e' possibile il fallback a shock_3sigma e' DICHIARATO nel
payload (stress_fallback=true, stress_scenario = quello APPLICATO davvero).

OUTPUT METRICS:
- Percentile NAV (P5/10/25/50/75/90/95)
- Expected Shortfall ES95, ES99 (Acerbi-Tasche coherent)
- Cornish-Fisher VaR99 (skew + kurt corrected)
- Max DD distribution
- Stress scenario comparison if requested
"""
import hashlib
import json
import os
import time
from datetime import datetime
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

try:
    from scipy import stats
    SCIPY_OK = True
except Exception:
    SCIPY_OK = False

try:
    from arch import arch_model
    ARCH_OK = True
except Exception:
    ARCH_OK = False

from bellomberg.storage.memory_db import MemoryDB


CACHE_TTL_SEC = 600
_CACHE: Dict[str, Any] = {}

DEFAULT_DF_T = 4  # piu' fat-tailed di prima (era 5)
MIN_OBSERVATIONS = 60

# Traiettorie campione: il motore ne mette SEMPRE in cache questo numero (il pool)
# e all'uscita ne affetta quante ne ha chieste il chiamante (_vista_paths). Cosi'
# la cache resta condivisa fra chi ne vuole 200 (F5) e chi ne vuole 10 (tool degli
# agenti, fan chart del memo): stessa simulazione, nessun doppio calcolo, nessuna
# divergenza fra il numero a schermo e quello nel memo. Coincide col tetto pubblico.
SAMPLE_PATHS_POOL = 500


def _vista_paths(result: Dict[str, Any], quante: int) -> Dict[str, Any]:
    """Copia superficiale del risultato col solo campione richiesto.

    Non muta l'entry in cache (che tiene il pool intero) e non ricalcola niente:
    sceglie `quante` traiettorie distribuite uniformemente su quelle gia' pronte.
    """
    pool = result.get("sample_paths") or []
    if quante >= len(pool):
        return result
    passo = len(pool) / quante
    vista = dict(result)
    vista["sample_paths"] = [pool[int(k * passo)] for k in range(quante)]
    vista["sample_paths_n"] = len(vista["sample_paths"])
    return vista

# Periodi storici per stress replay (UTC dates)
GFC_2008_START = "2008-09-15"  # Lehman collapse
GFC_2008_END   = "2009-03-31"
COVID_2020_START = "2020-02-20"
COVID_2020_END   = "2020-04-30"

# Cache disco della finestra stress (collaudo run #44: la finestra 2008 veniva
# riscaricata 3-4 volte NELLA STESSA run, coi download falliti dei nomi giovani
# ripetuti a ogni giro — prezzi immutabili). Modello: xbrl_cache di sec_xbrl.
# TTL 30gg e non infinito: un buco transitorio di Yahoo non resta congelato per
# sempre. L'uso della cache e' DICHIARATO nel meta ("source").
from bellomberg.core.paths import DATA_DIR
STRESS_CACHE_DIR = str(DATA_DIR / "stress_cache")
STRESS_CACHE_TTL_S = 30 * 24 * 3600


def _log(msg: str):
    try:
        print(f"[MONTECARLO] {msg}", flush=True)
    except OSError:
        pass  # pipe stdout chiusa (es. parent Electron morto): mai uccidere l'endpoint


# I simboli senza serie prezzi utile stanno nel NEGOZIO PRIVATO dei prezzi speciali
# (negozi_privati.carica_prezzi_speciali: data/prezzi_speciali.json, forma in
# prezzi_speciali.example.json), riletto A OGNI CHIAMATA. Negozio assente = si FERMA:
# base_nav e' il NAV del perimetro simulabile e ci scalano TUTTI i percentili in euro.
def prezzi_speciali() -> Dict[str, Any]:
    """L'esito INTERO del negozio: {"prezzi": {...}, "origine": ..., "motivo": ...}."""
    from bellomberg.storage.negozi_privati import carica_prezzi_speciali
    return carica_prezzi_speciali()


def ko_negozio_prezzi(esito: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """L'errore DICHIARATO se il negozio non si legge, altrimenti None (regola PM 14/07)."""
    if esito["origine"] in ("assente", "illeggibile"):
        return {"error": "negozio dei prezzi speciali %s: %s — senza quella lista il perimetro "
                         "simulabile e la scala in euro dei percentili cambierebbero"
                         % (esito["origine"], esito["motivo"]),
                "negozio_prezzi": {"origine": esito["origine"], "motivo": esito["motivo"]},
                "timestamp": datetime.now().isoformat()}
    return None


def _yf_ticker(t: str, salta: frozenset) -> Optional[str]:
    """`salta` arriva dal chiamante (letto UNA volta per giro): senza default, cosi' un
    punto di chiamata dimenticato e' un TypeError e non un insieme vuoto zitto."""
    t = t.strip().upper()
    if t in salta:
        return None
    return t


def _get_holdings_weights(salta: frozenset):
    """Ritorna (pesi normalizzati, NAV EUR del perimetro simulabile ex-SKIP).
    Fix review 22/07: base_nav prendeva il totale DB (che include le posizioni
    SKIP non simulate) — i numeri EUR ora scalano sul perimetro davvero simulato."""
    db = MemoryDB()
    snap = db.get_portfolio_summary()
    if snap.get("fx_incomplete"):
        return None, None
    positions = snap.get("positions", [])
    if not positions:
        return None, 0.0
    total = 0.0
    raw: Dict[str, float] = {}
    for p in positions:
        sym = _yf_ticker(p["ticker"], salta)
        if not sym:
            continue
        w = float(p.get("valore_mercato", 0) or 0)
        if w <= 0:
            continue
        raw[sym] = w
        total += w
    if total <= 0:
        return None, 0.0
    return {t: w / total for t, w in raw.items()}, total


def _download_returns(tickers: List[str], years: int = 5) -> Optional[pd.DataFrame]:
    if not tickers:
        return None
    period_map = {1: "1y", 2: "2y", 5: "5y", 10: "10y"}
    period_str = period_map.get(int(years), "5y")
    try:
        from bellomberg.cli.price_updater import AliasFontiError, data_ticker_map
        dl_map = data_ticker_map(tickers)
        raw = yf.download(list(dl_map.values()), period=period_str, progress=False,
                           auto_adjust=True, threads=True)
        _ren = {dato: reale for reale, dato in dl_map.items() if dato != reale}
        if _ren:
            raw = (raw.rename(columns=_ren, level=-1)
                   if isinstance(raw.columns, pd.MultiIndex) else raw.rename(columns=_ren))
        prices = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        if isinstance(prices, pd.Series):
            prices = prices.to_frame(tickers[0])
        returns = prices.pct_change().dropna(how="all")
        if len(returns) < MIN_OBSERVATIONS:
            return None
        return returns
    except AliasFontiError:
        raise
    except Exception as e:
        _log(f"yf download FAILED: {e}")
        return None


# ============================================================
# METHOD 1: PARAMETRIC MULTIVARIATE-T (LEGACY)
# ============================================================

def _simulate_parametric_t(mu: np.ndarray, cov: np.ndarray,
                            n_periods: int, n_sims: int,
                            df: int = DEFAULT_DF_T) -> np.ndarray:
    """Multivariate Student-t simulation via Cholesky decomposition."""
    n_assets = len(mu)
    # audit/11 §4: la covarianza di una multivariate-t con scale Sigma e' Sigma*df/(df-2):
    # usare la cov empirica come scale RADDOPPIAVA la varianza simulata con df=4
    # (vol ~ +41%). Si scala la matrice cosi' i campioni t hanno la cov storica.
    scale = cov * (df - 2) / df if df > 2 else cov
    try:
        L = np.linalg.cholesky(scale)
    except np.linalg.LinAlgError:
        L = np.linalg.cholesky(scale + np.eye(n_assets) * 1e-8)
    z = np.random.standard_normal((n_sims, n_periods, n_assets))
    chi = np.random.chisquare(df, size=(n_sims, n_periods, 1)) / df
    t_samples = z / np.sqrt(chi)
    correlated = t_samples @ L.T
    return correlated + mu


# ============================================================
# METHOD 2: BLOCK BOOTSTRAP (Politis-Romano)
# ============================================================

def _simulate_block_bootstrap(returns: np.ndarray, n_periods: int, n_sims: int,
                                drift_adjust: Optional[np.ndarray] = None) -> np.ndarray:
    """Block bootstrap stazionario.
    Block size = round(n^(1/3)) (Politis-Romano rule of thumb).
    Preserva autocorrelation e vol clustering.
    """
    n_obs, n_assets = returns.shape
    block_size = max(5, int(round(n_obs ** (1/3))))
    n_blocks = int(np.ceil(n_periods / block_size))

    out = np.zeros((n_sims, n_periods, n_assets))
    for s in range(n_sims):
        # Sample blocks indices
        starts = np.random.randint(0, max(1, n_obs - block_size), size=n_blocks)
        sim_returns = []
        for st in starts:
            sim_returns.append(returns[st:st + block_size])
        sim_full = np.concatenate(sim_returns, axis=0)[:n_periods]
        # Pad if needed
        if len(sim_full) < n_periods:
            extra = n_periods - len(sim_full)
            pad_starts = np.random.randint(0, max(1, n_obs - extra), size=1)[0]
            sim_full = np.concatenate([sim_full, returns[pad_starts:pad_starts + extra]], axis=0)
        out[s] = sim_full[:n_periods]

    # Apply drift adjustment (sottrai mean originale + aggiungi drift desiderato)
    if drift_adjust is not None:
        empirical_mean = returns.mean(axis=0)
        # Re-center to drift_adjust (mantieni shocks, cambia drift)
        out = out - empirical_mean + drift_adjust
    return out


# ============================================================
# METHOD 3: FILTERED HISTORICAL SIMULATION (FHS)
# ============================================================

def _fit_garch_per_asset(returns: np.ndarray):
    """Fit GARCH(1,1) per ogni asset.
    Returns:
      - std_resid_matrix: shape (n_obs, n_assets) residui standardizzati
      - sigma_forecast: shape (n_assets,) vol forecast 1-day ahead
      - sigma_historical: shape (n_assets,) vol unconditional storica per fallback
      - garch_params: shape (n_assets, 3) [omega_dec, alpha, beta] per la ricorsione
        (fix review 22/07; per gli asset in fallback: [sigma_hist^2, 0, 0] = vol costante)
      - fallback_idx: indici asset col fit fallito (dichiarati a valle, regola 14/07)
    """
    if not ARCH_OK:
        raise RuntimeError("arch library not installed - FHS unavailable")
    n_obs, n_assets = returns.shape
    std_resid = np.zeros((n_obs, n_assets))
    sigma_fc = np.zeros(n_assets)
    sigma_hist = returns.std(axis=0)
    garch_params = np.zeros((n_assets, 3))
    fallback_idx: List[int] = []

    for i in range(n_assets):
        r_pct = returns[:, i] * 100  # arch lib piu' stabile in %
        try:
            model = arch_model(r_pct, mean="Constant", vol="GARCH", p=1, o=0, q=1, dist="normal")
            res = model.fit(disp="off", show_warning=False)
            cond_vol_pct = res.conditional_volatility  # in %
            cond_vol = cond_vol_pct / 100.0  # back to decimal
            # standardized residuals
            std_resid[:, i] = (returns[:, i] - returns[:, i].mean()) / np.maximum(cond_vol, 1e-8)
            # forecast 1-day ahead vol
            fc = res.forecast(horizon=1, reindex=False)
            sigma_fc[i] = float(np.sqrt(fc.variance.iloc[-1, -1])) / 100.0
            # parametri per la ricorsione (omega dai pct^2 alla scala decimale)
            garch_params[i] = [float(res.params.get("omega", 0.0)) / 10000.0,
                               float(res.params.get("alpha[1]", 0.0)),
                               float(res.params.get("beta[1]", 0.0))]
        except Exception as e:
            _log(f"GARCH fit failed for asset {i}: {e}, using historical sigma fallback")
            std_resid[:, i] = (returns[:, i] - returns[:, i].mean()) / np.maximum(sigma_hist[i], 1e-8)
            sigma_fc[i] = float(sigma_hist[i])
            garch_params[i] = [float(sigma_hist[i]) ** 2, 0.0, 0.0]  # vol costante
            fallback_idx.append(i)

    return std_resid, sigma_fc, sigma_hist, garch_params, fallback_idx


def _simulate_fhs(returns: np.ndarray, n_periods: int, n_sims: int,
                    drift_adjust: Optional[np.ndarray] = None):
    """Filtered Historical Simulation CON ricorsione GARCH per path (fix review
    quant 22/07: prima la vol restava CONGELATA al forecast 1-day per tutto
    l'orizzonte e i residui erano iid nel tempo -> niente mean-reversion ne'
    clustering, bias regime-dipendente su VaR/ES a 252g; Christoffersen 2012
    cap.8, Barone-Adesi et al. 1999).
    1. Fit GARCH(1,1) per asset -> z standardizzati, sigma_fc, (omega, alpha, beta)
    2. z bootstrappati per-giorno con lo STESSO indice temporale tra asset
       (preserva la dipendenza cross-asset empirica)
    3. eps_t = sigma_t * z_t;  r_t = eps_t + drift;
       sigma^2_{t+1} = omega + alpha * eps_t^2 + beta * sigma^2_t
    Ritorna (out, meta): meta dichiara gli asset in fallback storico.
    """
    n_obs, n_assets = returns.shape
    std_resid, sigma_fc, _, gp, fallback_idx = _fit_garch_per_asset(returns)
    omega, alpha, beta = gp[:, 0], gp[:, 1], gp[:, 2]
    drift = drift_adjust if drift_adjust is not None else 0.0

    out = np.zeros((n_sims, n_periods, n_assets))
    sigma2 = np.broadcast_to(sigma_fc ** 2, (n_sims, n_assets)).copy()
    for t in range(n_periods):
        idx_t = np.random.randint(0, n_obs, size=n_sims)  # stesso giorno storico per tutti gli asset
        z_t = std_resid[idx_t]                            # (n_sims, n_assets)
        eps = np.sqrt(sigma2) * z_t
        out[:, t, :] = eps + drift
        sigma2 = omega + alpha * eps * eps + beta * sigma2
    meta = {"garch_fallback_idx": fallback_idx}
    return out, meta


# ============================================================
# STRESS SCENARIOS
# ============================================================

STRESS_MIN_COVERAGE = 0.80   # quota minima di giorni REALI nella finestra per usare la storia vera
STRESS_MIN_BETA_OBS = 60     # obs minime per stimare il beta del proxy


def _spy_recent_returns_2y():
    """SPY 2y per i beta dei proxy stress, cache in-memory 1h.
    Un fallimento NON viene cacheato (si ritenta alla chiamata dopo)."""
    ent = _CACHE.get("stress_spy_recent_2y")
    if ent is not None and time.time() - ent["ts"] < 3600:
        return ent["data"]
    try:
        s_raw = yf.download("SPY", period="2y", progress=False, auto_adjust=True)
        s_prices = s_raw["Close"] if "Close" in s_raw.columns else s_raw
        sr = s_prices.squeeze().pct_change().dropna()
        _CACHE["stress_spy_recent_2y"] = {"ts": time.time(), "data": sr}
        return sr
    except Exception:
        return None


def _stress_window_returns(tickers: List[str], scenario: str,
                             recent_rdf: Optional[pd.DataFrame]
                             ) -> Tuple[Optional[pd.DataFrame], Any]:
    """Rendimenti REALI della finestra stress via download DEDICATO (fix 14/07:
    prima la finestra usciva dal lookback di calibrazione, quindi il 2008 era
    IMPOSSIBILE col cap 10y e il 2020 moriva sul dropna del ticker piu' giovane).

    Nomi senza storia nella finestra -> proxy DICHIARATO: beta x SPY, con beta
    stimato sui rendimenti recenti (recent_rdf) vs SPY. Niente proxy zitti.

    Ritorna (DataFrame colonne=tickers allineate, meta) o (None, motivo).
    """
    start, end = ((GFC_2008_START, GFC_2008_END) if scenario == "gfc_2008"
                  else (COVID_2020_START, COVID_2020_END))

    # Risolvi PRIMA della cache: cambio o guasto del negozio non puo' riusare una
    # finestra scaricata con una coppia reale->fonte precedente.
    try:
        from bellomberg.cli.price_updater import data_ticker_map
        dl_map = data_ticker_map(tickers, riservati=("SPY",))
    except Exception as e:
        return None, "alias yfinance non risolvibile: %s" % str(e)[:160]

    # Cache disco per scenario e coppie reali->fonte: la finestra e' storia immutabile.
    alias_key = ",".join("%s=%s" % item for item in sorted(dl_map.items()))
    _ck = hashlib.md5((scenario + "|" + alias_key).encode()).hexdigest()[:16]
    _cp = os.path.join(STRESS_CACHE_DIR, f"{scenario}_{_ck}.json")
    win = None
    cache_src = None
    try:
        if os.path.exists(_cp) and (time.time() - os.path.getmtime(_cp)) < STRESS_CACHE_TTL_S:
            with open(_cp, "r", encoding="utf-8") as f:
                _payload = json.load(f)
            win = pd.DataFrame(_payload["data"], index=pd.to_datetime(_payload["index"]),
                               columns=_payload["columns"]).astype(float)
            cache_src = f"cache disco ({(time.time() - os.path.getmtime(_cp)) / 86400.0:.0f}gg)"
    except Exception as e:
        _log(f"stress cache illeggibile ({str(e)[:80]}): riscarico")
        win = None
        cache_src = None

    if win is None:
        try:
            download_symbols = list(dict.fromkeys(list(dl_map.values()) + ["SPY"]))
            raw = yf.download(download_symbols, start=start, end=end,
                              progress=False, auto_adjust=True, threads=True)
            _ren = {dato: reale for reale, dato in dl_map.items() if dato != reale}
            if _ren:
                raw = (raw.rename(columns=_ren, level=-1)
                       if isinstance(raw.columns, pd.MultiIndex) else raw.rename(columns=_ren))
            prices = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
            if isinstance(prices, pd.Series):
                prices = prices.to_frame("SPY")
            win = prices.ffill().pct_change().dropna(how="all")
        except Exception as e:
            return None, f"download finestra {scenario} fallito: {str(e)[:120]}"
        cache_src = "download"

    if win is None or win.empty or "SPY" not in win.columns or win["SPY"].notna().sum() < 10:
        return None, f"nessun dato utile nella finestra {start}..{end} (SPY incluso)"

    # Salva SOLO un download appena passato dal gate di validita' su SPY:
    # mai cacheare spazzatura (un fallimento totale non finisce su disco).
    if cache_src == "download":
        try:
            os.makedirs(STRESS_CACHE_DIR, exist_ok=True)
            with open(_cp, "w", encoding="utf-8") as f:
                json.dump({"index": [d.strftime("%Y-%m-%d") for d in win.index],
                           "columns": [str(c) for c in win.columns],
                           "data": win.values.tolist()}, f)
        except Exception as e:
            _log(f"stress cache non scrivibile: {str(e)[:80]}")

    spy_win = win["SPY"].fillna(0.0)
    n_win = len(win)

    # SPY recente per la stima dei beta dei proxy (best-effort, cache 1h:
    # anche lui veniva riscaricato a ogni chiamata, 3-4 volte per run)
    spy_recent = _spy_recent_returns_2y()

    out = {}
    real: List[str] = []
    proxied: Dict[str, str] = {}
    zero_filled: Dict[str, int] = {}  # review 22/07: i buchi riempiti a 0 vanno CONTATI
    _spy_holes = int(win["SPY"].isna().sum())
    if _spy_holes > 0:
        zero_filled["SPY"] = _spy_holes
    for t in tickers:
        col = win[t] if t in win.columns else None
        if col is not None and col.notna().sum() >= STRESS_MIN_COVERAGE * n_win:
            _holes = int(col.isna().sum())
            if _holes > 0:
                zero_filled[t] = _holes
            out[t] = col.fillna(0.0)
            real.append(t)
            continue
        # PROXY dichiarato: beta (rendimenti recenti vs SPY) x SPY nella finestra
        beta = None
        if spy_recent is not None and recent_rdf is not None and t in recent_rdf.columns:
            rt = recent_rdf[t].dropna()
            common = rt.index.intersection(spy_recent.index)
            if len(common) >= STRESS_MIN_BETA_OBS:
                s = spy_recent.loc[common]
                var_s = float(s.var())
                if var_s > 0:
                    beta = float(rt.loc[common].cov(s) / var_s)
        if beta is None:
            beta = 1.0
            proxied[t] = "PROXY 1.00 x SPY (storia non disponibile nella finestra; beta non stimabile)"
        else:
            proxied[t] = f"PROXY {beta:.2f} x SPY (storia non disponibile nella finestra)"
        out[t] = beta * spy_win
    df = pd.DataFrame(out, index=win.index)[list(tickers)]
    meta = {"window": {"start": start, "end": end, "trading_days": int(n_win),
                       "source": cache_src},
            "real_history": real, "proxied": proxied,
            # giorni senza quotazione riempiti a rendimento 0 dentro la finestra
            # (attenuano la perdita replay: dichiarati, regola 14/07)
            "zero_filled_days": zero_filled}
    return df, meta


def _apply_stress(sim_returns: np.ndarray, scenario: str,
                    returns_df: Optional[pd.DataFrame] = None,
                    cov: Optional[np.ndarray] = None) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Modifica sim_returns per stress scenario.

    Ritorna (sim_returns, meta): meta DICHIARA cosa e' stato applicato davvero
    (fix 14/07: prima il fallback a shock_3sigma era SILENZIOSO e il payload
    echeggiava l'etichetta richiesta — il PM leggeva 'gfc_2008' su un -3σ).

    Args:
      sim_returns: shape (n_sims, n_periods, n_assets)
      scenario: "none" | "gfc_2008" | "covid_2020" | "shock_3sigma"
      returns_df: rendimenti recenti (per tickers/ordine colonne e beta dei proxy)
      cov: per shock 3sigma
    """
    meta: Dict[str, Any] = {"requested": scenario, "applied": scenario, "fallback": False}
    if scenario == "none":
        return sim_returns, meta

    n_sims, n_periods, n_assets = sim_returns.shape

    if scenario in ("gfc_2008", "covid_2020"):
        tickers = list(returns_df.columns) if returns_df is not None else []
        win_df, wmeta = (None, "returns_df mancante (serve per ordine colonne/beta)") \
            if not tickers else _stress_window_returns(tickers, scenario, returns_df)
        if win_df is None:
            _log(f"stress {scenario} NON applicabile ({wmeta}): fallback DICHIARATO a shock_3sigma")
            arr, m3 = _apply_stress(sim_returns, "shock_3sigma", cov=cov)
            meta.update({"applied": "shock_3sigma", "fallback": True,
                         "fallback_reason": str(wmeta), "shock_note": m3.get("shock_note")})
            return arr, meta
        stress_returns = win_df.values  # (n_stress_days, n_assets)
        n_stress = min(len(stress_returns), n_periods)
        sim_out = sim_returns.copy()
        sim_out[:, :n_stress, :] = stress_returns[:n_stress]  # replay identico su ogni sim
        meta.update(wmeta)
        meta["replaced_days"] = int(n_stress)
        if len(stress_returns) > n_periods:
            meta["window_truncated"] = (f"finestra di {len(stress_returns)} giorni troncata "
                                        f"all'orizzonte di {n_periods}")
        return sim_out, meta

    if scenario == "shock_3sigma":
        if cov is None:
            sigma_proxy = np.std(sim_returns, axis=(0, 1))  # per-asset
        else:
            sigma_proxy = np.sqrt(np.diag(cov))
        sim_out = sim_returns.copy()
        # Day 0: -3 sigma shock per asset
        sim_out[:, 0, :] = -3.0 * sigma_proxy
        meta["shock_note"] = ("-3σ simultaneo su tutti gli asset al giorno 0 (correlazione 1 "
                              "per costruzione quel giorno); correlazioni dei giorni successivi "
                              "NON stressate")
        return sim_out, meta

    meta.update({"applied": "none", "fallback": True,
                 "fallback_reason": f"scenario sconosciuto '{scenario}'"})
    return sim_returns, meta


# ============================================================
# DRIFT SHRINKAGE
# ============================================================

def _compute_drift(mu_historical: np.ndarray, mode: str) -> np.ndarray:
    """Returns daily drift vector to apply."""
    if mode == "zero":
        return np.zeros_like(mu_historical)
    if mode == "shrinkage":
        return 0.3 * mu_historical  # Jorion-style 70% shrinkage
    if mode == "historical":
        return mu_historical
    return np.zeros_like(mu_historical)  # default conservative


# ============================================================
# RISK METRICS POST-SIMULATION
# ============================================================

def _cornish_fisher_var(returns_sim: np.ndarray, alpha: float = 0.01) -> float:
    """Cornish-Fisher VaR: corregge VaR gaussian con skewness e kurtosis empirici."""
    mu = float(returns_sim.mean())
    sigma = float(returns_sim.std())
    sk = float(stats.skew(returns_sim))
    kurt = float(stats.kurtosis(returns_sim))  # excess kurtosis
    z = stats.norm.ppf(alpha)
    z_cf = z + (z**2 - 1) * sk / 6 + (z**3 - 3*z) * kurt / 24 - (2*z**3 - 5*z) * (sk**2) / 36
    return float(mu + sigma * z_cf)


def _expected_shortfall(returns_sim: np.ndarray, alpha: float = 0.05) -> float:
    """ES = mean of returns below alpha-percentile (Acerbi-Tasche coherent)."""
    var_threshold = np.percentile(returns_sim, alpha * 100)
    tail = returns_sim[returns_sim <= var_threshold]
    if len(tail) == 0:
        return float(var_threshold)
    return float(tail.mean())


# ============================================================
# MAIN ENGINE
# ============================================================

def run_monte_carlo(
    horizon_days: int = 252,
    n_sims: int = 10000,
    lookback_years: int = 5,
    method: str = "fhs",  # "parametric_t" | "fhs" | "block_bootstrap"
    drift_mode: str = "zero",  # "zero" | "shrinkage" | "historical"
    stress_scenario: str = "none",  # "none" | "gfc_2008" | "covid_2020" | "shock_3sigma"
    add_tickers: Optional[List[str]] = None,
    remove_tickers: Optional[List[str]] = None,
    equal_weight_added: bool = True,
    seed: Optional[int] = None,
    force_refresh: bool = False,
    # Quante delle n_sims traiettorie spedire nel payload (richiesta PM 26/07 via
    # ponte: "perche' solo 10?"). Il DEFAULT RESTA 10 di proposito: `sample_paths`
    # non lo legge solo la UI — finisce anche nel tool result degli agenti
    # (chat_tools.py get_portfolio_montecarlo) e nel fan chart del memo PDF
    # (charts_quant.chart_mc_fan, che disegna OGNI path ricevuto). Alzare il
    # default gonfierebbe il contesto di una run da ~10 EUR e trasformerebbe il
    # grafico del memo in una macchia grigia. Chi vuole di piu' lo CHIEDE: oggi
    # lo fanno gli endpoint di F5. Nessun ricalcolo, i path sono gia' in `cum`.
    sample_paths_n: int = 10,
    _override_weights: Optional[Dict[str, float]] = None,
    _override_nav: Optional[float] = None,
    _extra_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Bank-grade Monte Carlo.

    Default args = FHS + 5y lookback + zero drift + no stress.
    """
    if not (NUMPY_OK and YF_OK and SCIPY_OK):
        return {"error": "librerie mancanti (numpy/yfinance/scipy)",
                "timestamp": datetime.now().isoformat()}

    # Il negozio PRIMA della cache e del DB: la chiave di cache non lo contiene, quindi un
    # risultato calcolato col negozio a posto non deve poter essere reso a negozio rotto.
    _prezzi = prezzi_speciali()
    _ko = ko_negozio_prezzi(_prezzi)
    if _ko:
        return _ko
    salta = _prezzi["prezzi"]["senza_yfinance"]

    if method == "fhs" and not ARCH_OK:
        _log("arch lib mancante, fallback a block_bootstrap")
        method = "block_bootstrap"

    n_sims = max(1000, min(int(n_sims), 50000))
    horizon_days = max(5, min(int(horizon_days), 1260))
    lookback_years = max(1, min(int(lookback_years), 10))
    # tetto 500: oltre, il payload cresce senza che l'occhio distingua una traccia
    # in piu' (e a 500x33 punti siamo gia' a ~130 KB di JSON)
    sample_paths_n = max(1, min(int(sample_paths_n), SAMPLE_PATHS_POOL))

    # audit/11 §2: la chiave DEVE includere anche gli override v3 (pesi/NAV del what-if)
    # e il seed — prima due what-if diversi condividevano la stessa entry di cache.
    _ow_key = ""
    if _override_weights is not None:
        try:
            _ow_key = str(sorted((str(k), round(float(v), 8)) for k, v in _override_weights.items()))
        except Exception:
            _ow_key = str(sorted(_override_weights.items()))
    # NB: `sample_paths_n` NON entra nella chiave, ed e' voluto (review 26/07). La
    # cache tiene sempre il POOL completo e il campione si affetta all'uscita
    # (_vista_paths): cosi' F5 (che ne chiede 200) e il tool degli agenti (10) —
    # identici in tutto il resto — continuano a condividere LA STESSA simulazione,
    # com'era prima di introdurre il parametro. Metterlo in chiave sembrava
    # corretto e invece separava le due letture: senza seed sono due Monte Carlo
    # diversi, e il PM avrebbe letto un ES99 su F5 e un altro nel memo.
    cache_key = (f"mc:v3:{horizon_days}:{n_sims}:{lookback_years}:{method}:{drift_mode}:"
                 f"{stress_scenario}:{sorted(add_tickers or [])}:{sorted(remove_tickers or [])}:"
                 f"{_ow_key}:{_override_nav}:{seed}")
    if not force_refresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            vista = _vista_paths(entry["data"], sample_paths_n)
            if _extra_meta:
                # F43 (2), 27/08: la simulazione in cache e' la stessa (stessi pesi
                # post), ma la DICHIARAZIONE (modifiche applicate/saltate, pesi e NAV
                # pre) e' di chi chiama: «vendi 25k di AAA» e «chiudi AAA» condividono
                # la chiave, e il secondo leggeva la nota del primo. Su una COPIA:
                # `_vista_paths` col campione intero restituisce l'entry stessa.
                if vista is entry["data"]:
                    vista = dict(vista)
                vista.update(_extra_meta)
            return vista

    if seed is not None:
        np.random.seed(int(seed))

    # V3 path: override weights provided directly (already preprocessed by run_monte_carlo_v3)
    if _override_weights is not None:
        weights = dict(_override_weights)
        if not weights:
            return {"error": "empty override weights", "timestamp": datetime.now().isoformat()}
        tot_w = sum(weights.values())
        weights = {t: w / tot_w for t, w in weights.items()}
        base_nav = float(_override_nav) if _override_nav else 0.0
        if base_nav <= 0:
            db = MemoryDB()
            base_nav = float(db.get_portfolio_summary().get("totale_valore_mercato_eur") or 0)
    else:
        # Legacy v2 path
        base_weights, base_total_ex_skip = _get_holdings_weights(salta)
        if not base_weights:
            return {"error": "no portfolio holdings", "timestamp": datetime.now().isoformat()}

        weights = dict(base_weights)
        if remove_tickers:
            for t in remove_tickers:
                sym = _yf_ticker(t, salta)
                if sym and sym in weights:
                    del weights[sym]
        if add_tickers:
            added = [_yf_ticker(t, salta) for t in add_tickers if _yf_ticker(t, salta)]
            if added:
                new_alloc = 0.30
                per_new = new_alloc / len(added)
                scale = 1 - new_alloc
                weights = {t: w * scale for t, w in weights.items()}
                for t in added:
                    weights[t] = per_new

        if not weights:
            return {"error": "empty portfolio after what-if", "timestamp": datetime.now().isoformat()}

        tot_w = sum(weights.values())
        weights = {t: w / tot_w for t, w in weights.items()}

        # fix review 22/07: NAV di scala = perimetro simulabile (ex-SKIP), non il
        # totale DB che include posizioni non simulate. Dichiarato nel payload.
        base_nav = float(base_total_ex_skip)

    tickers = list(weights.keys())
    _log(f"download returns for {len(tickers)} tickers, lookback={lookback_years}y")
    try:
        returns_df = _download_returns(tickers, years=lookback_years)
    except Exception as e:
        return {"error": "portfolio returns failed: " + str(e),
                "timestamp": datetime.now().isoformat()}
    if returns_df is None:
        return {"error": "cannot download returns",
                "timestamp": datetime.now().isoformat()}

    available = [t for t in tickers if t in returns_df.columns]
    if len(available) < 2:
        return {"error": f"only {len(available)} ticker(s) with returns",
                "timestamp": datetime.now().isoformat()}

    rdf = returns_df[available].dropna(how="any")
    rr = rdf.values
    w = np.array([weights[t] for t in available])
    w = w / w.sum()

    # DICHIARAZIONE campione (regola no-fallback 14/07): il dropna(how='any')
    # allinea tutto al ticker piu' giovane; se il taglio e' pesante va detto.
    calibration_note = None
    _panel_len = len(returns_df)
    if _panel_len > 0 and len(rr) < 0.6 * _panel_len:
        _youngest = min(available, key=lambda t: int(returns_df[t].notna().sum()))
        calibration_note = (
            f"campione di calibrazione TAGLIATO a {len(rr)} obs dal ticker piu' giovane "
            f"({_youngest}: {int(returns_df[_youngest].notna().sum())} obs su {_panel_len} del panel "
            f"{lookback_years}y): vol e correlazioni stimate su finestra corta")
        _log("CALIBRAZIONE: " + calibration_note)

    mu_daily_historical = rr.mean(axis=0)
    cov_daily = np.cov(rr.T)
    drift = _compute_drift(mu_daily_historical, drift_mode)

    _log(f"method={method} drift={drift_mode} stress={stress_scenario} "
         f"sims={n_sims} x horizon={horizon_days}d x assets={len(available)} "
         f"calibration_obs={len(rr)}")

    # Simulate
    garch_fallback_assets: List[str] = []
    if method == "parametric_t":
        sim_returns = _simulate_parametric_t(drift, cov_daily, horizon_days, n_sims, DEFAULT_DF_T)
    elif method == "block_bootstrap":
        sim_returns = _simulate_block_bootstrap(rr, horizon_days, n_sims, drift_adjust=drift)
    elif method == "fhs":
        sim_returns, fhs_meta = _simulate_fhs(rr, horizon_days, n_sims, drift_adjust=drift)
        # fix review 22/07: asset col fit GARCH fallito (vol storica costante)
        # DICHIARATI nel payload, non solo nel log
        garch_fallback_assets = [available[i] for i in fhs_meta.get("garch_fallback_idx", [])]
    else:
        return {"error": f"unknown method '{method}'", "timestamp": datetime.now().isoformat()}

    # Apply stress scenario (modifies sim_returns); meta dichiara cosa e' stato
    # applicato DAVVERO (fix 14/07: niente piu' fallback silenziosi etichettati replay)
    sim_returns, stress_meta = _apply_stress(sim_returns, stress_scenario, returns_df=rdf, cov=cov_daily)

    # Perdita DETERMINISTICA della finestra replay (il replay e' identico su ogni
    # sim): e' IL numero "replay 2008/2020 in EUR" per memo e budget stress (#187)
    if stress_meta.get("applied") in ("gfc_2008", "covid_2020") and not stress_meta.get("fallback"):
        _n_rep = int(stress_meta.get("replaced_days") or 0)
        if _n_rep > 0:
            _port_stress = sim_returns[0, :_n_rep, :] @ w
            _wloss = float(np.prod(1.0 + _port_stress) - 1.0)
            stress_meta["window_loss_pct"] = round(_wloss * 100, 2)
            stress_meta["window_loss_eur"] = round(_wloss * base_nav, 0)
            stress_meta["basis"] = ("rendimenti in valuta LOCALE per-asset scalati sul NAV EUR "
                                    "(dichiarato; per il replay GFC direzione conservativa)")

    # Portfolio path
    port_daily = sim_returns @ w
    cum = np.cumprod(1 + port_daily, axis=1)
    final_ratio = cum[:, -1]

    # Percentili
    pct = np.percentile(final_ratio, [5, 10, 25, 50, 75, 90, 95])
    percentiles = {"p5": float(pct[0]), "p10": float(pct[1]), "p25": float(pct[2]),
                   "p50": float(pct[3]), "p75": float(pct[4]), "p90": float(pct[5]),
                   "p95": float(pct[6])}
    pct_eur = {k: float(v * base_nav) for k, v in percentiles.items()}

    expected = float((final_ratio.mean() - 1) * 100)
    median = float((np.median(final_ratio) - 1) * 100)
    stdev = float(final_ratio.std() * 100)
    sharpe = float(expected / stdev) if stdev > 0 else 0.0

    prob_neg = float(np.mean(final_ratio < 1.0) * 100)
    prob_l10 = float(np.mean(final_ratio < 0.9) * 100)
    prob_l20 = float(np.mean(final_ratio < 0.8) * 100)
    prob_g10 = float(np.mean(final_ratio > 1.1) * 100)
    prob_g20 = float(np.mean(final_ratio > 1.2) * 100)

    # Coherent risk metrics
    returns_at_horizon = final_ratio - 1.0  # return scalare per sim
    es95 = _expected_shortfall(returns_at_horizon, alpha=0.05) * 100
    es99 = _expected_shortfall(returns_at_horizon, alpha=0.01) * 100
    var95 = float(np.percentile(returns_at_horizon, 5) * 100)
    var99 = float(np.percentile(returns_at_horizon, 1) * 100)
    cf_var99 = float(_cornish_fisher_var(returns_at_horizon, alpha=0.01) * 100)

    # Max drawdown
    rolling_max = np.maximum.accumulate(cum, axis=1)
    dd = (cum - rolling_max) / rolling_max
    max_dd = dd.min(axis=1)
    max_dd_pct = np.percentile(max_dd, [5, 50, 95]) * 100

    # Sample paths: si campiona SEMPRE il pool (uniformemente sulle n_sims
    # simulate) e si affetta all'uscita — v. SAMPLE_PATHS_POOL. Nessun ricalcolo:
    # sono righe di `cum` gia' in memoria, si sceglie solo quante spedirne.
    paths_idx = np.linspace(0, n_sims - 1, num=min(SAMPLE_PATHS_POOL, n_sims), dtype=int)
    step = max(1, horizon_days // 30)
    _pidx = sorted(set(list(range(0, horizon_days, step)) + [horizon_days - 1]))
    # 6 decimali: sono RATIO (NAV/NAV0), quindi la risoluzione e' 0,10 EUR su un NAV
    # da 100k — piu' fine dell'euro intero a cui sono gia' arrotondate le bande del
    # fan chart, e invisibile su una traccia disegnata. A piena precisione ogni
    # numero occupa 18 caratteri e 200 traiettorie pesano 128 KB invece di ~52.
    sample_paths = [[round(float(cum[i][j]), 6) for j in _pidx] for i in paths_idx]
    # Giorno di ciascun punto dei sample_paths: esplicito, cosi' i consumatori (fan
    # chart) non devono re-indovinare lo step di decimazione. Include sempre il giorno
    # terminale, come le bande.
    sample_paths_days = [int(d) + 1 for d in _pidx]

    # Fan chart bands per-day (#178): percentili dei path per il fan chart del PDF.
    # p10/p90 (#143, 15/07): servono alle bande annidate del fan; sono percentili VERI
    # degli stessi path, non interpolazioni.
    band_step = max(1, horizon_days // 60)
    # L'ULTIMO punto deve essere il giorno TERMINALE: range(0, 252, 4) si ferma a 248
    # (giorno 249), mentre percentiles_eur e terminal_hist stanno su cum[:,-1] (giorno
    # 252). Senza questo, il fan chart e la tabella percentili del memo mostrano due
    # P5/P95 DIVERSI per lo stesso portafoglio (review 15/07).
    band_idx = sorted(set(list(range(0, horizon_days, band_step)) + [horizon_days - 1]))
    fan = np.percentile(cum[:, band_idx], [5, 10, 25, 50, 75, 90, 95], axis=0)
    fan_bands = {
        "days": [int(d) + 1 for d in band_idx],
        "p5":  [round(float(x) * base_nav, 0) for x in fan[0]],
        "p10": [round(float(x) * base_nav, 0) for x in fan[1]],
        "p25": [round(float(x) * base_nav, 0) for x in fan[2]],
        "p50": [round(float(x) * base_nav, 0) for x in fan[3]],
        "p75": [round(float(x) * base_nav, 0) for x in fan[4]],
        "p90": [round(float(x) * base_nav, 0) for x in fan[5]],
        "p95": [round(float(x) * base_nav, 0) for x in fan[6]],
    }

    # Distribuzione dei valori TERMINALI (#143, 15/07): istogramma dei NAV simulati a
    # fine orizzonte, per il marginale a lato del fan chart. Dati veri, non ricampionati.
    _cnt, _edges = np.histogram(final_ratio * base_nav, bins=48)
    terminal_hist = {
        "counts": [int(c) for c in _cnt],
        "edges_eur": [round(float(e), 0) for e in _edges],
    }

    result = {
        "timestamp": datetime.now().isoformat(),
        "version": "v2",
        "method": method,
        "method_description": {
            "parametric_t": f"Multivariate Student-t (df={DEFAULT_DF_T}) - LEGACY, risk-naive",
            "fhs": "Filtered Historical Simulation (GARCH + bootstrap residuals) - bank-grade",
            "block_bootstrap": "Politis-Romano block bootstrap - preserves vol clustering",
        }.get(method, method),
        "drift_mode": drift_mode,
        # ONESTO (fix 14/07): stress_scenario = quello APPLICATO davvero; se il
        # replay non era possibile il fallback e' dichiarato, mai etichettato replay
        "stress_scenario": stress_meta.get("applied", stress_scenario),
        "stress_requested": stress_scenario,
        "stress_fallback": bool(stress_meta.get("fallback")),
        "stress_meta": stress_meta,
        "lookback_years": lookback_years,
        "lookback_days_calibration": int(len(rr)),
        "n_sims": int(n_sims),
        "horizon_days": int(horizon_days),
        "horizon_years": round(horizon_days / 252, 2),
        "n_assets": len(available),
        "calibration_note": calibration_note,
        # DICHIARAZIONE base valutaria (review 14/07): i rendimenti (calibrazione
        # E finestre stress) sono in VALUTA LOCALE per-asset; i campi *_eur usano
        # il NAV EUR solo come SCALA. Per il replay GFC (USD in apprezzamento
        # nella finestra) la perdita EUR vera e' meno negativa: direzione
        # conservativa. Conversione EUR completa del motore = voce P1 nel MASTER.
        "returns_basis": ("valuta LOCALE per-asset (FX non convertito, dichiarato): "
                          "i campi *_eur scalano sul NAV EUR"),
        "tickers_analyzed": available,
        "removed_tickers": remove_tickers or [],
        "added_tickers": add_tickers or [],
        "weights": {t: round(float(w[i]), 4) for i, t in enumerate(available)},
        "base_nav_eur": base_nav,
        # review 22/07: base di scala EUR dichiarata (perimetro simulato, ex-SKIP)
        "base_nav_note": ("NAV del perimetro SIMULATO (somma valore_mercato dei ticker "
                          "analizzabili, posizioni SKIP escluse); nel path v3 = nav_post"),
        "garch_fallback_assets": garch_fallback_assets,
        "percentiles_ratio": percentiles,
        "percentiles_eur": pct_eur,
        "fan_bands": fan_bands,
        "terminal_hist": terminal_hist,
        "expected_return_pct": round(expected, 2),
        "median_return_pct": round(median, 2),
        "stdev_pct": round(stdev, 2),
        "sharpe_simulated": round(sharpe, 2),
        "prob_negative_pct": round(prob_neg, 2),
        "prob_loss_10pct": round(prob_l10, 2),
        "prob_loss_20pct": round(prob_l20, 2),
        "prob_gain_10pct": round(prob_g10, 2),
        "prob_gain_20pct": round(prob_g20, 2),
        # Coherent risk metrics
        "var_95_pct": round(var95, 2),
        "var_99_pct": round(var99, 2),
        "var_99_cornish_fisher_pct": round(cf_var99, 2),
        "es_95_pct": round(es95, 2),
        "es_99_pct": round(es99, 2),
        "es_95_eur": round(es95 / 100 * base_nav, 0),
        "es_99_eur": round(es99 / 100 * base_nav, 0),
        # DD
        "max_drawdown_p5_pct": round(float(max_dd_pct[0]), 2),
        "max_drawdown_median_pct": round(float(max_dd_pct[1]), 2),
        "max_drawdown_p95_pct": round(float(max_dd_pct[2]), 2),
        "sample_paths": sample_paths,
        "sample_paths_days": sample_paths_days,
        # dichiarato, cosi' il consumatore puo' scrivere "N traiettorie su n_sims
        # simulate" invece di far credere che il campione sia tutto (mai due misure
        # zitte): il compagno `n_sims` e' gia' nel payload qui sopra
        "sample_paths_n": len(sample_paths),
    }

    if _extra_meta:
        result.update(_extra_meta)

    # in cache va il pool intero; al chiamante solo il campione che ha chiesto
    _CACHE[cache_key] = {"ts": time.time(), "data": result}
    _log(f"MC done: method={method} E[R]={expected:.2f}% ES99={es99:.2f}% "
         f"P(loss>20%)={prob_l20:.1f}% P50_EUR={pct_eur['p50']:,.0f}")
    return _vista_paths(result, sample_paths_n)


# ============================================================
# V3 ENTRYPOINT - structured modifications with real EUR sizing
# ============================================================

def run_monte_carlo_v3(
    horizon_days: int = 252,
    n_sims: int = 10000,
    lookback_years: int = 5,
    method: str = "fhs",
    drift_mode: str = "zero",
    stress_scenario: str = "none",
    modifications: Optional[List[Dict[str, Any]]] = None,
    seed: Optional[int] = None,
    force_refresh: bool = False,
    sample_paths_n: int = 10,   # v. nota in run_monte_carlo: default basso apposta
) -> Dict[str, Any]:
    """Monte Carlo v3: accetta modifiche strutturate al portafoglio con sizing reale.

    modifications: lista di dict:
      {"action": "add",    "ticker": "ALFA", "amount_eur": 1000}
      {"action": "remove", "ticker": "BETA", "amount_eur": 500}
      {"action": "remove", "ticker": "BETA", "amount_pct": 50}
      {"action": "trim",   "ticker": "GAMMA", "amount_pct": 30}

    Restituisce in piu':
      weights_pre, weights_post   (% allocazione prima/dopo)
      nav_pre_eur, nav_post_eur   (NAV totale prima/dopo)
      modifications_applied, skipped_modifications
    """
    # Come in run_monte_carlo: il negozio PRIMA del DB, e la sua assenza ferma il giro
    # (nav_pre/nav_post sono in euro e scalano sul perimetro).
    _prezzi = prezzi_speciali()
    _ko = ko_negozio_prezzi(_prezzi)
    if _ko:
        return _ko
    salta = _prezzi["prezzi"]["senza_yfinance"]

    db = MemoryDB()
    snap = db.get_portfolio_summary()
    if snap.get("fx_incomplete"):
        return {"error": "FX incompleto: pesi Monte Carlo EUR n.d. (" +
                         ", ".join(snap["fx_incomplete"]) + ")",
                "timestamp": datetime.now().isoformat()}
    positions = snap.get("positions", []) or []

    holdings_eur_pre: Dict[str, float] = {}
    for p in positions:
        sym = _yf_ticker(p["ticker"], salta)
        if not sym:
            continue
        v = float(p.get("valore_mercato", 0) or 0)
        if v > 0:
            holdings_eur_pre[sym] = holdings_eur_pre.get(sym, 0.0) + v

    nav_pre = sum(holdings_eur_pre.values())
    if nav_pre <= 0 and not modifications:
        return {"error": "no portfolio holdings", "timestamp": datetime.now().isoformat()}

    weights_pre = {t: round(v / nav_pre, 6) for t, v in holdings_eur_pre.items()} if nav_pre > 0 else {}

    # Apply modifications
    holdings_eur_post: Dict[str, float] = dict(holdings_eur_pre)
    skipped: List[Dict[str, str]] = []
    applied: List[Dict[str, Any]] = []

    for mod in (modifications or []):
        action = (mod.get("action") or "").lower().strip()
        ticker_raw = (mod.get("ticker") or "").strip().upper()
        amt_eur = mod.get("amount_eur")
        amt_pct = mod.get("amount_pct")

        if not ticker_raw:
            skipped.append({"ticker": "", "reason": "empty ticker"})
            continue

        sym = _yf_ticker(ticker_raw, salta)
        if not sym:
            skipped.append({"ticker": ticker_raw,
                            "reason": "ticker in SKIP list (crypto/no yfinance proxy)"})
            continue

        if action == "add":
            if not amt_eur or float(amt_eur) <= 0:
                skipped.append({"ticker": ticker_raw,
                                "reason": "ADD requires positive amount_eur"})
                continue
            # Validate ticker exists in yfinance
            try:
                from bellomberg.cli.price_updater import data_ticker as _dt5
                h = yf.Ticker(_dt5(sym)).history(period="5d")
                if h.empty or h["Close"].dropna().empty:
                    skipped.append({"ticker": ticker_raw,
                                    "reason": f"yfinance returned empty history for '{sym}'. "
                                              "Check suffix (.L .MI .DE .HK .T)"})
                    continue
            except Exception as e:
                skipped.append({"ticker": ticker_raw,
                                "reason": f"yfinance error: {str(e)[:80]}"})
                continue
            holdings_eur_post[sym] = holdings_eur_post.get(sym, 0.0) + float(amt_eur)
            applied.append({"action": "add", "ticker": sym, "amount_eur": float(amt_eur)})

        elif action == "remove":
            if sym not in holdings_eur_post:
                skipped.append({"ticker": ticker_raw,
                                "reason": f"'{sym}' not currently in portfolio"})
                continue
            # review 27/08: un importo NON positivo finiva nel ramo «chiusura senza
            # importo» con una nota falsa (e da oggi `truncated: false` a certificarla);
            # `add` e `trim` lo scartano gia' con un motivo, `remove` no
            if (amt_eur is not None and float(amt_eur) <= 0) or (amt_pct is not None and float(amt_pct) <= 0):
                skipped.append({"ticker": ticker_raw,
                                "reason": "REMOVE requires positive amount_eur/amount_pct "
                                          "(omit both = full close)"})
                continue
            cur = holdings_eur_post[sym]
            # F43 (2), 27/08: le chiavi di prima tengono il CHIESTO; quanto viene
            # tolto davvero sta in `amount_eur_effettivo`, e se il chiesto supera la
            # posizione lo dicono `truncated` + `note`. Prima la vendita piu' grande
            # della posizione chiudeva zitta, e con amount_pct > 100
            # `amount_eur_resolved` dichiarava piu' EUR tolti di quanti ne valesse.
            note = None
            if amt_eur is not None:
                chiesto = float(amt_eur)
                effettivo = min(chiesto, cur)
                # al centesimo: `cur` in produzione e' quantita*prezzo*fx con rumore
                # sotto il centesimo, il PM digita i 2 decimali che legge nel book
                # (review: 10000 su 9999.996 non e' una troncatura di 0,00 EUR)
                troncata = round(chiesto, 2) > round(cur, 2)
                applied_meta = {"action": "remove", "ticker": sym, "amount_eur": chiesto}
                if amt_pct is not None:
                    # vince l'EUR (com'era): la percentuale non sparisce zitta
                    applied_meta["amount_pct_ignorata"] = float(amt_pct)
                if troncata:
                    note = (f"chiesti {chiesto:.2f} EUR ma la posizione residua valeva {cur:.2f} EUR: "
                            f"chiusa per intero, {chiesto - cur:.2f} EUR non applicati")
            elif amt_pct is not None:
                pct_chiesta = float(amt_pct)
                effettivo = cur * min(pct_chiesta, 100.0) / 100.0
                troncata = pct_chiesta > 100.0
                applied_meta = {"action": "remove", "ticker": sym, "amount_pct": pct_chiesta,
                                "amount_eur_resolved": round(effettivo, 2)}
                if troncata:
                    note = (f"chiesto il {pct_chiesta:g}% ma la posizione e' il 100%: "
                            f"chiusa per intero")
            else:
                effettivo = cur
                troncata = False
                applied_meta = {"action": "remove", "ticker": sym, "amount_eur": cur,
                                "note": "full position closed (no amount specified)"}
            applied_meta["amount_eur_effettivo"] = round(effettivo, 2)
            applied_meta["truncated"] = troncata
            if note:
                applied_meta["note"] = note
            new_val = cur - effettivo
            if new_val <= 0:
                del holdings_eur_post[sym]
            else:
                holdings_eur_post[sym] = new_val
            applied.append(applied_meta)

        elif action == "trim":
            if sym not in holdings_eur_post:
                skipped.append({"ticker": ticker_raw,
                                "reason": f"'{sym}' not currently in portfolio"})
                continue
            if not amt_pct or float(amt_pct) <= 0:
                skipped.append({"ticker": ticker_raw,
                                "reason": "TRIM requires positive amount_pct (0-100)"})
                continue
            pct_chiesta = float(amt_pct)
            pct = min(pct_chiesta, 100.0)
            cur = holdings_eur_post[sym]
            new_val = cur * (1 - pct / 100.0)
            if new_val <= 0:
                del holdings_eur_post[sym]
            else:
                holdings_eur_post[sym] = new_val
            # F43 (2): `amount_pct` resta il tetto applicato (com'era); il chiesto e
            # la troncatura sono dichiarati accanto, come nel ramo remove
            voce = {"action": "trim", "ticker": sym, "amount_pct": pct,
                    "amount_pct_chiesto": pct_chiesta,
                    "amount_eur_resolved": round(cur - new_val, 2),
                    "amount_eur_effettivo": round(cur - new_val, 2),
                    "truncated": pct_chiesta > 100.0}
            if voce["truncated"]:
                voce["note"] = (f"chiesto il {pct_chiesta:g}% ma il tetto del trim e' il 100%: "
                                f"posizione chiusa per intero")
            applied.append(voce)

        else:
            skipped.append({"ticker": ticker_raw,
                            "reason": f"unknown action '{action}' (use add/remove/trim)"})

    nav_post = sum(holdings_eur_post.values())
    weights_post = {t: round(v / nav_post, 6) for t, v in holdings_eur_post.items()} if nav_post > 0 else {}

    if not holdings_eur_post:
        return {"error": "empty portfolio after modifications",
                "weights_pre": weights_pre,
                "nav_pre_eur": round(nav_pre, 2),
                "skipped_modifications": skipped,
                "timestamp": datetime.now().isoformat()}

    extra_meta = {
        "weights_pre": weights_pre,
        "weights_post": weights_post,
        "nav_pre_eur": round(nav_pre, 2),
        "nav_post_eur": round(nav_post, 2),
        "modifications_applied": applied,
        "skipped_modifications": skipped,
        "version": "v3",
    }

    return run_monte_carlo(
        horizon_days=horizon_days,
        n_sims=n_sims,
        lookback_years=lookback_years,
        method=method,
        drift_mode=drift_mode,
        stress_scenario=stress_scenario,
        seed=seed,
        force_refresh=force_refresh,
        sample_paths_n=sample_paths_n,
        _override_weights=holdings_eur_post,
        _override_nav=nav_post,
        _extra_meta=extra_meta,
    )


def invalidate_cache():
    _CACHE.clear()


if __name__ == "__main__":
    import json
    print("=== TEST: FHS + zero drift + 5y lookback + 1y horizon ===")
    r = run_monte_carlo(horizon_days=252, n_sims=3000, lookback_years=5,
                          method="fhs", drift_mode="zero", force_refresh=True)
    print(json.dumps({k: v for k, v in r.items() if k != "sample_paths"}, indent=2, default=str))
