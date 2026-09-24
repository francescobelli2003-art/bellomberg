"""Bounded SEC notices -> durable refresh events; never AI or method records.

Catalog notices establish publication identity, not verified financial inputs.
The existing common preparation service must acquire and validate their contents.
"""
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlsplit

from bellomberg.storage.valuation_discovery import DiscoveryStore, encoded
from .preparation_exhibits import _sec_parts
from .valuation_source_events import _iso, _read_db, _tracked


LIMITATION = "SEC recent/history catalog, at most two pages over 400 days. Earnings: 8-K/8-K-A Item 2.02 only, not all guidance channels. Non-SEC and 6-K/40-F require configured Filing Diff coverage. Catalog notices are not financial-input proofs."


def publication_entries(ticker, response, cutoff, triggers):
    if (not isinstance(response, dict) or response.get('stato') != 'ok' or response.get('motivi')
            or response.get('fonte') != 'SEC EDGAR' or not isinstance(response.get('documenti'), list)):
        raise ValueError('publication catalog incomplete: ' + str(response.get('motivi') if isinstance(response, dict) else type(response).__name__))
    if len(response['documenti']) > 200:
        raise ValueError('publication catalog exceeds the 200-notice scan limit')
    if not re.fullmatch(r'CIK:[0-9]{10}', str(response.get('emittente_id', ''))):
        raise ValueError('publication catalog has no verified issuer')
    entries, urls, excluded = {}, {}, []
    for raw in response['documenti']:
        if not isinstance(raw, dict):
            raise ValueError('publication notice must be an object')
        form = raw.get('form')
        if form in ('8-K', '8-K/A') and (not isinstance(raw.get('items'), list)
                or any(not isinstance(item, str) for item in raw['items'])):
            raise ValueError('SEC notice items must be a list of strings')
        if form in ('10-K', '10-Q', '20-F', '10-K/A', '10-Q/A', '20-F/A'):
            trigger = 'filing_diff'
        elif form in ('8-K', '8-K/A') and '2.02' in raw.get('items', []):
            trigger = 'guidance'
        else:
            excluded.append({'form': form, 'reason': 'outside supported notice coverage'})
            continue
        cik, accession, _ = _sec_parts(raw['url'])
        issuer = 'CIK:' + cik.zfill(10)
        if (raw.get('ticker') != ticker or raw.get('emittente_id') != issuer
                or response.get('emittente_id') != issuer or urlsplit(raw['url']).fragment
                or raw.get('accession') != accession[:10] + '-' + accession[10:12] + '-' + accession[12:]):
            raise ValueError('publication ticker, issuer or accession differs from SEC URL')
        filed = _iso(raw.get('filed_date'), 'publication date')
        if filed > cutoff:
            raise ValueError('publication date after scan cutoff')
        if trigger == 'filing_diff' and _iso(raw.get('report_date'), 'report date') > filed:
            raise ValueError('reporting period after publication')
        entry = {k: raw.get(k) for k in ('url', 'form', 'filed_date', 'report_date', 'emittente_id', 'accession')}
        entry['trigger'] = trigger
        body = encoded(entry)
        if raw['url'] in urls and urls[raw['url']] != body:
            raise ValueError('conflicting publication metadata for one URL')
        urls[raw['url']] = body
        if trigger in triggers:
            entry['identity'] = sha256(body.encode()).hexdigest()
            entries[entry['identity']] = entry
    return list(entries.values()), excluded


def _initial_new(manager, ticker, entries):
    current = (manager.versions.current(ticker) or {}).get('current')
    if not current:
        return set()  # Tracking owns first preparation; discovery establishes a baseline.
    snapshot = current['acquisition_snapshot']
    cutoff = _iso(snapshot['case']['as_of'], 'current model information cutoff')
    documents = ((current.get('preparation') or {}).get('provenance') or {}).get('documents') or {}
    known = {doc.get('url') for doc in documents.values()}
    known.update(row.get('source_id') for row in snapshot['case']['records'])
    return {e['identity'] for e in entries if e['url'] not in known and _iso(e['filed_date'], 'publication date') >= cutoff}


def scan_publications(manager, *, fetch=None):
    """Scan one due tracked ticker. Disabled policy performs no DB/network I/O."""
    state = manager.runtime.status()
    triggers = set(state.get('triggers', ())) & {'filing_diff', 'guidance'}
    if state.get('status') != 'configured' or not triggers:
        return {'status': 'disabled', 'reason': 'publication_triggers_not_authorized'}
    policy = manager.runtime._require(sorted(triggers)[0])
    errors = []
    with closing(_read_db(manager.jobs.db_path)) as db:
        tickers = _tracked(db, errors)
    if errors:
        return {'status': 'error', 'sources': errors}
    root = manager.runtime.data_root
    path = (root / 'valuation_discovery' / (sha256(encoded(policy).encode()).hexdigest() + '.sqlite3')).resolve()
    if not path.is_relative_to(root) or path == Path(manager.jobs.db_path).resolve():
        raise ValueError('discovery journal outside dedicated runtime location')
    store = DiscoveryStore(path, manager.jobs.clock)
    claim = store.claim(tickers)
    if claim is None:
        return {'status': 'idle', 'limitation': LIMITATION}
    ticker = claim['ticker']
    result = {'status': 'ok', 'ticker': ticker, 'limitation': LIMITATION, 'jobs': []}
    try:
        def still_tracked():
            errors = []
            with closing(_read_db(manager.jobs.db_path)) as db:
                tracked = _tracked(db, errors)
            if errors or ticker not in tracked:
                raise ValueError('tracking unavailable or ticker removed during source scan')
        def deliver():
            for pending in store.pending(claim):
                still_tracked()
                manager.runtime._unchanged(pending['trigger'], policy)
                # Queue identity survives enqueue-to-receipt crashes. Content
                # acquisition and every paid stage remain in the shared worker.
                job = manager.enqueue_refresh(ticker, pending['trigger'], 'sec-publications-v1:' + pending['identity'])
                store.delivered(claim, pending['identity'], job['id'])
                result['jobs'].append(job['id'])
        deliver()
        if fetch is None:
            from bellomberg.market_data.sec_edgar import get_publication_catalog
            fetch = get_publication_catalog
        response = fetch(ticker, days=400, max_pages=2)
        manager.runtime._unchanged(sorted(triggers)[0], policy)
        still_tracked()
        entries, excluded = publication_entries(ticker, response, manager.jobs.clock().date(), triggers)
        initial = _initial_new(manager, ticker, entries) if not claim['initialized'] else set()
        result.update(notices=len(entries), excluded=excluded,
                      new_notices=store.observe(claim, entries, initial), baseline=not bool(claim['initialized']))
        if excluded:
            result['status'] = 'partial'
        deliver()
    except Exception as exc:
        result.update(status='error', reason=type(exc).__name__ + ': ' + str(exc)[:500])
    store.finish(claim, result)
    return result
