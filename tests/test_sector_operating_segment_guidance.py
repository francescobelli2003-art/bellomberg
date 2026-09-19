"""Synthetic segment revenue builds: evidence, reconciliation and unchanged FCFF.

All amounts are invented. Providers are injected; workbooks stay in pytest TEMP.
"""
from copy import deepcopy

import pytest
from openpyxl import load_workbook

from bellomberg.valuation import dcf_engine, operating_adapter, sector_analysis
from test_sector_operating_drivers import DAY, bundle_for, operating_records


PERIODS = [{"start": "2026-01-01", "end": "2026-12-31"},
           {"start": "2027-01-01", "end": "2027-12-31"}]
URL = "https://example.org/synthetic/segment-evidence"


def growth_evidence(kind="analyst_estimate", period=None):
    return {"kind": kind, "sources": [{"source_id": URL, "locator": "Synthetic table, row A"}],
            "guidance_period": deepcopy(period), "derivation": "Synthetic forecast derivation"}


def segment(segment_id, base, growth, role="segment"):
    return {"segment_id": segment_id, "label": "Synthetic " + segment_id, "role": role,
            "base": base, "growth": list(growth),
            "base_evidence": {"kind": "historical",
                              "sources": [{"source_id": URL, "locator": "Synthetic annual segment report"}],
                              "observation_period": {"start": "2025-01-01", "end": "2025-12-31"},
                              "derivation": "Synthetic reported annual revenue"},
            "growth_evidence": [growth_evidence() for _ in PERIODS]}


def segment_build():
    # Independent oracle: 66+44-10=100, then 72.6+37.4-10=100.
    return {"basis": "segment_guidance", "segments": [
        segment("A", 60., [.1, .1]), segment("B", 50., [-.12, 37.4 / 44. - 1]),
        segment("C", -10., [0., 0.], role="consolidation")]}


def segment_records(build=None):
    rows = operating_records()
    for row in rows:
        if row["driver"] == "revenue_build":
            row["value"] = deepcopy(segment_build() if build is None else build)
    return rows


def _row(rows, driver, scenario="base"):
    return next(row for row in rows if row["driver"] == driver and row["scenario"] == scenario)


def segment_bundle(rows, profile="manufacturing"):
    return sector_analysis.prepare_sector_analysis("SYNTH-SEG", as_of=DAY, providers={
        "profile": lambda *a, **k: {"status": "ok", "source_id": "synthetic", "as_of": DAY,
            "data": {"info": {}, "evidence": [
                {"field": field, "value": value, "source_id": "synthetic", "as_of": DAY}
                for field, value in (("instrument", "equity"), ("business_model", profile))]}},
        "method_inputs": lambda *a, **k: {"status": "ok", "source_id": "synthetic", "as_of": DAY,
                                           "records": deepcopy(rows)}},
        user_context={"analysis_context": {"scenario_rationale": {
            scenario: "Synthetic scenario with explicit segment evidence" for scenario in ("bear", "base", "bull")}}})


def calculate(rows, tmp_path, profile="manufacturing"):
    return dcf_engine.generate_valuation("SYNTH-SEG", prepared_bundle=segment_bundle(rows, profile),
                                         output_dir=str(tmp_path))


def assert_blocked(result, fragment):
    assert result["valuation_usability"]["usable"] is False
    assert result.get("fair_value_base") is None
    assert any(fragment in reason for reason in result["analytical_quality"]["issues"]), result["analytical_quality"]


def test_segment_build_same_total_same_value_and_workbook(tmp_path):
    result = calculate(segment_records(), tmp_path)
    assert result["valuation_usability"]["usable"], result["analytical_quality"]
    assert result["fair_value_base"] == pytest.approx(14.13)
    for case in result["calculation_details"]["scenarios"].values():
        assert case["rows"]["revenue"] == pytest.approx([100., 100.])
    workbook = load_workbook(result["path"], data_only=True)
    try:
        assert workbook["Valuation"]["B2"].value == pytest.approx(14.13)
    finally:
        workbook.close()
    proof = next(row for row in result["analytical_quality"]["rows"]
                 if row["driver"] == "revenue_build" and row["scenario"] == "base")
    assert proof["values"] == segment_build()


def test_company_guidance_on_exact_period_without_consolidation(tmp_path):
    build = {"basis": "segment_guidance", "segments": [
        segment("A", 60., [.1, .1]), segment("B", 40., [34. / 40. - 1, 27.4 / 34. - 1])]}
    for item in build["segments"]:
        item["growth_evidence"] = [growth_evidence("company_guidance", period) for period in PERIODS]
    rows = segment_records(build)
    for row in rows:
        if row["driver"] in ("revenue_build", "revenue_growth"):
            row["kind"] = "company_guidance"
    result = calculate(rows, tmp_path)
    assert result["valuation_usability"]["usable"], result["analytical_quality"]
    assert result["fair_value_base"] == pytest.approx(14.13)


def test_segment_order_is_not_an_economic_difference(tmp_path):
    rows = segment_records()
    _row(rows, "revenue_build", "bull")["value"]["segments"].reverse()
    result = calculate(rows, tmp_path)
    assert result["valuation_usability"]["usable"], result["analytical_quality"]


def test_explicit_consolidation_elimination_can_reach_zero_without_residual_proxy(tmp_path):
    build = segment_build()
    build['segments'][1]['growth'] = [34. / 50. - 1, 27.4 / 34. - 1]
    build['segments'][2]['growth'] = [-1., 0.]
    result = calculate(segment_records(build), tmp_path)
    assert result['valuation_usability']['usable'], result['analytical_quality']
    assert result['calculation_details']['scenarios']['base']['rows']['revenue'] == pytest.approx([100.,100.])
    assert result['fair_value_base'] == pytest.approx(14.13)


def test_july_june_years_use_derived_growth_without_calendar_defaults(tmp_path):
    rows = segment_records()
    periods = [{"start": "2026-07-01", "end": "2027-06-30"},
               {"start": "2027-07-01", "end": "2028-06-30"}]
    for row in rows:
        row["period"] = ("2026-06-30" if row["period"] == "2025-12-31"
                         else "|".join(p["start"] + "/" + p["end"] for p in periods))
        if row["driver"] == "calendar":
            row["value"].update(valuation_date="2026-06-30", periods=periods)
        elif row["driver"] == "quotation":
            row["value"]["price_as_of"] = "2026-06-30"
        elif row["driver"] == "revenue_build":
            for item in row["value"]["segments"]:
                item["base_evidence"]["observation_period"] = {"start": "2025-07-01", "end": "2026-06-30"}
                for cell, source_period in zip(item["growth_evidence"], PERIODS):
                    cell["guidance_period"] = source_period
    result = calculate(rows, tmp_path)
    assert result["valuation_usability"]["usable"], result["analytical_quality"]
    assert result["fair_value_base"] == pytest.approx(14.13)


# Each negative case asserts the specific missing control, not merely an unusable FV.
FAULTS = {
    "anonymous-residual": "campi esatti basis/segments",
    "segment-extra-field": "ogni voce richiede esattamente",
    "segments-not-list": "ogni voce richiede esattamente",
    "single-segment": "almeno due segment",
    "two-consolidations": "al massimo una consolidation",
    "duplicate-id": "segment_id o label",
    "empty-label": "segment_id o label",
    "bad-role": "almeno due segment",
    "zero-segment": "base finita richiesta",
    "zero-consolidation": "base finita richiesta",
    "bool-base": "base finita richiesta",
    "base-estimate": "base osservata richiesta",
    "base-wrong-date": "anno intero chiuso alla data valore",
    "base-short-year": "anno intero chiuso alla data valore",
    "empty-locator": "base osservata richiesta",
    "base-empty-derivation": "base osservata richiesta",
    "base-empty-sources": "base osservata richiesta",
    "growth-minus-one": "crescita finita > -100%",
    "growth-short": "crescita finita > -100%",
    "growth-bool": "crescita finita > -100%",
    "evidence-short": "evidenza di crescita richiesta per ogni periodo",
    "cell-proxy": "evidenza company_guidance o analyst_estimate",
    "cell-extra-field": "evidenza company_guidance o analyst_estimate",
    "cell-empty-derivation": "evidenza company_guidance o analyst_estimate",
    "cell-bad-url": "evidenza company_guidance o analyst_estimate",
    "cell-bad-period": "guidance_period start/end ISO oppure null",
    "guidance-wrong-period": "company_guidance solo se",
    "guidance-consolidation": "company_guidance solo se",
    "record-kind": "record revenue_build con celle derivate",
    "aggregate-kind": "record revenue_growth con celle derivate",
    "opening-reconciliation": "somma delle basi non riconciliata",
    "forecast-reconciliation": "segmenti non riconciliati ai ricavi nel periodo 1",
    "forecast-overflow": "ricavi non finiti nel periodo 0",
    "rounded-growth": "segmenti non riconciliati ai ricavi nel periodo 1",
    "scenario-opening": "stesse aperture dei segmenti in bear/base/bull",
    "scenario-evidence": "stesse aperture dei segmenti in bear/base/bull",
    "scenario-basis": "stesse aperture dei segmenti in bear/base/bull",
}


def faulty_records(case):
    rows = segment_records()
    record = _row(rows, "revenue_build")
    build = record["value"]
    first, second, consolidation = build["segments"]
    base = first["base_evidence"]
    cell = first["growth_evidence"][0]
    if case == "anonymous-residual": build["other_revenue"] = [0., 0.]
    elif case == "segment-extra-field": first["plug"] = 0.
    elif case == "segments-not-list": build["segments"] = {}
    elif case == "single-segment": build["segments"] = [segment("A", 100., [0., 0.])]
    elif case == "two-consolidations":
        build["segments"] = [segment("A", 60., [0., 0.]), segment("B", 60., [0., 0.]),
                             segment("C", -10., [0., 0.], "consolidation"), segment("D", -10., [0., 0.], "consolidation")]
    elif case == "duplicate-id": second["segment_id"] = "A"
    elif case == "empty-label": first["label"] = " "
    elif case == "bad-role": first["role"] = "unknown"
    elif case == "zero-segment": first["base"] = 0.
    elif case == "zero-consolidation": consolidation["base"] = 0.
    elif case == "bool-base": first["base"] = True
    elif case == "base-estimate": base["kind"] = "analyst_estimate"
    elif case == "base-wrong-date": base["observation_period"] = {"start": "2024-07-01", "end": "2025-06-30"}
    elif case == "base-short-year": base["observation_period"]["start"] = "2025-07-01"
    elif case == "empty-locator": base["sources"][0]["locator"] = " "
    elif case == "base-empty-derivation": base["derivation"] = " "
    elif case == "base-empty-sources": base["sources"] = []
    elif case == "growth-minus-one": first["growth"][0] = -1.
    elif case == "growth-short": first["growth"].pop()
    elif case == "growth-bool": first["growth"][0] = True
    elif case == "evidence-short": first["growth_evidence"].pop()
    elif case == "cell-proxy": cell["kind"] = "proxy"
    elif case == "cell-extra-field": cell["unconsumed"] = "value"
    elif case == "cell-empty-derivation": cell["derivation"] = " "
    elif case == "cell-bad-url": cell["sources"][0]["source_id"] = "report.pdf"
    elif case == "cell-bad-period": cell["guidance_period"] = {"start": "2027-01-01", "end": "2026-12-31"}
    elif case == "guidance-wrong-period": cell["kind"] = "company_guidance"
    elif case == "guidance-consolidation": consolidation["growth_evidence"][0] = growth_evidence("company_guidance", PERIODS[0])
    elif case == "record-kind": record["kind"] = "company_guidance"
    elif case == "aggregate-kind": _row(rows, "revenue_growth")["kind"] = "company_guidance"
    elif case == "opening-reconciliation": first.update(base=61., growth=[66. / 61. - 1, .1])
    elif case == "forecast-reconciliation": second["growth"][1] = -.14
    elif case == "forecast-overflow":
        first["growth"][0] = second["growth"][0] = 1e308
        _row(rows, "revenue_growth")["value"][0] = 1e308
    elif case == "rounded-growth":
        first["growth"][1] = .123456789
        second["growth"][1] = round((110. - 66. * (1 + .123456789)) / 44. - 1, 6)
    elif case == "scenario-opening":
        first.update(base=61., growth=[66. / 61. - 1, .1])
        second.update(base=49., growth=[44. / 49. - 1, 37.4 / 44. - 1])
    elif case == "scenario-evidence": base["derivation"] = "A different opening observation"
    elif case == "scenario-basis": record["value"] = deepcopy(_row(operating_records(), "revenue_build")["value"])
    else: raise AssertionError("Unknown synthetic case: " + case)
    return rows


@pytest.mark.parametrize("case", FAULTS)
def test_segment_fault_blocks_for_specific_reason(case, tmp_path):
    assert_blocked(calculate(faulty_records(case), tmp_path), FAULTS[case])


@pytest.mark.parametrize("profile", sorted(set(operating_adapter.REVENUE_BASES) - {"manufacturing"}))
def test_segment_basis_is_not_enabled_for_other_profiles(profile, tmp_path):
    assert_blocked(calculate(segment_records(), tmp_path, profile), "base economica o campi non compatibili col profilo")


@pytest.mark.parametrize("profile", sorted(operating_adapter.REVENUE_BASES))
def test_existing_profile_basis_keeps_its_value(profile, tmp_path):
    result = dcf_engine.generate_valuation("SYNTH-EXT", prepared_bundle=bundle_for(profile=profile), output_dir=str(tmp_path))
    assert result["valuation_usability"]["usable"], result["analytical_quality"]
    assert result["fair_value_base"] == pytest.approx(14.13)
