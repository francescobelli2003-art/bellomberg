"""Property NAV economics: cash rent, cap rates, recurring capex and debt once."""
from copy import deepcopy
import pytest
from bellomberg.valuation import dcf_engine,sector_analysis
from test_sector_operating_drivers import operating_records,DAY,SPAN

FY='2026-01-01/2026-12-31'


def property_records(occupancy=.9,cap_rate=.1):
    rows=[r for r in operating_records() if r['driver'] in ('perimeter','calendar','quotation','shares')]
    for r in rows:
        if r['period']==SPAN:r['period']='2025-12-31'
        if r['driver']=='calendar':r['value'].update(periods=[],discount_convention='snapshot')
        if r['driver']=='quotation':r['value']['price']=55.
    def add(driver,field,value,unit='contract',basis='GAAP',scenario='model',period=FY):
        rows.append(dict(driver=driver,field=field,value=deepcopy(value),unit=unit,accounting_basis=basis,
            scenario=scenario,period=period,entity='SYNTH-GROUP',source_id='https://example.org/synthetic/property',
            as_of=DAY,valid_until='2027-01-01',kind='analyst_estimate',rationale='Explicit synthetic property case and cap rate convention'))
    add('forward_year','ffo_affo_bridge',{'start':'2026-01-01','end':'2026-12-31'},basis='calendar',period='2025-12-31')
    add('property_scope','property_nav',{'ASSET-A':{'tenure':'freehold','ownership_fraction':1.,'area_unit':'million square metres'}},basis='scope',period='2025-12-31')
    add('policy','property_nav',{'portfolio':'stabilized_no_acquisitions_disposals_or_development',
        'claims':'wholly_owned_ordinary_basic_no_convertibles','rent':'annual_cash_plus_separate_straight_line',
        'cap_rate_basis':'cash_NOI_before_recurring_capex_assuming_backlog_cured',
        'backlog_timing':'paid_from_opening_cash_before_forward_income_no_letting_delay',
        'debt_restrictions':'none_no_covenants_guarantees_or_restricted_cash',
    'central_costs':'exclude_property_opex_interest_tax_and_capex',
        'accrual_basis':'current_cash_costs_equal_expense_exclude_opening_payables_no_new_accruals',
        'tax_basis':'current_corporate_cash_tax_equals_expense_no_deferred_excludes_property_tax_and_opening_payables',
        'central_capitalization':'recurring_corporate_cash_cost_and_current_corporate_tax',
        'ffo_basis':'GAAP_property_depreciation_only_no_other_adjustments',
        'affo_basis':'reconciled_FFO_less_straightline_and_recurring_capex',
        'funding':'documented_FYend_only_pro_rata_existing_shareholders',
        'other_claims':'opening_other_liabilities_fees_tax_payables_paid_at_FYend'},basis='scope',period='2025-12-31')
    add('components','property_debt',{'cash':20.,'debt':50.,'preferred':0.,'other_liabilities':0.,
        'accrued_fees':0.,'distributions_payable':0.,'tax':0.,'equity_adjustments':0.},period='2025-12-31')
    add('debt_schedule','property_debt',{'LOAN-A':{'face_value':50.,'settlement_value':50.,'annual_coupon':.05,
        'maturity':'2030-12-31','basis':'fixed_rate_bullet_full_year_coupon_settlement_at_or_above_par'}},period='2025-12-31')
    noi=100*occupancy-30; ni=noi+4-10-2-2.5;ffo=ni+10;affo=ffo-4-3-1-1
    for sc in ('bear','base','bull'):
        add('property_income','property_income',{'ASSET-A':{'area_m':10.,'annual_rent_per_area':10.,
            'occupancy':occupancy,'other_income':0.,'recoveries':0.,'cash_operating_costs':30.,
            'cash_lease_incentives':0.,'straight_line_rent':4.,'property_depreciation':10.}},scenario=sc)
        add('property_capex','property_capex',{'ASSET-A':{'maintenance':3.,'tenant_improvements':1.,
            'leasing_costs':1.,'expansion':0.,'backlog':5.}},scenario=sc)
        add('property_values','property_nav',{'ASSET-A':{'cap_rate':cap_rate,'stabilized_noi':noi,
            'basis':'cash_NOI_before_recurring_capex_assuming_backlog_cured','comparable_basis':'Explicit synthetic same-tenure cash NOI case'}},scenario=sc)
        add('ffo_bridge','ffo_affo_bridge',{'net_income':ni,'ffo':ffo,'affo':affo,
            'central_cash_cost':2.,'cash_tax':0.,'asset_sale_gains':0.,'property_impairments':0.},scenario=sc)
        add('central_cost_multiple','property_nav',10.,'multiple','valuation',sc)
        add('nav_target','valuation_target',1.,'ratio','valuation',sc)
        add('target_basis','valuation_target','equity_nav','text','valuation',sc)
        add('funding','property_debt',{'minimum_cash':10.,'shareholder_distribution':5.,'refinancing':{},
            'equity_contribution':0.,'equity_commitment_id':'none','equity_available_date':'2026-12-31'},scenario=sc)
    return rows


def property_bundle(rows=None):
    return sector_analysis.prepare_sector_analysis('SYNTH-PROP',as_of=DAY,providers={
        'profile':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'data':{'evidence':[
            {'field':f,'value':v,'source_id':'synthetic','as_of':DAY} for f,v in [('instrument','equity'),('business_model','property_owner')]]}},
        'method_inputs':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'records':deepcopy(property_records() if rows is None else rows)}},
        user_context={'analysis_context':{'scenario_rationale':{s:'Synthetic NAV scenario and forward cash bridge' for s in ('bear','base','bull')}}})


@pytest.mark.parametrize('occupancy,cap_rate,oracle',[(.9,.1,54.5),(.8,.1,44.5),(.9,.12,44.5)])
def test_property_cash_noi_nav_and_affo_independent_oracle(tmp_path,occupancy,cap_rate,oracle):
    result=dcf_engine.generate_valuation('SYNTH-PROP',prepared_bundle=property_bundle(property_records(occupancy,cap_rate)),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==oracle
    detail=result['calculation_details']['scenarios']['base']
    assert detail['cash_noi']==pytest.approx(100*occupancy-30)
    assert detail['affo']==pytest.approx(100*occupancy-39.5)
    assert detail['nav']['components']['debt']==50.


@pytest.mark.parametrize('fault',['missing_capex','stale','unfunded_maturity','intrayear_maturity','debt_double','ffo','caprate','nonstabilized','development','other_entity','nonconsumed'])
def test_property_incomplete_or_incompatible_inputs_cannot_make_nav(tmp_path,fault):
    rows=property_records();get=lambda d:next(r for r in rows if r['driver']==d)
    if fault=='missing_capex':rows.remove(get('property_capex'))
    if fault=='stale':get('property_values')['valid_until']='2020-01-01'
    if fault=='unfunded_maturity':
        get('debt_schedule')['value']['LOAN-A']['maturity']='2026-12-31'
        get('funding')['value']['shareholder_distribution']=40.
    if fault=='intrayear_maturity':get('debt_schedule')['value']['LOAN-A']['maturity']='2026-03-31'
    if fault=='debt_double':get('components')['value']['debt']=100.
    if fault=='ffo':get('ffo_bridge')['value']['ffo']=100.
    if fault=='caprate':get('property_values')['value']['ASSET-A']['cap_rate']=0.
    if fault=='nonstabilized':get('property_values')['value']['ASSET-A']['stabilized_noi']=70.
    if fault=='development':get('property_capex')['value']['ASSET-A']['expansion']=10.
    if fault=='other_entity':get('property_income')['entity']='OTHER'
    if fault=='nonconsumed':get('property_income')['value']['ASSET-A']['silent_growth']=.03
    result=dcf_engine.generate_valuation('SYNTH-PROP',prepared_bundle=property_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    assert result['acquisition_tasks']


def test_maturity_paid_from_actual_cash_has_no_second_nav_deduction(tmp_path):
    rows=property_records();next(r for r in rows if r['driver']=='debt_schedule')['value']['LOAN-A']['maturity']='2026-12-31'
    result=dcf_engine.generate_valuation('SYNTH-PROP',prepared_bundle=property_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==54.5
    assert result['calculation_details']['scenarios']['base']['forward_closing_cash']==pytest.approx(10.5)


def test_dated_committed_refinancing_resolves_only_the_matching_maturity(tmp_path):
    rows=property_records()
    next(r for r in rows if r['driver']=='debt_schedule')['value']['LOAN-A']['maturity']='2026-12-31'
    for row in rows:
        if row['driver']=='funding':
            row['value']['shareholder_distribution']=40.
            row['value']['refinancing']={'LOAN-A':{'amount':50.,'commitment_id':'Synthetic committed facility',
                'available_date':'2026-12-31','new_maturity':'2030-12-31','terms':'committed_at_face_no_fee_FYend_draw'}}
    good=dcf_engine.generate_valuation('SYNTH-PROP',prepared_bundle=property_bundle(rows),output_dir=str(tmp_path))
    assert good['valuation_usability']['usable'],good.get('error')
    assert good['fair_value_base']==54.5
    assert good['calculation_details']['scenarios']['base']['forward_closing_cash']==25.5
    next(r for r in rows if r['driver']=='funding')['value']['refinancing']['LOAN-A']['available_date']='2027-01-01'
    bad=dcf_engine.generate_valuation('SYNTH-PROP',prepared_bundle=property_bundle(rows),output_dir=str(tmp_path))
    assert not bad['valuation_usability']['usable'] and 'rifinanziamento' in bad['error']


@pytest.mark.parametrize('fault',['negative_recurring_cash','scope_bool','own_credit_discount','covenant','forward_calendar'])
def test_property_nav_does_not_hide_economic_or_contract_gaps(tmp_path,fault):
    rows=property_records();get=lambda d:next(r for r in rows if r['driver']==d)['value']
    if fault=='negative_recurring_cash':get('property_capex')['ASSET-A']['maintenance']=100.
    if fault=='scope_bool':get('property_scope')['ASSET-A']['ownership_fraction']=True
    if fault=='own_credit_discount':get('debt_schedule')['LOAN-A']['settlement_value']=30.
    if fault=='covenant':get('policy')['debt_restrictions']='LTV_covenant'
    if fault=='forward_calendar':get('forward_year')['end']='2027-12-31'
    result=dcf_engine.generate_valuation('SYNTH-PROP',prepared_bundle=property_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None


def test_backlog_cure_cannot_be_funded_by_rents_earned_after_the_cure(tmp_path):
    rows=property_records()
    for row in rows:
        if row['driver']=='property_capex':row['value']['ASSET-A']['backlog']=50.
    result=dcf_engine.generate_valuation('SYNTH-PROP',prepared_bundle=property_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable'] and 'backlog opening' in result['error']


def test_current_corporate_tax_and_opening_tax_payable_are_distinct_claims(tmp_path):
    rows=property_records()
    next(r for r in rows if r['driver']=='components')['value']['tax']=5.
    for r in rows:
        if r['driver']=='ffo_bridge':
            r['value']['cash_tax']=1.
            for key in ('net_income','ffo','affo'):r['value'][key]-=1.
    result=dcf_engine.generate_valuation('SYNTH-PROP',prepared_bundle=property_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    # Opening tax claim5 once, recurring corporate tax1 capitalized at explicit10.
    assert result['fair_value_base']==53.
    assert result['calculation_details']['scenarios']['base']['forward_closing_cash']==54.5
