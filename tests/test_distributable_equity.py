"""Synthetic cash/capital ledgers: no market, filing, or database dependencies."""
from copy import deepcopy

import pytest


def inputs():
    return {
        "distribution_policy": "full_sweep_after_buffers",
        "reconciliation_basis": "Synthetic entity GAAP to statutory and consolidation bridge",
        "upstream_approval_basis": "Synthetic conditional forecast; approvals not certified",
        "terminal_basis": "Synthetic continuing equity including outstanding debt and buffers",
        "parent_opening_cash": 20.0, "parent_cash_minimum": [10.0, 10.0],
        "parent_opening_debt": 5.0, "terminal_debt": 5.0,
        "parent_gaap_net_income": [2.0, 2.0], "consolidation_adjustments": [0.0, 0.0],
        "parent_cash_flows": {
            "admin_fees_received": [3.0, 3.0], "tax_transfers_received": [2.0, 2.0],
            "investment_cash_income": [1.0, 1.0], "other_cash_receipts": [0.0, 0.0],
            "holding_cash_costs": [2.0, 2.0], "external_taxes": [2.0, 2.0],
            "interest_paid": [1.0, 1.0], "capex": [1.0, 1.0],
            "acquisitions": [0.0, 0.0], "debt_issued": [0.0, 0.0],
            "debt_repaid": [0.0, 0.0],
        },
        "subsidiaries": [{
            "id": "LEGAL-A", "opening_gaap_equity": 95.0,
            "opening_gaap_to_statutory_equity": 5.0, "opening_statutory_capital": 100.0,
            "gaap_net_income": [10.0, 12.0], "gaap_to_statutory_income": [-2.0, -2.0],
            "other_statutory_movements": [0.0, 0.0],
            "required_statutory_capital": [104.0, 110.0],
            "liquidity_before_transfers": [20.0, 20.0], "minimum_liquidity": [10.0, 10.0],
            "permitted_distribution": [5.0, 5.0], "proposed_distribution": [4.0, 4.0],
            "proposed_contribution": [0.0, 0.0],
        }],
        "ke": .1, "shares_m": 10.0, "discount_periods": [1.0, 2.0],
        "terminal_equity": 100.0,
    }, [12.0, 14.0], [2027, 2028]


def project(args=None):
    try:
        from bellomberg.valuation.distributable_equity import project_distributable_equity
    except ImportError:
        pytest.fail("Missing pure distributable-equity ledger")
    return project_distributable_equity(*(args or inputs()))


def unavailable(result, issue):
    assert result["status"] == "n.d."
    assert result["equity_value"] is None
    assert result["fair_value_per_share"] is None
    assert any(issue in reason for reason in result["issues"]), result["issues"]


def test_cash_ledger_and_equity_value_match_independent_hand_calculation():
    result = project()
    assert result["status"] == "CALCOLABILE"
    assert not result["issues"]
    assert [r["shareholder_net_distribution"] for r in result["rows"]] == [14.0, 4.0]
    assert [r["parent_closing_cash"] for r in result["rows"]] == [10.0, 10.0]
    assert [r["parent_closing_debt"] for r in result["rows"]] == [5.0, 5.0]
    assert [r["subsidiaries"][0]["closing_statutory_capital"] for r in result["rows"]] == [104.0, 110.0]
    assert result["rows"][0]["subsidiaries"][0]["statutory_net_income"] == 8.0
    # 14 / 1.1 + (4 + 100) / 1.21; no second addition of cash, NI or net debt.
    assert result["equity_value"] == pytest.approx(98.67768595041322)
    assert result["fair_value_per_share"] == pytest.approx(9.867768595041322)


def test_forecast_only_ledger_uses_identical_rows_without_inventing_terminal_values():
    from bellomberg.valuation.distributable_equity import project_distributable_equity
    cap, income, years = inputs()
    complete = project_distributable_equity(cap, income, years)
    for key in ('terminal_equity', 'terminal_debt', 'terminal_basis'):
        cap.pop(key)
    original = deepcopy(cap)
    unavailable(project_distributable_equity(cap, income, years), 'terminal')
    forecast = project_distributable_equity(cap, income, years, forecast_only=True)
    assert forecast['status'] == 'FORECAST_CALCOLABILE'
    assert forecast['rows'] == complete['rows']
    assert forecast['equity_value'] is None and forecast['fair_value_per_share'] is None
    assert 'terminal_equity' not in forecast and cap == original


def test_forecast_only_does_not_discard_terminal_claims_or_weaken_cash_constraints():
    from bellomberg.valuation.distributable_equity import project_distributable_equity
    cap, income, years = inputs()
    unavailable(project_distributable_equity(cap, income, years, forecast_only=True), 'input non consumati')
    for key in ('terminal_equity', 'terminal_debt', 'terminal_basis'):
        cap.pop(key)
    cap['subsidiaries'][0]['proposed_distribution'][0] = 30.
    unavailable(project_distributable_equity(cap, income, years, forecast_only=True), 'closing')


def test_consolidated_income_is_reconciled_but_never_added_to_parent_cash():
    args = inputs()
    args[0]["subsidiaries"][0]["gaap_net_income"] = [1000.0, 1200.0]
    args[1][:] = [1002.0, 1202.0]
    result = project(args)
    assert result["status"] == "CALCOLABILE"
    assert result["equity_value"] == pytest.approx(98.67768595041322)


def test_explicit_zero_upstream_distribution_is_valid():
    args = inputs()
    args[0]["subsidiaries"][0]["proposed_distribution"][0] = 0.0
    result = project(args)
    assert result["status"] == "CALCOLABILE"
    assert result["rows"][0]["shareholder_net_distribution"] == 10.0
    assert result["rows"][0]["subsidiaries"][0]["closing_statutory_capital"] == 108.0


def test_losses_and_contributions_produce_explicit_shareholder_funding_need():
    args = inputs()
    sub = args[0]["subsidiaries"][0]
    sub["gaap_net_income"][0] = -10.0
    sub["proposed_distribution"][0] = 0.0
    sub["proposed_contribution"][0] = 16.0
    args[1][0] = -8.0
    result = project(args)
    first = result["rows"][0]
    assert result["status"] == "CALCOLABILE"
    assert first["subsidiaries"][0]["closing_statutory_capital"] == 104.0
    assert first["shareholder_net_distribution"] == -6.0
    assert first["funding_required"] == 6.0
    assert first["shareholder_equity_contribution"] == 6.0
    assert first["shareholder_cash_distribution"] == 0.0
    assert first["parent_closing_cash"] == 10.0
    assert result["equity_value"] == pytest.approx(80.49586776859503)


@pytest.mark.parametrize("field,value,issue", [
    ("required_statutory_capital", 105.0, "required_statutory_capital"),
    ("liquidity_before_transfers", 13.0, "minimum_liquidity"),
    ("permitted_distribution", 3.0, "permitted_distribution"),
])
def test_capital_liquidity_and_permission_each_bind_separately(field, value, issue):
    args = inputs()
    args[0]["subsidiaries"][0][field][0] = value
    unavailable(project(args), issue)


def test_one_entity_surplus_cannot_hide_another_entity_capital_deficit():
    args = inputs()
    second = deepcopy(args[0]["subsidiaries"][0])
    second["id"] = "LEGAL-B"
    second["opening_gaap_equity"] = 995.0
    second["opening_statutory_capital"] = 1000.0
    args[0]["subsidiaries"].append(second)
    args[0]["subsidiaries"][0]["required_statutory_capital"][0] = 105.0
    args[1][:] = [22.0, 26.0]
    unavailable(project(args), "LEGAL-A")


@pytest.mark.parametrize("target", ["opening", "income"])
def test_gaap_to_statutory_and_consolidation_bridges_must_reconcile(target):
    args = inputs()
    if target == "opening":
        args[0]["subsidiaries"][0]["opening_gaap_to_statutory_equity"] = 6.0
        issue = "opening_statutory_capital"
    else:
        args[1][0] += .02
        issue = "consolidated_net_income"
    unavailable(project(args), issue)


def test_income_reconciliation_accepts_only_the_explicit_absolute_tolerance():
    args = inputs()
    args[1][0] += .005
    assert project(args)["status"] == "CALCOLABILE"
    args[1][0] += .02
    unavailable(project(args), "consolidated_net_income")


@pytest.mark.parametrize("issue,mutate", [
    ("debt_repaid", lambda c: c["parent_cash_flows"]["debt_repaid"].__setitem__(0, 6.0)),
    ("terminal_debt", lambda c: c.__setitem__("terminal_debt", 4.0)),
    ("debt_issued", lambda c: c["parent_cash_flows"]["debt_issued"].__setitem__(0, -1.0)),
])
def test_debt_rollforward_and_terminal_must_remain_consistent(issue, mutate):
    args = inputs()
    mutate(args[0])
    unavailable(project(args), issue)


def test_debt_borrowing_and_repayment_change_cash_once():
    args = inputs()
    args[0]["parent_cash_flows"]["debt_issued"] = [3.0, 0.0]
    args[0]["parent_cash_flows"]["debt_repaid"] = [0.0, 2.0]
    args[0]["terminal_debt"] = 6.0
    result = project(args)
    assert result["status"] == "CALCOLABILE"
    assert [r["shareholder_net_distribution"] for r in result["rows"]] == [17.0, 2.0]
    assert [r["parent_closing_debt"] for r in result["rows"]] == [8.0, 6.0]


@pytest.mark.parametrize("key", [
    "parent_opening_cash", "parent_cash_minimum", "parent_gaap_net_income",
    "consolidation_adjustments", "parent_opening_debt", "terminal_debt", "terminal_equity",
    "ke", "shares_m", "discount_periods", "reconciliation_basis", "upstream_approval_basis",
    "terminal_basis", "distribution_policy", "parent_cash_flows", "subsidiaries",
])
def test_missing_material_capital_input_never_uses_a_default(key):
    args = inputs()
    del args[0][key]
    unavailable(project(args), key)


@pytest.mark.parametrize("key", [
    "id", "opening_statutory_capital", "opening_gaap_equity", "opening_gaap_to_statutory_equity",
    "gaap_net_income", "gaap_to_statutory_income", "other_statutory_movements",
    "required_statutory_capital", "liquidity_before_transfers", "minimum_liquidity",
    "permitted_distribution", "proposed_distribution", "proposed_contribution",
])
def test_missing_subsidiary_input_never_uses_a_default(key):
    args = inputs()
    del args[0]["subsidiaries"][0][key]
    unavailable(project(args), key)


@pytest.mark.parametrize("key", list(inputs()[0]["parent_cash_flows"]))
def test_missing_parent_cash_flow_is_not_treated_as_zero(key):
    args = inputs()
    del args[0]["parent_cash_flows"][key]
    unavailable(project(args), key)


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), "5", [], [1.0]])
def test_invalid_or_incomplete_paths_fail_closed(bad):
    args = inputs()
    args[0]["parent_gaap_net_income"] = bad if isinstance(bad, list) else [bad, 2.0]
    unavailable(project(args), "parent_gaap_net_income")


@pytest.mark.parametrize("field,value", [
    ("ke", 0.0), ("shares_m", 0.0), ("terminal_equity", -1.0),
    ("terminal_equity", float("nan")), ("terminal_debt", float("inf")),
    ("parent_opening_cash", True), ("parent_opening_debt", -1.0),
    ("discount_periods", [0.0, 2.0]), ("discount_periods", [2.0, 1.0]),
    ("terminal_basis", " "), ("upstream_approval_basis", False),
    ("distribution_policy", "assumed_sweep"), ("subsidiaries", []),
])
def test_invalid_valuation_and_contract_inputs_fail_closed(field, value):
    args = inputs()
    args[0][field] = value
    unavailable(project(args), field)


@pytest.mark.parametrize("key", ["required_statutory_capital", "minimum_liquidity",
                                  "permitted_distribution", "proposed_distribution", "proposed_contribution"])
def test_capital_and_transfer_constraints_cannot_be_negative(key):
    args = inputs()
    args[0]["subsidiaries"][0][key][0] = -1.0
    unavailable(project(args), key)


@pytest.mark.parametrize("years", [[], [True, 2028], [2028, 2027], [2027, 2027], [2027.0, 2028]])
def test_forecast_years_must_identify_a_nonempty_increasing_horizon(years):
    args = inputs()
    unavailable(project((args[0], args[1], years)), "years")


def test_no_capital_contract_means_unavailable():
    unavailable(project((None, [12.0, 14.0], [2027, 2028])), "capital")


def test_duplicate_entity_id_cannot_double_count_dividends():
    args = inputs()
    args[0]["subsidiaries"].append(deepcopy(args[0]["subsidiaries"][0]))
    unavailable(project(args), "id")


@pytest.mark.parametrize("scope,field", [
    ("capital", "net_debt"), ("parent_cash_flows", "unconsumed_receipt"),
    ("subsidiary", "unconsumed_dividend"),
])
def test_unconsumed_capital_inputs_are_rejected_even_if_they_look_documentable(scope, field):
    args = inputs()
    owner = args[0] if scope == "capital" else (
        args[0]["parent_cash_flows"] if scope == "parent_cash_flows" else args[0]["subsidiaries"][0])
    owner[field] = [100.0, 100.0]
    unavailable(project(args), field)


@pytest.mark.parametrize("overflow", ["capital", "discount"])
def test_finite_inputs_whose_arithmetic_overflows_produce_no_value(overflow):
    args = inputs()
    if overflow == "capital":
        args[0]["subsidiaries"][0]["other_statutory_movements"] = [1e308, 1e308]
    else:
        args[0]["ke"] = 1e308
    unavailable(project(args), "finit")


@pytest.mark.parametrize("valid", [True, False])
def test_inputs_are_untouched_on_success_and_failure(valid):
    args = inputs()
    if not valid:
        args[0]["subsidiaries"][0]["permitted_distribution"][0] = 0.0
    original = deepcopy(args)
    project(args)
    assert args == original
