"""Synthetic PDF balances: whole-page coverage before economic classification."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

import pytest

from test_pdf_statement_evidence import primary
from bellomberg.valuation.balance_sheet_evidence import collect_balance_sheet, normalize_balance_sheet
from bellomberg.valuation.statement_table_evidence import collect_statement_tables, _json


def rows():
    data = [('Noncurrent assets', None), ('Property, plant and equipment', 40),
        ('Other intangible assets', 10), (None, 50), ('Current assets', None),
        ('Inventories', 20), ('Cash and cash equivalents', 5), ('Other receivables', 0),
        (None, 25), ('Total assets', 75), ('Equity', None), ('Capital stock', 20),
        ('Other reserves', 10), ('Equity attributable to Synthetic stockholders', 30),
        ('Equity attributable to noncontrolling interest', 5), (None, 35),
        ('Noncurrent liabilities', None), ('Financial liabilities', 20),
        ('Other liabilities', 5), (None, 25), ('Current liabilities', None),
        ('Trade accounts payable', 10), ('Income tax liabilities', 5), (None, 15),
        ('Total equity and liabilities', 75)]
    return [(label, [] if value is None else [str(value)]*3) for label, value in data]


def case(tmp_path, data=None):
    source = primary(tmp_path, balance_rows=rows() if data is None else data)
    tables = collect_statement_tables([source], tmp_path)
    assert tables['status'] == 'ready', tables
    source['statement_table_fields'] = tables['packets'][source['id']]
    return source, normalize_balance_sheet(source)


def test_pdf_balance_reconciles_all_sections_without_fabricated_tags_or_judgments(tmp_path):
    source, result = case(tmp_path)
    assert result['status'] == 'ready', result
    ledger = result['documents'][0]; body = json.loads(ledger['text'])
    leaves = body['components']
    assert len(leaves) == 9 and len({f['reported_tag'] for f in leaves}) == 9
    assert sum(int(f['value_exact']) for f in leaves if f['accounting_side'] == 'asset') == 75
    assert sum(int(f['value_exact']) for f in leaves if f['accounting_side'] == 'liability') == 40
    assert all(f['namespace'] == 'reported-statement' for f in leaves)
    assert all(f['economic_classification'] == 'unreviewed' and f['model_treatment'] is None for f in leaves)
    assert body['reported_balance_reconciled'] and not body['economic_classification_approved']
    assert ledger['metadata']['entity'] == source['metadata']['emittente_id']
    assert ledger['metadata']['security_identity_verified'] is False
    assert any(f['value_exact'] == '0' for f in leaves)
    assert any(g['parent']['label'] is None for g in body['groups'])
    from bellomberg.valuation.input_preparation import _catalog
    catalog, issues, _ = _catalog([ledger, source], date(2025, 9, 1))
    assert not issues and len(catalog) == 2, issues
    forged = deepcopy(ledger); content = json.loads(forged['text'])
    content['components'][0]['value_exact'] = '0'
    forged['text'] = _json(content); forged['sha256'] = sha256(forged['text'].encode()).hexdigest()
    catalog, issues, _ = _catalog([forged, source], date(2025, 9, 1))
    assert forged['id'] not in catalog and issues


@pytest.mark.parametrize('mutation', ['missing_cell', 'bad_subtotal', 'bad_equity', 'extra_row', 'unknown_section'])
def test_incomplete_or_unreconciled_printed_balance_is_not_a_ledger(tmp_path, mutation):
    data = rows()
    if mutation == 'missing_cell': data[6][1][-1] = '-'
    elif mutation == 'bad_subtotal': data[3][1][-1] = '51'
    elif mutation == 'bad_equity': data[13][1][-1] = '31'
    elif mutation == 'extra_row': data.insert(9, ('Unexplained subtotal', ['25']*3))
    else: data[0] = ('Unclassified assets', [])
    _, result = case(tmp_path, data)
    assert result['status'] == 'incomplete' and result['documents'] == [], result


@pytest.mark.parametrize('mutation', ['omit_zero_row', 'omit_section', 'change_section', 'omit_closing'])
def test_rehashed_geometry_cannot_omit_or_reclassify_primary_balance_rows(tmp_path, mutation):
    source, _ = case(tmp_path)
    packet = source['statement_table_fields']; words = packet['pages'][1]['words']
    target = {'omit_zero_row':'Other', 'omit_section':'Noncurrent', 'change_section':'Noncurrent',
              'omit_closing':'Total'}[mutation]
    candidates = [w for w in words if w['text'] == target]
    # Other receivables is the second Other; closing Total is the last Total.
    word = candidates[1] if mutation == 'omit_zero_row' else candidates[-1] if mutation == 'omit_closing' else candidates[0]
    if mutation == 'change_section': word['text'] = 'Current'
    else: words[:] = [w for w in words if abs(w['bottom']-word['bottom']) > 1]
    packet['sha256'] = sha256(_json({k:v for k,v in packet.items() if k != 'sha256'}).encode()).hexdigest()
    result = normalize_balance_sheet(source)
    assert result['status'] == 'incomplete' and not result['documents'], result


def test_pdf_balance_collector_and_shared_preparer_use_the_same_replay(tmp_path, monkeypatch):
    from bellomberg.valuation import valuation_sources
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    source, normalized = case(tmp_path)
    result = collect_balance_sheet(source, tmp_path)
    assert result['status'] == 'ready' and result['documents'] == normalized['documents'], result
    monkeypatch.setattr(valuation_sources, 'collect_documents', lambda *a,**k: {
        'status':'ready','documents':[source],'issues':[],'coverage':{}})
    report = collect_preparation_evidence('SYNTH.DE', as_of='2025-09-01', archive_root=tmp_path,
        method_id='operating_fcff', price_fetch=lambda ticker,on:{'symbol':ticker,'date':on,'currency':'EUR','close':10},
        earnings_fetch=lambda *_a,**_k:pytest.fail('unexpected fetch'),
        download=lambda *_a,**_k:pytest.fail('unexpected download'))
    assert report['balance_sheet']['status'] == 'ready', report['balance_sheet']
    assert not report['preparation_ready']  # Annual and security identity still absent.


def test_pdf_classification_uses_original_exact_issuer_id_and_rejects_cash_as_nwc(tmp_path):
    from bellomberg.valuation.balance_working_capital import balance_nwc_proof
    from bellomberg.valuation.input_preparation import _source_scale
    source, result = case(tmp_path)
    assert result['status'] == 'ready', result
    ledger = result['documents'][0]; components = json.loads(ledger['text'])['components']
    treatments = {'Inventories':'operating_nwc', 'Other receivables':'operating_nwc',
        'Trade accounts payable':'operating_nwc', 'Cash and cash equivalents':'cash_or_investment',
        'Financial liabilities':'financing', 'Income tax liabilities':'income_tax'}
    choices = [{'component_tag':f['reported_tag'], 'treatment':treatments.get(f['label'],'nonoperating'),
        'judgment':'analyst_estimate', 'rationale':'Synthetic explicit economic perimeter.',
        'evidence_ids':[source['id']], 'evidence_quote':f['proof']['row_literal']['quote']} for f in components]
    item = {'value':10, 'evidence_ids':[source['id'],ledger['id']], 'calculation':{
        'operation':'balance_sheet_nwc','balance_document_id':ledger['id'],
        'classifications':choices,'nonmonetary_review':[]}}
    def prove(entity=None):
        return balance_nwc_proof(item,[source,ledger],'EUR million','2025-06-30',
            entity or source['metadata']['emittente_id'],scale=_source_scale)
    assert prove() is None
    assert prove('Synthetic Industries SE') is not None  # No silent prefix/identity mapping.
    next(r for r in choices if r['treatment']=='cash_or_investment')['treatment']='operating_nwc'
    item['value']=15
    assert 'cannot be smuggled' in prove()
