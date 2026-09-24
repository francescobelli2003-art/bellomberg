"""Structured historical evidence does not silently change its economic meaning."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.input_preparation import prepare_method_inputs
from test_input_preparation import _bundle, _documents, _operating_plan


def _structured_document(facts):
    body = json.dumps({"facts": facts})
    return {"id": "structured-facts", "url": "https://example.org/issuer/structured-facts",
            "published_at": "2026-02-01", "text": body,
            "sha256": sha256(body.encode("utf-8")).hexdigest()}


def _attach_json_fact(item, *, value, unit="EUR", fact_index=0):
    item = deepcopy(item)
    for key in ("evidence_quote", "quoted_value", "quoted_unit", "period_quote"):
        item.pop(key, None)
    item.update(value=value, evidence_ids=["structured-facts"],
                quoted_value=value * 1_000_000 if unit == "EUR" else value,
                quoted_unit=unit,
                evidence_pointer={"value": f"/facts/{fact_index}/value",
                                  "unit": f"/facts/{fact_index}/unit",
                                  "period": f"/facts/{fact_index}/end"})
    return item


def test_historical_revenue_requires_a_canonical_revenue_concept():
    plan = _operating_plan()
    plan["model"]["historical_revenue"] = _attach_json_fact(
        plan["model"]["historical_revenue"], value=100.)
    wrong_metric = {"taxonomy": "us-gaap", "concept": "Assets", "value": 100_000_000,
                    "unit": "EUR", "start": "2025-01-01", "end": "2025-12-31"}
    result = prepare_method_inputs(_bundle(), documents=_documents() + [_structured_document([wrong_metric])],
                                   propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any(issue["code"] == "unverified_fact" and issue["field"] == "historical_revenue"
               for issue in result["issues"])
    assert result["bundle"]["case"]["records"] == []

    revenue = {**wrong_metric, "concept": "RevenueFromContractWithCustomerExcludingAssessedTax"}
    result = prepare_method_inputs(_bundle(), documents=_documents() + [_structured_document([revenue])],
                                   propose=lambda *_: plan)
    assert result["status"] == "prepared", result["issues"]


def test_historical_xbrl_observation_cannot_be_called_company_guidance():
    plan = _operating_plan()
    plan["scenarios"]["base"]["wacc"] = _attach_json_fact(
        plan["scenarios"]["base"]["wacc"], value=.1, unit="ratio")
    plan["scenarios"]["base"]["wacc"]["kind"] = "company_guidance"
    observed = {"taxonomy": "us-gaap", "concept": "ObservedHistoricalRatio",
                "value": .1, "unit": "ratio", "end": "2025-12-31"}
    result = prepare_method_inputs(_bundle(), documents=_documents() + [_structured_document([observed])],
                                   propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any(issue["code"] == "unverified_fact" and issue["field"] == "wacc"
               for issue in result["issues"])
    assert result["bundle"]["case"]["records"] == []


def test_literal_company_guidance_remains_distinct_from_structured_historical_facts():
    plan = _operating_plan()
    guidance = "Management guidance: ratio 0.1 for planning"
    source = _structured_document([])
    source.update(id="guidance", text=guidance,
                  sha256=sha256(guidance.encode("utf-8")).hexdigest())
    plan["scenarios"]["base"]["wacc"].update(
        kind="company_guidance", evidence_ids=["guidance"],
        evidence_quote=guidance, quoted_value=.1, quoted_unit="ratio")
    result = prepare_method_inputs(_bundle(), documents=_documents() + [source],
                                   propose=lambda *_: plan)
    assert result["status"] == "prepared", result["issues"]


def test_literal_opening_measure_cannot_borrow_a_different_period():
    from bellomberg.valuation.input_preparation import _fact_proof
    source = {"text": ("2025-12-31 Diluted shares: 10 million shares. "
                       "2026-12-31 Diluted shares: 20 million shares.")}
    item = {"value": 20., "evidence_quote": "Diluted shares: 20 million shares",
            "quoted_value": 20., "quoted_unit": "million shares",
            "period_quote": "2025-12-31"}
    assert _fact_proof("shares", item, [source], "million shares", "2025-12-31") is not None
    item["period_quote"] = "2025-12-31 Diluted shares: 10 million shares. 2026-12-31 Diluted shares: 20 million shares"
    assert _fact_proof("shares", item, [source], "million shares", "2025-12-31") is not None
    item["period_quote"] = "2026-12-31 Diluted shares: 20 million shares"
    assert _fact_proof("shares", item, [source], "million shares", "2026-12-31") is None
    del item["period_quote"]
    assert _fact_proof("shares", item, [source], "million shares", "2026-12-31") is not None


def test_total_assets_are_not_an_operating_working_capital_observation():
    plan = _operating_plan()
    plan["model"]["opening_nwc"] = _attach_json_fact(plan["model"]["opening_nwc"], value=100.)
    source = _structured_document([{"taxonomy": "us-gaap", "concept": "Assets",
        "value": 100_000_000, "unit": "EUR", "end": "2025-12-31"}])
    result = prepare_method_inputs(_bundle(), documents=_documents() + [source], propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any(row["field"] == "opening_nwc" and row["code"] == "unverified_fact" for row in result["issues"])
    from bellomberg.valuation.input_preparation import _fact_proof
    assert _fact_proof("opening_nwc", {"value": 100, "evidence_quote": "Assets: EUR 100 million",
        "quoted_value": 100, "quoted_unit": "EUR million"},
        [{"text": "Assets: EUR 100 million"}], "EUR million", "2025-12-31") is not None


def test_working_capital_components_have_fixed_economic_signs():
    from bellomberg.valuation.input_preparation import _fact_proof
    source = _structured_document([
        {"taxonomy": "us-gaap", "concept": "AccountsReceivableNetCurrent", "value": 100_000_000,
         "unit": "EUR", "end": "2025-12-31"},
        {"taxonomy": "us-gaap", "concept": "AccountsPayableCurrent", "value": 20_000_000,
         "unit": "EUR", "end": "2025-12-31"}])
    terms = []
    for index, value in enumerate((100, 20)):
        fact = _attach_json_fact({}, value=value, fact_index=index)
        fact.pop("value")
        terms.append({"coefficient": 1 if index == 0 else -1, **fact})
    item = {"value": 80., "calculation": {"operation": "sum", "terms": terms}}
    assert _fact_proof("opening_nwc", item, [source], "EUR million", "2025-12-31") is None
    terms[1]["coefficient"] = 1
    item["value"] = 120.
    assert _fact_proof("opening_nwc", item, [source], "EUR million", "2025-12-31") is not None


def test_entity_tagged_observation_cannot_supply_another_legal_entity():
    from bellomberg.valuation.input_preparation import _fact_proof
    source = _structured_document([{"concept": "reported_equity", "value": 12000.,
        "unit": "USD thousand", "end": "2025-12-31", "entity": "Synthetic Parent"}])
    source["metadata"] = {"entity": "Synthetic Parent"}
    item = _attach_json_fact({}, value=12000., unit="USD thousand")
    item["value"] = 12.
    assert _fact_proof("opening_common_equity", item, [source], "USD million", "2025-12-31",
                       expected_entity="Synthetic Parent") is None
    assert "entita" in _fact_proof("opening_common_equity", item, [source], "USD million", "2025-12-31",
                                    expected_entity="Synthetic Bank")
    source["metadata"]["entity"] = "Synthetic Bank"
    assert "entita" in _fact_proof("opening_common_equity", item, [source], "USD million", "2025-12-31",
                                    expected_entity="Synthetic Parent")


@pytest.mark.parametrize("driver", ["opening_parent_equity", "capital.parent_opening_cash"])
@pytest.mark.parametrize("tagged", [False, True])
def test_parent_regulatory_driver_rejects_generic_structured_assets(driver, tagged):
    from bellomberg.valuation.input_preparation import _fact_proof
    source = _structured_document([{"taxonomy": "us-gaap", "concept": "Assets", "value": 12000.,
                                    "unit": "USD thousand", "end": "2025-12-31",
                                    **({"entity": "Synthetic Parent"} if tagged else {})}])
    if tagged:
        source["metadata"] = {"entity": "Synthetic Parent"}
    item = _attach_json_fact({}, value=12000., unit="USD thousand")
    item["value"] = 12.
    error = _fact_proof(driver, item, [source], "USD million", "2025-12-31",
                        expected_entity="Synthetic Parent")
    assert error and "regolamentare" in error


@pytest.mark.parametrize("driver", ["opening_parent_equity", "capital.parent_opening_cash"])
def test_parent_regulatory_driver_rejects_literal_assets_even_with_matching_value_and_date(driver):
    from bellomberg.valuation.input_preparation import _fact_proof
    line = "Assets: USD 12 million as of 2025-12-31."
    item = {"value": 12., "evidence_quote": "Assets: USD 12 million",
            "quoted_value": 12., "quoted_unit": "USD million", "period_quote": line}
    error = _fact_proof(driver, item, [{"text": line}], "USD million", "2025-12-31",
                        expected_entity="Synthetic Parent")
    assert error and "regolamentare" in error


def test_compiler_passes_expected_entity_to_structured_opening_proofs():
    plan = _operating_plan()
    plan["model"]["shares"] = _attach_json_fact(plan["model"]["shares"], value=10., unit="million shares")
    source = _structured_document([{"concept": "shares", "value": 10., "unit": "million shares",
                                   "end": "2025-12-31", "entity": "ANOTHER-ISSUER"}])
    result = prepare_method_inputs(_bundle(), documents=_documents() + [source], propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any(issue["field"] == "shares" and issue["code"] == "unverified_fact" for issue in result["issues"])
    source = _structured_document([{"concept": "shares", "value": 10., "unit": "million shares",
                                   "end": "2025-12-31", "entity": "SYNTH-GROUP"}])
    result = prepare_method_inputs(_bundle(), documents=_documents() + [source], propose=lambda *_: plan)
    assert result["status"] == "prepared", result["issues"]


def _parent_regulatory_case(driver):
    from bellomberg.valuation.input_evidence import parent_regulatory_components
    components = parent_regulatory_components(driver)
    facts, terms = [], []
    result = 0.
    for index, (code, sign) in enumerate(components.items()):
        observed = 100000. if index == 0 else 20000.
        facts.append({"taxonomy": "fr-y-9lp-pc", "concept": code, "value": observed,
            "unit": "USD thousand", "end": "2025-12-31", "entity": "Synthetic Parent", "scope": "parent_only"})
        terms.append({"coefficient": sign, "evidence_ids": ["structured-facts"],
            "evidence_pointer": {"value": f"/facts/{index}/value", "unit": f"/facts/{index}/unit", "period": f"/facts/{index}/end"},
            "quoted_value": observed, "quoted_unit": "USD thousand"})
        result += observed * sign / 1000
    document = _structured_document(facts)
    document["metadata"] = {"normalizer": "regulatory_pdf_v1", "entity": "Synthetic Parent", "scope": "parent_only"}
    return {"value": result, "evidence_ids": [document["id"]],
            "calculation": {"operation": "sum", "terms": terms}}, document


@pytest.mark.parametrize("driver", ["opening_parent_equity", "capital.parent_opening_cash"])
def test_regulatory_common_equity_and_cash_require_the_exact_observed_bridge(driver):
    from bellomberg.valuation.input_preparation import _fact_proof
    item, document = _parent_regulatory_case(driver)
    assert _fact_proof(driver, item, [document], "USD million", "2025-12-31",
                       expected_entity="Synthetic Parent") is None
    # TOTAL equity is not common equity; one side of cash is not total cash.
    term = item["calculation"]["terms"][0]
    direct = {key: value for key, value in term.items() if key != "coefficient"}
    direct["value"] = term["quoted_value"] / 1000
    assert _fact_proof(driver, direct, [document], "USD million", "2025-12-31",
                       expected_entity="Synthetic Parent")


@pytest.mark.parametrize("mutation", ["wrong_sign", "other_concept", "duplicate_concept", "mixed_source",
                                      "consolidated_scope", "wrong_entity", "wrong_period"])
def test_parent_regulatory_bridge_does_not_relax_accounting_scope(mutation):
    from bellomberg.valuation.input_preparation import _fact_proof
    item, document = _parent_regulatory_case("opening_parent_equity")
    facts = json.loads(document["text"])["facts"]
    if mutation == "wrong_sign":
        item["calculation"]["terms"][1]["coefficient"] = 1
        item["value"] = 120.
    elif mutation == "other_concept":
        facts[1]["concept"] = "BHCP0368"  # borrowings cannot substitute preferred capital
    elif mutation == "duplicate_concept":
        facts[1]["concept"] = facts[0]["concept"]
    elif mutation == "mixed_source":
        facts[1]["taxonomy"] = "unrelated-taxonomy"
        document["metadata"].pop("normalizer")
    elif mutation == "consolidated_scope":
        facts[1]["scope"] = "consolidated"
    elif mutation == "wrong_entity":
        facts[1]["entity"] = "Synthetic Subsidiary"
    else:
        facts[1]["end"] = "2024-12-31"
    document["text"] = json.dumps({"facts": facts})
    assert _fact_proof("opening_parent_equity", item, [document], "USD million", "2025-12-31",
                       expected_entity="Synthetic Parent")


def test_reported_borrowing_lines_do_not_certify_the_total_parent_debt_perimeter():
    from bellomberg.valuation.input_preparation import _fact_proof
    item, document = _parent_regulatory_case("capital.parent_opening_cash")
    assert _fact_proof("capital.parent_opening_debt", item, [document], "USD million", "2025-12-31",
                       expected_entity="Synthetic Parent")


def _ttm_case():
    """Two synthetic SEC-shaped accession documents; no live issuer data."""
    cik = "0000000001"
    concept = "RevenueFromContractWithCustomerExcludingAssessedTax"

    def document(accession, filed, form, observations):
        facts = [{"taxonomy": "us-gaap", "concept": concept, "unit": "EUR",
                  "observation": {"val": value, "start": start, "end": end,
                                  "accn": accession[:10] + "-" + accession[10:12] + "-" + accession[12:],
                                  "filed": filed, "form": form}}
                 for value, start, end in observations]
        body = json.dumps({"cik": cik, "issuer": "SYNTH-GROUP", "facts": facts}, separators=(",", ":"))
        return {"id": "xbrl-" + cik + "-" + accession,
                "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK" + cik + ".json",
                "published_at": filed, "text": body, "sha256": sha256(body.encode()).hexdigest(),
                "metadata": {"emittente_id": "CIK:" + cik, "accession": accession},
                "extraction_coverage": {"status": "selected_filing_facts"}}

    annual = document("000000000126000001", "2026-02-15", "10-K",
                      [(100_000_000, "2025-01-01", "2025-12-31")])
    interim = document("000000000126000002", "2026-08-15", "10-Q",
                       [(60_000_000, "2026-01-01", "2026-06-30"),
                        (45_000_000, "2025-01-01", "2025-06-30")])

    def term(doc, index, value):
        return {"evidence_ids": [doc["id"]],
                "evidence_pointer": {"value": f"/facts/{index}/observation/val",
                                     "unit": f"/facts/{index}/unit",
                                     "period": f"/facts/{index}/observation/end"},
                "quoted_value": value, "quoted_unit": "EUR"}

    item = {"value": 115., "evidence_ids": [annual["id"], interim["id"]],
            "calculation": {"operation": "trailing_twelve_months", "terms": {
                "annual": term(annual, 0, 100_000_000),
                "current_ytd": term(interim, 0, 60_000_000),
                "prior_ytd": term(interim, 1, 45_000_000)}}}
    return item, [annual, interim]


def test_historical_revenue_ttm_reconciles_three_traceable_observations():
    from bellomberg.valuation.input_preparation import _fact_proof

    item, documents = _ttm_case()
    assert _fact_proof("historical_revenue", item, documents, "EUR million", "2026-06-30") is None
    item["value"] = 116.
    assert _fact_proof("historical_revenue", item, documents, "EUR million", "2026-06-30") is not None


@pytest.mark.parametrize("change", ["current_start", "prior_start", "prior_end", "current_end",
                                     "trailing_horizon", "short_annual", "long_ytd"])
def test_ttm_rejects_invalid_period_reconciliation(change):
    from bellomberg.valuation.input_preparation import _fact_proof

    item, documents = _ttm_case()
    annual = json.loads(documents[0]["text"])
    interim = json.loads(documents[1]["text"])
    if change == "current_start":
        interim["facts"][0]["observation"]["start"] = "2026-01-02"
    elif change == "prior_start":
        interim["facts"][1]["observation"]["start"] = "2025-01-02"
    elif change == "prior_end":
        interim["facts"][1]["observation"]["end"] = "2025-12-31"
    elif change == "current_end":
        interim["facts"][0]["observation"]["end"] = "2026-06-29"
    elif change == "trailing_horizon":
        interim["facts"][1]["observation"]["end"] = "2025-05-31"
    elif change == "short_annual":
        annual["facts"][0]["observation"]["start"] = "2025-04-01"
        interim["facts"][1]["observation"]["start"] = "2025-04-01"
    else:
        interim["facts"][0]["observation"]["end"] = "2026-12-31"
    documents[0]["text"] = json.dumps(annual)
    documents[1]["text"] = json.dumps(interim)
    opening = "2026-12-31" if change == "long_ytd" else "2026-06-30"
    assert _fact_proof("historical_revenue", item, documents, "EUR million", opening) is not None


@pytest.mark.parametrize("change", ["issuer", "metadata_issuer", "source_url", "identity",
                                     "concept", "currency", "pointer", "quoted_value",
                                     "missing_proof", "coefficient", "surplus_citation"])
def test_ttm_rejects_mixed_or_unproved_operands(change):
    from bellomberg.valuation.input_preparation import _fact_proof

    item, documents = _ttm_case()
    interim = json.loads(documents[1]["text"])
    terms = item["calculation"]["terms"]
    if change == "issuer":
        interim["cik"] = "0000000002"
    elif change == "metadata_issuer":
        documents[1]["metadata"]["emittente_id"] = "CIK:0000000002"
    elif change == "source_url":
        documents[1]["url"] = "https://example.org/not-companyfacts.json"
    elif change == "identity":
        interim["facts"][0]["observation"]["accn"] = "0000000001-26-999999"
    elif change == "concept":
        interim["facts"][1]["concept"] = "Revenues"
    elif change == "currency":
        interim["facts"][1]["unit"] = "USD"
        terms["prior_ytd"]["quoted_unit"] = "USD"
    elif change == "pointer":
        terms["prior_ytd"]["evidence_pointer"]["unit"] = "/facts/0/unit"
    elif change == "quoted_value":
        terms["annual"]["quoted_value"] = 99_000_000
    elif change == "missing_proof":
        del terms["current_ytd"]["evidence_pointer"]
    elif change == "surplus_citation":
        item["evidence_ids"].append("unrelated")
    else:
        terms["prior_ytd"]["coefficient"] = -1
    documents[1]["text"] = json.dumps(interim)
    assert _fact_proof("historical_revenue", item, documents, "EUR million", "2026-06-30") is not None


def test_ttm_is_unavailable_to_nonrevenue_and_existing_sum_remains_valid():
    from bellomberg.valuation.input_preparation import _fact_proof

    item, documents = _ttm_case()
    assert _fact_proof("opening_nwc", item, documents, "EUR million", "2026-06-30") is not None
    item["calculation"] = {"operation": "sum", "terms": [
        {"coefficient": 1, **item["calculation"]["terms"]["current_ytd"]},
        {"coefficient": -1, **item["calculation"]["terms"]["prior_ytd"]}]}
    item["value"] = 15.
    assert _fact_proof("historical_revenue", item, documents, "EUR million", "2026-06-30") is not None
    # The NWC sum contract still accepts same-period signed operating components
    # in test_working_capital_components_have_fixed_economic_signs above.
