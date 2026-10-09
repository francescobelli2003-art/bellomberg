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


def pl_cell(text, language):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    index = lines.index('POSIZIONI' if language == 'it' else 'POSITIONS')
    # The five real KPI values follow the five headings in the existing table.
    return lines[index + 4]


@pytest.mark.parametrize('value', [MISSING, None, float('nan'), float('inf'), -float('inf'), False],
                         ids=['missing', 'none', 'nan', 'positive_inf', 'negative_inf', 'bool'])
@pytest.mark.parametrize('language', ['it', 'en'])
def test_unavailable_total_pl_is_not_a_green_zero(tmp_path, monkeypatch, value, language):
    data = portfolio()
    if value is not MISSING:
        data['totale_pl_eur'] = value
    texts, spans = render(tmp_path, monkeypatch, data, language)
    expected = 'n.d.' if language == 'it' else 'n/a'
    assert pl_cell(texts[1], language) == expected
    cell = next(span for span in spans[1] if span['text'] == expected)
    assert cell['color'] not in (0x507B32, 0xC00000)


@pytest.mark.parametrize('value,expected', [(0, '+0 €  (+0,0%)'),
                                         (100, '+100 €  (+14,3%)'),
                                         (-100, '-100 €  (-11,1%)')])
def test_attested_pl_including_zero_keeps_amount_and_cost_basis_return(tmp_path, monkeypatch, value, expected):
    data = portfolio()
    data['totale_pl_eur'] = value
    texts, _ = render(tmp_path, monkeypatch, data)
    assert pl_cell(texts[1], 'it') == expected
    assert '1.000 €' in texts[1] and '800 €' in texts[1] and '200 €' in texts[1]


def test_undefined_pl_percentage_does_not_turn_into_zero_percent(tmp_path, monkeypatch):
    data = portfolio()
    data['totale_pl_eur'] = 800  # Attested amount, but zero cost basis.
    texts, _ = render(tmp_path, monkeypatch, data)
    cell = pl_cell(texts[1], 'it')
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
