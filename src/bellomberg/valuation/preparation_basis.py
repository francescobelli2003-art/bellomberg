"""Select pinned prior research without borrowing approvals or inventing freshness."""
from copy import copy, deepcopy

from .preparation_seed import _digest, make_seed


def capture_prior_basis(current):
    """Called on the publisher's verified current payload, before checkpointing."""
    if not current or not current.get('current_generation'):
        return {'status': 'fresh_required', 'reason': 'no_previous_generation'}
    payload = current.get('current') or {}
    identity = {'source_generation_id': current['current_generation'],
                'source_snapshot_id': payload.get('snapshot_id')}
    if (payload.get('generation_id') != current['current_generation']
            or not identity['source_snapshot_id'] or not current.get('artifact', {}).get('available')):
        raise ValueError('previous generation or workbook cannot be verified')
    preparation = payload.get('preparation') or {}
    basis = preparation.get('review_basis')
    if basis is None:
        return {**identity, 'status': 'fresh_required', 'reason': 'prior_review_basis_absent'}
    _verify_basis(basis)
    if (preparation.get('status') != 'prepared'
            or basis['dossier']['ticker'] != payload.get('ticker')
            or basis['dossier']['method_id'] != payload.get('valuation_decision', {}).get('method_id')
            or basis['dossier']['as_of'] != payload.get('valuation_decision', {}).get('as_of')
            or _digest((preparation.get('proposal') or {}).get('plan')) != basis['seed']['plan_sha256']):
        raise ValueError('prior preparation differs from the published generation')
    return {**identity, 'status': 'available', 'basis_sha256': _digest(basis), 'basis': deepcopy(basis)}


def _verify_basis(basis):
    from .preparation_refresh import _compiled
    if (not isinstance(basis, dict) or set(basis) != {'version', 'dossier', 'contract', 'seed'}
            or type(basis['version']) is not int or basis['version'] != 1):
        raise ValueError('invalid prior preparation basis envelope')
    plan = _compiled(basis['seed'], basis['dossier'], basis['contract'])
    if _digest(plan) != basis['seed']['plan_sha256']:
        raise ValueError('prior preparation basis changed during recompilation')


def _verified_prior(prior):
    if not isinstance(prior, dict) or prior.get('status') not in ('available', 'fresh_required'):
        raise ValueError('invalid pinned preparation selection')
    if prior['status'] == 'fresh_required':
        if prior.get('reason') not in ('no_previous_generation', 'prior_review_basis_absent'):
            raise ValueError('unknown fresh preparation reason')
        return None
    basis = prior.get('basis')
    if (not prior.get('source_generation_id') or not prior.get('source_snapshot_id')
            or prior.get('basis_sha256') != _digest(basis)):
        raise ValueError('pinned preparation basis identity changed')
    _verify_basis(basis)
    return basis


def retain_prior_documents(report, prior):
    """Keep original receipts dated; preserve current failures and newer identities."""
    basis = _verified_prior(prior)
    result = deepcopy(report)
    if basis is None:
        return result
    documents = result.get('documents')
    if not isinstance(documents, list):
        raise ValueError('current acquisition document list required')
    ids = [d.get('id') for d in documents if isinstance(d, dict)]
    if len(ids) != len(documents) or len(set(ids)) != len(ids):
        raise ValueError('current acquisition document identities are ambiguous')
    retained = [deepcopy(doc) for doc in basis['dossier']['documents'] if doc['id'] not in ids]
    if retained:
        result['documents'].extend(retained)
        result['retained_review_evidence'] = {
            'document_ids': [d['id'] for d in retained],
            'basis': 'Historical receipts retained with original dates and identities. Not downloaded or remotely verified now; current acquisition gaps remain unchanged.'}
    return result


class BasisProposer:
    """Choose exact reuse, explicit review, or documented fresh preparation."""
    def __init__(self, staged, prior):
        from .preparation_ai import StagedProposer
        if not isinstance(staged, StagedProposer):
            raise ValueError('automatic prior selection requires a staged proposer')
        self.staged, self.prior = staged, deepcopy(prior)
        self.refresh_lineage = None
        self.preparation_selection = None

    def __call__(self, dossier, contract):
        from .preparation_ai import _model_dossier
        from .preparation_refresh import RefreshProposer, StructuralRefreshRequired, _compiled, _without_review_day
        basis = _verified_prior(self.prior)
        self.refresh_lineage = None
        self.preparation_selection = {key: deepcopy(value) for key, value in self.prior.items() if key != 'basis'}
        self.preparation_selection['human_approved'] = False
        if self.staged.seed is not None:
            if basis is not None:
                raise ValueError('candidate seed and previous-generation review basis cannot be combined')
            self.preparation_selection['mode'] = 'resume_candidate'
            # StagedProposer restores only the exact issuer/dossier/contract and
            # recompiles paid inputs before it can request any remaining work.
            return self.staged(dossier, contract)
        if basis is None:
            self.preparation_selection['mode'] = 'fresh'
            return self.staged(dossier, contract)
        if dossier['ticker'] != basis['dossier']['ticker']:
            raise ValueError('pinned preparation belongs to a different issuer')
        if dossier['as_of'] < basis['dossier']['as_of']:
            raise ValueError('preparation cutoff precedes the pinned research')
        if (_digest(_model_dossier(dossier)) == basis['seed']['dossier_sha256']
                and _digest(contract) == basis['seed']['contract_sha256']):
            self.preparation_selection['mode'] = 'unchanged_context'
            return _compiled(basis['seed'], dossier, contract)
        identity_keys = ('profile_id', 'profile_version', 'method_version', 'instrument')
        incompatible = (dossier['method_id'] != basis['dossier']['method_id']
            or any(dossier.get('decision', {}).get(k) != basis['dossier'].get('decision', {}).get(k) for k in identity_keys)
            or _digest(_without_review_day(contract, dossier['as_of'])) !=
               _digest(_without_review_day(basis['contract'], basis['dossier']['as_of'])))
        if incompatible:
            self.preparation_selection.update(mode='fresh', reason='economic_contract_changed')
            return self.staged(dossier, contract)
        self.preparation_selection['mode'] = 'review'
        reviewer = RefreshProposer(self.staged.proposer, basis['seed'], basis['dossier'], basis['contract'],
            excerpt_manifest=self.staged.forecast_excerpt_manifest,
            compact_scopes=['model', *contract['scenarios']], repair_terminal=True)
        try:
            plan = reviewer(dossier, contract)
        except StructuralRefreshRequired as change:
            # A newly reviewed calendar/perimeter cannot inherit old scenario paths.
            self.preparation_selection.update(mode='fresh_scenarios', reason='opening_structure_changed',
                                               opening_review=deepcopy(change.review))
            plan = {'model': deepcopy(change.model), 'scenarios': {s: {} for s in contract['scenarios']},
                    'scenario_rationale': {}}
            fresh = copy(self.staged)
            fresh.seed = make_seed(dossier, contract, plan)
            return fresh(dossier, contract)  # Recompiles opening before another stage may spend.
        self.refresh_lineage = deepcopy(reviewer.refresh_lineage)
        return plan
