"""Pure statutory-capital and parent-cash projection, in the case currency, millions.

CALCOLABILE certifies arithmetic completeness only, never source truth, regulatory
approval, funding availability or economic adequacy. Required capital and permitted
distributions are supplied per legal entity on reconciled statutory bases. No RBC
ratio, minimum capital, GAAP profit or aggregate surplus becomes an implicit dividend.
"""
from math import fsum, isclose, isfinite


CASH_SIGNS = {
    "admin_fees_received": 1, "tax_transfers_received": 1,
    "investment_cash_income": 1, "other_cash_receipts": 1,
    "holding_cash_costs": -1, "external_taxes": -1, "interest_paid": -1,
    "capex": -1, "acquisitions": -1, "debt_issued": 1, "debt_repaid": -1,
}
SUB_PATHS = (
    "gaap_net_income", "gaap_to_statutory_income", "other_statutory_movements",
    "required_statutory_capital", "liquidity_before_transfers", "minimum_liquidity",
    "permitted_distribution", "proposed_distribution", "proposed_contribution",
)
NONNEGATIVE_SUB_PATHS = frozenset((
    "required_statutory_capital", "minimum_liquidity", "permitted_distribution",
    "proposed_distribution", "proposed_contribution",
))
CAPITAL_FIELDS = frozenset((
    "distribution_policy", "reconciliation_basis", "upstream_approval_basis", "terminal_basis",
    "parent_opening_cash", "parent_cash_minimum", "parent_opening_debt", "terminal_debt",
    "parent_gaap_net_income", "consolidation_adjustments", "parent_cash_flows", "subsidiaries",
    "ke", "shares_m", "discount_periods", "terminal_equity",
))
TERMINAL_FIELDS = frozenset(('terminal_basis', 'terminal_debt', 'terminal_equity'))
SUB_FIELDS = frozenset(SUB_PATHS) | {
    "id", "opening_statutory_capital", "opening_gaap_equity", "opening_gaap_to_statutory_equity",
}
NOTE = ("CALCOLABILE indica completezza matematica, non verifica di fonti, approvazioni, "
        "adeguatezza del capitale o disponibilita' di finanziamenti. Il full sweep "
        "include apporti equity richiesti se il flusso azionisti e' negativo. "
        "Vincoli annuali: la liquidita' infrannuale non e' certificata.")


def _finite(value):
    try:
        return type(value) in (int, float) and isfinite(value)
    except (OverflowError, ValueError):
        return False


def _total(*values):
    value = fsum(values)
    if not isfinite(value):
        raise ArithmeticError("risultato non finito")
    return value


def project_distributable_equity(capital: dict | None, consolidated_net_income: list,
                                years: list, *, forecast_only=False) -> dict:
    """Project an explicit full-sweep equity policy; never read or mutate external state.

    Cash flow keys have the directions in CASH_SIGNS (costs are subtracted). Signed
    reversals/refunds are allowed except debt issuance/repayment, which are nonnegative.
    Opening cash is consumed by the ledger, not added again to fair value. Parent NI
    is used only for the consolidation check. Subsidiary cash forecasts must already
    include their fees/tax remittances to the parent; the caller documents this bridge.
    Terminal equity is a supplied continuing equity value after the final distribution,
    with the same closing parent debt; no net-debt or initial-surplus bridge is added.
    Negative shareholder flows are explicit financing requirements, not zero floors.
    forecast_only validates the same annual ledger without terminal inputs and
    returns FORECAST_CALCOLABILE with no equity value. It cannot certify a valuation.
    """
    issues = []

    def unavailable():
        return {"status": "n.d.", "issues": list(issues), "rows": [],
                "equity_value": None, "fair_value_per_share": None, "note": NOTE}

    if not isinstance(capital, dict):
        issues.append("capital: contratto del capitale assente o non oggetto")
        return unavailable()

    def reject_unknown(owner, allowed, label):
        unknown = set(owner) - set(allowed)
        if unknown:
            issues.append(label + ": input non consumati: " + ", ".join(sorted(str(key) for key in unknown)))

    reject_unknown(capital, CAPITAL_FIELDS - TERMINAL_FIELDS if forecast_only else CAPITAL_FIELDS, "capital")
    if (not isinstance(years, list) or not years
            or any(type(year) is not int or not 1 <= year <= 9999 for year in years)
            or any(a >= b for a, b in zip(years, years[1:]))):
        issues.append("years: servono esercizi interi espliciti, crescenti e non vuoti")
        return unavailable()
    count = len(years)

    def number(value, label, nonnegative=False, positive=False):
        if not _finite(value):
            issues.append(label + ": numero finito esplicito assente/non valido")
            return None
        if (nonnegative and value < 0) or (positive and value <= 0):
            issues.append(label + (": deve essere positivo" if positive else ": negativo non ammesso"))
            return None
        return float(value)

    def path(owner, key, prefix="", nonnegative=False, positive=False):
        values = owner.get(key)
        label = prefix + key
        if not isinstance(values, list) or len(values) != count:
            issues.append(label + ": percorso esplicito incompleto/non lista")
            return None
        return [number(value, f"{label}[{years[i]}]", nonnegative, positive)
                for i, value in enumerate(values)]

    policy = capital.get("distribution_policy")
    if policy != "full_sweep_after_buffers":
        issues.append("distribution_policy: serve full_sweep_after_buffers esplicito")
    for key in (("reconciliation_basis", "upstream_approval_basis") if forecast_only
                else ("reconciliation_basis", "terminal_basis", "upstream_approval_basis")):
        value = capital.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(key + ": base documentale esplicita assente")

    ni = path({"consolidated_net_income": consolidated_net_income}, "consolidated_net_income")
    normal = {}
    for key in (("parent_opening_cash", "parent_opening_debt") if forecast_only
                else ("parent_opening_cash", "parent_opening_debt", "terminal_debt", "terminal_equity")):
        normal[key] = number(capital.get(key), key, nonnegative=True)
    for key in ("ke", "shares_m"):
        normal[key] = number(capital.get(key), key, positive=True)
    for key in ("parent_cash_minimum", "parent_gaap_net_income", "consolidation_adjustments"):
        normal[key] = path(capital, key, nonnegative=key == "parent_cash_minimum")
    periods = path(capital, "discount_periods", positive=True)
    if periods and all(value is not None for value in periods):
        if any(a >= b for a, b in zip(periods, periods[1:])):
            issues.append("discount_periods: periodi non strettamente crescenti")

    cash = capital.get("parent_cash_flows")
    cash_paths = {}
    if not isinstance(cash, dict):
        issues.append("parent_cash_flows: oggetto esplicito assente/non valido")
    else:
        reject_unknown(cash, CASH_SIGNS, "parent_cash_flows")
        for key in CASH_SIGNS:
            cash_paths[key] = path(cash, key, "parent_cash_flows.",
                                   nonnegative=key in ("debt_issued", "debt_repaid"))

    subsidiaries = capital.get("subsidiaries")
    subs, seen = [], set()
    if not isinstance(subsidiaries, list) or not subsidiaries:
        issues.append("subsidiaries: serve una lista non vuota di entita' legali")
    else:
        for index, source in enumerate(subsidiaries):
            prefix = f"subsidiaries[{index}]."
            if not isinstance(source, dict):
                issues.append(prefix + "entita' non oggetto")
                continue
            reject_unknown(source, SUB_FIELDS, prefix)
            identity = source.get("id")
            if not isinstance(identity, str) or not identity.strip():
                issues.append(prefix + "id assente/non valido")
            elif identity.strip() in seen:
                issues.append(prefix + "id duplicato: " + identity)
            else:
                seen.add(identity.strip())
                prefix = identity.strip() + "."
            sub = {"id": identity}
            for key in ("opening_statutory_capital", "opening_gaap_equity",
                        "opening_gaap_to_statutory_equity"):
                sub[key] = number(source.get(key), prefix + key)
            for key in SUB_PATHS:
                sub[key] = path(source, key, prefix, nonnegative=key in NONNEGATIVE_SUB_PATHS)
            subs.append(sub)
    if issues:
        return unavailable()

    rows = []
    try:
        for sub in subs:
            delta = _total(sub["opening_gaap_equity"], sub["opening_gaap_to_statutory_equity"],
                           -sub["opening_statutory_capital"])
            if abs(delta) > .01:
                issues.append(sub["id"] + ".opening_statutory_capital: ponte iniziale non riconciliato")
        if issues:
            return unavailable()
        cap_open = {sub["id"]: sub["opening_statutory_capital"] for sub in subs}
        parent_cash, debt = normal["parent_opening_cash"], normal["parent_opening_debt"]
        for index, year in enumerate(years):
            entity_rows = []
            for sub in subs:
                identity = sub["id"]
                distribution, contribution = (sub["proposed_distribution"][index],
                                                sub["proposed_contribution"][index])
                statutory_income = _total(sub["gaap_net_income"][index], sub["gaap_to_statutory_income"][index])
                closing_capital = _total(cap_open[identity], statutory_income,
                                         sub["other_statutory_movements"][index], contribution, -distribution)
                closing_liquidity = _total(sub["liquidity_before_transfers"][index], contribution, -distribution)
                prefix = f"{identity}[{year}]: "
                if closing_capital < sub["required_statutory_capital"][index]:
                    issues.append(prefix + "closing capital sotto required_statutory_capital")
                if closing_liquidity < sub["minimum_liquidity"][index]:
                    issues.append(prefix + "closing liquidity sotto minimum_liquidity")
                if distribution > sub["permitted_distribution"][index]:
                    issues.append(prefix + "distribuzione oltre permitted_distribution")
                entity_rows.append({
                    "id": identity, "opening_statutory_capital": cap_open[identity],
                    **{key: sub[key][index] for key in SUB_PATHS},
                    "statutory_net_income": statutory_income,
                    "closing_statutory_capital": closing_capital, "closing_liquidity": closing_liquidity,
                })
                cap_open[identity] = closing_capital
            reconciliation_delta = _total(*(sub["gaap_net_income"][index] for sub in subs),
                normal["parent_gaap_net_income"][index], normal["consolidation_adjustments"][index], -ni[index])
            if abs(reconciliation_delta) > .01:
                issues.append(f"consolidated_net_income[{year}]: somma entita'/parent/eliminazioni non riconciliata")
            upstream = _total(*(sub["proposed_distribution"][index] for sub in subs))
            contributions = _total(*(sub["proposed_contribution"][index] for sub in subs))
            flows = {key: values[index] for key, values in cash_paths.items()}
            cash_net = _total(*(flows[key] * sign for key, sign in CASH_SIGNS.items()))
            cash_before = _total(parent_cash, upstream, -contributions, cash_net)
            minimum = normal["parent_cash_minimum"][index]
            shareholder_flow = _total(cash_before, -minimum)
            debt_close = _total(debt, flows["debt_issued"], -flows["debt_repaid"])
            if debt_close < 0:
                issues.append(f"parent_cash_flows.debt_repaid[{year}]: rimborso oltre il debito disponibile")
            discount = 1 / (_total(1.0, normal["ke"]) ** periods[index])
            pv = shareholder_flow * discount
            if not _finite(discount) or discount <= 0 or not _finite(pv):
                raise ArithmeticError("sconto/flusso attualizzato non finito o sconto nullo")
            rows.append({
                "year": year, "subsidiaries": entity_rows,
                "consolidated_net_income": ni[index], "reconciliation_delta": reconciliation_delta,
                "parent_gaap_net_income": normal["parent_gaap_net_income"][index],
                "consolidation_adjustments": normal["consolidation_adjustments"][index],
                "upstream_distributions": upstream, "subsidiary_contributions": contributions,
                "parent_opening_cash": parent_cash, "parent_cash_flows": flows,
                "parent_cash_flow_net": cash_net, "parent_cash_before_distribution": cash_before,
                "parent_cash_minimum": minimum, "shareholder_net_distribution": shareholder_flow,
                "shareholder_cash_distribution": max(shareholder_flow, 0.0),
                "shareholder_equity_contribution": max(-shareholder_flow, 0.0),
                "funding_required": max(-shareholder_flow, 0.0), "parent_closing_cash": minimum,
                "parent_opening_debt": debt, "parent_closing_debt": debt_close,
                "discount_period": periods[index], "discount_factor": discount,
                "discounted_shareholder_flow": pv,
            })
            parent_cash, debt = minimum, debt_close
        if forecast_only:
            if issues:
                return unavailable()
            return {"status": "FORECAST_CALCOLABILE", "issues": [], "rows": rows,
                    "equity_value": None, "fair_value_per_share": None,
                    "note": NOTE + " Terminale non fornito: nessuna valutazione calcolata."}
        if not isclose(normal["terminal_debt"], debt, rel_tol=0.0, abs_tol=1e-9):
            issues.append("terminal_debt: non coincide con il debito finale del parent")
        if issues:
            return unavailable()
        terminal_pv = normal["terminal_equity"] * rows[-1]["discount_factor"]
        equity_value = _total(*(row["discounted_shareholder_flow"] for row in rows), terminal_pv)
        fair_value = equity_value / normal["shares_m"]
        if not _finite(fair_value):
            raise ArithmeticError("fair value non finito")
    except (ArithmeticError, ValueError) as exc:
        issues.append("calcolo non finito/non rappresentabile: " + str(exc))
        return unavailable()
    return {"status": "CALCOLABILE", "issues": [], "rows": rows,
            "equity_value": equity_value, "fair_value_per_share": fair_value,
            "terminal_equity": normal["terminal_equity"], "terminal_debt": debt,
            "discounted_terminal_equity": terminal_pv, "distribution_policy": policy,
            "terminal_basis": capital["terminal_basis"],
            "reconciliation_basis": capital["reconciliation_basis"],
            "upstream_approval_basis": capital["upstream_approval_basis"], "note": NOTE}
