"""Finite project APV: construction timing, tax inventory and real funding capacity."""
from copy import deepcopy
import pytest
from bellomberg.valuation import dcf_engine
from test_insurance_valuation import insurance_bundle
from test_sector_operating_drivers import operating_records,DAY,SPAN


def developer_records(*,capitalize=False,minimum=0.):
    rows=[r for r in operating_records() if r['driver'] in ('perimeter','calendar','quotation','shares')]
    for r in rows:
        if r['driver']=='quotation':r['value']['price']=4.
    def add(driver,field,value,unit='contract',basis='tax',scenario='model',period=SPAN):
        rows.append(dict(driver=driver,field=field,value=deepcopy(value),unit=unit,accounting_basis=basis,
            scenario=scenario,period=period,entity='SYNTH-GROUP',source_id='https://example.org/synthetic/project-tax-and-funding',
            as_of=DAY,valid_until='2027-01-01',kind='analyst_estimate',rationale='Explicit synthetic finite asset, fiscal and funding case'))
    add('policy','project_funding',{'ownership':'wholly_owned_ordinary_basic_pro_rata_existing_shareholders',
        'business':'finite_projects_all_sold_no_other_assets_or_claims',
        'timing':'construction_and_opex_at_start_completion_and_delivery_at_end_no_lag',
        'tax':'current_cash_tax_no_NOL_refund_limits_or_deferred_tax',
        'tax_group':'single_taxpayer_single_jurisdiction_full_current_project_profit_loss_offset',
        'interest':'documented_tax_capitalization_allocations_residual_current_deduction',
        'debt':'existing_fixed_rate_bullet_no_new_borrowing_fees_covenants_or_guarantees',
        'financing_cost_pv':'expected_after_tax_deadweight_cost_at_valuation_date_excluded_from_Ku_and_cashflows',
        'expected_shield':'source_estimated_expected_cash_tax_benefit_not_engine_default_risk_estimate',
        'cash_policy':'full_sweep_after_operating_buffer_release_at_finite_end'},basis='scope')
    add('projects','project_inventory',{'PROJECT-A':{'units_total':10,'opening_unlevered_tax_basis':40.,
        'opening_tax_basis':40.,'opening_receivable':0.,'title':'wholly_owned_freehold',
        'permits':'unconditional_approved','unit_basis':'whole_saleable_lots',
        'price_basis':'financial_currency_million_per_lot','prepayments':'none',
        'tax_source':'Synthetic statutory inventory and borrowing-cost treatment',
        'taxpayer_entity':'SYNTH-GROUP','tax_jurisdiction':'Synthetic single tax jurisdiction'}},basis='tax',period='2025-12-31')
    add('opening_cash','project_funding',20.,'EUR million','cash',period='2025-12-31')
    add('opening_cash_buffer','project_funding',minimum,'EUR million','cash',period='2025-12-31')
    add('debt_schedule','project_funding',{'LOAN-A':{'face_value':40.,'settlement_value':40.,'annual_coupon':.1,
        'maturity':'2027-12-31','basis':'fixed_rate_bullet_full_year_coupon_settlement_at_or_above_par'}},period='2025-12-31')
    for sc in ('bear','base','bull'):
        add('project_forecasts','project_cash_flows',{'PROJECT-A':{'remaining_construction_budget':60.,
            'completion_period':1,'construction_cash':[60.,0.],'units_delivered':[0,10],
            'price_per_lot':[15.,15.],'operating_cash_cost':[0.,0.],'closing_receivable':[0.,0.],
            'tax_capitalized_interest':[4. if capitalize else 0.,0.]}},scenario=sc)
        add('central_cash_cost','project_cash_flows',[0.,0.],'EUR million','tax',sc)
        add('tax_rate','project_cash_flows',[.2,.2],'ratio','tax',sc)
        add('minimum_cash','project_funding',[minimum,minimum],'EUR million','cash',sc)
        add('funding','project_funding',{'capacities':[44.+minimum,0.],
            'commitments':{'0':{'id':'Synthetic irrevocable pro-rata commitment',
                'available_date':'2026-01-01','terms':'draw_when_needed_existing_shareholders_no_new_shares'}}},scenario=sc)
        add('ku','discount_rate_inputs',.1,'ratio','valuation',sc)
        add('tax_shield_discount_rate','discount_rate_inputs',.1,'ratio','valuation',sc)
        add('expected_tax_shield','project_funding',[0.,1.6 if capitalize else .8],'EUR million','cash',sc)
        add('financing_cost_pv','project_funding',0.,'EUR million','valuation',sc)
    return rows


def developer_bundle(rows=None):
    return insurance_bundle(developer_records() if rows is None else rows,profile='property_developer')


@pytest.mark.parametrize('capitalize,minimum',[(False,0.),(True,0.),(False,5.)])
def test_finite_property_apv_equals_independent_timed_equity_cash_oracle(tmp_path,capitalize,minimum):
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=developer_bundle(developer_records(capitalize=capitalize,minimum=minimum)),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    ending=97.6 if capitalize else 96.8
    equity_pv=-(40+minimum)-4/1.1+(ending+minimum)/1.21
    assert result['fair_value_base']==round(equity_pv/10,2)
    calc=result['calculation_details']['scenarios']['base']
    assert calc['terminal_value']==0.
    assert calc['ledger'][0]['funding_at_start']==pytest.approx(40+minimum)
    assert calc['ledger'][0]['funding_at_end']==pytest.approx(4.)
    assert calc['projects']['PROJECT-A']['closing_inventory']==pytest.approx(0.)
    assert calc['projects']['PROJECT-A']['closing_receivable']==pytest.approx(0.)


@pytest.mark.parametrize('fault',['construction_unfunded','missing_budget','unsold_units','early_sale','ar_uncollected','missing_tax',
    'excess_shield','capitalized_twice','late_commitment','stale','wrong_entity','extra'])
def test_finite_property_missing_or_inconsistent_inputs_produce_acquisitions(tmp_path,fault):
    rows=developer_records();get=lambda d:next(r for r in rows if r['driver']==d and r['scenario']=='base')
    f=get('project_forecasts')['value']['PROJECT-A']
    if fault=='construction_unfunded':get('funding')['value']['capacities']=[39.,0.]
    if fault=='missing_budget':f.pop('remaining_construction_budget')
    if fault=='unsold_units':f['units_delivered']=[0,9]
    if fault=='early_sale':f.update(completion_period=2,construction_cash=[30.,30.],units_delivered=[1,9])
    if fault=='ar_uncollected':f['closing_receivable']=[0.,10.]
    if fault=='missing_tax':rows.remove(get('tax_rate'))
    if fault=='excess_shield':get('expected_tax_shield')['value']=[1.,1.]
    if fault=='capitalized_twice':f['tax_capitalized_interest']=[8.,0.]
    if fault=='late_commitment':get('funding')['value']['commitments']['0']['available_date']='2026-12-31'
    if fault=='stale':get('project_forecasts')['valid_until']='2020-01-01'
    if fault=='wrong_entity':get('project_forecasts')['entity']='WRONG'
    if fault=='extra':f['silent_terminal_growth']=.02
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=developer_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    assert result['acquisition_tasks']


@pytest.mark.parametrize('shock',['price','cost','delay','delay_actual'])
def test_finite_project_price_cost_and_timing_sensitivities_have_independent_oracles(tmp_path,shock):
    rows=developer_records(); end=104.8 if shock=='price' else 98.8
    for row in rows:
        d=row['driver']
        if shock in ('price','cost'):
            if d=='project_forecasts':
                f=row['value']['PROJECT-A']
                if shock=='price':f['price_per_lot']=[16.,16.]
                else:f.update(remaining_construction_budget=70.,construction_cash=[70.,0.])
            if shock=='cost' and d=='funding':row['value']['capacities']=[54.,0.]
        else:
            if row['period']==SPAN:row['period']=SPAN+'|2028-01-01/2028-12-31'
            if d=='calendar':row['value']['periods'].append({'start':'2028-01-01','end':'2028-12-31'})
            if d=='project_forecasts':row['value']['PROJECT-A'].update(completion_period=2,
                construction_cash=[30.,30.,0.],units_delivered=[0,0,10],price_per_lot=[15.,15.,15.],
                operating_cash_cost=[0.,0.,0.],closing_receivable=[0.,0.,0.],tax_capitalized_interest=[0.,0.,0.])
            if d in ('central_cash_cost','minimum_cash','expected_tax_shield'):row['value']=[0.,0.,0.]
            if d=='tax_rate':row['value']=[.2,.2,.2]
            if d=='funding':row['value']={'capacities':[14.,74.,0.],'commitments':{
                str(i):{'id':'Synthetic phase funding '+str(i),'available_date':str(2026+i)+'-01-01',
                    'terms':'draw_when_needed_existing_shareholders_no_new_shares'} for i in range(2)}}
    expected=(-40-4/1.1+104.8/1.21) if shock=='price' else (-50-4/1.1+98.8/1.21) if shock=='cost' else (-10-34/1.1-44/1.21+140/1.1**3)
    if shock=='delay_actual':
        for row in rows:
            if row['driver']=='calendar':row['value']['discount_convention']='ACT/365F'
            if row['driver']=='project_forecasts':row['value']['PROJECT-A'].update(
                completion_period=3,construction_cash=[30.,20.,10.])
            if row['driver']=='funding':
                row['value']['capacities']=[14.,64.,10.]
                row['value']['commitments']['2']={'id':'Synthetic final construction funding',
                    'available_date':'2028-01-01','terms':'draw_when_needed_existing_shareholders_no_new_shares'}
        from datetime import date
        delivery_time=(date(2028,12,31)-date(2025,12,31)).days/365
        expected=-10-24/1.1-54/1.21+140/1.1**delivery_time
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=developer_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round(expected/10,2)


def test_zero_terminal_uses_the_existing_fcff_primitive_without_gordon_or_growth_input():
    from bellomberg.valuation.dcf_buyside_v3 import _dcf_value
    result=_dcf_value({'documented_inputs':True,'discount_periods':[0.,2.],'shares_m':10.,
        'net_debt':0.,'equity_adjustments':[]},[-60.,140.],.1,None,terminal_value=0.)
    assert result==pytest.approx((-60+140/1.21)/10)


@pytest.mark.parametrize('case',['buffer_down','opening_excess'])
def test_apv_and_actual_ledger_release_cash_at_the_same_start_boundary(tmp_path,case):
    rows=developer_records(minimum=5. if case=='buffer_down' else 0.)
    for row in rows:
        if case=='buffer_down' and row['driver']=='minimum_cash':row['value']=[5.,0.]
        if case=='opening_excess' and row['driver']=='opening_cash':row['value']=80.
        if case=='opening_excess' and row['driver']=='quotation':row['value']['price']=10.
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=developer_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    calc=result['calculation_details']['scenarios']['base']
    value=sum((r.get('shareholder_distribution_at_start',0.)-r['funding_at_start'])/1.1**i+
        r['shareholder_distribution_at_end']/1.1**(i+1) for i,r in enumerate(calc['ledger']))
    assert calc['fair_value_per_share']*10==pytest.approx(value)


@pytest.mark.parametrize('fault',['units','rate'])
def test_out_of_range_project_arithmetic_is_an_acquisition_not_a_crash(tmp_path,fault):
    rows=developer_records()
    if fault=='units':next(r for r in rows if r['driver']=='projects')['value']['PROJECT-A']['units_total']=10**400
    else:
        for row in rows:
            if row['driver']=='ku':row['value']=1e308
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=developer_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    assert result['acquisition_tasks']


def test_increasing_operating_buffer_requires_early_funding_and_changes_pv(tmp_path):
    rows=developer_records()
    for r in rows:
        if r['driver']=='minimum_cash':r['value']=[0.,5.]
        if r['driver']=='funding':
            r['value']['capacities']=[44.,5.]
            r['value']['commitments']['1']={'id':'Synthetic second period buffer funding','available_date':'2027-01-01',
                'terms':'draw_when_needed_existing_shareholders_no_new_shares'}
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=developer_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    expected=-40.-9/1.1+101.8/1.21
    assert result['fair_value_base']==round(expected/10,2)
    assert result['calculation_details']['scenarios']['base']['ledger'][1]['funding_at_start']==5.


def test_tax_losses_cannot_be_offset_between_unrelated_project_taxpayers(tmp_path):
    rows=developer_records()
    next(r for r in rows if r['driver']=='projects')['value']['PROJECT-A']['taxpayer_entity']='UNRELATED-SPV'
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=developer_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
