"""T8: native frozen Red Team request, explicit review, no real provider/storage."""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from test_tetto_specialisti_16k import bb
from test_weekly_memory_memo15 import db
from test_evidence_prompt_policy_memo15 import actor
from bellomberg.agents import red_team
from bellomberg.agents.weekly_lifecycle import bind_blackboard
from bellomberg.core import evidence_followup_policy as followup, llm_client
from bellomberg.core.evidence_prompt_policy import select_template
from bellomberg.agents.specialists.eventdesk import EventDeskSpecialist
from bellomberg.storage.weekly_run_store import WeeklyRunStore, WeeklyRunBlocked


MISSING = object()


def attach(board, db, marker=followup.POLICY):
    contract = {'evidence_prompt_policy': 'weekly-evidence-prompts/1', 'roster': [], 'r2_specialists': []}
    if marker is not MISSING:
        contract.update({followup.KEY: marker, followup.AS_OF_KEY: '2032-01-02'})
    store = WeeklyRunStore(db, db.save_memo('[IN PROGRESS]'), context={
        'contract_version': 1, 'contract': contract, 'language': 'it',
        'research_started_at': '2032-01-02T10:00:00+00:00', 'portfolio': {'positions': []}})
    board.run_scope = 'weekly'
    bind_blackboard(board, store)
    store.complete('priming', {})
    board.tool_receipts = [{'tool': 'get_financial_history', 'input': {'ticker': 'SYNTH'},
        'run_id': store.run_id, 'round': 1, 'success': True, 'truncated': False,
        'timestamp': '2032-01-01T09:00:00+00:00', 'output': json.dumps({
            'revenue': 731, 'period_start': '2031-01-01', 'period_end': '2031-12-31',
            'duration': 'annual', 'fiscal_year_label': 'FY2031', 'unit': 'million',
            'currency': 'EUR', 'definition': 'reported_revenue',
            'issuer_identity': {'status': 'PRIMARY_SOURCE_VERIFIED', 'ticker': 'SYNTH', 'name': 'Synthetic Issuer',
                                'basis': 'synthetic filing'}})}]
    board.write('fundamentals', 1, 'SYNTH: ricavi da verificare per periodo, identita e valuta.')
    return store


def claim(board, **changes):
    fact = followup.project_receipts(board.tool_receipts, run_id=board.weekly_store.run_id)['facts'][0]
    return {key: deepcopy(fact[key]) for key in (
        'source_receipt', 'metric', 'value', 'ticker', 'issuer_name', 'period_start', 'period_end',
        'duration', 'fiscal_year_label', 'unit', 'currency', 'definition', 'observed_at')} | changes


def response(claims):
    return 'Analisi sintetica circoscritta alle fonti ricevute.\n```evidence_review\n' + json.dumps({'claims': claims}) + '\n```'


def client(monkeypatch, text):
    sent = []
    def send(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'id': 'synthetic-red-t8', 'model': 'synthetic/model',
            'choices': [{'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 37, 'completion_tokens': 19, 'cost': .002}})
    sdk = llm_client.OpenRouterClient(api_key='offline-test', max_retries=0, trasporto=httpx.MockTransport(send))
    monkeypatch.setattr(llm_client, 'OpenRouterClient', lambda **kw: sdk)
    monkeypatch.setattr(llm_client, 'modello', lambda *a: 'synthetic/model')
    monkeypatch.setattr(red_team, '_preventivo_prima_dell_invio', lambda *a: None)
    return sent


@pytest.mark.parametrize('case,expected', [('missing', 'UNVERIFIED'), ('correct', 'CONSISTENT_EXPLICIT'),
                                        ('different', 'CONTRADICTION_EXPLICIT')])
def test_native_red_review_distinguishes_missing_correct_and_explicit_contradiction(bb, db, monkeypatch, case, expected):
    attach(bb, db)
    row = claim(bb)
    if case == 'missing': row.pop('source_receipt')
    if case == 'different': row['value'] = 932
    raw = response([row])
    sent = client(monkeypatch, raw)
    result = red_team.run_red_team(bb)
    assert len(sent) == 1
    wire = str(sent[0])
    assert 'EVIDENZA DELLA RUN: METADATI E LACUNE' in wire
    assert 'reported_revenue' in wire and 'SYNTH' in wire and 'evidence_review' in wire
    checkpoint = bb.specialist_checkpoints['red_team:R1']
    review = checkpoint['evidence_review']
    assert checkpoint['critique_raw'] == raw and raw in result
    assert review['claims'][0]['status'] == expected
    assert review['semantic_scope'] == 'NOT_ASSESSED' and review['policy'] == followup.POLICY
    assert review['response_sha256'] and review['evidence_sha256']
    assert 'PASS' not in json.dumps(review)
    assert expected in result
    if case == 'correct':
        assert 'CONTRADICTION' not in result and 'UNVERIFIED' not in json.dumps(review['claims'])


@pytest.mark.parametrize('raw,expected', [('Confermo tutto senza ulteriori dettagli.', 'NOT_ASSESSED'),
    ('```evidence_review\nnot json\n```', 'UNVERIFIED'),
    ('```evidence_review\n{"claims": "wrong type"}\n```', 'UNVERIFIED')])
def test_unknown_or_malformed_review_never_becomes_universal_pass(bb, db, monkeypatch, raw, expected):
    attach(bb, db); client(monkeypatch, raw)
    red_team.run_red_team(bb)
    review = bb.specialist_checkpoints['red_team:R1']['evidence_review']
    assert review['status'] == expected and 'PASS' not in json.dumps(review)


@pytest.mark.parametrize('marker', [None, '', {}, 'weekly-evidence-followup/999'])
def test_invalid_followup_marker_fails_before_best_effort_client(bb, db, monkeypatch, marker):
    attach(bb, db, marker); sent = client(monkeypatch, 'must not send')
    with pytest.raises(WeeklyRunBlocked): red_team.run_red_team(bb)
    assert sent == []


def test_eventdesk_native_request_uses_new_rules_only_for_new_marker(bb, db, monkeypatch):
    attach(bb, db); desk, capture = actor(bb, monkeypatch)
    desk._run_loop(1)
    text = str(capture.calls[0]['system'])
    assert 'count tecnico' in text and 'candidati incerti' in text and 'PRICING NON VALUTABILE' in text
    legacy_board = SimpleNamespace(run_scope='weekly', weekly_store=SimpleNamespace(context={
        'contract': {'evidence_prompt_policy': 'weekly-evidence-prompts/1'}}))
    legacy = select_template(legacy_board, 'eventdesk', EventDeskSpecialist.system_prompt)
    assert 'count tecnico' not in legacy and 'candidati incerti' not in legacy


def test_complete_checkpoint_replays_saved_review_without_reading_live_receipts(bb, db, monkeypatch):
    store = attach(bb, db); raw = response([claim(bb)]); sent = client(monkeypatch, raw)
    first = red_team.run_red_team(bb); frozen = deepcopy(bb.specialist_checkpoints)
    bind_blackboard(bb, WeeklyRunStore(db, store.memo_id))
    bb.tool_receipts = [{'changed': 'must not read'}]
    monkeypatch.setattr(followup, 'followup_block', lambda *_: pytest.fail('saved evidence rebuilt'))
    assert red_team.run_red_team(bb) == first and len(sent) == 1
    assert bb.specialist_checkpoints == frozen


@pytest.mark.parametrize('change', [
    {'issuer_name': None}, {'observed_at': None}, {'currency': None}, {'period_end': None},
    {'method': 'invented method'}, {'value': True}, {'value': float('inf')}, {'unknown': 'unsupported'},
])
def test_missing_dimensions_or_unsupported_claims_never_imply_numeric_clearance(bb, db, monkeypatch, change):
    attach(bb, db); row = claim(bb, **change)
    raw = response([row]).replace('Infinity', '1e999')
    client(monkeypatch, raw); red_team.run_red_team(bb)
    review = bb.specialist_checkpoints['red_team:R1']['evidence_review']
    assert review['status'] == 'UNVERIFIED'
    assert 'CONTRADICTION_EXPLICIT' not in json.dumps(review)


@pytest.mark.parametrize('field,value', [('period_end', '2030-12-31'), ('currency', 'USD'),
                                       ('issuer_name', 'Another Issuer'), ('ticker', 'OTHER')])
def test_same_number_does_not_clear_wrong_period_identity_or_currency(bb, db, monkeypatch, field, value):
    attach(bb, db); client(monkeypatch, response([claim(bb, **{field: value})]))
    red_team.run_red_team(bb)
    finding = bb.specialist_checkpoints['red_team:R1']['evidence_review']['claims'][0]
    assert finding['status'] == 'CONTRADICTION_EXPLICIT'
    assert finding['contradicted_fields'] == [field]


def test_reference_types_are_exact_and_no_boolean_index_is_accepted(bb, db, monkeypatch):
    attach(bb, db); row = claim(bb); row['source_receipt']['index'] = False
    client(monkeypatch, response([row])); red_team.run_red_team(bb)
    assert bb.specialist_checkpoints['red_team:R1']['evidence_review']['status'] == 'UNVERIFIED'


def test_native_metadata_paths_are_copied_and_compared_in_full(bb, db, monkeypatch):
    attach(bb, db)
    payload = json.loads(bb.tool_receipts[0]['output'])
    payload['metric_metadata'] = {'revenue': {'currency': 'EUR', 'unit': 'million'}}
    bb.tool_receipts[0]['output'] = json.dumps(payload)
    row = claim(bb)
    assert row['source_receipt']['metadata_paths'] == ['', '/metric_metadata/revenue']
    sent = client(monkeypatch, response([row]))
    red_team.run_red_team(bb)
    assert bb.specialist_checkpoints['red_team:R1']['evidence_review']['status'] == 'ASSESSED_EXPLICIT'
    assert 'metadata_paths' in str(sent[0])


@pytest.mark.parametrize('change', [
    {'metadata_paths': ['/foreign']}, {'metadata_paths': []}, {'metadata_paths': None},
    {'metadata_paths': ''}, {'metadata_paths': ['', '']}, {'metadata_paths': [False]},
    {'metadata_paths': ['not-a-pointer']}, {'metadata_paths': ['/bad~escape']},
    {'future_scope': ['']}, {'sha256': '0' * 64}, {'path': '/other'},
])
def test_native_changed_or_unsupported_metadata_reference_stays_unverified(bb, db, monkeypatch, change):
    attach(bb, db)
    row = claim(bb)
    row['source_receipt'].update(change)
    client(monkeypatch, response([row]))
    red_team.run_red_team(bb)
    finding = bb.specialist_checkpoints['red_team:R1']['evidence_review']['claims'][0]
    assert finding['status'] == 'UNVERIFIED'
    assert finding['reasons'] == ['SOURCE_RECEIPT_UNVERIFIED']


def test_four_field_historical_reference_matches_only_the_historical_snapshot(bb, db):
    attach(bb, db)
    current = followup.project_receipts(bb.tool_receipts, run_id=bb.weekly_store.run_id)
    row = claim(bb)
    row['source_receipt'].pop('metadata_paths')
    assert red_team._review_evidence_claims(response([row]), current)['status'] == 'UNVERIFIED'
    historical = deepcopy(current)
    historical['facts'][0]['source_receipt'].pop('metadata_paths')
    assert red_team._review_evidence_claims(response([row]), historical)['status'] == 'ASSESSED_EXPLICIT'


@pytest.mark.parametrize('paths', [[], None, '', ['', ''], [False], ['not-a-pointer'], ['/bad~escape']])
def test_invalid_metadata_paths_are_not_attested_even_if_snapshot_matches(bb, db, paths):
    attach(bb, db)
    evidence = followup.project_receipts(bb.tool_receipts, run_id=bb.weekly_store.run_id)
    row = claim(bb)
    row['source_receipt']['metadata_paths'] = paths
    evidence['facts'][0]['source_receipt']['metadata_paths'] = paths
    finding = red_team._review_evidence_claims(response([row]), evidence)['claims'][0]
    assert finding['status'] == 'UNVERIFIED'
    assert finding['reasons'] == ['SOURCE_RECEIPT_UNVERIFIED']


def test_unattested_identity_does_not_become_verified_by_metadata_paths(bb, db, monkeypatch):
    attach(bb, db)
    payload = json.loads(bb.tool_receipts[0]['output'])
    payload['issuer_identity'].pop('ticker')
    bb.tool_receipts[0]['output'] = json.dumps(payload)
    client(monkeypatch, response([claim(bb)]))
    red_team.run_red_team(bb)
    finding = bb.specialist_checkpoints['red_team:R1']['evidence_review']['claims'][0]
    assert finding['status'] == 'UNVERIFIED'
    assert 'MISSING_OR_UNSUPPORTED:identity_basis' in finding['reasons']


def test_paid_reply_after_crash_uses_exact_body_receipts_and_cost(bb, db, monkeypatch, tmp_path):
    from bellomberg.core.request_journal import RequestJournal
    store = attach(bb, db); raw = response([claim(bb)]); sent = client(monkeypatch, raw)
    def journal():
        return RequestJournal(tmp_path / 'requests.sqlite', run_id=store.run_id, authorized_usd=10,
            authorization={'source': 'offline-test'}, metadata=lambda model: {
                'id': model, 'context_length': 400000, 'top_provider': {'max_completion_tokens': 128000},
                'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})
    class Crash(BaseException): pass
    persist = bb.persist_run_checkpoint
    def crash(event, payload):
        if event == 'red_team_report': raise Crash()
        return persist(event, payload)
    bb.persist_run_checkpoint = crash
    with llm_client.request_scope(journal(), phase='red_team'):
        with pytest.raises(Crash): red_team.run_red_team(bb)
    assert len(sent) == 1
    with journal()._db() as conn: before = [dict(row) for row in conn.execute('SELECT * FROM requests')]
    bind_blackboard(bb, WeeklyRunStore(db, store.memo_id))
    frozen = deepcopy(bb.specialist_checkpoints['red_team:R1'])
    bb.tool_receipts = [{'changed': 'must not read'}]
    monkeypatch.setattr(followup, 'followup_block', lambda *_: pytest.fail('paid input rebuilt'))
    monkeypatch.setattr(followup, 'project_receipts', lambda *a, **k: pytest.fail('paid evidence rebuilt'))
    with llm_client.request_scope(journal(), phase='red_team'):
        result = red_team.run_red_team(bb)
    assert len(sent) == 1 and raw in result and 'CONSISTENT_EXPLICIT' in result
    after_checkpoint = bb.specialist_checkpoints['red_team:R1']
    assert after_checkpoint['evidence_followup_snapshot'] == frozen['evidence_followup_snapshot']
    assert after_checkpoint['user_msg'] == frozen['user_msg']
    with journal()._db() as conn: after = [dict(row) for row in conn.execute('SELECT * FROM requests')]
    assert after == before
