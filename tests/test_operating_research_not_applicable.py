"""An explicit no-capitalized-research policy needs no invented useful life."""
from copy import deepcopy

import pytest

from bellomberg.valuation.dcf_engine import generate_valuation
from bellomberg.valuation.dcf_buyside_v3 import _scenario_numbers
from tests.test_sector_operating_drivers import bundle_for, operating_records


def records_with_life(value):
    records = operating_records()
    row = next(row for row in records if row['driver'] == 'capdev_amortization_years')
    row['value'] = value
    row['rationale'] = ('Explicit not applicable: all research is expensed, with no '
                        'capitalized development or opening research amortization.')
    return records


def test_not_applicable_preserves_all_cash_flows_and_value(tmp_path):
    positive = generate_valuation('SYNTH-EXT', prepared_bundle=bundle_for(),
                                  output_dir=str(tmp_path / 'positive'))
    absent = generate_valuation('SYNTH-EXT', prepared_bundle=bundle_for(records_with_life(0)),
                               output_dir=str(tmp_path / 'absent'))
    assert absent['valuation_usability']['usable'], absent['valuation_usability']
    assert absent['calculation_details'] == positive['calculation_details']
    for scenario in ('bear', 'base', 'bull'):
        assert absent[f'fair_value_{scenario}'] == positive[f'fair_value_{scenario}']


@pytest.mark.parametrize('scenario', ['bear', 'base', 'bull'])
@pytest.mark.parametrize('driver', ['capdev_pct', 'opening_intangible_amortization'])
def test_not_applicable_rejects_any_capitalization_or_opening_runoff(tmp_path, scenario, driver):
    records = records_with_life(0)
    row = next(row for row in records if row['driver'] == driver and row['scenario'] == scenario)
    row['value'][-1] = .01
    result = generate_valuation('SYNTH-EXT', prepared_bundle=bundle_for(records), output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert result.get('fair_value_base') is None
    assert 'capdev_amortization_years' in str(result['acquisition_tasks'])


@pytest.mark.parametrize('life', [-1, .5, True, None])
def test_not_applicable_does_not_relax_integer_or_missing_input_checks(tmp_path, life):
    result = generate_valuation('SYNTH-EXT', prepared_bundle=bundle_for(records_with_life(life)),
                               output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']


@pytest.mark.parametrize('missing', ['source_id', 'rationale'])
def test_zero_still_requires_documented_evidence(tmp_path, missing):
    records = records_with_life(0)
    next(row for row in records if row['driver'] == 'capdev_amortization_years')[missing] = ''
    result = generate_valuation('SYNTH-EXT', prepared_bundle=bundle_for(records), output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']


@pytest.mark.parametrize('driver', [None, 'capdev_pct', 'opening_intangible_amortization'])
def test_numeric_engine_checks_not_applicable_before_computing(driver):
    records = operating_records()
    scenario = {row['driver']: deepcopy(row['value']) for row in records if row['scenario'] == 'base'}
    spec = {'documented_inputs': True, 'capdev_amortization_years': 0}
    if driver:
        scenario[driver][-1] = .01
        with pytest.raises(ValueError, match='not applicable'):
            _scenario_numbers(spec, scenario, 100., nwc0=0.)
    else:
        rows = _scenario_numbers(spec, scenario, 100., nwc0=0.)
        assert rows['research_amortization'] == [0., 0.]
        assert rows['ufcf'] == [10., 10.]


@pytest.mark.parametrize('driver,life,expected_calls', [
    ('capdev_amortization_years', -1, 5),
    ('capdev_pct', 0, 8), ('opening_intangible_amortization', 0, 6)])
def test_invalid_research_policy_stops_before_next_paid_stage(driver, life, expected_calls):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.preparation_ai import StagedProposer
    from tests.test_input_preparation import _bundle, _documents, _operating_plan
    plan, calls = _operating_plan(), []
    plan['model']['capdev_amortization_years']['value'] = life
    if driver != 'capdev_amortization_years':
        plan['scenarios']['bear'][driver]['value'][-1] = .01

    def propose(dossier, contract):
        stage = contract['preparation_stage']
        calls.append(stage)
        assert len(calls) <= expected_calls, 'paid next stage must not start'
        source = plan['model'] if stage['scope'] == 'model' else plan['scenarios'][stage['scope']]
        return {'drivers': {key: deepcopy(source[key]) for key in stage['drivers']},
                'rationale': 'Synthetic explicitly inconsistent research policy'}

    result = prepare_method_inputs(_bundle(), documents=_documents(),
        propose=StagedProposer(propose, opening_drivers_per_stage=1))
    assert result['status'] == 'incomplete'
    assert len(calls) == expected_calls
    assert 'capdev_amortization_years' in str(result['issues'])
