"""Tests for machine-readable export (JSON/SARIF) and the quality gate."""

import json

from agentic_code_reviewer.cli import _gate_violation
from agentic_code_reviewer.cli.exporters import (
    export_review,
    review_to_json,
    review_to_sarif,
)
from agentic_code_reviewer.models.findings import ReviewFinding, Severity, VerificationStatus
from agentic_code_reviewer.models.review import Review
from agentic_code_reviewer.models.schemas import WorkflowResult


def _finding(
    severity=Severity.HIGH,
    title="SQL injection",
    file_path="db.py",
    start_line=11,
    category="security",
):
    return ReviewFinding(
        category=category,
        severity=severity,
        confidence=0.9,
        title=title,
        description="User input reaches the SQL query.",
        repository="o/r",
        file_path=file_path,
        start_line=start_line,
        end_line=start_line,
        evidence="query = f\"...{user_input}\"",
        impact="Data exfiltration",
        recommendation="Use parameterised queries.",
        verification_status=VerificationStatus.VERIFIED,
        rule_id="sql-interpolated",
        related_files=["db.py", "app.py"],
    )


def _result(*findings):
    review = Review(
        repository="o/r",
        summary="Reviewed db.py",
        findings=list(findings) or [_finding()],
        model="mock/model",
    )
    return WorkflowResult(review=review, llm_call_count=5, estimated_cost_usd=0.001)


def test_review_to_json_roundtrip():
    payload = json.loads(review_to_json(_result()))
    assert payload["schema"] == "agentic-code-reviewer/v1"
    assert payload["review"]["repository"] == "o/r"
    assert payload["usage"]["llm_calls"] == 5
    finding = payload["review"]["findings"][0]
    assert finding["severity"] == "high"
    assert finding["file_path"] == "db.py"
    assert finding["start_line"] == 11
    assert finding["verification_status"] == "verified"


def test_review_to_sarif_structure():
    doc = json.loads(review_to_sarif(_result()))
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "agentic-code-reviewer"
    results = run["results"]
    assert len(results) == 1
    result = results[0]
    assert result["ruleId"] == "sql-interpolated"
    assert result["level"] == "error"  # HIGH maps to error
    loc = result["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "db.py"
    assert loc["region"]["startLine"] == 11
    rule = run["tool"]["driver"]["rules"][0]
    assert rule["properties"]["category"] == "security"
    assert rule["defaultConfiguration"]["level"] == "error"


def test_sarif_level_mapping_for_all_severities():
    findings = [_finding(severity=s) for s in Severity]
    doc = json.loads(review_to_sarif(_result(*findings)))
    levels = {r["level"] for r in doc["runs"][0]["results"]}
    assert levels == {"error", "warning", "note"}


def test_export_review_writes_file(tmp_path):
    out = tmp_path / "review.json"
    export_review(_result(), "json", out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["review"]["model"] == "mock/model"


def test_export_review_to_stream(tmp_path):
    import io

    stream = io.StringIO()
    export_review(_result(), "sarif", None, text_stream=stream)
    doc = json.loads(stream.getvalue())
    assert doc["version"] == "2.1.0"


def test_export_review_markdown_fallback():
    import io

    stream = io.StringIO()
    export_review(_result(), "markdown", None, text_stream=stream)
    assert "SQL injection" in stream.getvalue()


# ---------------------------------------------------------------------------
# Quality gate (--fail-on)
# ---------------------------------------------------------------------------


def test_gate_no_fail_on_passes():
    assert _gate_violation(_result(), None) == ""


def test_gate_passes_when_below_threshold():
    result = _result(_finding(severity=Severity.MEDIUM))
    assert _gate_violation(result, "high") == ""


def test_gate_fails_on_high():
    result = _result(_finding(severity=Severity.HIGH))
    violation = _gate_violation(result, "high")
    assert violation
    assert "HIGH" in violation


def test_gate_any_trips_on_info():
    result = _result(_finding(severity=Severity.INFO))
    assert _gate_violation(result, "any")


def test_gate_ignores_filtered_findings(tmp_path):
    """Findings on ignored paths must not trip the gate."""
    from agentic_code_reviewer.cli import _filtered_findings
    from agentic_code_reviewer.config.file_config import RepoConfig

    result = _result(_finding(file_path="tests/test_db.py"))
    filtered = _filtered_findings(
        result, RepoConfig(ignore_patterns=["**/test_*.py"])
    )
    assert filtered.review.findings == []
    assert _gate_violation(filtered, "high") == ""


def test_gate_from_config_derives_flag_level():
    """The .reviewer.yaml gate must be enforced when --fail-on is absent."""
    from agentic_code_reviewer.cli import _gate_from_config
    from agentic_code_reviewer.config.file_config import QualityGate, RepoConfig

    # critical: 0 -> any critical finding fails the gate.
    gate = _gate_from_config(RepoConfig(quality_gate=QualityGate(critical=0)))
    assert gate == "critical"
    assert _gate_violation(_result(_finding(severity=Severity.CRITICAL)), gate)

    # high: 5, critical None -> high is the lowest configured bucket.
    gate2 = _gate_from_config(
        RepoConfig(quality_gate=QualityGate(critical=None, high=5))
    )
    assert gate2 == "high"
    assert _gate_violation(_result(), gate2)  # default finding is HIGH

    # No gate configured -> nothing enforced.
    assert _gate_from_config(RepoConfig()) is None
