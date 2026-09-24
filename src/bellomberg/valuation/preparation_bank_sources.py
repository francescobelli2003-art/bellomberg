"""Acquire exact-quarter FDIC observations from verified SEC subsidiary names."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlencode

from .document_evidence import source_dates
from .fdic_evidence import normalize_fdic_financials, _object, _REQUIRED, _OPTIONAL
from .preparation_exhibits import _sec_parts


LIMITATION = ('Bounded FDIC lookup from standalone English bank-name lines in SEC EX-21 exhibits. '
    'Names match after display case, whitespace and a terminal period only; no aliases or fuzzy matching. '
    'A matched regulatory top holder does not establish direct/full ownership, a complete legal inventory, '
    'unrestricted cash or parent-only accounts. Other layouts and non-FDIC institutions remain uncovered.')


def _label(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('explicit legal entity name required')
    return ' '.join(value.split()).rstrip('.').upper()


def _candidates(primary, documents, cutoff):
    cik, _, _ = _sec_parts(primary['url'])
    issuer = 'CIK:' + cik.zfill(10)
    if (primary.get('metadata') or {}).get('emittente_id') != issuer:
        raise ValueError('opening SEC issuer metadata differs from URL')
    source_dates(primary, cutoff)
    known = {doc['id']: doc for doc in documents}
    names = {}
    for doc in documents:
        meta = doc.get('metadata') or {}
        if not re.fullmatch(r'EX-21(?:\.\d+)?', meta.get('form', '')):
            continue
        parent = known.get(meta.get('primary_document_id'))
        if (meta.get('emittente_id') != issuer or _sec_parts(doc['url'])[0] != cik
                or parent is None or (parent.get('metadata') or {}).get('emittente_id') != issuer
                or _sec_parts(parent['url'])[0] != cik):
            raise ValueError('subsidiary exhibit has no verified same-issuer parent')
        source_dates(doc, cutoff)
        text = doc['text']
        if sha256(text.encode('utf-8')).hexdigest() != doc['sha256']:
            raise ValueError('subsidiary exhibit text hash changed')
        for match in re.finditer(r'^[^\r\n]+$', text, re.M):
            name = match[0].strip()
            if (not 1 < len(name) <= 140 or not re.search(r'\bBank\b', name, re.I)
                    or re.match(r'(?:subsidiaries|parent|name|bank holding)\b', name, re.I)
                    or any(ch in name for ch in '%;:<>')):
                continue
            start = match.start() + len(match[0]) - len(match[0].lstrip())
            names.setdefault(_label(name), {'name': name, 'source_reference': {
                'document_id': doc['id'], 'text_sha256': doc['sha256'], 'published_at': doc['published_at'],
                'char_start': start, 'char_end_exclusive': start + len(name), 'quote': name}})
    if not names or len(names) > 4:
        raise ValueError('one to four explicit bank candidates required; no partial or invented bank inventory')
    return list(names.values())


def collect_bank_sources(*, primary, documents, on, as_of, archive_root, download=None, now=None):
    """Acquire observations only; economic scope and parent ledgers remain separate."""
    result = {'status': 'incomplete', 'documents': [], 'issues': [], 'bank_candidates': [],
              'legal_inventory_certified': False, 'limitation': LIMITATION}
    try:
        cutoff, period = date.fromisoformat(as_of), date.fromisoformat(on)
        if (cutoff.isoformat() != as_of or period.isoformat() != on or period > cutoff
                or primary['metadata']['report_date'] != on
                or period.strftime('%m%d') not in ('0331','0630','0930','1231')):
            raise ValueError('exact quarter-end opening before cutoff required')
        stamp = now if now is not None else datetime.now(timezone.utc)
        if stamp.utcoffset() is None or stamp.astimezone(timezone.utc).date() > cutoff:
            raise ValueError('observed source availability after cutoff or timezone missing')
        parent_name = primary['metadata']['issuer']
        _label(parent_name)
        candidates = _candidates(primary, documents, cutoff)
        result['bank_candidates'] = candidates
        from bellomberg.market_data.lettore_trimestrali import scarica_documento
        root = Path(archive_root).resolve()
        fields = ('NAME','CERT','RSSDID','RSSDHCR','NAMEHCR','REPDTE','INSFDIC','IBA','CBLRIND', *_REQUIRED, *_OPTIONAL)
        for candidate in candidates:
            url = None
            try:
                escaped = ' '.join(candidate['name'].split()).upper().replace('\\', '\\\\').replace('"', '\\"')
                url = 'https://api.fdic.gov/banks/financials?' + urlencode({
                    'filters': 'NAME:"' + escaped + '" AND REPDTE:' + period.strftime('%Y%m%d'),
                    'limit': 2, 'format': 'json', 'fields': ','.join(fields)})
                fetched = (download or scarica_documento)(url, str(root), host_consentiti=['api.fdic.gov'], public_only=True)
                if fetched.get('stato') != 'ok' or fetched.get('url_finale') != url:
                    raise ValueError('FDIC source unavailable or redirected: ' + str(fetched.get('motivo')))
                path = Path(fetched['path']).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('FDIC source outside verified archive')
                raw = path.read_bytes(); digest = sha256(raw).hexdigest()
                if digest != fetched.get('sha256'):
                    raise ValueError('FDIC byte hash differs from download receipt')
                text = raw.decode('utf-8')
                payload = json.loads(text, object_pairs_hook=_object)
                if (type(payload['meta']['total']) is not int or payload['meta']['total'] != 1
                        or type(payload['totals']['count']) is not int or payload['totals']['count'] != 1
                        or not isinstance(payload['data'], list) or len(payload['data']) != 1):
                    raise ValueError('FDIC bank-name lookup missing or ambiguous; exact single report required')
                row = payload['data'][0]['data']
                if _label(row['NAME']) != _label(candidate['name']) or _label(row['NAMEHCR']) != _label(parent_name):
                    raise ValueError('FDIC bank/top-holder names differ from SEC evidence')
                if not isinstance(row['RSSDHCR'], str) or not re.fullmatch('[1-9][0-9]*', row['RSSDHCR']):
                    raise ValueError('FDIC positive top-holder RSSD missing')
                source = {'id': digest, 'url': url, 'text': text, 'sha256': digest, 'document_sha256': digest,
                    'published_at': None, 'available_at': stamp.astimezone(timezone.utc).date().isoformat(),
                    'availability_basis': 'observed_download',
                    'retrieval': {'url': url, 'document_sha256': digest, 'retrieved_at': stamp.isoformat()},
                    'metadata': {'acquisition_role': 'fdic_bank_financials', 'identity_evidence': {
                        'primary_document_id': primary['id'], 'parent_name': parent_name,
                        'bank_name_reference': candidate['source_reference'], 'limitation': LIMITATION}}}
                normalized = normalize_fdic_financials(source, expected_cert=row['CERT'], expected_rssd=row['RSSDID'],
                    expected_entity=row['NAME'], expected_parent_rssd=int(row['RSSDHCR']), expected_report_date=on)
                if normalized['status'] != 'ready':
                    raise ValueError('; '.join(issue['reason'] for issue in normalized['issues']))
                result['documents'].extend([source, *normalized['documents']])
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                result['issues'].append({'source': 'FDIC bank financials', 'url': url,
                    'bank': candidate['name'], 'reason': type(exc).__name__ + ': ' + str(exc)})
        result['status'] = 'incomplete' if result['issues'] else 'ready'
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        result['issues'].append({'source': 'FDIC bank financials', 'reason': type(exc).__name__ + ': ' + str(exc)})
    return result
