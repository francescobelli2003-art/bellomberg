"""Canonical paths for the packaged application.

Runtime data never lives inside the installed package. Development keeps the
existing `<project>/data` junction unless BELLOMBERG_DATA_DIR overrides it.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _project_root() -> Path:
    configured = os.environ.get("BELLOMBERG_PROJECT_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "bellomberg").is_dir():
            return parent
    raise RuntimeError(
        "progetto installato senza BELLOMBERG_PROJECT_ROOT: configurare anche BELLOMBERG_DATA_DIR")


PROJECT_ROOT = _project_root()

# Runtime path overrides normally live in the project's private .env. Load that
# exact file before resolving constants; never search from the process cwd.
try:
    from dotenv import load_dotenv as _load_project_dotenv
except ImportError:  # dependency absence is surfaced by modules that require it
    _load_project_dotenv = None
if _load_project_dotenv is not None:
    _load_project_dotenv(PROJECT_ROOT / ".env", override=False)


def _runtime_path(variable: str, default: str) -> Path:
    raw = os.environ.get(variable, "").strip()
    path = Path(raw).expanduser() if raw else PROJECT_ROOT / default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DATA_DIR = _runtime_path("BELLOMBERG_DATA_DIR", "data")
REPORT_DIR = _runtime_path("BELLOMBERG_REPORT_DIR", "report")
RESEARCH_NOTES_DIR = _runtime_path("BELLOMBERG_RESEARCH_NOTES_DIR", "research_notes")
MODELS_DIR = (PROJECT_ROOT / "models").resolve()
SQLITE_PATH = DATA_DIR / "consigliere.db"
CHROMA_PATH = DATA_DIR / "chroma"
EXAMPLES_DIR = PACKAGE_ROOT / "resources" / "examples"
