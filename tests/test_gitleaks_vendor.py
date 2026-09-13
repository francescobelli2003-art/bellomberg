"""Skipped minified JS must pass the real pinned scanner on its original bytes."""
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tools.release import verifica_pubblico as vp


@pytest.fixture
def binary():
    executable = Path(vp.GITLEAKS_EXE)
    if not executable.is_file():
        pytest.skip('Pinned Gitleaks unavailable; real-engine minified JS test')
    return str(executable)


def minified(*, secret=False):
    value = 'synthetic caffè'
    if secret:
        # Inert synthetic token built at runtime; no credential in the source.
        value = 'ghp_' + ''.join(('p4TnV8', 'x7DjQ2', 'z5KsB9', 'w3HmR6', 'y1FcN0', 'a8EeL4'))
    return ('/* synthetic fixture */\r\nconst github_token="' + value + '"; // gitleaks:allow\r\n').encode('utf-8')


def test_real_engine_reads_clean_minified_bytes_without_crlf_or_utf8_changes(tmp_path, binary):
    tree = tmp_path / 'tree'; tree.mkdir()
    payload = minified()
    asset = tree / 'plotly-2.32.0.min.js'; asset.write_bytes(payload)
    outcome = vp.controllo_gitleaks(str(tree), exe=binary)
    assert outcome.ok, outcome.errore
    assert f'{len(payload)}/{len(payload)} byte letti' in outcome.note
    assert '.min.js' in outcome.note and 'stdin' in outcome.note
    assert asset.read_bytes() == payload


def test_real_engine_detects_secret_in_min_js_despite_allow_comment(tmp_path, binary):
    tree = tmp_path / 'tree'; (tree / 'app/public/vendor').mkdir(parents=True)
    asset = tree / 'app/public/vendor/plotly-2.32.0.min.js'
    asset.write_bytes(minified(secret=True))
    outcome = vp.controllo_gitleaks(str(tree), exe=binary)
    assert not outcome.errore, outcome.errore
    assert not outcome.ok
    assert any(hit.file == 'app/public/vendor/plotly-2.32.0.min.js' and hit.token == 'github-pat' for hit in outcome.hit)
    assert 'ghp_' not in repr(outcome)


def test_real_engine_scans_original_pinned_plotly_bundle(tmp_path, binary):
    original = Path(vp.REPO) / 'app/public/vendor/plotly-2.32.0.min.js'
    payload = original.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == '0a17719a72751704861215da0e5c5cdb3f9a8d50eff5cb84cb6f8b80786682b0'
    tree = tmp_path / 'tree'; tree.mkdir()
    asset = tree / original.name; asset.write_bytes(payload)
    outcome = vp.controllo_gitleaks(str(tree), exe=binary)
    assert outcome.ok, outcome.errore or outcome.hit
    assert f'{len(payload)}/{len(payload)} byte letti' in outcome.note
    assert asset.read_bytes() == payload


def test_mixed_directory_svg_and_min_js_have_exact_combined_coverage(tmp_path, binary):
    tree = tmp_path / 'tree'; tree.mkdir()
    sources = {'ordinary.js': b'const ordinary = true;\n',
               'diagram.svg': '<svg><!-- caffè --></svg>\r\n'.encode('utf-8'),
               'ordinary.min.js': b'const already_scanned = true;\n',
               'plotly-2.32.0.min.js': minified()}
    for name, payload in sources.items():
        (tree / name).write_bytes(payload)
    outcome = vp.controllo_gitleaks(str(tree), exe=binary)
    total = sum(map(len, sources.values()))
    assert outcome.ok, outcome.errore
    assert f'{total}/{total} byte letti' in outcome.note


@pytest.mark.parametrize('fault', ['missing_count', 'partial_count', 'missing_report', 'exit_failure', 'hit_without_findings', 'changed_bytes'])
def test_min_js_stdin_failures_stay_fail_closed(tmp_path, binary, fault):
    tree = tmp_path / 'tree'; tree.mkdir()
    asset = tree / 'plotly-2.32.0.min.js'; asset.write_bytes(minified())
    def execute(args, **kwargs):
        if args[1] != 'stdin':
            return subprocess.run(args, **kwargs)
        assert isinstance(kwargs['input'], bytes)
        assert 'encoding' not in kwargs and not kwargs.get('text')
        report = Path(args[args.index('--report-path') + 1])
        if fault != 'missing_report':
            report.write_text('[]')
        count = len(kwargs['input']) - (fault == 'partial_count')
        stderr = b'no count' if fault == 'missing_count' else f'scanned ~{count} bytes'.encode()
        code = 1 if fault == 'exit_failure' else vp.GITLEAKS_EXIT_HIT if fault == 'hit_without_findings' else 0
        if fault == 'changed_bytes':
            asset.write_bytes(minified() + b'changed')
        return SimpleNamespace(returncode=code, stdout=b'', stderr=stderr)
    outcome = vp.controllo_gitleaks(str(tree), exe=binary, esegui=execute)
    assert not outcome.ok
    assert 'plotly-2.32.0.min.js' in outcome.errore
    assert 'NON scansionati' not in outcome.errore


def test_unhandled_skipped_extension_remains_blocking(tmp_path, binary):
    tree = tmp_path / 'tree'; tree.mkdir()
    (tree / 'plotly-2.32.0.min.js').write_bytes(minified())
    (tree / 'unexpected.png').write_bytes(b'not scanned')
    outcome = vp.controllo_gitleaks(str(tree), exe=binary)
    assert not outcome.ok and 'NON scansionati' in outcome.errore
    # 13/09: both reasons stay in the verdict: the unscanned file is named and the
    # failed raster provenance check is not replaced by the coverage report.
    assert 'unexpected.png' in outcome.errore and 'verifica raster' in outcome.errore
