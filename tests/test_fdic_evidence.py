"""Synthetic bank observations; no issuer data or prepared valuation records."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.fdic_evidence import normalize_fdic_financials
from bellomberg.valuation.input_preparation import _catalog, _day, _fact_proof


def _document():
    row = {"CERT": 12345, "RSSDID": 23456, "RSSDHCR": "34567", "NAME": "SYNTHETIC BANK",
           "NAMEHCR": "SYNTHETIC HOLDING", "REPDTE": "20260630", "EQ": 11000,
           "EQPP": 1000, "RBCT1C": 12000, "INSFDIC": 1, "IBA": 0, "CBLRIND": 0}
    text = json.dumps({"meta": {"total": 1}, "data": [{"data": row}], "totals": {"count": 1}})
    digest = sha256(text.encode()).hexdigest()
    url = "https://api.fdic.gov/banks/financials?filters=CERT:12345%20AND%20REPDTE:20260630"
    return {"id": digest, "url": url, "text": text, "sha256": digest,
            "document_sha256": digest, "published_at": None, "available_at": "2026-09-10",
            "availability_basis": "observed_download", "retrieval": {
                "url": url, "document_sha256": digest, "retrieved_at": "2026-09-10T10:00:00Z"}}


def _normalize(document):
    return normalize_fdic_financials(document, expected_cert=12345, expected_rssd=23456,
        expected_entity="SYNTHETIC BANK", expected_parent_rssd=34567, expected_report_date="2026-06-30")


def _rehash(document, payload):
    document["text"] = json.dumps(payload)
    digest = sha256(document["text"].encode()).hexdigest()
    document.update(id=digest, sha256=digest, document_sha256=digest)
    document["retrieval"]["document_sha256"] = digest


def _bridge(document, codes, value):
    terms = []
    for index, fact in enumerate(json.loads(document["text"])["facts"]):
        if fact["concept"] in codes:
            terms.append({"coefficient": codes[fact["concept"]], "evidence_ids": [document["id"]],
                "quoted_value": fact["value"], "quoted_unit": fact["unit"], "evidence_pointer": {
                    "value": f"/facts/{index}/value", "unit": f"/facts/{index}/unit", "period": f"/facts/{index}/end"}})
    if len(terms) == 1:
        return {"value": value, **{key: val for key, val in terms[0].items() if key != "coefficient"}}
    return {"value": value, "evidence_ids": [document["id"]],
            "calculation": {"operation": "sum", "terms": terms}}


def test_fdic_preserves_report_identity_raw_amounts_and_observed_availability():
    original = _document(); before = deepcopy(original)
    result = _normalize(original)
    assert result["status"] == "ready", result["issues"]
    assert original == before
    doc = result["documents"][0]
    assert doc["published_at"] is None and doc["available_at"] == "2026-09-10"
    facts = json.loads(doc["text"])["facts"]
    assert [(f["concept"], f["value"]) for f in facts] == [("EQ", 11000), ("EQPP", 1000), ("RBCT1C", 12000)]
    assert {f["unit"] for f in facts} == {"USD thousand"}
    assert {f["entity"] for f in facts} == {"SYNTHETIC BANK"}
    assert {f["scope"] for f in facts} == {"bank_institution"}
    assert all(f["proof"]["source_document_id"] == original["id"] for f in facts)
    catalog, issues, _ = _catalog([original, doc], _day("2026-09-10"))
    assert not issues and doc["id"] in catalog


def test_fdic_cash_is_optional_observation_never_inferred_from_equity_or_group_cash():
    original = _document(); payload = json.loads(original['text'])
    payload['data'][0]['data'].update(CHBAL=3500, CHBALI=2000)
    _rehash(original, payload)
    doc = _normalize(original)['documents'][0]
    facts = json.loads(doc['text'])['facts']
    assert next(f['value'] for f in facts if f['concept'] == 'CHBAL') == 3500
    item = _bridge(doc, {'CHBAL': 1}, 3.5)
    assert _fact_proof('liquidity_opening_cash', item, [doc], 'USD million', '2026-06-30',
        expected_entity='SYNTHETIC BANK') is None
    assert _fact_proof('liquidity_opening_cash', _bridge(doc, {'CHBALI': 1}, 2), [doc],
        'USD million', '2026-06-30', expected_entity='SYNTHETIC BANK')
    assert not any(f['concept'] == 'CHBAL' for f in json.loads(_normalize(_document())['documents'][0]['text'])['facts'])


@pytest.mark.parametrize("field,value", [("CERT", 999), ("RSSDID", 999), ("RSSDHCR", "999"),
    ("NAME", "OTHER BANK"), ("REPDTE", "20260331"), ("EQ", None), ("EQPP", ""),
    ("RBCT1C", True), ("RBCT1C", "12000"), ("EQ", float("nan")),
    ("CBLRIND", 1), ("CBLRIND", None), ("CBLRIND", False), ("IBA", 1), ("INSFDIC", 0)])
def test_fdic_rejects_identity_period_and_missing_or_invalid_amounts(field, value):
    original = _document(); payload = json.loads(original["text"])
    payload["data"][0]["data"][field] = value; _rehash(original, payload)
    result = _normalize(original)
    assert result["status"] == "incomplete" and result["documents"] == []


@pytest.mark.parametrize("fault", ["count", "duplicate", "missing", "host", "hash", "future", "publication"])
def test_fdic_rejects_ambiguous_unattested_or_misdated_source(fault):
    original = _document(); payload = json.loads(original["text"])
    if fault == "count": payload["meta"]["total"] = 2
    if fault == "duplicate": payload["data"].append(deepcopy(payload["data"][0]))
    if fault == "missing": del payload["data"][0]["data"]["EQPP"]
    _rehash(original, payload)
    if fault == "host": original["url"] = original["url"].replace("api.fdic.gov", "example.org")
    if fault == "hash": original["document_sha256"] = "0" * 64
    if fault == "future": original["available_at"] = "2026-03-30"
    if fault == "publication": original.update(published_at="2026-08-01", availability_basis="publication", available_at="2026-08-01")
    assert _normalize(original)["status"] == "incomplete"


def test_fdic_catalog_recompiles_source_and_rejects_rehashed_invention():
    original = _document(); doc = _normalize(original)["documents"][0]
    payload = json.loads(doc["text"]); payload["facts"][0]["value"] += 1
    doc["text"] = json.dumps(payload); doc["sha256"] = sha256(doc["text"].encode()).hexdigest()
    _, issues, _ = _catalog([original, doc], _day("2026-09-10"))
    assert any(issue["code"] == "unverified_regulatory_normalization" for issue in issues)


def test_fdic_normalization_requires_original_and_refuses_duplicate_json_properties():
    original = _document(); doc = _normalize(original)["documents"][0]
    _, issues, _ = _catalog([doc], _day("2026-09-10"))
    assert any(issue["code"] == "unverified_regulatory_normalization" for issue in issues)
    original["text"] = original["text"].replace('"EQ": 11000', '"EQ": 9000, "EQ": 11000')
    digest = sha256(original["text"].encode()).hexdigest()
    original.update(id=digest, sha256=digest, document_sha256=digest)
    original["retrieval"]["document_sha256"] = digest
    assert _normalize(original)["status"] == "incomplete"


@pytest.mark.parametrize("suffix,codes,value", [
    ("opening_gaap_equity", {"EQ": 1, "EQPP": -1}, 10.),
    ("opening_statutory_capital", {"RBCT1C": 1}, 12.),
    ("opening_gaap_to_statutory_equity", {"RBCT1C": 1, "EQ": -1, "EQPP": 1}, 2.)])
def test_fdic_common_equity_bridges_require_exact_bank_scope_and_components(suffix, codes, value):
    doc = _normalize(_document())["documents"][0]
    item = _bridge(doc, codes, value); driver = "capital.subsidiaries.0." + suffix
    assert _fact_proof(driver, item, [doc], "USD million", "2026-06-30", expected_entity="SYNTHETIC BANK") is None
    assert _fact_proof(driver, item, [doc], "USD million", "2026-06-30", expected_entity="SYNTHETIC HOLDING")
    assert _fact_proof("opening_common_equity", item, [doc], "USD million", "2026-06-30", expected_entity="SYNTHETIC BANK")
    if "calculation" in item:
        item["calculation"]["terms"][0]["coefficient"] *= -1
    else:
        item = _bridge(doc, {"EQ": 1}, 11.)
    assert _fact_proof(driver, item, [doc], "USD million", "2026-06-30", expected_entity="SYNTHETIC BANK")


def test_fdic_observations_reach_existing_bank_preparer_and_engine(tmp_path):
    from bellomberg.valuation.dcf_engine import generate_valuation
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from test_input_preparation_bank import BANK, _documents, _propose, _estimate
    from test_sector_analysis import DAY, providers_for
    original = _document(); payload = json.loads(original["text"])
    payload["data"][0]["data"].update(NAME=BANK, REPDTE="20251231", EQ=90000, EQPP=0, RBCT1C=90000)
    original["url"] = original["url"].replace("20260630", "20251231")
    original["retrieval"]["url"] = original["url"]
    _rehash(original, payload)
    doc = normalize_fdic_financials(original, expected_cert=12345, expected_rssd=23456,
        expected_parent_rssd=34567, expected_entity=BANK, expected_report_date="2025-12-31")["documents"][0]

    def propose(dossier, contract):
        assert contract["bank_fdic_policy"]["taxonomy"] == "fdic-ris"
        plan = _propose(dossier, contract)
        for scenario in plan["scenarios"].values():
            for suffix, codes, value in (
                ("opening_gaap_equity", {"EQ": 1, "EQPP": -1}, 90.),
                ("opening_statutory_capital", {"RBCT1C": 1}, 90.),
                ("opening_gaap_to_statutory_equity", {"RBCT1C": 1, "EQ": -1, "EQPP": 1}, 0.),
            ):
                item = _estimate(value, "Synthetic bank-specific FDIC opening evidence")
                item.update(kind="historical", **_bridge(doc, codes, value))
                scenario["capital.subsidiaries.0." + suffix] = item
        return plan

    bundle = prepare_sector_analysis("SYNTH-BANK", as_of=DAY, providers=providers_for("bank"))
    prepared = prepare_method_inputs(bundle, documents=[*_documents(), original, doc], propose=propose)
    assert prepared["status"] == "prepared", prepared["issues"]
    result = generate_valuation("SYNTH-BANK", prepared_bundle=prepared["bundle"], output_dir=str(tmp_path))
    assert result["valuation_usability"]["usable"], result.get("acquisition_tasks")
    assert result["input_consumption"]["status"] == "complete"
    assert result["fair_value_base"] == pytest.approx(10.)
