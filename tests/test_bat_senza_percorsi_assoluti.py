"""B8 (02/09, pubblicazione): gli script di schedulazione trovano il repo da soli
(%~dp0 nei .bat, $PSScriptRoot nei .ps1). Un percorso assoluto del PC del PM rende
il repo inutilizzabile da chiunque altro. I CRLF dei .bat li misura
tests/test_bat_crlf.py: qui si guarda solo il contenuto.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = ["run_briefing_v2.bat", "run_db_backup.bat", "run_news_feed.bat", "run_price_updater.bat",
          "run_consigliere_scheduled.bat", "install_all_schedulers.ps1", "fix_price_updater.ps1"]
WRAPPER = [s for s in SCRIPT if s.endswith(".bat")]
PYTHON_WRAPPER = {
    "bellomberg_api.py": "bellomberg/api/bellomberg_api.py",
    "briefing_engine.py": "bellomberg/cli/briefing_engine.py",
    "consigliere_multi.py": "bellomberg/agents/consigliere_multi.py",
    "price_updater.py": "bellomberg/cli/price_updater.py",
    "regenerate_memo.py": "bellomberg/cli/regenerate_memo.py",
}
ASSOLUTO = re.compile(r"[A-Za-z]:\\")


def _righe(nome):
    return open(os.path.join(REPO, "tools", "ops", "windows", nome), encoding="utf-8", errors="replace").read().splitlines()


def _righe_root(nome):
    return open(os.path.join(REPO, nome), encoding="utf-8", errors="replace").read().splitlines()


def _commento(riga):
    r = riga.strip()
    return r.upper().startswith("REM") or r.startswith("::") or r.startswith("#") or r.startswith("<#")


def test_nessun_percorso_assoluto_fuori_dai_commenti():
    colpe = []
    for s in SCRIPT:
        for i, riga in enumerate(_righe(s), 1):
            if ASSOLUTO.search(riga) and not _commento(riga):
                colpe.append(f"{s}:{i}: {riga.strip()[:80]}")
    assert not colpe, "\n".join(colpe)


def test_i_bat_si_ancorano_alla_propria_cartella():
    for s in [x for x in SCRIPT if x.endswith(".bat")]:
        testo = "\n".join(_righe(s))
        assert "%~dp0" in testo, s


def test_i_ps1_usano_psscriptroot():
    for s in [x for x in SCRIPT if x.endswith(".ps1")]:
        testo = "\n".join(_righe(s))
        assert "$PSScriptRoot" in testo, s
        assert '$ProjectPath     = "C:' not in testo and '$ProjectPath = "C:' not in testo, s


def test_i_wrapper_root_risolvono_la_junction_e_inoltrano_al_launcher():
    for nome in WRAPPER:
        testo = "\n".join(_righe_root(nome))
        assert "os.path.realpath" in testo and '"%~dp0."' in testo, nome
        assert 'if not defined BELLOMBERG_PROJECT_ROOT set "BELLOMBERG_PROJECT_ROOT=%ROOT%"' in testo, nome
        assert f'call "%ROOT%\\tools\\ops\\windows\\{nome}"' in testo, nome
        assert 'set "RC=%ERRORLEVEL%"' in testo, nome
        assert "endlocal & exit /b %RC%" in testo, nome


@pytest.mark.skipif(os.name != "nt", reason="inoltro .bat eseguibile soltanto da cmd.exe")
@pytest.mark.parametrize("nome", WRAPPER)
def test_i_wrapper_root_preservano_exit_code_su_launcher_sintetico(tmp_path, nome):
    destinazione = tmp_path / "tools" / "ops" / "windows"
    destinazione.mkdir(parents=True)
    wrapper = tmp_path / nome
    wrapper.write_bytes(("\r\n".join(_righe_root(nome)) + "\r\n").encode("utf-8"))
    (destinazione / nome).write_bytes(
        b"@echo off\r\necho PROJECT_ROOT=[%BELLOMBERG_PROJECT_ROOT%]\r\nexit /b 37\r\n")
    env = dict(os.environ)
    env.pop("BELLOMBERG_PROJECT_ROOT", None)
    esito = subprocess.run(["cmd.exe", "/d", "/c", str(wrapper)], cwd=tmp_path,
                           env=env, capture_output=True, text=True, timeout=15)
    assert esito.returncode == 37, esito.stdout + esito.stderr
    assert f"PROJECT_ROOT=[{tmp_path.resolve()}]" in esito.stdout


@pytest.mark.parametrize(("nome", "modulo"), PYTHON_WRAPPER.items())
def test_i_launcher_python_root_impostano_project_root_prima_dell_import(tmp_path, nome, modulo):
    wrapper = tmp_path / nome
    wrapper.write_text(Path(REPO, nome).read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / modulo
    target.parent.mkdir(parents=True, exist_ok=True)
    for parent in (target.parent, *target.parents):
        if parent == tmp_path:
            break
        (parent / "__init__.py").touch()
    target.write_text(
        "import os\nprint('PROJECT_ROOT=[' + os.environ.get('BELLOMBERG_PROJECT_ROOT', '') + ']')\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    env = dict(os.environ, PYTHONPATH=str(tmp_path), PYTHON_DOTENV_DISABLED="1")
    env.pop("BELLOMBERG_PROJECT_ROOT", None)
    esito = subprocess.run([sys.executable, str(wrapper)], cwd=outside, env=env,
                           capture_output=True, text=True, timeout=15)
    assert esito.returncode == 0, esito.stdout + esito.stderr
    assert f"PROJECT_ROOT=[{tmp_path.resolve()}]" in esito.stdout


@pytest.mark.skipif(os.name != "nt", reason="risoluzione .bat eseguibile soltanto da cmd.exe")
def test_backup_separa_warning_da_data_dir_e_azzera_il_valore_ereditato(tmp_path):
    """Esegue soltanto il preambolo del BAT contro un `python.cmd` sintetico.

    Nessun endpoint, DB o scheduler viene toccato: il frammento si ferma prima di creare
    directory. Un warning su stderr non deve diventare il path e DATA_DIR ereditata non
    deve sopravvivere a un output valido diverso.
    """
    sorgente = "\n".join(_righe("run_db_backup.bat"))
    preambolo, separatore, _ = sorgente.partition('set "LOG=%DATA_DIR%\\backup.log"')
    assert separatore, "punto di arresto del preambolo non trovato"
    data_dir = tmp_path / "private O'Brien data"
    bin_dir = tmp_path / "bin"
    launcher_dir = tmp_path / "tools" / "ops" / "windows"
    bin_dir.mkdir()
    launcher_dir.mkdir(parents=True)
    (bin_dir / "python.cmd").write_bytes(
        b"@echo off\r\necho %SYNTH_DATA_DIR%\r\necho warning-sintetico 1>&2\r\nexit /b 0\r\n")
    probe = launcher_dir / "run_db_backup.bat"
    probe.write_bytes((preambolo + "\n" +
                       "echo DATA_DIR=[%DATA_DIR%]\n" +
                       "echo PROJECT_ROOT=[%BELLOMBERG_PROJECT_ROOT%]\n" +
                       "type \"%DATA_DIR_ERR%\"\n" +
                       "endlocal\nexit /b 0\n").replace("\n", "\r\n").encode("utf-8"))
    env = dict(os.environ, DATA_DIR="EREDITATA", SYNTH_DATA_DIR=str(data_dir))
    env.pop("BELLOMBERG_PROJECT_ROOT", None)
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    env[path_key] = str(bin_dir) + os.pathsep + env.get(path_key, "")
    esito = subprocess.run(["cmd.exe", "/d", "/c", str(probe)], cwd=tmp_path,
                           env=env, capture_output=True, text=True, timeout=15)
    assert esito.returncode == 0, esito.stdout + esito.stderr
    assert f"DATA_DIR=[{data_dir}]" in esito.stdout
    assert "warning-sintetico" in esito.stdout
    assert "DATA_DIR=[EREDITATA]" not in esito.stdout
    assert f"PROJECT_ROOT=[{tmp_path.resolve()}]" in esito.stdout
