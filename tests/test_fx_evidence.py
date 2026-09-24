"""Exact-date primary reference FX: orientation, arithmetic, availability and gaps."""
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import pytest

ON='2026-06-30'; AS_OF='2026-08-02'; NOW=datetime(2026,8,2,12,tzinfo=timezone.utc)
CSV=('KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS,UNIT,UNIT_MULT\n'
     'EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-06-30,1.25,A,USD,0\n').encode()


def source(raw=CSV):
    from bellomberg.valuation.fx_evidence import reference_url
    digest=sha256(raw).hexdigest(); url=reference_url('USD','EUR',ON)
    return {'id':digest,'url':url,'text':raw.decode('utf-8'),'sha256':digest,'document_sha256':digest,
            'published_at':None,'availability_basis':'observed_download',
            'retrieval':{'url':url,'document_sha256':digest,'retrieved_at':NOW.isoformat()}}


def normalize(doc, financial='USD', quote='EUR'):
    from bellomberg.valuation.fx_evidence import normalize_fx
    return normalize_fx(doc,financial_currency=financial,quote_currency=quote,on=ON,as_of=AS_OF)


@pytest.mark.parametrize('financial,quote,expected,operation',[
    ('USD','EUR',.8,'reciprocal'),('EUR','USD',1.25,'identity')])
def test_orientation_and_exact_source_are_recompiled(financial,quote,expected,operation):
    from bellomberg.valuation.input_preparation import _catalog, _fact_proof
    src=source(); doc=normalize(src,financial,quote); body=json.loads(doc['text'])
    fact,=body['facts']
    assert fact['value']==expected and fact['unit']==quote+' per '+financial and fact['end']==ON
    assert body['calculation']['operation']==operation
    assert body['observation']['OBS_VALUE']=='1.25'
    assert doc['published_at'] is None and doc['available_at']==AS_OF
    catalog,issues,_=_catalog([src,doc],date.fromisoformat(AS_OF))
    assert not issues and doc['id'] in catalog
    evidence={'value':expected,'quoted_value':expected,'quoted_unit':fact['unit'],
        'evidence_ids':[doc['id']],'evidence_pointer':{'value':'/facts/0/value','unit':'/facts/0/unit','period':'/facts/0/end'}}
    assert _fact_proof('financial_to_quote_rate',evidence,[catalog[doc['id']]],fact['unit'],ON) is None
    tampered=deepcopy(doc); wrong=json.loads(tampered['text']); wrong['facts'][0]['value']=3.0
    tampered['text']=json.dumps(wrong);tampered['sha256']=sha256(tampered['text'].encode()).hexdigest()
    catalog,issues,_=_catalog([src,tampered],date.fromisoformat(AS_OF))
    assert issues and doc['id'] not in catalog
    catalog,issues,_=_catalog([doc],date.fromisoformat(AS_OF))
    assert issues and doc['id'] not in catalog


@pytest.mark.parametrize('old,new',[(b'2026-06-30',b'2026-06-29'),(b',1.25,',b',0,'),
    (b',1.25,',b',NaN,'),(b',1.25,',b',-1.25,'),(b',1.25,',b',Inf,'),
    (b',1.25,',b',,'),(b',A,USD,0',b',M,USD,0'),(b',A,USD,0',b',A,USD,3'),
    (b',USD,EUR,',b',GBP,EUR,'),(b'EXR.D.USD',b'EXR.M.USD'),
    (b',SP00,A,',b',SP01,A,'),(b',A,USD,0',b',A,EUR,0'),
    (b'OBS_VALUE,',b'OBS_VALUE,OBS_VALUE,'),(CSV,CSV+CSV.splitlines()[1]+b'\n')])
def test_wrong_date_series_units_status_duplicate_or_missing_values_rejected(old,new):
    with pytest.raises(ValueError): normalize(source(CSV.replace(old,new)))


@pytest.mark.parametrize('problem',['hash','url','future_retrieval','missing_receipt'])
def test_raw_provenance_and_availability_required(problem):
    src=source()
    if problem=='hash': src['document_sha256']='0'*64
    if problem=='url': src['url']='https://example.org/fx'
    if problem=='future_retrieval': src['retrieval']['retrieved_at']='2026-08-03T00:00:00+00:00'
    if problem=='missing_receipt': del src['retrieval']
    with pytest.raises((ValueError,KeyError)): normalize(src)


def test_collector_uses_immutable_downloader_and_returns_raw_plus_derived(tmp_path):
    from bellomberg.valuation.fx_evidence import collect_fx_evidence, reference_url
    calls=[]
    def download(url,dest,**kwargs):
        calls.append((url,dest,kwargs)); p=tmp_path/'fx.csv';p.write_bytes(CSV)
        return {'stato':'ok','url_finale':url,'path':str(p),'sha256':sha256(CSV).hexdigest()}
    result=collect_fx_evidence(financial_currency='USD',quote_currency='EUR',on=ON,as_of=AS_OF,
                              archive_root=tmp_path,download=download,now=NOW)
    assert result['status']=='ready' and len(result['documents'])==2
    assert calls==[(reference_url('USD','EUR',ON),str(tmp_path),
                   {'host_consentiti':['data-api.ecb.europa.eu'],'public_only':True})]


@pytest.mark.parametrize('financial,quote',[('USD','USD'),('USD','GBP'),(None,'EUR'),('USD','GBp')])
def test_no_silent_cross_currency_or_subunit_defaults(tmp_path,financial,quote):
    from bellomberg.valuation.fx_evidence import collect_fx_evidence
    result=collect_fx_evidence(financial_currency=financial,quote_currency=quote,on=ON,as_of=AS_OF,
        archive_root=tmp_path,download=lambda *a,**k:pytest.fail('no network for unsupported or identical units'))
    assert result['status']==('identity' if financial==quote else 'incomplete')
    assert not result['documents']


@pytest.mark.parametrize('fault',['outside','hash','redirect','unavailable'])
def test_bad_acquisition_never_returns_a_conversion(tmp_path,fault):
    from bellomberg.valuation.fx_evidence import collect_fx_evidence
    root=tmp_path/'archive';root.mkdir();p=(tmp_path if fault=='outside' else root)/'fx.csv';p.write_bytes(CSV)
    def download(url,*a,**k):
        return {'stato':'errore' if fault=='unavailable' else 'ok','url_finale':url+'/other' if fault=='redirect' else url,
                'path':str(p),'sha256':'0'*64 if fault=='hash' else sha256(CSV).hexdigest()}
    result=collect_fx_evidence(financial_currency='USD',quote_currency='EUR',on=ON,as_of=AS_OF,
                              archive_root=root,download=download,now=NOW)
    assert result['status']=='incomplete' and not result['documents'] and result['issues']
