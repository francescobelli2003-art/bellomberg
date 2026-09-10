"""Common-equity bank/credit valuation with an audited legal-capital cash bridge."""
from .operating_adapter import SCHEMA as OPERATING_SCHEMA
from .documented_inputs import finish_documented
from .capital_inputs import bind_capital_inputs, assemble_capital, validate_constraints, equal, validate_terminal
from .distributable_equity import project_distributable_equity
from .dcf_quality import SCENARIOS, _finite

SCHEMA={k:OPERATING_SCHEMA[k] for k in ('perimeter','calendar','quotation')}
SCHEMA['legal_structure']=('regulatory_capital','contract','scope','future','contract','model')
for _key in ('opening_common_equity','opening_parent_equity','opening_consolidation_adjustments','opening_intangibles'):
    SCHEMA[_key]=('tangible_book_equity','money','GAAP','opening','number','model')
EARNINGS={'net_interest_income':1,'fee_income':1,'operating_expenses':-1,'credit_losses':-1,
          'taxes':-1,'other_income':1,'preferred_and_minorities':-1,'other_comprehensive_income':1}
for _key in EARNINGS:SCHEMA[_key]=('equity_return_path','money','GAAP','future','path','scenario')
for _key in ('terminal_roe','terminal_growth'):SCHEMA[_key]=('terminal_assumptions','ratio','valuation','future','number','scenario')
SCHEMA.update({
    'funding_capacity':('capital_retention','money','cash','future','path','scenario'),
    'ownership_policy':('capital_retention','text','scope','future','text','scenario'),
    'capital_constraints':('regulatory_capital','contract','statutory','future','contract','scenario'),
    'liquidity_bridge':('legal_entity_liquidity','contract','cash','future','contract','scenario'),
    'terminal_ledger':('terminal_assumptions','contract','valuation','terminal','contract','scenario'),
})


def generate_bank(bundle,*,output_dir,metadata):
    from .dcf_bank import _fv_residual_income, _fv_ptbv
    bound=bind_capital_inputs(bundle,SCHEMA); problem=bound['problem']; results={}
    if not bound['issues']:
        model=bound['values']['model']; n=len(bound['times'])
        if model['opening_common_equity']<=0 or not 0<=model['opening_intangibles']<model['opening_common_equity']:
            problem('opening_common_equity','Common book positivo e intangibili riconciliati richiesti')
        for scenario in SCENARIOS:
            sc=bound['values'][scenario]; cap=assemble_capital(sc,bound['legal_ids'])
            if sc['ownership_policy']!='pro_rata_existing_shareholders':
                problem('ownership_policy',scenario+': variazione ownership non supportata: acquisire ponte diluizione esplicito')
            if cap['discount_periods']!=bound['times']:
                problem('capital.discount_periods',scenario+': sconto non riconciliato al calendario')
            if any(v!=0 for v in sc['other_comprehensive_income']):
                problem('other_comprehensive_income',scenario+': OCI/FX non zero richiede un ponte common-equity completo; FV n.d.')
            if any(v<0 for v in sc['funding_capacity']):problem('funding_capacity',scenario+': capacita negativa')
            if not equal(model['opening_common_equity'],model['opening_parent_equity']+model['opening_consolidation_adjustments']+sum(s['opening_gaap_equity'] for s in cap['subsidiaries'])):
                problem('opening_common_equity',scenario+': saldi entita/parent/eliminazioni non riconciliati')
            income=[sum(sc[k][i]*sign for k,sign in EARNINGS.items()) for i in range(n)]
            ledger=project_distributable_equity(cap,income,[int(p['end'][:4]) for p in bound['calendar']['periods']])
            if ledger['status']!='CALCOLABILE':
                for issue in ledger['issues']:problem('capital_retention',scenario+': '+issue)
                continue
            validate_constraints(cap,sc['capital_constraints'],sc['liquidity_bridge'],problem)
            distributions=[r['shareholder_net_distribution'] for r in ledger['rows']]
            if any(r['funding_required']>sc['funding_capacity'][i] for i,r in enumerate(ledger['rows'])):
                problem('funding_capacity',scenario+': apporto necessario eccede fondi documentati')
            book=model['opening_common_equity']+sum(income)-sum(distributions)
            ke=cap['ke']; g=sc['terminal_growth']; roe=sc['terminal_roe']
            if not book>0 or not ke>max(g,0) or not roe>g or g<=-1:
                problem('terminal_roe',scenario+': book/Ke/ROE/g non sostenibili; nessun clamp');continue
            tv=_fv_ptbv({'documented_inputs':True,'bvps':book/cap['shares_m'],'ke':ke,'growth_lt':g,'roe_terminal':roe})*cap['shares_m']
            if not equal(tv,cap['terminal_equity']):problem('capital.terminal_equity',scenario+': TV non riconciliato a book finale, ROE e crescita')
            if not bound['issues']:validate_terminal(sc['terminal_ledger'],cap,ledger,book,sc,problem,terminal_income=book*sc['terminal_roe'])
            spec={'documented_inputs':True,'book_value':model['opening_common_equity'],'shares':cap['shares_m'],
                  'ke':ke,'net_income':income,'distributions':distributions,'discount_periods':bound['times'],'terminal_equity':tv}
            try:fv=_fv_residual_income(spec)
            except ArithmeticError as exc:problem('discount_rate_inputs',scenario+': '+str(exc));continue
            if not equal(fv*cap['shares_m'],ledger['equity_value']):problem('capital_retention',scenario+': RI e cash equity non riconciliati')
            results[scenario]={'fair_value_per_share':fv,'closing_common_equity':book,
                'opening_tangible_equity':model['opening_common_equity']-model['opening_intangibles'],
                'residual_income_value':fv*cap['shares_m'],'cash_equity_value':ledger['equity_value'],
                'terminal_common_equity_value':tv,'rows':ledger['rows'],'value_basis':'equity'}
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='bank')
