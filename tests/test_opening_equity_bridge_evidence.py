"""Scenario scope does not turn opening debt/equity claims into forecasts."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.dcf_engine import generate_valuation
from bellomberg.valuation.input_preparation import prepare_method_inputs
from test_input_preparation import _bundle, _documents, _operating_plan
from test_preparation_evidence_schema import branches
from test_sector_analysis import DAY


def case(*, end='2025-12-31', entity='SYNTH-GROUP', unit='EUR million', quoted=12.):
    facts = [{'value':v, 'unit':unit, 'end':end, 'entity':entity, 'concept':name}
             for v,name in [(12.,'DebtLongtermAndShorttermCombinedAmount'),
                            (3.,'CashAndCashEquivalentsAtCarryingValue'),(2.,'MinorityInterest')]]
    text = json.dumps({'entity':entity, 'facts':facts})
    doc = {'id':'opening-bridge','url':'https://example.org/issuer/opening-balance',
           'published_at':DAY,'text':text,'sha256':sha256(text.encode()).hexdigest()}
    plan = _operating_plan()
    def term(index, sign):
        return {'coefficient':sign,'evidence_ids':['opening-bridge'],
            'evidence_pointer':{'value':f'/facts/{index}/value', 'unit':f'/facts/{index}/unit', 'period':f'/facts/{index}/end'},
            'quoted_value':quoted if index==0 else facts[index]['value'],'quoted_unit':unit}
    for scenario in plan['scenarios'].values():
        scenario['net_debt'].update(value=9.,kind='historical',evidence_ids=['opening-bridge'],
            rationale='Observed debt less cash at the common opening date, not forecast terminal debt.',
            calculation={'operation':'sum','terms':[term(0,1),term(1,-1)]})
        scenario['equity_adjustments'].update(value=2.,kind='historical',evidence_ids=['opening-bridge'],
            rationale='Synthetic observed opening equity claim, held in this scenario scope.',
            evidence_pointer=term(2,1)['evidence_pointer'],quoted_value=2.,quoted_unit=unit)
    return _documents()+[doc],plan


@pytest.mark.parametrize('driver', ['net_debt','equity_adjustments'])
def test_wire_contract_accepts_historical_opening_claims_inside_a_scenario(driver):
    historical = [b for b in branches(driver) if b['properties']['kind']['enum']==['historical']]
    assert len(historical)==1
    assert {'calculation','evidence_pointer','quoted_value','quoted_unit'} <= historical[0]['properties'].keys()


def test_opening_bridge_compiles_and_generates_without_changing_legacy_record_period(tmp_path):
    documents,plan=case()
    prepared=prepare_method_inputs(_bundle(),documents=documents,propose=lambda *a:plan)
    assert prepared['status']=='prepared',prepared['issues']
    records=[r for r in prepared['proposal']['method_records'] if r['driver'] in ('net_debt','equity_adjustments')]
    assert len(records)==6 and all(r['kind']=='historical' for r in records)
    assert all(r['period'].startswith('2026-01-01/2026-12-31|') for r in records)
    result=generate_valuation('SYNTH-EXT',prepared_bundle=prepared['bundle'],output_dir=tmp_path)
    assert result['valuation_usability']['usable'],result['acquisition_tasks']


@pytest.mark.parametrize('fault', [{'end':'2026-12-31'}, {'entity':'OTHER'}, {'unit':'USD million'}, {'quoted':99.}])
def test_opening_bridge_rejects_forecast_date_other_entity_currency_and_wrong_arithmetic(fault):
    documents,plan=case(**fault)
    prepared=prepare_method_inputs(_bundle(),documents=documents,propose=lambda *a:plan)
    assert prepared['status']=='incomplete'
    assert any(i['code']=='unverified_fact' and i['field'] in ('net_debt','equity_adjustments') for i in prepared['issues'])


@pytest.mark.parametrize('driver',['wacc','terminal_growth','terminal_ronic','revenue_growth'])
def test_true_forecasts_still_cannot_be_relabelled_historical(driver):
    documents,plan=case()
    plan['scenarios']['base'][driver]['kind']='historical'
    prepared=prepare_method_inputs(_bundle(),documents=documents,propose=lambda *a:plan)
    assert any(i['code']=='historical_forecast' and i['field']==driver for i in prepared['issues'])


def test_existing_estimated_opening_bridges_remain_compatible():
    plan=_operating_plan()
    prepared=prepare_method_inputs(_bundle(),documents=_documents(),propose=lambda *a:deepcopy(plan))
    assert prepared['status']=='prepared',prepared['issues']
