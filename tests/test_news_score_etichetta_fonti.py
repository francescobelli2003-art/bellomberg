# -*- coding: utf-8 -*-
"""Il verdetto news dice QUANTE FONTI sono mute, non quanti nomi (audit run 10/09).

Nei memo #53 e #54 il cruscotto diceva "(news: fonti mute 6/6)": erano 6 NOMI del book
su 6 con copertura ridotta, e le fonti mute erano DUE (gnews, thenewsapi) su quattro.
L'Event Desk lo ha ricopiato come "mute 6/6" e il Capo lo ha narrato al PM come
"sei fonti su sei mute". L'etichetta breve deve dire la cosa vera e stare nella
colonna del PDF (Helvetica-Bold 8.5, ~203 pt utili).

Valori e simboli INVENTATI.
"""
import bellomberg.agents.specialist_scores as ss
from bellomberg.agents import agent_tools


PORTAFOGLIO = {"positions": [
    {"ticker": t, "peso_pct": p} for t, p in
    (("ALFA", 20), ("BETA", 15), ("GAMMA", 12), ("DELTA", 10), ("EPSI", 8), ("ZETA", 6), ("ETA", 2))]}


def _search_news_parziale(query, max_results=10):
    return {"query": query, "count": 3,
            "news": [{"title": "notizia %d su %s" % (i, query), "descrizione": ""} for i in range(3)],
            "fonti": {"marketaux": "live", "thenewsapi": "SKIP_BUDGET", "gnews": "SKIP_COOLDOWN",
                      "yfinance": "live"},
            "copertura": "PARZIALE", "fonti_mute": ["gnews", "thenewsapi"],
            "avviso": "COPERTURA DEGRADATA"}


def _senza_polymarket(query, max_results=10):
    return {"query": query, "count": 0, "results": [], "error": "Polymarket n.d. (prova)"}


def test_il_verdetto_conta_le_fonti_mute_sul_totale_delle_fonti(monkeypatch):
    monkeypatch.setattr(agent_tools, "tool_search_news", _search_news_parziale)
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events", _senza_polymarket)
    s = ss.eventdesk_score(PORTAFOGLIO)
    assert s is not None
    assert "(news: 2/4 fonti mute)" in s["verdict"], s["verdict"]
    assert "6/6" not in s["verdict"], s["verdict"]
    m = s["metrics"]["news"]
    assert m["n_fonti_mute"] == 2 and m["n_fonti_candidate"] == 4
    assert m["fonti_mute"] == ["gnews", "thenewsapi"]
    assert m["n_nomi_scoperti"] == 6 and m["n_nomi"] == 6


def test_la_riga_di_dettaglio_nomina_le_fonti_prima_dei_nomi(monkeypatch):
    monkeypatch.setattr(agent_tools, "tool_search_news", _search_news_parziale)
    n = ss.news_score(PORTAFOGLIO)
    riga = [l for l in n["lines"] if l[0] == "Copertura fonti"][0][1]
    assert riga.startswith("PARZIALE - mute: gnews, thenewsapi (2/4 fonti)"), riga
    assert "6/6 nomi" in riga


def test_il_suffisso_sta_nella_colonna_del_pdf_nel_caso_peggiore(monkeypatch):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    monkeypatch.setattr(agent_tools, "tool_search_news", _search_news_parziale)
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events", _senza_polymarket)
    s = ss.eventdesk_score(PORTAFOGLIO)
    peggiore = "EVENTI IN FERMENTO" + s["verdict"][s["verdict"].index(" (news:"):]
    assert stringWidth(peggiore, "Helvetica-Bold", 8.5) <= 203, peggiore


def test_copertura_piena_nessun_suffisso(monkeypatch):
    def pieno(query, max_results=10):
        return {"query": query, "count": 2, "news": [{"title": "x"}, {"title": "y"}],
                "fonti": {"marketaux": "live", "thenewsapi": "live", "gnews": "live", "yfinance": "live"}}
    monkeypatch.setattr(agent_tools, "tool_search_news", pieno)
    monkeypatch.setattr(agent_tools, "tool_get_polymarket_events", _senza_polymarket)
    s = ss.eventdesk_score(PORTAFOGLIO)
    assert "(news:" not in s["verdict"], s["verdict"]
