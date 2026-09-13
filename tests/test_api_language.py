"""Offline ASGI boundary: captured language, streams, bootstrap and preferences."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from bellomberg.core.language import current_language, language_context
from bellomberg.storage import preferences


@pytest.fixture
def saved(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", path)
    return path


async def invoke(app, path="/probe", headers=()):
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    await app({"type": "http", "asgi": {"version": "3.0"}, "method": "GET",
               "path": path, "headers": list(headers)}, receive, send)
    return messages


def middleware(app):
    from bellomberg.api.language_middleware import LanguageMiddleware
    return LanguageMiddleware(app)


async def probe(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": current_language().encode()})


@pytest.mark.parametrize("language", ["it", "en"])
def test_explicit_header_covers_response_and_resets_outer_context(saved, language):
    preferences.set_language_preference("it")
    with language_context("it"):
        result = asyncio.run(invoke(middleware(probe), headers=[(b"x-bb-language", language.encode())]))
        assert current_language() == "it"
    assert result[1]["body"] == language.encode()
    assert (b"content-language", language.encode()) in result[0]["headers"]


def test_missing_header_reads_saved_preference_once_for_entire_stream(saved):
    preferences.set_language_preference("en")
    async def stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": current_language().encode(), "more_body": True})
        preferences.set_language_preference("it")
        await asyncio.sleep(0)
        await send({"type": "http.response.body", "body": current_language().encode(), "more_body": False})
    result = asyncio.run(invoke(middleware(stream)))
    assert [row["body"] for row in result if "body" in row] == [b"en", b"en"]


def test_concurrent_requests_never_share_language(saved):
    async def run():
        both_entered = asyncio.Event()
        entered = []
        async def concurrent(scope, receive, send):
            entered.append(current_language())
            if len(entered) == 2:
                both_entered.set()
            await both_entered.wait()
            await probe(scope, receive, send)
        app = middleware(concurrent)
        return await asyncio.gather(invoke(app, headers=[(b"x-bb-language", b"en")]),
                                    invoke(app, headers=[(b"x-bb-language", b"it")]))
    result = asyncio.run(run())
    assert [row[1]["body"] for row in result] == [b"en", b"it"]


@pytest.mark.parametrize("headers", [[(b"x-bb-language", b"fr")], [(b"x-bb-language", b"")],
    [(b"x-bb-language", b"IT")], [(b"x-bb-language", b"it,en")],
    [(b"x-bb-language", b"it"), (b"x-bb-language", b"en")]])
def test_invalid_or_duplicate_header_refuses_before_app(saved, headers):
    async def forbidden(*_):
        raise AssertionError("invalid language reached application")
    result = asyncio.run(invoke(middleware(forbidden), headers=headers))
    assert result[0]["status"] == 400
    assert json.loads(result[1]["body"])["code"] == "unsupported_language"
    assert not saved.exists()


def test_corrupt_preference_requires_explicit_language_for_normal_requests(saved):
    saved.write_text("{broken", encoding="utf-8")
    before = saved.read_bytes()
    result = asyncio.run(invoke(middleware(probe)))
    assert result[0]["status"] == 503
    assert json.loads(result[1]["body"])["code"] == "language_preference_unavailable"
    explicit = asyncio.run(invoke(middleware(probe), headers=[(b"x-bb-language", b"en")]))
    assert explicit[0]["status"] == 200 and explicit[1]["body"] == b"en"
    assert saved.read_bytes() == before


@pytest.mark.parametrize("path", ["/health", "/auth/login", "/auth/status", "/preferences"])
def test_corrupt_preference_does_not_block_authentication_or_bootstrap(saved, path):
    saved.write_text("{broken", encoding="utf-8")
    result = asyncio.run(invoke(middleware(probe), path=path))
    assert result[0]["status"] == 200
    assert (b"x-bb-language-source", b"bootstrap_default") in result[0]["headers"]
    assert (b"x-bb-language-warning", b"preferences_unavailable") in result[0]["headers"]


def test_exception_in_stream_still_resets_language(saved):
    async def fail(scope, receive, send):
        assert current_language() == "en"
        raise RuntimeError("synthetic")
    with language_context("it"):
        with pytest.raises(RuntimeError):
            asyncio.run(invoke(middleware(fail), headers=[(b"x-bb-language", b"en")]))
        assert current_language() == "it"


def preference_app():
    from bellomberg.api.language_routes import create_language_router
    app = FastAPI()
    def require_session(request: Request):
        if request.headers.get("x-bb-token") != "synthetic":
            raise HTTPException(401, "auth required")
    app.include_router(create_language_router(require_session))
    return middleware(app)


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_preferences_require_auth_even_without_null_origin(saved, method):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=preference_app()), base_url="http://test") as client:
            return await client.request(method, "/preferences", json={"language": "en"} if method == "PUT" else None)
    result = asyncio.run(run())
    assert result.status_code == 401
    assert not saved.exists()


def test_preferences_first_choice_roundtrip_and_extra_fields_refused(saved):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=preference_app()), base_url="http://test",
                                     headers={"x-bb-token": "synthetic", "x-bb-language": "en"}) as client:
            initial = await client.get("/preferences")
            changed = await client.put("/preferences", json={"language": "en"})
            readback = await client.get("/preferences")
            rejected = await client.put("/preferences", json={"language": "it", "models": "changed"})
            return initial, changed, readback, rejected
    initial, changed, readback, rejected = asyncio.run(run())
    assert initial.json() == {"language": "it", "selected": False, "source": "compatibility_default"}
    assert changed.json() == readback.json() == {"language": "en", "selected": True, "source": "preferences"}
    assert rejected.status_code == 422
    assert json.loads(saved.read_text(encoding="utf-8"))["language"] == "en"


def test_preferences_repair_requires_the_exact_observed_fingerprint(saved):
    saved.write_bytes(b"{broken")
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=preference_app()), base_url="http://test",
                                     headers={"x-bb-token": "synthetic", "x-bb-language": "en"}) as client:
            error = await client.get("/preferences")
            assert error.status_code == 503
            detail = error.json()["detail"]
            assert detail["code"] == "language_preference_unavailable"
            fingerprint = detail["fingerprint"]
            refused = await client.put("/preferences", json={"language": "en", "repair_fingerprint": "0" * 64})
            assert refused.status_code == 409
            return await client.put("/preferences", json={"language": "en", "repair_fingerprint": fingerprint})
    repaired = asyncio.run(run())
    assert repaired.status_code == 200
    assert repaired.json()["backup_created"] is True
    assert "backup_path" not in repaired.json()


def test_real_api_cors_allows_language_and_preferences_router_is_installed(saved, monkeypatch):
    from bellomberg.api import bellomberg_api as api
    monkeypatch.setitem(api._SESSIONS, "synthetic-language-test", 10**15)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://127.0.0.1") as client:
            preflight = await client.options("/preferences", headers={"Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "x-bb-token,x-bb-language,content-type"})
            response = await client.get("/preferences", headers={"X-BB-Language": "en", "X-BB-Token": "synthetic-language-test"})
            return preflight, response
    preflight, response = asyncio.run(run())
    assert preflight.status_code == 200
    assert "x-bb-language" in preflight.headers["access-control-allow-headers"].lower()
    assert response.status_code == 200
    assert response.headers["content-language"] == "en"
    assert response.json()["selected"] is False


def test_real_cors_exposes_language_failure_to_the_desktop(saved):
    from bellomberg.api import bellomberg_api as api
    saved.write_text("{broken", encoding="utf-8")
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://127.0.0.1") as client:
            return await client.get("/portfolio", headers={"Origin": "http://localhost:5173"})
    result = asyncio.run(run())
    assert result.status_code == 503
    assert result.json()["code"] == "language_preference_unavailable"
    assert result.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.parametrize("language,expected", [("it", "login richiesto"), ("en", "login required")])
def test_real_auth_error_is_generated_in_selected_language(saved, language, expected):
    from bellomberg.api import bellomberg_api as api
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://127.0.0.1") as client:
            return await client.get("/preferences", headers={"X-BB-Language": language})
    result = asyncio.run(run())
    assert result.status_code == 401
    assert expected in result.json()["detail"]


@pytest.mark.parametrize("language,expected", [("it", "ticker non valido"), ("en", "Invalid ticker")])
def test_real_validation_error_does_not_need_network_or_db(saved, language, expected):
    from bellomberg.api import bellomberg_api as api
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://127.0.0.1") as client:
            return await client.get("/market/ohlc", params={"ticker": "X" * 17}, headers={"X-BB-Language": language})
    result = asyncio.run(run())
    assert result.status_code == 400
    assert result.json()["detail"] == expected


@pytest.mark.parametrize("language,prefix", [("it", "GUARDIA"), ("en", "GUARD")])
@pytest.mark.parametrize("code,amount", [("cash_threshold", 100001), ("cash_duplicate", 100)])
def test_cash_guards_have_stable_codes_in_both_languages_without_mutation(saved, tmp_path, monkeypatch,
                                                                         language, prefix, code, amount):
    from bellomberg.api import bellomberg_api as api
    from bellomberg.storage import memory_db
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    db = memory_db.MemoryDB(str(tmp_path / "cash.db"), str(tmp_path / "chroma"))
    db.apply_cash_movement("DEPOSIT", 100, data="2001-01-01")
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setitem(api._SESSIONS, "synthetic-cash-language", 10**15)
    with db._conn() as conn:
        before = tuple(conn.execute("SELECT balance_cents FROM cash_state").fetchone())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://127.0.0.1") as client:
            return await client.post("/cash/movement", headers={"X-BB-Language": language,
                "X-BB-Token": "synthetic-cash-language"}, json={"tipo": "DEPOSIT", "importo_eur": amount, "data": "2001-01-01"})
    result = asyncio.run(run())
    assert result.status_code == 422
    assert result.json()["code"] == code
    assert prefix in result.json()["detail"]
    with db._conn() as conn:
        assert tuple(conn.execute("SELECT balance_cents FROM cash_state").fetchone()) == before
        assert conn.execute("SELECT COUNT(*) FROM cash_movements").fetchone()[0] == 1
