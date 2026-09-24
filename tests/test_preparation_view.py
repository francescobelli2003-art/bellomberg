"""Prompt projection tests use synthetic documents only; no provider or DB access."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.preparation_view import select_stage_view


def _document(ident, text, metadata):
    return {"id": ident, "url": "https://example.test/" + ident,
            "published_at": "2025-01-10", "text": text,
            "sha256": sha256(text.encode("utf-8")).hexdigest(),
            "document_sha256": "a" * 64, "metadata": metadata,
            "extraction_coverage": {"status": "synthetic"}}


def _dossier():
    ticker = "SYNTH"
    xbrl = json.dumps({"cik": 123, "issuer": "Synthetic Issuer",
                       "facts": [{"taxonomy": "us-gaap", "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                                  "unit": "USD", "observation": {"val": 42, "end": "2024-12-31",
                                                                  "filed": "2025-01-10", "accn": "000000012325000001"}}]})
    price = json.dumps({"symbol": ticker,
                        "observation": {"symbol": ticker, "date": "2025-01-10",
                                        "close": 7, "currency": "USD"},
                        "facts": [{"value": 7, "unit": "USD per share", "end": "2025-01-10"}]})
    listing = json.dumps({"listing": {"symbol": ticker, "title": "Ordinary shares",
                                      "shares_per_quote": 1, "status": "ok"},
                          "facts": [{"value": 1, "unit": "shares per quote", "end": "2025-01-10"}]})
    unknown_json = json.dumps({"other_evidence": {"value": 3}})
    return {"ticker": ticker, "as_of": "2025-01-10", "method_id": "operating_fcff",
            "decision": {"method_id": "operating_fcff"},
            "document_acquisition": {"status": "ready"}, "acquisition_tasks": [],
            "acquired_sources": {"profile": {"status": "ok", "data": {"sector": "software"}},
                                 "financials": {"status": "ok", "data": "financial data " * 100},
                                 "filings": {"status": "ok", "data": "filing data " * 100},
                                 "guidance": {"status": "data_missing"},
                                 "consensus": {"status": "ok", "records": []}},
            "documents": [
                _document("filing", "Unstructured filing narrative. " * 1000,
                          {"form": "10-K", "report_date": "2024-12-31"}),
                _document("xbrl-0000000123-000000012325000001", xbrl,
                          {"emittente_id": "CIK:0000000123", "accession": "000000012325000001"}),
                _document("quote", price, {"ticker": ticker, "price_as_of": "2025-01-10"}),
                _document("listing", listing, {"ticker": ticker, "share_class": "ordinary"}),
                _document("other", unknown_json, {"type": "unclassified"}),
            ]}


def test_model_projection_preserves_structured_text_and_original_identity():
    dossier = _dossier()
    original = deepcopy(dossier)
    view = select_stage_view(dossier, "model")

    assert dossier == original
    assert view is not dossier and view["documents"] is not dossier["documents"]
    assert "text" not in view["documents"][0]
    for index in (1, 2, 3, 4):
        assert view["documents"][index] == original["documents"][index]
    assert view["documents"][0]["metadata"] == original["documents"][0]["metadata"]
    assert view["documents"][0]["id"] == original["documents"][0]["id"]
    assert view["documents"][0]["sha256"] == original["documents"][0]["sha256"]
    # JSON-pointer positions have not been rebuilt or renumbered.
    xbrl = json.loads(view["documents"][1]["text"])
    assert xbrl["facts"][0]["observation"]["val"] == 42
    assert json.loads(view["documents"][2]["text"])["facts"][0]["value"] == 7
    assert json.loads(view["documents"][3]["text"])["facts"][0]["value"] == 1
    assert set(view["acquired_sources"]) == {"profile", "guidance", "consensus"}
    assert view["acquired_sources"]["profile"] == original["acquired_sources"]["profile"]
    report = view["stage_view"]
    assert report["stage"] == "model" and report["structured_triplet_present"] is True
    assert report["documents"][0]["view"] == "metadata_only"
    assert report["documents"][0]["original_text_sha256"] == original["documents"][0]["sha256"]
    assert report["documents"][1]["view"] == "full_text"
    assert {row["name"] for row in report["acquired_sources"] if row["view"] == "excluded"} == {"financials", "filings"}
    assert len(json.dumps(view)) < len(json.dumps(original)) / 2
    view["documents"][1]["metadata"]["accession"] = "changed"
    assert dossier == original


def test_missing_or_ambiguous_structured_sources_keep_all_text_and_sources():
    for change in ("missing_listing", "other_ticker", "duplicate_price"):
        dossier = _dossier()
        if change == "missing_listing":
            del dossier["documents"][3]
        elif change == "other_ticker":
            dossier["documents"][2]["metadata"]["ticker"] = "OTHER"
        else:
            dossier["documents"].append(deepcopy(dossier["documents"][2]))
        view = select_stage_view(dossier, "model")
        assert view["documents"] == dossier["documents"]
        assert view["acquired_sources"] == dossier["acquired_sources"]
        assert view["stage_view"]["structured_triplet_present"] is False
        assert all(row["view"] == "full_text" for row in view["stage_view"]["documents"])


def test_forecast_passes_through_as_deep_copy_without_projection_report():
    dossier = _dossier()
    for stage in ("forecast", "bear", "base", "bull"):
        view = select_stage_view(dossier, stage)
        assert view == dossier and view is not dossier
        view["documents"][0]["text"] = "edited"
        assert dossier["documents"][0]["text"].startswith("Unstructured")
    with pytest.raises(ValueError, match="stage"):
        select_stage_view(dossier, "unknown")


@pytest.mark.parametrize("names", [["capdev_amortization_years"],
                                   ["historical_revenue", "capdev_amortization_years"],
                                   ["opening_nwc"], ["historical_revenue", "opening_nwc"]])
@pytest.mark.parametrize("selection", ["full", "forecast_excerpts", "opening_excerpts"])
def test_model_scope_economic_policy_receives_narrative(names, selection):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.operating_adapter import SCHEMA
    dossier = _dossier()
    original = deepcopy(dossier)
    document = dossier["documents"][0]
    selected = document["text"][:30]
    manifest = [{"source_id": document["id"], "original_text_sha256_utf8": document["sha256"],
                 "excerpts": [{"theme": "accounting_policy", "char_start": 0, "char_end_exclusive": len(selected),
                               "excerpt_sha256_utf8": sha256(selected.encode()).hexdigest()}]}]
    options = {}
    if selection == "forecast_excerpts": options["forecast_excerpt_manifest"] = manifest
    if selection == "opening_excerpts": options["opening_excerpt_manifest"] = manifest
    seen = []
    class Observed(Exception):
        pass
    def observe(view, contract):
        seen.append(view)
        assert contract["preparation_stage"]["scope"] == "model"
        assert "text" in view["documents"][0], "numeric opening projection must not hide accounting policy or working-capital classification"
        if selection == "full":
            assert view["documents"][0] == document
        else:
            assert selected in view["documents"][0]["text"]
            assert view["stage_view"]["documents"][0]["view"] == "verified_excerpts"
        assert view["documents"][1:] == original["documents"][1:]
        raise Observed()
    contract = {"schema": {name: SCHEMA[name] for name in names}, "scenarios": ["bear", "base", "bull"]}
    with pytest.raises(Observed):
        StagedProposer(observe, **options)(dossier, contract)
    assert len(seen) == 1 and dossier == original


def test_declared_text_hash_mismatch_is_rejected_before_projection():
    dossier = _dossier()
    dossier["documents"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        select_stage_view(dossier, "model")


def test_bank_opening_keeps_legal_entity_narratives_even_with_consolidated_xbrl():
    dossier = _dossier()
    dossier["method_id"] = "bank_residual_income"
    dossier["decision"]["method_id"] = "bank_residual_income"
    view = select_stage_view(dossier, "model")
    assert view["documents"] == dossier["documents"]
    assert view["acquired_sources"] == dossier["acquired_sources"]
    assert "legal-entity" in view["stage_view"]["limitation"]


def _bank_excerpt_case():
    dossier = _dossier()
    dossier["method_id"] = dossier["decision"]["method_id"] = "bank_residual_income"
    first = "Parént balance at period end: cash 11.\nÜnrelated lengthy narrative.\nParent equity: 23.\n"
    second = "Bank subsidiary deposits: 31.\nOther unrelated discussion.\n"
    dossier["documents"][0] = _document("parent-report", first, {"form": "Y9LP", "report_date": "2024-12-31"})
    dossier["documents"][0]["page_references"] = [{"page": 3, "char_start": 0, "char_end_exclusive": len(first)}]
    dossier["documents"].append(_document("bank-report", second, {"form": "Call", "report_date": "2024-12-31"}))

    def entry(document, selections):
        body = document["text"]
        excerpts = []
        for theme, selected in selections:
            start = body.index(selected)
            end = start + len(selected)
            excerpts.append({"theme": theme, "char_start": start, "char_end_exclusive": end,
                             "excerpt_sha256_utf8": sha256(selected.encode("utf-8")).hexdigest(),
                             "excerpt_chars": len(selected), "excerpt_utf8_bytes": len(selected.encode("utf-8")),
                             "line_start": body.count("\n", 0, start) + 1,
                             "line_end": body.count("\n", 0, end - 1) + 1})
        return {"source_id": document["id"], "original_text_sha256_utf8": document["sha256"],
                "original_document_sha256_bytes": document["document_sha256"],
                "url": document["url"], "published_at": document["published_at"],
                "form": document["metadata"]["form"], "report_date": document["metadata"]["report_date"],
                "original_text_utf8_bytes": len(body.encode("utf-8")), "excerpts": excerpts}

    manifest = [entry(dossier["documents"][0], [("parent_cash", "Parént balance at period end: cash 11.\n"),
                                                   ("parent_equity", "Parent equity: 23.\n")]),
                entry(dossier["documents"][-1], [("bank_deposits", "Bank subsidiary deposits: 31.\n")])]
    return dossier, manifest


def test_bank_manifest_projects_only_verified_spans_and_preserves_json_and_original():
    dossier, manifest = _bank_excerpt_case()
    original = deepcopy(dossier)
    view = select_stage_view(dossier, "model", excerpt_manifest=manifest)

    assert dossier == original
    parent = view["documents"][0]
    assert "Parént balance at period end: cash 11." in parent["text"]
    assert "Parent equity: 23." in parent["text"]
    assert "Ünrelated lengthy narrative." not in parent["text"]
    assert "Other unrelated discussion." not in view["documents"][-1]["text"]
    assert parent["sha256"] == original["documents"][0]["sha256"]
    assert parent["page_references"] == original["documents"][0]["page_references"]
    assert view["documents"][1:5] == original["documents"][1:5]
    assert view["acquired_sources"] == original["acquired_sources"]
    report = view["stage_view"]
    assert report["documents"][0]["view"] == "verified_excerpts"
    assert report["documents"][0]["excerpts"][0]["char_start"] == 0
    assert report["documents"][0]["projected_text_sha256"] == sha256(parent["text"].encode("utf-8")).hexdigest()
    assert report["documents"][0]["selected_source_utf8_bytes"] > report["documents"][0]["selected_source_chars"]
    assert report["excerpt_projection"]["narrative_documents"] == 2
    assert report["excerpt_projection"]["semantic_coverage_certified"] is False


@pytest.mark.parametrize("mutation", ["changed_document", "changed_span_hash", "shifted_offset",
                                      "overlap", "omitted_document", "unknown_document", "json_document",
                                      "changed_original_bytes", "wrong_line", "changed_url"])
def test_bank_manifest_rejects_stale_incomplete_or_unverifiable_excerpts(mutation):
    dossier, manifest = _bank_excerpt_case()
    if mutation == "changed_document":
        dossier["documents"][0]["text"] += "Changed."
        dossier["documents"][0]["sha256"] = sha256(dossier["documents"][0]["text"].encode("utf-8")).hexdigest()
    elif mutation == "changed_span_hash":
        manifest[0]["excerpts"][0]["excerpt_sha256_utf8"] = "0" * 64
    elif mutation == "shifted_offset":
        manifest[0]["excerpts"][0]["char_start"] += 1
    elif mutation == "overlap":
        manifest[0]["excerpts"][1]["char_start"] = 0
    elif mutation == "omitted_document":
        manifest.pop()
    elif mutation == "unknown_document":
        manifest[0]["source_id"] = "absent"
    elif mutation == "json_document":
        manifest.append({"source_id": dossier["documents"][1]["id"], "excerpts": []})
    elif mutation == "changed_original_bytes":
        manifest[0]["original_document_sha256_bytes"] = "0" * 64
    elif mutation == "changed_url":
        manifest[0]["url"] = "https://example.test/another"
    else:
        manifest[0]["excerpts"][0]["line_end"] = 99
    with pytest.raises(ValueError):
        select_stage_view(dossier, "model", excerpt_manifest=manifest)


def test_manifest_is_opt_in_for_forecasts_and_unsupported_methods_are_rejected():
    dossier, manifest = _bank_excerpt_case()
    assert select_stage_view(dossier, "model")["documents"] == dossier["documents"]
    assert select_stage_view(dossier, "forecast") == dossier
    for method in ("operating_fcff", "bank_residual_income"):
        dossier["method_id"] = method
        for stage in ("forecast", "bear", "base", "bull"):
            view = select_stage_view(dossier, stage, excerpt_manifest=manifest)
            assert view["stage_view"]["stage"] == stage
            assert view["documents"][1:5] == dossier["documents"][1:5]
            assert view["acquired_sources"] == dossier["acquired_sources"]
            assert view["documents"][0]["text"] != dossier["documents"][0]["text"]
    dossier["method_id"] = "unsupported_method"
    with pytest.raises(ValueError, match="manifest"):
        select_stage_view(dossier, "model", excerpt_manifest=manifest)


def _layout_case():
    dossier, manifest = _bank_excerpt_case()
    body = "Policy: R&D expensed.\n\n \t\n\u00a0\n\nInventory\t(12)\n  Advances\t(3)\n\n\nEnd."
    document = dossier["documents"][0]
    document["text"] = body
    document["sha256"] = sha256(body.encode()).hexdigest()
    manifest[0] = {"source_id": document["id"], "original_text_sha256_utf8": document["sha256"],
                   "layout_projection": "collapse_blank_lines_v1",
                   "selection_rationale": "Complete synthetic note for accounting policy.",
                   "coverage_limitations": "This note alone does not establish model coverage.",
                   "excerpts": [{"theme": "accounting", "char_start": 0,
                                 "char_end_exclusive": len(body),
                                 "excerpt_sha256_utf8": document["sha256"]}]}
    return dossier, manifest


def test_blank_line_projection_preserves_content_lines_source_identity_and_structured_facts():
    dossier, manifest = _layout_case()
    original = deepcopy(dossier)
    view = select_stage_view(dossier, "forecast", excerpt_manifest=manifest)
    text = view["documents"][0]["text"]
    assert "Policy: R&D expensed.\n\nInventory\t(12)\n  Advances\t(3)\n\nEnd." in text
    assert dossier == original
    assert view["documents"][0]["sha256"] == original["documents"][0]["sha256"]
    assert view["documents"][1:5] == original["documents"][1:5]
    row = view["stage_view"]["documents"][0]
    assert row["layout_projection"] == "collapse_blank_lines_v1"
    assert row["selection_rationale"] == manifest[0]["selection_rationale"]
    assert row["coverage_limitations"] == manifest[0]["coverage_limitations"]
    assert row["removed_layout_chars"] == 7
    assert row["removed_layout_utf8_bytes"] == 8
    assert row["selected_source_chars"] == len(original["documents"][0]["text"])
    assert row["excluded_source_chars"] == 0
    assert view["stage_view"]["excerpt_projection"]["removed_layout_utf8_bytes"] == 8
    assert view["stage_view"]["excerpt_projection"]["semantic_coverage_certified"] is False


@pytest.mark.parametrize("mode", ["trim_all", "", False, {"policy": "collapse_blank_lines_v1"}])
def test_unknown_layout_projection_is_rejected(mode):
    dossier, manifest = _layout_case()
    manifest[0]["layout_projection"] = mode
    with pytest.raises(ValueError, match="layout projection"):
        select_stage_view(dossier, "forecast", excerpt_manifest=manifest)


def test_layout_quotes_must_exist_in_both_original_and_visible_fragment():
    from bellomberg.valuation.preparation_ai import verify_visible_citations
    dossier, manifest = _layout_case()
    view = select_stage_view(dossier, "forecast", excerpt_manifest=manifest)
    evidence = {"evidence_ids": [dossier["documents"][0]["id"]]}
    for quote in ("Policy: R&D expensed.", "Inventory\t(12)\n  Advances\t(3)"):
        verify_visible_citations({**evidence, "evidence_quote": quote}, view, dossier)
    for quote in ("Policy: R&D expensed.\n\n \t\n\u00a0\n\nInventory",
                  "Policy: R&D expensed.\n\nInventory", "Advances\t(3)\n\nEnd."):
        with pytest.raises(ValueError, match="non visibile"):
            verify_visible_citations({**evidence, "evidence_quote": quote}, view, dossier)
    view["documents"][0]["text"] = "Policy: R&D expensed."
    with pytest.raises(ValueError, match="non visibile"):
        verify_visible_citations({**evidence, "evidence_quote": "Inventory\t(12)"}, view, dossier)
