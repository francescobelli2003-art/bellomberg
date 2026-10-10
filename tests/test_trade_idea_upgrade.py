"""Public upgrade API contract: synthetic SQLite and no legacy workbook fixture."""
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from test_trade_idea_store import BASE_SCHEMA, db_path, migrated  # noqa: F401 (fixture)


def test_storage_upgrade_api_preserves_detail_and_adds_action(tmp_path):
    from bellomberg.api import trade_idea_routes as routes
    path = tmp_path / "pre-trade-idea.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(BASE_SCHEMA)
        before = conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    app = FastAPI()
    routes.install_trade_idea_routes(app, lambda: "synthetic-session", db_path=path)
    with TestClient(app) as client:
        response = client.get("/trade-ideas/runs")
    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"] == "Trade Idea storage non pronto: Trade Idea schema absent: run explicit migration"
    assert payload.get("error_code") == "trade_idea_schema_absent"
    assert payload["storage"]["status"] == "schema_absent"
    assert payload["storage"]["action"] == "run_explicit_migration"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall() == before


@pytest.mark.parametrize("state", ["db_missing", "db_unreadable"])
def test_storage_api_does_not_offer_migration_for_database_path_or_integrity_failure(tmp_path, state):
    from bellomberg.api import trade_idea_routes as routes
    path = tmp_path / "private-location.db"
    if state == "db_unreadable":
        path.write_bytes(b"synthetic corrupt SQLite")
    app = FastAPI()
    routes.install_trade_idea_routes(app, lambda: "synthetic-session", db_path=path)
    with TestClient(app) as client:
        response = client.get("/trade-ideas/active")
    assert response.status_code == 503
    assert response.json()["storage"]["status"] == state
    assert response.json()["storage"]["update_required"] is False
    assert str(path) not in response.text
    assert path.exists() is (state == "db_unreadable")


def test_preflight_storage_block_does_not_call_source_qualification(tmp_path, monkeypatch):
    from functools import partial
    from bellomberg.agents import trade_idea
    from bellomberg.api import trade_idea_routes as routes
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(BASE_SCHEMA)
    catalogue = {"models": {role: {"id": model, "context_length": 500000,
        "max_completion_tokens": 128000, "supported_efforts": ["low", "medium", "high", "max"],
        "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
        for role, model in trade_idea.MODEL_IDS.items()}}
    def never_qualify(*args, **kwargs):
        pytest.fail("blocked storage must not acquire sources")
    monkeypatch.setattr(routes, "preflight_trade_idea", partial(trade_idea.preflight_trade_idea,
        catalog_fetcher=lambda: catalogue, key_checker=lambda: None, mandate_loader=lambda: {},
        identity_resolver=lambda ticker: {"ticker": ticker, "name": "Synthetic", "exchange": "XNAS",
            "currency": "USD", "status": "confirmed", "reason": None}, source_qualifier=never_qualify))
    monkeypatch.setattr(routes, "_spawn_worker", lambda *a, **k: pytest.fail("storage blocked worker start"))
    app = FastAPI()
    routes.install_trade_idea_routes(app, lambda: "synthetic-session", db_path=path,
                                    source_archive_root=tmp_path / "archive")
    with TestClient(app) as client:
        response = client.post("/trade-ideas/preflight", json={"ticker": "TEST", "budget_limit_usd": "5"})
        start = client.post("/trade-ideas/runs", json={"ticker": "TEST", "budget_limit_usd": "5",
            "idempotency_key": "synthetic-upgrade", "cost_acknowledged": True,
            "authorization": {"accepted": True, "source_fingerprint": "a" * 64,
                "activities": ["committee"], "max_revision_rounds": 0}})
    assert start.status_code == 503
    assert start.json()["storage"]["status"] == "schema_absent"
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["identity"]["status"] == "confirmed"
    assert body["storage"]["error_code"] == "trade_idea_schema_absent"
    assert body["source_qualification"]["execution_status"] == "not_run"
    assert body["source_qualification"]["reasons"] == []
    assert len(body["reasons"]) == 1 and "storage non pronto" in body["reasons"][0]


# ---- 09/10 (Opus 5.5): lacune B1/B2/B6 della riverifica R14 -------------------------
STORAGE_KEYS = {"status", "error_code", "update_required", "action", "documentation"}


def _partial_state(conn, state):
    from bellomberg.storage.trade_idea_store import SCHEMA, ensure_schema
    if state == "solo_runs":
        conn.execute(SCHEMA[0])
        return "schema_partial"
    if state == "forma_diversa":
        conn.execute("CREATE TABLE trade_idea_runs(id TEXT)")
        return "schema_incompatible"
    ensure_schema(conn)
    if state == "indice_mancante":
        conn.execute("DROP INDEX idx_trade_idea_one_active")
        return "schema_partial"
    return None


@pytest.mark.parametrize("state", ["solo_runs", "forma_diversa", "indice_mancante", "sano"])
def test_workspace_storage_refusal_uses_the_shared_contract(tmp_path, state):
    """B1: /workspace con storage non pronto = 503 + error_code + storage, come le altre rotte."""
    from bellomberg.api.trade_idea_workspace_routes import install_trade_idea_workspace_routes
    path = tmp_path / "workspace.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(BASE_SCHEMA)
        expected = _partial_state(conn, state)
    app = FastAPI()
    install_trade_idea_workspace_routes(app, lambda: "synthetic-session", db_path=path)
    with TestClient(app) as client:
        response = client.get("/trade-ideas/runs/synthetic-run/workspace")
    if expected is None:
        assert response.status_code == 404, response.text
        return
    assert response.status_code == 503, response.text
    payload = response.json()
    assert set(payload) == {"detail", "error_code", "storage"}
    assert set(payload["storage"]) == STORAGE_KEYS
    assert payload["storage"]["status"] == expected
    assert payload["error_code"] == payload["storage"]["error_code"] == "trade_idea_" + expected
    assert payload["storage"]["update_required"] is (expected == "schema_partial")
    assert payload["storage"]["documentation"] == "docs/TRADE_IDEA.md#aggiornamento-storage"
    assert payload["detail"].startswith("Trade Idea storage non pronto: ")
    assert str(path) not in response.text and str(tmp_path) not in response.text


from test_persistence import db as api_db  # noqa: E402,F401


def _decisions(api_db, monkeypatch, url="/decisions"):
    from bellomberg.api import bellomberg_api as api
    monkeypatch.setattr(api, "get_db", lambda: api_db)
    api.app.dependency_overrides[api.require_session] = lambda: None
    try:
        return TestClient(api.app, base_url="http://127.0.0.1",
                          raise_server_exceptions=False).get(url)
    finally:
        api.app.dependency_overrides.pop(api.require_session, None)


@pytest.mark.parametrize("state", ["assente", "solo_runs", "forma_diversa", "indice_mancante", "sano"])
def test_decisions_survive_partial_trade_idea_schema_and_declare_it(api_db, monkeypatch, state):
    """B2: schema Trade Idea parziale/incompatibile = decisioni + note servite, provenienza
    Trade Idea dichiarata n.d. in trade_idea_storage; mai 500, mai buco silenzioso."""
    with api_db._conn() as conn:
        research = conn.execute("INSERT INTO decisions(ticker,action,status,timestamp) VALUES(?,?,?,?)",
                                ("ZZB2RES", "RESEARCH", "PENDING", "2031-01-01T00:00:00")).lastrowid
        operative = conn.execute("INSERT INTO decisions(ticker,action,status,timestamp) VALUES(?,?,?,?)",
                                 ("ZZB2OPE", "BUY", "PENDING", "2031-01-01T00:00:00")).lastrowid
        expected = None if state == "assente" else _partial_state(conn, state)
    assert api_db.add_decision_note(research, "PM", "nota sintetica B2")
    response = _decisions(api_db, monkeypatch)
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    rows = {row["id"]: row for row in body["decisions"]}
    assert {research, operative} <= set(rows)
    assert rows[research]["notes_status"] == "available"
    assert [note["testo"] for note in rows[research]["notes"]] == ["nota sintetica B2"]
    assert "trade_idea_storage" in body
    if expected is None:
        assert body["trade_idea_storage"] is None
        return
    storage = body["trade_idea_storage"]
    assert set(storage) == STORAGE_KEYS
    assert storage["status"] == expected
    assert storage["error_code"] == "trade_idea_" + expected
    assert storage["update_required"] is (expected == "schema_partial")
    assert storage["action"] == ("run_explicit_migration" if expected == "schema_partial" else "inspect_schema")
    assert rows[research]["trade_idea"] is None and rows[operative]["trade_idea"] is None


def test_decisions_unclassified_trade_idea_failure_is_declared_without_sql_text(api_db, monkeypatch):
    """B2: un guasto della lettura provenienza non classificato dalla diagnosi esce
    lookup_failed dichiarato, senza il testo SQL, e le decisioni restano servite."""
    from bellomberg.storage import trade_idea_store as tis
    with api_db._conn() as conn:
        conn.execute("INSERT INTO decisions(ticker,action,status,timestamp) VALUES(?,?,?,?)",
                     ("ZZB2LKP", "RESEARCH", "PENDING", "2031-01-01T00:00:00"))
        _partial_state(conn, "sano")
    def broken(self, ids):
        raise sqlite3.OperationalError("SEGRETO_SQL_SINTETICO")
    monkeypatch.setattr(tis.TradeIdeaStore, "lookup_decisions", broken)
    response = _decisions(api_db, monkeypatch)
    assert response.status_code == 200, response.text[:300]
    assert response.json()["trade_idea_storage"] == {
        "status": "lookup_failed", "error_code": "trade_idea_lookup_failed", "update_required": False,
        "action": "check_database_access", "documentation": "docs/TRADE_IDEA.md#aggiornamento-storage"}
    assert "SEGRETO_SQL_SINTETICO" not in response.text
    assert [row["ticker"] for row in response.json()["decisions"]] == ["ZZB2LKP"]


def test_saved_run_without_execution_marker_is_declared_legacy(migrated):
    """B6: run salvata senza marcatore (accettata prima del 09/10) = unknown_legacy,
    mai completed inventato; un marcatore diverso da completed e' rifiutato all'accettazione."""
    from bellomberg.api import trade_idea_routes as routes
    from test_trade_idea_store import store
    from test_trade_idea_pipeline import _priced_request
    current = store(migrated)
    legacy = current.create_run(_priced_request(), idempotency_key="b6-legacy")["run"]
    assert legacy.get("source_qualification_execution") is None
    public = routes._public_run(legacy)
    assert public["source_qualification"]["execution_status"] == "unknown_legacy"
    assert "source_qualification_execution" not in public
    with pytest.raises(ValueError, match="completed"):
        current.create_run({**_priced_request(), "source_qualification_execution": "failed"},
                           idempotency_key="b6-failed")


# ---- 10/10 (R14b, Opus 5.5): riserve M1/M5/B4 della revisione -------------------------
def test_decisions_second_batch_failure_drops_partial_provenance(api_db, monkeypatch):
    """M1 (R14b): ?limit>1000 legge la provenienza a lotti da 1000; un guasto al SECONDO lotto
    non lascia esposta la provenienza parziale del primo: tutto n.d., lookup_failed dichiarato."""
    from bellomberg.storage import trade_idea_store as tis
    with api_db._conn() as conn:
        for i in range(1500):
            conn.execute("INSERT INTO decisions(ticker,action,status,timestamp) VALUES(?,?,?,?)",
                         ("ZZM1%04d" % i, "RESEARCH", "PENDING", "2031-01-01T00:00:00"))
        _partial_state(conn, "sano")
    calls = []

    def flaky(self, chunk):
        calls.append(len(chunk))
        if len(calls) == 1:
            return {chunk[0]: {"run_id": "SYNTHETIC-M1", "status": "delivered"}}
        raise sqlite3.OperationalError("SEGRETO_SQL_SINTETICO")
    monkeypatch.setattr(tis.TradeIdeaStore, "lookup_decisions", flaky)
    response = _decisions(api_db, monkeypatch, "/decisions?limit=1500")
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert calls == [1000, 500]
    assert len(body["decisions"]) == 1500
    assert [row["id"] for row in body["decisions"] if row.get("trade_idea")] == []
    assert body["trade_idea_storage"]["status"] == "lookup_failed"
    assert "SYNTHETIC-M1" not in response.text and "SEGRETO_SQL_SINTETICO" not in response.text


def _rollback_journal_db(path):
    from bellomberg.storage.trade_idea_store import ensure_schema
    conn = sqlite3.connect(path)
    conn.executescript(BASE_SCHEMA)
    ensure_schema(conn)
    conn.commit()
    conn.close()


def test_storage_exclusive_lock_is_db_busy_retry_not_unreadable(tmp_path):
    """B4 (R14b): un lock esclusivo passeggero e' db_busy/retry, non db_unreadable; la
    diagnosi resta in sola lettura e lo stesso DB, liberato, apre."""
    from bellomberg.storage.trade_idea_store import StorageNotReady, TradeIdeaStore
    path = tmp_path / "busy.db"
    _rollback_journal_db(path)
    before = path.read_bytes()
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(StorageNotReady) as caught:
            TradeIdeaStore(path, lock_busy_s=0.2)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert caught.value.storage == {
        "status": "db_busy", "error_code": "trade_idea_db_busy", "update_required": False,
        "action": "retry", "documentation": "docs/TRADE_IDEA.md#aggiornamento-storage"}
    assert str(path) not in str(caught.value)
    assert path.read_bytes() == before
    TradeIdeaStore(path, lock_busy_s=0.2)


@pytest.mark.parametrize("kind,code,expected", [
    ("operational", None, False),  # Python 3.10 o eccezione senza codice: mai dal testo
    ("operational", 5, True), ("operational", 6, True),
    ("operational", 261, True), ("operational", 517, True),  # SQLITE_BUSY_RECOVERY/_SNAPSHOT
    ("operational", 11, False), ("operational", 14, False),  # CORRUPT, CANTOPEN
    ("database", 5, False),
])
def test_busy_classification_uses_sqlite_error_code_not_text(kind, code, expected):
    from bellomberg.storage.trade_idea_store import _db_occupato
    cls = sqlite3.OperationalError if kind == "operational" else sqlite3.DatabaseError
    exc = cls("database is locked")
    if code is not None:
        exc.sqlite_errorcode = code
    assert _db_occupato(exc) is expected


def _admission_app(migrated, tmp_path, monkeypatch, mode):
    from copy import deepcopy
    from fastapi import Header, HTTPException
    from bellomberg.agents import trade_idea, trade_idea_sources as sources
    from bellomberg.api import trade_idea_routes as routes
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    from bellomberg.valuation import trade_idea_model as model, input_preparation
    from test_trade_idea_store import store
    from test_trade_idea_pipeline import _priced_request
    from test_trade_idea_pm_sources import IDENTITY, transport, _profile_providers
    current = store(migrated)
    priced = _priced_request(budget="30")
    for row in priced["catalog_snapshot"]["models"].values():
        row["supported_efforts"] = ["low", "medium", "max"]
    download, _calls = transport("Unreadable source; no primary issuer or period")
    original_ingest = sources.ingest_document_sources
    monkeypatch.setattr(sources, "ingest_document_sources",
                        lambda *a, **k: original_ingest(*a, **k, download=download))
    monkeypatch.setattr(input_preparation, "has_approved_inputs", lambda *a, **k: pytest.fail("excel"))

    def preflight(ticker, pm_view, view_source, budget, **options):
        start = "identity_resolver" in options  # solo POST /runs pinna l'identita del preflight

        def qualify(t, identity, day, **kwargs):
            return model.research_admission(t, identity, day, providers=_profile_providers(day),
                                            analysis_mode=RESEARCH_ANALYSIS_MODE, **kwargs)
        qualifier = options.get("source_qualifier", qualify)
        if start and mode == "senza_controverifica":
            qualifier = qualify  # preflight che non richiama la controverifica della rotta
        out = trade_idea.preflight_trade_idea(ticker, pm_view, view_source, budget,
            analysis_mode=options.get("analysis_mode"), archive_root=options["archive_root"],
            execution_policy=options.get("execution_policy"), source_qualifier=qualifier,
            document_sources=options.get("document_sources", []),
            catalog_fetcher=lambda: priced["catalog_snapshot"],
            identity_resolver=options.get("identity_resolver", lambda _: deepcopy(IDENTITY)),
            key_checker=lambda: None, mandate_loader=lambda: {}, active_checker=lambda: False)
        if start and mode == "misura_non_completed":
            out["source_qualification"]["execution_status"] = "failed"
        return out
    workers = []
    monkeypatch.setattr(routes, "_store", lambda *_a, **_k: current)
    monkeypatch.setattr(routes, "preflight_trade_idea", preflight)
    monkeypatch.setattr(routes, "_spawn_worker", lambda rid, *_a, **_k: workers.append(rid))
    app = FastAPI()

    def session(x_bb_token: str = Header(default="")):
        if x_bb_token != "m5":
            raise HTTPException(401)
        return x_bb_token
    routes.install_trade_idea_routes(app, session, db_path=migrated, source_archive_root=tmp_path / "sources")
    body = {"ticker": IDENTITY["ticker"], "pm_view": "ipotesi sintetica", "view_source": "manual",
            "budget_limit_usd": "30", "document_sources": []}
    return app, current, workers, body


@pytest.mark.parametrize("mode", ["normale", "senza_controverifica", "misura_non_completed"])
def test_start_marker_is_derived_from_the_measured_counter_check(migrated, tmp_path, monkeypatch, mode):
    """M5 (R14b): il marcatore salvato da POST /runs e' la misura del preflight di avvio
    (source_qualification.execution_status) e la controverifica deve essere tornata nella
    rotta: altrimenti 428 col messaggio, nessuna run accettata, nessun worker."""
    app, current, workers, body = _admission_app(migrated, tmp_path, monkeypatch, mode)
    with TestClient(app, headers={"X-BB-Token": "m5"}) as client:
        public = client.post("/trade-ideas/preflight", json=body)
        assert public.status_code == 200 and public.json()["ok"], public.text
        assert public.json()["source_qualification"]["execution_status"] == "completed"
        start = {**body, "cost_acknowledged": True, "idempotency_key": "m5-" + mode,
                 "authorization": {"accepted": True,
                                   "source_fingerprint": public.json()["source_qualification"]["fingerprint"],
                                   "activities": ["committee"], "max_revision_rounds": 0}}
        response = client.post("/trade-ideas/runs", json=start)
        if mode != "normale":
            measured = "completed" if mode == "senza_controverifica" else "failed"
            assert response.status_code == 428, response.text
            assert response.json()["detail"] == ("Controverifica delle fonti del preflight non eseguita "
                                                 "(execution_status=" + measured + ")")
            assert current.list_runs()["total"] == 0 and workers == []
            return
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        shown = client.get("/trade-ideas/runs/" + run_id).json()["run"]
    assert current.get_accepted_request(run_id)["source_qualification_execution"] == "completed"
    assert shown["source_qualification"]["execution_status"] == "completed"
    assert workers == [run_id]
