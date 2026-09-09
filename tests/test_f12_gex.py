"""Test OFFLINE campo `gex` su GET /options/vol_surface (richiesta frontend
25/07 sera, ok PM "prosegui"; changelog (44)).

Contratto UI (VolSurfacePage.GexProfile): campo `gex` con by_strike[]
{strike, gex_1pct_usd, call_oi, put_oi} + flip_strike + net_gex_1pct_usd +
basis + error dichiarato. Chiave presente = dichiarazione (assente = pannello
in attesa). Zero rete: build_vol_surface, get_iv_context e compute_gex stubbati.
"""
import pytest

import bellomberg.api.bellomberg_api as api


_GEX_OK = {
    "ticker": "SPY", "spot_est": 630.0,
    "expiries_used": ["2026-07-27", "2026-07-31", "2026-08-07"],
    "net_gex_usd_per_1pct": 1.25e9,
    "gamma_flip_strike": 612.5,
    "top_strikes": [
        {"strike": 600.0, "net_gex_usd": -4.0e8, "call_oi": 1000, "put_oi": 9000},
        {"strike": 630.0, "net_gex_usd": 9.0e8, "call_oi": 8000, "put_oi": 2000},
    ],
    "_source": "polygon chain -> GEX", "_timestamp": "x",
}


@pytest.fixture
def endpoint_stubbato(monkeypatch):
    from bellomberg.portfolio import vol_surface; from bellomberg.market_data import iv_history
    monkeypatch.setattr(vol_surface, "build_vol_surface",
                        lambda t, **kw: {"ticker": t.upper(), "slices": []})
    monkeypatch.setattr(iv_history, "get_iv_context",
                        lambda t, **kw: {"error": "stub"})


def test_gex_mapping_contratto_ui(endpoint_stubbato, monkeypatch):
    from bellomberg.portfolio import positioning_tools
    monkeypatch.setattr(positioning_tools, "compute_gex", lambda t, **kw: dict(_GEX_OK))
    out = api.get_vol_surface("SPY")
    g = out["gex"]
    assert "error" not in g
    assert g["by_strike"] == [
        {"strike": 600.0, "gex_1pct_usd": -4.0e8, "call_oi": 1000, "put_oi": 9000},
        {"strike": 630.0, "gex_1pct_usd": 9.0e8, "call_oi": 8000, "put_oi": 2000},
    ]
    assert g["flip_strike"] == 612.5
    assert g["net_gex_1pct_usd"] == 1.25e9
    assert "SqueezeMetrics" in g["basis"] and "3 expiry" in g["basis"]


def test_gex_errore_provider_dichiarato(endpoint_stubbato, monkeypatch):
    from bellomberg.portfolio import positioning_tools
    monkeypatch.setattr(positioning_tools, "compute_gex",
                        lambda t, **kw: {"error": "POLYGON_API_KEY mancante"})
    out = api.get_vol_surface("SPY")
    assert out["gex"] == {"error": "POLYGON_API_KEY mancante"}


def test_gex_eccezione_dichiarata_mai_500(endpoint_stubbato, monkeypatch):
    # compute_gex non dovrebbe MAI propagare ("errori come dict"), ma se
    # succedesse il campo additivo non deve rompere F12: error dichiarato.
    from bellomberg.portfolio import positioning_tools
    def _boom(t, **kw):
        raise RuntimeError("polygon esploso")
    monkeypatch.setattr(positioning_tools, "compute_gex", _boom)
    out = api.get_vol_surface("SPY")
    assert "polygon esploso" in out["gex"]["error"]
    assert out["ticker"] == "SPY"          # il resto del payload sopravvive


def test_gex_chiave_sempre_presente(endpoint_stubbato, monkeypatch):
    # contratto pattern fonti_mute: chiave PRESENTE = dichiarazione resa,
    # solo un payload senza campo lascia il pannello in "attesa backend".
    from bellomberg.portfolio import positioning_tools
    monkeypatch.setattr(positioning_tools, "compute_gex",
                        lambda t, **kw: {"error": "x"})
    out = api.get_vol_surface("SPY")
    assert "gex" in out and "iv_history_context" in out


def test_gex_top_strikes_vuoto_reso_comè(endpoint_stubbato, monkeypatch):
    # by_strike vuoto NON è un errore backend: la UI dichiara da sola
    # "n.d. — by_strike vuoto o insufficiente" (righe <2). Mai inventare righe.
    from bellomberg.portfolio import positioning_tools
    g = dict(_GEX_OK)
    g["top_strikes"] = []
    monkeypatch.setattr(positioning_tools, "compute_gex", lambda t, **kw: g)
    out = api.get_vol_surface("SPY")
    assert out["gex"]["by_strike"] == []
    assert out["gex"]["flip_strike"] == 612.5
