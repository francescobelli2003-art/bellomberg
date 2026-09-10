"""Stabilized property NAV with a distinct, reconciled forward-year cash bridge."""
from copy import deepcopy
from .nav_adapter import COMMON,COMPONENTS
from .documented_inputs import bind_inputs,finish_documented
from .capital_inputs import equal
from .dcf_quality import SCENARIOS,_finite,_date,_text

SCHEMA={k:v for k,v in COMMON.items() if k not in ('nav_target','target_basis')}
SCHEMA.update({
    'forward_year':('ffo_affo_bridge','contract','calendar','opening','contract','model'),
    'property_scope':('property_nav','contract','scope','opening','contract','model'),
    'policy':('property_nav','contract','scope','opening','contract','model'),
    'components':('property_debt','contract','GAAP','opening','contract','model'),
    'debt_schedule':('property_debt','contract','GAAP','opening','contract','model'),
    'property_income':('property_income','contract','GAAP','future','contract','scenario'),
    'property_capex':('property_capex','contract','GAAP','future','contract','scenario'),
    'property_values':('property_nav','contract','GAAP','future','contract','scenario'),
    'ffo_bridge':('ffo_affo_bridge','contract','GAAP','future','contract','scenario'),
    'funding':('property_debt','contract','GAAP','future','contract','scenario'),
    'central_cost_multiple':('property_nav','multiple','valuation','future','number','scenario'),
    'nav_target':('valuation_target','ratio','valuation','future','number','scenario'),
    'target_basis':('valuation_target','text','valuation','future','text','scenario'),
})
POLICY={'portfolio':'stabilized_no_acquisitions_disposals_or_development',
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
    'other_claims':'opening_other_liabilities_fees_tax_payables_paid_at_FYend'}
INCOME={'area_m','annual_rent_per_area','occupancy','other_income','recoveries','cash_operating_costs',
    'cash_lease_incentives','straight_line_rent','property_depreciation'}
CAPEX={'maintenance','tenant_improvements','leasing_costs','expansion','backlog'}
FFO={'net_income','ffo','affo','central_cash_cost','cash_tax','asset_sale_gains','property_impairments'}


def _numbers(value,keys,*,signed=()):
    return isinstance(value,dict) and set(value)==keys and all(_finite(v) and (k in signed or v>=0) for k,v in value.items())


def _debt(schedule,opening,forward_end,problem):
    face=settlement=interest=maturing=0.;due={}
    for identity,loan in schedule.items():
        if (not _text(identity) or not isinstance(loan,dict) or set(loan)!={'face_value','settlement_value','annual_coupon','maturity','basis'} or
                not all(_finite(loan[k]) and loan[k]>=0 for k in ('face_value','settlement_value','annual_coupon')) or
                loan['face_value']<=0 or loan['settlement_value']<loan['face_value'] or
                loan['basis']!='fixed_rate_bullet_full_year_coupon_settlement_at_or_above_par' or not _date(loan['maturity'])):
            problem('debt_schedule','Prestito bullet fixed-rate, capitale, settlement >= par, coupon e scadenza richiesti');continue
        maturity=_date(loan['maturity'])
        if maturity<=opening or maturity<forward_end:
            problem('debt_schedule',identity+': scadenza infrannuale/scaduta richiede calendario eventi e coupon esatto');continue
        face+=loan['face_value'];settlement+=loan['settlement_value'];interest+=loan['face_value']*loan['annual_coupon']
        if maturity==forward_end:due[identity]=loan['face_value'];maturing+=loan['face_value']
    return {'face':face,'settlement':settlement,'interest':interest,'maturing':maturing,'due':due}


def _funding(funding,debt,forward_end,problem):
    fields={'minimum_cash','shareholder_distribution','refinancing','equity_contribution','equity_commitment_id','equity_available_date'}
    if (set(funding)!=fields or any(not _finite(funding[k]) or funding[k]<0 for k in ('minimum_cash','shareholder_distribution','equity_contribution')) or
            not isinstance(funding['refinancing'],dict) or not _text(funding['equity_commitment_id']) or
            funding['equity_available_date']!=forward_end.isoformat()):
        problem('funding','Piano cash completo, fonti finanziamento e date esatte richiesti');return None
    if (funding['equity_contribution']==0)!=(funding['equity_commitment_id']=='none'):
        problem('funding','Apporto pro-rata richiede impegno identificato; nessun capitale futuro presunto')
    total=0.
    for identity,refi in funding['refinancing'].items():
        if (identity not in debt['due'] or not isinstance(refi,dict) or
                set(refi)!={'amount','commitment_id','available_date','new_maturity','terms'} or
                not _finite(refi['amount']) or not 0<refi['amount']<=debt['due'][identity] or
                not _text(refi['commitment_id']) or refi['commitment_id']=='none' or
                refi['available_date']!=forward_end.isoformat() or not _date(refi['new_maturity']) or
                _date(refi['new_maturity'])<=forward_end or refi['terms']!='committed_at_face_no_fee_FYend_draw'):
            problem('funding',identity+': rifinanziamento non riconciliato a scadenza e linea documentata');continue
        total+=refi['amount']
    return total+funding['equity_contribution']


def generate_property(bundle,*,output_dir,metadata):
    from .dcf_mnav import compute_mnav_values
    candidates=[r['value'] for r in bundle['case']['records'] if r.get('driver')=='forward_year' and r.get('scenario')=='model']
    period=candidates[0] if len(candidates)==1 else {}
    bound=bind_inputs(bundle,SCHEMA,horizon='snapshot',forward_period=period);problem=bound['problem'];results={}
    if not bound['issues']:
        model=bound['values']['model'];scope=model['property_scope'];components=model['components']
        if not scope or any(not _text(k) or not isinstance(v,dict) or set(v)!={'tenure','ownership_fraction','area_unit'} or
                not _finite(v['ownership_fraction']) or v!={'tenure':'freehold','ownership_fraction':1.,'area_unit':'million square metres'} for k,v in scope.items()):
            problem('property_scope','Immobili freehold interamente posseduti, perimetro e unita espliciti richiesti')
        if model['policy']!=POLICY:problem('policy','Convenzioni NOI/caprate/capex/FFO/claims non supportate')
        if not _numbers(components,COMPONENTS-{'gross_assets'}) or components.get('preferred')!=0 or components.get('equity_adjustments')!=0 or model['shares']<=0:
            problem('components','Cash/claims completi richiesti; preferred/diluizione/adjustment generici non supportati')
        if bound['issues']:return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='property_nav')
        end=_date(period['end']);debt=_debt(model['debt_schedule'],_date(bound['calendar']['valuation_date']),end,problem)
        if not equal(debt['settlement'],components['debt']):problem('components','Debito NAV non riconciliato ai settlement value dei prestiti; niente duplicazioni')
        for scenario in SCENARIOS:
            sc=bound['values'][scenario];income=sc['property_income'];capex=sc['property_capex'];values=sc['property_values'];bridge=sc['ffo_bridge']
            if set(income)!=set(scope) or set(capex)!=set(scope) or set(values)!=set(scope):
                problem('property_scope',scenario+': copertura immobili incompleta');continue
            if not _numbers(bridge,FFO,signed=('net_income','ffo','affo')) or bridge['asset_sale_gains']!=0 or bridge['property_impairments']!=0:
                problem('ffo_bridge',scenario+': ponte completo GAAP/FFO/AFFO richiesto; vendite/impairment/altre rettifiche richiedono proiezione specifica');continue
            if sc['central_cost_multiple']<=0 or sc['nav_target']<=0 or sc['target_basis']!='equity_nav':
                problem('nav_target',scenario+': target equity NAV e multiple costi centrali positivi documentati richiesti');continue
            gross=noi=straight=depreciation=recurring=backlog=0.;assets={}
            for identity in scope:
                p,c,v=income[identity],capex[identity],values[identity]
                if not _numbers(p,INCOME,signed=('straight_line_rent',)) or not _numbers(c,CAPEX) or not 0<=p['occupancy']<=1 or c['expansion']!=0:
                    problem('property_income',identity+': driver rent/capex incompleti o sviluppo non modellato');continue
                if (not isinstance(v,dict) or set(v)!={'cap_rate','stabilized_noi','basis','comparable_basis'} or
                        not _finite(v['cap_rate']) or v['cap_rate']<=0 or not _finite(v['stabilized_noi']) or
                        v['basis']!=POLICY['cap_rate_basis'] or not _text(v['comparable_basis'])):
                    problem('property_values',identity+': cap rate, convenzione e comparabilita documentati richiesti');continue
                cash_noi=p['area_m']*p['annual_rent_per_area']*p['occupancy']+p['other_income']+p['recoveries']-p['cash_operating_costs']-p['cash_lease_incentives']
                upkeep=c['maintenance']+c['tenant_improvements']+c['leasing_costs']
                if cash_noi<=upkeep or not equal(cash_noi,v['stabilized_noi']):
                    problem('property_values',identity+': NOI non stabilizzato o insufficiente al capex ricorrente; acquisire proiezione alternativa')
                value=cash_noi/v['cap_rate']-c['backlog']
                if value<=0:problem('property_values',identity+': backlog assorbe il valore; acquisire base alternativa')
                gross+=value;noi+=cash_noi;straight+=p['straight_line_rent'];depreciation+=p['property_depreciation'];recurring+=upkeep;backlog+=c['backlog']
                assets[identity]={'cash_noi':cash_noi,'recurring_capex':upkeep,'backlog':c['backlog'],'asset_value':value}
            ni=noi+straight-depreciation-bridge['central_cash_cost']-debt['interest']-bridge['cash_tax']
            ffo=ni+depreciation;affo=ffo-straight-recurring
            for k,val in (('net_income',ni),('ffo',ffo),('affo',affo)):
                if not equal(val,bridge[k]):problem('ffo_bridge',scenario+': '+k+' non riconciliato')
            funds=_funding(sc['funding'],debt,end,problem)
            if funds is None:continue
            if components['cash']-backlog<sc['funding']['minimum_cash']:
                problem('property_capex',scenario+': cura backlog opening non finanziata prima del NOI stabilizzato; acquisire tempi, funding e lease gap')
            other=sum(components[k] for k in ('other_liabilities','accrued_fees','distributions_payable','tax'))
            cash=components['cash']+affo-backlog-debt['maturing']-other+funds-sc['funding']['shareholder_distribution']
            if cash<sc['funding']['minimum_cash']:problem('funding',scenario+': cassa insufficiente dopo capex, scadenze e distribuzioni')
            adjusted=deepcopy(components);adjusted.update(gross_assets=gross,
                equity_adjustments=-(bridge['central_cash_cost']+bridge['cash_tax'])*sc['central_cost_multiple'])
            nav=compute_mnav_values({'documented_inputs':True,'components':adjusted,'shares':model['shares'],
                'nav_target':sc['nav_target'],'target_basis':sc['target_basis']})
            results[scenario]={'fair_value_per_share':nav['fair_value_nav'],'value_basis':'equity','nav':nav,
                'assets':assets,'cash_noi':noi,'gaap_net_income':ni,'ffo':ffo,'affo':affo,
                'ffo_affo_basis':POLICY['affo_basis'],'forward_closing_cash':cash,'debt':debt,
                'valuation_basis':'Opening NAV; forward FY only reconciles income/cash. Closing cash is not added to NAV.'}
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='property_nav')
