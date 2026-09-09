"""sanity_check di dcf_engine (#204b): severita' OK/WARN/BLOCK e flag di
esclusione dall'ACTION TABLE. Funzione pura, zero rete.
"""
from bellomberg.valuation.dcf_engine import sanity_check


def test_sanity_severita_e_esclusione():
    ok = sanity_check(100.0, 100.0)
    assert ok["severity"] == "OK" and not ok["exclude_from_action_table"]

    warn = sanity_check(145.0, 100.0)   # divergenza 45%: cautela, non esclusione
    assert warn["severity"] == "WARN" and not warn["exclude_from_action_table"]

    block = sanity_check(160.0, 100.0)  # divergenza 60%: VAL sospetta, esclusa
    assert block["severity"] == "BLOCK" and block["exclude_from_action_table"]
    assert "ESCLUSO dalla ACTION TABLE" in block["headline"]

    nd = sanity_check(None, 100.0)      # input mancante: n/d dichiarato, mai crash
    assert nd["status"] == "n/d" and nd["severity"] == "OK"
