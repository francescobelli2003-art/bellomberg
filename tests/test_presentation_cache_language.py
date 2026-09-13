"""Bilingual authored cache text, with an explicit and pure read boundary."""
from copy import deepcopy

import pytest

from bellomberg.core.language import language_context


def test_roundtrip_preserves_cache_numbers_time_codes_and_original_strings(monkeypatch):
    from bellomberg.core.presentation import message, render_payload
    with language_context("it"):
        cache = {"note": message("Copertura di {n} giorni", "Coverage of {n} days", n=12),
                 "status": "non_disponibile", "source_original": "Documento originale italiano",
                 "timestamp": "2001-01-01T00:00:00Z", "ttl": 600, "numbers": [1.25, None, -2],
                 "nested": [{"quote": "Testo italiano dell'utente"}]}
    before = deepcopy(cache)
    calls = []
    def forbidden_fetch(*_args, **_kwargs):
        calls.append("fetch")
        raise AssertionError("Presentation cannot fetch")
    monkeypatch.setattr("socket.socket.connect", forbidden_fetch)
    monkeypatch.setattr("urllib.request.urlopen", forbidden_fetch)
    for language, expected in [("it", "Copertura di 12 giorni"), ("en", "Coverage of 12 days"),
                               ("it", "Copertura di 12 giorni")]:
        result = render_payload(cache, language=language)
        assert result["note"] == expected
        assert result["status"] == "non_disponibile"
        assert result["source_original"] == "Documento originale italiano"
        for key in ("timestamp", "ttl", "numbers", "nested"):
            assert result[key] == before[key]
        result["numbers"].append(9)
        result["nested"][0]["quote"] = "Mutated caller copy"
        assert cache == before
    assert calls == []


def test_nested_authored_parameters_follow_reader_original_parameters_do_not():
    from bellomberg.core.presentation import message, render_payload
    with language_context("it"):
        cache = {"note": message("{kind}: {amount:.2f} ({original})",
                                 "{kind}: {amount:.2f} ({original})",
                                 kind=message("Saldo", "Balance"), amount=12.5,
                                 original="nota privata originale")}
    with language_context("en"):
        result = render_payload(cache)
    assert result["note"] == "Balance: 12.50 (nota privata originale)"
    assert cache["note"] == "Saldo: 12.50 (nota privata originale)"


def test_rendered_payload_can_enter_another_cache_without_losing_authored_pairs():
    from bellomberg.core.presentation import message, render_payload
    import json
    with language_context("it"):
        inner = {"note": message("Dato assente", "Missing data")}
    outer = {"child": render_payload(inner, language="en")}
    italian = render_payload(outer, language="it")
    assert italian["child"]["note"] == "Dato assente"
    assert json.loads(json.dumps(italian)) == {"child": {"note": "Dato assente"}}
    assert outer["child"]["note"] == "Missing data"


def test_plain_keys_and_values_are_never_matched_as_translation_candidates():
    from bellomberg.core.presentation import render_payload
    payload = {"saldo noto": ["Saldo noto", "Missing data", "error", "unknown", "it"]}
    assert render_payload(payload, language="en") == payload


def test_invalid_language_and_missing_format_parameter_are_explicit_errors():
    from bellomberg.core.presentation import message, render_payload
    with pytest.raises(ValueError):
        render_payload({}, language="fr")
    with language_context("en"), pytest.raises((KeyError, ValueError)):
        message("Dato {n} {missing}", "Data {n} {missing}", n=1)


def test_literal_formula_braces_are_not_interpreted_without_parameters():
    from bellomberg.core.presentation import message, render_payload
    with language_context("it"):
        payload = {"method": message("V_{t-1} invariato", "V_{t-1} unchanged")}
    assert render_payload(payload, language="en")["method"] == "V_{t-1} unchanged"


def test_join_and_authored_exception_keep_variants_without_matching_external_text():
    from bellomberg.core.presentation import message, render_payload, join_messages, error_text
    with language_context("it"):
        joined = join_messages("; ", [message("Dato assente", "Missing data"), "Dato originale"])
        error = ValueError(message("Prezzo assente", "Missing price"))
        payload = {"joined": joined, "error": error_text(error), "external": error_text(ValueError("Prezzo originale"))}
    english = render_payload(payload, language="en")
    assert english == {"joined": "Missing data; Dato originale", "error": "Missing price", "external": "Prezzo originale"}
    assert payload["joined"] == "Dato assente; Dato originale"
