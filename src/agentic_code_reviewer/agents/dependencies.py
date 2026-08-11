"""Dependencies / supply-chain analysis agent.

Assesses risk introduced by dependency and lockfile changes: unpinned ranges,
new attack surface, lockfile/manifest drift, unjustified new dependencies.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class DependenciesAgent(AnalysisAgent):
    name = "dependencies"
    category = "dependencies"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
