"""Offline growth snapshots: actual aggregate projection, no GitHub writes."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.ops import measure_growth as growth


NOW = datetime(2026, 9, 12, 23, 45, tzinfo=timezone.utc)


def fake_gh(calls, *, failed=None, malformed=None):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        endpoint = command[-1]
        assert command[:4] == ["gh", "api", "--method", "GET"]
        assert kwargs["shell"] is False
        if failed and endpoint.endswith(failed):
            return subprocess.CompletedProcess(command, 1, "", "gh: forbidden (HTTP 403) hidden-secret@example.invalid ghp_FAKE")
        if malformed and endpoint.endswith(malformed):
            return subprocess.CompletedProcess(command, 0, "{broken", "")
        if endpoint.endswith("referrers"):
            data = [{"referrer": "news.ycombinator.com", "count": 11, "uniques": 7, "email": "hidden@example.invalid"}]
        elif endpoint.endswith("views") or endpoint.endswith("clones"):
            key = endpoint.rsplit("/", 1)[-1]
            data = {"count": 19, "uniques": 8, key: [{"timestamp": "2026-09-12T00:00:00Z", "count": 19, "uniques": 8}], "token": "ghp_FAKE"}
        else:
            data = {"stargazers_count": 3, "forks_count": 0, "owner": {"login": "hidden-owner"}, "token": "ghp_FAKE"}
        return subprocess.CompletedProcess(command, 0, json.dumps(data), "")
    return run


def test_snapshot_preserves_aggregates_and_only_calls_four_read_endpoints(tmp_path):
    calls = []
    path, data = growth.collect("sample-owner/sample-repo", tmp_path, runner=fake_gh(calls), now=NOW)
    assert path.name.startswith("2026-09-12")
    assert json.loads(path.read_text(encoding="utf-8")) == data
    assert data["observed_at_utc"] == "2026-09-12T23:45:00Z"
    assert data["status"] == "ok"
    assert data["repository"] == {"status": "ok", "stars": 3, "forks": 0}
    assert data["views"]["count"] == 19 and data["clones"]["uniques"] == 8
    assert data["referrers"]["items"] == [{"referrer": "news.ycombinator.com", "count": 11, "uniques": 7}]
    assert len(calls) == 4
    assert all("stargazers" not in command[-1] for command, _ in calls)
    saved = path.read_text(encoding="utf-8")
    assert all(secret not in saved for secret in ("ghp_FAKE", "hidden-owner", "@example", '"token"', '"owner"'))


def test_partial_403_is_unavailable_with_cause_never_zero(tmp_path):
    path, data = growth.collect("sample/repo", tmp_path, runner=fake_gh([], failed="clones"), now=NOW)
    assert data["status"] == "unavailable"
    assert data["repository"]["stars"] == 3
    assert data["clones"] == {"status": "unavailable", "cause": "http_error", "http_status": 403}
    assert "ghp_FAKE" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("error,cause", [(FileNotFoundError(), "gh_unavailable"), (subprocess.TimeoutExpired("gh", 30), "gh_timeout")])
def test_transport_errors_are_recorded(tmp_path, error, cause):
    def fail(*args, **kwargs):
        raise error
    _, data = growth.collect("sample/repo", tmp_path, runner=fail, now=NOW)
    assert data["status"] == "unavailable"
    assert all(data[key] == {"status": "unavailable", "cause": cause} for key in ("repository", "views", "clones", "referrers"))


def test_invalid_json_is_visible(tmp_path):
    _, data = growth.collect("sample/repo", tmp_path, runner=fake_gh([], malformed="views"), now=NOW)
    assert data["views"] == {"status": "unavailable", "cause": "invalid_json"}
    assert data["status"] == "unavailable"


def test_same_day_snapshot_cannot_be_overwritten_and_does_not_refetch(tmp_path):
    calls = []
    path, _ = growth.collect("sample/repo", tmp_path, runner=fake_gh(calls), now=NOW)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        growth.collect("sample/repo", tmp_path, runner=fake_gh(calls), now=NOW)
    assert path.read_bytes() == before and len(calls) == 4


def test_checkout_path_is_rejected_before_fetch_or_creation(monkeypatch, tmp_path):
    project = tmp_path / "checkout"
    project.mkdir()
    monkeypatch.setattr(growth, "PROJECT_ROOT", project)
    for output in (project, project / "new" / "metrics", project / ".." / "checkout" / "metrics"):
        with pytest.raises(ValueError, match="checkout"):
            growth.collect("sample/repo", output, runner=lambda *a, **k: pytest.fail("must reject before GET"), now=NOW)
    assert list(project.iterdir()) == []


@pytest.mark.parametrize("repo", ["sample/../repo", "--help", "sample", "/sample/repo", "sample/repo?token=secret"])
def test_repo_must_be_an_explicit_owner_name(tmp_path, repo):
    with pytest.raises(ValueError):
        growth.collect(repo, tmp_path, runner=lambda *a, **k: pytest.fail("invalid repo reached gh"), now=NOW)


def test_atomic_publish_failure_leaves_no_partial_snapshot(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OSError("synthetic link failure")
    monkeypatch.setattr(growth.os, "link", fail)
    with pytest.raises(OSError):
        growth.collect("sample/repo", tmp_path, runner=fake_gh([]), now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_main_returns_one_for_missing_gh_and_writes_declared_snapshot(monkeypatch, tmp_path, capsys):
    def fail(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(growth.subprocess, "run", fail)
    assert growth.main(["--repo", "sample/repo", "--output", str(tmp_path)]) == 1
    assert "unavailable" in capsys.readouterr().out
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["repository"]["cause"] == "gh_unavailable"


def test_503_is_preserved_without_echoing_raw_error(tmp_path):
    def fail(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "gh: backend unavailable (HTTP 503) ghp_FAKE")
    path, data = growth.collect("sample/repo", tmp_path, runner=fail, now=NOW)
    assert data["repository"] == {"status": "unavailable", "cause": "http_error", "http_status": 503}
    assert "ghp_FAKE" not in path.read_text()


@pytest.mark.parametrize("stars", [True, -1, None, "3"])
def test_bad_counts_are_unavailable_without_numeric_substitution(tmp_path, stars):
    def fake(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps({"stargazers_count": stars, "forks_count": 0}), "")
    _, data = growth.collect("sample/repo", tmp_path, runner=fake, now=NOW)
    assert data["repository"] == {"status": "unavailable", "cause": "invalid_aggregate_schema"}


def test_concurrent_publisher_cannot_replace_winning_snapshot(monkeypatch, tmp_path):
    link = growth.os.link
    def race(source, destination):
        Path(destination).write_bytes(b"winner")
        link(source, destination)
    monkeypatch.setattr(growth.os, "link", race)
    with pytest.raises(FileExistsError):
        growth.collect("sample/repo", tmp_path, runner=fake_gh([]), now=NOW)
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].read_bytes() == b"winner"


def test_timezone_is_normalized_to_utc_day(tmp_path):
    from datetime import timedelta
    local = datetime(2026, 9, 13, 0, 15, tzinfo=timezone(timedelta(hours=2)))
    path, data = growth.collect("sample/repo", tmp_path, runner=fake_gh([]), now=local)
    assert data["utc_day"] == "2026-09-12"
    assert path.name.startswith("2026-09-12")


def test_another_git_checkout_is_also_protected(tmp_path):
    checkout = tmp_path / "another"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(ValueError, match="checkout"):
        growth.collect("sample/repo", checkout / "metrics", runner=lambda *a, **k: pytest.fail("must not fetch"), now=NOW)


def test_invalid_utf8_from_gh_is_declared(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
    _, data = growth.collect("sample/repo", tmp_path, runner=fail, now=NOW)
    assert data["repository"] == {"status": "unavailable", "cause": "gh_invalid_encoding"}
