"""Reliability / resilience agent (spec section 1.2).

Detects retry storms, retries without backoff, retries of non-idempotent
operations, duplicate processing, missing idempotency, cascading failures,
failure amplification, ack problems, dependency failure propagation, timeout
mismatches, and missing graceful degradation. Reasons about operation
semantics rather than flagging every retry loop.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class ReliabilityAgent(AnalysisAgent):
    name = "reliability"
    category = "reliability"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
