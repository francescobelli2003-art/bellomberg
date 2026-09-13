"""Persistent presentation preference; no portfolio or historical content writes."""
import json

import pytest


@pytest.fixture
def preferences(tmp_path, monkeypatch):
    from bellomberg.storage import preferences
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")
    return preferences


def test_missing_preference_is_declared_and_read_does_not_create_file(preferences):
    assert preferences.read_preferences() == {
        "language": "it", "selected": False, "source": "compatibility_default"}
    assert preferences.get_language_preference() == "it"
    assert not preferences.PREFERENCES_PATH.exists()


@pytest.mark.parametrize("language", ["it", "en"])
def test_choice_is_persisted_and_read_back(preferences, language):
    result = preferences.set_language_preference(language)
    assert result == {"language": language, "selected": True, "source": "preferences"}
    assert json.loads(preferences.PREFERENCES_PATH.read_text())["language"] == language
    assert preferences.read_preferences() == result


@pytest.mark.parametrize("raw", ['{', 'null', '[]', '{}', '{"language":"fr"}', '{"language":null}'])
def test_broken_preference_never_becomes_an_italian_fallback(preferences, raw):
    preferences.PREFERENCES_PATH.write_text(raw)
    with pytest.raises(preferences.PreferenceError):
        preferences.read_preferences()
    with pytest.raises(preferences.PreferenceError):
        preferences.set_language_preference("en")
    assert preferences.PREFERENCES_PATH.read_text() == raw


@pytest.mark.parametrize("value", [None, "", "EN", "fr", True, 1])
def test_unsupported_choice_has_no_write(preferences, value):
    with pytest.raises(ValueError):
        preferences.set_language_preference(value)
    assert not preferences.PREFERENCES_PATH.exists()


def test_save_preserves_unrelated_preferences_and_runtime_files(preferences):
    preferences.PREFERENCES_PATH.write_text(json.dumps({"language": "it", "other": {"enabled": True}}))
    historical = preferences.PREFERENCES_PATH.with_name("historical-memo.md")
    historical.write_text("Original user text. No automatic translation.")
    before = historical.read_bytes()
    preferences.set_language_preference("en")
    assert json.loads(preferences.PREFERENCES_PATH.read_text())["other"] == {"enabled": True}
    assert historical.read_bytes() == before


def test_atomic_replace_failure_keeps_original_and_removes_temporary_file(preferences, monkeypatch):
    preferences.set_language_preference("it")
    before = preferences.PREFERENCES_PATH.read_bytes()

    def fail(*args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(preferences.os, "replace", fail)
    with pytest.raises(preferences.PreferenceError, match="save"):
        preferences.set_language_preference("en")
    assert preferences.PREFERENCES_PATH.read_bytes() == before
    assert list(preferences.PREFERENCES_PATH.parent.iterdir()) == [preferences.PREFERENCES_PATH]


def test_read_io_error_is_declared(preferences, monkeypatch):
    preferences.PREFERENCES_PATH.mkdir()
    with pytest.raises(preferences.PreferenceError, match="read"):
        preferences.get_language_preference()


def test_explicit_repair_backs_up_original_bytes_before_new_choice(preferences):
    import hashlib
    original = b'{bad json\r\n\xff'
    preferences.PREFERENCES_PATH.write_bytes(original)
    with pytest.raises(preferences.PreferenceError) as caught:
        preferences.read_preferences()
    fingerprint = caught.value.fingerprint
    assert fingerprint == hashlib.sha256(original).hexdigest()
    result = preferences.set_language_preference("en", repair_fingerprint=fingerprint)
    assert result["backup_created"] is True
    assert "backup_path" not in result
    backups = list(preferences.PREFERENCES_PATH.parent.glob("preferences-corrupt-*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert preferences.read_preferences()["language"] == "en"


def test_repair_fingerprint_drift_refuses_without_writes(preferences):
    preferences.PREFERENCES_PATH.write_bytes(b"{bad")
    with pytest.raises(preferences.PreferenceError) as caught:
        preferences.read_preferences()
    preferences.PREFERENCES_PATH.write_bytes(b"{different")
    before = {p.name: p.read_bytes() for p in preferences.PREFERENCES_PATH.parent.iterdir()}
    with pytest.raises(preferences.PreferenceConflict):
        preferences.set_language_preference("en", repair_fingerprint=caught.value.fingerprint)
    assert {p.name: p.read_bytes() for p in preferences.PREFERENCES_PATH.parent.iterdir()} == before


def test_repair_backup_failure_preserves_original(preferences, monkeypatch):
    preferences.PREFERENCES_PATH.write_bytes(b"{bad")
    with pytest.raises(preferences.PreferenceError) as caught:
        preferences.read_preferences()
    def fail(*_args, **_kwargs):
        raise OSError("synthetic backup error")
    monkeypatch.setattr(preferences, "_backup_corrupt", fail)
    with pytest.raises(preferences.PreferenceError):
        preferences.set_language_preference("en", repair_fingerprint=caught.value.fingerprint)
    assert preferences.PREFERENCES_PATH.read_bytes() == b"{bad"


def test_repair_token_is_rejected_for_valid_preferences(preferences):
    import hashlib
    preferences.set_language_preference("it")
    before = preferences.PREFERENCES_PATH.read_bytes()
    with pytest.raises(preferences.PreferenceConflict):
        preferences.set_language_preference("en", repair_fingerprint=hashlib.sha256(before).hexdigest())
    assert preferences.PREFERENCES_PATH.read_bytes() == before


def test_repair_detects_change_during_backup(preferences, monkeypatch):
    preferences.PREFERENCES_PATH.write_bytes(b"{bad")
    with pytest.raises(preferences.PreferenceError) as caught:
        preferences.read_preferences()
    original_backup = preferences._backup_corrupt
    def concurrent_change(raw, fingerprint):
        original_backup(raw, fingerprint)
        preferences.PREFERENCES_PATH.write_bytes(b"{changed-during-backup")
    monkeypatch.setattr(preferences, "_backup_corrupt", concurrent_change)
    with pytest.raises(preferences.PreferenceConflict):
        preferences.set_language_preference("en", repair_fingerprint=caught.value.fingerprint)
    assert preferences.PREFERENCES_PATH.read_bytes() == b"{changed-during-backup"
    assert not list(preferences.PREFERENCES_PATH.parent.glob(".preferences-*.tmp"))
