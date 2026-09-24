"""Earnings evidence uses synthetic SEC documents and a bounded local transport."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest


PARENT = "https://www.sec.gov/Archives/edgar/data/123/000000012326000002/event.htm"
RELEASE = PARENT.rsplit("/", 1)[0] + "/release.htm"
ROW = '<tr><td>99.1</td><td><a href="release.htm">Earnings press release</a></td></tr>'


def _anchor():
    return {"url": "https://www.sec.gov/Archives/edgar/data/123/000000012326000003/quarter.htm",
            "published_at": "2026-08-03", "metadata": {"emittente_id": "CIK:0000000123",
            "issuer": "Synthetic Issuer", "report_date": "2026-06-30", "form": "10-Q"}}


def _entry():
    return {"ticker": "SYNTH", "url": PARENT, "form": "8-K", "filed_date": "2026-07-30",
            "report_date": "2026-07-30", "accession": "0000000123-26-000002",
            "items": ["2.02", "9.01"], "emittente_id": "CIK:0000000123", "issuer": "Synthetic Issuer"}


def _transport(tmp_path, calls, *, html=None, failure=None):
    content = {PARENT: (html or '<html><table>' + ROW + '</table></html>').encode(),
               RELEASE: b'<html><p>Synthetic issuer raises its full-year revenue guidance.</p></html>'}
    def download(url, root, **kwargs):
        calls.append(url)
        assert kwargs == {"host_consentiti": ["www.sec.gov", "sec.gov"], "public_only": True}
        if failure == "unavailable" and url == RELEASE:
            return {"stato": "errore", "motivo": "synthetic HTTP 403"}
        raw = content[url]
        p = Path(root) / (sha256(raw).hexdigest() + '.htm')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return {"stato": "ok", "path": str(p), "sha256": '0'*64 if failure == "changed" else sha256(raw).hexdigest(),
                "url_finale": RELEASE if failure == "redirect" and url == PARENT else url}
    return download


def _collect(tmp_path, *, entries=None, fetch=None, html=None, failure=None, max_filings=4):
    from bellomberg.valuation.preparation_earnings import collect_earnings_evidence
    calls = []
    if fetch is None:
        fetch = lambda *a, **k: deepcopy([_entry()] if entries is None else entries)
    result = collect_earnings_evidence('SYNTH', primary=_anchor(), as_of='2026-09-10',
        archive_root=tmp_path, fetch=fetch, download=_transport(tmp_path, calls, html=html, failure=failure),
        max_filings=max_filings)
    return result, calls


def test_verified_earnings_exhibit_joins_source_catalog_with_its_original_filing_date(tmp_path):
    result, calls = _collect(tmp_path)
    assert result['status'] == 'ready' and not result['issues']
    assert calls == [PARENT, RELEASE]
    assert {d['metadata']['form'] for d in result['documents']} == {'8-K', 'EX-99.1'}
    assert all(d['published_at'] == '2026-07-30' for d in result['documents'])
    release = result['documents'][1]
    assert release['url'] == RELEASE and release['metadata']['primary_document_id'] == result['documents'][0]['id']
    assert release['sha256'] == sha256(release['text'].encode()).hexdigest()
    assert 'guidance' in release['text']
    assert result['selection']['opening_date'] == '2026-06-30'
    assert result['numeric_guidance_extracted'] is False


@pytest.mark.parametrize('change', ['issuer', 'accession', 'future', 'old', 'not_earnings', 'bad_date'])
def test_unrelated_future_or_unqualified_entries_are_not_downloaded(tmp_path, change):
    entry = _entry()
    if change == 'issuer': entry['emittente_id'] = 'CIK:0000000999'
    elif change == 'accession': entry['accession'] = '0000000123-26-000009'
    elif change == 'future': entry['filed_date'] = '2026-10-01'
    elif change == 'old': entry['filed_date'] = '2026-05-01'
    elif change == 'not_earnings': entry['items'] = ['5.02']
    else: entry['filed_date'] = 'not a date'
    result, calls = _collect(tmp_path, entries=[entry])
    assert not calls and not result['documents']
    assert result['status'] in ('incomplete', 'not_found')
    assert result['issues'] or result['selection']['excluded']


@pytest.mark.parametrize('failure', ['unavailable', 'changed', 'redirect'])
def test_failed_acquisition_cannot_become_complete_evidence(tmp_path, failure):
    result, _ = _collect(tmp_path, failure=failure)
    assert result['status'] == 'incomplete' and result['issues']
    assert not any(d['metadata']['form'].startswith('EX-') for d in result['documents'])


@pytest.mark.parametrize('row', [
    '<tr><td>99.1</td><td>Earnings press release, attachment missing</td></tr>',
    ROW.replace('release.htm', 'https://example.org/other.htm'),
    ROW.replace('release.htm', '../000000012326000009/release.htm'),
    ROW + ROW.replace('release.htm', 'another.htm'),
])
def test_missing_external_or_conflicting_exhibit_link_remains_a_gap(tmp_path, row):
    result, calls = _collect(tmp_path, html='<html><table>'+row+'</table></html>')
    assert result['status'] == 'incomplete' and result['issues']
    assert calls == [PARENT]


def test_duplicate_catalog_entry_and_identical_dom_rows_do_not_repeat_downloads(tmp_path):
    result, calls = _collect(tmp_path, entries=[_entry(), _entry()], html='<html><table>'+ROW+ROW+'</table></html>')
    assert result['status'] == 'ready' and calls == [PARENT, RELEASE]
    assert len(result['documents']) == 2


def test_empty_layout_cells_do_not_hide_the_explicit_exhibit_number(tmp_path):
    row = ROW.replace('<tr>', '<tr><td> </td>').replace('<td><a', '<td></td><td><a')
    result, calls = _collect(tmp_path, html='<html><table>'+row+'</table></html>')
    assert result['status'] == 'ready' and calls == [PARENT, RELEASE]


def test_provider_error_is_not_reported_as_no_published_guidance(tmp_path):
    def fetch(*args, **kwargs):
        kwargs['motivo'].append('synthetic submissions unavailable')
        return []
    result, calls = _collect(tmp_path, fetch=fetch)
    assert result['status'] == 'incomplete' and not calls
    assert any('submissions unavailable' in r['reason'] for r in result['issues'])


def test_conflicting_catalog_dates_quarantine_the_filing_instead_of_using_first(tmp_path):
    other = {**_entry(), 'filed_date': '2026-07-31'}
    result, calls = _collect(tmp_path, entries=[_entry(), other, _entry()])
    assert result['status'] == 'incomplete' and result['issues']
    assert not calls and not result['documents']


def test_catalog_cap_is_disclosed_even_when_returned_entries_repeat(tmp_path):
    result, calls = _collect(tmp_path, entries=[_entry()] * 40)
    assert result['status'] == 'incomplete'
    assert any('cap reached' in r['reason'] for r in result['issues'])
    assert calls == [PARENT, RELEASE]


def test_explicit_filing_limit_prevents_downloading_older_candidate(tmp_path):
    newer = {**_entry(), 'filed_date': '2026-08-01', 'accession': '0000000123-26-000004',
             'url': PARENT.replace('000000012326000002', '000000012326000004')}
    result, calls = _collect(tmp_path, entries=[_entry(), newer], max_filings=1)
    assert result['status'] == 'incomplete'  # synthetic newer document is unavailable
    assert calls == [newer['url']]
    assert {'url': PARENT, 'reason': 'filing limit'} in result['selection']['excluded']


@pytest.mark.parametrize('missing_call', [False, True])
def test_common_collector_retains_earnings_evidence_without_moving_opening_date(tmp_path, monkeypatch, missing_call):
    from test_preparation_sources import _setup
    from bellomberg.valuation import preparation_earnings
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    _setup(monkeypatch, tmp_path)
    doc = {'id': 'synthetic-release', 'text': 'Documented company outlook', 'metadata': {'form': 'EX-99.1'}}
    gap = {'source': 'SEC earnings releases', 'url': 'https://issuer.example/call',
           'reason': 'Explicitly referenced call material not acquired'}
    references = [{'url': gap['url'], 'status': 'not_acquired'}] if missing_call else []
    status = 'incomplete' if missing_call else 'ready'
    calls = []
    def earnings(*args, **kwargs):
        calls.append(kwargs)
        return {'status': status, 'documents': [doc], 'issues': [gap] if missing_call else [],
                'referenced_materials': references, 'numeric_guidance_extracted': False}
    monkeypatch.setattr(preparation_earnings, 'collect_earnings_evidence', earnings)
    result = collect_preparation_evidence('SYNTH', as_of='2026-02-02', archive_root=tmp_path,
        price_fetch=lambda ticker,on: {'symbol':ticker,'date':on,'currency':'EUR','close':12.5})
    assert len(calls) == 1 and result['selection']['opening_date'] == '2025-12-31'
    assert doc in result['documents'] and result['earnings_releases']['status'] == status
    assert result['earnings_releases']['numeric_guidance_extracted'] is False
    assert result['earnings_releases']['referenced_materials'] == references
    if missing_call:
        assert gap in result['issues']


@pytest.mark.parametrize('opening_form',['6-K','20-F'])
@pytest.mark.parametrize('html',[
    '<html><h1>Interim management report</h1><p>Company outlook: next half sales broadly stable.</p></html>',
    '<html><p>Company notice concerning voting rights and repurchased shares.</p></html>',
])
def test_foreign_reports_preserve_narrative_without_claiming_numeric_guidance(tmp_path,opening_form,html):
    from bellomberg.valuation.preparation_earnings import collect_earnings_evidence
    primary=_anchor();primary['metadata']['form']=opening_form
    entry={**_entry(),'form':'6-K','items':[]};calls=[];queries=[]
    def fetch(ticker,**kwargs): queries.append(kwargs);return [entry]
    result=collect_earnings_evidence('SYNTH',primary=primary,as_of='2026-09-10',archive_root=tmp_path,
        fetch=fetch,download=_transport(tmp_path,calls,html='<h1>FORM 6-K</h1>'+html))
    assert queries[0]['form_types']==['6-K','6-K/A']
    assert result['status']=='ready' and calls==[PARENT]
    assert len(result['documents'])==1 and result['documents'][0]['metadata']['form']=='6-K'
    assert result['documents'][0]['metadata']['evidence_role']=='foreign_current_report'
    assert result['documents'][0]['published_at']==entry['filed_date']
    assert result['numeric_guidance_extracted'] is False
    assert 'not a guidance classification' in result['limitation']


@pytest.mark.parametrize('fault',['issuer','accession','future','changed','redirect','conflicting'])
def test_foreign_reports_retain_provenance_and_cutoff_guards(tmp_path,fault):
    from bellomberg.valuation.preparation_earnings import collect_earnings_evidence
    primary=_anchor();primary['metadata']['form']='6-K'
    entry={**_entry(),'form':'6-K','items':[]}
    if fault=='issuer': entry['emittente_id']='CIK:0000000999'
    if fault=='accession': entry['accession']='0000000123-26-000099'
    if fault=='future': entry['filed_date']='2026-12-01'
    entries=[entry,{**entry,'filed_date':'2026-07-31'}] if fault=='conflicting' else [entry]
    calls=[]
    result=collect_earnings_evidence('SYNTH',primary=primary,as_of='2026-09-10',archive_root=tmp_path,
        fetch=lambda *a,**k:entries,download=_transport(tmp_path,calls,html='<html>Foreign report</html>',failure=fault))
    assert not result['documents'] and result['status'] in ('incomplete','not_found')


def test_foreign_report_explicit_exhibit_keeps_its_own_publication_provenance(tmp_path):
    from bellomberg.valuation.preparation_earnings import collect_earnings_evidence
    primary=_anchor();primary['metadata']['form']='20-F';calls=[]
    result=collect_earnings_evidence('SYNTH',primary=primary,as_of='2026-09-10',archive_root=tmp_path,
        fetch=lambda *a,**k:[{**_entry(),'form':'6-K','items':[]}],
        download=_transport(tmp_path,calls,html='<h1>FORM 6-K</h1><table>'+ROW+'</table>'))
    assert result['status']=='ready' and calls==[PARENT,RELEASE]
    assert result['documents'][1]['metadata']['publication_basis'].endswith('SEC-dated 6-K')


def test_foreign_http_success_with_nonfiling_body_stays_incomplete(tmp_path):
    from bellomberg.valuation.preparation_earnings import collect_earnings_evidence
    primary=_anchor();primary['metadata']['form']='6-K';calls=[]
    result=collect_earnings_evidence('SYNTH',primary=primary,as_of='2026-09-10',archive_root=tmp_path,
        fetch=lambda *a,**k:[{**_entry(),'form':'6-K','items':[]}],
        download=_transport(tmp_path,calls,html='<html>Request temporarily unavailable</html>'))
    assert result['status']=='incomplete' and not result['documents']


@pytest.mark.parametrize('same_exhibit', [False, True])
@pytest.mark.parametrize('access', ['You may access the audio conference at', 'A replay of the conference call is available at'])
def test_referenced_call_material_is_not_confused_with_acquired_announcement(tmp_path, same_exhibit, access):
    from bellomberg.valuation.preparation_earnings import collect_earnings_evidence
    primary = _anchor(); primary['metadata']['form'] = '6-K'
    target = RELEASE if same_exhibit else 'https://issuer.example/static-files/call'
    html = '<h1>FORM 6-K</h1><p>' + access + ' the investor website: ' + target + '.</p>'
    if same_exhibit:
        html += '<table>' + ROW + '</table>'
    calls = []
    result = collect_earnings_evidence('SYNTH', primary=primary, as_of='2026-09-10', archive_root=tmp_path,
        fetch=lambda *a, **k: [{**_entry(), 'form': '6-K', 'items': []}],
        download=_transport(tmp_path, calls, html=html))
    assert result['status'] == ('ready' if same_exhibit else 'incomplete')
    assert calls == ([PARENT, RELEASE] if same_exhibit else [PARENT])
    reference, = result['referenced_materials']
    assert reference['url'] == target
    assert reference['status'] == ('acquired' if same_exhibit else 'not_acquired')
    source = result['documents'][0]
    proof = reference['source_reference']
    assert proof['document_id'] == source['id'] and proof['text_sha256'] == source['sha256']
    assert source['text'][proof['char_start']:proof['char_end_exclusive']] == proof['quote']
    assert target in proof['quote'] and source['sha256'] == sha256(source['text'].encode()).hexdigest()
    if not same_exhibit:
        assert any('referenced call material not acquired' in issue['reason'] for issue in result['issues'])
    assert not result['numeric_guidance_extracted']


@pytest.mark.parametrize('text', ['Read company news at',
    'You may access the conference call on request. Read company news at',
    'You may access the conference call ' + 'other information ' * 40])
def test_general_website_link_is_not_a_call_material_reference(tmp_path, text):
    result, _ = _collect(tmp_path, html='<p>'+text+' https://issuer.example/.</p><table>'+ROW+'</table>')
    assert result['status'] == 'ready'
    assert not result.get('referenced_materials')


@pytest.mark.parametrize('conflict',[False,True])
def test_common_catalog_deduplicates_only_identical_primary_provenance(tmp_path,monkeypatch,conflict):
    from test_preparation_sources import _setup
    from bellomberg.valuation import preparation_earnings
    from bellomberg.valuation.preparation_sources import collect_preparation_evidence
    primary=_setup(monkeypatch,tmp_path);repeated=deepcopy(primary)
    repeated.pop('archive_path');repeated['metadata']['evidence_role']='foreign_current_report'
    if conflict: repeated['published_at']='2026-02-02'
    monkeypatch.setattr(preparation_earnings,'collect_earnings_evidence',lambda *a,**k:{
        'status':'ready','documents':[repeated],'issues':[],'numeric_guidance_extracted':False})
    result=collect_preparation_evidence('SYNTH',as_of='2026-02-02',archive_root=tmp_path,
        price_fetch=lambda ticker,on:{'symbol':ticker,'date':on,'currency':'EUR','close':12.5})
    if conflict:
        assert not result['preparation_ready'] and not result['documents']
        assert any('provenance' in issue['reason'] for issue in result['issues'])
    else:
        assert result['preparation_ready']
        same=[d for d in result['documents'] if d['id']==primary['id']]
        assert len(same)==1 and same[0]['metadata']==primary['metadata']
        assert result['earnings_releases']['reused_document_ids']==[primary['id']]
