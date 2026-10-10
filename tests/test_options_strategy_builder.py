"""Option builder engine (09/10, Opus 5.5): share legs, first-expiry analysis for calendars,
probability of profit, per-leg view, short-rate route.

Oracles are written here and do NOT import the engine's pricing: published reference values
(Hull, Haug) and a separate Black-Scholes-Merton built on math.erfc.
"""
import math

import pytest

from bellomberg.portfolio.options_strategy import european_option, simulate_strategy


def N(x):
    return 0.5 * math.erfc(-x / math.sqrt(2))


def bsm(s, k, days, vol, r, q, kind):
    """Independent oracle (closed form, math.erfc)."""
    t = days / 365
    if t <= 0:
        return max(0.0, s - k) if kind == "call" else max(0.0, k - s)
    d1 = (math.log(s / k) + (r - q + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if kind == "call":
        return s * math.exp(-q * t) * N(d1) - k * math.exp(-r * t) * N(d2)
    return k * math.exp(-r * t) * N(-d2) - s * math.exp(-q * t) * N(-d1)


def request(legs, **changes):
    return {"spot": 100, "rate": 0, "dividend_yield": 0, "elapsed_days": 0, "iv_shift": 0,
            "commission": 0, "currency": "USD", "legs": legs, **changes}


def opt(kind="call", side="buy", strike=100, days=30, iv=.2, premium=1, qty=1, mult=100):
    return {"type": kind, "side": side, "quantity": qty, "strike": strike, "days": days,
            "iv": iv, "premium": premium, "multiplier": mult}


def shares(side="buy", qty=100, price=100):
    return {"type": "stock", "side": side, "quantity": qty, "premium": price}


# ── published reference values ──────────────────────────────────────────────

def test_published_reference_prices():
    # Hull, Options Futures and Other Derivatives, Example 15.6: S=42 K=40 r=10% σ=20% T=0.5 → c=4.76, p=0.81.
    # 182.5 days = 0.5 years under ACT/365.
    call = european_option(42, 40, 182.5, .2, .10, 0, "call")
    put = european_option(42, 40, 182.5, .2, .10, 0, "put")
    assert call["price"] == pytest.approx(4.759422392871528, abs=1e-12)
    assert put["price"] == pytest.approx(0.8085993729000958, abs=1e-12)
    # Haug, The Complete Guide to Option Pricing Formulas, Merton (1973): S=100 K=95 T=0.5 r=10% q=5% σ=20% → p=2.4648.
    assert european_option(100, 95, 182.5, .2, .10, .05, "put")["price"] == pytest.approx(2.4647876467558234, abs=1e-12)


def test_limits_t_to_zero_and_iv_to_zero():
    # T → 0: the price converges to intrinsic value.
    assert european_option(110, 100, 1e-6, .3, .05, .01, "call")["price"] == pytest.approx(10, abs=1e-6)
    assert european_option(90, 100, 1e-6, .3, .05, .01, "put")["price"] == pytest.approx(10, abs=1e-6)
    # IV → 0: discounted forward intrinsic max(S e^(−qT) − K e^(−rT), 0).
    t = 90 / 365
    fwd = 100 * math.exp(-.01 * t) - 98 * math.exp(-.05 * t)
    assert european_option(100, 98, 90, 1e-8, .05, .01, "call")["price"] == pytest.approx(fwd, abs=1e-9)
    assert european_option(100, 98, 90, 1e-8, .05, .01, "put")["price"] == pytest.approx(0, abs=1e-9)


@pytest.mark.parametrize("kind", ["call", "put"])
def test_engine_matches_independent_oracle_and_finite_difference_greeks(kind):
    args = (103, 100, 45, .27, .043, .012)
    out = european_option(*args, kind)
    assert out["price"] == pytest.approx(bsm(*args, kind), abs=1e-12)
    h = 1e-3
    up, down = bsm(103 + h, *args[1:], kind), bsm(103 - h, *args[1:], kind)
    assert out["delta"] == pytest.approx((up - down) / (2 * h), rel=1e-6)
    assert out["gamma"] == pytest.approx((up - 2 * bsm(*args, kind) + down) / h ** 2, rel=1e-4)
    vu, vd = bsm(103, 100, 45, .27 + h, .043, .012, kind), bsm(103, 100, 45, .27 - h, .043, .012, kind)
    assert out["vega"] == pytest.approx((vu - vd) / (2 * h) / 100, rel=1e-6)          # per 1 IV point
    tu, td = bsm(103, 100, 45 + h, .27, .043, .012, kind), bsm(103, 100, 45 - h, .27, .043, .012, kind)
    assert out["theta"] == pytest.approx((td - tu) / (2 * h), rel=1e-6)               # per calendar day


# ── share legs ───────────────────────────────────────────────────────────────

def test_covered_call_exact_payoff_breakeven_and_capped_profit():
    out = simulate_strategy(request([shares(price=100), opt(side="sell", strike=105, premium=2)]))
    # Hand calculation: outlay 100·100 − 2·100 = 9800; BE 98; max profit (105 − 98)·100; max loss at S=0: 9800.
    assert out["entry_cost"] == 9800 and out["entry_kind"] == "debit"
    assert out["breakevens"] == [98]
    assert out["max_profit"] == 700 and out["unlimited_profit"] is False
    assert out["max_loss"] == 9800 and out["unlimited_loss"] is False
    assert out["today"]["delta"] == pytest.approx(100 - 100 * european_option(100, 105, 30, .2, 0, 0, "call")["delta"])


def test_protective_put_and_collar_bounds():
    protective = simulate_strategy(request([shares(price=100), opt(kind="put", strike=95, premium=1.5)]))
    # Max loss (100 − 95 + 1.5)·100 = 650; profit unbounded; BE 101.5.
    assert protective["max_loss"] == 650 and protective["unlimited_profit"] is True
    assert protective["breakevens"] == [101.5]
    collar = simulate_strategy(request([shares(price=100), opt(kind="put", strike=95, premium=1.5),
                                        opt(side="sell", strike=110, premium=1.5)]))
    # Zero-cost collar: loss capped at 500, gain capped at 1000, BE at 100.
    assert collar["max_loss"] == 500 and collar["max_profit"] == 1000 and collar["breakevens"] == [100]


def test_short_shares_are_unbounded_loss_and_share_legs_pay_no_option_commission():
    out = simulate_strategy(request([shares(side="sell", qty=100, price=100), opt(kind="put", strike=90, premium=1)], commission=2))
    assert out["fees"] == 2                                  # only the option contract
    assert out["unlimited_loss"] is True and out["max_loss"] is None
    # short shares + long call is a bounded synthetic put: (110 − 100)·100 + 100 premium + 2 fee
    hedged = simulate_strategy(request([shares(side="sell", qty=100, price=100), opt(strike=110, premium=1)], commission=2))
    assert hedged["unlimited_loss"] is False and hedged["max_loss"] == 1102


@pytest.mark.parametrize("bad", [{"quantity": 1.5}, {"quantity": 0}, {"premium": None}, {"multiplier": 100}])
def test_invalid_share_legs_are_rejected(bad):
    with pytest.raises(ValueError):
        simulate_strategy(request([{**shares(), **bad}, opt()]))


def test_shares_alone_are_not_an_option_strategy():
    with pytest.raises(ValueError):
        simulate_strategy(request([shares()]))


# ── calendars: first-expiry analysis ─────────────────────────────────────────

def calendar(q=0.0, vol_model="constant"):
    # 10/10: these oracles keep each leg's own IV (the declared "constant" model); the forward
    # model has its own tests below.
    return request([opt(side="sell", days=30, iv=.25, premium=2.5), opt(days=60, iv=.23, premium=3.6)],
                   rate=.045, dividend_yield=q, vol_model=vol_model)


def first_expiry(price, q=0.0):
    return -100 * max(price - 100, 0) + 100 * bsm(price, 100, 30, .23, .045, q, "call") - 110


def test_calendar_first_expiry_breakevens_extremes_and_tails():
    out = simulate_strategy(calendar())
    assert out["expiry_basis"] == "model_first_expiry"
    row = next(r for r in out["curve"] if r["price"] == 100)
    assert row["expiry"] == pytest.approx(first_expiry(100), abs=1e-9)
    assert len(out["breakevens"]) == 2
    for root in out["breakevens"]:
        assert first_expiry(root) == pytest.approx(0, abs=1e-6)
    # Independent dense scan for the peak.
    peak = max(first_expiry(80 + i * 1e-3) for i in range(40001))
    assert out["max_profit"] == pytest.approx(peak, abs=1e-4)
    # q = 0: the right tail flattens at 100·100·(1 − e^(−rτ)) − 110; the worst point is S = 0 (−110).
    assert out["unlimited_loss"] is False and out["max_loss"] == pytest.approx(110, abs=1e-9)
    assert out["tail_limit"] == pytest.approx(10000 * (1 - math.exp(-.045 * 30 / 365)) - 110, abs=1e-9)


def test_calendar_with_dividend_yield_declares_unbounded_model_loss():
    # With q > 0 a European far call grows like S·e^(−qτ) < S: the short near call wins as S → ∞.
    out = simulate_strategy(calendar(q=.015))
    assert out["unlimited_loss"] is True and out["max_loss"] is None


# ── probability of profit, per-leg view, zero-cost entry ─────────────────────

def test_probability_of_profit_long_call_matches_lognormal_closed_form():
    out = simulate_strategy(request([opt(strike=100, premium=2, iv=.25)], rate=.04, dividend_yield=.01))
    t, s = 30 / 365, .25
    expected = N((math.log(100 / 102) + (.04 - .01 - s * s / 2) * t) / (s * math.sqrt(t)))
    assert out["breakevens"] == [102]
    assert out["probability_of_profit"]["value"] == pytest.approx(expected, abs=1e-12)
    assert out["probability_of_profit"]["sigma"] == .25


def test_probability_of_profit_iron_condor_is_mass_between_breakevens():
    legs = [opt("put", strike=85, premium=.5), opt("put", "sell", 90, premium=1.5),
            opt("call", "sell", 110, premium=1.5), opt("call", strike=115, premium=.5)]
    out = simulate_strategy(request(legs))
    t, s = 30 / 365, .2
    F = lambda x: N((math.log(x / 100) + s * s / 2 * t) / (s * math.sqrt(t)))
    assert out["breakevens"] == [88, 112]
    assert out["probability_of_profit"]["value"] == pytest.approx(F(112) - F(88), abs=1e-12)


def test_zero_cost_entry_is_even_and_legs_report_per_contract_greeks():
    out = simulate_strategy(request([opt(premium=5), opt(side="sell", strike=110, premium=5)]))
    assert out["entry_cost"] == 0 and out["entry_kind"] == "even"
    first, second = out["legs"]
    unit = bsm(100, 100, 30, .2, 0, 0, "call")
    assert first["value"] == pytest.approx(unit, abs=1e-12)
    assert first["pnl_today"] == pytest.approx(100 * (unit - 5), abs=1e-9)
    assert first["delta"] == pytest.approx(100 * european_option(100, 100, 30, .2, 0, 0, "call")["delta"])
    assert second["delta"] < 0                               # a short contract carries negative delta


def test_curve_grid_is_finer_and_keeps_strikes_and_spot():
    out = simulate_strategy(request([opt(strike=97.5)], spot=101.3))
    prices = [r["price"] for r in out["curve"]]
    assert len(prices) >= 241 and 97.5 in prices and 101.3 in prices


@pytest.mark.parametrize("args", [(100, 95, 30, .3, .04, .01), (0, 95, 30, .3, .04, .01), (80, 95, 0, .3, .04, .01),
                                  (120, 95, 0, .3, .04, .01), (100, 100, 720, 1.5, -.01, .03)])
@pytest.mark.parametrize("kind", ["call", "put"])
def test_fast_scan_pricer_has_parity_with_the_validated_pricer(args, kind):
    from bellomberg.portfolio.options_strategy import _unit_price
    assert _unit_price(*args, kind == "call") == european_option(*args, kind)["price"]


# ── review 10/10 (Opus 5.5): H1 heatmap ulp, M1 dividend artefact, M2 forward vol ──────────────

import random

from bellomberg.portfolio.options_strategy import _european_core, _heatmap_elapsed, _unit_price


def test_h1_reviewer_case_fractional_days_does_not_crash_and_last_row_is_the_expiry():
    # Reproduced on 00fbd1c: horizon * 5 / 5 > horizon by one ulp → sqrt(−1.5e−19) → ValueError → 422.
    days = 0.45336077546296294
    assert days * 5 / 5 > days, "the float trap this test is about"
    out = simulate_strategy(request([opt(strike=102.5, days=days, iv=.3, premium=1.2)], spot=98.67, rate=.04))
    assert out["heatmap"][-1]["elapsed_days"] == days
    # last row = exact intrinsic at the expiry, priced by an independent formula
    for cell in out["heatmap"][-1]["cells"]:
        assert cell["pnl"] == pytest.approx(100 * max(cell["price"] - 102.5, 0) - 120, abs=1e-9)


def test_h1_property_heatmap_rows_never_pass_the_first_expiry_on_1000_random_horizons():
    rng = random.Random(20261010)
    trapped = 0
    for _ in range(1000):
        horizon = rng.uniform(1e-4, 60)
        trapped += horizon * 5 / 5 > horizon
        rows = _heatmap_elapsed(horizon)
        assert len(rows) == 6 and rows[0] == 0 and rows[-1] == horizon
        assert all(a < b for a, b in zip(rows, rows[1:])) and all(horizon - e >= 0 for e in rows)
    assert trapped > 0, "the sample must contain horizons that hit the float trap"


@pytest.mark.parametrize("mixed", [False, True])
def test_h1_property_trapped_random_horizons_simulate_end_to_end(mixed):
    # Only horizons where `h * 5 / 5 > h` (the ones that raised on 00fbd1c), 25 per case: the full
    # engine is slow under the offline runner, the 1000-value property above covers the lattice.
    rng = random.Random(20261011 + mixed)
    done = 0
    while done < 25:
        days = rng.uniform(1e-4, 60)
        if not days * 5 / 5 > days:
            continue
        done += 1
        legs = [opt(side="sell" if mixed else "buy", strike=100, days=days, iv=rng.uniform(.05, 1.5), premium=2)]
        if mixed:
            legs.append(opt(days=days + rng.uniform(1e-3, 90), iv=legs[0]["iv"], premium=3))
        out = simulate_strategy(request(legs, spot=rng.uniform(50, 150), rate=.04,
                                        elapsed_days=days * rng.random(), scenario_spot=rng.uniform(50, 150)))
        assert out["heatmap"][-1]["elapsed_days"] == days
        assert all(math.isfinite(c["pnl"]) for row in out["heatmap"] for c in row["cells"])


def test_residual_days_clamp_is_ulp_only_beyond_it_is_an_error():
    at_expiry = _european_core(110.0, 100.0, -1e-12, .3, .04, 0.0, True)
    assert at_expiry["price"] == 10 and at_expiry["theta"] is None
    assert _unit_price(90.0, 100.0, -1e-12, .3, .04, 0.0, False) == 10
    with pytest.raises(ValueError, match="negativ"):
        _european_core(110.0, 100.0, -1e-6, .3, .04, 0.0, True)
    with pytest.raises(ValueError, match="negativ"):
        _unit_price(110.0, 100.0, -1e-6, .3, .04, 0.0, True)


def reviewer_calendar(vol_model, q=0.0):
    return request([opt(side="sell", days=30, iv=.30, premium=2.9), opt(days=60, iv=.24, premium=4.4)],
                   rate=.04, dividend_yield=q, vol_model=vol_model)


def test_m2_forward_vol_is_the_default_and_values_the_far_leg_with_the_surface_forward():
    fwd = math.sqrt((.24 ** 2 * 60 - .30 ** 2 * 30) / 30)
    default = simulate_strategy({k: v for k, v in reviewer_calendar("forward").items() if k != "vol_model"})
    assert default["vol_model"] == "forward"
    at100 = next(r for r in default["curve"] if r["price"] == 100)["expiry"]
    assert at100 == pytest.approx(100 * bsm(100, 100, 30, fwd, .04, 0, "call") - 150, abs=1e-9)
    assert at100 == pytest.approx(48.13, abs=.01)
    const = simulate_strategy(reviewer_calendar("constant"))
    assert next(r for r in const["curve"] if r["price"] == 100)["expiry"] == pytest.approx(
        100 * bsm(100, 100, 30, .24, .04, 0, "call") - 150, abs=1e-9)
    # vol costante .24 sulla gamba lunga > vol forward (~.159): il valore deve essere piu' alto
    assert next(r for r in const["curve"] if r["price"] == 100)["expiry"] > at100
    assert default["forward_vols"] == [{"index": 1, "near_iv": .30, "leg_iv": .24, "forward_iv": pytest.approx(fwd, abs=1e-15)}]
    # today is the same under both models (no variance spent yet)
    assert default["today"]["pnl"] == pytest.approx(const["today"]["pnl"], abs=1e-12)


def test_m2_forward_path_at_intermediate_time_spends_the_near_variance():
    out = simulate_strategy({**reviewer_calendar("forward"), "elapsed_days": 12, "scenario_spot": 104, "iv_shift": .02})
    residual = math.sqrt((.24 ** 2 * 60 - .30 ** 2 * 12) / 48)
    expected = -100 * bsm(104, 100, 18, .32, .04, 0, "call") + 100 * bsm(104, 100, 48, residual + .02, .04, 0, "call") - 150
    assert out["scenario"]["pnl"] == pytest.approx(expected, abs=1e-9)


def test_m2_non_positive_forward_variance_is_declared_not_replaced():
    inverted = request([opt(side="sell", days=30, iv=.60, premium=2.9), opt(days=60, iv=.30, premium=4.4)])
    with pytest.raises(ValueError, match="forward"):
        simulate_strategy(inverted)
    out = simulate_strategy({**inverted, "vol_model": "constant"})
    assert out["forward_vols"][0]["forward_iv"] is None and out["vol_model"] == "constant"
    with pytest.raises(ValueError):
        simulate_strategy({**inverted, "vol_model": "smile"})


def test_m1_dividend_calendar_loss_is_flagged_as_european_artefact_with_a_finite_reference():
    out = simulate_strategy(reviewer_calendar("forward", q=.015))
    assert out["unlimited_loss"] is True and out["unlimited_loss_reason"] == "european_dividend"
    fwd = math.sqrt((.24 ** 2 * 60 - .30 ** 2 * 30) / 30)
    ref = -100 * (400 - 100) + 100 * bsm(400, 100, 30, fwd, .04, .015, "call") - 150
    assert out["tail_reference"] == {"price": 400, "pnl": pytest.approx(ref, abs=1e-9)}
    # two short near calls against one long far call: the loss is unbounded for real
    ratio = simulate_strategy(request([opt(side="sell", days=30, iv=.3, premium=2.9, qty=2), opt(days=60, iv=.24, premium=4.4)],
                                      rate=.04, dividend_yield=.015))
    assert ratio["unlimited_loss"] is True and ratio["unlimited_loss_reason"] == "structural"
    naked = simulate_strategy(request([opt(side="sell", premium=2.9)]))
    assert naked["unlimited_loss_reason"] == "structural" and naked["tail_reference"] is None


def test_m2_forward_anchor_is_the_first_expiry_leg_with_the_nearest_strike():
    # Diagonal: two first-expiry legs (K 100 at 30%, K 120 at 40%); the far K 100 leg spends the 30% variance.
    legs = [opt(side="sell", strike=100, days=30, iv=.30, premium=2.9), opt(side="sell", strike=120, days=30, iv=.40, premium=.4),
            opt(strike=100, days=60, iv=.26, premium=4.4)]
    out = simulate_strategy(request(legs, rate=.04))
    assert out["forward_vols"] == [{"index": 2, "near_iv": .30, "leg_iv": .26,
                                    "forward_iv": pytest.approx(math.sqrt((.26 ** 2 * 60 - .30 ** 2 * 30) / 30), abs=1e-15)}]


def test_leg_greeks_are_per_contract_and_pnl_today_is_per_position():
    # Review L3/R3: three contracts → the leg greeks stay per ONE contract, the P/L is the position's.
    out = simulate_strategy(request([opt(premium=5, qty=3)]))
    leg = out["legs"][0]
    unit = european_option(100, 100, 30, .2, 0, 0, "call")
    assert leg["delta"] == pytest.approx(100 * unit["delta"]) and leg["vega"] == pytest.approx(100 * unit["vega"])
    assert leg["pnl_today"] == pytest.approx(3 * 100 * (unit["price"] - 5), abs=1e-9)
    assert out["today"]["delta"] == pytest.approx(3 * leg["delta"])
