"""Real JSON serialization retains only explicitly authored language variants."""
import json
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bellomberg.core.language import language_context
from bellomberg.core.presentation import message


def test_real_json_response_preserves_authored_pairs_without_translating_source_or_numbers():
    from bellomberg.core.api_presentation import PresentationJSONResponse
    with language_context("it"):
        payload = {"measure": 1234.5, "source_text": "Testo fonte originale", "notes": [message("Serie incompleta", "Incomplete series")],
                   "held": {"SYNTH": {"note": message("{n} osservazioni", "{n} observations", n=12)}},
                   "stable_code": "INSUFFICIENT_HISTORY"}
    before = deepcopy(payload)
    app = FastAPI(default_response_class=PresentationJSONResponse)
    @app.get("/synthetic")
    def synthetic():
        return payload
    with TestClient(app) as client:
        received = client.get("/synthetic").json()
    assert received["measure"] == 1234.5 and received["source_text"] == "Testo fonte originale"
    assert received["stable_code"] == "INSUFFICIENT_HISTORY"
    assert received["notes"] == ["Serie incompleta"]
    assert received["_presentation_v1"] == {
        "version": 1, "texts": [
            {"path": ["notes", 0], "it": "Serie incompleta", "en": "Incomplete series"},
            {"path": ["held", "SYNTH", "note"], "it": "12 osservazioni", "en": "12 observations"}]}
    assert payload == before and "_presentation_v1" not in payload


def test_plain_source_and_historical_documents_have_no_presentation_metadata():
    from bellomberg.core.api_presentation import PresentationJSONResponse
    payload = {"memo": "Testo storico originale", "notes": ["Appunto personale"], "values": [0, None, False]}
    assert json.loads(PresentationJSONResponse(payload).body) == payload


def test_backend_default_response_class_uses_the_serialization_boundary():
    from bellomberg.api.bellomberg_api import app
    from bellomberg.core.api_presentation import PresentationJSONResponse
    assert app.router.default_response_class is PresentationJSONResponse
