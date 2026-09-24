"""Immutable parent-report bytes and their original download receipts.

This archive does not establish legal identity or current remote availability.
The collector re-extracts and validates a retained PDF before using its facts.
"""
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlsplit

from .document_evidence import source_dates


def _bucket(root, url):
    parts = urlsplit(url)
    query = parse_qs(parts.query, strict_parsing=True)
    if (parts.scheme != 'https' or parts.netloc != 'www.ffiec.gov'
            or parts.path != '/npw/FinancialReport/ReturnFinancialReportPDF'
            or parts.fragment or set(query) != {'dt', 'id', 'rpt'}
            or any(len(v) != 1 for v in query.values())
            or not re.fullmatch(r'[0-9]{8}', query['dt'][0])
            or not re.fullmatch(r'[1-9][0-9]*', query['id'][0])
            or query['rpt'][0] not in ('FRY9LP', 'FRY9SP')):
        raise ValueError('exact FFIEC parent-report URL required')
    root = Path(root).resolve()
    bucket = (root / 'parent_report_receipts' / sha256(url.encode()).hexdigest()).resolve()
    if not bucket.is_relative_to(root):
        raise ValueError('parent receipt directory outside archive')
    return bucket


def _immutable(path, raw):
    """Install only complete bytes, with no overwrite of a concurrent writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.resolve().is_relative_to(path.parent.resolve()):
        raise ValueError('parent archive file resolves outside its directory')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('immutable parent archive content differs')
    finally:
        temporary.unlink()


def remember_parent_report(path, *, archive_root, retrieval, cutoff):
    """Preserve received bytes; caller owns receipt authenticity and source review."""
    root = Path(archive_root).resolve()
    source = Path(path).resolve()
    if not source.is_relative_to(root):
        raise ValueError('parent PDF outside archive')
    raw = source.read_bytes()
    digest = sha256(raw).hexdigest()
    if not raw.startswith(b'%PDF-'):
        raise ValueError('parent archive requires PDF bytes')
    document = {'url': retrieval.get('url'), 'document_sha256': digest, 'retrieval': retrieval,
                'published_at': None, 'availability_basis': 'observed_download'}
    day = date.fromisoformat(cutoff)
    if day.isoformat() != cutoff:
        raise ValueError('canonical parent archive cutoff required')
    source_dates(document, day)
    bucket = _bucket(root, document['url'])
    receipt_path = bucket / (digest + '.json')
    pdf_path = bucket / (digest + '.pdf')
    if not receipt_path.resolve().is_relative_to(bucket):
        raise ValueError('parent receipt resolves outside its directory')
    _immutable(pdf_path, raw)
    if receipt_path.exists():
        # Same received bytes retain the first receipt, never today's date.
        retained = json.loads(receipt_path.read_text(encoding='utf-8'))
        if (not isinstance(retained, dict) or set(retained) != {'version', 'retrieval'}
                or type(retained.get('version')) is not int or retained['version'] != 1
                or not isinstance(retained.get('retrieval'), dict)):
            raise ValueError('existing parent receipt differs from its identity')
        source_dates({**document, 'retrieval': retained['retrieval']}, day)
    else:
        _immutable(receipt_path, (json.dumps({'version': 1, 'retrieval': retrieval},
            sort_keys=True, separators=(',', ':')) + '\n').encode())
    return str(receipt_path)


def archived_parent_report(url, *, archive_root, cutoff):
    """Return one unambiguous historical report, retaining its availability date."""
    bucket = _bucket(archive_root, url)
    receipts = sorted(bucket.glob('*.json'))
    if not receipts:
        return None
    if len(receipts) != 1:
        raise ValueError('multiple archived versions; explicit report reconciliation required')
    path = receipts[0]
    if not path.resolve().is_relative_to(bucket):
        raise ValueError('retained parent receipt outside its directory')
    body = json.loads(path.read_text(encoding='utf-8'))
    if set(body) != {'version', 'retrieval'} or type(body['version']) is not int or body['version'] != 1:
        raise ValueError('invalid parent archive receipt')
    receipt = body['retrieval']
    source_dates({'url': url, 'document_sha256': path.stem, 'retrieval': receipt,
                  'published_at': None, 'availability_basis': 'observed_download'}, cutoff)
    pdf_path = path.with_suffix('.pdf').resolve()
    if not pdf_path.is_relative_to(Path(archive_root).resolve()):
        raise ValueError('retained parent PDF outside archive')
    raw = pdf_path.read_bytes()
    if sha256(raw).hexdigest() != path.stem or not raw.startswith(b'%PDF-'):
        raise ValueError('retained parent PDF hash or format mismatch')
    return {'path': str(pdf_path), 'retrieval': receipt, 'receipt_path': str(path)}
