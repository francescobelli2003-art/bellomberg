"""Ordinary HTTP admission freezes the research mode and committee-only grant."""
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import pytest

from bellomberg.agents import trade_idea, trade_idea_sources as sources
from bellomberg.api import trade_idea_routes as routes
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.valuation import trade_idea_model as model, input_preparation
from test_trade_idea_store import db_path, migrated, store
from test_trade_idea_pipeline import _priced_request
from test_trade_idea_pm_sources import IDENTITY, URL, transport, _profile_providers


@pytest.mark.parametrize('partial', [False, True])
def test_normal_http_admission_start_retry_is_research_only(migrated, tmp_path, monkeypatch, partial):
    current = store(migrated)
    priced = _priced_request(budget='30')
    for row in priced['catalog_snapshot']['models'].values():
        row['supported_efforts'] = ['low', 'medium', 'max']
    download, source_calls = transport('Unreadable source; no primary issuer or period')
    original_ingest = sources.ingest_document_sources
    monkeypatch.setattr(sources, 'ingest_document_sources',
        lambda *a, **k: original_ingest(*a, **k, download=download))
    workers, modes = [], []
    def forbidden(*_a, **_k):
        pytest.fail('Research HTTP admission invoked an Excel driver gate')
    monkeypatch.setattr(input_preparation, 'has_approved_inputs', forbidden)
    def preflight(ticker, pm_view, view_source, budget, **options):
        modes.append(options.get('analysis_mode'))
        def qualify(t, identity, day, **kwargs):
            return model.research_admission(t, identity, day,
                providers=_profile_providers(day), analysis_mode=RESEARCH_ANALYSIS_MODE, **kwargs)
        return trade_idea.preflight_trade_idea(ticker, pm_view, view_source, budget,
            analysis_mode=options.get('analysis_mode'), archive_root=options['archive_root'],
            execution_policy=options.get('execution_policy'),
            source_qualifier=options.get('source_qualifier', qualify),
            document_sources=options.get('document_sources', []),
            catalog_fetcher=lambda: priced['catalog_snapshot'],
            identity_resolver=options.get('identity_resolver', lambda _: deepcopy(IDENTITY)),
            key_checker=lambda: None, mandate_loader=lambda: {}, active_checker=lambda: False)
    monkeypatch.setattr(routes, '_store', lambda *_a, **_k: current)
    monkeypatch.setattr(routes, 'preflight_trade_idea', preflight)
    monkeypatch.setattr(routes, '_spawn_worker', lambda rid, *_a, **_k: workers.append(rid))
    app = FastAPI()
    def session(x_bb_token: str = Header(default='')):
        if x_bb_token != 'research-offline':
            raise HTTPException(401)
        return x_bb_token
    routes.install_trade_idea_routes(app, session, db_path=migrated,
        source_archive_root=tmp_path / 'sources')
    body = {'ticker': IDENTITY['ticker'], 'pm_view': 'A hypothesis to test',
        'view_source': 'manual', 'budget_limit_usd': '30',
        'document_sources': [{'url': URL}] if partial else []}
    with TestClient(app, headers={'X-BB-Token': 'research-offline'}) as client:
        assert client.post('/trade-ideas/preflight', json=body,
            headers={'X-BB-Token': 'expired'}).status_code == 401
        assert modes == [] and workers == []
        checked = client.post('/trade-ideas/preflight', json=body)
        assert checked.status_code == 200 and checked.json()['ok'], checked.text
        public = checked.json()
        assert public['analysis_mode'] == RESEARCH_ANALYSIS_MODE
        assert public['preparation'] == {'required': False, 'paid': False, 'status': 'not_required'}
        assert public['document_sources']['status'] == ('needs_verification' if partial else 'not_supplied')
        assert '_source_qualification' not in public and str(tmp_path) not in checked.text
        start = {**body, 'cost_acknowledged': True, 'idempotency_key': 'new-research',
            'authorization': {'accepted': True, 'source_fingerprint': public['source_qualification']['fingerprint'],
                'activities': ['committee'], 'max_revision_rounds': 0}}
        accepted = client.post('/trade-ideas/runs', json=start)
        assert accepted.status_code == 202, accepted.text
        rid = accepted.json()['run_id']
        assert client.post('/trade-ideas/runs', json=start).json()['run_id'] == rid
        assert workers == [rid] and modes == [RESEARCH_ANALYSIS_MODE] * 2
        detail = current.get_run(rid)
        assert detail['run']['analysis_mode'] == RESEARCH_ANALYSIS_MODE
        # PM Lotto 2: new runs accept /4 (investment memo; Capo MEDIUM, 128000 output tokens as /3).
        assert detail['run']['execution_policy'] == 'trade-idea-research/4'
        assert {role: row['reasoning_effort'] for role, row in detail['run']['models'].items()} == {
            'specialist': 'medium', 'red_team': 'medium', 'capo': 'medium', 'aux': 'medium'}
        assert detail['run']['authorization'] == start['authorization']
        assert detail['cost']['requests'] == 0
        # B6 (09/10, Opus 5.5): la run salvata espone la controverifica fonti eseguita
        public_run = client.get('/trade-ideas/runs/' + rid)
        assert public_run.status_code == 200, public_run.text
        assert public_run.json()['run']['source_qualification']['execution_status'] == 'completed'
        assert 'source_qualification_execution' not in public_run.json()['run']
        assert [row['source_qualification']['execution_status']
                for row in client.get('/trade-ideas/runs').json()['runs']] == ['completed']
        assert len([row for row in source_calls if row[0] == 'request']) == int(partial)
        changed = deepcopy(start)
        changed['authorization']['activities'].append('model_preparation')
        assert client.post('/trade-ideas/runs', json=changed).status_code == 409
        assert workers == [rid] and current.list_runs()['total'] == 1
