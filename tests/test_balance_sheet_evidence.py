"""Complete reported balance coverage is distinct from an economic NWC decision."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from test_balance_detail_evidence import raw_source, source
from bellomberg.valuation.balance_sheet_evidence import (
    collect_balance_sheet, extract_balance_sheet_packet, normalize_balance_sheet,
)


def balance_raw():
    rows = []
    values = [('CashAndCashEquivalentsAtCarryingValue', 20), ('OtherAssetsCurrent', 10),
        ('AssetsCurrent', 30), ('PropertyPlantAndEquipmentNet', 50), ('Assets', 80),
        ('AccountsPayableCurrent', 12), ('LiabilitiesCurrent', 12), ('LongTermDebtNoncurrent', 8),
        ('Liabilities', 20), ('CommonStockValue', 5), ('RetainedEarningsAccumulatedDeficit', 55),
        ('StockholdersEquity', 60), ('LiabilitiesAndStockholdersEquity', 80)]
    for index, (tag, value) in enumerate(values):
        if tag == 'CommonStockValue':
            rows.append('<tr><td>Contingencies</td><td><ix:nonfraction name="us-gaap:CommitmentsAndContingencies" '
                'contextref="now" unitref="usd" xsi:nil="true"></ix:nonfraction></td></tr>')
        rows.append(f'<tr><td>{tag}</td><td><ix:nonfraction id="b{index}" name="us-gaap:{tag}" '
            f'contextref="now" unitref="usd" scale="3">{value}</ix:nonfraction></td></tr>')
    return raw_source().replace(b'<table>', ('<table>'+''.join(rows)+'</table><table>').encode(), 1)


def primary(raw=None):
    raw = balance_raw() if raw is None else raw
    doc = source(raw)
    doc['balance_sheet_fields'] = extract_balance_sheet_packet(doc, raw)
    return doc


def test_full_balance_and_note_leaves_reconcile_without_approving_economics():
    doc = primary(); before = deepcopy(doc)
    result = normalize_balance_sheet(doc)
    assert result['status'] == 'ready', result
    assert doc == before
    body = json.loads(result['documents'][0]['text'])
    leaves = body['components']
    assert len(leaves) == len({leaf['reported_tag'] for leaf in leaves}) == 7
    assert sum(Decimal(f['value_exact']) for f in leaves if f['accounting_side'] == 'asset') == 80000
    assert sum(Decimal(f['value_exact']) for f in leaves if f['accounting_side'] == 'liability') == 20000
    assert all(f['economic_classification'] == 'unreviewed' and f['model_treatment'] is None for f in leaves)
    assert body['economic_classification_approved'] is False
    assert body['nonmonetary_disclosures'][0]['value_exact'] is None
    assert body['nonmonetary_disclosures'][0]['reported_tag'] == 'us-gaap:CommitmentsAndContingencies'
    assert all(f['proof']['source_document_id'] == doc['id'] for f in leaves)
    assert 'facts' not in body and 'method_records' not in body


@pytest.mark.parametrize('before,after', [
    ('>20<', '>21<'), ('>60<', '>61<'), ('>55<', '>54<'),
    ('name="us-gaap:AssetsCurrent"', 'name="ex:UnsupportedSubtotal"'),
    ('id="b0"', 'xsi:nil="true" id="b0"'),
    ('id="b0" name="us-gaap:CashAndCashEquivalentsAtCarryingValue" contextref="now"',
     'id="b0" name="us-gaap:CashAndCashEquivalentsAtCarryingValue" contextref="prior"'),
    ('name="us-gaap:AccountsPayableCurrent"', 'name="ex:Supplies"'),
    ('<td>PropertyPlantAndEquipmentNet</td>', '<td>PropertyPlantAndEquipmentNet</td><td>999</td>'),
    ('<td>AssetsCurrent</td>', '</tr><tr><td>Untagged amount</td><td>7</td></tr><tr><td>AssetsCurrent</td>'),
])
def test_missing_conflicting_or_duplicate_coverage_fails(before, after):
    result = normalize_balance_sheet(primary(balance_raw().replace(before.encode(), after.encode(), 1)))
    assert result['status'] == 'incomplete' and result['issues'] and not result['documents']


def test_missing_balance_is_explicit_and_never_complete():
    result = normalize_balance_sheet(primary(raw_source()))
    assert result['status'] == 'not_applicable'
    assert not result['documents'] and 'not established' in result['reason']


def test_duplicate_balance_and_unsupported_layout_fail():
    raw = balance_raw()
    table = raw[raw.index(b'<table>'):raw.index(b'</table>')+8]
    result = normalize_balance_sheet(primary(raw.replace(b'</html>', table+b'</html>')))
    assert result['status'] == 'incomplete'
    raw = raw.replace(b'us-gaap:LiabilitiesAndStockholdersEquity', b'ex:UnknownClosingTotal')
    assert normalize_balance_sheet(primary(raw))['status'] == 'incomplete'


def test_note_parent_must_match_balance_even_when_both_tables_reconcile():
    raw = balance_raw().replace(b'>7.5<', b'>8.5<').replace(b'>10<', b'>11<')
    # Keep the balance at 10, while the internally reconciled note now totals 11.
    raw = raw.replace(b'scale="3">11<', b'scale="3">10<', 1)
    result = normalize_balance_sheet(primary(raw))
    assert result['status'] == 'incomplete' and 'parent' in str(result['issues'])


@pytest.mark.parametrize('derived_first', [False, True])
def test_catalog_recompiles_and_rejects_forged_ledger(derived_first):
    from bellomberg.valuation.input_preparation import _catalog
    doc = primary(); derived = normalize_balance_sheet(doc)['documents'][0]
    docs = [derived, doc] if derived_first else [doc, derived]
    catalog, issues, _ = _catalog(docs, date(2026, 2, 2))
    assert not issues and derived['id'] in catalog
    body = json.loads(derived['text']); body['components'][0]['economic_classification'] = 'approved'
    derived['text'] = json.dumps(body); derived['sha256'] = sha256(derived['text'].encode()).hexdigest()
    catalog, issues, _ = _catalog(docs, date(2026, 2, 2))
    assert issues and derived['id'] not in catalog


def test_packet_source_note_binding():
    doc = primary()
    doc['balance_detail_fields']['sha256'] = '0'*64
    assert normalize_balance_sheet(doc)['status'] == 'incomplete'


@pytest.mark.parametrize('mutation', ['dimension', 'currency', 'issuer', 'period', 'namespace', 'extra_note', 'unit_missing'])
def test_wrong_entity_scope_currency_and_unlinked_notes_never_establish_coverage(mutation):
    raw = balance_raw()
    if mutation == 'dimension':
        raw = raw.replace(b'</xbrli:entity>', b'<xbrli:segment>Division</xbrli:segment></xbrli:entity>', 1)
    elif mutation == 'currency':
        raw = raw.replace(b'<table>', b'<xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit><table>', 1)
        raw = raw.replace(b'unitref="usd" scale="3">20', b'unitref="eur" scale="3">20', 1)
    elif mutation == 'issuer':
        raw = raw.replace(b'>123<', b'>321<')
    elif mutation == 'period':
        raw = raw.replace(b'>2025-12-31<', b'>2025-12-30<')
    elif mutation == 'namespace':
        raw = raw.replace(b'http://fasb.org/us-gaap/2025', b'https://example.com/not-gaap')
    elif mutation == 'extra_note':
        # Both printed tables still reconcile, but the note has no balance parent.
        raw = raw.replace(b'<td>OtherAssetsCurrent</td><td><ix:nonfraction id="b1" name="us-gaap:OtherAssetsCurrent"',
                          b'<td>OtherAssetsCurrent</td><td><ix:nonfraction id="b1" name="ex:UnclassifiedCurrentAsset"')
    else:
        raw = raw.replace(b'unitref="usd" scale="3">20', b'unitref="missing" scale="3">20', 1)
        with pytest.raises(ValueError, match='unit missing'):
            primary(raw)
        return
    assert normalize_balance_sheet(primary(raw))['status'] == 'incomplete'


def test_no_note_table_preserves_reported_aggregate_and_explicit_coverage_limit():
    raw = balance_raw()
    start = raw.index(b'<table>', raw.index(b'</table>')+8)
    raw = raw[:start]+b'</html>'
    doc = primary(raw)
    result = normalize_balance_sheet(doc)
    assert result['status'] == 'ready', result
    body = json.loads(result['documents'][0]['text'])
    aggregate = next(c for c in body['components'] if c['reported_tag'] == 'us-gaap:OtherAssetsCurrent')
    assert aggregate['value_exact'] == '10000' and aggregate['model_treatment'] is None
    assert body['economic_classification_approved'] is False


def test_declared_monetary_contingency_is_not_silently_dropped():
    raw = balance_raw().replace(b'xsi:nil="true"></ix:nonfraction>', b'>3</ix:nonfraction>')
    assert normalize_balance_sheet(primary(raw))['status'] == 'incomplete'


def test_collect_and_view_preserve_original_evidence(tmp_path):
    from bellomberg.valuation.preparation_view import select_stage_view
    doc = source(balance_raw()); before = deepcopy(doc)
    path = tmp_path/'source.htm'; path.write_bytes(balance_raw())
    doc['archive_path'] = str(path)
    result = collect_balance_sheet(doc, tmp_path)
    assert result['status'] == 'ready', result
    assert {k: v for k, v in doc.items() if k != 'archive_path'} == before
    doc['balance_sheet_fields'] = result['packets'][doc['id']]
    doc['balance_detail_fields'] = result['detail_packets'][doc['id']]
    dossier = {'ticker': 'SYNTH', 'method_id': 'operating_fcff', 'documents': [doc, *result['documents']]}
    snapshot = deepcopy(dossier)
    view = select_stage_view(dossier, stage='model')
    assert dossier == snapshot
    assert 'balance_sheet_fields' not in view['documents'][0]
    assert view['documents'][1] == result['documents'][0]
    assert view['stage_view']['documents'][0]['balance_sheet_layout_packet']['view'] == 'excluded'
    assert collect_balance_sheet(doc, tmp_path/'outside')['status'] == 'incomplete'


@pytest.mark.parametrize('broken', [False, True])
def test_common_collector_emits_recompilable_coverage_or_stops_before_ai(tmp_path, monkeypatch, broken):
    from bellomberg.valuation import preparation_earnings, quotation_evidence
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from test_preparation_sources import _setup
    raw = balance_raw().replace(b'>20<', b'>21<', 1) if broken else balance_raw()
    origin = _setup(monkeypatch, tmp_path); origin.update(source(raw))
    (tmp_path/'statement.html').write_bytes(raw)
    monkeypatch.setattr(preparation_earnings, 'collect_earnings_evidence',
        lambda *a, **k: {'status': 'not_requested', 'documents': [], 'issues': []})
    monkeypatch.setattr(quotation_evidence, 'listing_identity_document',
        lambda *a, **k: {'status': 'ready', 'documents': [{'id': 'listing', 'text': '{}'}], 'issues': []})
    before = deepcopy(origin)
    report = collect_preparation_evidence('SYNTH', as_of='2026-02-02', archive_root=tmp_path,
        method_id='operating_fcff', price_fetch=lambda ticker, on:
        {'symbol': ticker, 'date': on, 'close': 12.5, 'currency': 'EUR'})
    assert origin == before
    if broken:
        assert not report['preparation_ready'] and not report['documents']
        assert report['balance_sheet']['status'] == 'incomplete'
    else:
        assert report['preparation_ready'] and report['balance_sheet']['status'] == 'ready'
        docs = [d for d in report['documents'] if d['id'] in
                (origin['id'], 'balance-details-'+origin['id'], 'balance-sheet-'+origin['id'])]
        catalog, issues, _ = _catalog(docs, date(2026, 2, 2))
        assert not issues and len(catalog) == 3
