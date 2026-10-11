# -*- coding: utf-8 -*-
"""Logica finanziaria degli score macro / crypto / politics / eventdesk (fix 09/10, v2 10/10, Opus 5.5).

Cosa deve essere vero per chi legge il cruscotto del memo:
  - MACRO: nelle crisi vere (novembre 2008, marzo-aprile 2020) lo score dice STRESS, non
    «neutrale»; il 2021 esce espansivo e il 2022 restrittivo. Ciclo e stress sono due
    sotto-indici e vince il piu' severo. Una riga senza dato, con data assente/futura o
    vecchia oltre il limite e' DICHIARATA (n.d./STALE) e sta fuori dal massimo; sotto la
    copertura minima non si emette un verdetto. v2: la curva appena dis-invertita vale
    almeno 2 punti; il decennale che si muove di 25/40/60 bp in un mese e' stress (in
    entrambe le direzioni); la regola di Sahm riceve la storia dalla dashboard; il CPI che
    punteggia e' il core (headline informativo).
  - CRYPTO: misura BTC/ETH/SOL pesati per open interest. Una memecoin con funding al 255%
    non cambia il verdetto; il funding al tasso base 10,95% e' neutro e uno negativo emerge.
    v2: il premio e' INFORMATIVO (il funding Hyperliquid e' la media oraria del premio):
    niente doppio conteggio; funding e premio istantaneo rilevanti e di segno opposto =
    «CRYPTO SEGNALI DISCORDI», mai EUFORICO/CAPITOLAZIONE. I casi usano combinazioni
    FISICAMENTE possibili (funding derivato dal premio medio con la formula del protocollo).
  - POLITICS: i mercati di pace/risoluzione non sono rischio di coda, ma una domanda di
    ESCALATION che contiene una parola di pace («peace talks collapse and war resume») si';
    l'unita' della probabilita' e' la frazione, il resto si rifiuta.
  - EVENTDESK: il volume di notizie restituite dai provider non e' rischio; le righe n.d.
    stanno fuori dal massimo; con meno di 2 righe di rischio non c'e' verdetto; il verdetto
    con il suffisso news sta nella colonna del PDF (<=195 pt).

Tutti gli input sono INVENTATI e stilizzati (ordini di grandezza di regime, non serie FRED);
i simboli delle altcoin sono sintetici.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import bellomberg.agents.specialist_scores as ss
from bellomberg.agents import agent_tools

OGGI = date(2031, 3, 17)
G = (OGGI - timedelta(days=1)).isoformat()   # osservazione giornaliera fresca
M = (OGGI - timedelta(days=60)).isoformat()  # osservazione mensile in vigore
ADESSO = datetime(2031, 3, 17, 12, tzinfo=timezone.utc)
Y10 = 4.4


def _dash(vix=None, curve=None, tips=None, cpi=None, hy=None, ig=None, unemp=None, delta10=None,
          cpi_head=None, min18=None, **date_override):
    """Payload con la forma di tool_get_macro_dashboard. `cpi` = CPI CORE (punteggiato),
    `cpi_head` = headline (informativo; di default uguale al core), `delta10` = variazione
    del decennale a 1 mese in bp (campo ref_1m), `min18` = minimo T10Y2Y 18 mesi in pp."""
    ind = {}
    if vix is not None:
        ind["vix_close"] = {"value": vix, "date": date_override.get("d_vix", G)}
    if hy is not None:
        ind["high_yield_spread"] = {"value": hy, "date": date_override.get("d_hy", G)}
    if ig is not None:
        ind["ig_credit_spread"] = {"value": ig, "date": date_override.get("d_ig", G)}
    if tips is not None:
        ind["real_10y_rate"] = {"value": tips, "date": date_override.get("d_tips", G)}
    if cpi is not None:
        ind["us_core_cpi_yoy"] = {"value": 330.1, "yoy_pct": cpi, "date": date_override.get("d_cpi", M)}
        ind["us_cpi_yoy"] = {"value": 321.5, "yoy_pct": cpi if cpi_head is None else cpi_head,
                             "date": date_override.get("d_cpi", M)}
    if unemp is not None:
        ind["us_unemployment"] = {"value": 4.4, "change_vs_prev": unemp, "date": date_override.get("d_unemp", M)}
    d = {"indicators": ind}
    if curve is not None or delta10 is not None:
        d10 = date_override.get("d_curve", G)
        ind["10y_treasury"] = {"value": Y10, "date": d10}
        if delta10 is not None:
            rif = date.fromisoformat(d10) - timedelta(days=date_override.get("gap10", 30))
            ind["10y_treasury"]["ref_1m"] = {"date": rif.isoformat(), "value": round(Y10 - delta10 / 100.0, 6)}
    if curve is not None:
        ind["2y_treasury"] = {"value": 3.9, "date": date_override.get("d_2y", G)}
        d["yield_curve_10y_2y_bps"] = curve
    if min18 is not None:
        ind["yield_curve_10y_2y"] = {"value": (curve or 0) / 100.0, "date": G,
                                     "min_18m": {"value": min18, "date": date_override.get("d_min", M),
                                                 "last_negative_date": (date_override.get("d_min", M)
                                                                        if min18 < 0 else None),
                                                 "window_from": (OGGI - timedelta(days=549)).isoformat(),
                                                 "window_to": G,
                                                 "copertura_completa": date_override.get("completa", True)}}
    return d


REGIMI = {
    # stilizzati: ordini di grandezza del regime (cpi = CPI core)
    "nov-2008": dict(vix=60, curve=220, tips=3.0, cpi=2.0, hy=16.0, ig=5.5, unemp=0.3, delta10=-85),
    "mar-2020": dict(vix=45, curve=50, tips=-0.4, cpi=2.1, hy=8.0, ig=3.0, unemp=10.3, delta10=-71),
    "2021": dict(vix=18, curve=140, tips=-0.9, cpi=4.2, hy=3.1, ig=0.9, unemp=-0.3, delta10=-10),
    "2022": dict(vix=28, curve=-40, tips=1.5, cpi=8.2, hy=5.0, ig=1.6, unemp=0.0, delta10=40),
    "neutro": dict(vix=17, curve=40, tips=0.8, cpi=2.9, hy=3.8, ig=1.2, unemp=0.1, delta10=5),
}


def _riga(s, frammento):
    righe = [r for r in s["lines"] if frammento in str(r[0])]
    assert len(righe) == 1, s["lines"]
    return righe[0]


# ----------------------------------------------------------------- MACRO: regimi

@pytest.mark.parametrize("regime,verdetto,governa", [
    ("nov-2008", "STRESS ACUTO", "stress"),
    ("mar-2020", "STRESS ACUTO", "stress"),
    ("2021", "REGIME ESPANSIVO", "ciclo"),
    ("2022", "REGIME RESTRITTIVO", "ciclo"),
    ("neutro", "REGIME NEUTRALE", "ciclo"),
])
def test_macro_i_regimi_storici_escono_col_verdetto_giusto(regime, verdetto, governa):
    s = ss.macro_score(_dash(**REGIMI[regime]), oggi=OGGI)
    assert s["verdict"].startswith(verdetto), s["verdict"]
    assert s["metrics"]["governa"] == governa
    sub = s["metrics"]["sottoindici"][governa]
    # il punteggio in testa (indice e colore nel PDF) e' quello del sotto-indice che governa
    assert (s["score"], s["max_score"]) == (sub["score"], sub["max_score"])
    # entrambi i sotto-indici sono in chiaro nel verdetto
    assert "[C " in s["verdict"] and " / S " in s["verdict"]


def test_macro_crisi_2008_stress_al_massimo_e_ciclo_non_lo_annulla():
    s = ss.macro_score(_dash(**REGIMI["nov-2008"]), oggi=OGGI)
    st = s["metrics"]["sottoindici"]["stress"]
    assert st["score"] == st["max_score"] == 12
    # la curva ripida (+220) nel ciclo NON abbassa il verdetto: verdetto = il piu' severo
    assert _riga(s, "Curva")[2] == 0


def test_macro_2022_tasso_reale_ex_ante_non_si_compensa_col_cpi():
    # prima: real FF ex-post = FF - CPI = -5 -> 0 punti «accomodante» nell'anno dei rialzi
    s = ss.macro_score(_dash(**REGIMI["2022"]), oggi=OGGI)
    assert _riga(s, "TIPS")[2] == 2 and _riga(s, "CPI core")[2] == 3


# ------------------------------------------------------ MACRO: n.d. / STALE / copertura

def test_macro_riga_stale_dichiarata_e_fuori_dal_massimo():
    vecchia = (OGGI - timedelta(days=7)).isoformat()   # limite giornaliere 6
    s = ss.macro_score(_dash(**REGIMI["neutro"], d_vix=vecchia), oggi=OGGI)
    lab, val, pt = _riga(s, "VIX")
    assert pt is None and str(val).startswith("STALE") and lab in s["unscored"]
    assert s["metrics"]["sottoindici"]["stress"]["max_score"] == 9     # 3 righe misurate, non 4
    assert lab in s["metrics"]["stale"]


@pytest.mark.parametrize("data_vix,motivo", [("", "assente"), ("2031-13-40", "invalida"),
                                             ("2031-03-18", "futura")])
def test_macro_data_non_verificabile_e_nd_non_punteggiata(data_vix, motivo):
    d = _dash(**REGIMI["neutro"])
    d["indicators"]["vix_close"]["date"] = data_vix
    s = ss.macro_score(d, oggi=OGGI)
    lab, val, pt = _riga(s, "VIX")
    assert pt is None and motivo in str(val) and lab in s["unscored"]


def test_macro_limite_mensile_80_giorni_al_confine():
    # v2: 80 giorni dal calendario BLS (CPI di agosto, datato 1/8, in vigore fino al 14/10 = 74)
    d80 = (OGGI - timedelta(days=80)).isoformat()
    d81 = (OGGI - timedelta(days=81)).isoformat()
    assert _riga(ss.macro_score(_dash(**REGIMI["neutro"], d_cpi=d80), oggi=OGGI), "CPI core")[2] == 1
    assert _riga(ss.macro_score(_dash(**REGIMI["neutro"], d_cpi=d81), oggi=OGGI), "CPI core")[2] is None
    assert ss._MACRO_ETA_MENSILE == 80


def test_macro_cpi_di_agosto_in_vigore_a_meta_ottobre_non_e_stale():
    s = ss.macro_score(_dash(**REGIMI["neutro"], d_cpi="2026-08-01", d_vix="2026-10-13", d_hy="2026-10-13",
                             d_ig="2026-10-13", d_tips="2026-10-13", d_curve="2026-10-13", d_2y="2026-10-13",
                             d_unemp="2026-09-01"), oggi=date(2026, 10, 14))
    assert _riga(s, "CPI core")[2] == 1


def test_macro_solo_vix_non_basta_per_un_verdetto():
    # prima: VIX 35 da solo = 3/3 = 100/100 «RISK-OFF / RECESSIVO»
    s = ss.macro_score(_dash(vix=35), oggi=OGGI)
    assert s["score"] is None and s["max_score"] is None
    assert s["verdict"].startswith("n.d.: copertura insufficiente"), s["verdict"]
    # le 7 righe attese e assenti sono dichiarate, non sparite (+ l'headline informativo)
    assert len(s["unscored"]) == 7 and len(s["lines"]) == 9


def test_macro_stress_con_2_righe_su_4_non_emette_verdetto():
    # v2: 4 righe di stress, maggioranza = 3. VIX e HY senza IG e senza decennale = n.d.
    s = ss.macro_score(_dash(**dict(REGIMI["nov-2008"], ig=None, delta10=None)), oggi=OGGI)
    st = s["metrics"]["sottoindici"]["stress"]
    assert st["misurate"] == 2 and st["score"] is None
    assert s["metrics"]["governa"] == "ciclo"


def test_macro_tips_assente_dichiarato_senza_ripiego_sul_real_fed_funds():
    d = _dash(**dict(REGIMI["2022"], tips=None))
    d["real_fed_funds_pct"] = -5.1
    s = ss.macro_score(d, oggi=OGGI)
    lab, val, pt = _riga(s, "TIPS")
    assert pt is None and "DFII10" in str(val) and "NON lo sostituisce" in str(val)
    assert s["metrics"]["sottoindici"]["ciclo"]["max_score"] == 9


def test_macro_nessuna_metrica_presente_resta_none():
    assert ss.macro_score({"indicators": {}}, oggi=OGGI) is None


def test_macro_massimo_raggiungibile_uguale_al_dichiarato():
    peggio = dict(vix=80, curve=-200, tips=4.0, cpi=9.0, hy=20.0, ig=6.0, unemp=1.0, delta10=100)
    s = ss.macro_score(_dash(**peggio), oggi=OGGI)
    for nome in ("ciclo", "stress"):
        sub = s["metrics"]["sottoindici"][nome]
        assert sub["score"] == sub["max_score"] == 12, (nome, sub)
    assert s["score"] == s["max_score"]


@pytest.mark.parametrize("metrica,sotto,punti", [
    # 10/10: VIX calibrato sui percentili 60/80/95 di FRED VIXCLS dal 1997 (core/soglie_score)
    ("vix", 20.49, 0), ("vix", 20.5, 1), ("vix", 24.99, 1), ("vix", 25, 2), ("vix", 34.09, 2), ("vix", 34.1, 3),
    ("hy", 3.99, 0), ("hy", 4, 1), ("hy", 5.5, 2), ("hy", 7, 3),
    ("ig", 1.29, 0), ("ig", 1.3, 1), ("ig", 1.7, 2), ("ig", 2.5, 3),
    ("curve", 75, 0), ("curve", 74, 1), ("curve", 24, 2), ("curve", -1, 3),
    # v2 (decisione PM): r* 0,8-1,3% + term premium -> neutrale ~1-1,5%
    ("tips", 0.49, 0), ("tips", 0.5, 1), ("tips", 1.49, 1), ("tips", 1.5, 2), ("tips", 2.49, 2), ("tips", 2.5, 3),
    ("cpi", 2.49, 0), ("cpi", 2.5, 1), ("cpi", 3.5, 2), ("cpi", 5, 3),
    ("unemp", 0.1, 0), ("unemp", 0.2, 1), ("unemp", 0.3, 2), ("unemp", 0.5, 3),
    # v2: decennale a 1 mese, simmetrico
    ("delta10", 24.9, 0), ("delta10", 25, 1), ("delta10", 39.9, 1), ("delta10", 40, 2), ("delta10", 60, 3),
    ("delta10", -24.9, 0), ("delta10", -25, 1), ("delta10", -40, 2), ("delta10", -60, 3),
])
def test_macro_confini_delle_bande(metrica, sotto, punti):
    base = dict(REGIMI["neutro"])
    base[metrica] = sotto
    s = ss.macro_score(_dash(**base), oggi=OGGI)
    frammento = {"vix": "VIX", "hy": "HY", "ig": "IG", "curve": "Curva", "tips": "TIPS",
                 "cpi": "CPI core", "unemp": "Disoccup", "delta10": "Decennale"}[metrica]
    assert _riga(s, frammento)[2] == punti


# ------------------------------------------------------ MACRO: CPI core / headline (v2)

def test_macro_il_punteggio_usa_il_core_e_l_headline_e_informativo():
    # luglio 2008 stilizzato: headline 5,6% (energia), core 2,4%
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], cpi=2.4, cpi_head=5.6)), oggi=OGGI)
    assert _riga(s, "CPI core")[2] == 0
    lab, val, pt = _riga(s, "headline")
    assert pt is None and lab in s["excluded"] and lab not in s["unscored"] and "5.6%" in val
    assert s["metrics"]["cpi_yoy"] == 5.6 and s["metrics"]["core_cpi_yoy"] == 2.4


def test_macro_core_assente_dichiarato_e_l_headline_non_lo_sostituisce():
    d = _dash(**REGIMI["neutro"])
    del d["indicators"]["us_core_cpi_yoy"]
    s = ss.macro_score(d, oggi=OGGI)
    lab, val, pt = _riga(s, "CPI core")
    assert pt is None and lab in s["unscored"] and "CPILFESL" in val and "headline NON" in val
    assert s["metrics"]["sottoindici"]["ciclo"]["max_score"] == 9


# ------------------------------------------------------ MACRO: dis-inversione (v2)

def test_macro_curva_dis_invertita_vale_almeno_2_punti():
    # curva +80 (prima = 0 punti «inizio ciclo») ma minimo -0,50 pp nei 18 mesi
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], curve=80, min18=-0.50)), oggi=OGGI)
    lab, val, pt = _riga(s, "Curva")
    assert pt == 2 and "dis-inversione" in val
    assert s["metrics"]["curve_disinversione"] is True and s["metrics"]["curve_min_18m_bps"] == -50


def test_macro_curva_mai_invertita_resta_a_zero_e_lo_dice():
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], curve=80, min18=0.30)), oggi=OGGI)
    lab, val, pt = _riga(s, "Curva")
    assert pt == 0 and "minimo 18 mesi +30" in val and "dis-inversione" not in val


def test_macro_curva_ancora_invertita_resta_3():
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], curve=-10, min18=-0.8)), oggi=OGGI)
    assert _riga(s, "Curva")[2] == 3 and s["metrics"]["curve_disinversione"] is False


def test_macro_senza_storia_della_curva_la_dis_inversione_e_dichiarata_nd():
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], curve=80)), oggi=OGGI)
    lab, val, pt = _riga(s, "Curva")
    assert pt == 0 and "dis-inversione n.d." in val and "T10Y2Y" in val


def test_macro_storia_parziale_senza_inversione_non_verificabile_con_inversione_conta():
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], curve=80, min18=0.30, completa=False)), oggi=OGGI)
    assert "non verificabile" in _riga(s, "Curva")[1] and _riga(s, "Curva")[2] == 0
    # un minimo negativo trovato anche in una storia parziale E' una prova di inversione
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], curve=80, min18=-0.30, completa=False)), oggi=OGGI)
    assert _riga(s, "Curva")[2] == 2


def test_macro_minimo_fuori_finestra_non_conta():
    vecchio = (OGGI - timedelta(days=600)).isoformat()
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], curve=80, min18=-0.5, d_min=vecchio)), oggi=OGGI)
    assert _riga(s, "Curva")[2] == 0 and "fuori dalla finestra" in _riga(s, "Curva")[1]


# ------------------------------------------------------ MACRO: decennale a 1 mese (v2)

def test_macro_decennale_riferimento_assente_dichiarato_nd():
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], delta10=None)), oggi=OGGI)
    lab, val, pt = _riga(s, "Decennale")
    assert pt is None and lab in s["unscored"] and "riferimento a 1 mese assente" in val


@pytest.mark.parametrize("gap", [20, 45])
def test_macro_decennale_riferimento_fuori_finestra_nd(gap):
    s = ss.macro_score(_dash(**dict(REGIMI["neutro"], delta10=50), gap10=gap), oggi=OGGI)
    lab, val, pt = _riga(s, "Decennale")
    assert pt is None and ("riferimento a %d giorni" % gap) in val


def test_macro_memo73_il_rialzo_del_decennale_entra_nello_stress():
    # ottobre 2026 (memo 73): 10y 5,31, 2y 4,84, +53 bp in un mese, VIX 15
    d = _dash(vix=15.01, curve=47, tips=2.9, cpi=2.45, cpi_head=3.4, hy=3.3, ig=0.9, unemp=0.0, delta10=53)
    s = ss.macro_score(d, oggi=OGGI)
    assert _riga(s, "Decennale")[2] == 2
    assert s["metrics"]["sottoindici"]["stress"]["indice"] == 17


# ------------------------------------------------------ MACRO: bear steepening (10/10)

def _dash_tassi(y10, y10_rif, y2, y2_rif, gap=30, gap2=None, **altri):
    """Payload con 10y e 2y VERI (livello oggi e a 1 mese): curva = (10y - 2y) in bp, come
    la calcola tool_get_macro_dashboard. `altri` = le altre righe del ciclo/stress."""
    d = _dash(**altri)
    rif = (date.fromisoformat(G) - timedelta(days=gap)).isoformat()
    rif2 = (date.fromisoformat(G) - timedelta(days=gap if gap2 is None else gap2)).isoformat()
    d["indicators"]["10y_treasury"] = {"value": y10, "date": G}
    d["indicators"]["2y_treasury"] = {"value": y2, "date": G}
    if y10_rif is not None:
        d["indicators"]["10y_treasury"]["ref_1m"] = {"date": rif, "value": y10_rif}
    if y2_rif is not None:
        d["indicators"]["2y_treasury"]["ref_1m"] = {"date": rif2, "value": y2_rif}
    d["yield_curve_10y_2y_bps"] = round((y10 - y2) * 100, 0)
    return d


MEMO73_ALTRI = dict(vix=15.01, tips=2.9, cpi=2.45, cpi_head=3.4, hy=3.3, ig=0.9, unemp=0.0)


def test_macro_memo73_il_rialzo_del_decennale_non_fa_scendere_il_ciclo():
    # prima del rialzo: 10y 5,31, 2y 4,84 -> curva 47 bp (1 punto), decennale fermo
    prima = ss.macro_score(_dash_tassi(5.31, 5.31, 4.84, 4.84, **MEMO73_ALTRI), oggi=OGGI)
    # dopo: 10y 5,75 (+44 bp in un mese), 2y fermo -> curva 91 bp: senza la regola 0 punti
    dopo = ss.macro_score(_dash_tassi(5.75, 5.31, 4.84, 4.84, **MEMO73_ALTRI), oggi=OGGI)
    assert _riga(prima, "Curva")[2] == 1
    lab, val, pt = _riga(dopo, "Curva")
    assert pt == 3, val
    assert "curva ripida per rialzo dei tassi lunghi (bear steepening)" in val and "+44" in val
    cp, cd = prima["metrics"]["sottoindici"]["ciclo"], dopo["metrics"]["sottoindici"]["ciclo"]
    assert cd["score"] >= cp["score"] and cd["indice"] >= cp["indice"], (cp, cd)
    assert dopo["metrics"]["curve_bear_steepening"] is True
    # la riga STRESS «decennale a 1 mese» resta
    assert _riga(dopo, "Decennale")[2] == 2


@pytest.mark.parametrize("y10,atteso", [
    (5.55, 1),   # +24 bp: sotto soglia, vale il livello (curva 71 bp = 1)
    (5.56, 2),   # +25 bp: bear steepening, curva 72 bp -> almeno 2
    (5.70, 2),   # +39 bp: curva 86 bp (livello 0) -> 2
    (5.71, 3),   # +40 bp: forte -> 3
])
def test_macro_bear_steepening_confini_25_e_40(y10, atteso):
    s = ss.macro_score(_dash_tassi(y10, 5.31, 4.84, 4.84, **MEMO73_ALTRI), oggi=OGGI)
    assert _riga(s, "Curva")[2] == atteso, _riga(s, "Curva")


def test_macro_bull_steepening_resta_come_prima():
    # curva ripida perche' SCENDE il 2 anni (taglio Fed): 10y fermo, 2y -60 bp -> curva 100 = 0
    s = ss.macro_score(_dash_tassi(4.40, 4.40, 3.40, 4.00, **MEMO73_ALTRI), oggi=OGGI)
    lab, val, pt = _riga(s, "Curva")
    assert pt == 0 and "bear steepening" not in val and s["metrics"]["curve_bear_steepening"] is False
    # bull steepening col decennale in calo: idem
    s = ss.macro_score(_dash_tassi(4.10, 4.40, 3.00, 3.80, **MEMO73_ALTRI), oggi=OGGI)
    assert _riga(s, "Curva")[2] == 0


def test_macro_rialzo_del_decennale_con_curva_che_si_appiattisce_non_e_bear_steepening():
    # 10y +30 bp ma 2y +50 bp (bear flattening): la curva scende, nessun rialzo di punti
    s = ss.macro_score(_dash_tassi(5.00, 4.70, 4.00, 3.50, **MEMO73_ALTRI), oggi=OGGI)
    assert _riga(s, "Curva")[2] == 0 and s["metrics"]["curve_bear_steepening"] is False


@pytest.mark.parametrize("y10_rif,y2_rif,gap2,frammento", [
    (None, 4.84, None, "variazione a 1 mese del decennale n.d."),
    (5.31, None, None, "variazione a 1 mese del 2 anni n.d."),
    (5.31, 4.84, 33, "DGS10 e DGS2 su date diverse"),
])
def test_macro_bear_steepening_non_decidibile_riga_nd(y10_rif, y2_rif, gap2, frammento):
    s = ss.macro_score(_dash_tassi(5.75, y10_rif, 4.84, y2_rif, gap2=gap2, **MEMO73_ALTRI), oggi=OGGI)
    lab, val, pt = _riga(s, "Curva")
    assert pt is None and lab in s["unscored"] and frammento in val, val
    assert "bear steepening non verificabile" in val


def test_macro_decennale_sotto_soglia_basta_senza_il_2_anni():
    # +10 bp: il bear steepening e' escluso anche senza la storia del 2 anni
    s = ss.macro_score(_dash_tassi(5.41, 5.31, 4.84, None, **MEMO73_ALTRI), oggi=OGGI)
    assert _riga(s, "Curva")[2] == 1


def test_macro_curva_invertita_non_ha_bisogno_del_bear_steepening():
    s = ss.macro_score(_dash_tassi(4.00, None, 4.20, None, **MEMO73_ALTRI), oggi=OGGI)
    lab, val, pt = _riga(s, "Curva")
    assert pt == 3 and "bear steepening n.d." in val


def test_macro_bear_steepening_in_inglese():
    from bellomberg.core.language import language_context
    with language_context("en"):
        s = ss.macro_score(_dash_tassi(5.75, 5.31, 4.84, 4.84, **MEMO73_ALTRI), oggi=OGGI)
    assert "steep curve from rising long rates (bear steepening)" in _riga(s, "curve")[1]


# ------------------------------------------------------ MACRO: governo del verdetto

def test_macro_parita_esatta_governa_lo_stress():
    # ciclo 6/12 (curva -40, TIPS 1,5, core 2,9) e stress 6/12 (VIX 25, HY 5,5, IG 1,7)
    s = ss.macro_score(_dash(vix=25, hy=5.5, ig=1.7, delta10=0, curve=-40, tips=1.5, cpi=2.9, unemp=0.0),
                       oggi=OGGI)
    so = s["metrics"]["sottoindici"]
    assert so["ciclo"]["score"] == so["stress"]["score"] == 6
    assert s["metrics"]["governa"] == "stress" and s["verdict"].startswith("STRESS ELEVATO")


def test_macro_tutto_in_banda_bassa_l_etichetta_e_quella_del_ciclo():
    # stress 1/12 (frazione piu' alta) e ciclo 0/12: entrambi banda bassa -> «ESPANSIVO»
    # (10/10: VIX 21 sopra la prima soglia calibrata 20,5)
    s = ss.macro_score(_dash(vix=21, hy=3, ig=1, delta10=0, curve=140, tips=-0.9, cpi=2.0, unemp=0.0),
                       oggi=OGGI)
    assert s["metrics"]["sottoindici"]["stress"]["score"] == 1
    assert s["metrics"]["governa"] == "ciclo" and s["verdict"].startswith("REGIME ESPANSIVO")


def test_macro_eta_della_curva_e_quella_della_componente_piu_vecchia():
    vecchia = (OGGI - timedelta(days=9)).isoformat()
    for k in ("d_curve", "d_2y"):   # 10y fresco e 2y vecchio, poi il contrario
        s = ss.macro_score(_dash(**REGIMI["neutro"], **{k: vecchia}), oggi=OGGI)
        lab, val, pt = _riga(s, "Curva")
        assert pt is None and str(val).startswith("STALE") and vecchia in str(val), (k, val)


def test_macro_nd_tradotto_in_inglese():
    from bellomberg.core.language import language_context
    with language_context("en"):
        s = ss.macro_score(_dash(curve=40, tips=0.8, cpi=2.9, unemp=0.1, vix=17), oggi=OGGI)
    assert "S n/a" in s["verdict"] and "n.d." not in s["verdict"], s["verdict"]


# ------------------------------------------------------ MACRO: regola di Sahm

def _storia(valori, fine=date(2031, 3, 1), salta=()):
    """Storia mensile che FINISCE a `fine` (primo del mese); `salta` = mesi da togliere."""
    out = []
    y, m = fine.year, fine.month
    for v in reversed(valori):
        out.append({"date": "%04d-%02d-01" % (y, m), "value": v})
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    out.reverse()
    return [o for o in out if o["date"] not in salta]


def test_macro_regola_di_sahm_con_la_storia():
    d = _dash(**REGIMI["neutro"], d_unemp="2031-03-01")
    # 12 mesi a 4,0 poi 4,5/4,6/4,7: media 3m 4,6 - minimo 4,0 = +0,6 -> segnale (3 punti)
    d["indicators"]["us_unemployment"]["history"] = _storia([4.0] * 12 + [4.5, 4.6, 4.7])
    s = ss.macro_score(d, oggi=OGGI)
    lab, val, pt = _riga(s, "Sahm")
    assert pt == 3 and s["metrics"]["sahm"] == pytest.approx(0.6)
    assert "proxy" not in lab


def test_macro_sahm_storia_ferma_prima_del_dato_corrente_e_proxy_dichiarato():
    d = _dash(**REGIMI["neutro"], d_unemp="2031-03-01")
    d["indicators"]["us_unemployment"]["history"] = _storia([4.0] * 15, fine=date(2031, 2, 1))
    s = ss.macro_score(d, oggi=OGGI)
    lab, val, _ = _riga(s, "Disoccup")
    assert "proxy" in lab and "storia ferma al 2031-02-01" in val


def test_macro_sahm_mese_mancante_calcolato_e_dichiarato():
    d = _dash(**REGIMI["neutro"], d_unemp="2031-03-01")
    d["indicators"]["us_unemployment"]["history"] = _storia([4.0] * 17, salta=("2030-10-01",))
    s = ss.macro_score(d, oggi=OGGI)
    lab, val, pt = _riga(s, "Sahm")
    assert pt == 0 and "1 mesi mancanti" in val


def test_macro_senza_storia_la_disoccupazione_e_un_proxy_dichiarato():
    s = ss.macro_score(_dash(**REGIMI["neutro"]), oggi=OGGI)
    lab, val, _ = _riga(s, "Disoccup")
    assert "proxy" in lab and "Sahm n.d." in lab and "storia mensile assente" in val
    assert s["metrics"]["sahm"] is None


# ------------------------------------------------------ DASHBOARD -> score (cablaggio v2)

def _fred_finto(serie_per_id):
    def fetch(series_id, last_n=24):
        obs = serie_per_id.get(series_id)
        if obs is None:
            return {"error": "serie sintetica assente: %s" % series_id}
        return {"series_id": series_id, "observations": obs[-last_n:], "rejected_observations": []}
    return fetch


def _giornaliere(fine, giorni, valore_fn):
    out = []
    for i in range(giorni, -1, -1):
        d = fine - timedelta(days=i)
        if d.weekday() < 5:
            out.append({"date": d.isoformat(), "value": valore_fn(d)})
    return out


def test_dashboard_porta_la_storia_che_serve_allo_score(monkeypatch):
    fine = date(2031, 3, 14)   # venerdi'
    un_mese = fine - timedelta(days=30)
    serie = {
        "UNRATE": _storia([4.0] * 12 + [4.5, 4.6, 4.7] + [4.7] * 9, fine=date(2031, 2, 1))[-24:],
        # T10Y2Y invertita fino a un anno fa, poi positiva
        "T10Y2Y": _giornaliere(fine, 700, lambda d: -0.6 if d < fine - timedelta(days=365) else 0.8),
        # decennale: +44 bp nell'ultimo mese
        "DGS10": _giornaliere(fine, 80, lambda d: 5.31 if d <= un_mese else 5.75),
        "DGS2": _giornaliere(fine, 30, lambda d: 0.0),   # 2 anni a ZERO: la curva NON deve sparire
    }
    monkeypatch.setattr(agent_tools, "_fred_fetch_series", _fred_finto(serie))
    monkeypatch.setattr(agent_tools, "_NATIVE_INDICATORS", {})
    dash = agent_tools.tool_get_macro_dashboard()
    ind = dash["indicators"]
    assert len(ind["us_unemployment"]["history"]) == 24
    m = ind["yield_curve_10y_2y"]["min_18m"]
    assert m["value"] == -0.6 and m["copertura_completa"] is True and m["window_to"] == fine.isoformat()
    # l'ultimo negativo (non la data del minimo, in fondo alla finestra) prova l'inversione
    assert m["last_negative_date"] < (fine - timedelta(days=364)).isoformat() < m["window_to"]
    assert ind["10y_treasury"]["ref_1m"]["value"] == 5.31
    assert dash["yield_curve_10y_2y_bps"] == 575   # 5,75 - 0,0: prima `if y10 and y2` la cancellava
    # nessun campo interno trapela nella dashboard compatta
    assert all("_observations" not in v for v in ind.values() if isinstance(v, dict))
    # cablaggio: lo score legge quei campi
    s = ss.macro_score(dash, oggi=date(2031, 3, 16))
    assert _riga(s, "Decennale")[2] == 2 and "+44 bps" in _riga(s, "Decennale")[1]
    # dis-inversione (almeno 2) E bear steepening forte (+44 bp col 2 anni fermo: almeno 3)
    assert _riga(s, "Curva")[2] == 3 and s["metrics"]["curve_disinversione"] is True
    assert "bear steepening" in _riga(s, "Curva")[1] and s["metrics"]["delta_2y_1m_bps"] == 0
    assert ind["2y_treasury"]["ref_1m"]["value"] == 0.0
    # storia: 12 mesi a 4,0, poi 4,5/4,6/4,7 e 4,7 fino all'ultimo: media 3m 4,7 - minimo 4,0
    assert "proxy" not in _riga(s, "Disoccup")[0] and s["metrics"]["sahm"] == pytest.approx(0.7)


def test_dashboard_storia_corta_dichiarata(monkeypatch):
    fine = date(2031, 3, 14)
    serie = {"T10Y2Y": _giornaliere(fine, 100, lambda d: 0.5),
             "DGS10": _giornaliere(fine, 10, lambda d: 4.0)}
    monkeypatch.setattr(agent_tools, "_fred_fetch_series", _fred_finto(serie))
    monkeypatch.setattr(agent_tools, "_NATIVE_INDICATORS", {})
    ind = agent_tools.tool_get_macro_dashboard()["indicators"]
    assert ind["yield_curve_10y_2y"]["min_18m"]["copertura_completa"] is False
    assert "ref_1m" not in ind["10y_treasury"] and "30 giorni" in ind["10y_treasury"]["ref_1m_error"]


# ------------------------------------------------------------------- CRYPTO

def _r(a, f, p, oi):
    return {"asset": a, "funding_annualized_pct": f, "premium_basis_pts": p, "oi_usd_m": oi}


def _funding_da_premio(premio_medio_bp):
    """Formula Hyperliquid: F_8h = P + clamp(I - P, -5 bp, +5 bp), I = 1 bp; annualizzato."""
    f8 = premio_medio_bp + max(-5.0, min(5.0, 1.0 - premio_medio_bp))
    return f8 * 10.95   # 1 bp ogni 8 ore = 10,95%/anno


def _fis(a, premio_medio_bp, oi, istantaneo_bp=None):
    """Riga FISICAMENTE possibile: funding dalla media oraria del premio, premio istantaneo
    uguale alla media salvo indicazione (un istante puo' divergere dalla media dell'ora)."""
    return _r(a, _funding_da_premio(premio_medio_bp),
              premio_medio_bp if istantaneo_bp is None else istantaneo_bp, oi)


def _intel(major, altro=(), ts="2031-03-17T11:00:00+00:00"):
    righe = list(major) + list(altro)
    out = {"top_10_perps_by_oi": righe,
           "highest_funding_long_pressure": sorted(righe, key=lambda x: -x["funding_annualized_pct"])[:5]}
    if ts is not None:
        out["fetched_at_utc"] = ts
    return out


MAJOR_BASE = [_r("BTC", 10.95, 1, 3000), _r("ETH", 10.95, 0.5, 1500), _r("SOL", 10.95, 1, 400)]


def test_formula_di_riferimento_del_funding():
    assert _funding_da_premio(0) == pytest.approx(10.95)
    assert _funding_da_premio(-4) == pytest.approx(10.95) and _funding_da_premio(6) == pytest.approx(10.95)
    assert _funding_da_premio(10) == pytest.approx(5 * 10.95)


def test_crypto_major_al_tasso_base_e_neutro():
    s = ss.crypto_score(_intel(MAJOR_BASE), adesso=ADESSO)
    assert s["score"] == 0 and s["verdict"] == "CRYPTO CALMO"
    assert s["metrics"]["funding_scarto_base_pp"] == pytest.approx(0)


@pytest.mark.parametrize("premio", [-4.0, -2.0, 0.0, 3.0, 6.0])
def test_crypto_zona_neutra_del_premio_e_calma(premio):
    s = ss.crypto_score(_intel([_fis(a, premio, oi) for a, oi in (("BTC", 3000), ("ETH", 1500))]),
                        adesso=ADESSO)
    assert s["verdict"] == "CRYPTO CALMO" and s["metrics"]["premio_scarto_zona_bp"] == 0


def test_crypto_la_microcap_estrema_non_decide_il_verdetto():
    # memo reali: una memecoin da ~2 M$ di OI al 255% dava «EUFORICO 100/100»
    s = ss.crypto_score(_intel(MAJOR_BASE, [_r("ZQMEME", 255.0, 45, 2.2)]), adesso=ADESSO)
    assert s["score"] == 0 and s["verdict"] == "CRYPTO CALMO"
    assert "ZQMEME" not in s["metrics"]["major_usati"]


def test_crypto_il_premio_e_informativo_niente_doppio_conteggio():
    # premio medio +10 bp -> funding 54,75%/anno (scarto +43,8): v1 = 3 + 1 punti di premio
    s = ss.crypto_score(_intel([_fis("BTC", 10, 3000), _fis("ETH", 10, 1500)]), adesso=ADESSO)
    # 10/10: scarto 43,8 fra p95 e p99 -> 2,73 punti, banda EUFORICO (>= 2,25)
    assert s["score"] == pytest.approx(2.73, abs=0.01) and s["max_score"] == 3
    assert s["verdict"] == "CRYPTO EUFORICO"
    lab, val, pt = _riga(s, "Premio")
    assert pt is None and lab in s["excluded"] and lab not in s["unscored"]


def test_crypto_premio_negativo_e_short_non_surriscaldato():
    # premio medio -6 bp -> funding -10,95%/anno (scarto -21,9) -> 2 punti, lato short (10/10:
    # fra i percentili 80 e 95 del calibrato, banda «sotto pressione»)
    s = ss.crypto_score(_intel([_fis(a, -6, oi) for a, oi in (("BTC", 3000), ("ETH", 1500), ("SOL", 400))]),
                        adesso=ADESSO)
    assert s["metrics"]["direzione"] == "short"
    assert "SURRISCALDATO" not in s["verdict"] and "EUFORICO" not in s["verdict"]
    assert s["verdict"] == "CRYPTO SOTTO PRESSIONE (short)"


def test_crypto_premio_positivo_resta_froth():
    # premio medio +8 bp -> funding 32,85%/anno (scarto +21,9) -> 2 punti, lato long
    s = ss.crypto_score(_intel([_fis(a, 8, oi) for a, oi in (("BTC", 3000), ("ETH", 1500), ("SOL", 400))]),
                        adesso=ADESSO)
    assert s["metrics"]["direzione"] == "long" and s["verdict"] == "CRYPTO SURRISCALDATO"


@pytest.mark.parametrize("medio,istantaneo", [(10.5, -45.0), (-7.7, 45.0), (10.5, -6.0), (-7.7, 8.0)])
def test_crypto_funding_e_premio_di_segno_opposto_sono_segnali_discordi(medio, istantaneo):
    # funding +60%/anno con premio istantaneo -45 bp (flash crash dentro l'ora) e funding
    # -30%/anno con premio +45 bp (squeeze): la media dell'ora e l'istante si contraddicono
    s = ss.crypto_score(_intel([_fis("BTC", medio, 3000, istantaneo), _fis("ETH", medio, 1500, istantaneo)]),
                        adesso=ADESSO)
    assert s["verdict"] == "CRYPTO SEGNALI DISCORDI", s["verdict"]
    assert s["metrics"]["segnali_discordi"] is True
    assert "EUFORICO" not in s["verdict"] and "CAPITOLAZIONE" not in s["verdict"]


def test_crypto_premio_appena_fuori_zona_non_e_discorde():
    # scarto -1 bp fuori zona (< 2): rumore, vince il funding
    s = ss.crypto_score(_intel([_fis("BTC", 10.5, 3000, -5.0), _fis("ETH", 10.5, 1500, -5.0)]), adesso=ADESSO)
    assert s["verdict"] == "CRYPTO EUFORICO" and s["metrics"]["segnali_discordi"] is False


def test_crypto_funding_negativo_di_un_major_emerge():
    # ETH a -9,84% con BTC al tasso base: prima invisibile (max dei funding PIU' ALTI)
    major = [_r("BTC", 10.95, 1, 3000), _r("ETH", -9.84, -3, 1500), _r("SOL", 10.95, 1, 400)]
    s = ss.crypto_score(_intel(major), adesso=ADESSO)
    atteso = (10.95 * 3000 - 9.84 * 1500 + 10.95 * 400) / 4900
    assert s["metrics"]["funding_ann_pct"] == pytest.approx(atteso)
    # scarto -6,36: fra il p50 (3,35 -> 0,75) e il p80 (11,7 -> 1,5) -> 1,02 punti, continuo
    assert _riga(s, "Funding")[2] == pytest.approx(1.02, abs=0.01) and s["metrics"]["direzione"] == "short"


def test_crypto_capitolazione_dei_major():
    major = [_fis("BTC", -50, 3000), _fis("ETH", -60, 1500), _fis("SOL", -70, 400)]
    s = ss.crypto_score(_intel(major), adesso=ADESSO)
    assert (s["score"], s["max_score"]) == (3, 3) and s["verdict"] == "CRYPTO CAPITOLAZIONE"


# 10/10: ancore CALIBRATE (percentili 50/80/95/99 di |scarto| sugli ultimi 2 anni Hyperliquid)
# ai confini delle etichette (0,75 / 1,5 / 2,25 punti), saturazione a 3 al p99; punti CONTINUI
@pytest.mark.parametrize("scarto,punti", [(0, 0), (3.35, 0.75), (11.7, 1.5), (27, 2.25), (53.45, 3),
                                          (80, 3), (-3.35, 0.75), (-27, 2.25),
                                          ((3.35 + 11.7) / 2, 1.125)])
def test_crypto_confini_funding_relativi_al_tasso_base(scarto, punti):
    f = 10.95 + scarto
    s = ss.crypto_score(_intel([_r("BTC", f, 0, 1000), _r("ETH", f, 0, 500)]), adesso=ADESSO)
    assert _riga(s, "Funding")[2] == pytest.approx(punti, abs=0.01)


def test_crypto_niente_scoglio_un_centesimo_di_scarto_sposta_una_frazione():
    # il vecchio gradino a 15: 14,99 -> 1 punto, 15 -> 2 punti. Ora la differenza e' minima
    def p(scarto):
        f = 10.95 + scarto
        return _riga(ss.crypto_score(_intel([_r("BTC", f, 0, 1000), _r("ETH", f, 0, 500)]),
                                     adesso=ADESSO), "Funding")[2]
    for x in (4.99, 11.69, 14.99, 26.99):
        assert abs(p(x + 0.01) - p(x)) <= 0.02, x


def test_crypto_senza_eth_il_funding_e_nd_e_niente_verdetto():
    s = ss.crypto_score(_intel([_r("BTC", 40, 20, 3000)]), adesso=ADESSO)
    assert s["score"] is None and s["verdict"].startswith("n.d.")
    assert "ETH" in str(_riga(s, "Funding")[1])


def test_crypto_payload_stale_oltre_24_ore():
    s = ss.crypto_score(_intel(MAJOR_BASE, ts="2031-03-16T10:00:00+00:00"), adesso=ADESSO)
    assert s["score"] is None and s["verdict"].startswith("n.d.")
    assert "STALE" in str(_riga(s, "Funding")[1])


def test_crypto_osservazione_nel_futuro_rifiutata():
    s = ss.crypto_score(_intel(MAJOR_BASE, ts="2031-03-17T13:00:00+00:00"), adesso=ADESSO)
    assert s["score"] is None and s["verdict"].startswith("n.d.")
    lab, val, pt = _riga(s, "Funding")
    assert pt is None and "futura" in str(val) and lab in s["unscored"]


def test_crypto_timestamp_assente_e_un_avviso_dichiarato():
    s = ss.crypto_score(_intel(MAJOR_BASE, ts=None), adesso=ADESSO)
    lab, val, pt = _riga(s, "Osservazione")
    assert pt is None and "freschezza non verificabile" in str(val) and lab in s["excluded"]
    assert s["score"] == 0   # il dato live resta, l'eta' non verificabile e' dichiarata


def test_crypto_timestamp_senza_fuso_dichiarato_mai_typeerror():
    s = ss.crypto_score(_intel(MAJOR_BASE, ts="2031-03-17T11:00:00"), adesso=ADESSO)
    lab, val, pt = _riga(s, "Osservazione")
    assert pt is None and "timestamp assente o invalido" in str(val)
    assert s["metrics"]["eta_ore"] is None


def test_crypto_il_vecchio_input_top5_non_basta_piu():
    assert ss.crypto_score({"highest_funding_long_pressure": [_r("ZQMEME", 255.0, 45, 2.2)]}) is None


def test_tool_hyperliquid_dichiara_l_istante_di_osservazione(monkeypatch):
    import requests

    class _Risp:
        def raise_for_status(self):
            return None

        def json(self):
            ctx = {"markPx": "10", "prevDayPx": "9", "funding": "0.0000125", "dayNtlVlm": "1000",
                   "openInterest": "500", "premium": "0.0001"}
            return [{"universe": [{"name": "BTC"}]}, [ctx]]

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Risp())
    out = agent_tools.tool_get_hyperliquid_intel(focus_asset=None, builder_dexs=False)
    ts = datetime.fromisoformat(out["fetched_at_utc"])
    assert ts.tzinfo is not None and abs((datetime.now(timezone.utc) - ts).total_seconds()) < 120


def test_freshness_funding_al_tasso_base_ripetuto_non_e_fonte_ferma(monkeypatch, tmp_path):
    from bellomberg.core import freshness
    monkeypatch.setattr(freshness, "SNAP_PATH", str(tmp_path / "snap.json"))
    g1, g2 = date(2031, 3, 1), date(2031, 3, 17)
    for giorno in (g1, g2):   # stesso 10,95 a 16 giorni di distanza, data = giorno di acquisizione
        rep = freshness.check_and_update({"hl_funding:BTC": {"value": 10.95, "obs_date": giorno.isoformat()}},
                                         today=giorno)
    assert rep["stale"] == [] and rep["unknown"] == [] and rep["fresh"] == 1


# ----------------------------------------------------------------- POLITICS

@pytest.mark.parametrize("domanda", [
    "Will the Russia x Ukraine war end by December 31, 2099?",
    "Will the war in Gaza end by June 30?",
    "Iran-Israel war ends by December 31?",
    "Will China and the US sign a tariff deal by December 31?",
    "Will the US lower tariffs on China in 2099?",
    "Will the US avoid a recession in 2099?",
    "Russia x Ukraine peace agreement in 2099?",
    "Will Israel pause strikes in Gaza?",
    "Will Israel and Hamas agree to stop attacks?",
])
def test_politics_i_mercati_di_pace_non_sono_coda(domanda):
    for alternative in ss._POLI_TERMINI.values():
        assert not ss._poli_termini_ok(domanda, alternative), domanda


@pytest.mark.parametrize("domanda,tema", [
    ("Will China invade Taiwan by end of 2099?", "Cina-Taiwan"),
    ("US recession by end of 2099?", "recessione USA"),
    ("Will Israel strike Iran before year-end?", "Iran-Israele"),
])
def test_politics_end_of_e_una_data_non_una_pace(domanda, tema):
    assert ss._poli_termini_ok(domanda, ss._POLI_TERMINI[tema])


# I 25 titoli della revisione avversariale (10/10): True = mercato di PACE/risoluzione.
TITOLI_REVISIONE = [
    ("Will peace talks collapse and war resume by December 31, 2026?", False),
    ("Ceasefire violated: will Russia attack Kyiv again by end of 2026?", False),
    ("Will the US-China trade deal fail and China tariffs rise above 60%?", False),
    ("Will Iran attack Israel before talks?", False),
    ("Will Israel resume strikes on Iran by November 30?", False),
    ("Will the US withdraw from the Iran deal and strike Iran?", False),
    ("Will NATO end up in a war with Russia in 2026?", False),
    ("Will Iran suspend IAEA cooperation and Israel strike Iran?", False),
    ("Will the Fed lower rates as the US enters recession in 2026?", False),
    ("US recession in 2026?", False),
    ("Will China invade Taiwan by end of 2026?", False),
    ("Will the war in Ukraine end in 2026?", True),
    ("Russia x Ukraine ceasefire in 2026?", True),
    ("Will the US avoid a recession in 2026?", True),
    ("Israel strikes Iran by October 31?", False),
    ("Will the US officially declare war on Iran by December 31, 2026?", False),
    ("Will Iran close the Strait of Hormuz in 2026?", False),
    ("Will China blockade Taiwan in 2026?", False),
    ("Will the trade war escalate in 2026?", False),
    ("Will Israel attack Iran again after the ceasefire ends?", False),
    ("Will Iran and Israel sign a ceasefire agreement by year-end?", True),
    ("Will Israel strike Iran's nuclear facilities before the end of October?", False),
    ("Will Russia invade a NATO country before the end of 2026?", False),
    ("Will the Gaza war end before 2027?", True),
    ("Ceasefire broken?", False),
]


@pytest.mark.parametrize("domanda,pace", TITOLI_REVISIONE)
def test_politics_i_25_titoli_della_revisione(domanda, pace):
    assert ss._poli_risoluzione(domanda) is pace, domanda


@pytest.mark.parametrize("domanda,tema", [
    ("Will peace talks collapse and war resume by December 31, 2026?", "conflitto/guerra"),
    ("Ceasefire violated: will Russia attack Kyiv again by end of 2026?", "conflitto/guerra"),
    # v6 (10/10): per i dazi «fail\w*» e' guardia di allentamento: falso negativo accettato,
    # la frase non e' piu' fra quelle che contano (v. test_soglie_score_fonte_unica)
    ("Will Iran attack Israel before talks?", "Iran-Israele"),
    ("Will Israel resume strikes on Iran by November 30?", "Iran-Israele"),
    ("Will the US withdraw from the Iran deal and strike Iran?", "Iran-Israele"),
    ("Will NATO end up in a war with Russia in 2026?", "conflitto/guerra"),
    ("Will Iran suspend IAEA cooperation and Israel strike Iran?", "Iran-Israele"),
    ("Will Israel attack Iran again after the ceasefire ends?", "Iran-Israele"),
    ("Will the Fed lower rates as the US enters recession in 2026?", "recessione USA"),
])
def test_politics_l_escalation_con_parole_di_pace_conta_per_il_tema(domanda, tema):
    assert ss._poli_termini_ok(domanda, ss._POLI_TERMINI[tema]), domanda


def _ev(q, p, vol):
    return {"title": q, "end_date": "2099-12-31T00:00:00Z",
            "markets": [{"question": q, "outcomes": ["Yes", "No"], "prices": [str(p), str(1 - p)],
                         "end_date": "2099-12-31T00:00:00Z", "volume_24h": vol}]}


def test_politics_il_mercato_di_pace_piu_scambiato_non_vince(monkeypatch):
    risposte = {ss._POLI_TOPICS["conflitto/guerra"]: [
        _ev("Will the Russia x Ukraine war end by December 31, 2099?", 0.18, 900_000.0),
        _ev("Will the US officially declare war on Iran by December 31, 2099?", 0.04, 50_000.0)]}
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events",
                        lambda q, max_results=10: {"results": risposte.get(q, [])})
    s = ss.politics_score()
    assert s["metrics"]["topics"]["conflitto/guerra"] == pytest.approx(0.04)


def test_politics_il_mercato_di_escalation_piu_scambiato_vince(monkeypatch):
    risposte = {ss._POLI_TOPICS["conflitto/guerra"]: [
        _ev("Will peace talks collapse and war resume by December 31, 2099?", 0.22, 900_000.0),
        _ev("Will the US officially declare war on Iran by December 31, 2099?", 0.04, 50_000.0)]}
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events",
                        lambda q, max_results=10: {"results": risposte.get(q, [])})
    s = ss.politics_score()
    assert s["metrics"]["topics"]["conflitto/guerra"] == pytest.approx(0.22)


@pytest.mark.parametrize("valore", [1.0, 1.5, 45, 0.0])
def test_politics_unita_ambigua_rifiutata_e_dichiarata(valore):
    s = ss.politics_score({"Synthetic A": 0.3, "Synthetic B": valore})
    assert "Synthetic B" not in s["metrics"]["topics"]
    assert "Synthetic B" in s["metrics"]["non_calcolabili"]
    lab, _, pt = _riga(s, "Synthetic B")
    assert pt is None and lab in s["unscored"]
    assert s["max_score"] == 3


def test_politics_solo_valori_ambigui_nessun_numero():
    assert ss.politics_score({"Synthetic": 1.0}) is None
    assert ss.politics_score({"Synthetic": 1.5}) is None


@pytest.mark.parametrize("tema,prob,punti", [
    ("recessione USA", 0.19, 0), ("recessione USA", 0.20, 1), ("recessione USA", 0.55, 3),
    ("conflitto/guerra", 0.049, 0), ("conflitto/guerra", 0.05, 1), ("conflitto/guerra", 0.30, 3),
    ("Iran-Israele", 0.34, 1), ("Iran-Israele", 0.35, 2),
    ("tema sintetico", 0.10, 1), ("tema sintetico", 0.45, 3),
])
def test_politics_soglie_per_tema(tema, prob, punti):
    s = ss.politics_score({tema: prob})
    assert _riga(s, tema)[2] == punti


# ----------------------------------------------------------------- EVENTDESK

PF = {"positions": [{"ticker": t, "peso_pct": 10} for t in ("ZAA", "ZBB", "ZCC", "ZDD", "ZEE", "ZFF")]}


def _news(n_per_nome):
    return lambda q, max_results=10: {"news": [{"title": "notizia sintetica"}] * n_per_nome,
                                      "fonti": {"fonte_a": "live", "fonte_b": "live"}}


def _geo(monkeypatch, temi):
    risposte = {ss._POLI_TOPICS[t]: [_ev(q, p, 10.0)] for t, (q, p) in temi.items()}
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events",
                        lambda q, max_results=10: {"results": risposte.get(q, [])})


GEO2 = {"recessione USA": ("US recession by end of 2099?", 0.25),
        "conflitto/guerra": ("Will the US officially declare war on Iran by end of 2099?", 0.04)}


def test_eventdesk_il_volume_di_notizie_non_cambia_il_rischio(monkeypatch):
    _geo(monkeypatch, GEO2)
    esiti = []
    for k in (10, 1):   # 60 item contro 6 item: stessa geopolitica
        monkeypatch.setattr(agent_tools, "tool_search_news", _news(k))
        s = ss.eventdesk_score(PF)
        esiti.append((s["score"], s["max_score"], s["verdict"]))
        lab, _, pt = _riga(s, "Volume notizie")
        assert pt is None and lab in s["excluded"]
    assert esiti[0] == esiti[1] == (1, 6, "EVENTI CALMI")


def test_eventdesk_riga_eventi_nd_fuori_dal_massimo(monkeypatch):
    _geo(monkeypatch, GEO2)
    monkeypatch.setattr(agent_tools, "tool_search_news", _news(10))
    s = ss.eventdesk_score(PF)
    lab, _, pt = _riga(s, "Eventi ad alto impatto")
    assert pt is None and lab in s["unscored"]
    assert s["max_score"] == 3 * s["metrics"]["righe_rischio"] == 6


def test_eventdesk_geopolitica_nd_e_solo_volume_niente_verdetto(monkeypatch):
    # prima: geopolitica n.d. + 60 item = 3/6 «EVENTI CALDI»
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events",
                        lambda q, max_results=10: {"results": [], "error": "n.d. (prova)"})
    monkeypatch.setattr(agent_tools, "tool_search_news", _news(10))
    s = ss.eventdesk_score(PF)
    assert s["score"] is None and s["max_score"] is None
    assert s["verdict"].startswith("n.d.: copertura insufficiente")
    geo = [r for r in s["lines"] if r[0].startswith("GEO |")]
    assert geo and all(r[2] is None for r in geo)


def test_eventdesk_un_solo_tema_non_basta(monkeypatch):
    _geo(monkeypatch, {"recessione USA": GEO2["recessione USA"]})
    monkeypatch.setattr(agent_tools, "tool_search_news", _news(10))
    s = ss.eventdesk_score(PF)
    assert s["score"] is None and s["verdict"].startswith("n.d.")


def test_eventdesk_contratto_risk_lines_di_news_score(monkeypatch):
    """Il news_score rifatto dichiara le righe di RISCHIO in `risk_lines`: solo quelle contano."""
    _geo(monkeypatch, GEO2)
    finto = {"domain": "news", "score": 2, "max_score": 6, "verdict": "x", "metrics": {},
             "lines": [("Riga rischio sintetica", "7", 2), ("Volume notizie (book)", "60", 3)],
             "risk_lines": ["Riga rischio sintetica"]}
    monkeypatch.setattr(ss, "news_score", lambda portfolio_data=None: finto)
    s = ss.eventdesk_score(PF)
    assert s["metrics"]["righe_rischio"] == 3 and s["max_score"] == 9 and s["score"] == 3
    assert _riga(s, "Volume")[2] is None


def test_eventdesk_riga_di_rischio_news_con_punti_none_non_rompe_la_somma(monkeypatch):
    _geo(monkeypatch, GEO2)
    finto = {"domain": "news", "score": None, "max_score": None, "verdict": "x", "metrics": {},
             "lines": [("Riga rischio sintetica", "n.d.: fonte muta", None)],
             "risk_lines": ["Riga rischio sintetica"]}
    monkeypatch.setattr(ss, "news_score", lambda portfolio_data=None: finto)
    s = ss.eventdesk_score(PF)
    lab, _, pt = _riga(s, "Riga rischio sintetica")
    assert pt is None and lab in s["unscored"]
    assert s["metrics"]["righe_rischio"] == 2 and s["max_score"] == 6


# ------------------------------------------------------------- _verdict_bands

def test_verdict_bands_massimo_zero_e_nd_non_basso():
    assert ss._verdict_bands(0, 0, ["BASSO", "M", "E", "C"]).startswith("n.d.")
    assert ss._verdict_bands(0, None, ["BASSO", "M", "E", "C"]).startswith("n.d.")
    assert ss._verdict_bands(0, 3, ["BASSO", "M", "E", "C"]) == "BASSO"


@pytest.mark.parametrize("score,etichetta", [(0, "B"), (2, "B"), (3, "M"), (5, "M"), (6, "E"), (8, "E"), (9, "C")])
def test_verdict_bands_confini_su_12(score, etichetta):
    assert ss._verdict_bands(score, 12, ["B", "M", "E", "C"]) == etichetta


# ---------------------------------------------------------- colonna del PDF

@pytest.mark.parametrize("lingua", ["it", "en"])
def test_i_verdetti_nuovi_stanno_nella_colonna_del_pdf(lingua):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from bellomberg.core.language import language_context
    with language_context(lingua):
        casi = [ss.macro_score(_dash(**dict(vix=80, curve=-200, tips=4.0, cpi=9.0, hy=4.1, ig=1.4,
                                            unemp=1.0, delta10=0)), oggi=OGGI),
                ss.macro_score(_dash(vix=35, hy=9, ig=3, delta10=70), oggi=OGGI),
                ss.crypto_score(_intel([_fis("BTC", -7.7, 9), _fis("ETH", -7.7, 9)]), adesso=ADESSO),
                ss.crypto_score(_intel([_fis("BTC", 10.5, 9, -45), _fis("ETH", 10.5, 9, -45)]), adesso=ADESSO)]
        for s in casi:
            v = s["verdict"]
            assert stringWidth(v, "Helvetica-Bold", 8.5) <= 203, v


def _news_finto(copertura, nf=None, nc=None):
    met = {"copertura": copertura, "n_fonti_mute": nf, "n_fonti_candidate": nc}
    return lambda portfolio_data=None: {"domain": "news", "score": None, "max_score": None, "verdict": "x",
                                        "metrics": met, "lines": [], "risk_lines": []}


@pytest.mark.parametrize("lingua", ["it", "en"])
@pytest.mark.parametrize("copertura,nf,nc", [("PARZIALE", 12, 12), ("PARZIALE", None, None), ("IGNOTA", None, None)])
@pytest.mark.parametrize("probs", [{}, {"recessione USA": 0.10, "conflitto/guerra": 0.01},
                                   {"recessione USA": 0.25, "conflitto/guerra": 0.06},
                                   {"recessione USA": 0.40, "conflitto/guerra": 0.20},
                                   {"recessione USA": 0.60, "conflitto/guerra": 0.40}])
def test_eventdesk_verdetto_con_suffisso_news_sta_in_195_pt(monkeypatch, lingua, copertura, nf, nc, probs):
    """Revisione BASSO 7: col verdetto n.d. il suffisso arrivava a 213,5 e 260,7 pt."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from bellomberg.core.language import language_context
    originale = ss.politics_score
    monkeypatch.setattr(ss, "news_score", _news_finto(copertura, nf, nc))
    monkeypatch.setattr(ss, "politics_score", lambda: originale(dict(probs)) if probs else None)
    with language_context(lingua):
        s = ss.eventdesk_score(PF)
    v = s["verdict"]
    assert "(news" in v and stringWidth(v, "Helvetica-Bold", 8.5) <= 195, (v, stringWidth(v, "Helvetica-Bold", 8.5))
    if not probs:
        assert v.startswith(("n.d.: copertura scarsa", "n/a: thin coverage")), v
