"""Requirement-alignment analysis agent.

Checks the change against the stated requirement/PR description: is the
intended behaviour implemented, is there scope creep, are named acceptance
criteria covered?
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class RequirementAlignmentAgent(AnalysisAgent):
    name = "requirement_alignment"
    category = "requirement_alignment"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
