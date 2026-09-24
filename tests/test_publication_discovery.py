"""Synthetic SEC notices, actual persistent journal and valuation queue; no AI."""
from copy import deepcopy
from datetime import timedelta
import json
import sqlite3
from threading import Event, Thread

import pytest

from bellomberg.valuation.publication_discovery import scan_publications
from bellomberg.storage.valuation_discovery import DiscoveryStore
from test_valuation_automation import automation as fixture, TICKER


@pytest.fixture
def setup(tmp_path, monkeypatch):
    manager, observed, policy_path, directory = fixture.__wrapped__(tmp_path, monkeypatch)
    policy = json.loads(policy_path.read_text())
    policy['triggers'].append('guidance')
    policy_path.write_text(json.dumps(policy))
    with manager.versions.db._conn() as db:
        db.execute('INSERT INTO positions(ticker,quantita,is_active) VALUES(?,1,1)', (TICKER,))
    return manager, observed, policy_path


def notice(number=1, form='10-Q'):
    acc = f'0000000123-26-{number:06d}'
    return {'ticker': TICKER, 'form': form, 'filed_date': '2026-09-09',
            'report_date': '2026-06-30', 'emittente_id': 'CIK:0000000123', 'accession': acc,
            'url': 'https://www.sec.gov/Archives/edgar/data/123/' + acc.replace('-', '') + '/report.htm',
            'items': ['2.02'] if form == '8-K' else []}


def catalog(rows):
    return {'stato': 'ok', 'motivi': [], 'fonte': 'SEC EDGAR',
            'emittente_id': 'CIK:0000000123', 'documenti': deepcopy(rows)}


def later(manager, hours=7):
    now = manager.jobs.clock() + timedelta(hours=hours)
    manager.jobs.clock = manager.versions.clock = lambda: now


def scan(manager, rows):
    return scan_publications(manager, fetch=lambda ticker, **kw: catalog(rows))


def count_jobs(manager):
    with manager.jobs._connect(read_only=True) as db:
        return db.execute('SELECT COUNT(*) FROM valuation_jobs').fetchone()[0]


def test_baseline_then_new_release_enqueues_once_across_restart_and_row_order(setup):
    manager, observed, _ = setup
    annual, earnings = notice(), notice(2, '8-K')
    assert scan(manager, [annual])['baseline'] is True
    assert count_jobs(manager) == 0
    later(manager)
    changed = scan(manager, [earnings, annual])
    assert changed['status'] == 'ok' and changed['new_notices'] == 1
    job = manager.jobs.get(changed['jobs'][0])
    assert job['request']['trigger'] == 'guidance' and job['status'] == 'queued'
    assert 'bundle' not in job['request']
    later(manager)
    repeat = scan(manager, [annual, earnings, earnings])
    assert repeat['new_notices'] == 0 and repeat['jobs'] == []
    assert count_jobs(manager) == 1 and not any(observed.values())
    assert not (manager.runtime.data_root / 'valuation_ai_budgets').exists()


def test_enqueue_receipt_crash_recovers_same_job_without_losing_event(setup, monkeypatch):
    manager, observed, _ = setup
    scan(manager, [notice()])
    later(manager)
    delivered = DiscoveryStore.delivered
    monkeypatch.setattr(DiscoveryStore, 'delivered', lambda *_: (_ for _ in ()).throw(OSError('synthetic receipt outage')))
    failed = scan(manager, [notice(), notice(2, '8-K')])
    assert failed['status'] == 'error' and count_jobs(manager) == 1
    monkeypatch.setattr(DiscoveryStore, 'delivered', delivered)
    later(manager, 2)
    recovered = scan(manager, [notice(), notice(2, '8-K')])
    assert recovered['status'] == 'ok' and len(recovered['jobs']) == 1
    assert recovered['new_notices'] == 0 and count_jobs(manager) == 1
    assert not any(observed.values())


@pytest.mark.parametrize('problem', ['partial', 'issuer', 'future', 'foreign_url', 'conflict', 'items', 'empty_issuer'])
def test_bad_catalog_never_marks_sources_seen_or_enqueues(setup, problem):
    manager, observed, _ = setup
    response = catalog([notice()])
    if problem == 'partial': response.update(stato='parziale', motivi=['synthetic page unavailable'])
    elif problem == 'issuer': response['emittente_id'] = 'CIK:0000000999'
    elif problem == 'future': response['documenti'][0]['filed_date'] = '2027-01-01'
    elif problem == 'foreign_url': response['documenti'][0]['url'] = 'https://example.org/report.htm'
    elif problem == 'items': response['documenti'][0].update(form='8-K', items='12.020')
    elif problem == 'empty_issuer': response.update(emittente_id=None, documenti=[])
    else:
        duplicate = deepcopy(response['documenti'][0]); duplicate['filed_date'] = '2026-09-08'
        response['documenti'].append(duplicate)
    result = scan_publications(manager, fetch=lambda *a, **k: response)
    assert result['status'] == 'error' and count_jobs(manager) == 0
    path = next((manager.runtime.data_root / 'valuation_discovery').glob('*.sqlite3'))
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT initialized FROM scans').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM seen').fetchone()[0] == 0
    assert not any(observed.values())


def test_raw_sec_publication_reaches_shared_preparer_and_current_workbook(setup, monkeypatch):
    from pathlib import Path
    from bellomberg.market_data import sec_edgar
    from test_filing_catalogs import Risposta
    manager, observed, _ = setup
    rows = [notice()]
    def payload(*args, **kwargs):
        names = {'form': 'form', 'accessionNumber': 'accession', 'filingDate': 'filed_date',
                 'reportDate': 'report_date'}
        recent = {k: [row[v] for row in rows] for k, v in names.items()}
        recent.update(primaryDocument=['report.htm'] * len(rows), items=[','.join(row['items']) for row in rows])
        return Risposta({'cik': '123', 'name': 'Synthetic Issuer', 'filings': {'recent': recent, 'files': []}})
    monkeypatch.setattr(sec_edgar, 'lookup_cik', lambda *a, **k: '0000000123')
    monkeypatch.setattr(sec_edgar, '_headers', lambda: {'User-Agent': 'synthetic-test'})
    monkeypatch.setattr(sec_edgar.requests, 'get', payload)
    assert scan_publications(manager)['baseline']
    later(manager)
    rows.append(notice(2, '8-K'))
    changed = scan_publications(manager)
    assert changed['new_notices'] == 1 and not any(observed.values())
    result = manager.run_one(owner='synthetic-discovery')
    assert result['id'] == changed['jobs'][0] and result['status'] == 'succeeded', result
    current = manager.versions.current(TICKER)
    assert current['current_generation'] == result['result']['generation_id']
    assert current['artifact']['available'] and Path(current['current']['path']).read_bytes().startswith(b'PK')
    calls = len(observed['paid'])
    assert calls and len(observed['acquire']) == len(observed['collect']) == 1
    later(manager)
    assert scan_publications(manager)['new_notices'] == 0
    assert manager.run_one(owner='synthetic-discovery') is None
    assert len(observed['paid']) == calls


@pytest.mark.parametrize('known', [False, True])
def test_first_scan_compares_current_model_sources_and_cutoff(setup, monkeypatch, known):
    manager, observed, _ = setup
    row = notice()
    current = {'current': {'acquisition_snapshot': {'case': {'as_of': '2026-09-08',
        'records': [{'source_id': row['url']}] if known else []}}}}
    monkeypatch.setattr(manager.versions, 'current', lambda _: deepcopy(current))
    result = scan(manager, [row])
    assert result['new_notices'] == (0 if known else 1)
    assert count_jobs(manager) == (0 if known else 1)
    assert not any(observed.values())


def test_global_scan_lease_survives_second_worker_and_expiration(tmp_path):
    from datetime import datetime, timezone
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    first = DiscoveryStore(tmp_path / 'scans.sqlite3', lambda: now)
    claim = first.claim(['SYNTH-A', 'SYNTH-B'])
    second = DiscoveryStore(first.path, lambda: now)
    assert second.claim(['SYNTH-A', 'SYNTH-B']) is None
    now += timedelta(minutes=6)
    resumed = second.claim(['SYNTH-A', 'SYNTH-B'])
    assert resumed['token'] != claim['token']
    with pytest.raises(RuntimeError, match='lease lost'):
        first.observe(claim, [], set())
    second.finish(resumed, {'status': 'ok'})
    assert second.claim(['SYNTH-A', 'SYNTH-B'])['ticker'] == 'SYNTH-B'


@pytest.mark.parametrize('change', ['disable', 'remove'])
def test_revocation_or_removal_during_fetch_does_not_queue(setup, change):
    manager, observed, policy_path = setup
    scan(manager, [notice()]); later(manager)
    def fetch(*a, **k):
        if change == 'disable': policy_path.write_text(json.dumps({'version': 1, 'enabled': False}))
        else:
            with manager.versions.db._conn() as db: db.execute('UPDATE positions SET is_active=0')
        return catalog([notice(), notice(2, '8-K')])
    assert scan_publications(manager, fetch=fetch)['status'] == 'error'
    assert count_jobs(manager) == 0 and not any(observed.values())


def test_disabled_scan_does_not_open_journal_or_fetch(setup):
    manager, observed, policy_path = setup
    policy_path.write_text(json.dumps({'version': 1, 'enabled': False}))
    assert scan_publications(manager, fetch=lambda *a, **k: pytest.fail('network'))['status'] == 'disabled'
    assert not manager.runtime.data_root.exists() and not any(observed.values())


def test_slow_discovery_does_not_block_model_worker_or_hide_inflight_stop(monkeypatch):
    from bellomberg.valuation import valuation_automation_installation as install
    from types import SimpleNamespace
    entered, paid_worker, release = Event(), Event(), Event()
    def discover(_):
        entered.set()
        assert release.wait(3)
        return {'status': 'ok'}
    def work(**kw):
        paid_worker.set()
        return None
    monkeypatch.setattr(install, 'scan_publications', discover)
    for name in ('reconcile_source_events', 'reconcile_tracking', 'reconcile_price_events'):
        monkeypatch.setattr(install, name, lambda _: {})
    runner = install.AutomationRunner(SimpleNamespace(recover=lambda: [], run_one=work), poll_seconds=.01, reconcile_seconds=.02)
    runner.start()
    try:
        assert entered.wait(1) and paid_worker.wait(1)
        assert runner.stop(timeout=.01)['status'] == 'stopping'
    finally:
        release.set()
        assert runner.stop(timeout=2)['status'] == 'stopped'
