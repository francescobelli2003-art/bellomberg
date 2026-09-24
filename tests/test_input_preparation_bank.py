"""A sourced, synthetic bank plan reaches the existing legal-capital adapter offline.

The proposer below writes economic assumptions, never method-record metadata. The
document is deliberately fictional and does not attest a real bank or approval.
"""
from hashlib import sha256
import json
import re

import pytest

from bellomberg.valuation.dcf_engine import generate_valuation
from bellomberg.valuation.input_preparation import prepare_method_inputs
from bellomberg.valuation.regulatory_evidence import normalize_regulatory_pdf
from bellomberg.valuation.sector_analysis import prepare_sector_analysis
from test_sector_analysis import DAY, providers_for


ISSUER = "SYNTH-BANK-GROUP"
PARENT = "SYNTH-BANK-PARENT"
BANK = "SYNTH-BANK-LEGAL"
SOURCE_ID = "fictional-bank-statement"
SOURCE_URL = "https://example.org/fictional-bank/statement"
PARENT_PDF_URL = "https://example.org/fictional-bank/parent-report.pdf"
STATEMENT = "\n".join((
    "Fictional bank statement; all amounts below are test data.",
    "Consolidated common equity: USD 100 million as of 2025-12-31.",
    "Parent common equity: USD 10 million as of 2025-12-31.",
    "Consolidation adjustments: USD 0 million as of 2025-12-31.",
    "Intangible assets: USD 0 million as of 2025-12-31.",
    "Parent cash: USD 10 million as of 2025-12-31.",
    "Parent debt: USD 0 million as of 2025-12-31.",
    "Subsidiary GAAP equity: USD 90 million as of 2025-12-31.",
    "Subsidiary GAAP-to-statutory equity: USD 0 million as of 2025-12-31.",
    "Subsidiary statutory capital: USD 90 million as of 2025-12-31.",
    BANK + " cash: USD 20 million as of 2025-12-31.",
    "Ordinary shares: 10 million shares as of 2025-12-31.",
    "Observed price: USD 10 per share as of 2025-12-31.",
    "Quoted shares: 1 shares per quote.",
))


def _document():
    return {"id": SOURCE_ID, "url": SOURCE_URL, "published_at": "2026-09-09",
            "text": STATEMENT, "sha256": sha256(STATEMENT.encode("utf-8")).hexdigest()}


def _regulatory_documents():
    """Synthetic attested PDF text plus its deterministic parent Schedule PC facts."""
    pages = (
        "\n".join(("FR Y-9LP", "Board of Governors of the Federal Reserve System",
                   "Parent Company Only Financial Statements for Large Holding Companies",
                   "Date of Report: December 31, 2025", PARENT,
                   "Printed Name of Chief Financial Officer (or Equivalent) (BHCP C490) Legal Title of Holding Company (RSSD 9017)")),
        "\n".join(("FR Y-9LP", "Schedule PC—Parent Company Only Balance Sheet",
                   "Dollar Amounts in Thousands  BHCP Amount", "Assets",
                   "a. Balances with subsidiary or affiliated depository institutions ........ 5993 8,000 1.a.",
                   "b. Balances with unrelated depository institutions ........ 0010 2,000 1.b.")),
        "\n".join(("FR Y-9LP", "Schedule PC— Continued",
                   "Dollar Amounts in Thousands  BHCP Amount", "Liabilities and Equity Capital",
                   "a. Commercial paper ........ 2309 0 13.a.",
                   "b. Other borrowings ........ 2332 0 13.b.",
                   "14. Other borrowed money with a remaining maturity of more than one year ........ 0368 0 14.",
                   "16. Subordinated notes and debentures (1) ........ 4062 0 16.",
                   "a. Perpetual preferred stock (including related surplus) ........ 3283 2,000 20.a.",
                   "h. TOTAL EQUITY CAPITAL (sum of items 20.a through 20.f) ........ 3210 12,000 20.h.")),
    )
    references, offset = [], 0
    for number, page in enumerate(pages, 1):
        references.append({"pagina": number, "inizio": offset, "fine": offset + len(page),
                           "sha256": sha256(page.encode("utf-8")).hexdigest()})
        offset += len(page) + 1
    body = "\n".join(pages)
    rawsha = sha256(b"fictional parent report bytes").hexdigest()
    pdf = {"id": rawsha, "url": PARENT_PDF_URL, "published_at": None,
           "document_sha256": rawsha, "text": body,
           "sha256": sha256(body.encode("utf-8")).hexdigest(),
           "availability_basis": "observed_download", "available_at": "2026-09-09",
           "retrieval": {"url": PARENT_PDF_URL, "document_sha256": rawsha,
                         "retrieved_at": "2026-09-09T08:00:00Z"},
           "page_references": references,
           "extraction_coverage": {"format": "pdf", "pages": 3, "raw_sha256_checked": True}}
    normalized = normalize_regulatory_pdf(pdf, expected_form="FR Y-9LP",
                                          expected_entity=PARENT, expected_report_date="2025-12-31")
    assert normalized["status"] == "ready", normalized["issues"]
    return [pdf, normalized["documents"][0]]


def _documents():
    return [_document(), *_regulatory_documents()]


def _regulatory_bridge(facts_document, components, value):
    facts = json.loads(facts_document["text"])["facts"]
    indices = {fact["concept"]: index for index, fact in enumerate(facts)}
    terms = []
    for code, sign in components.items():
        index = indices[code]
        fact = facts[index]
        terms.append({"coefficient": sign, "evidence_ids": [facts_document["id"]],
                      "evidence_pointer": {"value": f"/facts/{index}/value",
                                           "unit": f"/facts/{index}/unit",
                                           "period": f"/facts/{index}/end"},
                      "quoted_value": fact["value"], "quoted_unit": fact["unit"]})
    assert sum(term["coefficient"] * term["quoted_value"] for term in terms) == value * 1000
    return {**_estimate(value, "Exact parent Schedule PC component bridge from fictional PDF"),
            "kind": "historical", "evidence_ids": [facts_document["id"]],
            "calculation": {"operation": "sum", "terms": terms}}


def _estimate(value, reason):
    return {"value": value, "kind": "analyst_estimate", "evidence_ids": [SOURCE_ID],
            "rationale": reason, "valid_until": DAY,
            "valid_until_basis": {"policy": "same_day", "as_of": DAY}}


def _observed(text, label, unit):
    line = next(line for line in text.splitlines() if line.startswith(label + ": "))
    number = re.search(r"(?<!\w)-?\d+(?:\.\d+)?(?=\s)", line)
    assert number is not None, label
    observed = float(number.group())
    return {**_estimate(observed, "Observed in the fictional bank statement: " + label),
            "kind": "historical", "evidence_quote": line.split(" as of ")[0],
            "period_quote": line, "quoted_value": observed, "quoted_unit": unit}


def _terminal_proof(net_income, subsidiary_income):
    """One explicit continuing year, balanced at zero growth and full payout."""
    return {"capital": {
        "distribution_policy": "full_sweep_after_buffers",
        "reconciliation_basis": "Fictional GAAP/statutory equity bridge",
        "upstream_approval_basis": "Conditional assumed payout; no approval claimed",
        "terminal_basis": "Fictional continuing equity with unchanged capital buffers",
        "parent_opening_cash": 10., "parent_cash_minimum": [10.],
        "parent_opening_debt": 0., "terminal_debt": 0.,
        "parent_gaap_net_income": [2.], "consolidation_adjustments": [0.],
        "parent_cash_flows": {
            "admin_fees_received": [2.], "tax_transfers_received": [0.],
            "investment_cash_income": [0.], "other_cash_receipts": [0.],
            "holding_cash_costs": [0.], "external_taxes": [0.],
            "interest_paid": [0.], "capex": [0.], "acquisitions": [0.],
            "debt_issued": [0.], "debt_repaid": [0.]},
        "subsidiaries": [{
            "id": BANK, "opening_gaap_equity": 90.,
            "opening_gaap_to_statutory_equity": 0.,
            "opening_statutory_capital": 90.,
            "gaap_net_income": [subsidiary_income],
            "gaap_to_statutory_income": [0.],
            "other_statutory_movements": [0.],
            "required_statutory_capital": [90.],
            "liquidity_before_transfers": [20. + subsidiary_income],
            "minimum_liquidity": [10.],
            "permitted_distribution": [subsidiary_income],
            "proposed_distribution": [subsidiary_income],
            "proposed_contribution": [0.]}],
        "ke": .1, "shares_m": 10., "discount_periods": [1.],
        "terminal_equity": 0.},
        "capital_constraints": {BANK: {"basis": "common_equity", "constraints": [{
            "id": "Fictional common-equity requirement", "exposure": [900.],
            "ratio": [.1], "buffer": [0.], "absolute_floor": [0.],
            "terminal_requirement": 90.}]}},
        "liquidity_bridge": {BANK: {
            "opening_cash": 20., "operating_cash": [net_income],
            "investing_cash": [0.], "financing_cash": [0.],
            "parent_fees_paid": [2.], "parent_tax_paid": [0.]}}}


def _scenario_plan(net_income, text, facts_document):
    """A full ten-year analyst case derived from a documented opening position."""
    years = 10
    subsidiary_income = net_income - 2.
    economics = {
        "net_interest_income": [net_income + 2.] * years,
        "fee_income": [0.] * years,
        "operating_expenses": [2.] * years,
        "credit_losses": [0.] * years,
        "taxes": [0.] * years,
        "other_income": [0.] * years,
        "preferred_and_minorities": [0.] * years,
        "other_comprehensive_income": [0.] * years,
        "terminal_roe": net_income / 100., "terminal_growth": 0.,
        "funding_capacity": [0.] * years,
        "ownership_policy": "pro_rata_existing_shareholders",
        "capital_constraints": {BANK: {"basis": "common_equity", "constraints": [{
            "id": "Fictional common-equity requirement", "exposure": [900.] * years,
            "ratio": [.1] * years, "buffer": [0.] * years,
            "absolute_floor": [0.] * years, "terminal_requirement": 90.}]}},
        "liquidity_bridge": {BANK: {
            "opening_cash": 20., "operating_cash": [net_income] * years,
            "investing_cash": [0.] * years, "financing_cash": [0.] * years,
            "parent_fees_paid": [2.] * years, "parent_tax_paid": [0.] * years}},
        "terminal_ledger": _terminal_proof(net_income, subsidiary_income),
        "capital.distribution_policy": "full_sweep_after_buffers",
        "capital.reconciliation_basis": "Fictional GAAP/statutory equity bridge",
        "capital.upstream_approval_basis": "Conditional assumed payout; no approval claimed",
        "capital.terminal_basis": "Fictional continuing equity with unchanged capital buffers",
        "capital.parent_cash_minimum": [10.] * years,
        "capital.terminal_debt": 0.,
        "capital.parent_gaap_net_income": [2.] * years,
        "capital.consolidation_adjustments": [0.] * years,
        "capital.discount_periods": [float(i) for i in range(1, years + 1)],
        "capital.ke": .1,
        "capital.terminal_equity": 10. * net_income,
        "capital.parent_cash_flows.admin_fees_received": [2.] * years,
        "capital.parent_cash_flows.tax_transfers_received": [0.] * years,
        "capital.parent_cash_flows.investment_cash_income": [0.] * years,
        "capital.parent_cash_flows.other_cash_receipts": [0.] * years,
        "capital.parent_cash_flows.holding_cash_costs": [0.] * years,
        "capital.parent_cash_flows.external_taxes": [0.] * years,
        "capital.parent_cash_flows.interest_paid": [0.] * years,
        "capital.parent_cash_flows.capex": [0.] * years,
        "capital.parent_cash_flows.acquisitions": [0.] * years,
        "capital.parent_cash_flows.debt_issued": [0.] * years,
        "capital.parent_cash_flows.debt_repaid": [0.] * years,
        "capital.subsidiaries.0.gaap_net_income": [subsidiary_income] * years,
        "capital.subsidiaries.0.gaap_to_statutory_income": [0.] * years,
        "capital.subsidiaries.0.other_statutory_movements": [0.] * years,
        "capital.subsidiaries.0.required_statutory_capital": [90.] * years,
        "capital.subsidiaries.0.liquidity_before_transfers": [20. + subsidiary_income] * years,
        "capital.subsidiaries.0.minimum_liquidity": [10.] * years,
        "capital.subsidiaries.0.permitted_distribution": [subsidiary_income] * years,
        "capital.subsidiaries.0.proposed_distribution": [subsidiary_income] * years,
        "capital.subsidiaries.0.proposed_contribution": [0.] * years,
    }
    reason = ("Fictional ten-year case: constant net interest and explicit zero loss, "
              "tax, OCI, and financing assumptions; subsidiary income pays out only "
              "after the stated common-equity and liquidity buffers.")
    result = {driver: _estimate(value, reason) for driver, value in economics.items()}
    cash = _observed(text, BANK + ' cash', 'USD million')
    result['liquidity_bridge']['facts'] = {BANK: {key: cash[key] for key in (
        'evidence_ids', 'evidence_quote', 'period_quote', 'quoted_value', 'quoted_unit')}}
    for driver, label, unit in (
        ("capital.parent_opening_debt", "Parent debt", "USD million"),
        ("capital.shares_m", "Ordinary shares", "million shares"),
        ("capital.subsidiaries.0.opening_gaap_equity", "Subsidiary GAAP equity", "USD million"),
        ("capital.subsidiaries.0.opening_gaap_to_statutory_equity", "Subsidiary GAAP-to-statutory equity", "USD million"),
        ("capital.subsidiaries.0.opening_statutory_capital", "Subsidiary statutory capital", "USD million"),
    ):
        result[driver] = _observed(text, label, unit)
    result["capital.parent_opening_cash"] = _regulatory_bridge(
        facts_document, {"BHCP5993": 1, "BHCP0010": 1}, 10.)
    return result


def _propose(dossier, contract):
    assert contract["method_id"] == "bank_residual_income"
    assert contract["standard_horizon_years"] == 10
    assert contract["bank_dynamic_capital"] is True
    assert contract["bank_capital_mapping"]["capital.parent_opening_cash"][0] == "parent_ledger"
    text = dossier["documents"][0]["text"]
    facts_document = next(doc for doc in dossier["documents"]
                          if (doc.get("metadata") or {}).get("normalizer") == "regulatory_pdf_v1")
    model = {
        "perimeter": _estimate({"entity": ISSUER, "currency": "USD", "share_class": "ordinary"},
                               "Fictional consolidated bank perimeter"),
        "calendar": _estimate({"valuation_date": "2025-12-31",
                               "periods": [{"start": f"{year}-01-01", "end": f"{year}-12-31"}
                                           for year in range(2026, 2036)],
                               "discount_convention": "annual_end"},
                              "Ten full calendar years after the observed opening balance"),
        "legal_structure": _estimate({"parent_entity": PARENT,
                                      "subsidiaries": [{"id": BANK,
                                                        "regime": "Fictional common-equity regime"}],
                                      "capital_basis": "common_equity", "accounting_basis": "GAAP"},
                                     "Fictional legal-entity model"),
    }
    quotation = {"financial_currency": "USD", "quote_currency": "USD",
                 "quote_unit": "USD", "quote_units_per_currency": 1.,
                 "financial_to_quote_rate": 1., "shares_per_quote": 1.,
                 "share_class": "ordinary", "price": 10., "price_as_of": "2025-12-31"}
    model["quotation"] = {**_estimate(quotation, "Observed fictional quote"),
        "kind": "historical", "facts": {
            "price": {"evidence_ids": [SOURCE_ID],
                      "evidence_quote": "Observed price: USD 10 per share",
                      "quoted_value": 10., "quoted_unit": "USD per share",
                      "date_quote": "Observed price: USD 10 per share as of 2025-12-31."},
            "shares_per_quote": {"evidence_ids": [SOURCE_ID],
                                 "evidence_quote": "Quoted shares: 1 shares per quote.",
                                 "quoted_value": 1., "quoted_unit": "shares per quote"}}}
    for driver, label in (
        ("opening_common_equity", "Consolidated common equity"),
        ("opening_consolidation_adjustments", "Consolidation adjustments"),
        ("opening_intangibles", "Intangible assets"),
    ):
        model[driver] = _observed(text, label, "USD million")
    model["opening_parent_equity"] = _regulatory_bridge(
        facts_document, {"BHCP3210": 1, "BHCP3283": -1}, 10.)
    return {"model": model,
            "scenarios": {name: _scenario_plan(income, text, facts_document)
                          for name, income in (("bear", 8.), ("base", 10.), ("bull", 12.))},
            "scenario_rationale": {
                "bear": "Fictional low income of USD 8 million, constant for ten years",
                "base": "Fictional central income of USD 10 million, constant for ten years",
                "bull": "Fictional high income of USD 12 million, constant for ten years"}}


def test_raw_bank_statement_to_economic_plan_records_and_adapter(tmp_path):
    bundle = prepare_sector_analysis("SYNTH-BANK", as_of=DAY, providers=providers_for("bank"))
    prepared = prepare_method_inputs(bundle, documents=_documents(), propose=_propose)
    assert prepared["status"] == "prepared", prepared["issues"]
    assert prepared["issues"] == []
    assert prepared["proposal"]["approval_status"] == "automatic_non_approved"
    assert prepared["provenance"]["documents"][SOURCE_ID]["origin_check"] == "offline_reference_only"
    rows = prepared["proposal"]["method_records"]
    assert rows and len(rows) == len(prepared["bundle"]["case"]["records"])
    assert {row["source_id"] for row in rows} == {SOURCE_URL, PARENT_PDF_URL}
    normalized_id = _documents()[-1]["id"]
    assert {row["source_locator"] for row in rows} == {SOURCE_ID, normalized_id}
    assert next(row for row in rows if row["driver"] == "opening_parent_equity")["source_locator"] == normalized_id
    parent_cash = next(row for row in rows if row["driver"] == "capital.parent_opening_cash" and row["scenario"] == "base")
    assert parent_cash["source_locator"] == normalized_id
    assert parent_cash["field"] == "parent_ledger"
    assert parent_cash["entity"] == PARENT and parent_cash["unit"] == "USD million"
    assert parent_cash["period"] == "2025-12-31"
    opening = next(row for row in rows if row["driver"] == "opening_common_equity")
    future = next(row for row in rows if row["driver"] == "net_interest_income" and row["scenario"] == "base")
    assert opening["kind"] == "historical" and future["kind"] == "analyst_estimate"
    assert opening["field"] == "tangible_book_equity" and opening["unit"] == "USD million"
    assert future["field"] == "equity_return_path" and len(future["value"]) == 10
    assert prepared["provenance"]["expiry_policies"]["base.net_interest_income"] == {
        "policy": "same_day", "as_of": DAY}

    valuation = generate_valuation("SYNTH-BANK", prepared_bundle=prepared["bundle"],
                                   output_dir=str(tmp_path))
    assert valuation["valuation_usability"]["usable"], valuation.get("error")
    assert valuation["input_consumption"]["status"] == "complete"
    assert valuation["fair_value_bear"] == pytest.approx(8.)
    assert valuation["fair_value_base"] == pytest.approx(10.)
    assert valuation["fair_value_bull"] == pytest.approx(12.)
    assert valuation["calculation_details"]["scenarios"]["base"]["cash_equity_value"] == pytest.approx(100.)


def test_nested_opening_cash_cannot_pass_as_an_unsourced_forecast_assumption():
    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))
    def propose(dossier, contract):
        plan = _propose(dossier, contract)
        for scope in plan['scenarios'].values():
            scope['liquidity_bridge'].pop('facts', None)
        return plan
    result = prepare_method_inputs(bundle, documents=_documents(), propose=propose)
    assert result['status'] == 'incomplete'
    assert any(row['code'] == 'unverified_opening_cash' for row in result['issues'])


@pytest.mark.parametrize('problem', ['wrong_value', 'wrong_entity', 'wrong_period', 'missing_entity', 'uncited_source', 'extra_field'])
def test_nested_opening_cash_proof_is_bound_to_amount_bank_date_and_cited_source(problem):
    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))
    def propose(dossier, contract):
        plan = _propose(dossier, contract)
        item = plan['scenarios']['base']['liquidity_bridge']; fact = item['facts'][BANK]
        if problem == 'wrong_value': item['value'][BANK]['opening_cash'] += 1
        elif problem == 'wrong_entity': item['facts']['OTHER BANK'] = item['facts'].pop(BANK)
        elif problem == 'wrong_period': fact['period_quote'] = fact['period_quote'].replace('2025-12-31', '2024-12-31')
        elif problem == 'missing_entity': item['facts'] = {}
        elif problem == 'uncited_source': fact['evidence_ids'] = ['not-cited']
        else: fact['manual_override'] = True
        return plan
    result = prepare_method_inputs(bundle, documents=_documents(), propose=propose)
    assert result['status'] == 'incomplete'
    assert any(row['code'] == 'unverified_opening_cash' for row in result['issues'])


def test_parent_cash_date_pointer_typo_is_traced_without_mutating_the_proposal():
    from copy import deepcopy
    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))
    captured = {}
    def propose(dossier, contract):
        plan = _propose(dossier, contract)
        pointers = plan['scenarios']['base']['capital.parent_opening_cash']['calculation']['terms'][1]['evidence_pointer']
        pointers['value'] = pointers['period']
        captured.update(plan=plan, before=deepcopy(plan))
        return plan
    result = prepare_method_inputs(bundle, documents=_documents(), propose=propose)
    assert result['status'] == 'prepared', result['issues']
    assert captured['plan'] == captured['before']
    record = next(r for r in result['proposal']['method_records'] if r['driver'] == 'capital.parent_opening_cash' and r['scenario'] == 'base')
    assert record['value'] == 10.
    assert 'SOURCE POINTER NORMALIZED' in record['rationale']
    assert 'supplied_value_pointer' in record['rationale'] and 'validated_value_pointer' in record['rationale']


@pytest.mark.parametrize('problem', ['wrong_quote', 'wrong_unit', 'other_fact', 'arbitrary_pointer'])
def test_parent_cash_pointer_normalization_cannot_fix_economic_or_ambiguous_claims(problem):
    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))
    def propose(dossier, contract):
        plan = _propose(dossier, contract)
        item = plan['scenarios']['base']['capital.parent_opening_cash']
        term = item['calculation']['terms'][1]; ptr = term['evidence_pointer']; ptr['value'] = ptr['period']
        if problem == 'wrong_quote': term['quoted_value'] += 1
        elif problem == 'wrong_unit': term['quoted_unit'] = 'EUR thousand'
        elif problem == 'other_fact': ptr['unit'] = '/facts/0/unit'
        else: ptr['value'] = '/unrelated/path'
        return plan
    result = prepare_method_inputs(bundle, documents=_documents(), propose=propose)
    assert result['status'] == 'incomplete'
    assert any(r['field'] == 'capital.parent_opening_cash' and r['code'] == 'unverified_fact' for r in result['issues'])


def test_staged_parent_cash_repair_and_constraint_results_are_visible_only_after_validation():
    from copy import deepcopy
    from bellomberg.valuation.preparation_ai import StagedProposer
    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))
    contexts, answers, originals = [], [], []
    def propose(dossier, contract):
        contexts.append(deepcopy(dossier))
        full = _propose(dossier, contract)
        stage = contract['preparation_stage']; scope = stage['scope']; names = stage['drivers']
        values = full['model'] if scope == 'model' else full['scenarios'][scope]
        answer = {'drivers': {name: values[name] for name in names}, 'rationale': 'Synthetic documented stage'}
        if scope == 'bear' and 'capital.parent_opening_cash' in names:
            ptr = answer['drivers']['capital.parent_opening_cash']['calculation']['terms'][1]['evidence_pointer']
            ptr['value'] = ptr['period']
        answers.append(answer); originals.append(deepcopy(answer))
        return answer
    result = prepare_method_inputs(bundle, documents=_documents(), propose=StagedProposer(propose, drivers_per_stage=12))
    assert result['status'] == 'prepared', result['issues']
    assert answers == originals
    assert all('completed_proof_normalizations' not in row and 'derived_bank_capital' not in row for row in contexts[:3])
    fourth = contexts[3]
    assert fourth['completed_proof_normalizations'][0]['driver'] == 'capital.parent_opening_cash'
    assert fourth['derived_bank_capital']['bear']['entities'][BANK]['required_statutory_capital'] == [90.] * 10
    parent = fourth['completed_plan']['scenarios']['bear']['capital.parent_opening_cash']
    assert parent['calculation']['terms'][1]['evidence_pointer']['value'].endswith('/end')


def test_parent_cash_pointer_typo_remains_strict_without_a_trace_collector():
    from bellomberg.valuation.input_preparation import _fact_proof
    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=DAY, providers=providers_for('bank'))
    proof_errors = []
    def propose(dossier, contract):
        plan = _propose(dossier, contract)
        item = plan['scenarios']['bear']['capital.parent_opening_cash']
        ptr = item['calculation']['terms'][1]['evidence_pointer']; ptr['value'] = ptr['period']
        evidence = [row for row in _documents() if row['id'] in item['evidence_ids']]
        proof_errors.append(_fact_proof('capital.parent_opening_cash', item, evidence, 'USD million', '2025-12-31',
                           expected_entity=PARENT))
        return plan
    prepare_method_inputs(bundle, documents=_documents(), propose=propose)
    assert len(proof_errors) == 1 and proof_errors[0] is not None


def _same_issuer_proposal(dossier, contract):
    plan = _propose(dossier, contract)
    plan["model"]["perimeter"]["value"]["entity"] = PARENT
    return plan


def test_parent_and_consolidated_reporting_may_share_legal_issuer(tmp_path):
    bundle = prepare_sector_analysis("SYNTH-BANK", as_of=DAY, providers=providers_for("bank"))
    prepared = prepare_method_inputs(bundle, documents=_documents(), propose=_same_issuer_proposal)
    assert prepared["status"] == "prepared", prepared["issues"]
    rows = prepared["proposal"]["method_records"]
    openings = {row["driver"]: row for row in rows
                if row["driver"] in {"opening_common_equity", "opening_parent_equity"}}
    assert {row["entity"] for row in openings.values()} == {PARENT}
    assert openings["opening_common_equity"]["value"] == 100.
    assert openings["opening_parent_equity"]["value"] == 10.
    assert openings["opening_common_equity"]["source_locator"] == SOURCE_ID
    assert openings["opening_parent_equity"]["source_locator"] == _documents()[-1]["id"]
    valuation = generate_valuation("SYNTH-BANK", prepared_bundle=prepared["bundle"],
                                   output_dir=str(tmp_path))
    assert valuation["valuation_usability"]["usable"], valuation.get("acquisition_tasks")
    assert valuation["input_consumption"]["status"] == "complete"
    assert valuation["fair_value_base"] == pytest.approx(10.)


@pytest.mark.parametrize("child_ids", [[PARENT], [ISSUER], [BANK, BANK]])
def test_reporting_scope_does_not_permit_duplicate_or_self_subsidiaries(child_ids):
    bundle = prepare_sector_analysis("SYNTH-BANK", as_of=DAY, providers=providers_for("bank"))
    def propose(dossier, contract):
        plan = _propose(dossier, contract)
        plan["model"]["legal_structure"]["value"]["subsidiaries"] = [
            {"id": identity, "regime": "Fictional common-equity regime"} for identity in child_ids]
        return plan

    prepared = prepare_method_inputs(bundle, documents=_documents(), propose=propose)
    assert prepared["status"] == "incomplete"
    assert any(issue["code"] == "bank_entities" for issue in prepared["issues"])
    assert prepared["bundle"]["case"]["records"] == []


def test_same_issuer_parent_proof_cannot_substitute_consolidated_equity():
    bundle = prepare_sector_analysis("SYNTH-BANK", as_of=DAY, providers=providers_for("bank"))
    def propose(dossier, contract):
        plan = _same_issuer_proposal(dossier, contract)
        plan["model"]["opening_common_equity"] = plan["model"]["opening_parent_equity"]
        return plan

    prepared = prepare_method_inputs(bundle, documents=_documents(), propose=propose)
    assert prepared["status"] == "incomplete"
    assert any(issue["field"] == "opening_common_equity" and issue["code"] == "unverified_fact"
               for issue in prepared["issues"])
    assert prepared["bundle"]["case"]["records"] == []


@pytest.mark.parametrize("batch_size", [1, 6, 100])
def test_bank_continuing_ledger_receives_completed_entity_forecasts(batch_size, tmp_path):
    from bellomberg.valuation.preparation_ai import StagedProposer
    bundle = prepare_sector_analysis(ISSUER, as_of=DAY,
        providers=providers_for("bank"))

    def propose(dossier, contract):
        full = _propose(dossier, contract)
        spec = contract["preparation_stage"]
        scope, names = spec["scope"], spec["drivers"]
        source = full["model"] if scope == "model" else full["scenarios"][scope]
        values = {name: source[name] for name in names}
        closing = {"terminal_ledger", "capital.terminal_equity", "capital.terminal_debt"}.intersection(names)
        if closing:
            completed = dossier["completed_plan"]["scenarios"][scope]
            needed = {"capital.parent_cash_flows.debt_repaid", "capital.parent_cash_flows.debt_issued",
                      "capital.subsidiaries.0.gaap_net_income", "capital.subsidiaries.0.permitted_distribution",
                      "capital.subsidiaries.0.minimum_liquidity", "liquidity_bridge", "capital_constraints"}
            if not needed <= completed.keys():
                values.update({name: None for name in closing})
                return {"drivers": values, "rationale": "Continuing capital and liquidity require final entity balances first"}
        return {"drivers": values, "rationale": "Synthetic bank case with explicit completed entity forecasts"}

    result = prepare_method_inputs(bundle, documents=_documents(),
        propose=StagedProposer(propose, drivers_per_stage=batch_size))
    assert result["status"] == "prepared", result["issues"]
    valuation = generate_valuation(ISSUER, prepared_bundle=result["bundle"], output_dir=str(tmp_path))
    assert valuation["valuation_usability"]["usable"], valuation.get("acquisition_tasks")


@pytest.mark.parametrize("bad_shape", ["parent_alias", "extra_subsidiary_fields", "overlapping_dates"])
def test_bank_invalid_opening_shape_stops_before_forecast_and_is_not_repaired(bad_shape):
    from copy import deepcopy
    from bellomberg.valuation.preparation_ai import StagedProposer
    bundle = prepare_sector_analysis(ISSUER, as_of=DAY, providers=providers_for("bank"))
    answers, originals = [], []

    def propose(dossier, contract):
        assert not answers, "Invalid opening must not trigger a forecast request"
        full = _propose(dossier, contract)
        model = full["model"]
        legal = model["legal_structure"]["value"]
        if bad_shape == "parent_alias":
            legal["parent"] = legal.pop("parent_entity")
            legal["group"] = ISSUER
        elif bad_shape == "extra_subsidiary_fields":
            legal["subsidiaries"][0]["name"] = BANK
        else:
            calendar = model["calendar"]["value"]
            calendar["discount_convention"] = "ACT/365F"
            calendar["periods"][0]["start"] = calendar["valuation_date"]
        answer = {"drivers": model, "rationale": "Synthetic malformed bank opening"}
        answers.append(answer)
        originals.append(deepcopy(answer))
        return answer

    result = prepare_method_inputs(bundle, documents=_documents(), propose=StagedProposer(propose))
    assert result["status"] == "incomplete" and result["bundle"]["case"]["records"] == []
    assert len(answers) == 1 and answers == originals
    expected = "date non valide o non contigue" if bad_shape == "overlapping_dates" else "bank opening structure incomplete"
    assert expected in result["issues"][0]["reason"]


def test_bank_unsupported_distribution_policy_stops_before_next_paid_stage():
    from bellomberg.valuation.preparation_ai import StagedProposer
    bundle = prepare_sector_analysis(ISSUER, as_of=DAY, providers=providers_for('bank'))
    calls = []
    def propose(dossier, contract):
        assert not any('capital.distribution_policy' in prior for prior in calls)
        full = _propose(dossier, contract)
        stage = contract['preparation_stage']; scope = stage['scope']; names = stage['drivers']
        calls.append(names)
        values = full['model'] if scope == 'model' else full['scenarios'][scope]
        answer = {name: values[name] for name in names}
        if 'capital.distribution_policy' in names:
            answer['capital.distribution_policy']['value'] = 'Hold a fixed quarterly dividend'
        return {'drivers': answer, 'rationale': 'Synthetic unsupported payout policy'}
    result = prepare_method_inputs(bundle, documents=_documents(), propose=StagedProposer(propose, drivers_per_stage=12))
    assert result['status'] == 'incomplete'
    assert 'full_sweep_after_buffers' in result['issues'][0]['reason']
    assert len(calls) == 3


def test_bank_opening_capital_missing_quote_fails_closed():
    bundle = prepare_sector_analysis("SYNTH-BANK", as_of=DAY, providers=providers_for("bank"))
    def missing_opening_proof(dossier, contract):
        plan = _propose(dossier, contract)
        plan["scenarios"]["base"]["capital.subsidiaries.0.opening_statutory_capital"]["evidence_quote"] = (
            "Fictional nonexistent statutory balance: USD 90 million.")
        return plan

    prepared = prepare_method_inputs(bundle, documents=_documents(), propose=missing_opening_proof)
    assert prepared["status"] == "incomplete"
    assert any(issue["code"] == "unverified_fact" for issue in prepared["issues"])
    assert prepared["bundle"]["case"]["records"] == []
