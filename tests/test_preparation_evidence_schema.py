"""The provider contract must not invite evidence rejected by the compiler."""
import pytest

from bellomberg.valuation.operating_adapter import SCHEMA
from bellomberg.valuation.preparation_ai import response_format


PROOF_FIELDS = {'evidence_quote', 'quoted_value', 'quoted_unit', 'period_quote',
                'facts', 'evidence_pointer', 'calculation'}


def test_capital_distribution_wire_schema_requires_the_supported_policy():
    import jsonschema
    contract = {'schema': {'capital.distribution_policy': ('parent_ledger', 'text', 'cash', 'future', 'text', 'scenario')},
                'preparation_stage': {'scope': 'bear', 'drivers': ['capital.distribution_policy']}}
    schema = response_format(contract)['json_schema']['schema']
    driver = {'value': 'Hold a fixed quarterly dividend', 'kind': 'analyst_estimate', 'evidence_ids': ['synthetic'],
              'rationale': 'Synthetic policy', 'valid_until': '2026-06-30',
              'valid_until_basis': {'policy': 'same_day', 'as_of': '2026-06-30'}}
    payload = {'drivers': {'capital.distribution_policy': driver}, 'rationale': 'Synthetic'}
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(payload, schema)
    driver['value'] = 'full_sweep_after_buffers'
    jsonschema.validate(payload, schema)


def test_capital_share_count_wire_requires_one_primary_observation_source():
    import jsonschema
    contract = {'schema': {'capital.shares_m': ('share_count', 'number', 'scope', 'opening', 'number', 'scenario')},
                'preparation_stage': {'scope': 'bear', 'drivers': ['capital.shares_m']}}
    schema = response_format(contract)['json_schema']['schema']
    driver = {'value': 10, 'kind': 'historical', 'evidence_ids': ['primary', 'narrative'],
              'rationale': 'Synthetic observed share count', 'valid_until': '2026-06-30',
              'valid_until_basis': {'policy': 'same_day', 'as_of': '2026-06-30'},
              'evidence_pointer': {'value': '/facts/0/value', 'unit': '/facts/0/unit', 'period': '/facts/0/end'},
              'quoted_value': 10000000, 'quoted_unit': 'shares'}
    payload = {'drivers': {'capital.shares_m': driver}, 'rationale': 'Synthetic'}
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(payload, schema)
    driver['evidence_ids'] = ['primary']
    jsonschema.validate(payload, schema)


def branches(name):
    contract = {'schema': {name: SCHEMA[name]},
                'preparation_stage': {'scope': 'model' if SCHEMA[name][-1] == 'model' else 'bear',
                                      'drivers': [name]}}
    schema = response_format(contract)['json_schema']['schema']
    choices = schema['properties']['drivers']['properties'][name]['anyOf']
    assert {'type': 'null'} in choices  # A genuine evidence gap remains representable.
    return [choice for choice in choices if choice.get('type') == 'object']


@pytest.mark.parametrize('name', ['perimeter', 'calendar', 'capdev_amortization_years',
                                  'net_debt', 'wacc', 'revenue_growth', 'terminal_bridge'])
def test_analyst_estimate_wire_contract_excludes_numerical_fact_proofs(name):
    allowed = [row for row in branches(name) if 'analyst_estimate' in row['properties']['kind']['enum']]
    assert allowed
    for row in allowed:
        assert row['properties']['kind']['enum'] == ['analyst_estimate']
        assert not PROOF_FIELDS & row['properties'].keys()
        assert row['additionalProperties'] is False
        assert {'value', 'evidence_ids', 'rationale', 'valid_until', 'valid_until_basis'} <= set(row['required'])


@pytest.mark.parametrize('name', ['historical_revenue', 'opening_nwc', 'shares', 'quotation'])
def test_opening_contract_keeps_exact_fact_proofs(name):
    row, = branches(name)
    assert row['properties']['kind']['enum'] == ['historical']
    assert row['additionalProperties'] is False
    if name == 'quotation':
        assert 'facts' in row['required']
    else:
        assert {'evidence_pointer', 'calculation', 'evidence_quote'} <= row['properties'].keys()


@pytest.mark.parametrize('name', ['wacc', 'net_debt', 'revenue_growth'])
def test_company_guidance_wire_contract_accepts_literal_proofs_only(name):
    allowed = [row for row in branches(name) if 'company_guidance' in row['properties']['kind']['enum']]
    assert len(allowed) == 1
    row = allowed[0]
    assert row['properties']['kind']['enum'] == ['company_guidance']
    assert {'evidence_quote', 'quoted_value', 'quoted_unit', 'period_quote'} <= row['properties'].keys()
    assert not {'calculation', 'evidence_pointer', 'facts'} & row['properties'].keys()
def test_liquidity_forecast_requires_separate_historical_bank_cash_facts():
    from bellomberg.valuation.preparation_ai import response_format
    from bellomberg.valuation.bank_adapter import SCHEMA
    fmt = response_format({'schema': SCHEMA, 'preparation_stage': {'drivers': ['liquidity_bridge']}})
    branches = fmt['json_schema']['schema']['properties']['drivers']['properties']['liquidity_bridge']['anyOf']
    estimate = next(branch for branch in branches if branch.get('properties', {}).get('kind', {}).get('enum') == ['analyst_estimate'])
    assert 'facts' in estimate['required']
    fields = estimate['properties']['facts']['additionalProperties']['properties']
    assert {'evidence_ids', 'evidence_pointer', 'quoted_value', 'quoted_unit', 'period_quote'} <= fields.keys()
    assert 'calculation' not in fields and 'value' not in fields
