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
- rank (dal 09/10, A2 audit Vol Deck) su IV a scadenza COSTANTE 30g interpolata
  in varianza totale fra le scadenze a cavallo; il "front" (prima expiry con
  days >= 2, fallback alla piu' vicina) resta solo come informazione secondaria;
- percentile = % dei giorni storici (incluso oggi) con IV 30g <= corrente,
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
# 09/10 (A1 audit Vol Deck, Opus 5.5): dalla v1.6 la superficie legge la chain
# COMPLETA e l'ATM e' interpolata a K/S=1; le righe v1.5 venivano dal ramo
# campionato troncato ai 400 strike piu' bassi. Il tag distingue le due serie e
# get_iv_context conta le osservazioni costruite su righe v1.5.
SOURCE_TAG = "vol_surface v1.6 (Polygon chains complete, ATM interp K/S spot)"
LEGACY_SOURCE_PREFIX = "vol_surface v1.5"
PARTIAL_MARK = " | PARTIAL: "
SPOT_FALLBACK_MARK = " | SPOT_FALLBACK: "
# MA-2 (review v2, 10/10, Opus 5.5): il rank esclude DI DEFAULT le righe v1.5
# (chain troncata: ATM spostata di ~3,5 punti), PARTIAL e SPOT_FALLBACK (spot di un
# istante diverso dalle quote), contandole; sotto MIN_RANK_OBS giorni puliti il
# rank e' «storia insufficiente» (un percentile su pochi giorni non e' un rank).
MIN_RANK_OBS = 20             # ~un mese di borsa
# A2 audit Vol Deck: il rank si fa su IV a SCADENZA COSTANTE, non sul front che
# scorre da 6 a 2 giorni e poi salta (term structure identica = rank 20..100).
TENOR_DAYS = 30


def constant_maturity_iv(points, tenor_days: int = TENOR_DAYS,
                         min_days: int = FRONT_MIN_DAYS) -> Dict[str, Any]:
    """IV a scadenza costante `tenor_days` (giorni di calendario) interpolata in
    VARIANZA TOTALE: w(T) = sigma^2 * T lineare in T fra le due scadenze a cavallo
    (convenzione standard, nessun arbitraggio di calendario introdotto se w e'
    crescente). Mai estrapolata: senza scadenze a cavallo -> iv None con motivo.

    points: iterabile di (days, iv) — days di calendario, iv frazione.
    Ritorna {"iv", "days_low", "days_high", "reason", "calendar_arbitrage"}."""
    pts = sorted((int(d), float(v)) for d, v in points
                 if d is not None and v is not None and int(d) >= min_days and float(v) > 0)
    out = {"iv": None, "days_low": None, "days_high": None, "reason": None,
           "calendar_arbitrage": False}
    exact = [v for d, v in pts if d == tenor_days]
    if exact:
        out.update(iv=exact[0], days_low=tenor_days, days_high=tenor_days)
        return out
    below = [p for p in pts if p[0] < tenor_days]
    above = [p for p in pts if p[0] > tenor_days]
    if not below or not above:
        lato = "nessuna scadenza sotto" if not below else "nessuna scadenza sopra"
        out["reason"] = (f"IV {tenor_days}g non interpolabile: {lato} i {tenor_days} giorni "
                         f"(scadenze >= {min_days}g disponibili: {[d for d, _ in pts]}); mai estrapolata")
        return out
    (d1, s1), (d2, s2) = below[-1], above[0]
    w1, w2 = s1 * s1 * d1, s2 * s2 * d2
    w = w1 + (w2 - w1) * (tenor_days - d1) / (d2 - d1)
    out.update(days_low=d1, days_high=d2, calendar_arbitrage=w2 < w1)
    if w <= 0:
        out["reason"] = f"varianza totale interpolata non positiva fra {d1}g e {d2}g"
        return out
    out["iv"] = (w / tenor_days) ** 0.5
    return out
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
        # 05/10: mai il testo dell'eccezione nei messaggi (puo' portare URL con chiave)
        return {"error": "DB non raggiungibile: " + type(e).__name__}
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
                # 05/10: il TIPO, mai str(e): un'eccezione di requests porta l'URL con apiKey
                out["errors"][t] = "vol_surface: " + type(e).__name__
                continue
            if vs.get("error"):
                # provider giu'/key mancante: buco DICHIARATO, nessuna riga inventata
                out["errors"][t] = str(vs["error"])
                continue
            if vs.get("spot_proxy") or vs.get("metrics_qualified") is False:
                # R02 punto 4 (10/10, Opus 5.5): un'ATM calcolata su uno strike usato
                # come spot NON entra nello storico del rank, nemmeno marcata.
                out["errors"][t] = ("spot proxy (%s): ATM non qualificata, nessuna riga salvata"
                                    % (vs.get("spot_proxy") or "non qualificato"))
                continue
            n, attempted = 0, 0
            # A1: una scadenza con chain incompleta resta salvata ma MARCATA nella
            # colonna source (nessuna migrazione di schema): get_iv_context la conta.
            partial = {r.get("expiry"): (r.get("reason") or "chain incompleta")
                       for r in ((vs.get("coverage") or {}).get("rows") or [])
                       if r.get("status") == "partial"}
            for s in vs.get("term_structure") or []:
                if s.get("atm_iv") is None:
                    continue
                attempted += 1
                tag = SOURCE_TAG + (PARTIAL_MARK + str(partial[s["expiry"]])[:120]
                                    if s.get("expiry") in partial else "")
                if vs.get("spot_fallback"):
                    tag += SPOT_FALLBACK_MARK + "yfinance"   # spot non della stessa istantanea
                cur = conn.execute(
                    "INSERT OR IGNORE INTO iv_history "
                    "(ticker, snap_date, expiry, days, atm_iv, rr25, bf25, "
                    " spot, spot_source, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (t, today, s["expiry"], int(s["days"]), float(s["atm_iv"]),
                     s.get("rr25"), s.get("bf25"), vs.get("spot_est"),
                     vs.get("spot_source"), tag))
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


def _percentile_leq(series: List[float], current: float) -> float:
    return 100.0 * sum(1 for v in series if v <= current) / len(series)


def _row_flag(src: str) -> Optional[str]:
    """Motivo per cui una riga NON entra nel rank (MA-2), o None."""
    if src.startswith(LEGACY_SOURCE_PREFIX):
        return "legacy_v15"
    if PARTIAL_MARK in src:
        return "partial"
    if SPOT_FALLBACK_MARK in src:
        return "spot_fallback"
    return None


def get_iv_context(ticker: str, db_path: Optional[str] = None, *,
                   min_obs: int = MIN_RANK_OBS, include_flagged: bool = False) -> Dict[str, Any]:
    """IV Rank dallo storico raccolto. Sempre dichiarati: n_obs, finestra,
    formula; young=True sotto YOUNG_THRESHOLD_OBS. Storia insufficiente o
    tabella assente = error dichiarato, mai un rank inventato.

    09/10 (A2 audit Vol Deck, Opus 5.5): il rank (`iv_percentile`) e' calcolato
    sull'IV a SCADENZA COSTANTE di TENOR_DAYS giorni, interpolata in varianza
    totale dalle righe per scadenza gia' salvate (vale anche sullo storico). Un
    giorno senza scadenze a cavallo dei 30g e' ESCLUSO e contato, mai estrapolato;
    se manca proprio l'ultima foto il rank e' n.d. (error), non quello di ieri.
    Il front grezzo resta come informazione SECONDARIA (`iv_front_*`): il suo
    tenor scorre (6..2 giorni) e il suo percentile non e' un rank di volatilita'.

    10/10 (MA-2 review v2, Opus 5.5): righe v1.5, PARTIAL e SPOT_FALLBACK ESCLUSE
    di default dal rank (prima contate ma usate: 22 giorni v1.5 con ATM +3,5 punti
    davano percentile 33 invece di ~100), conteggi in `excluded_rows`/`excluded_days`;
    `include_flagged=True` le rimette (diagnostica, dichiarato in `flagged_included`).
    Sotto `min_obs` giorni con IV 30g pulita: «storia insufficiente» (error)."""
    base = {"ticker": ticker.upper(),
            "basis": (f"percentile = % giorni storici (incluso oggi) con IV a scadenza costante "
                      f"{TENOR_DAYS}g <= corrente; IV {TENOR_DAYS}g = interpolazione lineare della "
                      "varianza totale sigma^2*T fra le due scadenze a cavallo (mai estrapolata); "
                      "floor strutturale 100/n a storia corta (v. young)"),
            "tenor_days": TENOR_DAYS,
            "young_threshold_obs": YOUNG_THRESHOLD_OBS,
            "_source": "iv_history.get_iv_context"}
    try:
        conn = _connect(db_path)
    except Exception as e:
        return {**base, "error": "DB non raggiungibile: " + type(e).__name__}
    try:
        try:
            rows = conn.execute(
                "SELECT snap_date, days, atm_iv, source FROM iv_history "
                "WHERE ticker=? ORDER BY snap_date", (ticker.upper(),)).fetchall()
        except sqlite3.OperationalError as e:
            # tabella assente = migrazione 6 non ancora applicata: dichiarato
            return {**base, "error": "iv_history non disponibile: " + type(e).__name__}
    finally:
        conn.close()
    by_day: Dict[str, List[tuple]] = {}
    for d, days, iv, src in rows:
        by_day.setdefault(d, []).append((int(days), float(iv), str(src or "")))
    series30: Dict[str, float] = {}
    excluded: Dict[str, str] = {}
    partial_days, legacy_days, arb_days = set(), set(), set()
    excluded_rows = {"legacy_v15": 0, "partial": 0, "spot_fallback": 0}
    excluded_days = {"legacy_v15": 0, "partial": 0, "spot_fallback": 0}
    flag_excluded = set()
    for d, lst in by_day.items():
        flags = [_row_flag(x[2]) for x in lst]
        for f in flags:
            if f:
                excluded_rows[f] += 1
        clean = lst if include_flagged else [x for x, f in zip(lst, flags) if f is None]
        cm = constant_maturity_iv([(x[0], x[1]) for x in clean])
        if cm["iv"] is None:
            day_flags = sorted({f for f in flags if f})
            if day_flags and not include_flagged:
                # giorno perso PER le righe escluse: contato sotto il suo motivo
                for f in day_flags:
                    excluded_days[f] += 1
                flag_excluded.add(d)
                excluded[d] = ("righe escluse dal rank (%s): IV %dg non ricostruibile dalle righe pulite"
                               % (", ".join(day_flags), TENOR_DAYS))
            else:
                excluded[d] = cm["reason"]
            continue
        lst = clean
        # arrotondata a 1e-6: lo stesso livello di vol ricostruito per interpolazione
        # differisce per rumore float (1e-17) e il confronto <= del percentile lo
        # trasformava in rank 33 invece di 100 (misurato nel test di stabilita')
        series30[d] = round(cm["iv"], 6)
        used = [x for x in lst if x[0] in (cm["days_low"], cm["days_high"])]
        if any(PARTIAL_MARK in x[2] for x in used):
            partial_days.add(d)
        if any(x[2].startswith(LEGACY_SOURCE_PREFIX) for x in used):
            legacy_days.add(d)
        if cm["calendar_arbitrage"]:
            arb_days.add(d)
    # front grezzo: SECONDARIO, tenor scorrevole (dichiarato)
    front = _front_series([(d, x[0], x[1]) for d, lst in by_day.items() for x in lst])
    secondary: Dict[str, Any] = {}
    if front:
        fdays = sorted(front)
        fseries = [front[d] for d in fdays]
        last = by_day[fdays[-1]]
        eligible = sorted(x[0] for x in last if x[0] >= FRONT_MIN_DAYS)
        secondary = {"iv_front_current": round(fseries[-1], 4),
                     "iv_front_days": eligible[0] if eligible else min(x[0] for x in last),
                     "iv_front_percentile": round(_percentile_leq(fseries, fseries[-1]), 1) if len(fseries) >= 2 else None,
                     "front_basis": (f"SECONDARIO: ATM della prima scadenza >= {FRONT_MIN_DAYS}g, il cui tenor "
                                     "scorre ogni giorno; il suo percentile mescola tenor diversi e non e' l'IV rank")}
    declared = {**base, **secondary,
                "n_days_excluded_no_bracket": len(excluded) - len(flag_excluded),
                "excluded_dates": sorted(excluded)[-10:],
                "excluded_rows": excluded_rows,
                "excluded_days": excluded_days,
                "n_days_excluded_flagged": len(flag_excluded),
                "flagged_included": bool(include_flagged),
                "min_obs": min_obs,
                "exclusion_basis": ("escluse di default dal rank: righe v1.5 (chain troncata), PARTIAL "
                                    "(chain incompleta), SPOT_FALLBACK (spot di un istante diverso dalle "
                                    "quote); un giorno senza IV 30g dalle righe pulite e' escluso e contato")}
    last_day = max(by_day) if by_day else None
    if last_day is not None and last_day not in series30:
        return {**declared, "n_obs": len(series30), "last_snap_date": last_day,
                "error": f"IV {TENOR_DAYS}g costante n.d. per l'ultima foto ({last_day}): {excluded[last_day]}"}
    if len(series30) < max(2, min_obs):
        return {**declared, "n_obs": len(series30),
                "error": ("storia insufficiente per un percentile "
                          f"({len(series30)} osservazioni IV {TENOR_DAYS}g pulite, minime {max(2, min_obs)})")}
    days_sorted = sorted(series30)
    series = [series30[d] for d in days_sorted]
    current = series[-1]
    return {**declared,
            "iv_30d_current": round(current, 4),
            "iv_percentile": round(_percentile_leq(series, current), 1),
            "iv_min": round(min(series), 4),
            "iv_max": round(max(series), 4),
            "n_obs": len(series),
            "young": len(series) < YOUNG_THRESHOLD_OBS,
            "history_from": days_sorted[0],
            "last_snap_date": days_sorted[-1],
            "n_obs_partial": len(partial_days),
            "n_obs_legacy_v15": len(legacy_days),
            "n_obs_calendar_arbitrage": len(arb_days),
            "current_partial": days_sorted[-1] in partial_days,
            "current_legacy_v15": days_sorted[-1] in legacy_days,
            "error": None}


if __name__ == "__main__":
    import json
    r = save_daily_snapshot()
    print(json.dumps(r, indent=2))
    for t in iv_tickers():
        print(t, json.dumps(get_iv_context(t), indent=2))
