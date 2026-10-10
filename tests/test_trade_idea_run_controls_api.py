"""Authenticated admission binds the accepted source pin and exact run activities."""
from copy import deepcopy
from datetime import datetime, timezone
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from bellomberg.api import trade_idea_routes as routes
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.core.trade_idea_policy import role_effort
from test_trade_idea_store import db_path, migrated, store
from test_trade_idea_pipeline import _priced_request


@pytest.fixture
def admission(migrated, monkeypatch):
    # Contratto attuale (Excel archiviato, decisione PM): l'avvio HTTP ammette solo la
    # ricerca. La qualificazione e' quella VERA di research_admission (impronta e bundle
    # ricontrollati dal route e dallo store); il grant ammesso e' solo 'committee'.
    from bellomberg.api import bellomberg_api as api
    from bellomberg.agents.trade_idea import source_qualification_summary
    from bellomberg.valuation import trade_idea_model as model
    from test_trade_idea_pm_sources import IDENTITY, _profile_providers
    current = store(migrated)
    accepted = _priced_request(budget="30")
    day = datetime.now(timezone.utc).date().isoformat()
    qualification = model.research_admission(IDENTITY["ticker"], deepcopy(IDENTITY), day,
        archive_root=migrated.parent / 'archive', providers=_profile_providers(day),
        analysis_mode=RESEARCH_ANALYSIS_MODE)
    assert qualification["status"] == "research_required", qualification["reasons"]
    # Percorsi privati dentro la qualificazione: il preflight pubblico non deve esporli.
    qualification["source_report"]["documents"] = [{"id": "filing", "archive_path": "PRIVATE_ARCHIVE_CANARY"}]
    qualification["fingerprint"] = model.source_fingerprint(qualification)
    grant = {"accepted": True, "source_fingerprint": qualification["fingerprint"],
             "activities": ["committee"], "max_revision_rounds": 0}
    calls, workers = [], []
    def preflight(*_a, **_k):
        calls.append(1)
        identity = deepcopy(IDENTITY)
        try:
            selected = (_k['source_qualifier'](IDENTITY["ticker"], identity, qualification['as_of'])
                        if 'source_qualifier' in _k else qualification)
        except Exception as exc:
            # Come il preflight vero: la controverifica fallita e' un motivo di blocco, non un 500.
            return {"ok": False, "reasons": ["fonti non qualificabili: " + type(exc).__name__ + ": " + str(exc)]}
        policy = {"analysis_mode": _k.get("analysis_mode"), "execution_policy": _k.get("execution_policy")}
        return {"ok": True, "reasons": [], "identity": deepcopy(IDENTITY),
            "budget": {"status": "ready", "limit_usd": "30"},
            "models": [{"role": role, "model": spec["model"], "reasoning_effort": role_effort(policy, role)}
                       for role, spec in accepted["models"].items()],
            "catalog_snapshot": deepcopy(accepted["catalog_snapshot"]),
            # come il preflight vero: qualificatore tornato = execution_status 'completed'
            # (10/10 R14b M5, Opus 5.5: POST /runs salva questa misura, non un letterale)
            "source_qualification": source_qualification_summary(selected, execution_status="completed"),
            "_source_qualification": deepcopy(selected),
            "preparation": {"required": False, "paid": False, "status": "not_required"},
            "analysis_mode": _k.get("analysis_mode"),
            **({"execution_policy": _k["execution_policy"]} if _k.get("execution_policy") else {})}
    monkeypatch.setattr(routes, "_store", lambda *_a, **_k: current)
    monkeypatch.setattr(routes, "preflight_trade_idea", preflight)
    # This suite isolates HTTP grant/idempotency. Documentary counterproof is
    # exercised with real archive bytes by test_trade_idea_pm_sources.py.
    from bellomberg.valuation import trade_idea_model
    monkeypatch.setattr(trade_idea_model, 'recheck_accepted_sources', lambda saved, *_a, **_k: deepcopy(saved))
    monkeypatch.setattr(routes, "_spawn_worker", lambda run, *_a, **_k: workers.append(run))
    monkeypatch.setattr(api, "_SESSIONS", {"admission-session": time.time() + 3600})
    app = FastAPI()
    routes.install_trade_idea_routes(app, api.require_session, db_path=migrated,
                                     source_archive_root=migrated.parent / 'archive')
    body = {"ticker": IDENTITY["ticker"], "pm_view": "Source-bound synthetic thesis", "view_source": "manual",
        "budget_limit_usd": "30", "idempotency_key": "isolated-admission",
        "cost_acknowledged": True, "authorization": grant}
    with TestClient(app, headers={"X-BB-Token": "admission-session"}) as client:
        yield client, current, body, qualification, calls, workers


def test_public_preflight_excludes_private_source_bundle_and_document_paths(admission):
    client, current, body, qualification, calls, workers = admission
    response = client.post("/trade-ideas/preflight", json={key: body[key] for key in
        ("ticker", "pm_view", "view_source", "budget_limit_usd")})
    assert response.status_code == 200
    assert "PRIVATE_ARCHIVE_CANARY" not in response.text and "_source_qualification" not in response.text
    assert response.json()["source_qualification"]["fingerprint"] == body["authorization"]["source_fingerprint"]
    assert current.list_runs()["total"] == 0 and workers == []


@pytest.mark.parametrize("mutation", ["missing_grant", "false_consent", "changed_source_pin", "excel_preparation"])
def test_incomplete_admission_never_creates_a_run_or_worker(admission, mutation):
    client, current, body, qualification, calls, workers = admission
    body = deepcopy(body)
    if mutation == "changed_source_pin":
        # Il preflight vede un'impronta fonti diversa da quella del grant gia' firmato.
        qualification["fingerprint"] = "c" * 64
    # Preflight eseguito davvero: ogni mutazione deve cadere per il SUO motivo
    # (prima del 05/10 cadevano tutte per lo snapshot assente).
    checked = client.post('/trade-ideas/preflight', json={key: body[key] for key in
        ('ticker', 'pm_view', 'view_source', 'budget_limit_usd')})
    assert checked.status_code == 200 and checked.json()["ok"], checked.text
    if mutation == "missing_grant":
        body.pop("authorization")
    elif mutation == "false_consent":
        body["cost_acknowledged"] = False
    elif mutation == "excel_preparation":
        # Excel archiviato: un grant che chiede la preparazione del modello non avvia nulla.
        body["authorization"].update(activities=["model_preparation", "committee"], max_revision_rounds=0)
    response = client.post("/trade-ideas/runs", json=body)
    expected = {"missing_grant": (422, "authorization"),
                "false_consent": (428, "Conferma del limite di spesa"),
                "changed_source_pin": (428, "Snapshot del preflight assente"),
                "excel_preparation": (428, "Research-only authorization permits committee research")}[mutation]
    assert response.status_code == expected[0] and expected[1] in response.text, response.text
    assert current.list_runs()["total"] == 0 and workers == []


def test_accepted_http_run_and_exact_retry_keep_one_source_pin_and_worker(admission):
    client, current, body, qualification, calls, workers = admission
    checked = client.post('/trade-ideas/preflight', json={key: body[key] for key in
        ('ticker', 'pm_view', 'view_source', 'budget_limit_usd')})
    assert checked.status_code == 200, checked.text
    first = client.post("/trade-ideas/runs", json=body)
    assert first.status_code == 202, first.text
    run_id = first.json()["run_id"]
    qualification["fingerprint"] = "c" * 64
    retry = client.post("/trade-ideas/runs", json=body)
    assert retry.status_code == 202 and retry.json()["run_id"] == run_id
    assert calls == [1, 1] and workers == [run_id]
    detail = current.get_run(run_id)
    assert detail["run"]["authorization"] == body["authorization"]
    assert detail["run"]["source_qualification"]["fingerprint"] == body["authorization"]["source_fingerprint"]
    assert detail["cost"]["requests"] == 0
    changed = deepcopy(body)
    changed["authorization"].update(activities=["committee", "model_revision"], max_revision_rounds=1)
    assert client.post("/trade-ideas/runs", json=changed).status_code == 409
    assert current.list_runs()["total"] == 1 and workers == [run_id]


def test_unauthenticated_admission_calls_no_preflight_or_worker(admission):
    client, current, body, qualification, calls, workers = admission
    assert client.post("/trade-ideas/runs", json=body, headers={"X-BB-Token": "expired"}).status_code == 401
    assert calls == workers == [] and current.list_runs()["total"] == 0


ROUTES_RUN = [("get", "", "lettura"), ("post", "/stop", "arresto"),
              ("post", "/delivery/recover", "consegna"), ("post", "/costs/reconcile", "riconciliazione"),
              ("get", "/artifacts/synthetic-artifact", "download"), ("post", "/email/retry", "email")]


@pytest.mark.parametrize("method,suffix,operation", ROUTES_RUN)
def test_absent_run_is_404_on_every_run_route(admission, method, suffix, operation):
    # Z3b 05/10 (classe C): la run assente e' dichiarata dallo store vero (TradeIdeaStore._row).
    client, current, body, qualification, calls, workers = admission
    response = getattr(client, method)("/trade-ideas/runs/absent-run" + suffix)
    assert response.status_code == 404 and "Run Trade Idea non trovata" in response.text, response.text
    assert workers == [] and current.list_runs()["total"] == 0


@pytest.mark.parametrize("method,suffix,operation", ROUTES_RUN)
def test_internal_missing_key_is_declared_not_reported_as_absent(admission, monkeypatch, method, suffix, operation):
    # Prima del 05/10 ogni KeyError diventava 404 «non trovata»: una riga di consegna o una
    # riserva di costo mancante sembravano una run inesistente (fallback silenzioso).
    client, current, body, qualification, calls, workers = admission
    def broken(*_a, **_k):
        raise KeyError("delivery row absent")
    monkeypatch.setattr(current, "request_stop", lambda *_a, **_k: None)
    monkeypatch.setattr(current, "get_run", broken)
    response = getattr(client, method)("/trade-ideas/runs/inconsistent-run" + suffix)
    assert response.status_code == 500, response.text
    assert ("Record Trade Idea incoerente (KeyError): operazione di " + operation + " bloccata") in response.text
    assert "non trovata" not in response.text and "delivery row" not in response.text and workers == []


def test_research_qualification_never_carries_an_excel_grant_by_construction(migrated, tmp_path):
    """Z3b 05/10, verifica chiesta da main: validate_run_authorization salta il controllo
    'solo committee' se una qualificazione di ricerca avesse status diverso da research_required.
    Qui si FISSA che nessun produttore reale crea quello stato e che la qualificazione vera
    con un grant Excel e' rifiutata da contratto e store prima di creare una run."""
    from bellomberg.core.trade_idea_contract import validate_run_authorization
    from bellomberg.valuation import trade_idea_model as model
    from test_trade_idea_pm_sources import IDENTITY, _profile_providers
    day = datetime.now(timezone.utc).date().isoformat()
    produced = [model.research_admission(IDENTITY["ticker"], deepcopy(IDENTITY), day,
                    archive_root=tmp_path, providers=_profile_providers(day),
                    analysis_mode=RESEARCH_ANALYSIS_MODE),
                model.research_admission(IDENTITY["ticker"], dict(IDENTITY, name="Other issuer"), day,
                    archive_root=tmp_path, providers=_profile_providers(day),
                    analysis_mode=RESEARCH_ANALYSIS_MODE)]
    admitted = produced[0]
    produced.append(model.recheck_accepted_sources(admitted, IDENTITY["ticker"], deepcopy(IDENTITY),
                                                   archive_root=tmp_path))
    produced.append(model.research_source_qualification(admitted, {"status": "empty", "revision_id": None,
        "revision_sha256": None, "documents": [], "session_sha256": "e" * 64, "ticker": IDENTITY["ticker"],
        "as_of": day, "parent_grant_fingerprint": admitted["fingerprint"], "run_id": "synthetic-run"}))
    # I produttori reali: la modalita' ricerca esce SOLO come research_required o blocked.
    assert [row["status"] for row in produced] == ["research_required", "blocked",
                                                    "research_required", "research_required"]
    assert all(row["analysis_mode"] == RESEARCH_ANALYSIS_MODE for row in produced)
    for qualification in produced:
        for activities, rounds in ((["model_preparation", "committee"], 0),
                                   (["committee", "model_revision"], 1),
                                   (["model_preparation", "committee", "model_revision"], 1)):
            grant = {"accepted": True, "source_fingerprint": qualification["fingerprint"],
                     "activities": activities, "max_revision_rounds": rounds}
            with pytest.raises(ValueError):
                validate_run_authorization(grant, qualification)
    # Lo store (ultima porta prima del DB) rifiuta la qualificazione vera con grant Excel.
    current = store(migrated)
    priced = _priced_request(budget="30")
    request = {**priced, "analysis_mode": RESEARCH_ANALYSIS_MODE, "ticker": IDENTITY["ticker"],
        "company_name": IDENTITY["name"], "exchange": IDENTITY["exchange"], "currency": IDENTITY["currency"],
        "source_qualification": admitted,
        "authorization": {"accepted": True, "source_fingerprint": admitted["fingerprint"],
                          "activities": ["model_preparation", "committee"], "max_revision_rounds": 0}}
    with pytest.raises(ValueError, match="Research-only authorization"):
        current.create_run(request, idempotency_key="research-with-excel-grant")
    assert current.list_runs()["total"] == 0


@pytest.mark.parametrize("status", ["qualified", "preparation_required"])
@pytest.mark.parametrize("activities,rounds", [(["model_preparation", "committee"], 0),
                                               (["committee"], 0), (["committee", "model_revision"], 1)])
def test_research_mode_with_non_research_status_is_refused_by_the_spending_gate(status, activities, rounds):
    """Z3b 05/10, difesa in profondita' voluta da main: prima la funzione ACCETTAVA
    ricerca + status 'qualified' + grant Excel (misurato). Ora qualunque grant e' rifiutato."""
    from bellomberg.core.trade_idea_contract import validate_run_authorization
    qualification = {"status": status, "fingerprint": "b" * 64, "analysis_mode": RESEARCH_ANALYSIS_MODE,
                     "method_id": "operating_fcff", "coverage": {}, "reasons": []}
    grant = {"accepted": True, "source_fingerprint": "b" * 64,
             "activities": activities, "max_revision_rounds": rounds}
    with pytest.raises(ValueError, match="requires research_required sources; status " + status):
        validate_run_authorization(grant, qualification)


def test_legacy_qualified_sources_keep_their_historical_authorization():
    # Lo storico Excel (senza analysis_mode) non cambia: la difesa vale solo per la ricerca.
    from bellomberg.core.trade_idea_contract import validate_run_authorization
    qualification = {"status": "qualified", "fingerprint": "b" * 64, "method_id": "operating_fcff",
                     "coverage": {}, "reasons": []}
    grant = {"accepted": True, "source_fingerprint": "b" * 64,
             "activities": ["model_preparation", "committee"], "max_revision_rounds": 0}
    assert validate_run_authorization(grant, qualification)["activities"] == ["model_preparation", "committee"]
