"""Offline presentation boundaries: source errors, numeric identity and cached text."""
import json
from threading import Event

import pytest

from bellomberg.core.language import current_language, language_context
from bellomberg.storage import preferences


@pytest.fixture(autouse=True)
def isolated_preference(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")
    preferences.set_language_preference("it")


def test_journal_validation_and_conflict_follow_context_without_rewriting_notes(tmp_path, monkeypatch):
    from bellomberg.storage import journal, memory_db
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    db = memory_db.MemoryDB(db_path=str(tmp_path / "journal.db"), chroma_path=str(tmp_path / "chroma"))
    store = journal.JournalStore(db)
    with language_context("en"):
        with pytest.raises(journal.JournalInvalid, match="Title: enter"):
            store.create(kind="macro", ticker=None, title="", body="Originale italiano")
        row = store.create(kind="macro", ticker=None, title="Titolo originale", body="Originale italiano")
        with pytest.raises(journal.JournalConflict, match="note has changed"):
            store.update(row["id"], expected_version=2, kind="macro", ticker=None, title="Altered", body="Altered")
    assert store.get(row["id"])["body"] == "Originale italiano"
    assert store.history(row["id"])["total"] == 1


def test_options_simulation_same_numbers_with_localized_limits():
    from bellomberg.portfolio.options_strategy import simulate_strategy
    body = {"spot": 100, "rate": .02, "dividend_yield": 0, "elapsed_days": 0,
            "iv_shift": 0, "commission": 1, "currency": "EUR", "legs": [
                {"type": "call", "side": "buy", "quantity": 1, "strike": 100,
                 "days": 30, "iv": .2, "premium": 3, "multiplier": 100}]}
    with language_context("it"):
        italian = simulate_strategy(body)
    with language_context("en"):
        english = simulate_strategy(body)
        with pytest.raises(ValueError, match="simulation: object required"):
            simulate_strategy(None)
    for key in ("entry_cost", "today", "scenario", "curve", "heatmap", "breakevens", "max_loss"):
        assert english[key] == italian[key]
    assert english["limits"][0].startswith("European options")
    assert italian["limits"][0].startswith("Opzioni europee")


def test_chain_cache_relabels_authored_warnings_without_another_provider_request(monkeypatch):
    from bellomberg.market_data import polygon_data
    from bellomberg.portfolio import vol_surface as vol
    vol._CHAIN_CACHE.clear()
    calls = []
    monkeypatch.setattr(polygon_data, "polygon_available", lambda: True)
    def page(*args, **kwargs):
        calls.append((args, kwargs))
        return {"results": [{"details": {"ticker": "O:TEST", "contract_type": "call",
                           "expiration_date": "2030-01-01", "strike_price": 100}}]}
    monkeypatch.setattr(polygon_data, "_get", page)
    with language_context("it"):
        italian = vol.get_chain_detail("TEST", "2030-01-01")
    with language_context("en"):
        english = vol.get_chain_detail("TEST", "2030-01-01")
    assert len(calls) == 1
    assert english["cached"] is True
    assert italian["chain"][0]["quality"][0].startswith("bid/ask mancanti")
    assert english["chain"][0]["quality"][0].startswith("Missing bid/ask")
    assert json.loads(json.dumps(english))["chain"][0]["strike"] == 100


def test_options_worker_captures_language_before_thread_and_preference_change(monkeypatch):
    from bellomberg.portfolio import options_download as download
    manager = download.OptionsDownloadManager()
    started, released, finished = Event(), Event(), Event()
    seen = []
    def observe(job):
        started.set()
        assert released.wait(3)
        seen.append(current_language())
        raise RuntimeError("synthetic provider failure")
    monkeypatch.setattr(manager, "_observe_spot", observe)
    original_fail = manager._fail
    def fail(job, error):
        original_fail(job, error)
        finished.set()
    monkeypatch.setattr(manager, "_fail", fail)
    with language_context("en"):
        job = manager.start("TEST", ["2030-01-01"])
    assert started.wait(3)
    preferences.set_language_preference("it")
    released.set()
    assert finished.wait(3)
    status = manager.status(job["id"])
    assert seen == ["en"]
    assert status["output_language"] == "en"
    assert status["error"].startswith("Download interrupted")


def test_progress_labels_follow_request_but_historical_text_is_verbatim():
    from bellomberg.agents.score_history import progress_payload
    historic = {"captured_at": "2001-01-01", "reflection": {"text": "Lezione originale"},
                "score_error": "Errore originale", "scorecard": {}}
    with language_context("en"):
        result = progress_payload([historic])
    assert result["history"]["note"].startswith("History recorded")
    assert result["agents"][0]["role"] == "Synthesis and collective decisions"
    assert result["runs"][0]["reflection"]["text"] == "Lezione originale"
    assert result["runs"][0]["score_error"] == "Errore originale"
