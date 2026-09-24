"""Narrow comparative SEC statement extraction, preserving conflicting tags.

Only an unrounded, single common-stock row with two explicitly ordered dates is
supported. This adds source observations, never rewrites companyfacts or creates
valuation records. A disagreement needs an explicit selection and remains visible.
"""
from datetime import date
from hashlib import sha256
import json
import re

from .input_evidence import same_entity_name, structured_fact_proof
from .preparation_exhibits import _sec_parts

NORMALIZER = 'sec_statement_shares_v1'
PREFIX = 'statement-shares-'
_INTEGER = r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)'
_MONTHS = ('January February March April May June July August September October November December').split()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def normalize_statement_shares(source, tagged_documents):
    """Recompile from original catalog texts; source dates and hashes stay intact."""
    try:
        meta = source['metadata']; cik, accession, _ = _sec_parts(source['url'])
        cik = cik.zfill(10); on = date.fromisoformat(meta['report_date'])
        text = source['text']; issuer = meta['issuer']
        if (meta['emittente_id'] != 'CIK:' + cik or meta['accession'].replace('-', '') != accession
                or meta['form'] not in ('10-K', '10-Q', '10-K/A', '10-Q/A')
                or not isinstance(issuer, str) or not issuer.strip()
                or on.isoformat() != meta['report_date'] or on > date.fromisoformat(source['published_at'])
                or source['sha256'] != sha256(text.encode()).hexdigest()
                or not re.fullmatch(r'[0-9a-f]{64}', source['id'])
                or source['document_sha256'] != source['id']):
            raise ValueError('statement filing identity, period or text hash differs')
        # Whitespace only: do not strip labels, digits, signs or source qualifiers.
        body = ' '.join(text.split())
        sections = re.findall(r'Consolidated (?:Statements of Financial Condition|Balance Sheets) '
            r'\(In Thousands, Except Share and Per Share Data\) (.*?) '
            r'See notes to consolidated financial statements\.', body, re.I)
        eligible = []
        for section in sections:
            header = re.match(r'('+'|'.join(_MONTHS)+r') ([0-9]{1,2}), ([0-9]{4}) ([0-9]{4}) Assets\b', section, re.I)
            if not header:
                continue
            month = [m.lower() for m in _MONTHS].index(header[1].lower()) + 1
            days = [date(int(header[n]), month, int(header[2])).isoformat() for n in (3, 4)]
            if days[0] != on.isoformat() or days[1] >= days[0]:
                continue
            # Exact wording excludes issued-only, treasury, weighted averages,
            # class-specific counts and numbers scaled by the monetary header.
            rows = re.findall(r'(?:^|[; ])(Common stock, \$[0-9.]+ par value; '+_INTEGER+
                r' shares authorized; ('+_INTEGER+r') shares and ('+_INTEGER+
                r') shares issued and outstanding, respectively)\b', section, re.I)
            if len(rows) != 1 or len(re.findall(r'\bcommon stock\b', section, re.I)) != 1:
                continue
            row, first, second = rows[0]
            literal_row = re.escape(row).replace(r'\ ', r'\s+')
            if (re.search(r'\bclass\s+[a-z0-9]+\b', section, re.I)
                    or not re.search(r'^[ \t]*'+literal_row+r'(?=\s|$)', text, re.I | re.M)):
                continue
            eligible.append((days, row, [int(v.replace(',', '')) for v in (first, second)]))
        if len(eligible) != 1:
            raise ValueError('unambiguous unrounded common-share row and ordered comparative dates required')
        days, row, values = eligible[0]
        par_value = re.match(r'Common stock, (\$[0-9.]+) par value;', row, re.I)[1]
        if any(v <= 0 for v in values):
            raise ValueError('positive reported common shares required')
        tag_id = 'xbrl-' + cik + '-' + accession
        matches = [doc for doc in tagged_documents if doc['id'] == tag_id]
        if len(matches) != 1:
            raise ValueError('same-filing companyfacts comparison required')
        tagged = matches[0]; raw = json.loads(tagged['text']); tm = tagged['metadata']
        if (tagged['sha256'] != sha256(tagged['text'].encode()).hexdigest()
                or tagged['url'] != 'https://data.sec.gov/api/xbrl/companyfacts/CIK' + cik + '.json'
                or tagged['published_at'] != source['published_at']
                or tm['emittente_id'] != 'CIK:' + cik or tm['accession'] != accession
                or str(raw['cik']).zfill(10) != cik or not same_entity_name(raw['issuer'], issuer)):
            raise ValueError('tag comparison source identity differs from statement')
        observations = []
        for index, fact in enumerate(raw['facts']):
            if (fact.get('taxonomy'), fact.get('concept')) != ('us-gaap', 'CommonStockSharesOutstanding'):
                continue
            obs = fact['observation']
            if obs.get('end') != on.isoformat():
                continue
            if (fact['unit'] != 'shares' or type(obs['val']) not in (int, float)
                    or obs['val'] <= 0 or not float(obs['val']).is_integer()
                    or obs.get('accn', '').replace('-', '') != accession or obs.get('filed') != source['published_at']
                    or 'start' in obs):
                raise ValueError('ambiguous tagged opening common shares')
            observations.append({'source_id': tag_id, 'pointer': f'/facts/{index}/observation/val',
                                 'value': obs['val'], 'unit': 'shares', 'end': obs['end']})
        if not observations:
            raise ValueError('same-date common-share tag comparison unavailable')
        conflicts = [obs for obs in observations if obs['value'] != values[0]]
        payload = {'issuer': issuer, 'facts': [
            {'taxonomy': NORMALIZER, 'concept': 'CommonStockSharesOutstanding', 'entity': issuer,
             'share_class': 'Common Stock', 'value': value, 'unit': 'shares', 'end': day}
            for day, value in zip(days, values)],
            'source_proof': {'source_document_id': source['id'], 'statement_row': row,
                'ordered_dates': days, 'units_basis': 'In Thousands, Except Share and Per Share Data'},
            'tag_comparison': {'status': 'conflict' if conflicts else 'consistent',
                'observations': observations, 'conflicts': conflicts,
                'limitation': 'A source disagreement is not an issuer correction. Explicit source selection must remain disclosed.'}}
        encoded = _json(payload)
        doc = {'id': PREFIX + source['id'], 'document_sha256': source['document_sha256'],
            'url': source['url'], 'published_at': source['published_at'], 'text': encoded,
            'sha256': sha256(encoded.encode()).hexdigest(), 'metadata': {
                'normalizer': NORMALIZER, 'source_document_id': source['id'], 'comparison_document_id': tag_id,
                'entity': issuer, 'report_date': on.isoformat(), 'share_class': 'Common Stock',
                'share_title': 'Common Stock, ' + par_value + ' par value'}, 'origin': NORMALIZER}
        return {'status': 'ready', 'documents': [doc], 'issues': [], 'source_disagreement': bool(conflicts)}
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}


def is_statement_shares(document):
    metadata = document.get('metadata')
    return (str(document.get('id', '')).startswith(PREFIX)
            or isinstance(metadata, dict) and metadata.get('normalizer') == NORMALIZER)


def statement_share_proof(driver, item, evidence, unit, period, expected_entity, scale):
    """Explicitly select a primary statement; never silently repair a bad tag."""
    try:
        if driver not in ('shares', 'capital.shares_m') or len(evidence) != 1 or not is_statement_shares(evidence[0]):
            raise ValueError('statement shares source allowed only for a single common-share observation')
        if any(k in item for k in ('evidence_pointer', 'quoted_value', 'quoted_unit', 'evidence_quote', 'period_quote', 'facts')):
            raise ValueError('statement shares require explicit source selection, without mixed proof fields')
        calc = item.get('calculation'); raw = json.loads(evidence[0]['text'])
        if (not isinstance(calc, dict) or set(calc) != {'type', 'fact_index', 'selection_basis', 'acknowledged_conflicts'}
                or calc['type'] != 'statement_shares' or type(calc['fact_index']) is not int or calc['fact_index'] != 0):
            raise ValueError('explicit opening statement share selection required')
        comparison = raw['tag_comparison']
        basis = 'primary_statement_over_conflicting_tags' if comparison['conflicts'] else 'primary_statement_consistent_with_tags'
        if calc['selection_basis'] != basis or calc['acknowledged_conflicts'] != comparison['conflicts']:
            raise ValueError('all conflicting share observations must be acknowledged explicitly')
        fact = raw['facts'][0]
        proof = {'value': item.get('value'), 'evidence_ids': item.get('evidence_ids'),
            'evidence_pointer': {'value': '/facts/0/value', 'unit': '/facts/0/unit', 'period': '/facts/0/end'},
            'quoted_value': fact['value'], 'quoted_unit': fact['unit']}
        return structured_fact_proof(proof, evidence, unit, period, scale=scale,
            expected_entity=expected_entity, allowed_concepts={(NORMALIZER, 'CommonStockSharesOutstanding')})
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return str(exc)


def share_conflict_disclosure(evidence):
    parts = []
    for doc in evidence:
        if is_statement_shares(doc):
            raw = json.loads(doc['text']); fact = raw['facts'][0]
            if raw['tag_comparison']['conflicts']:
                parts.append('SOURCE DISAGREEMENT: selected primary statement '+str(fact['value'])+' '+fact['unit']+
                    ' at '+fact['end']+'; conflicting SEC tags '+_json(raw['tag_comparison']['conflicts'])+
                    '. This selection is not an issuer correction or PM approval. ')
    return ''.join(parts)


def share_selection_problem(item, catalog, entity, period, share_class):
    relevant = []
    for doc in catalog.values():
        if not is_statement_shares(doc):
            continue
        meta = doc['metadata']
        if meta['report_date'] != period or not same_entity_name(meta['entity'], entity):
            continue
        raw = json.loads(doc['text'])
        if doc['id'] in item.get('evidence_ids', []) and share_class.casefold() not in (
                meta['share_class'].casefold(), meta['share_title'].casefold()):
            return 'statement share class differs from the model perimeter'
        # _catalog has recompiled every observation from its own original bytes.
        # Two receipts can differ in raw-byte identity while proving the same
        # statement. Ignore only that identity; keep URL, dates, metadata, every
        # fact, the comparative row and the full tag disagreement identical.
        raw['source_proof'].pop('source_document_id')
        identity = _json({'url': doc['url'], 'published_at': doc['published_at'],
            'available_at': doc.get('available_at'), 'availability_basis': doc.get('availability_basis'),
            'metadata': {key: value for key, value in meta.items() if key != 'source_document_id'},
            'body': raw})
        relevant.append((doc['id'], identity, bool(raw['tag_comparison']['conflicts'])))
    error = 'conflicting share sources: explicit reconciled statement selection required'
    if relevant and any(identity != relevant[0][1] for _, identity, _ in relevant):
        return error
    if any(conflict for _, _, conflict in relevant) and not any(
            item.get('evidence_ids') == [ident] for ident, _, _ in relevant):
        return error
    return None
