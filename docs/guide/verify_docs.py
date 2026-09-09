"""Check public handbook links, page inventory and safe synthetic SVG structure.

Standard library only; reads source/docs, never runtime data or configuration.
Run from any working directory: python docs/guide/verify_docs.py
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "guide"
ASSETS = ROOT / "docs" / "assets" / "product"
EXPECTED = [
    ("dashboard", "01-command-center"), ("performance", "02-performance"),
    ("watchlist", "03-watchlist"), ("market", "04-global-markets"),
    ("news", "05-news-desk"), ("fundamentals", "06-fundamentals"),
    ("factors", "07-factor-lab"), ("montecarlo", "08-monte-carlo"),
    ("vol", "09-vol-deck"), ("edge", "10-edge-scanner"),
    ("chat", "11-agent-chat"), ("agents", "12-agents-live"),
    ("progress", "13-agent-progress"), ("memos", "14-memo-archive"),
    ("decisions", "15-decisions"), ("trades", "16-trade-entry"),
    ("movements", "17-movements"), ("mandato", "18-mandate-journal"),
    ("settings", "19-settings"),
]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def anchors(path: Path) -> set[str]:
    result: set[str] = set()
    seen: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^#{1,6}\s", line):
            continue
        title = re.sub(r"^#+\s+", "", line).strip().lower()
        title = re.sub(r"[^\w\- ]", "", title, flags=re.UNICODE).replace(" ", "-")
        n = seen.get(title, 0)
        seen[title] = n + 1
        result.add(title if n == 0 else f"{title}-{n}")
    return result


def check_links(doc: Path) -> int:
    text = doc.read_text(encoding="utf-8")
    count = 0
    if text.startswith("\ufeff"):
        fail(f"UTF-8 BOM: {doc.relative_to(ROOT)}")
    for match in LINK.finditer(text):
        url = urlsplit(match.group(1).strip("<>"))
        if url.scheme or url.netloc:
            continue
        count += 1
        target = (doc.parent / unquote(url.path)).resolve() if url.path else doc
        if not target.is_relative_to(ROOT):
            fail(f"Link leaves source tree: {doc.relative_to(ROOT)} → {match.group(1)}")
        elif not target.exists():
            fail(f"Missing target: {doc.relative_to(ROOT)} → {match.group(1)}")
        elif url.fragment and target.suffix.lower() == ".md" and unquote(url.fragment) not in anchors(target):
            fail(f"Missing anchor: {doc.relative_to(ROOT)} → {match.group(1)}")
    return count


def main() -> int:
    docs = [ROOT / "README.md", *sorted(GUIDE.rglob("*.md"))]
    links = sum(check_links(doc) for doc in docs)
    registry = (ROOT / "app/src/lib/navigation.ts").read_text(encoding="utf-8")
    actual = re.findall(r"^\s*\['([^']+)',\s*'[^']+',", registry, re.MULTILINE)
    short_names = re.findall(r"^\s*\['[^']+',\s*'[^']+',\s*'([^']+)'", registry, re.MULTILINE)
    if actual != [item[0] for item in EXPECTED]:
        fail("Navigation order changed: update the handbook inventory.")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    handbook = (GUIDE / "README.md").read_text(encoding="utf-8")
    pages = list((GUIDE / "pages").glob("*.md"))
    if len(pages) != 19:
        fail(f"Expected 19 page chapters, found {len(pages)}.")
    for index, (_, slug) in enumerate(EXPECTED, 1):
        doc = GUIDE / "pages" / f"{slug}.md"
        svg = ASSETS / f"{slug}.svg"
        if not doc.exists() or not svg.exists():
            fail(f"Missing F{index} chapter or mock: {slug}")
            continue
        if not doc.read_text(encoding="utf-8").startswith(f"# F{index} —"):
            fail(f"Incorrect function label: {slug}")
        row = re.compile(rf"^\| F{index} \| .*\({re.escape('docs/guide/pages/' + slug + '.md')}\)", re.MULTILINE)
        if not row.search(readme):
            fail(f"README inventory mismatch for F{index}")
        if f"(pages/{slug}.md)" not in handbook:
            fail(f"Handbook omits F{index}")
    svgs = sorted(ASSETS.glob("*.svg"))
    if len(svgs) != 19:
        fail(f"Expected 19 synthetic SVG assets, found {len(svgs)}.")
    for path in svgs:
        raw = path.read_text(encoding="utf-8")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            fail(f"Invalid SVG {path.name}: {exc}")
            continue
        if root.tag != "{http://www.w3.org/2000/svg}svg" or root.get("viewBox") != "0 0 1280 900":
            fail(f"Unexpected SVG root or bounds: {path.name}")
        nav = root.find("{http://www.w3.org/2000/svg}g[@id='navigation']")
        labels = [] if nav is None else [node.text for node in nav.findall("{http://www.w3.org/2000/svg}text")]
        expected_labels = [label for i, short in enumerate(short_names, 1) for label in (f"F{i}", short)]
        if labels != expected_labels:
            fail(f"Top navigation does not match source registry: {path.name}")
        title = root.find("{http://www.w3.org/2000/svg}title")
        desc = root.find("{http://www.w3.org/2000/svg}desc")
        if title is None or "DEMO" not in (title.text or "") or desc is None or "invented" not in (desc.text or ""):
            fail(f"Missing accessible synthetic-data declaration: {path.name}")
        for node in root.iter():
            name = node.tag.rsplit("}", 1)[-1]
            if name in {"script", "image", "foreignObject", "a", "use", "iframe"}:
                fail(f"Unexpected executable, linked or raster content: {path.name}/{name}")
            for attr, value in node.attrib.items():
                key = attr.rsplit("}", 1)[-1].lower()
                if key.startswith("on") or key == "href" or "url(" in value.lower():
                    fail(f"Unexpected link/event reference: {path.name}/{attr}")
        if "base64" in raw.lower() or re.search(r"(?i)(?:file:|https?:)//", raw.replace("http://www.w3.org/2000/svg", "")):
            fail(f"Unexpected external content: {path.name}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"OK: {len(docs)} Markdown documents, {links} local links, 19 ordered destinations, {len(svgs)} accessible synthetic SVG assets.")
    print("This checks documentation structure; it does not certify app behavior, provider access or private-data exclusion across the repository.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
