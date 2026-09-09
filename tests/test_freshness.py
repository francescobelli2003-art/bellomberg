"""Blocco QUALITA' DATI appeso dal CODICE al memo (21/07: i 24 STALE del #46
erano nel prompt del Capo e in zero righe del memo). Solo il formatter puro:
check_and_update scrive lo snapshot su disco e resta fuori da questa suite.
"""
from bellomberg.core.freshness import format_for_memo


def test_format_for_memo_dichiara_gli_stale_e_tace_se_fresco():
    report = {"stale": [
        "vix: valore IDENTICO da 12 giorni (16.5): fonte ferma o cachata",
        "dxy: osservazione del 2026-07-10 = 11 giorni fa (limite 6 per questa serie)",
    ], "fresh": 22, "checked": 24}
    block = format_for_memo(report)
    assert block.startswith("## QUALITA' DATI")
    assert "- vix:" in block and "- dxy:" in block
    assert "24 serie esterne controllate: 22 fresche, 2 STALE" in block

    # tutto fresco (o report assente) -> stringa vuota: il memo resta pulito
    assert format_for_memo({"stale": [], "fresh": 5, "checked": 5}) == ""
    assert format_for_memo({}) == ""
    assert format_for_memo(None) == ""
