"""Observability / diagnostics analysis agent.

Finds failure paths that would be invisible in production: swallowed errors
without logging, dropped error context, missing signals on new async work.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class ObservabilityAgent(AnalysisAgent):
    name = "observability"
    category = "observability"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
