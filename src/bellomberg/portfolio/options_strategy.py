"""Pure, offline options laboratory. No quote retrieval, portfolio or order API.

European Black–Scholes–Merton with continuous dividend yield; ACT/365.
Cash flows are in the explicitly supplied currency. Price is per underlying
unit; position values/greeks apply quantity, direction and contract multiplier.
"""
from fractions import Fraction
from math import erf, exp, fsum, isfinite, log, pi, sqrt


def _number(value, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: numero finito obbligatorio")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name}: numero fuori intervallo") from exc
    if not isfinite(value):
        raise ValueError(f"{name}: numero finito obbligatorio")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f"{name}: fuori intervallo consentito [{low}, {high}]")
    return float(value)


def european_option(spot, strike, days, iv, rate, dividend_yield, option_type):
    """Unit price; delta/dSpot, gamma/dSpot², vega/rho per 1pp, theta/day.

    At expiry the intrinsic value is exact. At the strike, expiry derivatives
    are undefined and returned as None; they must not become zero in the UI.
    """
    s = _number(spot, "spot", 0, 1e9)
    k = _number(strike, "strike", 1e-9, 1e9)
    d = _number(days, "giorni", 0, 36500)
    vol = _number(iv, "IV", 1e-8, 5)
    r = _number(rate, "tasso", -1, 1)
    q = _number(dividend_yield, "dividend yield", -1, 1)
    if option_type not in ("call", "put"):
        raise ValueError("tipo opzione: call o put")
    call = option_type == "call"
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
        raise ValueError("simulazione: oggetto obbligatorio")
    cfg = {"spot": _number(body.get("spot"), "spot", 1e-8, 1e8),
           "scenario_spot": _number(body.get("scenario_spot", body.get("spot")), "prezzo scenario", 0, 1e8),
           "rate": _number(body.get("rate"), "tasso", -1, 1),
           "dividend_yield": _number(body.get("dividend_yield"), "dividend yield", -1, 1),
           "elapsed_days": _number(body.get("elapsed_days"), "giorni trascorsi", 0, 36500),
           "iv_shift": _number(body.get("iv_shift"), "shock IV", -4.99, 4.99),
           "commission": _number(body.get("commission"), "costo per contratto", 0, 1e6)}
    currency = body.get("currency")
    if not isinstance(currency, str) or not currency.isalpha() or len(currency) != 3 or not currency.isupper():
        raise ValueError("valuta: codice ISO di tre lettere obbligatorio")
    cfg["currency"] = currency
    rows = body.get("legs")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
        raise ValueError("servono da 1 a 12 gambe")
    legs = []
    for i, raw in enumerate(rows, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"gamba {i}: oggetto obbligatorio")
        if raw.get("type") not in ("call", "put") or raw.get("side") not in ("buy", "sell"):
            raise ValueError(f"gamba {i}: tipo/direzione non validi")
        qty = _number(raw.get("quantity"), f"gamba {i} quantità", 1, 100000)
        if not qty.is_integer():
            raise ValueError(f"gamba {i}: quantità contratti intera obbligatoria")
        leg = {"type": raw["type"], "side": raw["side"], "quantity": qty,
               "strike": _number(raw.get("strike"), f"gamba {i} strike", 1e-8, 1e8),
               "days": _number(raw.get("days"), f"gamba {i} giorni a scadenza", 0, 36500),
               "iv": _number(raw.get("iv"), f"gamba {i} IV", 1e-8, 5),
               "premium": _number(raw.get("premium"), f"gamba {i} premio", 0, 1e8),
               "multiplier": _number(raw.get("multiplier"), f"gamba {i} moltiplicatore", 1e-8, 1e6)}
        if cfg["elapsed_days"] > leg["days"]:
            raise ValueError("orizzonte oltre la prima scadenza: non si inventa il percorso di regolamento delle gambe scadute")
        if not 0 < leg["iv"] + cfg["iv_shift"] <= 5:
            raise ValueError(f"gamba {i}: IV dopo lo shock fuori intervallo (0, 500%]")
        # Preserve decimal contract arithmetic: 3 * 0.1 and 1 * 0.3 cancel
        # exactly. Never decide risk or break-even using a cash epsilon.
        leg["units"] = Fraction(str(qty)) * Fraction(str(leg["multiplier"])) * (1 if leg["side"] == "buy" else -1)
        legs.append(leg)
    return cfg, legs


def simulate_strategy(body):
    """Calculate a reviewable hypothetical strategy. Invalid inputs raise ValueError."""
    cfg, legs = _validated(body)
    same_expiry = len({leg["days"] for leg in legs}) == 1
    fees = sum(Fraction(str(leg["quantity"])) for leg in legs) * Fraction(str(cfg["commission"]))
    premium = sum(leg["units"] * Fraction(str(leg["premium"])) for leg in legs)
    entry_cost = premium + fees

    def intrinsic_at(price):
        price = Fraction(str(price))
        return sum(leg["units"] * (max(0, price - Fraction(str(leg["strike"]))) if leg["type"] == "call"
                                   else max(0, Fraction(str(leg["strike"])) - price)) for leg in legs) - entry_cost

    # Offset economically identical exposures before floating-point pricing.
    # Different IV/tenor assumptions remain separate model positions.
    grouped = {}
    for leg in legs:
        key = (leg["type"], leg["strike"], leg["days"], leg["iv"])
        grouped[key] = grouped.get(key, Fraction(0)) + leg["units"]
    model_legs = [(key, float(units)) for key, units in grouped.items() if units]

    def scenario_at(price, elapsed, shift):
        values = [(units, european_option(price, strike, days - elapsed, iv + shift,
                                         cfg["rate"], cfg["dividend_yield"], kind))
                  for (kind, strike, days, iv), units in model_legs]
        result = {"price": price, "pnl": (float(intrinsic_at(price)) if same_expiry and elapsed == legs[0]["days"]
                   else fsum([units * value["price"] for units, value in values] + [-float(entry_cost)]))}
        for greek in ("delta", "gamma", "vega", "theta", "rho"):
            result[greek] = (None if any(v[greek] is None for _, v in values)
                             else fsum(units * v[greek] for units, v in values))
        return result

    strikes = sorted({leg["strike"] for leg in legs})
    low = max(0, min(cfg["spot"] * .6, strikes[0] * .8))
    high = max(cfg["spot"] * 1.4, strikes[-1] * 1.2)
    grid = sorted({low + (high - low) * i / 120 for i in range(121)} | set(strikes) | {cfg["spot"]})
    curves = [{"price": s,
               "expiry": float(intrinsic_at(s)) if same_expiry else None,
               "today": scenario_at(s, 0, 0)["pnl"],
               "scenario": scenario_at(s, cfg["elapsed_days"], cfg["iv_shift"])["pnl"]} for s in grid]
    roots, zero_intervals, max_profit, max_loss = [], [], None, None
    unlimited_profit = unlimited_loss = False
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
        tail_slope = sum(leg["units"] for leg in legs if leg["type"] == "call")
        if not tail_slope and values[-1] == 0:
            zero_intervals.append({"from": knots[-1], "to": None})
        if tail_slope:
            tail_root = knots[-1] - values[-1] / tail_slope
            if tail_root > knots[-1]:
                roots.append(tail_root)
        unlimited_profit, unlimited_loss = tail_slope > 0, tail_slope < 0
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
    now = scenario_at(cfg["spot"], 0, 0)
    selected = scenario_at(cfg["scenario_spot"], cfg["elapsed_days"], cfg["iv_shift"])
    heat = []
    # Price × elapsed-time lattice. Volatility shock is the explicit UI setting.
    horizon = min(leg["days"] for leg in legs)
    for i in range(6):
        elapsed = horizon * i / 5
        heat.append({"elapsed_days": elapsed, "cells": [scenario_at(cfg["spot"] * ratio, elapsed, cfg["iv_shift"])
                    for ratio in (.7, .8, .9, 1.0, 1.1, 1.2, 1.3)]})
    return {"currency": cfg["currency"], "model": "Black–Scholes–Merton europeo",
            "model_source": "calcolo locale teorico, nessuna quotazione recuperata",
            "greek_units": {"delta": "unità sottostante", "gamma": "delta per unità di prezzo",
                            "vega": "valuta per +1 punto percentuale IV", "theta": "valuta per giorno ACT/365",
                            "rho": "valuta per +1 punto percentuale tasso"},
            "entry_cost": float(entry_cost), "net_premium": float(premium), "fees": float(fees),
            "entry_kind": "debit" if entry_cost >= 0 else "credit",
            "same_expiry": same_expiry, "expiry_days": horizon,
            "breakevens": sorted(set(float(x) for x in roots)),
            "breakeven_intervals": [{"from": float(x["from"]), "to": None if x["to"] is None else float(x["to"])} for x in zero_intervals],
            "max_profit": None if max_profit is None else float(max_profit),
            "max_loss": None if max_loss is None else float(max_loss),
            "unlimited_profit": unlimited_profit, "unlimited_loss": unlimited_loss,
            "today": now, "scenario": selected, "curve": curves, "heatmap": heat,
            "assumptions": {**cfg, "premium_basis": "premi inseriti dall'utente; non sono prezzi di esecuzione",
                            "fees_basis": "costo iniziale per contratto; uscita, slippage, finanziamento e imposte esclusi"},
            "limits": ["Opzioni europee; esercizio anticipato americano e dividendi discreti non modellati.",
                       "IV costante per gamba più shock parallelo; nessuna previsione di mercato.",
                       "Premi e quote possono riferirsi a istanti diversi: controllare i timestamp prima di confrontarli.",
                       "Payoff unico a scadenza disponibile solo se tutte le gambe scadono insieme; per calendari, scenari fino alla prima scadenza."]}
