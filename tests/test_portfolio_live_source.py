"""Voce 9 di MASTER_TODO §9-quattuortrigies (riparata 01/08/2026, Fable 5).

memory_db.get_portfolio_summary() dichiara nel campo `source` l'avviso
"FX non disponibile per X: valori in valuta NATIVA sommati nel NAV"
(regola 14/07); agent_tools.tool_get_portfolio_live lo SOVRASCRIVEVA
incondizionatamente con una stringa fissa — l'unico punto in cui gli agenti
leggevano quell'avviso in chiaro. Ora la stringa fissa si applica SOLO nel
caso pulito (fx_incomplete assente).
"""
from bellomberg.agents import agent_tools
from bellomberg.storage import memory_db


def _stub_db(monkeypatch, summ):
    class _DB:
        def get_portfolio_summary(self):
            return summ

    monkeypatch.setattr(memory_db, "MemoryDB", lambda: _DB())


def test_avviso_fx_sopravvive_al_tool(monkeypatch):
    _stub_db(monkeypatch, {
        "n_positions": 1,
        "source": ("SQLite database (live) — ATTENZIONE: FX non disponibile per "
                   "ALFA: valori in valuta NATIVA sommati nel NAV"),
        "fx_incomplete": ["ALFA"],
    })
    out = agent_tools.tool_get_portfolio_live()
    assert "FX non disponibile" in out["source"], out["source"]


def test_caso_pulito_tiene_la_dicitura_fonte_unica(monkeypatch):
    _stub_db(monkeypatch, {
        "n_positions": 1,
        "source": "SQLite database (live, multi-currency normalized to EUR)",
        "fx_incomplete": None,
    })
    out = agent_tools.tool_get_portfolio_live()
    assert "fonte unica" in out["source"], out["source"]
