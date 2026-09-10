"""Recognized RAB, cash investment and financing are separate documented drivers."""
from copy import deepcopy
import pytest
from bellomberg.valuation import dcf_engine,sector_analysis
from test_sector_operating_drivers import operating_records,DAY,SPAN


def rab_records():
    rows=[r for r in operating_records() if r['driver'] in ('perimeter','calendar','quotation','shares')]
    def add(driver,field,value,unit='EUR million',basis='regulatory',scenario='model',period=SPAN):
        rows.append(dict(driver=driver,field=field,value=deepcopy(value),unit=unit,accounting_basis=basis,
            scenario=scenario,period=period,entity='SYNTH-GROUP',source_id='https://example.org/synthetic/regulator',
            as_of=DAY,valid_until='2027-01-01',kind='analyst_estimate',rationale='Synthetic recognized base and financing case'))
    add('regime','regulatory_regime',{'regulator':'SYNTH-REGULATOR','jurisdiction':'SYNTHETIC',
        'decision_id':'Synthetic decision','review_start':'2026-01-01','review_end':'2028-12-31',
        'return_basis':'nominal','tax_basis':'pretax','return_base':'opening_recognized'},'contract','scope')
    add('opening_rab','recognized_regulatory_base',100.,period='2025-12-31')
    add('opening_unrecognized_investment','network_investment',0.,period='2025-12-31')
    add('claims','cash_distributions',{'debt_basis':'all_interest_bearing_claims',
        'equity_basis':'ordinary_only_no_other_senior_or_minority_claims',
        'distribution_policy':'full_sweep_after_minimum_cash','ownership_policy':'pro_rata_existing_shareholders'},'contract','scope')
    for key,value in [('opening_debt',20.),('opening_cash',5.),('opening_working_capital',0.)]:
        add(key,'cash_distributions',value,basis='cash',period='2025-12-31')
    for scenario in ('bear','base','bull'):
        add('ke','discount_rate_inputs',.1,'ratio','valuation',scenario)
        add('terminal_growth','terminal_assumptions',0.,'ratio','valuation',scenario)
        for driver,value in {'allowed_return':.1,'indexation':0.,'recognized_capex':5.,'regulatory_depreciation':5.,
            'disposals_rab':0.,'disposal_realization_multiple':1.,'cash_capex':5.,'book_depreciation':5.,'allowed_opex':10.,'cash_opex':10.,
            'incentives':0.,'tax_allowance':0.,'cash_tax':0.,'interest_paid':1.,'interest_received':0.,
            'working_capital_change':0.,'disposal_cash':0.,'debt_issued':0.,'debt_repaid':0.,
            'minimum_cash':5.,'permitted_distribution':9.,'funding_capacity':0.}.items():
            field='allowed_return' if driver in ('allowed_return','indexation') else 'network_investment' if driver in ('recognized_capex','regulatory_depreciation','disposals_rab','cash_capex','book_depreciation','disposal_realization_multiple') else 'cash_distributions'
            unit='ratio' if driver in ('allowed_return','indexation','disposal_realization_multiple') else 'EUR million'
            add(driver,field,[value,value],unit,'regulatory' if field in ('allowed_return','network_investment') else 'cash',scenario)
        add('continuing','terminal_assumptions',{r['driver']:r['value'][-1] for r in rows if r['scenario']==scenario and isinstance(r['value'],list)},'contract','regulatory',scenario,period='2027-12-31')
    return rows


def rab_bundle(rows=None):
    return sector_analysis.prepare_sector_analysis('SYNTH-RAB',as_of=DAY,providers={
        'profile':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'data':{'evidence':[
            {'field':f,'value':v,'source_id':'synthetic','as_of':DAY} for f,v in [('instrument','equity'),
            ('business_model','regulated_network'),('regulatory_model','rab'),('regulator','SYNTH-REGULATOR')]]}},
        'method_inputs':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'records':deepcopy(rab_records() if rows is None else rows)}},
        user_context={'analysis_context':{'scenario_rationale':{s:'Explicit synthetic regulated cash case' for s in ('bear','base','bull')}}})


def test_regulated_cash_value_has_no_payout_or_unfunded_capex(tmp_path):
    result=dcf_engine.generate_valuation('SYNTH-RAB',prepared_bundle=rab_bundle(),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    # Cash9 each year, perpetuity9/.1, ten shares =>9. No second cash/debt bridge.
    assert result['fair_value_base']==9.
    assert result['calculation_details']['scenarios']['base']['rows'][0]['closing_rab']==100.


@pytest.mark.parametrize('fault',['missing','stale','regulator','vanilla','cash_capex','funding','terminal','extra','indexation','working_capital','disposal','tax_credit'])
def test_rab_holes_and_incompatible_regimes_are_acquisitions(tmp_path,fault):
    rows=rab_records();get=lambda d:next(r for r in rows if r['driver']==d and r['scenario']=='base')
    if fault=='missing':rows.remove(get('cash_capex'))
    if fault=='stale':next(r for r in rows if r['driver']=='regime')['valid_until']='2020-01-01'
    if fault=='regulator':next(r for r in rows if r['driver']=='regime')['value']['regulator']='OTHER'
    if fault=='vanilla':next(r for r in rows if r['driver']=='regime')['value']['tax_basis']='vanilla'
    if fault=='cash_capex':get('cash_capex')['value']=[100.,100.]
    if fault=='funding':get('debt_repaid')['value']=[21.,0.]
    if fault=='terminal':get('continuing')['value']['recognized_capex']=10.
    if fault=='extra':get('continuing')['value']['payout']=.5
    if fault=='indexation':get('indexation')['value']=[.02,.02]
    if fault=='working_capital':get('continuing')['value']['working_capital_change']=-1.
    if fault=='disposal':get('continuing')['value']['disposal_cash']=1.
    if fault=='tax_credit':get('continuing')['value']['cash_tax']=-1.
    result=dcf_engine.generate_valuation('SYNTH-RAB',prepared_bundle=rab_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert result.get('fair_value_base') is None and result['acquisition_tasks']


def test_cash_capex_sensitivity_does_not_change_recognized_asset_base(tmp_path):
    rows=rab_records()
    next(r for r in rows if r['driver']=='cash_capex' and r['scenario']=='base')['value']=[7.,5.]
    result=dcf_engine.generate_valuation('SYNTH-RAB',prepared_bundle=rab_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round((7/1.1+99/1.21)/10,2)
    assert result['calculation_details']['scenarios']['base']['rows'][0]['closing_rab']==100.


def test_real_return_on_indexed_base_has_nominal_cash_without_double_inflation(tmp_path):
    rows=rab_records()
    next(r for r in rows if r['driver']=='regime')['value']['return_basis']='real'
    for row in rows:
        if row['scenario']!='base':continue
        if row['driver']=='indexation':row['value']=[.02,.02]
        if row['driver']=='terminal_growth':row['value']=.02
        if row['driver']=='permitted_distribution':row['value']=[10.,10.]
        if row['driver']=='continuing':row['value'].update(indexation=.02,debt_issued=.4,minimum_cash=5.1,permitted_distribution=10.)
    result=dcf_engine.generate_valuation('SYNTH-RAB',prepared_bundle=rab_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round((9/1.1+(9.2+9.704/.08)/1.21)/10,2)
    assert result['calculation_details']['scenarios']['base']['rows'][1]['closing_rab']==pytest.approx(104.04)


@pytest.mark.parametrize('period',['forecast','continuing'])
def test_recognized_investment_cannot_be_funded_by_an_implicit_paid_stock(tmp_path,period):
    rows=rab_records()
    for row in rows:
        if period=='forecast' and row['driver']=='cash_capex':row['value']=[0.,0.]
        if period=='forecast' and row['driver']=='permitted_distribution':row['value']=[14.,14.]
        if period=='continuing' and row['driver']=='continuing':row['value'].update(cash_capex=0.,permitted_distribution=14.)
    result=dcf_engine.generate_valuation('SYNTH-RAB',prepared_bundle=rab_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert any(t['field']=='network_investment' for t in result['acquisition_tasks'])
