"""Explicit, typed review state threaded through the workflow.

No global mutable state is used: a single :class:`ReviewState` instance is the
read/write contract between stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_code_reviewer.models.findings import AgentError, ReviewFinding
from agentic_code_reviewer.models.review import ContextChunk, Review, ReviewPlan
from agentic_code_reviewer.models.schemas import ChangeSummary, VerificationResult


@dataclass
class ReviewRequest:
    """Normalised description of what to review (framework-agnostic input)."""

    repository: str
    pull_request: int | None = None
    commit: str | None = None
    base_commit: str | None = None
    diff_text: str = ""
    changed_files: list[str] = field(default_factory=list)
    # Maps repository-relative path -> file content at the reviewed snapshot.
    repo_files: dict[str, str] = field(default_factory=dict)
    local_path: str | None = None
    source: str = "inline"  # inline | github_pr | github_commit | local
    # Stated intent to review against (PR description, ticket text, or the
    # ``--requirement`` flag). Empty when none was provided; drives the
    # requirement_alignment check and grounds the planner.
    description: str = ""


@dataclass
class ReviewState:
    request: ReviewRequest

    plan: ReviewPlan | None = None
    change_summary: ChangeSummary | None = None
    context_chunks: list[ContextChunk] = field(default_factory=list)
    # Shared repository snapshot built once per review (never rebuilt per agent).
    snapshot: Any = None

    findings: list[ReviewFinding] = field(default_factory=list)
    verification_results: list[VerificationResult] = field(default_factory=list)
    errors: list[AgentError] = field(default_factory=list)

    final_review: Review | None = None

    stage_status: dict[str, str] = field(default_factory=dict)
    latency_seconds: dict[str, float] = field(default_factory=dict)
    token_usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float = 0.0
    llm_call_count: int = 0

    def record_stage(self, name: str, status: str, seconds: float) -> None:
        self.stage_status[name] = status
        self.latency_seconds[name] = seconds

    def record_error(self, error: AgentError) -> None:
        self.errors.append(error)

    def record_usage(self, usage: dict[str, int], cost: float) -> None:
        for k, v in usage.items():
            self.token_usage[k] = self.token_usage.get(k, 0) + v
        self.estimated_cost_usd += cost
