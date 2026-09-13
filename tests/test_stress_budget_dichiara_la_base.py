# -*- coding: utf-8 -*-
"""Il budget di stress dice su quale base e' calcolato il replay (audit run 10/09).

Un memo presentava il replay GFC del NAV contro il budget come il numero che vincola tutto,
piu' volte, senza dire quanti nomi erano PROXY beta x SPY (storia 2008 assente) e
che il replay copriva l'intera finestra. Le misure esistevano in `stress_meta` e il motore
di sizing le scartava. Ora le propaga e il blocco per il Capo le stampa.

Numeri INVENTATI.
"""
from bellomberg.portfolio.sizing_engine import _stress_var_budget, format_for_capo


PARAMS = {"budget_stress_nav_pct": -25.0, "budget_var99_nav_pct": -4.0}


def _stress(meta):
    return {"stress_scenario": "gfc_2008", "stress_fallback": False, "stress_meta": meta}


def test_le_misure_del_replay_arrivano_nel_budget():
    meta = {"window_loss_pct": -30.0, "real_history": ["A", "B"], "proxied": {"C": "PROXY 1.1 x SPY"},
            "window": {"trading_days": 138}, "replaced_days": 138, "zero_filled_days": {"B": 3}}
    b = _stress_var_budget(90000.0, 10000.0, {}, _stress(meta), PARAMS)
    assert b["gfc_n_real"] == 2 and b["gfc_n_proxy"] == 1
    assert b["gfc_window_days"] == 138 and b["gfc_replaced_days"] == 138
    assert b["gfc_zero_filled_names"] == 1
    assert b["gfc_replay_nav_pct"] == -27.0     # -30% sull'investito = 90% del NAV


def test_senza_meta_i_campi_sono_none_non_zero():
    b = _stress_var_budget(90000.0, 10000.0, {}, _stress({"window_loss_pct": -30.0}), PARAMS)
    assert b["gfc_n_real"] is None and b["gfc_n_proxy"] is None and b["gfc_window_days"] is None


def test_il_blocco_del_capo_dice_quanti_proxy():
    meta = {"window_loss_pct": -30.0, "real_history": ["A", "B"], "proxied": {"C": "x", "D": "y"},
            "window": {"trading_days": 138}, "replaced_days": 22, "zero_filled_days": {}}
    b = _stress_var_budget(90000.0, 10000.0, {}, _stress(meta), PARAMS)
    sizing = {"summary": {"invested_capital_eur": 90000.0, "cash_buffer_eur": 10000.0,
                          "cash_pct_of_invested": 11.1, "total_room_existing_eur": 0.0,
                          "deployable_from_cash_eur": 0.0, "names_over_limit": [], "n_names_with_room": 0,
                          "stress_var_budget": b, "params": {"posizione_minima_eur": 100.0},
                          "unclassified": [], "classification_note": "x"},
              "positions": [], "candidates": []}
    testo = format_for_capo(sizing)
    assert "2 nomi con storia 2008 vera, 2 PROXY beta x SPY" in testo, testo
    assert "finestra replicata 22/138 giorni" in testo, testo
