"""Explicit queue runner over the common preparer, immutable storage and publisher.

No worker starts on import and no spending authorization is inferred here. The
caller supplies the configured, budgeted proposer and an explicitly queued job.
Interrupted work is resumed only after the queue's separate recovery procedure.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import Event, Thread

from bellomberg.storage.valuation_jobs import LeaseLost, assert_lease


class ValuationWorker:
    def __init__(self, jobs, versions, *, propose, archive_root, output_dir, prepare=None, quote_provider=None,
                 acquire=None, authorize=None, fx_collector=None):
        if Path(jobs.db_path).resolve() != Path(versions.db.db_path).resolve():
            raise ValueError("queue and publisher must share the same database")
        self.jobs, self.versions = jobs, versions
        self.propose = propose
        self.archive_root, self.output_dir = Path(archive_root).resolve(), Path(output_dir).resolve()
        if not any(self.output_dir.is_relative_to(root) for root in versions.roots):
            raise ValueError("output directory outside publication roots")
        if prepare is None:
            from .preparation_service import collect_and_prepare
            prepare = collect_and_prepare
        self.prepare = prepare
        self.quote_provider = quote_provider
        self.fx_collector = fx_collector
        self.acquire, self.authorize = acquire, authorize

    @contextmanager
    def _heartbeat(self, job, seconds):
        stop, failures = Event(), []
        def renew():
            while not stop.wait(max(.1, seconds / 3)):
                try:
                    self.jobs.heartbeat(job["id"], job["lease_owner"], job["lease_token"], lease_seconds=seconds)
                except Exception as exc:
                    failures.append(exc)
                    stop.set()
        thread = Thread(target=renew, daemon=True, name="valuation-lease")
        thread.start()
        def guard(conn):
            if failures:
                raise LeaseLost("heartbeat failed: " + type(failures[0]).__name__)
            assert_lease(conn, job["id"], job["lease_owner"], job["lease_token"], now=self.jobs.clock())
        try:
            yield guard
        finally:
            stop.set()
            thread.join()

    def run_one(self, *, owner, lease_seconds=120):
        job = self.jobs.claim(owner, lease_seconds=lease_seconds)
        if job is None:
            return None
        with self._heartbeat(job, lease_seconds) as guard:
            return self._run_claimed(job, guard=guard)

    def _reprice(self, job, identity):
        """Reuse the existing quote contract, preserving the immutable model.

        The result belongs to one exact generation. Readers must discard it if
        current changes; it never rolls the FV forward or overwrites the Excel.
        """
        from .market_quote import build_market_quote, build_repriced_quote
        request = job["request"]
        current = self.versions.current(job["ticker"])
        payload = (current or {}).get("current")
        if not payload or payload["generation_id"] != request.get("generation_id"):
            raise ValueError("price comparison requires the exact current generation")
        if not current["artifact"]["available"]:
            raise ValueError("current workbook unavailable: " + current["artifact"]["reason"])
        rows = [row for row in payload["acquisition_snapshot"]["case"]["records"]
                if row.get("driver") == "quotation" and row.get("scenario") == "model"]
        if len(rows) != 1:
            raise ValueError("documented quotation contract missing or ambiguous")
        if any(key in request for key in ('profile', 'fx_evidence', 'fx', 'financial_to_quote_rate')):
            raise ValueError("price/FX facts must be acquired by the worker, never supplied in the request")
        checkpoint = job.get("checkpoint") or {}
        evidence = checkpoint.get('payload') or {}
        profile = evidence.get('profile') if checkpoint.get('stage') in ('quote_acquired', 'quote_fx_acquired') else None
        if profile is None:
            provider = self.quote_provider
            if provider is None:
                from .sector_analysis import default_sector_providers
                provider = default_sector_providers()["profile"]
            profile = provider(job["ticker"], as_of=request["as_of"])
            self.jobs.checkpoint(*identity, stage="quote_acquired", payload={"profile": profile})
        quote_bundle = {"case": {"ticker": job["ticker"], "as_of": request["as_of"],
            "info": (profile.get("data") or {}).get("info") or {}, "sources": {"profile": profile}}}
        quote = build_market_quote(quote_bundle, rows[0]["value"],
            {s: payload.get("fair_value_" + s) for s in ("bear", "base", "bull")})
        if quote['status'] == 'fx_not_rolled':
            if checkpoint.get('stage') == 'quote_fx_acquired':
                fx_evidence = evidence.get('fx_evidence')
            else:
                from .fx_evidence import collect_fx_evidence
                q = rows[0]['value']
                fx_evidence = (self.fx_collector or collect_fx_evidence)(financial_currency=q['financial_currency'],
                    quote_currency=q['quote_currency'], on=quote['observed_local_date'], as_of=request['as_of'],
                    archive_root=self.archive_root / 'quote-fx')
                self.jobs.checkpoint(*identity, stage='quote_fx_acquired',
                                     payload={'profile': profile, 'fx_evidence': fx_evidence})
            quote = build_repriced_quote(payload, quote_bundle, rows[0]['value'], fx_evidence)
        if self.authorize is not None:
            self.authorize(job)
        return self.jobs.finish(*identity, status="succeeded" if quote["status"] == "ok" else "incomplete",
            reason=quote["message"], result={"snapshot_id": payload["snapshot_id"],
                "generation_id": payload["generation_id"], "market_quote": quote})

    def _run_claimed(self, job, *, guard=None):
        """Process one lease; usable artifacts become current only through CAS.

        A process crash may leave an orphan artifact or a registered snapshot.
        The persisted result checkpoint plus exact-generation registration avoid
        another preparation and another thesis on recovery. A checkpoint before
        the paid response relies on the proposer's durable billing journal.
        """
        from .sector_analysis import validate_bundle
        from .dcf_quality import assess_valuation_usability
        supplied_identity = (job["id"], job["lease_owner"], job["lease_token"])
        job = self.jobs.get(job["id"])
        if (job is None or job["status"] != "running"
                or (job["id"], job["lease_owner"], job["lease_token"]) != supplied_identity):
            raise LeaseLost("job is not running")
        identity = (job["id"], job["lease_owner"], job["lease_token"])
        if guard is None:
            guard = lambda conn: assert_lease(conn, *identity, now=self.jobs.clock())
        with self.versions._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            guard(conn)
        request = job["request"]
        try:
            if self.authorize is not None:
                self.authorize(job)
            if job["kind"] == "reprice":
                return self._reprice(job, identity)
            if "expected_current_generation" not in request:
                raise ValueError("expected current generation required at enqueue")
            checkpoint = job.get("checkpoint") or {}
            evidence = checkpoint.get("payload") or {}
            if "bundle" not in request and not evidence.get("valuation"):
                if not evidence.get("bundle"):
                    if self.acquire is None:
                        raise ValueError("queued intent requires an acquisition service")
                    evidence = self.acquire(job)
                    validate_bundle(evidence["bundle"], job["ticker"])
                    persisted = self.jobs.checkpoint(*identity, stage="acquired", payload=evidence)
                    # Start from the exact persisted form used after a restart.
                    # JSON normalization can reorder mappings whose iteration
                    # becomes a list in the prompt, changing the paid cache key.
                    evidence = persisted["checkpoint"]["payload"]
                bundle = validate_bundle(evidence["bundle"], job["ticker"])
            elif "bundle" in request:
                bundle = validate_bundle(request["bundle"], job["ticker"])
            result = (checkpoint.get("payload") or {}).get("valuation")
            if result is None:
                prior = evidence.get('prior_preparation')
                if prior is not None:
                    expected = (evidence.get('expected_current_generation') if request.get('capture_head_at_start')
                                else request['expected_current_generation'])
                    if prior.get('source_generation_id') != expected:
                        raise ValueError('pinned research differs from the acquired current generation')
                recoverable = []
                def leased(proposer, *, forward_context=True):
                    def invoke(*args, **kwargs):
                        with self.versions._conn() as conn:
                            conn.execute("BEGIN IMMEDIATE")
                            guard(conn)
                        try:
                            return proposer(*args, **kwargs)
                        except (OSError, LeaseLost) as exc:
                            recoverable.append(exc)
                            raise
                    project_context = getattr(proposer, 'prepare_context', None) if forward_context else None
                    if callable(project_context):
                        invoke.prepare_context = leased(project_context, forward_context=False)
                    return invoke
                from .preparation_ai import StagedProposer
                proposer = (StagedProposer(leased(self.propose.proposer),
                            drivers_per_stage=self.propose.drivers_per_stage,
                            opening_drivers_per_stage=self.propose.opening_drivers_per_stage,
                            opening_excerpt_manifest=self.propose.opening_excerpt_manifest,
                            forecast_excerpt_manifest=self.propose.forecast_excerpt_manifest,
                            opening_dossier=self.propose.opening_dossier,
                            stage_dossiers=self.propose.stage_dossiers, seed=self.propose.seed)
                            if isinstance(self.propose, StagedProposer) else leased(self.propose))
                result = self.prepare(bundle, archive_root=self.archive_root, propose=proposer,
                    filing_results=deepcopy(request.get("filing_results", [])), output_dir=self.output_dir,
                    **({'prior_preparation': deepcopy(evidence['prior_preparation'])}
                       if 'prior_preparation' in evidence else {}),
                    **({"source_report": deepcopy(evidence["source_report"])} if "source_report" in evidence else {}))
                # The economic compiler reports proposal errors as incomplete.
                # A transport/lease failure must retain its source checkpoint so
                # recovery can reuse the already paid stages exactly.
                if recoverable:
                    raise recoverable[-1]
                if result.get("ticker") != job["ticker"]:
                    raise ValueError("prepared ticker differs from queued issuer")
                self.jobs.checkpoint(*identity, stage="prepared", payload={"valuation": result,
                    **({"source_refresh_after_approval": True}
                       if evidence.get("source_refresh_after_approval") is True else {}),
                    **({"expected_current_generation": evidence["expected_current_generation"]}
                       if request.get("capture_head_at_start") else {})})
            # Snapshot registration verifies the exact immutable payload before
            # returning an existing thesis. Never trust a checkpoint's marker.
            if self.authorize is not None:
                self.authorize(job)
            rationales = (result.get("acquisition_snapshot") or {}).get("analysis_context", {}).get("scenario_rationale", {})
            thesis = self.versions.db.save_valuation_thesis(job["ticker"], valuation_payload=result,
                reuse_generation=True, price=result.get("price"), engine=result.get("engine"),
                variant_view="\n".join(str(rationales.get(s, "")) for s in ("bear", "base", "bull")))
            if thesis is None:
                raise OSError("snapshot/thesis registration failed; prepared checkpoint retained")
            def publication_guard(conn):
                guard(conn)
                if self.authorize is not None:
                    self.authorize(job)
            publication = self.versions.publish(result["snapshot_id"], result["generation_id"],
                expected_current_generation=(evidence["expected_current_generation"]
                    if request.get("capture_head_at_start") else request["expected_current_generation"]),
                guard=publication_guard)
            usable = assess_valuation_usability(result)["usable"]
            status = "succeeded" if usable and publication["status"] == "published" else "incomplete"
            return self.jobs.finish(*identity, status=status, reason=publication["reason"],
                result={"snapshot_id": result["snapshot_id"], "generation_id": result["generation_id"],
                        "thesis_id": thesis, "publication": publication})
        except LeaseLost:
            raise  # A superseded worker cannot finish or replace another lease.
        except OSError as exc:
            return self.jobs.interrupt(*identity, reason=(type(exc).__name__ + ": " + str(exc))[:1000])
        except Exception as exc:
            return self.jobs.finish(*identity, status="failed", reason=(type(exc).__name__ + ": " + str(exc))[:1000])
