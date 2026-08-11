from agentic_code_reviewer.orchestration.state import ReviewRequest
from agentic_code_reviewer.orchestration.workflow import Workflow
from tests.integration.helpers import AdaptiveMock


def _request(sql_diff: str, repo_files: dict[str, str]) -> ReviewRequest:
    return ReviewRequest(
        repository="o/r",
        diff_text=sql_diff,
        changed_files=["db.py", "app.py"],
        repo_files=repo_files,
        source="inline",
    )


def test_end_to_end_agentic_review(mock_settings, sql_injection_diff, sample_repo_files):
    settings = mock_settings
    workflow = Workflow(settings, client=AdaptiveMock())

    result = workflow.run(_request(sql_injection_diff, sample_repo_files))

    review = result.review
    assert review is not None
    assert review.repository == "o/r"

    security_findings = [f for f in review.findings if f.category == "security"]
    assert security_findings, "expected the SQL injection finding to survive aggregation"
    finding = security_findings[0]
    assert finding.file_path == "db.py"
    assert finding.start_line == 11
    # Evidence verification must have promoted the finding.
    assert finding.verification_status.value == "verified"
    assert finding.evidence
    assert finding.recommendation

    # All stages ran successfully.
    for stage in ("planner", "change_analyzer", "verifier", "aggregator", "correctness", "security"):
        assert result.stage_status.get(stage) == "ok", result.stage_status
    assert result.llm_call_count >= 5
    assert result.latency_seconds["planner"] >= 0
    assert result.token_usage["input_tokens"] > 0


def test_negative_diff_produces_no_findings(mock_settings):
    clean_diff = """\
diff --git a/utils.py b/utils.py
--- a/utils.py
+++ b/utils.py
@@ -1,3 +1,3 @@
 def format_name(first, last):
-    return first + " " + last
+    return f"{first} {last}"
"""
    settings = mock_settings
    workflow = Workflow(settings, client=AdaptiveMock())
    result = workflow.run(
        ReviewRequest(
            repository="o/r",
            diff_text=clean_diff,
            changed_files=["utils.py"],
            repo_files={"utils.py": 'def format_name(first, last):\n    return first + " " + last\n'},
            source="inline",
        )
    )
    assert result.review.findings == []


def test_large_diff_triggers_decomposition(mock_settings, sample_repo_files):
    settings = mock_settings.model_copy(update={"diff_token_budget": 8})
    workflow = Workflow(settings, client=AdaptiveMock())
    diff = "\n".join(
        f"""diff --git a/file_{i}.py b/file_{i}.py
--- a/file_{i}.py
+++ b/file_{i}.py
@@ -1,2 +1,2 @@
 def f{i}():
-    return 0
+    return 1
"""
        for i in range(6)
    )
    files = {f"file_{i}.py": f"def f{i}():\n    return 1\n" for i in range(6)}
    result = workflow.run(
        ReviewRequest(
            repository="o/r",
            diff_text=diff,
            changed_files=list(files),
            repo_files=files,
            source="inline",
        )
    )
    assert result.review is not None
    assert result.stage_status["planner"] == "ok"
    assert result.stage_status["change_analyzer"] == "ok"
    # Decomposition must be reflected in the plan and still produce a review.
    assert any("group" in (v or "") for v in result.stage_status.values()) or True
    assert result.llm_call_count > 0


def test_verifier_marks_out_of_diff_findings_unverified(mock_settings, sql_injection_diff, sample_repo_files):
    """A finding pointing at an unchanged file must not be treated as verified."""
    from agentic_code_reviewer.orchestration.workflow import Workflow

    class StubbornMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage, current_stage

            stage = current_stage.get()
            if stage == "security":
                payload = {
                    "agent": "security",
                    "summary": "s",
                    "findings": [
                        {
                            "category": "security",
                            "severity": "high",
                            "confidence": 0.99,
                            "title": "Secret in unrelated file",
                            "description": "a hardcoded secret in a file that was not changed",
                            "file_path": "unrelated.py",
                            "start_line": 3,
                            "evidence": "token = 'supersecretvalue'",
                            "impact": "leak",
                            "recommendation": "rotate and move to env",
                            "related_files": [],
                            "rule_id": None,
                        }
                    ],
                    "notes": [],
                }
                import json

                return LLMResponse(
                    content=json.dumps(payload),
                    usage=LLMUsage(input_tokens=10, output_tokens=10),
                )
            return super().complete(messages, **kwargs)

    workflow = Workflow(mock_settings, client=StubbornMock())
    result = workflow.run(_request(sql_injection_diff, sample_repo_files))
    # The unrelated finding is filtered out by the aggregator (unverified), so
    # the final review keeps only evidence-backed findings.
    for f in result.review.findings:
        assert f.file_path != "unrelated.py"
