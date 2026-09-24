"""B7 (02/09, pubblicazione): ogni pacchetto di terze parti importato dal codice di
prodotto sta in requirements.txt. Il 31/08 la CI era rossa perche' pypdf mancava e
lettore_trimestrali lo importa dentro un try che chiama «illeggibile» anche il
ModuleNotFoundError; pydantic e' importato direttamente ma arrivava solo come
dipendenza di FastAPI.

Perimetro: i launcher Python in radice e il package ``src/bellomberg``. Test,
archivio privato, prototipi, QA e tool operativi non sono dipendenze del prodotto.
Import opzionali dichiarati nel codice (try/except con feature spenta) stanno in
OPZIONALI. Legge i nodi import dell'AST, anche annidati; ignora testo e import
relativi. Non vede importlib con nome in variabile.
"""
import ast
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# nome importato -> nome della distribuzione in requirements
ALIAS = {"dotenv": "python-dotenv", "yaml": "pyyaml", "PIL": "pillow", "sklearn": "scikit-learn",
         "bs4": "beautifulsoup4", "dateutil": "python-dateutil", "ib_insync": "ib_async"}
# import opzionali dichiarati nel codice (try/except con feature spenta): non obbligatori
OPZIONALI = {"chromadb", "ib_async", "ib_insync", "win32com", "pythoncom", "pywintypes"}


def _file_py():
    files = [n for n in os.listdir(REPO) if n.endswith(".py")]
    package = os.path.join(REPO, "src", "bellomberg")
    for radice, _dirs, nomi in os.walk(package):
        for nome in nomi:
            if nome.endswith(".py"):
                files.append(os.path.relpath(os.path.join(radice, nome), REPO))
    return sorted(f.replace("\\", "/") for f in files)


def _moduli_locali(files):
    return {os.path.splitext(os.path.basename(f))[0] for f in files} | {"bellomberg"}


def _nomi_importati(testo):
    nomi = set()
    for node in ast.walk(ast.parse(testo)):
        if isinstance(node, ast.Import):
            nomi.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            nomi.add(node.module.split('.')[0])
    return nomi


def _importati():
    files = _file_py()
    nomi = set()
    for f in files:
        testo = open(os.path.join(REPO, f), encoding="utf-8", errors="replace").read()
        nomi.update(_nomi_importati(testo))
    return nomi - set(sys.stdlib_module_names) - _moduli_locali(files) - OPZIONALI


def _requirements():
    req = set()
    for riga in open(os.path.join(REPO, "requirements.txt"), encoding="utf-8"):
        if riga.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*([A-Za-z0-9_.\-]+)", riga)
        if m:
            req.add(m.group(1).lower().replace("_", "-").split("[")[0])
    return req


def _normalizza(nome):
    return ALIAS.get(nome, nome).lower().replace("_", "-")


def test_import_inventory_reads_python_statements_not_prompt_text():
    source = '''\
"""from invented prose, not a Python import"""
PROMPT = """\nfrom the fixed proposed assumptions\nimport fictional\n"""
import os, json as serialization
from pathlib import Path
from .relative import helper
def function():
    try:
        from provider.client import Client
    except ImportError:
        pass
'''
    assert _nomi_importati(source) == {'os', 'json', 'pathlib', 'provider'}


def test_ogni_import_di_terze_parti_e_in_requirements():
    req = _requirements()
    mancanti = sorted(n for n in _importati() if _normalizza(n) not in req)
    assert not mancanti, f"importati dal prodotto ma assenti da requirements.txt: {mancanti}"


def test_requirements_non_elenca_pacchetti_che_nessuno_importa():
    """Il verso contrario: una riga di requirements che nessun modulo importa e' un
    peso per chi installa. `uvicorn` e' l'eccezione dichiarata (lo lancia
    bellomberg_api a runtime, non lo importa a inizio riga)."""
    usati = {_normalizza(n) for n in _importati()} | {_normalizza(n) for n in OPZIONALI} | {"uvicorn"}
    inutili = sorted(r for r in _requirements() if r not in usati)
    assert not inutili, f"in requirements.txt ma nessun modulo di prodotto li importa: {inutili}"
