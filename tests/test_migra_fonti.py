"""La migrazione e' una MISURA, non una frase: se il round-trip non torna, esce
in errore invece di dichiarare 'fatto'. Simboli inventati (tests/ e' pubblico)."""
import json
import os
import sys

from tools.migrations import migra_fonti_guidance as mig

SORGENTE = '''
FONTI = {
    "ACME.MI": {"nome": "Acme", "tipo": "equity", "ir_url": "https://example.invalid/ir"},
    "ORO.MI": {"nome": "Oro ETC", "tipo": "etc"},
}
'''


def test_legge_il_dict_senza_eseguire_il_modulo(tmp_path):
    f = tmp_path / "finto.py"
    f.write_text(SORGENTE, encoding="utf-8")
    d = mig.leggi_fonti(str(f))
    assert set(d) == {"ACME.MI", "ORO.MI"}


def test_riconcilia_conta_voci_e_campi(tmp_path):
    d = {"ACME.MI": {"nome": "Acme", "tipo": "equity"}}
    dest = tmp_path / "out.json"
    dest.write_text(json.dumps(d), encoding="utf-8")
    assert mig.riconcilia(d, str(dest)) == {"voci": 1, "campi": 2, "differenze": []}


def test_stato_dichiara_la_migrazione_da_fare(tmp_path):
    """L'ancora c'e' ancora: c'e' da migrare, e lo strumento deve dirlo."""
    src = tmp_path / "finto.py"
    src.write_text(SORGENTE, encoding="utf-8")
    codice, frase = mig.stato(str(src), str(tmp_path / "mai_scritto.json"))
    assert codice == "da_fare", (codice, frase)


def test_stato_dichiara_la_migrazione_GIA_ESEGUITA_invece_di_sembrare_rotta(tmp_path):
    """Dopo la migrazione il letterale non c'e' piu': lanciato nudo, lo strumento usciva
    1 con «ANCORA NON TROVATA», che chi clona il repo legge come un guasto. Deve invece
    misurare i due capi e dichiarare 'fatta'."""
    src = tmp_path / "finto.py"
    src.write_text("# il dict e' migrato: qui non c'e' piu'\n", encoding="utf-8")
    dest = tmp_path / "negozio.json"
    dest.write_text(json.dumps({"_leggimi": "servizio",
                                "ACME.MI": {"nome": "Acme", "tipo": "equity"}}),
                    encoding="utf-8")
    codice, frase = mig.stato(str(src), str(dest))
    assert codice == "fatta", (codice, frase)


def test_stato_NON_dichiara_fatta_se_il_negozio_non_c_e(tmp_path):
    """L'altra meta': niente ancora E niente negozio non e' «gia' fatta», e un 'fatta'
    li' sarebbe un fallback che dichiara compiuto cio' che non e' mai avvenuto. Vale
    anche per un negozio esistente ma senza voci."""
    src = tmp_path / "finto.py"
    src.write_text("# nessuna ancora\n", encoding="utf-8")
    assert mig.stato(str(src), str(tmp_path / "assente.json"))[0] == "rotta"
    vuoto = tmp_path / "vuoto.json"
    vuoto.write_text(json.dumps({"_leggimi": "solo servizio"}), encoding="utf-8")
    assert mig.stato(str(src), str(vuoto))[0] == "rotta"


def test_riconcilia_ACCUSA_una_voce_persa(tmp_path):
    d = {"ACME.MI": {"nome": "Acme", "tipo": "equity"}, "ORO.MI": {"nome": "O", "tipo": "etc"}}
    dest = tmp_path / "out.json"
    dest.write_text(json.dumps({"ACME.MI": d["ACME.MI"]}), encoding="utf-8")
    r = mig.riconcilia(d, str(dest))
    assert r["differenze"], "una voce persa DEVE comparire nelle differenze"
