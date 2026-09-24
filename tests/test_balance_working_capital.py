"""A complete numeric balance cannot silently become a narrow working-capital sum."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.balance_sheet_evidence import normalize_balance_sheet
from bellomberg.valuation.balance_working_capital import (
    balance_nwc_proof, balance_nwc_selection_problem, balance_nwc_policy,
)
from bellomberg.valuation.input_preparation import _fact_proof, _source_scale, prepare_method_inputs
from test_balance_sheet_evidence import balance_raw, primary
from test_input_preparation import _bundle, _documents, _operating_plan


def case():
    origin = primary(balance_raw().replace(b'iso4217:USD', b'iso4217:EUR'))
    ledger = normalize_balance_sheet(origin)['documents'][0]
    body = json.loads(ledger['text'])
    classifications = []
    for component in body['components']:
        tag = component['reported_tag']
        treatment = {'us-gaap:CashAndCashEquivalentsAtCarryingValue': 'cash_or_investment',
                     'us-gaap:PropertyPlantAndEquipmentNet': 'fixed_or_intangible_asset',
                     'us-gaap:LongTermDebtNoncurrent': 'financing'}.get(tag, 'operating_nwc')
        classifications.append({'component_tag': tag, 'treatment': treatment,
            'judgment': 'analyst_estimate', 'rationale': 'Synthetic business classification based on the reported nature of this balance.',
            'evidence_ids': [origin['id']], 'evidence_quote': component['label']})
    review = [{'disclosure_index': index, 'treatment': 'outside_nwc', 'judgment': 'analyst_estimate',
        'rationale': 'Unquantified contingencies are outside this NWC measure and require a separate valuation review, not an assumed zero.',
        'evidence_ids': [origin['id']], 'evidence_quote': row['label']}
        for index, row in enumerate(body['nonmonetary_disclosures'])]
    item = {**_operating_plan()['model']['opening_nwc'], 'value': -.002,
        'evidence_ids': [ledger['id'], origin['id']], 'calculation': {
            'operation': 'balance_sheet_nwc', 'balance_document_id': ledger['id'],
            'classifications': classifications, 'nonmonetary_review': review}}
    for key in ('evidence_quote', 'quoted_value', 'quoted_unit', 'period_quote'):
        item.pop(key)
    return origin, ledger, item


def prove(item, docs, unit='EUR million', entity='Synthetic Industrial Issuer', period='2025-12-31'):
    return balance_nwc_proof(item, docs, unit, period, entity, scale=_source_scale)


def test_full_classification_reconciles_selected_amount_and_keeps_judgments_separate():
    origin, ledger, item = case(); before = deepcopy((origin, ledger, item))
    assert prove(item, [origin, ledger]) is None
    assert _fact_proof('opening_nwc', item, [origin, ledger], 'EUR million', '2025-12-31',
                       expected_entity='Synthetic Industrial Issuer') is None
    assert (origin, ledger, item) == before
    assert _fact_proof('shares', item, [origin, ledger], 'EUR million', '2025-12-31') is not None


@pytest.mark.parametrize('mutation', ['omitted', 'duplicate', 'unknown', 'unresolved', 'blank_reason',
    'fake_quote', 'missing_evidence', 'wrong_source', 'invented_amount', 'pm_approval', 'misstated_kind',
    'cash_in_nwc', 'debt_in_nwc', 'fixed_assets_in_nwc', 'debt_as_asset', 'wrong_total',
    'missing_disclosure', 'duplicate_disclosure', 'unresolved_disclosure', 'nil_as_zero',
    'mixed_quote', 'old_operation', 'missing_primary', 'forged_ledger', 'extra_calculation'])
def test_incomplete_false_or_unconsumed_coverage_is_rejected(mutation):
    origin, ledger, item = case(); docs = [origin, ledger]
    calc = item['calculation']; rows = calc['classifications']
    if mutation == 'omitted': rows.pop()
    elif mutation == 'duplicate': rows.append(deepcopy(rows[0]))
    elif mutation == 'unknown': rows[0]['component_tag'] = 'ex:NotReported'
    elif mutation == 'unresolved': rows[0]['treatment'] = 'unresolved'
    elif mutation == 'blank_reason': rows[0]['rationale'] = ' '
    elif mutation == 'fake_quote': rows[0]['evidence_quote'] = 'An invented unreported accounting policy'
    elif mutation == 'missing_evidence': rows[0]['evidence_ids'] = []
    elif mutation == 'wrong_source': rows[0]['evidence_ids'] = [ledger['id']]
    elif mutation == 'invented_amount': rows[0]['value'] = 0
    elif mutation == 'pm_approval': rows[0]['approved_by_pm'] = True
    elif mutation == 'misstated_kind': rows[0]['judgment'] = 'historical'
    elif mutation in ('cash_in_nwc', 'debt_in_nwc', 'fixed_assets_in_nwc'):
        role = {'cash_in_nwc':'cash_or_investment', 'debt_in_nwc':'financing',
                'fixed_assets_in_nwc':'fixed_or_intangible_asset'}[mutation]
        next(row for row in rows if row['treatment'] == role)['treatment'] = 'operating_nwc'
    elif mutation == 'debt_as_asset':
        next(row for row in rows if row['treatment'] == 'financing')['treatment'] = 'fixed_or_intangible_asset'
    elif mutation == 'wrong_total': item['value'] = 0
    elif mutation == 'missing_disclosure': calc['nonmonetary_review'] = []
    elif mutation == 'duplicate_disclosure': calc['nonmonetary_review'] *= 2
    elif mutation == 'unresolved_disclosure': calc['nonmonetary_review'][0]['treatment'] = 'unresolved'
    elif mutation == 'nil_as_zero': calc['nonmonetary_review'][0]['value'] = 0
    elif mutation == 'mixed_quote': item['evidence_quote'] = 'Net working capital'
    elif mutation == 'old_operation': calc['operation'] = 'sum'
    elif mutation == 'missing_primary': docs.pop(0)
    elif mutation == 'forged_ledger':
        body = json.loads(ledger['text']); body['components'][0]['value_exact'] = '0'
        ledger['text'] = json.dumps(body); ledger['sha256'] = sha256(ledger['text'].encode()).hexdigest()
    else: calc['skip_unknown'] = True
    assert prove(item, docs) is not None


@pytest.mark.parametrize('unit,entity,period', [('USD million', 'Synthetic Industrial Issuer', '2025-12-31'),
    ('EUR million', 'Other Issuer', '2025-12-31'), ('EUR million', 'Synthetic Industrial Issuer', '2024-12-31')])
def test_exact_currency_issuer_and_opening_date_required(unit, entity, period):
    origin, ledger, item = case()
    assert prove(item, [origin, ledger], unit, entity, period) is not None


def test_catalog_presence_requires_coverage_even_if_driver_omits_the_ledger():
    origin, ledger, item = case()
    catalog = {d['id']: d for d in (origin, ledger)}
    narrow = _operating_plan()['model']['opening_nwc']
    assert balance_nwc_selection_problem(narrow, catalog, 'Synthetic Industrial Issuer', '2025-12-31')
    assert balance_nwc_selection_problem(item, catalog, 'Synthetic Industrial Issuer', '2025-12-31') is None
    assert balance_nwc_selection_problem(narrow, {}, 'SYNTH-GROUP', '2025-12-31') is None


def test_compiler_requires_and_preserves_coverage_and_contingency_limit():
    origin, ledger, item = case()
    docs = _documents()+[origin, ledger]; plan = _operating_plan()
    plan['model']['perimeter']['value']['entity'] = 'Synthetic Industrial Issuer'
    result = prepare_method_inputs(_bundle(), documents=docs, propose=lambda *_: plan)
    assert result['status'] == 'incomplete'
    assert any(issue['code'] == 'incomplete_balance_coverage' for issue in result['issues'])
    plan['model']['opening_nwc'] = item; captured = {}
    def propose(dossier, contract):
        captured.update(contract); return plan
    result = prepare_method_inputs(_bundle(), documents=docs, propose=propose)
    assert result['status'] == 'prepared', result['issues']
    assert captured['opening_nwc_structured_policy']['reported_balance_coverage']['operation'] == 'balance_sheet_nwc'
    record = next(r for r in result['proposal']['method_records'] if r['driver'] == 'opening_nwc')
    assert record['value'] == -.002 and 'ANALYST CLASSIFICATION' in record['rationale']
    assert 'nonmonetary_review' in record['rationale'] and 'not a PM approval' in record['rationale']
    proof = result['provenance']['balance_nwc_classification']
    assert proof['calculation'] == item['calculation'] and proof['sha256'] in record['rationale']
    assert len(record['rationale']) < len(json.dumps(proof['calculation']))
    assert proof['origin'] == 'analyst_classification_not_approved'


def test_opening_view_retains_classification_narrative_and_checks_nested_citations():
    from bellomberg.valuation.preparation_ai import verify_visible_citations
    from bellomberg.valuation.preparation_view import select_stage_view
    from test_preparation_view import _dossier
    origin, ledger, item = case(); dossier = _dossier()
    dossier['documents'].extend([origin, ledger]); before = deepcopy(dossier)
    view = select_stage_view(dossier, 'model')
    assert dossier == before and view['stage_view']['structured_triplet_present']
    shown = next(d for d in view['documents'] if d['id'] == origin['id'])
    assert shown['text'] == origin['text']
    verify_visible_citations({'opening_nwc': item}, view, dossier)
    del shown['text']
    with pytest.raises(ValueError, match='non visibile'):
        verify_visible_citations({'opening_nwc': item}, view, dossier)


def test_policy_is_absent_for_existing_catalogs_and_binds_source_identity():
    assert balance_nwc_policy(_documents()) is None
    origin, ledger, _ = case()
    policy = balance_nwc_policy([ledger, origin])
    assert policy['sources'] == [{'id': ledger['id'], 'sha256': ledger['sha256'],
        'entity': 'Synthetic Industrial Issuer', 'report_date': '2025-12-31'}]


@pytest.mark.parametrize('bad', [None, 0, [], {'calculation': []}])
def test_malformed_driver_is_incomplete_without_provenance_exception(bad):
    origin, ledger, _ = case(); plan = _operating_plan()
    plan['model']['opening_nwc'] = bad
    result = prepare_method_inputs(_bundle(), documents=_documents()+[origin, ledger], propose=lambda *_: plan)
    assert result['status'] == 'incomplete'
    assert 'balance_nwc_classification' not in result['provenance']


def test_narrow_opening_stops_before_any_forecast_request():
    from bellomberg.valuation.preparation_ai import StagedProposer
    origin, ledger, _ = case(); docs = _documents()+[origin, ledger]
    plan = _operating_plan(); plan['model']['perimeter']['value']['entity'] = 'Synthetic Industrial Issuer'
    captured = {}
    def capture(dossier, contract):
        captured.update(dossier=dossier, contract=contract)
    prepare_method_inputs(_bundle(), documents=docs, propose=capture)
    calls = []
    def answer(dossier, contract):
        scope = contract['preparation_stage']['scope']; calls.append(scope)
        assert scope == 'model', 'Invalid NWC must stop before a paid forecast'
        return {'drivers': plan['model'], 'rationale': 'Synthetic opening source review'}
    with pytest.raises(ValueError, match='complete reported-balance classifications'):
        StagedProposer(answer)(captured['dossier'], captured['contract'])
    assert calls == ['model']


def test_selected_balance_flows_through_existing_valuation_and_workbook(tmp_path):
    from pathlib import Path
    from bellomberg.valuation.dcf_engine import generate_valuation
    origin, ledger, item = case(); plan = _operating_plan()
    plan['model']['perimeter']['value']['entity'] = 'Synthetic Industrial Issuer'
    plan['model']['opening_nwc'] = item
    result = prepare_method_inputs(_bundle(), documents=_documents()+[origin, ledger], propose=lambda *_: plan)
    assert result['status'] == 'prepared', result['issues']
    valuation = generate_valuation('SYNTH-EXT', prepared_bundle=result['bundle'], output_dir=str(tmp_path))
    assert valuation['valuation_usability']['usable'], valuation.get('acquisition_tasks')
    assert valuation['input_consumption']['status'] == 'complete'
    assert Path(valuation['path']).is_file()
    assert result['proposal']['approval_status'] == 'automatic_non_approved'
