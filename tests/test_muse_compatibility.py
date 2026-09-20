"""Regressioni del contratto Muse: finale senza tool e Max solo nei desk scelti."""
from copy import deepcopy

import pytest

from bellomberg.core import llm_client as llm


MUSE = "meta/muse-spark-1.3"
TOOLS = [{"name": "echo", "description": "Synthetic probe", "input_schema": {
    "type": "object", "properties": {"text": {"type": "string"}}}}]
HISTORY = [
    {"role": "user", "content": "Check the synthetic result."},
    {"role": "assistant", "content": [
        {"type": "tool_use", "id": "probe-id", "name": "echo", "input": {"text": "OK"}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "probe-id", "content": "OK"},
        {"type": "text", "text": "Tools disabled: write the final answer."}]},
]


@pytest.mark.parametrize("choice", ["none", {"type": "none"}])
def test_muse_final_disables_tools_without_invalid_choice_and_keeps_result(choice):
    messages = deepcopy(HISTORY)
    tools = deepcopy(TOOLS)
    body = llm.costruisci_corpo(MUSE, 1024, messages, tools=tools, tool_choice=choice)
    assert "tools" not in body and "tool_choice" not in body
    assert body["messages"][-2] == {
        "role": "tool", "tool_call_id": "probe-id", "content": "OK"}
    assert body["messages"][-1] == {
        "role": "user", "content": "Tools disabled: write the final answer."}
    assert body["messages"][-3]["tool_calls"][0]["id"] == "probe-id"
    assert messages == HISTORY and tools == TOOLS


@pytest.mark.parametrize("choice", [None, {"type": "auto"}])
def test_muse_normal_turn_keeps_tools_available(choice):
    body = llm.costruisci_corpo(MUSE, 1024, HISTORY[:1], tools=TOOLS, tool_choice=choice)
    assert body["tools"][0]["function"]["name"] == "echo"
    assert body.get("tool_choice", "auto") == "auto"


@pytest.mark.parametrize("model", ["google/gemini-3.8-flash", "anthropic/claude-opus-5"])
def test_other_providers_keep_their_final_choice_contract(model):
    body = llm.costruisci_corpo(model, 1024, HISTORY, tools=TOOLS, tool_choice={"type": "none"})
    assert body["tool_choice"] == "none" and body["tools"]


@pytest.mark.parametrize("model,expected", [
    (MUSE, {"effort": "max"}),
    ("google/gemini-3.8-flash", {"effort": "medium"}),
    ("anthropic/claude-opus-5", {"effort": "medium"}),
])
def test_specialist_effort_reaches_the_wire_without_changing_other_models(model, expected):
    body = llm.costruisci_corpo(model, 1024, HISTORY[:1], thinking=llm.thinking_consigliere(model))
    assert body["reasoning"] == expected
    # Another role using the same Muse model retains its existing adaptive policy.
    assert llm.costruisci_corpo(MUSE, 1024, HISTORY[:1], thinking={"type": "adaptive"})[
        "reasoning"] == {"effort": "medium"}


def test_invalid_explicit_effort_is_rejected_instead_of_silently_using_medium():
    with pytest.raises(ValueError, match="effort"):
        llm.costruisci_corpo(MUSE, 1024, HISTORY[:1],
                            thinking={"type": "effort", "effort": "typo"})
