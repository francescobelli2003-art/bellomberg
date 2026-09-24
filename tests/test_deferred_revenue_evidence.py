"""Current customer advances are operating liabilities, not cash or total debt."""
from copy import deepcopy

import pytest

from bellomberg.valuation.input_preparation import _fact_proof, prepare_method_inputs
from test_input_evidence_semantics import _structured_document, _attach_json_fact
from test_input_preparation import _bundle, _documents


def _case(concepts=('DeferredRevenueCurrent',)):
    facts = [{'taxonomy': 'us-gaap', 'concept': 'AccountsReceivableNetCurrent',
              'value': 70_000_000, 'unit': 'EUR', 'end': '2025-12-31'}]
    facts += [{'taxonomy': 'us-gaap', 'concept': concept, 'value': 12_000_000,
               'unit': 'EUR', 'end': '2025-12-31'} for concept in concepts]
    terms = []
    for index, fact in enumerate(facts):
        term = _attach_json_fact({}, value=fact['value'] / 1_000_000, fact_index=index)
        term.pop('value')
        terms.append({'coefficient': 1 if index == 0 else -1, **term})
    return _structured_document(facts), {
        'value': 70 - 12 * len(concepts), 'calculation': {'operation': 'sum', 'terms': terms}}


def test_current_deferred_revenue_has_a_source_proved_negative_sign():
    source, item = _case()
    before = deepcopy((source, item))
    assert _fact_proof('opening_nwc', item, [source], 'EUR million', '2025-12-31') is None
    assert (source, item) == before
    item['calculation']['terms'][1]['coefficient'] = 1
    item['value'] = 82
    assert _fact_proof('opening_nwc', item, [source], 'EUR million', '2025-12-31') is not None


@pytest.mark.parametrize('concept', ['DeferredRevenue', 'DeferredRevenueNoncurrent',
    'ContractWithCustomerLiabilityRevenueRecognized', 'LiabilitiesCurrent'])
def test_total_noncurrent_and_flow_concepts_are_not_current_customer_advances(concept):
    source, item = _case((concept,))
    assert _fact_proof('opening_nwc', item, [source], 'EUR million', '2025-12-31') is not None


def test_two_names_for_customer_liabilities_cannot_be_added_together():
    source, item = _case(('DeferredRevenueCurrent', 'ContractWithCustomerLiabilityCurrent'))
    error = _fact_proof('opening_nwc', item, [source], 'EUR million', '2025-12-31')
    assert 'duplicato' in error


def test_customer_advances_keep_exact_period_and_amount_checks():
    source, item = _case()
    assert _fact_proof('opening_nwc', item, [source], 'EUR million', '2024-12-31') is not None
    item['value'] += 1
    assert _fact_proof('opening_nwc', item, [source], 'EUR million', '2025-12-31') is not None


def test_new_contract_component_is_declared_only_when_present_in_source_catalog():
    source, _ = _case()
    captured = []
    def capture(dossier, contract):
        captured.append(contract)
    prepare_method_inputs(_bundle(), documents=_documents(), propose=capture)
    prepare_method_inputs(_bundle(), documents=_documents() + [source], propose=capture)
    assert len(captured) == 2
    before, after = captured
    old = before['opening_nwc_structured_policy']['components']
    new = after['opening_nwc_structured_policy']['components']
    added = {'taxonomy': 'us-gaap', 'concept': 'DeferredRevenueCurrent', 'coefficient': -1}
    assert added not in old and added in new
    assert [row for row in new if row != added] == old
    after['opening_nwc_structured_policy']['components'] = old
    assert before == after
