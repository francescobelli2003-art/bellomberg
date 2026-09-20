import sqlite3

from bellomberg.agents.filing_context import committee_filing_context, get_filing_changes
from bellomberg.agents import chat_tools
from bellomberg.agents.specialists.base import Specialist
from bellomberg.core.language import language_context
from bellomberg.storage.filing_store import FilingStore, ensure_schema


PROFILE = {"ticker": "TEST", "emittente_id": "CIK:123", "lingua": "en",
           "tipo": "annuale", "perimetro": "consolidato",
           "verifica": {"lingua": "English", "tipo": "annual", "perimetro": "consolidated"},
           "sezioni": {"risk": {"inizio": "Risk Factors", "fine": "End"}}}


def _archive(tmp_path):
    path = tmp_path / "filing.sqlite"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
    store = FilingStore(path)
    store.set_profile("TEST", PROFILE)
    citation = {"url": "https://sec.example/report", "sha256": "abc",
                "sezione": "risk", "inizio": 1, "fine": 12,
                "pagine_fisiche": [2], "testo": "material change"}
    result = {"stato": "parziale", "ultimo_non_verificato": True,
              "freschezza": {"stato": "stale"}, "copertura": {"stato": "limitata"},
              "motivi": ["newer document unverified"], "confronto_corrente": None,
              "confronto_storico": {"stato": "ok", "cambiamenti": [
                  {"tipo": "aggiunto", "dopo": citation} for _ in range(8)]}}
    run = store.start_run("TEST")
    store.finish_run(run["id"], status="parziale", result=result,
                     judgment={"status": "errore", "reason": "model missing", "findings": []},
                     index={"status": "skipped", "reason": "no judgment"})
    return path


def test_archive_consumer_preserves_historical_and_declares_limits(tmp_path):
    path = _archive(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA query_only=ON")
        before = conn.execute("SELECT COUNT(*) FROM filing_runs").fetchone()[0]
    result = get_filing_changes("TEST", db_path=path)
    assert result["scope"] == "storico"
    assert result["latest_unverified"] is True
    assert result["freshness"]["stato"] == "stale"
    assert result["changes_shown"] == 5 and result["changes_total"] == 8
    assert result["judgment_status"] == "errore" and result["judgment_reason"] == "model missing"
    assert result["index_status"] == "skipped"
    assert result["changes"][0]["estratti"]["dopo"]["citation_id"] == "C1-dopo"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM filing_runs").fetchone()[0] == before


def test_tool_registry_and_committee_bounds(tmp_path, monkeypatch):
    path = _archive(tmp_path)
    monkeypatch.setattr("bellomberg.agents.filing_context.SQLITE_PATH", path)
    names = {t["name"] for t in chat_tools.get_tools_for_agent("fundamentals")}
    assert "get_filing_changes" in names
    payload = chat_tools.dispatch("get_filing_changes", {"ticker": "TEST"})
    assert payload["data"]["scope"] == "storico"
    block = committee_filing_context(["TEST", "MISSING"], db_path=path, max_tickers=1)
    assert "TEST" in block and "TICKER NON INCLUSI" in block
    assert "MISSING" not in block


def test_schema_missing_is_not_silent(tmp_path):
    path = tmp_path / "empty.sqlite"
    path.touch()
    result = get_filing_changes("TEST", db_path=path)
    assert result["status"] == "non_disponibile" and "schema filing assente" in result["reason"]


def test_corrupt_archive_and_english_committee_message(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    path.write_bytes(b"not sqlite")
    result = get_filing_changes("TEST", db_path=path)
    assert result["status"] == "non_disponibile" and result["reason"]
    with language_context("en"):
        block = committee_filing_context(["TEST"], db_path=path)
    assert "FILING ARCHIVE" in block and "TEST" in block


def test_filing_priming_reaches_each_specialist_round_without_fake_report():
    class FakeBoard:
        memory_db = None
        data = {"_filing_context": "ARCHIVIO FILING TEST, storico e stale"}

        def summary_for_specialist(self, _):
            return {}

    board = FakeBoard()
    specialist = Specialist(board, client=object())
    for round_n in (0, 1, 2):
        assert "ARCHIVIO FILING TEST, storico e stale" in specialist._build_round_context(round_n)
    assert "filing_context" not in board.data
