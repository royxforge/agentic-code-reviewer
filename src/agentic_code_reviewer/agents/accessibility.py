"""Accessibility analysis agent.

Reviews frontend/HTML changes for accessibility defects: missing labels/alt,
non-keyboard-operable elements, focus traps, colour-only meaning.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class AccessibilityAgent(AnalysisAgent):
    name = "accessibility"
    category = "accessibility"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
