"""Synthetic SEC layouts and citation boundaries; no issuer-specific facts."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from test_preparation_sections import _budgeted


def _document(ident, text, **metadata):
    return {'id': ident, 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'document_sha256': 'b' * 64, 'url': 'https://example.test/' + ident,
            'published_at': '2025-08-01',
            'metadata': {'emittente_id': 'CIK:0000000999', **metadata}}


def _case(*, appendix=False):
    annual = '''SEC cover and table of contents.
Item 1.
Business
3
Item 1. Business
Annual products, customers and competition.
Item 1A. Risk Factors
Annual risk factors that remain important.
Item 1B. Unresolved Staff Comments
No unresolved comments.
Item 2. Properties
Annual facilities.
Item 3. Legal Proceedings
Annual material litigation.
Item 4. Mine Safety Disclosures
No relevant disclosure.
Item 5. Market for Registrant's Common Equity
Annual shareholder information.
Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations
Annual operating drivers and management discussion.
Item 7A. Quantitative and Qualitative Disclosures about Market Risk
Annual market risk and hedges.
Item 8. Financial Statements and Supplementary Data
Annual financial statements with complete notes below.
CONSOLIDATED BALANCE SHEETS
Annual balances.
NOTES TO CONSOLIDATED FINANCIAL STATEMENTS
1. Organization
Issuer scope and consolidated activities.
2. Summary of Significant Accounting Policies
Research expense and amortization policies, with estimates distinguished.
3. Receivables
Annual receivables disclosure.
4. Commitments
Annual commitments disclosure.
Item 9. Changes in and Disagreements with Accountants on Accounting and Financial Disclosure
Annual accountant disclosure.
Item 9A. Controls and Procedures
Annual controls.
Item 15. Exhibits and Financial Statement Schedules
Annual exhibits.
Item 16. Form 10-K Summary
No summary.
SIGNATURES
Signed report.
'''
    if appendix:
        start = annual.index('CONSOLIDATED BALANCE SHEETS')
        end = annual.index('Item 9. Changes')
        financials = annual[start:end]
        annual = annual[:start] + 'See Item 15 for statements and notes.\n' + annual[end:]
        annual = annual.replace('Annual exhibits.\n', financials + 'Annual exhibits.\n')
    quarter = '''Quarter cover.
Item 1.
Financial Statements
3
Item 1. Condensed Consolidated Financial Statements (Unaudited)
Quarter statements with comparative balances and cash flows.
NOTES TO CONDENSED CONSOLIDATED FINANCIAL STATEMENTS
1. Organization
Current consolidated issuer scope.
2. Accounting Policies
Quarter notes containing accounting changes and operating working capital details.
Item 2. Management's Discussion and Analysis of Financial Condition and Results of Operations
Quarter outlook and operational drivers.
Item 3. Quantitative and Qualitative Disclosures about Market Risk
Quarter market risk.
Item 4. Controls and Procedures
Quarter controls.
Item 1. Legal Proceedings
Quarter legal proceedings.
Item 1A. Risk Factors
Quarter changed risks.
Item 6. Exhibits
Quarter exhibits.
SIGNATURES
Signed report.
'''
    docs = [_document('annual', annual, form='10-K', report_date='2024-12-31', accession='0000000999-25-000001'),
            _document('current', quarter, form='10-Q', report_date='2025-06-30', accession='0000000999-25-000002'),
            _document('comparative', quarter.replace('Quarter', 'Prior quarter'), form='10-Q',
                      report_date='2024-06-30', accession='0000000999-24-000002')]
    for doc in list(docs):
        acc = doc['metadata']['accession']
        body = json.dumps({'cik': 999, 'issuer': 'Synthetic issuer', 'facts': [{
            'taxonomy': 'us-gaap', 'concept': 'Revenue', 'label': 'Revenue', 'unit': 'USD',
            'observation': {'val': 19, 'end': doc['metadata']['report_date'],
                            'accn': acc, 'form': doc['metadata']['form']}}]})
        docs.append(_document('xbrl-0000000999-' + acc.replace('-', ''), body,
                              accession=acc.replace('-', '')))
    docs.append(_document('release', 'Full release with updated guidance.', form='EX-99.1'))
    return {'ticker': 'SYNTH', 'as_of': '2025-08-02', 'method_id': 'operating_fcff',
            'documents': docs, 'acquired_sources': {}, 'document_acquisition': {'issues': ['Coverage gap retained']}}


def _context(dossier, scope):
    from bellomberg.valuation.operating_adapter import SCHEMA
    result = deepcopy(dossier)
    result['completed_plan'] = {'model': {}, 'scenarios': {}, 'scenario_rationale': {}}
    if scope != 'model':
        # This tests projection shape, not economic validity; StagedProposer owns
        # the actual opening compiler before it reaches forecast preparation.
        result['completed_plan']['model'] = {name: {'value': 'synthetic validated opening reference'}
                                            for name, spec in SCHEMA.items() if spec[-1] == 'model'}
    return result


def _view(dossier, scope='model', context=None):
    from bellomberg.valuation.sec_preparation_sections import select_sec_fcff_context
    return select_sec_fcff_context(dossier, context or _context(dossier, scope),
                                   {'preparation_stage': {'scope': scope}})


@pytest.mark.parametrize('appendix', [False, True])
def test_opening_retains_current_notes_and_annual_policy_with_every_structured_fact(appendix):
    dossier = _case(appendix=appendix)
    before = deepcopy(dossier)
    view = _view(dossier)
    texts = {d['id']: d.get('text') for d in view['documents']}
    assert 'Research expense and amortization policies' in texts['annual']
    assert 'Issuer scope and consolidated activities' in texts['annual']
    assert 'Annual commitments disclosure.' not in texts['annual']
    assert 'Quarter statements with comparative balances' in texts['current']
    assert 'Quarter notes containing accounting changes' in texts['current']
    assert 'Quarter outlook and operational drivers.' not in texts['current']
    assert 'operating working capital details' in texts['comparative']
    assert 'outlook and operational drivers' not in texts['comparative']
    assert 'Full release with updated guidance.' in texts['release']
    assert all(d in view['documents'] for d in dossier['documents'] if d['id'].startswith('xbrl-'))
    assert view['stage_view']['automatic_selection']['semantic_coverage_certified'] is False
    assert view['stage_view']['automatic_selection']['scope'] == 'model'
    assert view['document_acquisition'] == dossier['document_acquisition'] and dossier == before


@pytest.mark.parametrize('appendix', [False, True])
def test_forecasts_retain_annual_business_risks_notes_and_current_evidence(appendix):
    dossier = _case(appendix=appendix)
    context = _context(dossier, 'bear')
    view = _view(dossier, 'bear', context)
    texts = {d['id']: d.get('text') for d in view['documents']}
    for phrase in ('products, customers', 'risk factors', 'material litigation',
                   'operating drivers', 'market risk and hedges', 'receivables disclosure', 'commitments disclosure'):
        assert phrase in texts['annual']
    assert dossier['documents'][1]['text'] in texts['current']
    assert 'comparative balances and cash flows' in texts['comparative']
    assert 'working capital details' not in texts['comparative']
    assert texts['xbrl-0000000999-000000099925000001'] is None
    assert texts['xbrl-0000000999-000000099924000002'] is None
    assert texts['xbrl-0000000999-000000099925000002']
    assert view['completed_plan'] == context['completed_plan']
    assert len(view['stage_view']['automatic_selection']['excluded_historical_xbrl']) == 2


def test_omitted_quotes_and_json_pointers_cannot_be_cited_from_projection():
    from bellomberg.valuation.preparation_ai import verify_visible_citations
    dossier = _case()
    view = _view(dossier, 'bear')
    verify_visible_citations({'x': {'evidence_ids': ['annual'], 'evidence_quote': 'Annual material litigation.'}}, view, dossier)
    verify_visible_citations({'x': {'evidence_ids': ['xbrl-0000000999-000000099925000002'],
                                  'evidence_pointer': {'value': '/facts/0/observation/val'}}}, view, dossier)
    for invalid in ({'evidence_ids': ['annual'], 'evidence_quote': 'Annual controls.'},
                    {'evidence_ids': ['xbrl-0000000999-000000099925000001'],
                     'evidence_pointer': {'value': '/facts/0/observation/val'}}):
        with pytest.raises(ValueError, match='visibile'):
            verify_visible_citations({'x': invalid}, view, dossier)


@pytest.mark.parametrize('fault', ['wrong_issuer', 'future_period', 'no_comparative', 'no_xbrl',
                                 'ambiguous_xbrl', 'wrong_accession', 'future_publication', 'missing_policy',
                                 'duplicate_heading', 'incomplete_opening', 'wrong_family', 'unknown_scope'])
def test_incomplete_or_ambiguous_selection_returns_no_projection(fault):
    dossier = _case()
    if fault == 'wrong_issuer':
        dossier['documents'][1]['metadata']['emittente_id'] = 'CIK:0000000888'
    elif fault == 'future_period':
        dossier['documents'][1]['metadata']['report_date'] = '2026-06-30'
    elif fault == 'no_comparative':
        dossier['documents'].pop(2)
    elif fault == 'no_xbrl':
        dossier['documents'].pop(3)
    elif fault == 'ambiguous_xbrl':
        dossier['documents'].append(deepcopy(dossier['documents'][3]))
    elif fault == 'wrong_accession':
        dossier['documents'][3]['metadata']['accession'] = '000000099925000003'
    elif fault == 'future_publication':
        dossier['documents'][0]['published_at'] = '2026-01-01'
    elif fault == 'missing_policy':
        dossier['documents'][0]['text'] = dossier['documents'][0]['text'].replace('Summary of Significant Accounting Policies', 'Other annual disclosure')
    elif fault == 'duplicate_heading':
        dossier['documents'][0]['text'] += '\nItem 3. Legal Proceedings\nAmbiguous duplicate.\n'
    elif fault == 'wrong_family':
        dossier['method_id'] = 'bank_residual_income'
    for doc in dossier['documents']:
        doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    context = _context(dossier, 'bear')
    if fault == 'incomplete_opening':
        del context['completed_plan']['model']['opening_nwc']
    assert _view(dossier, 'nonsense' if fault == 'unknown_scope' else 'bear', context) is None


def test_tampered_source_hash_is_not_hidden_by_selection():
    dossier = _case()
    dossier['documents'][0]['text'] += 'Unhashed change.'
    with pytest.raises(ValueError, match='SHA'):
        _view(dossier)


@pytest.mark.parametrize('prefix,accepted', [('(Unaudited)', True), ('Unknown intervening section', False)])
def test_comparative_notes_accept_only_explicit_unaudited_heading_annotation(prefix, accepted):
    dossier = _case()
    doc = dossier['documents'][2]
    doc['text'] = doc['text'].replace('STATEMENTS\n1. Organization', 'STATEMENTS\n\n' + prefix + '\n\n1. Organization')
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    assert (_view(dossier) is not None) is accepted


def test_opening_refuses_unrecognized_current_financial_section():
    dossier = _case()
    doc = dossier['documents'][1]
    doc['text'] = doc['text'].replace('Item 1. Condensed Consolidated Financial Statements', 'Unknown financial section')
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    assert _view(dossier) is None


def test_paid_sec_view_replays_without_pricing_and_explicit_manifests_stay_owned_by_caller(tmp_path):
    dossier = _case()
    doc = dossier['documents'][0]
    doc['text'] = doc['text'].replace('Annual products, customers and competition.', 'Annual business narrative. ' * 8000)
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    context = _context(dossier, 'model')
    calls = []
    proposer = _budgeted(tmp_path, calls=calls, context_length=45000)
    contract = {'schema': {}, 'preparation_stage': {'scope': 'model', 'drivers': []}}
    view = proposer.prepare_context(context, contract, source_dossier=dossier)
    assert view['stage_view']['automatic_selection']['policy'] == 'sec_fcff_stage_sections_v1'
    assert proposer(view, contract) == {'done': True}
    assert len(calls) == 1
    restarted = _budgeted(tmp_path, calls=calls)
    restarted.metadata = lambda _: pytest.fail('Existing paid SEC view must replay without pricing')
    for options in ({}, {'allow_selection': False, 'allow_cached_selection': True}):
        replay = restarted.prepare_context(context, contract, source_dossier=dossier, **options)
        assert replay == view and restarted(replay, contract) == {'done': True}
    assert restarted.prepare_context(context, contract, source_dossier=dossier, allow_selection=False) == context
    assert len(calls) == 1
