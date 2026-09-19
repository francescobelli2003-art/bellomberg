"""Finite licensed-development decision tree with live original-holder cash flows."""
from bellomberg.core.language import text as tr

from .documented_presentation import _finish, _line, PERCENT, PRICE
from .linked_model import LinkedModel, SCENARIOS, ready, total


def apply_development_formulas(wb, payload):
    if not ready(payload, {'development_rnpv'}):
        return False
    m = LinkedModel(wb, payload)
    m.quotation_checks()
    asset = m.value('model', 'asset')
    order = asset['stage_order']
    original_shares = m.ref('model', 'shares')
    opening_cash = m.ref('model', 'opening_cash')
    opening_buffer = m.ref('model', 'opening_cash_buffer')
    wind_down = m.ref('model', 'life', 'final_wind_down_cost')
    for s in SCENARIOS:
        summary = m.sheet('Dev ' + s, tr('Albero decisionale — ', 'Decision tree — ') + s,
                          [tr('Probabilita', 'Probability'), tr('FV contributo', 'Value contribution')])
        _line(summary, 4, tr('Rami condizionali fino alla scadenza; nessun residuo oltre il contratto.',
                             'Conditional branches through expiry; no value after contract end.'), m.end, height=38)
        ku = m.ref(s, 'ku')
        shield_rate = m.ref(s, 'tax_shield_discount_rate')
        finance_cost = m.ref(s, 'financing_cost_pv')
        risk_rate = m.ref(s, 'risk_contract', 'rate_excluding_modeled_technical_risk')
        risk_addon = m.ref(s, 'risk_contract', 'modeled_transition_risk_addon')
        m.check(s, 'Opening cash / buffer / shares',
                f'AND({opening_cash}>=0,{opening_buffer}>=0,{opening_buffer}<={opening_cash},{original_shares}>0)')
        m.check(s, 'Rates / excluded financing effects',
                f'AND({ku}>0,{shield_rate}>0,{finance_cost}=0,{risk_addon}=0)')
        m.equal(s, 'No duplicate modeled transition risk', risk_rate, ku)
        m.check(s, 'Final wind-down cost', wind_down + '>=0')
        stage = m.value(s, 'stage_schedule')
        probabilities = {name: m.ref(s, 'probabilities', name) for name in order}
        reach_start = 12 + len(order)
        result_row = reach_start + len(order) + 3
        reach = '1'
        branches = []
        for j, name in enumerate(order):
            p = probabilities[name]
            m.check(s, name + ' conditional probability', f'AND({p}>=0,{p}<=1)')
            m.check(s, name + ' positive transition representable',
                    f'OR({reach}=0,{p}=0,{reach}*{p}>0)')
            fail = m.calc(summary, 9 + j, 4, 'failure: ' + name, f'{reach}*(1-{p})', PERCENT)
            branches.append((name, fail, stage[name]['period']))
            reach = m.calc(summary, reach_start + j, 4, name + ' success reach', f'{reach}*{p}', PERCENT)
        success = m.calc(summary, 9 + len(order), 4, 'full success', reach, PERCENT)
        branches.append((None, success, m.n))
        m.equal(s, 'Exhaustive outcome probabilities', total(b[1] for b in branches), '1')
        final_stage = stage[order[-1]]['period']
        for j, name in enumerate(order):
            expected_period = stage[name]['period']
            period = m.ref(s, 'stage_schedule', name, 'period')
            m.check(s, name + ' immutable stage year', period + '=' + str(expected_period))
            m.check(s, name + ' stage order / horizon',
                    f'AND({period}=INT({period}),{period}>={1 if j == 0 else stage[order[j-1]]["period"]+1},{period}<{m.n})')
            for key in ('cost', 'success_received', 'success_paid', 'failure_close_cost'):
                m.check(s, name + ' ' + key, m.ref(s, 'stage_schedule', name, key) + '>=0')
        commitments = m.value(s, 'funding')['commitments']
        for i in range(m.n):
            tax = m.ref(s, 'tax_rate', i)
            minimum = m.ref(s, 'minimum_cash', i)
            central = m.ref(s, 'central_cash_cost', i)
            shield = m.ref(s, 'expected_tax_shield', i)
            capacity = m.ref(s, 'funding', 'capacities', i)
            m.check(s, f'{i+1} tax / buffer / central / shield',
                    f'AND({tax}>=0,{tax}<=1,{minimum}>=0,{central}>=0,{shield}=0)')
            m.check(s, f'{i+1} committed funding shape',
                    capacity + ('>0' if str(i) in commitments else '=0'))
            for key in ('units', 'net_price_per_unit', 'incoming_royalty_rate',
                        'outgoing_royalty_rate', 'cash_operating_cost'):
                ref = m.ref(s, 'commercial', key, i)
                m.check(s, f'{i+1} commercial {key}', ref + '>=0')
                if key in ('incoming_royalty_rate', 'outgoing_royalty_rate'):
                    m.check(s, f'{i+1} {key} maximum', ref + '<=1')
            if i < final_stage:
                for key in ('units', 'cash_operating_cost'):
                    m.check(s, f'{i+1} no pre-outcome {key}', m.ref(s, 'commercial', key, i) + '=0')
            if str(i) in commitments and commitments[str(i)]['terms'] == 'draw_when_needed_outside_common_equity_at_fixed_price':
                price = m.ref(s, 'funding', 'commitments', str(i), 'issue_price')
                m.check(s, f'{i+1} fixed issue price', price + '>0')

        contributions = []
        for index, (failed, probability, years) in enumerate(branches):
            name = 'Dev ' + s + ' ' + ('S' if failed is None else str(index + 1))
            ws = m.sheet(name, (tr('Successo completo', 'Full success') if failed is None else
                                tr('Fallimento ', 'Failure ') + failed) + ' — ' + s, m.years[:years])
            _line(ws, 4, tr('Flussi dei soci originari; rami a probabilita zero non consumano funding.',
                            'Original-holder cash flows; zero-probability branches consume no funding.'),
                  max(m.end, years + 3), height=38)
            cash, previous_buffer, shares = opening_cash, opening_buffer, original_shares
            holder_pvs = []
            for i in range(years):
                c = i + 4
                stage_costs, stage_receipts = [], []
                for identity in order:
                    if stage[identity]['period'] != i + 1:
                        continue
                    stage_costs.append(m.ref(s, 'stage_schedule', identity, 'cost'))
                    stage_receipts.append('-' + m.ref(s, 'stage_schedule', identity, 'failure_close_cost')
                                          if identity == failed else
                                          m.ref(s, 'stage_schedule', identity, 'success_received') + '-' +
                                          m.ref(s, 'stage_schedule', identity, 'success_paid'))
                central = m.ref(s, 'central_cash_cost', i)
                cost = total([*stage_costs, central])
                receipts = total(stage_receipts)
                if failed is None:
                    commercial = lambda key: m.ref(s, 'commercial', key, i)
                    incoming = f"{commercial('units')}*{commercial('net_price_per_unit')}*{commercial('incoming_royalty_rate')}"
                    receipts += f'+({incoming})*(1-{commercial("outgoing_royalty_rate")})'
                    cost += '+' + commercial('cash_operating_cost')
                    if i == m.n - 1:
                        receipts += '-' + wind_down
                r = lambda offset, label, expression: m.calc(ws, 9 + offset, c, label, expression)
                start_cost = r(0, 'start costs', cost)
                end_receipts = r(1, 'end receipts', receipts)
                tax = r(2, 'cash tax', f'MAX(0,{end_receipts}-{start_cost})*{m.ref(s,"tax_rate",i)}')
                buffer = m.ref(s, 'minimum_cash', i)
                early = r(3, 'funding at start', f'MAX(0,{buffer}-({cash}-{start_cost}))')
                start_distribution = r(4, 'distribution at start', f'MAX(0,{cash}-{start_cost}-{buffer})')
                after_start = r(5, 'cash after start', f'{cash}+{early}-{start_cost}-{start_distribution}')
                available = r(6, 'cash before final sweep', f'{after_start}+{end_receipts}-{tax}')
                closing_minimum = '0' if i == years - 1 else buffer
                late = r(7, 'funding at end', f'MAX(0,{closing_minimum}-{available})')
                end_distribution = r(8, 'distribution at end', f'{available}+{late}-{closing_minimum}')
                capacity = m.ref(s, 'funding', 'capacities', i)
                m.check(s, f'{name} {i+1} committed funding',
                        f'OR({probability}=0,{early}+{late}<={capacity}+0.000000001)')
                commitment = commitments.get(str(i))
                outside = commitment is not None and commitment['terms'] == 'draw_when_needed_outside_common_equity_at_fixed_price'
                issue_price = m.ref(s, 'funding', 'commitments', str(i), 'issue_price') if outside else None
                new_start = r(9, 'new shares at start', f'{early}/{issue_price}' if outside else '0')
                after_start_shares = r(10, 'shares after start', f'{shares}+{new_start}')
                first = r(11, 'original-holder start cash',
                          f'{start_distribution}*{original_shares}/{after_start_shares}' +
                          ('' if outside else f'-{early}*{original_shares}/{shares}'))
                new_end = r(12, 'new shares at end', f'{late}/{issue_price}' if outside else '0')
                after_end_shares = r(13, 'closing shares', f'{after_start_shares}+{new_end}')
                last = r(14, 'original-holder end cash',
                         f'{end_distribution}*{original_shares}/{after_end_shares}' +
                         ('' if outside else f'-{late}*{original_shares}/{after_start_shares}'))
                m.support_calls[s].append(r(15, 'reachable original-holder start call',
                                             f'IF({probability}>0,MAX(0,-{first}),0)'))
                m.support_calls[s].append(r(16, 'reachable original-holder end call',
                                             f'IF({probability}>0,MAX(0,-{last}),0)'))
                start_time = '0' if i == 0 else m.period(i - 1)
                holder_pvs.append(r(17, 'PV original-holder cash',
                                    f'{first}/(1+{ku})^{start_time}+{last}/(1+{ku})^{m.period(i)}'))
                cash, previous_buffer, shares = closing_minimum, buffer, after_end_shares
            branch_value = m.calc(ws, 29, 4, 'conditional value per original share',
                                  f'IF({probability}=0,0,{total(holder_pvs)}/{original_shares})', PRICE)
            weighted = m.calc(summary, 9 + index, 5, 'weighted ' + ('success' if failed is None else failed),
                              f'{probability}*{branch_value}', PRICE)
            summary.cell(9 + index, 2).value = 'success' if failed is None else 'failure: ' + failed
            contributions.append(weighted)
            _finish(ws, 31, max(m.end, years + 3))
        m.results[s] = m.calc(summary, result_row, 4, 'risk-weighted value per original share',
                              total(contributions), PRICE)
        m.calc(summary, result_row + 1, 4, 'terminal value after expiry', '0')
        _finish(summary, result_row + 3, max(m.end, 6))
    m.finish()
    return True
