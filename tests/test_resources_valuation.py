"""Finite producing resources: independent cash oracle and reserve/closure limits."""
from copy import deepcopy
import pytest
from bellomberg.valuation import dcf_engine
from test_property_development import developer_records
from test_insurance_valuation import insurance_bundle
from test_sector_operating_drivers import SPAN,DAY


def resource_records():
    rows=[r for r in developer_records() if r['driver'] not in ('policy','projects','project_forecasts')]
    for r in rows:
        if r['driver']=='quotation':r['value']['price']=9.
        if r['field']=='project_funding':r['field']='enterprise_equity_bridge'
        if r['field']=='project_cash_flows':r['field']='resource_costs'
        if r['driver']=='opening_cash':r['value']=30.
        if r['driver']=='debt_schedule':r['value']={'no_debt':True}
        if r['driver']=='expected_tax_shield':r['value']=[0.,0.]
        if r['driver']=='funding':r['value']={'capacities':[0.,10.],'commitments':{
            '1':{'id':'Synthetic second production year operating funds','available_date':'2027-01-01',
                'terms':'draw_when_needed_existing_shareholders_no_new_shares'}}}
    from bellomberg.valuation.resources_adapter import POLICY
    def add(driver,field,value,scenario='model',basis='resource',period=SPAN):
        rows.append(dict(driver=driver,field=field,value=deepcopy(value),unit='contract',accounting_basis=basis,
            scenario=scenario,period=period,entity='SYNTH-GROUP',source_id='https://example.org/synthetic/reserve-engineer-and-tax-case',
            as_of=DAY,valid_until='2027-01-01',kind='analyst_estimate',rationale='Explicit synthetic extraction, cost and closure case'))
    add('policy','enterprise_equity_bridge',POLICY,basis='scope')
    add('assets','reserves_resources',{'FIELD-A':{'recoverable_reserves':15.,'opening_annual_output':10.,
        'reserve_class':'proved_economically_recoverable','reserve_standard':'Synthetic engineering report standard',
        'reserve_report':'Synthetic independent engineer remaining gross saleable reserves',
        'volume_unit':'million_barrels','price_unit':'EUR_per_barrel','rights':'wholly_owned_unconditional_producing',
        'rights_expiry':'2027-12-31','taxpayer_entity':'SYNTH-GROUP','tax_jurisdiction':'Synthetic jurisdiction',
        'opening_tax_basis':20.,'tax_source':'Synthetic capital allowance schedule',
        'residual_policy':'abandoned_without_proceeds_or_future_recovery'}},period='2025-12-31')
    for sc in ('bear','base','bull'):
        add('forecasts','production_prices',{'FIELD-A':{'decline':[0.,.5],'price':[10.,10.],
            'unit_cash_cost':[2.,2.],'fixed_cash_cost':[0.,0.],'royalty_rate':[.1,.1],
            'capex':[10.,0.],'tax_allowance':[20.,10.]}},sc)
        add('closure','closure_obligations',{'FIELD-A':{'period':2,'cash_cost':5.,'remaining_abandoned':0.,
            'obligations':'all_restoration_paid_at_closure_no_other_claims','tax':'deductible_when_paid',
            'cost_source':'Synthetic full restoration estimate'}},sc)
    return rows


def resource_bundle(rows=None):
    return insurance_bundle(resource_records() if rows is None else rows,profile='resources')


def generate(tmp_path,rows=None):
    return dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=resource_bundle(rows),output_dir=str(tmp_path))


def test_extraction_depletion_royalty_and_closure_match_independent_cash_oracle(tmp_path):
    result=generate(tmp_path)
    assert result['valuation_usability']['usable'],result.get('error')
    # First outlay30 uses cash30. Year1 proceeds90 less tax10; year2 startcost10,
    # final proceeds45 less restoration5 and tax4. No debt or artificial terminal.
    expected=(80.-10.)/1.1+36./1.21
    assert result['fair_value_base']==round(expected/10,2)
    calc=result['calculation_details']['scenarios']['base']
    assert calc['terminal_value']==0.
    assert [r['production'] for r in calc['assets']['FIELD-A']['rows']]==[10.,5.]
    assert calc['assets']['FIELD-A']['remaining_reserves']==0.


@pytest.mark.parametrize('closure,cash_capacity',[(5.,10.),(30.,17.),(30.,16.99)])
def test_resource_debt_and_late_restoration_funding_reconcile_to_equity(tmp_path,closure,cash_capacity):
    rows=resource_records()
    for r in rows:
        if r['driver']=='debt_schedule':r['value']={'LOAN-A':{'face_value':20.,'settlement_value':20.,
            'annual_coupon':.1,'maturity':'2027-12-31','basis':'fixed_rate_bullet_full_year_coupon_settlement_at_or_above_par'}}
        if r['driver']=='expected_tax_shield':r['value']=[.4,.4 if closure==5. else 0.]
        if r['driver']=='closure':r['value']['FIELD-A']['cash_cost']=closure
        if r['driver']=='funding':r['value']['capacities'][1]=cash_capacity
    result=generate(tmp_path,rows)
    if cash_capacity==16.99:
        assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    else:
        assert result['valuation_usability']['usable'],result.get('error')
        expected=68.4/1.1+(14.4 if closure==5. else -7.)/1.21
        assert result['fair_value_base']==round(expected/10,2)


@pytest.mark.parametrize('fault',['reserves_missing','overdraw','expiry','late_closure','missing_closure','unfunded',
    'tax_overdraw','remaining_basis','wrong_unit','wrong_taxpayer','stale','extra','uneconomic','closure_double_tax','restricted_cash'])
def test_resource_incomplete_or_inconsistent_source_case_returns_no_fair_value(tmp_path,fault):
    rows=resource_records();get=lambda d:next(r for r in rows if r['driver']==d)
    a=get('assets')['value']['FIELD-A'];f=get('forecasts')['value']['FIELD-A'];c=get('closure')['value']['FIELD-A']
    if fault=='reserves_missing':a.pop('recoverable_reserves')
    if fault=='overdraw':a['recoverable_reserves']=14.
    if fault=='expiry':a['rights_expiry']='2026-12-31'
    if fault=='late_closure':c['period']=3
    if fault=='missing_closure':rows.remove(get('closure'))
    if fault=='unfunded':get('opening_cash')['value']=29.
    if fault=='tax_overdraw':f['tax_allowance']=[31.,0.]
    if fault=='remaining_basis':f['tax_allowance']=[10.,10.]
    if fault=='wrong_unit':a['price_unit']='USD_per_barrel'
    if fault=='wrong_taxpayer':a['taxpayer_entity']='OUTSIDE'
    if fault=='stale':get('forecasts')['valid_until']='2020-01-01'
    if fault=='extra':f['reserve_growth_default']=.02
    if fault=='uneconomic':f['price']=[1.,1.]
    if fault=='closure_double_tax':get('policy')['value']['restoration']='also_capitalized_in_tax_basis'
    if fault=='restricted_cash':get('policy')['value']['cash_basis']='includes_restoration_escrow'
    result=generate(tmp_path,rows)
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    assert result['acquisition_tasks']


@pytest.mark.parametrize('shock',['price','volume','capex','closure'])
def test_resource_economic_sensitivities_use_independent_oracles(tmp_path,shock):
    rows=resource_records()
    for r in rows:
        if r['driver']=='forecasts':
            f=r['value']['FIELD-A']
            if shock=='price':f['price']=[12.,12.]
            if shock=='volume':f['decline']=[0.,.7]
            if shock=='capex':f.update(capex=[20.,0.],tax_allowance=[25.,15.])
        if r['driver']=='closure':
            if shock=='volume':r['value']['FIELD-A']['remaining_abandoned']=2.
            if shock=='closure':r['value']['FIELD-A']['cash_cost']=10.
        if shock=='capex' and r['driver']=='funding':
            r['value']['capacities'][0]=10.
            r['value']['commitments']['0']={'id':'Synthetic extra sustaining investment funding',
                'available_date':'2026-01-01','terms':'draw_when_needed_existing_shareholders_no_new_shares'}
    expected={'price':84.4/1.1+43.2/1.21,'volume':74./1.1+20.8/1.21,
              'capex':-10.+71./1.1+37./1.21,'closure':70./1.1+32./1.21}[shock]
    result=generate(tmp_path,rows)
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round(expected/10,2)
