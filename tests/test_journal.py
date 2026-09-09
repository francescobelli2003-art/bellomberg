"""Diario: vere transazioni SQLite su database temporaneo, senza LLM o rete."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from bellomberg.storage import memory_db
from bellomberg.storage.journal import (
    JOURNAL_MIGRATION, JournalStore, JournalConflict, JournalMissing,
    JournalUnavailable, JournalInvalid,
)
from bellomberg.api.journal_routes import create_journal_router


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    result = memory_db.MemoryDB(str(tmp_path / "data" / "test.db"), str(tmp_path / "chroma"))
    with result._conn() as conn:
        for statement in JOURNAL_MIGRATION[2]:
            conn.execute(statement)
    return result


def create(store, **overrides):
    return store.create(**({"kind": "thesis", "ticker": "SYNTH.MI", "title": "Tesi iniziale",
                           "body": "La mia tesi.\nCondizioni che la smentiscono."} | overrides))


def test_persistenza_origine_versioni_e_archivio_reversibile(db):
    store = JournalStore(db)
    a = create(store)
    assert a["origin"] == "user" and a["version"] == 1 and a["archived_at"] is None
    assert a["created_at"].endswith("Z")
    b = store.update(a["id"], expected_version=1, kind="macro", ticker=None,
                     title="Scenario aggiornato", body="Nuova evidenza")
    assert b["version"] == 2 and b["created_at"] == a["created_at"]
    c = store.set_archived(a["id"], expected_version=2, archived=True)
    assert c["archived_at"] and c["version"] == 3
    assert store.list_entries()["total"] == 0
    assert store.list_entries(status="archived")["total"] == 1
    d = store.set_archived(a["id"], expected_version=3, archived=False)
    assert d["archived_at"] is None and d["version"] == 4
    reread = JournalStore(db).get(a["id"])
    assert reread == d
    history = store.history(a["id"])
    assert [x["version"] for x in history["items"]] == [4, 3, 2, 1]
    assert [x["action"] for x in history["items"]] == ["restore", "archive", "update", "create"]
    assert history["items"][-1]["body"] == a["body"]


def test_conflitto_non_perde_o_duplica_versioni(db):
    store = JournalStore(db)
    a = create(store)
    store.update(a["id"], expected_version=1, kind="thesis", ticker="SYNTH.MI", title="A", body="B")
    with pytest.raises(JournalConflict) as error:
        store.update(a["id"], expected_version=1, kind="thesis", ticker=None, title="Stale", body="C")
    assert error.value.current_version == 2
    with pytest.raises(JournalConflict):
        store.set_archived(a["id"], expected_version=1, archived=True)
    assert store.get(a["id"])["title"] == "A"
    assert store.history(a["id"])["total"] == 2


def test_due_writer_concorrenti_non_sovrascrivono(db):
    a = create(JournalStore(db))
    def writer(label):
        try:
            return JournalStore(db).update(a["id"], expected_version=1, kind="macro", ticker=None,
                                            title=label, body="test")["version"]
        except JournalConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer, ["Prima", "Seconda"]))
    assert sorted(map(str, results)) == ["2", "conflict"]
    assert JournalStore(db).history(a["id"])["total"] == 2


def test_fallimento_storico_annulla_anche_aggiornamento(db):
    store = JournalStore(db)
    a = create(store)
    with db._conn() as conn:
        conn.execute("CREATE TRIGGER journal_fault BEFORE INSERT ON journal_revisions "
                     "BEGIN SELECT RAISE(ABORT, 'fault'); END")
    with pytest.raises(JournalUnavailable):
        store.update(a["id"], expected_version=1, kind="macro", ticker=None, title="Persa", body="test")
    assert store.get(a["id"]) == a
    assert store.history(a["id"])["total"] == 1


@pytest.mark.parametrize("override", [
    {"title": "   "}, {"body": ""}, {"title": "x" * 161}, {"body": "x" * 30001},
    {"kind": "ai"}, {"ticker": "BAD TICKER"}, {"ticker": "x" * 33}, {"body": "x\x00y"},
])
def test_input_invalidi_non_scrivono(db, override):
    store = JournalStore(db)
    with pytest.raises(JournalInvalid):
        create(store, **override)
    assert store.list_entries(status="all")["total"] == 0


def test_ticker_opzionale_e_ricerca_letterale(db):
    store = JournalStore(db)
    create(store, kind="macro", ticker=None, title="Inflazione 5%_test")
    create(store, ticker="new-synth", title="Altro")
    assert store.list_entries(query="%_test")["total"] == 1
    assert store.list_entries(ticker="NEW-SYNTH")["items"][0]["ticker"] == "NEW-SYNTH"
    assert store.list_entries(limit=1)["total"] == 2
    assert len(store.list_entries(limit=1, offset=1)["items"]) == 1


def test_id_assente_e_schema_assente_non_sembrano_elenco_vuoto(db):
    store = JournalStore(db)
    with pytest.raises(JournalMissing):
        store.get(999)
    with db._conn() as conn:
        conn.execute("DROP TABLE journal_revisions")
        conn.execute("DROP TABLE journal_entries")
    with pytest.raises(JournalUnavailable) as error:
        store.list_entries()
    assert error.value.code == "journal_schema_missing"


def test_schema_idempotente_e_nessun_tocco_ad_altre_tabelle(db):
    with db._conn() as conn:
        before = {r[0]: conn.execute('SELECT COUNT(*) FROM "' + r[0] + '"').fetchone()[0]
                  for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'journal_%' AND name != 'sqlite_sequence'")}
        for statement in JOURNAL_MIGRATION[2]:
            conn.execute(statement)
    a = create(JournalStore(db))
    JournalStore(db).set_archived(a["id"], expected_version=1, archived=True)
    with db._conn() as conn:
        assert {name: conn.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
                for name in before} == before


def test_migrazione_versionata_nuova_e_idempotente_su_memoria_reale(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    # Il root puo' aver gia' collegato la migrazione nella lista canonica.
    migrations = [item for item in memory_db.MIGRATIONS if item[0] != JOURNAL_MIGRATION[0]]
    monkeypatch.setattr(memory_db, "MIGRATIONS", sorted(migrations + [JOURNAL_MIGRATION], key=lambda item: item[0]))
    path = str(tmp_path / "fresh" / "test.db")
    first = memory_db.MemoryDB(path, str(tmp_path / "chroma"))
    entry = create(JournalStore(first))
    second = memory_db.MemoryDB(path, str(tmp_path / "chroma"))
    assert JournalStore(second).get(entry["id"]) == entry
    with second._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=?", (JOURNAL_MIGRATION[0],)).fetchone()[0] == 1


def test_fallimento_storico_annulla_anche_creazione(db):
    with db._conn() as conn:
        conn.execute("CREATE TRIGGER journal_create_fault BEFORE INSERT ON journal_revisions "
                     "BEGIN SELECT RAISE(ABORT, 'fault'); END")
    with pytest.raises(JournalUnavailable):
        create(JournalStore(db))
    assert JournalStore(db).list_entries()["total"] == 0


def test_archivio_non_duplica_e_non_permette_edit_involontario(db):
    store = JournalStore(db)
    a = create(store)
    b = store.set_archived(a["id"], expected_version=1, archived=True)
    assert store.set_archived(a["id"], expected_version=2, archived=True) == b
    with pytest.raises(JournalInvalid):
        store.update(a["id"], expected_version=2, kind="macro", ticker=None, title="X", body="Y")
    assert store.get(a["id"]) == b and store.history(a["id"])["total"] == 2


def test_cronologia_paginata_senza_perdita_e_immutabilita_dei_testi(db):
    store = JournalStore(db)
    a = create(store)
    for version in range(1, 6):
        store.update(a["id"], expected_version=version, kind="macro", ticker=None,
                     title="Titolo", body="Testo " + str(version))
    pages = [store.history(a["id"], limit=2, offset=n) for n in (0, 2, 4)]
    assert [r["version"] for page in pages for r in page["items"]] == [6, 5, 4, 3, 2, 1]
    assert all(page["total"] == 6 for page in pages)
    assert pages[-1]["items"][-1]["body"] == a["body"]


@pytest.fixture
def client(db):
    calls = []
    def get_db():
        calls.append(True)
        return db
    def session(request: Request):
        if request.headers.get("X-BB-Token") != "synthetic-session":
            raise HTTPException(401, "session required")
    app = FastAPI()
    app.include_router(create_journal_router(get_db, session))
    with TestClient(app) as result:
        yield result, calls


TOKEN = {"X-BB-Token": "synthetic-session"}
PAYLOAD = {"kind": "macro", "ticker": None, "title": "Scenario", "body": "Testo utente"}


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/journal", None), ("GET", "/journal/1", None), ("GET", "/journal/1/versions", None),
    ("POST", "/journal", PAYLOAD), ("PUT", "/journal/1", PAYLOAD | {"expected_version": 1}),
    ("POST", "/journal/1/archive", {"expected_version": 1, "archived": True}),
])
def test_ogni_route_richiede_sessione_prima_del_db(client, method, path, body):
    http, calls = client
    response = http.request(method, path, json=body)
    assert response.status_code == 401 and not calls


def test_api_create_update_history_conflitto(client):
    http, _ = client
    a = http.post("/journal", headers=TOKEN, json=PAYLOAD)
    assert a.status_code == 201
    entry = a.json()
    path = "/journal/" + str(entry["id"])
    assert http.get(path, headers=TOKEN).json() == entry
    update = http.put(path, headers=TOKEN, json=PAYLOAD | {"body": "Revisione", "expected_version": 1})
    assert update.status_code == 200 and update.json()["version"] == 2
    conflict = http.put(path, headers=TOKEN, json=PAYLOAD | {"expected_version": 1})
    assert conflict.status_code == 409 and conflict.json()["detail"]["current_version"] == 2
    assert http.get(path + "/versions", headers=TOKEN).json()["total"] == 2
    assert http.get("/journal/999", headers=TOKEN).status_code == 404


def test_api_non_accetta_origine_inventata_o_versione_coercita(client):
    http, _ = client
    assert http.post("/journal", headers=TOKEN, json=PAYLOAD | {"origin": "user"}).status_code == 422
    for invalid in [True, 0, "1", 1.5]:
        assert http.put("/journal/1", headers=TOKEN, json=PAYLOAD | {"expected_version": invalid}).status_code == 422


def test_api_db_guasto_errore_esplicito_senza_path():
    app = FastAPI()
    def boom():
        raise sqlite3.OperationalError("secret-local-path")
    app.include_router(create_journal_router(boom, lambda: True))
    with TestClient(app) as http:
        result = http.get("/journal")
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "journal_unavailable"
    assert "secret-local-path" not in result.text
