"""Research notes: identity, frozen selection and native delivery (synthetic SQLite only)."""
from contextlib import contextmanager
from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.core import current_facts as facts
from bellomberg.storage import memory_db
from bellomberg.storage.weekly_run_store import WeeklyRunStore, WeeklyRunBlocked


@pytest.fixture
def notes_db(tmp_path, monkeypatch):
    path = str(tmp_path / 'notes.sqlite')
    monkeypatch.setattr(memory_db, 'SQLITE_PATH', path)
    monkeypatch.setattr(facts, '_RESEARCH_CACHE', {'text': None, 'ts': 0.0})
    db = memory_db.MemoryDB.__new__(memory_db.MemoryDB)
    db.db_path = path
    @contextmanager
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()
    db._conn = connect
    with connect() as conn:
        conn.executescript('''
        CREATE TABLE decisions(id INTEGER PRIMARY KEY, ticker TEXT, timestamp TEXT,
          memo_id INTEGER, action TEXT DEFAULT 'RESEARCH', status TEXT DEFAULT 'PENDING',
          timing TEXT DEFAULT '', rationale TEXT DEFAULT '', veto INTEGER DEFAULT 0,
          veto_revoked_at TEXT, archive_override INTEGER);
        CREATE TABLE decision_notes(id INTEGER PRIMARY KEY, decision_id INTEGER, autore TEXT,
          testo TEXT, timestamp TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE memos(id INTEGER PRIMARY KEY, notes TEXT);
        INSERT INTO memos VALUES(1,'synthetic');
        ''')
    return db


def decision(db, ident, ticker='ZZTEST', *, status='PENDING', timestamp='2026-01-01 09:00:00', veto=0, revoked=None):
    with db._conn() as conn:
        conn.execute('INSERT INTO decisions(id,ticker,timestamp,status,veto,veto_revoked_at) VALUES(?,?,?,?,?,?)',
                     (ident,ticker,timestamp,status,veto,revoked))


def note(db, ident, did, text, author='PM', timestamp='2026-01-02 09:00:00'):
    with db._conn() as conn:
        conn.execute('INSERT INTO decision_notes VALUES(?,?,?,?,?)', (ident,did,author,text,timestamp))


def store_for(db, policy=True):
    return WeeklyRunStore(db, 1, context={'contract_version': 1, 'language': 'it',
        'portfolio': {'positions': []}, 'research_started_at': '2026-10-09T15:00:00+00:00',
        'contract': {'research_notes_policy': 'weekly-research-notes/1'} if policy else {}})


def test_research_block_retains_old_pm_question_after_new_card(notes_db):
    decision(notes_db, 1)
    decision(notes_db, 2, timestamp='2026-10-09 10:00:00')
    note(notes_db, 1, 1, 'OLD ORIGINAL QUESTION SENTINEL')
    result = facts.research_block()
    assert 'OLD ORIGINAL QUESTION SENTINEL' in result
    assert 'decision_id=1' in result and 'decision_id=2' in result


def test_latest_active_pm_question_precedes_older_current_card_question(notes_db):
    decision(notes_db, 1)
    decision(notes_db, 2, timestamp='2026-01-03 09:00:00')
    note(notes_db, 1, 2, 'OLDER CURRENT-CARD QUESTION', timestamp='2026-01-04 09:00:00')
    note(notes_db, 2, 1, 'NEWER ORIGINAL-CARD REQUEST', timestamp='2026-10-08 09:00:00')
    frozen = facts.capture_research_notes(notes_db, '2026-10-09T15:00:00+00:00', budget_chars=300)
    assert [row['note_id'] for row in frozen['notes'] if row['status'] == 'included'] == [2]
    assert frozen['notes'][0]['reason'] == 'budget_chars'
    assert frozen['notes'][1]['decision_id'] == 1


def test_more_than_eight_tickers_sixty_global_and_twenty_thread_are_accounted(notes_db):
    for i in range(1, 11):
        decision(notes_db, i, 'ZZ' + str(i))
    for i in range(1, 71):
        note(notes_db, i, 1 if i <= 30 else (i % 10) + 1, 'EVENT ' + str(i),
             author='AI' if i % 2 else 'PM')
    snapshot = facts.capture_research_notes(notes_db, '2026-10-09T15:00:00+00:00')
    assert len(snapshot['cards']) == 10
    assert {row['note_id'] for row in snapshot['notes']} == set(range(1, 71))
    assert len(notes_db.get_decision_notes(1)) > 20
    assert all(row['status'] in {'included','pending','excluded'} and row['reason'] for row in snapshot['notes'])


def test_pm_priority_budget_no_dedup_same_timestamp_no_silent_truncation(notes_db):
    decision(notes_db, 1)
    note(notes_db, 1, 1, 'PM FIRST QUESTION')
    for i in range(2, 8):
        note(notes_db, i, 1, 'AI' * 800, 'AI')
    note(notes_db, 8, 1, 'PM FIRST QUESTION')
    snapshot = facts.capture_research_notes(notes_db, '2026-10-09T15:00:00+00:00', budget_chars=600)
    included = {row['note_id'] for row in snapshot['notes'] if row['status'] == 'included'}
    assert included == {1, 8}
    assert snapshot['status'] == 'INCOMPLETO'
    assert all(row['reason'] == 'budget_chars' for row in snapshot['notes'] if row['status'] == 'pending')
    assert snapshot['texts']['2'] == 'AI' * 800
    assert snapshot['texts']['1'] == snapshot['texts']['8']


@pytest.mark.parametrize('status,veto,revoked,reason', [
    ('ARCHIVED',0,None,'decision_status:ARCHIVED'), ('SKIPPED',1,None,'veto'),
    ('PENDING',0,'2026-01-03','veto_revoked'), ('REVOKED',0,None,'decision_status:REVOKED')])
def test_inactive_or_revoked_notes_are_not_orders(notes_db,status,veto,revoked,reason):
    decision(notes_db,1,status=status,veto=veto,revoked=revoked)
    note(notes_db,1,1,'HISTORICAL DO NOT BUY')
    snapshot = facts.capture_research_notes(notes_db,'2026-10-09T15:00:00+00:00')
    assert snapshot['notes'][0]['status'] == 'excluded'
    assert snapshot['notes'][0]['reason'] == reason
    assert 'HISTORICAL DO NOT BUY' not in facts.research_block(notes_context=snapshot)


def test_freeze_no_live_reacquisition_late_note_excluded_and_missing_blocks(notes_db):
    decision(notes_db,1)
    note(notes_db,1,1,'BEFORE FREEZE')
    note(notes_db,2,1,'AFTER CUTOFF',timestamp='2026-10-10T00:00:00+00:00')
    store = store_for(notes_db)
    snapshot = facts.freeze_research_notes(store, capture=True)
    note(notes_db,3,1,'ARRIVED LATER')
    reopened = WeeklyRunStore(notes_db,1)
    assert facts.freeze_research_notes(reopened) == snapshot
    assert snapshot['notes'][1]['reason'] == 'after_cutoff'
    assert snapshot['texts']['1'] == 'BEFORE FREEZE' and '3' not in snapshot['texts']
    with notes_db._conn() as conn:
        conn.execute("DELETE FROM weekly_checkpoints WHERE stage='research_notes_context_v1'")
    with pytest.raises(WeeklyRunBlocked,match='[Nn]ote|[Nn]otes'):
        facts.freeze_research_notes(reopened)


def test_legacy_and_invalid_marker(notes_db):
    store = store_for(notes_db,False)
    assert facts.freeze_research_notes(store) is None
    store.context['contract']['research_notes_policy'] = 'corrupt'
    with pytest.raises(WeeklyRunBlocked):
        facts.freeze_research_notes(store)


def test_db_error_declared_not_empty_pipeline(notes_db):
    with notes_db._conn() as conn:
        conn.execute('DROP TABLE decision_notes')
    snapshot = facts.capture_research_notes(notes_db,'2026-10-09T15:00:00+00:00')
    assert snapshot['status'] == 'INCOMPLETO' and snapshot['error']
    assert 'INCOMPLETO' in facts.research_block(notes_context=snapshot)
    with pytest.raises(RuntimeError,match='note'):
        notes_db.get_decision_notes(1)


def test_full_original_text_over_two_thousand_preserved(notes_db):
    decision(notes_db,1)
    original = 'PM-' + 'x' * 2400 + '-END'
    ident = notes_db.add_decision_note(1,'PM',original)
    assert notes_db.get_decision_notes(1)[0]['testo'] == original
    assert ident is not None


def test_receipt_matches_actual_message_and_rejects_missing_payload(notes_db):
    decision(notes_db,1)
    note(notes_db,1,1,'ORIGINAL QUESTION')
    store = store_for(notes_db)
    snapshot = facts.freeze_research_notes(store,capture=True)
    board = SimpleNamespace(weekly_store=store,run_scope='weekly')
    body = facts.research_block(notes_context=snapshot)
    receipt = facts.record_research_notes_delivery(board,'fundamentals:1',body)
    assert receipt['delivered_note_ids'] == [1]
    assert receipt['unknown_note_ids'] == []
    assert receipt['excluded_note_ids'] == []
    with pytest.raises(WeeklyRunBlocked):
        facts.record_research_notes_delivery(board,'capo','body without notes')


def test_reply_links_original_question_and_is_idempotent(notes_db):
    decision(notes_db,1)
    decision(notes_db,2,timestamp='2026-01-03 09:00:00')
    note(notes_db,1,1,'ORIGINAL QUESTION')
    store = store_for(notes_db)
    snapshot = facts.freeze_research_notes(store,capture=True)
    board = SimpleNamespace(weekly_store=store,run_scope='weekly',current_round=1)
    facts.record_research_notes_delivery(board,'fundamentals:1',facts.research_block(notes_context=snapshot))
    first = facts.add_frozen_research_reply(board,1,'EXACT AI TEXT',[1],event_id='reply-one')
    assert first['ok'] is True and first['decision_id'] == 1
    assert facts.add_frozen_research_reply(board,1,'EXACT AI TEXT',[1],event_id='reply-one') == first
    assert notes_db.get_decision_notes(1)[-1]['testo'] == 'EXACT AI TEXT'
    assert len(notes_db.get_decision_notes(1)) == 2
    assert facts.add_frozen_research_reply(board,2,'WRONG CARD',[1],event_id='reply-wrong')['ok'] is False
    assert facts.add_frozen_research_reply(board,1,'UNKNOWN QUESTION',[999],event_id='reply-unknown')['ok'] is False
    assert notes_db.get_decision_notes(2) == []

from test_persistence import db as api_db


@pytest.mark.parametrize('broken', [False, True])
def test_http_note_status_distinguishes_db_error_from_verified_empty(api_db, monkeypatch, broken):
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api as api
    with api_db._conn() as conn:
        ident = conn.execute("INSERT INTO decisions(ticker,action,status,timestamp) VALUES(?,?,?,?)",
                             ('ZZTEST','RESEARCH','PENDING','2031-01-01T00:00:00')).lastrowid
    monkeypatch.setattr(api,'get_db',lambda: api_db)
    if broken:
        def fail(*args, **kwargs):
            raise sqlite3.OperationalError('PRIVATE_SQL_PATH_SENTINEL')
        monkeypatch.setattr(api_db,'get_decision_notes',fail)
    api.app.dependency_overrides[api.require_session] = lambda: None
    try:
        response = TestClient(api.app,base_url='http://127.0.0.1').get('/decisions')
    finally:
        api.app.dependency_overrides.pop(api.require_session,None)
    assert response.status_code == 200
    row = next(row for row in response.json()['decisions'] if row['id'] == ident)
    assert row['notes'] == []
    assert row['notes_status'] == ('unavailable' if broken else 'available')
    assert row['notes_error'] == ('decision_notes_unavailable' if broken else None)
    assert 'PRIVATE_SQL_PATH_SENTINEL' not in response.text


def test_resume_contract_retains_absence_and_rejects_invalid_marker():
    from bellomberg.agents import consigliere_multi as cm
    current = {'models': {'synthetic': 'unchanged'}, 'research_notes_policy': 'weekly-research-notes/1'}
    historical = {'models': {'synthetic': 'unchanged'}}
    assert cm._resume_publication_contract(current,historical) == historical
    assert cm._resume_publication_contract(current,current) == current
    with pytest.raises(WeeklyRunBlocked):
        cm._resume_publication_contract(current,{'research_notes_policy':None})


def test_current_card_keeps_trigger_and_thesis_separate_from_original_notes(notes_db):
    decision(notes_db,1)
    decision(notes_db,2,timestamp='2026-01-03 09:00:00')
    with notes_db._conn() as conn:
        conn.execute("UPDATE decisions SET timing='CURRENT TRIGGER',rationale='CURRENT THESIS',memo_id=41 WHERE id=2")
    note(notes_db,1,1,'ORIGINAL QUESTION')
    frozen = facts.capture_research_notes(notes_db,'2026-10-09T15:00:00+00:00')
    block = facts.research_block(notes_context=frozen)
    assert 'CURRENT TRIGGER' in block and 'CURRENT THESIS' in block
    assert frozen['cards'][0]['memo_id'] == 41
    assert frozen['notes'][0]['decision_id'] == 1


def test_reply_requires_delivery_receipt_not_merely_archived_selection(notes_db):
    decision(notes_db,1)
    note(notes_db,1,1,'QUESTION NOT YET DELIVERED')
    store = store_for(notes_db)
    snapshot = facts.freeze_research_notes(store,capture=True)
    board = SimpleNamespace(weekly_store=store,run_scope='weekly',current_round=1)
    assert facts.add_frozen_research_reply(board,1,'PREMATURE ANSWER',[1],event_id='premature')['ok'] is False
    facts.record_research_notes_delivery(board,'fundamentals:1',facts.research_block(notes_context=snapshot))
    assert facts.add_frozen_research_reply(board,1,'DELIVERED ANSWER',[1],event_id='delivered')['ok'] is True


def test_identical_ai_answers_from_distinct_tool_events_remain_distinct(notes_db):
    decision(notes_db,1)
    note(notes_db,1,1,'QUESTION')
    store = store_for(notes_db)
    snapshot = facts.freeze_research_notes(store,capture=True)
    board = SimpleNamespace(weekly_store=store,run_scope='weekly',current_round=1)
    facts.record_research_notes_delivery(board,'fundamentals:1',facts.research_block(notes_context=snapshot))
    first = facts.add_frozen_research_reply(board,1,'SAME ANSWER',[1],event_id='call-one')
    second = facts.add_frozen_research_reply(board,1,'SAME ANSWER',[1],event_id='call-two')
    assert first['note_id'] != second['note_id']
    assert facts.add_frozen_research_reply(board,1,'SAME ANSWER',[1],event_id='call-one') == first
    assert len(notes_db.get_decision_notes(1)) == 3


@pytest.mark.parametrize('change', ['none', 'missing', 'event', 'input', 'note', 'checksum', 'legacy', 'invalid'])
def test_native_reply_recovery_requires_exact_committed_event(notes_db, monkeypatch, change):
    decision(notes_db, 1)
    note(notes_db, 1, 1, 'QUESTION')
    store = store_for(notes_db)
    frozen = facts.freeze_research_notes(store, capture=True)
    board = SimpleNamespace(weekly_store=store, run_scope='weekly', current_round=1)
    facts.record_research_notes_delivery(board, 'fundamentals:1', facts.research_block(notes_context=frozen))
    key, tool_key = 'fundamentals:R1', 'a' * 64
    receipt = facts.add_frozen_research_reply(board, 1, 'ANSWER', [1], event_id=key + ':' + tool_key)
    input_ = {'decision_id': 1, 'note': 'ANSWER', 'note_ids': [1]}
    if change == 'event':
        tool_key = 'b' * 64
    elif change == 'input':
        input_['note'] = 'OTHER ANSWER'
    elif change == 'legacy':
        store.context['contract'].pop('research_notes_policy')
    elif change == 'invalid':
        store.context['contract']['research_notes_policy'] = None
    with notes_db._conn() as conn:
        if change == 'missing':
            conn.execute("DELETE FROM weekly_checkpoints WHERE stage LIKE 'research_notes_reply_v1:%'")
        elif change == 'checksum':
            conn.execute("UPDATE weekly_checkpoints SET payload_sha256='wrong' WHERE stage LIKE 'research_notes_reply_v1:%'")
        elif change == 'note':
            conn.execute("UPDATE decision_notes SET testo='MUTATED' WHERE autore='AI'")
    statements = []
    original_conn = notes_db._conn
    @contextmanager
    def traced_read():
        with original_conn() as conn:
            conn.set_trace_callback(statements.append)
            yield conn
    monkeypatch.setattr(notes_db, '_conn', traced_read)
    for _ in range(2):
        if change in ('checksum', 'invalid'):
            with pytest.raises(WeeklyRunBlocked):
                facts.recover_frozen_research_reply(store, key, tool_key, input_)
        else:
            assert facts.recover_frozen_research_reply(store, key, tool_key, input_) == (
                receipt if change == 'none' else None)
    assert len(notes_db.get_decision_notes(1)) == 2
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)


@pytest.mark.parametrize('old_status,manual', [('ARCHIVED',None),('EXPIRED',None),('PENDING',1)])
def test_archived_history_is_delivered_as_context_only_with_active_card(notes_db,old_status,manual):
    decision(notes_db,1,status=old_status)
    decision(notes_db,2,timestamp='2026-01-03 09:00:00')
    with notes_db._conn() as conn:
        conn.execute('UPDATE decisions SET archive_override=? WHERE id=1',(manual,))
    note(notes_db,1,1,'ARCHIVED BACKGROUND')
    note(notes_db,2,2,'CURRENT PM QUESTION')
    frozen = facts.capture_research_notes(notes_db,'2026-10-09T15:00:00+00:00')
    old = frozen['notes'][0]
    assert old['status'] == 'included' and old['context_role'] == 'CONTESTO_STORICO'
    assert old['decision_status'] == old_status and old['decision_id'] == 1
    assert old['archive_override'] == manual
    block = facts.research_block(notes_context=frozen)
    assert 'ARCHIVED BACKGROUND' in block and 'CONTESTO_STORICO' in block
    small = facts.capture_research_notes(notes_db,'2026-10-09T15:00:00+00:00',budget_chars=300)
    assert small['notes'][1]['status'] == 'included'
    assert small['notes'][0]['status'] == 'pending'
    assert small['status'] == 'INCOMPLETO'
