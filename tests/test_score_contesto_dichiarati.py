"""Contratti dei buchi nel prompt realmente inviato, con client sintetico."""
from types import SimpleNamespace

import pytest

from bellomberg.agents.specialists import base
from bellomberg.agents import agent_tools, specialist_scores
from bellomberg.core import current_facts


class Fermati(BaseException):
    pass


@pytest.fixture
def spec(monkeypatch):
    board = SimpleNamespace(data={}, memory_db=None,
                            mark_specialist_start=lambda *a: None)
    s = base.Specialist(board, client=object())
    monkeypatch.setattr(s, "_build_tools_schema", lambda: [])
    monkeypatch.setattr(base, "USE_PROMPT_CACHING", False)
    s.system_prompt = "SISTEMA SINTETICO"
    return s


@pytest.mark.parametrize("desk", ["fundamentals", "macro", "crypto", "quant", "options", "eventdesk"])
def test_eccezione_score_non_viene_assorbita_dal_desk(monkeypatch, desk):
    import importlib
    modulo = importlib.import_module("bellomberg.agents.specialists." + desk)
    classe = next(c for c in vars(modulo).values()
                  if isinstance(c, type) and issubclass(c, base.Specialist)
                  and c is not base.Specialist)
    def rotto(*a, **kw):
        raise RuntimeError("score fonte KO")
    monkeypatch.setattr(specialist_scores, desk + "_score", rotto)
    monkeypatch.setattr(agent_tools, "tool_get_portfolio_live", lambda: {})
    with pytest.raises(RuntimeError, match="score fonte KO"):
        classe(None, client=object()).compute_score()


def test_score_rotto_dichiarato_anche_al_round_successivo(spec, monkeypatch):
    chiamate = []
    def rotto():
        chiamate.append(1)
        raise RuntimeError("provider score KO")
    monkeypatch.setattr(spec, "compute_score", rotto)
    a = spec._build_round_context(0)
    b = spec._build_round_context(0)
    assert chiamate == [1]
    for testo in (a, b):
        assert "SCORE n.d." in testo and "provider score KO" in testo


def test_desk_senza_score_non_segnala_falso_guasto(spec):
    assert "SCORE n.d." not in spec._build_round_context(0)


@pytest.mark.parametrize("guasto", [None, "current_facts_block", "favorites_block", "pm_theses_block"])
def test_un_blocco_rotto_preserva_gli_altri_nel_client(spec, monkeypatch, guasto):
    nomi = ("current_facts_block", "favorites_block", "pm_theses_block")
    def fonte(nome):
        def leggi():
            if nome == guasto:
                raise RuntimeError("fonte " + nome + " KO")
            return "\nBLOCCO SANO " + nome
        return leggi
    for nome in nomi:
        monkeypatch.setattr(current_facts, nome, fonte(nome))
    consegne = []
    def cattura(**kwargs):
        consegne.append(kwargs)
        raise Fermati()
    spec.client = SimpleNamespace(messages=SimpleNamespace(create=cattura))
    with pytest.raises(Fermati):
        spec.run(0)
    testo = consegne[0]["messages"][0]["content"]
    for nome in nomi:
        if nome == guasto:
            assert "fonte " + nome + " KO" in testo
            assert "CONTESTO n.d." in testo
        else:
            assert "BLOCCO SANO " + nome in testo


def test_preferiti_guasti_diversi_da_elenco_vuoto_e_non_messi_in_cache(monkeypatch):
    import bellomberg.core.current_facts as cf
    from bellomberg.storage import memory_db
    monkeypatch.setattr(cf, "_FAV_CACHE", {"text": None, "ts": 0})
    def rotto(*a):
        raise RuntimeError("DB preferiti KO")
    monkeypatch.setattr(memory_db, "connect_sqlite", rotto)
    testo = cf.favorites_block()
    assert "PREFERITI n.d." in testo and "DB preferiti KO" in testo
    assert cf._FAV_CACHE["text"] is None
    conn = SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchall=lambda: []), close=lambda: None)
    monkeypatch.setattr(memory_db, "connect_sqlite", lambda *a: conn)
    assert cf.favorites_block() == ""
