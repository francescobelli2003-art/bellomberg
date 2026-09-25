"""An imported manual cost reconciliation is exact and never permits a retry."""
import json
import sqlite3

import pytest

from test_preparation_credit_recovery import reconciled
from bellomberg.valuation.preparation_runtime import PreparationRuntime


def _case(tmp_path):
    proposer, key, digest, _ = reconciled(tmp_path)
    identity = 'd4fb68bc-25ef-473d-b73c-58648c7fe1a1'
    folder = tmp_path / 'valuation_ai_budgets'
    folder.mkdir()
    target = folder / (identity + '.sqlite3')
    with proposer._db() as source, sqlite3.connect(target) as copied:
        source.backup(copied)
    policy = {'version': 1, 'enabled': True, 'authorization_id': identity,
              'authorized_usd': '10', 'triggers': ['manual_refresh'],
              'reconciled_rejections': {key: digest}}
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(policy), encoding='utf-8')
    runtime = PreparationRuntime(path, data_root=tmp_path, archive_root=tmp_path, output_dir=tmp_path)
    return runtime, target, policy, proposer


def test_exact_manual_reconciliation_is_auditable_but_never_retryable(tmp_path):
    runtime, _, _, proposer = _case(tmp_path)
    audit = runtime.budget_audit()
    assert audit['state'] == 'reconciled' and audit['known_cost_usd'] == '0'
    assert audit['reconciled_nonretryable_rejections'] == 1
    proposer.call = lambda **_: pytest.fail('reconciliation is not retry authorization')
    with pytest.raises(ValueError, match='incomplete AI response'):
        proposer({'ticker': 'SYNTH'}, {'schema': {}})
    assert runtime.budget_audit() == audit


@pytest.mark.parametrize('fault', ['bad_hash', 'missing_row', 'cost', 'response', 'receipt', 'unknown'])
def test_reconciliation_pin_cannot_hide_a_changed_or_unknown_request(tmp_path, fault):
    runtime, target, policy, _ = _case(tmp_path)
    if fault in ('bad_hash', 'missing_row'):
        if fault == 'bad_hash':
            policy['reconciled_rejections'] = {next(iter(policy['reconciled_rejections'])): '0' * 64}
        else:
            policy['reconciled_rejections'] = {'0' * 64: next(iter(policy['reconciled_rejections'].values()))}
        runtime.policy_path.write_text(json.dumps(policy), encoding='utf-8')
    else:
        with sqlite3.connect(target) as db:
            if fault == 'cost':
                db.execute('UPDATE requests SET cost=1')
            elif fault == 'response':
                db.execute("UPDATE requests SET response='unverified answer'")
            elif fault == 'receipt':
                db.execute("UPDATE requests SET receipt='{}'")
            else:
                db.execute("UPDATE requests SET state='unknown',cost=NULL")
    with pytest.raises(ValueError, match='reconcil'):
        runtime.budget_audit()
