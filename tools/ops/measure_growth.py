"""Read-only, manual GitHub growth measurement; one immutable UTC-day snapshot.

Usage: python tools/ops/measure_growth.py --repo owner/name --output EXTERNAL_DIR
Exit 0: all four sources measured; exit 1: unavailable source or refused output.
No scheduler, account enumeration, stargazers, credentials, or product data.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")


def _output_directory(output):
    directory = Path(output).resolve()
    root = PROJECT_ROOT.resolve()
    protected = [root] + [(root / name).resolve() for name in ("data", "data_pre_junction", "report", "research_notes")]
    if any(directory == item or item in directory.parents for item in protected):
        raise ValueError("output must be outside the checkout and product runtime directories")
    if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
        raise ValueError("output must be outside every checkout")
    if directory.exists() and not directory.is_dir():
        raise ValueError("output must be a directory")
    return directory


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("invalid aggregate count")
    return value


def _project(data, kind):
    if kind == "repository":
        return {"stars": _count(data["stargazers_count"]), "forks": _count(data["forks_count"])}
    if kind == "referrers":
        if not isinstance(data, list):
            raise ValueError("referrers must be a list")
        items = []
        for item in data:
            referrer = item["referrer"]
            # Aggregate referrer labels only: no full URLs or email addresses.
            if not isinstance(referrer, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .-]{0,252}", referrer):
                raise ValueError("invalid referrer label")
            items.append({"referrer": referrer, "count": _count(item["count"]), "uniques": _count(item["uniques"])})
        return {"window_days": 14, "items": items}
    rows = data[kind]
    if not isinstance(rows, list):
        raise ValueError("traffic must be a list")
    days = []
    for row in rows:
        timestamp = row["timestamp"]
        if not isinstance(timestamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z", timestamp):
            raise ValueError("invalid UTC traffic date")
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        days.append({"timestamp": timestamp, "count": _count(row["count"]), "uniques": _count(row["uniques"])})
    return {"window_days": 14, "count": _count(data["count"]), "uniques": _count(data["uniques"]), "days": days}


def _measure(runner, endpoint, kind):
    try:
        result = runner(["gh", "api", "--method", "GET", endpoint], capture_output=True,
                        text=True, encoding="utf-8", timeout=30, shell=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return {"status": "unavailable", "cause": "gh_unavailable"}
    except subprocess.TimeoutExpired:
        return {"status": "unavailable", "cause": "gh_timeout"}
    except UnicodeDecodeError:
        return {"status": "unavailable", "cause": "gh_invalid_encoding"}
    except OSError as error:
        return {"status": "unavailable", "cause": "gh_os_error", "error_type": type(error).__name__}
    if result.returncode != 0:
        # Never persist arbitrary stderr: it may contain credentials/account data.
        status = re.search(r"\bHTTP\s+(\d{3})\b", result.stderr or "")
        if status:
            return {"status": "unavailable", "cause": "http_error", "http_status": int(status.group(1))}
        return {"status": "unavailable", "cause": "gh_failed", "exit_code": result.returncode}
    try:
        raw = json.loads(result.stdout)
    except (TypeError, ValueError):
        return {"status": "unavailable", "cause": "invalid_json"}
    try:
        return {"status": "ok", **_project(raw, kind)}
    except (KeyError, TypeError, ValueError, OverflowError):
        return {"status": "unavailable", "cause": "invalid_aggregate_schema"}


def _publish(path, snapshot):
    descriptor, temporary = tempfile.mkstemp(prefix=".growth-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link atomically publishes the complete file and refuses an
        # existing destination, including a concurrent run. Never os.replace().
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def collect(repo, output, *, runner=None, now=None):
    """Return (saved path, snapshot). Errors remain unavailable, never fake zeros."""
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise ValueError("repo must be an explicit owner/name")
    directory = _output_directory(output)
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("observation time must have an explicit timezone")
    observed = observed.astimezone(timezone.utc)
    filename = observed.strftime("%Y-%m-%d") + "--" + repo.replace("/", "--").lower() + ".json"
    path = directory / filename
    if path.exists():
        raise FileExistsError("UTC-day snapshot already exists; it will not be overwritten")
    base = "repos/" + repo
    endpoints = {"repository": base, "views": base + "/traffic/views",
                 "clones": base + "/traffic/clones", "referrers": base + "/traffic/popular/referrers"}
    snapshot = {"schema_version": 1, "repo": repo,
                "observed_at_utc": observed.isoformat().replace("+00:00", "Z"),
                "utc_day": observed.date().isoformat()}
    for kind, endpoint in endpoints.items():
        snapshot[kind] = _measure(runner or subprocess.run, endpoint, kind)
    snapshot["status"] = "ok" if all(snapshot[kind]["status"] == "ok" for kind in endpoints) else "unavailable"
    if _output_directory(output) != directory:
        raise ValueError("output path changed during measurement")
    directory.mkdir(parents=True, exist_ok=True)
    _publish(path, snapshot)
    return path, snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Explicit owner/name (no implicit current repository)")
    parser.add_argument("--output", required=True, type=Path, help="Directory outside every checkout and product runtime")
    args = parser.parse_args(argv)
    try:
        path, snapshot = collect(args.repo, args.output)
    except (OSError, ValueError) as error:
        print("unavailable: snapshot not written (" + type(error).__name__ + "): " + str(error))
        return 1
    print(snapshot["status"] + ": " + str(path))
    for key in ("repository", "views", "clones", "referrers"):
        print(key + ": " + json.dumps(snapshot[key], ensure_ascii=False))
    return 0 if snapshot["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
