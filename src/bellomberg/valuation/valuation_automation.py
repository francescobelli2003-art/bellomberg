"""Configured queue orchestration over the shared sources, preparer and publisher.

Notifications only enqueue. Acquisition and paid stages run under the queue's
single global lease. Importing or constructing this service starts no thread,
migrates no schema, and does not infer permission from the private pilot budget.
"""
from copy import deepcopy
from hashlib import sha256
import json


class ValuationAutomation:
    def __init__(self, runtime, jobs, versions, *, acquire=None, collect=None, prepare=None, quote_provider=None):
        from pathlib import Path
        if Path(jobs.db_path).resolve() != Path(versions.db.db_path).resolve():
            raise ValueError("automation queue and publisher must share the database")
        if not any(runtime.output_dir.is_relative_to(root) for root in versions.roots):
            raise ValueError("output directory outside publication roots")
        self.runtime, self.jobs, self.versions = runtime, jobs, versions
        self.acquire, self.collect, self.prepare = acquire, collect, prepare
        self.quote_provider = quote_provider

    def _enqueue(self, ticker, trigger, evidence_key, *, initial=False, filing_results=()):
        if trigger == "price":
            raise PermissionError("price authorization cannot enqueue preparation")
        policy = self.runtime._require(trigger)
        current = self.versions.current(ticker)
        if initial and (current or {}).get("current_generation"):
            if current["artifact"]["available"]:
                return {"status": "current", "generation_id": current["current_generation"]}
            return {"status": "unavailable", "generation_id": current["current_generation"],
                    "reason": current["artifact"]["reason"]}
        request = {"trigger": trigger, "authorization": policy,
                   "as_of": self.jobs.clock().date().isoformat(),
                   "refresh": not initial,
                   "capture_head_at_start": True,
                   "expected_current_generation": (current or {}).get("current_generation"),
                   "filing_results": deepcopy(list(filing_results))}
        # Same source notification across tracking/startup channels reuses its
        # original request, even after current changes. New evidence is explicit.
        identity = sha256(json.dumps({"version": 1, "authorization": policy, "evidence_key": evidence_key},
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return self.jobs.enqueue(ticker, "prepare", evidence_key, request, max_attempts=3,
                                 event_identity="valuation-event-v1:" + identity, coalesce_tracking=True)

    def enqueue_initial(self, ticker, trigger):
        job = self._enqueue(ticker, trigger, "tracked-security-initial", initial=True)
        return self._continue_after_cutoff(job) or job

    def enqueue_refresh(self, ticker, trigger, evidence_key, *, filing_results=()):
        job = self._enqueue(ticker, trigger, evidence_key, filing_results=filing_results)
        return self._continue_after_cutoff(job) or self._continue_after_approval(job) or job

    def _continue_after_cutoff(self, job):
        """Complete the persisted handoff of an expired, never-started intent."""
        from datetime import date
        following, seen = None, set()
        while (job.get("result") or {}).get("unstarted_cutoff_expired"):
            day = job["result"]["unstarted_cutoff_expired"]
            if (job["id"] in seen or job["kind"] != "prepare" or job["status"] != "incomplete"
                    or job["attempts"] != 1 or job.get("checkpoint") or "bundle" in job["request"]
                    or date.fromisoformat(day).isoformat() != day
                    or day <= job["request"]["as_of"]):
                raise ValueError("invalid unstarted cutoff handoff")
            seen.add(job["id"])
            self._authorize(job)
            request = deepcopy(job["request"])
            request.pop("_event_identity")
            request.update(as_of=day, cutoff_parent_job_id=job["id"])
            following = self.jobs.enqueue(job["ticker"], "prepare", job["evidence_key"], request,
                max_attempts=job["max_attempts"], event_identity=f"unstarted-cutoff-v1:{job['id']}:{day}")
            job = following
        return following

    def _continue_after_approval(self, job):
        """Repair the finish/enqueue crash window using the persisted checkpoint.

        A source event must first restore an archived PM-approved model when no
        active approved head protects it. Only the following job proposes the
        new automatic candidate; the bootstrap never consumes paid preparation.
        """
        evidence = (job.get("checkpoint") or {}).get("payload") or {}
        if job["status"] != "succeeded" or evidence.get("source_refresh_after_approval") is not True:
            return None
        publication = (job.get("result") or {}).get("publication") or {}
        if publication.get("status") != "published" or publication.get("origin") != "approved":
            raise PermissionError("source continuation requires an attested approved publication")
        self._authorize(job)
        request = deepcopy(job["request"])
        event = request.pop("_event_identity")
        request["approval_bootstrap_job_id"] = job["id"]
        key = "source-after-approval-v1:" + sha256(event.encode()).hexdigest()
        return self.jobs.enqueue(job["ticker"], "prepare", key, request, max_attempts=3,
                                 event_identity=key, coalesce_tracking=True)

    def enqueue_price(self, ticker, evidence_key):
        policy = self.runtime._require("price")
        current = self.versions.current(ticker)
        if not (current or {}).get("current_generation"):
            return {"status": "no_current", "reason": "price_comparison_requires_current_model"}
        request = {"trigger": "price", "authorization": policy,
                   "as_of": self.jobs.clock().date().isoformat(),
                   "generation_id": current["current_generation"]}
        identity = sha256(json.dumps({"authorization": policy, "evidence_key": evidence_key,
                                     "generation_id": current["current_generation"]},
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return self.jobs.enqueue(ticker, "reprice", evidence_key, request, max_attempts=3,
                                 event_identity="valuation-price-v1:" + identity)

    def _authorize(self, job):
        request = job["request"]
        if (job["kind"] == "reprice") != (request["trigger"] == "price"):
            raise PermissionError("job kind differs from authorized trigger")
        self.runtime._unchanged(request["trigger"], request["authorization"])

    def _current_cutoff(self, job):
        if job["request"]["as_of"] != self.versions.clock().date().isoformat():
            raise ValueError("queued information cutoff expired; explicit fresh refresh required before AI")

    def _acquire(self, job):
        from .sector_analysis import prepare_sector_analysis, validate_bundle, revise_sector_analysis
        from .preparation_sources import collect_preparation_evidence
        from .input_preparation import has_approved_inputs
        self._authorize(job)
        request = job["request"]
        current = self.versions.current(job["ticker"]) or {}
        expected = current.get("current_generation")
        bundle = (self.acquire or prepare_sector_analysis)(job["ticker"], as_of=request["as_of"])
        bundle = validate_bundle(bundle, job["ticker"])
        if bundle["case"]["as_of"] != request["as_of"]:
            raise ValueError("acquisition changed the queued information cutoff")
        restore_approval = (request.get("refresh") and has_approved_inputs(bundle)
                            and not (current.get("approval") or {}).get("active"))
        if restore_approval and request.get("approval_bootstrap_job_id"):
            raise PermissionError("approved head no longer active after bootstrap; refresh held")
        if request.get("refresh") and has_approved_inputs(bundle) and not restore_approval:
            # A source refresh proposes a separate automatic candidate. The
            # archive and approved current remain protected by publication.
            bundle = revise_sector_analysis(bundle, method_records=[])
        if not has_approved_inputs(bundle):
            self._current_cutoff(job)
        from .preparation_basis import capture_prior_basis, retain_prior_documents
        prior = None if has_approved_inputs(bundle) else capture_prior_basis(current)
        report = ({"documents": [], "status": "approved_inputs"} if has_approved_inputs(bundle) else
                  (self.collect or collect_preparation_evidence)(job["ticker"], as_of=request["as_of"],
                      archive_root=self.runtime.archive_root, filing_results=deepcopy(request["filing_results"]),
                      financial_currency=(bundle['case'].get('info') or {}).get('financialCurrency'),
                      method_id=bundle['decision'].get('method_id')))
        if prior is not None:
            report = retain_prior_documents(report, prior)
        return {"bundle": bundle, "source_report": report, "expected_current_generation": expected,
                "source_refresh_after_approval": bool(restore_approval),
                **({'prior_preparation': prior} if prior is not None else {})}

    def run_one(self, *, owner, lease_seconds=120):
        from .valuation_worker import ValuationWorker
        from .preparation_ai import StagedProposer
        job = self.jobs.claim(owner, lease_seconds=lease_seconds)
        if job is None:
            return None
        identity = (job["id"], owner, job["lease_token"])
        try:
            self._authorize(job)
            today = self.jobs.clock().date().isoformat()
            from datetime import date
            queued_day = job["request"]["as_of"]
            # Only the first claim, with no source/result checkpoint or bundled
            # inputs, proves that no preparation stage has ever been attempted.
            # Paid/interrupted work retains its exact original request/journal.
            if (job["kind"] == "prepare" and job["attempts"] == 1 and not job.get("checkpoint")
                    and "bundle" not in job["request"] and date.fromisoformat(queued_day).isoformat() == queued_day
                    and queued_day < today == self.versions.clock().date().isoformat()):
                result = self.jobs.finish(*identity, status="incomplete", reason="queued cutoff expired before first acquisition",
                    result={"unstarted_cutoff_expired": today})
                try:
                    result["fresh_cutoff_job_id"] = self._continue_after_cutoff(result)["id"]
                except Exception as exc:
                    result["fresh_cutoff_enqueue_error"] = type(exc).__name__ + ": " + str(exc)[:500]
                return result
            staged = self.runtime.proposer_for(job["request"]["trigger"]) if job["kind"] == "prepare" else None
        except Exception as exc:
            return self.jobs.finish(*identity, status="failed", reason=(type(exc).__name__ + ": " + str(exc))[:1000])

        def guarded(*args, **kwargs):
            self._authorize(job)
            self._current_cutoff(job)
            try:
                return staged.proposer(*args, **kwargs)
            except Exception as exc:
                try:
                    audit = self.runtime.budget_audit()
                except Exception as audit_error:
                    raise OSError("Paid request journal cannot be reconciled; recovery blocked") from audit_error
                if audit["state"] != "reconciled":
                    raise OSError("Paid request cost unresolved; automatic retry blocked") from exc
                raise
        project_context = getattr(staged.proposer, 'prepare_context', None) if staged is not None else None
        if callable(project_context):
            def prepare_context(*args, **kwargs):
                self._authorize(job)
                self._current_cutoff(job)
                return project_context(*args, **kwargs)
            guarded.prepare_context = prepare_context
        propose = (StagedProposer(guarded, drivers_per_stage=staged.drivers_per_stage,
            opening_drivers_per_stage=staged.opening_drivers_per_stage,
            opening_excerpt_manifest=staged.opening_excerpt_manifest,
            forecast_excerpt_manifest=staged.forecast_excerpt_manifest,
            opening_dossier=staged.opening_dossier,
            stage_dossiers=staged.stage_dossiers, seed=staged.seed) if staged is not None else None)
        worker = ValuationWorker(self.jobs, self.versions, propose=propose,
            archive_root=self.runtime.archive_root, output_dir=self.runtime.output_dir,
            acquire=self._acquire, authorize=self._authorize, prepare=self.prepare, quote_provider=self.quote_provider)
        with worker._heartbeat(job, lease_seconds) as guard:
            result = worker._run_claimed(job, guard=guard)
        try:
            following = self._continue_after_approval(self.jobs.get(job["id"]))
            if following:
                result["source_refresh_job_id"] = following["id"]
        except Exception as exc:
            # The source poll retries the same immutable event and repairs this
            # enqueue; a successfully restored approval remains successful.
            result["source_refresh_enqueue_error"] = type(exc).__name__ + ": " + str(exc)[:500]
        return result

    def recover(self):
        """Resume only reconciled work; ambiguous billing remains visibly interrupted."""
        self.jobs.expire_leases()
        with self.jobs._connect(read_only=True) as conn:
            ids = [row[0] for row in conn.execute("SELECT id FROM valuation_jobs WHERE status='interrupted' ORDER BY id")]
            bootstraps = [row[0] for row in conn.execute("""SELECT parent.id FROM valuation_jobs parent
                WHERE parent.status='succeeded' AND parent.kind='prepare'
                AND json_extract(parent.checkpoint_json,'$.payload.source_refresh_after_approval')=1
                AND NOT EXISTS (SELECT 1 FROM valuation_jobs child WHERE child.kind='prepare'
                    AND child.ticker=parent.ticker
                    AND json_extract(child.request_json,'$.approval_bootstrap_job_id')=parent.id)
                ORDER BY parent.id""")]
            cutoffs = [row[0] for row in conn.execute("""SELECT parent.id FROM valuation_jobs parent
                WHERE parent.kind='prepare' AND parent.status='incomplete'
                AND json_extract(parent.result_json,'$.unstarted_cutoff_expired') IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM valuation_jobs child
                    WHERE json_extract(child.request_json,'$.cutoff_parent_job_id')=parent.id)
                ORDER BY parent.id""")]
        outcomes = []
        for job_id in cutoffs:
            try:
                following = self._continue_after_cutoff(self.jobs.get(job_id))
                outcomes.append({"job_id": job_id, "status": following["status"],
                                 "fresh_cutoff_job_id": following["id"], "reason": "unstarted_cutoff_continuation"})
            except Exception as exc:
                outcomes.append({"job_id": job_id, "status": "blocked", "reason": type(exc).__name__ + ": " + str(exc)})
        for job_id in bootstraps:
            try:
                following = self._continue_after_approval(self.jobs.get(job_id))
                if following:
                    outcomes.append({"job_id": job_id, "status": following["status"],
                                     "source_refresh_job_id": following["id"], "reason": "approved_bootstrap_continuation"})
            except Exception as exc:
                outcomes.append({"job_id": job_id, "status": "blocked", "reason": type(exc).__name__ + ": " + str(exc)})
        for job_id in ids:
            try:
                job = self.jobs.get(job_id)
                self._authorize(job)
                if job["kind"] == "reprice":
                    self.jobs.recover_interrupted(job_id, reason="price comparison recovery; no AI path",
                        cost_reconciliation={"reference": "reprice job:" + str(job_id),
                                             "ai_spend": "not_applicable_price_only"}, safe_to_retry=True)
                    outcomes.append({"job_id": job_id, "status": "queued", "reason": "price_only_no_ai_path"})
                    continue
                audit = self.runtime.budget_audit()
                if audit["state"] != "reconciled":
                    raise RuntimeError("billing audit " + audit["state"] + "; recovery not authorized")
                # This is the measured authorization total, not an invented
                # per-job cost. Its scope is part of the persisted reference.
                reference = "authorization:" + audit["authorization_id"] + ";scope=journal_total;requests=" + str(audit["request_count"])
                self.jobs.recover_interrupted(job_id, reason="startup recovery from persisted sources/results; billing reconciled",
                    cost_reconciliation={"reference": reference, "charged_usd": audit["known_cost_usd"]}, safe_to_retry=True)
                outcomes.append({"job_id": job_id, "status": "queued", "reason": "billing_reconciled"})
            except Exception as exc:
                outcomes.append({"job_id": job_id, "status": "blocked", "reason": type(exc).__name__ + ": " + str(exc)})
        return outcomes
