"""Real synthetic preparation, workbook and SQLite; no provider or main DB."""
from datetime import datetime, timezone
from pathlib import Path
import pytest

from test_input_preparation import _bundle, _documents, _propose_operating
from test_valuation_jobs import Clock


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.storage.valuation_jobs import ValuationJobStore, ensure_schema as jobs_schema
    from bellomberg.storage.valuation_versions import ValuationVersions, ensure_schema as versions_schema
    from bellomberg.valuation.valuation_worker import ValuationWorker
    from bellomberg.valuation.preparation_service import prepare_and_generate
    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self: None)
    db = MemoryDB(str(tmp_path / "workflow.db"), str(tmp_path / "chroma"))
    with db._conn() as conn:
        jobs_schema(conn)
        versions_schema(conn)
    clock = Clock()
    jobs = ValuationJobStore(db.db_path, clock=clock)
    versions = ValuationVersions(db, roots=[tmp_path],
        clock=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    calls = []
    def prepare(bundle, *, propose, output_dir, **kwargs):
        calls.append(bundle["snapshot_id"])
        return prepare_and_generate(bundle, documents=_documents(), propose=propose, output_dir=output_dir)
    worker = ValuationWorker(jobs, versions, propose=_propose_operating,
        archive_root=tmp_path / "sources", output_dir=tmp_path, prepare=prepare)
    return worker, calls, clock


def _enqueue(worker, evidence="evidence-1", current=None):
    return worker.jobs.enqueue("SYNTH-EXT", "prepare", evidence,
        {"bundle": _bundle(), "expected_current_generation": current}, max_attempts=2)


def test_worker_prepares_registers_and_publishes_once(workflow):
    worker, calls, _ = workflow
    first = _enqueue(worker)
    result = worker.run_one(owner="test-worker")
    assert result["status"] == "succeeded", result
    assert len(calls) == 1
    view = worker.versions.current("SYNTH-EXT")
    assert view["artifact"]["available"]
    assert view["current"]["generation_id"] == result["result"]["generation_id"]
    assert _enqueue(worker)["id"] == first["id"]
    assert worker.run_one(owner="test-worker") is None
    assert len(calls) == 1


def test_acquired_basis_must_match_pinned_generation_before_preparation(workflow):
    worker, calls, _ = workflow
    queued = worker.jobs.enqueue('SYNTH-EXT', 'prepare', 'different-basis',
        {'expected_current_generation': None, 'capture_head_at_start': True})
    worker.acquire = lambda job: {'bundle': _bundle(), 'expected_current_generation': 'head-at-acquisition',
        'prior_preparation': {'status': 'fresh_required', 'reason': 'prior_review_basis_absent',
                              'source_generation_id': 'unrelated-generation'}}
    result = worker.run_one(owner='basis-guard-test')
    assert result['status'] == 'failed'
    assert 'pinned research differs' in result['reason']
    assert calls == []
    assert worker.jobs.get(queued['id'])['checkpoint']['stage'] == 'acquired'


def test_resume_after_registration_does_not_prepare_or_duplicate_thesis(workflow, monkeypatch):
    worker, calls, clock = workflow
    queued = _enqueue(worker)
    active = worker.jobs.claim("before-crash", lease_seconds=10)
    original_publish = worker.versions.publish
    class Crash(BaseException):
        pass
    monkeypatch.setattr(worker.versions, "publish", lambda *_a, **_k: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        worker._run_claimed(active)
    assert len(calls) == 1
    checkpoint = worker.jobs.get(queued["id"])["checkpoint"]
    assert checkpoint["stage"] == "prepared"
    with worker.versions.db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM valuation_theses").fetchone()[0] == 1
    clock.advance(10)
    worker.jobs.expire_leases()
    worker.jobs.recover_interrupted(queued["id"], reason="synthetic proposer made no paid calls",
        cost_reconciliation={"reference": "synthetic-no-network", "charged_usd": "0"}, safe_to_retry=True)
    resumed = worker.jobs.claim("after-crash", lease_seconds=10)
    monkeypatch.setattr(worker.versions, "publish", original_publish)
    result = worker._run_claimed(resumed)
    assert result["status"] == "succeeded", result
    assert len(calls) == 1
    with worker.versions.db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM valuation_theses").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM valuation_publications").fetchone()[0] == 1


def test_stale_worker_cannot_adopt_replacement_lease(workflow):
    from bellomberg.storage.valuation_jobs import LeaseLost
    worker, calls, clock = workflow
    queued = _enqueue(worker)
    old = worker.jobs.claim("worker", lease_seconds=10)
    clock.advance(10)
    worker.jobs.expire_leases()
    worker.jobs.recover_interrupted(queued["id"], reason="no provider invoked",
        cost_reconciliation={"reference": "synthetic-no-network", "charged_usd": "0"}, safe_to_retry=True)
    replacement = worker.jobs.claim("worker", lease_seconds=10)
    with pytest.raises(LeaseLost):
        worker._run_claimed(old)
    assert not calls
    assert worker.jobs.get(queued["id"])["lease_token"] == replacement["lease_token"]


def test_failed_refresh_retains_visible_previous_model(workflow):
    from bellomberg.valuation.preparation_service import prepare_and_generate
    worker, calls, _ = workflow
    _enqueue(worker)
    first = worker.run_one(owner="worker")
    original = worker.versions.current("SYNTH-EXT")["current"]
    before = Path(original["path"]).read_bytes()
    _enqueue(worker, "new-document", first["result"]["generation_id"])
    worker.prepare = lambda bundle, **kwargs: prepare_and_generate(bundle, documents=[],
        propose=lambda *_: pytest.fail("missing evidence must not pay"), output_dir=worker.output_dir)
    second = worker.run_one(owner="worker")
    assert second["status"] == "incomplete"
    view = worker.versions.current("SYNTH-EXT")
    assert view["current"]["generation_id"] == original["generation_id"]
    assert view["latest_attempt"]["status"] == "incomplete"
    assert Path(original["path"]).read_bytes() == before


def test_price_only_job_preserves_model_and_never_invokes_preparation(workflow):
    from test_market_quote import quote_info, DAY
    worker, calls, _ = workflow
    _enqueue(worker)
    initial = worker.run_one(owner="worker")
    payload = worker.versions.current("SYNTH-EXT")["current"]
    original_bytes = Path(payload["path"]).read_bytes()
    original_sidecar = Path(payload["path"]).with_suffix(".payload.json").read_bytes()
    worker.prepare = lambda *_a, **_k: pytest.fail("price refresh cannot prepare a model")
    request = {"generation_id": initial["result"]["generation_id"], "as_of": DAY}
    profile = {"status": "ok", "source_id": "synthetic-profile", "as_of": DAY,
               "data": {"info": quote_info()}}
    worker.quote_provider = lambda ticker, *, as_of: profile
    worker.jobs.enqueue("SYNTH-EXT", "reprice", "price-v1", request)
    repriced = worker.run_one(owner="worker")
    assert repriced["status"] == "succeeded", repriced
    quote = repriced["result"]["market_quote"]
    assert quote["price"] == 30. and quote["price_model"] == 10.
    assert quote["upside_base_pct"] == round((payload["fair_value_base"] / 30. - 1) * 100, 1)
    assert quote["fv_basis"] == "valuation_date_no_rollforward"
    assert len(calls) == 1
    assert Path(payload["path"]).read_bytes() == original_bytes
    assert Path(payload["path"]).with_suffix(".payload.json").read_bytes() == original_sidecar
    assert worker.versions.current("SYNTH-EXT")["current"] == payload
    assert worker.jobs.latest_price_result("SYNTH-EXT", payload["generation_id"])["id"] == repriced["id"]
    assert worker.jobs.latest_price_result("SYNTH-EXT", "another-generation") is None
    profile["data"]["info"]["regularMarketPrice"] = None
    worker.jobs.enqueue("SYNTH-EXT", "reprice", "price-v2-missing", request)
    assert worker.jobs.latest_price_result("SYNTH-EXT", payload["generation_id"])["status"] == "queued"
    missing = worker.run_one(owner="worker")
    assert missing["status"] == "incomplete"
    latest = worker.jobs.latest_price_result("SYNTH-EXT", payload["generation_id"])
    assert latest["id"] == missing["id"]
    assert latest["result"]["market_quote"]["upside_base_pct"] is None


def test_transient_storage_failure_retains_result_for_explicit_recovery(workflow, monkeypatch):
    worker, calls, _ = workflow
    job = _enqueue(worker)
    save = worker.versions.db.save_valuation_thesis
    monkeypatch.setattr(worker.versions.db, "save_valuation_thesis", lambda *_a, **_k: None)
    interrupted = worker.run_one(owner="worker")
    assert interrupted["status"] == "interrupted" and interrupted["checkpoint"]["stage"] == "prepared"
    assert worker.run_one(owner="worker") is None
    assert len(calls) == 1
    monkeypatch.setattr(worker.versions.db, "save_valuation_thesis", save)
    worker.jobs.recover_interrupted(job["id"], reason="storage repaired; no paid fixture calls",
        cost_reconciliation={"reference": "synthetic-no-network", "charged_usd": "0"}, safe_to_retry=True)
    assert worker.run_one(owner="worker")["status"] == "succeeded"
    assert len(calls) == 1


@pytest.mark.parametrize('fact_key', ['profile', 'fx_evidence', 'fx', 'financial_to_quote_rate'])
def test_caller_cannot_supply_price_facts(workflow, fact_key):
    from test_market_quote import DAY, quote_info
    worker, calls, _ = workflow
    _enqueue(worker)
    first = worker.run_one(owner="worker")
    worker.quote_provider = lambda *_a, **_k: pytest.fail("caller price must be rejected before acquisition")
    worker.jobs.enqueue("SYNTH-EXT", "reprice", "invented-price", {
        "generation_id": first["result"]["generation_id"], "as_of": DAY,
        fact_key: {"status": "ok", "source_id": "fake-provider", "as_of": DAY, "data": {"info": quote_info()}}})
    result = worker.run_one(owner="worker")
    assert result["status"] == "failed" and "never supplied" in result["reason"]


def test_expired_lease_stops_next_proposal_stage(workflow):
    from bellomberg.storage.valuation_jobs import LeaseLost
    from bellomberg.valuation.preparation_ai import StagedProposer
    from test_input_preparation import _operating_plan
    worker, _, clock = workflow
    job = _enqueue(worker)
    active = worker.jobs.claim("worker", lease_seconds=10)
    calls, context_calls = [], []
    plan = _operating_plan()
    def provider(dossier, contract):
        stage = contract["preparation_stage"]
        calls.append(stage["scope"])
        values = plan["model"] if stage["scope"] == "model" else plan["scenarios"][stage["scope"]]
        clock.advance(10)  # A lost process cannot renew its lease.
        return {"drivers": {name: values[name] for name in stage["drivers"]}, "rationale": "Synthetic source reasoning"}
    def prepare_context(dossier, contract, **_):
        context_calls.append(contract['preparation_stage']['scope'])
        return dossier
    provider.prepare_context = prepare_context
    worker.propose = StagedProposer(provider)
    with pytest.raises(LeaseLost):
        worker._run_claimed(active)
    assert calls == ["model"]
    assert context_calls == ['model']  # The next free projection also requires the lease.
    assert worker.jobs.get(job["id"])["status"] == "interrupted"
    assert worker.versions.current("SYNTH-EXT") is None


def test_lease_lost_during_context_preparation_stops_provider(workflow):
    from bellomberg.storage.valuation_jobs import LeaseLost
    from bellomberg.valuation.preparation_ai import StagedProposer
    worker, _, clock = workflow
    job = _enqueue(worker)
    active = worker.jobs.claim('worker', lease_seconds=10)
    projections = []
    class Proposer:
        def prepare_context(self, dossier, contract, **options):
            projections.append(contract['preparation_stage']['scope'])
            clock.advance(10)
            return dossier
        def __call__(self, *_):
            pytest.fail('expired lease must stop the provider after context preparation')
    worker.propose = StagedProposer(Proposer())
    with pytest.raises(LeaseLost):
        worker._run_claimed(active)
    assert projections == ['model']
    assert worker.jobs.get(job['id'])['status'] == 'interrupted'
    assert worker.versions.current('SYNTH-EXT') is None


def test_worker_preserves_split_opening_and_source_selection(workflow):
    from hashlib import sha256
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.preparation_service import prepare_and_generate
    from test_input_preparation import _operating_plan
    worker, _, _ = workflow
    documents = _documents()
    original = documents[0]["text"]
    documents[0]["text"] += "\nUnselected source appendix."
    documents[0]["sha256"] = sha256(documents[0]["text"].encode()).hexdigest()
    manifest = [{"source_id": documents[0]["id"],
        "original_text_sha256_utf8": documents[0]["sha256"],
        "excerpts": [{"theme": "statement", "char_start": 0, "char_end_exclusive": len(original),
                      "excerpt_sha256_utf8": sha256(original.encode()).hexdigest()}]}]
    plan, calls = _operating_plan(), []
    def provider(dossier, contract):
        spec = contract["preparation_stage"]
        calls.append(spec)
        # Observe the actual paid boundary, not just attributes on a wrapper.
        assert "Unselected source appendix" not in dossier["documents"][0]["text"]
        assert dossier["stage_view"]["documents"][0]["view"] == "verified_excerpts"
        source = plan["model"] if spec["scope"] == "model" else plan["scenarios"][spec["scope"]]
        return {"drivers": {name: source[name] for name in spec["drivers"]},
                "rationale": "Synthetic original source evidence"}
    def prepare(bundle, *, propose, output_dir, **kwargs):
        return prepare_and_generate(bundle, documents=documents, propose=propose, output_dir=output_dir)
    worker.prepare = prepare
    worker.propose = StagedProposer(provider, opening_drivers_per_stage=1,
        opening_excerpt_manifest=manifest, forecast_excerpt_manifest=manifest)
    _enqueue(worker)
    result = worker.run_one(owner="projected-worker")
    assert result["status"] == "succeeded", result
    assert [spec["drivers"] for spec in calls if spec["scope"] == "model"] == [
        ["perimeter", "calendar", "quotation"], ["historical_revenue"], ["opening_nwc"],
        ["shares"], ["capdev_amortization_years"]]
    assert {spec["scope"] for spec in calls} == {"model", "bear", "base", "bull"}
    assert worker.versions.current("SYNTH-EXT")["artifact"]["available"]


def test_new_preparation_reaches_complete_mime_with_exact_workbook_bytes(workflow, monkeypatch):
    from bellomberg.reporting import email_sender
    from bellomberg.reporting.valuation_delivery import build_manifest, record_email_outcome
    worker, _, _ = workflow
    _enqueue(worker)
    job = worker.run_one(owner="worker")
    payload = worker.versions.current("SYNTH-EXT")["current"]
    result = {**payload, "_thesis_saved": {"thesis_id": job["result"]["thesis_id"]}}
    receipt = build_manifest({"SYNTH-EXT": result}, roots=[worker.output_dir])
    assert receipt["valuations"][0]["status"] == "ready"
    messages = []
    class SMTP:
        def __init__(self, *_a, **_k): pass
        def __enter__(self): return self
        def __exit__(self, *_a): pass
        def login(self, *_a): pass
        def send_message(self, message): messages.append(message)
    monkeypatch.setattr(email_sender, "email_configurata", lambda: True)
    for name in ("EMAIL_FROM", "EMAIL_TO"):
        monkeypatch.setattr(email_sender, name, "fixture@example.org")
    monkeypatch.setattr(email_sender, "EMAIL_PASSWORD", "synthetic-unused-secret", raising=False)
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", SMTP)
    body = email_sender.corpo_valutazioni({"SYNTH-EXT": result}, receipt["attachments"], delivery=receipt)
    sent = email_sender.invia_email_multi_allegati(receipt["attachments"], body_extra=body,
        expected_hashes=receipt["expected_hashes"], delivery_receipt=receipt)
    assert sent and len(messages) == 1  # Simulated transport only.
    parts = [part for part in messages[0].walk() if part.get_filename()]
    assert len(parts) == 1 and parts[0].get_filename() == Path(payload["path"]).name
    assert parts[0].get_payload(decode=True) == Path(payload["path"]).read_bytes()
    record_email_outcome(receipt, sent)
    assert receipt["valuations"][0]["email_included"]


def _cross_currency_model(worker):
    from test_market_quote import generate, quote_info, operating_records, repricing_fixture, DAY
    from bellomberg.valuation.fx_evidence import normalize_fx
    rows = operating_records()
    next(r['value'] for r in rows if r['driver'] == 'quotation').update(quote_currency='USD', quote_unit='USD')
    info = quote_info(); info['currency'] = 'USD'
    payload = generate(worker.output_dir, records=rows, info=info)
    assert payload['valuation_usability']['usable'], payload['valuation_usability']
    assert worker.versions.db.save_valuation_thesis(payload['ticker'], valuation_payload=payload) is not None
    worker.versions.publish(payload['snapshot_id'], payload['generation_id'], expected_current_generation=None)
    raw = repricing_fixture()[3]['documents'][0]
    fx = {'status': 'ready', 'documents': [raw, normalize_fx(raw, financial_currency='EUR',
        quote_currency='USD', on=DAY, as_of=DAY)], 'issues': []}
    worker.quote_provider = lambda ticker, *, as_of: {'status': 'ok', 'source_id': 'synthetic-profile',
        'as_of': DAY, 'data': {'info': info}}
    return payload, fx


def test_cross_currency_worker_resumes_fx_without_ai_or_reacquisition(workflow, monkeypatch):
    from test_market_quote import DAY
    from bellomberg.api.valuation_automation_routes import current_model_state
    worker, calls, clock = workflow
    payload, fx = _cross_currency_model(worker)
    original = Path(payload['path']).read_bytes()
    worker.propose = worker.prepare = lambda *a, **k: pytest.fail('price-only job cannot research')
    fx_calls = []
    def collect(**kwargs):
        fx_calls.append(kwargs)
        return fx
    worker.fx_collector = collect
    job = worker.jobs.enqueue(payload['ticker'], 'reprice', 'cross-fx',
        {'generation_id': payload['generation_id'], 'as_of': DAY}, max_attempts=2)
    active = worker.jobs.claim('first', lease_seconds=10)
    finish = worker.jobs.finish
    class Crash(BaseException): pass
    monkeypatch.setattr(worker.jobs, 'finish', lambda *a, **k: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash): worker._run_claimed(active)
    checkpoint = worker.jobs.get(job['id'])['checkpoint']
    assert checkpoint['stage'] == 'quote_fx_acquired'
    assert len(fx_calls) == 1 and fx_calls[0]['on'] == DAY
    assert fx_calls[0]['financial_currency'] == 'EUR' and fx_calls[0]['quote_currency'] == 'USD'
    clock.advance(10); worker.jobs.expire_leases()
    worker.jobs.recover_interrupted(job['id'], reason='synthetic crash after free acquisition',
        cost_reconciliation={'reference': 'synthetic-no-paid-work', 'charged_usd': '0'}, safe_to_retry=True)
    worker.quote_provider = worker.fx_collector = lambda *a, **k: pytest.fail('must reuse persisted evidence')
    monkeypatch.setattr(worker.jobs, 'finish', finish)
    result = worker.run_one(owner='resumed')
    assert result['status'] == 'succeeded', result
    quote = result['result']['market_quote']
    assert quote['fx']['rate'] == 1.25 and quote['contract'] == 'market_quote/2'
    assert calls == [] and Path(payload['path']).read_bytes() == original
    assert worker.versions.current(payload['ticker'])['current']['generation_id'] == payload['generation_id']
    state = current_model_state(worker.jobs.db_path, roots=[worker.output_dir], ticker=payload['ticker'])
    assert state['latest_price_job']['market_quote'] == quote
    # SQL guards reject changes; corrupted deserialization must also fail closed.
    import json
    import sqlite3
    altered = result['result']; altered['market_quote']['upside_base_pct'] = 999.
    with pytest.raises(sqlite3.IntegrityError), worker.versions.db._conn() as conn:
        conn.execute('UPDATE valuation_jobs SET result_json=? WHERE id=?', (json.dumps(altered), job['id']))
    monkeypatch.setattr(type(worker.jobs), 'latest_price_result', lambda *a, **k: result)
    state = current_model_state(worker.jobs.db_path, roots=[worker.output_dir], ticker=payload['ticker'])
    assert state['latest_price_job']['market_quote'] is None
    assert state['latest_price_job']['status'] == 'incomplete'


def test_cross_currency_missing_exact_day_fx_is_saved_as_incomplete(workflow):
    from test_market_quote import DAY
    worker, _, _ = workflow
    payload, _ = _cross_currency_model(worker)
    worker.fx_collector = lambda **k: {'status': 'incomplete', 'documents': [],
        'issues': [{'reason': 'no exact-day ECB observation'}]}
    worker.jobs.enqueue(payload['ticker'], 'reprice', 'missing-fx',
        {'generation_id': payload['generation_id'], 'as_of': DAY})
    result = worker.run_one(owner='worker')
    assert result['status'] == 'incomplete' and 'no exact-day ECB observation' in result['reason']
    assert result['checkpoint']['stage'] == 'quote_fx_acquired'
    assert result['result']['market_quote']['upside_base_pct'] is None
