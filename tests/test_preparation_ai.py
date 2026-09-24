"""Budget and recovery tests; no network or production database access."""
from types import SimpleNamespace
from copy import deepcopy
from hashlib import sha256
import json
import pytest


def _metadata():
    return {"id": "synthetic/model", "context_length": 100000,
            "pricing": {"prompt": "0.00001", "completion": "0.00002"}}


def _proposer(tmp_path, *, limit=10, call=None):
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    def response(**kwargs):
        return SimpleNamespace(id="synthetic-response", model="synthetic/model", stop_reason="end_turn",
            provider="synthetic", usage=SimpleNamespace(cost_usd=.2),
            content=[SimpleNamespace(type="text", text='{"model":{},"scenarios":{}}')])
    return BudgetedProposer(tmp_path / "budget.db", authorized_usd=limit,
        model="synthetic/model", max_tokens=16000, thinking={"type": "adaptive"},
        metadata=lambda _: _metadata(), call=call or response)


def test_paid_response_reused_after_restart_with_measured_cost(tmp_path):
    seen = []
    p = _proposer(tmp_path)
    original = p.call
    p.call = lambda **kw: (seen.append(kw), original(**kw))[1]
    first = p({"ticker": "SYNTH"}, {"schema": {}})
    restarted = _proposer(tmp_path, call=lambda **_: pytest.fail("repeat paid request"))
    assert restarted({"ticker": "SYNTH"}, {"schema": {}}) == first
    assert len(seen) == 1
    assert seen[0]["provider_max_price"] == {"prompt": 10., "completion": 20., "request": 0.}
    assert seen[0]["max_tokens"] == 16000
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert p.summary()["spent_usd"] == .2
    assert p.summary()["unknown_requests"] == 0


def _receipted_dossier():
    from hashlib import sha256
    text = "Synthetic annual revenue is 100 EUR million."
    digest = sha256(text.encode()).hexdigest()
    url = "https://example.org/synthetic/annual"
    return {"as_of": "2026-09-10", "documents": [{"id": "annual", "url": url,
        "text": text, "sha256": digest, "document_sha256": digest,
        "published_at": None, "available_at": "2026-09-09",
        "availability_basis": "observed_download", "retrieval": {"url": url,
            "document_sha256": digest, "retrieved_at": "2026-09-09T08:00:00Z"}}]}


def test_receipt_clock_change_reuses_paid_response_without_mutating_provenance(tmp_path):
    from copy import deepcopy
    calls, p = [], _proposer(tmp_path)
    call = p.call
    p.call = lambda **kw: (calls.append(kw), call(**kw))[1]
    dossier = _receipted_dossier()
    original = deepcopy(dossier)
    answer = p(dossier, {"schema": {}})
    assert dossier == original
    sent = json.loads(calls[0]["messages"][0]["content"])["dossier"]["documents"][0]
    assert "retrieved_at" not in sent["retrieval"]
    assert sent["available_at"] == "2026-09-09" and sent["published_at"] is None
    dossier["documents"][0]["retrieval"]["retrieved_at"] = "2026-09-09T10:00:00Z"
    restarted = _proposer(tmp_path, call=lambda **_: pytest.fail("repeat paid request"))
    restarted.metadata = lambda _: pytest.fail("cache must not fetch live pricing")
    assert restarted(dossier, {"schema": {}}) == answer
    assert len(calls) == 1 and restarted.summary()["requests"] == 1
    assert restarted.summary()["spent_usd"] == .2


@pytest.mark.parametrize("change", ["availability_day", "text", "raw_bytes", "contract", "completed_driver"])
def test_economic_source_dates_and_decisions_remain_distinct_paid_requests(tmp_path, change):
    from copy import deepcopy
    from hashlib import sha256
    p, dossier, contract = _proposer(tmp_path), _receipted_dossier(), {"schema": {}}
    p(dossier, contract)
    changed = deepcopy(dossier)
    doc = changed["documents"][0]
    if change == "availability_day":
        doc["available_at"] = "2026-09-10"
        doc["retrieval"]["retrieved_at"] = "2026-09-10T08:00:00Z"
    elif change == "text":
        doc["text"] = "Synthetic annual revenue is 101 EUR million."
        doc["sha256"] = sha256(doc["text"].encode()).hexdigest()
    elif change == "raw_bytes":
        doc["document_sha256"] = doc["retrieval"]["document_sha256"] = "1" * 64
    elif change == "contract":
        contract["schema"]["new_driver"] = "required"
    else:
        changed["completed_plan"] = {"model": {"growth": {"value": .06}}}
    p(changed, contract)
    assert p.summary()["requests"] == 2 and p.summary()["spent_usd"] == .4


@pytest.mark.parametrize("problem", ["missing_availability", "false_availability", "invalid_receipt", "future"])
def test_receipt_projection_requires_verified_explicit_availability(tmp_path, problem):
    p, dossier = _proposer(tmp_path), _receipted_dossier()
    p.metadata = lambda _: pytest.fail("invalid evidence must not fetch pricing")
    doc = dossier["documents"][0]
    if problem == "missing_availability": doc.pop("available_at")
    elif problem == "false_availability": doc["available_at"] = "2026-09-08"
    elif problem == "invalid_receipt": doc["retrieval"]["document_sha256"] = "0" * 64
    else:
        doc["available_at"] = "2026-09-11"
        doc["retrieval"]["retrieved_at"] = "2026-09-11T08:00:00Z"
    with pytest.raises(ValueError):
        p(dossier, {})
    assert p.summary()["requests"] == 0


@pytest.mark.parametrize("state", ["received", "truncated", "unknown", "reserved"])
@pytest.mark.parametrize("legacy_json_mode", [False, True])
def test_legacy_literal_receipt_request_is_reused_without_rewriting_journal(tmp_path, state, legacy_json_mode):
    from hashlib import sha256
    from bellomberg.valuation.preparation_ai import _json
    p, dossier = _proposer(tmp_path), _acquisition_dossier()
    answer = p(dossier, {})
    # Reconstruct the literal pre-projection request, preserving its actual key.
    with p._db() as db:
        row = db.execute("SELECT * FROM requests").fetchone()
        request = json.loads(row["request"])
        if legacy_json_mode:
            request.pop("response_format")
        request["messages"][0]["content"] = _json({"dossier": dossier, "contract": {}})
        literal = {k: v for k, v in request.items() if k != "provider_max_price"}
        key = sha256(_json(literal).encode()).hexdigest()
        receipt = json.loads(row["receipt"])
        if state == "truncated": receipt["stop_reason"] = "max_tokens"
        db.execute("UPDATE requests SET key=?,request=?,state=?,cost=?,receipt=?",
            (key, _json(request), "received" if state == "truncated" else state,
             None if state in ("unknown", "reserved") else row["cost"], _json(receipt)))
        before = dict(db.execute("SELECT * FROM requests").fetchone())
    summary = p.summary()
    dossier["documents"][0]["retrieval"]["retrieved_at"] = "2026-09-09T10:00:00Z"
    dossier["document_acquisition"]["coverage"].update(downloaded=0, reused=1, download_attempted=0)
    restarted = _proposer(tmp_path, call=lambda **_: pytest.fail("legacy paid retry"))
    restarted.metadata = lambda _: pytest.fail("legacy request must not fetch pricing")
    if state == "received": assert restarted(dossier, {}) == answer
    elif state == "truncated":
        with pytest.raises(ValueError, match="max_tokens"): restarted(dossier, {})
    else:
        with pytest.raises(RuntimeError, match="unresolved"): restarted(dossier, {})
    assert restarted.summary() == summary
    with p._db() as db:
        assert [dict(row) for row in db.execute("SELECT * FROM requests")] == [before]


def _acquisition_dossier():
    dossier = _receipted_dossier()
    dossier["document_acquisition"] = {"status": "ready", "issues": [], "coverage": {
        "downloaded": 1, "reused": 0, "download_attempted": 1, "deduplicated": 0,
        "accepted": 1, "excluded": 0, "limited": 0, "max_download_attempts": 4,
        "max_documents": 4, "catalog_checked": True, "catalog_status": "ok",
        "selection_policy": "opening_annual_comparative"}}
    return dossier


def test_download_and_archive_transport_counts_reuse_request_keep_full_provenance(tmp_path):
    from copy import deepcopy
    dossier, p = _acquisition_dossier(), _proposer(tmp_path)
    before = deepcopy(dossier)
    answer = p(dossier, {})
    assert dossier == before
    dossier["document_acquisition"]["coverage"].update(downloaded=0, reused=1,
        download_attempted=0, deduplicated=1)
    restarted = _proposer(tmp_path, call=lambda **_: pytest.fail("transport-only paid retry"))
    assert restarted(dossier, {}) == answer
    assert restarted.summary()["requests"] == 1


@pytest.mark.parametrize("field,value", [("catalog_status", "parziale"), ("catalog_checked", False),
    ("excluded", 1), ("limited", 1), ("max_documents", 3), ("selection_policy", "latest")])
def test_catalog_coverage_and_limits_remain_request_identity(tmp_path, field, value):
    dossier, p = _acquisition_dossier(), _proposer(tmp_path)
    p(dossier, {})
    dossier["document_acquisition"]["coverage"][field] = value
    p(dossier, {})
    assert p.summary()["requests"] == 2


@pytest.mark.parametrize("field,value", [("status", "incomplete"), ("issues", [{"reason": "unverified filing"}])])
def test_acquisition_status_and_gaps_are_not_erased_from_identity(tmp_path, field, value):
    dossier, p = _acquisition_dossier(), _proposer(tmp_path)
    p(dossier, {})
    dossier["document_acquisition"][field] = value
    p(dossier, {})
    assert p.summary()["requests"] == 2


@pytest.mark.parametrize("field,value", [("downloaded", -1), ("reused", True),
    ("download_attempted", 0), ("accepted", 2), ("max_download_attempts", 0)])
def test_invalid_transport_counts_block_before_pricing(tmp_path, field, value):
    dossier, p = _acquisition_dossier(), _proposer(tmp_path)
    dossier["document_acquisition"]["coverage"][field] = value
    p.metadata = lambda _: pytest.fail("invalid report must not fetch pricing")
    with pytest.raises(ValueError, match="transport counters"):
        p(dossier, {})
    assert p.summary()["requests"] == 0


def test_insufficient_budget_stops_before_network(tmp_path):
    p = _proposer(tmp_path, limit=.5, call=lambda **_: pytest.fail("over budget"))
    with pytest.raises(RuntimeError, match="budget"):
        p({}, {})
    assert p.summary()["requests"] == 0


@pytest.mark.parametrize("body", [
    '{"drivers": {}}}, "rationale": "synthetic"}',
    '{"drivers": {}, "drivers": {"unexpected": 1}}',
    '{"drivers": {"value": NaN}}',
])
def test_invalid_json_is_preserved_charged_once_and_never_silently_repaired(tmp_path, body):
    p = _proposer(tmp_path)
    original = p.call
    def malformed(**kw):
        result = original(**kw)
        result.content = [SimpleNamespace(type="text", text=body)]
        return result
    p.call = malformed
    with pytest.raises(ValueError):
        p({}, {})
    restarted = _proposer(tmp_path, call=lambda **_: pytest.fail("malformed response repaid"))
    with pytest.raises(ValueError):
        restarted({}, {})
    with restarted._db() as db:
        row = db.execute("SELECT response,cost FROM requests").fetchone()
        assert row["response"] == body and row["cost"] == 200000000
    assert restarted.summary()["requests"] == 1


@pytest.mark.parametrize("cost", [None, float("nan"), -1, True])
def test_unknown_invalid_cost_is_never_zero_and_blocks_new_call(tmp_path, cost):
    p = _proposer(tmp_path)
    call = p.call
    def unknown(**kw):
        result = call(**kw)
        result.usage.cost_usd = cost
        return result
    p.call = unknown
    with pytest.raises(RuntimeError, match="cost"):
        p({}, {})
    assert p.summary()["spent_usd"] is None
    assert p.summary()["unknown_requests"] == 1
    with pytest.raises(RuntimeError, match="cost|unresolved"):
        p({"new": True}, {})


def test_timeout_reservation_survives_restart_and_forbids_retry(tmp_path):
    def timeout(**_):
        raise TimeoutError("synthetic interrupted response")
    p = _proposer(tmp_path, call=timeout)
    with pytest.raises(TimeoutError):
        p({}, {})
    restarted = _proposer(tmp_path, call=lambda **_: pytest.fail("ambiguous retry"))
    with pytest.raises(RuntimeError, match="unresolved"):
        restarted({}, {})
    assert restarted.summary()["reserved_usd"] == 1.32


def test_authorized_cap_cannot_be_raised_by_reopening(tmp_path):
    _proposer(tmp_path)
    with pytest.raises(ValueError, match="authorization"):
        _proposer(tmp_path, limit=11)


def test_oversized_dossier_is_rejected_before_paid_request(tmp_path):
    p = _proposer(tmp_path, call=lambda **_: pytest.fail("oversized paid call"))
    with pytest.raises(ValueError, match="dossier"):
        p({"text": "x" * 150000}, {})
    assert p.summary()["requests"] == 0


def test_context_guard_counts_model_text_not_double_escaped_transport_json(tmp_path):
    p = _proposer(tmp_path)
    assert p({"text": '"' * 28000}, {}) == {"model": {}, "scenarios": {}}


def test_price_filter_reaches_wire_without_changing_ordinary_calls():
    from bellomberg.core.llm_client import costruisci_corpo
    kw = {"model": "synthetic/model", "max_tokens": 16000, "messages": [{"role": "user", "content": "source"}]}
    cap = {"prompt": 10., "completion": 20., "request": 0.}
    assert "provider" not in costruisci_corpo(**kw)
    assert costruisci_corpo(**kw, provider_max_price=cap)["provider"]["max_price"] == cap


def test_json_format_is_required_on_wire_and_preserves_price_ceiling():
    from bellomberg.core.llm_client import costruisci_corpo
    kw = {"model": "synthetic/model", "max_tokens": 16000,
          "messages": [{"role": "user", "content": "Return a JSON object"}]}
    cap = {"prompt": 10., "completion": 20., "request": 0.}
    assert "response_format" not in costruisci_corpo(**kw)
    body = costruisci_corpo(**kw, provider_max_price=cap, response_format={"type": "json_object"})
    assert body["response_format"] == {"type": "json_object"}
    assert body["provider"] == {"max_price": cap, "require_parameters": True}
    assert body["max_tokens"] == 16000


def test_staged_json_schema_reaches_wire_and_keeps_missing_sources_explicit():
    from copy import deepcopy
    from bellomberg.core.llm_client import costruisci_corpo
    from bellomberg.valuation.preparation_ai import response_format
    from bellomberg.valuation.operating_adapter import SCHEMA
    names = ["perimeter", "calendar", "quotation"]
    contract = {"schema": {name: SCHEMA[name] for name in names},
                "preparation_stage": {"drivers": names, "scope": "model"}}
    fmt = response_format(contract)
    # Meta strict mode requires every property and closed objects, which would
    # forbid optional proof fields and open economic contracts.
    assert fmt["json_schema"]["strict"] is False
    schema = fmt["json_schema"]["schema"]
    assert schema["required"] == ["drivers", "rationale"]
    assert schema["additionalProperties"] is False
    drivers = schema["properties"]["drivers"]
    assert set(drivers["properties"]) == set(names) and drivers["required"] == names
    assert drivers["properties"]["quotation"]["anyOf"][1] == {"type": "null"}
    assert drivers["properties"]["quotation"]["anyOf"][0]["required"][-1] == "facts"
    assert drivers["properties"]["perimeter"]["anyOf"][0]["properties"]["kind"] == {"enum": ["analyst_estimate"]}
    saved = deepcopy(fmt)
    body = costruisci_corpo(model="synthetic/model", max_tokens=16000, messages=[], response_format=fmt)
    assert body["response_format"] == saved and body["provider"]["require_parameters"] is True
    fmt["json_schema"]["schema"]["required"].append("mutation")
    assert body["response_format"] == saved


def test_bank_legal_wire_contract_matches_compiler_without_guessing_field_names():
    from bellomberg.valuation.bank_adapter import SCHEMA
    from bellomberg.valuation.input_preparation import _bank_schema
    from bellomberg.valuation.preparation_ai import response_format
    contract = {"schema": SCHEMA, "preparation_stage": {"drivers": ["legal_structure"]}}
    wire = response_format(contract)["json_schema"]["schema"]
    legal = wire["properties"]["drivers"]["properties"]["legal_structure"]["anyOf"][0]["properties"]["value"]
    # A provider must receive the same closed legal-entity shape the compiler
    # requires; the old generic object allowed parent/group and extra names.
    assert set(legal["required"]) == {"parent_entity", "subsidiaries", "capital_basis", "accounting_basis"}
    assert legal["additionalProperties"] is False
    properties = legal["properties"]
    child = properties["subsidiaries"]["items"]
    assert properties["subsidiaries"]["minItems"] == 1
    assert set(child["required"]) == {"id", "regime"} and child["additionalProperties"] is False
    value = {"parent_entity": "SYNTH-PARENT", "subsidiaries": [{"id": "SYNTH-BANK", "regime": "Synthetic regime"}],
             "capital_basis": properties["capital_basis"]["const"],
             "accounting_basis": properties["accounting_basis"]["const"]}
    _, _, issues = _bank_schema({"model": {"legal_structure": {"value": value}}}, {"entity": "SYNTH-GROUP"})
    assert not issues


@pytest.mark.parametrize("strict", [None, "false", 0, 1])
def test_json_schema_requires_an_explicit_boolean_mode(strict):
    from bellomberg.core.llm_client import costruisci_corpo
    fmt = {"type": "json_schema", "json_schema": {"name": "synthetic", "strict": strict,
           "schema": {"type": "object", "properties": {}, "additionalProperties": False}}}
    with pytest.raises(ValueError, match="response_format"):
        costruisci_corpo(model="synthetic/model", max_tokens=16000, messages=[], response_format=fmt)


@pytest.mark.parametrize("invalid", [{}, {"type": "text"}, {"type": "json_object", "extra": True}, "json"])
def test_unsupported_response_format_is_never_silently_ignored(invalid):
    from bellomberg.core.llm_client import costruisci_corpo
    with pytest.raises(ValueError, match="response_format"):
        costruisci_corpo(model="synthetic/model", max_tokens=16000, messages=[], response_format=invalid)


def test_staged_plan_reuses_paid_stages_and_never_changes_model_limits(tmp_path):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _operating_plan, _bundle, _documents
    full = _operating_plan()
    calls = []
    def call(**kw):
        calls.append(kw)
        stage = json.loads(kw["messages"][0]["content"])["contract"]["preparation_stage"]
        scope = stage["scope"]
        source = full["model"] if scope == "model" else full["scenarios"][scope]
        out = {"drivers": {name: source[name] for name in stage["drivers"]},
               "rationale": "Synthetic stage " + scope}
        return SimpleNamespace(id="stage-"+str(len(calls)), model=kw["model"], provider="synthetic",
            stop_reason="end_turn", usage=SimpleNamespace(cost_usd=.02),
            content=[SimpleNamespace(type="text", text=json.dumps(out))])
    proposer = StagedProposer(_proposer(tmp_path, call=call))
    one = prepare_method_inputs(_bundle(), documents=_documents(), propose=proposer)
    assert one["status"] == "prepared", one["issues"]
    count = len(calls)
    two = prepare_method_inputs(_bundle(), documents=_documents(), propose=proposer)
    assert two["status"] == "prepared" and len(calls) == count
    assert count > 1 and all(row["max_tokens"] == 16000 for row in calls)


def _supplemental_snapshot_case():
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _bundle, _documents
    captured = {}
    bundle, documents = _bundle(), _documents()
    def capture(dossier, contract):
        captured.update(dossier=deepcopy(dossier), contract=deepcopy(contract))
    prepare_method_inputs(bundle, documents=documents, propose=capture)
    extra = {"id": "supplement-1", "url": "https://example.org/issuer/supplement",
             "published_at": documents[0]["published_at"], "text": "Synthetic supplementary disclosure."}
    extra["sha256"] = sha256(extra["text"].encode()).hexdigest()
    extended = deepcopy(captured["dossier"])
    extended["documents"].append(extra)
    return bundle, captured, extended


def test_added_sources_reuse_exact_paid_opening_and_reach_every_forecast(tmp_path):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _operating_plan
    bundle, old, extended = _supplemental_snapshot_case()
    full, calls = _operating_plan(), []
    def call(**kw):
        request = json.loads(kw["messages"][0]["content"])
        calls.append(request)
        stage = request["contract"]["preparation_stage"]
        scope = stage["scope"]
        source = full["model"] if scope == "model" else full["scenarios"][scope]
        answer = {"drivers": {name: source[name] for name in stage["drivers"]},
                  "rationale": "Synthetic scoped stage " + scope}
        return SimpleNamespace(id="source-stage-" + str(len(calls)), model=kw["model"], provider="synthetic",
            stop_reason="end_turn", usage=SimpleNamespace(cost_usd=.02),
            content=[SimpleNamespace(type="text", text=json.dumps(answer))])
    paid = _proposer(tmp_path, call=call)
    def opening_only(dossier, contract):
        if contract["preparation_stage"]["scope"] != "model":
            raise RuntimeError("offline stop before forecast")
        return paid(dossier, contract)
    first = StagedProposer(opening_only)
    with pytest.raises(RuntimeError, match="offline stop"):
        first(old["dossier"], old["contract"])
    assert len(calls) == 1
    snapshot = deepcopy(old["dossier"])
    proposer = StagedProposer(paid, opening_dossier=snapshot)
    result = prepare_method_inputs(bundle, documents=extended["documents"], propose=proposer)
    assert result["status"] == "prepared", result["issues"]
    assert snapshot == old["dossier"]
    assert sum(c["contract"]["preparation_stage"]["scope"] == "model" for c in calls) == 1
    for request in calls[1:]:
        assert "bank_ledger_semantics" not in request["contract"]["preparation_stage"]
        assert "supplement-1" in {d["id"] for d in request["dossier"]["documents"]}
        assert request["dossier"]["source_extension"]["added_document_ids"] == ["supplement-1"]
        assert request["dossier"]["completed_plan"]["model"] == full["model"]


@pytest.mark.parametrize('change_excerpts', [False, 'full', 'excerpts', 'automatic'])
def test_supplemental_sources_preserve_paid_forecast_request_as_well_as_opening(tmp_path, change_excerpts):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from test_input_preparation import _operating_plan
    _, old, middle = _supplemental_snapshot_case()
    if change_excerpts == 'automatic':
        # Same source in both snapshots; layout alone makes the raw request too big.
        for dossier in (old['dossier'], middle):
            document = dossier['documents'][0]
            document['text'] += '\n \n' * 120000
            document['sha256'] = sha256(document['text'].encode()).hexdigest()
    full, calls = _operating_plan(), []
    def full_manifest(dossier):
        entries = []
        for doc in dossier['documents']:
            try:
                json.loads(doc['text'])
                continue
            except ValueError:
                pass
            digest = sha256(doc['text'].encode()).hexdigest()
            entries.append({'source_id': doc['id'], 'original_text_sha256_utf8': digest,
                'excerpts': [{'theme': 'synthetic_evidence', 'char_start': 0,
                             'char_end_exclusive': len(doc['text']), 'excerpt_sha256_utf8': digest}]})
        return entries
    opening_manifest = full_manifest(old['dossier']) if change_excerpts in ('full', 'excerpts') else None
    def call(**kw):
        payload = json.loads(kw['messages'][0]['content']); calls.append(payload)
        stage = payload['contract']['preparation_stage']; scope = stage['scope']
        source = full['model'] if scope == 'model' else full['scenarios'][scope]
        answer = {'drivers': {name: source[name] for name in stage['drivers']}, 'rationale': 'Synthetic immutable stage'}
        return SimpleNamespace(id='snapshot-'+str(len(calls)), model=kw['model'], provider='synthetic',
            stop_reason='end_turn', usage=SimpleNamespace(cost_usd=.02),
            content=[SimpleNamespace(type='text', text=json.dumps(answer))])
    paid = _proposer(tmp_path, call=call)
    paid.automatic_sections = change_excerpts == 'automatic'
    # This test deliberately groups all forecast drivers in one synthetic call.
    # Production keeps its conservative context guard and configured limits.
    paid.metadata = lambda _: {**_metadata(), 'context_length': 300000}
    seen = []
    def first_two(dossier, contract):
        seen.append(contract['preparation_stage'])
        if len(seen) == 3: raise RuntimeError('offline pause after paid forecast')
        return paid(dossier, contract)
    first_two.prepare_context = paid.prepare_context
    with pytest.raises(RuntimeError, match='offline pause'):
        StagedProposer(first_two, drivers_per_stage=100, opening_dossier=old['dossier'],
                       opening_excerpt_manifest=opening_manifest,
                       forecast_excerpt_manifest=full_manifest(middle) if change_excerpts == 'excerpts' else None)(middle, old['contract'])
    assert len(calls) == 2
    extended = deepcopy(middle)
    extra = deepcopy(extended['documents'][-1]); extra.update(id='supplement-2', url='https://example.org/issuer/second')
    extended['documents'].append(extra)
    history = [{'scope': 'bear', 'drivers': seen[1]['drivers'], 'dossier': middle}]
    if change_excerpts:
        history[0]['excerpt_manifest'] = full_manifest(middle) if change_excerpts == 'excerpts' else None
    saved = deepcopy(history)
    result = StagedProposer(paid, drivers_per_stage=100, opening_dossier=old['dossier'],
                           opening_excerpt_manifest=opening_manifest,
                           forecast_excerpt_manifest=full_manifest(extended) if change_excerpts in ('full', 'excerpts') else None,
                           stage_dossiers=history)(extended, old['contract'])
    assert result['scenarios']['bear'] == full['scenarios']['bear']
    assert history == saved
    assert sum(row['contract']['preparation_stage']['scope']=='model' for row in calls) == 1
    assert sum(row['contract']['preparation_stage']==seen[1] for row in calls) == 1
    assert 'supplement-2' not in {d['id'] for d in calls[1]['dossier']['documents']}
    for row in calls[2:]:
        assert 'supplement-2' in {d['id'] for d in row['dossier']['documents']}
        assert row['dossier']['source_extension']['added_document_ids'] == ['supplement-1', 'supplement-2']
        if change_excerpts:
            assert 'SOURCE EXCERPT' in row['dossier']['documents'][0]['text']


@pytest.mark.parametrize('problem', ['context', 'changed_source', 'missing_source', 'duplicate_source',
    'omits_opening', 'duplicate_stage', 'invalid_scope', 'duplicate_driver', 'invalid_entry', 'invalid_manifest'])
def test_stage_snapshots_reject_changed_sources_or_ambiguous_identity_before_any_call(problem):
    from bellomberg.valuation.preparation_ai import StagedProposer
    _, old, extended = _supplemental_snapshot_case()
    saved = {'scope': 'bear', 'drivers': ['growth'], 'dossier': deepcopy(extended)}
    history = [saved]
    if problem == 'context': saved['dossier']['as_of'] = '2026-01-01'
    elif problem == 'changed_source':
        doc = saved['dossier']['documents'][-1]
        doc['text'] += ' Conflicting source.'
        doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
    elif problem == 'missing_source': extended['documents'].pop()
    elif problem == 'duplicate_source': saved['dossier']['documents'].append(deepcopy(saved['dossier']['documents'][-1]))
    elif problem == 'omits_opening': saved['dossier']['documents'].pop(0)
    elif problem == 'duplicate_stage': history.append(deepcopy(saved))
    elif problem == 'invalid_scope': saved['scope'] = 'model'
    elif problem == 'duplicate_driver': saved['drivers'] *= 2
    elif problem == 'invalid_manifest': saved['excerpt_manifest'] = []
    else: saved['untracked'] = True
    with pytest.raises(ValueError, match='stage snapshot'):
        StagedProposer(lambda *_: pytest.fail('unexpected provider call'), opening_dossier=old['dossier'],
                       stage_dossiers=history)(extended, old['contract'])


def test_stage_snapshots_require_opening_and_exact_forecast_partition():
    from bellomberg.valuation.preparation_ai import StagedProposer
    from test_input_preparation import _operating_plan
    _, old, extended = _supplemental_snapshot_case()
    saved = {'scope': 'bear', 'drivers': ['not_a_driver'], 'dossier': extended}
    with pytest.raises(ValueError, match='explicit opening snapshot'):
        StagedProposer(lambda *_: pytest.fail('unexpected provider call'), stage_dossiers=[saved])
    calls = []
    def opening_only(dossier, contract):
        assert contract['preparation_stage']['scope'] == 'model'
        calls.append(contract)
        return {'drivers': _operating_plan()['model'], 'rationale': 'Synthetic opening'}
    with pytest.raises(ValueError, match='current driver partition'):
        StagedProposer(opening_only, opening_dossier=old['dossier'],
                       stage_dossiers=[saved])(extended, old['contract'])
    assert len(calls) == 1


def test_bank_forecast_contract_distinguishes_income_eliminations_from_opening_equity(tmp_path):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.sector_analysis import prepare_sector_analysis
    from bellomberg.valuation.dcf_engine import generate_valuation
    from bellomberg.valuation.bank_adapter import EARNINGS
    from test_sector_analysis import DAY, providers_for
    from test_input_preparation_bank import _documents, _propose
    policies = []
    def propose(dossier, contract):
        plan = _propose(dossier, contract)
        stage = contract["preparation_stage"]
        scope = stage["scope"]
        if scope == "model":
            assert "bank_ledger_semantics" not in stage
        else:
            policy = stage["bank_ledger_semantics"]
            assert policy["common_income_coefficients"] == EARNINGS
            assert policy["capital.consolidation_adjustments"]["measurement"] == "period_income_flow"
            assert policy["opening_consolidation_adjustments"]["measurement"] == "opening_equity_stock"
            requested = stage.get('bank_requested_contracts', {})
            if 'capital.reconciliation_basis' in stage['drivers']:
                assert 'same-entity' in requested['capital.reconciliation_basis']['requirement']
            if 'capital.parent_opening_debt' in stage['drivers']:
                assert requested['capital.parent_opening_debt']['required_reported_zero_balances'] == [
                    'BHCP2200', 'BHCP3605', 'BHCP3606', 'BHCP3607']
                assert requested['capital.distribution_policy']['value'] == 'full_sweep_after_buffers'
                assert 'upstream dividends' in requested['liquidity_bridge']['no_double_count']
            assert set(requested) <= set(stage['drivers'])
            if 'terminal_ledger' in stage['drivers']:
                terminal = requested['terminal_ledger']
                assert 'Never round' in terminal['fixed_requirements']
                assert 'one-element arrays' in terminal['shape']
            policies.append(policy)
            completed = dossier["completed_plan"]["scenarios"][scope]
            if set(EARNINGS) <= completed.keys():
                derived = dossier["derived_bank_forecasts"][scope]
                expected = [sum(completed[k]["value"][i] * sign for k, sign in EARNINGS.items())
                            for i in range(10)]
                assert derived["common_income"] == pytest.approx(expected)
                assert derived["intervals_first_to_last"] == 9
                assert derived["revenue_cagr"] == pytest.approx(0.)
            elif not any(set(EARNINGS) <= v.keys() for v in dossier["completed_plan"]["scenarios"].values()):
                assert "derived_bank_forecasts" not in dossier
        values = plan["model"] if scope == "model" else plan["scenarios"][scope]
        return {"drivers": {name: values[name] for name in stage["drivers"]},
                "rationale": "Synthetic legal-entity income and capital case"}
    bundle = prepare_sector_analysis("SYNTH-BANK", as_of=DAY, providers=providers_for("bank"))
    prepared = prepare_method_inputs(bundle, documents=_documents(),
        propose=StagedProposer(propose, drivers_per_stage=12))
    assert prepared["status"] == "prepared", prepared["issues"]
    assert policies and all(policy == policies[0] for policy in policies)
    result = generate_valuation("SYNTH-BANK", prepared_bundle=prepared["bundle"], output_dir=str(tmp_path))
    assert result["valuation_usability"]["usable"], result.get("acquisition_tasks")


def test_derived_bank_forecasts_use_forecast_intervals_and_preserve_proposal():
    from bellomberg.valuation.preparation_ai import _bank_forecast_arithmetic
    from bellomberg.valuation.bank_adapter import EARNINGS
    plan = {"scenarios": {"bear": {k: {"value": [0., 0., 0.]} for k in EARNINGS}, "base": {}}}
    plan["scenarios"]["bear"]["net_interest_income"]["value"] = [80., 88., 96.8]
    plan["scenarios"]["bear"]["operating_expenses"]["value"] = [20., 22., 24.]
    before = deepcopy(plan)
    result = _bank_forecast_arithmetic(plan)["bear"]
    assert plan == before
    assert result["revenue"] == [80., 88., 96.8]
    assert result["common_income"] == pytest.approx([60., 66., 72.8])
    assert result["revenue_cagr"] == pytest.approx(.1)
    assert result["intervals_first_to_last"] == 2
    assert set(_bank_forecast_arithmetic(plan)) == {"bear"}
    plan["scenarios"]["bear"]["net_interest_income"]["value"][0] = 0.
    assert _bank_forecast_arithmetic(plan)["bear"]["revenue_cagr"] is None
    plan["scenarios"]["bear"]["taxes"]["value"] = [1., 2.]
    with pytest.raises(ValueError, match="bank forecast arithmetic"):
        _bank_forecast_arithmetic(plan)


@pytest.mark.parametrize("bad", [True, float("nan"), 1e308])
def test_derived_bank_forecasts_reject_invalid_or_overflowed_totals(bad):
    from bellomberg.valuation.preparation_ai import _bank_forecast_arithmetic
    from bellomberg.valuation.bank_adapter import EARNINGS
    plan = {"scenarios": {"bear": {k: {"value": [bad, bad]} for k in EARNINGS}}}
    with pytest.raises(ValueError, match="bank forecast arithmetic"):
        _bank_forecast_arithmetic(plan)


@pytest.mark.parametrize("change", ["ticker", "as_of", "method_id", "decision", "acquired_sources",
    "acquisition_tasks", "missing_document", "text", "publication", "raw_sha", "duplicate", "new_key"])
def test_opening_snapshot_cannot_reuse_changed_or_missing_evidence(change):
    from bellomberg.valuation.preparation_ai import StagedProposer
    _, old, extended = _supplemental_snapshot_case()
    snapshot = deepcopy(old["dossier"])
    if change in ("ticker", "as_of", "method_id", "decision", "acquired_sources", "acquisition_tasks"):
        snapshot[change] = "changed"
    elif change == "missing_document":
        extended["documents"].pop(0)
    elif change == "duplicate":
        snapshot["documents"].append(deepcopy(snapshot["documents"][0]))
    elif change == "new_key":
        snapshot["untracked_context"] = "changed"
    else:
        doc = snapshot["documents"][0]
        if change == "text":
            doc["text"] += " A conflicting restatement."
            doc["sha256"] = sha256(doc["text"].encode()).hexdigest()
        elif change == "publication":
            doc["published_at"] = "2026-09-08"
            doc["available_at"] = "2026-09-08"
        else:
            doc["document_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="opening snapshot"):
        StagedProposer(lambda *_: pytest.fail("unexpected provider call"), opening_dossier=snapshot)(
            extended, old["contract"])


@pytest.mark.parametrize("explicit_null", [False, True])
def test_missing_opening_stage_does_not_spend_on_forecasts(tmp_path, explicit_null):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _bundle, _documents
    calls = []
    def call(**kw):
        calls.append(kw)
        spec = json.loads(kw["messages"][0]["content"])["contract"]["preparation_stage"]
        missing = {name: None for name in spec["drivers"]} if explicit_null else {}
        return SimpleNamespace(id="partial", model=kw["model"], provider="synthetic",
            stop_reason="end_turn", usage=SimpleNamespace(cost_usd=.02),
            content=[SimpleNamespace(type="text", text=json.dumps({"drivers": missing, "rationale": "Source missing"}))])
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=StagedProposer(_proposer(tmp_path, call=call)))
    assert result["status"] == "incomplete" and len(calls) == 1
    assert "quotation" in result["issues"][0]["reason"]


def test_unverified_opening_stage_stops_before_paying_for_forecasts(tmp_path):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _bundle, _documents, _operating_plan
    plan = _operating_plan()
    plan["model"]["historical_revenue"]["value"] = 999
    calls = []
    def propose(dossier, contract):
        calls.append(contract)
        return {"drivers": plan["model"], "rationale": "Synthetic wrong opening"}
    result = prepare_method_inputs(_bundle(), documents=_documents(), propose=StagedProposer(propose))
    assert result["status"] == "incomplete" and len(calls) == 1


def test_split_opening_validates_each_fact_and_reuses_paid_steps(tmp_path):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.dcf_engine import generate_valuation
    from test_input_preparation import _operating_plan, _bundle, _documents
    plan, calls = _operating_plan(), []

    def call(**kw):
        request = json.loads(kw["messages"][0]["content"])
        spec = request["contract"]["preparation_stage"]
        calls.append((spec, request["dossier"]["completed_plan"], kw["max_tokens"]))
        scope = spec["scope"]
        source = plan["model"] if scope == "model" else plan["scenarios"][scope]
        answer = {"drivers": {name: source[name] for name in spec["drivers"]},
                  "rationale": "Synthetic independent sourced step"}
        return SimpleNamespace(id="split-" + str(len(calls)), model=kw["model"], provider="synthetic",
            stop_reason="end_turn", usage=SimpleNamespace(cost_usd=.02),
            content=[SimpleNamespace(type="text", text=json.dumps(answer))])

    proposer = StagedProposer(_proposer(tmp_path, call=call), opening_drivers_per_stage=1)
    first = prepare_method_inputs(_bundle(), documents=_documents(), propose=proposer)
    assert first["status"] == "prepared", first["issues"]
    opening = [row for row in calls if row[0]["scope"] == "model"]
    assert opening[0][0]["drivers"] == ["perimeter", "calendar", "quotation"]
    assert [row[0]["drivers"] for row in opening[1:]] == [
        ["historical_revenue"], ["opening_nwc"], ["shares"], ["capdev_amortization_years"]]
    for previous, current in zip(opening, opening[1:]):
        assert set(previous[0]["drivers"]) <= set(current[1]["model"])
    assert all(row[2] == 16000 for row in calls)
    count = len(calls)
    restarted = StagedProposer(_proposer(tmp_path, call=lambda **_: pytest.fail("paid replay")),
                               opening_drivers_per_stage=1)
    again = prepare_method_inputs(_bundle(), documents=_documents(), propose=restarted)
    assert again["status"] == "prepared" and len(calls) == count
    valuation = generate_valuation("SYNTH-EXT", prepared_bundle=again["bundle"], output_dir=str(tmp_path))
    assert valuation["valuation_usability"]["usable"], valuation.get("acquisition_tasks")


@pytest.mark.parametrize("bad_driver,expected_calls", [("quotation", 1), ("historical_revenue", 2)])
def test_split_opening_invalid_fact_stops_before_next_paid_step(bad_driver, expected_calls):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _operating_plan, _bundle, _documents
    plan, calls = _operating_plan(), []
    if bad_driver == "quotation":
        plan["model"][bad_driver]["facts"]["price"]["quoted_value"] = 999
    else:
        plan["model"][bad_driver]["value"] = 999
    def propose(dossier, contract):
        spec = contract["preparation_stage"]
        calls.append(spec)
        assert spec["scope"] == "model", "forecast must not start"
        return {"drivers": {name: plan["model"][name] for name in spec["drivers"]},
                "rationale": "Synthetic incorrect observation"}
    result = prepare_method_inputs(_bundle(), documents=_documents(),
        propose=StagedProposer(propose, opening_drivers_per_stage=1))
    assert result["status"] == "incomplete" and len(calls) == expected_calls
    assert result["bundle"]["case"]["records"] == []


def test_split_opening_does_not_split_dynamic_bank_dependencies():
    from bellomberg.valuation.preparation_ai import StagedProposer
    proposer = StagedProposer(lambda *_: pytest.fail("unsupported paid call"), opening_drivers_per_stage=1)
    with pytest.raises(ValueError, match="operating_fcff"):
        proposer({}, {"method_id": "bank_residual_income", "bank_dynamic_capital": True})


@pytest.mark.parametrize("batch_size", [1, 6, 100])
def test_fcff_terminal_receives_completed_forecast_before_normalizing(batch_size, tmp_path):
    from bellomberg.valuation.preparation_ai import StagedProposer
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from bellomberg.valuation.dcf_engine import generate_valuation
    from test_input_preparation import _bundle, _documents, _operating_plan
    plan = _operating_plan()
    terminals = []

    def propose(dossier, contract):
        spec = contract["preparation_stage"]
        scope, names = spec["scope"], spec["drivers"]
        source = plan["model"] if scope == "model" else plan["scenarios"][scope]
        values = {name: source[name] for name in names}
        if "terminal_bridge" in names:
            completed = dossier["completed_plan"]["scenarios"][scope]
            prerequisites = {"revenue_growth", "gross_margin", "rnd_pct", "sga_pct",
                             "capdev_pct", "da_tan_pct", "opening_intangible_amortization"}
            if not prerequisites <= completed.keys():
                values["terminal_bridge"] = None
                return {"drivers": values, "rationale": "Final explicit EBIT cannot be normalized before its forecasts exist"}
            terminals.append(scope)
        return {"drivers": values, "rationale": "Synthetic source-backed scenario, terminal based on completed forecast"}

    result = prepare_method_inputs(_bundle(), documents=_documents(),
        propose=StagedProposer(propose, drivers_per_stage=batch_size))
    assert result["status"] == "prepared", result["issues"]
    assert terminals == ["bear", "base", "bull"]
    valuation = generate_valuation("SYNTH-EXT", prepared_bundle=result["bundle"], output_dir=str(tmp_path))
    assert valuation["valuation_usability"]["usable"], valuation.get("acquisition_tasks")


@pytest.mark.parametrize("count", [True, 0, -1, 1.5, "1"])
def test_split_opening_rejects_invalid_batch_size(count):
    from bellomberg.valuation.preparation_ai import StagedProposer
    with pytest.raises(ValueError, match="opening_drivers_per_stage"):
        StagedProposer(lambda *_: None, opening_drivers_per_stage=count)
