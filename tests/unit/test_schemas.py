import pytest
from pydantic import ValidationError

from agentic_code_reviewer.models.findings import (
    ReviewFinding,
)
from agentic_code_reviewer.models.review import Review, ReviewMetrics, ReviewPlan
from agentic_code_reviewer.orchestration.state import ReviewRequest, ReviewState


def _finding(**overrides):
    base = dict(
        category="security",
        severity="high",
        confidence=0.9,
        title="SQL injection",
        description="Query is interpolated into SQL.",
    )
    base.update(overrides)
    return ReviewFinding(**base)


def test_severity_is_closed_set():
    with pytest.raises(ValidationError):
        _finding(severity="catastrophic")


def test_confidence_range():
    with pytest.raises(ValidationError):
        _finding(confidence=1.5)
    with pytest.raises(ValidationError):
        _finding(confidence=-0.1)
    assert _finding(confidence=0.0).confidence == 0.0
    assert _finding(confidence=1.0).confidence == 1.0


def test_line_validation():
    with pytest.raises(ValidationError):
        _finding(start_line=0)
    with pytest.raises(ValidationError):
        _finding(start_line=5, end_line=3)
    assert _finding(start_line=3, end_line=5).end_line == 5


def test_required_fields():
    with pytest.raises(ValidationError):
        ReviewFinding(category="security", severity="high", confidence=0.9, title="x", description="")
    with pytest.raises(ValidationError):
        ReviewFinding(category="security", severity="high", confidence=0.9, title="", description="x")


def test_plan_checks():
    plan = ReviewPlan(
        summary="refactor",
        affected_files=["a.py"],
        required_checks=["correctness", "security"],
    )
    assert plan.required_checks == ["correctness", "security"]


def test_review_metrics_counts():
    findings = [
        _finding(severity="critical"),
        _finding(severity="high"),
        _finding(severity="high"),
        _finding(severity="info"),
    ]
    metrics = ReviewMetrics.from_findings(findings)
    assert metrics.critical == 1
    assert metrics.high == 2
    assert metrics.info == 1
    assert metrics.medium == 0


def test_review_markdown_is_concise():
    review = Review(
        repository="o/r",
        summary="summary",
        findings=[_finding(severity="high", start_line=12, file_path="db.py")],
    )
    md = review.to_markdown()
    assert "### 🔴 High" in md
    assert "db.py:12" in md
    assert "### Summary" in md
    # Internal stage data must not leak into the external review.
    assert "planner" not in md.lower() or "plan" not in md


def test_state_usage_recording():
    state = ReviewState(request=ReviewRequest(repository="o/r", diff_text="x"))
    state.record_usage({"input_tokens": 10, "output_tokens": 5}, 0.001)
    state.record_usage({"input_tokens": 10, "output_tokens": 5}, 0.001)
    assert state.token_usage["input_tokens"] == 20
    assert state.estimated_cost_usd == 0.002
    assert state.llm_call_count == 0
