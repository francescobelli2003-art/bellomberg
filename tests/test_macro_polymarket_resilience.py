"""Polymarket failures stay tool failures; Macro still completes its SSE reply.

Synthetic provider transport and storage only. No paid calls or production DB.
"""
import asyncio
import json
import time
from types import SimpleNamespace as NS

import httpx
import pytest
import requests


@pytest.fixture
def poly(monkeypatch):
    from bellomberg.agents import agent_tools as at
    monkeypatch.setattr(at, "_POLY_CACHE", {})
    monkeypatch.setattr(at, "_POLY_BLOCKED", None)
    monkeypatch.setattr(at, "_POLY_BLOCKED_AT", 0.0, raising=False)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    return at


def _reply(value, status=200):
    return NS(status_code=status, json=lambda: value)


def _fault(kind):
    def get(*args, **kwargs):
        if kind == "timeout":
            raise requests.Timeout("synthetic timeout")
        if kind == "http":
            return _reply(None, 503)
        if kind == "invalid_json":
            def invalid():
                raise ValueError("synthetic invalid JSON")
            return NS(status_code=200, json=invalid)
        if kind == "invalid_shape":
            return _reply({"unexpected": "provider payload"})
        if kind == "null_json":
            return _reply(None)
        if kind == "exception":
            raise RuntimeError("synthetic transport exception")
        if kind == "tls":
            raise requests.exceptions.SSLError("hostname mismatch")
        raise AssertionError(kind)
    return get


@pytest.mark.parametrize("kind,cause", [
    ("timeout", "Timeout"), ("http", "HTTP 503"),
    ("invalid_json", "JSON"), ("invalid_shape", "JSON"),
    ("null_json", "JSON"), ("exception", "RuntimeError"),
    ("tls", "hostname mismatch"),
])
def test_provider_failure_is_explicit_not_a_successful_empty_search(poly, monkeypatch, kind, cause):
    monkeypatch.setattr(poly._req, "get", _fault(kind))
    result = poly.tool_get_polymarket_events("fed")
    assert result["results"] == []
    assert cause in result["error"]
    assert result["status"] == "unavailable"
    assert result["fetch_warnings"]


def test_malformed_event_is_declared_while_valid_other_source_is_kept(poly, monkeypatch):
    def get(url, **kwargs):
        if url.endswith("public-search"):
            return _reply({"events": ["invalid event"]})
        if url.endswith("events"):
            return _reply([{"title": "Fed meeting", "slug": "fed-meeting", "markets": []}])
        return _reply([])
    monkeypatch.setattr(poly._req, "get", get)
    result = poly.tool_get_polymarket_events("fed")
    assert len(result["results"]) == 1
    assert result["status"] == "partial"
    assert "/public-search" in result["fetch_warnings"][0]


def test_parser_exception_is_not_swallowed(poly, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("synthetic parser failure")
    monkeypatch.setattr(poly, "_poly_fetch", explode)
    result = poly.tool_get_polymarket_events("fed")
    assert "RuntimeError" in result["error"]
    assert len(result["fetch_warnings"]) == 3


def test_empty_healthy_search_remains_a_valid_empty_search(poly, monkeypatch):
    monkeypatch.setattr(poly._req, "get", lambda url, **kw:
                        _reply({"events": []} if url.endswith("public-search") else []))
    result = poly.tool_get_polymarket_events("fed")
    assert result["results"] == []
    assert "error" not in result
    assert "fetch_warnings" not in result


@pytest.mark.parametrize("events", [None, []])
def test_documented_nullable_events_are_valid(poly, monkeypatch, events):
    monkeypatch.setattr(poly._req, "get", lambda url, **kw:
                        _reply({"events": events} if url.endswith("public-search") else []))
    result = poly.tool_get_polymarket_events("fed")
    assert "error" not in result
    assert "fetch_warnings" not in result


@pytest.mark.parametrize("source", ["public-search", "events", "markets"])
def test_invalid_nested_prices_are_declared_missing(poly, monkeypatch, source):
    market = {"question": "Fed meeting?", "slug": "fed", "outcomes": '["Yes","No"]',
              "outcomePrices": "invalid JSON"}
    event = {"title": "Fed meeting", "slug": "fed", "markets": [market]}
    def get(url, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        if endpoint == "public-search":
            return _reply({"events": [event] if source == endpoint else []})
        return _reply(([event] if endpoint == "events" else [market]) if source == endpoint else [])
    monkeypatch.setattr(poly._req, "get", get)
    result = poly.tool_get_polymarket_events("fed")
    assert result["status"] == "partial"
    assert "JSON" in result["fetch_warnings"][0]
    item = result["results"][0]
    row = item if source == "markets" else item["markets"][0]
    assert row["prices"] is None


def test_tls_check_is_not_bypassed_and_recovers_after_cache_expiry(poly, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("verify", True) is not False
        if len(calls) == 1:
            raise requests.exceptions.SSLError("hostname mismatch")
        return _reply({"events": []} if url.endswith("public-search") else [])
    monkeypatch.setattr(poly._req, "get", get)
    first = poly.tool_get_polymarket_events("fed")
    assert "hostname mismatch" in first["error"]
    assert "blocco del regolatore" not in first["error"]
    assert len(calls) == 1
    now[0] += poly._POLY_CACHE_TTL + 1
    second = poly.tool_get_polymarket_events("fed")
    assert "error" not in second
    assert len(calls) == 4


def _chunk(delta=None, finish=None):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}


@pytest.mark.parametrize("kind", ["healthy", "partial", "timeout", "http", "invalid_json", "invalid_shape", "exception", "tls", "dispatch_exception"])
def test_macro_finishes_stream_with_provider_status_and_cause(poly, monkeypatch, kind):
    from bellomberg.agents import chat_engine as ce
    from bellomberg.core import llm_client as lc
    if kind == "partial":
        monkeypatch.setattr(poly._req, "get", lambda url, **kw: _reply(
            {"events": [{"title": "Fed meeting", "slug": "fed", "markets": []}]})
            if url.endswith("public-search") else _reply(None, 503))
    elif kind == "healthy":
        monkeypatch.setattr(poly._req, "get", lambda url, **kw: _reply(
            {"events": [{"title": "Fed meeting", "slug": "fed", "markets": []}]}
            if url.endswith("public-search") else []))
    elif kind == "dispatch_exception":
        def explode(*args, **kwargs):
            raise RuntimeError("synthetic dispatch failure")
        monkeypatch.setattr(ce, "dispatch", explode)
    else:
        monkeypatch.setattr(poly._req, "get", _fault(kind))
    monkeypatch.setattr(ce, "OPENROUTER_API_KEY", "synthetic")
    monkeypatch.setattr(ce, "_save_message", lambda *a, **kw: None)
    monkeypatch.setattr(ce, "get_messages", lambda _: [{"role": "user", "content": "synthetic"}])
    monkeypatch.setattr(ce, "_leggi_mandato_chat", lambda: (None, "synthetic"))
    monkeypatch.setattr(ce, "_build_system", lambda *a, **kw: "synthetic system")
    monkeypatch.setattr(ce, "_log", lambda _: None)
    monkeypatch.setattr(ce, "_done_payload", lambda session_id, **kw: dict(session_id=session_id, **kw))
    requests_sent = []
    replies = iter([
        [_chunk({"tool_calls": [{"index": 0, "id": "poly", "function": {
            "name": "get_polymarket_events", "arguments": '{"query":"fed"}'}}]}, finish="tool_calls")],
        [_chunk({"content": "Synthetic final reply: provider status acknowledged."}, finish="stop")],
    ])
    def handle(request):
        requests_sent.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text="".join("data: " + json.dumps(row) + "\n\n" for row in next(replies)) + "data: [DONE]\n\n")
    client = lc.AsyncOpenRouterClient(api_key="synthetic", trasporto=httpx.MockTransport(handle))
    monkeypatch.setattr(ce, "AsyncOpenRouterClient", lambda: client)
    async def run():
        try:
            return [part async for part in ce.stream_chat(1, "macro", "synthetic")]
        finally:
            await client._http.aclose()
    wire = asyncio.run(run())
    events = [(part.split("\n")[0][7:], json.loads(part.split("data: ", 1)[1]))
              for part in wire if part.startswith("event:")]
    result = next(payload for name, payload in events if name == "tool_result")
    assert result["ok"] is (kind in ("healthy", "partial"))
    if kind == "partial":
        assert '"status": "partial"' in result["result_preview"]
        assert "HTTP 503" in result["result_preview"]
    elif kind != "healthy":
        assert "error" in result["result_preview"]
        assert "_source" in result["result_preview"]
        content = next(m["content"] for m in requests_sent[1]["messages"] if m["role"] == "tool")
        assert "error" in json.loads(content)
    assert not any(name == "error" for name, _ in events)
    assert events[-1][0] == "done"
    assert events[-1][1]["ok"] is True
    assert sum(name == "done" for name, _ in events) == 1
    assert len(requests_sent) == 2
