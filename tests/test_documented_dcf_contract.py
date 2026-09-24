"""Documented tool contract through real adapters, temporary SQLite, F17 and local MIME.

The SYNTH-EXT fixture is invented and flat: this certifies wiring, not issuer economics.
No provider/LLM/SMTP connection is permitted. No real issuer is certified by this fixture.
"""
from copy import deepcopy
from datetime import datetime
from email import policy
from email.parser import BytesParser
from hashlib import sha256
import json
from pathlib import Path
import socket

import pytest

from test_sector_operating_drivers import DAY, bundle_for
from test_sector_valuation_api import endpoint
from test_valuation_snapshot_persistence import db


@pytest.fixture
def contract_tools(tmp_path, monkeypatch, db):
    from bellomberg.agents import chat_tools, consigliere_multi
    from bellomberg.reporting import email_sender
    from bellomberg.storage import memory_db
    from bellomberg.valuation import dcf_engine

    def forbidden(*args, **kwargs):
        pytest.fail("Documented contract test attempted network or legacy calculation")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(dcf_engine, "_generate_valuation_legacy", forbidden)
    monkeypatch.setattr(dcf_engine, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(chat_tools, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(consigliere_multi, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(consigliere_multi, "MODELS_DIR", tmp_path / "unused-models")
    monkeypatch.setattr(memory_db, "MemoryDB", lambda: db)
    monkeypatch.setattr(email_sender, "EMAIL_FROM", "synthetic@example.invalid")
    monkeypatch.setattr(email_sender, "EMAIL_TO", "receiver@example.invalid")
    monkeypatch.setattr(email_sender, "EMAIL_PASSWORD", "synthetic-test-only")
    return chat_tools, consigliere_multi, email_sender


def _call(chat_tools, *, bundle=None, extra=None):
    source = bundle_for(profile="manufacturing") if bundle is None else bundle
    providers = {"profile": lambda *a, **k: deepcopy(source["case"]["sources"]["profile"])}
    args = {"ticker": "SYNTH-EXT", "method_records": deepcopy(source["case"]["records"]),
            "analysis_context": deepcopy(source["analysis_context"]), **(extra or {})}
    return args, chat_tools.dispatch("get_valuation", args, sector_providers=providers, as_of=DAY)["data"]


def test_tool_schema_accepts_context_that_the_real_documented_adapter_consumes(contract_tools):
    chat_tools, _, _ = contract_tools
    args, result = _call(chat_tools)
    assert result["valuation_usability"]["usable"], result
    schema = next(tool for tool in chat_tools.TOOL_DEFINITIONS if tool["name"] == "get_valuation")["input_schema"]
    context_schema = schema["properties"]["analysis_context"]
    missing = set(context_schema.get("required", [])) - set(args["analysis_context"])
    assert not missing, "Valid documented calculation rejected by the advertised tool contract: " + str(missing)
    assert set(context_schema["properties"]) == {"scenario_rationale", "revisions"}


def test_committee_dispatch_prepares_empty_records_and_tracks_exact_revised_snapshot(contract_tools, db, tmp_path, monkeypatch):
    from bellomberg.agents.specialists.base import Specialist, Blackboard
    from bellomberg.valuation.preparation_service import prepare_and_generate
    from test_input_preparation import _bundle, _documents, _propose_operating
    chat_tools, _, _ = contract_tools
    _publication_store(db, tmp_path, monkeypatch)
    original = _bundle()
    calls = []

    def authorized_prepare(bundle):
        calls.append(bundle["snapshot_id"])
        return prepare_and_generate(bundle, documents=_documents(), propose=_propose_operating,
                                    output_dir=tmp_path)

    specialist = Specialist.__new__(Specialist)
    specialist.name = "fundamentals"
    specialist._sector_bundles = {"SYNTH-EXT": original}
    specialist.blackboard = Blackboard(valuation_preparer=authorized_prepare)
    result = specialist._execute_meta_tool("get_valuation", {"ticker": "SYNTH-EXT"})["data"]
    assert not original["case"]["records"] and calls == [original["snapshot_id"]]
    assert result["valuation_usability"]["usable"], result
    assert result["preparation"]["status"] == "prepared"
    assert result["snapshot_id"] != original["snapshot_id"]
    assert result["_thesis_saved"]["thesis_id"] is not None
    assert Path(result["path"]).is_file()
    assert specialist._sector_bundles["SYNTH-EXT"]["snapshot_id"] == result["snapshot_id"]
    assert specialist.blackboard.valuation_results["SYNTH-EXT"]["generation_id"] == result["generation_id"]

    # A later round reuses the acquired records/current artifact, with no new preparation.
    second = specialist._execute_meta_tool("get_valuation", {"ticker": "SYNTH-EXT"})["data"]
    assert second["reused"] and second["generation_id"] == result["generation_id"]
    assert calls == [original["snapshot_id"]]


def test_explicit_desk_records_do_not_invoke_automatic_preparer(contract_tools):
    chat_tools, _, _ = contract_tools
    bundle = bundle_for(profile="manufacturing")
    result = chat_tools.dispatch("get_valuation", {"ticker": "SYNTH-EXT"}, prepared_bundle=bundle,
        valuation_preparer=lambda *_: pytest.fail("Existing records must not be silently replaced"))["data"]
    assert result["valuation_usability"]["usable"], result


def _publication_store(db, tmp_path, monkeypatch):
    from datetime import timezone
    from bellomberg.storage import valuation_versions as module
    with db._conn() as conn:
        module.ensure_schema(conn)
    real = module.ValuationVersions
    monkeypatch.setattr(module, "ValuationVersions", lambda db, *, roots: real(db, roots=roots,
        clock=lambda: datetime.fromisoformat(DAY).replace(tzinfo=timezone.utc)))
    return real(db, roots=[tmp_path], clock=lambda: datetime.fromisoformat(DAY).replace(tzinfo=timezone.utc))


def test_dispatch_publishes_registered_generation_and_respects_lock(contract_tools, db, tmp_path, monkeypatch):
    chat_tools, _, _ = contract_tools
    store = _publication_store(db, tmp_path, monkeypatch)
    _, first = _call(chat_tools)
    assert first["model_publication"]["status"] == "published"
    assert store.current("SYNTH-EXT")["current_generation"] == first["generation_id"]
    store.set_locked("SYNTH-EXT", True)
    _, next_result = _call(chat_tools)
    assert next_result["model_publication"]["status"] == "held"
    assert store.current("SYNTH-EXT")["current_generation"] == first["generation_id"]


def test_dispatch_captures_current_before_calculating_candidate(contract_tools, db, tmp_path, monkeypatch):
    from bellomberg.valuation import dcf_engine
    chat_tools, _, _ = contract_tools
    store = _publication_store(db, tmp_path, monkeypatch)
    generate = dcf_engine.generate_valuation
    concurrent = []
    def interleaved(*args, **kwargs):
        winner = generate(*args, **kwargs)
        assert db.save_valuation_thesis("SYNTH-EXT", valuation_payload=winner) is not None
        assert store.publish(winner["snapshot_id"], winner["generation_id"], expected_current_generation=None)["status"] == "published"
        concurrent.append(winner)
        return generate(*args, **kwargs)
    monkeypatch.setattr(dcf_engine, "generate_valuation", interleaved)
    _, result = _call(chat_tools)
    assert result["model_publication"]["status"] == "superseded"
    assert store.current("SYNTH-EXT")["current_generation"] == concurrent[0]["generation_id"]


def test_dispatch_without_publication_schema_declares_gap_and_does_not_migrate(contract_tools, db):
    chat_tools, _, _ = contract_tools
    _, result = _call(chat_tools)
    assert result["_thesis_saved"]["thesis_id"] is not None
    assert result["model_publication"]["status"] == "unavailable"
    assert "migration" in result["model_publication"]["reason"]
    with db._conn() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='valuation_model_heads'").fetchone() is None


def test_missing_publication_schema_blocks_paid_preparation(contract_tools):
    from test_input_preparation import _bundle
    chat_tools, _, _ = contract_tools
    calls = []
    result = chat_tools.dispatch("get_valuation", {"ticker": "SYNTH-EXT"},
        prepared_bundle=_bundle(), valuation_preparer=lambda *args: calls.append(args))
    assert calls == [], "Do not spend before the publication store is ready"
    result = result["data"]
    assert result["preparation"]["status"] == "blocked"
    assert "migration" in result["preparation"]["reason"]
    assert result["model_publication"]["status"] == "unavailable"
    assert result["valuation_usability"]["usable"] is False


def test_locked_candidate_is_labelled_in_capo_email_and_receipt(contract_tools, db, tmp_path, monkeypatch):
    from bellomberg.reporting.valuation_delivery import build_manifest
    from bellomberg.valuation.sector_analysis import valuation_results_block
    chat_tools, _, email_sender = contract_tools
    store = _publication_store(db, tmp_path, monkeypatch)
    _, current = _call(chat_tools)
    store.set_locked("SYNTH-EXT", True)
    _, candidate = _call(chat_tools)
    results = {"SYNTH-EXT": candidate}
    receipt = build_manifest(results, roots=[tmp_path])
    assert receipt["valuations"][0]["model_publication"]["status"] == "held"
    assert receipt["valuations"][0]["artifact_status"] == "available"
    assert '"status": "held"' in valuation_results_block(results)
    assert "held" in email_sender.corpo_valutazioni(results, receipt["attachments"], receipt)
    # A later round reuses the actual current revision, not the latest held candidate.
    reused = chat_tools.dispatch("get_valuation", {"ticker": "SYNTH-EXT"},
        prepared_bundle=current["acquisition_snapshot"], as_of=DAY)["data"]
    assert reused["generation_id"] == current["generation_id"]
    assert reused["model_publication"]["status"] == "current"


def test_documented_call_does_not_request_legacy_inputs(contract_tools):
    chat_tools, _, _ = contract_tools
    args, result = _call(chat_tools)
    assert result["valuation_usability"]["usable"], result
    note = result.get("_analyst_note", "")
    assert "growth_path" not in note and "CAGR" not in note, note


def test_documented_call_preserves_the_analyst_view_in_the_thesis(contract_tools, db):
    chat_tools, _, _ = contract_tools
    args, result = _call(chat_tools)
    assert result["valuation_usability"]["usable"], result
    saved = db.get_valuation_history("SYNTH-EXT", n=1)[0]
    for scenario, rationale in args["analysis_context"]["scenario_rationale"].items():
        assert scenario in saved["variant_view"] and rationale in saved["variant_view"], saved["variant_view"]


@pytest.mark.parametrize("extra", [{"variant_view": "Legacy estimate"}, {"growth_path": [.1, .1]},
                                   {"scenarios": {"base": {"revenue_growth": [.1, .1]}}},
                                   {"peers": ["SYNTH-PEER"]}])
def test_legacy_arguments_still_block_documented_numbers(contract_tools, extra):
    chat_tools, _, _ = contract_tools
    _, result = _call(chat_tools, extra=extra)
    assert not result["valuation_usability"]["usable"]
    assert result.get("fair_value_base") is None
    assert "Parametri legacy non consumati" in str(result["analytical_quality"])


def test_synthetic_dcf_reaches_sqlite_f17_workbook_sidecar_and_mime(contract_tools, db, tmp_path, monkeypatch):
    chat_tools, committee, email_sender = contract_tools
    from bellomberg.storage.valuation_jobs import ensure_schema as jobs_schema
    _publication_store(db, tmp_path, monkeypatch)
    with db._conn() as conn:
        jobs_schema(conn)
    tables = ("valuation_snapshots", "valuation_theses", "valuation_snapshot_links")

    def counts():
        with db._conn() as connection:
            return tuple(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in tables)

    before = counts()
    args, result = _call(chat_tools)
    assert result["valuation_usability"]["usable"], result
    assert counts() == tuple(value + 1 for value in before)
    assert result["fair_value_base"] == pytest.approx(14.13)
    assert len(result["input_consumption"]["consumed_records"]) == len(args["method_records"]) == 61
    assert result["acquisition_snapshot"]["case"]["sources"]["method_inputs"]["source_id"] == "explicit_method_records"
    workbook = Path(result["path"])
    assert workbook.parent == tmp_path
    sidecar = json.loads(workbook.with_suffix(".payload.json").read_text(encoding="utf-8"))
    assert sidecar["workbook_sha256"] == sha256(workbook.read_bytes()).hexdigest()
    for key in ("snapshot_id", "generation_id", "fair_value_base"):
        assert sidecar[key] == result[key]
    saved = db.get_valuation_snapshot(result["snapshot_id"], generation_id=result["generation_id"])
    assert saved["fair_value_base"] == result["fair_value_base"]
    model = endpoint(tmp_path, model_db=db)["models"][0]
    assert model["current_generation"] and model["valuation_usability"]["usable"], model
    assert model["fair_value"] == result["fair_value_base"]
    assert model["generation_id"] == result["generation_id"]
    attachments = committee._collect_dcf_files(datetime(1970, 1, 1))
    assert attachments == [str(workbook)]
    messages = []

    class LocalSMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def login(self, *args): pass
        def send_message(self, message): messages.append(message.as_bytes())

    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", LocalSMTP)
    body = email_sender.corpo_valutazioni({"SYNTH-EXT": result}, attachments)
    assert email_sender.invia_email_multi_allegati(attachments, oggetto="SYNTHETIC wiring proof", body_extra=body)
    assert len(messages) == 1
    mime = BytesParser(policy=policy.default).parsebytes(messages[0])
    attached = list(mime.iter_attachments())
    assert len(attached) == 1 and attached[0].get_filename() == workbook.name
    assert attached[0].get_payload(decode=True) == workbook.read_bytes()
    html = mime.get_body(preferencelist=("html",)).get_content()
    assert "SYNTH-EXT" in html and "14.13 EUR" in html and result["snapshot_id"][:12] in html
    assert counts() == tuple(value + 1 for value in before), "Read/export paths changed the isolated valuation DB"
    (tmp_path / "synthetic-dcf.eml").write_bytes(messages[0])
    (tmp_path / "synthetic-dcf-receipt.json").write_text(json.dumps({
        "scope": "Synthetic offline wiring proof; no real issuer or full committee run certified",
        "ticker": result["ticker"], "method": result["method"],
        "snapshot_id": result["snapshot_id"], "generation_id": result["generation_id"],
        "consumed_records": len(result["input_consumption"]["consumed_records"]),
        "fair_value_base": result["fair_value_base"], "f17_fair_value": model["fair_value"],
        "workbook": workbook.name, "workbook_sha256": sidecar["workbook_sha256"],
        "valuation_tables": list(tables), "counts_before": before, "counts_after": counts(),
        "mime_attachments": [part.get_filename() for part in attached],
    }, indent=2), encoding="utf-8")


def test_reused_generation_keeps_registration_and_is_deliverable(contract_tools, tmp_path):
    chat_tools, committee, _ = contract_tools
    _, first = _call(chat_tools)
    reused = chat_tools.dispatch("get_valuation", {"ticker": "SYNTH-EXT"},
        prepared_bundle=first["acquisition_snapshot"], as_of=DAY)["data"]
    assert reused["reused"] is True
    assert reused["generation_id"] == first["generation_id"]
    assert committee._collect_dcf_files(datetime(2100, 1, 1), {"SYNTH-EXT": reused}) == [first["path"]]
