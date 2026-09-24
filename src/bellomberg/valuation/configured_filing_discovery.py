"""Acquire one due, explicitly enabled Filing Diff profile for tracked issuers.

This reuses the existing scheduler, immutable run storage and source verifier.
It does not classify a generic 6-K as a statement, run a paid judge or indexer,
or enqueue a valuation from unverified text. Source-event reconciliation owns
the latter step. Missing profiles and incomplete coverage stay visible.
"""
from contextlib import closing

from .valuation_source_events import _read_db, _tracked


def scan_configured_filings(manager, *, pipeline=None):
    state = manager.runtime.status()
    if state.get('status') != 'configured' or 'filing_diff' not in state.get('triggers', ()):
        return {'status': 'disabled', 'reason': 'filing_diff_trigger_not_authorized'}
    policy = manager.runtime._require('filing_diff')

    def tracked():
        errors = []
        with closing(_read_db(manager.jobs.db_path)) as db:
            tickers = _tracked(db, errors)
        if errors:
            raise ValueError('tracked source unavailable: ' + str(errors))
        return set(tickers)

    from bellomberg.storage.filing_store import FilingStore, RunAlreadyActive, RunNotDue
    from bellomberg.market_data.filing_service import FilingService
    store = FilingStore(manager.jobs.db_path)
    tickers = tracked()
    profiles = {p['ticker']: p for p in store.list_profiles() if p['ticker'] in tickers}
    gaps = [{'ticker': ticker, 'reason': 'filing_profile_absent' if ticker not in profiles
             else 'filing_profile_disabled'} for ticker in sorted(tickers)
            if ticker not in profiles or not profiles[ticker]['enabled']]
    due = sorted((row for row in store.next_due(now=manager.jobs.clock()) if row['ticker'] in tickers),
                 key=lambda row: (row['next_due'] or '', row['ticker']))
    active = [{'ticker': ticker, 'run_id': row['id'], 'status': row['status']}
              for ticker in sorted(tickers) for row in store.list_runs(ticker)
              if row['status'] in ('queued', 'running')]
    result = {'status': 'busy' if active else 'partial' if gaps else 'idle',
              'coverage_gaps': gaps, 'active_runs': active,
              'limitation': 'Only enabled, configured Filing Diff profiles and their verified report scope; '
                            'standalone guidance releases outside those rules remain uncovered.'}
    if not due:
        return result
    ticker = due[0]['ticker']
    selected = profiles[ticker]

    def guard():
        manager.runtime._unchanged('filing_diff', policy)
        if ticker not in tracked():
            raise ValueError('ticker removed during configured source scan')
        current = store.get_profile(ticker)
        if not current or not current['enabled'] or current['version'] != selected['version']:
            raise ValueError('filing profile changed or disabled during source scan')

    def acquire(profile, *, archivio):
        guard()
        collector = pipeline
        if collector is None:
            from bellomberg.market_data.filing_pipeline import esegui_profilo
            collector = esegui_profilo
        return collector(profile, archivio=archivio, oggi=manager.jobs.clock().date())

    def skip_paid(*_):
        return {'status': 'skipped', 'findings': [], 'model': None, 'usage': None,
                'reason': 'valuation source discovery: paid judgment and indexing disabled'}

    guard()
    service = FilingService(store, manager.runtime.archive_root, pipeline=acquire,
                            judge=skip_paid, indexer=skip_paid)
    try:
        queued = service.queue(ticker, trigger='scheduled')
    except (RunAlreadyActive, RunNotDue) as exc:
        return {**result, 'status': 'busy', 'ticker': ticker, 'reason': str(exc)}
    row = service.execute(queued['id'])
    status = {'ok': 'ok', 'parziale': 'partial'}.get(row['status'], 'error')
    return {**result, 'status': 'partial' if status == 'ok' and gaps else status,
            'ticker': ticker, 'run_id': row['id'], 'filing_status': row['status'],
            'reason': row['reason'], 'profile_version': row['profile_version']}
