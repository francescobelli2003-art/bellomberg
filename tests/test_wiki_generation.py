"""Wiki pages derive from the canonical guide, without a second editable manual.

Every link is relative (PM decision 2A): no repository owner or host is written. The oracle
below resolves each destination the way a browser does from a Wiki page URL and checks the
target on disk and at the commit with its own code; it never reuses the generator's link logic.
Git repositories are throwaway ones under tmp_path.
"""
from collections import Counter
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urljoin, urlsplit

import pytest


PAGE_URL = 'https://github.com/o/r/wiki/Pagina'
REMOVED = 'was removed: the Wiki writes relative links only'


def generator(path=None):
    path = path or Path(__file__).resolve().parents[1] / 'docs/guide/build_wiki.py'
    spec = importlib.util.spec_from_file_location('wiki_generator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args, check=True, stdin=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    result = subprocess.run(['git', '-c', 'commit.gpgsign=false', *args], cwd=root, env=env, input=stdin,
                            capture_output=True, encoding='utf-8', errors='replace')
    if check:
        assert result.returncode == 0, result.stderr
    return result


def commit(root):
    """Index everything under root in its own throwaway repository and return the commit."""
    if not (root / '.git').exists():
        git(root, 'init', '-q')
    git(root, 'add', '--all', '--force', '.')
    tree = git(root, 'write-tree').stdout.strip()
    return git(root, 'commit-tree', tree, '-m', 'fixture').stdout.strip()


def handbook(root, where='docs/guide', marker=''):
    guide = root / where
    (guide / 'pages').mkdir(parents=True)
    (guide.parent / 'assets').mkdir()
    (guide.parent / 'assets/demo.svg').write_text('<svg/>', encoding='utf-8')
    (root / 'SUPPORT.md').write_text('# Support\n\n## Contact\n', encoding='utf-8')
    (root / 'private').mkdir()
    (root / 'private/secret.md').write_text('# Secret\n', encoding='utf-8')
    (guide / 'README.md').write_text(f'# Handbook{marker}\n\n[Install](installation.md#start)\n[Ledger](pages/16-trade-entry.md)\n[Assets](../assets)\n', encoding='utf-8')
    (guide / 'installation.md').write_text('# Installation\n\n## Start\n\n[Home](README.md)\n[Help](../../SUPPORT.md)\n[Contact](../../SUPPORT.md#contact)\n', encoding='utf-8')
    (guide / 'pages/16-trade-entry.md').write_text('# Trade entry\n\n![Demo](../../assets/demo.svg)\n[Same](#notes)\n[External](https://example.com/x)\n\n```md\n[Literal](do-not-rewrite.md)\n```\n', encoding='utf-8')
    return guide


def destinations(text):
    """Inline link and image destinations outside fenced blocks and code spans."""
    found, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'\s*(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            found += re.findall(r'\]\(\s*<?([^\s)>]+)', re.sub(r'`+[^`]*`+', '', line))
    return found


def resolve_every_destination(output, root, ref, sources):
    """Count destinations by kind; fail on any that a Wiki page would not reach."""
    absolute_in_sources = {dest for text in sources for dest in destinations(text)
                           if urlsplit(dest).scheme or urlsplit(dest).netloc}
    kinds, problems, targets = Counter(), [], []
    for name, text in output.items():
        for dest in destinations(text):
            parts = urlsplit(dest)
            if parts.scheme or parts.netloc:
                kinds['absolute'] += 1
                if dest not in absolute_in_sources:
                    problems.append((name, dest, 'absolute destination not in the source'))
                continue
            if not parts.path:
                kinds['anchor'] += 1
                continue
            path = urlsplit(urljoin(PAGE_URL, dest)).path
            repository = re.fullmatch(r'/o/r/(blob|tree|raw)/([^/]+)/(.+)', path)
            wiki = re.fullmatch(r'/o/r/wiki/([^/]+)', path)
            if repository and unquote(repository.group(2)) == ref:
                kinds[repository.group(1)] += 1
                expected = 'tree' if repository.group(1) == 'tree' else 'blob'
                targets.append((name, dest, unquote(repository.group(3)), expected))
            elif wiki and wiki.group(1) + '.md' in output:
                kinds['wiki'] += 1
            else:
                problems.append((name, dest, 'resolves to neither a generated page nor the commit'))
    listed = ''.join(f'{ref}:{relative}\n' for _, _, relative, _ in targets)
    at_ref = git(root, 'cat-file', '--batch-check=%(objecttype)', stdin=listed).stdout.splitlines()
    assert len(at_ref) == len(targets)
    for (name, dest, relative, expected), found in zip(targets, at_ref):
        on_disk = (root / relative).is_dir() if expected == 'tree' else (root / relative).is_file()
        if not on_disk or found != expected:
            problems.append((name, dest, 'repository target missing on disk or at the commit'))
    assert problems == []
    return kinds


def test_wiki_links_are_relative_and_preserve_prose_code_and_external_links(tmp_path):
    guide = handbook(tmp_path)
    sha = commit(tmp_path)
    output = generator().build_wiki(guide, sha, '0.8.0')
    assert set(output) == {'Home.md', 'Installation.md', 'Page-16-Trade-Entry.md', '_Sidebar.md', '_Footer.md'}
    assert '[Install](Installation#start)' in output['Home.md']
    assert '[Ledger](Page-16-Trade-Entry)' in output['Home.md']
    assert f'[Assets](../tree/{sha}/docs/assets)' in output['Home.md']
    assert f'[Help](../blob/{sha}/SUPPORT.md)' in output['Installation.md']
    assert f'[Contact](../blob/{sha}/SUPPORT.md#contact)' in output['Installation.md']
    assert f'![Demo](../raw/{sha}/docs/assets/demo.svg)' in output['Page-16-Trade-Entry.md']
    assert output['Installation.md'].splitlines()[1] == f'[Canonical source](../blob/{sha}/docs/guide/installation.md) · Bellomberg 0.8.0'
    assert f'[source handbook](../blob/{sha}/docs/guide/README.md)' in output['_Footer.md']
    assert '[Same](#notes)' in output['Page-16-Trade-Entry.md']
    assert '[External](https://example.com/x)' in output['Page-16-Trade-Entry.md']
    assert '[Literal](do-not-rewrite.md)' in output['Page-16-Trade-Entry.md']
    assert '0.8.0' in output['_Footer.md']
    assert 'Installation' in output['_Sidebar.md']


@pytest.mark.parametrize('where', ['docs/guide', 'handbook/en'])
def test_fake_handbook_destinations_all_resolve_from_a_wiki_page_without_any_host(tmp_path, where):
    guide = handbook(tmp_path, where)
    sha = commit(tmp_path)
    output = generator().build_wiki(guide, sha, '0.8.0')
    sources = [path.read_text(encoding='utf-8') for path in guide.rglob('*.md')]
    kinds = resolve_every_destination(output, tmp_path, sha, sources)
    assert kinds == {'blob': 6, 'tree': 1, 'raw': 1, 'wiki': 6, 'absolute': 1, 'anchor': 1}
    assert [name for name, text in output.items() if 'github.com' in text] == []


@pytest.mark.parametrize('bad, reason', [('../../../outside.md', 'Missing or outside source link'),
                                         ('missing.md', 'Missing or outside source link'),
                                         ('../../private/secret.md', 'Private source link')])
def test_wiki_refuses_missing_outside_or_private_links(tmp_path, bad, reason):
    repo = tmp_path / 'repo'
    guide = handbook(repo)
    # The outside target EXISTS on disk: the refusal must come from the root check, not from absence.
    (tmp_path / 'outside.md').write_text('# Outside\n', encoding='utf-8')
    (guide / 'README.md').write_text(f'# Handbook\n[Unsafe]({bad})\n', encoding='utf-8')
    sha = commit(repo)
    with pytest.raises(ValueError, match=reason):
        generator().build_wiki(guide, sha, '0.8.0')


def test_a_branch_name_is_written_as_the_commit_it_names_here(tmp_path):
    guide = handbook(tmp_path)
    sha = commit(tmp_path)
    git(tmp_path, 'update-ref', 'refs/heads/main', sha)
    output = generator().build_wiki(guide, 'main', '0.8.0')
    joined = ''.join(output.values())
    assert '../blob/' + sha + '/' in joined
    assert '/main/' not in joined


def test_a_missing_handbook_is_refused_before_git_is_asked(tmp_path):
    with pytest.raises(ValueError, match='Handbook is missing'):
        generator().build_wiki(tmp_path / 'docs' / 'guide', 'main', '0.8.0')


def test_wiki_refuses_a_ref_this_checkout_cannot_resolve(tmp_path):
    guide = handbook(tmp_path / 'public')
    commit(tmp_path / 'public')
    handbook(tmp_path / 'other', marker=' elsewhere')
    other = commit(tmp_path / 'other')
    tree = git(tmp_path / 'public', 'write-tree').stdout.strip()
    for ref in (other, 'no-such-branch', tree):
        with pytest.raises(ValueError, match='does not resolve to a commit in this git checkout'):
            generator().build_wiki(guide, ref, '0.8.0')


@pytest.mark.parametrize('late', ['linked file', 'guide page', 'folder that was a file'])
def test_wiki_refuses_paths_that_exist_on_disk_but_not_at_the_ref(tmp_path, late):
    guide = handbook(tmp_path)
    (tmp_path / 'notes').write_text('a file at the commit\n', encoding='utf-8')
    sha = commit(tmp_path)
    if late == 'linked file':
        (tmp_path / 'LATE.md').write_text('# Late\n', encoding='utf-8')
        with (guide / 'installation.md').open('a', encoding='utf-8') as page:
            page.write('[Late](../../LATE.md)\n')
        missing = 'LATE.md'
    elif late == 'guide page':
        (guide / 'late.md').write_text('# Late\n', encoding='utf-8')
        missing = 'docs/guide/late.md'
    else:
        (tmp_path / 'notes').unlink()
        (tmp_path / 'notes').mkdir()
        (tmp_path / 'notes/today.md').write_text('# Today\n', encoding='utf-8')
        with (guide / 'installation.md').open('a', encoding='utf-8') as page:
            page.write('[Notes](../../notes)\n')
        missing = 'notes'
    with pytest.raises(ValueError, match='not present at the ref in this git checkout') as refused:
        generator().build_wiki(guide, sha, '0.8.0')
    assert missing in str(refused.value).split(': ', 1)[1].split(', ')


def test_wiki_requires_the_handbook_root_to_be_the_top_of_a_git_checkout(tmp_path):
    guide = handbook(tmp_path / 'tree')
    with pytest.raises(ValueError, match='is not the top of a git checkout'):
        generator().build_wiki(guide, 'HEAD', '0.8.0')
    (tmp_path / 'outer.md').write_text('# Outer\n', encoding='utf-8')
    sha = commit(tmp_path)
    with pytest.raises(ValueError, match='is not the top of a git checkout'):
        generator().build_wiki(guide, sha, '0.8.0')


def test_wiki_verifies_its_own_checkout_even_when_git_variables_point_elsewhere(tmp_path, monkeypatch):
    guide = handbook(tmp_path / 'public')
    sha = commit(tmp_path / 'public')
    handbook(tmp_path / 'other', marker=' elsewhere')
    commit(tmp_path / 'other')
    monkeypatch.setenv('GIT_DIR', str(tmp_path / 'other/.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(tmp_path / 'other'))
    output = generator().build_wiki(guide, sha, '0.8.0')
    assert f'[Help](../blob/{sha}/SUPPORT.md)' in output['Installation.md']


def test_wiki_write_and_check_measure_stale_missing_and_unexpected_files(tmp_path):
    module = generator()
    guide = handbook(tmp_path / 'repo')
    content = module.build_wiki(guide, commit(tmp_path / 'repo'), '0.8.0')
    output = tmp_path / 'wiki'
    module.write_wiki(output, content)
    assert module.check_wiki(output, content) == []
    (output / 'Home.md').write_text('stale', encoding='utf-8')
    (output / 'Installation.md').unlink()
    (output / 'Unexpected.md').write_text('extra', encoding='utf-8')
    assert set(module.check_wiki(output, content)) == {'changed: Home.md', 'missing: Installation.md', 'unexpected: Unexpected.md'}
    with pytest.raises(ValueError, match='Output must be empty'):
        module.write_wiki(output, content)


@pytest.mark.parametrize('option', [['--repository-url', 'https://github.com/example/terminal'],
                                    ['--repository-url=https://github.com/example/terminal'],
                                    ['--repository-url']])
def test_cli_rejects_the_removed_repository_url_option(tmp_path, monkeypatch, capsys, option):
    output = tmp_path / 'wiki'
    monkeypatch.setattr(sys, 'argv', ['build_wiki.py', *option, '--ref', 'HEAD', '--output', str(output)])
    with pytest.raises(SystemExit) as stopped:
        generator().main()
    assert stopped.value.code == 2
    assert '--repository-url ' + REMOVED in capsys.readouterr().err
    assert not output.exists()


def test_cli_requires_an_explicit_ref(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['build_wiki.py', '--output', str(tmp_path / 'wiki')])
    with pytest.raises(SystemExit) as stopped:
        generator().main()
    assert stopped.value.code == 2
    assert 'the following arguments are required: --ref' in capsys.readouterr().err


def test_cli_generates_then_checks_a_wiki_from_its_own_checkout(tmp_path, monkeypatch, capsys):
    repo = tmp_path / 'repo'
    guide = handbook(repo)
    (repo / 'pyproject.toml').write_text('[project]\nversion = "0.8.0"\n', encoding='utf-8')
    script = guide / 'build_wiki.py'
    script.write_bytes((Path(__file__).resolve().parents[1] / 'docs/guide/build_wiki.py').read_bytes())
    sha = commit(repo)
    output = tmp_path / 'wiki'
    monkeypatch.setattr(sys, 'argv', ['build_wiki.py', '--ref', sha, '--output', str(output)])
    assert generator(script).main() == 0
    assert 'Wiki: 5 generated files; 0 differences' in capsys.readouterr().out
    assert f'[Help](../blob/{sha}/SUPPORT.md)' in (output / 'Installation.md').read_text(encoding='utf-8')
    (output / 'Home.md').write_text('stale', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['build_wiki.py', '--ref', sha, '--output', str(output), '--check'])
    assert generator(script).main() == 1
    assert 'changed: Home.md' in capsys.readouterr().out


def test_real_handbook_texts_build_nineteen_pages_whose_destinations_all_resolve(tmp_path):
    """The real guide pages are copied into a throwaway checkout; each linked path that exists
    in this tree gets an empty stand-in, so a broken link stays broken and the build refuses it."""
    root = Path(__file__).resolve().parents[1]
    real_guide = root / 'docs/guide'
    guide = tmp_path / 'docs/guide'
    sources = []
    for page in sorted(real_guide.rglob('*.md')):
        copy = guide / page.relative_to(real_guide)
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(page.read_bytes())
        text = page.read_text(encoding='utf-8')
        sources.append(text)
        for dest in destinations(text):
            parts = urlsplit(dest)
            if parts.scheme or parts.netloc or not parts.path:
                continue
            target = (page.parent / unquote(parts.path)).resolve()
            if not target.is_relative_to(root) or (target.is_relative_to(real_guide) and target.suffix == '.md'):
                continue
            stand_in = tmp_path / target.relative_to(root)
            if target.is_dir():
                stand_in.mkdir(parents=True, exist_ok=True)
                (stand_in / 'stand-in').touch()
            elif target.is_file():
                stand_in.parent.mkdir(parents=True, exist_ok=True)
                stand_in.touch()
    sha = commit(tmp_path)
    result = generator().build_wiki(guide, sha, '0.8.0')
    assert len([name for name in result if name.startswith('Page-')]) == 19
    kinds = resolve_every_destination(result, tmp_path, sha, sources)
    assert kinds['blob'] and kinds['tree'] and kinds['raw'] and kinds['wiki']
