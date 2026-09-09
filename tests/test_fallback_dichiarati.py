"""Test OFFLINE: i ripieghi di specialists/base.py non sono piu' MUTI (P1 26/07 sera-4).

Il buco, misurato dalla review di I-1: `_build_tools_schema` e `_execute_meta_tool`
ripiegavano sul registro LEGACY `agent_tools` con un `except Exception:` nudo, senza
dire nulla. Se l'import di `chat_tools` fallisse, il comitato girerebbe con un
arsenale mutilato — degrado misurato: **quant 22->3 tool, options 15->3,
fundamentals 24->6, macro 13->8** — e `agent_tools.execute_tool` risponderebbe
"Tool sconosciuto" per **30 dei 51** nomi che il modello ha nello schema. Il memo
usciva comunque. E' il pattern che la regola PM 14/07 vieta, sul percorso di una
run da ~10 EUR.

Il fix NON toglie il ripiego (una run mutilata e' meglio di nessuna run): toglie
il silenzio, su tre canali — log, registro `FALLBACK_DICHIARATI`, e il prompt del
modello, che deve dichiarare il buco nel report.

Qui si verifica che il ripiego SCATTI e PARLI, e — meta' altrettanto importante —
che nel percorso SANO non cambi assolutamente nulla.

Zero rete, zero DB, zero LLM: l'import di chat_tools viene fatto fallire a mano.
"""
import builtins
import json
from types import SimpleNamespace

import pytest

from bellomberg.agents.specialists import base as sbase


class _Finto(sbase.Specialist):
    name = "quant"          # combacia con una chiave dei SUBSETS veri
    role = "finto"
    system_prompt = "PROMPT DI PROVA"
    tools_used = ["get_portfolio_live", "get_market_data"]


def _spec():
    return _Finto(blackboard=None, client=object())


@pytest.fixture(autouse=True)
def _registro_pulito():
    """Il registro e' modulo-level: senza pulizia i test si contaminano."""
    sbase.FALLBACK_DICHIARATI.clear()
    yield
    sbase.FALLBACK_DICHIARATI.clear()


@pytest.fixture
def chat_tools_rotto(monkeypatch):
    """Fa fallire SOLO l'import canonico di `chat_tools`, lasciando vivo il resto."""
    vero = builtins.__import__

    def finto(nome, globals=None, locals=None, fromlist=(), level=0):
        if (nome == "bellomberg.agents.chat_tools"
                or (nome == "bellomberg.agents" and "chat_tools" in fromlist)):
            raise ImportError("boom simulato dal test")
        return vero(nome, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", finto)
    return finto


# --------------------------------------------------------------------------
# 1. PERCORSO SANO: niente deve cambiare (la meta' che protegge la run)
# --------------------------------------------------------------------------

def test_percorso_sano_nessun_fallback_e_nessuna_riga_nel_prompt():
    s = _spec()
    schema = s._build_tools_schema()
    nomi = [t["name"] for t in schema]
    assert sbase.FALLBACK_DICHIARATI == [], (
        f"ripiego dichiarato su un percorso SANO: {sbase.FALLBACK_DICHIARATI}")
    assert s._arsenale_degradato is None, "arsenale marcato degradato senza motivo"
    # il subset vivo del quant e' ricco: se qui arrivassero 3 tool sarebbe il legacy
    assert len(nomi) > 10, f"arsenale sospetto nel caso sano: {len(nomi)} tool"
    assert "ask_specialist" in nomi and "read_blackboard" in nomi


def test_il_default_di_classe_esiste_sempre():
    """Serve perche' il prompt fa `if self._arsenale_degradato`: su un'istanza
    costruita da codice vecchio non deve alzare AttributeError."""
    assert sbase.Specialist._arsenale_degradato is None


# --------------------------------------------------------------------------
# 2. RIPIEGO SUL REGISTRO: scatta, e parla su tutti e tre i canali
# --------------------------------------------------------------------------

def test_ripiego_registro_dichiarato_nel_log_e_nel_registro(chat_tools_rotto, capsys):
    s = _spec()
    schema = s._build_tools_schema()

    assert sbase.FALLBACK_DICHIARATI, "il ripiego sul registro legacy e' ancora MUTO"
    testo = sbase.FALLBACK_DICHIARATI[0]
    assert "_build_tools_schema[quant]" in testo, testo
    assert "ImportError" in testo and "boom simulato" in testo, (
        "la CAUSA non e' dichiarata: senza il tipo e il messaggio l'autopsia riparte da zero")
    assert "LEGACY" in testo
    assert "[!!]" in capsys.readouterr().out, "niente urlato sul log del PM"
    # il ripiego funziona ancora: la run limpica, non muore
    assert [t["name"] for t in schema], "il ripiego non ha prodotto alcun tool"


def test_ripiego_registro_dichiarato_AL_MODELLO(chat_tools_rotto):
    """Il canale che conta: un report che tace il buco inganna il PM."""
    s = _spec()
    s._build_tools_schema()
    assert s._arsenale_degradato, "il modello non viene informato del degrado"
    assert "ARSENALE DEGRADATO" in (
        "[!! ARSENALE DEGRADATO - DICHIARALO NEL REPORT] " + s._arsenale_degradato)


def test_degrado_misurato_vivo_vs_legacy(monkeypatch):
    """Misura il degrado invece di affermarlo: se un domani il registro legacy
    tornasse ricco quanto il vivo, la premessa della voce P1 andrebbe rifatta.
    (Niente fixture `chat_tools_rotto` qui: serve MISURARE prima il caso sano.)"""
    vivo = len(_spec()._build_tools_schema())
    vero = builtins.__import__

    def finto(nome, globals=None, locals=None, fromlist=(), level=0):
        if (nome == "bellomberg.agents.chat_tools"
                or (nome == "bellomberg.agents" and "chat_tools" in fromlist)):
            raise ImportError("boom")
        return vero(nome, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", finto)
    legacy = len(_spec()._build_tools_schema())
    assert legacy < vivo, (
        f"il ripiego non e' piu' povero del registro vivo ({legacy} vs {vivo}): "
        "la premessa della voce P1 va rimisurata")


# --------------------------------------------------------------------------
# 3. RIPIEGO SUL DISPATCHER: lo scambio arriva al modello nel tool_result
# --------------------------------------------------------------------------

def test_ripiego_dispatcher_dichiarato_nel_tool_result(chat_tools_rotto, monkeypatch):
    # 26/07 sera-5 (Opus 5, voce I-3 passo 0): questo test apriva il DB DI
    # PRODUZIONE — `tool_get_portfolio_live` fa `MemoryDB()` (agent_tools.py:1280)
    # e la suite e' offline per contratto. Trovato dalla guardia autouse di
    # conftest, non a mano. Si stubba SOLO la foglia che tocca il DB: la
    # `execute_tool` vera e il dispatch vero restano nel percorso del test
    # (TOOL_DISPATCHER tiene il riferimento alla funzione -> setitem, non setattr).
    from bellomberg.agents import agent_tools
    monkeypatch.setitem(agent_tools.TOOL_DISPATCHER, "get_portfolio_live",
                        lambda **_k: {"positions": [], "src": "stub offline"})
    s = _spec()
    r = s._execute_meta_tool("get_portfolio_live", {})
    assert isinstance(r, dict)
    assert "_fallback_dichiarato" in r, (
        "lo SCAMBIO di dispatcher e' ancora muto verso il modello: vedrebbe un "
        "risultato (o un 'Tool sconosciuto') senza sapere che gliel'ha dato il legacy")
    assert "dispatcher LEGACY" in r["_fallback_dichiarato"]
    assert sbase.FALLBACK_DICHIARATI, "nulla nel registro"
    # che lo stub sia stato USATO va asserito: `base.py` aggiunge
    # `_fallback_dichiarato` a QUALUNQUE dict, anche a
    # {"error": "Tool sconosciuto"} — se un domani la chiave del dispatcher
    # viene rinominata, il setitem diventa inefficace e il test scivolerebbe
    # su quel ramo passando comunque (review 26/07 sera-5)
    assert r.get("src") == "stub offline", (
        "lo stub non e' stato usato: il dispatch e' finito su 'Tool sconosciuto' "
        "e il test non prova piu' quello che dice")


def test_meta_tool_read_blackboard_non_passa_dal_ripiego(chat_tools_rotto):
    """`read_blackboard` e `ask_specialist` sono gestiti PRIMA del dispatcher:
    non devono essere marcati come ripieghi."""
    class _BB:
        def summary_for_specialist(self, _n):
            return {"ok": True}
    s = _Finto(blackboard=_BB(), client=object())
    r = s._execute_meta_tool("read_blackboard", {})
    # 21/08: la forma di ritorno e' cambiata di proposito (ora e' la vista che
    # dichiara le quote per desk, cosi' nessun desk sparisce sotto il tetto dei
    # tool_result). Cio' che questo test deve provare resta lo stesso: il meta-tool
    # non passa dal dispatcher e non viene marcato come ripiego.
    assert "ok" in json.dumps(r, default=str).lower(), (
        "il contenuto del blackboard non arriva piu' nel risultato: %r" % (r,))
    assert sbase.FALLBACK_DICHIARATI == [], (
        f"meta-tool marcato come ripiego per errore: {sbase.FALLBACK_DICHIARATI}")


# --------------------------------------------------------------------------
# 4. Il dichiaratore non puo' rompere il chiamante (e' dentro un except)
# --------------------------------------------------------------------------

def test_il_dichiaratore_regge_lo_stdout_morto(monkeypatch):
    """Residuo dichiarato della review (50): un logger non deve MAI poter
    rompere il chiamante. Qui siamo dentro un `except`: se il print alza, il
    ripiego non parte e la run muore per colpa del log."""
    def print_morto(*a, **k):
        raise OSError("[Errno 22] Invalid argument")

    monkeypatch.setattr(builtins, "print", print_morto)
    testo = sbase._dichiara_fallback("prova", ValueError("x"), "dettaglio")
    assert "ValueError" in testo
    assert sbase.FALLBACK_DICHIARATI, "il registro deve reggere anche senza stdout"


def test_il_dichiaratore_regge_un_errore_senza_messaggio():
    testo = sbase._dichiara_fallback("prova", RuntimeError(), "")
    assert "RuntimeError" in testo, (
        "un'eccezione a messaggio vuoto non deve produrre una dichiarazione vuota "
        "(stessa classe del detail: '' sanato dal lotto (50))")


# Red Team: verificare gli argomenti consegnati al client, non solo il log.
_TOOL_RED_TEAM = ("get_portfolio_live", "get_portfolio_risk", "get_advanced_metrics")


class _RichiestaRedTeam(BaseException):
    def __init__(self, kwargs):
        self.kwargs = kwargs


@pytest.fixture
def richiesta_red_team(monkeypatch):
    from bellomberg.core import current_facts
    from bellomberg.core import llm_client

    def cattura(**kwargs):
        raise _RichiestaRedTeam(kwargs)

    monkeypatch.setattr(current_facts, "pm_theses_block", lambda: "")
    monkeypatch.setattr(llm_client, "OpenRouterClient", lambda **kwargs:
                        SimpleNamespace(messages=SimpleNamespace(create=cattura)))

    def invoca():
        from bellomberg.agents.red_team import run_red_team
        bb = SimpleNamespace(data={"quant": {1: "Tesi sintetica da verificare."}},
                             memory_db=None)
        with pytest.raises(_RichiestaRedTeam) as richiesta:
            run_red_team(bb)
        return richiesta.value.kwargs

    return invoca


@pytest.mark.parametrize("mancanti", [(), ("get_portfolio_risk",), _TOOL_RED_TEAM])
def test_red_team_dichiara_solo_i_tool_mancanti(monkeypatch, richiesta_red_team, mancanti):
    from bellomberg.agents import chat_tools
    schemi = [s for s in chat_tools.TOOL_DEFINITIONS if s["name"] not in mancanti]
    monkeypatch.setattr(chat_tools, "TOOL_DEFINITIONS", schemi)
    attesi = [s for s in schemi if s["name"] in _TOOL_RED_TEAM]

    richiesta = richiesta_red_team()
    assert richiesta.get("tools", []) == attesi
    testo = richiesta["messages"][0]["content"]
    assert ("ARSENALE DEGRADATO" in testo) == bool(mancanti)
    assert bool(sbase.FALLBACK_DICHIARATI) == bool(mancanti)
    if mancanti:
        avviso = testo.split("ARSENALE DEGRADATO", 1)[1]
        for nome in mancanti:
            assert nome in avviso
        for nome in set(_TOOL_RED_TEAM) - set(mancanti):
            assert nome not in avviso
        assert "non hai potuto verificare" in avviso
        assert "a memoria" in avviso


def test_red_team_import_fallito_dichiara_causa_e_tool(
        richiesta_red_team, chat_tools_rotto, capsys):
    richiesta = richiesta_red_team()
    assert "tools" not in richiesta
    testo = richiesta["messages"][0]["content"]
    assert "ARSENALE DEGRADATO" in testo
    assert "ImportError" in testo and "boom simulato dal test" in testo
    for nome in _TOOL_RED_TEAM:
        assert nome in testo
    assert "run_red_team.tools_schema" in sbase.FALLBACK_DICHIARATI[-1]
    assert "boom simulato dal test" in capsys.readouterr().out
