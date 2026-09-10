"""Documented recognized-asset/cash bridge to the existing regulated DDM engine."""
from .documented_inputs import bind_inputs,finish_documented
from .operating_adapter import SCHEMA as OPERATING_SCHEMA
from .dcf_quality import SCENARIOS,_finite,_date,_text
from .capital_inputs import equal

SCHEMA={k:OPERATING_SCHEMA[k] for k in ('perimeter','calendar','quotation','shares')}
SCHEMA.update({
    'regime':('regulatory_regime','contract','scope','future','contract','model'),
    'claims':('cash_distributions','contract','scope','future','contract','model'),
    'opening_rab':('recognized_regulatory_base','money','regulatory','opening','number','model'),
    'opening_debt':('cash_distributions','money','cash','opening','number','model'),
    'opening_cash':('cash_distributions','money','cash','opening','number','model'),
    'opening_working_capital':('cash_distributions','money','cash','opening','number','model'),
    'opening_unrecognized_investment':('network_investment','money','regulatory','opening','number','model'),
    'ke':('discount_rate_inputs','ratio','valuation','future','number','scenario'),
    'terminal_growth':('terminal_assumptions','ratio','valuation','future','number','scenario'),
    'continuing':('terminal_assumptions','contract','regulatory','terminal','contract','scenario'),
})
PATHS=('allowed_return','indexation','recognized_capex','regulatory_depreciation','disposals_rab','disposal_realization_multiple',
       'cash_capex','book_depreciation','allowed_opex','cash_opex','incentives','tax_allowance',
       'cash_tax','interest_paid','interest_received','working_capital_change','disposal_cash',
       'debt_issued','debt_repaid','minimum_cash','permitted_distribution','funding_capacity')
for _driver in PATHS:
    _field='allowed_return' if _driver in ('allowed_return','indexation') else 'network_investment' if _driver in ('recognized_capex','regulatory_depreciation','disposals_rab','cash_capex','book_depreciation','disposal_realization_multiple') else 'cash_distributions'
    SCHEMA[_driver]=(_field,'ratio' if _field=='allowed_return' or _driver=='disposal_realization_multiple' else 'money',
        'regulatory' if _field in ('allowed_return','network_investment') else 'cash','future','path','scenario')


def generate_rab(bundle,*,output_dir,metadata):
    from .dcf_rab import project_documented_rab,_fv_ddm_regolato
    bound=bind_inputs(bundle,SCHEMA); problem=bound['problem']; result={}
    if not bound['issues']:
        model=bound['values']['model']; regime=model['regime']; calendar=bound['calendar']
        keys={'regulator','jurisdiction','decision_id','review_start','review_end','return_basis','tax_basis','return_base'}
        if set(regime)!=keys or any(not _text(v) for v in regime.values()):
            problem('regime','Regime, regolatore e calendario della decisione completi richiesti')
        else:
            regulator=[r['value'] for r in bundle['decision']['evidence'] if r.get('field')=='regulator']
            if regulator!=[regime['regulator']]:problem('regime','Regolatore non riconciliato alla classificazione acquisita')
            start,end=_date(regime['review_start']),_date(regime['review_end'])
            last=_date(calendar['periods'][-1]['end'])
            if not start or not end or start>_date(calendar['periods'][0]['start']) or (end.year,end.month,end.day)<(last.year+1,last.month,last.day):
                problem('regime','Regime non copre forecast e anno continuing; acquisire successiva decisione/assunzione esplicita')
            if regime['return_basis'] not in ('real','nominal') or regime['tax_basis']!='pretax' or regime['return_base']!='opening_recognized':
                problem('regime','Supporto solo pre-tax su base riconosciuta opening: vanilla/post-tax/average base richiedono riconciliazione specifica')
        claims={'debt_basis':'all_interest_bearing_claims','equity_basis':'ordinary_only_no_other_senior_or_minority_claims',
                'distribution_policy':'full_sweep_after_minimum_cash','ownership_policy':'pro_rata_existing_shareholders'}
        if model['claims']!=claims:problem('claims','Riconciliare claims, minoranze, policy cash e ownership; struttura non supportata')
        if model['opening_rab']<=0 or model['shares']<=0 or any(model[k]<0 for k in ('opening_debt','opening_cash','opening_working_capital','opening_unrecognized_investment')):
            problem('opening_rab','Base riconosciuta/azioni positive e cash/debito non negativi richiesti')
        previous=_date(calendar['valuation_date'])
        for p in calendar['periods']:
            end=_date(p['end'])
            if (end.year,end.month,end.day)!=(previous.year+1,previous.month,previous.day):
                problem('calendar','RAB richiede periodi annuali interi; nessuna annualizzazione implicita')
            previous=end
        for scenario in SCENARIOS:
            sc=bound['values'][scenario]; tail=sc['continuing']
            if set(tail)!=set(PATHS) or any(not _finite(v) for v in tail.values()):
                problem('continuing',scenario+': driver completi e numerici per anno continuing richiesti');continue
            periods=[{k:sc[k][i] for k in PATHS} for i in range(len(bound['times']))]+[tail]
            if sc['ke']<=max(0,sc['terminal_growth']) or sc['terminal_growth']<=-1:
                problem('ke',scenario+': Ke/g non ammissibili, nessun clamp');continue
            for p in periods:
                if regime.get('return_basis')=='nominal' and p['indexation']!=0:
                    problem('indexation',scenario+': ritorno nominale piu indicizzazione duplica inflazione')
                if p['indexation']<=-1 or p['tax_allowance']!=0:
                    problem('regime',scenario+': indicizzazione non valida o tax allowance aggiuntiva al pre-tax non supportata')
                if not equal(p['disposal_cash'],p['disposals_rab']*p['disposal_realization_multiple']):
                    problem('disposal_cash',scenario+': proventi non riconciliati a base dismessa e valore di realizzo documentato')
                if any(p[k]<0 for k in PATHS if k not in ('indexation','incentives','working_capital_change')):
                    problem('cash_distributions',scenario+': costo/base/finanziamento negativo non supportato')
            if bound['issues']:continue
            rows=project_documented_rab({k:model['opening_'+k] for k in ('rab','debt','cash','working_capital','unrecognized_investment')},periods)
            for i,row in enumerate(rows):
                if row['closing_rab']<=0 or row['closing_debt']<0 or row['closing_working_capital']<0 or row['shareholder_net_distribution']>row['permitted_distribution'] or row['funding_required']>row['funding_capacity']:
                    problem('cash_distributions',scenario+': saldi/funding/distribuzione non sostenibili nel periodo '+str(i))
                if row['closing_unrecognized_investment']<0:
                    problem('recognized_capex',scenario+': investimento riconosciuto eccede cassa investita e stock pagato pregresso nel periodo '+str(i))
            last,terminal=rows[-2:]; growth=1+sc['terminal_growth']
            for key in ('rab','cash','debt','working_capital','unrecognized_investment'):
                if not equal(terminal['closing_'+key],last['closing_'+key]*growth):
                    problem('continuing',scenario+': '+key+' non sostiene crescita continuing')
            if terminal['shareholder_net_distribution']<=0:
                problem('continuing',scenario+': cash continuing non positivo o reinvestimento non coerente')
            try:
                fv=_fv_ddm_regolato({'documented_inputs':True,'ke':sc['ke'],'growth_lt':sc['terminal_growth'],
                    'discount_periods':bound['times'],'shares':model['shares'],
                    'cash_distributions':[r['shareholder_net_distribution'] for r in rows[:-1]],
                    'terminal_distribution':terminal['shareholder_net_distribution']})
            except ArithmeticError as exc:problem('ke',scenario+': '+str(exc));continue
            result[scenario]={'fair_value_per_share':fv,'rows':rows[:-1],'continuing':terminal,'value_basis':'equity',
                'income_basis':'Regulatory revenue less cash operating costs, book depreciation, cash interest and cash tax; not reported GAAP/IFRS income.'}
    return finish_documented(bundle,bound,result,metadata=metadata,output_dir=output_dir,engine='rab')
