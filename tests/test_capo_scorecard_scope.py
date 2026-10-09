"""R07: native Capo scorecard scope, isolated SQLite and HTTP receipts."""
from copy import deepcopy
import json

import httpx
import pytest

from bellomberg.agents import capo, scorekeeper
from bellomberg.agents.weekly_lifecycle import bind_blackboard
from bellomberg.core import evidence_followup_policy as followup, llm_client
from bellomberg.core.request_journal import RequestJournal
from bellomberg.storage.weekly_run_store import WeeklyRunStore
from test_capo_collasso import _prepara, _msg, MEMO_VERO
from test_llm_retry_stream import sse, chunk
from test_reflection36_policy import agg, score
from test_tetto_specialisti_16k import bb
from test_weekly_memory_memo15 import db


def mixed_score():
    value = score(108)
    for i, row in enumerate(value['details']):
        row['action'] = 'BUY' if i < 35 else 'ADD' if i < 71 else 'SELL'
    value['by_action'] = {action: agg([r for r in value['details'] if r['action'] == action])
                          for action in ('BUY', 'ADD', 'SELL')}
    return value


def attach(db, board, *, marker=followup.POLICY, sc=None):
    contract = {'roster': [], 'r2_specialists': [], 'reflection_policy': 'weekly-reflection36/1'}
    if marker is not None:
        contract.update({followup.KEY: marker, followup.AS_OF_KEY: '2032-01-01'})
    store = WeeklyRunStore(db, db.save_memo('[IN PROGRESS]'), context={
        'contract_version': 1, 'contract': contract, 'language': 'it',
        'research_started_at': '2032-01-01T10:00:00', 'portfolio': {'positions': []}})
    store.complete('priming', {'scorecard': mixed_score() if sc is None else sc})
    board.run_scope = 'weekly'
    bind_blackboard(board, store)
    return store


def binding(groups=('action:BUY',), use='sizing', instruction='Aumento il sizing dei BUY.'):
    return {'group_ids': list(groups), 'use': use, 'instruction': instruction}


def response_for(rows):
    prose = '\n'.join(row['instruction'] for row in rows)
    return MEMO_VERO + '\n' + prose + '\n<scorecard_bindings>' + json.dumps({'rows': rows}) + '</scorecard_bindings>'


def native_client(monkeypatch, response, requests):
    _prepara(monkeypatch, lambda *_: _msg(response))
    def send(request):
        body = json.loads(request.content)
        requests.append(body)
        return sse({'id': 'synthetic-capo-scope', 'model': body['model'],
                    **chunk({'content': response})},
                   {**chunk(finish='stop'),
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'cost': .02}})
    client = llm_client.OpenRouterClient(api_key='offline', max_retries=0,
                                        trasporto=httpx.MockTransport(send))
    monkeypatch.setattr(capo, 'OpenRouterClient', lambda **kw: client)


def journal(tmp_path, store):
    return RequestJournal(tmp_path / 'capo-scope.sqlite', run_id=store.run_id,
        authorization={'source': 'synthetic offline'}, authorized_usd=10,
        metadata=lambda model: {'id': model, 'context_length': 1_000_000,
            'top_provider': {'max_completion_tokens': 128000},
            'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})


def test_native_rejects_small_group_binding_without_global_memo_gate(db, bb, monkeypatch, tmp_path):
    store = attach(db, bb)
    raw = response_for([binding()])
    sent = []
    native_client(monkeypatch, raw, sent)
    with llm_client.request_scope(journal(tmp_path, store), phase='capo', agent='capo', round_n=3):
        text, usage = capo.run_capo(bb, memory_db=db)
    assert usage.get('scorecard_scope', {}).get('status') == 'REJECTED_BINDINGS'
    assert usage['scorecard_scope']['accepted_bindings'] == []
    assert usage['scorecard_scope']['rejected_bindings'][0]['code'] == 'GROUP_NOT_OPERATIONAL'
    assert usage['complete'] is True
    assert 'SCORECARD: binding operativo rifiutato' in text
    assert 'meno di 36 esiti' in text
    assert raw in text
    assert len(sent) == 1
    context = sent[0]['messages'][-1]['content']
    assert '"group_id": "action:BUY"' in context
    assert '"n": 35' in context and '"n": 36' in context and '"n": 37' in context
    assert 'sizing, selezione, ranking o regole' in sent[0]['messages'][0]['content']
    frozen = deepcopy(bb.data['_capo_request'])
    with journal(tmp_path, store)._db() as conn:
        before = [dict(r) for r in conn.execute('SELECT * FROM requests')]
    assert len(before) == 1
    assert json.loads(before[0]['response'])['choices'][0]['message']['content'] == raw
    monkeypatch.setattr(scorekeeper, 'compute_scorecard', lambda *a, **k: pytest.fail('refetch'))
    # Replay from the native durable snapshot, without rebuilding paid context.
    bind_blackboard(bb, WeeklyRunStore(db, store.memo_id))
    with llm_client.request_scope(journal(tmp_path, store), phase='capo', agent='capo', round_n=3):
        replay, replay_usage = capo.run_capo(bb, memory_db=db)
    assert replay == text and replay_usage['scorecard_scope'] == usage['scorecard_scope']
    assert bb.data['_capo_request'] == frozen and len(sent) == 1
    with journal(tmp_path, store)._db() as conn:
        assert [dict(r) for r in conn.execute('SELECT * FROM requests')] == before


def test_native_explicit_prose_is_flagged_but_paraphrase_is_not_certified(db, bb, monkeypatch):
    attach(db, bb)
    raw = MEMO_VERO + '\nIl track record di action:BUY giustifica un aumento del sizing.\n'
    raw += 'Privilegio gradualmente gli acquisti nelle prossime settimane.'
    _prepara(monkeypatch, lambda *_: _msg(raw))
    text, usage = capo.run_capo(bb)
    assert usage.get('scorecard_scope', {}).get('status') == 'REJECTED_BINDINGS'
    assert usage['scorecard_scope']['binding_status'] == 'ABSENT'
    assert usage['scorecard_scope']['prose_scope'] == 'NOT_ASSESSED'
    assert usage['scorecard_scope']['explicit_prose'][0]['code'] == 'GROUP_NOT_OPERATIONAL'
    assert 'NOT_ASSESSED' in text and raw in text


def test_legacy_reflection36_request_and_output_do_not_get_new_policy(db, bb, monkeypatch):
    attach(db, bb, marker=None)
    raw = response_for([binding()])
    calls = _prepara(monkeypatch, lambda *_: _msg(raw))
    text, usage = capo.run_capo(bb)
    assert 'scorecard_scope' not in usage
    assert 'scorecard_scope' not in bb.data['_capo_request']
    assert '<scorecard_scope>' not in calls[0]['messages'][0]['content']
    assert 'SCORECARD: binding operativo rifiutato' not in text
    assert raw in text


def projected():
    from bellomberg.core.scorecard_scope_policy import project
    return project(mixed_score())


@pytest.mark.parametrize('group,n,eligible', [('action:BUY', 35, False),
    ('action:ADD', 36, True), ('action:SELL', 37, True), ('overall', 108, True)])
def test_group_scope_does_not_borrow_total(group, n, eligible):
    scope = projected()
    assert scope['groups'][group]['n'] == n
    assert scope['groups'][group]['operational_eligible'] is eligible
    assert scope['groups'][group]['descriptive_only'] is (not eligible)


@pytest.mark.parametrize('groups,use,code', [
    (['action:BUY'], 'sizing', 'GROUP_NOT_OPERATIONAL'),
    (['overall', 'action:BUY'], 'ranking', 'GROUP_NOT_OPERATIONAL'),
    (['action:ADD', 'action:BUY'], 'selection', 'GROUP_NOT_OPERATIONAL'),
    (['action:UNKNOWN'], 'description', 'UNKNOWN_GROUP'),
    (['overall', 'overall'], 'action_rule', 'DUPLICATE_GROUP'),
    ([], 'operational_lesson', 'INVALID_BINDING'),
    (['overall'], 'unbounded', 'INVALID_BINDING'),
])
def test_structured_bindings_reject_unknown_duplicate_and_small_groups(groups, use, code):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(response_for([binding(groups, use)]), projected())
    assert result['status'] == 'REJECTED_BINDINGS'
    assert result['accepted_bindings'] == []
    assert result['rejected_bindings'][0]['code'] == code
    assert result['prose_scope'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('groups,use', [(['action:BUY'], 'description'),
    (['action:ADD'], 'sizing'), (['action:ADD', 'action:SELL'], 'ranking')])
def test_valid_binding_does_not_certify_free_prose_or_financial_truth(groups, use):
    from bellomberg.core.scorecard_scope_policy import assess
    row = binding(groups, use)
    raw = response_for([row])
    result = assess(raw, projected())
    assert result['binding_status'] == 'VALID'
    assert result['status'] == 'BINDINGS_CHECKED'
    assert result['accepted_bindings'] == [row]
    assert result['prose_scope'] == 'NOT_ASSESSED'
    assert result['semantic_scope'] == 'NOT_ASSESSED'
    assert result['raw_sha256']


@pytest.mark.parametrize('block', ['{}', '{"rows": false}', '{"rows": [], "rows": []}',
    '{"rows": [{"group_ids":["overall"],"use":"description","instruction":"absent"}]}',
    '{"rows": [], "extra": true}', 'NaN', 'not json'])
def test_malformed_binding_block_never_counts_as_valid(block):
    from bellomberg.core.scorecard_scope_policy import assess
    raw = MEMO_VERO + '<scorecard_bindings>' + block + '</scorecard_bindings>'
    result = assess(raw, projected())
    assert result['binding_status'] == 'INVALID'
    assert result['status'] == 'REJECTED_BINDINGS'
    assert result['accepted_bindings'] == []


@pytest.mark.parametrize('raw', [MEMO_VERO + '<scorecard_bindings>',
    MEMO_VERO + '</scorecard_bindings>', response_for([]) + response_for([])],
    ids=['open_only', 'close_only', 'duplicate'])
def test_broken_or_duplicate_envelope_is_declared(raw):
    from bellomberg.core.scorecard_scope_policy import assess
    assert assess(raw, projected())['binding_status'] == 'INVALID'


def test_absent_bindings_and_uncovered_prose_are_not_silent_passes():
    from bellomberg.core.scorecard_scope_policy import assess, notice
    raw = MEMO_VERO + '\nPrivilegio gradualmente gli acquisti nelle prossime settimane.'
    result = assess(raw, projected())
    assert result['status'] == 'NOT_ASSESSED' and result['binding_status'] == 'ABSENT'
    assert result['explicit_prose'] == []
    assert 'NOT_ASSESSED' in notice(result)


def test_descriptive_stats_remain_readable_without_implicit_operational_instruction():
    from bellomberg.core.scorecard_scope_policy import format_track_record_scope_for_capo
    text = format_track_record_scope_for_capo(projected())
    assert '"n": 35' in text and '"hits": 18' in text
    assert '"operational_eligible": false' in text
    assert 'directional local currency 4w/1w' in text
    assert 'pesa le voci' not in text and 'alza la soglia' not in text


@pytest.mark.parametrize('use', ['sizing', 'selection', 'ranking', 'action_rule', 'operational_lesson'])
def test_every_operational_use_of_small_group_is_rejected(use):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(response_for([binding(use=use)]), projected())
    assert result['rejected_bindings'] == [{'row': 0, 'code': 'GROUP_NOT_OPERATIONAL'}]


@pytest.mark.parametrize('group,code', [('action:BUY', 'GROUP_NOT_OPERATIONAL'),
    ('action:UNKNOWN', 'UNKNOWN_GROUP'), ('action:ADD', None)])
@pytest.mark.parametrize('suffix', ['un aumento del sizing.', 'una modifica della selezione.',
    'una modifica del ranking.', "una modifica delle regole d'azione.", 'una lezione operativa.'])
def test_closed_prose_forms_are_scoped_without_global_certification(group, code, suffix):
    from bellomberg.core.scorecard_scope_policy import assess
    raw = MEMO_VERO + '\nIl track record di ' + group + ' giustifica ' + suffix
    result = assess(raw, projected())
    assert len(result['explicit_prose']) == 1
    assert result['explicit_prose'][0]['code'] == code
    assert result['prose_scope'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('bad', ['', False, {}, [], 'weekly-evidence-followup/2'])
def test_invalid_new_marker_is_not_legacy_and_never_dispatches(db, bb, monkeypatch, bad):
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    attach(db, bb, marker=bad)
    monkeypatch.setattr(capo, 'OpenRouterClient', lambda **kw: pytest.fail('provider dispatched'))
    with pytest.raises(WeeklyRunBlocked, match='follow-up'):
        capo.run_capo(bb)


def test_legacy_native_http_resume_keeps_original_body_and_paid_receipt(db, bb, monkeypatch, tmp_path):
    store = attach(db, bb, marker=None)
    raw = response_for([binding()])
    sent = []
    native_client(monkeypatch, raw, sent)
    with llm_client.request_scope(journal(tmp_path, store), phase='capo', agent='capo', round_n=3):
        text, usage = capo.run_capo(bb)
    frozen = deepcopy(bb.data['_capo_request'])
    with journal(tmp_path, store)._db() as conn:
        before = [dict(r) for r in conn.execute('SELECT * FROM requests')]
    assert json.loads(before[0]['response'])['choices'][0]['message']['content'] == raw
    assert 'scorecard_scope' not in usage and 'scorecard_scope' not in frozen
    assert '<scorecard_scope>' not in frozen['user_message']
    bind_blackboard(bb, WeeklyRunStore(db, store.memo_id))
    with llm_client.request_scope(journal(tmp_path, store), phase='capo', agent='capo', round_n=3):
        replay, replay_usage = capo.run_capo(bb)
    assert text == replay and 'scorecard_scope' not in replay_usage
    assert len(sent) == 1 and bb.data['_capo_request'] == frozen
    with journal(tmp_path, store)._db() as conn:
        assert [dict(r) for r in conn.execute('SELECT * FROM requests')] == before


def test_changed_saved_projection_blocks_before_paid_request(db, bb, monkeypatch):
    attach(db, bb)
    calls = _prepara(monkeypatch, lambda *_: _msg(response_for([])))
    capo.run_capo(bb)
    bb.data['_capo_request']['scorecard_scope']['groups']['action:BUY']['n'] = 360
    with pytest.raises(ValueError, match='scope checkpoint changed'):
        capo.run_capo(bb)
    assert len(calls) == 1


@pytest.mark.parametrize('group,declared_groups,declared_use', [
    ('action:BUY', ['action:BUY'], 'description'),
    ('action:BUY', ['overall'], 'sizing'),
    ('action:ADD', ['action:ADD'], 'description'),
    ('action:ADD', ['overall'], 'sizing'),
])
def test_binding_cannot_contradict_its_recognized_operational_sentence(group, declared_groups, declared_use):
    from bellomberg.core.scorecard_scope_policy import assess
    phrase = 'Il track record di ' + group + ' giustifica un aumento del sizing.'
    row = binding(declared_groups, declared_use, phrase)
    result = assess(response_for([row]), projected())
    assert result['accepted_bindings'] == []
    assert result['rejected_bindings'][0]['row'] == 0
    assert result['status'] == 'REJECTED_BINDINGS'
    assert result['binding_status'] == 'INVALID'
    assert result['semantic_scope'] == result['prose_scope'] == 'NOT_ASSESSED'


def test_consistent_operational_binding_and_unrelated_description_remain_accepted():
    from bellomberg.core.scorecard_scope_policy import assess
    good = binding(['action:ADD'], 'sizing',
                   'Il track record di action:ADD giustifica un aumento del sizing.')
    description = binding(['action:BUY'], 'description', 'BUY ha un campione piccolo.')
    result = assess(response_for([good, description]), projected())
    assert result['accepted_bindings'] == [good, description]
    assert result['rejected_bindings'] == []
    assert result['status'] == 'BINDINGS_CHECKED'


def test_description_quoting_rejected_sentence_remains_descriptive():
    from bellomberg.core.scorecard_scope_policy import assess
    phrase = 'Il track record di action:BUY giustifica un aumento del sizing.'
    description = binding(['action:BUY'], 'description',
        'La frase "' + phrase + '" non e autorizzata dal campione BUY sotto soglia.')
    raw = phrase + '\n' + response_for([description])
    result = assess(raw, projected())
    assert result['explicit_prose'][0]['code'] == 'GROUP_NOT_OPERATIONAL'
    assert result['accepted_bindings'] == [description]
    assert result['rejected_bindings'] == []
    assert result['status'] == 'REJECTED_BINDINGS'


def test_multiline_instruction_containing_actual_covered_line_is_reconciled():
    from bellomberg.core.scorecard_scope_policy import assess
    phrase = 'Il track record di action:BUY giustifica un aumento del sizing.'
    row = binding(['overall'], 'description', 'Premessa descrittiva.\n' + phrase)
    result = assess(response_for([row]), projected())
    assert result['accepted_bindings'] == []
    assert result['rejected_bindings'][0]['code'] == 'GROUP_NOT_OPERATIONAL'
    assert result['status'] == 'REJECTED_BINDINGS'
