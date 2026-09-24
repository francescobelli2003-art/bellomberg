"""Explicit local authorization, shared journal and revocation; no network."""
from copy import deepcopy
import json

import pytest


def _policy(**changes):
    return {"version": 1, "enabled": True,
            "authorization_id": "f80ae03c-2b3a-4fb1-8517-8ac2f6361000",
            "authorized_usd": "2.50", "triggers": ["committee", "portfolio"], **changes}


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _runtime(tmp_path, factory):
    from bellomberg.valuation.preparation_runtime import PreparationRuntime
    return PreparationRuntime(tmp_path / "policy.json", data_root=tmp_path / "data",
        archive_root=tmp_path / "sources", output_dir=tmp_path / "models", proposer_factory=factory)


def test_fresh_install_is_disabled_without_creating_files_or_proposer(tmp_path):
    runtime = _runtime(tmp_path, lambda *_a, **_k: pytest.fail("missing authorization"))
    assert runtime.status() == {"status": "disabled", "reason": "configuration_absent"}
    assert runtime.preparer_for("committee") is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", [
    {"authorized_usd": True}, {"authorized_usd": 0}, {"authorized_usd": "NaN"},
    {"authorized_usd": "Infinity"}, {"authorized_usd": "0.0000000001"},
    {"authorization_id": "../pilot"}, {"triggers": ["chat_mention"]},
    {"triggers": ["committee", "committee"]}, {"max_tokens": 65536},
    {"model": "unapproved-model"}, {"enabled": 1}, {"version": True},
])
def test_invalid_policy_never_authorizes_provider(tmp_path, change):
    _write(tmp_path / "policy.json", _policy(**change))
    runtime = _runtime(tmp_path, lambda *_a, **_k: pytest.fail("invalid authorization"))
    with pytest.raises(ValueError):
        runtime.preparer_for("committee")
    assert not (tmp_path / "data").exists()


def test_duplicate_config_keys_fail_closed(tmp_path):
    (tmp_path / "policy.json").write_text('{"version":1,"enabled":false,"enabled":true}', encoding="utf-8")
    runtime = _runtime(tmp_path, lambda *_a, **_k: pytest.fail("duplicate config"))
    with pytest.raises(ValueError, match="duplicate"):
        runtime.status()


def test_trigger_not_authorized_and_plain_mention_do_not_create_budget(tmp_path):
    _write(tmp_path / "policy.json", _policy())
    runtime = _runtime(tmp_path, lambda *_a, **_k: pytest.fail("unauthorized trigger"))
    for trigger in ("watchlist", "chat_mention"):
        with pytest.raises(PermissionError):
            runtime.proposer_for(trigger)
    assert not (tmp_path / "data").exists()


def test_committee_and_queue_share_paid_cache_without_model_overrides(tmp_path):
    from types import SimpleNamespace
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _operating_plan, _bundle, _documents
    _write(tmp_path / "policy.json", _policy())
    plan, paid, journals = _operating_plan(), [], []
    def call(**kwargs):
        paid.append(kwargs)
        spec = json.loads(kwargs["messages"][0]["content"])["contract"]["preparation_stage"]
        source = plan["model"] if spec["scope"] == "model" else plan["scenarios"][spec["scope"]]
        out = {"drivers": {name: source[name] for name in spec["drivers"]}, "rationale": "Synthetic source case"}
        return SimpleNamespace(id="runtime-test", model=kwargs["model"], provider="synthetic",
            stop_reason="end_turn", usage=SimpleNamespace(cost_usd=.01),
            content=[SimpleNamespace(type="text", text=json.dumps(out))])
    def factory(journal, *, authorized_usd):
        journals.append(journal)
        assert str(authorized_usd) == "2.50"
        return BudgetedProposer(journal, authorized_usd=authorized_usd, model="synthetic/model",
            max_tokens=16000, thinking={"type": "adaptive"}, call=call,
            metadata=lambda model: {"id": model, "context_length": 100000,
                "pricing": {"prompt": "0.000001", "completion": "0.000001"}})
    runtime = _runtime(tmp_path, factory)
    first = prepare_method_inputs(_bundle(), documents=_documents(), propose=runtime.proposer_for("committee"))
    assert first["status"] == "prepared", first["issues"]
    count = len(paid)
    restarted = _runtime(tmp_path, factory)
    second = prepare_method_inputs(_bundle(), documents=_documents(), propose=restarted.proposer_for("portfolio"))
    assert second["status"] == "prepared" and len(paid) == count
    assert count > 1 and len(set(journals)) == 1
    assert str(journals[0]).startswith(str(tmp_path / "data"))
    assert all(request["model"] == "synthetic/model" and request["max_tokens"] == 16000 for request in paid)


def test_revocation_after_first_answer_prevents_next_provider_call(tmp_path):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _operating_plan, _bundle, _documents
    _write(tmp_path / "policy.json", _policy())
    plan, paid = _operating_plan(), []
    def factory(*args, **kwargs):
        def propose(dossier, contract):
            paid.append(contract)
            _write(tmp_path / "policy.json", {"version": 1, "enabled": False})
            return {"drivers": deepcopy(plan["model"]), "rationale": "Synthetic opening facts"}
        return propose
    runtime = _runtime(tmp_path, factory)
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=runtime.proposer_for("committee"))
    assert result["status"] == "incomplete" and len(paid) == 1
    assert any("authorization" in item["reason"] for item in result["issues"])


def test_rebinding_same_authorization_cannot_raise_persisted_cap(tmp_path):
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    _write(tmp_path / "policy.json", _policy())
    def factory(journal, *, authorized_usd):
        return BudgetedProposer(journal, authorized_usd=authorized_usd, model="synthetic/model",
            max_tokens=16000, thinking={}, call=lambda **_: pytest.fail("not invoking AI"))
    runtime = _runtime(tmp_path, factory)
    runtime.proposer_for("committee")
    _write(tmp_path / "policy.json", _policy(authorized_usd="3.00"))
    with pytest.raises(ValueError, match="persisted authorization"):
        runtime.proposer_for("committee")


def test_runtime_binding_uses_common_collector_and_real_workbook(tmp_path, monkeypatch):
    from pathlib import Path
    from test_input_preparation import _operating_plan, _bundle, _documents
    _write(tmp_path / "policy.json", _policy())
    plan, acquisitions = _operating_plan(), []
    def factory(*args, **kwargs):
        def propose(dossier, contract):
            spec = contract["preparation_stage"]
            source = plan["model"] if spec["scope"] == "model" else plan["scenarios"][spec["scope"]]
            return {"drivers": {name: source[name] for name in spec["drivers"]}, "rationale": "Synthetic source case"}
        return propose
    def collect(ticker, **kwargs):
        acquisitions.append((ticker, kwargs))
        return {"status": "ready", "documents": _documents(), "issues": []}
    monkeypatch.setattr("bellomberg.valuation.preparation_sources.collect_preparation_evidence", collect)
    runtime = _runtime(tmp_path, factory)
    filings = [{"ticker": "SYNTH-EXT", "candidati": []}]
    service = runtime.preparer_for("committee", filing_results=filings)
    filings.clear()
    result = service(_bundle())
    assert result["valuation_usability"]["usable"], result.get("error")
    assert Path(result["path"]).is_relative_to(tmp_path / "models")
    assert Path(result["path"]).is_file()
    assert result["preparation"]["proposal"]["approval_status"] == "automatic_non_approved"
    assert acquisitions[0][1]["archive_root"] == tmp_path / "sources"
    assert acquisitions[0][1]["filing_results"] == [{"ticker": "SYNTH-EXT", "candidati": []}]
    assert acquisitions[0][1]['method_id'] == 'operating_fcff'


def test_bound_service_checks_revocation_before_collecting_sources(tmp_path, monkeypatch):
    _write(tmp_path / "policy.json", _policy())
    runtime = _runtime(tmp_path, lambda *_a, **_k: lambda *_: pytest.fail("revoked provider"))
    service = runtime.preparer_for("committee")
    _write(tmp_path / "policy.json", {"version": 1, "enabled": False})
    monkeypatch.setattr("bellomberg.valuation.preparation_sources.collect_preparation_evidence",
                        lambda *_a, **_k: pytest.fail("revoked source acquisition"))
    with pytest.raises(PermissionError, match="authorization"):
        service({})


def test_runtime_forwards_context_preparation_and_rechecks_revocation_before_spending(tmp_path):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _bundle, _documents
    _write(tmp_path / 'policy.json', _policy())
    contexts = []
    class Proposer:
        def prepare_context(self, dossier, contract, **options):
            contexts.append((dossier, contract, options))
            _write(tmp_path / 'policy.json', {'version': 1, 'enabled': False})
            return dossier
        def __call__(self, *_):
            pytest.fail('revocation during free preparation must stop the paid call')
    runtime = _runtime(tmp_path, lambda *_a, **_k: Proposer())
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=runtime.proposer_for('committee'))
    assert len(contexts) == 1
    assert contexts[0][2]['source_dossier']['documents']
    assert any('authorization' in issue['reason'] for issue in result['issues'])


def test_installation_binding_reports_missing_invalid_and_excluded_policy(tmp_path, monkeypatch):
    from bellomberg.valuation import preparation_runtime as module
    monkeypatch.setattr("bellomberg.core.paths.DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("bellomberg.core.paths.REPORT_DIR", tmp_path / "report")
    missing = module.bind_installation_preparer("committee")
    assert missing["preparer"] is None
    assert missing["state"] == {"status": "disabled", "reason": "configuration_absent"}
    assert not (tmp_path / "data").exists()
    (tmp_path / "data").mkdir()
    policy_file = tmp_path / "data" / "valuation_automation.json"
    policy_file.write_text('{"version":1,"enabled":true}', encoding="utf-8")
    malformed = module.bind_installation_preparer("committee")
    assert malformed["preparer"] is None and malformed["state"]["status"] == "error"
    assert "policy schema" in malformed["state"]["reason"]
    _write(policy_file, _policy(triggers=["portfolio"]))
    excluded = module.bind_installation_preparer("committee")
    assert excluded["preparer"] is None
    assert excluded["state"] == {"status": "disabled", "reason": "trigger_not_authorized"}
    assert not (tmp_path / "data" / "valuation_ai_budgets").exists()


@pytest.mark.parametrize("state,cost,receipt_cost,error", [
    ("invented", 1, .000000001, "state"),
    ("received", 10000000, .02, "receipt"),
    ("received", 3000000000, 3, "exceeds cap"),
])
def test_recovery_budget_audit_rejects_inconsistent_journal(tmp_path, state, cost, receipt_cost, error):
    import sqlite3
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    _write(tmp_path / "policy.json", _policy())
    runtime = _runtime(tmp_path, None)
    journal = runtime.data_root / "valuation_ai_budgets" / (_policy()["authorization_id"] + ".sqlite3")
    BudgetedProposer(journal, authorized_usd="2.50", model="synthetic/model", max_tokens=16000,
                     thinking={}, call=lambda **_: pytest.fail("no provider during audit"))
    with sqlite3.connect(journal) as conn:
        conn.execute("INSERT INTO requests(key,state,reserved,cost,request,receipt) VALUES (?,?,?,?,?,?)",
                     ("synthetic", state, 10000000, cost, "{}",
                      json.dumps({"response_id": "synthetic-provider-receipt", "cost_usd": receipt_cost})))
    with pytest.raises(ValueError, match=error):
        runtime.budget_audit()
