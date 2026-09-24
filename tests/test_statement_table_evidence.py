"""Synthetic printed statements: source cells are observations, never forecasts."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

import pytest


def source(tmp_path, *, annual=False):
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    on, form = ('2025-12-31', '20-F') if annual else ('2026-06-30', '6-K')
    header = ('<tr><td></td><td colspan="2">Year ended December 31,</td></tr>'
              '<tr><td>Notes</td><td>2025</td><td>2024</td></tr>' if annual else
              '<tr><td></td><td colspan="2">Three-month period ended June 30,</td>'
              '<td colspan="2">Six-month period ended June 30,</td></tr>'
              '<tr><td>Notes</td><td>2026</td><td>2025</td><td>2026</td><td>2025</td></tr>')
    amounts = '<td>120</td><td>110</td>' if annual else '<td>33</td><td>29</td><td>65</td><td>60</td>'
    units = '<p>Consolidated Financial Statements - all amounts in thousands of U.S. dollars, unless otherwise stated</p>'
    html = ('<html><body><p>Synthetic Issuer SA</p>' + units +
        '<h2>CONSOLIDATED INCOME STATEMENTS</h2><table>'+header+
        '<tr><td>Net sales</td>'+amounts+'</tr></table>'+units+
        '<h2>CONSOLIDATED STATEMENTS OF FINANCIAL POSITION</h2><table>'+
        f'<tr><td></td><td>At {"December 31, 2025" if annual else "June 30, 2026"}</td>'
        '<td>At December 31, 2024</td></tr>'+
        '<tr><td>Current assets</td><td></td><td></td></tr>'+
        '<tr><td>Inventories, net</td><td>18</td><td>16</td></tr>'+
        '<tr><td>Trade receivables, net</td><td>25</td><td>22</td></tr>'+
        '<tr><td>Current liabilities</td><td></td><td></td></tr>'+
        '<tr><td>Trade payables</td><td>10</td><td>9</td></tr>'+
        '<tr><td>Total assets</td><td>80</td><td>75</td></tr>'+
        '<tr><td>Cash and cash equivalents</td><td></td><td>4</td></tr></table>'+units+
        '<h2>CONSOLIDATED STATEMENTS OF CASH FLOWS</h2><table>'+header+
        '<tr><td>Cash flows from operating activities</td>'+amounts+'</tr></table></body></html>')
    raw=html.encode(); digest=sha256(raw).hexdigest(); path=tmp_path/(form+'.html'); path.write_bytes(raw)
    text=estrai_testo(str(path),contenuto=raw)['testo']
    doc={'id':digest,'document_sha256':digest,'text':text,'sha256':sha256(text.encode()).hexdigest(),
         'url':'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm',
         'published_at':'2026-08-01','archive_path':str(path),
         'metadata':{'emittente_id':'CIK:0000000123','issuer':'Synthetic Issuer SA',
                     'accession':'0000000123-26-000001','form':form,'report_date':on}}
    return doc, raw


def normalized(tmp_path, *, annual=False):
    from bellomberg.valuation.statement_table_evidence import extract_statement_packet, normalize_statement_tables
    doc,raw=source(tmp_path,annual=annual)
    doc['statement_table_fields']=extract_statement_packet(doc,raw)
    result=normalize_statement_tables(doc)
    assert result['status']=='ready',result
    return doc,result['documents'][0]


def refresh_source(doc,raw):
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    doc['id']=doc['document_sha256']=sha256(raw).hexdigest()
    doc['text']=estrai_testo('report.html',contenuto=raw)['testo']
    doc['sha256']=sha256(doc['text'].encode()).hexdigest()


def test_quarter_and_half_year_columns_remain_distinct_with_exact_cells(tmp_path):
    primary,doc=normalized(tmp_path)
    facts=json.loads(doc['text'])['facts']
    revenue=[f for f in facts if f['concept']=='Revenue']
    assert [(f['value'],f['start'],f['end'],f['unit']) for f in revenue]==[
        (33,'2026-04-01','2026-06-30','USD thousand'),(29,'2025-04-01','2025-06-30','USD thousand'),
        (65,'2026-01-01','2026-06-30','USD thousand'),(60,'2025-01-01','2025-06-30','USD thousand')]
    assert revenue[2]['proof']['cell_text']=='65'
    assert revenue[2]['proof']['source_document_id']==primary['id']
    assert all(f['taxonomy']=='reported-statement' for f in facts)
    assert not any(f['label']=='Cash and cash equivalents' and f['end']=='2026-06-30' for f in facts)
    assert doc['metadata']['coverage']['missing_cells']>0


@pytest.mark.parametrize('damage',['bytes','issuer','accession','future_period','missing_unit','segment',
                                  'unknown_duration','duplicate_income','missing_balance'])
def test_unqualified_or_ambiguous_printed_statements_are_rejected(tmp_path,damage):
    from bellomberg.valuation.statement_table_evidence import extract_statement_packet,normalize_statement_tables
    doc,raw=source(tmp_path)
    if damage=='bytes':raw+=b'changed'
    elif damage=='issuer':doc['metadata']['emittente_id']='CIK:0000000999'
    elif damage=='accession':doc['metadata']['accession']='0000000123-26-000002'
    elif damage=='future_period':doc['metadata']['report_date']='2027-06-30'
    else:
        if damage=='missing_unit':raw=raw.replace(b'thousands of U.S. dollars',b'unstated units')
        elif damage=='segment':raw=raw.replace(b'CONSOLIDATED INCOME',b'SEGMENT INCOME')
        elif damage=='unknown_duration':raw=raw.replace(b'Six-month',b'Twenty-week')
        elif damage=='missing_balance':raw=raw.replace(b'STATEMENTS OF FINANCIAL POSITION',b'OTHER INFORMATION')
        else:
            block=raw[raw.index(b'<h2>'):raw.index(b'</table>')+len(b'</table>')]
            raw=raw.replace(b'</body>',block+b'</body>')
        refresh_source(doc,raw)
    try:doc['statement_table_fields']=extract_statement_packet(doc,raw)
    except ValueError:return
    assert normalize_statement_tables(doc)['status']=='incomplete'


def test_catalog_recompiles_normalization_and_does_not_trust_edited_fact(tmp_path):
    from bellomberg.valuation.input_preparation import _catalog
    primary,doc=normalized(tmp_path)
    assert not _catalog([primary,doc],date(2026,9,1))[1]
    altered=deepcopy(doc);payload=json.loads(altered['text']);payload['facts'][0]['value']+=10
    altered['text']=json.dumps(payload);altered['sha256']=sha256(altered['text'].encode()).hexdigest()
    assert _catalog([primary,altered],date(2026,9,1))[1]
    assert _catalog([doc],date(2026,9,1))[1]


def pointer(doc,index):
    fact=json.loads(doc['text'])['facts'][index]
    return {'evidence_ids':[doc['id']],'quoted_value':fact['value'],'quoted_unit':fact['unit'],
            'evidence_pointer':{'value':f'/facts/{index}/value','unit':f'/facts/{index}/unit','period':f'/facts/{index}/end'}}


def test_reported_revenue_ttm_uses_verified_periods_without_impersonating_xbrl(tmp_path):
    from bellomberg.valuation.input_preparation import _fact_proof,_catalog
    annual_source,annual=normalized(tmp_path,annual=True)
    interim_source,interim=normalized(tmp_path)
    docs=[annual_source,annual,interim_source,interim]
    catalog,issues,_=_catalog(docs,date(2026,9,1));assert not issues
    item={'value':.125,'evidence_ids':[annual['id'],interim['id']], 'calculation':{
        'operation':'trailing_twelve_months','terms':{'annual':pointer(annual,0),
        'current_ytd':pointer(interim,2),'prior_ytd':pointer(interim,3)}}}
    evidence=[catalog[annual['id']],catalog[interim['id']]]
    assert _fact_proof('historical_revenue',item,evidence,'USD million','2026-06-30',
                       expected_entity='Synthetic Issuer SA') is None
    item['calculation']['terms']['current_ytd']=pointer(interim,0)
    assert _fact_proof('historical_revenue',item,evidence,'USD million','2026-06-30',
                       expected_entity='Synthetic Issuer SA')


def test_preparer_teaches_printed_revenue_pointers_that_the_compiler_accepts(tmp_path):
    from test_input_preparation import _bundle
    from bellomberg.valuation.input_preparation import prepare_method_inputs, _fact_proof
    from bellomberg.valuation.preparation_ai import StagedProposer
    annual_source, annual = normalized(tmp_path, annual=True)
    interim_source, interim = normalized(tmp_path)
    docs = [annual_source, annual, interim_source, interim]; seen = {}
    def capture(dossier, contract):
        seen.update(dossier=dossier, contract=contract)
        raise RuntimeError('Offline capture; no AI response')
    result = prepare_method_inputs(_bundle(), documents=docs, propose=StagedProposer(capture))
    assert result['status'] == 'incomplete' and seen
    policy = seen['contract']['historical_revenue_policy']['printed_statement_format']
    assert policy['normalizer'] == 'statement_tables_v1'
    assert policy['taxonomy'] == 'reported-statement' and policy['concept'] == 'Revenue'
    def operand(document, index):
        fact = json.loads(document['text'])['facts'][index]
        return {'evidence_ids': [document['id']], 'quoted_value': fact['value'], 'quoted_unit': fact['unit'],
            'evidence_pointer': {key: template.format(index=index) for key, template in policy['evidence_pointer'].items()}}
    item = {'value': .125, 'evidence_ids': [annual['id'], interim['id']], 'calculation': {
        'operation': seen['contract']['historical_revenue_policy']['interim_operation'],
        'terms': {'annual': operand(annual, 0), 'current_ytd': operand(interim, 2), 'prior_ytd': operand(interim, 3)}}}
    assert _fact_proof('historical_revenue', item, [annual, interim], 'USD million', '2026-06-30',
                       expected_entity='Synthetic Issuer SA') is None


@pytest.mark.parametrize('business', ['software', 'bank'])
def test_printed_revenue_instructions_do_not_change_other_method_contexts(tmp_path, business):
    from test_input_preparation import _bundle, _documents
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    seen = {}
    def capture(dossier, contract):
        seen.update(contract)
        raise RuntimeError('Offline capture')
    docs = list(normalized(tmp_path)) if business == 'bank' else _documents()
    prepare_method_inputs(_bundle(business=business), documents=docs, propose=capture)
    assert seen
    if business == 'bank': assert 'historical_revenue_policy' not in seen
    else:
        assert seen['historical_revenue_policy'] == {
            'basis': 'Full-year observed revenue ending at calendar.valuation_date',
            'interim_operation': 'trailing_twelve_months', 'terms': ['annual', 'current_ytd', 'prior_ytd'],
            'formula': 'annual + current_ytd - prior_ytd',
            'requirements': 'SEC XBRL accession-bound observations; same issuer, concept and currency; contiguous annual/current YTD and comparable prior YTD; exact operand citations',
            'limitation': 'No quarter multiplication, implicit annualization or forecast opening revenue'}


def test_normalized_operating_components_preserve_economic_signs(tmp_path):
    from bellomberg.valuation.input_preparation import _fact_proof
    _,doc=normalized(tmp_path);facts=json.loads(doc['text'])['facts']
    signs={'Inventories':1,'TradeReceivables':1,'TradePayables':-1}
    terms=[{'coefficient':signs[f['concept']],**pointer(doc,i)} for i,f in enumerate(facts)
           if f['concept'] in signs and f['end']=='2026-06-30']
    item={'value':.033,'evidence_ids':[doc['id']],'calculation':{'operation':'sum','terms':terms}}
    assert _fact_proof('opening_nwc',item,[doc],'USD million','2026-06-30',
                      expected_entity='Synthetic Issuer SA') is None
    terms[-1]['coefficient']=1
    assert _fact_proof('opening_nwc',item,[doc],'USD million','2026-06-30',
                      expected_entity='Synthetic Issuer SA')


def test_packet_cannot_change_a_reported_cell_even_with_recomputed_packet_hash(tmp_path):
    from bellomberg.valuation.statement_table_evidence import normalize_statement_tables,_json
    primary,_=normalized(tmp_path)
    packet=primary['statement_table_fields'];packet['tables'][0]['rows'][2][1]['text']='999'
    packet.pop('sha256');packet['sha256']=sha256(_json(packet).encode()).hexdigest()
    assert normalize_statement_tables(primary)['status']=='incomplete'


def test_cash_reconciliation_year_header_does_not_become_a_monetary_flow(tmp_path):
    from bellomberg.valuation.statement_table_evidence import extract_statement_packet,normalize_statement_tables
    doc,raw=source(tmp_path)
    extra=(b'<tr><td>Movement in cash and cash equivalents</td></tr>'
           b'<tr><td>At the beginning of the period</td><td>10</td><td>9</td><td>10</td><td>9</td></tr>'
           b'<tr><td>Cash and cash equivalents</td><td>2026</td><td>2025</td><td>2026</td><td>2025</td></tr>')
    raw=raw.replace(b'</table></body>',extra+b'</table></body>')
    refresh_source(doc,raw)
    doc['statement_table_fields']=extract_statement_packet(doc,raw)
    result=normalize_statement_tables(doc);assert result['status']=='ready'
    output=result['documents'][0]
    assert output['metadata']['coverage']['excluded_reconciliation_rows']==3
    assert all(f['value'] not in (2026,2025) for f in json.loads(output['text'])['facts'])


@pytest.mark.parametrize('change',['negative_split','ambiguous','dash','percent','noncurrent'])
def test_cell_layout_and_monetary_context_are_not_guessed(tmp_path,change):
    from bellomberg.valuation.statement_table_evidence import extract_statement_packet,normalize_statement_tables
    doc,raw=source(tmp_path)
    if change=='negative_split':
        raw=raw.replace(b'<td>18</td>',b'<td>(18)</td>')
    elif change=='ambiguous':
        raw=raw.replace(b'<td>At June 30, 2026</td>',b'<td colspan="2">At June 30, 2026</td>')
        raw=raw.replace(b'<td>18</td>',b'<td>18</td><td>75</td>')
    elif change=='dash':raw=raw.replace(b'<td>18</td>',b'<td>-</td>')
    elif change=='percent':raw=raw.replace(b'<td>Notes</td>',b'<td>Percentage of revenue</td>')
    else:raw=raw.replace(b'Current assets',b'Non-current assets')
    refresh_source(doc,raw)
    doc['statement_table_fields']=extract_statement_packet(doc,raw)
    result=normalize_statement_tables(doc)
    if change=='percent':
        assert result['status']=='incomplete';return
    assert result['status']=='ready',result
    rows=[f for f in json.loads(result['documents'][0]['text'])['facts']
          if f['label']=='Inventories, net' and f['end']=='2026-06-30']
    if change=='negative_split':assert rows[0]['value']==-18
    elif change=='noncurrent':assert rows[0]['concept']=='label:Inventories, net'
    else:assert rows==[]


def test_collector_uses_printed_coverage_but_retains_missing_companyfacts_issue(tmp_path,monkeypatch):
    from bellomberg.valuation import valuation_sources,quotation_evidence
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from bellomberg.valuation.input_preparation import _catalog
    from bellomberg.valuation.preparation_view import _structured_kind
    annual,_=source(tmp_path,annual=True);interim,_=source(tmp_path)
    monkeypatch.setattr(valuation_sources,'collect_documents',lambda *a,**k:{
        'status':'ready','documents':[interim,annual],'issues':[],'coverage':{}})
    monkeypatch.setattr(valuation_sources,'company_facts_documents',lambda *a,**k:{
        'status':'incomplete','documents':[],'issues':[{'source':'SEC companyfacts','reason':'No tagged facts in fixture'}]})
    monkeypatch.setattr(quotation_evidence,'listing_identity_document',lambda *a,**k:{
        'status':'ready','documents':[{'id':'synthetic-listing','url':'https://example.org/listing',
        'text':'Synthetic listing proof','published_at':'2026-08-01'}],'issues':[]})
    result=collect_preparation_evidence('SYNTH',as_of='2026-09-01',archive_root=tmp_path,
        price_fetch=lambda ticker,on:{'symbol':ticker,'date':on,'close':12.5,'currency':'USD'},
        earnings_fetch=lambda *_a,**_k:{'stato':'ok','documenti':[],'motivi':[]})
    assert result['financial_sources_ready'] and result['preparation_ready']
    assert result['components']['company_facts']['status']=='incomplete' and result['status']=='partial'
    assert result['statement_tables']['status']=='ready'
    catalog,issues,_=_catalog(result['documents'],date(2026,9,1));assert not issues
    printed=[d for d in result['documents'] if d['id'].startswith('statement-tables-')]
    assert len(printed)==2
    assert all(_structured_kind(d,json.loads(d['text']),'SYNTH')=='printed_statement' for d in printed)
    assert all('statement_table_fields' in catalog[d['metadata']['source_document_id']] for d in printed)
    from bellomberg.valuation.preparation_view import select_stage_view
    dossier={'ticker':'SYNTH','method_id':'operating_fcff','documents':result['documents']}
    original=deepcopy(dossier)
    view=select_stage_view(dossier,'model')
    assert dossier==original
    assert view['stage_view']['structured_documents']['printed_statement']==2
    assert '/facts/N/value' in view['stage_view']['printed_statement_evidence']
    assert not any('statement_table_fields' in d for d in view['documents'])
    assert all(next(d for d in view['documents'] if d['id']==p['id'])['text']==p['text'] for p in printed)


def subtotal_source(tmp_path, change='valid'):
    from bellomberg.valuation.statement_table_evidence import extract_statement_packet
    doc,raw=source(tmp_path)
    block=('''<table><tr><td></td><td colspan="3">At June 30, 2026</td>
        <td colspan="3">At December 31, 2024</td></tr>
        <tr><td>Current liabilities</td><td colspan="6"></td></tr>
        <tr><td>Borrowings</td><td>4.1</td><td></td><td></td><td>3</td><td></td><td></td></tr>
        <tr><td>Customer advances</td><td>2.2</td><td></td><td></td><td>2</td><td></td><td></td></tr>
        <tr><td>Trade payables</td><td>10</td><td></td><td>16.3</td><td>9</td><td></td><td>14</td></tr>
        <tr><td>Total liabilities</td><td></td><td></td><td>16.3</td><td></td><td></td><td>14</td></tr></table>''')
    substitutions={
        'wrong_sum':('<td>10</td>','<td>11</td>'),
        'precision':('<td>4.1</td>','<td>4.10000000000000000000000000001</td>'),
        'negative_precision':('<td>4.1</td>','<td>(4.10000000000000000000000000001)</td>'),
        'dash':('<td>4.1</td>','<td>-</td>'),
        'blank':('<td>4.1</td>','<td></td>'),
        'text':('<td>4.1</td>','<td>n.a.</td>'),
        'column':('<td>4.1</td><td></td>','<td></td><td>4.1</td>'),
        'heading':('Current liabilities','Other items'),
        'unlabelled':('Customer advances',''),
        'extra_row':('<tr><td>Total liabilities', '<tr><td>Other liabilities</td><td>1</td><td></td><td></td><td>1</td><td></td><td></td></tr><tr><td>Total liabilities'),
        'extra_cell':('<td>10</td><td></td>','<td>10</td><td>1</td>'),
        'missing_boundary':('<tr><td>Total liabilities</td><td></td><td></td><td>16.3</td><td></td><td></td><td>14</td></tr>',''),
        'negative':('<td>4.1</td>','<td>(4.1)</td>'),
        'negative_split':('<td>4.1</td><td></td>','<td>(4.1</td><td>)</td>'),
        'percentage':('Customer advances','Margin %')}
    if change in substitutions:block=block.replace(*substitutions[change])
    negative=change.startswith('negative')
    if negative:block=block.replace('16.3','8.1')
    start=raw.index(b'<table>',raw.index(b'CONSOLIDATED STATEMENTS OF FINANCIAL POSITION'))
    end=raw.index(b'</table>',start)+len(b'</table>')
    raw=raw[:start]+block.encode()+raw[end:]
    refresh_source(doc,raw)
    doc['statement_table_fields']=extract_statement_packet(doc,raw)
    return doc


@pytest.mark.parametrize('change',['valid','negative','negative_split'])
def test_section_sum_resolves_line_amount_without_using_subtotal_as_payables(tmp_path,change):
    from bellomberg.valuation.statement_table_evidence import normalize_statement_tables
    from bellomberg.valuation.input_preparation import _catalog
    primary=subtotal_source(tmp_path,change)
    result=normalize_statement_tables(primary);assert result['status']=='ready'
    doc=result['documents'][0];facts=json.loads(doc['text'])['facts']
    payables=[f for f in facts if f['concept']=='TradePayables']
    assert [(f['value'],f['end']) for f in payables]==[(10,'2026-06-30'),(9,'2024-12-31')]
    proof=payables[0]['proof']['section_subtotal']
    assert proof['subtotal']['value']==(8.1 if change.startswith('negative') else 16.3)
    assert [t['value'] for t in proof['terms']]==[(-4.1 if change.startswith('negative') else 4.1),2.2,10]
    assert [t['row_index'] for t in proof['terms']]==[2,3,4]
    assert proof['subtotal']['column']==3 and payables[0]['proof']['column']==1
    assert doc['metadata']['coverage']['resolved_subtotal_cells']==2
    assert not _catalog([primary,doc],date(2026,9,1))[1]
    edited=deepcopy(doc);payload=json.loads(edited['text'])
    next(f for f in payload['facts'] if f['concept']=='TradePayables')['proof']['section_subtotal']['terms'][0]['value']=99
    edited['text']=json.dumps(payload);edited['sha256']=sha256(edited['text'].encode()).hexdigest()
    assert _catalog([primary,edited],date(2026,9,1))[1]


@pytest.mark.parametrize('change',['wrong_sum','precision','negative_precision','dash','blank','text','column','heading','unlabelled',
                                  'extra_row','extra_cell','missing_boundary','percentage'])
def test_unproven_subtotal_never_selects_the_first_numeric_cell(tmp_path,change):
    from bellomberg.valuation.statement_table_evidence import normalize_statement_tables
    result=normalize_statement_tables(subtotal_source(tmp_path,change));assert result['status']=='ready'
    facts=json.loads(result['documents'][0]['text'])['facts']
    assert not any(f['label']=='Trade payables' and f['end']=='2026-06-30' for f in facts)
    assert result['documents'][0]['metadata']['coverage']['ambiguous_cells']>0
