"""Bounded public-bank acquisition with synthetic SEC and FDIC evidence."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from test_fdic_evidence import _document


def _sources():
    url = 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/annual.htm'
    primary = {'id': 'primary', 'url': url, 'published_at': '2026-08-01',
        'metadata': {'emittente_id': 'CIK:0000000123', 'issuer': 'Synthetic Holding.',
                     'form': '10-K', 'report_date': '2026-06-30'}}
    text = 'Subsidiaries\nSynthetic Bank\nUnited States\n100%\n'
    exhibit = {'id': 'exhibit', 'url': url.replace('annual.htm', 'ex21.htm'),
        'text': text, 'sha256': sha256(text.encode()).hexdigest(), 'published_at': '2026-08-01',
        'metadata': {**primary['metadata'], 'form': 'EX-21', 'primary_document_id': 'primary'}}
    return primary, [primary, exhibit]


def _collect(tmp_path, *, fault=None, sources=None, as_of='2026-09-10'):
    from bellomberg.valuation.preparation_bank_sources import collect_bank_sources
    primary, docs = deepcopy(sources or _sources())
    payload = json.loads(_document()['text'])
    row = payload['data'][0]['data']
    if fault == 'parent': row['NAMEHCR'] = 'UNRELATED HOLDING'
    if fault == 'bank': row['NAME'] = 'UNRELATED BANK'
    if fault == 'period': row['REPDTE'] = '20260331'
    if fault == 'amount': row['RBCT1C'] = None
    if fault == 'ambiguous': payload['meta']['total'] = 2
    if fault == 'missing': payload['data'] = []; payload['meta']['total'] = payload['totals']['count'] = 0
    if fault == 'bad_rssd': row['RSSDHCR'] = 'unknown'
    calls = []
    def download(url, root, **kwargs):
        calls.append(url)
        assert kwargs == {'host_consentiti': ['api.fdic.gov'], 'public_only': True}
        assert urlsplit(url).path == '/banks/financials'
        query = parse_qs(urlsplit(url).query)
        assert query['filters'] == ['NAME:"SYNTHETIC BANK" AND REPDTE:20260630']
        assert query['limit'] == ['2'] and 'CHBAL' in query['fields'][0]
        if fault == 'unavailable': return {'stato': 'errore', 'motivo': 'synthetic HTTP 503'}
        raw = json.dumps(payload).encode()
        path = Path(root)/'fdic.json'; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        return {'stato': 'ok', 'path': str(path), 'sha256': sha256(raw).hexdigest() if fault != 'hash' else '0'*64,
                'url_finale': url if fault != 'redirect' else 'https://api.fdic.gov/banks/institutions'}
    result = collect_bank_sources(primary=primary, documents=docs, on='2026-06-30', as_of=as_of,
        archive_root=tmp_path, download=download, now=datetime(2026,9,10,10,tzinfo=timezone.utc))
    return result, calls


def test_declared_bank_resolves_exact_quarter_and_reuses_existing_normalizer(tmp_path):
    from bellomberg.valuation.input_preparation import _catalog, _day
    result, calls = _collect(tmp_path)
    assert result['status'] == 'ready' and len(calls) == 1
    raw, normalized = result['documents']
    assert normalized['metadata']['normalizer'] == 'fdic_financials_v1'
    assert normalized['metadata']['parent_rssd'] == 34567
    assert normalized['published_at'] is None and normalized['available_at'] == '2026-09-10'
    candidate, = result['bank_candidates']
    assert candidate['name'] == 'Synthetic Bank'
    assert candidate['source_reference']['document_id'] == 'exhibit'
    assert candidate['source_reference']['quote'] == 'Synthetic Bank'
    _, issues, _ = _catalog([raw, normalized], _day('2026-09-10'))
    assert not issues
    assert 'ownership' in result['limitation'] and 'parent-only' in result['limitation']


@pytest.mark.parametrize('fault', ['parent','bank','period','amount','ambiguous','missing',
                                  'bad_rssd','hash','redirect','unavailable'])
def test_wrong_or_unavailable_bank_source_never_becomes_complete(tmp_path, fault):
    result, calls = _collect(tmp_path, fault=fault)
    assert len(calls) == 1 and result['status'] == 'incomplete' and result['issues']
    assert not result['documents']


@pytest.mark.parametrize('fault', ['issuer','orphan','text_hash','missing','too_many','future','opening_future','opening_period'])
def test_unqualified_subsidiary_evidence_cannot_start_bank_lookup(tmp_path, fault):
    primary, docs = _sources(); exhibit = docs[1]
    if fault == 'issuer': exhibit['metadata']['emittente_id'] = 'CIK:0000000999'
    elif fault == 'orphan': exhibit['metadata']['primary_document_id'] = 'other'
    elif fault == 'text_hash': exhibit['sha256'] = '0'*64
    elif fault == 'future': exhibit['published_at'] = '2026-10-01'
    elif fault == 'opening_future': primary['published_at'] = '2026-10-01'
    elif fault == 'opening_period': primary['metadata']['report_date'] = '2026-03-31'
    elif fault == 'missing': docs.pop()
    else:
        exhibit['text'] = '\n'.join('Synthetic '+str(i)+' Bank' for i in range(5))
        exhibit['sha256'] = sha256(exhibit['text'].encode()).hexdigest()
    result, calls = _collect(tmp_path, sources=(primary,docs))
    assert result['status'] == 'incomplete' and result['issues'] and not calls


def test_observed_today_response_cannot_be_backdated_to_old_analysis(tmp_path):
    result, calls = _collect(tmp_path, as_of='2026-09-09')
    assert result['status'] == 'incomplete' and not result['documents']
    assert not calls  # Known availability mismatch is checked before downloading.


def test_repeated_exhibit_name_does_not_duplicate_lookup(tmp_path):
    primary, docs = _sources()
    docs[1]['text'] += 'Subsidiaries of Synthetic Bank\nSynthetic Bank\n'
    docs[1]['sha256'] = sha256(docs[1]['text'].encode()).hexdigest()
    result, calls = _collect(tmp_path, sources=(primary,docs))
    assert result['status'] == 'ready' and len(calls) == 1


@pytest.mark.parametrize('bank_status', ['ready','incomplete'])
def test_bank_evidence_is_used_by_common_collector_only_for_bank_method(tmp_path, monkeypatch, bank_status):
    from test_preparation_sources import _setup
    from bellomberg.valuation import preparation_bank_sources, preparation_parent_sources, market_reference_evidence
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    _setup(monkeypatch,tmp_path); calls=[]
    document = {'id':'synthetic-regulatory','text':'{}'}
    def collect(**kwargs):
        calls.append(kwargs)
        return {'status':bank_status, 'documents':[document] if bank_status=='ready' else [],
                'issues':[] if bank_status=='ready' else [{'source':'FDIC','reason':'synthetic unavailable'}]}
    monkeypatch.setattr(preparation_bank_sources,'collect_bank_sources',collect)
    monkeypatch.setattr(preparation_parent_sources,'collect_parent_sources',lambda **k:{'status':'ready','documents':[],'issues':[]})
    monkeypatch.setattr(market_reference_evidence,'collect_market_references',lambda **k:{'documents':[],'issues':[]})
    kwargs=dict(as_of='2026-02-02',archive_root=tmp_path,financial_currency='USD',
        price_fetch=lambda t,on:{'symbol':t,'date':on,'currency':'USD','close':12.5})
    regular=collect_preparation_evidence('SYNTH',method_id='operating_fcff',**kwargs)
    assert not calls and 'bank_regulatory_sources' not in regular
    bank=collect_preparation_evidence('SYNTH',method_id='bank_residual_income',**kwargs)
    assert len(calls)==1 and bank['bank_regulatory_sources']['status']==bank_status
    assert bank['preparation_ready'] == (bank_status=='ready')
    assert (document in bank['documents']) == (bank_status=='ready')
    if bank_status=='incomplete': assert bank['issues']
