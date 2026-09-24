"""Synthetic FR Y-9LP source proof; no issuer data, provider or database."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.regulatory_evidence import normalize_regulatory_pdf


ENTITY = "Synthetic Holding Company"
REPORT_DATE = "2026-06-30"


def _document():
    pages = [
        "\n".join(("FR Y-9LP", "Board of Governors of the Federal Reserve System",
                   "Parent Company Only Financial Statements for Large Holding Companies",
                   "Date of Report: June 30, 2026", ENTITY,
                   "Printed Name of Chief Financial Officer (or Equivalent) (BHCP C490) Legal Title of Holding Company (RSSD 9017)")),
        "\n".join(("FR Y-9LP", "Schedule PC—Parent Company Only Balance Sheet",
                   "Dollar Amounts in Thousands  BHCP Amount", "Assets",
                   "a. Balances with subsidiary or affiliated depository institutions ........ 5993 1,200 1.a.",
                   "b. Balances with unrelated depository institutions ........ 0010 0 1.b.")),
        "\n".join(("FR Y-9LP", "Schedule PC— Continued",
                   "Dollar Amounts in Thousands  BHCP Amount", "Liabilities and Equity Capital",
                   "a. Commercial paper ........ 2309 40 13.a.",
                   "b. Other borrowings ........ 2332 50 13.b.",
                   "14. Other borrowed money with a remaining maturity of more than one year ........ 0368 600 14.",
                   "16. Subordinated notes and debentures (1) ........ 4062 70 16.",
                   "a. Perpetual preferred stock (including related surplus) ........ 3283 80 20.a.",
                   "h. TOTAL EQUITY CAPITAL (sum of items 20.a through 20.f) ........ 3210 2,000 20.h.")),
    ]
    refs, position = [], 0
    for number, page in enumerate(pages, 1):
        refs.append({"pagina": number, "inizio": position, "fine": position + len(page),
                     "sha256": sha256(page.encode("utf-8")).hexdigest()})
        position += len(page) + 1
    body = "\n".join(pages)
    rawsha = sha256(b"synthetic PDF bytes").hexdigest()
    url = "https://example.test/regulatory.pdf"
    return {"id": rawsha, "url": url, "published_at": None,
            "document_sha256": rawsha, "text": body,
            "sha256": sha256(body.encode("utf-8")).hexdigest(),
            "availability_basis": "observed_download", "available_at": "2026-09-10",
            "retrieval": {"url": url, "document_sha256": rawsha,
                          "retrieved_at": "2026-09-10T08:00:00Z"},
            "page_references": refs,
            "extraction_coverage": {"format": "pdf", "pages": 3,
                                    "raw_sha256_checked": True}}


def _normalize(document, **changes):
    return normalize_regulatory_pdf(document, expected_form=changes.get("form", "FR Y-9LP"),
                                    expected_entity=changes.get("entity", ENTITY),
                                    expected_report_date=changes.get("date", REPORT_DATE))


def _changed(document, before, after):
    assert before in document["text"]
    document["text"] = document["text"].replace(before, after)
    document["sha256"] = sha256(document["text"].encode("utf-8")).hexdigest()
    pages = document["text"].split("\nFR Y-9LP\n")
    # Rebuild page references after a deliberate source change, so parsing—not
    # a stale page hash—must reject semantic drift.
    pages = [pages[0]] + ["FR Y-9LP\n" + page for page in pages[1:]]
    assert len(pages) == 3
    refs, position = [], 0
    for number, page in enumerate(pages, 1):
        refs.append({"pagina": number, "inizio": position, "fine": position + len(page),
                     "sha256": sha256(page.encode("utf-8")).hexdigest()})
        position += len(page) + 1
    document["page_references"] = refs
    return document


def test_parent_pc_facts_are_exact_sourced_json_and_deterministic():
    document = _document()
    original = deepcopy(document)
    result = _normalize(document)
    assert result["status"] == "ready" and result["issues"] == []
    assert document == original
    assert len(result["documents"]) == 1
    factdoc = result["documents"][0]
    assert factdoc["id"] == "regulatory-facts-" + document["document_sha256"]
    assert factdoc["document_sha256"] == document["document_sha256"]
    assert factdoc["metadata"] == {"source_document_id": document["id"], "entity": ENTITY,
                                   "scope": "parent_only", "report_date": REPORT_DATE,
                                   "form": "FR Y-9LP", "normalizer": "regulatory_pdf_v1"}
    assert factdoc["published_at"] is None and factdoc["available_at"] == "2026-09-10"
    assert factdoc["sha256"] == sha256(factdoc["text"].encode("utf-8")).hexdigest()
    facts = json.loads(factdoc["text"])["facts"]
    by_concept = {fact["concept"]: fact for fact in facts}
    assert set(by_concept) == {"BHCP5993", "BHCP0010", "BHCP2309", "BHCP2332",
                               "BHCP0368", "BHCP4062", "BHCP3283", "BHCP3210"}
    assert by_concept["BHCP5993"]["value"] == 1200
    assert by_concept["BHCP0010"]["value"] == 0  # explicitly reported zero
    assert by_concept["BHCP3210"]["value"] == 2000
    for fact in facts:
        assert fact["taxonomy"] == "fr-y-9lp-pc"
        assert fact["unit"] == "USD thousand"
        assert fact["end"] == REPORT_DATE and fact["entity"] == ENTITY
        proof = fact["proof"]
        row = document["text"][proof["char_start"]:proof["char_end_exclusive"]]
        assert sha256(row.encode("utf-8")).hexdigest() == proof["row_sha256_utf8"]
        assert fact["concept"] == proof["field_code"]
        assert document["page_references"][proof["page"] - 1]["sha256"] == proof["page_sha256_utf8"]
        assert "federalreserve.gov" in proof["unit_policy_url"]
        assert proof["unit_policy_basis"].startswith("USD inferred")
        for name in ("entity", "period", "unit"):
            segment = proof[name + "_proof"]
            text = document["text"][segment["char_start"]:segment["char_end_exclusive"]]
            assert sha256(text.encode("utf-8")).hexdigest() == segment["sha256_utf8"]
    assert _normalize(deepcopy(document)) == result


@pytest.mark.parametrize("change", ["missing", "blank", "na", "duplicate", "label", "extra_column",
                                    "unit", "entity", "date", "page_hash", "source_hash", "raw_id",
                                    "receipt", "unattested", "authority"])
def test_unverifiable_source_or_schedule_does_not_emit_numeric_facts(change):
    document = _document()
    if change == "missing":
        _changed(document, "2332 50 13.b.", "")
    elif change == "blank":
        _changed(document, "2332 50 13.b.", "2332   13.b.")
    elif change == "na":
        _changed(document, "2332 50 13.b.", "2332 N/A 13.b.")
    elif change == "duplicate":
        _changed(document, "2332 50 13.b.", "2332 50 13.b.\nb. Other borrowings ........ 2332 50 13.b.")
    elif change == "label":
        _changed(document, "b. Other borrowings", "b. Other liabilities")
    elif change == "extra_column":
        _changed(document, "2332 50 13.b.", "2332 50 51 13.b.")
    elif change == "unit":
        _changed(document, "Dollar Amounts in Thousands", "Amounts in Unknown Units")
    elif change == "entity":
        _changed(document, ENTITY, "Different Holding Company")
    elif change == "date":
        _changed(document, "Date of Report: June 30, 2026", "Date of Report: March 31, 2026")
    elif change == "page_hash":
        document["page_references"][1]["sha256"] = "0" * 64
    elif change == "source_hash":
        document["sha256"] = "0" * 64
    elif change == "raw_id":
        document["id"] = "0" * 64
    elif change == "receipt":
        document["retrieval"]["document_sha256"] = "0" * 64
    elif change == "authority":
        _changed(document, "Board of Governors of the Federal Reserve System", "Unknown Reporting Body")
    else:
        document["extraction_coverage"]["raw_sha256_checked"] = False
    result = _normalize(document)
    assert result["status"] == "incomplete"
    assert result["documents"] == []
    assert result["issues"]


def test_unsupported_form_and_wrong_expected_identity_fail_closed():
    document = _document()
    assert _normalize(document, form="FFIEC 031")["status"] == "unsupported"
    assert _normalize(document, entity="Another Parent")["status"] == "incomplete"
    assert _normalize(document, date="2026-03-31")["status"] == "incomplete"


def test_literal_future_report_date_cannot_precede_observed_source_availability():
    document = _changed(_document(), "Date of Report: June 30, 2026",
                        "Date of Report: September 30, 2026")
    result = _normalize(document, date="2026-09-30")
    assert result["status"] == "incomplete"
    assert result["documents"] == []
    assert any("availability" in issue["reason"] for issue in result["issues"])


@pytest.mark.parametrize("label,valid", [
    ("16. Subordinated notes and debentures1", True),
    ("16. Subordinated notes and debentures (1)", True),
    ("16. Subordinated notes and debentures2", False),
    ("16. Subordinated notes and debentures", False),
    ("16. Other liabilities1", False),
])
def test_large_parent_footnote_variant_keeps_exact_field_and_reported_label(label, valid):
    doc = _changed(_document(), "16. Subordinated notes and debentures (1)", label)
    original = deepcopy(doc)
    result = _normalize(doc)
    assert doc == original
    assert (result["status"] == "ready") is valid
    if valid:
        fact = next(f for f in json.loads(result["documents"][0]["text"])["facts"]
                    if f["concept"] == "BHCP4062")
        assert fact["value"] == 70 and fact["proof"]["label"] == label


def _supplemented_parent():
    rows = ["11. Deposits ........ 2200 0 11.",
            "12. Securities sold under agreements to repurchase ........ 0279 21 12.",
            "17. Other liabilities ........ 2930 33 17.",
            "a. Subsidiary banks ........ 3605 12 18.a.",
            "b. Nonbank subsidiaries ........ 3606 0 18.b.",
            "c. Related holding companies ........ 3607 3 18.c.",
            "21. TOTAL LIABILITIES AND EQUITY CAPITAL (sum of items 11 through 20.f) ........ 3300 2829 21."]
    return _changed(_document(), "Liabilities and Equity Capital",
                    "Liabilities and Equity Capital\n" + "\n".join(rows))


def test_supplemental_parent_liabilities_are_observations_not_a_debt_total():
    result = _normalize(_supplemented_parent())
    assert result["status"] == "ready", result["issues"]
    body = json.loads(result["documents"][0]["text"])
    facts = {f["concept"]: f for f in body["facts"]}
    assert {k: facts[k]["value"] for k in ("BHCP2200", "BHCP0279", "BHCP2930",
            "BHCP3605", "BHCP3606", "BHCP3607", "BHCP3300")} == {
                "BHCP2200": 0, "BHCP0279": 21, "BHCP2930": 33,
                "BHCP3605": 12, "BHCP3606": 0, "BHCP3607": 3, "BHCP3300": 2829}
    assert body["unavailable_fields"] == {}
    assert all(f["scope"] == "parent_only" and f["proof"]["page"] == 3
               for k, f in facts.items() if k not in ("BHCP5993", "BHCP0010"))


def test_absent_or_blank_supplemental_parent_debt_is_not_zero():
    absent = json.loads(_normalize(_document())["documents"][0]["text"])
    assert absent["unavailable_fields"]["BHCP3605"] == "field_not_reported"
    doc = _changed(_supplemented_parent(), "3605 12 18.a.", "3605 18.a.")
    result = _normalize(doc)
    assert result["status"] == "ready", result["issues"]
    body = json.loads(result["documents"][0]["text"])
    assert body["unavailable_fields"]["BHCP3605"] == "blank_amount"
    assert not any(f["concept"] == "BHCP3605" for f in body["facts"])


@pytest.mark.parametrize("replacement", ["3605 N/A 18.a.", "3605 -12 18.a.",
    "3605 12 18.b.", "3605 12 18.a.\na. Subsidiary banks ........ 3605 12 18.a."])
def test_supplemental_parent_debt_keeps_column_code_and_value_guards(replacement):
    result = _normalize(_changed(_supplemented_parent(), "3605 12 18.a.", replacement))
    assert result["status"] == "incomplete" and not result["documents"]


def test_large_parent_actual_pdf_widgets_support_superscript_and_supplemental_rows(tmp_path):
    import re
    from reportlab.pdfgen import canvas
    from bellomberg.valuation.document_evidence import pdf_document

    source = _changed(_supplemented_parent(), "16. Subordinated notes and debentures (1)",
                      "16. Subordinated notes and debentures1")
    path = tmp_path / "synthetic-large-parent.pdf"
    writer = canvas.Canvas(str(path), pagesize=(750, 900))
    for ref in source["page_references"]:
        writer.setFont("Helvetica", 6)
        for index, line in enumerate(source["text"][ref["inizio"]:ref["fine"]].splitlines()):
            y = 860 - index * 24
            match = re.fullmatch(r"(.*? \.{2,} )([0-9]{4}) ([0-9,]+) (\S+)", line)
            if match:
                line = match[1] + match[2] + " " + match[4]
                writer.acroForm.textfield(name="BHCP" + match[2], value=match[3],
                    x=650, y=y - 5, width=65, height=16)
            elif line.startswith("Date of Report:"):
                writer.acroForm.textfield(name="TEXT9999", value="June 30, 2026",
                    x=400, y=y - 5, width=200, height=16)
                line = "Date of Report:"
            elif line == ENTITY:
                writer.acroForm.textfield(name="NM_LGL", value=ENTITY,
                    x=400, y=y - 5, width=200, height=16)
            writer.drawString(20, y, line)
        writer.showPage()
    writer.save()
    document = pdf_document(path, archive_root=tmp_path, cutoff="2026-09-23", retrieval={
        "url": "https://example.test/large-parent.pdf",
        "document_sha256": sha256(path.read_bytes()).hexdigest(),
        "retrieved_at": "2026-09-23T10:00:00Z"})
    result = _normalize(document)
    assert result["status"] == "ready", result["issues"]
    facts = {f["concept"]: f for f in json.loads(result["documents"][0]["text"])["facts"]}
    assert facts["BHCP4062"]["proof"]["label"].endswith("debentures1")
    assert facts["BHCP4062"]["value"] == 70
    assert facts["BHCP3605"]["value"] == 12
    assert facts["BHCP2200"]["value"] == 0
    assert all(f["proof"]["form_field"]["source_document_sha256"] == document["id"]
               for f in facts.values())
