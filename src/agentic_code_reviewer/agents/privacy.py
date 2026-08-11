"""Privacy / compliance analysis agent.

Finds personal data leaking into logs, errors, analytics or storage, and
missing access control / audit trails around sensitive operations.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class PrivacyAgent(AnalysisAgent):
    name = "privacy"
    category = "privacy"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
