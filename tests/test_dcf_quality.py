"""Contract tests: evidence cannot turn mechanical scenarios into analyst work."""
from copy import deepcopy
from datetime import date

import pytest


DRIVERS = ("revenue_growth", "gross_margin", "rnd_pct", "sga_pct", "capdev_pct",
           "nwc_pct", "capex_pct", "tax_rate", "da_tan_pct")
SCENARIOS = ("bear", "base", "bull")
TODAY = date(2026, 9, 10)


def inputs():
    spec = {"ticker": "SYNTH.TEST", "currency": "USD", "wacc_inputs": {"wacc": .1, "rf": .04},
            "terminal_g": .02, "shares_m": 10, "net_debt": 5,
            "ronic_anchor": {"value": .16, "source": "Company capital returns scenario"}}
    scenarios = {s: {d: [.2 if d == "revenue_growth" else .4 if d == "gross_margin" else .01] * 5
                     for d in DRIVERS} for s in SCENARIOS}
    evidence = {"kind": "analyst_estimate", "source": "https://example.org/filing",
                "source_date": "2026-08-01", "valid_until": "2026-12-31", "metric": "driver",
                "basis": "consolidated reported revenue denominator", "rationale": "Explicit analyst forecast"}
    records = [dict(evidence, scenario=s, driver=d) for s in SCENARIOS for d in DRIVERS]
    records += [dict(evidence, scenario="model", driver=d) for d in
                ("last_revenue", "nwc0", "wacc", "terminal_g", "shares_m", "net_debt", "ronic")]
    context = {"as_of": TODAY.isoformat(), "forecast_years": [2026, 2027, 2028, 2029, 2030],
               "scenario_rationale": {s: "Distinct operating case explained" for s in SCENARIOS},
               "assumptions": records, "revisions": []}
    return spec, deepcopy(scenarios), scenarios, context


def compute(spec, scenarios, last_rev, nwc0=None):
    # Analytic function with an interaction: sequential driver attribution matters.
    g, m = scenarios["base"]["revenue_growth"][0], scenarios["base"]["gross_margin"][0]
    return {"fair_value_weighted": 100 * g + 200 * m + 1000 * g * m}


def assess(args=None, previous=None, calculator=compute):
    try:
        from bellomberg.valuation.dcf_quality import assess_quality
    except ImportError:
        pytest.fail("Missing analytical quality contract")
    spec, raw, resolved, context = args or inputs()
    return assess_quality(spec, raw, resolved, context, previous, last_rev=100,
                          nwc0=4, compute_fv=calculator, today=TODAY)


def prior_and_revision(args):
    snapshot = assess(args)["snapshot"]
    snapshot["as_of"] = "2026-08-02"
    for s in SCENARIOS:
        snapshot["scenarios"][s]["revenue_growth"] = [.1] * 5
        snapshot["scenarios"][s]["gross_margin"] = [.3] * 5
    args[3]["revisions"] = [{"metric": "operating outlook", "period": "FY2026", "basis": "reported consolidated",
                              "unit": "fraction", "previous_value": .1, "current_value": .2,
                              "value_type": "point", "previous_source": "https://example.org/previous",
                              "previous_date": "2026-08-01", "source": "https://example.org/current",
                              "source_date": "2026-09-01", "drivers": ["revenue_growth", "gross_margin"],
                              "rationale": "New evidence translated into the two forecast paths"}]
    return snapshot


def test_complete_evidence_is_documented_but_does_not_certify_economics():
    result = assess()
    assert result["status"] == "DOCUMENTATA"
    assert not result["issues"]
    assert "non certifica" in result["note"].lower()
    assert len(result["rows"]) == 34
    assert result["revision_bridge"]["status"] == "n.d."


def test_generated_scenarios_stay_draft_even_with_evidence_records():
    args = list(inputs()); args[1] = None
    assert assess(args)["status"] == "BOZZA_AUTOMATICA"


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "0.2", .2, [.2] * 4])
def test_non_numeric_or_incomplete_raw_paths_cannot_be_documented(bad):
    args = inputs(); args[1]["base"]["revenue_growth"] = bad
    result = assess(args)
    assert result["status"] == "INCOMPLETA"
    assert next(r for r in result["rows"] if r["scenario"] == "base" and r["driver"] == "revenue_growth")["issues"]


def test_resolved_path_must_match_the_path_that_evidence_describes():
    args = inputs(); args[2]["base"]["gross_margin"][1] = .1
    assert assess(args)["status"] == "INCOMPLETA"


@pytest.mark.parametrize("field,value", [("source_date", "2026-09-11"), ("valid_until", "2026-09-09"),
                                        ("basis", ""), ("source", "not a URL"), ("kind", "magic"),
                                        ("source_date", "20260801")])
def test_missing_stale_or_invalid_documentation_is_visible(field, value):
    args = inputs(); args[3]["assumptions"][0][field] = value
    result = assess(args)
    assert result["status"] == "INCOMPLETA"
    assert result["rows"][0]["issues"]


def test_model_anchors_require_evidence_and_real_numeric_values():
    args = inputs(); args[3]["assumptions"] = [r for r in args[3]["assumptions"] if r["scenario"] != "model"]
    args[0]["net_debt"] = None
    result = assess(args)
    assert result["status"] == "INCOMPLETA"
    assert all(r["issues"] for r in result["rows"] if r["scenario"] == "model")


def test_duplicate_and_malformed_evidence_is_not_silently_discarded():
    args = inputs(); args[3]["assumptions"] += [deepcopy(args[3]["assumptions"][0]), None]
    assert assess(args)["status"] == "INCOMPLETA"


@pytest.mark.parametrize("years", [None, [2026, 2027, 2029, 2030, 2031], [True, 2, 3, 4, 5]])
def test_forecast_years_must_be_explicit_contiguous_integers(years):
    args = inputs(); args[3]["forecast_years"] = years
    result = assess(args)
    assert result["status"] == "INCOMPLETA"
    assert result["snapshot"]["forecast_years"] is None


def test_counterfactual_bridge_reconciles_in_fixed_order_without_mutating_inputs():
    args = inputs(); previous = prior_and_revision(args); before = deepcopy((args, previous))
    result = assess(args, previous)
    bridge = result["revision_bridge"]
    assert bridge["status"] == "CALCOLATO"
    assert bridge["previous_rebased"] == pytest.approx(100)
    assert bridge["current"] == pytest.approx(180)
    assert bridge["delta"] == pytest.approx(80)
    assert [r["driver"] for r in bridge["steps"]] == ["revenue_growth", "gross_margin"]
    assert [r["delta"] for r in bridge["steps"]] == pytest.approx([40, 40])
    assert (args, previous) == before
    assert "correnti" in bridge["note"] and "storico" in bridge["note"]


@pytest.mark.parametrize("field,value", [("ticker", "OTHER.TEST"), ("currency", "EUR"),
                                        ("forecast_years", [2027, 2028, 2029, 2030, 2031]),
                                        ("as_of", "2026-10-01"), ("version", 2)])
def test_incompatible_snapshot_never_produces_a_numeric_bridge(field, value):
    args = inputs(); previous = prior_and_revision(args); previous[field] = value
    bridge = assess(args, previous)["revision_bridge"]
    assert bridge["status"] == "n.d."
    assert "delta" not in bridge


def test_missing_revision_mapping_preserves_floor_and_does_not_fabricate_fcff_effect():
    args = inputs(); previous = prior_and_revision(args)
    args[3]["revisions"][0].update(metric="Adjusted EPS", drivers=[], value_type="minimum", previous_value=5, current_value=5.25)
    result = assess(args, previous)
    assert result["revision_bridge"]["status"] == "n.d."
    assert result["revision_rows"][0]["value_type"] == "minimum"
    assert result["revision_rows"][0]["issues"]


def test_revision_date_order_and_unknown_driver_are_not_accepted():
    args = inputs(); previous = prior_and_revision(args)
    args[3]["revisions"][0].update(previous_date="2026-09-05", source_date="2026-09-01", drivers=["made_up"])
    assert assess(args, previous)["revision_bridge"]["status"] == "n.d."


def test_same_snapshot_computes_real_zero_only_when_identity_is_available():
    args = inputs(); previous = assess(args)["snapshot"]; previous["as_of"] = "2026-08-01"
    assert assess(args, previous)["revision_bridge"]["delta"] == 0


@pytest.mark.parametrize("outcome", [None, {"fair_value_weighted": float("nan")}, {"fair_value_weighted": True}])
def test_missing_or_nonfinite_calculation_is_declared(outcome):
    args = inputs(); previous = prior_and_revision(args)
    assert assess(args, previous, lambda *a, **k: outcome)["revision_bridge"]["status"] == "n.d."


def test_malformed_context_returns_visible_gaps_instead_of_crashing():
    args = list(inputs()); args[3] = ["malformed"]
    assert assess(args)["status"] == "INCOMPLETA"


def test_context_years_cannot_relabel_the_actual_workbook_horizon():
    args = inputs(); args[0]["_forecast_years"] = [2027, 2028, 2029, 2030, 2031]
    result = assess(args)
    assert result["status"] == "INCOMPLETA"
    assert result["snapshot"]["forecast_years"] is None


def test_proxy_remains_incomplete_even_when_fully_documented():
    args = inputs(); args[3]["assumptions"][0]["kind"] = "proxy"
    assert assess(args)["status"] == "INCOMPLETA"


def test_missing_current_anchor_prevents_engine_defaults_from_fabricating_bridge():
    args = inputs(); previous = prior_and_revision(args); args[0]["wacc_inputs"].pop("wacc")
    assert assess(args, previous)["revision_bridge"]["status"] == "n.d."


def test_zero_capm_wacc_uses_actual_explicit_spec_wacc_not_zero():
    args = inputs(); args[0]["wacc_inputs"]["wacc"] = 0; args[0]["wacc"] = .12
    row = next(r for r in assess(args)["rows"] if r["scenario"] == "model" and r["driver"] == "wacc")
    assert row["values"] == .12


def test_nonfinite_previous_snapshot_cannot_enter_calculator():
    args = inputs(); previous = prior_and_revision(args)
    previous["scenarios"]["base"]["gross_margin"][1] = float("nan")
    assert assess(args, previous)["revision_bridge"]["status"] == "n.d."


def test_engine_error_is_visible_and_preserves_snapshot():
    args = inputs(); previous = prior_and_revision(args)
    def broken(*args, **kwargs):
        raise RuntimeError("missing market anchor")
    result = assess(args, previous, broken)
    assert result["revision_bridge"]["status"] == "n.d."
    assert "RuntimeError" in result["revision_bridge"]["reason"]
    assert result["snapshot"]["ticker"] == "SYNTH.TEST"


@pytest.mark.parametrize("risk_free", [None, 0, True, float("nan")])
def test_missing_real_terminal_cap_cannot_be_documented_as_the_applied_g(risk_free):
    args = inputs(); args[0]["wacc_inputs"]["rf"] = risk_free; args[0]["terminal_g"] = .05
    result = assess(args)
    row = next(r for r in result["rows"] if r["scenario"] == "model" and r["driver"] == "terminal_g")
    assert row["values"] is None
    assert row["issues"]


def test_bad_numbers_in_report_are_portable_json_and_keep_their_gap():
    import json
    args = inputs(); args[0]["net_debt"] = float("nan")
    args[3]["assumptions"][0]["source_locator"] = float("inf")
    result = assess(args)
    assert result["status"] == "INCOMPLETA"
    json.dumps(result, allow_nan=False)


def controls(**overrides):
    return dict({"wacc_used": .11, "terminal_g_used": .015, "ronic_used": .14,
                 "terminal_method": "FCFF disciplinato", "terminal_warnings": [],
                 "mid_year": False, "equity_adjustments_used": [], "equity_adjustments_total": 0}, **overrides)


def test_report_uses_actual_model_controls_instead_of_precap_spec():
    args = inputs(); args[0]["_valuation_controls"] = controls(); before = deepcopy(args)
    result = assess(args)
    model = {r["driver"]: r for r in result["rows"] if r["scenario"] == "model"}
    assert [model[k]["values"] for k in ("wacc", "terminal_g", "ronic")] == [.11, .015, .14]
    assert result["model_controls"]["mid_year"] is False
    assert result["model_controls"]["terminal_method"] == "FCFF disciplinato"
    assert args == before


@pytest.mark.parametrize("anchor", [None, {"value": .1}, {"value": .1, "source": "PROXY RONIC=WACC"}])
def test_numeric_ronic_is_not_documented_when_its_actual_anchor_is_missing_or_proxy(anchor):
    args = inputs(); args[0]["ronic_anchor"] = anchor; args[0]["_valuation_controls"] = controls()
    result = assess(args)
    assert result["status"] == "INCOMPLETA"
    assert next(r for r in result["rows"] if r["driver"] == "ronic")["issues"]


@pytest.mark.parametrize("control", [
    {"terminal_method": "Gordon su UFCF Y5 (disciplined_terminal KO: missing)"},
    {"terminal_warnings": ["ROIC missing: RONIC=WACC"]},
    {"wacc_delta_bp_used": {"bear": 50, "bull": -20}},
    {"equity_adjustments_used": [{"label": "pension", "value_m": -5}], "equity_adjustments_total": -5},
    {"method_weights_used": {"dcf": .7, "comps": .3}},
])
def test_uncovered_controls_and_terminal_fallback_prevent_documented_status(control):
    args = inputs(); args[0]["_valuation_controls"] = controls(**control)
    result = assess(args)
    assert result["status"] == "INCOMPLETA"
    assert result["issues"]


def test_default_controls_are_disclosed_without_claiming_extra_evidence():
    args = inputs(); args[0]["_valuation_controls"] = controls(
        wacc_delta_bp_used={"bear": 0, "bull": 0}, method_weights_used={"dcf": 1, "comps": 0})
    result = assess(args)
    assert result["status"] == "DOCUMENTATA"
    assert "convenzione" in result["model_controls"]["note"].lower()


@pytest.mark.parametrize("period", ["FY2019", "FY2031", "Q1-2026", "2026", "later"])
def test_out_of_horizon_or_non_fy_revision_cannot_authorize_a_driver_change(period):
    args = inputs(); previous = prior_and_revision(args); args[3]["revisions"][0]["period"] = period
    assert assess(args, previous)["revision_bridge"]["status"] == "n.d."


def test_real_calculator_fallback_ronic_is_visible_with_complete_supplied_evidence():
    from bellomberg.valuation.dcf_buyside_v3 import compute_fair_values_v3
    args = inputs(); args[0]["ronic_anchor"] = {}
    fv = compute_fair_values_v3(args[0], args[2], 100, nwc0=4)
    args[0]["_valuation_controls"] = {k: fv[k] for k in controls() if k in fv}
    assert fv["ronic_used"] == .1  # actual fallback is the current WACC
    result = assess(args, calculator=compute_fair_values_v3)
    assert result["status"] == "INCOMPLETA"
    assert result["model_controls"]["ronic_used"] == .1


@pytest.mark.parametrize("defect", ["ko", "source", "warning"])
def test_real_terminal_controls_cannot_be_overruled_by_complete_documentation(monkeypatch, defect):
    from bellomberg.market_data import damodaran_wacc
    from bellomberg.valuation.dcf_buyside_v3 import compute_fair_values_v3
    args = inputs()
    if defect == "ko":
        def broken(*args, **kwargs):
            raise ValueError("terminal anchor failure")
        monkeypatch.setattr(damodaran_wacc, "disciplined_terminal", broken)
    elif defect == "source":
        args[0]["ronic_anchor"] = {"value": .14}
    # The complete fixture's .16 anchor triggers the existing convergence warning.
    fv = compute_fair_values_v3(args[0], args[2], 100, nwc0=4)
    args[0]["_valuation_controls"] = {k: fv[k] for k in controls() if k in fv}
    result = assess(args, calculator=compute_fair_values_v3)
    assert result["status"] == "INCOMPLETA"
    if defect == "ko":
        assert "KO" in result["model_controls"]["terminal_method"]
    elif defect == "warning":
        assert result["model_controls"]["terminal_warnings"]
    else:
        assert next(r for r in result["rows"] if r["driver"] == "ronic")["issues"]


def test_scenario_rationale_survives_for_the_workbook_without_aliasing_context():
    args = inputs()
    result = assess(args)
    assert result["scenario_rationale"] == args[3]["scenario_rationale"]
    result["scenario_rationale"]["base"] = "different"
    assert args[3]["scenario_rationale"]["base"] == "Distinct operating case explained"
