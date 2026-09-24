"""Acquire parent-only reports from an observed, recompiled FDIC top holder."""
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlencode

from .document_evidence import pdf_document, source_dates
from .preparation_bank_sources import _label
from .preparation_exhibits import _sec_parts
from .regulatory_evidence import normalize_regulatory_pdf


LIMITATION = ('FDIC top-holder identity selects report requests, not the applicable reporting form or '
    'a complete ownership perimeter. Supported parent-only PDFs require original bytes, matching cover '
    'identity/date and the existing PC/SC normalizer. Observations do not certify unrestricted cash, '
    'complete debt, distribution capacity, economic assumptions or absence of other reports.')


def _identity(primary, documents, on, cutoff):
    from .input_preparation import _catalog
    cik, _, _ = _sec_parts(primary['url'])
    if primary['metadata']['emittente_id'] != 'CIK:' + cik.zfill(10):
        raise ValueError('opening SEC identity differs from URL')
    source_dates(primary, cutoff)
    catalog, issues, _ = _catalog(documents, cutoff)
    if issues:
        raise ValueError('FDIC source cannot be recompiled: ' + '; '.join(i['reason'] for i in issues))
    parents, source_ids = set(), []
    for document in catalog.values():
        meta = document.get('metadata') or {}
        if meta.get('normalizer') != 'fdic_financials_v1':
            continue
        if meta['report_date'] != on or _label(meta['reported_top_holder']) != _label(primary['metadata']['issuer']):
            raise ValueError('FDIC top holder or period differs from opening SEC issuer')
        parents.add(meta['parent_rssd'])
        source_ids.append(document['id'])
    if len(parents) != 1 or not source_ids:
        raise ValueError('one unambiguous recompiled FDIC top holder required')
    return {'rssd': parents.pop(), 'sec_issuer': primary['metadata']['issuer'],
            'sec_document_id': primary['id'], 'fdic_document_ids': sorted(source_ids)}


def _legal_title(document):
    page = document['page_references'][0]
    if document.get('pdf_form_fields') is not None:
        from .pdf_form_evidence import form_field
        return form_field(document, 'NM_LGL', page['pagina'])[0]
    text = document['text'][page['inizio']:page['fine']]
    titles = re.findall(r'^([^\n]+)\nPrinted Name of Chief Financial Officer[^\n]*'
        r'Legal Title of Holding Company \(RSSD 9017\)\s*$', text, re.M)
    if len(titles) != 1:
        raise ValueError('unique literal parent legal title missing from PDF cover')
    return titles[0].strip()


def collect_parent_sources(*, primary, bank_documents, on, as_of, archive_root, download=None, now=None):
    result = {'status': 'incomplete', 'documents': [], 'issues': [], 'attempts': [], 'limitation': LIMITATION}
    try:
        period, cutoff = date.fromisoformat(on), date.fromisoformat(as_of)
        if (period.isoformat() != on or cutoff.isoformat() != as_of or period > cutoff
                or primary['metadata']['report_date'] != on or period.strftime('%m%d') not in ('0331','0630','0930','1231')):
            raise ValueError('exact quarter-end opening before cutoff required')
        stamp = now if now is not None else datetime.now(timezone.utc)
        if stamp.utcoffset() is None or stamp.astimezone(timezone.utc).date() > cutoff:
            raise ValueError('observed parent-source availability after cutoff or timezone missing')
        parent = _identity(primary, bank_documents, on, cutoff)
        result['parent_identity'] = parent
        forms = [('FRY9LP', 'FR Y-9LP')]
        if period.strftime('%m%d') in ('0630','1231'):
            forms.append(('FRY9SP', 'FR Y-9SP'))
        from bellomberg.market_data.lettore_trimestrali import scarica_documento
        root = Path(archive_root).resolve()
        verified = []
        for code, form in forms:
            url = 'https://www.ffiec.gov/npw/FinancialReport/ReturnFinancialReportPDF?' + urlencode(
                {'dt': period.strftime('%Y%m%d'), 'id': parent['rssd'], 'rpt': code})
            attempt = {'url': url, 'form': form, 'status': 'unavailable'}
            result['attempts'].append(attempt)
            try:
                fetched = (download or scarica_documento)(url, str(root), host_consentiti=['www.ffiec.gov'], public_only=True)
                retained = None
                if fetched.get('stato') != 'ok':
                    from .parent_report_archive import archived_parent_report
                    attempt['remote_reason'] = str(fetched.get('motivo'))
                    retained = archived_parent_report(url, archive_root=root, cutoff=cutoff)
                    if retained is None:
                        raise ValueError('FFIEC report unavailable: ' + str(fetched.get('motivo')))
                    fetched = {'stato': 'ok', 'url_finale': url, 'path': retained['path'],
                               'sha256': retained['retrieval']['document_sha256']}
                if fetched.get('url_finale') != url:
                    raise ValueError('FFIEC report redirected')
                path = Path(fetched['path']).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('parent PDF outside verified archive')
                raw = path.read_bytes(); digest = sha256(raw).hexdigest()
                if digest != fetched.get('sha256') or not raw.startswith(b'%PDF-'):
                    raise ValueError('parent PDF byte hash or format differs from receipt')
                receipt = retained['retrieval'] if retained else {
                    'url': url, 'document_sha256': digest, 'retrieved_at': stamp.isoformat()}
                source = pdf_document(path, archive_root=root, cutoff=as_of, retrieval=receipt,
                    metadata={'acquisition_role': 'ffiec_parent_report', 'parent_identity': parent})
                if source['extraction_coverage'].get('pages_without_text'):
                    raise ValueError('parent PDF has pages without extracted text')
                entity = _legal_title(source)
                if _label(entity) != _label(parent['sec_issuer']):
                    raise ValueError('PDF legal title differs from verified parent identity')
                normalized = normalize_regulatory_pdf(source, expected_form=form,
                    expected_entity=entity, expected_report_date=on)
                if normalized['status'] != 'ready':
                    raise ValueError('; '.join(i['reason'] for i in normalized['issues']))
                if retained:
                    attempt.update(status='archived', document_id=digest,
                                   archive_receipt=retained['receipt_path'],
                                   original_retrieval=receipt, remote_version_verified=False)
                    result['issues'].append({'source': 'FFIEC parent report', 'url': url,
                        'reason': 'Archived report reused with original receipt; current remote version '
                                  'unverified: ' + attempt['remote_reason']})
                else:
                    from .parent_report_archive import remember_parent_report
                    stored = remember_parent_report(path, archive_root=root, retrieval=receipt, cutoff=as_of)
                    attempt.update(status='verified', document_id=digest, archive_receipt=stored,
                                   remote_version_verified=True)
                verified.append([source, *normalized['documents']])
            except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
                attempt['reason'] = type(exc).__name__ + ': ' + str(exc)
        if len(verified) != 1:
            raise ValueError('one verified parent-only report required; unavailable or competing report forms remain unresolved')
        result.update(status='ready', documents=verified[0])
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        result['issues'].append({'source': 'FFIEC parent report', 'reason': type(exc).__name__ + ': ' + str(exc)})
    return result
