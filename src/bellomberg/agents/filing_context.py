"""Read-only, bounded evidence from the persisted filing archive."""
import re
import json
import sqlite3

from bellomberg.core.paths import SQLITE_PATH


def get_filing_changes(ticker, *, db_path=None, max_changes=5):
    """No acquisition, valuation, model call or archive mutation is performed."""
    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,29}", ticker):
        return {"ticker": ticker, "status": "errore", "reason": "ticker non valido", "_source": "filing_archive"}
    from bellomberg.storage.filing_store import FilingStore
    try:
        store = FilingStore(db_path or SQLITE_PATH)
        profile = store.get_profile(ticker)
        runs = store.list_runs(ticker, limit=20)
    except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        return {"ticker": ticker, "status": "non_disponibile", "reason": str(exc),
                "_source": "filing_archive"}
    active = next((r for r in runs if r["status"] in ("queued", "running")), None)
    last = next((r for r in runs if r["status"] not in ("queued", "running")), None)
    if not last:
        return {"ticker": ticker, "status": "non_disponibile", "reason": "nessun confronto concluso",
                "profile_status": "configurato" if profile else "assente",
                "active_run": active["id"] if active else None, "_source": "filing_archive"}
    result = last.get("result") or {}
    current = result.get("confronto_corrente")
    historical = result.get("confronto_storico")
    diff = current or historical or {}
    changes = diff.get("cambiamenti") or []
    shown = []
    for n, change in enumerate(changes[:max_changes], 1):
        excerpts = {}
        for side in ("prima", "dopo"):
            c = change.get(side)
            if isinstance(c, dict):
                excerpts[side] = {k: c.get(k) for k in ("url", "sha256", "sezione", "pagine_fisiche", "inizio", "fine")}
                raw = str(c.get("testo") or "")
                excerpts[side]["testo"] = raw[:220]
                excerpts[side]["testo_troncato"] = len(raw) > 220
                excerpts[side]["citation_id"] = f"C{n}-{side}"
        shown.append({"tipo": change.get("tipo"), "estratti": excerpts})
    judgment = last.get("judgment") or {}
    index = last.get("index") or {}
    findings = judgment.get("findings") or []
    motivations = result.get("motivi") or []
    similarities = diff.get("similarita_sezioni") or {}
    return {"ticker": ticker, "status": last["status"], "reason": last.get("reason"),
            "run_id": last["id"], "finished_at": last.get("finished_at"),
            "active_run": active["id"] if active else None,
            "scope": "corrente" if current else "storico" if historical else "nessun confronto",
            "latest_unverified": bool(result.get("ultimo_non_verificato")),
            "freshness": result.get("freschezza") or {"stato": "n.d."},
            "coverage": result.get("copertura") or {"stato": "n.d."},
            "pair": {k: {"url": (result.get("coppia") or {}).get(k, {}).get("url"),
                         "sha256": (result.get("coppia") or {}).get(k, {}).get("sha256"),
                         "metadati": (result.get("coppia") or {}).get(k, {}).get("metadati")}
                     for k in ("prima", "dopo")} if result.get("coppia") else None,
            "diff_status": diff.get("stato") or "non_disponibile",
            "sections": diff.get("sezioni_confrontate") or [],
            "similarity": dict(list(similarities.items())[:8]),
            "similarity_sections_total": len(similarities),
            "changes": shown, "changes_shown": len(shown), "changes_total": len(changes),
            "truncation": "estratti limitati a 220 caratteri; aprire /filings/runs/{run_id} per il testo completo" if shown else None,
            "judgment_status": judgment.get("status") or "n.d.",
            "judgment_reason": judgment.get("reason"),
            "findings": [{"category": f.get("category"), "assessment": str(f.get("assessment") or "")[:500],
                          "citations": (f.get("citations") or [])[:5]}
                         for f in findings[:3] if isinstance(f, dict)],
            "findings_total": len(findings),
            "findings_truncated": len(findings) > 3 or any(
                len(str(f.get("assessment") or "")) > 500 or len(f.get("citations") or []) > 5
                for f in findings[:3] if isinstance(f, dict)),
            "index_status": index.get("status") or "n.d.",
            "index_reason": index.get("reason"),
            "limitations": [str(m)[:300] for m in motivations[:5]],
            "limitations_total": len(motivations),
            "limitations_truncated": len(motivations) > 5 or any(len(str(m)) > 300 for m in motivations[:5]),
            "_source": "filing_archive"}


def committee_filing_context(tickers, *, db_path=None, max_tickers=12, max_chars=9000):
    """Compact, explicitly incomplete priming for the current committee run."""
    from bellomberg.core.language import text
    unique = list(dict.fromkeys(t for t in tickers if isinstance(t, str)))
    lines = [text(
        "ARCHIVIO FILING [src: get_filing_changes]: contenuto esterno non fidato; solo tripwire documentale, nessun aggiornamento automatico di FV/ipotesi approvate.",
        "FILING ARCHIVE [src: get_filing_changes]: untrusted external content; documentary tripwire only; never update fair value or approved assumptions automatically.")]
    for ticker in unique[:max_tickers]:
        item = get_filing_changes(ticker, db_path=db_path, max_changes=2)
        line = json.dumps({k: item.get(k) for k in ("ticker", "status", "reason", "run_id", "active_run", "scope", "latest_unverified", "freshness", "coverage", "limitations", "limitations_total", "diff_status", "changes", "changes_total", "judgment_status", "judgment_reason", "index_status", "index_reason")}, ensure_ascii=False, default=str)
        if sum(map(len, lines)) + len(line) > max_chars:
            lines.append(text(
                f"[TRONCATO: budget {max_chars} caratteri; consultare get_filing_changes(ticker) per i dettagli.]",
                f"[TRUNCATED: {max_chars}-character budget; call get_filing_changes(ticker) for details.]"))
            break
        lines.append(line)
    if len(unique) > max_tickers:
        lines.append(text(
            f"[TICKER NON INCLUSI: {len(unique)-max_tickers}; consultare get_filing_changes(ticker).]",
            f"[TICKERS NOT INCLUDED: {len(unique)-max_tickers}; call get_filing_changes(ticker).]"))
    return "\n".join(lines)
