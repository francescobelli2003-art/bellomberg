"""Source availability is not a backdated publication or reporting period."""
from copy import deepcopy
from datetime import date
from hashlib import sha256

import pytest

from test_input_preparation import _bundle, _documents, _operating_plan
from test_lettore_trimestrali import _pdf_minimo


def _retrieved_document():
    row = _documents()[0]
    row["published_at"] = None
    row["document_sha256"] = sha256(b"synthetic original").hexdigest()
    row["availability_basis"] = "observed_download"
    row["retrieval"] = {"url": row["url"], "document_sha256": row["document_sha256"],
                        "retrieved_at": "2026-09-10T08:00:00+00:00"}
    return row


def test_download_availability_preserves_unknown_publication_in_records_and_provenance():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    document = _retrieved_document()
    seen = []
    def propose(dossier, contract):
        seen.extend(dossier["documents"])
        return _operating_plan()
    result = prepare_method_inputs(_bundle(), documents=[document], propose=propose)
    assert result["status"] == "prepared", result["issues"]
    assert seen[0]["published_at"] is None
    assert seen[0]["available_at"] == "2026-09-10"
    source = result["provenance"]["documents"][document["id"]]
    assert source["availability_basis"] == "observed_download"
    assert source["published_at"] is None
    assert source["retrieval"] == document["retrieval"]
    assert {r["as_of"] for r in result["proposal"]["method_records"]} == {"2026-09-10"}


@pytest.mark.parametrize("problem", ["future", "naive", "wrong_hash", "wrong_url", "missing_receipt",
                                     "no_opt_in", "invalid_publication", "future_publication", "claimed_earlier"])
def test_invalid_or_implicit_availability_cannot_spend(problem):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    document = _retrieved_document()
    if problem == "future":
        document["retrieval"]["retrieved_at"] = "2026-09-30T08:00:00Z"
    elif problem == "naive":
        document["retrieval"]["retrieved_at"] = "2026-09-10T08:00:00"
    elif problem == "wrong_hash":
        document["retrieval"]["document_sha256"] = "0" * 64
    elif problem == "wrong_url":
        document["retrieval"]["url"] += "/other"
    elif problem == "missing_receipt":
        del document["retrieval"]
    elif problem == "no_opt_in":
        del document["availability_basis"]
    elif problem == "invalid_publication":
        document["published_at"] = "June 2026"
    elif problem == "future_publication":
        document["published_at"] = "2027-01-01"
    else:
        document["available_at"] = "2026-06-30"
    result = prepare_method_inputs(_bundle(), documents=[document],
        propose=lambda *_: pytest.fail("invalid source must stop before paid proposer"))
    assert result["status"] == "incomplete" and result["proposal"] is None
    assert result["issues"]


def test_availability_day_uses_utc_and_does_not_backdate_timezone():
    from bellomberg.valuation.document_evidence import source_dates
    document = _retrieved_document()
    document["retrieval"]["retrieved_at"] = "2026-09-10T23:30:00-02:00"
    with pytest.raises(ValueError, match="cutoff"):
        source_dates(document, date(2026, 9, 10))
    assert source_dates(document, date(2026, 9, 11))["available_at"] == "2026-09-11"


@pytest.mark.parametrize("problem", ["wrong_url", "wrong_hash", "before_publication"])
def test_published_source_cannot_retain_a_contradictory_receipt(problem):
    from bellomberg.valuation.document_evidence import source_dates
    document = _retrieved_document()
    document.update(published_at="2026-09-09", availability_basis="publication")
    if problem == "wrong_url":
        document["retrieval"]["url"] += "/other"
    elif problem == "wrong_hash":
        document["retrieval"]["document_sha256"] = "0" * 64
    else:
        document["retrieval"]["retrieved_at"] = "2026-09-08T08:00:00Z"
    with pytest.raises(ValueError):
        source_dates(document, date(2026, 9, 10))


def test_later_archive_read_does_not_rewrite_known_publication():
    from bellomberg.valuation.document_evidence import source_dates
    document = _retrieved_document()
    document.update(published_at="2026-09-09", availability_basis="publication")
    document["retrieval"]["retrieved_at"] = "2026-09-30T08:00:00Z"
    assert source_dates(document, date(2026, 9, 10))["available_at"] == "2026-09-09"


@pytest.mark.parametrize("url", ["https://exa mple.test/x", "https://[broken", "https://host.test:bad/x",
                                "https://host.test\n/hidden", "https://user:pass@host.test/file"])
def test_malformed_source_url_is_an_issue_not_a_crash_or_paid_attempt(url):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    document = _documents()[0]
    document["url"] = url
    result = prepare_method_inputs(_bundle(), documents=[document],
        propose=lambda *_: pytest.fail("invalid URL cannot spend"))
    assert result["status"] == "incomplete"
    assert any(issue["code"] == "invalid_url" for issue in result["issues"])


def test_pdf_uses_hashed_bytes_and_real_extraction_with_page_boundaries(tmp_path):
    from bellomberg.valuation.document_evidence import pdf_document
    raw = _pdf_minimo()
    path = tmp_path / "report.pdf"
    path.write_bytes(raw)
    receipt = {"url": "https://example.org/report.pdf", "document_sha256": sha256(raw).hexdigest(),
               "retrieved_at": "2026-09-10T08:00:00Z"}
    source = pdf_document(path, archive_root=tmp_path, retrieval=receipt,
                          cutoff="2026-09-10", metadata={"report_date": "2026-06-30"})
    assert source["published_at"] is None and source["available_at"] == "2026-09-10"
    assert source["document_sha256"] == receipt["document_sha256"] == source["id"]
    assert source["sha256"] == sha256(source["text"].encode()).hexdigest()
    page = source["page_references"][0]
    assert page["pagina"] == 1
    assert page["inizio"] == 0 and page["fine"] == len(source["text"])
    assert page["sha256"] == source["sha256"]
    assert "Risultati semestrali 2026 ok" in source["text"]
    assert source["metadata"]["report_date"] == "2026-06-30"
    assert "archive_path" not in source
    assert source["extraction_coverage"]["pages_without_text"] == []
    original = deepcopy(source)
    path.write_bytes(raw + b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        pdf_document(path, archive_root=tmp_path, retrieval=receipt, cutoff="2026-09-10")
    assert source == original


def test_pdf_does_not_read_outside_archive_or_accept_non_pdf(tmp_path):
    from bellomberg.valuation.document_evidence import pdf_document
    raw = b"not a PDF"
    path = tmp_path / "report.pdf"
    path.write_bytes(raw)
    receipt = {"url": "https://example.org/report.pdf", "document_sha256": sha256(raw).hexdigest(),
               "retrieved_at": "2026-09-10T08:00:00Z"}
    with pytest.raises(ValueError, match="archive"):
        pdf_document(path, archive_root=tmp_path / "other", retrieval=receipt, cutoff="2026-09-10")
    with pytest.raises(ValueError, match="PDF"):
        pdf_document(path, archive_root=tmp_path, retrieval=receipt, cutoff="2026-09-10")


@pytest.mark.parametrize("change", ["offset", "page", "digest", "truncated", "empty"])
def test_page_reference_tampering_stops_before_proposer(change):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    document = _retrieved_document()
    body = document["text"]
    page = {"pagina": 1, "inizio": 0, "fine": len(body), "sha256": sha256(body.encode()).hexdigest()}
    document["page_references"] = [page]
    if change == "offset":
        page["inizio"] = 1
    elif change == "page":
        page["pagina"] = 2
    elif change == "digest":
        page["sha256"] = "0" * 64
    elif change == "truncated":
        page["fine"] -= 1
        page["sha256"] = sha256(body[:-1].encode()).hexdigest()
    else:
        document["page_references"] = []
    result = prepare_method_inputs(_bundle(), documents=[document],
        propose=lambda *_: pytest.fail("bad page source must not spend"))
    assert result["status"] == "incomplete"
    assert any(issue["code"] == "invalid_page_references" for issue in result["issues"])


def test_regulatory_normalization_requires_the_original_pdf_in_the_catalog():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    document = _documents()[0]
    document["id"] = "regulatory-facts-" + "a" * 64
    document["metadata"] = {"normalizer": "regulatory_pdf_v1", "source_document_id": "a" * 64,
                            "form": "FR Y-9LP", "entity": "Synthetic Parent", "report_date": "2025-12-31"}
    result = prepare_method_inputs(_bundle(), documents=[document],
        propose=lambda *_: pytest.fail("derived facts without original PDF cannot spend"))
    assert result["status"] == "incomplete"
    assert any(issue["code"] == "unverified_regulatory_normalization" for issue in result["issues"])


def _regulatory_pair():
    from test_regulatory_evidence import _document, ENTITY, REPORT_DATE
    from bellomberg.valuation.regulatory_evidence import normalize_regulatory_pdf
    original = _document()
    result = normalize_regulatory_pdf(original, expected_form="FR Y-9LP", expected_entity=ENTITY,
                                      expected_report_date=REPORT_DATE)
    assert result["status"] == "ready", result["issues"]
    return original, result["documents"][0]


def test_regulatory_catalog_replays_original_pdf_even_if_normalized_document_comes_first():
    from bellomberg.valuation.input_preparation import _catalog, _fact_proof
    from test_regulatory_evidence import ENTITY, REPORT_DATE
    import json
    original, facts = _regulatory_pair()
    catalog, issues, provenance = _catalog([facts, original], date(2026, 9, 10))
    assert not issues and len(catalog) == 2
    terms = []
    for index, fact in enumerate(json.loads(facts["text"])["facts"]):
        if fact["concept"] not in ("BHCP3210", "BHCP3283"):
            continue
        terms.append({"coefficient": 1 if fact["concept"] == "BHCP3210" else -1,
            "evidence_ids": [facts["id"]], "quoted_value": fact["value"], "quoted_unit": fact["unit"],
            "evidence_pointer": {"value": f"/facts/{index}/value", "unit": f"/facts/{index}/unit", "period": f"/facts/{index}/end"}})
    item = {"value": 1.92, "evidence_ids": [facts["id"]], "calculation": {"operation": "sum", "terms": terms}}
    assert _fact_proof("opening_parent_equity", item, [catalog[facts["id"]]], "USD million", REPORT_DATE,
                       expected_entity=ENTITY) is None
    assert provenance[facts["id"]]["published_at"] is None


@pytest.mark.parametrize("mutation", ["value", "unit", "date", "entity", "row_proof", "source", "origin_changed"])
def test_regulatory_json_rehash_does_not_bypass_original_pdf_proof(mutation):
    from bellomberg.valuation.input_preparation import _catalog
    from test_regulatory_evidence import _changed
    import json
    original, facts = _regulatory_pair()
    payload = json.loads(facts["text"])
    if mutation == "value":
        payload["facts"][0]["value"] += 1
    elif mutation == "unit":
        payload["facts"][0]["unit"] = "USD million"
    elif mutation == "date":
        payload["facts"][0]["end"] = "2025-12-31"
    elif mutation == "entity":
        payload["facts"][0]["entity"] = "Another Legal Entity"
    elif mutation == "row_proof":
        payload["facts"][0]["proof"]["char_start"] += 1
    elif mutation == "source":
        facts["metadata"]["source_document_id"] = facts["id"]
    else:
        _changed(original, "5993 1,200", "5993 1,201")
    facts["text"] = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    facts["sha256"] = sha256(facts["text"].encode()).hexdigest()
    catalog, issues, _ = _catalog([original, facts], date(2026, 9, 10))
    assert facts["id"] not in catalog
    assert any(issue["code"] == "unverified_regulatory_normalization" for issue in issues)
