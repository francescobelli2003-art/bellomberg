"""Robustezza run (§9-bis n.5, collaudo mock 21/07 portato a pytest):
l'ULTIMA iterazione e' SEMPRE il report (tool_choice none + nudge + prefisso
dichiarato), la run normale resta invariata, il red team non scrive MAI una
critica vuota zitta nel blackboard.

Client Anthropic FINTO e scriptabile (sempre-tool_use finche' non viene
forzato): zero rete, zero API, zero DB. Heartbeat su file temporaneo
(data/current_run.json vero e' della UI live), current_facts/chat_tools
stubbati (leggerebbero DB vivo / dispatcher vero).
"""
import sys
import types

import pytest

from bellomberg.core import llm_pricing
from bellomberg.agents.specialists.base import MAX_TOOL_ITERS_SPECIALIST, Blackboard, Specialist
import bellomberg.agents.red_team as red_team_mod


# ---------- doppioni minimi delle risposte SDK Anthropic ----------
class _Usage:
    input_tokens = 100
    output_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, name="read_blackboard"):
        self.name = name
        self.input = {}
        self.id = "tu_mock"


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = _Usage()


def _tool_resp(name="read_blackboard"):
    return _Resp("tool_use", [_ToolUseBlock(name)])


def _text_resp(text):
    return _Resp("end_turn", [_TextBlock(text)])


class _FakeClient:
    """client.messages.create scriptabile: script(n_chiamata, kwargs) -> _Resp."""

    def __init__(self, script):
        self.calls = []
        self._script = script
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._script(len(self.calls), kwargs)


class _MockSpecialist(Specialist):
    name = "quant"
    role = "mock"
    system_prompt = "Sei un mock per il collaudo offline."
    tools_used = []


@pytest.fixture
def bb(tmp_path, monkeypatch):
    monkeypatch.setattr(Blackboard, "HEARTBEAT_PATH",
                        str(tmp_path / "current_run.json"))
    # FX deterministico: record_usage -> cost_eur non deve toccare price_updater
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur",
                        lambda: (0.9, "fallback"))
    llm_pricing.reset_fx_memo()
    # current_facts legge il DB vivo: stub (qui si collauda il loop, non i fatti)
    cf = types.ModuleType("current_facts")
    cf.current_facts_block = lambda: ""
    cf.favorites_block = lambda: ""
    cf.pm_theses_block = lambda: ""
    cf.research_block = lambda: ""
    monkeypatch.setitem(sys.modules, 'bellomberg.core.current_facts', cf)
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", cf, raising=False)
    # chat_tools: registro finto (il dispatcher vero chiamerebbe tool con rete)
    ct = types.ModuleType("chat_tools")
    _tool = {"name": "get_portfolio_live", "description": "mock",
             "input_schema": {"type": "object", "properties": {}}}
    ct.get_tools_for_agent = lambda name: [dict(_tool)]
    ct.TOOL_DEFINITIONS = [dict(_tool)]
    ct.dispatch = lambda name, args=None: {"ok": True, "mock": name}
    monkeypatch.setitem(sys.modules, 'bellomberg.agents.chat_tools', ct)
    import bellomberg.agents
    monkeypatch.setattr(bellomberg.agents, "chat_tools", ct, raising=False)
    board = Blackboard()  # memory_db=None: nessuna scrittura DB
    yield board
    llm_pricing.reset_fx_memo()


def test_report_forzato_alla_decima_iterazione(bb, capsys):
    def script(n, kwargs):
        if kwargs.get("tool_choice") == {"type": "none"}:
            return _text_resp("REPORT MOCK: dati raccolti, buchi dichiarati n.d.")
        assert n < MAX_TOOL_ITERS_SPECIALIST, \
            "l'ultima chiamata DEVE arrivare con tool_choice none (report forzato)"
        return _tool_resp()

    client = _FakeClient(script)
    out = _MockSpecialist(bb, client=client).run(1)

    assert out.startswith("[REPORT FORZATO AL LIMITE ITERAZIONI (%d)"
                          % MAX_TOOL_ITERS_SPECIALIST)
    # Voce 6 §9-quattuortrigies (01/08): il marcatore deve stare ANCHE nel log
    # (stdout), non solo nel testo del report in DB — sulla V6 tre report su
    # dieci lo portavano e `grep 'REPORT FORZATO'` sul log dava 0.
    assert "REPORT FORZATO AL LIMITE ITERAZIONI" in capsys.readouterr().out
    assert "REPORT MOCK" in out
    # budget 9+1: stesso numero di chiamate di prima, l'ultima e' il report
    assert len(client.calls) == MAX_TOOL_ITERS_SPECIALIST
    # nudge dichiarato IN CODA ai tool_result dello stesso messaggio user
    # (due user consecutivi = errore API)
    last_msgs = client.calls[-1]["messages"]
    assert last_msgs[-1]["role"] == "user"
    tail = last_msgs[-1]["content"][-1]
    assert tail.get("type") == "text"
    assert "LIMITE ITERAZIONI TOOL RAGGIUNTO" in tail["text"]
    roles = [m["role"] for m in last_msgs]
    assert all(a != b for a, b in zip(roles, roles[1:])), "ruoli non alternati"
    # i 9 giri tool sono stati ESEGUITI e l'usage e' contabilizzato per intero
    assert len(bb.tool_log) == MAX_TOOL_ITERS_SPECIALIST - 1
    entry = bb.usage_log[-1]
    assert entry["agent"] == "quant"
    assert entry["api_calls"] == MAX_TOOL_ITERS_SPECIALIST
    assert entry["status"] == "ok" and entry["cost_eur"] is not None


def test_run_normale_invariata(bb):
    def script(n, kwargs):
        assert "tool_choice" not in kwargs, "run normale: mai tool_choice forzato"
        return _tool_resp() if n == 1 else _text_resp("Analisi mock completa.")

    client = _FakeClient(script)
    out = _MockSpecialist(bb, client=client).run(1)

    assert out == "Analisi mock completa."
    assert "[REPORT FORZATO" not in out
    assert len(client.calls) == 2
    assert bb.usage_log[-1]["api_calls"] == 2


def test_red_team_forzatura_quarto_giro_e_critica_mai_vuota(bb, monkeypatch):
    from bellomberg.core import llm_client

    bb.write("quant", 1, "Tesi mock: Banca Gamma (GAMMA.MI) sottovalutata.")
    calls = []

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = self

        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) >= 4:
                assert kwargs.get("tool_choice") == {"type": "none"}
                return _text_resp("CRITICA MOCK: assunzione fragile su GAMMA.MI.")
            return _tool_resp("get_portfolio_live")

    monkeypatch.setattr(llm_client, "OpenRouterClient", _FakeAnthropic)
    critique = red_team_mod.run_red_team(bb)

    assert critique.startswith("[CRITICA AL LIMITE VERIFICHE (4 giri)")
    assert "CRITICA MOCK" in critique
    assert len(calls) == 4
    assert bb.read("_red_team", 1) == critique

    # cintura: anche se il giro forzato non produce testo, nel blackboard
    # finisce il buco DICHIARATO, mai una stringa vuota zitta (regola 14/07)
    calls.clear()
    bb2 = Blackboard()
    bb2.write("quant", 1, "Tesi mock 2.")

    class _FakeAnthropicVuoto(_FakeAnthropic):
        def create(self, **kwargs):
            calls.append(kwargs)
            return _text_resp("") if len(calls) >= 4 else _tool_resp("get_portfolio_live")

    monkeypatch.setattr(llm_client, "OpenRouterClient", _FakeAnthropicVuoto)
    critique2 = red_team_mod.run_red_team(bb2)
    assert critique2.startswith("[RED TEAM: nessuna critica prodotta")
    assert bb2.read("_red_team", 1) == critique2
