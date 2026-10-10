"""R13: unavailable portfolio figures and cover bounds in the actual PDF.

Only synthetic account data; use tools/testing/offline_pytest.py. PDF and PNG
artifacts stay in tmp_path, with the existing real chart and PDF renderers.
"""
from copy import deepcopy
from pathlib import Path

import pymupdf
import pytest

from bellomberg.core.language import language_context
from bellomberg.reporting import charts_institutional as ci
from bellomberg.reporting import pdf_institutional as pi


MISSING = object()
MANDATE_HASH = '8' * 64
MEMO = ('# Synthetic weekly memo\n'
        '[MANDATO PM: impronta ' + MANDATE_HASH + '; origine esempio; dichiarato_il 2032-04-21]\n'
        '## Ricerca\nDati sintetici; nessuna istruzione operativa.\n')


def portfolio():
    return {'positions': [{'ticker': 'ZZALFA', 'peso_pct': 50, 'pl_pct': 0},
                          {'ticker': 'ZZBETA', 'peso_pct': 50, 'pl_pct': 0}],
            'n_positions': 2, 'totale_valore_mercato_eur': 800,
            'cash_disponibile_eur': 200, 'nav_total_eur': 1000}


def render(tmp_path, monkeypatch, data, language='it', memo=MEMO):
    monkeypatch.setattr(pi, 'REPORT_DIR', str(tmp_path))
    monkeypatch.setattr(ci, 'DIR', str(tmp_path / 'charts'))
    out = tmp_path / 'memo.pdf'
    before = deepcopy(data)
    with language_context(language):
        result = pi.build_institutional_memo(memo, portfolio_data=data,
            output_path=str(out), title_date='2032-04-21')
    assert Path(result) == out
    assert data == before
    with pymupdf.open(out) as doc:
        assert len(doc) >= 2
        texts = [page.get_text() for page in doc]
        spans = [[span for block in page.get_text('dict')['blocks']
                  for line in block.get('lines', []) for span in line['spans']]
                 for page in doc]
        for n in (0, 1):
            doc[n].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(tmp_path / f'page-{n + 1}.png')
    return texts, spans


# Independent oracles for the KPI strip (10/10, Opus 5.5): literal numbers, never read
# back from pdf_institutional. A4 is 595.276 pt wide, the strip spans it minus 2 cm per
# side, five equal columns: 5 x 96.378 = 481.89 pt. Cell padding 8 left / 6 right; a
# value on one line is never below 8 pt, a wrapped value is set at exactly 8 pt.
KPI_TABLE_WIDTH = 481.89
KPI_PAD_LEFT, KPI_PAD_RIGHT = 8, 6
KPI_MIN_ONE_LINE = 8
KPI_WRAPPED_SIZE = 8
KPI_FULL_SIZE = 10
HEADER_GREY = (0xF2 / 255, 0xF4 / 255, 0xF7 / 255)


def kpi_geometry(pdf_path):
    """(left, column width, cells): each cell = its value spans top to bottom.
    The column grid comes from the drawn grey header band of the strip (the only
    481.89 pt wide band in that grey), not from the code's padding constants."""
    with pymupdf.open(pdf_path) as doc:
        page = doc[1]
        bands = [d['rect'] for d in page.get_drawings()
                 if d.get('fill') and all(abs(a - b) < .01 for a, b in zip(d['fill'], HEADER_GREY))
                 and abs(d['rect'].width - KPI_TABLE_WIDTH) < .5]
        assert bands, 'KPI header band not found'
        band = min(bands, key=lambda r: r.y0)
        spans = [span for block in page.get_text('dict')['blocks']
                 for line in block.get('lines', []) for span in line['spans']]
    left, width = band.x0, band.width / 5
    below = sorted((s for s in spans if s['text'].strip() and s['bbox'][1] > band.y1 - .5
                    and left - 1 < s['bbox'][0] < band.x1), key=lambda s: s['bbox'][1])
    row, bottom = [], band.y1
    for s in below:  # the strip's value row ends at the first vertical gap (spacer)
        if s['bbox'][1] > bottom + 10:
            break
        row.append(s)
        bottom = max(bottom, s['bbox'][3])
    cells = [[] for _ in range(5)]
    for s in row:
        cells[int((s['bbox'][0] - left) // width)].append(s)
    return left, width, cells


def pl_cell(pdf_path):
    """Every line of the P/L cell, top to bottom (one or more after wrapping)."""
    return [s['text'] for s in kpi_geometry(pdf_path)[2][3]]


@pytest.mark.parametrize('value', [MISSING, None, float('nan'), float('inf'), -float('inf'), False],
                         ids=['missing', 'none', 'nan', 'positive_inf', 'negative_inf', 'bool'])
@pytest.mark.parametrize('language', ['it', 'en'])
def test_unavailable_total_pl_is_not_a_green_zero(tmp_path, monkeypatch, value, language):
    data = portfolio()
    if value is not MISSING:
        data['totale_pl_eur'] = value
    texts, spans = render(tmp_path, monkeypatch, data, language)
    expected = 'n.d.' if language == 'it' else 'n/a'
    assert pl_cell(tmp_path / 'memo.pdf') == [expected]
    cell = next(span for span in spans[1] if span['text'] == expected)
    assert cell['color'] not in (0x507B32, 0xC00000)


def use_font(monkeypatch, font):
    """'dejavu' = the font the Linux CI picks (pdf_institutional._register_fonts,
    DejaVu branch), taken from matplotlib's bundled copy so Windows measures it too."""
    if font == 'registered':
        return
    import importlib.util
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    ttf = Path(importlib.util.find_spec('matplotlib').origin).parent / 'mpl-data' / 'fonts' / 'ttf'
    for name, file in (('ZZDV', 'DejaVuSans.ttf'), ('ZZDVB', 'DejaVuSans-Bold.ttf'),
                       ('ZZDVI', 'DejaVuSans-Oblique.ttf')):
        pdfmetrics.registerFont(TTFont(name, str(ttf / file)))
    pdfmetrics.registerFontFamily('ZZDV', normal='ZZDV', bold='ZZDVB', italic='ZZDVI', boldItalic='ZZDVB')
    monkeypatch.setattr(pi, '_register_fonts', lambda: ('ZZDV', 'ZZDVB', 'ZZDVI'))


def assert_whole_groups(lines):
    """A wrap never falls inside a group of digits: a line never starts with a
    separator, and two lines never meet digit-to-digit."""
    for prev, nxt in zip(lines, lines[1:]):
        assert nxt[:1] not in '.,', lines
        assert not (prev.rstrip()[-1:].isdigit() and nxt.lstrip()[:1].isdigit()), lines


BASE_AMOUNTS = dict(totale_valore_mercato_eur=1234567, cash_disponibile_eur=2345678,
                    nav_total_eur=3580245)
BASE_TEXTS = ['3.580.245 €', '1.234.567 €', '2.345.678 €']


@pytest.mark.parametrize('font', ['registered', 'dejavu'])
@pytest.mark.parametrize('fields,texts', [
    ({'totale_pl_eur': 0}, BASE_TEXTS + ['+0 €  (+0,0%)']),
    ({'totale_pl_eur': 100}, BASE_TEXTS + ['+100 €  (+0,0%)']),
    ({'totale_pl_eur': -100}, BASE_TEXTS + ['-100 €  (-0,0%)']),
    ({'totale_pl_eur': 123456}, BASE_TEXTS + ['+123.456 €  (+11,1%)']),
    ({'totale_pl_eur': -9876543}, BASE_TEXTS + ['-9.876.543 €  (-88,9%)']),
    ({'totale_pl_eur': None}, BASE_TEXTS + ['n.d.']),
    # Arial fits this on one line only at ~7.0 pt, DejaVu at ~5.6: below 8 it must wrap.
    ({'totale_pl_eur': 1234567, 'totale_valore_mercato_eur': 1334572},
     ['3.580.245 €', '1.334.572 €', '2.345.678 €', '+1.234.567 €  (+1234,5%)']),
    # A wide NAV (DejaVu ~9.0 pt) and huge NAV/invested/cash (Arial ~8.0, DejaVu wraps):
    # the reduction is not a P/L privilege.
    ({'totale_pl_eur': 0, 'nav_total_eur': 1234567890},
     ['1.234.567.890 €', '1.234.567 €', '2.345.678 €', '+0 €  (+0,0%)']),
    ({'totale_pl_eur': 0, 'nav_total_eur': 123456789012345,
      'totale_valore_mercato_eur': 123456789012345, 'cash_disponibile_eur': 987654321098765},
     ['123.456.789.012.345 €', '123.456.789.012.345 €', '987.654.321.098.765 €', '+0 €  (+0,0%)']),
], ids=['pl_zero', 'pl_plus', 'pl_minus', 'pl_six_digits', 'pl_seven_digits', 'pl_missing',
        'pl_between_7_and_8_pt', 'nav_ten_digits', 'all_fifteen_digits'])
def test_every_kpi_value_stays_inside_its_own_cell(tmp_path, monkeypatch, font, fields, texts):
    """Fix 09/10 + 10/10 (Opus 5.5): the P/L cell used to run into POSIZIONI
    ('(+32,8%)3'); every KPI value now fits its own cell, whole, at a legible size."""
    use_font(monkeypatch, font)
    data = portfolio()
    data.update(BASE_AMOUNTS)
    data.update(fields)
    page_texts, _ = render(tmp_path, monkeypatch, data)
    left, width, cells = kpi_geometry(tmp_path / 'memo.pdf')
    assert abs(width - KPI_TABLE_WIDTH / 5) < .1
    expected = texts + ['2']
    one_line_sizes = [cell[0]['size'] for cell in cells if len(cell) == 1]
    for i, cell in enumerate(cells):
        assert cell, i
        for s in cell:
            assert s['bbox'][0] >= left + i * width + KPI_PAD_LEFT - .5, (i, s['text'], s['bbox'])
            assert s['bbox'][2] <= left + (i + 1) * width - KPI_PAD_RIGHT + .5, (i, s['text'], s['bbox'])
        lines = [s['text'] for s in cell]
        # Never truncated: the whole value, on one line or more.
        assert ''.join(lines).replace(' ', '') == expected[i].replace(' ', ''), (i, lines)
        if len(cell) == 1:
            assert KPI_MIN_ONE_LINE - .01 <= cell[0]['size'] <= KPI_FULL_SIZE + .01, (i, cell[0]['size'])
            assert lines == [expected[i]]
        else:
            # Wrapped: exactly the declared 8 pt, never above another KPI's size.
            assert all(abs(s['size'] - KPI_WRAPPED_SIZE) < .01 for s in cell), (i, [s['size'] for s in cell])
            assert all(s['size'] <= min(one_line_sizes) + .01 for s in cell), (i, one_line_sizes)
            assert_whole_groups(lines)
    lines = [line.strip() for line in page_texts[1].splitlines() if line.strip()]
    assert not any(line.endswith(')2') or line.endswith(') 2') for line in lines), lines


@pytest.mark.parametrize('font', ['registered', 'dejavu'])
def test_kpi_case_between_7_and_8_pt_really_wraps(tmp_path, monkeypatch, font):
    """The oracle is the literal 8: a P/L that one line could hold at ~7.0 pt (Arial)
    is set on two lines at 8 pt, amount above percentage."""
    use_font(monkeypatch, font)
    data = portfolio()
    data.update(BASE_AMOUNTS, totale_pl_eur=1234567, totale_valore_mercato_eur=1334572)
    render(tmp_path, monkeypatch, data)
    assert pl_cell(tmp_path / 'memo.pdf') == ['+1.234.567 €', '(+1234,5%)']


@pytest.mark.parametrize('text,font_name', [
    ('123.456.789.012.345.678 €', 'Helvetica-Bold'),
    ('+123.456.789.012 €  (+123456,7%)', 'Helvetica-Bold'),
    ('1234567890123456789012345 €', 'Helvetica-Bold')])
def test_kpi_fit_never_goes_below_a_legible_size_and_keeps_every_digit(text, font_name):
    """Before 10/10 an amount without parentheses shrank without limit (15 digits
    6.3 pt with DejaVu, tending to 0). Now: never below 8 pt (above the 6.5 pt floor),
    wrapped after a thousands separator; a run without separators (pathological) is the
    only thing split elsewhere, still whole."""
    from reportlab.pdfbase import pdfmetrics
    width = KPI_TABLE_WIDTH / 5 - KPI_PAD_LEFT - KPI_PAD_RIGHT
    out, size = pi._fit_kpi_value(text, font_name, width)
    assert 6.5 <= KPI_WRAPPED_SIZE == size
    lines = out.split('\n')
    assert len(lines) > 1
    assert ''.join(lines).replace(' ', '') == text.replace(' ', '')
    assert all(pdfmetrics.stringWidth(line, font_name, size) <= width + .01 for line in lines)
    if '.' in text.split()[0]:
        assert_whole_groups(lines)


ACTION_MEMO = ('# Synthetic weekly memo\n## ACTION TABLE\n'
               '| Azione | Ticker | EUR | Timing | Confidence |\n|---|---|---|---|---|\n'
               '| BUY | ZZALFA | 1.234.567.890 € | Q4 sintetico | alta |\n'
               '| SELL | ZZBETA | 1.234.567 | Q4 sintetico | media |\n'
               '| HOLD | ZZGAMMA | 0 (incasso ~12.345) | Q4 sintetico | bassa |\n'
               '## Ricerca\nDati sintetici.\n')
ACTION_AMOUNTS = ['1.234.567.890 €', '1.234.567', '0 (incasso ~12.345)']
ACTION_HEADER_BLACK = (0x05 / 255, 0x06 / 255, 0x08 / 255)
CM = 72 / 2.54


@pytest.mark.parametrize('font', ['registered', 'dejavu'])
def test_action_table_amount_is_never_split_inside_its_digits(tmp_path, monkeypatch, font):
    """Fix 10/10 (Opus 5.5): the 1.5 cm EUR column broke '1.234.567' / '.890' with
    splitLongWords. Now the amount shrinks (never below 6.5 pt) or wraps after a
    thousands separator, whole and inside its column."""
    use_font(monkeypatch, font)
    data = portfolio()
    data['totale_pl_eur'] = 0
    render(tmp_path, monkeypatch, data, memo=ACTION_MEMO)
    with pymupdf.open(tmp_path / 'memo.pdf') as doc:
        found = None
        for page in doc:
            bands = [d['rect'] for d in page.get_drawings()
                     if d.get('fill') and all(abs(a - b) < .01 for a, b in zip(d['fill'], ACTION_HEADER_BLACK))
                     and abs(d['rect'].width - 17 * CM) < .5]
            if bands:
                found = page, min(bands, key=lambda r: r.y0)
                break
        assert found, 'action table header not found'
        page, band = found
        spans = [span for block in page.get_text('dict')['blocks']
                 for line in block.get('lines', []) for span in line['spans']]
    col_left = band.x0 + (2.1 + 2.6) * CM
    col_right = col_left + 1.5 * CM
    column = sorted((s for s in spans if s['text'].strip() and s['bbox'][1] > band.y1 - .5
                     and col_left - 1 < s['bbox'][0] < col_right), key=lambda s: s['bbox'][1])
    lines = [s['text'] for s in column][:sum(len(a) for a in ACTION_AMOUNTS)]
    joined = ''.join(lines).replace(' ', '')
    assert joined.startswith(''.join(ACTION_AMOUNTS).replace(' ', '')), lines
    used = []
    for amount in ACTION_AMOUNTS:  # regroup the column's lines row by row
        target, acc = amount.replace(' ', ''), []
        while ''.join(acc).replace(' ', '') != target:
            acc.append(lines[len(used) + len(acc)])
        used.extend(acc)
        if amount[:1].isdigit() and '(' not in amount:
            assert_whole_groups(acc)
    for s in column[:len(used)]:
        assert s['bbox'][2] <= col_right - 4 + .5, (s['text'], s['bbox'], col_right)
        assert s['size'] >= 6.5 - .01, (s['text'], s['size'])


@pytest.mark.parametrize('font', ['registered', 'dejavu'])
@pytest.mark.parametrize('value,expected', [(0, '+0 €  (+0,0%)'),
                                         (100, '+100 €  (+14,3%)'),
                                         (-100, '-100 €  (-11,1%)')])
def test_attested_pl_including_zero_keeps_amount_and_cost_basis_return(tmp_path, monkeypatch, value, expected, font):
    use_font(monkeypatch, font)
    data = portfolio()
    data['totale_pl_eur'] = value
    texts, _ = render(tmp_path, monkeypatch, data)
    assert pl_cell(tmp_path / 'memo.pdf') == [expected]
    assert '1.000 €' in texts[1] and '800 €' in texts[1] and '200 €' in texts[1]


def test_undefined_pl_percentage_does_not_turn_into_zero_percent(tmp_path, monkeypatch):
    data = portfolio()
    data['totale_pl_eur'] = 800  # Attested amount, but zero cost basis.
    texts, _ = render(tmp_path, monkeypatch, data)
    cell = ' '.join(pl_cell(tmp_path / 'memo.pdf'))
    assert '+800 €' in cell and 'n.d.' in cell and '0,0%' not in cell


@pytest.mark.parametrize('weights,available', [(None, 0), ([None, None], 0),
    ([50, None], 1), ([float('nan'), 50], 1), ([False, 50], 1)])
@pytest.mark.parametrize('language', ['it', 'en'])
def test_positions_without_attested_weights_declare_gap_not_zero_positions(
        tmp_path, monkeypatch, weights, available, language):
    data = portfolio()
    data['totale_pl_eur'] = 0
    for i, pos in enumerate(data['positions']):
        if weights is None:
            pos.pop('peso_pct')
        else:
            pos['peso_pct'] = weights[i]
    texts, _ = render(tmp_path, monkeypatch, data, language)
    cover = ' '.join(texts[0].split())
    assert ('Allocazione n.d.' if language == 'it' else 'Allocation n/a') in cover
    assert (f'{available} su 2 posizioni' if language == 'it' else f'{available} of 2 positions') in cover
    assert 'POSIZIONI' in texts[1] if language == 'it' else 'POSITIONS' in texts[1]


def test_attested_allocation_reaches_the_real_chart_without_reweighting(tmp_path, monkeypatch):
    data = portfolio()
    data['totale_pl_eur'] = 0
    data['positions'][0]['peso_pct'] = 40
    data['positions'][1]['peso_pct'] = 60
    observed = []
    original = ci.donut_chart
    def traced(items, *args, **kwargs):
        observed.append(deepcopy(items))
        return original(items, *args, **kwargs)
    monkeypatch.setattr(ci, 'donut_chart', traced)
    texts, _ = render(tmp_path, monkeypatch, data)
    assert observed == [[('ZZALFA', 40), ('ZZBETA', 60)]]
    assert 'Allocazione n.d.' not in texts[0]
    assert list((tmp_path / 'charts').glob('donut_*.png'))


def test_long_mandate_hash_stays_whole_and_inside_cover_column(tmp_path, monkeypatch):
    data = portfolio()
    data['totale_pl_eur'] = 0
    texts, spans = render(tmp_path, monkeypatch, data)
    # The text column ends where the existing amber separator starts.
    right = pi.W * .38 - .11 * pi.cm
    cover_lines = [s for s in spans[0] if abs(s['origin'][0] - 1.5 * pi.cm) < .1]
    assert cover_lines
    assert MANDATE_HASH in ''.join(s['text'] for s in cover_lines).replace(' ', '')
    assert all(s['bbox'][2] <= right + .3 for s in cover_lines), [
        (s['text'], s['bbox'][2], right) for s in cover_lines if s['bbox'][2] > right + .3]
    assert MANDATE_HASH in ''.join(texts[1].split())


@pytest.mark.parametrize('value,center,kpi', [
    (10000, 'NAV\n10k€', '10.000 €'), (0, 'NAV\n0k€', '0 €'),
    (MISSING, 'NAV\nn.d.', 'n.d.'), (None, 'NAV\nn.d.', 'n.d.'),
    (float('nan'), 'NAV\nn.d.', 'n.d.'), (float('inf'), 'NAV\nn.d.', 'n.d.'),
    (False, 'NAV\nn.d.', 'n.d.')],
    ids=['total_not_invested', 'zero', 'missing', 'none', 'nan', 'inf', 'bool'])
def test_chart_and_kpi_nav_use_only_attested_total(tmp_path, monkeypatch, value, center, kpi):
    data = portfolio()
    data.update(totale_valore_mercato_eur=4000, cash_disponibile_eur=6000, totale_pl_eur=0)
    if value is MISSING:
        data.pop('nav_total_eur')
    else:
        data['nav_total_eur'] = value
    observed = []
    original = ci.donut_chart
    def traced(items, actual_center, *args, **kwargs):
        observed.append(actual_center)
        return original(items, actual_center, *args, **kwargs)
    monkeypatch.setattr(ci, 'donut_chart', traced)
    texts, _ = render(tmp_path, monkeypatch, data)
    assert observed == [center]
    lines = [line.strip() for line in texts[1].splitlines() if line.strip()]
    index = lines.index('POSIZIONI')
    assert lines[index + 1:index + 4] == [kpi, '4.000 €', '6.000 €']


@pytest.mark.parametrize('field,offset', [('totale_valore_mercato_eur', 2), ('cash_disponibile_eur', 3)])
@pytest.mark.parametrize('value', [MISSING, None, float('nan'), float('inf'), False, 0],
                         ids=['missing', 'none', 'nan', 'inf', 'bool', 'zero'])
def test_invested_and_cash_kpi_do_not_invent_zero(tmp_path, monkeypatch, field, offset, value):
    data = portfolio()
    data['totale_pl_eur'] = 0
    if value is MISSING:
        data.pop(field)
    else:
        data[field] = value
    texts, _ = render(tmp_path, monkeypatch, data)
    lines = [line.strip() for line in texts[1].splitlines() if line.strip()]
    index = lines.index('POSIZIONI')
    assert lines[index + offset] == ('0 €' if type(value) is int and value == 0 else 'n.d.')


@pytest.mark.parametrize('data,expected,gap', [
    (None, ['n.d.'] * 5, 'posizioni non disponibili'),
    ({'positions': [], 'nav_total_eur': 0, 'totale_valore_mercato_eur': 0,
      'cash_disponibile_eur': 0, 'totale_pl_eur': 0},
     ['0 €', '0 €', '0 €', '+0 €  (n.d.)', '0'], '0 posizioni')], ids=['unavailable', 'attested_empty'])
def test_unavailable_portfolio_is_distinct_from_attested_empty(tmp_path, monkeypatch, data, expected, gap):
    texts, _ = render(tmp_path, monkeypatch, data)
    lines = [line.strip() for line in texts[1].splitlines() if line.strip()]
    index = lines.index('POSIZIONI')
    assert lines[index + 1:index + 6] == expected
    assert gap in ' '.join(texts[0].split())


def test_wide_normal_bullet_stays_inside_existing_column(tmp_path, monkeypatch):
    phrase = 'Monitoriamo WWWWWWWW e MMMMMMMM nelle prossime settimane con prudenza.'
    texts, spans = render(tmp_path, monkeypatch, portfolio(), memo='# BLUF\n' + phrase + '\n## Corpo\nDettagli.')
    cover_lines = [s for s in spans[0] if abs(s['origin'][0] - 1.5 * pi.cm) < .1]
    assert phrase in ' '.join(s['text'] for s in cover_lines)
    assert all(s['bbox'][2] <= pi.W * .38 - .11 * pi.cm + .3 for s in cover_lines)
