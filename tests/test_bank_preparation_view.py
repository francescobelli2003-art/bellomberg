"""Synthetic bank source projection: visible evidence and paid requests stay pinned."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.preparation_ai import verify_visible_citations
from test_preparation_sections import _budgeted


def _doc(ident, text, **metadata):
    return {'id': ident, 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'document_sha256': sha256(ident.encode()).hexdigest(),
            'url': 'https://example.invalid/annual', 'published_at': '2025-08-01',
            'available_at': '2025-08-01', 'metadata': metadata}


def _case():
    body = ('synthetic-annual\niso4217:USDxbrli:shares' + '0000000001 ' * 10000 +
            '\nUNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nWashington D.C.\nFORM 10-K\n'
            'Item 1. Business\nBank business and regulatory restrictions.\n'
            'Item 1A. Risk Factors\nNo distributions without prior permission.\n'
            'Item 8. Financial Statements and Supplementary Data\n'
            'Parent standalone cash is distinct from bank capital.\n')
    meta = {'form': '10-K', 'accession': '0000000001-25-000001',
            'emittente_id': 'CIK:0000000001', 'report_date': '2025-06-30'}
    old, new = _doc('old', body, **meta), _doc('new', body, **meta)
    fact = {'cik': '0000000001', 'facts': [{'taxonomy': 'us-gaap', 'concept': 'Assets',
        'label': 'Assets', 'unit': 'USD', 'observation': {'end': '2025-06-30', 'val': 123,
         'accn': '0000000001-25-000001', 'fy': 2025, 'form': '10-K'}}]}
    xbrl = _doc('xbrl-0000000001-000000000125000001', json.dumps(fact),
                accession='000000000125000001', emittente_id='CIK:0000000001')
    old['inline_parent_fields'] = {'technical_layout': 'repeated ' * 500}
    return {'method_id': 'bank_residual_income', 'ticker': 'SYNTH', 'as_of': '2025-08-02',
            'documents': [new, old, xbrl], 'completed_plan': {'model': {'prior': {
                'evidence_ids': ['old'], 'evidence_quote': 'No distributions without prior permission.'}}}}


def test_bank_default_selects_oversize_and_preserves_completed_evidence(tmp_path):
    source = _case(); before = deepcopy(source)
    proposer = _budgeted(tmp_path, context_length=60000)
    view = proposer.prepare_context(source, {'schema': {}}, source_dossier=source)
    assert view != source
    assert view['stage_view']['automatic_selection']['policy'] == 'bank_source_context_v1'
    assert view['completed_plan'] == source['completed_plan']
    verify_visible_citations(view['completed_plan'], view, source)
    assert proposer._prompt_bytes(proposer._request(view, {'schema': {}})) < 50808
    assert source == before and proposer.summary()['requests'] == 0


def test_bank_projection_does_not_alias_citations_or_shift_fact_indices():
    from bellomberg.valuation.bank_preparation_view import select_bank_context
    source = _case(); view = select_bank_context(source, source, {'schema': {}})
    old = next(d for d in view['documents'] if d['id'] == 'old')
    assert 'inline_parent_fields' not in old
    fact_id = source['documents'][2]['id']
    verify_visible_citations({'evidence_ids': [fact_id], 'evidence_pointer': {'amount': '/facts/0/observation/val'}}, view, source)
    with pytest.raises(ValueError, match='pointer'):
        verify_visible_citations({'evidence_ids': [fact_id], 'evidence_pointer': {'label': '/facts/0/label'}}, view, source)
    with pytest.raises(ValueError, match='citazione'):
        verify_visible_citations({'evidence_ids': ['old'], 'evidence_quote': 'iso4217:USD'}, view, source)


def test_unknown_cover_or_unlinked_xbrl_keeps_complete_annual():
    from bellomberg.valuation.bank_preparation_view import select_bank_context
    source = _case(); source['documents'] = [source['documents'][1]]
    view = select_bank_context(source, source, {'schema': {}})
    assert 'iso4217:USD' in view['documents'][0]['text']


def test_json_layout_compaction_preserves_entire_original_object_pointer():
    from bellomberg.valuation.bank_preparation_view import select_bank_context
    source = _case()
    facts = {'facts': [{'value': 12.5, 'unit': 'USD', 'note': 'Exact source wording.'}]}
    source['documents'].append(_doc('regulatory', json.dumps(facts, indent=4)))
    view = select_bank_context(source, source, {'schema': {}})
    body = next(d['text'] for d in view['documents'] if d['id'] == 'regulatory')
    assert '\n' not in body
    verify_visible_citations({'evidence_ids': ['regulatory'], 'evidence_pointer': {'full': '/facts/0'}}, view, source)


def _appendix_case():
    source = _case()
    header = source['documents'][0]['text'].split('Item 1. Business')[0]
    body = (header + 'Item 1. Business\n' + 'General business background. ' * 3000 +
        '\nSupervision and Regulation\nDistribution needs regulatory permission.\n'
        'Item 1A. Risk Factors\nAll credit and liquidity risks retained.\n'
        'Item 1B. Unresolved Staff Comments\nControls and properties.\n'
        'Item 3. Legal Proceedings\nAll litigation retained.\nItem 4. Mine Safety Disclosures\n'
        'Item 7. Management\nComplete operating discussion.\n'
        'Item 8. Financial Statements\nFinancial statements immediately following Item 16.\n'
        'Item 9. Changes\nGovernance and exhibit index.\nItem 16. Form 10-K Summary\n'
        'Consolidated Statements of Financial Condition\nAll financial statements and all notes retained.\n')
    for doc in source['documents'][:2]:
        doc['text'] = body; doc['sha256'] = sha256(body.encode()).hexdigest()
    source['completed_plan']['model']['prior']['evidence_quote'] = 'Distribution needs regulatory permission.'
    return source


def test_appendix_layout_preserves_regulation_risks_legal_and_all_financial_notes():
    from bellomberg.valuation.bank_preparation_view import select_bank_context
    source = _appendix_case(); view = select_bank_context(source, source, {'schema': {}})
    body = next(d['text'] for d in view['documents'] if d['id'] == 'old')
    assert 'All credit and liquidity risks retained.' in body
    assert 'All litigation retained.' in body and 'Complete operating discussion.' in body
    assert 'All financial statements and all notes retained.' in body
    assert 'Governance and exhibit index.' not in body
    verify_visible_citations(source['completed_plan'], view, source)


@pytest.mark.parametrize('edit', ['ambiguous_regulation', 'no_appendix_reference'])
def test_unknown_appendix_layout_does_not_omit_business_or_governance(edit):
    from bellomberg.valuation.bank_preparation_view import select_bank_context
    source = _appendix_case()
    for doc in source['documents'][:2]:
        if edit == 'ambiguous_regulation':
            doc['text'] += '\nSupervision and Regulation\nAmbiguous heading.\n'
        else:
            doc['text'] = doc['text'].replace('immediately following Item 16', 'somewhere else')
        doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    view = select_bank_context(source, source, {'schema': {}})
    old = next(d['text'] for d in view['documents'] if d['id'] == 'old')
    assert 'Governance and exhibit index.' in old


def test_prior_quote_omitted_by_selection_blocks_before_paid_request(tmp_path):
    source = _appendix_case()
    for doc in source['documents'][:2]:
        doc['text'] = doc['text'].replace('Supervision and Regulation', 'Important prior business detail.\nSupervision and Regulation')
        doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    source['completed_plan']['model']['prior']['evidence_quote'] = 'Important prior business detail.'
    proposer = _budgeted(tmp_path, context_length=60000)
    with pytest.raises(ValueError, match='prior.*evidence'):
        proposer.prepare_context(source, {'schema': {}}, source_dossier=source)
    assert proposer.summary()['requests'] == 0


def test_source_hash_tampering_is_not_hidden_by_duplicate_selection():
    from bellomberg.valuation.bank_preparation_view import select_bank_context
    source = _case(); source['documents'][0]['text'] += 'changed'
    with pytest.raises(ValueError, match='SHA-256'):
        select_bank_context(source, source, {'schema': {}})


def test_mixed_accession_facts_keep_observation_identity_and_annual_preamble():
    from bellomberg.valuation.bank_preparation_view import select_bank_context
    source = _case(); doc = source['documents'][2]; data = json.loads(doc['text'])
    data['facts'][0]['observation']['accn'] = 'another-accession'
    doc['text'] = json.dumps(data); doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    view = select_bank_context(source, source, {'schema': {}})
    assert 'iso4217:USD' in next(d['text'] for d in view['documents'] if d['id'] == 'old')
    verify_visible_citations({'evidence_ids': [doc['id']], 'evidence_pointer': {'full': '/facts/0'}}, view, source)


def test_completed_other_scenario_keeps_values_and_hash_with_declared_proof_omission():
    from bellomberg.valuation.bank_preparation_view import select_bank_context
    from bellomberg.valuation.preparation_seed import _digest
    source = _case(); driver = {'value': [1, 2], 'kind': 'judgment', 'rationale': 'Original analyst explanation.'}
    source['completed_plan'].update(scenarios={'bear': {'income': driver}, 'base': {'income': driver}, 'bull': {}},
                                    scenario_rationale={'bear': 'Full bear rationale.', 'base': 'Full base rationale.'})
    original = deepcopy(source)
    view = select_bank_context(source, source, {'preparation_stage': {'scope': 'base'}})
    assert view['completed_plan']['model'] == source['completed_plan']['model']
    assert view['completed_plan']['scenarios']['base'] == source['completed_plan']['scenarios']['base']
    assert view['completed_plan']['scenarios']['bear']['income'] == {
        'value': [1, 2], 'kind': 'judgment', 'source_driver_sha256': _digest(driver)}
    assert view['completed_plan']['scenario_rationale'] == source['completed_plan']['scenario_rationale']
    assert view['stage_view']['completed_plan_projection']['original_plan_sha256'] == _digest(source['completed_plan'])
    assert source == original


@pytest.mark.parametrize('options', [{'allow_selection': False}, {}])
def test_explicit_or_paid_bank_request_is_preserved(tmp_path, options):
    source = _case(); calls = []; contract = {'schema': {}}
    prior = _budgeted(tmp_path, automatic=False, context_length=1000000, calls=calls)
    if not options:
        prior(source, contract)
    current = _budgeted(tmp_path, calls=calls)
    current.metadata = lambda _: pytest.fail('explicit/paid context must not request metadata')
    assert current.prepare_context(source, contract, source_dossier=source, **options) == source


def test_projected_bank_answer_replays_before_new_pricing(tmp_path):
    source = _case(); calls = []; contract = {'schema': {}}
    proposer = _budgeted(tmp_path, context_length=60000, calls=calls)
    view = proposer.prepare_context(source, contract, source_dossier=source)
    proposer(view, contract)
    proposer.metadata = lambda _: pytest.fail('paid projection must replay first')
    assert proposer.prepare_context(source, contract, source_dossier=source,
        allow_selection=False, allow_cached_selection=True) == view
    assert len(calls) == 1


def test_bank_remaining_oversize_stops_before_reserving(tmp_path):
    source = _case(); source['completed_plan']['unshortened'] = 'Prior economic judgment. ' * 5000
    before = deepcopy(source); proposer = _budgeted(tmp_path, context_length=60000)
    with pytest.raises(ValueError, match='context'):
        proposer.prepare_context(source, {'schema': {}}, source_dossier=source)
    assert source == before and proposer.summary()['requests'] == 0
