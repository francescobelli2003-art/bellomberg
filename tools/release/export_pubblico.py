# -*- coding: utf-8 -*-
"""export_pubblico.py — dal repo privato al tree del repo pubblico (pubblicazione P2, 02/09).

  1. file TRACCIATI da git che combaciano con tools/release/policy/ALLOWLIST.txt
     (un percorso o glob per riga; '!pattern' toglie; cio' che non e' nominato non esce;
     un pattern positivo senza riscontro = lista stantia = STOP)
  2. STOP se uno dei file scelti e' modificato o staged (l'export corrisponde a un commit)
  3. copia byte per byte (copy2: i .bat restano CRLF) in una cartella temporanea
  4. cancello: tools/release/verifica_pubblico.py sul tree (exit != 0 = STOP)
  5. suite: su una COPIA verificata del tree, i passi del job `test` della CI (PASSI_SUITE: npm ci,
     pytest, tsc, test:release, build:bundles, contratti del mandato), TUTTI anche dopo un rosso,
     ognuno col suo timeout ed esito OK/KO/NON ESEGUITO (13/09; un passo KO = STOP, ogni rosso
     locale blocca il deposito; --senza-suite la salta e il riepilogo lo DICE)
  6. riepilogo: file, byte, esito per controllo, suite
  7. solo con --dest <clone pubblico> --commit: svuota il working tree del clone (tranne .git),
     copia il tree, git add -A, UN commit 'sync YYYY-MM-DD (privato <hash>)' con l'autore del
     git config del clone, la cui email DEVE essere @users.noreply.github.com (misurato).
     MAI push: lo lancia la chat su ordine esplicito del PM.
  D6 (04/09): con --dest, subito dopo l'autore e PRIMA di copiare, si misura la STORIA del clone
     contro tools/release/policy/STORIA.txt (registro privato degli hash depositati, INTERI): un commit che
     l'export non ha prodotto, un ramo locale oltre al corrente, un refs/pull, un tag, HEAD
     staccato, un origin/<ramo> non registrato = STOP. Un clone la cui storia l'export non ha
     prodotto e' il caso che questa guardia ferma: il repo pubblico si crea VUOTO e si clona da
     li'. Dopo ogni deposito l'hash nuovo va nel registro.

Uso:  python tools/release/export_pubblico.py                       # dry-run
      python tools/release/export_pubblico.py --tieni --corpus-root <snapshot_privato>
      python tools/release/export_pubblico.py --tieni                # lascia il temporaneo
      python tools/release/export_pubblico.py --dest <clone del repo NUOVO> --commit

Stato (T7 del piano): completo. Il cancello e' tools/release/verifica_pubblico.py (`CONTROLLI`);
il push non lo fa questo script, mai: e' P4 e lo ordina il PM.
"""
import argparse
import collections
import datetime as _dt
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

if __package__:
    from . import verifica_pubblico as vp
    from .verifica_deposito import valida_manifest, certifica_deposito, prepara_guardia
else:  # Direct CLI execution: Python supplies this script's directory.
    import verifica_pubblico as vp
    from verifica_deposito import valida_manifest, certifica_deposito, prepara_guardia

REPO = vp.REPO
PUBBLICO = vp.PUBBLICO
ALLOWLIST = os.path.join(PUBBLICO, "ALLOWLIST.txt")
REGISTRO = os.path.join(PUBBLICO, "STORIA.txt")   # D6: gli hash dei commit che l'export ha DEPOSITATO


def _run(args, cwd, check=True):
    return subprocess.run(args, cwd=cwd, capture_output=True, encoding="utf-8",
                          errors="replace", check=check)


def file_tracciati(repo):
    """I percorsi che git conosce (index), in forma posix: i non tracciati non escono mai."""
    out = _run(["git", "ls-files", "-z"], repo).stdout
    return [f for f in out.split("\0") if f]


def seleziona(tracciati, righe_allowlist):
    """(scelti, pattern senza riscontro). Un file esce se combacia con almeno un pattern
    positivo e con nessun '!pattern'. Una riga che non nomina nulla e' stantia e chi chiama
    deve fermarsi: vale per i positivi (non esce niente di piu', ma la lista mente) e —
    review T1 — per i negativi (un '!' scritto male manderebbe i file privati nel tree e a
    reggere resterebbe solo ESCLUSI); i negativi vuoti tornano col loro '!' davanti."""
    positivi = [r for r in righe_allowlist if not r.startswith("!")]
    negativi = [r[1:] for r in righe_allowlist if r.startswith("!")]
    scelti, usati, negativi_usati = [], set(), set()
    for f in tracciati:
        colpiti = [p for p in positivi if vp.combacia(f, p)]
        tolto_da = [n for n in negativi if vp.combacia(f, n)]
        if colpiti and not tolto_da:
            scelti.append(f)
        usati.update(colpiti)
        negativi_usati.update(tolto_da)
    senza_riscontro = [p for p in positivi if p not in usati]
    senza_riscontro += ["!" + n for n in negativi if n not in negativi_usati]
    return scelti, senza_riscontro


# --- T7: sporco, copia, suite, autore, deposito nel clone pubblico ------------------------------

def _via(percorso):
    """Cancella una cartella anche se contiene file di sola lettura: gli oggetti di `.git` su
    Windows lo sono, e senza il chmod resterebbe un `.git` a meta'."""
    for cartella, _, nomi in os.walk(percorso):
        for n in nomi:
            try:
                os.chmod(os.path.join(cartella, n), stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(percorso, ignore_errors=True)


def modificati(repo, files):
    """I file SCELTI che sono modificati, staged o cancellati: l'export deve corrispondere a un
    commit. `git status` gira senza pathspec (303 percorsi sulla riga di comando sfiorerebbero il
    limite di Windows) e l'incrocio si fa qui; i record dei RINOMINATI portano due percorsi
    (nuovo\\0vecchio) e il secondo NON e' un file sporco a se' (review T7)."""
    record = [r for r in _run(["git", "status", "--porcelain", "-z"], repo).stdout.split("\0") if r]
    scelti, sporchi, salta = set(files), set(), False
    for r in record:
        if salta:                      # provenienza di una rinomina: percorso NUDO, senza stato
            salta = False
            if r in scelti:            # uno degli scelti non e' piu' li': la copia fallirebbe
                sporchi.add(r)
            continue
        stato, percorso = r[:2], r[3:]
        if stato[0] in ("R", "C"):
            salta = True
        if percorso in scelti:
            sporchi.add(percorso)
    return sorted(sporchi)


def copia_in_temp(repo, files, temp_dir):
    """Copia i file SCELTI conservando i byte (copy2: i .bat restano CRLF). Torna i byte copiati.
    Un file tracciato ma ASSENTE dal disco (rinomina o cancellazione in volo di un'altra chat, 05/09)
    si DICHIARA prima di copiare qualunque cosa — tutti i mancanti in una riga, niente a meta' —
    invece di cadere con un traceback sul primo (buco D5)."""
    mancanti = [f for f in files if not os.path.isfile(os.path.join(repo, f.replace("/", os.sep)))]
    if mancanti:
        raise RuntimeError("%d file tracciati ma ASSENTI dal disco (rinomina o cancellazione in volo? "
                           "il tree misurabile e' l'indice o HEAD, non questo checkout): %s"
                           % (len(mancanti), ", ".join(mancanti[:20]) + (" ..." if len(mancanti) > 20 else "")))
    byte = 0
    for f in files:
        src = os.path.join(repo, f.replace("/", os.sep))
        dst = os.path.join(temp_dir, f.replace("/", os.sep))
        cartella = os.path.dirname(dst)
        if cartella:
            os.makedirs(cartella, exist_ok=True)
        shutil.copy2(src, dst)
        byte += os.path.getsize(dst)
    return byte


def _nomi_env_privato():
    """I NOMI (mai i valori) delle variabili del `.env` del repo privato. Servono a toglierle
    dall'ambiente della suite qualunque via le abbia portate nel processo (13/09, v. esegui_suite)."""
    path = os.path.join(REPO, ".env")
    if not os.path.isfile(path):
        return set()
    from dotenv import dotenv_values
    return set(dotenv_values(path, encoding="utf-8-sig"))


# --- 13/09 (decisione 3 delegata dal PM): la suite dell'export fa i passi del job `test` della CI ----
# Il 13/09 `vol-atlas.cjs` e' passato dal cancello (che girava solo npm ci + pytest) ed e' caduto in
# CI su `npm run test:release`. Ora la copia verificata esegue gli stessi passi, nello stesso ordine.
# Regola scelta: ogni rosso locale BLOCCA il deposito, senza eccezioni di piattaforma. Cio' che la
# macchina locale NON replica (Linux e macOS della CI, Node e Python diversi) si dichiara nella riga.

PassoSuite = collections.namedtuple("PassoSuite", "nome argv cartella timeout node dipende ci")

PASSI_SUITE = (
    # nome, argv ("python" = l'interprete di chi lancia; il resto si cerca nel PATH della suite),
    # cartella nella copia, timeout in secondi (tempi misurati il 13/09 per 5-10), passo Node,
    # dipende da, gemello in .github/workflows/ci.yml = (comando, working-directory).
    PassoSuite("npm ci", ("npm", "ci", "--no-audit", "--no-fund"), "app", 900, True, (),
               ("npm ci", "app")),
    PassoSuite("pytest", ("python", "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider", "-rfE"),
               "", 3600, False, ("npm ci",), ("pytest tests/ -q", "")),
    PassoSuite("tsc --noEmit", ("npm", "exec", "--no", "--", "tsc", "--noEmit"), "app", 300, True,
               ("npm ci",), ("npx tsc --noEmit", "app")),
    PassoSuite("test:release", ("npm", "run", "test:release"), "app", 900, True, ("npm ci",),
               ("npm run test:release", "app")),
    PassoSuite("build:bundles", ("npm", "run", "build:bundles"), "app", 900, True, ("npm ci",),
               ("npm run build:bundles", "app")),
    PassoSuite("tsc mandato", ("npm", "exec", "--no", "--", "tsc", "-p", "tests/mandato/tsconfig.json"),
               "app", 300, True, ("npm ci",), ("npx tsc -p tests/mandato/tsconfig.json", "app")),
    PassoSuite("contratti mandato", ("node", "tests/mandato/.tmp/tests/mandato/contratti.mjs"), "app",
               120, True, ("npm ci", "tsc mandato"),
               ("node tests/mandato/.tmp/tests/mandato/contratti.mjs", "app")),
)

DIFFERENZE_DALLA_CI = (
    # (comando del job `test` di ci.yml, working-directory, perche' l'export non lo ripete)
    ("python -m pip install --upgrade pip", "",
     "l'export usa l'interprete e i pacchetti di chi lancia: non installa nulla"),
    ("pip install -e . pytest", "",
     "PYTHONPATH punta a src/ della copia: la suite importa il pacchetto verificato, non un'installazione"),
    ("python -m compileall -q src/bellomberg tools/ops tools/migrations bellomberg_api.py "
     "consigliere_multi.py price_updater.py briefing_engine.py regenerate_memo.py", "",
     "non ripetuto: un errore di sintassi in un file che nessun test importa lo vede solo la CI"),
    ("python tools/release/cancello_hook.py --fase ci", "",
     "nel repo pubblico il passo si salta (le liste private non escono); l'export esegue il cancello completo"),
)

RISCRITTURE_DALLA_CI = (
    # (inizio del comando in ci.yml, come lo lancia l'export, perche')
    ("npx", ("npm", "exec", "--no", "--"),
     "npx senza terminale assume --yes e scaricherebbe un pacchetto assente: npm exec --no fallisce"),
    ("pytest", ("python", "-m", "pytest"),
     "l'interprete di chi lancia, con -p no:cacheprovider -rfE: niente cache nella copia, rossi nominati"),
)

NON_REPLICATE = ("macOS e Linux della CI NON replicati localmente (job test su ubuntu-latest con Python "
                 "3.12 e 3.14, job macos-source; nemmeno desktop-windows): un loro rosso resta possibile; "
                 "ogni rosso locale blocca il deposito")


class EsitoSuite(tuple):
    """(exit, riga) come prima, e si spacchetta ancora in due; in piu' `.passi`, i passi della CI
    sulla copia in ordine: {nome, esito OK|KO|NON ESEGUITO, exit, secondi, motivo}."""
    def __new__(cls, codice, riga, passi=None):
        obj = tuple.__new__(cls, (codice, riga))
        obj.passi = passi
        return obj


# --- lettura di ci.yml con la sola stdlib (PyYAML non e' una dipendenza dichiarata dell'export) ---

def _riga_significativa(riga):
    s = riga.strip()
    return bool(s) and not s.startswith("#")


def _scalare_yaml(valore, numero):
    """Uno scalare su una riga: tra virgolette, sequenza piatta `[a, b]` o semplice (col commento
    in coda tolto). Cio' che non e' uno di questi si RIFIUTA con la riga."""
    v = valore.strip()
    if v[:1] == '"':
        m = re.match(r'"([^"\\]*)"\s*(#.*)?$', v)
        if not m:
            raise ValueError("riga %d: stringa tra virgolette doppie con escape o testo dopo: %r" % (numero, v))
        return m.group(1)
    if v[:1] == "'":
        m = re.match(r"'((?:[^']|'')*)'\s*(#.*)?$", v)
        if not m:
            raise ValueError("riga %d: stringa tra virgolette semplici non chiusa: %r" % (numero, v))
        return m.group(1).replace("''", "'")
    if v[:1] == "[":
        m = re.match(r"\[([^\[\]{}]*)\]\s*(#.*)?$", v)
        if not m:
            raise ValueError("riga %d: sequenza tra parentesi annidata o non chiusa: %r" % (numero, v))
        return [_scalare_yaml(x, numero) for x in m.group(1).split(",") if x.strip()]
    if v[:1] in ("{", "&", "*", "!", "%", "@", "`", "|", ">"):
        raise ValueError("riga %d: costrutto YAML che il lettore non regge: %r" % (numero, v))
    i = v.find(" #")
    return v[:i].rstrip() if i >= 0 else v


class _LettoreYaml:
    """Mappe e sequenze a blocchi, scalari su una riga, blocchi letterali `|` e piegati `>` (con
    `-` o `+`). Una riga che non sa leggere e' un ValueError con il numero: un comando della CI
    letto male farebbe passare la parita' senza misurarla."""

    def __init__(self, testo):
        self.righe = testo.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.i = 0

    def _salta(self):
        while self.i < len(self.righe) and not _riga_significativa(self.righe[self.i]):
            self.i += 1
        return self.i < len(self.righe)

    def _rientro(self):
        riga = self.righe[self.i]
        spazi = len(riga) - len(riga.lstrip(" "))
        if riga[spazi:spazi + 1] == "\t":
            raise ValueError("riga %d: tabulazione nel rientro" % (self.i + 1))
        return spazi, riga[spazi:]

    @staticmethod
    def _voce(corpo):
        return corpo == "-" or corpo.startswith("- ")

    def documento(self):
        if not self._salta():
            raise ValueError("workflow vuoto")
        rientro, _ = self._rientro()
        radice = self.mappa(rientro)
        if self._salta():
            raise ValueError("riga %d: rientro inatteso dopo la fine del documento" % (self.i + 1))
        return radice

    def figlio(self, rientro):
        """Il valore di una chiave senza scalare: blocco piu' rientrato, o sequenza allo stesso rientro."""
        if not self._salta():
            return None
        r, corpo = self._rientro()
        if r > rientro:
            return self.sequenza(r) if self._voce(corpo) else self.mappa(r)
        if r == rientro and self._voce(corpo):
            return self.sequenza(r)
        return None

    def mappa(self, rientro):
        voci = {}
        while self._salta():
            r, corpo = self._rientro()
            if r < rientro:
                break
            numero = self.i + 1
            if r > rientro:
                raise ValueError("riga %d: rientro inatteso (uno scalare su piu' righe non e' letto)" % numero)
            if self._voce(corpo):
                break
            m = re.match(r"([A-Za-z0-9_$][\w.\-/]*)\s*:(?:\s+(.*))?$", corpo)
            if not m:
                raise ValueError("riga %d: attesa una chiave, trovato %r" % (numero, corpo))
            chiave, resto = m.group(1), (m.group(2) or "").strip()
            if chiave in voci:
                raise ValueError("riga %d: chiave %r ripetuta" % (numero, chiave))
            self.i += 1
            if not resto or resto.startswith("#"):
                voci[chiave] = self.figlio(rientro)
            elif resto[0] in "|>":
                voci[chiave] = self.blocco(resto, rientro, numero)
            else:
                voci[chiave] = _scalare_yaml(resto, numero)
        return voci

    def sequenza(self, rientro):
        voci = []
        while self._salta():
            r, corpo = self._rientro()
            if r < rientro or (r == rientro and not self._voce(corpo)):
                break
            numero = self.i + 1
            if r > rientro:
                raise ValueError("riga %d: rientro inatteso in una sequenza" % numero)
            resto = corpo[1:].lstrip(" ")
            if not resto or resto.startswith("#"):
                self.i += 1
                voci.append(self.figlio(rientro))
            elif re.match(r"[A-Za-z0-9_$][\w.\-/]*\s*:(\s|$)", resto):
                colonna = r + len(corpo) - len(resto)       # `- chiave: valore` apre una mappa li'
                self.righe[self.i] = " " * colonna + resto
                voci.append(self.mappa(colonna))
            elif resto[0] in "|>":
                self.i += 1
                voci.append(self.blocco(resto, rientro, numero))
            else:
                self.i += 1
                voci.append(_scalare_yaml(resto, numero))
        return voci

    def blocco(self, intestazione, rientro_genitore, numero):
        m = re.match(r"([|>])([-+]?)\s*(#.*)?$", intestazione)
        if not m:
            raise ValueError("riga %d: blocco con indentazione esplicita o intestazione non letta: %r"
                             % (numero, intestazione))
        tipo, taglio = m.group(1), m.group(2)
        contenuto, rientro = [], None
        while self.i < len(self.righe):
            riga = self.righe[self.i]
            if not riga.strip():
                contenuto.append((self.i + 1, ""))
                self.i += 1
                continue
            r = len(riga) - len(riga.lstrip(" "))
            if rientro is None:
                if r <= rientro_genitore:
                    break
                rientro = r
            if r < rientro:
                break
            contenuto.append((self.i + 1, riga[rientro:]))
            self.i += 1
        finali = 0
        while contenuto and contenuto[-1][1] == "":
            contenuto.pop()
            finali += 1
        if tipo == "|":
            testo = "\n".join(t for _, t in contenuto)
        else:
            testo, vuote, primo = "", 0, True
            for n, t in contenuto:
                if t == "":
                    vuote += 1
                    continue
                if t[:1] in (" ", "\t"):
                    raise ValueError("riga %d: blocco piegato '>' con righe piu' rientrate: il lettore "
                                     "non le piega come YAML" % n)
                testo += ("\n" * vuote + t) if (primo or vuote) else (" " + t)
                vuote, primo = 0, False
        if not contenuto:
            return "\n" * finali if taglio == "+" else ""
        if taglio == "-":
            return testo
        return testo + "\n" + ("\n" * finali if taglio == "+" else "")


def leggi_workflow(testo):
    """Un workflow GitHub letto con la sola stdlib, nel sottoinsieme di YAML che usa ci.yml.
    Cio' che non sa leggere lo rifiuta con la riga (ValueError), non lo indovina."""
    wf = _LettoreYaml(testo).documento()
    if not isinstance(wf.get("jobs"), dict):
        raise ValueError("workflow senza una mappa jobs")
    return wf


def _passi_del_job(testo, job):
    specifica = leggi_workflow(testo)["jobs"].get(job)
    if not isinstance(specifica, dict):
        raise ValueError("ci.yml: il job %r non e' una mappa" % job)
    passi = specifica.get("steps")
    if not isinstance(passi, list) or not all(isinstance(p, dict) for p in passi):
        raise ValueError("ci.yml: il job %r non ha una lista di steps" % job)
    return passi


def _comandi_di_run(run):
    """Ogni riga non vuota e non commento e' un comando; una riga che finisce con \\ continua."""
    comandi, sospeso = [], ""
    for riga in run.split("\n"):
        s = (sospeso + " " + riga.strip()).strip() if sospeso else riga.strip()
        sospeso = ""
        if s.endswith("\\"):
            sospeso = s[:-1].rstrip()
            continue
        if s and not s.startswith("#"):
            comandi.append(" ".join(s.split()))
    if sospeso:
        comandi.append(" ".join(sospeso.split()))
    return comandi


def comandi_ci(testo, job="test"):
    """[(comando, working-directory)] dei `run:` del job, in ordine. Una riga di un blocco `|` e'
    un comando; un blocco `>` piegato su piu' righe e' UN comando (la CI lo esegue come una riga)."""
    out = []
    for passo in _passi_del_job(testo, job):
        if "run" not in passo:
            continue
        if not isinstance(passo["run"], str):
            raise ValueError("ci.yml: un run del job %r non e' testo" % job)
        cartella = passo.get("working-directory") or ""
        out.extend((c, cartella) for c in _comandi_di_run(passo["run"]))
    return out


def _argv_fa(argv, comando):
    token = comando.split()
    for inizio, sostituto, _ in RISCRITTURE_DALLA_CI:
        if token[:1] == [inizio]:
            token = list(sostituto) + token[1:]
    return list(argv[:len(token)]) == token


def confronta_con_ci(testo, job="test"):
    """Parita' fra PASSI_SUITE e il job della CI. Liste vuote = parita'. `senza_gemello`: comandi
    della CI che l'export non fa e non dichiara; `gemelli_assenti`: passi dell'export che la CI non
    fa piu'; `differenze_stantie`: differenze dichiarate per comandi che la CI non ha piu';
    `fuori_ordine`: passi in un ordine diverso dalla CI; `argv_diversi`: passi il cui argv non
    comincia col comando gemello (a meno delle RISCRITTURE_DALLA_CI dichiarate)."""
    comandi = comandi_ci(testo, job)
    gemelli = {p.ci: p.nome for p in PASSI_SUITE}
    dichiarati = {(c, d) for c, d, _ in DIFFERENZE_DALLA_CI}
    ordine_ci = [gemelli[c] for c in comandi if c in gemelli]
    ordine_export = [p.nome for p in PASSI_SUITE if p.nome in ordine_ci]
    fuori_ordine = [] if ordine_ci == ordine_export else (
        [a for a, b in zip(ordine_export, ordine_ci) if a != b] or ordine_ci)
    return {
        "senza_gemello": [c for c in comandi if c not in gemelli and c not in dichiarati],
        "gemelli_assenti": [p.nome for p in PASSI_SUITE if p.ci not in comandi],
        "differenze_stantie": [c for c, d, _ in DIFFERENZE_DALLA_CI if (c, d) not in comandi],
        "fuori_ordine": fuori_ordine,
        "argv_diversi": [p.nome for p in PASSI_SUITE if not _argv_fa(p.argv, p.ci[0])],
    }


def _node_della_ci(cartella):
    """La versione di Node che la CI installa, letta dal ci.yml della copia; un buco si dichiara."""
    path = os.path.join(cartella, ".github", "workflows", "ci.yml")
    if not os.path.isfile(path):
        return "n.d. (ci.yml assente nella copia)"
    try:
        with open(path, encoding="utf-8") as fh:
            passi = _passi_del_job(fh.read(), "test")
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return "n.d. (ci.yml illeggibile: %s)" % e
    for passo in passi:
        if str(passo.get("uses", "")).startswith("actions/setup-node"):
            configurazione = passo.get("with", {})
            if not isinstance(configurazione, dict):
                return "n.d. (ci.yml illeggibile: with di actions/setup-node non e' una mappa)"
            versione = configurazione.get("node-version")
            if versione:
                return str(versione)
    return "n.d. (nessun actions/setup-node con node-version nel job test)"


# --- esecuzione dei passi ---

def _uccidi_albero(proc):
    """Il processo e i suoi figli: `npm run` e' cmd/sh -> node -> vite/tsc."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
        proc.wait(timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass


def _lancia(argv, cwd, env, stdout, stderr, timeout):
    """Un processo con timeout VERO. L'output va su file, non su pipe: un nipote rimasto vivo terrebbe
    aperta la pipe e la lettura non tornerebbe mai. Allo scadere (o a un Ctrl+C) si uccide l'albero
    intero, perche' i figli di `npm run` continuerebbero a scrivere nella copia. Torna l'exit;
    TimeoutExpired e OSError salgono a chi chiama."""
    extra = {} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=stdout, stderr=stderr, **extra)
    try:
        return proc.wait(timeout=timeout)
    except BaseException:
        _uccidi_albero(proc)
        raise


def _esito_passo(nome, esito, codice=None, secondi=0.0, motivo=""):
    return {"nome": nome, "esito": esito, "exit": codice, "secondi": round(secondi, 3), "motivo": motivo}


def _esegui_passo(passo, lavoro, env, lancia, orologio):
    """(esito, stdout, stderr, argv, cartella) di un passo lanciato nella copia."""
    argv = list(passo.argv)
    cwd = os.path.join(lavoro, passo.cartella) if passo.cartella else lavoro
    if argv[0] == "python":
        argv[0] = sys.executable
    else:
        trovato = shutil.which(argv[0], path=env.get("PATH"))
        if not trovato:
            return (_esito_passo(passo.nome, "KO", motivo="%s non trovato nel PATH" % argv[0]),
                    "", "", argv, cwd)
        argv[0] = trovato
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        inizio = orologio()
        try:
            codice = lancia(argv, cwd, env, out, err, passo.timeout)
            esito = _esito_passo(passo.nome, "OK" if codice == 0 else "KO", codice,
                                 motivo="" if codice == 0 else "exit %s" % codice)
        except subprocess.TimeoutExpired:
            esito = _esito_passo(passo.nome, "KO", motivo="timeout dopo %d s" % passo.timeout)
        except OSError as e:
            esito = _esito_passo(passo.nome, "KO", motivo="OSError (%s): %s"
                                 % (type(e).__name__, e.strerror or e))
        esito["secondi"] = round(orologio() - inizio, 3)
        out.seek(0)
        err.seek(0)
        return (esito, out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace"),
                argv, cwd)


def _versione_node(env, cwd, lancia):
    """La versione di Node di chi lancia, misurata; un buco si dichiara."""
    node = shutil.which("node", path=env.get("PATH"))
    if not node:
        return "n.d. (node non trovato nel PATH)"
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            codice = lancia([node, "--version"], cwd, env, out, err, 60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return "n.d. (%s)" % type(e).__name__
        out.seek(0)
        testo = out.read().decode("utf-8", "replace").strip()
    return testo if codice == 0 and testo else "n.d. (node --version: exit %s)" % codice


def _secondi(valore):
    return ("%.1f" % valore).replace(".", ",")


def _sezione(log, esito, argv=None, cwd=None, out="", err=""):
    testa = "=== passo %s: %s" % (esito["nome"], esito["esito"])
    if esito["esito"] != "NON ESEGUITO":
        testa += ", exit %s, %s s" % (esito["exit"], _secondi(esito["secondi"]))
    if esito["motivo"]:
        testa += " (%s)" % esito["motivo"]
    log.write(testa + " ===\n")
    if argv:
        log.write("$ %s   [cartella %s]\n" % (" ".join(argv), cwd))
    if out:
        log.write(out.rstrip("\n") + "\n")
    if err:
        log.write("--- STDERR ---\n" + err.rstrip("\n") + "\n")
    log.flush()


def _riga_passo(esito):
    if esito["esito"] == "OK":
        return "%s OK %s s" % (esito["nome"], _secondi(esito["secondi"]))
    if esito["esito"] == "KO":
        return "%s KO (%s) %s s" % (esito["nome"], esito["motivo"], _secondi(esito["secondi"]))
    return "%s NON ESEGUITO: %s" % (esito["nome"], esito["motivo"])


def esegui_suite(temp_dir, attesi=None, lancia=None, orologio=None):
    """I passi della CI su una COPIA del tree esportato: misura di coerenza, non di leak. Cure della review T7:
      · l'ambiente non porta ne' il DB ne' le chiavi del PM: via `BELLOMBERG_*` e, dal 13/09, via
        ogni variabile che porta il NOME di una riga del `.env` privato (nel dry-run il payload
        legacy importava i moduli e `config.py` caricava il `.env`: la suite ne ereditava 44,
        chiavi comprese, e dava due rossi in piu'). I nomi tolti si dichiarano nella riga;
      · `PYTHONDONTWRITEBYTECODE`: niente .pyc (168 file, 6 MB, che nessun controllo aveva visto);
      · la copia diventa un repo git come lo sara' il pubblico: tre test derivano i loro input da
        `git ls-files`/`git check-ignore` e in una cartella nuda git esce 128 con stdout vuoto
        (misurato: 17 cadute senza git, 14 con). `attesi` = quanti file l'indice deve contenere:
        se sono meno, quelle guardie misurerebbero meno del perimetro e si dichiara;
      · 13/09: con `app/package-lock.json` si installano le dipendenze Node DOPO l'indice, come la
        CI pubblica (misurato: 23 rossi senza, 5 con; i 18 erano moduli Node assenti). Per questo
        la suite gira su una copia verificata per hash: l'artefatto certificato non riceve ne'
        `node_modules` ne' i file che un test scrive. Un `npm ci` fallito e' un KO dichiarato.
      · 13/09 (decisione 3): dopo pytest i passi Node del job `test` della CI (PASSI_SUITE), TUTTI
        anche dopo un rosso, cosi' una corsa sola mostra ogni rosso; un passo parte solo se quelli
        da cui dipende sono OK (tutti da `npm ci`, i contratti del mandato dal loro tsc), ognuno ha
        un timeout che uccide l'albero dei processi, e ognuno finisce OK, KO o NON ESEGUITO col
        motivo. Senza `app/package-lock.json` i passi Node sono NON ESEGUITI (dichiarati) e pytest
        gira come prima. `lancia` e `orologio` si iniettano nelle prove.
    L'output completo resta in `<temp>.suite.log`, una sezione per passo; la riga nomina i test
    rossi, i passi e cio' che la macchina locale NON replica della CI."""
    lancia = lancia or _lancia
    orologio = orologio or time.monotonic
    lavoro = temp_dir.rstrip("\\/") + ".suite"
    log_path = temp_dir.rstrip("\\/") + ".suite.log"
    privati = _nomi_env_privato()
    env = {k: v for k, v in os.environ.items() if not k.startswith("BELLOMBERG_") and k not in privati}
    tolte = sorted(k for k in os.environ if k in privati and not k.startswith("BELLOMBERG_"))
    # Resolve the package under test from the verified copy, never the
    # maintainer's editable installation. Installation is checked separately.
    env["PYTHONPATH"] = os.pathsep.join((os.path.join(lavoro, "src"), lavoro))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    note = []
    if tolte:
        note.append("ambiente: %d variabili del .env privato tolte (%s)" % (len(tolte), ", ".join(tolte)))
    fatti, uscite, node_locale, node_ci = {}, {}, "n.d. (nessun app/package-lock.json)", None
    with open(log_path, "w", encoding="utf-8", newline="\n") as log:
        def stop(codice, riga):
            passi = [_esito_passo(p.nome, "NON ESEGUITO", motivo="STOP prima dei passi: " + riga[:200])
                     for p in PASSI_SUITE]
            for esito in passi:
                _sezione(log, esito)
            return EsitoSuite(codice, riga, passi)

        try:
            _via(lavoro)
            shutil.copytree(temp_dir, lavoro)
            if hash_artefatto(lavoro) != hash_artefatto(temp_dir):
                return stop(1, "STOP: la copia per la suite non e' identica all'artefatto")
            for args in (["git", "init", "-q"], ["git", "add", "-A"]):
                r = _run(args, lavoro, check=False)
                if r.returncode != 0:
                    return stop(r.returncode, "git %s nel tree: %s" % (args[1], (r.stderr or "").strip()[-200:]))
            if attesi is not None:
                n = len(file_tracciati(lavoro))
                if n != attesi:
                    return stop(1, ("STOP: l'indice del tree ha %d file sui %d scelti (il .gitignore pubblicato"
                                    " o un core.excludesFile ne toglie): le guardie che usano git"
                                    " misurerebbero meno del perimetro" % (n, attesi)))
            lockfile = os.path.isfile(os.path.join(lavoro, "app", "package-lock.json"))
            if not lockfile:
                note.append("nessun app/package-lock.json: dipendenze Node non installate, passi Node NON ESEGUITI")
            per_nome = {p.nome: p for p in PASSI_SUITE}
            for passo in PASSI_SUITE:
                argv = cwd = None
                out = err = ""
                # Un passo Node saltato per mancanza del lockfile non ferma chi non e' Node: pytest
                # gira come prima di questa cura (e il manifest resta INCOMPLETO).
                bloccanti = [d for d in passo.dipende
                             if fatti[d]["esito"] != "OK" and (lockfile or not per_nome[d].node)]
                if passo.node and not lockfile:
                    esito = _esito_passo(passo.nome, "NON ESEGUITO",
                                         motivo="nessun app/package-lock.json nella copia")
                elif bloccanti:
                    esito = _esito_passo(passo.nome, "NON ESEGUITO", motivo="dipende da " + ", ".join(
                        "%s (%s)" % (d, fatti[d]["esito"]) for d in bloccanti))
                else:
                    esito, out, err, argv, cwd = _esegui_passo(passo, lavoro, env, lancia, orologio)
                fatti[passo.nome], uscite[passo.nome] = esito, (out, err)
                _sezione(log, esito, argv, cwd, out, err)
            if lockfile:
                node_locale = _versione_node(env, lavoro, lancia)
            node_ci = _node_della_ci(lavoro)
        finally:
            _via(lavoro)
    passi = [fatti[p.nome] for p in PASSI_SUITE]
    out_pytest, err_pytest = uscite["pytest"]
    righe = [x for x in out_pytest.splitlines() if x.strip()]
    if fatti["pytest"]["esito"] == "NON ESEGUITO":
        riga = "pytest NON ESEGUITO: %s" % fatti["pytest"]["motivo"]
    else:
        riga = righe[-1] if righe else err_pytest[-200:]
        if fatti["pytest"]["esito"] == "KO" and not fatti["pytest"]["motivo"].startswith("exit"):
            riga = "pytest KO (%s): %s" % (fatti["pytest"]["motivo"], riga)
    rossi = [x.split(" ", 1)[1].split(" - ", 1)[0] for x in righe if x.startswith(("FAILED ", "ERROR "))]
    if rossi:
        riga += " — rossi: " + ", ".join(rossi[:40]) + (" (+%d)" % (len(rossi) - 40) if len(rossi) > 40 else "")
    codice = 0
    for esito in passi:
        if esito["esito"] == "KO":
            codice = esito["exit"] if isinstance(esito["exit"], int) and esito["exit"] else 1
            break
    if node_ci and node_ci.startswith("n.d. (ci.yml illeggibile"):
        codice = codice or 1
    parti = [riga] + note + ["passi: " + " · ".join(_riga_passo(e) for e in passi)]
    for esito in passi:
        if esito["esito"] == "KO" and esito["nome"] != "pytest":
            coda = " ".join((uscite[esito["nome"]][1] + " " + uscite[esito["nome"]][0]).split())[-300:]
            if coda:
                parti.append("%s: %s" % (esito["nome"], coda))
    parti.append("Node: locale %s, CI %s" % (node_locale, node_ci))
    parti.append("locale %s %s con Python %s; %s" % (platform.system(), platform.release(),
                                                    platform.python_version(), NON_REPLICATE))
    return EsitoSuite(codice, "; ".join(parti) + " — log completo: " + log_path, passi)


def _cancello(tree_dir, solo=None, blocca_osservazione=False, corpus_root=None):
    registro, registro_esiti = {}, {}
    rc = vp.esegui_tutti(
        tree_dir, solo=solo, blocca_osservazione=blocca_osservazione,
        corpus_root=corpus_root, registro_input=registro,
        registro_esiti=registro_esiti)
    return RisultatoCancello(rc, registro, registro_esiti)


class RisultatoCancello(int):
    """Compatibile con l'exit int storico, con in piu' l'impronta degli input usati."""
    def __new__(cls, valore, input_manifest=None, esiti_manifest=None):
        obj = int.__new__(cls, valore)
        obj.input_manifest = input_manifest or {}
        obj.esiti_manifest = esiti_manifest or {}
        return obj


def hash_artefatto(tree_dir):
    """SHA-256 deterministico di percorsi+byte del tree, esclusa la sola .git."""
    h = hashlib.sha256()
    n = 0
    for cartella, sub, nomi in os.walk(tree_dir):
        sub[:] = sorted(d for d in sub if d != ".git")
        for nome in sorted(n for n in nomi if n != ".git"):
            path = os.path.join(cartella, nome)
            rel = os.path.relpath(path, tree_dir).replace(os.sep, "/")
            with open(path, "rb") as fh:
                dati = fh.read()
            h.update(len(rel.encode("utf-8")).to_bytes(8, "big"))
            h.update(rel.encode("utf-8"))
            h.update(len(dati).to_bytes(8, "big"))
            h.update(dati)
            n += 1
    if n == 0:
        raise ValueError("artefatto vuoto: hash rifiutato")
    return h.hexdigest(), n


def scrivi_manifest(path, commit_sorgente, sha_artefatto, n_file, input_manifest,
                    rc_cancello, rc_suite, solo=None, senza_suite=False, sporchi=0,
                    esiti_manifest=None, sha_artefatto_post=None, n_file_post=None, passi=None):
    """Sidecar senza valori privati: solo hash, conteggi, KO/assenze e stato delle prove.
    Versione 2 (13/09): `verifica.suite_passi` porta i passi della CI sulla copia (nome, esito,
    exit, secondi, motivo; mai l'output). Passi assenti o NON ESEGUITI = INCOMPLETO, un KO = KO."""
    esiti_manifest = esiti_manifest or {}
    rigoroso = bool(esiti_manifest.get("rigoroso"))
    suite_passi = None if passi is None else [
        {k: p.get(k) for k in ("nome", "esito", "exit", "secondi", "motivo")} for p in passi]
    esiti_passi = [p["esito"] for p in suite_passi or ()]
    incompleto = bool(not rigoroso or solo or senza_suite or not input_manifest
                      or input_manifest.get("fonti_ko")
                      or input_manifest.get("conteggi", {}).get(
                          "payload_canali_guasti", 0)
                      or suite_passi is None or "NON ESEGUITO" in esiti_passi)
    stato = ("KO" if ("KO" in esiti_passi or ((int(rc_cancello) or int(rc_suite)) and not incompleto))
             else "INCOMPLETO" if incompleto else "OK")
    doc = {
        "versione": 2,
        "sorgente": {"commit": commit_sorgente or None},
        "artefatto": {
            "sha256": sha_artefatto_post or sha_artefatto,
            "file": n_file_post if n_file_post is not None else n_file,
            "sha256_prima_suite": sha_artefatto,
            "file_prima_suite": n_file,
            "immutato_dalla_suite": (
                (sha_artefatto_post or sha_artefatto) == sha_artefatto
                and (n_file_post if n_file_post is not None else n_file) == n_file),
        },
        "input": input_manifest or {
            "sha256_corpus_privato": None, "sha256_payload": None,
            "hash_liste": {}, "conteggi": {},
            "fonti_assenti": ["registro input non disponibile"],
            "fonti_ko": {},
        },
        "verifica": {
            "cancello_exit": int(rc_cancello),
            "suite_exit": int(rc_suite),
            "controlli_solo": list(solo or ()),
            "suite_non_eseguita": bool(senza_suite),
            "suite_passi": suite_passi,
            "sorgente_sporca_file": int(sporchi),
            "stato": stato,
            "rigoroso": rigoroso,
            "controlli": list(esiti_manifest.get("controlli", [])),
            "non_eseguiti": list(esiti_manifest.get("non_eseguiti", [])),
        },
    }
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, sort_keys=True, indent=2)
        fh.write("\n")
    return doc


def autore_pubblico(dest, vietate=None):
    """(nome, email) con cui firmare i commit pubblici, letti dal git config del CLONE. L'email
    dev'essere la noreply di GitHub; il NOME e' l'altra meta' dell'identita' e finisce verbatim
    nell'header di ogni commit: se contiene una stringa vietata si ferma, col token MASCHERATO."""
    nome = _run(["git", "config", "user.name"], dest, check=False).stdout.strip()
    email = _run(["git", "config", "user.email"], dest, check=False).stdout.strip()
    if not nome or not email:
        raise RuntimeError("git config user.name/user.email mancanti nel clone pubblico %s" % dest)
    if not email.endswith("@users.noreply.github.com"):
        raise RuntimeError("l'email dei commit pubblici deve essere quella noreply di GitHub, non %s" % email)
    if vietate is None:
        vietate = vp.leggi_lista(os.path.join(vp.PUBBLICO, "VIETATE.txt"))
    for v in vietate:
        if v.lower() in nome.lower():
            raise RuntimeError("il nome dei commit pubblici contiene una stringa vietata: %s (D6)"
                               % vp.maschera(v))
    return nome, email


# --- D6 (voce 6 dell'ordine del 04/09): la STORIA del clone, misurata PRIMA di svuotarlo ------------

def leggi_registro(registro):
    """Gli hash dei commit che l'export ha DEPOSITATO nel clone pubblico: uno per riga, dopo '#'
    e' commento. Un registro assente non vale «storia vuota» (regola 14/07): si ferma e lo dice —
    la prima pubblicazione sul repo NUOVO lo crea con la sola intestazione, e quell'atto e' voluto."""
    if not os.path.isfile(registro):
        raise RuntimeError("registro della storia pubblica ASSENTE: %s — senza, nessun commit del "
                           "clone e' verificabile; se e' la prima pubblicazione sul repo NUOVO, "
                           "crealo con la sola intestazione (D6)" % registro)
    noti = []
    with open(registro, encoding="utf-8-sig") as fh:      # utf-8-sig: un BOM non diventa un token
        for n, riga in enumerate(fh, 1):
            tok = riga.split("#", 1)[0].strip()
            if not tok:
                continue
            tok = tok.split()[0]
            if not re.fullmatch(r"[0-9a-f]{40}", tok):   # abbreviato/maiuscolo non combacerebbe MAI
                raise RuntimeError("registro %s, riga %d: «%s» non e' un hash INTERO (40 esadecimali "
                                   "minuscoli): una riga che non puo' combaciare con nessun commit e' "
                                   "un ripiego muto — si registra l'hash intero, mai abbreviato (D6)"
                                   % (registro, n, tok))
            noti.append(tok)
    return noti


def _git_nel_clone(dest, *args):
    """git nel clone, con la SUA riga di errore: un exit != 0 diventa RuntimeError (che `main`
    stampa come STOP), mai un CalledProcessError nudo (traceback, exit 1 = «hit trovati»)."""
    r = _run(["git", *args], dest, check=False)
    if r.returncode != 0:                            # exit != 0 = STOP dichiarato, con la riga di git
        raise RuntimeError("git %s nel clone %s: exit %d — %s"
                           % (args[0], dest, r.returncode,
                              ((r.stderr or "") + (r.stdout or "")).strip()[-200:]))
    return r.stdout or ""


def storia_del_clone(dest, registro=None):
    """Torna (commit raggiungibili da HEAD, ramo corrente) del clone, o ferma. Ferma se un commit
    NON sta nel registro — l'export non l'ha prodotto, quindi il cancello non l'ha misurato, e un
    push lo pubblicherebbe — e se il clone porta ref oltre al ramo corrente (rami locali, refs/pull,
    tag: `push --all`/`--mirror` li pubblicherebbe). I `refs/remotes/*` sono lo stato del SERVER
    visto dal clone (dopo un fetch ci finiscono i rami di Dependabot) e non escono con un push
    normale: si ignorano apposta — TRANNE `origin/<ramo corrente>`, che con `--mirror` uscirebbe e
    deve percio' puntare a un commit registrato. HEAD staccato = STOP. Un clone senza HEAD e'
    vergine: e' il primo sync, e passa."""
    registro = registro or REGISTRO
    noti = set(leggi_registro(registro))
    r = _run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], dest, check=False)
    if r.returncode == 0:
        commit = _git_nel_clone(dest, "rev-list", "HEAD").split()
    elif r.returncode == 1 and not (r.stdout or "").strip():
        commit = []                                     # nessun commit: clone vergine
    else:
        raise RuntimeError("git rev-parse HEAD nel clone %s: exit %d — %s"
                           % (dest, r.returncode, (r.stderr or "").strip()[-200:]))
    estranei = [c for c in commit if c not in noti]
    if estranei:
        raise RuntimeError("la STORIA del clone %s ha %d commit su %d che l'export non ha prodotto "
                           "(non stanno in %s): %s — un push da qui pubblicherebbe storia che il "
                           "cancello non ha misurato; il repo pubblico va creato VUOTO (senza README "
                           "o LICENSE) e clonato da li', oppure un hash ISPEZIONATO si registra a "
                           "mano, INTERO (D6)"
                           % (dest, len(estranei), len(commit), registro, ", ".join(estranei[:5])))
    r_ramo = _run(["git", "symbolic-ref", "--short", "HEAD"], dest, check=False)
    if r_ramo.returncode != 0:                       # HEAD staccato
        raise RuntimeError("il clone %s ha HEAD STACCATO (nessun ramo corrente): il deposito vuole un "
                           "ramo — `git -C %s switch <ramo>` (D6)" % (dest, dest))
    ramo = r_ramo.stdout.strip()
    ref = {}
    for riga in _git_nel_clone(dest, "for-each-ref", "--format=%(refname) %(objectname)").splitlines():
        if riga.strip():
            nome, oggetto = riga.split()[:2]
            ref[nome] = oggetto
    estranei_ref = [x for x in ref if x != "refs/heads/" + ramo and not x.startswith("refs/remotes/")]
    if estranei_ref:
        raise RuntimeError("il clone %s porta %d ref oltre al ramo corrente %s: %s — rami locali, "
                           "refs/pull e tag uscirebbero con un push --all/--mirror; il deposito vuole "
                           "un clone nudo (D6)"
                           % (dest, len(estranei_ref), ramo, ", ".join(estranei_ref[:10])))
    remoto = ref.get("refs/remotes/origin/" + ramo)
    if remoto is not None and remoto not in noti:
        raise RuntimeError("refs/remotes/origin/%s nel clone %s punta a %s, un commit che l'export non "
                           "ha prodotto: un push --mirror lo porterebbe fuori; si riparte da un clone "
                           "allineato al registro (D6)" % (ramo, dest, remoto))
    return commit, ramo


def registra_commit(registro, h, messaggio):
    """Appende l'hash depositato: e' cio' che rende misurabile il sync successivo."""
    with open(registro, "a", encoding="utf-8") as fh:
        fh.write("%s  # %s\n" % (h, messaggio))


def _msg_non_registrato(registro, h, messaggio, errore):
    return ("commit %s FATTO nel clone ma NON registrato in %s (%s): aggiungi a mano la riga "
            "«%s  # %s» prima del prossimo sync, o quel sync si fermera' sul commit dell'export "
            "stesso (D6)" % (h, registro, errore, h, messaggio))


def pubblica_in(dest, temp_dir, messaggio, scelti, esegui=None, vietate=None, registro=None):
    """Svuota il working tree del clone (mai `.git`), copia SOLO i file scelti — quelli che il
    cancello ha misurato, non la cartella intera — e fa UN commit. Torna l'hash, o None se non
    c'era niente da pubblicare (tree identico a HEAD: non e' un guasto, si dichiara). MAI push.
    Prima di svuotare misura la STORIA del clone (D6) e dopo il commit ne registra l'hash."""
    esegui = esegui or subprocess.run
    if not os.path.isdir(os.path.join(dest, ".git")):
        raise RuntimeError("%s non e' un clone git" % dest)
    vero_dest, vero_repo = os.path.realpath(dest), os.path.realpath(REPO)
    if (vero_dest == vero_repo or vero_repo.startswith(vero_dest + os.sep)
            or vero_dest.startswith(vero_repo + os.sep)):
        raise RuntimeError("--dest e' il repo privato (o lo contiene, o ci sta dentro): %s" % vero_dest)
    registro = registro or REGISTRO
    storia_del_clone(dest, registro)                # D6: ferma PRIMA che il working tree sia toccato
    nome, email = autore_pubblico(dest, vietate)
    for voce in os.listdir(dest):
        if voce == ".git":
            continue
        p = os.path.join(dest, voce)
        if os.path.islink(p):                      # si stacca il collegamento, non il bersaglio
            os.rmdir(p) if os.path.isdir(p) else os.remove(p)
        elif os.path.isdir(p):
            _via(p)
        else:
            os.remove(p)
    copia_in_temp(temp_dir, scelti, dest)
    amb = dict(os.environ)
    amb.update(GIT_AUTHOR_NAME=nome, GIT_AUTHOR_EMAIL=email,     # `--author` firma solo l'autore:
               GIT_COMMITTER_NAME=nome, GIT_COMMITTER_EMAIL=email)   # il committer viene da qui
    kw = dict(cwd=dest, capture_output=True, encoding="utf-8", errors="replace", env=amb)

    def git(*args):
        r = esegui(["git", *args], **kw)
        if r.returncode != 0:        # la riga di git arriva al PM, non un traceback
            raise RuntimeError("git %s: exit %d — %s"
                               % (args[0], r.returncode,
                                  ((r.stderr or "") + (r.stdout or "")).strip()[-300:]))
        return r.stdout or ""

    git("add", "-A")
    indice = {f for f in git("ls-files", "-z").split("\0") if f}
    attesi = {f.replace(os.sep, "/") for f in scelti}
    if indice != attesi:      # review T7: il .gitignore pubblicato, il .git/info/exclude del clone
        mancanti = sorted(attesi - indice)   # o un core.excludesFile possono togliere file DOPO la copia
        raise RuntimeError("l'indice del clone ha %d file sui %d copiati: qualcosa li toglie "
                           "(.gitignore pubblicato, .git/info/exclude, core.excludesFile) — %s"
                           % (len(indice), len(attesi), ", ".join(mancanti[:10])))
    if not git("status", "--porcelain").strip():
        return None
    git("commit", "-q", "-m", messaggio)
    h = git("rev-parse", "HEAD").strip()
    try:
        registra_commit(registro, h, messaggio)          # D6: il sync dopo lo riconoscera' da qui
    except OSError as e:                                 # il commit C'E': lo si dice, con la riga da scrivere
        raise RuntimeError(_msg_non_registrato(registro, h, messaggio, e))
    return h


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # review T2 F8: stdout redirezionato
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=None, help="clone del repo pubblico (senza: solo dry-run in temp)")
    ap.add_argument("--commit", action="store_true", help="con --dest: svuota, copia, UN commit (mai push)")
    ap.add_argument("--senza-suite", action="store_true", help="salta pytest nel tree (il riepilogo lo dice)")
    ap.add_argument("--solo", default=None, help="controlli del cancello (il verdetto resta INCOMPLETO)")
    ap.add_argument("--tieni", action="store_true", help="non cancella la cartella temporanea")
    ap.add_argument("--anche-sporco", action="store_true", help="esporta anche file modificati (dichiarato)")
    ap.add_argument("--rigoroso", action="store_true",
                    help="rende bloccanti i controlli in osservazione anche nel dry-run")
    ap.add_argument("--corpus-root", default=None,
                    help="snapshot privato esplicito per DB/.env/tuple/lista; obbligatorio con --rigoroso o --commit")
    a = ap.parse_args(argv)

    if (a.rigoroso or a.commit) and not a.corpus_root:
        print("STOP: --corpus-root e' obbligatorio con --rigoroso o --commit: "
              "la certificazione deve identificare lo snapshot privato confrontato")
        return 2

    if a.commit and (not a.dest or a.solo):
        print("STOP: --commit richiede --dest e tutti i controlli (senza --solo)")
        return 2

    tracciati = file_tracciati(REPO)
    with open(ALLOWLIST, "rb") as fh:
        allowlist_bytes = fh.read()
    scelti, senza = seleziona(tracciati, vp._lista_da_testo(allowlist_bytes.decode("utf-8-sig")))
    if senza:
        print("STOP: pattern dell'allowlist senza riscontro (lista stantia): %s" % ", ".join(senza))
        return 2
    if a.senza_suite and a.dest and a.commit:
        # la suite nel tree e' l'unica misura di coerenza del perimetro: saltarla E depositare
        # vorrebbe dire uscire 0 dichiarando una verifica che non c'e' stata (review T7)
        print("STOP: --senza-suite non si combina con --commit: il deposito uscirebbe 0 senza"
              " l'unica misura che il tree esportato regge da solo")
        return 2
    sporchi = modificati(REPO, scelti)
    if sporchi and a.anche_sporco and a.dest and a.commit:
        print("STOP: --anche-sporco non si combina con --commit: il messaggio del commit pubblico"
              " dichiara un commit privato che NON contiene questo codice (%d file: %s)"
              % (len(sporchi), ", ".join(sporchi[:10])))
        return 2
    if sporchi and not a.anche_sporco:
        print("STOP: %d file scelti sono modificati/staged (l'export corrisponde a un commit): %s"
              % (len(sporchi), ", ".join(sporchi[:10])))
        return 2
    autore = None
    if a.dest:
        try:
            autore = "%s <%s>" % autore_pubblico(a.dest)
            commit_clone, ramo = storia_del_clone(a.dest)     # D6: anche in dry-run, PRIMA di copiare
        except (RuntimeError, OSError) as e:      # OSError: un --dest inesistente su Windows e' NotADirectoryError
            print("STOP: %s" % e)
            return 2
        print("autore dei commit pubblici (D6): %s" % autore)
        print("storia del clone: %d commit, tutti registrati in %s (ramo %s)"
              % (len(commit_clone), REGISTRO, ramo))
    hash_privato_intero = _run(["git", "rev-parse", "HEAD"], REPO).stdout.strip()
    hash_privato = hash_privato_intero[:10]
    temp = tempfile.mkdtemp(prefix="bellomberg_export_")
    manifest_path = temp.rstrip("\\/") + ".manifest.json"
    try:
        byte = copia_in_temp(REPO, scelti, temp)
        sha_artefatto, file_hash = hash_artefatto(temp)
        print("export: file: %d, byte: %d, privato: %s%s, temp: %s"
              % (len(scelti), byte, hash_privato,
                 " (SPORCO: %d)" % len(sporchi) if sporchi else "", temp))
        # Sul DEPOSITO i controlli in OSSERVAZIONE tornano a bloccare: in dry-run l'exit 0
        # serve a vedere il conto scendere, ma qui sotto si pubblica proprio su `rc == 0`.
        parametri_cancello = {
            "solo": a.solo.split(",") if a.solo else None,
            "blocca_osservazione": bool(a.rigoroso or (a.dest and a.commit)),
        }
        # Non rompe gli stub/programmi storici: il nuovo argomento entra solo quando
        # il chiamante chiede esplicitamente uno snapshot diverso dal repo sorgente.
        if a.corpus_root is not None:
            parametri_cancello["corpus_root"] = a.corpus_root
        rc_cancello = _cancello(temp, **parametri_cancello)
        if a.senza_suite:
            rc_suite, riga, passi = 0, "NON ESEGUITA (--senza-suite): il verdetto non la conta", None
        else:
            esito_suite = esegui_suite(temp, attesi=len(scelti))
            rc_suite, riga = esito_suite
            passi = getattr(esito_suite, "passi", None)     # una suite senza passi = manifest INCOMPLETO
        sha_dopo_suite, file_dopo_suite = hash_artefatto(temp)
        if (sha_dopo_suite, file_dopo_suite) != (sha_artefatto, file_hash):
            rc_suite = max(2, rc_suite)
            riga = ("STOP: la suite ha modificato l'artefatto "
                    "(prima %s/%d file, dopo %s/%d file)"
                    % (sha_artefatto[:12], file_hash,
                       sha_dopo_suite[:12], file_dopo_suite))
        print("suite: %s" % riga)
        solo = a.solo.split(",") if a.solo else None
        input_manifest = dict(getattr(rc_cancello, "input_manifest", None) or {})
        # Impronta degli stessi byte usati per scegliere il perimetro, non di una
        # seconda lettura che potrebbe essere cambiata durante cancello e suite.
        input_manifest["hash_liste"] = dict(input_manifest.get("hash_liste", {}))
        input_manifest["hash_liste"]["ALLOWLIST.txt"] = hashlib.sha256(allowlist_bytes).hexdigest()
        doc_manifest = scrivi_manifest(
            manifest_path, hash_privato_intero, sha_artefatto, file_hash,
            input_manifest,
            rc_cancello, rc_suite, solo=solo, senza_suite=a.senza_suite,
            sporchi=len(sporchi),
            esiti_manifest=getattr(rc_cancello, "esiti_manifest", None),
            sha_artefatto_post=sha_dopo_suite, n_file_post=file_dopo_suite, passi=passi)
        print("manifest: %s (artefatto sha256 %s; stato %s)"
              % (manifest_path, sha_artefatto[:12], doc_manifest["verifica"]["stato"]))
        rc = max(rc_cancello, 1 if rc_suite else 0)
        if rc != 0:
            print("commit: NO (cancello %d, suite %d)%s"
                  % (rc_cancello, rc_suite, "" if a.tieni else " — con --tieni la temp resta da guardare"))
            return rc
        if a.dest and a.commit:
            try:
                valida_manifest(doc_manifest)
                prepara_guardia(a.dest)
                h = pubblica_in(a.dest, temp, "sync %s (privato %s)"
                                % (_dt.date.today().isoformat(), hash_privato), scelti)
                certificato = certifica_deposito(a.dest, doc_manifest, temp)
                print("certificato persistente e guardia pre-push: %s" % certificato)
            except (RuntimeError, OSError, ValueError) as e:
                print("STOP: %s" % e)
                if "NON registrato" in str(e):     # D6: il commit c'e', manca solo la riga nel registro
                    print("il deposito e' FATTO: aggiungi a mano la riga indicata al registro, poi il"
                          " push resta al PM")
                    return 2
                print("il clone %s puo' essere a meta' sync (svuotato e ricopiato, nessun commit):"
                      " `git -C %s status`, e per tornare a HEAD"
                      " `git -C %s restore --staged --worktree .`" % (a.dest, a.dest, a.dest))
                a.tieni = True     # la temp gia' certificata resta: si rideposita senza rifare tutto
                return 2
            print("commit: %s in %s — NESSUN PUSH: lo ordina il PM"
                  % (h[:7] if h else "niente da pubblicare (tree identico a HEAD)", a.dest))
        else:
            print("commit: NO (dry-run)")
        return 0
    finally:
        log_suite = temp.rstrip("\\/") + ".suite.log"
        if a.tieni:
            print("temp conservata: %s" % temp)
            if os.path.isfile(manifest_path):
                print("manifest conservato: %s" % manifest_path)
            if os.path.isfile(log_suite):
                print("output completo della suite conservato: %s" % log_suite)
        else:
            _via(temp)
            for sidecar in (manifest_path, log_suite):
                if os.path.isfile(sidecar):
                    os.remove(sidecar)


if __name__ == "__main__":
    sys.exit(main())
