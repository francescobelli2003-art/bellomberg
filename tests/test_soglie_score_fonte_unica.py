"""Soglie degli score: UNA sola fonte (core/soglie_score), punti continui, fascia al limite del
Position Doctor, pavimenti dal budget di stress (10/10/2026, Opus 5.5).

I test di «fonte unica» MUTANO la costante condivisa (monkeypatch sul modulo core) e
pretendono che cambino TUTTI i consumatori: se un modulo torna a una copia locale del numero,
il test cade. Numeri e ticker INVENTATI (ZZ*), zero rete.
"""
import sys
import types
from datetime import date, timedelta

import pandas as pd
import pytest

from bellomberg.agents import specialist_scores as S
from bellomberg.core import soglie_score as soglie
from bellomberg.core.language import language_context


# ------------------------------------------------------------------ helper

def _d(days):
    return str(date.today() + timedelta(days=days))


def _vix(v30, v3m, eta=0):
    asof = _d(-eta)
    return {"vix_30d": v30, "vix_3m": v3m, "vix_vix3m_ratio": round(v30 / v3m, 3),
            "vix_asof": {"vix_30d": asof, "vix_3m": asof}}


def _superficie(iv30, rv, rr37):
    rows = [{"expiry": _d(d), "days": d, "atm_iv": iv30, "rr25": rr37, "call_oi": 1000, "put_oi": 1500}
            for d in (9, 30, 37)]
    cov = [{"expiry": r["expiry"], "status": "loaded", "chain_complete": True} for r in rows]
    return {"term_structure": rows, "realized_vol_21d": rv, "coverage": {"rows": cov}}


def _opt(vix, surf):
    with language_context("it"):
        return S.options_score("SPY", vix_ts=vix, vol_surface=surf)


def _riga(s, pezzo, dove="lines"):
    return [r for r in s[dove] if pezzo in r[0]][0]


def _yf_finto(valori):
    """Modulo yfinance finto: Ticker(sym).history() con una sola chiusura di oggi."""
    class _T:
        def __init__(self, sym):
            self.sym = sym

        def history(self, period="5d"):
            if self.sym not in valori:
                return pd.DataFrame({"Close": []})
            return pd.DataFrame({"Close": [valori[self.sym]]}, index=pd.to_datetime([date.today()]))
    return types.SimpleNamespace(Ticker=_T)


def _strumento_vix(monkeypatch, v30, v3m):
    from bellomberg.portfolio import positioning_tools as pt
    monkeypatch.setitem(sys.modules, "yfinance", _yf_finto({"^VIX": v30, "^VIX3M": v3m}))
    with language_context("it"):
        return pt.get_vix_term_structure()


# ------------------------------------------------------------------ costanti congelate

def test_valori_congelati_e_vincoli_economici():
    assert soglie.BANDE_INDICE == (0.25, 0.50, 0.75)
    a = soglie.VIX3M_ANCORE
    assert list(a) == sorted(a) and a[2] <= soglie.VIX3M_INVERSIONE < a[3]   # ancora del 4 sotto 1,00
    assert soglie.VIX3M_PAVIMENTO_STRESS == a[-1]
    assert soglie.VRP_SCONTO < soglie.VRP_CARA
    assert soglie.SKEW_NORM_MARCATO == soglie.SKEW_NORM_ANCORE[2]
    assert soglie.ETA_MAX_GIORNALIERA_GG == 6 and S._MACRO_ETA_GIORNALIERA == 6
    assert (soglie.RV_PCT_BASSO, soglie.RV_PCT_ALTO) == (20, 80)


@pytest.mark.parametrize("frac,banda", [(0.0, 0), (0.2499, 0), (0.25, 1), (0.4999, 1), (0.5, 2),
                                        (0.7499, 2), (0.75, 3), (1.0, 3)])
def test_bande_25_50_75(frac, banda):
    assert soglie.banda(frac) == banda == S._banda(frac)


def test_legende_delle_bande_dicono_le_costanti():
    from bellomberg.reporting.i18n import LABELS
    # il testo e' una chiave i18n letterale: si verifica che dica le costanti condivise
    a, b, c = (int(round(x * 100)) for x in soglie.BANDE_INDICE)
    attesi = ["<%d" % a, "%d-%d" % (a, b), "%d-%d" % (b, c), "=%d" % c]
    legende = [k for k in LABELS if "Bande:" in k]
    assert len(legende) >= 2
    for k in legende:
        for testo in (k,) + tuple(LABELS[k]):
            norm = testo.replace("&lt;", "<").replace("&ge;", ">=")
            assert all(x in norm for x in attesi), testo


def test_bande_mutate_cambiano_verdetti_e_pavimento_vol(monkeypatch):
    lab = ["A", "B", "C", "D"]
    assert S._verdict_bands(2.1, 3, lab) == "C"           # 70: elevato con 25/50/75
    monkeypatch.setattr(soglie, "BANDE_INDICE", (0.25, 0.50, 0.70))
    assert S._verdict_bands(2.1, 3, lab) == "D"
    # pavimento IN STRESS = bordo della banda critica: col bordo a 0,70 lo score alzato e' 70%
    s = _opt(_vix(21.4, 20.0), _superficie(0.20, 0.15, -0.20 * 0.15))
    assert "TS_BACKWARDATION" in s["metrics"]["floors"]
    assert 0.70 <= s["score"] / s["max_score"] < 0.75


# ------------------------------------------------------------------ VIX/VIX3M: una taratura

@pytest.mark.parametrize("v30,v3m,codice", [(17.0, 20.0, "CONTANGO"), (19.0, 20.0, "FLAT"),
                                            (20.0, 20.0, "BACKWARDATION"), (22.0, 20.0, "BACKWARDATION")])
def test_strumento_e_score_classificano_uguale(monkeypatch, v30, v3m, codice):
    out = _strumento_vix(monkeypatch, v30, v3m)
    s = _opt(_vix(v30, v3m), _superficie(0.18, 0.15, -0.045))
    assert out["term_structure_code"] == codice == s["metrics"]["vix_term_structure"]
    assert out["term_structure"].startswith(codice)


def test_mutare_le_ancore_cambia_strumento_e_score_insieme(monkeypatch):
    # rapporto 0,95: FLAT e ~2,6 punti con le ancore calibrate
    out = _strumento_vix(monkeypatch, 19.0, 20.0)
    s = _opt(_vix(19.0, 20.0), _superficie(0.18, 0.15, -0.045))
    assert out["term_structure_code"] == "FLAT" and _riga(s, "VIX/VIX3M")[2] > 2
    monkeypatch.setattr(soglie, "VIX3M_ANCORE", (0.96, 0.98, 0.99, 1.10))
    out2 = _strumento_vix(monkeypatch, 19.0, 20.0)
    s2 = _opt(_vix(19.0, 20.0), _superficie(0.18, 0.15, -0.045))
    assert out2["term_structure_code"] == "CONTANGO" and out2["term_structure_thresholds"]["contango_below"] == 0.98
    assert _riga(s2, "VIX/VIX3M")[2] == 0.0
    assert "contango" in _riga(s2, "VIX/VIX3M")[1] and "sotto la norma" not in _riga(s2, "VIX/VIX3M")[1]


def test_vix_interpolato_alle_ancore_calibrate():
    for ratio, punti in zip(soglie.VIX3M_ANCORE, soglie.VIX3M_PUNTI):
        s = _opt(_vix(20.0 * ratio, 20.0), _superficie(0.18, 0.15, -0.045))
        assert _riga(s, "VIX/VIX3M")[2] == pytest.approx(punti, abs=0.05), ratio


@pytest.mark.parametrize("eta,stale", [(6, False), (7, True)])
def test_freschezza_vix_sei_giorni_come_la_macro(eta, stale):
    s = _opt(_vix(15.0, 17.5, eta=eta), _superficie(0.16, 0.13, -0.04))
    riga = _riga(s, "VIX/VIX3M")
    assert (riga[2] is None and "STALE" in riga[1]) if stale else riga[2] is not None, riga


def test_freschezza_vix_legge_la_costante(monkeypatch):
    monkeypatch.setattr(soglie, "ETA_MAX_GIORNALIERA_GG", 3)
    s = _opt(_vix(15.0, 17.5, eta=4), _superficie(0.16, 0.13, -0.04))
    assert "STALE" in _riga(s, "VIX/VIX3M")[1]


# ------------------------------------------------------------------ prezzo della protezione

def _interp_vs(iv30, rv, rr=-0.04, atm=0.18, rv_pct=None):
    from bellomberg.portfolio import vol_surface as vs
    front = {"expiry": _d(30), "days": 30, "atm_iv": atm, "rr25": rr, "bf25": 0.0, "pc_oi_ratio": None}
    _, ratio = soglie.prezzo_protezione(iv30, rv)
    with language_context("it"):
        return str(vs._interpret("ZZSYN", [front], None, rv, ratio, rv_pct_1y=rv_pct, iv30=iv30))


def _segnale(monkeypatch, ratio, spread):
    import bellomberg.portfolio.vol_surface as vs
    import bellomberg.portfolio.signal_engine as se
    monkeypatch.setattr(vs, "build_vol_surface", lambda t, max_expiries=6: {
        "iv_rv_spread_30d": spread, "iv_rv_ratio_30d": ratio, "rv_percentile_1y": 50})
    with language_context("it"):
        return se.sig_vol_risk_premium("ZZSYN")


def test_prezzo_protezione_stessa_soglia_in_score_superficie_e_segnale(monkeypatch):
    iv, rv = 0.27, 0.18                                   # 1,50x: nella norma con le soglie calibrate
    assert _opt(_vix(15.0, 17.5), _superficie(iv, rv, -0.05))["metrics"]["protection_price"] == "NORMAL"
    assert "vol risk premium nella norma" in _interp_vs(iv, rv)
    assert _segnale(monkeypatch, iv / rv, iv - rv) == []
    monkeypatch.setattr(soglie, "VRP_CARA", 1.40)        # mutare la costante sposta TUTTI e tre
    assert _opt(_vix(15.0, 17.5), _superficie(iv, rv, -0.05))["metrics"]["protection_price"] == "EXPENSIVE"
    assert "CARE" in _interp_vs(iv, rv)
    sig = _segnale(monkeypatch, iv / rv, iv - rv)
    assert sig and "CARE" in str(sig[0]["reading"])


def test_prezzo_protezione_invariante_di_scala():
    # stesso rapporto 1,9x con IV 12 e con IV 57: stessa lettura (prima +6 punti decidevano)
    for iv, rv in ((0.12, 0.12 / 1.9), (0.57, 0.30)):
        assert soglie.prezzo_protezione(iv, rv)[0] == "EXPENSIVE"
    assert soglie.prezzo_protezione(0.19, 0.18)[0] == "DISCOUNT"      # 1,06x
    assert soglie.prezzo_protezione(0.0, 0.18) == (None, None)


def test_forza_del_segnale_vrp_parte_dal_filtro_edge_scan(monkeypatch):
    sig = _segnale(monkeypatch, soglie.VRP_CARA, 0.10)
    assert sig and sig[0]["strength"] == 45                # sulla soglia: visibile nell'Edge Scan
    sig = _segnale(monkeypatch, soglie.VRP_CARA + 0.2, 0.10)
    assert sig[0]["strength"] == 65
    sig = _segnale(monkeypatch, soglie.VRP_SCONTO - 0.3, -0.02)
    assert sig[0]["strength"] == 75 and "A SCONTO" in str(sig[0]["reading"])


# ------------------------------------------------------------------ skew normalizzato

def test_marcato_della_superficie_e_l_ancora_dello_score(monkeypatch):
    # -0,066/0,18 = 0,367 >= 0,35: MARCATO; lo score lo vede sopra l'ancora del 2
    assert "MARCATO" in _interp_vs(0.20, 0.15, rr=-0.066, atm=0.18)
    assert "Skew put nella norma" in _interp_vs(0.20, 0.15, rr=-0.054, atm=0.18)   # 0,30
    monkeypatch.setattr(soglie, "SKEW_NORM_MARCATO", 0.28)
    assert "MARCATO" in _interp_vs(0.20, 0.15, rr=-0.054, atm=0.18)


def test_skew_dello_score_legge_le_ancore(monkeypatch):
    # v3: punteggio in punti vol assoluti (SKEW_RR25_ANCORE_PT); lo skew normalizzato non conta
    s = _opt(_vix(15.0, 17.5), _superficie(0.20, 0.16, -0.05))
    assert _riga(s, "RR25")[2] == pytest.approx(1.0)
    monkeypatch.setattr(soglie, "SKEW_NORM_ANCORE", (0.05, 0.10, 0.15, 0.25))
    assert _riga(_opt(_vix(15.0, 17.5), _superficie(0.20, 0.16, -0.05)), "RR25")[2] == pytest.approx(1.0)
    monkeypatch.setattr(soglie, "SKEW_RR25_ANCORE_PT", (1.0, 2.0, 3.0, 5.0))
    assert _riga(_opt(_vix(15.0, 17.5), _superficie(0.20, 0.16, -0.05)), "RR25")[2] == pytest.approx(3.0)


# ------------------------------------------------------------------ percentile della realizzata

def test_percentile_realizzata_20_80_anche_nella_superficie():
    assert "nella media" in _interp_vs(0.20, 0.16, rv_pct=22)        # prima COMPRESSO (<= 25)
    assert "COMPRESSO" in _interp_vs(0.20, 0.16, rv_pct=20)
    assert "nella media" in _interp_vs(0.20, 0.16, rv_pct=78)        # prima ELEVATO (>= 75)
    assert "ELEVATO" in _interp_vs(0.20, 0.16, rv_pct=80)


# ------------------------------------------------------------------ macro, crypto, valutazione

def _macro_vix(vix):
    oggi = date(2031, 3, 17)
    d = oggi.isoformat()
    ind = {"vix_close": {"value": vix, "date": d}, "high_yield_spread": {"value": 3.0, "date": d},
           "ig_credit_spread": {"value": 1.0, "date": d}}
    s = S.macro_score({"indicators": ind}, oggi=oggi)
    return _riga(s, "VIX")[2]


def test_macro_vix_legge_le_soglie_calibrate(monkeypatch):
    assert [_macro_vix(v) for v in (20.4, 20.5, 25.0, 34.1)] == [0, 1, 2, 3]
    monkeypatch.setattr(soglie, "MACRO_VIX_SOGLIE", (10.0, 11.0, 12.0))
    assert _macro_vix(20.4) == 3


def test_crypto_legge_le_ancore_calibrate(monkeypatch):
    from datetime import datetime, timezone
    adesso = datetime(2031, 3, 17, 12, tzinfo=timezone.utc)

    def p():
        f = 10.95 + 11.7
        intel = {"fetched_at_utc": adesso.isoformat(), "top_10_perps_by_oi": [
            {"asset": "BTC", "funding_annualized_pct": f, "premium_basis_pts": 0, "oi_usd_m": 1000},
            {"asset": "ETH", "funding_annualized_pct": f, "premium_basis_pts": 0, "oi_usd_m": 500}]}
        return _riga(S.crypto_score(intel, adesso=adesso), "Funding")[2]
    assert p() == pytest.approx(1.5)                      # p80: confine di SURRISCALDATO
    monkeypatch.setattr(soglie, "CRYPTO_FUNDING_ANCORE", (0.0, 2.0, 4.0, 6.0, 11.7))
    assert p() == pytest.approx(3.0)


def test_crypto_le_etichette_partono_ai_percentili_calibrati():
    # i confini delle bande comuni (0,75 / 1,5 / 2,25 punti su 3) cadono ESATTAMENTE sulle
    # ancore p50/p80/p95: EUFORICO al p95, non al p86 come con «p95 -> 3 punti»
    pts = dict(zip(soglie.CRYPTO_FUNDING_ANCORE, soglie.CRYPTO_FUNDING_PUNTI))
    a = soglie.CRYPTO_FUNDING_ANCORE
    assert [soglie.banda(pts[x] / 3.0) for x in a[1:4]] == [1, 2, 3]
    # _interp arrotonda a 0,01 punti: il confine si legge ~0,1 di scarto prima del p95 (dichiarato)
    assert soglie.banda(S._interp(a[3] - 0.5, a, soglie.CRYPTO_FUNDING_PUNTI) / 3.0) == 2


def test_mos_continuo_e_legato_alle_costanti(monkeypatch):
    # 10/10 (decisione delega PM): confini delle bande comuni a MOS +15 / -15 / -45
    assert [soglie.punti_mos(m) for m in (60, 45, 15, -15, -45, -75, -90)] == [0, 0, 0.75, 1.5, 2.25, 3.0, 3.0]
    assert [soglie.banda(soglie.punti_mos(m) / 3) for m in (15.1, 14.9, -14.9, -15.1, -44.9, -45.1)] == [0, 1, 1, 2, 2, 3]
    assert soglie.punti_mos(None) is None
    monkeypatch.setattr(soglie, "MOS_PIENO", -15.0)
    assert soglie.punti_mos(-15) == 3.0


@pytest.mark.parametrize("v30,codice", [(18.6, "CONTANGO"), (18.7, "FLAT"), (19.9, "FLAT"), (20.0, "BACKWARDATION")])
def test_flat_parte_dall_ancora_del_2(v30, codice):
    # 10/10: CONTANGO sotto 0,934 (p75), non piu' sotto la mediana
    assert soglie.struttura_vix(v30 / 20.0) == codice


# ------------------------------------------------------------------ pavimenti dal budget

@pytest.mark.parametrize("budget,nome,cluster,dal_mandato", [
    (30, 50.0, 60.0, True), (24, 40.0, 48.0, True), (None, 50.0, 60.0, False), (0, 50.0, 60.0, False)])
def test_pavimenti_dal_budget_di_stress(budget, nome, cluster, dal_mandato):
    assert soglie.pavimenti_concentrazione(budget) == (nome, cluster, dal_mandato)


def _settori(primo):
    resto = (100.0 - primo) / 6.0
    return {"econ_axis": {"by_bucket": [{"bucket": "ZZPrimo", "weight_pct": primo}]
                          + [{"bucket": "QQ%d" % i, "weight_pct": resto} for i in range(6)],
                          "coverage_pct": 100.0}}


def _quant(budget, top=None, cluster=20.0):
    pesi = ([("ZZTOP", top)] + [("ZZ%02d" % i, (100.0 - top) / 20) for i in range(20)] if top
            else [("ZZ%02d" % i, 5.0) for i in range(20)])
    mand = {"rischio": {"volatilita_target_pct": 20, **({"stress_gfc_pct": budget} if budget else {})}}
    pf = {"positions": [{"ticker": t, "valore_mercato_eur": v} for t, v in pesi], "cash_disponibile_eur": 0.0}
    rb = {"verdict": "RECONCILED", "beta_per_decisioni": True, "betas": {"portfolio_risk_spy": 1.0}}
    stress = {"stress_scenario": "gfc_2008", "stress_fallback": False,
              "stress_meta": {"window_loss_pct": -3.0, "proxied": {}}}
    with language_context("it"):
        return S.quant_score(pf, {"portfolio": {"vol_ewma_annual_pct": 8.0, "vol_annual_pct": 8.0,
                                                "beta_vs_spy": 0.5}},
                             beta_reconcile=rb, stress_data=stress, mandato=mand,
                             sector_data=_settori(cluster),
                             negozio={"veicoli": {}, "origine": "sintetico"})


def test_il_pavimento_del_nome_segue_il_budget_del_mandato():
    # nome al 45%: sotto il pavimento con budget 30 (50%), sopra con budget 24 (40%)
    assert _quant(30, top=45.0)["metrics"]["concentration_floor"] is None
    s = _quant(24, top=45.0)
    assert s["metrics"]["concentration_floor"] == "SINGLE_NAME" and s["verdict"] == "RISCHIO ELEVATO"
    assert s["metrics"]["floor_thresholds"] == {"single_name_pct": 40.0, "cluster_pct": 48.0,
                                                "from_mandate_stress_budget": True}
    assert any("budget di stress 24%" in str(v) for _, v, _m in s["info"]), s["info"]


def test_il_pavimento_del_cluster_e_60_col_budget_30_e_senza_budget_dichiarato():
    s = _quant(30, cluster=62.0)
    assert s["metrics"]["concentration_floor"] == "CLUSTER"     # prima 70% fisso: niente pavimento
    assert _quant(30, cluster=58.0)["metrics"]["concentration_floor"] is None
    nd = _quant(None, cluster=62.0)
    assert nd["metrics"]["concentration_floor"] == "CLUSTER"
    assert nd["metrics"]["floor_thresholds"]["from_mandate_stress_budget"] is False
    assert any("soglia di default" in str(v) for _, v, _m in nd["info"]), nd["info"]


# ------------------------------------------------------------------ Position Doctor

def _doctor(monkeypatch, forze):
    import bellomberg.portfolio.signal_engine as se
    sigs = [{"direction": d, "strength": f} for d, f in forze]
    monkeypatch.setattr(se, "scan_ticker", lambda t, **k: sigs)
    with language_context("it"):
        return se.position_doctor("ZZSYN")


@pytest.mark.parametrize("forze,inizio", [
    ([("bearish", 45)], "HOLD —"),                        # -0,45: misti
    ([("bearish", 60)], "HOLD (al limite, tendenza TRIM/HEDGE)"),   # z 2,0: prima HOLD
    ([("bearish", 63)], "HOLD (al limite, tendenza TRIM/HEDGE)"),   # z 2,1: prima TRIM/HEDGE
    ([("bearish", 71)], "TRIM/HEDGE"),
    ([("bullish", 55)], "HOLD (al limite, tendenza ADD/HOLD)"),
    ([("bullish", 72)], "ADD/HOLD"),
])
def test_position_doctor_fascia_al_limite(monkeypatch, forze, inizio):
    d = _doctor(monkeypatch, forze)
    assert str(d["verdict"]).startswith(inizio), d["verdict"]


def test_position_doctor_legge_la_costante(monkeypatch):
    monkeypatch.setattr(soglie, "DOCTOR_LIMITE", (0.3, 0.5))
    assert str(_doctor(monkeypatch, [("bearish", 60)])["verdict"]).startswith("TRIM/HEDGE")


# ------------------------------------------------------------------ geopolitica

def test_cina_taiwan_e_dazi_sono_due_temi_con_soglie_distinte():
    assert "Cina-Taiwan/dazi" not in S._POLI_TOPICS
    assert S._POLI_SOGLIE["Cina-Taiwan"] == S._POLI_SOGLIE["conflitto/guerra"]
    assert S._POLI_SOGLIE["dazi"] == (20, 40, 60)
    with language_context("it"):
        s = S.politics_score({"Cina-Taiwan": 0.16, "dazi": 0.16})
    assert _riga(s, "Cina-Taiwan")[2] == 2 and _riga(s, "dazi")[2] == 0
    assert S._poli_termini_ok("Will the US raise tariffs on EU goods in 2099?", S._POLI_TERMINI["dazi"])
    assert not S._poli_termini_ok("Will the US lower tariffs on EU goods in 2099?", S._POLI_TERMINI["dazi"])
    assert S._poli_termini_ok("Will China blockade Taiwan in 2099?", S._POLI_TERMINI["Cina-Taiwan"])


# ------------------------------------------------------------------ v3: mutazioni del revisore

def test_M1_funding_nella_banda_calma_non_ha_direzione():
    from datetime import datetime, timezone
    adesso = datetime(2031, 3, 17, 12, tzinfo=timezone.utc)
    for scarto in (1.0, -1.0):          # 0,22 punti: banda calma
        f = 10.95 + scarto
        intel = {"fetched_at_utc": adesso.isoformat(), "top_10_perps_by_oi": [
            {"asset": "BTC", "funding_annualized_pct": f, "premium_basis_pts": 0, "oi_usd_m": 1000},
            {"asset": "ETH", "funding_annualized_pct": f, "premium_basis_pts": 0, "oi_usd_m": 500}]}
        s = S.crypto_score(intel, adesso=adesso)
        assert s["metrics"]["direzione"] == "neutra" and s["verdict"] == "CRYPTO CALMO", s


def test_M7_il_pdf_colora_tutte_e_quattro_le_bande():
    from bellomberg.reporting import pdf_institutional as pdf
    if pdf.RED is None:
        pytest.skip("reportlab assente")
    assert [pdf.colore_banda(f) for f in (0.1, 0.3, 0.6, 0.8, 1.0)] == \
        [pdf.GREEN, pdf.GOLD_TXT, pdf.ORANGE, pdf.RED, pdf.RED]
    assert pdf.colore_banda(0.7499) is pdf.ORANGE and pdf.colore_banda(0.75) is pdf.RED


def test_M11_flat_non_si_legge_come_contango_pieno():
    s = _opt(_vix(19.0, 20.0), _superficie(0.18, 0.15, -0.045))       # 0,95: FLAT
    assert s["metrics"]["vix_term_structure"] == "FLAT"
    assert "contango sotto la norma" in _riga(s, "VIX/VIX3M")[1]


def test_M12_sulla_soglia_di_sconto_e_nella_norma():
    assert soglie.prezzo_protezione(soglie.VRP_SCONTO, 1.0)[0] == "NORMAL"
    assert soglie.prezzo_protezione(soglie.VRP_SCONTO - 1e-9, 1.0)[0] == "DISCOUNT"
    assert soglie.prezzo_protezione(soglie.VRP_CARA, 1.0)[0] == "EXPENSIVE"


# ------------------------------------------------------------------ v3: Doctor e dazi

def test_doctor_espone_le_soglie_nel_payload(monkeypatch):
    d = _doctor(monkeypatch, [("bearish", 60)])
    assert d["thresholds"] == {"hold_borderline_abs": soglie.DOCTOR_LIMITE[0],
                               "full_recommendation_abs": soglie.DOCTOR_LIMITE[1]}
    monkeypatch.setattr(soglie, "DOCTOR_LIMITE", (0.3, 0.5))
    assert _doctor(monkeypatch, [("bearish", 60)])["thresholds"]["full_recommendation_abs"] == 0.5


@pytest.mark.parametrize("domanda,conta", [
    ("Will the Supreme Court rule Trump's tariffs unconstitutional in 2099?", False),
    ("Will tariff revenue exceed $300B in 2099?", False),
    ("Will the US issue tariff refunds in 2099?", False),
    ("Will the Supreme Court strike down tariffs on China in 2099?", False),
    ("Will the US raise tariffs on China in 2099?", True),
    ("Will the US impose new tariffs on EU cars in 2099?", True),
    ("Will tariffs on Mexico rise above 25% by June 2099?", True),
    ("Will the US lower tariffs on China in 2099?", False),
    # v4: frasi del revisore, catch-all «tariffs on» tolto
    ("Tariffs on Mexico above 25% by June 2099?", False),
    ("Will tariffs on Canada be lifted in 2099?", False),
    ("Will tariffs on Canada be removed in 2099?", False),
    ("Will tariffs on Canada be repealed in 2099?", False),
    ("Will tariffs on Canada be reduced in 2099?", False),
    ("Will the court rule tariffs on China illegal in 2099?", False),
    ("Will the US exempt smartphones from tariffs on China in 2099?", False),
    ("Will the US delay tariffs on Mexico in 2099?", False),
    ("Will the US postpone tariffs on Mexico in 2099?", False),
    ("Will the US pause tariffs on Mexico in 2099?", False),
    ("Will the US suspend tariffs on Mexico in 2099?", False),
    ("Will the Supreme Court uphold tariffs on China in 2099?", False),
    ("Will tariffs on China fall below 30% in 2099?", False),
    ("Will tariffs on China be cut in 2099?", False),
    ("Will the US raise tariffs on Canada and then lift them in 2099?", False),
    ("Will tariffs on China be raised then removed in 2099?", False),
    # casi misti che DEVONO contare
    ("Will the US raise tariffs before the Supreme Court strikes them down in 2099?", True),
    ("Will the US raise tariffs to boost revenue in 2099?", True),
    ("Will the US hike tariffs on EU steel in 2099?", True),
    ("Will the trade war escalate in 2099?", True),
    # domande sui dazi senza escalation e senza parole di pace: non sono rischio
    ("Will the Supreme Court hear the tariff case in 2099?", False),
    ("Will tariffs be discussed at the G20 in 2099?", False),
])
def test_dazi_conta_solo_l_escalation(domanda, conta):
    assert S._poli_termini_ok(domanda, S._POLI_TERMINI["dazi"]) is conta, domanda


@pytest.mark.parametrize("domanda", [
    "Will the Supreme Court rule Trump's tariffs unconstitutional in 2099?",
    "Will the US issue tariff refunds in 2099?",
    "Will the court strike down the tariffs in 2099?",
])
def test_annullamento_e_rimborsi_sono_risoluzione(domanda):
    # un tema futuro con «tariff» nudo o con altri termini non deve contarli come rischio
    assert S._poli_risoluzione(domanda) is True, domanda


def test_entrate_non_sono_risoluzione_globale_ma_senza_verbo_non_contano():
    # v4: «revenue» fuori dalla lista globale (scartava «raise tariffs to boost revenue»)
    assert S._poli_risoluzione("Will the US raise tariffs to boost revenue in 2099?") is False
    assert not S._poli_termini_ok("Will tariff revenue exceed $300B in 2099?", S._POLI_TERMINI["dazi"])


def test_allentamento_dei_dazi_vale_solo_per_il_tema_dazi():
    # le parole di allentamento dei dazi non tolgono mercati agli altri temi
    assert S._poli_termini_ok("Will China delay an invasion of Taiwan in 2099?", S._POLI_TERMINI["Cina-Taiwan"])


def test_dazi_i_tre_mercati_del_revisore_non_fanno_rischio(monkeypatch):
    from bellomberg.agents import agent_tools
    fut = "2099-12-31T00:00:00Z"

    def mk(q, yes, vol):
        return {"question": q, "outcomes": ["Yes", "No"], "prices": [yes, 1 - yes], "end_date": fut,
                "volume_24h": vol, "activity_status": "active"}
    risposte = {S._POLI_TOPICS["dazi"]: [
        mk("Will the Supreme Court rule Trump's tariffs unconstitutional in 2099?", 0.65, 90000.0),
        mk("Will tariff revenue exceed $300B in 2099?", 0.7, 50000.0),
        mk("Will the US issue tariff refunds in 2099?", 0.55, 50000.0)]}
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events",
                        lambda q, max_results=8: {"results": risposte.get(q, [])})
    with language_context("it"):
        s = S.politics_score()
    assert "dazi" not in (s or {}).get("metrics", {}).get("topics", {}) if s else True
    if s:
        assert "dazi" in s["metrics"]["non_calcolabili"], s["metrics"]
    risposte[S._POLI_TOPICS["dazi"]].append(mk("Will the US raise tariffs on China in 2099?", 0.25, 1000.0))
    with language_context("it"):
        s = S.politics_score()
    assert s["metrics"]["topics"]["dazi"] == pytest.approx(0.25)
    assert _riga(s, "dazi")[2] == 1



# ------------------------------------------------------------------ v4: confini e rami muti

def test_M12_segnale_vrp_al_confine_esatto_dello_sconto(monkeypatch):
    # signal_engine: «elif ratio < VRP_SCONTO»: sul confine esatto nessun segnale (nella norma)
    assert _segnale(monkeypatch, soglie.VRP_SCONTO, 0.01) == []
    sig = _segnale(monkeypatch, soglie.VRP_SCONTO - 0.001, 0.01)
    assert sig and "A SCONTO" in str(sig[0]["reading"])
    assert _segnale(monkeypatch, soglie.VRP_CARA - 0.001, 0.10) == []


def test_B5_superficie_dichiara_skew_nd_senza_iv_atm():
    from bellomberg.portfolio import vol_surface as vs
    # IV ATM non positiva (0): _interpret la stampa, ma lo skew normalizzato non esiste
    front = {"expiry": _d(30), "days": 30, "atm_iv": 0.0, "rr25": -0.05, "bf25": 0.0, "pc_oi_ratio": None}
    with language_context("it"):
        testo = str(vs._interpret("ZZSYN", [{**front, "atm_iv": 0.18}], None, 0.15, 1.2, iv30=0.18))
        muto = str(vs._interpret("ZZSYN", [front], None, 0.15, 1.2, iv30=0.18))
    assert "Skew n.d." not in testo
    assert "Skew n.d.: RR25 -5.0pt misurato, IV ATM del front mancante" in muto
    assert "MARCATO" not in muto and "Skew put nella norma" not in muto



# ------------------------------------------------------------------ v5: dazi, whitelist di forma

# (domanda, conta). True = escalation esplicita; False = allentamento/risoluzione o forma non
# esplicita (falso negativo accettato, n.d. dichiarato). Nessuna inversione ammessa.
DAZI_V5 = [
    # escalation esplicita: conta
    ("Will the US raise tariffs on China in 2099?", True),
    ("Will Trump impose new tariffs on EU cars in 2099?", True),
    ("Will the US increase tariffs on Mexico by June 2099?", True),
    ("Will the US hike tariffs on EU steel in 2099?", True),
    ("Will Trump announce tariffs on pharmaceuticals in 2099?", True),
    ("Will the US put tariffs on Canadian lumber in 2099?", True),
    ("Will Trump slap tariffs on Japan in 2099?", True),
    ("Will the EU levy tariffs on US whiskey in 2099?", True),
    ("Will China introduce tariffs on US soybeans in 2099?", True),
    ("Will the US expand tariffs to semiconductors in 2099?", True),
    ("Will the US double tariffs on Chinese EVs in 2099?", True),
    ("Will the US triple tariffs on steel in 2099?", True),
    ("Will the US add tariffs on Vietnam in 2099?", True),
    ("Will tariffs on China rise above 60% in 2099?", True),
    ("Will tariffs on Mexico go up in 2099?", True),
    # v6: «exceed» misura un livello, non un'escalation; «fail» e' nella guardia (falso negativo
    # accettato: la frase mescola fallimento e rialzo)
    ("Will tariffs on China exceed 100% in 2099?", False),
    ("Will the US-China trade deal fail and China tariffs rise above 60%?", False),
    ("Will the US and China start a trade war in 2099?", True),
    ("Will the trade war escalate in 2099?", True),
    ("Will tariffs be raised again after the pause ends in 2099?", True),
    ("Will tariffs on Chinese goods be increased in 2099?", True),
    ("Will the US raise tariffs before the Supreme Court strikes them down in 2099?", True),
    ("Will the US raise tariffs to boost revenue in 2099?", True),
    # inversioni del revisore e allentamenti: NON contano
    ("Will the tariff hike be reversed in 2099?", False),
    ("Will the tariff hike be rolled back in 2099?", False),
    ("Will the tariff hike be canceled in 2099?", False),
    ("Will the court block the new tariffs in 2099?", False),
    ("Will the court overturn the new tariffs in 2099?", False),
    ("Will the court invalidate the new tariffs in 2099?", False),
    ("Will the US scrap the new tariffs on Canada in 2099?", False),
    ("Will the US drop the tariff increase on Mexico in 2099?", False),
    ("Will Trump back down on tariffs in 2099?", False),
    ("Will the tariffs on Canada be waived in 2099?", False),
    ("Will tariffs on China be lowered in 2099?", False),
    ("Will the US rescind the tariff hike in 2099?", False),
    ("Will the US revoke tariffs on India in 2099?", False),
    ("Will the US pause the tariff increase in 2099?", False),
    ("Will tariffs on Canada be lifted in 2099?", False),
    ("Will tariffs on Canada be removed in 2099?", False),
    ("Will tariffs on Canada be repealed in 2099?", False),
    ("Will the court rule tariffs on China illegal in 2099?", False),
    ("Will the US exempt smartphones from tariffs on China in 2099?", False),
    ("Will the US delay tariffs on Mexico in 2099?", False),
    ("Will the Supreme Court uphold tariffs on China in 2099?", False),
    ("Will tariffs on China fall below 30% in 2099?", False),
    ("Will the US raise tariffs on Canada and then lift them in 2099?", False),
    # forma di escalation valida ma ribaltata: decide la SECONDA guardia
    ("Will the US raise tariffs on China only to have them reversed in 2099?", False),
    ("Will the US impose tariffs on Brazil that are later rolled back in 2099?", False),
    ("Will the US hike tariffs on Mexico and then cancel them in 2099?", False),
    # i 3 mercati del primo giro
    ("Will the Supreme Court rule Trump's tariffs unconstitutional in 2099?", False),
    ("Will tariff revenue exceed $300B in 2099?", False),
    ("Will the US issue tariff refunds in 2099?", False),
    # forme nominali da sole: falso negativo accettato
    ("Will the new tariffs take effect in 2099?", False),
    ("Will the tariff increase take effect in 2099?", False),
    ("Tariffs on Mexico above 25% by June 2099?", False),
    ("Will the US not raise tariffs on China in 2099?", False),
]


def test_dazi_v5_casi_sono_almeno_41():
    assert len(DAZI_V5) >= 41


@pytest.mark.parametrize("domanda,conta", DAZI_V5)
def test_dazi_v5_whitelist_di_forma(domanda, conta):
    assert S._poli_termini_ok(domanda, S._POLI_TERMINI["dazi"]) is conta, domanda


def test_dazi_v5_la_riga_dichiara_il_limite():
    with language_context("it"):
        s = S.politics_score({"dazi": 0.3, "recessione USA": 0.1})
    assert "contati solo mercati di escalation esplicita" in _riga(s, "dazi")[1]


def test_dazi_v5_il_mercato_invertito_piu_scambiato_non_vince(monkeypatch):
    from bellomberg.agents import agent_tools
    fut = "2099-12-31T00:00:00Z"

    def mk(q, yes, vol):
        return {"question": q, "outcomes": ["Yes", "No"], "prices": [yes, 1 - yes], "end_date": fut,
                "volume_24h": vol, "activity_status": "active"}
    risposte = {S._POLI_TOPICS["dazi"]: [
        mk("Will the tariff hike be reversed in 2099?", 0.7, 900000.0),
        mk("Will the court block the new tariffs in 2099?", 0.7, 800000.0),
        mk("Will the US raise tariffs on China in 2099?", 0.25, 1000.0)]}
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events",
                        lambda q, max_results=8: {"results": risposte.get(q, [])})
    with language_context("it"):
        s = S.politics_score()
    assert s["metrics"]["topics"]["dazi"] == pytest.approx(0.25)
    assert _riga(s, "dazi")[2] == 1



# ------------------------------------------------------------------ v6: casi del revisore (dazi)
# Copiati dalla sonda avversariale del revisore (rv_soglie_dazi_casi.py, 11/10/2026). Atteso
# True = escalation, False = da scartare. INVERSIONE (False che conta) mai ammessa; i falsi
# negativi dichiarati sotto danno il tema n.d. dichiarato (accettato dal coordinatore).
DAZI_CASI_41 = [
    ("Will Trump raise tariffs before the Supreme Court strikes them down?", True),
    ("Will the Supreme Court strike down tariffs and Trump impose new tariffs on China?", False),
    ("Will tariffs on Canada be lifted by June 30, 2099?", False),
    ("Will tariffs on China be removed in 2099?", False),
    ("Will tariffs on EU goods be repealed in 2099?", False),
    ("Will the Supreme Court uphold tariffs on China?", False),
    ("Will the court rule tariffs on China illegal?", False),
    ("Will Trump exempt smartphones from tariffs on China?", False),
    ("Will the US grant an exemption from tariffs on Mexico?", False),
    ("Will tariffs on China fall below 30% in 2099?", False),
    ("Will Trump raise tariffs to boost revenue in 2099?", True),
    ("Will the US-China trade war escalate in 2099?", True),
    ("Will Trump announce new tariffs in 2099?", True),
    ("Will Trump cut tariffs on China to 10%?", False),
    ("Will tariffs on India be reduced in 2099?", False),
    ("Will Trump delay tariffs on the EU?", False),
    ("Will Trump postpone the tariffs on Mexico?", False),
    ("Will the tariff rate on China be higher than 50% on Dec 31?", True),
    ("Will the Supreme Court rule Trump tariffs unconstitutional in 2099?", False),
    ("Will the US issue tariff refunds in 2099?", False),
    ("Will tariff revenue exceed 300B in 2099?", False),
    ("Will the US and China agree to cut tariffs in 2099?", False),
    ("Will tariffs be raised again after the pause ends in 2099?", True),
    ("Will the tariff hike be reversed in 2099?", False),
    ("Will Trump announce new tariffs on Japan in 2099?", True),
    ("Will the tariff increase be rolled back in 2099?", False),
    ("Will the new tariffs be canceled in 2099?", False),
    ("Will the new tariffs be cancelled in 2099?", False),
    ("Will the court block the new tariffs in 2099?", False),
    ("Will Congress overturn the tariff hike in 2099?", False),
    ("Will Trump scrap the tariff increase in 2099?", False),
    ("Will the EU drop its new tariffs on US goods in 2099?", False),
    ("Will Trump back down on the tariff hike in 2099?", False),
    ("Will the tariff hike be waived for Canada in 2099?", False),
    ("Will the new tariffs be lowered in 2099?", False),
    ("Will tariffs on China be raised above 60% in 2099?", True),
    ("Will the EU impose retaliatory tariffs on US goods in 2099?", True),
    ("Will Trump impose a 25% tariff on EU cars in 2099?", True),
    ("Will the US increase tariffs on Canada after talks collapse in 2099?", True),
    ("Will a court invalidate the new tariffs in 2099?", False),
    ("Will Trump threaten new tariffs on Brazil in 2099?", True),
]

DAZI_CASI_V5 = [
    ("Will Trump not raise tariffs on China in 2099?", False),
    ("Will Trump decide not to raise tariffs in 2099?", False),
    ("Will Trump refuse to raise tariffs on China in 2099?", False),
    ("Will Trump fail to impose tariffs on the EU in 2099?", False),
    ("Will Trump be unable to impose tariffs in 2099?", False),
    ("Will the Senate reject a bill to raise tariffs in 2099?", False),
    ("Will the House vote to terminate the emergency used to impose tariffs in 2099?", False),
    ("Will the court rule Trump lacked authority to impose tariffs in 2099?", False),
    ("Will the Supreme Court rule against Trump's power to impose tariffs in 2099?", False),
    ("Will Congress prevent Trump from imposing tariffs in 2099?", False),
    ("Will Congress stop Trump from raising tariffs in 2099?", False),
    ("Will the US-China trade war be resolved in 2099?", False),
    ("Will the trade war ending be announced in 2099?", False),
    ("Will the trade war wind down in 2099?", False),
    ("Will the US and China de-escalate the trade war in 2099?", False),
    ("Will Trump avoid raising tariffs on Japan in 2099?", False),
    ("Will tariffs on China be raised or lowered in 2099?", False),
    ("Will Canada retaliate with tariffs in 2099?", False),   # falso negativo accettabile
    ("Will the court allow Trump to impose tariffs in 2099?", True),
    ("Will Biden impose tariffs on China in 2099?", True),
    ("Will the Senate pass a bill to raise tariffs in 2099?", True),
    ("Will Trump double tariffs on China in 2099?", True),
]

# livelli, non escalation (v6): «exceed/top» tolti dalla forma
DAZI_LIVELLI = [
    ("Will US tariffs on China exceed 0% in 2099?", False),
    ("Will the average US tariff rate top 20% in 2099?", False),
]
DAZI_FALSI_NEGATIVI_ACCETTATI = {
    "Will the tariff rate on China be higher than 50% on Dec 31?",
    "Will Trump threaten new tariffs on Brazil in 2099?",
    "Will the Senate pass a bill to raise tariffs in 2099?",
}


@pytest.mark.parametrize("domanda,atteso", DAZI_CASI_41 + DAZI_CASI_V5 + DAZI_LIVELLI)
def test_dazi_v6_nessuna_inversione(domanda, atteso):
    conta = S._poli_termini_ok(domanda, S._POLI_TERMINI["dazi"])
    if not atteso:
        assert conta is False, "INVERSIONE: " + domanda
    elif domanda not in DAZI_FALSI_NEGATIVI_ACCETTATI:
        assert conta is True, "falso negativo non dichiarato: " + domanda


def test_dazi_v6_la_guardia_vince_su_fail_dell_escalation():
    # «fail» e' in _POLI_ESCALATION (risoluzione forzata a False) ma per i dazi l'allentamento vince
    q = "Will Trump fail to impose tariffs on the EU in 2099?"
    assert S._poli_risoluzione(q.lower()) is False
    assert S._poli_termini_ok(q, S._POLI_TERMINI["dazi"]) is False


def test_dazi_v6_de_escalation_non_e_escalation():
    for q in ("Will the US and China de-escalate the trade war in 2099?",
              "Will the trade war deescalate in 2099?",
              "Will the trade war de-escalate in 2099?"):
        assert S._poli_termini_ok(q, S._POLI_TERMINI["dazi"]) is False, q
    assert S._poli_termini_ok("Will the trade war escalate in 2099?", S._POLI_TERMINI["dazi"]) is True



@pytest.mark.parametrize("domanda", [
    # forma di escalation valida, decide SOLO la seconda guardia (una parola per guardia)
    "Will the US impose tariffs on Mexico and then pause them in 2099?",
    # «pauses» non e' nella lista globale (che ha pause/paused): decide la guardia dei dazi
    "Will the US impose tariffs on Mexico until Trump pauses them in 2099?",
    "Will the US raise tariffs on China and then de-escalate in 2099?",
    "Will the US raise tariffs on China but then deescalate in 2099?",
    "Will Trump raise tariffs on India and then decline to enforce them in 2099?",
])
def test_dazi_v6_ogni_guardia_decide_da_sola(domanda):
    assert S._poli_termini_ok(domanda, S._POLI_TERMINI["dazi"]) is False, domanda


def test_dazi_v6_le_forme_non_leggono_de_escalate_come_escalate():
    import re
    for q in ("will the us and china de-escalate the trade war in 2099?",
              "will the trade war de-escalate in 2099?"):
        assert not any(re.search(x, q) for x in S._POLI_DAZI_FORME), q
    assert any(re.search(x, "will the trade war escalate in 2099?") for x in S._POLI_DAZI_FORME)
    assert any(re.search(x, "will the us and china escalate the trade war in 2099?") for x in S._POLI_DAZI_FORME)
