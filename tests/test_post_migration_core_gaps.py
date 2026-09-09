"""Release regressions on real package functions; synthetic inputs only."""
from datetime import date
import json

import pytest

from bellomberg.agents import agent_tools, specialist_scores
from bellomberg.core import llm_client


@pytest.fixture
def score_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(specialist_scores.cl, "carica_veicoli", lambda: {
        "origine": "synthetic", "motivo": None, "veicoli": {},
    })
    monkeypatch.setattr(specialist_scores, "REPORT_DIR", tmp_path)
    return {"positions": [{"ticker": "SYNTH", "peso_pct": 100}]}, tmp_path


def _score(score_inputs, source, fair_value, price=100, **extra):
    portfolio, report_dir = score_inputs
    if source == "sidecar":
        payload = {"_timestamp": date.today().isoformat(), "price": price,
                   "fair_value_final": fair_value, **extra}
        (report_dir / "VAL_SYNTH.payload.json").write_text(
            json.dumps(payload), encoding="utf-8")
        return specialist_scores.fundamentals_score(portfolio)
    return specialist_scores.fundamentals_score(portfolio, valuations={
        "SYNTH": {"fair_value": fair_value, "price": price, **extra},
    })


@pytest.mark.parametrize("source", ["injected", "sidecar"])
@pytest.mark.parametrize("fair_value,price", [
    (float("nan"), 100), (float("inf"), 100), (True, 100), ("120", 100),
    (120, float("nan")), (120, float("inf")), (120, 0), (120, -1),
    (120, True), (120, "100"), (1e308, 1e-308),
])
def test_invalid_valuation_never_becomes_a_scored_name(score_inputs, source, fair_value, price):
    assert _score(score_inputs, source, 120)["metrics"]["n_valued"] == 1
    assert _score(score_inputs, source, fair_value, price) is None


@pytest.mark.parametrize("source", ["injected", "sidecar"])
def test_zero_fair_value_is_not_missing_or_replaced(score_inputs, source):
    result = _score(score_inputs, source, 0, fair_value_weighted=200)
    assert result["metrics"] == {
        "avg_mos_pct": -100.0, "n_valued": 1, "n_cheap": 0, "n_rich": 1,
    }


def test_absent_final_value_can_use_lower_priority_present_value(score_inputs):
    result = _score(score_inputs, "sidecar", None, fair_value_weighted=120)
    assert result["metrics"]["avg_mos_pct"] == 20.0


def test_invalid_final_value_cannot_silently_use_lower_priority_value(score_inputs):
    assert _score(score_inputs, "sidecar", float("nan"), fair_value_weighted=120) is None


@pytest.mark.parametrize("source", ["injected", "sidecar"])
def test_flagged_valuation_remains_excluded(score_inputs, source):
    assert _score(score_inputs, source, 120, valuation_flagged=True) is None


def test_sidecar_sanity_block_remains_excluded(score_inputs):
    assert _score(score_inputs, "sidecar", 120, sanity={"severity": "BLOCK"}) is None


def test_partial_score_declares_name_with_invalid_valuation(score_inputs):
    portfolio, _ = score_inputs
    portfolio["positions"].append({"ticker": "BAD", "peso_pct": 50})
    result = specialist_scores.fundamentals_score(portfolio, valuations={
        "SYNTH": {"fair_value": 120, "price": 100},
        "BAD": {"fair_value": float("nan"), "price": 100},
    })
    assert result["metrics"]["n_valued"] == 1
    assert result["metrics"]["avg_mos_pct"] == 20.0
    assert "1/2 nomi valutati" in result["verdict"]
    assert "FV/prezzo assenti o non validi: BAD" in result["verdict"]


@pytest.mark.parametrize("value", ["0", "-1", "-100"])
def test_chat_token_ceiling_rejects_nonpositive_configuration(monkeypatch, value):
    monkeypatch.setenv("CHAT_MAX_TOKENS", value)
    with pytest.raises(llm_client.ConfigurazioneLLMMancante) as error:
        llm_client.chat_max_tokens()
    assert error.value.variabile == "CHAT_MAX_TOKENS"


@pytest.mark.parametrize("value", ["1", "12000"])
def test_chat_token_ceiling_accepts_positive_configuration(monkeypatch, value):
    monkeypatch.setenv("CHAT_MAX_TOKENS", value)
    assert llm_client.chat_max_tokens() == int(value)


@pytest.mark.parametrize("name", [None, [], {}, 42, "", "unknown"])
def test_malformed_tool_name_returns_error_without_dispatch(monkeypatch, name):
    calls = []
    monkeypatch.setattr(agent_tools, "TOOL_DISPATCHER", {
        "synthetic": lambda: calls.append(True),
    })
    result = agent_tools.execute_tool(name, {})
    assert isinstance(result.get("error"), str) and result["error"]
    assert calls == []


def test_valid_tool_dispatch_and_argument_error_are_preserved(monkeypatch):
    monkeypatch.setattr(agent_tools, "TOOL_DISPATCHER", {"synthetic": lambda number: number * 2})
    assert agent_tools.execute_tool("synthetic", {"number": 3}) == 6
    assert "Argomenti non validi" in agent_tools.execute_tool("synthetic", None)["error"]
