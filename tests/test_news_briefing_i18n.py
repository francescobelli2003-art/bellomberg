"""Synthetic providers only: language isolation never translates stored history."""
import copy
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from bellomberg.cli import briefing_engine as be
from bellomberg.market_data import news_aggregator as news
from bellomberg.core import llm_client as lc


@pytest.fixture
def briefing(tmp_path, monkeypatch):
    monkeypatch.setattr(be, "CACHE_PATH", str(tmp_path / "briefing.json"))
    monkeypatch.setattr(be, "_fetch_recent_news", lambda **kw: [{"title": "Citazione da mantenere 17%", "provider": "fake"}])
    monkeypatch.setattr(be, "_fetch_macro_snapshot", lambda: {})
    monkeypatch.setattr(be, "_fetch_portfolio_snapshot", lambda: {})
    from bellomberg.core import current_facts
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "STORICO: testo originale 23")
    monkeypatch.setattr(lc, "modello", lambda *a: "fake-model")
    calls = []
    def create(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        calls.append(prompt)
        en = "professional English" in prompt
        return NS(content=[lc.TextBlock("## MARKET TONE\n" + ("Measured English output." if en else "Testo italiano misurato."))], usage=NS(input_tokens=11, output_tokens=12))
    monkeypatch.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    return calls


def test_briefing_generates_both_languages_and_cache_does_not_cross(briefing):
    it = be.generate_briefing("morning", language="it")
    en = be.generate_briefing("morning", language="en")
    assert it["language"] == "it" and en["language"] == "en"
    assert "italiano" in it["briefing_md"] and "English" in en["briefing_md"]
    assert be.get_current_briefing(language="it")["briefing_md"] == it["briefing_md"]
    assert be.get_current_briefing(language="en")["briefing_md"] == en["briefing_md"]
    assert all("Citazione da mantenere 17%" in p and "STORICO: testo originale 23" in p for p in briefing)
    assert "briefing in italiano" not in briefing[1]
    assert "mattino" in it["slot_label"] and "Morning" in en["slot_label"]


def test_legacy_briefing_is_italian_only_and_get_never_calls_model(briefing):
    old = {"morning": {"briefing_md": "Storico intoccabile", "generated_at": datetime.now().isoformat()}, "_latest": "morning"}
    be._save_cache(old)
    before = open(be.CACHE_PATH, "rb").read()
    assert be.get_current_briefing(language="it")["briefing_md"] == "Storico intoccabile"
    missing = be.get_current_briefing(language="en")
    assert missing["stale"] and missing["language"] == "en"
    assert "not been generated" in missing["briefing_md"]
    assert open(be.CACHE_PATH, "rb").read() == before and not briefing


@pytest.mark.parametrize("language,headline", [("it", "Titolo sintetico"), ("en", "Synthetic headline")])
def test_news_prompt_selects_generated_language_only(monkeypatch, language, headline):
    calls = []
    def create(**kwargs):
        calls.append(kwargs["messages"][0]["content"])
        return NS(content=[lc.ThinkingBlock("private"), lc.TextBlock(json.dumps({"sentiment": "neutral", "sentiment_score": 0, "relevance": 7, "headline_it": headline, "why_matters": "Test reason"}))])
    monkeypatch.setattr(lc, "modello", lambda *a: "fake-model")
    monkeypatch.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    item = {"title": "Titolo originale 32%", "snippet": "Citazione originale 41", "ticker_mentioned": "SYNTH"}
    before = copy.deepcopy(item)
    result = news._classify_with_haiku(item, ["SYNTH (3.2%)"], language=language)
    assert result["headline"] == headline and result["language"] == language
    assert result["headline_it"] == (headline if language == "it" else "")
    assert item == before and "Titolo originale 32%" in calls[0] and "SYNTH (3.2%)" in calls[0]
    assert ("in ITALIANO" if language == "it" else "in ENGLISH") in calls[0]


@pytest.fixture
def news_db(tmp_path, monkeypatch):
    path = tmp_path / "news.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE news_feed (id INTEGER PRIMARY KEY, title, snippet, source, url, published_at, pulled_at, ticker_mentioned, theme, provider, sentiment, sentiment_score, relevance, headline_it, why_matters)")
        conn.execute("INSERT INTO news_feed VALUES (1, 'Original title', 'Original quotation', 'fake', 'https://example.invalid/story', '', ?, 'SYNTH', '', 'fake', 'neutral', 0, 7, 'Titolo storico IT', 'Motivo storico IT')", (datetime.now().isoformat(),))
    monkeypatch.setattr(news, "MemoryDB", lambda: NS(db_path=str(path)))
    monkeypatch.setattr(news, "_portfolio_weights_cached", lambda: {})
    monkeypatch.setattr(news, "_favorites_tickers", lambda: set())
    monkeypatch.setattr(news, "_classify_with_haiku", lambda *a, **k: pytest.fail("GET must not call the model"))
    return path


def test_news_legacy_english_gap_is_explicit_and_original_database_unchanged(news_db):
    with sqlite3.connect(news_db) as conn:
        before = conn.execute("SELECT * FROM news_feed").fetchall()
    it = news.get_feed(language="it")[0]
    en = news.get_feed(language="en")[0]
    assert it["title"] == "Titolo storico IT" and it["summary_language"] == "it"
    assert en["title"] == "Original title" and en["snippet"] == "Original quotation"
    assert en["summary_status"] == "unavailable" and "not available" in en["summary_note"]
    assert en["headline_it"] == "Titolo storico IT"
    with sqlite3.connect(news_db) as conn:
        assert conn.execute("SELECT * FROM news_feed").fetchall() == before


def test_news_summary_cache_is_bound_to_language_and_original_content(news_db):
    original = news.get_feed(language="en")[0]
    news._save_summary(str(news_db), original, {"language": "en", "headline": "English generated summary", "why_matters": "English reason"})
    assert news.get_feed(language="en")[0]["title"] == "English generated summary"
    assert news.get_feed(language="it")[0]["title"] == "Titolo storico IT"
    with sqlite3.connect(news_db) as conn:
        conn.execute("UPDATE news_feed SET title='Changed original' WHERE id=1")
    changed = news.get_feed(language="en")[0]
    assert changed["title"] == "Changed original" and changed["summary_status"] == "unavailable"


def test_invalid_language_fails_before_sources(briefing, news_db):
    with pytest.raises(ValueError):
        be.generate_briefing("morning", language="fr")
    with pytest.raises(ValueError):
        news.get_feed(language="fr")
    assert not briefing


def test_news_pull_persists_english_only_in_generated_cache(news_db, monkeypatch):
    from bellomberg.market_data import tiingo_news
    monkeypatch.setattr(news, 'MemoryDB', lambda: NS(db_path=str(news_db), get_portfolio_summary=lambda: {'positions':[]}))
    monkeypatch.setattr(news, 'giro_news', lambda positions: ([], []))
    monkeypatch.setattr(news, '_termini_del_giro', lambda context: {})
    monkeypatch.setattr(news, 'providers_blocked', lambda: {})
    monkeypatch.setattr(tiingo_news, 'tiingo_available', lambda: False)
    monkeypatch.setattr(news, '_scrivi_stato_giro', lambda result: None)
    item = {'title':'New original quote', 'snippet':'Original source prose', 'source':'fake',
            'url':'https://example.invalid/new-story', 'provider':'fake'}
    monkeypatch.setattr(news, 'search_news_global', lambda *a, **kw: [copy.deepcopy(item)])
    from bellomberg.core.language import current_language
    languages = []
    def classify(*args):
        languages.append(current_language())
        return {'language':current_language(), 'headline':'New English summary', 'headline_it':'',
                'why_matters':'New English reason', 'sentiment':'bullish', 'sentiment_score':.25, 'relevance':7}
    monkeypatch.setattr(news, '_classify_with_haiku', classify)
    result = news.auto_pull_feed(language='en')
    assert result['saved'] == 1 and result['language'] == 'en' and not result['summary_errors']
    assert languages == ['en']
    with sqlite3.connect(news_db) as conn:
        row = conn.execute('SELECT title,snippet,headline_it,why_matters,sentiment_score FROM news_feed WHERE url=?', (item['url'],)).fetchone()
    assert row == ('New original quote','Original source prose','','',.25)
    result = next(r for r in news.get_feed(language='en') if r['url'] == item['url'])
    assert result['title'] == 'New English summary' and result['snippet_original'] == item['snippet']
    assert news.auto_pull_feed(language='it')['saved'] == 0  # no implicit historical translation


def test_corrupt_english_summary_is_declared_and_does_not_rewrite_originals(news_db):
    row = news.get_feed(language='en')[0]
    path = news._summary_path(str(news_db), row, 'en')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{broken', encoding='utf8')
    shown = news.get_feed(language='en')[0]
    assert shown['summary_status'] == 'invalid' and shown['title'] == 'Original title'
    assert 'not available' in shown['summary_note']
