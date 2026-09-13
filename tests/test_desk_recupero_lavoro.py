# -*- coding: utf-8 -*-
"""Il recupero dei desk produce il LAVORO richiesto, e quando non lo produce lo DICHIARA
(prerequisito 3 del mandato PM, 12/09/2026, Fable 5.1 — via A + marcatore).

Contesto: dossier 50 F02 (call troncata a 0 char -> UN ritentativo della stessa call) e
F25 (il Capo legge l'ultimo round con testo vero). La recon del 12/09
(desk_recovery.md) ha misurato che il ritentativo partiva con tool_choice=none: il round
ritentato era un riassunto a memoria, senza get_valuation, senza Excel e senza alcun
segno nel testo che arriva a blackboard, DB, memoria del desk e Capo.

Cosa deve essere vero (una prova per riga):
  T1  nel ritentativo i tool restano DISPONIBILI, il ragionamento resta spento, un nudge
      dichiarato chiude i messaggi del ritentativo, e dopo il ritentativo il ragionamento
      torna acceso per le iterazioni successive; Fundamentals chiama get_valuation
      (tool_log + valuation_results + FV nel testo)
  T2  un round >= 1 chiuso con ZERO tool si apre col marcatore di classe
      «[ROUND N SENZA TOOL: ...]», che arriva in blackboard, a DB e nel log; con i tool
      chiamati NON compare; R0 e i segnaposto ne sono esenti
  T3  la riga di usage_log porta `retry_vuoto` (0/1), distinguibile dal retry 529 a
      parita' di api_calls; l'aggregato per desk nel heartbeat lo porta
  T4  il segnaposto «(2 tentativi ...)» arriva INTERO al Capo (tetto 240) e sopra il tetto
      il taglio e' dichiarato con «[...]»
  T5  il prefisso «[ROUND N SENZA REPORT ...]» porta data, ora e fuso del round scelto,
      letti dall'indice che Blackboard.write registra; senza indice dichiara «orario n.d.»
  T6  il system prompt del Capo porta la dottrina sui due marcatori e il prompt costruito
      da run_capo (client finto) porta prefisso + orario + marcatore SENZA TOOL

ORACOLO (lezione «misure circolari»): il client finto RISPETTA il contratto dell'API —
con tool_choice=none non restituisce MAI tool_use. Un finto che ignorasse tool_choice
farebbe passare T1 anche col codice vecchio.

Client finto (idioma di test_tetto_specialisti_16k), zero rete, zero DB vivo.
"""
import copy
import json
import re
import sys
from types import SimpleNamespace

import pytest

from test_tetto_specialisti_16k import bb, _Resp, _TextBlock, _Usage  # noqa: F401
import bellomberg.agents.specialists.base as base
from bellomberg.agents.specialists.base import Blackboard, Specialist
from bellomberg.agents import capo
from bellomberg.agents.capo import scegli_report_specialisti


# ------------------------------------------------------------------ finti

class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, name, input_):
        self.name = name
        self.input = input_
        self.id = "tu_" + name


class _ClientCheRispettaIlContratto:
    """Come _FakeClient, ma con tool_choice={"type":"none"} NON puo' rendere tool_use:
    e' il contratto dell'API (llm_client._tool_choice_openai -> "none"). Se il copione
    lo violasse, la prova fallirebbe QUI, con la causa, invece di passare per sbaglio.
    Conserva una COPIA dei messaggi di ogni call: base.py muta la stessa lista fra una
    call e l'altra, e senza copia le call sarebbero indistinguibili a posteriori."""

    def __init__(self, script):
        self.calls = []
        self.messaggi_per_call = []
        self._script = script
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self.messaggi_per_call.append(copy.deepcopy(kwargs.get("messages")))
        r = self._script(len(self.calls), kwargs)
        if kwargs.get("tool_choice") == {"type": "none"} and r.stop_reason == "tool_use":
            raise AssertionError("il copione ha reso tool_use con tool_choice=none: "
                                 "il finto non deve mentire sul contratto dell'API")
        return r


class _Err529(Exception):
    status_code = 529


_RIASSUNTO = "RIASSUNTO SENZA LAVORO: in questo turno scrivo a memoria. " + "x" * 900


def _copione_vuoto_poi_lavoro(n, kw):
    """Call 1: max_tokens con 0 char (il caso F02, run 1 memo #53 righe 6895-6908 del log).
    Call 2 (il ritentativo): se i tool sono disponibili -> get_valuation; se sono spenti
    (codice vecchio) -> il modello puo' solo scrivere un riassunto SENZA lavoro.
    Call 3: il report con il FV letto dal tool."""
    if n == 1:
        return _Resp("max_tokens", [], _Usage(out=16000))
    if n == 2:
        if kw.get("tool_choice") == {"type": "none"}:
            return _Resp("end_turn", [_TextBlock(_RIASSUNTO)], _Usage(out=400))
        return _Resp("tool_use", [_ToolUseBlock("get_valuation",
                     {"ticker": "ALFA", "variant_view": "prova di collaudo"})], _Usage(out=200))
    return _Resp("end_turn", [_TextBlock(
        "REPORT R2 - ALFA FV 12.5 EUR [src: get_valuation] " + "y" * 900)], _Usage(out=300))


def _copione_vuoto_poi_riassunto(n, kw):
    """Call 1 vuota; call 2 un testo lungo SENZA tool (il modello poteva, non ha voluto)."""
    if n == 1:
        return _Resp("max_tokens", [], _Usage(out=16000))
    return _Resp("end_turn", [_TextBlock(_RIASSUNTO)], _Usage(out=400))


def _copione_tool_poi_report(n, kw):
    if n == 1:
        return _Resp("tool_use", [_ToolUseBlock("get_valuation", {"ticker": "ALFA"})], _Usage())
    return _Resp("end_turn", [_TextBlock("REPORT CON LAVORO [src: get_valuation] " + "z" * 900)], _Usage())


def _copione_529_poi_report(n, kw):
    if n == 1:
        raise _Err529("overloaded (finto)")
    return _Resp("end_turn", [_TextBlock("REPORT DOPO IL 529 " + "w" * 900)], _Usage())


_TOOL_GET_VALUATION = {"name": "get_valuation", "description": "mock",
                       "input_schema": {"type": "object", "properties": {}}}


def _dispatch_finto(name, args=None, **kw):
    """Il dispatcher finto accetta `caller`/`prepared_bundle` (base._execute_meta_tool) e
    rende un payload nella forma che finisce in blackboard.valuation_results."""
    if name == "get_valuation":
        return {"ok": True, "data": {
            "ticker": "ALFA", "snapshot_id": "snap-prova", "generation_id": "gen-prova",
            "valuation_usability": {"usable": True}, "fair_value_final": 12.5,
            "currency": "EUR", "valuation_decision": {"method_id": "operating_fcff"}}}
    return {"ok": True, "mock": name}


class _MockFundamentals(Specialist):
    name = "fundamentals"
    role = "mock"
    system_prompt = "Sei un mock di Fundamentals per il collaudo offline."
    tools_used = ["get_valuation"]


class _DbFinto:
    """Registra cio' che Blackboard.write persiste: e' il testo che la memoria del desk
    rileggera' nelle run successive."""

    def __init__(self):
        self.salvati = []

    def save_specialist_report(self, memo_id, specialist, round_n, content):
        self.salvati.append((memo_id, specialist, round_n, content))


@pytest.fixture
def bb_fund(bb):
    """Estende la fixture `bb` (chat_tools e current_facts gia' stubbati): registro con
    get_valuation, dispatcher che accetta i kwargs veri, research_block con la firma
    vera (base.run la chiama con sector_bundles=/decision_links=)."""
    ct = sys.modules["bellomberg.agents.chat_tools"]
    ct.get_tools_for_agent = lambda name: [dict(_TOOL_GET_VALUATION)]
    ct.TOOL_DEFINITIONS = [dict(_TOOL_GET_VALUATION)]
    ct.dispatch = _dispatch_finto
    cf = sys.modules["bellomberg.core.current_facts"]
    cf.research_block = lambda **k: ""
    return bb


MARCATORE_SENZA_TOOL_R2 = ("[ROUND 2 SENZA TOOL: nessuna chiamata tool in questo round; "
                           "i numeri non sono verificati con i tool]")


# ------------------------------------------------------------ T1: il lavoro

def test_T1_nel_ritentativo_fundamentals_puo_chiamare_get_valuation(bb_fund):
    client = _ClientCheRispettaIlContratto(_copione_vuoto_poi_lavoro)
    out = _MockFundamentals(bb_fund, client=client).run(2)

    # il ritentativo e' la seconda call: ragionamento spento MA tool disponibili (via A)
    assert client.calls[1]["thinking"] == {"type": "disabled"}
    assert client.calls[1].get("tool_choice") != {"type": "none"}, (
        "il ritentativo spegne i tool: il round ritentato e' un riassunto a memoria")
    assert len(client.calls) == 3, [c.get("tool_choice") for c in client.calls]
    # il lavoro c'e' stato: get_valuation nel tool_log del round ritentato...
    voci = [v for v in bb_fund.tool_log
            if v["tool"] == "get_valuation" and v["specialist"] == "fundamentals" and v["round"] == 2]
    assert voci, bb_fund.tool_log
    # ...il FV nel registro che il Capo (valuation_results_block) e l'email leggono...
    assert bb_fund.valuation_results.get("ALFA", {}).get("fair_value_final") == 12.5
    # ...e nel testo del report (quello che va in blackboard, DB e memo)
    assert "[src: get_valuation]" in out
    assert "RIASSUNTO SENZA LAVORO" not in out
    assert "SENZA TOOL" not in out, "con il lavoro fatto il marcatore sarebbe un falso allarme"


def test_T1_il_ritentativo_chiude_i_messaggi_con_un_nudge_dichiarato(bb_fund):
    """Il modello del ritentativo deve SAPERE che il primo tentativo non ha prodotto testo
    e che i tool restano disponibili: oggi rimanda gli stessi messaggi senza una parola
    (dossier 50 F02, domanda 1). La prima call NON porta il nudge."""
    client = _ClientCheRispettaIlContratto(_copione_vuoto_poi_lavoro)
    _MockFundamentals(bb_fund, client=client).run(2)

    def _ultimo_user(msgs):
        m = [x for x in msgs if x.get("role") == "user"][-1]
        c = m.get("content")
        if isinstance(c, list):
            return " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
        return str(c)

    prima = _ultimo_user(client.messaggi_per_call[0])
    ritentativo = _ultimo_user(client.messaggi_per_call[1])
    assert "non ha prodotto testo" not in prima
    assert "non ha prodotto testo" in ritentativo, ritentativo[-400:]
    assert "usa i tool" in ritentativo.lower(), ritentativo[-400:]


def test_T1_dopo_il_ritentativo_il_ragionamento_torna_acceso(bb_fund):
    """Recon 12/09 par. 2.3: `_thinking` restava `disabled` per il resto del round. La
    terza call (il report dopo il tool) deve tornare al valore di prima del ritentativo."""
    client = _ClientCheRispettaIlContratto(_copione_vuoto_poi_lavoro)
    _MockFundamentals(bb_fund, client=client).run(2)
    assert client.calls[0]["thinking"] == {"type": "adaptive"}
    assert client.calls[1]["thinking"] == {"type": "disabled"}
    assert client.calls[2]["thinking"] == {"type": "adaptive"}, client.calls[2]["thinking"]


# ---------------------------------------------- T2: zero tool = dichiarato

def test_T2_un_round_chiuso_con_zero_tool_lo_dichiara_in_testa_al_report(bb_fund, capsys):
    """Se a fine round _tool_calls_round == 0 il testo lo dice, come gia' fanno
    [REPORT FORZATO ...] e [COLLASSO ...]: il Capo, il DB e la memoria del desk lo
    leggono. Anche il log (lezione 01/08: un marcatore solo nel DB e' invisibile a chi
    legge il log)."""
    db = _DbFinto()
    bb_fund.memory_db = db
    bb_fund.memo_id = 7
    client = _ClientCheRispettaIlContratto(_copione_vuoto_poi_riassunto)
    out = _MockFundamentals(bb_fund, client=client).run(2)

    assert out.startswith(MARCATORE_SENZA_TOOL_R2), out[:250]
    assert "RIASSUNTO SENZA LAVORO" in out                      # il testo resta, dichiarato
    assert bb_fund.data["fundamentals"][2].startswith(MARCATORE_SENZA_TOOL_R2)
    # a DB (memoria del desk delle run successive): il round 2 con testo vero si persiste
    assert db.salvati and db.salvati[-1][3].startswith(MARCATORE_SENZA_TOOL_R2), db.salvati
    assert "ROUND 2 SENZA TOOL" in capsys.readouterr().out


def test_T2_con_i_tool_chiamati_nessun_marcatore(bb_fund):
    client = _ClientCheRispettaIlContratto(_copione_tool_poi_report)
    out = _MockFundamentals(bb_fund, client=client).run(2)
    assert "SENZA TOOL" not in out, out[:250]


def test_T2_il_round_0_e_i_segnaposto_non_portano_il_marcatore(bb_fund):
    """R0 e' ricognizione (fuori perimetro, come per il collasso); un segnaposto «No output
    produced» non ha numeri da verificare e il marcatore in testa lo travestirebbe da
    report agli occhi di _e_segnaposto (predicato sui primi 120 char)."""
    client = _ClientCheRispettaIlContratto(lambda n, kw: _Resp("end_turn", [_TextBlock(_RIASSUNTO)], _Usage()))
    out0 = _MockFundamentals(bb_fund, client=client).run(0)
    assert "SENZA TOOL" not in out0, out0[:250]

    client = _ClientCheRispettaIlContratto(lambda n, kw: _Resp("max_tokens", [], _Usage(out=16000)))
    out2 = _MockFundamentals(bb_fund, client=client).run(2)
    assert "No output produced in round 2" in out2 and "2 tentativi" in out2
    assert "SENZA TOOL" not in out2, out2
    assert capo._e_segnaposto(out2), "il segnaposto deve restare riconoscibile dal Capo"


# --------------------------------------------- T3: il registro lo distingue

def test_T3_il_registro_usage_distingue_il_ritentativo_dal_retry_529(bb_fund, monkeypatch):
    """`api_calls = iteration + _retry_529 + _retry_vuoto`: una riga con api_calls=2 era
    identica per un 529 e per un ritentativo a 0 char. Il campo dedicato le separa, e
    l'aggregato per desk che va nel heartbeat (Agents Live) lo porta."""
    monkeypatch.setattr(base, "RETRY_529_BACKOFF_S", (0.0, 0.0))   # niente pause vere nel test

    client = _ClientCheRispettaIlContratto(_copione_vuoto_poi_riassunto)
    _MockFundamentals(bb_fund, client=client).run(2)
    riga = [u for u in bb_fund.usage_log if u["agent"] == "fundamentals" and u["round"] == 2][-1]
    assert riga["api_calls"] == 2, riga
    assert riga.get("retry_vuoto") == 1, riga

    client = _ClientCheRispettaIlContratto(_copione_529_poi_report)
    _MockFundamentals(bb_fund, client=client).run(1)
    riga529 = [u for u in bb_fund.usage_log if u["agent"] == "fundamentals" and u["round"] == 1][-1]
    assert riga529["api_calls"] == 2, riga529
    assert riga529.get("retry_vuoto") == 0, riga529

    with open(Blackboard.HEARTBEAT_PATH, encoding="utf-8") as f:
        hb = json.load(f)
    assert hb["usage_by_specialist"]["fundamentals"]["retry_vuoto"] == 1, hb["usage_by_specialist"]


# ------------------------------------ T4: il segnaposto arriva intero al Capo

SEGNAPOSTO_DUE_TENTATIVI = ("[fundamentals] No output produced in round 2 (2 tentativi: anche il "
                            "ritentativo senza ragionamento e' uscito con 0 char di testo)")


def test_T4_il_segnaposto_a_due_tentativi_arriva_intero_al_capo():
    """Misurato il 12/09: il segnaposto e' lungo 122-129 char e il prefisso lo tagliava a
    120 SENZA segno, con la parentesi che chiudeva subito dopo (lezione «il taglio che
    richiude il delimitatore»)."""
    assert 120 < len(SEGNAPOSTO_DUE_TENTATIVI) <= capo.SEGNAPOSTO_PREFISSO_MAX
    r = scegli_report_specialisti({"fundamentals": {1: "REPORT R1 VERO " + "w" * 200, 2: SEGNAPOSTO_DUE_TENTATIVI}})
    testa = r["fundamentals"]["report"].split("\n\n")[0]
    assert r["fundamentals"]["round"] == 1
    assert "0 char di testo)" in testa, testa
    assert "[...]" not in testa


def test_T4_sopra_il_tetto_il_taglio_e_dichiarato():
    lungo = "[ERROR options round 2]: HTTP 500 " + "dettaglio " * 60
    assert len(lungo) > capo.SEGNAPOSTO_PREFISSO_MAX
    r = scegli_report_specialisti({"options": {1: "REPORT R1", 2: lungo}})
    testa = r["options"]["report"].split("\n\n")[0]
    assert "[...]" in testa, testa
    assert lungo[:capo.SEGNAPOSTO_PREFISSO_MAX] in testa
    # stesso tetto e stessa dichiarazione nel ramo [NO REPORT]
    r2 = scegli_report_specialisti({"options": {1: lungo}})["options"]
    assert r2["no_report"] is True and "[...]" in r2["report"], r2["report"]


# --------------------------------- T5: data/ora del report scelto

def test_T5_il_capo_riceve_data_ora_e_fuso_del_report_scelto():
    data = {"quant": {1: "REPORT R1 VERO", 2: "[quant] No output produced in round 2"}}
    orari = {"quant": {1: "2026-09-10T16:10:28+02:00", 2: "2026-09-10T16:25:00+02:00"}}
    r = scegli_report_specialisti(data, orari=orari)["quant"]
    testa = r["report"].split("\n\n")[0]
    assert "Round 1" in testa and "2026-09-10" in testa and "16:10" in testa, testa
    assert "UTC+02:00" in testa, testa


def test_T5_senza_indice_o_senza_fuso_lo_dichiara():
    data = {"quant": {1: "REPORT R1 VERO", 2: "[quant] No output produced in round 2"}}
    testa = scegli_report_specialisti(data)["quant"]["report"].split("\n\n")[0]
    assert "orario n.d." in testa, testa                       # regenerate_memo: nessun indice
    # chiavi stringa (JSON) nell'indice e ISO senza offset: orario si', fuso dichiarato n.d.
    orari = {"quant": {"1": "2026-09-10T16:10:28"}}
    testa = scegli_report_specialisti(data, orari=orari)["quant"]["report"].split("\n\n")[0]
    assert "16:10" in testa and "fuso n.d." in testa, testa


def test_T5_blackboard_write_registra_l_orario_del_round_col_fuso(bb):
    bb.write("quant", 1, "REPORT R1")
    iso = bb.orari_report["quant"][1]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", iso), iso


# ------------------------------- T6: il prompt del Capo (run_capo + client finto)

class _StreamFinto:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._msg


def _prepara_capo(monkeypatch):
    """Idioma di test_red_team_assente_dichiarato: client finto in streaming + contorno
    stubbato. Ritorna le chiamate catturate (system e messages)."""
    from bellomberg.valuation import cef_lookthrough
    from bellomberg.core import current_facts
    from bellomberg.portfolio import signal_engine
    memo = ("## SINTESI ESECUTIVA\nIl comitato conferma il posizionamento. "
            + "Analisi e numeri dai tool, buchi dichiarati n.d. " * 80)
    chiamate = []

    class _Messages:
        def create(self, **kw):
            raise AssertionError("il Capo deve restare in STREAMING (voce 1, 01/08)")

        def stream(self, **kw):
            chiamate.append(kw)
            return _StreamFinto(SimpleNamespace(
                content=[SimpleNamespace(type="text", text=memo)],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=100, output_tokens=100)))

    class _ClientFinto:
        def __init__(self, **kw):
            self.messages = _Messages()

    monkeypatch.setattr(capo, "OpenRouterClient", _ClientFinto)
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "(fatti finti)")
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda: "")
    monkeypatch.setattr(cef_lookthrough, "capo_block", lambda: "")
    monkeypatch.setattr(signal_engine, "scan_portfolio", lambda **k: {"signals": []})
    return chiamate


def _system_text(chiamata):
    s = chiamata["system"]
    if isinstance(s, list):
        return " ".join(b.get("text", "") for b in s if isinstance(b, dict))
    return str(s)


def test_T6_il_system_prompt_del_capo_porta_la_dottrina_sui_due_marcatori(monkeypatch):
    """Accanto a DOMINI SCOPERTI (che scatta solo su [NO REPORT]): cosa fare con un round
    perso e con un round senza tool — dichiararlo nel memo, dire su quale round ci si
    basa, confidence non sopra MEDIA sulle proposte nuove di quel desk."""
    chiamate = _prepara_capo(monkeypatch)
    capo.run_capo(SimpleNamespace(data={"macro": {1: "report macro finto"}}))
    system = _system_text(chiamate[0])
    assert "[ROUND N SENZA REPORT" in system and "[ROUND N SENZA TOOL" in system
    assert "non sopra MEDIA" in system
    assert "DOMINI SCOPERTI" in system                         # la regola accanto a cui vive


def test_T6_il_prompt_costruito_da_run_capo_porta_prefisso_orario_e_marcatore(monkeypatch):
    chiamate = _prepara_capo(monkeypatch)
    blackboard = SimpleNamespace(
        data={"quant": {1: "REPORT R1 VERO DI QUANT " + "q" * 300, 2: SEGNAPOSTO_DUE_TENTATIVI.replace("fundamentals", "quant")},
              "fundamentals": {2: MARCATORE_SENZA_TOOL_R2 + "\n\nREPORT R2 DI FUNDAMENTALS " + "f" * 300}},
        orari_report={"quant": {1: "2026-09-10T16:10:28+02:00", 2: "2026-09-10T16:25:00+02:00"}})
    capo.run_capo(blackboard)
    um = chiamate[0]["messages"][0]["content"]
    assert "--- QUANT (Round 1) ---" in um
    assert "[ROUND 2 SENZA REPORT: [quant] No output produced in round 2 (2 tentativi" in um
    assert "0 char di testo)" in um                            # il segnaposto INTERO
    assert "2026-09-10" in um and "16:10" in um and "UTC+02:00" in um
    assert MARCATORE_SENZA_TOOL_R2 in um                       # il marcatore del desk passa al Capo
