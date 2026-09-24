"""Deterministic source-section selection for a recognized English IFRS layout.

No economic inputs or completeness verdicts are produced. Call after the common
source catalog has validated provenance. Unknown/ambiguous layouts retain the
whole source with an explicit issue; the normal context guard can still stop it.
This selector is deliberately separate from spending and paid-response replay.
"""
from datetime import date
from hashlib import sha256
import json
import re


POLICY = 'ifrs_note_sections_v1'
_REQUIRED_POLICIES = frozenset({
    'property, plant and equipment', 'intangible assets', 'inventories',
    'trade and other receivables', 'current and deferred income tax', 'provisions',
    'trade and other payables', 'revenue recognition'})
_SELECTED_POLICIES = _REQUIRED_POLICIES | {
    'right-of-use assets and lease liabilities', 'impairment of non-financial assets',
    'cash and cash equivalents', 'equity', 'cost of sales and other selling expenses'}
_SELECTED_NOTES = frozenset({
    'segment information', 'receivables non-current, net', 'inventories, net',
    'receivables and prepayments, net', 'current tax assets and liabilities',
    'trade receivables, net', 'other liabilities', 'non-current provisions',
    'current allowances and provisions', 'cash flow disclosures'})
_REQUIRED_NOTES = {'segment information', 'inventories, net', 'trade receivables, net', 'cash flow disclosures'}
_MONTHS = {name: number for number, name in enumerate(
    ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'), 1)}
_PERIOD = re.compile(r'For\s+the\s+(?:three|six|nine|twelve)-month\s+period\s+ended\s+'
                     r'([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s*[-\u2013]\s*all\s+amounts', re.I)


def _line_pattern(title):
    return r'^[^\S\n]*' + r'[^\S\n]+'.join(re.escape(word) for word in title.split()) + r'[^\S\n]*$'


def _anchor(body, title):
    matches = list(re.finditer(_line_pattern(title), body, re.M | re.I))
    if len(matches) != 1:
        raise ValueError('missing or ambiguous heading: ' + title)
    return matches[0].start()


def _sections(body, start, end, *, numbered):
    prefix = r'[0-9]{1,3}' if numbered else '[A-Z]'
    pattern = re.compile(r'^(' + prefix + r')[^\S\n]{2,}([^\n]{2,150})$', re.M)
    matches = [m for m in pattern.finditer(body, start, end) if re.search(r'[A-Za-z]', m[2])]
    labels = [int(m[1]) if numbered else ord(m[1]) - ord('A') + 1 for m in matches]
    if not labels or labels != list(range(1, len(labels) + 1)):
        raise ValueError('missing, duplicate or unordered section labels')
    titles = [' '.join(m[2].split()).casefold() for m in matches]
    if len(set(titles)) != len(titles):
        raise ValueError('ambiguous repeated section title')
    return [(titles[i], m.start(), matches[i+1].start() if i+1 < len(matches) else end)
            for i, m in enumerate(matches)]


def _annual_ranges(body):
    general = _anchor(body, 'I. GENERAL INFORMATION')
    accounting = _anchor(body, 'II. ACCOUNTING POLICIES')
    risk = _anchor(body, 'III. FINANCIAL RISK MANAGEMENT')
    notes = _anchor(body, 'IV. OTHER NOTES TO THE CONSOLIDATED FINANCIAL STATEMENTS')
    if not general < accounting < risk < notes:
        raise ValueError('annual section boundaries out of order')
    policies = _sections(body, accounting, risk, numbered=False)
    financial = _sections(body, notes, len(body), numbered=True)
    if not _REQUIRED_POLICIES <= {title for title, _, _ in policies}:
        raise ValueError('required accounting-policy headings not identified')
    if not _REQUIRED_NOTES <= {title for title, _, _ in financial}:
        raise ValueError('required financial-note headings not identified')
    ranges = [(general, accounting, 'issuer_scope')]
    ranges.extend((start, end, 'accounting_policy') for title, start, end in policies if title in _SELECTED_POLICIES)
    tail = next((start for title, start, _ in financial if title == 'business combinations'), None)
    ranges.extend((start, end, 'financial_note') for title, start, end in financial
                  if title in _SELECTED_NOTES and (tail is None or start < tail))
    if tail is not None:
        ranges.append((tail, len(body), 'business_and_recent_developments'))
    return ranges


def _management_prefix(document, documents, as_of):
    metadata = document.get('metadata') or {}
    if metadata.get('form') != '6-K' or metadata.get('evidence_role') != 'foreign_current_report':
        return None
    if any((d.get('metadata') or {}).get('normalizer') == 'statement_tables_v1'
           and d['metadata'].get('source_document_id') == document['id'] for d in documents):
        return None  # The independently normalized primary report must stay whole.
    issuer = metadata.get('emittente_id')
    if not issuer:
        return None
    body = document['text']
    outlook = list(re.finditer(_line_pattern('Market Background and Outlook'), body, re.M | re.I))
    if len(outlook) != 1:
        return None
    statements = re.finditer(_line_pattern('Consolidated Condensed Interim Financial Statements'), body, re.M | re.I)
    for header in statements:
        period = _PERIOD.search(body, header.end(), header.end() + 600)
        if period is None or outlook[0].start() >= header.start():
            continue
        try:
            report_date = date(int(period[3]), _MONTHS[period[1].title()], int(period[2])).isoformat()
        except (KeyError, ValueError):
            continue
        if report_date > as_of:
            continue
        peers = []
        for source in documents:
            peer = source.get('metadata') or {}
            if (source['id'] == document['id'] or peer.get('emittente_id') != issuer
                    or peer.get('form') != '6-K' or peer.get('report_date') != report_date
                    or peer.get('perimetro') != 'consolidato'):
                continue
            normalized = [d for d in documents if (d.get('metadata') or {}).get('normalizer') == 'statement_tables_v1'
                          and d['metadata'].get('source_document_id') == source['id']
                          and d['metadata'].get('emittente_id') == issuer
                          and d['metadata'].get('report_date') == report_date]
            if len(normalized) == 1:
                peers.append(source['id'])
        if len(peers) == 1:
            return header.start(), report_date, peers[0]
        return None  # Do not skip an unresolved first dated statement to choose a later one.
    return None


def select_fcff_note_sections(dossier):
    """Build an exhaustive manifest without issuer IDs, dates or numeric offsets in rules.

    An older annual may be narrowed only with a same-issuer, later interim source
    retained whole and a management report whose statement header proves that
    period. Event dates never substitute for accounting periods. This qualifies
    document selection, not the sufficiency of those sources for a valuation.
    """
    if dossier.get('method_id') != 'operating_fcff':
        raise ValueError('section policy supports FCFF only')
    as_of = date.fromisoformat(dossier['as_of']).isoformat()
    documents = dossier['documents']
    ids, narrative = set(), []
    for document in documents:
        ident, body = document.get('id'), document.get('text')
        if not isinstance(ident, str) or not ident or ident in ids or not isinstance(body, str):
            raise ValueError('unique document IDs and source text required')
        ids.add(ident)
        if document.get('sha256') != sha256(body.encode('utf-8')).hexdigest():
            raise ValueError('source text SHA-256 mismatch: ' + ident)
        try:
            json.loads(body)
        except ValueError:
            narrative.append(document)
    management = {d['id']: pair for d in narrative if (pair := _management_prefix(d, documents, as_of)) is not None}
    manifest, issues, applied = [], [], False
    for document in narrative:
        ident, body, metadata = document['id'], document['text'], document.get('metadata') or {}
        ranges = [(0, len(body), 'complete_source')]
        rationale = 'Whole narrative retained; repeated blank lines only are compacted.'
        limitation = 'Source presence does not certify economic sufficiency.'
        if ident in management:
            end, period, primary = management[ident]
            ranges = [(0, end, 'complete_management_narrative')]
            rationale = 'Management narrative and outlook retained before the dated statement appendix; complete current source ' + primary + ' has the same issuer and reporting period ' + period + '.'
            limitation = 'The management financial/APM appendices are excluded. The separate current source is retained whole, not asserted byte-identical or economically sufficient.'
        elif metadata.get('form') == '20-F':
            try:
                if not isinstance(metadata.get('report_date'), str):
                    raise ValueError('annual reporting period missing')
                report_date = date.fromisoformat(metadata['report_date']).isoformat()
                current = [pair for d in narrative if d['id'] in management
                           and (d.get('metadata') or {}).get('emittente_id') == metadata.get('emittente_id')
                           and report_date < (pair := management[d['id']])[1]]
                if not current:
                    raise ValueError('later same-issuer current notes and dated management report not qualified')
                ranges = _annual_ranges(body)
                rationale = 'Recognized annual accounting policies and complete relevant note sections, with later same-issuer interim notes and management narrative retained separately; no economic figures supplied by the selector.'
                limitation = 'Annual business/risk/ESG text, financial-risk section and unselected financial notes are omitted. Later sources do not prove those omissions unchanged or immaterial. Request missing evidence or leave the driver incomplete; omission never means zero.'
            except (KeyError, ValueError) as exc:
                reason = str(exc)
                issues.append({'source_id': ident, 'code': 'section_selection_unavailable', 'reason': reason})
                limitation = 'Whole annual retained because automatic section selection failed: ' + reason
        applied |= ranges != [(0, len(body), 'complete_source')]
        excerpts = [{'theme': theme, 'char_start': start, 'char_end_exclusive': end,
                     'excerpt_sha256_utf8': sha256(body[start:end].encode('utf-8')).hexdigest()}
                    for start, end, theme in ranges]
        manifest.append({'source_id': ident, 'original_text_sha256_utf8': document['sha256'],
                         'original_document_sha256_bytes': document.get('document_sha256'),
                         'url': document.get('url'), 'published_at': document.get('published_at'),
                         'layout_projection': 'collapse_blank_lines_v1', 'excerpts': excerpts,
                         'selection_rationale': POLICY + ': ' + rationale, 'coverage_limitations': limitation})
    return {'policy': POLICY, 'manifest': manifest, 'issues': issues, 'selection_applied': applied,
            'semantic_coverage_certified': False}
