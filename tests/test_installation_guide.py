"""Public first-run instructions and the existing handbook consistency gate."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_handbook_links_navigation_and_assets_are_checked_by_the_suite():
    result = subprocess.run([sys.executable, str(ROOT / "docs/guide/verify_docs.py")],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_first_run_explains_explicit_language_and_documented_opening_positions():
    first = (ROOT / "docs/guide/first-session.md").read_text(encoding="utf-8")
    for required in ("English or Italian", "Opening position", "not a purchase date", "does not create", "historical NAV", "120 seconds"):
        assert required in first
    assert first.index("English or Italian") < first.index("Establish the ledger")


def test_mac_installation_distinguishes_source_ci_from_real_hardware_validation():
    text = (ROOT / "docs/guide/installation.md").read_text(encoding="utf-8")
    assert "macos-source" in text
    assert "physical Mac" in text and "not been verified" in text
    for required in ("pip check", 'BELLOMBERG_PYTHON="$PWD/.venv/bin/python"', "launchd", "Excel/COM", "Intel"):
        assert required in text


def test_backend_lifespan_announces_the_configured_port(monkeypatch, capsys):
    import asyncio
    from bellomberg.api import bellomberg_api as api
    monkeypatch.setenv('BELLOMBERG_API_PORT', '8876')
    async def boot():
        async with api.lifespan(api.app):
            pass
    asyncio.run(boot())
    assert 'Listening on http://127.0.0.1:8876' in capsys.readouterr().out
