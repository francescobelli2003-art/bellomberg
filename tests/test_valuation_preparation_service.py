"""Shared sources-to-workbook service; synthetic proposer, actual method engines."""
from pathlib import Path
import pytest

from test_input_preparation import _bundle, _documents, _propose_operating, DAY


def test_new_case_prepares_generates_and_preserves_source_provenance(tmp_path):
    from bellomberg.valuation.preparation_service import prepare_and_generate
    result = prepare_and_generate(_bundle(), documents=_documents(), propose=_propose_operating,
                                  output_dir=tmp_path)
    assert result["preparation"]["status"] == "prepared"
    assert result["preparation"]["provenance"]["origin"] == "automatic_non_approved"
    assert result["valuation_usability"]["usable"], result
    assert Path(result["path"]).is_file()
    assert result["snapshot_id"] != _bundle()["snapshot_id"]
    assert result["input_consumption"]["status"] == "complete"
    assert result["analysis_standard"]["actual_annual_periods"] == 10
    assert "preparation" in Path(result["path"]).with_suffix(".payload.json").read_text(encoding="utf-8")


def test_missing_source_is_incomplete_without_fake_workbook_or_paid_call(tmp_path):
    from bellomberg.valuation.preparation_service import prepare_and_generate
    calls = []
    result = prepare_and_generate(_bundle(), documents=[], propose=lambda *a: calls.append(a), output_dir=tmp_path)
    assert calls == []
    assert not result["valuation_usability"]["usable"]
    assert result["preparation"]["issues"]
    assert result.get("path") is None
    assert not list(tmp_path.glob("*.xlsx"))


def test_source_gaps_reach_proposer_and_persist_in_workbook_receipt(tmp_path):
    from bellomberg.valuation.preparation_service import prepare_and_generate
    report = {"status": "partial", "issues": [{"reason": "new filing unavailable"}],
              "coverage": {"reused": 1, "downloaded": 0}, "documents": _documents()}
    def propose(dossier, contract):
        assert dossier["document_acquisition"]["issues"] == report["issues"]
        return _propose_operating(dossier, contract)
    result = prepare_and_generate(_bundle(), documents=report["documents"], propose=propose,
                                  source_report=report, output_dir=tmp_path)
    import json
    receipt = json.loads(Path(result["path"]).with_suffix(".payload.json").read_text(encoding="utf-8"))
    assert receipt["preparation"]["provenance"]["source_acquisition"]["issues"] == report["issues"]


def test_unapproved_partial_plan_does_not_replace_original_acquisition(tmp_path):
    from bellomberg.valuation.preparation_service import prepare_and_generate
    original = _bundle()
    result = prepare_and_generate(original, documents=_documents(), propose=lambda *a: {"model": {}}, output_dir=tmp_path)
    assert result["snapshot_id"] == original["snapshot_id"]
    assert result["preparation"]["status"] == "incomplete"
    assert not result["valuation_usability"]["usable"]


def test_failed_preparation_does_not_publish_a_workbook_from_old_inputs(tmp_path):
    from bellomberg.valuation.preparation_service import prepare_and_generate
    from test_sector_operating_drivers import bundle_for
    result = prepare_and_generate(bundle_for(), documents=[], propose=lambda *a: None, output_dir=tmp_path)
    assert result.get("path") is None
    assert not list(tmp_path.glob("*.xlsx"))


def test_approved_archive_does_not_reacquire_documents_or_pay(tmp_path, monkeypatch):
    from bellomberg.valuation.preparation_service import collect_and_prepare
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from test_sector_analysis import providers_for
    from test_sector_operating_drivers import operating_records
    import pytest
    providers = providers_for("manufacturing")
    providers["method_inputs"] = lambda ticker, *, as_of: {
        "status": "ok", "source_id": "method_records_archive", "as_of": DAY,
        "data": {"sets": {"operating_fcff": {"set_id": 1, "method_version": "2",
            "records": operating_records(), "earliest_valid_until": "2027-01-01",
            "scenario_rationale": {s: "Existing reviewed scenario" for s in ("bear", "base", "bull")},
            "review": {"reviewer": "Synthetic reviewer", "reviewed_at": DAY}}}}, "records": []}
    bundle = prepare_sector_analysis("SYNTH-EXT", as_of=DAY, providers=providers)
    monkeypatch.setattr("bellomberg.valuation.valuation_sources.collect_documents",
                        lambda *a, **k: pytest.fail("unnecessary source acquisition"))
    result = collect_and_prepare(bundle, archive_root=tmp_path / "archive",
                                 propose=lambda *a: pytest.fail("approved set must not pay"), output_dir=tmp_path)
    assert result["preparation"]["status"] == "approved_reused"
    assert result["valuation_usability"]["usable"]


@pytest.mark.parametrize('business,method', [('manufacturing','operating_fcff'),('bank','bank_residual_income')])
def test_common_service_requests_market_references_in_financial_currency(tmp_path, monkeypatch, business, method):
    from bellomberg.valuation.preparation_service import collect_and_prepare
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from test_sector_analysis import providers_for
    providers = providers_for(business)
    original = providers['profile']
    def profile(*args, **kwargs):
        source = original(*args, **kwargs)
        source['data']['info'].update(financialCurrency='USD', currency='EUR')
        return source
    providers['profile'] = profile
    bundle = prepare_sector_analysis('SYNTH-EXT', as_of=DAY, providers=providers)
    calls = []
    def collect(ticker, **kwargs):
        calls.append(kwargs)
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': 'synthetic', 'reason': 'stop before AI'}]}
    monkeypatch.setattr('bellomberg.valuation.preparation_sources.collect_preparation_evidence', collect)
    result = collect_and_prepare(bundle, archive_root=tmp_path,
        propose=lambda *a: (_ for _ in ()).throw(AssertionError('source gap must stop before AI')))
    assert calls[0]['financial_currency'] == 'USD'
    assert calls[0]['method_id'] == method
    assert not result['valuation_usability']['usable']
