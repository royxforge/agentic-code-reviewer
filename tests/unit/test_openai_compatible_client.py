"""OpenAI-compatible provider client tests (transport-mocked, no network)."""

from __future__ import annotations

import httpx
import pytest

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import ConfigurationError
from agentic_code_reviewer.llm.client import build_llm_client
from agentic_code_reviewer.llm.openai_compatible_client import OpenAICompatibleClient


def _settings(**overrides) -> Settings:
    base = dict(
        LLM_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_BASE_URL="http://localhost:8000/v1",
        OPENAI_COMPATIBLE_MODEL="deepseek-chat",
        OPENAI_COMPATIBLE_API_KEY="",
    )
    base.update(overrides)
    return Settings(**base)


def _completion_json(content: str = '{"ok": true}') -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
    }


def test_complete_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "localhost"
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=_completion_json())

    client = OpenAICompatibleClient(_settings(), transport=httpx.MockTransport(handler))
    assert client.provider == "openai-compatible"
    assert client.model == "deepseek-chat"

    resp = client.complete(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=10, timeout=5
    )
    assert resp.content == '{"ok": true}'
    assert resp.usage.input_tokens == 11
    assert resp.usage.output_tokens == 5


def test_empty_api_key_falls_back_to_placeholder() -> None:
    """Local servers ignore the key; the SDK still needs a non-empty value."""

    def handler(request: httpx.Request) -> httpx.Response:
        # The SDK sends Authorization: Bearer <key>.
        assert request.headers["authorization"] == "Bearer not-needed"
        return httpx.Response(200, json=_completion_json("ok"))

    client = OpenAICompatibleClient(_settings(), transport=httpx.MockTransport(handler))
    resp = client.complete(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=10, timeout=5
    )
    assert resp.content == "ok"


def test_factory_builds_compatible_client() -> None:
    client = build_llm_client(_settings())
    assert isinstance(client, OpenAICompatibleClient)


def test_factory_requires_base_url() -> None:
    with pytest.raises(ConfigurationError):
        build_llm_client(_settings(OPENAI_COMPATIBLE_BASE_URL=""))


def test_factory_requires_model() -> None:
    with pytest.raises(ConfigurationError):
        build_llm_client(_settings(OPENAI_COMPATIBLE_MODEL=""))
