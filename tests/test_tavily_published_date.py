# -*- coding: utf-8 -*-
"""La ricerca web porta la data di pubblicazione quando la fonte la da' (audit 10/09).

Run 1 del 10/09 (memo #53): con Polymarket irraggiungibile, l'Event Desk ha preso da una
ricerca web "street ~35% di probabilita' di rialzo Fed [src: tavily_search Bloomberg]" e il
Capo l'ha messo in tabella scenari; alle 22:45 il mercato dava 65,5%. Il tool scartava la
`published_date` che Tavily restituisce: nessun desk poteva dire di QUANDO fosse quel 35%.

Trasporto finto; niente rete.
"""
from types import SimpleNamespace as NS

from bellomberg.agents import agent_tools as at


def _post(payload):
    def post(url, json=None, timeout=None):
        return NS(status_code=200, json=lambda: payload, text="")
    return post


def test_la_data_di_pubblicazione_arriva_nel_risultato(monkeypatch):
    monkeypatch.setattr(at, "TAVILY_API_KEY", "prova")
    monkeypatch.setattr(at._req, "post", _post({"answer": "", "results": [
        {"title": "Fed hike odds", "url": "https://esempio.test/a", "content": "35%", "score": 0.9,
         "published_date": "Tue, 26 Aug 2026 12:00:00 GMT"},
        {"title": "senza data", "url": "https://esempio.test/b", "content": "x", "score": 0.5},
    ]}))
    r = at.tool_tavily_search("fed hike odds", max_results=5)
    assert r["results"][0]["published_date"] == "Tue, 26 Aug 2026 12:00:00 GMT"
    assert r["results"][1]["published_date"] is None, "assente = dichiarata assente, non omessa"
    assert "published_date" in (r.get("nota") or ""), r
