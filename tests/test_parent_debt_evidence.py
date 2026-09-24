"""FR Y-9LP funding principal only with explicit zero ancillary funding balances."""
from copy import deepcopy
import json
import pytest
from bellomberg.valuation.input_preparation import _fact_proof
from test_regulatory_evidence import _supplemented_parent, _changed, _normalize, ENTITY, REPORT_DATE


def case():
    original = _supplemented_parent()
    original = _changed(original, '3605 12 18.a.', '3605 0 18.a.')
    original = _changed(original, '3607 3 18.c.', '3607 0 18.c.')
    document = _normalize(original)['documents'][0]
    facts = json.loads(document['text'])['facts']
    codes = {'BHCP2309', 'BHCP2332', 'BHCP0368', 'BHCP4062', 'BHCP0279'}
    terms = [{'coefficient': 1, 'evidence_ids': [document['id']],
              'evidence_pointer': {'value': f'/facts/{i}/value', 'unit': f'/facts/{i}/unit', 'period': f'/facts/{i}/end'},
              'quoted_value': f['value'], 'quoted_unit': f['unit']}
             for i, f in enumerate(facts) if f['concept'] in codes]
    return {'value': .781, 'evidence_ids': [document['id']],
            'calculation': {'operation': 'sum', 'terms': terms}}, document


def prove(item, document):
    return _fact_proof('capital.parent_opening_debt', item, [document], 'USD million',
                       REPORT_DATE, expected_entity=ENTITY)


def test_parent_funding_principal_has_explicit_coverage_and_no_inferred_affiliate_debt():
    item, document = case()
    original = deepcopy((item, document))
    assert prove(item, document) is None
    assert (item, document) == original


@pytest.mark.parametrize('fault', ['missing_repo', 'nonzero_deposits', 'nonzero_affiliate',
    'absent_affiliate', 'duplicate_affiliate', 'wrong_date', 'wrong_entity', 'wrong_unit', 'mixed_source', 'other_liabilities'])
def test_incomplete_or_mixed_parent_funding_is_rejected(fault):
    item, document = case()
    raw = json.loads(document['text'])
    facts = raw['facts']
    auxiliary = next(f for f in facts if f['concept'] == 'BHCP3605')
    if fault == 'missing_repo':
        item['calculation']['terms'].pop()
    elif fault == 'nonzero_deposits':
        next(f for f in facts if f['concept'] == 'BHCP2200')['value'] = 1
    elif fault == 'nonzero_affiliate': auxiliary['value'] = 1
    elif fault == 'absent_affiliate': facts.remove(auxiliary)
    elif fault == 'duplicate_affiliate': facts.append(deepcopy(auxiliary))
    elif fault == 'wrong_date': auxiliary['end'] = '2025-06-30'
    elif fault == 'wrong_entity': auxiliary['entity'] = 'Other Parent'
    elif fault == 'wrong_unit': auxiliary['unit'] = 'EUR thousand'
    elif fault == 'mixed_source': item['evidence_ids'].append('unused-source')
    elif fault == 'other_liabilities':
        next(f for f in facts if f['concept'] == 'BHCP0279')['concept'] = 'BHCP2930'
    document['text'] = json.dumps(raw)
    assert prove(item, document)
