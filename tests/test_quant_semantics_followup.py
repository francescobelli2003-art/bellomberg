"""R09/R13: quantitative meaning survives tools, frozen projections and real PDFs.

All providers and portfolio inputs below are synthetic. Run via offline_pytest.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import re

import numpy as np
import pandas as pd
import pytest

from bellomberg.core.language import language_context
from bellomberg.core import evidence_prompt_policy as ep
from bellomberg.core import quant_render_snapshot as qs
from bellomberg.portfolio import portfolio_analytics as pa
from bellomberg.portfolio import portfolio_risk as pr
from bellomberg.portfolio import portfolio_sectors as ps
from bellomberg.reporting import charts_quant as cq
from bellomberg.reporting import pdf_institutional as pi


@pytest.fixture(autouse=True)
def rates_charts_in_tmp(monkeypatch, tmp_path):
    """build_quant_appendix_v2 draws the rates charts too: OUT_DIR is computed at import
    from REPORT_DIR, so without this a bare pytest wrote <repo>/report/rates_charts
    (same cure as test_quant_snapshot_memo15.py). 09/10 (Opus 5.5)."""
    from bellomberg.reporting import charts_rates
    monkeypatch.setattr(charts_rates, 'OUT_DIR', str(tmp_path / 'rates'))


@pytest.fixture
def synthetic_risk(monkeypatch):
    dates = pd.bdate_range('2032-01-02', periods=80)
    close = pd.DataFrame({'ZZALFA': 100 * np.exp(np.sin(np.arange(80)) / 20),
                          'SPY': 100 * np.exp(np.cos(np.arange(80)) / 30)}, index=dates)
    raw = pd.concat({'Close': close}, axis=1)
    summary = {'positions': [{'ticker': 'ZZALFA', 'valore_mercato': 1000,
                              'peso_pct': 100, 'valuta': 'EUR'}],
               'totale_valore_mercato_eur': 1000}
    monkeypatch.setattr(pr, 'prezzi_speciali', lambda: {'prezzi': {'senza_yfinance': []}})
    monkeypatch.setattr(pr, 'ko_negozio_prezzi', lambda _: None)
    monkeypatch.setattr(pr, 'MemoryDB', lambda: SimpleNamespace(get_portfolio_summary=lambda: summary))
    monkeypatch.setattr(pr.yf, 'download', lambda *a, **k: raw)
    monkeypatch.setattr(pr, '_convert_returns_to_eur', lambda r, *a, **k:
                        (r, {'converted': ['ZZALFA', 'SPY'], 'local_declared': []}))
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, 'data_ticker_map', lambda tickers, **k: dict(zip(tickers, tickers)))
    with language_context('en'):
        return pr.compute_portfolio_risk(force=True)


@pytest.mark.parametrize('language', ['it', 'en'])
def test_drawdown_short_history_identifies_its_own_cause(monkeypatch, language):
    nav = {'dates': ['2032-01-02', '2032-01-03', '2032-01-04'],
           'pnl_con_dividendi_eur': [0, 2, -1], 'cost_basis_eur': [100, 100, 100],
           'proxy_prices': {'ZZOTHER': 'irrelevant other calculation'}}
    monkeypatch.setattr(pa, 'compute_nav_history', lambda **k: nav)
    with language_context(language):
        out = pa.compute_drawdowns()
    assert out['error_code'] == 'INSUFFICIENT_HISTORY'
    assert (out['calculation'], out['observations'], out['minimum_observations']) == ('drawdowns', 3, 5)
    assert 'proxy' not in out['error'].lower()
    assert '3' in out['error'] and '5' in out['error']


def test_component_var_short_history_declares_common_returns_and_minimum(monkeypatch):
    dates = pd.bdate_range('2032-01-02', periods=12)
    close = pd.DataFrame({'ZZALFA': np.arange(12) + 100., 'ZZBETA': np.arange(12) + 200.}, index=dates)
    monkeypatch.setattr(pa, 'prezzi_speciali', lambda: {'prezzi': {'senza_yfinance': []}})
    monkeypatch.setattr(pa, 'ko_negozio_prezzi', lambda _: None)
    monkeypatch.setattr(pa, 'MemoryDB', lambda: SimpleNamespace(get_portfolio_summary=lambda:
        {'positions': [{'ticker': tk, 'valore_mercato': 1000} for tk in close]}))
    monkeypatch.setattr(pa.yf, 'download', lambda *a, **k: pd.concat({'Close': close}, axis=1))
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, 'data_ticker', lambda ticker: ticker)
    out = pa.compute_var_contribution()
    assert out['error_code'] == 'INSUFFICIENT_HISTORY'
    assert (out['calculation'], out['observations'], out['minimum_observations']) == ('component_var', 11, 60)
    assert out['observation_basis'] == 'common_daily_returns'
    assert 'proxy' not in out['error'].lower()
    diagnostic = ep.quant_semantics({'var_contribution': out})
    assert diagnostic['var_contribution']['observations'] == 11
    assert diagnostic['var_contribution']['status'] == 'UNAVAILABLE'
    assert diagnostic['var_backtest']['clustering_assessed'] is False


def test_advanced_metrics_short_history_keeps_nav_cause(monkeypatch):
    from bellomberg.portfolio import advanced_metrics as am, twr_engine
    monkeypatch.setattr(twr_engine, 'compute_twr_payload', lambda: {'error': 'synthetic unavailable'})
    monkeypatch.setattr(pa, 'compute_nav_history', lambda: {'pnl_con_dividendi_eur': [1, 2, 3], 'cost_basis_eur': [100] * 3})
    out = am.portfolio_metrics()
    assert out['error_code'] == 'INSUFFICIENT_HISTORY'
    assert (out['observations'], out['minimum_observations']) == (3, 10)
    assert out['calculation'] == 'advanced_metrics_nav'


def test_backtest_short_sample_does_not_report_clustering(monkeypatch):
    from bellomberg.portfolio import var_backtest as vb
    from bellomberg.storage import memory_db
    from bellomberg.cli import price_updater
    dates = pd.bdate_range('2032-01-02', periods=80)
    close = pd.DataFrame({'ZZALFA': np.arange(80) + 100., 'ZZBETA': np.arange(80) + 200.}, index=dates)
    monkeypatch.setattr(pr, 'prezzi_speciali', lambda: {'prezzi': {'senza_yfinance': []}})
    monkeypatch.setattr(pr, 'ko_negozio_prezzi', lambda _: None)
    monkeypatch.setattr(memory_db, 'MemoryDB', lambda: SimpleNamespace(get_portfolio_summary=lambda:
        {'positions': [{'ticker': tk, 'valore_mercato': 1000, 'valuta': 'EUR'} for tk in close]}))
    monkeypatch.setattr(pr.yf, 'download', lambda *a, **k: pd.concat({'Close': close}, axis=1))
    monkeypatch.setattr(price_updater, 'data_ticker_map', lambda tickers, **k: dict(zip(tickers, tickers)))
    monkeypatch.setattr(pr, '_convert_returns_to_eur', lambda r, *a, **k: (r, {'converted': list(close)}))
    out = vb.backtest_var(window=60)
    assert out['error_code'] == 'INSUFFICIENT_HISTORY'
    assert (out['observations'], out['minimum_observations']) == (79, 120)
    assert out['calculation'] == 'var_backtest'
    assert 'var95' not in out and 'var99' not in out


@pytest.mark.parametrize('language', ['it', 'en'])
def test_effective_n_one_is_sector_concentration_not_independent_bets(tmp_path, monkeypatch, language):
    monkeypatch.setattr(ps, 'CACHE_PATH', str(tmp_path / 'sectors.json'))
    monkeypatch.setattr(ps.cl, 'carica_veicoli', lambda: {'veicoli': {}, 'origine': 'synthetic', 'motivo': None})
    summary = {'positions': [{'ticker': tk, 'valore_mercato': value}
                            for tk, value in [('ZZALFA', 100), ('ZZBETA', 300)]]}
    with language_context(language):
        out = ps.compute_sector_exposure(summary, fetch=lambda _: {'sector': 'Technology'})
    metric = out['econ_axis']['effective_n_metadata']
    assert out['econ_axis']['hhi'] == 1 and out['econ_axis']['effective_n'] == 1
    assert metric['definition'] == 'inverse_sector_hhi'
    assert metric['axis'] == 'economic_sector'
    assert metric['weight_basis'] == 'invested_market_value_eur_excluding_cash'
    assert metric['independence_assessed'] is False
    text = str(metric['interpretation']).lower()
    assert ('non misura' if language == 'it' else 'does not measure') in text
    assert ('indipendenza' if language == 'it' else 'independence') in text


def test_risk_metadata_does_not_claim_live_rf_or_completed_backtest(synthetic_risk):
    assert 'live' not in synthetic_risk['sharpe_note'].lower()
    assert 'validated by' not in synthetic_risk['var_hierarchy']['official'].lower()
    assert synthetic_risk['risk_free_status'] == 'zero_rate_convention'
    scaling = synthetic_risk['time_scaling']
    assert scaling['method'] == 'sqrt_time'
    assert scaling['assumptions_tested'] is False
    assert scaling['weekly_var_computed'] is False
    assert scaling['backtest_status'] == 'not_in_payload'
    assert 'uncorrelated_returns' in scaling['assumptions']
    assert synthetic_risk['analysis_window']['return_observations'] == 79


def test_pure_prompt_hook_preserves_policy_one_and_distinct_failures():
    assert ep.POLICY == 'weekly-evidence-prompts/1'
    priming = {'quant_advanced_metrics': qs.entry({'risk_free_used': .025,
               'risk_free_status': 'source_metadata_unavailable'}, 'preflight.synthetic'),
               'drawdowns': {'error_code': 'INSUFFICIENT_HISTORY', 'error': 'short history',
                 'calculation': 'drawdowns', 'observations': 3, 'minimum_observations': 5},
               'var_backtest': {'error_code': 'INSUFFICIENT_HISTORY', 'observations': 41,
                 'minimum_observations': 312, 'calculation': 'var_backtest'}}
    before = deepcopy(priming)
    out = ep.quant_semantics(priming)
    assert out['risk_free']['status'] == 'source_metadata_unavailable'
    assert out['drawdowns']['observations'] == 3
    assert out['var_backtest']['observations'] == 41
    assert out['var_backtest']['clustering_assessed'] is False
    assert out['var_backtest']['causal_inference_assessed'] is False
    assert priming == before
    assert 'sqrt(time)' in out['interpretation_rules']
    assert 'proxy' in out['interpretation_rules']


@pytest.mark.parametrize('status', ['source_metadata_unavailable', 'live'])
def test_risk_free_projection_keeps_status_without_promoting_scalar(status):
    advanced = {'risk_free_used': .025,
        'risk_free_status': status, 'risk_free_source': 'synthetic.rate',
        'risk_free_observed_at': '2032-04-21T09:00:00+00:00',
        'risk_free_note': 'tasso live / live rf'}
    out = ep.quant_semantics({'advanced_metrics': advanced})
    assert out['risk_free']['status'] == status
    assert out['risk_free']['source'] == 'synthetic.rate'
    assert out['risk_free']['observed_at'] == '2032-04-21T09:00:00+00:00'
    if status == 'source_metadata_unavailable':
        for language in ('it', 'en'):
            with language_context(language):
                text = ' '.join(ep.quant_semantics_notes(advanced=advanced))
            assert 'tasso live' not in text and 'live rf' not in text


def _pdf_text_and_images(path):
    import pymupdf
    with pymupdf.open(path) as doc:
        text = '\n'.join(page.get_text() for page in doc)
        for index, page in enumerate(doc):
            page.get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(str(path.with_name(f'{path.stem}-{index + 1}.png')))
    path.with_suffix('.txt').write_text(text, encoding='utf-8')
    return ' '.join(text.split())


@pytest.mark.parametrize('language', ['it', 'en'])
@pytest.mark.parametrize('nulls', [False, True])
def test_real_memo_and_appendix_keep_semantics_and_frozen_origin(tmp_path, monkeypatch, synthetic_risk, language, nulls):
    risk = deepcopy(synthetic_risk)
    if nulls:
        risk['portfolio'] = dict.fromkeys(risk['portfolio'])
    advanced = {'sharpe': None if nulls else .75, 'sortino': None,
                'risk_free_used': .025, 'risk_free_status': 'source_metadata_unavailable',
                'risk_free_source': 'synthetic.scalar', 'benchmark_ticker': 'ZZBENCH',
                'benchmark_alignment': '2032-01-02 / 2032-04-21; EUR; common dates',
                '_source': 'synthetic.frozen.advanced'}
    entries = {slot: qs.entry(None, slot) for slot in qs.SLOTS}
    entries['risk_data'] = qs.entry(risk, 'synthetic.frozen.risk')
    entries['advanced_metrics'] = qs.entry(advanced, 'synthetic.frozen.advanced')
    snapshot = {'version': qs.POLICY, 'entries': entries,
                'research_started_at': '2032-04-21T09:00:00+00:00'}
    before = deepcopy(snapshot)
    monkeypatch.setattr(cq, 'REPORT_DIR', tmp_path)
    monkeypatch.setattr(cq, 'CHART_DIR', str(tmp_path / 'charts'))
    monkeypatch.setattr(pi, 'REPORT_DIR', str(tmp_path))
    monkeypatch.setattr(pi, '_gen_charts', lambda *a: (None, None, None))
    appendix = Path(cq.build_quant_appendix_v2(quant_snapshot=snapshot,
        output_path=str(tmp_path / 'appendix.pdf'), language=language))
    memo = Path(pi.build_institutional_memo('## BLUF\nSynthetic quantitative memo.\n',
        risk_data=risk, output_path=str(tmp_path / 'memo.pdf'), title_date='2032-04-21', language=language))
    for path in (appendix, memo):
        text = _pdf_text_and_images(path)
        assert not re.search(r'\b(?:None|nan)\b', text, re.IGNORECASE)
        assert 'sqrt(time)' in text
        assert 'SPY' in text and 'EUR' in text
        assert synthetic_risk['analysis_window']['start'] in text
        assert ('frequenza osservata' if language == 'it' else 'observed frequency') in text
        assert '1 giorno su 20' not in text and '1 day in 20' not in text
        assert 'rf=0' in text
    text = _pdf_text_and_images(appendix)
    assert 'source_metadata_unavailable' in text
    assert 'tasso live' not in text.lower() and 'live rf' not in text.lower()
    assert 'ZZBENCH' in text and 'synthetic.frozen.advanced' in text
    assert snapshot == before


@pytest.mark.parametrize('language', ['it', 'en'])
def test_numeric_table_single_fault_is_visible_in_real_pdf(tmp_path, monkeypatch, language):
    entries = {slot: qs.entry(None, slot) for slot in qs.SLOTS}
    snapshot = {'version': qs.POLICY, 'entries': entries, 'research_started_at': '2032-04-21'}
    monkeypatch.setattr(cq, 'REPORT_DIR', tmp_path)
    monkeypatch.setattr(cq, 'CHART_DIR', str(tmp_path / 'charts'))
    monkeypatch.setattr(cq, '_numeric_tables', lambda *a, **k: (_ for _ in ()).throw(ValueError('synthetic fault')))
    path = Path(cq.build_quant_appendix_v2(quant_snapshot=snapshot,
        output_path=str(tmp_path / 'fault.pdf'), language=language))
    text = _pdf_text_and_images(path)
    assert 'QUANT_TABLES_UNAVAILABLE' in text and 'ValueError' in text
    assert 'synthetic fault' not in text  # exception bodies are not diagnostics for the memo


@pytest.mark.parametrize('language', ['it', 'en'])
@pytest.mark.parametrize('value', [None, 0.0])
def test_null_benchmark_metrics_and_valid_zero_in_real_pdf(tmp_path, monkeypatch, language, value):
    entries = {slot: qs.entry(None, slot) for slot in qs.SLOTS}
    advanced = {'sharpe': None, 'sortino': None, 'benchmark_ticker': 'ZZBENCH',
        'benchmark': {'beta': value, 'alpha_annual_pct': value, 'information_ratio': value},
        'risk_free_used': .025, 'risk_free_status': 'source_metadata_unavailable'}
    entries['advanced_metrics'] = qs.entry(advanced, 'synthetic.review.null_benchmark')
    snapshot = {'version': qs.POLICY, 'entries': entries, 'research_started_at': '2032-10-01'}
    before = deepcopy(snapshot)
    monkeypatch.setattr(cq, 'REPORT_DIR', tmp_path)
    monkeypatch.setattr(cq, 'CHART_DIR', str(tmp_path / 'charts'))
    path = Path(cq.build_quant_appendix_v2(quant_snapshot=snapshot,
        output_path=str(tmp_path / 'null-benchmark.pdf'), language=language))
    text = _pdf_text_and_images(path)
    assert not re.search(r'\bNone\b', text), text
    # 09/10 (Opus 5.5): absent metrics say the localized n.d./n/a, never «n/d», never
    # with a «%» glued to it, and a missing origin does not end in a double dot.
    absent = 'n.d.' if language == 'it' else 'n/a'
    assert 'n/d' not in text and absent + '%' not in text and 'n.d..' not in text, text
    assert ('CAGR ' + absent) in text and ('origine n.d.' if language == 'it' else 'origin n/a.') in text, text
    if value == 0.0:
        assert text.count('0.0') >= 3, text
    assert snapshot == before


def _table_texts(flow):
    from reportlab.platypus import Table
    out = []
    for item in flow:
        if isinstance(item, Table):
            out.extend(str(cell) for row in item._cellvalues for cell in row if isinstance(cell, str))
    return out


@pytest.mark.parametrize('language', ['it', 'en'])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), np.float64('nan')],
                         ids=['nan', 'inf', 'minus_inf', 'numpy_nan'])
def test_nan_and_inf_metrics_are_unavailable_not_nan_percent(language, bad):
    """10/10 (Opus 5.5): NaN/inf printed «nan%»/«inf» in _fmt, gv and bv; now the
    localized n.d./n/a, and no reading is written about a non-number."""
    absent = 'n.d.' if language == 'it' else 'n/a'
    risk = {'portfolio': {'vol_annual_pct': bad, 'sharpe': bad, 'beta_vs_spy': bad,
                          'max_dd_1y_pct': 12.34567}}
    am = {'cagr_pct': bad, 'max_drawdown_pct': bad, 'sharpe': bad, 'win_rate_pct': 56.78901,
          'benchmark_ticker': 'ZZBENCH',
          'benchmark': {'beta': bad, 'alpha_annual_pct': bad, 'information_ratio': bad}}
    with language_context(language):
        assert cq._fmt(bad, '%') == absent
        flow = cq._numeric_tables(risk, {}, {}, advanced_metrics_snapshot=am)
    cells = _table_texts(flow)
    assert cells and not any(re.search(r'\b(?:nan|inf)\b', c, re.IGNORECASE) for c in cells), cells
    assert absent + '%' not in cells and cells.count(absent) >= 8, cells
    assert '56.78901%' in cells  # a real number keeps its suffix
    from reportlab.platypus import Paragraph
    prose = ' '.join(item.getPlainText() for item in flow if isinstance(item, Paragraph))
    for sentence in ('Lo Sharpe a', 'A Sharpe ratio of', 'Con beta', 'With a beta of'):
        assert sentence not in prose, prose
