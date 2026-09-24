"""Synthetic inline note components, independently reconciled to reported totals."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.balance_detail_evidence import (
    extract_balance_detail_packet, normalize_balance_details,
)


def raw_source():
    header = '''<html xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:dei="http://xbrl.sec.gov/dei/2025"
 xmlns:ex="https://example.com/issuer/2025" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">
 <ix:nonnumeric name="dei:EntityRegistrantName">Synthetic Industrial Issuer</ix:nonnumeric>'''
    for key, end in [('now', '2025-12-31'), ('prior', '2024-12-31')]:
        header += f'''<xbrli:context id="{key}"><xbrli:entity>
 <xbrli:identifier scheme="http://www.sec.gov/CIK">123</xbrli:identifier></xbrli:entity>
 <xbrli:period><xbrli:instant>{end}</xbrli:instant></xbrli:period></xbrli:context>'''
    header += '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    rows = []
    for index, (label, tag, now, prior, fmt) in enumerate([
        ('Supplies', 'ex:Supplies', '7.5', '1.5', 'num-dot-decimal'),
        ('Prepayments', 'us-gaap:PrepaidExpenseCurrent', '2.5', '0.5', 'num-dot-decimal'),
        ('Adjustment', 'ex:Adjustment', '—', '—', 'fixed-zero'),
        ('Asset subtotal', 'us-gaap:OtherAssetsCurrent', '10', '2', 'num-dot-decimal'),
    ]):
        cells = ''.join(f'<td><ix:nonfraction id="f{index}-{period}" name="{tag}" '
            f'contextref="{period}" unitref="usd" format="ixt:{fmt}" scale="3">{value}</ix:nonfraction></td>'
            for period, value in [('now', now), ('prior', prior)])
        rows.append('<tr><td>'+label+'</td>'+cells+'</tr>')
    return (header+'<table><tr><th>Detail</th><th>2025</th><th>2024</th></tr>'+
            ''.join(rows)+'</table></html>').encode()


def source(raw=None):
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    raw = raw_source() if raw is None else raw
    text = estrai_testo('report.htm', contenuto=raw)['testo']
    digest = sha256(raw).hexdigest()
    doc = {'id': digest, 'document_sha256': digest, 'text': text,
        'sha256': sha256(text.encode()).hexdigest(),
        'url': 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm',
        'published_at': '2026-02-01', 'metadata': {'issuer': 'Synthetic Industrial Issuer',
            'form': '10-K', 'emittente_id': 'CIK:0000000123',
            'accession': '0000000123-26-000001', 'report_date': '2025-12-31'}}
    doc['balance_detail_fields'] = extract_balance_detail_packet(doc, raw)
    return doc


def test_components_are_dated_scaled_and_reconciled_without_economic_classification():
    primary = source(); before = deepcopy(primary)
    result = normalize_balance_details(primary)
    assert result['status'] == 'ready', result
    assert primary == before
    document = result['documents'][0]; body = json.loads(document['text'])
    group = body['groups'][0]
    assert group['parent']['value_exact'] == '10000'
    assert [item['value_exact'] for item in group['components']] == ['7500', '2500', '0']
    assert all(item['unit'] == 'USD' and item['end'] == '2025-12-31' for item in group['components'])
    assert group['components'][0]['reported_tag'] == 'ex:Supplies'
    assert group['components'][0]['namespace'] == 'https://example.com/issuer/2025'
    assert group['components'][2]['explicit_zero'] is True
    assert body['economic_classification_approved'] is False
    assert 'facts' not in body and 'method_records' not in body
    assert 'complete working capital' in document['metadata']['limitation']


@pytest.mark.parametrize('before,after', [
    ('>7.5<', '>7.6<'), ('>123<', '>456<'), ('>2025-12-31<', '>2025-12-30<'),
    ('name="ex:Supplies"', 'xsi:nil="true" name="ex:Supplies"'),
    ('ixt:num-dot-decimal', 'ixt:unknown'),
    ('contextref="now" unitref="usd" format="ixt:num-dot-decimal" scale="3">7.5',
     'contextref="prior" unitref="usd" format="ixt:num-dot-decimal" scale="3">7.5'),
    ('>Synthetic Industrial Issuer<', '>Different Issuer<'),
])
def test_wrong_amount_identity_period_nil_and_transformation_are_incomplete(before, after):
    primary = source(raw_source().replace(before.encode(), after.encode()))
    result = normalize_balance_details(primary)
    assert result['status'] == 'incomplete' and result['issues'] and not result['documents']


def test_packet_and_raw_source_tampering_are_rejected():
    primary = source()
    with pytest.raises(ValueError, match='bytes'):
        extract_balance_detail_packet(primary, raw_source()+b' ')
    primary['balance_detail_fields']['issuer'] = 'Modified'
    assert normalize_balance_details(primary)['status'] == 'incomplete'


def test_duplicate_context_and_namespace_override_are_rejected():
    raw = raw_source().replace(b'</html>', b'<xbrli:context id="now"/></html>')
    with pytest.raises(ValueError, match='duplicate'):
        source(raw)
    raw = raw_source().replace(b'<table>', b'<table xmlns:ex="https://example.com/other">')
    with pytest.raises(ValueError, match='namespace'):
        source(raw)


@pytest.mark.parametrize('derived_first', [False, True])
def test_catalog_recompiles_components_and_rejects_forged_output(derived_first):
    from bellomberg.valuation.input_preparation import _catalog
    primary = source(); derived = normalize_balance_details(primary)['documents'][0]
    docs = [derived, primary] if derived_first else [primary, derived]
    catalog, issues, _ = _catalog(docs, date(2026, 2, 2))
    assert not issues and derived['id'] in catalog
    changed = json.loads(derived['text']); changed['groups'][0]['parent']['value_exact'] = '1'
    derived['text'] = json.dumps(changed); derived['sha256'] = sha256(derived['text'].encode()).hexdigest()
    catalog, issues, _ = _catalog(docs, date(2026, 2, 2))
    assert issues and derived['id'] not in catalog


def test_archive_collector_rechecks_bytes_and_path_and_discloses_missing_tables(tmp_path):
    from bellomberg.valuation.balance_detail_evidence import collect_balance_details
    primary = source(); path = tmp_path / 'primary.htm'; path.write_bytes(raw_source())
    primary['archive_path'] = str(path)
    result = collect_balance_details(primary, tmp_path)
    assert result['status'] == 'ready' and primary['id'] in result['packets']
    assert 'archive_path' not in result['documents'][0]
    path.write_bytes(raw_source()+b' ')
    assert collect_balance_details(primary, tmp_path)['status'] == 'incomplete'
    assert collect_balance_details(primary, tmp_path/'other')['status'] == 'incomplete'
    raw = raw_source().replace(b'us-gaap:OtherAssetsCurrent', b'ex:UnknownSubtotal')
    path.write_bytes(raw); primary = source(raw); primary['archive_path'] = str(path)
    result = collect_balance_details(primary, tmp_path)
    assert result['status'] == 'not_applicable' and not result['documents']
    assert 'coverage not established' in result['reason']


@pytest.mark.parametrize('method,form,requested', [
    ('operating_fcff', '10-K', True), ('operating_fcff', '10-Q', True),
    ('bank_residual_income', '10-K', False), ('operating_fcff', '6-K', False),
])
def test_common_collector_requests_only_current_us_operating_notes(tmp_path, monkeypatch, method, form, requested):
    from bellomberg.valuation import balance_detail_evidence as module
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from test_preparation_sources import _setup
    primary = _setup(monkeypatch, tmp_path); primary['metadata']['form'] = form
    calls = []
    def collect(doc, root):
        calls.append(doc['id'])
        return {'status': 'incomplete', 'packets': {}, 'documents': [],
                'issues': [{'source': 'balance_details_v1', 'reason': 'Synthetic subtotal conflict'}]}
    monkeypatch.setattr(module, 'collect_balance_details', collect)
    report = collect_preparation_evidence('SYNTH', as_of='2026-02-02', archive_root=tmp_path,
        method_id=method, price_fetch=lambda ticker, on: {'symbol': ticker, 'date': on, 'close': 12.5, 'currency': 'EUR'})
    assert calls == ([primary['id']] if requested else [])
    assert report['balance_details']['status'] == ('incomplete' if requested else 'not_requested')
    if requested:
        assert any('subtotal conflict' in item['reason'] for item in report['issues'])
        assert not report['preparation_ready'] and not report['documents']


@pytest.mark.parametrize('mutation', ['currency', 'dimensions', 'untagged', 'duplicate', 'negative', 'nil_zero'])
def test_conflicting_scope_currency_missing_and_duplicate_rows_fail_closed(mutation):
    raw = raw_source()
    if mutation == 'currency':
        raw = raw.replace(b'<table>', b'<xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit><table>')
        raw = raw.replace(b'unitref="usd"', b'unitref="eur"', 1)
    elif mutation == 'dimensions':
        raw = raw.replace(b'</xbrli:entity>', b'<xbrli:segment>Segment-specific observation</xbrli:segment></xbrli:entity>', 1)
    elif mutation == 'untagged':
        raw = raw.replace(b'<tr><td>Prepayments', b'<tr><td>Unclassified item</td><td>0</td></tr><tr><td>Prepayments')
    elif mutation == 'duplicate':
        raw = raw.replace(b'us-gaap:PrepaidExpenseCurrent', b'ex:Supplies')
    elif mutation == 'negative':
        raw = raw.replace(b'name="ex:Supplies"', b'sign="-" name="ex:Supplies"')
    else:
        raw = raw.replace(b'name="ex:Adjustment"', b'xsi:nil="true" name="ex:Adjustment"')
    result = normalize_balance_details(source(raw))
    assert result['status'] == 'incomplete' and not result['documents'] and result['issues']


def test_currency_and_signed_amounts_are_preserved_without_usd_or_zero_fallback():
    raw = raw_source().replace(b'iso4217:USD', b'iso4217:EUR')
    raw = raw.replace(b'name="ex:Supplies"', b'sign="-" name="ex:Supplies"')
    raw = raw.replace(b'scale="3">10<', b'sign="-" scale="3">5<')
    result = normalize_balance_details(source(raw))
    assert result['status'] == 'ready', result
    group = json.loads(result['documents'][0]['text'])['groups'][0]
    assert group['parent']['value_exact'] == '-5000' and group['parent']['unit'] == 'EUR'
    assert group['components'][0]['value_exact'] == '-7500'


def test_prompt_excludes_raw_packet_but_keeps_all_normalized_components():
    from bellomberg.valuation.preparation_view import select_stage_view
    primary = source(); derived = normalize_balance_details(primary)['documents'][0]
    dossier = {'ticker': 'SYNTH', 'method_id': 'operating_fcff', 'documents': [primary, derived]}
    before = deepcopy(dossier); view = select_stage_view(dossier, 'model')
    assert dossier == before
    assert 'balance_detail_fields' not in view['documents'][0]
    assert view['documents'][1] == derived
    report = view['stage_view']['documents'][0]['balance_layout_packet']
    assert report['view'] == 'excluded' and report['original_bytes'] > 0 and len(report['sha256']) == 64


def test_common_collector_retains_packet_and_recompilable_document(tmp_path, monkeypatch):
    from bellomberg.valuation import preparation_earnings, quotation_evidence
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from test_preparation_sources import _setup
    primary = _setup(monkeypatch, tmp_path); primary.update(source())
    (tmp_path / 'statement.html').write_bytes(raw_source())
    monkeypatch.setattr(preparation_earnings, 'collect_earnings_evidence',
        lambda *a, **k: {'status': 'not_requested', 'documents': [], 'issues': []})
    monkeypatch.setattr(quotation_evidence, 'listing_identity_document',
        lambda *a, **k: {'status': 'ready', 'documents': [{'id': 'listing', 'text': '{}'}], 'issues': []})
    before = deepcopy(primary)
    report = collect_preparation_evidence('SYNTH', as_of='2026-02-02', archive_root=tmp_path,
        method_id='operating_fcff', price_fetch=lambda ticker, on:
        {'symbol': ticker, 'date': on, 'close': 12.5, 'currency': 'EUR'})
    assert primary == before and report['preparation_ready']
    assert report['balance_details']['status'] == 'ready'
    docs = [d for d in report['documents'] if d['id'] in (primary['id'], 'balance-details-'+primary['id'])]
    assert len(docs) == 2 and all('archive_path' not in d for d in docs)
    catalog, issues, _ = _catalog(docs, date(2026, 2, 2))
    assert not issues and len(catalog) == 2

