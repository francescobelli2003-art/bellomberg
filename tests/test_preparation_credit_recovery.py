"""An explicit funded retry preserves a reconciled credit refusal and paid work."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3

import pytest

from test_preparation_ai import _proposer


def reconciled(tmp_path, proposer=None):
    from bellomberg.core.llm_client import APIStatusError
    from bellomberg.valuation.preparation_ai import _json
    p = proposer or _proposer(tmp_path)
    success = p.call
    metadata = {'limit_source': 'openrouter_credits', 'provider_name': None}
    reason = 'APIStatusError: HTTP 402: This request requires more credits, or fewer max_tokens. | metadata: ' + json.dumps(metadata)
    p.call = lambda **_: (_ for _ in ()).throw(APIStatusError(402, 'Insufficient credits',
        {'error': {'code': 402, 'metadata': metadata}}))
    with pytest.raises(APIStatusError):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    with p._db() as db:
        row = db.execute('SELECT * FROM requests').fetchone()
        receipt = {**json.loads(row['receipt']), 'stop_reason': 'request_rejected', 'cost_usd': 0,
            'billing_reconciliation': {
                'status': 'reconciled_nonbillable_credit_admission_rejection',
                'observed_error': reason, 'api_usage_receipt_available': False,
                'generation_id_available': False, 'funding_required': True,
                'automatic_retry_authorized': False,
                'request_auxiliary_services': 'none: text-only single request without search, tools, media or file parsing',
                'provider_billing_policy': 'https://openrouter.ai/docs/guides/features/zero-completion-insurance'}}
        text = _json(receipt)
        db.execute("UPDATE requests SET state='rejected',cost=0,receipt=?", (text,))
    p.call = success
    return p, row['key'], sha256(text.encode()).hexdigest(), text


def funding():
    return {'utc': datetime.now(timezone.utc).isoformat(), 'http_status': 200,
        'endpoint': 'GET /api/v1/credits', 'total_credits': 10, 'total_usage': 0,
        'available_usd': '10', 'inference_calls': 0}


def test_manual_reconciliation_alone_does_not_authorize_retry(tmp_path):
    p, _, _, _ = reconciled(tmp_path)
    p.call = lambda **_: pytest.fail('unapproved retry')
    with pytest.raises(ValueError, match='incomplete'):
        p({'ticker': 'SYNTH'}, {'schema': {}})


def test_explicit_funded_retry_archives_refusal_and_reuses_result(tmp_path):
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    from bellomberg.valuation.preparation_rejections import verify_history
    p, key, digest, original = reconciled(tmp_path)
    before = p.summary()
    result = authorize_credit_retry(p, key, expected_receipt_sha256=digest,
        funding_receipt=funding(), operator_reference='synthetic-operator-funding-confirmation')
    assert result['request_key'] == key and p.summary() == before
    with p._db() as db:
        history = verify_history(db, key)
        assert len(history) == 1
        proof = json.loads(history[0][2])['pre_provider_rejection']
        assert proof['reconciled_receipt_text'] == original
        assert proof['reconciled_receipt_sha256'] == digest
    calls = []
    success = p.call
    p.call = lambda **kw: (calls.append(deepcopy(kw)), success(**kw))[1]
    answer = p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p({'ticker': 'SYNTH'}, {'schema': {}}) == answer
    assert len(calls) == 1 and p.summary()['spent_usd'] == .2
    with p._db() as db:
        assert len(verify_history(db, key)) == 1
        assert db.execute('SELECT key FROM requests').fetchone()[0] == key


@pytest.mark.parametrize('fault', ['insufficient_balance', 'stale_balance', 'bad_hash', 'unknown_cost', 'response_present', 'provider_error'])
def test_credit_retry_rejects_unverified_or_unfunded_state_without_writing(tmp_path, fault):
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    from bellomberg.valuation.preparation_ai import _json
    p, key, digest, _ = reconciled(tmp_path)
    evidence = funding()
    if fault == 'insufficient_balance':
        evidence.update(total_credits=.01, available_usd='.01')
    elif fault == 'stale_balance':
        evidence['utc'] = '2020-01-01T00:00:00+00:00'
    elif fault == 'bad_hash':
        digest = '0' * 64
    else:
        with p._db() as db:
            if fault == 'unknown_cost':
                db.execute("UPDATE requests SET state='unknown',cost=NULL")
            elif fault == 'response_present':
                db.execute("UPDATE requests SET response='partial output'")
            else:
                text = json.loads(db.execute('SELECT receipt FROM requests').fetchone()[0])
                text['billing_reconciliation']['observed_error'] = 'upstream provider error'
                serialized = _json(text)
                db.execute('UPDATE requests SET receipt=?', (serialized,))
                digest = sha256(serialized.encode()).hexdigest()
    with p._db() as db:
        before = list(db.iterdump())
    with pytest.raises((ValueError, RuntimeError)):
        authorize_credit_retry(p, key, expected_receipt_sha256=digest,
            funding_receipt=evidence, operator_reference='synthetic-operator-funding-confirmation')
    with p._db() as db:
        assert list(db.iterdump()) == before


def test_credit_recovery_preserves_prior_inflight_attempt_and_blocks_ambiguous_retry(tmp_path, monkeypatch):
    import time
    from test_preparation_rejections import rejection
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    from bellomberg.valuation.preparation_rejections import verify_history
    now = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    p = _proposer(tmp_path)
    success = p.call
    p.call = lambda **_: (_ for _ in ()).throw(rejection())
    with pytest.raises(OSError):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    now[0] += 121
    with p._db() as db:
        original_history = [tuple(row) for row in db.execute('SELECT * FROM request_rejections')]
    p.call = success
    p, key, digest, _ = reconciled(tmp_path, p)
    authorize_credit_retry(p, key, expected_receipt_sha256=digest,
        funding_receipt=funding(), operator_reference='synthetic-funded-recovery')
    with p._db() as db:
        assert len(verify_history(db, key)) == 2
        assert tuple(db.execute('SELECT * FROM request_rejections ORDER BY attempt').fetchone()) == original_history[0]
    p.call = lambda **_: (_ for _ in ()).throw(TimeoutError('uncertain delivery'))
    with pytest.raises(TimeoutError):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p.summary()['unknown_requests'] == 1
    with pytest.raises(RuntimeError, match='unresolved'):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    with p._db() as db:
        assert len(verify_history(db, key)) == 2


@pytest.mark.parametrize('tamper', ['balance', 'reserved'])
def test_runtime_audit_checks_credit_history_before_and_after_paid_reply(tmp_path, tamper):
    from test_preparation_runtime import _policy, _runtime, _write
    from test_preparation_ai import _metadata
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    _write(tmp_path / 'policy.json', _policy())
    runtime = _runtime(tmp_path, None)
    path = runtime.data_root / 'valuation_ai_budgets' / (_policy()['authorization_id'] + '.sqlite3')
    p = BudgetedProposer(path, authorized_usd='2.50', model='synthetic/model', max_tokens=16000,
        thinking={}, metadata=lambda _: _metadata(), call=_proposer(tmp_path).call)
    p, key, digest, _ = reconciled(tmp_path, p)
    authorize_credit_retry(p, key, expected_receipt_sha256=digest,
        funding_receipt=funding(), operator_reference='synthetic-funded-recovery')
    assert runtime.budget_audit()['state'] == 'reconciled'
    p({'ticker': 'SYNTH'}, {'schema': {}})
    assert runtime.budget_audit()['state'] == 'reconciled'
    with p._db() as db:
        proof = json.loads(db.execute('SELECT receipt FROM request_rejections').fetchone()[0])
        if tamper == 'balance':
            proof['pre_provider_rejection']['funding_receipt']['available_usd'] = '999'
        else:
            proof['pre_provider_rejection']['reserved_nano_usd'] = 1
        db.execute('UPDATE request_rejections SET receipt=?', (json.dumps(proof),))
    with pytest.raises(ValueError, match='credit'):
        runtime.budget_audit()


def test_credit_grant_does_not_skip_live_price_cap_or_concurrent_reservation(tmp_path):
    from test_preparation_ai import _metadata
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    p, key, digest, _ = reconciled(tmp_path)
    authorize_credit_retry(p, key, expected_receipt_sha256=digest,
        funding_receipt=funding(), operator_reference='synthetic-funded-recovery')
    metadata = _metadata(); metadata['pricing']['prompt'] = '.01'
    p.metadata = lambda _: metadata
    with pytest.raises(RuntimeError, match='budget insufficient'):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    p.metadata = lambda _: _metadata()
    success = p.call
    def call(**request):
        with pytest.raises(RuntimeError, match='unresolved'):
            _proposer(tmp_path, call=lambda **_: pytest.fail('concurrent duplicate'))(
                {'ticker': 'SYNTH'}, {'schema': {}})
        return success(**request)
    p.call = call
    p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p.summary()['spent_usd'] == .2
    with pytest.raises(ValueError):
        authorize_credit_retry(p, key, expected_receipt_sha256=digest,
            funding_receipt=funding(), operator_reference='repeated-old-authorization')
