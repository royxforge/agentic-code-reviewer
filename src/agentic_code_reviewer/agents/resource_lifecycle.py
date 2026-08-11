"""Resource-lifecycle agent (spec section 1.6).

Detects file/socket/connection leaks, goroutines/tasks that never terminate,
listeners never removed, subscriptions never cancelled, background jobs that
outlive their scope, uncleaned timers, leftover temp files, locks not
released, open transactions, and resources created repeatedly and retained
indefinitely. Pays attention to language/framework lifecycle conventions.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class ResourceLifecycleAgent(AnalysisAgent):
    name = "resource_lifecycle"
    category = "resource_lifecycle"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
