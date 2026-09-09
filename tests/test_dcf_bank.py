"""Motore banca V4 (§9-novies n.1, collaudato 21/07): funzioni PURE di
dcf_bank, spec costruiti a mano con la stessa struttura di build_bank_spec.
Nessuna rete: build_bank_spec viene chiamata senza ticker_obj, con
currency == financialCurrency (niente ramo FX) e profilo senza dam_industry
(niente lookup Damodaran).
"""
import pytest

from bellomberg.valuation.dcf_bank import (N_YEARS, _cost_of_risk_ttc, _fv_ddm, _fv_residual_income,
                      build_bank_spec, compute_fair_values)


def _spec(bvps=10.0, shares=100.0, ke=0.09, g=0.02, payout=0.5,
          roe_vec=None, roe_terminal=None):
    roe_vec = roe_vec or [0.12] * N_YEARS
    return {
        "bvps": bvps, "shares": shares,
        "book_value": (bvps * shares) if bvps else None,
        "ke": ke, "growth_lt": g, "payout": payout,
        "roe_vec": roe_vec,
        "roe_terminal": roe_terminal if roe_terminal is not None else roe_vec[-1],
    }


INFO = {
    "currency": "EUR", "financialCurrency": "EUR",
    "sharesOutstanding": 1_000_000_000, "bookValue": 10.0,
    "returnOnEquity": 0.12, "trailingEps": 1.2, "currentPrice": 8.0,
    "payoutRatio": 0.50, "beta": 1.0,
    "country": "Italy", "longName": "Banca Test", "sector": "Financial Services",
}
WACC = {"rf": 0.03, "erp": 0.05, "crp": 0.0}
PROFILE = {"fade_years_default": 5, "franchise_cap": 0.04}


def test_identita_ddm_ri_con_payout_endogeno():
    # payout* = 1 - g/ROE (identita' di crescita sostenibile): il book cresce
    # esattamente a g e DDM multi-stage e RI convergono al centesimo (clean
    # surplus + stessa exit) — il punto del fix V4 (GAMMA 58% -> 11% nel #46).
    roe, g = 0.12, 0.02
    spec = _spec(ke=0.09, g=g, payout=1.0 - g / roe, roe_vec=[roe] * N_YEARS)
    ri = _fv_residual_income(spec)
    dd = _fv_ddm(spec)
    assert ri is not None and dd is not None
    assert abs(ri - dd) <= 0.02, f"RI {ri} != DDM {dd}: identita' clean-surplus rotta"


def test_ddm_nd_senza_book():
    # Book n.d. -> DDM None dichiarato, MAI ripiego su EPS (regola 14/07).
    spec = _spec(bvps=None)
    assert _fv_ddm(spec) is None
    fv = compute_fair_values(spec)
    assert fv["fair_value_ddm"] is None
    assert "DDM" not in fv["blend_methods"]


def test_gerarchia_fade_analista_profilo_default_e_non_numerico():
    # analista > profilo > default 10 anni (V4), fonte dichiarata in _sources.
    s_prof = build_bank_spec("TEST.MI", INFO, WACC, profile=PROFILE)
    assert s_prof["fade_years"] == 5
    assert "PROFILO" in s_prof["_sources"]["fade_years"]

    s_agent = build_bank_spec("TEST.MI", INFO, WACC, fade_years=7, profile=PROFILE)
    assert s_agent["fade_years"] == 7
    assert s_agent["_sources"]["fade_years"] == "OVERRIDE ANALISTA"

    s_none = build_bank_spec("TEST.MI", INFO, WACC, profile=None)
    assert s_none["fade_years"] == N_YEARS

    # fuori bound e non numerico: IGNORATI ma DICHIARATI (mai crash, mai zitti)
    s_oob = build_bank_spec("TEST.MI", INFO, WACC, fade_years=99, profile=PROFILE)
    assert s_oob["fade_years"] == 5
    assert "IGNORATO" in s_oob["_sources"]["fade_years_note"]

    s_str = build_bank_spec("TEST.MI", INFO, WACC, fade_years="sette", profile=PROFILE)
    assert s_str["fade_years"] == 5
    assert "non numerico" in s_str["_sources"]["fade_years_note"]


def test_costo_del_rischio_finestra_segno_e_misti():
    # overlap <3 anni: 2 punti non fanno un ciclo -> None (n.d. dal chiamante)
    h2 = {"items": {"impairment_ifrs9": {2022: -50.0, 2023: -55.0},
                    "loans_to_customers": {2022: 10_000.0, 2023: 11_000.0}}}
    assert _cost_of_risk_ttc(h2) is None

    # serie tutta negativa (convenzione filing): costo POSITIVO in bps, mai abs() cieco
    anni = (2020, 2021, 2022, 2023)
    h_neg = {"items": {"impairment_ifrs9": dict(zip(anni, (-40.0, -60.0, -50.0, -55.0))),
                       "loans_to_customers": {y: 10_000.0 for y in anni}},
             "_source": "esef"}
    cor = _cost_of_risk_ttc(h_neg)
    assert cor["sign"] == -1.0 and cor["mixed"] is False
    assert cor["bps"][2021] == 60.0
    assert cor["avg_bps"] == pytest.approx(51.25, abs=0.06)

    # segni MISTI (rilasci post-COVID): convenzione dichiarata NON determinabile
    h_mix = {"items": {"impairment_ifrs9": dict(zip(anni, (-40.0, 35.0, -50.0, 45.0))),
                       "loans_to_customers": {y: 10_000.0 for y in anni}}}
    cm = _cost_of_risk_ttc(h_mix)
    assert cm["mixed"] is True
    assert "MISTI" in cm["sign_note"]


def test_ptbv_mai_espulso_dai_gemelli_ri_ddm():
    # RI e DDM sono correlati per costruzione: se l'outlier e' il P/TBV (unico
    # cross-check indipendente) resta nel blend con warning, non viene votato fuori.
    spec = _spec(ke=0.10, g=0.02, payout=0.5, roe_vec=[0.45] * N_YEARS)
    fv = compute_fair_values(spec)
    vals = [fv["fair_value_ri"], fv["fair_value_ptbv"], fv["fair_value_ddm"]]
    assert all(v is not None for v in vals)
    med = sorted(vals)[1]
    assert abs(fv["fair_value_ptbv"] / med - 1.0) > 0.6, "setup: P/TBV deve essere outlier"
    assert "P/TBV" in fv["blend_methods"]
    assert any("NON escluso" in w for w in fv["warnings"])


def test_warning_terminale_su_roe_anno_10_del_path():
    # path completo 10y: il terminale gira sull'anno 10, il franchise cap non
    # morde la perpetuita' -> va DETTO. Se path e terminale coincidono, silenzio.
    s_path = _spec(roe_vec=[0.15] * N_YEARS, roe_terminal=0.10)
    assert any("TERMINALE su ROE anno-10" in w
               for w in compute_fair_values(s_path)["warnings"])
    s_ok = _spec(roe_vec=[0.15] * N_YEARS, roe_terminal=0.15)
    assert not any("TERMINALE su ROE anno-10" in w
                   for w in compute_fair_values(s_ok)["warnings"])
