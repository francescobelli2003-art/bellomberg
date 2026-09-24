"""Explicit operator recovery of one reconciled, nonbillable credit refusal.

No provider call or billing reconciliation occurs here. The operator supplies the
exact reconciled receipt hash and a recent account-credit observation. Normal
pricing, budget, history and concurrency checks still precede the next request.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import time


POLICY = 'https://openrouter.ai/docs/guides/features/zero-completion-insurance'
REASON = 'credit_admission_rejected'
SOURCE = 'openrouter_credits'


def _manual_receipt(text, expected_digest):
    if not isinstance(text, str) or sha256(text.encode()).hexdigest() != expected_digest:
        raise ValueError('reconciled credit receipt hash differs')
    receipt = json.loads(text)
    judgment = receipt.get('billing_reconciliation')
    if (receipt.get('stop_reason') != 'request_rejected' or type(receipt.get('cost_usd')) not in (int, float)
            or receipt['cost_usd'] != 0 or 'pre_provider_rejection' in receipt
            or not isinstance(judgment, dict)
            or judgment.get('status') != 'reconciled_nonbillable_credit_admission_rejection'
            or judgment.get('api_usage_receipt_available') is not False
            or judgment.get('generation_id_available') is not False
            or judgment.get('funding_required') is not True
            or judgment.get('automatic_retry_authorized') is not False
            or judgment.get('provider_billing_policy') != POLICY
            or judgment.get('request_auxiliary_services') !=
                'none: text-only single request without search, tools, media or file parsing'):
        raise ValueError('not a reconciled nonbillable credit admission refusal')
    error = judgment.get('observed_error')
    prefix = 'APIStatusError: HTTP 402: This request requires more credits, or fewer max_tokens.'
    if not isinstance(error, str) or not error.startswith(prefix) or ' | metadata: ' not in error:
        raise ValueError('missing exact platform credit refusal')
    metadata = json.loads(error.split(' | metadata: ', 1)[1])
    if metadata.get('limit_source') != SOURCE or metadata.get('provider_name', 'missing') is not None:
        raise ValueError('not a platform credit admission refusal')
    return receipt


def _funding(evidence, reserved, authorized_at):
    if (not isinstance(evidence, dict) or evidence.get('http_status') != 200
            or evidence.get('endpoint') != 'GET /api/v1/credits' or evidence.get('inference_calls') != 0
            or type(reserved) is not int or reserved <= 0):
        raise ValueError('invalid credit observation')
    try:
        credits, usage, available = (Decimal(str(evidence[name])) for name in
                                     ('total_credits', 'total_usage', 'available_usd'))
        observed = datetime.fromisoformat(evidence['utc'])
        if observed.tzinfo is None:
            raise ValueError('credit observation requires timezone')
        at = observed.timestamp()
    except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
        raise ValueError('invalid credit observation values') from exc
    if (not all(v.is_finite() and v >= 0 for v in (credits, usage, available))
            or available != credits - usage or available * 10**9 < reserved
            or not 0 <= authorized_at - at <= 300):
        raise ValueError('stale, inconsistent or insufficient verified credit')
    return {k: evidence[k] for k in ('utc', 'http_status', 'endpoint', 'total_credits',
                                   'total_usage', 'available_usd', 'inference_calls')}


def validate_credit_proof(receipt):
    proof = receipt.get('pre_provider_rejection')
    if (not isinstance(proof, dict) or type(proof.get('version')) is not int or proof['version'] != 2
            or proof.get('code') != 402 or proof.get('reason') != REASON
            or proof.get('limit_source') != SOURCE or proof.get('policy') != POLICY
            or receipt.get('stop_reason') != 'request_rejected'
            or type(receipt.get('cost_usd')) not in (int, float) or receipt['cost_usd'] != 0):
        raise ValueError('invalid explicit credit recovery proof')
    at = proof.get('authorized_at')
    if (type(at) not in (int, float) or not math.isfinite(at) or at < 0
            or proof.get('retry_at') != at or not isinstance(proof.get('operator_reference'), str)
            or not proof['operator_reference'].strip()):
        raise ValueError('invalid explicit credit recovery authorization')
    original = _manual_receipt(proof.get('reconciled_receipt_text'), proof.get('reconciled_receipt_sha256'))
    if (proof.get('reserved_nano_usd') != original.get('reserve_nano_usd')
            or {k: v for k, v in receipt.items() if k != 'pre_provider_rejection'} != original):
        raise ValueError('credit recovery differs from the reconciled reservation or receipt')
    _funding(proof.get('funding_receipt'), proof.get('reserved_nano_usd'), at)
    return proof


def authorize_credit_retry(proposer, key, *, expected_receipt_sha256, funding_receipt, operator_reference):
    """Archive an exact rejected attempt; never erase it or change the work key."""
    from .preparation_ai import _json
    from .preparation_rejections import verify_history, MAX_ATTEMPTS
    with proposer._db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM requests WHERE key=?', (key,)).fetchone()
        if row is None or row['state'] != 'rejected' or row['cost'] != 0 or row['response'] is not None:
            raise ValueError('credit recovery requires a reconciled empty rejected request')
        original = _manual_receipt(row['receipt'], expected_receipt_sha256)
        request = json.loads(row['request'])
        pricing = request.pop('provider_max_price', None)
        if (not isinstance(pricing, dict) or pricing.get('request') != 0
                or set(request) != {'model', 'max_tokens', 'thinking', 'system', 'messages', 'response_format'}
                or not isinstance(request['system'], str) or len(request['messages']) != 1
                or request['messages'][0].get('role') != 'user'
                or not isinstance(request['messages'][0].get('content'), str)
                or sha256(_json(request).encode()).hexdigest() != key):
            raise ValueError('credit recovery requires the exact text-only request without auxiliary services')
        history = verify_history(db, key)
        if len(history) + 1 >= MAX_ATTEMPTS:
            raise RuntimeError('credit recovery retry limit reached')
        rows = db.execute('SELECT cost,state FROM requests').fetchall()
        if any(r['cost'] is None or r['state'] == 'overrun' for r in rows):
            raise RuntimeError('unresolved costs block credit recovery')
        cap = db.execute('SELECT cap FROM authorization WHERE id=1').fetchone()[0]
        if sum(r['cost'] for r in rows) + row['reserved'] > cap:
            raise RuntimeError('authorized budget insufficient for credit recovery')
        now = time.time()
        proof = {'version': 2, 'code': 402, 'reason': REASON, 'limit_source': SOURCE, 'policy': POLICY,
            'authorized_at': now, 'retry_at': now, 'operator_reference': operator_reference,
            'reserved_nano_usd': row['reserved'],
            'funding_receipt': _funding(funding_receipt, row['reserved'], now),
            'reconciled_receipt_text': row['receipt'], 'reconciled_receipt_sha256': expected_receipt_sha256}
        receipt = {**original, 'pre_provider_rejection': proof}
        validate_credit_proof(receipt)
        serialized = _json(receipt)
        db.execute('INSERT INTO request_rejections(key,attempt,request,receipt) VALUES(?,?,?,?)',
                   (key, len(history)+1, row['request'], serialized))
        db.execute('UPDATE requests SET receipt=? WHERE key=?', (serialized, key))
        verify_history(db, key)
    return {'request_key': key, 'attempts_archived': len(history)+1, 'paid_calls': 0,
            'reconciled_receipt_sha256': expected_receipt_sha256, 'authorized_at': now}
