"""Correctness / bug-analysis agent.

Looks for concrete defects: wrong conditions, off-by-one errors, missing null
handling, incorrect state transitions, API misuse, races, resource leaks, type
mismatches and regression risks. Every finding must carry concrete evidence  -
vague "this might be a problem" findings are prohibited.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class CorrectnessAgent(AnalysisAgent):
    name = "correctness"
    category = "correctness"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
