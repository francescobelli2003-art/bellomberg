"""Bank residual income reconciles to the existing legal-entity cash ledger."""
from copy import deepcopy
from pathlib import Path

import pytest

from bellomberg.valuation import dcf_engine, sector_analysis
from test_sector_operating_drivers import DAY, SPAN, operating_records
from test_distributable_equity import inputs as capital_fixture


def bank_records():
    rows=[r for r in operating_records() if r['driver'] in ('perimeter','calendar','quotation')]
    def add(driver,field,value,unit='EUR million',basis='GAAP',scenario='model',period=SPAN,entity='SYNTH-GROUP'):
        rows.append(dict(driver=driver,field=field,value=deepcopy(value),scenario=scenario,period=period,
            entity=entity,unit=unit,accounting_basis=basis,source_id='https://example.org/synthetic/bank',
            as_of=DAY,valid_until='2027-01-01',kind='analyst_estimate',rationale='Synthetic explicit bank assumption'))
    add('legal_structure','regulatory_capital',{'parent_entity':'SYNTH-PARENT',
        'subsidiaries':[{'id':'LEGAL-A','regime':'Synthetic common-equity regime'}],
        'capital_basis':'common_equity','accounting_basis':'GAAP'},unit='contract',basis='scope')
    add('opening_common_equity','tangible_book_equity',110.,period='2025-12-31')
    add('opening_parent_equity','tangible_book_equity',15.,period='2025-12-31',entity='SYNTH-PARENT')
    add('opening_consolidation_adjustments','tangible_book_equity',0.,period='2025-12-31')
    add('opening_intangibles','tangible_book_equity',10.,period='2025-12-31')
    capital,_,_=capital_fixture()
    # Terminal common book 118; ROE10%, g0, Ke10% => continuing equity118.
    capital['terminal_equity']=118.
    for scenario in ('bear','base','bull'):
        for key,value in {'net_interest_income':[20.,22.],'fee_income':[4.,4.],
                'operating_expenses':[8.,8.],'credit_losses':[2.,2.],'taxes':[2.,2.],
                'other_income':[0.,0.],'preferred_and_minorities':[0.,0.],
                'other_comprehensive_income':[0.,0.]}.items():
            add(key,'equity_return_path',value,scenario=scenario)
        add('terminal_roe','terminal_assumptions',.1,unit='ratio',basis='valuation',scenario=scenario)
        add('terminal_growth','terminal_assumptions',0.,unit='ratio',basis='valuation',scenario=scenario)
        add('funding_capacity','capital_retention',[0.,0.],basis='cash',scenario=scenario)
        add('ownership_policy','capital_retention','pro_rata_existing_shareholders',unit='text',basis='scope',scenario=scenario)
        add('capital_constraints','regulatory_capital',{'LEGAL-A':{'basis':'common_equity','constraints':[
            {'id':'Risk requirement','exposure':[1000.,1000.],'ratio':[.1,.1],'buffer':[4.,10.],
             'absolute_floor':[0.,0.],'terminal_requirement':110.}]}},unit='contract',basis='statutory',scenario=scenario)
        add('liquidity_bridge','legal_entity_liquidity',{'LEGAL-A':{'opening_cash':20.,
            'operating_cash':[5.,9.],'investing_cash':[0.,0.],'financing_cash':[0.,0.],
            'parent_fees_paid':[3.,3.],'parent_tax_paid':[2.,2.]}},unit='contract',basis='cash',scenario=scenario)
        terminal=deepcopy(capital)
        terminal.update(parent_opening_cash=10.,parent_cash_minimum=[10.],terminal_equity=0.,
            parent_gaap_net_income=[2.],consolidation_adjustments=[0.],discount_periods=[1.])
        terminal['parent_cash_flows']={k:[v[0]] for k,v in capital['parent_cash_flows'].items()}
        terminal['parent_cash_flows']['other_cash_receipts']=[2.]
        sub=terminal['subsidiaries'][0]
        sub.update(opening_gaap_equity=109.,opening_gaap_to_statutory_equity=1.,opening_statutory_capital=110.)
        for k,v in list(sub.items()):
            if isinstance(v,list):sub[k]=[v[-1]]
        sub.update(gaap_net_income=[9.8],gaap_to_statutory_income=[0.],required_statutory_capital=[110.],
                   permitted_distribution=[9.8],proposed_distribution=[9.8],liquidity_before_transfers=[25.8])
        add('terminal_ledger','terminal_assumptions',{'capital':terminal,
            'capital_constraints':{'LEGAL-A':{'basis':'common_equity','constraints':[
                {'id':'Risk requirement','exposure':[1000.],'ratio':[.1],'buffer':[10.],
                 'absolute_floor':[0.],'terminal_requirement':110.}]}},
            'liquidity_bridge':{'LEGAL-A':{'opening_cash':16.,'operating_cash':[14.8],
                'investing_cash':[0.],'financing_cash':[0.],'parent_fees_paid':[3.],'parent_tax_paid':[2.]}}},
            unit='contract',basis='valuation',scenario=scenario,period='2027-12-31')
        for key,value in capital.items():
            def cap_record(driver,key,value,entity='SYNTH-GROUP'):
                field={'ke':'discount_rate_inputs','shares_m':'diluted_shares','discount_periods':'forecast_periods',
                       'terminal_equity':'terminal_assumptions'}.get(key,'parent_ledger')
                unit='text' if isinstance(value,str) else {'ke':'ratio','shares_m':'million shares','discount_periods':'years'}.get(key,'EUR million')
                basis='GAAP' if key in ('parent_gaap_net_income','consolidation_adjustments') else 'valuation' if field in ('discount_rate_inputs','diluted_shares','forecast_periods','terminal_assumptions') else 'cash'
                period='2025-12-31' if key in ('parent_opening_cash','parent_opening_debt','ke','shares_m') else '2027-12-31' if key.startswith('terminal_') else SPAN
                add(driver,field,value,unit,basis,scenario,period,'SYNTH-PARENT' if field=='parent_ledger' else entity)
            if key=='subsidiaries':
                for i,sub in enumerate(value):
                    for name,number in sub.items():
                        if name=='id':continue
                        field='legal_entity_liquidity' if name in ('liquidity_before_transfers','minimum_liquidity') else 'legal_entity_capital'
                        add(f'capital.subsidiaries.{i}.{name}',field,number,basis='GAAP' if name in ('opening_gaap_equity','gaap_net_income') else 'statutory',
                            scenario=scenario,period='2025-12-31' if name.startswith('opening_') else SPAN,entity=sub['id'])
            elif key=='parent_cash_flows':
                for name,numbers in value.items():
                    add('capital.parent_cash_flows.'+name,'parent_ledger',numbers,basis='cash',scenario=scenario,entity='SYNTH-PARENT')
            else: cap_record('capital.'+key,key,value)
    return rows


@pytest.mark.parametrize('child_ids', [['SYNTH-PARENT'], ['SYNTH-GROUP'], ['LEGAL-A', 'LEGAL-A']])
def test_capital_binding_rejects_duplicate_or_self_subsidiaries(child_ids):
    from bellomberg.valuation.bank_adapter import SCHEMA
    from bellomberg.valuation.capital_inputs import bind_capital_inputs
    rows = bank_records()
    legal = next(row['value'] for row in rows if row['driver'] == 'legal_structure')
    legal['subsidiaries'] = [{'id': identity, 'regime': 'Synthetic common-equity regime'}
                             for identity in child_ids]
    bound = bind_capital_inputs(bank_bundle(rows), SCHEMA)
    assert any(issue['field'] == 'regulatory_capital'
               and issue['reason'].startswith('legal_structure: Subsidiaries distinte')
               for issue in bound['issues'])


def bank_bundle(rows=None,profile='bank',symbol='SYNTH-BANK'):
    return sector_analysis.prepare_sector_analysis(symbol,as_of=DAY,providers={
        'profile':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'data':{
            'evidence':[{'field':f,'value':v,'source_id':'synthetic','as_of':DAY}
                        for f,v in [('instrument','equity'),('business_model',profile)]]}},
        'method_inputs':lambda *a,**k:{'status':'ok','source_id':'synthetic','as_of':DAY,'records':deepcopy(bank_records() if rows is None else rows)}},
        user_context={'analysis_context':{'scenario_rationale':{s:'Synthetic capital and credit scenario' for s in ('bear','base','bull')}}})


@pytest.mark.parametrize('profile',['bank','balance_sheet_lender','mortgage_lender'])
def test_bank_common_equity_and_cash_value_match_independent_oracle(tmp_path,profile):
    result=dcf_engine.generate_valuation('SYNTH-BANK',prepared_bundle=bank_bundle(profile=profile),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'], result.get('error')
    # Shareholder14,4 and terminal118; (14/1.1+122/1.21)/10 = 11.36.
    assert result['fair_value_base']==pytest.approx(11.36)
    base=result['calculation_details']['scenarios']['base']
    assert base['closing_common_equity']==pytest.approx(118.)
    assert base['residual_income_value']==pytest.approx(base['cash_equity_value'])
    assert Path(result['path']).is_file()


@pytest.mark.parametrize('fault',['missing_capital','bad_constraints','capital_basis','liquidity','income','terminal','source','extra','funding'])
def test_bank_incomplete_or_unreconciled_capital_is_not_a_fair_value(tmp_path,fault):
    rows=bank_records(); get=lambda d:next(r for r in rows if r['driver']==d and r['scenario']=='base')
    if fault=='missing_capital': rows.remove(get('capital.subsidiaries.0.required_statutory_capital'))
    if fault=='bad_constraints': get('capital_constraints')['value']['LEGAL-A']['constraints'][0]['ratio'][0]=.2
    if fault=='capital_basis': next(r for r in rows if r['driver']=='legal_structure')['value']['capital_basis']='total_capital'
    if fault=='liquidity': get('liquidity_bridge')['value']['LEGAL-A']['operating_cash'][0]=0.
    if fault=='income':get('credit_losses')['value'][0]=8.
    if fault=='terminal':get('capital.terminal_equity')['value']=500.
    if fault=='source':get('capital_constraints')['valid_until']='2020-01-01'
    if fault=='extra':get('capital_constraints')['value']['LEGAL-A']['unknown']=5.
    if fault=='funding':get('capital.parent_cash_minimum')['value']=[100.,100.]
    result=dcf_engine.generate_valuation('SYNTH-BANK',prepared_bundle=bank_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert result.get('fair_value_base') is None
    assert result['acquisition_tasks']


@pytest.mark.parametrize('profile',['bank','balance_sheet_lender','mortgage_lender'])
def test_bank_discount_sensitivity_uses_cash_and_terminal_without_median(tmp_path,profile):
    rows=bank_records()
    for r in rows:
        if r['scenario']!='base':continue
        if r['driver']=='capital.ke':r['value']=.12
        if r['driver']=='capital.terminal_equity':r['value']=118*.1/.12
        if r['driver']=='terminal_ledger':r['value']['capital']['ke']=.12
    result=dcf_engine.generate_valuation('SYNTH-BANK',prepared_bundle=bank_bundle(rows,profile),output_dir=str(tmp_path))
    assert result['valuation_usability']['usable'],result.get('error')
    assert result['fair_value_base']==round((14/1.12+(4+118*.1/.12)/1.12**2)/10,2)
    assert result['fair_value_base']<result['fair_value_bull']


@pytest.mark.parametrize('fault',['terminal_funding','terminal_capital','terminal_liquidity','terminal_perimeter','oci','calendar','historical_terminal','terminal_next_requirement'])
def test_bank_terminal_and_accounting_holes_have_precise_acquisitions(tmp_path,fault):
    rows=bank_records(); get=lambda d:next(r for r in rows if r['driver']==d and r['scenario']=='base')
    terminal=get('terminal_ledger')['value']
    if fault=='terminal_funding':terminal['capital']['parent_cash_minimum']=[100.]
    if fault=='terminal_capital':terminal['capital']['subsidiaries'][0]['required_statutory_capital']=[120.]
    if fault=='terminal_liquidity':terminal['liquidity_bridge']['LEGAL-A']['opening_cash']=10.
    if fault=='terminal_perimeter':terminal['capital']['subsidiaries'][0]['id']='OTHER'
    if fault=='oci':get('other_comprehensive_income')['value']=[1.,0.]
    if fault=='calendar':get('capital.discount_periods')['value']=[1.,3.]
    if fault=='historical_terminal':get('terminal_ledger')['kind']='historical'
    if fault=='terminal_next_requirement':terminal['capital_constraints']['LEGAL-A']['constraints'][0]['terminal_requirement']=100000000.
    result=dcf_engine.generate_valuation('SYNTH-BANK',prepared_bundle=bank_bundle(rows),output_dir=str(tmp_path))
    assert not result['valuation_usability']['usable']
    assert result.get('fair_value_base') is None
    assert any(t['field'] in ('terminal_assumptions','equity_return_path','forecast_periods') for t in result['acquisition_tasks'])
