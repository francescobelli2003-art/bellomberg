"""Generate a GitHub Wiki from the canonical handbook. No network or publication.

python docs/guide/build_wiki.py --repository-url <public-repository-url> --output <empty-directory>
Repeat with --check to measure whether a generated Wiki still matches the guide.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit


LINK = re.compile(r'(!?\[[^\]]*\]\()([^\s)]+)([^)]*\))')


def _name(relative: Path) -> str:
    if relative.as_posix() == 'README.md':
        return 'Home'
    words = relative.with_suffix('').as_posix().replace('pages/', 'Page-').replace('/', '-')
    return '-'.join(word.title() for word in words.split('-'))


def build_wiki(guide: Path, repository_url: str, ref: str, version: str) -> dict[str, str]:
    guide = guide.resolve()
    root = guide.parents[1]
    repository_url = repository_url.rstrip('/').removesuffix('.git')
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository_url):
        raise ValueError('Expected a public HTTPS GitHub repository URL without credentials or query')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', ref) or '..' in ref:
        raise ValueError('Invalid source revision')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', version):
        raise ValueError('Invalid application version')
    sources = sorted(guide.rglob('*.md'))
    names = {path: _name(path.relative_to(guide)) for path in sources}
    if not sources or 'Home' not in names.values() or len(set(names.values())) != len(names):
        raise ValueError('Handbook is missing or contains duplicate Wiki page names')
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
                linked = repository_url + '/' + mode + '/' + quote(ref, safe='') + '/' + quote(relative.as_posix(), safe='/') + fragment
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
        source_link = repository_url + '/blob/' + quote(ref, safe='') + '/' + source.relative_to(root).as_posix()
        result[names[source] + '.md'] = (
            f'<!-- Generated from the canonical handbook; edit the source guide and regenerate. -->\n'
            f'[Canonical source]({source_link}) · Bellomberg {version}\n\n' + ''.join(lines))
    ordered = ['Home', *sorted(name for name in titles if name != 'Home' and not name.startswith('Page-')),
               *sorted(name for name in titles if name.startswith('Page-'))]
    result['_Sidebar.md'] = '\n'.join(f'- [{titles[name]}]({name})' for name in ordered) + '\n'
    result['_Footer.md'] = f'Bellomberg {version} · Generated from the [source handbook]({repository_url}/blob/{quote(ref, safe="")}/docs/guide/README.md). Edit the guide and regenerate; do not maintain a second manual here.\n'
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-url', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ref', default='main', help='Published commit or branch; used only in source links')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    guide = Path(__file__).resolve().parent
    metadata = (guide.parents[1] / 'pyproject.toml').read_text(encoding='utf-8')
    version = re.search(r'^version\s*=\s*"([^"]+)"', metadata, flags=re.MULTILINE)
    try:
        if version is None:
            raise ValueError('Application version missing')
        content = build_wiki(guide, args.repository_url, args.ref, version.group(1))
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
