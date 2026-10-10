"""Il consigliere parte solo con un mandato leggibile e completo."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

import bellomberg.core.mandato_pm as mp
from test_cablaggio_consigliere_multi import run_offline


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


def test_zero_exit_without_verified_outcome_never_marks_weekly_complete(ambiente):
    c, p, _calls, api = ambiente
    p.write_text(json.dumps(_valido()), encoding='utf-8')
    response = c.post('/consigliere/run')
    state = api.run_state.get(response.json()['task_id'])
    assert state['status'] == 'failed'
    assert 'outcome' in state['error'].lower()


@pytest.mark.parametrize('status', ['completed', 'incomplete', 'failed'])
def test_weekly_api_consumes_task_bound_terminal_outcome(ambiente, monkeypatch, status):
    from pathlib import Path
    c, p, calls, api = ambiente
    p.write_text(json.dumps(_valido()), encoding='utf-8')
    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        if '--outcome' in argv:
            path = Path(argv[argv.index('--outcome') + 1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'task_id': argv[argv.index('--task-id') + 1],
                'status': status, 'memo_id': 7, 'analytical_status': 'complete' if status == 'completed' else 'incomplete',
                'artifact_status': 'available' if status == 'completed' else 'missing',
                'delivery_status': 'pending', 'first_error': None if status == 'completed' else {'message': 'Synthetic PDF failure'}}))
        return _Proc()
    monkeypatch.setattr(api.subprocess, 'Popen', spawn)
    response = c.post('/consigliere/run')
    state = api.run_state.get(response.json()['task_id'])
    assert state['status'] == status
    assert state['outcome']['memo_id'] == 7


def test_weekly_recovery_options_require_exact_run_and_paid_authorization(ambiente):
    c, p, calls, _api = ambiente
    p.write_text(json.dumps(_valido()), encoding='utf-8')
    assert c.post('/consigliere/run', json={'delivery_only': True}).status_code == 422
    assert c.post('/consigliere/run', json={'resume_memo_id': 7}).status_code == 428
    assert calls == []


def test_weekly_api_selects_native_run_and_only_spawns_saved_delivery(ambiente, run_offline, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.storage import memory_db
    c, p, calls, api = ambiente
    p.write_text(json.dumps(_valido()), encoding='utf-8')
    result = cm.run_multi_agent(send_email=False)
    p.unlink()  # Frozen delivery is independent of a fresh economic mandate.
    database = memory_db.MemoryDB()
    monkeypatch.setattr(api, 'get_db', lambda: database)
    listed = c.get('/consigliere/runs')
    assert listed.status_code == 200, listed.text
    assert listed.json()['runs'][0]['memo_id'] == result['memo_id']
    selected = c.get('/consigliere/runs/' + str(result['memo_id'])).json()
    assert selected['delivery_recovery_available'] and not selected['resume_available']
    assert c.post('/consigliere/run', json={'resume_memo_id': result['memo_id'] + 1,
                                           'delivery_only': True}).status_code == 404
    assert calls == []
    response = c.post('/consigliere/run', json={'resume_memo_id': result['memo_id'], 'delivery_only': True})
    assert response.status_code == 200, response.text
    argv = calls[0][0][0]
    assert argv[argv.index('--resume-memo-id') + 1] == str(result['memo_id'])
    assert '--delivery-only' in argv and '--no-email' in argv and '--authorize-new-ai' not in argv


@pytest.mark.parametrize('patch', [
    {'task_id': 'different'}, {'analytical_status': 'incomplete'}, {'artifact_status': 'missing'}, {'memo_id': None},
])
def test_weekly_worker_outcome_rejects_wrong_identity_or_partial_completion(tmp_path, patch):
    from bellomberg.api.weekly_recovery_routes import read_worker_outcome
    value = {'task_id': 'expected', 'status': 'completed', 'memo_id': 1,
             'analytical_status': 'complete', 'artifact_status': 'available'}
    value.update(patch)
    path = tmp_path / 'outcome.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError):
        read_worker_outcome(path, 'expected', 0)


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


@pytest.mark.parametrize("pronto", [False, True])
def test_store_trade_idea_non_pronto_dichiara_il_controllo_non_eseguito(pronto, ambiente, monkeypatch, tmp_path):
    """B4 (09/10, Opus 5.5): store Trade Idea non pronto = la weekly parte comunque ma il
    controllo «Trade Idea attiva» e' DICHIARATO non eseguito (risposta, stato, log)."""
    from bellomberg.storage import trade_idea_store as tis
    from bellomberg.api import trade_idea_routes as routes
    c, p, chiamate, api = ambiente
    p.write_text(json.dumps(_valido()), encoding="utf-8")
    monkeypatch.setattr(routes, "paid_run_is_active", lambda: False)
    if pronto:
        monkeypatch.setattr(tis.TradeIdeaStore, "__init__", lambda self, *a, **k: None)
        monkeypatch.setattr(routes, "_active_id", lambda store: None)
    else:
        def rifiuta(self, *a, **k):
            raise tis.StorageNotReady("schema_partial", "Trade Idea schema partial: run explicit migration")
        monkeypatch.setattr(tis.TradeIdeaStore, "__init__", rifiuta)
    r = c.post("/consigliere/run")
    assert r.status_code == 200, r.text
    assert len(chiamate) == 1
    check = r.json()["trade_idea_active_check"]
    assert api.run_state.get(r.json()["task_id"])["trade_idea_active_check"] == check
    log = (tmp_path / "consigliere_run.log").read_text(encoding="utf-8")
    if pronto:
        assert check == {"status": "completed", "reason": None, "storage": None}
        assert "TRADE IDEA ACTIVE CHECK NOT RUN" not in log
        return
    assert check["status"] == "not_run"
    assert check["reason"] == "trade_idea_storage_unavailable"
    assert check["storage"]["status"] == "schema_partial"
    assert check["storage"]["error_code"] == "trade_idea_schema_partial"
    assert check["storage"]["update_required"] is True
    assert "TRADE IDEA ACTIVE CHECK NOT RUN: trade_idea_storage_unavailable" in log


# ---- 10/10 (R14b, Opus 5.5): riserve M2 e B4 della revisione ---------------------------
def _db_sintetico(path, stato):
    """DB sintetico in journal DELETE (in WAL un BEGIN EXCLUSIVE non blocca i lettori)."""
    import sqlite3
    from test_trade_idea_store import BASE_SCHEMA
    from bellomberg.storage.trade_idea_store import ensure_schema, SCHEMA
    conn = sqlite3.connect(path)
    conn.executescript(BASE_SCHEMA)
    if stato == "sano":
        ensure_schema(conn)
    elif stato == "parziale":
        conn.execute(SCHEMA[0])
    conn.commit()
    conn.close()


def _righe_controllo(log, task_id):
    prefisso = "[" + task_id + "] TRADE_IDEA_ACTIVE_CHECK "
    return [json.loads(riga[len(prefisso):]) for riga in log.splitlines() if riga.startswith(prefisso)]


@pytest.mark.parametrize("stato", ["parziale", "assente", "sano"])
def test_guardia_os_della_run_pagata_consultata_anche_con_store_non_pronto(stato, ambiente, monkeypatch, tmp_path):
    """M2 (R14b): store Trade Idea non pronto o assente NON spegne la guardia OS della run
    pagata da CLI (committee_paid_run.lock): due run pagate insieme = 409, nessuno spawn."""
    from bellomberg.api import trade_idea_routes as routes
    c, p, chiamate, api = ambiente
    p.write_text(json.dumps(_valido()), encoding="utf-8")
    consultata = []
    monkeypatch.setattr(routes, "paid_run_is_active", lambda: consultata.append(1) or True)
    path = tmp_path / ("m2_" + stato + ".db")
    if stato != "assente":
        _db_sintetico(path, stato)
    monkeypatch.setattr(api, "valuation_db_path", path)
    r = c.post("/consigliere/run")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "un'altra run pagata e' attiva"
    assert consultata == [1] and chiamate == []
    assert api.run_state.runs == {}


def test_errore_store_non_classificato_dichiara_il_controllo_non_eseguito(ambiente, monkeypatch, tmp_path):
    """B4 (R14b): un errore dello store fuori dalla diagnosi (RuntimeError) = not_run dichiarato
    col tipo d'errore, mai «completed»; la guardia OS resta consultata e la weekly parte."""
    from bellomberg.api import trade_idea_routes as routes
    c, p, chiamate, api = ambiente
    p.write_text(json.dumps(_valido()), encoding="utf-8")
    consultata = []
    monkeypatch.setattr(routes, "paid_run_is_active", lambda: consultata.append(1) or False)

    def guasto(*_a, **_k):
        raise RuntimeError("guasto sintetico")
    monkeypatch.setattr(api, "TradeIdeaStore", guasto)
    r = c.post("/consigliere/run")
    assert r.status_code == 200, r.text
    assert len(chiamate) == 1 and consultata == [1]
    check = r.json()["trade_idea_active_check"]
    assert check == {"status": "not_run", "reason": "trade_idea_store_error:RuntimeError", "storage": None}
    log = (tmp_path / "consigliere_run.log").read_text(encoding="utf-8")
    assert _righe_controllo(log, r.json()["task_id"]) == [check]
    assert "storage_status=None error_code=None" in log


@pytest.mark.parametrize("stato", ["sano", "parziale", "occupato"])
def test_esito_controllo_trade_idea_scritto_nel_log_come_json(stato, ambiente, monkeypatch, tmp_path):
    """B4 (R14b): run_state e' in memoria; l'esito del controllo resta nel log della run
    come riga JSON compatta (sempre), e la riga NOT RUN porta storage.status/error_code:
    lock passeggero (db_busy, action retry) distinto da migrazione mancante."""
    import functools
    import sqlite3
    from bellomberg.api import trade_idea_routes as routes
    from bellomberg.storage.trade_idea_store import TradeIdeaStore
    c, p, chiamate, api = ambiente
    p.write_text(json.dumps(_valido()), encoding="utf-8")
    monkeypatch.setattr(routes, "paid_run_is_active", lambda: False)
    path = tmp_path / ("b4_" + stato + ".db")
    _db_sintetico(path, "parziale" if stato == "parziale" else "sano")
    monkeypatch.setattr(api, "valuation_db_path", path)
    # store vero, attesa del lock accorciata (default 10 s): nessuno stub della diagnosi
    monkeypatch.setattr(api, "TradeIdeaStore", functools.partial(TradeIdeaStore, lock_busy_s=0.2))
    holder = None
    if stato == "occupato":
        holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        holder.execute("BEGIN EXCLUSIVE")
    try:
        r = c.post("/consigliere/run")
    finally:
        if holder is not None:
            holder.execute("ROLLBACK")
            holder.close()
    assert r.status_code == 200, r.text
    assert len(chiamate) == 1
    check = r.json()["trade_idea_active_check"]
    task_id = r.json()["task_id"]
    log = (tmp_path / "consigliere_run.log").read_text(encoding="utf-8")
    assert _righe_controllo(log, task_id) == [check]
    atteso = {"sano": None, "parziale": ("schema_partial", True, "run_explicit_migration"),
              "occupato": ("db_busy", False, "retry")}[stato]
    if atteso is None:
        assert check == {"status": "completed", "reason": None, "storage": None}
        assert "TRADE IDEA ACTIVE CHECK NOT RUN" not in log
        return
    status, update_required, action = atteso
    assert check["status"] == "not_run" and check["reason"] == "trade_idea_storage_unavailable"
    assert check["storage"]["status"] == status
    assert check["storage"]["error_code"] == "trade_idea_" + status
    assert check["storage"]["update_required"] is update_required
    assert check["storage"]["action"] == action
    assert ("[" + task_id + "] TRADE IDEA ACTIVE CHECK NOT RUN: trade_idea_storage_unavailable"
            " storage_status=" + status + " error_code=trade_idea_" + status) in log.splitlines()
