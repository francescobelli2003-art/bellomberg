"""Observed regression reference; no forward beta, leverage or WACC selection.

The archive pins yfinance's normalized observations, not original HTTP bytes.
EUR listings retain the same-date FX leg for an explicit USD comparison.
Dates/currencies are checked and the same covariance routine serves quant tools.
"""
from calendar import monthrange
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from urllib.parse import quote

from .document_evidence import source_dates

_BENCHMARK = '^GSPC'
_FX = 'EURUSD=X'
_CONVERSION = {'source_currency': 'EUR', 'target_currency': 'USD', 'symbol': _FX,
    'rate_unit': 'USD per EUR', 'price_field': 'Close',
    'operation': 'multiply adjusted stock price by same-date FX close; common dates only'}
_FX_LIMITATION = (' EUR stock prices are converted with same-date Yahoo EURUSD Close, without fill. '
    'Stock, FX and index may have different closing times; dates are provider session labels, '
    'not synchronized timestamps. This is not an intraday hedge return or FX-neutral beta. '
    'A blank FX bar at the requested exclusive endpoint is retained but never used.')
_LIMITATION = ('Historical regression reference, not a chosen forward beta or WACC. '
    'Daily Yahoo Adj Close includes stock distribution/split adjustments; S&P 500 is a price index, '
    'not a total-return market benchmark. No equivalence to the provider five-year monthly beta. '
    'Currency, exposure, leverage, financing weights and taxes need separate analyst judgment. '
    'Hashes pin normalized provider observations, not raw HTTP bytes. Weekday freshness is not an exchange calendar.')


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _window(cutoff):
    return date(cutoff.year - 5, cutoff.month, min(cutoff.day, monthrange(cutoff.year - 5, cutoff.month)[1]))


def _url(symbol):
    return 'https://finance.yahoo.com/quote/' + quote(symbol, safe='') + '/history/'


def _series(raw, symbol, start, cutoff, *, currency='USD', price_field='Adj Close', value_key='adjusted_close'):
    from bellomberg.market_data.market_inputs import _bdays_between, _STALE_BDAYS
    if not isinstance(raw, dict) or raw.get('symbol') != symbol or raw.get('currency') != currency:
        raise ValueError('observed history identity/currency mismatch: ' + symbol)
    if raw.get('price_field') != price_field:
        raise ValueError('explicit price field required: '+price_field)
    rows = raw.get('observations')
    if not isinstance(rows, list) or len(rows) < 253:
        raise ValueError('insufficient five-year observation coverage')
    values, missing, seen = {}, [], set()
    for row in rows:
        day = date.fromisoformat(row['date']); price = row[value_key]
        endpoint_blank = symbol == _FX and day == cutoff and price is None
        if row['date'] != day.isoformat() or not (start <= day < cutoff or endpoint_blank) or day in seen:
            raise ValueError('duplicate or out-of-window history date')
        seen.add(day)
        if price is None:
            missing.append(day.isoformat())
            continue
        if type(price) not in (int, float) or not isfinite(price) or price <= 0:
            raise ValueError('missing/nonfinite/nonpositive adjusted price')
        values[day] = price
    days = sorted(values)
    if len(days) < 253:
        raise ValueError('insufficient observed prices after declared blanks')
    if _bdays_between(start, days[0]) > _STALE_BDAYS or _bdays_between(days[-1], cutoff) > _STALE_BDAYS:
        raise ValueError('short or stale history window')
    if any(_bdays_between(a, b) > 5 for a, b in zip(days, days[1:])):
        raise ValueError('unqualified long gap in history')
    return values, sorted(missing)


def normalize_beta_reference(raw, *, as_of, retrieval):
    from bellomberg.market_data.return_statistics import paired_beta_statistics
    cutoff = date.fromisoformat(as_of); start = _window(cutoff)
    digest = sha256(raw).hexdigest()
    if not isinstance(retrieval, dict) or retrieval.get('document_sha256') != digest:
        raise ValueError('normalized history snapshot SHA256 mismatch')
    snapshot = json.loads(raw)
    ticker = snapshot['ticker']
    if (not isinstance(ticker, str) or not ticker.strip() or ticker == _BENCHMARK
            or snapshot['currency'] != 'USD' or snapshot['as_of'] != as_of
            or snapshot['start'] != start.isoformat() or snapshot['end_exclusive'] != as_of
            or retrieval.get('url') != _url(ticker)):
        raise ValueError('snapshot identity/window differs from requested reference')
    document = {'url': _url(ticker), 'document_sha256': digest, 'published_at': None,
                'availability_basis': 'observed_download', 'retrieval': retrieval}
    document.update(source_dates(document, cutoff))
    legs = snapshot['series']; converted = legs[ticker].get('currency') == 'EUR'
    if (set(legs) != ({ticker, _BENCHMARK, _FX} if converted else {ticker, _BENCHMARK})
            or snapshot.get('currency_conversion') != (_CONVERSION if converted else None)):
        raise ValueError('exact issuer and benchmark histories required')
    stock, missing_stock = _series(legs[ticker], ticker, start, cutoff, currency='EUR' if converted else 'USD')
    market, missing_market = _series(legs[_BENCHMARK], _BENCHMARK, start, cutoff)
    fx_coverage = {}; missing = {ticker: missing_stock, _BENCHMARK: missing_market}
    limitation = _LIMITATION
    if converted:
        fx, missing[_FX] = _series(legs[_FX], _FX, start, cutoff, price_field='Close', value_key='close')
        fx_coverage = {'fx_unmatched_stock_dates': len(stock.keys()-fx.keys()),
                       'fx_dates_without_stock': len(fx.keys()-stock.keys())}
        stock = {day: price*fx[day] for day, price in stock.items() if day in fx}
        if any(not isfinite(price) or price <= 0 for price in stock.values()):
            raise ValueError('converted stock price nonfinite/nonpositive')
        limitation += _FX_LIMITATION
    days = sorted(stock.keys() & market.keys())
    if len(days) < 253:
        raise ValueError('insufficient common observations')
    if converted:
        from bellomberg.market_data.market_inputs import _bdays_between, _STALE_BDAYS
        if (_bdays_between(start, days[0]) > _STALE_BDAYS or _bdays_between(days[-1], cutoff) > _STALE_BDAYS
                or any(_bdays_between(a, b) > 5 for a, b in zip(days, days[1:]))):
            raise ValueError('short, stale or gapped common stock/FX/benchmark window')
    returns = [(stock[b] / stock[a] - 1, market[b] / market[a] - 1) for a, b in zip(days, days[1:])]
    statistic = paired_beta_statistics(returns)
    if statistic['beta'] is None or not isfinite(statistic['beta']):
        raise ValueError('benchmark variance absent or nonfinite')
    statistic.update(first_observation=days[0].isoformat(), last_observation=days[-1].isoformat(),
        method='covariance(asset,benchmark)/variance(benchmark), same sample denominator',
        sampling='daily available common dates, adjacent simple returns, no fill/interpolation')
    coverage = {'unpaired_dates': len(stock.keys() ^ market.keys()),
        'missing_observations': missing, **fx_coverage,
        'series': {name: {'url': _url(name), 'observations': len(leg['observations']),
            'normalized_observations_sha256': sha256(_json(leg['observations']).encode('utf-8')).hexdigest()}
            for name, leg in legs.items()}}
    text = _json({'dataset': 'historical_beta', 'ticker': ticker, 'benchmark': _BENCHMARK,
        'currency': 'USD', 'requested_start': start.isoformat(), 'end_exclusive': as_of,
        'regression': statistic, 'coverage': coverage, 'limitation': limitation,
        **({'currency_conversion': _CONVERSION} if converted else {})})
    text_hash = sha256(text.encode('utf-8')).hexdigest()
    document.update(id='beta-' + text_hash, text=text, sha256=text_hash,
        origin='yfinance_history_regression', metadata={'normalizer': 'beta_reference_v1',
        'ticker': ticker, 'currency': 'USD', 'benchmark': _BENCHMARK, 'reference_cutoff': as_of},
        extraction_coverage={'status': 'derived_statistical_reference', 'normalized_snapshot_sha256_checked': True,
            'raw_http_bytes_attested': False, 'limitation': limitation})
    return document


def collect_beta_reference(ticker, *, currency, as_of, archive_root, fetch=None, now=None):
    result = {'status': 'incomplete', 'documents': [], 'issues': [], 'limitation': _LIMITATION}
    try:
        if currency != 'USD':
            raise ValueError('beta reference currently supports explicit USD histories only')
        cutoff = date.fromisoformat(as_of); start = _window(cutoff).isoformat()
        if fetch is None:
            import yfinance as yf
            def fetch(symbol, beginning, end):
                instrument = yf.Ticker(symbol)
                frame = instrument.history(start=beginning, end=end, auto_adjust=False, actions=False, raise_errors=True)
                metadata = instrument.history_metadata
                field, key = ('Close', 'close') if symbol == _FX else ('Adj Close', 'adjusted_close')
                return {'symbol': metadata.get('symbol'), 'currency': metadata.get('currency'), 'price_field': field,
                    'observations': [{'date': stamp.date().isoformat(),
                        key: None if value != value else float(value)}
                        for stamp, value in frame[field].items()]}
        snapshot = {'ticker': ticker, 'currency': currency, 'as_of': as_of, 'start': start,
            'end_exclusive': as_of, 'series': {name: fetch(name, start, as_of) for name in (ticker, _BENCHMARK)}}
        if snapshot['series'][ticker].get('currency') == 'EUR':
            snapshot['currency_conversion'] = dict(_CONVERSION)
            snapshot['series'][_FX] = fetch(_FX, start, as_of)
        raw = _json(snapshot).encode('utf-8'); digest = sha256(raw).hexdigest()
        stamp = now if now is not None else datetime.now(timezone.utc)
        document = normalize_beta_reference(raw, as_of=as_of, retrieval={
            'url': _url(ticker), 'document_sha256': digest, 'retrieved_at': stamp.isoformat()})
        root = Path(archive_root).resolve(); root.mkdir(parents=True, exist_ok=True)
        path = root / ('beta-' + digest + '.json')
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError('existing beta archive differs from its content hash')
        else:
            with path.open('xb') as file:
                file.write(raw)
        result.update(status='ready', documents=[document])
        coverage = json.loads(document['text'])['coverage']; missing = coverage['missing_observations']
        result['limitation'] = document['extraction_coverage']['limitation']
        if any(missing.values()):
            result['status'] = 'partial'
            result['issues'].append({'source': 'historical beta reference',
                'reason': 'Blank price observations preserved in archive and excluded without filling; last used date is explicit.',
                'missing_observations': missing})
        if coverage.get('fx_unmatched_stock_dates'):
            result['status'] = 'partial'
            result['issues'].append({'source': 'historical beta reference',
                'reason': 'Stock dates lacking observed same-date FX are excluded without filling.',
                'fx_unmatched_stock_dates': coverage['fx_unmatched_stock_dates']})
    except Exception as exc:
        result['issues'].append({'source': 'historical beta reference', 'reason': type(exc).__name__ + ': ' + str(exc)})
    return result
