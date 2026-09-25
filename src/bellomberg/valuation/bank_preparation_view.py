"""Bank prompt projection over immutable receipts; never economic certification.

Keep every source identity and every structured observation/index. A repeated
narrative may use a declared cover excerpt only when an identical full source
is retained. Prior cited IDs keep their own text; citations are never aliased.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re

from .preparation_view import _structured_kind, _canonical

POLICY = 'bank_source_context_v1'


def _cited(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'evidence_ids' and isinstance(child, list):
                yield from (ident for ident in child if isinstance(ident, str))
            else:
                yield from _cited(child)
    elif isinstance(value, list):
        for child in value:
            yield from _cited(child)


def _cover(document, facts):
    """Recognize a SEC cover after an inline-XBRL preamble, not arbitrary prose."""
    meta, body = document.get('metadata') or {}, document['text']
    if meta.get('form') != '10-K' or not isinstance(meta.get('accession'), str):
        return 0
    accession = meta['accession'].replace('-', '')
    matches = [d for d, parsed in facts if d['metadata']['accession'] == accession
               and d['metadata']['emittente_id'] == meta.get('emittente_id')
               and all(isinstance(f['observation'].get('accn'), str)
                       and f['observation']['accn'].replace('-', '') == accession
                       for f in parsed['facts'])]
    if len(matches) != 1:
        return 0
    covers = list(re.finditer(r'^UNITED STATES[^\S\n]*\n\s*SECURITIES AND EXCHANGE COMMISSION[^\S\n]*$', body, re.M))
    if len(covers) != 1:
        return 0
    cover = covers[0]
    from .sec_preparation_sections import _item
    try:
        business = _item(body, '1', 'business')
    except ValueError:
        return 0
    if (not cover.end() < business or not re.search(r'^FORM 10-K\s*$', body[cover.end():cover.end()+400], re.M)
            or not all(token in body[:cover.start()] for token in ('iso4217:', 'xbrli:'))):
        return 0
    return cover.start()


def _appendix_ranges(body, cover):
    """Only the explicit SEC layout with complete statements after Item 16."""
    if not cover:
        return None
    from .sec_preparation_sections import _item
    try:
        business = _item(body, '1', 'business')
        risk, risk_end, legal, legal_end, management, statements, controls, summary = (
            _item(body, number, '.+') for number in ('1A', '1B', '3', '4', '7', '8', '9', '16'))
        supervision = list(re.finditer(r'^Supervision and Regulation[^\S\n]*$', body, re.M))
        if len(supervision) != 1:
            return None
        regulation = supervision[0].start()
        if not cover < business < regulation < risk < risk_end < legal < legal_end < management < statements < controls < summary:
            return None
        if not re.search(r'immediately following Item\s+16\b', body[statements:controls], re.I):
            return None
        if not re.search(r'^Consolidated (?:Statements of Financial Condition|Balance Sheets)[^\S\n]*$', body[summary:], re.M | re.I):
            return None
    except ValueError:
        return None
    return [(cover, min(cover + 1000, business)), (regulation, risk_end),
            (legal, legal_end), (management, controls), (summary, len(body))]


def select_bank_context(dossier, context, contract):
    from .preparation_refresh import _review_view
    if dossier.get('method_id') != 'bank_residual_income':
        raise ValueError('bank context policy requires bank method')
    documents = dossier['documents']; facts = []; narrative = []; structured = {}; ids = set()
    for doc in documents:
        ident, body = doc.get('id'), doc.get('text')
        if not isinstance(ident, str) or ident in ids or not isinstance(body, str) or not body.strip():
            raise ValueError('bank context requires unique IDs and source text')
        ids.add(ident)
        if doc.get('sha256') != sha256(body.encode()).hexdigest():
            raise ValueError('bank source text SHA-256 mismatch')
        try:
            parsed = json.loads(body)
        except ValueError:
            narrative.append(doc)
            continue
        structured[ident] = parsed
        if (_structured_kind(doc, parsed, dossier['ticker']) == 'xbrl'
                and all(isinstance(f['observation'].get('accn'), str)
                        and f['observation']['accn'].replace('-', '') == doc['metadata']['accession']
                        for f in parsed['facts'])):
            facts.append((doc, parsed))
    protected = set(_cited({k: context.get(k) for k in ('prior_plan', 'reviewed_plan', 'completed_plan')}))
    groups = {}
    for doc in narrative:
        meta = doc.get('metadata') or {}
        key = (doc.get('url'), doc.get('published_at'), meta.get('emittente_id'),
               meta.get('form'), meta.get('report_date'), doc['text'])
        groups.setdefault(key, []).append(doc)
    retained = {}
    for group in groups.values():
        selected = next((d for d in group if d['id'] in protected), group[0])
        for doc in group:
            if doc is not selected and doc['id'] not in protected:
                retained[doc['id']] = selected['id']
    manifest = []
    for doc in narrative:
        body = doc['text']; start = _cover(doc, facts); end = len(body)
        ranges = _appendix_ranges(body, start) or [(start, end)]
        rationale = ('All narrative from the recognized SEC cover through the end is retained; '
                     'inline-XBRL preamble is omitted, with accession-bound structured facts retained.'
                     if start else 'Whole narrative retained; unrecognized cover is never guessed.')
        if len(ranges) > 1:
            rationale = ('Recognized statements-after-Item-16 layout: retain cover excerpt, complete supervision/regulation and risks, '
                         'legal proceedings, management/market discussion and every statement/note in the full financial appendix. '
                         'Other business prose, properties/cybersecurity, share-market/governance sections and exhibit index omitted. '
                         'These omissions are not certified immaterial; request them if needed for an economic driver.')
        if doc['id'] in retained:
            end = min(start + 1000, end)
            ranges = [(start, end)]
            rationale += (' Cover excerpt only for this duplicate receipt: identical full extracted text, URL '
                          'and publication date in source ' + retained[doc['id']] +
                          '. Cite that retained source for omitted text using its own dates and identity. '
                          'Raw bytes and other metadata are not claimed identical; no citation alias.')
        manifest.append({'source_id': doc['id'], 'original_text_sha256_utf8': doc['sha256'],
            'original_document_sha256_bytes': doc.get('document_sha256'), 'url': doc.get('url'),
            'published_at': doc.get('published_at'), 'layout_projection': 'collapse_blank_lines_v1',
            'excerpts': [{'theme': 'bank_narrative', 'char_start': lo, 'char_end_exclusive': hi,
                          'excerpt_sha256_utf8': sha256(body[lo:hi].encode()).hexdigest()} for lo, hi in ranges],
            'selection_rationale': rationale,
            'coverage_limitations': 'Selection does not certify sufficiency, freshness or unchanged economics. Omitted text cannot support claims under this source ID.'})
    view = _review_view(dossier, manifest)
    for key, value in context.items():
        if key not in {'documents', 'stage_view', 'acquired_sources', 'document_acquisition', 'review_view_omissions'}:
            view[key] = deepcopy(value)
    reports = {r['id']: r for r in view['stage_view']['documents']}
    xbrl = {d['id']: parsed for d, parsed in facts}
    for doc in view['documents']:
        report = reports[doc['id']]
        for field in ('pdf_form_fields', 'inline_parent_fields'):
            if field in doc:
                raw = _canonical(doc.pop(field)).encode()
                report[field] = {'view': 'excluded', 'sha256': sha256(raw).hexdigest(), 'bytes': len(raw),
                    'reason': 'Acquisition layout omitted. Original catalog, primary narrative and normalized source documents remain; layout is not visible evidence.'}
        if doc['id'] in xbrl:
            parsed = deepcopy(xbrl[doc['id']])
            for fact in parsed['facts']:
                fact.pop('label', None)
                for key in ('accn', 'fy', 'fp', 'form', 'filed', 'frame'):
                    fact['observation'].pop(key, None)
            doc['text'] = _canonical(parsed)
            report.update(view='xbrl_metadata_projection',
                reason='Every fact, value, unit, period and original array index retained. Labels and observation accn/fy/fp/form/filed/frame omitted; pointers to omitted or changed parent objects are invalid.')
        elif doc['id'] in structured:
            compact = _canonical(structured[doc['id']])
            if compact != doc['text']:
                doc['text'] = compact
                report.update(view='json_layout_projection', reason='JSON whitespace compacted; complete parsed values, object keys and array indices unchanged.')
    view['stage_view']['automatic_selection'] = {'policy': POLICY, 'semantic_coverage_certified': False}
    from .preparation_refresh import _completed_plan_view
    return _completed_plan_view(view, contract)
