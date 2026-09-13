"""Actual call sites with replay clients: selected output language, stable records."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bellomberg.core import language
from bellomberg.agents import consigliere_multi as cm, agent_tools, reflection, scorekeeper
from test_pipeline_replay_artifacts import replay, replay_loop, run_offline, db
from test_tetto_specialisti_16k import _Resp, _TextBlock, _Usage
from test_core_audit_regressions import _chat, _chunk


@pytest.mark.parametrize("selected", ["it", "en"])
def test_complete_replay_captures_one_language_for_all_new_agent_calls(replay, monkeypatch, selected):
    from bellomberg.storage import preferences
    current = [selected]
    monkeypatch.setattr(preferences, "get_language_preference", lambda: current[0])
    original_news = agent_tools.tool_search_news
    def change_preference(*args, **kwargs):
        current[0] = "en" if selected == "it" else "it"
        return original_news(*args, **kwargs)
    monkeypatch.setattr(agent_tools, "tool_search_news", change_preference)
    cm.run_multi_agent(language=selected)
    assert replay.blackboard.language == selected
    needle = "NEW OUTPUT LANGUAGE: professional English" if selected == "en" else "LINGUA DEL NUOVO OUTPUT: italiano professionale"
    calls = [call for client in replay.clients.values() for call in client.calls]
    calls += replay.capo_calls + replay.side_calls
    assert len(calls) > 18
    for call in calls:
        system = str(call["system"])
        assert needle in system
        if selected == "en":
            assert "Scrivi in ITALIANO professionale" not in system
            assert "tutte in italiano scorrevole" not in system
            assert "in italiano professionale e diretto" not in system
    assert language.current_language() == current[0]
    assert all(row["language"] == selected for row in replay.blackboard.usage_log)


def test_round_workers_receive_frozen_language_in_threads(monkeypatch):
    seen = []
    class Desk:
        name = "macro"
        def __init__(self, blackboard):
            seen.append(language.current_language())
        def run(self, round_n):
            seen.append(language.current_language())
    monkeypatch.setattr(cm, "SPECIALIST_ORDER", [Desk])
    monkeypatch.setenv("CONSIGLIERE_PARALLEL", "2")
    with language.language_context("it"):
        cm.run_round(SimpleNamespace(data={}, language="en"), 0)
        assert language.current_language() == "it"
    assert seen == ["en", "en"]


def test_reflection_labels_new_lesson_but_preserves_previous_text(monkeypatch):
    from bellomberg.core import llm_client
    previous = {"date": "2026-01-01", "memo_id": 1, "lesson": "Testo storico immutabile"}
    calls, saved = [], []
    monkeypatch.setattr(scorekeeper, "compute_scorecard", lambda: {"overall": {"n": 30}})
    monkeypatch.setattr(scorekeeper, "format_track_record_for_capo", lambda *a, **kw: "synthetic measured track")
    monkeypatch.setattr(reflection, "_load_lessons", lambda: [deepcopy(previous)])
    monkeypatch.setattr(reflection, "_save_lessons", lambda rows: saved.extend(rows))
    def create(**kwargs):
        calls.append(kwargs)
        return _Resp("end_turn", [_TextBlock("1. Synthetic new lesson: verify the next source.")], _Usage())
    monkeypatch.setattr(llm_client, "OpenRouterClient", lambda **kw: SimpleNamespace(messages=SimpleNamespace(create=create)))
    with language.language_context("en"):
        reflection.generate_lesson("## ACTION TABLE\nsynthetic", memo_id=2)
    assert "NEW OUTPUT LANGUAGE: professional English" in calls[0]["system"]
    assert "righe numerate, in italiano" not in calls[0]["system"]
    assert saved[0] == previous
    assert saved[1]["language"] == "en"


def test_chat_prompt_uses_selected_language_without_rewriting_current_facts(monkeypatch):
    from bellomberg.agents import chat_engine
    from bellomberg.core import current_facts
    original = "Testo storico: Rispondi in italiano. Quotazione originale invariata."
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: original)
    with language.language_context("en"):
        prompt = chat_engine._build_system("fundamentals", mandato=None, errore_mandato="synthetic")
    assert original in prompt
    assert "NEW OUTPUT LANGUAGE: professional English" in prompt
    assert "Italiano professionale, chiaro, tecnico" not in prompt


@pytest.mark.parametrize("selected", ["it", "en"])
def test_real_chat_stream_and_tool_worker_keep_language_after_preference_change(replay, monkeypatch, selected):
    from bellomberg.agents import chat_engine
    from bellomberg.storage import preferences
    actual_build = chat_engine._build_system
    current, seen = [selected], []
    monkeypatch.setattr(preferences, "get_language_preference", lambda: current[0])
    first = [_chunk({"tool_calls": [{"index": 0, "id": "a", "function": {
        "name": "tool_a", "arguments": '{}'}}]}, finish="tool_calls")]
    run, _, _, requests = _chat(monkeypatch, [first, [_chunk({"content": "Synthetic response"}, finish="stop")]])
    monkeypatch.setattr(chat_engine, "_build_system", actual_build)
    def dispatch(*args, **kwargs):
        current[0] = "en" if selected == "it" else "it"
        seen.append(language.current_language())
        return {"synthetic": True}
    monkeypatch.setattr(chat_engine, "dispatch", dispatch)
    events = replay.loop.run_until_complete(run())
    needle = language.output_language_instruction(selected)
    assert len(requests) == 2
    assert all(needle in request["messages"][0]["content"] for request in requests)
    assert seen == [selected]
    assert language.current_language() == current[0]
    assert any(event.startswith("event: done") for event in events)
