"""Finite unlevered cash valuation and separately reconciled financing effects."""
from .cash_math import present_value,cash_sweep
from .dcf_quality import _finite,_text,_date
from .capital_inputs import equal


def loan_cashflows(schedule,calendar,problem):
    n=len(calendar['periods']);ends=[p['end'] for p in calendar['periods']]
    interest=[0.]*n;principal=[0.]*n;settlement=0.
    if set(schedule)=={'no_debt'} and schedule['no_debt'] is True:
        return {'interest':interest,'principal':principal,'settlement':settlement}
    for identity,loan in schedule.items():
        if (not _text(identity) or not isinstance(loan,dict) or set(loan)!={'face_value','settlement_value','annual_coupon','maturity','basis'} or
                any(not _finite(loan[k]) or loan[k]<0 for k in ('face_value','settlement_value','annual_coupon')) or
                loan['face_value']<=0 or loan['settlement_value']<loan['face_value'] or
                loan['basis']!='fixed_rate_bullet_full_year_coupon_settlement_at_or_above_par' or loan['maturity'] not in ends):
            problem('debt_schedule','Prestiti esistenti, settlement >= par e scadenze a fine periodo entro vita finita richiesti');continue
        last=ends.index(loan['maturity']);settlement+=loan['settlement_value']
        principal[last]+=loan['face_value']
        for i in range(last+1):interest[i]+=loan['face_value']*loan['annual_coupon']
    return {'interest':interest,'principal':principal,'settlement':settlement}


def funding_capacities(funding,calendar,problem,*,allow_issuance=False):
    n=len(calendar['periods'])
    if (not isinstance(funding,dict) or set(funding)!={'capacities','commitments'} or
            not isinstance(funding['capacities'],list) or len(funding['capacities'])!=n or
            any(not _finite(v) or v<0 for v in funding['capacities']) or not isinstance(funding['commitments'],dict)):
        problem('funding','Capacita per periodo e impegni fonte-specifici richiesti');return None
    active={str(i) for i,v in enumerate(funding['capacities']) if v>0}
    if set(funding['commitments'])!=active:
        problem('funding','Ogni capacita positiva richiede il proprio impegno, nessun record extra');return None
    names=set()
    for key,commitment in funding['commitments'].items():
        external=isinstance(commitment,dict) and commitment.get('terms')=='draw_when_needed_outside_common_equity_at_fixed_price'
        keys={'id','available_date','terms'}|({'issue_price'} if external else set())
        if (key not in active or not isinstance(commitment,dict) or set(commitment)!=keys or
                not _text(commitment['id']) or commitment['id'] in names or commitment['id']=='none' or
                commitment['available_date']!=calendar['periods'][int(key)]['start'] or
                (external and (not allow_issuance or not _finite(commitment['issue_price']) or commitment['issue_price']<=0)) or
                (not external and commitment['terms']!='draw_when_needed_existing_shareholders_no_new_shares')):
            problem('funding','Impegno irrevocabile e termini azioni/prezzo disponibili prima dei costi iniziali richiesti');return None
        names.add(commitment['id'])
    return funding['capacities']


def _original_holder_cash(funding,distribution,shares,original_shares,commitment):
    """New investors fund only their subscribed cash; old cash calls stay negative."""
    external=commitment is not None and commitment['terms']=='draw_when_needed_outside_common_equity_at_fixed_price'
    contribution=0. if external else funding*original_shares/shares
    issued=funding/commitment['issue_price'] if external else 0.
    shares+=issued
    return max(0.,distribution)*original_shares/shares-contribution,shares,issued


def finite_equity_value(model,sc,economics,debt,calendar,times,problem,*,allow_issuance=False):
    """APV uses expected financing inputs; the cash ledger tests this source case."""
    from .dcf_buyside_v3 import _dcf_value
    capacities=funding_capacities(sc['funding'],calendar,problem,allow_issuance=allow_issuance)
    if capacities is None:return None
    if (model['opening_cash']<0 or not 0<=model['opening_cash_buffer']<=model['opening_cash'] or model['shares']<=0 or
            sc['ku']<=0 or sc['tax_shield_discount_rate']<=0 or sc['financing_cost_pv']<0 or
            any(not 0<=v<=1 for v in sc['tax_rate']) or any(v<0 for v in sc['minimum_cash'])):
        problem('project_funding','Cash/buffer/azioni/tassi/tax/costi finanziari fuori dominio');return None
    if allow_issuance and (debt['settlement'] or any(debt['interest']) or any(debt['principal']) or
            any(sc['expected_tax_shield']) or sc['financing_cost_pv']):
        problem('funding','Ledger quote emesse supporta solo equity senza debito, shield o costi finanziari aggiuntivi');return None
    cash=model['opening_cash'];previous_buffer=model['opening_cash_buffer'];flows=[];periods=[];ledger=[]
    shares=model['shares'];holder_flows=[]
    for i,end_time in enumerate(times):
        interest=debt['interest'][i];capitalized=economics['capitalized_interest'][i]
        if capitalized<0 or capitalized>interest+1e-9:
            problem('project_funding','Interessi capitalizzati eccedono coupon effettivo nel periodo '+str(i))
        tax_u=max(0.,economics['taxable_unlevered'][i])*sc['tax_rate'][i]
        tax_l=max(0.,economics['taxable_financed_before_interest'][i]-(interest-capitalized))*sc['tax_rate'][i]
        usable_shield=tax_u-tax_l;expected=sc['expected_tax_shield'][i]
        if expected<0 or expected>usable_shield+1e-9:
            problem('expected_tax_shield','Beneficio atteso eccede quello fiscalmente utilizzabile nel caso: periodo '+str(i))
        buffer=sc['minimum_cash'][i];cost=economics['start_costs'][i];receipts=economics['end_receipts'][i]
        start_sweep=cash_sweep(cash-cost,buffer)
        early=start_sweep['funding_required'];start_distribution=max(0.,start_sweep['distribution'])
        after_start=cash+early-cost-start_distribution
        available=after_start+receipts-tax_l-interest-debt['principal'][i]
        closing_minimum=0. if i==len(times)-1 else buffer
        sweep=cash_sweep(available,closing_minimum);late=sweep['funding_required']
        if early+late>capacities[i]+1e-9:
            problem('funding','Fondi necessari prima dei costi e a fine periodo eccedono impegni: periodo '+str(i))
        start_cf=-cost-(buffer-previous_buffer)
        end_cf=receipts-tax_u+(buffer if i==len(times)-1 else 0.)
        flows.extend((start_cf,end_cf));periods.extend((0. if i==0 else times[i-1],end_time))
        ledger.append({'period':calendar['periods'][i],'opening_cash':cash,'start_costs':cost,
            'funding_at_start':early,'end_receipts':receipts,'unlevered_tax':tax_u,'cash_tax':tax_l,
            'shareholder_distribution_at_start':start_distribution,
            'usable_tax_shield':usable_shield,'expected_tax_shield':expected,'interest_paid':interest,
            'principal_paid':debt['principal'][i],'funding_at_end':late,
            'shareholder_distribution_at_end':sweep['distribution'],'closing_cash':closing_minimum,
            'unlevered_start_cashflow':start_cf,'unlevered_end_cashflow':end_cf})
        if allow_issuance:
            opening_shares=shares;commitment=sc['funding']['commitments'].get(str(i))
            first,shares,issued_start=_original_holder_cash(early,start_distribution,shares,model['shares'],commitment)
            last,shares,issued_end=_original_holder_cash(late,sweep['distribution'],shares,model['shares'],commitment)
            holder_flows.extend((first,last))
            ledger[-1].update(opening_shares=opening_shares,new_shares_at_start=issued_start,new_shares_at_end=issued_end,
                closing_shares=shares,original_holder_cashflow_at_start=first,original_holder_cashflow_at_end=last)
        cash=closing_minimum;previous_buffer=buffer
    shield_pv=present_value(sc['expected_tax_shield'],times,sc['tax_shield_discount_rate'])
    free_cash=model['opening_cash']-model['opening_cash_buffer']
    spec={'documented_inputs':True,'discount_periods':periods,'shares_m':model['shares'],
        'net_debt':debt['settlement']-free_cash,
        'equity_adjustments':[{'label':'Expected after-tax financing effects',
            'value_m':shield_pv-sc['financing_cost_pv']}]}
    value=(present_value(holder_flows,periods,sc['ku'])/model['shares'] if allow_issuance else
           _dcf_value(spec,flows,sc['ku'],None,terminal_value=0.))
    return {'fair_value_per_share':value,'value_basis':'equity','terminal_value':0.,'ledger':ledger,
        'unlevered_cashflows':flows,'cashflow_times':periods,'tax_shield_pv':shield_pv,
        'expected_financing_cost_pv':sc['financing_cost_pv'],'opening_excess_cash':free_cash,
        'debt_settlement_value':debt['settlement'],'valuation_basis':'Finite APV; expected financing inputs acquired, no engine default-risk estimate.'}
