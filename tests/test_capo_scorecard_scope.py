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


# R07 prosa (PM 09/10, "Riconosci anche la prosa"): BUY=35, ADD=36, SELL=37, overall=108.
OPERATIONAL_ON_BUY = [
    'Dato il 60% di hit sui BUY (action:BUY, n=35), raddoppiamo la size su ZZSCA.',
    'Compra ZZSCA con size 4%: il track record dei BUY (35 esiti) lo giustifica.',
    '- Il track record di action:BUY giustifica un aumento del sizing.',
    'Il track record di action:BUY giustifica un aumento del sizing',
    '**Il track record di action:BUY giustifica un aumento del sizing.**',
    'Il track record di action:BUY giustifica un aumento della size.',
    '1. **Riduci** la posizione in TSLA al 2%: hit rate dei BUY 40%.',
    'Raccomando di aumentare la size su ZZSCA dato il track record dei BUY (35 esiti).',
    'Buy ZZSCA at 4% weight: the BUY track record (35 outcomes) supports it.',
    'We increase the position in ZZSCA to 5% given action:BUY hit rate (n=35).',
    'Il track record di BUY e ADD (35 e 36 esiti) giustifica di comprare: compriamo ZZSCA al 4%.',
    'Raddoppiamo la size su ZZSCA: hit rate dei BUY: 60%.',
    'Aumentiamo la size di ZZSCA al 4%: track record overall e dei BUY (108 e 35 esiti).',
]

# v3 (10/10, regola stretta): le stesse frasi SENZA misura numerica accanto al gruppo
# non sono una citazione esplicita dello scorecard -> NOT_ASSESSED (perdita accettata).
WITHOUT_NUMERIC_CITATION = [
    'Compra ZZSCA con size 4%: il track record dei BUY lo giustifica.',
    'Raccomando di aumentare la size su ZZSCA dato il track record dei BUY.',
    'Buy ZZSCA at 4% weight: the BUY track record supports it.',
    'We increase the position in ZZSCA to 5% given action:BUY hit rate.',
    'Il track record di BUY e ADD giustifica di comprare: compriamo ZZSCA al 4%.',
    'Aumentiamo la size di ZZSCA al 4%: track record overall e dei BUY a favore.',
    '- **Aumentiamo il peso** delle idee BUY al 5% grazie al track record dei BUY.',
    'Dato il track record di action:BUY, aumentiamo la size al 3%.',
    'Pertanto alziamo il peso MEDIA al 3% perché lo scorecard MEDIA mostra hit 60%.',
]


@pytest.mark.parametrize('line', WITHOUT_NUMERIC_CITATION)
def test_operational_prose_without_numeric_citation_is_not_assessed(line):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert result['explicit_prose'] == [] and result['status'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('line', OPERATIONAL_ON_BUY)
def test_operational_prose_on_small_group_is_rejected_and_kept_in_memo(line):
    from bellomberg.core.scorecard_scope_policy import assess, notice
    raw = MEMO_VERO + '\n' + line + '\n'
    result = assess(raw, projected())
    assert result['status'] == 'REJECTED_BINDINGS'
    assert result['binding_status'] == 'ABSENT'
    assert [e['code'] for e in result['explicit_prose']] == ['GROUP_NOT_OPERATIONAL']
    assert 'action:BUY' in result['explicit_prose'][0]['group_ids']
    assert result['explicit_prose'][0]['instruction'] == line.strip()
    assert 'binding operativo rifiutato' in notice(result)
    assert result['prose_scope'] == result['semantic_scope'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('line', [
    "Il track record dei BUY e' di 20 esiti, campione insufficiente.",
    "Il track record dei BUY e' 35 esiti con hit 51%: solo descrittivo.",
    'Il track record dei BUY sale a 35 esiti e la size media resta al 3%.',
    'Non aumentiamo la size dei BUY: il track record (n=35) non basta.',
    'Se il track record dei BUY superasse 36 esiti, aumenteremmo la size.',
    "Un aumento della size dei BUY non e' giustificato dal track record.",
    'Possiamo comprare ZZSCA al 4%? Il track record dei BUY ha 35 esiti.',
    'La frase "Compra ZZSCA con size 4%: il track record dei BUY lo giustifica." e\' fuori soglia.',
    'Compra ZZSCA con size 4% sulla base della tesi fondamentale.',
    'Aumento il sizing dei BUY.',
    'Privilegio gradualmente gli acquisti nelle prossime settimane.',
    # v2 (PM 10/10): a markdown quotation (`>`) is somebody else's text, never the Capo's.
    '> We increase the position in ZZSCA to 5% given action:BUY hit rate.',
    '> Il track record di action:BUY giustifica un aumento del sizing.',
])
def test_descriptive_or_unscored_prose_is_not_recognized(line):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, projected())
    assert result['explicit_prose'] == []
    assert result['status'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('line,groups', [
    ('Aumentiamo la size degli ADD al 3%: track record action:ADD n=36.', ['action:ADD']),
    ('- **Riduci** la posizione in TSLA al 2%: hit rate dei SELL 60%.', ['action:SELL']),
    ("Raccomando di aumentare la size su ZZSCA: il track record overall (108 esiti) e' solido.", ['overall']),
    ('Aumentiamo la size di ZZSCA al 5% (confidenza ALTA, 50 esiti, hit rate 70%).', ['confidence:ALTA']),
])
def test_operational_prose_on_eligible_group_is_checked_not_rejected(line, groups):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, projected())
    assert [(e['group_ids'], e['code']) for e in result['explicit_prose']] == [(groups, None)]
    assert result['status'] == 'BINDINGS_CHECKED'


@pytest.mark.parametrize('declared_groups,declared_use,line', [
    (['action:BUY'], 'description', 'Compra ZZSCA con size 4%: action:BUY ha hit 60%.'),
    (['overall'], 'sizing', 'Aumento il sizing dei BUY del 2% grazie al track record (35 esiti).'),
    (['overall'], 'description', '- Compra ZZSCA con size 4%: il track record dei BUY (35 esiti) lo giustifica.'),
])
def test_overall_or_description_label_does_not_launder_small_group(declared_groups, declared_use, line):
    from bellomberg.core.scorecard_scope_policy import assess
    row = binding(declared_groups, declared_use, line)
    result = assess(response_for([row]), projected())
    assert result['accepted_bindings'] == []
    assert result['rejected_bindings'] == [{'row': 0, 'code': 'GROUP_NOT_OPERATIONAL'}]
    assert result['status'] == 'REJECTED_BINDINGS'


@pytest.mark.parametrize('declared_groups,sentence,code', [
    (['action:BUY'], 'Compra ZZSCA con size 4%: action:BUY ha hit 60%.', 'GROUP_NOT_OPERATIONAL'),
    (['action:ADD'], 'Aumentiamo la size degli ADD al 3%: track record action:ADD n=36.',
     'EXPLICIT_SCOPE_MISMATCH'),
])
def test_description_binding_quoting_part_of_formatted_line_is_checked(declared_groups, sentence, code):
    # The binding quotes the sentence without the markdown of the memo line: only
    # the binding's own text can reveal that its declared description is operational.
    from bellomberg.core.scorecard_scope_policy import assess
    row = binding(declared_groups, 'description', sentence)
    raw = (MEMO_VERO + '\n- **' + sentence + '** Nota a margine.\n<scorecard_bindings>'
           + json.dumps({'rows': [row]}) + '</scorecard_bindings>')
    result = assess(raw, projected())
    assert result['accepted_bindings'] == []
    assert result['rejected_bindings'] == [{'row': 0, 'code': code}]


def test_eligible_operational_sentence_labelled_description_is_a_mismatch():
    from bellomberg.core.scorecard_scope_policy import assess
    line = 'Aumentiamo la size degli ADD al 3%: track record action:ADD n=36.'
    bad = assess(response_for([binding(['action:ADD'], 'description', line)]), projected())
    assert bad['rejected_bindings'] == [{'row': 0, 'code': 'EXPLICIT_SCOPE_MISMATCH'}]
    good_row = binding(['action:ADD'], 'sizing', line)
    good = assess(response_for([good_row]), projected())
    assert good['accepted_bindings'] == [good_row] and good['status'] == 'BINDINGS_CHECKED'


def test_native_bullet_prose_on_small_group_is_rejected_and_left_in_memo(db, bb, monkeypatch):
    attach(db, bb)
    raw = MEMO_VERO + '\n- **Compra ZZSCA con size 4%: il track record dei BUY (35 esiti) lo giustifica.**\n'
    _prepara(monkeypatch, lambda *_: _msg(raw))
    text, usage = capo.run_capo(bb)
    assert usage['scorecard_scope']['status'] == 'REJECTED_BINDINGS'
    assert usage['scorecard_scope']['explicit_prose'][0]['code'] == 'GROUP_NOT_OPERATIONAL'
    assert 'binding operativo rifiutato' in text and raw in text


# R07 prosa v2 (PM 10/10, review avversariale): da co-occorrenza a LEGAME. Scope del
# revisore: BUY 35, MEDIA 30, BASSA 28 sotto soglia; ADD 36, SELL 37, ALTA 50,
# overall 108, quant 40, macro 68.
def reviewer_scope(confidence=True):
    from bellomberg.core.scorecard_scope_policy import project
    rows = []
    for i in range(108):
        rows.append({'id': i + 1, 'memo_id': 700 + i, 'hit': i % 2 == 0, 'horizon_used': '4w',
                     'action': 'BUY' if i < 35 else 'ADD' if i < 71 else 'SELL',
                     'confidence_bucket': 'ALTA' if i < 50 else 'MEDIA' if i < 80 else 'BASSA',
                     'specialists': ['quant'] if i < 40 else ['macro']})
    value = {'computed_at': '2032-01-01T10:00:00', 'details': rows, 'overall': agg(rows),
             'by_action': {a: agg([r for r in rows if r['action'] == a]) for a in ('BUY', 'ADD', 'SELL')},
             'by_confidence': ({c: agg([r for r in rows if r['confidence_bucket'] == c])
                                for c in ('ALTA', 'MEDIA', 'BASSA')} if confidence else {}),
             'by_specialist': {s: agg([r for r in rows if s in r['specialists']]) for s in ('quant', 'macro')},
             'n_unmeasurable': 0, 'worst_calls': [], 'method_note': 'directional local currency 4w/1w'}
    return project(value)


# Le 64 frasi lecite della review (falsi positivi della v1: 24): mai un rifiuto.
REVIEW_LAWFUL = [
    '| BUY | 35 | 54% |',
    '| Azione | Esiti | Hit rate | Peso medio |',
    '| BUY | 35 esiti | 54% | riduciamo |',
    'Riduciamo il rischio di coda al 2% del NAV tramite put su SPY.',
    "Aumentiamo l'esposizione a QQQ al 5% del NAV: il track record complessivo resta descrittivo.",
    'Il campione è sotto soglia quindi non lo uso.',
    'Abbiamo 35 esiti BUY: aumenteremo la size solo sopra 36.',
    'Scenario base (probabilità 55%): riduciamo la posizione su ZZSCA al 3%; il track record dei BUY '
    '(35 esiti) è solo descrittivo.',
    'Compriamo BTC al 2% del NAV con convinzione ALTA; lo scorecard ALTA conta 50 esiti.',
    'Riduciamo il peso di TSLA al 1,5% del NAV; la convinzione è MEDIA e il track record della fascia '
    'MEDIA è di 30 esiti, solo descrittivo.',
    'Aumentiamo la posizione su MSFT al 4%: la decisione si fonda sui fondamentali, non sul track record.',
    "Riduciamo l'allocazione azionaria al 60%; hit rate overall 50% su 108 esiti.",
    "Tagliamo l'esposizione al credito HY al 3% — il Red Team ha segnalato che il campione BUY è piccolo.",
    'Il Red Team: «Riducete la size dei BUY, il track record è 35 esiti».',
    '> Red Team: Riduciamo la size dei BUY, il track record conta 35 esiti.',
    'La posizione del mercato sui tassi è cambiata: compriamo duration al 10% del NAV, hit rate della curva 60%.',
    'Vendiamo il 20% della posizione su ENI; esiti delle SELL: 37, sopra soglia.',
    'Alleggeriamo GLD al 4% del NAV. Lo scorecard (overall, 108 esiti) è citato solo come contesto.',
    '## Raccomandazione 3 — HOLD su AAPL, peso 6%, track record overall 108 esiti',
    'Manteniamo HOLD su AAPL; riduciamo il peso al 6% (track record overall: 108 esiti).',
    'Compriamo EXIT strategy hedge al 1%: il track record dei SHORT non esiste.',
    "Aumentiamo l'esposizione a energia al 7%; il desk macro ha 68 esiti nello scorecard.",
    'Raddoppiamo la posizione su ZZSCD al 2% del NAV, campione specialistico quant n=40.',
    'Riduciamo la duration: probabilità di recessione 35%, il campione macro è ampio (68 esiti).',
    '1. Compriamo SPY al 5% del NAV (convinzione ALTA, hit rate ALTA 52% su 50 esiti).',
    '**Riduciamo il beta al 70%**: il track record SELL (37 esiti) conferma la disciplina.',
    'Aumentiamo la size delle ADD al 3%: action:ADD conta 36 esiti.',
    'Media mobile a 200 giorni: riduciamo il peso al 2%, track record tecnico 60%.',
    'The BUY bucket has 35 outcomes, so we keep it descriptive and trim ZZSCA to 3% on valuation.',
    'We trim ZZSCA to 3% of NAV; the overall hit rate (108 outcomes) is context only.',
    'Then reduce exposure to EM to 4%; scorecard overall 108 outcomes.',
    'Riduciamo la posizione MEDIA del portafoglio al 3% per titolo; hit rate overall 50%.',
    'Il consenso MEDIA sugli utili: riduciamo il peso di ASML al 2%, esiti trimestrali positivi.',
    'Aumentiamo la posizione: la size passa al 4% su SELL-side upgrade, esiti del consensus positivi.',
    'Vendiamo puts su SPY per il 2% del NAV; win rate storico delle put spread 70%.',
    "Riduciamo l'esposizione al dollaro al 15%; outcome dello scenario base 60% di probabilità.",
    "Compriamo TLT al 6% del NAV, perché l'hit rate di questo segnale nel campione accademico è 62%.",
    'Aumentiamo il peso su SHORT duration al 8%, esiti delle aste positivi.',
    'Riduciamo la posizione al 3%. Il track record BUY (35 esiti) resta descrittivo.',
    'Riduciamo il peso attribuito al campione BUY (35 esiti), troppo piccolo per guidare il sizing.',
    'Dimezziamo il peso dato allo scorecard dei BUY nelle valutazioni: 35 esiti sono pochi.',
    'Aumentiamo la size delle ADD al 3% (action:ADD 36 esiti), mentre i BUY (35 esiti) restano solo descritti.',
    'Hit rate dei BUY: aumenta al 54% su 35 esiti; peso medio invariato al 3%.',
    "Peso medio delle idee BUY: aumenta dal 2% al 3% nell'ultimo trimestre (35 esiti).",
    'Il mercato è ottimista e sovrappesa i semiconduttori al 25% (hit rate BUY 54%, 35 esiti).',
    "Storicamente, quando aumentiamo la size dei BUY oltre il 4%, l'hit rate scende (35 esiti).",
    'Compriamo ZZSCA al 3% del NAV: hit rate dei BUY 54% su 35 esiti, da leggere con cautela.',
    'Riduciamo GOOGL al 2% del NAV, in linea con la disciplina: lo scorecard mostra BUY 35 esiti e SELL 37 esiti.',
    'Alziamo la liquidità al 12% del NAV, mentre il track record per convinzione (ALTA 50, MEDIA 30, '
    'BASSA 28 esiti) è riportato in appendice.',
    "Fase di BASSA liquidità: riduciamo l'esposizione al 5%, esiti delle aste deboli.",
    'Riduciamo il peso di AAPL al 5% (HOLD), track record overall 108 esiti.',
    'Portiamo EXIT parziale: vendiamo il 30% della posizione, hit rate overall 50%.',
    'Scenario 2 (30%): rate-cut della Fed; BUY hit rate 54% over 35 outcomes.',
    'Scenario 2 (30%): rate-cut by the Fed, BUY hit rate 54% over 35 outcomes.',
    'We cut equity exposure to 55% of NAV; the scorecard shows BUY at 35 outcomes and SELL at 37.',
    "Aumentiamo la copertura al 10% del NAV — il Red Team osserva che l'hit rate BUY (35 esiti) è fragile.",
    'Compriamo protezione al 2% del NAV; la nota del Red Team ricorda: track record BUY 35 esiti.',
    '> **Red Team:** Aumentiamo la size dei BUY al 4%? No: il track record conta 35 esiti.',
    "> Red Team (citazione): aumentiamo la size dei BUY al 4% perché l'hit rate è 54%.",
    'Riduciamo la posizione su REDUCE-candidates al 2%, outcome atteso positivo.',
    'Il punto di forza: compra chi ha track record, e la posizione BUY del consensus è al 70%.',
    'Raddoppiamo la copertura al 4%: il campione overall (108 esiti) e quello ALTA (50) sono adeguati, '
    'BASSA (28) resta in osservazione.',
    'Trim TSLA to 1% of NAV. BUY outcomes: 35 (descriptive only).',
    'Then trim TSLA to 1% of NAV, BUY outcomes 35 for reference.',
]


@pytest.mark.parametrize('line', REVIEW_LAWFUL)
def test_lawful_prose_from_review_is_never_rejected(line):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert result['status'] != 'REJECTED_BINDINGS'
    assert [e for e in result['explicit_prose'] if e['code']] == []


def test_review_lawful_battery_is_the_full_64():
    assert len(REVIEW_LAWFUL) == len(set(REVIEW_LAWFUL)) == 64


# Operative: legame visibile verbo -> size <- giustificazione dal track record del gruppo.
REVIEW_OPERATIVE = [
    ('Aumentiamo la size dei BUY al 4%: il track record BUY (35 esiti, hit 54%) lo giustifica.', ['action:BUY']),
    ('- **Aumentiamo il peso** delle idee BUY al 5% grazie al track record dei BUY (35 esiti).', ['action:BUY']),
    ('Dato il track record di action:BUY (n=35), aumentiamo la size al 3%.', ['action:BUY']),
    ('Riduciamo la size delle idee a convinzione BASSA al 1%: il loro hit rate (28 esiti) è debole.',
     ['confidence:BASSA']),
    ('We increase BUY sizing to 4% given the BUY track record of 35 outcomes.', ['action:BUY']),
    ('Pertanto alziamo il peso MEDIA al 3% perché lo scorecard MEDIA (30 esiti) mostra hit 60%.',
     ['confidence:MEDIA']),
    ('Aumentiamo la size dei BUY al 4%: track record overall 108 esiti.', ['action:BUY', 'overall']),
    ('Aumentiamo la size dei BUY al 4%, sostenuti da un hit rate del 54% (35 esiti).', ['action:BUY']),
    ("Porteremo la size dei BUY al 4% grazie all'hit rate (35 esiti).", ['action:BUY']),
    ("Incrementiamo del 50% la size dei BUY: lo scorecard (35 esiti) e' incoraggiante.", ['action:BUY']),
]


@pytest.mark.parametrize('line,groups', REVIEW_OPERATIVE)
def test_linked_operational_prose_names_its_groups_and_is_rejected(line, groups):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert [(e['group_ids'], e['code']) for e in result['explicit_prose']] == [(groups, 'GROUP_NOT_OPERATIONAL')]
    assert result['status'] == 'REJECTED_BINDINGS'


def test_labels_absent_from_scope_are_never_groups_nor_unknown():
    from bellomberg.core.scorecard_scope_policy import assess
    for line in ['Compriamo SPY al 5% del NAV (convinzione ALTA, hit rate 50%).',
                 'Riduciamo il peso di AAPL al 5% grazie al track record HOLD (40 esiti).']:
        result = assess(MEMO_VERO + '\n' + line, reviewer_scope(confidence=False))
        assert result['explicit_prose'] == [] and result['status'] == 'NOT_ASSESSED'


def test_overall_is_reported_beside_the_specific_group_it_cannot_lend_to():
    # Prompt: "riporta TUTTI i group_ids coinvolti"; overall non presta il campione.
    from bellomberg.core.scorecard_scope_policy import assess
    line = 'Aumentiamo la size degli ADD al 3%: track record overall e action:ADD n=36.'
    result = assess(MEMO_VERO + '\n' + line, projected())
    assert [(e['group_ids'], e['code']) for e in result['explicit_prose']] == [(['action:ADD', 'overall'], None)]
    short = assess(response_for([binding(['action:ADD'], 'sizing', line)]), projected())
    assert short['rejected_bindings'] == [{'row': 0, 'code': 'EXPLICIT_SCOPE_MISMATCH'}]
    full_row = binding(['action:ADD', 'overall'], 'sizing', line)
    full = assess(response_for([full_row]), projected())
    assert full['accepted_bindings'] == [full_row] and full['status'] == 'BINDINGS_CHECKED'


@pytest.mark.parametrize('separated,linked', [
    # ";" chiude la proposizione: il richiamo dopo non giustifica l'azione.
    ("Compriamo ZZSCA al 3% del NAV; dato l'hit rate dei BUY 54% su 35 esiti.",
     "Compriamo ZZSCA al 3% del NAV, dato l'hit rate dei BUY 54% su 35 esiti."),
    # v3: ", e" separa come ";".
    ('Aumentiamo la size di ZZSCA al 3%, e dato il track record dei BUY (35 esiti) restiamo prudenti.',
     'Aumentiamo la size di ZZSCA al 3% e dato il track record dei BUY (35 esiti) restiamo prudenti.'),
    # v3: "ma" separa (mutante M1 della seconda review).
    ("Aumentiamo la size delle ADD al 3,5% ma dato l'hit rate dei BUY (35 esiti) la size dei BUY resta al 3%.",
     "Aumentiamo la size delle ADD al 3,5% e dato l'hit rate dei BUY (35 esiti) la size dei BUY resta al 3%."),
    # una domanda non e' un'istruzione.
    ('Aumentiamo la size dei BUY al 4% visto il track record di 35 esiti?',
     'Aumentiamo la size dei BUY al 4% visto il track record di 35 esiti.'),
    # il testo tra virgolette e' di altri.
    ('Il desk scrive: «Aumentiamo la size dei BUY al 4% grazie al track record dei BUY (35 esiti)».',
     'Aumentiamo la size dei BUY al 4% grazie al track record dei BUY (35 esiti).'),
    ('Nel memo "Aumentiamo la size dei BUY al 4% grazie al track record dei BUY (35 esiti)" e lo respingiamo.',
     'Nel memo aumentiamo la size dei BUY al 4% grazie al track record dei BUY (35 esiti).'),
    ('Nel memo “Aumentiamo la size dei BUY al 4% grazie al track record dei BUY (35 esiti)”.',
     'Nel memo: aumentiamo la size dei BUY al 4% grazie al track record dei BUY (35 esiti).'),
    # v3: discorso riportato PRIMA del verbo (sostiene che, propone:, argues).
    ('Il Red Team sostiene che alziamo la size dei BUY al 4% grazie a un hit rate su soli 35 esiti.',
     'Alziamo la size dei BUY al 4% grazie a un hit rate su soli 35 esiti.'),
    ("Il Red Team propone: alziamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti).",
     "Il comitato decide: alziamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti)."),
    ('The Red Team argues we size up BUY ideas to 4% because of a 54% hit rate on 35 outcomes.',
     'We size up BUY ideas to 4% because of a 54% hit rate on 35 outcomes.'),
    # trattino spaziato, "mentre": proposizioni separate.
    ('Aumentiamo la size di ZZSCA al 4% - hit rate dei BUY 54%.', 'Aumentiamo la size di ZZSCA al 4%: hit rate dei BUY 54%.'),
    ('Aumentiamo la size di ZZSCA al 4%, mentre hit rate dei BUY 54%.',
     'Aumentiamo la size di ZZSCA al 4%, dato il hit rate dei BUY 54%.'),
    # riportato dopo il verbo: il pezzo che riferisce (il Red Team osserva) non lega, anche dopo "perche'".
    ("Aumentiamo la size di ZZSCA al 4%: il Red Team osserva l'hit rate dei BUY 54%.",
     "Aumentiamo la size di ZZSCA al 4%: l'hit rate dei BUY 54%."),
    ("Aumentiamo la size di ZZSCA al 4% perche' il Red Team osserva l'hit rate dei BUY 54%.",
     "Aumentiamo la size di ZZSCA al 4% perche' l'hit rate dei BUY e' 54%."),
    # cautela/contesto sciolgono il legame.
    ('Aumentiamo la size di ZZSCA al 4%: hit rate dei BUY 54%, solo come contesto.',
     'Aumentiamo la size di ZZSCA al 4%: hit rate dei BUY 54%, a favore.'),
    # v3: la frase che dichiara il limite non e' mai respinta.
    ('Portiamo la size delle idee a convinzione ALTA al 5%: lo scorecard e\' ALTA 50, MEDIA 30, BASSA 28 esiti, '
     'e solo ALTA supera la soglia.',
     'Portiamo la size delle idee a convinzione MEDIA al 3%: track record MEDIA 30 esiti.'),
    # meno peso allo scorecard e' conforme; piu' peso no.
    ('Riduciamo il peso attribuito al campione BUY: hit rate dei BUY 54% su 35 esiti.',
     'Riduciamo il peso delle idee BUY: hit rate dei BUY 54% su 35 esiti.'),
    # 3a persona singolare e verbo dentro una parola: descrittivi.
    ('Il desk aumenta la size di ZZSCA al 4% grazie al track record dei BUY (35 esiti).',
     'Aumentiamo la size di ZZSCA al 4% grazie al track record dei BUY (35 esiti).'),
    ('Scenario: rate-cut al 4% grazie al track record dei BUY (35 esiti).',
     'Scenario: cut. Cut ZZSCA to 4% given the BUY track record (35 outcomes).'),
    # imperativo solo a inizio frase.
    ('Il punto: compra ZZSCA al 4% grazie al track record dei BUY (35 esiti).',
     'Compra ZZSCA al 4% grazie al track record dei BUY (35 esiti).'),
    # MEDIA/BASSA aggettivi senza "convinzione" non sono il gruppo della size.
    ('Riduciamo la posizione MEDIA al 3%: il loro hit rate (30 esiti) e\' debole.',
     'Riduciamo la posizione a convinzione MEDIA al 3%: il loro hit rate (30 esiti) e\' debole.'),
    # v3: parentesi descrittiva con la sola etichetta non e' una citazione.
    ('Riduciamo la size di ZZSCA al 3% (consenso MEDIA, 30 esiti).',
     'Riduciamo la size di ZZSCA al 3% (convinzione MEDIA, 30 esiti).'),
    ('Aumentiamo ZZSCA al 5% (BUY, upside 25%, esito della trimestrale atteso il 20/11).',
     'Aumentiamo ZZSCA al 5% (BUY, 35 esiti, upside 25%).'),
    # "esiti" generico senza gruppo non e' un richiamo al gruppo.
    ('Riduciamo la size dei titoli al 3% grazie agli esiti delle aste BUY-back.',
     'Riduciamo la size dei titoli al 3% grazie ai 35 esiti dei BUY.'),
    # v3: track record senza numero e senza gruppo accanto (del management) non cita.
    ('Portiamo la size del BUY su Ferrari al 4%, dato il track record del management: 12 trimestri di beat.',
     'Portiamo la size del BUY su Ferrari al 4%, dato il track record dei BUY: 35 esiti.'),
    # v3: la citazione deve APRIRE la giustificazione, non comparire dopo.
    ('Portiamo la size dei BUY al 4% grazie alla valutazione, con hit rate dei BUY 54% su 35 esiti.',
     'Portiamo la size dei BUY al 4% grazie all\'hit rate dei BUY 54% su 35 esiti, con la valutazione.'),
    # citazione markdown.
    ('> Aumentiamo la size di ZZSCA al 4% grazie al track record dei BUY (35 esiti).',
     'Aumentiamo la size di ZZSCA al 4% grazie al track record dei BUY (35 esiti).'),
    # v3: copertura dei falsi negativi dentro la regola stretta.
    ('La size dei BUY e\' stata portata al 4% (35 esiti).',
     "La size dei BUY viene portata al 4% grazie all'hit rate dei BUY (54%, 35 esiti)."),
    ('Aumento della size media nel trimestre: hit rate dei BUY 54% su 35 esiti.',
     'Aumento della size sui BUY al 4% (hit rate 54%, 35 esiti).'),
    ('Riportiamo per completezza: hit rate dei BUY 54% su 35 esiti.',
     "Riportiamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti)."),
])
def test_link_markers_decide_between_not_assessed_and_rejection(separated, linked):
    from bellomberg.core.scorecard_scope_policy import assess
    assert assess(MEMO_VERO + '\n' + separated, reviewer_scope())['explicit_prose'] == []
    result = assess(MEMO_VERO + '\n' + linked, reviewer_scope())
    assert [e['code'] for e in result['explicit_prose']] == ['GROUP_NOT_OPERATIONAL']
    assert result['status'] == 'REJECTED_BINDINGS'


@pytest.mark.parametrize('formatted', [False, True])
def test_binding_text_naming_more_groups_than_declared_is_a_mismatch(formatted):
    from bellomberg.core.scorecard_scope_policy import assess
    sentence = 'Aumentiamo la size degli ADD e dei SELL al 3%: track record action:ADD n=36 e action:SELL n=37.'
    row = binding(['action:ADD'], 'sizing', sentence)
    memo = MEMO_VERO + '\n' + ('- **' + sentence + '** Nota a margine.' if formatted else sentence)
    result = assess(memo + '\n<scorecard_bindings>' + json.dumps({'rows': [row]}) + '</scorecard_bindings>',
                    projected())
    assert result['rejected_bindings'] == [{'row': 0, 'code': 'EXPLICIT_SCOPE_MISMATCH'}]
    both = binding(['action:ADD', 'action:SELL'], 'sizing', sentence)
    ok = assess(memo + '\n<scorecard_bindings>' + json.dumps({'rows': [both]}) + '</scorecard_bindings>',
                projected())
    assert ok['accepted_bindings'] == [both] and ok['status'] == 'BINDINGS_CHECKED'


def test_notice_quotes_rejected_prose_with_its_line_and_separates_the_block():
    from bellomberg.core.scorecard_scope_policy import assess, notice
    short = 'Aumentiamo la size dei BUY al 4% grazie al track record dei BUY (35 esiti).'
    long = ('Aumentiamo la size dei BUY al 4% grazie al track record dei BUY (35 esiti), '
            + 'con una motivazione che continua ben oltre il limite della nota ' * 3).strip()
    rows = [binding(['action:BUY'], 'sizing', short)]
    raw = ('Premessa.\n<scorecard_bindings>\n' + json.dumps({'rows': rows}) + '\n</scorecard_bindings>\n'
           + short + '\nAltro testo.\n' + long)
    result = assess(raw, reviewer_scope())
    lines = raw.splitlines()
    assert [e['line'] for e in result['explicit_prose']] == [lines.index(short) + 1, lines.index(long) + 1]
    text = notice(result)
    assert ('riga ' + str(lines.index(short) + 1) + ' «' + short + '» (GROUP_NOT_OPERATIONAL: action:BUY)'
            in text)
    cut = '«' + long[:120].rstrip() + '…'
    assert 'riga ' + str(lines.index(long) + 1) + ' ' + cut + ' (' in text
    assert long[:120].rstrip() + '…»' not in text and long not in text
    assert 'Respinta la PROSA del memo' in text
    assert 'Respinto il BLOCCO scorecard_bindings: riga JSON 0 (GROUP_NOT_OPERATIONAL)' in text
    prose_only = notice(assess(MEMO_VERO + '\n' + short, reviewer_scope()))
    assert 'Respinta la PROSA del memo' in prose_only and 'BLOCCO' not in prose_only
    block_only = notice(assess(response_for([binding()]), reviewer_scope()))
    assert 'BLOCCO' in block_only and 'PROSA' not in block_only


def test_policy_v2_prompt_states_the_linked_prose_rule():
    from bellomberg.core import scorecard_scope_policy as policy
    assert policy.POLICY == 'capo-scorecard-scope/2'
    assert policy.POLICY in policy.INSTRUCTIONS
    assert 'overall compreso' in policy.INSTRUCTIONS and 'nota di rifiuto' in policy.INSTRUCTIONS
    assert 'Overall non presta il campione' in policy.INSTRUCTIONS
    # v3: the prompt states the strict rule the check applies (numeric citation, same clause).
    assert 'CITAZIONE NUMERICA' in policy.INSTRUCTIONS and 'stessa proposizione' in policy.INSTRUCTIONS
    assert 'due punti' not in policy.INSTRUCTIONS
    assert 'citazione numerica' in policy.notice(policy.assess(MEMO_VERO, projected()))


def test_unclosed_quotes_and_verb_floods_stay_linear():
    import time
    from bellomberg.core.scorecard_scope_policy import assess
    scope = reviewer_scope()
    for raw in ['“' * 40000, '«' * 40000, '"a' * 20001,
                'Riduciamo ' * 4000 + 'size 3% esiti BUY', 'Aumentiamo la size dei BUY' + ' e BUY' * 8000,
                'Aumentiamo la size dei BUY al 4% grazie al track record dei BUY ' + '1 e ' * 20000 + 'esiti',
                'Aumentiamo la size dei BUY al 4% (' * 3000 + 'hit rate 54%, 35 esiti)',
                'Aumentiamo la size dei BUY al 4%; ' * 3000]:
        started = time.perf_counter()
        assess(raw, scope)
        assert time.perf_counter() - started < 2.0


# R07 prosa v3 (10/10, regola stretta del coordinatore dopo la seconda review: v2 13/67
# falsi positivi). Corpus NUOVO del secondo revisore (scratchpad r07v2rev/corpus.py):
# 67 lecite, 24 operative. Scope: reviewer_scope().
REVIEW2_LAWFUL = [
    "**Decisioni.** Manteniamo ASML al 4,5% del NAV: la tesi sull'EUV High-NA resta intatta e il multiplo forward (28x) e' sotto la media quinquennale.",
    "1. **ZZSCA - BUY, ALTA convinzione.** Apriamo una posizione al 3% del NAV con stop a -12%; upside stimato 22% nello scenario base (probabilita' 55%).",
    "Incrementiamo LVMH al 2,5% del NAV perche' il de-rating ha gia' scontato il rallentamento cinese (P/E 19x contro 24x storico).",
    "Riportiamo la liquidita' al 10% del NAV in vista della riunione FOMC; il track record dei BUY (35 esiti) resta descrittivo.",
    'Ribilanciamo il book verso i difensivi: XLU e XLP salgono al 6% complessivo, dato il beta del portafoglio a 1,15.',
    '| Ticker | Azione | Peso NAV | Convinzione | Upside |',
    '| MSFT | ADD | 5,0% -> 6,0% | ALTA | 18% |',
    '| PLTR | BUY | 0% -> 1,5% | MEDIA | 35% |',
    'Aumentiamo la size delle ADD al 3,5% grazie al track record delle ADD (36 esiti, hit rate 61%).',
    "Aumentiamo la size delle idee ad ALTA convinzione al 5% grazie all'hit rate della fascia ALTA (50 esiti, 64%).",
    'We size up GOOGL to 4% of NAV given the 31% discount to our DCF fair value.',
    "We trim AMD to 2% because the Red Team's bear case (probability 35%) now looks more likely.",
    'Accorciamo la duration: TLT scende al 3% del NAV, dato il term premium in risalita.',
    'Scale into COIN over three weeks, up to 2% of NAV, on the back of the ETF inflow data.',
    "Portiamo META al 4% del NAV: l'hit rate complessivo del comitato su 108 esiti e' del 57%, ma la decisione poggia sulla valutazione (EV/EBIT 17x).",
    'Portiamo META al 4% del NAV (hit rate overall 57% su 108 esiti).',
    "Riduciamo la posizione BUY su TSMC al 3% perche' l'esito delle elezioni a Taiwan resta binario.",
    'We trim our BUY position in TSMC to 3% because the election outcome in Taiwan is binary.',
    "Aumentiamo la posizione BUY in Leonardo al 3% perche' il campione nazionale della difesa beneficia del riarmo europeo.",
    'We cut the BUY position in NKE to 1% because margins were hit by tariffs.',
    'Aumentiamo ZZSCA al 5% (BUY, upside 25%, esito della trimestrale atteso il 20/11).',
    'Compriamo ZZSCB al 2% del NAV (BUY, esito del contenzioso con Delta atteso a dicembre).',
    'Aumentiamo la size delle ADD al 3,5% grazie al track record delle ADD (36 esiti), e i BUY (35 esiti) restano al 3%.',
    "Portiamo la size delle idee a convinzione ALTA al 5%: per convinzione lo scorecard e' ALTA 50, MEDIA 30, BASSA 28 esiti, e solo ALTA supera la soglia.",
    'We size up conviction ALTA ideas to 5%: by conviction the scorecard is ALTA 50, MEDIA 30, BASSA 28 outcomes, and only ALTA clears the 36 bar.',
    "Il Red Team sostiene che alziamo troppo la size dei BUY al 4% grazie a un hit rate su soli 35 esiti; accogliamo l'obiezione e restiamo al 3%.",
    'The Red Team argues we size up BUY ideas to 4% because of a 54% hit rate on 35 outcomes; we agree and keep them at 3%.',
    "Il Red Team propone: alziamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti). Respingiamo la proposta.",
    "Il Red Team propone: «alziamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti)». Respingiamo la proposta.",
    "> Red Team: portiamo la size delle idee a convinzione MEDIA al 3% perche' il loro hit rate (30 esiti) e' al 60%.",
    'Portiamo la size del BUY su Ferrari al 4%, dato il track record del management: 12 trimestri consecutivi di beat.',
    "Lasciamo invariata la size dei BUY al 3%: il track record (35 esiti) e' sotto la soglia di 36 e vale solo come descrizione.",
    'Lo scorecard dei BUY conta 35 esiti con hit rate 54%: lo riportiamo per completezza, senza effetti su size o selezione.',
    '### Sizing',
    'La size media delle nuove idee resta al 3% del NAV, con un tetto del 6% per singolo nome e del 25% per settore.',
    '2. **Sizing BUY**: portiamo la size dei nuovi BUY al 3% del NAV in linea con il limite di rischio del mandato.',
    '3. **Sizing ADD**: incrementiamo la size delle ADD al 4% grazie al track record delle ADD, 36 esiti con hit rate 61% [src: scorekeeper].',
    'Il tasso di successo delle SELL (37 esiti, 59%) giustifica: tagliamo la posizione in INTC al 1%.',
    "Aumentiamo l'esposizione a GLD al 7% del NAV, dato il calo dei rendimenti reali (TIPS 10 anni all'1,6%).",
    "Sovrappesiamo l'Europa (EWG, EWQ) portando il peso al 12%, alla luce del differenziale di crescita degli utili (+6% contro +3% negli USA).",
    'Raddoppiamo la copertura su QQQ (put 450, scadenza dicembre) al 2% del NAV, data la concentrazione del book sui semiconduttori (34%).',
    "Tagliamo la posizione su BABA al 1,5% per il rischio regolamentare: la probabilita' di nuove sanzioni sale al 40% secondo il nostro scenario.",
    "Dimezziamo l'esposizione al credito HY (HYG) al 2%: gli spread a 290 pb non pagano il rischio di default (tasso implicito 4,1%).",
    '**Rischi.** La convinzione MEDIA su PLTR riflette la dipendenza dai contratti governativi (55% dei ricavi).',
    'Il campione dei BUY (35 esiti) e quello delle idee a convinzione MEDIA (30) e BASSA (28) restano sotto 36: li citiamo come descrizione.',
    'Per trasparenza: track record overall 108 esiti, hit rate 57%; ADD 36 esiti, SELL 37 esiti, ALTA 50 esiti; BUY 35, MEDIA 30, BASSA 28.',
    'Accumuliamo SOL fino al 2% del NAV in tre tranche, sostenuti dalla ripresa delle commissioni on-chain (+40% m/m).',
    'Alleggeriamo NOVO-B al 2,5%: il dato sulle prescrizioni settimanali di Wegovy (-4%) conferma il rallentamento.',
    'Riduciamo il peso attribuito al campione BUY nelle valutazioni del comitato: 35 esiti sono pochi.',
    'Raise MSFT to 6% of NAV based on the Azure re-acceleration (+34% y/y).',
    'We add to SELL conviction on TSLA, cutting the position to 0,5% given the delivery miss (-8% y/y).',
    'We increase the ADD sizing to 3.5% given the ADD track record (36 outcomes); BUY (35 outcomes) is description only.',
    "Aumentiamo SAP al 3% del NAV - l'hit rate dei BUY (35 esiti) e' solo una descrizione, non la ragione.",
    'Compriamo SPY al 5% del NAV, idea a MEDIA convinzione, coerente con un hit rate overall del 57% su 108 esiti.',
    'Portiamo il peso dei SELL al 4% grazie al loro track record: SELL 37 esiti, hit rate 59%.',
    "Vendiamo il 30% della posizione in AMZN, portandola al 3%, perche' il target price (USD 215) e' stato raggiunto.",
    "Teniamo la size delle idee a convinzione BASSA all'1% del NAV; lo scorecard BASSA (28 esiti) e' descrittivo.",
    '| Gruppo | n | Hit rate | Uso |',
    '| action:BUY | 35 | 54% | solo descrizione |',
    '| confidence:ALTA | 50 | 64% | sizing abilitato |',
    'Il comitato propone di portare la size delle ALTA al 5%, grazie al track record della fascia ALTA (50 esiti).',
    "Alziamo l'esposizione al dollaro al 15% del NAV perche' il campione di dati PMI di settembre indica un rallentamento europeo.",
    "Portiamo BRK.B al 6%: l'esito del buyback (USD 4 mld) e la cassa a USD 325 mld offrono un floor.",
    "Riduciamo XOM al 2% a seguito dell'esito dell'OPEC+ di domenica (aumento della produzione di 400 kb/g).",
    "Raddoppiamo ENEL al 3% del NAV grazie all'esito dell'asta per le rinnovabili (1,2 GW aggiudicati).",
    "Portiamo la posizione BUY su ENEL al 3% grazie all'esito dell'asta per le rinnovabili (1,2 GW aggiudicati).",
    'Il PM ricorda che aumentiamo la size dei BUY al 4% solo dopo 36 esiti: oggi siamo a 35.',
]


REVIEW2_OPERATIVE = [
    "Aumentiamo la size dei BUY al 4% grazie all'hit rate dei BUY (54%, 35 esiti).",
    "Incrementiamo la size delle idee a convinzione MEDIA al 3% perche' il loro hit rate (30 esiti) e' al 60%.",
    'Portiamo la size dei BUY al 4% grazie al track record overall (108 esiti, hit rate 57%).',
    'We size up BUY ideas to 4% given the BUY hit rate of 54% over 35 outcomes.',
    'Alziamo il peso delle idee a convinzione BASSA al 2% (track record BASSA: 28 esiti, 61%).',
    'Raddoppiamo ZZSCA al 6% del NAV: hit rate dei BUY 54% su 35 esiti.',
    '4. **Sizing**: aumentiamo la size dei BUY al 4%, sostenuti dal track record dei BUY (35 esiti, 54%).',
    '| ZZSCA | BUY | 3% -> 5% | Aumentiamo la size dei BUY al 5% grazie al track record dei BUY (35 esiti) |',
    "Dato l'hit rate dei BUY (54%, 35 esiti), portiamo la size dei nuovi BUY al 4%.",
    'Il track record della fascia MEDIA (30 esiti, 63%) giustifica: aumentiamo la size delle idee a convinzione MEDIA al 3%.',
    "Riportiamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti).",
    "La size dei BUY viene portata al 4% grazie all'hit rate dei BUY (54%, 35 esiti).",
    'Aumento della size sui BUY al 4% (hit rate 54%, 35 esiti).',
    'Trim the conviction BASSA bucket to 0,5% of NAV given its track record (28 outcomes, 46%).',
    "Let's size up the BUY bucket to 4% on the back of its 54% hit rate (35 outcomes).",
    "Sovrappesiamo i BUY portando il loro peso al 4% in virtu' del track record dei BUY (35 esiti).",
    'Aumentiamo la size dei BUY al 4% e quella delle ADD al 4% grazie al track record di BUY e ADD (35 e 36 esiti).',
    "Raccomando di aumentare la size dei BUY al 4% alla luce dell'hit rate dei BUY (54% su 35 esiti).",
    'We scale into BUY ideas, raising their sizing to 4%, given the BUY hit rate (35 outcomes).',
    'Alziamo la size delle idee a convinzione MEDIA al 3%, dato il loro track record di 30 esiti al 63%.',
    "Aumentiamo la size dei BUY al 4% grazie all'hit rate del 54% (action:BUY, n=35).",
    'We size up BUY ideas to 4%, as the BUY scorecard (35 outcomes, 54%) supports it.',
]

# Residui DICHIARATI (fuori dalla regola stretta, NOT_ASSESSED): negazione nella
# proposizione ("senza"), verbo che non governa la size ("la cui size sale").
REVIEW2_RESIDUAL = [
    "Aumentiamo la size dei BUY al 4%, senza stravolgere il book, grazie all'hit rate dei BUY (35 esiti).",
    "Ribilanciamo verso i BUY, la cui size sale al 4% grazie all'hit rate di 54% su 35 esiti.",
]


# 30 frasi lecite NUOVE dell'autore v3, in stile memo: citazioni generiche, gruppi
# idonei, limite dichiarato, discorso riportato, separatori, parentesi descrittive.
AUTHOR_V3_LAWFUL = [
    "Aumentiamo la posizione BUY su ASML al 4% perche' il campione di ordini EUV del trimestre (35 macchine) e' solido.",
    "Portiamo la size di ZZSCA al 5% grazie all'esito della trimestrale (ricavi +94% a/a).",
    'Riduciamo il peso di TSLA al 2% dato il track record del CEO sulle consegne (3 guidance mancate su 4).',
    "Alziamo l'esposizione all'oro al 6% del NAV grazie al tasso di successo storico delle coperture in recessione (80%).",
    'Compriamo MSFT al 3% (BUY, convinzione MEDIA, target 520 USD).',
    "Incrementiamo la posizione SELL-side su INTC al 1% perche' l'hit rate dei nuovi nodi produttivi e' al 40%.",
    'Il Red Team osserva che aumentiamo la size dei BUY al 4% grazie a un hit rate del 54% su 35 esiti: obiezione accolta.',
    'Secondo il Red Team portiamo la size dei BUY al 4% grazie al track record dei BUY (35 esiti); non lo facciamo.',
    'Aumentiamo la size delle idee a convinzione ALTA al 5% grazie al track record ALTA (50 esiti), e quella MEDIA (30 esiti) resta al 3%.',
    'Portiamo AMZN al 4% del NAV grazie al track record overall (108 esiti, hit rate 57%).',
    "Aumentiamo la size delle SELL al 3% dato l'hit rate delle SELL (59% su 37 esiti).",
    "Riduciamo la size dei BUY al 2% perche' il campione dei BUY (35 esiti) e' ancora sotto soglia.",
    'Aumentiamo GOOGL al 4%; lo scorecard dei BUY (35 esiti, 54%) resta solo descrittivo.',
    'Teniamo la size dei BUY al 3% del NAV: hit rate dei BUY 54% su 35 esiti, campione insufficiente.',
    'Aumentiamo la size delle ADD al 4% grazie al track record delle ADD (36 esiti), ma i BUY (35 esiti) restano fermi.',
    "Alziamo il peso di XLE al 5% mentre l'hit rate dei BUY (35 esiti) e' al 54%.",
    'Compriamo NEE al 3% del NAV - track record dei BUY 35 esiti, non usato per la size.',
    'The PM notes we raise BUY sizing to 4% given the BUY hit rate (35 outcomes); we decline.',
    'We raise MSFT to 6% of NAV given the 27% free-cash-flow margin and the Azure backlog.',
    'We trim BUY names to 3% as the scorecard for BUY calls (35 outcomes) is too small to rely on.',
    "Accumuliamo ETH al 3% del NAV grazie all'esito positivo dell'upgrade (commissioni -90%).",
    "Riduciamo il peso attribuito all'hit rate dei BUY (54%, 35 esiti) nella scelta delle size.",
    'Portiamo la copertura su SPY al 3% del NAV visto il track record del VIX sopra 25 (12 episodi dal 2008).',
    "Aumentiamo la posizione su ENI al 3% grazie all'esito dell'asta di Mozambico LNG (2 blocchi aggiudicati).",
    '| AAPL | BUY | 4% | hit rate dei BUY 54% (35 esiti) | solo descrizione |',
    'Portiamo la size dei BUY al 4%? Il track record dei BUY ha 35 esiti.',
    'Dato il quadro macro, aumentiamo la duration al 7%: il campione delle recessioni dal 1970 (8 episodi) lo consente.',
    "Raddoppiamo la posizione su UBER al 2% perche' gli outcomes del contenzioso in California (3 su 3 vinti) sono favorevoli.",
    "Scendiamo al 3% su META; il track record dei BUY e' 35 esiti con hit 54%.",
    "Aumentiamo la size delle idee a convinzione ALTA al 5% grazie all'hit rate della fascia ALTA (64% su 50 esiti), con i BUY (35 esiti) solo descritti.",
]


def test_v3_batteries_have_the_declared_size():
    assert len(set(REVIEW2_LAWFUL)) == 67 and len(set(AUTHOR_V3_LAWFUL)) == 30
    assert len(set(REVIEW2_OPERATIVE)) == 22 and len(REVIEW2_RESIDUAL) == 2


@pytest.mark.parametrize('line', REVIEW2_LAWFUL + AUTHOR_V3_LAWFUL)
def test_v3_lawful_prose_is_never_rejected(line):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert result['status'] != 'REJECTED_BINDINGS'
    assert [e for e in result['explicit_prose'] if e['code']] == []


@pytest.mark.parametrize('line', REVIEW2_OPERATIVE)
def test_v3_explicit_numeric_citation_of_small_group_is_rejected(line):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert result['status'] == 'REJECTED_BINDINGS'
    assert [e['code'] for e in result['explicit_prose']] == ['GROUP_NOT_OPERATIONAL']
    assert any(not reviewer_scope()['groups'][k]['operational_eligible']
               for k in result['explicit_prose'][0]['group_ids'])


@pytest.mark.parametrize('line', REVIEW2_RESIDUAL)
def test_v3_declared_residuals_stay_not_assessed(line):
    # If one of these starts being rejected, update the residual list on purpose.
    from bellomberg.core.scorecard_scope_policy import assess
    assert assess(MEMO_VERO + '\n' + line, reviewer_scope())['status'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('line,groups', [
    # generic citation without a group label is NOT a citation (cause A of v2).
    ("Aumentiamo la posizione BUY su TSMC al 3% perche' l'esito delle elezioni a Taiwan resta binario.", None),
    ('Portiamo la posizione BUY su ENEL al 3% grazie all\'esito dell\'asta per le rinnovabili (1,2 GW).', None),
    # numeric citation without a label binds only the group that owns the size.
    ("Aumentiamo la size dei BUY al 4% grazie all'hit rate (35 esiti).", ['action:BUY']),
    ("Aumentiamo ZZSCA al 4% grazie all'hit rate (35 esiti).", None),
    ("Aumentiamo la size dei BUY al 4% grazie a un tasso di successo dell'80% nelle fasi III.", None),
    # v3.1 (D1 terza review): a bare percentage without a label never cites ("del 15% annuo").
    ("Aumentiamo la size dei BUY al 4% dato il loro hit rate del 54%.", None),
    # an id outside the scope is never a group of this check (no UNKNOWN_GROUP from prose).
    ('Aumentiamo la size di ZZSCA al 4% grazie al track record di action:HOLD (n=40).', None),
])
def test_v3_what_counts_as_an_explicit_citation(line, groups):
    from bellomberg.core.scorecard_scope_policy import assess
    found = assess(MEMO_VERO + '\n' + line, reviewer_scope())['explicit_prose']
    assert ([e['group_ids'] for e in found] or None) == (groups and [groups])


# Mutanti M1-M4 della seconda review: ciascuno ha qui il caso che lo uccide.
def test_m1_ma_closes_the_clause():
    from bellomberg.core.scorecard_scope_policy import assess
    line = "Aumentiamo la size delle ADD al 3,5% ma dato l'hit rate dei BUY (35 esiti) la size dei BUY resta al 3%."
    assert assess(MEMO_VERO + '\n' + line, reviewer_scope())['explicit_prose'] == []
    joined = assess(MEMO_VERO + '\n' + line.replace(' ma ', ' e '), reviewer_scope())
    assert joined['status'] == 'REJECTED_BINDINGS'


@pytest.mark.parametrize('size,closed', [(119, True), (120, True), (121, False)])
def test_m2_excerpt_closes_only_the_whole_sentence(size, closed):
    from bellomberg.core.scorecard_scope_policy import _excerpt
    text = 'c' * size
    out = _excerpt(text)
    assert out == ('\u00ab' + text + '\u00bb' if closed else '\u00ab' + text[:120] + '\u2026')


def test_m3_english_contraction_negates():
    from bellomberg.core.scorecard_scope_policy import assess
    line = "Aumentiamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti), which isn't much."
    assert assess(MEMO_VERO + '\n' + line, reviewer_scope())['explicit_prose'] == []
    plain = line.replace(", which isn't much", '')
    assert assess(MEMO_VERO + '\n' + plain, reviewer_scope())['status'] == 'REJECTED_BINDINGS'


def test_m4_third_verb_of_a_clause_is_still_read():
    from bellomberg.core.scorecard_scope_policy import assess, _MAX_VERBS
    assert _MAX_VERBS == 6
    line = ("Compriamo (idea nuova) AAPL, vendiamo (uscita) TSLA e aumentiamo la size dei BUY al 4% "
            "grazie all'hit rate dei BUY (35 esiti).")
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert [(e['group_ids'], e['code']) for e in result['explicit_prose']] == [(['action:BUY'], 'GROUP_NOT_OPERATIONAL')]


@pytest.mark.parametrize('saved_under_v1', ['system', 'scope'])
def test_capo_checkpoint_saved_under_policy_v1_does_not_resume(db, bb, monkeypatch, saved_under_v1):
    # POLICY /2: a Capo request saved with the /1 prompt or the /1 scope projection fails
    # closed before any paid call; the original request stays untouched.
    from bellomberg.core import scorecard_scope_policy as policy
    attach(db, bb)
    calls = _prepara(monkeypatch, lambda *_: _msg(response_for([])))
    capo.run_capo(bb)
    saved = bb.data['_capo_request']
    assert policy.POLICY in saved['system'] and saved['scorecard_scope']['policy'] == policy.POLICY
    if saved_under_v1 == 'system':
        saved['system'] = saved['system'].replace(policy.INSTRUCTIONS, policy.INSTRUCTIONS.replace(
            'capo-scorecard-scope/2', 'capo-scorecard-scope/1'))
        expected = 'Capo checkpoint contract changed'
    else:
        saved['scorecard_scope']['policy'] = 'capo-scorecard-scope/1'
        expected = 'scope checkpoint changed'
    frozen = deepcopy(saved)
    with pytest.raises(ValueError, match=expected):
        capo.run_capo(bb)
    assert len(calls) == 1 and bb.data['_capo_request'] == frozen


@pytest.mark.parametrize('line,rejected', [
    ("Aumentiamo la size dei BUY al 4% grazie all'hit rate dei BUY su 35.", True),
    ("Aumentiamo la size dei BUY al 4% grazie all'hit rate dei BUY nel 2025.", False),
    ('Aumento della size media nel trimestre: hit rate dei BUY 54% su 35 esiti.', False),
    ("Riduciamo la size di ZZSCA al 3% (BUY, hit rate overall 57% su 108 esiti).", False),
])
def test_v3_su_count_nominal_colon_and_comma_edges(line, rejected):
    # "su 35" after a head is a count; a nominal opening is linked only by a parenthesis
    # or a marker, never by a colon; a comma after a lone label closes the chunk.
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert (result['status'] == 'REJECTED_BINDINGS') is rejected


@pytest.mark.parametrize('line', [
    # a citation attributed to somebody else inside the justification does not link.
    "Aumentiamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti) segnalato dal Red Team.",
    # reported speech in an EARLIER clause of the sentence frames the later verb.
    "Il Red Team scrive \u2014 aumentiamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti).",
    # after a colon the citation must open the piece.
    'Aumentiamo la size dei BUY al 4%: oggi hit rate dei BUY 54% su 35 esiti.',
])
def test_v3_attributed_framed_or_late_citations_are_not_assessed(line):
    from bellomberg.core.scorecard_scope_policy import assess
    assert assess(MEMO_VERO + '\n' + line, reviewer_scope())['explicit_prose'] == []
    own = (line.replace(' segnalato dal Red Team', '').replace('Il Red Team scrive \u2014 a', 'A')
           .replace('oggi ', ''))
    assert assess(MEMO_VERO + '\n' + own, reviewer_scope())['status'] == 'REJECTED_BINDINGS'


# v3.1 (10/10, terza review di 6a6a1bb: APPROVATO CON RISERVE, 2/75 falsi positivi).
# Corpus integrale del terzo revisore (scope reviewer_scope): lecite e operative.
REVIEW3_LAWFUL = [
    'Il gruppo BUY conta 35 esiti con hit rate del 54%: dato da leggere, nessuna modifica alla size.',
    'Portiamo ZZSCA al 4% del portafoglio grazie alla revisione al rialzo delle stime di Q3, cresciute del 35%.',
    'Aumentiamo il peso di ASML al 3,5% perché il backlog è salito a 35 miliardi di euro.',
    'Riduciamo la posizione in Intel al 2% visto il calo del margine lordo al 35%.',
    'La convinzione MEDIA su Adobe, con upside del 35%, porta il comitato a mantenere la size invariata al 2%.',
    'Portiamo Adobe al 2,5% (convinzione MEDIA, upside 35%).',
    'Portiamo Adobe al 2,5% (convinzione MEDIA, 35% di upside al target).',
    'Portiamo Adobe al 2,5% (convinzione MEDIA su 35 dollari di target).',
    'Aumentiamo la size di Uber al 3% dato il BUY rating del broker con target a 35 dollari.',
    "Raccomando di aumentare l'esposizione ai semiconduttori perché il buy-side è sottopesato del 35% rispetto al benchmark.",
    'We increase the position in MSFT to 5% on the back of 35% cloud growth in Q3.',
    'We trim the stake in Tesla to 1% given Q3 auto gross margin of 35%.',
    'The SELL hit rate of 62% over 37 outcomes supports trimming the position in Intel to 1%.',
    "Riduciamo la size di Intel all'1% grazie al track record dei SELL (37 esiti, hit rate 62%).",
    'Aumentiamo il peso delle idee a convinzione ALTA al 5% grazie al loro hit rate del 58% su 50 esiti.',
    'Alziamo la size media al 3% sulla base del track record overall (108 esiti, hit rate 55%).',
    'Il Red Team sostiene che dovremmo aumentare la size dei BUY grazie al loro hit rate (35 esiti).',
    '«Aumentiamo la size dei BUY dato il track record BUY (35 esiti)», scrive il Red Team: il comitato non lo segue.',
    'Red Team: aumentiamo la size dei BUY grazie al loro track record di 35 esiti.',
    'Il gruppo BUY (35 esiti, hit rate 54%) resta sotto la soglia di 36: lo trattiamo solo come descrizione.',
    'Con 35 esiti il campione BUY non basta per modificare il sizing.',
    'Nel Q3 i BUY hanno reso il 35%, ma manteniamo la size invariata.',
    'Manteniamo la posizione su META al 4%: il track record ALTA (50 esiti, 58%) è coerente con la tesi.',
    'Aumentiamo la posizione su META al 4%: il track record ALTA (50 esiti, 58%) è coerente con la tesi.',
    'Aumentiamo la posizione su META al 4%: catalizzatori di Q3 e margine operativo al 35%.',
    '| Azione | Size | Motivo (scorecard) |',
    '|---|---|---|',
    '| Aumentiamo META | 4% | track record ALTA: 50 esiti, 58% |',
    '| Riduciamo INTC | 1% | hit rate SELL 62% su 37 esiti |',
    '## Sizing',
    '- ZZSCA: portiamo il peso al 4% dopo la guidance di Q3 (+35% di ricavi data center).',
    '- ADBE: convinzione MEDIA, size invariata al 2%, upside 35%.',
    '- UBER: aumentiamo la size al 3% visto il prezzo a 35 dollari, sotto il fair value.',
    'Se il gruppo BUY superasse i 36 esiti, aumenteremmo la size.',
    'Qualora i BUY raggiungano 36 esiti, valuteremo un aumento della size.',
    'Il track record dei BUY, 35 esiti e hit rate del 54%, è riportato in appendice per completezza.',
    'Abbiamo 35 trade chiusi sui BUY e 36 sugli ADD; la size resta guidata dai fondamentali.',
    'Il comitato aumenta la size di AMZN al 3% grazie ai risultati di Q3, con ricavi AWS in crescita del 35%.',
    'La size di AMZN viene portata al 3% alla luce dei risultati di Q3 (AWS +35%).',
    'The position in AVGO is increased to 4% in light of the 35% rise in AI revenue.',
    'The position in AVGO is increased to 4% owing to strong Q3 results and a 35% backlog expansion.',
    'We add to BUY-rated names only where fundamentals support it, sizing each at 2%.',
    'We overweight semis at 6% given the broker BUY rating on 35 names in the sector.',
    'Il nostro hit rate complessivo (overall, 108 esiti, 55%) giustifica di mantenere la struttura del book.',
    'Proponiamo di portare TSM al 3% sulla base del track record overall: 108 esiti, 55%.',
    "Portiamo la posizione in Ferrari al 2% perché la convinzione è MEDIA e l'upside al target di 350 euro è del 35%.",
    'Aumentiamo la size di Hyperliquid al 3% grazie al rafforzamento dei volumi on-chain, cresciuti del 35% in Q3.',
    'Portiamo la posizione in ZZSCC al 6% vista la convinzione ALTA e uno sconto al NAV del 35%.',
    "Riduciamo la size dei titoli a convinzione BASSA all'1% perché la liquidità media scende a 35 giorni di copertura.",
    'Aumentiamo la size dei BUY al 3% perché il loro upside medio è del 35%.',
    'Aumentiamo il peso di Rheinmetall al 3% perché il campione di 35 ordini NATO conferma la domanda.',
    'Aumentiamo la size dei BUY sulla difesa al 3% perché il campione su 35 commesse NATO conferma la domanda.',
    'Portiamo la size dei BUY sugli asset manager (Brookfield, KKR) al 4% grazie al loro track record del 15% annuo nel private equity.',
    'Aumentiamo la size delle idee BUY nei media al 3% grazie ai loro hit al box office (+35% su base annua).',
    'We increase the position in Apple to 4% given the overall market share of 35% in premium handsets.',
    'We increase BUY sizing to 4% given overall Q3 earnings growth of 35%.',
    'The BUY bucket shows 35 outcomes and a 54% hit rate; we keep sizing unchanged.',
    'Il track record BUY conta 35 esiti; aumentiamo la size di ZZSCA al 4% grazie alla guidance.',
    'Aumentiamo la size di ZZSCA al 4% grazie alla guidance; il track record BUY conta 35 esiti.',
    'Aumentiamo la size di ZZSCA al 4% grazie alla guidance, e il track record BUY (35 esiti) resta sullo sfondo.',
    'Aumentiamo la size degli ADD al 3% grazie al track record degli ADD (36 esiti, 57%).',
    'Aumentiamo la size degli ADD al 3% grazie al track record ADD e SELL (36 e 37 esiti).',
    'Alla luce di un trimestre con ricavi in crescita del 35%, portiamo la posizione in Nvidia al 5%, il massimo consentito dal mandato.',
    'Mentre il track record dei BUY (35 esiti) resta da leggere con prudenza, alziamo la size di Meta al 4% per la crescita del 35% degli utili.',
    'Dato il prezzo di 35 dollari, sotto il nostro fair value di 48, aumentiamo la size di Uber al 3%.',
    'Visto il Q3 con margini al 35%, aumentiamo il peso di Ferrari al 2,5%.',
    'In light of the 35% drawdown since March, we cut the position in Hyperliquid to 2%.',
    'Supported by a 35% free cash flow margin, we raise the weight of Visa to 4%.',
    'We raise the position in LVMH to 3% because Q3 organic growth of 35% in Japan beat consensus.',
    'Raddoppiamo la posizione in ZZSCC al 6% grazie allo sconto sul NAV del 35%, il più ampio dal 2020.',
    'Aumentiamo la size di Brookfield al 4% grazie al track record del management, 35 anni di compounding al 15%.',
    'Portiamo il peso di ENI al 2% (dividend yield 6%, 35% di payout sui flussi).',
    'Il Red Team propone: «portiamo la size dei BUY al 4% dato il loro hit rate del 54% su 35 esiti»; respinto.',
    'Secondo il Red Team aumentiamo la size dei BUY grazie al track record BUY (35 esiti), ma il comitato dissente.',
    "Tagliamo la posizione in Intel all'1% perché il track record SELL (37 esiti, 62%) supera la soglia e lo usiamo.",
]
REVIEW3_OPERATIVE = [
    'We raise the BUY weight to 4%, supported by the BUY hit rate of 54% (35 outcomes).',
    'Supported by the BUY track record (n = 35), we raise the weight of BUY ideas to 4%.',
    'In light of the BUY hit rate (54%, n=35) we lift BUY weights to 4%.',
    'Given n = 35 BUY outcomes, we increase BUY sizing to 4%.',
    'Dato il track record BUY (35/36 esiti), alziamo la size dei BUY al 3%.',
    'Portiamo i BUY al 3% di peso: hit rate BUY 54% su 35 esiti.',
    '- BUY: aumentiamo la size al 3% grazie al track record BUY (35 esiti).',
    '- Aumento della size dei BUY al 3% (track record BUY: 35 esiti, 54%).',
    'Raccomando di portare la size dei BUY al 3% grazie al loro hit rate del 54% su 35 esiti.',
    "Let's raise the weight of MEDIA-conviction ideas to 3% because the MEDIA bucket has 30 outcomes at 57%.",
    'Dimezziamo la size delle idee a convinzione BASSA, sulla base del loro hit rate del 39% (28 esiti).',
    'La size dei BUY sarà portata al 3%, dato il track record BUY di 35 esiti.',
    "Incrementiamo l'esposizione BUY al 4% perché n=35 sui BUY è il campione più ampio dopo gli ADD.",
    'Aumentiamo la size delle idee a convinzione MEDIA al 3% visto il tasso di successo MEDIA (30 osservazioni, 57 %).',
    'Aumentiamo il peso dei BUY al 3% grazie a un hit rate del 54% su 35 osservazioni.',
    'Aumentiamo la size dei BUY al 3%: il loro track record conta 35 esiti.',
    'Raddoppiamo il peso degli ADD e dei BUY al 4% grazie al track record ADD/BUY (36/35 esiti).',
    'Aumentiamo la size degli ADD grazie al track record ADD e BUY (36 e 35 esiti).',
    'We increase BUY sizing to 4% given the overall hit rate of 55% on 108 outcomes.',
    'The BUY sizing is increased to 4% on the strength of the BUY hit rate (35 outcomes).',
    'Owing to the BUY track record (35 outcomes), we increase the BUY position to 4%.',
    'Visto che i BUY hanno 35 esiti con il 54% di hit rate, aumentiamo la size dei BUY al 4%.',
    'Sovrappesiamo i BUY al 3% (track record: 35 trade chiusi, hit rate 54 %).',
]
# Residui dichiarati (NOT_ASSESSED): verbo "stands at" spezza il blocco; riga di tabella.
REVIEW3_RESIDUAL = [
    'We are increasing BUY sizing to 4% because the BUY hit rate stands at 54% across 35 outcomes.',
    '| Aumentiamo la size dei BUY | 3% | hit rate BUY 54% (35 esiti) |',
]
# D1: una citazione SENZA etichetta vale solo con testata piena + nome di conteggio
# accanto al numero. Sonde del revisore e frasi dell'autore: mai respinte.
D1_LAWFUL = [
    'We trim BUY positions to 2% given their 35% hit from tariffs.',
    'Aumentiamo la size delle idee BUY su Berkshire al 4% grazie al track record su 35 anni di Buffett.',
    'Aumentiamo la size dei BUY su Eli Lilly al 3% grazie al loro hit rate del 70% nelle fasi III.',
    'Aumentiamo il peso dei BUY su Novo Nordisk al 3% perché il campione su 35 pazienti della fase III è solido.',
    'Portiamo la size delle idee BUY sul lusso al 4% dato il loro track record del 12% annuo di crescita organica.',
    'We raise BUY sizing in pharma to 3% because their hit rate of 60% in phase III trials is best in class.',
    'Aumentiamo la size dei BUY su Ferrari al 3% visto il campione del mondo con il 35% di quota.',
    'Aumentiamo la size dei BUY sugli asset manager al 4% grazie al loro track record su 35 operazioni di M&A.',
    'We cut BUY positions in airlines to 1% due to their track record of 35% fuel cost overruns.',
    'We increase BUY sizing in defense to 4% on the strength of their hit rate (35 contracts won).',
    'Aumentiamo la size dei BUY sul lusso al 3% forte del loro track record di 20 anni.',
    'We are increasing BUY sizing in chips to 4% owing to the 35% hit rate on tariff exemptions.',
    "We're trimming BUY positions to 2% due to the Fed outcome.",
    'Dato che il track record del CEO conta 35 acquisizioni, aumentiamo la size dei BUY su DHR al 3%.',
    'Owing to the hit rate of 70% in phase III (35 trials), we raise BUY sizing in biotech to 3%.',
    'Aumentiamo la size dei BUY al 3% in forza del contratto da 35 miliardi.',
    "Portiamo la size dei BUY al 3% visto che il campione su 35 pazienti e' solido.",
    'Aumentiamo la size dei BUY al 3% sulla scorta del campione su 35 pazienti.',
    'We increase BUY sizing to 3% in view of the hit rate on 35 drilling wells.',
    'Aumentiamo la size dei BUY al 3% grazie al campione (35 esiti) delle vendite al dettaglio.',
    'Aumentiamo la size dei BUY al 3% grazie al loro hit su 35 brevetti.',
    'We raise BUY sizing to 3% considering their track record over 35 years.',
    "Le osservazioni del Red Team: aumentiamo la size dei BUY al 4% grazie all'hit rate dei BUY (35 esiti).",
    "Aumentiamo la size dei BUY al 3% perche' il loro hit rate del 54% e' il migliore del settore.",
    'Aumentiamo la size delle ADD al 4% sulla scorta del track record delle ADD (36 operazioni chiuse).',
    'We are increasing ADD sizing to 4% given the ADD track record (36 outcomes).',
    "Aumentiamo la size dei BUY al 3% dato che il track record su 35 anni del gestore e' solido.",
]
# D2 (osservazioni conta come conteggio), D3 (legami, we are -ing, visto/dato che,
# trade chiusi/operazioni) e la finestra M4: respinte.
V31_OPERATIVE = [
    'Dato il track record BUY (35 osservazioni), aumentiamo la size dei BUY al 4%.',
    'Aumentiamo la size dei BUY al 4% grazie al track record BUY (35 osservazioni).',
    'Aumentiamo la size dei BUY al 4% grazie al track record BUY (35 observations).',
    'Aumentiamo la size dei BUY al 4% grazie al track record BUY (35 trade chiusi).',
    'Aumentiamo la size dei BUY al 4% grazie al track record BUY (35 operazioni chiuse).',
    'Aumentiamo la size dei BUY al 4% sulla scorta del track record BUY (35 esiti).',
    'Aumentiamo la size dei BUY al 4% forte del track record BUY (35 esiti).',
    'Aumentiamo la size dei BUY al 4% in forza del track record BUY (35 esiti).',
    'We increase BUY sizing to 4% on the strength of the BUY track record (35 outcomes).',
    'We increase BUY sizing to 4% owing to the BUY track record (35 outcomes).',
    'We increase BUY sizing to 4% due to the BUY track record (35 outcomes).',
    'We increase BUY sizing to 4% in view of the BUY track record (35 outcomes).',
    'We increase BUY sizing to 4% considering the BUY track record (35 outcomes).',
    'We are increasing BUY sizing to 4% given the BUY track record (35 outcomes).',
    'We will increase BUY sizing to 4% given the BUY track record (35 outcomes).',
    "We're increasing BUY sizing to 4% given the BUY track record (35 outcomes).",
    'Aumentiamo la size dei BUY al 4% perché i BUY contano 35 esiti con il 54%.',
    'Aumentiamo la size dei BUY al 4% grazie al 54% di hit rate dei BUY su 35 esiti.',
    'Aumentiamo la size dei BUY al 4% grazie al track record dei BUY su 35 operazioni.',
    'Aumentiamo la size dei BUY al 4% grazie al loro track record (35 esiti).',
    'Aumentiamo la size dei BUY al 4% grazie al loro track record su 35 esiti.',
    'We raise BUY sizing to 4% given their hit rate (n=35).',
    "Aumentiamo la size dei BUY al 4% grazie all'hit rate dei BUY su 35.",
    'Aumentiamo la size delle idee a convinzione BASSA al 2% sulla base del track record delle nostre idee a convinzione BASSA e MEDIA (28 e 30 esiti).',
]
# Residui D3 dichiarati: nomi di conteggio fuori elenco.
V31_RESIDUAL = [
    'Aumentiamo la size dei BUY al 4% grazie al track record BUY (35 segnali).',
    'Aumentiamo la size dei BUY al 4% grazie al track record BUY di 35 chiamate.',
]


def test_v31_batteries_have_the_declared_size():
    assert len(set(REVIEW3_LAWFUL)) == 75 and len(set(REVIEW3_OPERATIVE)) == 23
    assert len(set(D1_LAWFUL)) == 27 and len(set(V31_OPERATIVE)) == 24


@pytest.mark.parametrize('line', REVIEW3_LAWFUL + D1_LAWFUL)
def test_v31_lawful_prose_is_never_rejected(line):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert result['status'] != 'REJECTED_BINDINGS'
    assert [e for e in result['explicit_prose'] if e['code']] == []


@pytest.mark.parametrize('line', REVIEW3_OPERATIVE + V31_OPERATIVE)
def test_v31_explicit_citation_of_small_group_is_rejected(line):
    from bellomberg.core.scorecard_scope_policy import assess
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert result['status'] == 'REJECTED_BINDINGS'
    assert any(not reviewer_scope()['groups'][k]['operational_eligible']
               for e in result['explicit_prose'] for k in e['group_ids'])


@pytest.mark.parametrize('line', REVIEW3_RESIDUAL + V31_RESIDUAL)
def test_v31_declared_residuals_stay_not_assessed(line):
    # If one of these starts being rejected, update the residual list on purpose.
    from bellomberg.core.scorecard_scope_policy import assess
    assert assess(MEMO_VERO + '\n' + line, reviewer_scope())['status'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('line,groups', [
    # D1: without a label only a full head + a named count beside the number binds the size owner.
    ("Aumentiamo la size dei BUY al 4% grazie al loro track record (35 esiti).", ['action:BUY']),
    ("Aumentiamo la size dei BUY al 4% grazie al loro hit rate (n=35).", ['action:BUY']),
    ("Aumentiamo la size dei BUY al 4% grazie al loro track record (35 trade chiusi).", ['action:BUY']),
    ("Aumentiamo la size dei BUY al 4% grazie al loro track record del 54%.", None),
    ("Aumentiamo la size dei BUY al 4% grazie al loro track record su 35 anni.", None),
    ("Aumentiamo la size dei BUY al 4% grazie al campione (35 esiti).", None),
    ("Aumentiamo la size dei BUY al 4% grazie al loro hit (35 esiti).", None),
    ("Aumentiamo la size dei BUY al 4% grazie al loro track record su 35 operazioni.", None),
    # with a label the weak heads and "operazioni" still cite.
    ("Aumentiamo la size dei BUY al 4% grazie al campione BUY (35 esiti).", ['action:BUY']),
    ("Aumentiamo la size dei BUY al 4% grazie al track record dei BUY su 35 operazioni.", ['action:BUY']),
    ("Aumentiamo la size dei BUY al 4% grazie al track record dei BUY su 35 commesse.", None),
    # D2: the noun "osservazioni" is a count; the verbs still frame reported speech.
    ("Aumentiamo la size dei BUY al 4% grazie al track record BUY (35 osservazioni).", ['action:BUY']),
    ("Il PM osserva che aumentiamo la size dei BUY al 4% grazie al track record BUY (35 esiti).", None),
    ("Osserviamo che aumentiamo la size dei BUY al 4% grazie al track record BUY (35 esiti).", None),
    ("Aumentiamo la size dei BUY al 4% grazie al track record BUY (35 esiti) che il Red Team osserva.", None),
])
def test_v31_d1_d2_what_counts_as_a_citation(line, groups):
    from bellomberg.core.scorecard_scope_policy import assess
    found = assess(MEMO_VERO + '\n' + line, reviewer_scope())['explicit_prose']
    assert ([e['group_ids'] for e in found] or None) == (groups and [groups])


def test_m4_citation_window_reaches_a_long_coordinated_label_list():
    # Third review M4: with a 60-character window the count "(28 e 30 esiti)" falls outside.
    from bellomberg.core.scorecard_scope_policy import assess
    line = ("Aumentiamo la size delle idee a convinzione BASSA al 2% sulla base del track record delle "
            "nostre idee a convinzione BASSA e MEDIA (28 e 30 esiti).")
    result = assess(MEMO_VERO + '\n' + line, reviewer_scope())
    assert result['status'] == 'REJECTED_BINDINGS'
    assert result['explicit_prose'][0]['group_ids'] == ['confidence:BASSA', 'confidence:MEDIA']
