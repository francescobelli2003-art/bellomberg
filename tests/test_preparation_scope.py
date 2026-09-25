"""A limited activation cannot spend on or enqueue another instrument."""
import json
from types import SimpleNamespace

import pytest

from bellomberg.valuation.preparation_runtime import PreparationRuntime, read_policy
from bellomberg.valuation.valuation_automation import ValuationAutomation


def _policy(**changes):
    return {"version": 1, "enabled": True,
            "authorization_id": "afe409bb-41f6-44f9-8c3c-ddcfe69e4890",
            "authorized_usd": "1.25", "triggers": ["manual_refresh", "price"],
            "tickers": ["TST-SCOPE"], **changes}


def _runtime(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(_policy()), encoding="utf-8")
    calls = []
    class Proposer:
        def __call__(self, dossier, contract):
            calls.append("paid")
            return {"allowed": True}
        def prepare_context(self, dossier, contract, **options):
            calls.append("context")
            return dossier
    runtime = PreparationRuntime(path, data_root=tmp_path / "data",
        archive_root=tmp_path / "archive", output_dir=tmp_path / "models",
        proposer_factory=lambda *_a, **_k: Proposer())
    return runtime, path, calls


@pytest.mark.parametrize("tickers", [[], "TST-SCOPE", [None], ["tst-scope"],
    [" TST-SCOPE"], ["../TST-SCOPE"], ["TST-SCOPE", "TST-SCOPE"]])
def test_invalid_ticker_scope_is_rejected(tmp_path, tickers):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(_policy(tickers=tickers)), encoding="utf-8")
    with pytest.raises(ValueError, match="ticker"):
        read_policy(path)


def test_valid_scope_is_visible_and_allowed_request_reaches_proposer(tmp_path):
    runtime, _, calls = _runtime(tmp_path)
    assert runtime.status()["tickers"] == ["TST-SCOPE"]
    bound = runtime.proposer_for("manual_refresh").proposer
    assert bound({"ticker": "TST-SCOPE"}, {}) == {"allowed": True}
    assert calls == ["paid"]


@pytest.mark.parametrize("ticker", ["TST-OTHER", None])
def test_denied_instrument_stops_before_context_or_provider(tmp_path, ticker):
    runtime, _, calls = _runtime(tmp_path)
    bound = runtime.proposer_for("manual_refresh").proposer
    for operation in (bound, bound.prepare_context):
        with pytest.raises(PermissionError, match="ticker"):
            operation({"ticker": ticker}, {})
    assert calls == []


def test_denied_instrument_stops_before_source_collection(tmp_path, monkeypatch):
    runtime, _, calls = _runtime(tmp_path)
    monkeypatch.setattr("bellomberg.valuation.preparation_service.collect_and_prepare",
                        lambda *_a, **_k: pytest.fail("unauthorized acquisition"))
    with pytest.raises(PermissionError, match="ticker"):
        runtime.preparer_for("manual_refresh")({"case": {"ticker": "TST-OTHER"}})
    assert calls == []


def test_scope_revocation_stops_already_bound_request(tmp_path):
    runtime, path, calls = _runtime(tmp_path)
    bound = runtime.proposer_for("manual_refresh").proposer
    path.write_text(json.dumps(_policy(tickers=["TST-OTHER"])), encoding="utf-8")
    with pytest.raises(PermissionError, match="changed"):
        bound({"ticker": "TST-SCOPE"}, {})
    assert calls == []


def test_queue_scope_is_enforced_at_enqueue_and_before_recovery(tmp_path):
    runtime, _, calls = _runtime(tmp_path)
    manager = object.__new__(ValuationAutomation)
    manager.runtime = runtime
    manager.versions = SimpleNamespace(current=lambda *_: pytest.fail("unauthorized lookup"))
    for operation in (
        lambda: manager.enqueue_initial("TST-OTHER", "manual_refresh"),
        lambda: manager.enqueue_refresh("TST-OTHER", "manual_refresh", "synthetic-source"),
        lambda: manager.enqueue_price("TST-OTHER", "synthetic-quote"),
        lambda: manager._authorize({"ticker": "TST-OTHER", "kind": "prepare",
            "request": {"trigger": "manual_refresh", "authorization": read_policy(runtime.policy_path)}}),
    ):
        with pytest.raises(PermissionError, match="ticker"):
            operation()
    assert calls == []
