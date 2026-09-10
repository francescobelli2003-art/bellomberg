"""Insurance-specific earnings feed the existing legal-capital/cash ledger."""
from .bank_adapter import SCHEMA as BANK_SCHEMA
from .capital_inputs import bind_capital_inputs,assemble_capital,validate_constraints,validate_terminal,equal
from .distributable_equity import project_distributable_equity
from .documented_inputs import finish_documented
from .dcf_quality import SCENARIOS,_date
from .insurance_economics import PC_PATHS,project_pc,common_book
from .life_economics import LIFE_PATHS,project_life,life_book,validate_reserve_report

_SHARED={'perimeter','calendar','quotation','legal_structure','opening_common_equity','opening_parent_equity',
    'opening_consolidation_adjustments','funding_capacity','ownership_policy','capital_constraints','liquidity_bridge',
    'terminal_growth','terminal_ledger'}
SCHEMA={k:v for k,v in BANK_SCHEMA.items() if k in _SHARED}
for _key in ('opening_common_equity','opening_parent_equity','opening_consolidation_adjustments'):
    SCHEMA[_key]=('accounting_bridge',*SCHEMA[_key][1:])
SCHEMA.update({
    'consolidated_income':('accounting_bridge','money','GAAP','future','path','scenario'),
    'terminal_income':('terminal_assumptions','money','GAAP','terminal','number','scenario'),
    'continuing_economics':('terminal_assumptions','contract','GAAP','terminal','contract','scenario'),
})
PC_SCHEMA={'product':('premiums','contract','scope','future','contract','model'),
    'opening_balance':('accounting_bridge','contract','GAAP','opening','contract','model')}
for _key in PC_PATHS:
    _field='premiums' if _key.startswith('premium_') else 'claims_reserves' if _key in ('loss_ratio','catastrophe_losses','prior_reserve_development','claims_paid') else 'reinsurance' if _key.startswith(('ceded_','reinsurance_')) else 'investment_income' if _key.startswith('investment_') else 'expenses_tax'
    PC_SCHEMA[_key]=(_field,'ratio' if _key in ('loss_ratio','ceded_fraction','investment_yield') else 'money','GAAP','future','path','scenario')

LIFE_SCHEMA={'product':('in_force_business','contract','scope','future','contract','model'),
    'opening_balance':('accounting_bridge','contract','GAAP','opening','contract','model'),
    'reserve_report':('insurance_liabilities','contract','GAAP','future','contract','scenario')}
for _key in LIFE_PATHS:
    _field=('new_business' if _key in ('new_policies','acquisition_cost_per_policy') else
        'actuarial_assumptions' if _key in ('mortality_rate','lapse_rate') else
        'in_force_business' if _key in ('premium_per_policy','death_benefit_per_policy','admin_cost_per_policy') else
        'insurance_liabilities' if _key in ('closing_policy_reserve','claims_paid') else
        'investment_income' if _key.startswith('investment_') else 'expenses_tax')
    _unit='million policies' if _key=='new_policies' else 'ratio' if _key in ('mortality_rate','lapse_rate','investment_yield') else 'money_per_policy' if _key.endswith('_per_policy') else 'money'
    LIFE_SCHEMA[_key]=(_field,_unit,'GAAP','future','path','scenario')


def generate_insurance(bundle,*,output_dir,metadata):
    life=bundle['decision']['method_id']=='insurance_life_distributable_equity'
    paths,project,book=(LIFE_PATHS,project_life,life_book) if life else (PC_PATHS,project_pc,common_book)
    bound=bind_capital_inputs(bundle,SCHEMA,entity_schema=LIFE_SCHEMA if life else PC_SCHEMA);problem=bound['problem'];results={}
    if not bound['issues']:
        model=bound['values']['model'];ids=bound['legal_ids'];years=[int(p['end'][:4]) for p in bound['calendar']['periods']]
        previous=_date(bound['calendar']['valuation_date'])
        for period in bound['calendar']['periods']:
            end=_date(period['end'])
            if (end.year,end.month,end.day)!=(previous.year+1,previous.month,previous.day):
                problem('calendar','Assicurazioni: periodi annuali interi richiesti, niente annualizzazione implicita')
            previous=end
        for scenario in SCENARIOS:
            sc=bound['values'][scenario];cap=assemble_capital(sc,ids);terminal=sc['terminal_ledger']
            if sc['ownership_policy']!='pro_rata_existing_shareholders' or cap['discount_periods']!=bound['times']:
                problem('ownership_policy',scenario+': ownership/calendario non riconciliati')
            if cap['ke']<=max(0,sc['terminal_growth']) or sc['terminal_growth']<=-1 or any(v<0 for v in sc['funding_capacity']):
                problem('terminal_growth',scenario+': Ke/g/funding non supportati');continue
            ledger=project_distributable_equity(cap,sc['consolidated_income'],years)
            if not isinstance(terminal,dict) or set(terminal)!={'capital','capital_constraints','liquidity_bridge'}:
                problem('terminal_ledger',scenario+': contratto continuing completo richiesto');continue
            tail=project_distributable_equity(terminal['capital'],[sc['terminal_income']],[years[-1]+1])
            for result in (ledger,tail):
                if result['status']!='CALCOLABILE':
                    for issue in result['issues']:problem('accounting_bridge',scenario+': '+issue)
            if bound['issues']:continue
            validate_constraints(cap,sc['capital_constraints'],sc['liquidity_bridge'],problem)
            validate_constraints(terminal['capital'],terminal['capital_constraints'],terminal['liquidity_bridge'],problem)
            terminal_subs={s['id']:s for s in terminal['capital']['subsidiaries']}
            if set(terminal_subs)!=set(ids) or set(sc['continuing_economics'])!=set(ids):
                problem('continuing_economics',scenario+': perimetro entita non completo');continue
            if bound['issues']:continue
            if not equal(model['opening_common_equity'],model['opening_parent_equity']+model['opening_consolidation_adjustments']+sum(s['opening_gaap_equity'] for s in cap['subsidiaries'])):
                problem('opening_common_equity',scenario+': equity opening gruppo non riconciliato')
            entities={}
            for i,sub in enumerate(cap['subsidiaries']):
                identity=sub['id'];prefix='insurance.'+str(i)+'.';t_sub=terminal_subs[identity]
                periods=[{k:sc[prefix+k][j] for k in paths} for j in range(len(years))]+[sc['continuing_economics'][identity]]
                if not life and isinstance(periods[-1],dict) and periods[-1].get('prior_reserve_development')!=0:
                    problem('claims_reserves',identity+': continuing senza sviluppo riserve pregresse; acquisire ponte per coorti prima di capitalizzarlo')
                economics=project(model[prefix+'opening_balance'],model[prefix+'product'],periods,
                    sub['proposed_distribution']+t_sub['proposed_distribution'],
                    sub['proposed_contribution']+t_sub['proposed_contribution'],problem)
                if economics is None:continue
                if life:
                    validate_reserve_report(sc[prefix+'reserve_report'],model[prefix+'opening_balance'],periods,problem)
                entities[identity]=economics
                if not equal(book(model[prefix+'opening_balance']),sub['opening_gaap_equity']) or not equal(model[prefix+'opening_balance']['cash'],sc['liquidity_bridge'][identity]['opening_cash']):
                    problem('accounting_bridge',identity+': bilancio opening non riconciliato al ledger capitale/cash')
                for j,row in enumerate(economics['rows']):
                    legal=sub if j<len(years) else t_sub;k=j if j<len(years) else 0
                    bridge=(sc['liquidity_bridge'] if j<len(years) else terminal['liquidity_bridge'])[identity]
                    if not equal(row['net_income'],legal['gaap_net_income'][k]) or not equal(row['cash_before_transfers'],legal['liquidity_before_transfers'][k]):
                        problem('accounting_bridge',identity+': utile/cash del business non riconciliati al ledger nel periodo '+str(j))
                    for cash in ('operating_cash','investing_cash','financing_cash','parent_fees_paid','parent_tax_paid'):
                        if not equal(row[cash],bridge[cash][k]):problem('liquidity_bridge',identity+': '+cash+' non riconciliato')
                last,continuing=economics['rows'][-2:]
                for key,value in last['closing_balance'].items():
                    if not equal(continuing['closing_balance'][key],value*(1+sc['terminal_growth'])):
                        problem('continuing_economics',identity+': saldo '+key+' non sostiene crescita continuing')
                if life:
                    growth=1+sc['terminal_growth']
                    # Defined continuing regime: constant unit economics/probabilities,
                    # new policies and aggregate balances/cash scale with population.
                    next_period={k:v if k.endswith('_per_policy') or k in ('mortality_rate','lapse_rate','investment_yield') else v*growth for k,v in periods[-1].items()}
                    second=project_life(continuing['closing_balance'],model[prefix+'product'],[next_period],
                        [t_sub['proposed_distribution'][0]*growth],[t_sub['proposed_contribution'][0]*growth],problem,computed_opening=True)
                    if second:
                        for k in ('net_income','cash_before_transfers'):
                            if not equal(second['rows'][0][k],continuing[k]*growth):problem('continuing_economics',identity+': secondo anno '+k+' non sostenibile')
                        for k,v in continuing['closing_balance'].items():
                            if not equal(second['closing_balance'][k],v*growth):problem('continuing_economics',identity+': secondo anno saldo '+k+' non sostenibile')
                        economics['second_continuing_year']=second['rows'][0]
            if any(r['funding_required']>sc['funding_capacity'][i] for i,r in enumerate(ledger['rows'])):
                problem('funding_capacity',scenario+': apporto necessario non finanziato')
            closing=model['opening_common_equity']+sum(sc['consolidated_income'])-sum(r['shareholder_net_distribution'] for r in ledger['rows'])
            checked=validate_terminal(terminal,cap,ledger,closing,sc,problem,terminal_income=sc['terminal_income']) if not bound['issues'] else None
            if checked:
                tv=checked['rows'][0]['shareholder_net_distribution']/(cap['ke']-sc['terminal_growth'])
                if not equal(tv,cap['terminal_equity']):problem('terminal_ledger',scenario+': valore continuing non riconciliato al cash distribuibile e Ke/g')
            results[scenario]={'fair_value_per_share':ledger['fair_value_per_share'],'entities':entities,
                'rows':ledger['rows'],'terminal_equity':cap['terminal_equity'],'value_basis':'equity'}
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='insurance_life' if life else 'insurance_pc')
