"""Real local Git pushes and synthetic release certificates; no network."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from tools.release import verifica_deposito as guard
from tools.release import verifica_pubblico

CONTROLLI_V1_ATTESI = (
    "gitleaks", "vietate", "env", "esclusi", "lotti", "dimensione",
    "valori_db", "lista_privata", "ticker_soli", "payload", "valori_estesi", "vietate_forme",
)


def git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=check)


def manifest(tree, versione=2):
    """A complete green manifest. Version 2 (13/09) carries the CI steps the export ran on the
    verified copy; version 1 is the shape of the certificates emitted before that."""
    files, sha = guard._files_artifact(tree)
    doc = {
        "versione": versione,
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
                          for name in sorted(CONTROLLI_V1_ATTESI if versione == 1
                                             else (*CONTROLLI_V1_ATTESI, "testo_pm"))],
        },
    }
    if versione >= 2:
        doc["verifica"]["suite_passi"] = [
            {"nome": nome, "esito": "OK", "exit": 0, "secondi": 1.5, "motivo": ""}
            for nome in guard.PASSI_RICHIESTI]
    return doc


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


def test_legacy_checks_are_accepted_only_for_a_version_one_rewrite(tmp_path):
    (tmp_path / "synthetic.txt").write_bytes(b"Synthetic\n")
    legacy = manifest(tmp_path, versione=1)
    guard.valida_manifest(legacy, guard.VERSIONI_RISCRITTURA)
    with pytest.raises(ValueError, match="Manifest"):
        guard.valida_manifest(legacy)
    legacy["versione"] = 2
    legacy["verifica"]["suite_passi"] = manifest(tmp_path)["verifica"]["suite_passi"]
    with pytest.raises(ValueError, match="Manifest"):
        guard.valida_manifest(legacy, guard.VERSIONI_RISCRITTURA)


def test_the_guard_requires_thirteen_checks_including_testo_pm():
    """13/09: the thirteenth check (free text of the owner's private database) is required on
    every deposit like the other twelve. Counted here so that dropping it is a visible change."""
    assert len(guard.CONTROLLI_RICHIESTI) == 13
    assert "testo_pm" in guard.CONTROLLI_RICHIESTI


def test_required_steps_are_the_steps_the_export_runs():
    """verify.py is stdlib-only and copied into the public clone: it cannot import the export.
    Its literal copy of the step names must match the export's own list, in the CI order."""
    from tools.release import export_pubblico
    assert guard.PASSI_RICHIESTI == tuple(p.nome for p in export_pubblico.PASSI_SUITE)


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
    # 13/09: the CI steps on the verified copy
    lambda d: d["verifica"].pop("suite_passi"),
    lambda d: d["verifica"].update(suite_passi=None),
    lambda d: d["verifica"]["suite_passi"].pop(),
    lambda d: d["verifica"]["suite_passi"].reverse(),
    lambda d: d["verifica"]["suite_passi"].append(dict(d["verifica"]["suite_passi"][0])),
    lambda d: d["verifica"]["suite_passi"][3].update(esito="KO", exit=1, motivo="exit 1"),
    lambda d: d["verifica"]["suite_passi"][6].update(esito="NON ESEGUITO", exit=None,
                                                     motivo="dipende da tsc mandato, KO"),
    lambda d: d["verifica"]["suite_passi"][0].update(exit=None),
    lambda d: d["verifica"]["suite_passi"][0].update(exit="0"),
    lambda d: d["verifica"]["suite_passi"][2].update(exit=2),
    lambda d: d["verifica"]["suite_passi"][1].pop("esito"),
    lambda d: d.update(versione=1),
    lambda d: d.update(versione=3),
    lambda d: d.update(versione=True),
])
def test_manifest_cannot_certify_missing_or_failed_proofs(release, change):
    doc = manifest(release[2])
    guard.valida_manifest(doc)            # the untouched manifest is valid: the change is the cause
    change(doc)
    with pytest.raises(ValueError, match="Manifest"):
        guard.valida_manifest(doc)


def test_a_version_1_manifest_is_valid_only_where_the_caller_admits_it(release):
    doc = manifest(release[2], versione=1)
    assert "suite_passi" not in doc["verifica"]
    with pytest.raises(ValueError, match="Manifest"):
        guard.valida_manifest(doc)
    guard.valida_manifest(doc, versioni=(1, 2))
    with pytest.raises(ValueError, match="Manifest"):
        guard.certifica_deposito(release[0], doc, release[2])


def test_a_version_1_certificate_does_not_authorize_an_ordinary_push(release):
    """Before 13/09 the certificate proved only pytest. Accepting it on an ordinary push would
    reopen the gap the steps close: the guard stops, whatever version the verifier file has."""
    repo, remote, artifact = release
    guard.certifica_deposito(repo, manifest(artifact, versione=1), artifact, versioni=(1,))
    result = git(repo, "push", "origin", "main", check=False)
    assert result.returncode != 0 and "Manifest incompleto" in result.stderr, result.stderr
    assert not git(remote, "show-ref", check=False).stdout


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


def _rewrite_messages(repo):
    """Real commit objects, preserving every byte except messages and parent OIDs."""
    mapping = {}
    old = git(repo, "rev-parse", "HEAD").stdout.strip()
    for oid in git(repo, "rev-list", "--reverse", "HEAD").stdout.split():
        raw = subprocess.run(["git", "cat-file", "commit", oid], cwd=repo,
                             capture_output=True, check=True).stdout
        headers, message = raw.split(b"\n\n", 1)
        lines = []
        for line in headers.split(b"\n"):
            if line.startswith(b"parent "):
                line = b"parent " + mapping[line[7:].decode()].encode()
            lines.append(line)
        rewritten = b"\n".join(lines) + b"\n\n" + message.replace(b" (Codex GPT-6)", b"")
        result = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"],
                                cwd=repo, input=rewritten, capture_output=True, check=True)
        mapping[oid] = result.stdout.decode().strip()
    git(repo, "update-ref", "refs/heads/main", mapping[old], old)
    return old, mapping[old]


def _published_with_label(release):
    repo, _, artifact = release
    git(repo, "commit", "--amend", "-qm", "Release (Codex GPT-6)")
    path = guard.certifica_deposito(repo, manifest(artifact), artifact)
    previous = json.loads(path.read_text(encoding="utf-8"))
    git(repo, "push", "origin", "main")
    return previous


def test_authorized_message_rewrite_push_preserves_tree_and_cleans_history(release):
    repo, remote, artifact = release
    previous = _published_with_label(release)
    old, new = _rewrite_messages(repo)
    assert old != new
    path = guard.certifica_riscrittura_messaggi(repo, previous, artifact)
    proof = json.loads(path.read_text())["riscrittura_messaggi"]
    assert proof["precedente_head"] == old
    result = git(repo, "push", f"--force-with-lease=refs/heads/main:{old}", "origin", "main", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(remote, "rev-parse", "main").stdout.strip() == new
    assert "Codex" not in git(remote, "log", "--format=%B", "main").stdout
    assert git(repo, "rev-parse", f"{old}^{{tree}}").stdout == git(repo, "rev-parse", f"{new}^{{tree}}").stdout
    # Consumed permission: normal recertification removes the exceptional path.
    path = guard.certifica_deposito(repo, manifest(artifact), artifact)
    assert "riscrittura_messaggi" not in json.loads(path.read_text())


def test_authorized_message_rewrite_still_works_on_a_version_1_certificate(release):
    """The certificates already emitted carry a version 1 manifest. A metadata-only rewrite of
    that published history publishes no new content, so it stays allowed: certifica_riscrittura
    passes the versions to certifica_deposito, and verifica_push admits version 1 only on the
    rewrite path (the ordinary push is refused, see the test above)."""
    repo, remote, artifact = release
    git(repo, "commit", "--amend", "-qm", "Release (Codex GPT-6)")
    path = guard.certifica_deposito(repo, manifest(artifact, versione=1), artifact, versioni=(1,))
    previous = json.loads(path.read_text(encoding="utf-8"))
    assert previous["manifest"]["versione"] == 1
    # Published before version 2 existed: the guard of that time accepted it.
    git(repo, "push", "--no-verify", "origin", "main")
    old, new = _rewrite_messages(repo)
    path = guard.certifica_riscrittura_messaggi(repo, previous, artifact)
    assert json.loads(path.read_text())["manifest"]["versione"] == 1
    result = git(repo, "push", f"--force-with-lease=refs/heads/main:{old}", "origin", "main", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(remote, "rev-parse", "main").stdout.strip() == new
    # The same version 1 certificate without the rewrite permission is refused again.
    cert = json.loads(path.read_text())
    del cert["riscrittura_messaggi"]
    path.write_text(json.dumps(cert), encoding="utf-8")
    with pytest.raises(ValueError, match="Manifest"):
        guard.verifica_push(repo, f"refs/heads/main {new} refs/heads/main {new}", previous["remote_url"])


def test_rewritten_messages_still_rejected_without_explicit_certificate(release):
    repo, _, artifact = release
    _published_with_label(release)
    old, _ = _rewrite_messages(repo)
    guard.certifica_deposito(repo, manifest(artifact), artifact)
    result = git(repo, "push", f"--force-with-lease=refs/heads/main:{old}", "origin", "main", check=False)
    assert result.returncode != 0 and "Storia remota estranea" in result.stderr


@pytest.mark.parametrize("mutation", ["tree", "author", "extra_commit", "intermediate_tree", "wrong_remote", "message"])
def test_message_rewrite_cannot_certify_other_changes(release, mutation):
    repo, _, artifact = release
    previous = _published_with_label(release)
    _rewrite_messages(repo)
    if mutation in ("tree", "intermediate_tree"):
        (repo / "readme.txt").write_text("Changed content", encoding="utf-8")
        git(repo, "add", "readme.txt")
        git(repo, "commit", "--amend", "-qm", "Changed content")
        if mutation == "intermediate_tree":
            (repo / "readme.txt").write_bytes((artifact / "readme.txt").read_bytes())
            git(repo, "add", "readme.txt")
            git(repo, "commit", "-qm", "restore final tree")
    elif mutation == "author":
        git(repo, "commit", "--amend", "--author=Other <other@example.com>", "--no-edit")
    elif mutation == "extra_commit":
        git(repo, "commit", "--allow-empty", "-qm", "Extra")
    elif mutation == "message":
        git(repo, "commit", "--amend", "-qm", "Unapproved extra message")
    else:
        git(repo, "remote", "set-url", "origin", "https://example.invalid/wrong.git")
    with pytest.raises(ValueError):
        guard.certifica_riscrittura_messaggi(repo, previous, artifact)


def test_message_rewrite_permission_does_not_accept_other_remote_head(release):
    repo, _, artifact = release
    previous = _published_with_label(release)
    old, new = _rewrite_messages(repo)
    guard.certifica_riscrittura_messaggi(repo, previous, artifact)
    with pytest.raises(ValueError, match="Storia remota estranea"):
        guard.verifica_push(repo, f"refs/heads/main {new} refs/heads/main {'f'*40}", previous["remote_url"])
    for remote_oid in ("0" * 40, new):
        with pytest.raises(ValueError, match="Storia remota estranea"):
            guard.verifica_push(repo, f"refs/heads/main {new} refs/heads/main {remote_oid}", previous["remote_url"])
    # Certificate tampering must not bless a different original history.
    path = repo / ".git/bellomberg-release/certificate.json"
    cert = json.loads(path.read_text())
    cert["riscrittura_messaggi"]["precedente_history"] = [new]
    path.write_text(json.dumps(cert), encoding="utf-8")
    with pytest.raises(ValueError):
        guard.verifica_push(repo, f"refs/heads/main {new} refs/heads/main {old}", previous["remote_url"])


def test_rewrite_checks_intermediate_trees_with_same_commit_count(release):
    repo, _, artifact = release
    _published_with_label(release)
    git(repo, "commit", "--allow-empty", "-qm", "Middle (Codex GPT-6)")
    git(repo, "commit", "--allow-empty", "-qm", "Final (Codex GPT-6)")
    path = guard.certifica_deposito(repo, manifest(artifact), artifact)
    previous = json.loads(path.read_text())
    git(repo, "push", "origin", "main")
    _, head = _rewrite_messages(repo)
    guard.certifica_riscrittura_messaggi(repo, previous, artifact)
    # Even another previously certified ancestor is not the exact approved lease.
    with pytest.raises(ValueError, match="Storia remota estranea"):
        guard.verifica_push(repo, f"refs/heads/main {head} refs/heads/main {previous['history'][1]}",
                            previous["remote_url"])
    def raw_git(*args, data=None):
        return subprocess.run(["git", *args], cwd=repo, input=data,
                              capture_output=True, check=True).stdout
    blob = raw_git("hash-object", "-w", "--stdin", data=b"Intermediate changed content\n").strip()
    tree = raw_git("mktree", data=b"100644 blob " + blob + b"\treadme.txt\n").strip()
    middle = git(repo, "rev-parse", "HEAD^").stdout.strip()
    raw = raw_git("cat-file", "commit", middle)
    raw = b"tree " + tree + b"\n" + raw.split(b"\n", 1)[1]
    changed_middle = raw_git("hash-object", "-t", "commit", "-w", "--stdin", data=raw).strip()
    raw = raw_git("cat-file", "commit", head).replace(
        b"parent " + middle.encode() + b"\n", b"parent " + changed_middle + b"\n", 1)
    changed_head = raw_git("hash-object", "-t", "commit", "-w", "--stdin", data=raw).decode().strip()
    git(repo, "update-ref", "refs/heads/main", changed_head, head)
    assert len(git(repo, "rev-list", "HEAD").stdout.split()) == len(previous["history"])
    assert git(repo, "rev-parse", "HEAD^{tree}").stdout.strip() == previous["tree"]
    with pytest.raises(ValueError, match="oltre la rimozione"):
        guard.certifica_riscrittura_messaggi(repo, previous, artifact)
