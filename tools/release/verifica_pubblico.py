# -*- coding: utf-8 -*-
"""verifica_pubblico.py — il CANCELLO anti-leak del repo pubblico (pubblicazione P2, 02/09).

Gira sul tree ESPORTATO (tools/release/export_pubblico.py) prima di ogni commit sul pubblico.
Ogni controllo e' una misura che conta e ferma: stampa file:riga e il token MASCHERATO,
mai il valore. Le liste stanno in tools/release/policy/ (nel privato, mai esportate):
  ALLOWLIST.txt  cosa esce (la legge l'export)      ESCLUSI.txt   cosa non deve mai apparire
  VIETATE.txt    stringhe vietate                    ECCEZIONI.txt eccezioni motivate, per riga
  SIMBOLI.txt    basi del book in una forma che il DB non porta (base<TAB>motivo): cercate dai
                 controlli 9 e 10 come le basi del DB, e contate a parte nella nota

Controlli: gitleaks · vietate · env (valori del .env vivo, in memoria) · esclusi · lotti
(ticker + quantita'/prezzo sulla stessa riga: DB + tuple dell'attic) · dimensione ·
valori_db (importi di cash_movements, nav_snapshots, positions, trade_history nei 3
formati) · lista_privata (VIETATI di prova_numeri_del_book.py) · ticker_soli (F1 04/09:
le basi dei ticker DA SOLE, senza quantita' ne' prezzo) · payload · valori_estesi · vietate_forme · testo_pm (13/09: il testo
libero dell'utente del DB copiato nel tree, regola a catena, v. COLONNE_TESTO_UTENTE). Verdetto: exit 0 solo se TUTTI eseguiti e a zero; 1 = hit; 2 = un
controllo non eseguito o guasto (regola 14/07: un cancello parziale non e' mai verde).
I controlli nominati in OSSERVAZIONE contano e si stampano ma NON cambiano l'exit, e
finche' ne resta uno con riscontri il verdetto non dice mai «PULITO» e basta.

Uso:  python tools/release/verifica_pubblico.py --tree <cartella esportata> [--solo vietate,env]
      python tools/release/verifica_pubblico.py --prova-env [--env <file>]  # autoprova del controllo 3 sul .env VERO: conteggi
      python tools/release/verifica_pubblico.py --scarica-gitleaks # download pinnato + sha256 in policy/bin/

Stato: `--tree` esegue i controlli registrati in `CONTROLLI` (`esegui_tutti`), e lo chiama anche
tools/release/export_pubblico.py in-process prima di depositare nel clone pubblico. `ticker_soli`
e' in OSSERVAZIONE: i riscontri sono dichiarati e diventera' bloccante
quando le cure D1/D2/D4 li avranno portati a zero — si toglie il suo nome da OSSERVAZIONE.
"""
import bisect
import difflib
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from dataclasses import dataclass, field

REPO = str(Path(__file__).resolve().parents[2])
PUBBLICO = str(Path(__file__).resolve().parent / "policy")
GITLEAKS_VERSIONE = "8.30.1"
GITLEAKS_URL = ("https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/"
                "gitleaks_8.30.1_windows_x64.zip")
GITLEAKS_SHA256 = "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e"
GITLEAKS_EXE = os.path.join(PUBBLICO, "bin", "gitleaks.exe")
GITLEAKS_EXIT_HIT = 7   # il codice che gitleaks usa per «leak trovati» SOLO se il giro e' finito
                        # bene: l'1 resta il suo codice di guasto (T6, v. controllo_gitleaks)
GITLEAKS_SCOPERTI = ("app/package-lock.json",)   # l'allowlist globale del config di default 8.30.1
                        # salta i lockfile: il MOTORE non li legge (li leggono i
                        # controlli in memoria). Ogni altro salto e' un KO, non una nota.
MAX_TREE = 15 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
BUDGET_SCREENSHOTS = "BUDGET_SCREENSHOTS_APPROVATO.json"
MIN_ENV = 8
MIN_TOKEN = 6
CONTROLLI = ("gitleaks", "vietate", "env", "esclusi", "lotti", "dimensione",
             "valori_db", "lista_privata", "ticker_soli", "payload",
             "valori_estesi", "vietate_forme",   # 05/09 (D5 4b): nati in osservazione, v. COLONNE_ESTESE
             "testo_pm")   # 13/09 (G3B): il testo libero dell'utente, nato in osservazione (v. COLONNE_TESTO_UTENTE)
OSSERVAZIONE = ("ticker_soli", "payload", "valori_estesi", "vietate_forme", "testo_pm")   # eseguiti e stampati, MA non cambiano l'exit: un controllo
                        # con collisioni ancora da classificare renderebbe il cancello rosso
                        # e verrebbe spento (lezione «una guardia che grida al lupo»). Il
                        # verdetto li DICHIARA e non dice mai «PULITO» e basta. Per renderne
                        # uno bloccante si toglie il suo nome da qui: una riga, e c'e' il test
                        # che prova che quella riga basta.


@dataclass
class Hit:
    file: str
    riga: int
    token: str          # GIA' mascherato


@dataclass
class Esito:
    nome: str
    hit: list = field(default_factory=list)
    note: str = ""
    errore: str = ""    # != "" = controllo NON eseguito o guasto (KO dichiarato)
    osservazione: bool = False   # conta e si stampa, ma non cambia l'exit (v. OSSERVAZIONE)
    misure: dict = field(default_factory=dict)  # public byte/file counts only; never corpus values

    @property
    def ok(self):
        return not self.hit and not self.errore


def _lista_da_testo(testo):
    righe = []
    for r in io.StringIO(testo):
        r = r.split("#", 1)[0].strip()
        if r:
            righe.append(r)
    return righe


def leggi_lista(path):
    """Righe non vuote, senza i commenti (`#` fino a fine riga), senza spazi ai lati."""
    with open(path, encoding="utf-8-sig") as fh:   # utf-8-sig: un BOM in testa non entra nel primo pattern
        return _lista_da_testo(fh.read())


def _eccezioni_da_testo(testo, fonte):
    ecc = set()
    for n, r in enumerate(io.StringIO(testo), 1):
        if not r.strip() or r.lstrip().startswith("#"):
            continue
        campi = r.rstrip("\r\n").split("\t")
        if len(campi) != 4 or not all(c.strip() for c in campi):
            raise ValueError("%s:%d: servono 4 campi tab-separati (file, controllo, token, motivo)" % (fonte, n))
        ecc.add((campi[0].strip(), campi[1].strip(), campi[2].strip()))
    return ecc


def leggi_eccezioni(path):
    """ECCEZIONI.txt: `file<TAB>controllo<TAB>token<TAB>motivo`, 4 campi tutti pieni o
    ValueError (una riga senza motivo non e' un'eccezione, e' un buco). File assente = nessuna."""
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8-sig") as fh:   # utf-8-sig: un BOM in testa non entra nel primo pattern
        return _eccezioni_da_testo(fh.read(), path)


def _risolvi_deroghe_upstream(tree, eccezioni, grandi):
    """Resolve exact path@sha256 pins against the bytes actually being scanned.

    Pinned exceptions cover only exact numeric/ticker coincidences in the four
    declared controls and the individual-file size limit. Secrets, forbidden
    content, Gitleaks and MAX_TREE still run without a pinned exemption.
    A changed pinned file is a gate error, including if shortened below MAX_FILE.
    Pins may cover upstream assets or attested synthetic fixtures after explicit
    approval. A fixture still requires full source review: the pin verifies its
    reviewed bytes and does not exempt source content from review.
    """
    verificati = {}

    def percorso(guardato):
        if "@sha256=" not in guardato:
            return guardato
        rel, digest = guardato.rsplit("@sha256=", 1)
        if (not re.fullmatch(r"[0-9a-f]{64}", digest) or "\\" in rel or ":" in rel
                or any(part in ("", ".", "..") for part in rel.split("/"))
                or any(char in rel for char in "*?")):
            raise ValueError("deroga upstream: percorso esatto e SHA-256 valido richiesti")
        if rel not in tree:
            return rel  # An absent receipt cannot exempt a filename containing the pin suffix.
        actual = verificati.get(rel)
        if actual is None:
            actual = hashlib.sha256(tree[rel]).hexdigest()
        if actual != digest:
            raise ValueError("SHA-256 upstream diverso per %s: deroga rifiutata" % rel)
        verificati[rel] = actual
        return rel

    risolte = set()
    for rel, controllo, token in eccezioni:
        if "@sha256=" in rel:
            numeric = controllo in {"lista_privata", "valori_estesi"} and re.fullmatch(r"[0-9]+(?:[.,][0-9]+)*", token)
            percentuale = controllo == "valori_estesi" and re.fullmatch(r"[0-9]+(?:[.,][0-9]+)*%", token)
            numeric = numeric or percentuale
            ticker = controllo in {"lotti", "ticker_soli"} and re.fullmatch(r"[A-Z0-9]+(?:[.-][A-Z0-9]+)*", token)
            if not (numeric or ticker):
                raise ValueError("deroga upstream ammessa solo per token esatti numerici/ticker dei controlli dichiarati")
        risolte.add((percorso(rel), controllo, token))
    return risolte, [percorso(rel) for rel in grandi], verificati


def _simboli_da_testo(testo, fonte):
    out = set()
    for n, r in enumerate(io.StringIO(testo), 1):
        if not r.strip() or r.lstrip().startswith("#"):
            continue
        campi = r.rstrip("\r\n").split("\t")
        if len(campi) != 2 or not all(c.strip() for c in campi):
            raise ValueError("%s:%d: servono 2 campi tab-separati (base, motivo)" % (fonte, n))
        out.add(campi[0].strip().upper())
    return out


def leggi_simboli(path):
    """SIMBOLI.txt: `base<TAB>motivo`, basi del book non derivabili dal ticker o dal nome
    presenti nel DB. Senza motivo non e' una dichiarazione, e' un buco: ValueError.
    File assente = nessun simbolo dichiarato. Le basi tornano MAIUSCOLE come quelle del DB."""
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8-sig") as fh:
        return _simboli_da_testo(fh.read(), path)


def _regex(pattern):
    """Glob delle liste: '*' e '?' non attraversano '/', '**' si', 'dir/' = la cartella e
    tutto sotto (anche con glob nel nome: 'mockup_*/')."""
    if pattern.endswith("/"):
        return re.compile(_regex(pattern[:-1]).pattern[:-1] + "/.*$")
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i) and (i == 0 or pattern[i - 1] == "/"):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out += "(?:/.*)?"
            i += 3
        elif pattern.startswith("**", i):
            # review T1: ogni altra forma degradava in silenzio a '*' (regola 14/07)
            raise ValueError("'**' ammesso solo come '**/x' o 'x/**': %r" % pattern)
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile("^" + out + "$")


def combacia(rel, pattern):
    """`rel` e' un percorso relativo in forma posix ('a/b.py')."""
    return _regex(pattern).match(rel) is not None


# --- T2: il tree in memoria, la maschera, i controlli sul testo, il verdetto ---------------------

def carica_tree(root, *, vuoto_ammesso=False):
    """{relpath posix: bytes} di tutto cio' che sta sotto `root`, saltando `.git`.
    In memoria: i controlli non toccano piu' il disco e l'autoprova (T3) costruisce
    lo stesso dict senza scrivere nulla. Root inesistente, root che e' un file o cartella
    vuota = eccezione (review T2 F1: os.walk su un percorso sbagliato non entra nel ciclo
    e il verdetto usciva PULITO su zero file — regola 14/07). `.git` si salta sia come
    cartella sia come FILE (worktree: dentro c'e' il percorso assoluto del repo privato,
    review T2 F7). Le junction NON si saltano: os.walk le attraversa e il tree le legge
    (verso stretto: cio' che una copia pubblicherebbe come cartella vera va scansionato)."""
    if not os.path.isdir(root):
        raise FileNotFoundError("tree non trovato (non e' una cartella): %s" % root)
    tree = {}
    for cartella, sub, nomi in os.walk(root):
        sub[:] = [d for d in sub if d != ".git"]
        nomi = [n for n in nomi if n != ".git"]
        for n in nomi:
            p = os.path.join(cartella, n)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            with open(p, "rb") as fh:
                tree[rel] = fh.read()
    if not tree and not vuoto_ammesso:
        raise ValueError("tree vuoto: nessun file sotto %s" % root)
    return tree


def maschera(token):
    """Primi 2 caratteri + lunghezza: quanto basta a riconoscere QUALE stringa ha
    colpito senza scriverla (l'output del cancello finisce in log e chat).
    SOTTO I 4 CARATTERI NON ESCE NESSUN CARATTERE: su un token di 2 caratteri il
    prefisso rivelerebbe il token intero. I token corti diventano indistinguibili
    nell'output; il conteggio e il file:riga restano, il simbolo no."""
    if len(token) < 4:
        return "…(%d)" % len(token)
    return "%s…(%d)" % (token[:2], len(token))


def _screenshot_verifier():
    """Load trusted verifier code, never Python from the artifact being scanned."""
    if not hasattr(_screenshot_verifier, "module"):
        import importlib.util
        path = Path(__file__).resolve().parents[2] / "docs" / "guide" / "verify_screenshots.py"
        spec = importlib.util.spec_from_file_location("_release_screenshot_verifier", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _screenshot_verifier.module = module
    return _screenshot_verifier.module


def _raster_pins_da_testo(testo):
    """Private visual ratification: exact image and whole public-manifest hashes."""
    verifier = _screenshot_verifier()
    value = verifier._json(testo.encode("utf-8"))
    verifier._keys(value, ("schema_version", "screenshots"), "private screenshot ratification")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or not isinstance(value["screenshots"], list):
        raise ValueError("ratifica screenshot: versione/forma non supportata")
    seen = set()
    for row in value["screenshots"]:
        verifier._keys(row, ("path", "sha256", "manifest_sha256"), "private screenshot pin")
        rel = verifier._path(row["path"], verifier.SCREENSHOT_DIR, ".png")
        if rel in seen or any(char in rel for char in "*?"):
            raise ValueError("ratifica screenshot: percorso duplicato o non esatto")
        seen.add(rel)
        verifier._digest(row["sha256"])
        verifier._digest(row["manifest_sha256"])
    return value["screenshots"]


def _raster_verificati(tree, pins=None):
    """Structural provenance AND private visual ratification, rechecked on bytes."""
    pins = _raster_pins_da_testo(json.dumps({"schema_version": 1, "screenshots": pins or []}))
    verified = _screenshot_verifier().verify_tree(tree)
    by_path = {row["path"]: row for row in pins}
    if set(verified) != set(by_path):
        raise ValueError("PNG non ratificati o inventario diverso dalla ratifica privata")
    for rel, receipt in verified.items():
        if any(receipt[key] != by_path[rel][key] for key in ("sha256", "manifest_sha256")):
            raise ValueError("PNG/manifest cambiato rispetto alla review visiva: " + rel)
    return verified


class _TreeRaster(dict):
    """Carry frozen private pins with the in-memory bytes of all text controls."""
    def __init__(self, tree, pins):
        super().__init__(tree)
        self.raster_pins = json.loads(json.dumps(pins))
        _raster_verificati(self, self.raster_pins)


def _testi(tree):
    raster = _raster_verificati(tree, getattr(tree, "raster_pins", None))
    return {rel: b.decode("utf-8", "replace") for rel, b in tree.items() if rel not in raster}


def _righe(t):
    """Le righe di un testo come le conta grep -n e l'editor: split su '\\n' e non
    splitlines() (che conta anche il form feed: fix_price_updater.ps1:8 ne ha uno e
    sposterebbe i numeri), senza il '\\r' dei file CRLF (review T2 F2: una regex con
    '$' non combaciava mai). La usano _cerca (T2-T4) e controllo_lotti (T5)."""
    return [r.rstrip("\r") for r in t.split("\n")]


def _cerca(testi, token, senza_maiuscole=False, regex=None):
    """Grezzi (file, riga, token) per ogni riga che contiene il token (o combacia con
    la regex, che DEVE contenere il token letterale: il pre-filtro `token in testo`
    vale per entrambi). Righe numerate da _righe()."""
    hit = []
    ago = token.lower() if senza_maiuscole else token
    for rel in sorted(testi):
        t = testi[rel]
        if ago not in (t.lower() if senza_maiuscole else t):
            continue
        for n, riga in enumerate(_righe(t), 1):
            if regex is not None:
                if regex.search(riga):
                    hit.append((rel, n, token))
            elif ago in (riga.lower() if senza_maiuscole else riga):
                hit.append((rel, n, token))
    return hit


def _applica_eccezioni(grezzi, controllo, eccezioni):
    """Toglie i grezzi coperti da una riga di ECCEZIONI.txt (stesso file, stesso
    controllo, stesso token — il token copiato IDENTICO alla lista, maiuscole comprese)
    e conta quante ne ha applicate. Torna anche le eccezioni di QUESTO controllo che non
    hanno coperto nulla (review T2 F4: un refuso nel file o nel token lascia l'hit, giusto,
    ma la riga stantia restava invisibile)."""
    rimasti, applicate, usate = [], 0, set()
    for (rel, n, tok) in grezzi:
        chiave = (rel, controllo, tok)
        if chiave in eccezioni:
            applicate += 1
            usate.add(chiave)
        else:
            rimasti.append((rel, n, tok))
    senza_riscontro = {e for e in eccezioni if e[1] == controllo} - usate
    return rimasti, applicate, senza_riscontro


def _plurale(n, singolare, plurale):
    return "1 %s" % singolare if n == 1 else "%d %s" % (n, plurale)


def _esito(nome, grezzi, eccezioni, note="", etichette=None):
    """Esito con i token GIA' mascherati; nella nota le eccezioni applicate e quelle
    senza riscontro. Con `etichette` (token -> origine, es. 'positions.prezzo_medio') il
    token stampato e' ORIGINE…(lunghezza): per un importo due cifre in chiaro sono gia'
    troppe (T4), e l'origine dice al PM da dove viene il numero."""
    rimasti, applicate, senza = _applica_eccezioni(grezzi, nome, set(eccezioni or ()))
    if applicate:
        note = (note + "; " if note else "") + _plurale(applicate, "eccezione applicata", "eccezioni applicate")
    if senza:
        note = (note + "; " if note else "") + _plurale(len(senza), "eccezione senza riscontro", "eccezioni senza riscontro")
    etichette = etichette or {}

    def _tok(t):
        return "%s…(%d)" % (etichette[t], len(t)) if t in etichette else maschera(t)

    return Esito(nome, [Hit(r, n, _tok(t)) for (r, n, t) in rimasti], note=note)


def controllo_vietate(tree, vietate, eccezioni=()):
    """Controllo 2: ogni stringa di VIETATE.txt cercata senza distinzione di maiuscole
    in ogni file del tree; un hit per riga e per stringa. Una stringa con la barra rovescia
    (un percorso) si cerca anche ESCAPATA (`\\\\` in JSON e nelle stringhe Python) e con le
    barre in avanti (buco D5, 05/09): il token resta la stringa della lista, cosi' una riga di
    ECCEZIONI.txt la copre in tutte le forme."""
    testi = _testi(tree)
    grezzi = []
    for v in vietate:
        grezzi += _cerca(testi, v, senza_maiuscole=True)
    return _esito("vietate", grezzi, eccezioni, note="%d stringhe cercate" % len(vietate))


def controllo_vietate_forme(tree, vietate, eccezioni=()):
    """Controllo 2-bis (05/09, buco D5 4b), in OSSERVAZIONE finche' il conto non e' zero: le
    stringhe di VIETATE.txt con la barra rovescia (percorsi) cercate nelle forme che la ricerca
    letterale perde — ESCAPATA (`\\\\`, JSON e stringhe Python) e con le barre in avanti. Solo
    i riscontri NUOVI rispetto al controllo 2 (la forma letterale li conta gia'); il token resta
    la stringa della lista, cosi' una riga di ECCEZIONI.txt (controllo `vietate_forme`) la copre.
    Quando i riscontri saranno curati o derogati, il nome esce da OSSERVAZIONE e le forme
    entrano nel controllo 2."""
    testi = _testi(tree)
    letterali = set()
    grezzi = set()
    for v in vietate:
        if "\\" not in v:
            continue
        letterali |= {(rel, n) for (rel, n, _) in _cerca(testi, v, senza_maiuscole=True)}
        for f in (v.replace("\\", "\\\\"), v.replace("\\", "/")):
            grezzi |= {(rel, n, v) for (rel, n, _) in _cerca(testi, f, senza_maiuscole=True)}
    grezzi = {g for g in grezzi if (g[0], g[1]) not in letterali}
    con_barra = sum(1 for v in vietate if "\\" in v)
    return _esito("vietate_forme", sorted(grezzi), eccezioni,
                  note="%d percorsi cercati escapati e con le barre in avanti; solo i riscontri che la forma letterale non da'" % con_barra)


def controllo_esclusi(tree, esclusi):
    """Controllo 4: nessun file del tree deve combaciare con ESCLUSI.txt (un glob
    troppo largo nell'allowlist si vede qui)."""
    hit = [Hit(rel, 0, "-") for rel in sorted(tree) if any(combacia(rel, e) for e in esclusi)]
    return Esito("esclusi", hit, note="%d pattern" % len(esclusi))


def _budget_screenshot_da_testo(testo):
    """Private size ratification, separate from visual pins; no reusable allowance."""
    verifier = _screenshot_verifier()
    value = verifier._json(testo.encode("utf-8"))
    verifier._keys(value, ("schema_version", "manifest_sha256", "files", "bytes"), "screenshot budget")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("budget screenshot: versione non supportata")
    verifier._digest(value["manifest_sha256"])
    if any(type(value[key]) is not int or value[key] <= 0 for key in ("files", "bytes")):
        raise ValueError("budget screenshot: conteggio file e byte devono essere interi positivi")
    return value


def _pacchetto_screenshot(tree, budget):
    """Exact verified PNG/DOM/manifest inventory, for size accounting only."""
    budget = _budget_screenshot_da_testo(json.dumps(budget))
    raster = _raster_verificati(tree, getattr(tree, "raster_pins", None))
    if not raster:
        raise ValueError("budget screenshot: pacchetto ratificato assente")
    if any(row["manifest_sha256"] != budget["manifest_sha256"] for row in raster.values()):
        raise ValueError("budget screenshot: manifest diverso dalla ratifica dimensionale")
    paths = set(raster) | {row["dom_path"] for row in raster.values()} | {_screenshot_verifier().MANIFEST_PATH}
    if len(paths) != budget["files"] or sum(len(tree[p]) for p in paths) != budget["bytes"]:
        raise ValueError("budget screenshot: conteggio file o byte diverso dalla ratifica dimensionale")
    return paths


def controllo_dimensione(tree, grandi_ammessi=(), max_tree=MAX_TREE, max_file=MAX_FILE,
                         *, budget_screenshot=None):
    """Controllo 6: ordinary bytes <= MAX_TREE; exact ratified captures counted separately.

    Without the optional private size ratification the entire tree keeps the old limit.
    Every capture file retains MAX_FILE, including when named in an upstream exception.
    This separation grants no exemption from any text or secret scan.
    """
    catture = set() if budget_screenshot is None else _pacchetto_screenshot(tree, budget_screenshot)
    ammessi = set(grandi_ammessi) - catture
    hit = [Hit(rel, 0, "%d byte" % len(b)) for rel, b in sorted(tree.items())
           if len(b) > max_file and rel not in ammessi]
    totale = sum(len(b) for b in tree.values())
    byte_catture = sum(len(tree[p]) for p in catture)
    ordinario = totale - byte_catture
    if ordinario > max_tree:
        hit.append(Hit("<tree>", 0, "%d byte ordinari" % ordinario))
    note = "%d file, %d byte" % (len(tree), totale)
    if budget_screenshot is not None:
        note += "; ordinario %d file, %d byte; catture ratificate %d file, %d byte" % (
            len(tree) - len(catture), ordinario, len(catture), byte_catture)
    misure = {"file_totali": len(tree), "byte_totali": totale,
              "file_ordinari": len(tree) - len(catture), "byte_ordinari": ordinario,
              "file_catture": len(catture), "byte_catture": byte_catture}
    return Esito("dimensione", hit, note=note, misure=misure)


def verdetto(esiti, non_eseguiti=()):
    """Stampa la tabella (file:riga + token mascherato, mai un valore) e torna l'exit:
    0 solo se tutti i controlli BLOCCANTI sono stati eseguiti e stanno a zero; 1 = almeno
    un hit bloccante; 2 = un controllo bloccante non eseguito (--solo) o guasto. Un
    cancello parziale non e' verde. I controlli in OSSERVAZIONE non entrano nell'exit —
    ne' i loro hit ne' i loro KO — ma finiscono SEMPRE nella riga del verdetto, che in
    quel caso non dice «PULITO» ma «PULITO sui controlli bloccanti»: il giorno in cui
    questa distinzione sparisce dalla frase, il cancello ricomincia a dire piu' di quanto
    misura (l'errore del 03/09)."""
    print("%-14s %6s  %s" % ("controllo", "hit", "esito / note"))
    rc = 0
    oss_hit, oss_ko = [], []
    for e in esiti:
        if e.errore:
            print("%-14s %6s  %sKO (non eseguito/guasto): %s" % (
                e.nome, "-", "OSSERVAZIONE " if e.osservazione else "", e.errore))
            if e.osservazione:
                oss_ko.append(e.nome)
            else:
                rc = max(rc, 2)
        else:
            stato = "OSSERVAZIONE (non blocca)" if (e.osservazione and e.hit) else ("OK" if e.ok else "STOP")
            print("%-14s %6d  %s%s" % (e.nome, len(e.hit), stato, ("  " + e.note) if e.note else ""))
            for h in e.hit[:200]:
                print("    %s:%d  %s" % (h.file, h.riga, h.token))
            if len(e.hit) > 200:
                print("    ... altri %d hit non stampati" % (len(e.hit) - 200))
            if e.hit:
                if e.osservazione:
                    oss_hit.append((e.nome, len(e.hit)))
                else:
                    rc = max(rc, 1)
    for n in non_eseguiti:
        print("%-14s %6s  NON ESEGUITO (--solo): il verdetto non puo' essere verde" % (n, "-"))
        rc = max(rc, 2)
    titolo = {0: "PULITO", 1: "STOP", 2: "INCOMPLETO"}[rc]
    coda = ""
    if oss_hit:
        coda += " — %s in osservazione: %s" % (
            _plurale(sum(n for _, n in oss_hit), "riscontro", "riscontri"),
            ", ".join("%s (%d)" % (nm, n) for nm, n in oss_hit))
    if oss_ko:
        coda += " — %s: %s" % (
            _plurale(len(oss_ko), "controllo in osservazione NON eseguito",
                     "controlli in osservazione NON eseguiti"), ", ".join(oss_ko))
    if rc == 0 and coda:
        titolo = "PULITO sui controlli bloccanti"   # mai «PULITO» e basta con qualcosa di scoperto
    print("VERDETTO: %s (exit %d)%s" % (titolo, rc, coda))
    return rc


# --- T3: controllo 3, i valori del .env vivo (in memoria) e l'autoprova ------------------------

def _grezzo(riga):
    """Il lato destro di una riga `K=...` del .env com'e' SCRITTO: senza le virgolette
    esterne (rfind dello stesso carattere: regge `"..." # commento`), spazi ai lati via."""
    lato = riga.rstrip("\r\n").split("=", 1)[1].strip() if "=" in riga else ""
    if lato[:1] in ('"', "'") and lato.rfind(lato[0]) > 0:
        lato = lato[1:lato.rfind(lato[0])]
    return lato


def _coppie_env(path_env):
    """(nome, valore) del .env via dotenv_values: niente finisce nell'ambiente, niente su
    disco. FileNotFoundError se il file manca (regola 14/07: un .env assente non e'
    «0 valori = pulito», e' un KO). ValueError, coi SOLI numeri di riga, se dotenv
    salterebbe una riga che non capisce (review T3 F1: lo diceva con un WARNING nel log
    e il valore su quella riga non veniva mai cercato). Se il valore decodificato non
    sta nella riga grezza (escape `\\"`, `\\t`: review T3 F5) entra anche la forma
    grezza sotto lo stesso nome, cosi' una riga del .env incollata tal quale si trova."""
    if not os.path.isfile(path_env):
        raise FileNotFoundError(".env non trovato: %s" % path_env)
    from dotenv import dotenv_values
    from dotenv.parser import parse_stream
    with open(path_env, encoding="utf-8-sig") as fh:
        bindings = list(parse_stream(fh))
    rotte = [b.original.line for b in bindings if b.error]
    if rotte:
        raise ValueError(".env: righe non interpretabili, dotenv le salterebbe (numeri di riga): %s" % rotte)
    coppie = [(k, (v or "").strip()) for k, v in dotenv_values(path_env, encoding="utf-8-sig").items()]
    for b in bindings:
        if b.key and (b.value or "").strip() not in b.original.string:
            grezzo = _grezzo(b.original.string)
            if grezzo and (b.key, grezzo) not in coppie:
                coppie.append((b.key, grezzo))
    return coppie


def valori_env(path_env):
    """[(nome, valore)] con valore da MIN_ENV caratteri in su, nell'ordine del file. Il
    nome serve alla maschera (NOME…(lunghezza)): il valore non si stampa MAI, nemmeno
    due caratteri (un PIN o una password di 8 caratteri li regalerebbe)."""
    return [(k, v) for k, v in _coppie_env(path_env) if len(v) >= MIN_ENV]


def variabili_corte(path_env):
    """I NOMI delle variabili con un valore non vuoto sotto MIN_ENV caratteri: non si
    cercano (troppo corti per essere univoci nel tree) e il verdetto lo dichiara."""
    return list(dict.fromkeys(k for k, v in _coppie_env(path_env) if 0 < len(v) < MIN_ENV))


def _pezzi(valore):
    """Cosa si cerca di un valore: il valore intero, oppure — se dotenv l'ha letto su piu'
    righe (virgolette multiriga, `\\n` letterale, chiavi PEM: review T3 F4) — i suoi
    pezzi da MIN_ENV caratteri in su, perche' la ricerca e' riga per riga. Vuoto = il
    valore non e' cercabile, e il controllo lo dichiara."""
    if "\n" not in valore:
        return [valore]
    return [p.strip() for p in valore.split("\n") if len(p.strip()) >= MIN_ENV]


def _valori_template(testo):
    """{NOME: valore} delle righe `NOME=valore` di un .env.example (valore spogliato di spazi e
    virgolette; le righe vuote o commentate non contano). Serve a riconoscere i valori del .env
    vero che sono gia' pubblici perche' identici al default committato."""
    out = {}
    for riga in testo.split("\n"):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", riga.rstrip("\r"))
        if m:
            out[m.group(1)] = m.group(2).strip().strip("'\"").strip()
    return out


def controllo_env(tree, coppie, corte=(), eccezioni=()):
    """Controllo 3: ogni valore del .env cercato letteralmente (maiuscole comprese) in
    ogni file del tree, a pezzi se sta su piu' righe. Token = nome della variabile +
    lunghezza del valore. Nella nota, per nome: le variabili corte e i valori non
    cercabili. Zero valori = KO dichiarato, non «0 hit» (review T3 F1: T7 chiama questo
    controllo direttamente). ECCEZIONI: la regola «un valore del .env non ha mai un motivo
    per stare nel pubblico» e' caduta il 03/09 con la decisione D3 (il nome del PM nel
    NOTICE, che il `.env` tiene in `PM_NAME`). La chiave e' il NOME della variabile, mai il
    valore — il file delle eccezioni non deve contenere un segreto — e una riga si scrive
    solo dove quel valore e' pubblico PER DECISIONE del PM: mai per una chiave."""
    if not coppie:
        return Esito("env", errore="nessun valore da %d caratteri nel .env: non e' un verde" % MIN_ENV)
    testi = _testi(tree)
    # 05/09 (voce 8): un valore del .env IDENTICO al default committato in .env.example e'
    # pubblico per costruzione (per esempio gli slug dei modelli nel template)
    # — non si cerca, e la nota lo dice per nome. Diverso dal template =
    # si cerca; template assente nel tree = non si esclude nulla.
    template = _valori_template(testi.get(".env.example", ""))
    pubbliche = list(dict.fromkeys(n for n, v in coppie if template.get(n) == v))
    coppie = [(n, v) for n, v in coppie if template.get(n) != v]
    nomi = {}
    for nome, valore in coppie:
        if nome not in nomi.setdefault(valore, []):
            nomi[valore].append(nome)
    grezzi, non_cercabili = [], []
    for valore, suoi in nomi.items():
        pezzi = _pezzi(valore)
        if not pezzi:
            non_cercabili += suoi
            continue
        for pezzo in pezzi:
            grezzi += [(r, n, valore) for (r, n, _) in _cerca(testi, pezzo)]
    grezzi = list(dict.fromkeys(grezzi))          # due pezzi sulla stessa riga = un hit
    lung = {}                                     # il token e' il NOME, la lunghezza resta del VALORE
    per_nome = []
    for (r, n, v) in grezzi:
        etichetta = "/".join(nomi[v])
        lung[etichetta] = len(v)
        per_nome.append((r, n, etichetta))
    rimasti, applicate, senza = _applica_eccezioni(per_nome, "env", set(eccezioni or ()))
    hit = [Hit(r, n, "%s…(%d)" % (t, lung[t])) for (r, n, t) in rimasti]
    note = "%d valori cercati (da %d caratteri)" % (len(coppie), MIN_ENV)
    if pubbliche:
        note += "; %s: %s" % (_plurale(len(pubbliche), "valore identico al template pubblicato non cercato",
                                        "valori identici al template pubblicato non cercati"), ", ".join(pubbliche))
    if applicate:
        note += "; " + _plurale(applicate, "eccezione applicata", "eccezioni applicate")
    if senza:
        note += "; " + _plurale(len(senza), "eccezione senza riscontro", "eccezioni senza riscontro")
    if corte:
        note += "; %s: %s" % (_plurale(len(corte), "variabile con valore corto non cercata",
                                        "variabili con valore corto non cercate"), ", ".join(corte))
    if non_cercabili:
        note += "; %s (pezzi sotto %d caratteri): %s" % (
            _plurale(len(non_cercabili), "valore non cercabile", "valori non cercabili"), MIN_ENV, ", ".join(non_cercabili))
    return Esito("env", hit, note=note)


def prova_env(coppie):
    """Autoprova del controllo 3: un tree IN MEMORIA con un file per coppia; ogni file
    deve cadere. Ritorna (valori, visti cadere, nomi NON caduti): la garanzia e' una
    misura, non una frase, e il KO dice QUALE variabile (il nome, mai il valore: review
    T3 F6). T7 la lancia a ogni run prima del controllo (review T3 F4)."""
    tree = {"prova/%03d.txt" % i: ("x %s y\n" % v).encode("utf-8") for i, (_, v) in enumerate(coppie)}
    caduti = {h.file for h in controllo_env(tree, coppie).hit} if coppie else set()
    non_caduti = [nome for i, (nome, _) in enumerate(coppie) if "prova/%03d.txt" % i not in caduti]
    return len(coppie), len(caduti), non_caduti


# --- T4: controllo A (valori vivi del DB nei 3 formati) e controllo B (lista privata) ----------

COLONNE_VIVE = (("cash_movements", "amount_eur"), ("nav_snapshots", "nav_total_eur"),
                ("nav_snapshots", "invested_eur"), ("nav_snapshots", "cash_eur"),
                ("positions", "quantita"), ("positions", "prezzo_medio"),
                ("trade_history", "quantita"), ("trade_history", "prezzo"),
                ("trade_history", "realized_local"), ("trade_history", "realized_eur"))   # T4: +2 rispetto al piano (P&L realizzati: stessa classe)
# 05/09 (buco D5, 4b): le guidance del PM (low/mid/high; quelle in percentuale hanno anche le forme
# col %, v. percentuali_db) e i prezzi delle posizioni (ricerca accelerata dal
# pre-filtro _candidati_numerici). Nascono nel controllo `valori_estesi`, in OSSERVAZIONE: misurati
# il 05/09 su HEAD danno riscontri in test, app e script (collisioni con cifre di fixture) che
# fermerebbero ogni push; quando il conto e' zero entrano in COLONNE_VIVE e il nome esce da
# OSSERVAZIONE, come per ticker_soli e payload.
COLONNE_ESTESE = (("company_guidance", "value_low"), ("company_guidance", "value_mid"),
                  ("company_guidance", "value_high"), ("position_prices", "prezzo"))


def _migliaia(n):
    return "{:,}".format(int(n)).replace(",", ".")


def forme_numero(v):
    """Regola calibrata (piano T4): cercare tutte le forme da 5 caratteri produce anche
    collisioni fra importi tondi, timeout e fixture.
    INTERO: la forma con le migliaia ('27.500'), che col filtro MIN_TOKEN=6
    vale da 10.000 in su ('5.500' ha 5 caratteri: BUCO DICHIARATO, un importo intero a
    4 cifre non si cerca in nessuna forma; le quantita' sotto 1.000 le copre il controllo
    5 col ticker); le cifre nude da 1.000.000. DECIMALI: '1234.56', '1234,56', '1.234,56';
    con 1 decimale anche repr col punto, con la virgola e con le migliaia ('2345.5',
    '2.345,5': e' come un REAL del DB finisce in una fixture, review T4 F3); con piu' di
    2 decimali repr col punto e con la virgola. Il segno non e' il segreto: si lavora
    sul valore assoluto. Token da MIN_TOKEN caratteri in su.
    ZERO: nessuna forma (06/09). Uno zero non porta informazione — non si puo' dedurre da un
    token quale valore del book vale 0 — e le sue forme colpiscono i formati e gli inizializzatori
    di mezzo repo. Buco DICHIARATO nella nota del controllo, non silenzioso."""
    v = abs(float(v))
    if v == 0:
        return set()
    out = set()
    if v == int(v):
        out.add(_migliaia(v))
        if v >= 1_000_000:
            out.add("%d" % int(v))
    else:
        s = "%.2f" % v
        intero, dec = s.split(".")
        out.update({s, intero + "," + dec, _migliaia(int(intero)) + "," + dec})
        r = repr(v)
        if "." in r and len(r.split(".")[1]) > 2 and "e" not in r:
            out.update({r, r.replace(".", ",")})
        if "." in r and len(r.split(".")[1]) == 1 and "e" not in r:   # review T4 F3
            i1, d1 = r.split(".")
            out.update({r, i1 + "," + d1, _migliaia(int(i1)) + "," + d1})
    return {f for f in out if len(f) >= MIN_TOKEN}


def forme_percentuale(v):
    """Le forme di una PERCENTUALE del book: «27,5%», «27.5%», «27,50%», «27.50%», e «27%» se
    intera; anche con lo spazio prima del segno. Il segno % e' la specificita' che la lunghezza
    non da': NIENTE filtro MIN_TOKEN (buco D5, 05/09: «27,5%» ha 5 caratteri e spariva).
    Il segno meno non e' il segreto.
    ZERO: nessuna forma (06/09). Il token "0.00%" e' anche il FORMATO NUMERICO
    dei fogli Excel: trovarlo nel codice non prova la presenza di un dato finanziario.
    Uno zero non e' un segreto: da "0.00%" nessuno deduce QUALE guidance del PM valga zero,
    mentre un controllo che grida su un formato e' quello che poi qualcuno spegne. Il buco
    e' DICHIARATO nella nota del controllo, non silenzioso."""
    v = abs(float(v))
    if v == 0:
        return set()
    out = set()
    if v == int(v):
        out |= {"%d%%" % int(v), "%d,0%%" % int(v), "%d.0%%" % int(v)}
    s = "%.2f" % v
    i, d = s.split(".")
    out |= {s + "%", i + "," + d + "%"}
    r = repr(v)
    if "." in r and "e" not in r and len(r.split(".")[1]) <= 2:
        out |= {r + "%", r.replace(".", ",") + "%"}
    # specificita' minima: almeno 4 caratteri prima del segno («27,5%», «27,00%»; NON «27%» ne'
    # «5%»: misurato il 05/09 su HEAD, «5%» colpiva CSS e fixture ovunque — un'intera in
    # percentuale senza decimali non e' cercabile, buco DICHIARATO)
    out = {f for f in out if len(f[:-1]) >= 4}
    return out | {f[:-1] + " %" for f in out}


_RUN_NUMERICO = re.compile(r"\d[\d.,]*\d|\d")


def _candidati_numerici(testi, *, max_length=None):
    """I numeri del tree, estratti UNA volta: ogni sequenza di cifre e separatori, e ogni suo
    pezzo che comincia a un gruppo di cifre e finisce a un gruppo di cifre («27.500,00» da'
    anche «27.500» e «500,00»). E' un SOVRAINSIEME di cio' che `_regex_numero` accetta (mai una
    cifra prima o dopo il token): un token fuori da qui non puo' essere un hit, e non si cerca.
    Serve perche' `position_prices` porta ~18.000 prezzi (~80.000 token): cercarli uno per uno
    in ogni file costerebbe minuti a ogni push (05/09, 4b).
    `max_length` e' la lunghezza massima dei token EFFETTIVAMENTE cercati: sequenze
    piu' lunghe non possono uguagliarne uno. Questo evita le sottosequenze inutili
    degli array minificati, senza limitare file, valori o posizioni esaminate."""
    out = set()
    for t in testi.values():
        for run in _RUN_NUMERICO.findall(t):
            pezzi = re.split(r"([.,])", run)
            for i in range(0, len(pezzi), 2):
                acc = ""
                for j in range(i, len(pezzi)):
                    acc += pezzi[j]
                    if max_length is not None and len(acc) > max_length:
                        break
                    if j % 2 == 0 and pezzi[j]:
                        out.add(acc)
    return out


def _regex_numero(token):
    """Confini numerici (review T4 F2). A sinistra mai una cifra ('98765.43' non dentro
    '198765.43'); se il token comincia con 3 cifre + separatore nemmeno 'cifra.' davanti
    ('555,55' non dentro '1.555,55': e' un altro numero); 'DEPOSIT,27.500' e
    '[98765.43,555.55]' invece colpiscono. A destra mai una cifra; per i token INTERI
    ('27.500', '1500000') nemmeno separatore + cifre diverse da zero o + 3 cifre
    ('27.500,50' e '27.500.000' sono altri numeri; '27.500,00' e '1500000.0' lo stesso).
    Buchi dichiarati: la notazione inglese '1,234.56' non e' una forma cercata da
    forme_numero (T4; i lotti di T5 la cercano, review T5 F2, e '1,234' ha i confini di
    un intero); una tupla SQL con ticker + cifre la copre il controllo 5."""
    sinistra = r"(?<!\d)"
    if re.match(r"\d{3}[.,]", token):
        sinistra += r"(?<!\d\.)"
    destra = r"(?!\d)"
    if re.fullmatch(r"\d+|\d{1,3}([.,]\d{3})+", token):   # review T5 F2: anche '1,234' e' un intero
        destra += r"(?![.,]\d*[1-9])(?![.,]\d{3})"
    return re.compile(sinistra + re.escape(token) + destra)


def _apri_ro(db_path):
    """Il DB del PM si apre SOLO in sola lettura (uri mode=ro): P2 non scrive mai."""
    if not os.path.isfile(db_path):
        raise FileNotFoundError("DB non trovato: %s" % db_path)
    from urllib.parse import quote
    # review T4 F6: senza quote un '#' nel percorso troncava l'URI, mode=ro spariva e sqlite
    # CREAVA un file spurio; '%41' veniva decodificato
    return sqlite3.connect("file:%s?mode=ro" % quote(db_path.replace("\\", "/"), safe="/:"), uri=True)


def valori_db(db_path, colonne=None):
    """{valore: 'tabella.colonna'} delle COLONNE_VIVE (o delle `colonne` passate: COLONNE_ESTESE
    per il controllo in osservazione; piu' origini unite da '/'): l'origine e' la maschera del
    token, mai una cifra. Una colonna assente = OperationalError dichiarato, non «0 valori»."""
    c = _apri_ro(db_path)
    try:
        out = {}
        for tab, col in (colonne or COLONNE_VIVE):
            for (v,) in c.execute("SELECT %s FROM %s" % (col, tab)):
                if v is not None:
                    origine = "%s.%s" % (tab, col)
                    out.setdefault(float(v), [])
                    if origine not in out[float(v)]:
                        out[float(v)].append(origine)
        return {v: "/".join(o) for v, o in out.items()}
    finally:
        c.close()


def percentuali_db(db_path):
    """{valore: origine} delle guidance del PM espresse in PERCENTUALE (`company_guidance.unit`
    che comincia per `pct`: margini, crescite): cercate con `forme_percentuale`, fuori dal filtro
    di lunghezza (buco D5, 05/09). Le guidance in valuta restano a valori_db. Tabella assente =
    OperationalError dichiarato, non «0 percentuali»."""
    c = _apri_ro(db_path)
    try:
        out = {}
        for (u, lo, mid, hi) in c.execute("SELECT unit, value_low, value_mid, value_high FROM company_guidance"):
            if not (u or "").strip().lower().startswith("pct"):
                continue
            for col, v in (("value_low", lo), ("value_mid", mid), ("value_high", hi)):
                if v is not None:
                    origine = "company_guidance.%s" % col
                    out.setdefault(float(v), [])
                    if origine not in out[float(v)]:
                        out[float(v)].append(origine)
        return {v: "/".join(o) for v, o in out.items()}
    finally:
        c.close()


def _etichette(origini, forme):
    """{token: origini unite da '/'} per la maschera; un set al posto del dict = maschera generica."""
    etich = {}
    for v, origine in origini.items():
        for f in forme(v):
            if origine:
                etich.setdefault(f, [])
                for o in origine.split("/"):
                    if o not in etich[f]:
                        etich[f].append(o)
    return {f: "/".join(o) for f, o in etich.items()}


def controllo_valori_db(tree, valori, eccezioni=(), percentuali=None, *,
                        nome_controllo="valori_db"):
    """Controllo A: ogni valore vivo del DB, nelle sue forme, cercato con confini numerici
    in ogni file del tree. `valori` = dict valore -> origine (valori_db) o un set;
    `percentuali` = dict valore -> origine delle guidance in percentuale (percentuali_db),
    cercate con le forme col % e senza filtro di lunghezza (buco D5, 05/09). I token si
    cercano solo se stanno fra i numeri del tree (`_candidati_numerici`: un sovrainsieme)."""
    origini = valori if isinstance(valori, dict) else {v: None for v in valori}
    percentuali = percentuali or {}
    if not origini:   # review T4 F1: un DB con lo schema e 0 righe e' un path sbagliato, non un verde
        return Esito(nome_controllo, errore="nessun valore dal DB da cercare: non e' un verde (DB vuoto = path sbagliato?)")
    token = set()
    for v in origini:
        token |= forme_numero(v)
    token_pct = set()
    for v in percentuali:
        token_pct |= forme_percentuale(v)
    if not token and not token_pct:
        return Esito(nome_controllo, errore="%d valori ma nessun token cercabile: non e' un verde" % len(origini))
    testi = _testi(tree)
    numeric_tokens = token | {t.rstrip("% ").strip() for t in token_pct}
    candidati = _candidati_numerici(testi, max_length=max(map(len, numeric_tokens)))
    grezzi = []
    for t in sorted(token):
        if t in candidati:
            grezzi += _cerca(testi, t, regex=_regex_numero(t))
    for t in sorted(token_pct):
        if t.rstrip("% ").strip() in candidati:
            grezzi += _cerca(testi, t, regex=re.compile(r"(?<![\d.,])" + re.escape(t)))
    note = "%d valori, %d token (interi a 4 cifre senza decimali NON cercati)" % (len(origini), len(token))
    # 06/09: uno ZERO non genera token (v. forme_numero/forme_percentuale). Il buco si DICHIARA:
    # un controllo che smette di cercare qualcosa in silenzio e' peggio di uno che non lo cercava.
    zeri = sum(1 for v in origini if float(v) == 0) + sum(1 for v in percentuali if float(v) == 0)
    if zeri:
        note += "; %s (uno zero non e' un segreto)" % _plurale(
            zeri, "valore ZERO non cercato", "valori ZERO non cercati")
    if percentuali:
        note += "; %s (%d token col %%)" % (_plurale(len(percentuali), "percentuale", "percentuali"), len(token_pct))
    etichette = _etichette(origini, forme_numero)
    etichette.update(_etichette(percentuali, forme_percentuale))
    return _esito(nome_controllo, grezzi, eccezioni, note=note, etichette=etichette)


def lista_privata(path_py):
    """Il dict VIETATI di prova_numeri_del_book.py, appiattito in {stringa: file della
    prova che la elenca per primo}: si cerca in TUTTO il tree, non solo in quei file. Letto
    con ast.literal_eval, senza ESEGUIRE il modulo. VIETATI assente = ValueError, non
    «0 stringhe»."""
    if not os.path.isfile(path_py):
        raise FileNotFoundError("lista privata non trovata: %s" % path_py)
    import ast
    with open(path_py, encoding="utf-8") as fh:
        albero = ast.parse(fh.read())
    for nodo in albero.body:
        if isinstance(nodo, ast.Assign) and any(getattr(t, "id", None) == "VIETATI" for t in nodo.targets):
            out = {}
            for file_prova, stringhe in ast.literal_eval(nodo.value).items():
                # review T4 F7: una stringa nuda diventava caratteri singoli, un dict annidato le
                # sue chiavi (la cifra passava ZITTA), una stringa vuota colpiva ogni riga
                if not isinstance(stringhe, (list, tuple, set, frozenset)) or \
                        not all(isinstance(s, str) and s.strip() for s in stringhe):
                    raise ValueError("%s: VIETATI[%r] deve essere una lista di stringhe non vuote" % (path_py, file_prova))
                for s in stringhe:
                    out.setdefault(s, file_prova)
            if not out:   # review T4 F1
                raise ValueError("%s: VIETATI vuoto: non e' un verde" % path_py)
            return out
    raise ValueError("%s: nessun dict VIETATI a livello di modulo" % path_py)


def controllo_lista_privata(tree, stringhe, eccezioni=()):
    """Controllo B: ogni stringa della lista privata cercata letteralmente in ogni file;
    token = file della prova che la elenca + lunghezza, mai la stringa."""
    origini = stringhe if isinstance(stringhe, dict) else {s: None for s in stringhe}
    if not origini:   # review T4 F1
        return Esito("lista_privata", errore="nessuna stringa nella lista privata: non e' un verde")
    testi = _testi(tree)
    grezzi = []
    for s in sorted(origini):
        grezzi += _cerca(testi, s)
    return _esito("lista_privata", grezzi, eccezioni, note="%d stringhe" % len(origini),
                  etichette=_etichette(origini, lambda s: {s}))


# --- T5: controllo 5, i lotti del PM (ticker + quantita'/prezzo sulla stessa riga) -------------

def lotti_db(db_path):
    """{(ticker, quantita, prezzo)} da positions (prezzo_medio) e trade_history (prezzo), in
    sola lettura; righe con ticker vuoto o numeri NULL saltate (una quantita' 0 — posizione
    chiusa col suo prezzo medio — o un prezzo 0 restano: review T5 F6). DB assente =
    FileNotFoundError; zero lotti li dichiara controllo_lotti (DB vuoto = path sbagliato)."""
    c = _apri_ro(db_path)
    try:
        out = set()
        for tab, col in (("positions", "prezzo_medio"), ("trade_history", "prezzo")):
            for (t, q, p) in c.execute("SELECT ticker, quantita, %s FROM %s" % (col, tab)):
                if t and q is not None and p is not None:
                    out.add((t, float(q), float(p)))
        return out
    finally:
        c.close()


def lotti_da_tuple(path_py):
    """La lista TRADES dello script one-off in attic, letta con ast senza ESEGUIRE il modulo
    (come lista_privata): i lotti come li scriveva l'export del broker, col ticker NON
    normalizzato (un simbolo diverso da quello del DB, che il DB da solo non coprirebbe).
    Ogni elemento deve essere
    (data, ticker, azione, quantita, prezzo) = (str, str, str, numero, numero): uno che non
    lo e' = ValueError coi numeri di riga (review T5 F4: la regex di prima lo perdeva ZITTA e
    il KO scattava solo a zero); file assente = FileNotFoundError; TRADES assente, non lista
    o vuota = ValueError."""
    if not os.path.isfile(path_py):
        raise FileNotFoundError("tuple non trovate: %s" % path_py)
    import ast
    with open(path_py, encoding="utf-8") as fh:
        modulo = ast.parse(fh.read(), path_py)
    liste = [n.value for n in modulo.body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "TRADES" for t in n.targets)]
    if not liste or not isinstance(liste[-1], (ast.List, ast.Tuple)):
        raise ValueError("%s: nessuna lista TRADES a livello di modulo: non e' un verde" % path_py)
    lotti, rotte = set(), []
    for e in liste[-1].elts:
        try:
            v = ast.literal_eval(e)
        except (ValueError, TypeError):
            v = None
        if (isinstance(v, tuple) and len(v) == 5 and all(isinstance(s, str) for s in v[:3]) and v[1]
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v[3:])):
            lotti.add((v[1], float(v[3]), float(v[4])))
        else:
            rotte.append(e.lineno)
    if rotte:
        raise ValueError("%s: %d elementi di TRADES non nella forma (data, ticker, azione, quantita, prezzo): righe %s"
                         % (path_py, len(rotte), rotte))
    if not lotti:
        raise ValueError("%s: TRADES vuota: non e' un verde" % path_py)
    return lotti


def _forme_lotto(x):
    """Le forme con cui una quantita' o un prezzo del lotto puo' essere ricopiato. INTERO: le
    cifre nude e, da 1.000, con le migliaia all'italiana ('1.234') e all'inglese ('1,234': il
    ':,' di Python usa la notazione inglese, review T5 F2). DECIMALE:
    repr col punto e con la virgola; a 1, 2, 3 e 4 decimali (review T5 F3: i prezzi medi a
    5+ decimali si ricopiano arrotondati) col punto, con la virgola, con migliaia+virgola e
    con migliaia+punto ('3.117,46' e '3,117.46'); la sola parte intera, nuda e con le migliaia
    (un prezzo preciso puo' essere copiato troncando i decimali). Il segno non e'
    il segreto. Nessun filtro di lunghezza: una forma vale da sola con 4+ cifre del VALORE
    (_significative) e insieme all'altro numero sempre. La regola piu' larga («ticker + una
    forma qualsiasi di q o p») produce piu' collisioni. Residui dichiarati in controllo_lotti."""
    x = abs(float(x))
    if x == int(x):
        out = {"%d" % int(x)}
        if x >= 1000:
            out.add(_migliaia(x))
            out.add("{:,}".format(int(x)))
        return out
    r = repr(x)
    out = {r, r.replace(".", ","), "%d" % int(x)}
    for f in ("%.1f", "%.2f", "%.3f", "%.4f"):
        s = f % x
        intero, dec = s.split(".")
        out.update({s, intero + "," + dec, _migliaia(int(intero)) + "," + dec,
                    "{:,}".format(int(intero)) + "." + dec})
    if int(x) >= 1000:
        out.add(_migliaia(int(x)))
        out.add("{:,}".format(int(x)))
    return out


def _cifre(token):
    return len(re.sub(r"[^0-9]", "", token))


def _significative(x):
    """Cifre del VALORE al netto degli zeri di padding (1234.0 -> 4, 9.75 -> 3, 3117.456789 -> 10):
    un token paddato ('9.750', '31.50') non ne aggiunge (review T5 F3). Gli zeri di una
    quantita' tonda contano (1000 -> 4: review T5 F5, la classe di T4 F5 la decide il PM in D5)."""
    x = abs(float(x))
    return _cifre("%d" % int(x)) if x == int(x) else _cifre(repr(x).rstrip("0"))


_TONDO = re.compile(r"[1-9]0{3,}")


def controllo_lotti(tree, lotti, eccezioni=()):
    """Controllo 5: una riga e' un lotto se contiene il ticker base (prima del punto, confini
    alfanumerici, maiuscole come nel DB) E quantita' E prezzo in una delle loro forme, OPPURE
    uno dei due con 4+ cifre (le cifre del valore, non del token: min(_cifre, _significative)).
    Ogni forma si cerca anche sulla riga con le virgole sostituite da spazi (review T5 F1: in
    una cella CSV o in un array compatto — 'T,q,p', la riga stessa di importa_trade_csv — la
    virgola dopo la quantita' intera la faceva sparire ai confini di _regex_numero.
    Un numero italiano con la virgola i cui pezzi coincidono con q e p, 'T 11,9',
    si cura con una riga di ECCEZIONI). Per i ticker .L il DB tiene il prezzo in GBX: si cerca
    anche in GBP (p/100, review T5 F9). Una quantita' tonda accanto al ticker e' un hit come
    gli altri (review T5 F5: classe di T4 F5, la decide il PM riga per riga in D5); la nota
    conta quanti hit vengono da un solo numero tondo. Token = il ticker intero + lunghezza (i
    ticker sono pubblici, decisione PM 02/09; quantita' e prezzo non si stampano MAI); un hit
    per ticker e riga; le eccezioni sono per file e ticker. Zero lotti = KO dichiarato.
    Buchi dichiarati: il ticker in minuscolo (pre-filtro E regex sono case-sensitive: per
    chiuderlo vanno cambiati entrambi); ticker e numeri su righe diverse; il prezzo
    ARROTONDATO all'intero (si cerca solo la parte intera troncata: l'arrotondato a 1-3 cifre
    colpiva anche date ISO e commenti); le migliaia con lo
    spazio; 5+ decimali diversi da repr; il mezzo binario di %.2f (23.455 -> '23.45')."""
    if not lotti:
        return Esito("lotti", errore="nessun lotto da cercare: non e' un verde (DB vuoto = path sbagliato?)")
    testi = _testi(tree)
    grezzi, pieni, tondi = set(), set(), set()
    for (ticker, q, p) in sorted(lotti):
        base = ticker.split(".")[0]
        rt = re.compile(r"(?<![A-Za-z0-9])" + re.escape(base) + r"(?![A-Za-z0-9])")
        forme_p = _forme_lotto(p) | (_forme_lotto(p / 100) if ticker.endswith(".L") else set())
        fq = [(t, _regex_numero(t), min(_cifre(t), _significative(q))) for t in _forme_lotto(q)]
        fp = [(t, _regex_numero(t), min(_cifre(t), _significative(p))) for t in forme_p]
        for rel in sorted(testi):
            if base not in testi[rel]:          # pre-filtro: 136 lotti (48 ticker, 36 basi) x 303 file x righe, misura del 02/09
                continue
            for n, riga in enumerate(_righe(testi[rel]), 1):   # stessa numerazione di _cerca (review T2 F9)
                if not rt.search(riga):
                    continue
                alt = riga.replace(",", " ")     # celle CSV / array compatti (review T5 F1)
                hq = [(t, c) for t, rx, c in fq if rx.search(riga) or rx.search(alt)]
                hp = [(t, c) for t, rx, c in fp if rx.search(riga) or rx.search(alt)]
                sole = [t for t, c in hq + hp if c >= 4]
                if (hq and hp) or sole:
                    grezzi.add((rel, n, ticker))
                    if hq and hp:
                        pieni.add((rel, n, ticker))
                    elif all(_TONDO.fullmatch(re.sub(r"\D", "", t)) for t in sole):
                        tondi.add((rel, n, ticker))
    tondi -= pieni
    note = "%d lotti" % len(lotti)
    if tondi:
        note += "; %s" % _plurale(len(tondi), "hit da un solo numero tondo", "hit da un solo numero tondo")
    return _esito("lotti", sorted(grezzi), eccezioni, note=note, etichette={t: t for (t, _, _) in lotti})


# --- F1: controllo 9, i ticker del DB cercati DA SOLI ------------------------------------------

TABELLE_TICKER = ("positions", "trade_history", "favorite_companies")


def ticker_db(db_path):
    """Le BASI dei ticker (prima del punto, senza spazi, maiuscole) da positions,
    trade_history e favorite_companies, in sola lettura. I PREFERITI ci sono perche' un
    titolo messo tra i preferiti e mai comprato identifica chi lo scrive esattamente come
    una posizione, e nessun controllo li guardava. Ticker vuoto saltato."""
    c = _apri_ro(db_path)
    try:
        out = set()
        for tab in TABELLE_TICKER:
            for (t,) in c.execute("SELECT DISTINCT ticker FROM %s" % tab):
                b = (t or "").split(".")[0].strip().upper()
                if b:
                    out.add(b)
        return out
    finally:
        c.close()


def controllo_ticker_soli(tree, ticker, eccezioni=(), dichiarati=0):
    """Controllo 9 (F1, 04/09): la base di un ticker del DB su una riga, DA SOLA — senza
    pretendere quantita' e prezzo accanto (v. controllo_lotti).
    Confini ALFANUMERICI (il trattino basso separa e basta: `px_BASE` e' un riscontro).
    ⚠️ NON APPLICARE QUI `_dentro_identificatore` (che il controllo 10 usa apposta): stessa
    forma, significato OPPOSTO. Nel SORGENTE `px_BASE` e' un identificatore che NOMINA il
    titolo, cioe' precisamente la fuga; nel PAYLOAD `get_<venue>_intel` e' il nome di uno
    strumento e il modello non ci legge una posizione. Uniformare i due confini "per
    coerenza" nasconderebbe quelle menzioni. Cerca anche nei NOMI
    DEI FILE, senza distinzione di maiuscole e con riga 0 come controllo_esclusi: un file
    chiamato `test_BASE_x.py` si legge nell'elenco file di GitHub senza aprirlo, e nessun
    controllo guardava i percorsi.
    Zero ticker = KO dichiarato (DB vuoto o schema cambiato = path sbagliato).
    BUCO DICHIARATO: nel CONTENUTO si cerca solo maiuscolo, com'e' scritto nel DB. Il
    minuscolo sommergerebbe il conto di collisioni sulle basi da 2-3 lettere; nei PERCORSI
    invece si cerca senza maiuscole, perche' sono pochi e verificabili a occhio."""
    if not ticker:
        return Esito("ticker_soli", errore="nessun ticker da cercare: non e' un verde (DB vuoto = path sbagliato?)")
    testi = _testi(tree)
    grezzi = []
    for b in sorted(ticker):
        confine = r"(?<![A-Za-z0-9])" + re.escape(b) + r"(?![A-Za-z0-9])"
        # 05/09 (buco D5, 4b): dalle 4 lettere anche in MINUSCOLO, stesso confine del controllo 10
        # (71 righe in 13 file la scrivevano cosi', misura di a1 del 04/09); sotto le 4 no: il
        # rumore sommergerebbe il conto (buco dichiarato, invariato).
        senza_mai = len(b) >= 4
        grezzi += _cerca(testi, b, senza_maiuscole=senza_mai,
                         regex=re.compile(confine, re.IGNORECASE if senza_mai else 0))
        nel_nome = re.compile(confine, re.IGNORECASE)
        grezzi += [(rel, 0, b) for rel in sorted(tree) if nel_nome.search(rel)]
    note = "%d ticker cercati, %d presenti in %d file (prima delle eccezioni)" % (
        len(ticker), len({t for (_, _, t) in grezzi}), len({r for (r, _, _) in grezzi}))
    if dichiarati:
        note += "; " + _plurale(dichiarati, "dichiarato in SIMBOLI.txt", "dichiarati in SIMBOLI.txt")
    return _esito("ticker_soli", grezzi, eccezioni, note=note)


# --- T10 (04/09): controllo 10, il PAYLOAD — il testo SPEDITO AI MODELLI -----------------
# I nove controlli precedenti guardano il SORGENTE: cosa viene PUBBLICATO. Questo guarda
# un'altra cosa, e sono due misure diverse: cosa DECIDE. Un simbolo del book dentro un
# system prompt o dentro la description di uno schema di tool non e' un problema di
# privacy soltanto: puo' orientare il modello in base alle etichette del portafoglio.
# Il controllo cerca questi riferimenti statici; non misura l'effetto sulle decisioni.
#
# COME SI LEGGE UNO ZERO QUI. Il corpus non sono i file: sono le COSTANTI che finiscono nel
# testo spedito all'API. Nel percorso legacy si rendono importando i moduli; gli effetti
# degli import vanno verificati in isolamento da backend e scheduler concorrenti.
# Si importa dal REPO e non dal tree esportato perche' l'export e' una COPIA
# (`shutil.copy2`, tools/release/export_pubblico.py): le costanti sono le stesse byte per byte.
#
# CIO' CHE QUESTO CONTROLLO NON MISURA, dichiarato (regola 14/07, e «il cancello da' PULITO
# solo per cio' che misura»): il testo composto a RUNTIME — i blocchi che arrivano dal DB
# (portafoglio, tesi, preferiti, score), i `tool_result` che i dispatcher rimandano al
# modello, le f-string costruite dentro le funzioni. Quelli portano il book PER PROGETTO,
# perche' vengono dai dati di chi esegue: qui si cerca solo cio' che e' CABLATO NEL CODICE.

CANALI_PAYLOAD = (
    # (canale, modulo, attributo) — il testo statico che parte verso l'API
    ("capo/system", "bellomberg.agents.capo", "CAPO_SYSTEM_PROMPT"),
    ("capo/nudge_collasso", "bellomberg.agents.capo", "NUDGE_COLLASSO_MEMO"),
    ("desk/regole_di_stile", "bellomberg.agents.specialists.base", "SPECIALIST_STYLE_RULES"),
    ("fatti/statici", "bellomberg.core.current_facts", "FACTS_STATIC"),
    ("fatti/note", "bellomberg.core.current_facts", "NOTES"),
    ("fatti/contesto", "bellomberg.core.current_facts", "CONTEXT_SNAPSHOT"),
    ("chat/system_base", "bellomberg.agents.chat_engine", "SYSTEM_PROMPTS_BASE"),
    ("chat/anti_allucinazione", "bellomberg.agents.chat_engine", "_ANTI_HALLUCINATION"),
    ("tool/registro_vivo", "bellomberg.agents.chat_tools", "TOOL_DEFINITIONS"),
    ("tool/registro_legacy", "bellomberg.agents.agent_tools", "TOOLS_SCHEMA"),
    ("tool/emit_action_table", "bellomberg.agents.action_table_extract", "EMIT_TOOL"),
    ("news/temi_macro", "bellomberg.market_data.news_topics", "TOPICS"),
    # 04/09 (Opus 5): tre `system=` VIVI che il corpus non copriva. Oggi valgono ZERO, ma
    # «zero non misurato» e «zero misurato» sono due cose diverse: un edit li' era invisibile.
    ("estrazione/system", "bellomberg.agents.action_table_extract", "_SYSTEM"),
    ("red_team/system", "bellomberg.agents.red_team", "RED_TEAM_PROMPT"),
    ("reflection/system", "bellomberg.agents.reflection", "REFLECTION_PROMPT"),
    # 05/09 (a1 l'ha trovato, a2 lo misura): il prompt con cui Haiku classifica OGNI notizia era
    # una f-string dentro una funzione — un canale del payload che il corpus non rendeva. a1 l'ha
    # portato in una costante (commit aa035da); la lista viva delle posizioni resta iniettata a
    # runtime, e il decimo controllo dichiara di non misurarla (testo composto a runtime).
    ("news/sentiment", "bellomberg.market_data.news_aggregator", "NEWS_SENTIMENT_PROMPT"),
)
MIN_NOME = 5        # sotto, un nome di societa' e' rumore (e le sigle le cerca gia' l'ago 1)
MIN_PAROLA = 6      # una PAROLA distintiva del nome: 6 tiene «Saipem», scarta «Banca»/«Monte»
PAROLE_GENERICHE = frozenset("""
holding holdings group spa societa azioni ordinarie inc corp corporation incorporated
plc ltd limited company companies trust fund funds etf ucits index capital partners
management investment investments international global europe america asiapac
banca banco bank banking finance financial insurance assicurazioni technologies
technology systems industries industrie holdingsa sicav nv ag oyj asa
healthcare
""".split())   # I termini generici di settore non identificano da soli una societa'.
# Allargare questa lista senza misurare le menzioni perse riduce la copertura del controllo.


def _rendi(x):
    """Il testo che l'API riceve davvero: una stringa e' se stessa, un registro di schemi
    o di temi e' il suo JSON (e' cosi' che finisce nel campo `tools=` o in un tool_result)."""
    if isinstance(x, str):
        return x
    return json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)


def _importa_modulo(nome):
    """Import per NOME, iniettabile. Gli effetti sul filesystem vanno misurati isolando
    gli import: backend e task schedulati concorrenti possono scrivere negli stessi
    percorsi e confondere la misura."""
    import importlib
    return importlib.import_module(nome)


def payload_statico(canali=CANALI_PAYLOAD, importa=None, specialisti=None):
    """({canale: testo}, [(canale, guasto)]) — il corpus del controllo 10.
    Ogni canale che NON si rende e' un GUASTO DICHIARATO, mai un canale saltato: uno zero
    su un corpus incompleto e' una bugia, e chi lo legge non ha modo di accorgersene.
    `importa`/`specialisti` iniettabili: i test non importano il mondo."""
    imp = importa or _importa_modulo
    testi, guasti = {}, []
    for canale, modulo, attributo in canali:
        try:
            testi[canale] = _rendi(getattr(imp(modulo), attributo))
        except Exception as e:
            guasti.append((canale, "%s: %s" % (type(e).__name__, e)))
    try:
        elenco = specialisti if specialisti is not None else imp("bellomberg.agents.specialists").ALL_SPECIALISTS
        for cls in elenco:
            # attributo di CLASSE: non si istanzia niente (il costruttore ha effetti)
            testi["desk/" + getattr(cls, "name", cls.__name__)] = _rendi(cls.system_prompt)
    except Exception as e:
        guasti.append(("desk/*", "%s: %s" % (type(e).__name__, e)))
    return testi, guasti


_PAYLOAD_SENTINELLA = "__BELLOMBERG_PAYLOAD__"
_PAYLOAD_RUNNER = r"""
import importlib, json, os, sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass
root = os.path.realpath(sys.argv[1])
canali = json.loads(sys.argv[2])
sys.path[:0] = [os.path.join(root, "src"), root]

def dentro(modulo):
    percorso = getattr(modulo, "__file__", None)
    if not percorso:
        raise ImportError("modulo senza __file__")
    reale = os.path.realpath(percorso)
    if os.path.commonpath([root, reale]) != root:
        raise ImportError("modulo risolto fuori dal tree materializzato: " + reale)
    return modulo

def rendi(x):
    return x if isinstance(x, str) else json.dumps(
        x, ensure_ascii=False, sort_keys=True, default=str)

testi, guasti = {}, []
for canale, nome_modulo, attributo in canali:
    try:
        modulo = dentro(importlib.import_module(nome_modulo))
        testi[canale] = rendi(getattr(modulo, attributo))
    except Exception as e:
        guasti.append([canale, type(e).__name__ + ": " + str(e)])
try:
    pacchetto = dentro(importlib.import_module("bellomberg.agents.specialists"))
    for cls in pacchetto.ALL_SPECIALISTS:
        modulo_cls = dentro(importlib.import_module(cls.__module__))
        if getattr(modulo_cls, cls.__name__, None) is not cls:
            raise ImportError("classe specialista non proviene dal modulo materializzato")
        testi["desk/" + getattr(cls, "name", cls.__name__)] = rendi(cls.system_prompt)
except Exception as e:
    guasti.append(["desk/*", type(e).__name__ + ": " + str(e)])
print(%r + json.dumps({"testi": testi, "guasti": guasti}, ensure_ascii=False))
""" % _PAYLOAD_SENTINELLA


def payload_statico_da_tree(source_root):
    """Rende i soli canali whitelistati dal tree materializzato, in un interprete pulito.

    L'elenco dei moduli non arriva da CLI o dal chiamante: e' CANALI_PAYLOAD. Il runner
    verifica anche che ogni modulo applicativo e ogni classe desk risolva sotto source_root.
    Un canale guasto resta nel risultato come guasto; gli altri non vengono buttati.
    """
    root = os.path.realpath(source_root)
    if not os.path.isdir(root):
        raise FileNotFoundError("source root payload non trovato: " + root)
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP",
        "LOCALAPPDATA", "APPDATA", "USERPROFILE",
    }}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _PAYLOAD_RUNNER, root,
             json.dumps(CANALI_PAYLOAD, ensure_ascii=False)],
            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {}, [("payload/processo", "processo pulito: timeout dopo 60 s")]
    righe = [r for r in (proc.stdout or "").splitlines()
             if r.startswith(_PAYLOAD_SENTINELLA)]
    if proc.returncode != 0 or len(righe) != 1:
        causa = (proc.stderr or proc.stdout or "nessun output")[-500:]
        return {}, [("payload/processo", "processo pulito exit %d: %s"
                     % (proc.returncode, causa.strip()))]
    try:
        payload = json.loads(righe[0][len(_PAYLOAD_SENTINELLA):])
        return payload["testi"], [tuple(g) for g in payload["guasti"]]
    except Exception as e:
        return {}, [("payload/processo", "output non leggibile: %s: %s"
                     % (type(e).__name__, e))]


def nomi_db(db_path):
    """I NOMI PER ESTESO delle societa' del book, dal DB, in sola lettura. Senza questo ago
    il controllo puo' segnare ZERO col book scritto in chiaro: nel perimetro ci sono ragioni
    sociali, veicoli e gestori senza un ticker accanto, e una bonifica dei soli simboli non
    li tocca. La lista NON puo' vivere nel perimetro pubblico (una guardia che elenca il
    segreto lo pubblica): sta nel DB, che nel perimetro non c'e'.
    BUCO DICHIARATO: le posizioni senza `nome` nel DB sono mute per questo ago, non zero."""
    c = _apri_ro(db_path)
    try:
        out = set()
        for tab, col in (("positions", "nome"), ("favorite_companies", "name")):
            for (v,) in c.execute("SELECT DISTINCT %s FROM %s" % (col, tab)):
                v = (v or "").strip()
                if len(v) >= MIN_NOME:
                    out.add(v)
        return out
    finally:
        c.close()


def _parole_distintive(nomi):
    """{parola: nome} delle parole che identificano da sole una societa'. Le generiche no:
    «Banca X» non deve far scattare ogni riga che dice «banca»."""
    fuori = {}
    for n in nomi:
        for p in re.split(r"[^A-Za-z0-9]+", n):
            if len(p) >= MIN_PAROLA and p.lower() not in PAROLE_GENERICHE:
                fuori.setdefault(p, n)
    return fuori


def _dentro_identificatore(riga, token):
    """True se OGNI occorrenza del token in questa riga e' incollata a un `_`: allora e' un
    NOME DI FUNZIONE (`get_venue_intel`, esempio sintetico), non una menzione del book.
    Il confine del controllo e' `(?<![A-Za-z0-9])`, e l'underscore non e' alfanumerico: senza
    questa separazione un nome di tool conta come fuga. La regola e' conservativa:
    una riga che porta ANCHE una menzione nuda non viene separata.
    ⚠️ Vale SOLO per il controllo 10. Il controllo 9 guarda il SORGENTE e li' `px_BASE` e'
    un identificatore che NOMINA il titolo: applicargli questa regola renderebbe invisibili
    le menzioni dei titoli in quegli identificatori. Due confini diversi APPOSTA.
    Non e' un'esclusione zitta: i separati finiscono nella NOTA dell'esito, col conto e coi
    token mascherati. Prezzo DICHIARATO: una menzione vera scritta dentro uno snake_case
    (`VAL_ACME_X.xlsx`, esempio sintetico) finisce fra i separati — resta visibile nella nota, non nel totale."""
    basso, ago = riga.lower(), token.lower()
    i = basso.find(ago)
    if i < 0:
        return False
    while i >= 0:
        prima = basso[i - 1] if i > 0 else ""
        dopo = basso[i + len(ago)] if i + len(ago) < len(basso) else ""
        if prima != "_" and dopo != "_":
            return False
        i = basso.find(ago, i + 1)
    return True


def controllo_payload(testi, ticker, nomi=(), guasti=(), eccezioni=(), dichiarati=0):
    """Controllo 10: i simboli e i NOMI del book nel testo che arriva ai modelli.
    Tre aghi, e il secondo e' quello che decide se il controllo vale qualcosa:
      1. le BASI dei ticker, confini alfanumerici come il controllo 9. In piu' del 9, qui si
         cerca anche in MINUSCOLO per le basi da 4+ caratteri: il payload e' prosa, e uno
         slug minuscolo dentro la description di un tool e' un riscontro. Sotto i 4 il
         minuscolo sommergerebbe il conto: BUCO DICHIARATO.
      2. i NOMI PER ESTESO (dal DB) e le loro PAROLE DISTINTIVE: un nome senza ticker
         sopravvive a qualunque bonifica dei simboli.
      3. — la FORMA (cardinalita', elenchi settoriali che ricalcano la composizione) non e'
         ancora misurata: dichiarata qui come scoperta, non taciuta.
    Un canale non reso e' un KO di tutto il controllo: zero su un corpus incompleto e' falso."""
    if guasti:
        return Esito("payload", errore="%d canali non resi (%s): uno zero su un corpus "
                     "incompleto sarebbe falso" % (len(guasti), "; ".join(
                         "%s -> %s" % (c, e) for c, e in guasti)))
    if not testi:
        return Esito("payload", errore="nessun canale reso: corpus vuoto, non e' un verde")
    if not ticker:
        return Esito("payload", errore="nessun ticker da cercare: non e' un verde (DB vuoto?)")
    grezzi = []
    for b in sorted(ticker):
        confine = r"(?<![A-Za-z0-9])" + re.escape(b) + r"(?![A-Za-z0-9])"
        senza_mai = len(b) >= 4
        grezzi += _cerca(testi, b, senza_maiuscole=senza_mai,
                         regex=re.compile(confine, re.IGNORECASE if senza_mai else 0))
    parole = _parole_distintive(nomi)
    for n in sorted(nomi):
        grezzi += _cerca(testi, n, senza_maiuscole=True)
    for p in sorted(parole):
        confine = r"(?<![A-Za-z0-9])" + re.escape(p) + r"(?![A-Za-z0-9])"
        grezzi += _cerca(testi, p, senza_maiuscole=True, regex=re.compile(confine, re.IGNORECASE))
    prosa, identificatori = [], []
    for (canale, n, tok) in grezzi:
        righe = _righe(testi[canale])
        riga = righe[n - 1] if 0 < n <= len(righe) else ""
        (identificatori if _dentro_identificatore(riga, tok) else prosa).append((canale, n, tok))
    note = ("%d canali resi, %d ticker e %d nomi (%d parole distintive) cercati; "
            "%d canali colpiti (prima delle eccezioni). NON misura il testo composto a runtime ne' la FORMA" % (
                len(testi), len(ticker), len(nomi), len(parole),
                len({r for (r, _, _) in prosa})))
    if identificatori:
        note += ("; %d riscontri DENTRO identificatori (nomi di tool/funzione), separati e "
                 "DICHIARATI: %s" % (len(identificatori),
                                     ", ".join(sorted({maschera(t) for (_, _, t) in identificatori}))))
    if dichiarati:
        note += "; " + _plurale(dichiarati, "dichiarato in SIMBOLI.txt", "dichiarati in SIMBOLI.txt")
    return _esito("payload", prosa, eccezioni, note=note)


# --- G3B (13/09): controllo 13, il TESTO LIBERO DELL'UTENTE copiato nel tree -------------------
# I dodici controlli precedenti cercano numeri, ticker, nomi, stringhe di lista e percorsi: nessuno
# leggeva il testo libero del DB. Questo controllo cerca copie di quelle frasi, parola per
# parola, in TUTTO il
# tree (anche cio' che e' gia' pubblico e che una revisione del solo diff non rilegge).
#
# LA CLASSE e' «testo libero dell'utente», non «testo del PM»: `cash_movements.note` la scrive chi
# registra o importa la riga (a volte parla del PM in terza persona), ma resta testo privato del DB.
# Il nome del controllo resta `testo_pm` (quello della proposta 13/09).
#
# DA DOVE VENGONO LE COLONNE: dal registro delle «parole del PM» che il codice rende ai modelli
# (`pm_verbatim` in storage/memory_db.py e i suoi chiamanti) PIU' il censimento dello schema: ogni
# colonna di testo di un DB nuovo sta in COLONNE_TESTO_UTENTE o in COLONNE_TESTO_NON_UTENTE col suo
# motivo, e un test crea il DB in una cartella temporanea e pretende che nessuna resti fuori (una
# colonna nuova di testo libero non passa zitta).
#
# LA REGOLA, misurata il 13/09 sullo snapshot e sugli oggetti git del clone (due implementazioni
# indipendenti, stessi numeri): parole NFKD senza segni diacritici, minuscole, SOLO lettere (le
# cifre separano e non contano); corse contigue di almeno TESTO_MIN_BLOCCO=3 parole in comune fra un
# testo e un file; corse dello stesso testo concatenate se il buco e' <= TESTO_MAX_BUCO=3 parole sia
# nel file sia nel testo; riscontro = catena con almeno TESTO_MIN_PAROLE=5 parole in comune.
# I parametri sono tarati sugli stessi casi osservati usati per verificarli:
# misura circolare, dichiarata. Rete stretta, non garanzia.
#
# CIO' CHE NON MISURA, misurato e dichiarato nella nota: parafrasi, traduzioni, riassunti; copie con
# meno di 3 parole intatte fra una modifica e l'altra; estratti e testi sotto 5 parole;
# il testo dei modelli (ripete i prompt
# del codice: inservibile come corpus); le parole che vivono solo nei documenti privati.

COLONNE_TESTO_UTENTE = (
    # (tabella, colonna, filtro (colonna, valore) o None, motivo)
    ("decisions", "pm_feedback", None, "commento del PM su una decisione, reso ai modelli da pm_verbatim"),
    ("decisions", "veto_reason", None, "motivo obbligatorio del veto del PM, reso da pm_verbatim"),
    ("pm_feedback", "feedback_text", None, "feedback del PM agli specialisti, reso da pm_verbatim"),
    ("favorite_companies", "note", None, "nota del PM su un preferito, resa da pm_verbatim"),
    ("decision_notes", "testo", ("autore", "PM"), "nota del PM su una decisione (quelle con autore AI sono dei modelli)"),
    ("trade_history", "pm_rationale", None, "motivazione del trade scritta dal PM"),
    ("trade_history", "note", None, "nota libera del trade (PM o importatore)"),
    ("cash_movements", "note", None, "causale libera del movimento: la scrive chi registra o importa la riga, non sempre il PM"),
    ("positions", "tesi", None, "tesi della posizione"),
    ("positions", "temi_monitoraggio", None, "temi da monitorare della posizione"),
    ("positions", "note", None, "nota libera della posizione"),
    ("position_openings", "provenienza", None, "provenienza dichiarata di un saldo iniziale"),
    ("position_openings", "nota", None, "nota libera di un saldo iniziale"),
    ("journal_entries", "title", None, "Diario utente (origin = 'user')"),
    ("journal_entries", "body", None, "Diario utente (origin = 'user')"),
    ("journal_revisions", "title", None, "revisioni immutabili del Diario utente"),
    ("journal_revisions", "body", None, "revisioni immutabili del Diario utente"),
    ("chat_messages", "content", ("role", "user"), "messaggi dell'utente in chat; ci finiscono anche i prompt rapidi dell'app (v. SOGLIA_TEMPLATE); assistant e system sono dei modelli"),
    ("chat_sessions", "title", None, "titolo della chat: rinominabile a mano dall'utente (altrimenti un default o un titolo generato)"),
    ("themes_tracked", "theme", None, "tema tracciato: nessuno scrittore nel codice oggi, dentro per prudenza"),
    ("themes_tracked", "notes", None, "note dei temi: nessuno scrittore nel codice oggi, dentro per prudenza"),
    ("method_record_reviews", "note", None, "nota del PM sulla revisione di un set di record documentati (D1A, 13/09)"),
)

COLONNE_TESTO_NON_UTENTE = (
    # (motivo, colonne "tabella.colonna"): ogni altra colonna di testo di un DB nuovo
    ("data o ora scritta dal sistema", (
        "agent_score_history.started_at", "agent_score_history.completed_at", "agent_score_history.captured_at",
        "cash_movements.date", "cash_movements.created_at", "cash_state.updated_at",
        "chat_messages.timestamp", "chat_sessions.started_at", "chat_sessions.last_activity",
        "company_guidance.source_date", "company_guidance.effective_date", "company_guidance.valid_until",
        "company_guidance.created_at", "decision_notes.timestamp", "decisions.timestamp", "decisions.closed_at",
        "decisions.veto_at", "decisions.veto_revoked_at", "favorite_companies.added_at",
        "iv_history.snap_date", "iv_history.expiry", "iv_history.created_at",
        "journal_entries.created_at", "journal_entries.updated_at", "journal_entries.archived_at",
        "journal_revisions.created_at", "journal_revisions.saved_at", "journal_revisions.archived_at",
        "llm_usage.timestamp", "memos.timestamp", "nav_snapshots.date", "nav_snapshots.created_at",
        "news_feed.published_at", "news_feed.pulled_at", "pm_feedback.timestamp",
        "position_openings.as_of", "position_openings.created_at", "position_prices.timestamp",
        "positions.data_apertura", "positions.last_updated", "schema_version.applied_at",
        "specialist_reports.timestamp", "themes_tracked.first_mentioned", "themes_tracked.last_mentioned",
        "trade_history.data", "trade_history.created_at", "valuation_snapshot_links.created_at",
        "valuation_snapshots.created_at", "valuation_theses.date", "method_record_reviews.reviewed_at",
        "method_record_sets.prepared_at", "method_record_sets.earliest_valid_until",
        "method_record_sets.latest_as_of")),
    ("valore di un insieme chiuso (tipo, stato, ruolo, azione, lingua, valuta, sentiment)", (
        "cash_movements.type", "chat_messages.role", "chat_messages.output_language", "chat_sessions.specialist",
        "chat_sessions.output_language", "company_guidance.metric", "company_guidance.period",
        "company_guidance.status", "decision_notes.autore", "decisions.action", "decisions.confidence",
        "decisions.status", "journal_entries.kind", "journal_entries.origin", "journal_revisions.kind",
        "journal_revisions.origin", "journal_revisions.action", "llm_usage.agent", "llm_usage.cost_status",
        "llm_usage.cache_ttl", "llm_usage.output_language", "memos.output_language", "news_feed.sentiment",
        "pm_feedback.specialist", "pm_feedback.sentiment", "position_openings.valuta",
        "position_openings.precisione_data", "position_prices.valuta", "positions.valuta",
        "specialist_reports.specialist", "specialist_reports.output_language", "themes_tracked.conviction",
        "themes_tracked.status", "trade_history.action", "trade_history.valuta", "valuation_theses.sanity_severity",
        "method_record_reviews.decision")),
    ("identificativo tecnico o impronta", (
        "agent_score_history.run_id", "agent_score_history.payload_sha256", "valuation_snapshot_links.snapshot_id",
        "valuation_snapshot_links.generation_id", "valuation_snapshots.snapshot_id",
        "valuation_snapshots.generation_id", "valuation_snapshots.payload_sha256",
        "method_record_sets.method_id", "method_record_sets.method_version", "method_record_sets.records_sha256")),
    ("chi prepara o rivede un set di record: etichetta d'identita', non testo libero", (
        "method_record_sets.prepared_by", "method_record_reviews.reviewer")),
    ("set di record documentati preparati dai modelli dai documenti ufficiali (D1A): record, motivazioni "
     "degli scenari e provenienza", (
        "method_record_sets.records_json", "method_record_sets.scenario_rationale_json",
        "method_record_sets.provenance")),
    ("ticker, nome o anagrafica del titolo: li cercano lotti, ticker_soli e payload", (
        "company_guidance.ticker", "decisions.ticker", "favorite_companies.ticker", "favorite_companies.name",
        "favorite_companies.sector", "favorite_companies.industry", "iv_history.ticker", "journal_entries.ticker",
        "journal_revisions.ticker", "news_feed.ticker_mentioned", "position_openings.ticker",
        "position_prices.ticker", "positions.ticker", "positions.nome", "trade_history.ticker",
        "valuation_snapshots.ticker", "valuation_theses.ticker", "method_record_sets.ticker")),
    ("testo generato dai modelli o dal codice che li orchestra: ripete i prompt del codice, "
     "inservibile come corpus", (
        "decisions.timing", "decisions.rationale", "memos.title", "memos.full_markdown",
        "specialist_reports.content", "valuation_theses.variant_view", "valuation_theses.sanity_headline")),
    ("nota d'esito della decisione: la scrivono il codice (auto-archiviazione in memory_db, AUTO-ESCLUSA "
     "in action_validator) e tools/maintenance/archivia_run_duplicata.py, da template; PATCH /decisions/{id} "
     "la accetta ma nessuna pagina dell'app la invia (misurato 13/09): se una pagina comincia a "
     "scriverla, va fra le colonne dell'utente", (
        "decisions.outcome_notes",)),
    ("etichetta o nota scritta dal codice (fonte, provider, modello, motore, esito di un calcolo)", (
        "cash_state.source", "iv_history.spot_source", "iv_history.source", "llm_usage.model",
        "llm_usage.fx_source", "memos.notes", "nav_snapshots.source", "position_prices.source",
        "schema_version.description", "trade_history.link_origin", "trade_history.fx_fonte",
        "valuation_theses.growth_path", "valuation_theses.engine", "valuation_theses.subsector",
        "valuation_theses.profile_key")),
    ("registro guidance: fonte, unita', scadenza e nota le scrive l'agente col tool add_guidance "
     "(entered_by = il chiamante)", (
        "company_guidance.unit", "company_guidance.source_doc", "company_guidance.valid_until_source",
        "company_guidance.entered_by", "company_guidance.note")),
    ("notizie di terzi e loro sintesi generate", (
        "news_feed.title", "news_feed.snippet", "news_feed.source", "news_feed.url", "news_feed.theme",
        "news_feed.provider", "news_feed.headline_it", "news_feed.why_matters")),
    ("JSON o percorsi prodotti dal sistema", (
        "agent_score_history.payload_json", "memos.pdf_path", "memos.appendix_path", "memos.dcf_files",
        "valuation_snapshots.payload_json")),
)
TESTO_MIN_BLOCCO = 3
TESTO_MAX_BUCO = 3
TESTO_MIN_PAROLE = 5
# Esenzione per TEMPLATE, non per file (13/09): i prompt rapidi dell'app partono come messaggi
# dell'utente e tornano nel DB identici al catalogo, e la chat prende per titolo il primo messaggio.
# Un messaggio o un titolo la cui copertura da un prompt dei cataloghi (parole in blocchi da
# TESTO_MIN_BLOCCO in su / parole del testo) arriva a SOGLIA_TEMPLATE e' un prompt dell'app: i suoi
# riscontri si separano e si DICHIARANO nella nota (file:riga), non spariscono. Si separa solo cio'
# che viene dal prompt: una catena con TESTO_MIN_PAROLE parole in comune FUORI dalle parole coperte
# dal template (il resto del messaggio, fino al 30%) resta un riscontro (ripresa 13/09 sera: prima
# l'esenzione valeva per il messaggio intero e sul deposito non fermava). Il catalogo si legge
# dal tree misurato. Le coperture sono misurate per messaggio e titolo, anche sulla soglia.
# Un'eccezione per file si sarebbe invecchiata al trasloco dei
# prompt (misurato: da lib/chat-prompts.ts a i18n) e avrebbe silenziato anche un testo vero copiato in
# quei file. BUCO DICHIARATO: un testo dell'utente copiato DENTRO un catalogo diventa un template;
# vale solo per messaggi e titoli della chat, non per le altre colonne.
CATALOGHI_PROMPT = re.compile(r"^app/src/i18n/[^/]+/communications\.ts$")
ORIGINI_PROMPT_APP = ("chat_messages.content[user]", "chat_sessions.title")
SOGLIA_TEMPLATE = 0.7
_COMBINANTI = re.compile("[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]")
_PAROLA = re.compile(r"[a-z]+")
_VALORE_CATALOGO = re.compile(r'"(?:[^"\\]|\\.)*"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _origine_testo(colonna):
    tab, col, filtro, _ = colonna
    return "%s.%s%s" % (tab, col, ("[%s]" % filtro[1]) if filtro else "")


def _normalizza_testo(testo):
    """NFKD, via i segni diacritici, minuscolo. Le a-capo restano dove sono (NFKD non ne crea)."""
    return _COMBINANTI.sub("", unicodedata.normalize("NFKD", testo)).lower()


def parole_testo(testo):
    """Le parole come le confronta il controllo 13: solo lettere a-z dopo la normalizzazione."""
    return _PAROLA.findall(_normalizza_testo(testo or ""))


def testi_utente_db(db_path, colonne=COLONNE_TESTO_UTENTE):
    """[(origine, testo)] non vuoti delle colonne di testo libero dell'utente, in sola lettura. Una
    tabella o una colonna assente = OperationalError (un KO dichiarato), mai «0 testi»."""
    c = _apri_ro(db_path)
    try:
        out = []
        for colonna in colonne:
            tab, col, filtro, _ = colonna
            if filtro:
                righe = c.execute("SELECT %s FROM %s WHERE %s = ?" % (col, tab, filtro[0]), (filtro[1],))
            else:
                righe = c.execute("SELECT %s FROM %s" % (col, tab))
            for (v,) in righe:
                if isinstance(v, str) and v.strip():
                    out.append((_origine_testo(colonna), v))
        return out
    finally:
        c.close()


def _prompt_di_catalogo(testi, min_blocco=TESTO_MIN_BLOCCO):
    """[(file, riga, parole)] dei valori stringa dei cataloghi dei prompt nel tree misurato."""
    out = []
    for rel in sorted(testi):
        if not CATALOGHI_PROMPT.match(rel):
            continue
        for n, riga in enumerate(_righe(testi[rel]), 1):
            for m in _VALORE_CATALOGO.finditer(riga):
                try:
                    valore = json.loads('"%s"' % m.group(1))
                except ValueError:
                    valore = m.group(1)
                ws = parole_testo(valore)
                if len(ws) >= min_blocco:
                    out.append((rel, n, ws))
    return out


def _copertura_template(parole, prompt, min_blocco=TESTO_MIN_BLOCCO):
    """(copertura massima, posizioni coperte). La copertura di un testo da parte di un prompt del
    catalogo = parole in blocchi comuni da `min_blocco` in su, sul totale delle parole del testo: si
    prende la MASSIMA su un prompt solo (e' quella che decide se il testo e' un prompt dell'app). Le
    posizioni (indici nelle parole del testo) sono quelle dei blocchi di TUTTI i prompt: il prompt per
    ticker dell'app si compone di piu' voci del catalogo: limitarsi al prompt migliore
    attribuirebbe all'utente parti delle altre voci. Le parole del testo fuori da ogni
    blocco restano dell'utente."""
    migliore, coperte = 0.0, set()
    for _, _, ws in prompt:
        sm = difflib.SequenceMatcher(None, parole, ws, autojunk=False)
        blocchi = [b for b in sm.get_matching_blocks() if b.size >= min_blocco]
        migliore = max(migliore, sum(b.size for b in blocchi) / float(len(parole)))
        for b in blocchi:
            coperte.update(range(b.a, b.a + b.size))
    return migliore, coperte


def _corse_testo(parole_file, indice, primi, b):
    """[(id testo, inizio nel file, inizio nel testo, lunghezza)]: le corse massimali di parole in
    comune, lunghe almeno `b`, lungo ogni diagonale (testo, scarto)."""
    aperte, chiuse = {}, []
    for i in range(len(parole_file) - b + 1):
        if parole_file[i] not in primi:
            continue
        for tid, p in indice.get(tuple(parole_file[i:i + b]), ()):
            k = (tid, p - i)
            r = aperte.get(k)
            if r is not None and r[2] == i + b - 1:
                r[2] = i + b
            else:
                if r is not None:
                    chiuse.append((tid, r[0], r[1], r[2] - r[0]))
                aperte[k] = [i, p, i + b]
    chiuse.extend((k[0], r[0], r[1], r[2] - r[0]) for k, r in aperte.items())
    return chiuse


def _catene_testo(corse, max_buco):
    """[(id testo, inizio nel file, parole in comune, posizioni nel testo)]: corse dello stesso testo
    concatenate quando il buco e' <= max_buco parole sia nel file sia nel testo (una parola cambiata,
    tolta o aggiunta non spezza la copia). Ogni corsa allunga la catena compatibile con piu' parole,
    non solo l'ultima aperta: una corsa spuria in mezzo (un trigramma ripetuto altrove nel testo) non
    spezza la vera. Le posizioni (indici delle parole del testo in comune) servono all'esenzione dei
    prompt dell'app: si esenta cio' che viene dal template, non il messaggio intero."""
    per_testo = {}
    for tid, fi, ti, ln in corse:
        per_testo.setdefault(tid, []).append((fi, ti, ln))
    out = []
    for tid, rs in per_testo.items():
        rs.sort()
        catene = []                         # [inizio file, fine file, fine testo, parole, posizioni]
        for fi, ti, ln in rs:
            compatibili = [c for c in catene
                           if -ln < fi - c[1] <= max_buco and -ln < ti - c[2] <= max_buco]
            if compatibili:
                c = max(compatibili, key=lambda x: x[3])
                c[3] += max(0, fi + ln - max(c[1], fi))
                c[1] = max(c[1], fi + ln)
                c[2] = max(c[2], ti + ln)
                c[4].update(range(ti, ti + ln))
            else:
                catene.append([fi, fi + ln, ti + ln, ln, set(range(ti, ti + ln))])
        out.extend((tid, c[0], c[3], c[4]) for c in catene)
    return out


def controllo_testo_pm(tree, testi, eccezioni=(), min_blocco=TESTO_MIN_BLOCCO,
                       max_buco=TESTO_MAX_BUCO, min_parole=TESTO_MIN_PAROLE):
    """Controllo 13 (G3B, 13/09), in OSSERVAZIONE: il testo libero dell'utente (testi_utente_db)
    copiato nel tree. Regola a catena (v. sopra): hit = file:riga della prima parola in comune,
    token = ORIGINE...(N parole) = parole in comune. MAI il testo, nemmeno una parola. Un hit per file, riga e
    origine. Zero testi, o nessun testo da `min_parole` parole in su = KO dichiarato. Eccezioni per
    (file, testo_pm, origine), stantie dichiarate. I messaggi della chat che sono prompt dell'app
    e i loro titoli (SOGLIA_TEMPLATE) si separano e si dichiarano nella nota."""
    if not testi:
        return Esito("testo_pm", errore="nessun testo libero dell'utente dal DB: non e' un verde (DB vuoto = path sbagliato?)")
    visti, voci, corti = set(), [], 0
    for origine, testo in testi:
        ws = parole_testo(testo)
        if len(ws) < min_parole:
            corti += 1
            continue
        chiave = (origine, tuple(ws))
        if chiave not in visti:
            visti.add(chiave)
            voci.append((origine, ws))
    if not voci:
        return Esito("testo_pm", errore="%d testi, nessuno da %d parole in su: niente da cercare, non e' un verde"
                     % (len(testi), min_parole))
    indice, primi = {}, set()
    for tid, (_, ws) in enumerate(voci):
        for p in range(len(ws) - min_blocco + 1):
            indice.setdefault(tuple(ws[p:p + min_blocco]), []).append((tid, p))
            primi.add(ws[p])
    testi_tree = _testi(tree)
    grezzi = {}                     # (file, riga, tid) -> [(parole in comune, posizioni nel testo)]
    for rel in sorted(testi_tree):
        norm = _normalizza_testo(testi_tree[rel])
        parole, inizi = [], []
        for m in _PAROLA.finditer(norm):
            parole.append(m.group())
            inizi.append(m.start())
        if len(parole) < min_parole:
            continue
        a_capo = None
        for tid, inizio, n, posizioni in _catene_testo(_corse_testo(parole, indice, primi, min_blocco), max_buco):
            if n < min_parole:
                continue
            if a_capo is None:
                a_capo = [m.start() for m in re.finditer("\n", norm)]
            riga = bisect.bisect_right(a_capo, inizi[inizio]) + 1
            grezzi.setdefault((rel, riga, tid), []).append((n, posizioni))
    prompt = _prompt_di_catalogo(testi_tree, min_blocco) if any(
        voci[tid][0] in ORIGINI_PROMPT_APP for (_, _, tid) in grezzi) else []
    template = {}                   # tid dei prompt dell'app -> posizioni coperte dai blocchi del catalogo
    for tid in sorted({t for (_, _, t) in grezzi}):
        if voci[tid][0] in ORIGINI_PROMPT_APP and prompt:
            copertura, coperte = _copertura_template(voci[tid][1], prompt, min_blocco)
            if copertura >= SOGLIA_TEMPLATE:
                template[tid] = coperte
    per_riga, separati = {}, set()
    for (rel, riga, tid), catene in grezzi.items():
        for n, posizioni in catene:
            # separato solo se le parole in comune FUORI dal template non fanno da sole un riscontro
            if tid in template and len(posizioni - template[tid]) < min_parole:
                separati.add((rel, riga))
                continue
            k = (rel, riga, voci[tid][0])
            per_riga[k] = max(per_riga.get(k, 0), n)
    rimasti, applicate, senza = _applica_eccezioni(sorted(per_riga), "testo_pm", set(eccezioni or ()))
    hit = [Hit(r, n, "%s\u2026(%d parole)" % (o, per_riga[(r, n, o)])) for (r, n, o) in rimasti]
    note = ("%d testi da %d origini, %d cercabili (%d sotto %d parole NON cercabili); catena: blocchi da %d, "
            "buco <= %d, riscontro da %d parole, solo lettere NFKD" % (
                len(testi), len({o for o, _ in testi}), len(voci), corti, min_parole,
                min_blocco, max_buco, min_parole))
    if separati:
        elenco = sorted(separati)
        note += ("; %s dai prompt dell'app (copertura >= %.1f da %d prompt dei cataloghi, meno di %d "
                 "parole in comune fuori dal prompt) separati e DICHIARATI: %s%s" % (
                     _plurale(len(elenco), "riscontro", "riscontri"), SOGLIA_TEMPLATE, len(prompt), min_parole,
                     ", ".join("%s:%d" % x for x in elenco[:20]),
                     (" ... altri %d" % (len(elenco) - 20)) if len(elenco) > 20 else ""))
    if applicate:
        note += "; " + _plurale(applicate, "eccezione applicata", "eccezioni applicate")
    if senza:
        note += "; " + _plurale(len(senza), "eccezione senza riscontro", "eccezioni senza riscontro")
    note += ("; NON misura parafrasi, riassunti, traduzioni, copie con una parola cambiata ogni "
             "%d o meno, cioe' meno di %d parole intatte fra due modifiche, estratti e testi sotto "
             "%d parole, testo dei "
             "modelli, parole che vivono solo nei documenti privati" % (min_blocco, min_blocco, min_parole))
    return Esito("testo_pm", hit, note=note)


# --- T6: controllo 1, gitleaks pinnato (download con sha256, scansione della cartella) ---------

def scarica_gitleaks(url=GITLEAKS_URL, sha256_atteso=GITLEAKS_SHA256, dest_exe=GITLEAKS_EXE, fetch=None):
    """Scarica la release PINNATA, confronta lo sha256 PRIMA di scompattare ed estrae il solo
    gitleaks.exe in policy/bin/ (ignorato da git, la policy e' negli ESCLUSI). E' l'unica
    chiamata di rete di P2; `fetch` iniettabile = i test non toccano la rete."""
    if fetch is None:
        import urllib.request

        def fetch(u):
            with urllib.request.urlopen(u, timeout=120) as r:
                return r.read()
    dati = fetch(url)
    impronta = hashlib.sha256(dati).hexdigest()
    if impronta != sha256_atteso:
        raise RuntimeError("sha256 dello zip = %s…, attesa %s…: NON estratto"
                           % (impronta[:12], sha256_atteso[:12]))
    with zipfile.ZipFile(io.BytesIO(dati)) as z:
        nomi = [n for n in z.namelist() if n.lower().endswith("gitleaks.exe")]
        if not nomi:
            raise RuntimeError("lo zip non porta gitleaks.exe (%s)" % ", ".join(z.namelist()[:5]))
        cartella = os.path.dirname(dest_exe)
        if cartella:
            os.makedirs(cartella, exist_ok=True)
        with open(dest_exe, "wb") as fh:
            fh.write(z.read(nomi[0]))
    return dest_exe


def _saltati_gitleaks(tree_dir, stderr):
    """Paths the pinned directory scanner explicitly reports skipping."""
    root = Path(tree_dir).resolve()
    skipped = set()
    for raw in re.findall(r'skipping (?:file|directory): [^\n]*?path="(.+?)"', stderr):
        path = raw.replace("\\\\", "\\")  # zerolog escapes Windows separators
        try:
            skipped.add(Path(path).resolve().relative_to(root).as_posix())
        except ValueError:
            skipped.add(path.replace("\\", "/"))
    return skipped


def _copertura(tree_dir, stderr, stdin_letti=None, raster_pins=None):
    """Quanto ha letto DAVVERO il motore: la riga «scanned ~N bytes» (esatta al byte, misurata
    sul tree vero) confrontata coi byte del tree, meno i file che il config di default salta per
    disegno suo (GITLEAKS_SCOPERTI). Torna (errore, nota). Senza questa misura ogni salto del
    motore — lockfile, node_modules, .png, .zip, un file che non riesce ad aprire — usciva
    «0 hit, PULITO» senza dichiarare la copertura incompleta (review T6 F1)."""
    m = re.search(r"scanned ~(\d+) bytes", stderr)
    # 13/09 (Claude Opus 5): ogni motivo di KO resta nel verdetto, nessuno copre l'altro. Il
    # raster che non si certifica non toglie la dichiarazione dei byte che il motore non ha letto
    # (col numero e i nomi), il contatore assente non toglie l'esito del raster. Un raster non
    # certificato non riceve credito: i suoi byte restano fra gli attesi e il conto li nomina.
    errori = [] if m else ["il motore non dice quanti byte ha letto: copertura NON misurata"]
    try:
        # A scanner-only empty-directory probe is valid; export still rejects an
        # empty artifact through carica_tree's unchanged strict default.
        raster = _raster_verificati(carica_tree(tree_dir, vuoto_ammesso=True), raster_pins)
    except (OSError, ValueError) as exc:
        errori.append("verifica raster/ratifica non conclusa: " + str(exc))
        raster = {}
    stdin_letti = stdin_letti or {}
    if set(raster) - _saltati_gitleaks(tree_dir, stderr):
        errori.append("il motore non dichiara il salto degli esatti PNG ratificati: copertura raster NON misurata")
        raster = {}
    if set(raster) & set(stdin_letti):
        errori.append("PNG conteggiato sia come raster sia via stdin: copertura incoerente")
        raster = {}
    if not m:
        return ("; ".join(errori), "")
    raster_bytes = sum(row["bytes"] for row in raster.values())
    da_dir = int(m.group(1))
    letti, attesi = da_dir + sum(stdin_letti.values()), 0
    for cartella, sub, nomi in os.walk(tree_dir):
        sub[:] = [d for d in sub if d != ".git"]      # gitleaks salta .git come carica_tree (misurato)
        attesi += sum(os.path.getsize(os.path.join(cartella, n)) for n in nomi if n != ".git")
    fuori = [rel for rel in GITLEAKS_SCOPERTI
             if os.path.isfile(os.path.join(tree_dir, rel.replace("/", os.sep)))]
    scoperti = sum(os.path.getsize(os.path.join(tree_dir, rel.replace("/", os.sep))) for rel in fuori)
    nota = "%d/%d byte letti%s" % (letti, attesi,
                                   (" (%s fuori dal raggio del motore)" % ", ".join(fuori)) if fuori else "")
    if stdin_letti:
        svg = sum(rel.lower().endswith('.svg') for rel in stdin_letti)
        js = sum(rel.lower().endswith('.min.js') for rel in stdin_letti)
        nota += "; dir %d + %d SVG e %d .min.js via stdin %d byte, contatori del motore verificati" % (
            da_dir, svg, js, sum(stdin_letti.values()))
    if raster:
        nota += "; %d PNG ratificati, raster verificati %d byte (CRC/struttura/zlib/hash/provenienza); pixel NON scansionati da gitleaks" % (len(raster), raster_bytes)
    if letti > attesi - scoperti - raster_bytes:
        errori.append("conteggio del motore superiore ai byte attribuibili a dir/stdin: copertura incoerente")
    elif letti != attesi - scoperti - raster_bytes:
        nomi = sorted(_saltati_gitleaks(tree_dir, stderr) - set(stdin_letti) - set(raster))
        errori.append("letti %d byte su %d attesi (%d dichiarati fuori dal raggio, %d raster certificati separatamente): %d byte NON scansionati%s"
                      % (letti, attesi, scoperti, raster_bytes, attesi - scoperti - raster_bytes - letti,
                         (": " + ", ".join(nomi)) if nomi else ""))
    return ("; ".join(errori), nota)


def _scansiona_asset_gitleaks(tree_dir, exe, esegui, ambiente, temporaneo, stderr_dir):
    """Scan original SVG and skipped minified JS bytes through pinned stdin rules.

    Same pinned default rules and anti-ignore flags; no custom allowlist. Binary
    stdin preserves UTF-8 and CRLF exactly on Windows. Never reconstruct XML or
    inspect only rendered text: metadata, comments and attributes are all input.
    Only JS explicitly skipped by dir needs stdin: ordinary .min.js is already
    read there, while library names such as plotly are globally allowlisted.
    Counting both paths for the same file would invalidate byte coverage.
    """
    letti, finding = {}, []
    paths = []
    skipped = _saltati_gitleaks(tree_dir, stderr_dir)
    for folder, sub, names in os.walk(tree_dir):
        sub[:] = [name for name in sub if name != '.git']
        for name in names:
            path = Path(folder, name)
            rel = os.path.relpath(path, tree_dir).replace(os.sep, '/')
            if name.lower().endswith('.svg') or (name.lower().endswith('.min.js') and rel in skipped):
                paths.append(path)
    for index, path in enumerate(sorted(paths)):
        rel = os.path.relpath(path, tree_dir).replace(os.sep, '/')
        kind = 'SVG' if path.name.lower().endswith('.svg') else '.min.js'
        if path.is_symlink():
            raise ValueError(kind + ' collegato, sorgente non verificabile: ' + rel)
        payload = path.read_bytes()
        try:
            payload.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError(kind + ' non UTF-8, scansione non certificata: ' + rel) from exc
        report = os.path.join(temporaneo, 'asset-%d.json' % index)
        result = esegui([exe, 'stdin', '--no-banner', '--no-color', '--redact',
                         '--ignore-gitleaks-allow', '--gitleaks-ignore-path', temporaneo,
                         '--exit-code', str(GITLEAKS_EXIT_HIT), '--log-level', 'debug',
                         '--report-format', 'json', '--report-path', report],
                        input=payload, capture_output=True, env=ambiente, cwd=temporaneo)
        # No encoding/text mode on subprocess input: even a CRLF translation
        # would mean the scanner no longer inspected the original bytes.
        stderr = result.stderr or b''
        if isinstance(stderr, bytes): stderr = stderr.decode('utf-8', errors='replace')
        if result.returncode not in (0, GITLEAKS_EXIT_HIT):
            raise ValueError('%s %s: exit %d, scansione stdin NON conclusa' % (kind, rel, result.returncode))
        count = re.search(r'scanned ~(\d+) bytes', stderr)
        if not count or int(count.group(1)) != len(payload):
            raise ValueError('%s %s: copertura stdin non verificata (%s byte letti, %d inviati)' % (
                kind, rel, count.group(1) if count else 'n.d.', len(payload)))
        if not os.path.isfile(report):
            raise ValueError('%s %s: report stdin assente, scansione NON conclusa' % (kind, rel))
        with open(report, encoding='utf-8') as handle:
            rows = json.load(handle)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('%s %s: report stdin di forma ignota' % (kind, rel))
        if result.returncode == GITLEAKS_EXIT_HIT and not rows:
            raise ValueError('%s %s: exit leak ma report stdin vuoto, esito incoerente' % (kind, rel))
        if path.read_bytes() != payload:
            raise ValueError(kind + ' cambiato durante la scansione: ' + rel)
        letti[rel] = int(count.group(1))  # Observed engine count, not assumed input size.
        finding.extend(dict(row, File=str(path)) for row in rows)
    return letti, finding


def controllo_gitleaks(tree_dir, exe=GITLEAKS_EXE, esegui=None, raster_pins=None):
    """Il motore di terzi sulla CARTELLA esportata (gitleaks legge file, non il dict in
    memoria degli altri controlli). Ogni esito ambiguo e' un KO dichiarato, mai un «0 hit»:
      · gitleaks esce 1 sia per «leak trovati» sia per QUALUNQUE guasto (cmd/root.go v8.30.1:
        `if err != nil { os.Exit(1) }` viene PRIMA di `os.Exit(exitCode)`), percio' chiediamo
        i leak su GITLEAKS_EXIT_HIT e l'1 torna a significare «scansione non conclusa»;
      · il report lo scrive prima di uscire: se manca o non e' una lista JSON, KO;
      · la COPERTURA e' una misura, non una deduzione: `_copertura` confronta i byte che il motore
        dichiara di aver letto con quelli del tree (i file che salta per disegno suo sono elencati
        in GITLEAKS_SCOPERTI e finiscono nella nota); ogni altro salto e' un KO col numero e i nomi;
      · la versione la legge dall'EXE (`gitleaks version`), non dalla costante: un binario di
        un'altra versione, troncato o di un'altra architettura e' un KO, non una nota;
      · le regole sono le nostre: `gitleaks:allow` ignorato, `--gitleaks-ignore-path` tolto dal
        default '.' (la cwd di chi lancia) e le due variabili GITLEAKS_CONFIG* fuori
        dall'ambiente. Un `.gitleaks.toml`/`.gitleaksignore` DENTRO il tree lo spegnerebbe in
        silenzio (root.go li legge dalla cartella scansionata): li' rifiutiamo di scansionare.
      · gli SVG testuali e i .min.js saltati dall'allowlist del motore 8.30.1 passano anche
        via stdin binario con le stesse regole: contatore per file e report sul percorso
        originale. Non sono esclusioni e non si aggiungono a GITLEAKS_SCOPERTI.
    Il token dell'hit e' il NOME della regola: il segreto non entra mai nell'Esito."""
    if not os.path.isfile(exe):
        return Esito("gitleaks", errore="binario assente: %s (python tools/release/verifica_pubblico.py --scarica-gitleaks)" % exe)
    proprie = [n for n in (".gitleaks.toml", ".gitleaksignore")
               if os.path.exists(os.path.join(tree_dir, n))]
    if proprie:
        return Esito("gitleaks", errore="il tree porta %s: gitleaks userebbe QUELLE regole, non le nostre"
                                        % ", ".join(proprie))
    esegui = esegui or subprocess.run
    try:
        v = esegui([exe, "version"], capture_output=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return Esito("gitleaks", errore="binario non eseguibile: %s (%s)" % (exe, e))
    dette = ((v.stdout or "") + (v.stderr or "")).strip().splitlines()
    letta = dette[0].strip().lstrip("v") if dette else ""
    if v.returncode != 0 or letta != GITLEAKS_VERSIONE:
        return Esito("gitleaks", errore="l'eseguibile dice %r, atteso gitleaks %s: rilancia --scarica-gitleaks"
                                        % (letta[:20], GITLEAKS_VERSIONE))
    ambiente = {k: val for k, val in os.environ.items() if not k.startswith("GITLEAKS_")}
    with tempfile.TemporaryDirectory() as td:
        rep = os.path.join(td, "gitleaks.json")
        r = esegui([exe, "dir", tree_dir, "--no-banner", "--no-color", "--redact",
                    "--ignore-gitleaks-allow", "--gitleaks-ignore-path", td,
                    "--exit-code", str(GITLEAKS_EXIT_HIT), "--log-level", "debug",
                    "--report-format", "json", "--report-path", rep],
                   capture_output=True, encoding="utf-8", errors="replace", env=ambiente)
        if r.returncode not in (0, GITLEAKS_EXIT_HIT):
            return Esito("gitleaks", errore="exit %d, scansione NON conclusa: %s"
                                            % (r.returncode, (r.stderr or "").strip()[-300:]))
        if not os.path.isfile(rep):
            return Esito("gitleaks", errore="exit %d ma nessun report in %s: scansione NON conclusa"
                                            % (r.returncode, rep))
        try:
            with open(rep, encoding="utf-8") as fh:
                finding = json.load(fh) or []
        except ValueError as e:
            return Esito("gitleaks", errore="report illeggibile (%s: %s)" % (type(e).__name__, e))
        if not isinstance(finding, list):
            return Esito("gitleaks", errore="report di forma ignota (%s invece di una lista)" % type(finding).__name__)
        try:
            stdin_letti, stdin_finding = _scansiona_asset_gitleaks(tree_dir, exe, esegui, ambiente, td, r.stderr or "")
        except (OSError, ValueError) as e:
            return Esito('gitleaks', errore='Scansione SVG/min.js non conclusa: %s' % e)
        buco, quanto = _copertura(tree_dir, r.stderr or "", stdin_letti, raster_pins)
        if buco:
            return Esito("gitleaks", errore=buco)
        finding.extend(stdin_finding)
    hit = [Hit(os.path.relpath(f.get("File", ""), tree_dir).replace(os.sep, "/"),
               int(f.get("StartLine") or 0), str(f.get("RuleID", "?"))) for f in finding]
    return Esito("gitleaks", hit, note="gitleaks %s (versione letta dall'exe), dir, %s" % (letta, quanto))


# --- T7: gli 8 controlli in fila sul tree esportato ---------------------------------------------

def _importa_memory_db(importa=None):
    """Importa `memory_db` (che sa DOVE sta il DB del PM, come lo vede il backend) SENZA lasciare
    il suo `.env` nell'ambiente: memory_db lo carica con dotenv, e quei valori resterebbero nel
    processo — la suite del tree ESPORTATO (T7) li erediterebbe tutti (review T7)."""
    prima = dict(os.environ)
    try:
        if importa is not None:
            return importa()
        from bellomberg.storage import memory_db
        return memory_db
    finally:
        os.environ.clear()
        os.environ.update(prima)


def _env_con_autoprova(tree, path_env, eccezioni=()):
    """Review T3 F4: la garanzia del controllo env e' una MISURA a ogni run, non un numero nel
    changelog: prima l'autoprova (ogni valore visto cadere su un tree in memoria), poi il controllo."""
    coppie, corte = valori_env(path_env), variabili_corte(path_env)
    n, caduti, non_caduti = prova_env(coppie)
    if n == 0 or caduti < n:
        return Esito("env", errore="autoprova: %d valori, %d visti cadere%s" % (
            n, caduti, (", NON caduti: " + ", ".join(non_caduti)) if non_caduti else ""))
    return controllo_env(tree, coppie, corte, eccezioni)


class _MemoriaPigra:
    """Il DB del PM si importa alla PRIMA lettura di `SQLITE_PATH`, cioe' DENTRO il controllo che
    lo chiede (lotti, valori_db, ticker_soli, payload): un import fallito e' un KO di QUEL
    controllo — in osservazione non ferma — e non del preambolo, che fermerebbe anche un
    pre-commit i cui controlli bloccanti non leggono il DB (review 05/09). In CI (fase `ci`
    dell'hook) nessuno lo legge e non si importa mai."""
    def __init__(self):
        self._m = None

    @property
    def SQLITE_PATH(self):
        if self._m is None:
            self._m = _importa_memory_db()
        return self._m.SQLITE_PATH


def _normalizza_hash(x):
    if isinstance(x, dict):
        return {str(k): _normalizza_hash(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (set, frozenset, tuple, list)):
        return sorted((_normalizza_hash(v) for v in x),
                      key=lambda v: json.dumps(v, sort_keys=True, default=str))
    return x


def _sha_json(x):
    raw = json.dumps(_normalizza_hash(x), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass
class InputCongelati:
    valori: dict = field(default_factory=dict)
    errori: dict = field(default_factory=dict)
    assenti: list = field(default_factory=list)
    hash_liste: dict = field(default_factory=dict)
    sha256_corpus_privato: str = ""
    sha256_payload: str = ""
    conteggi: dict = field(default_factory=dict)

    def leggi(self, nome):
        if nome in self.errori:
            raise RuntimeError("input congelato %s: %s" % (nome, self.errori[nome]))
        return self.valori[nome]

    def manifest(self):
        return {
            "sha256_corpus_privato": self.sha256_corpus_privato or None,
            "sha256_payload": self.sha256_payload or None,
            "hash_liste": dict(sorted(self.hash_liste.items())),
            "conteggi": dict(sorted(self.conteggi.items())),
            "fonti_assenti": sorted(self.assenti),
            # Il dettaglio resta nell'esito del controllo; il sidecar pubblico porta
            # soltanto il tipo del guasto, mai path o frammenti degli input privati.
            "fonti_ko": {k: self.errori[k].split(":", 1)[0]
                          for k in sorted(self.errori)},
        }


def congela_input(tree_dir, controlli, pubblico, corpus_root):
    """Congela gli input privati al confine, senza esporne i valori nel manifest.

    tree_dir e' il codice materializzato; corpus_root e' la sorgente privata
    esplicita (checkout o snapshot). Il DB passa soltanto dalle funzioni read-only.
    """
    if not os.path.isdir(corpus_root):
        raise FileNotFoundError("corpus root non trovato: " + str(corpus_root))
    out = InputCongelati()

    def cattura(nome, fn, path=None, assente_ammesso=False):
        if path and not os.path.exists(path):
            out.assenti.append(nome)
            if assente_ammesso:
                out.valori[nome] = set() if nome == "simboli" else []
                out.conteggi[nome] = 0
                return
        try:
            valore = fn()
            out.valori[nome] = valore
            try:
                out.conteggi[nome] = len(valore)
            except TypeError:
                out.conteggi[nome] = 1
        except Exception as e:
            out.errori[nome] = "%s: %s" % (type(e).__name__, str(e)[:240])

    richiesti = set(controlli)
    parser_liste = {
        "VIETATE.txt": _lista_da_testo,
        "ESCLUSI.txt": _lista_da_testo,
        "ECCEZIONI.txt": lambda t: _eccezioni_da_testo(t, "ECCEZIONI.txt"),
        "SIMBOLI.txt": lambda t: _simboli_da_testo(t, "SIMBOLI.txt"),
        "GRANDI_AMMESSI.txt": _lista_da_testo,
        "SCREENSHOTS_APPROVATI.json": _raster_pins_da_testo,
        BUDGET_SCREENSHOTS: _budget_screenshot_da_testo,
    }
    for nome, parser in parser_liste.items():
        path = os.path.join(pubblico, nome)
        if os.path.isfile(path) or (nome == BUDGET_SCREENSHOTS and os.path.lexists(path)):
            try:
                with open(path, "rb") as fh:
                    raw = fh.read()
                out.hash_liste[nome] = hashlib.sha256(raw).hexdigest()
                out.valori["lista:" + nome] = parser(raw.decode("utf-8-sig"))
            except Exception as e:
                out.errori["lista:" + nome] = "%s: %s" % (type(e).__name__, str(e)[:240])
        else:
            out.assenti.append("lista:" + nome)
            out.valori["lista:" + nome] = (None if nome == BUDGET_SCREENSHOTS else
                                         set() if nome in {"ECCEZIONI.txt", "SIMBOLI.txt"} else [])
            if ((nome == "VIETATE.txt" and richiesti & {"vietate", "vietate_forme"})
                    or (nome == "ESCLUSI.txt" and "esclusi" in richiesti)):
                out.errori["lista:" + nome] = "FileNotFoundError: lista richiesta assente"

    env_path = os.path.join(corpus_root, ".env")
    db_path = os.path.join(corpus_root, "data", "consigliere.db")
    tuple_path = os.path.join(
        corpus_root, "archive", "private", "attic", "oneshot", "import_user_trades.py")
    privata_path = os.path.join(corpus_root, "tests", "system", "prova_numeri_del_book.py")

    if "env" in richiesti:
        cattura("env_tutte", lambda: _coppie_env(env_path), env_path)
        if "env_tutte" in out.valori:
            tutte = out.valori.pop("env_tutte")
            out.conteggi.pop("env_tutte", None)
            out.valori["env_coppie"] = [(k, v) for k, v in tutte if len(v) >= MIN_ENV]
            out.valori["env_corte"] = list(dict.fromkeys(
                k for k, v in tutte if 0 < len(v) < MIN_ENV))
            out.conteggi["env_coppie"] = len(out.valori["env_coppie"])
            out.conteggi["env_corte"] = len(out.valori["env_corte"])
        elif "env_tutte" in out.errori:
            errore = out.errori.pop("env_tutte")
            out.errori["env_coppie"] = errore
            out.errori["env_corte"] = errore
    if richiesti & {"lotti", "valori_db", "valori_estesi", "ticker_soli", "payload"}:
        if "lotti" in richiesti:
            cattura("lotti_db", lambda: lotti_db(db_path), db_path)
            cattura("lotti_tuple", lambda: lotti_da_tuple(tuple_path), tuple_path)
        if "valori_db" in richiesti:
            cattura("valori_db", lambda: valori_db(db_path), db_path)
        if "valori_estesi" in richiesti:
            cattura("valori_estesi", lambda: valori_db(db_path, COLONNE_ESTESE), db_path)
            cattura("percentuali", lambda: percentuali_db(db_path), db_path)
        if "ticker_soli" in richiesti or "payload" in richiesti:
            cattura("ticker", lambda: ticker_db(db_path), db_path)
        if "payload" in richiesti:
            cattura("nomi", lambda: nomi_db(db_path), db_path)
    if "lista_privata" in richiesti:
        cattura("lista_privata", lambda: lista_privata(privata_path), privata_path)
    if "testo_pm" in richiesti:
        # 13/09 (G3B): i testi liberi dell'utente; nel manifest entrano solo il conteggio e l'impronta
        cattura("testi_utente", lambda: testi_utente_db(db_path), db_path)
    if "payload" in richiesti:
        cattura("payload", lambda: payload_statico_da_tree(tree_dir), tree_dir)

    privati = {k: v for k, v in out.valori.items()
               if k != "payload" and not k.startswith("lista:")}
    errori_privati = {k: v for k, v in out.errori.items()
                      if k != "payload" and not k.startswith("lista:")}
    # Le liste pubbliche hanno hash propri e il payload ha un'impronta separata. Qui
    # entrano soltanto le sorgenti private lette (o il loro tipo di guasto).
    out.sha256_corpus_privato = _sha_json(
        {"valori": privati,
         "errori": {k: v.split(":", 1)[0] for k, v in errori_privati.items()}})
    if "payload" in out.valori:
        testi_payload, guasti_payload = out.valori["payload"]
        out.conteggi["payload_canali"] = len(testi_payload)
        out.conteggi["payload_canali_guasti"] = len(guasti_payload)
        out.sha256_payload = _sha_json(out.valori["payload"])
    return out


def esegui_controlli(tree_dir, solo=None, blocca_osservazione=False, pubblico=None,
                     corpus_root=None, input_congelati=None, registro_input=None):
    """Esegue i controlli e torna (esiti, non_eseguiti) SENZA stampare: e' il cuore di
    `esegui_tutti` (che vi aggiunge il verdetto) e di `tools/release/cancello_hook.py` (che ne stampa
    uno PARZIALE, dichiarato). Il DB del PM si importa solo quando un controllo lo legge
    (`_MemoriaPigra`), mai nel preambolo (05/09, voce 7). `pubblico` = la cartella delle liste
    (VIETATE, ESCLUSI, ECCEZIONI, GRANDI_AMMESSI): l'hook la passa materializzata dalla FONTE che
    misura (indice o HEAD), cosi' un edit non committato di una chat non giudica il commit di
    un'altra (voce 3, 05/09); senza, le liste di casa."""
    liste = pubblico or PUBBLICO
    try:
        da_fare = list(CONTROLLI) if not solo else [c for c in CONTROLLI if c in solo]
        memoria = _MemoriaPigra()
        sconosciuti = [c for c in (solo or []) if c not in CONTROLLI]
        if sconosciuti:
            raise ValueError("controlli sconosciuti: %r (validi: %s)" % (sconosciuti, ", ".join(CONTROLLI)))
        tree = carica_tree(tree_dir)
        congelati = input_congelati
        if congelati is None and corpus_root is not None:
            congelati = congela_input(tree_dir, da_fare, liste, os.path.realpath(corpus_root))
        if congelati is None:
            ecc = leggi_eccezioni(os.path.join(liste, "ECCEZIONI.txt"))
            simboli = leggi_simboli(os.path.join(liste, "SIMBOLI.txt"))
            grandi_path = os.path.join(liste, "GRANDI_AMMESSI.txt")
            grandi = leggi_lista(grandi_path) if os.path.isfile(grandi_path) else []
            raster_path = os.path.join(liste, "SCREENSHOTS_APPROVATI.json")
            if os.path.isfile(raster_path):
                with open(raster_path, encoding="utf-8-sig") as handle:
                    raster_pins = _raster_pins_da_testo(handle.read())
            else:
                raster_pins = []
            budget_path = os.path.join(liste, BUDGET_SCREENSHOTS)
            budget_screenshot = None
            if os.path.lexists(budget_path):
                with open(budget_path, encoding="utf-8-sig") as handle:
                    budget_screenshot = _budget_screenshot_da_testo(handle.read())
        else:
            ecc = congelati.leggi("lista:ECCEZIONI.txt")
            simboli = congelati.leggi("lista:SIMBOLI.txt")
            grandi = congelati.leggi("lista:GRANDI_AMMESSI.txt")
            raster_pins = congelati.leggi("lista:SCREENSHOTS_APPROVATI.json")
            budget_screenshot = congelati.leggi("lista:" + BUDGET_SCREENSHOTS)
        tree = _TreeRaster(tree, raster_pins)
        ecc, grandi, upstream = _risolvi_deroghe_upstream(tree, ecc, grandi)
        liste_congelate, errori_liste = {}, {}
        if "vietate" in da_fare or "vietate_forme" in da_fare:
            try:
                liste_congelate["vietate"] = (
                    congelati.leggi("lista:VIETATE.txt") if congelati is not None
                    else leggi_lista(os.path.join(liste, "VIETATE.txt")))
            except Exception as e:
                errori_liste["vietate"] = "%s: %s" % (type(e).__name__, e)
        if "esclusi" in da_fare:
            try:
                liste_congelate["esclusi"] = (
                    congelati.leggi("lista:ESCLUSI.txt") if congelati is not None
                    else leggi_lista(os.path.join(liste, "ESCLUSI.txt")))
            except Exception as e:
                errori_liste["esclusi"] = "%s: %s" % (type(e).__name__, e)
        if registro_input is not None:
            registro_input.clear()
            if congelati is None:
                registro_input.update({
                    "sha256_corpus_privato": None,
                    "sha256_payload": None,
                    "hash_liste": {},
                    "conteggi": {},
                    "fonti_assenti": ["corpus_root non dichiarato"],
                    "fonti_ko": {},
                })
            else:
                registro_input.update(congelati.manifest())
    except Exception as e:      # niente traceback e niente exit 1: il verdetto lo dichiara
        return [Esito("cancello", errore="%s: %s" % (type(e).__name__, e))], list(CONTROLLI)
    esiti = []

    def fonte(nome, legacy):
        return congelati.leggi(nome) if congelati is not None else legacy()

    def lista(nome):
        if nome in errori_liste:
            raise RuntimeError("lista %s: %s" % (nome, errori_liste[nome]))
        return liste_congelate[nome]

    def prova(nome, fn):
        try:
            e = fn()
        except Exception as ex:  # dichiarato nel verdetto, non ingoiato
            e = Esito(nome, errore="%s: %s" % (type(ex).__name__, ex))
        if upstream and nome in ("dimensione", "lista_privata", "lotti", "ticker_soli", "valori_estesi"):
            e.note += "; %d asset dichiarati verificati per percorso e SHA-256" % len(upstream)
        e.osservazione = (nome in OSSERVAZIONE) and not blocca_osservazione
        esiti.append(e)

    for nome in da_fare:
        if nome == "gitleaks":
            prova(nome, lambda: controllo_gitleaks(tree_dir, raster_pins=raster_pins) if raster_pins else controllo_gitleaks(tree_dir))
        elif nome == "vietate":
            prova(nome, lambda: controllo_vietate(tree, lista("vietate"), ecc))
        elif nome == "env":
            def _env():
                if congelati is None:
                    return _env_con_autoprova(tree, os.path.join(REPO, ".env"), ecc)
                coppie = fonte("env_coppie", lambda: [])
                corte = fonte("env_corte", lambda: [])
                n, caduti, non_caduti = prova_env(coppie)
                if n == 0 or caduti < n:
                    return Esito("env", errore="autoprova: %d valori, %d visti cadere%s" % (
                        n, caduti, (", NON caduti: " + ", ".join(non_caduti)) if non_caduti else ""))
                return controllo_env(tree, coppie, corte, ecc)
            prova(nome, _env)
        elif nome == "esclusi":
            prova(nome, lambda: controllo_esclusi(tree, lista("esclusi")))
        elif nome == "lotti":
            prova(nome, lambda: controllo_lotti(
                tree, (fonte("lotti_db", lambda: lotti_db(memoria.SQLITE_PATH))
                       | fonte("lotti_tuple", lambda: lotti_da_tuple(
                           os.path.join(REPO, "archive", "private", "attic", "oneshot",
                                        "import_user_trades.py")))), ecc))
        elif nome == "dimensione":
            prova(nome, lambda: controllo_dimensione(tree, grandi, budget_screenshot=budget_screenshot))
        elif nome == "valori_db":
            prova(nome, lambda: controllo_valori_db(
                tree, fonte("valori_db", lambda: valori_db(memoria.SQLITE_PATH)), ecc))
        elif nome == "valori_estesi":
            # 05/09 (buco D5, 4b), OSSERVAZIONE: guidance e prezzi delle posizioni (COLONNE_ESTESE) +
            # le guidance in percentuale con le forme col %; stesso motore di valori_db, nome a parte
            # finche' il conto non e' zero (i riscontri sono nei file di altre chat: v. ponte 05/09)
            def _estesi():
                e = controllo_valori_db(
                    tree,
                    fonte("valori_estesi", lambda: valori_db(memoria.SQLITE_PATH, COLONNE_ESTESE)),
                    ecc,
                    percentuali=fonte(
                        "percentuali", lambda: percentuali_db(memoria.SQLITE_PATH)),
                    nome_controllo="valori_estesi")
                return e
            prova(nome, _estesi)
        elif nome == "vietate_forme":
            prova(nome, lambda: controllo_vietate_forme(
                tree, lista("vietate"), ecc))
        elif nome == "lista_privata":
            prova(nome, lambda: controllo_lista_privata(
                tree, fonte("lista_privata", lambda: lista_privata(
                    os.path.join(REPO, "tests", "system", "prova_numeri_del_book.py"))), ecc))
        elif nome == "ticker_soli":
            prova(nome, lambda: controllo_ticker_soli(
                tree, fonte("ticker", lambda: ticker_db(memoria.SQLITE_PATH)) | simboli, ecc,
                                                      dichiarati=len(simboli)))
        elif nome == "payload":
            # Nel percorso legacy il corpus e' reso importando i moduli; nel percorso
            # congelato arriva da `payload_statico_da_tree(tree_dir)`. In entrambi i casi
            # misura il TESTO SPEDITO AI MODELLI, quindi risponde alla domanda «cosa DECIDE».
            def _payload():
                if congelati is None:
                    db = memoria.SQLITE_PATH
                    # 13/09: gli import del payload legacy caricano il `.env` del privato
                    # (config.py fa load_dotenv); come per `_importa_memory_db` l'ambiente
                    # torna com'era, o la suite dell'export eredita le chiavi del PM.
                    prima = dict(os.environ)
                    try:
                        testi, guasti = payload_statico()
                    finally:
                        os.environ.clear()
                        os.environ.update(prima)
                    ticker = ticker_db(db)
                    nomi = nomi_db(db)
                else:
                    testi, guasti = fonte("payload", lambda: ({}, []))
                    ticker = fonte("ticker", set)
                    nomi = fonte("nomi", set)
                return controllo_payload(testi, ticker | simboli, nomi, guasti, ecc,
                                         dichiarati=len(simboli))
            prova(nome, _payload)
        elif nome == "testo_pm":
            # 13/09 (G3B), OSSERVAZIONE: il testo libero dell'utente (COLONNE_TESTO_UTENTE) copiato nel tree
            prova(nome, lambda: controllo_testo_pm(
                tree, fonte("testi_utente", lambda: testi_utente_db(memoria.SQLITE_PATH)), ecc))
        else:
            # 04/09: senza questo ramo un nome aggiunto a CONTROLLI e mai cablato qui veniva
            # SALTATO in silenzio, e `non_eseguiti` non lo vedeva (sta in da_fare): il cancello
            # usciva col suo verdetto come se fosse girato tutto. E' il buco che avrebbe reso
            # INERTE il decimo controllo il giorno in cui lo si registra.
            prova(nome, lambda n=nome: Esito(n, errore=(
                "registrato in CONTROLLI ma senza ramo in esegui_tutti: NON e' stato eseguito")))
    if len(esiti) != len(da_fare):
        # cintura: un ramo che per un errore non chiamasse prova() non si vedrebbe altrimenti.
        # Una garanzia dichiarata dev'essere una MISURA: qui si contano gli esiti, non si spera.
        esiti = esiti + [Esito("cancello", errore=(
            "%d controlli richiesti, %d esiti prodotti: un ramo di esegui_tutti non ha "
            "chiamato prova()" % (len(da_fare), len(esiti))))]
    return esiti, [c for c in CONTROLLI if c not in da_fare]


def esegui_tutti(tree_dir, solo=None, blocca_osservazione=False, corpus_root=None,
                 input_congelati=None, registro_input=None, registro_esiti=None):
    """Tutti i controlli sul tree esportato, sorgenti dal privato, col VERDETTO stampato. Ogni
    sorgente mancante e' un KO dichiarato NEL SUO controllo, mai un salto; un guasto del PREAMBOLO
    (tree inesistente, liste illeggibili, `--solo` con un nome sconosciuto) e' un KO di tutto il
    cancello e vale exit 2, non 1 — che in tutto il cancello vuol dire «hit trovati» (review T7).
    `blocca_osservazione=True` toglie l'esenzione ai controlli di OSSERVAZIONE: si usa quando si
    DEPOSITA davvero (export_pubblico --commit). Senza, il deposito girerebbe su un exit 0 che
    dichiara i riscontri solo a parole, e le parole non le legge nessuna macchina (scettici 04/09)."""
    esiti, non_eseguiti = esegui_controlli(
        tree_dir, solo=solo, blocca_osservazione=blocca_osservazione,
        corpus_root=corpus_root, input_congelati=input_congelati,
        registro_input=registro_input)
    if registro_esiti is not None:
        registro_esiti.clear()
        registro_esiti.update({
            "rigoroso": bool(blocca_osservazione),
            "controlli": [
                {"nome": e.nome, "hit": len(e.hit), "errore": bool(e.errore),
                 "osservazione": bool(e.osservazione),
                 **({"misure": dict(e.misure)} if e.misure else {})}
                for e in esiti
            ],
            "non_eseguiti": list(non_eseguiti),
        })
    return verdetto(esiti, non_eseguiti=non_eseguiti)


def _argparser():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", help="cartella del tree esportato")
    ap.add_argument("--solo", help="controlli da eseguire, separati da virgola (il verdetto resta INCOMPLETO)")
    ap.add_argument("--prova-env", action="store_true", help="autoprova del controllo 3 sul .env vivo (stampa conteggi)")
    ap.add_argument("--env", default=os.path.join(REPO, ".env"), help="percorso del .env (default: quello del repo)")
    ap.add_argument("--scarica-gitleaks", action="store_true",
                    help="scarica la release pinnata di gitleaks in policy/bin/ (sha256 confrontato prima)")
    return ap


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # review T2 F8: stdout redirezionato = cp1252
    except AttributeError:                                           # stdout sostituito (capsys)
        pass
    ap = _argparser()
    a = ap.parse_args(argv)
    if a.prova_env and (a.tree or a.solo or a.scarica_gitleaks):
        # review T3 F7: prima --prova-env vinceva in silenzio e un KO dichiarato diventava un verde
        ap.error("--prova-env si usa da solo (con --tree/--solo/--scarica-gitleaks le altre opzioni resterebbero ignorate)")
    if a.scarica_gitleaks and (a.tree or a.solo):
        # stessa classe di F7: il download non esegue il cancello, e --tree resterebbe ignorato
        ap.error("--scarica-gitleaks si usa da solo (scarica il binario pinnato, non esegue i controlli)")
    if a.prova_env:
        try:
            coppie, corte = valori_env(a.env), variabili_corte(a.env)
        except (FileNotFoundError, ValueError) as e:
            print("prova-env: KO, %s" % e)
            return 2
        n, caduti, non_caduti = prova_env(coppie)
        riga = "prova-env: valori %d, visti cadere %d" % (n, caduti)
        if corte:
            riga += "; %s: %s" % (_plurale(len(corte), "variabile con valore corto non cercata",
                                            "variabili con valore corto non cercate"), ", ".join(corte))
        print(riga)
        if n == 0:
            print("prova-env: KO, nessun valore da %d caratteri in su: non e' un verde" % MIN_ENV)
        elif caduti < n:
            print("prova-env: KO, %s su %d NON %s cadere: %s" % (
                _plurale(n - caduti, "valore", "valori"), n, "visto" if n - caduti == 1 else "visti", ", ".join(non_caduti)))
        return 0 if (n > 0 and caduti == n) else 1
    if a.scarica_gitleaks:
        try:
            print("scaricato: %s" % scarica_gitleaks())
        except (RuntimeError, OSError) as e:   # sha256 diverso, rete, disco: 2 = guasto, non 1 = hit
            print("KO: %s" % e)
            return 2
        return 0
    if a.tree:
        return esegui_tutti(a.tree, solo=a.solo.split(",") if a.solo else None)
    ap.error("serve --tree, --prova-env o --scarica-gitleaks")


if __name__ == "__main__":
    sys.exit(main())
