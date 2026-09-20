# -*- coding: utf-8 -*-
"""Troncatura a ZERO char: UN ritentativo dichiarato senza ragionamento (audit run 10/09).

Run 1 del 10/09 (memo #53): quattro call su z-ai/glm-5.3 sono uscite con
stop_reason=max_tokens, output_tokens=16000 e 0 char di testo visibile — tutto il tetto
speso in ragionamento, nessun report — e il round e' morto in "No output produced":
Fundamentals senza R1 e R2, Quant e Options senza R2, Capo che lo dichiara al PM.
La troncatura CON testo resta com'era (dichiarata, nessun ritentativo): il caso da curare
e' solo quello in cui non c'e' NIENTE da leggere.

Cosa deve essere vero:
  - stessa call (stessi messaggi + nudge dichiarato in coda), ragionamento spento, UNA volta sola;
  - i TOOL RESTANO DISPONIBILI nel ritentativo (12/09, via A del mandato PM: con
    tool_choice=none il round ritentato era un riassunto a memoria, senza get_valuation e
    senza Excel — recon desk_recovery.md par. 2.1; il lavoro e' provato in
    tests/test_desk_recupero_lavoro.py);
  - il ritentativo si vede nel log e nel conto (api_calls e token sommati);
  - se anche il ritentativo e' vuoto resta "No output produced", dichiarando i 2 tentativi.

Client finto, zero rete (idioma di test_tetto_specialisti_16k).
"""
import pytest
from test_tetto_specialisti_16k import bb, _FakeClient, _Resp, _TextBlock, _Usage, _MockSpecialist  # noqa: F401


def _vuoto_poi_report(n, kw):
    if n == 1:
        return _Resp("max_tokens", [], _Usage(out=16000))
    return _Resp("end_turn", [_TextBlock("REPORT DOPO IL RITENTATIVO")], _Usage(out=300))


@pytest.mark.parametrize("model,thinking", [
    ("google/gemini-3.8-flash", {"type": "adaptive"}),
    ("meta/muse-spark-1.3", {"type": "effort", "effort": "max"}),
])
def test_troncatura_a_zero_char_ritenta_una_volta_senza_ragionamento(bb, capsys, monkeypatch, model, thinking):
    monkeypatch.setenv("CONSIGLIERE_QUANT_MODEL", model)
    client = _FakeClient(_vuoto_poi_report)
    out = _MockSpecialist(bb, client=client).run(2)
    assert "REPORT DOPO IL RITENTATIVO" in out
    assert "No output produced" not in out
    assert len(client.calls) == 2, [c.get("thinking") for c in client.calls]
    assert client.calls[0]["thinking"] == thinking
    assert client.calls[1]["thinking"] == {"type": "disabled"}
    # 12/09: fino a f710bd3 qui si asseriva `== {"type": "none"}`, cioe' si FISSAVA che il
    # ritentativo non potesse chiamare tool. Verso invertito con la via A del mandato:
    # i tool restano disponibili (il tool_choice none resta solo sull'ultima iterazione,
    # regola del report garantito, provata in test_run_robustness).
    assert client.calls[1].get("tool_choice") != {"type": "none"}, client.calls[1].get("tool_choice")
    log = capsys.readouterr().out
    assert "RITENTO" in log and "0 char" in log, log
    assert "tool disponibili" in log, log


def test_il_ritentativo_entra_nel_conto_della_run(bb):
    client = _FakeClient(_vuoto_poi_report)
    _MockSpecialist(bb, client=client).run(2)
    riga = [u for u in bb.usage_log if u.get("agent") == "quant" and u.get("round") == 2][-1]
    assert riga["api_calls"] == 2, riga
    assert riga["out"] == 16300, riga


def test_ritentativo_anch_esso_vuoto_dichiara_i_due_tentativi(bb):
    client = _FakeClient(lambda n, kw: _Resp("max_tokens", [], _Usage(out=16000)))
    out = _MockSpecialist(bb, client=client).run(2)
    assert len(client.calls) == 2, "il ritentativo e' UNO: mai un terzo giro"
    assert "No output produced in round 2" in out
    assert "2 tentativi" in out, out


def test_troncatura_con_testo_non_ritenta(bb):
    client = _FakeClient(lambda n, kw: _Resp("max_tokens", [_TextBlock("meta' analisi")], _Usage(out=16000)))
    out = _MockSpecialist(bb, client=client).run(2)
    assert len(client.calls) == 1
    assert "meta' analisi" in out
