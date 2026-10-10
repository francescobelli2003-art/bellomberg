import json

import pytest

from bellomberg.market_data import news_aggregator as na


class _Block:
    def __init__(self, text=None, kind="text"):
        self.type = kind
        self.text = text


class _Response:
    def __init__(self, blocks):
        self.content = blocks


COSTRUTTORI = []


def _install_client(monkeypatch, responses):
    from bellomberg.core import llm_client

    class FakeClient:
        def __init__(self, *args, **kwargs):
            COSTRUTTORI.append(kwargs)
            self.messages = self
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(llm_client, "OpenRouterClient", FakeClient)


@pytest.mark.parametrize("payload", [
    '{"sentiment":"bullish","relevance":8}',
    '```json\n{"sentiment":"bearish","relevance":3}\n```',
    'reasoning: I considered several cases.\n{"sentiment":"neutral","relevance":5}',
])
def test_classifier_extracts_json_from_textual_output(monkeypatch, payload):
    _install_client(monkeypatch, [_Response([_Block("private reasoning", "reasoning"), _Block(payload)])])
    result = na._classify_with_haiku({"title": "T", "snippet": "S"}, ["ACME (1%)"])
    assert result["_classification_status"] == "classified"
    assert result["sentiment"] in {"bullish", "bearish", "neutral"}


def test_classifier_returns_measured_failure_when_no_json_exists(monkeypatch):
    _install_client(monkeypatch, [_Response([_Block("not JSON at all")])])
    result = na._classify_with_haiku({"title": "T", "snippet": "S"}, [])
    assert result["_classification_status"] == "failed"
    assert result["sentiment"] == "neutral"


@pytest.mark.parametrize("status", [429, 529, 500, 503])
def test_classifier_un_solo_livello_di_retry(monkeypatch, status):
    """REV_G3 R3: i retry li fa il client OpenRouter (max_retries, timeout dichiarato);
    il classificatore non ci gira sopra un secondo ciclo (fino a 12 POST per notizia)."""
    class Failure(Exception):
        status_code = status

    risposte = [Failure("retry")] + [_Response([_Block(json.dumps({"sentiment": "neutral", "relevance": 4}))])]
    _install_client(monkeypatch, risposte)
    monkeypatch.setattr(na.time, "sleep", lambda _seconds: pytest.fail("secondo livello di retry"))
    result = na._classify_with_haiku({"title": "T", "snippet": "S"}, [])
    assert result["_classification_status"] == "failed"
    assert len(risposte) == 1                       # una sola chiamata consumata
    assert COSTRUTTORI[-1] == {"timeout": na.CLASSIFIER_TIMEOUT_S}


def test_classifier_does_not_retry_permanent_status(monkeypatch):
    class Failure(Exception):
        status_code = 400

    _install_client(monkeypatch, [Failure("bad request")])
    monkeypatch.setattr(na.time, "sleep", lambda _seconds: pytest.fail("unexpected retry"))
    result = na._classify_with_haiku({"title": "T", "snippet": "S"}, [])
    assert result["_classification_status"] == "failed"


def test_feed_counts_partial_classification_and_keeps_valid_items(tmp_path, monkeypatch):
    from bellomberg.storage.memory_db import MemoryDB

    db = MemoryDB(db_path=str(tmp_path / "feed.db"), chroma_path=str(tmp_path / "chroma"))
    monkeypatch.setattr(na, "MemoryDB", lambda: db)
    monkeypatch.setattr(na, "giro_news", lambda _positions: (["ACME"], ["ACME (1%)"]))
    monkeypatch.setattr(na, "_termini_del_giro", lambda _source: {})
    monkeypatch.setattr(na, "_favorites_tickers", lambda: set())
    monkeypatch.setattr(na, "search_news_for_ticker", lambda *_args, **_kwargs: [
        {"title": "one", "url": "https://example.invalid/1"},
        {"title": "two", "url": "https://example.invalid/2"},
    ])
    monkeypatch.setattr(na, "search_news_global", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(na, "_drop_junk", lambda items: items)
    monkeypatch.setattr(na, "_dedupe", lambda items: items)
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    monkeypatch.setattr(na, "_scrivi_stato_giro", lambda _out: None)
    monkeypatch.setattr(na, "_fetch_tiingo", lambda *_args: [])   # feed generale (passo 2.5): rete
    monkeypatch.setattr(na, "_fetch_gnews_top", lambda *_a, **_k: [])   # 10/10 passo 2.4: rete
    results = iter([
        {"sentiment": "neutral", "sentiment_score": 0, "relevance": 5,
         "headline_it": "", "why_matters": "", "_classification_status": "classified"},
        {"sentiment": "neutral", "sentiment_score": 0, "relevance": 5,
         "headline_it": "", "why_matters": "", "_classification_status": "failed"},
    ])
    monkeypatch.setattr(na, "_classify_with_haiku", lambda *_args: next(results))

    out = na.auto_pull_feed(days=1, classify=True, max_per_ticker=2, max_per_theme=1)
    assert out["classification_attempted"] == 2
    assert out["classified"] == 1
    assert out["classification_failed"] == 1
    assert out["saved"] == 2
    assert out["degraded"] is True


# ---------------------------------------------------------------- G3 (04/10/2026)
# Una classificazione fallita non entra piu' come «neutral/5» indistinguibile: classified=0,
# sentiment/score/relevance NULL, conteggio nel risultato del giro e in /news/feed.
def _giro_finto(tmp_path, monkeypatch, esiti, n=2):
    from bellomberg.storage.memory_db import MemoryDB

    db = MemoryDB(db_path=str(tmp_path / "feed.db"), chroma_path=str(tmp_path / "chroma"))
    monkeypatch.setattr(na, "MemoryDB", lambda: db)
    monkeypatch.setattr(na, "giro_news", lambda _positions: (["ACME"], ["ACME (1%)"]))
    monkeypatch.setattr(na, "_termini_del_giro", lambda _source: {})
    monkeypatch.setattr(na, "_favorites_tickers", lambda: set())
    monkeypatch.setattr(na, "_portfolio_weights_cached", lambda: {})
    monkeypatch.setattr(na, "search_news_for_ticker", lambda *_args, **_kwargs: [
        {"title": "titolo %d" % i, "url": "https://example.invalid/%d" % i} for i in range(1, n + 1)])
    monkeypatch.setattr(na, "search_news_global", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(na, "_drop_junk", lambda items: items)
    monkeypatch.setattr(na, "_dedupe", lambda items: items)
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    monkeypatch.setattr(na, "_scrivi_stato_giro", lambda _out: None)
    # il feed generale Tiingo (passo 2.5 di auto_pull_feed) passa dal wrapper anche con
    # search_news_* finti: con la chiave del .env interrogava api.tiingo.com davvero
    monkeypatch.setattr(na, "_fetch_tiingo", lambda *_args: [])
    monkeypatch.setattr(na, "_fetch_gnews_top", lambda *_a, **_k: [])   # 10/10 passo 2.4: rete
    it = iter(esiti)
    monkeypatch.setattr(na, "_classify_with_haiku", lambda *_args: next(it))
    return db


def _righe(db):
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        return {r[0]: r[1:] for r in conn.execute(
            "SELECT url, sentiment, sentiment_score, relevance, classified FROM news_feed")}


OK_CLS = {"sentiment": "bullish", "sentiment_score": 0.4, "relevance": 8,
          "headline_it": "", "why_matters": "", "_classification_status": "classified"}


def test_classificazione_fallita_salvata_come_non_classificata(tmp_path, monkeypatch):
    db = _giro_finto(tmp_path, monkeypatch, [OK_CLS, na._classification_result("failed", "x")])
    out = na.auto_pull_feed(days=1, classify=True, max_per_ticker=2, max_per_theme=1)
    assert out["not_classified"] == 1 and out["degraded"] is True
    righe = _righe(db)
    assert righe["https://example.invalid/1"] == ("bullish", 0.4, 8, 1)
    assert righe["https://example.invalid/2"] == (None, None, None, 0)   # niente neutral/5 finto
    feed = {r["url"]: r for r in na.get_feed(limit=10)}
    assert feed["https://example.invalid/1"]["classification_status"] == "classified"
    assert feed["https://example.invalid/2"]["classification_status"] == "not_classified"
    assert feed["https://example.invalid/2"]["relevance"] is None
    assert "classified" not in feed["https://example.invalid/2"]
    # Decisione PM (04/10): il filtro di rilevanza NON scarta le non classificate: restano,
    # marcate, senza un valore di rilevanza inventato.
    filtrate = {r["url"]: r for r in na.get_feed(limit=10, min_relevance=5)}
    assert set(filtrate) == {"https://example.invalid/1", "https://example.invalid/2"}
    assert filtrate["https://example.invalid/2"]["classification_status"] == "not_classified"
    assert filtrate["https://example.invalid/2"]["relevance"] is None
    assert [r["url"] for r in na.get_feed(limit=10, min_relevance=9)] == ["https://example.invalid/2"]


def test_giro_senza_classificazione_marca_tutto_non_classificato(tmp_path, monkeypatch):
    db = _giro_finto(tmp_path, monkeypatch, [])
    out = na.auto_pull_feed(days=1, classify=False, max_per_ticker=2, max_per_theme=1)
    assert out["saved"] == 2 and out["not_classified"] == 2
    assert set(_righe(db).values()) == {(None, None, None, 0)}


def test_righe_precedenti_hanno_stato_unknown(tmp_path, monkeypatch):
    import sqlite3
    db = _giro_finto(tmp_path, monkeypatch, [])
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("INSERT INTO news_feed (title, url, sentiment, relevance) "
                     "VALUES ('vecchia', 'https://example.invalid/v', 'neutral', 5)")
    assert na.get_feed(limit=5)[0]["classification_status"] == "unknown"


def test_migrazione_aggiunge_la_colonna_classified(tmp_path):
    import sqlite3
    from bellomberg.storage.memory_db import MemoryDB

    path = tmp_path / "vecchio.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE news_feed (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                     "snippet TEXT, source TEXT, url TEXT UNIQUE, published_at TEXT, pulled_at TEXT, "
                     "ticker_mentioned TEXT, theme TEXT, provider TEXT, sentiment TEXT, sentiment_score REAL, "
                     "relevance INTEGER, notified INTEGER DEFAULT 0, headline_it TEXT, why_matters TEXT)")
    MemoryDB(db_path=str(path), chroma_path=str(tmp_path / "chroma"))
    with sqlite3.connect(path) as conn:
        cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(news_feed)")}
    assert cols["classified"] == "INTEGER"


def test_should_stop_interrompe_il_giro_e_lo_dichiara(tmp_path, monkeypatch):
    db = _giro_finto(tmp_path, monkeypatch, [OK_CLS, OK_CLS, OK_CLS], n=3)
    chiamate = []

    def ferma():
        chiamate.append(1)
        return len(_righe(db)) >= 1          # fermati dopo la prima notizia salvata

    out = na.auto_pull_feed(days=1, classify=True, max_per_ticker=3, max_per_theme=1, should_stop=ferma)
    assert out["interrupted"] is True and out["degraded"] is True
    assert out["saved"] == 1


# Coordinatore G3: nei log e negli stati esposti solo tipo + stato HTTP, mai l'URL con la chiave.
CHIAVE = "CHIAVEFINTAzz9876543210"


class _ErroreConUrl(Exception):
    def __init__(self, status=None):
        super().__init__("HTTPSConnectionPool: Max retries exceeded with url: /v2/everything?q=ACME&apiKey="
                         + CHIAVE)
        if status is not None:
            self.response = type("R", (), {"status_code": status})()


def test_log_dei_provider_senza_chiave(monkeypatch, capsys):
    import bellomberg.market_data.news_sources as ns

    monkeypatch.setattr(na, "NEWSAPI_KEY", CHIAVE)
    monkeypatch.setattr(na, "_provider_allowed", lambda *a: True)
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: (_ for _ in ()).throw(_ErroreConUrl()))
    assert na._fetch_newsapi("ACME") == []
    assert na._eccezione_sicura(_ErroreConUrl(429)).startswith("_ErroreConUrl (HTTP 429): ")
    assert CHIAVE not in na._eccezione_sicura(_ErroreConUrl(429))
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: (_ for _ in ()).throw(_ErroreConUrl(401)))
    for nome in ("MARKETAUX_KEY", "THENEWSAPI_KEY", "GNEWS_KEY", "MARKETAUX_API_KEY", "THENEWSAPI_API_KEY",
                 "GNEWS_API_KEY"):
        if hasattr(ns, nome):
            monkeypatch.setattr(ns, nome, CHIAVE)
    monkeypatch.setattr(na, "provider_status", lambda *a, **k: None)
    monkeypatch.setattr(na, "_provider_record", lambda *a, **k: None)
    for fn in ("fetch_marketaux", "fetch_thenewsapi", "fetch_gnews"):
        if hasattr(ns, fn):
            getattr(ns, fn)("ACME")
    log = capsys.readouterr().out
    assert CHIAVE not in log and "apiKey" not in log
    assert "_ErroreConUrl" in log


def test_motivo_finnhub_esposto_senza_chiave(monkeypatch):
    import bellomberg.market_data.finnhub_news as fh

    monkeypatch.setattr(fh, "fetch_company_news",
                        lambda *a, **k: (_ for _ in ()).throw(_ErroreConUrl(403)))
    monkeypatch.setattr(na, "_voce_termini", lambda t: (["Acme"], "voce", {}))
    monkeypatch.setattr(na, "_simbolo_news", lambda t, nome: ("ACME", "test"))
    monkeypatch.setattr(na, "_all_rss_cached", lambda *a, **k: [])
    # _fetch_finnhub_news resta VERO (e' lui sotto prova, sopra fetch_company_news finto)
    for fn in ("_fetch_newsapi", "_fetch_thenewsapi", "_fetch_gnews", "_fetch_yfinance_news", "_fetch_tiingo"):
        monkeypatch.setattr(na, fn, lambda *a, **k: [])
    na._RUNTIME_PROVIDER_FAILURES.clear()
    na.search_news_for_ticker("ACME", days=1, max_per_source=1)
    motivi = " ".join(str(v) for v in na._runtime_failures_correnti().values())
    assert "finnhub" in na._runtime_failures_correnti()            # il guasto resta dichiarato
    assert CHIAVE not in motivi and "apiKey" not in motivi and "HTTP 403" in motivi
    na._RUNTIME_PROVIDER_FAILURES.clear()


def test_tripwire_rete_dei_test_notizie(niente_rete_notizie):
    """G3 difetto 6: la scoperta del simbolo USA interroga Yahoo con requests: nei test notizie
    la rete e' ferma e la violazione e' registrata (qui la si legge e si svuota)."""
    import yfinance

    assert na._ticker_us_yahoo_univoco("ACME.DE", "Acme Industries AG") is None
    assert na._nome_emittente_yahoo("ACME.DE") == ""          # yfinance fermato anche con curl_cffi
    viste = list(niente_rete_notizie)
    niente_rete_notizie.clear()
    assert "query2.finance.yahoo.com" in viste and "yfinance.Ticker" in viste
    assert yfinance.Ticker is not None


def test_briefing_non_classificate_in_coda_presenti_e_marcate(tmp_path, monkeypatch):
    """Decisione PM (04/10): nel briefing le non classificate restano, in coda e marcate."""
    import sqlite3
    from datetime import datetime
    from bellomberg.cli import briefing_engine as be
    import bellomberg.storage.memory_db as mdb

    db = _giro_finto(tmp_path, monkeypatch, [])
    adesso = datetime.now().isoformat()
    with sqlite3.connect(db.db_path) as conn:
        # la non classificata ha un tema macro: senza il riordino entrerebbe PRIMA (blocco macro)
        for titolo, tema, sent, rel, cls in (("senza voto", "fed", None, None, 0), ("con voto", "", "bullish", 3, 1)):
            conn.execute("INSERT INTO news_feed (title, url, pulled_at, theme, sentiment, relevance, classified) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (titolo, "https://example.invalid/" + titolo.replace(" ", "-"),
                                                          adesso, tema, sent, rel, cls))
    monkeypatch.setattr(mdb, "MemoryDB", lambda: db)
    # i fatti correnti del prompt leggono FRED dal vivo: fuori tema qui, e sarebbe rete vera
    import bellomberg.core.current_facts as cf
    monkeypatch.setattr(cf, "current_facts_block", lambda *a, **k: "(fatti correnti finti)")
    news = be._fetch_recent_news(lookback_h=8, limit=10)
    assert [n["title"] for n in news] == ["con voto", "senza voto"]          # in coda, ma presente
    assert news[1]["classification_status"] == "not_classified" and news[1]["relevance"] is None
    assert news[0]["classification_status"] == "classified"
    prompt = be._build_briefing_prompt("morning", news, {}, {}, language="it")
    riga = next(r for r in prompt.splitlines() if "senza voto" in r)
    assert "non classificata" in riga and "rel5" not in riga and "neut" not in riga
    prompt_en = be._build_briefing_prompt("morning", news, {}, {}, language="en")
    assert "unclassified" in next(r for r in prompt_en.splitlines() if "senza voto" in r)


@pytest.mark.parametrize("payload,campo", [
    ('{"relevance": 8}', "sentiment"),
    ('{"sentiment": "bullish"}', "relevance"),
    ('{"sentiment": "forse", "relevance": 8}', "sentiment"),
    ('{"sentiment": "bearish", "relevance": "alta"}', "relevance"),
    ('{"sentiment": "bearish", "relevance": 42}', "relevance"),
])
def test_classificazione_con_campi_mancanti_e_fallita(monkeypatch, payload, campo):
    """REV_G3 R5: un JSON senza i campi della classificazione non diventa neutral/5 «classificata»."""
    _install_client(monkeypatch, [_Response([_Block(payload)])])
    out = na._classify_with_haiku({"title": "t", "snippet": "s"}, ["ZZTEST (1%)"])
    assert out["_classification_status"] == "failed"
    assert campo in out.get("_classification_error", "")


def test_sentiment_score_assente_resta_none(monkeypatch):
    _install_client(monkeypatch, [_Response([_Block('{"sentiment": "bullish", "relevance": 7}')])])
    out = na._classify_with_haiku({"title": "t", "snippet": "s"}, ["ZZTEST (1%)"])
    assert out["_classification_status"] == "classified"
    assert out["relevance"] == 7 and out["sentiment_score"] is None      # nessuno 0 inventato


def test_briefing_righe_precedenti_marcate_non_verificate(tmp_path, monkeypatch):
    """REV_G3 R6: le righe di prima (classified NULL) possono essere neutral/5 finti."""
    import sqlite3
    from datetime import datetime
    from bellomberg.cli import briefing_engine as be
    import bellomberg.storage.memory_db as mdb

    db = _giro_finto(tmp_path, monkeypatch, [])
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("INSERT INTO news_feed (title, url, pulled_at, theme, sentiment, relevance, classified) "
                     "VALUES ('zzprecedente', 'https://example.invalid/zzprecedente', ?, '', 'neutral', 5, NULL)",
                     (datetime.now().isoformat(),))
    monkeypatch.setattr(mdb, "MemoryDB", lambda: db)
    # come sopra: niente FRED dal vivo (prima passava solo se la cache di current_facts
    # era gia' piena da un test precedente)
    import bellomberg.core.current_facts as cf
    monkeypatch.setattr(cf, "current_facts_block", lambda *a, **k: "(fatti correnti finti)")
    news = be._fetch_recent_news(lookback_h=8, limit=10)
    assert news[0]["classification_status"] == "unknown"
    riga = next(r for r in be._build_briefing_prompt("morning", news, {}, {}, language="it").splitlines()
                if "zzprecedente" in r)
    assert "non verificata" in riga
