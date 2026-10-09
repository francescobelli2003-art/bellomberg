"""New-weekly Reflection36: real SQLite/checkpoints, HTTP fake and legacy controls."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_weekly_memory_memo15 import db, native
from test_tetto_specialisti_16k import bb
from bellomberg.core import reflection_policy as rp, llm_client
from bellomberg.agents import reflection as r, scorekeeper as sk, consigliere_multi as cm
from bellomberg.storage.weekly_run_store import WeeklyRunStore, WeeklyRunBlocked, digest
from bellomberg.agents.weekly_lifecycle import bind_blackboard

MISSING=object()

def agg(rows):
    n=len(rows); hits=sum(r['hit'] for r in rows)
    return {'n':n,'hits':hits,'hit_rate_pct':round(hits/n*100,1) if n else 0.,
            'avg_edge_pct':0.,'small_sample':n<10}

def score(n):
    rows=[{'id':i+1,'memo_id':700+i,'action':'BUY','hit':i%2==0,'confidence_bucket':'ALTA',
           'specialists':['quant'],'horizon_used':'4w'} for i in range(n)]
    return {'computed_at':'2032-01-01T10:00:00','details':rows,'overall':agg(rows),
            'by_action':{'BUY':agg(rows)},'by_confidence':{'ALTA':agg(rows)},
            'by_specialist':{'quant':agg(rows)},'n_unmeasurable':0,'worst_calls':[],
            'method_note':'directional local currency 4w/1w'}

def attach(db, sc=MISSING, marker=rp.POLICY):
    contract={} if marker is MISSING else {rp.KEY:marker}
    contract.update(roster=[],r2_specialists=[])
    mid=db.save_memo('[IN PROGRESS]')
    store=WeeklyRunStore(db,mid,context={'contract_version':1,'contract':contract,
        'language':'it','research_started_at':'2032-01-01T10:00:00','portfolio':{'positions':[]}})
    if sc is not MISSING:store.complete('priming',{'scorecard':sc})
    return store

def request(groups):
    return {'model':'synthetic/model','thinking':{'type':'disabled'},'max_tokens':8000,
            'system':rp.SYSTEM,'user':json.dumps({'eligible_groups':groups},sort_keys=True)}

def output(keys=('overall',)):
    return json.dumps({'rows':[{'group_ids':list(keys),'instruction':'Usare evidenza primaria [src: scorekeeper].'}]})

@pytest.fixture
def client(monkeypatch,tmp_path):
    calls=[]
    class Client:
        def __init__(self,*a,**kw):self.messages=self
        def create(self,**kw):
            calls.append(deepcopy(kw))
            return SimpleNamespace(content=[SimpleNamespace(text=output())],usage=None,stop_reason='end_turn')
    monkeypatch.setattr(llm_client,'OpenRouterClient',Client)
    monkeypatch.setattr(llm_client,'modello',lambda *a,**kw:'synthetic/model')
    monkeypatch.setattr(llm_client,'thinking_fase',lambda *a,**kw:{'type':'disabled'})
    monkeypatch.setattr(r,'LESSONS_PATH',str(tmp_path/'legacy_lessons.json'))
    monkeypatch.setattr(r,'_refusal_reason',lambda *a:None)
    return calls

@pytest.mark.parametrize('n',[0,1,35,36,37])
def test_generation_counts_and_no_archive_write(db,client,monkeypatch,n):
    store=attach(db,score(n));evidence=rp.prepare_input(store,request_factory=request)
    monkeypatch.setattr(sk,'compute_scorecard',lambda *a,**k:pytest.fail('producer called'))
    result={};usage={}
    text=r.generate_lesson('',memo_id=store.memo_id,policy_input=evidence,result_out=result,usage_out=usage)
    assert len(client)==(1 if n>=36 else 0)
    assert bool(text)==(n>=36)
    assert result['status']==('generated' if n>=36 else 'not_generated')
    assert usage['api_calls']==(1 if n>=36 else 0)
    assert not Path(r.LESSONS_PATH).exists()
    store.complete('reflection',result)
    assert rp.verified_lesson(store)==(text or None)

def test_total_does_not_lend_count_to_subgroup(db,client):
    sc=score(72)
    for row in sc['details'][2:]:row['action']='SELL'
    sc['by_action']={'BUY':agg(sc['details'][:2]),'SELL':agg(sc['details'][2:])}
    evidence=rp.prepare_input(attach(db,sc),request_factory=request)
    assert 'action:BUY' not in evidence['groups'] and evidence['groups']['action:SELL']['n']==70
    result={};r.generate_lesson('',policy_input=evidence,result_out=result)
    assert 'action:BUY' not in client[0]['messages'][0]['content']
    assert rp.parse_rows(output(('overall','action:BUY')),evidence) is None
    assert rp.parse_rows(output(('overall','action:SELL')),evidence)


@pytest.mark.parametrize('n', [0, 1, 35, 36, 37])
def test_capo_projection_preserves_descriptive_counts_and_producer(n):
    sc = score(n)
    before = deepcopy(sc)
    producer = rp.groups_for(sc)
    projection = rp.descriptive_groups_for(sc)
    groups = projection['groups']
    for key in ('overall', 'action:BUY', 'confidence:ALTA', 'specialist:quant'):
        group = groups[key]
        assert group['group_id'] == key and group['n'] == n
        assert group['hits'] == sc['overall']['hits']
        assert group['descriptive_only'] is (n < 36)
        assert group['operational_eligible'] is (n >= 36)
        assert group['method_note'] == sc['method_note']
    assert sc == before and rp.groups_for(sc) == producer


@pytest.mark.parametrize('fault', ['duplicate', 'bad_id', 'bad_hit', 'duplicate_desk',
    'missing_details', 'missing_membership', 'bool_n', 'str_n', 'mismatch_n', 'bad_hits', 'nan_stat'])
def test_capo_projection_never_attests_bad_membership_or_counts(fault):
    sc = score(36)
    if fault == 'duplicate': sc['details'][1]['id'] = sc['details'][0]['id']
    elif fault == 'bad_id': sc['details'][0]['id'] = True
    elif fault == 'bad_hit': sc['details'][0]['hit'] = 1
    elif fault == 'duplicate_desk': sc['details'][0]['specialists'] *= 2
    elif fault == 'missing_details': sc.pop('details')
    elif fault == 'missing_membership': sc['details'][0].pop('action')
    elif fault == 'bool_n': sc['by_action']['BUY']['n'] = True
    elif fault == 'str_n': sc['by_action']['BUY']['n'] = '36'
    elif fault == 'mismatch_n': sc['by_action']['BUY']['n'] = 37
    elif fault == 'bad_hits': sc['by_action']['BUY']['hits'] = 0
    elif fault == 'nan_stat': sc['by_action']['BUY']['avg_edge_pct'] = float('nan')
    projected = rp.descriptive_groups_for(sc)
    assert not projected['groups'].get('action:BUY', {}).get('operational_eligible', False)
    assert projected['issues']
    # Unavailable evidence must remain JSON transportable, with explicit nulls.
    json.dumps(projected, allow_nan=False)

@pytest.mark.parametrize('fault',['duplicate','bool_id','bool_n','string_n','nan_n','count','hits','missing_details','duplicate_desk'])
def test_bad_denominators_do_not_attest_overall(fault):
    sc=score(36)
    if fault=='duplicate':sc['details'][1]['id']=sc['details'][0]['id']
    elif fault=='bool_id':sc['details'][0]['id']=True
    elif fault in ('bool_n','string_n','nan_n'):sc['overall']['n']={'bool_n':True,'string_n':'36','nan_n':float('nan')}[fault]
    elif fault=='count':sc['overall']['n']=72
    elif fault=='hits':sc['overall']['hits']=0
    elif fault=='missing_details':sc.pop('details')
    else:sc['details'][0]['specialists']=['quant','quant']
    groups,reasons=rp.groups_for(sc)
    assert 'overall' not in groups and reasons

@pytest.mark.parametrize('bad',[{},'not json',{'rows':[{'instruction':'no group'}]},
    {'rows':[{'group_ids':['unknown'],'instruction':'x [src: scorekeeper]'}]},
    {'rows':[{'group_ids':['overall','overall'],'instruction':'x [src: scorekeeper]'}]}])
def test_invalid_output_no_operational_lesson(bad):
    evidence={'groups':rp.groups_for(score(36))[0]}
    assert rp.parse_rows(bad if isinstance(bad,str) else json.dumps(bad),evidence) is None

@pytest.mark.parametrize('marker',[None,'',False,{},[],rp.POLICY+'/2'])
def test_invalid_policy_precedes_all_work(db,bb,monkeypatch,marker):
    store=attach(db,score(36),marker);bb.run_scope='weekly';bind_blackboard(bb,store)
    monkeypatch.setattr(llm_client,'modello',lambda *a:pytest.fail('configuration called'))
    from bellomberg.agents import capo
    with pytest.raises(WeeklyRunBlocked):capo.run_capo(bb)
    with pytest.raises(WeeklyRunBlocked):rp.prepare_input(store,request_factory=lambda _:pytest.fail('request factory'))
    with pytest.raises(WeeklyRunBlocked):cm._resume_publication_contract({},store.context['contract'])

def test_newrun_and_legacy_contract(monkeypatch):
    monkeypatch.setattr(llm_client,'modello_o_buco',lambda *a:'synthetic/model')
    new=cm._weekly_contract();assert new[rp.KEY]==rp.POLICY
    assert rp.KEY not in cm._resume_publication_contract(new,{})
    assert not rp.enabled({})

@pytest.mark.parametrize('target',['priming','reflection_input_v1','reflection'])
def test_checkpoint_tamper_blocks(db,target):
    store=attach(db,score(36));e=rp.prepare_input(store,request_factory=request)
    store.complete('reflection',rp.result_for(e,rp.parse_rows(output(),e)))
    with db._conn() as conn:conn.execute('UPDATE weekly_checkpoints SET payload_json=? WHERE memo_id=? AND stage=?',('{}',store.memo_id,target))
    with pytest.raises(WeeklyRunBlocked):rp.verified_lesson(store)

def test_semantic_metadata_tamper_even_with_valid_storage_hash(db):
    store=attach(db,score(36));e=rp.prepare_input(store,request_factory=request)
    result=rp.result_for(e,rp.parse_rows(output(),e));store.complete('reflection',result)
    result['rows'][0]['group_ids']=['action:UNKNOWN']
    raw=json.dumps(result)
    import hashlib
    with db._conn() as conn:conn.execute('UPDATE weekly_checkpoints SET payload_json=?,payload_sha256=? WHERE memo_id=? AND stage=?',
        (raw,hashlib.sha256(raw.encode()).hexdigest(),store.memo_id,'reflection'))
    with pytest.raises(WeeklyRunBlocked):rp.verified_lesson(store)

def test_readtime_legacy_exclusion_no_rewrite(db,client,tmp_path):
    old,_=native(db,tmp_path,body='# OLD VALID MEMO')
    path=Path(r.LESSONS_PATH);raw=json.dumps([{'memo_id':old,'date':'2030-01-01','lesson':'OLD_OPERATIONAL_CANARY'}]).encode();path.write_bytes(raw)
    store=attach(db,score(2))
    assert 'OLD_OPERATIONAL_CANARY' in r.get_latest_lesson_block(eligible_memo_ids={old})
    block=rp.memory_projection(store)
    assert 'OLD_OPERATIONAL_CANARY' not in block and 'n.d.' in block
    assert path.read_bytes()==raw and client==[]
    assert 'OLD_OPERATIONAL_CANARY' not in db.build_capo_memory_context(reflection_block=block,track_record_snapshot=score(2))

def test_verified_memory_selection_and_freeze(db,tmp_path):
    # Use the real completed-memo/artifact reader; replace only fixture-produced empty stages.
    mid,source=native(db,tmp_path)
    with db._conn() as conn:
        context=source.context;context['contract'][rp.KEY]=rp.POLICY
        conn.execute('UPDATE weekly_runs SET context_json=?,context_sha256=? WHERE memo_id=?',(json.dumps(context),digest(context),mid))
        conn.execute("DELETE FROM weekly_checkpoints WHERE memo_id=? AND stage IN ('priming','reflection')",(mid,))
    source=WeeklyRunStore(db,mid);source.complete('priming',{'scorecard':score(36)})
    e=rp.prepare_input(source,request_factory=request);text=rp.result_for(e,rp.parse_rows(output(),e));source.complete('reflection',text)
    store=attach(db,score(2));block=rp.memory_projection(store)
    assert text['lesson'] in block
    before=store.get(rp.MEMORY_STAGE)
    assert rp.memory_projection(WeeklyRunStore(db,store.memo_id))==block
    assert store.get(rp.MEMORY_STAGE)==before

@pytest.mark.parametrize('consumer',['capo','specialist'])
def test_descriptive_consumer_keeps_stats_not_instructions(db,consumer):
    if consumer=='capo':text=db.build_capo_memory_context(reflection_block=None,track_record_snapshot=score(2),max_chars=10000)
    else:text=db.build_specialist_memory_context('quant',track_record_snapshot=score(2),max_chars=10000)
    assert '1/2 hit' in text
    assert 'pesa le voci' not in text and 'alza la soglia' not in text and 'impara da queste' not in text

@pytest.mark.parametrize('consumer',['capo','specialist'])
def test_explicit_missing_snapshot_never_refetches(db,monkeypatch,consumer):
    monkeypatch.setattr(sk,'get_track_record_for_capo',lambda:pytest.fail('refetch'))
    monkeypatch.setattr(sk,'get_track_record_for_specialist',lambda *a:pytest.fail('refetch'))
    if consumer=='capo':text=db.build_capo_memory_context(reflection_block=None,track_record_snapshot=None)
    else:text=db.build_specialist_memory_context('quant',track_record_snapshot=None)
    assert 'snapshot priming non disponibile' in text

def test_legacy_generation_and_formatter_unchanged(db,client,monkeypatch):
    sc=score(2);monkeypatch.setattr(sk,'compute_scorecard',lambda:sc)
    # Legacy output remains arbitrary text, not required to be new-policy JSON.
    lesson=r.generate_lesson('## ACTION TABLE\n|a|b|\n',memo_id=900)
    assert lesson==output() and len(client)==1 and Path(r.LESSONS_PATH).exists()
    assert 'alza la soglia' in sk.format_track_record_for_specialist(sc,'quant')
    assert 'pesa le voci' in sk.format_track_record_for_capo(sc)

def test_paid_receipt_replay_after_result_crash(db,monkeypatch,tmp_path):
    import httpx
    from bellomberg.core.request_journal import RequestJournal
    store=attach(db,score(36));evidence=rp.prepare_input(store,request_factory=request);sent=[]
    def send(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200,json={'id':'synthetic-reflection-paid','model':'synthetic/model',
            'choices':[{'message':{'role':'assistant','content':output()},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'cost':.02}})
    client=llm_client.OpenRouterClient(api_key='offline',max_retries=0,trasporto=httpx.MockTransport(send))
    monkeypatch.setattr(llm_client,'OpenRouterClient',lambda **kw:client)
    def journal():return RequestJournal(tmp_path/'paid.sqlite',run_id=store.run_id,authorized_usd=10,
        authorization={'source':'offline'},metadata=lambda model:{'id':model,'context_length':400000,
        'top_provider':{'max_completion_tokens':128000},'pricing':{'prompt':'0.000001','completion':'0.000002'}})
    class Crash(BaseException):pass
    complete=store.complete
    def fail(stage,*args,**kwargs):
        if stage=='reflection':raise Crash()
        return complete(stage,*args,**kwargs)
    monkeypatch.setattr(store,'complete',fail)
    result={}
    with llm_client.request_scope(journal(),phase='reflection',agent='_reflection'):
        text=r.generate_lesson('',policy_input=evidence,result_out=result)
        with pytest.raises(Crash):store.complete('reflection',result)
    assert len(sent)==1 and text
    with journal()._db() as conn:before=[dict(row) for row in conn.execute('SELECT * FROM requests')]
    resumed=WeeklyRunStore(db,store.memo_id)
    e=rp.prepare_input(resumed,request_factory=lambda *_:pytest.fail('config reread'))
    monkeypatch.setattr(sk,'compute_scorecard',lambda:pytest.fail('refetch'))
    with llm_client.request_scope(journal(),phase='reflection',agent='_reflection'):
        replay={};assert r.generate_lesson('',policy_input=e,result_out=replay)==text
        resumed.complete('reflection',replay)
    with journal()._db() as conn:after=[dict(row) for row in conn.execute('SELECT * FROM requests')]
    assert len(sent)==1 and before==after and result==replay
    assert rp.verified_lesson(resumed)==text


def test_native_capo_request_uses_descriptive_memory_and_saved_request(db,bb,monkeypatch):
    from test_capo_collasso import _prepara,_msg,MEMO_VERO
    from bellomberg.agents import capo
    calls=_prepara(monkeypatch,lambda *_:_msg(MEMO_VERO))
    store=attach(db,score(2));bb.run_scope='weekly';bind_blackboard(bb,store)
    bb.data['macro']={2:'Synthetic macro report'}
    first,_=capo.run_capo(bb,memory_db=db)
    original=deepcopy(bb.data['_capo_request'])
    assert '1/2 hit' in original['user_message']
    assert 'pesa le voci' not in original['user_message']
    assert 'Reflection operativa n.d.' in original['user_message']
    monkeypatch.setattr(rp,'memory_projection',lambda *_:pytest.fail('memory rebuilt after saved request'))
    second,_=capo.run_capo(bb,memory_db=db)
    assert second==first and calls[1]==calls[0] and bb.data['_capo_request']==original

@pytest.mark.parametrize('marker',[MISSING,rp.POLICY])
def test_native_specialist_memory_boundary(db,bb,monkeypatch,marker):
    from bellomberg.agents.specialists.base import Specialist
    store=attach(db,score(2),marker);bb.run_scope='weekly';bind_blackboard(bb,store);bb.memory_db=db
    monkeypatch.setattr(sk,'get_track_record_for_specialist',lambda *_:'LEGACY_CONTEXT_CANARY')
    text=Specialist._build_memory_block(SimpleNamespace(blackboard=bb,name='quant'))
    if marker is MISSING:assert 'LEGACY_CONTEXT_CANARY' in text
    else:assert '1/2 hit' in text and 'alza la soglia' not in text and 'LEGACY_CONTEXT_CANARY' not in text

@pytest.mark.parametrize('target',['capo','specialist'])
def test_integrity_outside_memory_best_effort_catch(db,bb,monkeypatch,target):
    from bellomberg.agents import capo
    from bellomberg.agents.specialists.base import Specialist
    from test_capo_collasso import _prepara,_msg,MEMO_VERO
    calls=_prepara(monkeypatch,lambda *_:_msg(MEMO_VERO))
    store=attach(db,score(2));bb.run_scope='weekly';bind_blackboard(bb,store);bb.memory_db=db
    with db._conn() as conn:conn.execute("UPDATE weekly_checkpoints SET payload_json='{}' WHERE memo_id=? AND stage='priming'",(store.memo_id,))
    with pytest.raises(WeeklyRunBlocked):
        if target=='capo':capo.run_capo(bb,memory_db=db)
        else:Specialist._build_memory_block(SimpleNamespace(blackboard=bb,name='quant'))
    assert calls==[]

def test_request_configuration_failure_is_declared(db,client):
    e=rp.prepare_input(attach(db,score(36)),request_factory=lambda *_:(_ for _ in ()).throw(ValueError('private-body')))
    assert e['request'] is None and e['request_cause']=='REQUEST_CONFIGURATION_UNAVAILABLE'
    result={};r.generate_lesson('',policy_input=e,result_out=result)
    assert client==[] and 'private-body' not in str(e)

# Full native orchestrator: only the established external boundaries are fake.
from test_cablaggio_consigliere_multi import run_offline

@pytest.fixture
def _native_research_contract(run_offline, monkeypatch):
    """Use the current research contract; retain the real Reflection36 ledger."""
    from test_weekly_recovery import _research_contract
    _research_contract(run_offline, monkeypatch)
    contract = cm._weekly_contract()
    assert contract['analysis_mode'] == 'fundamentals_research_v1'
    assert contract[rp.KEY] == rp.POLICY

@pytest.mark.usefixtures("_native_research_contract")
def test_native_weekly_new_policy_result_is_verified(run_offline,monkeypatch,client):
    import sys
    monkeypatch.setitem(sys.modules,'bellomberg.agents.reflection',r)
    monkeypatch.setattr(sk,'compute_scorecard',lambda **kw:score(36))
    cm.run_multi_agent()
    board=run_offline.catturato['bb'];store=board.weekly_store
    assert store.context['contract'][rp.KEY]==rp.POLICY
    assert store.get(rp.INPUT_STAGE)['groups']['overall']['n']==36
    assert store.get('reflection')['status']=='generated'
    assert rp.verified_lesson(store)
    assert len(client)==1


def _replace_stage(db,mid,stage,payload):
    import hashlib
    raw=json.dumps(payload)
    with db._conn() as conn:conn.execute('UPDATE weekly_checkpoints SET payload_json=?,payload_sha256=? WHERE memo_id=? AND stage=?',
        (raw,hashlib.sha256(raw.encode()).hexdigest(),mid,stage))

@pytest.mark.parametrize('field',['groups','request','run_id'])
def test_restored_input_revalidates_semantics_before_client(db,field):
    store=attach(db,score(36));e=rp.prepare_input(store,request_factory=request)
    if field=='groups':e['groups']['action:BUY']['n']=999
    elif field=='request':e['request']['user']=json.dumps({'eligible_groups':{'action:UNKNOWN':{'n':36}}})
    else:e['run_id']='different-run'
    _replace_stage(db,store.memo_id,rp.INPUT_STAGE,e)
    with pytest.raises(WeeklyRunBlocked):rp.prepare_input(store,request_factory=lambda *_:pytest.fail('factory retried'))

def test_saved_memory_projection_has_semantic_proof(db):
    store=attach(db,score(2));rp.memory_projection(store)
    saved=store.get(rp.MEMORY_STAGE);saved['block']='OLD_OPERATIONAL_CANARY'
    _replace_stage(db,store.memo_id,rp.MEMORY_STAGE,saved)
    with pytest.raises(WeeklyRunBlocked):rp.memory_projection(store)


@pytest.mark.usefixtures("_native_research_contract")
def test_native_weekly_reflection_integrity_error_not_best_effort(run_offline,monkeypatch,client):
    import sys
    monkeypatch.setitem(sys.modules,'bellomberg.agents.reflection',r)
    monkeypatch.setattr(sk,'compute_scorecard',lambda **kw:score(36))
    def broken(*args,**kwargs):raise WeeklyRunBlocked('synthetic integrity failure')
    monkeypatch.setattr(r,'generate_lesson',broken)
    with pytest.raises(WeeklyRunBlocked,match='synthetic integrity failure'):cm.run_multi_agent()
    assert client==[]
    assert run_offline.catturato['bb'].weekly_store.get('reflection') is None

@pytest.mark.parametrize('kind',['missing','none','malformed'])
def test_missing_evidence_stays_unavailable_no_factory_no_client(db,client,kind):
    store=attach(db,sc=MISSING if kind=='missing' else None if kind=='none' else {'overall':{'n':36}})
    e=rp.prepare_input(store,request_factory=lambda *_:pytest.fail('factory called'))
    result={};r.generate_lesson('',policy_input=e,result_out=result)
    assert result['cause']=='NO_ELIGIBLE_GROUPS' and client==[]

@pytest.mark.parametrize('n',[35,36])
def test_group_count_is_not_number_of_runs(n):
    sc=score(n)
    for detail in sc['details']:detail['memo_id']=900
    groups,_=rp.groups_for(sc)
    assert ('overall' in groups)==(n>=36)
    if n>=36:assert groups['overall']['n']==36


@pytest.mark.parametrize('new_policy',[False,True])
def test_exception_boundary_does_not_change_legacy_contract(db,client,monkeypatch,new_policy):
    monkeypatch.setattr(sk,'compute_scorecard',lambda:score(2))
    class FailedClient:
        def __init__(self,**kw):self.messages=self
        def create(self,**kw):raise WeeklyRunBlocked('synthetic legacy boundary')
    monkeypatch.setattr(llm_client,'OpenRouterClient',FailedClient)
    if new_policy:
        e=rp.prepare_input(attach(db,score(36)),request_factory=request)
        with pytest.raises(WeeklyRunBlocked):r.generate_lesson('',policy_input=e)
    else:
        usage={};assert r.generate_lesson('',usage_out=usage)==''
        assert usage['status']=='api_error'


@pytest.mark.usefixtures("_native_research_contract")
def test_result_without_input_cannot_reconstruct_evidence_on_resume(run_offline,monkeypatch,client):
    import sys
    from bellomberg.reporting import pdf_report
    monkeypatch.setitem(sys.modules,'bellomberg.agents.reflection',r)
    monkeypatch.setattr(sk,'compute_scorecard',lambda **kw:score(36))
    def no_pdf(**kw):raise RuntimeError('synthetic missing PDF')
    monkeypatch.setattr(sys.modules['bellomberg.reporting.pdf_institutional'],'build_institutional_memo',no_pdf)
    monkeypatch.setattr(pdf_report,'build_pdf_report',no_pdf)
    with pytest.raises(WeeklyRunBlocked):cm.run_multi_agent(send_email=False)
    store=run_offline.catturato['bb'].weekly_store
    assert store.get('reflection')['status']=='generated' and len(client)==1
    with store.db._conn() as conn:conn.execute('DELETE FROM weekly_checkpoints WHERE memo_id=? AND stage=?',(store.memo_id,rp.INPUT_STAGE))
    factory_calls=[];original=r.policy_request
    def counted(groups):factory_calls.append(True);return original(groups)
    monkeypatch.setattr(r,'policy_request',counted)
    with pytest.raises(WeeklyRunBlocked,match='priva'):
        rp.prepare_input(WeeklyRunStore(store.db,store.memo_id),request_factory=counted)
    assert factory_calls==[] and store.get(rp.INPUT_STAGE) is None and len(client)==1


@pytest.mark.parametrize('contradiction',['missing_request','request_failure_cause','result_failure_cause'])
def test_generated_rows_require_successful_request_evidence(db,contradiction):
    store=attach(db,score(36))
    factory=(lambda *_:(_ for _ in ()).throw(ValueError('synthetic configuration failure'))) if contradiction=='missing_request' else request
    evidence=rp.prepare_input(store,request_factory=factory)
    if contradiction=='request_failure_cause':
        evidence['request_cause']='REQUEST_CONFIGURATION_UNAVAILABLE'
        _replace_stage(db,store.memo_id,rp.INPUT_STAGE,evidence)
    result=rp.result_for(evidence,rp.parse_rows(output(),evidence),
                         cause='NO_ELIGIBLE_GROUPS' if contradiction=='result_failure_cause' else None)
    # Adversarial first result write: hashes valid, producer input/result contradict.
    store.complete('reflection',result)
    with pytest.raises(WeeklyRunBlocked):rp.verified_lesson(store)

@pytest.mark.parametrize('configuration_failure',[False,True])
def test_no_request_nonoperative_result_remains_valid(db,configuration_failure):
    store=attach(db,score(36 if configuration_failure else 35))
    evidence=rp.prepare_input(store,request_factory=lambda *_:(_ for _ in ()).throw(ValueError('synthetic config')))
    cause='REQUEST_CONFIGURATION_UNAVAILABLE' if configuration_failure else 'NO_ELIGIBLE_GROUPS'
    result=rp.result_for(evidence,cause=cause);store.complete('reflection',result)
    assert rp.verified_lesson(store) is None

def test_successful_request_operational_result_remains_valid(db):
    store=attach(db,score(36));evidence=rp.prepare_input(store,request_factory=request)
    result=rp.result_for(evidence,rp.parse_rows(output(),evidence));store.complete('reflection',result)
    assert rp.verified_lesson(store)==result['lesson']
