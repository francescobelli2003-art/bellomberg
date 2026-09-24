"""Reproducible FDIC bank observations, separate from holding-company accounts.

No I/O, ownership inference or method records. The acquiring caller authenticates
the HTTP receipt; catalog validation later recompiles from the original response.
"""
from datetime import date
from hashlib import sha256
import json
from math import isfinite
from urllib.parse import urlsplit

from .document_evidence import source_dates


_REQUIRED = ("EQ", "EQPP", "RBCT1C")
_OPTIONAL = ("RWAJ", "RWAJT", "INTAN", "INTANGW", "INTANMSR", "ASSET", "LIAB", "CHBAL", "CHBALI")
_DEFINITIONS = "https://api.fdic.gov/banks/docs/risview_properties.yaml"
_UNITS = "https://banks.data.fdic.gov/bankfind-suite/financialreporting/report"


def bank_components(driver):
    """Common equity, CET1 and their opening bridge; never total capital."""
    import re
    if driver == 'liquidity_opening_cash':
        return {'CHBAL': 1}
    match = re.fullmatch(r"capital\.subsidiaries\.(?:0|[1-9][0-9]*)\.(.+)", driver)
    return {
        "opening_gaap_equity": {"EQ": 1, "EQPP": -1},
        "opening_statutory_capital": {"RBCT1C": 1},
        "opening_gaap_to_statutory_equity": {"RBCT1C": 1, "EQ": -1, "EQPP": 1},
    }.get(match[1]) if match else None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property: " + key)
        result[key] = value
    return result


def normalize_fdic_financials(document, *, expected_cert, expected_rssd,
                             expected_entity, expected_parent_rssd, expected_report_date):
    """Decode one exact bank/quarter, with availability observed on acquisition.

    Monetary RIS fields use the FDIC reporting unit, thousands of US dollars.
    The index creation timestamp is not the bank report's publication date.
    Regulatory top-holder identity does not establish direct or full ownership.
    """
    try:
        if any(type(value) is not int or value <= 0 for value in
               (expected_cert, expected_rssd, expected_parent_rssd)):
            raise ValueError("positive integer certificate/RSSD identities required")
        if (not isinstance(expected_entity, str) or not expected_entity.strip()
                or expected_entity != expected_entity.strip()):
            raise ValueError("exact bank entity required")
        period = date.fromisoformat(expected_report_date)
        if period.isoformat() != expected_report_date or period.strftime("%m%d") not in ("0331", "0630", "0930", "1231"):
            raise ValueError("exact quarter-end report date required")
        url = urlsplit(document["url"])
        if (url.scheme != "https" or url.hostname != "api.fdic.gov" or url.port not in (None, 443)
                or url.username or url.password or url.path != "/banks/financials" or url.fragment):
            raise ValueError("FDIC financials response URL required")
        body = document["text"]
        digest = sha256(body.encode("utf-8")).hexdigest()
        if any(document.get(key) != digest for key in ("id", "sha256", "document_sha256")):
            raise ValueError("original UTF-8 JSON byte identity mismatch")
        if document.get("published_at") is not None or document.get("availability_basis") != "observed_download":
            raise ValueError("FDIC availability must be observed download, not index creation or report date")
        dates = source_dates(document, date.fromisoformat(document["available_at"]))
        if period > date.fromisoformat(dates["available_at"]):
            raise ValueError("report date after observed availability")
        payload = json.loads(body, object_pairs_hook=_object)
        rows = payload["data"]
        if (not isinstance(rows, list) or len(rows) != 1
                or type(payload["meta"]["total"]) is not int or payload["meta"]["total"] != 1
                or type(payload["totals"]["count"]) is not int or payload["totals"]["count"] != 1):
            raise ValueError("one unambiguous bank/quarter response required; no truncated result")
        row = rows[0]["data"]
        if (type(row["CERT"]) is not int or row["CERT"] != expected_cert
                or type(row["RSSDID"]) is not int or row["RSSDID"] != expected_rssd
                or row["RSSDHCR"] != str(expected_parent_rssd) or row["NAME"] != expected_entity
                or row["REPDTE"] != period.strftime("%Y%m%d")):
            raise ValueError("FDIC bank, top holder or report period differs from expected identity")
        if not isinstance(row.get("NAMEHCR"), str) or not row["NAMEHCR"].strip():
            raise ValueError("reported regulatory top-holder name missing")
        # FDIC's IDT1CNOCB definition conditions CET1 on these flags. Read the
        # raw RBCT1C amount, never that derived field's zero default.
        for flag, expected in (("INSFDIC", 1), ("IBA", 0), ("CBLRIND", 0)):
            if type(row.get(flag)) not in (int, float) or row[flag] != expected:
                raise ValueError("CET1 reporting basis not established: " + flag)
        facts = []
        for code in (*_REQUIRED, *(key for key in _OPTIONAL if key in row)):
            value = row[code]
            if type(value) not in (int, float) or not isfinite(value):
                raise ValueError("missing/non-numeric FDIC amount: " + code)
            facts.append({"taxonomy": "fdic-ris", "concept": code, "value": value,
                "unit": "USD thousand", "end": expected_report_date, "entity": expected_entity,
                "scope": "bank_institution", "proof": {"source_document_id": digest,
                    "json_pointer": "/data/0/data/" + code, "definitions": _DEFINITIONS,
                    "unit_policy": _UNITS, "unit_policy_basis": "FDIC financial reports: Values in Thousands USD"}})
        text = json.dumps({"source_document_id": digest, "facts": facts},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        normalized = {"id": "fdic-facts-" + digest, "url": document["url"], **dates,
            "document_sha256": digest, "text": text, "sha256": sha256(text.encode()).hexdigest(),
            "metadata": {"normalizer": "fdic_financials_v1", "source_document_id": digest,
                "scope": "bank_institution", "entity": expected_entity, "cert": expected_cert,
                "rssd": expected_rssd, "parent_rssd": expected_parent_rssd,
                "cet1_reporting_flags": {key: row[key] for key in ("INSFDIC", "IBA", "CBLRIND")},
                "reported_top_holder": row["NAMEHCR"], "report_date": expected_report_date}}
        return {"status": "ready", "documents": [normalized], "issues": []}
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, AttributeError) as exc:
        return {"status": "incomplete", "documents": [], "issues": [{"field": "fdic_financials",
            "code": "unverified_source_fact", "reason": str(exc), "blocking": True}]}
