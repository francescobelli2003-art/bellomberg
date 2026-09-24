"""Forecast compression preserves original pointer values and paid identities."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from test_sec_preparation_sections import _case, _context
from test_preparation_sections import _budgeted
from bellomberg.valuation.preparation_ai import verify_visible_citations


def _inputs():
    dossier = _case()
    source = dossier['documents'][4]
    parsed = json.loads(source['text'])
    first = parsed['facts'][0]
    first['observation'].update(start='2025-01-01', fy=2025, fp='Q2', filed='2025-08-01')
    prior = deepcopy(first)
    prior['observation'].update(start='2024-01-01', end='2024-06-30', val=11)
    parsed['facts'] = [prior, first, deepcopy(first)]
    parsed['facts'][2]['concept'] = 'InventoryNet'
    source['text'] = json.dumps(parsed)
    source['sha256'] = sha256(source['text'].encode()).hexdigest()
    context = _context(dossier, 'bear')
    context['completed_plan']['model']['calendar']['value'] = {'valuation_date': '2025-06-30', 'periods': []}
    contract = {'schema': {}, 'preparation_stage': {'scope': 'bear', 'drivers': []}}
    return dossier, context, contract, source['id']


def _project(dossier, context, contract):
    from bellomberg.valuation.sec_current_fact_view import select_sec_current_facts
    return select_sec_current_facts(dossier, context, contract)


def test_pointer_from_modified_visible_json_is_rejected_against_original():
    dossier, context, _, ident = _inputs()
    source = next(d for d in context['documents'] if d['id'] == ident)
    parsed = json.loads(source['text'])
    parsed['facts'][1]['observation']['val'] += 1
    source['text'] = json.dumps(parsed)
    with pytest.raises(ValueError, match='pointer.*non visibile'):
        verify_visible_citations({'evidence_ids': [ident], 'evidence_pointer': {
            'value': '/facts/1/observation/val'}}, context, dossier)


def test_retained_facts_keep_indices_numbers_periods_units_and_original_documents():
    dossier, context, contract, ident = _inputs()
    before = deepcopy((dossier, context, contract))
    view = _project(dossier, context, contract)
    assert (dossier, context, contract) == before
    projected = json.loads(next(d['text'] for d in view['documents'] if d['id'] == ident))
    assert projected['facts'][0] is None and len(projected['facts']) == 3
    assert projected['facts'][1]['observation'] == {'start': '2025-01-01', 'end': '2025-06-30', 'val': 19}
    for path in ('/facts/1/observation/val', '/facts/1/observation/start', '/facts/1/observation/end',
                 '/facts/1/unit', '/facts/1/concept', '/facts/2/observation/val'):
        verify_visible_citations({'evidence_ids': [ident], 'evidence_pointer': {'value': path}}, view, dossier)
    selection = view['stage_view']['automatic_selection']
    assert selection['policy'] == 'sec_fcff_current_facts_v1'
    assert selection['semantic_coverage_certified'] is False
    assert selection['structured_projection']['retained_facts'] == 2
    assert selection['structured_projection']['omitted_facts'] == 1
    assert view['completed_plan'] == context['completed_plan']
    current_body = next(d['text'] for d in view['documents'] if d['id'] == 'current')
    assert dossier['documents'][1]['text'] in current_body


@pytest.mark.parametrize('path', ['/facts/0', '/facts/0/observation/val', '/facts/1',
    '/facts/1/observation', '/facts/1/label', '/facts/1/observation/accn', '/facts'])
def test_omitted_facts_metadata_and_changed_parent_objects_cannot_be_cited(path):
    dossier, context, contract, ident = _inputs()
    view = _project(dossier, context, contract)
    with pytest.raises(ValueError, match='pointer.*non visibile'):
        verify_visible_citations({'evidence_ids': [ident], 'evidence_pointer': {'value': path}}, view, dossier)


@pytest.mark.parametrize('fault', ['opening', 'date_mismatch', 'bad_period', 'no_current_facts'])
def test_projection_refuses_unmatched_period_or_opening_stage(fault):
    dossier, context, contract, ident = _inputs()
    if fault == 'opening':
        contract['preparation_stage']['scope'] = 'model'
    elif fault == 'date_mismatch':
        context['completed_plan']['model']['calendar']['value']['valuation_date'] = '2024-12-31'
    else:
        source = next(d for d in dossier['documents'] if d['id'] == ident)
        parsed = json.loads(source['text'])
        for fact in parsed['facts']:
            fact['observation']['end'] = 'invalid-date' if fault == 'bad_period' else '2024-06-30'
        source['text'] = json.dumps(parsed)
        source['sha256'] = sha256(source['text'].encode()).hexdigest()
    assert _project(dossier, context, contract) is None


def test_oversized_forecast_selects_projection_and_replays_without_new_charge(tmp_path):
    dossier, context, contract, ident = _inputs()
    source = next(d for d in dossier['documents'] if d['id'] == ident)
    parsed = json.loads(source['text'])
    for fact in parsed['facts']:
        fact['label'] = 'Long descriptive accounting label ' * 2500
    source['text'] = json.dumps(parsed)
    source['sha256'] = sha256(source['text'].encode()).hexdigest()
    context['documents'] = deepcopy(dossier['documents'])
    calls = []
    proposer = _budgeted(tmp_path, calls=calls, context_length=65000)
    view = proposer.prepare_context(context, contract, source_dossier=dossier)
    assert view['stage_view']['automatic_selection']['policy'] == 'sec_fcff_current_facts_v1'
    assert proposer(view, contract) == {'done': True}
    restarted = _budgeted(tmp_path, calls=calls)
    restarted.metadata = lambda _: pytest.fail('Paid request must not need current pricing')
    replay = restarted.prepare_context(context, contract, source_dossier=dossier,
                                      allow_selection=False, allow_cached_selection=True)
    assert replay == view and restarted(replay, contract) == {'done': True}
    assert len(calls) == 1
