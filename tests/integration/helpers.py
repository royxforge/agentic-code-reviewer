"""Adaptive mock LLM client for deterministic end-to-end tests.

Returns valid structured JSON per stage, adapting findings to the diff content
so a single client can serve all benchmark entries. This is a test double  -  it
is never used to claim real evaluation results.
"""

from __future__ import annotations

import json

from agentic_code_reviewer.llm.client import current_stage
from agentic_code_reviewer.llm.mock_client import MockLLMClient


def _security_finding() -> dict:
    return {
        "category": "security",
        "severity": "high",
        "confidence": 0.92,
        "title": "SQL injection via interpolated query",
        "description": (
            "The user-controlled query is interpolated directly into the SQL "
            "statement with an f-string, allowing an attacker to alter the executed SQL."
        ),
        "file_path": "db.py",
        "start_line": 11,
        "evidence": 'sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"',
        "impact": "SQL injection; attacker can read or modify database contents.",
        "recommendation": "Use a parameterized query and pass the value as a parameter.",
        "related_files": ["app.py"],
        "rule_id": None,
    }


def _off_by_one_finding() -> dict:
    return {
        "category": "correctness",
        "severity": "medium",
        "confidence": 0.85,
        "title": "Binary search skips exact matches",
        "description": (
            "The loop now compares with `>` instead of `>=`, so when the threshold "
            "is present exactly, the search returns a later element or None."
        ),
        "file_path": "search.py",
        "start_line": 7,
        "evidence": "if items[mid] > threshold:",
        "impact": "find_first returns the wrong element for exact matches.",
        "recommendation": "Compare with `>=` and keep `low <= high`.",
        "related_files": [],
        "rule_id": None,
    }


def _analysis_result(agent: str, findings: list[dict]) -> dict:
    return {"agent": agent, "summary": "checked", "findings": findings, "notes": []}


def _findings_for(diff_hint: str) -> list[dict]:
    if "db.py" in diff_hint and "search_users" in diff_hint:
        return [_security_finding()]
    if "search.py" in diff_hint and "find_first" in diff_hint:
        return [_off_by_one_finding()]
    return []


def _stage_response(stage: str, content: str) -> str:
    diff_hint = content
    if stage == "planner":
        checks = (
            ["correctness"]
            if "utils.py" in diff_hint
            else ["security", "correctness"]
        )
        plan = {
            "summary": "change under review",
            "affected_files": [],
            "affected_components": ["core"],
            "risk_areas": ["security", "data integrity"] if "db.py" in diff_hint else [],
            "required_checks": checks,
            "decomposition_required": False,
            "decomposition_notes": "",
        }
        return json.dumps(plan)
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
        findings = _findings_for(diff_hint)
        if stage == "security":
            findings = [f for f in findings if f["category"] == "security"]
        elif stage == "correctness":
            findings = [f for f in findings if f["category"] == "correctness"]
        else:
            findings = []
        return json.dumps(_analysis_result(stage, findings))
    if stage in ("baseline_single", "baseline_context", "baseline_rag"):
        return json.dumps({"findings": _findings_for(diff_hint)})
    if stage == "aggregator":
        return json.dumps(
            {"summary": "review complete", "findings": _findings_for(diff_hint)}
        )
    return "{}"


class AdaptiveMock(MockLLMClient):
    """MockLLMClient that adapts findings to the diff content."""

    def complete(self, messages, **kwargs):
        stage = current_stage.get()
        content = "\n".join(m.get("content", "") for m in messages)
        self.calls.append(stage)
        if stage in self.fail_with:
            raise RuntimeError(f"mock failure for {stage}")
        if stage in self.timeout_on:
            from agentic_code_reviewer.llm.client import LLMTimeoutError

            raise LLMTimeoutError(f"mock timeout for {stage}")
        if stage in self.rate_limit_on:
            from agentic_code_reviewer.llm.client import LLMRateLimitError

            raise LLMRateLimitError(f"mock rate limit for {stage}")
        payload = _stage_response(stage, content)
        from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage

        return LLMResponse(
            content=payload,
            usage=LLMUsage(input_tokens=len(content) // 4, output_tokens=len(payload) // 4),
        )
