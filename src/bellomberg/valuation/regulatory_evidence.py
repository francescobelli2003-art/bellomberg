"""Exact FR Y-9LP PC / FR Y-9SP SC observations from an attested PDF.

This normalizer does no file or network I/O. It reads the text/page references
produced by document_evidence.pdf_document, never a filename or catalog date.
The caller remains responsible for authenticating the original PDF acquisition.
Only individually reported parent-company amounts are emitted; totals and
valuation inputs such as common equity, unrestricted cash or complete parent debt
are not inferred.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import re

from .document_evidence import source_dates, verify_page_references


_FORM = "FR Y-9LP"
_TAXONOMY = "fr-y-9lp-pc"
_SCOPE = "parent_only"
_UNIT = "USD thousand"
# Federal Reserve's U.S. FR Y-9LP General Instructions, C. Rounding, require
# dollar amounts in thousands. Each accepted Schedule PC page must itself say
# "Dollar Amounts in Thousands" under the BHCP header. Thus USD is the unit
# of this U.S. Federal Reserve reporting form, not a filename/caller default.
_UNIT_POLICY = "https://www.federalreserve.gov/apps/reportingforms/Download/DownloadAttachment?guid=eb9daf46-0a00-4955-8055-a0eac6350cf5"

# MDRM family BHCP is printed above Schedule PC Amount. Each field code below
# is paired with its exact form line label and item marker, not issuer values.
_FIELDS = (
    ("BHCP5993", "assets", "a. Balances with subsidiary or affiliated depository institutions", "1.a."),
    ("BHCP0010", "assets", "b. Balances with unrelated depository institutions", "1.b."),
    ("BHCP2309", "liabilities", "a. Commercial paper", "13.a."),
    ("BHCP2332", "liabilities", "b. Other borrowings", "13.b."),
    ("BHCP0368", "liabilities", "14. Other borrowed money with a remaining maturity of more than one year", "14."),
    ("BHCP4062", "liabilities", "16. Subordinated notes and debentures (1)", "16."),
    ("BHCP3283", "liabilities", "a. Perpetual preferred stock (including related surplus)", "20.a."),
    ("BHCP3210", "liabilities", "h. TOTAL EQUITY CAPITAL (sum of items 20.a through 20.f)", "20.h."),
)
# Additional individually reported liabilities establish their own scope only.
# Missing/blank rows are unavailable, never zero or an inferred debt total.
_LARGE_SUPPLEMENTAL = (
    ("BHCP2200", "liabilities", "11. Deposits", "11."),
    ("BHCP0279", "liabilities", "12. Securities sold under agreements to repurchase", "12."),
    ("BHCP2930", "liabilities", "17. Other liabilities", "17."),
    ("BHCP3605", "liabilities", "a. Subsidiary banks", "18.a."),
    ("BHCP3606", "liabilities", "b. Nonbank subsidiaries", "18.b."),
    ("BHCP3607", "liabilities", "c. Related holding companies", "18.c."),
    ("BHCP3300", "liabilities", "21. TOTAL LIABILITIES AND EQUITY CAPITAL (sum of items 11 through 20.f)", "21."),
)
# FR Y-9SP Schedule SC reports both sides on one page. Its item numbers,
# labels and BHSP family are distinct from Y-9LP; no debt mapping is inferred.
# Official form: https://www.federalreserve.gov/apps/reportingforms/Download/DownloadAttachment?guid=952d3fba-fbba-4d84-b663-afd312a62c67
_SMALL_FIELDS = (
    ("BHSP5993", "assets", "a. Balances with subsidiary or affiliated depository institutions", "1.a."),
    ("BHSP0010", "assets", "b. Balances with unrelated depository institutions", "1.b."),
    ("BHSP3283", "liabilities", "a. Perpetual preferred stock (including related surplus)", "16.a."),
    ("BHSP3210", "liabilities", "f. Total equity capital (sum of items 16.a through 16.e)", "16.f."),
)
# Individually observed carrying balances, never cash principal, standalone bank
# equity or implied zeros. Conditional blank boxes remain explicitly unavailable.
_SMALL_SUPPLEMENTAL = (
    ("BHSP3239", "assets", "a. Equity investment", "4.a."),
    ("BHSP3238", "assets", "b. Goodwill", "4.b."),
    ("BHSP3148", "assets", "c. Loans and advances to and receivables due from bank subsidiary(ies)", "4.c."),
    ("BHSP0088", "assets", "a. Equity investment", "5.a."),
    ("BHSP0201", "assets", "a. Equity investment", "6.a."),
    ("BHSP2309", "liabilities", "a. Commercial paper", "10.a."),
    ("BHSP2724", "liabilities", "b. Other short-term borrowings", "10.b."),
    ("BHSP3151", "liabilities", "11. Long-term borrowings (includes limited-life preferred stock and related surplus)", "11."),
    ("BHSP3605", "liabilities", "a. Subsidiary bank(s)", "14.a."),
    ("BHSP3621", "liabilities", "b. Nonbank subsidiaries and related institutions2", "14.b."),
)
_FORMS = {
    _FORM: {"taxonomy": _TAXONOMY, "schedule": "PC", "family": "BHCP",
            "fields": _FIELDS, "supplemental_fields": _LARGE_SUPPLEMENTAL,
            "unit_policy": _UNIT_POLICY},
    "FR Y-9SP": {"taxonomy": "fr-y-9sp-sc", "schedule": "SC", "family": "BHSP",
                 "fields": _SMALL_FIELDS, "supplemental_fields": _SMALL_SUPPLEMENTAL,
                 "unit_policy": "https://www.federalreserve.gov/apps/reportingforms/Download/DownloadAttachment?guid=30e57c13-081a-41e3-a24d-69b334b6fdc6"},
}
_MONTHS = {name: number for number, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"), 1)}
_AMOUNT = re.compile(r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)")


class _UnavailableObservation(ValueError):
    def __init__(self, concept, reason):
        super().__init__(concept + ": " + reason)
        self.reason = reason


def _issue(field, code, reason):
    return {"field": field, "code": code, "reason": reason, "blocking": True}


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _span(body, page, start, end):
    """Absolute character offset and original extracted-text hash."""
    text = body[start:end]
    return {"page": page, "char_start": start, "char_end_exclusive": end,
            "sha256_utf8": _digest(text)}


def _line_matches(body, pattern):
    return list(re.finditer(pattern, body, re.M))


def _valid_day(raw):
    if not isinstance(raw, str):
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == raw else None


def _source(document):
    if not isinstance(document, dict):
        raise ValueError("attested PDF document required")
    rawsha = document.get("document_sha256")
    if (not isinstance(rawsha, str) or not re.fullmatch(r"[0-9a-f]{64}", rawsha)
            or document.get("id") != rawsha):
        raise ValueError("source ID must equal attested PDF byte SHA-256")
    body = document.get("text")
    if not isinstance(body, str) or not body.strip() or document.get("sha256") != _digest(body):
        raise ValueError("original extracted text SHA-256 mismatch")
    coverage = document.get("extraction_coverage")
    refs = document.get("page_references")
    if (not isinstance(coverage, dict) or coverage.get("format") != "pdf"
            or coverage.get("raw_sha256_checked") is not True
            or type(coverage.get("pages")) is not int or coverage["pages"] != len(refs or [])):
        raise ValueError("attested PDF extraction coverage missing")
    verify_page_references(body, refs)
    available = _valid_day(document.get("available_at"))
    if available is None:
        raise ValueError("source availability date absent")
    dates = source_dates(document, available)
    pages = [(ref, body[ref["inizio"]:ref["fine"]]) for ref in refs]
    return body, rawsha, dates, pages


def _cover(body, ref, page_text, expected_entity, expected_report_date, form, document=None):
    if (form not in page_text or "Parent Company Only Financial Statements" not in page_text
            or "Board of Governors of the Federal Reserve System" not in page_text):
        raise ValueError(form + " parent-only cover missing")
    if document is not None and document.get('pdf_form_fields') is not None:
        from .pdf_form_evidence import form_field
        if page_text.count('Date of Report:') != 1 or page_text.count('Legal Title of Holding Company (RSSD 9017)') != 1:
            raise ValueError('PDF cover date/entity labels absent or ambiguous')
        value, period = form_field(document, 'TEXT9999', ref['pagina'])
        match = re.fullmatch(r'([A-Za-z]+)\s+([0-9]{1,2}),\s+([0-9]{4})', value)
        if match is None or match[1] not in _MONTHS:
            raise ValueError('PDF report-date field invalid')
        day = date(int(match[3]), _MONTHS[match[1]], int(match[2])).isoformat()
        entity, entity_proof = form_field(document, 'NM_LGL', ref['pagina'])
        if day != expected_report_date or entity != expected_entity:
            raise ValueError('expected entity/report date differs from PDF fields')
        printed_dates = _line_matches(page_text, r'^Date of Report:[ \t]+([^\n]+)[ \t]*$')
        if printed_dates and (len(printed_dates) != 1 or printed_dates[0][1].strip() != value):
            raise ValueError('printed report date differs from PDF field')
        printed_entities = _line_matches(page_text,
            r'^([^\n]+)\nPrinted Name of Chief Financial Officer[^\n]*Legal Title of Holding Company \(RSSD 9017\)\s*$')
        if printed_entities and (len(printed_entities) != 1 or printed_entities[0][1].strip() != entity):
            raise ValueError('printed legal entity differs from PDF field')
        return entity_proof, period
    date_matches = _line_matches(page_text, r"^Date of Report:\s+([A-Za-z]+)\s+([0-9]{1,2}),\s+([0-9]{4})\s*$")
    if len(date_matches) != 1:
        raise ValueError("unique literal Date of Report missing")
    date_match = date_matches[0]
    month = _MONTHS.get(date_match[1])
    if month is None:
        raise ValueError("unknown report month")
    reported = date(int(date_match[3]), month, int(date_match[2])).isoformat()
    if reported != expected_report_date:
        raise ValueError("expected report date differs from PDF cover")
    title_matches = _line_matches(page_text,
        r"^([^\n]+)\nPrinted Name of Chief Financial Officer[^\n]*Legal Title of Holding Company \(RSSD 9017\)\s*$")
    if len(title_matches) != 1:
        raise ValueError("unique legal-title line missing from PDF cover")
    title = title_matches[0]
    observed_entity = title[1].strip()
    if not observed_entity or observed_entity != expected_entity:
        raise ValueError("expected legal entity differs from PDF cover")
    return (_span(body, ref["pagina"], ref["inizio"] + title.start(1), ref["inizio"] + title.end(1)),
            _span(body, ref["pagina"], ref["inizio"] + date_match.start(), ref["inizio"] + date_match.end()))


def _schedule_page(body, pages, kind, form, definition):
    candidates = []
    schedule, family = definition["schedule"], definition["family"]
    for ref, text in pages:
        if schedule == "SC":
            selected = ("Schedule SC" in text and "Balance Sheet" in text
                        and re.search(r"(?m)^Assets\s*$", text)
                        and re.search(r"(?m)^Liabilities and Equity Capital\s*$", text))
        elif kind == "assets":
            selected = ("Schedule PC" in text and "Parent Company Only Balance Sheet" in text
                        and re.search(r"(?m)^Assets\s*$", text))
        else:
            selected = ("Schedule PC" in text and "Continued" in text
                        and re.search(r"(?m)^Liabilities and Equity Capital\s*$", text))
        if selected:
            candidates.append((ref, text))
    if len(candidates) != 1:
        raise ValueError("Schedule " + schedule + " " + kind + " page absent or ambiguous")
    ref, text = candidates[0]
    if form not in text:
        raise ValueError("form header absent on Schedule " + schedule + " page")
    unit_matches = _line_matches(text, r"^Dollar Amounts in Thousands\s+" + family + r" Amount\s*$")
    if len(unit_matches) != 1:
        raise ValueError("unique Schedule " + schedule + " dollar-thousands/" + family + " header absent")
    match = unit_matches[0]
    unit_proof = _span(body, ref["pagina"], ref["inizio"] + match.start(), ref["inizio"] + match.end())
    return ref, text, unit_proof


def _fact(body, ref, page_text, unit_proof, concept, label, item, entity, report_date,
          entity_proof, period_proof, rawsha, form, definition, document=None):
    code = concept.removeprefix(definition["family"])
    candidates = _line_matches(page_text, r"^.*(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9]).*$")
    if not candidates:
        raise _UnavailableObservation(concept, "field_not_reported")
    if len(candidates) != 1:
        raise ValueError(concept + ": unique field code absent or duplicated")
    matched = candidates[0]
    line = matched.group(0)
    field_proof = None
    amount = ""
    allowed_labels = {label}
    # Current official PC form prints footnote 1 as a superscript. Accept only
    # this exact rendering for this field, preserving the observed label/proof.
    if form == "FR Y-9LP" and concept == "BHCP4062":
        allowed_labels.add("16. Subordinated notes and debentures1")
    if document is not None and document.get('pdf_form_fields') is not None:
        from .pdf_form_evidence import form_field
        amount, field_proof = form_field(document, concept, ref['pagina'], allow_blank=True)
    blank = re.fullmatch(r'\s*(.*?)\s*\.{2,}\s*' + re.escape(code) + r'\s+' + re.escape(item) + r'\s*', line)
    if blank is not None:
        observed_label = ' '.join(blank[1].split())
        if observed_label not in allowed_labels:
            raise ValueError(concept + ': PDF field label drift')
        if not amount.strip():
            raise _UnavailableObservation(concept, "blank_amount")
        # Parsed copy only: original page text and its proof offsets stay unchanged.
        line = blank[1] + ' .. ' + code + ' ' + amount + ' ' + item
    parsed = re.fullmatch(r"\s*(.*?)\s*\.{2,}\s*" + re.escape(code)
                          + r"\s+(\S+)\s+(\S+)\s*", line)
    if parsed is None:
        raise ValueError(concept + ": label, amount or column layout ambiguous")
    observed_label = " ".join(parsed[1].split())
    if observed_label not in allowed_labels or parsed[3] != item:
        raise ValueError(concept + ": field label or line item drift")
    amount = parsed[2]
    if field_proof is not None and amount != field_proof['value'].strip():
        raise ValueError(concept + ': page text and PDF field differ')
    # General Instructions D expressly permit negative Schedule SC investments.
    magnitude = amount[1:] if form == 'FR Y-9SP' and concept in (
        'BHSP3239', 'BHSP0088', 'BHSP0201') and amount.startswith('-') else amount
    if not _AMOUNT.fullmatch(magnitude):
        raise ValueError(concept + ": amount blank, N/A or non-numeric")
    value = int(amount.replace(",", ""))
    start, end = ref["inizio"] + matched.start(), ref["inizio"] + matched.end()
    proof = {"source_document_id": rawsha, "page": ref["pagina"],
             "page_sha256_utf8": ref["sha256"], "char_start": start,
             "char_end_exclusive": end, "row_sha256_utf8": _digest(body[start:end]),
             "field_code": concept, "line_item": item, "label": observed_label,
             "unit_proof": unit_proof, "period_proof": period_proof,
             "entity_proof": entity_proof, "unit_policy_url": definition["unit_policy"],
             "unit_policy_basis": "USD inferred from U.S. Federal Reserve " + form + " form and Schedule " + definition["schedule"] + " dollar-thousands header"}
    if field_proof is not None:
        proof['form_field'] = field_proof
    return {"taxonomy": definition["taxonomy"], "concept": concept, "value": value,
            "unit": _UNIT, "end": report_date, "entity": entity, "scope": _SCOPE,
            "proof": proof}


def normalize_regulatory_pdf(document, *, expected_form, expected_entity, expected_report_date):
    """Compile Y-9LP PC or Y-9SP SC; fail closed on any required missing field.

    Call only on a document constructed by ``pdf_document`` from original PDF
    bytes. The normalized JSON is an observation catalog, not a method record,
    approved input, total parent cash/debt, or subsidiary capital bridge.
    """
    definition = _FORMS.get(expected_form) if isinstance(expected_form, str) else None
    if definition is None:
        return {"status": "unsupported", "documents": [], "issues": [
            _issue("form", "unsupported_form", "Normalizer supports parent-only FR Y-9LP PC and FR Y-9SP SC")]}
    if (not isinstance(expected_entity, str) or not expected_entity.strip()
            or expected_entity != expected_entity.strip() or "\n" in expected_entity
            or _valid_day(expected_report_date) is None):
        return {"status": "incomplete", "documents": [], "issues": [
            _issue("contract", "invalid_expectation", "Exact legal entity and ISO report date required")]}
    try:
        body, rawsha, dates, pages = _source(document)
        entity_proof, period_proof = _cover(body, *pages[0], expected_entity, expected_report_date, expected_form, document)
        if _valid_day(expected_report_date) > _valid_day(dates["available_at"]):
            raise ValueError("literal report date exceeds documented source availability")
        selected = {kind: _schedule_page(body, pages, kind, expected_form, definition) for kind in ("assets", "liabilities")}
        facts, unavailable = [], {}
        for concept, kind, label, item in definition["fields"]:
            ref, text, unit_proof = selected[kind]
            facts.append(_fact(body, ref, text, unit_proof, concept, label, item,
                               expected_entity, expected_report_date,
                               entity_proof, period_proof, rawsha, expected_form, definition, document))
        for concept, kind, label, item in definition.get("supplemental_fields", ()):
            ref, text, unit_proof = selected[kind]
            try:
                facts.append(_fact(body, ref, text, unit_proof, concept, label, item,
                    expected_entity, expected_report_date, entity_proof, period_proof,
                    rawsha, expected_form, definition, document))
            except _UnavailableObservation as exc:
                unavailable[concept] = exc.reason
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        return {"status": "incomplete", "documents": [], "issues": [
            _issue("regulatory_pdf", "unverified_source_fact", str(exc))]}

    payload = {"form": expected_form, "source_document_id": rawsha, "facts": facts}
    if definition.get("supplemental_fields"):
        payload["unavailable_fields"] = unavailable
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    normalized = {"id": "regulatory-facts-" + rawsha, "url": document["url"],
                  "published_at": dates["published_at"],
                  "available_at": dates["available_at"],
                  "availability_basis": dates["availability_basis"],
                  "retrieval": deepcopy(dates["retrieval"]),
                  "document_sha256": rawsha, "text": text, "sha256": _digest(text),
                  "metadata": {"source_document_id": document["id"],
                               "entity": expected_entity, "scope": _SCOPE,
                               "report_date": expected_report_date, "form": expected_form,
                               "normalizer": "regulatory_pdf_v1"}}
    return {"status": "ready", "documents": [normalized], "issues": []}
