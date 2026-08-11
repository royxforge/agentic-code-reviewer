"""OpenAI provider client (lazy import of the ``openai`` SDK)."""

from __future__ import annotations

from typing import Any, cast

import httpx

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import (
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from agentic_code_reviewer.llm.client import BaseLLMClient, LLMResponse, LLMUsage


class OpenAIClient(BaseLLMClient):
    provider = "openai"
    # Human-readable label used in error messages (subclassed by the
    # OpenAI-compatible client so errors name the right endpoint).
    label = "OpenAI"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        import openai

        self.model = settings.openai_model
        self._embed_model = settings.openai_embedding_model
        self._client = openai.OpenAI(
            **self._sdk_kwargs(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                transport=transport,
            )
        )

    @staticmethod
    def _sdk_kwargs(
        api_key: str,
        base_url: str,
        transport: httpx.BaseTransport | None,
    ) -> dict[str, Any]:
        """Build the OpenAI SDK client kwargs (shared with compatible endpoints)."""
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        if transport is not None:
            kwargs["http_client"] = httpx.Client(transport=transport)
        return kwargs

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        import openai

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=cast(Any, messages),
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except openai.RateLimitError as exc:
            raise LLMRateLimitError(
                f"{self.label} rate limit", detail=str(exc)
            ) from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError(f"{self.label} timeout", detail=str(exc)) from exc
        except openai.AuthenticationError as exc:
            raise LLMAuthenticationError(
                f"{self.label} auth failed", detail=str(exc)
            ) from exc
        except openai.APIError as exc:
            raise _to_llm_error(exc, self.label) from exc

        choice = resp.choices[0]
        usage = LLMUsage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
            cached_tokens=getattr(resp.usage, "prompt_tokens_details", None).cached_tokens  # type: ignore[union-attr]
            if getattr(resp.usage, "prompt_tokens_details", None)
            else 0,
        )
        return LLMResponse(
            content=choice.message.content or "",
            usage=usage,
            raw=resp,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        import openai

        try:
            resp = self._client.embeddings.create(
                model=self._embed_model, input=texts
            )
        except openai.APIError as exc:
            raise _to_llm_error(exc, self.label) from exc
        ordered = sorted(resp.data, key=lambda d: d.index)
        return [item.embedding for item in ordered]


def _to_llm_error(exc: Exception, label: str) -> Exception:
    """Map remaining openai errors into our taxonomy."""
    from agentic_code_reviewer.errors import LLMError

    status = getattr(exc, "status_code", None)
    if status == 429:
        return LLMRateLimitError(f"{label} rate limit", detail=str(exc))
    if status == 401:
        return LLMAuthenticationError(f"{label} auth failed", detail=str(exc))
    return LLMError(f"{label} error: {exc}")
