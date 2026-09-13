"""
scorekeeper.py — SCOREKEEPER #190/#211 (Fase 2, loop che apprende).

Job in PURO CODICE (niente LLM): per ogni decisione direzionale del comitato
calcola l'esito di MERCATO a t+1w/t+4w rispetto al prezzo del giorno della
decisione, e aggrega hit-rate per AZIONE, per CONFIDENCE (la promessa di
capo.py: "la confidence verra' verificata a posteriori sullo scorecard") e
PER SPECIALISTA (firma = lo specialista ha citato il ticker nei suoi report
dello stesso memo). Il blocco TRACK RECORD viene iniettato nella memoria del
Capo e degli specialisti: il sistema smette di ricordare solo COSA ha detto
e inizia a sapere SE aveva ragione.

REGOLE DI MISURA (dichiarate nel blocco):
- direzione: BUY*/ADD* = long (hit se il prezzo SALE); SELL*/TRIM*/EXIT*/
  SHORT* = de-risk (hit se il prezzo SCENDE = perdita evitata).
- HOLD/HEDGE/RESEARCH e ticker non-quotabili: esclusi (non misurabili
  direzionalmente) — conteggiati come "non misurabili".
- orizzonte primario t+4w; se non ancora osservabile si usa t+1w e lo si
  dichiara; decisioni piu' giovani di 7gg escluse.
- rendimenti in valuta di QUOTAZIONE (percentuali scale-invariant: misurano
  la DIREZIONE della call, non il P&L EUR del conto).
- e' l'esito di MERCATO della raccomandazione, indipendente dallo status
  (una SKIPPED che poi sale era comunque una call giusta).

Snapshot runtime in data/scorekeeper_snapshot.json (niente tabelle nuove);
ricalcolo se piu' vecchio di SNAP_TTL_H ore o su force.
"""
import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bellomberg.core.paths import DATA_DIR

SNAP_PATH = str(DATA_DIR / "scorekeeper_snapshot.json")
SNAP_TTL_H = 12.0
DEGRADED_COOLDOWN_H = 0.25   # riuso breve del degradato: retry si', ma non 9 sweep/run
MIN_AGE_DAYS = 7          # sotto i 7gg l'esito non e' osservabile
LOOKBACK_DAYS = 270       # decisioni considerate
SMALL_SAMPLE_N = 10       # sotto questa numerosita' si dichiara "campione piccolo"

# ---------------------------------------------------------------------------
# CONFIDENCE: il DB ne contiene tre generazioni di etichette (voce I-3, fix
# 26/07 sera-5, Opus 5). Il bucket girava su ("ALTA","MEDIA","BASSA") esatte:
# `.upper()` salvava i mixed-case italiani ma NON gli inglesi, quindi 14 call
# su 74 (11 HIGH, 1 MEDIUM, 2 MEDIUM-HIGH) sparivano dagli aggregati SENZA che
# nessun campo lo dichiarasse. Effetto misurato: il blocco che arriva al Capo
# diceva "ALTA 4/9 = 44,4% PEGGIO di MEDIA 27/49 = 55,1%", cioe' calibrazione
# smentita; con i sinonimi al loro posto diventa "ALTA 12/20 = 60,0% MEGLIO di
# MEDIA 27/50 = 54,0%". La conclusione era un artefatto del buco.
# I sinonimi si mappano SOLO dove non c'e' niente da interpretare: le etichette
# intermedie (MEDIUM-HIGH, LOW-MED) e la spazzatura ("-") NON si forzano in un
# bucket -> finiscono in `by_confidence_scartate`, dichiarate col loro nome.
# ---------------------------------------------------------------------------
CONF_BUCKETS = ("ALTA", "MEDIA", "BASSA")
CONF_SINONIMI = {"HIGH": "ALTA", "MEDIUM": "MEDIA", "LOW": "BASSA",
                 "ALTO": "ALTA", "MEDIO": "MEDIA", "BASSO": "BASSA"}


def conf_bucket(raw: Optional[str]) -> Optional[str]:
    """Etichetta di confidence -> bucket canonico, `None` se non mappabile.

    `None` non e' un fallimento da nascondere: e' la dichiarazione che quella
    riga non appartiene a nessun bucket senza che qualcuno la interpreti.
    """
    c = (raw or "").strip().upper()
    if not c:
        return None
    if c in CONF_BUCKETS:
        return c
    return CONF_SINONIMI.get(c)


_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")
_MEM_CACHE: Dict[str, Any] = {}
_FETCH_FAIL: Dict[str, str] = {}   # ticker -> prima causa fetch KO (giro corrente)
FETCH_FAIL_LOG_CAP = 5             # prime N cause loggate per esteso


def _log(msg: str):
    try:
        print(f"[SCOREKEEPER] {msg}", flush=True)
    except OSError:
        pass


def _direction(action: str) -> Optional[int]:
    """+1 = call long (hit se sale), -1 = de-risk (hit se scende), None = non misurabile."""
    a = (action or "").strip().upper()
    for p in ("BUY", "ADD"):
        if a.startswith(p):
            return 1
    for p in ("SELL", "TRIM", "EXIT", "SHORT"):
        if a.startswith(p):
            return -1
    return None


def _norm_action(action: str) -> str:
    a = (action or "").strip().upper()
    for p in ("BUY", "ADD", "SELL", "TRIM", "EXIT", "SHORT"):
        if a.startswith(p):
            return p
    return a.split(" ")[0][:12] if a else "?"


def _price_history(ticker: str, start: str):
    """Serie Close daily dal giorno 'start' a oggi (cache in-process)."""
    key = "px:" + ticker + ":" + start
    if key in _MEM_CACHE:
        return _MEM_CACHE[key]
    try:
        import yfinance as yf
        from bellomberg.cli.price_updater import data_ticker as _dt
        h = yf.download(_dt(ticker), start=start, progress=False, auto_adjust=True)
        closes = h["Close"] if "Close" in h.columns else h
        if hasattr(closes, "squeeze"):
            closes = closes.squeeze()
        closes = closes.dropna()
        _MEM_CACHE[key] = closes if len(closes) else None
    except Exception as e:
        # audit/21 §4 n.1: qui c'era un bare except MUTO — lo 0/170 del 25/07
        # era indiagnosticabile dal log. Regola 14/07: la causa si dichiara.
        cause = f"{type(e).__name__}: {str(e)[:160]}"
        if ticker not in _FETCH_FAIL:
            _FETCH_FAIL[ticker] = cause
            if len(_FETCH_FAIL) <= FETCH_FAIL_LOG_CAP:
                _log(f"fetch KO {ticker}: {cause}")
            elif len(_FETCH_FAIL) == FETCH_FAIL_LOG_CAP + 1:
                _log("fetch KO: cap log raggiunto, cause complete nello scorecard")
        _MEM_CACHE[key] = None
    return _MEM_CACHE[key]


def _price_on_or_after(closes, day, max_slip_days=6):
    """Primo close a partire da 'day' (entro max_slip_days), else None."""
    if closes is None or len(closes) == 0:
        return None
    import pandas as pd
    ts = pd.Timestamp(day)
    if getattr(closes.index, "tz", None) is not None:
        ts = ts.tz_localize(closes.index.tz)
    sel = closes[closes.index >= ts]
    if sel.empty:
        return None
    first_ts = sel.index[0]
    if (first_ts - ts).days > max_slip_days:
        return None
    return float(sel.iloc[0])


def _signing_specialists(conn, memo_id, ticker) -> List[str]:
    """Firma #211: gli specialisti che hanno citato il ticker nei report dello
    stesso memo (euristica testuale dichiarata; base senza suffisso con word
    boundary, case-sensitive sulle maiuscole)."""
    if not memo_id:
        return []
    try:
        rows = conn.execute(
            "SELECT specialist, content FROM specialist_reports WHERE memo_id = ?",
            (memo_id,)).fetchall()
    except Exception:
        return []
    base = ticker.split(".")[0]
    pat = re.compile(r"(?<![A-Z0-9])" + re.escape(base) + r"(?![A-Z0-9])")
    out = []
    for r in rows:
        sp, content = r["specialist"], (r["content"] or "")
        if sp.startswith("_"):
            continue  # righe di servizio (es. _red_team persistito): non sono firme
        if ticker in content or pat.search(content):
            if sp not in out:
                out.append(sp)
    return out


def compute_scorecard(db=None, force: bool = False) -> Dict[str, Any]:
    """Calcola (o riusa dallo snapshot) lo scorecard completo."""
    # snapshot fresco -> riuso (la memoria degli specialisti lo chiede 7+ volte a run)
    if not force:
        try:
            with open(SNAP_PATH, encoding="utf-8") as f:
                snap = json.load(f)
            age_h = (datetime.now() - datetime.fromisoformat(snap["computed_at"])).total_seconds() / 3600
            sc_old = snap["scorecard"]
            # uno snapshot DEGRADATO (misura fallita) non gode del TTL pieno:
            # 12h di riuso trasformano un guasto di un minuto in una giornata
            # di track record muto (successo il 25/07). Ma "mai riusarlo"
            # costerebbe ~9 sweep di rete a run con provider giu' (review
            # 26/07): cooldown DICHIARATO — entro 15' si riusa il degradato
            # (ogni consumer lo rende come n.d., non e' un fallback zitto),
            # oltre si ritenta il calcolo vero.
            if "degraded" in sc_old:
                ttl_h = DEGRADED_COOLDOWN_H if sc_old["degraded"] else SNAP_TTL_H
            else:
                # snapshot pre-fix SENZA flag (es. l'avvelenato del 25/07):
                # n=0 con non-misurabili >0 = misura fallita, MAI riusato —
                # senza flag i formatter lo renderebbero come vuoto legittimo
                _poisoned = (sc_old.get("overall", {}).get("n", 0) == 0
                             and sc_old.get("n_unmeasurable", 0) > 0)
                ttl_h = 0.0 if _poisoned else SNAP_TTL_H
            if age_h < ttl_h:
                return sc_old
        except Exception:
            pass

    if db is None:
        from bellomberg.storage.memory_db import MemoryDB
        db = MemoryDB()

    # La cache dei prezzi vale solo nel calcolo corrente. Il TTL dello snapshot
    # e force=True devono poter osservare prezzi nuovi anche nel processo lungo.
    _MEM_CACHE.clear()
    _FETCH_FAIL.clear()   # cause del giro corrente, non residui del precedente
    cutoff_new = (datetime.now() - timedelta(days=MIN_AGE_DAYS)).isoformat()
    cutoff_old = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    with db._conn() as conn:
        # audit 11/09 (Fable 5.1): le decisioni di una run marcata DUPLICATO (memory_db.
        # MARCATORE_DUPLICATO in outcome_notes) non entrano nel track record: doppierebbero
        # le call della stessa settimana (memo #54 ripeteva il #53 sette ore dopo). Il
        # filtro si applica solo se la colonna esiste (schema minimo dei banchi offline:
        # senza colonna non puo' esistere nessun marcatore).
        _colonne = {r[1] for r in conn.execute("PRAGMA table_info(decisions)").fetchall()}
        _filtro_dup = ("AND COALESCE(outcome_notes, '') NOT LIKE '[DUPLICATO%' "
                       if "outcome_notes" in _colonne else "")
        rows = conn.execute(
            "SELECT id, memo_id, timestamp, action, ticker, eur_amount, confidence, status "
            "FROM decisions WHERE ticker IS NOT NULL AND timestamp >= ? AND timestamp < ? "
            + _filtro_dup + "ORDER BY timestamp", (cutoff_old, cutoff_new)).fetchall()

        details: List[Dict[str, Any]] = []
        n_unmeasurable = 0
        n_candidates = 0   # direzionali con ticker quotabile: la base per "degraded"
        for r in rows:
            tk = (r["ticker"] or "").strip().upper()
            direction = _direction(r["action"])
            if direction is None or not _TICKER_RE.match(tk):
                n_unmeasurable += 1
                continue
            n_candidates += 1
            d0 = str(r["timestamp"])[:10]
            closes = _price_history(tk, (datetime.fromisoformat(d0) - timedelta(days=5)).strftime("%Y-%m-%d"))
            p0 = _price_on_or_after(closes, d0)
            if p0 is None or p0 <= 0:
                n_unmeasurable += 1
                continue
            d1w = (datetime.fromisoformat(d0) + timedelta(days=7)).strftime("%Y-%m-%d")
            d4w = (datetime.fromisoformat(d0) + timedelta(days=28)).strftime("%Y-%m-%d")
            p1w = _price_on_or_after(closes, d1w)
            p4w = _price_on_or_after(closes, d4w) if d4w <= datetime.now().strftime("%Y-%m-%d") else None
            ret_1w = (p1w / p0 - 1) * 100 if p1w else None
            ret_4w = (p4w / p0 - 1) * 100 if p4w else None
            ret_used, horizon = (ret_4w, "4w") if ret_4w is not None else (ret_1w, "1w")
            if ret_used is None:
                n_unmeasurable += 1
                continue
            hit = (ret_used > 0) if direction == 1 else (ret_used < 0)
            details.append({
                "id": r["id"], "memo_id": r["memo_id"], "date": d0,
                "action": _norm_action(r["action"]), "ticker": tk,
                "direction": "long" if direction == 1 else "de-risk",
                # raw + derivato, entrambi: l'etichetta vera non si perde (il PM
                # deve poter vedere che quella call diceva "MEDIUM-HIGH")
                "confidence": (r["confidence"] or "").strip().upper() or None,
                "confidence_bucket": conf_bucket(r["confidence"]),
                "status": r["status"],
                "ret_1w_pct": round(ret_1w, 2) if ret_1w is not None else None,
                "ret_4w_pct": round(ret_4w, 2) if ret_4w is not None else None,
                "horizon_used": horizon,
                # edge = rendimento NELLA DIREZIONE della call (un TRIM seguito
                # da -39% e' un edge di +39: call giusta, non "peggiore call")
                "edge_pct": round(ret_used * direction, 2),
                "hit": bool(hit),
                "specialists": _signing_specialists(conn, r["memo_id"], tk),
            })

    def _agg(items):
        n = len(items)
        if n == 0:
            return {"n": 0}
        hits = sum(1 for d in items if d["hit"])
        edges = [d["edge_pct"] for d in items]
        return {"n": n, "hits": hits, "hit_rate_pct": round(hits / n * 100, 1),
                "avg_edge_pct": round(sum(edges) / n, 2),
                "small_sample": n < SMALL_SAMPLE_N}

    by_action: Dict[str, Any] = {}
    for a in sorted({d["action"] for d in details}):
        by_action[a] = _agg([d for d in details if d["action"] == a])
    by_conf: Dict[str, Any] = {}
    for cfd in CONF_BUCKETS:
        items = [d for d in details if d["confidence_bucket"] == cfd]
        if items:
            by_conf[cfd] = _agg(items)
    # gli scarti si DICHIARANO, non si inghiottono: senza questo blocco la
    # somma dei bucket non torna col totale e nessuno se ne accorge
    _scartate = [d for d in details if d["confidence_bucket"] is None]
    _etichette: Dict[str, int] = {}
    for d in _scartate:
        k = d["confidence"] or "(vuota)"
        _etichette[k] = _etichette.get(k, 0) + 1
    by_conf_scarti = {
        "n": len(_scartate),
        "etichette": _etichette,
        "motivo": ("etichette non mappabili su ALTA/MEDIA/BASSA senza "
                   "interpretarle (intermedie tipo MEDIUM-HIGH, o vuote): "
                   "escluse dai bucket e contate qui, mai forzate"),
        "sinonimi_applicati": {k: v for k, v in CONF_SINONIMI.items()
                               if any((d["confidence"] == k) for d in details)},
    }
    by_spec: Dict[str, Any] = {}
    all_specs = sorted({s for d in details for s in d["specialists"]})
    for sp in all_specs:
        by_spec[sp] = _agg([d for d in details if sp in d["specialists"]])

    # ranking DIREZIONALE: peggiore = edge piu' negativo (call sbagliata), non
    # rendimento grezzo (che premierebbe come "peggiori" i TRIM azzeccati)
    ranked = sorted(details, key=lambda d: d["edge_pct"])

    # misura FALLITA (non "storico assente"): c'erano call misurabili ma nessun
    # prezzo e' arrivato — va dichiarata a valle, mai resa come vuoto legittimo
    degraded = bool(n_candidates > 0 and len(details) == 0)

    scorecard = {
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": LOOKBACK_DAYS,
        "overall": _agg(details),
        "by_action": by_action,
        "by_confidence": by_conf,
        "by_confidence_scartate": by_conf_scarti,
        "by_specialist": by_spec,
        "n_unmeasurable": n_unmeasurable,
        "n_directional_candidates": n_candidates,
        "n_fetch_fail": len(_FETCH_FAIL),
        "fetch_fail_causes": dict(list(_FETCH_FAIL.items())[:FETCH_FAIL_LOG_CAP]),
        "degraded": degraded,
        "worst_calls": [{k: d[k] for k in ("id", "date", "action", "ticker", "edge_pct", "horizon_used", "hit")}
                        for d in ranked[:3]],
        "best_calls": [{k: d[k] for k in ("id", "date", "action", "ticker", "edge_pct", "horizon_used", "hit")}
                       for d in ranked[-3:][::-1]],
        "details": details,
        "method_note": ("esito di MERCATO della call (direzione, valuta di quotazione), "
                        "indipendente dallo status; orizzonte 4w, 1w se 4w non osservabile; "
                        "firma specialista = ticker citato nei report dello stesso memo (euristica)"),
    }
    try:
        os.makedirs(os.path.dirname(SNAP_PATH), exist_ok=True)
        tmp = SNAP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"computed_at": scorecard["computed_at"], "scorecard": scorecard},
                      f, ensure_ascii=False, indent=1)
        os.replace(tmp, SNAP_PATH)
    except Exception as e:
        _log("snapshot save failed: " + str(e))
    if degraded:
        # senza questa pulizia un retry nello stesso processo riuserebbe i None
        # avvelenati in cache e fallirebbe senza nemmeno riprovare la rete
        for k in [k for k, v in _MEM_CACHE.items() if v is None]:
            del _MEM_CACHE[k]
        _log(f"SCORECARD DEGRADATO: 0 misurate su {n_candidates} candidati "
             f"direzionali ({len(_FETCH_FAIL)} fetch KO) — snapshot marcato, "
             f"riuso solo in cooldown {DEGRADED_COOLDOWN_H:.2f}h poi retry")
    ov = scorecard["overall"]
    _log(f"scorecard: {ov.get('n', 0)} decisioni misurate, hit-rate {ov.get('hit_rate_pct')}%, "
         f"{n_unmeasurable} non misurabili ({len(_FETCH_FAIL)} fetch KO), "
         f"{len(all_specs)} specialisti attribuiti")
    return scorecard


def _tronca_dichiarando(testo: str, max_chars: int, quale: str) -> str:
    """Taglio dichiarato invece che muto (regola PM 14/07, applicata ai prompt).

    Un `[:max_chars]` zitto su un blocco che il Capo usa per PESARE LE FIRME e'
    un fallback silenzioso: il modello vede una frase interrotta e non sa se il
    dato manca o e' stato tagliato. Costo zero quando non tronca.
    """
    if len(testo) <= max_chars:
        return testo
    coda = f"\n[!! BLOCCO {quale} TRONCATO a {max_chars} char: dato INCOMPLETO, non dedurne assenza]"
    return testo[:max(0, max_chars - len(coda))].rstrip() + coda


def _fmt_line(label, a):
    if not a or a.get("n", 0) == 0:
        return None
    tail = " (campione piccolo)" if a.get("small_sample") else ""
    return (f"{label}: {a['hits']}/{a['n']} hit ({a['hit_rate_pct']}%), "
            f"edge medio {a['avg_edge_pct']:+.1f}%{tail}")


def format_track_record_for_capo(sc: Dict[str, Any], max_chars: int = 2000) -> str:
    """Blocco TRACK RECORD per la memoria del Capo (#190 + #211).

    `max_chars` 1700 -> 2000 il 26/07 sera-5 (Opus 5): la riga nuova di quadro
    parziale porta il blocco reale a **1648 char MISURATI**, cioe' 52 di margine
    sul vecchio tetto. Alla prossima etichetta legacy o al prossimo specialista
    il `[:max_chars]` avrebbe iniziato a tagliare **la coda** — dove vivono le
    peggiori call e la nota di metodo — e il Capo non se ne sarebbe accorto.
    Nessun effetto sul prompt di oggi (1648 sta sotto entrambi i tetti).
    """
    if not sc:
        return ""
    if sc.get("overall", {}).get("n", 0) == 0:
        if not sc.get("degraded"):
            return ""   # finestra genuinamente senza call misurabili: niente storico
        # misura FALLITA: il blocco sparire in silenzio e' la violazione 14/07
        # trovata dall'audit/21 — qui si dichiara, con le cause
        causes = sc.get("fetch_fail_causes") or {}
        cause_txt = "; ".join(f"{t}: {c}" for t, c in list(causes.items())[:3]) \
            or "nessuna eccezione registrata (serie prezzi vuote dal provider)"
        return ("--- TRACK RECORD n.d. — MISURA FALLITA (scorekeeper #190/#211) ---\n"
                f"0 call misurabili su {sc.get('n_directional_candidates', '?')} candidati "
                "direzionali: il fetch prezzi e' fallito. Lo storico ESISTE ma non e' "
                "misurabile in questa run: non dedurre un track record assente e non "
                "pesare le firme a sensazione.\n"
                f"[prime cause: {cause_txt}]")[:max_chars]
    L = ["--- TRACK RECORD (SCOREKEEPER #190/#211: esito di MERCATO delle tue call, calcolo in codice) ---"]
    L.append(_fmt_line("TOTALE call direzionali", sc["overall"]))
    for a, agg in sc.get("by_action", {}).items():
        line = _fmt_line("  " + a, agg)
        if line:
            L.append(line)
    if sc.get("by_confidence"):
        L.append("PER CONFIDENCE (la calibrazione promessa: ALTA deve battere MEDIA/BASSA):")
        for cfd, agg in sc["by_confidence"].items():
            line = _fmt_line("  " + cfd, agg)
            if line:
                L.append(line)
        # quadro PARZIALE dichiarato (fix 26/07 sera-5): finche' in DB
        # convivono etichette intermedie, il Capo deve sapere che qualche call
        # non e' in nessun bucket — prima sparivano zitte e la lettura della
        # calibrazione ne usciva ROVESCIATA
        _sc = sc.get("by_confidence_scartate") or {}
        if _sc.get("n"):
            # riga TENUTA CORTA di proposito: il contesto del Capo e' a 8365
            # char su un cap di 8500 (misurato 26/07 sera-5) e la prima
            # versione, 184 char, sforava mangiando la frase FINALE della
            # memoria — quella che gli dice cosa fare coi commenti del PM
            _et = ", ".join(f"{k}={v}" for k, v in (_sc.get("etichette") or {}).items())
            L.append(f"  [ATTENZIONE: {_sc['n']} call fuori dai bucket ({_et}): "
                     f"quadro per confidence PARZIALE, non dedurne una "
                     f"calibrazione piu' fine di quella che c'e']")
        # Voce 3 §9-quattuortrigies (01/08): sotto il titolo "la calibrazione
        # promessa" il Capo leggeva 60,0 > 53,8 e concludeva promessa mantenuta —
        # su n=20/52 e' rumore (Fisher p=0,79). Il verdetto ora e' SCRITTO,
        # in UNA riga corta: il contesto del Capo ha un budget (v. capo.py:223).
        _a = sc["by_confidence"].get("ALTA") or {}
        _m = sc["by_confidence"].get("MEDIA") or {}
        _p = fisher_bilaterale(_a.get("hits"), _a.get("n"), _m.get("hits"), _m.get("n"))
        if _p is not None:
            _diff = (_a.get("hit_rate_pct") or 0) - (_m.get("hit_rate_pct") or 0)
            _verdetto = ("differenza REALE al 95%" if _p < 0.05 else
                         "RUMORE con questi n, NON dedurne la promessa mantenuta")
            L.append(f"  [ALTA vs MEDIA: {_diff:+.1f} pt, Fisher p={_p:.2f} -> {_verdetto}]")
    if sc.get("by_specialist"):
        L.append("PER SPECIALISTA (firma = ticker citato nei suoi report; pesa le voci su questo, non a sensazione):")
        for sp, agg in sorted(sc["by_specialist"].items(),
                              key=lambda kv: -(kv[1].get("hit_rate_pct") or 0)):
            line = _fmt_line("  " + sp, agg)
            if line:
                L.append(line)
    wc = sc.get("worst_calls") or []
    if wc:
        L.append("PEGGIORI call recenti per edge direzionale (impara da queste): " + "; ".join(
            f"#{c['id']} {c['action']} {c['ticker']} edge {c['edge_pct']:+.1f}% ({c['horizon_used']})"
            for c in wc))
    # degrado PARZIALE dichiarato (review 26/07): con n>0 il blocco e' sano ma
    # i fetch KO vanno distinti dai non-misurabili genuini (HOLD, ticker sporchi)
    _ko = (f", di cui {sc.get('n_fetch_fail')} per fetch prezzi KO"
           if sc.get("n_fetch_fail") else "")
    L.append(f"[{sc.get('n_unmeasurable', 0)} decisioni non misurabili escluse{_ko}; {sc.get('method_note', '')}]")
    return _tronca_dichiarando("\n".join(x for x in L if x), max_chars,
                               "TRACK RECORD")


def format_track_record_for_specialist(sc: Dict[str, Any], specialist: str,
                                        max_chars: int = 600) -> str:
    """Blocco breve per la memoria di UNO specialista (#211)."""
    if not sc:
        return ""
    if sc.get("overall", {}).get("n", 0) == 0 and sc.get("degraded"):
        return ("--- YOUR TRACK RECORD n.d. — misura fallita (scorekeeper: fetch "
                "prezzi KO) ---\nLo storico esiste ma non e' misurabile in questa "
                "run: non dedurne assenza di track record.")[:max_chars]
    own = (sc.get("by_specialist") or {}).get(specialist)
    # fusione 15/07: finche' eventdesk non ha firme proprie, EREDITA (dichiarato)
    # il track record firmato da news+politics — senza questa riga il desk nuovo
    # partirebbe con memoria vuota buttando lo storico del dominio.
    legacy_lines = []
    if specialist == "eventdesk":
        for _old in ("news", "politics"):
            _o = (sc.get("by_specialist") or {}).get(_old)
            if _o:
                _ln = _fmt_line("Ereditato da " + _old.upper() + " (pre-fusione)", _o)
                if _ln:
                    legacy_lines.append(_ln)
    ov = sc.get("overall") or {}
    if not own and not legacy_lines and not ov.get("n"):
        return ""
    L = ["--- YOUR TRACK RECORD (scorekeeper #211, esito di mercato delle call che hai firmato) ---"]
    line = _fmt_line("Le call coi tuoi numeri dentro", own) if own else None
    L.append(line or "(nessuna call direzionale attribuita ai tuoi report nel periodo)")
    L.extend(legacy_lines)
    ovl = _fmt_line("Comitato nel complesso", ov)
    if ovl:
        L.append(ovl)
    L.append("Usa questo dato: se il tuo hit-rate e' sotto il comitato, alza la soglia di evidenza prima di firmare una call.")
    # misurato 26/07: 329 char (488 per eventdesk, che eredita news+politics) su 600
    return _tronca_dichiarando("\n".join(L), max_chars, "YOUR TRACK RECORD")


def get_track_record_for_capo() -> str:
    return format_track_record_for_capo(compute_scorecard())


def get_track_record_for_specialist(specialist: str) -> str:
    return format_track_record_for_specialist(compute_scorecard(), specialist)


# ============================================================================
# LETTURA PER IL PM (voce I-3, 26/07 sera-5, Opus 5)
#
# Fino a oggi questa misura viveva SOLO dentro i prompt: `grep scorekeeper` su
# bellomberg_api.py e su app/src dava 0. Qui si apre la finestra, con due
# vincoli di progetto scritti nel codice perche' non si perdano:
#
#  1. NON SI RICALCOLA DA QUI. Il ricalcolo apre 32 serie prezzi e, fatto
#     dall'ambiente sbagliato, ha gia' avvelenato la misura DUE volte (25/07
#     sandbox dell'audit; 26/07 la suite di test, v. tests/conftest.py). Il
#     ricalcolo e' del processo della run (consigliere_multi.py:473, force=True).
#  2. ONESTA' STATISTICA OBBLIGATORIA. n=74 non e' un campione su cui dire
#     "il comitato batte il mercato": ogni aggregato esce con l'IC 95% e con
#     lo stato dichiarato, mai un numero nudo.
# ============================================================================

Z95 = 1.959963985


def wilson_ci95(n: Any, hits: Any) -> Optional[Dict[str, Any]]:
    """IC 95% di una proporzione col metodo WILSON.

    Wilson e non Wald (`p +- 1.96*sqrt(p(1-p)/n)`) perche' su questi campioni
    Wald MENTE: il bucket BASSA ha n=2 con 0 hit e Wald darebbe "0% +- 0",
    cioe' certezza assoluta ricavata da due osservazioni. Wilson sullo stesso
    dato da' **[0%, 65.8%]**, che e' la verita': non si sa niente.
    (Il 73.5% scritto qui fino alla review del 26/07 sera-5 era un numero
    SBAGLIATO in un commento che giustificava la scelta del metodo: 65.8 e' il
    valore vero, verificato per inversione dello score test.)

    `None` — dichiarato, non zero — se il dominio non e' valido: n<=0, hits
    assente o fuori da [0, n]. Con `hits` non validato, `p(1-p)` diventa
    negativo e `** 0.5` in Python restituisce un **complex**.
    """
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return None
    if not isinstance(hits, int) or isinstance(hits, bool) or hits < 0 or hits > n:
        return None
    p = hits / float(n)
    z2 = Z95 * Z95
    den = 1.0 + z2 / n
    centro = (p + z2 / (2 * n)) / den
    mezza = Z95 * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5) / den
    lo = round(max(0.0, centro - mezza) * 100, 1)
    hi = round(min(1.0, centro + mezza) * 100, 1)
    # larghezza dagli estremi GIA' arrotondati: calcolandola su quelli grezzi
    # discordava da hi-lo di 0,1 pt nel 25,5% dei casi (review 26/07 sera-5) —
    # "[9,5% - 90,5%], ampiezza 81,1" sono due misure discordanti nella stessa riga
    return {"low_pct": lo, "high_pct": hi,
            "width_pct_points": round(hi - lo, 1), "method": "Wilson"}


def fisher_bilaterale(hits_a, n_a, hits_b, n_b):
    """p-value ESATTO bilaterale (Fisher) per il confronto fra due proporzioni.

    Voce 3 §9-quattuortrigies (01/08): il blocco del Capo affermava la
    calibrazione mostrando 60,0% > 53,8% senza dire che su n=20/52 la
    differenza e' rumore (p=0,7922, riprodotto dai test alla quarta cifra).
    Esatto e non normale perche' i bucket sono PICCOLI (stessa ragione di
    Wilson qui sopra); implementato su interi con math.comb — il confronto
    p(x) <= p(osservato) avviene sui numeratori interi, niente epsilon.

    `None` — dichiarato, non zero — se il dominio non e' valido (stessa
    disciplina di wilson_ci95): conteggi non-int/bool, n<=0, hits fuori [0,n].
    """
    for v in (hits_a, n_a, hits_b, n_b):
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return None
    if n_a <= 0 or n_b <= 0 or hits_a > n_a or hits_b > n_b:
        return None
    from math import comb
    n = n_a + n_b
    k = hits_a + hits_b
    den = comb(n, k)
    p_oss = comb(n_a, hits_a) * comb(n_b, hits_b)
    p = 0
    for x in range(max(0, k - n_b), min(n_a, k) + 1):
        px = comb(n_a, x) * comb(n_b, k - x)
        if px <= p_oss:
            p += px
    return p / den


def _caveat_confidence(sc: Dict[str, Any]) -> str:
    """Il quadro per confidence e' completo o parziale? Detto in chiaro."""
    scarti = sc.get("by_confidence_scartate")
    if scarti is None:
        # snapshot pre-fix: il buco NON e' "non lo so", e' CALCOLABILE da qui
        # (review 26/07 sera-5: degradare un fatto misurabile a incertezza e'
        # la stessa colpa del buco). Su quello delle 14:45: 74 - 60 = 14.
        _tot = (sc.get("overall") or {}).get("n")
        _somma = sum((v or {}).get("n") or 0
                     for v in (sc.get("by_confidence") or {}).values())
        _fuori = (_tot - _somma) if isinstance(_tot, int) else None
        if _fuori:
            return ("snapshot scritto PRIMA del fix 26/07 sera-5: %s call su %s "
                    "sono FUORI dai bucket e il file non lo dichiara (numero "
                    "ricavato qui: overall.n meno la somma dei bucket). Il "
                    "confronto fra ALTA e MEDIA puo' essere ROVESCIATO: si "
                    "accende al primo ricalcolo, che la run fa da sola."
                    % (_fuori, _tot))
        return ("snapshot scritto PRIMA del fix 26/07 sera-5: non dichiara gli "
                "scarti, ma la somma dei bucket torna col totale. Si accende al "
                "primo ricalcolo (la run lo fa da sola).")
    n = scarti.get("n") or 0
    if not n:
        return "tutte le call misurate sono in un bucket: quadro completo."
    et = ", ".join(f"{k}={v}" for k, v in (scarti.get("etichette") or {}).items())
    return ("PARZIALE: %s call fuori dai bucket (%s). Etichette intermedie non "
            "forzate in ALTA/MEDIA/BASSA: renderle, cosi' si vede che il "
            "confronto fra bucket non copre tutto." % (n, et))


def _con_ci95(agg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Copia arricchita di un aggregato: mai mutazione dello snapshot letto."""
    if not isinstance(agg, dict):
        return agg
    out = dict(agg)
    # `hits` NON ha un default a 0 (review 26/07 sera-5): con `or 0` un campo
    # assente produceva un IC [0%, 4.9%] servito ACCANTO a "hit-rate 52,7%",
    # due misure incompatibili entrambe presentate come vere. `hits == 0`
    # legittimo resta coperto (e' un int).
    out["ci95"] = wilson_ci95(agg.get("n"), agg.get("hits"))
    if out["ci95"] is None and agg.get("n"):
        out["ci95_error"] = ("IC non calcolabile: 'hits' assente o fuori "
                             "dall'intervallo [0, n] in questo aggregato")
    return out


# le 29 chiavi del contratto: il pattern (25) vuole la CHIAVE sempre presente e
# il buco nel VALORE. La prima versione ne inizializzava 7 e le altre 22
# nascevano solo nel ramo buono, cioe' sparivano ESATTAMENTE nei 4 rami in cui
# il PM ha piu' bisogno di capire (review 26/07 sera-5, finding n.1).
_VUOTI_DI_CONTRATTO: Dict[str, Any] = {
    "computed_at": None, "age_hours": None, "age_error": None,
    "ttl_hours": SNAP_TTL_H, "stale": None, "degraded": None,
    "window_days": None, "maturation_days": MIN_AGE_DAYS,
    "overall": None, "by_action": {}, "by_confidence": {},
    "by_confidence_scartate": None, "by_specialist": {},
    "n_unmeasurable": None, "n_directional_candidates": None,
    "n_fetch_fail": None, "fetch_fail_causes": {},
    "fetch_fail_causes_troncate": None,
    "worst_calls": [], "best_calls": [], "details": [],
    "method_note": None, "small_sample_threshold": SMALL_SAMPLE_N,
    "trend": None, "caveats": {}, "incoerenza": None,
}


def scorecard_for_api(path: Optional[str] = None) -> Dict[str, Any]:
    """Payload del track record per la UI: SERVE lo snapshot, non lo calcola.

    `stato` e' l'UNICO campo autorevole (`available` dice solo "snapshot
    leggibile", ed e' True anche su `vuoto`/`degradato`). Cinque valori, quattro
    significati — il frontend ne tipizza cinque:
      - `assente` / `illeggibile` -> nessuna misura usabile: `error` valorizzato
      - `degradato`              -> c'erano call misurabili e il fetch prezzi e' fallito
      - `vuoto`                  -> finestra genuinamente senza call misurabili
      - `ok`                     -> dato vero

    Ogni chiave del contratto e' presente in OGNI ramo (pattern (25)): il buco
    sta nel valore, mai nella chiave, perche' la pagina non deve indovinare.
    """
    p = path or SNAP_PATH
    out: Dict[str, Any] = {
        "available": False,
        "stato": "assente",
        "error": None,
        "src": "scorekeeper snapshot (#190/#211)",
        "snapshot_file": os.path.basename(p),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "ricalcolo_da_questo_endpoint": False,
        "nota_ricalcolo": ("mai: 32 serie prezzi per giro, e un ricalcolo "
                           "dall'ambiente sbagliato ha gia' avvelenato la misura "
                           "due volte. Lo scrive la run (force) o "
                           "`python scorekeeper.py`."),
    }
    out.update({k: (dict(v) if isinstance(v, dict) else (list(v) if isinstance(v, list) else v))
                for k, v in _VUOTI_DI_CONTRATTO.items()})
    try:
        with open(p, encoding="utf-8") as f:
            snap = json.load(f)
    except FileNotFoundError:
        out["error"] = ("snapshot assente: il track record non e' ancora stato "
                        "calcolato su questa macchina")
        return out
    except Exception as e:
        out["stato"] = "illeggibile"
        out["error"] = f"snapshot illeggibile: {type(e).__name__}: {e}"
        return out

    if not isinstance(snap, dict):
        out["stato"] = "illeggibile"
        out["error"] = f"snapshot non e' un oggetto JSON ma {type(snap).__name__}"
        return out
    sc = snap.get("scorecard")
    if not isinstance(sc, dict) or not sc:
        out["stato"] = "illeggibile"
        out["error"] = ("snapshot senza chiave 'scorecard' utilizzabile "
                        f"(trovato {type(sc).__name__})")
        return out

    try:
        return _arricchisci(out, sc)
    except Exception as e:
        # uno snapshot corrotto nei TIPI non deve diventare un 500: il 500 e'
        # dichiarato solo per uno sviluppatore, mentre la pagina ha uno stato
        # `illeggibile` fatto esattamente per questo (review 26/07 sera-5)
        out["stato"] = "illeggibile"
        out["error"] = (f"snapshot con struttura inattesa: {type(e).__name__}: {e}"
                        " — il file c'e' ma non e' interpretabile")
        return out


def _arricchisci(out: Dict[str, Any], sc: Dict[str, Any]) -> Dict[str, Any]:
    """Riempie il payload dal blocco `scorecard`. Separata per poterla
    proteggere in blocco: qualunque tipo inatteso -> `stato: illeggibile`."""
    age_h = None
    try:
        age_h = round((datetime.now()
                       - datetime.fromisoformat(sc["computed_at"])).total_seconds() / 3600, 2)
    except Exception as e:
        # eta' IGNOTA non e' "fresco". `stale=False` in pagina significa una cosa
        # sola: dato buono. Il caso non e' teorico: il giorno che `computed_at`
        # avra' un timezone, `now()` naive - aware alza TypeError.
        out["age_error"] = (f"computed_at non interpretabile ({type(e).__name__}): "
                            f"{sc.get('computed_at')!r} -> eta' e freschezza IGNOTE")
    ov = sc.get("overall")
    if not isinstance(ov, dict):
        out["stato"] = "illeggibile"
        out["available"] = True
        out["error"] = ("scorecard senza aggregato 'overall': lo snapshot c'e' ma "
                        "non porta la misura (NON e' una finestra vuota)")
        return out
    n = ov.get("n") or 0
    degraded = bool(sc.get("degraded"))
    if degraded and n > 0:
        # il writer garantisce degraded => n==0, ma il payload legge un FILE che
        # puo' venire da qualsiasi versione: l'incoerenza si dichiara
        out["incoerenza"] = (f"degraded=True con overall.n={n}: lo snapshot si "
                             "contraddice, tratta i numeri con sospetto")
    stale = None if age_h is None else (age_h > SNAP_TTL_H)
    if age_h is not None and age_h < 0:
        out["age_error"] = (f"computed_at nel FUTURO di {abs(age_h)}h: orologio "
                            "sfasato fra chi ha scritto e chi legge")
    out.update({
        "available": True,
        "stato": ("degradato" if (degraded and n == 0)
                  else ("ok" if n > 0 else "vuoto")),
        "computed_at": sc.get("computed_at"),
        "age_hours": age_h,
        "ttl_hours": SNAP_TTL_H,
        "stale": stale,
        "degraded": degraded,
        "window_days": sc.get("window_days"),
        "overall": _con_ci95(sc.get("overall")),
        "by_action": {k: _con_ci95(v) for k, v in (sc.get("by_action") or {}).items()},
        "by_confidence": {k: _con_ci95(v) for k, v in (sc.get("by_confidence") or {}).items()},
        "by_confidence_scartate": sc.get("by_confidence_scartate"),
        "by_specialist": {k: _con_ci95(v) for k, v in (sc.get("by_specialist") or {}).items()},
        "n_unmeasurable": sc.get("n_unmeasurable"),
        "n_directional_candidates": sc.get("n_directional_candidates"),
        # NB unita': conta i TICKER falliti, non le call (`_FETCH_FAIL` e'
        # indicizzato per ticker) — "32 fetch KO" accanto a "74 call" si
        # leggerebbe come 32 call
        "n_fetch_fail": sc.get("n_fetch_fail"),
        "fetch_fail_causes": sc.get("fetch_fail_causes") or {},
        "worst_calls": sc.get("worst_calls") or [],
        "best_calls": sc.get("best_calls") or [],
        "details": sc.get("details") or [],
        "method_note": sc.get("method_note"),
        "small_sample_threshold": SMALL_SAMPLE_N,
        "trend": {"available": False,
                  "reason": ("questo endpoint serve l'ultimo snapshot. Lo storico "
                             "registrato a fine run è nella pagina Progressi agenti "
                             "e in GET /agents/progress; nessuna ricostruzione retrodatata.")},
    })
    ov_ci = (out["overall"] or {}).get("ci95") or {}
    out["caveats"] = {
        "campione": (
            "n=%s: IC 95%% dell'hit-rate complessivo [%s%%, %s%%] (Wilson). "
            "Se l'intervallo contiene 50%% il risultato NON e' distinguibile da "
            "una moneta: si legge come indizio, non come prova."
            % (n, ov_ci.get("low_pct"), ov_ci.get("high_pct"))) if n else None,
        "per_specialista": ("EURISTICO: la firma non e' un campo del DB, e' dedotta "
                            "dal ticker citato nei report dello stesso memo. Un "
                            "ticker citato da due desk conta per entrambi."),
        "per_confidence": _caveat_confidence(sc),
        "orizzonte": ("esito a 4 settimane quando osservabile, altrimenti 1 "
                      "settimana (campo horizon_used riga per riga). La finestra "
                      "e' %s giorni che finiscono %s giorni fa: sotto quella "
                      "maturazione l'esito non e' osservabile, quindi le call "
                      "dell'ultima settimana NON sono qui."
                      % (sc.get("window_days"), MIN_AGE_DAYS)),
        "non_misurabili": ("%s call escluse, e la cifra somma TRE cause diverse: "
                           "non direzionali (HOLD/RESEARCH), ticker non quotabili, "
                           "e prezzo/orizzonte non disponibili. Non leggerla come "
                           "'tutte HOLD'." % sc.get("n_unmeasurable")),
        "edge": ("edge = rendimento NELLA DIREZIONE della call: un TRIM seguito "
                 "da -39% e' un edge di +39, non una call sbagliata."),
        "details": ("una riga per call misurata (id decisione, ticker, edge, hit): "
                    "e' il dato con cui si accende la colonna Outcome di F10 "
                    "senza una chiamata in piu'."),
    }
    if degraded:
        out["caveats"]["misura_fallita"] = (
            "MISURA FALLITA, non storico assente: c'erano %s call misurabili e "
            "nessun prezzo e' arrivato (%s ticker KO). Le cause sono in "
            "fetch_fail_causes: renderle, non nasconderle."
            % (sc.get("n_directional_candidates"), sc.get("n_fetch_fail")))
    # degrado PARZIALE: il Capo lo legge da sempre ("di cui N per fetch KO"), il
    # PM no — due canali della stessa casa che dicevano cose diverse sullo stesso
    # dato (review 26/07 sera-5). Con n>0 lo `stato` resta `ok`, ma il buco si dice.
    _ko = sc.get("n_fetch_fail") or 0
    if _ko and n > 0:
        out["caveats"]["fetch_parziale"] = (
            "misura PARZIALE: %s ticker hanno fallito il fetch prezzi, quindi "
            "l'hit-rate e' calcolato su %s call su %s candidati direzionali. "
            "Lo stato resta 'ok' perche' la misura esiste, ma non e' completa."
            % (_ko, n, sc.get("n_directional_candidates")))
    # le cause sono troncate a monte (FETCH_FAIL_LOG_CAP): senza dirlo, la
    # pagina mostrerebbe 5 cause su 32 sembrando completa
    _cause = len(out["fetch_fail_causes"] or {})
    if _ko and _ko > _cause:
        out["fetch_fail_causes_troncate"] = _ko - _cause
        out["caveats"]["cause_troncate"] = (
            "delle %s cause di fetch KO ne sono salvate solo %s (cap del "
            "registro): %s non sono recuperabili da qui."
            % (_ko, _cause, _ko - _cause))
    return out


if __name__ == "__main__":
    sc = compute_scorecard(force=True)
    print()
    print(format_track_record_for_capo(sc))
    print()
    print(format_track_record_for_specialist(sc, "quant"))
