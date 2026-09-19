"""Linked regulated asset, cash and continuing-distribution schedules."""
from bellomberg.core.language import text as tr
from .linked_model import LinkedModel, SCENARIOS, ready, total
from .documented_presentation import _finish, PERCENT, PRICE
from .rab_adapter import PATHS


def apply_rab_formulas(wb, payload):
    if not ready(payload, {'regulated_rab'}):
        return False
    m = LinkedModel(wb, payload)
    m.quotation_checks()
    shares = m.ref('model', 'shares')
    fields = ('rab', 'debt', 'cash', 'working_capital', 'unrecognized_investment')
    for scenario in SCENARIOS:
        ws = m.sheet('RAB ' + scenario, tr('Rete regolata — ', 'Regulated network — ') + scenario,
                     [*m.years, tr('Continuing', 'Continuing')])
        end = max(m.end, m.n + 4)
        opening = {k: m.ref('model', 'opening_' + k) for k in fields}
        for key, ref in opening.items():
            m.check(scenario, 'Opening ' + key, ref + ('>0' if key == 'rab' else '>=0'))
        ke, growth = (m.ref(scenario, key) for key in ('ke', 'terminal_growth'))
        m.check(scenario, 'Shares / Ke / g', f'AND({shares}>0,{ke}>MAX(0,{growth}),{growth}>-1)')
        cashflows = []
        for i in range(m.n + 1):
            c = i + 4
            p = {k: m.ref(scenario, 'continuing', k) if i == m.n else m.ref(scenario, k, i) for k in PATHS}
            for j, (key, ref) in enumerate(p.items(), 8):
                m.calc(ws, j, c, key.replace('_', ' '), ref, PERCENT if key in ('allowed_return', 'indexation') else PRICE)
                if key not in ('indexation', 'incentives', 'working_capital_change'):
                    m.check(scenario, f'{i + 1} / {key}', ref + '>=0')
            m.check(scenario, f'{i + 1} / indexation', p['indexation'] + '>-1')
            m.check(scenario, f'{i + 1} / tax allowance', p['tax_allowance'] + '=0')
            if m.value('model', 'regime')['return_basis'] == 'nominal':
                m.check(scenario, f'{i + 1} / nominal return', p['indexation'] + '=0')
            m.equal(scenario, f'{i + 1} / disposal proceeds', p['disposal_cash'], p['disposals_rab'] + '*' + p['disposal_realization_multiple'])
            r = lambda row, label, formula: m.calc(ws, row, c, label, formula)
            close = {}
            close['rab'] = r(33, tr('RAB finale', 'Closing RAB'), f"{opening['rab']}*(1+{p['indexation']})+{p['recognized_capex']}-{p['regulatory_depreciation']}-{p['disposals_rab']}")
            revenue = r(34, tr('Ricavi regolatori', 'Regulatory revenue'), f"{opening['rab']}*{p['allowed_return']}+{p['regulatory_depreciation']}+{p['allowed_opex']}+{p['incentives']}+{p['tax_allowance']}")
            income = r(35, tr('Utile dopo imposte cash', 'Income after cash tax'), f"{revenue}-{p['cash_opex']}-{p['book_depreciation']}-{p['interest_paid']}+{p['interest_received']}-{p['cash_tax']}")
            cash = r(36, tr('Cassa prima della distribuzione', 'Cash before distribution'), f"{opening['cash']}+{income}+{p['book_depreciation']}-{p['cash_capex']}-{p['working_capital_change']}+{p['disposal_cash']}+{p['debt_issued']}-{p['debt_repaid']}")
            distribution = r(37, tr('Distribuzione netta ai soci', 'Net shareholder distribution'), f"{cash}-{p['minimum_cash']}")
            funding = r(38, tr('Capitale richiesto ai soci', 'Required shareholder funding'), f'MAX(0,-{distribution})')
            m.support_calls[scenario].append(funding)
            close['debt'] = r(40, tr('Debito finale', 'Closing debt'), f"{opening['debt']}+{p['debt_issued']}-{p['debt_repaid']}")
            close['cash'] = r(41, tr('Cassa finale', 'Closing cash'), p['minimum_cash'])
            close['working_capital'] = r(42, tr('Circolante finale', 'Closing working capital'), f"{opening['working_capital']}+{p['working_capital_change']}")
            close['unrecognized_investment'] = r(43, tr('Investimenti non riconosciuti', 'Unrecognized investment'), f"{opening['unrecognized_investment']}+{p['cash_capex']}-{p['recognized_capex']}")
            for key, ref in close.items():
                m.check(scenario, f'{i + 1} / closing {key}', ref + ('>0' if key == 'rab' else '>=0'))
            m.check(scenario, f'{i + 1} / distribution and funding', f"AND({distribution}<={p['permitted_distribution']},{funding}<={p['funding_capacity']})")
            if i < m.n:
                cashflows.append(r(45, tr('Valore attuale distribuzione', 'PV distribution'), f'{distribution}/(1+{ke})^{m.period(i)}'))
            else:
                for key in fields:
                    m.equal(scenario, 'Continuing / ' + key, close[key], f'{opening[key]}*(1+{growth})')
                m.check(scenario, 'Positive continuing cash', distribution + '>0')
                terminal = r(47, tr('Valore terminale', 'Terminal value'), f'{distribution}/({ke}-{growth})')
                terminal_pv = r(48, tr('Valore attuale terminale', 'PV terminal value'), f'{terminal}/(1+{ke})^{m.period(m.n-1)}')
            opening = close
        equity = m.calc(ws, 50, 4, tr('Valore del capitale azionario', 'Equity value'), total([*cashflows, terminal_pv]))
        m.results[scenario] = m.calc(ws, 51, 4, tr('Valore per azione', 'Value per share'), f'{equity}/{shares}', PRICE)
        m.calc(ws, 53, 4, tr('Quota del valore dal terminale', 'Terminal share of value'), f'{terminal_pv}/{equity}', PERCENT)
        _finish(ws, 55, end)
    m.finish()
    return True
