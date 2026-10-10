"""T9: native weekly consumers of the combined follow-up contract, offline only.

Reuse the existing SQLite/PDF/SMTP harness and actual provider clients. Only
transport, account snapshots and unrelated market engines are synthetic.
"""
from copy import deepcopy
from functools import partial
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

from test_cablaggio_consigliere_multi import run_offline
from test_company_source_research import FrozenTransport, html, SITE
from test_gate_research_memo15 import _sealed_sources, MEMO
from test_reflection36_policy import score
from test_weekly_recovery import _store
from test_weekly_research_native import _provider_reply, native_red_team, native_capo
from test_weekly_research_without_workbook import research_weekly, _REAL_EXTRACT
from bellomberg.agents import agent_tools, consigliere_multi as cm
from bellomberg.core import freshness as _REAL_FRESHNESS


_NATIVE_POLYMARKET = agent_tools.tool_get_polymarket_events
_RELEASE_CHECK = _REAL_FRESHNESS.check_release_freshness
EXPIRY = '2099-06-18'
REPORT = ('Synthetic research: the available issuer statement has been read; the other '
          'issuer document is unavailable. Revenue, periods and currency require their '
          'own source and cannot be transferred between issuers. Options ATM and IV/HV '
          'remain unverified when quote currency or IV observation time is absent. '
          'Prediction candidates are not verified relevant markets, and their missing '
          'history is not zero change. Retention is a hypothesis, not an observed fact. '
          'Bear: persistent churn; base: stable retention; bull: improving retention. '
          'No AI fair value or economic certification is claimed. ')


def _native_committee(monkeypatch, *, populated, proposals=False):
    """Small wiring helper: all six desks, Red, Capo and tool loop stay native."""
    from bellomberg.agents import company_research_tools as bindings, red_team, capo, scorekeeper
    from bellomberg.agents.company_source_research import ResearchSession
    from bellomberg.core import llm_client, llm_pricing
    from bellomberg.valuation import preparation_ai
    from bellomberg.market_data import sec_xbrl, freschezza_trimestrale

    source = ('Synthetic issuer SYNTH-A\nPublished on 2026-09-09\nYear ended 2025-12-31\n'
              'Consolidated financial statements. Revenue 100 million EUR. '
              'Only this annual statement is available; interim statements are absent.')
    transport = FrozenTransport({'/investors/a.html': (html(source), 'text/html'),
        '/investors/b.html': (b'Synthetic document forbidden', 'text/html', 403, {})})
    original_bind = bindings.bind_weekly_company_research
    monkeypatch.setattr(bindings, 'bind_weekly_company_research', lambda board, store:
        original_bind(board, store, identity_resolver=lambda ticker: {
            'status': 'confirmed', 'ticker': ticker, 'name': 'Synthetic issuer ' + ticker,
            'exchange': 'XSYNTH', 'currency': 'EUR' if ticker == 'SYNTH-A' else 'USD'},
            profile_provider=lambda *a, **k: {'status': 'ok', 'data': {'info': {'website': SITE}}},
            session_factory=partial(ResearchSession, download=transport)))
    monkeypatch.setattr(preparation_ai, 'live_metadata', lambda model: {
        'id': model, 'context_length': 1000000, 'top_provider': {'max_completion_tokens': 128000},
        'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})
    monkeypatch.setattr(llm_pricing, '_fx_usd_to_eur', lambda: (0.9, 'offline synthetic FX'))
    releases = []
    def release_check(current, as_of):
        releases.append((deepcopy(current), as_of))
        return _RELEASE_CHECK(current, as_of)
    monkeypatch.setitem(sys.modules, 'bellomberg.core.freshness', _REAL_FRESHNESS)
    monkeypatch.setattr(_REAL_FRESHNESS, 'check_release_freshness', release_check)
    monkeypatch.setattr(_REAL_FRESHNESS, 'check_and_update', lambda *a: pytest.fail('Legacy freshness in new run'))
    monkeypatch.setattr(cm, 'tool_get_macro_dashboard', lambda: {'indicators': {
        'synthetic_missing': {'value': None, 'date': '2020-01-01', 'src': 'test'},
        'synthetic_value': {'value': 7.25, 'date': '2020-01-01', 'src': 'test'}}})
    # Numeric upstream sources are synthetic; dispatch, stamping, receipt and
    # follow-up projection stay real. Wrapper identity is deliberately unattested.
    financial_calls = []
    def financial_source(ticker, years, **kwargs):
        assert ticker == 'SYNTH-A' and years == 1
        financial_calls.append((ticker, years))
        return {'ticker': ticker, 'issuer_identity': {'status': 'PRIMARY_SOURCE_VERIFIED',
            'ticker': ticker, 'name': 'Synthetic issuer SYNTH-A', 'basis': 'synthetic filing'}}
    def financial_period(ticker, **kwargs):
        assert ticker == 'SYNTH-A'
        return {'stato': 'aggiornato', 'period_start': '2025-01-01', 'period_end': '2025-12-31',
            'duration': 'annual', 'fiscal_year_label': 'FY2025', 'unita': 'million', 'valuta': 'EUR',
            'definition': 'reported_revenue', 'valori': {'revenue': 100},
            'filing_date': '2026-09-09', 'fonte': 'synthetic frozen issuer'}
    monkeypatch.setattr(sec_xbrl, 'get_financial_history', financial_source)
    monkeypatch.setattr(freschezza_trimestrale, 'blocco_per_tool', financial_period)
    # Upstream score input is synthetic; T5 projection/formatter/assessment are real.
    sc = score(35)
    sc.update(n_directional_candidates=35, n_fetch_fail=0, degraded=False)
    monkeypatch.setattr(scorekeeper, 'compute_scorecard', lambda **kwargs: deepcopy(sc))
    action_table = ('| Action | Ticker | EUR | Timing | Confidence | Rationale |\n'
                    '|---|---|---|---|---|---|\n'
                    '| BUY | SYNTH-A | 100 | now | HIGH | Read issuer source |\n'
                    '| ADD | SYNTH-A | 50 | now | HIGH | Same sealed issuer evidence |\n'
                    '| BUY | SYNTH-B | 70 | now | HIGH | Missing issuer document |\n'
                    if proposals else 'Nessuna proposta operativa. Esito RESEARCH.\n')
    instruction = 'Aumento il sizing dei BUY in base al solo sottogruppo.'
    raw_capo = ('# Memo settimanale\n\n## ACTION TABLE\n' + action_table + '\n' + REPORT * 7
        + '\n' + instruction + '\n<scorecard_bindings>' + json.dumps({'rows': [{
            'group_ids': ['action:BUY'], 'use': 'sizing', 'instruction': instruction}]})
        + '</scorecard_bindings>')
    requests, boards, turns = [], [], {}
    client_class = llm_client.OpenRouterClient

    def client(desk, board=None):
        if board is not None and board not in boards:
            boards.append(board)

        def send(request):
            body = json.loads(request.content)
            round_n = board.current_round if board is not None else None
            key = (desk, round_n)
            turn = turns.get(key, 0)
            turns[key] = turn + 1
            requests.append({'desk': desk, 'round': round_n, 'body': body})
            calls = None
            if populated and desk == 'fundamentals' and round_n == 0:
                if turn == 0:
                    calls = [('acquire_company_source', {'ticker': ticker, 'source': {
                        'url': SITE + '/investors/' + suffix + '.html'}})
                        for ticker, suffix in [('SYNTH-A', 'a'), ('SYNTH-B', 'b')]]
                elif turn == 1:
                    document = board.company_source_session_if_known('SYNTH-A').snapshot()['documents'][0]
                    calls = [('read_company_dossier', {'ticker': 'SYNTH-A', 'section': 'document',
                                                       'document_id': document['id']}),
                             ('get_financial_history', {'ticker': 'SYNTH-A', 'years': 1})]
            if desk == 'options' and round_n == 1 and turn == 0:
                calls = [('get_options_data', {'ticker': 'SYNTH', 'expiry': EXPIRY})]
            if desk == 'eventdesk' and round_n == 1 and turn == 0:
                calls = [('get_polymarket_events', {'query': 'committee scenario', 'max_results': 3})]
            content = raw_capo if desk == 'capo' else REPORT * 2
            if desk == 'red_team':
                claims = []
                if populated:
                    supplied = body['messages'][-1]['content'].split(
                        '=== EVIDENZA DELLA RUN: METADATI E LACUNE ===', 1)[1].lstrip()
                    evidence, _ = json.JSONDecoder().raw_decode(supplied)
                    fact = next(f for f in evidence['facts'] if f['metric'] == 'revenue')
                    claims = [{'source_receipt': deepcopy(fact['source_receipt']),
                        'metric': 'revenue', 'value': 100, 'ticker': 'SYNTH-A', 'issuer_name': None,
                        'period_start': '2025-01-01', 'period_end': '2025-12-31', 'duration': 'annual',
                        'fiscal_year_label': 'FY2025', 'unit': 'million', 'currency': 'EUR',
                        'definition': 'reported_revenue', 'observed_at': fact['observed_at']}]
                content += '\n```evidence_review\n' + json.dumps({'claims': claims}) + '\n```'
            return _provider_reply(body, '' if calls else content, calls,
                                   ident='synthetic-followup-' + str(len(requests)))
        return client_class(api_key='offline', max_retries=0, trasporto=httpx.MockTransport(send))

    classes = [cm.MacroSpecialist, cm.EventDeskSpecialist, cm.CryptoSpecialist,
               cm.FundamentalsSpecialist, cm.QuantSpecialist, cm.OptionsSpecialist]
    native = []
    for cls in classes:
        def initialize(self, board, *, original=cls):
            original.__init__(self, board, client=client(original.name, board))
        native.append(type('Followup' + cls.__name__, (cls,), {'__init__': initialize}))
    monkeypatch.setattr(cm, 'SPECIALIST_ORDER', native)
    monkeypatch.setattr(red_team, 'run_red_team', native_red_team)
    monkeypatch.setattr(cm, 'run_capo', native_capo)
    monkeypatch.setattr(llm_client, 'OpenRouterClient', lambda *a, **k: client('red_team'))
    monkeypatch.setattr(capo, 'OpenRouterClient', lambda *a, **k: client('capo'))
    return SimpleNamespace(requests=requests, boards=boards, documents=transport,
                           raw_capo=raw_capo, desk_names={cls.name for cls in classes},
                           releases=releases, financial_calls=financial_calls)


def _market_transport(monkeypatch, mode):
    """Real Options and Polymarket adapters receive only these HTTP bodies."""
    from bellomberg.market_data import polygon_data
    sent = []
    monkeypatch.setattr(polygon_data, 'POLYGON_KEY', '' if mode == 'missing_key' else 'SYNTHETIC_KEY')
    monkeypatch.setattr(polygon_data, 'REQ_OK', True)
    monkeypatch.setattr(agent_tools, '_get_ibkr_options', lambda *a, **k: None)
    monkeypatch.setattr(agent_tools, 'YFINANCE_AVAILABLE', False)
    monkeypatch.setattr(agent_tools, 'tool_get_polymarket_events', _NATIVE_POLYMARKET)
    monkeypatch.setattr(agent_tools, '_POLY_CACHE', {})
    monkeypatch.setattr(agent_tools, '_POLY_BLOCKED', None)

    def get(url, params=None, **kwargs):
        sent.append({'url': url, 'params': {k: v for k, v in (params or {}).items() if k != 'apiKey'}})
        status = 200
        if url.startswith('https://api.polygon.io/'):
            assert mode != 'missing_key'
            if url.endswith('/v3/snapshot/options/SYNTH'):
                assert params['expiration_date'] == EXPIRY
                status = {'forbidden': 403, 'rate_limit': 429}.get(mode, 200)
                payload = {'results': [{'details': {'ticker': 'O:SYNTH-' + side,
                    'contract_type': side, 'strike_price': 120, 'expiration_date': EXPIRY},
                    'underlying_asset': {'price': 123, 'ticker': 'SYNTH',
                        'last_updated': 1767225600000000000, 'timeframe': 'DELAYED'},
                    'implied_volatility': .3, 'open_interest': 10} for side in ('call', 'put')]}
            elif url.endswith('/v3/reference/options/contracts'):
                # Existing renderer option-calendar acquisition is separate from
                # the desk's requested chain. Declare its own unavailable source.
                assert params['underlying_ticker'] in ('SYNTH-A', 'SYNTH-B')
                status, payload = 403, {'error': 'Synthetic appendix source unavailable'}
            else:
                pytest.fail('Unexpected Polygon URL: ' + url)
        elif url.startswith('https://gamma-api.polymarket.com/'):
            payload = {'events': []} if url.endswith('/public-search') else []
            if url.endswith('/markets'):
                payload = [{'id': 'synthetic-uncertain', 'slug': 'synthetic-untranslated',
                    'question': 'Decisione monetaria sintetica?', 'active': True, 'closed': False,
                    'endDate': '2099-01-01T00:00:00Z', 'outcomes': '["Yes","No"]',
                    'outcomePrices': '["0.4","0.6"]', 'volume24hr': 20}]
        else:
            pytest.fail('Unexpected market URL: ' + url)
        return SimpleNamespace(status_code=status, text='synthetic provider response',
                               json=lambda: deepcopy(payload))

    monkeypatch.setattr(polygon_data.requests, 'get', get)
    return sent


def _profile(monkeypatch, populated):
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.core import current_facts
    monkeypatch.setattr(current_facts, '_RESEARCH_CACHE', {'text': None, 'ts': 0.0})
    with MemoryDB()._conn() as conn:
        conn.execute('DELETE FROM positions')
        if populated:
            for ticker, currency in [('SYNTH-A', 'EUR'), ('SYNTH-B', 'USD')]:
                conn.execute('INSERT INTO positions(ticker,nome,quantita,prezzo_medio,valuta) VALUES(?,?,?,?,?)',
                             (ticker, 'Synthetic issuer ' + ticker, 1, 10, currency))
            for ident in (901, 902):
                conn.execute('INSERT INTO decisions(id,ticker,action,status,timestamp) VALUES(?,?,?,?,?)',
                    (ident, 'SYNTH-A', 'RESEARCH', 'PENDING', '2026-01-0' + str(ident - 900) + ' 09:00:00'))
            for ident in range(801, 862):
                conn.execute('INSERT INTO decision_notes(id,decision_id,autore,testo,timestamp) VALUES(?,?,?,?,?)',
                    (ident, 901, 'PM', 'SYNTHETIC OLD QUESTION ' + str(ident), '2026-01-01 09:30:00'))
        assert conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == (2 if populated else 0)
        assert conn.execute('SELECT COUNT(*) FROM decision_notes').fetchone()[0] == (61 if populated else 0)
    positions = [{'ticker': ticker, 'nome': 'Synthetic issuer ' + ticker, 'quantita': 1,
        'prezzo_medio': 10, 'valuta': currency, 'tipo': 'operating', 'valore_mercato_eur': 400,
        'fx_to_eur': fx} for ticker, currency, fx in [('SYNTH-A', 'EUR', 1), ('SYNTH-B', 'USD', .8)]]
    portfolio = {'positions': positions if populated else [], 'n_positions': 2 if populated else 0,
        'nav_total_eur': 1000 if populated else 0, 'totale_valore_mercato_eur': 800 if populated else 0,
        'cash_disponibile_eur': 200 if populated else 0, 'cash_source': 'sqlite:synthetic',
        'fx_pl_method': 'Synthetic explicit current FX; historical cost kept separately'}
    monkeypatch.setattr(MemoryDB, 'get_portfolio_summary', lambda self: deepcopy(portfolio))
    return portfolio


@pytest.mark.parametrize('populated,mode', [(False, 'missing_key'), (True, 'observed_unqualified'),
                                           (True, 'forbidden'), (True, 'rate_limit')])
def test_native_followup_profiles_options_partial_sources_and_frozen_replay(
        research_weekly, monkeypatch, tmp_path, populated, mode):
    """Catches lost policy/metadata at native request, seal, publication or resume."""
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.core import llm_client, current_facts, evidence_followup_policy as followup
    from bellomberg.agents import action_validator
    observed, forbidden = research_weekly
    portfolio = _profile(monkeypatch, populated)
    market_requests = _market_transport(monkeypatch, mode)
    proposals = mode == 'observed_unqualified'
    native = _native_committee(monkeypatch, populated=populated, proposals=proposals)
    if proposals:
        monkeypatch.setattr(MemoryDB, 'extract_and_save_decisions', _REAL_EXTRACT)
        monkeypatch.setattr(sys.modules['bellomberg.portfolio.portfolio_risk'], 'compute_portfolio_risk',
            lambda: {'portfolio': {'var_95_1d_pct': -1.2}, 'source': 'synthetic frozen risk'})
        monkeypatch.setattr(sys.modules['bellomberg.portfolio.sizing_engine'], 'compute_sizing',
            lambda *a, **k: {'summary': {'n_positions': 2, 'deployable_from_cash_eur': 200},
                             'positions': [], 'source': 'synthetic frozen sizing'})
        # A real foreign sidecar is present, but research must never consult it.
        Path(cm.REPORT_DIR, 'VAL_SYNTH-A.payload.json').write_text(json.dumps({
            'run_id': 'foreign-historical-run', 'sanity': {'severity': 'BLOCK'}}), encoding='utf-8')
        monkeypatch.setattr(action_validator, '_sanity_payload',
                            lambda *a, **k: pytest.fail('Research consulted foreign legacy sidecar'))
    result = cm.run_multi_agent(send_email=True)
    store, board = _store(), native.boards[0]
    assert result['status'] == 'completed'
    contract = deepcopy(store.context['contract'])
    assert contract[followup.KEY] == followup.POLICY and contract[followup.AS_OF_KEY]
    assert contract['research_notes_policy'] == 'weekly-research-notes/1'
    assert contract['reflection_policy'] == 'weekly-reflection36/1'
    assert store.context['portfolio'] == portfolio
    priming = store.get('priming')
    freshness = priming['freshness_report']
    assert freshness['schema'] == 'release-freshness/1' and freshness['checked'] == 2
    assert freshness['as_of'] == contract[followup.AS_OF_KEY]
    assert freshness['observations']['test:synthetic_missing']['status'] == 'UNKNOWN'
    assert len(native.releases) == 1 and native.releases[0][0]['test:synthetic_missing']['value'] is None
    sealed = store.get('research_dossier')
    assert set(sealed['reports']) == native.desk_names
    options = [r for r in sealed['tool_receipts'] if r['tool'] == 'get_options_data']
    assert len(options) == 1 and options[0]['input'] == {'ticker': 'SYNTH', 'expiry': EXPIRY}
    option_output = json.loads(options[0]['output'])
    option_data = option_output.get('data', option_output)
    polygon_calls = [r for r in market_requests if r['url'].endswith('/v3/snapshot/options/SYNTH')]
    assert len(polygon_calls) == (0 if mode == 'missing_key' else 1)
    if proposals:
        assert option_data['spot_observation']['price'] == 123
        assert option_data['spot_status'] == 'UNVERIFIED_CURRENCY'
        assert option_data['spot'] is None and option_data['atm_iv_call_pct'] is None
        assert option_data['iv_hv_comparison_status'] == 'UNVERIFIED'
    else:
        reason = 'chiave o modulo assente' if mode == 'missing_key' else 'HTTP ' + ('403' if mode == 'forbidden' else '429')
        assert reason in json.dumps(option_output) and option_output.get('status') != 'ok'
    prediction = next(r for r in sealed['tool_receipts'] if r['tool'] == 'get_polymarket_events')
    prediction_output = json.loads(prediction['output'])
    prediction_data = prediction_output.get('data', prediction_output)
    # R11 p.5 (Opus 5.5): the only candidate has no lexical/server match, so it is declared
    # (identity, UNVERIFIED, no prices) and the outcome is "nothing found with these queries".
    assert prediction_data['count'] == 0 and prediction_data['relevant_count'] is None
    assert prediction_data['search_outcome'] == 'NO_MARKET_FOUND_WITH_THESE_QUERIES'
    withheld = prediction_data['coverage']['withheld_nonlexical_candidates']
    assert [w['url'].rsplit('/', 1)[-1] for w in withheld] == ['synthetic-untranslated']
    assert withheld[0]['search_match']['relevance_status'] == 'UNVERIFIED' and 'prices' not in withheld[0]
    frozen_notes = store.get('research_notes_context_v1')
    assert {n['note_id'] for n in frozen_notes['notes']} == (set(range(801, 862)) if populated else set())
    included = {n['note_id'] for n in frozen_notes['notes'] if n['status'] == 'included'}
    for target in ('fundamentals:1', 'fundamentals:2', 'capo'):
        assert set(store.get('research_notes_delivery_v1:' + target)['delivered_note_ids']) == included
    wires = {desk: json.dumps([r['body'] for r in native.requests if r['desk'] == desk])
             for desk in native.desk_names | {'red_team', 'capo'}}
    assert all('weekly-evidence-followup/1' in wire for wire in wires.values())
    assert 'count tecnico' in wires['eventdesk'] and 'EVIDENZA DELLA RUN' in wires['red_team']
    if populated:
        # The latest PM note on the old decision is delivered first; older notes
        # outside the unchanged context budget remain explicitly pending, not lost.
        assert 'SYNTHETIC OLD QUESTION 861' in wires['capo']
        assert frozen_notes['texts']['801'] == 'SYNTHETIC OLD QUESTION 801'
        assert frozen_notes['status'] == 'INCOMPLETO'
        assert any(n['status'] == 'pending' and n['reason'] == 'budget_chars' for n in frozen_notes['notes'])
    else:
        assert 'SYNTHETIC OLD QUESTION' not in wires['capo']
    red_checkpoint = board.specialist_checkpoints['red_team:R1']
    assert red_checkpoint['evidence_followup_snapshot']['policy'] == followup.POLICY
    assert red_checkpoint['evidence_followup_snapshot']['semantic_scope'] == 'NOT_ASSESSED'
    assert red_checkpoint['evidence_review']['evidence_sha256']
    assert red_checkpoint['evidence_review']['response_sha256']
    if populated:
        fact = next(f for f in red_checkpoint['evidence_followup_snapshot']['facts'] if f['metric'] == 'revenue')
        assert (fact['value'], fact['period_start'], fact['period_end'], fact['unit'], fact['currency']) == (
            100, '2025-01-01', '2025-12-31', 'million', 'EUR')
        assert fact['source_receipt']['metadata_paths'] == ['/data/ultimo_periodo_pubblicato']
        finding = red_checkpoint['evidence_review']['claims'][0]
        assert finding['status'] == 'UNVERIFIED'
        assert set(finding['reasons']) == {'MISSING_OR_UNSUPPORTED:issuer_name', 'MISSING_OR_UNSUPPORTED:identity_basis'}
    scope = store.get('capo')['usage']['scorecard_scope']
    assert scope['status'] == 'REJECTED_BINDINGS' and scope['semantic_scope'] == 'NOT_ASSESSED'
    assert scope['rejected_bindings'][0]['code'] == 'GROUP_NOT_OPERATIONAL'
    assert store.get('capo')['usage']['complete'] is True
    assert native.raw_capo in store.get('capo')['memo']
    assert store.get('memo_facts_v1')['error'] is None
    if populated:
        assert len(native.documents.requests) == 2
        assert len(sealed['dossiers']['SYNTH-A']['documents']) == 1
        assert sealed['dossiers']['SYNTH-B']['documents'] == []
        assert sealed['dossiers']['SYNTH-B']['issues']
        assert not sealed['dossiers']['SYNTH-A']['research_complete']
    else:
        assert not native.documents.requests and sealed['dossiers'] == {}
    if proposals:
        rows = store.get('memo_validated')['publication']['assessments']
        assert [(r['action'], r['ticker'], r['status']) for r in rows] == [
            ('BUY', 'SYNTH-A', 'OPERATIVE'), ('ADD', 'SYNTH-A', 'OPERATIVE'),
            ('BUY', 'SYNTH-B', 'CHECK_UNAVAILABLE')]
    costs = deepcopy(result['request_costs'])
    assert costs['unknown_requests'] == 0
    assert costs['native_request_count'] == len(native.requests)
    assert costs['cost_usd'] == pytest.approx(.002 * len(native.requests))
    assert len({r['request_id'] for r in costs['requests']}) == len(native.requests)
    request = deepcopy(board.data['_capo_request'])
    market_count = len(market_requests)
    if populated:
        MemoryDB().add_decision_note(901, 'PM', 'SYNTHETIC LATE NOTE MUST NOT ENTER PAID REQUEST')
    monkeypatch.setattr(current_facts, 'capture_research_notes', lambda *a, **k: pytest.fail('Live notes recaptured'))
    monkeypatch.setattr(_REAL_FRESHNESS, 'check_release_freshness', lambda *a: pytest.fail('Release freshness reacquired'))
    for _ in range(2):
        with llm_client.request_scope(board.weekly_store.request_journal, phase='capo', agent='capo', round_n=3):
            replay, usage = native_capo(board)
        assert replay == store.get('capo')['memo'] and usage['scorecard_scope'] == scope
        recovered = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=True)
        assert recovered['request_costs'] == costs
    assert board.data['_capo_request'] == request and store.context['contract'] == contract
    assert store.get('research_notes_context_v1') == frozen_notes
    assert store.get('priming') == priming and len(native.releases) == 1
    assert len(native.requests) == costs['native_request_count'] and len(market_requests) == market_count
    assert native.financial_calls == ([('SYNTH-A', 1)] if populated else [])
    assert len(observed.inviati) == 1
    assert all(forbidden[k] == 0 for k in ('prepare_binding', 'compiler', 'coverage'))
    if os.environ.get('BELLOMBERG_OFFLINE_TEST_SANDBOX'):
        Path(os.environ['BELLOMBERG_OFFLINE_TEST_SANDBOX'], 'followup-' + mode + '.json').write_text(
            json.dumps({'contract': contract, 'portfolio': portfolio, 'costs': costs, 'scope': scope,
                'options': option_output, 'prediction': prediction_output, 'notes': frozen_notes,
                'requests': native.requests, 'freshness': freshness,
                'red_review': red_checkpoint['evidence_review'],
                'simulated_smtp_acceptances': len(observed.inviati)}, indent=2),
            encoding='utf-8')


@pytest.mark.parametrize('missing', ['mandate', 'risk', 'sizing'])
def test_followup_native_publication_keeps_each_missing_control_closed(research_weekly, monkeypatch, missing):
    """A new follow-up marker cannot excuse an absent original research guard."""
    from bellomberg.agents.weekly_lifecycle import create_run
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.valuation import company_dossier
    from bellomberg.core import evidence_followup_policy as followup
    db = MemoryDB()
    contract = cm._weekly_contract()
    assert contract[followup.KEY] == followup.POLICY
    store = create_run(db, {'positions': [], 'n_positions': 0}, {'synthetic': 'mandate'}, contract, 'it')
    store.request_journal = None
    sealed = _sealed_sources()
    store.complete('research_dossier', sealed)
    store.complete('synthesis_context', {'risk_data': None if missing == 'risk' else {'source': 'synthetic risk'}})
    if missing == 'mandate':
        store.context.pop('mandate_sha256')
    board = SimpleNamespace(analysis_mode=contract['analysis_mode'], run_scope='weekly',
        data={'_research_thesis': sealed, '_sizing': None if missing == 'sizing' else {'source': 'synthetic sizing'}},
        valuation_results={}, read=lambda desk, round_n: sealed['reports'][desk])
    monkeypatch.setattr(company_dossier, 'dossier_for_board', lambda bb, ticker: sealed['dossiers'][ticker])
    result = cm._publication_gate(board, db, store, store.memo_id, MEMO, MEMO, None, [])
    row = result['publication']['assessments'][0]
    assert row['status'] == 'CHECK_UNAVAILABLE' and missing in row['reason']
