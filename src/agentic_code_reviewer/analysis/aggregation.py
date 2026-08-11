"""Deterministic aggregation helpers.

The aggregator never blindly concatenates agent output: findings are
normalised, deduplicated, filtered against confidence and verification
thresholds, ranked, and cross-checked against the LLM's final selection so
evidence is always preserved.
"""

from __future__ import annotations

from agentic_code_reviewer.models.findings import (
    EvidenceStatus,
    ReviewFinding,
    VerificationStatus,
    verification_from_evidence,
)


def dedupe_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Drop duplicates (same file + category + start_line), keeping the strongest."""
    best: dict[tuple[str, str, int | None], ReviewFinding] = {}
    for finding in findings:
        key = finding.dedupe_key()
        existing = best.get(key)
        if existing is None:
            best[key] = finding
            continue
        # Keep the one with higher confidence; on tie, higher severity.
        if finding.confidence > existing.confidence + 1e-9:
            best[key] = finding
        elif abs(finding.confidence - existing.confidence) <= 1e-9 and (
            finding.severity.rank > existing.severity.rank
        ):
            best[key] = finding
    return list(best.values())


def filter_findings(
    findings: list[ReviewFinding],
    min_confidence: float,
    *,
    strictness: str = "balanced",
) -> list[ReviewFinding]:
    """False-positive control (evidence-status aware).

    ``rejected`` findings are always dropped (the claim contradicts repository
    state). ``insufficient_evidence`` findings are dropped unless ``strictness``
    is ``lenient``. ``strict`` keeps only ``confirmed`` findings. Everything
    below ``min_confidence`` is dropped regardless.
    """
    keep = {
        "strict": {EvidenceStatus.CONFIRMED},
        "balanced": {EvidenceStatus.CONFIRMED, EvidenceStatus.STRONGLY_SUPPORTED},
        "lenient": {
            EvidenceStatus.CONFIRMED,
            EvidenceStatus.STRONGLY_SUPPORTED,
            EvidenceStatus.INSUFFICIENT_EVIDENCE,
        },
    }.get(strictness, {EvidenceStatus.CONFIRMED, EvidenceStatus.STRONGLY_SUPPORTED})
    out = []
    for finding in findings:
        if finding.confidence < min_confidence - 1e-9:
            continue
        if finding.evidence_status not in keep:
            continue
        out.append(finding)
    return out


def rank_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Rank by (severity, confidence, verification status)."""
    return sorted(
        findings,
        key=lambda f: (f.severity.rank, f.confidence, f.verification_status.rank),
        reverse=True,
    )


def merge_group_findings(findings_by_group: list[list[ReviewFinding]]) -> list[ReviewFinding]:
    """Merge findings produced across decomposed diff groups."""
    merged: dict[tuple[str, str, int | None], ReviewFinding] = {}
    for group in findings_by_group:
        for finding in group:
            key = finding.dedupe_key()
            existing = merged.get(key)
            if existing is None or finding.confidence > existing.confidence:
                merged[key] = finding
    return list(merged.values())


def cross_check_llm_selection(
    llm_findings: list[ReviewFinding], candidates: list[ReviewFinding]
) -> list[ReviewFinding]:
    """Validate the LLM aggregator's final list against stage candidates.

    Returns the original candidates that the LLM selected (evidence preserved),
    using the LLM's severity where provided. Findings invented by the LLM are
    dropped  -  the aggregator can only down-select, never invent.
    """
    accepted: list[ReviewFinding] = []
    for llm_finding in llm_findings:
        match = _closest_candidate(llm_finding, candidates)
        if match is None:
            continue
        # Preserve evidence from the candidate; adopt LLM severity as advisory.
        from agentic_code_reviewer.models.findings import Severity

        raw = llm_finding.severity.value if hasattr(llm_finding.severity, "value") else llm_finding.severity
        severity = Severity(raw)
        accepted.append(match.model_copy(update={"severity": severity}))
    # Any candidate not mentioned by the LLM is kept only if strongly supported.
    mentioned = {f.dedupe_key() for f in accepted}
    for candidate in candidates:
        if candidate.dedupe_key() not in mentioned:
            if candidate.verification_status.rank >= VerificationStatus.STRONGLY_INFERRED.rank:
                accepted.append(candidate)
    return rank_findings(dedupe_findings(accepted))


def _closest_candidate(
    llm_finding: ReviewFinding, candidates: list[ReviewFinding]
) -> ReviewFinding | None:
    """Find the candidate the LLM is referring to (file+line, then title text)."""
    for candidate in candidates:
        if (
            llm_finding.file_path
            and candidate.file_path == llm_finding.file_path
            and llm_finding.start_line is not None
            and candidate.start_line == llm_finding.start_line
        ):
            return candidate
        if (
            llm_finding.file_path
            and candidate.file_path == llm_finding.file_path
            and llm_finding.category == candidate.category
        ):
            return candidate
    # Fall back to title similarity on the same file.
    for candidate in candidates:
        if candidate.file_path == llm_finding.file_path and _title_similar(
            llm_finding.title, candidate.title
        ):
            return candidate
    return None


def _title_similar(a: str, b: str) -> bool:
    import re

    def tokens(text: str) -> set[str]:
        return {t.lower() for t in re.findall(r"[A-Za-z_]\w{2,}", text)}

    overlap = tokens(a) & tokens(b)
    return len(overlap) >= 2


# ----------------------------------------------------------------------
# Cross-category correlation (spec section 8)
# ----------------------------------------------------------------------
def _token_overlap(a: str, b: str) -> int:
    import re

    def tokens(text: str) -> set[str]:
        return {t.lower() for t in re.findall(r"[A-Za-z_]\w{2,}", text)}

    return len(tokens(a) & tokens(b))


def _lines_close(a: ReviewFinding, b: ReviewFinding, max_gap: int = 3) -> bool:
    """Both findings carry nearby (or missing) line references on the same file."""
    if a.start_line is None or b.start_line is None:
        return a.file_path == b.file_path
    return a.file_path == b.file_path and abs(a.start_line - b.start_line) <= max_gap


def cluster_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Cluster overlapping findings into canonical findings.

    Normalisation dimensions: file, changed lines, root cause (token overlap).
    A cluster keeps the strongest finding as canonical and records the other
    categories in ``related_categories``  -  e.g. a security SQLi finding that
    correctness/data_integrity also reported becomes one finding with
    ``primary_category=security, related_categories=[correctness, data_integrity]``.
    Genuinely different defects on the same file are never merged.
    """
    ordered = sorted(findings, key=lambda x: (x.file_path, x.start_line or 0, -x.severity.rank))
    groups: list[list[ReviewFinding]] = []
    for finding in ordered:
        for group in groups:
            rep = group[0]
            if _lines_close(rep, finding) and _token_overlap(
                f"{rep.title} {rep.description}", f"{finding.title} {finding.description}"
            ) >= 2:
                group.append(finding)
                break
        else:
            groups.append([finding])

    out: list[ReviewFinding] = []
    for group in groups:
        canonical = max(group, key=lambda x: (x.severity.rank, x.confidence))
        related = sorted({f.category for f in group if f.category != canonical.category})
        if len(group) > 1:
            canonical = canonical.model_copy(update={"related_categories": related})
        out.append(canonical)
    return dedupe_findings(out)


# ----------------------------------------------------------------------
# Cross-change interaction analysis (spec section 11)
# ----------------------------------------------------------------------
# (category pair) -> (rule_id, title, template). Only pairs that can produce
# a materially new defect are listed; the findings must co-locate on evidence.
_INTERACTION_RULES: dict[
    tuple[str, str], tuple[str, str, str]
] = {
    ("security", "performance"): (
        "interaction:security-performance",
        "Cross-user data exposure via caching",
        "A security issue and a performance change co-locate on {file}: new caching can make "
        "the security defect reach other users (e.g. a cached response or memoised value that "
        "is not keyed by the security-relevant identifier).",
    ),
    ("concurrency", "data_integrity"): (
        "interaction:concurrency-data-integrity",
        "Concurrent mutation can corrupt state",
        "A concurrency defect and a data-integrity concern co-locate on {file}: a race can "
        "turn the data-integrity issue into silent corruption (check-then-act, unsynchronised "
        "writes).",
    ),
    ("api_contract", "regression"): (
        "interaction:api-contract-regression",
        "API change may break existing callers",
        "An API-contract change and a regression concern co-locate on {file}: the signature or "
        "behaviour change can break callers that were not updated in this diff.",
    ),
    ("performance", "reliability"): (
        "interaction:performance-reliability",
        "Retry and performance failure amplification",
        "A performance finding and a reliability finding co-locate on {file}: retries around a "
        "slow path can amplify latency and failure load.",
    ),
    ("compatibility", "data_integrity"): (
        "interaction:compatibility-data-integrity",
        "Rolling-deployment data mismatch",
        "A compatibility concern and a data-integrity concern co-locate on {file}: a schema or "
        "message change can make old and new versions corrupt or reject each other's data "
        "during a rolling deployment.",
    ),
    ("dependencies", "security"): (
        "interaction:dependencies-security",
        "Vulnerable dependency introduces attack surface",
        "A dependency change and a security concern co-locate on {file}: a new or upgraded "
        "dependency expands the attack surface around an already-flagged security issue.",
    ),
}


def interaction_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Detect materially new defects from combinations of strong findings.

    Only ``confirmed`` / ``strongly_supported`` findings on the same file and
    nearby lines participate; the synthetic finding is itself only
    ``strongly_supported`` and is deduplicated by (rule_id, file, line).
    """
    strong = [
        f for f in findings if f.evidence_status.rank >= EvidenceStatus.STRONGLY_SUPPORTED.rank
    ]
    by_category: dict[str, list[ReviewFinding]] = {}
    for f in strong:
        by_category.setdefault(f.category, []).append(f)

    out: list[ReviewFinding] = []
    for (cat_a, cat_b), (rule_id, title, template) in _INTERACTION_RULES.items():
        for a in by_category.get(cat_a, []):
            for b in by_category.get(cat_b, []):
                if a is b or not _lines_close(a, b, max_gap=5):
                    continue
                line = a.start_line or b.start_line
                out.append(
                    ReviewFinding(
                        category=cat_a,
                        severity=max(a.severity, b.severity, key=lambda s: s.rank),
                        confidence=min(a.confidence, b.confidence),
                        title=title,
                        description=template.format(file=a.file_path or b.file_path),
                        file_path=a.file_path or b.file_path,
                        start_line=line,
                        evidence_status=EvidenceStatus.STRONGLY_SUPPORTED,
                        verification_status=verification_from_evidence(
                            EvidenceStatus.STRONGLY_SUPPORTED
                        ),
                        evidence_layers=["interaction"],
                        rule_id=rule_id,
                        related_categories=sorted({cat_a, cat_b} - {cat_a}),
                    )
                )
    # dedupe by (rule_id, file, line)
    seen: set[tuple[str, str, int | None]] = set()
    unique = []
    for f in out:
        key = (f.rule_id or "", f.file_path, f.start_line)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


# ----------------------------------------------------------------------
# Severity stabilisation (spec section 10)
# ----------------------------------------------------------------------
def stabilize_severities(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Derive a deterministic severity from the model factors when an agent
    provided them; otherwise keep the LLM-provided severity unchanged."""
    from agentic_code_reviewer.analysis.severity import (
        derive_severity,
        severity_factors_from_finding,
    )

    out = []
    for f in findings:
        factors = severity_factors_from_finding(f)
        if factors:
            f = f.model_copy(update={"severity": derive_severity(**factors)})
        out.append(f)
    return out
