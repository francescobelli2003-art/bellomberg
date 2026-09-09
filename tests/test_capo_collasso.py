"""Guardia anti-collasso sul MEMO del Capo (12/08/2026, Fable 5).

Misurato nella run di collaudo 12/08 (run_20260812_105503, memo #50): il Capo
ha risposto con l'ECO della coda del prompt (blocco EDGE SCAN + "Genera il
memo di questa settimana.", out=396 token) e ha chiuso il turno — nessun
errore API, nessun refusal, niente max_tokens. Il memo salvato era un
moncherino di 3.196 char e capo.py:418 intercettava solo il testo VUOTO:
un'eco da 396 token passava zitta fino al PDF del PM.

Stessa classe (e stessa cura) dell'annuncio-senza-tool dei desk (fix 03/08,
tests/test_resilienza_r0.py): testo sotto soglia -> UN retry col nudge; se
ricade -> marcatore dichiarato nel testo E nel log. Fuori perimetro
DICHIARATO: refusal (ha gia' la sua dichiarazione, non si discute coi
safeguard) e max_tokens (un memo lungo troncato non e' un collasso).

Client Anthropic FINTO scriptabile per-chiamata (idioma di
test_capo_streaming, esteso): zero rete, zero DB.
"""
from types import SimpleNamespace

import pytest

from bellomberg.agents import capo
from bellomberg.valuation import cef_lookthrough
from bellomberg.core import current_facts
from bellomberg.portfolio import signal_engine

MEMO_VERO = ("## SINTESI ESECUTIVA\nIl comitato conferma il posizionamento. " +
             "Analisi e numeri dai tool, buchi dichiarati n.d. " * 80)  # ~4.400 char
ECO_PROMPT = ("=== EDGE SCAN (SEGNALI OGGETTIVI) ===\n[100] ALFA Vol Risk "
              "Premium (bearish): +17.9pt\n\nGenera il memo di questa settimana.")


def _msg(testo, stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=testo)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=64278, output_tokens=396),
    )


class _StreamFinto:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._msg


class _MessagesScriptati:
    """stream() scriptabile: script(n_chiamata, kwargs) -> messaggio finto."""

    def __init__(self, script, chiamate):
        self._script = script
        self.chiamate = chiamate

    def create(self, **kwargs):
        raise AssertionError("il Capo deve restare in STREAMING (voce 1, 01/08)")

    def stream(self, **kwargs):
        self.chiamate.append(kwargs)
        return _StreamFinto(self._script(len(self.chiamate), kwargs))


def _prepara(monkeypatch, script):
    chiamate = []

    class _AnthropicFinto:
        def __init__(self, **kwargs):
            self.messages = _MessagesScriptati(script, chiamate)

    monkeypatch.setattr(capo, "OpenRouterClient", _AnthropicFinto)
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "(fatti finti)")
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda: "")
    monkeypatch.setattr(cef_lookthrough, "capo_block", lambda: "")
    monkeypatch.setattr(signal_engine, "scan_portfolio", lambda **k: {"signals": []})
    return chiamate


def _bb():
    return SimpleNamespace(data={"macro": {2: "report macro finto"}})


def test_eco_corta_riceve_un_retry_col_nudge(monkeypatch, capsys):
    """Il caso del memo #50: eco del prompt alla prima chiamata, memo vero
    alla seconda — il round si salva e il retry e' dichiarato a log."""
    chiamate = _prepara(monkeypatch, lambda n, kw: _msg(ECO_PROMPT) if n == 1
                        else _msg(MEMO_VERO))
    final, usage = capo.run_capo(_bb())

    assert "SINTESI ESECUTIVA" in final
    assert "[MEMO COLLASSATO" not in final
    assert len(chiamate) == 2
    # il nudge viaggia nella seconda chiamata: assistant(eco) + user(nudge)
    msgs2 = chiamate[1]["messages"]
    assert msgs2[-2]["role"] == "assistant"
    assert msgs2[-1]["role"] == "user"
    assert "memo" in str(msgs2[-1]["content"]).lower()
    assert "COLLASSATO" in capsys.readouterr().out


def test_collasso_doppio_esce_marcato(monkeypatch, capsys):
    """Se anche il retry fa eco: marcatore dichiarato in testa al memo e nel
    log — mai piu' un moncherino zitto fino al PDF del PM."""
    chiamate = _prepara(monkeypatch, lambda n, kw: _msg(ECO_PROMPT))
    final, usage = capo.run_capo(_bb())

    assert "[MEMO COLLASSATO" in final
    assert ECO_PROMPT.split("\n")[0] in final  # l'eco resta, ma marcata
    assert len(chiamate) == 2  # UN solo retry, mai un loop
    assert "MEMO COLLASSATO" in capsys.readouterr().out


def test_memo_normale_una_sola_chiamata(monkeypatch):
    """Controprova: un memo vero non paga nessun giro in piu'."""
    chiamate = _prepara(monkeypatch, lambda n, kw: _msg(MEMO_VERO))
    final, usage = capo.run_capo(_bb())

    assert "SINTESI ESECUTIVA" in final
    assert "[MEMO COLLASSATO" not in final
    assert len(chiamate) == 1


def test_refusal_fuori_perimetro_dichiarato(monkeypatch):
    """Un refusal e' corto ma ha GIA' la sua dichiarazione: niente retry
    (non si discute coi safeguard), una sola chiamata come oggi."""
    def script(n, kw):
        m = _msg("", stop_reason="refusal")
        m.content = []
        return m

    chiamate = _prepara(monkeypatch, script)
    final, usage = capo.run_capo(_bb())

    assert len(chiamate) == 1
    assert "[MEMO COLLASSATO" not in final
