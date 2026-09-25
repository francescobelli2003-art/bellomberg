"""Reported note subtotals and their components; no operating/financing inference."""
from copy import deepcopy
from decimal import Decimal, localcontext
from hashlib import sha256
import re

from .statement_table_evidence import _identity, _json

NORMALIZER = 'balance_details_v1'
PREFIX = 'balance-details-'
ROOTS = frozenset('us-gaap:' + name for name in (
    'OtherAssetsCurrent', 'OtherAssetsNoncurrent', 'AccruedLiabilitiesCurrent',
    'OtherLiabilitiesCurrent', 'OtherLiabilitiesNoncurrent'))


def extract_balance_detail_packet(source, raw):
    """Keep original inline contexts and table order from verified source bytes."""
    from bs4 import BeautifulSoup
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    _identity(source)
    if sha256(raw).hexdigest() != source['document_sha256']:
        raise ValueError('balance detail bytes differ from source')
    if estrai_testo('report.htm', contenuto=raw).get('testo') != source['text']:
        raise ValueError('balance detail text differs from source bytes')
    soup = BeautifulSoup(raw, 'html.parser')
    namespaces = {k[6:]: v for k, v in (soup.html.attrs if soup.html else {}).items()
                  if k.startswith('xmlns:')}
    for node in soup.find_all(True):
        if any(k.startswith('xmlns:') and namespaces.get(k[6:]) != v for k, v in node.attrs.items()):
            raise ValueError('local namespace replacement in balance detail')
    def indexed(tag):
        nodes = soup.find_all(tag); index = {n.get('id'): str(n) for n in nodes}
        if None in index or len(index) != len(nodes):
            raise ValueError('duplicate or missing inline context/unit identity')
        return index
    contexts, units = indexed('xbrli:context'), indexed('xbrli:unit')
    names = {n.get_text(' ', strip=True) for n in soup.find_all('ix:nonnumeric',
             attrs={'name': 'dei:EntityRegistrantName'})}
    if len(names) != 1 or not next(iter(names)):
        raise ValueError('unique inline issuer name required')
    tables, refs, unitrefs = [], set(), set()
    for index, table in enumerate(soup.find_all('table')):
        facts = table.find_all('ix:nonfraction')
        if table.find_parent('table') or not facts or facts[-1].get('name') not in ROOTS:
            continue
        tables.append({'index': index, 'html': str(table)})
        refs.update(n.get('contextref') for n in facts)
        unitrefs.update(n.get('unitref') for n in facts)
    if len(tables) > 50 or any(len(t['html']) > 256_000 for t in tables):
        raise ValueError('balance detail layout exceeds bounded extraction')
    if not refs <= contexts.keys() or not unitrefs <= units.keys():
        raise ValueError('inline context or unit reference missing')
    packet = {'format': NORMALIZER, 'source_document_sha256': source['id'],
        'source_text_sha256': source['sha256'], 'issuer': next(iter(names)),
        'namespaces': namespaces, 'contexts': {k: contexts[k] for k in sorted(refs)},
        'units': {k: units[k] for k in sorted(unitrefs)}, 'tables': tables}
    packet['sha256'] = sha256(_json(packet).encode()).hexdigest()
    return packet


def _contexts(packet, cik, on):
    from bs4 import BeautifulSoup
    result = set()
    for key, xml in packet['contexts'].items():
        node = BeautifulSoup(xml, 'html.parser').find('xbrli:context')
        if node is None or node.get('id') != key:
            raise ValueError('context identity changed')
        instant = node.find('xbrli:instant')
        if instant is None or instant.get_text(strip=True) != on:
            continue
        entity, period = node.find('xbrli:entity'), node.find('xbrli:period')
        identifiers = node.find_all('xbrli:identifier')
        if (len(identifiers) != 1 or len(node.find_all('xbrli:entity')) != 1
                or len(node.find_all('xbrli:period')) != 1 or len(node.find_all('xbrli:instant')) != 1
                or node.find(['xbrli:startdate', 'xbrli:enddate', 'xbrli:forever'])
                or identifiers[0].parent != entity or entity.parent != node
                or instant.parent != period or period.parent != node
                or identifiers[0].get('scheme') != 'http://www.sec.gov/CIK'
                or identifiers[0].get_text(strip=True).zfill(10) != cik):
            raise ValueError('balance context issuer or instant is ambiguous')
        if node.find(['xbrli:segment', 'xbrli:scenario', 'xbrldi:explicitmember', 'xbrldi:typedmember']):
            continue
        result.add(key)
    return result


def _observation(node, packet, on):
    from bs4 import BeautifulSoup
    ns = packet['namespaces']; tag = node.get('name', '')
    if not re.fullmatch(r'[A-Za-z_][\w.-]*:[A-Za-z_][\w.-]*', tag):
        raise ValueError('explicit qualified concept required')
    prefix, _ = tag.split(':')
    if not re.fullmatch(r'https?://[^\s]+', ns.get(prefix, '')):
        raise ValueError('concept namespace missing')
    if node.get('xsi:nil') not in (None, 'false', '0') or node.get('continuedat'):
        raise ValueError('missing or continued monetary observation')
    unit = BeautifulSoup(packet['units'][node['unitref']], 'html.parser').find('xbrli:unit')
    measures = unit.find_all('xbrli:measure')
    currency = measures[0].get_text(strip=True) if len(measures) == 1 else ''
    if (unit.get('id') != node['unitref'] or unit.find('xbrli:divide')
            or not re.fullmatch(r'iso4217:[A-Z]{3}', currency) or currency[-3:] in ('XXX', 'XTS')):
        raise ValueError('simple explicit monetary unit required')
    text = node.get_text('', strip=True); fmt = node.get('format'); local = None
    if fmt:
        fmt_prefix, local = fmt.split(':')
        if not re.fullmatch(r'http://www.xbrl.org/inlineXBRL/transformation/\d{4}-\d{2}-\d{2}', ns.get(fmt_prefix, '')):
            raise ValueError('numeric transformation namespace not recognized')
    zero = local == 'fixed-zero' and bool(text)
    if zero:
        amount = Decimal(0)
    elif (local in (None, 'num-dot-decimal') and len(text) <= 128
          and re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?', text)):
        amount = Decimal(text.replace(',', ''))
    else:
        raise ValueError('monetary text or transformation not supported')
    scale = node.get('scale', '0')
    if not re.fullmatch(r'-?\d{1,2}', scale) or not -12 <= int(scale) <= 12 or node.get('sign') not in (None, '-'):
        raise ValueError('numeric scale/sign not supported')
    amount *= Decimal(10) ** int(scale)
    if node.get('sign') == '-': amount = -amount
    return {'reported_tag': tag, 'namespace': ns[prefix], 'value_exact': format(amount.normalize(), 'f'),
            'unit': currency[-3:], 'end': on, 'explicit_zero': zero,
            'reported_decimals': node.get('decimals'), 'context_id': node['contextref'], 'fact_id': node.get('id')}


def _same_sec_inline_issuer(inline_name, catalog_name):
    """Allow Inc suffix typography only inside the verified SEC balance reader.

    The legal-name stem stays exact apart from ASCII case. CIK, source hashes
    and fact contexts remain independently checked; this is not a general alias.
    """
    from .input_evidence import same_entity_name
    if same_entity_name(inline_name, catalog_name):
        return True
    names = [re.fullmatch(r'(.+[^\s,]),? [Ii][Nn][Cc]\.?', name)
             if isinstance(name, str) else None for name in (inline_name, catalog_name)]
    return all(names) and same_entity_name(names[0][1], names[1][1])


def _validated_packet(source, field, normalizer):
    """Verify common source, issuer, namespace and layout-envelope bindings."""
    meta, cik, _ = _identity(source)
    if meta['form'] not in ('10-K', '10-Q'):
        raise ValueError('balance detail reader requires a supported US filing')
    packet = deepcopy(source[field]); digest = packet.pop('sha256')
    if (packet.get('format') != normalizer or sha256(_json(packet).encode()).hexdigest() != digest
            or packet['source_document_sha256'] != source['id'] or packet['source_text_sha256'] != source['sha256']
            or not _same_sec_inline_issuer(packet['issuer'], meta['issuer'])):
        raise ValueError('balance detail packet, source or issuer changed')
    ns = packet['namespaces']
    for prefix, uri in [('xbrli', 'http://www.xbrl.org/2003/instance'),
                        ('ix', 'http://www.xbrl.org/2013/inlineXBRL'),
                        ('iso4217', 'http://www.xbrl.org/2003/iso4217')]:
        if ns.get(prefix) != uri:
            raise ValueError('inline or currency namespace differs')
    if (not re.fullmatch(r'https?://fasb.org/us-gaap/20\d{2}', ns.get('us-gaap', ''))
            or not re.fullmatch(r'https?://xbrl.sec.gov/dei/20\d{2}', ns.get('dei', ''))):
        raise ValueError('accounting or issuer namespace differs')
    return meta, cik, packet, digest


def normalize_balance_details(source):
    """Reconcile every reported row; never manufacture a missing component or zero."""
    from bs4 import BeautifulSoup
    try:
        meta, cik, packet, digest = _validated_packet(source, 'balance_detail_fields', NORMALIZER)
        contexts = _contexts(packet, cik, meta['report_date'])
        groups, roots = [], set()
        with localcontext() as ctx:
            ctx.prec = 256
            for item in packet['tables']:
                table = BeautifulSoup(item['html'], 'html.parser').find('table')
                compact = lambda s: re.sub(r'\s+', '', s)
                if compact(table.get_text(' ', strip=True)) not in compact(source['text']):
                    raise ValueError('balance table differs from primary text')
                rows = [r for r in table.find_all('tr') if r.find_parent('table') is table]
                tagged = [i for i, row in enumerate(rows) if row.find('ix:nonfraction')]
                if not tagged or len(rows) > 500:
                    raise ValueError('balance detail rows missing or excessive')
                observations = []
                for index in range(tagged[0], tagged[-1]+1):
                    row = rows[index]
                    if not row.get_text(strip=True):
                        continue
                    selected = [n for n in row.find_all('ix:nonfraction') if n.get('contextref') in contexts]
                    if len(selected) != 1:
                        raise ValueError('one consolidated opening fact required for every component row')
                    observation = _observation(selected[0], packet, meta['report_date'])
                    observation['proof'] = {'source_document_id': source['id'], 'packet_sha256': digest,
                                            'table_index': item['index'], 'row_index': index}
                    cells = row.find_all(['td', 'th'], recursive=False)
                    observation['label'] = cells[0].get_text(' ', strip=True) if cells else ''
                    observations.append(observation)
                if len(observations) < 2:
                    raise ValueError('a subtotal without components is not a decomposition')
                parent, children = observations[-1], observations[:-1]
                if parent['reported_tag'] not in ROOTS or parent['reported_tag'] in roots:
                    raise ValueError('unsupported or duplicate subtotal decomposition')
                roots.add(parent['reported_tag'])
                tags = [c['reported_tag'] for c in children]
                if len(tags) != len(set(tags)) or parent['reported_tag'] in tags:
                    raise ValueError('duplicate component or subtotal counted as a component')
                if any(c['unit'] != parent['unit'] for c in children):
                    raise ValueError('component currencies differ')
                if sum((Decimal(c['value_exact']) for c in children), Decimal(0)) != Decimal(parent['value_exact']):
                    raise ValueError('reported components do not reconcile to their subtotal')
                groups.append({'parent': parent, 'components': children})
        if not groups:
            return {'status': 'not_applicable', 'documents': [], 'issues': [],
                    'reason': 'No supported tagged subtotal table; source coverage not established.'}
        text = _json({'issuer': meta['issuer'], 'groups': groups, 'economic_classification_approved': False})
        doc = {'id': PREFIX+source['id'], 'document_sha256': source['id'], 'url': source['url'],
            'published_at': source['published_at'], 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'origin': NORMALIZER, 'metadata': {'normalizer': NORMALIZER, 'source_document_id': source['id'],
                'entity': meta['issuer'], 'scope': 'consolidated', 'report_date': meta['report_date'],
                'limitation': 'Reported subtotal decompositions only; not complete working capital, economic classification, forecasts or method records.'}}
        return {'status': 'ready', 'documents': [doc], 'issues': []}
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, ArithmeticError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}


def collect_balance_details(source, archive_root):
    """Acquire only the selected opening filing, retaining recompile evidence."""
    from pathlib import Path
    try:
        path = Path(source['archive_path']).resolve()
        if not path.is_relative_to(Path(archive_root).resolve()):
            raise ValueError('balance detail source outside verified archive')
        packet = extract_balance_detail_packet(source, path.read_bytes())
        result = normalize_balance_details({**source, 'balance_detail_fields': packet})
        result['packets'] = {source['id']: packet} if result['status'] == 'ready' else {}
        return result
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {'status': 'incomplete', 'documents': [], 'packets': {},
                'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}
