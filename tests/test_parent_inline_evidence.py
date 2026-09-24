"""Synthetic primary inline tags through the common bank input compiler."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.parent_inline_evidence import extract_parent_packet, normalize_parent_inline
from bellomberg.valuation.input_preparation import _catalog, _fact_proof


def raw_source():
    return '''<html xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:srt="http://fasb.org/srt/2026" xmlns:us-gaap="http://fasb.org/us-gaap/2026" xmlns:dei="http://xbrl.sec.gov/dei/2026"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">
 <ix:nonnumeric name="dei:EntityRegistrantName">SYNTH-BANK-PARENT</ix:nonnumeric>
 <xbrli:context id="parent"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">123</xbrli:identifier>
 <xbrli:segment><xbrldi:explicitmember dimension="srt:ConsolidatedEntitiesAxis">srt:ParentCompanyMember</xbrldi:explicitmember></xbrli:segment>
 </xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
 <xbrli:context id="issuer"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">123</xbrli:identifier></xbrli:entity>
 <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
 <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
 <ix:nonfraction id="equity" name="us-gaap:StockholdersEquity" contextref="parent" unitref="usd" format="ixt:num-dot-decimal" scale="6">10</ix:nonfraction>
 <ix:nonfraction id="cash" name="us-gaap:CashAndCashEquivalentsAtCarryingValue" contextref="parent" unitref="usd" format="ixt:num-dot-decimal" scale="6">10</ix:nonfraction>
 <ix:nonfraction id="preferred" name="us-gaap:PreferredStockValue" contextref="issuer" unitref="usd" format="ixt:fixed-zero" scale="3">none</ix:nonfraction>
 </html>'''.encode()


def source(raw=None):
    raw = raw_source() if raw is None else raw
    digest = sha256(raw).hexdigest()
    doc = {'id': digest, 'document_sha256': digest,
        'text': 'Synthetic audited parent statements.', 'sha256': sha256(b'Synthetic audited parent statements.').hexdigest(),
        'url': 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm',
        'published_at': '2026-02-01', 'metadata': {'form': '10-K', 'emittente_id': 'CIK:0000000123',
        'accession': '0000000123-26-000001', 'report_date': '2025-12-31'}}
    doc['inline_parent_fields'] = extract_parent_packet(doc, raw)
    return doc


def normal(doc):
    return normalize_parent_inline(doc, expected_report_date='2025-12-31')


def observation(doc, index):
    fact = json.loads(doc['text'])['facts'][index]
    return {'evidence_ids': [doc['id']], 'evidence_pointer': {'value': f'/facts/{index}/value',
            'unit': f'/facts/{index}/unit', 'period': f'/facts/{index}/end'},
            'quoted_value': fact['value'], 'quoted_unit': fact['unit']}


def test_scaled_parent_balances_and_issuer_explicit_zero_are_recompiled():
    original = source(); result = normal(original)
    assert result['status'] == 'ready', result
    doc = result['documents'][0]; facts = json.loads(doc['text'])['facts']
    assert [f['value'] for f in facts] == [10_000_000, 0, 10_000_000]
    assert facts[1]['proof']['reported_scope'] == 'issuer_capital'
    catalog, issues, _ = _catalog([original, doc], date(2026, 9, 10))
    assert not issues and doc['id'] in catalog
    cash = {**observation(doc, 2), 'value': 10}
    assert _fact_proof('capital.parent_opening_cash', cash, [doc], 'USD million', '2025-12-31', expected_entity='SYNTH-BANK-PARENT') is None
    assert _fact_proof('opening_common_equity', cash, [doc], 'USD million', '2025-12-31', expected_entity='SYNTH-BANK-PARENT') is not None


@pytest.mark.parametrize('before,after', [
    ('srt:ParentCompanyMember', 'srt:SubsidiariesMember'),
    ('>123<', '>999<'), ('iso4217:USD<', 'iso4217:EUR<'),
    ('2025-12-31', '2024-12-31'),
    ('format="ixt:fixed-zero" scale="3">none', 'format="ixt:num-dot-decimal" scale="3">1'),
    ('id="cash"', 'xsi:nil="true" id="cash"'),
    ('http://fasb.org/srt/2026', 'https://example.org/srt/2026'),
    ('ixt:num-dot-decimal', 'ixt:unknown'),
])
def test_wrong_scope_issuer_unit_date_or_unverified_numeric_fact_is_incomplete(before, after):
    assert normal(source(raw_source().replace(before.encode(), after.encode())))['status'] == 'incomplete'


def test_source_bytes_duplicate_contexts_and_conflicting_values_are_rejected():
    original = source()
    with pytest.raises(ValueError, match='source bytes'):
        extract_parent_packet(original, raw_source() + b' ')
    raw = raw_source().replace(b'</html>', b'<xbrli:context id="parent"></xbrli:context></html>')
    with pytest.raises(ValueError, match='duplicate'):
        source(raw)
    raw = raw_source().replace(b'</html>', b'<ix:nonfraction name="us-gaap:StockholdersEquity" contextref="parent" unitref="usd" scale="6">11</ix:nonfraction></html>')
    assert normal(source(raw))['status'] == 'incomplete'


def test_forged_normalization_or_packet_cannot_pass_catalog():
    original = source(); doc = normal(original)['documents'][0]
    changed = deepcopy(doc); parsed = json.loads(changed['text']); parsed['facts'][0]['value'] += 1
    changed['text'] = json.dumps(parsed); changed['sha256'] = sha256(changed['text'].encode()).hexdigest()
    assert _catalog([original, changed], date(2026, 9, 10))[1]
    original['inline_parent_fields']['issuer'] = 'ANOTHER-ISSUER'
    assert _catalog([original, doc], date(2026, 9, 10))[1]


def test_locally_redefined_accounting_namespace_cannot_be_erased_by_extraction():
    raw = raw_source().replace(b'<ix:nonfraction id="equity"',
        b'<div xmlns:us-gaap="https://example.org/not-gaap"><ix:nonfraction id="equity"')
    raw = raw.replace(b'</html>', b'</div></html>')
    with pytest.raises(ValueError, match='namespace'):
        source(raw)


def test_ambiguous_period_cannot_prove_an_opening_balance():
    raw = raw_source().replace(b'<xbrli:instant>2025-12-31</xbrli:instant>',
        b'<xbrli:instant>2025-12-31</xbrli:instant><xbrli:instant>2024-12-31</xbrli:instant>')
    assert normal(source(raw))['status'] == 'incomplete'


def test_sec_parent_balances_participate_in_scoped_consolidation():
    from test_bank_consolidation_evidence import _case, _proof
    docs, item, context = _case()
    original = source(); parent = normal(original)['documents'][0]
    old_id = item['calculation']['terms']['parent']['evidence_ids'][0]
    docs = [d for d in docs if d['id'] != old_id] + [original, parent]
    item['calculation']['terms']['parent'] = {'value': 10., 'evidence_ids': [parent['id']],
        'calculation': {'operation': 'sum', 'terms': [
            {'coefficient': 1, **observation(parent, 0)}, {'coefficient': -1, **observation(parent, 1)}]}}
    item['evidence_ids'] = [parent['id'] if i == old_id else i for i in item['evidence_ids']]
    item['value'] = 0.  # Group 100 less parent 10 less bank 90, all synthetic.
    context['model']['opening_parent_equity']['value'] = 10.
    context['model']['legal_structure']['value']['parent_entity'] = 'SYNTH-BANK-PARENT'
    catalog, issues, _ = _catalog(docs, date(2026, 9, 10))
    assert not issues and _proof(item, list(catalog.values()), context) is None
    context['model']['legal_structure']['value']['parent_entity'] = 'OTHER-PARENT'
    assert _proof(item, list(catalog.values()), context)


def test_common_collector_preserves_packet_and_source_before_dropping_archive_path(tmp_path, monkeypatch):
    from bellomberg.valuation import valuation_sources, preparation_exhibits, preparation_earnings
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from bellomberg.valuation import quotation_evidence
    original = source(); path = tmp_path / 'statement.htm'; path.write_bytes(raw_source())
    original['archive_path'] = str(path)
    monkeypatch.setattr(valuation_sources, 'collect_documents', lambda *a, **k: {
        'status': 'ready', 'documents': [original], 'issues': []})
    monkeypatch.setattr(valuation_sources, 'company_facts_documents', lambda *a, **k: {'status': 'ready', 'documents': [{'id': 'synthetic-xbrl'}], 'issues': []})
    for module, name in ((preparation_exhibits, 'collect_preparation_exhibits'), (preparation_earnings, 'collect_earnings_evidence')):
        monkeypatch.setattr(module, name, lambda *a, **k: {'status': 'ready', 'documents': [], 'issues': []})
    monkeypatch.setattr(quotation_evidence, 'listing_identity_document', lambda *a, **k: {'status': 'ready', 'documents': [{'id': 'synthetic-listing'}], 'issues': []})
    result = collect_preparation_evidence('SYNTH', as_of='2026-09-10', archive_root=tmp_path,
        price_fetch=lambda ticker,on: {'symbol':ticker, 'date':on, 'currency':'USD', 'close':10})
    assert result['parent_inline']['status'] == 'ready' and result['preparation_ready']
    primary = next(d for d in result['documents'] if d['id'] == original['id'])
    parent = next(d for d in result['documents'] if d['id'].startswith('sec-parent-'))
    assert primary['inline_parent_fields'] == original['inline_parent_fields']
    assert 'archive_path' not in primary and not _catalog([primary, parent], date(2026, 9, 10))[1]
    path.write_bytes(b'changed source')
    result = collect_preparation_evidence('SYNTH', as_of='2026-09-10', archive_root=tmp_path,
        price_fetch=lambda ticker,on: {'symbol':ticker, 'date':on, 'currency':'USD', 'close':10})
    assert result['parent_inline']['status'] == 'unavailable' and result['parent_inline']['issues']
    assert not any(d['id'].startswith('sec-parent-') for d in result['documents'])


def test_common_bank_preparer_accepts_parent_primary_source_and_generates_workbook(tmp_path):
    from test_input_preparation_bank import _documents, _propose, _estimate, DAY, providers_for
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.dcf_engine import generate_valuation
    original = source(); doc = normal(original)['documents'][0]
    def propose(dossier, contract):
        plan = _propose(dossier, contract)
        common = {**_estimate(10, 'Synthetic SEC parent book minus explicit preferred zero'), 'kind': 'historical',
            'evidence_ids': [doc['id']], 'calculation': {'operation': 'sum', 'terms': [
                {'coefficient': 1, **observation(doc, 0)}, {'coefficient': -1, **observation(doc, 1)}]}}
        plan['model']['opening_parent_equity'] = common
        for scenario in plan['scenarios'].values():
            scenario['capital.parent_opening_cash'] = {**_estimate(10, 'Observed synthetic parent cash; transfer limits remain separate'),
                'kind': 'historical', **observation(doc, 2)}
        return plan
    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))
    prepared = prepare_method_inputs(bundle, documents=[*_documents(), original, doc], propose=propose)
    assert prepared['status'] == 'prepared', prepared['issues']
    result = generate_valuation('SYNTH-BANK', prepared_bundle=prepared['bundle'], output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'], result.get('error')
    assert result['input_consumption']['status'] == 'complete'
