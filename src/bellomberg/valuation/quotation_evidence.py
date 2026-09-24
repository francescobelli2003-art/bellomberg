"""Explicit listing-unit identity and dated unadjusted price observations."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
import json
import re
from urllib.parse import quote


def listing_identity(raw, ticker):
    """An ordinary share maps 1:1 only within the same verified listing class.

    ADRs, preferred shares, debt, units and ambiguous cover rows do not receive a
    ratio. This is a disclosed unit identity, never a guessed economic input.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "html.parser")
    contexts = {}
    names = {"dei:Security12bTitle": "title", "dei:TradingSymbol": "symbol",
             "dei:SecurityExchangeName": "exchange"}
    for node in soup.find_all("ix:nonnumeric"):
        key, context = names.get(node.get("name")), node.get("contextref")
        if key and context:
            contexts.setdefault(context, {}).setdefault(key, set()).add(node.get_text(" ", strip=True))
    matches = []
    for context, fields in contexts.items():
        if set(fields) != set(names.values()) or any(len(v) != 1 for v in fields.values()):
            continue
        item = {key: next(iter(values)) for key, values in fields.items()}
        if item["symbol"] != ticker or not item["exchange"]:
            continue
        title = item["title"].lower()
        if (not re.search(r"\b(common (?:stock|shares)|ordinary shares)\b", title)
            or re.search(r"\b(depositary|depository|adr|ads|preferred|preference|units|notes|bonds)\b", title)):
            continue
        matches.append({**item, "context_id": context})
    if len(matches) != 1:
        return {"status": "incomplete", "reason": "ordinary listing class missing or ambiguous; ratio requires evidence"}
    return {"status": "verified", **matches[0], "shares_per_quote": 1,
            "basis": "one_listed_ordinary_share_is_one_share_of_the_same_class"}


def historical_quote_document(ticker, *, on, as_of, fetch=None, include_identity=False):
    try:
        day = date.fromisoformat(on)
        if day > date.fromisoformat(as_of):
            raise ValueError("price date after information cutoff")
        if fetch is None:
            import yfinance as yf
            def fetch(symbol, target):
                instrument = yf.Ticker(symbol)
                rows = instrument.history(start=target, end=(date.fromisoformat(target) + timedelta(days=1)).isoformat(),
                                          auto_adjust=False, actions=False, raise_errors=True)
                metadata = instrument.history_metadata
                if len(rows) != 1:
                    raise ValueError("no unique unadjusted close for requested date")
                observed = {"symbol": metadata.get("symbol"), "currency": metadata.get("currency"),
                            "date": rows.index[0].date().isoformat(), "close": float(rows.iloc[0]["Close"])}
                if include_identity:
                    observed['identity'] = {key: metadata.get(key) for key in
                        ('symbol', 'longName', 'exchangeName', 'fullExchangeName', 'instrumentType', 'currency')}
                    observed['identity_observed_on'] = datetime.now(timezone.utc).date().isoformat()
                return observed
        raw = fetch(ticker, on)
        value, currency = raw.get("close"), raw.get("currency")
        if raw.get("symbol") != ticker or raw.get("date") != on:
            raise ValueError("price identity/date differs from request")
        if type(value) not in (int, float) or not isfinite(value) or value <= 0:
            raise ValueError("observed close unavailable")
        if not isinstance(currency, str) or not (re.fullmatch("[A-Z]{3}", currency) or currency == "GBp"):
            raise ValueError("observed quote currency unavailable")
        text = json.dumps({"symbol": ticker, "observation": raw,
                           "facts": [{"value": value, "unit": currency + " per share", "end": on}]},
                          ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        document = {"id": "price-" + ticker + "-" + on, "published_at": on,
                    "url": "https://finance.yahoo.com/quote/" + quote(ticker, safe="") + "/history/",
                    "text": text, "sha256": sha256(text.encode()).hexdigest(),
                    "origin": "yfinance_unadjusted_close", "metadata": {"ticker": ticker, "price_as_of": on},
                    "extraction_coverage": {"status": "single_unadjusted_close", "missing_day_policy": "no_fallback"}}
        return {"status": "ready", "documents": [document], "issues": []}
    except Exception as exc:
        return {"status": "incomplete", "documents": [],
                "issues": [{"source": "yfinance historical close", "reason": type(exc).__name__ + ": " + str(exc)}]}


def listing_identity_document(source, raw, *, ticker, on):
    if sha256(raw).hexdigest() != source.get("document_sha256"):
        raise ValueError("listing document bytes differ from verified primary source")
    if (source.get("metadata") or {}).get("report_date") != on:
        raise ValueError("listing evidence must belong to the opening-period filing")
    identity = listing_identity(raw, ticker)
    if identity["status"] != "verified":
        return {"status": "incomplete", "documents": [], "issues": [identity]}
    text = json.dumps({"listing": identity, "facts": [{"value": 1, "unit": "shares per quote", "end": on}]},
                      ensure_ascii=False, separators=(",", ":"))
    document = {"id": "listing-" + source["id"], "url": source["url"],
                "published_at": source["published_at"], "text": text,
                "sha256": sha256(text.encode()).hexdigest(), "document_sha256": source["document_sha256"],
                "origin": "SEC_listing_unit_identity", "metadata": {"ticker": ticker,
                    "share_class": identity["title"], "basis": identity["basis"], "report_date": on},
                "extraction_coverage": {"status": "derived_unit_identity", "context_id": identity["context_id"],
                    "limitation": "same listed ordinary class only; no ADR ratio or cross-class conversion"}}
    return {"status": "ready", "documents": [document], "issues": []}
