"""Maintainability / code-quality analysis agent.

Reports material maintainability risks only: dead or duplicated logic that will
drift, god functions, leaked abstractions. Style preferences are never reported.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class MaintainabilityAgent(AnalysisAgent):
    name = "maintainability"
    category = "maintainability"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
