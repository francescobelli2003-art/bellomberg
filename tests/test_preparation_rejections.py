"""Only documented pre-provider rejections can release a reservation and retry."""
import json
import sqlite3
from copy import deepcopy

import pytest

from test_preparation_ai import _proposer


def rejection(**changes):
    from bellomberg.core.llm_client import APIStatusError
    metadata = {'reason': 'in_flight_budget_exhausted',
                'limit_source': 'openrouter_in_flight_budget',
                'headers': {'Retry-After': '120'}, 'provider_name': None}
    metadata.update(changes)
    return APIStatusError(402, 'Temporarily held credits',
                          {'error': {'code': 402, 'metadata': metadata}})


def test_rejected_request_retries_after_deadline_preserving_history_and_cache(tmp_path, monkeypatch):
    import time
    now = [1000.0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    p = _proposer(tmp_path)
    success, seen = p.call, []
    def call(**request):
        seen.append(deepcopy(request))
        if len(seen) == 1:
            raise rejection()
        return success(**request)
    p.call = call
    with pytest.raises(Exception, match='Temporarily held'):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p.summary()['spent_usd'] == 0
    assert p.summary()['unknown_requests'] == 0
    restarted = _proposer(tmp_path, call=call)
    with pytest.raises(OSError, match='Retry-After'):
        restarted({'ticker': 'SYNTH'}, {'schema': {}})
    assert len(seen) == 1
    now[0] = 1120
    result = restarted({'ticker': 'SYNTH'}, {'schema': {}})
    assert seen[0] == seen[1]
    assert restarted({'ticker': 'SYNTH'}, {'schema': {}}) == result
    assert len(seen) == 2 and restarted.summary()['spent_usd'] == .2
    with sqlite3.connect(p.path) as db:
        history = db.execute('SELECT key,attempt,request,receipt FROM request_rejections').fetchall()
        assert len(history) == 1 and history[0][1] == 1
        assert json.loads(history[0][2]) == seen[0]
        receipt = json.loads(history[0][3])
        assert receipt['cost_usd'] == 0 and receipt['stop_reason'] == 'request_rejected'
        assert receipt['pre_provider_rejection']['retry_at'] == 1120
        assert db.execute('SELECT key FROM requests').fetchone()[0] == history[0][0]


@pytest.mark.parametrize('change', [
    {'reason': 'weight_exceeds_budget'}, {'limit_source': 'openrouter_credits'},
    {'provider_name': 'upstream'}, {'headers': {}}, {'headers': {'Retry-After': '-1'}},
    {'headers': {'Retry-After': 'NaN'}}, {'headers': {'Retry-After': True}},
])
def test_other_or_unverifiable_errors_keep_unknown_cost(tmp_path, change):
    p = _proposer(tmp_path, call=lambda **_: (_ for _ in ()).throw(rejection(**change)))
    with pytest.raises(Exception):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p.summary()['spent_usd'] is None and p.summary()['unknown_requests'] == 1
    with pytest.raises(RuntimeError, match='unresolved'):
        _proposer(tmp_path)({'ticker': 'SYNTH'}, {'schema': {}})


def test_repeated_rejections_are_bounded_without_sleeping_or_changing_request(tmp_path, monkeypatch):
    import time
    now, calls = [1000.0], []
    monkeypatch.setattr(time, 'time', lambda: now[0])
    def call(**request):
        calls.append(request)
        raise rejection()
    p = _proposer(tmp_path, call=call)
    for _ in range(3):
        with pytest.raises(Exception, match='Temporarily held'):
            p({'ticker': 'SYNTH'}, {'schema': {}})
        now[0] += 121
    with pytest.raises(RuntimeError, match='retry limit'):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    with sqlite3.connect(p.path) as db:
        assert db.execute('SELECT count(*) FROM request_rejections').fetchone()[0] == 3


@pytest.mark.parametrize('fault', ['unknown_other', 'higher_price', 'history_changed'])
def test_retry_rechecks_budget_and_history_before_sending(tmp_path, monkeypatch, fault):
    import time
    from test_preparation_ai import _metadata
    now = [1000.0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    p = _proposer(tmp_path, limit=2, call=lambda **_: (_ for _ in ()).throw(rejection()))
    with pytest.raises(Exception):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    now[0] += 121
    p.call = lambda **_: pytest.fail('unsafe repeat call')
    if fault == 'higher_price':
        meta = _metadata(); meta['pricing']['prompt'] = '.0001'
        p.metadata = lambda _: meta
    else:
        with sqlite3.connect(p.path) as db:
            if fault == 'unknown_other':
                db.execute("INSERT INTO requests(key,state,reserved,request,receipt) VALUES ('other','unknown',1,'{}','{}')")
            else:
                db.execute("UPDATE request_rejections SET receipt='{}'")
    with pytest.raises((RuntimeError, ValueError)):
        p({'ticker': 'SYNTH'}, {'schema': {}})


def test_runtime_audit_accepts_verified_rejection_and_rejects_tampered_archive(tmp_path, monkeypatch):
    import time
    now = [1000.0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    from test_preparation_runtime import _policy, _runtime, _write
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    from test_preparation_ai import _metadata
    _write(tmp_path / 'policy.json', _policy())
    runtime = _runtime(tmp_path, None)
    path = runtime.data_root / 'valuation_ai_budgets' / (_policy()['authorization_id'] + '.sqlite3')
    p = BudgetedProposer(path, authorized_usd='2.50', model='synthetic/model', max_tokens=16000,
                         thinking={}, metadata=lambda _: _metadata(),
                         call=lambda **_: (_ for _ in ()).throw(rejection()))
    with pytest.raises(Exception):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    assert runtime.budget_audit()['state'] == 'deferred'
    now[0] += 121
    assert runtime.budget_audit()['state'] == 'reconciled'
    with sqlite3.connect(path) as db:
        db.execute("UPDATE request_rejections SET request='{}'")
    with pytest.raises(ValueError, match='rejection'):
        runtime.budget_audit()


def test_retry_transport_failure_stays_unknown_and_does_not_erase_rejection(tmp_path, monkeypatch):
    import time
    now = [1000.0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    p = _proposer(tmp_path, call=lambda **_: (_ for _ in ()).throw(rejection()))
    with pytest.raises(Exception):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    now[0] += 121
    p.call = lambda **_: (_ for _ in ()).throw(TimeoutError('uncertain delivery'))
    with pytest.raises(TimeoutError):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p.summary()['spent_usd'] is None and p.summary()['unknown_requests'] == 1
    with sqlite3.connect(p.path) as db:
        assert db.execute('SELECT count(*) FROM request_rejections').fetchone()[0] == 1
    with pytest.raises(RuntimeError, match='unresolved'):
        _proposer(tmp_path)({'ticker': 'SYNTH'}, {'schema': {}})


def test_retry_reservation_blocks_a_competing_proposer(tmp_path, monkeypatch):
    import time
    now = [1000.0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    p = _proposer(tmp_path)
    success = p.call
    p.call = lambda **_: (_ for _ in ()).throw(rejection())
    with pytest.raises(Exception):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    now[0] += 121
    def call(**request):
        with pytest.raises(RuntimeError, match='unresolved'):
            _proposer(tmp_path, call=lambda **_: pytest.fail('double paid request'))(
                {'ticker': 'SYNTH'}, {'schema': {}})
        return success(**request)
    p.call = call
    p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p.summary()['spent_usd'] == .2


from test_valuation_automation import automation, TICKER


def test_queue_preserves_sources_until_rejection_deadline_then_completes(automation, monkeypatch):
    import time
    now = [1_700_000_000.0]  # XLSX ZIP timestamps must remain after 1980.
    monkeypatch.setattr(time, 'time', lambda: now[0])
    manager, observed, _, _ = automation
    def fail_once(spec):
        if len(observed['paid']) == 2:
            raise rejection()
    observed['on_paid'] = fail_once
    queued = manager.enqueue_initial(TICKER, 'portfolio')
    initial = manager.run_one(owner='before-rejection')
    assert initial['status'] == 'interrupted', initial
    assert manager.jobs.get(queued['id'])['checkpoint']['stage'] == 'acquired'
    assert manager.recover()[0]['status'] == 'blocked'
    assert manager.run_one(owner='too-early') is None
    now[0] += 121
    assert manager.recover()[0]['status'] == 'queued'
    final = manager.run_one(owner='after-rejection')
    assert final['status'] == 'succeeded', final.get('reason')
    assert len(observed['collect']) == 1 and len(observed['acquire']) == 1
    assert observed['paid'][1] == observed['paid'][2]
    assert all(call != observed['paid'][0] for call in observed['paid'][1:])
    assert manager.runtime.budget_audit()['state'] == 'reconciled'
    assert manager.versions.current(TICKER)['artifact']['available']
