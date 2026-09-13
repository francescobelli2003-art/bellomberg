"""Authored news labels switch language; sources, queries and cached facts do not."""
from copy import deepcopy
import json

import pytest

from bellomberg.core.language import language_context
from bellomberg.core.presentation import render_payload


@pytest.mark.parametrize('language,label,category', [
    ('it', 'Tesorerie quotate Bitcoin', 'Banche Centrali & Tassi'),
    ('en', 'Listed Bitcoin treasuries', 'Central Banks & Rates'),
])
def test_topic_labels_have_variants_without_changing_queries_or_identifiers(language, label, category):
    from bellomberg.market_data import news_topics as topics
    before = deepcopy(topics.TOPICS)
    with language_context(language):
        view = render_payload(topics.TOPICS)
        assert next(t['label'] for t in view if t['id'] == 'mstr_saylor') == label
        assert render_payload(topics.CATEGORIES)['rates'] == category
    assert [{k: v for k, v in t.items() if k != 'label'} for t in view] == [
        {k: v for k, v in t.items() if k != 'label'} for t in before]
    assert topics.TOPICS == before


def test_macro_cache_rerenders_label_without_refetch_or_translating_original(monkeypatch):
    from bellomberg.market_data import news_aggregator as news, news_topics as topics
    from bellomberg.core.api_presentation import PresentationJSONResponse
    topic = next(t for t in topics.TOPICS if t['id'] == 'pmi_global')
    monkeypatch.setattr(topics, 'TOPICS', [topic])
    monkeypatch.setattr(news, '_CACHE', {})
    monkeypatch.setattr(news, 'RSS_FEEDS', {})
    monkeypatch.setattr(news, '_titoli_del_giro', lambda *_: {'origine': 'assente', 'temi': {}})
    calls = []
    original = {'title': 'Originalquelle unverändert', 'snippet': 'Testo originale non tradotto',
                'url': 'https://synthetic.invalid/news', 'published_at': '2026-09-12T10:00:00Z'}
    monkeypatch.setattr(news, '_fetch_newsapi', lambda query, **kw: calls.append(query) or [deepcopy(original)])
    monkeypatch.setattr(news, '_fetch_gnews', lambda *a, **k: [])
    with language_context('it'):
        italian = news.fetch_macro_news(min_importance=1, include_reddit=False)
    cached = deepcopy(news._CACHE)
    with language_context('en'):
        english = news.fetch_macro_news(min_importance=1, include_reddit=False)
        body = json.loads(PresentationJSONResponse({'news': english}).body)
    assert italian[0]['topic_label'] == 'PMI Globali'
    assert english[0]['topic_label'] == 'Global PMIs'
    assert calls == [topic['query']]
    assert news._CACHE == cached
    for view in (italian, english):
        assert {k: view[0][k] for k in original} == original
        assert view[0]['topic_id'] == 'pmi_global' and view[0]['tickers_affected'] == []
    assert body['_presentation_v1']['texts'] == [
        {'path': ['news', 0, 'topic_label'], 'it': 'PMI Globali', 'en': 'Global PMIs'}]


@pytest.fixture
def synthetic_sec(monkeypatch):
    from bellomberg.market_data import sec_edgar as sec
    filing = {'description': 'Original filing description in source language', 'filed_date': '2026-09-10',
              'url': 'https://synthetic.invalid/8k'}
    structure = {'form': 'S-1', 'description': '', 'filed_date': '2026-09-11', 'url': 'https://synthetic.invalid/s1'}
    trade = {'owner': 'SYNTH OWNER', 'relation': 'Chief Executive Officer', 'action': 'BUY', 'shares': 1234.0,
             'price_usd': 12.5, 'value_usd': 15425.0, 'trade_date': '2026-09-12', 'url': 'https://synthetic.invalid/form4'}
    calls = []
    monkeypatch.setattr(sec, 'get_8k_events', lambda *a, **k: calls.append('8k') or [deepcopy(filing)])
    monkeypatch.setattr(sec, 'get_recent_filings', lambda *a, **k: calls.append('structure') or [deepcopy(structure)])
    monkeypatch.setattr(sec, 'get_insider_trades', lambda *a, **k: calls.append('insider') or [deepcopy(trade)])
    monkeypatch.setattr(sec.time, 'sleep', lambda *_: None)
    return sec, filing, trade, calls


def test_sec_authored_text_has_pairs_and_keeps_original_filing_and_numeric_metadata(synthetic_sec):
    sec, filing, trade, calls = synthetic_sec
    from bellomberg.core.api_presentation import PresentationJSONResponse
    with language_context('it'):
        events = sec.get_corporate_events_for_portfolio(['SYNTH'])
    before = deepcopy(events)
    with language_context('en'):
        english = render_payload(events)
        response = json.loads(PresentationJSONResponse({'events': english}).body)
    by_type = {e['type']: e for e in english}
    assert by_type['8-K']['title'] == 'SYNTH: 8-K Material Event filed'
    assert by_type['8-K']['snippet'] == filing['description']
    assert by_type['8-K']['snippet_origin'] == 'source'
    assert by_type['S-1']['snippet'] == 'Securities registration (capital raising)'
    assert by_type['S-1']['snippet_origin'] == 'bellomberg'
    assert 'BUYS 1,234 shares at $12.50 (value $15,425)' in by_type['Form 4']['title']
    assert 'ACQUISTA 1.234 azioni a $12,50 (valore $15.425)' in events[0]['title']
    assert by_type['Form 4']['metadata'] == trade
    assert all(e['title_origin'] == 'bellomberg' and e['presentation_languages'] == ['it', 'en'] for e in events)
    assert calls == ['8k', 'structure', 'insider']
    assert events == before
    source_index = next(i for i, e in enumerate(english) if e['type'] == '8-K')
    assert not any(t['path'] == ['events', source_index, 'snippet'] for t in response['_presentation_v1']['texts'])


def test_corporate_cache_preserves_pairs_and_provenance_without_extra_provider_calls(monkeypatch, synthetic_sec):
    sec, filing, trade, calls = synthetic_sec
    from bellomberg.market_data import news_aggregator as news
    class DB:
        def get_portfolio_summary(self): return {'positions': [{'ticker': 'SYNTH'}]}
    monkeypatch.setattr(news, 'MemoryDB', DB)
    monkeypatch.setattr(news, '_CACHE', {})
    monkeypatch.setattr(news, '_termini_del_giro', lambda *_: {})
    monkeypatch.setattr(news, 'escluso_dalle_news', lambda *a: False)
    monkeypatch.setattr(news, '_fetch_newsapi', lambda *a, **k: [])
    with language_context('it'):
        italian = news.fetch_corporate_events()
    before = deepcopy(news._CACHE)
    with language_context('en'):
        english = news.fetch_corporate_events()
    assert calls == ['8k', 'structure', 'insider']
    assert news._CACHE == before
    assert 'ACQUISTA' in next(e['title'] for e in italian if e['event_type'] == 'Form 4')
    assert 'BUYS' in next(e['title'] for e in english if e['event_type'] == 'Form 4')
    assert all(e['title_origin'] == 'bellomberg' for e in english)
    assert next(e['snippet'] for e in english if e['event_type'] == '8-K') == filing['description']


def test_sec_structural_failure_localizes_only_authored_prefix(monkeypatch, synthetic_sec):
    sec, *_ = synthetic_sec
    def failed(*a, **kw): raise ValueError('Original provider diagnostic')
    monkeypatch.setattr(sec, 'get_recent_filings', failed)
    reasons = []
    with language_context('it'):
        events = sec.get_corporate_events_for_portfolio(['SYNTH'], motivo=reasons)
    assert len(events) == 2
    assert render_payload(reasons, language='en') == [
        'Structural filings (SYNTH) error: ValueError: Original provider diagnostic']


# 13/09: GRANT non resta piu' il codice interno nel titolo (era fissato qui): il codice resta in
# metadata.action, il titolo dice la frase SEC del codice A.
@pytest.mark.parametrize('action,verb', [('SELL', 'SELLS'), ('GRANT', 'ACQUIRES')])
def test_sec_action_codes_and_original_relation_remain_exact(synthetic_sec, action, verb):
    sec, filing, trade, calls = synthetic_sec
    trade['action'] = action
    with language_context('en'):
        events = sec.get_corporate_events_for_portfolio(['SYNTH'])
    event = next(e for e in events if e['type'] == 'Form 4')
    assert f' {verb} 1,234 shares ' in event['title']
    assert '(Chief Executive Officer)' in event['title']
    assert event['metadata'] == trade and event['importance'] == 3


# Form 4: una frase per codice di transazione, presa dalla definizione SEC (Investor Bulletin
# «Insider Transactions and Forms 3, 4, and 5»): A = grant, award, or other acquisition of
# securities from the company; M = exercise or conversion of derivative security; D = sale or
# transfer of securities back to the company; F = payment of exercise price or tax liability
# using portion of securities received from the company; G = gift of securities BY OR TO the
# insider (la direzione non si legge: frase neutra). Prima il titolo portava GRANT, OPTION_EX,
# DISP o la lettera nuda anche nella frase italiana.
FORM4_FRASI = [
    ('P', 'BUY',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) ACQUISTA 1.234 azioni a $12,50 (valore $15.425)',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) BUYS 1,234 shares at $12.50 (value $15,425)',
     'Insider SYNTH OWNER ACQUISTA azioni il 2026-09-12',
     'Insider SYNTH OWNER BUYS shares on 2026-09-12'),
    ('S', 'SELL',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) VENDE 1.234 azioni a $12,50 (valore $15.425)',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) SELLS 1,234 shares at $12.50 (value $15,425)',
     'Insider SYNTH OWNER VENDE azioni il 2026-09-12',
     'Insider SYNTH OWNER SELLS shares on 2026-09-12'),
    ('A', 'GRANT',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) RICEVE 1.234 azioni dalla società '
     '(assegnazione, premio o altra acquisizione) a $12,50 (valore $15.425)',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) ACQUIRES 1,234 shares from the company '
     '(grant, award or other acquisition) at $12.50 (value $15,425)',
     'Insider SYNTH OWNER RICEVE azioni dalla società (assegnazione, premio o altra acquisizione) il 2026-09-12',
     'Insider SYNTH OWNER ACQUIRES shares from the company (grant, award or other acquisition) on 2026-09-12'),
    ('M', 'OPTION_EX',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) ACQUISISCE 1.234 azioni da esercizio o conversione '
     'di derivati a $12,50 (valore $15.425)',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) ACQUIRES 1,234 shares by exercise or conversion '
     'of derivatives at $12.50 (value $15,425)',
     'Insider SYNTH OWNER ACQUISISCE azioni da esercizio o conversione di derivati il 2026-09-12',
     'Insider SYNTH OWNER ACQUIRES shares by exercise or conversion of derivatives on 2026-09-12'),
    ('D', 'DISP',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) VENDE O TRASFERISCE 1.234 azioni alla società '
     'a $12,50 (valore $15.425)',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) SELLS OR TRANSFERS 1,234 shares back to the company '
     'at $12.50 (value $15,425)',
     'Insider SYNTH OWNER VENDE O TRASFERISCE azioni alla società il 2026-09-12',
     'Insider SYNTH OWNER SELLS OR TRANSFERS shares back to the company on 2026-09-12'),
    ('F', 'F',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) USA 1.234 azioni ricevute dalla società per pagare '
     'prezzo di esercizio o imposte, a $12,50 (valore $15.425)',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) USES 1,234 shares received from the company to pay '
     'exercise price or tax liability, at $12.50 (value $15,425)',
     'Insider SYNTH OWNER USA azioni ricevute dalla società per pagare prezzo di esercizio o imposte il 2026-09-12',
     'Insider SYNTH OWNER USES shares received from the company to pay exercise price or tax liability on 2026-09-12'),
    ('G', 'G',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) REGISTRA UNA DONAZIONE (FATTA O RICEVUTA) DI 1.234 azioni '
     'a $12,50 (valore $15.425)',
     'SYNTH: SYNTH OWNER (Chief Executive Officer) REPORTS A GIFT (MADE OR RECEIVED) OF 1,234 shares '
     'at $12.50 (value $15,425)',
     'Insider SYNTH OWNER REGISTRA UNA DONAZIONE (FATTA O RICEVUTA) DI azioni il 2026-09-12',
     'Insider SYNTH OWNER REPORTS A GIFT (MADE OR RECEIVED) OF shares on 2026-09-12'),
    ('J', 'J',
     "SYNTH: SYNTH OWNER (Chief Executive Officer) REGISTRA UN'OPERAZIONE CON CODICE SEC J SU 1.234 azioni "
     "a $12,50 (valore $15.425)",
     'SYNTH: SYNTH OWNER (Chief Executive Officer) REPORTS A SEC CODE J TRANSACTION ON 1,234 shares '
     'at $12.50 (value $15,425)',
     "Insider SYNTH OWNER REGISTRA UN'OPERAZIONE CON CODICE SEC J il 2026-09-12",
     'Insider SYNTH OWNER REPORTS A SEC CODE J TRANSACTION on 2026-09-12'),
    ('?', '?',
     "SYNTH: SYNTH OWNER (Chief Executive Officer) REGISTRA UN'OPERAZIONE SENZA CODICE DI TRANSAZIONE SU "
     "1.234 azioni a $12,50 (valore $15.425)",
     'SYNTH: SYNTH OWNER (Chief Executive Officer) REPORTS A TRANSACTION WITHOUT A TRANSACTION CODE ON '
     '1,234 shares at $12.50 (value $15,425)',
     "Insider SYNTH OWNER REGISTRA UN'OPERAZIONE SENZA CODICE DI TRANSAZIONE il 2026-09-12",
     'Insider SYNTH OWNER REPORTS A TRANSACTION WITHOUT A TRANSACTION CODE on 2026-09-12'),
]


@pytest.mark.parametrize('code,action,title_it,title_en,snippet_it,snippet_en', FORM4_FRASI)
def test_form4_each_sec_code_has_its_own_sentence_in_both_languages(
        synthetic_sec, code, action, title_it, title_en, snippet_it, snippet_en):
    sec, filing, trade, calls = synthetic_sec
    trade.update(code=code, action=action)
    with language_context('it'):
        events = sec.get_corporate_events_for_portfolio(['SYNTH'])
    event = next(e for e in events if e['type'] == 'Form 4')
    english = render_payload(event, language='en')
    assert (event['title'], event['snippet']) == (title_it, snippet_it)
    assert (english['title'], english['snippet']) == (title_en, snippet_en)
    assert '_' not in event['title'] + english['title']
    assert event['metadata']['action'] == action and event['metadata']['code'] == code


# --- Form 4 VERO: get_recent_filings e get_insider_trades girano davvero su una rete finta ---
FORM4_URL = 'https://www.sec.gov/Archives/edgar/data/42/000000004226000001/xslF345X05/form4.xml'
FORM4_RAW_URL = 'https://www.sec.gov/Archives/edgar/data/42/000000004226000001/form4.xml'


class _SecResponse:
    def __init__(self, status=200, text='', payload=None):
        self.status_code, self.text, self._payload = status, text, payload

    def json(self):
        return self._payload


@pytest.fixture
def sec_fake_network(monkeypatch):
    from datetime import datetime
    from bellomberg.market_data import sec_edgar as sec
    today = datetime.now().strftime('%Y-%m-%d')
    documents = {}

    def get(url, headers=None, timeout=None, **kw):
        if url == 'https://data.sec.gov/submissions/CIK0000000042.json':
            return _SecResponse(payload={'name': 'Synthetic Corp', 'filings': {'recent': {
                'form': ['4'], 'filingDate': [today], 'accessionNumber': ['0000000042-26-000001'],
                'primaryDocument': ['xslF345X05/form4.xml'], 'primaryDocDescription': ['']}}})
        if url.endswith('/index.json'):
            return _SecResponse(payload={'directory': {'item': []}})
        answer = documents[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(sec, 'lookup_cik', lambda *a, **k: '0000000042')
    monkeypatch.setattr(sec.requests, 'get', get)
    monkeypatch.setattr(sec.time, 'sleep', lambda *_: None)
    return sec, documents


def _form4_xml(relationship, owner='<rptOwnerName>SYNTH OWNER</rptOwnerName>'):
    return ('<?xml version="1.0"?><ownershipDocument><issuer><issuerTradingSymbol>SYNTH</issuerTradingSymbol>'
            '</issuer><reportingOwner><reportingOwnerId><rptOwnerCik>0000000001</rptOwnerCik>' + owner +
            '</reportingOwnerId><reportingOwnerRelationship>' + relationship + '</reportingOwnerRelationship>'
            '</reportingOwner><nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-09-12'
            '</value></transactionDate><transactionCoding><transactionCode>P</transactionCode>'
            '</transactionCoding><transactionAmounts><transactionShares><value>100</value></transactionShares>'
            '<transactionPricePerShare><value>10</value></transactionPricePerShare></transactionAmounts>'
            '</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>')


# Lo schema ownership SEC ha isDirector/isOfficer/isTenPercentOwner/isOther (+ officerTitle,
# otherText): <directorIndicator>, che il codice cercava, non esiste. I flag valgono true/false
# nei filing recenti (ownership.xml vero di un 10% owner, 2024) e 1/0 in quelli vecchi.
FORM4_ROLES = [
    ('<isDirector>false</isDirector><isOfficer>false</isOfficer><isTenPercentOwner>true</isTenPercentOwner>'
     '<isOther>false</isOther>', ' (azionista oltre il 10%) ', ' (10% owner) '),
    ('<isDirector>true</isDirector><isOfficer>false</isOfficer><isTenPercentOwner>false</isTenPercentOwner>'
     '<isOther>false</isOther>', ' (amministratore) ', ' (director) '),
    ('<isDirector>1</isDirector><isOfficer>0</isOfficer><isTenPercentOwner>0</isTenPercentOwner>'
     '<isOther>0</isOther>', ' (amministratore) ', ' (director) '),
    ('<isDirector>0</isDirector><isOfficer>1</isOfficer><isTenPercentOwner>0</isTenPercentOwner>'
     '<isOther>0</isOther>', ' (dirigente) ', ' (officer) '),
    # piu' flag insieme si uniscono; officerTitle resta come nella fonte e NON si taglia a 30
    ('<isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>EVP, Chief Financial Officer and '
     'Treasurer</officerTitle><isTenPercentOwner>1</isTenPercentOwner><isOther>0</isOther>',
     ' (amministratore, EVP, Chief Financial Officer and Treasurer, azionista oltre il 10%) ',
     ' (director, EVP, Chief Financial Officer and Treasurer, 10% owner) '),
    ('<isDirector>false</isDirector><isOfficer>false</isOfficer><isTenPercentOwner>false</isTenPercentOwner>'
     '<isOther>true</isOther><otherText>Member of a group</otherText>',
     ' (Member of a group) ', ' (Member of a group) '),
    ('<isDirector>false</isDirector><isOfficer>false</isOfficer><isTenPercentOwner>false</isTenPercentOwner>'
     '<isOther>true</isOther>', ' (altro rapporto) ', ' (other relationship) '),
    # nessun ruolo: niente parentesi vuote
    ('<isDirector>0</isDirector><isOfficer>false</isOfficer><isTenPercentOwner>0</isTenPercentOwner>'
     '<isOther>false</isOther>', ' ', ' '),
]


@pytest.mark.parametrize('relationship,role_it,role_en', FORM4_ROLES)
def test_real_form4_reads_the_role_from_sec_flags(sec_fake_network, relationship, role_it, role_en):
    sec, documents = sec_fake_network
    documents[FORM4_RAW_URL] = _SecResponse(text=_form4_xml(relationship))
    with language_context('it'):
        events = sec.get_corporate_events_for_portfolio(['SYNTH'])
    (event,) = [e for e in events if e['type'] == 'Form 4']
    english = render_payload(event, language='en')
    assert event['title'] == 'SYNTH: SYNTH OWNER' + role_it + 'ACQUISTA 100 azioni a $10,00 (valore $1.000)'
    assert english['title'] == 'SYNTH: SYNTH OWNER' + role_en + 'BUYS 100 shares at $10.00 (value $1,000)'


def test_real_form4_without_owner_says_so_in_our_words(sec_fake_network):
    sec, documents = sec_fake_network
    documents[FORM4_RAW_URL] = _SecResponse(text=_form4_xml('<isDirector>1</isDirector>', owner=''))
    with language_context('it'):
        events = sec.get_corporate_events_for_portfolio(['SYNTH'])
    (event,) = [e for e in events if e['type'] == 'Form 4']
    english = render_payload(event, language='en')
    assert event['title'] == ('SYNTH: titolare non indicato nel filing (amministratore) '
                              'ACQUISTA 100 azioni a $10,00 (valore $1.000)')
    assert english['title'] == ('SYNTH: owner not stated in the filing (director) '
                                'BUYS 100 shares at $10.00 (value $1,000)')


@pytest.mark.parametrize('response,status', [
    (_SecResponse(text='<html><body>Form 4 rendered page</body></html>'), 200),
    (_SecResponse(status=404, text='Not Found'), 404),
])
def test_form4_without_xml_document_invents_no_event_and_declares_it(sec_fake_network, response, status):
    sec, documents = sec_fake_network
    documents[FORM4_RAW_URL] = response
    reasons = []
    with language_context('it'):
        trades = sec.get_insider_trades('SYNTH', motivo=reasons)
    assert trades == []
    assert reasons == ['Form 4 %s: documento XML non trovato (HTTP %d), operazione non letta' % (FORM4_URL, status)]
    assert render_payload(reasons, language='en') == [
        'Form 4 %s: XML document not found (HTTP %d), transaction not read' % (FORM4_URL, status)]


def test_form4_read_failure_is_declared_in_both_languages(sec_fake_network):
    sec, documents = sec_fake_network
    documents[FORM4_RAW_URL] = ConnectionError('synthetic network down')
    reasons = []
    with language_context('it'):
        trades = sec.get_insider_trades('SYNTH', motivo=reasons)
    assert trades == []
    assert reasons == ['Form 4 %s: errore di lettura (ConnectionError: synthetic network down), '
                       'operazione non letta' % FORM4_URL]
    assert render_payload(reasons, language='en') == [
        'Form 4 %s: read error (ConnectionError: synthetic network down), transaction not read' % FORM4_URL]


# --- fonti mute del giro news: il codice resta, la frase ha le due lingue fino al confine HTTP ---
@pytest.fixture
def news_blocked_sources(monkeypatch, tmp_path):
    from bellomberg.core.paths import EXAMPLES_DIR
    from bellomberg.market_data import news_aggregator as news, tiingo_news
    for attr in ('NEWSAPI_KEY', 'THENEWSAPI_KEY', 'GNEWS_KEY'):
        monkeypatch.setattr(news, attr, 'synthetic-key')
    monkeypatch.setattr(tiingo_news, 'tiingo_available', lambda: True)
    monkeypatch.setattr(news, 'PERCORSO_TERMINI', str(EXAMPLES_DIR / 'news_search_terms.example.json'))
    monkeypatch.setattr(news, '_NEWS_RATE_PATH', str(tmp_path / 'news_rate_state.json'))
    return news


def test_missing_keys_and_stores_are_declared_in_both_languages(monkeypatch, tmp_path, news_blocked_sources):
    from bellomberg.core.api_presentation import PresentationJSONResponse
    from bellomberg.market_data import tiingo_news
    from bellomberg.storage import negozi_privati
    news = news_blocked_sources
    for attr in ('NEWSAPI_KEY', 'THENEWSAPI_KEY', 'GNEWS_KEY'):
        monkeypatch.setattr(news, attr, '')
    monkeypatch.setattr(tiingo_news, 'tiingo_available', lambda: False)
    terms, topics = str(tmp_path / 'terms_missing.json'), str(tmp_path / 'topics_missing.json')
    monkeypatch.setattr(news, 'PERCORSO_TERMINI', terms)
    monkeypatch.setattr(negozi_privati, 'PERCORSO_TEMI_TITOLI', topics)
    with language_context('it'):
        italian = news.providers_blocked()
    english = render_payload(italian, language='en')
    assert italian == {
        'newsapi': 'SENZA_CHIAVE: NEWS_API_KEY assente nel .env',
        'thenewsapi': 'SENZA_CHIAVE: THENEWSAPI_API_KEY assente nel .env',
        'gnews': 'SENZA_CHIAVE: GNEWS_API_KEY assente nel .env',
        'tiingo': 'SENZA_CHIAVE: TIINGO_API_KEY assente nel .env (o requests non importabile)',
        news.TERMINI_MUTI: ("NEGOZIO_ASSENTE: negozio non trovato: %s (copia news_search_terms.example.json "
                            "in data/ e mettici le TUE societa')" % terms),
        news.TEMI_TITOLI_MUTI: ('NEGOZIO_ASSENTE: negozio non trovato: %s (copia news_topics_tickers.example.json '
                                'in data/ e mettici i TUOI dati)' % topics),
    }
    assert english == {
        'newsapi': 'SENZA_CHIAVE: NEWS_API_KEY missing from .env',
        'thenewsapi': 'SENZA_CHIAVE: THENEWSAPI_API_KEY missing from .env',
        'gnews': 'SENZA_CHIAVE: GNEWS_API_KEY missing from .env',
        'tiingo': 'SENZA_CHIAVE: TIINGO_API_KEY missing from .env (or requests cannot be imported)',
        news.TERMINI_MUTI: ('NEGOZIO_ASSENTE: Store not found: %s (copy news_search_terms.example.json '
                            'into data/ and enter YOUR companies)' % terms),
        news.TEMI_TITOLI_MUTI: ('NEGOZIO_ASSENTE: Store not found: %s (copy news_topics_tickers.example.json '
                                'into data/ and enter YOUR data)' % topics),
    }
    body = json.loads(PresentationJSONResponse({'fonti_mute': italian}).body)
    assert {tuple(t['path']): (t['it'], t['en']) for t in body['_presentation_v1']['texts']} == {
        ('fonti_mute', key): (italian[key], english[key]) for key in italian}


def test_modules_that_cannot_be_imported_are_declared_in_both_languages(monkeypatch, news_blocked_sources):
    import sys
    import types
    news = news_blocked_sources

    def broken(name):
        module = types.ModuleType(name)

        def __getattr__(attribute):
            if attribute.startswith('__'):
                raise AttributeError(attribute)
            raise ImportError('Original import diagnostic')
        module.__getattr__ = __getattr__
        return module
    monkeypatch.setitem(sys.modules, 'bellomberg.market_data.tiingo_news', broken('tiingo_news'))
    monkeypatch.setitem(sys.modules, 'bellomberg.storage.negozi_privati', broken('negozi_privati'))
    with language_context('it'):
        italian = news.providers_blocked()
    assert italian == {
        'tiingo': 'MODULO_ASSENTE: tiingo_news non importabile (ImportError)',
        news.TEMI_TITOLI_MUTI: 'MODULO_ASSENTE: negozi_privati non importabile (ImportError: Original import diagnostic)'}
    assert render_payload(italian, language='en') == {
        'tiingo': 'MODULO_ASSENTE: tiingo_news cannot be imported (ImportError)',
        news.TEMI_TITOLI_MUTI: 'MODULO_ASSENTE: negozi_privati cannot be imported (ImportError: Original import diagnostic)'}


@pytest.mark.parametrize('content,reason_it,reason_en', [
    ('[]', "il negozio non e' un oggetto JSON ma list", 'Store is not a JSON object but list'),
    ('{"alfa": ["Alfa SpA"]}',
     "chiave 'alfa' non canonica o doppia: scrivila MAIUSCOLA, senza spazi e una volta sola ('ALFA')",
     "Noncanonical or duplicate key 'alfa': write it UPPERCASE, without spaces, once only ('ALFA')"),
    ('{"ALFA": []}',
     "voce 'ALFA' malformata: serve una lista non vuota di termini (stringhe), oppure null per escludere "
     "il simbolo dalle news",
     "Malformed entry 'ALFA': a nonempty list of terms (strings) is required, or null to exclude "
     "the symbol from news"),
])
def test_unreadable_terms_store_keeps_its_reason_in_both_languages(
        monkeypatch, tmp_path, news_blocked_sources, content, reason_it, reason_en):
    news = news_blocked_sources
    store = tmp_path / 'news_search_terms.json'
    store.write_text(content, encoding='utf-8')
    monkeypatch.setattr(news, 'PERCORSO_TERMINI', str(store))
    with language_context('it'):
        italian = news.providers_blocked()
    assert italian == {news.TERMINI_MUTI: 'NEGOZIO_ILLEGGIBILE: ' + reason_it}
    assert render_payload(italian, language='en') == {news.TERMINI_MUTI: 'NEGOZIO_ILLEGGIBILE: ' + reason_en}


@pytest.mark.parametrize('language,label', [('it', 'Taiwan & Cina USA'), ('en', 'Taiwan & US-China')])
def test_taiwan_topic_label_reads_as_a_sentence_in_each_language(language, label):
    from bellomberg.market_data import news_topics as topics
    with language_context(language):
        assert next(t['label'] for t in render_payload(topics.TOPICS) if t['id'] == 'taiwan_china') == label
