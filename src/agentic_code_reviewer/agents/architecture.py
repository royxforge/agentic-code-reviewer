"""Architecture / design agent (spec section 1.3).

Reports only *material* architectural defects: bypassed abstractions, violated
module boundaries, circular dependencies, business logic in the wrong layer,
duplicated capabilities, inappropriate global state, excessive coupling, and
new direct dependencies that bypass existing services. Never a general
"I don't like this design" reviewer  -  every finding needs concrete
maintainability, correctness, security, reliability or operational risk and
repository-structure evidence.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class ArchitectureAgent(AnalysisAgent):
    name = "architecture"
    category = "architecture"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
