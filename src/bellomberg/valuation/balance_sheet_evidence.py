"""Reconcile supported classified balances and note leaves, without economic judgments."""
from copy import deepcopy
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import re

from .balance_detail_evidence import (
    _contexts, _observation, _validated_packet, extract_balance_detail_packet,
    normalize_balance_details,
)
from .statement_table_evidence import _json

NORMALIZER = 'balance_sheet_v1'
PREFIX = 'balance-sheet-'
_TOTAL = 'us-gaap:LiabilitiesAndStockholdersEquity'


def extract_balance_sheet_packet(source, raw):
    """Select the full table from verified raw bytes; retain contexts and note binding."""
    from bs4 import BeautifulSoup
    details = extract_balance_detail_packet(source, raw)
    soup = BeautifulSoup(raw, 'html.parser')
    packet = {k: deepcopy(v) for k, v in details.items()
              if k not in ('format', 'sha256', 'contexts', 'units', 'tables')}
    tables, refs, units = [], set(), set()
    for index, table in enumerate(soup.find_all('table')):
        facts = table.find_all('ix:nonfraction')
        names = {n.get('name') for n in facts}
        # A partial candidate is an unsupported balance, not an absent disclosure.
        if table.find_parent('table') or not ({'us-gaap:Assets', _TOTAL} & names):
            continue
        tables.append({'index': index, 'html': str(table)})
        refs.update(n.get('contextref') for n in facts)
        units.update(n.get('unitref') for n in facts)
    if len(tables) > 20 or any(len(t['html']) > 512_000 for t in tables):
        raise ValueError('balance sheet layout exceeds bounded extraction')
    all_contexts = {n['id']: str(n) for n in soup.find_all('xbrli:context')}
    all_units = {n['id']: str(n) for n in soup.find_all('xbrli:unit')}
    if not refs <= all_contexts.keys() or not units <= all_units.keys():
        raise ValueError('balance sheet inline context or unit missing')
    packet.update(format=NORMALIZER, tables=tables, note_packet_sha256=details['sha256'],
        contexts={k: all_contexts[k] for k in sorted(refs)},
        units={k: all_units[k] for k in sorted(units)})
    packet['sha256'] = sha256(_json(packet).encode()).hexdigest()
    return packet


def _caption_facts(cells, packet):
    """Preserve descriptive equity figures in labels without adding them as USD.

    Only the named share-count/per-share concepts in the caption qualify. Their
    units must agree; monetary and unknown caption facts remain unsupported.
    """
    from bs4 import BeautifulSoup
    nodes = cells[0].find_all('ix:nonfraction') if cells else []
    shares = {'us-gaap:'+name for name in ('CommonStockSharesAuthorized',
        'CommonStockSharesIssued', 'CommonStockSharesOutstanding', 'TreasuryStockCommonShares')}
    for node in nodes:
        unit = BeautifulSoup(packet['units'][node['unitref']], 'html.parser').find('xbrli:unit')
        if unit is None or unit.get('id') != node['unitref']:
            raise ValueError('caption fact unit identity changed')
        measures = [m.get_text(strip=True) for m in unit.find_all('xbrli:measure')]
        if node.get('name') in shares:
            valid = not unit.find('xbrli:divide') and measures == ['xbrli:shares']
        elif node.get('name') == 'us-gaap:CommonStockParOrStatedValuePerShare':
            numerator, denominator = unit.find('xbrli:unitnumerator'), unit.find('xbrli:unitdenominator')
            valid = (unit.find('xbrli:divide') is not None and numerator is not None and denominator is not None
                and len(measures) == 2 and re.fullmatch(r'iso4217:[A-Z]{3}', measures[0])
                and measures[0][-3:] not in ('XXX', 'XTS') and measures[1] == 'xbrli:shares'
                and [m.get_text(strip=True) for m in numerator.find_all('xbrli:measure')] == measures[:1]
                and [m.get_text(strip=True) for m in denominator.find_all('xbrli:measure')] == measures[1:])
        else:
            valid = False
        if not valid:
            raise ValueError('unsupported concept or unit in balance caption')
    return {id(node) for node in nodes}


def _rows(source, packet, digest, contexts, on):
    from bs4 import BeautifulSoup
    if len(packet['tables']) != 1:
        raise ValueError('unique complete balance table required')
    item = packet['tables'][0]
    table = BeautifulSoup(item['html'], 'html.parser').find('table')
    compact = lambda s: re.sub(r'\s+', '', s)
    if table is None or compact(table.get_text(' ', strip=True)) not in compact(source['text']):
        raise ValueError('balance sheet table differs from primary text')
    rows = [r for r in table.find_all('tr') if r.find_parent('table') is table]
    tagged = [i for i, row in enumerate(rows) if row.find('ix:nonfraction')]
    if not tagged or len(rows) > 500:
        raise ValueError('balance sheet rows missing or excessive')
    observations, disclosures = [], []
    for index in range(tagged[0], tagged[-1]+1):
        row = rows[index]
        cells = row.find_all(['td', 'th'], recursive=False)
        captions = _caption_facts(cells, packet)
        nodes = [node for node in row.find_all('ix:nonfraction') if id(node) not in captions]
        # Section headings are allowed; a printed but untagged numeric cell is not.
        for cell in cells[1:]:
            copy = BeautifulSoup(str(cell), 'html.parser')
            for fact in copy.find_all('ix:nonfraction'):
                fact.decompose()
            if re.search(r'[0-9]|^\s*[-\u2013\u2014]\s*$', copy.get_text(' ', strip=True)):
                raise ValueError('untagged numeric balance cell')
        if not nodes:
            if re.search(r'[0-9]', row.get_text(' ', strip=True)):
                raise ValueError('untagged numeric balance row')
            continue
        selected = [n for n in nodes if n.get('contextref') in contexts]
        if len(selected) != 1:
            raise ValueError('one consolidated opening fact required for every balance row')
        node = selected[0]
        proof = {'source_document_id': source['id'], 'packet_sha256': digest,
                 'table_index': item['index'], 'row_index': index}
        label = cells[0].get_text(' ', strip=True) if cells else ''
        if node.get('name') == 'us-gaap:CommitmentsAndContingencies':
            if node.get('xsi:nil') not in ('true', '1') or node.get('continuedat'):
                raise ValueError('unsupported monetary contingency balance row')
            disclosures.append({'reported_tag': node['name'], 'label': label, 'value_exact': None,
                'end': on, 'proof': proof, 'status': 'not_quantified_requires_separate_review'})
        else:
            observation = _observation(node, packet, on)
            # Inline XBRL may report a positive debit/contra-account amount,
            # while its printed balance cell explicitly subtracts it. Retain
            # both representations; never flip an already-negative fact twice.
            cell_text = compact(node.find_parent(['td', 'th']).get_text(' ', strip=True))
            fact_text = compact(node.get_text(' ', strip=True))
            if cell_text == '(' + fact_text + ')' and Decimal(observation['value_exact']) > 0:
                observation['inline_value_exact'] = observation['value_exact']
                observation['value_exact'] = '-' + observation['value_exact']
                observation['presentation_sign'] = 'negative_parentheses'
            observations.append({**observation, 'proof': proof, 'label': label})
    return observations, disclosures


def _reconcile(facts, notes):
    tags = [f['reported_tag'] for f in facts]
    if len(tags) != len(set(tags)) or not facts:
        raise ValueError('duplicate or missing balance fact')
    if len({f['unit'] for f in facts}) != 1:
        raise ValueError('balance sheet currencies differ')
    by_tag = dict(zip(tags, facts))
    required = ['us-gaap:'+t for t in ('AssetsCurrent', 'Assets', 'LiabilitiesCurrent',
                                      'Liabilities', 'StockholdersEquity', 'LiabilitiesAndStockholdersEquity')]
    if not set(required) <= by_tag.keys():
        raise ValueError('classified balance totals missing; unsupported layout')
    positions = [tags.index(t) for t in required]
    if positions != sorted(positions) or positions[-1] != len(tags)-1:
        raise ValueError('balance totals order or closing coverage unsupported')
    def group(tag, start):
        parent = by_tag[tag]; children = facts[start:tags.index(tag)]
        if not children or sum((Decimal(f['value_exact']) for f in children), Decimal(0)) != Decimal(parent['value_exact']):
            raise ValueError('reported balance components do not reconcile: '+tag)
        return {'parent': parent, 'components': children}
    ca = group(required[0], 0)
    assets = group(required[1], positions[0])
    cl = group(required[2], positions[1]+1)
    liabilities = group(required[3], positions[2])
    equity = group(required[4], positions[3]+1)
    if positions[5] != positions[4]+1:
        raise ValueError('uncovered row between equity and closing balance')
    value = lambda tag: Decimal(by_tag[tag]['value_exact'])
    if value(required[1]) != value(_TOTAL) or value(_TOTAL) != value(required[3])+value(required[4]):
        raise ValueError('assets, liabilities and equity do not reconcile')
    decompositions = {}
    for g in [ca, cl, *notes]:
        tag = g['parent']['reported_tag']
        if tag in decompositions:
            raise ValueError('duplicate balance decomposition')
        decompositions[tag] = g
    leaves, used = [], set()
    def expand(fact, side, ancestors):
        tag = fact['reported_tag']
        if tag in ancestors:
            raise ValueError('cycle in balance decomposition')
        if tag in decompositions:
            if tag in used:
                raise ValueError('balance decomposition counted twice')
            used.add(tag); g = decompositions[tag]
            if any(fact[k] != g['parent'][k] for k in ('reported_tag', 'namespace', 'value_exact', 'unit', 'end')):
                raise ValueError('note parent differs from balance parent')
            for child in g['components']:
                expand(child, side, ancestors+[tag])
        else:
            leaves.append({**fact, 'accounting_side': side, 'reported_ancestors': ancestors,
                           'economic_classification': 'unreviewed', 'model_treatment': None})
    for side, g in [('asset', assets), ('liability', liabilities)]:
        for child in g['components']:
            expand(child, side, [g['parent']['reported_tag']])
        if sum(Decimal(f['value_exact']) for f in leaves if f['accounting_side'] == side) != Decimal(g['parent']['value_exact']):
            raise ValueError('balance leaves do not reconcile')
    if set(decompositions) != used or len({f['reported_tag'] for f in leaves}) != len(leaves):
        raise ValueError('unlinked note decomposition or duplicate balance leaf')
    return [ca, assets, cl, liabilities, equity], leaves


def normalize_balance_sheet(source):
    """Recompile reported coverage. This is neither an NWC definition nor a model."""
    try:
        if source.get('filing_verification') is not None:
            from .pdf_balance_evidence import normalize_pdf_balance
            return normalize_pdf_balance(source)
        meta, cik, packet, digest = _validated_packet(source, 'balance_sheet_fields', NORMALIZER)
        if not packet['tables']:
            return {'status': 'not_applicable', 'documents': [], 'issues': [],
                    'reason': 'No supported tagged balance table; reported balance coverage not established.'}
        if source['balance_detail_fields']['sha256'] != packet['note_packet_sha256']:
            raise ValueError('balance sheet note packet changed')
        details = normalize_balance_details(source)
        if details['status'] not in ('ready', 'not_applicable'):
            raise ValueError('balance sheet notes cannot be recompiled: '+repr(details['issues']))
        notes = json.loads(details['documents'][0]['text'])['groups'] if details['documents'] else []
        contexts = _contexts(packet, cik, meta['report_date'])
        with localcontext() as ctx:
            ctx.prec = 256
            facts, disclosures = _rows(source, packet, digest, contexts, meta['report_date'])
            groups, leaves = _reconcile(facts, notes)
        text = _json({'issuer': meta['issuer'], 'report_date': meta['report_date'],
            'groups': groups, 'components': leaves, 'nonmonetary_disclosures': disclosures,
            'reported_balance_reconciled': True, 'economic_classification_approved': False})
        document = {'id': PREFIX+source['id'], 'document_sha256': source['id'], 'url': source['url'],
            'published_at': source['published_at'], 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
            'origin': NORMALIZER, 'metadata': {'normalizer': NORMALIZER, 'source_document_id': source['id'],
                'entity': meta['issuer'], 'scope': 'consolidated', 'report_date': meta['report_date'],
                'limitation': 'Reported balance and available note leaves only; no economic classification, complete working capital, forecasts or method records.'}}
        return {'status': 'ready', 'documents': [document], 'issues': []}
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, ArithmeticError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}


def collect_balance_sheet(source, archive_root):
    from pathlib import Path
    try:
        path = Path(source['archive_path']).resolve()
        if not path.is_relative_to(Path(archive_root).resolve()):
            raise ValueError('balance sheet source outside verified archive')
        raw = path.read_bytes()
        if source.get('filing_verification') is not None:
            from .pdf_statement_evidence import extract_pdf_statement_packet
            packet = extract_pdf_statement_packet(source, raw)
            result = normalize_balance_sheet({**source, 'statement_table_fields':packet})
            result.update(packets={}, detail_packets={},
                statement_packets={source['id']:packet} if result['status'] == 'ready' else {})
            return result
        packet = extract_balance_sheet_packet(source, raw)
        details = extract_balance_detail_packet(source, raw)
        result = normalize_balance_sheet({**source, 'balance_sheet_fields': packet, 'balance_detail_fields': details})
        result['packets'] = {source['id']: packet} if result['status'] == 'ready' else {}
        result['detail_packets'] = {source['id']: details} if result['status'] == 'ready' else {}
        return result
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {'status': 'incomplete', 'documents': [], 'packets': {}, 'detail_packets': {},
                'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}
