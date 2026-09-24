"""
positioning_tools.py — Market Positioning Pack (#177)

Tre tool per leggere il POSIZIONAMENTO del mercato:

1. compute_gex(ticker)         — Gamma Exposure dei dealer da chain Polygon
                                  (metodologia SqueezeMetrics: call gamma long,
                                  put gamma short, convenzione dealer-long-calls).
                                  Output: GEX netto, profilo per strike, gamma flip.
2. get_cot_positioning(market) — CFTC Commitments of Traders (TFF report, GRATIS,
                                  settimanale): posizioni nette di Dealer /
                                  Asset Manager / Leveraged Funds sui futures
                                  (ES, NQ, EUR, oro, petrolio, 10Y, VIX, BTC...).
3. get_vix_term_structure()    — VIX9D/VIX/VIX3M/VIX6M via yfinance:
                                  contango/backwardation come termometro regime.

Riferimenti: SqueezeMetrics "GEX"; Garleanu-Pedersen-Poteshman (RFS 2009);
CFTC Traders in Financial Futures.
Convenzione anti-hallucination: ogni risultato ha _source e _timestamp;
errori ritornati come {"error": ...}, mai eccezioni propagate.
"""
from bellomberg.core.paths import PROJECT_ROOT
from bellomberg.core.language import scoped_language
from bellomberg.core.presentation import error_text, message
import os
from datetime import datetime, date
from typing import Dict, Any, Optional, List

try:
    import requests
    REQ_OK = True
except ImportError:
    REQ_OK = False

try:  # carica .env in esecuzione standalone
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


# ============================================================
# 1) GEX — Gamma Exposure (richiede Polygon Options)
# ============================================================

@scoped_language
def compute_gex(ticker: str, max_expiries: int = 5,
                days_window: int = 45) -> Dict[str, Any]:
    """Dealer Gamma Exposure aggregata sulle expiry entro days_window giorni.

    GEX per contratto = gamma * OI * 100 (multiplier) * spot^2 * 0.01
    (dollar-gamma per movimento dell'1% dello spot).
    Convenzione: dealer LONG call gamma (+), SHORT put gamma (-).
    Gamma flip = livello di prezzo dove il GEX cumulato cambia segno.
    """
    src = "polygon chain -> GEX (SqueezeMetrics convention)"
    try:
        from bellomberg.market_data.polygon_data import polygon_available, get_option_expirations, get_options_chain
        if not polygon_available():
            return {"error": message("POLYGON_API_KEY mancante o non attiva", "POLYGON_API_KEY missing or inactive"), "_source": src}

        exp = get_option_expirations(ticker)
        if exp.get("error"):
            return {"error": message("scadenze: {reason}", "expirations: {reason}", reason=exp['error']), "_source": src}
        today = datetime.now().date()
        chosen: List[str] = []
        for e in exp.get("expirations", []):
            try:
                d = datetime.strptime(e, "%Y-%m-%d").date()
            except Exception:
                continue
            if 0 <= (d - today).days <= days_window:
                chosen.append(e)
            if len(chosen) >= max_expiries:
                break
        if not chosen:
            return {"error": message("nessuna expiry entro {days} giorni", "no expiry within {days} days", days=days_window), "_source": src}

        spot = None
        by_strike: Dict[float, Dict[str, float]] = {}
        tot_call_gex = tot_put_gex = 0.0
        tot_call_oi = tot_put_oi = 0
        n_contracts = 0
        for e in chosen:
            ch = get_options_chain(ticker, e, max_contracts=1200)
            if ch.get("error"):
                continue
            for c in ch.get("chain", []):
                gamma = c.get("gamma")
                oi = c.get("oi") or 0
                strike = c.get("strike")
                ctype = (c.get("type") or "").lower()
                px = c.get("close")
                if spot is None and px and strike and abs(px) > 0:
                    pass  # lo spot lo stimiamo sotto via strike ATM
                if gamma is None or not oi or not strike:
                    continue
                n_contracts += 1
                # spot proxy: strike del contratto con delta ~0.5 lo raffiniamo dopo;
                # qui accumuliamo gamma raw, lo scaliamo a fine loop quando abbiamo spot
                rec = by_strike.setdefault(float(strike), {"call": 0.0, "put": 0.0,
                                                            "call_oi": 0, "put_oi": 0})
                if ctype == "call":
                    rec["call"] += float(gamma) * oi
                    rec["call_oi"] += oi
                    tot_call_oi += oi
                else:
                    rec["put"] += float(gamma) * oi
                    rec["put_oi"] += oi
                    tot_put_oi += oi
                # stima spot: delta più vicino a 0.5 sulle call
                d = c.get("delta")
                if ctype == "call" and d is not None and abs(d - 0.5) < 0.06:
                    spot = float(strike)

        if not by_strike:
            return {"error": message("chain senza gamma/OI utilizzabili", "chain has no usable gamma/OI"), "_source": src}
        spot_method = "atm_call_strike"
        if spot is None:
            ks = sorted(by_strike.keys())
            spot = ks[len(ks) // 2]  # fallback: strike mediano
            spot_method = "median_strike"
        spot_note = (message("Spot proxy: strike call con delta vicino a 0,5; non è una quotazione del sottostante.",
                             "Spot proxy: call strike with delta near 0.5; this is not an underlying quote.")
                     if spot_method == "atm_call_strike" else
                     message("Spot proxy: strike mediano, non essendoci una call con delta vicino a 0,5; non è una quotazione del sottostante.",
                             "Spot proxy: median strike because no call has delta near 0.5; this is not an underlying quote."))

        scale = 100.0 * spot * spot * 0.01  # dollar gamma per 1% move
        profile = []
        for k in sorted(by_strike.keys()):
            rec = by_strike[k]
            call_gex = rec["call"] * scale
            put_gex = -rec["put"] * scale  # dealer short put gamma
            net = call_gex + put_gex
            tot_call_gex += call_gex
            tot_put_gex += put_gex
            profile.append({"strike": k, "net_gex_usd": round(net, 0),
                            "call_oi": rec["call_oi"], "put_oi": rec["put_oi"]})

        net_gex = tot_call_gex + tot_put_gex
        # gamma flip: zero-crossing del GEX cumulato lungo gli strike
        flip = None
        cum = 0.0
        prev_k, prev_cum = None, None
        for row in profile:
            cum += row["net_gex_usd"]
            if prev_cum is not None and (prev_cum < 0 <= cum or prev_cum > 0 >= cum):
                flip = round((prev_k + row["strike"]) / 2, 2)
                break
            prev_k, prev_cum = row["strike"], cum

        # tieni solo i 12 strike più rilevanti per output compatto
        profile_top = sorted(profile, key=lambda r: abs(r["net_gex_usd"]),
                             reverse=True)[:12]
        profile_top.sort(key=lambda r: r["strike"])

        # Regime: priorità a spot vs gamma flip (zero gamma level); il GEX netto
        # totale resta come misura di intensità. Evita label contraddittorie.
        gamma_pos = (spot > flip) if flip is not None else (net_gex > 0)
        regime_basis = (message("spot sopra il gamma flip", "spot above the gamma flip") if gamma_pos else
                        message("spot sotto il gamma flip", "spot below the gamma flip")) if flip is not None else message("GEX netto; flip non disponibile", "net GEX; flip unavailable")
        regime = (message("POSITIVO ({basis}): dealer comprano i dip e vendono i rally -> mercato compresso, mean-reversion, vol venduta",
                          "POSITIVE ({basis}): dealers buy dips and sell rallies -> compressed market, mean reversion, volatility sold", basis=regime_basis)
                  if gamma_pos else message("NEGATIVO ({basis}): dealer amplificano i movimenti -> accelerazioni, momentum, vol comprata",
                                             "NEGATIVE ({basis}): dealers amplify moves -> acceleration, momentum, volatility bought", basis=regime_basis))

        return {
            "ticker": ticker.upper(),
            "spot_est": spot,
            "spot_method": spot_method,
            "spot_note": spot_note,
            "expiries_used": chosen,
            "n_contracts": n_contracts,
            "net_gex_usd_per_1pct": round(net_gex, 0),
            "call_gex_usd": round(tot_call_gex, 0),
            "put_gex_usd": round(tot_put_gex, 0),
            "total_call_oi": tot_call_oi,
            "total_put_oi": tot_put_oi,
            "put_call_oi_ratio": round(tot_put_oi / tot_call_oi, 3) if tot_call_oi else None,
            "gamma_flip_strike": flip,
            "spot_vs_flip": (None if flip is None else
                             (message("sopra il flip (gamma positivo)", "above the flip (positive gamma)") if spot > flip
                              else message("sotto il flip (gamma negativo)", "below the flip (negative gamma)"))),
            "regime": regime,
            "note": message("OI e put/call ratio calcolati sui contratti con greeks validi "
                    "nello snapshot delayed (i deep-OTM senza quote sono esclusi: "
                    "gamma trascurabile)", "OI and put/call ratio use contracts with valid greeks in the delayed snapshot (deep-OTM contracts without quotes are excluded: negligible gamma)"),
            "top_strikes": profile_top,
            "_source": src,
            "_timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": error_text(e), "_source": src}


# ============================================================
# 2) COT — CFTC Commitments of Traders (TFF/Disaggregated, gratis)
# ============================================================

_COT_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"  # TFF Futures Only
_COT_DISAGGREGATED_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
_COT_COMMODITIES = {
    "WTI": "067651", "CL": "067651",  # WTI-PHYSICAL, NYMEX
    "GOLD": "088691", "GC": "088691",  # GOLD, COMEX
}

COT_MARKET_ALIASES = {
    "ES": "E-MINI S&P 500", "SPX": "E-MINI S&P 500", "SP500": "E-MINI S&P 500",
    "NQ": "NASDAQ MINI", "NASDAQ": "NASDAQ MINI",
    "EUR": "EURO FX", "EURUSD": "EURO FX",
    "JPY": "JAPANESE YEN", "GBP": "BRITISH POUND",
    "10Y": "10-YEAR U.S. TREASURY NOTES", "ZN": "10-YEAR U.S. TREASURY NOTES",
    "VIX": "VIX FUTURES", "BTC": "BITCOIN", "BITCOIN": "BITCOIN",
}


def _cot_net(row: Dict[str, Any], prefix: str) -> Optional[int]:
    """Net = long - short per una categoria TFF (campi dinamici, schema-resilient)."""
    lk = [k for k in row if k.startswith(prefix) and "long" in k and "spread" not in k]
    sk = [k for k in row if k.startswith(prefix) and "short" in k and "spread" not in k]
    try:
        if lk and sk:
            return int(float(row[lk[0]])) - int(float(row[sk[0]]))
    except (TypeError, ValueError):
        pass
    return None


def _cot_net_fields(row: Dict[str, Any], long_key: str, short_key: str) -> Optional[int]:
    """Disaggregated: nomi esatti, senza usare varianti Old/Other come proxy."""
    try:
        if row.get(long_key) is not None and row.get(short_key) is not None:
            return int(float(row[long_key])) - int(float(row[short_key]))
    except (TypeError, ValueError, OverflowError):
        pass
    return None


_COT_DISAGGREGATED_CATS = {
    "producer_merchant": ("prod_merc_positions_long", "prod_merc_positions_short"),
    "swap_dealers": ("swap_positions_long_all", "swap__positions_short_all"),
    "managed_money": ("m_money_positions_long_all", "m_money_positions_short_all"),
    "other_reportables": ("other_rept_positions_long", "other_rept_positions_short"),
}


@scoped_language
def get_cot_positioning(market: str = "ES", weeks: int = 52) -> Dict[str, Any]:
    """CFTC futures-only: TFF finanziari, Disaggregated per WTI e oro."""
    market_key = (market or "").upper().strip()
    commodity = market_key in _COT_COMMODITIES
    src = ("CFTC publicreporting.cftc.gov (Disaggregated futures-only)" if commodity
           else "CFTC publicreporting.cftc.gov (TFF futures-only)")
    if market_key in {"OIL", "CRUDE OIL"}:
        return {"error": message(
            "'OIL/CRUDE OIL' e' ambiguo: specifica WTI o CL (WTI-PHYSICAL NYMEX, CFTC 067651); altri contratti petroliferi non sono supportati",
            "'OIL/CRUDE OIL' is ambiguous: specify WTI or CL (WTI-PHYSICAL NYMEX, CFTC 067651); other oil contracts are unsupported"),
            "_source": "CFTC Commitments of Traders"}
    if not REQ_OK:
        return {"error": message("requests non disponibile", "requests unavailable"), "_source": src}
    name = COT_MARKET_ALIASES.get(market_key, market_key)
    try:
        if not 1 <= int(weeks) <= 104:
            return {"error": message("weeks deve essere tra 1 e 104", "weeks must be between 1 and 104"), "_source": src}
        code = _COT_COMMODITIES.get(market_key)
        params = {
            "$where": (f"cftc_contract_market_code = '{code}'" if commodity else
                       f"upper(contract_market_name) like '%{name}%'"),
            "$order": "report_date_as_yyyy_mm_dd DESC",
            # audit/11 §4: il limit va preso LARGO — le 52 righe piu' recenti del LIKE
            # includono le varianti (MICRO E-MINI...) e dopo il filtro esatto la storia
            # per il percentile 1y restava troncata. Si taglia a 'weeks' DOPO il dedup.
            "$limit": int(weeks) * 4,
        }
        r = requests.get(_COT_DISAGGREGATED_URL if commodity else _COT_URL,
                         params=params, timeout=20)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "_body": r.text[:200], "_source": src}
        rows = r.json()
        if not rows:
            return {"error": message("nessun contratto CFTC trovato per '{name}'", "no CFTC contract found for '{name}'", name=name), "_source": src}

        # Commodity: codice CFTC esatto. TFF: conserva selezione storica #177.
        if commodity:
            rows = [x for x in rows if x.get("cftc_contract_market_code") == code]
            if not rows:
                return {"error": message("risposta CFTC senza contratto {code}",
                                         "CFTC response missing contract {code}", code=code), "_source": src}
        else:
            exact = [x for x in rows if (x.get("contract_market_name") or "").upper() == name]
            target = name if exact else (rows[0].get("contract_market_name") or "")
            rows = [x for x in rows
                    if (x.get("contract_market_name") or "").upper() == target.upper()]
        seen_dates = set()
        dedup = []
        for x in rows:
            d = x.get("report_date_as_yyyy_mm_dd", "")[:10]
            if d in seen_dates:
                continue
            seen_dates.add(d)
            dedup.append(x)
        rows = dedup[:int(weeks)]  # taglio a 'weeks' DOPO filtro esatto + dedup (audit/11)
        if not rows:
            return {"error": message("nessuna riga per contratto '{target}'", "no rows for contract '{target}'", target=target), "_source": src}

        latest, prev = rows[0], (rows[1] if len(rows) > 1 else None)
        if commodity:
            nets_now = {label: _cot_net_fields(latest, *keys)
                        for label, keys in _COT_DISAGGREGATED_CATS.items()}
            nets_prev = {label: _cot_net_fields(prev, *keys)
                         for label, keys in _COT_DISAGGREGATED_CATS.items()} if prev else {}
            hist_key = "managed_money"
            hist = [n for n in (_cot_net_fields(x, *_COT_DISAGGREGATED_CATS[hist_key])
                                for x in rows) if n is not None]
        else:
            cats = {"dealer": "dealer", "asset_manager": "asset_mgr", "leveraged_funds": "lev_money"}
            nets_now = {label: _cot_net(latest, pref) for label, pref in cats.items()}
            nets_prev = {label: _cot_net(prev, pref) for label, pref in cats.items()} if prev else {}
            hist_key = "leveraged_funds"
            hist = [n for n in (_cot_net(x, "lev_money") for x in rows) if n is not None]

        # Percentile sullo storico disponibile della categoria pertinente.
        lev_now = nets_now.get(hist_key)
        pctile = None
        if lev_now is not None and len(hist) > 10:
            pctile = round(100.0 * sum(1 for v in hist if v <= lev_now) / len(hist), 0)

        report_date = (latest.get("report_date_as_yyyy_mm_dd") or "")[:10]
        try:
            age_days = (date.today() - date.fromisoformat(report_date)).days
            freshness_status = "FRESH" if 0 <= age_days <= 14 else "STALE"
        except ValueError:
            age_days, freshness_status = None, "UNKNOWN"
        if commodity and freshness_status != "FRESH":
            pctile = None  # non presentare una lettura contrarian come corrente

        return {
            "market_query": market_key,
            "contract_market_name": latest.get("contract_market_name"),
            "cftc_contract_market_code": latest.get("cftc_contract_market_code"),
            "report_date": report_date,
            "freshness": {"status": freshness_status, "age_days": age_days},
            "open_interest": latest.get("open_interest_all"),
            "net_positions": nets_now,
            "wow_change": {k: (nets_now[k] - nets_prev[k])
                           for k in nets_now
                           if nets_now.get(k) is not None and nets_prev.get(k) is not None},
            ("managed_money_net_percentile_1y" if commodity else
             "leveraged_funds_net_percentile_1y"): pctile,
            "reading": (None if pctile is None else
                        (message("ESTREMO LONG (≥90° pct): crowded, rischio squeeze ribassista — contrarian bearish", "EXTREME LONG (≥90th pct): crowded, downside squeeze risk — contrarian bearish") if pctile >= 90 else
                         message("ESTREMO SHORT (≤10° pct): crowded short, fuel per squeeze rialzista — contrarian bullish", "EXTREME SHORT (≤10th pct): crowded short, upside squeeze fuel — contrarian bullish") if pctile <= 10 else
                         message("posizionamento non estremo", "positioning is not extreme"))),
            "n_weeks_history": len(rows),
            "_source": src,
            "_timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": error_text(e), "_source": src}


# ============================================================
# 3) VIX term structure
# ============================================================

@scoped_language
def get_vix_term_structure() -> Dict[str, Any]:
    """VIX9D / VIX / VIX3M / VIX6M: contango = regime calmo (carry per vol seller),
    backwardation = stress (domanda di protezione immediata)."""
    src = "yfinance ^VIX9D/^VIX/^VIX3M/^VIX6M"
    try:
        import yfinance as yf
        out: Dict[str, Any] = {}
        for sym, label in (("^VIX9D", "vix_9d"), ("^VIX", "vix_30d"),
                           ("^VIX3M", "vix_3m"), ("^VIX6M", "vix_6m")):
            try:
                h = yf.Ticker(sym).history(period="5d")
                if h is not None and not h.empty:
                    out[label] = round(float(h["Close"].iloc[-1]), 2)
            except Exception:
                continue
        if "vix_30d" not in out:
            return {"error": message("VIX non scaricabile", "VIX download unavailable"), "_source": src}
        v, v3 = out.get("vix_30d"), out.get("vix_3m")
        ratio = round(v / v3, 3) if (v and v3) else None
        state = (None if ratio is None else
                 (message("CONTANGO (normale): curva ascendente, carry positivo per vol seller", "CONTANGO (normal): upward curve, positive carry for volatility sellers")
                  if ratio < 0.97 else
                  message("BACKWARDATION (stress): domanda di protezione immediata, regime risk-off", "BACKWARDATION (stress): demand for immediate protection, risk-off regime")
                  if ratio > 1.03 else message("FLAT: transizione di regime, attenzione", "FLAT: regime transition, caution")))
        return {**out, "vix_vix3m_ratio": ratio, "term_structure": state,
                "_source": src, "_timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"error": error_text(e), "_source": src}


if __name__ == "__main__":
    import json
    print("=== VIX term structure ===")
    print(json.dumps(get_vix_term_structure(), indent=1))
    print("\n=== COT ES (leveraged funds) ===")
    r = get_cot_positioning("ES")
    print(json.dumps({k: v for k, v in r.items() if k != "_raw"}, indent=1, default=str))
    print("\n=== GEX SPY ===")
    g = compute_gex("SPY")
    print(json.dumps({k: v for k, v in g.items() if k != "top_strikes"}, indent=1))
    if g.get("top_strikes"):
        print("top strikes:", g["top_strikes"][:5])
