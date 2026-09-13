# -*- coding: utf-8 -*-
"""politics_score legge SOLO mercati aperti e pertinenti (audit run 10/09, memo #54).

Cosa deve essere vero per chi usa il programma:
  - il cruscotto "Eventi & Geopolitica" del memo/PDF non puo' segnare 100% su quattro
    temi perche' il tool ha restituito una partita di calcio, un mercato sulle parole di
    un podcast o un mercato gia' RISOLTO nel 2025: nel memo #54 e' uscito "78/100 critico"
    con "recessione USA 100%" mentre il mercato vero stava al 7%;
  - un mercato risolto (prezzo esattamente 0 o 1, o data di chiusura passata) non e' una
    probabilita' di coda: si scarta e si dichiara;
  - il tema senza un mercato aperto pertinente NON prende un numero preso a caso: esce
    n.d. dichiarato, e il verdetto si calcola sui temi che hanno un mercato.

Tutti i mercati qui sotto sono INVENTATI o ricalcano la forma vera del tool.
"""
import pytest

import bellomberg.agents.specialist_scores as ss
from bellomberg.agents import agent_tools


def _ev(title, markets, end="2099-12-31T00:00:00Z", vol=None):
    return {"type": "event_group", "title": title, "end_date": end, "volume_24h": vol,
            "markets": [{"question": q, "outcomes": ["Yes", "No"], "prices": [str(p), str(1 - p)],
                         "end_date": mend or end, "volume_24h": mvol} for (q, p, mend, mvol) in markets]}


def _risposte(monkeypatch, per_query):
    chiamate = []

    def finto(query, max_results=10):
        chiamate.append(query)
        return {"query": query, "count": len(per_query.get(query, [])), "results": per_query.get(query, [])}

    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events", finto)
    return chiamate


# ------------------------------------------------ il caso vero del memo #54

def test_il_massimo_yes_di_un_mercato_estraneo_o_risolto_non_diventa_la_probabilita(monkeypatch):
    q = ss._POLI_TOPICS["recessione USA"]
    _risposte(monkeypatch, {q: [
        _ev("US recession by end of 2026?", [("US recession by end of 2026?", 0.07, "2099-01-31T00:00:00Z", 900.0)]),
        # partita di calcio con Yes a 0,995 e data futura: pertinenza zero
        _ev("Sevilla FC vs. Valencia CF", [("Will Sevilla FC win on 2099-09-11?", 0.995, None, 4_000_000.0)]),
        # mercato RISOLTO: prezzo esattamente 1 e data passata
        _ev("US recession in 2020?", [("US recession by end of 2020?", 1.0, "2020-12-31T00:00:00Z", 0.0)]),
    ]})
    s = ss.politics_score()
    assert s is not None
    assert s["metrics"]["topics"]["recessione USA"] == pytest.approx(0.07), s["metrics"]
    assert "US recession by end of 2026?" in s["metrics"]["mercati"]["recessione USA"]["question"]


def test_un_mercato_risolto_con_data_futura_ma_prezzo_a_uno_si_scarta(monkeypatch):
    q = ss._POLI_TOPICS["Iran-Israele"]
    _risposte(monkeypatch, {q: [
        _ev("Israel strikes Iran by February 28, 2026?", [("Israel strikes Iran by February 28, 2026?", 1.0, "2099-02-28T00:00:00Z", None)]),
        _ev("Israel strikes Iran by end of 2099?", [("Israel strikes Iran by end of 2099?", 0.31, "2099-12-31T00:00:00Z", 12.0)]),
    ]})
    s = ss.politics_score()
    assert s["metrics"]["topics"]["Iran-Israele"] == pytest.approx(0.31)


def test_senza_mercato_pertinente_il_tema_e_nd_dichiarato_non_un_numero(monkeypatch):
    q_rec = ss._POLI_TOPICS["recessione USA"]
    q_war = ss._POLI_TOPICS["conflitto/guerra"]
    _risposte(monkeypatch, {
        q_rec: [_ev("US recession by end of 2026?", [("US recession by end of 2026?", 0.07, "2099-01-31T00:00:00Z", 1.0)])],
        # per il tema guerra torna solo un mercato di "menzione" gia' risolto
        q_war: [_ev("What will Melania say?", [("Will Melania say \"Conflict\" or \"War\" 3+ times?", 1.0, "2020-03-02T00:00:00Z", None)])],
    })
    s = ss.politics_score()
    assert "conflitto/guerra" not in s["metrics"]["topics"]
    assert "conflitto/guerra" in s["metrics"]["non_calcolabili"], s["metrics"]
    # il verdetto si calcola sui temi con mercato: 1 tema -> max 3 punti
    assert s["max_score"] == 3 and s["score"] == 0
    etichette = [l[0] for l in s["lines"]]
    assert any("conflitto/guerra" in e and "n.d." in e for e in etichette), etichette


def test_fra_piu_mercati_pertinenti_vince_il_piu_scambiato_non_il_piu_alto(monkeypatch):
    q = ss._POLI_TOPICS["Cina-Taiwan/dazi"]
    _risposte(monkeypatch, {q: [
        _ev("China x Taiwan?", [("Will China invade Taiwan by end of 2099?", 0.60, "2099-12-31T00:00:00Z", 50.0)]),
        _ev("China x Taiwan 2?", [("Will China blockade Taiwan by end of 2099?", 0.12, "2099-12-31T00:00:00Z", 900_000.0)]),
    ]})
    s = ss.politics_score()
    assert s["metrics"]["topics"]["Cina-Taiwan/dazi"] == pytest.approx(0.12)


def test_i_mercati_di_menzione_verbale_non_sono_rischio_di_coda(monkeypatch):
    q = ss._POLI_TOPICS["conflitto/guerra"]
    _risposte(monkeypatch, {q: [
        _ev("What will Rutte say on 2099-04-09?", [("Will Mark Rutte say \"Peace\" or \"War\" 3+ times during his speech?", 0.8, "2099-04-09T00:00:00Z", 10.0)]),
        _ev("Russia x Moldova", [("Russia invades Moldova by end of 2099?", 0.22, "2099-12-31T00:00:00Z", 5.0)]),
    ]})
    s = ss.politics_score()
    assert s["metrics"]["topics"]["conflitto/guerra"] == pytest.approx(0.22)


def test_una_tregua_non_e_rischio_di_coda(monkeypatch):
    """La probabilita' di un cessate il fuoco misura la pace: non entra nel tema guerra."""
    q = ss._POLI_TOPICS["conflitto/guerra"]
    _risposte(monkeypatch, {q: [
        _ev("Russia x Ukraine ceasefire", [("Russia x Ukraine ceasefire by end of 2099?", 0.22, "2099-12-31T00:00:00Z", 5.0)]),
    ]})
    s = ss.politics_score()
    assert s is None or "conflitto/guerra" not in s["metrics"]["topics"]


def test_lo_stesso_mercato_non_conta_per_due_temi(monkeypatch):
    """Misurato dal vivo l'11/09: "war 2026" e "iran war 2026" davano entrambi «Will the US
    officially declare war on Iran by December 31, 2026?». Un mercato vale per UN tema."""
    stesso = ("Will the US officially declare war on Iran by end of 2099?", 0.022, "2099-12-31T00:00:00Z", 400.0)
    altro = ("Will Iran attack a Gulf state by end of 2099?", 0.74, "2099-12-31T00:00:00Z", 50.0)
    _risposte(monkeypatch, {ss._POLI_TOPICS["conflitto/guerra"]: [_ev("x", [stesso])],
                            ss._POLI_TOPICS["Iran-Israele"]: [_ev("x", [stesso]), _ev("y", [altro])]})
    s = ss.politics_score()
    assert s["metrics"]["topics"]["conflitto/guerra"] == pytest.approx(0.022)
    assert s["metrics"]["topics"]["Iran-Israele"] == pytest.approx(0.74)
    assert s["metrics"]["mercati"]["Iran-Israele"]["question"].startswith("Will Iran attack")


def test_tool_muto_resta_none_come_prima(monkeypatch):
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events",
                        lambda query, max_results=10: {"error": "Polymarket n.d.: TLS", "count": 0, "results": []})
    assert ss.politics_score() is None
