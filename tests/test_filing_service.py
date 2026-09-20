import sqlite3

from bellomberg.market_data.filing_service import FilingService, default_indexer
from bellomberg.storage.filing_store import FilingStore, ensure_schema


PROFILE = {"ticker": "ABC", "emittente_id": "CIK:123", "lingua": "en", "tipo": "annuale",
           "perimetro": "consolidato", "verifica": {"lingua": "English", "tipo": "annual", "perimetro": "consolidated"},
           "sezioni": {"risk": {"inizio": "Risk", "fine": "End"}}, "fonti": []}


def setup(tmp_path, pipeline, judge=None, indexer=None):
    path = tmp_path / "filing.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    store = FilingStore(path)
    store.set_profile("ABC", PROFILE, qualitative_enabled=True)
    return store, FilingService(store, tmp_path / "archive", pipeline=pipeline, judge=judge,
                                indexer=indexer or (lambda *_: {"status": "ok", "reason": None}))


def result():
    return {"stato": "ok", "motivi": [], "coppia": {"prima": {"sha256": "a" * 64}, "dopo": {"sha256": "b" * 64}},
            "confronto_corrente": {"stato": "ok", "cambiamenti": [{"tipo": "modificato",
                "dopo": {"url": "https://example.org/b", "sha256": "b" * 64,
                         "inizio": 1, "fine": 15, "testo": "Risk increased", "sezione": "risk"}}]}}


def test_queued_run_freezes_profile_and_reuses_judgment(tmp_path):
    calls = []
    def judge(_):
        calls.append(1)
        return {"status": "ok", "findings": [{"category": "risk", "assessment": "Rischio aumentato",
                                                "citations": ["C1-dopo"]}], "model": "stub", "usage": {"input_tokens": 1}}
    store, svc = setup(tmp_path, lambda *a, **k: result(), judge)
    queued = svc.queue("ABC")
    assert queued["status"] == "queued"
    store.set_profile("ABC", {**PROFILE, "perimetro": "solo"}, qualitative_enabled=True)
    first = svc.execute(queued["id"])
    assert first["profile_version"] == 1 and first["status"] == "ok"
    store.set_profile("ABC", PROFILE, qualitative_enabled=True)
    second = svc.run("ABC")
    assert second["judgment"]["status"] == "ok"
    assert len(calls) == 2  # nuova revisione non riusa il giudizio
    third = svc.run("ABC")
    assert third["judgment"]["reused_from_run"] == second["id"]
    assert len(calls) == 2
    english = svc.execute(svc.queue("ABC", language="en")["id"])
    assert english["judgment_language"] == "en" and "reused_from_run" not in english["judgment"]
    assert len(calls) == 3
    assert svc.status("ABC")["runs"][0]["id"] == english["id"]


def test_pipeline_failure_is_persisted_over_previous_success(tmp_path):
    answers = iter([result(), RuntimeError("provider down")])
    def pipeline(*_, **__):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer
    store, svc = setup(tmp_path, pipeline, judge=lambda _: {"status": "skipped", "findings": [], "reason": "stub"})
    assert svc.run("ABC")["status"] == "ok"
    failed = svc.run("ABC")
    assert failed["status"] == "errore" and "provider down" in failed["reason"]
    assert svc.status("ABC")["status"] == "errore"


def test_unserializable_pipeline_result_finishes_as_error(tmp_path):
    store, svc = setup(tmp_path, lambda *a, **k: {"stato": "ok", "motivi": [], "bad": float("nan")})
    row = svc.run("ABC")
    assert row["status"] == "errore" and "Out of range float" in row["reason"]
    assert row["result"] is None and svc.status("ABC")["active_run"] is None


def test_index_failure_remains_explicit_without_losing_diff(tmp_path):
    def broken(*_):
        raise RuntimeError("Chroma unavailable")
    store, svc = setup(tmp_path, lambda *a, **k: result(),
                       judge=lambda _: {"status": "ok", "findings": [{"category": "risk", "assessment": "x", "citations": ["C1-dopo"]}]},
                       indexer=broken)
    row = svc.run("ABC")
    assert row["status"] == "parziale" and row["result"]["stato"] == "ok"
    assert row["index"]["status"] == "errore"


def test_judge_cannot_forge_citation(tmp_path):
    store, svc = setup(tmp_path, lambda *a, **k: result(),
                       judge=lambda _: {"status": "ok", "findings": [{"category": "risk", "assessment": "x", "citations": ["C999-dopo"]}]})
    row = svc.run("ABC")
    assert row["status"] == "errore" and "citation_id" in row["reason"]


def test_diff_is_indexed_even_when_qualitative_is_off(tmp_path):
    indexed = []
    store, svc = setup(tmp_path, lambda *a, **k: result(),
                       indexer=lambda run, output, judgment: (indexed.append((output, judgment)) or
                                                              {"status": "ok", "reason": None}))
    store.set_profile("ABC", PROFILE, qualitative_enabled=False)
    row = svc.run("ABC")
    assert row["status"] == "ok" and row["judgment"]["status"] == "skipped"
    assert len(indexed) == 1 and indexed[0][0]["confronto_corrente"]["cambiamenti"]


def test_default_indexer_writes_diff_chunks_and_separate_judgment(monkeypatch, tmp_path):
    import chromadb
    stored = {}
    class Collection:
        def upsert(self, **kwargs):
            stored.update(kwargs)
    class Client:
        def get_or_create_collection(self, name):
            assert name == "filing_diffs"
            return Collection()
    def connect(*, path, settings):
        assert path == str(tmp_path)
        assert settings.anonymized_telemetry is False
        assert settings.allow_reset is False
        return Client()
    monkeypatch.setattr(chromadb, "PersistentClient", connect)
    output = default_indexer({"id": 9, "ticker": "ABC", "evidence_key": "e"}, result(),
                             {"findings": [{"category": "risk", "assessment": "Nuovo rischio",
                                            "citations": ["C1-dopo"]}]}, chroma_path=tmp_path)
    assert output["diff_chunks"] == 1 and output["judgment_chunks"] == 1
    assert {m["kind"] for m in stored["metadatas"]} == {"diff", "judgment"}
    assert stored["metadatas"][0]["citation_id"] == "C1-dopo"


def test_default_indexer_uses_temporary_db_parent_with_qualitative_off(monkeypatch, tmp_path):
    import chromadb
    from bellomberg.core import paths
    paths_seen = []
    class Collection:
        def upsert(self, **kwargs):
            pass
    class Client:
        def get_or_create_collection(self, name):
            return Collection()
    def connect(*, path, settings):
        paths_seen.append(path)
        return Client()
    monkeypatch.setattr(chromadb, "PersistentClient", connect)
    monkeypatch.setattr(paths, "CHROMA_PATH", tmp_path / "real_chroma_should_not_be_used")
    db = tmp_path / "isolated" / "filing.sqlite"
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store = FilingStore(db)
    store.set_profile("ABC", PROFILE, qualitative_enabled=False)
    svc = FilingService(store, tmp_path / "archive", pipeline=lambda *a, **k: result())
    row = svc.run("ABC")
    assert row["index"]["status"] == "ok"
    assert paths_seen == [str(db.parent / "chroma")]


def test_identical_pair_with_historical_context_does_not_reuse_current_judgment(tmp_path):
    calls = []
    current = result()
    current["coppia"]["ambito"] = "ultimo_verificato"
    current["freschezza"] = {"stato": "non_garantita", "checked_at": "2026-09-19"}
    historical = result()
    historical["coppia"]["ambito"] = "storico"
    historical["confronto_storico"] = historical.pop("confronto_corrente")
    historical["ultimo_non_verificato"] = True
    historical["freschezza"] = {"stato": "stale", "checked_at": "2026-09-20"}
    outputs = iter([current, historical, historical])
    def judge(_):
        calls.append(1)
        return {"status": "ok", "findings": [{"category": "risk", "assessment": "Rischio",
                                                "citations": ["C1-dopo"]}], "model": "stub", "usage": {}}
    _, svc = setup(tmp_path, lambda *a, **k: next(outputs), judge=judge)
    first, second, third = (svc.run("ABC") for _ in range(3))
    assert first["evidence_key"] != second["evidence_key"]
    assert "reused_from_run" not in second["judgment"]
    assert third["judgment"]["reused_from_run"] == second["id"]
    assert len(calls) == 2


def test_two_runs_share_immutable_snapshot_from_real_downloader(tmp_path, monkeypatch):
    import socket
    from pathlib import Path
    from bellomberg.market_data import lettore_trimestrali
    class Response:
        status_code = 200
        content = b"<html><body>Annual risk changed.</body></html>"
        url = "https://example.org/report.html"
        headers = {"Content-Type": "text/html"}
        def raise_for_status(self):
            pass
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))])
    monkeypatch.setattr(lettore_trimestrali.requests, "get", lambda *a, **k: Response())
    paths = []
    def pipeline(_, *, archivio):
        snapshot = lettore_trimestrali.scarica_documento(
            "https://example.org/report.html", str(archivio),
            host_consentiti={"example.org"}, public_only=True)
        assert snapshot["stato"] == "ok"
        paths.append(Path(snapshot["path"]))
        citation = {"url": snapshot["url"], "sha256": snapshot["sha256"],
                    "inizio": 0, "fine": 12, "testo": "Annual risk", "sezione": "risk"}
        return {"stato": "ok", "motivi": [], "coppia": {"prima": {"sha256": snapshot["sha256"]},
              "dopo": {"sha256": snapshot["sha256"]}}, "confronto_corrente": {
              "stato": "ok", "cambiamenti": [{"tipo": "modificato", "dopo": citation}]}}
    store, svc = setup(tmp_path, pipeline, indexer=lambda *_: {"status": "skipped", "reason": "test"})
    store.set_profile("ABC", PROFILE, qualitative_enabled=False)
    first = svc.run("ABC")
    before = paths[0].stat().st_mtime_ns
    second = svc.run("ABC")
    assert paths[0] == paths[1]
    assert paths[0].stat().st_mtime_ns == before
    assert len(list((tmp_path / "archive" / "ABC" / "documents").iterdir())) == 1
    assert first["result"]["confronto_corrente"]["cambiamenti"][0]["dopo"]["sha256"] == second["result"]["confronto_corrente"]["cambiamenti"][0]["dopo"]["sha256"]
