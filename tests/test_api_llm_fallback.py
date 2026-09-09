import builtins
import sys
import types

from bellomberg.api import bellomberg_api


def _agents_list_endpoint():
    return next(
        route.endpoint
        for route in bellomberg_api.app.routes
        if getattr(route, "path", None) == "/agents/list"
    )


def _chat_engine_sintetico(monkeypatch):
    modulo = types.ModuleType("bellomberg.agents.chat_engine")
    modulo.AGENT_DISPLAY_NAMES = {"capo": "Capo", "macro": "Macro"}
    modulo.LEGACY_AGENT_IDS = set()
    monkeypatch.setitem(sys.modules, 'bellomberg.agents.chat_engine', modulo)


def test_agents_list_dichiara_la_causa_se_llm_client_non_e_importabile(monkeypatch):
    _chat_engine_sintetico(monkeypatch)
    import_reale = builtins.__import__

    def import_con_guasto(name, *args, **kwargs):
        if name == "bellomberg.core.llm_client":
            raise ImportError("guasto llm sintetico")
        return import_reale(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_con_guasto)

    risposta = _agents_list_endpoint()()

    assert [agente["id"] for agente in risposta["agents"]] == ["capo", "macro"]
    assert {agente["model"] for agente in risposta["agents"]} == {
        "n.d. (llm_client non importabile: guasto llm sintetico)"
    }
    assert risposta["engines"] == {"engines_error": "guasto llm sintetico"}


def test_agents_list_preserva_i_modelli_risolti_quando_llm_client_e_sano(monkeypatch):
    _chat_engine_sintetico(monkeypatch)
    modulo = types.ModuleType("bellomberg.core.llm_client")
    modulo.DESK = ("macro",)

    def modello_o_buco(funzione, agente=None, round_n=None):
        dettagli = ":".join(
            parte for parte in (funzione, agente, str(round_n) if round_n is not None else None)
            if parte is not None
        )
        return "modello-sintetico:" + dettagli

    modulo.modello_o_buco = modello_o_buco
    monkeypatch.setitem(sys.modules, 'bellomberg.core.llm_client', modulo)

    risposta = _agents_list_endpoint()()

    assert [agente["model"] for agente in risposta["agents"]] == [
        "modello-sintetico:chat:capo",
        "modello-sintetico:chat:macro",
    ]
    assert risposta["engines"]["red_team"] == "modello-sintetico:red_team"
    assert risposta["engines"]["committee_macro"] == "modello-sintetico:consigliere:macro"
    assert all("llm_client non importabile" not in agente["model"]
               for agente in risposta["agents"])
