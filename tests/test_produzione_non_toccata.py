# -*- coding: utf-8 -*-
"""I presidi del conftest devono FUNZIONARE, non solo esistere (21/08).

`tests/conftest.py` ha dal 26/07 un presidio `(a-ter)` che redirige il Chroma:

    monkeypatch.setattr(memory_db, "CHROMA_PATH", str(tmp_path / "chroma"))

**Non funzionava.** `MemoryDB.__init__(self, db_path=SQLITE_PATH, chroma_path=CHROMA_PATH)`
lega i default **alla definizione della classe**, quindi riassegnare la costante del
modulo non li tocca:

    memory_db.CHROMA_PATH      -> C:\\Temp\\FINTO\\chroma   (patchata)
    __init__.__defaults__      -> ('data\\consigliere.db', 'data\\chroma')  (INVARIATO)

Chi ci passava: i due `MemoryDB(db_path)` di `tests/test_prezzi_freschezza.py`, gli
unici che non passano `chroma_path`. Il tripwire sul DB non scatta, perche' quel
`db_path` e' un tmp legittimo: **il presidio cadeva nell'uso normale e corretto**,
non in quello patologico.

MISURATO sul file del PM (`data/chroma/chroma.sqlite3`, 6.377.472 byte):

    PRIMA sha= 67a062c2c8957273
    pytest tests/test_prezzi_freschezza.py -q  ->  16 passed
    DOPO  sha= 1e154d2b2ff3c11c

Cioe' ogni giro di suite riscriveva lo store vettoriale di PRODUZIONE. Stessa
famiglia dell'heartbeat di F4 chiuso lo stesso giorno, e dello scorekeeper del 25/07.

La cura non e' nel conftest ma alla RADICE: i default si risolvono ora **a ogni
chiamata**, cosi' la costante del modulo e' davvero la fonte di verita' e chiunque la
rediriga (conftest, strumenti di misura, script) viene ascoltato.
"""
import os

from bellomberg.storage import memory_db


def test_CHROMA_PATH_e_letta_alla_CHIAMATA_non_congelata_nel_default(tmp_path, monkeypatch):
    """Il difetto in una riga: una costante di modulo che nessuno puo' redirigere
    non e' una costante di configurazione, e' un letterale con un bel nome."""
    finto = str(tmp_path / "chroma_finto")
    monkeypatch.setattr(memory_db, "CHROMA_PATH", finto)
    visti = []

    class _FintoClient:
        def __init__(self, path=None, **kw):
            visti.append(path)

        def get_or_create_collection(self, *a, **k):
            class _C:
                def add(self, *a, **k):
                    pass

                def query(self, *a, **k):
                    return {}

                def count(self):
                    return 0
            return _C()

    import chromadb
    monkeypatch.setattr(chromadb, "PersistentClient", _FintoClient)
    memory_db.MemoryDB(db_path=str(tmp_path / "prova.db"))

    assert visti, "chromadb non e' stato aperto: il test non misura piu' nulla"
    for p in visti:
        assert os.path.normcase(str(p)) == os.path.normcase(finto), (
            "MemoryDB ha aperto %r invece del path redirezionato %r: il presidio "
            "(a-ter) del conftest e' inefficace e la suite scrive sul Chroma di "
            "PRODUZIONE" % (p, finto))


def test_SQLITE_PATH_e_letta_alla_CHIAMATA_non_congelata_nel_default(monkeypatch):
    """Stessa classe sul db_path. Qui il tripwire del conftest copre gia' il danno
    (patcha `connect_sqlite`, non il path), ma il difetto di forma e' identico e
    lasciarlo aperto significa che la prossima costante rediretta non funzionera'."""
    monkeypatch.setattr(memory_db, "SQLITE_PATH", "percorso_finto_di_prova.db")
    import inspect
    firma = inspect.signature(memory_db.MemoryDB.__init__)
    assert firma.parameters["db_path"].default is None, (
        "db_path ha ancora un default congelato: %r"
        % firma.parameters["db_path"].default)
    assert firma.parameters["chroma_path"].default is None, (
        "chroma_path ha ancora un default congelato: %r"
        % firma.parameters["chroma_path"].default)
