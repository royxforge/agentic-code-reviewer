"""Auto-capture: turn verified review findings into knowledgebase entries.

After a review, confirmed / strongly_supported findings above a severity floor
become ``finding``-kind knowledge entries for that repository (repo memory).
The next review of the same repository can reference them: "we already found
SQL injection in db.py on 2026-08-15; make sure this change actually fixes it"
instead of re-discovering the class of problem from scratch.

Capture is opt-in (``KNOWLEDGE_AUTO_CAPTURE=true``) so it never surprises a
user, and it is strictly best-effort - storage failures never affect the
review that produced the findings.
"""

from __future__ import annotations

from agentic_code_reviewer.knowledge.models import KnowledgeEntry, KnowledgeKind
from agentic_code_reviewer.knowledge.store import KnowledgeStore
from agentic_code_reviewer.models.findings import (
    EvidenceStatus,
    ReviewFinding,
    Severity,
)

# Only findings at or above this severity become repo memory.
_MIN_SEVERITY = Severity.MEDIUM

# Only findings that deterministic evidence backs up become repo memory.
_ALLOWED_EVIDENCE = {EvidenceStatus.CONFIRMED, EvidenceStatus.STRONGLY_SUPPORTED}


def finding_to_entry(finding: ReviewFinding, repository: str) -> KnowledgeEntry:
    """One verified finding -> one knowledge entry."""
    content = (
        f"{finding.description}\n"
        f"Evidence: {finding.evidence}\n"
        f"Recommendation: {finding.recommendation}"
    )
    return KnowledgeEntry(
        kind=KnowledgeKind.FINDING,
        title=f"[{finding.severity.value}] {finding.title}",
        content=content.strip(),
        repository=repository,
        category=finding.category,
        source_file=finding.file_path,
        source="auto_capture",
        tags=["verified", finding.category, finding.severity.value],
    )


def capture_review_findings(
    store: KnowledgeStore,
    findings: list[ReviewFinding],
    repository: str,
    *,
    min_severity: Severity = _MIN_SEVERITY,
) -> int:
    """Persist verified findings above the severity floor; returns count written."""
    candidates = [
        finding_to_entry(f, repository)
        for f in findings
        if f.severity.rank >= min_severity.rank
        and f.evidence_status in _ALLOWED_EVIDENCE
    ]
    if not candidates:
        return 0
    return store.add_many(candidates)
