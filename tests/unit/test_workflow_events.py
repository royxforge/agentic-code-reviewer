from __future__ import annotations

from agentic_code_reviewer.orchestration.events import WorkflowEvent
from agentic_code_reviewer.orchestration.state import ReviewRequest
from agentic_code_reviewer.orchestration.workflow import Workflow
from tests.integration.helpers import AdaptiveMock


def _request(
    sql_diff: str,
    repo_files: dict[str, str],
    *,
    description: str = "",
) -> ReviewRequest:
    return ReviewRequest(
        repository="o/r",
        diff_text=sql_diff,
        changed_files=["db.py", "app.py"],
        repo_files=repo_files,
        source="inline",
        description=description,
    )


def test_workflow_emits_lifecycle_events(mock_settings, sql_injection_diff, sample_repo_files):
    events: list[WorkflowEvent] = []

    workflow = Workflow(
        mock_settings,
        client=AdaptiveMock(),
        event_sink=events.append,
    )
    result = workflow.run(_request(sql_injection_diff, sample_repo_files))

    kinds = [e.kind for e in events]

    # Stage lifecycle: each stage must announce start and completion.
    assert "stage_start" in kinds
    assert "stage_done" in kinds
    assert kinds.count("stage_start") == kinds.count("stage_done")

    # The planner requests security+correctness for this diff; every requested
    # stage must emit a done event, plus planner/change_analyzer/verifier/aggregator.
    done_stages = {e.stage for e in events if e.kind == "stage_done"}
    for expected in (
        "planner", "change_analyzer", "correctness", "security",
        "verifier", "aggregator",
    ):
        assert expected in done_stages, done_stages

    # Usage events stream cumulative counters and finish carries the result.
    usage = [e for e in events if e.kind == "usage"]
    assert usage, "expected usage events"
    assert usage[-1].payload["calls"] == result.llm_call_count
    assert usage[-1].payload["total_tokens"] == sum(result.token_usage.values())

    # The security agent found the SQL injection finding  -  streamed live.
    findings = [e for e in events if e.kind == "finding"]
    assert findings
    assert any(e.payload.category == "security" for e in findings)

    finish = [e for e in events if e.kind == "finish"]
    assert len(finish) == 1
    assert finish[0].payload is result
    assert finish[0].payload.review.repository == "o/r"


def test_new_analysis_agents_run_when_planner_requests_them(
    mock_settings, sql_injection_diff, sample_repo_files
):
    """All 17 categories run in parallel when the planner asks for them."""
    import json

    from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage, current_stage

    all_checks = [
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
        "dead_code",
    ]
    events: list[WorkflowEvent] = []

    class AllChecksMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            if current_stage.get() == "planner":
                plan = {
                    "summary": "change under review",
                    "affected_files": [],
                    "affected_components": [],
                    "risk_areas": [],
                    "required_checks": all_checks,
                    "decomposition_required": False,
                    "decomposition_notes": "",
                }
                return LLMResponse(
                    content=json.dumps(plan),
                    usage=LLMUsage(input_tokens=10, output_tokens=10),
                )
            return super().complete(messages, **kwargs)

    workflow = Workflow(
        mock_settings, client=AllChecksMock(), event_sink=events.append
    )
    result = workflow.run(
        _request(
            sql_injection_diff,
            sample_repo_files,
            description="Add search that filters users by name",
        )
    )

    done = {e.stage for e in events if e.kind == "stage_done"}
    for check in all_checks:
        assert check in done, done
    assert result.review is not None


def test_ablation_switch_disables_new_agent(
    mock_settings, sql_injection_diff, sample_repo_files
):
    """WORKFLOW_USE_PERFORMANCE_AGENT=false stops the performance stage."""
    import json

    from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage, current_stage

    events: list[WorkflowEvent] = []

    class PerfRequestingMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            if current_stage.get() == "planner":
                plan = {
                    "summary": "change under review",
                    "affected_files": [],
                    "affected_components": [],
                    "risk_areas": [],
                    "required_checks": ["performance", "security"],
                    "decomposition_required": False,
                    "decomposition_notes": "",
                }
                return LLMResponse(
                    content=json.dumps(plan),
                    usage=LLMUsage(input_tokens=10, output_tokens=10),
                )
            return super().complete(messages, **kwargs)

    settings = mock_settings.model_copy(
        update={"workflow_use_performance_agent": False}
    )
    workflow = Workflow(settings, client=PerfRequestingMock(), event_sink=events.append)
    workflow.run(_request(sql_injection_diff, sample_repo_files))

    done = {e.stage for e in events if e.kind == "stage_done"}
    assert "performance" not in done
    assert "security" in done  # unrelated agents still run


def test_requirement_alignment_skipped_without_description(
    mock_settings, sql_injection_diff, sample_repo_files
):
    """requirement_alignment never runs when no stated requirement exists."""
    import json

    from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage, current_stage

    events: list[WorkflowEvent] = []

    class ReqRequestingMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            if current_stage.get() == "planner":
                plan = {
                    "summary": "change under review",
                    "affected_files": [],
                    "affected_components": [],
                    "risk_areas": [],
                    "required_checks": ["requirement_alignment", "security"],
                    "decomposition_required": False,
                    "decomposition_notes": "",
                }
                return LLMResponse(
                    content=json.dumps(plan),
                    usage=LLMUsage(input_tokens=10, output_tokens=10),
                )
            return super().complete(messages, **kwargs)

    workflow = Workflow(
        mock_settings, client=ReqRequestingMock(), event_sink=events.append
    )
    workflow.run(_request(sql_injection_diff, sample_repo_files))  # description=""

    done = {e.stage for e in events if e.kind == "stage_done"}
    assert "requirement_alignment" not in done
    assert "security" in done  # unrelated checks still run


def test_requirement_alignment_runs_when_description_provided(
    mock_settings, sql_injection_diff, sample_repo_files
):
    """With a stated requirement the check runs and the planner sees it."""
    import json

    from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage, current_stage

    events: list[WorkflowEvent] = []
    seen: list[str] = []

    class ReqProvidingMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            stage = current_stage.get()
            if stage == "planner":
                seen.append("\n".join(m.get("content", "") for m in messages))
                plan = {
                    "summary": "change under review",
                    "affected_files": [],
                    "affected_components": [],
                    "risk_areas": [],
                    "required_checks": ["requirement_alignment"],
                    "decomposition_required": False,
                    "decomposition_notes": "",
                }
                return LLMResponse(
                    content=json.dumps(plan),
                    usage=LLMUsage(input_tokens=10, output_tokens=10),
                )
            return super().complete(messages, **kwargs)

    workflow = Workflow(
        mock_settings, client=ReqProvidingMock(), event_sink=events.append
    )
    workflow.run(
        _request(
            sql_injection_diff,
            sample_repo_files,
            description="Search must filter by exact name match",
        )
    )

    done = {e.stage for e in events if e.kind == "stage_done"}
    assert "requirement_alignment" in done
    assert seen and "Search must filter by exact name match" in seen[0]


def test_dead_code_runs_even_when_planner_does_not_ask(
    mock_settings, sql_injection_diff, sample_repo_files
):
    """The deterministic dead-code check is always-on and never requires an LLM."""
    import json

    from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage, current_stage

    events: list[WorkflowEvent] = []

    class NoDeadCodeMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            if current_stage.get() == "planner":
                plan = {
                    "summary": "change under review",
                    "affected_files": [],
                    "affected_components": [],
                    "risk_areas": [],
                    "required_checks": ["security"],
                    "decomposition_required": False,
                    "decomposition_notes": "",
                }
                return LLMResponse(
                    content=json.dumps(plan),
                    usage=LLMUsage(input_tokens=10, output_tokens=10),
                )
            return super().complete(messages, **kwargs)

    workflow = Workflow(
        mock_settings, client=NoDeadCodeMock(), event_sink=events.append
    )
    workflow.run(_request(sql_injection_diff, sample_repo_files))

    done = {e.stage for e in events if e.kind == "stage_done"}
    assert "dead_code" in done


def test_ablation_switch_disables_dead_code_checker(
    mock_settings, sql_injection_diff, sample_repo_files
):
    """WORKFLOW_USE_DEAD_CODE_CHECKER=false stops the deterministic check."""
    import json

    from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage, current_stage

    events: list[WorkflowEvent] = []

    class ReqMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            if current_stage.get() == "planner":
                plan = {
                    "summary": "change under review",
                    "affected_files": [],
                    "affected_components": [],
                    "risk_areas": [],
                    "required_checks": ["security"],
                    "decomposition_required": False,
                    "decomposition_notes": "",
                }
                return LLMResponse(
                    content=json.dumps(plan),
                    usage=LLMUsage(input_tokens=10, output_tokens=10),
                )
            return super().complete(messages, **kwargs)

    settings = mock_settings.model_copy(update={"workflow_use_dead_code_checker": False})
    workflow = Workflow(settings, client=ReqMock(), event_sink=events.append)
    workflow.run(_request(sql_injection_diff, sample_repo_files))

    done = {e.stage for e in events if e.kind == "stage_done"}
    assert "dead_code" not in done


def test_workflow_emits_failure_events_for_stage_failures(
    mock_settings, sql_injection_diff, sample_repo_files
):
    events: list[WorkflowEvent] = []

    class FailingMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            from agentic_code_reviewer.llm.client import current_stage

            if current_stage.get() == "security":
                raise RuntimeError("boom")
            return super().complete(messages, **kwargs)

    workflow = Workflow(
        mock_settings,
        client=FailingMock(),
        event_sink=events.append,
    )
    result = workflow.run(_request(sql_injection_diff, sample_repo_files))

    failed = [e for e in events if e.kind == "stage_failed"]
    assert any(e.stage == "security" for e in failed)
    assert "boom" in failed[0].message

    # Failure of one analysis stage must not kill the review.
    assert result.review is not None
    assert any(e.kind == "finish" for e in events)
