"""Native desk/Red/Capo requests and source parsing, frozen transport only."""
from copy import deepcopy
from functools import partial
from hashlib import sha256
from pathlib import Path
import json
import os

import httpx
import pytest

from test_weekly_research_without_workbook import research_weekly
from test_cablaggio_consigliere_multi import run_offline
from test_weekly_recovery import _store
from test_company_source_research import FrozenTransport, html, SITE
from bellomberg.agents import consigliere_multi as cm
from bellomberg.agents.red_team import run_red_team as native_red_team
from bellomberg.agents.capo import run_capo as native_capo


@pytest.mark.parametrize('crash', ['before_commit', 'after_commit', 'invalid_receipt'])
def test_native_note_reply_crash_recovery_is_verified_and_never_redispatched(
        research_weekly, monkeypatch, tmp_path, crash):
    from bellomberg.agents.specialists import base
    from bellomberg.storage.memory_db import MemoryDB

    class ProcessExit(BaseException):
        pass

    original = base.Specialist._execute_meta_tool
    def interrupt(self, name, input_):
        if name == 'add_research_note':
            if crash != 'before_commit':
                assert original(self, name, input_)['ok']
            raise ProcessExit('synthetic reply crash')
        return original(self, name, input_)

    monkeypatch.setattr(base.Specialist, '_execute_meta_tool', interrupt)
    with pytest.raises(ProcessExit):
        test_native_weekly_sources_all_desks_red_capo_pdf_and_two_exact_recoveries(
            research_weekly, monkeypatch, tmp_path)
    store = _store()
    if crash == 'invalid_receipt':
        with store.db._conn() as conn:
            conn.execute("UPDATE weekly_checkpoints SET payload_sha256='invalid' "
                         "WHERE stage LIKE 'research_notes_reply_v1:%'")
    before = store.status()
    assert before['resume_available'] is (crash == 'after_commit')
    assert ('Esito tool incerto' in (before['blocked_reason'] or '')) is (crash != 'after_commit')
    # Once a dispatch is inflight, no second write is authorized, even when committed.
    def no_redispatch(self, name, input_):
        assert name != 'add_research_note', 'reply side effect dispatched twice'
        return original(self, name, input_)
    monkeypatch.setattr(base.Specialist, '_execute_meta_tool', no_redispatch)
    if crash == 'after_commit':
        cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
        after = store.status()
        # Every already paid response retains its native request identity and receipt.
        saved = {row['request_id']: row for row in after['request_costs']['requests']}
        assert all(saved[row['request_id']] == row for row in before['request_costs']['requests'])
        paid = after['request_costs']
        cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
        assert store.status()['request_costs'] == paid
    else:
        with pytest.raises(RuntimeError, match='outcome unknown|Checkpoint modificato'):
            cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
        assert store.status()['request_costs'] == before['request_costs']
    answers = [row for row in MemoryDB().get_decision_notes(901) if row['autore'] == 'AI']
    assert len(answers) == (0 if crash == 'before_commit' else 1)
    if os.environ.get('BELLOMBERG_OFFLINE_TEST_SANDBOX'):
        Path(os.environ['BELLOMBERG_OFFLINE_TEST_SANDBOX'], 'native-reply-' + crash + '.json').write_text(
            json.dumps({'before': before, 'after': store.status(), 'answers': answers}, indent=2), encoding='utf-8')


def _provider_reply(body, text, tool_calls=None, *, ident):
    usage = {'prompt_tokens': 100, 'completion_tokens': 80, 'total_tokens': 180, 'cost': 0.002,
        'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
        'completion_tokens_details': {'reasoning_tokens': 0}}
    if body.get('stream'):
        # ZR 05/10: dal 02/10 anche i DESK vanno in streaming (prima solo Red/Capo): le
        # chiamate ai tool devono viaggiare nel delta SSE, non solo il testo.
        delta = {'content': text}
        if tool_calls:
            delta['tool_calls'] = [{'index': index, 'id': ident + '-' + str(index), 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(arguments)}}
                for index, (name, arguments) in enumerate(tool_calls)]
        data = {'id': ident, 'model': body['model'], 'choices': [
            {'index': 0, 'delta': delta, 'finish_reason': 'tool_calls' if tool_calls else 'stop'}],
            'usage': usage}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=('data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n').encode())
    message = {'role': 'assistant', 'content': text}
    if tool_calls:
        message['tool_calls'] = [{'id': ident + '-' + str(index), 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}
            for index, (name, arguments) in enumerate(tool_calls)]
    return httpx.Response(200, json={'id': ident, 'model': body['model'],
        'choices': [{'index': 0, 'message': message,
                     'finish_reason': 'tool_calls' if tool_calls else 'stop'}], 'usage': usage})


def test_native_weekly_sources_all_desks_red_capo_pdf_and_two_exact_recoveries(research_weekly, monkeypatch, tmp_path):
    from bellomberg.agents import company_research_tools as bindings, red_team, capo, chat_tools
    from bellomberg.agents.company_source_research import ResearchSession
    from bellomberg.agents.specialists import base
    from bellomberg.core import llm_client
    from bellomberg.core import llm_pricing
    from bellomberg.valuation import preparation_ai
    from bellomberg.core.research_analysis import research_reference
    observed, forbidden = research_weekly
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.core import current_facts as note_facts
    monkeypatch.setattr(note_facts, '_RESEARCH_CACHE', {'text': None, 'ts': 0.0})
    with MemoryDB()._conn() as conn:
        for ident, when in ((901, '2026-01-01 09:00:00'), (902, '2026-01-02 09:00:00')):
            conn.execute("INSERT INTO decisions(id,ticker,action,status,timestamp) VALUES(?,?,?,?,?)",
                         (ident, 'SYNTH-A', 'RESEARCH', 'PENDING', when))
        conn.execute("INSERT INTO decision_notes(id,decision_id,autore,testo,timestamp) VALUES(?,?,?,?,?)",
                     (801,901,'PM','SYNTHETIC ORIGINAL PM QUESTION','2026-01-01 09:30:00'))
        conn.execute("INSERT INTO decisions(id,ticker,action,status,timestamp,veto) VALUES(?,?,?,?,?,?)",
                     (903,'SYNTH-B','RESEARCH','SKIPPED','2026-01-01 09:00:00',1))
        conn.execute("INSERT INTO decision_notes(id,decision_id,autore,testo,timestamp) VALUES(?,?,?,?,?)",
                     (802,903,'PM','SYNTHETIC VETO QUESTION','2026-01-01 09:30:00'))
        conn.execute("INSERT INTO decisions(id,ticker,action,status,timestamp) VALUES(?,?,?,?,?)",
                     (900,'SYNTH-A','RESEARCH','EXPIRED','2025-12-01 09:00:00'))
        conn.execute("INSERT INTO decision_notes(id,decision_id,autore,testo,timestamp) VALUES(?,?,?,?,?)",
                     (803,900,'PM','SYNTHETIC ARCHIVED BACKGROUND','2025-12-01 09:30:00'))
    requests, document_reads, consensus_reads, native_boards = [], [], [], []
    text_a = ('Synthetic issuer SYNTH-A\nPublished on 2026-09-09\nYear ended 2025-12-31\n'
              'Consolidated financial statements. Revenue 100 million EUR. Cash flow 12 million EUR. '
              'Synthetic evidence; margins depend on customer retention.')
    text_b = text_a.replace('SYNTH-A', 'SYNTH-B').replace('Revenue 100', 'Revenue 80')
    transport = FrozenTransport({'/investors/a.html': (html(text_a), 'text/html'),
                                 '/investors/b.html': (html(text_b), 'text/html')})
    original_bind = bindings.bind_weekly_company_research

    def bind(board, store):
        return original_bind(board, store, identity_resolver=lambda ticker: {
            'status': 'confirmed', 'ticker': ticker, 'name': 'Synthetic issuer ' + ticker,
            'exchange': 'XSYNTH', 'currency': 'EUR'},
            profile_provider=lambda *a, **k: {'status': 'ok', 'data': {'info': {'website': SITE}}},
            session_factory=partial(ResearchSession, download=transport))

    monkeypatch.setattr(bindings, 'bind_weekly_company_research', bind)
    original_dispatch = chat_tools.dispatch

    def dispatch(name, params, **kwargs):
        if name != 'get_consensus_estimates':
            pytest.fail('Unexpected external tool in frozen native weekly: ' + name)
        ticker = params['ticker']
        consensus_reads.append(ticker)
        return {'ticker': ticker, 'status': 'ok' if ticker == 'SYNTH-A' else 'unavailable',
            'price_targets': {'mean': 12, 'currency': 'EUR'} if ticker == 'SYNTH-A' else None,
            'source': 'frozen consensus provider', 'retrieved_at': '2026-10-03T00:00:00Z',
            'data_date': None, 'analyst_count': None,
            'gaps': [] if ticker == 'SYNTH-A' else ['Consensus unavailable; no target inferred']}

    monkeypatch.setattr(chat_tools, 'dispatch', dispatch)
    monkeypatch.setattr(preparation_ai, 'live_metadata', lambda model: {
        'id': model, 'context_length': 1000000, 'top_provider': {'max_completion_tokens': 128000},
        'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})
    monkeypatch.setattr(llm_pricing, '_fx_usd_to_eur', lambda: (0.9, 'offline synthetic FX'))
    client_class = llm_client.OpenRouterClient
    reports = ('Business quality depends on customer retention and disciplined investment. '
        'Observed statements report revenue and cash flow [src: read_company_dossier]. '
        'Consensus analisti is a separate reference [src: get_consensus_estimates]; SYNTH-B has no '
        'consensus and analyst count/data date remain n.d. Assumption: margins recover only if '
        'retention holds, a hypothesis falsified by renewed churn. Bear: demand pressure persists; '
        'base: operating discipline; bull: improved demand. Risks and catalysts require monitoring. '
        'RESEARCH, with no operational proposal and no AI fair value. ')

    # Provider conversation state survives construction of a new client on resume.
    turns = {}
    def native_client(desk, board=None):
        if board is not None:
            native_boards.append(board)
        def send(request):
            body = json.loads(request.content)
            round_n = board.current_round if board is not None else None
            key = (desk, round_n)
            turn = turns.get(key, 0)
            turns[key] = turn + 1
            assert not {item['function']['name'] for item in body.get('tools', [])}.intersection(
                {'get_valuation', 'submit_candidate_model_plan', 'get_candidate_model_inputs'})
            requests.append({'desk': desk, 'round': round_n, 'body': body})
            tool_calls = None
            if desk == 'fundamentals' and round_n == 0 and turn == 0:
                tool_calls = [('acquire_company_source', {'ticker': ticker, 'source': {
                    'url': SITE + '/investors/' + suffix + '.html'}})
                    for ticker, suffix in [('SYNTH-A', 'a'), ('SYNTH-B', 'b')]]
                tool_calls += [('get_consensus_estimates', {'ticker': ticker}) for ticker in ('SYNTH-A', 'SYNTH-B')]
            elif desk == 'fundamentals' and round_n == 0 and turn == 1:
                tool_calls = []
                for ticker in ('SYNTH-A', 'SYNTH-B'):
                    snapshot = board.company_source_session_if_known(ticker).snapshot()
                    assert snapshot['documents'], snapshot
                    document_id = snapshot['documents'][0]['id']
                    tool_calls.append(('read_company_dossier', {'ticker': ticker,
                        'section': 'document', 'document_id': document_id}))
            elif desk == 'fundamentals' and round_n == 0 and turn == 2:
                for message in body['messages']:
                    if message.get('role') == 'tool':
                        result = json.loads(message['content'])
                        if result.get('document_id') and result.get('text'):
                            document_reads.append(result)
                assert len(document_reads) == 2
                assert all(row['complete'] and row['sha256'] for row in document_reads)
            if desk == 'fundamentals' and round_n == 1 and turn == 0:
                tool_calls = [('add_research_note', {'decision_id': 901, 'note_ids': [801],
                    'note': 'Synthetic answer to the ORIGINAL question [src: read_company_dossier]'})]
            if desk == 'capo' or desk == 'fundamentals' and round_n in (1,2):
                rendered = json.dumps(body, ensure_ascii=False)
                assert 'SYNTHETIC ORIGINAL PM QUESTION' in rendered
                assert 'SYNTHETIC ARCHIVED BACKGROUND' in rendered and 'CONTESTO_STORICO' in rendered
                assert 'SYNTHETIC VETO QUESTION' not in rendered
                assert 'note_ids' in rendered
            if board is not None and round_n == 2:
                ref = research_reference(board)
                assert ref['thesis_sha256'] in json.dumps(body)
            if desk in ('red_team', 'capo'):
                sealed = _store().get('research_dossier')
                assert sealed['dossier_sha256'] in json.dumps(body)
                assert sealed['thesis_sha256'] in json.dumps(body)
            content = ('# Memo settimanale\n\n## ACTION TABLE\nNessuna proposta operativa. '
                       'Esito RESEARCH per entrambi i titoli.\n\n' + reports * 7 if desk == 'capo'
                       else ('Obiezione: retention e margini restano da verificare. ' + reports * 2))
            return _provider_reply(body, content if tool_calls is None else '', tool_calls,
                                   ident='offline-weekly-' + str(len(requests)))
        return client_class(api_key='offline', max_retries=0, trasporto=httpx.MockTransport(send))

    originals = [cm.MacroSpecialist, cm.EventDeskSpecialist, cm.CryptoSpecialist,
                 cm.FundamentalsSpecialist, cm.QuantSpecialist, cm.OptionsSpecialist]
    native_classes = []
    for cls in originals:
        def initialize(self, board, *, original=cls):
            original.__init__(self, board, client=native_client(original.name, board))
        native_classes.append(type('Native' + cls.__name__, (cls,), {'__init__': initialize}))
    monkeypatch.setattr(cm, 'SPECIALIST_ORDER', native_classes)
    monkeypatch.setattr(red_team, 'run_red_team', native_red_team)
    monkeypatch.setattr(cm, 'run_capo', native_capo)
    monkeypatch.setattr(llm_client, 'OpenRouterClient', lambda *a, **k: native_client('red_team'))
    monkeypatch.setattr(capo, 'OpenRouterClient', lambda *a, **k: native_client('capo'))
    result = cm.run_multi_agent(send_email=True)
    store = _store()
    sealed = store.get('research_dossier')
    assert result['status'] == 'completed'
    frozen_notes = store.get('research_notes_context_v1')
    expected_notes = {row['note_id'] for row in frozen_notes['notes'] if row['status'] == 'included'}
    assert expected_notes == {801,803}
    for destination in ('fundamentals:1', 'fundamentals:2', 'capo'):
        delivery = store.get('research_notes_delivery_v1:' + destination)
        assert expected_notes == set(delivery['delivered_note_ids'])
        assert not (expected_notes & set(delivery['excluded_note_ids']))
        assert delivery['excluded_note_ids'] == [802]
        assert delivery['unknown_note_ids'] == []
    assert any(row['autore'] == 'AI' for row in MemoryDB().get_decision_notes(901))
    assert MemoryDB().get_decision_notes(902) == []
    with MemoryDB()._conn() as conn:
        replies = conn.execute("SELECT payload_json FROM weekly_checkpoints WHERE stage LIKE 'research_notes_reply_v1:%'").fetchall()
    assert len(replies) == 1
    linked_reply = json.loads(replies[0][0])
    assert linked_reply['decision_id'] == 901 and linked_reply['reply_to_note_ids'] == [801]
    assert len(transport.requests) == 2 and set(consensus_reads) == {'SYNTH-A', 'SYNTH-B'}
    assert sealed['tool_receipts'], 'Observed prices/consensus were not bound to the research seal'
    assert set(sealed['reports']) == {cls.name for cls in originals}
    assert {row['input']['ticker'] for row in sealed['tool_receipts']
            if row['tool'] == 'get_consensus_estimates'} == {'SYNTH-A', 'SYNTH-B'}
    assert result['request_costs']['unknown_requests'] == 0 and result['request_costs']['cost_usd'] > 0
    costs = deepcopy(result['request_costs'])
    decisions = store.get('decisions_finalized')
    report_count = len(requests)
    original_capo_request = deepcopy(native_boards[0].data['_capo_request'])
    # Late DB mutation cannot alter an already paid request or trigger another transport.
    MemoryDB().add_decision_note(901, 'PM', 'LATE QUESTION AFTER ACCEPTANCE')
    def no_recapture(*args, **kwargs):
        pytest.fail('A paid request attempted to reread live notes')
    monkeypatch.setattr(note_facts, 'capture_research_notes', no_recapture)
    for _ in range(2):
        with llm_client.request_scope(native_boards[0].weekly_store.request_journal,
                                     phase='capo', agent='capo', round_n=3):
            replay_text, replay_usage = native_capo(native_boards[0])
        assert replay_text == store.get('capo')['memo']
        assert replay_usage['complete'] is True
        assert native_boards[0].data['_capo_request'] == original_capo_request
        assert len(requests) == report_count
    assert store.get('research_notes_context_v1') == frozen_notes
    artifacts = {row['path']: row['sha256'] for row in result['artifacts']}
    for path in artifacts:
        assert Path(path).resolve().is_relative_to(tmp_path.resolve())
        Path(path).unlink()
    for _ in range(2):
        recovered = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True)
        assert recovered['status'] == 'completed'
        assert recovered['request_costs'] == costs
    assert len(requests) == report_count and len(observed.inviati) == 1
    assert store.get('decisions_finalized') == decisions
    assert len(transport.requests) == 2
    assert all(sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in artifacts.items())
    assert all(forbidden[key] == 0 for key in ('prepare_binding', 'compiler', 'coverage'))
    evidence = {'status': recovered['status'], 'source_requests': len(transport.requests),
        'provider_requests': report_count, 'smtp_acceptances_simulated': len(observed.inviati),
        'workbook_calls': {key: forbidden[key] for key in ('prepare_binding', 'compiler', 'coverage')},
        'research_ref': store.get('red_team')['research_ref'], 'costs': costs,
        'frozen_notes': frozen_notes, 'note_reply': linked_reply,
        'note_deliveries': {d: store.get('research_notes_delivery_v1:' + d)
                            for d in ('fundamentals:1','fundamentals:2','capo')},
        'transport_bodies': [r for r in requests if r['desk'] in ('fundamentals','capo')],
        'original_artifact_hashes': artifacts, 'remaining_work': recovered['remaining_work']}
    # ZR 05/10: la prova esportata esiste solo sotto tools/testing/offline_pytest.py, che crea
    # la sandbox; con pytest nudo (clone pulito) le asserzioni sopra restano tutte, l'export no
    # (stesso schema di test_documented_consumers / test_managed_care_integration).
    if os.environ.get('BELLOMBERG_OFFLINE_TEST_SANDBOX'):
        (Path(os.environ['BELLOMBERG_OFFLINE_TEST_SANDBOX']) / 'native-weekly-evidence.json').write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')


@pytest.mark.parametrize('fault', ['timeout', 'disconnect', 'incomplete', '503', '504'])
def test_native_research_provider_failure_keeps_unknown_cost_and_blocks_two_resumes(research_weekly, monkeypatch, fault):
    from bellomberg.core.llm_client import OpenRouterClient
    from bellomberg.valuation import preparation_ai
    from test_cablaggio_consigliere_multi import _DeskFundamentals
    monkeypatch.setattr(preparation_ai, 'live_metadata', lambda model: {
        'id': model, 'context_length': 1000, 'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})
    calls = []
    def send(request):
        calls.append(request)
        if fault == 'timeout':
            raise httpx.ReadTimeout('synthetic original research timeout')
        if fault == 'disconnect':
            raise httpx.RemoteProtocolError('synthetic original disconnection')
        if fault == 'incomplete':
            return httpx.Response(200, json={'id': 'incomplete-native', 'model': 'test/model',
                'choices': [{'message': {'content': 'partial response'}, 'finish_reason': None}]})
        return httpx.Response(int(fault), json={'error': {'code': int(fault), 'message': 'original provider fault'}})
    client = OpenRouterClient(api_key='offline', max_retries=4, trasporto=httpx.MockTransport(send))
    def fail(self, round_n):
        client.messages.create(model='test/model', max_tokens=20,
                               messages=[{'role': 'user', 'content': 'Frozen research request'}])
    monkeypatch.setattr(_DeskFundamentals, 'run', fail)
    with pytest.raises(Exception):
        cm.run_multi_agent(send_email=True)
    store = _store()
    first = store.status()
    for _ in range(2):
        with pytest.raises(Exception):
            cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=True)
    last = store.status()
    assert len(calls) == 1 and last['status'] == 'incomplete'
    assert last['first_error'] == first['first_error']
    assert last['request_costs'] == first['request_costs']
    assert last['request_costs']['unknown_requests'] == 1
    assert last['request_costs']['cost_usd'] is None and last['request_costs']['reserved_usd'] > 0
    assert not research_weekly[0].inviati


def test_native_reply_crash_proof_export_is_optional(research_weekly, monkeypatch, tmp_path):
    # Normal pytest/CI has no runner export directory; functional checks still run.
    monkeypatch.delenv('BELLOMBERG_OFFLINE_TEST_SANDBOX', raising=False)
    test_native_note_reply_crash_recovery_is_verified_and_never_redispatched(
        research_weekly, monkeypatch, tmp_path, 'before_commit')
