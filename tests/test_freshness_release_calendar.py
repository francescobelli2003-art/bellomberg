"""Release evidence is synthetic; real helpers run only under offline isolation."""
from copy import deepcopy
from datetime import date

import pytest

from bellomberg.core import freshness as fr


AS_OF = date(2032, 10, 1)


def observation(**changes):
    item = {
        "series_id": "SYNTH_MONTHLY", "value": 123.0,
        "obs_date": "2032-08-01", "observation_period": "2032-08-01",
        "release_date": "2032-09-12", "retrieved_at": "2032-10-01T08:00:00+00:00",
        "next_expected_release": "2032-10-12",
        "release_calendar": {
            "status": "verified", "series_id": "SYNTH_MONTHLY",
            "source": "https://calendar.example.test/monthly",
            "as_of": "2032-10-01", "latest_release_date": "2032-09-12",
            "latest_observation_period": "2032-08-01",
        },
    }
    item.update(changes)
    return item


def check(item, as_of=AS_OF):
    return fr.check_release_freshness({"synthetic": item}, as_of=as_of)


def result(item, as_of=AS_OF):
    return check(item, as_of)["observations"]["synthetic"]


def test_monthly_first_day_is_current_when_last_published():
    out = result(observation())
    assert out["status"] == "CURRENT_PUBLISHED"
    assert out["age_days"] == 61
    assert out["observation_period"] == "2032-08-01"
    assert out["release_date"] == "2032-09-12"
    assert out["retrieved_at"] == "2032-10-01T08:00:00+00:00"
    assert out["next_expected_release"] == "2032-10-12"


@pytest.mark.parametrize("latest_period", ["2032-08-01", "2032-09-01"])
def test_only_documented_new_release_supersedes_including_revision(latest_period):
    item = observation(next_expected_release="2032-10-01")
    item["release_calendar"].update(latest_release_date="2032-09-30",
                                     latest_observation_period=latest_period)
    report = check(item)
    out = report["observations"]["synthetic"]
    assert out["status"] == "SUPERSEDED"
    assert out["latest_release_date"] == "2032-09-30"
    assert len(report["stale"]) == 1 and not report["unknown"]


def test_expected_date_passed_is_not_proof_publication_occurred():
    item = observation(next_expected_release="2032-09-30")
    report = check(item)
    assert report["observations"]["synthetic"]["status"] == "RELEASE_EXPECTED_UNCONFIRMED"
    assert not report["stale"] and len(report["unknown"]) == 1


def test_postponed_release_keeps_last_publication_current():
    item = observation(next_expected_release="2032-10-15")
    item["release_calendar"]["postponed_from"] = "2032-09-30"
    assert result(item)["status"] == "CURRENT_PUBLISHED"


def test_quarterly_gdp_old_period_is_not_superseded_by_age():
    item = observation(obs_date="2032-04-01", observation_period="2032-04-01",
                       release_date="2032-09-28", next_expected_release="2032-10-28")
    item["release_calendar"].update(latest_release_date="2032-09-28",
                                     latest_observation_period="2032-04-01")
    out = result(item)
    assert out["status"] == "CURRENT_PUBLISHED" and out["age_days"] == 183


def test_policy_rate_unchanged_and_zero_are_valid_values():
    first = observation(value=0)
    assert result(first)["status"] == "CURRENT_PUBLISHED"
    second = deepcopy(first)
    second.update(obs_date="2032-09-01", observation_period="2032-09-01",
                  release_date="2032-09-30")
    second["release_calendar"].update(latest_release_date="2032-09-30",
                                       latest_observation_period="2032-09-01")
    assert result(second)["status"] == "CURRENT_PUBLISHED"


def test_funding_without_observation_time_is_unknown_despite_retrieval():
    out = result({"value": 0.03, "retrieved_at": "2032-10-01T08:00:00+00:00"})
    assert out["status"] == "UNKNOWN" and out["age_days"] is None
    assert "osservazione" in out["reason"]


def test_without_calendar_age_is_only_a_measure():
    out = result(observation(release_calendar=None))
    assert out["status"] == "UNKNOWN" and out["age_days"] == 61
    assert "calendario" in out["reason"]


@pytest.mark.parametrize("mutation", [
    {"status": "unverified"}, {"source": ""}, {"as_of": None},
    {"as_of": "2032-10-02"}, {"as_of": "2032-09-29"},
    {"series_id": "OTHER_SERIES"}, {"latest_release_date": "2032-10-02"},
    {"latest_observation_period": "2032-11-01"},
])
def test_superseded_requires_verified_bound_calendar_available_as_of(mutation):
    item = observation()
    item["release_calendar"].update(latest_release_date="2032-09-30",
                                     latest_observation_period="2032-09-01")
    item["release_calendar"].update(mutation)
    assert result(item)["status"] == "UNKNOWN"


def test_older_calendar_can_prove_missing_release_but_not_currentness():
    item = observation()
    item["release_calendar"]["as_of"] = "2032-09-30"
    assert result(item)["status"] == "UNKNOWN"
    item["release_calendar"].update(latest_release_date="2032-09-30",
                                     latest_observation_period="2032-09-01")
    assert result(item)["status"] == "SUPERSEDED"


@pytest.mark.parametrize("changes", [
    {"observation_period": "2032-08-02"}, {"observation_period": "2032-11-01"},
    {"observation_period": "20320801"}, {"release_date": "2032-10-02"},
    {"release_date": "2032-07-01"}, {"retrieved_at": "2032-10-02T00:00:00Z"},
    {"retrieved_at": "2032-09-01T00:00:00Z"}, {"retrieved_at": None},
    {"retrieved_at": "2032-10-01T08:00:00"}, {"next_expected_release": "bad-date"},
    {"value": None}, {"value": True}, {"value": float("nan")}, {"value": float("inf")},
])
def test_invalid_future_or_inconsistent_metadata_never_becomes_current(changes):
    assert result(observation(**changes))["status"] == "UNKNOWN"


def test_pure_report_is_repeatable_and_input_is_unchanged(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("release classifier must not read/write legacy snapshot")
    monkeypatch.setattr(fr, "_load", forbidden)
    monkeypatch.setattr(fr, "_save", forbidden)
    item = observation()
    before = deepcopy(item)
    a, b = check(item), check(item)
    assert a == b and item == before
    assert a["schema"] == "release-freshness/1" and a["as_of"] == "2032-10-01"
    assert a["checked"] == a["fresh"] + len(a["stale"]) + len(a["unknown"])


@pytest.mark.parametrize("as_of", [None, "20321001", "bad-date"])
def test_frozen_cutoff_must_be_explicit_and_valid(as_of):
    with pytest.raises(ValueError, match="as_of"):
        check(observation(), as_of)


def test_projection_keeps_value_and_metadata_distinct_and_no_alias_mutation():
    item = observation()
    entry = {"value": 123.0, "date": "2032-08-01", "release_metadata": deepcopy(item)}
    projected = fr.project_macro_observation(entry)
    assert projected["value"] == 123.0 and projected["obs_date"] == entry["date"]
    assert result(projected)["status"] == "CURRENT_PUBLISHED"
    projected["release_calendar"]["status"] = "unverified"
    assert entry["release_metadata"]["release_calendar"]["status"] == "verified"
    missing = fr.project_macro_observation({"error": "provider unavailable", "date": "2032-08-01"})
    assert missing["value"] is None and result(missing)["status"] == "UNKNOWN"


def test_new_formatters_show_dates_and_verification_limit_without_fetch(monkeypatch):
    item = observation(release_calendar=None)
    report = check(item)
    monkeypatch.setattr(fr, "_load", lambda: pytest.fail("formatter accessed snapshot"))
    for text in (fr.format_for_memo(report), fr.format_for_capo(report)):
        assert "UNKNOWN" in text and "2032-08-01" in text and "2032-09-12" in text
        assert "61" in text and "2032-10-01" in text and "calendario" in text
        assert "SUPERSEDED: 0" in text
        assert "[src: freshness" in text


def test_attested_calendar_source_is_visible_in_both_formatters():
    report = check(observation())
    for text in (fr.format_for_memo(report), fr.format_for_capo(report)):
        assert "https://calendar.example.test/monthly" in text
        assert "CURRENT_PUBLISHED: 1" in text


def test_all_statuses_count_once_and_json_round_trip_preserves_cutoff():
    import json
    superseded = observation()
    superseded["release_calendar"].update(latest_release_date="2032-09-30",
                                           latest_observation_period="2032-09-01")
    report = fr.check_release_freshness({
        "current": observation(), "superseded": superseded,
        "expected": observation(next_expected_release="2032-09-30"),
        "unknown": observation(release_calendar=None),
    }, as_of="2032-10-01")
    assert (report["checked"], report["fresh"], len(report["stale"]), len(report["unknown"])) == (4, 1, 1, 2)
    frozen = json.loads(json.dumps(report))
    assert frozen == report and fr.format_for_capo(frozen) == fr.format_for_capo(report)


@pytest.mark.parametrize("schema", [None, "release-freshness/999", ""])
def test_invalid_report_marker_never_silently_uses_legacy_formatter(schema):
    report = {"schema": schema, "stale": [], "unknown": [], "fresh": 1, "checked": 1}
    for formatter in (fr.format_for_memo, fr.format_for_capo):
        with pytest.raises(ValueError, match="schema freshness"):
            formatter(report)


@pytest.mark.parametrize("calendar", [False, "verified", [], {"status": True}])
def test_malformed_calendar_is_unknown_and_never_raises(calendar):
    assert result(observation(release_calendar=calendar))["status"] == "UNKNOWN"


def test_next_expected_release_without_calendar_is_only_unconfirmed():
    out = result(observation(release_calendar=None, next_expected_release="2032-09-30"))
    assert out["status"] == "RELEASE_EXPECTED_UNCONFIRMED"


def test_same_release_with_later_published_period_supersedes_older_row():
    item = observation()
    item["release_calendar"]["latest_observation_period"] = "2032-09-01"
    assert result(item)["status"] == "SUPERSEDED"


def test_legacy_formatting_and_threshold_check_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "SNAP_PATH", str(tmp_path / "legacy.json"))
    legacy = fr.check_and_update({"us_cpi": {"value": 123, "obs_date": "2032-08-01"}}, today=AS_OF)
    assert "schema" not in legacy and len(legacy["stale"]) == 1
    assert "61 giorni fa (limite 45 per questa serie)" in legacy["stale"][0]
    assert "1 serie esterne controllate: 0 fresche, 1 STALE" in fr.format_for_memo(legacy)
    assert "FRESHNESS CHECK: DATI STALE O FRESCHEZZA N.D." in fr.format_for_capo(legacy)


def test_real_fred_fetch_has_honest_metadata_and_cache_preserves_retrieval(monkeypatch):
    from bellomberg.agents import agent_tools as at
    from bellomberg.core import config
    monkeypatch.setattr(config, "FRED_API_KEY", "synthetic-offline")
    monkeypatch.setattr(at, "_FRED_CACHE", {})
    calls = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"realtime_start": "2032-09-30", "realtime_end": "2032-09-30",
                    "observations": [{"date": "2032-08-01", "value": "123",
                                      "realtime_start": "2032-09-30", "realtime_end": "2032-09-30"}]}
    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()
    monkeypatch.setattr(at._req, "get", fetch)
    first = at._fred_fetch_series("SYNTH_MONTHLY")
    second = at._fred_fetch_series("SYNTH_MONTHLY")
    metadata = first["release_metadata"]
    assert metadata["observation_period"] == "2032-08-01"
    assert metadata["retrieved_at"] == first["fetched_at"] == second["release_metadata"]["retrieved_at"]
    assert metadata["release_date"] is None and metadata["next_expected_release"] is None
    assert metadata["release_calendar"]["status"] == "UNKNOWN"
    assert len(calls) == 1 and "series/observations" in calls[0][0][0]
    assert first["observations"] == [{"date": "2032-08-01", "value": 123.0}]


def test_real_macro_indicator_and_dashboard_preserve_release_evidence(monkeypatch):
    from bellomberg.agents import agent_tools as at
    item = observation()
    payload = {"observations": [{"date": "2031-08-01", "value": 100},
                                {"date": "2032-08-01", "value": 123}],
               "rejected_observations": [], "release_metadata": item}
    calls = []
    def fetch(series, last_n=24):
        calls.append(series)
        return deepcopy(payload)
    monkeypatch.setattr(at, "_fred_fetch_series", fetch)
    monkeypatch.setattr(at, "_NATIVE_INDICATORS", {})
    individual = at.tool_get_macro_indicator("us_cpi_yoy")
    dashboard = at.tool_get_macro_dashboard()
    entry = dashboard["indicators"]["us_cpi_yoy"]
    assert individual["release_metadata"] == entry["release_metadata"] == item
    assert entry["yoy_pct"] == 23 and entry["value"] == 123
    assert result(fr.project_macro_observation(entry))["status"] == "CURRENT_PUBLISHED"
    assert len(calls) == 1 + len(at._FRED_INDICATORS)
