"""Provider-agnostic LLM client abstraction.

All providers expose the same ``complete`` contract; the workflow never talks to
OpenAI/Anthropic/Ollama directly. Retries with exponential backoff are applied
here so stages never see transient provider failures.
"""

from __future__ import annotations

import contextvars
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import (
    ConfigurationError,
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMTimeoutError,
)

# The stage currently running; used by the mock client and logging to
# attribute calls. Not used for prompt content.
current_stage: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llm_stage", default="unknown"
)


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
        }


@dataclass
class LLMResponse:
    content: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    raw: Any = None
    # True when this response came from the content-addressed disk cache (no
    # provider call was made). Usage is zeroed so cost/call counters reflect
    # only real LLM calls.
    cached: bool = False


class BaseLLMClient(ABC):
    """Interface every provider must implement."""

    provider: str
    model: str
    # Optional content-addressed cache attached by ``build_llm_client`` when
    # LLM_CACHE_ENABLED is set (see llm/cache.py). Defaults to None.
    response_cache: object | None = None

    @abstractmethod
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        """Send a chat completion and return the text response."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(f"{self.provider} does not expose embeddings")

    # ------------------------------------------------------------------
    def complete_with_retry(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
        max_retries: int = 3,
        backoff: float = 2.0,
    ) -> LLMResponse:
        """Call :meth:`complete`, retrying transient failures with backoff.

        When a :class:`ResponseCache` is attached (``self.response_cache``) the
        request is served from disk on a content-address hit, skipping the
        provider entirely.
        """
        cache = getattr(self, "response_cache", None)
        key: str | None = None
        if cache is not None:
            key = cache.key(
                messages,
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            hit = cache.get(key)
            if hit is not None:
                return hit

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = self.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                if cache is not None and key is not None:
                    cache.put(key, response)
                return response
            except LLMRateLimitError as exc:
                last_error = exc
            except LLMTimeoutError as exc:
                last_error = exc
            except LLMAuthenticationError:
                raise
            except Exception as exc:  # network / 5xx / provider-specific
                if getattr(exc, "should_retry", True) is False:
                    raise
                last_error = exc
            if attempt < max_retries:
                time.sleep(backoff * (2**attempt) + (attempt * 0.1))
        raise last_error  # type: ignore[misc]


def build_llm_client(settings: Settings) -> BaseLLMClient:
    """Construct the configured LLM client (lazy provider imports)."""
    client = _build_client_inner(settings)
    if settings.llm_cache_enabled:
        from agentic_code_reviewer.llm.cache import ResponseCache

        try:
            client.response_cache = ResponseCache(settings.llm_cache_dir or None)
        except Exception:  # noqa: BLE001 - a broken cache dir must not kill the client
            pass
    return client


def _build_client_inner(settings: Settings) -> BaseLLMClient:
    """Construct the configured LLM client without cache wiring."""
    # Normalize the user-facing "openai-compatible" spelling. The environment-variable path
    # is normalized by Settings, but CLI overrides via model_copy(update=...)
    # bypass pydantic validators, so be defensive here too.
    provider = settings.llm_provider.replace("-", "_")
    if provider == "openai":
        if not settings.openai_api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is not set. Provide it via acr configure or the environment, "
                "or set LLM_PROVIDER=ollama (local), LLM_PROVIDER=gemini, "
                "LLM_PROVIDER=openai_compatible, or LLM_PROVIDER=mock (tests)."
            )
        from agentic_code_reviewer.llm.openai_client import OpenAIClient

        return OpenAIClient(settings)
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is not set. Provide it via acr configure or the environment, "
                "or set LLM_PROVIDER=ollama, LLM_PROVIDER=gemini, "
                "LLM_PROVIDER=openai_compatible, or LLM_PROVIDER=mock."
            )
        from agentic_code_reviewer.llm.anthropic_client import AnthropicClient

        return AnthropicClient(settings)
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ConfigurationError(
                "GEMINI_API_KEY is not set. Provide it via acr configure or the environment, "
                "or set LLM_PROVIDER=ollama (local), LLM_PROVIDER=mock (tests), "
                "or LLM_PROVIDER=openai_compatible to reach Gemini via its "
                "OpenAI-compatible endpoint."
            )
        from agentic_code_reviewer.llm.gemini_client import GeminiClient

        return GeminiClient(settings)
    if provider == "openai_compatible":
        if not settings.openai_compatible_base_url:
            raise ConfigurationError(
                "OPENAI_COMPATIBLE_BASE_URL is not set. Point it at any "
                "OpenAI-compatible endpoint (vLLM, LM Studio, Groq, OpenRouter, "
                "DeepSeek, ...), e.g. http://localhost:8000/v1."
            )
        if not settings.openai_compatible_model:
            raise ConfigurationError(
                "OPENAI_COMPATIBLE_MODEL is not set  -  choose the model name your "
                "OpenAI-compatible server serves."
            )
        from agentic_code_reviewer.llm.openai_compatible_client import OpenAICompatibleClient

        return OpenAICompatibleClient(settings)
    if provider == "ollama":
        from agentic_code_reviewer.llm.ollama_client import OllamaClient

        return OllamaClient(settings)
    if provider == "mock":
        from agentic_code_reviewer.llm.mock_client import MockLLMClient

        return MockLLMClient()
    raise ConfigurationError(f"Unknown LLM_PROVIDER: {provider!r}")
