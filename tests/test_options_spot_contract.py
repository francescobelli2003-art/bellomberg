"""R02: synthetic provider observations never become invented spot/ATM quotes."""
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from bellomberg.core import options_expiry
from bellomberg.market_data import polygon_data as pd

EXPIRY = '2099-06-18'
ASOF = 4083955200000000000  # 2099-06-01 UTC; synthetic clock below


@pytest.fixture(autouse=True)
def clock_and_key(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2099, 6, 1, 12, tzinfo=tz)

    class Day(date):
        @classmethod
        def today(cls):
            return cls(2099, 6, 1)

    monkeypatch.setattr(pd, 'datetime', Clock)
    monkeypatch.setattr(options_expiry, 'date', Day)
    monkeypatch.setattr(pd, 'POLYGON_KEY', 'SYNTHETIC_OPTIONS_TEST')
    monkeypatch.setattr(pd, 'REQ_OK', True)


def rows():
    return [dict(type=t, expiry=EXPIRY, strike=80., delta=d, iv=.6, oi=10)
            for t, d in [('call', .09), ('put', -.91)]]


def summary(monkeypatch, chain, coverage='COMPLETE'):
    monkeypatch.setattr(pd, 'get_options_chain', lambda *a, **k:
                        {'chain': chain, 'coverage': {'status': coverage}})
    return pd.get_options_summary_polygon('SYNTH', EXPIRY)


def quote(**changes):
    return dict(price=123., ticker='SYNTH', last_updated=ASOF,
                timeframe='DELAYED', **changes)


def test_missing_underlying_quote_is_not_strike(monkeypatch):
    result = summary(monkeypatch, rows())
    assert result['spot'] is None
    assert result['iv_atm_call'] is None
    assert result['max_pain_vs_spot_pct'] is None
    assert result['max_pain_strike'] == 80.
    assert result['total_call_oi'] == 10
    assert result['spot_status'] == 'MISSING'
    assert result['atm_status'] != 'QUALIFIED'


def test_near_half_delta_is_not_observed_spot(monkeypatch):
    chain = rows()
    chain[0]['delta'] = .5
    result = summary(monkeypatch, chain)
    assert result['spot'] is None
    assert result['spot_proxy'] is None
    assert result['atm_strike'] is None


def test_documented_quote_survives_without_invented_currency_or_iv_time(monkeypatch):
    chain = rows()
    for row in chain:
        row['underlying_asset'] = quote()
    result = summary(monkeypatch, chain)
    assert result['spot_observation']['price'] == 123.
    assert result['spot_observation']['ticker'] == 'SYNTH'
    assert result['spot_asof'] == '2099-06-01T00:00:00+00:00'
    assert result['spot_currency'] is None
    assert result['spot_status'] == 'UNVERIFIED_CURRENCY'
    assert result['spot'] is None
    assert result['iv_atm_call'] is None
    assert result['max_pain_vs_spot_pct'] is None
    assert 'currency_unattested' in result['atm_issues']
    assert 'iv_timestamp_unattested' in result['atm_issues']
    candidates = result['observed_strike_candidates']
    assert candidates['status'] == 'UNVERIFIED_CURRENCY_AND_TIMING'
    assert candidates['call']['strike'] == 80.
    assert candidates['call']['iv'] == .6
    assert candidates['put']['expiry'] == EXPIRY


def test_observed_candidates_follow_price_not_half_delta(monkeypatch):
    chain = rows()
    chain += [dict(type=t, expiry=EXPIRY, strike=120., delta=d, iv=.3, oi=4)
              for t, d in [('call', .2), ('put', -.8)]]
    chain[0]['delta'] = .5
    for row in chain:
        row['underlying_asset'] = quote()
    result = summary(monkeypatch, chain)
    assert result['observed_strike_candidates']['call']['strike'] == 120.
    assert result['observed_strike_candidates']['put']['iv'] == .3
    assert result['atm_strike'] is None and result['iv_atm_call'] is None


def test_unattested_currency_extension_is_not_promoted(monkeypatch):
    chain = rows()
    for row in chain:
        row['underlying_asset'] = quote(currency='EUR')
    result = summary(monkeypatch, chain)
    assert result['spot_observation']['currency'] == 'EUR'
    assert result['spot_currency'] is None and result['spot'] is None
    assert result['spot_status'] == 'UNVERIFIED_CURRENCY'


@pytest.mark.parametrize('bad', [0, -1, float('nan'), float('inf'), True, '123'])
def test_invalid_observed_price_is_not_spot(monkeypatch, bad):
    chain = rows()
    for row in chain:
        row['underlying_asset'] = {**quote(), 'price': bad}
    result = summary(monkeypatch, chain)
    assert result['spot'] is None
    assert result['spot_observation'] is None
    assert result['spot_status'] == 'INVALID_PRICE'


@pytest.mark.parametrize('change,status', [
    ({'ticker': 'OTHER'}, 'IDENTITY_MISMATCH'),
    ({'last_updated': None}, 'INVALID_TIMESTAMP'),
    ({'last_updated': ASOF * 1000}, 'INVALID_TIMESTAMP'),
])
def test_quote_identity_and_time_must_be_valid(monkeypatch, change, status):
    chain = rows()
    for row in chain:
        row['underlying_asset'] = {**quote(), **change}
    result = summary(monkeypatch, chain)
    assert result['spot'] is None and result['spot_observation'] is None
    assert result['spot_status'] == status


@pytest.mark.parametrize('change', [{'last_updated': ASOF - 1000000000}, {'price': 124.},
                                  {'currency': 'EUR'}])
def test_inconsistent_observations_are_not_merged(monkeypatch, change):
    chain = rows()
    chain[0]['underlying_asset'] = quote()
    chain[1]['underlying_asset'] = {**quote(), **change}
    result = summary(monkeypatch, chain)
    assert result['spot'] is None
    assert result['spot_status'] == 'CONFLICTING_OBSERVATIONS'
    assert result['spot_observation'] is None


def test_partial_chain_preserves_oi_with_coverage_not_atm(monkeypatch):
    result = summary(monkeypatch, rows(), 'PARTIAL')
    assert result['coverage']['status'] == 'PARTIAL'
    assert result['total_put_oi'] == 10
    assert result['iv_atm_put'] is None
    assert 'chain_not_complete' in result['atm_issues']


def test_call_and_put_must_share_requested_expiry(monkeypatch):
    chain = rows()
    chain[1]['expiry'] = '2099-07-17'
    result = summary(monkeypatch, chain)
    assert result['error'] == 'chain incompleta per expiry richiesta'
    assert result['coverage']['rows_rejected_expiry'] == 1


def test_missing_key_never_fetches(monkeypatch):
    monkeypatch.setattr(pd, 'POLYGON_KEY', '')
    monkeypatch.setattr(pd, '_get', lambda *a, **k: pytest.fail('unexpected provider call'))
    assert 'POLYGON_API_KEY' in pd.get_options_summary_polygon('SYNTH', EXPIRY)['error']


def test_raw_provider_observation_is_transported_without_an_extra_fetch(monkeypatch):
    payload = {'results': [dict(details={'ticker': 'O:SYNTH', 'contract_type': 'call',
                           'strike_price': 80., 'expiration_date': EXPIRY},
                           underlying_asset=quote(), implied_volatility=.6)]}
    original = deepcopy(payload)
    requests = []

    def get(url, **kwargs):
        requests.append(url)
        return SimpleNamespace(status_code=200, text='', json=lambda: payload)

    monkeypatch.setattr(pd.requests, 'get', get)
    result = pd.get_options_chain('SYNTH', EXPIRY)
    assert result['chain'][0]['underlying_asset'] == quote()
    assert result['chain'][0]['strike'] == 80.
    assert result['chain'][0]['iv'] == .6
    assert len(requests) == 1
    assert payload == original


def test_score_rejects_unqualified_atm_even_if_numeric_fields_survive():
    from bellomberg.agents import specialist_scores as s
    out = s.options_score('SYNTH', options_data={
        'expiry_used': EXPIRY, 'atm_status': 'UNVERIFIED',
        'atm_issues': ['iv_timestamp_unattested'], 'atm_iv_call_pct': 60.,
        'atm_iv_put_pct': 70., 'put_call_oi_ratio': 1.})
    assert out['metrics']['atm_iv'] is None
    # fix score 09/10 (Opus 5.5): ATM IV e P/C di una scadenza sono informativi, nessun punto;
    # la non qualificazione resta DICHIARATA fra le righe informative
    assert out['score'] is None and out['max_score'] is None
    assert any(r[0].startswith('ATM IV') and 'UNVERIFIED' in r[1] for r in out['info'])


def test_tool_preserves_missing_spot_and_declares_comparison_limit(monkeypatch):
    from bellomberg.agents import agent_tools as a
    result = summary(monkeypatch, rows())
    monkeypatch.setattr(pd, 'get_options_summary_polygon', lambda *a, **k: result)
    monkeypatch.setattr(a, '_get_ibkr_options', lambda *a, **k: pytest.fail('unexpected fallback'))
    out = a.tool_get_options_data('SYNTH', EXPIRY)
    assert out['spot'] is None
    assert out['atm_iv_call_pct'] is None
    assert out['iv_hv_comparison_status'] == 'UNVERIFIED'


def http_summary(monkeypatch, observations):
    """Exercise the real adapter/summary boundary, replacing only HTTP."""
    payload = {'results': [
        {'details': {'ticker': 'O:SYNTH_' + side, 'contract_type': side,
                     'strike_price': 120., 'expiration_date': EXPIRY},
         'underlying_asset': observation, 'implied_volatility': .3,
         'open_interest': 10}
        for side, observation in zip(('call', 'put'), observations)]}
    requests = []

    def get(url, **kwargs):
        requests.append(url)
        return SimpleNamespace(status_code=200, text='', json=lambda: payload)

    monkeypatch.setattr(pd.requests, 'get', get)
    out = pd.get_options_summary_polygon('SYNTH', EXPIRY)
    assert len(requests) == 1
    return out


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('other,status', [
    ({'ticker': 'SYNTH'}, 'MISSING_PRICE'),
    (None, 'MISSING'),
    ({**quote(), 'price': 0}, 'INVALID_PRICE'),
    ({**quote(), 'ticker': 'OTHER'}, 'IDENTITY_MISMATCH'),
    ({**quote(), 'last_updated': None}, 'INVALID_TIMESTAMP'),
])
def test_valid_observation_survives_incomplete_or_invalid_rows(monkeypatch, reverse, other, status):
    observations = [quote(), other]
    if reverse:
        observations.reverse()
    out = http_summary(monkeypatch, observations)
    assert out['spot_observation'] == quote()
    assert out['spot_status'] == 'UNVERIFIED_CURRENCY'
    assert out['spot_observation_issues'] == {status: 1}
    assert out['spot_observation_coverage'] == 'PARTIAL'
    assert [item['observation'] for item in out['spot_observations']] == observations
    assert sorted(item['status'] for item in out['spot_observations']) == sorted([
        status, 'UNVERIFIED_CURRENCY'])
    assert out['observed_strike_candidates']['call']['strike'] == 120.
    assert 'underlying_quote_partial' in out['atm_issues']
    assert out['spot'] is None and out['iv_atm_call'] is None
    assert out['max_pain_vs_spot_pct'] is None


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('change', [{'price': 124.}, {'last_updated': ASOF - 1000000000},
                                  {'currency': 'EUR'}])
def test_conflicting_raw_observations_remain_visible_in_both_orders(monkeypatch, reverse, change):
    observations = [quote(), {**quote(), **change}]
    if reverse:
        observations.reverse()
    out = http_summary(monkeypatch, observations)
    assert out['spot_status'] == 'CONFLICTING_OBSERVATIONS'
    assert out['spot_observation'] is None
    assert out['observed_strike_candidates'] is None
    assert [item['observation'] for item in out['spot_observations']] == observations
    assert out['spot'] is None and out['max_pain_vs_spot_pct'] is None
    assert 'underlying_quotes_conflicting' in out['atm_issues']


def test_absent_and_invalid_prices_have_distinct_raw_statuses(monkeypatch):
    out = http_summary(monkeypatch, [{'ticker': 'SYNTH'}, {**quote(), 'price': 0}])
    assert out['spot_observation'] is None
    assert out['spot_status'] == 'UNAVAILABLE'
    assert out['spot_observation_coverage'] == 'UNAVAILABLE'
    assert out['spot_observation_issues'] == {'MISSING_PRICE': 1, 'INVALID_PRICE': 1}
    assert [item['status'] for item in out['spot_observations']] == ['MISSING_PRICE', 'INVALID_PRICE']
