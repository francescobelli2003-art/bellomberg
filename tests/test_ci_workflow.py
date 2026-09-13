# -*- coding: utf-8 -*-
"""Il workflow CI (`.github/workflows/ci.yml`) letto come lo legge GitHub: YAML valido, i tre
job attesi (Linux con matrice 3.12+3.14, desktop Windows, sorgente macOS), i passi che il
mandato 12/09 chiede al job macOS, nessun segreto nel file (la ANTHROPIC_API_KEY e' un dummy
DICHIARATO) e il job macOS che non spende minuti a pagamento sul repo privato senza un ordine
esplicito (i runner macOS costano ~10x Linux; gratuiti solo sui repo pubblici).

Batteria OFFLINE: legge un file del repo, non chiama GitHub. `yaml` arriva con chromadb e
uvicorn[standard] (requirements.txt): se manca, il test cade con la causa, non salta.
"""
import os
import re

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(REPO, ".github", "workflows", "ci.yml")


def _testo():
    with open(WORKFLOW, encoding="utf-8") as f:
        return f.read()


def _workflow():
    wf = yaml.safe_load(_testo())
    assert isinstance(wf, dict) and "jobs" in wf, "ci.yml non e' un workflow GitHub leggibile"
    return wf


def _job(nome):
    jobs = _workflow()["jobs"]
    assert nome in jobs, "job %s assente: presenti %s" % (nome, sorted(jobs))
    return jobs[nome]


def _run_di(job):
    """Il testo di tutti i passi `run` del job, concatenato (cio' che il runner esegue)."""
    return "\n".join(str(s.get("run", "")) for s in job.get("steps", []))


def _trigger():
    wf = _workflow()
    # PyYAML (YAML 1.1) legge la chiave `on` come booleano True
    return wf.get("on", wf.get(True))


# ---------------------------------------------------------------------------------------------
# i job e i loro nomi
# ---------------------------------------------------------------------------------------------
def test_i_tre_job_esistono_con_il_loro_nome():
    jobs = _workflow()["jobs"]
    assert {"test", "desktop-windows", "macos-source"} <= set(jobs), sorted(jobs)


def test_il_job_macos_gira_su_un_runner_apple_silicon():
    job = _job("macos-source")
    assert job["runs-on"] == "macos-latest"


def test_il_job_linux_prova_python_312_e_314():
    job = _job("test")
    versioni = [str(v) for v in job["strategy"]["matrix"]["python-version"]]
    assert "3.12" in versioni and "3.14" in versioni, versioni
    setup = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-python")]
    assert setup and setup[0]["with"]["python-version"] == "${{ matrix.python-version }}"


def test_il_job_windows_costruisce_ancora_l_installer_nsis():
    assert "electron-builder --win nsis" in _run_di(_job("desktop-windows"))


# ---------------------------------------------------------------------------------------------
# i passi del job macOS (mandato 12/09, sezione 4)
# ---------------------------------------------------------------------------------------------
def test_il_job_macos_installa_il_pacchetto_e_ne_verifica_la_coerenza():
    run = _run_di(_job("macos-source"))
    assert "pip install -e . pytest" in run
    assert "pip check" in run


def test_il_job_macos_compila_e_lancia_la_suite_offline():
    run = _run_di(_job("macos-source"))
    assert "compileall" in run and "src/bellomberg" in run
    assert re.search(r"pytest tests/? -q", run), run


def test_il_job_macos_costruisce_i_bundle_e_prova_electron():
    job = _job("macos-source")
    run = _run_di(job)
    for comando in ("npm ci", "npm run build:bundles", "npm run test:release",
                    "npm run desktop:install", "npm run test:desktop"):
        assert comando in run, "manca %r nel job macos-source" % comando
    working = {s.get("working-directory") for s in job["steps"] if "npm" in str(s.get("run", ""))}
    assert working == {"app"}, working


def test_il_job_macos_usa_python_312():
    setup = [s for s in _job("macos-source")["steps"]
             if str(s.get("uses", "")).startswith("actions/setup-python")]
    assert setup and str(setup[0]["with"]["python-version"]) == "3.12"


# ---------------------------------------------------------------------------------------------
# nessun segreto, nessuna spesa non dichiarata
# ---------------------------------------------------------------------------------------------
def test_nessun_segreto_nel_workflow():
    testo = _testo()
    assert "secrets." not in testo, "il workflow legge un segreto: la suite e' offline"
    for job in _workflow()["jobs"].values():
        chiave = (job.get("env") or {}).get("ANTHROPIC_API_KEY")
        if chiave is not None:
            assert chiave == "dummy-ci-offline", chiave
    assert not re.search(r"sk-(ant|or)-[A-Za-z0-9_-]{8,}", testo)


def test_il_job_macos_non_spende_minuti_sul_repo_privato_senza_ordine():
    """Runner macOS: gratuiti sui repo pubblici, ~10x Linux sui privati (doc GitHub billing,
    letta il 12/09). Il job gira sui repo pubblici e, sul privato, solo a comando (dispatch)."""
    job = _job("macos-source")
    condizione = str(job.get("if", ""))
    assert "github.event.repository.private" in condizione, condizione
    assert "workflow_dispatch" in condizione, condizione
    assert "workflow_dispatch" in _trigger(), _trigger()


def test_i_job_linux_e_windows_restano_senza_condizione_di_costo():
    assert "if" not in _job("test") and "if" not in _job("desktop-windows")
