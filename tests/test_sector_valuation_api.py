"""Execute the real F17 endpoint body against temp files/DB, without API startup."""
import ast
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy

import pytest

from test_sector_usability import payload_for


def endpoint(tmp_path, snapshots=None):
    source = Path("src/bellomberg/api/bellomberg_api.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "list_valuation_models")
    node.decorator_list = []
    db = SimpleNamespace(get_latest_valuation_snapshots=lambda: snapshots or {})
    namespace = {"os": os, "json": json, "datetime": datetime,
                 "get_db": lambda: db, "_known_tickers": lambda db: set(),
                 "_val_dirs": lambda: [str(tmp_path)],
                 "_VAL_NAME": re.compile(r"^(VAL|DCF)_(.+?)(?:_(\d{8}(?:_\d{4})?))?(_FLAGGED)?\.xlsx$")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "f17-endpoint", "exec"), namespace)
    return namespace["list_valuation_models"]()


def write_model(tmp_path, payload):
    workbook = tmp_path / "VAL_SYNTH.xlsx"
    workbook.write_bytes(b"synthetic workbook")
    payload.update(generation_id="c1a47890-8b24-4ec8-86ae-d29ecdc23062",
                   workbook_sha256=hashlib.sha256(workbook.read_bytes()).hexdigest(), _timestamp=datetime.now().isoformat())
    workbook.with_suffix(".payload.json").write_text(json.dumps(payload), encoding="utf-8")
    return workbook


def test_external_ticker_uses_canonical_sidecar_identity_and_common_number(tmp_path):
    payload = payload_for()
    write_model(tmp_path, payload)
    model = endpoint(tmp_path)["models"][0]
    assert model["ticker"] == "SYNTH" and not model["matched"]
    assert model["identity_status"] == "canonical"
    assert model["fair_value"] == payload["fair_value_weighted"]
    assert model["valuation_usability"]["usable"]
    assert model["snapshot_id"] == payload["snapshot_id"]


def test_legacy_numbers_and_blocked_nav_sotp_never_reappear_in_f17(tmp_path):
    payload = {"ticker": "SYNTH", "engine": "mnav", "fair_value_nav": 900,
               "fair_value_sotp": 1000, "fair_value_base": 1100,
               "sanity": {"severity": "BLOCK"}, "nav_per_share": 50}
    write_model(tmp_path, payload)
    model = endpoint(tmp_path)["models"][0]
    assert model["fair_value"] is None and model["upside_pct"] is None
    for key in ("fair_value_nav", "fair_value_sotp", "fair_value_base"):
        assert model["detail"][key] is None
    assert model["detail"]["nav_per_share"] == 50
    assert model["valuation_usability"]["usable"] is False


def test_changed_workbook_cannot_reuse_sidecar_number(tmp_path):
    workbook = write_model(tmp_path, payload_for())
    workbook.write_bytes(b"different generation")
    model = endpoint(tmp_path)["models"][0]
    assert model["fair_value"] is None
    assert model["valuation_usability"]["usable"] is False
    assert "generaz" in " ".join(model["detail"]["warnings"]).lower()


def test_incomplete_snapshot_is_visible_without_any_workbook_or_holding(tmp_path):
    payload = payload_for("managed_care")
    payload["acquisition_tasks"] = [{"field": "capital_bridge", "status": "missing"}]
    data = endpoint(tmp_path, {"SYNTH": {"created_at": "2026-09-10", "payload": payload}})
    assert data["count"] == 1
    model = data["models"][0]
    assert model["file"] == "" and model["fair_value"] is None
    assert model["acquisition_tasks"] == payload["acquisition_tasks"]


@pytest.mark.parametrize("current_model", ["managed_care", "software", "bank"])
def test_latest_snapshot_wins_over_touched_older_workbook_even_on_same_day(tmp_path, current_model):
    historical = payload_for()
    workbook = write_model(tmp_path, historical)
    current = payload_for(current_model)
    current.update(generation_id="8011949f-a0e4-44f4-b883-c2ec59b16113",
                   fair_value_weighted=240)
    if current_model == "managed_care":
        current["acquisition_tasks"] = [{"field": "capital_bridge", "status": "missing"}]
    # A copied/touched historical workbook must not become the current analysis.
    touched = datetime.fromisoformat("2026-09-10T23:59:00").timestamp()
    os.utime(workbook, (touched, touched))
    data = endpoint(tmp_path, {"SYNTH": {"created_at": "2026-09-10T10:00:00", "payload": current}})
    stored, old = data["models"]
    assert stored["file"] == "" and stored["generation_id"] == current["generation_id"]
    assert stored["canonical"] is True and stored["current_generation"] is True
    assert old["file"] == workbook.name
    assert old["canonical"] is False and old["current_generation"] is False
    assert old["valuation_usability"]["usable"] is False
    assert old["fair_value"] is None and old["detail"]["fair_value_weighted"] is None
    assert old["upside_pct"] is None
    assert any("generation_id" in reason or "snapshot_id" in reason
               for reason in old["valuation_usability"]["reasons"])
    expected = None if current_model == "managed_care" else 240
    assert stored["fair_value"] == expected
    assert stored["valuation_usability"]["usable"] is (current_model != "managed_care")
    # F17 sorts canonical before mtime: its existing selection must also pick current.
    selected = sorted(data["models"], key=lambda model: (
        model["canonical"], model["generated_at"] or ""), reverse=True)[0]
    assert selected["generation_id"] == current["generation_id"]


def test_current_matching_sidecar_keeps_its_value_and_uses_snapshot_revision_time(tmp_path):
    current = payload_for()
    workbook = write_model(tmp_path, current)
    touched = datetime.fromisoformat("2026-09-10T23:59:00").timestamp()
    os.utime(workbook, (touched, touched))
    data = endpoint(tmp_path, {"SYNTH": {"created_at": "2026-09-10T10:00:00", "payload": current}})
    assert data["count"] == 1
    model = data["models"][0]
    assert model["current_generation"] is True and model["canonical"] is True
    assert model["fair_value"] == 120
    assert model["generated_at"] == "2026-09-10 10:00:00"


@pytest.mark.parametrize("artifact_error,with_path", [
    ("workbook_changed", True), ("workbook_changed", False),
    ("sidecar_corrupt", True), ("sidecar_missing", True)])
def test_unverifiable_artifact_cannot_revive_same_generation_from_db(tmp_path, artifact_error, with_path):
    current = payload_for()
    workbook = write_model(tmp_path, current)
    if with_path:
        current["path"] = str(workbook)
    before = deepcopy(current)
    if artifact_error == "workbook_changed":
        workbook.write_bytes(b"changed workbook bytes")
    elif artifact_error == "sidecar_corrupt":
        workbook.with_suffix(".payload.json").write_text("{broken", encoding="utf-8")
    else:
        workbook.with_suffix(".payload.json").unlink()
    data = endpoint(tmp_path, {"SYNTH": {"created_at": "2026-09-10T10:00:00", "payload": current}})
    assert data["models"]
    assert all(model["fair_value"] is None and model["upside_pct"] is None for model in data["models"])
    assert all(model["valuation_usability"]["usable"] is False for model in data["models"])
    same_generation = [model for model in data["models"] if model["generation_id"] == current["generation_id"]]
    assert same_generation
    assert all(model["sanity_severity"] == "BLOCK" for model in same_generation)
    assert any("artefatto" in " ".join(model["detail"].get("warnings", [])).lower()
               for model in same_generation)
    assert current == before


def test_corrupt_historical_artifact_does_not_block_new_generation_without_workbook(tmp_path):
    historical = payload_for()
    workbook = write_model(tmp_path, historical)
    workbook.with_suffix(".payload.json").write_text("{broken", encoding="utf-8")
    current = payload_for("bank")
    current.update(generation_id="8011949f-a0e4-44f4-b883-c2ec59b16113",
                   fair_value_weighted=240)
    data = endpoint(tmp_path, {"SYNTH": {"created_at": "2026-09-10T10:00:00", "payload": current}})
    model = data["models"][0]
    assert model["file"] == "" and model["generation_id"] == current["generation_id"]
    assert model["fair_value"] == 240 and model["valuation_usability"]["usable"] is True
