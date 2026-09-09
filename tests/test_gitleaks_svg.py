"""Textual SVGs must be scanned, with independently measured byte coverage."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tools.release import verifica_pubblico as vp


@pytest.fixture
def binary():
    path = Path(vp.GITLEAKS_EXE)
    if not path.is_file():
        pytest.skip('Pinned local Gitleaks executable unavailable; real-engine SVG test')
    return str(path)


def svg(*, secret=False):
    # Assemble an inert synthetic token at runtime; the source contains no key.
    content = 'Synthetic illustration: nessuna credenziale, caffè.'
    if secret:
        token = 'ghp_' + ''.join(('y7JzP9', 'x2NqF4', 'v8BaR6', 's3DhK5', 'm1UcT0', 'w4EeL2'))
        content = 'github_token = "' + token + '"'
    return ('<svg xmlns="http://www.w3.org/2000/svg">\r\n'
            '<metadata>' + content + '</metadata>\r\n</svg>\r\n').encode('utf-8')


def test_real_engine_reads_clean_svg_bytes_including_crlf_and_utf8(tmp_path, binary):
    tree = tmp_path / 'tree'; tree.mkdir()
    payload = svg()
    (tree / 'overview.svg').write_bytes(payload)
    outcome = vp.controllo_gitleaks(str(tree), exe=binary)
    assert outcome.ok, outcome.errore
    assert f'{len(payload)}/{len(payload)} byte letti' in outcome.note
    assert (tree / 'overview.svg').read_bytes() == payload


def test_real_engine_finds_secret_in_svg_and_reports_original_path(tmp_path, binary):
    tree = tmp_path / 'tree'; (tree / 'docs/assets').mkdir(parents=True)
    payload = svg(secret=True)
    (tree / 'docs/assets/secret.SVG').write_bytes(payload)
    outcome = vp.controllo_gitleaks(str(tree), exe=binary)
    assert not outcome.errore, outcome.errore
    assert not outcome.ok
    assert any(hit.file == 'docs/assets/secret.SVG' and hit.token == 'github-pat' for hit in outcome.hit)
    assert 'ghp_' not in repr(outcome)


def test_real_engine_keeps_other_unknown_skips_blocking(tmp_path, binary):
    tree = tmp_path / 'tree'; tree.mkdir()
    (tree / 'overview.svg').write_bytes(svg())
    (tree / 'unexpected.png').write_bytes(b'unscanned synthetic image')
    outcome = vp.controllo_gitleaks(str(tree), exe=binary)
    assert not outcome.ok and 'NON scansionati' in outcome.errore


@pytest.mark.parametrize('fault', ['missing_count', 'partial_count', 'missing_report', 'exit_failure', 'hit_without_findings'])
def test_svg_scan_failures_remain_explicit_errors(tmp_path, binary, fault):
    tree = tmp_path / 'tree'; tree.mkdir()
    (tree / 'overview.svg').write_bytes(svg())
    def execute(args, **kwargs):
        if args[1] != 'stdin':
            return subprocess.run(args, **kwargs)
        report = Path(args[args.index('--report-path') + 1])
        if fault != 'missing_report':
            report.write_text('[]')
        count = len(kwargs['input']) - (1 if fault == 'partial_count' else 0)
        stderr = b'no count' if fault == 'missing_count' else f'scanned ~{count} bytes'.encode()
        code = 1 if fault == 'exit_failure' else vp.GITLEAKS_EXIT_HIT if fault == 'hit_without_findings' else 0
        return SimpleNamespace(returncode=code, stdout=b'', stderr=stderr)
    outcome = vp.controllo_gitleaks(str(tree), exe=binary, esegui=execute)
    assert not outcome.ok and 'SVG' in outcome.errore
    assert 'NON scansionati' not in outcome.errore  # The failed stdin stage is identified.


def test_svg_non_utf8_bytes_are_declared_unsupported(tmp_path, binary):
    tree = tmp_path / 'tree'; tree.mkdir()
    (tree / 'overview.svg').write_bytes(b'<svg>\xff</svg>')
    outcome = vp.controllo_gitleaks(str(tree), exe=binary)
    assert not outcome.ok and 'UTF-8' in outcome.errore
