"""Data / schema integrity analysis agent.

Inspects migrations, validation, serialization compatibility, partial writes,
and mutation-during-iteration for data-corruption risk.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class DataIntegrityAgent(AnalysisAgent):
    name = "data_integrity"
    category = "data_integrity"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
