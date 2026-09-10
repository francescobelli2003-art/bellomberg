"""Finite licensed-development decision tree with cash and ownership per event."""
from copy import deepcopy
from .resources_adapter import SCHEMA as RESOURCE_SCHEMA
from .documented_inputs import bind_inputs,finish_documented
from .finite_cashflows import loan_cashflows,finite_equity_value,funding_capacities
from .capital_inputs import equal
from .dcf_quality import SCENARIOS,_finite,_text,_date

SCHEMA={k:tuple('development_funding' if v=='enterprise_equity_bridge' else 'development_cash_flows' if v=='resource_costs' else v for v in spec)
        for k,spec in RESOURCE_SCHEMA.items() if k not in ('assets','forecasts','closure')}
SCHEMA.update({'asset':('asset_stages','contract','development','opening','contract','model'),
    'life':('asset_life','contract','development','future','contract','model'),
    'stage_schedule':('asset_stages','contract','development','future','contract','scenario'),
    'probabilities':('conditional_probabilities','contract','development','future','contract','scenario'),
    'commercial':('development_cash_flows','contract','development','future','contract','scenario'),
    'risk_contract':('conditional_probabilities','contract','development','future','contract','scenario')})
POLICY={'ownership':'ordinary_common_pro_rata_calls_or_fixed_price_new_equity_no_preferences_warrants_or_fees',
    'funding':'irrevocable_capacity_for_all_reachable_outcomes_terms_known_at_period_start',
    'business':'single_precommercial_licensor_no_manufacturing_capex_inventory_receivables_or_other_claims',
    'development':'sequential_conditional_success_failure_absorbing_continue_all_successful_stages',
    'timing':'cost_at_period_start_outcome_milestone_royalty_tax_wind_down_at_end',
    'tax':'single_taxpayer_current_cash_tax_all_R&D_and_paid_costs_deductible_no_NOL_refund_deferred_or_opening_tax_basis',
    'royalties':'incoming_on_licensee_net_sales_outgoing_on_incoming_royalty_receipts_no_lag',
    'risk':'modeled_technical_transition_risk_only_in_probabilities_not_rate_or_commercial_amounts',
    'cash':'unrestricted_full_sweep_after_buffer_release_on_failure_or_contract_end',
    'finance':'no_debt_interest_tax_shield_or_additional_financing_costs',
    'life':'patent_contract_economic_expiry_coincident_no_residual_assets_or_payments'}
ASSET_KEYS={'id','stage_order','rights','taxpayer_entity','tax_jurisdiction','tax_source','unit','price_unit'}
STAGE_KEYS={'period','cost','success_received','success_paid','failure_close_cost'}
COMMERCIAL={'units','net_price_per_unit','incoming_royalty_rate','outgoing_royalty_rate','cash_operating_cost'}


def validate_tree(model,sc,bound):
    problem=bound['problem'];n=len(bound['times']);asset=model['asset'];life=model['life']
    if (set(asset)!=ASSET_KEYS or not _text(asset['id']) or
            not isinstance(asset['stage_order'],list) or not asset['stage_order'] or
            any(not _text(v) for v in asset['stage_order']) or len(set(asset['stage_order']))!=len(asset['stage_order']) or
            asset['rights']!='wholly_owned_exclusive_license_no_other_assets_or_claims' or
            asset['taxpayer_entity']!=bound['perimeter']['entity'] or
            any(not _text(asset[k]) for k in ('tax_jurisdiction','tax_source')) or
            asset['unit']!='million_licensed_units' or asset['price_unit']!=bound['perimeter']['currency']+'_per_licensed_unit'):
        problem('asset_stages','Asset/stadi/diritti/unita o perimetro fiscale incompleti');return False
    last=bound['calendar']['periods'][-1]['end']
    if (set(life)!={'patent_expiry','contract_expiry','economic_expiry','basis','final_wind_down_cost','source'} or
            any(life[k]!=last for k in ('patent_expiry','contract_expiry','economic_expiry')) or
            life['basis']!='all_rights_and_royalties_end_together_no_residual_receipts' or not _text(life['source']) or
            not _finite(life['final_wind_down_cost']) or life['final_wind_down_cost']<0):
        problem('asset_life','Scadenze brevetto/contratto/economica e chiusura finale devono coincidere nel perimetro supportato');return False
    order=asset['stage_order'];schedule=sc['stage_schedule'];probs=sc['probabilities'];commercial=sc['commercial'];risk=sc['risk_contract']
    if set(schedule)!=set(order) or set(probs)!=set(order):
        problem('conditional_probabilities','Ogni stadio richiede probabilita condizionale e flussi, nessun ramo omesso');return False
    previous=0
    for identity in order:
        s=schedule[identity];q=probs[identity]
        if (not isinstance(s,dict) or set(s)!=STAGE_KEYS or type(s['period']) is not int or not previous<s['period']<n or
                any(not _finite(s[k]) or s[k]<0 for k in STAGE_KEYS-{'period'}) or not _finite(q) or not 0<=q<=1):
            problem('asset_stages',identity+': ordine annuale, costi/esiti o probabilita incompleti');return False
        previous=s['period']
    if (set(commercial)!=COMMERCIAL or any(not isinstance(commercial[k],list) or len(commercial[k])!=n or
            any(not _finite(v) or v<0 for v in commercial[k]) for k in COMMERCIAL) or
            any(v>1 for k in ('incoming_royalty_rate','outgoing_royalty_rate') for v in commercial[k]) or
            any(commercial['units'][:previous]) or any(commercial['cash_operating_cost'][:previous])):
        problem('development_cash_flows','Flussi commerciali completi, condizionali al successo e solo dopo ultimo esito richiesti');return False
    if (set(risk)!={'rate_excluding_modeled_technical_risk','modeled_transition_risk_addon','cashflow_basis'} or
            not _finite(risk['rate_excluding_modeled_technical_risk']) or not equal(risk['rate_excluding_modeled_technical_risk'],sc['ku']) or
            not _finite(risk['modeled_transition_risk_addon']) or risk['modeled_transition_risk_addon']!=0 or
            risk['cashflow_basis']!='conditional_on_full_success_not_probability_weighted'):
        problem('conditional_probabilities','Rischio transizione gia modellato: niente maggiorazione tasso o flussi gia ponderati');return False
    if any(v<0 for v in sc['central_cash_cost']):
        problem('central_cash_cost','Costi centrali negativi non supportati');return False
    if any(not 0<=v<=1 for v in sc['tax_rate']) or any(v<0 for v in sc['minimum_cash']):
        problem('development_funding','Aliquote e buffer fuori dominio anche nei periodi di rami irraggiungibili');return False
    return True


def _outcome_economics(model,sc,failed_stage):
    order=model['asset']['stage_order'];schedule=sc['stage_schedule']
    end=schedule[failed_stage]['period'] if failed_stage is not None else len(sc['tax_rate'])
    econ={k:[0.]*end for k in ('start_costs','end_receipts','taxable_unlevered','taxable_financed_before_interest','capitalized_interest')}
    for identity in order:
        stage=schedule[identity];i=stage['period']-1
        if i>=end:break
        econ['start_costs'][i]+=stage['cost']
        econ['end_receipts'][i]+=(-stage['failure_close_cost'] if identity==failed_stage else stage['success_received']-stage['success_paid'])
        if identity==failed_stage:break
    commercial=sc['commercial']
    for i in range(end):
        econ['start_costs'][i]+=sc['central_cash_cost'][i]
        if failed_stage is None:
            incoming=commercial['units'][i]*commercial['net_price_per_unit'][i]*commercial['incoming_royalty_rate'][i]
            outgoing=incoming*commercial['outgoing_royalty_rate'][i]
            econ['end_receipts'][i]+=incoming-outgoing
            econ['start_costs'][i]+=commercial['cash_operating_cost'][i]
        taxable=econ['end_receipts'][i]-econ['start_costs'][i]
        econ['taxable_unlevered'][i]=taxable;econ['taxable_financed_before_interest'][i]=taxable
    if failed_stage is None:
        expense=model['life']['final_wind_down_cost'];econ['end_receipts'][-1]-=expense
        econ['taxable_unlevered'][-1]-=expense;econ['taxable_financed_before_interest'][-1]-=expense
    return econ


def generate_development(bundle,*,output_dir,metadata):
    bound=bind_inputs(bundle,SCHEMA);problem=bound['problem'];results={}
    if not bound['issues']:
        model=bound['values']['model']
        if model['policy']!=POLICY:problem('policy','Perimetro rNPV/licenze/capitale/tax non supportato')
        if model['debt_schedule']!={'no_debt':True} or model['debt_schedule'].get('no_debt') is not True:
            problem('development_funding','Licensor rNPV supportato solo senza debito esplicitamente attestato')
        previous=_date(bound['calendar']['valuation_date'])
        for period in bound['calendar']['periods']:
            end=_date(period['end'])
            if (end.year,end.month,end.day)!=(previous.year+1,previous.month,previous.day):problem('calendar','Stadi/costi a confini di esercizi annuali interi richiesti')
            previous=end
        for scenario in SCENARIOS:
            sc=bound['values'][scenario]
            if not validate_tree(model,sc,bound):continue
            if any(sc['expected_tax_shield']) or sc['financing_cost_pv']:
                problem('development_funding','Nessun beneficio interessi/costo finanziario ammesso nel perimetro senza debito');continue
            if funding_capacities(sc['funding'],bound['calendar'],problem,allow_issuance=True) is None:continue
            order=model['asset']['stage_order'];reach=1.;outcomes=[];value=0.
            branches=[]
            for identity in order:
                branches.append((identity,reach*(1.-sc['probabilities'][identity])))
                next_reach=reach*sc['probabilities'][identity]
                if reach>0 and sc['probabilities'][identity]>0 and next_reach==0.:
                    problem('conditional_probabilities','Probabilita positiva non rappresentabile; nessuna conversione silenziosa in ramo impossibile')
                reach=next_reach
            branches.append((None,reach))
            for failed,probability in branches:
                label='success' if failed is None else 'failure:'+failed
                if probability==0.:
                    outcomes.append({'outcome':label,'probability':0.,'reachable':False,'value_contribution':0.});continue
                economics=_outcome_economics(model,sc,failed);n=len(economics['start_costs'])
                calendar={**bound['calendar'],'periods':bound['calendar']['periods'][:n]}
                case={k:v[:n] if isinstance(v,list) else deepcopy(v) for k,v in sc.items()}
                case['funding']={'capacities':sc['funding']['capacities'][:n],
                    'commitments':{k:deepcopy(v) for k,v in sc['funding']['commitments'].items() if int(k)<n}}
                debt=loan_cashflows(model['debt_schedule'],calendar,problem)
                try:
                    projected=finite_equity_value(model,case,economics,debt,calendar,bound['times'][:n],problem,allow_issuance=True)
                except ArithmeticError as exc:
                    problem('development_funding','Flussi/azioni non rappresentabili: '+type(exc).__name__);continue
                if projected is not None:
                    contribution=probability*projected['fair_value_per_share'];value+=contribution
                    outcomes.append({'outcome':label,'probability':probability,'reachable':True,'value_contribution':contribution,**projected})
            results[scenario]={'fair_value_per_share':value,'value_basis':'equity','terminal_value':0.,'outcomes':outcomes,
                'risk_basis':'conditional_branch_cash_with_transition_risk_excluded_from_discount_rate'}
    return finish_documented(bundle,bound,results,metadata=metadata,output_dir=output_dir,engine='development_rnpv')
