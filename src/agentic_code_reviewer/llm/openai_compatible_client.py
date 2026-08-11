"""OpenAI-compatible provider client.

Covers any endpoint that speaks the OpenAI chat-completions protocol: vLLM,
LM Studio, llama.cpp server, Groq, OpenRouter, DeepSeek, Together, localai, ...
It reuses the ``OpenAIClient`` implementation and only differs in where it
points and how it authenticates:

* the base URL is **required** (there is no sensible default),
* the API key is **optional**  -  many local servers ignore it entirely, so an
  empty key falls back to a placeholder instead of failing configuration.
"""

from __future__ import annotations

from typing import Any

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.llm.openai_client import OpenAIClient


class OpenAICompatibleClient(OpenAIClient):
    provider = "openai-compatible"
    label = "OpenAI-compatible"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Any | None = None,
    ) -> None:
        import openai

        self.model = settings.openai_compatible_model
        self._embed_model = settings.openai_compatible_embedding_model
        self._client = openai.OpenAI(
            **self._sdk_kwargs(
                # Local servers (LM Studio, vLLM) don't validate keys; the SDK
                # requires *something*, so substitute a harmless placeholder.
                api_key=settings.openai_compatible_api_key or "not-needed",
                base_url=settings.openai_compatible_base_url.rstrip("/"),
                transport=transport,
            )
        )
