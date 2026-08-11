"""Performance-analysis agent.

Finds material performance defects: algorithmic regressions, N+1 queries,
blocking calls in async paths, unbounded caches, redundant hot-path work.
Only issues with concrete cost are reported.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class PerformanceAgent(AnalysisAgent):
    name = "performance"
    category = "performance"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
