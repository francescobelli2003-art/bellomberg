"""Tiingo News SPENTA e GNews «al top» (10/10, Opus 5.5 — ordine PM).

Ordine del PM: «GNews fallo al top, voglio notizie importanti per tutto; Tiingo disattivalo
e assicurati che non ci siano problemi con la run».

Tiingo: abbonamento News API non rinnovato, ogni giro HTTP 403 e il feed si dichiarava
DEGRADATO per una fonte che nessuno paga. Spenta = nessuna chiamata HTTP, stato SPENTA
dichiarato in `fonti_spente()` (non in `providers_blocked()`: spenta non e' guasta), il
giro NON e' degradato per lei. Riattivazione: TIINGO_NEWS_ENABLED=1.

GNews: misure 10/10 (probe live, C:/dev/_news/probe.json, e news_feed in sola lettura).
- nome Yahoo col suffisso legale tra virgolette -> 0-1 articoli sui nomi del book provati;
- una sigla di 3 lettere nella query inglese -> politica indiana (174 righe a rilevanza 2,2);
- .DE in tedesco: 3 articoli in inglese, 25 su 45 in tedesco;
- ordine per (peso testata, data) prima del tetto per ticker, basse dichiarate.

Nessuna rete: requests.get e' sempre finto o vietato. Simboli e testate inventati.
"""
import importlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

import bellomberg.market_data.news_aggregator as na
from bellomberg.core.paths import EXAMPLES_DIR
from bellomberg.core.presentation import render_payload
from bellomberg.market_data import gnews_cache, tiingo_news

SEGRETO = "QQSEGRETOGNEWSFINTO987"
_FETCH_TIINGO_VERO = na._fetch_tiingo
_FETCH_GNEWS_TOP_VERO = na._fetch_gnews_top
_SCRIVI_VERO = na._scrivi_stato_giro


class _Risposta:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _ambiente(tmp_path, monkeypatch):
    tiingo_news.reset_status()
    na.reset_esiti_fonti()
    na.invalidate_cache()
    monkeypatch.setattr(na, "_NEWS_RATE_PATH", str(tmp_path / "news_rate_state.json"))
    monkeypatch.setattr(gnews_cache, "GNEWS_CACHE_PATH", tmp_path / "gnews_cache.json")
    for attr in ("NEWSAPI_KEY", "THENEWSAPI_KEY", "MARKETAUX_KEY"):
        monkeypatch.setattr(na, attr, "chiave-di-prova")
    monkeypatch.setattr(na, "GNEWS_KEY", SEGRETO)
    monkeypatch.setattr(na, "PERCORSO_TERMINI", str(EXAMPLES_DIR / "news_search_terms.example.json"))
    # Tiingo: chiave PRESENTE e fonte SPENTA (lo stato di produzione dal 10/10)
    monkeypatch.setattr(tiingo_news, "TIINGO_KEY", "chiave-finta-tiingo")
    monkeypatch.setattr(tiingo_news, "REQ_OK", True)
    monkeypatch.setattr(tiingo_news, "FONTE_SPENTA", True)
    chiamate_tiingo = []

    def _tiingo_get(*a, **k):
        chiamate_tiingo.append(a[:1])
        return _Risposta(403, text='{"detail":"You do not have permission to access the News API"}')
    monkeypatch.setattr(tiingo_news.requests, "get", _tiingo_get)

    def _niente_rete(*a, **k):
        raise AssertionError("rete vera tentata nel test: %r" % (a[:1],))
    monkeypatch.setattr(na.requests, "get", _niente_rete)
    yield NS(chiamate_tiingo=chiamate_tiingo)
    tiingo_news.reset_status()
    na.reset_esiti_fonti()
    na.invalidate_cache()


@pytest.fixture
def log_na(monkeypatch):
    righe = []
    monkeypatch.setattr(na, "_log", lambda msg: righe.append(str(msg)))
    return righe


# ===================================================================== Tiingo SPENTA

def test_tiingo_spenta_nessuna_chiamata_e_stato_dichiarato(_ambiente):
    motivo = []
    assert tiingo_news.fetch_tiingo_news(["ZZTEST"], motivo=motivo) == []
    assert tiingo_news.fetch_tiingo_news(None, motivo=motivo) == []
    assert _ambiente.chiamate_tiingo == []
    st = tiingo_news.last_status()
    assert st["stato"] == "SPENTA" and st["http"] is None
    assert motivo and all(m.startswith("SPENTA: ") for m in motivo)
    assert "TIINGO_NEWS_ENABLED=1" in motivo[0]
    assert "chiave-finta-tiingo" not in " ".join(motivo)


def test_tiingo_spenta_non_e_disponibile_anche_con_la_chiave():
    assert tiingo_news.tiingo_spenta() is True
    assert tiingo_news.tiingo_available() is False


def test_wrapper_del_giro_non_chiama_il_modulo_ne_conta_un_esito(_ambiente, monkeypatch):
    chiamato = []
    monkeypatch.setattr(tiingo_news, "fetch_tiingo_news", lambda *a, **k: chiamato.append(1) or [])
    assert _FETCH_TIINGO_VERO(["ZZTEST"], 1, 5) == []
    assert _FETCH_TIINGO_VERO(None, 1, 10) == []
    assert chiamato == [] and _ambiente.chiamate_tiingo == []
    assert na._esito_nel_giro("tiingo") == "non_interrogata"
    assert "tiingo" not in na.esiti_fonti()


def test_wrapper_guarda_lo_spegnimento_non_solo_la_disponibilita(_ambiente, monkeypatch):
    """Difesa in profondita': il wrapper del giro chiede `tiingo_spenta()` PRIMA di
    `tiingo_available()`. Se un domani la disponibilita' smettesse di includere lo
    spegnimento (es. un modulo riscritto), il giro resterebbe comunque senza chiamate."""
    chiamato = []
    monkeypatch.setattr(tiingo_news, "tiingo_available", lambda: True)
    monkeypatch.setattr(tiingo_news, "fetch_tiingo_news", lambda *a, **k: chiamato.append(1) or [])
    assert _FETCH_TIINGO_VERO(None, 1, 10) == []
    assert chiamato == []


def test_spenta_e_in_fonti_spente_non_in_fonti_mute_anche_dopo_un_403():
    # un 403 VERO registrato a fonte accesa, poi la fonte si spegne: non resta «muta»
    tiingo_news._segna_stato("HTTP_403", http=403)
    fuori = na.providers_blocked()
    assert "tiingo" not in fuori, fuori
    spente = na.fonti_spente()
    assert str(spente["tiingo"]).startswith("SPENTA: Tiingo News")
    en = render_payload(spente, language="en")
    assert str(en["tiingo"]).startswith("SPENTA: Tiingo News") and "not renewed" in str(en["tiingo"])
    assert "non rinnovato" in str(render_payload(spente, language="it")["tiingo"])
    assert str(spente["reddit"]).startswith("SPENTA")   # Reddit resta dichiarata come prima


def test_riaccesa_esce_da_fonti_spente_e_torna_misurata(monkeypatch):
    monkeypatch.setattr(tiingo_news, "FONTE_SPENTA", False)
    assert "tiingo" not in na.fonti_spente()
    tiingo_news._segna_stato("HTTP_403", http=403)
    assert na.providers_blocked()["tiingo"].startswith("HTTP_403")


def test_interruttore_TIINGO_NEWS_ENABLED(monkeypatch, capsys):
    """Riattivazione = una variabile del .env, letta all'import. La chiave non si tocca."""
    try:
        monkeypatch.setenv("TIINGO_NEWS_ENABLED", "1")
        importlib.reload(tiingo_news)
        assert tiingo_news.FONTE_SPENTA is False
        monkeypatch.setenv("TIINGO_NEWS_ENABLED", "0")
        importlib.reload(tiingo_news)
        assert tiingo_news.FONTE_SPENTA is True
        monkeypatch.delenv("TIINGO_NEWS_ENABLED")
        importlib.reload(tiingo_news)
        assert tiingo_news.FONTE_SPENTA is True      # default: SPENTA
        monkeypatch.setenv("TIINGO_NEWS_ENABLED", "")
        importlib.reload(tiingo_news)
        assert tiingo_news.FONTE_SPENTA is True      # riga vuota del template: SPENTA
        monkeypatch.setenv("TIINGO_NEWS_ENABLED", "si")
        importlib.reload(tiingo_news)
        assert tiingo_news.FONTE_SPENTA is True      # non valido: SPENTA, e lo dice
        assert "non valido" in capsys.readouterr().out
        # v2 (riserva 5): il motivo lo porta anche fonti_spente() (rotte, chat, giro)
        assert "valore non valido: 'si'" in tiingo_news.MOTIVO_SPENTA
        assert "valore non valido: 'si'" in str(na.fonti_spente()["tiingo"])
        assert "invalid value: 'si'" in str(render_payload(na.fonti_spente(), language="en")["tiingo"])
        monkeypatch.setenv("TIINGO_NEWS_ENABLED", "0")
        importlib.reload(tiingo_news)
        assert "non valido" not in tiingo_news.MOTIVO_SPENTA
    finally:
        monkeypatch.delenv("TIINGO_NEWS_ENABLED", raising=False)
        importlib.reload(tiingo_news)


# ---------------------------------------------------------------- giro completo

def _db_finto(tmp_path):
    p = tmp_path / "feed.db"
    with sqlite3.connect(p) as c:
        c.execute("""CREATE TABLE news_feed (id INTEGER PRIMARY KEY, title TEXT, snippet TEXT, source TEXT,
                     url TEXT UNIQUE, published_at TEXT, pulled_at TEXT, ticker_mentioned TEXT, theme TEXT,
                     provider TEXT, sentiment TEXT, sentiment_score REAL, relevance INTEGER,
                     notified INTEGER DEFAULT 0, headline_it TEXT, why_matters TEXT, classified INTEGER)""")
    return str(p)


def _iso(ore_fa: float) -> str:
    """Data RELATIVA a adesso: il giro scarta gli articoli piu' vecchi di `days` (v2)."""
    return (datetime.now(timezone.utc) - timedelta(hours=ore_fa)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _articolo(n, fonte, quando=None, provider="gnews"):
    quando = _iso(1) if quando is None else quando
    return {"title": f"titolo {n} {fonte}", "url": f"http://esempio.invalid/{n}", "source": fonte,
            "published_at": quando, "provider": provider}


@pytest.fixture
def giro(tmp_path, monkeypatch):
    db_path = _db_finto(tmp_path)
    monkeypatch.setattr(na, "MemoryDB", lambda: NS(db_path=db_path, get_portfolio_summary=lambda: {"positions": []}))
    monkeypatch.setattr(na, "giro_news", lambda positions: (["ZZTEST"], ["ZZTEST (1.0%)"]))
    monkeypatch.setattr(na, "_termini_del_giro", lambda contesto: {})
    monkeypatch.setattr(na, "_favorites_tickers", lambda: set())
    monkeypatch.setattr(na, "_save_summary", lambda *a, **k: None)
    monkeypatch.setattr(na, "search_news_global", lambda *a, **k: [])
    monkeypatch.setattr(na, "_fetch_tiingo", _FETCH_TIINGO_VERO)        # il wrapper VERO
    monkeypatch.setattr(na, "_fetch_gnews_top", lambda *a, **k: [_articolo(99, "Reuters")])
    monkeypatch.setattr(na, "_classify_with_haiku", lambda it, ctx: {
        "sentiment": "neutral", "sentiment_score": 0.0, "relevance": 6, "headline_it": "h",
        "why_matters": "w", "_classification_status": "classified"})
    scritti = []
    monkeypatch.setattr(na, "_scrivi_stato_giro", lambda out: scritti.append(out))
    per_ticker = {"items": [_articolo(1, "Yahoo Finance"), _articolo(2, "Reuters")]}
    monkeypatch.setattr(na, "search_news_for_ticker",
                        lambda tk, days=1, max_per_source=5: [dict(i) for i in per_ticker["items"]])
    return NS(scritti=scritti, per_ticker=per_ticker)


def test_giro_completo_con_tiingo_spenta_non_e_degradato(giro, _ambiente):
    out = na.auto_pull_feed(classify=True, language="it")
    assert _ambiente.chiamate_tiingo == []                       # nessuna HTTP a Tiingo
    assert out["fonti_esito"]["tiingo"] == "SPENTA"
    assert "tiingo" not in out["providers_blocked"], out["providers_blocked"]
    assert out["degraded"] is False, out
    assert out["saved"] == 3 and out["provenienza"] == {"gnews": 3}
    assert giro.scritti and giro.scritti[0]["fonti_esito"]["tiingo"] == "SPENTA"


def test_giro_completo_stato_scritto_e_riletto(giro, tmp_path, monkeypatch):
    percorso = str(tmp_path / "news_feed_status.json")
    monkeypatch.setattr(na, "_scrivi_stato_giro", lambda out: _SCRIVI_VERO(out, percorso))
    na.auto_pull_feed(classify=True, language="it")
    letto = na.stato_ultimo_giro(percorso)
    assert letto["stato"] == "ok", letto
    assert letto["fonti_esito"]["tiingo"] == "SPENTA"
    assert "tiingo" not in (letto["providers_blocked"] or {})
    assert letto["ranking_testate"] == {"oltre_tetto": 0, "bassa_oltre_tetto": 0, "bassa_tenute": 0,
                                        "gia_nel_feed": 0, "gia_nel_giro": 0, "oltre_eta": 0,
                                        "senza_data": 0, "feed_non_controllato": 0}


# ====================================================================== GNews query

@pytest.mark.parametrize("nome,atteso", [
    ("ZETACORE Corporation", "ZETACORE"),
    ("Zeta Micro Devices, Inc.", "Zeta Micro Devices"),
    ("Nordzeta A/S", "Nordzeta"),
    ("Zetametall AG", "Zetametall"),
    ("Zetardo S.p.a.", "Zetardo"),
    ("Zetachem Aktiengesellschaft", "Zetachem"),
    ("Zeta Square Holdings", "Zeta Square"),            # v2: Holdings e' una parola generica
    ("Zeta Holdings, Inc.", "Zeta"),                    # due giri del ciclo (mutante N1)
    ("Zetabet Inc. Class A", "Zetabet"),                # classe azionaria in mezzo
    ("Zeta Hathaway Inc. Class B", "Zeta Hathaway"),
    ("Zeta-Cola Company (The)", "Zeta-Cola"),
    ("The Zeta Group", "Zeta"),
    ("Zeta Technology, Inc.", "Zeta"),
    ("Zeta Systems, Inc.", "Zeta"),
    ("Zeta Technologies Corp", "Zeta"),
    ("ZetaMorgan Chase & Co.", "ZetaMorgan Chase"),     # «&» senza lettere esce dalla coda
    ("Zeta Systems Group", "Zeta"),
    ("Corporation", "Corporation"),                              # almeno una parola resta
])
def test_nome_per_ricerca_toglie_il_suffisso_legale(nome, atteso):
    assert na.nome_per_ricerca(nome) == atteso


def test_query_inglese_senza_termini_corti_quella_locale_con():
    termini = ["Banca Zeta dei Colli", "Zeta dei Colli", "Bzc"]
    assert na.query_da_termini(termini, "en") == '"Banca Zeta dei Colli" OR "Zeta dei Colli"'
    assert na.query_da_termini(termini, "it").endswith(" OR Bzc")
    assert na.query_da_termini(["Bzc"], "en") == "Bzc"          # unico termine: resta
    assert na.query_da_termini(["Acme Manifatture", "Acme"], "en") == '"Acme Manifatture" OR Acme'


def test_filtro_termini_corti_rispetta_le_maiuscole():
    items = [{"title": "Opposition BZc members host a lunch", "provider": "gnews"},
             {"title": "Bzc, cda in settimana", "provider": "gnews"},
             {"title": "BZC e Zeta: le dichiarazioni", "provider": "gnews"},
             {"title": "La Zeta dei Colli sale", "provider": "gnews"}]
    tenuti = [i["title"] for i in na._filter_items_by_terms(items, ["Zeta dei Colli", "Bzc"])]
    assert tenuti == ["Bzc, cda in settimana", "BZC e Zeta: le dichiarazioni", "La Zeta dei Colli sale"]


def _provider_testuali(monkeypatch):
    chiamate = []

    def _gnews(query, days=3, max_results=10, lang="en"):
        chiamate.append(("gnews", lang, query))
        return []
    monkeypatch.setattr(na, "_fetch_gnews", _gnews)
    monkeypatch.setattr(na, "_fetch_newsapi", lambda q, *a, **k: chiamate.append(("newsapi", "en", q)) or [])
    monkeypatch.setattr(na, "_fetch_thenewsapi", lambda q, *a, **k: chiamate.append(("thenewsapi", "en", q)) or [])
    monkeypatch.setattr(na, "_fetch_yfinance_news", lambda t, n: [])
    monkeypatch.setattr(na, "_fetch_finnhub_news", lambda *a, **k: [])
    monkeypatch.setattr(na, "_all_rss_cached", lambda *a, **k: [])
    monkeypatch.setattr(na, "_simbolo_news", lambda t, nome: (t, "test"))
    return chiamate


@pytest.mark.parametrize("ticker,lingua", [("ZZK.DE", "de"), ("ZZQ.MI", "it"), ("ZZUS", None),
                                            ("ZZJ.L", None)])
def test_lingua_locale_per_borsa(monkeypatch, ticker, lingua):
    chiamate = _provider_testuali(monkeypatch)
    monkeypatch.setattr(na, "_voce_termini", lambda t: (["Zeta Werke"], "negozio", {}))
    na.search_news_for_ticker(ticker, days=1, max_per_source=1)
    lingue = [lg for p, lg, _q in chiamate if p == "gnews"]
    assert lingue == (["en", lingua] if lingua else ["en"])


def test_nome_yahoo_pulito_entra_in_query_e_filtro(monkeypatch, log_na):
    chiamate = _provider_testuali(monkeypatch)
    monkeypatch.setattr(na, "_voce_termini", lambda t: (None, "assente", {"origine": "negozio", "motivo": ""}))
    monkeypatch.setattr(na, "_nome_emittente_yahoo", lambda t: "Zeta Micro Devices, Inc.")
    monkeypatch.setattr(na, "_fetch_gnews", lambda q, days=3, max_results=10, lang="en": chiamate.append(
        ("gnews", lang, q)) or [{"title": "Zeta Micro Devices beats estimates", "provider": "gnews",
                                 "url": "http://esempio.invalid/z", "source": "CNBC"}])
    items = na.search_news_for_ticker("ZZMD", days=1, max_per_source=1)
    assert {q for _p, _l, q in chiamate} == {'"Zeta Micro Devices"'}
    assert [i["title"] for i in items] == ["Zeta Micro Devices beats estimates"]
    assert any("senza suffisso legale" in r for r in log_na)


# =================================================================== GNews parametri

def test_fetch_gnews_parametri_misurati_e_chiave_mai_nel_log(monkeypatch, log_na):
    viste = []

    def _get(url, params=None, timeout=None):
        viste.append((url, dict(params)))
        return _Risposta(401, text="invalid apikey=" + SEGRETO)
    monkeypatch.setattr(na.requests, "get", _get)
    assert na._fetch_gnews('"Zeta Werke"', days=1, max_results=25, lang="de") == []
    url, p = viste[0]
    assert url == "https://gnews.io/api/v4/search"
    assert p["in"] == "title,description" and p["sortby"] == "publishedAt"
    assert p["lang"] == "de" and p["max"] == 25 and p["q"] == '"Zeta Werke"'
    assert "country" not in p          # misurato: nessun guadagno sulle query per nome
    assert any("gnews HTTP 401" in r for r in log_na)
    assert not any(SEGRETO in r for r in log_na), log_na


def test_fetch_gnews_eccezione_con_url_non_mostra_la_chiave(monkeypatch, log_na):
    def _esplode(url, params=None, timeout=None):
        raise RuntimeError("https://gnews.io/api/v4/top-headlines?apikey=" + SEGRETO)
    monkeypatch.setattr(na.requests, "get", _esplode)
    assert _FETCH_GNEWS_TOP_VERO(10, 1) == []
    assert na._fetch_gnews("zeta", days=1) == []
    assert any("top-headlines failed" in r for r in log_na)
    assert not any(SEGRETO in r for r in log_na), log_na


def test_top_headlines_parametri_cache_e_budget(monkeypatch):
    viste = []
    art = {"title": "Markets rally", "url": "http://esempio.invalid/th", "publishedAt": "2099-01-01T00:00:00Z",
           "description": "d", "source": {"name": "CNBC", "url": "https://www.cnbc.com"}}

    def _get(url, params=None, timeout=None):
        viste.append((url, dict(params)))
        return _Risposta(200, {"totalArticles": 1, "articles": [art]})
    monkeypatch.setattr(na.requests, "get", _get)
    primo = _FETCH_GNEWS_TOP_VERO(10, 1)
    assert [i["title"] for i in primo] == ["Markets rally"] and primo[0]["provider"] == "gnews"
    url, p = viste[0]
    assert url == "https://gnews.io/api/v4/top-headlines"
    assert (p["category"], p["country"], p["lang"], p["max"]) == ("business", "us", "en", 10)
    stato = json.loads(open(na._NEWS_RATE_PATH, encoding="utf-8").read())["gnews"]
    assert stato["count"] == 1 and na._chiave_gnews_top().lower() in stato["per_query"]
    # seconda chiamata entro il cooldown: nessuna HTTP, articoli dalla cache, dichiarati
    secondo = _FETCH_GNEWS_TOP_VERO(10, 1)
    assert len(viste) == 1
    assert secondo and "_gnews_cache_eta_s" in secondo[0]
    assert json.loads(open(na._NEWS_RATE_PATH, encoding="utf-8").read())["gnews"]["count"] == 1


def test_top_headlines_senza_chiave_nessuna_chiamata(monkeypatch):
    monkeypatch.setattr(na, "GNEWS_KEY", "")
    assert _FETCH_GNEWS_TOP_VERO(10, 1) == []      # rete vietata: sarebbe esploso
    assert na.providers_blocked()["gnews"].startswith("SENZA_CHIAVE")


def test_limiti_gnews_e_cache_invariati():
    assert na.NEWS_PROVIDER_LIMITS["gnews"] == {"daily": 800, "cooldown": 2 * 3600, "disable": 12 * 3600}
    assert gnews_cache.TTL_S == na.NEWS_PROVIDER_LIMITS["gnews"]["cooldown"]
    assert na.GNEWS_MAX_ART == 25


def test_registro_cooldown_non_sfratta_le_query_del_giro(monkeypatch):
    """Il registro teneva 60 query: la 61-esima sfrattava la piu' vecchia, che al giro dopo
    si ripagava. Ora il tetto e' MAX_QUERY_COOLDOWN."""
    for i in range(80):
        na._provider_record("gnews", f"query di prova {i}")
    stato = json.loads(open(na._NEWS_RATE_PATH, encoding="utf-8").read())["gnews"]
    assert len(stato["per_query"]) == 80
    assert na.provider_status("gnews", "query di prova 0") == "SKIP_COOLDOWN"


# ========================================================================= ranking

def test_testata_di_qualita_batte_la_generica_a_parita_di_data():
    q = "2026-10-09T10:00:00Z"
    items = [_articolo(1, "Times of India", q), _articolo(2, "Zeta Daily", q),
             _articolo(3, "Reuters", q), _articolo(4, "Seeking Alpha", q)]
    ordinati = na.ordina_per_qualita(items)
    assert [i["source"] for i in ordinati] == ["Reuters", "Seeking Alpha", "Zeta Daily", "Times of India"]
    assert [i["qualita_testata"] for i in ordinati] == ["primaria", "finanziaria", "standard", "bassa"]


def test_a_parita_di_testata_vince_la_piu_recente():
    items = [_articolo(1, "Reuters", "2026-10-08T10:00:00Z"), _articolo(2, "Reuters", "2026-10-09T10:00:00Z")]
    assert [i["url"][-1] for i in na.ordina_per_qualita(items)] == ["2", "1"]


def test_peso_dal_dominio_quando_manca_il_nome():
    assert na.peso_testata({"source": "", "url": "https://www.reuters.com/x"}) == 3
    assert na.peso_testata({"source": "timesofindia.indiatimes.com", "url": ""}) == 0
    assert na.peso_testata({"source": "Testata Ignota", "url": ""}) == 1


def test_le_basse_non_si_scartano_e_il_taglio_si_conta():
    items = [_articolo(i, "EUROPE SAYS") for i in range(3)] + [_articolo(10 + i, "Reuters") for i in range(3)]
    ordinati = na.ordina_per_qualita(items)
    assert len(ordinati) == 6                                    # nessuno scartato
    assert na.conta_tagli_qualita(ordinati, 4) == {"oltre_tetto": 2, "bassa_oltre_tetto": 2, "bassa_tenute": 1}


def test_giro_dichiara_le_basse_tagliate_dal_tetto(giro, log_na):
    # come lo restituisce search_news_for_ticker vero: GIA' ordinato per qualita'
    giro.per_ticker["items"] = na.ordina_per_qualita([_articolo(i, "The Tribune") for i in range(6)]
                                                     + [_articolo(20 + i, "Bloomberg") for i in range(6)])
    out = na.auto_pull_feed(classify=True, max_per_ticker=5, language="it")
    r = out["ranking_testate"]
    assert (r["oltre_tetto"], r["bassa_oltre_tetto"], r["bassa_tenute"]) == (2, 2, 4)
    assert any("ranking testate" in r and "bassa qualita'" in r for r in log_na), log_na


def test_search_news_for_ticker_ordina_per_qualita(monkeypatch):
    _provider_testuali(monkeypatch)
    monkeypatch.setattr(na, "_voce_termini", lambda t: (["Zeta Werke"], "negozio", {}))
    q = "2026-10-09T10:00:00Z"
    monkeypatch.setattr(na, "_fetch_gnews", lambda *a, **k: [
        {"title": "Zeta Werke news A", "source": "Hindustan Times", "url": "http://esempio.invalid/a",
         "published_at": "2026-10-09T11:00:00Z", "provider": "gnews"},
        {"title": "Zeta Werke news B", "source": "Financial Times", "url": "http://esempio.invalid/b",
         "published_at": q, "provider": "gnews"}])
    items = na.search_news_for_ticker("ZZW", days=1, max_per_source=1)
    assert [i["source"] for i in items] == ["Financial Times", "Hindustan Times"]


# ============================================ v2 (10/10, riserve del revisore) — Opus 5.5
# Riserva 1: il tetto per ticker/tema si applica alle sole notizie NUOVE e nella finestra.

def _nel_feed(giro_db_path, *articoli):
    with sqlite3.connect(giro_db_path) as c:
        for a in articoli:
            c.execute("INSERT INTO news_feed (title, url, source, published_at, pulled_at) "
                      "VALUES (?, ?, ?, ?, datetime('now'))",
                      (a["title"], a["url"], a["source"], a["published_at"]))


def _db_del_giro():
    return na.MemoryDB().db_path


def test_gia_nel_feed_non_occupa_il_tetto(giro):
    """Il difetto del revisore: 10 articoli di qualita' GIA' salvati occupavano i 10 posti a
    ogni giro (24h) e le notizie nuove standard non entravano mai."""
    vecchi = [_articolo(i, "Reuters", _iso(2)) for i in range(10)]
    _nel_feed(_db_del_giro(), *vecchi)
    nuovi = [_articolo(100 + i, "Zeta Daily", _iso(3)) for i in range(3)]
    giro.per_ticker["items"] = na.ordina_per_qualita(vecchi + nuovi)
    out = na.auto_pull_feed(classify=True, max_per_ticker=5, language="it")
    r = out["ranking_testate"]
    assert r["gia_nel_feed"] == 10 and r["oltre_tetto"] == 0, r
    assert out["saved"] == 3 + 1            # i 3 nuovi + la top-headline del fixture
    assert out["skipped_duplicates"] == 10  # i doppioni tolti prima del tetto si contano
    with sqlite3.connect(_db_del_giro()) as c:
        salvati = {u for (u,) in c.execute("SELECT url FROM news_feed")}
    assert all(a["url"] in salvati for a in nuovi)


def test_oltre_tetto_conta_solo_le_nuove(giro):
    vecchi = [_articolo(i, "Bloomberg", _iso(1)) for i in range(4)]
    _nel_feed(_db_del_giro(), *vecchi)
    nuovi = [_articolo(100 + i, "Zeta Daily", _iso(2)) for i in range(12)]
    giro.per_ticker["items"] = na.ordina_per_qualita(vecchi + nuovi)
    out = na.auto_pull_feed(classify=True, max_per_ticker=5, language="it")
    assert out["ranking_testate"]["oltre_tetto"] == 2       # 12 nuove, tetto 10
    assert out["ranking_testate"]["gia_nel_feed"] == 4


def test_titolo_gia_nel_feed_esce_prima_del_tetto_e_si_conta(giro):
    _nel_feed(_db_del_giro(), {"title": "Zeta sale del 5%!", "url": "http://altrove.invalid/x",
                               "source": "Reuters", "published_at": _iso(2)})
    giro.per_ticker["items"] = [{"title": "zeta SALE del 5%", "url": "http://esempio.invalid/nuovo",
                                 "source": "Zeta Daily", "published_at": _iso(1), "provider": "gnews"}]
    out = na.auto_pull_feed(classify=True, language="it")
    assert out["ranking_testate"]["gia_nel_feed"] == 1
    assert out["doppioni_titolo"]["titolo_db"] == 1
    assert out["saved"] == 1                 # solo la top-headline


def test_eta_oltre_days_fuori_dal_ranking_dichiarata(giro, log_na):
    giro.per_ticker["items"] = [_articolo(1, "Reuters", _iso(30)), _articolo(2, "Zeta Daily", _iso(2)),
                                {"title": "senza data", "url": "http://esempio.invalid/sd",
                                 "source": "Reuters", "provider": "gnews"}]
    out = na.auto_pull_feed(days=1, classify=True, language="it")
    r = out["ranking_testate"]
    assert (r["oltre_eta"], r["senza_data"]) == (1, 1), r
    with sqlite3.connect(_db_del_giro()) as c:
        salvati = {u for (u,) in c.execute("SELECT url FROM news_feed")}
    assert "http://esempio.invalid/1" not in salvati
    assert {"http://esempio.invalid/2", "http://esempio.invalid/sd"} <= salvati
    assert any("piu' vecchi di 1 giorni" in x for x in log_na), log_na


def test_stesso_articolo_su_due_ticker_occupa_un_posto_solo(giro, monkeypatch):
    monkeypatch.setattr(na, "giro_news", lambda positions: (["ZZA", "ZZB"], ["ZZA (1.0%)", "ZZB (1.0%)"]))
    comune = _articolo(1, "Reuters")
    propri = {"ZZA": [], "ZZB": [_articolo(10 + i, "Zeta Daily", _iso(2)) for i in range(2)]}
    monkeypatch.setattr(na, "search_news_for_ticker",
                        lambda tk, days=1, max_per_source=5: na.ordina_per_qualita(
                            [dict(comune)] + [dict(i) for i in propri[tk]]))
    out = na.auto_pull_feed(classify=True, max_per_ticker=1, language="it")   # tetto 2 per ticker
    r = out["ranking_testate"]
    assert r["gia_nel_giro"] == 1 and r["oltre_tetto"] == 0, r
    assert out["saved"] == 1 + 2 + 1


def test_lettura_del_feed_fallita_si_conta_e_non_ferma_il_giro(giro, monkeypatch, log_na):
    def _rotto(cur, it, titoli):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(na, "_gia_nel_feed", _rotto)
    out = na.auto_pull_feed(classify=True, language="it")
    assert out["ranking_testate"]["feed_non_controllato"] == 3
    assert any("non controllati contro il feed" in x for x in log_na), log_na


def test_fasce_di_freschezza_prima_della_testata():
    ora = 1_800_000_000.0

    def iso(ore):
        return datetime.fromtimestamp(ora - ore * 3600, timezone.utc).isoformat()
    items = [{"source": "Reuters", "url": "http://esempio.invalid/r20", "published_at": iso(20)},
             {"source": "Zeta Daily", "url": "http://esempio.invalid/z1", "published_at": iso(1)},
             {"source": "Times of India", "url": "http://esempio.invalid/t2", "published_at": iso(2)},
             {"source": "Reuters", "url": "http://esempio.invalid/nd"},
             # standard e datata: precede il Reuters SENZA data (mutante V7)
             {"source": "Zeta Old", "url": "http://esempio.invalid/b30", "published_at": iso(30)},
             {"source": "Seeking Alpha", "url": "http://esempio.invalid/s5", "published_at": iso(5)}]
    ordinati = [i["url"].rsplit("/", 1)[1] for i in na.ordina_per_qualita(items, ora=ora)]
    # <6h: finanziaria, standard, bassa | <24h: Reuters | oltre: la datata, poi il senza data
    assert ordinati == ["s5", "z1", "t2", "r20", "b30", "nd"]
    assert [na.fascia_freschezza(i, ora) for i in items] == [1, 0, 0, 2, 2, 0]


def test_candidati_nuovi_ordina_con_lo_stesso_orologio(tmp_path):
    db = _db_finto(tmp_path)
    ora = 1_800_000_000.0

    def iso(ore):
        return datetime.fromtimestamp(ora - ore * 3600, timezone.utc).isoformat()
    conta = {}
    with sqlite3.connect(db) as c:
        out = na.candidati_nuovi([{"source": "Reuters", "url": "http://e.invalid/a", "published_at": iso(7)},
                                  {"source": "Zeta Daily", "url": "http://e.invalid/b", "published_at": iso(5)},
                                  {"source": "Zeta Daily", "url": "http://e.invalid/c", "published_at": iso(25)}],
                                 c.cursor(), set(), set(), 1, conta, ora)
    assert [i["url"][-1] for i in out] == ["b", "a"]
    assert conta == {"oltre_eta": 1}


# Riserva 2: nome Yahoo di UNA parola -> query e filtro col contesto di borsa.

def test_nome_di_una_parola_query_col_contesto(monkeypatch, log_na):
    chiamate = _provider_testuali(monkeypatch)
    monkeypatch.setattr(na, "_voce_termini", lambda t: (None, "assente", {"origine": "negozio", "motivo": ""}))
    monkeypatch.setattr(na, "_nome_emittente_yahoo", lambda t: "Raffaello S.p.A.")
    na.search_news_for_ticker("RFZ.MI", days=1, max_per_source=1)
    query = {lg: q for p, lg, q in chiamate if p == "gnews"}
    assert query["en"] == '"Raffaello" AND ( RFZ OR stock OR shares OR "share price" )'
    assert query["it"] == '"Raffaello" AND ( RFZ OR azioni OR titolo )'
    from bellomberg.market_data.news_sources import gnews_safe_query
    assert gnews_safe_query(query["en"]) == '"Raffaello" AND (RFZ OR stock OR shares OR "share price")'
    assert {q for p, _l, q in chiamate if p == "newsapi"} == {query["en"]}
    assert any("nome di una parola sola" in r for r in log_na)


def test_shares_come_verbo_non_vale_come_contesto():
    # 10/10 probe live GNews: «X DiCaprio Shares Political Message» passava col contesto «shares».
    items = [{"title": "Raffaello DiCaprio Shares Political Message", "snippet": "", "provider": "gnews"},
             {"title": "Raffaello shares of the group rose 3%", "snippet": "", "provider": "gnews"},
             {"title": "Raffaello stock jumps", "snippet": "", "provider": "gnews"},
             {"title": "Raffaello share price hits record", "snippet": "", "provider": "gnews"}]
    out = na._filter_items_by_terms(items, ["Raffaello"], contesto=na.contesto_nome_singolo("RFZ.MI"))
    assert [i["title"] for i in out] == [i["title"] for i in items[1:]]


def test_nome_di_due_parole_resta_senza_contesto(monkeypatch):
    chiamate = _provider_testuali(monkeypatch)
    monkeypatch.setattr(na, "_voce_termini", lambda t: (None, "assente", {"origine": "negozio", "motivo": ""}))
    monkeypatch.setattr(na, "_nome_emittente_yahoo", lambda t: "Zeta Werke AG")
    na.search_news_for_ticker("ZZK.DE", days=1, max_per_source=1)
    assert {q for p, _l, q in chiamate if p == "gnews"} == {'"Zeta Werke"'}


@pytest.mark.parametrize("nome,tk,titolo,snippet,tenuto", [
    ("Target", "TGT", "Fed hits its inflation target", "Rates stay put", False),
    ("Target", "TGT", "Target shares slide after earnings", "", True),
    ("Target", "TGT", "Retailer update", "Target fell 3% as TGT guided lower", True),
    ("Target", "TGT", "target shares", "", False),          # il nome si confronta come scritto
    ("Raffaello", "RFZ.MI", "Raffaello DiZeta stars in new film", "box office", False),
    ("Raffaello", "RFZ.MI", "Raffaello wins defence order", "The group's shares rose 2%", True),
    ("Raffaello", "RFZ.MI", "Raffaello, il titolo vola in Borsa", "", True),
    ("Zebra", "ZBR.DE", "Zebra Leverkusen beat Hertha 2-0", "Bundesliga", False),
    ("Zebra", "ZBR.DE", "Zebra-Aktie legt zu", "", True),
])
def test_filtro_nome_singolo_vuole_il_contesto(nome, tk, titolo, snippet, tenuto):
    item = {"title": titolo, "snippet": snippet, "provider": "gnews"}
    tenuti = na._filter_items_by_terms([item], [nome], na.contesto_nome_singolo(tk))
    assert bool(tenuti) is tenuto


def test_filtro_contesto_non_conta_il_nome_uguale_al_simbolo():
    # il nome scritto MAIUSCOLO coincide col simbolo: non vale anche come contesto (mutante V20)
    item = {"title": "ZX results", "provider": "gnews"}
    assert na._filter_items_by_terms([item], ["Zx"], na.contesto_nome_singolo("ZX")) == []


def test_fondo_senza_voce_non_si_cerca_per_nome(monkeypatch, log_na):
    chiamate = _provider_testuali(monkeypatch)
    monkeypatch.setattr(na, "_voce_termini", lambda t: (None, "assente", {"origine": "negozio", "motivo": ""}))
    monkeypatch.setattr(na, "_nome_emittente_yahoo", lambda t: "Zeta MSCI World UCITS ETF USD (Acc)")
    yf = []
    monkeypatch.setattr(na, "_fetch_yfinance_news", lambda t, n: yf.append(t) or [])
    na.search_news_for_ticker("ZZW.MI", days=1, max_per_source=1)
    assert chiamate == [] and yf == ["ZZW.MI"]
    assert any("e' un fondo" in r for r in log_na)
    for nome in ("Zeta Core Xtrackers", "iShares Zeta", "Zeta ETC"):
        assert na.nome_e_un_fondo(nome)
    assert not na.nome_e_un_fondo("Zetafund Holdings")


# Riserva 4: nome esatto o dominio, mai sottostringhe ne' path.

@pytest.mark.parametrize("fonte,url,peso", [
    ("CNBC TV18", "", 1), ("Bloomberg Quint", "", 1), ("Fortune India", "", 1), ("Forbes India", "", 1),
    ("", "https://www.cnbctv18.com/market/x", 1), ("", "https://www.forbesindia.com/x", 1),
    ("Zeta News", "https://www.zetanews.example/reuters/bloomberg-story", 1),   # il path non promuove
    ("CNBC", "", 3), ("", "https://www.cnbc.com/2026/x", 3),
    ("MF Milano Finanza", "", 3), ("Milano Finanza", "", 3), ("MF", "", 3),
    ("Börsen-Zeitung", "", 3), ("Boersen-Zeitung", "", 3), ("F.A.Z.", "", 3), ("FAZ", "", 3),
    ("Il Sole 24 ORE", "", 3), ("ANSA", "", 3), ("ANSA Economia", "https://www.ansa.it/x", 3),
    ("Radiocor", "", 3), ("AP News", "", 3),
    ("Teleborsa", "", 2), ("Der Aktionär", "", 2), ("finanzen.net", "", 2),
    ("Yahoo", "https://finnhub.io/api/news?id=1", 2), ("", "https://uk.finance.yahoo.com/n", 2),
    ("Yahoo Entertainment", "https://www.yahoo.com/entertainment/x", 1),
    ("Barrons.com", "", 3),                          # nome che e' un dominio
    ("Zeta Daily", "https://www.reuters.com/x", 3),  # il dominio vince sul nome ignoto
    ("New York Post", "", 0), ("", "https://nypost.com/x", 0),
    ("Reuters", "https://finance.yahoo.com/x", 3),   # piu' corrispondenze: vale la piu' alta
])
def test_peso_testata_nome_esatto_o_dominio(fonte, url, peso):
    assert na.peso_testata({"source": fonte, "url": url}) == peso


# Riserva 5 e mutanti sopravvissuti alla prima versione.

def test_news_sources_fetch_gnews_parametri_allineati(monkeypatch):
    from bellomberg.market_data import news_sources as ns
    viste = []
    monkeypatch.setattr(ns, "GNEWS_API_KEY", SEGRETO)
    monkeypatch.setattr(ns, "_provider_skip_reason", lambda p, q: None)
    monkeypatch.setattr(ns, "_rate_limiter", lambda: (None, lambda *a, **k: None))
    monkeypatch.setattr(ns.requests, "get", lambda url, params=None, timeout=None: viste.append(dict(params))
                        or _Risposta(200, {"articles": []}))
    assert ns.fetch_gnews("zeta", max_news=30) == []
    p = viste[0]
    assert p["in"] == "title,description" and p["sortby"] == "publishedAt"
    assert p["max"] == 25 and p["lang"] == "en"            # quantita' invariate


def test_query_tedesca_tiene_i_termini_corti():
    """Mutante N2: solo l'INGLESE perde le sigle; la stampa tedesca le scrive."""
    assert na.query_da_termini(["Zeta Werke", "ZW"], "de") == '"Zeta Werke" OR ZW'


def test_top_headlines_403_spegne_gnews(monkeypatch):
    """Mutante N7: un 403 (quota/piano) disabilita GNews come le ricerche."""
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: _Risposta(403, text="forbidden"))
    assert _FETCH_GNEWS_TOP_VERO(10, 1) == []
    assert na.provider_status("gnews", "una query qualsiasi") == "SKIP_DISABLED"


def test_top_headlines_rispetta_il_fermo(giro, monkeypatch):
    """Mutante N8: un arresto del backend durante i temi salta anche le top-headlines."""
    temi = []
    monkeypatch.setattr(na, "search_news_global", lambda *a, **k: temi.append(1) or [])
    top = []
    monkeypatch.setattr(na, "_fetch_gnews_top", lambda *a, **k: top.append(1) or [])
    out = na.auto_pull_feed(classify=True, language="it", should_stop=lambda: len(temi) >= 8)
    assert len(temi) == 8 and top == [] and out["interrupted"] is True


def test_macro_ordina_prima_per_importanza_del_tema(monkeypatch):
    """Mutante N9: l'importanza del tema viene prima della testata e della freschezza."""
    from bellomberg.market_data import news_topics
    monkeypatch.setattr(news_topics, "TOPICS", [
        {"id": "t5", "label": "T5", "category": "zz", "importance": 5, "query": "zeta alta"},
        {"id": "t3", "label": "T3", "category": "zz", "importance": 3, "query": "zeta bassa"}])
    monkeypatch.setattr(news_topics, "CATEGORIES", {"zz": "ZZ"})
    monkeypatch.setattr(na, "RSS_FEEDS", {})
    monkeypatch.setattr(na, "_titoli_del_giro", lambda contesto: {"temi": {}, "origine": "ok"})
    monkeypatch.setattr(na, "_fetch_newsapi", lambda *a, **k: [])
    risposte = {"zeta alta": {"title": "alta", "source": "New York Post", "url": "http://e.invalid/a",
                              "published_at": _iso(20), "provider": "gnews"},
                "zeta bassa": {"title": "bassa", "source": "Reuters", "url": "http://e.invalid/b",
                               "published_at": _iso(1), "provider": "gnews"}}
    monkeypatch.setattr(na, "_fetch_gnews", lambda q, **k: [dict(risposte[q])])
    out = na.fetch_macro_news(categories=["zz"], include_reddit=False)
    assert [i["title"] for i in out] == ["alta", "bassa"]


def test_tetto_per_tema_contato_sul_suo_tetto(giro, monkeypatch):
    """Mutante N13: i tagli dei temi si contano su max_per_theme, non sul tetto dei ticker."""
    giro.per_ticker["items"] = []
    monkeypatch.setattr(na, "search_news_global", lambda q, **k: [
        _articolo(f"{q}-{i}", "Zeta Daily") for i in range(4)])
    out = na.auto_pull_feed(classify=False, max_per_ticker=5, max_per_theme=2, language="it")
    assert out["ranking_testate"]["oltre_tetto"] == 8 * 2      # 8 temi x (4 - 2)


def test_temi_e_top_headlines_passano_dai_candidati_nuovi(giro, monkeypatch):
    """Anche i temi (tetto max_per_theme) e le top-headlines escono se il feed le ha gia':
    per url (temi) e per titolo normalizzato (top-headlines, url diverso)."""
    giro.per_ticker["items"] = []
    tema = [_articolo(f"tema-{i}", "Zeta Daily") for i in range(3)]
    _nel_feed(_db_del_giro(), tema[0], tema[1],
              {"title": "Markets Rally Again", "url": "http://altrove.invalid/m", "source": "CNBC",
               "published_at": _iso(1)})
    monkeypatch.setattr(na, "search_news_global", lambda q, **k: [dict(i) for i in tema])
    monkeypatch.setattr(na, "_fetch_gnews_top", lambda *a, **k: [
        {"title": "markets rally again", "url": "http://esempio.invalid/top", "source": "CNBC",
         "published_at": _iso(1), "provider": "gnews"}])
    out = na.auto_pull_feed(classify=False, max_per_theme=1, language="it")
    r = out["ranking_testate"]
    # 8 temi: a ogni tema 2 gia' nel feed (url); il terzo entra al primo tema, poi e' gia' nel
    # giro; la top-headline esce per titolo
    assert r["oltre_tetto"] == 0, r
    assert r["gia_nel_feed"] == 8 * 2 + 1 and r["gia_nel_giro"] == 7, r
    assert out["doppioni_titolo"]["titolo_db"] == 1
    assert out["saved"] == 1
