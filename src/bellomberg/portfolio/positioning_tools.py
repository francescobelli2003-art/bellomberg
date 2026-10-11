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
# 10/10 (Opus 5.5): soglie del rapporto VIX/VIX3M dalla fonte unica (le stesse dello score)
from bellomberg.core import soglie_score as _soglie
import math
import os
from datetime import datetime, date, timedelta, timezone
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

# Spot del GEX (integrazione 10/10, Opus 5.5): UNA sola regola con la superficie, in
# bellomberg.market_data.spot_alignment.resolve_spot (prezzo Polygon -> barra yfinance 1m
# allineata all'ora delle quote entro tolleranza, con la finestra 16:00-16:15 ET -> lastPrice
# NON qualificato -> None, e qui il proxy-strike R02). La funzione locale della barra
# (27ee494/29570a8) era un duplicato con una regola diversa (solo barre precedenti): rimossa.


@scoped_language
def compute_gex(ticker: str, max_expiries: int = 5,
                days_window: int = 45, min_days: int = 2) -> Dict[str, Any]:
    """Dealer Gamma Exposure aggregata sulle expiry fra min_days e days_window giorni.

    GEX per contratto = gamma * OI * moltiplicatore * spot^2 * 0.01
    (dollar-gamma per movimento dell'1% dello spot); moltiplicatore = shares_per_contract
    del contratto se il fornitore lo dà, altrimenti 100 (standard OCC) DICHIARATO.
    Convenzione: dealer LONG call gamma (+), SHORT put gamma (-).

    Fix 09/10 (Opus 5.5, audit SCORE-VOL-QUANT §3.1 + VOLDECK M4/M5):
    - regime = segno del GEX netto allo spot (mai piu' «NEGATIVO» con GEX netto positivo);
    - gamma flip = ZERO-GAMMA vero: radice del profilo GEX(S') ricalcolato su una griglia di
      spot ipotetici con il gamma di Black-Scholes di ciascun contratto (sua IV, r=q=0), non
      il cambio di segno della somma cumulata per strike (che dipendeva dalle code);
    - scadenze >= min_days (niente 0DTE/1DTE, come vol_surface) e le MENSILI (terzo venerdi')
      entro la finestra sempre incluse: con le giornaliere di SPY le prime 5 erano una settimana;
    - una scadenza in errore NON resta in expiries_used: va in expiries_failed col motivo e
      il payload e' PARZIALE dichiarato; idem una chain troncata o parziale (coverage).
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
        in_window: List[str] = []
        for e in exp.get("expirations", []):
            try:
                d = datetime.strptime(e, "%Y-%m-%d").date()
            except Exception:
                continue
            if min_days <= (d - today).days <= days_window:
                in_window.append(e)
        in_window = sorted(set(in_window))
        if not in_window:
            return {"error": message("nessuna expiry fra {lo} e {days} giorni", "no expiry between {lo} and {days} days",
                                     lo=min_days, days=days_window), "_source": src}

        def _terzo_venerdi(s):
            d = datetime.strptime(s, "%Y-%m-%d").date()
            return d.weekday() == 4 and 15 <= d.day <= 21
        chosen = in_window[:max_expiries]
        mensili_aggiunte = [e for e in in_window if _terzo_venerdi(e) and e not in chosen]
        chosen = sorted(chosen + mensili_aggiunte)

        expiries_used: List[str] = []
        expiries_failed: List[Dict[str, Any]] = []
        expiries_partial: List[Dict[str, Any]] = []
        contracts: List[Dict[str, Any]] = []
        spot_rows: List[Dict[str, Any]] = []
        moltiplicatore_dichiarato = 0
        for e in chosen:
            ch = get_options_chain(ticker, e, max_contracts=1200)
            if ch.get("error"):
                expiries_failed.append({"expiry": e, "error": str(ch.get("error"))[:120]})
                continue
            expiries_used.append(e)
            cov = ch.get("coverage") or {}
            if cov.get("status") not in (None, "COMPLETE"):
                expiries_partial.append({"expiry": e, "status": cov.get("status"),
                                         "issues": list(cov.get("issues") or []) + list(cov.get("errors") or [])})
            dte = max((datetime.strptime(e, "%Y-%m-%d").date() - today).days, 1)
            for c in ch.get("chain", []):
                spot_rows.append(c)
                gamma = c.get("gamma")
                oi = c.get("oi") or 0
                strike = c.get("strike")
                ctype = (c.get("type") or "").lower()
                if gamma is None or not oi or not strike or ctype not in ("call", "put"):
                    continue
                mult = c.get("shares_per_contract")
                if not isinstance(mult, (int, float)) or isinstance(mult, bool) or mult <= 0:
                    mult = 100.0
                    moltiplicatore_dichiarato += 1
                iv = c.get("iv")
                contracts.append({"strike": float(strike), "type": ctype, "gamma": float(gamma),
                                  "oi": float(oi), "mult": float(mult), "t": dte / 365.0,
                                  "iv": float(iv) if isinstance(iv, (int, float)) and not isinstance(iv, bool)
                                  and math.isfinite(iv) and iv > 0 else None,
                                  "delta": c.get("delta"), "dte": dte})

        if not expiries_used:
            return {"error": message("nessuna scadenza leggibile ({n} in errore)", "no readable expiry ({n} failed)",
                                     n=len(expiries_failed)),
                    "expiries_failed": expiries_failed, "_source": src}
        if not contracts:
            return {"error": message("chain senza gamma/OI utilizzabili", "chain has no usable gamma/OI"),
                    "expiries_used": expiries_used, "expiries_failed": expiries_failed, "_source": src}

        # SPOT: regola UNICA con la superficie (spot_alignment.resolve_spot). 1) quotazione del
        # sottostante dallo snapshot, solo se coerente (R02: unica, ticker giusto, timestamp
        # valido); 2) barra yfinance 1m allineata all'ora delle quote (ancora robusta
        # quote_anchor, come la superficie), finestra 16:00-16:15 ET compresa; 3) lastPrice NON
        # qualificato; 4) proxy-strike DICHIARATO piu' sotto.
        from bellomberg.market_data import spot_alignment as _sa
        spot = None
        spot_method = None
        spot_asof = None
        underlying = None
        try:
            from bellomberg.market_data.polygon_data import _summary_spot_observation
            obs = _summary_spot_observation(ticker, spot_rows)
            if obs.get("spot_observation") and obs["spot_observation"].get("price"):
                underlying = {"price": obs["spot_observation"]["price"], "timestamp": obs.get("spot_asof"),
                              "timeframe": obs["spot_observation"].get("timeframe")}
        except Exception:
            underlying = None
        stamps = []
        for c in spot_rows:
            iso = c.get("quote_timestamp")
            ns = c.get("quote_timestamp_ns")
            if iso:
                stamps.append(iso)
            elif isinstance(ns, (int, float)) and not isinstance(ns, bool) and ns > 0:
                try:
                    stamps.append(datetime.fromtimestamp(ns / 1e9, timezone.utc))
                except (OverflowError, OSError, ValueError):
                    pass
        anchor = _sa.quote_anchor(stamps) if stamps else None
        spot_quote_time = anchor
        res = _sa.resolve_spot(ticker, underlying=underlying, anchor=anchor)
        spot_alignment = res.get("spot_alignment")
        spot_bar_note = None
        if res.get("spot") is not None:
            spot = float(res["spot"])
            spot_asof = res.get("spot_timestamp")
            if res["spot_source"] == _sa.SOURCE_POLYGON:
                spot_method = "underlying_quote"
            elif res["spot_source"] == _sa.SOURCE_BAR:
                spot_method = ("yfinance_1m_bar_close" if (spot_alignment or {}).get("session_close")
                               else "yfinance_1m_bar")
            else:
                spot_method = "yfinance_last_price"
        if spot_method not in ("underlying_quote", "yfinance_1m_bar", "yfinance_1m_bar_close"):
            if anchor is None:
                # riserva 9: senza l'ora della quota (last_quote.last_updated assente nella chain,
                # possibile sul piano Polygon del PM) la barra NON si attiva: lo si dichiara
                spot_bar_note = message("barra a 1 minuto non tentata: nessuna ora della quota nella chain (last_quote.last_updated assente)",
                                        "1-minute bar not attempted: no quote time in the chain (last_quote.last_updated missing)")
            else:
                spot_bar_note = message("barra yfinance a 1 minuto non allineata all'ora delle quote ({q}): {r}",
                                        "1-minute yfinance bar not aligned to the quote time ({q}): {r}",
                                        q=anchor.isoformat(), r=(spot_alignment or {}).get("reason") or "n.d.")
        if spot is None:
            # call con delta piu' vicino a 0,5 sulla scadenza PIU' VICINA letta (prima: l'ultima
            # call con |delta-0,5|<0,06 di QUALSIASI scadenza, anche la piu' lontana)
            near = min(c["dte"] for c in contracts)
            atm = [c for c in contracts if c["type"] == "call" and c["dte"] == near
                   and isinstance(c.get("delta"), (int, float)) and abs(c["delta"] - 0.5) < 0.06]
            if atm:
                spot = min(atm, key=lambda c: abs(c["delta"] - 0.5))["strike"]
                spot_method = "atm_call_strike"
            else:
                ks = sorted({c["strike"] for c in contracts})
                spot = ks[len(ks) // 2]
                spot_method = "median_strike"
        spot_note = {
            "yfinance_1m_bar": message("Spot: chiusura della barra yfinance a 1 minuto allineata all'ora delle quote della chain (entro {t} s).",
                                       "Spot: close of the 1-minute yfinance bar aligned to the chain quote time (within {t} s).", t=_sa.ALIGN_TOLERANCE_SECONDS),
            "yfinance_1m_bar_close": message("Spot: chiusura dell'ultima barra yfinance a 1 minuto della seduta (16:00 ET): quote delle opzioni fra le 16:00 e le 16:15 ET, quando le opzioni su ETF d'indice come SPY trattano ancora e l'azionario e' chiuso.",
                                             "Spot: close of the session's last 1-minute yfinance bar (16:00 ET): option quotes between 16:00 and 16:15 ET, when index-ETF options such as SPY still trade and the stock market is closed."),
            "yfinance_last_price": message("Spot NON qualificato: yfinance lastPrice letto ora, istante diverso dalle quote della chain (ripiego dichiarato di spot_alignment).",
                                           "Spot NOT qualified: yfinance lastPrice read now, a different instant from the chain quotes (declared spot_alignment fallback)."),
            "underlying_quote": message("Spot: quotazione del sottostante riportata da Polygon nello snapshot opzioni (valuta non attestata dal fornitore).",
                                        "Spot: underlying quote reported by Polygon in the options snapshot (currency not attested by the provider)."),
            "atm_call_strike": message("Spot proxy: strike della call con delta più vicino a 0,5 sulla scadenza più vicina; non è una quotazione del sottostante.",
                                       "Spot proxy: call strike with delta closest to 0.5 on the nearest expiry; this is not an underlying quote."),
            "median_strike": message("Spot proxy: strike mediano, non essendoci una call con delta vicino a 0,5; non è una quotazione del sottostante.",
                                     "Spot proxy: median strike because no call has delta near 0.5; this is not an underlying quote."),
        }[spot_method]

        # R02-b: etichetta macchina dello spot. QUALIFICATO solo la quotazione del sottostante
        # (unica, coerente, timestamp valido) letta dallo snapshot; gli strike sono proxy.
        if spot_bar_note and spot_method in ("atm_call_strike", "median_strike", "yfinance_last_price"):
            spot_note = spot_note + " " + spot_bar_note[0].upper() + spot_bar_note[1:] + "."
        spot_qualified = spot_method in ("underlying_quote", "yfinance_1m_bar", "yfinance_1m_bar_close")
        spot_source = {"underlying_quote": "polygon_underlying_quote", "yfinance_1m_bar": "yfinance_1m_bar_aligned",
                       "yfinance_1m_bar_close": "yfinance_1m_bar_session_close",
                       "yfinance_last_price": "yfinance_last_price_unqualified",
                       "atm_call_strike": "proxy_strike_delta50",
                       "median_strike": "proxy_strike_median"}[spot_method]

        # GEX allo spot con il gamma del FORNITORE (definizione per contratto invariata)
        by_strike: Dict[float, Dict[str, float]] = {}
        tot_call_gex = tot_put_gex = 0.0
        tot_call_oi = tot_put_oi = 0
        for c in contracts:
            g = c["gamma"] * c["oi"] * c["mult"] * spot * spot * 0.01
            rec = by_strike.setdefault(c["strike"], {"net": 0.0, "call_oi": 0, "put_oi": 0})
            if c["type"] == "call":
                tot_call_gex += g; rec["net"] += g; rec["call_oi"] += int(c["oi"]); tot_call_oi += int(c["oi"])
            else:
                tot_put_gex -= g; rec["net"] -= g; rec["put_oi"] += int(c["oi"]); tot_put_oi += int(c["oi"])
        net_gex = tot_call_gex + tot_put_gex
        profile = [{"strike": k, "net_gex_usd": round(v["net"], 0), "call_oi": v["call_oi"], "put_oi": v["put_oi"]}
                   for k, v in sorted(by_strike.items())]

        # PROFILO GEX(S') con gamma di Black-Scholes (r=q=0, IV del contratto, ACT/365)
        def _bs_gamma(S, K, sig, T):
            v = sig * math.sqrt(T)
            if v <= 0 or S <= 0 or K <= 0:
                return 0.0
            d1 = (math.log(S / K) + 0.5 * v * v) / v
            return math.exp(-0.5 * d1 * d1) / (math.sqrt(2 * math.pi) * S * v)
        prezzabili = [c for c in contracts if c["iv"] is not None]
        peso_tot = sum(abs(c["gamma"] * c["oi"] * c["mult"]) for c in contracts) or 1.0
        peso_prezz = sum(abs(c["gamma"] * c["oi"] * c["mult"]) for c in prezzabili)
        profile_coverage_pct = round(100.0 * peso_prezz / peso_tot, 1)

        def _gex_at(S):
            tot = 0.0
            for c in prezzabili:
                g = _bs_gamma(S, c["strike"], c["iv"], c["t"]) * c["oi"] * c["mult"] * S * S * 0.01
                tot += g if c["type"] == "call" else -g
            return tot
        flip = None
        flip_note = None
        radici_tutte: List[float] = []
        GRID_PCT, GRID_N = 0.15, 121   # spot +-15%, passo 25 bp (121 punti)
        if prezzabili:
            griglia = [spot * (1 - GRID_PCT + 2 * GRID_PCT * i / (GRID_N - 1)) for i in range(GRID_N)]
            valori = [_gex_at(S) for S in griglia]
            radici = []
            for (s0, v0), (s1, v1) in zip(zip(griglia, valori), zip(griglia[1:], valori[1:])):
                if v0 == 0:
                    radici.append(s0)
                elif (v0 < 0 < v1) or (v0 > 0 > v1):
                    radici.append(s0 + (s1 - s0) * (-v0) / (v1 - v0))   # interpolazione lineare
            if radici:
                # riserva 8: con piu' radici sulla griglia si DICHIARANO tutte (gamma_flip_roots)
                # e il criterio: lo zero-gamma e' la radice piu' VICINA allo spot (il cambio di
                # regime che il prezzo incontra per primo), non la prima della griglia
                radici_tutte = sorted(round(r, 2) for r in radici)
                flip = round(min(radici, key=lambda r: abs(r - spot)), 2)
            else:
                flip_note = message("nessuno zero-gamma entro ±15% dallo spot: GEX di segno costante sulla griglia",
                                    "no zero-gamma within ±15% of spot: GEX keeps one sign across the grid")
        else:
            flip_note = message("nessun contratto con IV valida: profilo zero-gamma non calcolabile",
                                "no contract with a valid IV: zero-gamma profile cannot be computed")

        profile_top = sorted(profile, key=lambda r: abs(r["net_gex_usd"]), reverse=True)[:12]
        profile_top.sort(key=lambda r: r["strike"])

        # Regime = SEGNO del GEX netto allo spot (coerente col numero stampato accanto)
        gamma_pos = net_gex > 0
        regime_basis = message("GEX netto allo spot {segno}", "net GEX at spot {segno}",
                               segno=("> 0" if gamma_pos else "<= 0"))
        regime = (message("POSITIVO ({basis}): dealer comprano i dip e vendono i rally -> mercato compresso, mean-reversion, vol venduta",
                          "POSITIVE ({basis}): dealers buy dips and sell rallies -> compressed market, mean reversion, volatility sold", basis=regime_basis)
                  if gamma_pos else message("NEGATIVO ({basis}): dealer amplificano i movimenti -> accelerazioni, momentum, vol comprata",
                                             "NEGATIVE ({basis}): dealers amplify moves -> acceleration, momentum, volatility bought", basis=regime_basis))

        parziale = bool(expiries_failed or expiries_partial)
        coverage = {"status": "PARTIAL" if parziale else "COMPLETE",
                    "expiries_requested": len(chosen), "expiries_read": len(expiries_used),
                    "expiries_failed": len(expiries_failed), "expiries_partial": len(expiries_partial)}
        out = {
            "ticker": ticker.upper(),
            "spot_est": spot,
            "spot_method": spot_method,
            "spot_note": spot_note,
            "spot_source": spot_source,
            "spot_qualified": spot_qualified,
            "spot_asof": spot_asof,
            "spot_quote_time": spot_quote_time.isoformat() if spot_quote_time else None,
            "spot_alignment": spot_alignment,
            "expiries_used": expiries_used,
            "expiries_failed": expiries_failed,
            "expiries_partial": expiries_partial,
            "monthly_expiries_added": mensili_aggiunte,
            "expiry_rule": message("scadenze fra {lo} e {hi} giorni: le prime {n} più le mensili (terzo venerdì) della finestra",
                                   "expiries between {lo} and {hi} days: the first {n} plus the monthly ones (third Friday) in the window",
                                   lo=min_days, hi=days_window, n=max_expiries),
            "coverage": coverage,
            "partial": parziale,
            "n_contracts": len(contracts),
            "net_gex_usd_per_1pct": round(net_gex, 0),
            "call_gex_usd": round(tot_call_gex, 0),
            "put_gex_usd": round(tot_put_gex, 0),
            "total_call_oi": tot_call_oi,
            "total_put_oi": tot_put_oi,
            "put_call_oi_ratio": round(tot_put_oi / tot_call_oi, 3) if tot_call_oi else None,
            "gamma_flip_strike": flip,
            "gamma_flip_method": message("zero-gamma: radice di GEX(S') su griglia ±15% (passo 25 bp), gamma Black-Scholes con IV del contratto, r=q=0",
                                         "zero-gamma: root of GEX(S') on a ±15% grid (25 bp step), Black-Scholes gamma with each contract's IV, r=q=0"),
            "gamma_flip_note": flip_note,
            "gamma_flip_roots": radici_tutte,
            "gamma_flip_choice": (None if flip is None else
                                  message("radice piu' vicina allo spot ({n} radici sulla griglia)",
                                          "root closest to spot ({n} roots on the grid)", n=len(radici_tutte))),
            "spot_bar_note": spot_bar_note,
            "gamma_profile_coverage_pct": profile_coverage_pct,
            "multiplier_note": (message("moltiplicatore 100 (standard OCC) assunto per {n} contratti senza shares_per_contract",
                                        "multiplier 100 (OCC standard) assumed for {n} contracts without shares_per_contract",
                                        n=moltiplicatore_dichiarato) if moltiplicatore_dichiarato else None),
            # con uno spot PROXY (strike) la posizione rispetto allo zero-gamma non e' un fatto
            # di mercato: «non valutato» dichiarato (residuo R02). Il regime NON ne dipende:
            # segue il segno del GEX netto, che non dipende dallo spot (fattore S^2 > 0).
            "spot_vs_flip": (None if flip is None else
                             message("non valutato: spot proxy ({s}), non una quotazione", "not evaluated: proxy spot ({s}), not a quote", s=spot_source)
                             if not spot_qualified else
                             (message("spot sopra lo zero-gamma", "spot above zero-gamma") if spot > flip
                              else message("spot sotto lo zero-gamma", "spot below zero-gamma"))),
            "regime": regime,
            "note": message("OI e put/call ratio calcolati sui contratti con greeks validi "
                    "nello snapshot delayed (i deep-OTM senza quote sono esclusi: "
                    "gamma trascurabile)", "OI and put/call ratio use contracts with valid greeks in the delayed snapshot (deep-OTM contracts without quotes are excluded: negligible gamma)"),
            "top_strikes": profile_top,
            "_source": src,
            "_timestamp": datetime.now().isoformat(),
        }
        if parziale:
            out["partial_note"] = message("GEX PARZIALE: {f} scadenze in errore e {p} chain incomplete su {n} richieste; il numero non copre tutta la finestra",
                                          "PARTIAL GEX: {f} expiries failed and {p} incomplete chains out of {n} requested; the number does not cover the whole window",
                                          f=len(expiries_failed), p=len(expiries_partial), n=len(chosen))
        return out
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

        # Commodity: codice CFTC esatto. TFF: nome esatto, mai prima riga del LIKE.
        if commodity:
            rows = [x for x in rows if x.get("cftc_contract_market_code") == code]
            if not rows:
                return {"error": message("risposta CFTC senza contratto {code}",
                                         "CFTC response missing contract {code}", code=code), "_source": src}
        else:
            target = name
            rows = [x for x in rows
                    if (x.get("contract_market_name") or "").upper() == target.upper()]
            codes = {x.get("cftc_contract_market_code") for x in rows
                     if x.get("cftc_contract_market_code")}
            if len(codes) > 1:
                return {"error": message("identita' CFTC ambigua per '{name}': piu' codici contratto",
                                         "ambiguous CFTC identity for '{name}': multiple contract codes", name=name),
                        "_source": src}
        seen_dates = set()
        dedup = []
        for x in rows:
            d = (x.get("report_date_as_yyyy_mm_dd") or "")[:10]
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
        if freshness_status != "FRESH":
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
        asof: Dict[str, Any] = {}   # fix 09/10: data della chiusura di ogni serie (freschezza dichiarata)
        for sym, label in (("^VIX9D", "vix_9d"), ("^VIX", "vix_30d"),
                           ("^VIX3M", "vix_3m"), ("^VIX6M", "vix_6m")):
            try:
                h = yf.Ticker(sym).history(period="5d")
                if h is not None and not h.empty:
                    out[label] = round(float(h["Close"].iloc[-1]), 2)
                    asof[label] = str(h.index[-1])[:10]
            except Exception:
                continue
        if "vix_30d" not in out:
            return {"error": message("VIX non scaricabile", "VIX download unavailable"), "_source": src}
        v, v3 = out.get("vix_30d"), out.get("vix_3m")
        ratio = round(v / v3, 3) if (v and v3) else None
        # 10/10 (Opus 5.5): UNA taratura sola con options_score. Prima qui 0,97/1,03 e nello
        # score 0,88-1,06: con 0,99 lo strumento diceva FLAT e lo score 3,7/6. Ora il codice
        # viene da core/soglie_score.struttura_vix: CONTANGO sotto il p75 storico (ancora del 2
        # dello score), FLAT fra il p75 e 1,00, BACKWARDATION da 1,00 (inversione).
        codice = _soglie.struttura_vix(ratio)
        state = {"CONTANGO": message("CONTANGO (normale): curva ascendente, carry positivo per vol seller", "CONTANGO (normal): upward curve, positive carry for volatility sellers"),
                 "FLAT": message("FLAT: contango sotto la norma (rapporto sopra il 75° percentile storico), curva verso l'inversione", "FLAT: below-normal contango (ratio above its historical 75th percentile), curve moving toward inversion"),
                 "BACKWARDATION": message("BACKWARDATION (stress): domanda di protezione immediata, regime risk-off", "BACKWARDATION (stress): demand for immediate protection, risk-off regime"),
                 }.get(codice)
        return {**out, "vix_vix3m_ratio": ratio, "term_structure": state, "term_structure_code": codice,
                "term_structure_thresholds": {"contango_below": _soglie.VIX3M_ANCORE[1],
                                               "backwardation_from": _soglie.VIX3M_INVERSIONE},
                "vix_asof": asof,
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
