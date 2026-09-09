"""
consensus_estimates.py — CONSENSUS DEGLI ANALISTI (Fase 5, 15/07, richiesta PM).

Attese di mercato per un ticker via yfinance (fonte dichiarata, gratis):
  - stime EPS e RICAVI (trimestre corrente/prossimo, anno corrente/prossimo)
    con avg/low/high, numero analisti e growth implicito
  - REVISIONI delle stime EPS (oggi vs 7/30/60/90 giorni fa): il momentum
    delle stime, spesso piu' informativo del livello
  - target price (mean/median/high/low) + upside implicito vs prezzo corrente
  - mix raccomandazioni (strongBuy..strongSell) e trend vs 3 mesi fa

Regola PM "no fallback silenziosi": ogni blocco mancante e' dichiarato
("n.d. (motivo)"), mai omesso in silenzio. Cache in-memory 1h per ticker
(le stime non si muovono intraday).
"""
import time

_CACHE = {}   # {ticker: (ts, payload)}
_TTL_S = 3600


def _df_rows(df, cols):
    """DataFrame yfinance -> lista di dict {period, <cols>} (period e' l'indice)."""
    out = []
    for period, row in df.iterrows():
        d = {"period": str(period)}
        for c in cols:
            v = row.get(c)
            try:
                d[c] = None if v is None else round(float(v), 4)
            except Exception:
                d[c] = v
        out.append(d)
    return out


def get_consensus(ticker: str) -> dict:
    """Consensus completo per un ticker. Non solleva: errori dichiarati nel payload."""
    tkr = (ticker or "").upper().strip()
    if not tkr:
        return {"error": "ticker mancante"}
    hit = _CACHE.get(tkr)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]

    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance non disponibile"}
    tk = yf.Ticker(tkr)
    out = {"ticker": tkr, "source": "yfinance (consensus Yahoo Finance)"}

    # --- target price + upside implicito ---
    try:
        pt = tk.analyst_price_targets or {}
        cur, mean = pt.get("current"), pt.get("mean")
        out["price_targets"] = {
            "current_price": cur, "mean": mean, "median": pt.get("median"),
            "high": pt.get("high"), "low": pt.get("low"),
            "implied_upside_pct": (round((mean / cur - 1) * 100, 1)
                                   if cur and mean else None),
        }
    except Exception as e:
        out["price_targets"] = f"n.d. ({type(e).__name__}: {str(e)[:80]})"

    # --- stime EPS e ricavi ---
    for attr, key, cols in (
            ("earnings_estimate", "eps_estimates",
             ["avg", "low", "high", "yearAgoEps", "numberOfAnalysts", "growth"]),
            ("revenue_estimate", "revenue_estimates",
             ["avg", "low", "high", "yearAgoRevenue", "numberOfAnalysts", "growth"])):
        try:
            df = getattr(tk, attr)
            if df is None or df.empty:
                out[key] = "n.d. (Yahoo non copre le stime per questo ticker)"
            else:
                out[key] = _df_rows(df, cols)
        except Exception as e:
            out[key] = f"n.d. ({type(e).__name__}: {str(e)[:80]})"

    # --- revisioni EPS: momentum delle stime (current vs 30/90 giorni fa) ---
    try:
        df = tk.eps_trend
        if df is None or df.empty:
            out["eps_revisions"] = "n.d. (eps_trend vuoto)"
        else:
            revs = []
            for period, row in df.iterrows():
                cur = row.get("current")
                d = {"period": str(period), "current": cur}
                for horizon in ("30daysAgo", "90daysAgo"):
                    prev = row.get(horizon)
                    d["chg_vs_" + horizon.replace("daysAgo", "d") + "_pct"] = (
                        round((cur / prev - 1) * 100, 2) if cur and prev else None)
                revs.append(d)
            out["eps_revisions"] = revs
            out["eps_revisions_note"] = ("revisioni POSITIVE = analisti che alzano le stime "
                                         "(momentum favorevole); negative = tagli in corso")
    except Exception as e:
        out["eps_revisions"] = f"n.d. ({type(e).__name__}: {str(e)[:80]})"

    # --- raccomandazioni: mix corrente + trend vs 3 mesi fa ---
    try:
        df = tk.recommendations_summary
        if df is None or df.empty:
            out["recommendations"] = "n.d. (nessuna copertura)"
        else:
            rows = _df_rows(df, ["strongBuy", "buy", "hold", "sell", "strongSell"])
            # yfinance etichetta i period come '0m'/'-3m' oppure '0'/'3' a seconda
            # della versione: accetta entrambi
            now = next((r for r in rows if r["period"] in ("0m", "0")), rows[0])
            m3 = next((r for r in rows if r["period"] in ("-3m", "3")), None)
            out["recommendations"] = {"current_month": now, "three_months_ago": m3}
    except Exception as e:
        out["recommendations"] = f"n.d. ({type(e).__name__}: {str(e)[:80]})"

    _CACHE[tkr] = (time.time(), out)
    return out


if __name__ == "__main__":
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(get_consensus(sys.argv[1] if len(sys.argv) > 1 else "BSX"),
                     indent=2, ensure_ascii=False, default=str))
