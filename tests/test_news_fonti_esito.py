"""04/10 (B2, Opus 5.5) — aggregatore news: fonti mute DICHIARATE + Finnhub collegato.

Contratto C-A (scratchpad recon R1 §7.1):
  - Tiingo/Finnhub: l'esito dell'ultima chiamata entra in providers_blocked() col codice in
    testa (HTTP_401, ...) finche' e' piu' giovane di TTL_ESITO_FONTE_S; «mai interrogata» =
    nessuna voce; un live successivo guarisce.
  - Finnhub company-news solo per simboli US (niente «.» ne' «^»), provider «finnhub»
    minuscolo, published_at UTC con Z, fidato nel filtro per termini.
  - GNews: la chiamata `it` per i .MI ha una chiave di cooldown SUA (prima finiva sempre in
    SKIP_COOLDOWN); in cooldown si serve la cache condivisa (gnews_cache).
  - log dei fetcher: solo il TIPO dell'eccezione (str(e) = URL con la chiave).
  - auto_pull_feed: provenienza misurata + fonti_esito del giro, persistite; degraded vero
    con Tiingo/Finnhub muti; file vecchi senza chiavi -> n.d.
  - Reddit SPENTA per decisione PM 04/10: mai interrogato, dichiarato.
Ticker e numeri INVENTATI. Zero rete: requests e fetcher stubbati.
"""
import json
import types
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

import bellomberg.market_data.news_aggregator as na
from bellomberg.core.paths import EXAMPLES_DIR
from bellomberg.market_data import tiingo_news, gnews_cache, finnhub_news

SEGRETO = "QQSEGRETOFINTO123"
_FETCH_TIINGO_VERO = na._fetch_tiingo


class _Risposta:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _stato_pulito(tmp_path, monkeypatch):
    tiingo_news.reset_status()
    na.reset_esiti_fonti()
    na.invalidate_cache()
    monkeypatch.setattr(na, "_NEWS_RATE_PATH", str(tmp_path / "news_rate_state.json"))
    monkeypatch.setattr(na, "_MARKETAUX_STATE_PATH", str(tmp_path / "marketaux_state.json"))
    monkeypatch.setattr(gnews_cache, "GNEWS_CACHE_PATH", tmp_path / "gnews_cache.json")
    for attr in ("NEWSAPI_KEY", "THENEWSAPI_KEY", "GNEWS_KEY", "MARKETAUX_KEY"):
        monkeypatch.setattr(na, attr, "chiave-di-prova")
    monkeypatch.setattr(na, "PERCORSO_TERMINI", str(EXAMPLES_DIR / "news_search_terms.example.json"))
    monkeypatch.setattr(tiingo_news, "tiingo_available", lambda: True)
    monkeypatch.setattr(tiingo_news, "TIINGO_KEY", "chiave-finta-tiingo")
    # 10/10: Tiingo SPENTA per decisione PM; qui si prova il percorso DORMIENTE (riaccensione)
    monkeypatch.setattr(tiingo_news, "FONTE_SPENTA", False)
    # 10/10: top headlines GNews (passo 2.4 del giro) fuori da questi test: hanno i loro
    monkeypatch.setattr(na, "_fetch_gnews_top", lambda *a, **k: [])
    # rete VIETATA: qualunque requests.get non stubbato dal test fallisce rumorosamente
    def _niente_rete(*a, **k):
        raise AssertionError("rete vera tentata nel test: %r" % (a[:1],))
    monkeypatch.setattr(na.requests, "get", _niente_rete)
    yield
    tiingo_news.reset_status()
    na.reset_esiti_fonti()
    na.invalidate_cache()


@pytest.fixture
def log_na(monkeypatch):
    righe = []
    monkeypatch.setattr(na, "_log", lambda msg: righe.append(str(msg)))
    return righe


# ------------------------------------------------------------------ Tiingo

def _tiingo_risponde(monkeypatch, status, payload=None):
    monkeypatch.setattr(tiingo_news.requests, "get",
                        lambda *a, **k: _Risposta(status, payload, text="Unauthorized token=" + SEGRETO))


def test_tiingo_mai_interrogata_non_e_muta():
    assert "tiingo" not in na.providers_blocked()
    assert "finnhub" not in na.providers_blocked()


def test_tiingo_401_e_dichiarata_col_codice_in_testa(monkeypatch):
    _tiingo_risponde(monkeypatch, 401)
    assert na._fetch_tiingo(["ZZTEST"], 1, 5) == []
    fuori = na.providers_blocked()
    assert fuori["tiingo"].startswith("HTTP_401"), fuori
    assert SEGRETO not in fuori["tiingo"]


def test_tiingo_un_200_dopo_il_401_guarisce(monkeypatch):
    _tiingo_risponde(monkeypatch, 401)
    na._fetch_tiingo(None, 1, 5)
    assert "tiingo" in na.providers_blocked()
    _tiingo_risponde(monkeypatch, 200, [{"title": "t", "url": "http://esempio.invalid/1"}])
    assert len(na._fetch_tiingo(None, 1, 5)) == 1
    assert "tiingo" not in na.providers_blocked()


def test_tiingo_esito_piu_vecchio_del_ttl_sparisce(monkeypatch):
    _tiingo_risponde(monkeypatch, 403)
    na._fetch_tiingo(None, 1, 5)
    st = tiingo_news.last_status()
    giovane = st["mono"] + na.TTL_ESITO_FONTE_S - 60
    vecchio = st["mono"] + na.TTL_ESITO_FONTE_S + 1
    assert na._esito_muto_recente("tiingo", "Tiingo", st, ora_mono=giovane).startswith("HTTP_403")
    assert na._esito_muto_recente("tiingo", "Tiingo", st, ora_mono=vecchio) is None


def test_tiingo_eta_misurata_su_monotonic(monkeypatch):
    """Il wall-clock (`quando`) e' solo un'etichetta: spostarlo di un giorno non cambia nulla."""
    _tiingo_risponde(monkeypatch, 401)
    na._fetch_tiingo(None, 1, 5)
    st = tiingo_news.last_status()
    st["quando"] = "2000-01-01T00:00:00"
    assert na._esito_muto_recente("tiingo", "Tiingo", st) is not None


def test_tiingo_modulo_che_esplode_e_dichiarato(monkeypatch, log_na):
    def _esplode(*a, **k):
        raise RuntimeError("https://api.tiingo.com/tiingo/news?token=" + SEGRETO)
    monkeypatch.setattr(tiingo_news, "fetch_tiingo_news", _esplode)
    assert na._fetch_tiingo(None, 1, 5) == []
    assert na.esiti_fonti()["tiingo"]["stato"] == "ERRORE_RuntimeError"
    assert na.providers_blocked()["tiingo"].startswith("ERRORE_RuntimeError")
    assert any("RuntimeError" in r for r in log_na) and not any(SEGRETO in r for r in log_na)


# ------------------------------------------------------------------ Finnhub

def _finnhub_finto(monkeypatch, items=None, motivo_testo=None, chiamate=None):
    def _f(ticker, days=7, max_items=20, motivo=None):
        if chiamate is not None:
            chiamate.append(ticker)
        if motivo_testo and motivo is not None:
            motivo.append(motivo_testo)
        return list(items or [])
    monkeypatch.setattr(finnhub_news, "fetch_company_news", _f)


def test_finnhub_normalizzato_minuscolo_e_utc_z(monkeypatch):
    locale = datetime(2026, 1, 15, 10, 30, 0)   # naive = ora LOCALE (fromtimestamp)
    _finnhub_finto(monkeypatch, items=[{"title": "Titolo finto", "snippet": "s", "url": "http://esempio.invalid/f",
                                        "provider": "Finnhub", "source": "Fonte", "published_at": locale.isoformat(),
                                        "image": "x", "category": "company"}])
    out = na._fetch_finnhub_news("ZZTEST", 2, 5)
    assert len(out) == 1
    assert out[0]["provider"] == "finnhub"
    atteso = locale.astimezone().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert out[0]["published_at"] == atteso and atteso.endswith("Z")
    assert na.esiti_fonti()["finnhub"]["stato"] == "live"
    assert "finnhub" not in na.providers_blocked()


def test_finnhub_usa_l_epoch_utc_quando_c_e(monkeypatch, log_na):
    """B3 (RV-N P3): l'epoch originale vince sull'ora locale, anche se questa e' incoerente."""
    ep = 1768469400  # 2026-01-15T09:30:00Z
    _finnhub_finto(monkeypatch, items=[{"title": "T", "url": "http://esempio.invalid/e", "source": "F",
                                        "published_at": "1999-01-01T00:00:00", "published_epoch": ep}])
    out = na._fetch_finnhub_news("ZZTEST", 2, 5)
    assert out[0]["published_at"] == "2026-01-15T09:30:00Z"
    assert not any("PROXY" in r for r in log_na)


def test_finnhub_ora_ripetuta_del_25_10_distinta_dall_epoch(monkeypatch):
    """VF-N P3: nella notte del 25/10/2026 le 02:30 di Roma esistono DUE volte (00:30Z in CEST
    e 01:30Z in CET). Finnhub da' lo stesso published_at locale per entrambe; solo l'epoch le
    distingue. Due articoli con la stessa ora locale e epoch diversi devono uscire con due
    istanti UTC diversi: qualunque ricalcolo dall'ora locale li rende UGUALI, su qualunque
    fuso della macchina (il test non dipende da TZ)."""
    ep_cest = int(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).timestamp())
    ep_cet = int(datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc).timestamp())
    stessa_ora_locale = "2026-10-25T02:30:00"
    _finnhub_finto(monkeypatch, items=[
        {"title": "prima", "url": "http://esempio.invalid/r1", "source": "F",
         "published_at": stessa_ora_locale, "published_epoch": ep_cest},
        {"title": "seconda", "url": "http://esempio.invalid/r2", "source": "F",
         "published_at": stessa_ora_locale, "published_epoch": ep_cet}])
    out = {it["title"]: it["published_at"] for it in na._fetch_finnhub_news("ZZTEST", 2, 5)}
    assert out == {"prima": "2026-10-25T00:30:00Z", "seconda": "2026-10-25T01:30:00Z"}


def test_finnhub_epoch_none_data_vuota_dichiarata(monkeypatch, log_na):
    _finnhub_finto(monkeypatch, items=[{"title": "T", "url": "http://esempio.invalid/n", "source": "F",
                                        "published_at": "", "published_epoch": None}])
    out = na._fetch_finnhub_news("ZZTEST", 2, 5)
    assert out[0]["published_at"] == ""
    assert any("senza data" in r for r in log_na), log_na


def test_finnhub_senza_campo_epoch_e_proxy_dichiarato(monkeypatch, log_na):
    _finnhub_finto(monkeypatch, items=[{"title": "T", "url": "http://esempio.invalid/p", "source": "F",
                                        "published_at": "2026-01-15T10:30:00"}])
    na._fetch_finnhub_news("ZZTEST", 2, 5)
    assert any("PROXY" in r and "published_epoch assente" in r for r in log_na), log_na


@pytest.mark.parametrize("testo,codice", [
    ("401 unauthorized - check FINNHUB_API_KEY validity", "HTTP_401"),
    ("429 rate limited (60 req/min free tier)", "HTTP_429"),
    ("HTTP 503 on /company-news: down", "HTTP_503"),
    ("missing FINNHUB_API_KEY in .env", "SENZA_CHIAVE"),
])
def test_finnhub_muto_dichiarato_col_codice(monkeypatch, testo, codice):
    _finnhub_finto(monkeypatch, motivo_testo=testo)
    assert na._fetch_finnhub_news("ZZTEST", 2, 5) == []
    assert na.providers_blocked()["finnhub"].startswith(codice)


def test_finnhub_motivo_con_chiave_mascherato(monkeypatch):
    _finnhub_finto(monkeypatch, motivo_testo="error on /company-news: https://finnhub.io/x?token=" + SEGRETO)
    na._fetch_finnhub_news("ZZTEST", 2, 5)
    assert SEGRETO not in json.dumps(na.esiti_fonti())
    assert SEGRETO not in na.providers_blocked()["finnhub"]


def test_finnhub_esito_scaduto_sparisce(monkeypatch):
    _finnhub_finto(monkeypatch, motivo_testo="429 rate limited")
    na._fetch_finnhub_news("ZZTEST", 2, 5)
    assert "finnhub" in na.providers_blocked()
    with na._ESITI_LOCK:
        na._ESITI_FONTI["finnhub"]["mono"] -= na.TTL_ESITO_FONTE_S + 1
    assert "finnhub" not in na.providers_blocked()


@pytest.fixture
def ricerca_finta(monkeypatch):
    """Tutti i fetcher spenti; Tiingo e Finnhub contano le chiamate."""
    chiamate = {"tiingo": [], "finnhub": []}
    for nome in ("_fetch_newsapi", "_fetch_thenewsapi", "_fetch_gnews"):
        monkeypatch.setattr(na, nome, lambda *a, **k: [])
    monkeypatch.setattr(na, "_fetch_yfinance_news", lambda t, n: [])
    monkeypatch.setattr(na, "_all_rss_cached", lambda: [])
    # Integrazione G3: anche la risoluzione dell'identita' (Yahoo) e la scoperta del simbolo USA
    # sono rete: risposte vuote finte, come nel fixture provider_finti di test_news_search_terms.
    monkeypatch.setattr(na, "_nome_emittente_yahoo", lambda t: "")
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: types.SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"quotes": []}))
    monkeypatch.setattr(na, "_fetch_tiingo", lambda tk, d, l: chiamate["tiingo"].append(tk) or [])
    monkeypatch.setattr(na, "_fetch_finnhub_news",
                        lambda tk, d, n: chiamate["finnhub"].append(tk) or [
                            {"title": "Nulla a che vedere", "snippet": "", "url": "http://esempio.invalid/" + tk,
                             "source": "F", "provider": "finnhub", "published_at": "2026-01-01T00:00:00Z"}])
    return chiamate


def test_finnhub_e_tiingo_solo_per_simboli_usa(ricerca_finta):
    na.search_news_for_ticker("ZZTEST", days=1, max_per_source=2)
    na.search_news_for_ticker("QQSYN.MI", days=1, max_per_source=2)
    na.search_news_for_ticker("^ZZIDX", days=1, max_per_source=2)
    assert ricerca_finta["finnhub"] == ["ZZTEST"]
    assert ricerca_finta["tiingo"] == [["ZZTEST"]]


def test_finnhub_e_fidato_nel_filtro_per_termini(ricerca_finta):
    """Il titolo non contiene il ticker ne' i termini: Finnhub e' taggato per simbolo."""
    out = na.search_news_for_ticker("ZZTEST", days=1, max_per_source=2)
    assert [it["provider"] for it in out] == ["finnhub"]


# ------------------------------------------------------------------ GNews

def _gnews_200(monkeypatch, chiamate, articoli):
    def _get(url, params=None, timeout=None, **k):
        chiamate.append(dict(params or {}))
        return _Risposta(200, {"articles": articoli})
    monkeypatch.setattr(na.requests, "get", _get)


def _art(titolo, ore_fa=1):
    pub = (datetime.now(timezone.utc) - timedelta(hours=ore_fa)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"title": titolo, "url": "http://esempio.invalid/" + titolo, "publishedAt": pub,
            "description": "", "source": {"name": "Finta"}}


def test_gnews_it_ha_una_chiave_di_cooldown_sua(monkeypatch):
    chiamate = []
    _gnews_200(monkeypatch, chiamate, [_art("uno")])
    q = '"Qqsyn Manifatture Riunite Societa per Azioni Finte" OR QQSYN OR "Qqsyn Holding Fittizia"'
    assert len(q) > 80  # la lingua in CODA sarebbe tagliata da provider_status
    na._fetch_gnews(q, 1, 5)
    na._fetch_gnews(q, 1, 5, lang="it")
    assert [c["lang"] for c in chiamate] == ["en", "it"]


def test_gnews_in_cooldown_serve_la_cache_e_filtra_per_data(monkeypatch):
    chiamate = []
    _gnews_200(monkeypatch, chiamate, [_art("fresco", 2), _art("vecchio", 24 * 5)])
    prima = na._fetch_gnews("ZZTEST", 1, 5)
    # il 200 si mappa com'e' (filtra la API col parametro `from`); la cache in lettura filtra
    # per data, perche' puo' contenere articoli salvati da news_sources senza `from`
    assert len(chiamate) == 1 and len(prima) == 2
    seconda = na._fetch_gnews("ZZTEST", 1, 5)
    assert len(chiamate) == 1, "in cooldown non deve partire una seconda chiamata"
    assert [it["title"] for it in seconda] == ["fresco"]
    assert all("_gnews_cache_eta_s" in it for it in seconda)


def test_gnews_cooldown_senza_cache_e_dichiarato(monkeypatch, log_na):
    st = {"gnews": {"per_query": {"zztest": time.time()}}}
    with open(na._NEWS_RATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f)
    assert na._fetch_gnews("ZZTEST", 1, 5) == []
    assert any("SKIP_COOLDOWN" in r for r in log_na), log_na


# ------------------------------------------------------------------ chiavi nei log

@pytest.mark.parametrize("fetcher,nome", [("_fetch_newsapi", "newsapi"), ("_fetch_thenewsapi", "thenewsapi"),
                                          ("_fetch_gnews", "gnews"), ("_fetch_marketaux", "marketaux")])
def test_eccezione_dei_fetcher_nel_log_solo_col_tipo(monkeypatch, log_na, fetcher, nome):
    def _esplode(url, params=None, **k):
        raise ConnectionError(f"{url}?apiKey={SEGRETO}&api_token={SEGRETO}")
    monkeypatch.setattr(na.requests, "get", _esplode)
    assert getattr(na, fetcher)("ZZTEST", 1, 5) == []
    righe = [r for r in log_na if f"{nome} failed" in r]
    assert righe and "ConnectionError" in righe[0], log_na
    assert not any(SEGRETO in r for r in log_na)


def test_top_global_eccezione_nel_log_solo_col_tipo(monkeypatch, log_na):
    """Rilievo RV-N: get_top_global (GET /news/top) stampava str(e) = URL con apiKey."""
    def _esplode(url, params=None, **k):
        raise ConnectionError(f"{url}?apiKey={SEGRETO}")
    monkeypatch.setattr(na.requests, "get", _esplode)
    monkeypatch.setattr(na, "_fetch_rss", lambda *a, **k: [])
    assert na.get_top_global(limit=5) == []
    righe = [r for r in log_na if "top_global newsapi failed" in r]
    assert righe and "ConnectionError" in righe[0], log_na
    assert not any(SEGRETO in r for r in log_na)


def test_top_global_non_200_e_dichiarato(monkeypatch, log_na):
    monkeypatch.setattr(na.requests, "get", lambda *a, **k: _Risposta(429, {}, text="limit apiKey=" + SEGRETO))
    monkeypatch.setattr(na, "_fetch_rss", lambda *a, **k: [])
    assert na.get_top_global(limit=5) == []
    assert any("top_global newsapi HTTP 429" in r for r in log_na), log_na
    assert not any(SEGRETO in r for r in log_na)


# ------------------------------------------------------------------ auto_pull_feed

def _db_finto(tmp_path):
    p = tmp_path / "feed.db"
    with sqlite3.connect(p) as c:
        c.execute("""CREATE TABLE news_feed (id INTEGER PRIMARY KEY, title TEXT, snippet TEXT, source TEXT,
                     url TEXT UNIQUE, published_at TEXT, pulled_at TEXT, ticker_mentioned TEXT, theme TEXT,
                     provider TEXT, sentiment TEXT, sentiment_score REAL, relevance INTEGER,
                     notified INTEGER DEFAULT 0, headline_it TEXT, why_matters TEXT)""")
    return str(p)


@pytest.fixture
def giro_finto(tmp_path, monkeypatch):
    db_path = _db_finto(tmp_path)
    monkeypatch.setattr(na, "MemoryDB", lambda: NS(db_path=db_path, get_portfolio_summary=lambda: {"positions": []}))
    monkeypatch.setattr(na, "giro_news", lambda positions: (["ZZTEST"], []))
    monkeypatch.setattr(na, "_termini_del_giro", lambda contesto: {})
    monkeypatch.setattr(na, "_favorites_tickers", lambda: set())
    monkeypatch.setattr(na, "_save_summary", lambda *a, **k: None)
    monkeypatch.setattr(na, "search_news_global", lambda *a, **k: [])
    scritti = []
    monkeypatch.setattr(na, "_scrivi_stato_giro", lambda out: scritti.append(out))
    stato = {"finnhub": "live"}

    def _per_ticker(tk, days=1, max_per_source=5):
        na._registra_esito("finnhub", stato["finnhub"], "" if stato["finnhub"] == "live" else "429 rate limited")
        return [
            {"title": "a", "url": "http://esempio.invalid/a", "provider": "finnhub"},
            {"title": "b", "url": "http://esempio.invalid/b", "provider": "finnhub"},
            {"title": "c", "url": "http://esempio.invalid/c", "provider": "gnews", "_gnews_cache_eta_s": 12.0},
            {"title": "d", "url": "http://esempio.invalid/d", "provider": "gnews"},
        ]
    monkeypatch.setattr(na, "search_news_for_ticker", _per_ticker)
    monkeypatch.setattr(na, "_fetch_tiingo", lambda tk, d, l: [
        {"title": "t", "url": "http://esempio.invalid/t", "provider": "tiingo"}])
    return NS(scritti=scritti, stato=stato)


def test_auto_pull_misura_la_provenienza_e_gli_esiti(giro_finto, monkeypatch):
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    out = na.auto_pull_feed(classify=False, language="it")
    assert out["provenienza"] == {"finnhub": 2, "gnews": 1, "gnews (cache)": 1, "tiingo": 1}
    assert sum(out["provenienza"].values()) == out["fetched"]
    # Tiingo stubbato al wrapper: nessun esito NEL giro; Finnhub live
    assert out["fonti_esito"]["finnhub"] == "live"
    assert out["fonti_esito"]["tiingo"] == "non_interrogata"
    assert out["degraded"] is False
    assert giro_finto.scritti[0]["provenienza"] == out["provenienza"]
    assert giro_finto.scritti[0]["fonti_esito"] == out["fonti_esito"]


def test_auto_pull_degradato_con_finnhub_muto(giro_finto, monkeypatch):
    monkeypatch.setattr(na, "providers_blocked", lambda: {})  # isola il ramo degli esiti del giro
    giro_finto.stato["finnhub"] = "HTTP_429"
    out = na.auto_pull_feed(classify=False, language="it")
    assert out["fonti_esito"]["finnhub"] == "HTTP_429"
    assert out["degraded"] is True


def test_auto_pull_esito_anteriore_al_giro_non_e_del_giro(giro_finto, monkeypatch):
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    na._registra_esito("tiingo", "HTTP_401", "vecchio")
    out = na.auto_pull_feed(classify=False, language="it")
    assert out["fonti_esito"]["tiingo"] == "non_interrogata"


def test_auto_pull_tiingo_401_nel_giro_e_dichiarato(giro_finto, monkeypatch):
    """Il wrapper VERO di Tiingo (requests stubbato a 401): fonti_esito, degraded E
    providers_blocked (non stubbato qui) lo dicono."""
    monkeypatch.setattr(na, "_fetch_tiingo", _FETCH_TIINGO_VERO)
    _tiingo_risponde(monkeypatch, 401)
    out = na.auto_pull_feed(classify=False, language="it")
    assert out["fonti_esito"]["tiingo"] == "HTTP_401"
    assert out["providers_blocked"]["tiingo"].startswith("HTTP_401")
    assert out["degraded"] is True


def test_stato_ultimo_giro_file_vecchio_dichiara_nd(tmp_path):
    p = tmp_path / "news_feed_status.json"
    p.write_text(json.dumps({"timestamp": datetime.now().isoformat(), "esito": "ok", "fetched": 3,
                             "classified": 3, "saved": 3, "skipped_duplicates": 0,
                             "providers_blocked": {}}), encoding="utf-8")
    st = na.stato_ultimo_giro(str(p))
    assert st["provenienza"] == "n.d." and st["fonti_esito"] == "n.d."
    assert st["finnhub_tagli"] == "n.d." and st["doppioni_titolo"] == "n.d."


def test_stato_ultimo_giro_rilegge_le_chiavi_nuove(tmp_path):
    p = str(tmp_path / "news_feed_status.json")
    na._scrivi_stato_giro({"degraded": True, "fetched": 2, "provenienza": {"finnhub": 2},
                           "fonti_esito": {"tiingo": "HTTP_401", "finnhub": "live"},
                           "finnhub_tagli": {"scartati": 3, "troncati_dal_fornitore": []},
                           "doppioni_titolo": {"titolo": 2, "titolo_db": 1}}, path=p)
    st = na.stato_ultimo_giro(p)
    assert st["stato"] == "degradato"
    assert st["provenienza"] == {"finnhub": 2}
    assert st["fonti_esito"] == {"tiingo": "HTTP_401", "finnhub": "live"}
    assert st["finnhub_tagli"] == {"scartati": 3, "troncati_dal_fornitore": []}
    assert st["doppioni_titolo"] == {"titolo": 2, "titolo_db": 1}


# ------------------------------------------------------------------ Reddit SPENTA

def test_reddit_spenta_mai_interrogata_e_dichiarata(monkeypatch, log_na):
    from bellomberg.market_data import reddit_news

    def _vietato(*a, **k):
        raise AssertionError("Reddit interrogato nonostante la decisione PM 04/10")
    monkeypatch.setattr(reddit_news, "fetch_reddit_top", _vietato)
    monkeypatch.setattr(na, "_fetch_newsapi", lambda *a, **k: [])
    monkeypatch.setattr(na, "_fetch_gnews", lambda *a, **k: [])
    monkeypatch.setattr(na, "_fetch_rss", lambda *a, **k: [])
    na.fetch_macro_news(min_importance=1, include_reddit=True)
    assert any("reddit" in r and "SPENTA" in r for r in log_na), log_na
    assert na.fonti_spente()["reddit"].startswith("SPENTA")


def test_reddit_spenta_nel_giro_ma_non_degrada(giro_finto, monkeypatch):
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    out = na.auto_pull_feed(classify=False, language="it")
    assert out["fonti_esito"]["reddit"] == "SPENTA"
    assert out["degraded"] is False


# ------------------------------------------------------------------ Finnhub: tagli (misure M1)

def _n_articoli(n):
    base = datetime(2026, 1, 15, 12, 0, 0)
    return [{"title": "art %03d" % i, "snippet": "", "url": "http://esempio.invalid/n%d" % i,
             "source": "F", "published_at": (base - timedelta(minutes=i)).isoformat()} for i in range(n)]


def test_finnhub_tetto_per_ticker_tiene_i_piu_recenti_e_conta_gli_scarti(monkeypatch, log_na):
    grezzi = list(reversed(_n_articoli(40)))   # arrivano dal piu' vecchio: l'ordine lo rifa' il wrapper
    _finnhub_finto(monkeypatch, items=grezzi)
    na._azzera_tagli_finnhub()
    out = na._fetch_finnhub_news("ZZTEST", 1, 1000)
    assert len(out) == na.FINNHUB_MAX_PER_TICKER
    assert [it["title"] for it in out[:2]] == ["art 000", "art 001"]
    assert na._tagli_finnhub() == {"scartati": 40 - na.FINNHUB_MAX_PER_TICKER, "troncati_dal_fornitore": []}
    assert any("scartati %d" % (40 - na.FINNHUB_MAX_PER_TICKER) in r for r in log_na), log_na


def test_finnhub_troncato_dal_fornitore_e_dichiarato(monkeypatch, log_na):
    _finnhub_finto(monkeypatch, items=_n_articoli(na.FINNHUB_TETTO_FORNITORE))
    na._azzera_tagli_finnhub()
    na._fetch_finnhub_news("ZZTEST", 1, 5)
    assert na._tagli_finnhub()["troncati_dal_fornitore"] == ["ZZTEST"]
    assert any("TRONCATA dal fornitore" in r for r in log_na), log_na


def test_finnhub_sotto_il_tetto_del_fornitore_non_e_troncato(monkeypatch):
    _finnhub_finto(monkeypatch, items=_n_articoli(na.FINNHUB_TETTO_FORNITORE - 1))
    na._azzera_tagli_finnhub()
    na._fetch_finnhub_news("ZZTEST", 1, 5)
    assert na._tagli_finnhub()["troncati_dal_fornitore"] == []


def test_auto_pull_porta_i_tagli_finnhub_del_giro(giro_finto, monkeypatch):
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    _finnhub_finto(monkeypatch, items=_n_articoli(40))

    def _per_ticker(tk, days=1, max_per_source=5):
        return na._fetch_finnhub_news(tk, days, max_per_source)
    monkeypatch.setattr(na, "search_news_for_ticker", _per_ticker)
    na._FINNHUB_TAGLI_GIRO["scartati"] = 999   # residuo di un giro precedente: va azzerato
    out = na.auto_pull_feed(classify=False, language="it")
    assert out["finnhub_tagli"] == {"scartati": 35, "troncati_dal_fornitore": []}
    assert giro_finto.scritti[0]["finnhub_tagli"] == out["finnhub_tagli"]


# ------------------------------------------------------------------ rilievi RV-N (P2)

def test_giro_finnhub_parziale_non_e_live(giro_finto, monkeypatch):
    """P2-1: 429, 429, 200 su tre ticker -> l'ultimo esito e' live ma il giro e' PARZIALE."""
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    monkeypatch.setattr(na, "giro_news", lambda positions: (["ZZAAA", "ZZBBB", "ZZCCC"], []))
    sequenza = iter(["429 rate limited", "429 rate limited", None])

    def _f(ticker, days=7, max_items=20, motivo=None):
        m = next(sequenza)
        if m:
            motivo.append(m)
        return []
    monkeypatch.setattr(finnhub_news, "fetch_company_news", _f)
    monkeypatch.setattr(na, "search_news_for_ticker", lambda tk, days=1, max_per_source=5:
                        na._fetch_finnhub_news(tk, days, max_per_source))
    out = na.auto_pull_feed(classify=False, language="it")
    assert out["fonti_esito"]["finnhub"] == "PARZIALE: HTTP_429 2/3, live 1/3"
    assert out["degraded"] is True


def test_giro_tiingo_401_poi_200_e_parziale(giro_finto, monkeypatch):
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    monkeypatch.setattr(na, "_fetch_tiingo", _FETCH_TIINGO_VERO)
    risposte = iter([_Risposta(200, []), _Risposta(401, None, text="no")])
    monkeypatch.setattr(tiingo_news.requests, "get", lambda *a, **k: next(risposte))
    monkeypatch.setattr(na, "search_news_for_ticker", lambda tk, days=1, max_per_source=5:
                        na._fetch_tiingo([tk], days, max_per_source))
    out = na.auto_pull_feed(classify=False, language="it")
    assert out["fonti_esito"]["tiingo"].startswith("PARZIALE:"), out["fonti_esito"]
    assert "HTTP_401 1/2" in out["fonti_esito"]["tiingo"] and "live 1/2" in out["fonti_esito"]["tiingo"]


def test_tiingo_vuoto_sospetto_dichiarato(monkeypatch):
    """B1: VUOTO_SOSPETTO (feed generale 200 [] per SOGLIA chiamate) -> providers_blocked lo dice."""
    monkeypatch.setattr(tiingo_news.requests, "get", lambda *a, **k: _Risposta(200, []))
    for _ in range(tiingo_news.SOGLIA_VUOTI_GENERALI):
        na._fetch_tiingo(None, 1, 5)
    assert tiingo_news.last_status()["stato"] == "VUOTO_SOSPETTO"
    m = na.providers_blocked()["tiingo"]
    assert m.startswith("VUOTO_SOSPETTO") and "abbonamento" in m, m


def test_simboli_non_azionari_non_vanno_a_tiingo_ne_finnhub():
    for t in ("ZZC-USD", "ZZF=F", "ZZX/USDT", "QQSYN.MI", "^ZZIDX", ""):
        assert na._simbolo_usa(t) is False, t
    assert na._simbolo_usa("ZZTEST") is True


def test_doppione_finnhub_per_titolo_tiene_l_url_diretto():
    """P2-2: stesso articolo da Tiingo (url diretto) e Finnhub (redirect): resta Tiingo."""
    na._azzera_doppioni_giro()
    items = [
        {"title": "ZZTest Corp beats estimates", "provider": "finnhub",
         "url": "https://finnhub.io/api/news?id=123", "published_at": "2026-01-15T10:00:00Z"},
        {"title": "ZZTest Corp Beats Estimates!", "provider": "tiingo",
         "url": "https://esempio.invalid/zztest", "published_at": "2026-01-15T12:00:00Z"},
        {"title": "Altro titolo", "provider": "finnhub",
         "url": "https://finnhub.io/api/news?id=124", "published_at": "2026-01-15T10:00:00Z"},
    ]
    out = na._dedupe_titoli(items)
    assert [it["provider"] for it in out] == ["tiingo", "finnhub"]
    assert out[1]["title"] == "Altro titolo"
    assert na._doppioni_giro()["titolo"] == 1


def test_stesso_titolo_fuori_finestra_non_e_doppione():
    na._azzera_doppioni_giro()
    items = [{"title": "Rapporto settimanale ZZTEST", "url": "https://esempio.invalid/1",
              "published_at": "2026-01-01T10:00:00Z"},
             {"title": "Rapporto settimanale ZZTEST", "url": "https://finnhub.io/api/news?id=9",
              "published_at": "2026-01-08T10:00:00Z"}]
    assert len(na._dedupe_titoli(items)) == 2
    assert na._doppioni_giro()["titolo"] == 0


def test_ricerca_per_ticker_toglie_il_doppione_finnhub(monkeypatch):
    for nome in ("_fetch_newsapi", "_fetch_thenewsapi", "_fetch_gnews"):
        monkeypatch.setattr(na, nome, lambda *a, **k: [])
    monkeypatch.setattr(na, "_fetch_yfinance_news", lambda t, n: [])
    monkeypatch.setattr(na, "_all_rss_cached", lambda: [])
    monkeypatch.setattr(na, "_nome_emittente_yahoo", lambda t: "")   # integrazione G3: identita' = rete
    monkeypatch.setattr(na, "_fetch_tiingo", lambda tk, d, l: [
        {"title": "ZZTest sale", "url": "https://esempio.invalid/a", "provider": "tiingo",
         "published_at": "2026-01-15T10:00:00Z"}])
    monkeypatch.setattr(na, "_fetch_finnhub_news", lambda tk, d, n: [
        {"title": "ZZTest sale", "url": "https://finnhub.io/api/news?id=1", "provider": "finnhub",
         "published_at": "2026-01-15T09:00:00Z"}])
    out = na.search_news_for_ticker("ZZTEST", days=1, max_per_source=2)
    assert [it["provider"] for it in out] == ["tiingo"]


def test_auto_pull_scarta_finnhub_gia_nel_feed_e_conta(giro_finto, monkeypatch):
    """P2-2 nel giro: il titolo era gia' nel feed (giro precedente, altra fonte): niente
    seconda riga ne' seconda notifica."""
    monkeypatch.setattr(na, "providers_blocked", lambda: {})
    db_path = na.MemoryDB().db_path
    with sqlite3.connect(db_path) as c:
        c.execute("INSERT INTO news_feed (title, url, provider, pulled_at) VALUES (?, ?, ?, datetime('now'))",
                  ("ZZTest sale!", "https://esempio.invalid/vecchio", "tiingo"))
    monkeypatch.setattr(na, "search_news_for_ticker", lambda tk, days=1, max_per_source=5: [
        {"title": "zztest SALE", "url": "https://finnhub.io/api/news?id=77", "provider": "finnhub"}])
    monkeypatch.setattr(na, "_fetch_tiingo", lambda tk, d, l: [])
    out = na.auto_pull_feed(classify=False, language="it")
    assert out["saved"] == 0 and out["skipped_duplicates"] == 1
    assert out["doppioni_titolo"] == {"titolo": 0, "titolo_db": 1}
    assert giro_finto.scritti[0]["doppioni_titolo"] == out["doppioni_titolo"]


def test_gnews_non_200_corpo_mascherato_nel_log(monkeypatch, log_na):
    """P2-3 (buco trovato dal banco RV-N): il corpo del non-200 di GNews puo' riecheggiare la chiave."""
    monkeypatch.setattr(na.requests, "get",
                        lambda *a, **k: _Risposta(403, None, text="forbidden apikey=" + SEGRETO))
    assert na._fetch_gnews("ZZTEST", 1, 5) == []
    righe = [r for r in log_na if "gnews HTTP 403" in r]
    assert righe, log_na
    assert not any(SEGRETO in r for r in log_na)
