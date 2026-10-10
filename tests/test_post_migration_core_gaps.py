"""Release regressions on real package functions; synthetic inputs only."""
from datetime import date
import json

import pytest

from bellomberg.agents import agent_tools, specialist_scores
from bellomberg.core import llm_client


_USE_MODEL_PRICE = object()


@pytest.fixture
def score_inputs(monkeypatch, tmp_path):
    import test_sector_usability as fixtures
    monkeypatch.setattr(fixtures, "DAY", date.today().isoformat())
    monkeypatch.setattr(specialist_scores.cl, "carica_veicoli", lambda: {
        "origine": "synthetic", "motivo": None, "veicoli": {},
    })
    monkeypatch.setattr(specialist_scores, "REPORT_DIR", tmp_path)
    # 09/10 (Opus 5.5): MOS sul prezzo corrente; qui l'oggetto sono i FV/prezzi invalidi
    from test_score_fondamentali_news_correzione import prezzo_corrente_uguale_al_modello
    prezzo_corrente_uguale_al_modello(monkeypatch)
    return {"positions": [{"ticker": "SYNTH", "peso_pct": 100}]}, tmp_path


def _nd(result):
    """v2 10/10 (Opus 5.5): nessun nome misurabile = n.d. DICHIARATO (dict), non None."""
    return (isinstance(result, dict) and result["score"] is None and result["max_score"] is None
            and result["verdict"].startswith("n.d. - ") and result["metrics"]["n_valued"] == 0)


def _score(score_inputs, source, fair_value, price=_USE_MODEL_PRICE, **extra):
    from hashlib import sha256
    from test_sector_usability import payload_for
    portfolio, report_dir = score_inputs
    documented = payload_for()
    if price is _USE_MODEL_PRICE:
        price = documented["price"]
    documented.pop("fair_value_weighted")  # Preserve the original per-test FV precedence.
    if source == "sidecar":
        workbook = report_dir / "VAL_SYNTH.xlsx"
        workbook.write_bytes(b"synthetic score workbook")
        payload = {**documented, "_timestamp": date.today().isoformat(), "price": price,
                   "workbook_sha256": sha256(workbook.read_bytes()).hexdigest(),
                   "fair_value_final": fair_value, **extra}
        (report_dir / "VAL_SYNTH.payload.json").write_text(
            json.dumps(payload), encoding="utf-8")
        return specialist_scores.fundamentals_score(portfolio)
    return specialist_scores.fundamentals_score(portfolio, valuations={
        "SYNTH": {**documented, "fair_value": fair_value, "price": price, **extra},
    })


@pytest.mark.parametrize("source", ["injected", "sidecar"])
@pytest.mark.parametrize("fair_value,price", [
    (float("nan"), 10), (float("inf"), 10), (True, 10), ("14.13", 10),
    (14.13, float("nan")), (14.13, float("inf")), (14.13, 0), (14.13, -1),
    (14.13, True), (14.13, "10"), (1e308, 1e-308),
])
def test_invalid_valuation_never_becomes_a_scored_name(score_inputs, source, fair_value, price):
    assert _score(score_inputs, source, 14.13)["metrics"]["n_valued"] == 1
    assert _nd(_score(score_inputs, source, fair_value, price))


@pytest.mark.parametrize("source", ["injected", "sidecar"])
def test_zero_fair_value_is_not_missing_or_replaced(score_inputs, source):
    result = _score(score_inputs, source, 0, fair_value_weighted=200)
    # FV zero = -100%, troncato al tetto dichiarato -50% (09/10): presente, non mancante
    assert result["metrics"] == {
        "mos_book_pct": -50.0, "n_valued": 1, "copertura_book_pct": 100.0,
        "quota_cara_pct": 100.0, "quota_sconto_pct": 0.0, "n_troncati": 1,
        # v2 10/10: cari/troncati citati nel verdetto, nomi col prezzo da seconda fonte
        "quota_cari_troncati_pct": 100.0, "n_proxy_prezzo": 0,
    }


def test_absent_final_value_can_use_lower_priority_present_value(score_inputs):
    result = _score(score_inputs, "sidecar", None, fair_value_weighted=14.13)
    assert result["metrics"]["mos_book_pct"] == 41.3


def test_invalid_final_value_cannot_silently_use_lower_priority_value(score_inputs):
    assert _nd(_score(score_inputs, "sidecar", float("nan"), fair_value_weighted=14.13))


@pytest.mark.parametrize("source", ["injected", "sidecar"])
def test_flagged_valuation_remains_excluded(score_inputs, source):
    assert _nd(_score(score_inputs, source, 14.13, valuation_flagged=True))


def test_sidecar_sanity_block_remains_excluded(score_inputs):
    assert _nd(_score(score_inputs, "sidecar", 14.13, sanity={"severity": "BLOCK"}))


def test_partial_score_declares_name_with_invalid_valuation(score_inputs):
    from test_sector_usability import payload_for
    portfolio, _ = score_inputs
    portfolio["positions"].append({"ticker": "BAD", "peso_pct": 50})
    documented = payload_for()
    result = specialist_scores.fundamentals_score(portfolio, valuations={
        "SYNTH": {**documented, "fair_value": documented["fair_value_base"]},
        "BAD": {**documented, "ticker": "BAD", "fair_value": float("nan")},
    })
    assert result["metrics"]["n_valued"] == 1
    assert result["metrics"]["mos_book_pct"] == 41.3
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
