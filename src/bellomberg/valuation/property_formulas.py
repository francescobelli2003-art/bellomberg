"""Stabilized property NAV and separate forward cash, FFO and AFFO formulas."""
from bellomberg.core.language import text as tr
from .linked_model import LinkedModel, SCENARIOS, ready, total
from .documented_presentation import _finish, _line, PRICE, PERCENT
from .real_estate_adapter import INCOME, CAPEX


def apply_property_formulas(wb, payload):
    if not ready(payload, {'property_nav'}):
        return False
    m = LinkedModel(wb, payload); m.quotation_checks()
    shares = m.ref('model', 'shares')
    comps = {k: m.ref('model', 'components', k) for k in m.value('model', 'components')}
    loans = m.value('model', 'debt_schedule')
    end = m.value('model', 'forward_year')['end']
    debt = {k: m.ref('model', 'debt_schedule', k, 'face_value') for k in loans}
    settlements = {k: m.ref('model', 'debt_schedule', k, 'settlement_value') for k in loans}
    interest = total(debt[k] + '*' + m.ref('model', 'debt_schedule', k, 'annual_coupon') for k in loans)
    due = {k: debt[k] for k in loans if loans[k]['maturity'] == end}
    m.bind('model', 'components', ('debt',), total(settlements.values()))
    for s in SCENARIOS:
        ws = m.sheet('Property ' + s, tr('Immobili — ', 'Properties — ') + s, [end])
        _line(ws, 4, tr('NAV alla data iniziale. La cassa dell anno successivo verifica la sostenibilita e non si aggiunge al NAV.',
                        'Opening NAV. Forward-year cash checks sustainability and is not added to NAV.'), 6, height=38)
        m.check(s, 'Shares / targets', f"AND({shares}>0,{m.ref(s,'nav_target')}>0,{m.ref(s,'central_cost_multiple')}>0)")
        for key, ref in comps.items():
            m.check(s, 'Opening ' + key, ref + ('=0' if key in ('preferred', 'equity_adjustments') else '>=0'))
        for k in loans:
            m.check(s, 'Debt ' + k, f"AND({debt[k]}>0,{settlements[k]}>={debt[k]},{m.ref('model','debt_schedule',k,'annual_coupon')}>=0)")
        assets, noies, upkeep, backlogs, straight, depreciation = [], [], [], [], [], []
        row = 9
        for name in m.value('model', 'property_scope'):
            _line(ws, row, name, 6, True); row += 1
            p = {k: m.ref(s, 'property_income', name, k) for k in INCOME}
            c = {k: m.ref(s, 'property_capex', name, k) for k in CAPEX}
            rate = m.ref(s, 'property_values', name, 'cap_rate')
            for key, ref in {**p, **c}.items():
                if key != 'straight_line_rent':
                    m.check(s, name + ' / ' + key, ref + ('=0' if key == 'expansion' else '>=0'))
            m.check(s, name + ' / cap rate and occupancy', f"AND({rate}>0,{p['occupancy']}<=1)")
            calc = lambda offset, label, expression, fmt=PRICE: m.calc(ws, row + offset, 4, label, expression, fmt)
            rent = calc(0, tr('Canoni cash annui', 'Annual cash rent'), f"{p['area_m']}*{p['annual_rent_per_area']}*{p['occupancy']}")
            noi = calc(1, 'Cash NOI', f"{rent}+{p['other_income']}+{p['recoveries']}-{p['cash_operating_costs']}-{p['cash_lease_incentives']}")
            recurring = calc(2, tr('Capex ricorrente', 'Recurring capex'), total(c[k] for k in ('maintenance','tenant_improvements','leasing_costs')))
            calc(3, tr('Cap rate', 'Cap rate'), rate, PERCENT)
            calc(4, tr('Lavori arretrati', 'Backlog capex'), c['backlog'])
            value = calc(5, tr('Valore dell immobile', 'Property value'), f"{noi}/{rate}-{c['backlog']}")
            m.check(s, name + ' / stabilized asset', f'AND({noi}>{recurring},{value}>0)')
            m.bind(s, 'property_values', (name, 'stabilized_noi'), noi)
            assets.append(value); noies.append(noi); upkeep.append(recurring); backlogs.append(c['backlog'])
            straight.append(p['straight_line_rent']); depreciation.append(p['property_depreciation'])
            row += 8
        b = {k: m.ref(s, 'ffo_bridge', k) for k in m.value(s, 'ffo_bridge')}
        for key in ('central_cash_cost', 'cash_tax', 'asset_sale_gains', 'property_impairments'):
            m.check(s, key, b[key] + ('=0' if key in ('asset_sale_gains','property_impairments') else '>=0'))
        r = lambda offset, label, expression: m.calc(ws, row + offset, 4, label, expression)
        ni = r(0, tr('Utile netto', 'Net income'), f"{total(noies)}+{total(straight)}-{total(depreciation)}-{b['central_cash_cost']}-{interest}-{b['cash_tax']}")
        ffo = r(1, 'FFO', ni + '+' + total(depreciation))
        affo = r(2, 'AFFO', f'{ffo}-{total(straight)}-{total(upkeep)}')
        for key, ref in (('net_income', ni), ('ffo', ffo), ('affo', affo)):
            m.bind(s, 'ffo_bridge', (key,), ref)
        funding = {k: m.ref(s, 'funding', k) for k in ('minimum_cash','shareholder_distribution','equity_contribution')}
        m.support_calls[s].append(funding['equity_contribution'])
        for key, ref in funding.items():
            m.check(s, 'Funding / ' + key, ref + '>=0')
        refis = []
        for k in m.value(s, 'funding')['refinancing']:
            ref = m.ref(s, 'funding', 'refinancing', k, 'amount'); refis.append(ref)
            m.check(s, 'Refinancing ' + k, f'AND({ref}>0,{ref}<={due[k]})')
        if m.value(s, 'funding')['equity_commitment_id'] == 'none':
            m.check(s, 'No new equity commitment', funding['equity_contribution'] + '=0')
        else:
            m.check(s, 'Existing equity commitment', funding['equity_contribution'] + '>0')
        other = total(comps[k] for k in ('other_liabilities','accrued_fees','distributions_payable','tax'))
        cash = r(4, tr('Cassa finale forward', 'Forward closing cash'), f"{comps['cash']}+{affo}-{total(backlogs)}-{total(due.values())}-{other}+{total(refis)}+{funding['equity_contribution']}-{funding['shareholder_distribution']}")
        m.check(s, 'Opening cash after backlog', f"{comps['cash']}-{total(backlogs)}>={funding['minimum_cash']}")
        m.check(s, 'Forward liquidity', f"{cash}>={funding['minimum_cash']}")
        central = r(6, tr('Valore costi centrali e imposte', 'Capitalized central costs and tax'), f"({b['central_cash_cost']}+{b['cash_tax']})*{m.ref(s,'central_cost_multiple')}")
        nav = r(7, tr('NAV del capitale ordinario', 'Common equity NAV'), f"{total(assets)}+{comps['cash']}-{comps['debt']}-{other}-{central}")
        m.results[s] = r(8, tr('Valore per azione', 'Value per share'), f"{nav}*{m.ref(s,'nav_target')}/{shares}")
        _finish(ws, row + 11, 6)
    m.finish()
    return True
