"""Offline contract tests for documented automatic input preparation."""
from copy import deepcopy
from hashlib import sha256
import re
import json

from bellomberg.valuation.dcf_engine import generate_valuation
from bellomberg.valuation.sector_analysis import prepare_sector_analysis
from test_sector_analysis import providers_for, DAY

from bellomberg.valuation.input_preparation import prepare_method_inputs


def _documents(*, published_at="2026-09-09", digest=True):
    text = ("Synthetic issuer annual statement. Revenue: EUR 100 million for FY ended 2025-12-31. "
            "Net working capital: EUR 0 million as of 2025-12-31. "
            "Diluted shares: 10 million shares as of 2025-12-31. "
            "Price: EUR 10 per share as of 2025-12-31. Ratio: 1 shares per quote. "
            "Parent cash: EUR 5 million as of 2025-12-31. "
            "Operating business sells ten units at EUR ten million each.")
    row = {"id": "annual-1", "url": "https://example.org/issuer/annual-report",
           "published_at": published_at, "text": text}
    if digest:
        row["sha256"] = sha256(text.encode("utf-8")).hexdigest()
    return [row]


def _bundle(*, records=None, assumptions=None, business="software"):
    providers = providers_for(business, records=records)
    return prepare_sector_analysis("SYNTH-EXT", as_of=DAY, providers=providers,
                                   user_context={"assumptions": assumptions or {}})


def _operating_plan(years=10):
    periods = [{"start": f"{year}-01-01", "end": f"{year}-12-31"}
               for year in range(2026, 2026 + years)]
    plan = {"model": {}, "scenarios": {s: {} for s in ("bear", "base", "bull")},
            "scenario_rationale": {s: "Synthetic sourced business case " + s
                                   for s in ("bear", "base", "bull")}}
    def estimate(value):
        return {"value": deepcopy(value), "kind": "analyst_estimate", "evidence_ids": ["annual-1"],
                "rationale": "Synthetic analyst case anchored to the annual statement",
                "valid_until": DAY, "valid_until_basis": {"policy": "same_day", "as_of": DAY}}
    def fact(value, quote, unit):
        return {**estimate(value), "kind": "historical", "evidence_quote": quote,
                "quoted_value": value, "quoted_unit": unit,
                "period_quote": quote + (" for FY ended 2025-12-31" if quote.startswith("Revenue:")
                                         else " as of 2025-12-31")}
    plan["model"].update({
        "perimeter": estimate({"entity": "SYNTH-GROUP", "currency": "EUR", "share_class": "ordinary"}),
        "calendar": estimate({"valuation_date": "2025-12-31", "periods": periods,
                              "discount_convention": "annual_end"}),
        "quotation": estimate({"financial_currency": "EUR", "quote_currency": "EUR", "quote_unit": "EUR",
                               "quote_units_per_currency": 1., "financial_to_quote_rate": 1.,
                               "shares_per_quote": 1., "share_class": "ordinary", "price": 10.,
                               "price_as_of": "2025-12-31"}),
        "historical_revenue": fact(100., "Revenue: EUR 100 million", "EUR million"),
        "opening_nwc": fact(0., "Net working capital: EUR 0 million", "EUR million"),
        "shares": fact(10., "Diluted shares: 10 million shares", "million shares"),
        "capdev_amortization_years": estimate(4),
    })
    plan["model"]["quotation"].update(kind="historical", facts={
        "price": {"evidence_ids": ["annual-1"], "evidence_quote": "Price: EUR 10 per share",
                  "quoted_value": 10., "quoted_unit": "EUR per share", "date_quote": "Price: EUR 10 per share as of 2025-12-31"},
        "shares_per_quote": {"evidence_ids": ["annual-1"], "evidence_quote": "Ratio: 1 shares per quote",
                             "quoted_value": 1., "quoted_unit": "shares per quote"}})
    policies = {"tax": "no_loss_tax_credit", "sbc": "included_in_operating_costs",
                "leases": "operating_rent_in_costs", "research": "expensed_except_explicit_capdev",
                "cycle": "explicit_forecast", "patents": "explicit_forecast"}
    for scenario in plan["scenarios"].values():
        scenario["revenue_build"] = estimate({"basis": "subscribers_arpu", "volume": [10.] * years,
                                              "unit_price": [10.] * years, "utilization": [1.] * years,
                                              "other_revenue": [0.] * years})
        for driver, value in {"revenue_growth": 0., "gross_margin": .4, "rnd_pct": .1,
                              "sga_pct": .1, "capdev_pct": 0., "da_tan_pct": .05,
                              "tax_rate": 0., "capex_pct": .1, "nwc_pct": 0.}.items():
            scenario[driver] = estimate([value] * years)
        scenario["opening_intangible_amortization"] = estimate([0.] * years)
        scenario["terminal_bridge"] = estimate({"normalized_ebit": 15.,
            "capitalized_research_adjustment": 0., "cycle_adjustment": 0.,
            "expiring_product_loss": 0., "replacement_product_income": 0.,
            "other_adjustment": 0.})
        scenario.update({"wacc": estimate(.1), "terminal_growth": estimate(0.),
                         "terminal_ronic": estimate(.1), "net_debt": estimate(0.),
                         "equity_adjustments": estimate(0.), "accounting_policies": estimate(policies)})
    return plan


def _propose_operating(dossier, contract):
    assert dossier["ticker"] == "SYNTH-EXT"
    assert dossier["documents"][0]["id"] == "annual-1"
    assert contract["method_id"] == "operating_fcff"
    assert contract["schema"]["revenue_growth"][0] == "revenue_drivers"
    plan = _operating_plan()
    text = dossier["documents"][0]["text"]
    for driver, pattern in (("historical_revenue", r"Revenue: EUR (\d+) million"),
                            ("opening_nwc", r"Net working capital: EUR (\d+) million"),
                            ("shares", r"Diluted shares: (\d+) million shares")):
        observed = float(re.search(pattern, text).group(1))
        plan["model"][driver]["value"] = observed
        plan["model"][driver]["quoted_value"] = observed
    return plan


def test_plan_compiles_to_records_and_existing_fcff_adapter(tmp_path):
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=_propose_operating)
    assert result["status"] == "prepared", result["issues"]
    assert result["issues"] == []
    assert result["provenance"]["documents"]["annual-1"]["sha256_status"] == "verified"
    assert result["provenance"]["expiry_policies"]["base.wacc"] == {"policy": "same_day", "as_of": DAY}
    rows = result["proposal"]["method_records"]
    assert len(rows) == 61
    opening = next(r for r in rows if r["driver"] == "historical_revenue")
    forecast = next(r for r in rows if r["driver"] == "revenue_growth" and r["scenario"] == "base")
    assert opening["kind"] == "historical" and forecast["kind"] == "analyst_estimate"
    assert forecast["field"] == "revenue_drivers" and forecast["unit"] == "ratio"
    assert forecast["source_id"] == "https://example.org/issuer/annual-report"
    assert forecast["source_locator"] == "annual-1"
    assert len(result["bundle"]["case"]["records"]) == len(rows)
    valuation = generate_valuation("SYNTH-EXT", prepared_bundle=result["bundle"], output_dir=str(tmp_path))
    assert valuation["valuation_usability"]["usable"], valuation.get("acquisition_tasks")
    assert valuation["input_consumption"]["status"] == "complete"
    assert valuation["acquisition_snapshot"]["case"]["scenario_rationale_origin"] == "analysis_context"


def test_proposer_receives_shared_calendar_quotation_and_expiry_contract():
    seen = {}
    def capture(dossier, contract):
        seen.update(contract)
        return _propose_operating(dossier, contract)
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=capture)
    assert result["status"] == "prepared", result["issues"]
    shared = seen["common_record_contract"]
    assert "`annual_end`" in shared and "`ACT/365F`" in shared
    for key in _operating_plan()["model"]["quotation"]["value"]:
        assert "`" + key + "`" in shared
    assert seen["driver_envelope"]["valid_until_basis"] == seen["expiry_policy"]
    assert "perimeter and calendar are analyst_estimate" in seen["driver_envelope"]["kind_policy"]
    assert "## Operating FCFF" not in shared


def test_approved_short_horizon_is_reused_without_proposer():
    from test_sector_operating_drivers import operating_records
    source = {"status": "ok", "source_id": "method_records_archive", "as_of": DAY,
              "data": {"sets": {"operating_fcff": {
                  "set_id": 1, "method_version": "2", "records": operating_records(),
                  "earliest_valid_until": "2027-01-01",
                  "scenario_rationale": {s: "Existing approved case" for s in ("bear", "base", "bull")},
                  "review": {"reviewer": "synthetic reviewer", "reviewed_at": DAY}}}}, "records": []}
    providers = providers_for()
    providers["method_inputs"] = lambda ticker, *, as_of: deepcopy(source)
    bundle = prepare_sector_analysis("SYNTH-EXT", as_of=DAY, providers=providers)
    result = prepare_method_inputs(bundle, documents=[], propose=lambda *_: 1 / 0)
    assert result["status"] == "approved_reused" and result["issues"] == []
    assert result["bundle"]["snapshot_id"] == bundle["snapshot_id"]
    assert result["proposal"] is None


def test_missing_driver_stays_incomplete_and_no_value_is_filled():
    plan = _operating_plan()
    del plan["scenarios"]["base"]["revenue_growth"]
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any("revenue_growth" in issue["reason"] for issue in result["issues"])
    assert not any(r["driver"] == "revenue_growth" and r["scenario"] == "base"
                   for r in result["proposal"]["method_records"])


def test_invented_citation_is_rejected_without_replacing_bundle():
    plan = _operating_plan()
    plan["scenarios"]["base"]["revenue_growth"]["evidence_ids"] = ["imaginary"]
    original = _bundle()
    result = prepare_method_inputs(original, documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any("imaginary" in issue["reason"] for issue in result["issues"])
    assert result["bundle"]["snapshot_id"] == original["snapshot_id"]


def test_real_citation_does_not_certify_invented_historical_number():
    plan = _operating_plan()
    plan["model"]["historical_revenue"]["value"] = 123.
    plan["model"]["historical_revenue"]["quoted_value"] = 123.
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any("historical_revenue" in issue["reason"] for issue in result["issues"])
    assert result["bundle"]["case"]["records"] == []


def test_opening_balance_cannot_bypass_proof_by_becoming_estimate():
    plan = _operating_plan()
    item = plan["model"]["historical_revenue"]
    item.update(value=987654., kind="analyst_estimate")
    for key in ("evidence_quote", "quoted_value", "quoted_unit"):
        item.pop(key)
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any(i["code"] == "opening_fact_required" for i in result["issues"])


def test_quotation_price_requires_observed_source_proof():
    plan = _operating_plan()
    plan["model"]["quotation"]["value"]["price"] = 99999.
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any(i["field"] == "quotation" for i in result["issues"])


def test_ten_daily_periods_do_not_count_as_ten_years():
    from datetime import date, timedelta
    plan = _operating_plan()
    plan["model"]["calendar"]["value"]["discount_convention"] = "ACT/365F"
    plan["model"]["calendar"]["value"]["periods"] = [
        {"start": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
         "end": (date(2026, 1, 1) + timedelta(days=i)).isoformat()} for i in range(10)]
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any(i["code"] == "annual_period_required" for i in result["issues"])


def test_structured_primary_facts_and_explicit_balance_reconciliation():
    plan = _operating_plan()
    text = json.dumps({"facts": [
        {"taxonomy": "us-gaap", "concept": "Revenues", "value": 100000000,
         "unit": "EUR", "start": "2025-01-01", "end": "2025-12-31"},
        {"taxonomy": "us-gaap", "concept": "AccountsReceivableNetCurrent", "value": 12500000,
         "unit": "EUR", "end": "2025-12-31"},
        {"taxonomy": "us-gaap", "concept": "AccountsPayableCurrent", "value": 12500000,
         "unit": "EUR", "end": "2025-12-31"}]})
    docs = _documents() + [{"id": "primary-facts", "text": text, "sha256": sha256(text.encode()).hexdigest(),
        "url": "https://example.org/issuer/facts.json", "published_at": "2026-02-01"}]
    def proof(index, amount):
        return {"evidence_ids": ["primary-facts"], "quoted_value": amount, "quoted_unit": "EUR",
                "evidence_pointer": {key: f"/facts/{index}/{field}" for key, field in
                                     (("value", "value"), ("unit", "unit"), ("period", "end"))}}
    for driver in ("historical_revenue", "opening_nwc"):
        for key in ("evidence_quote", "quoted_value", "quoted_unit", "period_quote"):
            plan["model"][driver].pop(key)
        plan["model"][driver]["evidence_ids"] = ["primary-facts"]
    plan["model"]["historical_revenue"].update(proof(0, 100000000))
    plan["model"]["opening_nwc"]["calculation"] = {"operation": "sum", "terms": [
        {**proof(1, 12500000), "coefficient": 1}, {**proof(2, 12500000), "coefficient": -1}]}
    result = prepare_method_inputs(_bundle(), documents=docs, propose=lambda *_: plan)
    assert result["status"] == "prepared", result["issues"]
    plan["model"]["historical_revenue"]["quoted_value"] = 999000000
    result = prepare_method_inputs(_bundle(), documents=docs, propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any(i["code"] == "unverified_fact" for i in result["issues"])
    plan["model"]["historical_revenue"].update(proof(0, 100000000))
    docs[1]["text"] = text.replace("2025-12-31", "2024-12-31")
    docs[1]["sha256"] = sha256(docs[1]["text"].encode()).hexdigest()
    result = prepare_method_inputs(_bundle(), documents=docs, propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any("periodo" in i["reason"] for i in result["issues"])


def test_json_fact_cannot_borrow_period_or_unit_from_another_observation():
    from bellomberg.valuation.input_evidence import structured_fact_proof
    from bellomberg.valuation.input_preparation import _source_scale
    evidence = [{"id": "facts", "text": json.dumps({"facts": [
        {"value": 100, "unit": "EUR million", "end": "2024-12-31"},
        {"value": 1, "unit": "USD million", "end": "2025-12-31"}]})}]
    item = {"value": 100, "evidence_ids": ["facts"], "quoted_value": 100, "quoted_unit": "EUR million",
            "evidence_pointer": {"value": "/facts/0/value", "unit": "/facts/0/unit", "period": "/facts/1/end"}}
    assert structured_fact_proof(item, evidence, "EUR million", "2025-12-31", scale=_source_scale) is not None
    item.update(quoted_unit="USD million")
    item["evidence_pointer"].update(unit="/facts/1/unit", period="/facts/0/end")
    assert structured_fact_proof(item, evidence, "USD million", "2024-12-31", scale=_source_scale) is not None


def test_ttm_revenue_survives_full_compiler_and_existing_engine(tmp_path):
    from test_input_evidence_semantics import _ttm_case
    item, xbrl_documents = _ttm_case()
    plan = json.loads(json.dumps(_operating_plan()).replace("2025-12-31", "2026-06-30"))
    plan["model"]["calendar"]["value"]["periods"] = [
        {"start": f"{year}-07-01", "end": f"{year + 1}-06-30"} for year in range(2026, 2036)]
    historical = plan["model"]["historical_revenue"]
    for key in ("evidence_quote", "quoted_value", "quoted_unit", "period_quote"):
        historical.pop(key)
    historical.update(item)
    for scenario in plan["scenarios"].values():
        scenario["revenue_build"]["value"]["unit_price"] = [11.5] * 10
        scenario["terminal_bridge"]["value"]["normalized_ebit"] = 17.25
    documents = _documents()
    documents[0]["text"] = documents[0]["text"].replace("2025-12-31", "2026-06-30")
    documents[0]["sha256"] = sha256(documents[0]["text"].encode()).hexdigest()
    documents.extend(xbrl_documents)

    def propose(dossier, contract):
        assert contract["historical_revenue_policy"]["interim_operation"] == "trailing_twelve_months"
        return plan

    result = prepare_method_inputs(_bundle(), documents=documents, propose=propose)
    assert result["status"] == "prepared", result["issues"]
    record = next(row for row in result["proposal"]["method_records"] if row["driver"] == "historical_revenue")
    assert record["value"] == 115. and record["kind"] == "historical"
    assert all(doc["id"] in record["source_locator"] for doc in xbrl_documents)
    valuation = generate_valuation("SYNTH-EXT", prepared_bundle=result["bundle"], output_dir=str(tmp_path))
    assert valuation["valuation_usability"]["usable"], valuation.get("acquisition_tasks")


def test_unsupported_numeric_scale_is_not_assumed():
    plan = _operating_plan()
    plan["model"]["historical_revenue"]["quoted_unit"] = "USD million"
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any("historical_revenue" in issue["reason"] for issue in result["issues"])


def test_expiry_text_must_be_in_a_cited_source():
    plan = _operating_plan()
    plan["scenarios"]["base"]["wacc"]["valid_until"] = "2026-12-31"
    plan["scenarios"]["base"]["wacc"]["valid_until_basis"] = {"policy": "same_day", "as_of": DAY}
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any(issue["code"] == "unverified_expiry" for issue in result["issues"])


def test_future_source_and_bad_hash_never_reach_proposer():
    for documents in (_documents(published_at="2026-09-11"),
                      [{k: v for k, v in _documents()[0].items() if k != "published_at"}],
                      [{**_documents()[0], "sha256": "0" * 64}]):
        result = prepare_method_inputs(_bundle(), documents=documents,
                                       propose=lambda *_: 1 / 0)
        assert result["status"] == "incomplete"
        assert result["proposal"] is None
        assert result["issues"]


def test_raw_document_provenance_is_retained_without_claiming_byte_verification():
    doc = {**_documents()[0], "document_sha256": "a" * 64,
           "metadata": {"issuer": "SYNTH-GROUP", "report_date": "2025-12-31"},
           "extraction_coverage": {"status": "partial", "section": "financials"}}
    result = prepare_method_inputs(_bundle(), documents=[doc], propose=_propose_operating)
    entry = result["provenance"]["documents"]["annual-1"]
    assert entry["document_sha256"] == "a" * 64
    assert entry["document_sha256_status"] == "declared_not_rechecked"
    assert entry["metadata"] == doc["metadata"]
    assert entry["extraction_coverage"] == doc["extraction_coverage"]


def test_conflicting_provider_record_blocks_preparation():
    from test_sector_operating_drivers import operating_records
    conflict = deepcopy(next(r for r in operating_records() if r["driver"] == "historical_revenue"))
    conflict["value"] = 999.
    result = prepare_method_inputs(_bundle(records=[conflict]), documents=_documents(),
                                   propose=_propose_operating)
    assert result["status"] == "incomplete"
    assert any(issue["code"] == "conflicting_input" for issue in result["issues"])


def test_legacy_assumptions_are_not_silently_dropped():
    result = prepare_method_inputs(_bundle(assumptions={"growth_override": [.2]}),
                                   documents=_documents(), propose=lambda *_: 1 / 0)
    assert result["status"] == "incomplete"
    assert any("growth_override" in issue["reason"] for issue in result["issues"])


def test_new_fcff_candidate_requires_ten_years():
    result = prepare_method_inputs(_bundle(), documents=_documents(),
                                   propose=lambda *_: _operating_plan(years=9))
    assert result["status"] == "incomplete"
    assert any("10" in issue["reason"] for issue in result["issues"])


def test_bank_missing_legal_capital_contract_is_specific_block():
    bank = _bundle(business="bank")
    plan = _operating_plan()
    plan["model"] = {k: v for k, v in plan["model"].items()
                     if k in ("perimeter", "calendar", "quotation")}
    plan["scenarios"] = {s: {} for s in ("bear", "base", "bull")}
    result = prepare_method_inputs(bank, documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert any("legal_structure" in issue["reason"] for issue in result["issues"])
    assert any("capital" in issue["reason"] for issue in result["issues"])


def test_bank_parent_cash_literal_is_rejected_even_when_ledger_is_incomplete():
    bank = _bundle(business="bank")
    plan = _operating_plan()
    estimate = deepcopy(plan["model"]["capdev_amortization_years"])
    plan["model"] = {k: v for k, v in plan["model"].items()
                     if k in ("perimeter", "calendar", "quotation")}
    plan["model"]["legal_structure"] = {**estimate,
        "value": {"parent_entity": "SYNTH-PARENT",
                  "subsidiaries": [{"id": "SYNTH-LEGAL", "regime": "synthetic common-equity regime"}],
                  "capital_basis": "common_equity", "accounting_basis": "GAAP"}}
    plan["scenarios"] = {s: {"capital.parent_opening_cash": {**estimate, "value": 5., "kind": "historical",
                          "evidence_quote": "Parent cash: EUR 5 million", "quoted_value": 5., "quoted_unit": "EUR million",
                          "period_quote": "Parent cash: EUR 5 million as of 2025-12-31"}}
                         for s in ("bear", "base", "bull")}
    result = prepare_method_inputs(bank, documents=_documents(), propose=lambda *_: plan)
    assert result["status"] == "incomplete"
    assert not any(r["driver"] == "capital.parent_opening_cash"
                   for r in result["proposal"]["method_records"])
    assert any(issue["field"] == "capital.parent_opening_cash"
               and issue["code"] == "unverified_fact" for issue in result["issues"])
    assert any("capital.subsidiaries.0" in issue["reason"] for issue in result["issues"])
