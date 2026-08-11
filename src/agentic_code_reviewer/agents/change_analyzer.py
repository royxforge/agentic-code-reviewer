"""Change-understanding agent.

Analyses the purpose and mechanics of the change: control flow, APIs, data
flow, state, dependencies, compatibility, and affected callers/tests. It
explicitly separates known facts from inferences.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, BaseAgent
from agentic_code_reviewer.analysis.context import render_context
from agentic_code_reviewer.models.schemas import ChangeSummary
from agentic_code_reviewer.orchestration.state import ReviewState


class ChangeAnalyzerAgent(BaseAgent):
    name = "change_analyzer"
    prompt_version = "v1"

    def run(self, state: ReviewState, *args: object) -> AgentRun:
        system, task = self._render(
            DIFF=state.request.diff_text,
            PLAN=state.plan.model_dump_json() if state.plan else "{}",
            CONTEXT=render_context(state.context_chunks),
            CHANGED_FILES=", ".join(state.request.changed_files),
            REPOSITORY=state.request.repository,
        )
        summary, usage, cost, calls = self._structured(
            ChangeSummary, system=system, task=task, max_tokens=3000
        )
        return self._run(summary, usage, cost, calls)

    def merge(self, summaries: list[ChangeSummary]) -> ChangeSummary:
        """Merge per-group summaries when the change was decomposed."""
        if not summaries:
            return ChangeSummary(purpose="(change analysis failed)")
        if len(summaries) == 1:
            return summaries[0]
        base = summaries[0]
        for other in summaries[1:]:
            base.purpose = f"{base.purpose} | {other.purpose}"
            base.control_flow_changes.extend(other.control_flow_changes)
            base.api_changes.extend(other.api_changes)
            base.data_flow_changes.extend(other.data_flow_changes)
            base.state_changes.extend(other.state_changes)
            base.dependency_changes.extend(other.dependency_changes)
            base.affected_callers.extend(other.affected_callers)
            base.affected_tests.extend(other.affected_tests)
            base.known_facts.extend(other.known_facts)
            base.inferences.extend(other.inferences)
        return base
