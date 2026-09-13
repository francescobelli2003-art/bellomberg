# -*- coding: utf-8 -*-
"""Il Capo legge l'ultimo report UTILE di ogni desk, non un segnaposto (audit run 10/09).

Run 1 del 10/09 (memo #53): quant e options avevano un R1 completo in blackboard e un R2
uscito vuoto (segnaposto «No output produced in round 2»). `run_capo` prendeva
`max(rounds)`: il Capo ha ricevuto il segnaposto e ha scritto al PM che tre desk erano muti,
ricavando i loro numeri «da citazioni di secondo livello». Il round 0 e' ricognizione e
NON si promuove: senza un R1/R2 vero resta il segnaposto, dichiarato.

12/09 (Fable 5.1, prerequisito 3): il prefisso porta anche data/ora/fuso del round scelto
quando la Blackboard viva ha l'indice `orari_report`, e dichiara «orario n.d.» senza
(regenerate_memo rimonta i report dal DB e non ha l'indice). Tetto del segnaposto citato
e taglio dichiarato: tests/test_desk_recupero_lavoro.py (T4, T5).

Nessun LLM: si prova la sola scelta dei report.
"""
from bellomberg.agents.capo import scegli_report_specialisti


def test_r2_segnaposto_e_r1_vero_il_capo_legge_r1_e_lo_dichiara():
    data = {"quant": {0: "# QUANT R0 ricognizione", 1: "REPORT R1 VERO con numeri",
                      2: "[quant] No output produced in round 2"}}
    r = scegli_report_specialisti(data)["quant"]
    assert r["round"] == 1
    assert "REPORT R1 VERO con numeri" in r["report"]
    assert r["report"].startswith("[ROUND 2 SENZA REPORT: [quant] No output produced in round 2")
    testa = r["report"].split("\n\n")[0]
    assert "Round 1" in testa and "orario n.d." in testa, testa   # senza indice: dichiarato


def test_con_l_indice_degli_orari_il_prefisso_data_il_round_scelto():
    data = {"quant": {1: "REPORT R1 VERO con numeri", 2: "[quant] No output produced in round 2"}}
    orari = {"quant": {1: "2026-09-10T16:10:28+02:00", 2: "2026-09-10T16:25:00+02:00"}}
    testa = scegli_report_specialisti(data, orari=orari)["quant"]["report"].split("\n\n")[0]
    assert "Round 1, scritto il 2026-09-10 alle 16:10:28 (UTC+02:00)" in testa, testa
    assert "16:25" not in testa, "e' l'orario del round SCELTO, non di quello perso"


def test_senza_r1_vero_il_round_0_non_si_promuove_e_il_desk_e_scoperto():
    data = {"fundamentals": {0: "# FUNDAMENTALS R0 ricognizione", 1: "[fundamentals] No output produced in round 1",
                             2: "[fundamentals] No output produced in round 2"}}
    r = scegli_report_specialisti(data)["fundamentals"]
    assert r["no_report"] is True
    assert r["report"].startswith("[NO REPORT]"), r["report"]      # il letterale della regola DOMINI SCOPERTI
    assert "No output produced in round 2" in r["report"]           # il segnaposto resta citato
    assert "ricognizione" not in r["report"]


def test_report_normale_invariato_e_chiavi_stringa_da_json():
    data = {"macro": {"0": "R0", "1": "R1 FINALE"}, "_red_team": {"1": "critica"}}
    out = scegli_report_specialisti(data)
    assert out["macro"] == {"round": 1, "report": "R1 FINALE", "no_report": False}
    assert "_red_team" not in out


def test_anche_un_error_e_un_segnaposto():
    data = {"options": {1: "REPORT R1", 2: "[ERROR options round 2]: HTTP 500"}}
    r = scegli_report_specialisti(data)["options"]
    assert r["round"] == 1 and "REPORT R1" in r["report"] and "HTTP 500" in r["report"]
