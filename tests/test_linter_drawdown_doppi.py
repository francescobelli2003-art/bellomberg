# -*- coding: utf-8 -*-
"""Il linter segnala lo stesso indice con due drawdown «dal massimo» diversi (audit 10/09).

Memo #53: «il Nifty e' a -10,8% dal massimo [tool]» a una riga e «il Nifty e' a -14,79% dai
massimi [articolo web]» a un'altra, mai riconciliati; il -14,79% era un valore di mesi
prima. Il check e' generale (qualunque nome) e deterministico; flag-only.

Testi INVENTATI.
"""
from bellomberg.reporting.memo_linter import _check_drawdown_duplicati, build_linter_block


def test_due_drawdown_diversi_sullo_stesso_nome_sono_segnalati():
    memo = ("Il contesto: il Sigma e' a -10,8% dal massimo con RSI a 20,6 [src: get_price_live].\n"
            "Piu' avanti: il Sigma e' a -14,79% dai massimi con deflussi esteri [src: tavily].\n")
    out = _check_drawdown_duplicati(memo)
    assert len(out) == 1 and "Sigma" in out[0] and "-10.8%" in out[0] and "-14.79%" in out[0], out


def test_lo_stesso_valore_ripetuto_non_e_un_conflitto():
    memo = ("Il Sigma e' a -10,8% dal massimo [src: tool].\nRipeto: il Sigma resta a -10,8% dal massimo.\n")
    assert _check_drawdown_duplicati(memo) == []


def test_nomi_diversi_non_si_confondono():
    memo = "Il Sigma e' a -10,8% dal massimo. L'Omega e' a -3,2% dal massimo.\n"
    assert _check_drawdown_duplicati(memo) == []


def test_il_blocco_del_linter_porta_il_nuovo_check():
    memo = ("## ACTION TABLE\n\n| Action | Ticker | EUR | Timing | Confidence |\n|---|---|---|---|---|\n"
            "| HOLD | ALFA | 0 | x | MEDIA |\n\nIl Sigma e' a -10,8% dal massimo [src: t].\n"
            "Il Sigma e' a -14,79% dai massimi [src: t].\n")
    blocco = build_linter_block(memo, {"nav_total_eur": 100000.0, "cash_disponibile_eur": 5000.0})
    assert "drawdown" in blocco and "Sigma" in blocco, blocco
