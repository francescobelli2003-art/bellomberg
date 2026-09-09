"""_spia_scritture.py — la SPIA DELLE SCRITTURE della suite (22/08 sera-2).

Voce (1b) del MASTER, Fase A («misura, poi il numero», ok PM). Registra ogni
apertura in scrittura, creazione di cartella, rename/replace e cancellazione
che un test fa DENTRO gli alberi di produzione del repo — senza bloccare nulla:
il tripwire (Fase B) e' una decisione del PM che arriva DOPO aver visto il
numero. Discende dalla `SpiaFile` di `prova_edge_scan.py` (22/08) con tre
estensioni misurate come necessarie: `io.open` (nome diverso da `builtins.open`:
patchare uno non cambia l'altro — e' la via di zip/pathlib/`market_inputs`),
le CANCELLAZIONI (`os.remove`/`unlink`/`rmdir`), e `portfolio.json` in RADICE,
che sta fuori dai tre prefissi censiti.

Alberi sorvegliati: `data/` (junction → C:\\BellombergData: si confronta il
REALPATH), `report/`, `research_notes/`, piu' il file `portfolio.json` in radice.
Esenti: i percorsi passati in `esenti` (in produzione: il tmp di pytest e il
tmp di sistema).

LIMITE DICHIARATO — e scritto nel rapporto, non solo qui: `os.open` a basso
livello e chi scrive da C (sqlite3, chromadb) non passano da questi nomi
Python. Lo sqlite ha il suo tripwire in conftest (b).
"""
import builtins
import io
import os
from typing import Any, Dict, List, Optional

ALBERI_SORVEGLIATI = ("data", "report", "research_notes")
FILE_SORVEGLIATI = ("portfolio.json",)
LIMITE = ("NON vede: os.open a basso livello e le scritture fatte da C "
          "(sqlite3, chromadb) — lo sqlite ha il tripwire dedicato in conftest.")


class ScritturaInProduzione(BaseException):
    """Un test ha provato a scrivere in produzione (FASE B, ok PM 22/08 sera-2).

    Deriva da **BaseException**, non da Exception, di proposito — come
    `conftest.ProduzioneToccata`: i punti che scrivono stanno spesso dentro
    `try/except Exception: pass`, e un'eccezione normale verrebbe inghiottita
    lasciando il tripwire MUTO. Si solleva PRIMA di chiamare l'originale: il
    file non nasce.
    """


def _reale(p) -> str:
    """Path confrontabile su Windows: junction risolta, maiuscole e slash
    normalizzati (stessa forma di `tests/conftest._reale`)."""
    return os.path.normcase(os.path.realpath(str(p)))


class SpiaScritture:
    def __init__(self, radice: str, esenti: Optional[List[str]] = None,
                 tripwire: bool = False, alberi_extra: Optional[List[str]] = None):
        # tripwire=False e' la FASE A (REGISTRA: misura e lascia fare);
        # tripwire=True e' la FASE B (il test che scrive FALLISCE). Il default
        # resta la misura: chi vuole mordere lo dice — lo dice il conftest.
        self.tripwire = bool(tripwire)
        self.radice = _reale(radice)
        self.alberi = [os.path.join(self.radice, d) + os.sep for d in ALBERI_SORVEGLIATI]
        # B4 (02/09): la cartella dati puo' stare fuori dal repo (BELLOMBERG_DATA_DIR)
        self.alberi += [_reale(a) + os.sep for a in (alberi_extra or [])]
        self.file = [os.path.join(self.radice, f) for f in FILE_SORVEGLIATI]
        self.esenti = [_reale(e) + os.sep for e in (esenti or [])]
        self.scritture: List[Dict[str, Any]] = []
        self.test_corrente: Optional[str] = None
        self._orig: Dict[str, Any] = {}
        self._installata = False
        self._dentro_makedirs = False

    # ---- classificazione -------------------------------------------------
    def _nostro(self, percorso) -> bool:
        if percorso is None:
            return False
        try:
            a = _reale(percorso)
        except Exception:
            return False
        for e in self.esenti:
            if a.startswith(e):
                return False
        if a in self.file:
            return True
        return any(a.startswith(d) for d in self.alberi)

    def _registra(self, op: str, percorso, modo: Optional[str] = None):
        # fuori da un test (import a livello modulo, collection) il nodeid non
        # c'e': si etichetta, non si lascia un None che nel rapporto sembra un
        # test chiamato «None»
        ev = {"op": op, "percorso": str(percorso),
              "test": self.test_corrente or "<fuori da un test: import/collection>"}
        if modo is not None:
            ev["modo"] = modo
        self.scritture.append(ev)
        if self.tripwire:
            # l'evento e' gia' nel rapporto (si vede cosa ha fatto scattare il
            # tripwire); si solleva PRIMA che l'originale scriva.
            raise ScritturaInProduzione(
                "un test ha provato a SCRIVERE IN PRODUZIONE: %s %s%s (test: %s). "
                "La suite e' offline per contratto: scrivi sotto tmp_path, oppure "
                "redirigi il path come fanno i presidi (a) di tests/conftest.py "
                "(SNAP_PATH, CHROMA_PATH, HEARTBEAT_PATH, CACHE_DIR). Per la sola "
                "misura senza fermare nulla: BELLOMBERG_SPIA_SCRITTURE=registra."
                % (op, ev["percorso"], " (modo %s)" % modo if modo else "",
                   ev["test"]))

    # ---- installazione ---------------------------------------------------
    def installa(self):
        if self._installata:
            return self
        spia = self
        self._orig = {
            "builtins.open": builtins.open, "io.open": io.open,
            "os.makedirs": os.makedirs, "os.mkdir": os.mkdir,
            "os.replace": os.replace, "os.rename": os.rename,
            "os.remove": os.remove, "os.unlink": os.unlink, "os.rmdir": os.rmdir,
        }
        _open = self._orig["builtins.open"]

        def open_spia(file, mode="r", *a, **k):
            if any(c in str(mode) for c in "wax+") and spia._nostro(file):
                spia._registra("open", file, str(mode))
            return _open(file, mode, *a, **k)

        def _wrap(nome, op, quale_arg=0):
            orig = spia._orig[nome]

            def f(*a, **k):
                bersaglio = a[quale_arg] if len(a) > quale_arg else None
                if spia._nostro(bersaglio) and not spia._dentro_makedirs:
                    spia._registra(op, bersaglio)
                return orig(*a, **k)
            return f

        # `os.makedirs` chiama `os.mkdir` per ogni livello: senza questo flag
        # la stessa cartella usciva DUE volte (makedirs + mkdir) — misurato
        # dal test della spia, non supposto.
        _makedirs_orig = spia._orig["os.makedirs"]

        def makedirs_spia(name, *a, **k):
            if spia._nostro(name):
                spia._registra("makedirs", name)
            spia._dentro_makedirs = True
            try:
                return _makedirs_orig(name, *a, **k)
            finally:
                spia._dentro_makedirs = False

        builtins.open = open_spia
        io.open = open_spia
        os.makedirs = makedirs_spia
        os.mkdir = _wrap("os.mkdir", "mkdir")
        os.replace = _wrap("os.replace", "replace", quale_arg=1)
        os.rename = _wrap("os.rename", "rename", quale_arg=1)
        os.remove = _wrap("os.remove", "remove")
        os.unlink = _wrap("os.unlink", "unlink")
        os.rmdir = _wrap("os.rmdir", "rmdir")
        self._installata = True
        return self

    def disinstalla(self):
        if not self._installata:
            return
        builtins.open = self._orig["builtins.open"]
        io.open = self._orig["io.open"]
        os.makedirs = self._orig["os.makedirs"]
        os.mkdir = self._orig["os.mkdir"]
        os.replace = self._orig["os.replace"]
        os.rename = self._orig["os.rename"]
        os.remove = self._orig["os.remove"]
        os.unlink = self._orig["os.unlink"]
        os.rmdir = self._orig["os.rmdir"]
        self._installata = False

    # ---- rapporto --------------------------------------------------------
    def rapporto(self) -> str:
        """Per file: quante scritture e quanti test distinti. Chiude SEMPRE col
        limite dichiarato: uno zero che non nomina cio' che non vede mente."""
        righe = []
        per_file: Dict[str, Dict[str, Any]] = {}
        for ev in self.scritture:
            d = per_file.setdefault(ev["percorso"], {"n": 0, "test": set(), "op": set()})
            d["n"] += 1
            d["test"].add(ev["test"])
            d["op"].add(ev["op"])
        n_test = len({ev["test"] for ev in self.scritture})
        righe.append("SPIA SCRITTURE: %d scritture fuori dal tmp, da %d test, su %d file"
                     % (len(self.scritture), n_test, len(per_file)))
        for p, d in sorted(per_file.items(), key=lambda kv: -kv[1]["n"]):
            righe.append("  %4d x %-10s %s  [%d test]"
                         % (d["n"], "/".join(sorted(d["op"])), p, len(d["test"])))
        righe.append("  " + LIMITE)
        return "\n".join(righe)
