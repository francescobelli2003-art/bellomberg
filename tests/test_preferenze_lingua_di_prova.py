# -*- coding: utf-8 -*-
"""La lingua della suite non la decide `data/preferences.json` di chi la lancia (13/09, Claude Opus 5).

`capture_language()` senza contesto legge la preferenza SALVATA: senza la fixture autouse
`preferenze_di_prova` del conftest, i test che pretendono frasi italiane sono verdi solo finche'
chi lancia la suite tiene «it». Prove di natura diversa:
  1. il CABLAGGIO: in un test qualunque il file letto sta nel tmp del test e non e' quello di
     `paths.DATA_DIR` — l'oracolo viene da `paths`, non dal conftest sotto prova;
  2. la SENSIBILITA': con «en» nel file che il modulo legge, le stesse frasi escono inglesi —
     senza questa la 1 potrebbe dirottare un file che non decide niente;
  3. l'ESITO: in un test qualunque le frasi sono italiane per la ragione DICHIARATA (preferenza
     assente = `compatibility_default`), non per la scelta salvata da qualcuno;
  4. il GENERATORE dell'esempio committato resta italiano anche con «en» salvato: l'oracolo e'
     il file committato, non il codice sotto prova.
Mutazione da prendere: togliere la `setattr` dalla fixture. La 1 cade ovunque; la 3 cade solo
dove chi lancia HA un file salvato (in un clone senza file passerebbe comunque): per questo la
prova del cablaggio confronta i percorsi e non le frasi.
Nessun numero del book: il profilo e' l'esempio committato del repo.
"""
import copy
import json
import os

from bellomberg.core import language
from bellomberg.core import mandato_pm as mp
from bellomberg.core import paths
from bellomberg.storage import preferences

TESTA_CASSA_IT = "## GESTIONE DEL CASH (regola attiva, IMPIEGO NEUTRO)"
TESTA_CASSA_EN = "## CASH MANAGEMENT (active rule, NEUTRAL DEPLOYMENT)"


def _reale(p):
    return os.path.normcase(os.path.realpath(str(p)))


def _frasi():
    """Tre uscite che la suite pretende in una lingua: la sezione cassa per i modelli, l'errore
    di un intervallo rovesciato e la lingua dell'anteprima."""
    esempio = mp.profilo_esempio()
    rovesciato = copy.deepcopy(esempio)
    rovesciato["cassa"]["cassa_tipica_pct"] = [15, 10]
    _, errori = mp.valida(rovesciato)
    return mp.sezioni(esempio)["cassa"], "\n".join(errori), mp.anteprima(esempio)["output_language"]


def _inglese_salvato(tmp_path, monkeypatch):
    salvato = tmp_path / "chi_lancia" / "preferences.json"
    salvato.parent.mkdir()
    salvato.write_text(json.dumps({"language": "en"}), encoding="utf-8")
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", salvato)


def test_il_file_delle_preferenze_sta_nel_tmp_del_test_non_nei_dati_di_chi_lancia(tmp_path):
    letto = _reale(preferences.PREFERENCES_PATH)
    assert letto != _reale(paths.DATA_DIR / "preferences.json"), (
        "la suite legge la preferenza di chi la lancia: la fixture autouse "
        "preferenze_di_prova del conftest non ha girato")
    assert letto.startswith(_reale(tmp_path) + os.sep), letto
    assert not preferences.PREFERENCES_PATH.exists(), "la fixture deve lasciare il file ASSENTE"


def test_un_inglese_salvato_cambia_le_frasi_quindi_dirottare_il_file_conta(tmp_path, monkeypatch):
    _inglese_salvato(tmp_path, monkeypatch)
    assert language.capture_language() == "en"
    cassa, errori, lingua = _frasi()
    assert cassa.startswith(TESTA_CASSA_EN), cassa[:80]
    assert "minimum <= maximum" in errori and "minimo <= massimo" not in errori, errori
    assert lingua == "en"


def test_le_frasi_della_suite_sono_italiane_per_la_ragione_dichiarata():
    assert preferences.read_preferences() == {
        "language": "it", "selected": False, "source": "compatibility_default"}, (
        "la lingua viene da una preferenza SALVATA, non dallo stato dichiarato del primo avvio")
    cassa, errori, lingua = _frasi()
    assert cassa.startswith(TESTA_CASSA_IT), cassa[:80]
    assert "minimo <= massimo" in errori, errori
    assert lingua == "it"


def test_l_esempio_committato_si_rigenera_italiano_anche_con_inglese_salvato(tmp_path, monkeypatch):
    """`scrivi_esempio` (lanciato da `python -m bellomberg.core.mandato_pm --scrivi-esempio`)
    rendeva le descrizioni dei campi nella preferenza di chi lo lancia: con «en» salvato avrebbe
    riscritto in inglese il file di esempio committato, che e' italiano."""
    _inglese_salvato(tmp_path, monkeypatch)
    with open(mp.ESEMPIO_MANDATO, encoding="utf-8") as fh:
        committato = json.load(fh)
    generato = json.loads(open(mp.scrivi_esempio(str(tmp_path / "esempio.json")), encoding="utf-8").read())
    assert generato["_campi"] == committato["_campi"]
