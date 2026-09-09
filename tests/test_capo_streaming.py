"""Voci 1 e 5 di MASTER_TODO §9-quattuortrigies (riparate 01/08/2026, Fable 5).

Voce 1 (P1): il memo del Capo si troncava a max_tokens. Misurato in DB: i DUE
memo prodotti su Opus 5 (id 48 del 28/07 e id 49 del 30/07) sono usciti a
capo_tokens_out = 32000 ESATTI col marker [NOTA AUTOMATICA]; i memo su
Opus 4.8 (46, 47) chiudevano a 22.342/20.379. Decisione PM 01/08: streaming +
tetto a 64000 (l'SDK rifiuta le non-streaming che stima sopra ~10 minuti).

Voce 5 (P3): capo.py stampava "Opus 4.8" in testa mentre il modello vero e'
claude-opus-5 — l'etichetta ora deriva da CAPO_MODEL e non puo' piu' invecchiare.

Suite OFFLINE: client Anthropic FINTO (il .create esplode apposta: prova che
il ramo non-streaming e' morto), fonti contestuali stubbate (niente rete/DB).
"""
from types import SimpleNamespace

import pytest

from bellomberg.agents import capo
from bellomberg.core import llm_client
from bellomberg.core import current_facts
from bellomberg.valuation import cef_lookthrough
from bellomberg.portfolio import signal_engine


def _messaggio_finto(stop_reason="end_turn"):
    # 12/08: lunghezza REALISTICA (un weekly vero e' 15k-30k char) — sotto la
    # SOGLIA_COLLASSO_MEMO della guardia anti-eco il finto farebbe scattare il
    # retry e questi test misurerebbero la guardia, non lo streaming.
    testo = "MEMO FINTO DEL CAPO. " + ("Sezione con numeri dai tool. " * 120)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=testo)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1234, output_tokens=567),
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


class _MessagesFinti:
    def __init__(self, msg, chiamate):
        self._msg = msg
        self.chiamate = chiamate

    def create(self, **kwargs):
        raise AssertionError(
            "run_capo ha usato client.messages.create (NON-streaming): "
            "la voce 1 richiede lo streaming")

    def stream(self, **kwargs):
        self.chiamate.append(kwargs)
        return _StreamFinto(self._msg)


def _prepara(monkeypatch, msg):
    """Stub di tutto cio' che tocca rete/DB prima della chiamata, + client finto."""
    chiamate = []

    class _AnthropicFinto:
        def __init__(self, **kwargs):
            self.messages = _MessagesFinti(msg, chiamate)

    monkeypatch.setattr(capo, "OpenRouterClient", _AnthropicFinto)
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "(fatti finti)")
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda: "")
    monkeypatch.setattr(cef_lookthrough, "capo_block", lambda: "")
    monkeypatch.setattr(signal_engine, "scan_portfolio",
                        lambda **k: {"signals": []})
    return chiamate


def _blackboard_finto():
    return SimpleNamespace(data={"macro": {2: "report macro finto"}})


def test_capo_chiama_in_streaming_col_tetto_64k(monkeypatch):
    chiamate = _prepara(monkeypatch, _messaggio_finto())
    final, usage = capo.run_capo(_blackboard_finto())
    assert len(chiamate) == 1
    kw = chiamate[0]
    assert kw["max_tokens"] == 64000, kw["max_tokens"]
    assert kw["model"] == llm_client.modello("capo")
    # il thinking del comitato resta ADAPTIVE (regola CLAUDE.md: com'era)
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["system"], "system prompt del Capo perso nella conversione"
    assert "MEMO FINTO DEL CAPO" in final
    assert usage["output_tokens"] == 567


def test_troncamento_ancora_dichiarato_nel_memo(monkeypatch):
    """La regola 14/07 deve reggere anche dopo il fix: se il memo arriva a
    max_tokens, la nota automatica resta nel testo (col tetto NUOVO)."""
    _prepara(monkeypatch, _messaggio_finto(stop_reason="max_tokens"))
    final, _usage = capo.run_capo(_blackboard_finto())
    assert "[NOTA AUTOMATICA" in final


def test_etichetta_modello_non_stantia(monkeypatch, capsys):
    """Voce 5: la testata del log deriva da CAPO_MODEL, niente piu' 'Opus 4.8'."""
    _prepara(monkeypatch, _messaggio_finto())
    capo.run_capo(_blackboard_finto())
    out = capsys.readouterr().out
    assert llm_client.modello("capo") in out
    assert "Opus 4.8" not in out
