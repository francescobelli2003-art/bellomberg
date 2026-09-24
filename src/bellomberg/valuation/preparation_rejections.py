"""Narrow OpenRouter pre-provider rejection proof; ambiguous failures stay blocked.

Policy: https://openrouter.ai/docs/api/reference/limits#in-flight-spending-budget
No sleeps or automatic network loops: a later invocation may retry the same work.
"""
from hashlib import sha256
import json
import math
import time


POLICY = 'https://openrouter.ai/docs/api/reference/limits#in-flight-spending-budget'
REASON, SOURCE = 'in_flight_budget_exhausted', 'openrouter_in_flight_budget'
MAX_ATTEMPTS = 3


class PreparationRetryDeferred(OSError):
    """Keep the queue's source checkpoint without declaring an economic failure."""


def rejection_proof(exc):
    from bellomberg.core.llm_client import APIStatusError
    if not isinstance(exc, APIStatusError) or exc.status_code != 402:
        return None
    error = exc.body.get('error') if isinstance(exc.body, dict) else None
    if not isinstance(error, dict) or error.get('code') != 402:
        return None
    meta = error.get('metadata')
    if (not isinstance(meta, dict) or meta.get('reason') != REASON
            or meta.get('limit_source') != SOURCE or meta.get('provider_name') is not None):
        return None
    headers = meta.get('headers')
    delay = headers.get('Retry-After') if isinstance(headers, dict) else None
    if (type(delay) not in (str, int) or not str(delay).isascii()
            or not str(delay).isdigit() or not 0 < int(delay) <= 86400):
        return None
    now = time.time()
    return {'version': 1, 'code': 402, 'reason': REASON, 'limit_source': SOURCE,
            'retry_after_seconds': int(delay), 'rejected_at': now,
            'retry_at': now + int(delay), 'policy': POLICY}


def validate_proof(receipt):
    proof = receipt.get('pre_provider_rejection')
    if isinstance(proof, dict) and proof.get('version') == 2:
        from .preparation_credit_recovery import validate_credit_proof
        return validate_credit_proof(receipt)
    if (not isinstance(proof, dict) or type(proof.get('version')) is not int or proof.get('version') != 1
            or proof.get('code') != 402 or proof.get('reason') != REASON
            or proof.get('limit_source') != SOURCE or proof.get('policy') != POLICY
            or receipt.get('stop_reason') != 'request_rejected'
            or type(receipt.get('cost_usd')) not in (int, float) or receipt['cost_usd'] != 0):
        raise ValueError('invalid pre-provider rejection receipt')
    delay, at, due = (proof.get(name) for name in ('retry_after_seconds', 'rejected_at', 'retry_at'))
    if (type(delay) is not int or not 0 < delay <= 86400
            or any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in (at, due))
            or due != at + delay):
        raise ValueError('invalid pre-provider rejection deadline')
    return proof


def verify_history(db, key):
    from .preparation_ai import _json
    import sqlite3
    try:
        history = db.execute('SELECT attempt,request,receipt FROM request_rejections WHERE key=? ORDER BY attempt',
                             (key,)).fetchall()
    except sqlite3.Error as exc:
        raise ValueError('missing rejection history') from exc
    for index, (attempt, request_text, receipt_text) in enumerate(history, 1):
        receipt, request = json.loads(receipt_text), json.loads(request_text)
        validate_proof(receipt)
        request.pop('provider_max_price', None)
        if attempt != index or sha256(_json(request).encode()).hexdigest() != key:
            raise ValueError('rejection history differs from request identity')
    return history


def retry_allowed(db, row, *, check_deadline=True):
    receipt = json.loads(row['receipt'])
    if 'pre_provider_rejection' not in receipt:
        return False  # Historical reconciliations do not authorize a retry.
    proof = validate_proof(receipt)
    history = verify_history(db, row['key'])
    if (row['cost'] != 0 or row['response'] is not None or not history
            or history[-1][1] != row['request'] or history[-1][2] != row['receipt']):
        raise ValueError('rejection history differs from current journal')
    if check_deadline:
        if len(history) >= MAX_ATTEMPTS:
            raise RuntimeError('pre-provider rejection retry limit reached; inspect provider account')
        if time.time() < proof['retry_at']:
            raise PreparationRetryDeferred('pre-provider rejection Retry-After has not elapsed')
    return True


def record_rejection(db, key, quote, proof):
    from .preparation_ai import _json
    row = db.execute('SELECT * FROM requests WHERE key=?', (key,)).fetchone()
    if row['state'] != 'reserved' or row['cost'] is not None or row['response'] is not None:
        raise ValueError('rejection without an active reservation')
    history = verify_history(db, key)
    receipt = _json({**quote, 'stop_reason': 'request_rejected', 'cost_usd': 0,
                     'pre_provider_rejection': proof})
    validate_proof(json.loads(receipt))
    db.execute('INSERT INTO request_rejections(key,attempt,request,receipt) VALUES(?,?,?,?)',
               (key, len(history) + 1, row['request'], receipt))
    db.execute("UPDATE requests SET state='rejected',cost=0,receipt=?,error='APIStatusError' WHERE key=?",
               (receipt, key))
