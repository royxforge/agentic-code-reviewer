"""Compare a benchmark run against a stored baseline.

An experiment writes ``metrics.json`` with one row per system (see
:func:`agentic_code_reviewer.evaluation.metrics.to_table_row`). Comparing the
current run against such a baseline is what turns that artifact into a
regression gate: a prompt, model or retriever change can quietly trade recall
for latency, and nothing in the summary table makes that visible.

Directions are declared per metric rather than assumed: quality metrics are
better when higher, while false-positive rate, latency, cost and token usage
are better when lower.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agentic_code_reviewer.errors import BenchmarkError

# Metrics for which an increase is a regression. Everything else is treated as
# "higher is better", so a new quality metric does not need to be registered to
# be gated correctly.
LOWER_IS_BETTER = frozenset(
    {"false_positive_rate", "mean_latency_s", "mean_cost_usd", "total_tokens"}
)

# Repeated LLM sampling makes runs non-deterministic, so a move smaller than
# this is treated as noise. Callers can override it per run.
DEFAULT_TOLERANCE = 0.02

# Bookkeeping fields that are not comparable metrics.
_NON_METRIC_KEYS = frozenset({"system"})


def load_metrics(path: str | Path) -> dict[str, dict]:
    """Read a ``metrics.json`` artifact into ``{system: row}``.

    Raises:
        BenchmarkError: The file is missing, unreadable, or not in the expected
            ``{system: {...}}`` shape.
    """
    path = Path(path)
    if not path.exists():
        raise BenchmarkError(f"Baseline metrics file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"Baseline metrics file is not valid JSON: {path} ({exc})") from exc
    if not isinstance(data, dict) or not data:
        raise BenchmarkError(f"Baseline metrics file has no systems: {path}")

    rows: dict[str, dict] = {}
    for system, row in data.items():
        if not isinstance(row, dict):
            raise BenchmarkError(f"Baseline entry for system '{system}' is not an object")
        rows[str(system)] = row
    return rows


@dataclass(frozen=True)
class MetricDelta:
    """One metric comparison for one system."""

    system: str
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None  # candidate - baseline
    regressed: bool
    # Direction is carried on the delta so labels stay correct for metrics such
    # as latency and cost, where a rise is the bad outcome.
    lower_is_better: bool = False
    # True when the baseline had a value the candidate could not produce, e.g.
    # no findings at all. That is unverifiable rather than improved.
    unavailable: bool = False

    @property
    def improved(self) -> bool:
        """True only when the metric moved in the beneficial direction."""
        if self.delta is None or self.delta == 0.0 or self.regressed:
            return False
        return self.delta < 0 if self.lower_is_better else self.delta > 0

    @property
    def label(self) -> str:
        if self.unavailable:
            return "regressed "
        if self.regressed:
            return "regressed "
        if self.improved:
            return "improved  "
        if self.delta == 0.0:
            return "unchanged "
        # Moved the wrong way, but not far enough to fail the gate.
        return "tolerated "

    def format(self) -> str:
        """Render a single line, marking direction and size of the change."""
        if self.unavailable:
            return (
                f"  {self.label} {self.system}.{self.metric}: "
                f"{_fmt(self.baseline)} -> unavailable"
            )
        return (
            f"  {self.label} {self.system}.{self.metric}: "
            f"{_fmt(self.baseline)} -> {_fmt(self.candidate)} "
            f"({'+' if (self.delta or 0) > 0 else ''}{_fmt(self.delta)})"
        )


@dataclass
class RegressionReport:
    """Outcome of comparing a candidate run against a baseline."""

    deltas: list[MetricDelta]
    tolerance: float = DEFAULT_TOLERANCE
    missing_systems: list[str] = field(default_factory=list)
    extra_systems: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Callers may pass ``None`` explicitly; normalise to a fresh list.
        self.missing_systems = list(self.missing_systems or [])
        self.extra_systems = list(self.extra_systems or [])

    @property
    def regressions(self) -> list[MetricDelta]:
        return [d for d in self.deltas if d.regressed]

    @property
    def ok(self) -> bool:
        """True when nothing regressed and every baseline system reported."""
        return not self.regressions and not self.missing_systems

    def format(self) -> str:
        """Human-readable report; ASCII only so it is safe to log or persist."""
        lines = [f"Regression check vs baseline (tolerance {self.tolerance:g}):"]
        for system in self.missing_systems:
            lines.append(f"  regressed  {system}: missing from this run")
        regressions = self.regressions
        if regressions:
            lines += [d.format() for d in regressions]
        improved = [d for d in self.deltas if d.improved]
        if improved:
            lines += [d.format() for d in improved]
        if self.extra_systems:
            lines.append(f"  (new systems not in baseline: {', '.join(self.extra_systems)})")
        if self.ok:
            lines.append("  No regressions detected.")
        else:
            lines.append(f"  {len(regressions) + len(self.missing_systems)} regression(s) detected.")
        return "\n".join(lines)


def compare_metrics(
    baseline: dict[str, dict],
    candidate: dict[str, dict],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    metrics: set[str] | None = None,
) -> RegressionReport:
    """Compare two ``{system: metrics row}`` mappings.

    Args:
        baseline: Rows from a previous run's ``metrics.json``.
        candidate: Rows from the run under test.
        tolerance: Magnitude a metric may move before it counts as a regression.
            Metrics are compared on their own scale, so this is one absolute
            allowance (e.g. ``0.02`` on a 0-1 ratio, or 0.02 USD for cost).
        metrics: Optional restriction to a subset of metric names. The default
            gates every metric present in both rows.

    Returns:
        A :class:`RegressionReport`; check :attr:`RegressionReport.ok`.
    """
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")

    deltas: list[MetricDelta] = []
    missing: list[str] = []

    for system, baseline_row in baseline.items():
        candidate_row = candidate.get(system)
        if candidate_row is None:
            missing.append(system)
            continue
        for metric, baseline_value in baseline_row.items():
            if metric in _NON_METRIC_KEYS:
                continue
            if metrics is not None and metric not in metrics:
                continue
            if baseline_value is None:
                continue
            candidate_value = candidate_row.get(metric)

            if candidate_value is None:
                deltas.append(
                    MetricDelta(
                        system=system,
                        metric=metric,
                        baseline=baseline_value,
                        candidate=None,
                        delta=None,
                        regressed=True,
                        unavailable=True,
                    )
                )
                continue

            bval = float(baseline_value)
            cval = float(candidate_value)
            # Round before judging so a float artefact (0.66 - 0.68 =
            # -0.020000000000000018) cannot tip a change sitting exactly on the
            # tolerance boundary into a false regression.
            delta = round(cval - bval, 6)
            # Positive ``worse`` means the change is in the harmful direction:
            # a rise for cost/latency metrics, a fall for quality metrics.
            worse = delta if metric in LOWER_IS_BETTER else -delta
            deltas.append(
                MetricDelta(
                    system=system,
                    metric=metric,
                    baseline=bval,
                    candidate=cval,
                    delta=delta,
                    regressed=worse > tolerance,
                    lower_is_better=metric in LOWER_IS_BETTER,
                )
            )

    extra = [system for system in candidate if system not in baseline]
    return RegressionReport(
        deltas=deltas,
        tolerance=tolerance,
        missing_systems=missing,
        extra_systems=extra,
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
