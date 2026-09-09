"""
iv_history.py — IV HISTORY DB + IV RANK VERO (voce quant P1, ok PM 25/07 sera).

Il problema: senza IV storica salvata non esiste un IV Rank vero, e le decisioni
di vendita premio (covered call ecc.) restano senza contesto di valutazione vol.
Qui: (1) un COLLECTOR che salva un'istantanea AL GIORNO per ticker della term
structure IV (ATM/RR25/BF25 per expiry, riusando vol_surface.build_vol_surface
su Polygon — zero calcoli nuovi), (2) l'IV RANK come percentile dell'ATM IV
front di oggi contro lo storico raccolto.

Convenzioni DICHIARATE (regola no-fallback 14/07):
- lo storico parte VUOTO e matura nel tempo: n_obs sempre nel payload, e sotto
  YOUNG_THRESHOLD_OBS il rank e' dichiarato "giovane" — mai spacciato per maturo;
- weekend: SKIP dichiarato (le chain Polygon sono ferme al venerdi': salvare
  sabato/domenica duplicherebbe la chiusura e falserebbe il percentile). I
  festivi US infrasettimanali possono duplicare la chiusura precedente: raro,
  accettato e documentato qui;
- errore del provider (Polygon giu', key mancante) = log dichiarato e NESSUNA
  riga scritta — mai una riga inventata;
- idempotenza: UNIQUE(ticker, snap_date, expiry) + INSERT OR IGNORE = vince la
  prima foto del giorno (il task gira ogni 15'; le run successive non riscrivono);
- "front" = stessa convenzione di vol_surface: prima expiry con days >= 2
  (gli 0DTE distorcono), fallback alla piu' vicina;
- percentile = % dei giorni storici (incluso oggi) con ATM IV front <= corrente,
  formula dichiarata nel payload. In piu' min/max per il rank classico.
"""
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

def stato_iv_tickers() -> Dict[str, Any]:
    """{"tickers": (...), "origine", "motivo"}: l'esito del caricamento del negozio privato
    (negozi_privati.carica_iv: data/iv_tickers.json, forma in iv_tickers.example.json), riletto
    a ogni chiamata, per DICHIARARE origine e motivo dove la lista serve."""
    from bellomberg.storage.negozi_privati import carica_iv
    return carica_iv()


def iv_tickers() -> Sequence[str]:
    """La lista DICHIARATA dei simboli con IV storica (i nomi con opzioni/premio in book piu' il
    benchmark vol di mercato). Negozio assente o illeggibile = tupla VUOTA: chi la usa dichiara
    origine e motivo con `stato_iv_tickers`, mai una lista di ripiego."""
    return stato_iv_tickers()["tickers"]
FRONT_MIN_DAYS = 2            # come vol_surface: 0DTE fuori dalla lettura
YOUNG_THRESHOLD_OBS = 60      # ~3 mesi di borsa: sotto, rank dichiarato giovane
MAX_EXPIRIES = 6
SOURCE_TAG = "vol_surface v1.5 (Polygon chains, spot yfinance)"
# Review A1 (25/07): il task PriceUpdater gira ogni 15' H24 (misurato: repeat
# infinito, non "8h" come nel commento del .bat) -> senza guardia la prima run
# del giorno (~00:05) salverebbe la chiusura di IERI etichettata OGGI. Foto solo
# dalle 16 locali (~10:05 ET, mercato US aperto anche nelle settimane di
# disallineamento DST): etichetta vera, ora di campionamento costante, e il PC
# e' tipicamente acceso (alle 22 potrebbe essere spento = buchi nella serie).
COLLECT_FROM_HOUR = 16


def _log(msg: str):
    try:
        print(f"[IV] {msg}", flush=True)
    except OSError:
        pass


def _now_hour() -> int:
    """Ora locale corrente (separata per i test)."""
    return datetime.now().hour


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    if db_path is None:
        from bellomberg.storage.memory_db import SQLITE_PATH
        db_path = SQLITE_PATH
    # quick-win audit/21 §6 n.2: era l'unica via raw post-giugno senza i PRAGMA
    # di progetto (WAL, busy_timeout, FK) — ora passa dal connettore comune
    from bellomberg.storage.memory_db import connect_sqlite
    return connect_sqlite(db_path, timeout=15)


def save_daily_snapshot(db_path: Optional[str] = None,
                        tickers: Optional[Sequence[str]] = None,
                        snap_date: Optional[str] = None,
                        force: bool = False) -> Dict[str, Any]:
    """Salva l'istantanea IV del giorno per i ticker della lista. Guard interni:
    weekend = SKIP dichiarato; prima delle COLLECT_FROM_HOUR locali = SKIP
    dichiarato (review A1: a mercato US chiuso le chain sono la chiusura di
    IERI — mai etichettarla oggi); giorno gia' salvato = SKIP (idempotente).
    `snap_date`/`force` servono a collaudo/backfill manuale dichiarato — NB
    backfill: i `days` per expiry vengono da vol_surface e sono relativi alla
    data di RUN, non a snap_date (review B1, scarto di pochi giorni tollerato
    e dichiarato qui)."""
    today = snap_date or date.today().isoformat()
    try:
        dow = datetime.strptime(today, "%Y-%m-%d").weekday()
    except ValueError:
        return {"error": f"snap_date non valida: {today}"}
    out: Dict[str, Any] = {"snap_date": today, "saved": {}, "skipped": {},
                           "errors": {}}
    if tickers is None:
        # la lista e' un DATO del negozio privato: assente o illeggibile = nessuna foto e
        # l'esito lo dice (regola 14/07), mai una lista di ripiego
        st = stato_iv_tickers()
        if st["origine"] in ("assente", "illeggibile"):
            out["error"] = ("negozio iv_tickers %s: %s — nessuna foto salvata"
                            % (st["origine"], st["motivo"]))
            return out
        tickers = st["tickers"]
    if dow >= 5 and not force:
        out["skipped"]["_weekend"] = ("sabato/domenica: chain ferme al venerdi', "
                                      "snapshot non salvato (dichiarato)")
        return out
    if snap_date is None and not force and _now_hour() < COLLECT_FROM_HOUR:
        out["skipped"]["_pre_open"] = (
            f"prima delle {COLLECT_FROM_HOUR}:00 locali il mercato US non e' "
            "aperto e le chain sono la chiusura di IERI: foto rinviata (dichiarato)")
        return out

    try:
        conn = _connect(db_path)
    except Exception as e:
        return {"error": "DB non raggiungibile: " + str(e)}
    try:
        for t in tickers:
            t = t.upper()
            if not force:
                row = conn.execute(
                    "SELECT COUNT(*) FROM iv_history WHERE ticker=? AND snap_date=?",
                    (t, today)).fetchone()
                if row and row[0] > 0:
                    out["skipped"][t] = f"gia' salvato oggi ({row[0]} expiry)"
                    continue
            try:
                from bellomberg.portfolio.vol_surface import build_vol_surface
                vs = build_vol_surface(t, max_expiries=MAX_EXPIRIES)
            except Exception as e:
                out["errors"][t] = "vol_surface: " + str(e)
                continue
            if vs.get("error"):
                # provider giu'/key mancante: buco DICHIARATO, nessuna riga inventata
                out["errors"][t] = str(vs["error"])
                continue
            n, attempted = 0, 0
            for s in vs.get("term_structure") or []:
                if s.get("atm_iv") is None:
                    continue
                attempted += 1
                cur = conn.execute(
                    "INSERT OR IGNORE INTO iv_history "
                    "(ticker, snap_date, expiry, days, atm_iv, rr25, bf25, "
                    " spot, spot_source, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (t, today, s["expiry"], int(s["days"]), float(s["atm_iv"]),
                     s.get("rr25"), s.get("bf25"), vs.get("spot_est"),
                     vs.get("spot_source"), SOURCE_TAG))
                n += cur.rowcount
            conn.commit()
            if n > 0:
                out["saved"][t] = n
                _log(f"{t}: salvate {n} expiry per {today}")
            elif attempted > 0:
                # review M2: force su giorno gia' presente = OR IGNORE a 0 righe;
                # e' uno skip, NON un errore provider (diagnosi vera, non falsa)
                out["skipped"][t] = f"righe gia' presenti per {today} (OR IGNORE)"
            else:
                out["errors"][t] = "nessuna expiry con ATM IV valida dal provider"
    finally:
        conn.close()
    return out


def _front_series(rows: List[tuple]) -> Dict[str, float]:
    """Da (snap_date, days, atm_iv) -> ATM IV FRONT per giorno: prima expiry
    con days >= FRONT_MIN_DAYS, fallback alla piu' vicina."""
    by_day: Dict[str, List[tuple]] = {}
    for d, days, iv in rows:
        by_day.setdefault(d, []).append((int(days), float(iv)))
    front: Dict[str, float] = {}
    for d, lst in by_day.items():
        lst.sort()
        eligible = [x for x in lst if x[0] >= FRONT_MIN_DAYS]
        front[d] = (eligible[0][1] if eligible else lst[0][1])
    return front


def get_iv_context(ticker: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """IV Rank dallo storico raccolto. Sempre dichiarati: n_obs, finestra,
    formula; young=True sotto YOUNG_THRESHOLD_OBS. Storia insufficiente o
    tabella assente = error dichiarato, mai un rank inventato."""
    base = {"ticker": ticker.upper(),
            "basis": ("percentile = % giorni storici (incluso oggi) con ATM IV "
                      f"front <= corrente; front = prima expiry >= {FRONT_MIN_DAYS}g; "
                      "floor strutturale 100/n a storia corta (v. young)"),
            "young_threshold_obs": YOUNG_THRESHOLD_OBS,
            "_source": "iv_history.get_iv_context"}
    try:
        conn = _connect(db_path)
    except Exception as e:
        return {**base, "error": "DB non raggiungibile: " + str(e)}
    try:
        try:
            rows = conn.execute(
                "SELECT snap_date, days, atm_iv FROM iv_history "
                "WHERE ticker=? ORDER BY snap_date", (ticker.upper(),)).fetchall()
        except sqlite3.OperationalError as e:
            # tabella assente = migrazione 6 non ancora applicata: dichiarato
            return {**base, "error": "iv_history non disponibile: " + str(e)}
    finally:
        conn.close()
    front = _front_series(rows)
    if len(front) < 2:
        return {**base, "n_obs": len(front),
                "error": ("storia insufficiente per un percentile "
                          f"({len(front)} osservazioni, minime 2)")}
    days_sorted = sorted(front)
    series = [front[d] for d in days_sorted]
    current = series[-1]
    pct = 100.0 * sum(1 for v in series if v <= current) / len(series)
    return {**base,
            "iv_front_current": round(current, 4),
            "iv_percentile": round(pct, 1),
            "iv_min": round(min(series), 4),
            "iv_max": round(max(series), 4),
            "n_obs": len(series),
            "young": len(series) < YOUNG_THRESHOLD_OBS,
            "history_from": days_sorted[0],
            "last_snap_date": days_sorted[-1],
            "error": None}


if __name__ == "__main__":
    import json
    r = save_daily_snapshot()
    print(json.dumps(r, indent=2))
    for t in iv_tickers():
        print(t, json.dumps(get_iv_context(t), indent=2))
