"""Synthetic sources exercise the common preparer, not manually inserted records."""
from copy import deepcopy
from hashlib import sha256

import pytest

from bellomberg.valuation.input_preparation import prepare_method_inputs
from bellomberg.valuation.preparation_ai import StagedProposer, response_format
from bellomberg.valuation.dcf_engine import generate_valuation
from test_sector_nav_drivers import nav_bundle, nav_records, DATE, DAY


LABELS = {
    'gross_assets': 'Investments excluding cash', 'cash': 'Cash', 'debt': 'Debt',
    'preferred': 'Preferred claims', 'other_liabilities': 'Other liabilities',
    'accrued_fees': 'Accrued fees', 'distributions_payable': 'Distributions payable',
    'tax': 'Tax liabilities', 'equity_adjustments': 'Equity adjustments',
}


def fixture():
    plan = {'model': {}, 'scenarios': {s: {} for s in ('bear', 'base', 'bull')},
            'scenario_rationale': {s: 'Synthetic explicit discount assessment ' + s
                                   for s in ('bear', 'base', 'bull')}}
    lines = []

    def observed(value, quote, unit):
        span = f'SYNTH-GROUP ordinary as of {DATE}: {quote}.'
        lines.append(span)
        return {'evidence_ids': ['nav'], 'evidence_quote': quote, 'quoted_value': value,
                'quoted_unit': unit, 'period_quote': span}

    for record in nav_records():
        target = plan['model'] if record['scenario'] == 'model' else plan['scenarios'][record['scenario']]
        target[record['driver']] = {
            'value': deepcopy(record['value']), 'kind': 'analyst_estimate',
            'evidence_ids': ['nav'], 'rationale': 'Synthetic issuer-specific analysis',
            'valid_until': DAY, 'valid_until_basis': {'policy': 'same_day', 'as_of': DAY}}
    model = plan['model']
    for name in ('quotation', 'shares', 'components', 'publication',
                 'reported_nav_per_share', 'reported_nav_precision'):
        model[name]['kind'] = 'historical'
    model['shares'].update(observed(10., 'Shares outstanding: 10 million shares', 'million shares'))
    model['components']['facts'] = {
        name: observed(value, f'{LABELS[name]}: EUR {value:g} million', 'EUR million')
        for name, value in model['components']['value'].items()}
    nav_proof = observed(10., 'Net asset value per share: EUR 10.00 per share', 'EUR per share')
    model['reported_nav_per_share'].update(nav_proof)
    model['reported_nav_precision'].update(deepcopy(nav_proof))
    publication = (f'SYNTH-ISSUER published on {DAY}: SYNTH-GROUP ordinary '
                   f'as of {DATE}, common equity net asset value.')
    lines.append(publication)
    model['publication']['evidence_quote'] = publication
    lines.extend([f'Price: EUR 10 per share as of {DATE}.', 'Ratio: 1 shares per quote.'])
    model['quotation']['facts'] = {
        'price': {'evidence_ids': ['nav'], 'evidence_quote': 'Price: EUR 10 per share',
                  'quoted_value': 10., 'quoted_unit': 'EUR per share',
                  'date_quote': f'Price: EUR 10 per share as of {DATE}'},
        'shares_per_quote': {'evidence_ids': ['nav'], 'evidence_quote': 'Ratio: 1 shares per quote',
                             'quoted_value': 1., 'quoted_unit': 'shares per quote'}}
    body = '\n'.join(lines)
    docs = [{'id': 'nav', 'url': 'https://example.org/synthetic/nav', 'published_at': DAY,
             'text': body, 'sha256': sha256(body.encode()).hexdigest()}]
    return plan, docs


def prepare(plan=None, docs=None, *, profile='cef', proposer=None):
    defaults = fixture()
    plan = defaults[0] if plan is None else plan
    docs = defaults[1] if docs is None else docs
    return prepare_method_inputs(nav_bundle(rows=[], profile=profile), documents=docs,
                                 propose=proposer or (lambda *_: deepcopy(plan)))


@pytest.mark.parametrize('profile', ['cef', 'investment_holding'])
def test_empty_nav_bundle_prepares_and_reaches_existing_workbook(tmp_path, profile):
    assert nav_bundle(rows=[], profile=profile)['case']['records'] == []
    prepared = prepare(profile=profile)
    assert prepared['status'] == 'prepared', prepared['issues']
    rows = prepared['proposal']['method_records']
    assert len(rows) == 15 and {r['period'] for r in rows} == {DATE}
    assert prepared['proposal']['approval_status'] == 'automatic_non_approved'
    result = generate_valuation('SYNTH-NAV', prepared_bundle=prepared['bundle'], output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'], result.get('error')
    assert result['fair_value_base'] == 10.
    assert result['input_consumption']['status'] == 'complete'


@pytest.mark.parametrize('fault', ['forecast', 'historical_target', 'estimated_component',
    'missing_zero_proof', 'wrong_component', 'extra_proof', 'other_entity', 'other_class',
    'other_date', 'wrong_precision', 'invented_publisher', 'gross_nav', 'publication_date',
    'other_currency', 'missing_target'])
def test_nav_source_gaps_and_incoherent_kinds_stay_incomplete(fault):
    plan, docs = fixture()
    model = plan['model']
    if fault == 'forecast':
        model['calendar']['value'].update(periods=[{'start': '2026-01-01', 'end': '2026-12-31'}] * 10,
                                          discount_convention='annual_end')
    elif fault == 'historical_target':
        plan['scenarios']['base']['nav_target']['kind'] = 'historical'
    elif fault == 'estimated_component':
        model['components']['kind'] = 'analyst_estimate'
    elif fault == 'missing_zero_proof':
        del model['components']['facts']['preferred']
    elif fault == 'wrong_component':
        model['components']['facts']['debt'] = deepcopy(model['components']['facts']['cash'])
        model['components']['value']['debt'] = 5.
    elif fault == 'extra_proof':
        model['components']['facts']['cash']['ignored'] = 'unvalidated'
    elif fault in ('other_entity', 'other_class', 'other_date'):
        replacement = {'other_entity': ('SYNTH-GROUP', 'OTHER-GROUP'),
                       'other_class': ('ordinary', 'preferred'),
                       'other_date': (DATE, '2025-12-30')}[fault]
        source = model['components']['facts']['cash']['period_quote']
        changed = source.replace(*replacement)
        docs[0]['text'] = docs[0]['text'].replace(source, changed)
        docs[0]['sha256'] = sha256(docs[0]['text'].encode()).hexdigest()
        model['components']['facts']['cash']['period_quote'] = changed
    elif fault == 'wrong_precision':
        model['reported_nav_precision']['value'] = 1
    elif fault == 'invented_publisher':
        model['publication']['value']['publisher'] = 'OTHER-ISSUER'
    elif fault == 'gross_nav':
        source = model['publication']['evidence_quote']
        changed = source.replace('common equity net asset value', 'gross asset value')
        docs[0]['text'] = docs[0]['text'].replace(source, changed)
        docs[0]['sha256'] = sha256(docs[0]['text'].encode()).hexdigest()
        model['publication']['evidence_quote'] = changed
    elif fault == 'publication_date':
        model['publication']['value']['publication_date'] = '2026-09-08'
    elif fault == 'other_currency':
        model['components']['facts']['cash']['quoted_unit'] = 'USD million'
    elif fault == 'missing_target':
        del plan['scenarios']['base']['nav_target']
    result = prepare(plan, docs)
    assert result['status'] == 'incomplete', result
    assert result['bundle']['case']['records'] == []


def test_staged_nav_uses_snapshot_schema_and_checks_opening_before_targets():
    plan, docs = fixture()
    calls = []
    def propose(dossier, contract):
        stage = contract['preparation_stage']
        calls.append(stage['scope'])
        wire = response_format(contract)['json_schema']['schema']['properties']['drivers']['properties']
        if stage['scope'] == 'model':
            calendar = wire['calendar']['anyOf'][0]['properties']['value']['properties']
            assert calendar['periods']['maxItems'] == 0
            assert calendar['discount_convention']['const'] == 'snapshot'
            assert 'facts' in wire['components']['anyOf'][0]['properties']
        else:
            assert wire['nav_target']['anyOf'][0]['properties']['kind']['enum'] == ['analyst_estimate']
        values = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {name: values[name] for name in stage['drivers']}, 'rationale': 'Synthetic stage'}
    result = prepare(plan, docs, proposer=StagedProposer(propose))
    assert result['status'] == 'prepared', result['issues']
    assert calls == ['model', 'bear', 'base', 'bull']
    calls.clear()
    plan['model']['components']['value']['cash'] = 99.
    result = prepare(plan, docs, proposer=StagedProposer(propose))
    assert result['status'] == 'incomplete'
    assert calls == ['model']


def test_valid_proofs_still_cannot_bypass_engine_nav_reconciliation(tmp_path):
    plan, docs = fixture()
    plan['model']['components']['value']['debt'] = 30.
    proof = plan['model']['components']['facts']['debt']
    for name in ('evidence_quote', 'period_quote'):
        proof[name] = proof[name].replace('EUR 20 million', 'EUR 30 million')
    proof['quoted_value'] = 30.
    docs[0]['text'] = docs[0]['text'].replace('EUR 20 million', 'EUR 30 million')
    docs[0]['sha256'] = sha256(docs[0]['text'].encode()).hexdigest()
    result = prepare(plan, docs)
    assert result['status'] == 'prepared', result['issues']
    valuation = generate_valuation('SYNTH-NAV', prepared_bundle=result['bundle'], output_dir=str(tmp_path))
    assert not valuation['valuation_usability']['usable']
    assert valuation.get('fair_value_base') is None


def test_nav_common_service_reuses_prior_basis_and_requires_explicit_refresh(tmp_path, monkeypatch):
    from datetime import date, timedelta
    from pathlib import Path
    from bellomberg.valuation.preparation_service import prepare_and_generate
    from bellomberg.valuation.preparation_basis import capture_prior_basis
    from test_preparation_refresh import _compact_answer
    import test_sector_nav_drivers

    plan, docs = fixture()
    original = prepare_and_generate(nav_bundle(rows=[]), documents=docs,
        propose=lambda *_: deepcopy(plan), output_dir=tmp_path / 'original')
    assert original['ok'], original.get('error')
    assert original['valuation_usability']['usable'], original['valuation_usability']
    prior = capture_prior_basis({'current_generation': original['generation_id'],
        'current': original, 'artifact': {'available': True}})
    original_hash = sha256(Path(original['path']).read_bytes()).hexdigest()
    reused = prepare_and_generate(nav_bundle(rows=[]), documents=docs,
        propose=StagedProposer(lambda *_: pytest.fail('Unchanged context must not request AI')),
        prior_preparation=prior, output_dir=tmp_path / 'reused')
    assert reused['ok'], reused.get('error')
    assert reused['preparation']['provenance']['preparation_selection']['mode'] == 'unchanged_context'

    tomorrow = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    monkeypatch.setattr(test_sector_nav_drivers, 'DAY', tomorrow)
    calls = []
    def review(dossier, contract):
        spec = contract['preparation_refresh']
        calls.append(spec['scope'])
        answer = {'reviews': {name: {'action': 'reuse', 'source_driver_sha256': digest,
            'review_rationale': 'Explicit current synthetic review; dated snapshot remains unchanged.',
            'evidence_ids': ['nav']} for name, digest in spec['driver_hashes'].items()},
            'rationale': 'Current review of the synthetic NAV discount.'}
        return _compact_answer(answer, spec['compact_wire'])
    refreshed = prepare_and_generate(nav_bundle(rows=[]), documents=docs,
        propose=StagedProposer(review), prior_preparation=prior, output_dir=tmp_path / 'reviewed')
    assert refreshed['ok'], refreshed.get('error')
    assert calls == ['model', 'bear', 'base', 'bull']
    rows = refreshed['preparation']['proposal']['method_records']
    assert {r['period'] for r in rows} == {DATE}
    assert {r['valid_until'] for r in rows} == {tomorrow}
    assert refreshed['preparation']['provenance']['refresh_review']['human_approved'] is False
    assert sha256(Path(original['path']).read_bytes()).hexdigest() == original_hash

    from bellomberg.valuation.dcf_quality import normalize_valuation_payload
    blocked = deepcopy(original)
    blocked['sanity']['exclude_from_action_table'] = True
    blocked['preparation']['untrusted_outputs'] = {'fair_value_base': 456.}
    blocked = normalize_valuation_payload(blocked, as_of=DAY)
    assert not blocked['valuation_usability']['usable'] and blocked['fair_value_base'] is None
    assert blocked['preparation']['proposal']['plan'] == plan
    assert blocked['calculation_details']['scenarios']['base']['fair_value_per_share'] is None
    assert blocked['preparation']['untrusted_outputs']['fair_value_base'] is None


def test_nav_unproved_publication_and_precision_cannot_consume_unused_fields():
    for name in ('publication', 'reported_nav_precision'):
        plan, docs = fixture()
        plan['model'][name]['calculation'] = {'operation': 'invented'}
        result = prepare(plan, docs)
        assert result['status'] == 'incomplete'
        assert any(issue['field'] == name for issue in result['issues'])
