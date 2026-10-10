"""Pure, offline options laboratory. No quote retrieval, portfolio or order API.

European Black–Scholes–Merton with continuous dividend yield; ACT/365.
Cash flows are in the explicitly supplied currency. Price is per underlying
unit; position values/greeks apply quantity, direction and contract multiplier.
"""
from bellomberg.core.presentation import message as _ui_text
from fractions import Fraction
from math import erf, exp, fsum, isfinite, log, pi, sqrt


def _number(value, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(_ui_text(f'{name}: numero finito obbligatorio', f'{name}: finite number required'))
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(_ui_text(f'{name}: numero fuori intervallo', f'{name}: number out of range')) from exc
    if not isfinite(value):
        raise ValueError(_ui_text(f'{name}: numero finito obbligatorio', f'{name}: finite number required'))
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(_ui_text(f'{name}: fuori intervallo consentito [{low}, {high}]', f'{name}: outside the allowed range [{low}, {high}]'))
    return float(value)


# 10/10 (Opus 5.5, review H1): residual days computed as `days - elapsed` can land a few ulp below
# zero (e.g. `horizon * 5 / 5 > horizon` in binary floating point). Inside this tolerance the leg
# is AT expiry; beyond it the request is wrong and is refused, never clamped.
_DAYS_EPS = 1e-9


def _residual_days(d):
    if d < 0:
        if d < -_DAYS_EPS:
            raise ValueError(_ui_text(f'giorni residui negativi ({d:.3g}): orizzonte oltre la scadenza',
                                      f'negative residual days ({d:.3g}): horizon beyond expiry'))
        return 0.0
    return d


def _heatmap_elapsed(horizon):
    """Six rows from today to the first expiry; the last one is the expiry EXACTLY (review H1:
    `horizon * 5 / 5` can exceed `horizon` by one ulp and push residual days below zero)."""
    return [horizon * i / 5 for i in range(5)] + [horizon]


def _unit_price(s, k, d, vol, r, q, call):
    """Price only, for inputs ALREADY validated by `_validated` (dense first-expiry scans).

    Same formulas as `european_option`; kept separate so a 2000-point grid does not rebuild
    bilingual validation messages on every evaluation. Parity is asserted in the tests.
    """
    d = _residual_days(d)
    if d <= 0:
        return max(0.0, s - k) if call else max(0.0, k - s)
    t = d / 365
    dr, dq = exp(-r * t), exp(-q * t)
    if s <= 0:
        return 0.0 if call else k * dr
    root_t = sqrt(t)
    d1 = (log(s / k) + (r - q + vol * vol / 2) * t) / (vol * root_t)
    d2 = d1 - vol * root_t
    cdf = lambda x: (1 + erf(x / sqrt(2))) / 2
    price = s * dq * cdf(d1) - k * dr * cdf(d2) if call else k * dr * cdf(-d2) - s * dq * cdf(-d1)
    return max(0.0, price)


def european_option(spot, strike, days, iv, rate, dividend_yield, option_type):
    """Unit price; delta/dSpot, gamma/dSpot², vega/rho per 1pp, theta/day.

    At expiry the intrinsic value is exact. At the strike, expiry derivatives
    are undefined and returned as None; they must not become zero in the UI.
    """
    s = _number(spot, "spot", 0, 1e9)
    k = _number(strike, "strike", 1e-9, 1e9)
    d = _number(days, _ui_text('giorni', 'days'), 0, 36500)
    vol = _number(iv, "IV", 1e-8, 5)
    r = _number(rate, _ui_text('tasso', 'rate'), -1, 1)
    q = _number(dividend_yield, "dividend yield", -1, 1)
    if option_type not in ("call", "put"):
        raise ValueError(_ui_text('tipo opzione: call o put', 'option type: call or put'))
    return _european_core(s, k, d, vol, r, q, option_type == "call")


def _european_core(s, k, d, vol, r, q, call):
    """`european_option` after validation; the engine calls it with inputs `_validated` checked."""
    d = _residual_days(d)
    if d == 0:
        kink = s == k
        delta = None if kink else (float(s > k) if call else -float(s < k))
        return {"price": max(0, s - k) if call else max(0, k - s),
                "delta": delta, "gamma": None if kink else 0.0,
                "vega": 0.0, "theta": None, "rho": 0.0}
    t = d / 365
    dr, dq = exp(-r * t), exp(-q * t)
    if s == 0:
        return {"price": 0.0 if call else k * dr,
                "delta": 0.0 if call else -dq, "gamma": 0.0,
                "vega": 0.0, "theta": 0.0 if call else r * k * dr / 365,
                "rho": 0.0 if call else -k * t * dr / 100}
    root_t = sqrt(t)
    d1 = (log(s / k) + (r - q + vol * vol / 2) * t) / (vol * root_t)
    d2 = d1 - vol * root_t
    cdf = lambda x: (1 + erf(x / sqrt(2))) / 2
    density = exp(-d1 * d1 / 2) / sqrt(2 * pi)
    first_theta = -s * dq * density * vol / (2 * root_t)
    if call:
        price = s * dq * cdf(d1) - k * dr * cdf(d2)
        delta = dq * cdf(d1)
        theta = first_theta - r * k * dr * cdf(d2) + q * s * dq * cdf(d1)
        rho = k * t * dr * cdf(d2) / 100
    else:
        price = k * dr * cdf(-d2) - s * dq * cdf(-d1)
        delta = -dq * cdf(-d1)
        theta = first_theta + r * k * dr * cdf(-d2) - q * s * dq * cdf(-d1)
        rho = -k * t * dr * cdf(-d2) / 100
    return {"price": max(0.0, price), "delta": delta,
            "gamma": dq * density / (s * vol * root_t),
            "vega": s * dq * density * root_t / 100,
            "theta": theta / 365, "rho": rho}


def _validated(body):
    if not isinstance(body, dict):
        raise ValueError(_ui_text('simulazione: oggetto obbligatorio', 'simulation: object required'))
    cfg = {"spot": _number(body.get("spot"), "spot", 1e-8, 1e8),
           "scenario_spot": _number(body.get("scenario_spot", body.get("spot")), _ui_text('prezzo scenario', 'scenario price'), 0, 1e8),
           "rate": _number(body.get("rate"), _ui_text('tasso', 'rate'), -1, 1),
           "dividend_yield": _number(body.get("dividend_yield"), "dividend yield", -1, 1),
           "elapsed_days": _number(body.get("elapsed_days"), _ui_text('giorni trascorsi', 'elapsed days'), 0, 36500),
           "iv_shift": _number(body.get("iv_shift"), "shock IV", -4.99, 4.99),
           "commission": _number(body.get("commission"), _ui_text('costo per contratto', 'cost per contract'), 0, 1e6)}
    currency = body.get("currency")
    if not isinstance(currency, str) or not currency.isalpha() or len(currency) != 3 or not currency.isupper():
        raise ValueError(_ui_text('valuta: codice ISO di tre lettere obbligatorio', 'currency: three-letter ISO code required'))
    cfg["currency"] = currency
    # 10/10 (Opus 5.5, review M2): how a leg that outlives the first expiry is valued along the way.
    # "forward" = the term structure of today's surface (forward variance between the first expiry
    # and the leg's own); "constant" = each leg keeps its own IV. Echoed in the result.
    vol_model = body.get("vol_model", "forward")
    if vol_model not in ("forward", "constant"):
        raise ValueError(_ui_text('modello di volatilità: forward o constant', 'volatility model: forward or constant'))
    cfg["vol_model"] = vol_model
    rows = body.get("legs")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
        raise ValueError(_ui_text('servono da 1 a 12 gambe', '1 to 12 legs required'))
    legs = []
    for i, raw in enumerate(rows, 1):
        if not isinstance(raw, dict):
            raise ValueError(_ui_text(f'gamba {i}: oggetto obbligatorio', f'leg {i}: object required'))
        if raw.get("type") == "stock" and raw.get("side") in ("buy", "sell"):
            # 09/10 (Opus 5.5): underlying leg (covered call, collar, protective put).
            # Quantity in shares, entry price per share; no strike, expiry or IV.
            shares = _number(raw.get("quantity"), _ui_text(f'gamba {i} azioni', f'leg {i} shares'), 1, 1e7)
            if not shares.is_integer():
                raise ValueError(_ui_text(f'gamba {i}: numero di azioni intero obbligatorio', f'leg {i}: integer number of shares required'))
            if raw.get("multiplier", 1) != 1 or isinstance(raw.get("multiplier", 1), bool):
                raise ValueError(_ui_text(f'gamba {i}: le azioni hanno moltiplicatore 1', f'leg {i}: shares have multiplier 1'))
            leg = {"type": "stock", "side": raw["side"], "quantity": shares, "strike": None, "days": None, "iv": None,
                   "premium": _number(raw.get("premium"), _ui_text(f"gamba {i} prezzo d'ingresso azioni", f'leg {i} share entry price'), 1e-8, 1e8),
                   "multiplier": 1.0}
            leg["units"] = Fraction(str(shares)) * (1 if leg["side"] == "buy" else -1)
            legs.append(leg)
            continue
        if raw.get("type") not in ("call", "put") or raw.get("side") not in ("buy", "sell"):
            raise ValueError(_ui_text(f'gamba {i}: tipo/direzione non validi', f'leg {i}: invalid type/direction'))
        qty = _number(raw.get("quantity"), _ui_text(f'gamba {i} quantità', f'leg {i} quantity'), 1, 100000)
        if not qty.is_integer():
            raise ValueError(_ui_text(f'gamba {i}: quantità contratti intera obbligatoria', f'leg {i}: integer contract quantity required'))
        leg = {"type": raw["type"], "side": raw["side"], "quantity": qty,
               "strike": _number(raw.get("strike"), _ui_text(f'gamba {i} strike', f'leg {i} strike'), 1e-8, 1e8),
               "days": _number(raw.get("days"), _ui_text(f'gamba {i} giorni a scadenza', f'leg {i} days to expiry'), 0, 36500),
               "iv": _number(raw.get("iv"), _ui_text(f'gamba {i} IV', f'leg {i} IV'), 1e-8, 5),
               "premium": _number(raw.get("premium"), _ui_text(f'gamba {i} premio', f'leg {i} premium'), 0, 1e8),
               "multiplier": _number(raw.get("multiplier"), _ui_text(f'gamba {i} moltiplicatore', f'leg {i} multiplier'), 1e-8, 1e6)}
        if cfg["elapsed_days"] > leg["days"]:
            raise ValueError(_ui_text('orizzonte oltre la prima scadenza: non si inventa il percorso di regolamento delle gambe scadute', 'horizon beyond the first expiry: settlement paths for expired legs are not invented'))
        if not 0 < leg["iv"] + cfg["iv_shift"] <= 5:
            raise ValueError(_ui_text(f'gamba {i}: IV dopo lo shock fuori intervallo (0, 500%]', f'leg {i}: IV after shock outside the range (0, 500%]'))
        # Preserve decimal contract arithmetic: 3 * 0.1 and 1 * 0.3 cancel
        # exactly. Never decide risk or break-even using a cash epsilon.
        leg["units"] = Fraction(str(qty)) * Fraction(str(leg["multiplier"])) * (1 if leg["side"] == "buy" else -1)
        legs.append(leg)
    if not any(leg["type"] != "stock" for leg in legs):
        raise ValueError(_ui_text('serve almeno una gamba in opzioni', 'at least one option leg required'))
    return cfg, legs


def simulate_strategy(body):
    """Calculate a reviewable hypothetical strategy. Invalid inputs raise ValueError."""
    cfg, legs = _validated(body)
    option_legs = [leg for leg in legs if leg["type"] != "stock"]
    same_expiry = len({leg["days"] for leg in option_legs}) == 1
    # Commission is per option contract; share legs carry no contract fee (declared in assumptions).
    fees = sum(Fraction(str(leg["quantity"])) for leg in option_legs) * Fraction(str(cfg["commission"]))
    premium = sum(leg["units"] * Fraction(str(leg["premium"])) for leg in legs)
    entry_cost = premium + fees

    def payoff(leg, price):
        if leg["type"] == "stock":
            return price
        strike = Fraction(str(leg["strike"]))
        return max(0, price - strike) if leg["type"] == "call" else max(0, strike - price)

    def intrinsic_at(price):
        price = Fraction(str(price))
        return sum(leg["units"] * payoff(leg, price) for leg in legs) - entry_cost

    # Offset economically identical exposures before floating-point pricing.
    # Different IV/tenor assumptions remain separate model positions.
    grouped = {}
    for leg in legs:
        key = (leg["type"], leg["strike"], leg["days"], leg["iv"])
        grouped[key] = grouped.get(key, Fraction(0)) + leg["units"]
    model_legs = [(key, float(units)) for key, units in grouped.items() if units]

    horizon = min(leg["days"] for leg in option_legs)

    # 10/10 (Opus 5.5, review M2). A leg expiring after the first expiry T1 is valued, at elapsed
    # time e ≤ T1, with the volatility its own implied variance leaves once the first-expiry leg's
    # variance has been spent: σ(e)² = (σL²·TL − σ1²·e) / (TL − e). At e = 0 it is σL (today
    # unchanged); at e = T1 it is the forward vol √((σL²·TL − σ1²·T1)/(TL − T1)). σ1 = IV of the
    # first-expiry leg with the nearest strike (same strike in a calendar). A non-positive forward
    # variance is a calendar arbitrage in the inputs: declared, never replaced.
    first_legs = [leg for leg in option_legs if leg["days"] == horizon]
    anchors, forward_vols = {}, []
    for index, leg in enumerate(legs):
        if leg["type"] == "stock" or leg["days"] <= horizon:
            continue
        gap = min(abs(f["strike"] - leg["strike"]) for f in first_legs)
        near = [f["iv"] for f in first_legs if abs(f["strike"] - leg["strike"]) == gap]
        sigma1 = fsum(near) / len(near)
        variance = leg["iv"] ** 2 * leg["days"] - sigma1 ** 2 * horizon
        forward = sqrt(variance / (leg["days"] - horizon)) if variance > 0 else None
        anchors[(leg["strike"], leg["days"], leg["iv"])] = sigma1
        forward_vols.append({"index": index, "near_iv": sigma1, "leg_iv": leg["iv"], "forward_iv": forward})
        if cfg["vol_model"] == "forward":
            if forward is None:
                raise ValueError(_ui_text(
                    f'gamba {index + 1}: varianza forward non positiva (IV {leg["iv"]:.2%} a {leg["days"]:.2f} g contro {sigma1:.2%} a {horizon:.2f} g): forward vol n.d.; scegli «IV costante per gamba» per valutarla',
                    f'leg {index + 1}: non-positive forward variance (IV {leg["iv"]:.2%} at {leg["days"]:.2f} d vs {sigma1:.2%} at {horizon:.2f} d): forward vol n/a; choose "constant IV per leg" to value it'))
            if not 0 < forward + cfg["iv_shift"] <= 5:
                raise ValueError(_ui_text(f'gamba {index + 1}: forward vol dopo lo shock fuori intervallo (0, 500%]',
                                          f'leg {index + 1}: forward vol after shock outside the range (0, 500%]'))

    def leg_vol(strike, days, iv, elapsed):
        if cfg["vol_model"] == "constant" or days <= horizon or elapsed <= 0:
            return iv
        sigma1 = anchors[(strike, days, iv)]
        return sqrt((iv * iv * days - sigma1 * sigma1 * elapsed) / (days - elapsed))

    def unit_value(kind, strike, days, iv, price, elapsed, shift):
        if kind == "stock":
            return {"price": price, "delta": 1.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}
        return _european_core(float(price), strike, days - elapsed, leg_vol(strike, days, iv, elapsed) + shift,
                              cfg["rate"], cfg["dividend_yield"], kind == "call")

    def scenario_at(price, elapsed, shift):
        values = [(units, unit_value(kind, strike, days, iv, price, elapsed, shift))
                  for (kind, strike, days, iv), units in model_legs]
        result = {"price": price, "pnl": (float(intrinsic_at(price)) if same_expiry and elapsed == option_legs[0]["days"]
                   else fsum([units * value["price"] for units, value in values] + [-float(entry_cost)]))}
        for greek in ("delta", "gamma", "vega", "theta", "rho"):
            result[greek] = (None if any(v[greek] is None for _, v in values)
                             else fsum(units * v[greek] for units, v in values))
        return result

    strikes = sorted({leg["strike"] for leg in option_legs})
    low = max(0, min(cfg["spot"] * .6, strikes[0] * .8))
    high = max(cfg["spot"] * 1.4, strikes[-1] * 1.2)
    # 09/10 (Opus 5.5): 240 intervals (was 120) so the pointer reading lands on a computed price
    # at least every ~0.4% of spot; strikes and spot stay exact grid nodes.
    grid = sorted({low + (high - low) * i / 240 for i in range(241)} | set(strikes) | {cfg["spot"]})

    def first_expiry_pnl(price):
        """P/L when the first option expires: exact intrinsic, or BSM for the legs still alive."""
        if same_expiry:
            return float(intrinsic_at(price))
        return fsum([units * (price if kind == "stock" else
                              _unit_price(price, strike, days - horizon, leg_vol(strike, days, iv, horizon) + cfg["iv_shift"], cfg["rate"],
                                          cfg["dividend_yield"], kind == "call"))
                     for (kind, strike, days, iv), units in model_legs] + [-float(entry_cost)])

    curves = [{"price": s,
               "expiry": first_expiry_pnl(s),
               "today": scenario_at(s, 0, 0)["pnl"],
               "scenario": scenario_at(s, cfg["elapsed_days"], cfg["iv_shift"])["pnl"]} for s in grid]
    roots, zero_intervals, max_profit, max_loss = [], [], None, None
    unlimited_profit = unlimited_loss = False
    limit_note = None
    loss_reason = None
    tail_reference = None
    if same_expiry:
        knots = [Fraction(0)] + [Fraction(str(strike)) for strike in strikes]
        values = [intrinsic_at(s) for s in knots]
        for x1, x2, y1, y2 in zip(knots, knots[1:], values, values[1:]):
            if y1 == 0 and y2 == 0:
                zero_intervals.append({"from": x1, "to": x2})
            if y1 == 0:
                roots.append(x1)
            if y1 * y2 < 0:
                roots.append(x1 - y1 * (x2 - x1) / (y2 - y1))
        if values[-1] == 0:
            roots.append(knots[-1])
        # Above the last strike only calls and shares move with the price.
        tail_slope = sum(leg["units"] for leg in legs if leg["type"] in ("call", "stock"))
        if not tail_slope and values[-1] == 0:
            zero_intervals.append({"from": knots[-1], "to": None})
        if tail_slope:
            tail_root = knots[-1] - values[-1] / tail_slope
            if tail_root > knots[-1]:
                roots.append(tail_root)
        unlimited_profit, unlimited_loss = tail_slope > 0, tail_slope < 0
        loss_reason = "structural" if unlimited_loss else None
        max_profit = None if unlimited_profit else max(values)
        max_loss = None if unlimited_loss else max(0, -min(values))
        merged = []
        for interval in zero_intervals:
            if merged and merged[-1]["to"] == interval["from"]:
                merged[-1]["to"] = interval["to"]
            else:
                merged.append(dict(interval))
        zero_intervals = merged
        roots = [root for root in roots if not any(interval["from"] <= root and
                 (interval["to"] is None or root <= interval["to"]) for interval in zero_intervals)]
    else:
        # 09/10 (Opus 5.5, audit M6): mixed expiries. The first-expiry P/L is smooth (BSM on the
        # live legs, constant IV + the chosen shock). Tails are analytic: as S → ∞ a live call
        # tends to S·e^(−qτ) − K·e^(−rτ), a put to 0, an expired call to S − K, a share to S.
        r, q = cfg["rate"], cfg["dividend_yield"]
        slope, level, slope_without_q = 0.0, -float(entry_cost), 0.0
        for (kind, strike, days, iv), units in model_legs:
            if kind == "stock":
                slope += units
                slope_without_q += units
            elif kind == "call":
                tau = (days - horizon) / 365
                slope += units * exp(-q * tau)
                slope_without_q += units
                level -= units * strike * exp(-r * tau)
        scale = fsum(abs(units) for _, units in model_legs) or 1.0
        tol = 1e-9 * scale
        unlimited_profit, unlimited_loss = slope > tol, slope < -tol
        # M1 (review 10/10): a loss that is unbounded ONLY because a European live call grows like
        # S·e^(−qτ) < S is an artefact of the model; an American holder would exercise early.
        if unlimited_loss:
            loss_reason = "european_dividend" if slope_without_q >= -tol else "structural"
        tail_reference = {"price": cfg["spot"] * 4, "pnl": first_expiry_pnl(cfg["spot"] * 4)}
        # sign of the P/L far to the right: the slope when it is not flat, otherwise the limit level
        far = (1 if slope > 0 else -1) if abs(slope) > tol else (1 if level > tol else -1 if level < -tol else 0)
        top = max(cfg["spot"] * 4, strikes[-1] * 3)
        n = 2000
        xs = [top * i / n for i in range(n + 1)]
        ys = [first_expiry_pnl(x) for x in xs]

        def bisect(a, b, fa):
            for _ in range(200):
                m = (a + b) / 2
                fm = first_expiry_pnl(m)
                if fm == 0 or b - a <= 1e-12 * max(1.0, b):
                    return m
                if (fm > 0) == (fa > 0):
                    a, fa = m, fm
                else:
                    b = m
            return (a + b) / 2

        for x1, x2, y1, y2 in zip(xs, xs[1:], ys, ys[1:]):
            if y1 == 0:
                roots.append(x1)
            elif y1 * y2 < 0:
                roots.append(bisect(x1, x2, y1))
        if ys[-1] != 0 and far and (ys[-1] > 0) != (far > 0):
            a, fa, b = xs[-1], ys[-1], xs[-1] * 2
            for _ in range(60):
                if (first_expiry_pnl(b) > 0) != (fa > 0):
                    roots.append(bisect(a, b, fa))
                    break
                a, fa, b = b, first_expiry_pnl(b), b * 2

        def golden(i, sign):
            a, b = xs[max(i - 1, 0)], xs[min(i + 1, n)]
            g = (5 ** .5 - 1) / 2
            c, d = b - g * (b - a), a + g * (b - a)
            for _ in range(80):
                if sign * first_expiry_pnl(c) > sign * first_expiry_pnl(d):
                    b = d
                else:
                    a = c
                c, d = b - g * (b - a), a + g * (b - a)
            return first_expiry_pnl((a + b) / 2)

        best = max(range(n + 1), key=lambda i: ys[i])
        worst = min(range(n + 1), key=lambda i: ys[i])
        top_value = max(ys[best], golden(best, 1))
        bottom_value = min(ys[worst], golden(worst, -1))
        if not unlimited_profit and not unlimited_loss:
            # Flat right tail: the limit is a supremum/infimum approached, never a grid artefact.
            top_value, bottom_value = max(top_value, level), min(bottom_value, level)
            limit_note = level
        max_profit = None if unlimited_profit else top_value
        max_loss = None if unlimited_loss else max(0.0, -bottom_value)
    now = scenario_at(cfg["spot"], 0, 0)
    selected = scenario_at(cfg["scenario_spot"], cfg["elapsed_days"], cfg["iv_shift"])
    heat = []
    # Price × elapsed-time lattice. Volatility shock is the explicit UI setting.
    for elapsed in _heatmap_elapsed(horizon):
        heat.append({"elapsed_days": elapsed, "cells": [scenario_at(cfg["spot"] * ratio, elapsed, cfg["iv_shift"])
                    for ratio in (.7, .8, .9, 1.0, 1.1, 1.2, 1.3)]})
    breakevens = sorted(set(float(x) for x in roots))

    # 09/10 (Opus 5.5): probability of profit at the first expiry, lognormal and risk-neutral.
    # σ = IV (+ shock) of the legs that expire first, weighted by |contracts × multiplier|.
    pop = None
    first = [(abs(float(leg["units"])), leg["iv"] + cfg["iv_shift"]) for leg in option_legs if leg["days"] == horizon]
    if horizon > 0 and first:
        sigma = fsum(w * v for w, v in first) / fsum(w for w, _ in first)
        t = horizon / 365
        drift = (cfg["rate"] - cfg["dividend_yield"] - sigma * sigma / 2) * t

        def below(x):
            if x is None:
                return 1.0
            if x <= 0:
                return 0.0
            return (1 + erf((log(x / cfg["spot"]) - drift) / (sigma * sqrt(t)) / sqrt(2))) / 2

        edges = [0.0] + breakevens + [None]
        probability = 0.0
        for a, b in zip(edges, edges[1:]):
            probe = (a + b) / 2 if b is not None else (cfg["spot"] if a == 0 else a * 2)
            if first_expiry_pnl(probe) > 0:
                probability += below(b) - below(a)
        pop = {"value": min(1.0, max(0.0, probability)), "sigma": sigma, "horizon_days": horizon,
               "basis": _ui_text('lognormale neutrale al rischio (drift r − q), σ = IV delle gambe alla prima scadenza ponderata per contratti, shock incluso; non è una previsione',
                                 'risk-neutral lognormal (drift r − q), σ = IV of the first-expiry legs weighted by contracts, shock included; not a forecast')}

    # Per-leg view at today's spot: model value, P/L against the entry price, greeks per contract.
    leg_details = []
    for index, leg in enumerate(legs):
        unit = unit_value(leg["type"], leg["strike"], leg["days"], leg["iv"], cfg["spot"], 0, 0)
        per_contract = (1 if leg["side"] == "buy" else -1) * leg["multiplier"]
        leg_details.append({"index": index, "type": leg["type"], "side": leg["side"],
                            "value": unit["price"], "pnl_today": float(leg["units"]) * (unit["price"] - leg["premium"]),
                            **{greek: None if unit[greek] is None else per_contract * unit[greek]
                               for greek in ("delta", "gamma", "vega", "theta", "rho")}})
    return {"currency": cfg["currency"], "model": _ui_text('Black–Scholes–Merton europeo', 'European Black–Scholes–Merton'),
            "model_source": _ui_text('calcolo locale teorico, nessuna quotazione recuperata', 'local theoretical calculation; no quote retrieved'),
            "greek_units": {"delta": _ui_text('unità sottostante', 'underlying units'), "gamma": _ui_text('delta per unità di prezzo', 'delta per price unit'),
                            "vega": _ui_text('valuta per +1 punto percentuale IV', 'currency per +1 percentage point IV'), "theta": _ui_text('valuta per giorno ACT/365', 'currency per ACT/365 day'),
                            "rho": _ui_text('valuta per +1 punto percentuale tasso', 'currency per +1 percentage point interest rate')},
            "entry_cost": float(entry_cost), "net_premium": float(premium), "fees": float(fees),
            # L1: a zero-cost entry is neither an outlay nor a credit.
            "entry_kind": "even" if entry_cost == 0 else "debit" if entry_cost > 0 else "credit",
            "same_expiry": same_expiry, "expiry_days": horizon,
            # exact = piecewise-linear payoff (one expiry); model_first_expiry = BSM on live legs at T1.
            "expiry_basis": "exact" if same_expiry else "model_first_expiry",
            "tail_limit": limit_note,
            "breakevens": breakevens,
            "breakeven_intervals": [{"from": float(x["from"]), "to": None if x["to"] is None else float(x["to"])} for x in zero_intervals],
            "max_profit": None if max_profit is None else float(max_profit),
            "max_loss": None if max_loss is None else float(max_loss),
            "unlimited_profit": unlimited_profit, "unlimited_loss": unlimited_loss,
            # structural | european_dividend (model artefact: bounded with American exercise) | None
            "unlimited_loss_reason": loss_reason,
            # first-expiry P/L at 4× spot, a finite reference next to an unbounded model tail
            "tail_reference": tail_reference,
            "vol_model": cfg["vol_model"],
            "vol_model_basis": (_ui_text('forward vol dalla superficie di oggi: una gamba che scade dopo la prima scadenza usa la varianza implicita residua σ(e)² = (σL²·TL − σ1²·e)/(TL − e); σ1 = IV della gamba alla prima scadenza con lo strike più vicino',
                                         "forward vol from today's surface: a leg expiring after the first expiry uses the residual implied variance σ(e)² = (σL²·TL − σ1²·e)/(TL − e); σ1 = IV of the first-expiry leg with the nearest strike")
                                if cfg["vol_model"] == "forward" else
                                _ui_text('IV costante per gamba: ogni gamba conserva la propria IV di oggi fino alla prima scadenza', "constant IV per leg: each leg keeps today's own IV up to the first expiry")),
            "forward_vols": forward_vols,
            "probability_of_profit": pop,
            "legs": leg_details,
            "today": now, "scenario": selected, "curve": curves, "heatmap": heat,
            "assumptions": {**cfg, "premium_basis": _ui_text("premi inseriti dall'utente; non sono prezzi di esecuzione", 'user-entered premiums; these are not execution prices'),
                            "fees_basis": _ui_text('costo iniziale per contratto in opzioni; azioni senza commissione; uscita, slippage, finanziamento e imposte esclusi', 'initial cost per option contract; shares carry no fee; exit, slippage, financing and taxes excluded')},
            "limits": [_ui_text('Opzioni europee; esercizio anticipato americano e dividendi discreti non modellati.', 'European options; American early exercise and discrete dividends are not modeled.'),
                       (_ui_text('IV costante per gamba più shock parallelo; nessuna previsione di mercato.', 'Constant IV per leg plus a parallel shock; no market forecast.')
                        if cfg["vol_model"] == "constant" or not forward_vols else
                        _ui_text('Gambe oltre la prima scadenza valutate con la forward vol della superficie di oggi, più shock parallelo; nessuna previsione di mercato.', "Legs beyond the first expiry are valued with today's surface forward vol, plus a parallel shock; no market forecast.")),
                       _ui_text('Premi e quote possono riferirsi a istanti diversi: controllare i timestamp prima di confrontarli.', 'Premiums and quotes may refer to different times: check timestamps before comparing them.'),
                       _ui_text('Con scadenze diverse il P/L alla prima scadenza valuta le gambe ancora vive con BSM: breakeven e massimi sono teorici (griglia di 2000 punti fino a 4× spot + affinamento, code analitiche).', 'With mixed expiries the first-expiry P/L values the live legs with BSM: break-evens and extremes are theoretical (2,000-point grid up to 4× spot + refinement, analytic tails).')]}
