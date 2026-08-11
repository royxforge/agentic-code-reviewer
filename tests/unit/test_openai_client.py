"""OpenAI provider client tests (transport-mocked, no network)."""

from __future__ import annotations

import httpx
import pytest

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import (
    ConfigurationError,
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from agentic_code_reviewer.llm.client import build_llm_client
from agentic_code_reviewer.llm.openai_client import OpenAIClient


def _settings(**overrides) -> Settings:
    base = dict(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
        OPENAI_MODEL="gpt-4o-mini",
    )
    base.update(overrides)
    return Settings(**base)


def _completion_json(content: str = "hello", *, cached: int = 0) -> dict:
    usage = {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}
    if cached:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


def test_complete_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json=_completion_json())

    client = OpenAIClient(_settings(), transport=httpx.MockTransport(handler))
    assert client.provider == "openai"
    assert client.model == "gpt-4o-mini"

    resp = client.complete(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=10, timeout=5
    )
    assert resp.content == "hello"
    assert resp.usage.input_tokens == 11
    assert resp.usage.output_tokens == 5
    assert resp.usage.cached_tokens == 0


def test_complete_counts_cached_tokens() -> None:
    """prompt_tokens_details.cached_tokens flows into usage (no crash when absent)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion_json(cached=7))

    client = OpenAIClient(_settings(), transport=httpx.MockTransport(handler))
    resp = client.complete(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=10, timeout=5
    )
    assert resp.usage.cached_tokens == 7


def test_complete_maps_rate_limit() -> None:
    client = OpenAIClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(429, json={"error": {"message": "rate limited"}})
        ),
    )
    with pytest.raises(LLMRateLimitError):
        client.complete(
            [{"role": "user", "content": "x"}], temperature=0, max_tokens=10, timeout=5
        )


def test_complete_maps_auth_error() -> None:
    client = OpenAIClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})
        ),
    )
    with pytest.raises(LLMAuthenticationError):
        client.complete(
            [{"role": "user", "content": "x"}], temperature=0, max_tokens=10, timeout=5
        )


def test_complete_maps_timeout(monkeypatch) -> None:
    """APITimeoutError from the SDK becomes LLMTimeoutError (raised directly
    rather than via an HTTP status, so the SDK's own retry logic is bypassed)."""
    import openai

    client = OpenAIClient(
        _settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_completion_json()))
    )

    def _timeout(*args, **kwargs):
        raise openai.APITimeoutError("request timed out")

    monkeypatch.setattr(client._client.chat.completions, "create", _timeout)
    with pytest.raises(LLMTimeoutError):
        client.complete(
            [{"role": "user", "content": "x"}], temperature=0, max_tokens=10, timeout=5
        )


def test_complete_maps_generic_api_error() -> None:
    from agentic_code_reviewer.errors import LLMError

    client = OpenAIClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(500, json={"error": {"message": "boom"}})
        ),
    )
    with pytest.raises(LLMError):
        client.complete(
            [{"role": "user", "content": "x"}], temperature=0, max_tokens=10, timeout=5
        )


def test_embed_sorts_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            },
        )

    client = OpenAIClient(_settings(), transport=httpx.MockTransport(handler))
    assert client.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]


def test_embed_maps_api_error() -> None:
    from agentic_code_reviewer.errors import LLMError

    client = OpenAIClient(
        _settings(),
        transport=httpx.MockTransport(lambda r: httpx.Response(500, json={})),
    )
    with pytest.raises(LLMError):
        client.embed(["a"])


def test_factory_builds_openai_client() -> None:
    client = build_llm_client(_settings())
    assert isinstance(client, OpenAIClient)
    assert client.model == "gpt-4o-mini"


def test_factory_requires_api_key() -> None:
    with pytest.raises(ConfigurationError):
        build_llm_client(_settings(OPENAI_API_KEY=""))
