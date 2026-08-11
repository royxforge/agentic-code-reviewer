"""Error-handling-analysis agent.

Inspects uncaught exceptions, overly broad or swallowed exception handling,
retry/timeout gaps, resource leaks, partial failures, and invalid fallbacks.
Distinguishes actual bugs from potential concerns from style preferences  -  only
actual or materially risky issues become findings.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class ErrorHandlingAgent(AnalysisAgent):
    name = "error_handling"
    category = "error_handling"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
