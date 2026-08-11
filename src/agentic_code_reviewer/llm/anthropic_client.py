"""Anthropic provider client (lazy import of the ``anthropic`` SDK).

Anthropic exposes no embeddings API, so ``embed`` is intentionally unavailable;
the retrieval layer falls back to the local embedder in that case.
"""

from __future__ import annotations

from typing import Any, cast

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import (
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from agentic_code_reviewer.llm.client import BaseLLMClient, LLMResponse, LLMUsage


class AnthropicClient(BaseLLMClient):
    provider = "anthropic"

    def __init__(self, settings: Settings) -> None:
        import anthropic

        self.model = settings.anthropic_model
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        import anthropic

        system = "\n".join(
            m["content"] for m in messages if m["role"] == "system"
        )
        user_parts = [
            {"type": "text", "text": m["content"]}
            for m in messages
            if m["role"] == "user"
        ]
        try:
            system_arg: Any = anthropic.NOT_GIVEN if not system else system
            resp = self._client.messages.create(
                model=self.model,
                system=system_arg,
                messages=cast(Any, [{"role": "user", "content": user_parts}]),
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError("Anthropic rate limit", detail=str(exc)) from exc
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError("Anthropic timeout", detail=str(exc)) from exc
        except anthropic.AuthenticationError as exc:
            raise LLMAuthenticationError(
                "Anthropic auth failed", detail=str(exc)
            ) from exc
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic error: {exc}") from exc

        text = "".join(
            block.text
            for block in resp.content
            if isinstance(block, anthropic.types.TextBlock)
        )
        usage = LLMUsage(
            input_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "output_tokens", 0) or 0,
            cached_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        return LLMResponse(content=text, usage=usage, raw=resp)
