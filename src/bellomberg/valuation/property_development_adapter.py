"""Permitted finite property phases feed common FCFF/APV and cash primitives."""
from .operating_adapter import SCHEMA as OPERATING_SCHEMA
from .documented_inputs import bind_inputs,finish_documented
from .finite_cashflows import loan_cashflows,finite_equity_value
from .capital_inputs import equal
from .dcf_quality import SCENARIOS,_finite,_text,_date

SCHEMA={k:OPERATING_SCHEMA[k] for k in ('perimeter','calendar','quotation','shares')}
SCHEMA.update({
    'policy':('project_funding','contract','scope','future','contract','model'),
    'projects':('project_inventory','contract','tax','opening','contract','model'),
    'opening_cash':('project_funding','money','cash','opening','number','model'),
    'opening_cash_buffer':('project_funding','money','cash','opening','number','model'),
    'debt_schedule':('project_funding','contract','tax','opening','contract','model'),
    'project_forecasts':('project_cash_flows','contract','tax','future','contract','scenario'),
    'central_cash_cost':('project_cash_flows','money','tax','future','path','scenario'),
    'tax_rate':('project_cash_flows','ratio','tax','future','path','scenario'),
    'minimum_cash':('project_funding','money','cash','future','path','scenario'),
    'funding':('project_funding','contract','tax','future','contract','scenario'),
    'ku':('discount_rate_inputs','ratio','valuation','future','number','scenario'),
    'tax_shield_discount_rate':('discount_rate_inputs','ratio','valuation','future','number','scenario'),
    'expected_tax_shield':('project_funding','money','cash','future','path','scenario'),
    'financing_cost_pv':('project_funding','money','valuation','future','number','scenario'),
})
POLICY={'ownership':'wholly_owned_ordinary_basic_pro_rata_existing_shareholders',
    'business':'finite_projects_all_sold_no_other_assets_or_claims',
    'timing':'construction_and_opex_at_start_completion_and_delivery_at_end_no_lag',
    'tax':'current_cash_tax_no_NOL_refund_limits_or_deferred_tax',
    'tax_group':'single_taxpayer_single_jurisdiction_full_current_project_profit_loss_offset',
    'interest':'documented_tax_capitalization_allocations_residual_current_deduction',
    'debt':'existing_fixed_rate_bullet_no_new_borrowing_fees_covenants_or_guarantees',
    'financing_cost_pv':'expected_after_tax_deadweight_cost_at_valuation_date_excluded_from_Ku_and_cashflows',
    'expected_shield':'source_estimated_expected_cash_tax_benefit_not_engine_default_risk_estimate',
    'cash_policy':'full_sweep_after_operating_buffer_release_at_finite_end'}
PROJECT_KEYS={'units_total','opening_unlevered_tax_basis','opening_tax_basis','opening_receivable','title','permits','unit_basis','price_basis','prepayments','tax_source','taxpayer_entity','tax_jurisdiction'}
PATHS={'construction_cash','units_delivered','price_per_lot','operating_cash_cost','closing_receivable','tax_capitalized_interest'}


def project_properties(projects,forecasts,central,problem,*,taxpayer):
    n=len(central);totals={k:[0.]*n for k in ('start_costs','end_receipts','taxable_unlevered','taxable_financed_before_interest','capitalized_interest')};out={}
    if not projects or set(projects)!=set(forecasts):
        problem('projects','Inventario e forecast devono coprire tutte le fasi');return None
    jurisdictions=set()
    for identity,p in projects.items():
        if (not _text(identity) or not isinstance(p,dict) or set(p)!=PROJECT_KEYS or type(p['units_total']) is not int or not _finite(p['units_total']) or p['units_total']<=0 or
                any(not _finite(p[k]) or p[k]<0 for k in ('opening_unlevered_tax_basis','opening_tax_basis','opening_receivable')) or
                p['opening_tax_basis']<p['opening_unlevered_tax_basis'] or not _text(p['tax_source']) or
                p['taxpayer_entity']!=taxpayer or not _text(p['tax_jurisdiction']) or
                any(p[k]!=v for k,v in {'title':'wholly_owned_freehold','permits':'unconditional_approved','unit_basis':'whole_saleable_lots',
                    'price_basis':'financial_currency_million_per_lot','prepayments':'none'}.items())):
            problem('projects',identity+': inventario/diritti/base fiscale fonte-specifica incompleti o non supportati');continue
        jurisdictions.add(p['tax_jurisdiction'])
        f=forecasts[identity]
        if (not isinstance(f,dict) or set(f)!=PATHS|{'remaining_construction_budget','completion_period'} or
                not _finite(f['remaining_construction_budget']) or f['remaining_construction_budget']<0 or
                type(f['completion_period']) is not int or not 1<=f['completion_period']<=n or
                any(not isinstance(f[k],list) or len(f[k])!=n or any(not _finite(v) or v<0 for v in f[k]) for k in PATHS) or
                any(type(v) is not int for v in f['units_delivered'])):
            problem('project_forecasts',identity+': budget, completamento e percorsi completi per ogni periodo richiesti');continue
        complete=f['completion_period'];units=p['units_total'];receivable=p['opening_receivable']
        unlevered=p['opening_unlevered_tax_basis'];financed=p['opening_tax_basis'];remaining=units;rows=[]
        if not equal(sum(f['construction_cash']),f['remaining_construction_budget']) or any(f['construction_cash'][complete:]) or any(f['tax_capitalized_interest'][complete:]):
            problem('project_forecasts',identity+': costo residuo/completamento/capitalizzazione non riconciliati')
        total_u=unlevered+sum(f['construction_cash']);total_l=financed+sum(f['construction_cash'])+sum(f['tax_capitalized_interest'])
        for i in range(n):
            sold=f['units_delivered'][i];revenue=sold*f['price_per_lot'][i]
            if sold>remaining or (sold and i+1<complete):problem('project_forecasts',identity+': consegna prima del completamento o oltre unita disponibili')
            cost=f['construction_cash'][i];capitalized=f['tax_capitalized_interest'][i]
            cogs_u=sold*total_u/units;cogs_l=sold*total_l/units
            unlevered+=cost-cogs_u;financed+=cost+capitalized-cogs_l;remaining-=sold
            closing=f['closing_receivable'][i];collections=receivable+revenue-closing
            if collections<0 or unlevered< -1e-8 or financed< -1e-8:problem('project_forecasts',identity+': crediti/incassi o inventario non riconciliati')
            totals['start_costs'][i]+=cost+f['operating_cash_cost'][i]
            totals['end_receipts'][i]+=collections
            totals['taxable_unlevered'][i]+=revenue-cogs_u-f['operating_cash_cost'][i]
            totals['taxable_financed_before_interest'][i]+=revenue-cogs_l-f['operating_cash_cost'][i]
            totals['capitalized_interest'][i]+=capitalized
            rows.append({'revenue':revenue,'collections':collections,'unlevered_cogs':cogs_u,'financed_cogs':cogs_l,
                'closing_unlevered_inventory':unlevered,'closing_tax_inventory':financed,'remaining_units':remaining})
            receivable=closing
        if remaining or not equal(unlevered,0) or not equal(financed,0) or receivable!=0:
            problem('project_forecasts',identity+': unita/inventario/crediti residui; niente terminale zero fittizio')
        out[identity]={'rows':rows,'closing_inventory':financed,'closing_receivable':receivable,'remaining_units':remaining}
    if len(jurisdictions)!=1:problem('projects','Unico contribuente e giurisdizione richiesti; niente compensazioni fiscali implicite fra SPV')
    if any(v<0 for v in central):problem('central_cash_cost','Costi centrali negativi non supportati')
    for i,cost in enumerate(central):
        totals['start_costs'][i]+=cost
        totals['taxable_unlevered'][i]-=cost;totals['taxable_financed_before_interest'][i]-=cost
    return totals,out


def generate_property_development(bundle,*,output_dir,metadata):
    bound=bind_inputs(bundle,SCHEMA);problem=bound['problem'];results={}
    if not bound['issues']:
        model=bound['values']['model']
        if model['policy']!=POLICY:problem('policy','Perimetro finito/pro-rata e convenzioni fiscali/temporali non supportati')
        previous=_date(bound['calendar']['valuation_date'])
        for p in bound['calendar']['periods']:
            end=_date(p['end'])
            if (end.year,end.month,end.day)!=(previous.year+1,previous.month,previous.day):problem('calendar','Progetti: esercizi annuali interi e pagamenti start/end richiesti')
            previous=end
        debt=loan_cashflows(model['debt_schedule'],bound['calendar'],problem)
        for scenario in SCENARIOS:
            sc=bound['values'][scenario]
            projected=project_properties(model['projects'],sc['project_forecasts'],sc['central_cash_cost'],problem,taxpayer=bound['perimeter']['entity'])
            if projected is None:continue
            economics,projects=projected
            try:
                value=finite_equity_value(model,sc,economics,debt,bound['calendar'],bound['times'],problem)
            except ArithmeticError as exc:
                problem('project_funding','Calcolo finanziario non rappresentabile: '+type(exc).__name__+'; acquisire driver numerici validi')
                continue
            if value:results[scenario]={**value,'projects':projects}
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='property_development')
