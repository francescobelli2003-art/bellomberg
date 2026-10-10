"""Volatilita' (options_score), GEX (compute_gex) e premio di volatilita' (signal_engine) — test
della LOGICA (fix score 09/10, Opus 5.5; audit SCORE-VOL-QUANT §1, §3.1, §3.3 e VOLDECK M4/M5).
Crash e mercato placido devono dare verdetti OPPOSTI; il regime gamma segue il segno del GEX
netto; lo zero-gamma e' la radice del profilo ricalcolato; una scadenza in errore non e'
«usata»; opzioni care non portano a TRIM/HEDGE. Numeri e ticker INVENTATI, zero rete.
"""
import math
from datetime import date, timedelta

import pytest

from bellomberg.agents import specialist_scores as S
from bellomberg.core.language import language_context


def _d(days):
    return str(date.today() + timedelta(days=days))


def _vix(v30, v3m):
    return {"vix_30d": v30, "vix_3m": v3m, "vix_vix3m_ratio": round(v30 / v3m, 3)}


def _superficie(iv30, rv, rr37, complete=True):
    rows = [{"expiry": _d(9), "days": 9, "atm_iv": iv30, "rr25": rr37, "call_oi": 1000, "put_oi": 1500},
            {"expiry": _d(30), "days": 30, "atm_iv": iv30, "rr25": rr37, "call_oi": 1000, "put_oi": 1600},
            {"expiry": _d(37), "days": 37, "atm_iv": iv30, "rr25": rr37, "call_oi": 900, "put_oi": 2000}]
    cov = [{"expiry": r["expiry"], "status": "loaded" if complete else "partial",
            "chain_complete": complete} for r in rows]
    return {"term_structure": rows, "realized_vol_30d": rv, "coverage": {"rows": cov}}


def _score(vix, surf, **kw):
    with language_context("it"):
        return S.options_score("SPY", vix_ts=vix, vol_surface=surf, **kw)


def _idx(s):
    return 100.0 * s["score"] / s["max_score"]


def test_crash_e_placido_danno_verdetti_opposti():
    # crash: IV 60, realizzata 45, backwardation 1,15, skew put -8 pt
    crash = _score(_vix(45.0, 39.0), _superficie(0.60, 0.45, -0.08))
    # placido: IV 12, realizzata 9,5, contango 0,85, skew -3,5 pt
    placido = _score(_vix(12.0, 14.1), _superficie(0.12, 0.095, -0.035))
    assert _idx(crash) >= 72 and "STRESS" in crash["verdict"], crash
    assert _idx(placido) < 25 and "CALMA" in placido["verdict"], placido


def test_memo_reali_con_vix_fermo_in_contango_non_sono_panico():
    # i 6 rapporti VIX/VIX3M dei memo (0,845..0,936) con superficie tipica: mai «stress»
    for ratio in (0.845, 0.854, 0.865, 0.904, 0.936):
        s = _score(_vix(15.0, 15.0 / ratio), _superficie(0.16, 0.13, -0.05))
        assert "STRESS" not in s["verdict"] and _idx(s) < 50, (ratio, s)


@pytest.mark.parametrize("campo,valori", [
    ("ratio", [0.80, 0.88, 0.92, 0.97, 1.00, 1.05, 1.20]),
    ("rr", [-0.02, -0.04, -0.06, -0.08, -0.11]),
])
def test_monotonia_piu_stress_punteggio_non_minore(campo, valori):
    def uno(v):
        ratio = v if campo == "ratio" else 0.9
        ivrv = v if campo == "ivrv" else 0.03
        rr = v if campo == "rr" else -0.05
        return _score(_vix(18.0, 18.0 / ratio), _superficie(0.18 + ivrv, 0.18, rr))["score"]
    punteggi = [uno(v) for v in valori]
    assert punteggi == sorted(punteggi) and punteggi[-1] > punteggi[0], list(zip(valori, punteggi))


def test_nessun_gradino_attorno_all_inversione_della_curva():
    a = _score(_vix(20.0, 20.0 / 0.9999), _superficie(0.2, 0.17, -0.05))
    b = _score(_vix(20.0, 20.0 / 1.0001), _superficie(0.2, 0.17, -0.05))
    assert abs(_idx(a) - _idx(b)) < 0.5


def test_put_call_e_solo_informativo_e_la_chain_parziale_e_nd():
    base = _score(_vix(15.0, 17.5), _superficie(0.16, 0.13, -0.05))
    od = {"expiry_used": _d(9), "put_call_oi_ratio": 6.8, "coverage": {"status": "COMPLETE"}}
    alto = _score(_vix(15.0, 17.5), _superficie(0.16, 0.13, -0.05), options_data=od)
    assert alto["score"] == base["score"]                       # il P/C non sposta il punteggio
    assert any("6.80" in r[1] for r in alto["info"]), alto["info"]
    parziale = _score(_vix(15.0, 17.5), _superficie(0.16, 0.13, -0.05),
                      options_data={**od, "partial": True, "coverage": {"status": "PARTIAL"}})
    riga = [r for r in parziale["info"] if "una scadenza" in r[0]][0]
    assert riga[1].startswith("n.d.: chain parziale") and "6.80" not in riga[1]


def test_superficie_parziale_rr_e_ivrv_nd_fuori_dal_massimo():
    s = _score(_vix(15.0, 17.5), _superficie(0.16, 0.13, -0.05, complete=False))
    assert s["max_score"] == 6 and len(s["unscored"]) == 1 and "RR25" in s["unscored"][0]
    assert all("chain parziale" in r[1] for r in s["lines"] if r[2] is None)
    prezzo = [r for r in s["info"] if r[0].startswith("Prezzo della protezione")][0]
    assert prezzo[1].startswith("n.d.: chain parziale") and s["metrics"]["iv_rv_30d_pts"] is None


# ------------------------------------------------ seconda versione (riserve review 10/10)

def test_iv_meno_rv_e_il_prezzo_della_protezione_non_sposta_il_regime():
    # riserva 5: IV-RV e' un sotto-verdetto informativo, mai punti di stress
    base = _score(_vix(18.0, 20.0), _superficie(0.18, 0.18, -0.05))
    for iv in (0.12, 0.15, 0.21, 0.25, 0.30):
        s = _score(_vix(18.0, 20.0), _superficie(iv, 0.18, -0.05))
        assert s["score"] == base["score"] and s["max_score"] == 9, (iv, s)
    cara = _score(_vix(18.0, 20.0), _superficie(0.26, 0.18, -0.05))
    assert cara["metrics"]["protection_price"] == "EXPENSIVE"
    assert any(r[0].startswith("Prezzo della protezione") and r[1].endswith(": cara") for r in cara["info"])


def test_crash_con_iv_sotto_la_realizzata_e_stress():
    # nel picco la realizzata corre piu' dell'implicita: IV 42 < RV 55 usciva «VOL TESA»
    s = _score(_vix(40.0, 33.0), _superficie(0.42, 0.55, -0.08))
    assert s["verdict"] == "VOL IN STRESS" and _idx(s) >= 72, s
    assert s["metrics"]["protection_price"] == "DISCOUNT"


def test_pavimento_stress_con_backwardation_profonda_anche_a_skew_piatto():
    s = _score(_vix(21.4, 20.0), _superficie(0.20, 0.15, -0.03))
    assert s["verdict"] == "VOL IN STRESS" and "TS_BACKWARDATION" in s["metrics"]["floors"], s
    assert any("Pavimento" in r[0] for r in s["info"])
    sotto = _score(_vix(21.0, 20.0), _superficie(0.20, 0.15, -0.03))      # 1,05 < 1,06
    assert sotto["metrics"]["floors"] == [] and sotto["verdict"] != "VOL IN STRESS"


@pytest.mark.parametrize("giorni,stantio", [(3, False), (10, True)])
def test_vix_oltre_cinque_giorni_e_stale(giorni, stantio):
    # banco: M4 (soglia 50 giorni) sopravviveva: nessun test passava una data vecchia
    asof = _d(-giorni)
    v = {"vix_30d": 15.0, "vix_3m": 17.5, "vix_asof": {"vix_30d": asof, "vix_3m": asof}}
    s = _score(v, _superficie(0.16, 0.13, -0.05))
    riga = [r for r in s["lines"] if "VIX/VIX3M" in r[0]][0]
    assert (riga[2] is None and "STALE" in riga[1]) if stantio else (riga[2] is not None), riga


def test_skew_call_positivo_non_e_domanda_di_copertura():
    # banco: M12 (abs del RR) sopravviveva. RR25 +5 pt (call sopra put) = 0 punti
    s = _score(_vix(15.0, 17.5), _superficie(0.16, 0.13, 0.05))
    riga = [r for r in s["lines"] if "RR25" in r[0]][0]
    assert riga[2] == 0.0, riga


def test_skew_normalizzato_solo_informativo():
    # riserva 11: RR25/ATM e' piu' ripido nel placido che nel crash: si mostra, non punteggia
    placido = _score(_vix(15.0, 17.5), _superficie(0.14, 0.11, -0.045))
    crash = _score(_vix(40.0, 33.0), _superficie(0.42, 0.55, -0.08))
    assert abs(placido["metrics"]["rr25_atm_ratio"]) > abs(crash["metrics"]["rr25_atm_ratio"])
    assert crash["score"] > placido["score"]


def test_fascia_della_riga_sul_suo_massimo():
    # riserva 2: struttura VIX 2,98 su 6 era stampata «critico» a meta' scala
    s = _score(_vix(19.0, 19.6), _superficie(0.16, 0.13, -0.05))
    with language_context("it"):
        blocco = S.format_score_block(s)
    riga = [l for l in blocco.splitlines() if "VIX/VIX3M" in l][0]
    assert "(attenzione)" in riga and "critico" not in riga, riga


def test_etichette_del_verdetto_corte_per_la_cella_del_pdf():
    # riserva 1: la cella Verdetto e' 7,4 cm meno 12 pt di margini; Helvetica-Bold 8,5
    from reportlab.lib.units import cm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    spazio = 7.4 * cm - 12
    casi = [_score(_vix(12.0, 14.1), _superficie(0.12, 0.095, -0.035)), _score(_vix(18.0, 20.0), _superficie(0.18, 0.15, -0.05)),
            _score(_vix(19.5, 19.6), _superficie(0.18, 0.15, -0.07)), _score(_vix(40.0, 33.0), _superficie(0.42, 0.55, -0.08)),
            S.options_score("SPY", vix_ts={"error": "x"}, vol_surface={"error": "y"})]
    for lingua in ("it", "en"):
        with language_context(lingua):
            casi.append(S.options_score("SPY", vix_ts={"error": "x"}, vol_surface={"error": "y"}))
            casi.append(S.options_score("SPY", vix_ts=_vix(40.0, 33.0), vol_surface=_superficie(0.42, 0.55, -0.08)))
    for s in casi:
        assert stringWidth(s["verdict"], "Helvetica-Bold", 8.5) <= spazio, s["verdict"]
    assert {casi[0]["verdict"], casi[3]["verdict"]} == {"VOL CALMA", "VOL IN STRESS"}


def test_etichette_inglesi_della_volatilita():
    od = {"expiry_used": _d(9), "put_call_oi_ratio": 1.4, "coverage": {"status": "COMPLETE"},
          "atm_status": "QUALIFIED", "atm_iv_put_pct": 17.0}
    with language_context("en"):
        s = S.options_score("SPY", vix_ts=_vix(15.0, 17.5), vol_surface=_superficie(0.16, 0.13, -0.05), options_data=od)
    testo = repr(s["lines"]) + repr(s["info"])
    assert "37d" in testo and "9d)" in testo and "37g" not in testo and "9g)" not in testo


def test_solo_options_data_niente_regime_dichiarato():
    s = S.options_score("SPY", options_data={"expiry_used": _d(9), "put_call_oi_ratio": 1.5})
    assert s["score"] is None and s["unavailable_reason"] == "no_regime_metric"
    assert "None/None" not in S.format_scoreboard({"options": s})


# ------------------------------------------------------------------ GEX

def _bs_gamma(S_, K, sig, T):
    v = sig * math.sqrt(T)
    d1 = (math.log(S_ / K) + 0.5 * v * v) / v
    return math.exp(-0.5 * d1 * d1) / (math.sqrt(2 * math.pi) * S_ * v)


def _chain(expiry, contratti, spot=100.0):
    T = max((date.fromisoformat(expiry) - date.today()).days, 1) / 365.0
    rows = []
    for tipo, K, oi in contratti:
        rows.append({"type": tipo, "strike": K, "oi": oi, "iv": 0.2, "gamma": _bs_gamma(spot, K, 0.2, T),
                     "delta": 0.5 if (tipo == "call" and K == spot) else 0.3, "expiry": expiry,
                     "underlying_asset": None})
    return {"chain": rows, "coverage": {"status": "COMPLETE"}}


def _gex(monkeypatch, catene, scadenze):
    from bellomberg.market_data import polygon_data as p
    from bellomberg.portfolio import positioning_tools as pt
    monkeypatch.setattr(p, "polygon_available", lambda: True)
    monkeypatch.setattr(p, "get_option_expirations", lambda t: {"expirations": scadenze})
    monkeypatch.setattr(p, "get_options_chain", lambda t, e, **k: catene[e])
    # integrazione 10/10: lo spot del GEX segue spot_alignment.resolve_spot, il cui passo 3 e'
    # yfinance lastPrice (NON qualificato). Nei test qui lastPrice non c'e': si prova il resto
    from bellomberg.market_data import spot_alignment as sa
    monkeypatch.setattr(sa, "_last_price_from_yfinance", lambda t: None)
    with language_context("it"):
        return pt.compute_gex("ZZSYN")


def test_gex_positivo_regime_positivo_e_flip_vero(monkeypatch):
    # scenario dell'audit (spot 100: put 95, call 100, call 105): GEX netto > 0 ma il vecchio
    # «flip» cumulato a 102,5 diceva NEGATIVO
    e = _d(10)
    out = _gex(monkeypatch, {e: _chain(e, [("put", 95.0, 1000), ("call", 100.0, 1000), ("call", 105.0, 1000)])}, [e])
    assert out["net_gex_usd_per_1pct"] > 0 and out["regime"].startswith("POSITIVO"), out
    flip = out["gamma_flip_strike"]
    if flip is not None:
        assert flip < out["spot_est"]          # sotto lo zero-gamma il segno cambia: spot sopra


def test_regime_segue_il_gex_netto_anche_con_lo_zero_gamma_sopra_lo_spot(monkeypatch):
    # call ATM pesanti, put ITM a 106: GEX allo spot > 0 ma il profilo cambia segno SOPRA lo
    # spot. Il regime e' il segno del GEX netto, non «spot sotto il flip» (banco: mutante
    # che decide dal confronto spot/flip sopravviveva)
    e = _d(10)
    out = _gex(monkeypatch, {e: _chain(e, [("call", 100.0, 1000), ("put", 106.0, 3000)])}, [e])
    assert out["net_gex_usd_per_1pct"] > 0 and out["gamma_flip_strike"] is not None
    assert out["gamma_flip_strike"] > out["spot_est"]
    assert out["regime"].startswith("POSITIVO"), out["regime"]


def test_gex_negativo_regime_negativo(monkeypatch):
    e = _d(10)
    out = _gex(monkeypatch, {e: _chain(e, [("put", 100.0, 5000), ("call", 100.0, 1000)])}, [e])
    assert out["net_gex_usd_per_1pct"] < 0 and out["regime"].startswith("NEGATIVO")


def test_zero_gamma_e_la_radice_del_profilo(monkeypatch):
    # put pesanti sotto (90), call pesanti sopra (110): il profilo passa per zero fra i due
    e = _d(20)
    out = _gex(monkeypatch, {e: _chain(e, [("put", 90.0, 1000), ("call", 110.0, 1000), ("call", 100.0, 1)])}, [e])
    flip = out["gamma_flip_strike"]
    assert flip is not None and 90.0 < flip < 110.0
    T = 20 / 365.0
    g = lambda S_: (_bs_gamma(S_, 110, .2, T) * 1000 - _bs_gamma(S_, 90, .2, T) * 1000 + _bs_gamma(S_, 100, .2, T))
    assert abs(g(flip)) < 0.02 * max(abs(g(95.0)), abs(g(105.0)))


def test_zero_gamma_con_due_radici_le_dichiara_e_sceglie_la_piu_vicina(monkeypatch):
    # riserva 8: call 86, put 99, call 106 -> il profilo passa per zero due volte. Oracolo
    # INDIPENDENTE (bisezione fine qui nel test): lo zero-gamma e' la radice piu' vicina allo
    # spot, non la prima della griglia (banco: M5 sopravviveva)
    e = _d(20)
    gambe = [("call", 86.0, 2000), ("put", 99.0, 6000), ("call", 106.0, 6000)]
    out = _gex(monkeypatch, {e: _chain(e, gambe)}, [e])
    spot, T = out["spot_est"], 20 / 365.0
    g = lambda S_: sum((1 if t == "call" else -1) * _bs_gamma(S_, K, .2, T) * oi for t, K, oi in gambe)
    xs = [spot * (0.85 + 0.30 * i / 6000) for i in range(6001)]
    radici = [(a + b) / 2 for a, b in zip(xs, xs[1:]) if g(a) * g(b) < 0]
    assert len(radici) == 2 and len(out["gamma_flip_roots"]) == 2, (radici, out["gamma_flip_roots"])
    vicina = min(radici, key=lambda r: abs(r - spot))
    assert vicina != min(radici)                       # la prima radice NON e' la piu' vicina
    assert out["gamma_flip_strike"] == pytest.approx(vicina, abs=0.2)
    assert out["gamma_flip_roots"] == sorted(out["gamma_flip_roots"])
    assert "2 radici" in out["gamma_flip_choice"]


def test_scadenza_in_errore_non_e_usata_e_il_gex_e_parziale(monkeypatch):
    e1, e2 = _d(5), _d(12)
    catene = {e1: _chain(e1, [("call", 100.0, 1000), ("put", 95.0, 500)]), e2: {"error": "HTTP 429"}}
    out = _gex(monkeypatch, catene, [e1, e2])
    assert out["expiries_used"] == [e1]
    assert out["expiries_failed"] == [{"expiry": e2, "error": "HTTP 429"}]
    assert out["partial"] is True and out["coverage"]["status"] == "PARTIAL" and "PARZIALE" in out["partial_note"]


def test_niente_0dte_e_mensile_inclusa(monkeypatch):
    oggi = date.today()
    giornaliere = [str(oggi + timedelta(days=k)) for k in range(0, 8)]
    # prossimo terzo venerdi' entro 45 giorni
    d = oggi + timedelta(days=9)
    while not (d.weekday() == 4 and 15 <= d.day <= 21):
        d += timedelta(days=1)
    mensile = str(d)
    scad = sorted(set(giornaliere + [mensile]))
    catene = {e: _chain(e, [("call", 100.0, 100), ("put", 95.0, 100)]) for e in scad}
    out = _gex(monkeypatch, catene, scad)
    assert all((date.fromisoformat(e) - oggi).days >= 2 for e in out["expiries_used"])
    assert mensile in out["expiries_used"]


# ------------------------------------------------------------- signal_engine

@pytest.mark.parametrize("spread", [0.15, -0.15])
def test_opzioni_care_o_a_sconto_non_spingono_trim_hedge(monkeypatch, spread):
    import bellomberg.portfolio.vol_surface as vs
    import bellomberg.portfolio.signal_engine as se
    monkeypatch.setattr(vs, "build_vol_surface", lambda t, max_expiries=6: {
        "iv_rv_spread_front": spread, "rv_percentile_1y": 50, "expected_move_pct": 5, "expected_move_days": 30})
    with language_context("it"):
        sigs = se.sig_vol_risk_premium("ZZSYN")
        monkeypatch.setattr(se, "scan_ticker", lambda t, **k: sigs)
        d = se.position_doctor("ZZSYN")
    assert sigs and all(x["direction"] == "neutral" for x in sigs), sigs
    assert d["net_score"] == 0 and "TRIM/HEDGE" not in str(d["verdict"])
    if spread > 0:   # testo e azione coerenti: care = niente protezione comprata qui
        assert "non da acquisto di protezione" in str(sigs[0]["reading"])
        assert "non una direzione sul titolo" in str(sigs[0]["reading"])


def test_api_f12_inoltra_spot_proxy_e_copertura(monkeypatch):
    # residuo R02: la UI riceveva regime e spot_est senza sapere che lo spot era uno strike
    import bellomberg.api.bellomberg_api as api
    from bellomberg.portfolio import vol_surface, positioning_tools
    from bellomberg.market_data import iv_history
    monkeypatch.setattr(vol_surface, "build_vol_surface", lambda t, **kw: {"ticker": t.upper(), "slices": []})
    monkeypatch.setattr(iv_history, "get_iv_context", lambda t, **kw: {"error": "stub"})
    monkeypatch.setattr(positioning_tools, "compute_gex", lambda t, **kw: {
        "spot_est": 100.0, "spot_source": "proxy_strike_delta50", "spot_qualified": False,
        "spot_note": "Spot proxy: ...", "spot_vs_flip": "non valutato: spot proxy", "partial": True,
        "expiries_failed": [{"expiry": "2099-01-16", "error": "HTTP 429"}], "expiries_used": ["2099-01-09"],
        "net_gex_usd_per_1pct": 1.0, "gamma_flip_strike": 95.0, "top_strikes": [], "regime": "POSITIVO"})
    g = api.get_vol_surface("ZZSYN")["gex"]
    assert g["spot_qualified"] is False and g["spot_source"] == "proxy_strike_delta50"
    assert g["partial"] is True and g["expiries_failed"][0]["error"] == "HTTP 429"
    assert "1 in errore" in g["basis"]


def test_spot_proxy_regime_dal_gex_netto_e_flip_non_valutato(monkeypatch):
    e = _d(20)
    out = _gex(monkeypatch, {e: _chain(e, [("put", 90.0, 1000), ("call", 110.0, 1000), ("call", 100.0, 1)])}, [e])
    assert out["spot_qualified"] is False and out["spot_source"].startswith("proxy_")
    assert out["spot_vs_flip"].startswith("non valutato")
    assert out["regime"].startswith("POSITIVO" if out["net_gex_usd_per_1pct"] > 0 else "NEGATIVO")


@pytest.mark.parametrize("ritardo_s,qualificato", [(30, True), (600, False)])
def test_spot_da_barra_1m_allineata_alle_quote(monkeypatch, ritardo_s, qualificato):
    # underlying_asset senza prezzo (piano del PM): barra yfinance 1m all'ora delle quote;
    # oltre la tolleranza dichiarata (120 s) la barra non vale e lo spot torna proxy
    import sys
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import pandas as pd
    e = _d(10)
    # ora FISSA a meta' seduta (12:00 ET): con l'ora corrente il caso 600 s cadeva, fra le
    # 16:00 e le 16:15 ET, nella tolleranza di chiusura della seduta (test instabile)
    ora_quote = datetime(2026, 10, 9, 16, 0, 0, tzinfo=timezone.utc)
    ch = _chain(e, [("call", 100.0, 1000), ("put", 95.0, 500)])
    for r in ch["chain"]:
        r["quote_timestamp_ns"] = int(ora_quote.timestamp() * 1e9)
        r["underlying_asset"] = {"ticker": "ZZSYN"}
    barra = pd.DataFrame({"Close": [102.83]}, index=pd.DatetimeIndex(
        [pd.Timestamp(ora_quote.timestamp() - ritardo_s, unit="s", tz="UTC")]))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda t: SimpleNamespace(history=lambda **kw: barra)))
    out = _gex(monkeypatch, {e: ch}, [e])
    assert out["spot_qualified"] is qualificato
    if qualificato:
        assert out["spot_est"] == 102.83 and out["spot_source"] == "yfinance_1m_bar_aligned"
        assert out["spot_quote_time"] == ora_quote.isoformat()
    else:
        assert out["spot_source"].startswith("proxy_")


def _gex_con_barra(monkeypatch, ora_quote, indice_barre, prezzi):
    import sys
    from types import SimpleNamespace
    import pandas as pd
    e = _d(10)
    ch = _chain(e, [("call", 100.0, 1000), ("put", 95.0, 500)])
    for r in ch["chain"]:
        if ora_quote is not None:
            r["quote_timestamp_ns"] = int(ora_quote.timestamp() * 1e9)
        r["underlying_asset"] = {"ticker": "ZZSYN"}
    barre = pd.DataFrame({"Close": prezzi}, index=pd.DatetimeIndex(indice_barre))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda t: SimpleNamespace(history=lambda **kw: barre)))
    return _gex(monkeypatch, {e: ch}, [e])


@pytest.mark.parametrize("hhmmss,qualificato", [("16:10:00", True), ("16:15:00", True), ("16:20:00", False)])
def test_quote_dopo_le_16_et_usano_la_chiusura_della_seduta_fino_alle_16_15(monkeypatch, hhmmss, qualificato):
    # riserva 9: le opzioni su SPY trattano fino alle 16:15 ET, l'azionario chiude alle 16:00:
    # una quota delle 16:10 non ha barra entro 120 s. Vale l'ultima barra della seduta, dichiarata
    import pandas as pd
    giorno = "2026-10-09"
    barre = pd.date_range(giorno + " 15:50", giorno + " 15:59", freq="1min", tz="America/New_York")
    prezzi = [500.0 + i for i in range(len(barre))]
    ora = pd.Timestamp(giorno + " " + hhmmss, tz="America/New_York").tz_convert("UTC").to_pydatetime()
    out = _gex_con_barra(monkeypatch, ora, barre, prezzi)
    assert out["spot_qualified"] is qualificato, out["spot_source"]
    if qualificato:
        assert out["spot_est"] == prezzi[-1] and out["spot_source"] == "yfinance_1m_bar_session_close"
        assert "16:15" in out["spot_note"]


def test_senza_ora_della_quota_la_barra_non_si_attiva_e_lo_dice(monkeypatch):
    # riserva 9: sul piano del PM last_quote.last_updated puo' mancare: niente barra, dichiarato
    from datetime import datetime, timezone
    import pandas as pd
    adesso = datetime.now(timezone.utc)
    out = _gex_con_barra(monkeypatch, None, [pd.Timestamp(adesso.timestamp() - 30, unit="s", tz="UTC")], [101.0])
    assert out["spot_qualified"] is False and out["spot_source"].startswith("proxy_")
    assert "last_quote.last_updated" in out["spot_bar_note"] and "last_quote.last_updated" in out["spot_note"]


# ---- integrazione 10/10 (Opus 5.5): spot del GEX = spot_alignment.resolve_spot ----

def test_gex_senza_barra_usa_lastprice_non_qualificato_e_non_valuta_il_flip(monkeypatch):
    # passo 3 della regola unica: lastPrice c'e' ma e' un istante diverso dalle quote ->
    # spot NON qualificato, posizione rispetto allo zero-gamma «non valutata», dichiarato
    from bellomberg.market_data import spot_alignment as sa
    e = _d(10)
    catene = {e: _chain(e, [("put", 95.0, 1000), ("call", 100.0, 1000), ("call", 105.0, 1000)])}
    from bellomberg.market_data import polygon_data as p
    from bellomberg.portfolio import positioning_tools as pt
    monkeypatch.setattr(p, "polygon_available", lambda: True)
    monkeypatch.setattr(p, "get_option_expirations", lambda t: {"expirations": [e]})
    monkeypatch.setattr(p, "get_options_chain", lambda t, x, **k: catene[x])
    monkeypatch.setattr(sa, "_last_price_from_yfinance", lambda t: 100.4)
    with language_context("it"):
        out = pt.compute_gex("ZZSYN")
    assert out["spot_est"] == 100.4 and out["spot_qualified"] is False
    assert out["spot_source"] == "yfinance_last_price_unqualified" and out["spot_method"] == "yfinance_last_price"
    assert "NON qualificato" in out["spot_note"] and "last_quote.last_updated" in out["spot_note"]
    if out["gamma_flip_strike"] is not None:
        assert out["spot_vs_flip"].startswith("non valutato")


def test_gex_e_superficie_scelgono_lo_stesso_spot_dalla_stessa_barra(monkeypatch):
    # stessa ancora, stesse barre: GEX e resolve_spot (usato dalla superficie) danno lo stesso
    # prezzo e lo stesso inizio barra — una regola sola, non due
    import sys
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import pandas as pd
    from bellomberg.market_data import spot_alignment as sa
    ora_quote = datetime(2026, 10, 9, 16, 0, 40, tzinfo=timezone.utc)
    # una sola barra, quella SUCCESSIVA (16:01, a 60 s dal minuto dell'ancora): la regola di
    # spot_alignment la accetta (scarto simmetrico entro 120 s), la vecchia regola locale del
    # GEX (solo barre precedenti) cadeva sul proxy — discrimina le due regole
    barre = pd.DataFrame({"Close": [103.0]}, index=pd.DatetimeIndex(
        [pd.Timestamp("2026-10-09 16:01", tz="UTC")]))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda t: SimpleNamespace(history=lambda **kw: barre)))
    e = _d(10)
    ch = _chain(e, [("call", 100.0, 1000), ("put", 95.0, 500)])
    for r in ch["chain"]:
        r["quote_timestamp"] = ora_quote.isoformat()
    out = _gex(monkeypatch, {e: ch}, [e])
    ref = sa.resolve_spot("ZZSYN", anchor=ora_quote, last_price_loader=lambda t: None)
    assert out["spot_est"] == ref["spot"] == 103.0
    assert out["spot_asof"] == ref["spot_timestamp"] and out["spot_qualified"] is ref["spot_qualified"] is True
    assert out["spot_alignment"]["offset_seconds"] == 60


def test_resolve_spot_finestra_16_00_16_15_et_anche_per_la_superficie():
    # la finestra di chiusura della v2 vol/quant vive ora in spot_alignment: vale per tutti
    import pandas as pd
    from bellomberg.market_data import spot_alignment as sa
    giorno = "2026-10-09"
    idx = pd.date_range(giorno + " 15:50", giorno + " 15:59", freq="1min", tz="America/New_York")
    barre = pd.DataFrame({"Close": [500.0 + i for i in range(len(idx))]}, index=idx)
    finestre = []
    def loader(t, a, b):
        finestre.append((a, b))
        return barre
    ora = pd.Timestamp(giorno + " 16:15:00", tz="America/New_York").tz_convert("UTC").to_pydatetime()
    r = sa.resolve_spot("X", anchor=ora, bars_loader=loader, last_price_loader=lambda t: None)
    assert r["spot"] == 509.0 and r["spot_qualified"] is True and r["spot_alignment"]["session_close"] is True
    # la finestra di lettura comprende la barra delle 15:59 ET anche con l'ancora alle 16:15
    assert finestre[0][0] <= idx[-1].tz_convert("UTC").to_pydatetime()
    ora = pd.Timestamp(giorno + " 16:20:00", tz="America/New_York").tz_convert("UTC").to_pydatetime()
    r = sa.resolve_spot("X", anchor=ora, bars_loader=loader, last_price_loader=lambda t: None)
    assert r["spot"] is None and r["spot_alignment"]["session_close"] is False
