"""Actual synthetic PDF bytes through the common parent-report acquisition."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from test_preparation_bank_sources import _collect as bank_sources, _sources


def _pdf(tmp_path, form, *, entity='Synthetic Holding.', bad_date=False):
    from reportlab.pdfgen import canvas
    from test_regulatory_evidence import _document as large, ENTITY
    from test_regulatory_small_parent import _document as small
    doc = large() if form == 'FRY9LP' else small(entity=entity)
    path = tmp_path/(form+'.pdf')
    writer = canvas.Canvas(str(path),pagesize=(850,900))
    for ref in doc['page_references']:
        text = doc['text'][ref['inizio']:ref['fine']].replace(ENTITY,entity)
        if bad_date: text=text.replace('June 30, 2026','March 31, 2026')
        writer.setFont('Helvetica',6)
        for i,line in enumerate(text.splitlines()): writer.drawString(20,860-i*20,line)
        writer.showPage()
    writer.save()
    return path.read_bytes()


def _collect(tmp_path, *, form='FRY9LP', fault=None, change_bank=None, as_of='2026-09-10'):
    from bellomberg.valuation.preparation_parent_sources import collect_parent_sources
    banks,_=bank_sources(tmp_path/'banks')
    primary,_=_sources()
    if change_bank:
        if change_bank=='identity': banks['documents'][1]['metadata']['parent_rssd']=999
        elif change_bank=='missing': banks['documents'].clear()
        else: primary['metadata']['issuer']='Other Holding'
    raw = _pdf(tmp_path,form,entity='Other Holding' if fault=='entity' else 'Synthetic Holding.',bad_date=fault=='period')
    calls=[]
    def download(url,root,**kwargs):
        calls.append(url)
        assert kwargs=={'host_consentiti':['www.ffiec.gov'],'public_only':True}
        parts=urlsplit(url);query=parse_qs(parts.query)
        assert parts.path=='/npw/FinancialReport/ReturnFinancialReportPDF'
        assert query['id']==['34567'] and query['dt']==['20260630']
        selected=query['rpt'][0]
        if fault=='unavailable' or selected!=form and fault!='ambiguous':
            return {'stato':'errore','motivo':'synthetic unavailable report'}
        content=_pdf(tmp_path,selected) if fault=='ambiguous' else raw
        root=Path(root);root.mkdir(parents=True,exist_ok=True)
        path=root/(selected+'.pdf');path.write_bytes(content)
        return {'stato':'ok','path':str(path),'url_finale':url if fault!='redirect' else url+'&other=1',
                'sha256':sha256(content).hexdigest() if fault!='hash' else '0'*64}
    result=collect_parent_sources(primary=primary,bank_documents=banks['documents'],on='2026-06-30',
        as_of=as_of,archive_root=tmp_path/'sources',download=download,now=datetime(2026,9,10,11,tzinfo=timezone.utc))
    return result,calls


@pytest.mark.parametrize('form',['FRY9LP','FRY9SP'])
def test_verified_parent_identifier_acquires_one_exact_supported_report(tmp_path,form):
    from bellomberg.valuation.input_preparation import _catalog,_day
    result,calls=_collect(tmp_path,form=form)
    assert result['status']=='ready',result['issues']
    assert len(calls)==2 and len(result['documents'])==2
    raw,normalized=result['documents']
    assert normalized['metadata']['normalizer']=='regulatory_pdf_v1'
    assert normalized['metadata']['entity']=='Synthetic Holding.'
    assert raw['published_at'] is None and raw['available_at']=='2026-09-10'
    assert result['parent_identity']['rssd']==34567
    assert {row['status'] for row in result['attempts']}=={'verified','unavailable'}
    _,issues,_=_catalog(result['documents'],_day('2026-09-10'))
    assert not issues


@pytest.mark.parametrize('fault',['entity','period','unavailable','hash','redirect','ambiguous'])
def test_parent_pdf_errors_and_competing_reports_remain_explicit(tmp_path,fault):
    result,calls=_collect(tmp_path,fault=fault)
    assert result['status']=='incomplete' and not result['documents'] and result['issues']
    assert len(calls)==2 and len(result['attempts'])==2


@pytest.mark.parametrize('fault',['identity','missing','parent'])
def test_parent_identifier_requires_recompiled_fdic_identity(tmp_path,fault):
    result,calls=_collect(tmp_path,change_bank=fault)
    assert result['status']=='incomplete' and not calls and result['issues']


def test_parent_observation_does_not_backdate_to_the_report_period(tmp_path):
    result,calls=_collect(tmp_path,as_of='2026-09-09')
    assert result['status']=='incomplete' and not calls and result['issues']


def test_parent_refresh_reuses_verified_bytes_with_original_dates_and_declares_remote_gap(tmp_path):
    from bellomberg.valuation.input_preparation import _catalog, _day
    original, _ = _collect(tmp_path)
    assert original['status'] == 'ready'
    repeated, calls = _collect(tmp_path, fault='unavailable', as_of='2026-09-11')
    assert repeated['status'] == 'ready', repeated['issues']
    assert len(calls) == 2
    assert repeated['documents'][0]['retrieval'] == original['documents'][0]['retrieval']
    assert repeated['documents'][0]['available_at'] == '2026-09-10'
    assert repeated['documents'][0]['document_sha256'] == original['documents'][0]['document_sha256']
    assert repeated['documents'][1]['text'] == original['documents'][1]['text']
    assert any(a['status'] == 'archived' for a in repeated['attempts'])
    assert any('version' in issue['reason'].lower() for issue in repeated['issues'])
    _, issues, _ = _catalog(repeated['documents'], _day('2026-09-11'))
    assert not issues


@pytest.mark.parametrize('fault', ['entity', 'period', 'hash', 'redirect', 'ambiguous'])
def test_existing_archive_does_not_hide_invalid_live_report(tmp_path, fault):
    original, _ = _collect(tmp_path)
    assert original['status'] == 'ready'
    current, _ = _collect(tmp_path, fault=fault)
    assert current['status'] == 'incomplete', current
    assert not current['documents']


@pytest.mark.parametrize('fault', ['bytes', 'missing_pdf', 'url', 'date', 'version', 'extra', 'ambiguous'])
def test_corrupt_or_ambiguous_retained_parent_is_not_used(tmp_path, fault):
    original, _ = _collect(tmp_path)
    receipt = Path(next(a['archive_receipt'] for a in original['attempts'] if a['status'] == 'verified'))
    body = json.loads(receipt.read_text(encoding='utf-8'))
    if fault == 'bytes': receipt.with_suffix('.pdf').write_bytes(b'%PDF- altered')
    elif fault == 'missing_pdf': receipt.with_suffix('.pdf').unlink()
    elif fault == 'url': body['retrieval']['url'] += '&other=1'
    elif fault == 'date': body['retrieval']['retrieved_at'] = '2099-01-01T00:00:00+00:00'
    elif fault == 'version': body['version'] = True
    elif fault == 'extra': body['approved'] = True
    elif fault == 'ambiguous':
        receipt.with_name('0' * 64 + '.json').write_text(json.dumps(body), encoding='utf-8')
    if fault in ('url', 'date', 'version', 'extra'):
        receipt.write_text(json.dumps(body), encoding='utf-8')
    current, _ = _collect(tmp_path, fault='unavailable')
    assert current['status'] == 'incomplete'
    assert not current['documents'] and current['issues']


def test_failed_archive_save_does_not_report_success_or_leave_readable_receipt(tmp_path, monkeypatch):
    from bellomberg.valuation import parent_report_archive
    def deny(*_args):
        raise PermissionError('synthetic read-only archive')
    monkeypatch.setattr(parent_report_archive.os, 'link', deny)
    current, _ = _collect(tmp_path)
    assert current['status'] == 'incomplete' and not current['documents']
    assert any('PermissionError' in a.get('reason', '') for a in current['attempts'])
    assert not list((tmp_path / 'sources').rglob('*.json'))


def test_recording_identical_report_keeps_first_receipt_and_rejects_future_availability(tmp_path):
    from bellomberg.valuation.parent_report_archive import remember_parent_report
    original, _ = _collect(tmp_path)
    source = original['documents'][0]
    receipt = Path(next(a['archive_receipt'] for a in original['attempts'] if a['status'] == 'verified'))
    old_bytes = receipt.read_bytes()
    newer = {**source['retrieval'], 'retrieved_at': '2026-09-11T10:00:00+00:00'}
    remember_parent_report(receipt.with_suffix('.pdf'), archive_root=tmp_path / 'sources',
                           retrieval=newer, cutoff='2026-09-11')
    assert receipt.read_bytes() == old_bytes
    with pytest.raises(ValueError, match='cutoff'):
        remember_parent_report(receipt.with_suffix('.pdf'), archive_root=tmp_path / 'sources',
                               retrieval=newer, cutoff='2026-09-10')


@pytest.mark.parametrize('malformed', [None, [], 'invalid', {'retrieved_at': None}])
def test_repeated_live_report_rejects_malformed_saved_receipt(tmp_path, malformed):
    from bellomberg.valuation.parent_report_archive import remember_parent_report
    original, _ = _collect(tmp_path)
    source = original['documents'][0]
    receipt = Path(next(a['archive_receipt'] for a in original['attempts'] if a['status'] == 'verified'))
    receipt.write_text(json.dumps({'version': 1, 'retrieval': malformed}), encoding='utf-8')
    original_bytes = receipt.read_bytes()
    with pytest.raises(ValueError):
        remember_parent_report(receipt.with_suffix('.pdf'), archive_root=tmp_path / 'sources',
                               retrieval=source['retrieval'], cutoff='2026-09-10')
    assert receipt.read_bytes() == original_bytes


def test_retained_pdf_is_reextracted_and_legal_identity_is_checked_again(tmp_path):
    original, _ = _collect(tmp_path)
    assert original['status'] == 'ready'
    # A newly verified holder identity cannot adopt the retained old holder's PDF.
    from bellomberg.valuation.preparation_parent_sources import collect_parent_sources
    from test_preparation_bank_sources import _collect as banks, _sources
    bank, _ = banks(tmp_path / 'other-bank')
    primary, _ = _sources()
    primary['metadata']['issuer'] = 'Other Holding'
    # Keep acquisition honest: both the raw FDIC response and normalization must
    # agree on the new holder before the PDF's conflicting cover is reached.
    raw, normalized = bank['documents']
    payload = json.loads(raw['text'])
    payload['data'][0]['data']['NAMEHCR'] = 'Other Holding'
    raw['text'] = json.dumps(payload)
    raw['sha256'] = raw['document_sha256'] = sha256(raw['text'].encode()).hexdigest()
    raw['id'] = raw['sha256']
    raw['retrieval']['document_sha256'] = raw['sha256']
    from bellomberg.valuation.fdic_evidence import normalize_fdic_financials
    changed = normalize_fdic_financials(raw, expected_cert=12345, expected_rssd=23456,
        expected_entity='SYNTHETIC BANK', expected_parent_rssd=34567, expected_report_date='2026-06-30')
    assert changed['status'] == 'ready', changed['issues']
    current = collect_parent_sources(primary=primary, bank_documents=[raw, *changed['documents']],
        on='2026-06-30', as_of='2026-09-11', archive_root=tmp_path / 'sources',
        download=lambda *a, **k: {'stato': 'errore', 'motivo': 'synthetic unavailable'},
        now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert current['status'] == 'incomplete' and not current['documents']
    assert any('legal title' in a.get('reason', '') for a in current['attempts'])


@pytest.mark.parametrize('parent_status',['ready','incomplete'])
@pytest.mark.parametrize('inline_status',['ready','incomplete'])
def test_common_collector_includes_parent_report_and_exposes_unavailable_source(tmp_path,monkeypatch,parent_status,inline_status):
    from test_preparation_sources import _setup
    from bellomberg.valuation import preparation_bank_sources, preparation_parent_sources, market_reference_evidence
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    from bellomberg.valuation import parent_inline_evidence
    _setup(monkeypatch,tmp_path)
    bank={'id':'bank','text':'{}'};parent={'id':'parent','text':'{}'};calls=[]
    inline={'id':'inline-parent','text':'{}'}
    monkeypatch.setattr(parent_inline_evidence,'collect_parent_inline',lambda *a:{
        'status':inline_status,'documents':[inline] if inline_status=='ready' else [],'packets':{},'issues':[]})
    monkeypatch.setattr(preparation_bank_sources,'collect_bank_sources',lambda **k:{'status':'ready','documents':[bank],'issues':[]})
    monkeypatch.setattr(market_reference_evidence,'collect_market_references',lambda **k:{'documents':[],'issues':[]})
    def acquire(**kwargs):
        calls.append(kwargs)
        return {'status':parent_status,'documents':[parent] if parent_status=='ready' else [],
                'issues':[] if parent_status=='ready' else [{'source':'FFIEC parent','reason':'unavailable report'}]}
    monkeypatch.setattr(preparation_parent_sources,'collect_parent_sources',acquire)
    result=collect_preparation_evidence('SYNTH',as_of='2026-02-02',archive_root=tmp_path,
        financial_currency='USD',method_id='bank_residual_income',
        price_fetch=lambda t,on:{'symbol':t,'date':on,'currency':'USD','close':12.5})
    assert len(calls)==1 and calls[0]['bank_documents']==[bank]
    assert result['parent_regulatory_sources']['status']==parent_status
    assert result['preparation_ready'] == ('ready' in (parent_status,inline_status))
    assert (inline in result['documents']) == (inline_status=='ready')
    if parent_status=='ready': assert parent in result['documents']
    else:
        assert any(i['source']=='FFIEC parent' for i in result['issues'])
        assert result['status'] == ('partial' if inline_status=='ready' else 'incomplete')
