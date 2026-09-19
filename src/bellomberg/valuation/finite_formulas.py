"""Finite asset/project projections and APV: no invented perpetuity or funding."""
from bellomberg.core.language import text as tr
from .linked_model import LinkedModel, SCENARIOS, ready, total
from .documented_presentation import _finish, _line, PRICE


def _resources(m, s, ws):
    econ = {k: [[] for _ in range(m.n)] for k in ('cost', 'receipts', 'tax_u', 'tax_l', 'capitalized')}
    row = 9
    for name, asset in m.value('model', 'assets').items():
        a = lambda k: m.ref('model', 'assets', name, k)
        closure = lambda k: m.ref(s, 'closure', name, k)
        remaining, production, basis = a('recoverable_reserves'), a('opening_annual_output'), a('opening_tax_basis')
        m.check(s, name+' opening reserves', f'AND({remaining}>0,{production}>0,{basis}>=0)')
        m.check(s, name+' closure', f'AND({closure("period")}=INT({closure("period")}),{closure("period")}>=1,{closure("period")}<={m.n},{closure("cash_cost")}>=0,{closure("remaining_abandoned")}>=0)')
        _line(ws, row, name, m.end, True); row += 1
        for i in range(m.n):
            p = {k: m.ref(s, 'forecasts', name, k, i) for k in ('decline','price','unit_cash_cost','fixed_cash_cost','royalty_rate','capex','tax_allowance')}
            for key, ref in p.items():
                m.check(s, f'{name} {i+1} {key}', ref+'>=0')
            m.check(s, f'{name} {i+1} fractions', f'AND({p["decline"]}<=1,{p["royalty_rate"]}<=1)')
            r = lambda j, label, expression: m.calc(ws, row+j, i+4, label, expression)
            production = r(0, tr('Produzione', 'Production'), production+'*(1-'+p['decline']+')')
            remaining = r(1, tr('Riserve residue', 'Remaining reserves'), remaining+'-'+production)
            revenue = r(2, tr('Ricavi', 'Revenue'), production+'*'+p['price'])
            opex = r(3, tr('Costi operativi cash', 'Cash operating costs'), production+'*'+p['unit_cash_cost']+'+'+p['fixed_cash_cost'])
            royalty = r(4, 'Royalty', revenue+'*'+p['royalty_rate'])
            restoration = r(5, tr('Costo di chiusura', 'Closure cost'), f'IF({closure("period")}={i+1},{closure("cash_cost")},0)')
            basis = r(6, tr('Base fiscale residua', 'Remaining tax basis'), basis+'+'+p['capex']+'-'+p['tax_allowance'])
            taxable = r(7, tr('Imponibile operativo', 'Operating taxable income'), f'{revenue}-{opex}-{royalty}-{p["tax_allowance"]}-{restoration}')
            m.check(s, f'{name} {i+1} reserves/tax basis', f'AND({remaining}>=-0.00000001,{basis}>=-0.00000001)')
            m.check(s, f'{name} {i+1} economic limit', f'OR({production}=0,{revenue}-{opex}-{royalty}>=-0.00000001)')
            m.check(s, f'{name} {i+1} closure cut-off', f'IF({i+1}>{closure("period")},AND({production}=0,{p["capex"]}=0,{p["fixed_cash_cost"]}=0,{p["tax_allowance"]}=0),TRUE)')
            if m.years[i] > asset['rights_expiry']:
                m.check(s, f'{name} {i+1} expired rights', production+'=0')
            econ['cost'][i].append(opex+'+'+p['capex'])
            econ['receipts'][i].append(f'{revenue}-{royalty}-{restoration}')
            econ['tax_u'][i].append(taxable); econ['tax_l'][i].append(taxable)
        m.equal(s, name+' abandoned reserves', remaining, closure('remaining_abandoned'))
        m.equal(s, name+' final tax basis', basis, '0')
        row += 10
    return econ, row


def _projects(m, s, ws):
    econ = {k: [[] for _ in range(m.n)] for k in ('cost', 'receipts', 'tax_u', 'tax_l', 'capitalized')}
    row = 9
    for name in m.value('model', 'projects'):
        a = lambda k: m.ref('model', 'projects', name, k)
        f = lambda k, *p: m.ref(s, 'project_forecasts', name, k, *p)
        units, unlevered, financed, receivable = (a(k) for k in ('units_total','opening_unlevered_tax_basis','opening_tax_basis','opening_receivable'))
        m.check(s, name+' opening', f'AND({units}>0,{units}=INT({units}),{unlevered}>=0,{financed}>={unlevered},{receivable}>=0)')
        complete = f('completion_period')
        m.check(s, name+' completion', f'AND({complete}=INT({complete}),{complete}>=1,{complete}<={m.n},{f("remaining_construction_budget")}>=0)')
        costs = total(f('construction_cash', i) for i in range(m.n))
        interest = total(f('tax_capitalized_interest', i) for i in range(m.n))
        m.equal(s, name+' remaining budget', costs, f('remaining_construction_budget'))
        cost_u, cost_l = '('+unlevered+'+'+costs+')', '('+financed+'+'+costs+'+'+interest+')'
        remaining = units
        _line(ws, row, name, m.end, True); row += 1
        for i in range(m.n):
            p = {k: f(k,i) for k in ('construction_cash','units_delivered','price_per_lot','operating_cash_cost','closing_receivable','tax_capitalized_interest')}
            for key, ref in p.items():
                m.check(s, f'{name} {i+1} {key}', ref+'>=0')
            sold, cost, capint = p['units_delivered'], p['construction_cash'], p['tax_capitalized_interest']
            m.check(s, f'{name} {i+1} whole units', f'AND({sold}=INT({sold}),{sold}<={remaining})')
            m.check(s, f'{name} {i+1} delivery timing', f'OR({sold}=0,{i+1}>={complete})')
            m.check(s, f'{name} {i+1} construction timing', f'IF({i+1}>{complete},AND({cost}=0,{capint}=0),TRUE)')
            r = lambda j, label, expression: m.calc(ws, row+j, i+4, label, expression)
            revenue = r(0, tr('Ricavi', 'Revenue'), sold+'*'+p['price_per_lot'])
            collections = r(1, tr('Incassi', 'Collections'), receivable+'+'+revenue+'-'+p['closing_receivable'])
            cogs_u = r(2, tr('Costo fiscale senza debito', 'Unlevered tax cost of sales'), sold+'*'+cost_u+'/'+units)
            cogs_l = r(3, tr('Costo fiscale finanziato', 'Financed tax cost of sales'), sold+'*'+cost_l+'/'+units)
            unlevered = r(4, tr('Inventario fiscale senza debito', 'Unlevered tax inventory'), unlevered+'+'+cost+'-'+cogs_u)
            financed = r(5, tr('Inventario fiscale finanziato', 'Financed tax inventory'), financed+'+'+cost+'+'+capint+'-'+cogs_l)
            remaining = r(6, tr('Unita invendute', 'Unsold units'), remaining+'-'+sold)
            m.check(s, f'{name} {i+1} inventory / cash', f'AND({collections}>=0,{unlevered}>=-0.00000001,{financed}>=-0.00000001)')
            econ['cost'][i].append(cost+'+'+p['operating_cash_cost'])
            econ['receipts'][i].append(collections)
            econ['tax_u'][i].append(revenue+'-'+cogs_u+'-'+p['operating_cash_cost'])
            econ['tax_l'][i].append(revenue+'-'+cogs_l+'-'+p['operating_cash_cost'])
            econ['capitalized'][i].append(capint)
            receivable = p['closing_receivable']
        for name_end, value in (('units',remaining),('unlevered inventory',unlevered),('financed inventory',financed),('receivables',receivable)):
            m.equal(s, name+' final '+name_end, value, '0')
        row += 9
    return econ, row


def _loans(m, s):
    loans = m.value('model', 'debt_schedule')
    debt = {'interest': [[] for _ in range(m.n)], 'principal': [[] for _ in range(m.n)], 'settlement': []}
    if loans == {'no_debt': True}:
        return debt
    for name, loan in loans.items():
        face, settlement, coupon = (m.ref('model','debt_schedule',name,k) for k in ('face_value','settlement_value','annual_coupon'))
        m.check(s, name+' loan', f'AND({face}>0,{settlement}>={face},{coupon}>=0)')
        last = m.years.index(loan['maturity'])
        debt['settlement'].append(settlement); debt['principal'][last].append(face)
        for i in range(last+1):
            debt['interest'][i].append(face+'*'+coupon)
    return debt


def _cash(m, s, econ, debt, ws, row):
    rate, shield_rate, finance_cost = (m.ref(s,k) for k in ('ku','tax_shield_discount_rate','financing_cost_pv'))
    cash, initial_buffer, shares = (m.ref('model',k) for k in ('opening_cash','opening_cash_buffer','shares'))
    m.check(s, 'Opening cash / buffer / shares', f'AND({cash}>=0,{initial_buffer}>=0,{initial_buffer}<={cash},{shares}>0)')
    m.check(s, 'Discount rates / financing cost', f'AND({rate}>0,{shield_rate}>0,{finance_cost}>=0)')
    previous_buffer = initial_buffer
    pv, shield_pv = [], []
    commitments = m.value(s, 'funding')['commitments']
    for i in range(m.n):
        cost, receipts, taxable_u, taxable_l, capint = (total(econ[k][i]) for k in ('cost','receipts','tax_u','tax_l','capitalized'))
        tax, buffer, expected = (m.ref(s,k,i) for k in ('tax_rate','minimum_cash','expected_tax_shield'))
        capacity = m.ref(s,'funding','capacities',i)
        m.check(s, f'{i+1} tax / buffer / funding', f'AND({tax}>=0,{tax}<=1,{buffer}>=0,{capacity}{">0" if str(i) in commitments else "=0"})')
        interest, principal = total(debt['interest'][i]), total(debt['principal'][i])
        m.check(s, f'{i+1} capitalized interest', f'AND({capint}>=0,{capint}<={interest}+0.000000001)')
        r = lambda j, label, expression: m.calc(ws, row+j, i+4, label, expression)
        r(0, tr('Spesa all inizio del periodo', 'Start-of-period spending'), cost)
        r(1, tr('Incassi netti a fine periodo', 'End-of-period net receipts'), receipts)
        tax_u = r(2, tr('Imposte senza debito', 'Unlevered tax'), f'MAX(0,{taxable_u})*{tax}')
        tax_l = r(3, tr('Imposte cash finanziate', 'Financed cash tax'), f'MAX(0,{taxable_l}-({interest}-{capint}))*{tax}')
        usable = r(4, tr('Scudo fiscale utilizzabile', 'Usable tax shield'), tax_u+'-'+tax_l)
        m.check(s, f'{i+1} expected tax shield', f'AND({expected}>=0,{expected}<={usable}+0.000000001)')
        early = r(5, tr('Apporto iniziale', 'Start funding'), f'MAX(0,{buffer}-({cash}-{cost}))')
        start_div = r(6, tr('Distribuzione iniziale', 'Start distribution'), f'MAX(0,{cash}-{cost}-{buffer})')
        after_start = r(7, tr('Cassa dopo spesa iniziale', 'Cash after initial spending'), f'{cash}+{early}-{cost}-{start_div}')
        available = r(8, tr('Cassa prima del saldo ai soci', 'Cash before final shareholder flow'), f'{after_start}+{receipts}-{tax_l}-{interest}-{principal}')
        minimum = '0' if i == m.n-1 else buffer
        late = r(9, tr('Apporto finale', 'End funding'), f'MAX(0,{minimum}-{available})')
        m.support_calls[s].extend((early, late))
        r(10, tr('Flusso netto finale ai soci', 'Net end shareholder cash flow'), available+'-'+minimum)
        m.check(s, f'{i+1} committed funding capacity', early+'+'+late+'<='+capacity+'+0.000000001')
        start_cf = r(12, tr('FCFF iniziale', 'Start FCFF'), f'-{cost}-({buffer}-{previous_buffer})')
        end_cf = r(13, tr('FCFF finale', 'End FCFF'), receipts+'-'+tax_u+('+'+buffer if i==m.n-1 else ''))
        start_time = '0' if i == 0 else m.period(i-1)
        pv.append(r(15, tr('Valore attuale FCFF', 'PV FCFF'), f'{start_cf}/(1+{rate})^{start_time}+{end_cf}/(1+{rate})^{m.period(i)}'))
        shield_pv.append(r(16, tr('Valore attuale scudo atteso', 'PV expected tax shield'), f'{expected}/(1+{shield_rate})^{m.period(i)}'))
        cash, previous_buffer = minimum, buffer
    enterprise = m.calc(ws,row+19,4,tr('Valore operativo finito', 'Finite operating value'),total(pv))
    equity = m.calc(ws,row+20,4,tr('Valore del capitale azionario', 'Equity value'),
        f'{enterprise}+{m.ref("model","opening_cash")}-{initial_buffer}-{total(debt["settlement"])}+{total(shield_pv)}-{finance_cost}')
    result = m.calc(ws,row+21,4,tr('Valore per azione', 'Value per share'), equity+'/'+shares, PRICE)
    m.calc(ws,row+23,4,tr('Valore terminale: vita finita', 'Terminal value: finite life'),'0')
    return result, row+25


def apply_finite_formulas(wb, payload):
    if not ready(payload, {'resources_asset_dcf','property_development_fcff'}):
        return False
    m = LinkedModel(wb, payload); m.quotation_checks()
    for s in SCENARIOS:
        ws = m.sheet('Finite '+s, tr('Vita finita — ', 'Finite life — ')+s)
        econ, row = (_resources if payload['method']=='resources_asset_dcf' else _projects)(m,s,ws)
        for i in range(m.n):
            central = m.ref(s,'central_cash_cost',i)
            m.check(s,f'{i+1} central costs',central+'>=0')
            econ['cost'][i].append(central)
            econ['tax_u'][i].append('-'+central); econ['tax_l'][i].append('-'+central)
        m.results[s], last = _cash(m,s,econ,_loans(m,s),ws,row)
        _finish(ws,last,m.end)
    m.finish()
    return True
