"""backfill_realized.py — F6 (pacchetto verita' dei numeri Lotto B, 23/07, audit/20).

Riempe le colonne realized_local/realized_eur (migrazione 5) sui SELL/TRIM
STORICI di trade_history, replay a COSTO MEDIO (stesso metodo #164 di
portfolio_analytics._basis_and_realized_at) con FX STORICO del giorno del trade
(yfinance via portfolio_analytics._build_fx_history — serve rete).

Regole:
- DRY-RUN di default: stampa il piano riga per riga, non scrive nulla.
- --apply: backup del DB in data/backups/ PRIMA di scrivere (sqlite backup API),
  poi UPDATE solo delle righe SELL/TRIM con realized_local IS NULL (idempotente:
  rilanciarlo non tocca righe gia' compilate, ne' i realized scritti live da
  log_trade).
- FX del giorno non disponibile -> realized_eur resta NULL = n.d. DICHIARATO
  (regola 14/07), realized_local si scrive comunque.
- Vendite oltre la qty posseduta (refuso ticker): nessun realized, riga saltata
  e DICHIARATA (coerente con portfolio_analytics).

Uso:  python scripts/backfill_realized.py           (dry-run)
      python scripts/backfill_realized.py --apply
"""
import os
import sqlite3
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", _ROOT)
sys.path.insert(0, _ROOT)
from bellomberg.core.paths import DATA_DIR, SQLITE_PATH
DB_PATH = str(SQLITE_PATH)
BACKUP_DIR = os.path.join(DATA_DIR, "backups")


def log(msg):
    print(f"[BACKFILL_REALIZED] {msg}", flush=True)


def _fx_lookup_factory(currencies, start, end):
    """FX storico per (data, valuta) via portfolio_analytics; fallback DICHIARATO None."""
    from bellomberg.portfolio.portfolio_analytics import _build_fx_history
    import pandas as pd
    # review Lotto B: yf.download ha end ESCLUSIVO (stesso pad di
    # portfolio_analytics:464) — senza +1g l'ultimo sell date prendeva
    # zitto l'FX del giorno prima.
    end_padded = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    fx = _build_fx_history(sorted(set(currencies)), start, end_padded)

    def lookup(d_iso, ccy):
        ccy = ccy.upper()
        if ccy == "EUR":
            return 1.0
        if fx.empty or ccy not in fx.columns:
            return None  # n.d. dichiarato: MAI un cambio inventato
        try:
            sub = fx[ccy].dropna()
            older = sub[sub.index <= pd.Timestamp(d_iso)]
            if len(older):
                return float(older.iloc[-1])
            # stesso fix di portfolio_analytics.fx_lookup (F-CONT-1): data prima
            # della prima oss (trade di apertura di domenica) -> prima oss disponibile
            newer = sub[sub.index > pd.Timestamp(d_iso)]
            if len(newer):
                return float(newer.iloc[0])
        except Exception:
            pass
        return None
    return lookup


def main():
    apply = "--apply" in sys.argv
    if not os.path.exists(DB_PATH):
        log(f"ERRORE: DB non trovato: {DB_PATH}")
        sys.exit(1)

    # hardening #32: mai connect raw sui DB del progetto (WAL/busy_timeout/FK)
    from bellomberg.storage.memory_db import connect_sqlite
    conn = connect_sqlite(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_history)")}
    if "realized_local" not in cols:
        log("ERRORE: colonna realized_local assente — la migrazione 5 non e' ancora "
            "applicata su questo DB (riavvia il backend o istanzia MemoryDB una volta).")
        sys.exit(1)

    trades = [dict(r) for r in conn.execute(
        "SELECT id, ticker, action, quantita, prezzo, valuta, data, "
        "realized_local FROM trade_history ORDER BY data, id")]
    log(f"{len(trades)} trade in storia")

    sell_dates = [(t["data"] or "")[:10] for t in trades
                  if (t["action"] or "").upper() in ("SELL", "TRIM")]
    if not sell_dates:
        log("nessun SELL/TRIM in storia: niente da fare")
        return
    currencies = {(t["valuta"] or "EUR").upper() for t in trades}
    fx_lookup = _fx_lookup_factory(currencies, min(sell_dates), max(sell_dates))

    # replay average cost per ticker (metodo #164)
    holdings = {}
    plan = []       # (id, realized_local, realized_eur, descr)
    skipped = []
    for t in trades:
        action = (t["action"] or "BUY").upper()
        if action == "DIVIDEND":
            continue
        tk = t["ticker"] or "?"
        qty = float(t["quantita"] or 0)
        px = float(t["prezzo"] or 0)
        ccy = (t["valuta"] or "EUR").upper()
        d = (t["data"] or "")[:10]
        h = holdings.setdefault(tk, {"qty": 0.0, "avg": 0.0})
        if action in ("BUY", "ADD"):
            new_q = h["qty"] + qty
            if new_q > 0:
                h["avg"] = (h["qty"] * h["avg"] + qty * px) / new_q
            h["qty"] = new_q
        elif action in ("SELL", "TRIM"):
            if h["qty"] <= 0:
                skipped.append(f"id {t['id']} {tk} {d}: vendita senza posizione "
                               "(refuso?) — nessun realized, DICHIARATO")
                continue
            sold = min(qty, h["qty"])
            realized_local = round(sold * (px - h["avg"]), 6)
            h["qty"] -= sold
            fxr = fx_lookup(d, ccy)
            realized_eur = round(realized_local * fxr, 6) if fxr else None
            if t["realized_local"] is not None:
                skipped.append(f"id {t['id']} {tk} {d}: realized gia' presente "
                               f"({t['realized_local']}), non tocco (idempotenza)")
                continue
            plan.append((t["id"], realized_local, realized_eur,
                         f"id {t['id']} {tk} {action} {sold:g} @ {px:g} {ccy} {d} -> "
                         f"realized_local {realized_local:+.2f} {ccy}, realized_eur "
                         + (f"{realized_eur:+.2f}" if realized_eur is not None
                            else "n.d. (FX storico non disponibile)")))

    log(f"piano: {len(plan)} righe da compilare, {len(skipped)} saltate")
    for _, _, _, descr in plan:
        log("  " + descr)
    for s in skipped:
        log("  SKIP " + s)

    if not apply:
        log("DRY-RUN: nessuna scrittura. Rilancia con --apply per applicare.")
        conn.close()
        return

    # backup PRIMA di scrivere (sqlite backup API, sicura anche con backend vivo)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bpath = os.path.join(BACKUP_DIR, f"consigliere_backup_{ts}_pre_backfill_realized.db")
    dst = sqlite3.connect(bpath)
    conn.backup(dst)
    dst.close()
    log(f"backup: {bpath}")

    for tid, rl, re_, _ in plan:
        conn.execute("UPDATE trade_history SET realized_local=?, realized_eur=? WHERE id=?",
                     (rl, re_, tid))
    conn.commit()
    log(f"APPLICATO: {len(plan)} righe aggiornate")
    conn.close()


if __name__ == "__main__":
    main()
