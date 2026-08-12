"""High-level review, plan and state models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentic_code_reviewer.models.findings import ReviewFinding

# The set of analysis checks the planner may request.
ALLOWED_CHECKS = (
    "correctness",
    "security",
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
    "dead_code",
)


class ReviewPlan(BaseModel):
    """Output of the Planner stage (never contains the final review)."""

    summary: str = Field(min_length=1)
    affected_files: list[str] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)
    risk_areas: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    decomposition_required: bool = False
    decomposition_notes: str = ""


class ContextChunk(BaseModel):
    """A unit of deliberately selected repository context."""

    repository: str = ""
    file_path: str
    symbol: str = ""
    kind: str = ""  # function | class | module | snippet
    start_line: int = 0
    end_line: int = 0
    text: str
    score: float = 0.0
    source: str = "context_selector"  # context_selector | retrieval


class ReviewMetrics(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0

    @classmethod
    def from_findings(cls, findings: list[ReviewFinding]) -> ReviewMetrics:
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
        return cls(**counts)


class Review(BaseModel):
    """The final, externally consumable review."""

    repository: str = ""
    pull_request: int | None = None
    commit: str | None = None

    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)

    generated_by: str = "agentic-code-reviewer"
    version: str = "0.2.1"
    model: str = ""
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    confidence_threshold: float = 0.0

    @property
    def metrics(self) -> ReviewMetrics:
        return ReviewMetrics.from_findings(self.findings)

    def to_markdown(self, system_name: str = "Agentic Code Review") -> str:
        """Render the review for GitHub/issues (concise, no internal reasoning)."""
        lines: list[str] = [f"## {system_name}", ""]
        for f in sorted(
            self.findings, key=lambda x: (x.severity.rank, -x.confidence), reverse=True
        ):
            icon = {"critical": "🟥", "high": "🔴", "medium": "🟡", "low": "🟢", "info": "⚪"}[
                f.severity.value
            ]
            lines.append(f"### {icon} {f.severity.value.title()}  -  {f.title}")
            lines.append("")
            loc = f.file_path or "?"
            if f.start_line:
                loc += f":{f.start_line}"
            lines.append(f"`{loc}`")
            lines.append("")
            lines.append(f.description.strip())
            if f.evidence:
                lines.append("")
                lines.append(f"**Evidence:** {f.evidence.strip()}")
            if f.recommendation:
                lines.append("")
                lines.append(f"**Recommendation:** {f.recommendation.strip()}")
            if f.evidence_status.value != "insufficient_evidence":
                lines.append("")
                lines.append(
                    f"Evidence: {f.evidence_status.value} · Confidence: {f.confidence:.2f}"
                )
                if f.evidence_layers:
                    lines.append(
                        f"Evidence layers: {', '.join(f.evidence_layers)}"
                    )
                if f.related_categories:
                    lines.append(
                        f"Related categories: {', '.join(f.related_categories)}"
                    )
            lines.append("")
            lines.append("---")
            lines.append("")
        m = self.metrics
        lines.append("### Summary")
        lines.append("")
        lines.append(
            f"- Critical: {m.critical}\n- High: {m.high}\n- Medium: {m.medium}\n"
            f"- Low: {m.low}\n- Info: {m.info}"
        )
        return "\n".join(lines).rstrip()
