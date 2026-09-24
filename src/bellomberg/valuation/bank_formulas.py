"""Linked common-equity, legal-capital and parent-cash bank schedules.

The legal and accounting descriptions remain the source record text. Numeric
projections below are simulations, guarded against the bank engine's contract.
"""
from bellomberg.core.language import text as tr

from .distributable_equity import CASH_SIGNS, NONNEGATIVE_SUB_PATHS
from .documented_presentation import _finish, _line
from .linked_model import LinkedModel, SCENARIOS, ready, total


EARNINGS = {
    'net_interest_income': 1, 'fee_income': 1, 'operating_expenses': -1,
    'credit_losses': -1, 'taxes': -1, 'other_income': 1,
    'preferred_and_minorities': -1, 'other_comprehensive_income': 1,
}


def apply_bank_formulas(wb, payload):
    if not ready(payload, {'bank_residual_income'}):
        return False
    m = LinkedModel(wb, payload)
    if not m.n:
        return False
    m.quotation_checks()
    opening_book = m.ref('model', 'opening_common_equity')
    intangibles = m.ref('model', 'opening_intangibles')
    parent_book = m.ref('model', 'opening_parent_equity')
    consolidation_open = m.ref('model', 'opening_consolidation_adjustments')
    legal_ids = [x['id'] for x in m.value('model', 'legal_structure')['subsidiaries']]

    def cap(s, *parts):
        width = 3 if parts[0] in ('subsidiaries', 'parent_cash_flows') else 1
        if parts[0] == 'parent_cash_flows':
            width = 2
        return m.ref(s, 'capital.' + '.'.join(map(str, parts[:width])), *parts[width:])

    def guard(s, label, expression):
        m.check(s, label, expression)

    def equal(s, label, a, b):
        m.equal(s, label, a, b)

    def calc(ws, row, column, label, expression):
        return m.calc(ws, row, column, label, expression)

    for s in SCENARIOS:
        ws = m.sheet('Bank ' + s, tr('Banca — ', 'Bank — ') + s,
                     [*m.years, tr('Continuing', 'Continuing')])
        _line(ws, 4, tr('Common equity, vincoli delle entita e cassa parent. Il continuing e un anno di prova.',
                        'Common equity, entity constraints and parent cash. Continuing is one proof year.'),
              max(m.end, m.n + 4), height=40)
        ke, shares = cap(s, 'ke'), cap(s, 'shares_m')
        g, roe = m.ref(s, 'terminal_growth'), m.ref(s, 'terminal_roe')
        guard(s, 'Common book and intangibles', f'AND({opening_book}>0,{intangibles}>=0,{intangibles}<{opening_book})')
        guard(s, 'Shares, Ke, ROE and growth', f'AND({shares}>0,{ke}>MAX(0,{g}),{roe}>{g},{g}>-1)')
        guard(s, 'Parent opening balances', f'AND({cap(s,"parent_opening_cash")}>=0,{cap(s,"parent_opening_debt")}>=0)')
        equal(s, 'Opening common equity', opening_book,
              total([parent_book, consolidation_open] + [cap(s, 'subsidiaries', j, 'opening_gaap_equity') for j in range(len(legal_ids))]))
        for j, identity in enumerate(legal_ids):
            eq, adj, statutory = (cap(s, 'subsidiaries', j, k) for k in
                                  ('opening_gaap_equity', 'opening_gaap_to_statutory_equity', 'opening_statutory_capital'))
            equal(s, identity + ' opening statutory bridge', statutory, f'{eq}+{adj}')
            guard(s, identity + ' opening statutory capital', statutory + '>=0')

        book = opening_book
        sub_capital = {j: cap(s, 'subsidiaries', j, 'opening_statutory_capital') for j in range(len(legal_ids))}
        sub_cash = {j: m.ref(s, 'liquidity_bridge', identity, 'opening_cash') for j, identity in enumerate(legal_ids)}
        parent_cash = cap(s, 'parent_opening_cash')
        debt = cap(s, 'parent_opening_debt')
        paid = []
        residuals = []
        prior_period = '0'
        last_sub_books = {j: cap(s, 'subsidiaries', j, 'opening_gaap_equity') for j in range(len(legal_ids))}
        for i in range(m.n):
            c = i + 4
            period = cap(s, 'discount_periods', i)
            equal(s, f'{i+1} discount calendar', period, m.period(i))
            guard(s, f'{i+1} discount period', f'{period}>{prior_period}')
            component_refs = [m.ref(s, key, i) for key in EARNINGS]
            for key, ref in zip(EARNINGS, component_refs):
                if key == 'other_comprehensive_income':
                    guard(s, f'{i+1} OCI bridge unsupported', ref + '=0')
            ni = calc(ws, 9, c, tr('Utile consolidato', 'Consolidated net income'),
                      total(ref if EARNINGS[key] > 0 else '-' + ref for key, ref in zip(EARNINGS, component_refs)))
            sub_ni, up, contributions = [], [], []
            fees, taxes = [], []
            for j, identity in enumerate(legal_ids):
                base = 20 + j * 16
                sub = lambda key: cap(s, 'subsidiaries', j, key, i)
                subincome = sub('gaap_net_income')
                sub_ni.append(subincome)
                statutory_income = calc(ws, base, c, identity + ' statutory NI',
                                        f"{subincome}+{sub('gaap_to_statutory_income')}")
                dis, contribution = sub('proposed_distribution'), sub('proposed_contribution')
                up.append(dis); contributions.append(contribution)
                closing = calc(ws, base + 1, c, identity + ' statutory capital',
                               f"{sub_capital[j]}+{statutory_income}+{sub('other_statutory_movements')}+{contribution}-{dis}")
                req = sub('required_statutory_capital')
                guard(s, f'{identity} {i+1} statutory headroom', f'{closing}>={req}')
                for key in NONNEGATIVE_SUB_PATHS:
                    guard(s, f'{identity} {i+1} {key}', sub(key) + '>=0')
                guard(s, f'{identity} {i+1} permitted upstream', f"{dis}<={sub('permitted_distribution')}")
                limits = []
                for k, constraint in enumerate(m.value(s, 'capital_constraints')[identity]['constraints']):
                    p = lambda key: m.ref(s, 'capital_constraints', identity, 'constraints', k, key, i)
                    for key in ('exposure', 'ratio', 'buffer', 'absolute_floor'):
                        guard(s, f'{identity} {i+1} {k} {key}', p(key) + '>=0')
                    limits.append(f"MAX({p('exposure')}*{p('ratio')}+{p('buffer')},{p('absolute_floor')})")
                required = calc(ws, base + 2, c, identity + ' required capital', f'MAX({",".join(limits)})')
                m.bind(s, 'capital.subsidiaries.' + str(j) + '.required_statutory_capital', (i,), required)
                bridge = lambda key: m.ref(s, 'liquidity_bridge', identity, key, i)
                before = calc(ws, base + 3, c, identity + ' cash before transfers',
                              f"{sub_cash[j]}+{bridge('operating_cash')}+{bridge('investing_cash')}+{bridge('financing_cash')}-{bridge('parent_fees_paid')}-{bridge('parent_tax_paid')}")
                m.bind(s, 'capital.subsidiaries.' + str(j) + '.liquidity_before_transfers', (i,), before)
                end_cash = calc(ws, base + 4, c, identity + ' closing cash', f'{before}+{contribution}-{dis}')
                guard(s, f'{identity} {i+1} liquidity', f"{end_cash}>={sub('minimum_liquidity')}")
                guard(s, f'{identity} {i+1} opening liquidity', sub_cash[j] + '>=0')
                sub_capital[j], sub_cash[j] = closing, end_cash
                last_sub_books[j] = calc(ws, base + 5, c, identity + ' GAAP equity',
                                         f'{last_sub_books[j]}+{subincome}+{contribution}-{dis}')
                fees.append(bridge('parent_fees_paid')); taxes.append(bridge('parent_tax_paid'))
            equal(s, f'{i+1} NI consolidation', ni,
                  total([*sub_ni, cap(s, 'parent_gaap_net_income', i), cap(s, 'consolidation_adjustments', i)]))
            equal(s, f'{i+1} parent admin transfers', cap(s, 'parent_cash_flows', 'admin_fees_received', i), total(fees))
            equal(s, f'{i+1} parent tax transfers', cap(s, 'parent_cash_flows', 'tax_transfers_received', i), total(taxes))
            flows = []
            for key, sign in CASH_SIGNS.items():
                ref = cap(s, 'parent_cash_flows', key, i)
                flows.append(ref if sign > 0 else '-' + ref)
                if key in ('debt_issued', 'debt_repaid'):
                    guard(s, f'{i+1} {key}', ref + '>=0')
            parent_net = calc(ws, 12, c, tr('Flusso parent', 'Parent cash flow'), total(flows))
            before = calc(ws, 13, c, tr('Cassa prima degli azionisti', 'Cash before shareholders'),
                          f'{parent_cash}+{total(up)}-{total(contributions)}+{parent_net}')
            minimum = cap(s, 'parent_cash_minimum', i)
            guard(s, f'{i+1} minimum parent cash', minimum + '>=0')
            distribution = calc(ws, 14, c, tr('Distribuzione netta', 'Net distribution'), f'{before}-{minimum}')
            funding = calc(ws, 15, c, tr('Apporto richiesto', 'Required funding'), f'MAX(0,-{distribution})')
            m.support_calls[s].append(funding)
            guard(s, f'{i+1} documented funding capacity', f"{funding}<={m.ref(s,'funding_capacity',i)}")
            guard(s, f'{i+1} funding capacity', m.ref(s, 'funding_capacity', i) + '>=0')
            debt = calc(ws, 16, c, tr('Debito parent finale', 'Closing parent debt'),
                        f"{debt}+{cap(s,'parent_cash_flows','debt_issued',i)}-{cap(s,'parent_cash_flows','debt_repaid',i)}")
            guard(s, f'{i+1} parent debt', debt + '>=0')
            next_book = calc(ws, 10, c, tr('Common equity finale', 'Closing common equity'),
                             f'{book}+{ni}-{distribution}')
            guard(s, f'{i+1} common equity', next_book + '>0')
            charge = calc(ws, 17, c, tr('Costo capitale', 'Equity charge'),
                          f'{book}*((1+{ke})^({period}-{prior_period})-1)')
            residual = calc(ws, 18, c, tr('Reddito residuale attualizzato', 'Discounted residual income'),
                            f'({ni}-{charge})/(1+{ke})^{period}')
            residuals.append(residual)
            paid.append(calc(ws, 19, c, tr('Distribuzione attualizzata', 'Discounted distribution'),
                             f'{distribution}/(1+{ke})^{period}'))
            book, parent_cash, prior_period = next_book, minimum, period
        equal(s, 'Terminal debt', cap(s, 'terminal_debt'), debt)
        tv = calc(ws, 10, m.n + 4, tr('P/TBV terminale', 'Terminal P/TBV equity'),
                  f'{book}*({roe}-{g})/({ke}-{g})')
        m.bind(s, 'capital.terminal_equity', (), tv)
        terminal = m.value(s, 'terminal_ledger')
        tcap = terminal['capital']
        retained_mode=terminal.get('statutory_projection')=='retained_flows_at_g'
        if retained_mode:
            ws['B4']=tr('Flussi trattenuti in crescita al g dichiarato; copertura del capitale verificata in perpetuita. Cassa e common equity riconciliati.',
                        'Retained flows grow at declared g; capital coverage is checked in perpetuity. Cash and common equity reconcile.')
        tref = lambda *parts: m.ref(s, 'terminal_ledger', 'capital', *parts)
        equal(s, 'Continuing parent cash opening', tref('parent_opening_cash'), parent_cash)
        equal(s, 'Continuing parent debt opening', tref('parent_opening_debt'), debt)
        equal(s, 'Continuing shares', tref('shares_m'), shares)
        equal(s, 'Continuing Ke', tref('ke'), ke)
        guard(s, 'Continuing terminal equity zero', tref('terminal_equity') + '=0')
        guard(s, 'Continuing one-year discount', tref('discount_periods', 0) + '=1')
        tni = calc(ws, 9, m.n + 4, tr('Utile continuing', 'Continuing income'), f'{book}*{roe}')
        terminal_up, terminal_contribution, terminal_sub_ni = [], [], []
        terminal_fees, terminal_taxes = [], []
        for j, identity in enumerate(legal_ids):
            tj = next(k for k, item in enumerate(tcap['subsidiaries']) if item['id'] == identity)
            base = 20 + j * 16
            sub = lambda key: tref('subsidiaries', tj, key, 0)
            op = lambda key: tref('subsidiaries', tj, key)
            equal(s, identity + ' continuing GAAP opening', op('opening_gaap_equity'), last_sub_books[j])
            equal(s, identity + ' continuing statutory opening', op('opening_statutory_capital'), sub_capital[j])
            equal(s, identity + ' continuing statutory bridge', op('opening_statutory_capital'),
                  f"{op('opening_gaap_equity')}+{op('opening_gaap_to_statutory_equity')}")
            equal(s, identity + ' continuing cash opening',
                  m.ref(s, 'terminal_ledger', 'liquidity_bridge', identity, 'opening_cash'), sub_cash[j])
            terminal_sub_ni.append(sub('gaap_net_income'))
            statutory_income = calc(ws, base, m.n + 4, identity + ' continuing statutory NI',
                                    f"{sub('gaap_net_income')}+{sub('gaap_to_statutory_income')}")
            dis, contribution = sub('proposed_distribution'), sub('proposed_contribution')
            terminal_up.append(dis); terminal_contribution.append(contribution)
            closing = calc(ws, base + 1, m.n + 4, identity + ' continuing statutory capital',
                           f"{sub_capital[j]}+{statutory_income}+{sub('other_statutory_movements')}+{contribution}-{dis}")
            if retained_mode:
                retained=f'({closing}-{sub_capital[j]})'
                left=f'IF({g}>0,{retained}*(1+{g}),{retained})'
                right=f'IF({g}>0,{g}*{sub("required_statutory_capital")},IF({g}<0,{g}*{sub_capital[j]},0))'
                guard(s,identity+' perpetual capital coverage',
                      f'OR({left}>={right},ABS({left}-{right})<=MAX(0.00000001,0.000000001*MAX(ABS({left}),ABS({right}))))')
            else:
                equal(s, identity + ' continuing capital growth', closing, f'{sub_capital[j]}*(1+{g})')
            guard(s, identity + ' continuing capital required', f"{closing}>={sub('required_statutory_capital')}")
            guard(s, identity + ' continuing upstream permitted', f"{dis}<={sub('permitted_distribution')}")
            for key in NONNEGATIVE_SUB_PATHS:
                guard(s, identity + ' continuing ' + key, sub(key) + '>=0')
            limits = []
            for k, constraint in enumerate(terminal['capital_constraints'][identity]['constraints']):
                p = lambda key: m.ref(s, 'terminal_ledger', 'capital_constraints', identity, 'constraints', k, key, 0)
                for key in ('exposure', 'ratio', 'buffer', 'absolute_floor'):
                    guard(s, identity + f' continuing {k} {key}', p(key) + '>=0')
                req = f"MAX({p('exposure')}*{p('ratio')}+{p('buffer')},{p('absolute_floor')})"
                limits.append(req)
                continuing_requirement = m.ref(s, 'terminal_ledger', 'capital_constraints',
                                               identity, 'constraints', k, 'terminal_requirement')
                equal(s, identity + f' continuing next requirement {k}',
                      continuing_requirement, f'({req})*(1+{g})')
            required = calc(ws, base + 2, m.n + 4, identity + ' continuing required capital', f'MAX({",".join(limits)})')
            m.bind(s, 'terminal_ledger', ('capital', 'subsidiaries', tj, 'required_statutory_capital', 0), required)
            equal(s, identity + ' terminal requirement', required,
                  f"MAX({','.join(m.ref(s,'capital_constraints',identity,'constraints',k,'terminal_requirement') for k in range(len(m.value(s,'capital_constraints')[identity]['constraints'])))})")
            bridge = lambda key: m.ref(s, 'terminal_ledger', 'liquidity_bridge', identity, key, 0)
            before = calc(ws, base + 3, m.n + 4, identity + ' continuing cash before transfers',
                          f"{sub_cash[j]}+{bridge('operating_cash')}+{bridge('investing_cash')}+{bridge('financing_cash')}-{bridge('parent_fees_paid')}-{bridge('parent_tax_paid')}")
            m.bind(s, 'terminal_ledger', ('capital', 'subsidiaries', tj, 'liquidity_before_transfers', 0), before)
            endcash = calc(ws, base + 4, m.n + 4, identity + ' continuing closing cash', f'{before}+{contribution}-{dis}')
            equal(s, identity + ' continuing liquidity growth', endcash, f'{sub_cash[j]}*(1+{g})')
            guard(s, identity + ' continuing minimum liquidity', f"{endcash}>={sub('minimum_liquidity')}")
            terminal_fees.append(bridge('parent_fees_paid')); terminal_taxes.append(bridge('parent_tax_paid'))
        equal(s, 'Continuing consolidated NI', tni,
              total([*terminal_sub_ni, tref('parent_gaap_net_income', 0), tref('consolidation_adjustments', 0)]))
        equal(s, 'Continuing parent admin transfers', tref('parent_cash_flows', 'admin_fees_received', 0), total(terminal_fees))
        equal(s, 'Continuing parent tax transfers', tref('parent_cash_flows', 'tax_transfers_received', 0), total(terminal_taxes))
        tflows = []
        for key, sign in CASH_SIGNS.items():
            ref = tref('parent_cash_flows', key, 0)
            tflows.append(ref if sign > 0 else '-' + ref)
            if key in ('debt_issued', 'debt_repaid'):
                guard(s, 'Continuing ' + key, ref + '>=0')
        net = calc(ws, 12, m.n + 4, tr('Flusso parent continuing', 'Continuing parent cash flow'), total(tflows))
        before = calc(ws, 13, m.n + 4, tr('Cassa parent continuing', 'Continuing parent cash'),
                      f'{parent_cash}+{total(terminal_up)}-{total(terminal_contribution)}+{net}')
        minimum = tref('parent_cash_minimum', 0)
        guard(s, 'Continuing minimum cash', minimum + '>=0')
        continuing_distribution = calc(ws, 14, m.n + 4, tr('Distribuzione continuing', 'Continuing distribution'), f'{before}-{minimum}')
        guard(s, 'Continuing distribution nonnegative', continuing_distribution + '>=0')
        equal(s, 'Continuing clean surplus', f'{tni}-{continuing_distribution}', f'{book}*{g}')
        equal(s, 'Continuing parent cash growth', minimum, f'{parent_cash}*(1+{g})')
        next_debt = calc(ws, 16, m.n + 4, tr('Debito continuing', 'Continuing debt'),
                         f"{debt}+{tref('parent_cash_flows','debt_issued',0)}-{tref('parent_cash_flows','debt_repaid',0)}")
        equal(s, 'Continuing parent debt growth', next_debt, f'{debt}*(1+{g})')
        equal(s, 'Continuing terminal debt', tref('terminal_debt'), next_debt)
        guard(s, 'Continuing terminal equity zero', tref('terminal_equity') + '=0')
        equity_cash = calc(ws, 55 + len(legal_ids) * 16, 4, tr('Capitale da distribuzioni', 'Equity from distributions'),
                           f'{total(paid)}+{tv}/(1+{ke})^{prior_period}')
        equity_ri = calc(ws, 56 + len(legal_ids) * 16, 4, tr('Capitale da reddito residuale', 'Residual income equity'),
                         f'{opening_book}+{total(residuals)}+({tv}-{book})/(1+{ke})^{prior_period}')
        equal(s, 'Cash and residual income value', equity_cash, equity_ri)
        m.results[s] = calc(ws, 57 + len(legal_ids) * 16, 4,
                            tr('Valore per azione', 'Value per share'), f'{equity_cash}/{shares}')
        _finish(ws, 60 + len(legal_ids) * 16, max(m.end, m.n + 4))
    m.finish()
    from .bank_presentation import apply_bank_summary
    apply_bank_summary(m)
    return True
