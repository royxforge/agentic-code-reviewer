"""Shared helpers for usage records."""

from __future__ import annotations

from agentic_code_reviewer.llm.client import LLMUsage


def empty_usage() -> LLMUsage:
    return LLMUsage()
