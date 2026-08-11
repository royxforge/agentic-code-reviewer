"""Authorization agent (spec section 1.1).

Looks for IDOR/BOLA, missing ownership checks, privilege escalation, tenant
isolation violations, missing authorization on newly added routes, checks that
run too late, insecure default permissions, and authorization logic that is
inconsistent with the repository's existing authorization architecture. Uses
repository evidence (snapshot patterns) to judge "existing patterns".
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class AuthorizationAgent(AnalysisAgent):
    name = "authorization"
    category = "authorization"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
