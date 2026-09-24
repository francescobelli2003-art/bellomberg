"""A normalized SEC issuer label must bind the opening amount to the model."""
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.input_preparation import _fact_proof, prepare_method_inputs
from test_input_preparation import _bundle
from test_opening_equity_bridge_evidence import case
from test_input_evidence_semantics import _ttm_case


def normalized_case():
    documents, plan = case()
    doc = documents[-1]
    raw = json.loads(doc['text'])
    raw.pop('entity')
    raw.update(cik='0000000001', issuer='SYNTH-GROUP')
    for fact in raw['facts']:
        fact.pop('entity')
        fact['taxonomy'] = 'us-gaap'
        fact['observation'] = {'val':fact.pop('value'), 'end':fact.pop('end'),
            'accn':'0000000001-26-000001', 'filed':doc['published_at']}
    doc.update(id='xbrl-0000000001-000000000126000001', origin='SEC_XBRL_tool',
        url='https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json',
        metadata={'emittente_id':'CIK:0000000001', 'accession':'000000000126000001'})
    doc['text'] = json.dumps(raw)
    doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    for scenario in plan['scenarios'].values():
        for driver in ('net_debt','equity_adjustments'):
            item = scenario[driver]
            item['evidence_ids'] = [doc['id']]
            terms = item['calculation']['terms'] if 'calculation' in item else [item]
            for term in terms:
                term['evidence_ids'] = [doc['id']]
                pointers = term['evidence_pointer']
                pointers['value'] = pointers['value'].replace('/value','/observation/val')
                pointers['period'] = pointers['period'].replace('/end','/observation/end')
    return documents, plan


def test_sec_opening_claims_with_matching_normalized_issuer_still_compile():
    documents, plan = normalized_case()
    result = prepare_method_inputs(_bundle(), documents=documents, propose=lambda *_:plan)
    assert result['status']=='prepared', result['issues']


@pytest.mark.parametrize('issuer', ['ANOTHER-ISSUER', '', None, ['SYNTH-GROUP'], 'missing'])
def test_compiler_rejects_different_or_missing_sec_root_issuer(issuer):
    documents, plan = normalized_case()
    raw = json.loads(documents[-1]['text'])
    if issuer=='missing':
        raw.pop('issuer')
    else:
        raw['issuer'] = issuer
    documents[-1]['text'] = json.dumps(raw)
    documents[-1]['sha256'] = sha256(documents[-1]['text'].encode()).hexdigest()
    result = prepare_method_inputs(_bundle(), documents=documents, propose=lambda *_:plan)
    assert result['status']=='incomplete'
    assert any(row['code']=='unverified_fact' and row['field']=='net_debt'
               for row in result['issues'])
    assert result['bundle']['case']['records']==[]


def test_sec_root_issuer_cannot_be_overridden_by_a_different_fact_or_metadata_label():
    documents, plan = normalized_case()
    raw = json.loads(documents[-1]['text'])
    raw['issuer'] = 'ANOTHER-ISSUER'
    for fact in raw['facts']:
        fact['entity'] = 'SYNTH-GROUP'
    documents[-1]['metadata']['entity'] = 'SYNTH-GROUP'
    documents[-1]['text'] = json.dumps(raw)
    item = plan['scenarios']['base']['net_debt']
    assert _fact_proof('net_debt', item, [documents[-1]], 'EUR million', '2025-12-31',
                       expected_entity='SYNTH-GROUP') is not None


def test_ttm_operands_from_one_other_issuer_do_not_bind_to_the_requested_entity():
    item, documents = _ttm_case()
    for doc in documents:
        raw=json.loads(doc['text']);raw['issuer']='ANOTHER-ISSUER';doc['text']=json.dumps(raw)
    assert _fact_proof('historical_revenue', item, documents, 'EUR million', '2026-06-30',
                       expected_entity='SYNTH-GROUP') is not None
