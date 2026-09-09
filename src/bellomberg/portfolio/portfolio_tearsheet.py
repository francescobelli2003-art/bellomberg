"""
portfolio_tearsheet.py — Quant fase 1c LOTTO 1: TEARSHEET sul TWR ufficiale.

Scelta PM (23/07 notte): NIENTE QuantStats — le metriche scalari istituzionali
esistono GIA' tutte in casa (advanced_metrics.compute_metrics: Sharpe/Sortino/
Calmar/Omega/VaR/CVaR/tail/streaks/Kelly + benchmark SPY-EUR) e qui vengono
RIUSATE, non ricalcolate (una fonte sola). Questo modulo aggiunge le VISTE che
mancano a un tearsheet: mensili, episodi di drawdown, rolling. Zero dipendenze.

Convenzioni DICHIARATE (regola no-fallback 14/07):
- serie = twr_index di twr_engine.compute_twr_payload (transizione di regime
  ricostruito->official gestita LI'; lezione fase 1b lotto 2);
- mensile = composto dei rendimenti daily del mese di calendario; il PRIMO mese
  e' dichiarato parziale se la base della serie cade dentro quel mese, l'ULTIMO
  e' sempre dichiarato parziale (in corso);
- rolling SOLO su finestra piena: meno osservazioni della finestra = blocco
  assente e nota dichiarata, mai una "vol a 5 punti";
- drawdown: episodio = dal massimo al recupero del massimo; episodio APERTO
  dichiarato con recovery null (e' il drawdown corrente);
- metriche scalari: advanced_metrics.portfolio_metrics (fonte unica; serie e
  allineamento benchmark dichiarati nel suo _source), mai duplicate qui.
"""
import math
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

TRADING_DAYS = 252          # stessa convenzione di advanced_metrics
ROLLING_WINDOWS = (30, 90)  # giorni di borsa
TOP_DRAWDOWNS = 5

_CACHE: Dict[str, Any] = {}
CACHE_TTL_SEC = 600  # 10 min, come attribution/analytics


def _log(msg: str):
    try:
        print(f"[TEARSHEET] {msg}", flush=True)
    except OSError:
        pass


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs)


def _std(xs: List[float]) -> Optional[float]:
    """Dev. standard campionaria (ddof=1); None se non calcolabile."""
    n = len(xs)
    if n < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _monthly_table(r_dates: List[str], rets: List[float],
                   base_date: str) -> Dict[str, Any]:
    """Rendimenti mensili composti + aggregato per anno, parzialita' dichiarata."""
    months: List[Dict[str, Any]] = []
    cur_key, factor, n = None, 1.0, 0
    for d, r in zip(r_dates, rets):
        key = d[:7]
        if key != cur_key:
            if cur_key is not None:
                months.append({"month": cur_key, "return_pct": round((factor - 1) * 100, 2),
                               "n_days": n, "partial": False, "_factor": factor})
            cur_key, factor, n = key, 1.0, 0
        factor *= (1.0 + r)
        n += 1
    if cur_key is not None:
        months.append({"month": cur_key, "return_pct": round((factor - 1) * 100, 2),
                       "n_days": n, "partial": False, "_factor": factor})
    if months:
        # primo mese parziale se la BASE della serie cade nello stesso mese
        # (la serie non copre il mese dal suo primo giorno di borsa)
        if (base_date or "")[:7] == months[0]["month"]:
            months[0]["partial"] = True
        months[-1]["partial"] = True   # ultimo mese: in corso, dichiarato

    # review 1c (M1): l'anno si compone dai fattori NON arrotondati (comporre i
    # return_pct a 2 decimali derivava fino a ~0,06pp/anno di drift zitto).
    years: List[Dict[str, Any]] = []
    ykey, yfactor, ypartial, ymonths = None, 1.0, False, 0
    for m in months:
        y = m["month"][:4]
        if y != ykey:
            if ykey is not None:
                years.append({"year": ykey, "return_pct": round((yfactor - 1) * 100, 2),
                              "partial": ypartial or ymonths < 12})
            ykey, yfactor, ypartial, ymonths = y, 1.0, False, 0
        yfactor *= m["_factor"]
        ypartial = ypartial or m["partial"]
        ymonths += 1
    if ykey is not None:
        # anno parziale anche se copre MENO di 12 mesi (coperture dichiarate)
        years.append({"year": ykey, "return_pct": round((yfactor - 1) * 100, 2),
                      "partial": ypartial or ymonths < 12})
    for m in months:
        m.pop("_factor")
    return {"months": months, "years": years}


def _drawdown_episodes(dates: List[str], idx: List[float]) -> Dict[str, Any]:
    """Episodi di drawdown dall'indice TWR: dal massimo al recupero del massimo.
    Episodio aperto = drawdown corrente, recovery null DICHIARATO."""
    episodes: List[Dict[str, Any]] = []
    peak, peak_i = idx[0], 0
    cur: Optional[Dict[str, Any]] = None
    for t in range(1, len(idx)):
        v = idx[t]
        if v >= peak:
            if cur is not None:
                cur["recovery_date"] = dates[t]
                cur["days_total"] = t - cur.pop("_peak_i")
                episodes.append(cur)
                cur = None
            peak, peak_i = v, t
            continue
        dd = v / peak - 1.0
        if cur is None:
            cur = {"start_date": dates[peak_i], "_peak_i": peak_i,
                   "trough_date": dates[t], "depth_pct": dd,
                   "days_to_trough": t - peak_i,
                   "recovery_date": None, "days_total": None, "open": False}
        elif dd < cur["depth_pct"]:
            cur["depth_pct"] = dd
            cur["trough_date"] = dates[t]
            cur["days_to_trough"] = t - cur["_peak_i"]
    current = None
    if cur is not None:
        cur["days_total"] = (len(idx) - 1) - cur.pop("_peak_i")   # finora, aperto
        cur["open"] = True   # review 1c (B1): flag anche nella voce in top,
        episodes.append(cur)  # 'current' e' una VISTA della stessa (dichiarato)
        current = {"start_date": cur["start_date"], "trough_date": cur["trough_date"],
                   "depth_pct": round(cur["depth_pct"] * 100, 2),
                   "days_to_trough": cur["days_to_trough"],
                   "days_total": cur["days_total"], "open": True,
                   "current_dd_pct": round((idx[-1] / peak - 1.0) * 100, 2)}
    for e in episodes:
        e["depth_pct"] = round(e["depth_pct"] * 100, 2)
    episodes.sort(key=lambda e: e["depth_pct"])
    return {"top": episodes[:TOP_DRAWDOWNS], "current": current,
            "n_episodes_total": len(episodes)}


def _rolling(r_dates: List[str], rets: List[float], window: int,
             rf_daily: float) -> Optional[Dict[str, Any]]:
    """Vol annualizzata e Sharpe su finestra PIENA. None se le osservazioni
    non bastano (dichiarato dal chiamante, mai una finestra accorciata zitta)."""
    n = len(rets)
    if n < window:
        return None
    dates_out, vol_out, sharpe_out = [], [], []
    for j in range(window - 1, n):
        w = rets[j - window + 1: j + 1]
        sd = _std(w)
        dates_out.append(r_dates[j])
        if sd is None:
            vol_out.append(None)      # non calcolabile: dichiarato, mai 0 finto
            sharpe_out.append(None)
            continue
        # review 1c (B3): vol 0 e' un valore VERO (finestra costante); e' lo
        # Sharpe a non essere calcolabile (div/0) -> null dichiarato solo li'.
        vol_out.append(round(sd * math.sqrt(TRADING_DAYS) * 100, 2))
        sharpe_out.append(None if sd == 0 else
                          round((_mean(w) - rf_daily) / sd * math.sqrt(TRADING_DAYS), 2))
    return {"window_days": window, "dates": dates_out,
            "vol_annual_pct": vol_out, "sharpe": sharpe_out}


def compute_tearsheet(twr_payload: Optional[Dict[str, Any]] = None,
                      metrics: Optional[Dict[str, Any]] = None,
                      rolling_windows=ROLLING_WINDOWS,
                      rf_annual: Optional[float] = None,
                      force: bool = False) -> Dict[str, Any]:
    """Tearsheet sulla serie TWR ufficiale. twr_payload/metrics/rf iniettabili
    per i test (default: twr_engine + advanced_metrics + market_inputs)."""
    injected = twr_payload is not None

    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"tearsheet:{today}:{tuple(rolling_windows)}"
    if not injected and not force and cache_key in _CACHE:
        ent = _CACHE[cache_key]
        if time.time() - ent["ts"] < CACHE_TTL_SEC:
            return ent["data"]

    if twr_payload is None:
        try:
            from bellomberg.portfolio.twr_engine import compute_twr_payload
            twr_payload = compute_twr_payload(force=force)
        except Exception as e:
            return {"error": f"twr_engine non disponibile: {e}"}
    if twr_payload.get("error"):
        return {"error": "serie TWR ufficiale non disponibile: " + str(twr_payload["error"])}
    dates = [str(d)[:10] for d in (twr_payload.get("dates") or [])]
    idx = twr_payload.get("twr_index") or []
    if len(dates) != len(idx) or len(idx) < 2:
        return {"error": f"serie TWR insufficiente ({len(idx)} punti)"}
    if any((not isinstance(v, (int, float))) or v != v or v <= 0 for v in idx):
        return {"error": "twr_index con valori non validi (<=0/NaN): base dati corrotta"}

    rets = [idx[t] / idx[t - 1] - 1.0 for t in range(1, len(idx))]
    r_dates = dates[1:]

    if rf_annual is None:
        try:
            from bellomberg.market_data.market_inputs import get_risk_free
            _rf = get_risk_free("EUR")
            # review 1c (B6): un tasso legittimo 0.0 NON deve diventare il ripiego
            rf_annual = 0.03 if _rf is None else float(_rf)
        except Exception:
            rf_annual = 0.03   # stesso ripiego dichiarato di twr_engine
    rf_daily = (1.0 + rf_annual) ** (1.0 / TRADING_DAYS) - 1.0

    notes: List[str] = []
    monthly = _monthly_table(r_dates, rets, base_date=dates[0])
    dd = _drawdown_episodes(dates, idx)

    rolling: Dict[str, Any] = {}
    for w in rolling_windows:
        blk = _rolling(r_dates, rets, int(w), rf_daily)
        if blk is None:
            notes.append(f"rolling {w}gg: solo {len(rets)} osservazioni (<{w}) "
                         "-> blocco assente, dichiarato (mai finestre accorciate)")
        else:
            rolling[f"w{w}"] = blk

    # metriche scalari: fonte unica advanced_metrics (mai duplicate qui)
    if metrics is None:
        if injected:
            metrics = {"error": "metriche non richieste (iniezione test senza metrics)"}
        else:
            try:
                from bellomberg.portfolio.advanced_metrics import portfolio_metrics
                metrics = portfolio_metrics()
            except Exception as e:
                metrics = {"error": f"advanced_metrics non disponibile: {e}"}
    if metrics.get("error"):
        notes.append("metriche scalari n.d.: " + str(metrics["error"]))
    elif "LEGACY" in str(metrics.get("_source", "")):
        # review 1c (B5): il fallback legacy di advanced_metrics (serie contaminata
        # dai flussi) e' dichiarato solo nel suo _source: qui va urlato, perche'
        # mensili/drawdown restano sul TWR -> due serie DIVERSE nel payload.
        notes.append("ATTENZIONE: metriche scalari su serie LEGACY (contaminata dai "
                     "flussi) mentre mensili/drawdown sono sul TWR ufficiale: due "
                     "serie DIVERSE nello stesso payload — non confrontarle")

    out = {
        "period": {"start": dates[0], "end": dates[-1], "n_trading_days": len(rets)},
        "monthly": monthly["months"],
        "yearly": monthly["years"],
        "drawdowns": dd,
        "rolling": rolling,
        "metrics": metrics,
        "regime_summary": twr_payload.get("regime_summary"),
        "risk_free_used": rf_annual,
        "notes": notes,
        "basis": ("TEARSHEET sulla serie TWR UFFICIALE (twr_index di twr_engine, "
                  "transizione di regime gestita); mensili composti dai daily con "
                  "parzialita' dichiarata (primo mese se la base cade nel mese, "
                  "ultimo sempre in-corso); rolling solo su finestra piena; "
                  "drawdown = da massimo a recupero, episodio aperto dichiarato; "
                  "metriche scalari RIUSATE da advanced_metrics (benchmark SPY-EUR "
                  "dichiarato li'), niente QuantStats per scelta PM 23/07. NB: "
                  "durate dei drawdown in GIORNI DI BORSA (non calendario); "
                  "'current' e' una vista dell'episodio aperto gia' in top (flag "
                  "open, non doppio conteggio); eventuali null nei rolling = non "
                  "calcolabile dichiarato; il max_drawdown_pct delle metriche puo' "
                  "divergere di ~0,1pp dagli episodi qui (arrotondamenti "
                  "indipendenti dello stesso indice, dichiarato)"),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "_source": "portfolio_tearsheet.compute_tearsheet",
    }
    if not injected:
        # review 1c (B4): purge delle chiavi dei giorni passati (backend
        # long-running: niente payload orfani che si accumulano)
        for k in [k for k in _CACHE if not k.startswith(f"tearsheet:{today}:")]:
            _CACHE.pop(k, None)
        _CACHE[cache_key] = {"ts": time.time(), "data": out}
    return out


if __name__ == "__main__":
    import json as _json
    o = compute_tearsheet()
    print(_json.dumps(o, ensure_ascii=False, indent=1)[:4000])
