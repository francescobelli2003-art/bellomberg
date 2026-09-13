# -*- coding: utf-8 -*-
"""Sonda dei modelli PRIMA del Round 0: un modello respinto si scopre subito (audit 10/09).

Run 1 del 10/09: il modello del red team era bloccato da OpenRouter (HTTP 403) e lo si e'
scoperto alle 16:15, quarantacinque minuti dopo l'avvio, a Round 0 e Round 1 gia' pagati.
L'HEALTH-CHECK pre-run pingava solo i tool locali. La sonda chiama ogni slug distinto una
volta con pochi token; l'esito e' dichiarato (OK/KO con la causa) e la run NON si ferma:
il red team resta best-effort, i desk hanno il loro modello.

Client finto; niente rete.
"""
from bellomberg.core import llm_client as lc


class _Resp:
    def __init__(self):
        self.content = []
        self.stop_reason = "end_turn"
        self.usage = None


class _Client:
    def __init__(self, ko):
        self.ko = ko
        self.messages = self
        self.chiamate = []

    def create(self, **kw):
        self.chiamate.append(kw)
        if kw["model"] in self.ko:
            raise RuntimeError(self.ko[kw["model"]])
        return _Resp()


def test_ogni_slug_distinto_e_sondato_una_volta_con_pochi_token():
    cl = _Client(ko={})
    esiti = lc.sonda_modelli(["a/uno", "a/uno", "b/due"], client=cl)
    assert [c["model"] for c in cl.chiamate] == ["a/uno", "b/due"]
    assert all(c["max_tokens"] <= 8 for c in cl.chiamate)
    assert esiti == {"a/uno": {"ok": True, "motivo": None}, "b/due": {"ok": True, "motivo": None}} or \
        all(e["ok"] and e["motivo"] is None for e in esiti.values())


def test_il_modello_respinto_e_ko_con_la_causa_e_gli_altri_restano_ok():
    cl = _Client(ko={"m/bloccato": "HTTP 403: This model requires you to complete the following before use: 18+ age confirmation"})
    esiti = lc.sonda_modelli(["m/bloccato", "a/uno"], client=cl)
    assert esiti["a/uno"]["ok"] is True
    assert esiti["m/bloccato"]["ok"] is False and "403" in esiti["m/bloccato"]["motivo"]


def test_senza_slug_nessuna_chiamata():
    cl = _Client(ko={})
    assert lc.sonda_modelli([], client=cl) == {}
    assert cl.chiamate == []


def test_la_riga_di_log_nomina_ok_e_ko():
    cl = _Client(ko={"m/bloccato": "HTTP 403 gate"})
    esiti = lc.sonda_modelli(["m/bloccato", "a/uno"], client=cl)
    righe = lc.righe_log_sonda(esiti)
    assert any("[OK]" in r and "a/uno" in r for r in righe), righe
    assert any("[KO]" in r and "m/bloccato" in r and "403" in r for r in righe), righe
