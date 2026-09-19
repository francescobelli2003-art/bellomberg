"""
BELLOMBERG - TWR Engine (motore contabile da fondo, audit 02 par.3 / fix #30)

Problema risolto (denuncia del PM): "se levo o aggiungo soldi non puo' essere
considerato drawdown o profit". Questo modulo separa FLUSSI ESTERNI di capitale
dal RENDIMENTO, come farebbe l'amministratore di un fondo.

Componenti:
  record_nav_snapshot()  - persiste il NAV ufficiale di OGGI in nav_snapshots
                           (stessa fonte del Dashboard: posizioni*prezzi correnti + cash).
                           Chiamato da price_updater a fine giro prezzi. No-op se la
                           tabella non esiste (la crea il PM con tools/migrations/setup_twr_tables.py).
  get_official_series()  - serie giornaliera del valore: nav_snapshots dove esistono
                           (regime 'official'), ricostruzione da compute_nav_history
                           per il passato pre-snapshot (regime 'reconstructed').
  compute_twr(series, flows) - TWR GIPS: r_t = (V_t - V_{t-1} - F_t) / V_{t-1},
                           convenzione flussi a FINE giornata (w=0, dichiarata).
  compute_irr(flows, terminal_value, ...) - XIRR money-weighted (Newton + bisezione).
  compute_twr_payload()  - payload completo per GET /portfolio/analytics/twr (cache 10 min).

REGIMI (documentati nel payload, campo 'regimes' per-data + 'regime_summary'):
  'official'      dal primo snapshot in poi. V = NAV totale (investito + cash).
                  F = SOLO flussi esterni dal ledger cash_movements (DEPOSIT +, WITHDRAWAL -).
                  Acquisti/vendite sono interni (cash <-> titoli dentro il NAV) e si elidono.
  'reconstructed' per il passato senza ledger ne' snapshot. V = solo capitale investito
                  mark-to-market (chiusure yfinance, da compute_nav_history). Senza storia
                  del cash, ogni BUY e' trattato come flusso IN al costo e ogni SELL come
                  flusso OUT al controvalore pieno: F_t = dCB_t - dRealized_t.
                  Cosi' gli acquisti NON sono profit e le vendite NON sono drawdown; il
                  realized resta rendimento (era nel prezzo durante l'holding period).
                  Dividendi NON trattati come flusso: le chiusure auto_adjust li
                  incorporano gia' nel rendimento (no double counting).
                  CB storico a FX STORICO del giorno del trade: usare il cambio
                  corrente altererebbe retroattivamente il costo storico.
  Transizione: se la ricostruzione copre il giorno del primo snapshot (d0) la catena e'
  continua (r ricostruiti fino a d0, poi r ufficiali da snapshot a snapshot con base =
  NAV totale dello snapshot d0); altrimenti il giorno di salto vale r=0 (dichiarato).

IRR money-weighted (modello ibrido per ere, coerente con i regimi TWR):
  - pre-d0: flussi impliciti nei trade (BUY = esce dalla tasca del PM, SELL/TRIM/
    DIVIDEND = rientra) - il cash non tracciato dell'epoca resta fuori perimetro;
  - a d0: deposito sintetico = cash del primo snapshot (la cassa ENTRA nel perimetro
    misurato del fondo);
  - post-d0: SOLO ledger cash_movements (i trade sono interni al NAV);
  - terminale: NAV totale live (o solo investito se non esistono snapshot).
  Robusto a ledger parziale: i movimenti backfillati con data <= d0 NON si
  double-contano (quei depositi finanziavano buy gia' contati come flusso).
"""
import time
import sqlite3
import math
from datetime import datetime, date
from typing import Dict, Any, Optional, List, Tuple

from bellomberg.storage.memory_db import MemoryDB, SQLITE_PATH, connect_sqlite
from bellomberg.core.presentation import message as _message, render_payload

_CACHE: Dict[str, Any] = {}
CACHE_TTL_SEC = 600  # 10 min, come portfolio_analytics


def _log(msg: str):
    # audit/11 §4: stesso guard di portfolio_analytics (fix 13/07, pipe morta Electron)
    try:
        print(f"[TWR] {msg}", flush=True)
    except OSError:
        pass


# F5 (riallineamento 23/07, audit/20): tolleranza di riconciliazione NAV —
# oltre questa soglia il payload alza `breach` e il health-check pre-run del
# consigliere la dichiara al Capo. Parametro PM, tarabile qui.
RECON_TOLERANCE_PCT = 1.0


def build_recon_note(nav_live: float, snaps: List[Dict[str, Any]],
                     tolerance_pct: float = RECON_TOLERANCE_PCT) -> Optional[Dict[str, Any]]:
    """F5: riconciliazione |NAV live − ultimo snapshot| con soglia DICHIARATA.
    Helper PURO (niente DB/rete) per testabilita' offline. Ritorna None senza
    snapshot (buco gestito dal chiamante, mai un breach inventato)."""
    if not snaps:
        return None
    last_snap = snaps[-1]
    snap_nav = float(last_snap["nav_total_eur"] or 0)
    delta_pct = ((nav_live - snap_nav) / snap_nav * 100.0) if snap_nav > 0 else None
    return {
        "nav_live_eur": round(nav_live, 2),
        "last_snapshot_date": last_snap["date"],
        "last_snapshot_nav_eur": round(snap_nav, 2),
        "last_snapshot_created_at": last_snap.get("created_at"),
        "delta_pct": round(delta_pct, 3) if delta_pct is not None else None,
        "tolerance_pct": tolerance_pct,
        "breach": (delta_pct is not None and abs(delta_pct) > tolerance_pct),
        "note": _message('delta = NAV live (prezzi correnti) vs ultimo snapshot ufficiale persistito; breach = |delta| oltre {v0}% dichiarato', 'delta = live NAV (current prices) versus the latest persisted official snapshot; breach = |delta| above {v0}% as disclosed', v0=tolerance_pct),
    }


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ============================================================
# SNAPSHOT NAV UFFICIALE (scritto dal giro prezzi)
# ============================================================

def record_nav_snapshot(db: Optional[MemoryDB] = None) -> Dict[str, Any]:
    """Scrive/aggiorna lo snapshot NAV di OGGI in nav_snapshots.
    Fonte ufficiale = memory_db.get_portfolio_summary() (posizioni * prezzi
    correnti convertiti EUR + cash scalare): la STESSA catena del Dashboard.
    Tollerante: se la tabella non esiste -> no-op con log (mai eccezioni)."""
    try:
        if db is None:
            db = MemoryDB()
        db_path = getattr(db, "db_path", SQLITE_PATH)
        conn = connect_sqlite(db_path)  # hardening #32: WAL + busy_timeout
        try:
            if not _table_exists(conn, "nav_snapshots"):
                _log("nav_snapshots assente: snapshot saltato (lancia tools/migrations/setup_twr_tables.py)")
                return {"ok": False, "reason": _message('tabella nav_snapshots assente', 'nav_snapshots table missing')}
            # Mark the start of valuation. A balance inserted during the read
            # must not certify this older NAV as covering that new balance.
            now_iso = datetime.now().isoformat(timespec="microseconds")
            snap = db.get_portfolio_summary()
            has_opening = (_table_exists(conn, "position_openings") and
                           conn.execute("SELECT 1 FROM position_openings LIMIT 1").fetchone() is not None)
            if has_opening and snap.get("stale_positions"):
                return {"ok": False, "reason": _message(
                    "Saldi iniziali: prezzi mancanti o vecchi per {tickers}; nessuno snapshot ufficiale al costo.",
                    "Opening balances: missing or old prices for {tickers}; no official snapshot at cost.",
                    tickers=", ".join(snap["stale_positions"]))}
            if snap.get("fx_incomplete"):
                _log("FX incompleto, snapshot non scritto: " + str(snap["fx_incomplete"]))
                return {"ok": False, "reason": _message("FX incompleto: {missing}", "Incomplete FX: {missing}", missing=str(snap["fx_incomplete"]))}
            _fx_non_misurati = {
                cur: source for cur, source in (snap.get("fx_sources") or {}).items()
                if source != "live"
            }
            if _fx_non_misurati:
                _log("FX non live, snapshot non scritto: " + str(_fx_non_misurati))
                return {"ok": False, "reason": _message("FX non live: {sources}", "Non-live FX: {sources}", sources=str(_fx_non_misurati))}
            nav_total = float(snap.get("nav_total_eur") or 0)
            invested = float(snap.get("totale_valore_mercato_eur") or 0)
            cash = float(snap.get("cash_disponibile_eur") or 0)
            # F43(1) 27/08 (review): uno zero NON misurato (portfolio.json
            # assente/illeggibile) non diventa storia ufficiale del NAV —
            # fail-closed come il POST della cassa; il giro prezzi successivo
            # riprova. Chiave presente e nulla = dichiarazione del lettore.
            if "cash_source" in snap and snap["cash_source"] is None:
                _log("cassa NON misurata, snapshot non scritto: " + str(snap.get("cash_source_note")))
                return {"ok": False, "reason": _message("cassa NON misurata: {reason}", "Cash NOT measured: {reason}", reason=snap.get("cash_source_note"))}
            if nav_total <= 0:
                _log("NAV <= 0: snapshot non scritto")
                return {"ok": False, "reason": "nav <= 0"}
            today_iso = date.today().isoformat()
            conn.execute(
                "INSERT INTO nav_snapshots (date, nav_total_eur, invested_eur, cash_eur, source, created_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET nav_total_eur=excluded.nav_total_eur, "
                "invested_eur=excluded.invested_eur, cash_eur=excluded.cash_eur, "
                "source=excluded.source, created_at=excluded.created_at",
                (today_iso, round(nav_total, 2), round(invested, 2), round(cash, 2),
                 "price_updater", now_iso))
            conn.commit()
            _log(f"snapshot {today_iso}: NAV EUR {nav_total:,.0f} (inv {invested:,.0f} + cash {cash:,.0f})")
            return {"ok": True, "date": today_iso, "nav_total_eur": round(nav_total, 2)}
        finally:
            conn.close()
    except Exception as e:
        _log(f"record_nav_snapshot failed (non bloccante): {e}")
        return {"ok": False, "reason": str(e)[:200]}


# ============================================================
# LETTURE TOLLERANTI (tabelle possono non esistere ancora)
# ============================================================

def _load_snapshots(db_path: str = SQLITE_PATH) -> List[Dict[str, Any]]:
    try:
        conn = connect_sqlite(db_path)  # hardening #32: WAL + busy_timeout
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "nav_snapshots"):
                return []
            rows = conn.execute(
                "SELECT date, nav_total_eur, invested_eur, cash_eur, source, created_at "
                "FROM nav_snapshots ORDER BY date ASC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        _log(f"load snapshots failed: {e}")
        return []


def get_cash_movements(db_path: str = SQLITE_PATH) -> List[Dict[str, Any]]:
    """Ledger flussi esterni, ordinato per data. [] se tabella assente."""
    try:
        conn = connect_sqlite(db_path)  # hardening #32: WAL + busy_timeout
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "cash_movements"):
                return []
            rows = conn.execute(
                "SELECT id, date, type, amount_eur, note FROM cash_movements "
                "ORDER BY date ASC, id ASC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        _log(f"load cash_movements failed: {e}")
        return []


# ============================================================
# SERIE UFFICIALE (snapshot dove esistono + ricostruzione passato)
# ============================================================

def get_official_series() -> Dict[str, Any]:
    """Serie giornaliera del valore con regime per-data e flussi esterni per-data.
    Returns {dates, values_eur, flows_eur, regimes, official_since, notes, snapshots, ledger}."""
    snaps = _load_snapshots()
    ledger = get_cash_movements()
    notes: List[str] = []

    openings = MemoryDB().get_opening_positions()
    if openings:
        # Registration time, not the balance's as_of, determines which observed
        # NAV includes every holding. No synthetic trade or historical cash.
        def instant(value):
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone()  # naive snapshot timestamps are local by contract
        latest = max(openings, key=lambda row: instant(row["created_at"]))
        added = latest["created_at"]
        covered = []
        latest_day = max(row["as_of"][:10] for row in openings)
        for snap in snaps:
            try:
                if (snap.get("source") == "price_updater"
                        and instant(snap["created_at"]) >= instant(added)
                        and snap["date"] >= latest_day):
                    covered.append(snap)
            except (ValueError, TypeError, KeyError):
                continue  # cannot certify which balances this observation includes
        excluded = len(snaps) - len(covered)
        d0 = covered[0]["date"] if covered else None
        notes.append(_message('Saldi iniziali registrati fino a {v0}; primo snapshot successivo: {v1}. Snapshot precedenti o non verificabili esclusi: {v2}. Storia degli acquisti ignota: nessuna ricostruzione del periodo precedente.', 'Opening balances registered through {v0}; first subsequent snapshot: {v1}. Earlier or unverifiable snapshots excluded: {v2}. Purchase history unknown: no reconstruction of the earlier period.', v0=added, v1=d0 or _message('n.d.', 'n/a'), v2=excluded))
        flows, previous = [], d0
        for snap in covered:
            flows.append(round(sum((1 if row["type"] == "DEPOSIT" else -1) * float(row["amount_eur"])
                                   for row in ledger if previous < row["date"] <= snap["date"]), 2))
            previous = snap["date"]
        return {"dates": [s["date"] for s in covered],
                "values_eur": [float(s["nav_total_eur"]) for s in covered],
                "flows_eur": flows, "regimes": ["official"] * len(covered),
                "official_since": d0, "seamless_transition": False,
                "notes": notes, "snapshots": covered, "ledger": ledger,
                "position_openings": openings, "baseline_added_at": added,
                "snapshot_prima_del_baseline": excluded,
                "recon_error": _message('Saldi iniziali: periodo precedente al primo snapshot coperto non ricostruibile.', 'Opening balances: the period before the first covered snapshot cannot be reconstructed.')}

    recon = {}
    try:
        from bellomberg.portfolio.portfolio_analytics import compute_nav_history
        recon = compute_nav_history() or {}
    except Exception as e:
        recon = {"error": str(e)}
        try:
            import traceback as _tb
            _log("ricostruzione NAV FALLITA (stack completo):\n" + _tb.format_exc())
        except Exception:
            pass
    if recon.get("error"):
        notes.append(_message('ricostruzione storica non disponibile: {v0}', 'Historical reconstruction unavailable: {v0}', v0=recon['error']))

    d0 = snaps[0]["date"] if snaps else None

    dates: List[str] = []
    values: List[float] = []
    flows: List[float] = []
    regimes: List[str] = []

    # --- tratto RICOSTRUITO: chiusure daily fino a d0 incluso (o tutto se niente snapshot)
    if not recon.get("error") and recon.get("dates"):
        rd = recon["dates"]
        rv = recon["nav_eur"]
        rcb = recon.get("cost_basis_eur") or [0.0] * len(rd)
        rre = recon.get("realized_sales_eur") or [0.0] * len(rd)
        for i, dstr in enumerate(rd):
            if d0 is not None and dstr > d0:
                break
            dates.append(dstr)
            values.append(float(rv[i]))
            regimes.append("reconstructed")
            if i == 0:
                flows.append(float(rcb[0]))  # capitale iniziale: e' un flusso, non un return
            else:
                f = (float(rcb[i]) - float(rcb[i - 1])) - (float(rre[i]) - float(rre[i - 1]))
                flows.append(round(f, 2))

    # --- tratto UFFICIALE: snapshot NAV totale + flussi esterni dal ledger
    seamless = bool(dates) and d0 is not None and dates[-1] == d0
    if snaps:
        flow_by_date: Dict[str, float] = {}
        for m in ledger:
            sgn = 1.0 if (m.get("type") == "DEPOSIT") else -1.0
            flow_by_date[m["date"]] = flow_by_date.get(m["date"], 0.0) + sgn * float(m.get("amount_eur") or 0)
        prev_date = d0
        last_recon_date = dates[-1] if dates else ""
        for s in snaps:
            dstr = s["date"]
            if dstr <= last_recon_date:
                # data gia' coperta dal tratto ricostruito (il punto base d0): non duplicare
                prev_date = dstr
                continue
            # flussi esterni nell'intervallo (prev_date, dstr]
            f = sum(v for k, v in flow_by_date.items()
                    if (prev_date is None or k > prev_date) and k <= dstr)
            dates.append(dstr)
            values.append(float(s["nav_total_eur"]))
            flows.append(round(f, 2))
            regimes.append("official")
            prev_date = dstr
        pre_ledger = [m for m in ledger if m["date"] <= (d0 or "9999-12-31")]
        if pre_ledger:
            notes.append(_message(
                "{v0} movimento del ledger precede il primo snapshot: nel tratto ricostruito i flussi sono gia' impliciti nei trade (non doppio-contati)." if len(pre_ledger) == 1 else
                "{v0} movimenti del ledger precedono il primo snapshot: nel tratto ricostruito i flussi sono gia' impliciti nei trade (non doppio-contati).",
                '{v0} ledger movement precedes the first snapshot: flows in the reconstructed segment are already implicit in trades (not counted twice).' if len(pre_ledger) == 1 else
                '{v0} ledger movements precede the first snapshot: flows in the reconstructed segment are already implicit in trades (not counted twice).', v0=len(pre_ledger)))
        if not seamless and dates:
            notes.append(_message('transizione {v0}: ricostruzione e snapshot non si sovrappongono, r del giorno di salto = 0 (perimetro non confrontabile).', 'Transition {v0}: reconstruction and snapshots do not overlap; return on the transition day = 0 (non-comparable scope).', v0=d0))
        if not ledger:
            notes.append(_message("ledger cash_movements vuoto: nel regime official i flussi esterni valgono 0 finche' non registri depositi/prelievi con tools/migrations/setup_twr_tables.py.", 'Empty cash_movements ledger: external flows are zero in the official regime until deposits/withdrawals are recorded with tools/migrations/setup_twr_tables.py.'))

    return {
        "dates": dates, "values_eur": values, "flows_eur": flows, "regimes": regimes,
        "official_since": d0, "seamless_transition": seamless,
        "notes": notes, "snapshots": snaps, "ledger": ledger,
        "recon_error": recon.get("error"),
    }


# ============================================================
# TWR GIPS + metriche sulla serie TWR
# ============================================================

def compute_twr(series: List[float], flows: List[float]) -> List[float]:
    """TWR GIPS: r_t = (V_t - V_{t-1} - F_t) / V_{t-1}.
    F_t = SOLO flussi esterni attribuiti all'intervallo (t-1, t], convenzione
    fine-giornata (w=0). Ritorna la lista dei rendimenti (len = len(series)-1).
    Guardia: V_{t-1} <= 0 -> r=0 (intervallo non misurabile)."""
    rets: List[float] = []
    for t in range(1, len(series)):
        v_prev = float(series[t - 1])
        v_now = float(series[t])
        f = float(flows[t]) if t < len(flows) else 0.0
        if v_prev > 0:
            rets.append((v_now - v_prev - f) / v_prev)
        else:
            rets.append(0.0)
    return rets


def _twr_metrics(dates: List[str], rets: List[float], rf_annual: float) -> Dict[str, Any]:
    """Indice base 100 + drawdown/max_dd/vol/Sharpe calcolati SULLA SERIE TWR."""
    index = [100.0]
    for r in rets:
        index.append(index[-1] * (1.0 + r))
    index = [round(x, 4) for x in index]

    peak = index[0]
    max_dd = 0.0
    for x in index:
        if x > peak:
            peak = x
        dd = (x - peak) / peak * 100.0
        if dd < max_dd:
            max_dd = dd
    overall_peak = max(index)
    current_dd = (index[-1] - overall_peak) / overall_peak * 100.0

    twr_total_pct = (index[-1] / 100.0 - 1.0) * 100.0
    ann_return = None
    vol_annual = None
    sharpe = None
    if len(dates) >= 2:
        try:
            span_days = (datetime.strptime(dates[-1], "%Y-%m-%d")
                         - datetime.strptime(dates[0], "%Y-%m-%d")).days
        except Exception:
            span_days = len(rets)
        if span_days >= 20:
            ann_return = ((index[-1] / 100.0) ** (365.25 / span_days) - 1.0) * 100.0
    if len(rets) >= 20:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        vol_annual = (var ** 0.5) * (252 ** 0.5) * 100.0
        if vol_annual and vol_annual > 0 and ann_return is not None:
            sharpe = (ann_return / 100.0 - rf_annual) / (vol_annual / 100.0)

    return {
        "index": index,
        "twr_total_pct": round(twr_total_pct, 2),
        "twr_annualized_pct": round(ann_return, 2) if ann_return is not None else None,
        "max_drawdown_pct": round(max_dd, 2),
        "current_drawdown_pct": round(current_dd, 2),
        "vol_annual_pct": round(vol_annual, 2) if vol_annual is not None else None,
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "risk_free_used": rf_annual,
    }


# ============================================================
# IRR MONEY-WEIGHTED (XIRR: Newton con fallback bisezione)
# ============================================================

def compute_irr(flows: List[Tuple[str, float]], terminal_value: float,
                terminal_date: Optional[str] = None) -> Optional[float]:
    """XIRR annualizzato. flows = [(iso_date, eur)] in convenzione INVESTITORE:
    deposito/acquisto = NEGATIVO (soldi che escono dalla tasca del PM),
    prelievo/vendita/dividendo = POSITIVO. terminal_value = valore liquidabile
    oggi (positivo). Newton-Raphson, fallback bisezione su [-0.95, 10].

    Formulazione a VALORE FUTURO (equivalente a NPV=0, numericamente piu'
    stabile vicino a r=-0.95): FV(r) = terminal + sum cf_i * (1+r)^yrs_i = 0,
    con yrs_i = anni dal flusso alla data terminale."""
    if not flows or terminal_value is None:
        return None
    t_end = terminal_date or date.today().isoformat()
    try:
        d_end = datetime.strptime(t_end[:10], "%Y-%m-%d")
    except Exception:
        return None
    cfs: List[Tuple[float, float]] = []  # (anni dal flusso alla data terminale, importo)
    for dstr, amt in flows:
        try:
            d = datetime.strptime((dstr or "")[:10], "%Y-%m-%d")
        except Exception:
            continue
        yrs = max((d_end - d).days / 365.25, 0.0)
        cfs.append((yrs, float(amt)))
    if not cfs:
        return None

    def fv(rate: float) -> float:
        total = float(terminal_value)
        for yrs, cf in cfs:
            total += cf * (1.0 + rate) ** yrs
        return total

    def fv_prime(rate: float) -> float:
        tot = 0.0
        for yrs, cf in cfs:
            if yrs > 0:
                tot += cf * yrs * (1.0 + rate) ** (yrs - 1.0)
        return tot

    tol = 1e-6 * max(1.0, abs(float(terminal_value)))

    # Newton-Raphson
    rate = 0.10
    for _ in range(60):
        f = fv(rate)
        if abs(f) < tol and -0.95 < rate < 10.0:
            return float(rate)
        fp = fv_prime(rate)
        if abs(fp) < 1e-12:
            break
        new_rate = rate - f / fp
        if new_rate <= -0.95:
            new_rate = (rate - 0.95) / 2.0
        if new_rate > 10.0:
            new_rate = (rate + 10.0) / 2.0
        if abs(new_rate - rate) < 1e-10:
            rate = new_rate
            break
        rate = new_rate
    if abs(fv(rate)) < tol and -0.95 < rate < 10.0:
        return float(rate)

    # Fallback: bisezione su [-0.95, 10] (richiede cambio di segno)
    lo, hi = -0.95, 10.0
    f_lo, f_hi = fv(lo), fv(hi)
    if f_lo * f_hi > 0:
        return None  # nessuna radice nel range ragionevole
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = fv(mid)
        if abs(f_mid) < tol or (hi - lo) < 1e-9:
            return float(mid)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return float((lo + hi) / 2.0)


def _build_irr_flows(ctx: Dict[str, Any], live_summary: Dict[str, Any]) -> Tuple[List[Tuple[str, float]], float, str]:
    """Flussi per l'IRR money-weighted, modello ibrido per ere (vedi docstring modulo).
    Returns (flows, terminal_value, basis)."""
    snaps = ctx.get("snapshots") or []
    ledger = ctx.get("ledger") or []
    d0 = snaps[0]["date"] if snaps else None
    flows: List[Tuple[str, float]] = []
    foreign = set()
    if ctx.get("position_openings"):
        if len(snaps) < 2:
            return [], 0.0, _message('IRR n.d.: servono due snapshot successivi alla registrazione di tutti i saldi iniziali.', 'IRR unavailable: two snapshots after registration of all opening balances are required.')
        terminal_day = snaps[-1]["date"]
        flows.append((d0, -float(snaps[0]["nav_total_eur"])))
        for movement in ledger:
            if d0 < movement["date"] <= terminal_day:
                amount = float(movement["amount_eur"])
                flows.append((movement["date"], -amount if movement["type"] == "DEPOSIT" else amount))
        return flows, float(snaps[-1]["nav_total_eur"]), (
            _message('Snapshot osservati dal {v0} al {v1}; NAV iniziale e flussi esterni documentati. Ultima registrazione saldo: {v2}; acquisti precedenti ignoti.', 'Observed snapshots from {v0} to {v1}; initial NAV and external flows documented. Latest balance registration: {v2}; earlier purchases unknown.', v0=d0, v1=terminal_day, v2=ctx['baseline_added_at']))

    # Era A: flussi impliciti nei trade fino a d0 incluso (o tutta la storia se no snapshot)
    try:
        db = MemoryDB()
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT ticker, action, quantita, prezzo, valuta, data "
                "FROM trade_history ORDER BY data ASC").fetchall()
        from bellomberg.cli.price_updater import get_fx_to_eur_con_fonte
        for r in rows:
            dstr = (r["data"] or "")[:10]
            if not dstr:
                continue
            if d0 is not None and dstr > d0:
                continue  # post-d0: i trade sono interni al NAV, non flussi esterni
            action = (r["action"] or "").upper()
            qty = float(r["quantita"] or 0)
            px = float(r["prezzo"] or 0)
            ccy = (r["valuta"] or "").upper()
            if ccy == "EUR":
                fx, source = 1.0, "identity"
            else:
                fx, source = get_fx_to_eur_con_fonte(ccy)
                foreign.add(ccy)
            if (not ccy or fx is None or not math.isfinite(fx) or fx <= 0
                    or source not in ("live", "identity")):
                return [], 0.0, _message('IRR non calcolabile: FX {v0}->EUR n.d. o fonte {v1} non utilizzabile', 'IRR cannot be calculated: FX {v0}->EUR unavailable or source {v1} unusable', v0=ccy or _message('valuta n.d.', 'currency unavailable'), v1=source)
            amt = qty * px * fx
            if action in ("BUY", "ADD"):
                flows.append((dstr, -amt))
            elif action in ("SELL", "TRIM", "DIVIDEND"):
                flows.append((dstr, amt))
    except Exception as e:
        _log(f"irr trade flows failed: {e}")
        return [], 0.0, _message("IRR non calcolabile: {error}", "IRR cannot be calculated: {error}", error=str(e))

    fx_note = (_message("; FX corrente (non storico) per {currencies}",
                        "; current (not historical) FX for {currencies}",
                        currencies=", ".join(sorted(foreign))) if foreign else "")

    if not snaps:
        terminal = float(live_summary.get("totale_valore_mercato_eur") or 0)
        return flows, terminal, _message("trades{fx_note}", "trades{fx_note}", fx_note=fx_note)

    # Transizione: la cassa del primo snapshot entra nel perimetro misurato
    cash_d0 = float(snaps[0].get("cash_eur") or 0)
    if cash_d0 > 0:
        flows.append((d0, -cash_d0))

    # Era B: solo ledger post-d0
    for m in ledger:
        if m["date"] <= d0:
            continue
        amt = float(m.get("amount_eur") or 0)
        flows.append((m["date"], -amt if m.get("type") == "DEPOSIT" else amt))

    terminal = float(live_summary.get("nav_total_eur") or 0)
    return flows, terminal, _message("hybrid: trades fino al primo snapshot + cash iniziale + ledger dopo{fx_note}",
                                     "hybrid: trades through the first snapshot + initial cash + subsequent ledger{fx_note}", fx_note=fx_note)


def _performance_coverage(db, ctx):
    """Calendar coverage, independent of return calculations or invented cash history."""
    snaps = sorted({s["date"] for s in ctx.get("snapshots", [])})
    first, last = (snaps[0], snaps[-1]) if snaps else (None, None)
    coverage = {"primo_trade": None, "primo_snapshot": first, "ultimo_snapshot": last,
                "official_since": ctx.get("official_since"), "n_trade_prima_del_primo_snapshot": None,
                "giorni_senza_snapshot": None, "nota": None}
    try:
        with db._conn() as conn:
            rows = conn.execute("SELECT data FROM trade_history ORDER BY data,id").fetchall()
        days = [datetime.fromisoformat(r["data"]).date().isoformat() for r in rows]
        coverage["primo_trade"] = min(days) if days else None
        if first:
            coverage["n_trade_prima_del_primo_snapshot"] = sum(d < first for d in days)
            coverage["giorni_senza_snapshot"] = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1 - len(snaps)
        coverage["nota"] = (_message("Primo trade: {v0}; primo snapshot: {v1}. Prima degli snapshot la serie e ricostruita senza storia completa della cassa. Giorni di calendario senza snapshot nell'intervallo: {v2}. Le registrazioni retrodatate non riscrivono gli snapshot NAV passati.", 'First trade: {v0}; first snapshot: {v1}. Before snapshots, the series is reconstructed without complete cash history. Calendar days without a snapshot in the interval: {v2}. Backdated entries do not rewrite past NAV snapshots.', v0=coverage['primo_trade'] or _message('n.d.', 'n/a'), v1=first or _message('n.d.', 'n/a'), v2=coverage['giorni_senza_snapshot'] if first else _message('n.d.', 'n/a')))
    except Exception as exc:
        coverage["nota"] = _message('Copertura non verificabile: {v0}: {v1}', 'Coverage cannot be verified: {v0}: {v1}', v0=type(exc).__name__, v1=exc)
    if ctx.get("position_openings"):
        coverage.update({"baseline_added_at": ctx["baseline_added_at"],
                         "snapshot_prima_del_baseline": ctx["snapshot_prima_del_baseline"],
                         "position_openings": ctx["position_openings"]})
        coverage["nota"] = (_message('Ultimo saldo iniziale registrato: {v0}; periodo coperto: {v1} - {v2}. Snapshot precedenti o non verificabili esclusi: {v3}. Data di acquisto e storia precedente ignote. Giorni senza snapshot nel periodo: {v4}.', 'Latest opening balance registered: {v0}; covered period: {v1} - {v2}. Earlier or unverifiable snapshots excluded: {v3}. Purchase date and earlier history unknown. Days without a snapshot in the period: {v4}.', v0=ctx['baseline_added_at'], v1=first or _message('n.d.', 'n/a'), v2=last or _message('n.d.', 'n/a'), v3=ctx['snapshot_prima_del_baseline'], v4=coverage['giorni_senza_snapshot'] if first else _message('n.d.', 'n/a')))
    return coverage


# ============================================================
# PAYLOAD COMPLETO (per GET /portfolio/analytics/twr)
# ============================================================

def compute_twr_payload(force: bool = False) -> Dict[str, Any]:
    """Payload per la pagina Performance: indice TWR base 100, metriche sulla
    serie TWR, IRR money-weighted, regime per tratto, as_of e riconciliazione
    NAV live vs ultimo snapshot. Cache 10 min."""
    if not force and "payload" in _CACHE:
        entry = _CACHE["payload"]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return render_payload(entry["data"])

    ctx = get_official_series()
    dates = ctx["dates"]
    values = ctx["values_eur"]
    flows = ctx["flows_eur"]
    regimes = ctx["regimes"]
    notes = list(ctx["notes"])
    snaps = ctx["snapshots"]

    if len(dates) < 2:
        return {"error": _message("serie insufficiente (servono >=2 punti): {reason}",
                                  "Insufficient series (at least 2 points required): {reason}",
                                  reason=ctx.get("recon_error") or _message("nessun dato", "no data")),
                "official_since": ctx.get("official_since"),
                "copertura": _performance_coverage(MemoryDB(), ctx),
                "timestamp": datetime.now().isoformat()}

    # rendimenti per segmento (mai attraverso un cambio di perimetro non sovrapposto)
    seamless = bool(ctx.get("seamless_transition", False))
    rets: List[float] = []
    for t in range(1, len(dates)):
        regime_change = regimes[t] != regimes[t - 1]
        if regime_change and not seamless:
            rets.append(0.0)  # giorno di salto dichiarato (nota gia' in ctx)
            continue
        if regime_change and seamless:
            # base del primo r ufficiale = NAV totale dello snapshot d0 (stesso perimetro
            # del punto t), NON il valore ricostruito (solo investito) della stessa data
            base = float(snaps[0]["nav_total_eur"]) if snaps else float(values[t - 1])
            f = float(flows[t])
            rets.append(((float(values[t]) - base - f) / base) if base > 0 else 0.0)
            continue
        rets.append(compute_twr([values[t - 1], values[t]], [0.0, float(flows[t])])[0])

    try:
        from bellomberg.market_data.market_inputs import get_risk_free
        rf = float(get_risk_free("EUR") or 0.03)
    except Exception:
        rf = 0.03
    metrics = _twr_metrics(dates, rets, rf)

    db = MemoryDB()
    live = db.get_portfolio_summary()
    irr_flows, terminal, irr_basis = _build_irr_flows(ctx, live)
    today_iso = date.today().isoformat()
    irr_as_of = snaps[-1]["date"] if ctx.get("position_openings") and snaps else today_iso
    irr = compute_irr(irr_flows, terminal, irr_as_of) if irr_flows else None

    # riconciliazione NAV live vs ultimo snapshot ufficiale (helper puro sotto)
    nav_live = float(live.get("nav_total_eur") or 0)
    recon_note = build_recon_note(nav_live, snaps)
    if recon_note and recon_note.get("breach"):
        _log(f"RICONCILIAZIONE NAV FUORI TOLLERANZA: delta {recon_note['delta_pct']:+.2f}% "
             f"(soglia {RECON_TOLERANCE_PCT}%) vs snapshot {recon_note['last_snapshot_date']}")
    if not snaps:
        # fix 30c: distingui tabella ASSENTE (serve lo script) da tabella PRESENTE ma vuota
        # (il primo snapshot arriva da solo a fine giro prezzi, o subito con REFRESH PREZZI).
        table_ok = False
        try:
            _conn = connect_sqlite(getattr(db, "db_path", SQLITE_PATH))  # hardening #32
            try:
                table_ok = _table_exists(_conn, "nav_snapshots")
            finally:
                _conn.close()
        except Exception:
            pass
        if table_ok:
            notes.append(_message('nessuno snapshot NAV ancora (tabella nav_snapshots pronta ma vuota): serie interamente ricostruita da chiusure. In attesa del primo snapshot: si scrive a fine del prossimo giro prezzi (15-30 min) o subito col bottone REFRESH PREZZI.', 'No NAV snapshot yet (nav_snapshots table ready but empty): the series is entirely reconstructed from closing prices. Awaiting the first snapshot at the end of the next price update (15-30 min), or immediately via REFRESH PRICES.'))
        else:
            notes.append(_message('nessuno snapshot NAV ancora: serie interamente ricostruita da chiusure. Lancia tools/migrations/setup_twr_tables.py e riavvia il backend per attivare il regime ufficiale.', 'No NAV snapshot yet: the series is entirely reconstructed from closing prices. Run tools/migrations/setup_twr_tables.py and restart the backend to activate the official regime.'))

    n_official = sum(1 for r in regimes if r == "official")
    n_recon = len(regimes) - n_official
    payload = {
        "as_of": {
            "computed_at": datetime.now().isoformat(timespec="seconds"),
            "price_basis": _message('official: snapshot NAV (prezzi del giro price_updater); reconstructed: chiusure daily yfinance auto-adjusted', 'official: NAV snapshot (prices from the price_updater run); reconstructed: auto-adjusted daily yfinance closes'),
            "fx_basis": _message('official: FX live al momento dello snapshot; reconstructed: FX daily storico (CB al cambio storico)', 'official: live FX at snapshot time; reconstructed: historical daily FX (cost basis at historical FX)'),
        },
        "dates": dates,
        "copertura": _performance_coverage(db, ctx),
        "irr_as_of": irr_as_of,
        "twr_index": metrics["index"],
        "regimes": regimes,
        "values_eur": [round(v, 2) for v in values],
        "flows_eur": flows,
        "regime_summary": {
            "official_since": ctx.get("official_since"),
            "n_official_days": n_official,
            "n_reconstructed_days": n_recon,
            "seamless_transition": seamless,
        },
        # L'ora dell'ultimo snapshot e' esposta anche fra le chiavi top-level:
        # l'indicatore «IN CORSO» legge queste; i pannelli di confronto NAV
        # leggono invece l'oggetto reconciliation.
        # Base oraria LOCALE con la T (record_nav_snapshot usa datetime.now())
        # — NON UTC come cash_movements.created_at. Senza snapshot: null dichiarato.
        "last_snapshot_created_at": (snaps[-1].get("created_at") if snaps else None),
        "metrics": {
            "twr_total_pct": metrics["twr_total_pct"],
            "twr_annualized_pct": metrics["twr_annualized_pct"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "current_drawdown_pct": metrics["current_drawdown_pct"],
            "vol_annual_pct": metrics["vol_annual_pct"],
            "sharpe": metrics["sharpe"],
            "risk_free_used": metrics["risk_free_used"],
            "irr_annual_pct": round(irr * 100.0, 2) if irr is not None else None,
            "irr_basis": irr_basis,
        },
        "external_flows": [
            {"date": m["date"], "type": m["type"], "amount_eur": m["amount_eur"], "note": m.get("note")}
            for m in (ctx.get("ledger") or [])
        ],
        "reconciliation": recon_note,
        "notes": notes,
        "methodology": (_message("TWR GIPS r_t=(V_t-V_{t-1}-F_t)/V_{t-1}, flussi a fine giornata (w=0); F = solo flussi esterni (ledger) nel regime official, net-invested-at-cost (dCB - dRealized) nel regime reconstructed pre-ledger. Drawdown/vol/Sharpe calcolati sull'indice TWR. IRR = XIRR money-weighted.", 'TWR GIPS r_t=(V_t-V_{t-1}-F_t)/V_{t-1}, end-of-day flows (w=0); F = external ledger flows only in the official regime, net-invested-at-cost (dCB - dRealized) in the pre-ledger reconstructed regime. Drawdown/volatility/Sharpe calculated on the TWR index. IRR = money-weighted XIRR.')),
        "n_days": len(dates),
        "timestamp": datetime.now().isoformat(),
    }
    _CACHE["payload"] = {"ts": time.time(), "data": payload}
    _log(f"payload TWR: {len(dates)} giorni ({n_recon} ricostruiti + {n_official} ufficiali), "
         f"TWR {metrics['twr_total_pct']:+.2f}%, maxDD {metrics['max_drawdown_pct']:.2f}%")
    return render_payload(payload)


def invalidate_cache():
    _CACHE.clear()


if __name__ == "__main__":
    import json
    p = compute_twr_payload(force=True)
    print(json.dumps({k: v for k, v in p.items()
                      if k not in ("dates", "twr_index", "values_eur", "flows_eur", "regimes")},
                     indent=2, ensure_ascii=False, default=str))
