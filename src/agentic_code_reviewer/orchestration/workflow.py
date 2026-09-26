"""Workflow orchestrator.

Runs the multi-step agentic pipeline over a :class:`ReviewRequest`:

    planner -> change_analyzer -> [analysis agents in parallel] -> verifier
           -> aggregator

Design decisions:
- No orchestration framework: an explicit state machine is sufficient and keeps
  the system understandable (see docs/architecture.md for the reasoning).
- Analysis agents run in parallel (independent, read-only on state).
- Every stage has a wall-clock timeout, retries on the LLM layer, structured
  error recording, and is non-fatal except for the planner (aborting a review
  without a plan is safer than reviewing blind).
- Findings are never silently dropped by the orchestrator: failure of an
  analysis stage degrades that check, never the whole review.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from functools import partial
from typing import TypeVar, cast

from agentic_code_reviewer.agents.accessibility import AccessibilityAgent
from agentic_code_reviewer.agents.aggregator import AggregatorAgent
from agentic_code_reviewer.agents.api_contract import ApiContractAgent
from agentic_code_reviewer.agents.architecture import ArchitectureAgent
from agentic_code_reviewer.agents.authorization import AuthorizationAgent
from agentic_code_reviewer.agents.base import AgentRun, BaseAgent
from agentic_code_reviewer.agents.change_analyzer import ChangeAnalyzerAgent
from agentic_code_reviewer.agents.compatibility import CompatibilityAgent
from agentic_code_reviewer.agents.concurrency import ConcurrencyAgent
from agentic_code_reviewer.agents.configuration import ConfigurationAgent
from agentic_code_reviewer.agents.correctness import CorrectnessAgent
from agentic_code_reviewer.agents.data_integrity import DataIntegrityAgent
from agentic_code_reviewer.agents.dead_code import DeadCodeChecker
from agentic_code_reviewer.agents.dependencies import DependenciesAgent
from agentic_code_reviewer.agents.error_handling import ErrorHandlingAgent
from agentic_code_reviewer.agents.i18n import I18nAgent
from agentic_code_reviewer.agents.maintainability import MaintainabilityAgent
from agentic_code_reviewer.agents.observability import ObservabilityAgent
from agentic_code_reviewer.agents.performance import PerformanceAgent
from agentic_code_reviewer.agents.planner import PlannerAgent
from agentic_code_reviewer.agents.privacy import PrivacyAgent
from agentic_code_reviewer.agents.regression import RegressionAgent
from agentic_code_reviewer.agents.reliability import ReliabilityAgent
from agentic_code_reviewer.agents.requirement_alignment import RequirementAlignmentAgent
from agentic_code_reviewer.agents.resource_lifecycle import ResourceLifecycleAgent
from agentic_code_reviewer.agents.security import SecurityAgent
from agentic_code_reviewer.agents.testing import TestingAgent
from agentic_code_reviewer.agents.verifier import VerifierAgent
from agentic_code_reviewer.analysis.aggregation import (
    cluster_findings,
    interaction_findings,
    merge_group_findings,
)
from agentic_code_reviewer.analysis.context import build_context
from agentic_code_reviewer.analysis.decomposition import (
    describe_groups,
    group_files,
    should_decompose,
)
from agentic_code_reviewer.analysis.diff import (
    DiffFile,
    diff_text_for_category,
    diff_to_text,
    parse_diff,
)
from agentic_code_reviewer.analysis.snapshot import RepositorySnapshot
from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import ReviewerError
from agentic_code_reviewer.knowledge.capture import capture_review_findings
from agentic_code_reviewer.knowledge.dismiss import suppressed_by_dismissals
from agentic_code_reviewer.knowledge.models import KnowledgeKind
from agentic_code_reviewer.knowledge.store import KnowledgeStore
from agentic_code_reviewer.llm.client import BaseLLMClient, build_llm_client
from agentic_code_reviewer.models.findings import AgentError
from agentic_code_reviewer.models.review import Review, ReviewPlan
from agentic_code_reviewer.models.schemas import (
    AnalysisResult,
    ChangeSummary,
    VerifierOutput,
    WorkflowResult,
)
from agentic_code_reviewer.observability.logging import (
    get_logger,
    new_correlation_id,
    set_correlation_id,
)
from agentic_code_reviewer.orchestration.events import (
    NOOP_SINK,
    EventSink,
    WorkflowEvent,
)
from agentic_code_reviewer.orchestration.state import ReviewRequest, ReviewState
from agentic_code_reviewer.retrieval.retriever import Retriever

log = get_logger("workflow")

T = TypeVar("T", bound=AgentRun)

# check name -> agent factory (agents are subclasses of AnalysisAgent)
_ANALYSIS_AGENTS: dict[str, type[BaseAgent]] = {
    "correctness": CorrectnessAgent,
    "security": SecurityAgent,
    "error_handling": ErrorHandlingAgent,
    "testing": TestingAgent,
    "regression": RegressionAgent,
    "performance": PerformanceAgent,
    "maintainability": MaintainabilityAgent,
    "observability": ObservabilityAgent,
    "data_integrity": DataIntegrityAgent,
    "accessibility": AccessibilityAgent,
    "concurrency": ConcurrencyAgent,
    "dependencies": DependenciesAgent,
    "privacy": PrivacyAgent,
    "i18n": I18nAgent,
    "api_contract": ApiContractAgent,
    "requirement_alignment": RequirementAlignmentAgent,
    "authorization": AuthorizationAgent,
    "reliability": ReliabilityAgent,
    "architecture": ArchitectureAgent,
    "compatibility": CompatibilityAgent,
    "configuration": ConfigurationAgent,
    "resource_lifecycle": ResourceLifecycleAgent,
    "dead_code": DeadCodeChecker,
}

# check name -> settings ablation switch (None = always on)
_CHECK_SWITCHES: dict[str, str] = {
    "security": "workflow_use_security_agent",
    "testing": "workflow_use_testing_agent",
    "performance": "workflow_use_performance_agent",
    "maintainability": "workflow_use_maintainability_agent",
    "observability": "workflow_use_observability_agent",
    "data_integrity": "workflow_use_data_integrity_agent",
    "accessibility": "workflow_use_accessibility_agent",
    "concurrency": "workflow_use_concurrency_agent",
    "dependencies": "workflow_use_dependencies_agent",
    "privacy": "workflow_use_privacy_agent",
    "i18n": "workflow_use_i18n_agent",
    "api_contract": "workflow_use_api_contract_agent",
    "requirement_alignment": "workflow_use_requirement_alignment_agent",
    "authorization": "workflow_use_authorization_agent",
    "reliability": "workflow_use_reliability_agent",
    "architecture": "workflow_use_architecture_agent",
    "compatibility": "workflow_use_compatibility_agent",
    "configuration": "workflow_use_configuration_agent",
    "resource_lifecycle": "workflow_use_resource_lifecycle_agent",
    "dead_code": "workflow_use_dead_code_checker",
}


class Workflow:
    def __init__(
        self,
        settings: Settings,
        client: BaseLLMClient | None = None,
        retriever: Retriever | None = None,
        event_sink: EventSink | None = None,
        knowledge_store: KnowledgeStore | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or build_llm_client(settings)
        self.retriever = retriever
        self._sink = event_sink or NOOP_SINK
        # Persistent repo memory; built lazily (only when enabled) so a missing
        # or unconfigured store never affects a review.
        self.knowledge_store = (
            knowledge_store
            if knowledge_store is not None
            else (
                KnowledgeStore(settings.knowledge_dir or None)
                if settings.knowledge_enabled
                else None
            )
        )

        self.planner = PlannerAgent(settings, self.client)
        self.change_analyzer = ChangeAnalyzerAgent(settings, self.client)
        self.verifier = VerifierAgent(settings, self.client)
        self.aggregator = AggregatorAgent(settings, self.client)
        # Per-category model-override clients (CATEGORY_MODELS), built lazily.
        self._category_clients: dict[str, BaseLLMClient] = {}

    def _emit(self, event: WorkflowEvent) -> None:
        """Forward a lifecycle event to the (optional) UI sink, never raising."""
        try:
            self._sink(event)
        except Exception:  # noqa: BLE001 - the UI must never break the review
            pass

    # ------------------------------------------------------------------
    def run(self, request: ReviewRequest) -> WorkflowResult:
        review_id = new_correlation_id()
        set_correlation_id(review_id)
        state = ReviewState(request=request)

        # Shared repository snapshot: built ONCE, queried by every stage
        # (verifier, dead-code checker, api-contract caller analysis, ...).
        try:
            state.snapshot = RepositorySnapshot(
                self._repository_files(state),
                changed_files=request.changed_files,
                local_path=request.local_path,
                history_enabled=self.settings.history_analysis,
            )
        except Exception as exc:  # noqa: BLE001 - snapshot must never break a review
            state.record_error(
                AgentError(
                    agent="snapshot",
                    stage="snapshot",
                    error_type=type(exc).__name__,
                    message=str(exc)[:500],
                )
            )
            log.warning("workflow.snapshot_failed", extra={"error": type(exc).__name__})
        log.info(
            "workflow.start",
            extra={"repository": request.repository, "source": request.source},
        )

        # 1. Planner (fatal)
        run = self._run_stage(state, "planner", lambda: self.planner.run(state), fatal=True)
        assert run is not None
        self._record_usage(state, run)
        state.plan = cast(ReviewPlan, run.result)
        self._emit(WorkflowEvent(kind="plan", stage="planner", payload=state.plan))

        # 2. Decomposition decision (deterministic token math)
        diff_files = parse_diff(request.diff_text)
        groups = self._diff_groups(diff_files, state)
        if len(groups) > 1:
            self._emit(
                WorkflowEvent(
                    kind="decomposed",
                    stage="decomposition",
                    payload=len(groups),
                    message=f"diff decomposed into {len(groups)} groups",
                )
            )

        # 3. Change understanding (per group when decomposed)
        self._run_change_analysis(state, groups)

        # 4. Context (retrieval or deliberate context selector)
        self._build_context(state)

        # 4b. Knowledgebase (persistent repo memory)  -  injected via $KNOWLEDGE$
        self._load_knowledge(state)

        # 5. Analysis agents (parallel across checks x groups)
        self._run_analysis_agents(state, groups)

        # 6. Evidence verification (deterministic, layered)
        self._run_verifier(state)

        # 7. Cross-category correlation + interaction analysis
        self._run_correlation(state)

        # 7b. Suppress findings dismissed in past reviews (repo memory)
        self._apply_dismissals(state)

        # 8. Aggregation (deterministic + LLM)
        self._run_aggregator(state)

        # 8b. Auto-capture verified findings into the knowledgebase (opt-in)
        self._capture_knowledge(state)

        result = WorkflowResult(
            review=state.final_review,  # type: ignore[arg-type]
            stage_status=state.stage_status,
            agent_errors=state.errors,
            latency_seconds=state.latency_seconds,
            token_usage=state.token_usage,
            estimated_cost_usd=round(state.estimated_cost_usd, 6),
            llm_call_count=state.llm_call_count,
        )
        log.info(
            "workflow.finish",
            extra={
                "stages": state.stage_status,
                "findings": len(result.review.findings) if result.review else 0,
                "cost": result.estimated_cost_usd,
            },
        )
        self._emit(WorkflowEvent(kind="finish", payload=result))
        return result

    # ------------------------------------------------------------------
    def _run_stage(
        self,
        state: ReviewState,
        name: str,
        fn: Callable[[], T],
        *,
        fatal: bool = False,
        timeout: float | None = None,
    ) -> T | None:
        """Run one stage with a hard wall-clock timeout.

        The worker thread is deliberately *not* joined on timeout: the executor
        is shut down with ``wait=False`` so a hung task (whose underlying LLM
        call still has its own HTTP timeout) can never extend the stage beyond
        its budget.
        """
        budget = timeout or self.settings.stage_timeout_seconds
        start = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(fn)
        self._emit(WorkflowEvent.stage_start(name))
        try:
            result = future.result(timeout=budget)
            state.record_stage(name, "ok", time.monotonic() - start)
            self._emit(WorkflowEvent.stage_done(name, time.monotonic() - start))
            return result
        except Exception as exc:  # noqa: BLE001 - stage failure is handled here
            elapsed = time.monotonic() - start
            if isinstance(exc, FutureTimeout):
                error_type = "AgentTimeoutError"
                message = f"stage {name} exceeded {budget}s"
            else:
                error_type = type(exc).__name__
                message = str(exc)[:500]
            state.record_stage(name, f"failed:{error_type}", elapsed)
            state.record_error(
                AgentError(agent=name, stage=name, error_type=error_type, message=message)
            )
            log.warning("workflow.stage_failed", extra={"stage": name, "error": error_type})
            self._emit(WorkflowEvent.stage_failed(name, elapsed, message))
            if fatal:
                pool.shutdown(wait=False, cancel_futures=True)
                raise ReviewerError(f"fatal stage failure in {name}: {message}") from exc
            return None
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _diff_groups(self, diff_files: list[DiffFile], state: ReviewState) -> list[list[DiffFile]]:
        if should_decompose(diff_files, self.settings.diff_token_budget):
            groups = group_files(diff_files, self.settings.diff_token_budget)
            if state.plan:
                state.plan.decomposition_required = True
                state.plan.decomposition_notes = describe_groups(groups)
            log.info(
                "workflow.decompose",
                extra={"groups": len(groups), "files": len(diff_files)},
            )
            return groups
        return [diff_files]

    def _run_change_analysis(self, state: ReviewState, groups: list[list[DiffFile]]) -> None:
        if len(groups) == 1:
            run = self._run_stage(
                state, "change_analyzer", lambda: self.change_analyzer.run(state)
            )
            if run is not None:
                self._record_usage(state, run)
                state.change_summary = cast(ChangeSummary, run.result)
        else:
            start = time.monotonic()
            tasks = [partial(self.change_analyzer.run, state, diff_to_text(g)) for g in groups]
            runs = self._run_parallel(state, "change_analyzer", tasks)
            summaries = [cast(ChangeSummary, run.result) for run in runs if run is not None]
            for run in runs:
                self._record_usage(state, run)
            state.change_summary = self.change_analyzer.merge(summaries)
            status = "ok" if summaries else "failed:ChangeAnalysis"
            state.record_stage("change_analyzer", status, time.monotonic() - start)

    def _load_knowledge(self, state: ReviewState) -> None:
        """Load the knowledgebase entries relevant to this review into state."""
        if self.knowledge_store is None:
            return
        try:
            from agentic_code_reviewer.analysis.context import build_query

            query = self._knowledge_query(state, build_query(state))
            state.knowledge_entries = self.knowledge_store.relevant(
                query,
                state.request.repository,
                top_k=self.settings.knowledge_top_k,
            )
        except Exception as exc:  # noqa: BLE001 - knowledge must never break a review
            state.knowledge_entries = []
            log.warning("workflow.knowledge_failed", extra={"error": type(exc).__name__})
        log.info("workflow.knowledge", extra={"entries": len(state.knowledge_entries)})

    @staticmethod
    def _knowledge_query(state: ReviewState, base_query: str) -> str:
        """A retrieval query grounded in the change itself.

        The query is used only for local token-overlap scoring (never sent to
        the LLM), so including the diff text is free and makes relevance far
        better: an entry about "f-string SQL" matches a diff containing one.
        """
        added: list[str] = []
        try:
            for diff_file in parse_diff(state.request.diff_text):
                added.extend(ln.text for ln in diff_file.added_lines)
        except Exception:  # noqa: BLE001 - best-effort query enrichment
            pass
        parts = [base_query, *added]
        return "\n".join(parts)[:8000]

    def _apply_dismissals(self, state: ReviewState) -> None:
        """Drop findings that match a user-dismissed entry from past reviews."""
        if self.knowledge_store is None or not state.findings:
            return
        try:
            dismissals = [
                e
                for e in self.knowledge_store.entries(state.request.repository)
                if e.kind == KnowledgeKind.DISMISSED
            ]
            if not dismissals:
                return
            kept = suppressed_by_dismissals(
                state.findings, dismissals, repository=state.request.repository
            )
            dropped = len(state.findings) - len(kept)
            if dropped:
                state.findings = kept
                log.info(
                    "workflow.dismissals",
                    extra={"suppressed": dropped, "kept": len(kept)},
                )
        except Exception as exc:  # noqa: BLE001 - dismissals must never break a review
            log.warning(
                "workflow.dismissals_failed",
                extra={"error": type(exc).__name__},
            )

    def _capture_knowledge(self, state: ReviewState) -> None:
        """Persist verified findings back into the knowledgebase (opt-in)."""
        if (
            self.knowledge_store is None
            or not self.settings.knowledge_auto_capture
            or not state.findings
        ):
            return
        try:
            captured = capture_review_findings(
                self.knowledge_store,
                state.findings,
                state.request.repository,
            )
            log.info("workflow.knowledge_captured", extra={"entries": captured})
        except Exception as exc:  # noqa: BLE001 - capture must never break a review
            log.warning(
                "workflow.knowledge_capture_failed",
                extra={"error": type(exc).__name__},
            )

    def _build_context(self, state: ReviewState) -> None:
        retriever = self._build_retriever(state)
        try:
            state.context_chunks = build_context(state, self.settings, retriever)
        except Exception as exc:  # noqa: BLE001 - context failure must not kill review
            state.record_error(
                AgentError(
                    agent="context",
                    stage="context",
                    error_type=type(exc).__name__,
                    message=str(exc)[:500],
                )
            )
            log.warning("workflow.context_failed", extra={"error": type(exc).__name__})
            state.context_chunks = []
        log.info("workflow.context", extra={"chunks": len(state.context_chunks)})
        self._emit(
            WorkflowEvent(
                kind="context",
                stage="context",
                payload=len(state.context_chunks),
                message=f"{len(state.context_chunks)} context chunk(s) selected",
            )
        )

    def _build_retriever(self, state: ReviewState) -> Retriever | None:
        if not self.settings.retrieval_enabled or not self.settings.workflow_use_retrieval:
            return None
        try:
            files = self._repository_files(state)
            if not files:
                return None
            if self.retriever is not None:
                if not self.retriever.is_ready:
                    self.retriever.index_repository(files, repository=state.request.repository)
                return self.retriever
            retriever = Retriever(self.settings, self.client)
            retriever.index_repository(files, repository=state.request.repository)
            return retriever
        except Exception as exc:  # noqa: BLE001
            log.warning("workflow.retrieval_disabled", extra={"error": type(exc).__name__})
            return None

    @staticmethod
    def _repository_files(state: ReviewState) -> dict[str, str]:
        if state.request.repo_files:
            return state.request.repo_files
        if state.request.local_path:
            from agentic_code_reviewer.github.adapter import read_local_repository

            return read_local_repository(state.request.local_path)
        return {}

    def _run_analysis_agents(self, state: ReviewState, groups: list[list[DiffFile]]) -> None:
        if state.plan is None:
            return
        checks = [
            c
            for c in state.plan.required_checks
            if c in _ANALYSIS_AGENTS and self._check_enabled(c) and self._check_applicable(c, state)
        ]
        # The dead-code check is deterministic and free: always run it when
        # enabled, regardless of what the planner chose.
        if self._check_enabled("dead_code") and "dead_code" not in checks:
            checks.append("dead_code")
        if not checks:
            return
        tasks: list[Callable[[], AgentRun]] = []
        task_names: list[str] = []
        for check in checks:
            agent = _ANALYSIS_AGENTS[check](self.settings, self._client_for_category(check))
            for group in groups:
                task_names.append(check)
                # Category-focused diff: each agent only sees the hunks its
                # deterministic patterns care about, cutting per-agent tokens
                # (the full diff is still what the verifier checks against).
                category = getattr(agent, "category", check)
                tasks.append(partial(agent.run, state, diff_text_for_category(group, category)))
        start = time.monotonic()
        runs = self._run_parallel(state, "analysis", tasks, task_names=task_names)
        elapsed = time.monotonic() - start
        failed_checks: set[str] = set()
        for name, run in zip(task_names, runs, strict=False):
            if run is None:
                failed_checks.add(name)
                continue
            analysis = cast(AnalysisResult, run.result)
            state.findings.extend(analysis.findings)
            for finding in analysis.findings:
                self._emit(
                    WorkflowEvent(kind="finding", stage=name, payload=finding)
                )
            self._record_usage(state, run)
        for check in checks:
            status = "failed:AgentTask" if check in failed_checks else "ok"
            state.record_stage(check, status, elapsed)
        if state.plan.decomposition_required:
            # Keep a group-aware merge for deduplication of repeated hunks.
            state.findings = merge_group_findings([state.findings])
        log.info("workflow.analysis", extra={"findings": len(state.findings)})

    def _check_enabled(self, check: str) -> bool:
        if self.settings.enabled_categories and check not in self.settings.enabled_categories:
            return False
        if check in self.settings.disabled_categories:
            return False
        field = _CHECK_SWITCHES.get(check)
        if field is not None and not getattr(self.settings, field, True):
            return False
        return True

    def _client_for_category(self, check: str) -> BaseLLMClient:
        """A per-category model override client (cached), else the shared client."""
        override = self.settings.category_models.get(check)
        if not override:
            return self.client
        cache = self._category_clients
        if check not in cache:
            provider = self.client.provider.replace("-", "_")
            field = {
                "openai": "openai_model",
                "anthropic": "anthropic_model",
                "gemini": "gemini_model",
                "ollama": "ollama_model",
                "openai_compatible": "openai_compatible_model",
            }.get(provider, "openai_model")
            settings = self.settings.model_copy(update={field: override})
            try:
                cache[check] = build_llm_client(settings)
            except Exception:  # noqa: BLE001 - fall back to the shared client
                cache[check] = self.client
        return cache[check]

    @staticmethod
    def _check_applicable(check: str, state: ReviewState) -> bool:
        """Skip checks whose inputs are absent (never degrade other checks)."""
        if check == "requirement_alignment":
            return bool(state.request.description.strip())
        return True

    def _run_verifier(self, state: ReviewState) -> None:
        if not state.findings or not self.settings.workflow_use_verifier:
            state.verification_results = []
            state.record_stage("verifier", "skipped", 0.0)
            self._emit(WorkflowEvent.stage_skipped("verifier", "no findings to verify"))
            return
        run = self._run_stage(state, "verifier", lambda: self.verifier.run(state))
        if run is not None:
            state.verification_results = cast(VerifierOutput, run.result).results
            self._emit(
                WorkflowEvent(
                    kind="log",
                    stage="verifier",
                    message=(
                        f"verified {len(state.verification_results)} finding(s): "
                        + ", ".join(
                            r.status.value for r in state.verification_results
                        )
                    ),
                )
            )

    def _run_correlation(self, state: ReviewState) -> None:
        """Cluster overlapping cross-category findings and detect interactions."""
        if not state.findings:
            return
        start = time.monotonic()
        state.findings = cluster_findings(state.findings)
        interactions = interaction_findings(state.findings)
        if interactions:
            state.findings = cluster_findings(state.findings + interactions)
        log.info(
            "workflow.correlation",
            extra={"findings": len(state.findings), "seconds": round(time.monotonic() - start, 3)},
        )

    def _run_aggregator(self, state: ReviewState) -> None:
        run = self._run_stage(state, "aggregator", lambda: self.aggregator.run(state))
        if run is not None and isinstance(run.result, Review):
            state.final_review = run.result
            self._record_usage(state, run)
            return
        # Degraded but honest fallback: deterministic review without LLM summary.
        # The stage failure status recorded by _run_stage stays visible.
        state.final_review = self._fallback_review(state)

    def _fallback_review(self, state: ReviewState) -> Review:
        from agentic_code_reviewer.analysis.aggregation import (
            cluster_findings,
            dedupe_findings,
            filter_findings,
            rank_findings,
            stabilize_severities,
        )

        clustered = cluster_findings(dedupe_findings(state.findings))
        filtered = filter_findings(
            clustered,
            self.settings.min_finding_confidence,
            strictness=self.settings.verification_strictness,
        )
        filtered = rank_findings(stabilize_severities(filtered))
        return Review(
            repository=state.request.repository,
            pull_request=state.request.pull_request,
            commit=state.request.commit,
            summary=(
                state.plan.summary
                if state.plan
                else "(aggregator unavailable; deterministic fallback)"
            ),
            findings=filtered,
            model=f"{self.client.provider}/{self.client.model}",
            confidence_threshold=self.settings.min_finding_confidence,
        )

    def _run_parallel(
        self,
        state: ReviewState,
        stage_name: str,
        tasks: Sequence[Callable[[], T]],
        *,
        task_names: list[str] | None = None,
    ) -> list[T | None]:
        """Run independent stage tasks in parallel with a shared wall-clock timeout."""
        if not tasks:
            return []
        names = task_names or [stage_name] * len(tasks)
        results: list[T | None] = [None] * len(tasks)
        start = time.monotonic()
        # Shared wall-clock deadline: futures are awaited sequentially, so each
        # one gets only the *remaining* budget. Giving every future the full
        # stage_timeout would let N tasks wait up to N * timeout in total.
        deadline = start + self.settings.stage_timeout_seconds
        pool = ThreadPoolExecutor(max_workers=max(1, self.settings.max_workers))
        futures = {pool.submit(task): idx for idx, task in enumerate(tasks)}
        try:
            for future in futures:
                idx = futures[future]
                self._emit(WorkflowEvent.stage_start(names[idx]))
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    # timeout=0 still returns immediately for completed futures.
                    results[idx] = future.result(timeout=remaining)
                    self._emit(
                        WorkflowEvent.stage_done(names[idx], time.monotonic() - start)
                    )
                except FutureTimeout:
                    state.record_error(
                        AgentError(
                            agent=names[idx],
                            stage=names[idx],
                            error_type="AgentTimeoutError",
                            message=f"task {names[idx]} exceeded stage timeout",
                        )
                    )
                    self._emit(
                        WorkflowEvent.stage_failed(
                            names[idx], time.monotonic() - start, "stage timeout"
                        )
                    )
                    log.warning("workflow.task_failed", extra={"task": names[idx], "error": "timeout"})
                except Exception as exc:  # noqa: BLE001
                    state.record_error(
                        AgentError(
                            agent=names[idx],
                            stage=names[idx],
                            error_type=type(exc).__name__,
                            message=str(exc)[:500],
                        )
                    )
                    self._emit(
                        WorkflowEvent.stage_failed(
                            names[idx], time.monotonic() - start, str(exc)[:500]
                        )
                    )
                    log.warning(
                        "workflow.task_failed",
                        extra={"task": names[idx], "error": type(exc).__name__},
                    )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        log.info(
            "workflow.parallel",
            extra={"stage": stage_name, "tasks": len(tasks), "seconds": round(time.monotonic() - start, 3)},
        )
        return results

    def _record_usage(self, state: ReviewState, run: AgentRun | None) -> None:
        if run is None:
            return
        state.record_usage(run.usage.to_dict(), run.cost)
        state.llm_call_count += run.calls
        self._emit(
            WorkflowEvent(
                kind="usage",
                payload={
                    "input_tokens": state.token_usage.get("input_tokens", 0),
                    "output_tokens": state.token_usage.get("output_tokens", 0),
                    "total_tokens": sum(state.token_usage.values()),
                    "cost_usd": round(state.estimated_cost_usd, 6),
                    "calls": state.llm_call_count,
                },
            )
        )
