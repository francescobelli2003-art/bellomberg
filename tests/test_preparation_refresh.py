"""Current-day review is explicit; old dates, facts and approvals stay protected."""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256

import pytest

from bellomberg.valuation.preparation_seed import _digest, make_seed
from bellomberg.valuation.preparation_ai import _model_dossier


def _case(method='bank'):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from test_sector_analysis import DAY, providers_for
    if method == 'bank':
        from test_input_preparation_bank import _documents, _propose
        ticker, business = 'SYNTH-BANK', 'bank'
    else:
        from test_input_preparation import _documents, _propose_operating as _propose
        ticker, business = 'SYNTH-EXT', 'software'
    old, current = {}, {}
    documents = _documents()
    def capture(target):
        def run(dossier, contract):
            target.update(dossier=deepcopy(dossier), contract=deepcopy(contract))
        return run
    tomorrow = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    for cutoff, target in ((DAY, old), (tomorrow, current)):
        bundle = prepare_sector_analysis(ticker, as_of=cutoff, providers=providers_for(business))
        prepare_method_inputs(bundle, documents=documents, propose=capture(target))
    plan = _propose(old['dossier'], old['contract'])
    seed = make_seed(old['dossier'], old['contract'], plan)
    review = {'source_plan_sha256': seed['plan_sha256'],
              'current_dossier_sha256': _digest(_model_dossier(current['dossier'])),
              'current_contract_sha256': _digest(current['contract']),
              'reviews': {}, 'scenario_rationale': deepcopy(plan['scenario_rationale'])}
    for scope, drivers in [('model', plan['model']), *plan['scenarios'].items()]:
        review['reviews'][scope] = {name: {
            'action': 'reuse', 'source_driver_sha256': _digest(driver),
            'review_rationale': 'Current review explicitly confirms the unchanged synthetic economics and dates.',
            'evidence_ids': deepcopy(driver['evidence_ids'])} for name, driver in drivers.items()}
    return old, current, seed, review


def _apply(old, current, seed, review):
    from bellomberg.valuation.preparation_refresh import apply_refresh_review
    return apply_refresh_review(seed, old['dossier'], old['contract'],
        current['dossier'], current['contract'], review, view=current['dossier'])


@pytest.mark.parametrize('method', ['bank', 'operating'])
def test_explicit_review_renews_complete_plan_through_existing_compiler(method):
    old, current, seed, review = _case(method)
    before = deepcopy((old, current, seed, review))
    renewed, lineage = _apply(old, current, seed, review)
    assert (old, current, seed, review) == before
    assert renewed['dossier_sha256'] == review['current_dossier_sha256']
    assert lineage['human_approved'] is False and lineage['method_compiler_verified'] is True
    assert lineage['valuation_engine_required'] is True
    assert len(lineage['reused_drivers']) == sum(map(len, review['reviews'].values()))
    assert renewed['plan']['model']['calendar']['value'] == seed['plan']['model']['calendar']['value']
    for scope, drivers in [('model', renewed['plan']['model']), *renewed['plan']['scenarios'].items()]:
        originals = seed['plan']['model'] if scope == 'model' else seed['plan']['scenarios'][scope]
        for name, item in drivers.items():
            assert item['value'] == originals[name]['value']
            assert item['valid_until'] == current['dossier']['as_of']
            assert item['evidence_ids'] == originals[name]['evidence_ids']
            assert originals[name]['rationale'] in item['rationale']


@pytest.mark.parametrize('fault', ['plan_hash', 'context_hash', 'contract_hash', 'driver_hash',
    'missing_driver', 'unknown_driver', 'missing_scope', 'missing_rationale', 'unknown_review_source',
    'approval', 'unknown_action', 'bad_historical_value', 'expired_replacement', 'stale_literal_expiry',
    'changed_source', 'different_ticker', 'incomplete_original', 'changed_contract'])
def test_invalid_or_incomplete_review_never_produces_renewed_seed(fault):
    old, current, seed, review = _case()
    choice = review['reviews']['model']['opening_common_equity']
    if fault == 'plan_hash': review['source_plan_sha256'] = '0' * 64
    elif fault == 'context_hash': review['current_dossier_sha256'] = '0' * 64
    elif fault == 'contract_hash': review['current_contract_sha256'] = '0' * 64
    elif fault == 'driver_hash': choice['source_driver_sha256'] = '0' * 64
    elif fault == 'missing_driver': del review['reviews']['base']['taxes']
    elif fault == 'unknown_driver': review['reviews']['base']['invented'] = deepcopy(choice)
    elif fault == 'missing_scope': del review['reviews']['bull']
    elif fault == 'missing_rationale': review['scenario_rationale']['base'] = ''
    elif fault == 'unknown_review_source': choice['evidence_ids'] = ['absent']
    elif fault == 'approval': review['human_approved'] = True
    elif fault == 'unknown_action': choice['action'] = 'assume'
    elif fault in ('bad_historical_value', 'expired_replacement'):
        item = deepcopy(seed['plan']['model']['opening_common_equity'])
        if fault == 'bad_historical_value':
            item['value'] += 1
            item.update(valid_until=current['dossier']['as_of'],
                        valid_until_basis=current['contract']['expiry_policy'])
        review['reviews']['model']['opening_common_equity'] = {'action': 'replace', 'driver': item}
    elif fault == 'stale_literal_expiry':
        item = seed['plan']['scenarios']['base']['taxes']
        text = 'Synthetic policy expires ' + old['dossier']['as_of'] + '.'
        for context in (old, current):
            document = context['dossier']['documents'][0]
            document['text'] += '\n' + text
            document['sha256'] = sha256(document['text'].encode()).hexdigest()
        item['valid_until_basis'] = text
        seed = make_seed(old['dossier'], old['contract'], seed['plan'])
        review['source_plan_sha256'] = seed['plan_sha256']
        review['reviews']['base']['taxes']['source_driver_sha256'] = _digest(item)
        review['current_dossier_sha256'] = _digest(_model_dossier(current['dossier']))
    elif fault == 'changed_source':
        document = current['dossier']['documents'][0]
        document['text'] += '\nA new reported development needs reassessment.'
        document['sha256'] = sha256(document['text'].encode()).hexdigest()
        review['current_dossier_sha256'] = _digest(_model_dossier(current['dossier']))
    elif fault == 'different_ticker':
        current['dossier']['ticker'] = 'SYNTH-OTHER'
        review['current_dossier_sha256'] = _digest(_model_dossier(current['dossier']))
    elif fault == 'incomplete_original':
        del seed['plan']['scenarios']['bull']['taxes']
        seed = make_seed(old['dossier'], old['contract'], seed['plan'])
        review['source_plan_sha256'] = seed['plan_sha256']
    elif fault == 'changed_contract':
        current['contract']['method_contract'] += '\nNew economic requirement.'
        review['current_contract_sha256'] = _digest(current['contract'])
    before = deepcopy((old, current, seed, review))
    with pytest.raises(ValueError):
        _apply(old, current, seed, review)
    assert (old, current, seed, review) == before


@pytest.mark.parametrize('compact', [False, True])
def test_review_proposer_reaches_common_workbook_service_and_records_lineage(tmp_path, compact):
    from bellomberg.valuation.preparation_refresh import RefreshProposer
    from bellomberg.valuation.preparation_service import prepare_and_generate
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from test_sector_analysis import providers_for
    old, current, seed, review = _case()
    calls = []
    def propose(dossier, contract):
        scope = contract['preparation_refresh']['scope']
        calls.append(scope)
        if scope == 'model':
            assert dossier['prior_plan']['model'] == seed['plan']['model']
        else:
            assert dossier['prior_plan']['scenarios'][scope] == seed['plan']['scenarios'][scope]
        assert dossier['plan_projection']['prior_plan_sha256'] == seed['plan_sha256']
        assert contract['preparation_refresh']['source_plan_sha256'] == seed['plan_sha256']
        answer = {'reviews': deepcopy(review['reviews'][scope]),
                  'rationale': review['scenario_rationale'].get(scope, 'Explicitly reviewed opening facts.')}
        spec = contract['preparation_refresh']
        return _compact_answer(answer, spec['compact_wire']) if 'compact_wire' in spec else answer
    reviewer = RefreshProposer(propose, seed, old['dossier'], old['contract'],
                               compact_scopes=['base', 'bull'] if compact else [])
    bundle = prepare_sector_analysis('SYNTH-BANK', as_of=current['dossier']['as_of'], providers=providers_for('bank'))
    result = prepare_and_generate(bundle, documents=current['dossier']['documents'], propose=reviewer, output_dir=tmp_path)
    assert result['ok'], result.get('error')
    assert calls == ['model', 'bear', 'base', 'bull']
    assert result['preparation']['provenance']['refresh_review']['source_plan_sha256'] == seed['plan_sha256']
    assert result['preparation']['review_basis']['seed']['plan'] == result['preparation']['proposal']['plan']
    assert result['preparation']['proposal']['approval_status'] == 'automatic_non_approved'


def test_review_scope_invalidity_stops_before_another_paid_callback():
    from bellomberg.valuation.preparation_refresh import RefreshProposer
    old, current, seed, review = _case()
    calls = []
    def invalid(dossier, contract):
        scope = contract['preparation_refresh']['scope']; calls.append(scope)
        result = deepcopy(review['reviews'][scope])
        item = deepcopy(seed['plan']['model']['opening_common_equity'])
        item.update(valid_until=current['dossier']['as_of'], valid_until_basis=current['contract']['expiry_policy'])
        item['value'] += 1
        result['opening_common_equity'] = {'action': 'replace', 'driver': item}
        return {'reviews': result, 'rationale': 'Synthetic inconsistent opening.'}
    reviewer = RefreshProposer(invalid, seed, old['dossier'], old['contract'])
    with pytest.raises(ValueError):
        reviewer(current['dossier'], current['contract'])
    assert calls == ['model'] and reviewer.refresh_lineage is None


@pytest.mark.parametrize('change', ['text', 'observed_download_day'])
def test_changed_cited_source_requires_replacement_in_prompt_and_wire_schema(change):
    from bellomberg.valuation.preparation_ai import response_format
    from bellomberg.valuation.preparation_refresh import RefreshProposer
    old, current, seed, _ = _case()
    document = current['dossier']['documents'][0]
    if change == 'text':
        document['text'] += '\nA newly acquired source version requires explicit replacement.'
        document['sha256'] = sha256(document['text'].encode()).hexdigest()
    else:
        for context in (old, current):
            doc = context['dossier']['documents'][0]
            doc.update(published_at=None, availability_basis='observed_download',
                       available_at=context['dossier']['as_of'], document_sha256=doc['sha256'],
                       retrieval={'url': doc['url'], 'document_sha256': doc['sha256'],
                                  'retrieved_at': context['dossier']['as_of'] + 'T08:00:00+00:00'})
        seed = make_seed(old['dossier'], old['contract'], seed['plan'])
    changed = {name for name, driver in seed['plan']['model'].items()
               if document['id'] in driver['evidence_ids']}
    assert changed
    class Captured(Exception):
        pass
    def inspect(dossier, contract):
        refresh = contract['preparation_refresh']
        assert set(refresh['reuse_ineligible']) == changed
        for item in refresh['reuse_ineligible'].values():
            assert item['required_action'] == 'replace_or_unavailable'
            fields = item['changed_sources'][document['id']]
            assert fields == (['sha256', 'text'] if change == 'text' else ['available_at'])
        assert 'pending' in refresh['coordination_notice']
        schema = response_format(contract)['json_schema']['schema']
        def resolve(node):
            while '$ref' in node:
                node = schema['$defs'][node['$ref'].removeprefix('#/$defs/')]
            return node
        reviews = resolve(schema['properties']['reviews'])['properties']
        for name, choices in reviews.items():
            actions = {resolve(branch)['properties']['action']['const']
                       for branch in resolve(choices)['anyOf']}
            assert actions == ({'replace', 'unavailable'} if name in changed
                               else {'reuse', 'replace', 'unavailable'})
        raise Captured()
    with pytest.raises(Captured):
        RefreshProposer(inspect, seed, old['dossier'], old['contract'])(current['dossier'], current['contract'])


def test_refresh_transport_uses_distinct_schema_without_changing_ordinary_request(tmp_path, monkeypatch):
    from bellomberg.valuation.preparation_ai import BudgetedProposer, SYSTEM, response_format
    from bellomberg.valuation.preparation_refresh import RefreshProposer
    old, current, seed, review = _case()
    paid = BudgetedProposer(tmp_path / 'journal.db', authorized_usd=1, model='synthetic/model',
        max_tokens=16000, thinking={'type': 'adaptive'}, metadata=lambda _: pytest.fail('offline request inspection'),
        call=lambda **_: pytest.fail('offline request inspection'))
    ordinary = paid._request(current['dossier'], current['contract'])
    def inspect(dossier, contract):
        request = paid._request(dossier, contract)
        assert request['max_tokens'] == ordinary['max_tokens'] and request['model'] == ordinary['model']
        assert request['system'] != SYSTEM
        wire = response_format(contract)['json_schema']['schema']
        from bellomberg.valuation import preparation_refresh
        with monkeypatch.context() as patch:
            patch.setattr(preparation_refresh, '_shared_schema', lambda schema: deepcopy(schema))
            plain = response_format(contract)['json_schema']['schema']
        def expand(node, chain=()):
            if isinstance(node, list): return [expand(v, chain) for v in node]
            if not isinstance(node, dict): return node
            if '$ref' in node:
                assert set(node) == {'$ref'} and node['$ref'] not in chain
                return expand(wire['$defs'][node['$ref'].removeprefix('#/$defs/')], (*chain, node['$ref']))
            return {k: expand(v, chain) for k, v in node.items() if k != '$defs'}
        assert expand(wire) == plain  # Every actual bank-scope constraint survives sharing.
        names = contract['preparation_refresh']['driver_hashes']
        assert set(wire['properties']['reviews']['properties']) == set(names)
        scope = contract['preparation_refresh']['scope']
        return {'reviews': deepcopy(review['reviews'][scope]),
                'rationale': review['scenario_rationale'].get(scope, 'Reviewed opening.')}
    RefreshProposer(inspect, seed, old['dossier'], old['contract'])(current['dossier'], current['contract'])
    assert paid._request(current['dossier'], current['contract']) == ordinary
    assert paid.summary()['requests'] == 0


@pytest.mark.parametrize('compact_resume', [False, True])
def test_interrupted_refresh_reuses_paid_scope_responses_from_journal(tmp_path, compact_resume):
    import json
    from types import SimpleNamespace
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    from bellomberg.valuation.preparation_refresh import RefreshProposer
    old, current, seed, review = _case()
    calls = []
    def provider(**request):
        spec = json.loads(request['messages'][0]['content'])['contract']['preparation_refresh']
        scope = spec['scope']
        calls.append(scope)
        answer = {'reviews': review['reviews'][scope],
                  'rationale': review['scenario_rationale'].get(scope, 'Opening reviewed.')}
        if 'compact_wire' in spec:
            answer = _compact_answer(answer, spec['compact_wire'])
        return SimpleNamespace(id='synthetic-refresh-' + scope, model=request['model'],
            provider='synthetic', stop_reason='end_turn', usage=SimpleNamespace(cost_usd=.01),
            content=[SimpleNamespace(type='text', text=json.dumps(answer))])
    def journal():
        return BudgetedProposer(tmp_path / 'journal.db', authorized_usd=3, model='synthetic/model',
            max_tokens=16000, thinking={'type': 'adaptive'}, call=provider,
            metadata=lambda _: {'id': 'synthetic/model', 'context_length': 1000000,
                                 'pricing': {'prompt': '0.000001', 'completion': '0.000001'}})
    paid = journal()
    def interrupted(dossier, contract):
        if contract['preparation_refresh']['scope'] == 'base':
            raise OSError('synthetic interruption before a new request')
        return paid(dossier, contract)
    with pytest.raises(OSError, match='before a new request'):
        RefreshProposer(interrupted, seed, old['dossier'], old['contract'])(current['dossier'], current['contract'])
    assert calls == ['model', 'bear']
    restarted = journal()
    options = {'compact_scopes': ['base', 'bull']} if compact_resume else {}
    reviewer = RefreshProposer(restarted, seed, old['dossier'], old['contract'], **options)
    reviewer(current['dossier'], current['contract'])
    assert calls == ['model', 'bear', 'base', 'bull']
    assert restarted.summary()['requests'] == 4 and restarted.summary()['spent_usd'] == .04


def _compact_answer(answer, refs):
    decisions = {}
    for name, choice in answer['reviews'].items():
        item = deepcopy(choice)
        if item['action'] == 'reuse':
            item.pop('source_driver_sha256')
            item['evidence_refs'] = [refs['sources'].index(ident) for ident in item.pop('evidence_ids')]
        decisions[str(refs['drivers'].index(name))] = item
    return {'reviews': decisions, 'rationale': answer['rationale']}


@pytest.mark.parametrize('method', ['bank', 'operating'])
def test_compact_refs_preserve_complete_choices_plan_and_wire_lineage(method):
    from jsonschema import Draft202012Validator
    from bellomberg.valuation.preparation_ai import response_format
    from bellomberg.valuation.preparation_refresh import RefreshProposer
    old, current, seed, review = _case(method)
    name = next(iter(seed['plan']['scenarios']['base']))
    replacement = deepcopy(seed['plan']['scenarios']['base'][name])
    replacement.update(valid_until=current['dossier']['as_of'], valid_until_basis=current['contract']['expiry_policy'])
    review['reviews']['base'][name] = {'action': 'replace', 'driver': replacement}
    expected, _ = _apply(old, current, seed, review)
    wires = {}
    def provider(dossier, contract):
        spec = contract['preparation_refresh']; scope = spec['scope']
        answer = {'reviews': deepcopy(review['reviews'][scope]),
                  'rationale': review['scenario_rationale'].get(scope, 'Opening reviewed.')}
        if scope in ('base', 'bull'):
            refs = spec['compact_wire']
            assert refs['version'] == 1 and refs['drivers'] == sorted(spec['driver_hashes'])
            answer = _compact_answer(answer, refs)
            schema = response_format(contract)['json_schema']['schema']
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(answer)
            wires[scope] = deepcopy((answer, refs))
            # A callable must not rebind the tables retained by the coordinator.
            refs['drivers'].reverse()
        else:
            assert 'compact_wire' not in spec
        return answer
    reviewer = RefreshProposer(provider, seed, old['dossier'], old['contract'], compact_scopes=['base', 'bull'])
    assert reviewer(current['dossier'], current['contract']) == expected['plan']
    assert reviewer.refresh_lineage['review']['reviews'] == review['reviews']
    for request in reviewer.refresh_lineage['requests']:
        if request['scope'] in wires:
            answer, refs = wires[request['scope']]
            assert request['wire_response'] == answer and request['compact_wire'] == refs
            assert request['answer_sha256'] == _digest(answer)
        else:
            assert 'compact_wire' not in request and 'wire_response' not in request


@pytest.mark.parametrize('fault', ['unknown_driver', 'boolean_driver', 'duplicate_driver', 'missing_driver',
    'unknown_source', 'boolean_source', 'duplicate_source', 'extra_key', 'old_wire', 'unavailable', 'changed_source'])
def test_compact_review_rejects_ambiguous_refs_and_gaps_before_another_call(fault):
    from bellomberg.valuation.preparation_refresh import RefreshProposer
    old, current, seed, review = _case()
    if fault == 'changed_source':
        doc = current['dossier']['documents'][0]
        doc['text'] += '\nNew source version.'; doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    calls = []
    def provider(dossier, contract):
        spec = contract['preparation_refresh']; calls.append(spec['scope'])
        answer = _compact_answer({'reviews': review['reviews']['model'], 'rationale': 'Explicit opening review.'}, spec['compact_wire'])
        item = answer['reviews']['0']
        if fault == 'unknown_driver': answer['reviews']['9999'] = answer['reviews'].pop('0')
        elif fault == 'boolean_driver': answer['reviews'][False] = answer['reviews'].pop('0')
        elif fault == 'duplicate_driver': answer['reviews']['00'] = deepcopy(item)
        elif fault == 'missing_driver': answer['reviews'].pop('0')
        elif fault == 'unknown_source': item['evidence_refs'] = [9999]
        elif fault == 'boolean_source': item['evidence_refs'] = [False]
        elif fault == 'duplicate_source': item['evidence_refs'] *= 2
        elif fault == 'extra_key': item['evidence_ids'] = ['invented']
        elif fault == 'old_wire': answer['reviews'] = deepcopy(review['reviews']['model'])
        elif fault == 'unavailable': answer['reviews']['0'] = {'action': 'unavailable', 'reason': 'Required statement unavailable.'}
        return answer
    reviewer = RefreshProposer(provider, seed, old['dossier'], old['contract'], compact_scopes=['model'])
    with pytest.raises(ValueError):
        reviewer(current['dossier'], current['contract'])
    assert calls == ['model'] and reviewer.refresh_lineage is None


def test_shared_schema_expands_to_identical_constraints_without_reference_cycles():
    from bellomberg.valuation.preparation_refresh import _shared_schema
    from bellomberg.valuation.preparation_ai import _json
    field = {'type': 'object', 'properties': {
        'value': {'type': 'number'}, 'reason': {'type': 'string', 'minLength': 1}},
        'required': ['value', 'reason'], 'additionalProperties': False}
    original = {'type': 'object', 'properties': {str(i): deepcopy(field) for i in range(20)},
                'required': [str(i) for i in range(20)], 'additionalProperties': False}
    shared = _shared_schema(original)
    def expand(node, chain=()):
        if isinstance(node, list): return [expand(v, chain) for v in node]
        if not isinstance(node, dict): return node
        if '$ref' in node:
            assert set(node) == {'$ref'} and node['$ref'] not in chain
            name = node['$ref'].removeprefix('#/$defs/')
            return expand(shared['$defs'][name], (*chain, node['$ref']))
        return {k: expand(v, chain) for k, v in node.items() if k != '$defs'}
    assert expand(shared) == original
    assert len(_json(shared)) < len(_json(original))


def test_future_literal_source_expiry_is_preserved_instead_of_extended():
    old, current, seed, review = _case()
    expiry = (date.fromisoformat(current['dossier']['as_of']) + timedelta(days=5)).isoformat()
    quoted_expiry = 'Synthetic reported policy expires ' + expiry + '.'
    for context in (old, current):
        doc = context['dossier']['documents'][0]
        doc['text'] += '\n' + quoted_expiry
        doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    item = seed['plan']['scenarios']['base']['taxes']
    item.update(valid_until=expiry, valid_until_basis=quoted_expiry)
    seed = make_seed(old['dossier'], old['contract'], seed['plan'])
    review.update(source_plan_sha256=seed['plan_sha256'],
                  current_dossier_sha256=_digest(_model_dossier(current['dossier'])))
    review['reviews']['base']['taxes']['source_driver_sha256'] = _digest(item)
    renewed, _ = _apply(old, current, seed, review)
    actual = renewed['plan']['scenarios']['base']['taxes']
    assert actual['valid_until'] == expiry and actual['valid_until_basis'] == quoted_expiry


def test_refresh_view_declares_provider_omissions_without_changing_source_catalog():
    from bellomberg.valuation.preparation_refresh import _review_view
    _, current, _, _ = _case()
    dossier = current['dossier']
    dossier['document_acquisition'] = {'status': 'partial', 'issues': ['Explicit missing source'],
                                       'acquired_document_index': [{'id': 'synthetic-index'}]}
    before = deepcopy(dossier)
    view = _review_view(dossier, None)
    assert dossier == before and view['documents'] == dossier['documents']
    assert view['document_acquisition']['issues'] == ['Explicit missing source']
    fields = [entry['field'] for entry in view['review_view_omissions']]
    assert 'document_acquisition.acquired_document_index' in fields
    for name in ('profile', 'financials', 'filings'):
        if 'data' in dossier['acquired_sources'][name]:
            assert 'data' not in view['acquired_sources'][name]
            assert 'acquired_sources.' + name + '.data' in fields
