# -*- coding: utf-8 -*-
"""I .bat schedulati misurati sui BYTE: CRLF puri e niente junction.

AutoBackup P1 (12/08): fine-riga LF-only rompevano i goto di cmd e il task
usciva 1/255 su backup RIUSCITI. La regola .gitattributes (*.bat text
eol=crlf) protegge solo checkout e clone futuri: NON fa sporcare `git
status` se uno strumento POSIX riscrive in LF la working tree (il
clean-filter normalizza entrambi a LF nell'index). L'unico guardiano
onesto e' contare i byte del file che lo scheduler esegue davvero.
"""
import os

import pytest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

BAT_SCHEDULATI = [
    "run_db_backup.bat",
    "run_briefing_v2.bat",
    "run_news_feed.bat",
    "run_price_updater.bat",
    "run_consigliere_scheduled.bat",
]


def _leggi(nome):
    with open(os.path.join(ROOT, "tools", "ops", "windows", nome), "rb") as f:
        return f.read()


@pytest.mark.parametrize("nome", BAT_SCHEDULATI)
def test_bat_crlf_puri(nome):
    raw = _leggi(nome)
    cr = raw.count(b"\r")
    lf = raw.count(b"\n")
    crlf = raw.count(b"\r\n")
    assert lf > 0, f"{nome}: file senza righe?"
    assert cr == lf == crlf, (
        f"{nome}: fine-riga misti o LF nudi (CR={cr}, LF={lf}, CRLF={crlf}) - "
        "cmd non trova le label dei goto e l'esito del task torna una bugia")


@pytest.mark.parametrize("nome", BAT_SCHEDULATI)
def test_bat_senza_bom(nome):
    raw = _leggi(nome)
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        f"{nome}: BOM UTF-8 in testa - cmd non riconosce piu' @echo off")


@pytest.mark.parametrize("nome", BAT_SCHEDULATI)
def test_bat_senza_junction(nome):
    raw = _leggi(nome)
    assert b"C:\\Bellomberg" not in raw, (
        f"{nome}: percorso junction C:\\Bellomberg riapparso - dal 02/09 (B8) i .bat "
        "si ancorano a %~dp0: un percorso cablato e' una regressione")


# ---------------------------------------------------------------------------
# 12/09: il lanciatore SENZA FINESTRA dei 5 task. L'azione del task diventa
# `wscript.exe //B //Nologo run_hidden.vbs "<bat>"`: cmd.exe e' un programma a
# console e sul desktop interattivo Windows gli apre una finestra visibile;
# WScript.Shell.Run(cmd, 0, True) la nasconde, aspetta e ritorna l'exit code.
# Stessi vincoli dei .bat: CRLF (.gitattributes: *.vbs text eol=crlf), niente
# BOM, e SOLO ASCII perche' WSH legge il file come ANSI.
# ---------------------------------------------------------------------------
LANCIATORE_VBS = "run_hidden.vbs"


def test_lanciatore_vbs_crlf_puro_senza_bom_solo_ascii():
    raw = _leggi(LANCIATORE_VBS)
    cr, lf, crlf = raw.count(b"\r"), raw.count(b"\n"), raw.count(b"\r\n")
    assert lf > 0 and cr == lf == crlf, (
        f"{LANCIATORE_VBS}: fine-riga misti o LF nudi (CR={cr}, LF={lf}, CRLF={crlf})")
    assert not raw.startswith(b"\xef\xbb\xbf"), f"{LANCIATORE_VBS}: BOM UTF-8 in testa"
    assert all(b < 128 for b in raw), f"{LANCIATORE_VBS}: byte non ASCII - WSH lo legge come ANSI"
    import re
    codice = [r for r in raw.decode("ascii").splitlines() if not r.lstrip().startswith("'")]
    assert not [r for r in codice if re.search(r"[A-Za-z]:\\", r)], (
        f"{LANCIATORE_VBS}: percorso cablato nel codice - il .bat da lanciare arriva come argomento")


def test_lanciatore_vbs_nasconde_la_finestra_aspetta_e_ritorna_l_exit_code_nel_sorgente():
    testo = _leggi(LANCIATORE_VBS).decode("ascii")
    assert "Option Explicit" in testo
    assert ", 0, True)" in testo, "Run(cmd, 0, True): 0 = finestra nascosta, True = aspetta la fine"
    assert "WScript.Quit rc" in testo, "l'exit code del .bat deve diventare quello di wscript.exe (LastTaskResult)"
    assert "WScript.Arguments.Count <> 1" in testo, "senza esattamente un argomento deve uscire con un codice suo"


@pytest.mark.skipif(os.name != "nt", reason="wscript.exe esiste solo su Windows")
@pytest.mark.parametrize("codice", [0, 42, 255])
def test_lanciatore_vbs_dal_vivo_propaga_l_exit_code_e_la_cwd(tmp_path, codice):
    """Misura, non frase: un .bat sintetico che scrive la sua cwd ed esce col codice
    dato, lanciato ESATTAMENTE come lo lancera' il task (wscript //B //Nologo)."""
    import shutil
    import subprocess
    wscript = shutil.which("wscript.exe") or os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
    if not os.path.exists(wscript):
        pytest.skip(f"wscript.exe non trovato in {wscript}: motore VBScript assente su questa macchina")
    sonda = tmp_path / "sonda.bat"
    sonda.write_bytes(b"@echo off\r\necho CWD=%CD%> \"%~dp0cwd.txt\"\r\nexit /b " + str(codice).encode() + b"\r\n")
    vbs = os.path.join(ROOT, "tools", "ops", "windows", LANCIATORE_VBS)
    esito = subprocess.run([wscript, "//B", "//Nologo", vbs, str(sonda)], cwd=str(tmp_path),
                           capture_output=True, text=True, timeout=60)
    assert esito.returncode == codice, f"exit {esito.returncode} invece di {codice}: {esito.stdout}{esito.stderr}"
    assert (tmp_path / "cwd.txt").read_text().strip() == f"CWD={tmp_path}", "la cwd del task non e' arrivata al .bat"


@pytest.mark.skipif(os.name != "nt", reason="wscript.exe esiste solo su Windows")
def test_lanciatore_vbs_dal_vivo_senza_argomento_esce_2(tmp_path):
    import shutil
    import subprocess
    wscript = shutil.which("wscript.exe")
    if not wscript:
        pytest.skip("wscript.exe non nel PATH: motore VBScript assente su questa macchina")
    vbs = os.path.join(ROOT, "tools", "ops", "windows", LANCIATORE_VBS)
    esito = subprocess.run([wscript, "//B", "//Nologo", vbs], cwd=str(tmp_path),
                           capture_output=True, text=True, timeout=60)
    assert esito.returncode == 2, esito.returncode
