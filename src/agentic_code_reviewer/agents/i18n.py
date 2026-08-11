"""Internationalization (i18n) analysis agent.

Finds localization defects in user-facing changes: hardcoded UI strings,
locale/timezone formatting assumptions, translation-breaking concatenation.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class I18nAgent(AnalysisAgent):
    name = "i18n"
    category = "i18n"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
