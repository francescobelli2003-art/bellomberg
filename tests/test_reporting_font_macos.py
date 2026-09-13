# -*- coding: utf-8 -*-
"""I font dei PDF e dei grafici su macOS (recon/mac.md A6 — 12/09): i quattro moduli di
reporting cercano Arial anche dove macOS la tiene (`/System/Library/Fonts/Supplemental`,
`/Library/Fonts`) e, quando NESSUN TTF Arial-like esiste, il ripiego (DejaVu Sans per matplotlib,
Helvetica built-in per reportlab) viene DICHIARATO a log — mai in silenzio (regola 14/07).

Batteria offline: `os.path.exists` e i registri font sono finti; i moduli si ricaricano con la
finzione e poi con il sistema vero, cosi' gli altri test vedono lo stato di prima.
"""
import importlib
import os
import sys

import pytest

MAC_ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
MAC_ARIAL_LIBRARY = "/Library/Fonts/Arial.ttf"
MODULI_MPL = ("bellomberg.reporting.style_terminal", "bellomberg.reporting.charts_institutional",
              "bellomberg.reporting.style_research")


def _ricarica(nome):
    if nome in sys.modules:
        return importlib.reload(sys.modules[nome])
    return importlib.import_module(nome)


@pytest.fixture
def ripristina(monkeypatch):
    """Dopo il test i moduli toccati si ricaricano col filesystem VERO."""
    toccati = []
    yield toccati
    monkeypatch.undo()
    for nome in toccati:
        importlib.reload(sys.modules[nome])


def _finto_exists(*veri):
    veri = {os.path.normpath(v) for v in veri}
    return lambda p: os.path.normpath(str(p)) in veri


@pytest.mark.parametrize("nome", MODULI_MPL)
@pytest.mark.parametrize("percorso", [MAC_ARIAL, MAC_ARIAL_LIBRARY])
def test_matplotlib_trova_arial_dove_la_tiene_macos(nome, percorso, monkeypatch, ripristina):
    from matplotlib import font_manager
    aggiunti = []
    monkeypatch.setattr(font_manager.fontManager, "addfont", lambda p: aggiunti.append(p))
    monkeypatch.setattr(os.path, "exists", _finto_exists(percorso))
    ripristina.append(nome)
    mod = _ricarica(nome)
    assert mod.FONT == "Arial", "%s: FONT=%r" % (nome, mod.FONT)
    assert aggiunti == [percorso]


@pytest.mark.parametrize("nome", MODULI_MPL)
def test_matplotlib_senza_nessun_ttf_dichiara_il_ripiego(nome, monkeypatch, ripristina, capsys):
    from matplotlib import font_manager
    monkeypatch.setattr(font_manager.fontManager, "addfont", lambda p: pytest.fail("addfont con nessun file"))
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    ripristina.append(nome)
    mod = _ricarica(nome)
    assert mod.FONT == "DejaVu Sans"
    err = capsys.readouterr().err
    assert "DejaVu Sans" in err and "ripiego" in err.lower(), err
    assert nome.rsplit(".", 1)[-1] in err, "la riga dice QUALE modulo: %r" % err


@pytest.mark.parametrize("nome", MODULI_MPL)
def test_matplotlib_con_arial_trovata_non_grida_al_ripiego(nome, monkeypatch, ripristina, capsys):
    from matplotlib import font_manager
    monkeypatch.setattr(font_manager.fontManager, "addfont", lambda p: None)
    monkeypatch.setattr(os.path, "exists", _finto_exists(MAC_ARIAL))
    ripristina.append(nome)
    _ricarica(nome)
    assert "ripiego" not in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------------------------
# reportlab (pdf_institutional._register_fonts)
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def pdf_pulito():
    from bellomberg.reporting import pdf_institutional as pi
    stato = (pi._FONT_DONE, getattr(pi, "_FONT_WARNED", None))
    pi._FONT_DONE = False
    if hasattr(pi, "_FONT_WARNED"):
        pi._FONT_WARNED = False
    yield pi
    pi._FONT_DONE = stato[0]
    if stato[1] is not None:
        pi._FONT_WARNED = stato[1]


def test_pdf_registra_arial_dai_percorsi_macos(monkeypatch, pdf_pulito):
    pi = pdf_pulito
    registrati = []
    monkeypatch.setattr(pi, "TTFont", lambda nome, percorso: (nome, percorso))
    monkeypatch.setattr(pi.pdfmetrics, "registerFont", lambda f: registrati.append(f))
    monkeypatch.setattr(os.path, "exists", _finto_exists(MAC_ARIAL))
    assert pi._register_fonts() == ("LS", "LSB", "LSI")
    assert ("LS", MAC_ARIAL) in registrati
    # Bold/Italic assenti sul finto filesystem: si ripiega sul regolare, mai su un file inventato
    assert registrati == [("LS", MAC_ARIAL), ("LSB", MAC_ARIAL), ("LSI", MAC_ARIAL)]


def test_pdf_senza_nessun_ttf_dichiara_helvetica_una_volta(monkeypatch, pdf_pulito, capsys):
    pi = pdf_pulito
    monkeypatch.setattr(pi.pdfmetrics, "registerFont", lambda f: pytest.fail("registerFont con nessun file"))
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    assert pi._register_fonts() == ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")
    err = capsys.readouterr().err
    assert "Helvetica" in err and "ripiego" in err.lower() and "pdf_institutional" in err, err
    pi._register_fonts()
    assert capsys.readouterr().err == "", "la dichiarazione esce una volta, non a ogni PDF"


def test_pdf_con_arial_trovata_non_grida_al_ripiego(monkeypatch, pdf_pulito, capsys):
    pi = pdf_pulito
    monkeypatch.setattr(pi, "TTFont", lambda nome, percorso: (nome, percorso))
    monkeypatch.setattr(pi.pdfmetrics, "registerFont", lambda f: None)
    monkeypatch.setattr(os.path, "exists", _finto_exists(MAC_ARIAL_LIBRARY))
    pi._register_fonts()
    assert capsys.readouterr().err == ""
