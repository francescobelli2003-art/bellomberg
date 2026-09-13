"""Il consigliere parte solo con un mandato leggibile e completo."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

import bellomberg.core.mandato_pm as mp


class _Proc:
    pid = 43210
    returncode = 0

    def wait(self, timeout=None):
        return 0


def _valido():
    m = copy.deepcopy(mp.profilo_esempio())
    m["versione"] = mp.VERSIONE_SCHEMA
    m["dichiarato_il"] = "2026-01-02"
    return m


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    p = tmp_path / "mandato_pm.json"
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(p))
    api.app.dependency_overrides[api.require_session] = lambda: None
    api._LAST_CALL.clear()
    api.run_state.runs.clear()
    api._CONSIGLIERE_PROCS.clear()
    chiamate = []
    monkeypatch.setattr(api.subprocess, "Popen", lambda *a, **k: chiamate.append((a, k)) or _Proc())
    monkeypatch.setattr(api, "DB_DIR", str(tmp_path))
    try:
        yield TestClient(api.app, base_url="http://127.0.0.1"), p, chiamate, api
    finally:
        api.app.dependency_overrides.clear()
        api._LAST_CALL.clear()
        api.run_state.runs.clear()
        api._CONSIGLIERE_PROCS.clear()


@pytest.mark.parametrize("forma", ["assente", "incompleto"])
def test_run_senza_mandato_pronto_rende_428_non_consuma_throttle_e_non_spawna(
        forma, ambiente):
    c, p, chiamate, api = ambiente
    if forma == "incompleto":
        m = _valido()
        m["rischio"]["drawdown_max_pct"] = None
        p.write_text(json.dumps(m), encoding="utf-8")
    r = c.post("/consigliere/run")
    assert r.status_code == 428 and isinstance(r.json()["detail"], str)
    assert chiamate == []
    assert "/consigliere/run" not in api._LAST_CALL

    p.write_text(json.dumps(_valido()), encoding="utf-8")
    buono = c.post("/consigliere/run")
    assert buono.status_code == 200, buono.text
    assert len(chiamate) == 1


@pytest.mark.parametrize("causa", ["illeggibile", "in_uso", "esempio"])
def test_run_con_mandato_non_leggibile_rende_503_e_non_spawna(
        causa, ambiente, monkeypatch):
    c, _p, chiamate, _api = ambiente
    monkeypatch.setattr(mp, "carica", lambda *a, **k: (_ for _ in ()).throw(
        mp.MandatoMancante("finto.json", causa, "guasto sintetico")))
    r = c.post("/consigliere/run")
    assert r.status_code == 503 and causa in r.json()["detail"]
    assert chiamate == []


def test_run_ammette_un_profilo_di_esempio_valido(ambiente):
    c, p, chiamate, _api = ambiente
    p.write_text(json.dumps(_valido()), encoding="utf-8")
    r = c.post("/consigliere/run")
    assert r.status_code == 200, r.text
    assert len(chiamate) == 1


@pytest.mark.parametrize('language,detail', [('it', 'e gia in corso'), ('en', 'already in progress')])
def test_run_gia_attiva_resta_409(ambiente, language, detail):
    c, _p, chiamate, api = ambiente
    api.run_state.runs["run_esistente"] = {"status": "running"}
    r = c.post("/consigliere/run", headers={'X-BB-Language': language})
    assert r.status_code == 409
    assert detail in r.json()["detail"]
    assert chiamate == []


def test_seconda_verifica_ferma_lo_spawn_se_il_mandato_cambia(ambiente, monkeypatch):
    c, p, chiamate, api = ambiente
    p.write_text(json.dumps(_valido()), encoding="utf-8")
    vero_start = api.run_state.start

    def start_e_rompi(task_id):
        vero_start(task_id)
        m = _valido()
        m["rischio"]["drawdown_max_pct"] = None
        p.write_text(json.dumps(m), encoding="utf-8")

    monkeypatch.setattr(api.run_state, "start", start_e_rompi)
    r = c.post("/consigliere/run")
    assert r.status_code == 200
    assert chiamate == []
    assert api.run_state.get(r.json()["task_id"])["status"] == "failed"
    assert "mandato" in api.run_state.get(r.json()["task_id"])["error"].lower()
    assert "/consigliere/run" not in api._LAST_CALL
    monkeypatch.setattr(api.run_state, "start", vero_start)
    p.write_text(json.dumps(_valido()), encoding="utf-8")
    assert c.post("/consigliere/run").status_code == 200
    assert len(chiamate) == 1


def test_rimborso_throttle_non_cancella_il_timestamp_di_un_altra_richiesta(ambiente):
    _c, _p, _chiamate, api = ambiente
    class _URL:
        path = "/consigliere/run"
    class _State:
        pass
    class _Request:
        url = _URL()
        state = _State()
    req = _Request()
    req.state.throttle_timestamp = 10.0
    api._LAST_CALL[req.url.path] = 10.0
    api._refund_throttle(req)
    assert req.url.path not in api._LAST_CALL

    api._LAST_CALL[req.url.path] = 11.0
    api._refund_throttle(req)
    assert api._LAST_CALL[req.url.path] == 11.0
