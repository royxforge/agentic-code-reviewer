"""Agent scaffolding.

Every stage agent receives the current :class:`ReviewState` and produces a
single :class:`AgentRun` containing the typed result plus usage/cost/calls so
the workflow keeps observability numbers even when stages run in parallel.
Prompts are loaded from the versioned ``prompts/`` tree.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.llm.client import BaseLLMClient, LLMUsage, current_stage
from agentic_code_reviewer.llm.cost import estimate_cost
from agentic_code_reviewer.llm.prompts import render
from agentic_code_reviewer.llm.structured_output import call_structured
from agentic_code_reviewer.models.schemas import AnalysisResult
from agentic_code_reviewer.orchestration.state import ReviewState

T = TypeVar("T", bound=BaseModel)


@dataclass
class AgentRun:
    """Everything a single agent execution produced."""

    result: BaseModel
    usage: LLMUsage = field(default_factory=LLMUsage)
    cost: float = 0.0
    calls: int = 0


class BaseAgent(ABC):
    name: str = "base"
    prompt_version: str = "v1"

    def __init__(self, settings: Settings, client: BaseLLMClient) -> None:
        self.settings = settings
        self.client = client

    @abstractmethod
    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        """Execute this stage against the current state.

        Agents that analyse a concrete diff (the analysis agents and the
        deterministic dead-code checker) consume ``diff_text`` directly;
        stage agents that only need ``state`` (planner, change analyzer,
        verifier, aggregator) accept it via ``*args`` in their overrides.
        """

    # -- helpers ---------------------------------------------------------
    def _structured(
        self,
        model: type[T],
        *,
        system: str,
        task: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[T, LLMUsage, float, int]:
        """Run one structured LLM call, returning (parsed, usage, cost, calls)."""
        token = current_stage.set(self.name)
        try:
            parsed, usage, calls = call_structured(
                self.client,
                model,
                system,
                task,
                temperature=temperature if temperature is not None else self.settings.temperature,
                max_tokens=max_tokens or self.settings.max_tokens,
                timeout=self.settings.llm_timeout_seconds,
                max_retries=1,
            )
        finally:
            current_stage.reset(token)
        cost = estimate_cost(self.client.provider, self.client.model, usage)
        return parsed, usage, cost, calls

    def _render(self, **kwargs: object) -> tuple[str, str]:
        return render(self.name, self.prompt_version, **kwargs)

    @staticmethod
    def _run(parsed: BaseModel, usage: LLMUsage, cost: float, calls: int) -> AgentRun:
        return AgentRun(result=parsed, usage=usage, cost=cost, calls=calls)


class AnalysisAgent(BaseAgent):
    """Shared behaviour for the analysis agents."""

    category: str = "general"

    def _assemble(
        self, state: ReviewState, diff_text: str, **extra: object
    ) -> tuple[str, str]:
        from agentic_code_reviewer.analysis.context import render_context

        plan = state.plan.model_dump_json() if state.plan else "{}"
        change_summary = (
            state.change_summary.model_dump_json() if state.change_summary else "{}"
        )
        return self._render(
            DIFF=diff_text,
            PLAN=plan,
            CHANGE_SUMMARY=change_summary,
            CONTEXT=render_context(state.context_chunks),
            CHANGED_FILES=", ".join(state.request.changed_files),
            REPOSITORY=state.request.repository,
            REQUIREMENT=state.request.description,
            HISTORY=self._history_digest(state),
            **extra,
        )

    def _callers_digest(self, state: ReviewState, max_symbols: int = 12) -> str:
        """Call sites of the changed symbols (from the shared snapshot)."""
        snapshot = getattr(state, "snapshot", None)
        if snapshot is None:
            return "(symbol index unavailable)"
        parts: list[str] = []
        for path in state.request.changed_files[:20]:
            for sym in snapshot.definitions_in_file(path)[:max_symbols]:
                callers = snapshot.callers_of(sym.name)
                if callers:
                    sites = ", ".join(f"{p}:{ln}" for p, ln in callers[:6])
                    parts.append(f"{sym.name} ({sym.kind}) <- {sites}")
        return "\n".join(parts) if parts else "(no repository callers found)"

    def _history_digest(self, state: ReviewState) -> str:
        """Compact git-history digest for the changed files (best-effort)."""
        snapshot = getattr(state, "snapshot", None)
        if snapshot is None or not snapshot.history_enabled:
            return ""
        parts: list[str] = []
        for path in state.request.changed_files[:8]:
            lines = snapshot.history_for(path)
            if lines:
                parts.append(f"{path}: {', '.join(lines)}")
        return "\n".join(parts) if parts else ""

    def _collect(
        self,
        state: ReviewState,
        diff_text: str,
        *,
        max_tokens: int | None = None,
        **extra: object,
    ) -> AgentRun:
        system, task = self._assemble(state, diff_text, **extra)
        parsed, usage, cost, calls = self._structured(
            AnalysisResult,
            system=system,
            task=task,
            max_tokens=max_tokens,
        )
        parsed.agent = self.name
        return self._run(parsed, usage, cost, calls)
