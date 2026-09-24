"""Configured source acquisition uses existing verification, without paid work."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from test_valuation_automation import automation as fixture, TICKER
from test_filing_store import profile


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from bellomberg.storage.filing_store import FilingStore, ensure_schema
    manager, observed, policy_path, _ = fixture.__wrapped__(tmp_path, monkeypatch)
    with manager.versions.db._conn() as db:
        ensure_schema(db)
        db.execute('INSERT INTO positions(ticker,quantita,is_active) VALUES(?,1,1)', (TICKER,))
    return manager, observed, policy_path, FilingStore(manager.jobs.db_path)


def scan(*args, **kwargs):
    from bellomberg.valuation.configured_filing_discovery import scan_configured_filings
    return scan_configured_filings(*args, **kwargs)


@pytest.mark.parametrize('disabled', [True, False])
def test_unapproved_scan_never_opens_source_store(setup, monkeypatch, disabled):
    manager, observed, policy_path, _ = setup
    policy = json.loads(policy_path.read_text())
    if disabled:
        policy = {'version': 1, 'enabled': False}
    else:
        policy['triggers'].remove('filing_diff')
    policy_path.write_text(json.dumps(policy))
    monkeypatch.setattr('bellomberg.storage.filing_store.FilingStore', lambda *_: pytest.fail('store opened'))
    assert scan(manager, pipeline=lambda *a, **k: pytest.fail('acquisition'))['status'] == 'disabled'
    assert not any(observed.values())


def test_one_due_tracked_profile_per_scan_no_judge_or_index(setup, monkeypatch):
    manager, observed, _, store = setup
    from bellomberg.market_data import filing_service
    monkeypatch.setattr(filing_service, 'judge_filing', lambda *_: pytest.fail('paid judgment'))
    monkeypatch.setattr(filing_service, 'default_indexer', lambda *_a, **_k: pytest.fail('index embeddings'))
    with manager.versions.db._conn() as db:
        db.execute("INSERT INTO favorite_companies(ticker,name) VALUES('SYNTH-Z','Synthetic')")
    for ticker in (TICKER, 'SYNTH-Z', 'UNTRACKED'):
        store.set_profile(ticker, profile(ticker), enabled=True, qualitative_enabled=True)
    calls = []
    def acquire(p, **kwargs):
        from test_filing_service import result
        calls.append((deepcopy(p), kwargs))
        return result()
    first = scan(manager, pipeline=acquire)
    assert first['status'] == 'ok' and first['ticker'] == TICKER
    row = store.get_run(first['run_id'])
    assert row['trigger'] == 'scheduled' and row['judgment']['status'] == 'skipped'
    assert row['index']['status'] == 'skipped' and len(calls) == 1
    assert str(manager.runtime.archive_root) in str(calls[0][1]['archivio'])
    assert scan(manager, pipeline=acquire)['ticker'] == 'SYNTH-Z'
    assert scan(manager, pipeline=acquire)['status'] == 'idle'
    assert len(calls) == 2 and not store.list_runs('UNTRACKED')
    assert not any(observed.values())


def test_missing_or_disabled_profile_remains_explicit(setup):
    manager, _, _, store = setup
    absent = scan(manager, pipeline=lambda *a, **k: pytest.fail('unconfigured acquisition'))
    assert absent['coverage_gaps'] == [{'ticker': TICKER, 'reason': 'filing_profile_absent'}]
    store.set_profile(TICKER, profile(TICKER), enabled=False)
    disabled = scan(manager)
    assert disabled['coverage_gaps'] == [{'ticker': TICKER, 'reason': 'filing_profile_disabled'}]
    assert not store.list_runs(TICKER)


def test_existing_active_filing_run_is_reported_without_second_acquisition(setup):
    manager, observed, _, store = setup
    store.set_profile(TICKER, profile(TICKER), enabled=True)
    active = store.start_run(TICKER, trigger='scheduled')
    result = scan(manager, pipeline=lambda *a, **k: pytest.fail('duplicate source acquisition'))
    assert result['status'] == 'busy'
    assert result['active_runs'] == [{'ticker': TICKER, 'run_id': active['id'], 'status': 'queued'}]
    assert len(store.list_runs(TICKER)) == 1 and not any(observed.values())


@pytest.mark.parametrize('change', ['authorization', 'untrack', 'profile'])
def test_change_after_queue_stops_download_and_finishes_run(setup, monkeypatch, change):
    from bellomberg.market_data.filing_service import FilingService
    manager, observed, policy_path, store = setup
    store.set_profile(TICKER, profile(TICKER), enabled=True)
    original = FilingService.queue
    def queue(service, *args, **kwargs):
        row = original(service, *args, **kwargs)
        if change == 'authorization':
            policy_path.write_text(json.dumps({'version': 1, 'enabled': False}))
        elif change == 'untrack':
            with manager.versions.db._conn() as db:
                db.execute('UPDATE positions SET is_active=0 WHERE ticker=?', (TICKER,))
        else:
            store.set_profile(TICKER, profile(TICKER), enabled=False)
        return row
    monkeypatch.setattr(FilingService, 'queue', queue)
    report = scan(manager, pipeline=lambda *a, **k: pytest.fail('download after revocation'))
    assert report['status'] == 'error'
    assert store.list_runs(TICKER)[0]['status'] == 'errore'
    assert not any(observed.values())


@pytest.mark.parametrize('statement', [True, False])
def test_verified_foreign_statement_reaches_existing_refresh_queue(setup, monkeypatch, tmp_path, statement):
    from bellomberg.market_data import filing_pipeline
    from bellomberg.valuation.valuation_source_events import reconcile_source_events
    from test_filing_verifica import documento, profilo
    manager, observed, _, store = setup
    p = profilo(ticker=TICKER, tipo='semestrale', fonti=['sec'])
    p['verifica']['tipo'] = 'Interim report'
    store.set_profile(TICKER, p, enabled=True, qualitative_enabled=True)
    path = documento(tmp_path, anno=2026)
    raw = path.read_bytes().replace(b'2026-12-31', b'2026-06-30').replace(b'Annual report', b'Interim report')
    if not statement:
        raw = raw.replace(b'Interim report', b'Share repurchase announcement')
    url = 'https://www.sec.gov/Archives/edgar/data/1234/000000123426000001/report.htm'
    row = {'ticker': TICKER, 'form': '6-K', 'filed_date': '2026-08-01',
           'report_date': '2026-06-30', 'emittente_id': 'CIK:0000001234',
           'accession': '0000001234-26-000001', 'url': url}
    monkeypatch.setattr('bellomberg.market_data.sec_edgar.get_filing_catalog',
                        lambda *_: {'stato': 'ok', 'motivi': [], 'documenti': [row]})
    def download(actual_url, archive, hosts):
        assert actual_url == url
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / 'synthetic.html'; target.write_bytes(raw)
        return {'path': str(target), 'sha256': sha256(raw).hexdigest()}
    monkeypatch.setattr(filing_pipeline, '_scarica', download)
    scanned = scan(manager)
    if not statement:
        stored = store.get_run(scanned['run_id'])
        assert stored['result']['candidati'][0]['stato'] == 'non_verificato'
        assert reconcile_source_events(manager)['outcomes'][0]['status'] == 'blocked'
        with manager.jobs._connect(read_only=True) as db:
            assert db.execute('SELECT count(*) FROM valuation_jobs').fetchone()[0] == 0
        assert not any(observed.values())
        return
    assert scanned['status'] in ('ok', 'partial'), scanned
    stored = store.get_run(scanned['run_id'])
    assert stored['result']['candidati'][0]['stato'] == 'verificato'
    first = reconcile_source_events(manager)['outcomes'][0]
    again = reconcile_source_events(manager)['outcomes'][0]
    assert first['status'] == 'queued' and again['id'] == first['id'] and again['reused']
    job = manager.jobs.get(first['id'])
    assert job['request']['filing_results'][0]['candidati'][0]['form'] == '6-K'
    assert not any(observed.values())


def test_catalog_error_does_not_stop_configured_source_scan(monkeypatch):
    from threading import Event
    from bellomberg.valuation import valuation_automation_installation as install
    calls, done = [], Event()
    runner = install.AutomationRunner(object())
    def broken(_):
        calls.append('catalog')
        raise OSError('synthetic catalog unavailable')
    def configured(_):
        calls.append('configured')
        done.set()
        runner._stop.set()
        return {'status': 'ok'}
    monkeypatch.setattr(install, 'scan_publications', broken)
    monkeypatch.setattr(install, 'scan_configured_filings', configured)
    runner._discover()
    assert done.is_set() and calls == ['catalog', 'configured']
    assert runner._state['discovery']['status'] == 'error'
    assert runner._state['configured_filings']['status'] == 'ok'
