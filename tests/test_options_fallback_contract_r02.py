"""R02 a/b/c (audit follow-up 09/10, Opus 5.5): contratto ATM/spot anche fuori da Polygon.

a. i rami fallback IBKR/yfinance di `_options_data_usa` emettono `atm_status`
   esplicito: l'ATM IV non qualificata non entra nello score, l'osservazione
   grezza resta a parte e dichiarata;
b. lo strike usato come spot (vol_surface, compute_gex) e' etichettato e
   `sig_dealer_gamma` non afferma la posizione rispetto al gamma flip;
c. IV <= 0 o non finita = dato invalido, n.d. dichiarato.

Solo valori inventati e ticker sintetici; niente rete, niente DB.
"""
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from bellomberg.agents import agent_tools as a
from bellomberg.agents import specialist_scores as s
from bellomberg.market_data import polygon_data as p

EXPIRY = (date.today() + timedelta(days=30)).isoformat()


def _yf_ticker(iv_call=.43, iv_put=.47, close=772.0):
    calls = pd.DataFrame({"strike": [770.0, 780.0], "impliedVolatility": [iv_call, .99],
                          "openInterest": [10, 10]})
    puts = pd.DataFrame({"strike": [770.0, 780.0], "impliedVolatility": [iv_put, .99],
                         "openInterest": [12, 12]})
    return SimpleNamespace(
        options=[EXPIRY],
        option_chain=lambda expiry: SimpleNamespace(calls=calls, puts=puts),
        history=lambda period: pd.DataFrame({"Close": [close]}))


def _yfinance_fallback(monkeypatch, **kw):
    monkeypatch.setattr(p, "polygon_available", lambda: False)
    monkeypatch.setattr(a, "_get_ibkr_options", lambda *args, **kwargs: None)
    monkeypatch.setattr(a, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(a.yf, "Ticker", lambda ticker: _yf_ticker(**kw))
    return a._options_data_usa("ZZTEST")


def test_r02a_yfinance_fallback_declares_unverified_atm_and_keeps_raw(monkeypatch):
    out = _yfinance_fallback(monkeypatch)
    assert out["data_source"] == "yfinance options"
    assert out["atm_status"] == "UNVERIFIED"
    assert "spot_from_yfinance_daily_close" in out["atm_issues"]
    assert "currency_unattested" in out["atm_issues"]
    assert "iv_timestamp_unattested" in out["atm_issues"]
    assert out["atm_iv_call_pct"] is None and out["atm_iv_put_pct"] is None
    raw = out["atm_iv_unqualified_observation"]
    assert raw["status"] == "UNVERIFIED"
    assert raw["call_pct"] == 43.0 and raw["put_pct"] == 47.0
    assert out["iv_hv_comparison_status"] == "UNVERIFIED"


def test_r02a_yfinance_fallback_does_not_score_atm_iv(monkeypatch):
    out = _yfinance_fallback(monkeypatch)
    score = s.options_score("ZZTEST", options_data=out)
    # fix score 09/10 (Opus 5.5): l'ATM IV di UNA scadenza non e' piu' punteggiata in nessun
    # caso (regime su struttura VIX e superficie a ~30g); non qualificata = n.d. dichiarato
    assert score["metrics"]["atm_iv"] is None and score["score"] is None
    atm = [r for r in score["info"] if r[0].startswith("ATM IV")]
    assert atm and atm[0][1].startswith("n.d.: UNVERIFIED")
    assert "UNVERIFIED" in s.format_score_block({**score, "score": 1, "max_score": 3, "verdict": "x"})


def test_r02a_yfinance_invalid_raw_iv_is_declared(monkeypatch):
    out = _yfinance_fallback(monkeypatch, iv_call=float("nan"))
    assert out["atm_iv_unqualified_observation"]["call_pct"] is None
    assert out["atm_iv_unqualified_observation"]["put_pct"] == 47.0
    assert "atm_iv_invalid" in out["atm_issues"]


@pytest.mark.parametrize("spot_source,issue", [
    ("IBKR marketPrice", None),
    ("IBKR close", "spot_is_close_not_live"),
    ("yfinance daily Close (not an intraday quote)", "spot_from_yfinance_daily_close"),
])
def test_r02a_ibkr_fallback_declares_unverified_atm(monkeypatch, spot_source, issue):
    monkeypatch.setattr(p, "polygon_available", lambda: False)
    monkeypatch.setattr(a, "_get_ibkr_options", lambda *args, **kwargs: dict(
        data_source="IBKR_TWS_requested_delayed_frozen", nearest_expiry=EXPIRY.replace("-", ""),
        spot=777.0, spot_source=spot_source, atm_iv_call_pct=43.0, atm_iv_put_pct=47.0,
        put_call_oi_ratio=1.2, total_call_oi=10, total_put_oi=12))
    out = a._options_data_usa("ZZTEST")
    assert out["atm_status"] == "UNVERIFIED"
    assert "iv_timestamp_unattested" in out["atm_issues"]
    assert "spot_timestamp_unattested" in out["atm_issues"]
    if issue:
        assert issue in out["atm_issues"]
    assert out["atm_iv_call_pct"] is None and out["atm_iv_put_pct"] is None
    assert out["atm_iv_unqualified_observation"]["call_pct"] == 43.0
    score = s.options_score("ZZTEST", options_data=out)
    assert score["metrics"]["atm_iv"] is None and score["score"] is None   # fix score 09/10


@pytest.mark.parametrize("iv", [0, 0.0, -5.0, float("nan"), float("inf")])
def test_r02c_non_positive_or_non_finite_iv_is_declared_nd(iv):
    out = s.options_score("ZZTEST", options_data={
        "nearest_expiry": EXPIRY, "atm_iv_call_pct": iv, "atm_iv_put_pct": iv,
        "put_call_oi_ratio": 1.0})
    # fix score 09/10: n.d. dichiarato fra le informative, mai punti
    assert out["metrics"]["atm_iv"] is None and out["score"] is None
    atm = [r for r in out["info"] if r[0].startswith("ATM IV")]
    assert len(atm) == 1 and atm[0][1].startswith("n.d.: IV non valida"), out["info"]


def test_r02c_invalid_iv_alone_is_declared_not_silent():
    out = s.options_score("ZZTEST", options_data={"nearest_expiry": EXPIRY, "atm_iv_call_pct": 0.0})
    assert out is not None
    assert out["score"] is None and out["max_score"] is None
    assert any(r[1].startswith("n.d.: IV non valida") for r in out["info"])   # fix score 09/10
    assert "CALMA" not in str(out) and "COMPLACENTE" not in str(out)


def test_r02c_one_valid_side_still_scored():
    out = s.options_score("ZZTEST", options_data={
        "nearest_expiry": EXPIRY, "atm_iv_call_pct": 18.0, "atm_iv_put_pct": -1.0})
    # fix score 09/10: il lato valido resta visibile (informativo), quello invalido dichiarato
    assert out["metrics"]["atm_iv"] == 18.0 and out["score"] is None
    assert any(r[1] == "n.d.: IV non valida: put <= 0" for r in out["info"]), out["info"]


# ---- review R02 (10/10, Opus 5.5): mutanti sopravvissuti M1 M2 M3 M7 M8 M10 + stringa ----

@pytest.mark.parametrize("raw", [0.0, -0.2])
def test_review_m1_iv_grezza_non_positiva_non_e_un_osservazione(raw):
    # M1: IV <= 0 tenuta come osservazione valida
    out = a._atm_fallback_non_qualificato({"atm_iv_call_pct": raw, "atm_iv_put_pct": 40.0, "spot_source": "ZZ"}, [])
    assert out["atm_iv_unqualified_observation"]["call_pct"] is None
    assert out["atm_iv_unqualified_observation"]["put_pct"] == 40.0
    assert "atm_iv_invalid" in out["atm_issues"]


def test_review_m3_yfinance_iv_zero_dichiarata_invalida(monkeypatch):
    # M3: con un controllo «truthy» l'IV 0 di Yahoo spariva come None senza atm_iv_invalid
    out = _yfinance_fallback(monkeypatch, iv_call=0.0)
    assert "atm_iv_invalid" in out["atm_issues"]
    assert out["atm_iv_unqualified_observation"]["call_pct"] is None


def test_review_m7_osservazione_porta_la_fonte_dello_spot(monkeypatch):
    out = _yfinance_fallback(monkeypatch)
    assert out["atm_iv_unqualified_observation"]["spot_source"] == "yfinance daily Close (not an intraday quote)"


def test_review_m8_e_punto4_yfinance_senza_spot_dice_solo_spot_missing(monkeypatch):
    monkeypatch.setattr(p, "polygon_available", lambda: False)
    monkeypatch.setattr(a, "_get_ibkr_options", lambda *args, **kwargs: None)
    monkeypatch.setattr(a, "YFINANCE_AVAILABLE", True)
    tk = _yf_ticker()
    tk.history = lambda period: pd.DataFrame({"Close": []})
    monkeypatch.setattr(a.yf, "Ticker", lambda ticker: tk)
    out = a._options_data_usa("ZZTEST")
    assert "spot_missing" in out["atm_issues"], out
    assert "spot_from_yfinance_daily_close" not in out["atm_issues"]


def test_review_m2_ibkr_senza_fonte_spot_dichiarata(monkeypatch):
    monkeypatch.setattr(p, "polygon_available", lambda: False)
    monkeypatch.setattr(a, "_get_ibkr_options", lambda *args, **kwargs: dict(
        data_source="IBKR_TWS_requested_delayed_frozen", nearest_expiry=EXPIRY.replace("-", ""),
        spot=777.0, spot_source=None, atm_iv_call_pct=43.0, atm_iv_put_pct=47.0,
        put_call_oi_ratio=1.2, total_call_oi=10, total_put_oi=12))
    out = a._options_data_usa("ZZTEST")
    assert "spot_source_missing" in out["atm_issues"]


def test_review_m10_un_solo_lato_invalido_e_dichiarato():
    out = s.options_score("ZZTEST", options_data={
        "nearest_expiry": EXPIRY, "atm_iv_call_pct": float("nan"), "atm_iv_put_pct": 18.0})
    assert any(r[1] == "n.d.: IV non valida: call non finita" for r in out["info"]), out["info"]


@pytest.mark.parametrize("raw,atteso", [("0.25", "call non numerica"), (True, "call non numerica"),
                                        (float("inf"), "call non finita"), (0, "call <= 0")])
def test_review_punto4_motivo_iv_per_tipo_e_in_inglese(raw, atteso):
    from bellomberg.core.language import language_context
    out = s.options_score("ZZTEST", options_data={"nearest_expiry": EXPIRY, "atm_iv_call_pct": raw})
    assert any(r[1] == "n.d.: IV non valida: " + atteso for r in out["info"]), out["info"]
    with language_context("en"):
        en = s.options_score("ZZTEST", options_data={"nearest_expiry": EXPIRY, "atm_iv_call_pct": raw})
    assert any(str(r[1]).startswith("n/a: invalid IV: call") for r in en["info"]), en["info"]


def test_review_punto1_gex_usa_lo_spot_osservato_da_polygon(monkeypatch):
    # spot VERO dallo snapshot (underlying_asset con timestamp) -> qualificato, rilevatore vivo
    import time
    from bellomberg.portfolio import positioning_tools as pt
    from bellomberg.portfolio import signal_engine as se
    expiry = (date.today() + timedelta(days=10)).isoformat()
    obs = {"price": 703.5, "ticker": "ZZTEST", "last_updated": int((time.time() - 60) * 1e9), "timeframe": "DELAYED"}
    monkeypatch.setattr(p, "polygon_available", lambda: True)
    monkeypatch.setattr(p, "get_option_expirations", lambda _: {"expirations": [expiry]})
    monkeypatch.setattr(p, "get_options_chain", lambda *args, **kw: {"chain": [
        {"gamma": .02, "oi": 100, "strike": 700, "type": "call", "delta": .5, "iv": .2, "underlying_asset": dict(obs)},
        {"gamma": .03, "oi": 300, "strike": 710, "type": "put", "delta": -.5, "iv": .2, "underlying_asset": dict(obs)}],
        "coverage": {"status": "COMPLETE"}})
    g = pt.compute_gex("ZZTEST")
    assert g["spot_est"] == 703.5 and g["spot_qualified"] is True and g["spot_source"] == "polygon_underlying_quote"
    monkeypatch.setattr(pt, "compute_gex", lambda *aa, **kk: dict(g, gamma_flip_strike=g["gamma_flip_strike"] or 690.0))
    esiti = {}
    se.sig_dealer_gamma("ZZTEST", esiti=esiti)
    assert esiti["dealer gamma"] == "interrogato"


# ---- b. strike come spot -------------------------------------------------

def _gex(monkeypatch, has_atm):
    from bellomberg.portfolio import positioning_tools as pt
    expiry = (date.today() + timedelta(days=10)).isoformat()
    monkeypatch.setattr(p, "polygon_available", lambda: True)
    monkeypatch.setattr(p, "get_option_expirations", lambda _: {"expirations": [expiry]})
    monkeypatch.setattr(p, "get_options_chain", lambda *args, **kw: {"chain": [
        {"gamma": .02, "oi": 100, "strike": 700, "type": "call", "delta": .5 if has_atm else .8},
        {"gamma": .03, "oi": 300, "strike": 710, "type": "put", "delta": -.5}]})
    # integrazione 10/10: passo 3 di spot_alignment.resolve_spot (lastPrice) assente nel test
    from bellomberg.market_data import spot_alignment as sa
    monkeypatch.setattr(sa, "_last_price_from_yfinance", lambda t: None)
    return pt.compute_gex("ZZTEST")


@pytest.mark.parametrize("has_atm,code", [(True, "proxy_strike_delta50"), (False, "proxy_strike_median")])
def test_r02b_compute_gex_labels_strike_proxy(monkeypatch, has_atm, code):
    out = _gex(monkeypatch, has_atm)
    assert out["spot_source"] == code
    assert out["spot_qualified"] is False


@pytest.mark.parametrize("payload", [
    {"gamma_flip_strike": 700, "spot_est": 690, "net_gex_usd_per_1pct": -1e4,
     "spot_qualified": False, "spot_source": "proxy_strike_median"},
    {"gamma_flip_strike": 700, "spot_est": 690, "net_gex_usd_per_1pct": -1e4},
])
def test_r02b_dealer_gamma_does_not_assert_position_vs_flip_on_proxy(monkeypatch, payload):
    from bellomberg.core.language import language_context
    from bellomberg.portfolio import positioning_tools
    from bellomberg.portfolio import signal_engine as se
    monkeypatch.setattr(positioning_tools, "compute_gex", lambda *args, **kw: dict(payload))
    for lang in ("it", "en"):
        esiti = {}
        with language_context(lang):
            sigs = se.sig_dealer_gamma("ZZTEST", esiti=esiti)
        text = str(sigs)
        assert "SOTTO il gamma flip" not in text and "BELOW the gamma flip" not in text
        assert "sopra il gamma flip" not in text and "above the gamma flip" not in text
        assert len(sigs) == 1
        sig = sigs[0]
        assert sig["direction"] == "neutral" and sig["strength"] == 0
        assert ("spot non qualificato" in sig["reading"]) if lang == "it" else ("spot not qualified" in sig["reading"])
        # review R02 (10/10): prima asseriva «interrogato» -> Edge Scan «Copertura PIENA» con
        # un rilevatore che non misura nulla. Ora MUTO col motivo: copertura DEGRADATA dichiarata
        assert esiti["dealer gamma"].startswith("MUTO") and "spot non qualificato" in esiti["dealer gamma"]


def test_r02b_vol_surface_labels_strike_proxy_spot(monkeypatch):
    from bellomberg.portfolio import vol_surface as vol
    exp = (date.today() + timedelta(days=20)).isoformat()

    def boom(_):
        raise RuntimeError("synthetic quote unavailable")

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=boom))
    monkeypatch.setattr(p, "polygon_available", lambda: True)
    monkeypatch.setattr(p, "get_option_expirations", lambda _: {"expirations": [exp]})
    chain = []
    for strike in (600, 650, 680, 700, 720, 750, 800):
        chain.append({"type": "call", "strike": strike, "iv": .3, "oi": 10,
                      "delta": .5 if strike == 700 else (.9 if strike < 700 else .2)})
        chain.append({"type": "put", "strike": strike, "iv": .35, "oi": 10,
                      "delta": -.5 if strike == 700 else (-.1 if strike < 700 else -.8)})
    # 10/10 (A1 audit Vol Deck): il ramo campionato legge la chain completa via
    # get_chain_detail (_complete_chain), non piu' get_options_chain(max 400)
    monkeypatch.setattr(vol, "get_chain_detail", lambda *args, **kw: {"chain": list(chain), "complete": True})
    vol._CHAIN_CACHE.clear()
    out = vol.build_vol_surface("ZZTEST", include_context=False)
    assert not out.get("error"), out
    assert out["spot_est"] == 700.0
    assert str(out["spot_source"]).startswith("PROXY")  # la UI legge questo prefisso
    assert out["spot_proxy"] == "proxy_strike_delta50"
    assert out["spot_qualified"] is False
    # R02 punto 4 (10/10): su spot proxy ATM/RR/BF e movimento atteso n.d.
    s0 = out["slices"][0]
    assert s0["atm_iv"] is None and s0["atm_iv_unqualified"] is not None
    assert out["expected_move_pct"] is None and out["metrics_qualified"] is False


def test_r02b_vol_surface_observed_spot_is_not_a_proxy(monkeypatch):
    from bellomberg.portfolio import vol_surface as vol
    exp = (date.today() + timedelta(days=20)).isoformat()
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda _: SimpleNamespace(fast_info={"lastPrice": 701.0})))
    monkeypatch.setattr(p, "polygon_available", lambda: True)
    monkeypatch.setattr(p, "get_option_expirations", lambda _: {"expirations": [exp]})
    chain = []
    for strike in (600, 650, 680, 700, 720, 750, 800):
        chain.append({"type": "call", "strike": strike, "iv": .3, "oi": 10, "delta": .5})
        chain.append({"type": "put", "strike": strike, "iv": .35, "oi": 10, "delta": -.5})
    monkeypatch.setattr(vol, "get_chain_detail", lambda *args, **kw: {"chain": list(chain), "complete": True})
    vol._CHAIN_CACHE.clear()
    out = vol.build_vol_surface("ZZTEST", include_context=False)
    assert not out.get("error"), out
    assert out["spot_est"] == 701.0 and out["spot_proxy"] is None
    # yfinance = ripiego (le pagine non portano il sottostante): mai qualificato
    assert out["spot_qualified"] is False and out["spot_fallback"] is True
