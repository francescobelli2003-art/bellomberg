"""B3 (02/09, pubblicazione): il nome del PM esce dal codice ed entra in PM_NAME
(.env, default "PM": e' un'etichetta di cortesia, non un dato — il default e'
dichiarato nel template). Con lui escono gli ESEMPI di ticker del suo book dal
prompt della chat (chat_engine.py:90): la regola «solo ticker reali del
portafoglio» resta, vale per chiunque cloni il repo.
"""
import importlib
import json
import os
import re

import dotenv
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE_CODICE = ["src/bellomberg/agents/bellomberg.py",
               "src/bellomberg/cli/briefing_engine.py",
               "src/bellomberg/agents/capo.py",
               "src/bellomberg/agents/chat_engine.py",
               "src/bellomberg/core/config.py", "app/package.json",
               "tools/migrations/setup_twr_tables.py"]


def _ricarica(*nomi):
    return [importlib.reload(importlib.import_module(n)) for n in nomi]


@pytest.fixture(autouse=True, scope="module")
def _moduli_come_prima():
    """Fuga del reload (review 02/09): senza, capo.CAPO_SYSTEM_PROMPT resterebbe
    «Mario Rossi» per tutta la sessione. I monkeypatch dei singoli test sono gia'
    disfatti quando questo yield riprende: i moduli rileggono l'ambiente VERO."""
    yield
    _ricarica("bellomberg.core.config", "bellomberg.agents.bellomberg",
              "bellomberg.agents.capo")


def test_nome_sparito_dai_file():
    colpe = []
    for f in FILE_CODICE:
        for i, riga in enumerate(open(os.path.join(REPO, f), encoding="utf-8"), 1):
            if re.search(r"Francesco|\bBelli\b", riga):
                colpe.append(f"{f}:{i}")
    assert not colpe, colpe


def test_esempi_di_ticker_del_book_spariti_dal_prompt_chat():
    t = open(os.path.join(REPO, "src", "bellomberg", "agents", "chat_engine.py"),
             encoding="utf-8").read()
    # la FORMA dell'elenco (tre ticker .MI in fila), non i simboli: una guardia che
    # elenca il book lo pubblica (lezione T9, 02/09; riscritta 05/09, lotto 4)
    assert not re.search(r"[A-Z]{2,6}\.MI, [A-Z]{2,6}\.MI, [A-Z]{2,6}\.MI", t)
    assert "get_portfolio_live" in t   # la regola resta: il tool e' la fonte dei ticker


def test_pm_name_dal_env(monkeypatch):
    monkeypatch.setenv("PM_NAME", "Mario Rossi")
    config, bellomberg, capo = _ricarica(
        "bellomberg.core.config", "bellomberg.agents.bellomberg",
        "bellomberg.agents.capo")
    assert config.PM_NAME == "Mario Rossi"
    assert config.PM_DESC == "Mario Rossi (il PM)"
    assert "Mario Rossi" in bellomberg.get_pdf_header_footer()["footer_text"]
    assert "interlocutore e' Mario Rossi (il PM)" in capo.CAPO_SYSTEM_PROMPT
    assert "{PM_DESC}" not in capo.CAPO_SYSTEM_PROMPT


def test_pm_name_default_dichiarato(monkeypatch):
    monkeypatch.delenv("PM_NAME", raising=False)
    # config.py:9 rilegge il .env al reload (override=False = riempie SOLO le chiavi
    # assenti): col PM_NAME nel .env del PM il default non sarebbe mai osservabile
    # e questo test diventerebbe ROSSO da lui e verde in CI (review 02/09).
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    config, bellomberg, capo = _ricarica(
        "bellomberg.core.config", "bellomberg.agents.bellomberg",
        "bellomberg.agents.capo")
    assert config.PM_NAME == "PM"
    assert config.PM_DESC == "il PM"
    assert "Confidential to PM" in bellomberg.get_pdf_header_footer()["footer_text"]
    assert "interlocutore e' il PM." in capo.CAPO_SYSTEM_PROMPT
    assert "PM (il PM)" not in capo.CAPO_SYSTEM_PROMPT


def test_pm_name_cablato_nei_prompt_di_chat_e_briefing(monkeypatch):
    """Il cablaggio, non l'helper (review 02/09): le f-string di chat_engine e
    briefing_engine leggono la globale a chiamata. raising=True: se sparisce
    `from config import PM_DESC`, setattr solleva = RED."""
    from bellomberg.cli import briefing_engine
    from bellomberg.agents import chat_engine
    from bellomberg.core import current_facts
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "")   # niente rete/DB
    monkeypatch.setattr(chat_engine, "PM_DESC", "Mario Rossi (il PM)")
    monkeypatch.setattr(briefing_engine, "PM_DESC", "Mario Rossi (il PM)")
    assert "interlocutore e' Mario Rossi (il PM)" in chat_engine._build_system("capo")
    assert "per Mario Rossi (il PM)" in briefing_engine._build_briefing_prompt("morning", [], {}, {})


def test_pm_name_vuoto_vale_default(monkeypatch):
    monkeypatch.setenv("PM_NAME", "   ")
    (config,) = _ricarica("bellomberg.core.config")
    assert config.PM_NAME == "PM"


def test_package_json_autore_generico():
    pj = json.load(open(os.path.join(REPO, "app/package.json"), encoding="utf-8"))
    assert pj["author"] == "Bellomberg contributors"
