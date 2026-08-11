"""Planner agent.

Understands the diff, identifies affected components and risk areas, and decides
which analysis stages are required. It never produces review findings  -  its
output drives the rest of the workflow.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, BaseAgent
from agentic_code_reviewer.analysis.decomposition import should_decompose
from agentic_code_reviewer.analysis.diff import parse_diff
from agentic_code_reviewer.models.review import ALLOWED_CHECKS, ReviewPlan
from agentic_code_reviewer.orchestration.state import ReviewState


class PlannerAgent(BaseAgent):
    name = "planner"
    prompt_version = "v1"

    def run(self, state: ReviewState, *args: object) -> AgentRun:
        diff_text = state.request.diff_text
        system, task = self._render(
            DIFF=diff_text,
            CHANGED_FILES=", ".join(state.request.changed_files),
            REPOSITORY=state.request.repository,
            DECOMPOSITION_HINT=self._decomposition_hint(diff_text),
            REQUIREMENT=state.request.description,
        )
        plan, usage, cost, calls = self._structured(
            ReviewPlan, system=system, task=task, max_tokens=2000
        )
        # Decomposition is decided by our own token math, not the model.
        try:
            files = parse_diff(diff_text)
            plan.decomposition_required = should_decompose(
                files, self.settings.diff_token_budget
            )
        except Exception:  # noqa: BLE001 - never let planning die on diff issues
            plan.decomposition_required = False
        # Restrict checks to the known set (schema safety).
        plan.required_checks = [
            c for c in plan.required_checks if c in ALLOWED_CHECKS
        ] or ["correctness"]
        return self._run(plan, usage, cost, calls)

    def _decomposition_hint(self, diff_text: str) -> str:
        tokens = len(diff_text) // 4
        if tokens > self.settings.diff_token_budget:
            return (
                f"The diff is large (~{tokens} tokens). Mark required checks "
                "clearly; analysis will be decomposed per file group."
            )
        return ""
