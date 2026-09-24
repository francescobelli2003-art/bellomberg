"""Deterministic stage context; never creates facts or changes proposed assumptions."""
from .capital_inputs import constraint_paths


class BankTerminalMismatch(ValueError):
    """A calculated terminal amount differs; no input has been corrected."""
    def __init__(self, scope, targets):
        from copy import deepcopy
        self.scope, self.targets = scope, deepcopy(targets)
        super().__init__('bank forecast arithmetic: declared terminal equity/debt differ from calculated balances')


def constraint_arithmetic(plan):
    result = {}
    for scenario, proposed in plan.get('scenarios', {}).items():
        if 'capital_constraints' not in proposed:
            continue
        try:
            model = plan['model']
            n = len(model['calendar']['value']['periods'])
            currency = model['perimeter']['value']['currency']
            ids = [row['id'] for row in model['legal_structure']['value']['subsidiaries']]
            constraints = proposed['capital_constraints']['value']
            if not n or not ids or len(set(ids)) != len(ids) or not isinstance(constraints, dict) or set(constraints) != set(ids):
                raise ValueError('periods/entities do not match the completed opening')
            entities = {}
            for identity in ids:
                item = constraints[identity]
                paths = constraint_paths(item, n)
                entities[identity] = {'by_constraint': paths,
                    'required_statutory_capital': [max(path[i] for path in paths.values()) for i in range(n)],
                    'terminal_requirement': max(row['terminal_requirement'] for row in item['constraints'])}
            result[scenario] = {'entities': entities, 'unit': currency + ' million',
                'basis': 'Arithmetic from completed proposed constraints, not observed facts or PM approval. For each period max(exposure * ratio + buffer, absolute_floor), then max across constraints. Terminal requirement is the max of explicit proposed terminal amounts.'}
        except (ValueError, KeyError, IndexError, TypeError, ArithmeticError) as exc:
            raise ValueError('bank constraint arithmetic: ' + str(exc)) from exc
    return result


def forecast_arithmetic(plan):
    """Expose completed annual balances and terminal arithmetic, without choosing a continuing ledger."""
    from .bank_adapter import EARNINGS
    from .capital_inputs import assemble_capital, equal, validate_constraints, validate_terminal
    from .distributable_equity import CAPITAL_FIELDS, TERMINAL_FIELDS, SUB_FIELDS, CASH_SIGNS, project_distributable_equity
    from .dcf_quality import _finite
    from .dcf_bank import _fv_ptbv
    from datetime import date
    result = {}
    for scenario, proposed in plan.get('scenarios', {}).items():
        try:
            model = plan.get('model', {})
            if 'legal_structure' not in model:
                continue
            ids = [s['id'] for s in model['legal_structure']['value']['subsidiaries']]
            required = {'capital.' + key for key in CAPITAL_FIELDS - TERMINAL_FIELDS - {'subsidiaries', 'parent_cash_flows'}}
            required |= {'capital.parent_cash_flows.' + key for key in CASH_SIGNS}
            required |= {'capital.subsidiaries.' + str(i) + '.' + key for i in range(len(ids)) for key in SUB_FIELDS - {'id'}}
            required |= set(EARNINGS) | {'capital_constraints', 'liquidity_bridge', 'terminal_growth', 'terminal_roe', 'funding_capacity', 'ownership_policy'}
            if not required <= proposed.keys():
                continue
            values = {key: proposed[key]['value'] for key in required}
            cap = assemble_capital(values, ids)
            periods = model['calendar']['value']['periods']; n = len(periods)
            if not ids or len(set(ids)) != len(ids) or not n:
                raise ValueError('empty or duplicated legal entities/calendar')
            if any(not isinstance(values[key], list) or len(values[key]) != n
                   or any(not _finite(v) for v in values[key]) for key in EARNINGS):
                raise ValueError('income paths do not match the completed calendar')
            calendar = model['calendar']['value']
            convention = calendar['discount_convention']
            if convention == 'annual_end':
                times = [float(i+1) for i in range(n)]
            elif convention == 'ACT/365F':
                times = [(date.fromisoformat(p['end']) - date.fromisoformat(calendar['valuation_date'])).days / 365. for p in periods]
            else:
                raise ValueError('unsupported discount convention')
            if cap['discount_periods'] != times or values['ownership_policy'] != 'pro_rata_existing_shareholders':
                raise ValueError('calendar/ownership policy not reconciled')
            income = [sum(values[key][i] * sign for key, sign in EARNINGS.items()) for i in range(n)]
            if not equal(model['opening_common_equity']['value'], model['opening_parent_equity']['value']
                         + model['opening_consolidation_adjustments']['value'] + sum(s['opening_gaap_equity'] for s in cap['subsidiaries'])):
                raise ValueError('opening common equity is not reconciled to legal entities')
            if any(v != 0 for v in values['other_comprehensive_income']):
                raise ValueError('nonzero OCI requires a supported common-equity bridge')
            ledger = project_distributable_equity(cap, income, [int(p['end'][:4]) for p in periods], forecast_only=True)
            if ledger['status'] != 'FORECAST_CALCOLABILE':
                raise ValueError('; '.join(ledger['issues']))
            issues = []
            validate_constraints(cap, values['capital_constraints'], values['liquidity_bridge'], lambda *row: issues.append(row))
            if issues:
                raise ValueError('; '.join(str(row) for row in issues))
            funding = values['funding_capacity']
            if (not isinstance(funding, list) or len(funding) != n or any(not _finite(v) or v < 0 for v in funding)
                    or any(row['funding_required'] > funding[i] for i,row in enumerate(ledger['rows']))):
                raise ValueError('required funding exceeds the documented capacity')
            distributions = [row['shareholder_net_distribution'] for row in ledger['rows']]
            book = model['opening_common_equity']['value'] + sum(income) - sum(distributions)
            g, roe, ke = values['terminal_growth'], values['terminal_roe'], cap['ke']
            if not all(_finite(x) for x in (book,g,roe,ke)) or not book > 0 or not ke > max(g,0) or not roe > g or g <= -1:
                raise ValueError('infeasible closing book/Ke/ROE/g')
            tv = _fv_ptbv({'documented_inputs': True, 'bvps': book / cap['shares_m'],
                           'ke': ke, 'growth_lt': g, 'roe_terminal': roe}) * cap['shares_m']
            last = ledger['rows'][-1]
            closing_subs = [{'id': sub['id'], 'gaap_equity': sub['opening_gaap_equity'] + sum(sub['gaap_net_income'])
                + sum(sub['proposed_contribution']) - sum(sub['proposed_distribution']),
                'statutory_capital': end['closing_statutory_capital'], 'liquidity': end['closing_liquidity']}
                for sub,end in zip(cap['subsidiaries'],last['subsidiaries'])]
            continuing_closes = {'parent_cash': last['parent_closing_cash'] * (1+g),
                'parent_debt': last['parent_closing_debt'] * (1+g),
                'subsidiaries': [{'id': row['id'], 'statutory_capital': row['statutory_capital'] * (1+g),
                                 'liquidity': row['liquidity'] * (1+g)} for row in closing_subs]}
            if any(not _finite(x) for x in (tv,book*roe,book*(roe-g))) or any(
                    not _finite(row[key]) for row in closing_subs for key in ('gaap_equity','statutory_capital','liquidity')) or any(
                    not _finite(x) for x in (continuing_closes['parent_cash'], continuing_closes['parent_debt'])) or any(
                    not _finite(row[key]) for row in continuing_closes['subsidiaries'] for key in ('statutory_capital','liquidity')):
                raise ValueError('nonfinite closing/terminal arithmetic')
            terminal_checked = False
            terminal_keys = {'terminal_ledger'} | {'capital.' + key for key in TERMINAL_FIELDS}
            if terminal_keys <= proposed.keys():
                full_cap = dict(cap, **{key: proposed['capital.' + key]['value'] for key in TERMINAL_FIELDS})
                targets = {'capital.' + key: {'declared': full_cap[key], 'calculated': expected}
                           for key, expected in (('terminal_equity', tv), ('terminal_debt', last['parent_closing_debt']))
                           if not equal(full_cap[key], expected)}
                if targets:
                    raise BankTerminalMismatch(scenario, targets)
                continuing = validate_terminal(proposed['terminal_ledger']['value'], full_cap, ledger, book, values,
                    lambda *row: issues.append(row), terminal_income=book*roe, allow_retained_flows=True)
                if issues or continuing is None:
                    raise ValueError('; '.join(str(row) for row in issues) or 'continuing ledger unavailable')
                terminal_checked = True
            result[scenario] = {'unit': model['perimeter']['value']['currency'] + ' million',
                'shareholder_distributions': distributions, 'closing_common_equity': book,
                'closing_parent_cash': last['parent_closing_cash'], 'closing_parent_debt': last['parent_closing_debt'],
                'closing_subsidiaries': closing_subs, 'continuing_common_income': book * roe,
                'continuing_closing_balances': continuing_closes,
                'continuing_shareholder_distribution': book * (roe-g), 'terminal_equity_target': tv,
                'terminal_continuity_checked': terminal_checked,
                'basis': 'Calculated from completed proposals with the existing annual cash engine. Not facts, approvals or a validated terminal. Supply an economically supported continuing ledger reconciling these balances, growth and distribution; do not invent cash or regulatory adjustments to force agreement. Full final valuation checks remain required.'}
        except BankTerminalMismatch:
            raise
        except (ValueError, KeyError, IndexError, TypeError, ArithmeticError) as exc:
            raise ValueError('bank forecast arithmetic: ' + str(exc)) from exc
    return result
