"""Wiki pages derive from the canonical guide, without a second editable manual."""
import importlib.util
from pathlib import Path

import pytest


def generator():
    path = Path(__file__).resolve().parents[1] / 'docs/guide/build_wiki.py'
    spec = importlib.util.spec_from_file_location('wiki_generator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def handbook(tmp_path):
    guide = tmp_path / 'docs/guide'
    (guide / 'pages').mkdir(parents=True)
    (tmp_path / 'docs/assets').mkdir()
    (tmp_path / 'docs/assets/demo.svg').write_text('<svg/>', encoding='utf-8')
    (tmp_path / 'SUPPORT.md').write_text('# Support\n', encoding='utf-8')
    (guide / 'README.md').write_text('# Handbook\n\n[Install](installation.md#start)\n[Ledger](pages/16-trade-entry.md)\n', encoding='utf-8')
    (guide / 'installation.md').write_text('# Installation\n\n## Start\n\n[Home](README.md)\n[Help](../../SUPPORT.md)\n', encoding='utf-8')
    (guide / 'pages/16-trade-entry.md').write_text('# Trade entry\n\n![Demo](../../assets/demo.svg)\n[Same](#notes)\n[External](https://example.com/x)\n\n```md\n[Literal](do-not-rewrite.md)\n```\n', encoding='utf-8')
    return guide


def test_wiki_rewrites_navigation_and_assets_but_preserves_prose_code_and_external_links(tmp_path):
    output = generator().build_wiki(handbook(tmp_path), 'https://github.com/example/terminal', 'abcdef1', '0.8.0')
    assert set(output) == {'Home.md', 'Installation.md', 'Page-16-Trade-Entry.md', '_Sidebar.md', '_Footer.md'}
    assert '[Install](Installation#start)' in output['Home.md']
    assert '[Ledger](Page-16-Trade-Entry)' in output['Home.md']
    assert '[Help](https://github.com/example/terminal/blob/abcdef1/SUPPORT.md)' in output['Installation.md']
    assert 'https://github.com/example/terminal/raw/abcdef1/docs/assets/demo.svg' in output['Page-16-Trade-Entry.md']
    assert '[Same](#notes)' in output['Page-16-Trade-Entry.md']
    assert '[External](https://example.com/x)' in output['Page-16-Trade-Entry.md']
    assert '[Literal](do-not-rewrite.md)' in output['Page-16-Trade-Entry.md']
    assert '0.8.0' in output['_Footer.md']
    assert 'Installation' in output['_Sidebar.md']


@pytest.mark.parametrize('bad', ['../../../outside.md', '../../private/secret.md', 'missing.md'])
def test_wiki_refuses_missing_or_private_links(tmp_path, bad):
    guide = handbook(tmp_path)
    (guide / 'README.md').write_text(f'# Handbook\n[Unsafe]({bad})\n', encoding='utf-8')
    with pytest.raises(ValueError):
        generator().build_wiki(guide, 'https://github.com/example/terminal', 'main', '0.8.0')


def test_wiki_write_and_check_measure_stale_missing_and_unexpected_files(tmp_path):
    module = generator()
    content = module.build_wiki(handbook(tmp_path), 'https://github.com/example/terminal', 'main', '0.8.0')
    output = tmp_path / 'wiki'
    module.write_wiki(output, content)
    assert module.check_wiki(output, content) == []
    (output / 'Home.md').write_text('stale', encoding='utf-8')
    (output / 'Installation.md').unlink()
    (output / 'Unexpected.md').write_text('extra', encoding='utf-8')
    assert set(module.check_wiki(output, content)) == {'changed: Home.md', 'missing: Installation.md', 'unexpected: Unexpected.md'}
    with pytest.raises(ValueError):
        module.write_wiki(output, content)


def test_real_handbook_build_has_all_nineteen_pages_and_no_unresolved_relative_assets():
    root = Path(__file__).resolve().parents[1]
    result = generator().build_wiki(root / 'docs/guide', 'https://github.com/example/terminal', 'main', '0.8.0')
    assert len([name for name in result if name.startswith('Page-')]) == 19
    assert all('](../' not in text for text in result.values())
