"""Immutable run evidence; consumes the existing scorekeeper, never recalculates it.

History starts when this feature is enabled. A run snapshot measures older calls,
not the future performance of the run whose memo identifies the snapshot.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from bellomberg.agents.scorekeeper import MIN_AGE_DAYS, SMALL_SAMPLE_N, SNAP_TTL_H, wilson_ci95

SCORE_HISTORY_MIGRATION = (9, "immutable scorekeeper evidence at completed runs", [
    """CREATE TABLE IF NOT EXISTS agent_score_history (
        run_id TEXT PRIMARY KEY,
        memo_id INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_score_history_completed ON agent_score_history(completed_at)",
    """CREATE TRIGGER IF NOT EXISTS agent_score_history_immutable_update
       BEFORE UPDATE ON agent_score_history BEGIN
       SELECT RAISE(ABORT, 'agent score history is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS agent_score_history_immutable_delete
       BEFORE DELETE ON agent_score_history BEGIN
       SELECT RAISE(ABORT, 'agent score history is immutable'); END""",
])

# Roles follow the active committee. Legacy desk names appear only when recorded.
AGENTS = (
    ("capo", "Capo / Comitato", "Sintesi e decisioni collettive"),
    ("macro", "Macro", "Regime economico e liquidità"),
    ("fundamentals", "Fundamentals", "Valutazioni e tesi societarie"),
    ("quant", "Quant", "Rischio, fattori e sizing"),
    ("options", "Options", "Volatilità e flussi opzioni"),
    ("crypto", "Crypto", "Mercati digitali e derivati"),
    ("eventdesk", "Event Desk", "Catalyst, notizie e geopolitica"),
    ("red_team", "Red Team", "Revisione critica del comitato"),
    ("_reflection", "Reflection", "Lezioni per la run successiva"),
    ("_action_table", "Action extractor", "Estrazione delle decisioni"),
)
METHOD_ID = "scorekeeper-directional-v1"
ATTRIBUTION = ("Attribuzione euristica: una call è assegnata ai desk che citano il titolo "
               "nei report dello stesso memo. Può contare per più desk; non è una firma verificata.")


class HistoryUnavailable(RuntimeError):
    pass


def _connect(path, mode):
    # mode=ro/rw never creates a missing database or follows an unintended cwd.
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise HistoryUnavailable("Database dello storico assente; nessun database creato")
    conn = sqlite3.connect(resolved.as_uri() + "?mode=" + mode, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if mode == "ro":
        conn.execute("PRAGMA query_only=ON")
    return conn


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _encoded(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def save_run_snapshot(db_path, *, run_id, memo_id, started_at, completed_at,
                      scorecard=None, score_error=None, lesson=None,
                      reflection_status="unavailable", operational=None):
    """Insert once after a run. Duplicate IDs preserve the original evidence.

    Requires the versioned migration; this writer never creates schema. All paths
    and run IDs are explicit. Missing scoring is persisted as a declared hole.
    """
    if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 200:
        raise ValueError("run_id non valido")
    if type(memo_id) is not int or memo_id <= 0:
        raise ValueError("memo_id positivo richiesto")
    for stamp in (started_at, completed_at):
        datetime.fromisoformat(stamp)
    payload = _json_safe({
        "version": 1, "method_id": METHOD_ID, "maturation_days": MIN_AGE_DAYS,
        "scorecard": scorecard if isinstance(scorecard, dict) else None,
        "score_error": score_error or (None if isinstance(scorecard, dict) else "Scorekeeper non disponibile nella run"),
        "reflection": {"text": lesson or None, "status": reflection_status,
                       "kind": "suggestion", "implementation_verified": False,
                       "source": "reflection della run", "performance_proven": False},
        "operational": operational or {},
    })
    raw = _encoded(payload)
    captured = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_connect(db_path, "rw")) as conn, conn:
        try:
            result = conn.execute(
                "INSERT INTO agent_score_history VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_id) DO NOTHING",
                (run_id, memo_id, started_at, completed_at, captured, raw,
                 hashlib.sha256(raw.encode("utf-8")).hexdigest()))
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                raise HistoryUnavailable("schema progressi assente: applicare la migrazione 9") from exc
            raise
        return {"saved": result.rowcount == 1, "run_id": run_id,
                "reason": "saved" if result.rowcount else "already_recorded_immutable"}


def read_history(db_path, limit=100):
    if type(limit) is not int or not 1 <= limit <= 250:
        raise ValueError("limit deve essere tra 1 e 250")
    with closing(_connect(db_path, "ro")) as conn:
        try:
            rows = conn.execute("SELECT * FROM agent_score_history ORDER BY completed_at DESC, rowid DESC LIMIT ?",
                                (limit,)).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                raise HistoryUnavailable("schema progressi assente: applicare la migrazione 9") from exc
            raise
    out = []
    for row in reversed(rows):
        raw = row["payload_json"]
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["payload_sha256"]:
            raise HistoryUnavailable("Integrità di uno snapshot non verificata; storico non utilizzabile")
        try:
            item = json.loads(raw)
            if not isinstance(item, dict) or item.get("version") != 1:
                raise ValueError("versione non supportata")
        except (ValueError, TypeError) as exc:
            raise HistoryUnavailable("Snapshot storico illeggibile") from exc
        item.update({k: row[k] for k in ("run_id", "memo_id", "started_at", "completed_at", "captured_at")})
        out.append(item)
    return out


def record_completed_run(db, blackboard, *, scorecard, score_error=None, lesson=None,
                         reflection_status="unavailable"):
    """Production completion adapter. It consumes evidence, never scores or calls models."""
    if db is None or blackboard.memo_id is None:
        raise HistoryUnavailable("DB/memo assente: storico progressi non registrabile")
    with blackboard._lock:
        usage, total = blackboard._usage_aggregates()
        models = {}
        for call in blackboard.usage_log:
            if call.get("agent") and call.get("model"):
                names = models.setdefault(call["agent"], [])
                if call["model"] not in names:
                    names.append(call["model"])
        operational = {"usage_by_specialist": usage, "usage_total": total,
                       "specialist_status": dict(blackboard.specialist_status), "models": models}
    return save_run_snapshot(db.db_path, run_id="memo:" + str(blackboard.memo_id), memo_id=blackboard.memo_id,
                             started_at=blackboard.start_time,
                             completed_at=datetime.now().isoformat(timespec="seconds"),
                             scorecard=scorecard, score_error=score_error, lesson=lesson,
                             reflection_status=reflection_status, operational=operational)


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _time_quality(sc, row):
    try:
        computed = datetime.fromisoformat(sc["computed_at"]).astimezone(timezone.utc)
        observed = (datetime.fromisoformat(row["completed_at"]).astimezone(timezone.utc)
                    if row.get("completed_at") else datetime.now(timezone.utc))
        age = (observed - computed).total_seconds() / 3600
        if age < 0:
            return "time_unknown"
        if age > SNAP_TTL_H:
            return "stale"
        return None
    except (TypeError, ValueError, KeyError):
        return "time_unknown"


def _point(row, agent):
    sc = row.get("scorecard") or {}
    by = sc.get("by_specialist") or {}
    agg = sc.get("overall") if agent == "capo" else by.get(agent)
    agg = agg if isinstance(agg, dict) else {}
    n, hits = _count(agg.get("n")), _count(agg.get("hits"))
    rate = _number(agg.get("hit_rate_pct"))
    quality = []
    if n is None:
        quality.append("missing")
    elif n == 0:
        quality.append("empty")
    if n and (rate is None or not 0 <= rate <= 100 or hits is None or hits > n
              or abs(rate - hits / n * 100) > 0.11):
        quality.append("invalid")
    if sc.get("degraded") is True:
        quality.append("degraded")
    failed = _count(sc.get("n_fetch_fail"))
    if failed is None or type(sc.get("degraded")) is not bool:
        quality.append("quality_unknown")
    elif failed > 0:
        quality.append("partial")
    time_quality = _time_quality(sc, row)
    if time_quality:
        quality.append(time_quality)
    if n and n < SMALL_SAMPLE_N:
        quality.append("small_sample")
    if not n or any(q in quality for q in ("invalid", "degraded")):
        rate = None
    details = sc.get("details") or []
    cohort = sorted((str(d.get("id")), d.get("horizon_used"), d.get("direction"))
                    for d in details if isinstance(d, dict)
                    and (agent == "capo" or agent in (d.get("specialists") or [])))
    cohort_ok = (n is not None and len(cohort) == n and len({c[0] for c in cohort}) == n and all(
        ident != "None" and horizon in ("1w", "4w") and direction in ("long", "de-risk")
        for ident, horizon, direction in cohort))
    if n and not cohort_ok:
        quality.append("cohort_unverified")
    compare_key = None
    if (cohort_ok and n and rate is not None and row.get("method_id") and sc.get("method_note")
            and _count(sc.get("window_days")) and _count(row.get("maturation_days"))
            and not any(q in quality for q in ("partial", "degraded", "quality_unknown", "stale", "time_unknown"))):
        compare_key = hashlib.sha256(_encoded({"method": row.get("method_id"),
            "note": sc.get("method_note"), "window": sc.get("window_days"),
            "maturation": row.get("maturation_days"), "cohort": cohort}).encode()).hexdigest()
    usage_key = {"red_team": "_red_team"}.get(agent, agent)
    operational = row.get("operational") or {}
    usage = (operational.get("usage_by_specialist") or {}).get(usage_key)
    status = (operational.get("specialist_status") or {}).get(usage_key)
    return {"run_id": row.get("run_id"), "memo_id": row.get("memo_id"),
            "completed_at": row.get("completed_at"), "computed_at": sc.get("computed_at"),
            "n": n, "hits": hits, "hit_rate_pct": rate,
            "avg_edge_pct": _number(agg.get("avg_edge_pct")) if rate is not None else None,
            "ci95": wilson_ci95(n, hits) if rate is not None else None,
            "quality": quality or ["ok"], "comparison_key": compare_key,
            "window_days": sc.get("window_days"), "maturation_days": row.get("maturation_days"),
            "source": "scorekeeper / agent_score_history" if row.get("run_id") else "scorekeeper snapshot corrente",
            "n_fetch_fail": sc.get("n_fetch_fail"), "n_unmeasurable": sc.get("n_unmeasurable"),
            "n_directional_candidates": sc.get("n_directional_candidates"),
            "operational": {"status": status, "usage": usage,
                            "models": (operational.get("models") or {}).get(usage_key, [])}}


def _delta(previous, current):
    result = {"available": False, "hit_rate_pp": None, "reason": "Servono due misure confrontabili"}
    if previous and current:
        if current["comparison_key"] and current["comparison_key"] == previous["comparison_key"]:
            result.update(available=True, hit_rate_pp=round(current["hit_rate_pct"] - previous["hit_rate_pct"], 2),
                          reason="Stesso metodo, decisioni e orizzonti; variazione della misura, non prova di apprendimento")
        else:
            result["reason"] = "Campione, orizzonte, metodo o qualità diversi: delta non confrontabile"
    return result


def progress_payload(rows, current_scorecard=None):
    agents = list(AGENTS)
    known = {a[0] for a in agents}
    for row in rows:
        for name in ((row.get("scorecard") or {}).get("by_specialist") or {}):
            if name not in known:
                agents.append((name, name.title() + " (storico)", "Desk presente nello storico"))
                known.add(name)
    current_row = None
    if not rows and current_scorecard and current_scorecard.get("available"):
        current_row = {"scorecard": current_scorecard, "method_id": METHOD_ID,
                       "maturation_days": current_scorecard.get("maturation_days"), "operational": {}}
    items = []
    for name, label, role in agents:
        series = [_point(row, name) for row in rows]
        for index, point in enumerate(series):
            point["delta"] = _delta(series[index - 1] if index else None, point)
        latest = series[-1] if series else (_point(current_row, name) if current_row else None)
        items.append({"id": name, "label": label, "role": role,
                      "attribution": "collective" if name == "capo" else (
                          "unsupported" if name in ("red_team", "_reflection", "_action_table") else "heuristic"),
                      "latest": latest, "series": series,
                      "delta": _delta(series[-2] if len(series) > 1 else None, latest)})
    runs = [{k: row.get(k) for k in ("run_id", "memo_id", "started_at", "completed_at", "captured_at",
                                     "reflection", "score_error")} for row in rows]
    return {"source": "SQLite agent_score_history + scorekeeper", "paid_analysis": False,
            "history": {"state": "ok" if rows else "empty", "count": len(rows),
                        "available": bool(rows), "first_captured_at": rows[0]["captured_at"] if rows else None,
                        "note": "Storico registrato dal primo completamento dopo l'attivazione; nessuna run retrodatata"},
            "trend": {"available": len(rows) >= 2, "reason": None if len(rows) >= 2 else "Servono almeno due snapshot di run"},
            "agents": items, "runs": runs,
            "current_scorecard": ({key: current_scorecard.get(key) for key in
                                   ("available", "stato", "error", "computed_at")}
                                  if current_scorecard else None),
            "method": {"score": "Hit rate delle call direzionali misurate dallo scorekeeper; non P&L del conto",
                       "attribution": ATTRIBUTION, "horizon": "Esito a quattro settimane, altrimenti una; maturazione minima richiesta",
                       "comparison": "Delta solo per lo stesso metodo, le stesse decisioni e gli stessi orizzonti, senza degrado dati",
                       "learning": "Le reflection sono suggerimenti. Attuazione e miglioramento causale non sono verificati automaticamente",
                       "timing": "La misura salvata a fine run riguarda call passate: le decisioni della nuova run devono ancora maturare"}}
