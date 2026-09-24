"""Synthetic comparative common-share disclosures and conflicting SEC tags."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

import pytest


def sources():
    text = '''SYNTHETIC FINANCIAL CORP. AND SUBSIDIARIES
Consolidated Statements of Financial Condition
(In Thousands, Except Share and Per Share Data)
June 30,
2026 2025
Assets
Cash 100 90
Stockholders' Equity
Common stock, $0.01 par value; 900,000,000 shares authorized;
12,345,678 shares and 12,100,000 shares issued and outstanding, respectively
123 121
See notes to consolidated financial statements.'''
    digest = sha256(text.encode()).hexdigest()
    primary = {'id': digest, 'document_sha256': digest, 'sha256': digest, 'text': text,
        'url': 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm',
        'published_at': '2026-08-01', 'metadata': {'form': '10-K', 'emittente_id': 'CIK:0000000123',
        'accession': '0000000123-26-000001', 'report_date': '2026-06-30', 'issuer': 'Synthetic Financial Corp.'}}
    raw = {'cik': '0000000123', 'issuer': 'SYNTHETIC FINANCIAL CORP.', 'facts': [
        {'taxonomy': 'us-gaap', 'concept': 'CommonStockSharesOutstanding', 'unit': 'shares',
         'observation': {'val': 12_100_000, 'end': '2026-06-30', 'filed': '2026-08-01', 'accn': '0000000123-26-000001'}}]}
    content = json.dumps(raw)
    tagged = {'id': 'xbrl-0000000123-000000012326000001', 'text': content,
        'sha256': sha256(content.encode()).hexdigest(), 'published_at': primary['published_at'],
        'url': 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000000123.json',
        'metadata': {'emittente_id': 'CIK:0000000123', 'accession': '000000012326000001'}}
    return primary, tagged


def normalize(primary, tagged):
    from bellomberg.valuation.statement_shares_evidence import normalize_statement_shares
    return normalize_statement_shares(primary, [tagged])


def selection(document):
    content = json.loads(document['text'])
    return {'value': 12.345678, 'evidence_ids': [document['id']], 'calculation': {
        'type': 'statement_shares', 'fact_index': 0,
        'selection_basis': 'primary_statement_over_conflicting_tags',
        'acknowledged_conflicts': content['tag_comparison']['conflicts']}}


def test_common_statement_value_keeps_comparative_date_and_conflict_without_editing_sources():
    from bellomberg.valuation.input_preparation import _catalog, _fact_proof
    primary, tagged = sources(); originals = deepcopy([primary, tagged])
    result = normalize(primary, tagged)
    assert result['status'] == 'ready', result
    doc = result['documents'][0]; content = json.loads(doc['text'])
    assert [(r['value'], r['end'], r['unit']) for r in content['facts']] == [
        (12_345_678, '2026-06-30', 'shares'), (12_100_000, '2025-06-30', 'shares')]
    assert content['tag_comparison']['status'] == 'conflict'
    assert content['tag_comparison']['conflicts'][0]['value'] == 12_100_000
    assert [primary, tagged] == originals
    catalog, issues, _ = _catalog([primary, tagged, doc], date(2026, 9, 1))
    assert not issues and doc['id'] in catalog
    assert _fact_proof('capital.shares_m', selection(doc), [doc], 'million shares',
        '2026-06-30', expected_entity='SYNTHETIC FINANCIAL CORP.') is None


@pytest.mark.parametrize('change', ['wrong_date', 'reverse_columns', 'rounded_units', 'class_a', 'nonvoting', 'duplicate_row',
    'missing_respectively', 'different_cik', 'different_tag_issuer', 'changed_text_hash'])
def test_ambiguous_or_inconsistent_statement_is_not_normalized(change):
    primary, tagged = sources()
    if change == 'wrong_date': primary['metadata']['report_date'] = '2026-03-31'
    elif change == 'reverse_columns': primary['text'] = primary['text'].replace('2026 2025', '2025 2026')
    elif change == 'rounded_units': primary['text'] = primary['text'].replace('Except Share and Per Share Data', 'Except Per Share Data')
    elif change == 'class_a': primary['text'] = primary['text'].replace('Common stock,', 'Class A common stock,')
    elif change == 'nonvoting': primary['text'] = primary['text'].replace('Common stock,', 'Nonvoting common stock,')
    elif change == 'duplicate_row': primary['text'] = primary['text'].replace('123 121', primary['text'].split("Stockholders' Equity\n")[1].split('123 121')[0]+'123 121')
    elif change == 'missing_respectively': primary['text'] = primary['text'].replace('respectively', '')
    elif change == 'different_cik': primary['metadata']['emittente_id'] = 'CIK:0000000999'
    elif change == 'different_tag_issuer':
        raw = json.loads(tagged['text']); raw['issuer'] = 'DIFFERENT CORP.'
        tagged['text'] = json.dumps(raw); tagged['sha256'] = sha256(tagged['text'].encode()).hexdigest()
    else: primary['text'] += 'Changed'
    if change != 'changed_text_hash': primary['sha256'] = sha256(primary['text'].encode()).hexdigest()
    assert normalize(primary, tagged)['status'] == 'incomplete'


@pytest.mark.parametrize('change', ['unacknowledged', 'wrong_value', 'wrong_date', 'wrong_entity', 'generic_pointer', 'other_driver'])
def test_conflict_cannot_be_silently_ignored_or_reclassified(change):
    from bellomberg.valuation.input_preparation import _fact_proof
    primary, tagged = sources(); doc = normalize(primary, tagged)['documents'][0]
    item = selection(doc); period = '2026-06-30'; entity = 'Synthetic Financial Corp.'; driver = 'capital.shares_m'
    if change == 'unacknowledged': item['calculation']['acknowledged_conflicts'] = []
    elif change == 'wrong_value': item['value'] = 12.1
    elif change == 'wrong_date': period = '2025-06-30'
    elif change == 'wrong_entity': entity = 'Different Corp.'
    elif change == 'other_driver': driver = 'opening_common_equity'
    else:
        item.pop('calculation'); item.update(quoted_value=12_345_678, quoted_unit='shares',
            evidence_pointer={'value': '/facts/0/value', 'unit': '/facts/0/unit', 'period': '/facts/0/end'})
    assert _fact_proof(driver, item, [doc], 'million shares', period, expected_entity=entity)


def test_normalized_source_is_recompiled_and_cannot_erase_conflict_or_original():
    from bellomberg.valuation.input_preparation import _catalog
    primary, tagged = sources(); doc = normalize(primary, tagged)['documents'][0]
    modified = deepcopy(doc); raw = json.loads(modified['text']); raw['tag_comparison']['conflicts'] = []
    modified['text'] = json.dumps(raw); modified['sha256'] = sha256(modified['text'].encode()).hexdigest()
    assert _catalog([primary, tagged, modified], date(2026, 9, 1))[1]
    assert _catalog([primary, doc], date(2026, 9, 1))[1]
    assert _catalog([tagged, doc], date(2026, 9, 1))[1]


@pytest.mark.parametrize('mode', ['selected_statement', 'matching_par_title', 'conflicting_tag', 'different_share_class'])
def test_compiler_retains_source_disagreement_and_blocks_silent_tag_selection(mode):
    from bellomberg.valuation.input_preparation import _catalog, _compile
    primary, tagged = sources(); doc = normalize(primary, tagged)['documents'][0]
    cutoff = date(2026, 9, 1); catalog, issues, _ = _catalog([primary, tagged, doc], cutoff)
    assert not issues
    item = {**selection(doc), 'kind': 'historical', 'rationale': 'Printed common shares selected.',
        'valid_until': '2026-09-01', 'valid_until_basis': {'policy': 'same_day', 'as_of': '2026-09-01'}}
    if mode == 'conflicting_tag':
        item.pop('calculation'); item.update(value=12.1, evidence_ids=[tagged['id']], quoted_value=12_100_000,
            quoted_unit='shares', evidence_pointer={'value': '/facts/0/observation/val',
            'unit': '/facts/0/unit', 'period': '/facts/0/observation/end'})
    plan = {'model': {'shares': item}, 'scenarios': {name: {} for name in ('bear', 'base', 'bull')}}
    perimeter = {'entity': 'Synthetic Financial Corp.', 'currency': 'USD',
                 'share_class': ('Class A' if mode == 'different_share_class' else
                    'Common Stock, $0.01 par value' if mode == 'matching_par_title' else 'Common Stock')}
    schema = {'shares': ('shares', 'million shares', 'common', 'opening', 'number', 'model')}
    records, issues, _ = _compile(plan, schema, {}, perimeter, {'valuation_date': '2026-06-30'}, None, catalog, cutoff)
    if mode in ('selected_statement', 'matching_par_title'):
        assert not issues and len(records) == 1
        assert records[0]['value'] == 12.345678
        assert 'SOURCE DISAGREEMENT' in records[0]['rationale']
        assert '12100000' in records[0]['rationale']
        assert 'not an issuer correction or PM approval' in records[0]['rationale']
    else:
        assert not records and issues[0]['code'] == 'source_disagreement'


def test_consistent_tag_does_not_create_a_false_conflict_and_missing_tag_stays_missing():
    primary, tagged = sources(); raw = json.loads(tagged['text'])
    raw['facts'][0]['observation']['val'] = 12_345_678
    tagged['text'] = json.dumps(raw); tagged['sha256'] = sha256(tagged['text'].encode()).hexdigest()
    result = normalize(primary, tagged)
    assert result['status'] == 'ready' and result['source_disagreement'] is False
    assert json.loads(result['documents'][0]['text'])['tag_comparison']['conflicts'] == []
    raw['facts'][0]['observation']['end'] = '2025-06-30'
    tagged['text'] = json.dumps(raw); tagged['sha256'] = sha256(tagged['text'].encode()).hexdigest()
    assert normalize(primary, tagged)['status'] == 'incomplete'


@pytest.mark.parametrize('change', ['equivalent_receipt', 'other_url', 'current_count', 'comparative_count', 'authorized_count'])
def test_multiple_statement_receipts_require_exact_equivalence_before_reusing_selection(change):
    from bellomberg.valuation.input_preparation import _catalog, _compile
    primary, tagged = sources()
    second = deepcopy(primary)
    second['text'] += '\n'  # Different acquired bytes; same normalized statement.
    if change == 'other_url': second['url'] = second['url'].replace('report.htm', 'other.htm')
    elif change == 'current_count': second['text'] = second['text'].replace('12,345,678', '12,100,000')
    elif change == 'comparative_count': second['text'] = second['text'].replace('12,100,000', '12,000,000')
    elif change == 'authorized_count': second['text'] = second['text'].replace('900,000,000', '800,000,000')
    digest = sha256(second['text'].encode()).hexdigest()
    second.update(id=digest, document_sha256=digest, sha256=digest)
    docs = [normalize(source, tagged)['documents'][0] for source in (primary, second)]
    inputs = [primary, second, tagged, *docs]; original = deepcopy(inputs)
    cutoff = date(2026, 9, 1)
    catalog, issues, _ = _catalog(inputs, cutoff)
    assert not issues and docs[0]['id'] != docs[1]['id']
    for selected in docs:
        item = {**selection(selected), 'kind': 'historical', 'rationale': 'Explicit statement selection with conflict retained.',
                'valid_until': '2026-09-01', 'valid_until_basis': {'policy': 'same_day', 'as_of': '2026-09-01'}}
        plan = {'model': {'shares': item}, 'scenarios': {name: {} for name in ('bear', 'base', 'bull')}}
        records, problems, _ = _compile(plan, {'shares': ('shares', 'million shares', 'common', 'opening', 'number', 'model')},
            {}, {'entity': 'Synthetic Financial Corp.', 'currency': 'USD', 'share_class': 'Common Stock'},
            {'valuation_date': '2026-06-30'}, None, catalog, cutoff)
        if change == 'equivalent_receipt':
            assert not problems and len(records) == 1
            assert records[0]['value'] == 12.345678 and 'SOURCE DISAGREEMENT' in records[0]['rationale']
        else:
            assert not records and problems[0]['code'] == 'source_disagreement'
    assert inputs == original


def test_unrelated_document_metadata_does_not_crash_source_classification():
    from bellomberg.valuation.statement_shares_evidence import is_statement_shares
    assert not is_statement_shares({'id': 'other-source', 'metadata': 'legacy unstructured note'})


def test_common_collector_adds_statement_observation_and_reports_disagreement(tmp_path, monkeypatch):
    from bellomberg.valuation import valuation_sources, preparation_exhibits, preparation_earnings, quotation_evidence
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    primary, tagged = sources(); originals = deepcopy([primary, tagged])
    path = tmp_path/'statement.html'; path.write_text(primary['text'], encoding='utf-8')
    primary['archive_path'] = str(path)
    monkeypatch.setattr(valuation_sources, 'collect_documents', lambda *a, **k: {
        'status': 'ready', 'documents': [primary], 'issues': [], 'coverage': {'reused': 1}})
    monkeypatch.setattr(valuation_sources, 'company_facts_documents', lambda *a, **k: {
        'status': 'ready', 'documents': [tagged], 'issues': []})
    empty = lambda *a, **k: {'status': 'ready', 'documents': [], 'issues': []}
    monkeypatch.setattr(preparation_exhibits, 'collect_preparation_exhibits', empty)
    monkeypatch.setattr(preparation_earnings, 'collect_earnings_evidence', empty)
    monkeypatch.setattr(quotation_evidence, 'listing_identity_document', lambda *a, **k: {
        'status': 'ready', 'documents': [{'id': 'synthetic-listing', 'text': '{}'}], 'issues': []})
    result = collect_preparation_evidence('SYNTH', as_of='2026-09-01', archive_root=tmp_path,
        price_fetch=lambda ticker, on: {'symbol': ticker, 'date': on, 'currency': 'USD', 'close': 8})
    assert result['preparation_ready'] and result['status'] == 'partial'
    assert result['statement_shares']['source_disagreement'] is True
    assert any(d['id'].startswith('statement-shares-') for d in result['documents'])
    assert any(r['source'] == 'statement_shares' for r in result['issues'])
    primary.pop('archive_path')
    assert [primary, tagged] == originals
