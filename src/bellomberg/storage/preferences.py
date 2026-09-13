"""Small runtime preferences, separate from portfolio and historical research.

An absent choice is declared to the first-run UI. Invalid saved content is an
error, never an implicit switch to another language.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import tempfile
import threading

from bellomberg.core.paths import DATA_DIR

PREFERENCES_PATH = DATA_DIR / "preferences.json"
_LOCK = threading.RLock()


class PreferenceError(RuntimeError):
    """A saved preference cannot be read or reliably written."""
    def __init__(self, message, *, fingerprint=None):
        super().__init__(message)
        self.fingerprint = fingerprint


class PreferenceConflict(PreferenceError):
    """The explicit repair no longer refers to the observed corrupt file."""


def _valid_language(value: object) -> str:
    if not isinstance(value, str) or value not in ("it", "en"):
        raise ValueError("language must be 'it' or 'en'")
    return value


def _read_document() -> dict | None:
    try:
        raw = PREFERENCES_PATH.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PreferenceError("Cannot read saved preferences") from exc
    fingerprint = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(raw.decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError("preferences must be an object")
        _valid_language(document.get("language"))
    except ValueError as exc:
        raise PreferenceError("Cannot read saved language preference", fingerprint=fingerprint) from exc
    return document


def read_preferences() -> dict:
    with _LOCK:
        document = _read_document()
        if document is None:
            return {"language": "it", "selected": False, "source": "compatibility_default"}
        return {"language": document["language"], "selected": True, "source": "preferences"}


def get_language_preference() -> str:
    return read_preferences()["language"]


def _backup_corrupt(raw: bytes, fingerprint: str) -> None:
    backup = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=PREFERENCES_PATH.parent,
                                         prefix="preferences-corrupt-", suffix=".bak", delete=False) as stream:
            backup = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if backup.read_bytes() != raw or hashlib.sha256(backup.read_bytes()).hexdigest() != fingerprint:
            raise OSError("Cannot verify corrupt preferences backup")
    except Exception:
        if backup is not None:
            backup.unlink(missing_ok=True)
        raise


def set_language_preference(language: str, *, repair_fingerprint: str | None = None) -> dict:
    language = _valid_language(language)
    with _LOCK:
        repair_raw = None
        try:
            document = _read_document()
        except PreferenceError as exc:
            if repair_fingerprint is None or exc.fingerprint is None:
                raise
            if repair_fingerprint != exc.fingerprint:
                raise PreferenceConflict("Preferences changed: read before repairing") from exc
            try:
                repair_raw = PREFERENCES_PATH.read_bytes()
                if hashlib.sha256(repair_raw).hexdigest() != repair_fingerprint:
                    raise PreferenceConflict("Preferences changed before backup")
                _backup_corrupt(repair_raw, repair_fingerprint)
            except OSError as backup_error:
                raise PreferenceError("Cannot save preferences: backup failed") from backup_error
            document = {}
        else:
            if repair_fingerprint is not None:
                raise PreferenceConflict("Explicit repair requires the observed corrupt preferences")
        if document is None:
            document = {}
        document["language"] = language
        temporary: Path | None = None
        try:
            PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=PREFERENCES_PATH.parent,
                prefix=".preferences-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if repair_raw is not None and PREFERENCES_PATH.read_bytes() != repair_raw:
                raise PreferenceConflict("Preferences changed after backup: repair cancelled")
            os.replace(temporary, PREFERENCES_PATH)
            temporary = None
            result = read_preferences()
            if result["language"] != language or not result["selected"]:
                raise PreferenceError("Cannot verify saved language preference")
            return {**result, "backup_created": True} if repair_raw is not None else result
        except OSError as exc:
            raise PreferenceError("Cannot save language preference") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
