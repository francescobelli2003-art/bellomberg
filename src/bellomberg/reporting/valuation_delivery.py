"""Run receipts and exact-generation attachments; never infer identity from names."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4


def describe_result(ticker, result):
    result = result if isinstance(result, dict) else {}
    usability = result.get("valuation_usability") or {}
    usable = usability.get("usable") is True
    publication = result.get("model_publication") or {}
    return {"ticker": ticker, "snapshot_id": result.get("snapshot_id"),
            "generation_id": result.get("generation_id"), "path": result.get("path"),
            "thesis_id": (result.get("_thesis_saved") or {}).get("thesis_id"),
            "method": (result.get("valuation_decision") or {}).get("method_id"),
            "revision_origin": "reused" if result.get("reused") else "generated" if result.get("path") else "not_created",
            "valuation_date": result.get("valuation_date"),
            "status": "calculated" if usable else "incomplete",
            "reason": "" if usable else (result.get("error") or "; ".join(usability.get("reasons") or [])
                                          or "Valutazione incompleta; motivo non disponibile"),
            "model_publication": {"status": publication.get("status", "not_verified"),
                                  "reason": publication.get("reason", "Stato della versione corrente non verificato")},
            # publication_status is the v1 artifact field, retained for old receipts.
            "artifact_status": "not_verified", "publication_status": "not_verified", "email_included": False,
            "recorded_at": datetime.now(timezone.utc).isoformat()}


def build_manifest(results, *, roots, attempts=()):
    """Allow only usable, registered artifacts whose sidecar and bytes match the request."""
    allowed = [Path(root).resolve() for root in roots]
    rows, files, hashes = [], [], {}
    for ticker, result in sorted((results or {}).items()):
        row = describe_result(ticker, result)
        rows.append(row)
        if row["status"] == "incomplete":
            continue
        def reject(status, reason):
            row.update(status=status, reason=reason, artifact_status="unavailable", publication_status="unavailable")
        try:
            path = Path(row["path"]).resolve() if row["path"] else None
        except (OSError, ValueError, TypeError) as exc:
            reject("file_unreadable", "Percorso workbook invalido: " + type(exc).__name__)
            continue
        if path is None or not path.is_file():
            reject("file_missing", "Calcolo disponibile ma workbook assente")
            continue
        if not any(path.is_relative_to(root) for root in allowed):
            reject("outside_model_roots", "Workbook fuori dalle cartelle dei modelli")
            continue
        if not row["thesis_id"]:
            reject("not_registered", "Registrazione snapshot non confermata: " + str(result.get("_thesis_saved")))
            continue
        try:
            sidecar = json.loads(path.with_suffix(".payload.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            reject("metadata_missing", "Metadati workbook non leggibili: " + type(exc).__name__)
            continue
        if (not isinstance(sidecar, dict) or not row["snapshot_id"] or not row["generation_id"]
                or any(sidecar.get(key) != row[key] for key in ("ticker", "snapshot_id", "generation_id"))
                or (sidecar.get("valuation_usability") or {}).get("usable") is not True):
            reject("metadata_mismatch", "Ticker, snapshot, generazione o utilizzabilita discordanti")
            continue
        try:
            digest = sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            reject("file_unreadable", "Workbook non leggibile: " + type(exc).__name__)
            continue
        if digest != result.get("workbook_sha256") or digest != sidecar.get("workbook_sha256"):
            reject("file_modified", "Workbook modificato rispetto alla generazione; copia conservata")
            continue
        file = str(path)
        row.update(status="ready", reason="Generazione registrata e contenuto verificato",
                   artifact_status="available", publication_status="available", workbook_sha256=digest, path=file)
        files.append(file)
        hashes[file] = digest
    return {"schema_version": 1, "valuations": rows, "attempts": list(attempts),
            "attachments": files, "expected_hashes": hashes, "email_status": "not_attempted"}


def recover_manifest(memo_id, *, receipts_dir, roots, memo_sha256):
    """Recheck the one saved run receipt for this memo; never infer files by mtime."""
    matches = []
    for path in Path(receipts_dir).glob("bellomberg_*_valuations.json"):
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(source, dict) and source.get("memo_id") == memo_id:
            matches.append((path, source))
    if len(matches) != 1:
        result = build_manifest({}, roots=roots)
        result.update(memo_id=memo_id, issues=["Receipt originale assente o ambiguo: nessun Excel attestabile"])
        return result

    path, source = matches[0]
    if (not isinstance(memo_sha256, str) or len(memo_sha256) != 64
            or source.get("memo_sha256") != memo_sha256):
        result = build_manifest({}, roots=roots)
        result.update(memo_id=memo_id, source_manifest=str(path),
                      issues=["Testo del memo originale assente o diverso dal receipt: nessun Excel attestabile"])
        return result
    rows = source.get("valuations")
    hashes = source.get("expected_hashes")
    attachments = source.get("attachments")
    if (source.get("schema_version") != 1 or not isinstance(rows, list)
            or not isinstance(hashes, dict) or not isinstance(attachments, list)):
        result = build_manifest({}, roots=roots)
        result.update(memo_id=memo_id, source_manifest=str(path),
                      issues=["Receipt originale malformato: nessun Excel attestabile"])
        return result

    results, issues, skipped = {}, [], []
    counts = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("ticker"), str):
            ticker = row["ticker"]
            counts[ticker] = counts.get(ticker, 0) + 1
    for row in rows:
        if not isinstance(row, dict):
            issues.append("Riga valuation malformata nel receipt originale")
            continue
        if row.get("status") != "ready" or row.get("publication_status") != "available":
            skipped.append({**row, "email_included": False})
            continue
        ticker, file = row.get("ticker"), row.get("path")
        digest = row.get("workbook_sha256")
        if (not isinstance(ticker, str) or not ticker or counts.get(ticker) != 1
                or not isinstance(file, str) or file not in attachments
                or not isinstance(digest, str) or hashes.get(file) != digest
                or not row.get("snapshot_id") or not row.get("generation_id")
                or type(row.get("thesis_id")) is not int or row["thesis_id"] < 1):
            issues.append("Receipt incompleto o discordante per " + str(ticker))
            continue
        results[ticker] = {"path": file, "snapshot_id": row["snapshot_id"],
                           "generation_id": row["generation_id"],
                           "workbook_sha256": digest,
                           "valuation_usability": {"usable": True},
                           "_thesis_saved": {"thesis_id": row["thesis_id"]},
                           "model_publication": {"status": "historical_receipt",
                               "reason": "Copia datata del pacchetto originale; versione corrente non riverificata"},
                           "reused": row.get("revision_origin") == "reused"}
    attempts = source.get("attempts")
    if not isinstance(attempts, list):
        issues.append("Tentativi originali non leggibili")
        attempts = []
    result = build_manifest(results, roots=roots, attempts=attempts)
    result["valuations"].extend(skipped)
    result.update(memo_id=memo_id, source_manifest=str(path), issues=issues)
    return result


def record_email_outcome(receipt, sent):
    """An assembled MIME is not evidence that SMTP delivered the message."""
    receipt["email_status"] = ("sent" if sent is True else
                               "package_failed" if receipt.get("email_status") == "package_failed"
                               else "not_sent")
    included = (set(receipt.get("mime_attachments") or []) & set(receipt.get("attachments") or [])
                if sent is True else set())
    for row in receipt.get("valuations", []):
        row["email_included"] = row.get("status") == "ready" and row.get("path") in included
    return receipt


def save_manifest(path, manifest):
    """A failed replacement leaves the previous receipt intact and raises to the caller."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
