"""Deterministic mock LLM client.

Used by the test-suite and for keyless local runs (``LLM_PROVIDER=mock``). It
returns scripted JSON per stage (the stage is tracked via a context variable
set by the stage runner), and can simulate timeouts / rate limits / invalid
JSON for failure-path tests.

The mock returns valid, schema-shaped JSON for every workflow stage so a
keyless run produces a real (if canned) review end-to-end. Do **not** use this
client to claim real evaluation results  -  it is a test double, not a model.
"""

from __future__ import annotations

import json

from agentic_code_reviewer.llm.client import (
    BaseLLMClient,
    LLMRateLimitError,
    LLMResponse,
    LLMTimeoutError,
    LLMUsage,
    current_stage,
)


def _stage_default(stage: str) -> str:
    """Valid JSON for the given workflow stage (used when no scripted response)."""
    if stage == "planner":
        return json.dumps(
            {
                "summary": "Canned review plan (mock provider).",
                "affected_files": [],
                "affected_components": ["core"],
                "risk_areas": [],
                "required_checks": ["security", "correctness"],
                "decomposition_required": False,
                "decomposition_notes": "",
            }
        )
    if stage == "change_analyzer":
        return json.dumps(
            {
                "purpose": "change intent",
                "control_flow_changes": [],
                "api_changes": [],
                "data_flow_changes": [],
                "state_changes": [],
                "dependency_changes": [],
                "backward_compatibility": "no break",
                "affected_callers": [],
                "affected_tests": [],
                "known_facts": ["diff parsed"],
                "inferences": [],
            }
        )
    if stage in (
        "security",
        "correctness",
        "error_handling",
        "testing",
        "regression",
        "performance",
        "maintainability",
        "observability",
        "data_integrity",
        "accessibility",
        "concurrency",
        "dependencies",
        "privacy",
        "i18n",
        "api_contract",
        "requirement_alignment",
        "authorization",
        "reliability",
        "architecture",
        "compatibility",
        "configuration",
        "resource_lifecycle",
    ):
        return json.dumps({"agent": stage, "summary": "checked", "findings": [], "notes": []})
    if stage == "aggregator":
        return json.dumps({"summary": "review complete", "findings": []})
    if stage.startswith("baseline_"):
        return json.dumps({"findings": []})
    return "{}"


class MockLLMClient(BaseLLMClient):
    provider = "mock"
    model = "mock-model"

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        *,
        fail_with: set[str] | None = None,
        timeout_on: set[str] | None = None,
        rate_limit_on: set[str] | None = None,
        invalid_json_on: set[str] | None = None,
        default_response: str = "{}",
    ) -> None:
        self.responses = responses or {}
        self.fail_with = fail_with or set()
        self.timeout_on = timeout_on or set()
        self.rate_limit_on = rate_limit_on or set()
        self.invalid_json_on = invalid_json_on or set()
        self.default_response = default_response
        self.calls: list[str] = []

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        stage = current_stage.get()
        self.calls.append(stage)
        if stage in self.fail_with:
            raise RuntimeError(f"mock failure for {stage}")
        if stage in self.timeout_on:
            raise LLMTimeoutError(f"mock timeout for {stage}")
        if stage in self.rate_limit_on:
            raise LLMRateLimitError(f"mock rate limit for {stage}")
        content = self.responses.get(stage, self.default_response)
        if content == self.default_response and self.default_response == "{}":
            # No scripted response: return a valid per-stage payload so keyless
            # (LLM_PROVIDER=mock) runs complete a full review end-to-end.
            content = _stage_default(stage)
        if stage in self.invalid_json_on:
            content = "this is { definitely not valid JSON"
        # Emulate a plausible token count for cost calculations in tests.
        return LLMResponse(
            content=content,
            usage=LLMUsage(
                input_tokens=sum(len(m.get("content", "")) for m in messages) // 4,
                output_tokens=len(content) // 4,
            ),
        )
