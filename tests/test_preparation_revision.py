"""Explicit same-case driver reuse must preserve evidence and require validation."""
from copy import deepcopy
from hashlib import sha256

import pytest

from test_preparation_seed import _paused, _bundle, _documents


def _revision():
    from bellomberg.valuation.preparation_seed import make_seed
    from bellomberg.valuation.preparation_ai import _json
    captured, full, _ = _paused()
    names = [name for name in full['scenarios']['bull'] if name.startswith('capital.parent_cash_flows.')]
    original = deepcopy(full)
    for name in names:
        del original['scenarios']['bull'][name]
    seed = make_seed(captured['dossier'], captured['contract'], original)
    revision = {'source_plan_sha256': seed['plan_sha256'], 'replacements': {},
        'reuse': [{'scope':'bull', 'source_scope':'base', 'driver':name,
                   'source_driver_sha256':sha256(_json(original['scenarios']['base'][name]).encode()).hexdigest(),
                   'rationale':'Explicitly shared parent policy; original source and dates retained.'} for name in names],
        'scenario_rationale':{'bull':'Bull earnings with the explicitly shared, documented parent cash policy.'}}
    return captured, full, seed, revision


def test_explicit_driver_reuse_completes_candidate_without_spending_or_approval():
    from bellomberg.valuation.preparation_seed import apply_revision
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    captured, full, seed, revision = _revision()
    before = deepcopy(seed)
    new_seed, receipt = apply_revision(seed, captured['dossier'], captured['contract'], revision,
                                      view=captured['dossier'])
    result = prepare_method_inputs(_bundle(), documents=_documents(),
        propose=StagedProposer(lambda *args:pytest.fail('complete proposal must not spend'), seed=new_seed))
    assert result['status']=='prepared', result['issues']
    assert result['proposal']['approval_status']=='automatic_non_approved'
    for reference in revision['reuse']:
        name = reference['driver']
        new = new_seed['plan']['scenarios']['bull'][name]
        original = full['scenarios']['base'][name]
        assert {k:v for k,v in new.items() if k!='rationale'} == {k:v for k,v in original.items() if k!='rationale'}
    assert seed == before
    assert len(receipt['reused_drivers'])==len(revision['reuse'])
    assert receipt['validation_required'] and receipt['human_approved'] is False


@pytest.mark.parametrize('problem', ['wrong_plan','wrong_driver_hash','unknown_scope','unknown_driver',
    'duplicate_target','missing_rationale','historical_replacement','unseen_citation','reference_chain','human_approval'])
def test_revision_rejects_ambiguous_or_unauthorized_reuse(problem):
    from bellomberg.valuation.preparation_seed import apply_revision
    captured, full, seed, revision = _revision()
    ref=revision['reuse'][0]
    if problem=='wrong_plan':revision['source_plan_sha256']='wrong'
    elif problem=='wrong_driver_hash':ref['source_driver_sha256']='wrong'
    elif problem=='unknown_scope':ref['scope']='other'
    elif problem=='unknown_driver':ref['driver']='unknown'
    elif problem=='duplicate_target':revision['replacements']={'bull':{ref['driver']:full['scenarios']['base'][ref['driver']]}}
    elif problem=='missing_rationale':revision['scenario_rationale']={}
    elif problem=='historical_replacement':
        item=deepcopy(full['scenarios']['bull']['capital.shares_m']);item['value']+=1
        revision['replacements']={'bull':{'capital.shares_m':item}}
    elif problem=='unseen_citation':
        item=deepcopy(full['scenarios']['bull']['taxes']);item['evidence_ids']=['unseen']
        revision['replacements']={'bull':{'taxes':item}}
    elif problem=='reference_chain':
        revision['reuse'].append(dict(ref,scope='bear',source_scope='bull'))
        revision['scenario_rationale']['bear']='Cannot depend on a driver created by this revision.'
    elif problem=='human_approval':revision['human_approved']=True
    with pytest.raises(ValueError):
        apply_revision(seed,captured['dossier'],captured['contract'],revision,view=captured['dossier'])


def test_reused_values_must_still_pass_economic_arithmetic_before_spending():
    from bellomberg.valuation.preparation_seed import apply_revision
    from bellomberg.valuation.preparation_ai import StagedProposer, _json
    captured, full, seed, revision = _revision()
    name='capital.subsidiaries.0.gaap_net_income'
    assert full['scenarios']['base'][name]['value'] != full['scenarios']['bull'][name]['value']
    revision['reuse'].append({'scope':'bull','source_scope':'base','driver':name,
        'source_driver_sha256':sha256(_json(full['scenarios']['base'][name]).encode()).hexdigest(),
        'rationale':'Deliberately unreconciled copied bank income for this negative test.'})
    new_seed,_=apply_revision(seed,captured['dossier'],captured['contract'],revision,view=captured['dossier'])
    with pytest.raises(ValueError,match='bank forecast arithmetic'):
        StagedProposer(lambda *args:pytest.fail('unreconciled copy must not spend'),seed=new_seed)(captured['dossier'],captured['contract'])


def test_revision_cannot_cite_a_known_document_omitted_from_its_visible_view():
    from bellomberg.valuation.preparation_seed import apply_revision
    captured, full, seed, revision = _revision()
    view = deepcopy(captured['dossier'])
    cited = full['scenarios']['base'][revision['reuse'][0]['driver']]['evidence_ids']
    view['documents'] = [doc for doc in view['documents'] if doc['id'] not in cited]
    with pytest.raises(ValueError, match='non visibile'):
        apply_revision(seed, captured['dossier'], captured['contract'], revision, view=view)


def test_references_read_original_seed_even_when_source_is_replaced_in_same_revision():
    from bellomberg.valuation.preparation_seed import apply_revision
    captured, full, seed, revision = _revision()
    name = revision['reuse'][0]['driver']
    changed = deepcopy(full['scenarios']['base'][name])
    changed['value'][0] += 1
    revision['replacements'] = {'base': {name: changed}}
    revision['scenario_rationale']['base'] = 'A separate changed policy, requiring ledger reconciliation.'
    new_seed, receipt = apply_revision(seed, captured['dossier'], captured['contract'], revision,
                                      view=captured['dossier'])
    assert new_seed['plan']['scenarios']['base'][name] == changed
    assert new_seed['plan']['scenarios']['bull'][name]['value'] == full['scenarios']['base'][name]['value']
    assert receipt['source_plan_sha256'] == seed['plan_sha256']
    assert receipt['plan_sha256'] == new_seed['plan_sha256']


@pytest.mark.parametrize('problem', ['self_reference', 'blank_rationale', 'extra_rationale',
                                   'historical_target', 'partial_envelope'])
def test_revision_cannot_hide_ambiguous_references_or_historical_edits(problem):
    from bellomberg.valuation.preparation_seed import apply_revision
    from bellomberg.valuation.preparation_ai import _json
    captured, full, seed, revision = _revision()
    reference = revision['reuse'][0]
    if problem == 'self_reference':
        reference['scope'] = reference['source_scope']
    elif problem == 'blank_rationale':
        reference['rationale'] = ' '
    elif problem == 'extra_rationale':
        revision['scenario_rationale']['bear'] = 'No corresponding change.'
    elif problem == 'historical_target':
        reference['driver'] = 'capital.shares_m'
        reference['source_driver_sha256'] = sha256(_json(full['scenarios']['base']['capital.shares_m']).encode()).hexdigest()
    elif problem == 'partial_envelope':
        revision['replacements'] = {'bull': {'taxes': {'value': [0]}}}
    with pytest.raises(ValueError):
        apply_revision(seed, captured['dossier'], captured['contract'], revision, view=captured['dossier'])
