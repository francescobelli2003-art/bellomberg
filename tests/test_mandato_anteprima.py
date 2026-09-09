"""Anteprima del mandato: stesso testo dei modelli, nessuna scrittura."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

import bellomberg.core.mandato_pm as mp


def _valido():
    m = copy.deepcopy(mp.profilo_esempio())
    m["versione"] = mp.VERSIONE_SCHEMA
    m["dichiarato_il"] = "2026-01-02"
    return m


@pytest.fixture
def client(tmp_path, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    p = tmp_path / "mandato_pm.json"
    m = _valido()
    p.write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(p))
    api.app.dependency_overrides[api.require_session] = lambda: None
    try:
        yield TestClient(api.app, base_url="http://127.0.0.1"), p
    finally:
        api.app.dependency_overrides.clear()


def test_get_anteprima_salvata_e_autenticata(client):
    c, _ = client
    c.app.dependency_overrides.clear()
    assert c.get("/mandato/anteprima").status_code == 401
    assert c.post("/mandato/anteprima", json=_valido()).status_code == 401


def test_get_mandato_incompleto_http_conserva_il_corpo_compilato(client):
    c, p = client
    m = _valido()
    m["_nota"] = "meta sintetica"
    m["rischio"]["drawdown_max_pct"] = None
    m["profilo"]["orizzonte_anni"] = "tre"
    p.write_text(json.dumps(m), encoding="utf-8")
    r = c.get("/mandato")
    assert r.status_code == 200, r.text
    assert r.json()["valori"]["_nota"] == "meta sintetica"
    assert r.json()["valori"]["profilo"]["orizzonte_anni"] == "tre"
    assert r.json()["campi_mancanti"] == ["drawdown_max_pct"]


def test_get_anteprima_restituisce_esattamente_il_blocco_prompt(client):
    c, _ = client
    import bellomberg.api.bellomberg_api as api
    api.app.dependency_overrides[api.require_session] = lambda: None
    r = c.get("/mandato/anteprima")
    m = mp.carica()
    assert r.status_code == 200
    assert r.json() == {"testo": mp.blocco_prompt(m), "impronta": mp.impronta(m),
                        "origine": m["origine"]}


def test_post_anteprima_valida_non_scrive_e_calcola_origine(client):
    c, p = client
    prima = p.read_bytes()
    corpo = _valido()
    corpo["cassa"]["cassa_minima_pct"] = 7
    r = c.post("/mandato/anteprima", json=corpo)
    assert r.status_code == 200, r.text
    assert r.json()["origine"] == "personalizzato"
    assert "cassa minima" in r.json()["testo"].lower()
    assert p.read_bytes() == prima


def test_post_anteprima_valida_prima_di_chiamare_blocco_prompt(client, monkeypatch):
    c, _ = client
    chiamate = []
    monkeypatch.setattr(mp, "blocco_prompt", lambda m: chiamate.append(m) or "PROMPT")
    corpo = _valido()
    corpo["rischio"]["drawdown_max_pct"] = None
    r = c.post("/mandato/anteprima", json=corpo)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)
    assert chiamate == []


def test_post_anteprima_rifiuta_origine_del_client_e_json_malformato(client):
    c, _ = client
    r = c.post("/mandato/anteprima", json=dict(_valido(), origine="personalizzato"))
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    rotto = c.post("/mandato/anteprima", content="{rotto", headers={"Content-Type": "application/json"})
    assert rotto.status_code == 422 and isinstance(rotto.json()["detail"], list)


def test_post_anteprima_ammette_opzionali_null_e_origine_esempio(client):
    c, _ = client
    r = c.post("/mandato/anteprima", json=mp.profilo_esempio())
    assert r.status_code == 200, r.text
    assert r.json()["origine"] == "esempio"
