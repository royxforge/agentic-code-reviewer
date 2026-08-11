"""Testing-analysis agent.

Assesses whether changed behaviour is tested, whether edge/negative cases exist,
and whether regression tests are warranted. Does not demand tests for trivial
changes without justification.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class TestingAgent(AnalysisAgent):
    name = "testing"
    category = "testing"
    prompt_version = "v2"  # behavioral-matrix upgrade (spec section 6)

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
