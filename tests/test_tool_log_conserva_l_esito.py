# -*- coding: utf-8 -*-
"""Il tool_log della blackboard conserva la testa dell'esito di ogni tool (audit run 10/09).

Fino all'11/09 il tool_log registrava solo l'input: per sapere cosa un desk avesse letto
(il «35%» preso dal web, il «count 0» di Polymarket, il prezzo di un titolo) non c'era
nulla da rileggere. Ora ogni voce porta `output` (testa, tetto fisso), `output_tappato`
e `output_chars`; il modello riceve l'esito intero come prima.

Client finto (idioma di test_tetto_specialisti_16k), zero rete.
"""
from test_tetto_specialisti_16k import bb, _FakeClient, _Resp, _TextBlock, _Usage, _MockSpecialist  # noqa: F401
import bellomberg.agents.specialists.base as base


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, name="get_portfolio_live"):
        self.name = name
        self.input = {"ticker": "ALFA"}
        self.id = "tu_prova"


def _tool_poi_report(n, kw):
    if n == 1:
        return _Resp("tool_use", [_ToolUseBlock()], _Usage())
    return _Resp("end_turn", [_TextBlock("REPORT")], _Usage())


def test_la_voce_del_tool_log_porta_la_testa_dell_esito(bb):
    client = _FakeClient(_tool_poi_report)
    _MockSpecialist(bb, client=client).run(1)
    voci = [v for v in bb.tool_log if v.get("tool") == "get_portfolio_live"]
    assert voci, bb.tool_log
    v = voci[-1]
    assert "output" in v and "get_portfolio_live" in v["output"], v      # il dispatch finto rende {"ok": True, "mock": name}
    assert v["output_tappato"] is False and v["output_chars"] == len(v["output"])
    assert v["input"].startswith("{'ticker': 'ALFA'")


def test_l_esito_lungo_e_tappato_e_dichiarato(bb, monkeypatch):
    import bellomberg.agents.chat_tools as ct
    monkeypatch.setattr(ct, "dispatch", lambda name, args=None, **k: {"blob": "x" * 5000})
    client = _FakeClient(_tool_poi_report)
    _MockSpecialist(bb, client=client).run(1)
    v = [v for v in bb.tool_log if v.get("tool") == "get_portfolio_live"][-1]
    assert len(v["output"]) == base.TOOL_LOG_OUTPUT_MAX
    assert v["output_tappato"] is True and v["output_chars"] > base.TOOL_LOG_OUTPUT_MAX
