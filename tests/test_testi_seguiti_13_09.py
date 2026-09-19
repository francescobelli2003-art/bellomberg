"""Quattro testi rimasti sbagliati o monolingue il 13/09 (Claude Opus 5).

1. L'avviso «copertura PARZIALE: fonti bloccate ...» delle cinque rotte news
   aveva il prefisso solo in italiano: con X-BB-Language: en la risposta mescolava
   le lingue. Ora una definizione sola, stessa dichiarazione nelle due lingue.
2. Il log del consigliere senza mandato rimandava a «pagina Mandato (F11)»: F11 e'
   la Chat agenti, il mandato sta in F18.
3. «pagina Cassa/Trade» non esiste piu': la pagina e' F16 (hint del tool degli
   agenti, errore del NAV storico, nota e --help dell'importatore CSV).
4. La guida inglese chiamava la pagina con l'etichetta italiana.

Oracolo: il TASTO e il NOME delle pagine si leggono dall'app (navigazione e
cataloghi i18n), non dal codice sotto test; le frasi attese delle rotte news sono
congelate qui. Provider, ticker e importi sono inventati.
"""
import asyncio
import re
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ oracolo dall'app

def _pagina(ident, lingua):
    """(tasto F, etichetta) della destinazione `ident` come la mostra l'app."""
    registro = (ROOT / "app/src/lib/navigation.ts").read_text(encoding="utf-8")
    ordine = re.findall(r"^\s*\['([^']+)',\s*'[^']+',", registro, re.MULTILINE)
    assert ident in ordine, "destinazione %r assente da navigation.ts: %r" % (ident, ordine)
    catalogo = (ROOT / f"app/src/i18n/{lingua}/shell.ts").read_text(encoding="utf-8")
    blocco = re.search(r"export const nav = \{(.*?)\};", catalogo, re.DOTALL)
    assert blocco, "blocco nav assente da %s/shell.ts" % lingua
    etichetta = re.search(rf"\b{ident}: '([^']+)'", blocco.group(1))
    assert etichetta, "etichetta %r assente da %s/shell.ts" % (ident, lingua)
    return "F%d" % (ordine.index(ident) + 1), etichetta.group(1)


# ------------------------------------------------------------------ 1. rotte news

MUTI = {"betanews": "SKIP_DISABLED", "alfanews": "SKIP_BUDGET"}   # inserimento NON in ordine
PREFISSO = {"it": "copertura PARZIALE: fonti bloccate alfanews, betanews",
            "en": "PARTIAL coverage: blocked sources alfanews, betanews"}
ROTTE = {
    "/news/providers": {"it": " — poche/zero news NON significano quiete",
                        "en": " — few/no news items do NOT imply calm"},
    "/news/ticker/ALFA": {"it": " — poche/zero news qui NON significa quiete",
                          "en": " — few/no news items here do NOT imply calm"},
    "/news/search?q=demo": {"it": " — poche/zero news qui NON significa quiete",
                            "en": " — few/no news items here do NOT imply calm"},
    "/news/portfolio": {"it": " — un ticker a 0 news puo' essere cecita', non quiete",
                        "en": " — zero news for a ticker may indicate lack of visibility, not calm"},
    "/news/macro": {"it": " — pochi/zero item macro NON significano quiete",
                    "en": " — few/no macro items do NOT imply calm"},
}


@pytest.fixture
def news_offline(monkeypatch):
    import bellomberg.market_data.news_aggregator as na
    stato = {"muti": dict(MUTI)}
    monkeypatch.setattr(na, "providers_blocked", lambda: dict(stato["muti"]))
    monkeypatch.setattr(na, "stato_ultimo_giro", lambda *a, **k: {"stato": "n.d. (prova)"})
    monkeypatch.setattr(na, "search_news_for_ticker", lambda *a, **k: [])
    monkeypatch.setattr(na, "search_news_global", lambda *a, **k: [])
    monkeypatch.setattr(na, "search_portfolio_news", lambda *a, **k: {})
    monkeypatch.setattr(na, "fetch_macro_news", lambda *a, **k: [])
    return stato


def _get(percorso, lingua):
    from bellomberg.api import bellomberg_api as api

    async def corsa():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                                     base_url="http://127.0.0.1") as client:
            return await client.get(percorso, headers={"X-BB-Language": lingua})
    return asyncio.run(corsa())


@pytest.mark.parametrize("lingua", ["it", "en"])
@pytest.mark.parametrize("percorso", sorted(ROTTE))
def test_avviso_provider_bloccati_nella_lingua_della_richiesta(news_offline, percorso, lingua):
    risposta = _get(percorso, lingua)
    assert risposta.status_code == 200, risposta.text
    assert risposta.headers["content-language"] == lingua
    corpo = risposta.json()
    assert corpo["fonti_mute"] == MUTI
    # la frase intera: prefisso nella lingua giusta, nomi in ordine, coda della rotta
    assert corpo["avviso"] == PREFISSO[lingua] + ROTTE[percorso][lingua], corpo["avviso"]


@pytest.mark.parametrize("lingua", ["it", "en"])
@pytest.mark.parametrize("percorso", sorted(ROTTE))
def test_senza_provider_bloccati_nessun_avviso_in_nessuna_lingua(news_offline, percorso, lingua):
    news_offline["muti"] = {}
    corpo = _get(percorso, lingua).json()
    assert "avviso" in corpo and corpo["avviso"] is None, corpo["avviso"]
    assert corpo["fonti_mute"] is None


@pytest.mark.parametrize("lingua", ["it", "en"])
@pytest.mark.parametrize("percorso", sorted(ROTTE))
def test_banner_uses_store_names_without_changing_source_keys_or_reasons(news_offline, percorso, lingua):
    sources = {
        "termini_news (negozio)": "NEGOZIO_ASSENTE: original terms diagnostic",
        "custom-source": "Original custom diagnostic: leave verbatim",
        "temi_titoli (negozio)": "NEGOZIO_ILLEGGIBILE: original topics diagnostic",
    }
    news_offline["muti"] = dict(sources)
    response = _get(percorso, lingua)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["fonti_mute"] == sources
    assert news_offline["muti"] == sources
    labels = {
        "it": ("titoli per tema (negozio)", "termini di ricerca news (negozio)"),
        "en": ("securities by topic (store)", "news search terms (store)"),
    }[lingua]
    # Reader names are the same as the News Desk labels, not replacements for wire keys.
    catalog = (ROOT / f"app/src/i18n/{lingua}/newsdesk.ts").read_text(encoding="utf-8")
    for key, label in zip(("muteStoreTopics", "muteStoreTerms"), labels):
        assert re.search(r'"' + key + r'":\s*"([^"\n]*)"', catalog).group(1) == label
    prefix = {"it": "copertura PARZIALE: fonti bloccate ", "en": "PARTIAL coverage: blocked sources "}[lingua]
    assert payload["avviso"] == prefix + "custom-source, " + ", ".join(labels) + ROTTE[percorso][lingua]


# ------------------------------------------------------------------ 2. consigliere senza mandato

def test_consigliere_senza_mandato_rimanda_alla_pagina_del_mandato(monkeypatch, tmp_path, capsys):
    from bellomberg.core import mandato_pm as mp
    from bellomberg.agents import consigliere_multi
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(tmp_path / "manca.json"))
    monkeypatch.setitem(consigliere_multi._MOTIVO_USCITA, "testo", "")
    with pytest.raises(SystemExit):
        consigliere_multi.mandato_o_esci()
    righe = [r for r in capsys.readouterr().out.splitlines() if "Il comitato non parte" in r]
    assert len(righe) == 1, righe
    tasto, nome = _pagina("mandato", "it")
    assert "compila la pagina %s (%s)" % (nome, tasto) in righe[0], righe[0]
    assert "F11" not in righe[0], righe[0]


# ------------------------------------------------------------------ 3. pagina di inserimento

@pytest.fixture
def book_vuoto(tmp_path, monkeypatch):
    import json
    from bellomberg.storage import memory_db
    db_path = tmp_path / "consigliere.db"
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(db_path))
    monkeypatch.setattr(memory_db, "CHROMA_PATH", str(tmp_path / "chroma"))
    pj = tmp_path / "portfolio.json"
    monkeypatch.setattr(memory_db, "PORTFOLIO_JSON_PATH", str(pj))
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    db = memory_db.MemoryDB(db_path=str(db_path), chroma_path=str(tmp_path / "chroma"))
    pj.write_text(json.dumps({"cash_disponibile_eur": 0}), encoding="utf-8")
    return db


def test_hint_del_tool_a_book_vuoto_nomina_la_pagina_di_inserimento(book_vuoto):
    from bellomberg.agents import agent_tools
    out = agent_tools.tool_get_portfolio_state()
    assert out["n_positions"] == 0
    tasto, nome = _pagina("trades", "it")
    assert "pagina %s, %s" % (nome, tasto) in out["hint"], out["hint"]
    assert "Cassa/Trade" not in out["hint"], out["hint"]


@pytest.fixture
def nav_senza_trade(monkeypatch):
    import bellomberg.portfolio.portfolio_analytics as pa
    monkeypatch.setattr(pa, "prezzi_speciali", lambda: {"prezzi": {"senza_yfinance": []}})
    monkeypatch.setattr(pa, "ko_negozio_prezzi", lambda esito: None)
    monkeypatch.setattr(pa, "_opening_positions", lambda: [])
    monkeypatch.setattr(pa, "NUMPY_OK", True)
    monkeypatch.setattr(pa, "YF_OK", True)
    monkeypatch.setattr(pa, "_trade_history", lambda: [])
    return pa


def test_nav_storico_senza_trade_nomina_la_pagina_nelle_due_lingue(nav_senza_trade):
    from bellomberg.core.language import language_context
    from bellomberg.core.presentation import render_payload
    pa = nav_senza_trade
    with language_context("it"):
        out = pa.compute_nav_history(force=True)
    tasto, nome_it = _pagina("trades", "it")
    _, nome_en = _pagina("trades", "en")
    it = str(out["error"])
    en = str(render_payload(out, language="en")["error"])
    assert it == ("trade_history vuota: importa i trade dalla pagina %s (%s) "
                  "o con tools/ops/importa_trade_csv.py." % (nome_it, tasto)), it
    assert en == ("Empty trade_history: import trades from the %s page (%s) "
                  "or with tools/ops/importa_trade_csv.py." % (nome_en, tasto)), en
    with language_context("en"):
        assert str(pa.compute_nav_history(force=True)["error"]) == en


def test_importatore_csv_nomina_la_pagina_nella_nota_e_nell_help(book_vuoto, capsys):
    from tools.ops import importa_trade_csv as itc
    tasto, nome = _pagina("trades", "it")
    esito = itc.importa(book_vuoto, [], apply=False)
    assert "pagina %s (%s)" % (nome, tasto) in esito["nota"], esito["nota"]
    with pytest.raises(SystemExit):
        itc.main(["--help"])
    aiuto = capsys.readouterr().out
    assert "pagina %s, %s" % (nome, tasto) in aiuto, aiuto
    assert "Cassa/Trade" not in aiuto and "Cassa/Trade" not in esito["nota"]


# ------------------------------------------------------------------ 4. guida inglese

def test_guida_configurazione_nomina_il_mandato_con_l_etichetta_inglese():
    testo = (ROOT / "docs/guide/configuration.md").read_text(encoding="utf-8")
    tasto, nome_en = _pagina("mandato", "en")
    _, nome_it = _pagina("mandato", "it")
    schede = {}
    for lingua in ("en", "it"):
        catalogo = (ROOT / f"app/src/i18n/{lingua}/mandate.ts").read_text(encoding="utf-8")
        schede[lingua] = re.search(r'"mandate": "([^"]+)"', catalogo).group(1)
    sezione = testo[testo.index("## Your mandate and instrument metadata"):]
    attesa = "**%s %s** (*%s*) → **%s** (*%s*)" % (tasto, nome_en, nome_it, schede["en"], schede["it"])
    assert attesa in " ".join(sezione.split()), attesa
    assert "F18 → Mandato**" not in testo
