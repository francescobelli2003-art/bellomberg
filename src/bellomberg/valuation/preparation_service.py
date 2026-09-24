"""Common preparation -> existing engine workflow for committee and queued jobs."""
from copy import deepcopy


def prepare_and_generate(bundle, *, documents, propose, output_dir=None, source_report=None,
                         prior_preparation=None):
    from .input_preparation import prepare_method_inputs
    from .dcf_engine import generate_valuation, _write_payload_sidecar
    if prior_preparation is not None:
        from .preparation_basis import BasisProposer, retain_prior_documents
        retained = retain_prior_documents({**deepcopy(source_report or {}), 'documents': documents}, prior_preparation)
        documents = retained['documents']
        if source_report is not None or 'retained_review_evidence' in retained:
            source_report = retained
        propose = BasisProposer(propose, prior_preparation)
    review_basis = {}
    def capture(dossier, contract):
        review_basis.update(dossier=deepcopy(dossier), contract=deepcopy(contract))
        return propose(dossier, contract)
    prepared = prepare_method_inputs(bundle, documents=documents, propose=capture, source_report=source_report)
    selection = getattr(propose, 'preparation_selection', None)
    if selection is not None:
        prepared['provenance']['preparation_selection'] = deepcopy(selection)
    if prepared['status'] == 'prepared' and review_basis:
        from .preparation_seed import make_seed, _digest
        review_basis['seed'] = make_seed(review_basis['dossier'], review_basis['contract'], prepared['proposal']['plan'])
        review_basis['version'] = 1
        lineage = getattr(propose, 'refresh_lineage', None)
        if lineage is not None:
            if lineage.get('plan_sha256') != _digest(prepared['proposal']['plan']):
                raise ValueError('refresh lineage differs from compiled plan')
            prepared['provenance']['refresh_review'] = deepcopy(lineage)
    candidate = prepared["bundle"]
    if prepared["issues"]:
        from uuid import uuid4
        from .dcf_quality import normalize_valuation_payload
        result = normalize_valuation_payload({"ticker": candidate["case"]["ticker"], "ok": False,
            "snapshot_id": candidate["snapshot_id"], "generation_id": str(uuid4()),
            "valuation_decision": deepcopy(candidate["decision"]),
            "acquisition_snapshot": candidate, "acquisition_tasks": deepcopy(candidate["acquisition_tasks"]),
            "input_consumption": {"status": "incomplete", "consumed_fields": [], "consumed_records": []},
            "error": "Preparazione incompleta: " + "; ".join(issue["reason"] for issue in prepared["issues"]),
            "exclude_from_action_table": True}, expected_decision=candidate["decision"],
            as_of=candidate["case"]["as_of"])
    else:
        result = generate_valuation(candidate["case"]["ticker"], prepared_bundle=candidate,
                                    output_dir=output_dir)
    # The exact plan is part of the recorded generation, never an approval.
    result["preparation"] = {key: deepcopy(prepared[key]) for key in
                             ("status", "issues", "proposal", "provenance")}
    if prepared['status'] == 'prepared' and review_basis:
        import json
        from .preparation_ai import _json
        # Schema descriptors contain tuples in memory. Persist the same finite
        # plain-JSON form used by requests, hashes and durable job checkpoints.
        result['preparation']['review_basis'] = json.loads(_json(review_basis))
    result["acquisition_tasks"].extend(deepcopy(prepared["issues"]))
    if result.get("path"):
        result = _write_payload_sidecar(result)
    return result


def collect_and_prepare(bundle, *, archive_root, propose, filing_results=(), output_dir=None,
                        catalog=None, download=None, source_report=None, prior_preparation=None):
    """Explicit preparation entry point; callers own paid-work authorization."""
    from .input_preparation import has_approved_inputs
    from .sector_analysis import validate_bundle
    bundle = validate_bundle(bundle, bundle["case"]["ticker"])
    if has_approved_inputs(bundle):
        return prepare_and_generate(bundle, documents=[], propose=propose, output_dir=output_dir)
    from .preparation_sources import collect_preparation_evidence
    report = deepcopy(source_report) if source_report is not None else collect_preparation_evidence(
        bundle["case"]["ticker"], as_of=bundle["case"]["as_of"], archive_root=archive_root,
        filing_results=filing_results, catalog=catalog, download=download,
        financial_currency=(bundle['case'].get('info') or {}).get('financialCurrency'),
        method_id=bundle['decision'].get('method_id'))
    return prepare_and_generate(bundle, documents=report["documents"], propose=propose,
                                source_report=report, output_dir=output_dir, prior_preparation=prior_preparation)
