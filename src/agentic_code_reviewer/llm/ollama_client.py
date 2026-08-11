"""Ollama local client.

Talks to the local Ollama server over plain HTTP (``/api/chat`` and
``/api/embed``), so no SDK is required and everything works keyless.
"""

from __future__ import annotations

import httpx

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import (
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from agentic_code_reviewer.llm.client import BaseLLMClient, LLMResponse, LLMUsage


class OllamaClient(BaseLLMClient):
    provider = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.ollama_model
        self._base = settings.ollama_base_url.rstrip("/")
        self._client = httpx.Client(timeout=settings.llm_timeout_seconds)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        try:
            resp = self._client.post(
                f"{self._base}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    # Ollama's native JSON mode: the server guarantees the
                    # response is a single valid JSON object.
                    "format": "json",
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Ollama timeout", detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise LLMAuthenticationError("Ollama auth failed") from exc
            if exc.response.status_code == 429:
                raise LLMRateLimitError("Ollama rate limit") from exc
            raise LLMError(f"Ollama HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(
                f"Ollama unreachable at {self._base} (is the server running?)",
                detail=str(exc),
            ) from exc

        data = resp.json()
        content = (data.get("message") or {}).get("content", "")
        prompt_eval = data.get("prompt_eval_count", 0) or 0
        eval_count = data.get("eval_count", 0) or 0
        return LLMResponse(
            content=content,
            usage=LLMUsage(input_tokens=prompt_eval, output_tokens=eval_count),
            raw=data,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self._client.post(
                f"{self._base}/api/embed",
                json={"model": self.model, "input": texts},
                timeout=60.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama embed failed: {exc}") from exc
        return resp.json().get("embeddings", [])
