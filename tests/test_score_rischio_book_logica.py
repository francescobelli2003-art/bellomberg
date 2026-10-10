"""Punteggio RISCHIO BOOK (quant_score) — test della LOGICA, non della forma (fix score 09/10,
Opus 5.5; audit SCORE-VOL-QUANT §2). Asseriscono le proprieta' che un risk manager pretende:
mono-titolo >= ELEVATO, monotonia (piu' rischio -> punteggio non minore), niente gradino attorno
al target di vol del PM, Sharpe fuori dal punteggio (niente pro-ciclicita'), coda contro il
budget del mandato, beta di Dimson per book non sincroni. Numeri, mandato e ticker INVENTATI.
"""
import math

import numpy as np
import pandas as pd
import pytest

from bellomberg.agents import specialist_scores as S
from bellomberg.core.language import language_context

_OK = {"verdict": "RECONCILED", "beta_per_decisioni": True, "betas": {"portfolio_risk_spy": 1.0}}
_MAND = {"rischio": {"volatilita_target_pct": 20, "stress_gfc_pct": 25}}
_NEG = {"veicoli": {"ZZETF": {"classe_size": "veicolo"}}, "origine": "sintetico"}


def _pos(pesi, nomi=None, cassa=0.0):
    nomi = nomi or ["ZZ%02d" % i for i in range(len(pesi))]
    return {"positions": [{"ticker": t, "valore_mercato_eur": w * 1000.0} for t, w in zip(nomi, pesi)],
            "cash_disponibile_eur": cassa}


def _stress(perdita):
    return {"stress_scenario": "gfc_2008", "stress_fallback": False,
            "stress_meta": {"window_loss_pct": perdita, "proxied": {}}}


def _settori(primo):
    resto = (100.0 - primo) / 6.0
    return {"econ_axis": {"by_bucket": [{"bucket": "ZZPrimo", "weight_pct": primo}]
                          + [{"bucket": "QQ%d" % i, "weight_pct": resto} for i in range(6)],
                          "coverage_pct": 100.0}}


_DIV = [1.0 / 30] * 30


def _q(pesi=_DIV, vol=15.0, beta=0.8, perdita=-12.0, primo_cluster=20.0, nomi=None, **port):
    port = {"vol_ewma_annual_pct": vol, "vol_annual_pct": vol, "beta_vs_spy": beta,
            "var_95_1d_pct": -1.645 * vol / math.sqrt(252), "sharpe": 1.0, "max_dd_1y_pct": -8.0, **port}
    with language_context("it"):
        return S.quant_score(_pos(pesi, nomi), {"portfolio": port}, beta_reconcile=_OK,
                             stress_data=_stress(perdita), mandato=_MAND,
                             sector_data=_settori(primo_cluster), negozio=_NEG)


def _idx(s):
    return 100.0 * s["score"] / s["max_score"]


def test_mono_titolo_e_almeno_elevato_anche_con_vol_bassa():
    # audit §2.2e: 100% su un titolo con vol 14 e beta 0,3 usciva «RISCHIO MEDIO»
    s = _q([1.0], vol=14.0, beta=0.3, perdita=-5.0, primo_cluster=100.0)
    assert _idx(s) >= 50.0 and s["verdict"] in ("RISCHIO ELEVATO", "RISCHIO CRITICO"), s
    assert s["metrics"]["concentration_floor"] == "SINGLE_NAME"


def test_mono_titolo_senza_dati_settoriali_resta_almeno_elevato_e_lo_dichiara():
    with language_context("it"):
        s = S.quant_score(_pos([1.0]), {"portfolio": {"vol_ewma_annual_pct": 10.0, "beta_vs_spy": 0.2}},
                          beta_reconcile=_OK, mandato=_MAND, negozio=_NEG, stress_data=_stress(-5.0))
    # senza pavimento: vol 0 + replay 0 + beta 0 + nome 3 + HHI 3 = 6/15 (40, «MEDIO»)
    assert _idx(s) >= 50.0 and s["verdict"] == "RISCHIO ELEVATO"
    assert any("Pavimento" in r[0] for r in s["info"]), s["info"]


def test_un_etf_al_100_per_cento_non_e_un_nome_singolo():
    # il veicolo diversificato (negozio: classe_size 'veicolo') non accende il pavimento del nome
    s = _q([1.0], nomi=["ZZETF"], vol=14.0, beta=0.9, perdita=-10.0, primo_cluster=20.0)
    assert s["metrics"]["top_single_name_pct"] == 0.0 and s["metrics"]["concentration_floor"] is None


@pytest.mark.parametrize("campo,valori", [
    ("vol", [8, 12, 15, 18, 19.99, 20, 20.01, 24, 28, 35, 60]),
    ("beta", [0.2, 0.6, 0.9, 1.0, 1.2, 1.4, 2.0]),
    ("perdita", [-2.0, -10.0, -15.0, -20.0, -25.0, -30.0, -45.0]),
    ("primo_cluster", [15.0, 25.0, 35.0, 45.0, 55.0, 69.0, 80.0]),
])
def test_monotonia_piu_rischio_punteggio_non_minore(campo, valori):
    punteggi = [_q(**{campo: v})["score"] for v in valori]
    assert punteggi == sorted(punteggi), list(zip(valori, punteggi))
    assert punteggi[-1] > punteggi[0]          # e la metrica conta davvero


def test_monotonia_sulla_concentrazione_per_nome():
    libri = [[1.0 / 30] * 30, [0.15] + [0.85 / 29] * 29, [0.30] + [0.70 / 29] * 29,
             [0.45] + [0.55 / 29] * 29, [0.6, 0.4], [1.0]]
    punteggi = [_q(p)["score"] for p in libri]
    assert punteggi == sorted(punteggi), punteggi


@pytest.mark.parametrize("centro", [15.0, 20.0, 25.0, 30.0])
def test_nessun_gradino_di_0_01_attorno_al_target_e_alle_ancore(centro):
    # target 20: le ancore della vol sono 15/20/25/30 (0,75x..1,5x). 0,02 punti di vol non
    # possono spostare l'indice 0-100 di piu' di 0,1 (prima: +5 punti grezzi, BASSO -> MEDIO)
    a, b = _q(vol=centro - 0.01), _q(vol=centro + 0.01)
    assert abs(_idx(b) - _idx(a)) < 0.1, (_idx(a), _idx(b))


def test_sharpe_e_drawdown_trailing_non_spostano_il_punteggio():
    # stesso book prima e dopo il crollo: il rischio non puo' risultare piu' basso a valle
    # di una corsa al rialzo (pro-ciclicita', audit §2.2d)
    corsa = _q(vol=32.0, beta=2.0, perdita=-40.0, sharpe=2.0, max_dd_1y_pct=-12.0)
    crollo = _q(vol=32.0, beta=2.0, perdita=-40.0, sharpe=-0.2, max_dd_1y_pct=-30.0)
    assert corsa["score"] == crollo["score"] and corsa["verdict"] == crollo["verdict"]
    assert not [r for r in corsa["lines"] if "Sharpe" in r[0] or "Drawdown" in r[0] or "VaR" in r[0]]


def test_la_coda_oltre_il_budget_pesa_e_si_vede():
    dentro, oltre = _q(perdita=-12.0), _q(perdita=-34.2)
    assert oltre["score"] > dentro["score"]
    riga = [r for r in oltre["lines"] if "Replay GFC" in r[0]][0]
    assert riga[2] == 3.0 and "-34.2%" in riga[1] and "-25%" in riga[1], riga


def test_replay_sul_nav_con_la_cassa_non_stressata():
    # -20% sul book investito, meta' NAV in cassa: -10% del NAV (stessa regola del sizing)
    with language_context("it"):
        s = S.quant_score(_pos(_DIV, cassa=1000.0), {"portfolio": {"vol_ewma_annual_pct": 15.0}},
                          stress_data=_stress(-20.0), mandato=_MAND, negozio=_NEG)
    assert s["metrics"]["stress_gfc_nav_pct"] == pytest.approx(-10.0)


@pytest.mark.parametrize("stress,parola", [
    (None, "non calcolato"), ({"error": "ZZrete finta"}, "ZZrete finta"),
    ({"stress_scenario": "shock_3sigma", "stress_fallback": True, "stress_meta": {}}, "non applicato")])
def test_replay_mancante_e_dichiarato_fuori_dal_massimo(stress, parola):
    with language_context("it"):
        s = S.quant_score(_pos(_DIV), {"portfolio": {"vol_ewma_annual_pct": 15.0}},
                          stress_data=stress, mandato=_MAND, negozio=_NEG)
    riga = [r for r in s["lines"] if "Replay GFC" in r[0]][0]
    assert riga[2] is None and parola in riga[1], riga
    assert riga[0] in s["unscored"]


def test_senza_mandato_la_vol_e_nd_non_una_soglia_di_ripiego():
    with language_context("it"):
        s = S.quant_score(_pos(_DIV), {"portfolio": {"vol_ewma_annual_pct": 31.0}}, negozio=_NEG)
    riga = [r for r in s["lines"] if "Vol" in r[0]][0]
    assert riga[2] is None and "31.0%" in riga[1] and "target del mandato n.d." in riga[1], riga


def test_vol_storica_usata_solo_come_ripiego_etichettato():
    with language_context("it"):
        s = S.quant_score(_pos(_DIV), {"portfolio": {"vol_annual_pct": 22.0}}, mandato=_MAND, negozio=_NEG)
    riga = [r for r in s["lines"] if "Vol" in r[0]][0]
    assert "EWMA n.d." in riga[0] and riga[2] is not None


def test_beta_di_dimson_preferita_e_dichiarata():
    s = _q(beta=0.6, beta_vs_spy_dimson=1.05)
    riga = [r for r in s["lines"] if "Beta" in r[0]][0]
    assert "Dimson" in riga[0] and riga[1] == "1.05" and "Dimson non riconciliata" not in riga[0]
    assert s["metrics"]["beta_method"] == "dimson"
    assert any("sincrona" in r[0] for r in s["info"])


def test_fx_incompleto_pesi_nd_mai_valute_sommate():
    port = {"positions": [{"ticker": "ZZA", "valore_mercato": 1000.0}, {"ticker": "ZZB.L", "valore_mercato": 85000.0}],
            "fx_incomplete": ["GBX"]}
    with language_context("it"):
        s = S.quant_score(port, {"portfolio": {"vol_ewma_annual_pct": 15.0}}, mandato=_MAND, negozio=_NEG)
    riga = [r for r in s["lines"] if "HHI" in r[0]][0]
    assert riga[2] is None and "FX incompleto" in riga[1] and s["metrics"]["hhi"] is None


# ------------------------------------------------ stimatori in portfolio_risk

def test_ewma_risponde_all_ultimo_shock_e_annualizza():
    from bellomberg.portfolio.portfolio_risk import ewma_vol_annual_pct
    calmo = [0.01 * (1 if i % 2 else -1) for i in range(200)]       # 1% al giorno costante
    v = ewma_vol_annual_pct(calmo)
    assert v == pytest.approx(0.01 * math.sqrt(252) * 100, rel=1e-6)
    scosso = calmo[:-5] + [0.05, -0.05, 0.05, -0.05, 0.05]
    assert ewma_vol_annual_pct(scosso) > 2 * v                        # la vol storica 1a si muoverebbe appena
    assert ewma_vol_annual_pct(calmo[:30]) is None                    # serie corta: dichiarato dal chiamante


def test_dimson_recupera_la_beta_di_un_book_che_reagisce_il_giorno_dopo():
    from bellomberg.portfolio.portfolio_risk import beta_dimson
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2026-01-01", periods=250)
    spy = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
    # book europeo: meta' della seduta USA entra lo stesso giorno, meta' il giorno dopo
    book = 0.5 * spy + 0.5 * spy.shift(1).fillna(0) + pd.Series(rng.normal(0, 0.002, len(idx)), index=idx)
    sincrona = float(book.cov(spy) / spy.var())
    assert sincrona == pytest.approx(0.5, abs=0.1)
    assert beta_dimson(book, spy) == pytest.approx(1.0, abs=0.1)


def test_ewma_usa_lambda_0_94_ricorsione_scritta_qui():
    # oracolo SCRITTO NEL TEST (non importato): seme = media dei quadrati delle prime 20,
    # poi var = 0,94 var + 0,06 r^2. Un lambda 0,97 sposta il numero (banco: M9 sopravviveva)
    from bellomberg.portfolio.portfolio_risk import ewma_vol_annual_pct
    r = [0.01 * (1 if i % 2 else -1) for i in range(20)] + [0.03 * (1 if i % 3 else -1) for i in range(60)]
    var = sum(x * x for x in r[:20]) / 20
    for x in r[20:]:
        var = 0.94 * var + 0.06 * x * x
    atteso = math.sqrt(var * 252) * 100
    assert ewma_vol_annual_pct(r) == pytest.approx(atteso, rel=1e-9)


def test_dimson_somma_anche_l_anticipo():
    # book che reagisce allo SPY di ieri (0,5), di oggi (0,3) e di domani (0,4): Dimson = 1,2.
    # Senza il termine in anticipo verrebbe ~0,8 (banco: M10 sopravviveva)
    from bellomberg.portfolio.portfolio_risk import beta_dimson
    rng = np.random.default_rng(11)
    idx = pd.bdate_range("2026-01-01", periods=300)
    spy = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
    book = (0.5 * spy.shift(1) + 0.3 * spy + 0.4 * spy.shift(-1)).dropna()
    assert beta_dimson(book, spy) == pytest.approx(1.2, abs=0.02)


# ------------------------------------------------ seconda versione (riserve review 10/10)

_MAND_CAP = {"rischio": {"volatilita_target_pct": 20, "stress_gfc_pct": 25},
             "sizing": {"cap_single_pct": 5, "cap_settore_pct": 25}}


def _q2(posizioni, cassa=0.0, vol=15.0, beta=0.8, dimson=None, perdita=-12.0, settori=None, mandato=_MAND,
        rb=None, negozio=_NEG, **port_extra):
    port = {"vol_ewma_annual_pct": vol, "vol_annual_pct": vol, "beta_vs_spy": beta}
    if dimson is not None:
        port["beta_vs_spy_dimson"] = dimson
    pf = {"positions": [{"ticker": t, "valore_mercato_eur": v} for t, v in posizioni], "cash_disponibile_eur": cassa}
    pf.update(port_extra)
    with language_context("it"):
        return S.quant_score(pf, {"portfolio": port}, beta_reconcile=rb or _OK, stress_data=_stress(perdita),
                             mandato=mandato, sector_data=settori if settori is not None else _settori(20.0),
                             negozio=negozio)


def _riga(s, pezzo):
    return [r for r in s["lines"] if pezzo in r[0]][0]


def test_novanta_per_cento_cassa_e_un_titolo_al_dieci_non_e_concentrato():
    # riserva 6: sull'investito era «mono-titolo al 100%» -> pavimento ELEVATO
    s = _q2([("ZZUNO", 10000.0)], cassa=90000.0, vol=30.0, perdita=-50.0)
    assert s["metrics"]["top_single_name_pct"] == pytest.approx(10.0)
    assert s["metrics"]["concentration_floor"] is None and s["verdict"] in ("RISCHIO BASSO", "RISCHIO MEDIO"), s
    # vol e replay sul NAV: cassa a vol 0, non stressata
    assert s["metrics"]["vol_nav_pct"] == pytest.approx(3.0) and s["metrics"]["stress_gfc_nav_pct"] == pytest.approx(-5.0)
    assert "sul NAV" in _riga(s, "Vol")[0] and _riga(s, "Vol")[1].startswith("3.0% (30.0% x 10.0%)")


def test_vol_ewma_preferita_alla_storica_quando_ci_sono_entrambe():
    # banco: M1 (storica preferita) sopravviveva perche' i test le passavano UGUALI
    with language_context("it"):
        s = S.quant_score(_pos(_DIV), {"portfolio": {"vol_ewma_annual_pct": 15.0, "vol_annual_pct": 40.0}},
                          mandato=_MAND, negozio=_NEG, stress_data=_stress(-10.0))
    riga = _riga(s, "Vol")
    assert "EWMA 0,94" in riga[0] and riga[1].startswith("15.0% (15.0% x 100.0%)") and riga[2] == 0.0
    assert any("40.0%" in r[1] for r in s["info"])      # la storica resta, informativa


def test_hhi_sui_soli_nomi_singoli_veicoli_esclusi_e_dichiarati():
    # riserva 7: ETF al 60% + 10 nomi al 4%: HHI = 10 x 4^2 = 160, non 3.760
    s = _q2([("ZZETF", 60.0)] + [("ZZ%02d" % i, 4.0) for i in range(10)])
    assert s["metrics"]["hhi"] == pytest.approx(160.0)
    assert "1 veicoli esclusi" in _riga(s, "HHI")[0] and "soli nomi singoli" in _riga(s, "HHI")[0]


def test_pavimento_elevato_con_replay_oltre_il_budget():
    # riserva 4: book diversificato, vol e beta tranquille, ma replay -34,2% contro budget 25
    s = _q2([("ZZ%02d" % i, 1.0) for i in range(30)], vol=14.0, beta=0.6, perdita=-34.2)
    assert s["verdict"] == "RISCHIO ELEVATO" and "STRESS_BUDGET" in s["metrics"]["floors"], s
    assert any("Pavimento" in r[0] and "SFORATO" in r[1] for r in s["info"]), s["info"]
    sotto = _q2([("ZZ%02d" % i, 1.0) for i in range(30)], vol=14.0, beta=0.6, perdita=-24.5)
    assert "STRESS_BUDGET" not in sotto["metrics"]["floors"] and sotto["verdict"] != "RISCHIO ELEVATO"


def test_book_reale_del_pm_esce_almeno_elevato():
    # memo 07/10 (numeri dei memo: vol 20,82 contro target 20, replay -34,2% contro budget 25,
    # Dimson 1,05 riconciliata con 1,03, primo nome 14,6%, cassa 1,6%) usciva «RISCHIO MEDIO 35»
    pesi = [14.6] + [7.4, 5.9, 5.2, 5.1, 4.5, 4.5, 4.0, 4.0, 3.7, 3.6, 3.6, 3.2, 3.2, 3.0, 2.8, 2.8, 2.6,
                     2.5, 2.1, 2.0, 1.9, 1.9, 1.6, 1.6, 1.6, 1.0]
    pos = [("ZZ%02d" % i, w * 3034.77) for i, w in enumerate(pesi)]
    rb = {"verdict": "RECONCILED", "beta_per_decisioni": True, "betas": {"portfolio_risk_spy": 1.03}, "threshold": 0.35}
    s = _q2(pos, cassa=5049.0, vol=20.82, beta=1.03, dimson=1.05, perdita=-34.2, settori=_settori(28.0), rb=rb)
    assert s["verdict"] in ("RISCHIO ELEVATO", "RISCHIO CRITICO") and "STRESS_BUDGET" in s["metrics"]["floors"], s


def test_dimson_non_riconciliata_pesa_la_beta_riconciliata_e_lo_dice():
    # riserva 3 (regola PM 06/10, review C1): il guardrail riconcilia la sincrona; una Dimson
    # oltre la soglia (0,35) da quella NON pesa
    rb = {"verdict": "RECONCILED", "beta_per_decisioni": True, "betas": {"portfolio_risk_spy": 0.90}, "threshold": 0.35}
    s = _q2([("ZZ%02d" % i, 1.0) for i in range(30)], beta=0.90, dimson=1.50, rb=rb)
    riga = _riga(s, "Beta")
    assert "sincrona" in riga[0] and riga[1] == "0.90" and "Dimson non riconciliata (1.50 vs 0.90" in riga[0]
    assert s["metrics"]["beta_vs_spy"] == pytest.approx(0.90) and s["metrics"]["beta_dimson_status"] == "NOT_RECONCILED"
    entro = _q2([("ZZ%02d" % i, 1.0) for i in range(30)], beta=0.90, dimson=1.20, rb=rb)
    assert "Dimson" in _riga(entro, "Beta")[0] and entro["metrics"]["beta_dimson_status"] == "USED"


def test_ancore_del_primo_nome_in_multipli_del_cap_del_mandato():
    # riserva 11: cap 5 -> 10% NAV e' 2x il cap = 1 punto; senza cap ancore fisse DICHIARATE
    libro = [("ZZTOP", 10.0)] + [("ZZ%02d" % i, 90.0 / 30) for i in range(30)]
    con_cap = _q2(libro, mandato=_MAND_CAP)
    assert _riga(con_cap, "Primo nome")[2] == pytest.approx(1.0) and "cap 5%" in _riga(con_cap, "Primo nome")[0]
    senza = _q2(libro)
    assert _riga(senza, "Primo nome")[2] == pytest.approx(0.0) and "ancore fisse" in _riga(senza, "Primo nome")[0]


def test_cluster_sul_nav_ancore_dal_cap_e_pavimento():
    libro = [("ZZ%02d" % i, 1.0) for i in range(30)]
    # cap_settore 25: ancore 25/40/55/70 -> cluster 40% = 1 punto
    assert _riga(_q2(libro, settori=_settori(40.0), mandato=_MAND_CAP), "cluster")[2] == pytest.approx(1.0)
    # 80% dell'investito senza cassa: pavimento CLUSTER (banco: M11 sopravviveva)
    pieno = _q2(libro, settori=_settori(80.0), vol=12.0, beta=0.5, perdita=-5.0)
    assert pieno["metrics"]["concentration_floor"] == "CLUSTER" and pieno["verdict"] == "RISCHIO ELEVATO"
    # stesso cluster con meta' NAV in cassa = 40% del NAV: niente pavimento
    meta = _q2(libro, cassa=30.0, settori=_settori(80.0), vol=12.0, beta=0.5, perdita=-5.0)
    assert meta["metrics"]["top_cluster_pct"] == pytest.approx(40.0) and meta["metrics"]["concentration_floor"] is None


def test_paniere_multi_settore_non_e_un_cluster():
    # banco: M7 sopravviveva. «Multi-settore» all'80% e' diversificato: il primo cluster e' ZZTech
    sett = {"econ_axis": {"by_bucket": [{"bucket": "Multi-settore", "weight_pct": 80.0},
                                        {"bucket": "ZZTech", "weight_pct": 30.0}], "coverage_pct": 100.0}}
    s = _q2([("ZZ%02d" % i, 1.0) for i in range(30)], settori=sett)
    assert s["metrics"]["top_cluster"] == "ZZTech" and s["metrics"]["concentration_floor"] is None


def test_fx_incompleto_replay_sul_nav_nd_mai_valute_native_sommate():
    # riserva 10: 1.000 EUR + 85.000 GBX + 10.000 di cassa dava «-30,5% NAV»
    port = {"positions": [{"ticker": "ZZA", "valore_mercato_eur": 1000.0}, {"ticker": "ZZB.L", "valore_mercato": 85000.0}],
            "cash_disponibile_eur": 10000.0, "fx_incomplete": ["ZZB.L:GBX"]}
    with language_context("it"):
        s = S.quant_score(port, {"portfolio": {"vol_ewma_annual_pct": 15.0}}, mandato=_MAND, negozio=_NEG,
                          stress_data=_stress(-34.0))
    riga = _riga(s, "Replay")
    assert riga[2] is None and "FX incompleto" in riga[1] and s["metrics"]["stress_gfc_nav_pct"] is None


def test_valore_di_posizione_nd_non_vale_zero():
    # riserva 10: B senza valore contava 0 nell'investito (NAV piu' grande, replay piu' mite)
    port = {"positions": [{"ticker": "ZZA", "valore_mercato_eur": 1000.0}, {"ticker": "ZZB", "valore_mercato_eur": None}],
            "cash_disponibile_eur": 1000.0}
    with language_context("it"):
        s = S.quant_score(port, {"portfolio": {"vol_ewma_annual_pct": 15.0}}, mandato=_MAND, negozio=_NEG,
                          stress_data=_stress(-20.0))
    assert s["metrics"]["stress_gfc_nav_pct"] == pytest.approx(-20.0)        # base investito, la piu' severa
    assert "valore di mercato n.d. per ZZB" in _riga(s, "Replay")[0]
    assert _riga(s, "HHI")[2] is None and "mai contato come 0" in _riga(s, "HHI")[1]


def test_etichette_inglesi_senza_italiano():
    with language_context("en"):
        s = S.quant_score(_pos(_DIV), {"portfolio": {"vol_ewma_annual_pct": 15.0, "beta_vs_spy": 0.9,
                                                      "beta_vs_spy_dimson": 1.0}},
                          beta_reconcile=_OK, mandato=_MAND, negozio=_NEG, stress_data=_stress(-10.0))
    beta = [r for r in s["lines"] if "Beta" in r[0]][0]
    assert "(Dimson ±1d)" in beta[0] and "±1g" not in beta[0] and "investito" not in beta[0] and beta[1] == "1.00"
