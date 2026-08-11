"""Regression / bug-risk agent.

Focuses on regressions and backward-compatibility risks: changed APIs, removed
behaviour, changed default values, newly unreachable branches, and callers that
the change could silently break.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class RegressionAgent(AnalysisAgent):
    name = "regression"
    category = "regression"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
