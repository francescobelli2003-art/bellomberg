"""Independent pure-math review: exact cashflows and finite-difference greeks."""
import math

import pytest

from bellomberg.portfolio.options_strategy import european_option, simulate_strategy


def request(legs, **changes):
    return {"spot": 100, "rate": 0, "dividend_yield": 0, "elapsed_days": 0,
            "iv_shift": 0, "commission": 0, "currency": "USD", "legs": legs, **changes}


def leg(**changes):
    return {"type": "call", "side": "buy", "quantity": 1, "strike": 100,
            "days": 30, "iv": .2, "premium": .1, "multiplier": 1e-8, **changes}


def test_small_real_loss_is_not_a_zero_payoff_interval():
    result = simulate_strategy(request([leg()]))
    assert result["max_loss"] == pytest.approx(1e-9, abs=1e-18)
    assert result["breakevens"] == [100.1]
    assert result["breakeven_intervals"] == []


def test_economically_offsetting_fractional_multipliers_are_bounded():
    result = simulate_strategy(request([leg(quantity=3, multiplier=.1, premium=5),
                                        leg(side="sell", quantity=1, multiplier=.3, premium=5)]))
    assert result["unlimited_profit"] is False
    assert result["unlimited_loss"] is False
    assert result["max_loss"] == 0
    assert result["max_profit"] == 0
    assert result["breakevens"] == []
    assert result["breakeven_intervals"] == [{"from": 0, "to": None}]


def test_oversized_json_integer_has_validation_error_not_overflow():
    with pytest.raises(ValueError):
        simulate_strategy(request([leg()], spot=10**1000))


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("spot,strike,days,vol,rate,yield_", [
    (100, 95, 180, .3, .04, .02),
    (100, 120, 30, .5, -.02, .03),
    (100, 100, 730, .15, .01, .1),
])
def test_theta_and_rho_match_price_derivatives(kind, spot, strike, days, vol, rate, yield_):
    base = european_option(spot, strike, days, vol, rate, yield_, kind)
    step = .001
    long = european_option(spot, strike, days + step, vol, rate, yield_, kind)["price"]
    short = european_option(spot, strike, days - step, vol, rate, yield_, kind)["price"]
    assert base["theta"] == pytest.approx((short - long) / (2 * step), rel=1e-6, abs=1e-9)
    step = 1e-5
    up = european_option(spot, strike, days, vol, rate + step, yield_, kind)["price"]
    down = european_option(spot, strike, days, vol, rate - step, yield_, kind)["price"]
    assert base["rho"] == pytest.approx((up - down) / (2 * step) / 100, rel=1e-6, abs=1e-9)


def test_mixed_expiry_first_settlement_matches_each_leg_theory():
    near = leg(days=30, multiplier=100, premium=5, side="sell")
    far = leg(days=60, multiplier=100, premium=7)
    result = simulate_strategy(request([near, far], elapsed_days=30, scenario_spot=110))
    remaining = european_option(110, 100, 30, .2, 0, 0, "call")["price"]
    assert result["scenario"]["pnl"] == pytest.approx(-100 * 10 + 100 * remaining - 200)
    assert result["same_expiry"] is False
    assert result["max_profit"] is None and result["max_loss"] is None
    assert result["breakevens"] == []
    assert all(point["expiry"] is None for point in result["curve"])
