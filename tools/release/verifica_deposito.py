"""Local release certificate and pre-push guard. No network or private data readers.

The installer copies this stdlib-only verifier into the public repository's Git
metadata. The certificate stays local. Removing the hook or using --no-verify
is an explicit bypass; this is an operator safeguard, not a hostile-user sandbox.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

CONTROLLI_RICHIESTI = frozenset((
    "gitleaks", "vietate", "env", "esclusi", "lotti", "dimensione",
    "valori_db", "lista_privata", "ticker_soli", "payload",
    "valori_estesi", "vietate_forme",
))


def _git(repo, *args, input=None):
    result = subprocess.run(["git", *args], cwd=repo, input=input,
                            capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Git check failed: " + " ".join(args[:2]))
    return result.stdout


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def valida_manifest(doc):
    """Reject a green-looking summary unless every required proof is present."""
    try:
        v, artifact, source = doc["verifica"], doc["artefatto"], doc["input"]
        checks = v["controlli"]
        names = [c["nome"] for c in checks]
        valid = (
            doc["versione"] == 1 and v["stato"] == "OK"
            and v["rigoroso"] is True
            and type(v["cancello_exit"]) is int and v["cancello_exit"] == 0
            and type(v["suite_exit"]) is int and v["suite_exit"] == 0
            and v["suite_non_eseguita"] is False
            and v["sorgente_sporca_file"] == 0
            and not v["controlli_solo"] and not v["non_eseguiti"]
            and len(names) == len(CONTROLLI_RICHIESTI)
            and set(names) == CONTROLLI_RICHIESTI
            and all(c["errore"] is False and c["osservazione"] is False
                    and type(c["hit"]) is int and c["hit"] == 0 for c in checks)
            and artifact["immutato_dalla_suite"] is True
            and artifact["sha256"] == artifact["sha256_prima_suite"]
            and artifact["file"] == artifact["file_prima_suite"] > 0
            and not source["fonti_ko"]
            and not source["conteggi"].get("payload_canali_guasti", 0)
            and all(re.fullmatch(r"[0-9a-f]{64}", value or "") for value in (
                artifact["sha256"], source["sha256_corpus_privato"], source["sha256_payload"]))
        )
    except (KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("Manifest incompleto o non valido: deposito/push non certificabile")


def _git_dir(repo):
    return Path(_git(repo, "rev-parse", "--absolute-git-dir").decode().strip())


def _files_git(repo):
    files = {}
    for entry in _git(repo, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise ValueError("Symlink/gitlink/non-file in release tree")
        files[name.decode("utf-8")] = _git(repo, "cat-file", "blob", oid.decode())
    return files


def _files_artifact(tree):
    tree = Path(tree)
    files, digest = {}, hashlib.sha256()
    for directory, dirs, names in os.walk(tree):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for d in dirs:
            path = Path(directory, d)
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("Linked directory in release artifact")
        for name in sorted(n for n in names if n != ".git"):
            path = Path(directory, name)
            if path.is_symlink():
                raise ValueError("Symlink in release artifact")
            rel = path.relative_to(tree).as_posix()
            data = path.read_bytes()
            encoded = rel.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
            files[rel] = data
    return files, digest.hexdigest()


def _stessi_byte_o_eol(original, committed):
    if original == committed:
        return True
    # Git's text normalization is allowed explicitly, never an arbitrary filter.
    try:
        original.decode("utf-8")
        committed.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return (b"\0" not in original and b"\0" not in committed
            and original.replace(b"\r\n", b"\n") == committed.replace(b"\r\n", b"\n"))


def _atomic_json(path, value):
    fd, tmp = tempfile.mkstemp(prefix="certificate-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def certifica_deposito(repo, manifest, artifact):
    """Bind checked content to Git objects, preserve evidence and install guard."""
    valida_manifest(manifest)
    expected, digest = _files_artifact(artifact)
    if digest != manifest["artefatto"]["sha256"] or len(expected) != manifest["artefatto"]["file"]:
        raise ValueError("Artefatto modificato dopo i controlli")
    committed = _files_git(repo)
    if set(expected) != set(committed) or any(
            not _stessi_byte_o_eol(data, committed[name]) for name, data in expected.items()):
        raise ValueError("Il commit non contiene i file controllati (contenuti/filtri Git)")
    if _git(repo, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Clone pubblico sporco dopo il deposito")
    branch = _git(repo, "symbolic-ref", "HEAD").decode().strip()
    remote = _git(repo, "remote", "get-url", "--push", "origin").decode().strip()
    if not remote:
        raise ValueError("Destinazione origin assente")
    directory = prepara_guardia(repo)
    verifier = directory / "verify.py"
    certificate = {
        "versione": 1, "manifest": manifest,
        "head": _git(repo, "rev-parse", "HEAD").decode().strip(),
        "tree": _git(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
        "history": _git(repo, "rev-list", "HEAD").decode().split(),
        "branch": branch, "remote_url": remote,
        "verifier_sha256": _sha(verifier.read_bytes()),
        "eol_normalized_files": sum(expected[n] != committed[n] for n in expected),
    }
    _atomic_json(directory / "certificate.json", certificate)
    return directory / "certificate.json"


def _verifica_riscrittura_messaggi(repo, old_history, new_history):
    """Only the PM-approved label removal; every other commit byte is preserved."""
    if (not isinstance(old_history, list) or not isinstance(new_history, list)
            or not old_history or len(old_history) != len(new_history)
            or len(set(old_history)) != len(old_history)
            or len(set(new_history)) != len(new_history)):
        raise ValueError("Riscrittura messaggi: storia incompleta o diversa")
    for history in (old_history, new_history):
        if not all(isinstance(oid, str) and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", oid)
                   for oid in history):
            raise ValueError("Riscrittura messaggi: identificativo non valido")
        if _git(repo, "rev-list", history[0]).decode().split() != history:
            raise ValueError("Riscrittura messaggi: storia non corrispondente agli oggetti Git")
    mapping = dict(zip(old_history, new_history))
    for old, new in mapping.items():
        raw = _git(repo, "cat-file", "commit", old)
        headers, message = raw.split(b"\n\n", 1)
        lines = []
        for line in headers.split(b"\n"):
            if line.startswith(b"parent "):
                parent = line[7:].decode()
                if parent not in mapping:
                    raise ValueError("Riscrittura messaggi: parent fuori dalla storia verificata")
                line = b"parent " + mapping[parent].encode()
            if line.startswith(b"gpgsig ") and old != new:
                raise ValueError("Riscrittura di commit firmati non supportata")
            lines.append(line)
        expected = b"\n".join(lines) + b"\n\n" + message.replace(b" (Codex GPT-6)", b"")
        if _git(repo, "cat-file", "commit", new) != expected:
            raise ValueError("Riscrittura messaggi: modifiche oltre la rimozione autorizzata")


def certifica_riscrittura_messaggi(repo, precedente, artifact):
    """Explicit one-push permission for an authorized metadata-only history rewrite.

    Call only on the PM's request. Keep the old certificate/bundle separately;
    use an exact --force-with-lease and ordinary recertification after success.
    Content proofs remain mandatory; this does not certify new application code.
    """
    try:
        valida_manifest(precedente["manifest"])
        history = _git(repo, "rev-list", "HEAD").decode().split()
        old_history = precedente["history"]
        if (precedente["versione"] != 1 or not old_history
                or precedente["head"] != old_history[0]
                or _git(repo, "rev-parse", precedente["head"] + "^{tree}").decode().strip() != precedente["tree"]
                or _git(repo, "symbolic-ref", "HEAD").decode().strip() != precedente["branch"]
                or _git(repo, "remote", "get-url", "--push", "origin").decode().strip() != precedente["remote_url"]):
            raise ValueError("Riscrittura messaggi: certificato precedente non corrispondente")
        _verifica_riscrittura_messaggi(repo, old_history, history)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Riscrittura messaggi: certificato precedente illeggibile") from exc
    path = certifica_deposito(repo, precedente["manifest"], artifact)
    certificate = json.loads(path.read_text(encoding="utf-8"))
    certificate["riscrittura_messaggi"] = {"precedente_head": precedente["head"],
                                          "precedente_history": old_history}
    _atomic_json(path, certificate)
    return path


def prepara_guardia(repo):
    """Install before modifying the destination: a missing certificate blocks push."""
    directory = _git_dir(repo) / "bellomberg-release"
    hooks = directory / "hooks"
    effective = Path(_git(repo, "rev-parse", "--git-path", "hooks/pre-push").decode().strip())
    if not effective.is_absolute():
        effective = Path(repo) / effective
    if effective.exists() and effective.resolve() != (hooks / "pre-push").resolve():
        raise ValueError("Hook pre-push preesistente: conservarlo e integrarlo prima del deposito")
    hooks.mkdir(parents=True, exist_ok=True)
    verifier = directory / "verify.py"
    verifier.write_bytes(Path(__file__).read_bytes())
    hook = "#!/bin/sh\nexec " + shlex.quote(sys.executable.replace("\\", "/"))
    hook += " " + shlex.quote(verifier.as_posix())
    hook += ' --repo "$(git rev-parse --show-toplevel)" --remote-url "$2"\n'
    (hooks / "pre-push").write_text(hook, encoding="utf-8", newline="\n")
    (hooks / "pre-push").chmod(0o755)
    _git(repo, "config", "--local", "core.hooksPath", hooks.as_posix())
    if _git(repo, "config", "--get", "core.hooksPath").decode().strip() != hooks.as_posix():
        raise ValueError("Installazione hook non confermata")
    return directory


def verifica_push(repo, refs, remote_url):
    try:
        directory = _git_dir(repo) / "bellomberg-release"
        certificate = json.loads((directory / "certificate.json").read_text(encoding="utf-8"))
        valida_manifest(certificate["manifest"])
        if certificate["versione"] != 1 or _sha((directory / "verify.py").read_bytes()) != certificate["verifier_sha256"]:
            raise ValueError("Verifier/certificato non corrispondenti")
        head = _git(repo, "rev-parse", "HEAD").decode().strip()
        if (head != certificate["head"]
                or _git(repo, "rev-parse", "HEAD^{tree}").decode().strip() != certificate["tree"]
                or _git(repo, "rev-list", "HEAD").decode().split() != certificate["history"]):
            raise ValueError("Commit/storia non certificati: eseguire un nuovo export completo")
        branch = _git(repo, "symbolic-ref", "HEAD").decode().strip()
        if branch != certificate["branch"]:
            raise ValueError("Ramo diverso da quello certificato")
        if remote_url != certificate["remote_url"]:
            raise ValueError("Destinazione diversa da quella certificata")
        if _git(repo, "status", "--porcelain", "--untracked-files=all").strip():
            raise ValueError("Clone pubblico modificato dopo la certificazione")
        lines = [line.split() for line in refs.splitlines() if line.strip()]
        if len(lines) != 1 or len(lines[0]) != 4:
            raise ValueError("Serve un solo ref certificato, niente tag/push multipli")
        local_ref, local_oid, remote_ref, remote_oid = lines[0]
        if local_ref != branch or remote_ref != branch or local_oid != head:
            raise ValueError("Ref spinto diverso dal commit/ramo certificato")
        rewrite = certificate.get("riscrittura_messaggi")
        if rewrite is not None:
            if (not isinstance(rewrite, dict) or remote_oid != rewrite.get("precedente_head")
                    or not rewrite.get("precedente_history")
                    or remote_oid != rewrite["precedente_history"][0]):
                raise ValueError("Storia remota estranea: riscrittura ammessa solo dal commit autorizzato")
            _verifica_riscrittura_messaggi(repo, rewrite["precedente_history"], certificate["history"])
        elif remote_oid != "0" * len(head) and remote_oid not in certificate["history"]:
            raise ValueError("Storia remota estranea: rifiutato anche un force-push")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Certificato locale assente o illeggibile") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--remote-url", required=True)
    args = parser.parse_args(argv)
    try:
        verifica_push(args.repo, sys.stdin.read(), args.remote_url)
    except ValueError as error:
        print("STOP: " + str(error), file=sys.stderr)
        return 2
    print("Release certificate verified: commit, history, destination and all required checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
