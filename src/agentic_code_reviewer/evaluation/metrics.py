"""Evaluation metrics.

Scoring model (documented in docs/research.md):

- A finding is a **true positive** if it matches the gold bug: same file
  (exact, or in ``affected_files``) AND identifier/token overlap with the gold
  bug description (or matching category as a weaker fallback).
- All other findings are **false positives**.
- An entry is **detected** if it produced at least one true positive.

All metrics are computed from actual runs  -  empty results stay empty until
experiments are executed; nothing here is ever fabricated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentic_code_reviewer.evaluation.benchmark import BenchmarkEntry
from agentic_code_reviewer.models.findings import ReviewFinding

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "in", "on", "of", "to", "for",
    "with", "is", "are", "was", "were", "be", "been", "this", "that", "these",
    "those", "it", "its", "not", "when", "while", "as", "at", "by", "from",
    "can", "will", "should", "may", "would", "could", "there", "their", "then",
}


def _tokens(text: str) -> set[str]:
    return {
        t.lower()
        for t in re.findall(r"[A-Za-z_]\w{2,}", text)
        if t.lower() not in _STOPWORDS
    }


def gold_keywords(entry: BenchmarkEntry) -> set[str]:
    """Keywords come only from the held-out solution, never from category labels."""
    return _tokens(f"{entry.bug_description} {entry.bug_fix}")


def finding_matches(entry: BenchmarkEntry, finding: ReviewFinding) -> bool:
    """Deterministic match between a finding and the gold bug."""
    file_ok = False
    if not entry.affected_files:
        file_ok = True  # no file ground truth  -  rely on text overlap
    elif finding.file_path in entry.affected_files:
        file_ok = True
    else:
        # Allow the same directory as a weak match for cross-file bugs.
        dir_of = lambda p: "/".join(p.split("/")[:-1])  # noqa: E731
        if dir_of(finding.file_path) and dir_of(finding.file_path) in {
            dir_of(f) for f in entry.affected_files
        }:
            file_ok = True
    if not file_ok:
        return False
    keywords = gold_keywords(entry)
    if not keywords:
        return False
    overlap = _tokens(f"{finding.title} {finding.description}") & keywords
    return bool(overlap)


@dataclass
class EntryScore:
    entry_id: str
    system: str
    findings: list[ReviewFinding] = field(default_factory=list)
    true_positives: int = 0
    false_positives: int = 0
    detected: bool = False
    missed_gold: bool = False
    severity_correct: bool = False
    latency_seconds: float = 0.0
    cost_usd: float = 0.0
    tokens: int = 0
    success: bool = True
    error: str | None = None


def score_entry(
    entry: BenchmarkEntry,
    findings: list[ReviewFinding],
    *,
    system: str,
    latency_seconds: float = 0.0,
    cost_usd: float = 0.0,
    tokens: int = 0,
    success: bool = True,
    error: str | None = None,
) -> EntryScore:
    matches = [finding_matches(entry, f) for f in findings]
    tp = sum(matches)
    score = EntryScore(
        entry_id=entry.entry_id,
        system=system,
        findings=findings,
        true_positives=tp,
        false_positives=len(findings) - tp,
        detected=tp > 0,
        missed_gold=(tp == 0 and bool(gold_keywords(entry))),
        latency_seconds=latency_seconds,
        cost_usd=cost_usd,
        tokens=tokens,
        success=success,
        error=error,
    )
    if tp and entry.severity:
        matched = [f for f, m in zip(findings, matches, strict=False) if m]
        score.severity_correct = any(
            f.severity.value == entry.severity for f in matched
        )
    return score


@dataclass
class SystemMetrics:
    system: str
    entries: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    detected: int = 0
    severity_correct: int = 0
    total_latency: float = 0.0
    total_cost: float = 0.0
    total_tokens: int = 0
    successes: int = 0
    failures: int = 0
    per_entry: dict[str, EntryScore] = field(default_factory=dict)

    @property
    def precision(self) -> float | None:
        denom = self.tp + self.fp
        return round(self.tp / denom, 4) if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.tp + self.fn
        return round(self.tp / denom, 4) if denom else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return round(2 * p * r / (p + r), 4)

    @property
    def false_positive_rate(self) -> float | None:
        denom = self.tp + self.fp
        return round(self.fp / denom, 4) if denom else None

    @property
    def bug_detection_rate(self) -> float | None:
        return round(self.detected / self.entries, 4) if self.entries else None

    @property
    def mean_latency(self) -> float:
        return round(self.total_latency / self.entries, 3) if self.entries else 0.0

    @property
    def mean_cost(self) -> float:
        return round(self.total_cost / self.entries, 6) if self.entries else 0.0

    @property
    def completion_rate(self) -> float | None:
        return round(self.successes / self.entries, 4) if self.entries else None


def aggregate(scores: list[EntryScore]) -> SystemMetrics:
    metrics = SystemMetrics(system=scores[0].system if scores else "unknown")
    for score in scores:
        metrics.entries += 1
        metrics.tp += score.true_positives
        metrics.fp += score.false_positives
        metrics.fn += 1 if (score.missed_gold and score.success) else 0
        metrics.detected += 1 if score.detected else 0
        metrics.severity_correct += 1 if score.severity_correct else 0
        metrics.total_latency += score.latency_seconds
        metrics.total_cost += score.cost_usd
        metrics.total_tokens += score.tokens
        metrics.successes += 1 if score.success else 0
        metrics.failures += 0 if score.success else 1
        metrics.per_entry[score.entry_id] = score
    return metrics


def to_table_row(metrics: SystemMetrics) -> dict:
    return {
        "system": metrics.system,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1": metrics.f1,
        "false_positive_rate": metrics.false_positive_rate,
        "bug_detection_rate": metrics.bug_detection_rate,
        "severity_accuracy": (
            round(metrics.severity_correct / metrics.detected, 4)
            if metrics.detected
            else None
        ),
        "completion_rate": metrics.completion_rate,
        "mean_latency_s": metrics.mean_latency,
        "mean_cost_usd": metrics.mean_cost,
        "total_tokens": metrics.total_tokens,
    }


def format_results_table(rows: list[dict]) -> str:
    """Render the comparison table (System | Precision | Recall | F1 | FPR | ...)."""
    headers = [
        "system", "precision", "recall", "f1", "false_positive_rate",
        "bug_detection_rate", "severity_accuracy", "completion_rate",
        "mean_latency_s", "mean_cost_usd",
    ]
    out = ["| " + " | ".join(h.replace("_", " ").title() for h in headers) + " |"]
    out.append("|" + "---|" * len(headers))
    for row in rows:
        cells = []
        for h in headers:
            v = row.get(h)
            cells.append(" - " if v is None else str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)
