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
