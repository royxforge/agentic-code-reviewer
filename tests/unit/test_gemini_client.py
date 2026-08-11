"""Gemini provider client tests (transport-mocked, no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import (
    ConfigurationError,
    LLMAuthenticationError,
    LLMRateLimitError,
)
from agentic_code_reviewer.llm.client import build_llm_client
from agentic_code_reviewer.llm.gemini_client import GeminiClient


def _settings(**overrides) -> Settings:
    base = dict(
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY="test-key",
        GEMINI_MODEL="gemini-2.5-flash",
    )
    base.update(overrides)
    return Settings(**base)


def _generate_response(text: str = "hello") -> dict:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}],
        "usageMetadata": {
            "promptTokenCount": 12,
            "candidatesTokenCount": 7,
            "cachedContentTokenCount": 3,
        },
    }


def test_complete_maps_request_and_response() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        assert request.headers["x-goog-api-key"] == "test-key"
        return httpx.Response(200, json=_generate_response('{"ok": true}'))

    client = GeminiClient(_settings(), transport=httpx.MockTransport(handler))
    resp = client.complete(
        [
            {"role": "system", "content": "you are a strict reviewer"},
            {"role": "user", "content": "review this diff"},
            {"role": "assistant", "content": "acknowledged"},
        ],
        temperature=0.2,
        max_tokens=512,
        timeout=30,
    )

    assert resp.content == '{"ok": true}'
    assert resp.usage.input_tokens == 12
    assert resp.usage.output_tokens == 7
    assert resp.usage.cached_tokens == 3
    assert "generateContent" in captured["url"]

    body = captured["body"]
    # Gemini has no "system" role: system → systemInstruction, assistant → model.
    assert body["systemInstruction"] == {"parts": [{"text": "you are a strict reviewer"}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "review this diff"}]},
        {"role": "model", "parts": [{"text": "acknowledged"}]},
    ]
    assert body["generationConfig"] == {
        "temperature": 0.2,
        "maxOutputTokens": 512,
        "responseMimeType": "application/json",
    }


def test_complete_without_system_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_generate_response("ok"))

    client = GeminiClient(_settings(), transport=httpx.MockTransport(handler))
    resp = client.complete(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=10, timeout=5
    )
    assert resp.content == "ok"


def test_complete_maps_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "message": "Resource has been exhausted.",
                    "status": "RESOURCE_EXHAUSTED",
                }
            },
        )

    client = GeminiClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMRateLimitError):
        client.complete([{"role": "user", "content": "x"}], temperature=0, max_tokens=10, timeout=5)


def test_complete_maps_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "API key not valid"}})

    client = GeminiClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMAuthenticationError):
        client.complete([{"role": "user", "content": "x"}], temperature=0, max_tokens=10, timeout=5)


def test_complete_maps_403_permission_denied() -> None:
    """Gemini reports invalid keys as 403 PERMISSION_DENIED, not 401."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "Permission denied"}})

    client = GeminiClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMAuthenticationError):
        client.complete([{"role": "user", "content": "x"}], temperature=0, max_tokens=10, timeout=5)


def test_complete_handles_empty_candidates() -> None:
    """A safety-blocked response has no candidates; return empty content, don't crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": [], "usageMetadata": {}})

    client = GeminiClient(_settings(), transport=httpx.MockTransport(handler))
    resp = client.complete(
        [{"role": "user", "content": "x"}], temperature=0, max_tokens=10, timeout=5
    )
    assert resp.content == ""


def test_complete_rejects_system_only_conversation() -> None:
    from agentic_code_reviewer.errors import LLMError

    client = GeminiClient(_settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(LLMError):
        client.complete(
            [{"role": "system", "content": "only a system prompt"}],
            temperature=0,
            max_tokens=10,
            timeout=5,
        )


def test_embed_batches_texts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "batchEmbedContents" in str(request.url)
        body = json.loads(request.content)
        assert body["requests"][0]["model"] == "models/text-embedding-004"
        assert body["requests"][1]["content"]["parts"][0]["text"] == "b"
        return httpx.Response(
            200,
            json={"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]},
        )

    client = GeminiClient(_settings(), transport=httpx.MockTransport(handler))
    assert client.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]


def test_factory_builds_gemini_client() -> None:
    client = build_llm_client(_settings())
    assert isinstance(client, GeminiClient)
    assert client.model == "gemini-2.5-flash"


def test_factory_requires_api_key() -> None:
    with pytest.raises(ConfigurationError):
        build_llm_client(_settings(GEMINI_API_KEY=""))
