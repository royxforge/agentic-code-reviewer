"""Estimated cost tracking.

Prices are USD per 1M tokens (input / output), based on list prices at the time
of writing. Unknown models fall back to a conservative default. These are
estimates for observability only  -  never a billing source of truth.
"""

from __future__ import annotations

from agentic_code_reviewer.llm.client import LLMUsage

# provider -> model -> (input $/1M, output $/1M)
_PRICING: dict[str, dict[str, tuple[float, float]]] = {
    "openai": {
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1-nano": (0.10, 0.40),
        "o4-mini": (1.10, 4.40),
        "text-embedding-3-small": (0.02, 0.0),
    },
    "anthropic": {
        "claude-opus-4-5": (5.00, 25.00),
        "claude-sonnet-4-5": (3.00, 15.00),
        "claude-sonnet-4": (3.00, 15.00),
        "claude-haiku-4-5": (1.00, 5.00),
        "claude-3-5-haiku-latest": (0.80, 4.00),
    },
    "gemini": {
        "gemini-2.5-pro": (1.25, 10.00),
        "gemini-2.5-flash": (0.15, 0.60),
        "gemini-2.5-flash-lite": (0.10, 0.40),
        "text-embedding-004": (0.10, 0.0),
    },
    # Common hosted OpenAI-compatible endpoints; anything else falls back to
    # the conservative default.
    "openai-compatible": {
        "deepseek-chat": (0.27, 1.10),
        "deepseek-reasoner": (0.55, 2.19),
        "grok-3": (3.00, 15.00),
    },
    "ollama": {},  # free / local
    "mock": {},
}

_DEFAULT_PRICE = (3.00, 15.00)


def estimate_cost(provider: str, model: str, usage: LLMUsage) -> float:
    """Return estimated USD cost for a usage record."""
    if provider in ("ollama", "mock"):
        return 0.0  # local / test providers are free
    table = _PRICING.get(provider, {})
    input_price, output_price = table.get(model, _DEFAULT_PRICE)
    cost = (usage.input_tokens / 1_000_000) * input_price
    cost += (usage.output_tokens / 1_000_000) * output_price
    # Cache reads are billed at a fraction of input price; ignore for estimates.
    return round(cost, 6)


def describe(provider: str, model: str) -> str:
    return f"{provider}/{model}"
