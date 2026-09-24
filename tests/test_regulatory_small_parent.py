"""Synthetic small-parent reporting; no real issuer amounts or approvals."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.input_preparation import _catalog, _day, _fact_proof
from bellomberg.valuation.regulatory_evidence import normalize_regulatory_pdf
from test_regulatory_evidence import _document as large_document


ENTITY = "Synthetic Small Parent"
DAY = "2026-06-30"


def _pages(document, pages):
    refs, offset = [], 0
    for number, text in enumerate(pages, 1):
        refs.append({"pagina": number, "inizio": offset, "fine": offset + len(text),
                     "sha256": sha256(text.encode()).hexdigest()})
        offset += len(text) + 1
    document["text"] = "\n".join(pages)
    document["sha256"] = sha256(document["text"].encode()).hexdigest()
    document["page_references"] = refs
    document["extraction_coverage"]["pages"] = len(refs)
    return document


def _document(entity=ENTITY, date="June 30, 2026"):
    document = large_document()
    raw = sha256(b"synthetic small parent PDF bytes").hexdigest()
    document.update(id=raw, document_sha256=raw)
    document["retrieval"]["document_sha256"] = raw
    return _pages(document, [
        "\n".join(("FR Y-9SP", "Board of Governors of the Federal Reserve System",
            "Parent Company Only Financial Statements for Small Holding Companies",
            "Date of Report: " + date, entity,
            "Printed Name of Chief Financial Officer (or Equivalent) (BHSP C490) Legal Title of Holding Company (RSSD 9017)")),
        "\n".join(("FR Y-9SP", "Schedule SC—Balance Sheet",
            "Dollar Amounts in Thousands BHSP Amount", "Assets",
            "a. Balances with subsidiary or affiliated depository institutions ........ 5993 8,000 1.a.",
            "b. Balances with unrelated depository institutions ........ 0010 2,000 1.b.",
            "Liabilities and Equity Capital",
            "a. Perpetual preferred stock (including related surplus) ........ 3283 2,000 16.a.",
            "f. Total equity capital (sum of items 16.a through 16.e) ........ 3210 12,000 16.f.")),
    ])


def _normalize(document, entity=ENTITY, day=DAY, form="FR Y-9SP"):
    return normalize_regulatory_pdf(document, expected_form=form,
                                   expected_entity=entity, expected_report_date=day)


def _mutate(document, old, new):
    pages = [document["text"][r["inizio"]:r["fine"]] for r in document["page_references"]]
    assert old in document["text"]
    return _pages(document, [p.replace(old, new) for p in pages])


def _bridge(document, driver):
    codes = {"opening_parent_equity": {"BHSP3210": 1, "BHSP3283": -1},
             "capital.parent_opening_cash": {"BHSP5993": 1, "BHSP0010": 1}}[driver]
    terms = []
    for i, fact in enumerate(json.loads(document["text"])["facts"]):
        if fact["concept"] in codes:
            terms.append({"coefficient": codes[fact["concept"]], "evidence_ids": [document["id"]],
                "quoted_value": fact["value"], "quoted_unit": fact["unit"],
                "evidence_pointer": {"value": f"/facts/{i}/value", "unit": f"/facts/{i}/unit", "period": f"/facts/{i}/end"}})
    return {"value": 10., "evidence_ids": [document["id"]], "calculation": {"operation": "sum", "terms": terms}}


def test_small_parent_observations_are_bound_to_original_pdf_and_exact_sc_fields():
    original = _document()
    before = deepcopy(original)
    result = _normalize(original)
    assert result["status"] == "ready", result["issues"]
    assert original == before
    doc = result["documents"][0]
    facts = json.loads(doc["text"])["facts"]
    assert [(r["taxonomy"], r["concept"], r["value"]) for r in facts] == [
        ("fr-y-9sp-sc", "BHSP5993", 8000), ("fr-y-9sp-sc", "BHSP0010", 2000),
        ("fr-y-9sp-sc", "BHSP3283", 2000), ("fr-y-9sp-sc", "BHSP3210", 12000)]
    assert {r["proof"]["line_item"] for r in facts} == {"1.a.", "1.b.", "16.a.", "16.f."}
    assert {r["proof"]["page"] for r in facts} == {2}
    for fact in facts:
        proof = fact["proof"]
        assert "FR Y-9SP" in proof["unit_policy_basis"]
        assert "Schedule SC" in proof["unit_policy_basis"]
        assert sha256(original["text"][proof["char_start"]:proof["char_end_exclusive"]].encode()).hexdigest() == proof["row_sha256_utf8"]
    catalog, issues, _ = _catalog([original, doc], _day("2026-09-10"))
    assert not issues and doc["id"] in catalog


@pytest.mark.parametrize("driver", ["opening_parent_equity", "capital.parent_opening_cash"])
def test_small_parent_common_equity_and_cash_compile_as_exact_signed_bridges(driver):
    result = _normalize(_document())
    assert result["status"] == "ready", result["issues"]
    doc = result["documents"][0]
    item = _bridge(doc, driver)
    assert _fact_proof(driver, item, [doc], "USD million", DAY, expected_entity=ENTITY) is None
    assert _fact_proof("capital.parent_opening_debt", item, [doc], "USD million", DAY, expected_entity=ENTITY)


@pytest.mark.parametrize("old,new", [
    ("0010 2,000 1.b.", "0010  1.b."), ("0010 2,000 1.b.", "0010 N/A 1.b."),
    ("0010 2,000 1.b.", "0010 2,000 5,000 1.b."), ("16.f.", "20.h."),
    ("f. Total equity capital", "f. Common equity capital"), ("BHSP Amount", "BHCP Amount"),
    ("Schedule SC", "Schedule PC"), ("FR Y-9SP", "FR Y-9LP"),
    ("June 30, 2026", "December 31, 2025"), (ENTITY, "Different Synthetic Parent"),
    ("3210 12,000 16.f.", "3210 12,000 16.f.\nf. Total equity capital (sum of items 16.a through 16.e) ........ 3210 12,000 16.f."),
])
def test_small_parent_refuses_ambiguous_or_mismatched_source(old, new):
    result = _normalize(_mutate(_document(), old, new))
    assert result["status"] == "incomplete" and not result["documents"]


def test_explicit_reported_zero_is_preserved_but_blank_is_not_zero():
    result = _normalize(_mutate(_document(), "0010 2,000 1.b.", "0010 0 1.b."))
    assert result["status"] == "ready"
    assert json.loads(result["documents"][0]["text"])["facts"][1]["value"] == 0


def _with_investments_and_borrowings():
    return _mutate(_document(), "Liabilities and Equity Capital", "\n".join((
        "a. Equity investment ........ 3239 91,000 4.a.",
        "a. Equity investment ........ 0088  5.a.",
        "Liabilities and Equity Capital",
        "a. Commercial paper ........ 2309 0 10.a.",
        "b. Other short-term borrowings ........ 2724 0 10.b.",
        "11. Long-term borrowings (includes limited-life preferred stock and related surplus) ........ 3151 7,900 11.",
        "a. Subsidiary bank(s) ........ 3605 0 14.a.",
        "b. Nonbank subsidiaries and related institutions2 ........ 3621  14.b.",
    )))


def test_optional_parent_balances_preserve_book_values_and_explicit_unavailability():
    original = _with_investments_and_borrowings()
    result = _normalize(original)
    assert result["status"] == "ready", result["issues"]
    doc = result["documents"][0]
    payload = json.loads(doc["text"])
    facts = {fact["concept"]: fact for fact in payload["facts"]}
    assert facts["BHSP3239"]["value"] == 91000
    assert facts["BHSP3151"]["value"] == 7900
    assert facts["BHSP2309"]["value"] == facts["BHSP2724"]["value"] == 0
    unavailable = payload["unavailable_fields"]
    assert unavailable["BHSP0088"] == unavailable["BHSP3621"] == "blank_amount"
    assert unavailable["BHSP0201"] == "field_not_reported"
    assert "BHSP0088" not in facts and "BHSP3621" not in facts
    _, issues, _ = _catalog([original, doc], _day("2026-09-10"))
    assert not issues
    index = next(i for i, fact in enumerate(payload["facts"]) if fact["concept"] == "BHSP3151")
    debt = {"value": 7.9, "evidence_ids": [doc["id"]], "quoted_value": 7900,
            "quoted_unit": "USD thousand", "evidence_pointer": {
                "value": f"/facts/{index}/value", "unit": f"/facts/{index}/unit", "period": f"/facts/{index}/end"}}
    # A reported book borrowing is not automatically the complete cash principal.
    assert _fact_proof("capital.parent_opening_debt", debt, [doc], "USD million", DAY, expected_entity=ENTITY)


@pytest.mark.parametrize("old,new", [
    ("3151 7,900 11.", "3151 N/A 11."),
    ("3151 7,900 11.", "3151 7,900 12."),
    ("a. Equity investment ........ 0088", "a. Other investment ........ 0088"),
    ("a. Commercial paper ........ 2309 0 10.a.", "a. Commercial paper ........ 2309 0 10.a.\na. Commercial paper ........ 2309 0 10.a."),
])
def test_optional_fields_still_reject_ambiguous_or_invalid_reported_data(old, new):
    result = _normalize(_mutate(_with_investments_and_borrowings(), old, new))
    assert result["status"] == "incomplete" and not result["documents"]


@pytest.mark.parametrize('old,new,code', [
    ('3239 91,000 4.a.', '3239 -91,000 4.a.', 'BHSP3239'),
    ('0088  5.a.', '0088 -100 5.a.', 'BHSP0088'),
])
def test_reported_negative_equity_investments_remain_negative(old, new, code):
    result = _normalize(_mutate(_with_investments_and_borrowings(), old, new))
    assert result['status'] == 'ready', result['issues']
    fact = next(f for f in json.loads(result['documents'][0]['text'])['facts'] if f['concept'] == code)
    assert fact['value'] < 0


def test_negative_borrowings_are_not_allowed_by_equity_investment_sign_rule():
    result = _normalize(_mutate(_with_investments_and_borrowings(), '3151 7,900 11.', '3151 -7,900 11.'))
    assert result['status'] == 'incomplete'


@pytest.mark.parametrize("mutation", ["mixed_family", "wrong_code", "wrong_sign", "wrong_period", "wrong_scope", "wrong_entity"])
def test_small_parent_proof_preserves_family_date_and_scope(mutation):
    result = _normalize(_document())
    assert result["status"] == "ready"
    doc = result["documents"][0]
    item = _bridge(doc, "opening_parent_equity")
    payload = json.loads(doc["text"])
    fact = next(f for f in payload["facts"] if f["concept"] == "BHSP3283")
    if mutation == "mixed_family": fact.update(taxonomy="fr-y-9lp-pc", concept="BHCP3283")
    elif mutation == "wrong_code": fact["concept"] = "BHSP3151"
    elif mutation == "wrong_sign":
        item["calculation"]["terms"][0]["coefficient"] = 1
        item["value"] = 14.
    elif mutation == "wrong_period": fact["end"] = "2025-12-31"
    elif mutation == "wrong_scope": fact["scope"] = "consolidated"
    else: fact["entity"] = "Synthetic Subsidiary"
    doc["text"] = json.dumps(payload)
    assert _fact_proof("opening_parent_equity", item, [doc], "USD million", DAY, expected_entity=ENTITY)


def test_small_parent_normalization_cannot_be_rehashed_to_invent_a_balance():
    original = _document()
    result = _normalize(original)
    assert result["status"] == "ready"
    doc = result["documents"][0]
    payload = json.loads(doc["text"])
    payload["facts"][0]["value"] += 1
    doc["text"] = json.dumps(payload)
    doc["sha256"] = sha256(doc["text"].encode()).hexdigest()
    catalog, issues, _ = _catalog([original, doc], _day("2026-09-10"))
    assert doc["id"] not in catalog
    assert any(i["code"] == "unverified_regulatory_normalization" for i in issues)


def test_small_parent_reaches_shared_bank_compiler_and_engine(tmp_path):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from bellomberg.valuation.dcf_engine import generate_valuation
    from test_input_preparation_bank import PARENT, _documents, _propose
    from test_sector_analysis import DAY as cutoff, providers_for

    original = _document(PARENT, "December 31, 2025")
    result = _normalize(original, entity=PARENT, day="2025-12-31")
    assert result["status"] == "ready"
    normalized = result["documents"][0]
    baseline_documents = _documents()

    def propose(dossier, contract):
        policy = contract["parent_regulatory_policy"]
        assert {r["form"] for r in policy["forms"]} == {"FR Y-9LP", "FR Y-9SP"}
        # Reuse only the synthetic economic plan; replace every parent proof
        # with SC operands from the actual catalog passed to this preparation.
        source = next(d for d in dossier["documents"] if d["id"] == normalized["id"])
        baseline = {**dossier, "documents": baseline_documents}
        plan = _propose(baseline, contract)
        targets = [(plan["model"], "opening_parent_equity")]
        targets += [(plan["scenarios"][s], "capital.parent_opening_cash") for s in contract["scenarios"]]
        for target, driver in targets:
            target[driver].update(_bridge(source, driver), rationale="Exact synthetic parent SC observed component bridge")
        return plan

    bundle = prepare_sector_analysis("SYNTH-BANK", as_of=cutoff, providers=providers_for("bank"))
    prepared = prepare_method_inputs(bundle, documents=[baseline_documents[0], original, normalized], propose=propose)
    assert prepared["status"] == "prepared", prepared["issues"]
    assert prepared["proposal"]["approval_status"] == "automatic_non_approved"
    parent_rows = [r for r in prepared["proposal"]["method_records"]
                   if r["driver"] in ("opening_parent_equity", "capital.parent_opening_cash")]
    assert len(parent_rows) == 4
    assert all(r["source_locator"] == normalized["id"] and r["entity"] == PARENT
               and r["period"] == "2025-12-31" and r["kind"] == "historical" for r in parent_rows)
    valuation = generate_valuation("SYNTH-BANK", prepared_bundle=prepared["bundle"], output_dir=str(tmp_path))
    assert valuation["valuation_usability"]["usable"], valuation.get("error")
    assert valuation["input_consumption"]["status"] == "complete"
    assert [valuation["fair_value_" + s] for s in ("bear", "base", "bull")] == pytest.approx([8., 10., 12.])
