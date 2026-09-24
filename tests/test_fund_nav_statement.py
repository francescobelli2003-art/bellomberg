"""Comparative NAV observations stay bound to their printed class and column."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.fund_nav_statement import normalize_fund_statement
from bellomberg.valuation.input_preparation import _catalog, _day, _source_scale
from bellomberg.valuation.fund_nav_preparation import prove_nav


BODY = '''Synthetic Holding Ltd. 26
UNAUDITED CONDENSED INTERIM STATEMENT OF FINANCIAL POSITION
As of June 30, 2026 and December 31, 2025
(Stated in United States Dollars)
2026 2025
Notes Unaudited Audited
Assets
Cash and cash equivalents $ 20 $ 10
Total Assets $ 200 $ 160
Total Liabilities $ 90 $ 60
Total Equity 110 100
Total Liabilities and Equity $ 200 $ 160
Net assets attributable to Public Shares $ 100 $ 90
Public Shares outstanding 10 10
Net assets per Public Share $ 10.00 $ 9.00
Net assets attributable to Special Voting Share $ 10 $ 10
Special Voting Share outstanding 1 1
Net assets per Special Voting Share $ 9.89 $ 10.00
The accompanying notes form an integral part of these statements.
'''


def source(body=BODY):
    digest = sha256(body.encode()).hexdigest()
    return {'id': 'a'*64, 'document_sha256': 'a'*64,
        'url': 'https://example.org/synthetic/interim.pdf', 'published_at': '2026-08-12',
        'text': body, 'sha256': digest,
        'metadata': {'issuer': 'Synthetic Holding Ltd.', 'report_date': '2026-06-30'},
        'page_references': [{'pagina': 1, 'inizio': 0, 'fine': len(body), 'sha256': digest}]}


def normalized(primary=None):
    result = normalize_fund_statement(primary or source())
    assert result['status'] == 'ready', result['issues']
    return result['documents'][0]


def proof(document, concept, *, share_class='Public Shares', on='2026-06-30', scale=1.):
    facts = json.loads(document['text'])['facts']
    index, fact = next((i,f) for i,f in enumerate(facts)
                      if f['concept'] == concept and f.get('share_class') == share_class and f['end'] == on)
    return {'value': fact['value']*scale, 'evidence_ids': [document['id']],
            'quoted_value': fact['value'], 'quoted_unit': fact['unit'],
            'evidence_pointer': {'value': f'/facts/{index}/value', 'unit': f'/facts/{index}/unit',
                                 'period': f'/facts/{index}/end'}}


def test_two_classes_and_two_dates_keep_amounts_shares_precision_and_source_offsets():
    doc = normalized()
    raw = json.loads(doc['text'])
    assert len(raw['facts']) == 26
    for fact in raw['facts']:
        locator = fact['source_locator']
        assert BODY[locator['start']:locator['end']] == locator['text']
    assert proof(doc, 'ClassNavPerShare')['value'] == 10.
    assert proof(doc, 'ClassNavPerSharePrecision')['value'] == 2
    assert proof(doc, 'ClassNetAssets', share_class='Special Voting Share')['value'] == 10.
    assert proof(doc, 'ClassNavPerShare', on='2025-12-31')['value'] == 9.
    assert raw['checks']['class_attribution_reconciled'] is True
    assert raw['limitation'] and 'method_records' not in raw


@pytest.mark.parametrize('change', [
    ('2026 2025', '2025 2026'), ('United States Dollars', 'Japanese Yen'),
    ('United States Dollars', 'Euros'),  # Dollar cells cannot silently become euros.
    ('Total Equity 110 100', 'Total Equity 999 100'),
    ('Total Liabilities and Equity $ 200 $ 160', 'Total Liabilities and Equity $ 201 $ 160'),
    ('Public Shares outstanding 10 10', 'Public Shares outstanding 10'),
    ('Public Shares outstanding 10 10', 'Public Shares outstanding - 10'),
    ('Net assets per Public Share $ 10.00 $ 9.00', 'Net assets per Public Share $ 12.00 $ 9.00'),
    ('Net assets attributable to Special Voting Share $ 10 $ 10', ''),
    ('Total Assets $ 200 $ 160', 'Total Assets $ 200 $ 160\nTotal Assets $ 200 $ 160'),
    ('Public Shares outstanding 10 10', 'Public Shares outstanding 10.5 10'),
])
def test_ambiguous_or_unreconciled_printed_tables_are_not_normalized(change):
    result = normalize_fund_statement(source(BODY.replace(*change)))
    assert result['status'] == 'incomplete' and result['documents'] == []


@pytest.mark.parametrize('fault', ['source_missing', 'value', 'class', 'period', 'raw_hash', 'page_hash'])
def test_catalog_recompiles_normalized_facts_from_primary_pages(fault):
    primary = source()
    doc = normalized(primary)
    if fault in ('value', 'class', 'period'):
        raw = json.loads(doc['text'])
        field, value = {'value': ('value', 999), 'class': ('share_class', 'Other Shares'),
                        'period': ('end', '2026-09-24')}[fault]
        raw['facts'][0][field] = value
        doc['text'] = json.dumps(raw)
        doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    elif fault == 'raw_hash':
        primary['text'] += ' altered'
    elif fault == 'page_hash':
        primary['page_references'][0]['sha256'] = 'f'*64
    documents = [doc] if fault == 'source_missing' else [primary, doc]
    catalog, issues, _ = _catalog(documents, _day('2026-09-24'))
    assert issues and doc['id'] not in catalog


def test_class_bound_structured_proofs_reach_common_numeric_compiler():
    from bellomberg.valuation.input_preparation import _fact_proof
    doc = normalized()
    catalog, issues, _ = _catalog([source(), doc], _day('2026-09-24'))
    assert not issues
    document = catalog[doc['id']]
    perimeter = {'entity': 'Synthetic Holding Ltd.', 'currency': 'USD', 'share_class': 'Public Shares'}
    observations = [('shares', 'ClassSharesOutstanding', 'million shares', 1e-6),
        ('reported_nav_per_share', 'ClassNavPerShare', 'USD per share', 1.),
        ('reported_nav_precision', 'ClassNavPerSharePrecision', 'decimals', 1)]
    for driver, concept, unit, scale in observations:
        item = proof(doc, concept, scale=scale)
        model = {'reported_nav_per_share': proof(doc, 'ClassNavPerShare')}
        assert prove_nav(driver, item, [document], unit, '2026-06-30', perimeter, model, _fact_proof) is None
        other = {**perimeter, 'share_class': 'Special Voting Share'}
        assert prove_nav(driver, item, [document], unit, '2026-06-30', other, {}, _fact_proof)
        item['evidence_pointer']['value'] = item['evidence_pointer']['period']
        assert prove_nav(driver, item, [document], unit, '2026-06-30', perimeter, {}, _fact_proof)


def test_wrong_semantic_observation_cannot_prove_nav_and_no_estimate_is_added():
    from bellomberg.valuation.input_preparation import _fact_proof
    doc = normalized()
    perimeter = {'entity':'Synthetic Holding Ltd.','currency':'USD','share_class':'Public Shares'}
    wrong = proof(doc, 'ClassNetAssets')
    assert prove_nav('reported_nav_per_share', wrong, [doc], 'USD per share', '2026-06-30', perimeter, {}, _fact_proof)
    assert prove_nav('reported_nav_per_share', {**proof(doc,'ClassNavPerShare'),'evidence_quote':'ignored'},
                     [doc], 'USD per share', '2026-06-30', perimeter, {}, _fact_proof)


@pytest.mark.parametrize('fault', ['missing_nav', 'other_source', 'other_class', 'other_date', 'wrong_price'])
def test_printed_precision_is_bound_to_the_actual_reported_nav_observation(fault):
    from bellomberg.valuation.input_preparation import _fact_proof
    doc = normalized()
    item = proof(doc, 'ClassNavPerSharePrecision', scale=1)
    nav = proof(doc, 'ClassNavPerShare')
    if fault == 'other_source':
        nav['evidence_ids'] = ['another-report']
    elif fault == 'other_class':
        nav = proof(doc, 'ClassNavPerShare', share_class='Special Voting Share')
    elif fault == 'other_date':
        nav = proof(doc, 'ClassNavPerShare', on='2025-12-31')
    elif fault == 'wrong_price':
        nav['value'] = 999.
    perimeter = {'entity': 'Synthetic Holding Ltd.', 'currency': 'USD', 'share_class': 'Public Shares'}
    model = {} if fault == 'missing_nav' else {'reported_nav_per_share': nav}
    assert prove_nav('reported_nav_precision', item, [doc], 'decimals', '2026-06-30',
                     perimeter, model, _fact_proof)


@pytest.mark.parametrize('publication_source', ['same_primary', 'unrelated_primary'])
def test_common_preparer_normalizes_primary_table_before_proposal(tmp_path, publication_source):
    from test_fund_nav_preparation import fixture, prepare, DATE, DAY
    from bellomberg.valuation.fund_nav_preparation import FACT_KEYS
    from bellomberg.valuation.dcf_engine import generate_valuation
    plan, literal = json.loads(json.dumps(fixture()).replace('SYNTH-GROUP', 'Synthetic Holding Ltd.')
                              .replace('ordinary', 'Public Shares').replace('"nav"', '"'+'a'*64+'"'))
    body = '''Synthetic Holding Ltd. 26
STATEMENT OF FINANCIAL POSITION
As of December 31, 2025 and December 31, 2024
(Stated in Euros)
2025 2024
Cash and cash equivalents $ 5,000,000 $ 5,000,000
Total Assets $ 125,000,000 $ 115,000,000
Total Liabilities $ 25,000,000 $ 25,000,000
Total Equity 100,000,000 90,000,000
Net assets attributable to Public Shares $ 100,000,000 $ 90,000,000
Public Shares outstanding 10,000,000 10,000,000
Net assets per Public Share $ 10.00 $ 9.00
'''.replace('$ ', '').rstrip()
    secondary = literal[0]['text']
    primary = source(body+'\n'+secondary)
    primary['page_references'] = [
        {'pagina': 1, 'inizio': 0, 'fine': len(body), 'sha256': sha256(body.encode()).hexdigest()},
        {'pagina': 2, 'inizio': len(body)+1, 'fine': len(primary['text']),
         'sha256': sha256(secondary.encode()).hexdigest()}]
    primary['published_at'] = DAY
    primary['metadata']['report_date'] = DATE
    documents = [primary]
    if publication_source == 'unrelated_primary':
        other = {**deepcopy(primary), 'id': 'b'*64, 'document_sha256': 'b'*64}
        documents.append(other)
        plan['model']['publication']['evidence_ids'] = [other['id']]
    seen = []
    def proposer(dossier, contract):
        normalized_doc = next(d for d in dossier['documents'] if d['id'] == 'fund-nav-statement-'+primary['id'])
        seen.append(normalized_doc['id'])
        assert dossier['fund_nav_statement_observations']['status'] == 'ready'
        assert contract['fund_nav_preparation']['structured_observations']
        for driver, concept, scale in [('shares','ClassSharesOutstanding',1e-6),
                ('reported_nav_per_share','ClassNavPerShare',1.),
                ('reported_nav_precision','ClassNavPerSharePrecision',1)]:
            envelope = plan['model'][driver]
            for key in FACT_KEYS:
                envelope.pop(key, None)
            envelope.update(proof(normalized_doc, concept, on=DATE, scale=scale))
        return plan
    prepared = prepare(docs=documents, proposer=proposer)
    assert seen == ['fund-nav-statement-'+primary['id']], prepared['issues']
    assert all(not doc['id'].startswith('fund-nav-statement-') for doc in documents)
    if publication_source == 'unrelated_primary':
        assert prepared['status'] == 'incomplete'
        assert any(issue['field'] == 'publication' for issue in prepared['issues'])
        return
    assert prepared['status'] == 'prepared', prepared['issues']
    result = generate_valuation('SYNTH-NAV', prepared_bundle=prepared['bundle'], output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'] and result['fair_value_base'] == 10.


def test_incomplete_table_keeps_acquisition_gap_visible_without_fabricated_observations():
    from test_fund_nav_preparation import prepare
    captured = {}
    primary = source(BODY.replace('Public Shares outstanding 10 10', 'Public Shares outstanding - 10'))
    def proposer(dossier, contract):
        captured.update(dossier)
        return {'model': {}, 'scenarios': {}, 'scenario_rationale': {}}
    result = prepare(docs=[primary], proposer=proposer)
    assert result['status'] == 'incomplete'
    assert len(captured['documents']) == 1
    report = captured['fund_nav_statement_observations']
    assert report['status'] == 'incomplete' and report['issues']
    assert result['bundle']['case']['records'] == []


@pytest.mark.parametrize('metadata', [None, 'not-an-object', []])
def test_malformed_statement_metadata_returns_a_gap_instead_of_crashing(metadata):
    from bellomberg.valuation.fund_nav_statement import statement_documents
    primary = source()
    primary['metadata'] = metadata
    documents, report = statement_documents([primary])
    assert documents == [primary] and report['status'] == 'incomplete'


def test_existing_observations_are_not_duplicated_or_repaired_when_tampered():
    from bellomberg.valuation.fund_nav_statement import statement_documents
    primary, doc = source(), normalized()
    augmented, report = statement_documents([primary, doc])
    assert augmented == [primary, doc] and report['status'] == 'ready'
    doc['text'] += ' '
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    augmented, _ = statement_documents([primary, doc])
    catalog, issues, _ = _catalog(augmented, _day('2026-09-24'))
    assert issues and doc['id'] not in catalog
