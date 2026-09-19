"""Linked managed-care earnings and explicit legal-capital shareholder cash.

The terminal equity is a documented ledger input. This engine has no terminal
growth assumption or continuing-year model, so none is inferred here.
"""
from copy import deepcopy

from bellomberg.core.language import text as tr

from .distributable_equity import CASH_SIGNS, NONNEGATIVE_SUB_PATHS
from .documented_presentation import _finish, _line, PERCENT, PRICE
from .linked_model import LinkedModel, SCENARIOS, ready, total
from .managed_care import ANNUAL_INPUTS, METHOD, SIGNED_INPUTS


def _linked_payload(payload):
    """Adapt the real managed-care calendar/result shape for shared sheet plumbing."""
    result = deepcopy(payload)
    rows = result.get('analytical_quality', {}).get('rows', [])
    calendar_row = next((r for r in rows if r.get('scenario') == 'model' and r.get('driver') == 'calendar'), None)
    if calendar_row is None or not isinstance(calendar_row.get('values'), dict):
        return None
    calendar = calendar_row['values']
    periods = calendar.get('fiscal_periods')
    if not isinstance(periods, list) or not periods:
        return None
    calendar['periods'] = [{'start': p['start'], 'end': p['payment_date']} for p in periods]
    calendar['discount_convention'] = 'ACT/365F'
    projection = result.get('managed_care', {}).get('scenarios', {})
    result['calculation_details'] = {'scenarios': {
        s: {'fair_value_per_share': projection.get(s, {}).get('fair_value_per_share')}
        for s in SCENARIOS}}
    return result


def apply_managed_care_formulas(wb, payload):
    shadow = _linked_payload(payload)
    if shadow is None or not ready(shadow, {METHOD}):
        return False
    m = LinkedModel(wb, shadow)
    m.quotation_checks()
    calendar = m.calendar
    interim = calendar['actuals_kind'] == 'interim'
    segments = sorted({r['driver'].split('.')[1] for r in shadow['analytical_quality']['rows']
                       if r['scenario'] == 'base' and r['driver'].startswith('segments.') and r['driver'].endswith('.premium')})
    adjustments = sorted({r['driver'] for r in shadow['analytical_quality']['rows']
                          if r['scenario'] == 'base' and r['driver'].startswith('adjustments_after_tax.')})
    ids = [v['id'] for v in m.value('model', 'perimeter')['subsidiaries']]
    actual = {key: m.ref('model', 'actuals.' + key) for key in ('premium', 'medical_costs', 'gna', 'net_income')}
    for i, year in enumerate(calendar['years']):
        m.check('bear', f'Calendar year {i+1}', m.ref('model', 'calendar', 'years', i) + '=' + str(year))
        for scenario in ('base', 'bull'):
            m.check(scenario, f'Calendar year {i+1}', m.ref('model', 'calendar', 'years', i) + '=' + str(year))
        for scenario in SCENARIOS:
            m.check(scenario, f'Fiscal period {i+1}', m.ref('model', 'calendar', 'fiscal_periods', i, 'year') + '=' + str(year))
    for s in SCENARIOS:
        m.check(s, 'Actuals year', m.ref('model', 'calendar', 'actuals_year') + '=' +
                str(calendar['actuals_year']))
        for key, ref in actual.items():
            if key != 'net_income':
                m.check(s, 'Actual ' + key, ref + '>=0')
    end = max(m.end, m.n + 3)

    def cap(s, *parts):
        width = 3 if parts[0] == 'subsidiaries' else 2 if parts[0] == 'parent_cash_flows' else 1
        return m.ref(s, 'capital.' + '.'.join(map(str, parts[:width])), *parts[width:])

    for s in SCENARIOS:
        ws = m.sheet('Care ' + s, tr('Managed care — ', 'Managed care — ') + s)
        _line(ws, 4, tr('Conto economico annuale e capitale distribuibile residuo. Terminal equity esplicito, senza crescita implicita.',
                        'Annual earnings and residual distributable capital. Explicit terminal equity, no implicit growth.'), end, height=40)
        ke, shares = cap(s, 'ke'), cap(s, 'shares_m')
        m.check(s, 'Ke and shares', f'AND({ke}>0,{shares}>0)')
        m.check(s, 'Parent opening balances', f'AND({cap(s,"parent_opening_cash")}>=0,{cap(s,"parent_opening_debt")}>=0)')
        for j, identity in enumerate(ids):
            opening = cap(s, 'subsidiaries', j, 'opening_statutory_capital')
            gaap = cap(s, 'subsidiaries', j, 'opening_gaap_equity')
            adjustment = cap(s, 'subsidiaries', j, 'opening_gaap_to_statutory_equity')
            m.equal(s, identity + ' opening statutory bridge', opening, f'{gaap}+{adjustment}')
        opening_capital = {j: cap(s, 'subsidiaries', j, 'opening_statutory_capital') for j in range(len(ids))}
        parent_cash = cap(s, 'parent_opening_cash')
        parent_debt = cap(s, 'parent_opening_debt')
        paid, previous_period = [], '0'
        legal_start = 9 + 2 * len(segments) + 16
        ledger_base = legal_start + 8 * len(ids) + 4
        for i in range(m.n):
            c = i + 4
            year = calendar['years'][i]
            period = cap(s, 'discount_periods', i)
            m.equal(s, f'{year} payment date', period, m.period(i))
            m.check(s, f'{year} ordered discount', f'{period}>{previous_period}')
            ref = lambda name: m.ref(s, name, i)
            for name in ANNUAL_INPUTS:
                if name not in SIGNED_INPUTS:
                    m.check(s, f'{year} {name}', ref(name) + ('>0' if name == 'diluted_shares_m' else '>=0'))
            premium_parts, medical_parts = [], []
            row = 9
            for name in segments:
                premium, mcr = ref('segments.' + name + '.premium'), ref('segments.' + name + '.mcr')
                m.check(s, f'{year} {name} premium/MCR', f'AND({premium}>=0,{mcr}>=0)')
                ws.cell(row, 2, name)
                m.calc(ws, row, c, name + ' premium', premium)
                medical_parts.append(m.calc(ws, row + 1, c, name + ' medical', f'{premium}*{mcr}'))
                premium_parts.append(premium)
                row += 2
            calc = lambda offset, label, expression: m.calc(ws, row + offset, c, label, expression)
            premium = calc(0, 'annual premium', f'{total(premium_parts)}+{ref("other_premium")}')
            medical = calc(1, 'annual medical', f'{total(medical_parts)}+{ref("other_medical_costs")}')
            investment = calc(2, 'investment income', f'{ref("average_invested_assets")}*{ref("investment_yield")}')
            revenue = calc(3, 'total revenue', f'{premium}+{ref("premium_tax_revenue")}+{investment}+{ref("other_revenue")}')
            gna = calc(4, 'G&A', f'{revenue}*{ref("gna_ratio")}')
            pretax = calc(5, 'pretax income', f'{revenue}-{medical}-{ref("premium_tax_expense")}-{gna}-{ref("da")}-{ref("other_operating_costs")}-{ref("interest_expense")}')
            ni = calc(6, 'net income', f'{pretax}-{ref("tax_expense")}')
            adjusted = calc(7, 'adjusted net income', f'{ni}+{total(ref(name) for name in adjustments)}')
            calc(8, 'diluted EPS', f'{ni}/{ref("diluted_shares_m")}')
            calc(9, 'adjusted diluted EPS', f'{adjusted}/{ref("diluted_shares_m")}')
            subtract = i == 0 and interim
            remaining_ni = calc(10, 'remaining net income', ni + ('-' + actual['net_income'] if subtract else ''))
            for offset, label, amount, key in ((11, 'remaining premium', premium, 'premium'),
                                               (12, 'remaining medical', medical, 'medical_costs'),
                                               (13, 'remaining G&A', gna, 'gna')):
                remaining = calc(offset, label, amount + ('-' + actual[key] if subtract else ''))
                m.check(s, f'{year} {label}', remaining + '>=-0.01')
            subsidiary_income, up, contributions = [], [], []
            for j, identity in enumerate(ids):
                sub = lambda key: cap(s, 'subsidiaries', j, key, i)
                statutory_income = m.calc(ws, legal_start + 8 * j, c, identity + ' statutory income',
                                          f"{sub('gaap_net_income')}+{sub('gaap_to_statutory_income')}")
                dis, contribution = sub('proposed_distribution'), sub('proposed_contribution')
                closing = m.calc(ws, legal_start + 8 * j + 1, c, identity + ' closing statutory capital',
                                 f"{opening_capital[j]}+{statutory_income}+{sub('other_statutory_movements')}+{contribution}-{dis}")
                m.check(s, f'{year} {identity} capital', f"{closing}>={sub('required_statutory_capital')}")
                m.check(s, f'{year} {identity} liquidity', f"{sub('liquidity_before_transfers')}+{contribution}-{dis}>={sub('minimum_liquidity')}")
                m.check(s, f'{year} {identity} permitted', f"{dis}<={sub('permitted_distribution')}")
                for key in NONNEGATIVE_SUB_PATHS:
                    m.check(s, f'{year} {identity} {key}', sub(key) + '>=0')
                opening_capital[j] = closing
                subsidiary_income.append(sub('gaap_net_income'))
                up.append(dis); contributions.append(contribution)
            m.equal(s, f'{year} consolidated income bridge', remaining_ni,
                    total([*subsidiary_income, cap(s, 'parent_gaap_net_income', i), cap(s, 'consolidation_adjustments', i)]))
            flows = lambda key: cap(s, 'parent_cash_flows', key, i)
            for name in ('debt_issued', 'debt_repaid'):
                m.check(s, f'{year} {name}', flows(name) + '>=0')
            cashflow = m.calc(ws, ledger_base, c, 'parent net cash flow',
                              total(flows(key) if sign > 0 else '-' + flows(key) for key, sign in CASH_SIGNS.items()))
            before = m.calc(ws, ledger_base + 1, c, 'parent cash before shareholders',
                            f'{parent_cash}+{total(up)}-{total(contributions)}+{cashflow}')
            minimum = cap(s, 'parent_cash_minimum', i)
            m.check(s, f'{year} minimum parent cash', minimum + '>=0')
            distribution = m.calc(ws, ledger_base + 2, c, 'net shareholder distribution', f'{before}-{minimum}')
            funding = m.calc(ws, ledger_base + 3, c, 'shareholder contribution required', f'MAX(0,-{distribution})')
            m.support_calls[s].append(funding)
            parent_debt = m.calc(ws, ledger_base + 4, c, 'closing parent debt',
                                 f"{parent_debt}+{flows('debt_issued')}-{flows('debt_repaid')}")
            m.check(s, f'{year} parent debt', parent_debt + '>=0')
            paid.append(m.calc(ws, ledger_base + 5, c, 'PV shareholder flow', f'{distribution}/(1+{ke})^{period}'))
            parent_cash, previous_period = minimum, period
        m.equal(s, 'Parent closing debt / terminal debt', parent_debt, cap(s, 'terminal_debt'))
        terminal = cap(s, 'terminal_equity')
        m.check(s, 'Explicit terminal equity', terminal + '>=0')
        equity = m.calc(ws, ledger_base + 8, 4, 'equity value',
                        f'{total(paid)}+{terminal}/(1+{ke})^{previous_period}')
        m.results[s] = m.calc(ws, ledger_base + 9, 4, 'value per share', f'{equity}/{shares}')
        _finish(ws, ledger_base + 12, end)
    m.finish()
    return True
