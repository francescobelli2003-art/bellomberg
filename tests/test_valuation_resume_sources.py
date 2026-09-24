"""A queued supplemental acquisition must not repay unchanged source stages."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from test_valuation_automation import automation, TICKER
from test_input_preparation import _bundle, _documents


@pytest.mark.parametrize('paid_stages',[1,2])
def test_queue_and_worker_preserve_explicit_historical_source_snapshots(automation,monkeypatch,paid_stages):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer
    manager,observed,_,_=automation
    original_collect=manager.collect
    report=original_collect(TICKER,as_of='2026-09-10',archive_root=manager.runtime.archive_root,filing_results=[])
    captured={}
    def capture(dossier,contract):captured.update(dossier=deepcopy(dossier),contract=deepcopy(contract))
    prepare_method_inputs(_bundle(),documents=_documents(),source_report=report,propose=capture)
    original_factory=manager.runtime.proposer_for
    bound=original_factory('portfolio')
    stages=[]
    def before_pause(dossier,contract):
        if len(stages)==paid_stages:raise RuntimeError('offline pause after paid stages')
        stages.append(deepcopy(contract['preparation_stage']))
        return bound.proposer(dossier,contract)
    with pytest.raises(RuntimeError,match='offline pause'):
        StagedProposer(before_pause,opening_dossier=captured['dossier'])(captured['dossier'],captured['contract'])
    assert len(observed['paid'])==paid_stages
    history=[]
    if paid_stages==2:
        history=[{'scope':stages[1]['scope'],'drivers':stages[1]['drivers'],'dossier':captured['dossier']}]
    def resumed(trigger,**kwargs):
        result=original_factory(trigger,**kwargs)
        result.opening_dossier=deepcopy(captured['dossier'])
        result.stage_dossiers=deepcopy(history)
        return result
    monkeypatch.setattr(manager.runtime,'proposer_for',resumed)
    extra=deepcopy(report['documents'][0])
    extra.update(id='supplemental-source',url='https://example.org/issuer/supplement',
                 text='Synthetic supplementary disclosure for the existing issuer.')
    extra['sha256']=sha256(extra['text'].encode()).hexdigest()
    extended=deepcopy(report);extended['documents'].append(extra)
    manager.collect=lambda *args,**kwargs:deepcopy(extended)
    manager.enqueue_initial(TICKER,'portfolio')
    result=manager.run_one(owner='resumed-worker')
    assert result['status']=='succeeded',result
    requests=[json.loads(call['messages'][0]['content']) for call in observed['paid']]
    for paid in stages:
        matched=[row for row in requests if row['contract']['preparation_stage']==paid]
        assert len(matched)==1,'an unchanged historical source stage was charged twice'
    for row in requests[paid_stages:]:
        assert 'supplemental-source' in {doc['id'] for doc in row['dossier']['documents']}
    current=manager.versions.current(TICKER)
    assert current['artifact']['available'] and current['current_generation']==result['result']['generation_id']


@pytest.mark.parametrize('paid_stages', [1, 2])
def test_queue_and_worker_resume_candidate_without_invoking_completed_stages(automation, monkeypatch, paid_stages):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.preparation_seed import make_seed
    manager, observed, _, _ = automation
    report = manager.collect(TICKER, as_of='2026-09-10', archive_root=manager.runtime.archive_root, filing_results=[])
    captured = {}
    def capture(dossier, contract):
        captured.update(dossier=deepcopy(dossier), contract=deepcopy(contract))
    prepare_method_inputs(_bundle(), documents=_documents(), source_report=report, propose=capture)
    original_factory = manager.runtime.proposer_for
    bound = original_factory('portfolio')
    stages = []
    def before_pause(dossier, contract):
        if len(stages) == paid_stages:
            captured['plan'] = deepcopy(dossier['completed_plan'])
            raise RuntimeError('offline pause after acquired stages')
        stages.append(deepcopy(contract['preparation_stage']))
        return bound.proposer(dossier, contract)
    with pytest.raises(RuntimeError, match='offline pause'):
        StagedProposer(before_pause)(captured['dossier'], captured['contract'])
    assert len(observed['paid']) == paid_stages
    seed = make_seed(captured['dossier'], captured['contract'], captured['plan'])
    def resumed(trigger, **kwargs):
        result = original_factory(trigger, **kwargs)
        original = result.proposer
        def remaining(dossier, contract):
            assert contract['preparation_stage'] not in stages, 'completed stage invoked after seed resume'
            return original(dossier, contract)
        result.proposer, result.seed = remaining, deepcopy(seed)
        return result
    monkeypatch.setattr(manager.runtime, 'proposer_for', resumed)
    manager.enqueue_initial(TICKER, 'portfolio')
    result = manager.run_one(owner='candidate-resume-worker')
    assert result['status'] == 'succeeded', result
    current = manager.versions.current(TICKER)
    assert current['artifact']['available'] and current['current_generation'] == result['result']['generation_id']
    paid = [json.loads(call['messages'][0]['content'])['contract']['preparation_stage'] for call in observed['paid']]
    for old in stages:
        assert paid.count(old) == 1
