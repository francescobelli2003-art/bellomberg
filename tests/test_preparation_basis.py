"""Verified prior research is pinned before acquisition checkpoints can resume."""
from copy import deepcopy
import pytest

from test_preparation_refresh import _case, _compact_answer


def _prior(method='operating'):
    old, current, seed, review = _case(method)
    basis = {'version': 1, **deepcopy(old), 'seed': deepcopy(seed)}
    return old, current, seed, review, {'current_generation': 'synthetic-generation',
        'artifact': {'available': True}, 'current': {'ticker': old['dossier']['ticker'],
            'snapshot_id': 'synthetic-snapshot', 'generation_id': 'synthetic-generation',
            'valuation_decision': old['dossier']['decision'],
            'preparation': {'status': 'prepared', 'review_basis': basis,
                            'proposal': {'plan': deepcopy(seed['plan'])}}}}


def test_exact_context_recompiles_without_calling_ai():
    from bellomberg.valuation.preparation_basis import capture_prior_basis, BasisProposer
    from bellomberg.valuation.preparation_ai import StagedProposer
    old, _, seed, _, current = _prior()
    captured = capture_prior_basis(current)
    proposer = BasisProposer(StagedProposer(lambda *_: pytest.fail('unchanged context cannot spend')), captured)
    result = proposer(old['dossier'], old['contract'])
    assert result == seed['plan']
    assert proposer.preparation_selection['mode'] == 'unchanged_context'
    assert proposer.preparation_selection['source_generation_id'] == 'synthetic-generation'


@pytest.mark.parametrize('fault', [None, 'changed_seed', 'published_basis'])
def test_explicit_candidate_resume_is_separate_from_previous_generation_review(fault):
    from bellomberg.valuation.preparation_basis import capture_prior_basis, BasisProposer
    from bellomberg.valuation.preparation_ai import StagedProposer
    old, _, seed, _, current = _prior()
    prior = capture_prior_basis(current if fault == 'published_basis' else None)
    if fault == 'changed_seed': seed['plan_sha256'] = '0' * 64
    def denied(*args): pytest.fail('candidate selection cannot invoke the provider')
    proposer = BasisProposer(StagedProposer(denied, seed=seed), prior)
    if fault:
        with pytest.raises(ValueError): proposer(old['dossier'], old['contract'])
    else:
        assert proposer(old['dossier'], old['contract']) == seed['plan']
        assert proposer.preparation_selection['mode'] == 'resume_candidate'
        assert proposer.preparation_selection['human_approved'] is False


@pytest.mark.parametrize('method', ['bank', 'operating'])
def test_new_cutoff_requires_explicit_reviews_with_original_sources(method):
    from bellomberg.valuation.preparation_basis import capture_prior_basis, BasisProposer
    from bellomberg.valuation.preparation_ai import StagedProposer
    old, current, seed, review, published = _prior(method)
    calls = []
    def reviewed(dossier, contract):
        stage = contract['preparation_refresh']; scope = stage['scope']; calls.append(scope)
        answer = {'reviews': review['reviews'][scope],
                  'rationale': review['scenario_rationale'].get(scope, 'Current opening review.')}
        return _compact_answer(answer, stage['compact_wire'])
    proposer = BasisProposer(StagedProposer(reviewed), capture_prior_basis(published))
    result = proposer(current['dossier'], current['contract'])
    assert calls == ['model', 'bear', 'base', 'bull']
    assert proposer.preparation_selection['mode'] == 'review'
    assert proposer.refresh_lineage['human_approved'] is False
    assert result['model']['calendar']['value'] == seed['plan']['model']['calendar']['value']


@pytest.mark.parametrize('fault', ['generation', 'ticker', 'method', 'plan', 'hash', 'artifact'])
def test_invalid_published_basis_fails_before_any_provider(fault):
    from bellomberg.valuation.preparation_basis import capture_prior_basis
    _, _, _, _, current = _prior()
    payload = current['current']; basis = payload['preparation']['review_basis']
    if fault == 'generation': payload['generation_id'] = 'different'
    elif fault == 'ticker': payload['ticker'] = 'UNRELATED'
    elif fault == 'method': payload['valuation_decision'] = {'method_id': 'unrelated'}
    elif fault == 'plan': payload['preparation']['proposal']['plan']['model']['opening_nwc']['value'] += 1
    elif fault == 'hash': basis['seed']['plan_sha256'] = '0' * 64
    elif fault == 'artifact': current['artifact']['available'] = False
    with pytest.raises(ValueError): capture_prior_basis(current)


def test_legacy_basis_absence_is_an_explicit_fresh_preparation_reason():
    from bellomberg.valuation.preparation_basis import capture_prior_basis
    _, _, _, _, current = _prior()
    del current['current']['preparation']['review_basis']
    result = capture_prior_basis(current)
    assert result['status'] == 'fresh_required' and result['reason'] == 'prior_review_basis_absent'
    assert result['source_generation_id'] == 'synthetic-generation'


def test_retained_documents_keep_dates_and_current_acquisition_gaps():
    from bellomberg.valuation.preparation_basis import capture_prior_basis, retain_prior_documents
    old, _, _, _, current = _prior()
    prior = capture_prior_basis(current)
    report = {'status': 'incomplete', 'issues': [{'reason': 'current download failed'}], 'documents': []}
    result = retain_prior_documents(report, prior)
    assert result['status'] == 'incomplete' and result['issues'] == report['issues']
    assert result['documents'] == old['dossier']['documents']
    assert result['retained_review_evidence']['document_ids'] == [d['id'] for d in old['dossier']['documents']]
    assert report['documents'] == []
    # A current receipt with the same ID wins; its changed identity is reviewed later.
    changed = deepcopy(result['documents'][0]); changed['metadata'] = {'source_update': 'explicit'}
    current_report = {'documents': [changed]}
    merged = retain_prior_documents(current_report, prior)
    assert merged['documents'][0] == changed
    assert len({d['id'] for d in merged['documents']}) == len(merged['documents'])
    other_generation = {**prior, 'source_generation_id': 'next-generation'}
    assert retain_prior_documents(report, other_generation) == result


@pytest.mark.parametrize('invalid_opening', [False, True])
def test_structural_opening_starts_fresh_scenarios_without_repaying_opening(invalid_opening):
    from bellomberg.valuation.preparation_basis import capture_prior_basis, BasisProposer
    from bellomberg.valuation.preparation_ai import StagedProposer
    old, current, seed, review, published = _prior()
    plan = deepcopy(seed['plan'])
    for drivers in [plan['model'], *plan['scenarios'].values()]:
        for item in drivers.values():
            item.update(valid_until=current['dossier']['as_of'], valid_until_basis=current['contract']['expiry_policy'])
    plan['model']['calendar']['value']['discount_convention'] = 'ACT/365F'
    if invalid_opening:
        plan['model']['calendar']['value']['periods'][0]['start'] = 'not-a-date'
    calls = []
    def provider(dossier, contract):
        if 'preparation_refresh' in contract:
            stage = contract['preparation_refresh']; calls.append(('review', stage['scope']))
            assert stage['scope'] == 'model'
            answer = {'reviews': deepcopy(review['reviews']['model']), 'rationale': 'Reassessed discount calendar.'}
            answer['reviews']['calendar'] = {'action': 'replace', 'driver': plan['model']['calendar']}
            return _compact_answer(answer, stage['compact_wire'])
        stage = contract['preparation_stage']; calls.append(('fresh', stage['scope']))
        assert stage['scope'] != 'model', 'reviewed opening must not be purchased again'
        return {'drivers': {name: deepcopy(plan['scenarios'][stage['scope']][name]) for name in stage['drivers']},
                'rationale': 'New synthetic scenario under the explicitly revised discount calendar.'}
    proposed = BasisProposer(StagedProposer(provider), capture_prior_basis(published))
    if invalid_opening:
        with pytest.raises(ValueError): proposed(current['dossier'], current['contract'])
        assert calls == [('review', 'model')]
    else:
        result = proposed(current['dossier'], current['contract'])
        assert result['model']['calendar']['value']['discount_convention'] == 'ACT/365F'
        assert proposed.preparation_selection['mode'] == 'fresh_scenarios'
        assert calls[0] == ('review', 'model') and {scope for mode, scope in calls if mode == 'fresh'} == {'bear', 'base', 'bull'}
        assert proposed.refresh_lineage is None  # Never claim the old scenario review was completed.


def test_changed_method_contract_selects_explicit_fresh_preparation():
    from bellomberg.valuation.preparation_basis import capture_prior_basis, BasisProposer
    from bellomberg.valuation.preparation_ai import StagedProposer
    _, current, _, _, published = _prior()
    current['contract']['method_contract'] += '\nSynthetic revision to the method contract.'
    class Captured(Exception): pass
    def inspect(dossier, contract):
        assert 'preparation_stage' in contract and 'preparation_refresh' not in contract
        assert contract['preparation_stage']['scope'] == 'model'
        raise Captured()
    proposer = BasisProposer(StagedProposer(inspect), capture_prior_basis(published))
    with pytest.raises(Captured): proposer(current['dossier'], current['contract'])
    assert proposer.preparation_selection['mode'] == 'fresh'
    assert proposer.preparation_selection['reason'] == 'economic_contract_changed'
