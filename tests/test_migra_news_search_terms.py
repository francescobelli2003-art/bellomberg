"""La migrazione dei termini di ricerca news e' una MISURA, non una frase: se il
round-trip non torna, esce in errore invece di dichiarare 'fatto'. Simboli
inventati (tests/ e' pubblico). Stessa forma di tests/test_migra_fonti.py.

Lotto 1 del criterio (1), 04/09 sera (Fable 5.1, chat a1)."""
import json
import os
import sys

from tools.migrations import migra_news_search_terms as mig

SORGENTE = '''
from typing import Dict, List

TICKER_SEARCH_TERMS: Dict[str, List[str]] = {
    "ACME.MI":  ["Acme Manifatture", "Acme"],  # un commento a fine riga
    "BETA.DE":  ["Beta Industrie"],
}
'''


def test_legge_il_dict_senza_eseguire_il_modulo(tmp_path):
    f = tmp_path / "finto.py"
    f.write_text(SORGENTE, encoding="utf-8")
    d = mig.leggi_termini(str(f))
    assert d == {"ACME.MI": ["Acme Manifatture", "Acme"], "BETA.DE": ["Beta Industrie"]}


def test_riconcilia_conta_voci_e_termini(tmp_path):
    d = {"ACME.MI": ["Acme Manifatture", "Acme"], "KRYPTO": None}
    dest = tmp_path / "out.json"
    dest.write_text(json.dumps({"_leggimi": "servizio", **d}), encoding="utf-8")
    assert mig.riconcilia(d, str(dest)) == {"voci": 2, "termini": 2, "differenze": []}


def test_riconcilia_elenca_ogni_differenza(tmp_path):
    atteso = {"ACME.MI": ["Acme"], "BETA.DE": ["Beta"]}
    dest = tmp_path / "out.json"
    dest.write_text(json.dumps({"ACME.MI": ["Acme", "Acme Manifatture"], "GAMMA": ["Gamma"]}),
                    encoding="utf-8")
    r = mig.riconcilia(atteso, str(dest))
    assert sorted(r["differenze"]) == ["ACME.MI: termini diversi", "BETA.DE: PERSA",
                                       "GAMMA: COMPARSA dal nulla"]


def test_stato_dichiara_la_migrazione_da_fare(tmp_path):
    src = tmp_path / "finto.py"
    src.write_text(SORGENTE, encoding="utf-8")
    codice, frase = mig.stato(str(src), str(tmp_path / "mai_scritto.json"))
    assert codice == "da_fare", (codice, frase)


def test_stato_dichiara_la_migrazione_GIA_ESEGUITA_invece_di_sembrare_rotta(tmp_path):
    src = tmp_path / "finto.py"
    src.write_text("# il dict e' migrato: qui non c'e' piu'\n", encoding="utf-8")
    dest = tmp_path / "negozio.json"
    dest.write_text(json.dumps({"_leggimi": "servizio", "ACME.MI": ["Acme"]}), encoding="utf-8")
    codice, frase = mig.stato(str(src), str(dest))
    assert codice == "fatta", (codice, frase)


def test_stato_dichiara_la_migrazione_rotta_senza_ancora_e_senza_negozio(tmp_path):
    src = tmp_path / "finto.py"
    src.write_text("# niente\n", encoding="utf-8")
    codice, frase = mig.stato(str(src), str(tmp_path / "mai_scritto.json"))
    assert codice == "rotta", (codice, frase)
    assert "mai_scritto.json" in frase


def test_la_destinazione_di_default_onora_BELLOMBERG_DATA_DIR_come_il_modulo(tmp_path, monkeypatch):
    """Il modulo legge da `memory_db.DB_DIR` (che onora BELLOMBERG_DATA_DIR): se il
    migratore scrivesse in RADICE/data con la variabile impostata, riconcilierebbe verde un
    negozio che a runtime risulta «assente» (review 04/09)."""
    monkeypatch.delenv("BELLOMBERG_DATA_DIR", raising=False)
    assert mig._dest_default() == os.path.join(mig.RADICE, "data", "news_search_terms.json")
    monkeypatch.setenv("BELLOMBERG_DATA_DIR", str(tmp_path / "altrove"))
    assert mig._dest_default() == os.path.join(str(tmp_path / "altrove"), "news_search_terms.json")
    monkeypatch.setenv("BELLOMBERG_DATA_DIR", "dati_relativi")
    assert mig._dest_default() == os.path.join(mig.RADICE, "dati_relativi", "news_search_terms.json")


def test_dry_run_non_scrive(tmp_path, monkeypatch, capsys):
    src = tmp_path / "finto.py"
    src.write_text(SORGENTE, encoding="utf-8")
    monkeypatch.setattr(mig, "SORGENTE", str(src))
    dest = tmp_path / "negozio.json"
    assert mig.main(["--dest", str(dest)]) == 0
    assert not dest.exists()
    assert "DRY-RUN" in capsys.readouterr().out


def test_apply_scrive_riconcilia_e_porta_gli_esclusi_come_null(tmp_path, monkeypatch, capsys):
    """`--escludi X` e' il modo in cui i simboli oggi cablati come «salta» (un set
    letterale nel codice) entrano nel negozio: voce `null`, esclusione DICHIARATA."""
    src = tmp_path / "finto.py"
    src.write_text(SORGENTE, encoding="utf-8")
    monkeypatch.setattr(mig, "SORGENTE", str(src))
    dest = tmp_path / "negozio.json"
    assert mig.main(["--apply", "--dest", str(dest), "--escludi", "KRYPTO"]) == 0
    scritto = json.load(open(dest, encoding="utf-8"))
    assert scritto["ACME.MI"] == ["Acme Manifatture", "Acme"]
    assert "KRYPTO" in scritto and scritto["KRYPTO"] is None
    assert scritto["_leggimi"]
    out = capsys.readouterr().out
    assert "riconciliata" in out


def test_apply_esce_in_errore_se_il_round_trip_non_torna(tmp_path, monkeypatch):
    """Una garanzia e' una misura: se il negozio scritto non combacia col sorgente,
    lo strumento NON dice «fatto»."""
    src = tmp_path / "finto.py"
    src.write_text(SORGENTE, encoding="utf-8")
    monkeypatch.setattr(mig, "SORGENTE", str(src))
    dest = tmp_path / "negozio.json"
    monkeypatch.setattr(mig, "riconcilia",
                        lambda atteso, p: {"voci": 1, "termini": 1,
                                           "differenze": ["BETA.DE: PERSA"]})
    try:
        mig.main(["--apply", "--dest", str(dest)])
    except SystemExit as e:
        assert "NON RICONCILIATA" in str(e)
    else:
        raise AssertionError("main ha dichiarato fatto con una differenza aperta")
