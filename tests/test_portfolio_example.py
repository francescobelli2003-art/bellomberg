"""B5 (02/09, pubblicazione): primo avvio a book vuoto. Il DB nasce da solo
(MemoryDB.__init__), la cassa no: vive in portfolio.json, gitignored. Il file
d'esempio parte da cassa 0 DICHIARATA (cash_source «portfolio.json», non un buco).
"""
import json
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESEMPIO = os.path.join(REPO, "src", "bellomberg", "resources", "examples",
                       "portfolio.example.json")


def test_esempio_parsa_e_ha_solo_cassa_zero():
    d = json.load(open(ESEMPIO, encoding="utf-8"))
    assert d["cash_disponibile_eur"] == 0
    assert set(d) <= {"cash_disponibile_eur", "_leggimi"}, set(d)


def test_esempio_e_una_cassa_misurata_non_un_buco():
    # L'esempio non e' più una fonte runtime: la cassa nasce solo da un primo
    # DEPOSIT esplicito o dalla migrazione controllata.
    d = json.load(open(ESEMPIO, encoding="utf-8"))
    assert d["cash_disponibile_eur"] == 0


def test_i_lettori_della_cassa_non_chiedono_altre_chiavi():
    """Chi legge portfolio.json con _pj.get("...")/_pj["..."] o dati["..."] deve
    chiedere solo chiavi dell'esempio: altrimenti l'esempio e' incompleto.

    PERIMETRO (review 02/09): i lettori della CASSA. `memory_db.import_from_json`
    / `import_hybrid` (erano i comandi import-json/import-hybrid di manage_portfolio, oggi in attic) leggono
    `etf`/`azioni` dello schema di maggio e sono ESCLUSI apposta: con l'esempio
    (solo cassa) import-hybrid azzererebbe il DB senza importare nulla — voce
    APERTA in MASTER §9-duoquinquagies, il 02/09 lo strumento e' andato in attic e `_leggimi` non lo nomina piu'."""
    chiavi = set()
    for f in ("src/bellomberg/storage/memory_db.py",
              "src/bellomberg/api/bellomberg_api.py",
              "src/bellomberg/agents/agent_tools.py",
              "src/bellomberg/portfolio/portfolio_analytics.py"):
        t = open(os.path.join(REPO, f), encoding="utf-8").read()
        for k in re.findall(r"""(?:_pj|dati)(?:\.get\(|\[)\s*["']([a-z_]+)["']""", t):
            chiavi.add(k)
    assert chiavi <= {"cash_disponibile_eur"}, chiavi


def test_esempio_tracciato_e_vero_gitignored():
    ign_es = subprocess.run(["git", "check-ignore", "portfolio.example.json"], cwd=REPO,
                            capture_output=True, text=True, encoding="utf-8").returncode
    assert ign_es == 1, "portfolio.example.json NON deve essere ignorato"
    ign_vero = subprocess.run(["git", "check-ignore", "portfolio.json"], cwd=REPO,
                              capture_output=True, text=True, encoding="utf-8").returncode
    assert ign_vero == 0, "portfolio.json DEVE restare ignorato"
