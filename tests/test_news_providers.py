"""Test OFFLINE voce F8 news providers (§9-vicies n.1, COME ok PM 25/07 sera).

Copre il motore riusato (providers_blocked su stato file temporaneo) e il
contratto dei due payload nuovi: GET /news/providers e i campi fonti_mute/
avviso su GET /news/macro (pattern voce (25), consumato dalla UI F8 che
tratta "chiave presente anche se null" come dichiarazione).

Zero rete: fetch_macro_news e' stubbato; il rate-limiter legge un file
di stato in tmp_path (mai quello vero in data/).
"""
import json
import time

import pytest

import bellomberg.market_data.news_aggregator as na
import bellomberg.api.bellomberg_api as api
from bellomberg.core.paths import EXAMPLES_DIR

REPO = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _chiavi_e_negozio_presenti(monkeypatch):
    """05/09 (lotto fonti mute, chat ba): providers_blocked() dichiara ANCHE le chiavi assenti e il
    negozio dei termini assente. Questa batteria misura budget e auto-disable: chiavi e negozio
    si iniettano presenti, cosi' i `== {}` valgono anche su un clone senza .env."""
    import os
    from bellomberg.market_data import tiingo_news
    for attr in ("NEWSAPI_KEY", "THENEWSAPI_KEY", "GNEWS_KEY"):
        monkeypatch.setattr(na, attr, "chiave-di-prova")
    monkeypatch.setattr(tiingo_news, "tiingo_available", lambda: True)
    monkeypatch.setattr(na, "PERCORSO_TERMINI", str(EXAMPLES_DIR / "news_search_terms.example.json"))


# ---------------------------------------------------------------
# helpers
# ---------------------------------------------------------------

def _write_state(tmp_path, monkeypatch, state):
    p = tmp_path / "news_rate_state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(na, "_NEWS_RATE_PATH", str(p))
    return p


def _today():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------
# providers_blocked: il motore riusato da entrambi gli endpoint
# ---------------------------------------------------------------

def test_blocked_vuoto_a_stato_pulito(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, {})
    assert na.providers_blocked() == {}


def test_blocked_file_assente_fail_open_dichiarato(tmp_path, monkeypatch):
    # File mai scritto: il limiter non SA nulla -> nessun provider dichiarato
    # muto (fail-open documentato in provider_status, non "sano").
    monkeypatch.setattr(na, "_NEWS_RATE_PATH", str(tmp_path / "inesistente.json"))
    assert na.providers_blocked() == {}


def test_blocked_budget_esaurito_oggi(tmp_path, monkeypatch):
    # tetto letto dalla costante, non hardcoded: e' gia' cambiato una volta (16/07)
    _write_state(tmp_path, monkeypatch,
                 {"newsapi": {"day": _today(),
                              "count": na.NEWS_PROVIDER_LIMITS["newsapi"]["daily"]}})
    assert na.providers_blocked() == {"newsapi": "SKIP_BUDGET"}


def test_blocked_budget_ieri_non_conta(tmp_path, monkeypatch):
    # 80/80 IERI: il giorno e' cambiato, il budget riparte -> non muto.
    _write_state(tmp_path, monkeypatch,
                 {"thenewsapi": {"day": "2000-01-01", "count": 80}})
    assert na.providers_blocked() == {}


def test_blocked_disabled_futuro_e_passato(tmp_path, monkeypatch):
    now = time.time()
    _write_state(tmp_path, monkeypatch, {
        "gnews":   {"disabled_until": now + 3600},   # ancora spento
        "newsapi": {"disabled_until": now - 3600},   # scaduto da solo
    })
    assert na.providers_blocked() == {"gnews": "SKIP_DISABLED"}


def test_blocked_disabled_vince_sul_budget(tmp_path, monkeypatch):
    # provider_status controlla disabled_until PRIMA del budget.
    _write_state(tmp_path, monkeypatch, {
        "newsapi": {"day": _today(),
                    "count": na.NEWS_PROVIDER_LIMITS["newsapi"]["daily"],
                    "disabled_until": time.time() + 3600},
    })
    assert na.providers_blocked() == {"newsapi": "SKIP_DISABLED"}


def test_blocked_ignora_cooldown_per_query(tmp_path, monkeypatch):
    # Il cooldown e' per-QUERY, non spegne il provider: la sonda di
    # providers_blocked usa una query mai vista apposta.
    _write_state(tmp_path, monkeypatch, {
        "gnews": {"day": _today(), "count": 5,
                  "per_query": {"fed rates": time.time()}},
    })
    assert na.providers_blocked() == {}


# ---------------------------------------------------------------
# GET /news/providers (funzione endpoint chiamata diretta, zero httpx)
# ---------------------------------------------------------------

def test_endpoint_providers_stato_pulito(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, {})
    out = api.get_news_providers()
    # contratto UI F8 (takeFonti): chiave PRESENTE anche se null = dichiarazione
    assert "fonti_mute" in out and out["fonti_mute"] is None
    assert "avviso" in out and out["avviso"] is None
    assert out["providers_contingentati"] == sorted(na.NEWS_PROVIDER_LIMITS)
    assert "non misurati qui" in out["nota"]
    assert out["timestamp"]


def test_endpoint_providers_con_muti(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, {
        "newsapi":    {"day": _today(),
                       "count": na.NEWS_PROVIDER_LIMITS["newsapi"]["daily"]},
        "thenewsapi": {"disabled_until": time.time() + 3600},
    })
    out = api.get_news_providers()
    assert out["fonti_mute"] == {"newsapi": "SKIP_BUDGET",
                                 "thenewsapi": "SKIP_DISABLED"}
    assert "PARZIALE" in out["avviso"]
    assert "newsapi" in out["avviso"] and "thenewsapi" in out["avviso"]


# ---------------------------------------------------------------
# GET /news/macro: campi fonti_mute/avviso additivi (pattern (25))
# ---------------------------------------------------------------

_FINTI = [{"title": "Fed cuts", "url": "http://x/1"},
          {"title": "EM rally", "url": "http://x/2"}]


def test_macro_dichiara_muti(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch,
                 {"gnews": {"day": _today(),
                            "count": na.NEWS_PROVIDER_LIMITS["gnews"]["daily"]}})
    monkeypatch.setattr(na, "fetch_macro_news", lambda **kw: list(_FINTI))
    out = api.get_news_macro(categories="rates, em")
    assert out["count"] == 2 and out["items"] == _FINTI
    assert out["categories_requested"] == ["rates", "em"]
    assert out["fonti_mute"] == {"gnews": "SKIP_BUDGET"}
    assert "PARZIALE" in out["avviso"] and "gnews" in out["avviso"]


def test_macro_chiavi_presenti_anche_senza_muti(tmp_path, monkeypatch):
    # A stato pulito i campi restano PRESENTI con valore None: per la UI
    # "assente" = endpoint che non dichiara, "None" = nessun provider muto.
    _write_state(tmp_path, monkeypatch, {})
    monkeypatch.setattr(na, "fetch_macro_news", lambda **kw: [])
    out = api.get_news_macro()
    assert "fonti_mute" in out and out["fonti_mute"] is None
    assert "avviso" in out and out["avviso"] is None
    assert out["count"] == 0 and out["categories_requested"] is None
