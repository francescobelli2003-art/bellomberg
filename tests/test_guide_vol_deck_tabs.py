"""F9 handbook names the Vol Deck tabs the app actually renders (13/09, Claude Opus 5).

The C7 layout replaced the Surface, Desk and Chain-Strategie views with four tabs, and the chapter
kept describing the old ones. Text files only: no repository import.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CHAPTER = ROOT / 'docs/guide/pages/09-vol-deck.md'


def _tabs():
    page = (ROOT / 'app/src/pages/VolSurfacePage.tsx').read_text(encoding='utf-8')
    block = re.search(r"\(\[([^\]]+)\] as const\)\.map\(mode =>", page)
    assert block, 'tab list not found in VolSurfacePage.tsx'
    ids = re.findall(r"'([a-z]+)'", block.group(1))
    labels = {}
    for lang in ('en', 'it'):
        catalogue = (ROOT / f'app/src/i18n/{lang}/voldeck.ts').read_text(encoding='utf-8')
        labels[lang] = [re.search(rf"\b{i}: '([^']+)'", catalogue).group(1) for i in ids]
    return ids, labels


def _section(text, title):
    start = text.index(title)
    end = text.find('\n## ', start + len(title))
    return text[start:end if end >= 0 else len(text)]


def test_vol_deck_chapter_names_every_rendered_tab_in_both_languages():
    ids, labels = _tabs()
    assert ids == ['acquisition', 'tools', 'chain', 'laboratory']
    text = CHAPTER.read_text(encoding='utf-8')
    missing = [f'**{en}** (*{it}*)' for en, it in zip(labels['en'], labels['it'])
               if f'**{en}** (*{it}*)' not in text]
    assert missing == []


def test_vol_deck_chapter_drops_the_pre_c7_views():
    text = CHAPTER.read_text(encoding='utf-8')
    assert [s for s in ('Chain–Strategie', 'Surface and Desk', '**Surface**', '**Desk**') if s in text] == []


def test_vol_deck_chapter_declares_the_cases_where_the_page_does_not_switch_tabs():
    """The review found two false promises in the first rewrite: a 0-1 DTE expiry loaded from Chain
    never builds a surface, and a failed page stops the download instead of «completing with gaps»."""
    text = CHAPTER.read_text(encoding='utf-8')
    chain = _section(text, '## Inspect the chain and Greeks')
    assert '0–1 DTE' in chain and 'Coverage and sources' in chain
    assert 'replaces the ticker' in chain and 'can no longer be resumed' in chain
    acquisition = _section(text, '## Load the expirations you need')
    assert 'including a download with gaps' not in acquisition
    assert 'If a page fails, the download stops at that expiry' in acquisition
    assert '**Coverage and sources** (*Copertura e fonti*)' in acquisition
