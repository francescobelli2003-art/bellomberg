"""Documented renewable term-life population, GAAP reserve and cash movements."""
from copy import deepcopy
from .dcf_quality import _finite,_text
from .capital_inputs import equal
from .insurance_economics import investment_movement

LIFE_OPENING={'cash','investments','policy_reserve','claims_payable','other_liabilities','in_force_m'}
LIFE_PRODUCT={'product':'annual_renewable_term','accounting':'GAAP',
    'reserve_basis':'future_coverage_GAAP_excluding_incurred_claims_DAC_CSM_UPR_deferred_tax',
    'renewal':'guaranteed_at_documented_premiums_and_death_cover',
    'timing':'new_and_premium_at_start_death_during_year_lapse_after_death_at_end',
    'benefits':'death_only_no_surrender_maturity_investment_guarantee_or_participation',
    'premium_basis':'fully_collected_no_receivable','reinsurance':'none',
    'investment_basis':'annual_cash_yield_on_opening_amortized_cost_no_OCI',
    'tax_basis':'current_cash_equals_expense_no_deferred','ownership_basis':'wholly_owned_ordinary_only',
    'other_liabilities_basis':'operating_payables_no_subsidiary_borrowing'}
LIFE_PATHS=('new_policies','mortality_rate','lapse_rate','premium_per_policy','death_benefit_per_policy',
    'admin_cost_per_policy','acquisition_cost_per_policy','closing_policy_reserve','claims_paid',
    'operating_expenses','cash_taxes','parent_fees_paid','parent_tax_paid','investment_yield',
    'investment_purchases','investment_cost_sold','investment_proceeds','investment_impairment','other_liability_change')
RESERVE_DRIVERS=('new_policies','mortality_rate','lapse_rate','premium_per_policy','death_benefit_per_policy',
    'admin_cost_per_policy','acquisition_cost_per_policy','closing_policy_reserve')


def life_book(balance):
    return balance['cash']+balance['investments']-balance['policy_reserve']-balance['claims_payable']-balance['other_liabilities']


def validate_reserve_report(report,opening,periods,problem):
    """Match the externally acquired actuarial projection to the consumed case."""
    if (not isinstance(report,dict) or set(report)!={'report_id','reserve_basis','opening','forecast','continuing'} or
            not _text(report['report_id']) or report['reserve_basis']!=LIFE_PRODUCT['reserve_basis']):
        problem('insurance_liabilities','Report attuariale identificato e base riserve coerente richiesti');return
    if report['opening']!={k:opening.get(k) for k in ('in_force_m','policy_reserve')}:
        problem('insurance_liabilities','Report attuariale non riconciliato al portafoglio/riserve opening')
    if report['forecast']!={k:[p.get(k) for p in periods[:-1]] for k in RESERVE_DRIVERS} or report['continuing']!={k:periods[-1].get(k) for k in RESERVE_DRIVERS}:
        problem('insurance_liabilities','Piano riserve e ipotesi attuariali/prodotto/nuova produzione non coincidono')


def project_life(opening,product,periods,distributions,contributions,problem,*,computed_opening=False):
    if product!=LIFE_PRODUCT:
        problem('in_force_business','Prodotto vita non supportato: acquisire ponte per garanzie, riassicurazione o altra base contabile');return None
    if not isinstance(opening,dict) or set(opening)!=LIFE_OPENING or any(not _finite(v) or v<(-1e-9 if computed_opening else 0) for v in opening.values()):
        problem('accounting_bridge','Bilancio e stock polizze opening completi e non negativi richiesti');return None
    if any(not isinstance(p,dict) or set(p)!=set(LIFE_PATHS) or any(not _finite(v) for v in p.values()) for p in periods):
        problem('actuarial_assumptions','Percorso completo demografico, economico e riserve attuariali richiesto');return None
    if not opening['in_force_m'] and opening['policy_reserve']:
        problem('insurance_liabilities','Policy reserve opening senza polizze in essere')
    balance=deepcopy(opening);rows=[]
    for i,p in enumerate(periods):
        if any(p[k]<0 for k in LIFE_PATHS if k!='other_liability_change') or not 0<=p['mortality_rate']<=1 or not 0<=p['lapse_rate']<=1:
            problem('actuarial_assumptions','Probabilita/costi vita fuori dominio nel periodo '+str(i));return None
        prior=deepcopy(balance);book_open=life_book(prior)
        exposed=prior['in_force_m']+p['new_policies'];deaths=exposed*p['mortality_rate']
        lapses=(exposed-deaths)*p['lapse_rate'];remaining=exposed-deaths-lapses
        premium=exposed*p['premium_per_policy'];claims=deaths*p['death_benefit_per_policy']
        admin=exposed*p['admin_cost_per_policy'];acquisition=p['new_policies']*p['acquisition_cost_per_policy']
        expenses=admin+acquisition+p['operating_expenses']
        if p['other_liability_change']>expenses:
            problem('expenses_tax','Operating payables vita: aumento superiore alle spese maturate')
        reserve_change=p['closing_policy_reserve']-prior['policy_reserve']
        investment=investment_movement(prior['investments'],p,problem)
        ni=premium-claims-expenses-reserve_change-p['cash_taxes']-p['parent_fees_paid']-p['parent_tax_paid']+investment['income']+investment['gain']
        operating=premium-p['claims_paid']-expenses-p['cash_taxes']+investment['income']+p['other_liability_change']
        cash_before=prior['cash']+operating+investment['cash']-p['parent_fees_paid']-p['parent_tax_paid']
        balance.update(cash=cash_before+contributions[i]-distributions[i],investments=investment['closing_assets'],
            policy_reserve=p['closing_policy_reserve'],claims_payable=prior['claims_payable']+claims-p['claims_paid'],
            other_liabilities=prior['other_liabilities']+p['other_liability_change'],in_force_m=remaining)
        if any(not _finite(v) or v< -1e-9 for v in balance.values()):
            problem('insurance_liabilities','Saldo vita non finito/negativo nel periodo '+str(i))
        if not remaining and balance['policy_reserve']:
            problem('insurance_liabilities','Riserva di copertura futura senza polizze; distinguere claim payable da policy reserve')
        book_close=life_book(balance)
        if not equal(book_close,book_open+ni+contributions[i]-distributions[i]):
            problem('accounting_bridge','Utile vita e stato patrimoniale non riconciliati nel periodo '+str(i))
        rows.append({'opening_in_force':prior['in_force_m'],'new_policies':p['new_policies'],'deaths':deaths,'lapses':lapses,
            'premiums':premium,'incurred_claims':claims,'administrative_costs':admin,'acquisition_costs':acquisition,
            'reserve_change':reserve_change,'net_income':ni,'investment_income':investment['income'],
            'operating_cash':operating,'investing_cash':investment['cash'],'financing_cash':0.,
            'parent_fees_paid':p['parent_fees_paid'],'parent_tax_paid':p['parent_tax_paid'],
            'cash_before_transfers':cash_before,'opening_common_equity':book_open,'closing_common_equity':book_close,
            'closing_balance':deepcopy(balance)})
    return {'rows':rows,'closing_balance':balance}
