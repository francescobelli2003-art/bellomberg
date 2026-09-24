"""Narrow FR Y-9LP principal bridge; mixed affiliate balances are never debt."""
import json
from .input_evidence import _finite, same_entity_name

_PRINCIPAL = ('BHCP0279', 'BHCP2309', 'BHCP2332', 'BHCP0368', 'BHCP4062')
_ZERO_REQUIRED = ('BHCP2200', 'BHCP3605', 'BHCP3606', 'BHCP3607')


def parent_debt_policy():
    return {'taxonomy': 'fr-y-9lp-pc', 'scope': 'parent_only',
            'principal_components': {code: 1 for code in _PRINCIPAL},
            'required_reported_zero_balances': list(_ZERO_REQUIRED),
            'other_liabilities': 'BHCP2930 must be reported separately; accrued expenses, taxes and operating-lease liabilities are excluded from funding principal. Related operating cash costs remain in the parent cash forecast; do not silently omit them or subtract them again as debt.',
            'requirement': 'Use a sum with all five principal components from one normalized FR Y-9LP. Deposits and all three affiliate balance rows must be explicitly reported zero at the same date, entity and unit. Missing or nonzero ancillary balances require another documented principal reconciliation, not assumed zero or wholesale inclusion of mixed balances. No FR Y-9SP equivalence is inferred.',
            'instructions': 'https://www.federalreserve.gov/apps/reportingforms/Download/DownloadAttachment?guid=eb9daf46-0a00-4955-8055-a0eac6350cf5'}


def validate_parent_debt_source(item, evidence, period, entity):
    """Validate coverage, leaving exact operand arithmetic to structured_fact_proof."""
    try:
        if len(evidence) != 1 or item.get('evidence_ids') != [evidence[0]['id']]:
            raise ValueError('parent debt: one complete normalized parent source required')
        doc = evidence[0]; metadata = doc.get('metadata', {})
        if (metadata.get('normalizer') != 'regulatory_pdf_v1' or metadata.get('form') != 'FR Y-9LP'
                or metadata.get('scope') != 'parent_only' or not same_entity_name(metadata.get('entity'), entity)):
            raise ValueError('parent debt: supported parent-only FR Y-9LP required')
        facts = json.loads(doc['text'])['facts']
        for code in (*_PRINCIPAL, *_ZERO_REQUIRED, 'BHCP2930'):
            matches = [f for f in facts if f.get('concept') == code]
            if len(matches) != 1:
                raise ValueError('parent debt: missing or duplicate coverage row ' + code)
            f = matches[0]
            if (f.get('taxonomy') != 'fr-y-9lp-pc' or f.get('scope') != 'parent_only'
                    or not same_entity_name(f.get('entity'), entity) or f.get('end') != period
                    or not _finite(f.get('value')) or f['value'] < 0 or f.get('unit') != 'USD thousand'):
                raise ValueError('parent debt: inconsistent coverage row ' + code)
            if code in _ZERO_REQUIRED and f['value'] != 0:
                raise ValueError('parent debt: ancillary funding needs a separate principal allocation: ' + code)
        calculation = item.get('calculation')
        if not isinstance(calculation, dict) or calculation.get('operation') != 'sum':
            raise ValueError('parent debt: explicit complete principal sum required')
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return str(exc)
    return None
