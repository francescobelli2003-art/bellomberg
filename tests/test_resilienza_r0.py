"""Fix 1 di §9-sextrigies (ok PM 03/08, Fable 5): resilienza R0/R1.

Due guasti misurati nel changelog (63):
- V7 03/08: fundamentals R0 MORTO alla prima chiamata su HTTP 529 (overloaded)
  senza alcun retry — un errore transiente ha buttato l'intero round.
- V6 28/07 (voce 4 §9-quattuortrigies): eventdesk R0 ha chiuso il turno con
  113 char di ANNUNCIO ("Avvio la raccolta...") e 0 tool, e il loop l'ha
  promosso a report finale senza fiatare.

Cura approvata: (a) retry dichiarato e limitato sul solo 529; (b) in R0/R1,
end_turn alla prima uscita con 0 tool e testo sotto soglia -> UN retry con
nudge esplicito; se ricade -> marcatore dichiarato nel testo E nel log.

Client Anthropic FINTO scriptabile (idioma di test_run_robustness): zero rete,
zero API, zero DB.
"""
import sys
import types

import pytest

from bellomberg.core import llm_pricing
import bellomberg.agents.specialists.base as base_mod
from bellomberg.agents.specialists.base import Blackboard, Specialist


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


class _Err529(Exception):
    """Doppione di anthropic OverloadedError: basta status_code."""
    status_code = 529


class _Err400(Exception):
    status_code = 400


class _FakeClient:
    """client.messages.create scriptabile: script(n_chiamata, kwargs) -> _Resp
    (o raise)."""

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


ANNUNCIO = "Avvio la raccolta Round 0 sui dati di posizionamento. Chiamate in parallelo dove possibile."
REPORT_VERO = "Analisi mock completa con i dati raccolti dai tool."


@pytest.fixture
def bb(tmp_path, monkeypatch):
    monkeypatch.setattr(Blackboard, "HEARTBEAT_PATH",
                        str(tmp_path / "current_run.json"))
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur",
                        lambda: (0.9, "fallback"))
    llm_pricing.reset_fx_memo()
    cf = types.ModuleType("current_facts")
    cf.current_facts_block = lambda: ""
    cf.favorites_block = lambda: ""
    cf.pm_theses_block = lambda: ""
    cf.research_block = lambda: ""
    monkeypatch.setitem(sys.modules, 'bellomberg.core.current_facts', cf)
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", cf, raising=False)
    ct = types.ModuleType("chat_tools")
    _tool = {"name": "get_portfolio_live", "description": "mock",
             "input_schema": {"type": "object", "properties": {}}}
    ct.get_tools_for_agent = lambda name: [dict(_tool)]
    ct.TOOL_DEFINITIONS = [dict(_tool)]
    ct.dispatch = lambda name, args=None: {"ok": True, "mock": name}
    monkeypatch.setitem(sys.modules, 'bellomberg.agents.chat_tools', ct)
    import bellomberg.agents
    monkeypatch.setattr(bellomberg.agents, "chat_tools", ct, raising=False)
    # pause a zero: il collaudo non deve dormire i backoff veri
    # (raising=False: prima della cura la costante non esiste e il rosso deve
    # venire dal COMPORTAMENTO, non dal setup della fixture)
    monkeypatch.setattr(base_mod, "RETRY_529_BACKOFF_S", (0.0, 0.0), raising=False)
    board = Blackboard()  # memory_db=None: nessuna scrittura DB
    yield board
    llm_pricing.reset_fx_memo()


# ============================================================
# (a) retry sul 529
# ============================================================
def test_529_transiente_si_riprende_col_retry(bb, capsys):
    """Il caso V7: 529 alla prima chiamata. Con la cura il round SOPRAVVIVE
    e il desk ripreso lavora normalmente (giro tool + report)."""
    def script(n, kwargs):
        if n == 1:
            raise _Err529("Error code: 529 - overloaded_error")
        return _tool_resp() if n == 2 else _text_resp(REPORT_VERO)

    client = _FakeClient(script)
    out = _MockSpecialist(bb, client=client).run(1)

    assert out == REPORT_VERO
    assert "[ERROR" not in out and "[COLLASSO" not in out
    # 1 fallita (529) + 1 tool + 1 report
    assert len(client.calls) == 3
    # il retry e' DICHIARATO a log (mai un recupero zitto)
    assert "529" in capsys.readouterr().out
    # e le chiamate extra sono CONTATE nell'usage (misura, non stima):
    # 2 iterazioni riuscite + 1 tentativo 529
    assert bb.usage_log[-1]["api_calls"] == 3


def test_529_esauriti_resta_errore_dichiarato(bb):
    """Retry LIMITATI: 529 anche dopo i backoff -> errore dichiarato, non loop."""
    def script(n, kwargs):
        raise _Err529("Error code: 529 - overloaded_error")

    client = _FakeClient(script)
    out = _MockSpecialist(bb, client=client).run(1)

    assert out.startswith("[ERROR quant round 1]")
    # 1 chiamata + i soli tentativi del backoff (2 nel collaudo)
    assert len(client.calls) == 1 + len(base_mod.RETRY_529_BACKOFF_S)


def test_errore_non_529_resta_immediato(bb):
    """Controprova: un 400 (credito, input rotto) NON si ritenta."""
    def script(n, kwargs):
        raise _Err400("Error code: 400 - credit balance too low")

    client = _FakeClient(script)
    out = _MockSpecialist(bb, client=client).run(1)

    assert out.startswith("[ERROR quant round 1]")
    assert len(client.calls) == 1


# ============================================================
# (b) guardia end_turn-senza-tool (annuncio promosso a report)
# ============================================================
def test_annuncio_senza_tool_riceve_un_retry_col_nudge(bb):
    """Il caso V6 eventdesk: end_turn subito, 0 tool, testo-annuncio. Con la
    cura: UN retry con nudge esplicito, e il round produce il report vero."""
    def script(n, kwargs):
        if n == 1:
            return _text_resp(ANNUNCIO)
        if n == 2:
            return _tool_resp()
        return _text_resp(REPORT_VERO)

    client = _FakeClient(script)
    out = _MockSpecialist(bb, client=client).run(1)

    assert out == REPORT_VERO
    assert len(client.calls) == 3
    # il nudge viaggia come user message DOPO l'annuncio dell'assistant
    msgs2 = client.calls[1]["messages"]
    assert msgs2[-1]["role"] == "user"
    assert "tool" in str(msgs2[-1]["content"]).lower()
    assert msgs2[-2]["role"] == "assistant"


def test_collasso_dopo_retry_esce_col_marcatore(bb, capsys):
    """Se anche il retry annuncia invece di lavorare: marcatore dichiarato
    nel TESTO e nel LOG — mai piu' un 'done (113 chars)' zitto."""
    def script(n, kwargs):
        return _text_resp(ANNUNCIO)

    client = _FakeClient(script)
    out = _MockSpecialist(bb, client=client).run(1)

    assert out.startswith("[COLLASSO ANNUNCIO-SENZA-TOOL")
    assert ANNUNCIO in out
    assert len(client.calls) == 2
    assert "COLLASSO ANNUNCIO-SENZA-TOOL" in capsys.readouterr().out


def test_report_corto_con_tool_a_monte_non_scatta(bb):
    """Controprova: un report corto DOPO lavoro tool e' legittimo."""
    def script(n, kwargs):
        return _tool_resp() if n == 1 else _text_resp("Sintesi breve: n.d. sui dati X.")

    client = _FakeClient(script)
    out = _MockSpecialist(bb, client=client).run(1)

    assert out == "Sintesi breve: n.d. sui dati X."
    assert "[COLLASSO" not in out
    assert len(client.calls) == 2


def test_round_2_fuori_perimetro_dichiarato(bb):
    """La guardia del COLLASSO vale per R0/R1 (dov'e' stato misurato): in R2 non
    scatta, non c'e' nudge e la call resta UNA — perimetro dichiarato.

    Aggiornato il 12/09 (prerequisito 3 del mandato PM): un round >= 1 chiuso con
    ZERO chiamate tool ora lo DICHIARA in testa, anche in R2. Quindi qui si pretende
    di piu' di prima, non di meno: (a) il testo del modello arriva INTERO, (b) la
    dichiarazione c'e' e nomina il round giusto, (c) non e' il marcatore del
    collasso (quello resta fuori perimetro in R2), (d) la call resta una sola."""
    def script(n, kwargs):
        return _text_resp(ANNUNCIO)

    client = _FakeClient(script)
    out = _MockSpecialist(bb, client=client).run(2)

    assert ANNUNCIO in out
    assert out.startswith("[ROUND 2 SENZA TOOL")
    assert "[COLLASSO" not in out
    assert len(client.calls) == 1
