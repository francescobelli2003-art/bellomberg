"""Declared SEC stage views over immutable sources, never economic certification.

Only a recognized annual/current/prior-year interim set with accession-bound
XBRL can be projected. Unknown or ambiguous layouts return no alternative; the
ordinary context guard then keeps the original evidence or stops before spend.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import re

POLICY = 'sec_fcff_stage_sections_v1'
_ITEM = re.compile(r'^[^\S\n]*Item[^\S\n]+(\d{1,2}[A-C]?)\.[^\S\n]+([^\n]+)$', re.M | re.I)
_NOTE = re.compile(r'^[^\S\n]*(?:Note[^\S\n]+)?(\d{1,2})\.[^\S\n]+([A-Za-z][^\n]{1,140})$', re.M | re.I)
_NOTES = re.compile(r'^[^\S\n]*NOTES TO (?:CONDENSED )?CONSOLIDATED FINANCIAL STATEMENTS[^\S\n]*$', re.M | re.I)


def _day(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('noncanonical date')
    return parsed


def _title(value):
    return ' '.join(value.split()).casefold().rstrip('.')


def _item(body, number, title):
    candidates = [m for m in _ITEM.finditer(body) if m[1].upper() == number
                  and re.fullmatch(title, _title(m[2]))]
    if len(candidates) != 1:
        raise ValueError('missing or ambiguous SEC Item ' + number)
    return candidates[0].start()


def _notes(body):
    """Find a real notes block, not its table-of-contents reference."""
    candidates = []
    items = list(_ITEM.finditer(body))
    for heading in _NOTES.finditer(body):
        later = [m for m in items if m.start() > heading.start()]
        if not later:
            continue
        end = later[0].start()
        notes = list(_NOTE.finditer(body, heading.end(), end))
        if (not notes or notes[0][1] != '1'
                or body[heading.end():notes[0].start()].strip().casefold() not in ('', '(unaudited)')
                or [int(m[1]) for m in notes] != list(range(1, len(notes) + 1))):
            continue
        titles = [_title(m[2]) for m in notes]
        if len(set(titles)) != len(titles):
            continue
        preceding = [m for m in items if m.start() < heading.start()]
        if not preceding:
            continue
        candidates.append((heading.start(), preceding[-1], end,
                           [(titles[i], m.start(), notes[i+1].start() if i+1 < len(notes) else end)
                            for i, m in enumerate(notes)]))
    if len(candidates) != 1:
        raise ValueError('missing or ambiguous numbered financial notes')
    return candidates[0]


def _annual_ranges(body, scope):
    business = _item(body, '1', r'business')
    risk = _item(body, '1A', r'risk factors')
    risk_end = _item(body, '1B', r'unresolved staff comments')
    legal = _item(body, '3', r'legal proceedings')
    legal_end = _item(body, '4', r'mine safety disclosures')
    management = _item(body, '7', r'management.s discussion and analysis of financial condition and results of operations')
    market = _item(body, '7A', r'quantitative and qualitative disclosures about market risk')
    statements = _item(body, '8', r'financial statements and supplementary data')
    accountants = _item(body, '9', r'changes in and disagreements with accountants on accounting and financial disclosure')
    if not business < risk < risk_end < legal < legal_end < management < market < statements < accountants:
        raise ValueError('SEC annual Items out of order')
    _, container, end, notes = _notes(body)
    if container[1] not in ('8', '15'):
        raise ValueError('financial notes outside Item 8 or 15')
    policies = [n for n in notes if re.fullmatch(r'(?:summary of )?(?:significant )?accounting policies', n[0])]
    if len(policies) != 1:
        raise ValueError('annual accounting-policy note not unique')
    if scope == 'model':
        return [(notes[0][1], policies[0][2], 'annual_scope_and_policies')]
    return [(business, risk_end, 'annual_business_and_risks'),
            (legal, legal_end, 'annual_legal_proceedings'),
            (management, statements, 'annual_management_and_market_risk'),
            (container.start(), end, 'annual_financial_statements_and_notes')]


def _interim_range(body, scope, theme):
    start = _item(body, '1', r'(?:condensed consolidated )?financial statements(?: \(unaudited\))?')
    management = _item(body, '2', r'management.s discussion and analysis of financial condition and results of operations')
    notes, container, end, _ = _notes(body)
    if container[1] != '1' or not start < notes < management or end != management:
        raise ValueError('interim financial statements/notes not bounded')
    return [(start, management if scope == 'model' else notes, theme)]


def _source_set(dossier):
    from .preparation_view import _structured_kind
    cutoff = _day(dossier['as_of'])
    docs = dossier['documents']
    ids = [d['id'] for d in docs]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate source IDs')
    primaries = [d for d in docs if (d.get('metadata') or {}).get('form') in ('10-K', '10-Q')]
    if len(primaries) != 3:
        raise ValueError('one annual and two interims required')
    issuers = {(d.get('metadata') or {}).get('emittente_id') for d in primaries}
    if len(issuers) != 1 or not re.fullmatch(r'CIK:\d{10}', next(iter(issuers)) or ''):
        raise ValueError('issuer mismatch')
    for doc in primaries:
        if (_day(doc['metadata']['report_date']) > cutoff or _day(doc['published_at']) > cutoff
                or _day(doc['published_at']) < _day(doc['metadata']['report_date'])):
            raise ValueError('source period/publication not available at cutoff')
    annuals = [d for d in primaries if d['metadata']['form'] == '10-K']
    quarters = sorted((d for d in primaries if d['metadata']['form'] == '10-Q'),
                      key=lambda d: d['metadata']['report_date'])
    if len(annuals) != 1 or len(quarters) != 2:
        raise ValueError('ambiguous annual/interim roles')
    annual, comparative, current = annuals[0], quarters[0], quarters[1]
    before, on = (_day(d['metadata']['report_date']) for d in (comparative, current))
    if not (before < _day(annual['metadata']['report_date']) < on
            and on.year == before.year + 1 and on.isoformat()[5:] == before.isoformat()[5:]):
        raise ValueError('prior-year comparative not matched')
    facts = {}
    for primary in primaries:
        accession = primary['metadata']['accession'].replace('-', '')
        matches = []
        for doc in docs:
            try:
                parsed = json.loads(doc['text'])
            except ValueError:
                continue
            meta = doc.get('metadata') or {}
            if (_structured_kind(doc, parsed, dossier['ticker']) == 'xbrl'
                    and meta.get('accession') == accession
                    and meta.get('emittente_id') == primary['metadata']['emittente_id']
                    and all(f['observation'].get('accn', '').replace('-', '') == accession
                            for f in parsed['facts'])):
                matches.append(doc['id'])
        if len(matches) != 1:
            raise ValueError('accession-bound XBRL not unique')
        facts[primary['id']] = matches[0]
    return annual, comparative, current, facts


def select_sec_fcff_context(dossier, context, contract):
    """Second automatic policy, after all existing paid request forms are tried.

    Forecast projection requires a completed opening shape; StagedProposer runs
    the real source/record compiler before reaching it. Projection never grants
    validity to a plan, a source, or the underlying economic assumptions.
    """
    scope = (contract.get('preparation_stage') or {}).get('scope')
    if dossier.get('method_id') != 'operating_fcff' or scope not in ('model', 'bear', 'base', 'bull'):
        return None
    from .operating_adapter import SCHEMA
    if scope != 'model':
        opening = (context.get('completed_plan') or {}).get('model') or {}
        if any(not isinstance(opening.get(name), dict) or 'value' not in opening[name]
               for name, spec in SCHEMA.items() if spec[-1] == 'model'):
            return None
    for doc in dossier['documents']:
        if doc.get('sha256') != sha256(doc['text'].encode('utf-8')).hexdigest():
            raise ValueError('source text SHA-256 mismatch')
    try:
        annual, comparative, current, facts = _source_set(dossier)
        ranges = {annual['id']: _annual_ranges(annual['text'], scope),
                  comparative['id']: _interim_range(comparative['text'], scope, 'comparative_financials')}
        if scope == 'model':
            ranges[current['id']] = _interim_range(current['text'], scope, 'current_financials')
    except (ValueError, TypeError, KeyError, IndexError):
        return None
    limitation = ('Opening: retain current and comparative financial statements with all their notes, and annual issuer/accounting-policy notes. '
                  'Annual business, risks and other notes, and interim sections outside financial statements/notes are omitted from this stage. '
                  'All structured facts remain. This does not certify policies unchanged or source sufficiency.' if scope == 'model' else
                  'Forecast: retain whole current report, annual business/risks/legal/management discussion and full financial notes, '
                  'plus comparative statements. Other annual sections and comparative notes/management narrative are omitted. '
                  'Historical annual/comparative XBRL texts are excluded after opening compilation; completed opening and original '
                  'source references remain. Request omitted evidence if needed; omission never means zero or unchanged.')
    manifest = []
    for doc in dossier['documents']:
        try:
            json.loads(doc['text'])
            continue
        except ValueError:
            pass
        spans = ranges.get(doc['id'], [(0, len(doc['text']), 'complete_source')])
        manifest.append({'source_id': doc['id'], 'original_text_sha256_utf8': doc['sha256'],
            'original_document_sha256_bytes': doc.get('document_sha256'),
            'url': doc.get('url'), 'published_at': doc.get('published_at'),
            'layout_projection': 'collapse_blank_lines_v1',
            'excerpts': [{'theme': theme, 'char_start': start, 'char_end_exclusive': end,
                          'excerpt_sha256_utf8': sha256(doc['text'][start:end].encode()).hexdigest()}
                         for start, end, theme in spans],
            'selection_rationale': POLICY + ': recognized SEC roles and complete section boundaries; no economic estimates.',
            'coverage_limitations': limitation})
    from .preparation_view import select_stage_view
    view = select_stage_view(dossier, scope, excerpt_manifest=manifest)
    for key, value in context.items():
        if key not in ('documents', 'stage_view', 'acquired_sources'):
            view[key] = deepcopy(value)
    excluded = []
    if scope != 'model':
        older = {facts[annual['id']], facts[comparative['id']]}
        descriptions = {row['id']: row for row in view['stage_view']['documents']}
        for doc in view['documents']:
            if doc['id'] in older:
                del doc['text']
                descriptions[doc['id']].update(view='metadata_only',
                    reason='Historical XBRL excluded only in forecast stage after opening compilation; original catalog and completed opening preserved.')
                excluded.append(doc['id'])
        view['stage_view']['structured_documents']['xbrl'] -= len(excluded)
        view['stage_view']['excerpt_projection']['full_json_documents'] -= len(excluded)
    view['stage_view']['automatic_selection'] = {'policy': POLICY, 'scope': scope,
        'current_document_id': current['id'], 'annual_document_id': annual['id'],
        'comparative_document_id': comparative['id'], 'excluded_historical_xbrl': excluded,
        'manifest': manifest, 'selection_applied': True, 'semantic_coverage_certified': False,
        'coverage_limitations': limitation}
    return view
