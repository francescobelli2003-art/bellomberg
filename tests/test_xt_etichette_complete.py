"""Ogni etichetta Excel scritta con `label('...')` di i18n_excel ha la sua voce nel dizionario.

`i18n_excel.label(key)` fa `LABELS[key]`: un letterale cambiato in un generatore senza cambiare
la chiave (o il contrario) e' un KeyError al primo giro di QUEL ramo, e molti rami dei
generatori (legacy, dati mancanti, casi rari) non li esegue nessun test. La guardia e'
statica: legge i sorgenti con `ast`, non esegue i generatori, e vale anche per i rami che
nessuna batteria raggiunge.

Cosa NON copre, e lo dichiara fermandosi: una chiamata con argomento non letterale (non si
controlla senza eseguirla), un import del modulo in una forma diversa da
`from ...i18n_excel import label [as alias]` (anche relativa: le sue chiamate sfuggirebbero al
censimento) e l'alias di `label` usato fuori da una chiamata diretta (assegnato o passato come
valore). Limite dichiarato: un import dinamico (`importlib.import_module`) non si vede con ast.
"""
import ast
from pathlib import Path

from bellomberg.reporting.i18n_excel import LABELS

SRC = Path(__file__).resolve().parents[1] / 'src' / 'bellomberg'
DIZIONARIO = 'reporting/i18n_excel.py'
# Pavimento, non l'elenco completo: un modulo nuovo che importa label entra da solo nel
# censimento; uno di questi che sparisse dal censimento vuol dire scansione rotta, non pulita.
MODULI_NOTI = {
    'valuation/dcf_bank.py', 'valuation/dcf_buyside_v3.py', 'valuation/dcf_engine.py',
    'valuation/dcf_mnav.py', 'valuation/dcf_modeler.py', 'valuation/dcf_quality_sheet.py',
    'valuation/dcf_rab.py', 'valuation/documented_inputs.py', 'valuation/managed_care_adapter.py',
}


def _e_il_dizionario(nome_modulo):
    return bool(nome_modulo) and nome_modulo.split('.')[-1] == 'i18n_excel'


def censisci(sorgente, rel):
    """(chiamate, non_letterali, forme_non_coperte, letterali) di UN sorgente.
    letterali = [(rel:riga, testo)] delle chiamate all'alias di `label` con un solo str letterale."""
    albero = ast.parse(sorgente, filename=rel)
    alias, forme = set(), []
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.ImportFrom) and _e_il_dizionario(nodo.module):
            for nome in nodo.names:
                if nome.name == 'label':
                    alias.add(nome.asname or 'label')
                else:
                    forme.append('%s:%d import di %s' % (rel, nodo.lineno, nome.name))
        elif isinstance(nodo, ast.Import):
            for nome in nodo.names:
                if _e_il_dizionario(nome.name):
                    forme.append('%s:%d import del modulo intero' % (rel, nodo.lineno))
        elif isinstance(nodo, ast.ImportFrom) and any(
                n.name == 'i18n_excel' for n in nodo.names):
            forme.append('%s:%d import del modulo intero' % (rel, nodo.lineno))
    chiamate, non_letterali, letterali = 0, [], []
    funzioni = {id(n.func) for n in ast.walk(albero) if isinstance(n, ast.Call)}
    for nodo in ast.walk(albero):
        if (isinstance(nodo, ast.Name) and nodo.id in alias and isinstance(nodo.ctx, ast.Load)
                and id(nodo) not in funzioni):
            forme.append('%s:%d alias di label usato fuori da una chiamata' % (rel, nodo.lineno))
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id in alias:
            chiamate += 1
            arg = nodo.args[0] if len(nodo.args) == 1 and not nodo.keywords else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                letterali.append(('%s:%d' % (rel, nodo.lineno), arg.value))
            else:
                non_letterali.append('%s:%d' % (rel, nodo.lineno))
    return chiamate, non_letterali, forme, letterali


def chiavi_mancanti(letterali):
    """Il confronto col dizionario vero, in UN posto: le prove lo usano come il test principale."""
    return [dove for dove, testo in letterali if testo not in LABELS]


def censimento(radice=SRC):
    moduli, non_letterali, forme, letterali = {}, [], [], []
    for percorso in sorted(radice.rglob('*.py')):
        rel = percorso.relative_to(radice).as_posix()
        if rel == DIZIONARIO:
            continue
        n, nl, fo, le = censisci(percorso.read_text(encoding='utf-8'), rel)
        if n or fo:
            moduli[rel] = n
        non_letterali += nl; forme += fo; letterali += le
    return moduli, non_letterali, forme, letterali


def test_ogni_letterale_ha_la_sua_chiave_nel_dizionario():
    moduli, non_letterali, forme, letterali = censimento()
    assert MODULI_NOTI <= set(moduli), sorted(MODULI_NOTI - set(moduli))
    assert all(moduli[m] > 0 for m in MODULI_NOTI), moduli
    assert letterali and len(letterali) == sum(moduli.values())
    assert forme == [], forme
    assert non_letterali == [], non_letterali
    mancanti = chiavi_mancanti(letterali)
    assert mancanti == [], mancanti


def test_il_censimento_vede_la_chiave_mancante_e_le_forme_che_non_copre():
    """Il censimento su sorgenti sintetici: senza questa prova un censimento che non trova
    nulla passerebbe per un albero pulito."""
    presente = next(iter(LABELS))
    sorgente = (
        "from bellomberg.reporting.i18n_excel import label as etichetta\n"
        "etichetta(%r)\n"
        "etichetta('Zorvex chiave sintetica assente')\n"
        "etichetta(nome_variabile)\n" % presente)
    n, non_letterali, forme, letterali = censisci(sorgente, 'sintetico.py')
    assert n == 3 and non_letterali == ['sintetico.py:4'] and forme == []
    assert chiavi_mancanti(letterali) == ['sintetico.py:3']
    _, _, forme, _ = censisci(
        "import bellomberg.reporting.i18n_excel\n"
        "from bellomberg.reporting import i18n_excel\n"
        "from bellomberg.reporting.i18n_excel import LABELS\n"
        "from . import i18n_excel\n"
        "from .. import i18n_excel\n"
        "from bellomberg.reporting.i18n_excel import label\n"
        "_t = label\n"
        "mappa(label, righe)\n", 'forme.py')
    assert forme == ['forme.py:1 import del modulo intero', 'forme.py:2 import del modulo intero',
                     'forme.py:3 import di LABELS', 'forme.py:4 import del modulo intero',
                     'forme.py:5 import del modulo intero',
                     'forme.py:7 alias di label usato fuori da una chiamata',
                     'forme.py:8 alias di label usato fuori da una chiamata']


def test_il_censimento_vede_una_chiave_mancante_in_un_generatore_vero(tmp_path):
    """Cablaggio sul formato di un modulo VERO: la prima chiamata letterale di dcf_buyside_v3
    cambiata in una copia fuori dall'albero diventa una chiave mancante alla sua riga."""
    rel = 'valuation/dcf_buyside_v3.py'
    sorgente = (SRC / rel).read_text(encoding='utf-8')
    _, _, _, letterali = censisci(sorgente, rel)
    semplici = sorted((int(d.rsplit(':', 1)[1]), d, t) for d, t in letterali
                      if t.isprintable() and not set(t) & set('\'"\\'))
    assert semplici, 'nessun letterale semplice in ' + rel
    riga, dove, testo = semplici[0]
    righe = sorgente.split('\n')
    virgolette = [q for q in ("'", '"') if righe[riga - 1].count(q + testo + q) == 1]
    assert len(virgolette) == 1, dove
    q = virgolette[0]
    righe[riga - 1] = righe[riga - 1].replace(q + testo + q, q + 'Zorvex ' + testo + q)
    copia = tmp_path / 'valuation' / 'dcf_buyside_v3.py'
    copia.parent.mkdir(parents=True)
    copia.write_text('\n'.join(righe), encoding='utf-8')
    _, _, _, mutati = censimento(tmp_path)
    assert chiavi_mancanti(mutati) == [dove]
