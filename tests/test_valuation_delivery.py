"""Offline delivery failures: generations, modified copies and complete MIME."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

import pytest

from bellomberg.agents import consigliere_multi as committee
from bellomberg.reporting import email_sender


def artifact(tmp_path, ticker="SYNTH.A", name="arbitrary-name.xlsx"):
    from openpyxl import Workbook
    path = tmp_path / name
    wb = Workbook()
    wb.active["A1"] = "Synthetic delivery fixture; no economic validation"
    wb.save(path)
    wb.close()
    result = {"ticker": ticker, "snapshot_id": "a" * 64,
              "generation_id": "11111111-1111-4111-8111-111111111111",
              "path": str(path), "valuation_usability": {"usable": True},
              "_thesis_saved": {"thesis_id": 7},
              "workbook_sha256": sha256(path.read_bytes()).hexdigest()}
    path.with_suffix(".payload.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def manifest(results, root):
    from bellomberg.reporting.valuation_delivery import build_manifest
    return build_manifest(results, roots=[root])


def test_run_selects_requested_generation_not_newer_unrelated_file(tmp_path, monkeypatch):
    result = artifact(tmp_path)
    artifact(tmp_path, "UNRELATED", "VAL_UNRELATED.xlsx")
    monkeypatch.setattr(committee, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(committee, "MODELS_DIR", tmp_path / "unused")
    # Old mtime does not invalidate a specific, verified reused revision.
    assert committee._collect_dcf_files(datetime(2100, 1, 1), {"SYNTH.A": result}) == [result["path"]]
    assert committee._collect_dcf_files(datetime(1970, 1, 1), {}) == []


@pytest.mark.parametrize("fault, expected", [
    ("incomplete", "incomplete"), ("missing_file", "file_missing"),
    ("modified", "file_modified"), ("wrong_generation", "metadata_mismatch"),
    ("unregistered", "not_registered"), ("missing_sidecar", "metadata_missing"),
])
def test_different_failure_stages_remain_explicit(tmp_path, fault, expected):
    result = artifact(tmp_path)
    path = Path(result["path"])
    if fault == "incomplete": result["valuation_usability"] = {"usable": False, "reasons": ["capital missing"]}
    if fault == "missing_file": path.unlink()
    if fault == "modified": path.write_bytes(path.read_bytes() + b"personal changes")
    if fault == "wrong_generation": result["generation_id"] = "22222222-2222-4222-8222-222222222222"
    if fault == "unregistered": result["_thesis_saved"] = {"error": "database locked"}
    if fault == "missing_sidecar": path.with_suffix(".payload.json").unlink()
    receipt = manifest({"SYNTH.A": result}, tmp_path)
    assert receipt["attachments"] == []
    row = receipt["valuations"][0]
    assert row["status"] == expected
    assert row["reason"]
    assert row["snapshot_id"] == result["snapshot_id"]
    assert row["generation_id"] == result["generation_id"]
    assert row["email_included"] is False


def test_missing_input_is_distinct_from_calculated_unpublished(tmp_path):
    results = {"SYNTH.A": {"error": "Provider rate limit", "valuation_usability": {"usable": False}},
               "SYNTH.B": {"valuation_usability": {"usable": True}, "snapshot_id": "b" * 64,
                           "generation_id": "id", "_thesis_saved": {"thesis_id": 8}}}
    receipt = manifest(results, tmp_path)
    assert [row["status"] for row in receipt["valuations"]] == ["incomplete", "file_missing"]
    assert receipt["valuations"][0]["reason"] == "Provider rate limit"


def test_outside_file_never_enters_package(tmp_path):
    result = artifact(tmp_path)
    inside = tmp_path / "models"
    inside.mkdir()
    receipt = manifest({"SYNTH.A": result}, inside)
    assert receipt["attachments"] == []
    assert receipt["valuations"][0]["status"] == "outside_model_roots"


def test_receipt_preserves_all_attempts_and_atomic_previous_on_failure(tmp_path, monkeypatch):
    from bellomberg.reporting import valuation_delivery as delivery
    receipt = manifest({"SYNTH.A": artifact(tmp_path)}, tmp_path)
    target = tmp_path / "run-valuations.json"
    delivery.save_manifest(target, receipt)
    original = target.read_bytes()
    def fail(*a): raise PermissionError("locked")
    monkeypatch.setattr(delivery.os, "replace", fail)
    with pytest.raises(PermissionError): delivery.save_manifest(target, {**receipt, "email_status": "sent"})
    assert target.read_bytes() == original


def test_email_does_not_silently_remove_an_attachment(tmp_path, monkeypatch):
    present = tmp_path / "memo.pdf"
    present.write_bytes(b"%PDF synthetic")
    missing = tmp_path / "deleted.xlsx"
    calls = []
    monkeypatch.setattr(email_sender, "email_configurata", lambda: True)
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", lambda *a, **k: calls.append(True))
    assert email_sender.invia_email_multi_allegati([str(present), str(missing)]) is False
    assert calls == [], "Incomplete package must fail before any SMTP connection"


def test_dispatch_failure_and_repeated_requests_are_kept(monkeypatch):
    from bellomberg.agents import chat_tools
    from bellomberg.agents.specialists.base import Blackboard, Specialist
    board = Blackboard()
    specialist = Specialist(board, client=object())
    specialist.name = "fundamentals"
    def fail(*a, **k): raise RuntimeError("provider interrupted")
    monkeypatch.setattr(chat_tools, "dispatch", fail)
    result = specialist._execute_meta_tool("get_valuation", {"ticker": "SYNTH.A"})
    assert board.valuation_results["SYNTH.A"] == result
    assert board.valuation_attempts[0]["reason"].endswith("provider interrupted")
    monkeypatch.setattr(chat_tools, "dispatch", lambda *a, **k: {"data": {"error": "capital missing"}})
    specialist._execute_meta_tool("get_valuation", {"ticker": "SYNTH.A"})
    assert len(board.valuation_attempts) == 2
    assert board.valuation_attempts[0]["reason"].endswith("provider interrupted")
    assert board.valuation_attempts[1]["reason"] == "capital missing"


def test_workbook_changed_during_package_build_aborts_email(tmp_path, monkeypatch):
    result = artifact(tmp_path)
    delivery = manifest({"SYNTH.A": result}, tmp_path)
    Path(result["path"]).write_bytes(b"personal version")
    calls = []
    monkeypatch.setattr(email_sender, "email_configurata", lambda: True)
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", lambda *a, **k: calls.append(True))
    assert email_sender.invia_email_multi_allegati(delivery["attachments"],
        expected_hashes=delivery["expected_hashes"], delivery_receipt=delivery) is False
    assert delivery["email_status"] == "package_failed"
    assert calls == []


def test_smtp_failure_does_not_claim_excel_was_included(tmp_path, monkeypatch):
    from bellomberg.reporting.valuation_delivery import record_email_outcome
    result = artifact(tmp_path)
    delivery = manifest({"SYNTH.A": result}, tmp_path)
    monkeypatch.setattr(email_sender, "email_configurata", lambda: True)
    def disconnected(*args, **kwargs):
        raise OSError("synthetic SMTP outage")
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", disconnected)
    sent = email_sender.invia_email_multi_allegati(delivery["attachments"],
        expected_hashes=delivery["expected_hashes"], delivery_receipt=delivery)
    assert sent is False
    assert delivery["mime_attachments"] == [result["path"]], "MIME package existed before SMTP"
    record_email_outcome(delivery, sent)
    assert delivery["email_status"] == "not_sent"
    assert delivery["valuations"][0]["email_included"] is False


def test_recovery_uses_only_memo_receipt_and_rechecks_hash(tmp_path):
    from bellomberg.reporting.valuation_delivery import recover_manifest, save_manifest
    models = tmp_path / "models"
    notes = tmp_path / "research_notes"
    models.mkdir()
    notes.mkdir()
    selected = artifact(models, ticker="SYNTH.A", name="selected.xlsx")
    unrelated = artifact(models, ticker="OTHER", name="VAL_OTHER.xlsx")
    original = manifest({"SYNTH.A": selected}, models)
    original["memo_id"] = 42
    original["memo_sha256"] = sha256(b"Synthetic original memo").hexdigest()
    save_manifest(notes / "bellomberg_20260922_1200_valuations.json", original)

    recovery = recover_manifest(42, receipts_dir=notes, roots=[models],
                                memo_sha256=original["memo_sha256"])
    assert recovery["attachments"] == [selected["path"]]
    assert unrelated["path"] not in recovery["attachments"]
    assert recovery["expected_hashes"][selected["path"]] == selected["workbook_sha256"]

    Path(selected["path"]).write_bytes(b"personal modified version")
    changed = recover_manifest(42, receipts_dir=notes, roots=[models],
                               memo_sha256=original["memo_sha256"])
    assert changed["attachments"] == []
    assert changed["valuations"][0]["status"] == "file_modified"


def test_recovery_without_exact_receipt_excludes_recent_workbook(tmp_path):
    from bellomberg.reporting.valuation_delivery import recover_manifest
    models = tmp_path / "models"
    notes = tmp_path / "research_notes"
    models.mkdir()
    notes.mkdir()
    artifact(models, name="VAL_RECENT_BUT_UNRELATED.xlsx")
    recovery = recover_manifest(42, receipts_dir=notes, roots=[models],
                                memo_sha256=sha256(b"Synthetic memo").hexdigest())
    assert recovery["attachments"] == []
    assert recovery["expected_hashes"] == {}
    assert recovery["issues"]


def test_recovery_rejects_ambiguous_receipt_or_duplicate_ticker(tmp_path):
    from bellomberg.reporting.valuation_delivery import recover_manifest, save_manifest
    models = tmp_path / "models"
    notes = tmp_path / "research_notes"
    models.mkdir()
    notes.mkdir()
    selected = artifact(models)
    original = manifest({"SYNTH.A": selected}, models)
    original["memo_id"] = 42
    original["memo_sha256"] = sha256(b"Synthetic original memo").hexdigest()
    first = notes / "bellomberg_20260922_1200_valuations.json"
    second = notes / "bellomberg_20260922_1201_valuations.json"
    save_manifest(first, original)
    save_manifest(second, original)
    assert recover_manifest(42, receipts_dir=notes, roots=[models],
                            memo_sha256=original["memo_sha256"])["attachments"] == []
    second.unlink()
    original["valuations"].append(dict(original["valuations"][0]))
    save_manifest(first, original)
    duplicate = recover_manifest(42, receipts_dir=notes, roots=[models],
                                 memo_sha256=original["memo_sha256"])
    assert duplicate["attachments"] == []
    assert duplicate["issues"]


def test_recovery_rejects_same_memo_id_with_different_original_text(tmp_path):
    from bellomberg.reporting.valuation_delivery import recover_manifest, save_manifest
    selected = artifact(tmp_path)
    original = manifest({"SYNTH.A": selected}, tmp_path)
    original.update(memo_id=42, memo_sha256=sha256(b"Old memo").hexdigest())
    save_manifest(tmp_path / "bellomberg_20260922_1200_valuations.json", original)
    recovery = recover_manifest(42, receipts_dir=tmp_path, roots=[tmp_path],
                                memo_sha256=sha256(b"Different restored memo").hexdigest())
    assert recovery["attachments"] == []
    assert recovery["issues"]
