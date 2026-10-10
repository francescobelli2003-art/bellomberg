"""Native tool and cached HTTP adapter, synthetic Gamma payloads only."""
import json
from types import SimpleNamespace
import pytest
from test_macro_polymarket_resilience import poly


def market(**changes):
    return {'slug': 'fed-market', 'question': 'Fed rate?', 'active': True, 'closed': False,
            'endDate': '2099-01-01T00:00:00Z', 'outcomes': '["Yes", "No"]',
            'outcomePrices': '["0.25", "0.75"]', 'volume24hr': 12, **changes}


def event(**changes):
    return {'slug': 'fed-event', 'title': 'Fed rates', 'active': True, 'closed': False,
            'endDate': '2099-01-01T00:00:00Z', 'markets': [market()], 'category': 'Economics', **changes}


def install(poly, monkeypatch, *, events=None, singles=None, source='public-search'):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        endpoint = url.rsplit('/', 1)[-1]
        if endpoint == 'public-search':
            body = {'events': events or [] if source == endpoint else []}
        elif endpoint == 'events':
            body = events or [] if source == endpoint else []
        else:
            body = singles or []
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(poly._req, 'get', get)
    return calls


@pytest.mark.parametrize('source', ['public-search', 'events', 'markets'])
@pytest.mark.parametrize('negative', [{'closed': True}, {'archived': True}, {'active': False}, {'endDate': '2000-01-01T00:00:00Z'}])
def test_negative_state_or_expiry_prevails(poly, monkeypatch, source, negative):
    install(poly, monkeypatch, events=[event(**negative)] if source != 'markets' else [],
            singles=[market(**negative)] if source == 'markets' else [], source=source)
    out = poly.tool_get_polymarket_events('fed')
    assert out['results'] == [] and out['count'] == 0
    assert out['coverage']['excluded_inactive'] == 1
    assert out['coverage']['exclusion_reasons']


@pytest.mark.parametrize('changes,reason', [
    ({'endDate': None}, 'end_date_missing'), ({'endDate': 'invalid'}, 'end_date_invalid'),
    ({'endDate': '2099-01-01'}, 'end_date_timezone_unknown'),
    ({'endDate': '2099-01-01T12:00:00'}, 'end_date_timezone_unknown'),
    ({'active': None}, 'active_not_confirmed'), ({'closed': None}, 'closed_not_confirmed'),
])
def test_uncertain_event_is_not_called_active(poly, monkeypatch, changes, reason):
    install(poly, monkeypatch, events=[event(**changes)])
    out = poly.tool_get_polymarket_events('fed')
    group = out['results'][0]
    assert group['activity_status'] == 'unknown' and group['active_markets'] == 0
    assert reason in group['activity_reasons']
    assert out['coverage_verified'] is False


def test_active_positive_raw_probabilities_and_cache(poly, monkeypatch):
    calls = install(poly, monkeypatch, events=[event()])
    first = poly.tool_get_polymarket_events('fed')
    count = len(calls)
    second = poly.tool_get_polymarket_events('fed')
    group = first['results'][0]
    assert first == second and len(calls) == count
    assert group['activity_status'] == 'active' and group['active_markets'] == 1
    assert group['markets'][0]['prices'] == ['0.25', '0.75']
    assert group['markets'][0]['outcomes'] == ['Yes', 'No']
    assert group['category'] == 'Economics'
    assert first['coverage_verified'] is False and first['completeness'] == 'unknown'
    assert 'aggregate' in first['note'].lower()


def test_filter_then_cap_and_explicit_submarket_counts(poly, monkeypatch):
    children = [market(slug='closed', closed=True)] + [market(slug=str(i)) for i in range(7)]
    children.append(market(slug='unknown', endDate=None))
    install(poly, monkeypatch, events=[event(markets=children)])
    group = poly.tool_get_polymarket_events('fed')['results'][0]
    assert group['active_markets'] == 7
    assert group['market_coverage'] == {'observed': 9, 'excluded_inactive': 1, 'eligible': 8,
        'returned': 5, 'omitted_limit': 3, 'unknown_activity': 1, 'completeness': 'partial'}
    assert len(group['markets']) == 5 and all(m['activity_status'] == 'active' for m in group['markets'])


def test_final_limit_declares_known_omissions(poly, monkeypatch):
    install(poly, monkeypatch, events=[event(slug='event-' + str(i)) for i in range(4)])
    out = poly.tool_get_polymarket_events('fed', max_results=2)
    assert out['count'] == 2 and out['coverage']['eligible_candidates'] == 4
    assert out['coverage']['omitted_result_limit'] == 2 and out['completeness'] == 'partial'


def test_full_page_budget_does_not_claim_complete(poly, monkeypatch):
    def get(url, **kwargs):
        endpoint = url.rsplit('/', 1)[-1]
        offset = kwargs['params'].get('offset', 0)
        body = {'events': []} if endpoint == 'public-search' else [
            event(slug=f'{endpoint}-{offset}-{i}') for i in range(500)] if endpoint == 'events' else []
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(poly._req, 'get', get)
    out = poly.tool_get_polymarket_events('fed')
    assert '/events' in out['coverage']['limits_reached']
    assert out['coverage']['observed_by_source']['/events'] == 1500
    assert out['coverage_verified'] is False and out['completeness'] == 'partial'


def test_closed_parent_cannot_reappear_as_active_single_market(poly, monkeypatch):
    install(poly, monkeypatch, events=[event(closed=True)], singles=[market()])
    out = poly.tool_get_polymarket_events('fed')
    assert out['results'] == []
    assert 'parent_inactive' in out['coverage']['exclusion_reasons']


def test_embedded_closed_parent_overrides_single_active_flag(poly, monkeypatch):
    install(poly, monkeypatch, singles=[market(events=[event(closed=True)])])
    out = poly.tool_get_polymarket_events('fed')
    assert out['results'] == []
    assert 'parent_inactive' in out['coverage']['exclusion_reasons']


@pytest.mark.parametrize('embedded', [False, True])
def test_unknown_parent_does_not_become_active_through_single_endpoint(poly, monkeypatch, embedded):
    install(poly, monkeypatch, events=[] if embedded else [event(endDate=None)],
            singles=[market(events=[event(endDate=None)])] if embedded else [market()])
    out = poly.tool_get_polymarket_events('fed')
    single = next(r for r in out['results'] if r['type'] == 'single_market')
    assert single['activity_status'] == 'unknown'
    assert 'parent_activity_unknown' in single['activity_reasons']


def test_non_boolean_archived_cannot_certify_active(poly, monkeypatch):
    install(poly, monkeypatch, events=[event(archived='true')])
    group = poly.tool_get_polymarket_events('fed')['results'][0]
    assert group['activity_status'] == 'unknown' and group['active_markets'] == 0


def test_nested_keyword_scan_limit_is_declared(poly, monkeypatch):
    children = [market(question='something else') for _ in range(31)]
    children[-1]['question'] = 'Fed rate?'
    install(poly, monkeypatch, events=[event(title='Other', slug='other', markets=children)], source='events')
    out = poly.tool_get_polymarket_events('fed')
    assert '/events:submarket_keyword_scan' in out['coverage']['limits_reached']


@pytest.mark.parametrize('change', [{'endDate': None}, {'endDate': '2099-01-01'}, {'active': None}, {'closed': True}])
def test_real_score_does_not_use_uncertain_or_closed_child(poly, monkeypatch, change):
    from bellomberg.agents import specialist_scores as ss
    child = market(question='US recession?', **change)
    install(poly, monkeypatch, events=[event(title='US recession', markets=[child])])
    out = poly.tool_get_polymarket_events('us recession')
    monkeypatch.setattr(poly, 'tool_get_polymarket_events', lambda *a, **k: out)
    scored = ss.politics_score()
    assert scored is None or 'recessione USA' not in scored['metrics']['topics']


@pytest.mark.parametrize('field,value', [('activity_status', 'unknown'), ('activity_status', 'inactive'), ('end_date', None), ('end_date', '2099-01-01')])
def test_consumer_rejects_invalid_quality_without_parent_date_substitution(field, value):
    from datetime import datetime, timezone
    from bellomberg.agents import specialist_scores as ss
    child = {'question': 'US recession?', 'outcomes': ['Yes', 'No'], 'prices': ['0.25', '0.75'],
             'end_date': '2099-01-01T00:00:00Z', 'activity_status': 'active', field: value}
    group = {'type': 'event_group', 'end_date': '2099-01-01T00:00:00Z',
             'activity_status': 'active', 'markets': [child]}
    selected = ss._poli_scegli_mercato([group], ss._POLI_TERMINI['recessione USA'], datetime.now(timezone.utc))
    assert selected[0] is None and selected[1] is None and selected[2] == 1


def test_legacy_score_selection_is_explicitly_limited_and_active_new_contract_works():
    from datetime import datetime, timezone
    from bellomberg.agents import specialist_scores as ss
    row = {'question': 'US recession?', 'outcomes': ['Yes', 'No'], 'prices': ['0.25', '0.75'],
           'end_date': '2099-01-01T00:00:00Z'}
    legacy = ss._poli_scegli_mercato([row], ss._POLI_TERMINI['recessione USA'], datetime.now(timezone.utc))
    assert legacy[0] == 0.25
    assert legacy[1]['activity_verification'] == 'legacy_date_and_unresolved_price_only'
    row['activity_status'] = 'active'
    current = ss._poli_scegli_mercato([row], ss._POLI_TERMINI['recessione USA'], datetime.now(timezone.utc))
    assert current[0] == 0.25 and current[1]['activity_verification'] == 'explicit_active'


def test_native_missing_flags_cannot_reuse_old_positive_fixture(poly, monkeypatch):
    from bellomberg.agents import specialist_scores as ss
    child = market(question='synthetic tail?', outcomePrices='["0.8","0.2"]')
    child.pop('active'); child.pop('closed')
    group = event(title='synthetic event', markets=[child])
    group.pop('active'); group.pop('closed')
    install(poly, monkeypatch, events=[group])
    out = poly.tool_get_polymarket_events('synthetic')
    assert out['results'][0]['activity_status'] == 'unknown'
    monkeypatch.setattr(poly, 'tool_get_polymarket_events', lambda *a, **k: out)
    monkeypatch.setattr(ss, '_POLI_TOPICS', {'Synthetic': 'synthetic'})
    assert ss.politics_score() is None


def test_single_market_preserves_existing_question_limit(poly, monkeypatch):
    question = 'Fed ' + 'x' * 196
    install(poly, monkeypatch, singles=[market(question=question)])
    out = poly.tool_get_polymarket_events('fed')
    assert out['results'][0]['question'] == question


@pytest.mark.parametrize('conflict', ['same_event', 'same_child', 'other_parent', 'malformed_parent'])
def test_later_negative_evidence_retracts_earlier_projection(poly, monkeypatch, conflict):
    from bellomberg.agents import specialist_scores as ss
    child = market(id='m1', question='US recession?', outcomePrices='["0.8","0.2"]')
    first = event(id='e1', title='US recession', markets=[child])
    search, events, singles = [first], [], []
    if conflict == 'same_event':
        events = [{**first, 'closed': True}]
    elif conflict == 'same_child':
        singles = [{**child, 'closed': True}]
    elif conflict == 'other_parent':
        events = [{**first, 'id': 'e2', 'slug': 'other-parent', 'closed': True}]
    else:
        search = []
        singles = [{**child, 'events': ['invalid-parent']}]
    def get(url, **kwargs):
        body = {'events': search} if url.endswith('/public-search') else events if url.endswith('/events') else singles
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(poly._req, 'get', get)
    out = poly.tool_get_polymarket_events('us recession')
    monkeypatch.setattr(poly, 'tool_get_polymarket_events', lambda *a, **k: out)
    monkeypatch.setattr(ss, '_POLI_TOPICS', {'recessione USA': 'us recession'})
    assert ss.politics_score() is None
    if conflict == 'malformed_parent':
        assert out['results'][0]['activity_status'] == 'unknown'
    else:
        assert not any(m['activity_status'] == 'active' for r in out['results'] for m in r.get('markets', []))


def test_country_search_never_promotes_sports_other_country_or_translation(poly, monkeypatch):
    rows = [event(slug='cup', title='Norland football cup', markets=[]),
            event(slug='other', title='Solaria election', markets=[]),
            event(slug='translated', title='Eleicao presidencial Norland', markets=[]),
            event(slug='short-title', title='Who will win?', markets=[])]
    calls = install(poly, monkeypatch, events=rows)
    out = poly.tool_get_polymarket_events('Norland election')
    assert out['count'] == len(rows)
    assert out['count_basis'] == 'technical_candidates_not_verified_relevant_markets'
    assert out['relevant_count'] is None and out['relevance_status'] == 'NOT_ASSESSED'
    by_slug = {r['url'].rsplit('/', 1)[-1]: r for r in out['results']}
    assert set(by_slug) == {r['slug'] for r in rows}  # No blind lexical rejection.
    assert by_slug['cup']['search_match']['missing_query_terms'] == ['election']
    assert by_slug['other']['search_match']['missing_query_terms'] == ['norland']
    assert by_slug['short-title']['search_match']['basis'] == 'server_search_only'
    assert all(r['search_match']['relevance_status'] == 'UNVERIFIED' for r in by_slug.values())
    assert out['query'] == 'Norland election'
    assert len(out['coverage']['pages']) == len(calls)
    assert out['coverage']['pages'][0]['query'] == out['query']


def test_search_records_why_expired_candidate_was_excluded_and_history_is_missing(poly, monkeypatch):
    install(poly, monkeypatch, events=[event(slug='past', endDate='2000-01-01T00:00:00Z',
                                           markets=[market(slug='past-child')]), event()])
    out = poly.tool_get_polymarket_events('fed')
    excluded = out['coverage']['excluded_candidates']
    assert excluded[0]['slug'] == 'past' and 'expired' in excluded[0]['reasons']
    assert excluded[0]['end_date'] == '2000-01-01T00:00:00Z'
    history = out['results'][0]['markets'][0]['historical_change']
    assert history['delta_7d'] is None and history['delta_30d'] is None
    assert history['status'] == 'UNAVAILABLE'
    assert 'COMPARABLE_HISTORY_NOT_FETCHED' in history['reason']


def test_zero_candidates_is_only_the_observed_search(poly, monkeypatch):
    install(poly, monkeypatch)
    out = poly.tool_get_polymarket_events('Norland election')
    assert out['count'] == 0 and out['relevant_count'] is None
    assert out['coverage_verified'] is False
    assert len(out['coverage']['pages']) == 3
    assert 'not evidence of market absence' in out['note']


@pytest.mark.parametrize('source', ['events', 'markets'])
def test_local_translated_and_incomplete_candidates_keep_identity_not_prices_without_lexical_match(poly, monkeypatch, source):
    titles = [('translated', 'Eleicao no Pais Azul'), ('incomplete', 'Who will win?')]
    rows = ([event(slug=slug, title=title, markets=[]) for slug, title in titles]
            if source == 'events' else [market(slug=slug, question=title) for slug, title in titles])
    install(poly, monkeypatch, events=rows if source == 'events' else [],
            singles=rows if source == 'markets' else [], source=source)
    out = poly.tool_get_polymarket_events('Norland election')
    # R11 p.5 (Opus 5.5): without any lexical/server candidate they are not returned as
    # results with prices, but they survive as declared identities (no blind rejection).
    assert out['results'] == [] and out['search_outcome'] == 'NO_MARKET_FOUND_WITH_THESE_QUERIES'
    withheld = out['coverage']['withheld_nonlexical_candidates']
    assert {r['url'].rsplit('/', 1)[-1] for r in withheld} == {slug for slug, _ in titles}
    assert {r['title'] for r in withheld} == {title for _, title in titles}
    assert out['coverage']['lexical_nonmatches'] == len(rows)
    assert all(r['search_match']['relevance_status'] == 'UNVERIFIED' for r in withheld)
    assert all(r['search_match']['basis'] == 'local_no_lexical_match' for r in withheld)
    assert out['relevant_count'] is None
    # Declared limit (riserva 3): the identity survives, the prices do not; the tool says
    # to retry in English and keeps each withheld candidate's activity state.
    assert all(r['activity_status'] == 'active' for r in withheld)
    assert '"prices"' not in json.dumps(out)
    assert 'retry the call with English terms' in out['coverage']['withheld_reason']
    assert 'termini inglesi' in out['hint']


@pytest.mark.parametrize('source', ['events', 'markets'])
def test_uncertain_local_candidates_keep_existing_cap_and_rank_after_keyword_candidates(poly, monkeypatch, source):
    titles = [('translated', 'Eleicao no Pais Azul', 999), ('matching', 'Norland election', 1)]
    rows = ([event(slug=slug, title=title, volume24hr=volume, markets=[]) for slug, title, volume in titles]
            if source == 'events' else [market(slug=slug, question=title, volume24hr=volume) for slug, title, volume in titles])
    install(poly, monkeypatch, events=rows if source == 'events' else [],
            singles=rows if source == 'markets' else [], source=source)
    out = poly.tool_get_polymarket_events('Norland election', max_results=1)
    assert len(out['results']) == 1 and out['results'][0]['url'].endswith('/matching')
    assert out['coverage']['eligible_candidates'] == 2 and out['coverage']['omitted_result_limit'] == 1
    assert out['coverage']['local_nonlexical_candidates'] == 1
    assert out['relevant_count'] is None and out['results'][0]['search_match']['relevance_status'] == 'UNVERIFIED'


def test_nested_expired_market_keeps_identity_expiry_and_exclusion_reason(poly, monkeypatch):
    expired = market(id='SYNTH-EXPIRED', slug='expired-child', endDate='2000-01-01T00:00:00Z')
    active = market(id='SYNTH-ACTIVE', slug='active-child')
    install(poly, monkeypatch, events=[event(id='SYNTH-PARENT', markets=[expired, active])])
    group = poly.tool_get_polymarket_events('fed')['results'][0]
    assert group['market_coverage']['excluded_inactive'] == 1
    excluded = group['excluded_markets'][0]
    assert excluded['id'] == 'SYNTH-EXPIRED' and excluded['slug'] == 'expired-child'
    assert excluded['end_date'] == '2000-01-01T00:00:00Z' and 'expired' in excluded['reasons']
    assert excluded['parent'] == {'id': 'SYNTH-PARENT', 'slug': 'fed-event'}
    assert excluded['source'] == '/public-search'
    assert group['excluded_markets_omitted'] == 0


def test_nested_exclusion_sample_declares_omitted_identities(poly, monkeypatch):
    expired = [market(id=f'OLD-{n}', slug=f'old-{n}', closed=True) for n in range(13)]
    install(poly, monkeypatch, events=[event(markets=expired + [market()])])
    group = poly.tool_get_polymarket_events('fed')['results'][0]
    assert group['market_coverage']['excluded_inactive'] == 13
    assert len(group['excluded_markets']) == 10 and group['excluded_markets_omitted'] == 3
    assert all(r['id'] and r['slug'] and 'closed' in r['reasons'] for r in group['excluded_markets'])


ABSENCE_WORDS = ('nessun rischio', 'no risk', 'non prezzato', 'not priced', 'no polymarket coverage',
                 'market does not exist', 'mercato assente')


@pytest.mark.parametrize('source', ['events', 'markets'])
def test_unrelated_high_volume_candidates_never_fill_results(poly, monkeypatch, source):
    # R11 p.5 (Opus 5.5): 15 synthetic high-volume unrelated rows, query with no lexical match.
    if source == 'events':
        rows = [event(slug=f'zz-sport-{i}', title=f'Zzcup final match {i}', volume24hr=10_000 - i,
                      markets=[market(slug=f'zz-sport-m-{i}', question=f'Will Zzteam {i} win?')]) for i in range(15)]
    else:
        rows = [market(slug=f'zz-sport-{i}', question=f'Will Zzteam {i} win the Zzcup?', volume24hr=10_000 - i)
                for i in range(15)]
    install(poly, monkeypatch, events=rows if source == 'events' else [],
            singles=rows if source == 'markets' else [], source=source)
    out = poly.tool_get_polymarket_events('Norland election')
    assert out['results'] == [] and out['count'] == 0
    assert out['search_outcome'] == 'NO_MARKET_FOUND_WITH_THESE_QUERIES'
    assert 'nessun mercato trovato con queste query' in out['hint'].lower()
    assert 'NON concludere che il mercato non esiste' in out['hint']
    assert out['coverage_verified'] is False and out['relevant_count'] is None
    cov = out['coverage']
    assert cov['local_nonlexical_candidates'] == 15 and cov['lexical_nonmatches'] == 15
    assert len(cov['withheld_nonlexical_candidates']) == 10 and cov['withheld_nonlexical_omitted'] == 5
    assert cov['eligible_candidates'] == 0 and cov['returned'] == 0
    # Identities only: no probabilities of unrelated markets reach the desk.
    assert all('prices' not in r and 'markets' not in r for r in cov['withheld_nonlexical_candidates'])
    blob = json.dumps(out, ensure_ascii=False).lower()
    assert '"prices"' not in blob
    assert not [w for w in ABSENCE_WORDS if w in blob]


def test_lexical_candidate_still_returned_with_uncertain_ones_labelled(poly, monkeypatch):
    rows = [event(slug=f'zz-sport-{i}', title=f'Zzcup final match {i}', volume24hr=10_000 - i, markets=[])
            for i in range(3)] + [event(slug='zz-match', title='Norland election', volume24hr=1, markets=[])]
    install(poly, monkeypatch, events=rows, source='events')
    out = poly.tool_get_polymarket_events('Norland election')
    assert out['results'][0]['url'].endswith('/zz-match')
    assert out['results'][0]['search_match']['basis'] == 'lexical_candidate'
    assert all(r['search_match']['relevance_status'] == 'UNVERIFIED' for r in out['results'])
    assert 'search_outcome' not in out and 'withheld_nonlexical_candidates' not in out['coverage']


def test_withheld_candidates_with_provider_error_stay_unavailable_not_absent(poly, monkeypatch):
    rows = [event(slug='zz-sport', title='Zzcup final', volume24hr=50, markets=[])]
    def get(url, **kwargs):
        endpoint = url.rsplit('/', 1)[-1]
        if endpoint == 'events':
            return SimpleNamespace(status_code=200, json=lambda: rows)
        return SimpleNamespace(status_code=503, json=lambda: {})
    monkeypatch.setattr(poly._req, 'get', get)
    out = poly.tool_get_polymarket_events('Norland election')
    assert out['results'] == [] and out['status'] == 'unavailable' and out.get('fetch_warnings')
    assert out['coverage']['withheld_nonlexical_candidates'][0]['url'].endswith('/zz-sport')
    assert 'search_outcome' not in out  # provider gap: n.d., never "nothing found"


def sport_events(n):
    return [event(slug=f'zz-sport-{i}', title=f'Zzcup final match {i}', volume24hr=10_000 - i,
                  markets=[market(slug=f'zz-sport-m-{i}', question=f'Will Zzteam {i} win?')]) for i in range(n)]


@pytest.mark.parametrize('brazil_volume', [50_000, 5])
def test_translated_relevant_market_without_server_search_is_only_an_identity(poly, monkeypatch, brazil_volume):
    # Accepted limit (riserva 3, Opus 5.5): "elezioni Brasile" vs "Brazil election" has no
    # lexical match; without server search the relevant market keeps identity and activity
    # (if within the 10 declared) but never prices, and the tool asks for an English retry.
    rows = sport_events(15) + [event(slug='brazil-presidential-election', title='Brazil Presidential Election',
                                     volume24hr=brazil_volume,
                                     markets=[market(slug='lula-win', question='Will Lula win the 2026 Brazil election?')])]
    install(poly, monkeypatch, events=rows, source='events')
    out = poly.tool_get_polymarket_events('elezioni Brasile')
    assert out['results'] == [] and out['search_outcome'] == 'NO_MARKET_FOUND_WITH_THESE_QUERIES'
    cov = out['coverage']
    slugs = [w['url'].rsplit('/', 1)[-1] for w in cov['withheld_nonlexical_candidates']]
    assert ('brazil-presidential-election' in slugs) is (brazil_volume == 50_000)
    assert cov['withheld_nonlexical_omitted'] == 6
    assert 'termini inglesi' in out['hint'] and 'English terms' in cov['withheld_reason']


def test_withheld_beyond_the_declared_sample_makes_the_response_partial(poly, monkeypatch):
    # Riserva 2 (Opus 5.5): 16 nonlexical candidates, 10 declared, 6 unseen -> partial.
    install(poly, monkeypatch, events=sport_events(16), source='events')
    out = poly.tool_get_polymarket_events('Norland election')
    cov = out['coverage']
    assert cov['omitted_result_limit'] == 0 and cov['withheld_nonlexical_omitted'] == 6
    assert out['observed_response_completeness'] == 'partial' and out['completeness'] == 'partial'


def test_withheld_within_the_declared_sample_is_not_called_partial(poly, monkeypatch):
    install(poly, monkeypatch, events=sport_events(10), source='events')
    out = poly.tool_get_polymarket_events('Norland election')
    assert out['coverage']['withheld_nonlexical_omitted'] == 0
    assert out['observed_response_completeness'] == 'complete_observed' and out['completeness'] == 'unknown'


@pytest.mark.parametrize('max_results', [1, 3, 25])
def test_withheld_identity_sample_is_ten_whatever_the_result_cap(poly, monkeypatch, max_results):
    # Mutation M6 (reviewer): the declared identities do not follow max_results.
    install(poly, monkeypatch, events=sport_events(15), source='events')
    cov = poly.tool_get_polymarket_events('Norland election', max_results=max_results)['coverage']
    assert len(cov['withheld_nonlexical_candidates']) == 10 and cov['withheld_nonlexical_omitted'] == 5


def test_withheld_identity_keeps_volume_and_activity(poly, monkeypatch):
    # Mutation M5 (reviewer): volume_24h must stay in the withheld identity.
    rows = [event(slug='zz-a', title='Zzcup', volume24hr=77, markets=[]),
            event(slug='zz-b', title='Zzcup two', volume24hr=3, active=None, markets=[])]
    install(poly, monkeypatch, events=rows, source='events')
    withheld = poly.tool_get_polymarket_events('Norland election')['coverage']['withheld_nonlexical_candidates']
    assert [(w['url'].rsplit('/', 1)[-1], w['volume_24h'], w['activity_status']) for w in withheld] == [
        ('zz-a', 77, 'active'), ('zz-b', 3, 'unknown')]


@pytest.mark.parametrize('source', ['events', 'public-search', 'markets'])
def test_only_inactive_lexical_candidates_is_a_distinct_outcome(poly, monkeypatch, source):
    # Riserva 4 (Opus 5.5): a closed "Norland election" beside 3 unrelated rows is not
    # "nothing found": the query matched, but only inactive markets.
    if source == 'markets':
        rows = [market(slug=f'zz-sport-{i}', question=f'Will Zzteam {i} win?') for i in range(3)]
        rows.append(market(slug='norland-election', question='Norland election winner?', closed=True))
        install(poly, monkeypatch, singles=rows, source='markets')
    else:
        rows = [event(slug='norland-election', title='Norland election', closed=True, volume24hr=1,
                      markets=[market(slug='nm')])]
        if source == 'events':
            rows = sport_events(3) + rows
        install(poly, monkeypatch, events=rows, source=source)
    out = poly.tool_get_polymarket_events('Norland election')
    assert out['results'] == [] and out['search_outcome'] == 'ONLY_INACTIVE_CANDIDATES'
    assert out['coverage']['excluded_inactive_search_candidates'] == 1
    assert out['coverage']['excluded_candidates'][0]['slug'] == 'norland-election'
    assert 'NON concludere' in out['hint'] and 'inattivi' in out['hint']


def test_inactive_unrelated_rows_do_not_turn_nothing_found_into_only_inactive(poly, monkeypatch):
    rows = sport_events(3) + [event(slug='zz-old', title='Zzcup old final', closed=True, markets=[])]
    install(poly, monkeypatch, events=rows, source='events')
    out = poly.tool_get_polymarket_events('Norland election')
    assert out['search_outcome'] == 'NO_MARKET_FOUND_WITH_THESE_QUERIES'
    assert out['coverage']['excluded_inactive'] == 1
    assert 'excluded_inactive_search_candidates' not in out['coverage']
