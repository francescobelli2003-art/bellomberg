"""Public documentation contracts for the packaged repository."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _project_metadata() -> tuple[str, str]:
    text = _text("pyproject.toml")
    version = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    python = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert version and python
    return version.group(1), python.group(1)


def test_readme_uses_packaged_install_and_entry_point():
    readme = _text("README.md")
    pyproject = _text("pyproject.toml")
    assert "python -m pip install -e ." in readme
    assert "python -m pip install -e . pytest" in readme
    assert "bellomberg-api" in readme
    assert re.search(r'^bellomberg-api\s*=\s*"[^"]+"', pyproject, re.MULTILINE)
    assert "python bellomberg_api.py" not in readme
    assert "pip install -r requirements.txt" not in readme


def test_frontend_commands_are_real_package_scripts():
    readme = _text("README.md")
    scripts = json.loads(_text("app/package.json"))["scripts"]
    for command in ("desktop:install", "test:release", "build:bundles", "test:desktop"):
        assert command in scripts
        assert f"npm run {command}" in readme
    assert "npm test" not in readme
    assert "npm ci" in readme
    assert "BELLOMBERG_PYTHON" in readme


def test_documented_runtime_versions_match_package_metadata():
    readme = _text("README.md")
    contributing = _text("CONTRIBUTING.md")
    _version, python_spec = _project_metadata()
    node_spec = json.loads(_text("app/package.json"))["engines"]["node"]

    python_min = python_spec.removeprefix(">=")
    node_min = node_spec.removeprefix(">=")
    node_parts = node_min.split(".")
    while len(node_parts) > 2 and node_parts[-1] == "0":
        node_parts.pop()
    node_min = ".".join(node_parts)
    for document in (readme, contributing):
        assert f"Python {python_min}" in document
        assert f"Node.js {node_min}" in document
    assert "Python 3.12" in readme or "CI uses Python 3.12" in contributing


def test_readme_describes_sqlite_cash_and_legacy_migration():
    readme = _text("README.md")
    assert "initial deposit" in readme
    assert "tools/migrations/migra_cassa_sqlite.py" in readme
    assert "--apply" in readme and "dry run" in readme
    assert "copy portfolio.example.json portfolio.json" not in readme.lower()
    assert "New installations do not use or require `portfolio.json`" in readme


def test_documented_paths_exist_in_packaged_layout():
    expected = (
        "src/bellomberg",
        "docs/ARCHITETTURA.md",
        "docs/setup/IBKR.md",
        "tools/ops/importa_trade_csv.py",
        "tools/migrations/migra_cassa_sqlite.py",
        "LICENSE",
        "NOTICE",
    )
    missing = [path for path in expected if not (ROOT / path).exists()]
    assert not missing, f"documented paths missing: {missing}"


def test_privacy_language_declares_remote_model_processing():
    readme = _text("README.md")
    lower = readme.lower()
    assert "openrouter" in lower
    assert "portfolio, mandate, and relevant research context" in lower
    assert "nothing leaves your computer" not in lower
    assert "not financial advice" in lower


def test_docs_do_not_claim_unbounded_validation_or_stale_counts():
    documents = "\n".join((_text("README.md"), _text("docs/ARCHITETTURA.md")))
    forbidden = (
        r"\bfully tested\b",
        r"\bbug[- ]free\b",
        r"\b\d+ endpoints\b",
        r"\b\d+ tests? (?:pass|passing|passed)\b",
        r"backend python \(.*?\d+ modules\)",
    )
    for pattern in forbidden:
        assert not re.search(pattern, documents, flags=re.IGNORECASE), pattern
    assert "offline" in documents.lower()
    assert "live" in documents.lower()


def test_public_documents_are_english():
    headings = re.findall(
        r"^#{1,3} +(.+)$",
        "\n".join((_text("README.md"), _text("docs/ARCHITETTURA.md"), _text("CONTRIBUTING.md"))),
        flags=re.MULTILINE,
    )
    assert headings
    italian_headings = re.compile(
        r"\b(architettura|installazione|requisiti|contribuire|licenza|dati personali|primo avvio)\b",
        flags=re.IGNORECASE,
    )
    assert not [heading for heading in headings if italian_headings.search(heading)]
