"""Real synthetic PDFs: column geometry and primary text must agree."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from test_filing_pdf_preparation import profile, URL


def statement_pdf(tmp_path, *, displaced=False, duplicated=False, nested_eps=False, balance_rows=None):
    writer=PdfWriter()
    def page(lines):
        p=writer.add_blank_page(width=612,height=792)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),
            NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Courier')})
        p[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject()
        commands=[]
        for x,y,size,text in lines:
            escaped=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
            commands.append(f'BT /F1 {size} Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET')
        stream.set_data('\n'.join(commands).encode('ascii'))
        p[NameObject('/Contents')]=writer._add_object(stream)
    page([(60,740-i*18,10,s) for i,s in enumerate(['Synthetic Industries SE', 'Half-year report',
        'Consolidated financial statements', 'For the six-month period ended June 30, 2025',
        'Risk factors','Synthetic narrative.','Management discussion'])])
    def table(title, headers, rows):
        lines=[(60,730,14,title),(60,680,8,'EUR million')]
        centers=[340+50*i for i in range(len(headers))]
        for x,header in zip(centers,headers):
            if ',' in header:
                day,year=header.rsplit(' ',1)
                lines.extend([(x-len(day)*2.4,691,8,day),(x-len(year)*2.4,680,8,year)])
            else:
                lines.append((x-len(header)*2.4,680,8,header))
        for i,(label,values) in enumerate(rows):
            y=655-i*18
            if label:
                lines.append((60,y,8,label))
            for j,value in enumerate(values):
                if value is None: continue
                x=centers[j]+15-len(value)*4.8
                if displaced and label=='Net sales' and j==1: x+=22
                lines.append((x,y,8,value))
        page(lines)
    duration=['Q2 2024','Q2 2025','H1 2024','H1 2025']
    eps=[('Earnings per share EUR',['0.2','0.3','0.4','0.5'])]
    if nested_eps:
        eps=[('Earnings per share EUR',[]),('Basic',['0.2','0.3','0.4','0.5']),
             ('Diluted',['0.2','0.3','0.4','0.5']),
             ('1 Calculation uses the weighted average share count described in the report notes.',[])]
    table('Consolidated Income Statements',duration,
        [('Net sales',['50','60','90','120']),('Cost of sales',['(20)','(30)','(45)','(60)']),
         ('Income from operations',['30','30','45','60'])]+eps)
    table('Consolidated Statements of Financial Position',
        ['June 30, 2024','Dec. 31, 2024','June 30, 2025'],
        balance_rows if balance_rows is not None else [('Current assets',[]),('Inventories',['10','15','20']),
         ('Trade accounts receivable',['15','20','30']),('Cash and cash equivalents',['5','5','-']),
         (None,['30','40','50']),('Total assets',['30','40','50']),
         ('Current liabilities',[]),('Trade accounts payable',['5','10','15']),
         ('Total liabilities',['5','10','15']),('Total equity',['25','30','35']),
         ('Total equity and liabilities',['30','40','50'])])
    table('Consolidated Statements of Cash Flows',duration,
        [('Net cash from operating activities',['10','20','30','40']),
         ('Payments for property plant and equipment',['(2)','(3)','(4)','(5)']),
         ('Cash and cash equivalents at end of period',['5','-','5','-'])])
    if duplicated:
        table('Consolidated Income Statements',duration,[('Net sales',['1','2','3','4'])])
    out=BytesIO(); writer.write(out)
    path=tmp_path/'synthetic-statements.pdf';path.write_bytes(out.getvalue())
    return path


def primary(tmp_path, **kwargs):
    from bellomberg.market_data.filing_verifica import verifica_documento
    from bellomberg.valuation.valuation_sources import collect_documents
    path=statement_pdf(tmp_path,**kwargs)
    verification=verifica_documento(path,url=URL,profilo=profile())
    assert verification['stato']=='ok',verification
    d=verification['documento']
    candidate={'stato':'verificato','path':str(path),'sha256':d['sha256'],'url':URL,
        'filed_date':'2025-08-05','metadati':d['metadati'],'filing_verification':d['filing_verification']}
    result=collect_documents('SYNTH.DE',as_of='2025-09-01',archive_root=tmp_path,
        filing_results=[{'ticker':'SYNTH.DE','candidati':[candidate]}],
        catalog=lambda _: {'stato':'ok','documenti':[],'motivi':[]})
    assert len(result['documents'])==1,result
    return result['documents'][0]


def normalized(tmp_path, **kwargs):
    from bellomberg.valuation.statement_table_evidence import collect_statement_tables
    source=primary(tmp_path,**kwargs)
    result=collect_statement_tables([source],tmp_path)
    return source,result


def test_common_statement_collector_reads_pdf_axes_and_keeps_missing_cells(tmp_path):
    source,result=normalized(tmp_path)
    assert result['status']=='ready',result
    doc=result['documents'][0]; facts=json.loads(doc['text'])['facts']
    revenue=[f for f in facts if f['concept']=='Revenue']
    assert [(f['start'],f['end'],f['value']) for f in revenue]==[
        ('2024-04-01','2024-06-30',50),('2025-04-01','2025-06-30',60),
        ('2024-01-01','2024-06-30',90),('2025-01-01','2025-06-30',120)]
    assert all(f['unit']=='EUR million' for f in facts)
    assert not any('per share' in (f['label'] or '').lower() for f in facts)
    assert not any(f['statement']=='cash_flow' and 'end of period' in f['label'] for f in facts)
    cash=[f for f in facts if f['label']=='Cash and cash equivalents']
    assert [f['end'] for f in cash]==['2024-06-30','2024-12-31']
    assert any(f['label'] is None and f['proof']['row_kind']=='unlabeled' for f in facts)
    assert doc['metadata']['emittente_id']==profile()['emittente_id']
    assert 'accession' not in doc['metadata']
    assert source['id'] in result['packets']


def test_pdf_normalization_replays_through_the_existing_input_catalog(tmp_path):
    from bellomberg.valuation.input_preparation import _catalog
    source,result=normalized(tmp_path)
    assert result['status']=='ready',result
    source['statement_table_fields']=result['packets'][source['id']]
    normalized_doc=result['documents'][0]
    catalog,issues,_=_catalog([normalized_doc,source],date(2025,9,1))
    assert not issues and len(catalog)==2,issues
    altered=deepcopy(normalized_doc)
    facts=json.loads(altered['text']);facts['facts'][0]['value']+=1
    altered['text']=json.dumps(facts);altered['sha256']=sha256(altered['text'].encode()).hexdigest()
    catalog,issues,_=_catalog([altered,source],date(2025,9,1))
    assert altered['id'] not in catalog and issues


def test_per_share_subrows_and_footer_never_inherit_the_million_unit(tmp_path):
    _,result=normalized(tmp_path,nested_eps=True)
    assert result['status']=='ready',result
    facts=json.loads(result['documents'][0]['text'])['facts']
    assert not any(f['label'] in ('Basic','Diluted') for f in facts)


@pytest.mark.parametrize('kwargs', [{'displaced':True},{'duplicated':True}])
def test_wrong_column_or_duplicate_statement_never_qualifies(tmp_path,kwargs):
    _,result=normalized(tmp_path,**kwargs)
    assert result['status']=='unavailable' and result['documents']==[]
    assert result['issues']


@pytest.mark.parametrize('mutation', ['number','unit','year','delete_last_cell'])
def test_rehashed_layout_packet_cannot_change_or_drop_reported_cells(tmp_path,mutation):
    from bellomberg.valuation.statement_table_evidence import normalize_statement_tables, _json
    source,result=normalized(tmp_path)
    packet=deepcopy(result['packets'][source['id']])
    words=packet['pages'][0]['words']
    if mutation=='number':
        next(w for w in words if w['text']=='120')['text']='121'
    elif mutation=='unit':
        next(w for w in words if w['text']=='EUR')['text']='USD'
    elif mutation=='year':
        next(w for w in words if w['text']=='2024')['text']='2023'
    else:
        words.remove(next(w for w in words if w['text']=='120'))
    packet.pop('sha256');packet['sha256']=sha256(_json(packet).encode()).hexdigest()
    result=normalize_statement_tables({**source,'statement_table_fields':packet})
    assert result['status']=='incomplete' and not result['documents']


def test_pdf_observations_cannot_bypass_full_nwc_coverage(tmp_path):
    from bellomberg.valuation.balance_working_capital import balance_nwc_selection_problem, balance_nwc_policy
    source,result=normalized(tmp_path)
    catalog={d['id']:d for d in [source]+result['documents']}
    assert balance_nwc_policy(list(catalog.values()))['status']=='incomplete'
    problem=balance_nwc_selection_problem({'evidence_ids':[source['id']]},catalog,
        profile()['emittente_id'],'2025-06-30')
    assert problem and 'complete reported balance' in problem


def test_pdf_coverage_detection_preserves_unrelated_legacy_metadata():
    from bellomberg.valuation.balance_working_capital import balance_nwc_policy
    assert balance_nwc_policy([{'id':'unrelated','metadata':['opaque legacy note']}]) is None


def test_quarter_labels_do_not_impose_a_calendar_on_a_different_fiscal_period(tmp_path):
    from bellomberg.valuation.pdf_statement_evidence import _axes
    source,result=normalized(tmp_path)
    words=result['packets'][source['id']]['pages'][0]['words']
    with pytest.raises(ValueError,match='verified January-June'):
        _axes(words,'income',{**source['metadata'],'report_start':'2024-12-01','report_date':'2025-05-31'})


def test_shared_preparer_reports_pdf_observations_but_stops_before_paid_model(tmp_path,monkeypatch):
    from bellomberg.valuation import valuation_sources
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from bellomberg.valuation.preparation_service import prepare_and_generate
    from test_input_preparation import _bundle
    source=primary(tmp_path)
    monkeypatch.setattr(valuation_sources,'collect_documents',lambda *a,**k:{
        'status':'ready','documents':[source],'issues':[],'coverage':{}})
    report=collect_preparation_evidence('SYNTH.DE',as_of='2025-09-01',archive_root=tmp_path,
        method_id='operating_fcff',
        price_fetch=lambda ticker,on:{'symbol':ticker,'date':on,'currency':'EUR','close':10},
        earnings_fetch=lambda *_a,**_k: pytest.fail('unexpected external source fetch'),
        download=lambda *_a,**_k: pytest.fail('unexpected download'))
    assert report['financial_sources_ready']
    assert report['statement_tables']['status']=='ready'
    assert report['balance_sheet']['status']=='incomplete'
    assert not report['preparation_ready'] and report['documents']==[]
    result=prepare_and_generate(_bundle(),documents=report['documents'],source_report=report,
        propose=lambda *_:pytest.fail('known incomplete PDF source paid for AI'))
    assert result['preparation']['status']=='incomplete'
    assert result.get('path') is None and not result['valuation_usability']['usable']


@pytest.mark.parametrize('reader',['parent','listing'])
def test_pdf_bytes_never_enter_inline_html_readers(tmp_path,monkeypatch,reader):
    import bs4
    source=primary(tmp_path)
    raw=Path(source['archive_path']).read_bytes()
    monkeypatch.setattr(bs4,'BeautifulSoup',lambda *_a,**_k:pytest.fail('PDF routed to HTML parser'))
    if reader=='parent':
        from bellomberg.valuation.parent_inline_evidence import extract_parent_packet
        with pytest.raises(ValueError,match='PDF'):
            extract_parent_packet(source,raw)
    else:
        from bellomberg.valuation.quotation_evidence import listing_identity
        result=listing_identity(raw,'SYNTH.DE')
        assert result['status']=='incomplete' and 'PDF' in result['reason']
