"""Runtime state and packaged entrypoints must not depend on the process cwd."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def _external_runtime_snapshot(tmp_path: Path) -> tuple[dict, Path]:
    project = tmp_path / "configured-project"
    project.mkdir()
    (project / ".env").write_text("QUIVER_API_KEY=from-project-dotenv\n", encoding="utf-8")

    code = r"""
import json
from bellomberg.agents import reflection, scorekeeper
from bellomberg.api import bellomberg_api as api
from bellomberg.cli import retro_title_chats
from bellomberg.market_data import esef, fonti_guidance, lettore_trimestrali, news_aggregator, quiver_data, sec_xbrl
from bellomberg.portfolio import portfolio_sectors
from bellomberg.reporting import charts_institutional, charts_quant, charts_rates, pdf_institutional, pdf_report
from bellomberg.valuation import dcf_modeler

print(json.dumps({
    "reflection": reflection.LESSONS_PATH,
    "scorekeeper": scorekeeper.SNAP_PATH,
    "sector_cache": portfolio_sectors.CACHE_PATH,
    "esef_cache": esef.CACHE_DIR,
    "sec_cache": sec_xbrl.CACHE_DIR,
    "guidance": fonti_guidance.PERCORSO_FONTI,
    "news_rate": news_aggregator._NEWS_RATE_PATH,
    "marketaux": news_aggregator._MARKETAUX_STATE_PATH,
    "quarterlies": lettore_trimestrali.TRIMESTRALI_DIR,
    "pdf_report": pdf_report.REPORT_DIR,
    "institutional_pdf": pdf_institutional.REPORT_DIR,
    "institutional_charts": charts_institutional.DIR,
    "quant_charts": charts_quant.CHART_DIR,
    "rates_charts": charts_rates.OUT_DIR,
    "models": dcf_modeler.MODELS_DIR,
    "memo_relative": api._resolve_memo_path("report/legacy.pdf"),
    "valuation_dirs": api._val_dirs(),
    "retro_db": retro_title_chats.DB,
    "retro_backups": retro_title_chats.BACKUP_DIR,
    "retro_titles": retro_title_chats.JSON_TITOLI,
    "quiver_key": quiver_data.QUIVER_KEY,
}))
"""
    env = dict(os.environ)
    env.update({
        "BELLOMBERG_PROJECT_ROOT": str(project),
        "BELLOMBERG_DATA_DIR": "state/data",
        "BELLOMBERG_REPORT_DIR": "outputs/report",
        "BELLOMBERG_RESEARCH_NOTES_DIR": "notes",
        "MPLCONFIGDIR": str(tmp_path / "mpl"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    env.pop("QUIVER_API_KEY", None)
    env.pop("PYTHON_DOTENV_DISABLED", None)  # exercise only the synthetic project .env
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.splitlines()[-1]), project


def test_external_cwd_keeps_mutable_runtime_files_out_of_package(tmp_path):
    snapshot, project = _external_runtime_snapshot(tmp_path)
    data = project / "state" / "data"
    report = project / "outputs" / "report"

    assert snapshot == {
        "reflection": str(data / "reflection_lessons.json"),
        "scorekeeper": str(data / "scorekeeper_snapshot.json"),
        "sector_cache": str(data / "sector_cache.json"),
        "esef_cache": str(data / "xbrl_cache"),
        "sec_cache": str(data / "xbrl_cache"),
        "guidance": str(data / "fonti_guidance.json"),
        "news_rate": str(data / "news_rate_state.json"),
        "marketaux": str(data / "marketaux_state.json"),
        "quarterlies": str(data / "trimestrali"),
        "pdf_report": str(report),
        "institutional_pdf": str(report),
        "institutional_charts": str(report / "inst_charts"),
        "quant_charts": str(report / "quant_charts"),
        "rates_charts": str(report / "rates_charts"),
        "models": str(project / "models"),
        "memo_relative": str(project / "report" / "legacy.pdf"),
        "valuation_dirs": [str(report), str(project / "models")],
        "retro_db": str(data / "consigliere.db"),
        "retro_backups": str(data / "backups"),
        "retro_titles": str(project / "archive" / "prototypes" / "mockup_f3_chat" / "titles_haiku.json"),
        "quiver_key": "from-project-dotenv",
    }


def test_api_spawns_the_packaged_committee_module(tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api as api
    from bellomberg.core import mandato_pm

    class Tasks:
        callback = None

        def add_task(self, callback):
            self.callback = callback

    class Proc:
        returncode = 0

        def wait(self, timeout=None):
            return 0

    calls = []
    tasks = Tasks()
    api.run_state.runs.clear()
    api._CONSIGLIERE_PROCS.clear()
    monkeypatch.setattr(api, "_require_mandato_run", lambda request: None)
    monkeypatch.setattr(api, "throttle", lambda request: None)
    monkeypatch.setattr(api, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(mandato_pm, "carica", lambda: {})
    monkeypatch.setattr(api.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv) or Proc())

    api.trigger_consigliere(tasks, object())
    assert tasks.callback is not None
    tasks.callback()

    assert calls == [[sys.executable, "-u", "-m", "bellomberg.agents.consigliere_multi"]]


def test_api_console_entrypoint_uses_the_packaged_app(monkeypatch):
    import uvicorn
    from bellomberg.cli import entrypoints

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))

    entrypoints.api_main()

    assert calls == [("bellomberg.api.bellomberg_api:app", {
        "host": "127.0.0.1", "port": 8765, "log_level": "info", "reload": False,
    })]


def test_dcf_bake_invokes_the_migrated_tool(tmp_path, monkeypatch):
    from bellomberg.core.paths import PROJECT_ROOT
    from bellomberg.valuation import dcf_engine

    workbook = tmp_path / "model.xlsx"
    workbook.write_bytes(b"synthetic")
    calls = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    # Excel COM is simulated: this checks the migrated tool path even on hosts
    # where the Windows-only process flag is unavailable.
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: calls.append(argv) or Completed())
    monkeypatch.setattr(dcf_engine, "_count_uncached_formulas", lambda path: 0)

    result = dcf_engine._bake_values({"path": str(workbook)})

    assert result["values_baked"] is True
    assert calls[0][calls[0].index("-File") + 1] == str(PROJECT_ROOT / "tools" / "ops" / "bake_xlsx_values.ps1")


def test_committee_first_start_records_failure_when_data_dir_does_not_exist(tmp_path):
    project = tmp_path / "project"
    data = tmp_path / "new-runtime" / "data"
    project.mkdir()
    env = dict(os.environ)
    env.update({
        "BELLOMBERG_PROJECT_ROOT": str(project),
        "BELLOMBERG_DATA_DIR": str(data),
        "BELLOMBERG_REPORT_DIR": str(tmp_path / "report"),
        "BELLOMBERG_RESEARCH_NOTES_DIR": str(tmp_path / "notes"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })

    run = subprocess.run(
        [sys.executable, "-m", "bellomberg.agents.consigliere_multi"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )

    assert run.returncode == 2
    heartbeat = json.loads((data / "current_run.json").read_text(encoding="utf-8"))
    assert heartbeat["running"] is False
    assert heartbeat["message"].startswith("Run TERMINATA con errore")
