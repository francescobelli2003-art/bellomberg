"""Offline behavior of the opt-in event queue through a real synthetic workbook."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_input_preparation import _bundle, _documents, _propose_operating
from test_sector_analysis import DAY


TICKER = "SYNTH-EXT"
AUTHORIZATION_ID = "f80ae03c-2b3a-4fb1-8517-8ac2f6361001"


def _policy(path, *, authorization_id=AUTHORIZATION_ID, enabled=True):
    value = {"version": 1, "enabled": enabled}
    if enabled:
        value.update(authorization_id=authorization_id, authorized_usd="2.50",
                     triggers=["portfolio", "watchlist", "filing_diff", "startup"])
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def automation(tmp_path, monkeypatch):
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.storage.valuation_jobs import ValuationJobStore, ensure_schema as jobs_schema
    from bellomberg.storage.valuation_versions import ValuationVersions, ensure_schema as versions_schema
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    from bellomberg.valuation.preparation_runtime import PreparationRuntime
    from bellomberg.valuation.valuation_automation import ValuationAutomation

    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self: None)
    db = MemoryDB(str(tmp_path / "models.db"), str(tmp_path / "chroma"))
    with db._conn() as conn:
        jobs_schema(conn)
        versions_schema(conn)
    clock = lambda: datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    jobs = ValuationJobStore(db.db_path, clock=clock)
    versions = ValuationVersions(db, roots=[tmp_path], clock=clock)
    policy_path = tmp_path / "policy.json"
    _policy(policy_path)
    observed = {"acquire": [], "collect": [], "paid": []}
    plan = _propose_operating({"ticker": TICKER, "documents": _documents()},
        {"method_id": "operating_fcff", "schema": {"revenue_growth": ("revenue_drivers",)}})

    def paid_call(**kwargs):
        observed["paid"].append(kwargs)
        contract = json.loads(kwargs["messages"][0]["content"])["contract"]
        spec = contract.get('preparation_refresh') or contract['preparation_stage']
        if observed.get("on_paid") is not None:
            observed["on_paid"](spec)
        source = plan["model"] if spec["scope"] == "model" else plan["scenarios"][spec["scope"]]
        if 'preparation_refresh' in contract:
            from test_preparation_refresh import _compact_answer
            answer = {'reviews': {name: {'action': 'reuse', 'source_driver_sha256': digest,
                'evidence_ids': deepcopy(source[name]['evidence_ids']),
                'review_rationale': 'Synthetic current evidence explicitly reviewed; same supported economics.'}
                for name, digest in spec['driver_hashes'].items()},
                'rationale': 'Complete synthetic scenario reaffirmed under the current evidence.'}
            if 'compact_wire' in spec:
                answer = _compact_answer(answer, spec['compact_wire'])
        else:
            answer = {"drivers": {name: deepcopy(source[name]) for name in spec["drivers"]},
                      "rationale": "Synthetic issuer case documented for this stage"}
        return SimpleNamespace(id="synthetic-receipt-" + str(len(observed["paid"])),
            model=kwargs["model"], provider="synthetic", stop_reason="end_turn",
            usage=SimpleNamespace(cost_usd=.01),
            content=[SimpleNamespace(type="text", text=json.dumps(answer))])

    def proposer_factory(journal, *, authorized_usd):
        return BudgetedProposer(journal, authorized_usd=authorized_usd,
            model="synthetic/model", max_tokens=16000, thinking={"type": "adaptive"},
            call=paid_call, metadata=lambda model: {"id": model,
                "context_length": 100000, "pricing": {"prompt": "0.000001", "completion": "0.000001"}})

    runtime = PreparationRuntime(policy_path, data_root=tmp_path / "data",
        archive_root=tmp_path / "sources", output_dir=tmp_path / "models",
        proposer_factory=proposer_factory)

    def acquire(ticker, *, as_of):
        observed["acquire"].append((ticker, as_of))
        assert ticker == TICKER and as_of == DAY
        return _bundle()

    def collect(ticker, *, as_of, archive_root, filing_results, financial_currency=None, method_id=None):
        observed["collect"].append((ticker, as_of, str(archive_root), deepcopy(filing_results)))
        observed['collect_currency'] = financial_currency
        observed['collect_method'] = method_id
        assert ticker == TICKER and as_of == DAY
        return {"status": "ready", "documents": _documents(), "issues": [],
                "preparation_ready": True, "acquired_document_index": []}

    manager = ValuationAutomation(runtime, jobs, versions, acquire=acquire, collect=collect)
    return manager, observed, policy_path, tmp_path


@pytest.mark.parametrize('business,method', [('software','operating_fcff'),('bank','bank_residual_income')])
def test_queued_acquisition_passes_explicit_financial_currency(automation, business, method):
    manager, observed, _, _ = automation
    from test_sector_analysis import providers_for
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    providers = providers_for(business); original = providers['profile']
    def profile(*a, **k):
        result = original(*a, **k)
        result['data']['info']['financialCurrency'] = 'USD'
        return result
    providers['profile'] = profile
    def acquire(ticker, *, as_of):
        return prepare_sector_analysis(ticker, as_of=as_of, providers=providers)
    manager.acquire = acquire
    queued = manager.enqueue_initial(TICKER, 'portfolio')
    manager._acquire(manager.jobs.get(queued['id']))
    assert observed['collect_currency'] == 'USD'
    assert observed['collect_method'] == method


@pytest.mark.parametrize('automatic_sections', [False, True])
def test_queued_automatic_context_recovers_without_repaying_completed_opening(automation, automatic_sections):
    from hashlib import sha256
    manager, observed, _, _ = automation
    documents = _documents()
    if automatic_sections:
        documents[0]['text'] += '\n \n' * 60000
        documents[0]['sha256'] = sha256(documents[0]['text'].encode()).hexdigest()
    original_collect = manager.collect
    def collect(*args, **kwargs):
        report = original_collect(*args, **kwargs)
        report['documents'] = deepcopy(documents)
        return report
    manager.collect = collect
    original_factory = manager.runtime.proposer_factory
    context_calls, instances = [], []
    fault = {'pending': True}
    def factory(*args, **kwargs):
        proposer = original_factory(*args, **kwargs)
        proposer.automatic_sections = automatic_sections
        instances.append(proposer)
        project = proposer.prepare_context
        def prepare_context(dossier, contract, **options):
            scope = contract['preparation_stage']['scope']
            context_calls.append(scope)
            if len(context_calls) == 3 and fault['pending']:
                fault['pending'] = False
                raise OSError('Synthetic interruption before forecast context')
            return project(dossier, contract, **options)
        proposer.prepare_context = prepare_context
        return proposer
    manager.runtime.proposer_factory = factory
    job = manager.enqueue_initial(TICKER, 'portfolio')
    interrupted = manager.run_one(owner='context-worker')
    assert interrupted['status'] == 'interrupted', interrupted
    assert interrupted['checkpoint']['stage'] == 'acquired'
    assert context_calls == ['model', 'bear', 'bear']
    assert len(observed['paid']) == 2
    assert manager.runtime.budget_audit()['request_count'] == 2
    assert manager.versions.current(TICKER) is None

    recovered = manager.recover()
    assert any(row['job_id'] == job['id'] and row['status'] == 'queued' for row in recovered)
    result = manager.run_one(owner='restarted-context-worker')
    assert result['status'] == 'succeeded', result
    assert len(instances) == 2 and instances[0] is not instances[1]
    requests = [json.loads(item['messages'][0]['content']) for item in observed['paid']]
    assert sum(row['contract']['preparation_stage']['scope'] == 'model' for row in requests) == 1
    stages = [row['contract']['preparation_stage'] for row in requests]
    assert len({(stage['scope'], tuple(stage['drivers'])) for stage in stages}) == len(stages)
    if automatic_sections:
        assert all(row['dossier']['stage_view']['automatic_selection']['policy'] == 'ifrs_note_sections_v1' for row in requests)
        assert all(row['dossier']['stage_view']['excerpt_projection']['removed_layout_utf8_bytes'] > 0 for row in requests)
    assert all(row['dossier']['completed_plan']['model'] for row in requests[1:])
    assert len(observed['acquire']) == len(observed['collect']) == 1
    assert manager.runtime.budget_audit()['request_count'] == len(observed['paid'])
    current = manager.versions.current(TICKER)
    assert current['artifact']['available'] and Path(current['current']['path']).is_file()
    with manager.versions.db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM valuation_theses').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM valuation_publications').fetchone()[0] == 1


def test_enqueue_is_pure_and_deduplicates_across_portfolio_watchlist(automation):
    manager, observed, _, directory = automation
    first = manager.enqueue_initial(TICKER, "portfolio")
    again = manager.enqueue_initial(TICKER, "watchlist")
    assert first["status"] == again["status"] == "queued"
    assert first["id"] == again["id"]
    assert again["reused"] is True
    filing = [{"ticker": TICKER, "candidati": []}]
    refresh = manager.enqueue_refresh(TICKER, "filing_diff", "document-sha-A",
                                      filing_results=filing)
    filing.clear()
    manager.jobs.clock = lambda: datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
    repeated = manager.enqueue_refresh(TICKER, "portfolio", "document-sha-A")
    assert repeated["id"] == refresh["id"] and repeated["reused"] is True
    assert refresh["request"]["filing_results"] == [{"ticker": TICKER, "candidati": []}]
    different = manager.enqueue_refresh(TICKER, "filing_diff", "document-sha-B")
    assert different["id"] != refresh["id"]
    _policy(directory / "policy.json", authorization_id="f80ae03c-2b3a-4fb1-8517-8ac2f6361002")
    newly_authorized = manager.enqueue_refresh(TICKER, "filing_diff", "document-sha-A")
    assert newly_authorized["id"] != refresh["id"]
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]
    assert not (directory / "data" / "valuation_ai_budgets").exists()


def test_run_builds_real_workbook_then_initial_returns_exact_current(automation):
    manager, observed, _, _ = automation
    queued = manager.enqueue_initial(TICKER, "portfolio")
    result = manager.run_one(owner="synthetic-worker")
    assert result["id"] == queued["id"] and result["status"] == "succeeded", result
    assert observed["acquire"] == [(TICKER, DAY)] and len(observed["collect"]) == 1
    assert observed["paid"], "a real proposal must pass through the budget journal"
    current = manager.versions.current(TICKER)
    assert current["current_generation"] == result["result"]["generation_id"]
    assert current["artifact"]["available"]
    assert Path(current["current"]["path"]).read_bytes().startswith(b"PK")
    assert manager.enqueue_initial(TICKER, "watchlist") == {
        "status": "current", "generation_id": current["current_generation"]}
    assert manager.run_one(owner="synthetic-worker") is None


@pytest.mark.parametrize("new_scope_claim", [False, True])
def test_running_initial_then_same_filing_refresh_reuses_only_equivalent_source_prompt(
        automation, new_scope_claim):
    from hashlib import sha256
    from bellomberg.valuation.valuation_sources import collect_documents
    from bellomberg.valuation.valuation_source_events import event_key

    manager, observed, _, _ = automation
    archive = manager.runtime.archive_root
    archive.mkdir(parents=True, exist_ok=True)
    url = "https://www.sec.gov/Archives/edgar/data/123456/000012345626000001/annual.htm"
    raw = ("<html><body>" + _documents()[0]["text"] + "</body></html>").encode()
    path = archive / "annual.htm"
    path.write_bytes(raw)
    digest = sha256(raw).hexdigest()
    metadata = {"emittente_id": "CIK:0000123456", "issuer": "Synthetic Issuer",
                "accession": "0000123456-26-000001", "form": "10-K",
                "report_date": "2025-12-31", "lingua": "en", "tipo": "annuale",
                "perimetro": "consolidated", "periodo_inizio": "2025-01-01",
                "periodo_fine": "2025-12-31"}
    catalog_metadata = deepcopy(metadata)
    if new_scope_claim:
        catalog_metadata.pop("perimetro")
    entry = {"ticker": TICKER, "form": "10-K", "filed_date": "2026-09-09",
             "url": url, "report_date": "2025-12-31",
             "emittente_id": metadata["emittente_id"], "metadati": catalog_metadata}
    candidate = {"fonte": "SEC EDGAR", "stato": "verificato", "url": url,
                 "filed_date": entry["filed_date"], "path": str(path), "sha256": digest,
                 "metadati": deepcopy(metadata)}
    filing = {"ticker": TICKER, "src": "filing_pipeline", "stato": "ok",
              "ultimo_non_verificato": False, "candidati": [candidate]}
    collected = []

    def collect(ticker, *, as_of, archive_root, filing_results, financial_currency=None, method_id=None):
        report = collect_documents(ticker, as_of=as_of, archive_root=archive_root,
            filing_results=filing_results,
            catalog=lambda _: {"stato": "ok", "documenti": [entry], "motivi": []},
            download=lambda *_a, **_k: {"stato": "ok", "path": str(path), "sha256": digest},
            selection_policy="opening_annual_comparative")
        assert report["status"] == "ready" and len(report["documents"]) == 1
        collected.append(report["documents"][0])
        # The existing synthetic plan remains the economic fixture; SEC acquisition
        # and its provenance are produced by the real collector in both jobs.
        return {**report, "documents": _documents() + report["documents"],
                "preparation_ready": True, "acquired_document_index": []}

    manager.collect = collect
    initial = manager.enqueue_initial(TICKER, "portfolio")
    queued = []

    def notify(_stage):
        if not queued:
            assert manager.jobs.get(initial["id"])["status"] == "running"
            queued.append(manager.enqueue_refresh(TICKER, "filing_diff", event_key(filing),
                                                  filing_results=[deepcopy(filing)]))
            observed["on_paid"] = None

    observed["on_paid"] = notify
    first = manager.run_one(owner="synthetic-worker")
    first_calls = len(observed["paid"])
    second = manager.run_one(owner="synthetic-worker")
    assert first["status"] == second["status"] == "succeeded"
    assert queued and queued[0]["id"] != initial["id"]
    assert first_calls > 1
    # Changed economic metadata needs four explicit review scopes, not another
    # initial forecast assembly. Equivalent acquisition-only changes spend zero.
    assert len(observed["paid"]) - first_calls == (4 if new_scope_claim else 0)
    assert manager.runtime.budget_audit()["request_count"] == first_calls + (4 if new_scope_claim else 0)
    assert collected[0]["document_sha256"] == collected[1]["document_sha256"] == digest
    assert collected[0]["sha256"] == collected[1]["sha256"]
    assert collected[0]["origin"] == "SEC" and collected[1]["origin"] == "filing_diff"
    assert collected[0]["metadata"] == (catalog_metadata if new_scope_claim else metadata)
    assert collected[1]["metadata"] == metadata


def test_policy_changed_after_enqueue_fails_before_acquisition_or_payment(automation):
    manager, observed, policy_path, _ = automation
    job = manager.enqueue_initial(TICKER, "portfolio")
    _policy(policy_path, enabled=False)
    failed = manager.run_one(owner="synthetic-worker")
    assert failed["id"] == job["id"] and failed["status"] == "failed"
    assert "authoriz" in (failed["reason"] or "").lower() or "policy" in (failed["reason"] or "").lower()
    assert not observed["acquire"] and not observed["collect"] and not observed["paid"]
    assert manager.versions.current(TICKER) is None


def test_stale_queued_cutoff_blocks_paid_work_before_source_collection(automation):
    from datetime import timedelta
    manager, observed, _, _ = automation
    job = manager.enqueue_initial(TICKER, "portfolio")
    original = deepcopy(job["request"])
    instant = manager.jobs.clock()
    manager.jobs.clock = manager.versions.clock = lambda: instant + timedelta(days=1)
    failed = manager.run_one(owner="next-day-worker")
    assert failed["status"] == "incomplete" and "cutoff expired" in failed["reason"]
    fresh = manager.jobs.get(failed["fresh_cutoff_job_id"])
    assert fresh["status"] == "queued" and fresh["request"]["as_of"] == "2026-09-11"
    assert not observed["paid"] and not observed["collect"] and not observed["acquire"]
    assert manager.jobs.get(job["id"])["request"] == original
    assert manager.versions.current(TICKER) is None


@pytest.mark.parametrize("refresh", [False, True])
def test_unstarted_yesterday_job_recovers_today_without_mutating_or_repeating_it(automation, refresh):
    from datetime import timedelta
    manager, observed, _, _ = automation
    clock = manager.jobs.clock
    manager.jobs.clock = lambda: clock() - timedelta(days=1)
    old = (manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-document") if refresh else
           manager.enqueue_initial(TICKER, "portfolio"))
    request = deepcopy(old["request"])
    manager.jobs.clock = clock
    retired = manager.run_one(owner="restarted")
    fresh_id = retired["fresh_cutoff_job_id"]
    assert not any(observed.values())
    assert manager.jobs.get(old["id"])["request"] == request
    assert manager.recover() == []
    result = manager.run_one(owner="restarted")
    assert result["id"] == fresh_id and result["status"] == "succeeded", result
    assert manager.jobs.get(fresh_id)["request"]["as_of"] == DAY
    assert len(observed["acquire"]) == len(observed["collect"]) == 1
    count = len(observed["paid"])
    assert count > 0
    repeated = (manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-document") if refresh else
                manager.enqueue_initial(TICKER, "portfolio"))
    assert repeated["status"] in ("succeeded", "current")
    assert manager.run_one(owner="again") is None and len(observed["paid"]) == count


@pytest.mark.parametrize("revoked", [False, True])
def test_restart_repairs_finish_to_fresh_enqueue_window(automation, monkeypatch, revoked):
    from datetime import timedelta
    manager, observed, policy, _ = automation
    clock = manager.jobs.clock
    manager.jobs.clock = lambda: clock() - timedelta(days=1)
    old = manager.enqueue_initial(TICKER, "portfolio")
    manager.jobs.clock = clock
    enqueue = manager.jobs.enqueue
    monkeypatch.setattr(manager.jobs, "enqueue", lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic enqueue outage")))
    retired = manager.run_one(owner="interrupted-window")
    assert retired["status"] == "incomplete" and "fresh_cutoff_enqueue_error" in retired
    assert not any(observed.values())
    monkeypatch.setattr(manager.jobs, "enqueue", enqueue)
    if revoked:
        _policy(policy, enabled=False)
    recovered = manager.recover()
    if revoked:
        assert any(row["job_id"] == old["id"] and row["status"] == "blocked" for row in recovered)
        assert manager.run_one(owner="after-revocation") is None
        assert not any(observed.values())
        return
    assert any(row["job_id"] == old["id"] and row.get("reason") == "unstarted_cutoff_continuation" for row in recovered)
    assert manager.recover() == []
    assert manager.run_one(owner="after-restart")["status"] == "succeeded"


def test_storage_interruption_preserves_acquired_sources_and_recovery(automation, monkeypatch):
    manager, observed, _, _ = automation
    queued = manager.enqueue_initial(TICKER, "portfolio")
    original_publish = manager.versions.publish
    monkeypatch.setattr(manager.versions, "publish",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("synthetic storage outage")))
    interrupted = manager.run_one(owner="synthetic-worker")
    assert interrupted["id"] == queued["id"] and interrupted["status"] == "interrupted", interrupted
    checkpoint = manager.jobs.get(queued["id"])["checkpoint"]
    assert checkpoint and checkpoint["stage"] == "prepared"
    prepared = checkpoint["payload"]["valuation"]
    assert prepared["preparation"]["provenance"]["documents"]["annual-1"]["sha256_status"] == "verified"
    original_source = deepcopy(checkpoint)
    paid_count = len(observed["paid"])
    assert paid_count and len(observed["acquire"]) == len(observed["collect"]) == 1
    monkeypatch.setattr(manager.versions, "publish", original_publish)
    recovery = manager.recover()
    assert any(row["job_id"] == queued["id"] and row["status"] == "queued" for row in recovery), recovery
    resumed = manager.run_one(owner="after-restart")
    assert resumed["status"] == "succeeded", resumed
    assert len(observed["paid"]) == paid_count
    assert len(observed["acquire"]) == len(observed["collect"]) == 1
    assert original_source["payload"] == checkpoint["payload"]
    assert resumed["result"]["generation_id"] == prepared["generation_id"]
    assert manager.versions.current(TICKER)["current_generation"] == resumed["result"]["generation_id"]


def test_missing_budget_journal_blocks_interrupted_prepare_recovery(automation):
    manager, _, _, _ = automation
    queued = manager.enqueue_initial(TICKER, "portfolio")
    claimed = manager.jobs.claim("synthetic-worker", lease_seconds=120)
    assert claimed["id"] == queued["id"]
    manager.jobs.checkpoint(claimed["id"], claimed["lease_owner"], claimed["lease_token"],
        stage="acquired", payload={"bundle": _bundle(), "source_report": {"status": "ready", "documents": _documents(), "issues": []}})
    manager.jobs.interrupt(claimed["id"], claimed["lease_owner"], claimed["lease_token"],
                           reason="synthetic crash before journal audit")
    recovered = manager.recover()
    assert any(row["job_id"] == queued["id"] and row["status"] == "blocked" for row in recovered), recovered
    assert manager.jobs.get(queued["id"])["status"] == "interrupted"


def test_revocation_in_last_paid_response_cannot_publish(automation):
    manager, observed, policy_path, _ = automation
    queued = manager.enqueue_initial(TICKER, "portfolio")
    def revoke_on_last_stage(spec):
        if spec["scope"] == "bull" and "terminal_bridge" in spec["drivers"]:
            _policy(policy_path, enabled=False)
    observed["on_paid"] = revoke_on_last_stage
    result = manager.run_one(owner="synthetic-worker")
    assert result["id"] == queued["id"] and result["status"] in ("failed", "interrupted"), result
    assert "authoriz" in result["reason"].lower()
    assert observed["paid"] and observed["acquire"] and observed["collect"]
    assert manager.versions.current(TICKER) is None
    checkpoint = manager.jobs.get(queued["id"])["checkpoint"]
    assert checkpoint["stage"] == "prepared", "last response must finish preparation before revocation gate"
    assert not any(row["status"] == "queued" for row in manager.recover())


def test_revocation_during_artifact_hash_cannot_promote(automation, monkeypatch):
    manager, observed, policy_path, _ = automation
    queued = manager.enqueue_initial(TICKER, "portfolio")
    original_artifact = manager.versions._artifact
    hashed = []
    def revoke_during_artifact(conn, payload):
        artifact = original_artifact(conn, payload)
        hashed.append(artifact)
        _policy(policy_path, enabled=False)
        return artifact
    monkeypatch.setattr(manager.versions, "_artifact", revoke_during_artifact)
    result = manager.run_one(owner="synthetic-worker")
    assert result["id"] == queued["id"] and result["status"] in ("failed", "interrupted"), result
    assert "authoriz" in result["reason"].lower()
    assert len(hashed) == 1 and hashed[0]["available"]
    assert observed["paid"] and manager.versions.current(TICKER) is None
    assert not any(row["status"] == "queued" for row in manager.recover())
    with manager.versions.db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM valuation_publications").fetchone()[0] == 0


@pytest.mark.parametrize("next_day", [False, True])
def test_unknown_provider_billing_interrupts_and_keeps_source_checkpoint(automation, next_day):
    manager, observed, _, _ = automation
    queued = manager.enqueue_initial(TICKER, "portfolio")
    def provider_fails(_spec):
        raise RuntimeError("synthetic transport error after reservation")
    observed["on_paid"] = provider_fails
    interrupted = manager.run_one(owner="synthetic-worker")
    assert interrupted["id"] == queued["id"] and interrupted["status"] == "interrupted", interrupted
    assert len(observed["paid"]) == 1
    checkpoint = manager.jobs.get(queued["id"])["checkpoint"]
    assert checkpoint["stage"] == "acquired"
    assert checkpoint["payload"]["source_report"]["documents"] == _documents()
    assert checkpoint["payload"]["bundle"]["snapshot_id"] == _bundle()["snapshot_id"]
    assert manager.runtime.budget_audit()["state"] == "unresolved"
    if next_day:
        from datetime import timedelta
        instant = manager.jobs.clock()
        manager.jobs.clock = manager.versions.clock = lambda: instant + timedelta(days=1)
    recovery = manager.recover()
    assert any(row["job_id"] == queued["id"] and row["status"] == "blocked" for row in recovery), recovery
    assert manager.jobs.get(queued["id"])["status"] == "interrupted"
    assert manager.run_one(owner="no_duplicate") is None
    assert len(observed["acquire"]) == len(observed["collect"]) == 1
    assert manager.versions.current(TICKER) is None


def test_new_source_proposes_separate_candidate_without_overwriting_pm_approval(automation):
    from test_valuation_versions import _approved_bundle
    from bellomberg.storage import method_records_store as archive
    manager, observed, _, _ = automation
    db = manager.versions.db
    bundle, _ = _approved_bundle(db)
    with db._conn() as conn:
        original_archive = deepcopy(archive.leggi_approvati(conn, TICKER, DAY))
    manager.acquire = lambda *_a, **_k: deepcopy(bundle)
    manager.enqueue_initial(TICKER, "portfolio")
    approved = manager.run_one(owner="synthetic-approved")
    assert approved["status"] == "succeeded" and not observed["paid"] and not observed["collect"]
    old_current = manager.versions.current(TICKER)
    assert old_current["approval"]["active"] is True
    manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-new-quarter")
    candidate = manager.run_one(owner="synthetic-refresh")
    assert candidate["status"] == "incomplete", candidate
    assert candidate["result"]["publication"]["status"] == "held"
    assert candidate["result"]["publication"]["origin"] == "automatic_non_approved"
    assert observed["paid"] and len(observed["collect"]) == 1
    assert manager.versions.current(TICKER)["current_generation"] == old_current["current_generation"]
    with db._conn() as conn:
        assert archive.leggi_approvati(conn, TICKER, DAY) == original_archive


def test_source_event_atomically_replaces_unstarted_initial_and_tracking_reuses_it(automation):
    manager, observed, _, _ = automation
    initial = manager.enqueue_initial(TICKER, "portfolio")
    source = manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-source-transition")
    previous = manager.jobs.get(initial["id"])
    assert previous["status"] == "failed" and previous["attempts"] == 0
    assert previous["request"] == initial["request"], "the original intent stays immutable"
    event = manager.jobs.events(initial["id"])[-1]
    assert event["action"] == "superseded_before_start"
    assert event["detail"]["replacement_job_id"] == source["id"]
    assert manager.enqueue_initial(TICKER, "watchlist")["id"] == source["id"]
    claimed = manager.jobs.claim("synthetic-worker", lease_seconds=60)
    assert claimed["id"] == source["id"]
    assert not observed["acquire"] and not observed["paid"]


def test_serial_refreshes_capture_current_when_work_starts_not_when_queued(automation):
    manager, observed, _, _ = automation
    manager.enqueue_initial(TICKER, "portfolio")
    first = manager.run_one(owner="synthetic-initial")
    old_generation = first["result"]["generation_id"]
    one = manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-new-filing")
    two = manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-next-filing")
    assert one["request"]["expected_current_generation"] == two["request"]["expected_current_generation"] == old_generation
    second = manager.run_one(owner="synthetic-one")
    third = manager.run_one(owner="synthetic-two")
    assert second["status"] == third["status"] == "succeeded"
    persisted = manager.jobs.get(two["id"])
    assert persisted["request"] == two["request"]
    assert persisted["checkpoint"]["payload"]["expected_current_generation"] == second["result"]["generation_id"]
    assert manager.versions.current(TICKER)["current_generation"] == third["result"]["generation_id"]


@pytest.mark.parametrize('concurrent_head', [False, True])
def test_review_resume_pins_basis_and_reuses_paid_scopes(automation, concurrent_head):
    manager, observed, _, root = automation
    manager.enqueue_initial(TICKER, 'portfolio')
    initial = manager.run_one(owner='initial')
    assert initial['status'] == 'succeeded'
    previous = manager.versions.current(TICKER)
    paid_before = len(observed['paid'])
    collect = manager.collect
    def updated(*args, **kwargs):
        result = collect(*args, **kwargs)
        result['issues'].append({'code': 'synthetic_new_disclosure', 'reason': 'New disclosure needs explicit review.'})
        return result
    manager.collect = updated
    factory = manager.runtime.proposer_factory
    stop = [True]
    def interrupted_factory(*args, **kwargs):
        paid = factory(*args, **kwargs)
        class InterruptOnce:
            def __call__(self, dossier, contract):
                stage = contract.get('preparation_refresh', {})
                if stage.get('scope') == 'base' and stop[0]:
                    stop[0] = False
                    raise OSError('interrupted before reserving base')
                return paid(dossier, contract)
            def prepare_context(self, *a, **k):
                return paid.prepare_context(*a, **k)
        return InterruptOnce()
    manager.runtime.proposer_factory = interrupted_factory
    queued = manager.enqueue_refresh(TICKER, 'filing_diff', 'synthetic-review-update')
    first = manager.run_one(owner='review-interrupted')
    assert first['status'] == 'interrupted', first
    checkpoint = manager.jobs.get(queued['id'])['checkpoint']
    pinned = checkpoint['payload']['prior_preparation']
    assert pinned['source_generation_id'] == previous['current_generation']
    assert len(observed['paid']) == paid_before + 2
    assert manager.runtime.budget_audit()['state'] == 'reconciled'
    expected_head = previous['current_generation']
    if concurrent_head:
        from bellomberg.valuation.preparation_service import prepare_and_generate
        other = prepare_and_generate(_bundle(), documents=_documents(), propose=_propose_operating,
                                     output_dir=root / 'models')
        assert manager.versions.db.save_valuation_thesis(TICKER, valuation_payload=other) is not None
        publication = manager.versions.publish(other['snapshot_id'], other['generation_id'],
            expected_current_generation=expected_head)
        assert publication['status'] == 'published'
        expected_head = other['generation_id']
    acquisitions = len(observed['acquire']), len(observed['collect'])
    manager.collect = lambda *a, **k: pytest.fail('interrupted review cannot reacquire or select a new basis')
    recovery = manager.recover()
    assert any(row['job_id'] == queued['id'] and row['status'] == 'queued' for row in recovery)
    resumed = manager.run_one(owner='review-resumed')
    assert resumed['status'] == ('incomplete' if concurrent_head else 'succeeded'), resumed
    assert len(observed['paid']) == paid_before + 4
    assert (len(observed['acquire']), len(observed['collect'])) == acquisitions
    stored = manager.jobs.get(queued['id'])['checkpoint']['payload']['valuation']
    selection = stored['preparation']['provenance']['preparation_selection']
    assert selection['mode'] == 'review' and selection['source_generation_id'] == pinned['source_generation_id']
    assert stored['preparation']['provenance']['refresh_review']['human_approved'] is False
    assert manager.versions.current(TICKER)['current_generation'] == (
        expected_head if concurrent_head else resumed['result']['generation_id'])


@pytest.mark.parametrize("initial_queued", [False, True])
def test_source_event_restores_archived_approval_before_automatic_candidate(automation, initial_queued):
    from test_valuation_versions import _approved_bundle
    manager, observed, _, _ = automation
    bundle, _ = _approved_bundle(manager.versions.db)
    manager.acquire = lambda *_a, **_k: deepcopy(bundle)
    if initial_queued:
        manager.enqueue_initial(TICKER, "portfolio")
    event = manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-approved-source")
    bootstrap = manager.run_one(owner="restore-approval")
    assert bootstrap["id"] == event["id"] and bootstrap["status"] == "succeeded"
    assert not observed["paid"] and not observed["collect"]
    approved = manager.versions.current(TICKER)
    assert approved["approval"]["active"] is True
    following = manager.jobs.get(bootstrap["source_refresh_job_id"])
    assert following["request"]["filing_results"] == event["request"]["filing_results"]
    assert following["request"]["as_of"] == event["request"]["as_of"]
    assert manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-approved-source")["id"] == following["id"]
    candidate = manager.run_one(owner="source-candidate")
    assert candidate["id"] == following["id"]
    assert candidate["result"]["publication"]["status"] == "held"
    assert observed["paid"] and len(observed["collect"]) == 1
    assert manager.versions.current(TICKER)["current_generation"] == approved["current_generation"]
    assert manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-approved-source")["id"] == following["id"]
    assert manager.run_one(owner="no-second-candidate") is None


def test_approval_bootstrap_repairs_finish_enqueue_crash_without_repaying(automation, monkeypatch):
    from test_valuation_versions import _approved_bundle
    manager, observed, _, _ = automation
    bundle, _ = _approved_bundle(manager.versions.db)
    manager.acquire = lambda *_a, **_k: deepcopy(bundle)
    manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-crash-event")
    original = manager._continue_after_approval
    monkeypatch.setattr(manager, "_continue_after_approval",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("synthetic enqueue outage")))
    restored = manager.run_one(owner="restore-before-crash")
    assert restored["status"] == "succeeded" and "source_refresh_enqueue_error" in restored
    assert manager.versions.current(TICKER)["approval"]["active"] is True
    assert not observed["paid"]
    monkeypatch.setattr(manager, "_continue_after_approval", original)
    recovery = manager.recover()
    assert any(row.get("reason") == "approved_bootstrap_continuation" for row in recovery)
    following = manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-crash-event")
    assert following["status"] == "queued"
    assert following["request"]["approval_bootstrap_job_id"] == restored["id"]
    candidate = manager.run_one(owner="after-crash")
    assert candidate["result"]["publication"]["status"] == "held"
    paid = len(observed["paid"])
    assert manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-crash-event")["id"] == following["id"]
    assert manager.run_one(owner="no-repeat") is None and len(observed["paid"]) == paid
    assert manager.recover() == []


def test_approval_continuation_keeps_source_order_before_newer_pending_event(automation):
    from datetime import timedelta
    from test_valuation_versions import _approved_bundle
    manager, observed, _, _ = automation
    bundle, _ = _approved_bundle(manager.versions.db)
    manager.acquire = lambda *_a, **_k: deepcopy(bundle)
    manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-source-A")
    instant = manager.jobs.clock()
    manager.jobs.clock = lambda: instant + timedelta(seconds=1)
    newer = manager.enqueue_refresh(TICKER, "filing_diff", "synthetic-source-B")
    manager.jobs.clock = lambda: instant + timedelta(seconds=2)
    bootstrap = manager.run_one(owner="approval-bootstrap")
    assert not observed["paid"]
    continuation = manager.run_one(owner="source-A-continuation")
    assert continuation["id"] == bootstrap["source_refresh_job_id"]
    following = manager.run_one(owner="source-B")
    assert following["id"] == newer["id"]
    assert manager.versions.current(TICKER)["latest_attempt"]["generation_id"] == following["result"]["generation_id"]
