# -*- coding: utf-8 -*-
"""Le due funzioni Windows-only per costruzione (Excel COM) su un sistema POSIX (recon/mac.md
A4, A5 — 12/09): il degrado si DICHIARA con la causa vera («solo Windows»), non con un
AttributeError travestito, e niente parte a vuoto. Batteria offline: nessun Excel, nessun
PowerShell, `subprocess.run` sostituito da un finto che conta.
"""
import subprocess
import sys

import pytest

from bellomberg.valuation import dcf_engine


def _workbook(tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "ZZTEST_dcf.xlsx"
    wb = Workbook()
    wb.active["A1"] = "=1+1"
    wb.save(p)
    return str(p)


# ---------------------------------------------------------------------------------------------
# A4: _bake_values (ricalcolo Excel COM dei workbook DCF)
# ---------------------------------------------------------------------------------------------
def test_su_posix_il_bake_dichiara_solo_windows_e_non_lancia_powershell(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    chiamate = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: chiamate.append(a) or pytest.fail("powershell lanciato su POSIX"))
    r = dcf_engine._bake_values({"path": _workbook(tmp_path)})
    assert r["values_baked"] is False
    assert "solo su Windows" in r["bake_error"] and "darwin" in r["bake_error"]
    assert "AttributeError" not in r["bake_error"]
    assert "fullCalcOnLoad" in r["bake_error"], "il modello resta valido: il ricalcolo parte all'apertura"
    assert chiamate == []


def test_su_windows_senza_l_attributo_il_flag_console_vale_zero_e_non_esplode(tmp_path, monkeypatch):
    """`subprocess.CREATE_NO_WINDOW` esiste solo su Windows: il codice lo prende con getattr, cosi'
    la costruzione degli argomenti non e' mai la causa del degrado."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)
    visti = {}

    def finto_run(cmd, **kw):
        visti.update(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", finto_run)
    monkeypatch.setattr(dcf_engine, "_count_uncached_formulas", lambda p: 0)
    r = dcf_engine._bake_values({"path": _workbook(tmp_path)})
    assert visti["creationflags"] == 0
    assert r["values_baked"] is True


def test_su_windows_il_flag_console_resta_quello_di_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    visti = {}

    def finto_run(cmd, **kw):
        visti.update(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", finto_run)
    monkeypatch.setattr(dcf_engine, "_count_uncached_formulas", lambda p: 0)
    dcf_engine._bake_values({"path": _workbook(tmp_path)})
    assert visti["creationflags"] == 0x08000000


# ---------------------------------------------------------------------------------------------
# A5: tools/ops/aggiorna_damodaran.py (conversione .xls via Excel COM)
# ---------------------------------------------------------------------------------------------
def test_aggiorna_damodaran_su_posix_esce_subito_con_il_motivo(monkeypatch):
    from tools.ops import aggiorna_damodaran as ad
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ad, "download", lambda: pytest.fail("download partito su POSIX: lavoro a vuoto"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("powershell lanciato su POSIX"))
    monkeypatch.setattr(sys, "argv", ["aggiorna_damodaran.py"])
    with pytest.raises(SystemExit) as exc:
        ad.main()
    testo = str(exc.value)
    assert "solo Windows" in testo and "COM" in testo and "linux" in testo


def test_aggiorna_damodaran_convert_rifiuta_su_posix_anche_da_import(monkeypatch):
    from tools.ops import aggiorna_damodaran as ad
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("powershell lanciato su POSIX"))
    with pytest.raises(SystemExit, match="solo Windows"):
        ad.convert()
