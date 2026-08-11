"""Typed input/output schemas for every workflow stage.

Structured (not free-text) contracts are the backbone of the system: each agent
consumes the state and emits one of these validated models, so no stage depends
on unstructured text produced by another stage.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentic_code_reviewer.models.findings import (
    AgentError,
    EvidenceStatus,
    ReviewFinding,
    VerificationStatus,
)
from agentic_code_reviewer.models.review import Review


class ChangeSummary(BaseModel):
    """Output of the change-understanding agent."""

    purpose: str
    control_flow_changes: list[str] = Field(default_factory=list)
    api_changes: list[str] = Field(default_factory=list)
    data_flow_changes: list[str] = Field(default_factory=list)
    state_changes: list[str] = Field(default_factory=list)
    dependency_changes: list[str] = Field(default_factory=list)
    backward_compatibility: str = ""
    affected_callers: list[str] = Field(default_factory=list)
    affected_tests: list[str] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """Output of a single analysis agent."""

    agent: str
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    """Output of the evidence-verification stage for one finding."""

    finding_id: str
    status: VerificationStatus
    method: str
    evidence: str
    evidence_status: EvidenceStatus = EvidenceStatus.INSUFFICIENT_EVIDENCE
    rejection_reason: str = ""
    evidence_layers: list[str] = Field(default_factory=list)


class VerifierOutput(BaseModel):
    """Verifier stage output (a list of per-finding results)."""

    results: list[VerificationResult] = Field(default_factory=list)


class WorkflowResult(BaseModel):
    """Everything a run produces, including observability data."""

    review: Review
    stage_status: dict[str, str] = Field(default_factory=dict)
    agent_errors: list[AgentError] = Field(default_factory=list)
    latency_seconds: dict[str, float] = Field(default_factory=dict)
    token_usage: dict[str, int] = Field(default_factory=dict)
    estimated_cost_usd: float = 0.0
    llm_call_count: int = 0
