"""REVIEW QUANT (brief audit/16, FASE 2) — prove numeriche su dati sintetici:
portfolio_garch (recupero parametri da serie GARCH simulata, CI), portfolio_montecarlo
(simulatori, drift zero -> mediana flat, fallback stress DICHIARATI), portfolio_factors
(regressione recupera beta noti).

Regole conftest: ZERO rete, ZERO DB vivo — yfinance/MemoryDB/downloader stubbati.
Ogni tolleranza e' DICHIARATA. I test xfail documentano difetti NOTI del brief
(FASE 1): al fix passeranno e andranno promossi a test pieni.
"""
import inspect
import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats as st


# ============================================================
# GARCH(1,1): serie simulata con parametri NOTI -> lo stimatore li recupera
# ============================================================

def _simula_garch(omega, alpha, beta, n, seed):
    """Serie GARCH(1,1) in unita' PCT (come lavora il modulo), innovazioni normali."""
    rng = np.random.default_rng(seed)
    var_uncond = omega / (1 - alpha - beta)
    sigma2 = var_uncond
    r = np.empty(n)
    burn = 500
    for t in range(-burn, n):
        eps = math.sqrt(sigma2) * rng.standard_normal()
        if t >= 0:
            r[t] = eps
        sigma2 = omega + alpha * eps ** 2 + beta * sigma2
    return r  # in pct


@pytest.fixture
def garch_sintetico(monkeypatch):
    """compute_portfolio_garch con rendimenti iniettati: GARCH(1,1) simulato
    omega=0.10, alpha=0.08, beta=0.85 (persistence 0.93, vol ~1.20%/g), n=2000."""
    import bellomberg.portfolio.portfolio_garch as pg
    r_pct = _simula_garch(0.10, 0.08, 0.85, 2000, seed=20260722)
    serie = pd.Series(r_pct / 100.0, index=pd.bdate_range("2018-01-02", periods=2000))
    meta = {"returns_basis": "EUR (sintetico)", "fx_conversion": {}, "sample_meta": {},
            "excluded_tickers": []}
    monkeypatch.setattr(pg, "_get_portfolio_returns", lambda *a, **k: (serie, meta))
    monkeypatch.setattr(pg, "MemoryDB", lambda: type("DB", (), {
        "get_portfolio_summary": lambda self: {"fx_incomplete": None}})())
    pg.invalidate_cache()
    out = pg.compute_portfolio_garch(force=True)
    pg.invalidate_cache()
    assert "error" not in out, out.get("error")
    return out


def test_garch_recupera_parametri(garch_sintetico):
    """Tolleranze dichiarate (n=2000): alpha +/-0.05, beta +/-0.07,
    persistence +/-0.05 dal vero 0.93, vol annua nel range della verita'."""
    out = garch_sintetico
    p = out["parameters"]
    alpha_eff = p.get("alpha[1]", 0.0) + 0.5 * p.get("gamma[1]", 0.0)
    assert alpha_eff == pytest.approx(0.08, abs=0.05)
    assert p.get("beta[1]", 0.0) == pytest.approx(0.85, abs=0.07)
    assert out["persistence"] == pytest.approx(0.93, abs=0.05)
    # vol unconditional vera: sqrt(0.10/0.07)=1.195%/g -> ~18.97% annua.
    # la conditional dell'ultimo giorno oscilla intorno: range largo dichiarato.
    assert 13.0 <= out["current_vol_annual_pct"] <= 26.0
    assert out["persistence_interpretation"].startswith("OK")
    assert out["n_obs"] == 2000


def test_garch_forecast_orizzonti_presenti(garch_sintetico):
    fv = garch_sintetico["forecast_vol"]
    for h in ("1d", "5d", "22d"):
        assert "vol_annualized_pct" in fv[h], fv[h]
        assert fv[h]["vol_annualized_pct"] > 0
        # fix E3 review 22/07: vol media sull'orizzonte (confrontabile con IV)
        assert fv[h]["vol_horizon_ann_pct"] > 0
    # base valutaria e convergenza DICHIARATE nel payload (fix E1/E2)
    assert "returns_basis" in garch_sintetico
    assert garch_sintetico["convergence"]["chosen_converged"] is True


def test_garch_ci_non_hardcoded(garch_sintetico):
    """CI veri (quantili della vol simulata, fix B1): non piu' vol*0.85/vol*1.15
    identici su ogni orizzonte; l'ampiezza CRESCE con l'orizzonte (a 1g la
    varianza GARCH e' quasi deterministica dati i parametri)."""
    fv = garch_sintetico["forecast_vol"]
    ratios, widths = [], {}
    for h in ("1d", "5d", "22d"):
        v = fv[h]["vol_annualized_pct"]
        ratios.append((round(fv[h]["ci95_low_pct"] / v, 4),
                       round(fv[h]["ci95_high_pct"] / v, 4)))
        widths[h] = fv[h]["ci95_high_pct"] - fv[h]["ci95_low_pct"]
        assert fv[h]["ci95_low_pct"] <= v + 0.01, (h, fv[h])
        assert fv[h]["ci95_high_pct"] >= v - 0.01, (h, fv[h])
    tutti_085_115 = all(lo == pytest.approx(0.85, abs=0.005) and
                        hi == pytest.approx(1.15, abs=0.005) for lo, hi in ratios)
    assert not tutti_085_115, f"CI ancora hardcoded +/-15%: {ratios}"
    assert widths["22d"] > widths["1d"], widths


# ============================================================
# MONTE CARLO — simulatori puri
# ============================================================

def test_parametric_t_cov_riprodotta():
    """La multivariate-t deve riprodurre la cov EMPIRICA in input (fix scala
    (df-2)/df). df=10 per momenti finiti; tolleranza 10% relativa."""
    from bellomberg.portfolio.portfolio_montecarlo import _simulate_parametric_t
    np.random.seed(101)
    vols = np.array([0.01, 0.03])
    corr = np.array([[1.0, 0.4], [0.4, 1.0]])
    cov = np.outer(vols, vols) * corr
    sim = _simulate_parametric_t(np.zeros(2), cov, n_periods=20, n_sims=4000, df=10)
    flat = sim.reshape(-1, 2)
    cov_emp = np.cov(flat.T)
    assert cov_emp[0, 0] == pytest.approx(cov[0, 0], rel=0.10)
    assert cov_emp[1, 1] == pytest.approx(cov[1, 1], rel=0.10)
    assert cov_emp[0, 1] == pytest.approx(cov[0, 1], rel=0.15)


def test_block_bootstrap_drift_ricentrato():
    """drift_adjust=0 -> media simulata ~0 anche se la storia ha drift positivo."""
    from bellomberg.portfolio.portfolio_montecarlo import _simulate_block_bootstrap
    np.random.seed(102)
    rr = np.random.normal([0.002, -0.001], 0.01, size=(400, 2))
    sim = _simulate_block_bootstrap(rr, n_periods=100, n_sims=300,
                                    drift_adjust=np.zeros(2))
    medie = sim.reshape(-1, 2).mean(axis=0)
    # SE ~ 0.01/sqrt(30000) ~ 6e-5 -> tolleranza 5e-4 (largamente sopra 3 SE)
    assert abs(medie[0]) < 5e-4
    assert abs(medie[1]) < 5e-4


def test_fhs_drift_zero_mediana_flat():
    """FHS con drift zero: mediana dei percorsi ~flat (tolleranza 2%, incluso
    il vol-drag atteso exp(-0.5*sigma^2*T) con vol 0.6%/g su 252 giorni)."""
    from bellomberg.portfolio.portfolio_montecarlo import _simulate_fhs
    np.random.seed(103)
    rr = np.random.normal(0.0, 0.006, size=(400, 3))
    sim, _ = _simulate_fhs(rr, n_periods=252, n_sims=800, drift_adjust=np.zeros(3))
    cum = np.cumprod(1 + sim, axis=1)
    mediane = np.median(cum[:, -1, :], axis=0)
    for m in mediane:
        assert abs(m - 1.0) < 0.02, mediane


def test_fhs_scala_vol_per_asset():
    """Asset con vol 4x -> distribuzione simulata con vol ~4x (ri-scaling coerente)."""
    from bellomberg.portfolio.portfolio_montecarlo import _simulate_fhs
    np.random.seed(104)
    rr = np.column_stack([np.random.normal(0, 0.005, 500),
                          np.random.normal(0, 0.020, 500)])
    sim, meta = _simulate_fhs(rr, n_periods=50, n_sims=400, drift_adjust=None)
    s = sim.reshape(-1, 2).std(axis=0)
    assert 2.5 < s[1] / s[0] < 6.0, s
    assert meta["garch_fallback_idx"] == []  # fit riusciti: nessun fallback zitto


def test_fhs_vol_decade_verso_unconditional():
    """FIX B2 (review 22/07): storia con regime recente ad ALTA vol (3% vs 1% di
    lungo periodo) -> la ricorsione GARCH parte da sigma_fc alto e DECADE verso
    l'unconditional lungo l'orizzonte (prima la vol restava congelata: piatta)."""
    from bellomberg.portfolio.portfolio_montecarlo import _simulate_fhs
    np.random.seed(105)
    calmo = np.random.normal(0, 0.010, size=(450, 1))
    stress = np.random.normal(0, 0.030, size=(50, 1))
    rr = np.vstack([calmo, stress])
    sim, _ = _simulate_fhs(rr, n_periods=252, n_sims=1500, drift_adjust=None)
    vol_g1 = sim[:, 0, 0].std()
    vol_g252 = sim[:, -1, 0].std()
    # con ricorsione vera: vol_g252 << vol_g1 (mean reversion verso ~1.3%)
    assert vol_g252 < 0.75 * vol_g1, (vol_g1, vol_g252)


def test_compute_drift_modes():
    from bellomberg.portfolio.portfolio_montecarlo import _compute_drift
    mu = np.array([0.001, -0.0004])
    assert np.allclose(_compute_drift(mu, "zero"), 0.0)
    assert np.allclose(_compute_drift(mu, "shrinkage"), 0.3 * mu)
    assert np.allclose(_compute_drift(mu, "historical"), mu)
    assert np.allclose(_compute_drift(mu, "roba_ignota"), 0.0)  # default conservativo


def test_cornish_fisher_e_es_su_normale():
    """Su un campione ~N(0, 2%): CF-VaR99 ~ mu - 2.326*sigma; ES95 ~ -2.063*sigma."""
    from bellomberg.portfolio.portfolio_montecarlo import _cornish_fisher_var, _expected_shortfall
    rng = np.random.default_rng(106)
    x = rng.normal(0.0, 0.02, size=200_000)
    cf = _cornish_fisher_var(x, alpha=0.01)
    assert cf == pytest.approx(-2.326 * 0.02, rel=0.03)
    es = _expected_shortfall(x, alpha=0.05)
    assert es == pytest.approx(-2.0627 * 0.02, rel=0.03)


# ============================================================
# MONTE CARLO — stress: fallback e replay SEMPRE dichiarati
# ============================================================

def test_stress_fallback_dichiarato(monkeypatch):
    """Replay impossibile -> fallback a shock_3sigma DICHIARATO nel meta
    (regola 14/07: mai un -3sigma etichettato 'gfc_2008')."""
    import bellomberg.portfolio.portfolio_montecarlo as pm
    monkeypatch.setattr(pm, "_stress_window_returns",
                        lambda *a, **k: (None, "finestra vuota (test)"))
    sim = np.zeros((10, 20, 2))
    rdf = pd.DataFrame(np.random.default_rng(1).normal(0, 0.01, (100, 2)),
                       columns=["AAA", "BBB"])
    cov = np.diag([1e-4, 4e-4])
    out, meta = pm._apply_stress(sim, "gfc_2008", returns_df=rdf, cov=cov)
    assert meta["requested"] == "gfc_2008"
    assert meta["applied"] == "shock_3sigma"
    assert meta["fallback"] is True
    assert "finestra vuota" in meta["fallback_reason"]
    # giorno 0 = -3 sigma per asset
    assert np.allclose(out[:, 0, 0], -3 * 0.01)
    assert np.allclose(out[:, 0, 1], -3 * 0.02)


def test_stress_replay_sostituisce_i_giorni(monkeypatch):
    import bellomberg.portfolio.portfolio_montecarlo as pm
    idx = pd.bdate_range("2008-09-15", periods=5)
    win = pd.DataFrame({"AAA": [-0.05, -0.02, 0.01, -0.04, 0.03],
                        "BBB": [-0.08, 0.01, -0.03, -0.06, 0.02]}, index=idx)
    monkeypatch.setattr(pm, "_stress_window_returns",
                        lambda *a, **k: (win, {"window": {"trading_days": 5},
                                               "real_history": ["AAA", "BBB"],
                                               "proxied": {}}))
    sim = np.full((7, 20, 2), 0.001)
    rdf = pd.DataFrame(np.zeros((100, 2)), columns=["AAA", "BBB"])
    out, meta = pm._apply_stress(sim, "gfc_2008", returns_df=rdf, cov=None)
    assert meta["fallback"] is False
    assert meta["replaced_days"] == 5
    # replay IDENTICO su ogni sim, giorni successivi intatti
    for s in range(7):
        assert np.allclose(out[s, :5, :], win.values)
        assert np.allclose(out[s, 5:, :], 0.001)


def test_stress_scenario_ignoto_dichiarato():
    import bellomberg.portfolio.portfolio_montecarlo as pm
    sim = np.zeros((3, 5, 2))
    out, meta = pm._apply_stress(sim, "scenario_inventato")
    assert meta["fallback"] is True
    assert meta["applied"] == "none"


# ============================================================
# MONTE CARLO — end-to-end offline: default drift zero, payload onesto
# ============================================================

class _FakeDBMC:
    def get_portfolio_summary(self):
        return {"positions": [], "totale_valore_mercato_eur": 100000.0}


def _mc_offline(monkeypatch, returns_df, **kwargs):
    import bellomberg.portfolio.portfolio_montecarlo as pm
    monkeypatch.setattr(pm, "MemoryDB", _FakeDBMC)
    # nuova firma fix E10: (pesi normalizzati, NAV EUR del perimetro ex-SKIP)
    monkeypatch.setattr(pm, "_get_holdings_weights",
                        lambda salta: ({"AAA": 0.6, "BBB": 0.4}, 100000.0))
    monkeypatch.setattr(pm, "_download_returns", lambda *a, **k: returns_df)
    pm.invalidate_cache()
    out = pm.run_monte_carlo(n_sims=2000, horizon_days=60,
                             method="block_bootstrap", force_refresh=True, **kwargs)
    pm.invalidate_cache()
    return out


def test_run_monte_carlo_default_drift_zero_mediana_flat(monkeypatch):
    """Il default di RISCHIO e' drift zero (firma) e la mediana resta ~flat."""
    from bellomberg.portfolio.portfolio_montecarlo import run_monte_carlo
    assert inspect.signature(run_monte_carlo).parameters["drift_mode"].default == "zero"

    rng = np.random.default_rng(200)
    idx = pd.bdate_range("2022-01-03", periods=500)
    rdf = pd.DataFrame({"AAA": rng.normal(0.001, 0.01, 500),   # drift storico positivo
                        "BBB": rng.normal(0.001, 0.015, 500)}, index=idx)
    out = _mc_offline(monkeypatch, rdf)
    assert "error" not in out, out.get("error")
    assert out["drift_mode"] == "zero"
    # drift storico +0.1%/g ANNULLATO dal drift zero: mediana 60g ~flat (+/-3%)
    assert abs(out["median_return_pct"]) < 3.0, out["median_return_pct"]
    assert out["stress_scenario"] == "none"
    assert out["base_nav_eur"] == pytest.approx(100000.0)
    assert out["percentiles_eur"]["p50"] == pytest.approx(
        (1 + out["median_return_pct"] / 100) * 100000.0, rel=0.01)


def test_run_monte_carlo_taglio_campione_dichiarato(monkeypatch):
    """Ticker giovane che taglia il panel oltre il 40% -> calibration_note DICHIARATA."""
    rng = np.random.default_rng(201)
    idx = pd.bdate_range("2022-01-03", periods=500)
    giovane = np.full(500, np.nan)
    giovane[-150:] = rng.normal(0, 0.02, 150)
    rdf = pd.DataFrame({"AAA": rng.normal(0, 0.01, 500), "BBB": giovane}, index=idx)
    out = _mc_offline(monkeypatch, rdf)
    assert "error" not in out, out.get("error")
    assert out["lookback_days_calibration"] == 150
    assert out["calibration_note"] is not None
    assert "BBB" in out["calibration_note"]


def test_run_monte_carlo_stress_fallback_nel_payload(monkeypatch):
    """gfc_2008 non replicabile -> payload con stress_scenario=shock_3sigma,
    stress_requested=gfc_2008, stress_fallback=True (mai mislabeling)."""
    import bellomberg.portfolio.portfolio_montecarlo as pm
    monkeypatch.setattr(pm, "_stress_window_returns",
                        lambda *a, **k: (None, "niente storia (test)"))
    rng = np.random.default_rng(202)
    idx = pd.bdate_range("2022-01-03", periods=400)
    rdf = pd.DataFrame({"AAA": rng.normal(0, 0.01, 400),
                        "BBB": rng.normal(0, 0.012, 400)}, index=idx)
    out = _mc_offline(monkeypatch, rdf, stress_scenario="gfc_2008")
    assert "error" not in out, out.get("error")
    assert out["stress_requested"] == "gfc_2008"
    assert out["stress_scenario"] == "shock_3sigma"
    assert out["stress_fallback"] is True
    assert "niente storia" in out["stress_meta"]["fallback_reason"]


# ============================================================
# FACTORS — regressione con beta NOTI -> li recupera
# ============================================================

def test_factor_regression_recupera_beta_noti(monkeypatch, tmp_path):
    """Asset costruito con alpha e beta noti sui fattori sintetici: la regressione
    HAC deve recuperarli. Tolleranze dichiarate: beta +/-0.05 (SE ~0.011 con
    n=750, noise 0.3%/g), alpha annualizzato +/-4pt."""
    import bellomberg.portfolio.portfolio_factors as pf
    import bellomberg.storage.negozi_privati as np_

    alias = tmp_path / "alias_fonti.json"
    alias.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(alias))

    rng = np.random.default_rng(300)
    n = 750
    idx = pd.bdate_range("2022-01-03", periods=n)
    fattori = {c: rng.normal(0, 0.01, n)
               for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]}
    ff = pd.DataFrame(fattori, index=idx)
    ff["RF"] = 0.0001

    beta_veri = {"Mkt-RF": 1.2, "SMB": 0.5, "HML": -0.3,
                 "RMW": 0.1, "CMA": 0.0, "Mom": 0.2}
    alpha_vero = 0.0003  # 0.03%/g -> 7.56% annuo
    eps = rng.normal(0, 0.003, n)
    r_asset = ff["RF"] + alpha_vero + sum(b * ff[c] for c, b in beta_veri.items()) + eps
    prezzi = pd.DataFrame({"Close": 100.0 * np.cumprod(1 + r_asset)}, index=idx)

    monkeypatch.setattr("yfinance.download", lambda *a, **k: prezzi.copy())
    out = pf.compute_holding_exposure("TESTUS", period="3y", ff_returns=ff,
                                      add_btc_factor=False, factor_region="us")
    assert out is not None
    assert out["n_obs"] >= 700
    assert out["beta_market"] == pytest.approx(1.2, abs=0.05)
    assert out["beta_smb"] == pytest.approx(0.5, abs=0.05)
    assert out["beta_hml"] == pytest.approx(-0.3, abs=0.05)
    assert out["beta_rmw"] == pytest.approx(0.1, abs=0.05)
    assert out["beta_cma"] == pytest.approx(0.0, abs=0.05)
    assert out["beta_mom"] == pytest.approx(0.2, abs=0.05)
    assert out["alpha_annualized_pct"] == pytest.approx(7.56, abs=4.0)
    assert out["r_squared"] > 0.85
    # lag Newey-West = floor(4*(n/100)^(2/9)) con n effettivo della regressione
    lag_atteso = int(np.floor(4 * (out["n_obs"] / 100) ** (2 / 9)))
    assert out["nw_lag"] == lag_atteso
    assert out["cov_type"] == "HAC-NeweyWest"
    assert abs(out["alpha_tstat"]) > 0  # riportato, non inventato


def test_factor_regression_beta_zero_non_inventa_esposizione(monkeypatch, tmp_path):
    """Asset di puro rumore (beta veri = 0): nessun beta 'trovato' oltre il rumore
    e alpha ~0 — il modulo non deve inventare esposizioni."""
    import bellomberg.portfolio.portfolio_factors as pf
    import bellomberg.storage.negozi_privati as np_

    alias = tmp_path / "alias_fonti.json"
    alias.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(alias))

    rng = np.random.default_rng(301)
    n = 750
    idx = pd.bdate_range("2022-01-03", periods=n)
    ff = pd.DataFrame({c: rng.normal(0, 0.01, n)
                       for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]}, index=idx)
    ff["RF"] = 0.0001
    r_asset = ff["RF"] + rng.normal(0, 0.003, n)
    prezzi = pd.DataFrame({"Close": 100.0 * np.cumprod(1 + r_asset)}, index=idx)

    monkeypatch.setattr("yfinance.download", lambda *a, **k: prezzi.copy())
    out = pf.compute_holding_exposure("TESTUS2", period="3y", ff_returns=ff,
                                      add_btc_factor=False, factor_region="us")
    assert out is not None
    for k in ["beta_market", "beta_smb", "beta_hml", "beta_rmw", "beta_cma", "beta_mom"]:
        assert abs(out[k]) < 0.06, (k, out[k])
    assert abs(out["alpha_annualized_pct"]) < 4.0
    assert out["r_squared"] < 0.05
