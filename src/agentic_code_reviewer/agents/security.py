"""Security-analysis agent.

Inspects the change for security defects (injection, XSS, auth bypass, secret
exposure, unsafe subprocess/deserialization, insecure crypto, prompt-injection
risks in AI systems, ...). Only evidence-backed issues are reported; findings
carry a line reference into the diff.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class SecurityAgent(AnalysisAgent):
    name = "security"
    category = "security"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
