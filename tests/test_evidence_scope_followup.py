"""Synthetic receipts: follow-up metadata never borrows another field's scope."""
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from test_tetto_specialisti_16k import bb
from test_weekly_memory_memo15 import db
from test_weekly_research_without_workbook import research_weekly
from test_cablaggio_consigliere_multi import run_offline
from bellomberg.core.freshness import check_release_freshness as _release_check, project_macro_observation as _macro_projection


def receipt(payload, tool='get_fundamentals'):
    return {'tool': tool, 'input': {'ticker': 'ZZTEST'}, 'output': json.dumps(payload),
            'success': True, 'truncated': False, 'observed_at': '2032-01-02T10:00:00+00:00',
            'run_id': 'synthetic-run', 'round': 1}


def usage():
    return {'prompt_tokens': 100, 'completion_tokens': 100, 'cost': .02,
            'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
            'completion_tokens_details': {'reasoning_tokens': 0}}


def test_period_end_is_not_h1_and_bucket_is_not_calendar_year():
    from bellomberg.core.evidence_followup_policy import project_fact
    row = receipt({'period_end': '2031-06-30', 'period': '0y', 'revenue': 713.25})
    before = deepcopy(row)
    evidence = project_fact(row, 'revenue')
    assert evidence['fiscal_year_label'] is None
    assert evidence['fiscal_year_status'] == 'UNVERIFIED'
    assert evidence['duration_status'] == 'UNVERIFIED'
    assert evidence['period_start'] is None and evidence['period_end'] == '2031-06-30'
    assert row == before


def test_quote_currency_and_other_desk_name_are_not_fact_metadata():
    from bellomberg.core.evidence_followup_policy import project_fact
    row = receipt({'quote_currency': 'GBP', 'eps': 4.75,
                   'other_desk_report': {'name': 'Invented Issuer'}, 'debt_to_equity': 37.25})
    eps = project_fact(row, 'eps')
    ratio = project_fact(row, 'debt_to_equity')
    assert eps['currency'] is None and eps['currency_status'] == 'UNVERIFIED'
    assert ratio['unit'] is None and ratio['unit_status'] == 'UNVERIFIED'
    assert eps['issuer_name'] is None and eps['identity_basis'] != 'other_desk_report'


def test_two_equal_values_keep_distinct_periods_and_receipt_paths():
    from bellomberg.core.evidence_followup_policy import project_receipts
    rows = [receipt({'items': [{'period_end': '2030-06-30', 'revenue': 713.25},
                              {'period_end': '2031-06-30', 'revenue': 713.25}]})]
    out = project_receipts(rows)
    assert [r['period_end'] for r in out['facts']] == ['2030-06-30', '2031-06-30']
    assert len({r['source_receipt']['path'] for r in out['facts']}) == 2
    assert all(r['run_id'] == 'synthetic-run' and r['round'] == 1 for r in out['facts'])


def test_fundamentals_native_adapter_keeps_raw_ratio_and_attests_identity(monkeypatch):
    from bellomberg.agents import agent_tools as at
    from bellomberg.market_data import consensus_estimates as ce
    monkeypatch.setattr(at, 'YFINANCE_AVAILABLE', True)
    monkeypatch.setattr(ce, 'provider_symbol', lambda _: 'ZZPROVIDER')
    monkeypatch.setattr(at.yf, 'Ticker', lambda _: SimpleNamespace(info={
        'symbol': 'ZZPROVIDER', 'longName': 'Synthetic Issuer', 'currency': 'GBP',
        'financialCurrency': 'USD', 'debtToEquity': 37.25}))
    payload = at.tool_get_fundamentals('ZZTEST')
    assert payload['debt_to_equity'] == 37.25
    assert payload['metric_metadata']['debt_to_equity']['unit'] is None
    assert payload['issuer_identity']['status'] == 'PROVIDER_SYMBOL_MATCH'
    assert payload['issuer_identity']['provider_symbol'] == 'ZZPROVIDER'


def test_freschezza_compaction_preserves_duration_only_when_present():
    from bellomberg.market_data.freschezza_trimestrale import _compatto
    source = {'stato': 'aggiornato', 'livelli': [{'stato': 'ok', 'period_end': '2031-06-30',
        'period_start': '2031-01-01', 'period_type': 'semiannual', 'fiscal_year_label': 'FY2031',
        'valori': {'revenue': 713.25}, 'unita': 'million', 'valuta': 'EUR'}]}
    out = _compatto(source, da_cache=False)
    assert out['period_start'] == '2031-01-01' and out['period_type'] == 'semiannual'
    assert out['fiscal_year_label'] == 'FY2031'
    source['livelli'][0].pop('period_start'); source['livelli'][0].pop('period_type')
    out = _compatto(source, da_cache=False)
    assert out.get('period_start') is None and out.get('period_type') is None


def test_new_marker_frozen_and_legacy_marker_unchanged(monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.core import llm_client
    from bellomberg.core.evidence_followup_policy import KEY, POLICY, enabled
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    monkeypatch.setattr(llm_client, 'modello_o_buco', lambda *a: 'synthetic/model')
    new = cm._weekly_contract()
    assert new[KEY] == POLICY and enabled(new)
    assert new['evidence_prompt_policy'] == 'weekly-evidence-prompts/1'
    assert KEY not in cm._resume_publication_contract(new, {})
    historical = {KEY: POLICY, 'evidence_followup_as_of': '2031-01-01'}
    assert cm._resume_publication_contract(new, historical)['evidence_followup_as_of'] == '2031-01-01'
    for bad in (None, '', False, 'weekly-evidence-followup/999'):
        with pytest.raises(WeeklyRunBlocked):
            cm._resume_publication_contract(new, {KEY: bad})


def test_mnav_methods_remain_distinct_without_changing_values(monkeypatch):
    from bellomberg.valuation import dat_metrics as dm
    import yfinance as yf
    monkeypatch.setattr(yf, 'Ticker', lambda ticker: SimpleNamespace(fast_info={'last_price': 11.0}))
    out = dm._derivati_mnav_btc({'btc_holdings': 100, 'basic_shares_outstanding': 50,
                               'debt': 250, 'pref': 25, 'cash': 20}, 'ZZTEST')['derived_mnav']
    assert out['mnav_equity_basic'] == .5
    assert out['metric_metadata']['mnav_equity_basic']['method'] == 'market_cap/asset_value'
    assert out['metric_metadata']['mnav_ev']['method'] == 'enterprise_value/asset_value'


def test_sec_period_metadata_belongs_to_the_selected_cell():
    from bellomberg.market_data.sec_xbrl import quarterly_history
    rows = [{'start': '2030-04-01', 'end': '2030-06-30', 'val': 713.25, 'form': '10-Q',
             'filed': '2030-07-25', 'accn': 'synthetic-old', 'fp': 'Q2', 'fy': 2030},
            {'start': '2031-04-01', 'end': '2031-06-30', 'val': 713.25, 'form': '10-Q',
             'filed': '2031-07-25', 'accn': 'synthetic-new', 'fp': 'Q2', 'fy': 2031}]
    facts = {'us-gaap': {'Revenues': {'units': {'USD': rows}}}}
    out = quarterly_history(facts, fino_al='2031-08-01')['latest_period']
    meta = out['metric_metadata']['revenue']
    assert meta['period_start'] == '2031-04-01' and meta['period_end'] == '2031-06-30'
    assert meta['fiscal_year_label'] == 'FY2031' and meta['accession'] == 'synthetic-new'
    assert meta['unit'] == 'USD' and meta['duration_days'] == 90
    rows[1].pop('fy')
    assert quarterly_history(facts, fino_al='2031-08-01')['latest_period']['metric_metadata']['revenue']['fiscal_year_label'] is None


def attach_followup(bb, db, priming=None):
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    from bellomberg.agents.weekly_lifecycle import bind_blackboard
    from bellomberg.core.evidence_followup_policy import KEY, POLICY, AS_OF_KEY
    mid = db.save_memo('[IN PROGRESS]')
    store = WeeklyRunStore(db, mid, context={'contract_version': 1, 'contract': {
        KEY: POLICY, AS_OF_KEY: '2032-01-02', 'memo_facts_policy': 'weekly-facts/1',
        'evidence_prompt_policy': 'weekly-evidence-prompts/1', 'roster': [], 'r2_specialists': []},
        'language': 'it', 'research_started_at': '2032-01-02T18:00:00+00:00',
        'portfolio': {'positions': []}})
    bb.run_scope = 'weekly'
    bind_blackboard(bb, store)
    store.complete('priming', priming if priming is not None else {
        'var_contribution': {'error': 'insufficient history', 'error_code': 'INSUFFICIENT_HISTORY',
                             'observations': 7, 'minimum_observations': 29, 'calculation': 'var_contribution'}})
    return store


@pytest.mark.parametrize('adversarial', [False, True])
def test_native_capo_http_memo_receipts_and_paid_resume(bb, db, monkeypatch, tmp_path, adversarial):
    import httpx
    from bellomberg.agents import capo
    from bellomberg.agents.weekly_lifecycle import bind_blackboard
    from bellomberg.core import llm_client, memo_facts_context as context, evidence_followup_policy as policy
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    from bellomberg.core.request_journal import RequestJournal
    from test_capo_collasso import _prepara, _msg, MEMO_VERO
    from test_llm_retry_stream import sse, chunk
    _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    store = attach_followup(bb, db)
    body = {'period_end': '2031-06-30', 'revenue': 713.25, 'currency': 'EUR'}
    if not adversarial:
        body.update(period_start='2031-01-01', period_type='semiannual', fiscal_year_label='FY2031',
                    unit='EUR', definition='synthetic reported revenue',
                    issuer_identity={'ticker': 'ZZTEST', 'status': 'PROVIDER_SYMBOL_MATCH', 'name': 'Synthetic Issuer',
                                     'basis': 'synthetic.provider.symbol'})
    row = receipt(body, 'get_financial_history'); row['run_id'] = store.run_id
    bb.tool_receipts = [row]
    raw = ('ZZTEST revenue ' + ('812,25' if adversarial else '713,25')
           + ' EUR 2031-06-30 [src: get_financial_history]\n'
           + ('ZZTEST H1 713,25 EUR [src: get_financial_history]\n' if adversarial else '')
           + 'Analisi qualitativa sintetica, senza altre cifre.\n' * 110)
    sent = []
    def send(request):
        sent.append(json.loads(request.content))
        return sse({'id': 'synthetic-followup-capo', 'model': sent[-1]['model'], **chunk({'content': raw})},
                   {**chunk(finish='stop'), 'usage': usage()})
    client = llm_client.OpenRouterClient(api_key='offline-test', max_retries=0, trasporto=httpx.MockTransport(send))
    monkeypatch.setattr(capo, 'OpenRouterClient', lambda **kw: client)
    def journal():
        return RequestJournal(tmp_path / 'capo-paid.sqlite', run_id=store.run_id, authorized_usd=10,
            authorization={'source': 'synthetic offline'}, metadata=lambda model: {'id': model,
                'context_length': 1000000, 'top_provider': {'max_completion_tokens': 128000},
                'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})
    with llm_client.request_scope(journal(), phase='capo', agent='capo', round_n=3):
        first, capo_usage = capo.run_capo(bb)
    # Native title/mandate and the declared T5 notice surround exact provider prose.
    from bellomberg.core.scorecard_scope_policy import notice
    suffix = notice(capo_usage['scorecard_scope'])
    assert suffix and first.endswith(raw + suffix) and first.count(raw) == 1
    assert first.count(suffix) == 1 and capo_usage['complete'] is True and len(sent) == 1
    wire = json.dumps(sent[0], ensure_ascii=False)
    assert policy.POLICY in wire and 'UNVERIFIED' in wire and 'CLAIMS_ONLY_NOT_PRIMARY_SOURCES' in wire
    assert 'INSUFFICIENT_HISTORY' in wire and 'minimum_observations' in wire
    original_request = deepcopy(bb.data['_capo_request'])
    with journal()._db() as conn:
        paid_before = [dict(r) for r in conn.execute('SELECT * FROM requests')]
    assert json.loads(paid_before[0]['response'])['choices'][0]['message']['content'] == raw
    # Crash boundary: request/reply durable, orchestration has not committed capo stage.
    bind_blackboard(bb, WeeklyRunStore(db, store.memo_id))
    monkeypatch.setattr(policy, 'followup_block', lambda *_: pytest.fail('paid context rebuilt'))
    with llm_client.request_scope(journal(), phase='capo', agent='capo', round_n=3):
        replay, replay_usage = capo.run_capo(bb)
    assert replay == first and replay_usage['complete'] is True and len(sent) == 1
    assert bb.data['_capo_request'] == original_request and bb.tool_receipts == [row]
    with journal()._db() as conn:
        assert [dict(r) for r in conn.execute('SELECT * FROM requests')] == paid_before
    block = context.checkpoint_memo_facts(store, replay)
    audit = store.get(context.STAGE)['report']
    assert 'Lacune metadati' in block and 'NOT_ASSESSED' in block
    flags = [f['dimensions']['semantic_scope'] for f in audit['findings']]
    # Adversarial cell has a period end but no duration (label or days): 812,25 is not a
    # proven falsity (D1 residual), it stays NOT_ASSESSED with the reason declared.
    assert 'MISMATCH_EXPLICIT' not in flags
    reasons = [f.get('semantic_scope_reason') for f in audit['findings']]
    assert ('CELL_DURATION_UNKNOWN' in reasons) is adversarial
    assert ('Lacune metadati delle ricevute (non falsita dimostrate): 0.' in block) is (not adversarial)
    assert context.checkpoint_memo_facts(WeeklyRunStore(db, store.memo_id), replay) == block
    assert audit['source_memo_sha256'] == __import__('hashlib').sha256(replay.encode()).hexdigest()


def test_other_run_receipt_cannot_attest_this_run():
    from bellomberg.core.evidence_followup_policy import project_receipts
    out = project_receipts([receipt({'revenue': 713.25})], run_id='another-run')
    assert out['facts'] == [] and out['issues'][0]['code'] == 'RECEIPT_RUN_DIFFERS'


def test_missing_receipt_metadata_and_correct_period_are_distinct():
    from bellomberg.core.memo_facts_context import collect_memo_facts_context
    from bellomberg.reporting.memo_facts import audit_memo_facts
    rows = [receipt({'period_end': '2031-06-30', 'eps': 4.75, 'quote_currency': 'GBP'})]
    s = collect_memo_facts_context('synthetic-run', '2032-01-02T18:00:00+00:00',
        {'system': 'rules', 'user_message': 'another desk: Fake Company EPS 4.75'}, rows, evidence_followup=True)
    r = audit_memo_facts('ZZTEST eps 4,75 GBP 2031-06-30 [src: get_fundamentals]', s)
    assert r['findings'][0]['dimensions']['semantic_scope'] == 'NOT_ASSESSED'
    fact = r['evidence_scope_followup']['facts'][0]
    assert fact['currency_status'] == 'UNVERIFIED' and fact['issuer_name'] is None


def test_native_priming_freezes_var_payload_and_release_cutoff_including_missing_values(research_weekly, monkeypatch):
    import sys
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store
    calls = {'var': 0, 'freshness': []}
    result = {'error': 'insufficient history', 'error_code': 'INSUFFICIENT_HISTORY',
              'observations': 7, 'minimum_observations': 29, 'calculation': 'var_contribution'}
    def var():
        calls['var'] += 1
        return deepcopy(result)
    def releases(current, as_of):
        calls['freshness'].append((deepcopy(current), as_of))
        return _release_check(current, as_of)
    monkeypatch.setattr(sys.modules['bellomberg.portfolio.portfolio_analytics'], 'compute_var_contribution', var)
    fresh = sys.modules['bellomberg.core.freshness']
    monkeypatch.setattr(fresh, 'check_release_freshness', releases, raising=False)
    monkeypatch.setattr(fresh, 'project_macro_observation', _macro_projection, raising=False)
    monkeypatch.setattr(fresh, 'check_and_update', lambda *_: pytest.fail('legacy freshness in new acceptance'))
    monkeypatch.setattr(cm, 'tool_get_macro_dashboard', lambda: {'indicators': {
        'synthetic_missing': {'value': None, 'date': '2020-01-01', 'src': 'test'},
        'synthetic_value': {'value': 7.25, 'date': '2020-01-01', 'src': 'test'}}})
    cm.run_multi_agent(send_email=False)
    store = _store()
    frozen = store.get('priming')
    assert frozen['var_contribution'] == result and calls['var'] == 1
    report = frozen['freshness_report']
    assert report['schema'] == 'release-freshness/1' and report['checked'] == 2
    assert report['observations']['test:synthetic_missing']['status'] == 'UNKNOWN'
    assert report['as_of'] == store.context['contract']['evidence_followup_as_of']
    assert calls['freshness'][0][0]['test:synthetic_missing']['value'] is None
    cm.run_multi_agent(resume_memo_id=store.memo_id, send_email=False)
    assert calls['var'] == 1 and len(calls['freshness']) == 1
    assert _store().get('priming') == frozen


@pytest.mark.parametrize('desk_name', ['fundamentals', 'quant'])
def test_native_desk_http_precise_no_acquisition_and_frozen_round_replay(bb, db, monkeypatch, desk_name):
    import httpx
    from bellomberg.core import llm_client, evidence_followup_policy as policy
    from bellomberg.agents.specialists.base import Specialist
    from bellomberg.agents.weekly_lifecycle import bind_blackboard
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    from test_llm_retry_stream import sse, chunk
    from bellomberg.core import current_facts
    monkeypatch.setattr(current_facts, 'research_block', lambda **kw: '')
    store = attach_followup(bb, db)
    row = receipt({'period_end': '2031-06-30', 'revenue': 713.25})
    row['run_id'] = store.run_id
    bb.tool_receipts = [row]
    sent = []
    def send(request):
        sent.append(json.loads(request.content))
        return sse({'id': 'synthetic-followup-desk', 'model': sent[-1]['model'],
                    **chunk({'content': 'Complete synthetic report. ' * 35})},
                   {**chunk(finish='stop'), 'usage': usage()})
    client = llm_client.OpenRouterClient(api_key='offline-test', max_retries=0, trasporto=httpx.MockTransport(send))
    cls = type('SyntheticFollowupDesk', (Specialist,), {'name': desk_name, 'role': 'offline',
                'system_prompt': 'Synthetic company evidence specialist.', 'tools_used': []})
    desk = cls(bb, client=client)
    monkeypatch.setattr(desk, '_build_round_context', lambda _: 'Synthetic frozen round context')
    monkeypatch.setattr(desk, 'compute_score', lambda: pytest.fail('unrequested scoring'))
    report = desk._run_loop(1)
    wire = json.dumps(sent[0], ensure_ascii=False)
    assert policy.POLICY in wire and 'INSUFFICIENT_HISTORY' in wire
    assert 'nessuna nuova acquisizione in questo round' in report
    assert row['observed_at'] in report and 'numeri non sono verificati con i tool' not in report
    bind_blackboard(bb, WeeklyRunStore(db, store.memo_id))
    monkeypatch.setattr(policy, 'followup_block', lambda *_: pytest.fail('frozen round context rebuilt'))
    assert desk._run_loop(1) == report and len(sent) == 1


def test_no_acquisition_notice_does_not_invent_receipts(bb, db):
    from bellomberg.core.evidence_followup_policy import no_acquisition_notice
    attach_followup(bb, db)
    bb.tool_receipts = []
    assert 'Nessuna ricevuta della run disponibile' in no_acquisition_notice(bb, 1)


def test_identity_and_metadata_are_not_borrowed_from_other_rows():
    from bellomberg.core.evidence_followup_policy import project_receipts
    payload = {'currency': 'GBP', 'issuer_identity': {'ticker': 'ZZOTHER',
        'status': 'PROVIDER_SYMBOL_MATCH', 'name': 'Other Issuer'},
        'eps_estimates': [{'period': '0y', 'avg': 4.75, 'currency': 'USD'},
                          {'period': '+1y', 'avg': 4.75}],
        'other_issuer': {'ticker': 'ZZOTHER', 'revenue': 713.25}}
    result = project_receipts([receipt(payload, 'get_consensus_estimates')])
    first, second = result['facts']
    assert first['metric'] == second['metric'] == 'eps'
    assert first['currency'] == 'USD' and second['currency'] is None
    assert first['issuer_name'] is None and second['issuer_name'] is None
    assert any(i['code'] == 'SCOPE_TICKER_DIFFERS' for i in result['issues'])


def test_native_tool_http_captures_run_round_timestamp_without_mutating_payload(bb, db, monkeypatch):
    import httpx
    from bellomberg.core import llm_client
    from bellomberg.agents import chat_tools
    from bellomberg.agents.specialists.base import Specialist
    from test_llm_retry_stream import sse, chunk
    store = attach_followup(bb, db)
    payload = {'ticker': 'ZZTEST', 'debt_to_equity': 37.25, '_source': 'synthetic.provider',
               'quote_currency': 'GBP'}
    source_before = deepcopy(payload)
    monkeypatch.setattr(chat_tools, 'dispatch', lambda *a, **kw: deepcopy(payload))
    schema = {'name': 'get_fundamentals', 'description': 'synthetic provider',
              'input_schema': {'type': 'object', 'properties': {'ticker': {'type': 'string'}}}}
    monkeypatch.setattr(chat_tools, 'get_tools_for_agent', lambda _: [schema])
    sent = []
    def send(request):
        sent.append(json.loads(request.content))
        delta = ({'tool_calls': [{'index': 0, 'id': 'synthetic-tool-id', 'type': 'function',
                    'function': {'name': 'get_fundamentals', 'arguments': '{"ticker":"ZZTEST"}'}}]}
                 if len(sent) == 1 else {'content': 'Completed synthetic report with declared limitations. ' * 30})
        return sse({'id': 'synthetic-call-' + str(len(sent)), 'model': sent[-1]['model'], **chunk(delta)},
                   {**chunk(finish='tool_calls' if len(sent) == 1 else 'stop'), 'usage': usage()})
    client = llm_client.OpenRouterClient(api_key='offline-test', max_retries=0, trasporto=httpx.MockTransport(send))
    cls = type('SyntheticReceiptDesk', (Specialist,), {'name': 'quant', 'role': 'offline',
        'system_prompt': 'Synthetic evidence specialist.', 'tools_used': ['get_fundamentals']})
    desk = cls(bb, client=client)
    monkeypatch.setattr(desk, '_build_round_context', lambda _: 'Synthetic round context')
    result = desk._run_loop(1)
    assert len(sent) == 2 and desk.run_result_status == 'complete'
    assert 'nessuna nuova acquisizione' not in result
    row = bb.tool_receipts[0]
    assert row['run_id'] == store.run_id and row['round'] == 1 and row['desk'] == 'quant'
    assert __import__('datetime').datetime.fromisoformat(row['observed_at']).utcoffset() is not None
    assert row['input'] == {'ticker': 'ZZTEST'} and row['success'] is True
    assert json.loads(row['output']) == source_before and payload == source_before
    delivered = [m for m in sent[1]['messages'] if m['role'] == 'tool']
    assert len(delivered) == 1 and json.loads(delivered[0]['content']) == source_before


@pytest.mark.parametrize('scope', [
    {'period': '0y', 'fiscal_year_label': None}, {'period_end': '2031-06-30'},
    {'period': '0y', 'period_end': '2031-06-30', 'fiscal_year_label': None}])
def test_unattested_fiscal_label_is_not_a_proven_contradiction(scope):
    from bellomberg.core.memo_facts_context import collect_memo_facts_context
    from bellomberg.reporting.memo_facts import audit_memo_facts
    rows = [receipt({**scope, 'revenue': 713.25, 'currency': 'EUR'}, 'get_financial_history')]
    snapshot = collect_memo_facts_context('synthetic-run', '2032-01-02T18:00:00+00:00',
        {'system': 'rules', 'user_message': 'Synthetic context'}, rows, evidence_followup=True)
    report = audit_memo_facts('ZZTEST revenue 713,25 EUR CY2031 [src: get_financial_history]', snapshot)
    assert report['findings'][0]['dimensions']['semantic_scope'] == 'NOT_ASSESSED'


def test_explicit_fiscal_metadata_is_used_without_bucket_inference():
    from bellomberg.core.memo_facts_context import collect_memo_facts_context
    from bellomberg.reporting.memo_facts import audit_memo_facts
    rows = [receipt({'period': '0y', 'fiscal_year_label': 'FY2031', 'revenue': 713.25,
                     'currency': 'EUR'}, 'get_financial_history')]
    snapshot = collect_memo_facts_context('synthetic-run', '2032-01-02T18:00:00+00:00',
        {'system': 'rules', 'user_message': 'Synthetic context'}, rows, evidence_followup=True)
    good = audit_memo_facts('ZZTEST revenue 713,25 EUR FY2031 [src: get_financial_history]', snapshot)
    bad = audit_memo_facts('ZZTEST revenue 713,25 EUR FY2030 [src: get_financial_history]', snapshot)
    assert good['findings'][0]['dimensions']['semantic_scope'] == 'CONSISTENT_EXPLICIT'
    assert bad['findings'][0]['dimensions']['semantic_scope'] == 'MISMATCH_EXPLICIT'


def test_native_sec_values_keep_selected_cell_metadata():
    from bellomberg.market_data.sec_xbrl import quarterly_history
    from bellomberg.core.evidence_followup_policy import project_receipts
    rows = [{'start': '2031-04-01', 'end': '2031-06-30', 'val': 713.25, 'form': '10-Q',
             'filed': '2031-07-25', 'accn': 'synthetic-only', 'fp': 'Q2', 'fy': 2031}]
    quarters = quarterly_history({'us-gaap': {'Revenues': {'units': {'USD': rows}}}}, fino_al='2031-08-01')
    row = receipt({'ticker': 'ZZTEST', 'latest_period': quarters['latest_period']}, 'get_financial_history')
    before = deepcopy(row)
    projected = project_receipts([row], run_id='synthetic-run')
    fact = next(f for f in projected['facts'] if f['source_receipt']['path'] == '/latest_period/values/revenue')
    assert fact['period_start'] == '2031-04-01' and fact['period_end'] == '2031-06-30'
    assert fact['unit'] == 'USD' and fact['currency'] == 'USD'
    assert fact['fiscal_year_label'] == 'FY2031' and fact['duration_days'] == 90
    assert row == before


@pytest.mark.parametrize('invalid_meta,issue', [
    ({'status': 'UNAVAILABLE'}, 'SCOPE_UNAVAILABLE'),
    ({'observed_at': '2033-01-01T00:00:00Z'}, 'SOURCE_AFTER_CUTOFF')])
@pytest.mark.parametrize('location', ['cell', 'container'])
@pytest.mark.parametrize('claim_value', ['713,25', '714,25'])
def test_excluded_cell_metadata_neither_certifies_nor_contradicts(invalid_meta, issue, location, claim_value):
    from bellomberg.core.memo_facts_context import collect_memo_facts_context
    from bellomberg.reporting.memo_facts import audit_memo_facts
    meta = {'period_end': '2031-06-30', 'unit': 'EUR', 'currency': 'EUR'}
    container = {'revenue': meta}
    (meta if location == 'cell' else container).update(invalid_meta)
    row = receipt({'revenue': 713.25, 'metric_metadata': container}, 'get_financial_history')
    snapshot = collect_memo_facts_context('synthetic-run', '2032-01-02T18:00:00Z',
        {'system': 'rules', 'user_message': 'synthetic'}, [row], evidence_followup=True)
    report = audit_memo_facts('ZZTEST revenue ' + claim_value + ' EUR 2031-06-30 [src: get_financial_history]', snapshot)
    assert any(i['code'] == issue for i in report['issues'])
    assert report['findings'][0]['dimensions']['semantic_scope'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('claim_value,expected', [
    ('713,25', 'CONSISTENT_EXPLICIT'), ('trimestrale 714,25', 'MISMATCH_EXPLICIT')])
def test_valid_cell_metadata_remains_usable_when_another_metric_is_excluded(claim_value, expected):
    """The cell states its span (91 days): a period end alone could not contradict (D1)."""
    from bellomberg.core.memo_facts_context import collect_memo_facts_context
    from bellomberg.reporting.memo_facts import audit_memo_facts
    row = receipt({'revenue': 713.25, 'eps': 4.75, 'metric_metadata': {
        'revenue': {'period_end': '2031-06-30', 'unit': 'EUR', 'currency': 'EUR', 'duration_days': 91},
        'eps': {'status': 'UNAVAILABLE'}}}, 'get_financial_history')
    snapshot = collect_memo_facts_context('synthetic-run', '2032-01-02T18:00:00Z',
        {'system': 'rules', 'user_message': 'synthetic'}, [row], evidence_followup=True)
    report = audit_memo_facts('ZZTEST revenue ' + claim_value + ' EUR 2031-06-30 [src: get_financial_history]', snapshot)
    assert any(i['code'] == 'SCOPE_UNAVAILABLE' for i in report['issues'])
    assert report['findings'][0]['dimensions']['semantic_scope'] == expected


# --- Lotto T4 follow-up D1-D3 (Opus 5.5): duration, run and round provenance ---

def _sec_q2_and_h1_receipt():
    """Synthetic 10-Q: two cells with the same end, Q2 (90 days) and H1 (180 days)."""
    from bellomberg.market_data.sec_xbrl import quarterly_history
    obs = [{'start': '2031-04-01', 'end': '2031-06-30', 'val': 713.25, 'form': '10-Q',
            'filed': '2031-07-25', 'accn': 'syn-q', 'fp': 'Q2', 'fy': 2031},
           {'start': '2031-01-01', 'end': '2031-06-30', 'val': 1426.5, 'form': '10-Q',
            'filed': '2031-07-25', 'accn': 'syn-q', 'fp': 'Q2', 'fy': 2031}]
    q = quarterly_history({'us-gaap': {'Revenues': {'units': {'USD': obs}}}}, fino_al='2031-08-01')
    meta = q['latest_period']['metric_metadata']['revenue']
    assert meta['duration_days'] == 90 and 'duration' not in meta and 'period_type' not in meta
    return receipt({'ticker': 'ZZTEST', 'latest_period': q['latest_period']}, 'get_financial_history')


def _audit_rows(memo, rows):
    from bellomberg.core.memo_facts_context import collect_memo_facts_context
    from bellomberg.reporting.memo_facts import audit_memo_facts
    snapshot = collect_memo_facts_context('synthetic-run', '2032-01-02T18:00:00Z',
        {'system': 'rules', 'user_message': 'synthetic'}, rows, evidence_followup=True)
    return audit_memo_facts(memo, snapshot)


def test_end_date_alone_does_not_attest_duration_of_a_cell():
    """D1 (verifier's former xfail): true H1 value without duration vs the Q2 cell."""
    from bellomberg.reporting.memo_facts import render_memo_facts
    report = _audit_rows('ZZTEST revenue 1426,50 USD 2031-06-30 [src: get_financial_history]',
                         [_sec_q2_and_h1_receipt()])
    finding = report['findings'][0]
    assert finding['dimensions']['semantic_scope'] == 'NOT_ASSESSED'
    assert finding['semantic_scope_reason'] == 'CLAIM_DURATION_UNDECLARED'
    assert 'MISMATCH_EXPLICIT' not in render_memo_facts(report)


@pytest.mark.parametrize('claim,expected,reason', [
    ('quarterly 1426,50', 'MISMATCH_EXPLICIT', None),
    ('trimestrale 713,25', 'CONSISTENT_EXPLICIT', None),
    ('semestrale 1426,50', 'NOT_ASSESSED', 'DURATION_CELL_UNAVAILABLE'),
    ('annual 713,25', 'NOT_ASSESSED', 'DURATION_CELL_UNAVAILABLE')])
def test_declared_duration_is_compared_with_duration_days_of_the_cell(claim, expected, reason):
    report = _audit_rows('ZZTEST revenue ' + claim + ' USD 2031-06-30 [src: get_financial_history]',
                         [_sec_q2_and_h1_receipt()])
    finding = report['findings'][0]
    assert finding['dimensions']['semantic_scope'] == expected
    assert finding.get('semantic_scope_reason') == reason


def test_fiscal_year_claim_names_annual_duration_but_not_a_quarter_cell():
    """FY2031 names a year: vs an annual cell it can contradict, vs the Q2 cell labelled FY2031 not."""
    quarter = _audit_rows('ZZTEST revenue 1426,50 USD FY2031 [src: get_financial_history]',
                          [_sec_q2_and_h1_receipt()])['findings'][0]
    assert quarter['dimensions']['semantic_scope'] == 'NOT_ASSESSED'
    assert quarter['semantic_scope_reason'] == 'CLAIM_DURATION_UNDECLARED'
    annual = receipt({'fiscal_year_label': 'FY2031', 'period_type': 'annual', 'revenue': 713.25,
                      'currency': 'EUR'}, 'get_financial_history')
    for claim, expected in (('713,25', 'CONSISTENT_EXPLICIT'), ('714,25', 'MISMATCH_EXPLICIT')):
        report = _audit_rows('ZZTEST revenue ' + claim + ' EUR FY2031 [src: get_financial_history]', [annual])
        assert report['findings'][0]['dimensions']['semantic_scope'] == expected


def test_duration_label_and_day_count_that_disagree_never_conclude():
    row = receipt({'revenue': 713.25, 'metric_metadata': {'revenue': {
        'period_end': '2031-06-30', 'unit': 'USD', 'currency': 'USD',
        'period_type': 'quarterly', 'duration_days': 180}}}, 'get_financial_history')
    for claim in ('quarterly 714,25', 'semestrale 714,25', '714,25'):
        report = _audit_rows('ZZTEST revenue ' + claim + ' USD 2031-06-30 [src: get_financial_history]', [row])
        assert report['findings'][0]['dimensions']['semantic_scope'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('missing', ['absent', None, ''])
def test_receipt_without_run_id_is_excluded_not_inherited(missing):
    """D2: the run is never inherited from the caller."""
    from bellomberg.core.evidence_followup_policy import project_receipts, project_fact
    row = receipt({'revenue': 713.25})
    if missing == 'absent':
        row.pop('run_id')
    else:
        row['run_id'] = missing
    out = project_receipts([row], run_id='synthetic-run')
    assert out['facts'] == []
    assert out['issues'][0] == {'code': 'RECEIPT_RUN_UNATTESTED', 'receipt_index': 0}
    with pytest.raises(ValueError, match='RECEIPT_RUN_UNATTESTED'):
        project_fact(row, 'revenue', run_id='synthetic-run')


def test_receipt_without_run_id_cannot_certify_memo_claim():
    row = receipt({'period_end': '2031-06-30', 'revenue': 713.25, 'currency': 'EUR'}, 'get_financial_history')
    row.pop('run_id')
    report = _audit_rows('ZZTEST revenue 713,25 EUR 2031-06-30 [src: get_financial_history]', [row])
    assert report['findings'][0]['dimensions']['semantic_scope'] == 'NOT_ASSESSED'
    assert {'code': 'RECEIPT_RUN_UNATTESTED', 'receipt_index': 0} in report['evidence_scope_followup']['issues']


@pytest.mark.parametrize('round_value,current,code', [
    (9, None, 'RECEIPT_ROUND_INCOHERENT'), (-1, None, 'RECEIPT_ROUND_INCOHERENT'),
    (True, None, 'RECEIPT_ROUND_INCOHERENT'), ('1', None, 'RECEIPT_ROUND_INCOHERENT'),
    (2, 1, 'RECEIPT_ROUND_INCOHERENT'), ('absent', None, 'RECEIPT_ROUND_UNATTESTED'),
    (None, None, 'RECEIPT_ROUND_UNATTESTED'), (0, 1, None), (1, 1, None), (2, None, None)])
def test_receipt_round_is_compared_not_only_copied(round_value, current, code):
    """D3: a round outside the weekly rounds, or after the current one, is incoherent."""
    from bellomberg.core.evidence_followup_policy import project_receipts
    row = receipt({'revenue': 713.25})
    if round_value == 'absent':
        row.pop('round')
    else:
        row['round'] = round_value
    out = project_receipts([row], run_id='synthetic-run', round_number=current)
    if code is None:
        assert out['issues'] == [] and out['facts'][0]['round'] == round_value
    else:
        assert out['facts'] == [] and out['issues'][0] == {'code': code, 'receipt_index': 0}


def test_incoherent_round_cannot_certify_memo_claim():
    row = receipt({'period_end': '2031-06-30', 'revenue': 713.25, 'currency': 'EUR'}, 'get_financial_history')
    row['round'] = 9
    report = _audit_rows('ZZTEST revenue 713,25 EUR 2031-06-30 [src: get_financial_history]', [row])
    assert report['findings'][0]['dimensions']['semantic_scope'] == 'NOT_ASSESSED'
    assert {'code': 'RECEIPT_ROUND_INCOHERENT', 'receipt_index': 0} in report['evidence_scope_followup']['issues']


@pytest.mark.parametrize('defect', ['no_run_id', 'future_round'])
def test_prompt_block_and_notice_drop_unattested_receipts(bb, db, defect):
    from bellomberg.core.evidence_followup_policy import followup_block, no_acquisition_notice
    store = attach_followup(bb, db)
    bb.current_round = 1
    exposure = receipt({'sector_weights': {'synthetic': 0.25}}, 'get_sector_exposure')
    exposure['run_id'] = store.run_id
    if defect == 'no_run_id':
        exposure.pop('run_id')
    else:
        exposure['round'] = 2
    bb.tool_receipts = [exposure]
    payload = json.loads(followup_block(bb).split('===\n', 1)[1])
    code = 'RECEIPT_RUN_UNATTESTED' if defect == 'no_run_id' else 'RECEIPT_ROUND_INCOHERENT'
    assert payload['quant_sources'] == {}
    assert {'code': code, 'receipt_index': 0} in payload['issues']
    assert 'Nessuna ricevuta della run disponibile' in no_acquisition_notice(bb, 1)


# --- T4 v2 (Opus 5.5): reviewer's cases on the day windows, YTD, Q labels, round 0 ---

def _cell(value, *, days=None, label=None, period=None, unit='USD'):
    meta = {'unit': unit, 'currency': unit}
    meta.update({'period': period} if period else {'period_end': '2031-06-30'})
    if days is not None:
        meta['duration_days'] = days
    if label is not None:
        meta['period_type'] = label
    return receipt({'revenue': value, 'metric_metadata': {'revenue': meta}}, 'get_financial_history')


def _scope(claim, rows, period='2031-06-30'):
    finding = _audit_rows('ZZTEST revenue ' + claim + ' USD ' + period + ' [src: get_financial_history]',
                          rows)['findings'][0]
    return finding['dimensions']['semantic_scope'], finding.get('semantic_scope_reason')


@pytest.mark.parametrize('days,word', [(91, 'trimestrale'), (92, 'quarterly'), (90.0, 'quarterly'),
                                       ('91', 'quarterly'), (181, 'semestrale'), (184, 'semiannual'),
                                       (364, 'annuale'), (366, 'annual'), (371, 'annual')])
def test_day_windows_classify_real_quarters_halves_and_years(days, word):
    """52/53-week years (364, 371) and 91-92 / 181-184 day spans are what filings carry."""
    assert _scope(word + ' 713,25', [_cell(713.25, days=days)]) == ('CONSISTENT_EXPLICIT', None)
    assert _scope(word + ' 714,25', [_cell(713.25, days=days)]) == ('MISMATCH_EXPLICIT', None)


def test_ytd_label_is_not_contradicted_by_its_nine_month_span():
    assert _scope('ytd 713,25', [_cell(713.25, days=273, label='ytd')]) == ('CONSISTENT_EXPLICIT', None)
    assert _scope('ytd 714,25', [_cell(713.25, days=273, label='ytd')]) == ('MISMATCH_EXPLICIT', None)


def test_quarter_label_of_the_claim_names_a_quarter():
    """Q2 FY2031 says quarterly: vs a 91-day cell it contradicts, vs a 181-day one it does not."""
    quarter = [_cell(713.25, days=91, period='Q2FY2031')]
    assert _scope('714,25', quarter, period='Q2 FY2031') == ('MISMATCH_EXPLICIT', None)
    half = [_cell(1426.5, days=181, period='Q2FY2031')]
    assert _scope('713,25', half, period='Q2 FY2031') == ('NOT_ASSESSED', 'CLAIM_DURATION_UNDECLARED')


@pytest.mark.parametrize('days', [None, True, False, '91.5', 'n.d.', ''])
def test_cell_without_any_duration_never_contradicts_an_undeclared_claim(days):
    """Residual D1: neither claim nor cell states a duration; a period end does not attest it."""
    rows = [_cell(713.25, days=days)]
    assert _scope('714,25', rows) == ('NOT_ASSESSED', 'CELL_DURATION_UNKNOWN')
    assert _scope('713,25', rows) == ('CONSISTENT_EXPLICIT', None)


@pytest.mark.parametrize('days', [True, '91.5', 'n.d.'])
def test_day_count_that_is_not_a_number_is_no_duration(days):
    assert _scope('quarterly 714,25', [_cell(713.25, days=days)])[0] == 'NOT_ASSESSED'
    assert _scope('quarterly 714,25', [_cell(713.25, days=days, label='quarterly')])[0] == 'NOT_ASSESSED'


@pytest.mark.parametrize('text,expected', [
    ('semi-annual', 'semiannual'), ('Semi annual', 'semiannual'), ('semiannual', 'semiannual'),
    ('half-yearly', 'semiannual'), ('bi-annual', 'AMBIGUOUS'), ('biannual', 'AMBIGUOUS'),
    ('non-annual', None), ('annual', 'annual'), ('yearly', 'annual'), ('quarterly', 'quarterly')])
def test_compound_duration_words_are_not_read_as_annual(text, expected):
    from bellomberg.reporting.memo_facts import _claim_duration
    assert _claim_duration('ZZTEST revenue ' + text + ' 1426,50 USD') == expected


@pytest.mark.parametrize('word', ['semi-annual', 'half-yearly'])
def test_hyphenated_half_year_claim_is_compared_with_the_half_year_cell(word):
    rows = [_cell(1426.5, days=181), _cell(2800.0, days=365)]
    assert _scope(word + ' 1426,50', rows) == ('CONSISTENT_EXPLICIT', None)
    assert _scope(word + ' 2800', rows) == ('MISMATCH_EXPLICIT', None)


def test_current_round_zero_still_excludes_a_later_receipt_round():
    from bellomberg.core.evidence_followup_policy import project_receipts
    row = receipt({'revenue': 713.25})
    out = project_receipts([row], run_id='synthetic-run', round_number=0)
    assert out['facts'] == []
    assert out['issues'][0] == {'code': 'RECEIPT_ROUND_INCOHERENT', 'receipt_index': 0}


@pytest.mark.parametrize('current', ['1', True, 1.0])
def test_current_round_not_an_int_is_declared_not_skipped(current):
    from bellomberg.core.evidence_followup_policy import project_receipts, project_fact
    row = receipt({'revenue': 713.25})
    row['round'] = 2
    out = project_receipts([row], run_id='synthetic-run', round_number=current)
    assert out['facts'] == []
    assert out['issues'][0] == {'code': 'CURRENT_ROUND_UNATTESTED', 'receipt_index': 0}
    with pytest.raises(ValueError, match='CURRENT_ROUND_UNATTESTED'):
        project_fact(row, 'revenue', run_id='synthetic-run', round_number=current)


@pytest.mark.parametrize('missing,code', [('run_id', 'RECEIPT_RUN_UNATTESTED'),
                                          ('round', 'RECEIPT_ROUND_UNATTESTED')])
def test_project_fact_without_caller_run_still_requires_attested_receipt(missing, code):
    from bellomberg.core.evidence_followup_policy import project_fact, project_receipts
    row = receipt({'revenue': 713.25})
    row.pop(missing)
    with pytest.raises(ValueError, match=code):
        project_fact(row, 'revenue')
    assert project_receipts([row])['issues'][0] == {'code': code, 'receipt_index': 0}
    attested = project_fact(receipt({'revenue': 713.25}), 'revenue')
    assert attested['run_id'] == 'synthetic-run' and attested['round'] == 1


def test_prompt_block_declares_a_current_round_that_is_not_an_int(bb, db):
    from bellomberg.core.evidence_followup_policy import followup_block
    store = attach_followup(bb, db)
    bb.current_round = '1'
    exposure = receipt({'sector_weights': {'synthetic': 0.25}}, 'get_sector_exposure')
    exposure['run_id'] = store.run_id
    bb.tool_receipts = [exposure]
    payload = json.loads(followup_block(bb).split('===\n', 1)[1])
    assert payload['quant_sources'] == {}
    assert {'code': 'CURRENT_ROUND_UNATTESTED', 'receipt_index': 0} in payload['issues']
