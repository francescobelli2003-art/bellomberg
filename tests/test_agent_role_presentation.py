"""Agent role labels are authored presentation; names and model IDs are data."""
from copy import deepcopy
import json
import sys
import types

from bellomberg.core.language import language_context
from bellomberg.core.presentation import render_payload


def test_cached_agents_list_switches_roles_without_model_resolution_or_data_changes(monkeypatch):
    from bellomberg.api import bellomberg_api as api
    from bellomberg.core.api_presentation import PresentationJSONResponse
    chat = types.ModuleType('bellomberg.agents.chat_engine')
    ids = ('capo', 'macro', 'quant', 'options', 'fundamentals', 'crypto', 'eventdesk', 'custom', 'politics', 'news')
    chat.AGENT_DISPLAY_NAMES = {key: 'Original desk name ' + key for key in ids}
    chat.LEGACY_AGENT_IDS = {'politics', 'news'}
    llm = types.ModuleType('bellomberg.core.llm_client')
    llm.DESK = ('macro',)
    calls = []
    def model(function, agent=None, **kw):
        calls.append((function, agent, kw))
        return 'synthetic-model:' + function + ':' + str(agent)
    llm.modello_o_buco = model
    monkeypatch.setitem(sys.modules, chat.__name__, chat)
    monkeypatch.setitem(sys.modules, llm.__name__, llm)
    with language_context('it'):
        payload = api.get_agents_list()
    before, resolved = deepcopy(payload), deepcopy(calls)
    with language_context('en'):
        english = render_payload(payload)
        body = json.loads(PresentationJSONResponse(english).body)
    roles_it = {row['id']: row['role'] for row in payload['agents']}
    roles_en = {row['id']: row['role'] for row in english['agents']}
    assert roles_it['macro'] == 'Stratega macroeconomico'
    assert roles_it['fundamentals'] == 'Analista fondamentale'
    assert roles_it['custom'] == 'Specialista'
    assert roles_en['macro'] == 'Macro Strategist'
    assert roles_en['fundamentals'] == 'Fundamentals Analyst'
    assert roles_en['custom'] == 'Specialist'
    assert 'politics' not in roles_en and 'news' not in roles_en
    assert calls == resolved and payload == before
    assert english['engines'] == payload['engines']
    assert [{k: v for k, v in row.items() if k != 'role'} for row in english['agents']] == [
        {k: v for k, v in row.items() if k != 'role'} for row in payload['agents']]
    metadata = body['_presentation_v1']['texts']
    assert len(metadata) == len(english['agents'])
    assert all(row['path'][0] == 'agents' and row['path'][-1] == 'role' for row in metadata)
