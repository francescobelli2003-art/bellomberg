from bellomberg.reporting import charts_agent


def test_bucket_settori_usa_il_dato_e_non_il_ticker():
    posizioni = [
        {"ticker": "ALFA.MI", "peso_pct": 20, "sector": "Tecnologia"},
        {"ticker": "BETA", "peso_pct": 15, "sector": "Tecnologia"},
        {"ticker": "GAMMA.L", "peso_pct": 5, "settore": "Salute"},
    ]
    assert charts_agent._bucket_settori(posizioni) == {
        "Tecnologia": 35.0,
        "Salute": 5.0,
    }

    posizioni[0]["ticker"] = "KRYPTO"
    assert charts_agent._bucket_settori(posizioni)["Tecnologia"] == 35.0


def test_bucket_settori_dichiara_settore_assente_senza_inventarlo():
    assert charts_agent._bucket_settori([
        {"ticker": "ALFA", "peso_pct": 7},
        {"ticker": "BETA", "peso_pct": 3, "sector": ""},
    ]) == {"n.d. (settore assente)": 10.0}
