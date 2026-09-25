"""Reconcile a bounded classified PDF balance; no economic or listing inference."""
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import re

from .pdf_statement_evidence import normalize_pdf_statements, FORMAT
from .statement_table_evidence import _json

_SECTIONS = ('Noncurrent assets', 'Current assets', 'Equity',
             'Noncurrent liabilities', 'Current liabilities')


def _compact(text):
    return ''.join((text or '').split()).lower()


def pdf_non_nwc(label):
    """Only explicit cash, financing, fixed and tax labels; unknowns stay judgments."""
    return _compact(label) in {_compact(s) for s in (
        'Cash and cash equivalents', 'Goodwill', 'Other intangible assets',
        'Property, plant and equipment', 'Other financial assets',
        'Investments accounted for using the equity method', 'Financial liabilities',
        'Deferred taxes', 'Claims for income tax refunds', 'Income tax liabilities')}


def _opening_rows(source, observations):
    facts = [f for f in observations if f['statement'] == 'balance']
    if not facts or len({f['proof']['page'] for f in facts}) != 1:
        raise ValueError('one complete PDF balance page required')
    ref = source['page_references'][facts[0]['proof']['page']-1]
    text = source['text'][ref['inizio']:ref['fine']]
    rows = {}
    for fact in facts:
        rows.setdefault(fact['proof']['row_literal']['start'], []).append(fact)
    cursor = facts[0]['proof']['header_literal']['end']; section = None; result = []
    for start, cells in sorted(rows.items()):
        current = [f for f in cells if f['end'] == source['metadata']['report_date']]
        if len(current) != 1:
            raise ValueError('every PDF balance row requires its opening amount; missing is not zero')
        f = current[0]; proof = f['proof']; literal = proof['row_literal']
        gap = text[cursor:start]
        if f['section'] != section:
            if f['section'] not in _SECTIONS or _compact(gap) != _compact(f['section']):
                raise ValueError('PDF balance section differs from original text or contains omitted rows')
            section = f['section']
        elif gap.strip():
            raise ValueError('PDF balance coverage omits original rows')
        cursor = literal['end']
        printed = proof['cell_text']
        exact = Decimal(printed.strip('()').replace(',', '')) * (-1 if printed.startswith('(') else 1)
        result.append({'reported_tag':f"reported-statement:{proof['page']}:{start}",
            'namespace':'reported-statement', 'value_exact':str(exact), 'unit':f['unit'],
            'end':f['end'], 'label':f['label'], 'proof':proof, 'reported_section':section})
    if text[cursor:].strip():
        raise ValueError('uncovered text after PDF closing balance; separate review required')
    if len({f['unit'] for f in result}) != 1:
        raise ValueError('PDF balance currencies/scales differ')
    return result


def _reconcile(rows):
    groups = []; leaves = []; totals = {}; cursor = 0
    value = lambda f: Decimal(f['value_exact'])
    def checked(parent, children, role):
        if not children or sum((value(f) for f in children), Decimal(0)) != value(parent):
            raise ValueError('PDF reported components do not reconcile exactly: '+role)
        groups.append({'parent':parent, 'components':children, 'structural_role':role})
    for section in _SECTIONS:
        block = []
        while cursor < len(rows) and rows[cursor]['reported_section'] == section:
            block.append(rows[cursor]); cursor += 1
        closing = None
        if section in ('Current assets', 'Current liabilities'):
            if not block: raise ValueError('missing PDF classified balance section')
            closing = block.pop()
            expected = 'Total assets' if section == 'Current assets' else 'Total equity and liabilities'
            if _compact(closing['label']) != _compact(expected):
                raise ValueError('explicit PDF closing total missing: '+expected)
        if len(block) < 2:
            raise ValueError('complete classified PDF section/subtotal required: '+section)
        parent = block.pop()
        if parent['label'] is not None and _compact(parent['label']) != _compact('Total '+section):
            raise ValueError('PDF section subtotal must be explicit or the final unlabeled row')
        if any(f['label'] is None for f in block):
            raise ValueError('unexplained nested PDF subtotal')
        if section == 'Equity':
            nested = [i for i,f in enumerate(block) if re.fullmatch(
                r'equityattributableto.+stockholders', _compact(f['label']))]
            if nested:
                if len(nested) != 1 or nested[0] == 0:
                    raise ValueError('unsupported nested PDF equity subtotal')
                i = nested[0]
                checked(block[i], block[:i], 'Equity attributable to stockholders')
                block = block[i:]
        checked(parent, block, section); totals[section] = parent
        if section != 'Equity':
            for f in block:
                leaves.append({**f, 'accounting_side':'asset' if section.endswith('assets') else 'liability',
                    'reported_ancestors':[parent['reported_tag']],
                    'economic_classification':'unreviewed', 'model_treatment':None})
        if closing:
            if section == 'Current assets':
                checked(closing, [totals[s] for s in _SECTIONS[:2]], 'Total assets')
                totals['assets'] = closing
            else:
                checked(closing, [totals[s] for s in _SECTIONS[2:]], 'Total equity and liabilities')
                if value(closing) != value(totals['assets']):
                    raise ValueError('PDF assets differ from equity and liabilities')
    if cursor != len(rows) or len({f['reported_tag'] for f in leaves}) != len(leaves):
        raise ValueError('uncovered or duplicate PDF balance row')
    return groups, leaves


def normalize_pdf_balance(source):
    from .balance_sheet_evidence import NORMALIZER, PREFIX
    result = normalize_pdf_statements(source)
    if result['status'] != 'ready':
        raise ValueError('PDF statements cannot be recompiled: '+repr(result['issues']))
    observations = json.loads(result['documents'][0]['text'])['facts']
    with localcontext() as ctx:
        ctx.prec = 256
        groups, leaves = _reconcile(_opening_rows(source, observations))
    meta = source['metadata']; entity = meta['emittente_id']
    text = _json({'issuer':entity, 'report_date':meta['report_date'], 'groups':groups,
        'components':leaves, 'nonmonetary_disclosures':[], 'reported_balance_reconciled':True,
        'economic_classification_approved':False})
    document = {'id':PREFIX+source['id'], 'document_sha256':source['id'], 'url':source['url'],
        'published_at':source['published_at'], 'text':text, 'sha256':sha256(text.encode()).hexdigest(),
        'origin':NORMALIZER, 'metadata':{'normalizer':NORMALIZER, 'source_document_id':source['id'],
            'entity':entity, 'scope':'consolidated', 'report_date':meta['report_date'], 'source_format':FORMAT,
            'identity_basis':'curated_filing_profile', 'security_identity_verified':False,
            'limitation':'Reported balance-page coverage only, exact reconciliation without rounding adjustments. '
                'Notes, mixed balances and unquantified commitments need separate economic review. '
                'No legal-name alias, listing identity, NWC classification or valuation approval inferred.'}}
    return {'status':'ready', 'documents':[document], 'issues':[]}
