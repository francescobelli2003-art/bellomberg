"""Synthetic primary-source files; no market or portfolio data and no network."""
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json

import pytest
from openpyxl import Workbook


DAY = '2026-02-02'
NOW = datetime(2026, 2, 2, 18, tzinfo=timezone.utc)


def erp_file(*, day=datetime(2026, 2, 1), duplicate=False, value=.05, wrong_header=False):
    wb = Workbook(); ws = wb.active; ws.title = 'Historical ERP'
    ws.append(['Start of month', 'T.Bond Rate', '$ Riskfree Rate',
               'ERP (T12m)' if not wrong_header else 'Unknown premium',
               'ERP (T12m) with adj riskfree rate', 'ERP (T12 m with sustainable payout)'])
    row = [day, .04, .038, value, .052, .049]
    ws.append(row)
    if duplicate: ws.append(row)
    stream = BytesIO(); wb.save(stream); wb.close()
    return stream.getvalue()


def document(kind, raw, *, retrieved_at=NOW.isoformat()):
    from bellomberg.valuation.market_reference_evidence import normalize_reference, reference_urls
    url = reference_urls(DAY)[kind]
    return normalize_reference(raw, kind=kind, as_of=DAY,
        retrieval={'url': url, 'document_sha256': sha256(raw).hexdigest(), 'retrieved_at': retrieved_at})


def test_treasury_preserves_date_unit_and_source_line_without_default():
    raw = b'Date,10 Yr\n02/02/2026,4.10\n01/30/2026,4.00\n'
    doc = document('treasury', raw)
    fact, = json.loads(doc['text'])['facts']
    assert fact['value'] == .041 and fact['end'] == DAY and fact['unit'] == 'ratio'
    assert fact['source_value'] == '4.10' and fact['source_unit'] == 'percent'
    assert fact['locator'] == {'row': 2, 'date_column': 'Date', 'value_column': '10 Yr'}
    assert doc['published_at'] is None and doc['available_at'] == DAY
    assert doc['availability_basis'] == 'observed_download'


def test_erp_variants_and_paired_rates_remain_distinct_with_exact_cells():
    doc = document('erp', erp_file())
    facts = json.loads(doc['text'])['facts']
    assert len(facts) == 5
    assert len({r['concept'] for r in facts}) == 5
    assert all(r['end'] == '2026-02-01' and r['unit'] == 'ratio' for r in facts)
    standard = next(r for r in facts if r['concept'] == 'erp_trailing_twelve_months')
    assert standard['value'] == .05
    assert standard['locator'] == {'sheet': 'Historical ERP', 'value_cell': 'D2', 'date_cell': 'A2', 'header_cell': 'D1'}
    assert json.loads(doc['text'])['limitation']


def test_erp_explicit_text_date_preserves_the_month_and_source_cell():
    doc = document('erp', erp_file(day='1-Feb-26'))
    facts = json.loads(doc['text'])['facts']
    assert all(row['end'] == '2026-02-01' for row in facts)
    assert all(row['locator']['date_cell'] == 'A2' for row in facts)


@pytest.mark.parametrize('day', ['2-Feb-26', 'Feb 2026', '02/01/26', '1-XYZ-26'])
def test_erp_ambiguous_or_non_month_start_text_date_is_rejected(day):
    with pytest.raises(ValueError): document('erp', erp_file(day=day))


@pytest.mark.parametrize('raw', [b'Date,10 Yr\n02/02/2026,N/A\n',
    b'Date,10 Yr\n02/02/2026,NaN\n', b'Date,10 Yr\n02/02/2026,4.1\n02/02/2026,4.2\n',
    b'Date,10 Yr\n01/20/2026,4.1\n', b'Date,Wrong\n02/02/2026,4.1\n'])
def test_treasury_missing_ambiguous_nonfinite_or_stale_is_rejected(raw):
    with pytest.raises(ValueError): document('treasury', raw)


@pytest.mark.parametrize('kwargs', [{'duplicate': True}, {'value': None}, {'value': 'not numeric'},
    {'wrong_header': True}, {'day': datetime(2025, 10, 1)}])
def test_erp_missing_ambiguous_or_stale_is_rejected(kwargs):
    with pytest.raises(ValueError): document('erp', erp_file(**kwargs))


@pytest.mark.parametrize('fault', ['hash', 'url', 'future_download'])
def test_raw_bytes_and_observed_availability_are_required(fault):
    from bellomberg.valuation.market_reference_evidence import normalize_reference, reference_urls
    raw = b'Date,10 Yr\n02/02/2026,4.10\n'
    receipt = {'url': reference_urls(DAY)['treasury'], 'document_sha256': sha256(raw).hexdigest(), 'retrieved_at': NOW.isoformat()}
    if fault == 'hash': receipt['document_sha256'] = '0' * 64
    if fault == 'url': receipt['url'] = 'https://example.org/unverified.csv'
    if fault == 'future_download': receipt['retrieved_at'] = '2026-02-03T00:00:00+00:00'
    with pytest.raises(ValueError): normalize_reference(raw, kind='treasury', as_of=DAY, retrieval=receipt)


def test_collector_uses_existing_trusted_downloader_and_never_builds_rates(tmp_path):
    from bellomberg.valuation.market_reference_evidence import collect_market_references, reference_urls
    calls = []
    urls = reference_urls(DAY)
    def download(url, dest, **kwargs):
        calls.append((url, kwargs))
        raw = erp_file() if url == urls['erp'] else b'Date,10 Yr\n02/02/2026,4.10\n'
        path = tmp_path / ('erp.xlsx' if url == urls['erp'] else 'treasury.csv'); path.write_bytes(raw)
        return {'stato': 'ok', 'url_finale': url, 'path': str(path), 'sha256': sha256(raw).hexdigest()}
    result = collect_market_references(currency='USD', as_of=DAY, archive_root=tmp_path, download=download, now=NOW)
    assert result['status'] == 'ready' and len(result['documents']) == 2
    assert all(c[1]['public_only'] is True and len(c[1]['host_consentiti']) == 1 for c in calls)
    assert all('wacc' not in json.loads(d['text']) for d in result['documents'])


def test_unsupported_currency_is_explicit_and_never_becomes_usd(tmp_path):
    from bellomberg.valuation.market_reference_evidence import collect_market_references
    result = collect_market_references(currency='EUR', as_of=DAY, archive_root=tmp_path,
        download=lambda *a, **k: pytest.fail('unsupported currency must not download USD'), now=NOW)
    assert result['status'] == 'incomplete' and not result['documents'] and result['issues']


@pytest.mark.parametrize('status', ['ready', 'partial', 'incomplete'])
def test_issuer_beta_reference_joins_market_catalog_with_declared_gaps(tmp_path, monkeypatch, status):
    from bellomberg.valuation.market_reference_evidence import collect_market_references, reference_urls
    calls = []
    expected = {'status': status, 'documents': [{'id': 'statistic'}] if status != 'incomplete' else [],
                'issues': [] if status == 'ready' else [{'source': 'beta', 'reason': 'missing history'}],
                'limitation': 'reference only'}
    def beta(ticker, **kwargs):
        calls.append((ticker, kwargs))
        return expected
    monkeypatch.setattr('bellomberg.valuation.beta_reference_evidence.collect_beta_reference', beta)
    def download(url, *a, **k):
        raw = erp_file() if url == reference_urls(DAY)['erp'] else b'Date,10 Yr\n02/02/2026,4.10\n'
        path = tmp_path / ('erp.xlsx' if url == reference_urls(DAY)['erp'] else 'treasury.csv')
        path.write_bytes(raw)
        return {'stato': 'ok', 'url_finale': url, 'path': str(path), 'sha256': sha256(raw).hexdigest()}
    result = collect_market_references(currency='USD', as_of=DAY, archive_root=tmp_path,
        download=download, now=NOW, ticker='SYNTH')
    assert calls[0][0] == 'SYNTH' and calls[0][1]['currency'] == 'USD'
    assert result['beta_reference']['status'] == status
    assert result['status'] == ('ready' if status == 'ready' else 'partial')
    assert result['issues'] == expected['issues']
    assert len(result['documents']) == (3 if status != 'incomplete' else 2)


@pytest.mark.parametrize('fault', ['outside', 'hash', 'redirect'])
def test_collector_rejects_unattested_source_bytes(tmp_path, fault):
    from bellomberg.valuation.market_reference_evidence import collect_market_references
    root = tmp_path / 'archive'; root.mkdir()
    raw = b'Date,10 Yr\n02/02/2026,4.10\n'
    path = (tmp_path if fault == 'outside' else root) / 'source'; path.write_bytes(raw)
    def download(url, *a, **k):
        return {'stato': 'ok', 'url_finale': 'https://example.org/redirect' if fault == 'redirect' else url,
                'path': str(path), 'sha256': '0' * 64 if fault == 'hash' else sha256(raw).hexdigest()}
    result = collect_market_references(currency='USD', as_of=DAY, archive_root=root, download=download, now=NOW)
    assert result['status'] == 'incomplete' and not result['documents'] and len(result['issues']) == 2
