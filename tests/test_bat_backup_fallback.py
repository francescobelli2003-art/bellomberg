# -*- coding: utf-8 -*-
"""AutoBackup 255 (11/09): il ramo FALLBACK di run_db_backup.bat eseguito fino in fondo.

Il difetto (dal 21/07, 9 giri su 9 col backend spento): alla riga
`echo ... KO (v. righe sopra) *** >> "%LOG%"` DENTRO il blocco `if %ERRORLEVEL% NEQ 0 ( ... )`
cmd legge la `)` non escapata come chiusura del blocco, il resto della riga e' un errore di
sintassi («*** was unexpected at this time.») e il batch abortisce con 255 SUBITO DOPO il
ritorno di python: lo zip c'era, ma VERIFY e retention non giravano e il task era rosso su
un backup sano. Il ramo backend salta il blocco con un goto e per questo era verde.

Qui il .bat VERO viene copiato in tmp con la sola porta del backend sostituita da una
chiusa, `BELLOMBERG_DATA_DIR` verso un DB fittizio > 1 MB e `%TEMP%` deviato: nessun
endpoint, nessun DB vivo, nessuna cartella del PM. E' la riproduzione (d2) della
ricognizione del 12/09. In coda un lint minimale sui .bat del repo: nessuna `)` nuda che
chiuda un blocco aperto su una riga precedente.
"""
import os
import shutil
import socket
import sqlite3
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAT_INTERNO = os.path.join(REPO, "tools", "ops", "windows", "run_db_backup.bat")
BACKUP_DIRETTO = os.path.join(REPO, "tools", "ops", "db_backup_direct.py")
PORTA_BACKEND = "127.0.0.1:8765"
SOLO_WINDOWS = pytest.mark.skipif(
    os.name != "nt",
    reason="il ramo fallback del backup e' un batch di cmd.exe: fuori da Windows non c'e' nulla da misurare")


def _porta_chiusa():
    """Una porta su cui NESSUNO ascolta: il .bat deve prendere il ramo BACKEND_DOWN."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _catena_in_tmp(tmp_path, porta):
    """Copia la catena vera (bat interno + db_backup_direct.py) sotto tmp_path/tools/ops/windows.
    Il .bat trova ROOT con %~dp0..\\..\\.. = tmp_path, quindi `python tools\\ops\\db_backup_direct.py`
    e' la copia. Nel .bat cambia SOLO la porta del backend."""
    windows = tmp_path / "tools" / "ops" / "windows"
    windows.mkdir(parents=True)
    raw = open(BAT_INTERNO, "rb").read()
    assert raw.count(PORTA_BACKEND.encode()) == 1, "la porta del backend deve comparire una volta sola"
    bat = windows / "run_db_backup.bat"
    bat.write_bytes(raw.replace(PORTA_BACKEND.encode(), f"127.0.0.1:{porta}".encode()))
    shutil.copyfile(BACKUP_DIRETTO, str(tmp_path / "tools" / "ops" / "db_backup_direct.py"))
    return bat


def _db_fittizio(cartella_dati, nome="dummy.db", byte_casuali=1_400_000):
    """Un DB sqlite sano con un blob INCOMPRIMIBILE: lo zip deve restare sopra 1 MB
    perche' VERIFY misura proprio quella soglia."""
    cartella_dati.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(cartella_dati / nome))
    con.execute("CREATE TABLE zavorra (id INTEGER PRIMARY KEY, dati BLOB)")
    con.execute("INSERT INTO zavorra (dati) VALUES (?)", (os.urandom(byte_casuali),))
    con.commit()
    con.close()


def _ambiente(tmp_path):
    env = dict(os.environ)
    env.pop("BELLOMBERG_PROJECT_ROOT", None)
    dati = tmp_path / "data"
    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    env["BELLOMBERG_DATA_DIR"] = str(dati)
    env["TEMP"] = str(temp)
    env["TMP"] = str(temp)
    # `python` del .bat = l'interprete di questa suite (l'editable install di bellomberg
    # e' sua); PYTHONPATH su src/ per il clone senza install.
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    env[path_key] = os.path.dirname(sys.executable) + os.pathsep + env.get(path_key, "")
    env["PYTHONPATH"] = os.path.join(REPO, "src") + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _esegui(bat, tmp_path, env):
    return subprocess.run(["cmd.exe", "/d", "/c", str(bat)], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=180)


def _log(tmp_path):
    return (tmp_path / "data" / "backup.log").read_text(encoding="utf-8", errors="replace")


@SOLO_WINDOWS
def test_ramo_fallback_esce_0_con_verify_ok_e_done(tmp_path):
    """Backend spento, python fa lo zip, quick_check ok: il task deve essere VERDE con
    la misura (VERIFY_OK) e la retention eseguite, e `=== done ===` in coda al log."""
    bat = _catena_in_tmp(tmp_path, _porta_chiusa())
    _db_fittizio(tmp_path / "data")
    esito = _esegui(bat, tmp_path, _ambiente(tmp_path))
    log = _log(tmp_path)
    assert "was unexpected at this time" not in (esito.stdout + esito.stderr), (
        "cmd ha abortito il batch per un errore di sintassi:\n" + esito.stdout + esito.stderr)
    assert esito.returncode == 0, f"exit {esito.returncode}\n--- log ---\n{log}\n--- console ---\n{esito.stdout}{esito.stderr}"
    assert "BACKEND_DOWN" in log, log
    assert "quick_check dummy.db: ok" in log, log
    assert "VERIFY_OK" in log, log
    assert "retention OK" in log or "cleanup: rimossi" in log, log
    assert log.rstrip().endswith("=== done ==="), log
    assert "FALLITO" not in log, log
    zip_prodotti = list((tmp_path / "data" / "backups").glob("bellomberg_backup_*.zip"))
    assert len(zip_prodotti) == 1 and zip_prodotti[0].stat().st_size > 1024 * 1024, zip_prodotti


@SOLO_WINDOWS
def test_ramo_fallback_ko_esce_1_e_scrive_la_riga_di_errore_leggibile(tmp_path):
    """Un DB illeggibile fa fallire quick_check: python esce 1, il .bat deve entrare nel
    blocco d'errore (quello che prima abortiva), scrivere la riga con le parentesi COME LE
    LEGGE IL PM (senza i ^ di escape) e uscire 1 dichiarato, senza VERIFY ne' retention."""
    bat = _catena_in_tmp(tmp_path, _porta_chiusa())
    dati = tmp_path / "data"
    dati.mkdir()
    (dati / "rotto.db").write_bytes(b"questo non e' un database sqlite" * 1000)
    esito = _esegui(bat, tmp_path, _ambiente(tmp_path))
    log = _log(tmp_path)
    assert "was unexpected at this time" not in (esito.stdout + esito.stderr), esito.stdout + esito.stderr
    assert esito.returncode == 1, f"exit {esito.returncode}\n{log}\n{esito.stdout}{esito.stderr}"
    assert "*** ERROR: python fallback failed or quick_check KO (v. righe sopra) ***" in log, log
    assert "^(" not in log and "^)" not in log, log
    assert "VERIFY_OK" not in log and "retention" not in log, log
    assert log.rstrip().endswith("=== done (FALLITO, exit 1) ==="), log


# ---------------------------------------------------------------------------
# Lint minimale: una `)` nuda dentro un blocco aperto su una riga precedente.
# Regola di cmd: dentro `( ... )` ogni `)` non fra virgolette e non escapata con ^
# chiude il blocco, anche in mezzo a un echo. Il lint segue solo cio' che serve a
# questa classe di difetto: virgolette, ^, %var%, separatori & |, e le tre forme che
# APRONO un gruppo (`(` a inizio comando, dopo `in`/`do`/`else`, o dopo la
# condizione di un `if`). Un gruppo aperto e chiuso sulla stessa riga (for-set,
# one-liner) e' libero; uno aperto su una riga precedente si chiude SOLO con una
# `)` come primo carattere della riga.
# ---------------------------------------------------------------------------
_SEPARATORI = ("&", "|")
_PAROLE_APRENTI = ("in", "do", "else")
_CARTELLE_SALTATE = {".git", "node_modules", "nodejs", "data", "data_pre_junction", "mappa",
                     "report", "research_notes", ".playwright-mcp"}


def parentesi_nude_nei_blocchi(testo):
    """[(riga, colonna, riga_di_apertura)] per ogni `)` che chiude un blocco aperto su
    una riga precedente senza essere il primo carattere della propria riga."""
    colpe = []
    aperti = []
    for n, riga in enumerate(testo.splitlines(), 1):
        s = riga.rstrip("\r")
        strip = s.strip()
        su = strip.upper()
        if not strip or su == "REM" or su.startswith("REM ") or strip.startswith("::"):
            continue
        in_quote = False
        comando = ""
        i = 0
        while i < len(s):
            c = s[i]
            if in_quote:
                in_quote = c != '"'
                i += 1
                continue
            if c == '"':
                in_quote = True
                comando += c
                i += 1
                continue
            if c == "^":
                comando += "^" + (s[i + 1] if i + 1 < len(s) else "")
                i += 2
                continue
            if c == "%":
                if i + 1 < len(s) and s[i + 1] == "%":
                    comando += "%%"
                    i += 2
                    continue
                if i + 1 < len(s) and (s[i + 1] == "~" or s[i + 1].isdigit()):
                    comando += "%"
                    i += 1
                    continue
                fine = s.find("%", i + 1)
                if fine == -1:
                    comando += "%"
                    i += 1
                    continue
                comando += "%V%"
                i = fine + 1
                continue
            if c in _SEPARATORI:
                comando = ""
                i += 1
                continue
            if c == "(":
                parole = comando.strip().split()
                apre = (not parole
                        or parole[-1].lower() in _PAROLE_APRENTI
                        or (parole[0].lower() == "if" and "(" not in comando))
                if apre:
                    aperti.append(n)
                    comando = ""
                else:
                    comando += c
                i += 1
                continue
            if c == ")":
                if aperti:
                    riga_apertura = aperti.pop()
                    if riga_apertura != n and strip[0] != ")":
                        colpe.append((n, i + 1, riga_apertura))
                    comando = ""
                else:
                    comando += c
                i += 1
                continue
            comando += c
            i += 1
    return colpe


def _bat_del_repo():
    for cartella, sotto, file in os.walk(REPO):
        sotto[:] = sorted(d for d in sotto if d not in _CARTELLE_SALTATE and not _reparse(os.path.join(cartella, d)))
        for f in sorted(file):
            if f.lower().endswith(".bat"):
                yield os.path.join(cartella, f)


def _reparse(percorso):
    """Junction/symlink: non si entra (la cartella dati del PM e' una junction)."""
    try:
        st = os.stat(percorso, follow_symlinks=False)
    except OSError:
        return True
    attributi = getattr(st, "st_file_attributes", 0)
    return bool(attributi & 0x400) or os.path.islink(percorso)


@pytest.mark.parametrize(("frammento", "atteso"), [
    # il difetto dell'11/09: la `)` di «(v. righe sopra)» chiude il blocco aperto alla riga 1
    ('if %ERRORLEVEL% NEQ 0 (\r\n    echo KO (v. righe sopra) *** >> "%LOG%"\r\n    goto :FAIL\r\n)\r\n',
     [(2, 28, 1)]),
    # la cura: parentesi escapate
    ('if %ERRORLEVEL% NEQ 0 (\r\n    echo KO ^(v. righe sopra^) *** >> "%LOG%"\r\n    goto :FAIL\r\n)\r\n',
     []),
    # parentesi fra virgolette: non sono sintassi
    ('if x (\r\n    echo "KO (v. righe sopra)" >> log\r\n)\r\n', []),
    # one-liner: gruppo aperto e chiuso sulla stessa riga
    ('if x (echo a) else (echo b)\r\n', []),
    # for-set dentro un if, poi blocco aperto: la `)` del for-set e' sulla stessa riga
    ('if exist "%E%" for %%I in ("%E%") do if %%~zI GTR 0 (\r\n    type "%E%" >> log\r\n)\r\n', []),
    # parentesi fuori da ogni blocco (dopo una label): innocue
    (':FAIL\r\necho === done (FALLITO, exit 1) === >> log\r\nexit /b 1\r\n', []),
    # blocco chiuso a meta' riga da una `)` nuda anche senza testo dopo: il resto del
    # blocco resta fuori e la `)` finale diventa orfana — e' la stessa classe
    ('if x (\r\n    echo KO (vedi) >> log\r\n    goto :FAIL\r\n)\r\n', [(2, 18, 1)]),
])
def test_il_lint_riconosce_la_forma_del_difetto(frammento, atteso):
    assert parentesi_nude_nei_blocchi(frammento) == atteso


def test_nessun_bat_del_repo_chiude_un_blocco_con_una_parentesi_nuda():
    trovati = list(_bat_del_repo())
    assert any(p.endswith("run_db_backup.bat") for p in trovati), "il .bat del backup non e' stato trovato: perimetro sbagliato"
    colpe = []
    for p in trovati:
        with open(p, "rb") as fh:
            testo = fh.read().decode("utf-8", errors="replace")
        for riga, colonna, apertura in parentesi_nude_nei_blocchi(testo):
            colpe.append(f"{os.path.relpath(p, REPO)}:{riga}:{colonna} chiude il blocco aperto alla riga {apertura}: "
                         "cmd abortisce con 255 (escapare con ^( e ^) o togliere le parentesi)")
    assert not colpe, "\n".join(colpe)
