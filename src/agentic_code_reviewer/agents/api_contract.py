"""API-contract / backward-compatibility analysis agent.

Finds breaking changes to public interfaces: signatures, exports, data shapes,
serialized formats, and the callers a change silently breaks.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class ApiContractAgent(AnalysisAgent):
    name = "api_contract"
    category = "api_contract"
    prompt_version = "v2"  # caller-aware upgrade (spec section 12)

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text, CALLERS=self._callers_digest(state))
