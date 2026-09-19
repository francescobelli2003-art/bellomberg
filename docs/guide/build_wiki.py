"""Generate a GitHub Wiki from the canonical handbook. No network or publication.

python docs/guide/build_wiki.py --ref <published-commit> --output <empty-directory>
Repeat with --check to measure whether a generated Wiki still matches the guide.

No repository address is written: every page and repository link it writes is relative to a
Wiki page URL of the form <repository>/wiki/<Page>; external links are copied unchanged. Guide pages link to each other by Wiki page name; repository files
become ../blob/<ref>/<path>, folders ../tree/<ref>/<path> and images ../raw/<ref>/<path>,
keeping any #fragment. The page header and the footer use the same form.

Run it from a git checkout whose top folder is the handbook root. With local git only,
--ref must resolve to a commit of that checkout and every linked path (page sources
included) must exist at that commit, so a ref this checkout does not know stops the build.
A branch or tag name is resolved to its full commit id and links use that id, so a name that
also exists in another repository cannot make the Wiki follow a different history.
Page text is read from the working tree and is not compared with the commit.
--repository-url was removed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, unquote, urlsplit


LINK = re.compile(r'(!?\[[^\]]*\]\()([^\s)]+)([^)]*\))')
REMOVED_REPOSITORY_URL = '--repository-url was removed: the Wiki writes relative links only'
# Variables that would send git to another repository than the handbook's own checkout.
GIT_LOCATION = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
                'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_NAMESPACE')


def _name(relative: Path) -> str:
    if relative.as_posix() == 'README.md':
        return 'Home'
    words = relative.with_suffix('').as_posix().replace('pages/', 'Page-').replace('/', '-')
    return '-'.join(word.title() for word in words.split('-'))


def _git(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    environment = {key: value for key, value in os.environ.items() if key not in GIT_LOCATION}
    try:
        return subprocess.run(['git', *args], cwd=root, env=environment, input=stdin,
                              capture_output=True, encoding='utf-8', errors='replace')
    except FileNotFoundError as error:
        raise ValueError('git is required to verify the source revision') from error


def _verify_revision(root: Path, ref: str) -> str:
    """Return the full commit id the ref names in this checkout, or stop."""
    top = _git(root, 'rev-parse', '--show-toplevel')
    if top.returncode != 0 or not os.path.samefile(top.stdout.strip(), root):
        raise ValueError('Handbook root is not the top of a git checkout: ' + str(root))
    resolved = _git(root, 'rev-parse', '--verify', '--quiet', ref + '^{commit}')
    commit = resolved.stdout.strip()
    if resolved.returncode != 0 or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Source revision does not resolve to a commit in this git checkout: ' + ref)
    return commit


def _verify_paths(root: Path, ref: str, linked: dict[str, str]) -> None:
    paths = sorted(linked)
    found = _git(root, 'cat-file', '--batch-check=%(objecttype)',
                 stdin=''.join(f'{ref}:{path}\n' for path in paths))
    kinds = found.stdout.splitlines()
    if found.returncode != 0 or len(kinds) != len(paths):
        raise ValueError('Could not read linked paths at the source revision: ' + ref)
    absent = [path or '.' for path, kind in zip(paths, kinds) if kind != linked[path]]
    if absent:
        raise ValueError(f'Linked paths not present at the ref in this git checkout ({ref}): ' + ', '.join(absent))


def build_wiki(guide: Path, ref: str, version: str) -> dict[str, str]:
    guide = guide.resolve()
    root = guide.parents[1]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', ref) or '..' in ref:
        raise ValueError('Invalid source revision')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', version):
        raise ValueError('Invalid application version')
    if not guide.is_dir():
        raise ValueError('Handbook is missing or contains duplicate Wiki page names')
    ref = _verify_revision(root, ref)
    sources = sorted(guide.rglob('*.md'))
    names = {path: _name(path.relative_to(guide)) for path in sources}
    if not sources or 'Home' not in names.values() or len(set(names.values())) != len(names):
        raise ValueError('Handbook is missing or contains duplicate Wiki page names')
    linked_at_ref = {}

    def repo_link(mode: str, relative: Path, fragment: str = '') -> str:
        linked_at_ref['' if not relative.parts else relative.as_posix()] = 'tree' if mode == 'tree' else 'blob'
        return '../' + mode + '/' + quote(ref, safe='') + '/' + quote(relative.as_posix(), safe='/') + fragment

    titles = {}
    result = {}
    for source in sources:
        text = source.read_text(encoding='utf-8')
        heading = re.search(r'^# (.+)$', text, flags=re.MULTILINE)
        if not heading:
            raise ValueError('Page has no title: ' + source.name)
        titles[names[source]] = heading.group(1)

        def rewrite(match):
            destination = match.group(2)
            url = urlsplit(destination)
            if url.scheme or url.netloc or not url.path:
                return match.group(0)
            target = (source.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(root) or not target.exists():
                raise ValueError('Missing or outside source link: ' + destination)
            relative = target.relative_to(root)
            if any(part in {'private', '.git', 'data', 'attic', 'archive'} for part in relative.parts):
                raise ValueError('Private source link: ' + destination)
            fragment = '#' + url.fragment if url.fragment else ''
            if target in names:
                linked = names[target] + fragment
            else:
                if match.group(1).startswith('!') and not target.is_file():
                    raise ValueError('Image link points to a directory: ' + destination)
                mode = 'raw' if match.group(1).startswith('!') else 'tree' if target.is_dir() else 'blob'
                linked = repo_link(mode, relative, fragment)
            return match.group(1) + linked + match.group(3)

        # Preserve fenced examples and inline code exactly; only real links move.
        lines = []
        fence = None
        for line in text.splitlines(keepends=True):
            marker = re.match(r'^\s*(`{3,}|~{3,})', line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
                lines.append(line)
            elif fence:
                lines.append(line)
            else:
                parts = re.split(r'(`+[^`]*`+)', line)
                lines.append(''.join(part if index % 2 else LINK.sub(rewrite, part)
                                     for index, part in enumerate(parts)))
        source_link = repo_link('blob', source.relative_to(root))
        result[names[source] + '.md'] = (
            f'<!-- Generated from the canonical handbook; edit the source guide and regenerate. -->\n'
            f'[Canonical source]({source_link}) · Bellomberg {version}\n\n' + ''.join(lines))
    ordered = ['Home', *sorted(name for name in titles if name != 'Home' and not name.startswith('Page-')),
               *sorted(name for name in titles if name.startswith('Page-'))]
    result['_Sidebar.md'] = '\n'.join(f'- [{titles[name]}]({name})' for name in ordered) + '\n'
    home = next(source for source in sources if names[source] == 'Home')
    handbook_link = repo_link('blob', home.relative_to(root))
    result['_Footer.md'] = f'Bellomberg {version} · Generated from the [source handbook]({handbook_link}). Edit the guide and regenerate; do not maintain a second manual here.\n'
    _verify_paths(root, ref, linked_at_ref)
    return result


def check_wiki(output: Path, content: dict[str, str]) -> list[str]:
    present = {path.relative_to(output).as_posix(): path for path in output.rglob('*')
               if path.is_file() and '.git' not in path.relative_to(output).parts} if output.exists() else {}
    issues = ['missing: ' + name for name in sorted(set(content) - set(present))]
    issues += ['unexpected: ' + name for name in sorted(set(present) - set(content))]
    issues += ['changed: ' + name for name in sorted(set(content) & set(present))
               if present[name].read_bytes() != content[name].encode('utf-8')]
    return issues


def write_wiki(output: Path, content: dict[str, str]) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be empty; generate separately and review the diff before updating a Wiki')
    output.mkdir(parents=True, exist_ok=True)
    for name, text in content.items():
        (output / name).write_bytes(text.encode('utf-8'))


class _RemovedOption(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        parser.error(REMOVED_REPOSITORY_URL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repository-url', nargs='?', action=_RemovedOption, help=argparse.SUPPRESS)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ref', required=True,
                        help='Commit of the repository that hosts the Wiki; must resolve in this checkout')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    guide = Path(__file__).resolve().parent
    metadata = (guide.parents[1] / 'pyproject.toml').read_text(encoding='utf-8')
    version = re.search(r'^version\s*=\s*"([^"]+)"', metadata, flags=re.MULTILINE)
    try:
        if version is None:
            raise ValueError('Application version missing')
        content = build_wiki(guide, args.ref, version.group(1))
        if not args.check:
            write_wiki(args.output, content)
        issues = check_wiki(args.output, content)
        for issue in issues:
            print(issue)
        print(f'Wiki: {len(content)} generated files; {len(issues)} differences; no publication performed')
        return 1 if issues else 0
    except (OSError, ValueError) as error:
        print(f'Wiki generation failed: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
