"""REVIEW QUANT (brief audit/16, FASE 2) — prove numeriche su dati sintetici:
var_backtest (Kupiec/Christoffersen/rolling), portfolio_risk (helper rendimenti,
conversione EUR, Ledoit-Wolf), Component VaR (portfolio_analytics).

Regole conftest: ZERO rete, ZERO DB vivo — yfinance e MemoryDB stubbati.
Ogni tolleranza e' DICHIARATA nel test; i dati sintetici hanno verita' nota.
"""
import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats as st

from bellomberg.portfolio.var_backtest import kupiec_pof, christoffersen_independence
from bellomberg.portfolio.portfolio_risk import (_weighted_portfolio_returns, _convert_returns_to_eur,
                            _ledoit_wolf_cc, _norm_currency)


# ============================================================
# KUPIEC POF — la statistica LR e' quella di letteratura
# ============================================================

def test_kupiec_lr_valore_esatto():
    """LR ricalcolato a mano dalla definizione Kupiec (1995) per (T=250, x=13, p=5%)."""
    T, x, p = 250, 13, 0.05
    pi = x / T
    ll_h0 = (T - x) * math.log(1 - p) + x * math.log(p)
    ll_h1 = (T - x) * math.log(1 - pi) + x * math.log(pi)
    lr_atteso = -2 * (ll_h0 - ll_h1)
    out = kupiec_pof(T, x, p)
    assert out["LR"] == pytest.approx(lr_atteso, abs=1e-3)
    assert out["p_value"] == pytest.approx(1 - st.chi2.cdf(lr_atteso, df=1), abs=1e-4)
    assert out["expected"] == pytest.approx(T * p, abs=0.1)


def test_kupiec_copertura_giusta_non_rifiuta():
    """Eccezioni Bernoulli(5%) iid su T=2000 -> Kupiec NON rifiuta (seed fisso)."""
    rng = np.random.default_rng(42)
    exc = rng.binomial(1, 0.05, size=2000)
    out = kupiec_pof(len(exc), int(exc.sum()), 0.05)
    assert out["pass_5pct"] is True


def test_kupiec_copertura_sbagliata_rifiuta():
    """12% di eccezioni dichiarando il 5% su T=500 -> rifiuto netto."""
    out = kupiec_pof(500, 60, 0.05)
    assert out["pass_5pct"] is False
    assert out["p_value"] < 0.001


def test_kupiec_zero_eccezioni_non_crasha():
    """x=0 (0*ln(0) via _xlogy): niente crash; 0 eccezioni su 500 al 5% e' sospetto -> rifiuta."""
    out = kupiec_pof(500, 0, 0.05)
    assert np.isfinite(out["LR"])
    assert out["pass_5pct"] is False  # LR = -2*500*ln(0.95) ~ 51


# ============================================================
# CHRISTOFFERSEN INDEPENDENCE — clustering rilevato, iid accettato
# ============================================================

def test_christoffersen_iid_accetta():
    rng = np.random.default_rng(7)
    exc = rng.binomial(1, 0.05, size=2000)
    out = christoffersen_independence(exc)
    assert out["pass_5pct"] is True


def test_christoffersen_cluster_rifiuta():
    """Eccezioni in raffiche da 5 (grappoli veri) -> indipendenza rifiutata."""
    blocco = [1] * 5 + [0] * 95
    exc = np.array(blocco * 10)
    out = christoffersen_independence(exc)
    assert out["pass_5pct"] is False
    assert out["consecutive_exceptions"] >= 30  # n11 alto per costruzione


def test_christoffersen_serie_senza_eccezioni_non_crasha():
    out = christoffersen_independence(np.zeros(100, dtype=int))
    assert out["LR"] == pytest.approx(0.0, abs=1e-9)
    assert out["pass_5pct"] is True


def test_christoffersen_transizioni_contate_giuste():
    """Conteggio transizioni verificato a mano su una serie corta nota:
    [0,1,1,0,0,1,0] -> coppie (0,1)(1,1)(1,0)(0,0)(0,1)(1,0)."""
    exc = np.array([0, 1, 1, 0, 0, 1, 0])
    out = christoffersen_independence(exc)
    tr = out["transitions"]
    assert (tr["n00"], tr["n01"], tr["n10"], tr["n11"]) == (1, 2, 2, 1)


# ============================================================
# BACKTEST VaR END-TO-END (offline): iid normale con vol nota
# -> hit-rate ~5%/1% e Kupiec che non rifiuta; pipeline reale
#    (FakeDB + yfinance stub), stessa strada del codice vivo.
# ============================================================

class _FakeDB:
    def get_portfolio_summary(self):
        return {"positions": [
            {"ticker": "AAA", "valuta": "EUR", "valore_mercato": 60000, "peso_pct": 60},
            {"ticker": "BBB", "valuta": "EUR", "valore_mercato": 40000, "peso_pct": 40},
        ], "totale_valore_mercato_eur": 100000}


def _fake_yf_download_prices(n_days: int, vols, seed: int):
    """Prezzi sintetici (cumprod di rendimenti normali iid a vol nota) in formato
    yf.download multi-ticker: colonne MultiIndex (campo, ticker)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-02", periods=n_days)
    data = {}
    for i, (tkr, v) in enumerate(vols.items()):
        r = rng.normal(0.0, v, size=n_days)
        data[("Close", tkr)] = 100.0 * np.cumprod(1 + r)
    df = pd.DataFrame(data, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


def test_backtest_var_hit_rate_iid(monkeypatch, tmp_path):
    """Book sintetico EUR, rendimenti iid N(0, 1%): il backtest completo deve
    misurare un hit-rate ~5% (VaR95) e ~1% (VaR99) e Kupiec NON deve rifiutare.
    Tolleranza hit-rate: [3.5%, 6.5%] e [0.4%, 2.0%] su ~1050 giorni testati."""
    import bellomberg.portfolio.var_backtest as vb
    import bellomberg.storage.negozi_privati as np_

    prices = _fake_yf_download_prices(1300, {"AAA": 0.010, "BBB": 0.012}, seed=11)
    alias = tmp_path / "alias_fonti.json"
    alias.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(alias))
    monkeypatch.setattr("yfinance.download",
                        lambda *a, **k: prices.copy())
    monkeypatch.setattr('bellomberg.storage.memory_db.MemoryDB', _FakeDB)

    out = vb.backtest_var(window=252, period="5y")
    assert "error" not in out, out.get("error")
    assert out["n_obs_tested"] == out["n_obs_series"] - 252

    k95 = out["var95"]["kupiec_pof"]
    k99 = out["var99"]["kupiec_pof"]
    assert 3.5 <= k95["exception_rate_pct"] <= 6.5, k95
    assert 0.4 <= k99["exception_rate_pct"] <= 2.0, k99
    assert k95["pass_5pct"] is True
    assert out["var95"]["christoffersen_ind"]["pass_5pct"] is True
    # conditional coverage = somma delle due LR ~ chi2(2)
    cc = out["var95"]["conditional_coverage"]
    lr_atteso = k95["LR"] + out["var95"]["christoffersen_ind"]["LR"]
    assert cc["LR"] == pytest.approx(lr_atteso, abs=1e-6)
    assert cc["p_value"] == pytest.approx(float(1 - st.chi2.cdf(lr_atteso, df=2)), abs=1e-4)


# ============================================================
# _weighted_portfolio_returns — verita' nota calcolata a mano
# ============================================================

def test_weighted_returns_rinormalizza_e_scarta():
    idx = pd.bdate_range("2024-01-01", periods=4)
    returns = pd.DataFrame({
        "AAA": [0.01, 0.01, np.nan, np.nan],
        "BBB": [0.02, np.nan, 0.02, np.nan],
    }, index=idx)
    w = np.array([0.6, 0.4])
    port, meta = _weighted_portfolio_returns(returns, w, ["AAA", "BBB"])
    # g1: 0.6*0.01+0.4*0.02 = 0.014 | g2: solo AAA (peso 0.6>=0.5) -> 0.01
    # g3: solo BBB (peso 0.4<0.5) -> SCARTATO | g4: vuoto -> scartato
    assert port.tolist() == pytest.approx([0.014, 0.01], abs=1e-12)
    assert meta["days_dropped_thin_xsection"] == 2
    assert meta["obs"] == 2


# ============================================================
# _convert_returns_to_eur — formula r_eur = (1+r_l)/(1+r_fx)-1
# ============================================================

def _fx_frame(pair: str, values, idx):
    df = pd.DataFrame({("Close", pair): values}, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


def test_convert_eur_formula_esatta(monkeypatch):
    """NB: serve una serie lunga — la prima obs convertita e' sempre NaN
    (fx_ret parte con pct_change) e su 4 obs la guardia 0.9 scatterebbe:
    difetto minore documentato nel report FASE 3 (prima obs persa per serie)."""
    rng = np.random.default_rng(9)
    idx = pd.bdate_range("2024-01-01", periods=100)
    r_local = pd.Series(rng.normal(0, 0.01, 99), index=idx[1:])
    returns = pd.DataFrame({"AAA": r_local})
    fx_px = 1.10 * np.cumprod(1 + rng.normal(0, 0.004, 100))  # EURUSD=X

    monkeypatch.setattr("yfinance.download",
                        lambda *a, **k: _fx_frame("EURUSD=X", fx_px, idx))
    out, meta = _convert_returns_to_eur(returns, {"AAA": "USD"}, period="1y")
    assert meta["converted"] == ["AAA"]

    fx_ser = pd.Series(fx_px, index=idx).reindex(returns.index).ffill().pct_change()
    atteso = (1 + r_local) / (1 + fx_ser) - 1
    pd.testing.assert_series_equal(out["AAA"], atteso, check_names=False)


def test_convert_eur_gbx_usa_pair_gbp(monkeypatch):
    """GBX/GBp (pence) deve chiedere EURGBP=X, mai EURGBX=X (fix 14/07)."""
    assert _norm_currency("GBX") == "GBP"
    assert _norm_currency("GBp") == "GBP"
    richieste = []

    def fake_dl(symbols, *a, **k):
        richieste.extend(symbols if isinstance(symbols, list) else [symbols])
        idx = pd.bdate_range("2024-01-01", periods=3)
        return _fx_frame("EURGBP=X", [0.85, 0.86, 0.84], idx)

    idx = pd.bdate_range("2024-01-01", periods=3)
    returns = pd.DataFrame({"IOTA.L": [0.01, 0.02]}, index=idx[1:])
    monkeypatch.setattr("yfinance.download", fake_dl)
    _convert_returns_to_eur(returns, {"IOTA.L": "GBX"}, period="1y")
    assert "EURGBP=X" in richieste
    assert all("GBX" not in s for s in richieste)


def test_convert_eur_guardia_anti_distruzione(monkeypatch):
    """FX che INIZIA tardi (prima quotazione al giorno 60 su 100) -> la conversione
    distruggerebbe >10% delle obs: serie TENUTA in valuta locale e DICHIARATA
    (regola no-fallback 14/07), mai colonne azzerate in silenzio."""
    idx = pd.bdate_range("2023-01-02", periods=100)
    rng = np.random.default_rng(3)
    returns = pd.DataFrame({"AAA": rng.normal(0, 0.01, 99)}, index=idx[1:])
    fx_tardivo = _fx_frame("EURUSD=X",
                           1.10 * np.cumprod(1 + rng.normal(0, 0.004, 41)),
                           idx[59:])  # prima quotazione FX al giorno 60

    monkeypatch.setattr("yfinance.download", lambda *a, **k: fx_tardivo)
    out, meta = _convert_returns_to_eur(returns, {"AAA": "USD"}, period="1y")
    assert meta["converted"] == []
    assert any("AAA" in s for s in meta["local_declared"])
    pd.testing.assert_series_equal(out["AAA"], returns["AAA"])  # intatta


def test_convert_eur_fx_stantio_dichiarato(monkeypatch):
    """FIX M2 (review 22/07): FX con 2 obs poi piu' nulla -> guardia di FRESCHEZZA
    (copertura FX reale <80% dei giorni) -> serie dichiarata in valuta locale,
    mai 'convertita' con cambio piatto ffillato."""
    idx = pd.bdate_range("2023-01-02", periods=100)
    rng = np.random.default_rng(4)
    returns = pd.DataFrame({"AAA": rng.normal(0, 0.01, 99)}, index=idx[1:])
    fx_2obs = _fx_frame("EURUSD=X", [1.10, 1.11], idx[:2])

    monkeypatch.setattr("yfinance.download", lambda *a, **k: fx_2obs)
    out, meta = _convert_returns_to_eur(returns, {"AAA": "USD"}, period="1y")
    assert meta["converted"] == [], \
        "FX stantio (2 obs ffillate su 99 giorni) trattato come conversione valida"


# ============================================================
# LEDOIT-WOLF constant-correlation — proprieta' strutturali
# ============================================================

def test_ledoit_wolf_proprieta():
    """delta in [0,1]; varianze campionarie PRESERVATE sulla diagonale; PSD;
    e con poco campione la shrinkage AVVICINA la matrice alla verita'
    (errore Frobenius <= sample cov) su dati a correlazione costante nota."""
    rng = np.random.default_rng(123)
    n, t = 6, 80
    vols = np.array([0.01, 0.015, 0.02, 0.025, 0.03, 0.012])
    corr_true = np.full((n, n), 0.4)
    np.fill_diagonal(corr_true, 1.0)
    cov_true = np.outer(vols, vols) * corr_true
    x = rng.multivariate_normal(np.zeros(n), cov_true, size=t)

    sigma, rbar, delta = _ledoit_wolf_cc(x)
    assert 0.0 <= delta <= 1.0
    xd = x - x.mean(axis=0, keepdims=True)
    sample = xd.T @ xd / t
    assert np.allclose(np.diag(sigma), np.diag(sample), atol=1e-15)
    assert np.min(np.linalg.eigvalsh(sigma)) > -1e-12
    err_lw = np.linalg.norm(sigma - cov_true, "fro")
    err_sample = np.linalg.norm(sample - cov_true, "fro")
    assert err_lw <= err_sample


# ============================================================
# COMPONENT VaR (portfolio_analytics) — Euler: somma = VaR totale
# ============================================================

def _componenti_setup(monkeypatch, n_assets, corr, navs, seed=5):
    import bellomberg.portfolio.portfolio_analytics as pa

    class FakeDB:
        def get_portfolio_summary(self):
            return {"positions": [
                {"ticker": f"T{i}", "valore_mercato": navs[i]} for i in range(n_assets)
            ]}

    rng = np.random.default_rng(seed)
    vols = np.linspace(0.01, 0.02, n_assets)
    corr_m = np.full((n_assets, n_assets), corr)
    np.fill_diagonal(corr_m, 1.0)
    cov = np.outer(vols, vols) * corr_m
    idx = pd.bdate_range("2023-01-02", periods=253)
    rets = rng.multivariate_normal(np.zeros(n_assets), cov, size=253)
    prices = 100.0 * np.cumprod(1 + rets, axis=0)
    df = pd.DataFrame({("Close", f"T{i}"): prices[:, i] for i in range(n_assets)}, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns)

    monkeypatch.setattr(pa, "MemoryDB", FakeDB)
    monkeypatch.setattr("yfinance.download", lambda *a, **k: df.copy())
    return pa, df


def test_component_var_somma_al_totale_2_asset(monkeypatch):
    """Portafoglio a 2 asset, correlazione nota 0.30: le componenti Euler devono
    sommare al VaR totale (tolleranza = solo arrotondamento a 2 decimali EUR)."""
    pa, _ = _componenti_setup(monkeypatch, 2, 0.30, [60000, 40000])
    out = pa.compute_var_contribution(lookback_days=252, confidence=0.05)
    assert "error" not in out, out.get("error")
    somma = sum(it["component_var_eur"] for it in out["items"])
    # Ogni componente e il totale sono arrotondati al centesimo.
    assert somma == pytest.approx(out["portfolio_var_eur_daily"], rel=0, abs=0.015)
    contribs = sum(it["contribution_pct_of_total_var"] for it in out["items"])
    assert contribs == pytest.approx(100.0, abs=0.1)


def test_component_var_riconciliato_con_formula(monkeypatch):
    """Il VaR parametrico del payload coincide con z*sigma_p_daily ricalcolato
    in modo indipendente dagli STESSI rendimenti passati alla funzione."""
    pa, df = _componenti_setup(monkeypatch, 3, 0.25, [50000, 30000, 20000])
    out = pa.compute_var_contribution(lookback_days=252, confidence=0.05)
    assert "error" not in out, out.get("error")

    close = df["Close"]
    rets = close.pct_change().dropna(how="any")
    w = np.array([50000, 30000, 20000], dtype=float)
    w = w / w.sum()
    sigma_daily = math.sqrt(w @ np.cov(rets.values.T) @ w)
    z = float(-st.norm.ppf(0.05))
    assert out["portfolio_var_pct_daily"] == pytest.approx(z * sigma_daily * 100, rel=1e-3)
    assert out["portfolio_var_eur_daily"] == pytest.approx(z * sigma_daily * 100000, rel=0, abs=0.005)
    assert out["z_alpha"] == pytest.approx(z, abs=1e-3)
