"""Synthetic observations exercise identity, dates and the regression contract."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

import pandas as pd
import pytest

DAY = '2026-02-03'
NOW = datetime(2026, 2, 3, 18, tzinfo=timezone.utc)


def histories():
    days = pd.bdate_range('2021-02-03', '2026-02-02')
    market, stock = 100., 80.
    rows = {'SYNTH': [], '^GSPC': []}
    for i, day in enumerate(days):
        if i:
            change = (.01, -.02, .004, .015)[i % 4]
            market *= 1 + change
            stock *= 1 + 2 * change + .001
        for name, price in [('SYNTH', stock), ('^GSPC', market)]:
            rows[name].append({'date': day.date().isoformat(), 'adjusted_close': price})
    return {name: {'symbol': name, 'currency': 'USD', 'price_field': 'Adj Close',
                  'observations': values} for name, values in rows.items()}


def collect(tmp_path, values=None, **kwargs):
    from bellomberg.valuation.beta_reference_evidence import collect_beta_reference
    values = values if values is not None else histories()
    calls = []
    def fetch(symbol, start, end):
        calls.append((symbol, start, end))
        return deepcopy(values[symbol])
    result = collect_beta_reference('SYNTH', as_of=DAY, currency='USD',
        archive_root=tmp_path, fetch=fetch, now=NOW, **kwargs)
    return result, calls


def test_beta_is_a_replayable_reference_with_explicit_adjustment_convention(tmp_path):
    from bellomberg.valuation.beta_reference_evidence import normalize_beta_reference
    result, calls = collect(tmp_path)
    assert result['status'] == 'ready' and len(result['documents']) == 1
    doc = result['documents'][0]; data = json.loads(doc['text'])
    assert data['regression']['beta'] == pytest.approx(2, abs=1e-12)
    assert data['regression']['n_obs'] == len(histories()['SYNTH']['observations']) - 1
    assert data['regression']['last_observation'] == '2026-02-02'
    assert calls == [('SYNTH', '2021-02-03', DAY), ('^GSPC', '2021-02-03', DAY)]
    assert doc['published_at'] is None and doc['available_at'] == DAY
    assert 'price index' in data['limitation'] and 'forward beta' in data['limitation']
    path, = tmp_path.glob('beta-*.json'); raw = path.read_bytes()
    assert sha256(raw).hexdigest() == doc['document_sha256']
    assert normalize_beta_reference(raw, as_of=DAY, retrieval=doc['retrieval']) == doc


@pytest.mark.parametrize('fault', ['wrong_symbol', 'wrong_currency', 'unadjusted', 'duplicate',
    'future_bar', 'stale', 'short_window', 'nonfinite', 'zero_variance'])
def test_unqualified_history_never_becomes_a_default_beta(tmp_path, fault):
    values = histories(); leg = values['SYNTH']; rows = leg['observations']
    if fault == 'wrong_symbol': leg['symbol'] = 'OTHER'
    elif fault == 'wrong_currency': leg['currency'] = 'EUR'
    elif fault == 'unadjusted': leg['price_field'] = 'Close'
    elif fault == 'duplicate': rows.append(deepcopy(rows[-1]))
    elif fault == 'future_bar': rows[-1]['date'] = DAY
    elif fault == 'stale': leg['observations'] = rows[:-10]
    elif fault == 'short_window': leg['observations'] = rows[200:]
    elif fault == 'nonfinite': rows[-1]['adjusted_close'] = float('inf')
    elif fault == 'zero_variance':
        for row in values['^GSPC']['observations']: row['adjusted_close'] = 100.
    result, _ = collect(tmp_path, values)
    assert result['status'] == 'incomplete' and not result['documents'] and result['issues']


def test_blank_last_session_is_preserved_and_declared_without_filling(tmp_path):
    values = histories()
    for leg in values.values(): leg['observations'][-1]['adjusted_close'] = None
    result, _ = collect(tmp_path, values)
    assert result['status'] == 'partial' and result['issues']
    doc, = result['documents']; data = json.loads(doc['text'])
    assert data['regression']['last_observation'] == '2026-01-30'
    assert data['coverage']['missing_observations'] == {'SYNTH':['2026-02-02'], '^GSPC':['2026-02-02']}
    assert data['regression']['n_obs'] == len(histories()['SYNTH']['observations']) - 2
    snapshot = json.loads(next(tmp_path.glob('beta-*.json')).read_text())
    assert snapshot['series']['SYNTH']['observations'][-1]['adjusted_close'] is None


def test_gapped_legs_use_identical_intervals_without_forward_fill(tmp_path):
    import numpy as np
    values = histories(); del values['SYNTH']['observations'][500]
    result, _ = collect(tmp_path, values)
    data = json.loads(result['documents'][0]['text'])
    prices = pd.DataFrame({k: {r['date']:r['adjusted_close'] for r in v['observations']} for k,v in values.items()}).dropna().sort_index()
    returns = prices.pct_change(fill_method=None).dropna()
    expected = np.cov(returns['SYNTH'], returns['^GSPC'])
    assert data['regression']['beta'] == pytest.approx(expected[0,1]/expected[1,1])
    assert data['coverage']['unpaired_dates'] == 1
    assert data['regression']['n_obs'] == len(returns)


def test_currency_failure_does_not_fetch_or_substitute_usd(tmp_path):
    from bellomberg.valuation.beta_reference_evidence import collect_beta_reference
    result = collect_beta_reference('SYNTH', as_of=DAY, currency='EUR', archive_root=tmp_path,
        fetch=lambda *a: pytest.fail('unsupported currency must not fetch'), now=NOW)
    assert result['status'] == 'incomplete' and not result['documents']


def test_normalizer_rejects_changed_snapshot_or_backdated_availability(tmp_path):
    from bellomberg.valuation.beta_reference_evidence import normalize_beta_reference
    result, _ = collect(tmp_path); doc = result['documents'][0]
    raw = next(tmp_path.glob('beta-*.json')).read_bytes()
    with pytest.raises(ValueError, match='SHA256'):
        normalize_beta_reference(raw+b' ', as_of=DAY, retrieval=doc['retrieval'])
    with pytest.raises(ValueError):
        normalize_beta_reference(raw, as_of='2026-02-02', retrieval=doc['retrieval'])


def test_default_provider_path_preserves_nan_as_a_declared_blank(tmp_path, monkeypatch):
    from bellomberg.valuation.beta_reference_evidence import collect_beta_reference
    values = histories(); calls = []
    class Instrument:
        def __init__(self, symbol): self.symbol = symbol
        @property
        def history_metadata(self): return {'symbol':self.symbol, 'currency':'USD'}
        def history(self, **kwargs):
            calls.append(kwargs)
            rows = values[self.symbol]['observations']
            series = [row['adjusted_close'] for row in rows]; series[-1] = float('nan')
            return pd.DataFrame({'Adj Close':series}, index=pd.to_datetime([r['date'] for r in rows]))
    monkeypatch.setattr('yfinance.Ticker', Instrument)
    result = collect_beta_reference('SYNTH', currency='USD', as_of=DAY, archive_root=tmp_path, now=NOW)
    assert result['status'] == 'partial' and result['documents']
    assert all(row == {'start':'2021-02-03','end':DAY,'auto_adjust':False,'actions':False,'raise_errors':True} for row in calls)
    snapshot = json.loads(next(tmp_path.glob('beta-*.json')).read_text())
    assert all(leg['observations'][-1]['adjusted_close'] is None for leg in snapshot['series'].values())


def euro_histories():
    values = histories(); fx = []
    for index, row in enumerate(values['SYNTH']['observations']):
        rate = 1.1 + (index % 9) * .013
        row['adjusted_close'] /= rate
        fx.append({'date': row['date'], 'close': rate})
    values['SYNTH']['currency'] = 'EUR'
    values['EURUSD=X'] = {'symbol': 'EURUSD=X', 'currency': 'USD', 'price_field': 'Close', 'observations': fx}
    return values


def test_euro_listing_beta_uses_same_date_usd_conversion_and_retains_all_legs(tmp_path):
    from bellomberg.valuation.beta_reference_evidence import normalize_beta_reference
    result, calls = collect(tmp_path, euro_histories())
    assert result['status'] == 'ready', result
    doc, = result['documents']; body = json.loads(doc['text'])
    assert body['regression']['beta'] == pytest.approx(2, abs=1e-12)
    assert calls[-1] == ('EURUSD=X', '2021-02-03', DAY)
    assert body['currency_conversion']['source_currency'] == 'EUR'
    assert body['currency_conversion']['rate_unit'] == 'USD per EUR'
    assert 'different closing times' in body['limitation']
    raw = next(tmp_path.glob('beta-*.json')).read_bytes()
    snapshot = json.loads(raw)
    assert set(snapshot['series']) == {'SYNTH', '^GSPC', 'EURUSD=X'}
    assert snapshot['series']['SYNTH']['currency'] == 'EUR'
    assert normalize_beta_reference(raw, as_of=DAY, retrieval=doc['retrieval']) == doc


@pytest.mark.parametrize('fault', ['inverse', 'currency', 'field', 'missing', 'duplicate', 'stale',
                                  'short', 'nonpositive', 'future_value', 'extra_leg', 'wrong_direction'])
def test_unqualified_fx_cannot_become_a_usd_beta(tmp_path, fault):
    from bellomberg.valuation.beta_reference_evidence import normalize_beta_reference
    values = euro_histories(); fx = values['EURUSD=X']; rows = fx['observations']
    if fault == 'inverse': fx['symbol'] = 'USDEUR=X'
    elif fault == 'currency': fx['currency'] = 'EUR'
    elif fault == 'field': fx['price_field'] = 'Adj Close'
    elif fault == 'missing': del values['EURUSD=X']
    elif fault == 'duplicate': rows.append(deepcopy(rows[0]))
    elif fault == 'stale': fx['observations'] = rows[:-10]
    elif fault == 'short': fx['observations'] = rows[300:]
    elif fault == 'nonpositive': rows[-1]['close'] = 0
    elif fault == 'future_value': rows.append({'date': DAY, 'close': 1.1})
    if fault in ('extra_leg', 'wrong_direction'):
        result, _ = collect(tmp_path, values); doc, = result['documents']
        snapshot = json.loads(next(tmp_path.glob('beta-*.json')).read_bytes())
        if fault == 'extra_leg': snapshot['series']['UNKNOWN'] = deepcopy(fx)
        else: snapshot['currency_conversion']['rate_unit'] = 'EUR per USD'
        raw = json.dumps(snapshot).encode(); retrieval = {**doc['retrieval'], 'document_sha256': sha256(raw).hexdigest()}
        with pytest.raises(ValueError): normalize_beta_reference(raw, as_of=DAY, retrieval=retrieval)
    else:
        result, _ = collect(tmp_path, values)
        assert result['status'] == 'incomplete' and not result['documents'] and result['issues']


def test_missing_fx_dates_are_declared_and_never_forward_filled(tmp_path):
    import numpy as np
    values = euro_histories(); del values['EURUSD=X']['observations'][500]
    result, _ = collect(tmp_path, values)
    assert result['status'] == 'partial' and result['issues']
    body = json.loads(result['documents'][0]['text'])
    rates = {r['date']: r['close'] for r in values['EURUSD=X']['observations']}
    prices = pd.DataFrame({k: {r['date']: r['adjusted_close'] * (rates[r['date']] if k == 'SYNTH' else 1)
        for r in v['observations'] if r['date'] in rates} for k, v in values.items() if k != 'EURUSD=X'}).dropna().sort_index()
    returns = prices.pct_change(fill_method=None).dropna()
    covariance = np.cov(returns['SYNTH'], returns['^GSPC'])
    assert body['regression']['beta'] == pytest.approx(covariance[0, 1] / covariance[1, 1])
    assert body['coverage']['fx_unmatched_stock_dates'] == 1
    assert body['regression']['n_obs'] == len(returns)


def test_blank_endpoint_is_preserved_as_excluded_not_an_observation(tmp_path):
    values = euro_histories(); values['EURUSD=X']['observations'].append({'date': DAY, 'close': None})
    result, _ = collect(tmp_path, values)
    assert result['status'] == 'partial'
    body = json.loads(result['documents'][0]['text'])
    assert body['coverage']['missing_observations']['EURUSD=X'] == [DAY]
    assert body['regression']['last_observation'] == '2026-02-02'
    assert body['regression']['n_obs'] == len(values['SYNTH']['observations'])-1
    assert json.loads(next(tmp_path.glob('beta-*.json')).read_bytes())['series']['EURUSD=X']['observations'][-1]['close'] is None


def test_default_provider_fetches_fx_close_without_relabelling_euro_prices(tmp_path, monkeypatch):
    from bellomberg.valuation.beta_reference_evidence import collect_beta_reference
    values = euro_histories()
    class Instrument:
        def __init__(self, symbol): self.symbol = symbol
        @property
        def history_metadata(self): return {k: values[self.symbol][k] for k in ('symbol', 'currency')}
        def history(self, **kwargs):
            leg = values[self.symbol]; key = 'close' if self.symbol == 'EURUSD=X' else 'adjusted_close'
            return pd.DataFrame({leg['price_field']: [r[key] for r in leg['observations']]},
                index=pd.to_datetime([r['date'] for r in leg['observations']]))
    monkeypatch.setattr('yfinance.Ticker', Instrument)
    result = collect_beta_reference('SYNTH', currency='USD', as_of=DAY, archive_root=tmp_path, now=NOW)
    assert result['status'] == 'ready', result
    assert json.loads(result['documents'][0]['text'])['regression']['beta'] == pytest.approx(2, abs=1e-12)
