from copy import deepcopy

import pytest

from bellomberg.valuation import dcf_engine
from bellomberg.valuation.dcf_buyside_v3 import _dcf_value
from tests.test_sector_operating_drivers import bundle_for, operating_records


def _bundle(period_count):
    records = deepcopy(operating_records())
    periods = [
        {"start": f"{year}-01-01", "end": f"{year}-12-31"}
        for year in range(2026, 2026 + period_count)
    ]
    span = "|".join(f"{p['start']}/{p['end']}" for p in periods)
    for row in records:
        row["period"] = "2025-12-31" if row["driver"] in ("quotation", "historical_revenue", "opening_nwc", "shares") else span
        value = row["value"]
        if row["driver"] == "calendar":
            row["value"]["periods"] = periods
        elif isinstance(value, list):
            row["value"] = value + [value[-1]] * (period_count - len(value))
        elif isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, list):
                    value[key] = child + [child[-1]] * (period_count - len(child))
    return bundle_for(records)


def _result(tmp_path, years):
    return dcf_engine.generate_valuation(
        "SYNTH-EXT", prepared_bundle=_bundle(years), output_dir=str(tmp_path)
    )


def test_documented_cashflow_rows_reconcile(tmp_path):
    result = _result(tmp_path, 4)
    scenario = result["calculation_details"]["scenarios"]["base"]
    rows = scenario["rows"]
    for i in range(4):
        assert rows["cost_of_sales"][i] + rows["gross_profit"][i] == pytest.approx(rows["revenue"][i])
        assert rows["ebitda"][i] == pytest.approx(
            rows["gross_profit"][i] - rows["research_expense"][i]
            - rows["selling_general_expense"][i] + rows["capitalized_development"][i]
        )
        assert rows["ebit"][i] == pytest.approx(
            rows["ebitda"][i] - rows["tangible_depreciation"][i]
            - rows["research_amortization"][i]
        )
        assert rows["nopat"][i] == pytest.approx(rows["ebit"][i] - rows["cash_tax"][i])
        assert rows["ufcf"][i] == pytest.approx(rows["nopat"][i] - rows["net_reinvestment"][i])


def test_valuation_bridge_ties_pv_terminal_equity_and_share(tmp_path):
    result = _result(tmp_path, 10)
    scenario = result["calculation_details"]["scenarios"]["base"]
    bridge = scenario["valuation_bridge"]
    assert sum(bridge["discounted_cash_flows"]) == pytest.approx(bridge["pv_explicit_cash_flows"])
    assert bridge["pv_explicit_cash_flows"] + bridge["pv_terminal_value"] == pytest.approx(bridge["enterprise_value"])
    assert bridge["enterprise_value"] - bridge["net_debt"] + bridge["equity_adjustments"] == pytest.approx(bridge["equity_value"])
    assert bridge["equity_value"] / bridge["diluted_shares"] == pytest.approx(bridge["fair_value_per_share"])

    spec = {"documented_inputs": True, "discount_periods": bridge["discount_periods"],
            "net_debt": bridge["net_debt"], "shares_m": bridge["diluted_shares"],
            "mid_year": False, "equity_adjustments": [{"label": "bridge", "value_m": bridge["equity_adjustments"]}]}
    default = _dcf_value(spec, scenario["rows"]["ufcf"], scenario["wacc"], scenario["terminal_growth"],
                         ebit_terminal=scenario["terminal_bridge"]["normalized_ebit"],
                         ronic=scenario["terminal_ronic"], tax_term=scenario["rows"]["cash_tax"][-1] / scenario["rows"]["ebit"][-1],)
    detailed = _dcf_value(spec, scenario["rows"]["ufcf"], scenario["wacc"], scenario["terminal_growth"],
                          ebit_terminal=scenario["terminal_bridge"]["normalized_ebit"],
                          ronic=scenario["terminal_ronic"], tax_term=scenario["rows"]["cash_tax"][-1] / scenario["rows"]["ebit"][-1],
                          return_details=True)
    assert default == detailed["fair_value_per_share"]


@pytest.mark.parametrize("period_count", [4, 10, 15])
def test_documented_calendar_is_consumed_without_fixed_horizon(tmp_path, period_count):
    result = _result(tmp_path, period_count)
    scenario = result["calculation_details"]["scenarios"]["base"]
    assert len(result["analytical_quality"]["snapshot"]["forecast_years"]) == period_count
    assert len(scenario["valuation_bridge"]["discount_periods"]) == period_count
    assert all(len(values) == period_count for values in scenario["rows"].values())
