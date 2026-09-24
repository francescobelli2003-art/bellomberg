"""Transport rejects unsupported terminal shapes before an economic proposal."""
from copy import deepcopy
import jsonschema
import pytest
from bellomberg.valuation.preparation_ai import response_format
from bellomberg.valuation.bank_adapter import SCHEMA
from test_input_preparation_bank import _terminal_proof


def _schema():
    contract = {'bank_dynamic_capital': True, 'schema': SCHEMA,
                'preparation_stage': {'drivers': ['terminal_ledger']}}
    return response_format(contract)['json_schema']['schema']['properties']['drivers']['properties']['terminal_ledger']['anyOf'][0]['properties']['value']


@pytest.mark.parametrize('invalid', ['entity_map', 'missing_cash_flow', 'two_periods', 'extra_key', 'embedded_tv', 'missing_constraint'])
def test_terminal_transport_accepts_engine_fixture_and_rejects_unsupported_shapes(invalid):
    original = _terminal_proof(10., 8.)
    jsonschema.validate(original, _schema())
    bad = deepcopy(original); cap = bad['capital']
    if invalid == 'entity_map': cap['subsidiaries'] = {s.pop('id'): s for s in cap['subsidiaries']}
    elif invalid == 'missing_cash_flow': cap['parent_cash_flows'].pop('debt_issued')
    elif invalid == 'two_periods': cap['subsidiaries'][0]['gaap_net_income'].append(8.)
    elif invalid == 'extra_key': cap['subsidiaries'][0]['assumed_cash'] = 0
    elif invalid == 'embedded_tv': cap['terminal_equity'] = 100
    else: next(iter(bad['capital_constraints'].values()))['constraints'][0].pop('terminal_requirement')
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(bad, _schema())
    assert _terminal_proof(10., 8.) == original
