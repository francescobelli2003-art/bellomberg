"""Class-specific observations from a bounded, comparative PDF balance sheet.

No target, missing liability, zero balance or complete valuation input is inferred.
Primary text, page hashes, headers and printed rounding remain reproducible.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import re

NORMALIZER = 'fund_nav_statement_v1'
PREFIX = 'fund-nav-statement-'
TAXONOMY = 'fund-nav-statement'
_NUMBER = r'(?:\(?-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?\)?)'
_ROW = re.compile(r'^(.*?)\s+\$?\s*('+_NUMBER+r')\s+\$?\s*('+_NUMBER+r')\s*$')
_TOTALS = {'Total Assets': 'TotalAssets', 'Total Liabilities': 'TotalLiabilities',
           'Total Equity': 'TotalEquity', 'Cash and cash equivalents': 'CashAndCashEquivalents',
           'Total Liabilities and Equity': 'TotalLiabilitiesAndEquity'}
_REQUIRED_TOTALS = set(_TOTALS.values()) - {'TotalLiabilitiesAndEquity'}
_CURRENCIES = {'United States Dollars': 'USD', 'Euros': 'EUR', 'Pounds Sterling': 'GBP'}
CONCEPTS = {'shares': 'ClassSharesOutstanding', 'reported_nav_per_share': 'ClassNavPerShare',
            'reported_nav_precision': 'ClassNavPerSharePrecision'}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _number(token):
    if len(token) > 32 or token.startswith('(') != token.endswith(')') or token.startswith('(-'):
        raise ValueError('unsupported monetary spelling')
    value = Decimal(token.strip('()').replace(',', ''))
    if token.startswith('('):
        value = -value
    return value


def _class_key(label):
    return re.sub(r'\bShares\b', 'Share', label, flags=re.I).casefold()


def normalize_fund_statement(source):
    """Recompile observations from one original page; never from supplied facts."""
    try:
        from .document_evidence import verify_page_references, source_dates
        from bellomberg.market_data.filing_verifica import _data
        text, meta = source['text'], source['metadata']
        issuer, on = meta['issuer'], date.fromisoformat(meta['report_date'])
        if (not isinstance(issuer, str) or not issuer.strip()
                or source['sha256'] != sha256(text.encode()).hexdigest()
                or not re.fullmatch('[0-9a-f]{64}', source['id'])
                or source['document_sha256'] != source['id']):
            raise ValueError('original PDF identity/text hash invalid')
        dates = source_dates(source, date.max)
        if on > date.fromisoformat(dates['available_at']):
            raise ValueError('report period after observed availability')
        verify_page_references(text, source['page_references'])
        pages = [p for p in source['page_references'] if 'STATEMENT OF FINANCIAL POSITION' in
                 text[p['inizio']:p['fine']] and 'Net assets attributable to ' in text[p['inizio']:p['fine']]]
        if len(pages) != 1:
            raise ValueError('one complete class-attributed financial-position page required')
        page = pages[0]
        body = text[page['inizio']:page['fine']]
        if not re.search(r'^'+re.escape(issuer)+r'\s+[0-9]+\s*$', body, re.M):
            raise ValueError('full legal issuer absent from statement page header/footer')
        headers = re.findall(r'^As of (.+?) and (.+?)\s*$', body, re.M)
        units = re.findall(r'^\(Stated in (.+?)\)\s*$', body, re.M)
        if len(headers) != 1 or len(units) != 1 or units[0] not in _CURRENCIES:
            raise ValueError('two complete dates and supported explicit monetary unit required')
        periods = [_data(v) for v in headers[0]]
        if periods[0] != on or periods[1] >= periods[0] or not re.search(
                rf'^{periods[0].year}\s+{periods[1].year}\s*$', body, re.M):
            raise ValueError('ordered comparative date/year columns disagree')
        currency = _CURRENCIES[units[0]]
        totals, classes, facts = {}, {}, []
        position = page['inizio']
        for raw_line in body.splitlines(keepends=True):
            line = raw_line.strip()
            relevant = (any(line.startswith(label+' ') for label in _TOTALS)
                        or line.startswith(('Net assets attributable to ', 'Net assets per '))
                        or re.search(r'\bShares? outstanding\b', line, re.I))
            if not relevant:
                position += len(raw_line)
                continue
            match = _ROW.fullmatch(line)
            if not match:
                raise ValueError('missing/ambiguous numeric cells: '+line)
            if '$' in line and currency != 'USD':
                raise ValueError('dollar cells conflict with the statement currency header')
            label, first, second = match.groups()
            values = [_number(first), _number(second)]
            if label in _TOTALS:
                role, share_class, concept = 'total', None, _TOTALS[label]
                target, key = totals, concept
            elif label.startswith('Net assets attributable to '):
                role, share_class, concept = 'assets', label.removeprefix('Net assets attributable to '), 'ClassNetAssets'
                target = classes.setdefault(_class_key(share_class), {})
                key = role
                target['share_class'] = share_class
            elif label.startswith('Net assets per '):
                role, share_class, concept = 'price', label.removeprefix('Net assets per '), 'ClassNavPerShare'
                target, key = classes.setdefault(_class_key(share_class), {}), role
            elif label.endswith(' outstanding'):
                role, share_class, concept = 'shares', label.removesuffix(' outstanding'), 'ClassSharesOutstanding'
                target, key = classes.setdefault(_class_key(share_class), {}), role
            else:
                raise ValueError('unsupported balance/class row: '+label)
            if key in target:
                raise ValueError('duplicate financial-position row: '+label)
            if any(v < 0 for v in values) or role == 'shares' and any(v <= 0 or v != v.to_integral_value() for v in values):
                raise ValueError('negative balance or nonpositive/fractional issued shares')
            target[key] = values
            start = position + raw_line.index(line)
            locator = {'page': page['pagina'], 'start': start, 'end': start+len(line), 'text': line,
                       'header': 'As of '+' and '.join(headers[0]), 'units_header': '(Stated in '+units[0]+')'}
            for column, (token, value, period) in enumerate(zip((first, second), values, periods)):
                precision = max(0, -value.as_tuple().exponent)
                unit = 'shares' if role == 'shares' else currency+' per share' if role == 'price' else currency
                fact = {'taxonomy': TAXONOMY, 'concept': concept, 'entity': issuer,
                    'share_class': share_class, 'value': int(value) if value == value.to_integral_value() else float(value),
                    'unit': unit, 'end': period.isoformat(), 'printed_value': token, 'printed_precision': precision,
                    'source_locator': {**locator, 'column': column}}
                facts.append(fact)
                if role == 'price':
                    if precision > 8:
                        raise ValueError('unsupported published NAV precision')
                    facts.append({**deepcopy(fact), 'concept': 'ClassNavPerSharePrecision',
                                  'value': precision, 'unit': 'decimals'})
            position += len(raw_line)
        if not _REQUIRED_TOTALS <= set(totals) or not classes or any(
                set(c) != {'assets','shares','price','share_class'} for c in classes.values()):
            raise ValueError('complete totals and matched class NAV/share/count rows required')
        for fact in facts:
            if fact['share_class'] is not None:
                fact['share_class'] = classes[_class_key(fact['share_class'])]['share_class']
        for column in (0, 1):
            if ('TotalLiabilitiesAndEquity' in totals and
                    totals['TotalLiabilitiesAndEquity'][column] != totals['TotalAssets'][column]):
                raise ValueError('reported liabilities-and-equity total differs from assets')
            if totals['TotalAssets'][column] - totals['TotalLiabilities'][column] != totals['TotalEquity'][column]:
                raise ValueError('reported assets, liabilities and equity do not reconcile')
            if sum(c['assets'][column] for c in classes.values()) != totals['TotalEquity'][column]:
                raise ValueError('class NAV allocation does not reconcile to reported total equity')
            for c in classes.values():
                balance, shares, price = (c[k][column] for k in ('assets','shares','price'))
                # Balance amounts can be printed to whole currency units while
                # the per-share NAV has decimals. Check overlapping rounding
                # intervals, retaining both original observations unchanged.
                tolerance = (Decimal(1).scaleb(balance.as_tuple().exponent)
                             + shares*Decimal(1).scaleb(price.as_tuple().exponent))/2
                if abs(balance-shares*price) > tolerance:
                    raise ValueError('class amount/share-count/NAV inconsistent at printed precision')
        payload = {'issuer': issuer, 'facts': facts,
            'source_document_id': source['id'], 'checks': {'class_attribution_reconciled': True,
                'balance_equation_reconciled': True, 'per_share_compatible_with_printed_precision': True},
            'limitation': 'Observed class balances, counts and printed NAV only. No target, zero liability, complete component bridge, publication authenticity or current market quote is inferred.'}
        encoded = _json(payload)
        doc = {'id': PREFIX+source['id'], 'document_sha256': source['id'], 'url': source['url'], **dates,
            'text': encoded, 'sha256': sha256(encoded.encode()).hexdigest(),
            'metadata': {'normalizer': NORMALIZER, 'source_document_id': source['id'],
                         'entity': issuer, 'report_date': on.isoformat()}, 'origin': NORMALIZER}
        return {'status': 'ready', 'documents': [doc], 'issues': []}
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}


def statement_documents(documents):
    """Add reproducible observations to supplied PDFs, preserving every source.

    This is not a source-discovery or economic-completeness certification.
    Unsupported tables remain visible to the caller and the proposer.
    """
    augmented, issues, attempted, accepted = deepcopy(documents), [], [], []
    ids = {doc.get('id') for doc in documents if isinstance(doc, dict) and isinstance(doc.get('id'), str)}
    for doc in documents:
        metadata = doc.get('metadata') if isinstance(doc, dict) else None
        if (not isinstance(doc, dict) or isinstance(metadata, dict) and metadata.get('normalizer')
                or 'STATEMENT OF FINANCIAL POSITION' not in str(doc.get('text', ''))):
            continue
        attempted.append(doc.get('id'))
        result = normalize_fund_statement(doc)
        issues.extend({**issue, 'document_id': doc.get('id')} for issue in result['issues'])
        for observation in result['documents']:
            accepted.append(observation['id'])
            if observation['id'] not in ids:
                augmented.append(observation)
                ids.add(observation['id'])
    status = ('partial' if accepted else 'incomplete') if issues else 'ready' if accepted else 'not_requested'
    return augmented, {'status': status, 'attempted': attempted, 'observations': accepted, 'issues': issues,
        'limitation': 'Extraction only; source discovery, publication, full component bridge, market quote and targets remain separately required.'}


def structured_nav_proof(driver, item, evidence, unit, period, perimeter, model=None):
    """Only canonical normalized observations of the selected share class."""
    try:
        from .input_evidence import _pointer, structured_fact_proof
        from .input_preparation import _source_scale
        if driver not in CONCEPTS or len(evidence) != 1 or any(k in item for k in
                ('evidence_quote','period_quote','facts','calculation')):
            raise ValueError('NAV structured proof requires one supported scalar observation, without mixed proofs')
        doc = evidence[0]
        if (doc.get('metadata') or {}).get('normalizer') != NORMALIZER or not doc['id'].startswith(PREFIX):
            raise ValueError('verified NAV statement normalization required')
        pointer = item.get('evidence_pointer', {}).get('value', '')
        match = re.fullmatch(r'(/facts/(?:0|[1-9][0-9]*))/value', pointer)
        if not match:
            raise ValueError('exact normalized NAV value pointer required')
        raw = json.loads(doc['text'])
        fact = _pointer(raw, match[1])
        if fact.get('share_class') != perimeter['share_class']:
            raise ValueError('NAV observation belongs to a different share class')
        if driver == 'reported_nav_precision':
            if type(item.get('value')) is not int:
                raise ValueError('published precision must be an integer')
            nav = (model or {}).get('reported_nav_per_share')
            if not isinstance(nav, dict) or nav.get('evidence_ids') != item.get('evidence_ids'):
                raise ValueError('printed precision must cite the reported NAV source')
            error = structured_nav_proof('reported_nav_per_share', nav, evidence,
                                         perimeter['currency']+' per share', period, perimeter)
            if error:
                raise ValueError('printed precision: '+error)
            nav_fact = _pointer(raw, nav['evidence_pointer']['value'].removesuffix('/value'))
            if nav_fact['source_locator'] != fact['source_locator']:
                raise ValueError('printed precision belongs to another NAV observation')
        return structured_fact_proof(item, evidence, unit, period, scale=_source_scale,
            expected_entity=perimeter['entity'], allowed_concepts={(TAXONOMY, CONCEPTS[driver])})
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        return str(exc)
