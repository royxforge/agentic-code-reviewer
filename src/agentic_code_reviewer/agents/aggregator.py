"""Aggregator agent.

Combines all stage findings into the final review: deterministic
deduplication, false-positive filtering and ranking, then an LLM pass that
writes a concise summary and may down-select findings. The LLM can only choose
from evidence-backed candidates  -  it can never invent findings.
"""

from __future__ import annotations

import json

from agentic_code_reviewer.agents.base import AgentRun, BaseAgent
from agentic_code_reviewer.analysis.aggregation import (
    cluster_findings,
    cross_check_llm_selection,
    dedupe_findings,
    filter_findings,
    rank_findings,
    stabilize_severities,
)
from agentic_code_reviewer.errors import ReviewValidationError
from agentic_code_reviewer.llm.llm_usage import empty_usage
from agentic_code_reviewer.models.review import Review
from agentic_code_reviewer.orchestration.state import ReviewState


class AggregatorAgent(BaseAgent):
    name = "aggregator"
    prompt_version = "v1"

    def run(self, state: ReviewState, *args: object) -> AgentRun:
        clustered = cluster_findings(dedupe_findings(state.findings))
        filtered = filter_findings(
            clustered,
            self.settings.min_finding_confidence,
            strictness=self.settings.verification_strictness,
        )
        ranked = rank_findings(stabilize_severities(filtered))

        summary = self._fallback_summary(state)
        final_findings = ranked
        usage = empty_usage()
        cost = 0.0
        calls = 0

        # Cap the findings serialized into the LLM prompt: the model can only
        # down-select from what it sees, and huge finding sets would blow the
        # aggregator budget. The full ranked list is kept for the fallback.
        cap = self.settings.aggregator_max_findings
        candidates = ranked[:cap] if cap > 0 else ranked

        if ranked:
            try:
                system, task = self._render(
                    FINDINGS=json.dumps(
                        [f.model_dump(mode="json") for f in candidates], indent=2
                    ),
                    PLAN=state.plan.model_dump_json() if state.plan else "{}",
                    CHANGE_SUMMARY=(
                        state.change_summary.model_dump_json()
                        if state.change_summary
                        else "{}"
                    ),
                    VERIFICATION=self._verification_summary(state),
                    REPOSITORY=state.request.repository,
                )
                llm_review, usage, cost, calls = self._structured(
                    Review,
                    system=system,
                    task=task,
                    max_tokens=4000,
                )
                final_findings = cross_check_llm_selection(
                    llm_review.findings, ranked
                )
                summary = llm_review.summary or summary
            except ReviewValidationError:
                # Never let aggregation fail the review: fall back to deterministic.
                final_findings = ranked

        review = Review(
            repository=state.request.repository,
            pull_request=state.request.pull_request,
            commit=state.request.commit,
            summary=summary,
            findings=final_findings,
            model=f"{self.client.provider}/{self.client.model}",
            prompt_versions=self._prompt_versions(state),
            confidence_threshold=self.settings.min_finding_confidence,
        )
        return self._run(review, usage, cost, calls)

    @staticmethod
    def _verification_summary(state: ReviewState) -> str:
        if not state.verification_results:
            return "(verification not performed)"
        lines = [
            f"- {r.finding_id}: {r.status.value} ({r.method})"
            for r in state.verification_results
        ]
        return "\n".join(lines)

    @staticmethod
    def _fallback_summary(state: ReviewState) -> str:
        if state.plan and state.plan.summary:
            return f"Change summary: {state.plan.summary}"
        return "No review summary could be generated."

    @staticmethod
    def _prompt_versions(state: ReviewState) -> dict[str, str]:
        from agentic_code_reviewer.llm.prompts import available_prompt_versions

        versions: dict[str, str] = {}
        for stage in (
            "planner",
            "change_analyzer",
            "correctness",
            "security",
            "error_handling",
            "testing",
            "regression",
            "aggregator",
        ):
            if available_prompt_versions(stage):
                versions[stage] = available_prompt_versions(stage)[-1]
        return versions
