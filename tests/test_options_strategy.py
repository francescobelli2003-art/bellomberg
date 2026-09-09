import math
import pytest

from bellomberg.portfolio.options_strategy import european_option, simulate_strategy


def payload(**changes):
    base = {"spot": 100, "rate": .04, "dividend_yield": .01,
            "elapsed_days": 10, "iv_shift": .03, "commission": 0, "currency": "USD",
            "legs": [{"type": "call", "side": "buy", "quantity": 1, "strike": 100,
                      "days": 30, "iv": .25, "premium": 5, "multiplier": 100},
                     {"type": "call", "side": "sell", "quantity": 1, "strike": 110,
                      "days": 30, "iv": .25, "premium": 2, "multiplier": 100}]}
    base.update(changes)
    return base


def test_put_call_parity_and_analytic_greeks_match_price_derivatives():
    args = [100, 105, 180, .31, .045, .017]
    call = european_option(*args, "call")
    put = european_option(*args, "put")
    assert call["price"] - put["price"] == pytest.approx(100 * math.exp(-.017 * 180/365) - 105 * math.exp(-.045 * 180/365))
    h = .001
    up = european_option(100+h, *args[1:], "call")["price"]
    down = european_option(100-h, *args[1:], "call")["price"]
    assert call["delta"] == pytest.approx((up-down)/(2*h), rel=1e-6)
    assert call["gamma"] == pytest.approx((up-2*call["price"]+down)/(h*h), rel=1e-4)
    up_vol = european_option(*args[:3], args[3]+h, *args[4:], "call")["price"]
    down_vol = european_option(*args[:3], args[3]-h, *args[4:], "call")["price"]
    assert call["vega"] == pytest.approx((up_vol-down_vol)/(2*h)/100, rel=1e-5)


def test_vertical_spread_expiry_identity_breakeven_and_multiplier():
    result = simulate_strategy(payload(elapsed_days=30))
    assert result["entry_cost"] == 300
    assert result["max_profit"] == 700
    assert result["max_loss"] == 300
    assert result["breakevens"] == [103]
    for row in result["curve"]:
        intrinsic = 100 * (max(row["price"]-100, 0)-max(row["price"]-110, 0)) - 300
        assert row["expiry"] == pytest.approx(intrinsic)
        assert row["scenario"] == pytest.approx(intrinsic)
    doubled = payload()
    for leg in doubled["legs"]:
        leg["multiplier"] = 200
    out = simulate_strategy(doubled)
    assert out["entry_cost"] == 600
    assert out["max_profit"] == 1400


def test_expiry_at_strike_does_not_invent_greeks():
    at = european_option(100, 100, 0, .2, 0, 0, "put")
    assert at["price"] == 0
    assert at["delta"] is None and at["gamma"] is None and at["theta"] is None
    assert european_option(0, 100, 0, .2, 0, 0, "put")["price"] == 100


def test_zero_cost_identical_opposite_legs_have_zero_curves():
    data = payload()
    data["legs"][1] = {**data["legs"][0], "side": "sell"}
    out = simulate_strategy(data)
    assert all(row["expiry"] == row["today"] == row["scenario"] == 0 for row in out["curve"])
    assert out["today"]["vega"] == 0
    assert out["breakevens"] == []
    assert out["breakeven_intervals"] == [{"from": 0, "to": None}]


def test_calendar_has_no_fictitious_final_payoff_and_rejects_horizon_beyond_first_expiry():
    data = payload()
    data["legs"][1]["days"] = 60
    out = simulate_strategy(data)
    assert out["same_expiry"] is False
    assert all(row["expiry"] is None for row in out["curve"])
    assert out["max_profit"] is None and out["breakevens"] == []
    data["elapsed_days"] = 31
    with pytest.raises(ValueError, match="prima scadenza"):
        simulate_strategy(data)


@pytest.mark.parametrize("field,value", [("spot", None), ("rate", ""), ("commission", False), ("iv_shift", float("nan"))])
def test_invalid_inputs_never_become_zero(field, value):
    with pytest.raises(ValueError):
        simulate_strategy(payload(**{field: value}))


@pytest.mark.parametrize("field,value", [("multiplier", None), ("quantity", 1.5), ("iv", 0), ("premium", -1)])
def test_invalid_legs_rejected(field, value):
    data = payload()
    data["legs"][0][field] = value
    with pytest.raises(ValueError):
        simulate_strategy(data)


def test_short_call_unbounded_loss_and_commissions_are_real_cashflows():
    # Forty synthetic units collect 100 cash; two contract fees of 3.5 cost 7.
    data = payload(spot=64, commission=3.5)
    data["legs"] = [{**data["legs"][0], "side": "sell", "strike": 64,
                     "premium": 2.5, "quantity": 2, "multiplier": 20}]
    out = simulate_strategy(data)
    assert out["fees"] == 7
    assert out["entry_cost"] == -93 and out["entry_kind"] == "credit"
    assert out["unlimited_loss"] and out["max_loss"] is None
    assert out["max_profit"] == 93
    # Only the net credit offsets intrinsic loss: strike plus 93 / 40.
    assert out["breakevens"] == pytest.approx([66.325])

def test_price_scenario_changes_mark_and_greeks_without_changing_entry_spot():
    data = payload(scenario_spot=110)
    out = simulate_strategy(data)
    assert out["today"]["price"] == 100 and out["scenario"]["price"] == 110
    row = next(r for r in out["curve"] if r["price"] == 110)
    assert out["scenario"]["pnl"] == pytest.approx(row["scenario"])
    assert out["scenario"]["delta"] != out["today"]["delta"]


def test_decimal_contract_exposures_cancel_without_fictitious_unlimited_profit():
    data = payload()
    first = {**data["legs"][0], "quantity": 3, "multiplier": .1}
    data["legs"] = [first, {**first, "side": "sell", "quantity": 1, "multiplier": .3}]
    out = simulate_strategy(data)
    assert out["entry_cost"] == 0
    assert all(row["expiry"] == row["today"] == row["scenario"] == 0 for row in out["curve"])
    assert all(out["today"][key] == 0 for key in ("delta", "gamma", "vega", "theta", "rho"))


@pytest.mark.parametrize("field", ["rate", "commission"])
def test_oversized_json_integer_raises_validation_error(field):
    with pytest.raises(ValueError):
        simulate_strategy(payload(**{field: 10 ** 1000}))


def test_small_real_net_exposure_and_cashflow_are_not_rounded_to_zero():
    data = payload()
    first = {**data["legs"][0], "quantity": 3, "multiplier": .1}
    data["legs"] = [first, {**first, "side": "sell", "quantity": 1, "multiplier": .2999999999999999}]
    out = simulate_strategy(data)
    assert out["entry_cost"] == pytest.approx(5e-16, rel=1e-14, abs=0)
    assert out["unlimited_profit"] is True
    assert out["unlimited_loss"] is False
    assert out["breakevens"] == [105]
    assert out["breakeven_intervals"] == []
