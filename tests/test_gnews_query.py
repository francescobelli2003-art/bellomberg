"""gnews_safe_query: sanificazione misurata live il 15/07 (token con
caratteri speciali -> 400) + fix 21/07 (token di SOLA punteggiatura -> 400
anche quotati: si scartano). Funzione pura, zero rete.
"""
from bellomberg.market_data.news_sources import gnews_safe_query


def test_token_speciali_quotati_e_sola_punteggiatura_scartata():
    q = gnews_safe_query('risk-off GAMMA.MI $467 ( & "founder premium" nav')
    assert q == '"risk-off" "GAMMA.MI" "$467" "founder premium" nav'

    # solo punteggiatura -> query vuota (meglio niente che un 400 sistematico)
    assert gnews_safe_query("( & -") == ""
    # gia' quotato resta intatto; alfanumerico resta nudo
    assert gnews_safe_query('"8-K filing" ALFA') == '"8-K filing" ALFA'
    # input degeneri
    assert gnews_safe_query("") == ""
    assert gnews_safe_query(None) == ""
