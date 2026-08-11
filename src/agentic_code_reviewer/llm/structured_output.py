"""Structured output extraction and validation.

LLM responses are never trusted blindly: we parse the JSON, validate it against
the target pydantic model, and on failure perform one controlled repair call.
Only after repair fails do we raise :class:`ReviewValidationError`.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agentic_code_reviewer.errors import ReviewValidationError
from agentic_code_reviewer.llm.client import BaseLLMClient, LLMUsage, current_stage

T = TypeVar("T", bound=BaseModel)


def extract_json(text: str) -> object:
    """Extract the first JSON value from model output (handles fenced blocks)."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    decoder = json.JSONDecoder()
    for start in range(len(cleaned)):
        if cleaned[start] not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[start:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError(f"no JSON value found in model output: {text[:200]!r}")


def parse_and_validate(model: type[T], text: str) -> T:
    """Parse JSON from ``text`` and validate it against ``model``."""
    value = extract_json(text)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}")
    return model.model_validate(value)


def call_structured(
    client: BaseLLMClient,
    model: type[T],
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout: float = 120.0,
    max_retries: int = 1,
) -> tuple[T, LLMUsage, int]:
    """Call the LLM and return a validated ``model`` instance.

    Returns ``(parsed_model, combined_usage, call_count)``. Raises
    :class:`ReviewValidationError` if output remains invalid after repair.
    """
    messages = [{"role": "system", "content": system}]
    user_msg = user + (
        "\n\nRespond with ONLY a single JSON object. Do not include markdown "
        "code fences, commentary, or trailing text."
    )
    messages.append({"role": "user", "content": user_msg})

    usage_total = LLMUsage()
    calls = 0
    raw_output = ""

    def _attempt(fix_hint: str | None = None) -> tuple[object, LLMUsage]:
        nonlocal calls, raw_output
        msg = messages[0]["content"]
        content = user_msg
        if fix_hint:
            content += (
                "\n\nYour previous response failed validation with this error:\n"
                f"{fix_hint}\n"
                "Your previous raw output was:\n"
                f"{raw_output[:1000]}\n"
                "Return ONLY the corrected JSON object now."
            )
        msgs = [{"role": "system", "content": msg}, {"role": "user", "content": content}]
        calls += 1
        resp = client.complete_with_retry(
            msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        raw_output = resp.content
        usage_total.input_tokens += resp.usage.input_tokens
        usage_total.output_tokens += resp.usage.output_tokens
        usage_total.cached_tokens += resp.usage.cached_tokens
        return extract_json(resp.content), resp.usage

    try:
        value, _ = _attempt()
        return model.model_validate(value), usage_total, calls
    except (ValueError, ValidationError, json.JSONDecodeError) as first:
        hint = f"{type(first).__name__}: {first}"
        try:
            value, _ = _attempt(fix_hint=hint)
            return model.model_validate(value), usage_total, calls
        except (ValueError, ValidationError, json.JSONDecodeError) as second:
            raise ReviewValidationError(
                f"stage '{current_stage.get()}' produced invalid structured output",
                detail=f"{type(second).__name__}: {second}",
            ) from second
