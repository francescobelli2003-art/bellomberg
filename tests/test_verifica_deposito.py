"""Real local Git pushes and synthetic release certificates; no network."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from tools.release import verifica_deposito as guard
from tools.release import verifica_pubblico


def git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=check)


def manifest(tree):
    files, sha = guard._files_artifact(tree)
    return {
        "versione": 1,
        "artefatto": {"file": len(files), "file_prima_suite": len(files),
                      "sha256": sha, "sha256_prima_suite": sha,
                      "immutato_dalla_suite": True},
        "input": {"fonti_ko": {}, "conteggi": {"payload_canali_guasti": 0},
                  "sha256_corpus_privato": "a" * 64, "sha256_payload": "b" * 64},
        "verifica": {
            "stato": "OK", "rigoroso": True, "cancello_exit": 0, "suite_exit": 0,
            "suite_non_eseguita": False, "sorgente_sporca_file": 0,
            "controlli_solo": [], "non_eseguiti": [],
            "controlli": [{"nome": name, "hit": 0, "errore": False, "osservazione": False}
                          for name in sorted(guard.CONTROLLI_RICHIESTI)],
        },
    }


@pytest.fixture
def release(tmp_path):
    repo, remote, artifact = (tmp_path / p for p in ("public", "remote.git", "artifact"))
    for p in (repo, remote, artifact):
        p.mkdir()
    git(remote, "init", "--bare", "-q", "-b", "main")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "release-test")
    git(repo, "config", "user.email", "123+release@users.noreply.github.com")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "remote", "add", "origin", str(remote))
    for directory in (repo, artifact):
        (directory / "readme.txt").write_bytes(b"Synthetic release\n")
    git(repo, "add", "readme.txt")
    git(repo, "commit", "-qm", "synthetic release")
    return repo, remote, artifact


def test_required_checks_stay_in_sync_with_real_gate():
    assert guard.CONTROLLI_RICHIESTI == set(verifica_pubblico.CONTROLLI)


def test_installed_guard_blocks_push_before_certificate_exists(release):
    repo, remote, _ = release
    guard.prepara_guardia(repo)
    result = git(repo, "push", "origin", "main", check=False)
    assert result.returncode != 0 and "Certificato locale assente" in result.stderr
    assert not git(remote, "show-ref", check=False).stdout


def test_force_push_cannot_overwrite_unreviewed_remote_history(release, tmp_path):
    repo, remote, artifact = release
    guard.certifica_deposito(repo, manifest(artifact), artifact)
    git(repo, "push", "origin", "main")
    other = tmp_path / "other"
    git(tmp_path, "clone", str(remote), str(other))
    git(other, "config", "user.name", "synthetic-other")
    git(other, "config", "user.email", "other@example.com")
    (other / "other.txt").write_text("Other work", encoding="utf-8")
    git(other, "add", "other.txt")
    git(other, "commit", "-qm", "other commit")
    git(other, "push", "origin", "main")
    result = git(repo, "push", "--force", "origin", "main", check=False)
    assert result.returncode != 0 and "Storia remota estranea" in result.stderr
    assert git(remote, "rev-parse", "main").stdout == git(other, "rev-parse", "HEAD").stdout


@pytest.mark.parametrize("change", [
    lambda d: d["verifica"].update(stato="INCOMPLETO"),
    lambda d: d["verifica"].update(suite_non_eseguita=True),
    lambda d: d["verifica"].update(suite_exit=1),
    lambda d: d["verifica"].update(non_eseguiti=["env"]),
    lambda d: d["verifica"]["controlli"].pop(),
    lambda d: d["verifica"]["controlli"].append(d["verifica"]["controlli"][0]),
    lambda d: d["verifica"]["controlli"][0].update(errore=True),
    lambda d: d["verifica"]["controlli"][0].update(osservazione=True),
    lambda d: d["verifica"]["controlli"][0].update(hit=1),
    lambda d: d["artefatto"].update(immutato_dalla_suite=False),
    lambda d: d["input"].update(sha256_corpus_privato=None),
])
def test_manifest_cannot_certify_missing_or_failed_proofs(release, change):
    doc = manifest(release[2])
    change(doc)
    with pytest.raises(ValueError, match="Manifest"):
        guard.valida_manifest(doc)


def test_certified_real_push_succeeds_and_metadata_stays_private(release):
    repo, remote, artifact = release
    path = guard.certifica_deposito(repo, manifest(artifact), artifact)
    assert path.is_relative_to(repo / ".git")
    result = git(repo, "push", "origin", "main", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(remote, "rev-parse", "main").stdout == git(repo, "rev-parse", "HEAD").stdout
    assert git(remote, "ls-tree", "-r", "--name-only", "main").stdout.splitlines() == ["readme.txt"]


@pytest.mark.parametrize("mutation", ["commit", "intermediate", "dirty", "certificate", "tag"])
def test_actual_push_rejects_uncertified_changes(release, mutation):
    repo, remote, artifact = release
    path = guard.certifica_deposito(repo, manifest(artifact), artifact)
    args = ["push", "origin", "main"]
    if mutation in ("commit", "intermediate"):
        (repo / "private.txt").write_text("Synthetic private note", encoding="utf-8")
        git(repo, "add", "private.txt")
        git(repo, "commit", "-qm", "uncertified change")
        if mutation == "intermediate":
            git(repo, "revert", "--no-edit", "HEAD")
    elif mutation == "dirty":
        (repo / "readme.txt").write_text("unreviewed", encoding="utf-8")
    elif mutation == "certificate":
        path.write_text("{}", encoding="utf-8")
    else:
        git(repo, "tag", "unreviewed")
        args = ["push", "origin", "refs/tags/unreviewed"]
    result = git(repo, *args, check=False)
    assert result.returncode != 0
    assert "STOP:" in result.stderr, result.stdout + result.stderr
    assert not git(remote, "show-ref", check=False).stdout


def test_push_rejects_other_destination_even_with_valid_commit(release, tmp_path):
    repo, _, artifact = release
    guard.certifica_deposito(repo, manifest(artifact), artifact)
    other = tmp_path / "other.git"
    other.mkdir()
    git(other, "init", "--bare", "-q")
    result = git(repo, "push", str(other), "main", check=False)
    assert result.returncode != 0 and "STOP:" in result.stderr
    assert not git(other, "show-ref", check=False).stdout


def test_artifact_or_git_filter_change_is_rejected(release):
    repo, _, artifact = release
    doc = manifest(artifact)
    (repo / "readme.txt").write_bytes(b"Different content\n")
    git(repo, "add", "readme.txt")
    git(repo, "commit", "-qm", "content differs from artifact")
    with pytest.raises(ValueError, match="file controllati"):
        guard.certifica_deposito(repo, doc, artifact)
    (artifact / "readme.txt").write_bytes(b"Different content\n")
    with pytest.raises(ValueError, match="modificato dopo"):
        guard.certifica_deposito(repo, doc, artifact)


def test_only_explicit_text_eol_normalization_is_allowed(release):
    repo, _, artifact = release
    (artifact / "readme.txt").write_bytes(b"Synthetic release\r\n")
    path = guard.certifica_deposito(repo, manifest(artifact), artifact)
    assert json.loads(path.read_text())["eol_normalized_files"] == 1
    assert not guard._stessi_byte_o_eol(b"X\0\r\n", b"X\0\n")
