# -*- coding: utf-8 -*-
"""Un errore API del red team lascia nel registro la CAUSA, non il silenzio (audit 10/09).

Run 1 del 10/09 (memo #53): il modello del red team era bloccato da OpenRouter (HTTP 403,
attestazione 18+); run_red_team ha stampato "API error (procedo senza)" e restituito "",
SENZA scrivere `_red_team`. Il Capo ha letto "chiave assente: il red team non risulta
girato" e ha scritto al PM che il red team "non ha girato": falso nella causa — e' girato
ed e' stato respinto dal gate del modello. Un guasto dichiarato nel registro arriva al
Capo, ai desk R2 e al memo con la causa vera.

Client finto che alza l'errore; blackboard senza DB; niente rete.
"""
import sys
import types

import pytest

import bellomberg.agents.red_team as rt
from bellomberg.agents.specialists.base import Blackboard


class _Boom:
    def __init__(self, *a, **k):
        self.messages = self

    def create(self, **kw):
        raise RuntimeError("HTTP 403: This model requires you to complete the following before use: "
                           "18+ age confirmation. Confirm at https://openrouter.ai/settings/preferences.")


@pytest.fixture
def bb(tmp_path, monkeypatch):
    monkeypatch.setattr(Blackboard, "HEARTBEAT_PATH", str(tmp_path / "current_run.json"))
    monkeypatch.setenv("RED_TEAM_MODEL", "prova/modello-bloccato")
    import bellomberg.core.llm_client as lc
    monkeypatch.setattr(lc, "OpenRouterClient", _Boom)
    cf = types.ModuleType("current_facts")
    cf.pm_theses_block = lambda: ""
    cf.current_facts_block = lambda: ""
    cf.favorites_block = lambda: ""
    cf.research_block = lambda: ""
    monkeypatch.setitem(sys.modules, "bellomberg.core.current_facts", cf)
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", cf, raising=False)
    board = Blackboard()
    board.write("macro", 1, "tesi macro di prova")
    return board


def test_l_errore_api_finisce_nel_registro_con_la_causa(bb):
    crit = rt.run_red_team(bb, portfolio_data=None, memory_db=None)
    assert crit == "", "il contratto del chiamante resta: stringa vuota = nessuna critica"
    registro = bb.data.get("_red_team") or {}
    assert registro, "il guasto deve lasciare traccia nel registro"
    testo = registro[max(registro.keys())]
    assert "NON DISPONIBILE" in testo and "403" in testo, testo


def test_il_capo_legge_la_causa_non_un_non_risulta_girato(bb):
    rt.run_red_team(bb, portfolio_data=None, memory_db=None)
    from bellomberg.agents.capo import _blocco_red_team
    righe = _blocco_red_team(bb.data.get("_red_team"))
    testo = "\n".join(righe)
    assert "ASSENTE" in testo, testo
    assert "403" in testo, testo
    assert "non risulta girato" not in testo, testo


def test_il_classificatore_riconosce_il_guasto_dichiarato(bb):
    rt.run_red_team(bb, portfolio_data=None, memory_db=None)
    testo = bb.data["_red_team"][1]
    motivo = rt.motivo_critica_non_utilizzabile(testo)
    assert motivo and "403" in motivo, motivo
