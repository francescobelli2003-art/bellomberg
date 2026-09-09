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
import os
from datetime import datetime
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
            return {"error": "POLYGON_API_KEY mancante o non attiva", "_source": src}

        exp = get_option_expirations(ticker)
        if exp.get("error"):
            return {"error": f"expirations: {exp['error']}", "_source": src}
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
            return {"error": f"nessuna expiry entro {days_window} giorni", "_source": src}

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
            return {"error": "chain senza gamma/OI utilizzabili", "_source": src}
        if spot is None:
            ks = sorted(by_strike.keys())
            spot = ks[len(ks) // 2]  # fallback: strike mediano

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
        regime = ("POSITIVO (spot sopra il gamma flip): dealer comprano i dip e "
                  "vendono i rally -> mercato compresso, mean-reversion, vol venduta"
                  if gamma_pos else
                  "NEGATIVO (spot sotto il gamma flip): dealer amplificano i "
                  "movimenti -> accelerazioni, momentum, vol comprata")

        return {
            "ticker": ticker.upper(),
            "spot_est": spot,
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
                             ("sopra il flip (gamma positivo)" if spot > flip
                              else "sotto il flip (gamma negativo)")),
            "regime": regime,
            "note": "OI e put/call ratio calcolati sui contratti con greeks validi "
                    "nello snapshot delayed (i deep-OTM senza quote sono esclusi: "
                    "gamma trascurabile)",
            "top_strikes": profile_top,
            "_source": src,
            "_timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "_source": src}


# ============================================================
# 2) COT — CFTC Commitments of Traders (TFF, gratis)
# ============================================================

_COT_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"  # TFF Futures Only

COT_MARKET_ALIASES = {
    "ES": "E-MINI S&P 500", "SPX": "E-MINI S&P 500", "SP500": "E-MINI S&P 500",
    "NQ": "NASDAQ MINI", "NASDAQ": "NASDAQ MINI",
    "EUR": "EURO FX", "EURUSD": "EURO FX",
    "JPY": "JAPANESE YEN", "GBP": "BRITISH POUND",
    "GOLD": "GOLD", "GC": "GOLD",
    "OIL": "CRUDE OIL", "WTI": "CRUDE OIL", "CL": "CRUDE OIL",
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


def get_cot_positioning(market: str = "ES", weeks: int = 52) -> Dict[str, Any]:
    """Posizionamento CFTC TFF (settimanale, gratis): Dealer / Asset Manager /
    Leveraged Funds net su un future. Include variazione WoW e percentile 1y
    del net Leveraged Funds (estremi = segnale contrarian)."""
    src = "CFTC publicreporting.cftc.gov (TFF futures-only)"
    if not REQ_OK:
        return {"error": "requests non disponibile", "_source": src}
    name = COT_MARKET_ALIASES.get((market or "").upper().strip(), market.upper().strip())
    try:
        params = {
            "$where": f"upper(contract_market_name) like '%{name}%'",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            # audit/11 §4: il limit va preso LARGO — le 52 righe piu' recenti del LIKE
            # includono le varianti (MICRO E-MINI...) e dopo il filtro esatto la storia
            # per il percentile 1y restava troncata. Si taglia a 'weeks' DOPO il dedup.
            "$limit": int(weeks) * 4,
        }
        r = requests.get(_COT_URL, params=params, timeout=20)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "_body": r.text[:200], "_source": src}
        rows = r.json()
        if not rows:
            return {"error": f"nessun mercato TFF matcha '{name}'", "_source": src}

        # Fix #177: il LIKE matcha anche varianti (es. MICRO E-MINI S&P 500).
        # Tieni SOLO lo stesso identico contratto: match esatto se esiste,
        # altrimenti il nome del primo risultato. Poi 1 riga per data.
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
            return {"error": f"nessuna riga per contratto '{target}'", "_source": src}

        latest, prev = rows[0], (rows[1] if len(rows) > 1 else None)
        cats = {"dealer": "dealer", "asset_manager": "asset_mgr", "leveraged_funds": "lev_money"}
        nets_now = {label: _cot_net(latest, pref) for label, pref in cats.items()}
        nets_prev = {label: _cot_net(prev, pref) for label, pref in cats.items()} if prev else {}

        # percentile 1y del net leveraged funds
        lev_hist = [n for n in (_cot_net(x, "lev_money") for x in rows) if n is not None]
        lev_now = nets_now.get("leveraged_funds")
        pctile = None
        if lev_now is not None and len(lev_hist) > 10:
            pctile = round(100.0 * sum(1 for v in lev_hist if v <= lev_now) / len(lev_hist), 0)

        return {
            "market_query": market.upper(),
            "contract_market_name": latest.get("contract_market_name"),
            "report_date": latest.get("report_date_as_yyyy_mm_dd", "")[:10],
            "open_interest": latest.get("open_interest_all"),
            "net_positions": nets_now,
            "wow_change": {k: (nets_now[k] - nets_prev[k])
                           for k in nets_now
                           if nets_now.get(k) is not None and nets_prev.get(k) is not None},
            "leveraged_funds_net_percentile_1y": pctile,
            "reading": (None if pctile is None else
                        ("ESTREMO LONG (>90° pct): crowded, rischio squeeze ribassista — contrarian bearish" if pctile >= 90 else
                         "ESTREMO SHORT (<10° pct): crowded short, fuel per squeeze rialzista — contrarian bullish" if pctile <= 10 else
                         "posizionamento non estremo")),
            "n_weeks_history": len(rows),
            "_source": src,
            "_timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "_source": src}


# ============================================================
# 3) VIX term structure
# ============================================================

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
            return {"error": "VIX non scaricabile", "_source": src}
        v, v3 = out.get("vix_30d"), out.get("vix_3m")
        ratio = round(v / v3, 3) if (v and v3) else None
        state = (None if ratio is None else
                 ("CONTANGO (normale): curva ascendente, carry positivo per vol seller"
                  if ratio < 0.97 else
                  "BACKWARDATION (stress): domanda di protezione immediata, regime risk-off"
                  if ratio > 1.03 else "FLAT: transizione di regime, attenzione"))
        return {**out, "vix_vix3m_ratio": ratio, "term_structure": state,
                "_source": src, "_timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"error": str(e), "_source": src}


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
