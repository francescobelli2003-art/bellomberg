"""Pure insurance earnings/balance/cash projection; valuation and capital live elsewhere."""
from copy import deepcopy
from .dcf_quality import _finite,_text
from .capital_inputs import equal

PC_OPENING={'cash','investments','premium_receivable','unearned_premium','claims_reserve',
            'ceded_unearned_premium','reinsurance_recoverable','reinsurance_payable','other_liabilities'}
PC_PATHS=('premium_written','premium_earned','premium_collected','loss_ratio','catastrophe_losses',
    'prior_reserve_development','claims_paid','ceded_fraction','ceded_premium_paid','reinsurance_recovered',
    'reinsurance_impairment','operating_expenses','cash_taxes','parent_fees_paid','parent_tax_paid',
    'investment_yield','investment_purchases','investment_cost_sold','investment_proceeds','investment_impairment',
    'other_liability_change')
PC_PRODUCT={'product':'short_tail_pc','reinsurance':'constant_quota_share',
    'reinsurance_commission':'none','other_liabilities_basis':'operating_payables_no_subsidiary_borrowing',
    'investment_basis':'annual_cash_yield_on_opening_amortized_cost_no_OCI','reserve_basis':'undiscounted_incurred',
    'tax_basis':'current_cash_equals_expense_no_deferred','ownership_basis':'wholly_owned_ordinary_only',
    'reinsurance_scope':'single_treaty_all_opening_and_future_claims','accounting':'GAAP'}


def common_book(balance):
    return (balance['cash']+balance['investments']+balance['premium_receivable']+
            balance['ceded_unearned_premium']+balance['reinsurance_recoverable']-
            balance['unearned_premium']-balance['claims_reserve']-balance['reinsurance_payable']-balance['other_liabilities'])


def investment_movement(opening_assets,period,problem):
    """One amortized-cost/cash bridge shared by the insurance products."""
    p=period
    if p['investment_proceeds'] and not p['investment_cost_sold']:
        problem('investment_income','Proventi da cessioni senza attivita venduta: acquisire ponte specifico')
    return {'income':opening_assets*p['investment_yield'],
        'gain':p['investment_proceeds']-p['investment_cost_sold']-p['investment_impairment'],
        'closing_assets':opening_assets+p['investment_purchases']-p['investment_cost_sold']-p['investment_impairment'],
        'cash':p['investment_proceeds']-p['investment_purchases']}


def project_pc(opening,product,periods,distributions,contributions,problem):
    if not isinstance(opening,dict) or set(opening)!=PC_OPENING or any(not _finite(v) or v<0 for v in opening.values()):
        problem('accounting_bridge','Bilancio opening PC completo e non negativo richiesto');return None
    if (not isinstance(product,dict) or set(product)!=set(PC_PRODUCT)|{'counterparty','opening_paid_claims_recoverable'} or
            any(product[k]!=v for k,v in PC_PRODUCT.items()) or not _text(product['counterparty']) or
            not _finite(product['opening_paid_claims_recoverable']) or product['opening_paid_claims_recoverable']<0):
        problem('premiums','Prodotto/base non supportati: solo short-tail GAAP e quota-share costante documentata');return None
    if any(not isinstance(p,dict) or set(p)!=set(PC_PATHS) or any(not _finite(v) for v in p.values()) for p in periods):
        problem('premiums','Driver PC completi e numerici richiesti per ogni periodo');return None
    cession=periods[0]['ceded_fraction']
    if not equal(opening['ceded_unearned_premium'],opening['unearned_premium']*cession):
        problem('reinsurance','UPR ceduta opening non riconciliata alla quota-share costante')
    if not equal(opening['reinsurance_recoverable'],opening['claims_reserve']*cession+product['opening_paid_claims_recoverable']):
        problem('reinsurance','Recuperabili opening: riconciliare quota riserve e crediti per sinistri gia pagati')
    if (cession==0)!=(product['counterparty']=='none') or (cession==0 and product['opening_paid_claims_recoverable']):
        problem('reinsurance','Identificare controparte del trattato; none solo per assenza esplicita di riassicurazione')
    balance=deepcopy(opening);rows=[]
    for i,p in enumerate(periods):
        if any(p[k]<0 for k in PC_PATHS if k not in ('prior_reserve_development','other_liability_change')) or not 0<=p['ceded_fraction']<=1 or not 0<=p['loss_ratio'] or p['ceded_fraction']!=cession:
            problem('claims_reserves','Driver costo/cessione non supportato nel periodo '+str(i));return None
        prior=deepcopy(balance);book_open=common_book(prior)
        if p['prior_reserve_development'] < -prior['claims_reserve']:
            problem('claims_reserves','Rilascio riserve pregresse superiore allo stock iniziale')
        if p['other_liability_change']>p['operating_expenses']:
            problem('expenses_tax','Operating payables: aumento superiore alle spese maturate, cassa operativa fittizia')
        incurred=p['premium_earned']*p['loss_ratio']+p['catastrophe_losses']+p['prior_reserve_development']
        recovery=incurred*cession;ceded_written=p['premium_written']*cession;ceded_earned=p['premium_earned']*cession
        investment=investment_movement(prior['investments'],p,problem)
        investment_income=investment['income'];investment_gain=investment['gain']
        ni=(p['premium_earned']-ceded_earned-incurred+recovery-p['reinsurance_impairment']-
            p['operating_expenses']-p['parent_fees_paid']-p['cash_taxes']-p['parent_tax_paid']+
            investment_income+investment_gain)
        operating=(p['premium_collected']-p['claims_paid']-p['ceded_premium_paid']+p['reinsurance_recovered']-
            p['operating_expenses']-p['cash_taxes']+investment_income+p['other_liability_change'])
        investing=investment['cash']
        cash_before=prior['cash']+operating+investing-p['parent_fees_paid']-p['parent_tax_paid']
        balance.update(cash=cash_before+contributions[i]-distributions[i],
            investments=investment['closing_assets'],
            premium_receivable=prior['premium_receivable']+p['premium_written']-p['premium_collected'],
            unearned_premium=prior['unearned_premium']+p['premium_written']-p['premium_earned'],
            claims_reserve=prior['claims_reserve']+incurred-p['claims_paid'],
            ceded_unearned_premium=prior['ceded_unearned_premium']+ceded_written-ceded_earned,
            reinsurance_recoverable=prior['reinsurance_recoverable']+recovery-p['reinsurance_recovered']-p['reinsurance_impairment'],
            reinsurance_payable=prior['reinsurance_payable']+ceded_written-p['ceded_premium_paid'],
            other_liabilities=prior['other_liabilities']+p['other_liability_change'])
        if any(not _finite(v) or v<0 for v in balance.values()):
            problem('accounting_bridge','Saldo assicurativo non finito/negativo nel periodo '+str(i))
        book_close=common_book(balance)
        if not equal(book_close,book_open+ni+contributions[i]-distributions[i]):
            problem('accounting_bridge','Utile e stato patrimoniale non riconciliati nel periodo '+str(i))
        rows.append({'net_income':ni,'incurred_claims':incurred,'reinsurance_income':recovery,
            'investment_income':investment_income,'investment_gain':investment_gain,
            'operating_cash':operating,'investing_cash':investing,'financing_cash':0.,
            'parent_fees_paid':p['parent_fees_paid'],'parent_tax_paid':p['parent_tax_paid'],
            'cash_before_transfers':cash_before,'opening_common_equity':book_open,'closing_common_equity':book_close,
            'closing_balance':deepcopy(balance),
            'combined_ratio':(incurred-recovery+p['operating_expenses']+p['parent_fees_paid'])/(p['premium_earned']-ceded_earned) if p['premium_earned']>ceded_earned else None})
    return {'rows':rows,'closing_balance':balance}
