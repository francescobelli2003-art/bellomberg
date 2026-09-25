"""Replay Filing Diff's curated PDF profile; no ticker/class identity inference.

This verifies consistency with the supplied profile and extracted primary text,
not the authority of an arbitrary profile or the raw PDF bytes at replay time.
Acquisition rechecks those bytes. Publication remains a separate source claim.
"""
from datetime import date
from hashlib import sha256
import re

from bellomberg.market_data.filing_verifica import _cerca_prova, _periodo_con_prova, _DURATE, _id
from .document_evidence import verify_page_references


SCHEMA = 'filing_pdf_v1'


def verify_filing_pdf(document):
    """Raise on changed identity, dates, rules, citations or page boundaries."""
    try:
        packet = document['filing_verification']
        metadata, body = document['metadata'], document['text']
        digest, url = document['document_sha256'], document['url']
        if (packet['schema'] != SCHEMA or metadata['filing_verification'] != SCHEMA
                or not re.fullmatch(r'[0-9a-f]{64}', digest) or document['id'] != digest
                or packet['document_sha256'] != digest or packet['url'] != url
                or packet['text_sha256'] != sha256(body.encode('utf-8')).hexdigest()
                or packet['identity_basis'] != 'curated_filing_profile'
                or packet['security_identity_verified'] is not False):
            raise ValueError('identity/hash/schema mismatch')
        pages = document['page_references']
        verify_page_references(body, pages)
        declared = packet['metadata']
        fields = ('emittente_id', 'lingua', 'tipo', 'perimetro', 'periodo_inizio', 'periodo_fine')
        if (set(declared) != set(fields) or any(not isinstance(declared[k], str)
                or not declared[k].strip() or declared[k] != metadata.get(k) for k in fields)
                or _id(declared['emittente_id']) != declared['emittente_id']
                or declared['periodo_fine'] != metadata['report_date']
                or declared['periodo_inizio'] != metadata['report_start']):
            raise ValueError('declared metadata mismatch')
        rules = packet['rules']
        expected = {field: _cerca_prova(body[:20_000] if field == 'emittente' else body,
            rules[field], field, url, digest) for field in ('emittente', 'lingua', 'tipo', 'perimetro')}
        start, end, expected['periodo'] = _periodo_con_prova(body, rules['periodo'],
            declared['tipo'], date.fromisoformat(declared['periodo_fine']), url, digest)
        low, high = _DURATE[declared['tipo']]
        available = document.get('published_at') or document.get('available_at')
        if (start.isoformat() != declared['periodo_inizio'] or not low <= (end-start).days+1 <= high
                or end > date.fromisoformat(available) or expected != packet['proofs']):
            raise ValueError('period or original proof mismatch')
        for proof in expected.values():
            if sum(p['inizio'] <= proof['inizio'] < proof['fine'] <= p['fine'] for p in pages) != 1:
                raise ValueError('proof does not belong to one primary PDF page')
    except (KeyError, TypeError, ValueError, AttributeError, re.error) as exc:
        raise ValueError('PDF filing verification invalid: ' + str(exc)) from exc
