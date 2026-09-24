"""Declared current-period XBRL projection, only after a compiled SEC opening.

Original fact indices and every retained value stay intact. Historical facts
and descriptive/repeated metadata are unavailable, never replaced by estimates.
"""
from copy import deepcopy
from hashlib import sha256
import json

from .sec_preparation_sections import _day, _source_set, select_sec_fcff_context

POLICY = 'sec_fcff_current_facts_v1'
_OMITTED_METADATA = ('accn', 'fy', 'fp', 'form', 'filed', 'frame')


def select_sec_current_facts(dossier, context, contract):
    scope = (contract.get('preparation_stage') or {}).get('scope')
    if scope not in ('bear', 'base', 'bull'):
        return None
    view = select_sec_fcff_context(dossier, context, contract)
    if view is None:
        return None
    try:
        _, _, current, sources = _source_set(dossier)
        period = current['metadata']['report_date']
        calendar = context['completed_plan']['model']['calendar']['value']
        if calendar['valuation_date'] != period:
            return None
        ident = sources[current['id']]
        original = next(d for d in dossier['documents'] if d['id'] == ident)
        parsed = json.loads(original['text'])
        facts = parsed['facts']
        retained = 0
        projected = deepcopy(parsed)
        for index, fact in enumerate(facts):
            observation = fact['observation']
            end = _day(observation['end'])
            if 'start' in observation and _day(observation['start']) > end:
                return None
            if observation['end'] != period:
                projected['facts'][index] = None
                continue
            retained += 1
            projected['facts'][index].pop('label', None)
            for key in _OMITTED_METADATA:
                projected['facts'][index]['observation'].pop(key, None)
        if not retained:
            return None
        body = json.dumps(projected, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (KeyError, TypeError, ValueError, StopIteration):
        return None
    limitation = ('Only XBRL facts ending at the compiled opening date remain. Array indices are unchanged; '
        'null entries mean omitted facts, never zero. Labels and repeated accession/form/filing/fiscal/frame '
        'metadata are omitted from retained facts. Source identity remains in the catalog. '
        'Whole current report and annual financial notes remain in this stage view. '
        'Request original evidence for omitted dates or fields; this selection does not certify economic coverage.')
    projection = {'policy': POLICY, 'source_id': ident, 'original_text_sha256': original['sha256'],
        'projected_text_sha256': sha256(body.encode()).hexdigest(), 'period_end': period,
        'retained_facts': retained, 'omitted_facts': len(facts) - retained,
        'omitted_fields': ['label', *('observation/' + key for key in _OMITTED_METADATA)],
        'coverage_limitations': limitation}
    next(d for d in view['documents'] if d['id'] == ident)['text'] = body
    next(d for d in view['stage_view']['documents'] if d['id'] == ident).update(
        view='verified_structured_projection', structured_projection=projection)
    view['stage_view']['structured_documents']['xbrl'] -= 1
    view['stage_view']['structured_documents']['projected_xbrl'] = 1
    view['stage_view']['excerpt_projection']['full_json_documents'] -= 1
    selection = view['stage_view']['automatic_selection']
    selection.update(parent_policy=selection['policy'], policy=POLICY, structured_projection=projection,
                     coverage_limitations=selection['coverage_limitations'] + ' ' + limitation)
    return view
