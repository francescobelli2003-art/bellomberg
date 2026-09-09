# -*- coding: utf-8 -*-
"""LA CLASSE del difetto «usi sostituiti, import no», chiusa per TUTTO il repo.

Due volte in due giorni una chirurgia ha sostituito gli USI di un nome e non la sua
riga di importazione, e tutte e due le volte il difetto e' arrivato in fondo con la
suite verde:

  05/09  `cef_lookthrough` — `CEF_MANAGERS`: due usi, zero definizioni. Nessuna
         batteria l'aveva visto perche' TUTTE quelle che nominano `capo_block` lo
         sostituiscono con una lambda: nessuna lo ESEGUE.
  06/09  `prova_contesto_agenti` — `llm_client` usato a 759/760/817 e mai importato
         (`7c5e9b2`, «ripuntato sul nome nuovo»): `main()` moriva di NameError alla
         prima riga della cattura. I due test del file importano il modulo e provano
         le classi di supporto, quindi restavano verdi.

La difesa del 05/09 (`test_cef_lookthrough_negozio.py::test_nessun_nome_globale_non_
definito`) e' la stessa idea, ma puntata su UN file: avrebbe taciuto su questo. Qui
la stessa misura gira su ogni `.py` del repo, e per farlo NON importa niente.

**PERCHE' NON IMPORTA I MODULI.** La versione del 05/09 usa `set(vars(modulo))` e
quindi ha bisogno di importare il file che misura. Su un modulo di dominio e'
innocuo; su uno strumento di prova no — e proprio uno strumento di prova e' il
difetto del 06/09. Qui si legge solo il TESTO: la guardia e' piu' severa (non vede i
nomi iniettati a runtime) e quel verso e' voluto — piu' falsi positivi possibili,
nessun falso negativo da iniezione. Misurato il 06/09: **zero riscontri su 378 file**,
quindi la severita' in piu' oggi non costa nulla.

**COSA NON PRENDE, dichiarato invece che scoperto dopo.**
  · `modulo.ATTRIBUTO` inesistente: e' un AttributeError, non un nome libero. La
    guardia vede `modulo`, non cosa gli chiedi.
  · Un nome legato ma SBAGLIATO (l'import c'e' e punta altrove).
  · `except ... as e` con `e` usato fuori dall'handler: Python cancella `e` alla
    fine del blocco, ma `symtable` lo considera LEGATO, quindi qui risulta pulito.
    Non e' teorico: `bellomberg_api.py:2984-2986` ha oggi esattamente questa forma
    (il ripiego che dovrebbe DICHIARARE che `llm_client` non e' importabile
    esploderebbe in NameError invece di dichiararlo). L'ha trovato pyflakes, non
    questa guardia. **Chi presenta questo controllo dica che chiude i NameError da
    nome MAI legato, non i NameError in generale.**
  · Solo `.py`, e non i file in `attic/` (quarantena) ne' `app/` (TypeScript).

**PERCHE' `__conditional_annotations__` STA FRA I NOMI NOTI.** Su Python 3.12+ il
compilatore genera quel simbolo da solo nei moduli con annotazioni pigre (PEP
649/749): la prima stesura di questa guardia dava 28 file «sporchi», tutti per quel
nome. Un controllo che nasce a 28 riscontri falsi viene spento entro il giorno dopo.
"""

import builtins
import os
import symtable

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cartelle fuori misura, e il perche' di ognuna.
ESCLUSE = {
    "attic",            # quarantena reversibile: codice morto per decisione
    "app",              # TypeScript, zero .py (misurato)
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    ".venv", "venv",
    "mappa",            # vault rigenerato dall'hook
    "data", "data_pre_junction", "report", "research_notes",   # runtime
    ".playwright-mcp",
}

# Nomi che il COMPILATORE inietta: non compaiono in nessun import e non sono difetti.
DUNDER_INIETTATI = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__debug__", "__path__", "__all__", "__qualname__",
    "__module__", "__class__", "__dict__", "__annotations__",
    "__conditional_annotations__", "__type_params__", "__firstlineno__",
    "__static_attributes__",
}
NOMI_NOTI = set(dir(builtins)) | DUNDER_INIETTATI


def nomi_liberi(sorgente, nome_file):
    """I nomi che il sorgente LEGGE dallo scope globale senza mai legarli.

    `symtable` risolve gli scope come il compilatore: un nome `is_global` che non e'
    ne' assegnato ne' importato in nessuno scope del file, e non e' un builtin,
    solleva NameError nell'istante in cui quella riga viene eseguita.
    """
    tavola = symtable.symtable(sorgente, nome_file, "exec")

    definiti = set(NOMI_NOTI)

    def raccogli(t):
        for s in t.get_symbols():
            if s.is_assigned() or s.is_imported():
                definiti.add(s.get_name())
        for figlio in t.get_children():
            raccogli(figlio)

    raccogli(tavola)

    fuori = []

    def cerca(t, dove):
        for s in t.get_symbols():
            if (s.is_global() and not s.is_assigned() and not s.is_imported()
                    and s.get_name() not in definiti):
                fuori.append("%s -> %s" % (dove, s.get_name()))
        for figlio in t.get_children():
            cerca(figlio, "%s.%s" % (dove, figlio.get_name()))

    cerca(tavola, os.path.basename(nome_file)[:-3])
    return fuori


def _file_python():
    for radice, dirs, files in os.walk(RADICE):
        dirs[:] = [d for d in dirs if d not in ESCLUSE and not d.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(radice, f)


def test_nessun_file_usa_un_nome_mai_definito():
    """Un nome letto dallo scope globale e mai legato = NameError garantito.

    Misurato il 06/09: 378 file, 0 errori di sintassi, **0 riscontri**. Il numero di
    file NON e' un contratto (il repo cresce); il contratto e' lo ZERO.
    """
    misurati, illeggibili, riscontri = 0, [], {}

    for percorso in sorted(_file_python()):
        misurati += 1
        try:
            with open(percorso, encoding="utf-8") as fh:
                sorgente = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            illeggibili.append("%s (%s)" % (os.path.relpath(percorso, RADICE), e))
            continue
        try:
            fuori = nomi_liberi(sorgente, os.path.basename(percorso))
        except SyntaxError as e:
            # Un file che non compila e' un difetto piu' grave, non meno: non lo
            # nascondo dietro un `continue` silenzioso.
            riscontri[os.path.relpath(percorso, RADICE)] = ["NON COMPILA: %s" % e]
            continue
        if fuori:
            riscontri[os.path.relpath(percorso, RADICE)] = fuori

    # Una garanzia dev'essere una MISURA: se il cammino non trova niente, questo test
    # direbbe "pulito" senza aver guardato nulla.
    # LA SOGLIA E' 100, NON 200, ed e' una misura non un'opinione: questo file ESCE nel
    # tree pubblico, dove i `.py` sono **243** e non 378 (misurato il 06/09 con
    # `export_pubblico.seleziona`). Una soglia a 200 avrebbe lasciato 43 file di margine
    # e sarebbe diventata rossa nel clone il giorno in cui qualcuno restringe l'export —
    # cioe' un test che cade a casa d'altri per una ragione che non c'entra col difetto
    # che sorveglia. La soglia serve a intercettare il cammino ROTTO (zero o quasi), non
    # a certificare la dimensione del repo.
    assert misurati > 100, (
        "la guardia ha misurato solo %d file: il cammino e' rotto e lo ZERO qui "
        "sotto non varrebbe niente (per riferimento: 378 nel repo completo il 06/09, "
        "243 nel tree pubblico)" % misurati)

    assert not illeggibili, "file .py non leggibili: %s" % "; ".join(illeggibili)

    assert not riscontri, (
        "nomi usati e mai definiti — NameError garantito appena quella riga viene "
        "eseguita (e' il difetto del 05/09 e del 06/09, in forma generale):\n"
        + "\n".join("  %s: %s" % (f, ", ".join(v)) for f, v in sorted(riscontri.items()))
        + "\nCura: importare o definire il nome accanto ai suoi usi. Se il nome "
          "arriva davvero a runtime da fuori (globals, plugin), aggiungilo a "
          "NOMI_NOTI DICHIARANDO da dove viene.")
