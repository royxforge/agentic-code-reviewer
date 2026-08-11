"""Concurrency analysis agent.

Finds races, check-then-act and mutation-during-iteration bugs, async pitfalls,
lock-ordering risk, and non-atomic compound operations on shared state.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class ConcurrencyAgent(AnalysisAgent):
    name = "concurrency"
    category = "concurrency"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
