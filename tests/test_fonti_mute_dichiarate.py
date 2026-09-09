# -*- coding: utf-8 -*-
"""Le news a book vuoto dichiarano le fonti mute NEL PAYLOAD (05/09, Fable 5.1, chat ba —
criterio (1), trovato da b6 col collaudo dello sconosciuto).

Su un clone senza chiavi `GET /news/ticker/{t}` e `/news/portfolio` rispondevano `count 0,
items [], fonti_mute null, avviso null`: le fonti senza chiave e il negozio dei termini
assente erano dichiarati SOLO nel log. Regola PM 14/07: «0 news» e «sono cieco» non sono lo
stesso valore. Il produttore e' `news_aggregator.providers_blocked()` ({fonte: motivo}, che
le rotte gia' leggono): oggi rende SOLO le cause di budget/auto-disable; qui si pretende che
renda anche la CHIAVE ASSENTE (col nome della variabile del .env, cosi' chi clona sa cosa
mettere), Tiingo senza chiave, e lo stato del negozio dei termini (assente/illeggibile).

Zero rete: le ricerche sono stubbate; il rate-limiter legge un file di stato in tmp_path.
"""
import os

import pytest

import bellomberg.market_data.news_aggregator as na

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESEMPIO_TERMINI = os.path.join(REPO, 'src/bellomberg/resources/examples/news_search_terms.example.json')
CHIAVI = (("newsapi", "NEWSAPI_KEY", "NEWS_API_KEY"),
          ("thenewsapi", "THENEWSAPI_KEY", "THENEWSAPI_API_KEY"),
          ("gnews", "GNEWS_KEY", "GNEWS_API_KEY"))


@pytest.fixture(autouse=True)
def _limiter_pulito(tmp_path, monkeypatch):
    """Stato del rate-limiter mai scritto (fail-open documentato): niente budget, niente
    auto-disable — qui si misurano solo chiavi e negozio."""
    monkeypatch.setattr(na, "_NEWS_RATE_PATH", str(tmp_path / "news_rate_state.json"))


def _chiavi(monkeypatch, presenti: bool, tiingo: bool):
    from bellomberg.market_data import tiingo_news
    for _p, attr, _var in CHIAVI:
        monkeypatch.setattr(na, attr, "chiave-di-prova" if presenti else "")
    monkeypatch.setattr(tiingo_news, "tiingo_available", lambda: tiingo)


def _negozio(monkeypatch, tmp_path, stato: str):
    if stato == "presente":
        monkeypatch.setattr(na, "PERCORSO_TERMINI", ESEMPIO_TERMINI)
    elif stato == "assente":
        monkeypatch.setattr(na, "PERCORSO_TERMINI", str(tmp_path / "manca.json"))
    else:
        p = tmp_path / "rotto.json"
        p.write_text("{ non json", encoding="utf-8")
        monkeypatch.setattr(na, "PERCORSO_TERMINI", str(p))


# ------------------------------------------------ il produttore

def test_con_chiavi_e_negozio_nessuna_fonte_e_muta(tmp_path, monkeypatch):
    _chiavi(monkeypatch, presenti=True, tiingo=True)
    _negozio(monkeypatch, tmp_path, "presente")
    assert na.providers_blocked() == {}


def test_senza_chiavi_ogni_provider_e_muto_col_nome_della_variabile(tmp_path, monkeypatch):
    _chiavi(monkeypatch, presenti=False, tiingo=False)
    _negozio(monkeypatch, tmp_path, "presente")
    fuori = na.providers_blocked()
    for p, _attr, var in CHIAVI:
        assert p in fuori, (p, fuori)
        assert fuori[p].startswith("SENZA_CHIAVE"), fuori[p]
        assert var in fuori[p], "il motivo deve dire QUALE variabile del .env manca: %r" % fuori[p]
    assert "tiingo" in fuori and "TIINGO_API_KEY" in fuori["tiingo"], fuori


def test_la_chiave_assente_vince_sul_budget(tmp_path, monkeypatch):
    """Un provider senza chiave non ha mai consumato budget: il motivo vero e' la chiave."""
    import json
    from datetime import datetime
    stato = {"newsapi": {"day": datetime.now().strftime("%Y-%m-%d"),
                         "count": na.NEWS_PROVIDER_LIMITS["newsapi"]["daily"]}}
    p = tmp_path / "news_rate_state.json"
    p.write_text(json.dumps(stato), encoding="utf-8")
    _chiavi(monkeypatch, presenti=False, tiingo=True)
    _negozio(monkeypatch, tmp_path, "presente")
    assert na.providers_blocked()["newsapi"].startswith("SENZA_CHIAVE")


def test_il_budget_resta_dichiarato_quando_la_chiave_c_e(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    stato = {"newsapi": {"day": datetime.now().strftime("%Y-%m-%d"),
                         "count": na.NEWS_PROVIDER_LIMITS["newsapi"]["daily"]}}
    (tmp_path / "news_rate_state.json").write_text(json.dumps(stato), encoding="utf-8")
    _chiavi(monkeypatch, presenti=True, tiingo=True)
    _negozio(monkeypatch, tmp_path, "presente")
    assert na.providers_blocked() == {"newsapi": "SKIP_BUDGET"}


def test_negozio_termini_assente_e_fra_le_fonti_mute(tmp_path, monkeypatch):
    _chiavi(monkeypatch, presenti=True, tiingo=True)
    _negozio(monkeypatch, tmp_path, "assente")
    fuori = na.providers_blocked()
    assert na.TERMINI_MUTI in fuori, fuori
    assert fuori[na.TERMINI_MUTI].startswith("NEGOZIO_ASSENTE"), fuori[na.TERMINI_MUTI]
    assert 'news_search_terms.example.json' in fuori[na.TERMINI_MUTI], (
        "il motivo deve dire cosa copiare in data/: %r" % fuori[na.TERMINI_MUTI])


def test_negozio_termini_illeggibile_e_fra_le_fonti_mute(tmp_path, monkeypatch):
    _chiavi(monkeypatch, presenti=True, tiingo=True)
    _negozio(monkeypatch, tmp_path, "illeggibile")
    fuori = na.providers_blocked()
    assert fuori.get(na.TERMINI_MUTI, "").startswith("NEGOZIO_ILLEGGIBILE"), fuori


# ------------------------------------------------ le due rotte a book vuoto (funzioni endpoint)

def test_rotta_news_ticker_senza_chiavi_dichiara_le_fonti_mute(tmp_path, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    _chiavi(monkeypatch, presenti=False, tiingo=False)
    _negozio(monkeypatch, tmp_path, "assente")
    monkeypatch.setattr(na, "search_news_for_ticker", lambda *a, **k: [])
    out = api.get_news_ticker("ALFA")
    assert out["count"] == 0 and out["items"] == []
    assert out["fonti_mute"], "count 0 con fonti_mute null: «sono cieco» spacciato per «0 news»"
    assert "newsapi" in out["fonti_mute"] and na.TERMINI_MUTI in out["fonti_mute"]
    assert out["avviso"] and "PARZIALE" in out["avviso"], out["avviso"]


def test_rotta_news_portfolio_senza_chiavi_dichiara_le_fonti_mute(tmp_path, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    _chiavi(monkeypatch, presenti=False, tiingo=False)
    _negozio(monkeypatch, tmp_path, "assente")
    monkeypatch.setattr(na, "search_portfolio_news", lambda *a, **k: {})
    out = api.get_news_portfolio()
    assert out["n_tickers"] == 0
    assert out["fonti_mute"] and "gnews" in out["fonti_mute"], out
    assert out["avviso"] and "PARZIALE" in out["avviso"], out["avviso"]


def test_rotta_news_ticker_con_tutto_a_posto_resta_senza_avviso(tmp_path, monkeypatch):
    """Frase di stato al presente: la dichiarazione compare SOLO quando il buco c'e'."""
    import bellomberg.api.bellomberg_api as api
    _chiavi(monkeypatch, presenti=True, tiingo=True)
    _negozio(monkeypatch, tmp_path, "presente")
    monkeypatch.setattr(na, "search_news_for_ticker", lambda *a, **k: [])
    out = api.get_news_ticker("ALFA")
    assert out["fonti_mute"] is None and out["avviso"] is None
