"""Insurance cash/capital oracles: no bank valuation and no generic underwriting priors."""
from copy import deepcopy
import pytest
from bellomberg.valuation import dcf_engine,sector_analysis
from test_sector_bank_capital import bank_records
from test_distributable_equity import inputs as capital_fixture
from test_sector_operating_drivers import DAY,SPAN


def pc_capital(count=2,*,terminal=False):
    cap=deepcopy(capital_fixture()[0]); path=lambda x:[float(x)]*count
    cap.update(parent_opening_cash=0.,parent_cash_minimum=path(0),parent_opening_debt=0.,terminal_debt=0.,
        parent_gaap_net_income=path(0),consolidation_adjustments=path(0),discount_periods=[float(i+1) for i in range(count)],
        terminal_equity=0. if terminal else 250.)
    cap['parent_cash_flows']={k:path(0) for k in cap['parent_cash_flows']}
    sub=cap['subsidiaries'][0]
    sub.update(opening_statutory_capital=60.,opening_gaap_equity=60.,opening_gaap_to_statutory_equity=0.,
        gaap_net_income=path(25),gaap_to_statutory_income=path(0),other_statutory_movements=path(0),
        required_statutory_capital=path(60),liquidity_before_transfers=path(35),minimum_liquidity=path(10),
        permitted_distribution=path(25),proposed_distribution=path(25),proposed_contribution=path(0))
    return cap


def pc_constraints(count):
    return {'LEGAL-A':{'basis':'common_equity','constraints':[{'id':'Source-calculated insurance common capital',
        'exposure':[60.]*count,'ratio':[1.]*count,'buffer':[0.]*count,'absolute_floor':[0.]*count,'terminal_requirement':60.}]}}


def pc_liquidity(count):
    return {'LEGAL-A':{'opening_cash':10.,'operating_cash':[25.]*count,'investing_cash':[0.]*count,
        'financing_cash':[0.]*count,'parent_fees_paid':[0.]*count,'parent_tax_paid':[0.]*count}}


def pc_records():
    # Reuse the tested record mapping of the existing capital ledger, not bank economics.
    rows=[r for r in bank_records() if r['driver'] in ('perimeter','calendar','quotation','legal_structure',
        'opening_common_equity','opening_parent_equity','opening_consolidation_adjustments','funding_capacity',
        'ownership_policy','capital_constraints','liquidity_bridge','terminal_growth','terminal_ledger') or r['driver'].startswith('capital.')]
    cap=pc_capital()
    for row in rows:
        d=row['driver']
        if d=='quotation':row['value']['price']=25.
        if d=='legal_structure':row['value']['subsidiaries'][0]['regime']='Synthetic insurance common-equity capital regime'
        if d=='opening_common_equity':row['value']=60.
        if d in ('opening_parent_equity','opening_consolidation_adjustments'):row['value']=0.
        if d.startswith('opening_'):row['field']='accounting_bridge'
        if d=='capital_constraints':row['value']=pc_constraints(2)
        if d=='liquidity_bridge':row['value']=pc_liquidity(2)
        if d=='terminal_ledger':row['value']={'capital':pc_capital(1,terminal=True),
            'capital_constraints':pc_constraints(1),'liquidity_bridge':pc_liquidity(1)}
        if d.startswith('capital.'):
            value=cap
            for key in d.split('.')[1:]:value=value[int(key)] if isinstance(value,list) else value[key]
            row['value']=deepcopy(value)
    def add(driver,field,value,unit='contract',basis='GAAP',scenario='model',period=SPAN,entity='LEGAL-A'):
        rows.append(dict(driver=driver,field=field,value=deepcopy(value),unit=unit,accounting_basis=basis,
            scenario=scenario,period=period,entity=entity,source_id='https://example.org/synthetic/insurance',
            as_of=DAY,valid_until='2027-01-01',kind='analyst_estimate',rationale='Explicit synthetic underwriting/reserve/cash case'))
    add('insurance.0.product','premiums',{'product':'short_tail_pc','reinsurance':'constant_quota_share',
        'reinsurance_commission':'none','other_liabilities_basis':'operating_payables_no_subsidiary_borrowing',
        'investment_basis':'annual_cash_yield_on_opening_amortized_cost_no_OCI','reserve_basis':'undiscounted_incurred',
        'tax_basis':'current_cash_equals_expense_no_deferred','ownership_basis':'wholly_owned_ordinary_only',
        'reinsurance_scope':'single_treaty_all_opening_and_future_claims','counterparty':'none','opening_paid_claims_recoverable':0.,
        'accounting':'GAAP'},basis='scope')
    opening={'cash':10.,'investments':100.,'premium_receivable':0.,'unearned_premium':30.,
        'claims_reserve':20.,'ceded_unearned_premium':0.,'reinsurance_recoverable':0.,'reinsurance_payable':0.,
        'other_liabilities':0.}
    add('insurance.0.opening_balance','accounting_bridge',opening,period='2025-12-31')
    parameters={'premium_written':100.,'premium_earned':100.,'premium_collected':100.,
        'loss_ratio':.6,'catastrophe_losses':0.,'prior_reserve_development':0.,'claims_paid':60.,
        'ceded_fraction':0.,'ceded_premium_paid':0.,'reinsurance_recovered':0.,'reinsurance_impairment':0.,
        'operating_expenses':20.,'cash_taxes':0.,'parent_fees_paid':0.,'parent_tax_paid':0.,
        'investment_yield':.05,'investment_purchases':0.,'investment_cost_sold':0.,
        'investment_proceeds':0.,'investment_impairment':0.,'other_liability_change':0.}
    def field(key):
        if key.startswith('premium_'):return 'premiums'
        if key in ('loss_ratio','catastrophe_losses','prior_reserve_development','claims_paid'):return 'claims_reserves'
        if key.startswith(('ceded_','reinsurance_')):return 'reinsurance'
        if key.startswith('investment_'):return 'investment_income'
        return 'expenses_tax'
    for sc in ('bear','base','bull'):
        add('consolidated_income','accounting_bridge',[25.,25.],unit='EUR million',scenario=sc,entity='SYNTH-GROUP')
        add('terminal_income','terminal_assumptions',25.,unit='EUR million',scenario=sc,period='2027-12-31',entity='SYNTH-GROUP')
        for key,value in parameters.items():
            add('insurance.0.'+key,field(key),[value,value],
                unit='ratio' if key in ('loss_ratio','ceded_fraction','investment_yield') else 'EUR million',scenario=sc)
        add('continuing_economics','terminal_assumptions',{'LEGAL-A':parameters},scenario=sc,period='2027-12-31',entity='SYNTH-GROUP')
    return rows


def insurance_bundle(rows=None,profile='insurance_pc'):
    return sector_analysis.prepare_sector_analysis('SYNTH-INS',as_of=DAY,providers={
        'profile':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'data':{'evidence':[
            {'field':f,'value':v,'source_id':'synthetic','as_of':DAY} for f,v in [('instrument','equity'),('business_model',profile)]]}},
        'method_inputs':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'records':deepcopy(pc_records() if rows is None else rows)}},
        user_context={'analysis_context':{'scenario_rationale':{s:'Synthetic insurance scenario with reconciled legal cash' for s in ('bear','base','bull')}}})


def test_pc_income_reserves_and_cash_match_stationary_independent_oracle(tmp_path):
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=insurance_bundle(),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    # Premium100 - claims60 - expenses20 + cash investment income5 =25.
    # Cash and common capital remain10/60; D25/Ke.1/shares10 =25.
    assert result['fair_value_base']==25.
    assert result['engine']=='insurance_pc'
    assert result['calculation_details']['scenarios']['base']['entities']['LEGAL-A']['rows'][0]['net_income']==25.


@pytest.mark.parametrize('fault',['reserve','cash','capital','reinsurance','investment','missing','stale','entity','unsupported'])
def test_pc_unreconciled_earnings_and_capital_do_not_make_a_value(tmp_path,fault):
    rows=pc_records();get=lambda d:next(r for r in rows if r['driver']==d and r['scenario']=='base')
    if fault=='reserve':get('insurance.0.prior_reserve_development')['value']=[10.,0.]
    if fault=='cash':get('insurance.0.premium_collected')['value']=[90.,100.]
    if fault=='capital':get('capital.subsidiaries.0.required_statutory_capital')['value']=[70.,60.]
    if fault=='reinsurance':get('insurance.0.reinsurance_recovered')['value']=[10.,0.]
    if fault=='investment':get('insurance.0.investment_yield')['value']=[.1,.05]
    if fault=='missing':rows.remove(get('insurance.0.claims_paid'))
    if fault=='stale':get('insurance.0.loss_ratio')['valid_until']='2020-01-01'
    if fault=='entity':get('insurance.0.loss_ratio')['entity']='MISSING-ENTITY'
    if fault=='unsupported':next(r for r in rows if r['driver']=='insurance.0.product')['value']['reinsurance']='excess_of_loss'
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=insurance_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert result.get('fair_value_base') is None and result['acquisition_tasks']


def test_adverse_reserve_requires_retention_before_claims_are_paid(tmp_path):
    rows=pc_records()
    changes={'insurance.0.prior_reserve_development':[10.,0.],'insurance.0.claims_paid':[60.,70.],
        'consolidated_income':[15.,25.],'capital.subsidiaries.0.gaap_net_income':[15.,25.],
        'capital.subsidiaries.0.proposed_distribution':[15.,25.]}
    for row in rows:
        if row['scenario']!='base':continue
        if row['driver'] in changes:row['value']=deepcopy(changes[row['driver']])
        if row['driver']=='liquidity_bridge':row['value']['LEGAL-A']['operating_cash']=[25.,15.]
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=insurance_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round((15/1.1+275/1.21)/10,2)
    first=result['calculation_details']['scenarios']['base']['entities']['LEGAL-A']['rows'][0]
    assert first['closing_balance']['claims_reserve']==30.
    assert first['closing_balance']['cash']==20.
    assert first['net_income']==15. and first['operating_cash']==25.


@pytest.mark.parametrize('fault',["reserve_release", "payables_financing"])
def test_pc_cannot_manufacture_profit_or_cash_from_balance_movements(fault):
    from bellomberg.valuation.insurance_economics import project_pc
    rows=pc_records(); get=lambda d:next(r['value'] for r in rows if r['driver']==d)
    opening=get('insurance.0.opening_balance'); period=deepcopy(get('continuing_economics')['LEGAL-A'])
    if fault=='reserve_release':
        period.update(loss_ratio=.8,prior_reserve_development=-30.,claims_paid=50.)
    else:
        period.update(other_liability_change=100.,investment_purchases=100.)
    issues=[]
    project_pc(opening,get('insurance.0.product'),[period],[25.],[0.],lambda f,m:issues.append((f,m)))
    assert any('pregress' in m.lower() or 'payables' in m.lower() for f,m in issues),issues


def test_positive_quota_share_consumes_premiums_reserves_recoveries_and_investments(tmp_path):
    rows=pc_records()
    changes={'insurance.0.ceded_fraction':[.2,.2],'insurance.0.ceded_premium_paid':[20.,20.],
        'insurance.0.reinsurance_recovered':[12.,12.],'consolidated_income':[16.5,16.5],
        'terminal_income':16.5,'capital.terminal_equity':165.,
        'capital.subsidiaries.0.gaap_net_income':[16.5,16.5],
        'capital.subsidiaries.0.liquidity_before_transfers':[26.5,26.5],
        'capital.subsidiaries.0.proposed_distribution':[16.5,16.5]}
    for r in rows:
        d=r['driver']
        if d in changes:r['value']=deepcopy(changes[d])
        if d=='insurance.0.product':r['value']['counterparty']='Synthetic reinsurer A'
        if d=='insurance.0.opening_balance':r['value'].update(investments=90.,ceded_unearned_premium=6.,reinsurance_recoverable=4.)
        if d=='liquidity_bridge':r['value']['LEGAL-A']['operating_cash']=[16.5,16.5]
        if d=='continuing_economics':r['value']['LEGAL-A'].update(ceded_fraction=.2,ceded_premium_paid=20.,reinsurance_recovered=12.)
        if d=='terminal_ledger':
            t=r['value']; sub=t['capital']['subsidiaries'][0]
            sub.update(gaap_net_income=[16.5],liquidity_before_transfers=[26.5],proposed_distribution=[16.5])
            t['liquidity_bridge']['LEGAL-A']['operating_cash']=[16.5]
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=insurance_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==16.5
    row=result['calculation_details']['scenarios']['base']['entities']['LEGAL-A']['rows'][0]
    assert row['reinsurance_income']==12. and row['closing_balance']['reinsurance_recoverable']==4.
