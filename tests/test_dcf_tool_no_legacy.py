"""The public DCF tool must preserve the validated engine's contract."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _engines(monkeypatch, outcome):
    from bellomberg.agents import agent_tools
    calls = []

    def modern(ticker, **kwargs):
        calls.append((ticker, kwargs))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def legacy(*args, **kwargs):
        pytest.fail("The unvalidated legacy builder must never be invoked")

    monkeypatch.setitem(sys.modules, 'bellomberg.valuation.dcf_engine', SimpleNamespace(generate_valuation=modern))
    monkeypatch.setitem(sys.modules, 'bellomberg.valuation.dcf_modeler', SimpleNamespace(build_dcf_excel=legacy))
    return agent_tools, calls


@pytest.mark.parametrize("outcome", [RuntimeError("synthetic engine failure"), {}, None,
    {"ok": True}, {"ok": False, "error": "validation failed", "path": "partial.xlsx"}])
def test_engine_failure_never_degrades_to_legacy(monkeypatch, outcome):
    module, calls = _engines(monkeypatch, outcome)
    result = module.tool_build_dcf_model("SYNTH")
    assert result.get("error")
    assert not result.get("path")
    assert len(calls) == 1


def test_terminal_growth_is_forwarded_and_model_metadata_preserved(monkeypatch):
    module, calls = _engines(monkeypatch, {
        "ok": True, "path": "synthetic.xlsx", "engine": "operating",
        "damodaran_wacc": {"wacc_pct": 0.0, "wacc": 0.10},
        "values_baked": False, "bake_error": "synthetic bake error",
        "sanity": {"severity": "WARN"}, "exclude_from_action_table": True,
        "fx_conversion": {"status": "missing"},
    })
    result = module.tool_build_dcf_model("SYNTH", perpetual_growth=0.02)
    assert calls == [("SYNTH", {"output_dir": str(Path(__file__).resolve().parents[1] / "models"), "terminal_growth": 0.02})]
    assert result["path"] == "synthetic.xlsx"
    assert result["wacc_pct"] == 0.0
    assert result["values_baked"] is False
    assert result["bake_error"] == "synthetic bake error"
    assert result["sanity"] == {"severity": "WARN"}
    assert result["exclude_from_action_table"] is True
    assert result["fx_conversion"] == {"status": "missing"}


def test_decimal_wacc_is_reported_as_percentage(monkeypatch):
    module, _ = _engines(monkeypatch, {
        "ok": True, "path": "synthetic.xlsx", "damodaran_wacc": {"wacc": 0.08}})
    assert module.tool_build_dcf_model("SYNTH")["wacc_pct"] == 8.0


def test_etf_is_explicit_without_workbook(monkeypatch):
    module, _ = _engines(monkeypatch, {"engine": "etf_passive", "subsector": "etf"})
    result = module.tool_build_dcf_model("SYNTH")
    assert result["engine"] == "etf_passive" and result["path"] is None
    assert result["note"] and not result.get("error")


@pytest.mark.parametrize("kwargs", [{"wacc": 0.08}, {"horizon_years": 10}])
def test_unsupported_legacy_override_is_declared_before_engine_call(monkeypatch, kwargs):
    module, calls = _engines(monkeypatch, {"ok": True, "path": "synthetic.xlsx"})
    result = module.tool_build_dcf_model("SYNTH", **kwargs)
    assert result.get("error")
    assert calls == []


def test_schema_does_not_promise_ignored_overrides(monkeypatch):
    module, _ = _engines(monkeypatch, {})
    schema = next(s for s in module.TOOLS_SCHEMA if s["name"] == "build_dcf_model")
    assert set(schema["input_schema"]["properties"]) == {"ticker", "perpetual_growth"}
    assert "6-sheet" not in schema["description"]
