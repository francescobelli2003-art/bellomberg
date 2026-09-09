"""Fotografia del mandato comune al system reale e all'evento SSE meta."""
import asyncio
import json
from types import SimpleNamespace

import pytest

import bellomberg.agents.chat_engine as chat
import bellomberg.core.mandato_pm as mp


class Fermati(BaseException):
    pass


@pytest.fixture
def cattura(tmp_path, monkeypatch):
    from bellomberg.core import current_facts
    path = tmp_path / "mandato_pm.json"
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(path))
    monkeypatch.setattr(chat, "OPENROUTER_API_KEY", "sintetica")
    monkeypatch.setattr(chat, "_log", lambda *a: None)
    monkeypatch.setattr(chat, "_save_message", lambda *a, **kw: None)
    monkeypatch.setattr(chat, "get_messages", lambda *a: [])
    monkeypatch.setattr(chat, "get_tools_for_agent", lambda *a: [])
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "FATTI SANI")
    chiamate = []
    class Stream:
        async def __aenter__(self):
            raise Fermati()
        async def __aexit__(self, *a):
            return False
    def stream(**kw):
        chiamate.append(kw)
        return Stream()
    monkeypatch.setattr(chat, "AsyncOpenRouterClient", lambda: SimpleNamespace(messages=SimpleNamespace(stream=stream)))

    def leggi():
        eventi = []
        async def consuma():
            with pytest.raises(Fermati):
                async for evento in chat.stream_chat(1, "options", "domanda sintetica"):
                    eventi.append(evento)
        asyncio.run(consuma())
        meta = json.loads(next(e for e in eventi if e.startswith("event: meta")).split("data: ", 1)[1])
        return chiamate[-1]["system"][0]["text"], meta
    return path, leggi


def test_mandato_a_poi_b_nel_client_e_nel_meta_della_stessa_chiamata(cattura):
    path, leggi = cattura
    fingerprints = []
    for anni in (7, 9):
        m = mp.profilo_esempio()
        m["profilo"]["orizzonte_anni"] = anni
        m["dichiarato_il"] = "2026-01-02"
        path.write_text(json.dumps(m), encoding="utf-8")
        letto = mp.carica()
        system, meta = leggi()
        fp = mp.impronta(letto)
        fingerprints.append(fp)
        assert fp[:8] in system
        assert meta["mandato"] == {"impronta": fp, "origine": letto["origine"], "dichiarato_il": "2026-01-02"}
        assert "{MANDATO:" not in system
        assert "FATTI SANI" in system
        assert "low IV = sell premium" not in system and "high IV = buy premium" not in system
        assert "GARCH" in system
    assert fingerprints[0] != fingerprints[1]


def test_senza_mandato_meta_null_e_buco_dichiarato(cattura):
    _path, leggi = cattura
    system, meta = leggi()
    assert meta["mandato"] is None
    assert "MANDATO n.d." in system and "FATTI SANI" in system
    assert "{MANDATO:" not in system
