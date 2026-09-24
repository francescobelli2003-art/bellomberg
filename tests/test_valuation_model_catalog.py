"""F17 reads an explicit current model, never a newer attempt or copied sidecar."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from test_sector_valuation_api import endpoint
from test_valuation_automation_routes import environment, _finished_job, _generated


def _model(listing, ticker="SYNTH-EXT"):
    return next(row for row in listing["models"] if row["ticker"] == ticker)


def _linked_thesis(db, payload, *, when, view):
    with db._conn() as conn:
        row = conn.execute("""SELECT thesis_id FROM valuation_snapshot_links
            WHERE snapshot_id=? AND generation_id=? AND thesis_id IS NOT NULL""",
            (payload["snapshot_id"], payload["generation_id"])).fetchone()
        assert row is not None
        conn.execute("UPDATE valuation_theses SET date=?, variant_view=? WHERE id=?",
                     (when, view, row[0]))


def test_queued_and_failed_preparation_without_snapshot_remains_visible(environment):
    db, _, jobs, root, _ = environment
    queued = jobs.enqueue("SYNTH-EXT", "prepare", "synthetic-new-listing", {"source": "synthetic"})
    shown = _model(endpoint(root, model_db=db))
    assert shown["automation"]["latest_prepare_job"]["id"] == queued["id"]
    assert shown["automation"]["latest_prepare_job"]["status"] == "queued"
    assert not shown["file"] and not shown.get("generation_id") and shown["generated_at"] is None
    assert shown["fair_value"] is None and shown["valuation_usability"]["usable"] is False
    claimed = jobs.claim("synthetic-worker", lease_seconds=60)
    jobs.finish(claimed["id"], claimed["lease_owner"], claimed["lease_token"],
                status="failed", reason="Synthetic source missing")
    shown = _model(endpoint(root, model_db=db))
    assert shown["automation"]["latest_prepare_job"]["reason"] == "Synthetic source missing"
    assert not shown.get("current_download") and shown["current_generation"] is False


def test_migrated_snapshot_without_publication_is_not_current(environment):
    db, _, _, root, _ = environment
    candidate = _generated(db, root)
    listing = endpoint(root, model_db=db)
    model = _model(listing)
    assert listing["automation_status"] == "available"
    assert model["generation_id"] == candidate["generation_id"]
    assert model["automation"]["status"] == "no_current"
    assert model["current_generation"] is False and model["canonical"] is False
    assert model["fair_value"] is None and model["valuation_usability"]["usable"] is False
    assert not model.get("current_download")


def test_unavailable_catalog_cannot_claim_a_current_generation(environment, monkeypatch):
    from bellomberg.api import valuation_automation_routes as routes
    db, versions, _, root, _ = environment
    published = _generated(db, root)
    assert versions.publish(published["snapshot_id"], published["generation_id"],
                            expected_current_generation=None)["status"] == "published"

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("synthetic catalog failure")

    monkeypatch.setattr(routes, "model_catalog_state", unavailable)
    listing = endpoint(root, model_db=db)
    model = _model(listing)
    assert listing["automation_status"] == "unavailable"
    assert "Registro versioni correnti non disponibile" in listing["nota"]
    assert model["automation"]["status"] == "unavailable"
    assert model["current_generation"] is False and not model.get("current_download")


def test_current_keeps_its_linked_thesis_after_held_refresh(environment):
    db, versions, _, root, _ = environment
    current = _generated(db, root)
    _linked_thesis(db, current, when="2026-09-10T10:00:00", view="Current synthetic thesis")
    assert versions.publish(current["snapshot_id"], current["generation_id"],
                            expected_current_generation=None)["status"] == "published"
    versions.set_locked(current["ticker"], True)
    refresh = _generated(db, root)
    _linked_thesis(db, refresh, when="2026-09-11T10:00:00", view="Held synthetic thesis")
    assert versions.publish(refresh["snapshot_id"], refresh["generation_id"],
                            expected_current_generation=current["generation_id"])["status"] == "held"

    listing = endpoint(root, model_db=db)
    shown = _model(listing)
    assert shown["current_generation"] is True
    assert shown["generation_id"] == current["generation_id"]
    assert shown["thesis_date"] == "2026-09-10T10:00:00"
    assert shown["variant_view"] == (current.get("variant_view") or "Current synthetic thesis")
    assert shown["price_at_thesis"] == current["price"]
    assert shown["automation"]["latest_publication_attempt"]["status"] == "held"


def test_copied_sidecar_with_same_ids_cannot_replace_current_fields(environment):
    db, versions, _, root, _ = environment
    current = _generated(db, root)
    _linked_thesis(db, current, when="2026-09-10T10:00:00", view="Current synthetic thesis")
    assert versions.publish(current["snapshot_id"], current["generation_id"],
                            expected_current_generation=None)["status"] == "published"
    original = Path(current["path"])
    original_sidecar = original.with_suffix(".payload.json")
    original_metadata = json.loads(original_sidecar.read_text(encoding="utf-8"))
    original_metadata["variant_view"] = "Altered original sidecar narrative"
    original_metadata["fair_value_base"] = current["fair_value_base"] + 1000
    original_sidecar.write_text(json.dumps(original_metadata), encoding="utf-8")

    alternatives = (root / "VAL_SYNTH-EXT.xlsx",
                    root / f"VAL_SYNTH-EXT_{current['generation_id']}.xlsx")
    copied = next(path for path in alternatives if path != original)
    copied.write_bytes(original.read_bytes())
    copy_metadata = deepcopy(original_metadata)
    copy_metadata["variant_view"] = "Copied sidecar narrative"
    copy_metadata["workbook_sha256"] = sha256(copied.read_bytes()).hexdigest()
    copied.with_suffix(".payload.json").write_text(json.dumps(copy_metadata), encoding="utf-8")

    listing = endpoint(root, model_db=db)
    current_rows = [row for row in listing["models"] if row.get("current_generation")]
    assert len(current_rows) == 1
    shown = current_rows[0]
    assert shown["file"] == original.name and shown["generation_id"] == current["generation_id"]
    assert shown["fair_value"] == current["fair_value_base"]
    assert shown["variant_view"] == (current.get("variant_view") or "Current synthetic thesis")
    assert shown["current_download"].endswith("/" + current["generation_id"] + "/workbook")
    duplicate = next(row for row in listing["models"] if row["file"] == copied.name)
    assert duplicate["current_generation"] is False and duplicate["canonical"] is False
    assert duplicate["fair_value"] is None and not duplicate.get("current_download")


def test_latest_reprice_success_then_failure_controls_displayed_quote(environment):
    from bellomberg.valuation.market_quote import CONTRACT, FRESHNESS_POLICY
    db, versions, jobs, root, _ = environment
    current = _generated(db, root)
    assert versions.publish(current["snapshot_id"], current["generation_id"],
                            expected_current_generation=None)["status"] == "published"
    request = {"generation_id": current["generation_id"], "as_of": date.today().isoformat()}
    observed = {"contract": CONTRACT, "freshness_policy": FRESHNESS_POLICY,
                "status": "ok", "observed_local_date": date.today().isoformat(),
                "price": 25.0, "upside_base_pct": 8.5}
    _finished_job(jobs, current["ticker"], "reprice", "synthetic-quote-ok",
                  request, "succeeded", {"market_quote": observed})
    first = _model(endpoint(root, model_db=db))
    assert first["current_generation"] is True
    assert first["automation"]["latest_price_job"]["status"] == "succeeded"
    assert first["market_quote"]["status_at_read"] == "ok"
    assert first["market_quote"]["price"] == 25.0 and first["upside_today_pct"] == 8.5

    _finished_job(jobs, current["ticker"], "reprice", "synthetic-quote-failed",
                  request, "failed")
    second = _model(endpoint(root, model_db=db))
    assert second["current_generation"] is True
    assert second["automation"]["latest_price_job"]["status"] == "failed"
    assert second["market_quote"]["status_at_read"] != "ok"
    assert second["market_quote"].get("price") is None and second["upside_today_pct"] is None
    assert second["price_at_thesis"] == current["price"]  # Model date remains separate.
