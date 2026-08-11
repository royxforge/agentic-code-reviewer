"""Runs benchmark systems over a dataset and produces scored metrics."""

from __future__ import annotations

import time
from collections.abc import Callable

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import BenchmarkError
from agentic_code_reviewer.evaluation.baselines import BASELINES, BaselineResult
from agentic_code_reviewer.evaluation.benchmark import BenchmarkEntry
from agentic_code_reviewer.evaluation.experiments import ExperimentTracker
from agentic_code_reviewer.evaluation.metrics import EntryScore, SystemMetrics, aggregate, score_entry
from agentic_code_reviewer.models.findings import ReviewFinding
from agentic_code_reviewer.observability.logging import get_logger
from agentic_code_reviewer.orchestration.state import ReviewRequest
from agentic_code_reviewer.orchestration.workflow import Workflow

log = get_logger("benchmark")


def run_agentic(entry: BenchmarkEntry, settings: Settings) -> BaselineResult:
    """System D: the full agentic workflow over a benchmark entry."""
    request = ReviewRequest(
        repository=entry.repository,
        commit=entry.commit,
        diff_text=entry.diff,
        changed_files=entry.affected_files,
        repo_files=entry.base_files,
        source="benchmark",
    )
    workflow = Workflow(settings)
    start = time.monotonic()
    try:
        result = workflow.run(request)
        findings: list[ReviewFinding] = (
            result.review.findings if result.review else []
        )
        return BaselineResult(
            system="agentic",
            findings=findings,
            latency_seconds=round(time.monotonic() - start, 3),
            cost_usd=result.estimated_cost_usd,
            tokens=sum(result.token_usage.values()),
            success=True,
        )
    except Exception as exc:  # noqa: BLE001 - a failed entry is recorded, not fatal
        return BaselineResult(
            system="agentic",
            findings=[],
            latency_seconds=round(time.monotonic() - start, 3),
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def run_benchmark(
    entries: list[BenchmarkEntry],
    systems: list[str],
    settings: Settings,
    tracker: ExperimentTracker,
    *,
    limit: int | None = None,
    summary: str = "",
    allow_leakage: bool = False,
    on_progress: Callable[[str, str, int, int], None] | None = None,
) -> dict[str, SystemMetrics]:
    """Run systems over a dataset.

    ``allow_leakage=True`` opts experiments into reviewing entries that carry a
    ``hold_out: false`` flag (i.e. where the solution is intentionally shown).
    By default such entries are rejected  -  leakage is the default-off.

    ``on_progress(entry_id, system, done, total)`` is invoked after every
    (entry, system) run so UI consumers can render live progress.
    """
    leaked = [e.entry_id for e in entries if not e.hold_out]
    if leaked and not allow_leakage:
        raise BenchmarkError(
            "Dataset contains hold_out=false entries (solution visible): "
            f"{leaked[:5]}. Refusing to run unless allow_leakage=True is passed "
            "by an experiment that explicitly requires it."
        )
    selected = entries if limit is None else entries[:limit]
    total = len(selected) * len(systems)
    log.info("benchmark.start", extra={"entries": len(selected), "systems": systems})

    scores_by_system: dict[str, list[EntryScore]] = {s: [] for s in systems}
    done = 0
    for entry in selected:
        for system in systems:
            result: BaselineResult
            if system == "agentic":
                result = run_agentic(entry, settings)
            else:
                baseline_cls = BASELINES[system]
                result = baseline_cls(settings).review(entry)

            score = score_entry(
                entry,
                result.findings,
                system=system,
                latency_seconds=result.latency_seconds,
                cost_usd=result.cost_usd,
                tokens=result.tokens,
                success=result.success,
                error=result.error,
            )
            scores_by_system[system].append(score)
            tracker.record_prediction(
                {
                    "entry_id": entry.entry_id,
                    "repository": entry.repository,
                    "system": system,
                    "success": result.success,
                    "latency_seconds": result.latency_seconds,
                    "cost_usd": result.cost_usd,
                    "tokens": result.tokens,
                    "tp": score.true_positives,
                    "fp": score.false_positives,
                    "detected": score.detected,
                    "findings": [f.model_dump(mode="json") for f in result.findings],
                    "error": result.error,
                }
            )
            if not result.success:
                tracker.record_failure(entry.entry_id, system, result.error or "unknown")
            done += 1
            if on_progress is not None:
                on_progress(entry.entry_id, system, done, total)

    metrics = {s: aggregate(scores_by_system[s]) for s in systems}
    tracker.finish(list(metrics.values()), summary=summary)
    log.info("benchmark.finish", extra={s: to_summary(m) for s, m in metrics.items()})
    return metrics


def to_summary(m: SystemMetrics) -> dict:
    return {
        "entries": m.entries,
        "precision": m.precision,
        "recall": m.recall,
        "f1": m.f1,
        "detection_rate": m.bug_detection_rate,
        "completion_rate": m.completion_rate,
        "cost": m.mean_cost,
    }
