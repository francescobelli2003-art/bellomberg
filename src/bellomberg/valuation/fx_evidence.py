"""Exact-day ECB reference FX with explicit orientation and reproducible arithmetic.

Reference rates are not transaction prices. Missing days are never carried.
The existing immutable downloader and source-availability rules are reused.
"""
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from hashlib import sha256
from io import StringIO
import json
from math import isfinite
from pathlib import Path
import re

from .document_evidence import source_dates

NORMALIZER='ecb_reference_fx_v1'
PREFIX='fx-ecb-'
LIMITATION='ECB daily reference rate, not a transaction or equity closing-time rate. Exact date only; no missing-day carry or inferred currency.'


def reference_url(financial, quote, on):
    if (any(not isinstance(c,str) or not re.fullmatch('[A-Z]{3}',c) for c in (financial,quote))
            or financial==quote or 'EUR' not in (financial,quote)):
        raise ValueError('ECB pair requires explicit distinct ISO currencies, one EUR; no subunit or cross-rate default')
    if date.fromisoformat(on).isoformat()!=on:
        raise ValueError('exact ISO FX date required')
    foreign=quote if financial=='EUR' else financial
    return ('https://data-api.ecb.europa.eu/service/data/EXR/D.'+foreign+'.EUR.SP00.A'
            '?startPeriod='+on+'&endPeriod='+on+'&format=csvdata')


def normalize_fx(source, *, financial_currency, quote_currency, on, as_of):
    url=reference_url(financial_currency,quote_currency,on)
    day,cutoff=date.fromisoformat(on),date.fromisoformat(as_of)
    raw=source['text'].encode('utf-8'); digest=sha256(raw).hexdigest()
    if (source['url']!=url or source['id']!=digest or source['document_sha256']!=digest
            or source['sha256']!=digest or day>cutoff or source.get('published_at') is not None
            or source.get('availability_basis')!='observed_download'):
        raise ValueError('FX raw identity, hash, date, URL or availability differs')
    dates=source_dates(source,cutoff)
    if date.fromisoformat(dates['available_at'])<day:
        raise ValueError('FX receipt precedes the observation date')
    try:
        reader=csv.DictReader(StringIO(source['text'].lstrip('\ufeff')),strict=True)
        headers=reader.fieldnames
        required=('KEY','FREQ','CURRENCY','CURRENCY_DENOM','EXR_TYPE','EXR_SUFFIX',
                  'TIME_PERIOD','OBS_VALUE','OBS_STATUS','UNIT','UNIT_MULT')
        if not headers or len(set(headers))!=len(headers) or not set(required)<=set(headers):
            raise ValueError('unique ECB series/value/date/unit headers required')
        rows=list(reader)
    except csv.Error as exc:
        raise ValueError('invalid ECB CSV structure') from exc
    if len(rows)!=1 or None in rows[0] or any(rows[0].get(k) is None for k in required):
        raise ValueError('one complete exact-day ECB observation required')
    row=rows[0]; foreign=quote_currency if financial_currency=='EUR' else financial_currency
    expected={'KEY':'EXR.D.'+foreign+'.EUR.SP00.A','FREQ':'D','CURRENCY':foreign,
        'CURRENCY_DENOM':'EUR','EXR_TYPE':'SP00','EXR_SUFFIX':'A','TIME_PERIOD':on,
        'OBS_STATUS':'A','UNIT':foreign,'UNIT_MULT':'0'}
    if any(row[k]!=v for k,v in expected.items()):
        raise ValueError('ECB series, pair, exact day, normal observation status or unscaled units differ')
    value_text=row['OBS_VALUE']
    if len(value_text)>40 or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',value_text):
        raise ValueError('finite positive decimal reference rate required')
    try:
        exact=Fraction(Decimal(value_text))
        if exact<=0: raise ValueError('reference rate must be positive')
        result=exact if financial_currency=='EUR' else 1/exact
        value=float(result)
    except (InvalidOperation,OverflowError) as exc:
        raise ValueError('reference conversion is not representable') from exc
    if not isfinite(value) or value<=0:
        raise ValueError('reference conversion must remain finite and positive')
    text=json.dumps({'dataset':'ECB EXR daily reference','observation':row,
        'source_document_id':source['id'],'source_line':2,
        'calculation':{'operation':'identity' if financial_currency=='EUR' else 'reciprocal',
            'exact_numerator':str(result.numerator),'exact_denominator':str(result.denominator)},
        'facts':[{'value':value,'unit':quote_currency+' per '+financial_currency,'end':on}],
        'limitation':LIMITATION},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return {'id':PREFIX+digest+'-'+financial_currency+'-'+quote_currency,'url':url,**dates,
        'text':text,'sha256':sha256(text.encode()).hexdigest(),'document_sha256':digest,
        'origin':NORMALIZER,'metadata':{'normalizer':NORMALIZER,'source_document_id':source['id'],
            'financial_currency':financial_currency,'quote_currency':quote_currency,'report_date':on,'as_of':as_of},
        'extraction_coverage':{'status':'exact_day_reference','limitation':LIMITATION}}


def collect_fx_evidence(*,financial_currency,quote_currency,on,as_of,archive_root,download=None,now=None):
    result={'status':'incomplete','documents':[],'issues':[],'limitation':LIMITATION}
    try:
        if (isinstance(financial_currency,str) and re.fullmatch('[A-Z]{3}',financial_currency)
                and financial_currency==quote_currency):
            result['status']='identity'
            return result
        url=reference_url(financial_currency,quote_currency,on)
        if date.fromisoformat(on)>date.fromisoformat(as_of):
            raise ValueError('FX observation after cutoff')
        from bellomberg.market_data.lettore_trimestrali import scarica_documento
        root=Path(archive_root).resolve()
        fetched=(download or scarica_documento)(url,str(root),host_consentiti=['data-api.ecb.europa.eu'],public_only=True)
        if fetched.get('stato')!='ok' or fetched.get('url_finale')!=url:
            raise ValueError('ECB reference unavailable or redirected: '+str(fetched.get('motivo')))
        path=Path(fetched['path']).resolve()
        if not path.is_relative_to(root): raise ValueError('FX source outside verified archive')
        raw=path.read_bytes();digest=sha256(raw).hexdigest()
        if digest!=fetched['sha256']: raise ValueError('FX byte hash differs from download receipt')
        stamp=now if now is not None else datetime.now(timezone.utc)
        source={'id':digest,'url':url,'text':raw.decode('utf-8'),'sha256':digest,'document_sha256':digest,
            'published_at':None,'availability_basis':'observed_download',
            'retrieval':{'url':url,'document_sha256':digest,'retrieved_at':stamp.isoformat()}}
        document=normalize_fx(source,financial_currency=financial_currency,quote_currency=quote_currency,on=on,as_of=as_of)
        result.update(status='ready',documents=[source,document])
    except Exception as exc:
        result['issues'].append({'source':'ECB reference FX','reason':type(exc).__name__+': '+str(exc)})
    return result
