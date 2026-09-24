"""Observe stored price transitions; enqueue quotes without changing model inputs."""
from hashlib import sha256
from datetime import datetime
import json
import math


def reconcile_price_events(manager):
    state = manager.runtime.status()
    if state["status"] == "disabled" or "price" not in state["triggers"]:
        return {"status": "disabled", "reason": "price_trigger_not_authorized", "outcomes": []}
    outcomes = []
    try:
        with manager.jobs._connect(read_only=True) as conn:
            tickers = [r[0] for r in conn.execute("""SELECT ticker FROM valuation_model_heads
                WHERE current_generation IS NOT NULL AND ticker IN (
                    SELECT ticker FROM positions WHERE quantita>0 AND is_active=1
                    UNION SELECT ticker FROM favorite_companies) ORDER BY ticker""")]
            observations = []
            for ticker in tickers:
                row = conn.execute("""SELECT id,prezzo,valuta,source,timestamp FROM position_prices
                    WHERE ticker=? ORDER BY id DESC LIMIT 1""", (ticker,)).fetchone()
                if row is None:
                    outcomes.append({"ticker": ticker, "status": "unavailable", "reason": "stored_price_absent"})
                    continue
                if (not isinstance(row["prezzo"], (float, int)) or not math.isfinite(row["prezzo"])
                        or row["prezzo"] <= 0 or not row["valuta"] or not row["source"]):
                    outcomes.append({"ticker": ticker, "status": "unavailable", "reason": "stored_price_invalid"})
                    continue
                # Repeated polls write repeated rows. Identify the beginning of
                # this price/currency/source transition: A->B->A is a new event,
                # whereas A->A is not. The observation date renews freshness.
                different = conn.execute("""SELECT MAX(id) FROM position_prices WHERE ticker=? AND
                    (prezzo IS NOT ? OR valuta IS NOT ? OR source IS NOT ?)""",
                    (ticker, row["prezzo"], row["valuta"], row["source"])).fetchone()[0]
                transition = conn.execute("SELECT MIN(id) FROM position_prices WHERE ticker=? AND id>?",
                                          (ticker, different if different is not None else -1)).fetchone()[0]
                observed_day = datetime.fromisoformat(row["timestamp"]).date().isoformat()
                identity = {"transition_id": transition, "price": row["prezzo"], "currency": row["valuta"],
                            "source": row["source"], "observed_at": observed_day}
                key = sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
                observations.append((ticker, "stored-price-v1:" + key))
        for ticker, key in observations:
            try:
                row = manager.enqueue_price(ticker, key)
                outcomes.append({"ticker": ticker, **{k: row[k] for k in ("id", "status", "reused", "reason") if k in row}})
            except Exception as exc:
                outcomes.append({"ticker": ticker, "status": "error", "reason": type(exc).__name__ + ": " + str(exc)[:500]})
        return {"status": "partial" if any(row["status"] in ("error", "unavailable") for row in outcomes) else "ok",
                "outcomes": outcomes}
    except Exception as exc:
        return {"status": "error", "reason": type(exc).__name__ + ": " + str(exc)[:500], "outcomes": outcomes}
