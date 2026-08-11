"""Google Gemini provider client.

Talks to the Gemini REST API (``v1beta``) over plain HTTP with ``httpx``, so
no SDK is required  -  the same pattern as the Ollama client. Both chat
(``generateContent``) and embeddings (``batchEmbedContents``) are supported.

Gemini has no ``system`` role in chat messages, so the first ``system``
message is translated into ``systemInstruction`` and ``assistant`` messages
into the ``model`` role.
"""

from __future__ import annotations

from typing import Any

import httpx

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import (
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from agentic_code_reviewer.llm.client import BaseLLMClient, LLMResponse, LLMUsage


class GeminiClient(BaseLLMClient):
    provider = "gemini"

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self.model = settings.gemini_model
        self._embed_model = settings.gemini_embedding_model
        self._base = settings.gemini_base_url.rstrip("/")
        self._api_key = settings.gemini_api_key
        self._client = httpx.Client(
            timeout=settings.llm_timeout_seconds, transport=transport
        )

    # ------------------------------------------------------------------
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {
                "role": "user" if m["role"] == "user" else "model",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
            if m["role"] in ("user", "assistant")
        ]
        if not contents:
            # Gemini rejects an empty contents array with a confusing 400;
            # fail loudly instead.
            raise LLMError(
                "Gemini requires at least one user/assistant message "
                "(system-only conversations are not supported)"
            )
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                # The workflow always asks for structured JSON; forcing the
                # MIME type makes the model emit parseable objects.
                "responseMimeType": "application/json",
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        try:
            resp = self._client.post(
                f"{self._base}/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self._api_key},
                json=body,
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Gemini timeout", detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise _map_http_error(exc) from exc
        except httpx.HTTPError as exc:
            raise LLMError("Gemini unreachable", detail=str(exc)) from exc

        data = resp.json()
        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            content=text,
            usage=LLMUsage(
                input_tokens=usage.get("promptTokenCount", 0) or 0,
                output_tokens=usage.get("candidatesTokenCount", 0) or 0,
                cached_tokens=usage.get("cachedContentTokenCount", 0) or 0,
            ),
            raw=data,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        requests = [
            {
                "model": f"models/{self._embed_model}",
                "content": {"parts": [{"text": text}]},
            }
            for text in texts
        ]
        try:
            resp = self._client.post(
                f"{self._base}/v1beta/models/{self._embed_model}:batchEmbedContents",
                headers={"x-goog-api-key": self._api_key},
                json={"requests": requests},
                timeout=60.0,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Gemini embed timeout", detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise _map_http_error(exc) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini embed failed: {exc}") from exc

        embeddings = resp.json().get("embeddings") or []
        return [item.get("values", []) for item in embeddings]


def _map_http_error(exc: httpx.HTTPStatusError) -> Exception:
    """Map Gemini HTTP errors into the shared error taxonomy."""
    status = exc.response.status_code
    detail = ""
    try:
        error = (exc.response.json() or {}).get("error") or {}
        detail = error.get("message", "")
    except Exception:  # noqa: BLE001 - non-JSON error body
        pass
    if status in (401, 403):
        return LLMAuthenticationError(
            "Gemini auth failed (check GEMINI_API_KEY)", detail=detail or str(exc)
        )
    if status == 429:
        return LLMRateLimitError("Gemini rate limit", detail=detail or str(exc))
    return LLMError(f"Gemini HTTP {status}", detail=detail or str(exc))
