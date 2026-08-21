"""Dismiss-to-knowledge feedback loop.

When a user dismisses a finding in triage, we persist a ``dismissed``-kind
entry for that repository. On later reviews, findings that match a dismissal
(same category + strong token overlap, or same file + category) are suppressed
before aggregation - the tool learns "we already decided this class of issue is
acceptable here" instead of re-reporting it every run.

This turns the knowledgebase from one-way memory into a learning loop, and it
is deliberately conservative: a dismissal only suppresses findings that
strongly resemble the dismissed one, and only within the same repository.
"""

from __future__ import annotations

from agentic_code_reviewer.analysis.rules import token_overlap
from agentic_code_reviewer.knowledge.models import KnowledgeEntry, KnowledgeKind
from agentic_code_reviewer.models.findings import ReviewFinding

# Minimum shared tokens between a candidate finding and a dismissal entry.
_MIN_OVERLAP_TOKENS = 3


def finding_to_dismissal_entry(finding: ReviewFinding, repository: str) -> KnowledgeEntry:
    """Turn a dismissed finding into a ``dismissed`` knowledge entry."""
    content = (
        f"{finding.description}\n"
        f"Evidence: {finding.evidence}\n"
        f"Recommendation: {finding.recommendation}"
    )
    return KnowledgeEntry(
        kind=KnowledgeKind.DISMISSED,
        title=finding.title,
        content=content.strip(),
        repository=repository,
        category=finding.category,
        source_file=finding.file_path,
        source="dismiss",
        tags=["dismissed", finding.category],
    )


def finding_matches_dismissal(
    finding: ReviewFinding,
    entry: KnowledgeEntry,
    repository: str = "",
) -> bool:
    """True when ``finding`` should be suppressed by ``entry``.

    Conservative rules: the dismissal must apply to the review's repository and
    category (or the entry has no category), and either the finding points at
    the same file as the dismissal or the two share enough identifiers.

    ``repository`` is the repository being reviewed - findings produced by
    agents may carry an empty ``repository`` field even though the review has
    one, so the workflow passes it explicitly.
    """
    if entry.kind != KnowledgeKind.DISMISSED:
        return False
    target = repository or finding.repository
    if not entry.applies_to(target):
        return False
    if entry.category and entry.category != finding.category:
        return False

    # Same file + same category is a strong signal even with sparse text.
    if (
        entry.source_file
        and finding.file_path
        and entry.source_file == finding.file_path
    ):
        return True

    overlap = token_overlap(
        f"{entry.title} {entry.content}",
        f"{finding.title} {finding.description}",
    )
    return len(overlap) >= _MIN_OVERLAP_TOKENS


def suppressed_by_dismissals(
    findings: list[ReviewFinding],
    dismissals: list[KnowledgeEntry],
    repository: str = "",
) -> list[ReviewFinding]:
    """Return the findings NOT suppressed by any dismissal entry."""
    if not dismissals:
        return findings
    return [
        f
        for f in findings
        if not any(
            finding_matches_dismissal(f, d, repository=repository) for d in dismissals
        )
    ]
