"""Baseline systems for controlled evaluation.

    Baseline A  -  single-pass : diff -> LLM -> findings
    Baseline B  -  context     : diff + deliberate repo context -> LLM -> findings
    Baseline C  -  RAG         : diff + retrieval hits -> LLM -> findings
    System D     -  agentic    : the full multi-stage Workflow

All baselines share the same interface (:meth:`review`), so the evaluation
framework can compare them under identical conditions.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel, Field

from agentic_code_reviewer.agents.base import AgentRun, BaseAgent
from agentic_code_reviewer.analysis.context import render_context
from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.evaluation.benchmark import BenchmarkEntry
from agentic_code_reviewer.models.findings import ReviewFinding
from agentic_code_reviewer.models.review import ContextChunk
from agentic_code_reviewer.orchestration.state import ReviewRequest, ReviewState
from agentic_code_reviewer.retrieval.chunker import chunk_file
from agentic_code_reviewer.retrieval.retriever import Retriever


class FindingsList(BaseModel):
    findings: list[ReviewFinding] = Field(default_factory=list)


@dataclass
class BaselineResult:
    system: str
    findings: list[ReviewFinding]
    latency_seconds: float = 0.0
    cost_usd: float = 0.0
    tokens: int = 0
    success: bool = True
    error: str | None = None


class Baseline(ABC):
    name: str = "baseline"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = _build_client(settings)

    @abstractmethod
    def review(self, entry: BenchmarkEntry) -> BaselineResult:
        """Review a benchmark entry (the bug-introducing change)."""

    def _run(
        self,
        agent: BaseAgent,
        entry: BenchmarkEntry,
        diff_text: str,
        context: list[ContextChunk],
    ) -> BaselineResult:
        state = ReviewState(
            request=ReviewRequest(
                repository=entry.repository,
                commit=entry.commit,
                diff_text=diff_text,
                changed_files=entry.affected_files,
                repo_files=entry.base_files,
                source="benchmark",
            )
        )
        state.context_chunks = context
        start = time.monotonic()
        try:
            run: AgentRun = agent.run(state, diff_text)
            result: FindingsList = run.result  # type: ignore[assignment]
            return BaselineResult(
                system=self.name,
                findings=result.findings,
                latency_seconds=round(time.monotonic() - start, 3),
                cost_usd=run.cost,
                tokens=sum(run.usage.to_dict().values()),
                success=True,
            )
        except Exception as exc:  # noqa: BLE001 - baselines must not crash evaluation
            return BaselineResult(
                system=self.name,
                findings=[],
                latency_seconds=round(time.monotonic() - start, 3),
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )


class SinglePassBaseline(Baseline):
    """Baseline A: diff -> LLM -> findings."""

    name = "single-pass"

    def review(self, entry: BenchmarkEntry) -> BaselineResult:
        agent = _BaselineAgent("baseline_single", self.settings, self.client)
        return self._run(agent, entry, entry.diff, [])


class ContextBaseline(Baseline):
    """Baseline B: diff + deliberate context selection -> LLM -> findings."""

    name = "context"

    def review(self, entry: BenchmarkEntry) -> BaselineResult:
        chunks: list[ContextChunk] = []
        used = 0
        for path in entry.affected_files:
            content = entry.base_files.get(path)
            if content is None:
                continue
            for code_chunk in chunk_file(
                path, content,
                max_chars=self.settings.chunk_max_chars,
                overlap_chars=self.settings.chunk_overlap_chars,
            ):
                chunk = ContextChunk(
                    repository=entry.repository,
                    file_path=path,
                    symbol=code_chunk.symbol,
                    kind=code_chunk.kind,
                    start_line=code_chunk.start_line,
                    end_line=code_chunk.end_line,
                    text=code_chunk.text,
                    source="context_selector",
                )
                used += len(chunk.text)
                if used > self.settings.context_char_budget:
                    break
                chunks.append(chunk)
        agent = _BaselineAgent("baseline_context", self.settings, self.client)
        return self._run(agent, entry, entry.diff, chunks)


class RagBaseline(Baseline):
    """Baseline C: diff + retrieval hits -> LLM -> findings."""

    name = "rag"

    def review(self, entry: BenchmarkEntry) -> BaselineResult:
        chunks: list[ContextChunk] = []
        if entry.base_files:
            retriever = Retriever(self.settings, self.client)
            retriever.index_repository(entry.base_files, repository=entry.repository)
            query = f"{entry.repository}\n{' '.join(entry.affected_files)}\n{entry.diff[:2000]}"
            chunks = retriever.retrieve(query, top_k=self.settings.retrieval_top_k)
        agent = _BaselineAgent("baseline_rag", self.settings, self.client)
        return self._run(agent, entry, entry.diff, chunks)


class _BaselineAgent(BaseAgent):
    """Tiny agent wrapper so baselines reuse the structured-output pipeline."""

    def __init__(self, name: str, settings: Settings, client: object) -> None:
        self.name = name
        self.prompt_version = "v1"
        super().__init__(settings, client)  # type: ignore[arg-type]

    def run(self, state: ReviewState, *args: object) -> AgentRun:
        diff_text = str(args[0]) if args else state.request.diff_text
        context = render_context(state.context_chunks)
        system, task = self._render(
            DIFF=diff_text,
            CONTEXT=context,
            CHANGED_FILES=", ".join(state.request.changed_files),
            REPOSITORY=state.request.repository,
        )
        parsed, usage, cost, calls = self._structured(
            FindingsList, system=system, task=task, max_tokens=6000
        )
        return self._run(parsed, usage, cost, calls)


def _build_client(settings: Settings):
    from agentic_code_reviewer.llm.client import build_llm_client

    return build_llm_client(settings)


BASELINES: dict[str, type[Baseline]] = {
    "single-pass": SinglePassBaseline,
    "context": ContextBaseline,
    "rag": RagBaseline,
}
