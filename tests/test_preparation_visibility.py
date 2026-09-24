"""Synthetic opening evidence must have been visible to the paid stage."""
from copy import deepcopy

import pytest

from bellomberg.valuation.input_preparation import _quotation_proof
from bellomberg.valuation.preparation_ai import StagedProposer
from test_preparation_view import _dossier


def _quotation(source, price, date_quote, *, evidence_quote):
    value = {"financial_currency": "EUR", "quote_currency": "EUR", "quote_unit": "EUR",
             "quote_units_per_currency": 1., "financial_to_quote_rate": 1.,
             "shares_per_quote": 1., "share_class": "ordinary", "price": price,
             "price_as_of": "2025-12-31"}
    item = {"value": value, "facts": {
        "price": {"evidence_ids": ["quote"], "evidence_quote": evidence_quote,
                  "quoted_value": price, "quoted_unit": "EUR per share", "date_quote": date_quote},
        "shares_per_quote": {"evidence_ids": ["quote"],
                             "evidence_quote": "Ratio: 1 shares per quote",
                             "quoted_value": 1., "quoted_unit": "shares per quote"}}}
    return _quotation_proof(item, [{"id": "quote", "text": source}])


def test_literal_price_date_must_share_one_contiguous_excerpt():
    source = ("2025-12-31 Price: EUR 10 per share. "
              "2026-01-01 Price: EUR 20 per share. Ratio: 1 shares per quote")
    assert _quotation(source, 20., "2025-12-31", evidence_quote="Price: EUR 20 per share")
    assert _quotation(source, 10., "2025-12-31 Price: EUR 10 per share",
                      evidence_quote="Price: EUR 10 per share") is None


@pytest.mark.parametrize("nested", [
    {"evidence_ids": ["filing"]},
    {"facts": {"price": {"evidence_ids": ["filing"]}}},
    {"calculation": {"terms": [{"evidence_ids": ["filing"]}]}},
])
def test_model_stage_rejects_citation_to_text_omitted_from_prompt(nested):
    dossier = _dossier()
    contract = {"schema": {"driver": ("field", "EUR", "basis", "opening", "number", "model")},
                "scenarios": ["bear", "base", "bull"]}
    calls = []
    def propose(view, narrowed):
        calls.append(narrowed["preparation_stage"]["scope"])
        assert "text" not in view["documents"][0]
        driver = {"value": 7., "kind": "historical", "rationale": "Synthetic"}
        driver.update(deepcopy(nested))
        return {"drivers": {"driver": driver}, "rationale": "Synthetic opening"}

    with pytest.raises(ValueError, match="citazione.*non visibile"):
        StagedProposer(propose)(dossier, contract)
    assert calls == ["model"]  # no later stage can pay after a hidden citation


@pytest.mark.parametrize("field", ["evidence_quote", "period_quote", "date_quote", "valid_until_basis"])
def test_projected_document_id_cannot_launder_a_quote_from_hidden_text(field):
    from test_preparation_view import _bank_excerpt_case
    dossier, manifest = _bank_excerpt_case()
    dossier["documents"][0].pop("page_references")
    source = dossier["documents"][0]
    hidden = source["text"].splitlines()[1]
    calls = []
    contract = {"schema": {"driver": ("field", "EUR", "basis", "opening", "number", "model")},
                "scenarios": ["bear", "base", "bull"]}
    def propose(view, narrowed):
        calls.append(narrowed["preparation_stage"]["scope"])
        assert hidden not in view["documents"][0]["text"]
        return {"drivers": {"driver": {"value": 7., "kind": "historical", "rationale": "Synthetic",
            "evidence_ids": [source["id"]], field: hidden}}, "rationale": "Synthetic opening"}
    with pytest.raises(ValueError, match="citazione.*non visibile"):
        StagedProposer(propose, opening_excerpt_manifest=manifest)(dossier, contract)
    assert calls == ["model"]


def test_visible_citations_use_original_fragments_not_projection_markers():
    from bellomberg.valuation.preparation_ai import verify_visible_citations
    from bellomberg.valuation.preparation_view import select_stage_view
    from test_preparation_view import _bank_excerpt_case
    dossier, manifest = _bank_excerpt_case()
    view = select_stage_view(dossier, "model", excerpt_manifest=manifest)
    source = dossier["documents"][0]
    first = manifest[0]["excerpts"][0]
    quote = source["text"][first["char_start"]:first["char_end_exclusive"]]
    fact = {"evidence_ids": [source["id"]], "facts": {"nested": {"evidence_quote": quote}}}
    verify_visible_citations(fact, view, dossier)
    fact["facts"]["nested"]["evidence_quote"] = view["documents"][0]["text"].splitlines()[0]
    with pytest.raises(ValueError, match="citazione.*non visibile"):
        verify_visible_citations(fact, view, dossier)


def test_structured_pointer_stays_visible_but_missing_pointer_is_rejected():
    from bellomberg.valuation.preparation_ai import verify_visible_citations
    from bellomberg.valuation.preparation_view import select_stage_view
    dossier = _dossier()
    view = select_stage_view(dossier, "model")
    fact = {"evidence_ids": ["quote"], "evidence_pointer": {
        "value": "/facts/0/value", "unit": "/facts/0/unit", "period": "/facts/0/end"}}
    verify_visible_citations(fact, view, dossier)
    fact["evidence_pointer"]["value"] = "/facts/1/value"
    with pytest.raises(ValueError, match="pointer.*non visibile"):
        verify_visible_citations(fact, view, dossier)


@pytest.mark.parametrize("project_forecasts", [False, True])
def test_bank_excerpts_compile_against_original_catalog_and_forecast_selection_is_explicit(tmp_path, project_forecasts):
    from hashlib import sha256
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.dcf_engine import generate_valuation
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from test_sector_analysis import DAY, providers_for
    from test_input_preparation_bank import _documents, _propose

    documents = _documents()
    document = documents[0]
    statement = document["text"]
    prefix = "Unrelated synthetic front matter.\n"
    document["text"] = prefix + statement + "\nUnrelated synthetic back matter."
    document["sha256"] = sha256(document["text"].encode()).hexdigest()
    manifest = [{"source_id": document["id"], "original_text_sha256_utf8": document["sha256"],
        "excerpts": [{"theme": "synthetic_opening", "char_start": len(prefix),
            "char_end_exclusive": len(prefix) + len(statement),
            "excerpt_sha256_utf8": sha256(statement.encode()).hexdigest()}]}]
    parent_pdf = documents[1]
    manifest.append({"source_id": parent_pdf["id"], "original_text_sha256_utf8": parent_pdf["sha256"],
        "excerpts": [{"theme": "parent_schedule", "char_start": 0,
            "char_end_exclusive": len(parent_pdf["text"]),
            "excerpt_sha256_utf8": parent_pdf["sha256"]}]})
    seen = []
    def propose(view, contract):
        stage = contract["preparation_stage"]
        scope = stage["scope"]
        seen.append(scope)
        if scope == "model" or project_forecasts:
            assert prefix not in view["documents"][0]["text"]
            assert view["documents"][0]["sha256"] == document["sha256"]
        else:
            assert view["documents"][0]["text"] == document["text"]
        full = _propose(view, contract)
        values = full["model"] if scope == "model" else full["scenarios"][scope]
        return {"drivers": {name: values[name] for name in stage["drivers"]},
                "rationale": "Synthetic sourced bank stage"}
    bundle = prepare_sector_analysis("SYNTH-BANK", as_of=DAY, providers=providers_for("bank"))
    prepared = prepare_method_inputs(bundle, documents=documents,
        propose=StagedProposer(propose, opening_excerpt_manifest=manifest,
            forecast_excerpt_manifest=manifest if project_forecasts else None))
    assert prepared["status"] == "prepared", prepared["issues"]
    assert set(seen) == {"model", "bear", "base", "bull"}
    assert prepared["provenance"]["documents"][document["id"]]["sha256"] == document["sha256"]
    result = generate_valuation("SYNTH-BANK", prepared_bundle=prepared["bundle"], output_dir=str(tmp_path))
    assert result["valuation_usability"]["usable"], result.get("error")
    assert result["fair_value_base"] == pytest.approx(10.)
