"""sizing_engine classifica dal negozio dei veicoli e scrive la provenienza per ogni riga
(05/09, Fable 5.1, classificazione lotto 2).

Prima: VEHICLE_TICKERS e SECTOR_OF (due liste di nomi del PM) erano cablati nel modulo, un
nome fuori dalla lista riceveva in silenzio il limite prudente single-stock, e nessun test
del repo nominava compute_sizing (zero copertura misurata dallo scettico del 04/09).

Ora `_limits_for` prende la CLASSE, la classe viene dal negozio con la sua provenienza, un
nome non classificato riceve lo STESSO limite di ieri ma etichettato come ripiego, e il testo
per il Capo dice quanti e quali. I numeri di ieri non si muovono: lo prova il confronto con
la cassetta (prove_backend), qui si provano le forme. Book e negozio finti, nessun simbolo
del PM.
"""
import json

import pytest

import bellomberg.storage.classificazione as cl
import bellomberg.portfolio.sizing_engine as se

NEGOZIO = {
    "ETFX.MI": {"tipo": "etf", "provenienza": "dichiarato", "classe_size": "veicolo",
                "settore_tema": "ETF Tema (tema)"},
    "FONDO.L": {"tipo": "cef", "provenienza": "dichiarato", "classe_size": "veicolo"},
    "BANCA.MI": {"tipo": "bank", "provenienza": "dichiarato", "classe_size": "single",
                 "settore_policy": "banks"},
    "BANCA2": {"tipo": "bank", "provenienza": "dichiarato", "classe_size": "single",
               "settore_policy": "banks"},
    "ACME": {"tipo": "operating", "provenienza": "dichiarato", "classe_size": "single",
             "settore_policy": "semis"},
    "SENZACLASSE": {"tipo": "etf", "provenienza": "dichiarato"},
}

BOOK = {
    "cash_disponibile_eur": 20000.0,
    "positions": [
        {"ticker": "ETFX.MI", "valore_mercato_eur": 30000},
        {"ticker": "FONDO.L", "valore_mercato_eur": 25000},
        {"ticker": "BANCA.MI", "valore_mercato_eur": 20000},
        {"ticker": "BANCA2", "valore_mercato_eur": 20000},
        {"ticker": "ACME", "valore_mercato_eur": 10000},
        {"ticker": "SENZACLASSE", "valore_mercato_eur": 5000},
        {"ticker": "IGNOTO.X", "valore_mercato_eur": 5000},
    ],
}


# A2 (06/09): i tetti non sono piu' costanti del modulo, vengono dal mandato. Questo file
# prova le CLASSI, non il mandato: dichiara i propri parametri e li passa a ogni chiamata,
# cosi' gli scenari (tre banche sotto il cap individuale ma insieme al cap di settore)
# restano veri qualunque mandato ci sia sul disco.
PARAMS = {
    "base_single": 0.10, "cap_single": 0.12,
    "base_veicolo": 0.30, "cap_veicolo": 0.33,
    "cap_settore": 0.30, "limite_minimo": 0.02,
    "posizione_minima_pct": 500.0 / 130000.0 * 100.0,
    "budget_stress_nav_pct": -25.0, "budget_var99_nav_pct": -4.0,
    "size_nuova_posizione": (0.02, 0.04), "max_posizioni": None, "top3_max": None,
    "impronta": "classe00",
}


@pytest.fixture
def params():
    return dict(PARAMS)


@pytest.fixture
def negozio(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps(NEGOZIO), encoding="utf-8")
    return cl.carica_veicoli(str(p))


def _riga(s, t):
    return next(p for p in s["positions"] if p["ticker"] == t)


def test_limiti_per_classe_non_per_ticker():
    assert se._limits_for_classe("veicolo", PARAMS) == (PARAMS["base_veicolo"], PARAMS["cap_veicolo"])
    assert se._limits_for_classe("single", PARAMS) == (PARAMS["base_single"], PARAMS["cap_single"])
    with pytest.raises(ValueError):
        se._limits_for_classe("grande", PARAMS)


def test_classe_dal_negozio_con_provenienza(negozio):
    e = se._classifica("FONDO.L", negozio, PARAMS)
    assert e.valore == "veicolo" and e.fonte == "registro_pm" and e.dominio == "classe_size"
    r = se._classifica("IGNOTO.X", negozio, PARAMS)
    assert r.valore == "single" and r.fonte == "ripiego" and "RIPIEGO" in r.dichiarazione.upper()
    assert "%.0f%%" % (PARAMS["cap_single"] * 100) in r.dichiarazione
    s = se._classifica("SENZACLASSE", negozio, PARAMS)
    assert s.valore == "single" and s.fonte == "ripiego" and "classe" in s.evidenza


def test_compute_sizing_scrive_classe_e_provenienza_per_ogni_riga(negozio):
    s = se.compute_sizing(BOOK, None, negozio=negozio, parametri=PARAMS)
    v = _riga(s, "FONDO.L")
    assert v["class"] == "veicolo" and v["class_fallback"] is False
    assert v["class_source"].startswith("registro_pm")
    assert v["max_position_pct"] <= PARAMS["cap_veicolo"] * 100
    i = _riga(s, "IGNOTO.X")
    assert i["class"] == "single" and i["class_fallback"] is True
    assert i["class_source"].startswith("ripiego")
    assert i["max_position_pct"] <= PARAMS["cap_single"] * 100
    # stesso limite di un single dichiarato con gli stessi input: il ripiego non muove numeri
    a = _riga(s, "ACME")
    assert i["max_position_pct"] == a["max_position_pct"]
    assert s["summary"]["unclassified"] == ["IGNOTO.X", "SENZACLASSE"]
    # «"2" in nota» era vacuo (il 12% contiene un 2 — review 05/09): il conteggio in testa
    assert s["summary"]["classification_note"].startswith("2 nomi su 7")
    assert "single-stock" in s["summary"]["classification_note"]


def test_settore_di_policy_dal_negozio_e_dichiarato(negozio, tmp_path):
    s = se.compute_sizing(BOOK, None, negozio=negozio, parametri=PARAMS)
    b = _riga(s, "BANCA.MI")
    assert b["sector_policy"] == "banks" and "registro_pm" in b["sector_source"]
    a = _riga(s, "IGNOTO.X")
    assert a["sector_policy"] is None and "nessun cap" in a["sector_source"].lower()
    # Il cap di settore riduce SOLO lo spazio di AGGIUNTA dei single-stock sotto il loro
    # limite (logica di ieri, intatta): con due banche non puo' mai mordere (2 x 12% = 24%
    # < 30%; e nel BOOK sopra sono gia' oltre il limite individuale, quindi tagliate).
    # Premessa corretta il 05/09 (era scritta in RED senza i numeri): serve una TERZA banca
    # a bassa vol, ognuna sotto il 12% ma insieme al 30% — e il settore viene dal negozio.
    n3 = dict(NEGOZIO)
    n3["BANCA3"] = {"tipo": "bank", "provenienza": "dichiarato", "classe_size": "single",
                    "settore_policy": "banks"}
    p3 = tmp_path / "v3.json"
    p3.write_text(json.dumps(n3), encoding="utf-8")
    book3 = {"cash_disponibile_eur": 20000.0, "positions": [
        {"ticker": "ETFX.MI", "valore_mercato_eur": 35000},
        {"ticker": "FONDO.L", "valore_mercato_eur": 35000},
        {"ticker": "BANCA.MI", "valore_mercato_eur": 10000},
        {"ticker": "BANCA2", "valore_mercato_eur": 10000},
        {"ticker": "BANCA3", "valore_mercato_eur": 10000}]}
    rischio = {"per_asset": {t: {"vol_annual_pct": 10} for t in ("BANCA.MI", "BANCA2", "BANCA3")}}
    s3 = se.compute_sizing(book3, rischio, negozio=cl.carica_veicoli(str(p3)), parametri=PARAMS)
    assert s3["summary"]["sectors_at_cap"] == ["banks"]
    for t in ("BANCA.MI", "BANCA2", "BANCA3"):
        r3 = _riga(s3, t)
        assert r3["sector_policy"] == "banks" and r3["remaining_capacity_eur"] == 0
        assert "settore banks al cap" in r3["verdict"]


def test_i_candidati_portano_la_stessa_provenienza(negozio):
    s = se.compute_sizing(BOOK, None, candidates=[{"ticker": "NUOVO", "vol_annual_pct": 30},
                                                  {"ticker": "ETFX.MI", "vol_annual_pct": 20}],
                          negozio=negozio, parametri=PARAMS)
    c = {x["ticker"]: x for x in s["candidates"]}
    assert c["NUOVO"]["class"] == "single" and c["NUOVO"]["class_fallback"] is True
    assert c["ETFX.MI"]["class"] == "veicolo" and c["ETFX.MI"]["class_fallback"] is False


def test_format_for_capo_dice_quanti_e_quali_non_classificati(negozio):
    s = se.compute_sizing(BOOK, None, candidates=[{"ticker": "NUOVO", "vol_annual_pct": 30}],
                          negozio=negozio, parametri=PARAMS)
    t = se.format_for_capo(s)
    righe = t.splitlines()
    i_policy = next(i for i, r in enumerate(righe) if r.startswith("POLICY ("))
    i_class = next(i for i, r in enumerate(righe) if r.startswith("CLASSIFICAZIONE:"))
    assert i_class == i_policy + 1, "la riga di sintesi sta subito dopo la POLICY, in testa"
    assert "2 VEICOLO" in righe[i_class] and "3 azione singola" in righe[i_class]
    assert "2 NON CLASSIFICAT" in righe[i_class] and "IGNOTO.X" in righe[i_class]
    assert "SENZACLASSE" in righe[i_class]
    r_ign = next(r for r in righe if r.strip().startswith("IGNOTO.X"))
    assert r_ign.endswith("| classe n.d. (ripiego)")
    r_ok = next(r for r in righe if r.strip().startswith("FONDO.L"))
    assert "classe n.d." not in r_ok
    r_cand = next(r for r in righe if r.strip().startswith("NUOVO"))
    assert "classe n.d. (ripiego)" in r_cand


def test_corr_stimata_ora_si_vede(negozio):
    s = se.compute_sizing(BOOK, None, negozio=negozio, parametri=PARAMS)
    t = se.format_for_capo(s)
    assert "(corr stim.)" in t, "una correlazione ignota riceve il moltiplicatore neutro e il Capo deve saperlo"


def test_liste_derivate_dal_negozio_sono_set_e_dict(negozio):
    """VEHICLE_TICKERS resta un set e SECTOR_OF un dict: specialist_scores e
    pulizia_tesi_invalide fanno `set | ...`, portfolio_sectors legge SECTOR_OF."""
    assert isinstance(se.VEHICLE_TICKERS, set) and isinstance(se.SECTOR_OF, dict)
    assert se.veicoli_del_negozio(negozio) == {"ETFX.MI", "FONDO.L"}
    assert se.settori_del_negozio(negozio) == {"BANCA.MI": "banks", "BANCA2": "banks", "ACME": "semis"}


def test_negozio_rotto_e_dichiarato_nel_summary(tmp_path):
    p = tmp_path / "rotto.json"
    p.write_text("{", encoding="utf-8")
    negozio = cl.carica_veicoli(str(p))
    s = se.compute_sizing(BOOK, None, negozio=negozio, parametri=PARAMS)
    assert "illeggibile" in s["summary"]["classification_note"]
    assert all(r["class_fallback"] for r in s["positions"])
    t = se.format_for_capo(s)
    assert "illeggibile" in t
    # i tagli/spazi calcolati col limite di ripiego restano numeri (niente nascosto), ma il
    # Capo deve leggere che NON sono policy finche' il file non e' corretto (review 05/09)
    assert "NON sono policy" in t and "NON sono policy" in s["summary"]["classification_note"]


@pytest.mark.parametrize("base_veicolo, cap_veicolo, pavimento", [
    (0.03, 0.04, 0.02), (0.03, 0.04, 0.09),
])
def test_ripiego_prudente_anche_con_tetti_invertiti(
        tmp_path, base_veicolo, cap_veicolo, pavimento):
    n = cl.carica_veicoli(str(tmp_path / "assente.json"))
    p = dict(PARAMS, base_veicolo=base_veicolo, cap_veicolo=cap_veicolo,
             limite_minimo=pavimento)
    base, cap = se._limits_for("IGNOTO.X", p, negozio=n)
    assert base == min(p["base_single"], base_veicolo)
    assert cap == min(p["cap_single"], cap_veicolo)
    risultato = se.compute_sizing(BOOK, None, negozio=n, parametri=p,
                                  candidates=[{"ticker": "NUOVO"}])
    for r in risultato["positions"] + risultato["candidates"]:
        assert r["class_fallback"]
        assert r["max_position_pct"] <= cap_veicolo * 100
        assert "minimo" in r["class_source"]
    assert any(r["trim_eur"] > 0 for r in risultato["positions"])
    assert "cap 4%" in risultato["summary"]["classification_note"]
