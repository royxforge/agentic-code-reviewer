import pytest

from agentic_code_reviewer.errors import ReviewerError
from agentic_code_reviewer.orchestration.state import ReviewRequest
from agentic_code_reviewer.orchestration.workflow import Workflow
from tests.conftest import SQL_INJECTION_DIFF
from tests.integration.helpers import AdaptiveMock


def _request() -> ReviewRequest:
    return ReviewRequest(
        repository="o/r",
        diff_text=SQL_INJECTION_DIFF,
        changed_files=["db.py"],
        repo_files={"db.py": ""},
        source="inline",
    )


def test_planner_failure_is_fatal(mock_settings):
    workflow = Workflow(mock_settings, client=AdaptiveMock(fail_with={"planner"}))
    with pytest.raises(ReviewerError):
        workflow.run(_request())


def test_analysis_agent_failure_is_non_fatal(mock_settings):
    workflow = Workflow(mock_settings, client=AdaptiveMock(fail_with={"security"}))
    result = workflow.run(_request())
    assert result.review is not None
    assert result.stage_status["security"].startswith("failed")
    assert any(e.stage == "security" for e in result.agent_errors)
    # Other stages still ran.
    assert result.stage_status["planner"] == "ok"
    assert result.stage_status["correctness"] == "ok"


def test_timeout_is_recorded_and_review_completes(mock_settings):
    workflow = Workflow(mock_settings, client=AdaptiveMock(timeout_on={"correctness"}))
    result = workflow.run(_request())
    assert result.review is not None
    assert result.stage_status["correctness"].startswith("failed")
    assert any(e.error_type == "LLMTimeoutError" for e in result.agent_errors)


def test_invalid_aggregator_json_falls_back_to_deterministic(mock_settings):
    workflow = Workflow(mock_settings, client=AdaptiveMock(invalid_json_on={"aggregator"}))
    result = workflow.run(_request())
    assert result.review is not None
    # The deterministic aggregator path still delivers the verified finding.
    assert any(f.category == "security" for f in result.review.findings)


def test_all_agents_down_still_yields_review(mock_settings):
    """If every analysis agent fails, the review completes with zero findings."""
    workflow = Workflow(
        mock_settings,
        client=AdaptiveMock(
            fail_with={"security", "correctness", "error_handling", "testing", "regression"}
        ),
    )
    result = workflow.run(_request())
    assert result.review is not None
    assert result.review.findings == []
    assert result.stage_status["aggregator"] == "ok"
