"""Authenticated product endpoints, using synthetic requests and no providers."""
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from bellomberg.api.options_routes import create_options_router
from bellomberg.portfolio import vol_surface


@pytest.fixture
def client():
    def require_session(request: Request):
        if request.headers.get('Authorization') != 'Bearer synthetic':
            raise HTTPException(401, 'session required')
    app = FastAPI()
    app.include_router(create_options_router(require_session))
    return TestClient(app)


HEADERS = {'Authorization': 'Bearer synthetic'}


@pytest.mark.parametrize('method,url,body', [
    ('GET', '/options/expiry_catalog/DEMO.X', None),
    ('GET', '/options/chain_detail/DEMO.X?expiry=2027-01-15', None),
    ('POST', '/options/strategy/simulate', {}),
    ('POST', '/options/download/DEMO.X', {}),
    ('GET', '/options/download/job/status', None),
    ('POST', '/options/download/job/pause', None),
    ('POST', '/options/download/job/resume', None),
    ('GET', '/options/download/job/chain?expiry=2027-01-15', None),
    ('GET', '/options/download/job/surface', None),
])
def test_anonymous_requests_cannot_fetch_or_calculate(client, method, url, body, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('unauthenticated request reached provider')
    monkeypatch.setattr(vol_surface, 'get_expiry_catalog', forbidden)
    monkeypatch.setattr(vol_surface, 'get_chain_detail', forbidden)
    assert client.request(method, url, json=body).status_code == 401


def test_catalog_cursor_budget_and_partial_coverage_are_preserved(client, monkeypatch):
    calls = []
    expected = {'expirations': ['2027-01-15'], 'complete': False,
                'next_after': '2027-01-15', 'requests_used': 2, 'error': None}
    def catalog(ticker, **kwargs):
        calls.append((ticker, kwargs)); return expected
    monkeypatch.setattr(vol_surface, 'get_expiry_catalog', catalog)
    response = client.get('/options/expiry_catalog/DEMO.X?after=2026-12-18&request_budget=2', headers=HEADERS)
    assert response.status_code == 200 and response.json() == expected
    assert calls == [('DEMO.X', {'after': '2026-12-18', 'request_budget': 2})]
    assert client.get('/options/expiry_catalog/DEMO.X?request_budget=9', headers=HEADERS).status_code == 422
    assert len(calls) == 1


def test_chain_pagination_is_explicit(client, monkeypatch):
    calls = []
    def chain(ticker, expiry, **kwargs):
        calls.append((ticker, expiry, kwargs))
        return {'chain': [], 'complete': False, 'next_cursor': 'next', 'error': 'provider unavailable'}
    monkeypatch.setattr(vol_surface, 'get_chain_detail', chain)
    response = client.get('/options/chain_detail/DEMO.X?expiry=2027-01-15&cursor=opaque', headers=HEADERS)
    assert response.status_code == 200
    assert response.json()['error'] == 'provider unavailable'
    assert calls == [('DEMO.X', '2027-01-15', {'cursor': 'opaque'})]


def test_input_errors_are_http_422(client, monkeypatch):
    def invalid(*args, **kwargs):
        raise ValueError('invalid expiry')
    monkeypatch.setattr(vol_surface, 'get_chain_detail', invalid)
    response = client.get('/options/chain_detail/DEMO.X?expiry=invalid', headers=HEADERS)
    assert response.status_code == 422
    assert response.json()['detail'] == 'invalid expiry'
    assert client.post('/options/strategy/simulate', json={}, headers=HEADERS).status_code == 422


def test_strategy_endpoint_uses_pure_model_and_returns_units(client):
    # Two synthetic 20-unit contracts: premium cash 100, fees 2 * 3.5 = 7.
    body = {'spot':64, 'rate':0.02, 'dividend_yield':0, 'elapsed_days':0,
            'iv_shift':0, 'commission':3.5, 'currency':'USD',
            'legs':[{'type':'call', 'side':'buy', 'quantity':2, 'strike':64,
                     'days':30, 'iv':0.25, 'premium':2.5, 'multiplier':20}]}
    response = client.post('/options/strategy/simulate', json=body, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data['fees'] == pytest.approx(7)
    assert data['max_loss'] == pytest.approx(107)
    assert data['unlimited_profit'] is True
    # Premium and fees are divided by the 40 underlying units at break-even.
    assert data['breakevens'] == pytest.approx([66.675])

def test_surface_invalid_expiry_is_a_client_error_before_provider(monkeypatch):
    from bellomberg.api.bellomberg_api import get_vol_surface
    def forbidden(*args, **kwargs):
        pytest.fail('invalid selection reached chain provider')
    monkeypatch.setattr(vol_surface, 'get_chain_detail', forbidden)
    with pytest.raises(HTTPException) as caught:
        get_vol_surface('DEMO.X', expiries='not-a-date', include_context=False)
    assert caught.value.status_code == 422


def test_download_routes_use_memory_filters_and_distinct_completion(client, monkeypatch):
    import sys
    from types import SimpleNamespace
    from bellomberg.market_data import polygon_data as provider
    from bellomberg.portfolio import options_download
    from tests.test_options_download import contracts, exp, settled
    mgr = options_download.OptionsDownloadManager()
    monkeypatch.setattr(options_download, 'downloads', mgr)
    monkeypatch.setattr(provider, 'polygon_available', lambda: True)
    monkeypatch.setattr(provider, '_get', lambda *a: {'results': contracts(exp())})
    monkeypatch.setitem(sys.modules, 'yfinance', SimpleNamespace(Ticker=lambda symbol: SimpleNamespace(fast_info={'lastPrice':100})))
    vol_surface._CHAIN_CACHE.clear()
    response = client.post('/options/download/DEMO.X', json={'expiries': [exp()]}, headers=HEADERS)
    assert response.status_code == 200
    job_id = response.json()['id']; settled(mgr, job_id)
    status = client.get(f'/options/download/{job_id}/status', headers=HEADERS).json()
    assert status['download_complete']
    view = client.get(f'/options/download/{job_id}/chain', params={'expiry':exp(), 'side':'put', 'limit':2}, headers=HEADERS).json()
    assert view['filtered_contracts'] == 7 and len(view['chain']) == 2 and view['has_more']
    assert view['chain_complete'] and all(c['type'] == 'put' for c in view['chain'])
    assert client.get(f'/options/download/{job_id}/surface', headers=HEADERS).json()['coverage']['download_complete']
    assert client.post(f'/options/download/{job_id}/pause', headers=HEADERS).json()['state'] == 'complete'
    assert client.post(f'/options/download/{job_id}/resume', headers=HEADERS).json()['state'] == 'complete'


@pytest.mark.parametrize('body', [{'expiries':[]}, {'expiries':['invalid']}, {'expiries':'2027-01-15'}, {'unexpected':True}])
def test_download_bad_request_does_not_launch_worker(client, body):
    assert client.post('/options/download/DEMO.X', json=body, headers=HEADERS).status_code == 422


def test_download_unknown_id_is_404(client):
    assert client.get('/options/download/missing/status', headers=HEADERS).status_code == 404


def test_options_authored_variants_roundtrip_on_same_synthetic_snapshot(monkeypatch):
    from bellomberg.api.language_middleware import LanguageMiddleware
    from bellomberg.core.language import language_context
    from bellomberg.portfolio import options_strategy
    from copy import deepcopy
    calls = []
    body = {'spot':64, 'rate':0.02, 'dividend_yield':0, 'elapsed_days':0,
            'iv_shift':0, 'commission':3.5, 'currency':'USD',
            'legs':[{'type':'call', 'side':'buy', 'quantity':2, 'strike':64,
                     'days':30, 'iv':0.25, 'premium':2.5, 'multiplier':20}]}
    with language_context('it'):
        cached = options_strategy.simulate_strategy(body)
        cached['source_text'] = 'Originale provider, non tradurre'
        cached['quality'] = [vol_surface._surface_text('IV assente', 'Missing IV')]
    before = deepcopy(cached)
    def same_snapshot(ticker, **kwargs):
        calls.append(ticker)
        return cached
    monkeypatch.setattr(vol_surface, 'get_expiry_catalog', same_snapshot)
    app = FastAPI()
    app.add_middleware(LanguageMiddleware)
    app.include_router(create_options_router(lambda: None))
    client = TestClient(app)
    responses = []
    for language in ('it', 'en', 'it'):
        response = client.get('/options/expiry_catalog/DEMO.X', headers={'X-BB-Language':language})
        assert response.status_code == 200
        assert response.headers['Content-Language'] == language
        data = response.json(); responses.append(data)
        assert data['max_loss'] == pytest.approx(107)
        assert data['breakevens'] == pytest.approx([66.675])
        assert data['source_text'] == 'Originale provider, non tradurre'
        assert data['quality'] == (['IV assente'] if language == 'it' else ['Missing IV'])
        texts = data['_presentation_v1']['texts']
        assert any(item['path'] == ['limits',0] for item in texts)
        assert not any(item['path'] == ['source_text'] for item in texts)
        assert data['limits'][0].startswith('Opzioni europee' if language == 'it' else 'European options')
    assert responses[0] == responses[2]
    assert responses[0]['curve'] == responses[1]['curve']
    assert responses[0]['heatmap'] == responses[1]['heatmap']
    assert cached == before
    assert calls == ['DEMO.X'] * 3


@pytest.mark.parametrize('slope', [0.05, -0.05, 0.0, None])
def test_surface_interpretation_retains_nested_variants_without_recalculation(slope):
    from bellomberg.core.language import language_context
    from bellomberg.core.presentation import render_payload
    with language_context('it'):
        result = vol_surface._interpret('DEMO', [{'atm_iv':.2, 'rr25':-.04, 'bf25':.02, 'pc_oi_ratio':1.5}],
            slope, .18, .02, expected_move=5, exp_move_days=30, rv_pct_1y=20,
            next_earnings='2035-01-10', back_days=60)
    original = str(result)
    english = render_payload(result, language='en')
    assert 'COMPRESSED movement regime' in english
    assert 'Next earnings expected on 2035-01-10' in english
    assert 'giorni' not in english and 'regime di movimento' not in english
    if slope in (0.05, -0.05):
        assert 'at 60 days' in english
    assert str(result) == original
    assert render_payload(result, language='it') == original
