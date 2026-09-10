"""Annual-renewable term life: independent population, profit and capital oracles."""
from copy import deepcopy
import pytest
from bellomberg.valuation import dcf_engine
from test_insurance_valuation import pc_records,insurance_bundle,DAY,SPAN


def life_records(mortality=.1,lapse=0.):
    rows=[r for r in pc_records() if not r['driver'].startswith('insurance.')]
    # Renewals survive deaths then lapses. New production maintains one million in force.
    new=1/((1-mortality)*(1-lapse))-1; insured=1+new
    claims=insured*mortality*30.; income=insured*(10.-mortality*30.-1.)-new*12.+5.
    changes={'consolidated_income':[income]*2,'terminal_income':income,'capital.terminal_equity':income/.1,
        'capital.subsidiaries.0.gaap_net_income':[income]*2,
        'capital.subsidiaries.0.liquidity_before_transfers':[10.+income]*2,
        'capital.subsidiaries.0.proposed_distribution':[income]*2}
    parameters={'new_policies':new,'mortality_rate':mortality,'lapse_rate':lapse,'premium_per_policy':10.,
        'death_benefit_per_policy':30.,'admin_cost_per_policy':1.,'acquisition_cost_per_policy':12.,
        'closing_policy_reserve':50.,'claims_paid':claims,'operating_expenses':0.,'cash_taxes':0.,
        'parent_fees_paid':0.,'parent_tax_paid':0.,'investment_yield':.05,'investment_purchases':0.,
        'investment_cost_sold':0.,'investment_proceeds':0.,'investment_impairment':0.,'other_liability_change':0.}
    for r in rows:
        d=r['driver']
        if d=='quotation':r['value']['price']=10.
        if d in changes:r['value']=deepcopy(changes[d])
        if d=='capital.subsidiaries.0.permitted_distribution':r['value']=[30.,30.]
        if d=='liquidity_bridge':r['value']['LEGAL-A']['operating_cash']=[income]*2
        if d=='continuing_economics':r['value']={'LEGAL-A':deepcopy(parameters)}
        if d=='terminal_ledger':
            t=r['value']; sub=t['capital']['subsidiaries'][0]
            sub.update(gaap_net_income=[income],liquidity_before_transfers=[10.+income],
                proposed_distribution=[income],permitted_distribution=[30.])
            t['liquidity_bridge']['LEGAL-A']['operating_cash']=[income]
    def add(driver,field,value,unit='contract',basis='GAAP',scenario='model',period=SPAN):
        rows.append(dict(driver='insurance.0.'+driver,field=field,value=deepcopy(value),unit=unit,accounting_basis=basis,
            scenario=scenario,period=period,entity='LEGAL-A',source_id='https://example.org/synthetic/life-actuarial',
            as_of=DAY,valid_until='2027-01-01',kind='analyst_estimate',rationale='Explicit synthetic term policy and reserve projection'))
    add('product','in_force_business',{'product':'annual_renewable_term','accounting':'GAAP',
        'reserve_basis':'future_coverage_GAAP_excluding_incurred_claims_DAC_CSM_UPR_deferred_tax',
        'renewal':'guaranteed_at_documented_premiums_and_death_cover',
        'timing':'new_and_premium_at_start_death_during_year_lapse_after_death_at_end',
        'benefits':'death_only_no_surrender_maturity_investment_guarantee_or_participation',
        'premium_basis':'fully_collected_no_receivable','reinsurance':'none',
        'investment_basis':'annual_cash_yield_on_opening_amortized_cost_no_OCI',
        'tax_basis':'current_cash_equals_expense_no_deferred','ownership_basis':'wholly_owned_ordinary_only',
        'other_liabilities_basis':'operating_payables_no_subsidiary_borrowing'},basis='scope')
    add('opening_balance','accounting_bridge',{'cash':10.,'investments':100.,'policy_reserve':50.,
        'claims_payable':0.,'other_liabilities':0.,'in_force_m':1.},period='2025-12-31')
    for sc in ('bear','base','bull'):
        for key,value in parameters.items():
            field=('new_business' if key in ('new_policies','acquisition_cost_per_policy') else
                'actuarial_assumptions' if key in ('mortality_rate','lapse_rate') else
                'in_force_business' if key in ('premium_per_policy','death_benefit_per_policy','admin_cost_per_policy') else
                'insurance_liabilities' if key in ('closing_policy_reserve','claims_paid') else
                'investment_income' if key.startswith('investment_') else 'expenses_tax')
            unit='million policies' if key=='new_policies' else 'ratio' if key in ('mortality_rate','lapse_rate','investment_yield') else 'EUR per policy' if key.endswith('_per_policy') else 'EUR million'
            add(key,field,[value,value],unit=unit,scenario=sc)
    for sc in ('bear','base','bull'):
        relevant=('new_policies','mortality_rate','lapse_rate','premium_per_policy','death_benefit_per_policy',
            'admin_cost_per_policy','acquisition_cost_per_policy','closing_policy_reserve')
        add('reserve_report','insurance_liabilities',{'report_id':'Synthetic actuarial reserve projection',
            'reserve_basis':'future_coverage_GAAP_excluding_incurred_claims_DAC_CSM_UPR_deferred_tax',
            'opening':{'in_force_m':1.,'policy_reserve':50.},
            'forecast':{k:[parameters[k]]*2 for k in relevant},'continuing':{k:parameters[k] for k in relevant}},scenario=sc)
    return rows


def life_bundle(rows=None):
    return insurance_bundle(life_records() if rows is None else rows,profile='insurance_life')


@pytest.mark.parametrize('mortality,lapse,oracle',[(.1,0.,31/3),(.2,0.,5.75),(.1,.2,26/3)])
def test_life_new_business_mortality_lapse_and_cash_oracle(tmp_path,mortality,lapse,oracle):
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=life_bundle(life_records(mortality,lapse)),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round(oracle,2)
    row=result['calculation_details']['scenarios']['base']['entities']['LEGAL-A']['rows'][0]
    assert row['closing_balance']['in_force_m']==pytest.approx(1.)
    assert row['net_income']==pytest.approx(oracle)
    assert row['new_policies']>0 and row['deaths']>0


@pytest.mark.parametrize('fault',['missing','stale','mortality','reserve','cash','new_business','entity','unsupported','extra_csm'])
def test_life_missing_or_unreconciled_actuarial_cash_inputs_are_unavailable(tmp_path,fault):
    rows=life_records(); get=lambda d:next(r for r in rows if r['driver']==d and r['scenario']=='base')
    if fault=='missing':rows.remove(get('insurance.0.closing_policy_reserve'))
    if fault=='stale':get('insurance.0.mortality_rate')['valid_until']='2020-01-01'
    if fault=='mortality':get('insurance.0.mortality_rate')['value']=[1.1,.1]
    if fault=='reserve':get('insurance.0.closing_policy_reserve')['value']=[60.,50.]
    if fault=='cash':get('insurance.0.claims_paid')['value']=[0.,0.]
    if fault=='new_business':get('insurance.0.new_policies')['value']=[0.,0.]
    if fault=='entity':get('insurance.0.lapse_rate')['entity']='WRONG'
    if fault=='unsupported':next(r for r in rows if r['driver']=='insurance.0.product')['value']['accounting']='IFRS17'
    if fault=='extra_csm':
        row=deepcopy(get('insurance.0.closing_policy_reserve'));row['driver']='csm';rows.append(row)
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=life_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    assert result['acquisition_tasks']


def test_increase_then_release_of_actuarial_reserves_delays_distributions(tmp_path):
    rows=life_records(); income=31/3
    changes={'insurance.0.closing_policy_reserve':[60.,50.],
        'consolidated_income':[income-10,income+10],
        'capital.subsidiaries.0.gaap_net_income':[income-10,income+10],
        'capital.subsidiaries.0.proposed_distribution':[income-10,income+10],
        'capital.subsidiaries.0.liquidity_before_transfers':[10+income,20+income]}
    for row in rows:
        if row['scenario']!='base':continue
        if row['driver'] in changes:row['value']=deepcopy(changes[row['driver']])
        if row['driver']=='insurance.0.reserve_report':row['value']['forecast']['closing_policy_reserve']=[60.,50.]
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=life_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round(((income-10)/1.1+(income+10+income/.1)/1.21)/10,2)
    first=result['calculation_details']['scenarios']['base']['entities']['LEGAL-A']['rows'][0]
    assert first['net_income']==pytest.approx(income-10) and first['operating_cash']==pytest.approx(income)
    assert first['closing_balance']['cash']==pytest.approx(20.)


def test_reserve_report_cannot_come_from_a_different_actuarial_scenario(tmp_path):
    rows=life_records()
    report=next(r['value'] for r in rows if r['driver']=='insurance.0.reserve_report' and r['scenario']=='base')
    report['forecast']['mortality_rate']=[.01,.01]
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=life_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable'] and result.get('fair_value_base') is None
    assert 'Piano riserve' in result['error']


def test_life_continuing_growth_reinvests_assets_reserves_and_common_capital(tmp_path):
    rows=life_records(); g=.02; tail_new=1.02/.9-1
    for r in rows:
        if r['scenario']!='base':continue
        d=r['driver']
        if d=='terminal_growth':r['value']=g
        if d=='terminal_income':r['value']=9.2
        if d=='capital.terminal_equity':r['value']=100.
        if d=='capital_constraints':r['value']['LEGAL-A']['constraints'][0]['terminal_requirement']=61.2
        if d=='continuing_economics':
            r['value']['LEGAL-A'].update(new_policies=tail_new,closing_policy_reserve=51.,
                claims_paid=3.4,investment_purchases=2.)
        if d=='insurance.0.reserve_report':r['value']['continuing'].update(new_policies=tail_new,closing_policy_reserve=51.)
        if d=='terminal_ledger':
            t=r['value']; sub=t['capital']['subsidiaries'][0]
            sub.update(gaap_net_income=[9.2],required_statutory_capital=[61.2],liquidity_before_transfers=[18.2],
                minimum_liquidity=[10.2],proposed_distribution=[8.])
            c=t['capital_constraints']['LEGAL-A']['constraints'][0]
            c.update(exposure=[61.2],terminal_requirement=61.2*1.02)
            t['liquidity_bridge']['LEGAL-A'].update(operating_cash=[10.2],investing_cash=[-2.])
    result=dcf_engine.generate_valuation('SYNTH-INS',prepared_bundle=life_bundle(rows),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round(((31/3)/1.1+(31/3+100.)/1.21)/10,2)
    econ=result['calculation_details']['scenarios']['base']['entities']['LEGAL-A']
    first=econ['rows'][-1];second=econ['second_continuing_year']
    assert first['closing_balance']['in_force_m']==pytest.approx(1.02)
    assert second['closing_balance']['in_force_m']==pytest.approx(1.0404)
    assert second['net_income']==pytest.approx(9.2*1.02)
    assert second['closing_common_equity']==pytest.approx(60*1.02**2)
