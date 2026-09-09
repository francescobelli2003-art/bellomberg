import time

import pytest
from fastapi.testclient import TestClient

from bellomberg.api import bellomberg_api as api


class _FakeDB:
    def get_portfolio(self):
        return [{"ticker": "SYNTHETIC", "quantita": 7}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "get_db", lambda: _FakeDB())
    api._SESSIONS.clear()
    return TestClient(api.app, base_url="http://127.0.0.1:8765")


def test_origin_null_non_legge_portafoglio_senza_sessione(client):
    response = client.get("/portfolio/positions", headers={"Origin": "null"})
    assert response.status_code == 401
    assert "SYNTHETIC" not in response.text


def test_origin_null_legge_portafoglio_con_sessione(client):
    api._SESSIONS["synthetic-token"] = time.time() + 60
    response = client.get(
        "/portfolio/positions",
        headers={"Origin": "null", "X-BB-Token": "synthetic-token"},
    )
    assert response.status_code == 200
    assert response.json()[0]["ticker"] == "SYNTHETIC"
    assert response.headers["access-control-allow-origin"] == "null"


@pytest.mark.parametrize("path", ["/health", "/auth/status"])
def test_origin_null_puo_leggere_solo_endpoint_pubblici(client, path):
    assert client.get(path, headers={"Origin": "null"}).status_code == 200


def test_preflight_origin_null_resta_disponibile(client):
    response = client.options(
        "/portfolio/positions",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-BB-Token",
        },
    )
    assert response.status_code == 200


def test_host_arbitrario_rifiutato():
    client = TestClient(api.app, base_url="http://evil.example")
    assert client.get("/health").status_code == 400


@pytest.mark.parametrize(
    ("pin", "misconfigured"),
    [(None, True), ("", True), ("1234", True), ("abcde", True),
     ("12345", True), ("１２３４", True), ("4821", False)],
)
def test_pin_rispetta_il_contratto_ui_ascii(monkeypatch, pin, misconfigured):
    if pin is None:
        monkeypatch.delenv("BELLOMBERG_PIN", raising=False)
    else:
        monkeypatch.setenv("BELLOMBERG_PIN", pin)
    assert api._pin_misconfigured() is misconfigured
