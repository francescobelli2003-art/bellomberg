"""Conditional development branches, funding, dilution and contractual expiry."""
from copy import deepcopy
import pytest
from bellomberg.valuation import dcf_engine
from test_resources_valuation import resource_records
from test_insurance_valuation import insurance_bundle
from test_sector_operating_drivers import DAY,SPAN


def development_records(*,probabilities=(.5,.4),outside_price=None):
    from bellomberg.valuation.development_adapter import POLICY
    span=SPAN+'|2028-01-01/2028-12-31|2029-01-01/2029-12-31'
    rows=[r for r in resource_records() if r['driver'] not in ('assets','forecasts','closure','policy')]
    for r in rows:
        if r['field']=='enterprise_equity_bridge':r['field']='development_funding'
        if r['field']=='resource_costs':r['field']='development_cash_flows'
        if r['period']==SPAN:r['period']=span
        if r['driver']=='quotation':r['value']['price']=2.
        if r['driver']=='calendar':r['value']['periods'] += [dict(start=f'{y}-01-01',end=f'{y}-12-31') for y in (2028,2029)]
        if r['driver']=='opening_cash':r['value']=20.
        if r['driver'] in ('central_cash_cost','minimum_cash','expected_tax_shield'):r['value']=[0.]*4
        if r['driver']=='tax_rate':r['value']=[.2]*4
        if r['driver']=='funding':
            r['value']={'capacities':[6.,22.,0.,0.],'commitments':{
                str(i):{'id':f'Synthetic unconditional phase funding {i}','available_date':f'{2026+i}-01-01',
                    'terms':'draw_when_needed_existing_shareholders_no_new_shares'} for i in range(2)}}
            if outside_price is not None:
                for c in r['value']['commitments'].values():c.update(
                    terms='draw_when_needed_outside_common_equity_at_fixed_price',issue_price=outside_price)
    def add(driver,field,value,scenario='model',basis='development',period=span):
        rows.append(dict(driver=driver,field=field,value=deepcopy(value),unit='contract',accounting_basis=basis,
            scenario=scenario,period=period,entity='SYNTH-GROUP',source_id='https://example.org/synthetic/conditional-license-development',
            as_of=DAY,valid_until='2027-01-01',kind='analyst_estimate',rationale='Sourced synthetic conditional development and financing case'))
    add('policy','development_funding',POLICY,basis='scope')
    add('asset','asset_stages',{'id':'ASSET-A','stage_order':['STAGE-A','STAGE-B'],
        'rights':'wholly_owned_exclusive_license_no_other_assets_or_claims','taxpayer_entity':'SYNTH-GROUP',
        'tax_jurisdiction':'Synthetic jurisdiction','tax_source':'Synthetic current deduction of all R&D/milestone/outgoing costs',
        'unit':'million_licensed_units','price_unit':'EUR_per_licensed_unit'},period='2025-12-31')
    add('life','asset_life',{'patent_expiry':'2029-12-31','contract_expiry':'2029-12-31','economic_expiry':'2029-12-31',
        'basis':'all_rights_and_royalties_end_together_no_residual_receipts','final_wind_down_cost':0.,
        'source':'Synthetic contract expires with patent; no post-expiry payments'})
    for sc in ('bear','base','bull'):
        add('stage_schedule','asset_stages',{'STAGE-A':{'period':1,'cost':10.,'success_received':0.,'success_paid':5.,'failure_close_cost':1.},
            'STAGE-B':{'period':2,'cost':20.,'success_received':10.,'success_paid':0.,'failure_close_cost':2.}},sc)
        add('probabilities','conditional_probabilities',dict(zip(('STAGE-A','STAGE-B'),probabilities)),sc)
        add('commercial','development_cash_flows',{'units':[0.,0.,10.,10.],'net_price_per_unit':[100.]*4,
            'incoming_royalty_rate':[.1]*4,'outgoing_royalty_rate':[0.]*4,'cash_operating_cost':[0.]*4},sc)
        add('risk_contract','conditional_probabilities',{'rate_excluding_modeled_technical_risk':.1,
            'modeled_transition_risk_addon':0.,'cashflow_basis':'conditional_on_full_success_not_probability_weighted'},sc)
    return rows


def development_bundle(rows=None):
    return insurance_bundle(development_records() if rows is None else rows,profile='development_asset')


def generate(tmp_path,rows=None):
    return dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=development_bundle(rows),output_dir=str(tmp_path))


@pytest.mark.parametrize('probabilities',[(.5,.4),(0.,.4),(1.,1.)])
def test_conditional_probabilities_use_stage_entry_cost_and_after_outcome_cash(tmp_path,probabilities):
    rows=development_records(probabilities=probabilities)
    if probabilities==(1.,1.):
        next(r for r in rows if r['driver']=='quotation')['value']['price']=10.
    if probabilities==(0.,.4):
        next(r for r in rows if r['driver']=='quotation')['value']['price']=1.
    result=generate(tmp_path,rows)
    assert result['valuation_usability']['usable'],result.get('error')
    a,b=probabilities
    # Initialcash20 - firstcost10, failure1 atFY1; success1 pays5 and enters
    # cost20 atFY2start; failure2 pays2, success2 receives10 (no current tax
    # as cost20 deductible sameFY), then100 royalties less20tax in each FY3/4.
    fail1=10.-1./1.1
    fail2=10.-25./1.1-2./1.21
    success=10.-25./1.1+10./1.21+80./1.1**3+80./1.1**4
    expected=(1-a)*fail1+a*(1-b)*fail2+a*b*success
    assert result['fair_value_base']==round(expected/10,2)
    calc=result['calculation_details']['scenarios']['base']
    assert sum(o['probability'] for o in calc['outcomes'])==pytest.approx(1.)
    assert calc['terminal_value']==0.


def test_failure_releases_documented_buffer_at_failure_date_and_skips_unreachable_funding(tmp_path):
    rows=development_records(probabilities=(0.,.4))
    for r in rows:
        if r['driver']=='quotation':r['value']['price']=1.
        if r['driver']=='minimum_cash':r['value']=[5.]*4
        if r['driver']=='funding':r['value']={'capacities':[0.,0.,0.,0.],'commitments':{}}
    result=generate(tmp_path,rows)
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round((5.+4./1.1)/10.,2)
    outcomes=result['calculation_details']['scenarios']['base']['outcomes']
    failure=next(o for o in outcomes if o['outcome']=='failure:STAGE-A')
    assert len(failure['ledger'])==1 and failure['ledger'][0]['closing_cash']==0.
    assert all(not o['reachable'] for o in outcomes if o is not failure)


@pytest.mark.parametrize('shock',['delay','expiry','royalty','wind_down'])
def test_development_timing_royalty_and_contract_end_sensitivities(tmp_path,shock):
    rows=development_records(probabilities=(1.,1.))
    for r in rows:
        d=r['driver']
        if d=='quotation':r['value']['price']=10.
        if shock=='delay':
            if d=='stage_schedule':r['value']['STAGE-B']['period']=3
            if d=='commercial':r['value']['units']=[0.,0.,0.,10.]
            if d=='funding':
                r['value']['capacities']=[6.,0.,22.,0.]
                c=r['value']['commitments'].pop('1');c['available_date']='2028-01-01';r['value']['commitments']['2']=c
        if shock=='expiry':
            if '|2029-01-01/2029-12-31' in r['period']:r['period']=r['period'].replace('|2029-01-01/2029-12-31','')
            if d=='calendar':r['value']['periods']=r['value']['periods'][:3]
            if d in ('central_cash_cost','tax_rate','minimum_cash','expected_tax_shield'):r['value']=r['value'][:3]
            if d=='funding':r['value']['capacities']=r['value']['capacities'][:3]
            if d=='commercial':r['value']={k:v[:3] for k,v in r['value'].items()}
            if d=='life':
                for k in ('patent_expiry','contract_expiry','economic_expiry'):r['value'][k]='2028-12-31'
        if shock=='royalty' and d=='commercial':r['value']['outgoing_royalty_rate']=[.2]*4
        if shock=='wind_down' and d=='life':r['value']['final_wind_down_cost']=10.
    expected={'delay':10.-5./1.1-20./1.21+10./1.1**3+80./1.1**4,
              'expiry':10.-25./1.1+10./1.21+80./1.1**3,
              'royalty':10.-25./1.1+10./1.21+64./1.1**3+64./1.1**4,
              'wind_down':10.-25./1.1+10./1.21+80./1.1**3+72./1.1**4}[shock]
    result=generate(tmp_path,rows)
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round(expected/10,2)
    calc=result['calculation_details']['scenarios']['base']
    assert sum(o['probability'] for o in calc['outcomes'])==pytest.approx(1.)
    assert calc['terminal_value']==0.


@pytest.mark.parametrize('fault',['missing_q','invalid_q','double_risk','early_sales','expired','stages_reversed',
    'funding_only_average','outside_price_missing','other_taxpayer','stale','unconsumed'])
def test_development_missing_or_inconsistent_path_returns_acquisition(tmp_path,fault):
    rows=development_records();get=lambda d:next(r for r in rows if r['driver']==d)
    if fault=='missing_q':rows.remove(get('probabilities'))
    if fault=='invalid_q':get('probabilities')['value']['STAGE-A']=1.1
    if fault=='double_risk':get('risk_contract')['value']['modeled_transition_risk_addon']=.2
    if fault=='early_sales':get('commercial')['value']['units'][0]=1.
    if fault=='expired':get('life')['value']['economic_expiry']='2028-12-31'
    if fault=='stages_reversed':get('stage_schedule')['value']['STAGE-B']['period']=1
    if fault=='funding_only_average':get('funding')['value']['capacities'][1]=11.
    if fault=='outside_price_missing':get('funding')['value']['commitments']['1']['terms']='draw_when_needed_outside_common_equity_at_fixed_price'
    if fault=='other_taxpayer':get('asset')['value']['taxpayer_entity']='UNRELATED'
    if fault=='stale':get('probabilities')['valid_until']='2020-01-01'
    if fault=='unconsumed':get('commercial')['value']['default_terminal_growth']=.02
    result=generate(tmp_path,rows)
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    assert result['acquisition_tasks']


@pytest.mark.parametrize('price',[2.,4.])
def test_sourced_external_equity_price_dilutes_original_holder_cashflows(tmp_path,price):
    result=generate(tmp_path,development_records(outside_price=price))
    assert result['valuation_usability']['usable'],result.get('error')
    # Opening10 distributed to original holders. Failure1 funding1 atFY1end,
    # no subsequent distributions. Success1 issues5/price then20/price atFY2
    # start. Failure2 again no proceeds. Fullsuccess FY2+3+4 distributions
    # accrue to original10 out of (10+25/price) million shares.
    fraction=10./(10.+25./price)
    expected=10.+.2*fraction*(10./1.21+80./1.1**3+80./1.1**4)
    assert result['fair_value_base']==round(expected/10,2)


@pytest.mark.parametrize('driver,value',[('minimum_cash',-100.),('tax_rate',2.)])
def test_impossible_path_does_not_hide_invalid_input_domain(tmp_path,driver,value):
    rows=development_records(probabilities=(0.,.4))
    for r in rows:
        if r['driver']=='quotation':r['value']['price']=1.
        if r['driver']==driver:r['value'][-1]=value
    result=generate(tmp_path,rows)
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None


def test_positive_transition_probability_underflow_is_not_an_impossible_branch(tmp_path):
    rows=development_records(probabilities=(1e-300,1e-300))
    for r in rows:
        if r['driver']=='quotation':r['value']['price']=1.
    result=generate(tmp_path,rows)
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    assert any('non rappresentabile' in t.get('reason','') for t in result['acquisition_tasks'])


@pytest.mark.parametrize('external_period',[0,1])
def test_external_investors_participate_in_later_pro_rata_calls(tmp_path,external_period):
    rows=development_records()
    for r in rows:
        if r['driver']=='funding':r['value']['commitments'][str(external_period)].update(
            terms='draw_when_needed_outside_common_equity_at_fixed_price',issue_price=2.)
    if external_period==0:
        expected=10.-.5*16./1.1-.3*1.6/1.21+.2*(8./1.21+64./1.1**3+64./1.1**4)
    else:
        expected=10.-3./1.1+.2*(5./1.21+40./1.1**3+40./1.1**4)
    result=generate(tmp_path,rows)
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round(expected/10.,2)
