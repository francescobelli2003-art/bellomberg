"""Foreign ordinary units require filings and exact provider identity together."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import pytest

PRIMARY = ('The Company has an authorized share capital of a single class of 80 million shares '
           'having a nominal value of USD1.00 per share. As of June 30, 2026, there were '
           '75,000,000 shares issued. The Company\u2019s shares trade on the Italian Stock Exchange '
           'and the Mexican Stock Exchange, and its American Depositary Securities (ADS) '
           'trade on the New York Stock Exchange.')
ANNUAL = ('"shares" refers to ordinary shares, par value $1.00, of the Company. '
          'The shares are also listed on the Italian Stock Exchange under the symbol "SYN".')


def source(text, *, annual=False, tmp_path=None):
    raw = ('<html><body><p>'+text+'</p></body></html>').encode()
    digest = sha256(raw).hexdigest()
    accession = '0000000001-26-000001' if annual else '0000000001-26-000002'
    result = {'id': digest, 'document_sha256': digest, 'text': text,
        'sha256': sha256(text.encode()).hexdigest(), 'published_at': '2026-03-01' if annual else '2026-08-01',
        'url': 'https://www.sec.gov/Archives/edgar/data/1/'+accession.replace('-', '')+'/statement.htm',
        'metadata': {'issuer': 'SYNTHETIC SA', 'emittente_id': 'CIK:0000000001',
            'accession': accession, 'form': '20-F' if annual else '6-K',
            'report_date': '2025-12-31' if annual else '2026-06-30'}}
    if tmp_path:
        path = tmp_path/(digest+'.html'); path.write_bytes(raw)
        result['archive_path'] = str(path)
    return result


def quote_raw():
    return {'symbol': 'SYN.MI', 'date': '2026-06-30', 'close': 14.5, 'currency': 'EUR',
        'identity': {'symbol': 'SYN.MI', 'longName': 'Synthetic S.A.', 'exchangeName': 'MIL',
            'fullExchangeName': 'Milan', 'instrumentType': 'EQUITY', 'currency': 'EUR'},
        'identity_observed_on': '2026-08-02'}


def inputs():
    from bellomberg.valuation.quotation_evidence import historical_quote_document
    quote = historical_quote_document('SYN.MI', on='2026-06-30', as_of='2026-08-02',
                                      fetch=lambda *a: quote_raw())['documents'][0]
    return source(PRIMARY), source(ANNUAL, annual=True), quote


def normalize(p, a, q):
    from bellomberg.valuation.foreign_listing_evidence import normalize_foreign_listing
    return normalize_foreign_listing(p, a, q, ticker='SYN.MI', on='2026-06-30', as_of='2026-08-02')


def test_same_ordinary_class_has_recompilable_unit_identity():
    from bellomberg.valuation.input_preparation import _catalog
    p, a, q = inputs()
    result = normalize(p, a, q)
    assert result['status'] == 'ready', result
    doc = result['documents'][0]; body = json.loads(doc['text'])
    assert body['listing']['shares_per_quote'] == 1
    assert body['listing']['symbol'] == 'SYN.MI'
    assert body['facts'] == [{'value': 1, 'unit': 'shares per quote', 'end': '2026-06-30'}]
    for proof in body['proofs']:
        original = {p['id']: p, a['id']: a}[proof['document_id']]
        collapsed = ' '.join(original['text'].split())
        assert collapsed[proof['start']:proof['end']] == proof['text']
    verified, issues, _ = _catalog([p, a, q, doc], date(2026, 8, 2))
    assert not issues and doc['id'] in verified
    for removed in (p, a, q):
        verified, issues, _ = _catalog([d for d in [p, a, q, doc] if d is not removed], date(2026, 8, 2))
        assert issues and doc['id'] not in verified
    forged = deepcopy(doc); forged['metadata']['share_class'] = 'ADR'
    verified, issues, _ = _catalog([p, a, q, forged], date(2026, 8, 2))
    assert issues and doc['id'] not in verified


@pytest.mark.parametrize('change', [
    ('p', 'single class', 'two classes'), ('p', 'shares trade on', 'ADS trade on'),
    ('p', 'Italian Stock Exchange', 'Other Stock Exchange'),
    ('p', 'June 30, 2026', 'June 30, 2025'),
    ('a', 'ordinary shares', 'preferred shares'), ('a', '"SYN"', '"OTHER"'),
    ('a', ANNUAL, ANNUAL+' '+ANNUAL),
    ('p', PRIMARY, PRIMARY+' '+PRIMARY),
])
def test_missing_conflicting_or_depositary_disclosures_stay_incomplete(change):
    p, a, q = inputs(); which, old, new = change
    if which == 'p': p = source(PRIMARY.replace(old, new))
    else: a = source(ANNUAL.replace(old, new), annual=True)
    result = normalize(p, a, q)
    assert result['status'] == 'incomplete' and not result['documents']


@pytest.mark.parametrize('field,value', [('longName', 'Other SA'), ('symbol', 'SYN'),
    ('exchangeName', 'NYQ'), ('fullExchangeName', 'NYSE'), ('instrumentType', 'ETF'),
    ('currency', 'USD')])
def test_wrong_provider_identity_is_not_repaired_by_suffix(field, value):
    p, a, q = inputs(); raw = json.loads(q['text']); raw['observation']['identity'][field] = value
    q['text'] = json.dumps(raw); q['sha256'] = sha256(q['text'].encode()).hexdigest()
    assert normalize(p, a, q)['status'] == 'incomplete'


@pytest.mark.parametrize('problem', ['missing_identity', 'future_identity', 'old_annual',
    'wrong_issuer', 'wrong_cik', 'wrong_quote_date', 'quote_hash', 'source_hash'])
def test_date_entity_and_hash_requirements(problem):
    p, a, q = inputs(); raw = json.loads(q['text'])
    if problem == 'missing_identity': del raw['observation']['identity']
    if problem == 'future_identity': raw['observation']['identity_observed_on'] = '2026-08-03'
    if problem == 'old_annual': a['metadata']['report_date'] = '2023-12-31'
    if problem == 'wrong_issuer': a['metadata']['issuer'] = 'OTHER SA'
    if problem == 'wrong_cik': a['metadata']['emittente_id'] = 'CIK:0000000002'
    if problem == 'wrong_quote_date': raw['observation']['date'] = '2026-06-29'
    q['text'] = json.dumps(raw); q['sha256'] = sha256(q['text'].encode()).hexdigest()
    if problem == 'quote_hash': q['sha256'] = '0'*64
    if problem == 'source_hash': p['sha256'] = '0'*64
    assert normalize(p, a, q)['status'] == 'incomplete'


def test_common_collector_uses_foreign_evidence_without_second_price_fetch(tmp_path, monkeypatch):
    from bellomberg.valuation import valuation_sources
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    p, a = source(PRIMARY, tmp_path=tmp_path), source(ANNUAL, annual=True, tmp_path=tmp_path)
    monkeypatch.setattr(valuation_sources, 'collect_documents', lambda *args, **kw: {
        'status': 'ready', 'documents': [p, a], 'issues': [], 'coverage': {}})
    monkeypatch.setattr(valuation_sources, 'company_facts_documents', lambda *args, **kw: {
        'status': 'ready', 'documents': [{'id': 'synthetic-xbrl', 'text': '{}'}], 'issues': []})
    calls = []
    def price(*args): calls.append(args); return quote_raw()
    result = collect_preparation_evidence('SYN.MI', as_of='2026-08-02', archive_root=tmp_path,
                                           price_fetch=price, earnings_fetch=lambda *a, **k: [])
    assert result['preparation_ready'], result['issues']
    assert calls == [('SYN.MI', '2026-06-30')]
    assert result['components']['listing']['status'] == 'ready'
    assert len([d for d in result['documents'] if d['id'].startswith('foreign-listing-')]) == 1


@pytest.mark.parametrize('include_identity', [False, True])
def test_live_quote_adapter_preserves_identity_only_when_requested(monkeypatch, include_identity):
    import pandas as pd
    import yfinance
    from datetime import datetime, timezone
    from bellomberg.valuation.quotation_evidence import historical_quote_document
    calls = []
    class Instrument:
        history_metadata = quote_raw()['identity']
        def history(self, **kwargs):
            calls.append(kwargs)
            return pd.DataFrame({'Close': [14.5]}, index=pd.to_datetime(['2026-06-30']))
    monkeypatch.setattr(yfinance, 'Ticker', lambda ticker: Instrument())
    today = datetime.now(timezone.utc).date().isoformat()
    result = historical_quote_document('SYN.MI', on='2026-06-30', as_of=today,
                                       include_identity=include_identity)
    assert result['status'] == 'ready'
    observed = json.loads(result['documents'][0]['text'])['observation']
    assert calls == [{'start': '2026-06-30', 'end': '2026-07-01', 'auto_adjust': False,
                     'actions': False, 'raise_errors': True}]
    assert ('identity' in observed) == include_identity
    if include_identity:
        assert observed['identity'] == quote_raw()['identity']
        assert observed['identity_observed_on'] == today
