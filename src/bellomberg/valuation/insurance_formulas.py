"""Linked insurance earnings, balance sheets and legally distributable equity."""
from bellomberg.core.language import text as tr

from .distributable_equity import CASH_SIGNS, NONNEGATIVE_SUB_PATHS
from .insurance_economics import PC_OPENING, PC_PATHS
from .life_economics import LIFE_OPENING, LIFE_PATHS, RESERVE_DRIVERS
from .documented_presentation import _finish, _line
from .linked_model import LinkedModel, SCENARIOS, ready, total


METHODS = {'insurance_pc_distributable_equity', 'insurance_life_distributable_equity'}


def apply_insurance_formulas(wb, payload):
    if not ready(payload, METHODS):
        return False
    m = LinkedModel(wb, payload)
    if not m.n:
        return False
    m.quotation_checks()
    life = payload['method'] == 'insurance_life_distributable_equity'
    opening_keys = LIFE_OPENING if life else PC_OPENING
    paths = LIFE_PATHS if life else PC_PATHS
    ids = [item['id'] for item in m.value('model', 'legal_structure')['subsidiaries']]
    opening_book = m.ref('model', 'opening_common_equity')
    parent_book = m.ref('model', 'opening_parent_equity')
    opening_adj = m.ref('model', 'opening_consolidation_adjustments')

    def cap(s, *parts):
        width = 3 if parts[0] == 'subsidiaries' else 2 if parts[0] == 'parent_cash_flows' else 1
        return m.ref(s, 'capital.' + '.'.join(map(str, parts[:width])), *parts[width:])

    def number(s, label, expression):
        m.check(s, label, expression)

    def same(s, label, first, second):
        m.equal(s, label, first, second)

    def econ_ref(s, j, key, i):
        return m.ref(s, 'insurance.' + str(j) + '.' + key, i)

    def continuing_ref(s, identity, key):
        return m.ref(s, 'continuing_economics', identity, key)

    def calc(ws, row, column, label, expression):
        return m.calc(ws, row, column, label, expression)

    def economic_period(ws, s, j, identity, i, prior, dis, contribution, *, second=False):
        """One explicit product year. `prior` always comes from previous formulas."""
        c = m.n + 5 if second else m.n + 4 if i == m.n else i + 4
        base = 9 + j * 45
        if second:
            growth = m.ref(s, 'terminal_growth')
            def p(key):
                ref = continuing_ref(s, identity, key)
                return ref if key.endswith('_per_policy') or key in ('mortality_rate', 'lapse_rate', 'investment_yield') else f'{ref}*(1+{growth})'
        elif i == m.n:
            p = lambda key: continuing_ref(s, identity, key)
        else:
            p = lambda key: econ_ref(s, j, key, i)
        for key in paths:
            ref = p(key)
            if key != 'other_liability_change' and not (not life and key == 'prior_reserve_development'):
                number(s, f'{identity} {i+1} {key}', ref + '>=0')
        number(s, f'{identity} {i+1} investment sale',
               f'OR({p("investment_proceeds")}=0,{p("investment_cost_sold")}>0)')
        r = lambda offset, label, expression: calc(ws, base + offset, c, identity + ' ' + label, expression)
        investment_income = r(0 if not life else 9, 'investment income', f"{prior['investments']}*{p('investment_yield')}")
        investment_gain = r(1 if not life else 10, 'investment gain',
                            f"{p('investment_proceeds')}-{p('investment_cost_sold')}-{p('investment_impairment')}")
        investments = r(9 if not life else 16, 'closing investments',
                        f"{prior['investments']}+{p('investment_purchases')}-{p('investment_cost_sold')}-{p('investment_impairment')}")
        investing = r(6 if not life else 13, 'investing cash',
                      f"{p('investment_proceeds')}-{p('investment_purchases')}")
        if life:
            number(s, identity + f' {i+1} probabilities',
                   f'AND({p("mortality_rate")}<=1,{p("lapse_rate")}<=1)')
            exposed = r(0, 'exposed policies', f"{prior['in_force_m']}+{p('new_policies')}")
            deaths = r(1, 'deaths', f"{exposed}*{p('mortality_rate')}")
            lapses = r(2, 'lapses', f"({exposed}-{deaths})*{p('lapse_rate')}")
            premiums = r(3, 'earned premiums', f"{exposed}*{p('premium_per_policy')}")
            claims = r(4, 'incurred claims', f"{deaths}*{p('death_benefit_per_policy')}")
            admin = r(5, 'administration', f"{exposed}*{p('admin_cost_per_policy')}")
            acquisition = r(6, 'acquisition cost', f"{p('new_policies')}*{p('acquisition_cost_per_policy')}")
            expenses = r(7, 'total expenses', f"{admin}+{acquisition}+{p('operating_expenses')}")
            number(s, identity + f' {i+1} payables', f"{p('other_liability_change')}<={expenses}")
            reserve_change = r(8, 'reserve change', f"{p('closing_policy_reserve')}-{prior['policy_reserve']}")
            ni = r(11, 'net income', f"{premiums}-{claims}-{expenses}-{reserve_change}-{p('cash_taxes')}-{p('parent_fees_paid')}-{p('parent_tax_paid')}+{investment_income}+{investment_gain}")
            operating = r(12, 'operating cash', f"{premiums}-{p('claims_paid')}-{expenses}-{p('cash_taxes')}+{investment_income}+{p('other_liability_change')}")
            cash_before = r(14, 'cash before transfers', f"{prior['cash']}+{operating}+{investing}-{p('parent_fees_paid')}-{p('parent_tax_paid')}")
            balance = {
                'cash': r(15, 'closing cash', f'{cash_before}+{contribution}-{dis}'),
                'investments': investments,
                'policy_reserve': r(17, 'closing policy reserve', p('closing_policy_reserve')),
                'claims_payable': r(18, 'closing claims payable', f"{prior['claims_payable']}+{claims}-{p('claims_paid')}"),
                'other_liabilities': r(19, 'closing other liabilities', f"{prior['other_liabilities']}+{p('other_liability_change')}"),
                'in_force_m': r(20, 'closing policies', f'{exposed}-{deaths}-{lapses}'),
            }
            book = r(21, 'closing common equity',
                     f"{balance['cash']}+{investments}-{balance['policy_reserve']}-{balance['claims_payable']}-{balance['other_liabilities']}")
            number(s, identity + f' {i+1} reserve population',
                   f"OR({balance['in_force_m']}>0,{balance['policy_reserve']}=0)")
            result = {'ni': ni, 'operating': operating, 'investing': investing,
                      'cash_before': cash_before, 'balance': balance, 'book': book}
        else:
            cession = p('ceded_fraction')
            number(s, identity + f' {i+1} ceded share', cession + '<=1')
            if i == m.n:
                number(s, identity + ' continuing prior reserve', p('prior_reserve_development') + '=0')
            number(s, identity + f' {i+1} reserve release',
                   f"{p('prior_reserve_development')}>=-{prior['claims_reserve']}")
            number(s, identity + f' {i+1} payables',
                   f"{p('other_liability_change')}<={p('operating_expenses')}")
            incurred = r(2, 'incurred claims',
                         f"{p('premium_earned')}*{p('loss_ratio')}+{p('catastrophe_losses')}+{p('prior_reserve_development')}")
            recovery = r(3, 'reinsurance income', f'{incurred}*{cession}')
            ceded_written = f"{p('premium_written')}*{cession}"
            ceded_earned = f"{p('premium_earned')}*{cession}"
            ni = r(4, 'net income',
                   f"{p('premium_earned')}-{ceded_earned}-{incurred}+{recovery}-{p('reinsurance_impairment')}-{p('operating_expenses')}-{p('parent_fees_paid')}-{p('cash_taxes')}-{p('parent_tax_paid')}+{investment_income}+{investment_gain}")
            operating = r(5, 'operating cash',
                          f"{p('premium_collected')}-{p('claims_paid')}-{p('ceded_premium_paid')}+{p('reinsurance_recovered')}-{p('operating_expenses')}-{p('cash_taxes')}+{investment_income}+{p('other_liability_change')}")
            cash_before = r(7, 'cash before transfers', f"{prior['cash']}+{operating}+{investing}-{p('parent_fees_paid')}-{p('parent_tax_paid')}")
            balance = {
                'cash': r(8, 'closing cash', f'{cash_before}+{contribution}-{dis}'),
                'investments': investments,
                'premium_receivable': r(10, 'premium receivable', f"{prior['premium_receivable']}+{p('premium_written')}-{p('premium_collected')}"),
                'unearned_premium': r(11, 'unearned premium', f"{prior['unearned_premium']}+{p('premium_written')}-{p('premium_earned')}"),
                'claims_reserve': r(12, 'claims reserve', f"{prior['claims_reserve']}+{incurred}-{p('claims_paid')}"),
                'ceded_unearned_premium': r(13, 'ceded unearned premium', f"{prior['ceded_unearned_premium']}+{ceded_written}-{ceded_earned}"),
                'reinsurance_recoverable': r(14, 'reinsurance recoverable', f"{prior['reinsurance_recoverable']}+{recovery}-{p('reinsurance_recovered')}-{p('reinsurance_impairment')}"),
                'reinsurance_payable': r(15, 'reinsurance payable', f"{prior['reinsurance_payable']}+{ceded_written}-{p('ceded_premium_paid')}"),
                'other_liabilities': r(16, 'other liabilities', f"{prior['other_liabilities']}+{p('other_liability_change')}"),
            }
            book = r(17, 'closing common equity',
                     total([balance[k] for k in ('cash','investments','premium_receivable','ceded_unearned_premium','reinsurance_recoverable')])
                     + '-' + total([balance[k] for k in ('unearned_premium','claims_reserve','reinsurance_payable','other_liabilities')]))
            result = {'ni': ni, 'operating': operating, 'investing': investing,
                      'cash_before': cash_before, 'balance': balance, 'book': book}
        opening_expr = (f"{prior['cash']}+{prior['investments']}-{prior['policy_reserve']}-{prior['claims_payable']}-{prior['other_liabilities']}"
                        if life else total([prior[k] for k in ('cash','investments','premium_receivable','ceded_unearned_premium','reinsurance_recoverable')])
                        + '-' + total([prior[k] for k in ('unearned_premium','claims_reserve','reinsurance_payable','other_liabilities')]))
        same(s, identity + f' {i+1} clean surplus', book, f'{opening_expr}+{ni}+{contribution}-{dis}')
        for key in opening_keys:
            number(s, identity + f' {i+1} closing {key}', balance[key] + '>=-0.000000001')
        return result

    for s in SCENARIOS:
        columns = [*m.years, tr('Continuing', 'Continuing')]
        if life:
            columns.append(tr('Continuing +1', 'Continuing +1'))
        ws = m.sheet(('Life ' if life else 'PC ') + s,
                     (tr('Assicurazione vita — ', 'Life insurance — ') if life else tr('Assicurazione danni — ', 'P&C insurance — ')) + s,
                     columns)
        end = max(m.end, m.n + (5 if life else 4))
        _line(ws, 4, tr('Economia del prodotto, capitale legale e flussi agli azionisti; editing simulativo.',
                        'Product economics, legal capital and shareholder cash; simulation edits.'), end, height=38)
        ke, shares = cap(s, 'ke'), cap(s, 'shares_m')
        g = m.ref(s, 'terminal_growth')
        number(s, 'Shares / Ke / growth', f'AND({shares}>0,{ke}>MAX(0,{g}),{g}>-1)')
        number(s, 'Opening common equity', opening_book + '>0')
        same(s, 'Opening group book', opening_book,
             total([parent_book, opening_adj] + [cap(s, 'subsidiaries', j, 'opening_gaap_equity') for j in range(len(ids))]))
        number(s, 'Parent opening balances', f'AND({cap(s,"parent_opening_cash")}>=0,{cap(s,"parent_opening_debt")}>=0)')
        economics = {}
        for j, identity in enumerate(ids):
            opening = {key: m.ref('model', f'insurance.{j}.opening_balance', key) for key in opening_keys}
            for key, ref in opening.items():
                number(s, identity + ' opening ' + key, ref + '>=0')
            if life:
                number(s, identity + ' opening reserve population',
                       f"OR({opening['in_force_m']}>0,{opening['policy_reserve']}=0)")
                opening_expr = f"{opening['cash']}+{opening['investments']}-{opening['policy_reserve']}-{opening['claims_payable']}-{opening['other_liabilities']}"
            else:
                first_cession = econ_ref(s, j, 'ceded_fraction', 0)
                product_recoverable = m.ref('model', f'insurance.{j}.product', 'opening_paid_claims_recoverable')
                counterparty = m.value('model', f'insurance.{j}.product')['counterparty']
                number(s, identity + ' reinsurance counterparty',
                       first_cession + ('=0' if counterparty == 'none' else '>0'))
                if counterparty == 'none':
                    number(s, identity + ' no paid recoverable without treaty',
                           product_recoverable + '=0')
                same(s, identity + ' opening ceded UPR', opening['ceded_unearned_premium'],
                     f"{opening['unearned_premium']}*{first_cession}")
                same(s, identity + ' opening recoverables', opening['reinsurance_recoverable'],
                     f"{opening['claims_reserve']}*{first_cession}+{product_recoverable}")
                number(s, identity + ' opening paid recoverables', product_recoverable + '>=0')
                opening_expr = (total([opening[k] for k in ('cash','investments','premium_receivable','ceded_unearned_premium','reinsurance_recoverable')])
                                + '-' + total([opening[k] for k in ('unearned_premium','claims_reserve','reinsurance_payable','other_liabilities')]))
            same(s, identity + ' opening GAAP book', opening_expr, cap(s, 'subsidiaries', j, 'opening_gaap_equity'))
            same(s, identity + ' opening legal cash', opening['cash'], m.ref(s, 'liquidity_bridge', identity, 'opening_cash'))
            same(s, identity + ' opening statutory bridge', cap(s, 'subsidiaries', j, 'opening_statutory_capital'),
                 f"{cap(s,'subsidiaries',j,'opening_gaap_equity')}+{cap(s,'subsidiaries',j,'opening_gaap_to_statutory_equity')}")
            economics[identity] = []
            prior = opening
            for i in range(m.n + 1):
                dis = (m.ref(s, 'terminal_ledger', 'capital', 'subsidiaries',
                             next(k for k, x in enumerate(m.value(s, 'terminal_ledger')['capital']['subsidiaries']) if x['id'] == identity),
                             'proposed_distribution', 0) if i == m.n else cap(s, 'subsidiaries', j, 'proposed_distribution', i))
                contribution = (m.ref(s, 'terminal_ledger', 'capital', 'subsidiaries',
                                      next(k for k, x in enumerate(m.value(s, 'terminal_ledger')['capital']['subsidiaries']) if x['id'] == identity),
                                      'proposed_contribution', 0) if i == m.n else cap(s, 'subsidiaries', j, 'proposed_contribution', i))
                if not life and i:
                    same(s, identity + f' {i+1} constant cession',
                         continuing_ref(s, identity, 'ceded_fraction') if i == m.n else econ_ref(s, j, 'ceded_fraction', i),
                         first_cession)
                row = economic_period(ws, s, j, identity, i, prior, dis, contribution)
                economics[identity].append(row)
                prior = row['balance']
                if i == m.n:
                    for key in opening_keys:
                        same(s, identity + ' continuing ' + key, row['balance'][key],
                             f"{economics[identity][-2]['balance'][key]}*(1+{g})")
            if life:
                report = lambda *parts: m.ref(s, f'insurance.{j}.reserve_report', *parts)
                for key in ('in_force_m', 'policy_reserve'):
                    same(s, identity + ' reserve report opening ' + key, report('opening', key), opening[key])
                for key in RESERVE_DRIVERS:
                    for i in range(m.n):
                        same(s, identity + f' reserve report {key} {i+1}', report('forecast', key, i), econ_ref(s, j, key, i))
                    same(s, identity + ' reserve report continuing ' + key,
                         report('continuing', key), continuing_ref(s, identity, key))
                tail = economics[identity][-1]
                second = economic_period(ws, s, j, identity, m.n + 1, tail['balance'],
                                         f"{dis}*(1+{g})", f"{contribution}*(1+{g})", second=True)
                for key in opening_keys:
                    same(s, identity + ' second continuing ' + key, second['balance'][key],
                         f"{tail['balance'][key]}*(1+{g})")
                for key in ('ni', 'cash_before'):
                    same(s, identity + ' second continuing ' + key, second[key], f"{tail[key]}*(1+{g})")

        ledger_base = 11 + len(ids) * 45
        statutory = {j: cap(s, 'subsidiaries', j, 'opening_statutory_capital') for j in range(len(ids))}
        gaap = {j: cap(s, 'subsidiaries', j, 'opening_gaap_equity') for j in range(len(ids))}
        legal_cash = {j: m.ref(s, 'liquidity_bridge', identity, 'opening_cash') for j, identity in enumerate(ids)}
        parent_cash = cap(s, 'parent_opening_cash')
        debt = cap(s, 'parent_opening_debt')
        common = opening_book
        discounted = []
        previous_period = '0'
        for i in range(m.n + 1):
            continuing = i == m.n
            c = i + 4
            terminal = m.value(s, 'terminal_ledger')
            terminal_cap = terminal['capital']
            tref = lambda *path: m.ref(s, 'terminal_ledger', 'capital', *path)
            if continuing:
                same(s, 'Continuing parent opening cash', tref('parent_opening_cash'), parent_cash)
                same(s, 'Continuing parent opening debt', tref('parent_opening_debt'), debt)
                same(s, 'Continuing shares', tref('shares_m'), shares)
                same(s, 'Continuing Ke', tref('ke'), ke)
                number(s, 'Continuing terminal equity zero', tref('terminal_equity') + '=0')
                number(s, 'Continuing discount period', tref('discount_periods', 0) + '=1')
            else:
                period = cap(s, 'discount_periods', i)
                same(s, f'{i+1} discount calendar', period, m.period(i))
                number(s, f'{i+1} ordered discount period', f'{period}>{previous_period}')
            entity_ni, up, contributions, fees, taxes = [], [], [], [], []
            for j, identity in enumerate(ids):
                tj = next(k for k, x in enumerate(terminal_cap['subsidiaries']) if x['id'] == identity)
                econ = economics[identity][i]
                sub = (lambda key: tref('subsidiaries', tj, key, 0)) if continuing else (lambda key: cap(s, 'subsidiaries', j, key, i))
                if continuing:
                    same(s, identity + ' continuing opening GAAP', tref('subsidiaries', tj, 'opening_gaap_equity'), gaap[j])
                    same(s, identity + ' continuing opening statutory', tref('subsidiaries', tj, 'opening_statutory_capital'), statutory[j])
                    same(s, identity + ' continuing opening liquidity',
                         m.ref(s, 'terminal_ledger', 'liquidity_bridge', identity, 'opening_cash'), legal_cash[j])
                    same(s, identity + ' continuing statutory opening bridge', tref('subsidiaries', tj, 'opening_statutory_capital'),
                         f"{tref('subsidiaries',tj,'opening_gaap_equity')}+{tref('subsidiaries',tj,'opening_gaap_to_statutory_equity')}")
                # The insurance calculation is the sole owner of legal profit/cash.
                if continuing:
                    m.bind(s, 'terminal_ledger', ('capital', 'subsidiaries', tj, 'gaap_net_income', 0), econ['ni'])
                    m.bind(s, 'terminal_ledger', ('capital', 'subsidiaries', tj, 'liquidity_before_transfers', 0), econ['cash_before'])
                else:
                    m.bind(s, 'capital.subsidiaries.' + str(j) + '.gaap_net_income', (i,), econ['ni'])
                    m.bind(s, 'capital.subsidiaries.' + str(j) + '.liquidity_before_transfers', (i,), econ['cash_before'])
                entity_ni.append(econ['ni'])
                dis, contribution = sub('proposed_distribution'), sub('proposed_contribution')
                up.append(dis); contributions.append(contribution)
                for key in NONNEGATIVE_SUB_PATHS:
                    number(s, identity + f' {i+1} {key}', sub(key) + '>=0')
                number(s, identity + f' {i+1} approved upstream', f"{dis}<={sub('permitted_distribution')}")
                statutory_income = calc(ws, ledger_base + j * 10, c, identity + ' statutory income',
                                        f"{econ['ni']}+{sub('gaap_to_statutory_income')}")
                close = calc(ws, ledger_base + j * 10 + 1, c, identity + ' statutory capital',
                             f"{statutory[j]}+{statutory_income}+{sub('other_statutory_movements')}+{contribution}-{dis}")
                constraints = (terminal['capital_constraints'] if continuing else m.value(s, 'capital_constraints'))[identity]['constraints']
                crefs = []
                for k in range(len(constraints)):
                    def r(key):
                        return (m.ref(s, 'terminal_ledger', 'capital_constraints', identity,
                                      'constraints', k, key, 0) if continuing else
                                m.ref(s, 'capital_constraints', identity, 'constraints', k, key, i))
                    for key in ('exposure', 'ratio', 'buffer', 'absolute_floor'):
                        number(s, identity + f' {i+1} constraint {k} {key}', r(key) + '>=0')
                    required = f"MAX({r('exposure')}*{r('ratio')}+{r('buffer')},{r('absolute_floor')})"
                    crefs.append(required)
                    if continuing:
                        future_req = m.ref(s, 'terminal_ledger', 'capital_constraints', identity, 'constraints', k, 'terminal_requirement')
                        same(s, identity + f' continuing next requirement {k}', future_req, f'({required})*(1+{g})')
                requirement = calc(ws, ledger_base + j * 10 + 2, c, identity + ' required capital',
                                   f'MAX({",".join(crefs)})')
                if continuing:
                    m.bind(s, 'terminal_ledger', ('capital', 'subsidiaries', tj, 'required_statutory_capital', 0), requirement)
                    forecast_terms = m.value(s, 'capital_constraints')[identity]['constraints']
                    same(s, identity + ' terminal required capital', requirement,
                         f"MAX({','.join(m.ref(s,'capital_constraints',identity,'constraints',k,'terminal_requirement') for k in range(len(forecast_terms)))})")
                    same(s, identity + ' statutory continuing growth', close, f'{statutory[j]}*(1+{g})')
                else:
                    m.bind(s, 'capital.subsidiaries.' + str(j) + '.required_statutory_capital', (i,), requirement)
                number(s, identity + f' {i+1} capital buffer', f'{close}>={requirement}')
                bridge_driver = 'terminal_ledger' if continuing else 'liquidity_bridge'
                bridge_path = ('liquidity_bridge', identity) if continuing else (identity,)
                def b(key):
                    return m.ref(s, bridge_driver, *bridge_path, key, 0 if continuing else i)
                for key, derived in (('operating_cash', econ['operating']), ('investing_cash', econ['investing']),
                                     ('parent_fees_paid', continuing_ref(s, identity, 'parent_fees_paid') if continuing else econ_ref(s, j, 'parent_fees_paid', i)),
                                     ('parent_tax_paid', continuing_ref(s, identity, 'parent_tax_paid') if continuing else econ_ref(s, j, 'parent_tax_paid', i))):
                    m.bind(s, bridge_driver, (*bridge_path, key, 0 if continuing else i), derived)
                number(s, identity + f' {i+1} no subsidiary financing', b('financing_cash') + '=0')
                fees.append(b('parent_fees_paid')); taxes.append(b('parent_tax_paid'))
                bridge_before = calc(ws, ledger_base + j * 10 + 3, c, identity + ' legal cash before transfer',
                                     f"{legal_cash[j]}+{b('operating_cash')}+{b('investing_cash')}+{b('financing_cash')}-{b('parent_fees_paid')}-{b('parent_tax_paid')}")
                same(s, identity + f' {i+1} cash bridge', bridge_before, econ['cash_before'])
                cash_close = calc(ws, ledger_base + j * 10 + 4, c, identity + ' legal closing liquidity',
                                  f'{bridge_before}+{contribution}-{dis}')
                same(s, identity + f' {i+1} legal/economic cash', cash_close, econ['balance']['cash'])
                number(s, identity + f' {i+1} minimum liquidity', f"{cash_close}>={sub('minimum_liquidity')}")
                gaap[j] = calc(ws, ledger_base + j * 10 + 5, c, identity + ' GAAP equity',
                               f'{gaap[j]}+{econ["ni"]}+{contribution}-{dis}')
                same(s, identity + f' {i+1} legal/economic equity', gaap[j], econ['book'])
                if continuing:
                    same(s, identity + ' continuing liquidity growth', cash_close, f'{legal_cash[j]}*(1+{g})')
                statutory[j], legal_cash[j] = close, cash_close
            ni = calc(ws, ledger_base + len(ids) * 10 + 1, c, 'consolidated net income',
                      total([*entity_ni, (tref('parent_gaap_net_income', 0) if continuing else cap(s, 'parent_gaap_net_income', i)),
                             (tref('consolidation_adjustments', 0) if continuing else cap(s, 'consolidation_adjustments', i))]))
            if continuing:
                m.bind(s, 'terminal_income', (), ni)
                m.bind(s, 'terminal_ledger', ('capital', 'parent_cash_flows', 'admin_fees_received', 0), total(fees))
                m.bind(s, 'terminal_ledger', ('capital', 'parent_cash_flows', 'tax_transfers_received', 0), total(taxes))
                flows = lambda key: tref('parent_cash_flows', key, 0)
                minimum = tref('parent_cash_minimum', 0)
            else:
                m.bind(s, 'consolidated_income', (i,), ni)
                m.bind(s, 'capital.parent_cash_flows.admin_fees_received', (i,), total(fees))
                m.bind(s, 'capital.parent_cash_flows.tax_transfers_received', (i,), total(taxes))
                flows = lambda key: cap(s, 'parent_cash_flows', key, i)
                minimum = cap(s, 'parent_cash_minimum', i)
            number(s, f'{i+1} parent minimum cash', minimum + '>=0')
            cashflow = calc(ws, ledger_base + len(ids) * 10 + 2, c, 'parent cash flows',
                            total(flows(key) if sign > 0 else '-' + flows(key) for key, sign in CASH_SIGNS.items()))
            for key in ('debt_issued', 'debt_repaid'):
                number(s, f'{i+1} {key}', flows(key) + '>=0')
            before = calc(ws, ledger_base + len(ids) * 10 + 3, c, 'parent cash before shareholders',
                          f'{parent_cash}+{total(up)}-{total(contributions)}+{cashflow}')
            distribution = calc(ws, ledger_base + len(ids) * 10 + 4, c, 'shareholder net distribution', f'{before}-{minimum}')
            debt = calc(ws, ledger_base + len(ids) * 10 + 5, c, 'parent closing debt',
                        f"{debt}+{flows('debt_issued')}-{flows('debt_repaid')}")
            number(s, f'{i+1} parent debt', debt + '>=0')
            book = calc(ws, ledger_base + len(ids) * 10 + 6, c, 'common book', f'{common}+{ni}-{distribution}')
            number(s, f'{i+1} common book', book + '>0')
            if continuing:
                number(s, 'Continuing cash distribution positive', distribution + '>=0')
                same(s, 'Continuing clean surplus', f'{ni}-{distribution}', f'{common}*{g}')
                same(s, 'Continuing parent cash growth', minimum, f'{parent_cash}*(1+{g})')
                same(s, 'Continuing parent debt growth', debt, f'{tref("parent_opening_debt")}*(1+{g})')
                same(s, 'Continuing terminal debt', tref('terminal_debt'), debt)
                tv = calc(ws, ledger_base + len(ids) * 10 + 8, c, 'terminal equity', f'{distribution}/({ke}-{g})')
                m.bind(s, 'capital.terminal_equity', (), tv)
            else:
                funding = calc(ws, ledger_base + len(ids) * 10 + 7, c, 'shareholder funding', f'MAX(0,-{distribution})')
                m.support_calls[s].append(funding)
                number(s, f'{i+1} documented funding', f"{funding}<={m.ref(s,'funding_capacity',i)}")
                number(s, f'{i+1} funding capacity', m.ref(s, 'funding_capacity', i) + '>=0')
                discounted.append(calc(ws, ledger_base + len(ids) * 10 + 8, c, 'PV distribution',
                                       f'{distribution}/(1+{ke})^{period}'))
                previous_period = period
                common = book
                parent_cash = minimum
        same(s, 'Forecast terminal debt', cap(s, 'terminal_debt'), tref('parent_opening_debt'))
        equity = calc(ws, ledger_base + len(ids) * 10 + 11, 4, 'equity value',
                      total([*discounted, f'{tv}/(1+{ke})^{previous_period}']))
        m.results[s] = calc(ws, ledger_base + len(ids) * 10 + 12, 4, 'value per share', f'{equity}/{shares}')
        _finish(ws, ledger_base + len(ids) * 10 + 15, end)
    m.finish()
    return True
