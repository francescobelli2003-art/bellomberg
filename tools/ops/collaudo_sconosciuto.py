# -*- coding: utf-8 -*-
"""Il collaudo dello sconosciuto (voce 8, criterio 3: «un clone pulito che si avvia e dichiara
ogni dato mancante»). Fa, misurando, quello che il README promette a chi clona:

  1. il tree che lo sconosciuto riceve (export in temp, oppure `--tree <clone>`)
  2. venv nuovo + `pip install -r requirements.txt` (+ pytest, come dice il README)
  3. `.env` dal template — PRIMA verbatim (PIN vuoto): il login deve rispondere 503 DICHIARATO
  4. `.env` col solo PIN: login, poi OGNI GET dell'app senza chiavi e a book vuoto, classificato
     DATO / DICHIARATO (errore, KO, n.d., stale...) / VUOTO (200 senza dato ne' avviso) / CRASH (5xx)
     / N.A. (vuole un parametro che la sonda non ha) / TIMEOUT
  5. la prima operazione di un utente vero: VERSAMENTO, BUY di un simbolo INVENTATO, la posizione
     in portafoglio e la cassa aggiornata
  6. `pytest tests/` nel venv (solo i pacchetti di requirements.txt: quello che ha lo sconosciuto)

Ogni fase e' un contatore, non una frase; il rapporto (stdout e `--rapporto`) porta una riga per
route. Il backend gira su una porta DIVERSA da 8765 (mai quella dell'app in uso) con un ambiente
RIPULITO (nessuna chiave della macchina che lo lancia) e una cartella dati temporanea.

Uso:
  python scripts/collaudo_sconosciuto.py                    # export --tieni --senza-suite, poi tutto
  python scripts/collaudo_sconosciuto.py --tree .           # su un clone (il tuo): rifiuta se .env esiste
  python scripts/collaudo_sconosciuto.py --tree D --venv V --senza-pip   # riusa un venv gia' installato
  --porta 8766 · --rapporto FILE.md · --senza-suite · --timeout-richiesta 30 · --anche-sporco (export)

Exit: 0 = collaudo completo senza guasti (VUOTO e TIMEOUT contano, non fermano: osservazione) ·
1 = un guasto misurato (pip, pacchetto mancante, avvio, login, CRASH, flusso, suite rossa) ·
2 = una fase NON eseguita o un KO dello strumento (porta, export, tree non pulito).
Guardia: se nel tree esistono gia' `.env`, `portfolio.json` o `data/`, si FERMA (exit 2): il
collaudo li scrive, e non sovrascrive quelli di nessuno.
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

BUCKETS = ("DATO", "DICHIARATO", "VUOTO", "CRASH", "N.A.", "TIMEOUT")
PIN_COLLAUDO = "4821"            # quattro cifre, non quello debole: il template dice cosi'
SIMBOLO_INVENTATO = "ZZTEST"     # nessun mercato lo quota: un prezzo «trovato» sarebbe inventato
ID_INVENTATO = 999999
PORTA_VIETATA = 8765             # l'app in uso (README): il collaudo non la tocca mai
VERSAMENTO_EUR = 1000.0
QUANTITA_INVENTATA = 10
PREZZO_INVENTATO = 12.5          # 10 x 12,5 = 125 EUR: cassa attesa 875 dopo il BUY

# --- come una risposta DICHIARA un buco (regola 14/07: mai un proxy zitto) -------------------
CHIAVI_DICHIARAZIONE = frozenset({
    "error", "errors", "errore", "errori", "warning", "warnings", "avviso", "avvisi", "stale",
    "ko", "missing", "mancante", "mancanti", "misconfigured", "unavailable", "non_disponibile"})
# giro 2 del 05/09: `stale_positions`, `sources_failed`, `fonti_mute` erano dichiarazioni vere che
# il nome esatto non prendeva — basta la PAROLA dentro la chiave, con un valore pieno
PAROLE_NELLA_CHIAVE = ("stale", "failed", "fallit", "mute", "missing", "mancant", "error", "warning", "avvis")
CHIAVI_STATO = frozenset({"status", "stato", "state", "esito", "result"})
VALORI_STATO = frozenset({
    "error", "errore", "ko", "stale", "n.d.", "nd", "missing", "unavailable", "degraded",
    "misconfigured", "not_configured", "no_data", "fallito", "failed", "assente"})
CHIAVI_FALSE = frozenset({"ok", "configured", "available", "disponibile", "configurato"})
PREFISSI_STRINGA = ("n.d.", "ko:", "ko ", "stale", "error:", "errore:")
VALORI_STRINGA = frozenset({"n.d.", "nd", "ko", "stale", "error", "errore"})

# --- l'ambiente che resta allo sconosciuto: il sistema, niente chiavi ------------------------
VARIABILI_DI_SISTEMA = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "USERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG",
    "LC_ALL", "TERM", "SHELL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PYTHONUTF8",
    "PYTHONIOENCODING", "TZ"})

# Eseguito nel VENV dello sconosciuto, cwd = tree: importa l'app (come fa uvicorn) e stampa le
# route con i parametri di percorso e le query OBBLIGATORIE. `app.routes` e' la fonte, non una
# grep del sorgente: /openapi.json e' spento nell'app (quick-win n.10).
PROGRAMMA_ROUTE = """
import importlib, json, os, sys
sys.path.insert(0, os.getcwd())
m = importlib.import_module(os.environ.get("COLLAUDO_MODULO", "bellomberg.api.bellomberg_api"))
out = []
for r in m.app.routes:
    dep = getattr(r, "dependant", None)
    if dep is None:
        continue
    qr = []
    for q in dep.query_params:
        req = getattr(q, "required", None)
        if req is None:
            fi = getattr(q, "field_info", None)
            req = fi.is_required() if hasattr(fi, "is_required") else False
        if req:
            qr.append(q.name)
    out.append({"path": r.path, "methods": sorted(r.methods or []),
                "path_params": [p.name for p in dep.path_params], "query_required": qr})
print(json.dumps(out))
"""

# Lo stesso avvio del README (`python bellomberg_api.py` = uvicorn su bellomberg_api:app), sulla
# porta del collaudo. La porta passa per l'ambiente: niente formattazione dentro il programma.
PROGRAMMA_SERVER = """
import os, sys
sys.path.insert(0, os.getcwd())
import uvicorn
uvicorn.run("bellomberg.api.bellomberg_api:app", host="127.0.0.1", port=int(os.environ["COLLAUDO_PORTA"]),
            log_level="warning")
"""


# ============================================================================
# PARTI PURE (provate in tests/test_collaudo_sconosciuto.py)
# ============================================================================
def _tronca(v, n=80):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "..."


def trova_dichiarazione(body, percorso="", profondita=0):
    """Il primo punto in cui il corpo DICHIARA un buco: «percorso=valore», o None."""
    if profondita > 8:
        return None
    if isinstance(body, str):
        s = body.strip().lower()
        if s in VALORI_STRINGA or s.startswith(PREFISSI_STRINGA):
            return "%s=%s" % (percorso or "corpo", _tronca(body))
        return None
    if isinstance(body, dict):
        for k, v in body.items():
            kl = str(k).lower()
            qui = "%s.%s" % (percorso, k) if percorso else str(k)
            if (kl in CHIAVI_DICHIARAZIONE or any(p in kl for p in PAROLE_NELLA_CHIAVE)) and v:
                return "%s=%s" % (qui, _tronca(v))
            if kl in CHIAVI_STATO and isinstance(v, str) and v.strip().lower() in VALORI_STATO:
                return "%s=%s" % (qui, v)
            if kl in CHIAVI_FALSE and v is False:
                return "%s=false" % qui
            d = trova_dichiarazione(v, qui, profondita + 1)
            if d:
                return d
        return None
    if isinstance(body, list):
        for i, v in enumerate(body[:100]):
            d = trova_dichiarazione(v, "%s[%d]" % (percorso, i), profondita + 1)
            if d:
                return d
    return None


def vuoto(body):
    """Vero se il corpo non porta NESSUN dato: None, stringa vuota, lista/dizionario vuoti o fatti
    solo di vuoti. Zero e False SONO dati (una cassa a 0 e' una misura)."""
    if body is None or body == "" or body == [] or body == {}:
        return True
    if isinstance(body, dict):
        return all(vuoto(v) for v in body.values())
    if isinstance(body, list):
        return all(vuoto(v) for v in body)
    return False


def collezioni_vuote(body):
    """Vero se il corpo e' un dizionario con ALMENO una lista/dizionario al primo livello e TUTTE
    vuote: `count:0 items:[] timestamp` e' la forma tipica di una fonte che tace (giro 2 del 05/09:
    /news/ticker senza chiavi, /news/feed, /news/ipo-calendar)."""
    if not isinstance(body, dict):
        return False
    collezioni = [v for v in body.values() if isinstance(v, (list, dict))]
    return bool(collezioni) and all(vuoto(c) for c in collezioni)


def _testo_detail(body):
    if isinstance(body, dict) and "detail" in body:
        return _tronca(body["detail"], 160)
    return _tronca(body, 160) if body is not None else ""


def _422_della_sonda(body):
    """Un 422 di validazione con `loc` in query/path e' colpa della sonda (parametro che non ha),
    non del codice; un 422 di guardia («GUARDIA PREZZI...») e' una dichiarazione."""
    det = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(det, list):
        return False
    for d in det:
        loc = d.get("loc") if isinstance(d, dict) else None
        if isinstance(loc, list) and loc and loc[0] in ("query", "path"):
            return True
        if isinstance(d, dict) and d.get("type") == "missing":
            return True
    return False


def classifica(status, body, errore=None):
    """Il verdetto su UNA risposta: {bucket, motivo}."""
    if errore:
        return {"bucket": "TIMEOUT", "motivo": errore}
    if status is None:
        return {"bucket": "TIMEOUT", "motivo": "nessuna risposta"}
    if status == 500 or (status > 500 and not (isinstance(body, dict) and body.get("detail"))):
        # 500 = eccezione non prevista (_err500); un 503 con `detail` e' un rifiuto VOLUTO
        # e motivato (fail-closed: PIN assente, DB occupato, portfolio.json illeggibile)
        return {"bucket": "CRASH", "motivo": "%d %s" % (status, _testo_detail(body))}
    if status == 422 and _422_della_sonda(body):
        return {"bucket": "N.A.", "motivo": "422 parametro della sonda: %s" % _testo_detail(body)}
    if status >= 400:
        return {"bucket": "DICHIARATO", "motivo": "%d %s" % (status, _testo_detail(body))}
    d = trova_dichiarazione(body)
    if d:
        return {"bucket": "DICHIARATO", "motivo": d}
    if vuoto(body):
        return {"bucket": "VUOTO", "motivo": "%d senza dato ne' avviso" % status}
    if collezioni_vuote(body):
        return {"bucket": "VUOTO", "motivo": "%d con ogni collezione vuota e nessun avviso" % status}
    return {"bucket": "DATO", "motivo": ""}


def ambiente_pulito(base, data_dir=None):
    """(ambiente, scartate): resta il sistema, va via tutto il resto — chiavi comprese. Le due
    variabili Python fissano l'UTF-8 dei print quando lo stdout e' un file (in un terminale
    Windows lo e' gia'): la misura riguarda il codice, non la console di chi lo lancia."""
    env, scartate = {}, []
    for k, v in base.items():
        if k.upper() in VARIABILI_DI_SISTEMA:
            env[k] = v
        else:
            scartate.append(k)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if data_dir:
        env["BELLOMBERG_DATA_DIR"] = data_dir
    return env, scartate


def testo_env(template, pin=None):
    """`copy .env.example .env` (pin=None: verbatim) e poi il solo PIN, come fa chi clona."""
    if pin is None:
        return template
    nuovo, n = re.subn(r"^BELLOMBERG_PIN=.*$", "BELLOMBERG_PIN=%s" % pin, template, flags=re.M)
    if n == 0:
        raise ValueError("il template non ha la riga BELLOMBERG_PIN=: non e' il .env.example atteso")
    return nuovo


def righe_valorizzate(testo):
    return len(re.findall(r"^[A-Z_][A-Z0-9_]*=\S", testo, flags=re.M))


def controlla_porta(porta):
    if int(porta) == PORTA_VIETATA:
        raise ValueError("porta %d RIFIUTATA: e' quella dell'app in uso (README); il collaudo "
                         "non la tocca mai — scegli un'altra con --porta" % PORTA_VIETATA)
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", int(porta)))
    except OSError as e:
        raise ValueError("porta %d OCCUPATA (%s): scegline un'altra con --porta" % (porta, e))
    finally:
        s.close()
    return int(porta)


def _valore_inventato(nome):
    n = nome.lower()
    if n == "id" or n.endswith("_id") or n in ("n", "idx", "index", "round", "numero"):
        return str(ID_INVENTATO)
    return SIMBOLO_INVENTATO


def route_sondabili(routes):
    """I GET; un parametro di percorso riceve un valore INVENTATO (e la riga lo dice); una query
    obbligatoria non si inventa: la route si salta dichiarando il parametro."""
    out = []
    for r in routes:
        if "GET" not in r.get("methods", []):
            continue
        pp = r.get("path_params", [])
        url = re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: _valore_inventato(m.group(1)), r["path"])
        qr = r.get("query_required", [])
        out.append({"path": r["path"], "url": url, "inventato": bool(pp),
                    "salta": ("query obbligatoria: " + ", ".join(qr)) if qr else None})
    return out


def riepilogo(esiti):
    conto = {b: 0 for b in BUCKETS}
    for e in esiti:
        conto[e["bucket"]] = conto.get(e["bucket"], 0) + 1
    return conto


FASI = ("pip", "senza_pin", "login", "sonda", "flusso", "suite")


def verdetto(fasi):
    """(codice, motivi). 1 se un guasto e' misurato; altrimenti 2 se una fase manca; altrimenti 0."""
    guasti, non_eseguite, note = [], [], []
    for nome in FASI:
        if fasi.get(nome) is None:
            non_eseguite.append("fase %s NON ESEGUITA" % nome)
    pip = fasi.get("pip")
    if pip is not None:
        if pip.get("rc", 1) != 0:
            guasti.append("pip install FALLITO (rc %s)" % pip.get("rc"))
        if pip.get("mancanti"):
            guasti.append("pacchetti MANCANTI dopo pip: %s" % ", ".join(pip["mancanti"]))
    for nome, etichetta in (("senza_pin", "avvio senza PIN"), ("login", "login"),
                            ("flusso", "flusso del primo utente")):
        f = fasi.get(nome)
        if f is not None and not f.get("ok"):
            guasti.append("%s: %s" % (etichetta, f.get("motivo", "KO")))
    sonda = fasi.get("sonda")
    if sonda is not None:
        c = riepilogo(sonda)
        if c["CRASH"]:
            guasti.append("CRASH: %d (5xx)" % c["CRASH"])
        note.append("DATO: %d · DICHIARATO: %d · N.A.: %d" % (c["DATO"], c["DICHIARATO"], c["N.A."]))
        note.append("VUOTO: %d (200 senza dato ne' avviso: osservazione, non ferma)" % c["VUOTO"])
        note.append("TIMEOUT: %d (osservazione, non ferma)" % c["TIMEOUT"])
    suite = fasi.get("suite")
    if suite is not None and suite.get("rc", 1) != 0:
        guasti.append("suite nel venv ROSSA (rc %s): %s" % (suite.get("rc"), suite.get("riga", "")))
    codice = 1 if guasti else (2 if non_eseguite else 0)
    return codice, guasti + non_eseguite + note


def rapporto_md(fasi, verdetto):
    codice, motivi = verdetto
    r = ["# Collaudo dello sconosciuto", "",
         "Verdetto: **exit %d**" % codice, ""]
    r += ["- %s" % m for m in motivi]
    r += ["", "## Fasi", "", "| fase | esito |", "|---|---|"]
    for nome in FASI + ("route", "clone"):
        f = fasi.get(nome)
        if nome == "sonda":
            f = riepilogo(f) if f is not None else None
        r.append("| %s | %s |" % (nome, "NON ESEGUITA" if f is None
                                  else _tronca(f, 300).replace("|", "/")))
    sonda = fasi.get("sonda") or []
    if sonda:
        c = riepilogo(sonda)
        r += ["", "## Sonda: %d route — %s" % (len(sonda), " · ".join("%s: %d" % (b, c[b]) for b in BUCKETS)),
              "", "| metodo | route | status | esito | s | nota |", "|---|---|---|---|---|---|"]
        for e in sonda:
            nota = e.get("motivo", "")
            if e.get("url") and e.get("url") != e.get("path"):
                nota = ("%s ← %s" % (nota, e["url"])).strip(" ←")
            r.append("| %s | %s | %s | %s | %.2f | %s |" % (
                e.get("metodo", "GET"), e.get("path", ""), e.get("status", ""), e["bucket"],
                e.get("secondi", 0.0), nota.replace("|", "/").replace("\n", " ")))
    return "\n".join(r) + "\n"


MARCATORE = ".collaudo_sconosciuto.json"
SCRITTI_DAL_COLLAUDO = (".env", "portfolio.json", "data")


def forma(body, max_chiavi=12):
    """La FORMA del corpo, mai i valori: chiavi con tipo e dimensione. Serve a leggere a mano un
    200 senza avviso: `items:list(0) count:int` e' DATO per il classificatore (0 e' un numero),
    ma per chi guarda e' una fonte che tace."""
    def _t(v):
        if v is None:
            return "null"
        if isinstance(v, (dict, list, str)):
            return "%s(%d)" % (type(v).__name__, len(v))
        return type(v).__name__
    if isinstance(body, dict):
        chiavi = list(body.keys())
        pezzi = ["%s:%s" % (k, _t(body[k])) for k in chiavi[:max_chiavi]]
        if len(chiavi) > max_chiavi:
            pezzi.append("... (+%d)" % (len(chiavi) - max_chiavi))
        return " ".join(pezzi)
    return _t(body)


def inizializza_come_clone(tree, escludi):
    """Lo sconosciuto CLONA: ha `.git` e i file sono tracciati. Il tree esportato no, e cinque
    batterie li enumerano con `git ls-files` (misurato il 05/09: 5 rossi per questo). Se `.git`
    manca: init + esclusioni locali (mai tracciate) + add + commit. Se c'e', non si tocca: e' un
    clone vero. Restituisce (creato, file tracciati)."""
    git = os.path.join(tree, ".git")
    creato = not os.path.exists(git)
    if creato:
        _run(["git", "init", "-q"], tree, timeout=60)
        os.makedirs(os.path.join(git, "info"), exist_ok=True)
        with open(os.path.join(git, "info", "exclude"), "a", encoding="utf-8") as f:
            f.write("".join("%s\n" % e for e in list(escludi) + [MARCATORE]))
        _run(["git", "-c", "user.name=collaudo", "-c", "user.email=collaudo@example.invalid",
              "add", "-A"], tree, timeout=300)
        _run(["git", "-c", "user.name=collaudo", "-c", "user.email=collaudo@example.invalid",
              "commit", "-q", "-m", "tree del collaudo dello sconosciuto"], tree, timeout=300)
    r, _ = _run(["git", "ls-files"], tree, timeout=120)
    return creato, len((r.stdout or "").split())


def scrivi_marcatore(tree):
    with open(os.path.join(tree, MARCATORE), "w", encoding="utf-8") as f:
        json.dump({"scritti": list(SCRITTI_DAL_COLLAUDO), "quando": time.strftime("%Y-%m-%d %H:%M:%S")}, f)


def pulisci_per_riuso(tree):
    """Rimuove SOLO cio' che il collaudo ha scritto lui, e lo sa dal marcatore: senza marcatore
    non e' il suo tree e non tocca niente (una copia non e' una sandbox)."""
    if not os.path.exists(os.path.join(tree, MARCATORE)):
        raise RuntimeError("--riusa rifiutato: nel tree manca il marcatore %s, quindi .env/"
                           "portfolio.json/data non li ha scritti il collaudo" % MARCATORE)
    rimossi = []
    for nome in SCRITTI_DAL_COLLAUDO:
        p = os.path.join(tree, nome)
        if os.path.isdir(p):
            shutil.rmtree(p)
            rimossi.append(nome)
        elif os.path.exists(p):
            os.remove(p)
            rimossi.append(nome)
    return rimossi


def nomi_requirements(testo):
    nomi = []
    for riga in testo.splitlines():
        riga = riga.split("#", 1)[0].strip()
        if not riga:
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", riga)
        if m:
            nomi.append(m.group(1))
    return nomi


def _norma(nome):
    return re.sub(r"[-_.]+", "-", nome.strip().lower())


def pacchetti_mancanti(nomi, elenco_pip):
    installati = {_norma(e.get("name", "")) for e in elenco_pip}
    return [n for n in nomi if _norma(n) not in installati]


# ============================================================================
# LE FASI (rete locale, processi: NON provate dalla batteria, misurate dal collaudo)
# ============================================================================
def _stampa(msg):
    print(msg, flush=True)


def _run(cmd, cwd, env=None, timeout=None, log=None):
    t0 = time.monotonic()
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write("$ %s\n%s\n%s\n" % (" ".join(map(str, cmd)), r.stdout or "", r.stderr or ""))
    return r, time.monotonic() - t0


def python_del_venv(venv):
    win = os.path.join(venv, "Scripts", "python.exe")
    return win if os.name == "nt" else os.path.join(venv, "bin", "python")


def esporta(anche_sporco=False):
    """Il tree dello sconosciuto: `export_pubblico.py --tieni --senza-suite` (la suite la lancia il
    collaudo nel VENV, non l'export con l'interprete di casa). Restituisce (temp, righe stdout)."""
    qui = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(os.path.dirname(qui), "release", "export_pubblico.py")
    if not os.path.exists(script):
        raise RuntimeError("tools/release/export_pubblico.py assente in %s: questo e' gia' un tree "
                           "esportato o un clone — usa --tree" % qui)
    cmd = [sys.executable, script, "--tieni", "--senza-suite"] + (["--anche-sporco"] if anche_sporco else [])
    r, sec = _run(cmd, cwd=os.path.dirname(os.path.dirname(qui)), timeout=1800)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"temp conservata: (.+)", out)
    if r.returncode != 0 or not m:
        raise RuntimeError("export rc %d in %.0f s — non e' il tree dello sconosciuto:\n%s"
                           % (r.returncode, sec, out[-2000:]))
    return m.group(1).strip(), out.strip().splitlines(), sec


def controlla_tree(tree):
    """Il tree deve avere cio' che il README promette e NON deve avere cio' che il collaudo scrive."""
    for nome in ("requirements.txt", "pyproject.toml", ".env.example", "src/bellomberg/resources/examples/portfolio.example.json", "src/bellomberg/api/bellomberg_api.py", "tests"):
        if not os.path.exists(os.path.join(tree, nome)):
            raise RuntimeError("nel tree manca %s: non e' il repo che il README descrive" % nome)
    presenti = [n for n in (".env", "portfolio.json", "data") if os.path.exists(os.path.join(tree, n))]
    if presenti:
        raise RuntimeError("nel tree esistono gia' %s: il collaudo li SCRIVE e non sovrascrive quelli "
                           "di nessuno — usalo su un clone pulito" % ", ".join(presenti))


def fase_pip(tree, venv, senza_pip, log):
    """venv + pip install -r requirements.txt + pytest. Contatori: rc, secondi, installati, mancanti."""
    py = python_del_venv(venv)
    esito = {"rc": None, "secondi_venv": 0.0, "secondi_pip": 0.0, "installati": 0, "mancanti": [],
             "python": None, "venv": venv}
    if not senza_pip:
        r, esito["secondi_venv"] = _run([sys.executable, "-m", "venv", venv], cwd=tree, timeout=600, log=log)
        if r.returncode != 0 or not os.path.exists(py):
            esito["rc"] = r.returncode or 1
            esito["errore"] = "venv non creato: %s" % (r.stderr or r.stdout)[-500:]
            return esito
        r, esito["secondi_pip"] = _run([py, "-m", "pip", "install", "-q", ".", "pytest"],
                                       cwd=tree, timeout=3600, log=log)
        esito["rc"] = r.returncode
        if r.returncode != 0:
            esito["errore"] = (r.stderr or r.stdout)[-1500:]
    else:
        esito["rc"] = 0 if os.path.exists(py) else 1
    r, _ = _run([py, "--version"], cwd=tree, timeout=60)
    esito["python"] = (r.stdout or r.stderr or "").strip()
    r, _ = _run([py, "-m", "pip", "list", "--format=json"], cwd=tree, timeout=300)
    try:
        elenco = json.loads(r.stdout or "[]")
    except ValueError:
        elenco = []
    esito["installati"] = len(elenco)
    with open(os.path.join(tree, "requirements.txt"), encoding="utf-8") as f:
        nomi = nomi_requirements(f.read())
    esito["mancanti"] = pacchetti_mancanti(nomi + ["pytest"], elenco)
    esito["richiesti"] = len(nomi)
    return esito


def elenca_route(tree, py, env, log):
    r, sec = _run([py, "-c", PROGRAMMA_ROUTE], cwd=tree, env=env, timeout=600, log=log)
    if r.returncode != 0:
        raise RuntimeError("import dell'app fallito nel venv (rc %d, %.0f s): %s"
                           % (r.returncode, sec, (r.stderr or r.stdout)[-1500:]))
    righe = [l for l in (r.stdout or "").splitlines() if l.startswith("[")]
    if not righe:
        raise RuntimeError("l'elenco delle route non e' arrivato: %s" % (r.stdout or "")[-500:])
    return json.loads(righe[-1]), sec


def richiesta(porta, metodo, url, corpo=None, token=None, timeout=30):
    import urllib.error
    import urllib.request
    dati, headers = None, {"Accept": "application/json"}
    if corpo is not None:
        dati = json.dumps(corpo).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-BB-Token"] = token
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (porta, url), data=dati,
                                 method=metodo, headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"status": None, "body": None, "errore": "%s: %s" % (type(e).__name__, e),
                "secondi": time.monotonic() - t0}
    testo = raw.decode("utf-8", "replace")
    try:
        body = json.loads(testo) if testo else None
    except ValueError:
        body = testo
    return {"status": status, "body": body, "errore": None, "secondi": time.monotonic() - t0}


class Backend:
    """Il backend nel venv, cwd = tree, ambiente ripulito, porta del collaudo; stdout su file."""

    def __init__(self, tree, py, env, porta, log):
        self.tree, self.py, self.porta, self.log = tree, py, porta, log
        self.env = dict(env, COLLAUDO_PORTA=str(porta))
        self.proc = None
        self.secondi_avvio = None

    def avvia(self, timeout=180):
        self._f = open(self.log, "a", encoding="utf-8")
        self._f.write("\n===== avvio backend porta %d =====\n" % self.porta)
        self._f.flush()
        t0 = time.monotonic()
        self.proc = subprocess.Popen([self.py, "-u", "-c", PROGRAMMA_SERVER], cwd=self.tree, env=self.env,
                                     stdout=self._f, stderr=subprocess.STDOUT)
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError("il backend e' USCITO (rc %s) prima di rispondere: %s"
                                   % (self.proc.returncode, self.coda_log()))
            r = richiesta(self.porta, "GET", "/health", timeout=5)
            if r["status"] == 200:
                self.secondi_avvio = time.monotonic() - t0
                return self.secondi_avvio
            time.sleep(0.5)
        self.ferma()
        raise RuntimeError("il backend non ha risposto a /health in %d s: %s" % (timeout, self.coda_log()))

    def coda_log(self, n=15):
        try:
            self._f.flush()
            with open(self.log, encoding="utf-8", errors="replace") as f:
                return " | ".join(f.read().splitlines()[-n:])
        except OSError as e:
            return "log illeggibile: %s" % e

    def ferma(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        if getattr(self, "_f", None):
            self._f.close()
            self._f = None


def scrivi_env(tree, pin):
    with open(os.path.join(tree, ".env.example"), encoding="utf-8") as f:
        template = f.read()
    with open(os.path.join(tree, ".env"), "w", encoding="utf-8") as f:
        f.write(testo_env(template, pin))


def fase_senza_pin(porta, timeout):
    """`.env` verbatim dal template: il PIN e' vuoto e il login deve dirlo (503), non aprire."""
    st = richiesta(porta, "GET", "/auth/status", timeout=timeout)
    lg = richiesta(porta, "POST", "/auth/login", {"pin": PIN_COLLAUDO}, timeout=timeout)
    ok = (st["status"] == 200 and isinstance(st["body"], dict) and st["body"].get("misconfigured") is True
          and lg["status"] == 503)
    motivo = "auth/status %s misconfigured=%s · login %s %s" % (
        st["status"], (st["body"] or {}).get("misconfigured") if isinstance(st["body"], dict) else "?",
        lg["status"], _testo_detail(lg["body"]))
    return {"ok": ok, "motivo": motivo}


def fase_login(porta, timeout):
    sbagliato = richiesta(porta, "POST", "/auth/login", {"pin": "0000"}, timeout=timeout)
    giusto = richiesta(porta, "POST", "/auth/login", {"pin": PIN_COLLAUDO}, timeout=timeout)
    token = giusto["body"].get("token") if isinstance(giusto["body"], dict) else None
    ok = sbagliato["status"] == 401 and giusto["status"] == 200 and bool(token)
    motivo = "PIN sbagliato %s · PIN giusto %s token=%s" % (sbagliato["status"], giusto["status"], bool(token))
    return {"ok": ok, "motivo": motivo, "token": token}


def fase_sonda(porta, token, route, timeout):
    esiti = []
    for r in route_sondabili(route):
        if r["salta"]:
            esiti.append({"metodo": "GET", "path": r["path"], "url": r["url"], "status": "",
                          "bucket": "N.A.", "motivo": r["salta"], "secondi": 0.0})
            continue
        risp = richiesta(porta, "GET", r["url"], token=token, timeout=timeout)
        c = classifica(risp["status"], risp["body"], risp["errore"])
        motivo = c["motivo"]
        if c["bucket"] in ("DATO", "VUOTO"):     # la forma, per giudicare a mano un 200 senza avviso
            motivo = "%sforma: %s" % ((motivo + " · ") if motivo else "", forma(risp["body"]))
        esiti.append({"metodo": "GET", "path": r["path"], "url": r["url"], "status": risp["status"],
                      "bucket": c["bucket"], "motivo": motivo, "secondi": risp["secondi"]})
        _stampa("  %-9s %-4s %6.2fs GET %s %s" % (c["bucket"], risp["status"], risp["secondi"], r["url"],
                                                  ("— " + motivo) if motivo else ""))
    return esiti


def fase_flusso(porta, token, tree, timeout):
    """VERSAMENTO, BUY di un simbolo inventato, la posizione in portafoglio, la cassa aggiornata."""
    passi, ok = [], True

    def passo(nome, risp, condizione, dettaglio=""):
        nonlocal ok
        c = classifica(risp["status"], risp["body"], risp["errore"])
        bene = condizione and c["bucket"] not in ("CRASH", "TIMEOUT")
        ok = ok and bene
        passi.append("%s: %s %s%s" % (nome, "OK" if bene else "KO", risp["status"],
                                      (" " + dettaglio) if dettaglio else (" " + c["motivo"] if not bene else "")))

    v = richiesta(porta, "POST", "/cash/movement", {"tipo": "DEPOSIT", "importo_eur": VERSAMENTO_EUR,
                                                     "nota": "collaudo"}, token=token, timeout=timeout)
    passo("versamento", v, v["status"] == 200)
    b = richiesta(porta, "POST", "/trade", {"ticker": SIMBOLO_INVENTATO, "action": "BUY",
                                            "quantita": QUANTITA_INVENTATA, "prezzo": PREZZO_INVENTATO,
                                            "valuta": "EUR", "note": "collaudo"}, token=token, timeout=timeout)
    passo("buy", b, b["status"] == 200)
    p = richiesta(porta, "GET", "/portfolio", token=token, timeout=max(timeout, 60))
    testo = json.dumps(p["body"], default=str) if p["body"] is not None else ""
    passo("portafoglio", p, p["status"] == 200 and SIMBOLO_INVENTATO in testo,
          "posizione %s %s" % (SIMBOLO_INVENTATO, "presente" if SIMBOLO_INVENTATO in testo else "ASSENTE"))
    attesa = VERSAMENTO_EUR - QUANTITA_INVENTATA * PREZZO_INVENTATO
    try:
        with open(os.path.join(tree, "portfolio.json"), encoding="utf-8") as f:
            cassa = float(json.load(f).get("cash_disponibile_eur"))
    except (OSError, ValueError, TypeError) as e:
        cassa = None
        passi.append("cassa: KO portfolio.json illeggibile (%s)" % e)
        ok = False
    if cassa is not None:
        bene = abs(cassa - attesa) < 0.005
        ok = ok and bene
        passi.append("cassa: %s %.2f (attesa %.2f)" % ("OK" if bene else "KO", cassa, attesa))
    return {"ok": ok, "motivo": " · ".join(passi), "passi": passi,
            "portafoglio": "%s · forma: %s" % (classifica(p["status"], p["body"], p["errore"])["motivo"]
                                              or "nessuna dichiarazione", forma(p["body"]))}


def fase_suite(tree, py, env, log, timeout):
    r, sec = _run([py, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"], cwd=tree, env=env,
                  timeout=timeout, log=log)
    righe = [l for l in (r.stdout or "").splitlines() if l.strip()]
    return {"rc": r.returncode, "secondi": sec, "riga": righe[-1] if righe else (r.stderr or "")[-300:]}


# ============================================================================
def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", default=None, help="tree gia' pronto (un clone); senza: export in temp")
    ap.add_argument("--venv", default=None, help="cartella del venv (default: <tree>/.venv-collaudo)")
    ap.add_argument("--senza-pip", action="store_true", help="riusa il venv senza reinstallare")
    ap.add_argument("--porta", type=int, default=8766)
    ap.add_argument("--rapporto", default=None, help="scrive il rapporto markdown qui")
    ap.add_argument("--senza-suite", action="store_true", help="salta pytest nel venv (fase NON ESEGUITA)")
    ap.add_argument("--timeout-richiesta", type=int, default=30)
    ap.add_argument("--timeout-suite", type=int, default=2400)
    ap.add_argument("--anche-sporco", action="store_true", help="passato all'export (dichiarato)")
    ap.add_argument("--tieni", action="store_true", help="non cancella la cartella dati temporanea")
    ap.add_argument("--riusa", action="store_true",
                    help="rimuove .env/portfolio.json/data scritti da un giro precedente (solo col marcatore)")
    a = ap.parse_args(argv)

    fasi = {n: None for n in FASI}
    t_inizio = time.monotonic()
    try:
        porta = controlla_porta(a.porta)
        if a.tree:
            tree, righe_export = os.path.abspath(a.tree), ["tree dato: %s" % a.tree]
        else:
            tree, righe_export, sec = esporta(a.anche_sporco)
            righe_export.append("export in %.0f s" % sec)
        _stampa("\n".join(righe_export))
        if a.riusa:
            _stampa("riuso: rimossi %s" % (", ".join(pulisci_per_riuso(tree)) or "niente"))
        controlla_tree(tree)
        venv = os.path.abspath(a.venv) if a.venv else os.path.join(tree, ".venv-collaudo")
        creato, tracciati = inizializza_come_clone(tree, escludi=[os.path.basename(venv) + "/"])
        fasi["clone"] = {"git_creato": creato, "tracciati": tracciati}
        _stampa("git: %s, %d file tracciati" % ("inizializzato come un clone (il tree esportato non ha .git)"
                                                 if creato else "gia' presente, non toccato", tracciati))
        scrivi_marcatore(tree)
    except (ValueError, RuntimeError) as e:
        _stampa("KO: %s" % e)
        return 2

    dati = tempfile.mkdtemp(prefix="bellomberg_collaudo_dati_")
    log = os.path.join(dati, "collaudo.log")
    env, scartate = ambiente_pulito(os.environ, data_dir=dati)
    env["BELLOMBERG_PROJECT_ROOT"] = os.path.abspath(tree)
    chiavi_scartate = sorted(k for k in scartate if re.search(r"KEY|TOKEN|SECRET|PASSWORD|PIN|BELLOMBERG", k, re.I))
    _stampa("tree: %s\nvenv: %s\ndati: %s\nambiente: %d variabili tenute, %d scartate (fra cui: %s)"
            % (tree, venv, dati, len(env), len(scartate), ", ".join(chiavi_scartate) or "nessuna chiave"))

    py = python_del_venv(venv)
    _stampa("\n[1/6] venv + pip install -r requirements.txt pytest ...")
    fasi["pip"] = fase_pip(tree, venv, a.senza_pip, log)
    _stampa("  rc %s · venv %.0f s · pip %.0f s · %s · installati %d · richiesti %d · mancanti: %s"
            % (fasi["pip"]["rc"], fasi["pip"]["secondi_venv"], fasi["pip"]["secondi_pip"],
               fasi["pip"]["python"], fasi["pip"]["installati"], fasi["pip"].get("richiesti", 0),
               ", ".join(fasi["pip"]["mancanti"]) or "nessuno"))
    if fasi["pip"].get("errore"):
        _stampa("  " + fasi["pip"]["errore"].replace("\n", "\n  "))

    backend = None
    try:
        if fasi["pip"]["rc"] == 0:
            _stampa("\n[2/6] route dell'app (import nel venv) ...")
            try:
                route, sec = elenca_route(tree, py, env, log)
                fasi["route"] = {"n": len(route), "get": sum(1 for r in route if "GET" in r["methods"]),
                                 "secondi_import": round(sec, 1)}
                _stampa("  %d route, %d GET, import %.1f s" % (len(route), fasi["route"]["get"], sec))
            except RuntimeError as e:
                route = None
                fasi["route"] = {"errore": str(e)}
                _stampa("  KO: %s" % e)

            _stampa("\n[3/6] .env verbatim dal template (PIN vuoto): il login deve dirlo ...")
            scrivi_env(tree, None)
            shutil.copyfile(os.path.join(tree, "src/bellomberg/resources/examples/portfolio.example.json"), os.path.join(tree, "portfolio.json"))
            backend = Backend(tree, py, env, porta, log)
            try:
                sec = backend.avvia()
                fasi["senza_pin"] = fase_senza_pin(porta, a.timeout_richiesta)
                fasi["senza_pin"]["secondi_avvio"] = round(sec, 1)
                _stampa("  avvio %.1f s · %s · %s" % (sec, "OK" if fasi["senza_pin"]["ok"] else "KO",
                                                      fasi["senza_pin"]["motivo"]))
            except RuntimeError as e:
                fasi["senza_pin"] = {"ok": False, "motivo": str(e)}
                _stampa("  KO: %s" % e)
            finally:
                backend.ferma()

            _stampa("\n[4/6] .env col PIN: login, poi ogni GET senza chiavi a book vuoto ...")
            scrivi_env(tree, PIN_COLLAUDO)
            backend = Backend(tree, py, env, porta, log)
            try:
                sec = backend.avvia()
                fasi["login"] = fase_login(porta, a.timeout_richiesta)
                fasi["login"]["secondi_avvio"] = round(sec, 1)
                _stampa("  avvio %.1f s · %s · %s" % (sec, "OK" if fasi["login"]["ok"] else "KO",
                                                      fasi["login"]["motivo"]))
                token = fasi["login"].pop("token", None)
                if route is not None and token:
                    fasi["sonda"] = fase_sonda(porta, token, route, a.timeout_richiesta)
                    c = riepilogo(fasi["sonda"])
                    _stampa("  sonda: %d route — %s" % (len(fasi["sonda"]),
                                                        " · ".join("%s %d" % (b, c[b]) for b in BUCKETS)))
                    _stampa("\n[5/6] il primo utente: VERSAMENTO, BUY %s, portafoglio, cassa ..." % SIMBOLO_INVENTATO)
                    fasi["flusso"] = fase_flusso(porta, token, tree, a.timeout_richiesta)
                    _stampa("  %s · %s" % ("OK" if fasi["flusso"]["ok"] else "KO", fasi["flusso"]["motivo"]))
                    _stampa("  /portfolio: %s" % fasi["flusso"]["portafoglio"])
            except RuntimeError as e:
                fasi["login"] = {"ok": False, "motivo": str(e)}
                _stampa("  KO: %s" % e)
            finally:
                backend.ferma()

            if not a.senza_suite:
                _stampa("\n[6/6] pytest tests/ nel venv (solo requirements.txt), senza cartella dati ...")
                env_suite, _ = ambiente_pulito(os.environ, data_dir=None)
                try:
                    fasi["suite"] = fase_suite(tree, py, env_suite, log, a.timeout_suite)
                    _stampa("  rc %s · %.0f s · %s" % (fasi["suite"]["rc"], fasi["suite"]["secondi"],
                                                       fasi["suite"]["riga"]))
                except subprocess.TimeoutExpired:
                    fasi["suite"] = {"rc": 1, "riga": "TIMEOUT dopo %d s" % a.timeout_suite}
                    _stampa("  KO: " + fasi["suite"]["riga"])
            else:
                _stampa("\n[6/6] suite NON ESEGUITA (--senza-suite)")
    finally:
        if backend:
            backend.ferma()

    v = verdetto(fasi)
    testo = rapporto_md(fasi, v)
    testo += "\nDurata totale: %.0f s · log: %s · tree: %s\n" % (time.monotonic() - t_inizio, log, tree)
    if a.rapporto:
        with open(a.rapporto, "w", encoding="utf-8") as f:
            f.write(testo)
        _stampa("\nrapporto scritto: %s" % a.rapporto)
    _stampa("\n" + "\n".join("- " + m for m in v[1]))
    _stampa("VERDETTO: exit %d · scritti nel tree: .env, portfolio.json, %s, %s%s · dati temp: %s%s"
            % (v[0], os.path.basename(venv), MARCATORE,
               ", .git" if fasi.get("clone", {}).get("git_creato") else "", dati,
               " (conservata)" if a.tieni or a.rapporto is None else ""))
    if not a.tieni and a.rapporto:
        shutil.rmtree(dati, ignore_errors=True)
    return v[0]


if __name__ == "__main__":
    sys.exit(main())
