"""Compatibility agent (spec section 1.4).

Detects incompatible serialized data, config changes, event/message schema
changes, old-client incompatibilities, renamed env vars, changed CLI args,
changed feature flags, changed cache formats, and rolling-deployment
incompatibilities. Inspects previous schemas, callers, serializers, consumers,
configuration, and repository history where available.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class CompatibilityAgent(AnalysisAgent):
    name = "compatibility"
    category = "compatibility"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
