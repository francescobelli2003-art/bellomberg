"""Synthetic source reconciliations, never manually prepared issuer valuations."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.input_preparation import _catalog, _day, _fact_proof, prepare_method_inputs
from bellomberg.valuation.fdic_evidence import normalize_fdic_financials
from bellomberg.valuation.regulatory_evidence import normalize_regulatory_pdf
from test_fdic_evidence import _document as fdic_source, _rehash, _bridge
from test_input_preparation_bank import ISSUER, PARENT, BANK, _documents, _estimate, _propose
from test_regulatory_small_parent import _mutate


def _case(bank_name=BANK):
    docs = _documents()
    parent = _mutate(docs[1], '3210 12,000 20.h.', '3210 102,000 20.h.')
    parent_facts = normalize_regulatory_pdf(parent, expected_form='FR Y-9LP',
        expected_entity=PARENT, expected_report_date='2025-12-31')['documents'][0]
    original = fdic_source()
    data = json.loads(original['text'])
    data['data'][0]['data'].update(NAME=bank_name, REPDTE='20251231', EQ=90000, EQPP=0, RBCT1C=90000)
    _rehash(original, data)
    bank = normalize_fdic_financials(original, expected_cert=12345, expected_rssd=23456,
        expected_parent_rssd=34567, expected_entity=bank_name, expected_report_date='2025-12-31')['documents'][0]
    cik, accession = '0000024680', '000002468026000001'
    raw = {'cik': int(cik), 'issuer': ISSUER, 'facts': [
        {'taxonomy': 'us-gaap', 'concept': code, 'unit': 'USD',
         'observation': {'val': value, 'end': '2025-12-31', 'accn': '0000024680-26-000001', 'filed': '2026-09-09'}}
        for code, value in [('StockholdersEquity', 100000000), ('PreferredStockValue', 0)]]}
    body = json.dumps(raw)
    group = {'id': 'xbrl-' + cik + '-' + accession,
        'url': 'https://data.sec.gov/api/xbrl/companyfacts/CIK' + cik + '.json',
        'text': body, 'sha256': sha256(body.encode()).hexdigest(), 'published_at': '2026-09-09',
        'metadata': {'emittente_id': 'CIK:' + cik, 'accession': accession}}
    group_terms = [{'coefficient': sign, 'evidence_ids': [group['id']],
        'quoted_value': raw['facts'][i]['observation']['val'], 'quoted_unit': 'USD',
        'evidence_pointer': {'value': f'/facts/{i}/observation/val',
            'unit': f'/facts/{i}/unit', 'period': f'/facts/{i}/observation/end'}}
        for i, sign in enumerate((1, -1))]
    terms = {'group': {'value': 100., 'evidence_ids': [group['id']],
                       'calculation': {'operation': 'sum', 'terms': group_terms}},
             'parent': _bridge(parent_facts, {'BHCP3210': 1, 'BHCP3283': -1}, 100.),
             'subsidiaries': {bank_name: _bridge(bank, {'EQ': 1, 'EQPP': -1}, 90.)}}
    item = {'value': -90., 'evidence_ids': [group['id'], parent_facts['id'], bank['id']],
            'calculation': {'operation': 'consolidation_equity', 'terms': terms}}
    context = {'model': {'opening_common_equity': {'value': 100.}, 'opening_parent_equity': {'value': 100.},
        'legal_structure': {'value': {'parent_entity': PARENT, 'subsidiaries': [{'id': bank_name, 'regime': 'synthetic'}],
            'capital_basis': 'common_equity', 'accounting_basis': 'GAAP'}}},
        'scenarios': {sc: {'capital.subsidiaries.0.opening_gaap_equity': {'value': 90.}}
                      for sc in ('bear', 'base', 'bull')}}
    return [docs[0], parent, parent_facts, original, bank, group], item, context


def _proof(item, docs, context):
    return _fact_proof('opening_consolidation_adjustments', item, docs, 'USD million',
        '2025-12-31', expected_entity=ISSUER, bank_context=context)


def test_source_reconciliation_derives_negative_elimination_across_bound_reporting_scopes():
    docs, item, context = _case()
    original = deepcopy((docs, item, context))
    catalog, issues, _ = _catalog(docs, _day('2026-09-10'))
    assert not issues
    assert _proof(item, list(catalog.values()), context) is None
    assert (docs, item, context) == original


@pytest.mark.parametrize('name', ['group', 'parent', 'subsidiary.0'])
def test_entity_names_cannot_overwrite_arithmetic_roles(name):
    docs, item, context = _case(name)
    assert _proof(item, docs, context) is None


@pytest.mark.parametrize('field', ['evidence_quote', 'period_quote', 'quoted_value', 'facts'])
def test_other_proof_forms_cannot_be_mixed_into_aggregate(field):
    docs, item, context = _case()
    item[field] = 'unused'
    assert _proof(item, docs, context)


def test_reconciliation_requires_an_opening_date_and_cannot_prove_other_drivers():
    docs, item, context = _case()
    assert _fact_proof('opening_consolidation_adjustments', item, docs, 'USD million',
        expected_entity=ISSUER, bank_context=context)
    assert _fact_proof('capital.parent_opening_debt', item, docs, 'USD million',
        '2025-12-31', expected_entity=ISSUER, bank_context=context)


def test_reconciliation_consumes_each_of_multiple_banks_once():
    docs, item, context = _case()
    raw = deepcopy(docs[3]); data = json.loads(raw['text'])
    data['data'][0]['data'].update(CERT=54321, RSSDID=45678, NAME='Second Synthetic Bank', EQ=30000, RBCT1C=30000)
    _rehash(raw, data)
    second = normalize_fdic_financials(raw, expected_cert=54321, expected_rssd=45678,
        expected_parent_rssd=34567, expected_entity='Second Synthetic Bank', expected_report_date='2025-12-31')['documents'][0]
    docs.extend([raw, second])
    item['calculation']['terms']['subsidiaries']['Second Synthetic Bank'] = _bridge(second, {'EQ': 1, 'EQPP': -1}, 30.)
    item['evidence_ids'].append(second['id']); item['value'] = -120.
    context['model']['legal_structure']['value']['subsidiaries'].append({'id': 'Second Synthetic Bank', 'regime': 'synthetic'})
    for values in context['scenarios'].values():
        values['capital.subsidiaries.1.opening_gaap_equity'] = {'value': 30.}
    assert _proof(item, docs, context) is None
    item['calculation']['terms']['subsidiaries']['Second Synthetic Bank'] = deepcopy(item['calculation']['terms']['subsidiaries'][BANK])
    assert _proof(item, docs, context)


@pytest.mark.parametrize('fault', ['value', 'missing_sub', 'extra_sub', 'parent_entity', 'group_entity',
    'group_preferred', 'parent_as_group', 'group_as_parent', 'wrong_date', 'wrong_sign', 'unconsumed',
    'source_union', 'model_group', 'model_parent', 'scenario_sub', 'missing_context'])
def test_consolidation_rejects_wrong_scope_period_components_and_unreconciled_plan(fault):
    docs, item, context = _case()
    terms = item['calculation']['terms']
    if fault == 'value': item['value'] = -89.
    elif fault == 'missing_sub': terms['subsidiaries'] = {}
    elif fault == 'extra_sub': terms['subsidiaries']['Unknown bank'] = deepcopy(terms['subsidiaries'][BANK])
    elif fault == 'parent_entity': context['model']['legal_structure']['value']['parent_entity'] = 'Other parent'
    elif fault == 'group_entity':
        raw = json.loads(docs[-1]['text']); raw['issuer'] = PARENT; docs[-1]['text'] = json.dumps(raw)
    elif fault == 'group_preferred': terms['group']['calculation']['terms'].pop()
    elif fault == 'parent_as_group': terms['group'] = deepcopy(terms['parent'])
    elif fault == 'group_as_parent': terms['parent'] = deepcopy(terms['group'])
    elif fault == 'wrong_date':
        raw = json.loads(docs[-1]['text']); raw['facts'][0]['observation']['end'] = '2025-06-30'; docs[-1]['text'] = json.dumps(raw)
    elif fault == 'wrong_sign': terms['group']['calculation']['terms'][1]['coefficient'] = 1
    elif fault == 'unconsumed': terms['group']['automatic_adjustment'] = 1.
    elif fault == 'source_union': item['evidence_ids'].pop()
    elif fault == 'model_group': context['model']['opening_common_equity']['value'] = 101.
    elif fault == 'model_parent': context['model']['opening_parent_equity']['value'] = 101.
    elif fault == 'scenario_sub': context['scenarios']['bear']['capital.subsidiaries.0.opening_gaap_equity']['value'] = 91.
    else: context = None
    assert _proof(item, docs, context)


@pytest.mark.parametrize('staged', [False, True])
def test_common_bank_preparer_consumes_derived_opening_and_reaches_engine(tmp_path, staged):
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from bellomberg.valuation.dcf_engine import generate_valuation
    from test_sector_analysis import DAY, providers_for
    docs, reconciliation, _ = _case()
    def propose(dossier, contract):
        assert contract['bank_consolidation_policy']['operation'] == 'consolidation_equity'
        plan = _propose({**dossier, 'documents': _documents()}, contract)
        plan['model']['opening_parent_equity'].update(reconciliation['calculation']['terms']['parent'])
        plan['model']['opening_consolidation_adjustments'] = {
            **_estimate(-90., 'Synthetic observed accounting reconciliation'),
            'kind': 'historical', **reconciliation}
        return plan
    proposer = propose
    if staged:
        from bellomberg.valuation.preparation_ai import StagedProposer
        def reply(dossier, contract):
            plan = propose(dossier, contract)
            scope = contract['preparation_stage']['scope']
            values = plan['model'] if scope == 'model' else plan['scenarios'][scope]
            return {'drivers': {name: values[name] for name in contract['preparation_stage']['drivers']},
                    'rationale': 'Synthetic documented banking case'}
        proposer = StagedProposer(reply, drivers_per_stage=16)
    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))
    prepared = prepare_method_inputs(bundle, documents=docs, propose=proposer)
    assert prepared['status'] == 'prepared', prepared['issues']
    record = next(r for r in prepared['proposal']['method_records'] if r['driver'] == 'opening_consolidation_adjustments')
    assert record['value'] == -90. and 'Riconciliazione aggregata' in record['rationale']
    result = generate_valuation('SYNTH-BANK', prepared_bundle=prepared['bundle'], output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'], result.get('acquisition_tasks')
    assert result['input_consumption']['status'] == 'complete'
    assert result['fair_value_base'] == pytest.approx(10.)
