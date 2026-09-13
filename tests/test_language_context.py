"""Request isolation and immutable captures, with a synthetic preference reader."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
from types import SimpleNamespace

import pytest

from bellomberg.core import language


@pytest.fixture
def preference(monkeypatch):
    state = {"value": "it", "reads": 0}
    def read():
        state["reads"] += 1
        return state["value"]
    monkeypatch.setitem(sys.modules, "bellomberg.storage.preferences", SimpleNamespace(get_language_preference=read))
    return state


def test_capture_uses_durable_preference_and_freezes_until_scope_ends(preference):
    selected = language.capture_language()
    with language.language_context(selected):
        preference["value"] = "en"
        assert language.current_language() == "it"
        assert language.capture_language("en") == "en"
        assert preference["reads"] == 1
    assert language.current_language() == "en"


def test_nested_scope_restores_language_after_exception(preference):
    with language.language_context("en"):
        with pytest.raises(RuntimeError), language.language_context("it"):
            raise RuntimeError("synthetic")
        assert language.current_language() == "en"
    assert language.current_language() == "it"


@pytest.mark.parametrize("value", [None, "", "fr", "EN", "en-GB", 1])
def test_unsupported_language_is_not_silently_replaced(value):
    with pytest.raises(ValueError):
        language.validate_language(value)


def test_malformed_durable_preference_is_an_error(preference):
    preference["value"] = "broken"
    with pytest.raises(ValueError):
        language.current_language()


def test_concurrent_tasks_keep_independent_languages(preference):
    async def task(selected):
        with language.language_context(selected):
            await asyncio.sleep(0)
            return language.current_language()
    async def run():
        return await asyncio.gather(task("it"), task("en"))
    assert asyncio.run(run()) == ["it", "en"]


def test_thread_worker_uses_explicit_run_capture(preference):
    selected = language.capture_language("en")
    preference["value"] = "it"
    def worker():
        with language.language_context(selected):
            return language.current_language()
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(worker).result() == "en"


def test_stream_scope_closes_underlying_generator_and_restores_on_cancel(preference):
    seen = []
    @language.scoped_language
    async def response():
        try:
            yield language.current_language()
        finally:
            seen.append(language.current_language())
    async def run():
        stream = response(language="en")
        assert await anext(stream) == "en"
        await stream.aclose()
        assert seen == ["en"]
        assert language.current_language() == "it"
    asyncio.run(run())
