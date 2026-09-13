"""New production language is explicit; legacy content/metadata is never rewritten."""
import pytest

from bellomberg.core.language import language_context
from bellomberg.storage import memory_db, preferences


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: self.__dict__.update(
        chroma_client=None, col_memos=None, col_decisions=None, col_feedback=None))
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")
    return memory_db.MemoryDB(db_path=str(tmp_path / "language.db"), chroma_path=str(tmp_path / "chroma"))


@pytest.mark.parametrize("language", ["it", "en"])
def test_memo_reports_usage_capture_production_language_and_return_it(db, language):
    with language_context(language):
        memo = db.save_memo("Original output", title="Unchanged title")
        db.save_specialist_report(memo, "macro", 2, "Report verbatim")
        db.save_llm_usage(memo, [{"agent": "macro", "round": 2, "model": "synthetic"}])
    assert db.get_recent_memos(1)[0]["output_language"] == language
    assert db.get_recent_memos(1)[0]["full_markdown"] == "Original output"
    assert db.get_recent_specialist_reports("macro", 1)[0]["output_language"] == language
    with db._conn() as conn:
        assert conn.execute("SELECT output_language FROM llm_usage").fetchone()[0] == language


def test_explicit_new_output_language_does_not_change_request_or_preference(db):
    preferences.set_language_preference("it")
    with language_context("it"):
        memo = db.save_memo("English output", output_language="en")
    assert db.get_recent_memos(1)[0]["output_language"] == "en"
    assert preferences.get_language_preference() == "it"
    with pytest.raises(ValueError):
        db.save_specialist_report(memo, "macro", 2, "bad", output_language="fr")
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM specialist_reports").fetchone()[0] == 0


def test_default_memo_title_uses_documented_output_language(db):
    with language_context("en"):
        db.save_memo("Nuovo memo", output_language="it")
    memo = db.get_recent_memos(1)[0]
    assert memo["title"].startswith("Nota di ricerca settimanale - ")
    assert memo["output_language"] == "it"
    with language_context("it"):
        db.save_memo("New memo", output_language="en")
    assert db.get_recent_memos(1)[0]["title"].startswith("Weekly Research Note - ")


def test_migration_preserves_legacy_bytes_and_null_language(tmp_path, monkeypatch):
    migrations = memory_db.MIGRATIONS
    monkeypatch.setattr(memory_db, "MIGRATIONS", [row for row in migrations if row[0] < 12])
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    db = memory_db.MemoryDB(db_path=str(tmp_path / "pre12.db"), chroma_path=str(tmp_path / "chroma"))
    with db._conn() as conn:
        assert "output_language" not in [row[1] for row in conn.execute("PRAGMA table_info(memos)")]
        conn.execute("INSERT INTO memos(timestamp,title,full_markdown) VALUES ('2001-01-01','Legacy title','Testo originale / original')")
        memo = conn.execute("SELECT id FROM memos").fetchone()[0]
        conn.execute("INSERT INTO specialist_reports(memo_id,specialist,round_n,content) VALUES (?,'macro',2,'Legacy report')", (memo,))
        conn.execute("INSERT INTO chat_sessions(specialist,title) VALUES ('macro','Legacy session')")
        session = conn.execute("SELECT id FROM chat_sessions").fetchone()[0]
        conn.execute("INSERT INTO chat_messages(session_id,role,content) VALUES (?,'assistant','Legacy reply')", (session,))
        before = conn.execute("SELECT full_markdown FROM memos WHERE id=?", (memo,)).fetchone()[0]
    monkeypatch.setattr(memory_db, "MIGRATIONS", migrations)
    db._init_sqlite()
    db._init_sqlite()  # versioned migration remains idempotent
    with db._conn() as conn:
        assert conn.execute("SELECT full_markdown,output_language FROM memos WHERE id=?", (memo,)).fetchone()[:] == (before, None)
        assert conn.execute("SELECT output_language FROM specialist_reports").fetchone()[0] is None
        assert conn.execute("SELECT output_language FROM chat_messages").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=12").fetchone()[0] == 1


def test_preference_change_never_regenerates_or_rewrites_historical_outputs(db, monkeypatch):
    from bellomberg.cli import regenerate_memo
    calls = []
    monkeypatch.setattr(regenerate_memo, "main", lambda *_a, **_k: calls.append("regenerate"))
    with language_context("it"):
        db.save_memo("Memo storico originale")
    with db._conn() as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM memos")]
    preferences.set_language_preference("en")
    with db._conn() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM memos")] == before
    assert calls == []


def test_opening_validation_and_performance_note_are_localized_readonly(db):
    with language_context("en"):
        with pytest.raises(ValueError, match="Invalid quote currency"):
            db.prepare_position_opening(ticker="TEST", quantita=2, prezzo_medio=10,
                valuta="???", as_of="2001-01-01", provenienza="Estratto originale")
        result = db.prepare_position_opening(ticker="TEST", quantita=2, prezzo_medio=10,
            valuta="EUR", as_of="2001-01-01", provenienza="Estratto originale")
    assert result["performance_note"].startswith("Balance known at the stated date")
    assert result["opening"]["provenienza"] == "Estratto originale"
    with db._conn() as conn:
        for table in ("positions", "position_openings", "trade_history", "cash_movements"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_chat_assistant_attests_output_language_user_text_is_not_inferred(db, monkeypatch):
    from bellomberg.agents import chat_engine as chat
    monkeypatch.setattr(chat, "MemoryDB", lambda: db)
    with language_context("en"):
        session = chat.create_session("macro", title="Original title")
        chat._save_message(session, "user", "Domanda originale italiana")
        chat._save_message(session, "assistant", "English response")
    rows = chat.get_messages(session)
    assert rows[0]["output_language"] is None
    assert rows[0]["content"] == "Domanda originale italiana"
    assert rows[1]["output_language"] == "en"
    assert chat.get_session_info(session)["output_language"] == "en"
    assert chat.list_sessions("macro")[0]["output_language"] == "en"


def test_chat_stream_meta_attests_captured_language_before_any_provider(db, monkeypatch):
    import asyncio
    import json
    from bellomberg.agents import chat_engine as chat
    calls = []
    monkeypatch.setattr(chat, "MemoryDB", lambda: db)
    monkeypatch.setattr(chat, "OPENROUTER_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(chat, "_modello_llm", lambda *_: "synthetic-model")
    monkeypatch.setattr(chat, "_chat_max_tokens", lambda: 1)
    monkeypatch.setattr(chat, "_leggi_mandato_chat", lambda: (None, "Synthetic missing mandate"))
    monkeypatch.setattr(chat, "_build_system", lambda *_a, **_k: "Synthetic prompt")
    monkeypatch.setattr(chat, "get_tools_for_agent", lambda *_: [])
    monkeypatch.setattr(chat, "AsyncOpenRouterClient", lambda: calls.append("provider"))
    with language_context("en"):
        session = chat.create_session("macro")
    async def run():
        stream = chat.stream_chat(session, "macro", "Testo utente originale", language="en")
        try:
            async for event in stream:
                if event.startswith("event: meta"):
                    preferences.set_language_preference("it")
                    return json.loads(event.split("data: ", 1)[1])
        finally:
            await stream.aclose()
    meta = asyncio.run(run())
    assert meta["output_language"] == "en"
    assert calls == []
    assert chat.get_messages(session)[0]["output_language"] is None
    assert chat.get_session_info(session)["title"] == "Chat with Macro"
