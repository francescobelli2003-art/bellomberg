"""Synthetic section retrieval: no issuer facts, provider calls or economic answers."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest


def _doc(ident, text, **metadata):
    return {'id': ident, 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'document_sha256': 'a' * 64, 'url': 'https://example.test/' + ident,
            'published_at': '2025-08-10', 'metadata': {'emittente_id': 'CIK:0000000999', **metadata}}


def _case():
    annual = '''Issuer overview omitted from this view.
I. GENERAL INFORMATION
Synthetic consolidated issuer.
II. ACCOUNTING POLICIES
A  Property, plant and equipment
Repairs are expensed; improvements require recognition criteria.
B  Intangible assets
Research is expensed. Development requires recognition criteria.
C  Inventories
Inventory is measured at the lower of cost and net realizable value.
D  Trade and other receivables
Receivables include operating and nonoperating items.
E  Current and deferred income tax
Deferred and current taxes have different bases.
F  Provisions
Recognize obligations when the criteria are met.
G  Trade and other payables
Operating payables are separately disclosed.
H  Revenue recognition
Recognize revenue on transfer of control.
I  Financial instruments
Annual financial instrument narrative outside the selected policy set.
III. FINANCIAL RISK MANAGEMENT
Annual financial risk narrative outside the selected policy set.
IV. OTHER NOTES TO THE CONSOLIDATED FINANCIAL STATEMENTS
1  Segment information
Segment disclosure retained.
2  Unrelated note
Unselected annual note.
3  Inventories, net
Inventory details retained.
4  Trade receivables, net
Receivables details retained.
5  Cash flow disclosures
Working capital cash flows retained.
6  Principal subsidiaries
Subsidiaries note not selected by the opening-note policy.
7  Business combinations
Recent business combinations retained.
8  Events after the reporting period
Events retained to document end.
'''
    current = _doc('current', 'Complete current financial statement and all current notes.',
                   form='6-K', report_date='2025-06-30', perimetro='consolidato')
    management = _doc('management', '''Interim management report.
Risk discussion retained.
Market Background and Outlook
Conditional issuer outlook retained.
Consolidated Condensed Interim Financial Statements
For the six-month period ended June 30, 2025 - all amounts in thousands of U.S. dollars.
Management report accounting appendix, separately excluded.
''', form='6-K', evidence_role='foreign_current_report', event_date='2025-08-01')
    normalized = _doc('statement-tables-current', json.dumps({'facts': [{'value': 7, 'unit': 'USD', 'end': '2025-06-30'}]}),
                      normalizer='statement_tables_v1', source_document_id='current', report_date='2025-06-30')
    return {'ticker': 'SYNTH', 'as_of': '2025-08-11', 'method_id': 'operating_fcff',
            'documents': [_doc('annual', annual, form='20-F', report_date='2024-12-31'), current, management, normalized]}


def test_automatic_sections_use_whole_heading_ranges_and_leave_current_evidence_complete():
    from bellomberg.valuation.preparation_sections import select_fcff_note_sections
    from bellomberg.valuation.preparation_view import select_stage_view
    dossier = _case()
    original = deepcopy(dossier)
    result = select_fcff_note_sections(dossier)
    assert result['selection_applied'] and not result['issues']
    assert result['semantic_coverage_certified'] is False
    view = select_stage_view(dossier, 'forecast', excerpt_manifest=result['manifest'])
    texts = {d['id']: d['text'] for d in view['documents']}
    assert 'Research is expensed.' in texts['annual']
    assert 'Receivables details retained.' in texts['annual']
    assert 'Events retained to document end.' in texts['annual']
    assert 'Unselected annual note.' not in texts['annual']
    assert 'Risk discussion retained.' in texts['management']
    assert 'accounting appendix' not in texts['management']
    assert dossier['documents'][1]['text'] in texts['current']
    assert view['documents'][-1] == dossier['documents'][-1]
    assert dossier == original
    for entry in result['manifest']:
        assert entry['selection_rationale'] and entry['coverage_limitations']
        assert entry['original_text_sha256_utf8']


@pytest.mark.parametrize('problem', ['missing_current', 'wrong_issuer', 'wrong_period', 'event_date_only',
                                    'stale_current', 'missing_outlook', 'unproven_statement'])
def test_annual_selection_needs_later_same_issuer_current_notes_and_dated_management(problem):
    from bellomberg.valuation.preparation_sections import select_fcff_note_sections
    dossier = _case()
    if problem == 'missing_current':
        dossier['documents'].pop(1)
    elif problem == 'wrong_issuer':
        dossier['documents'][1]['metadata']['emittente_id'] = 'CIK:0000000888'
    elif problem == 'wrong_period':
        dossier['documents'][1]['metadata']['report_date'] = '2025-03-31'
    elif problem == 'event_date_only':
        dossier['documents'][2]['text'] = dossier['documents'][2]['text'].replace('June 30, 2025', 'period not identified')
    elif problem == 'stale_current':
        for d in dossier['documents'][1:]:
            d['metadata']['report_date'] = '2023-06-30'
            d['text'] = d['text'].replace('June 30, 2025', 'June 30, 2023')
    elif problem == 'missing_outlook':
        dossier['documents'][2]['text'] = dossier['documents'][2]['text'].replace('Market Background and Outlook', 'No identified outlook section')
    else:
        dossier['documents'].pop()
    for d in dossier['documents']:
        d['sha256'] = sha256(d['text'].encode()).hexdigest()
    result = select_fcff_note_sections(dossier)
    annual = next(entry for entry in result['manifest'] if entry['source_id'] == 'annual')
    assert [(x['char_start'], x['char_end_exclusive']) for x in annual['excerpts']] == [(0, len(dossier['documents'][0]['text']))]
    assert result['issues']


@pytest.mark.parametrize('problem', ['duplicate_heading', 'missing_policy', 'note_gap', 'missing_boundary', 'unknown_period'])
def test_ambiguous_or_unknown_annual_hierarchy_retains_full_source_and_declares_gap(problem):
    from bellomberg.valuation.preparation_sections import select_fcff_note_sections
    dossier = _case()
    doc = dossier['documents'][0]
    if problem == 'duplicate_heading':
        doc['text'] = doc['text'].replace('III. FINANCIAL RISK MANAGEMENT', 'A  Property, plant and equipment\nDuplicate.\nIII. FINANCIAL RISK MANAGEMENT')
    elif problem == 'missing_policy':
        doc['text'] = doc['text'].replace('Intangible assets', 'Unrecognized policy')
    elif problem == 'note_gap':
        doc['text'] = doc['text'].replace('4  Trade receivables, net', '9  Trade receivables, net')
    elif problem == 'missing_boundary':
        doc['text'] = doc['text'].replace('III. FINANCIAL RISK MANAGEMENT', 'Unknown boundary')
    else:
        doc['metadata']['report_date'] = None
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    result = select_fcff_note_sections(dossier)
    entry = next(e for e in result['manifest'] if e['source_id'] == doc['id'])
    assert entry['excerpts'][0]['char_start'] == 0
    assert entry['excerpts'][0]['char_end_exclusive'] == len(doc['text'])
    assert any(i['source_id'] == 'annual' for i in result['issues'])


def test_bank_is_not_projected_by_fcff_section_policy_and_stale_text_hash_is_rejected():
    from bellomberg.valuation.preparation_sections import select_fcff_note_sections
    dossier = _case()
    dossier['method_id'] = 'bank_residual_income'
    with pytest.raises(ValueError, match='FCFF'):
        select_fcff_note_sections(dossier)
    dossier['method_id'] = 'operating_fcff'
    dossier['documents'][0]['text'] += 'Changed source.'
    with pytest.raises(ValueError, match='SHA'):
        select_fcff_note_sections(dossier)


def _large_case():
    dossier = _case()
    doc = dossier['documents'][0]
    doc['text'] = 'Unselected annual business background. ' * 4000 + '\n' + doc['text']
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    dossier['completed_plan'] = {'model': {}, 'scenarios': {}, 'scenario_rationale': {'bear': 'Earlier reasoning retained.'}}
    return dossier


def _budgeted(tmp_path, *, automatic=True, context_length=45000, calls=None):
    from types import SimpleNamespace
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    def call(**request):
        if calls is not None:
            calls.append(request)
        return SimpleNamespace(id='synthetic', model='synthetic/model', provider='synthetic', stop_reason='end_turn',
                               usage=SimpleNamespace(cost_usd=.01),
                               content=[SimpleNamespace(type='text', text='{"done":true}')])
    return BudgetedProposer(tmp_path/'sections.sqlite3', authorized_usd=5, model='synthetic/model',
                            max_tokens=1000, thinking={}, call=call, automatic_sections=automatic,
                            metadata=lambda _: {'id': 'synthetic/model', 'context_length': context_length,
                                                'pricing': {'prompt': '0.000001', 'completion': '0.000001'}})


def test_oversized_context_is_selected_automatically_and_projected_paid_response_replays(tmp_path):
    dossier, calls = _large_case(), []
    original = deepcopy(dossier)
    contract = {'schema': {}}
    proposer = _budgeted(tmp_path, calls=calls)
    view = proposer.prepare_context(dossier, contract, source_dossier=dossier)
    assert view['completed_plan'] == dossier['completed_plan']
    assert view['stage_view']['automatic_selection']['policy'] == 'ifrs_note_sections_v1'
    assert proposer(view, contract) == {'done': True}
    assert len(calls) == 1 and dossier == original
    restarted = _budgeted(tmp_path, calls=calls, context_length=1000000)
    restarted.metadata = lambda _: pytest.fail('paid projection must replay before pricing or a changed context limit')
    for options in ({}, {'allow_selection': False, 'allow_cached_selection': True}):
        again = restarted.prepare_context(dossier, contract, source_dossier=dossier, **options)
        assert again == view
        assert restarted(again, contract) == {'done': True}
    assert len(calls) == 1 and restarted.summary()['requests'] == 1


def test_existing_full_context_paid_answer_wins_over_new_automatic_policy(tmp_path):
    dossier, contract, calls = _large_case(), {'schema': {}}, []
    before = _budgeted(tmp_path, automatic=False, context_length=1000000, calls=calls)
    answer = before(dossier, contract)
    after = _budgeted(tmp_path, calls=calls)
    after.metadata = lambda _: pytest.fail('full paid response must replay before pricing')
    view = after.prepare_context(dossier, contract, source_dossier=dossier)
    assert view == dossier and after(view, contract) == answer
    assert len(calls) == 1


def test_context_growth_cannot_silently_discard_completed_economic_plan(tmp_path):
    dossier, calls = _large_case(), []
    dossier['completed_plan']['scenario_rationale']['bear'] = 'Earlier complete reasoning. ' * 4000
    original = deepcopy(dossier)
    proposer = _budgeted(tmp_path, calls=calls)
    with pytest.raises(ValueError, match='context'):
        proposer.prepare_context(dossier, {'schema': {}}, source_dossier=dossier)
    assert dossier == original and not calls
    assert proposer.summary()['requests'] == 0


def test_explicit_manifest_is_not_replaced_for_fcff_or_bank(tmp_path):
    dossier = _large_case()
    proposer = _budgeted(tmp_path)
    proposer.metadata = lambda _: pytest.fail('explicit selection must be preserved')
    assert proposer.prepare_context(dossier, {'schema': {}}, source_dossier=dossier, allow_selection=False) == dossier
    assert proposer.prepare_context(dossier, {'schema': {}}, source_dossier=dossier,
                                    allow_selection=False, allow_cached_selection=True) == dossier
    dossier['method_id'] = 'bank_residual_income'
    assert proposer.prepare_context(dossier, {'schema': {}}, source_dossier=dossier, allow_selection=False) == dossier


def test_context_already_within_allowance_keeps_original_request(tmp_path):
    dossier = _case()
    proposer = _budgeted(tmp_path)
    assert proposer.prepare_context(dossier, {'schema': {}}, source_dossier=dossier) == dossier
    assert proposer.summary()['requests'] == 0


def test_normalized_primary_report_is_never_replaced_by_its_management_prefix():
    from bellomberg.valuation.preparation_sections import select_fcff_note_sections
    dossier = _case()
    management = dossier['documents'][2]
    dossier['documents'].append(_doc('statement-tables-management', '{}', normalizer='statement_tables_v1',
                                    source_document_id=management['id'], report_date='2025-06-30'))
    result = select_fcff_note_sections(dossier)
    entry = next(m for m in result['manifest'] if m['source_id'] == management['id'])
    assert entry['excerpts'][0]['char_end_exclusive'] == len(management['text'])


def test_fcff_completed_scenario_proofs_do_not_exhaust_later_default_context(tmp_path):
    from bellomberg.valuation.preparation_seed import _digest
    dossier = _large_case()
    driver = {'value': [1, 2], 'kind': 'judgment', 'rationale': 'Prior driver explanation. ' * 4000}
    dossier['completed_plan'].update(scenarios={'bear': {'income': driver}, 'base': {},
                                              'bull': {'current': {'value': [3], 'kind': 'judgment', 'rationale': 'Current evidence stays.'}}})
    original = deepcopy(dossier)
    contract = {'schema': {}, 'preparation_stage': {'scope': 'bull', 'drivers': []}}
    calls = []; proposer = _budgeted(tmp_path, calls=calls)
    view = proposer.prepare_context(dossier, contract, source_dossier=dossier)
    assert view['completed_plan']['scenarios']['bear']['income'] == {
        'value': [1, 2], 'kind': 'judgment', 'source_driver_sha256': _digest(driver)}
    assert view['completed_plan']['scenarios']['bull'] == dossier['completed_plan']['scenarios']['bull']
    assert view['completed_plan']['scenario_rationale'] == dossier['completed_plan']['scenario_rationale']
    assert view['stage_view']['completed_plan_projection']['original_plan_sha256'] == _digest(dossier['completed_plan'])
    assert dossier == original and proposer.summary()['requests'] == 0
    proposer(view, contract)
    proposer.metadata = lambda _: pytest.fail('paid projected form must replay without pricing')
    again = proposer.prepare_context(dossier, contract, source_dossier=dossier, allow_selection=False, allow_cached_selection=True)
    assert again == view and len(calls) == 1


def test_fcff_small_context_retains_complete_scenario_proofs(tmp_path):
    dossier = _case()
    dossier['completed_plan'] = {'model': {}, 'scenarios': {'bear': {'income': {
        'value': [1], 'kind': 'judgment', 'rationale': 'Full prior proof.'}}, 'base': {}, 'bull': {}},
        'scenario_rationale': {'bear': 'Original scenario explanation.'}}
    contract = {'schema': {}, 'preparation_stage': {'scope': 'bull', 'drivers': []}}
    proposer = _budgeted(tmp_path, context_length=1000000)
    view = proposer.prepare_context(dossier, contract, source_dossier=dossier)
    assert view['completed_plan'] == dossier['completed_plan']
    assert 'completed_plan_projection' not in view.get('stage_view', {})


def test_fcff_raw_acquisition_can_be_omitted_without_losing_sources_gaps_or_plan(tmp_path):
    from bellomberg.valuation.preparation_seed import _digest
    dossier = _case()
    dossier['acquired_sources'] = {
        name: {'status': 'limited', 'source_id': name, 'as_of': dossier['as_of'],
               'data': {'raw': 'Unqualified provider detail. ' * 4000}, 'issues': ['Still incomplete.']}
        for name in ('profile', 'financials', 'filings')}
    dossier['acquired_sources']['consensus'] = {'status': 'missing', 'issues': ['No available consensus.']}
    dossier['document_acquisition'] = {'status': 'limited', 'issues': ['Unresolved source gap.'],
        'coverage': {'limited': 1}, 'acquired_document_index': {'large_index': 'Index record. ' * 4000}}
    dossier['completed_plan'] = {'model': {}, 'scenarios': {'bear': {'income': {
        'value': [1], 'kind': 'judgment', 'rationale': 'Complete prior driver proof.'}}, 'bull': {}},
        'scenario_rationale': {'bear': 'Prior scenario rationale.'}}
    original = deepcopy(dossier)
    contract = {'schema': {}, 'preparation_stage': {'scope': 'bull', 'drivers': []}}
    calls = []; proposer = _budgeted(tmp_path, calls=calls)
    view = proposer.prepare_context(dossier, contract, source_dossier=dossier)
    assert view['completed_plan'] == dossier['completed_plan']
    assert 'completed_plan_projection' not in view.get('stage_view', {})
    for name in ('profile', 'financials', 'filings'):
        assert view['acquired_sources'][name] == {k: v for k, v in dossier['acquired_sources'][name].items() if k != 'data'}
        omission = next(r for r in view['review_view_omissions'] if r['field'] == 'acquired_sources.' + name + '.data')
        assert omission['sha256'] == _digest(dossier['acquired_sources'][name]['data'])
    assert view['acquired_sources']['consensus'] == dossier['acquired_sources']['consensus']
    assert view['document_acquisition'] == {k: v for k, v in dossier['document_acquisition'].items() if k != 'acquired_document_index'}
    assert dossier['documents'][1]['text'] in view['documents'][1]['text']
    assert view['documents'][-1] == dossier['documents'][-1]
    assert dossier == original and proposer.summary()['requests'] == 0
    proposer(view, contract)
    proposer.metadata = lambda _: pytest.fail('paid context must replay without live metadata')
    assert proposer.prepare_context(dossier, contract, source_dossier=dossier,
        allow_selection=False, allow_cached_selection=True) == view
    assert len(calls) == 1
