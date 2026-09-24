"""SEC parent-only instant balances, with explicit XBRL entity dimensions.

The acquisition packet is extracted from hash-verified filing bytes. Like PDF
widgets, it is retained on the original source and recompiled before consumption.
It is source data, never a valuation record or an ownership/cash-transfer claim.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import math
import re

from .preparation_exhibits import _sec_parts


CONCEPTS = ('StockholdersEquity', 'PreferredStockValue', 'CashAndCashEquivalentsAtCarryingValue')
NORMALIZER = 'sec_parent_inline_v1'
TAXONOMY = 'sec-parent-us-gaap'


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def extract_parent_packet(source, raw):
    """Called only at acquisition; preserve explicit tags, units and contexts."""
    from bs4 import BeautifulSoup
    digest = sha256(raw).hexdigest()
    if source.get('id') != digest or source.get('document_sha256') != digest:
        raise ValueError('parent inline source bytes differ from archived filing identity')
    soup = BeautifulSoup(raw, 'html.parser')
    namespaces = {key[6:]: value for key, value in (soup.html.attrs if soup.html else {}).items()
                  if key.startswith('xmlns:')}
    for node in soup.find_all(True):
        if any(key.startswith('xmlns:') and namespaces.get(key[6:]) != value
               for key, value in node.attrs.items()):
            raise ValueError('local namespace changes unsupported in parent inline source')
    def indexed(tag):
        rows = soup.find_all(tag)
        result = {node.get('id'): node for node in rows}
        if None in result or len(result) != len(rows):
            raise ValueError('duplicate or missing inline context/unit ID')
        return result
    contexts, units = indexed('xbrli:context'), indexed('xbrli:unit')
    parents = {key for key, node in contexts.items() if node.find('xbrldi:explicitmember',
        attrs={'dimension': 'srt:ConsolidatedEntitiesAxis'}, string='srt:ParentCompanyMember')}
    facts = [node for node in soup.find_all('ix:nonfraction')
             if node.get('name') in {'us-gaap:' + c for c in CONCEPTS}
             and (node.get('contextref') in parents or node.get('name') == 'us-gaap:PreferredStockValue')]
    refs = {node.get('contextref') for node in facts}
    unitrefs = {node.get('unitref') for node in facts}
    names = {node.get_text(' ', strip=True) for node in soup.find_all('ix:nonnumeric',
             attrs={'name': 'dei:EntityRegistrantName'})}
    if len(names) != 1 or not next(iter(names)):
        raise ValueError('unique SEC registrant legal name required')
    packet = {'format': NORMALIZER, 'source_document_sha256': digest,
        'issuer': next(iter(names)), 'namespaces': namespaces,
        'contexts': {key: str(contexts[key]) for key in sorted(refs)},
        'units': {key: str(units[key]) for key in sorted(unitrefs)},
        'facts': [str(node) for node in facts]}
    packet['sha256'] = sha256(_encoded(packet).encode()).hexdigest()
    return packet


def normalize_parent_inline(source, *, expected_report_date):
    from bs4 import BeautifulSoup
    try:
        if date.fromisoformat(expected_report_date).isoformat() != expected_report_date:
            raise ValueError('exact ISO opening date required')
        cik, accession, _ = _sec_parts(source['url'])
        metadata = source['metadata']
        if (metadata.get('emittente_id') != 'CIK:' + cik.zfill(10)
                or str(metadata.get('accession', '')).replace('-', '') != accession
                or metadata.get('report_date') != expected_report_date
                or metadata.get('form') not in ('10-K', '10-Q', '10-K/A', '10-Q/A')
                or expected_report_date > source['published_at']):
            raise ValueError('SEC filing identity, date or accounting form differs')
        packet = deepcopy(source['inline_parent_fields'])
        supplied = packet.pop('sha256')
        if (packet.get('format') != NORMALIZER or supplied != sha256(_encoded(packet).encode()).hexdigest()
                or packet['source_document_sha256'] != source['id']
                or source['document_sha256'] != source['id']):
            raise ValueError('inline packet or original source identity changed')
        ns = packet['namespaces']
        if not re.fullmatch(r'https?://xbrl.sec.gov/dei/20[0-9]{2}', ns.get('dei', '')):
            raise ValueError('unverified SEC registrant namespace')
        for prefix, uri in (('xbrli', 'http://www.xbrl.org/2003/instance'),
                            ('xbrldi', 'http://xbrl.org/2006/xbrldi'),
                            ('ix', 'http://www.xbrl.org/2013/inlineXBRL')):
            if ns.get(prefix) != uri:
                raise ValueError('unverified inline/context namespace')
        for prefix in ('srt', 'us-gaap'):
            if not re.fullmatch(r'https?://fasb.org/' + prefix + r'/20[0-9]{2}', ns.get(prefix, '')):
                raise ValueError('unverified standard accounting namespace')
        if ns.get('iso4217') != 'http://www.xbrl.org/2003/iso4217':
            raise ValueError('unverified currency namespace')
        issuer = packet['issuer']
        if not isinstance(issuer, str) or not issuer.strip():
            raise ValueError('SEC legal issuer missing')
        contexts = {}
        for key, xml in packet['contexts'].items():
            node = BeautifulSoup(xml, 'html.parser').find('xbrli:context')
            identifier, instant = node.find('xbrli:identifier'), node.find('xbrli:instant')
            if node.get('id') != key or identifier is None or instant is None:
                continue  # Duration contexts cannot prove an opening balance.
            if (len(node.find_all('xbrli:entity')) != 1 or len(node.find_all('xbrli:identifier')) != 1
                    or len(node.find_all('xbrli:period')) != 1 or len(node.find_all('xbrli:instant')) != 1
                    or node.find('xbrli:startdate') or node.find('xbrli:enddate') or node.find('xbrli:forever')
                    or identifier.parent.name != 'xbrli:entity' or instant.parent.name != 'xbrli:period'
                    or identifier.parent.parent != node or instant.parent.parent != node):
                raise ValueError('ambiguous inline entity or opening period')
            if (identifier.get('scheme') != 'http://www.sec.gov/CIK'
                    or identifier.get_text(strip=True).zfill(10) != cik.zfill(10)):
                raise ValueError('inline context CIK differs from filing')
            if instant.get_text(strip=True) != expected_report_date:
                continue
            members = node.find_all('xbrldi:explicitmember')
            segment = node.find('xbrli:segment')
            if node.find('xbrldi:typedmember') or node.find('xbrli:scenario'):
                continue
            if segment and any(child.name != 'xbrldi:explicitmember' for child in segment.find_all(recursive=False)):
                continue
            if not members:
                contexts[key] = 'issuer_capital'
            elif (len(members) == 1 and members[0].get('dimension') == 'srt:ConsolidatedEntitiesAxis'
                  and members[0].get_text(strip=True) == 'srt:ParentCompanyMember'):
                contexts[key] = 'parent_only'
        values = {}
        for xml in packet['facts']:
            node = BeautifulSoup(xml, 'html.parser').find('ix:nonfraction')
            role = contexts.get(node.get('contextref'))
            concept = str(node.get('name', '')).removeprefix('us-gaap:')
            if role is None or concept not in CONCEPTS or node.get('name') != 'us-gaap:' + concept:
                continue
            if role != 'parent_only' and concept != 'PreferredStockValue':
                continue
            if node.get('xsi:nil') in ('true', '1') or node.get('continuedat'):
                raise ValueError('nil or continued monetary fact unsupported')
            unit = BeautifulSoup(packet['units'][node['unitref']], 'html.parser').find('xbrli:unit')
            measures = unit.find_all('xbrli:measure')
            if unit.get('id') != node['unitref'] or len(measures) != 1 or measures[0].get_text(strip=True) != 'iso4217:USD' or unit.find('xbrli:divide'):
                raise ValueError('parent balance requires a simple USD unit')
            text = node.get_text('', strip=True)
            fmt = node.get('format')
            local = None
            if fmt:
                prefix, local = fmt.split(':')
                if not ns.get(prefix, '').startswith('http://www.xbrl.org/inlineXBRL/transformation/'):
                    raise ValueError('unknown inline numeric transformation namespace')
            if local == 'fixed-zero' and text:
                value = Decimal(0)  # Explicit XBRL zero, never a missing-value default.
            elif local in (None, 'num-dot-decimal') and re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?', text):
                value = Decimal(text.replace(',', ''))
            else:
                raise ValueError('unsupported inline numeric transformation or text')
            scale = int(node.get('scale', '0'))
            if not -12 <= scale <= 12 or node.get('sign') not in (None, '-'):
                raise ValueError('unsupported numeric scale/sign')
            value *= Decimal(10) ** scale
            if node.get('sign') == '-': value = -value
            if role == 'issuer_capital' and value != 0:
                raise ValueError('nonzero preferred capital requires parent-only reporting context')
            amount = int(value) if value == value.to_integral_value() else float(value)
            if not isinstance(amount, int) and not math.isfinite(amount):
                raise ValueError('non-finite parent monetary balance')
            if concept in values and values[concept]['value'] != amount:
                raise ValueError('conflicting parent monetary observations')
            values.setdefault(concept, {'taxonomy': TAXONOMY, 'concept': concept, 'value': amount,
                'unit': 'USD', 'end': expected_report_date, 'entity': issuer, 'scope': 'parent_only',
                'proof': {'source_document_id': source['id'], 'packet_sha256': supplied,
                          'reported_scope': role, 'context_id': node['contextref'], 'fact_id': node.get('id')}})
        if set(values) != set(CONCEPTS):
            raise ValueError('parent equity, cash and explicit preferred-capital evidence required')
        text = _encoded({'issuer': issuer, 'facts': [values[key] for key in CONCEPTS]})
        doc = {'id': 'sec-parent-' + source['id'], 'url': source['url'], 'published_at': source['published_at'],
            'document_sha256': source['id'], 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'metadata': {'normalizer': NORMALIZER, 'source_document_id': source['id'], 'scope': 'parent_only',
                         'entity': issuer, 'report_date': expected_report_date},
            'origin': NORMALIZER}
        return {'status': 'ready', 'documents': [doc], 'issues': []}
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}


def collect_parent_inline(documents, archive_root):
    """Read already acquired filing bytes only; no network or AI side effects."""
    from pathlib import Path
    root = Path(archive_root).resolve()
    result = {'status': 'unavailable', 'documents': [], 'packets': {}, 'issues': []}
    for document in documents:
        try:
            path = Path(document['archive_path']).resolve()
            if not path.is_relative_to(root):
                raise ValueError('parent inline source outside verified archive')
            packet = extract_parent_packet(document, path.read_bytes())
            source = {**document, 'inline_parent_fields': packet}
            normalized = normalize_parent_inline(source, expected_report_date=document['metadata']['report_date'])
            result['issues'].extend(normalized['issues'])
            if normalized['status'] == 'ready':
                result['packets'][document['id']] = packet
                result['documents'].extend(normalized['documents'])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            result['issues'].append({'source': document.get('id'), 'reason': str(exc)})
    if result['documents']:
        result['status'] = 'partial' if result['issues'] else 'ready'
    return result
