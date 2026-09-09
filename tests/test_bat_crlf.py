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
