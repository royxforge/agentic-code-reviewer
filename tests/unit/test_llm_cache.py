from __future__ import annotations

import pytest

from agentic_code_reviewer.llm.cache import ResponseCache
from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage, build_llm_client
from agentic_code_reviewer.llm.mock_client import MockLLMClient


@pytest.fixture
def cache(tmp_path) -> ResponseCache:
    return ResponseCache(tmp_path)


def test_key_differs_on_content():
    a = ResponseCache.key([{"role": "user", "content": "hello"}], model="m", temperature=0.2, max_tokens=100)
    b = ResponseCache.key([{"role": "user", "content": "world"}], model="m", temperature=0.2, max_tokens=100)
    assert a != b


def test_key_same_for_identical_request():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    a = ResponseCache.key(msgs, model="m", temperature=0.2, max_tokens=100)
    b = ResponseCache.key(msgs, model="m", temperature=0.2, max_tokens=100)
    assert a == b


def test_key_changes_with_model_or_budget():
    msgs = [{"role": "user", "content": "x"}]
    a = ResponseCache.key(msgs, model="m1", temperature=0.2, max_tokens=100)
    b = ResponseCache.key(msgs, model="m2", temperature=0.2, max_tokens=100)
    assert a != b
    c = ResponseCache.key(msgs, model="m1", temperature=0.2, max_tokens=200)
    assert a != c


def test_put_get_roundtrip(cache):
    resp = LLMResponse(content='{"findings": []}', usage=LLMUsage(input_tokens=50, output_tokens=10))
    key = ResponseCache.key([{"role": "user", "content": "q"}], model="m", temperature=0.2, max_tokens=100)
    cache.put(key, resp)
    hit = cache.get(key)
    assert hit is not None
    assert hit.content == '{"findings": []}'
    assert hit.cached is True
    assert hit.usage.input_tokens == 50


def test_miss_returns_none(cache):
    key = ResponseCache.key([{"role": "user", "content": "nope"}], model="m", temperature=0.2, max_tokens=100)
    assert cache.get(key) is None


def test_never_recache_cache_hits(cache):
    key = ResponseCache.key([{"role": "user", "content": "q"}], model="m", temperature=0.2, max_tokens=100)
    cache.put(key, LLMResponse(content="first"))
    cache.put(key, LLMResponse(content="first", cached=True))
    assert cache.get(key).content == "first"


def test_corrupt_file_degrades_to_none(tmp_path):
    cache = ResponseCache(tmp_path)
    key = ResponseCache.key([{"role": "user", "content": "q"}], model="m", temperature=0.2, max_tokens=100)
    (tmp_path / f"{key}.json").write_text("{not json", encoding="utf-8")
    assert cache.get(key) is None


def test_clear_removes_all(tmp_path):
    cache = ResponseCache(tmp_path)
    key = ResponseCache.key([{"role": "user", "content": "q"}], model="m", temperature=0.2, max_tokens=100)
    cache.put(key, LLMResponse(content="x"))
    assert cache.clear() == 1
    assert cache.get(key) is None


def test_complete_with_retry_serves_from_cache(tmp_path):
    """A cached response must be served without calling the provider again."""

    cache = ResponseCache(tmp_path)
    client = MockLLMClient()
    client.response_cache = cache

    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    first = client.complete_with_retry(
        msgs, temperature=0.2, max_tokens=100, timeout=10
    )
    assert first.cached is False
    calls_after_first = len(client.calls)

    second = client.complete_with_retry(
        msgs, temperature=0.2, max_tokens=100, timeout=10
    )
    assert second.cached is True
    assert second.content == first.content
    assert len(client.calls) == calls_after_first  # no new provider call


def test_build_llm_client_attaches_cache_when_enabled(tmp_path):
    from agentic_code_reviewer.config.settings import Settings

    settings = Settings(
        LLM_PROVIDER="mock",
        LLM_CACHE_ENABLED="true",
        LLM_CACHE_DIR=str(tmp_path),
    )
    client = build_llm_client(settings)
    assert client.response_cache is not None


def test_build_llm_client_no_cache_by_default():
    from agentic_code_reviewer.config.settings import Settings

    settings = Settings(LLM_PROVIDER="mock")
    client = build_llm_client(settings)
    assert client.response_cache is None
