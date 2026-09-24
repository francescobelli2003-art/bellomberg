"""Price-only tracking preserves economic inputs and never opens an AI journal."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_valuation_automation import automation, TICKER
from test_market_quote import quote_info, DAY


def _current(automation):
    manager, observed, policy_path, _ = automation
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["triggers"].append("price")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    manager.enqueue_initial(TICKER, "portfolio")
    assert manager.run_one(owner="synthetic-initial")["status"] == "succeeded"
    payload = manager.versions.current(TICKER)["current"]
    manager.runtime.proposer_factory = lambda *_a, **_k: pytest.fail("price work must not construct an AI proposer")
    manager.prepare = lambda *_a, **_k: pytest.fail("price work must not prepare inputs")
    manager.quote_provider = lambda ticker, *, as_of: {
        "status": "ok", "source_id": "synthetic-profile", "as_of": DAY, "data": {"info": quote_info()}}
    return manager, observed, payload


def test_manager_price_only_reuses_current_without_preparation_or_payment(automation):
    manager, observed, current = _current(automation)
    before = deepcopy(observed)
    workbook = Path(current["path"])
    original = workbook.read_bytes(), workbook.with_suffix(".payload.json").read_bytes()
    queued = manager.enqueue_price(TICKER, "synthetic-price-observation")
    assert manager.enqueue_price(TICKER, "synthetic-price-observation")["id"] == queued["id"]
    result = manager.run_one(owner="synthetic-price-worker")
    assert result["status"] == "succeeded", result
    assert result["result"]["market_quote"]["price"] == 30
    assert observed == before
    assert manager.versions.current(TICKER)["current"] == current
    assert (workbook.read_bytes(), workbook.with_suffix(".payload.json").read_bytes()) == original


def test_price_recovery_needs_no_ai_billing_and_cannot_be_used_for_prepare(automation):
    from bellomberg.storage.valuation_jobs import RetryDenied
    manager, observed, _ = _current(automation)
    policy = json.loads(manager.runtime.policy_path.read_text(encoding="utf-8"))
    journal = manager.runtime.data_root / "valuation_ai_budgets" / (policy["authorization_id"] + ".sqlite3")
    journal.unlink()  # Synthetic only: no AI journal is required for quote acquisition.
    quote_provider = manager.quote_provider
    manager.quote_provider = lambda *_a, **_k: (_ for _ in ()).throw(OSError("synthetic quote timeout"))
    queued = manager.enqueue_price(TICKER, "synthetic-price-recovery")
    assert manager.run_one(owner="synthetic-price-worker")["status"] == "interrupted"
    assert manager.recover() == [{"job_id": queued["id"], "status": "queued", "reason": "price_only_no_ai_path"}]
    manager.quote_provider = quote_provider
    assert manager.run_one(owner="synthetic-restarted-price")["status"] == "succeeded"
    assert not journal.exists()
    prepare = manager.enqueue_refresh(TICKER, "portfolio", "synthetic-new-source")
    active = manager.jobs.claim("synthetic-prepare", lease_seconds=60)
    manager.jobs.interrupt(active["id"], active["lease_owner"], active["lease_token"], reason="synthetic crash")
    with pytest.raises(RetryDenied):
        manager.jobs.recover_interrupted(prepare["id"], reason="incorrect price-only bypass", safe_to_retry=True,
            cost_reconciliation={"reference": "synthetic", "ai_spend": "not_applicable_price_only"})
    with pytest.raises(PermissionError, match="price"):
        manager.runtime.proposer_for("price")
    with pytest.raises(PermissionError, match="price"):
        manager.enqueue_refresh(TICKER, "price", "synthetic-invalid-ai-trigger")


def test_repeated_price_poll_deduplicates_but_a_b_a_is_a_new_observation(automation):
    from bellomberg.valuation.valuation_price_events import reconcile_price_events
    manager, observed, _ = _current(automation)
    db = manager.versions.db
    db.add_or_update_position(TICKER, quantita=1, prezzo_medio=10, valuta="USD")
    def price(value):
        db.update_price(TICKER, value, valuta="USD", source="synthetic")
        return reconcile_price_events(manager)
    first = price(30)
    same = price(30)
    second = price(31)
    returned = price(30)
    assert first["status"] == same["status"] == second["status"] == returned["status"] == "ok"
    ids = [result["outcomes"][0]["id"] for result in (first, same, second, returned)]
    assert ids[0] == ids[1] and len(set(ids)) == 3
    assert same["outcomes"][0]["reused"] is True
    from datetime import timedelta
    instant = manager.jobs.clock()
    manager.jobs.clock = lambda: instant + timedelta(days=1)
    assert reconcile_price_events(manager)["outcomes"][0]["id"] == ids[-1]
    with db._conn() as conn:
        conn.execute("UPDATE positions SET is_active=0 WHERE ticker=?", (TICKER,))
    assert reconcile_price_events(manager)["outcomes"] == []


def test_price_event_discloses_missing_quote_without_inventing_a_price(automation):
    from bellomberg.valuation.valuation_price_events import reconcile_price_events
    manager, _, _ = _current(automation)
    manager.versions.db.add_or_update_position(TICKER, quantita=1, prezzo_medio=10, valuta="USD")
    result = reconcile_price_events(manager)
    assert result["status"] == "partial"
    assert result["outcomes"] == [{"ticker": TICKER, "status": "unavailable", "reason": "stored_price_absent"}]
    assert manager.run_one(owner="no-price-work") is None
