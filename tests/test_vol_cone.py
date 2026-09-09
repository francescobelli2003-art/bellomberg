"""Test OFFLINE vol_cone (lotto c 25/07, residuo dichiarato della (39)).

Tutti i numeri sono A VALORI A MANO (convenzione della suite): rendimenti
scelti, std campionaria e percentili ricalcolati su carta. Zero rete: closes
e vol_surface stubbati; cache pulita a ogni test.
"""
import math

import pytest


@pytest.fixture(autouse=True)
def _negozio_iv(monkeypatch, tmp_path):
    """La lista dei ticker con IV storica e' un DATO del negozio privato (iv_tickers.json): qui si
    INIETTA un negozio col benchmark e un simbolo inventato, cosi' la batteria non dipende dal
    book ne' dal negozio vero (05/09, lotto 5)."""
    from bellomberg.storage import negozi_privati
    p = tmp_path / "iv_tickers.json"
    p.write_text('{"tickers": ["SPY", "ALFA"]}', encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_IV", str(p))

import bellomberg.portfolio.vol_cone as vc


@pytest.fixture(autouse=True)
def cache_pulita():
    vc._CACHE.clear()
    yield
    vc._CACHE.clear()


# ---------------------------------------------------------------
# mattoni: percentile, std campionaria, serie rolling
# ---------------------------------------------------------------

def test_percentile_interpolazione_lineare():
    s = [1.0, 2.0, 3.0, 4.0]
    assert vc._percentile(s, 0) == 1.0
    assert vc._percentile(s, 100) == 4.0
    assert vc._percentile(s, 25) == pytest.approx(1.75)   # pos 0.75
    assert vc._percentile(s, 50) == pytest.approx(2.5)
    assert vc._percentile(s, 75) == pytest.approx(3.25)
    assert vc._percentile([7.0], 50) == 7.0               # singolo valore


def test_sample_std_a_mano():
    # [0.01, -0.01]: media 0, var (1e-4+1e-4)/1 = 2e-4
    assert vc._sample_std([0.01, -0.01]) == pytest.approx(math.sqrt(2e-4))
    # [1,2,3]: media 2, var (1+0+1)/2 = 1
    assert vc._sample_std([1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_rolling_series_a_mano():
    rets = [0.01, -0.01, 0.01, -0.01]
    serie = vc._rolling_vol_series(rets, 2)
    # ogni coppia (+-1%): std sqrt(2e-4), annualizzata sqrt(252) -> sqrt(0.0504)
    atteso = math.sqrt(0.0504)
    assert len(serie) == 3
    for v in serie:
        assert v == pytest.approx(atteso, abs=1e-9)
    # finestra piu' lunga dei rendimenti: vuota, mai inventata
    assert vc._rolling_vol_series(rets, 5) == []
    assert vc._rolling_vol_series(rets, 1) == []   # ddof=1 richiede >=2


def test_nearest_window():
    assert vc._nearest_window(7, (5, 10, 21, 63)) == 5     # |2| vs |3|
    assert vc._nearest_window(8, (5, 10, 21, 63)) == 10    # |3| vs |2|
    assert vc._nearest_window(40, (5, 10, 21, 63)) == 21   # |19| vs |23|
    assert vc._nearest_window(200, (5, 10, 21, 63)) == 63
    assert vc._nearest_window(5, (4, 6)) == 4              # pareggio -> corta


def test_calendario_vs_borsa_review_m1():
    # 45*252/365 = 31.07 -> 31; 60*252/365 = 41.4 -> 41; 365 -> 252
    assert vc._to_trading_days(45) == 31
    assert vc._to_trading_days(60) == 41
    assert vc._to_trading_days(365) == 252
    # LA regressione della review M1: il 45DTE canonico (calendario) deve
    # finire sulla finestra MENSILE (|31-21|=10), non trimestrale (|31-63|=32).
    # Senza conversione: |45-63|=18 < |45-21|=24 -> 63, sbagliato.
    assert vc._nearest_window(vc._to_trading_days(45), vc.WINDOWS) == 21
    assert vc._nearest_window(vc._to_trading_days(60), vc.WINDOWS) == 21


# ---------------------------------------------------------------
# build_cone (core puro)
# ---------------------------------------------------------------

def _closes_da_rets(rets, base=100.0):
    out = [base]
    for r in rets:
        out.append(out[-1] * (1 + r))
    return out


def test_build_cone_a_mano():
    # rets scelti: +1%, -1%, +2%, -2% con finestra 2 -> 3 osservazioni rolling:
    #  (+1,-1): sqrt(2e-4*252)          = 0.224499
    #  (-1,+2): sqrt(4.5e-4*252)        = 0.336749
    #  (+2,-2): sqrt(8e-4*252)          = 0.449000
    closes = _closes_da_rets([0.01, -0.01, 0.02, -0.02])
    out = vc.build_cone(closes, windows=(2,))
    assert out["n_returns"] == 4
    w = out["windows"][0]
    assert w["window"] == 2 and w["n_obs"] == 3 and w["young"] is True
    assert w["min"] == pytest.approx(0.2245, abs=2e-4)
    assert w["p25"] == pytest.approx(0.2806, abs=2e-4)   # 0.2245+0.5*(0.3367-0.2245)
    assert w["p50"] == pytest.approx(0.3367, abs=2e-4)
    assert w["p75"] == pytest.approx(0.3929, abs=2e-4)   # 0.3367+0.5*(0.4490-0.3367)
    assert w["max"] == pytest.approx(0.4490, abs=2e-4)
    assert w["current"] == pytest.approx(0.4490, abs=2e-4)  # ultima finestra


def test_build_cone_chiusure_costanti():
    out = vc.build_cone([100.0] * 10, windows=(3,))
    w = out["windows"][0]
    assert w["n_obs"] == 7
    assert w["min"] == w["max"] == w["current"] == 0.0


def test_build_cone_finestra_senza_dati():
    closes = _closes_da_rets([0.01, -0.01, 0.02])   # 3 rendimenti
    out = vc.build_cone(closes, windows=(5,))
    w = out["windows"][0]
    assert w["n_obs"] == 0 and "insufficienti" in w["error"]
    assert "current" not in w   # niente numeri inventati sulla finestra vuota


def test_build_cone_input_invalidi():
    assert "insufficienti" in vc.build_cone([100.0, 101.0])["error"]
    assert "non valid" in vc.build_cone([100.0, 0.0, 101.0])["error"]
    assert "non valid" in vc.build_cone([100.0, -5.0, 101.0])["error"]
    assert "non valid" in vc.build_cone([100.0, "x", 101.0])["error"]
    # review B2: NaN e' float, passava la guardia e produceva percentili nan
    assert "non valid" in vc.build_cone([100.0, float("nan"), 101.0])["error"]


# ---------------------------------------------------------------
# compute_vol_cone (wrapper: guardie, confronto, cache)
# ---------------------------------------------------------------

def _stub_surface(slices):
    def _f(ticker, **kw):
        return {"slices": slices}
    return _f


def test_compute_ticker_fuori_lista():
    out = vc.compute_vol_cone("AAPL")
    assert "fuori dalla lista" in out["error"]


def test_compute_payload_completo(monkeypatch):
    # closes a rendimento COSTANTE (+0.1%): ogni finestra rolling ha std 0 ->
    # cone tutto a 0 e qualsiasi IV positiva sta al 100% del realized.
    closes = _closes_da_rets([0.001] * 129)
    monkeypatch.setattr(vc, "_fetch_closes", lambda t: closes)
    from bellomberg.portfolio import vol_surface
    monkeypatch.setattr(vol_surface, "build_vol_surface", _stub_surface([
        {"expiry": "2026-08-01", "days": 7, "atm_iv": 0.20},
        {"expiry": "2026-09-04", "days": 40, "atm_iv": 0.25},
        {"expiry": "2026-10-30", "days": 100, "atm_iv": 0.30},
        {"expiry": "2026-11-30", "days": 130, "atm_iv": None},  # scartata
    ]))
    out = vc.compute_vol_cone("SPY")
    assert not out.get("error")
    assert [w["n_obs"] for w in out["realized"]["windows"]] == [125, 120, 109, 67]
    assert all(w["min"] == w["max"] == 0.0 for w in out["realized"]["windows"])
    assert len(out["implied"]["slices"]) == 3   # la slice senza atm_iv e' fuori
    assert [c["window"] for c in out["confronto"]] == [5, 21, 63]
    assert all(c["pct_realized_leq_iv"] == 100.0 for c in out["confronto"])
    assert "ddof=1" in out["basis"] and out["src"]


def test_compute_implied_giu_dichiarato_e_non_cachato(monkeypatch):
    chiamate = {"n": 0}
    def _fetch(t):
        chiamate["n"] += 1
        return _closes_da_rets([0.001] * 70)
    monkeypatch.setattr(vc, "_fetch_closes", _fetch)
    from bellomberg.portfolio import vol_surface
    monkeypatch.setattr(vol_surface, "build_vol_surface",
                        lambda t, **kw: {"error": "polygon giu'"})
    out = vc.compute_vol_cone("SPY")
    assert "polygon" in out["implied"]["error"]
    assert out["confronto"] == []
    assert not out.get("error")            # la parte realized resta buona
    vc.compute_vol_cone("SPY")
    assert chiamate["n"] == 2              # implied.error -> MAI cachato


def test_compute_cache_e_force(monkeypatch):
    chiamate = {"n": 0}
    def _fetch(t):
        chiamate["n"] += 1
        return _closes_da_rets([0.001] * 70)
    monkeypatch.setattr(vc, "_fetch_closes", _fetch)
    from bellomberg.portfolio import vol_surface
    monkeypatch.setattr(vol_surface, "build_vol_surface", _stub_surface(
        [{"expiry": "2026-08-01", "days": 7, "atm_iv": 0.2}]))
    vc.compute_vol_cone("SPY")
    vc.compute_vol_cone("SPY")
    assert chiamate["n"] == 1              # payload completo -> cache 10'
    vc.compute_vol_cone("SPY", force=True)
    assert chiamate["n"] == 2              # force bypassa


def test_compute_closes_giu_dichiarato(monkeypatch):
    def _boom(t):
        raise RuntimeError("yfinance giu'")
    monkeypatch.setattr(vc, "_fetch_closes", _boom)
    out = vc.compute_vol_cone("ALFA")
    assert "chiusure non disponibili" in out["error"]
    assert "yfinance giu'" in out["error"]


def test_compute_cone_rotto_propagato(monkeypatch):
    # review B3b: build_cone in errore dentro il wrapper = error top-level
    monkeypatch.setattr(vc, "_fetch_closes", lambda t: [100.0])
    out = vc.compute_vol_cone("SPY")
    assert "insufficienti" in out["error"]


def test_compute_slice_su_finestra_vuota_dichiarata(monkeypatch):
    # review B3a: 60 rendimenti -> finestra 63 vuota; una expiry lunga
    # (100g calendario -> 69 borsa -> w63) deve dichiarare il buco, mai
    # un percentile inventato.
    closes = _closes_da_rets([0.001] * 60)
    monkeypatch.setattr(vc, "_fetch_closes", lambda t: closes)
    from bellomberg.portfolio import vol_surface
    monkeypatch.setattr(vol_surface, "build_vol_surface", _stub_surface([
        {"expiry": "2026-08-01", "days": 7, "atm_iv": 0.20},
        {"expiry": "2026-10-30", "days": 100, "atm_iv": 0.30},
    ]))
    out = vc.compute_vol_cone("SPY")
    per_w = {c["window"]: c for c in out["confronto"]}
    assert per_w[5]["pct_realized_leq_iv"] == 100.0        # finestra viva: numero
    assert "senza cone" in per_w[63]["error"]              # finestra vuota: buco urlato
    assert "pct_realized_leq_iv" not in per_w[63]
