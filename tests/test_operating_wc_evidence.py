"""An issuer APM is a sourced perimeter, not an automatic forecast assumption."""
from datetime import date
from hashlib import sha256
import json
import pytest

DEFINITION=('Operating working capital is the difference between the main operating components '
    'of current assets and current liabilities. Operating working capital days is calculated '
    'in the following manner: Operating working capital days = [(Inventories + Trade receivables '
    '- Trade payables - Customer advances) / Annualized quarterly sales ] x 365. '
    'Operating working capital days is a non-IFRS alternative performance measure.')
TABLE=('(all amounts in thousands of U.S. dollars) At June 30, 2026 2025 '
       'Inventories 600 500 Trade receivables 300 250 Customer advances (20 ) (10 ) '
       'Trade payables (180 ) (140 ) Operating working capital 700 600 '
       'Annualized quarterly sales 4,000 3,600 Operating working capital days 64 61')


def originals(tmp_path, *, definition=DEFINITION, table=TABLE):
    # Use the existing synthetic layout extractor, not prepared economic records.
    from test_statement_table_evidence import source, refresh_source
    from bellomberg.valuation.statement_table_evidence import extract_statement_packet, normalize_statement_tables
    primary, raw = source(tmp_path)
    raw = raw.replace(b'<td>18</td>', b'<td>600</td>').replace(b'<td>25</td>', b'<td>300</td>')
    raw = raw.replace(b'<td>10</td>', b'<td>180</td>').replace(b'<tr><td>Total assets',
        b'<tr><td>Customer advances</td><td>20</td><td>12</td></tr><tr><td>Total assets')
    refresh_source(primary, raw)
    from pathlib import Path
    Path(primary['archive_path']).write_bytes(raw)
    primary['statement_table_fields'] = extract_statement_packet(primary, raw)
    statement = normalize_statement_tables(primary)['documents'][0]
    release = {'text': 'FORM 6-K Synthetic Issuer SA '+definition+' '+table,
        'url': 'https://www.sec.gov/Archives/edgar/data/123/000000012326000002/results.htm',
        'published_at': '2026-08-02', 'metadata': {'emittente_id': 'CIK:0000000123',
        'issuer': 'Synthetic Issuer SA', 'accession': '0000000123-26-000002', 'form': '6-K',
        'event_date': '2026-08-01', 'evidence_role': 'foreign_current_report'}}
    rehash(release)
    return primary, statement, release


def rehash(doc):
    doc['id'] = doc['document_sha256'] = doc['sha256'] = sha256(doc['text'].encode()).hexdigest()


def normalize(primary, statement, release):
    from bellomberg.valuation.operating_wc_evidence import normalize_operating_wc
    return normalize_operating_wc(release, primary, statement, on='2026-06-30', as_of='2026-08-03')


def test_disclosure_reconciles_current_components_without_adopting_a_forecast(tmp_path):
    primary, statement, release = originals(tmp_path)
    result = normalize(primary, statement, release)
    assert result['status'] == 'ready', result
    doc = result['documents'][0]; body = json.loads(doc['text'])
    assert body['reported_measure'] == {'label': 'Operating working capital',
        'value_exact': '700', 'unit': 'USD thousand', 'end': '2026-06-30',
        'comparative': {'value_exact': '600', 'end': '2025-06-30'}}
    assert len(body['reconciliation']) == 4
    assert body['forecast_perimeter_approved'] is False
    assert 'facts' not in body and 'method_records' not in body
    assert 'Annualized quarterly sales' in body['proofs'][1]['text']
    # Previous-year APM balances need not equal the different December balance column.
    facts = json.loads(statement['text'])['facts']
    for component in body['reconciliation']:
        assert component['document_id'] == statement['id']
        fact = facts[int(component['pointer'].split('/')[-1])]
        assert fact['end'] == '2026-06-30' and fact['unit'] == 'USD thousand'
    for proof in body['proofs']:
        assert ' '.join(release['text'].split())[proof['start']:proof['end']] == proof['text']


@pytest.mark.parametrize('change', ['definition', 'non_ifrs', 'missing', 'extra_cell', 'unit', 'date',
    'prior_date', 'current_sum', 'prior_sum', 'balanced_but_different', 'duplicate', 'formula_sign',
    'positive_liability', 'issuer', 'cik', 'accession', 'future', 'hash', 'statement_tamper'])
def test_missing_ambiguous_or_disagreeing_disclosure_stays_incomplete(tmp_path, change):
    primary, statement, release = originals(tmp_path)
    replacements = {
        'definition': ('Operating working capital is the difference', 'Working funds represent'),
        'non_ifrs': ('non-IFRS alternative performance measure', 'IFRS observation'),
        'missing': ('Inventories 600 500', 'Inventories - 500'),
        'extra_cell': ('Inventories 600 500', 'Inventories 600 500 400'),
        'unit': ('thousands of U.S. dollars', 'thousands of euros'),
        'date': ('At June 30, 2026', 'At June 29, 2026'),
        'prior_date': ('2026 2025 Inventories', '2026 2026 Inventories'),
        'current_sum': ('capital 700 600', 'capital 701 600'),
        'prior_sum': ('capital 700 600', 'capital 700 601'),
        'formula_sign': ('- Customer advances)', '+ Customer advances)'),
        'positive_liability': ('(20 ) (10 )', '20 (10 )')}
    if change in replacements:
        release['text'] = release['text'].replace(*replacements[change]); rehash(release)
    elif change == 'balanced_but_different':
        release['text'] = release['text'].replace('Inventories 600 500', 'Inventories 601 500').replace('capital 700 600', 'capital 701 600')
        rehash(release)
    elif change == 'duplicate': release['text'] += ' '+TABLE; rehash(release)
    elif change == 'issuer': release['metadata']['issuer'] = 'Different Company'
    elif change == 'cik': release['metadata']['emittente_id'] = 'CIK:0000000999'
    elif change == 'accession': release['metadata']['accession'] = primary['metadata']['accession']
    elif change == 'future': release['published_at'] = '2026-08-04'
    elif change == 'hash': release['text'] += ' edited'
    elif change == 'statement_tamper':
        body = json.loads(statement['text']); body['facts'][0]['value'] += 1
        statement['text'] = json.dumps(body); statement['sha256'] = sha256(statement['text'].encode()).hexdigest()
    result = normalize(primary, statement, release)
    assert result['status'] == 'incomplete' and result['issues'] and not result['documents']


@pytest.mark.parametrize('first', [True, False])
@pytest.mark.parametrize('damage', [None, 'changed_disclosure', 'changed_statement', 'missing_primary', 'missing_release'])
def test_catalog_recompiles_disclosure_and_dependencies_in_either_order(tmp_path, first, damage):
    from bellomberg.valuation.input_preparation import _catalog
    primary, statement, release = originals(tmp_path)
    doc = normalize(primary, statement, release)['documents'][0]
    originals_docs = [primary, statement, release]
    if damage == 'changed_disclosure':
        body = json.loads(doc['text']); body['reported_measure']['value_exact'] = '999'
        doc['text'] = json.dumps(body); doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    elif damage == 'changed_statement': statement['metadata']['scope'] = 'segment'
    elif damage == 'missing_primary': originals_docs.remove(primary)
    elif damage == 'missing_release': originals_docs.remove(release)
    docs = [doc, *originals_docs] if first else [*originals_docs, doc]
    catalog, issues, _ = _catalog(docs, date(2026, 8, 3))
    assert (doc['id'] in catalog) is (damage is None)
    assert bool(issues) is (damage is not None)


def test_model_prompt_keeps_verified_definition_when_narrative_is_projected(tmp_path):
    from test_preparation_view import _dossier
    from bellomberg.valuation.preparation_view import select_stage_view
    primary, statement, release = originals(tmp_path)
    doc = normalize(primary, statement, release)['documents'][0]
    dossier = _dossier(); dossier['documents'].extend([primary, statement, release, doc])
    view = select_stage_view(dossier, 'model')
    assert view['stage_view']['structured_triplet_present']
    assert view['documents'][-1] == doc and 'text' not in view['documents'][-2]


@pytest.mark.parametrize('table', [TABLE.replace('Inventories 600 500', 'Inventories 6,00 500'),
    TABLE.replace('capital 700 600', 'capital 700.000000000000000000000000000001 600'),
    TABLE+' 99', TABLE.replace('days 64 61', 'days 64 61.2.3')])
def test_malformed_or_rounded_amounts_are_not_accepted(tmp_path, table):
    assert normalize(*originals(tmp_path, table=table))['status'] == 'incomplete'


def test_supported_typography_and_exact_decimal_arithmetic(tmp_path):
    definition = DEFINITION.replace(' - ', ' \u2013 ')
    table = TABLE.replace('600 500', '600.0 500.0').replace('700 600', '700.0 600.0')
    result = normalize(*originals(tmp_path, definition=definition, table=table))
    assert result['status'] == 'ready', result


@pytest.mark.parametrize('disagreement', [False, True])
def test_common_collector_adds_disclosure_from_supplemental_reports(tmp_path, monkeypatch, disagreement):
    from test_statement_table_evidence import source
    from bellomberg.valuation import valuation_sources, quotation_evidence, preparation_earnings
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    primary, statement, release = originals(tmp_path); annual, _ = source(tmp_path, annual=True)
    if disagreement:
        release['text'] = release['text'].replace('capital 700 600', 'capital 701 600'); rehash(release)
    monkeypatch.setattr(valuation_sources, 'collect_documents', lambda *a, **k:
        {'status': 'ready', 'documents': [primary, annual], 'issues': []})
    monkeypatch.setattr(valuation_sources, 'company_facts_documents', lambda *a, **k:
        {'status': 'incomplete', 'documents': [], 'issues': []})
    monkeypatch.setattr(preparation_earnings, 'collect_earnings_evidence', lambda *a, **k:
        {'status': 'ready', 'documents': [release], 'issues': []})
    monkeypatch.setattr(quotation_evidence, 'listing_identity_document', lambda *a, **k:
        {'status': 'ready', 'documents': [{'id': 'synthetic-listing', 'text': '{}'}], 'issues': []})
    result = collect_preparation_evidence('SYNTH', as_of='2026-08-03', archive_root=tmp_path,
        price_fetch=lambda ticker, on: {'symbol': ticker, 'date': on, 'currency': 'USD', 'close': 12})
    assert result['preparation_ready'], result
    assert result['operating_wc_disclosures']['status'] == ('incomplete' if disagreement else 'ready')
    assert len([d for d in result['documents'] if d['id'].startswith('operating-wc-')]) == (0 if disagreement else 1)
    if disagreement:
        assert result['status'] == 'partial' and result['operating_wc_disclosures']['issues']
        assert any(issue['source'] == 'operating_wc_disclosure_v1' for issue in result['issues'])
