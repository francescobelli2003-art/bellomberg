"""Valuta di quotazione: priorita', provenienza e nessun USD implicito."""
import json

import pytest

import bellomberg.storage.classificazione as cl


def negozio(tmp_path, **campi):
    p = tmp_path / "veicoli.json"
    p.write_text(json.dumps({"FONDO.L": {"tipo": "cef", "provenienza": "dichiarato",
                                       "nav_valuta": "USD", **campi}}), encoding="utf-8")
    return cl.carica_veicoli(str(p))


def test_quotazione_diversa_da_nav_e_precedenza_posizione(tmp_path):
    n = negozio(tmp_path, valuta="GBX")
    assert n["motivo"] is None
    e = cl.valuta("FONDO.L", negozio=n)
    assert e.valore == "GBX" and e.fonte == "registro_pm"
    posizione = cl.valuta("FONDO.L", negozio=n, valuta_posizione="GBP")
    assert posizione.valore == "GBP" and posizione.fonte == "misura_dato"
    assert "posizione" in posizione.evidenza


def test_suffisso_e_solo_un_ripiego_e_ticker_nudo_resta_ignoto(tmp_path):
    n = negozio(tmp_path)
    assert cl.valuta("FONDO.L", negozio=n).valore == "GBX"
    assert cl.valuta("FONDO.L", negozio=n).fonte == "ripiego"
    e = cl.valuta("ALFA", negozio=n)
    assert e.valore is None and e.fonte == "nessuna"


def test_cambio_negozio_letto_a_ogni_chiamata(tmp_path, monkeypatch):
    p = tmp_path / "v.json"
    monkeypatch.setattr(cl, "PERCORSO_VEICOLI", str(p))
    for valuta in ("GBP", "GBX"):
        p.write_text(json.dumps({"FONDO.L": {"tipo": "cef", "provenienza": "dichiarato",
                                            "valuta": valuta}}), encoding="utf-8")
        assert cl.valuta("FONDO.L").valore == valuta


@pytest.mark.parametrize("dato", ["", "usd", "US", 3])
def test_valuta_esplicita_malformata_non_sparisce(tmp_path, dato):
    n = negozio(tmp_path, valuta=dato)
    assert n["origine"] == "illeggibile" and "valuta" in n["motivo"]
    assert cl.valuta("FONDO.L", negozio=n).valore is None


def test_negozio_guasto_non_impedisce_valuta_misurata_ma_impedisce_inferenza(tmp_path):
    n = {"veicoli": {}, "origine": "illeggibile", "motivo": "JSON rotto"}
    e = cl.valuta("FONDO.L", negozio=n)
    assert e.valore is None and "JSON rotto" in e.evidenza
    assert cl.valuta("FONDO.L", negozio=n, valuta_posizione="EUR").valore == "EUR"
    assert cl.valuta("FONDO.L", negozio=n, valuta_posizione="sbagliata").valore is None
