"""Read persisted source changes and enqueue opt-in valuation refreshes.

This module does not acquire sources or approve economic inputs. Filing Diff's
verified candidates identify an event; the preparer revalidates and selects the
documents. Guidance rows identify a change only, never method records.
"""

from contextlib import closing
from datetime import date, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.^=_:/-]{0,31}\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_META = ("emittente_id", "lingua", "tipo", "perimetro", "periodo_inizio", "periodo_fine")
_SOURCES = frozenset(("SEC EDGAR", "ESEF", "IR"))
_STATES = frozenset(("verificato", "duplicato", "versione_ambigua", "non_verificato", "non_applicabile"))


class IneligibleSource(ValueError):
    """A persisted source cannot safely authorize a refresh event."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _iso(value, field):
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise IneligibleSource(field + " missing or not an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise IneligibleSource(field + " is not a calendar date") from exc


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise IneligibleSource(field + " missing or invalid")
    return value


def _filing_documents(result):
    if not isinstance(result, dict) or result.get("src") != "filing_pipeline":
        raise IneligibleSource("persisted Filing Diff pipeline result required")
    ticker = result.get("ticker")
    if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker):
        raise IneligibleSource("Filing Diff ticker missing or invalid")
    if result.get("ultimo_non_verificato") is not False:
        raise IneligibleSource("latest filing unverified or coverage flag missing")
    candidates = result.get("candidati")
    if not isinstance(candidates, list):
        raise IneligibleSource("Filing Diff candidate list missing or malformed")
    identities, accepted, periods, unverified = set(), [], {}, []
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("stato") not in _STATES:
            raise IneligibleSource("Filing Diff candidate state malformed")
        state = candidate["stato"]
        if state == "versione_ambigua":
            raise IneligibleSource("ambiguous document version; no automatic refresh")
        if state == "non_verificato":
            unverified.append(candidate)
        if state not in ("verificato", "duplicato"):
            continue
        if candidate.get("fonte") not in _SOURCES:
            raise IneligibleSource("verified document source missing or invalid")
        url = _text(candidate.get("url"), "verified document URL")
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise IneligibleSource("verified document URL malformed") from exc
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise IneligibleSource("verified document URL is not HTTP(S) without credentials")
        path = _text(candidate.get("path"), "verified document path")
        if not Path(path).is_absolute():
            raise IneligibleSource("verified document path is not absolute")
        digest = candidate.get("sha256")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise IneligibleSource("verified document byte SHA256 missing or invalid")
        metadata = candidate.get("metadati")
        if not isinstance(metadata, dict):
            raise IneligibleSource("verified document metadata missing")
        meta = tuple(_text(metadata.get(field), field) for field in _META)
        beginning, end = _iso(meta[4], "periodo_inizio"), _iso(meta[5], "periodo_fine")
        published = _iso(candidate.get("filed_date"), "filed_date")
        if beginning > end or published < end:
            raise IneligibleSource("filing publication precedes the verified reporting period")
        previous = periods.setdefault(meta, digest)
        if previous != digest:
            raise IneligibleSource("conflicting byte hashes for one issuer/reporting period")
        identities.add((digest, *meta, published.isoformat()))
        accepted.append(candidate)
    if accepted:
        latest = max(_iso(candidate["metadati"]["periodo_fine"], "periodo_fine")
                     for candidate in accepted)
        for candidate in unverified:
            period = (candidate.get("report_date") or candidate.get("period_end")
                      or (candidate.get("metadati") or {}).get("periodo_fine"))
            if period is None or _iso(period, "unverified candidate period") >= latest:
                raise IneligibleSource("newer or undated filing is unverified")
    return ticker, sorted(identities), accepted


def event_key(result):
    """Pure identity of eligible verified filings; no clock, paths or judgments.

    ``None`` means that no eligible verified document exists. A malformed or
    ambiguous result raises ``IneligibleSource`` and must be reported, not skipped.
    The caller must separately recheck archived bytes and the information cutoff.
    """
    ticker, documents, _ = _filing_documents(result)
    if not documents:
        return None
    body = {"version": 1, "ticker": ticker, "documents": documents}
    return "filing-documents-v1:" + sha256(_canonical(body).encode("utf-8")).hexdigest()


def _verify_archive(candidates, archive_root, cutoff):
    root = Path(archive_root).resolve()
    for candidate in candidates:
        if _iso(candidate["filed_date"], "filed_date") > cutoff:
            raise IneligibleSource("filing publication is after the current information cutoff")
        path = Path(candidate["path"]).resolve()
        if not path.is_relative_to(root):
            raise IneligibleSource("verified document path outside preparation archive")
        try:
            with path.open("rb") as stream:
                digest = sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError as exc:
            raise IneligibleSource("verified archived document unavailable: " + str(exc)) from exc
        if digest.hexdigest() != candidate["sha256"]:
            raise IneligibleSource("verified archived document byte SHA256 changed")


def _guidance_key(ticker, rows, cutoff):
    if not rows:
        return None
    facts, by_metric = [], {}
    for row in rows:
        metric = _text(row.get("metric"), "guidance metric")
        period = _text(row.get("period"), "guidance period")
        unit = _text(row.get("unit"), "guidance unit")
        source = _text(row.get("source_doc"), "guidance source_doc")
        source_date = _iso(row.get("source_date"), "guidance source_date")
        valid_until = _iso(row.get("valid_until"), "guidance valid_until")
        if source_date > cutoff:
            raise IneligibleSource("guidance source_date after current information cutoff")
        if valid_until < source_date:
            raise IneligibleSource("guidance validity ends before source_date")
        if valid_until < cutoff:
            raise IneligibleSource("stale active guidance; source validity expired before cutoff")
        values = []
        for field in ("value_low", "value_mid", "value_high"):
            value = row.get(field)
            if value is None and field != "value_mid":
                values.append(None)
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise IneligibleSource("guidance " + field + " is not finite numeric evidence")
            else:
                values.append(float(value))
        low, mid, high = values
        if (low is not None and low > mid) or (high is not None and high < mid):
            raise IneligibleSource("guidance range conflicts with value_mid")
        fact = {"metric": metric, "period": period, "unit": unit,
                "value_low": low, "value_mid": mid, "value_high": high,
                "source_doc": source, "source_date": source_date.isoformat(),
                "valid_until": valid_until.isoformat(),
                "valid_until_source": row.get("valid_until_source")}
        group = (metric, period, unit)
        previous = by_metric.setdefault(group, fact)
        if previous != fact:
            raise IneligibleSource("conflicting active guidance for one metric/period/unit")
        facts.append(fact)
    # Row IDs, entry times, authors and registration notes are not economic facts.
    body = {"version": 1, "ticker": ticker,
            "active_guidance": sorted({_canonical(fact) for fact in facts})}
    return "guidance-active-v1:" + sha256(_canonical(body).encode("utf-8")).hexdigest()


def _read_db(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _error(source, exc):
    return {"source": source, "status": "error", "reason": type(exc).__name__ + ": " + str(exc)[:500]}


def _tracked(conn, errors):
    tickers = set()
    for source, query in (
        ("portfolio", "SELECT DISTINCT ticker FROM positions WHERE quantita>0 AND is_active=1"),
        ("watchlist", "SELECT DISTINCT ticker FROM favorite_companies"),
    ):
        try:
            for (value,) in conn.execute(query):
                if not isinstance(value, str) or not _TICKER.fullmatch(value.strip().upper()):
                    raise IneligibleSource("tracked ticker missing or invalid")
                tickers.add(value.strip().upper())
        except (sqlite3.Error, IneligibleSource) as exc:
            errors.append(_error(source, exc))
    return sorted(tickers)


def _filing_rows(conn, tickers, errors):
    query = """SELECT id,status,profile_json,result_json FROM filing_runs
        WHERE ticker=? AND (status NOT IN ('queued','running') OR status IS NULL)
        ORDER BY id DESC LIMIT 1"""
    try:
        conn.execute(query, ("",)).fetchone()  # Also checks required schema when untracked.
        return {ticker: conn.execute(query, (ticker,)).fetchone() for ticker in tickers}
    except sqlite3.Error as exc:
        errors.append(_error("filing_diff", exc))
        return None


def _guidance_rows(conn, tickers, errors):
    query = """SELECT metric,period,value_low,value_mid,value_high,unit,source_doc,
        source_date,valid_until,valid_until_source FROM company_guidance
        WHERE ticker=? AND status='active'"""
    try:
        conn.execute(query, ("",)).fetchall()
        return {ticker: [dict(row) for row in conn.execute(query, (ticker,))] for ticker in tickers}
    except sqlite3.Error as exc:
        errors.append(_error("guidance", exc))
        return None


def _filing_outcome(manager, ticker, row, cutoff):
    prefix = {"ticker": ticker, "trigger": "filing_diff"}
    if row is None:
        return {**prefix, "status": "no_evidence", "reason": "no finished Filing Diff run with result"}
    try:
        if row["status"] not in ("ok", "parziale", "errore", "non_disponibile", "skipped"):
            raise IneligibleSource("latest finished Filing Diff status invalid")
        if row["result_json"] is None:
            raise IneligibleSource("latest finished Filing Diff run without result; older proof not reused")
        if row["status"] in ("non_disponibile", "skipped"):
            raise IneligibleSource("latest finished Filing Diff run has no verified source")
        result = json.loads(row["result_json"])
        profile = json.loads(row["profile_json"])
        if not isinstance(profile, dict):
            raise IneligibleSource("Filing Diff profile malformed")
        key = event_key(result)
        if result["ticker"] != ticker:
            raise IneligibleSource("Filing Diff result ticker differs from tracked ticker")
        _, _, candidates = _filing_documents(result)
        from bellomberg.market_data.filing_verifica import _id
        expected = _id(profile.get("emittente_id"))
        for candidate in candidates:
            metadata = candidate["metadati"]
            if metadata["emittente_id"] != expected or any(
                    metadata[field] != profile.get(field) for field in ("lingua", "tipo", "perimetro")):
                raise IneligibleSource("verified issuer or report scope differs from persisted profile")
        if key is None:
            if row["status"] == "errore":
                raise IneligibleSource("latest failed Filing Diff run has no verified source")
            return {**prefix, "status": "no_evidence", "reason": "no eligible verified filing"}
        _verify_archive(candidates, manager.runtime.archive_root, cutoff)
    except Exception as exc:
        return {**prefix, "run_id": row["id"], "status": "blocked",
                "reason": type(exc).__name__ + ": " + str(exc)[:500]}
    try:
        queued = manager.enqueue_refresh(ticker, "filing_diff", key, filing_results=[result])
        return {**prefix, "run_id": row["id"], **{field: queued[field] for field in
                ("id", "status", "reused", "reason") if field in queued}}
    except Exception as exc:
        return {**prefix, "run_id": row["id"], "status": "error",
                "reason": type(exc).__name__ + ": " + str(exc)[:500]}


def _guidance_outcome(manager, ticker, rows, cutoff):
    prefix = {"ticker": ticker, "trigger": "guidance"}
    try:
        key = _guidance_key(ticker, rows, cutoff)
        if key is None:
            return {**prefix, "status": "no_evidence", "reason": "no active company guidance"}
    except Exception as exc:
        return {**prefix, "status": "blocked", "reason": type(exc).__name__ + ": " + str(exc)[:500]}
    try:
        queued = manager.enqueue_refresh(ticker, "guidance", key)
        return {**prefix, **{field: queued[field] for field in
                ("id", "status", "reused", "reason") if field in queued}}
    except Exception as exc:
        return {**prefix, "status": "error", "reason": type(exc).__name__ + ": " + str(exc)[:500]}


def reconcile_source_events(manager):
    """Poll committed Filing Diff and guidance with SQLite read-only, then queue.

    An opt-in trigger is required before opening the DB. Source reads finish and
    close before any queue write. One broken source is reported independently;
    it cannot silently turn an older filing or subset of guidance into evidence.
    """
    try:
        state = manager.runtime.status()
    except Exception as exc:
        return {"status": "unavailable", "sources": [_error("policy", exc)], "outcomes": []}
    triggers = set(state.get("triggers", ())) & {"filing_diff", "guidance"}
    if state.get("status") != "configured" or not triggers:
        return {"status": "disabled", "reason": "source_triggers_not_authorized",
                "sources": [], "outcomes": []}
    errors, outcomes = [], []
    try:
        instant = manager.jobs.clock()
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("queue clock must return a timezone-aware datetime")
        cutoff = instant.date()
        with closing(_read_db(manager.jobs.db_path)) as conn:
            tickers = _tracked(conn, errors)
            filings = _filing_rows(conn, tickers, errors) if "filing_diff" in triggers else None
            guidance = _guidance_rows(conn, tickers, errors) if "guidance" in triggers else None
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"status": "unavailable", "sources": errors + [_error("source_db", exc)],
                "outcomes": []}
    for ticker in tickers:
        if filings is not None:
            outcomes.append(_filing_outcome(manager, ticker, filings[ticker], cutoff))
        if guidance is not None:
            outcomes.append(_guidance_outcome(manager, ticker, guidance[ticker], cutoff))
    partial = bool(errors) or any(row["status"] in ("blocked", "error") for row in outcomes)
    return {"status": "partial" if partial else "ok", "sources": errors, "outcomes": outcomes}
