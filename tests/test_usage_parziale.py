"""Un contatore assente non diventa zero; costo provider e token sono indipendenti."""
from types import SimpleNamespace

import pytest
import sys

from bellomberg.core import llm_client
from bellomberg.core import llm_pricing
from bellomberg.agents.specialists.base import Blackboard
from bellomberg.agents.specialists import base


def u(**cambi):
    valori = dict(input_tokens=10, output_tokens=4, cache_read_input_tokens=0,
                  cache_creation_input_tokens=0, cost_usd=0.03)
    valori.update(cambi)
    return SimpleNamespace(**valori)


@pytest.mark.parametrize("risposta, atteso", [(None, None), (u(input_tokens=None), None),
                                             (u(input_tokens=0), 0), (u(), 10)])
def test_accumulo_distingue_assente_da_zero(risposta, atteso):
    totale = llm_client.somma_usage(None, risposta)
    assert totale["in"] == atteso
    assert totale["tokens_status"] == ("completo" if atteso is not None else "parziale")


def test_chiamata_successiva_non_ripara_token_mancanti_ma_costo_resta():
    a = llm_client.somma_usage(None, u(input_tokens=None))
    b = llm_client.somma_usage(a, u())
    assert b["in"] is None and b["out"] == 8
    assert b["tokens_missing"] == ["in"]
    assert b["cost_usd"] == pytest.approx(0.06)


def test_listino_non_prezzabile_con_input_parziale():
    r = llm_pricing.cost_usd("claude-opus-5", {"in": None, "out": 4})
    assert r["cost"] is None and r["status"] == "usage_unknown"
    assert r["breakdown"]["tokens"]["in"] is None


@pytest.mark.parametrize("valore", [True, 1.5, float("inf"), float("nan"), -1])
def test_contatore_invalido_non_diventa_misura(valore, monkeypatch):
    assert llm_pricing.normalize_usage({"in": valore})["in"] is None
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    bb = Blackboard()
    entry = bb.record_usage("quant", 1, "claude-opus-5", {"in": valore, "out": 4})
    assert entry["in"] is None


def test_costo_booleano_non_e_misurato():
    assert llm_client.somma_costo(0.0, u(cost_usd=False)) is None


def test_costo_provider_prevale_anche_con_token_parziali():
    r = llm_pricing.cost_usd("claude-opus-5", {"in": None, "out": 4, "cost_usd": 0.07})
    assert r["cost"] == 0.07 and r["status"] == "ok"
    assert r["breakdown"]["tokens"]["in"] is None


def test_blackboard_e_totale_preservano_null_e_costo_misurato(monkeypatch):
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    llm_pricing.reset_fx_memo()
    try:
        bb = Blackboard()
        r = bb.record_usage("quant", 1, "claude-opus-5",
                            {"in": None, "out": 4, "cache_read": 0, "cache_write": 0, "cost_usd": 0.1},
                            status="usage_unknown")
        assert r["in"] is None and r["cost_eur"] == pytest.approx(0.09)
        bb.record_usage("quant", 2, "claude-opus-5",
                        {"in": 10, "out": 4, "cache_read": 0, "cache_write": 0, "cost_usd": 0.1})
        by, total = bb._usage_aggregates()
        for risultato in (by["quant"], total):
            assert risultato["in"] is None and risultato["out"] == 8
            assert risultato["tokens_status"] == "parziale"
            assert risultato["tokens_missing"] == ["in"]
            assert risultato["cost_eur"] == pytest.approx(0.18)
    finally:
        llm_pricing.reset_fx_memo()


@pytest.mark.parametrize("risposta", [None, u(input_tokens=None), u(input_tokens=0)])
def test_round_reale_con_usage_incompleta_conserva_report_e_contatori(monkeypatch, risposta):
    monkeypatch.setattr(Blackboard, "_write_heartbeat", lambda self: None)
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    monkeypatch.setattr(base, "USE_PROMPT_CACHING", False)
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", SimpleNamespace(
        current_facts_block=lambda: "", favorites_block=lambda: "", pm_theses_block=lambda: ""), raising=False)
    llm_pricing.reset_fx_memo()
    try:
        bb = Blackboard()
        report = "REPORT SINTETICO COMPLETO. " * 40
        resp = SimpleNamespace(usage=risposta, stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=report)])
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
        spec = base.Specialist(bb, client=client)
        monkeypatch.setattr(spec, "_build_tools_schema", lambda: [])
        assert spec.run(0).strip() == report.strip()
        entry = bb.usage_log[0]
        assert entry["in"] == (getattr(risposta, "input_tokens", None))
        if risposta is not None:
            assert entry["cost_eur"] == pytest.approx(0.027)
        else:
            assert entry["cost_eur"] is None
    finally:
        llm_pricing.reset_fx_memo()


def test_chat_usage_parziale_non_perde_il_costo_provider(monkeypatch):
    from bellomberg.agents import chat_engine
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "live"))
    llm_pricing.reset_fx_memo()
    try:
        r = chat_engine._done_payload(1, ok=True, model="claude-opus-5", tokens_in=None,
                                      tokens_out=4, cache_read=None, cache_write=None,
                                      iterations=1, usage_visto=True, cost_usd=0.2)
        assert r["ok"] and r["tokens_in"] is None and r["tokens_status"] == "parziale"
        assert r["cost_eur"]["cost"] == pytest.approx(0.18)
    finally:
        llm_pricing.reset_fx_memo()
