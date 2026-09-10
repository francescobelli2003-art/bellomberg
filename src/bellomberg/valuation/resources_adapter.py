"""Source-bound producing reserves feed finite FCFF/APV, never a perpetuity."""
from .property_development_adapter import SCHEMA as PROJECT_SCHEMA,POLICY as PROJECT_POLICY
from .documented_inputs import bind_inputs,finish_documented
from .finite_cashflows import loan_cashflows,finite_equity_value
from .capital_inputs import equal
from .dcf_quality import SCENARIOS,_finite,_text,_date

SCHEMA={k:tuple('enterprise_equity_bridge' if v=='project_funding' else 'resource_costs' if v=='project_cash_flows' else v for v in spec)
        for k,spec in PROJECT_SCHEMA.items() if k not in ('projects','project_forecasts')}
SCHEMA.update({'assets':('reserves_resources','contract','resource','opening','contract','model'),
    'forecasts':('production_prices','contract','resource','future','contract','scenario'),
    'closure':('closure_obligations','contract','resource','future','contract','scenario')})
POLICY={**PROJECT_POLICY,'business':'finite_producing_assets_no_JV_PSC_hedges_inventory_receivables_or_other_claims',
    'timing':'opex_capex_at_start_production_royalty_tax_closure_at_end',
    'interest':'current_deduction_no_capitalized_borrowing_costs',
    'capital_allowances':'documented_tax_schedule_current_deductions_no_deferred_tax',
    'restoration':'nominal_cash_at_closure_tax_deductible_not_in_tax_basis_or_allowances_no_double_liability',
    'production_basis':'gross_saleable_before_cash_revenue_royalty_no_recovery_loss_all_sold_and_collected_same_period',
    'decline_basis':'year_over_year_change_in_annual_saleable_volume_not_instantaneous_flow_rate',
    'capex_basis':'sustaining_currently_producing_assets_no_reserve_additions_or_development',
    'cash_basis':'unrestricted_no_escrow_guarantees_or_restoration_funds',
    'economic_limit':'nonnegative_operating_margin_for_each_positive_production_period'}
ASSET_KEYS={'recoverable_reserves','opening_annual_output','reserve_class','reserve_standard','reserve_report',
    'volume_unit','price_unit','rights','rights_expiry','taxpayer_entity','tax_jurisdiction','opening_tax_basis','tax_source','residual_policy'}
PATHS={'decline','price','unit_cash_cost','fixed_cash_cost','royalty_rate','capex','tax_allowance'}


def project_resources(assets,forecasts,closures,central,calendar,perimeter,problem):
    n=len(central);totals={k:[0.]*n for k in ('start_costs','end_receipts','taxable_unlevered','taxable_financed_before_interest','capitalized_interest')};out={}
    if not assets or set(assets)!=set(forecasts) or set(assets)!=set(closures):
        problem('reserves_resources','Inventario, forecast e chiusura devono coprire tutti gli asset');return None
    jurisdictions=set()
    unit_prices={'million_barrels':perimeter['currency']+'_per_barrel','million_tonnes':perimeter['currency']+'_per_tonne'}
    for identity,a in assets.items():
        if (not _text(identity) or not isinstance(a,dict) or set(a)!=ASSET_KEYS or
                any(not _finite(a[k]) or a[k]<0 for k in ('recoverable_reserves','opening_annual_output','opening_tax_basis')) or
                a['recoverable_reserves']<=0 or a['opening_annual_output']<=0 or
                any(not _text(a[k]) for k in ('reserve_standard','reserve_report','tax_jurisdiction','tax_source')) or
                a['reserve_class']!='proved_economically_recoverable' or a['rights']!='wholly_owned_unconditional_producing' or
                a['residual_policy']!='abandoned_without_proceeds_or_future_recovery' or
                a['taxpayer_entity']!=perimeter['entity'] or not _text(a['volume_unit']) or a['volume_unit'] not in unit_prices or
                a['price_unit']!=unit_prices[a['volume_unit']] or not _date(a['rights_expiry'])):
            problem('reserves_resources',str(identity)+': riserve/diritti/unita/perimetro fiscale incompleti o non supportati');continue
        jurisdictions.add(a['tax_jurisdiction']);f=forecasts[identity];c=closures[identity]
        if (not isinstance(f,dict) or set(f)!=PATHS or
                any(not isinstance(f[k],list) or len(f[k])!=n or any(not _finite(v) or v<0 for v in f[k]) for k in PATHS) or
                any(v>1 for k in ('decline','royalty_rate') for v in f[k])):
            problem('production_prices',str(identity)+': percorsi produzione/prezzi/costi/aliquote incompleti');continue
        if (not isinstance(c,dict) or set(c)!={'period','cash_cost','remaining_abandoned','obligations','tax','cost_source'} or
                type(c['period']) is not int or not 1<=c['period']<=n or
                any(not _finite(c[k]) or c[k]<0 for k in ('cash_cost','remaining_abandoned')) or
                c['obligations']!='all_restoration_paid_at_closure_no_other_claims' or c['tax']!='deductible_when_paid' or
                not _text(c['cost_source'])):
            problem('closure_obligations',str(identity)+': data, costo e perimetro fiscale della chiusura richiesti');continue
        remaining=a['recoverable_reserves'];production=a['opening_annual_output'];tax_basis=a['opening_tax_basis'];rows=[]
        for i in range(n):
            production*=1.-f['decline'][i];remaining-=production
            if remaining< -1e-8:problem('reserves_resources',str(identity)+': estrazione oltre riserve recuperabili')
            if production and (calendar['periods'][i]['end']>a['rights_expiry'] or i+1>c['period']):
                problem('production_prices',str(identity)+': produzione oltre diritti o dopo chiusura')
            if i+1>c['period'] and any(f[k][i] for k in ('capex','fixed_cash_cost','tax_allowance')):
                problem('closure_obligations',str(identity)+': costi o deduzioni dopo chiusura non supportati')
            revenue=production*f['price'][i];opex=production*f['unit_cash_cost'][i]+f['fixed_cash_cost'][i]
            royalty=revenue*f['royalty_rate'][i];restoration=c['cash_cost'] if i+1==c['period'] else 0.
            if production and revenue-opex-royalty< -1e-8:
                problem('production_prices',str(identity)+': estrazione antieconomica; acquisire piano con limite economico/chiusura coerente')
            tax_basis+=f['capex'][i]-f['tax_allowance'][i]
            if tax_basis< -1e-8:problem('resource_costs',str(identity)+': deduzioni oltre base fiscale disponibile')
            taxable=revenue-opex-royalty-f['tax_allowance'][i]-restoration
            totals['start_costs'][i]+=opex+f['capex'][i]
            totals['end_receipts'][i]+=revenue-royalty-restoration
            totals['taxable_unlevered'][i]+=taxable;totals['taxable_financed_before_interest'][i]+=taxable
            rows.append({'production':production,'revenue':revenue,'operating_cost':opex,'royalty':royalty,
                'capex':f['capex'][i],'tax_allowance':f['tax_allowance'][i],'restoration_paid':restoration,
                'remaining_reserves':remaining,'closing_tax_basis':tax_basis})
        if not equal(remaining,c['remaining_abandoned']) or not equal(tax_basis,0.):
            problem('reserves_resources',str(identity)+': riserve residue/base fiscale non riconciliate alla chiusura finita')
        out[identity]={'rows':rows,'remaining_reserves':remaining,'abandoned_reserves':c['remaining_abandoned'],
            'closing_tax_basis':tax_basis,'reserve_report':a['reserve_report']}
    if len(jurisdictions)!=1:problem('reserves_resources','Unico contribuente e giurisdizione richiesti')
    if any(v<0 for v in central):problem('central_cash_cost','Costi centrali negativi non supportati')
    for i,cost in enumerate(central):
        totals['start_costs'][i]+=cost
        totals['taxable_unlevered'][i]-=cost;totals['taxable_financed_before_interest'][i]-=cost
    return totals,out


def generate_resources(bundle,*,output_dir,metadata):
    bound=bind_inputs(bundle,SCHEMA);problem=bound['problem'];results={}
    if not bound['issues']:
        model=bound['values']['model']
        if model['policy']!=POLICY:problem('policy','Perimetro risorse/tax/funding non supportato; acquisire convenzioni esplicite')
        previous=_date(bound['calendar']['valuation_date'])
        for p in bound['calendar']['periods']:
            end=_date(p['end'])
            if (end.year,end.month,end.day)!=(previous.year+1,previous.month,previous.day):problem('calendar','Produzione/finanziamenti richiedono esercizi annuali interi')
            previous=end
        debt=loan_cashflows(model['debt_schedule'],bound['calendar'],problem)
        for scenario in SCENARIOS:
            sc=bound['values'][scenario]
            try:
                projected=project_resources(model['assets'],sc['forecasts'],sc['closure'],sc['central_cash_cost'],bound['calendar'],bound['perimeter'],problem)
                if projected is None:continue
                economics,assets=projected
                value=finite_equity_value(model,sc,economics,debt,bound['calendar'],bound['times'],problem)
            except ArithmeticError as exc:
                problem('resource_costs','Calcolo risorse non rappresentabile: '+type(exc).__name__);continue
            if value:results[scenario]={**value,'assets':assets,'end_receipts_basis':'sales_less_royalty_and_restoration'}
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='resources_asset_dcf')
