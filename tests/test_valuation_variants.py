"""Personal edits survive official refreshes and interrupted copy recovery."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4
import pytest

from test_valuation_versions import setup, _generate, _publish


def variants(setup):
    from bellomberg.storage.valuation_variants import PersonalVariants, ensure_schema
    db, versions, directory = setup
    with db._conn() as conn:
        ensure_schema(conn)
    result = _generate(db, directory)
    _publish(versions, result)
    return PersonalVariants(versions, directory / "personal"), result


def test_personal_edits_survive_refresh_and_idempotent_copy_request(setup):
    db, versions, directory = setup
    store, model = variants(setup)
    ident = str(uuid4())
    copy = store.create(model["ticker"], "My own assumptions", request_id=ident)
    assert copy["status"] == "ready" and copy["modified"] is False
    personal = Path(copy["path"])
    original = Path(model["path"]).read_bytes()
    assert personal.read_bytes() == original
    personal.write_bytes(original + b"user owned edit")
    new = _generate(db, directory)
    _publish(versions, new, model["generation_id"])
    reread = store.create(model["ticker"], "My own assumptions", request_id=ident)
    assert reread["modified"] and reread["source_generation"] == model["generation_id"]
    assert personal.read_bytes().endswith(b"user owned edit")
    assert Path(model["path"]).read_bytes() == original
    with pytest.raises(ValueError, match="identity"):
        store.create(model["ticker"], "Different intent", request_id=ident)


def test_failed_save_is_visible_and_can_resume_without_new_variant(setup, monkeypatch):
    store, model = variants(setup)
    ident = str(uuid4())
    original_open = Path.open
    def denied(path, mode="r", *args, **kwargs):
        if path.parent == store.root and mode == "xb":
            raise PermissionError("synthetic locked directory")
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    pending = store.create(model["ticker"], "Personal", request_id=ident)
    assert pending["status"] == "pending" and not pending["available"]
    assert "PermissionError" in pending["error"]
    monkeypatch.setattr(Path, "open", original_open)
    ready = store.create(model["ticker"], "Personal", request_id=ident)
    assert ready["status"] == "ready" and ready["id"] == ident
    assert len(list(store.root.glob("*.xlsx"))) == 1


def test_partial_pending_copy_is_preserved_and_never_overwritten(setup, monkeypatch):
    store, model = variants(setup)
    original_open = Path.open
    class PartialWrite:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def write(self, raw):
            self.stream.write(raw[:25])
            self.stream.flush()
            raise OSError("synthetic interrupted write")
    def partial(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return PartialWrite(stream) if path.parent == store.root and mode == "xb" else stream
    monkeypatch.setattr(Path, "open", partial)
    ident = str(uuid4())
    pending = store.create(model["ticker"], "Partial", request_id=ident)
    raw = Path(pending["path"]).read_bytes()
    assert pending["status"] == "pending" and len(raw) == 25
    monkeypatch.setattr(Path, "open", original_open)
    blocked = store.create(model["ticker"], "Partial", request_id=ident)
    assert blocked["status"] == "blocked"
    assert Path(blocked["path"]).read_bytes() == raw


def test_concurrent_identical_request_returns_one_intact_variant(setup):
    store, model = variants(setup)
    ident = str(uuid4())
    start = Barrier(2)

    def create():
        start.wait(timeout=10)
        return store.create(model["ticker"], "Parallel", request_id=ident)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create)
        second = pool.submit(create)
        results = [first.result(timeout=15), second.result(timeout=15)]

    assert all(row["id"] == ident and row["status"] == "ready" for row in results)
    assert results[0]["path"] == results[1]["path"]
    assert Path(results[0]["path"]).read_bytes() == Path(model["path"]).read_bytes()
    assert len(list(store.root.glob("*.xlsx"))) == 1
    with store.versions._conn(read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM valuation_variants WHERE id=?", (ident,)).fetchone()[0] == 1


def test_new_request_does_not_adopt_preexisting_identical_personal_file(setup):
    store, model = variants(setup)
    ident = str(uuid4())
    target = store.root / ("PERSONAL_" + ident + ".xlsx")
    target.parent.mkdir(parents=True)
    original = Path(model["path"]).read_bytes()
    target.write_bytes(original)

    blocked = store.create(model["ticker"], "Personal", request_id=ident)
    assert blocked["status"] == "blocked" and not blocked["available"]
    assert target.read_bytes() == original
    repeated = store.create(model["ticker"], "Personal", request_id=ident)
    assert repeated["status"] == "blocked" and not repeated["available"]
    assert target.read_bytes() == original


def test_request_id_required_and_reservation_recovers_after_crash(setup, monkeypatch):
    store, model = variants(setup)
    with pytest.raises(TypeError, match="request_id"):
        store.create(model["ticker"], "Recovery")

    ident = str(uuid4())
    target = store.root / ("PERSONAL_" + ident + ".xlsx")
    original_open = Path.open

    class SyntheticCrash(BaseException):
        pass

    def crash_before_copy(path, mode="r", *args, **kwargs):
        if path == target and mode == "xb":
            raise SyntheticCrash("process stopped after reservation")
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", crash_before_copy)
        with pytest.raises(SyntheticCrash):
            store.create(model["ticker"], "Recovery", request_id=ident)

    with store.versions._conn(read_only=True) as conn:
        row = conn.execute("SELECT status FROM valuation_variants WHERE id=?", (ident,)).fetchone()
    assert row is not None and row["status"] == "pending"
    assert not target.exists()

    ready = store.create(model["ticker"], "Recovery", request_id=ident)
    assert ready["status"] == "ready" and ready["id"] == ident
    assert Path(ready["path"]).read_bytes() == Path(model["path"]).read_bytes()
