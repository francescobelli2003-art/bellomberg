"""Tetto di output degli specialisti 12k → 16k (decisione PM 27/08), il timeout
del client che lo segue, e la troncatura che si DIAGNOSTICA dal log.

I fatti (run V8 e V9): quant R2 troncata due volte su due con
`stop_reason=max_tokens`. In V9 il log aggrega `out=14.512` su 3 call e il
testo salvato era 9.578 char: a 2,1 char/token (misure di casa, `count_tokens`
del 26/07) ≈ 4,6k token di testo, quindi circa 2/3 del cap era ragionamento
adattivo — che conta nel `max_tokens` («hard cap on thinking plus response
text», skill claude-api, migrazione a Opus 5). Il WARN diceva solo «TRONCATA».

Review 27/08 (1 agente Fable): (1) alzare il cap del 33% senza toccare il
`timeout=240` del client (#196, anti-blocco) esponeva a un timeout + retry
che perde il report INTERO — la call da 12k in V9 e' durata <=203 s (65-80
tok/s), a 16k sono 200-250 s; l'SDK stesso stima per il non-streaming
3600 x max_tokens / 128000 = 450 s a 16k → il timeout segue il cap;
(2) i test asserivano `== 16000`, che non distingue la costante da un
letterale → sentinella sulla costante.

Client finto scriptabile (idioma di test_run_robustness), zero rete, heartbeat
su tmp.
"""
import re
import sys
import types

import pytest

from bellomberg.core import llm_pricing
import bellomberg.agents.specialists.base as base
from bellomberg.agents.specialists.base import Blackboard, Specialist


class _Usage:
    def __init__(self, out=50):
        self.input_tokens = 100
        self.output_tokens = out
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, stop_reason, content, usage=None):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = usage


class _FakeClient:
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
    # heartbeat su tmp: `data/current_run.json` vero e' della UI live (F4)
    monkeypatch.setattr(Blackboard, "HEARTBEAT_PATH", str(tmp_path / "current_run.json"))
    # FX deterministico: record_usage -> cost_eur non deve toccare price_updater
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "fallback"))
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
    board = Blackboard()
    yield board
    llm_pricing.reset_fx_memo()


def _riga_warn(out):
    righe = [r for r in out.splitlines() if "TRONCATA" in r and "[quant]" in r]
    assert len(righe) == 1, "attesa UNA riga WARN TRONCATA del quant, trovate: %r" % (out,)
    return righe[0]


# ------------------------------------------------------------------ il cap

def test_la_decisione_pm_e_sedicimila():
    assert base.MAX_TOKENS_SPECIALIST == 16000


def test_la_chiamata_api_segue_la_costante_non_un_letterale(bb, monkeypatch):
    """Il cablaggio: con la costante a una sentinella, la call la segue. Un
    `max_tokens=16000` scritto a mano passerebbe il test sul valore, non questo."""
    monkeypatch.setattr(base, "MAX_TOKENS_SPECIALIST", 4242)
    client = _FakeClient(lambda n, kw: _Resp("end_turn", [_TextBlock("REPORT")], _Usage()))

    _MockSpecialist(bb, client=client).run(1)

    assert client.calls[0]["max_tokens"] == 4242, client.calls[0]["max_tokens"]


# ------------------------------------------------------------- il timeout

def test_il_timeout_del_client_segue_il_cap(bb, monkeypatch):
    """Review 27/08: alzare il cap senza il timeout esponeva a timeout + retry
    che perde il report intero. Il client va costruito con un timeout >= alla
    stima dell'SDK per il non-streaming (3600 x cap / 128000) e mai sotto i
    240 s di #196 (anti-blocco), con lo stesso `max_retries=1`."""
    costruzioni = []

    class _AnthropicFinto:
        def __init__(self, **kw):
            costruzioni.append(kw)
            self.messages = None
    monkeypatch.setattr(base, "OpenRouterClient", _AnthropicFinto)

    _MockSpecialist(bb)  # senza client: lo costruisce da se'

    assert len(costruzioni) == 1
    kw = costruzioni[0]
    assert kw["timeout"] >= 3600.0 * base.MAX_TOKENS_SPECIALIST / 128000, kw
    assert kw["timeout"] >= 240.0, kw
    assert kw["max_retries"] == 1, kw
    assert kw["timeout"] == base.TIMEOUT_SPECIALIST_S


def test_il_timeout_e_una_funzione_del_cap():
    """Se un giorno il cap sale ancora, il timeout non puo' restare indietro
    in silenzio: il legame e' una FUNZIONE provata su piu' cap, non due numeri
    da tenere allineati (a 16k «450 fisso» sarebbe indistinguibile: e' il
    pavimento e la formula sugli altri cap a rendere il legame misurabile)."""
    assert base.timeout_specialisti(4000) == 240.0      # pavimento di #196
    assert base.timeout_specialisti(12000) == 337.5     # la stima dell'SDK: 3600 x cap / 128000
    assert base.timeout_specialisti(16000) == 450.0
    assert base.timeout_specialisti(32000) == 900.0
    assert base.TIMEOUT_SPECIALIST_S == base.timeout_specialisti(base.MAX_TOKENS_SPECIALIST)


# ---------------------------------------------------------------- il WARN

def test_la_troncatura_dichiara_cap_output_tokens_testo_e_secondi(bb, capsys):
    """Alla prossima troncatura il log deve dire da solo quanto era pensiero
    (cap, output_tokens = pensiero + testo, char del testo visibile) e quanto
    e' durata la call (la velocita' in tok/s e' cio' che decide il timeout)."""
    testo = "x" * 9578
    client = _FakeClient(
        lambda n, kw: _Resp("max_tokens", [_TextBlock(testo)], _Usage(out=16000)))

    _MockSpecialist(bb, client=client).run(2)

    riga = _riga_warn(capsys.readouterr().out)
    assert "cap %d token" % client.calls[0]["max_tokens"] in riga, riga
    assert "output_tokens=16000" in riga, riga
    assert "9578 char" in riga, riga
    assert "ragionamento" in riga, riga
    assert re.search(r"call di \d+\.\d s", riga), riga


def test_usage_assente_la_troncatura_dichiara_nd_non_zero(bb, capsys):
    """Senza usage il numero e' IGNOTO: n.d., mai 0 (regola 14/07) — e la
    dichiarazione non deve esplodere."""
    client = _FakeClient(
        lambda n, kw: _Resp("max_tokens", [_TextBlock("meta' analisi")], usage=None))

    out = _MockSpecialist(bb, client=client).run(2)

    riga = _riga_warn(capsys.readouterr().out)
    assert "output_tokens=n.d." in riga, riga
    assert "output_tokens=0" not in riga, riga
    assert "meta' analisi" in out


def test_senza_troncatura_nessun_warn(bb, capsys):
    client = _FakeClient(lambda n, kw: _Resp("end_turn", [_TextBlock("REPORT")], _Usage()))

    _MockSpecialist(bb, client=client).run(1)

    assert "TRONCATA" not in capsys.readouterr().out


def test_un_altro_stop_nel_ramo_finale_non_e_una_troncatura(bb, capsys):
    """`stop_sequence` entra nello stesso ramo finale di `max_tokens`: il WARN
    deve restare legato al SOLO max_tokens (verde gia' oggi: e' l'ancora della
    mutazione «la riga esce anche senza troncatura» del banco)."""
    client = _FakeClient(
        lambda n, kw: _Resp("stop_sequence", [_TextBlock("REPORT")], _Usage()))

    _MockSpecialist(bb, client=client).run(1)

    assert "TRONCATA" not in capsys.readouterr().out
