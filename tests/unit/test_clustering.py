"""Cross-category correlation, interaction analysis and severity stabilisation
tests (spec sections 8, 10, 11)."""

from agentic_code_reviewer.analysis.aggregation import (
    cluster_findings,
    interaction_findings,
    stabilize_severities,
)
from agentic_code_reviewer.models.findings import EvidenceStatus, ReviewFinding, Severity


def _f(**overrides):
    base = dict(
        category="security",
        severity="high",
        confidence=0.9,
        title="SQL injection",
        description="User input reaches SQL without parameterization.",
        file_path="db.py",
        start_line=11,
        evidence_status=EvidenceStatus.CONFIRMED,
    )
    base.update(overrides)
    return ReviewFinding(**base)


# ----------------------------------------------------------------------
# Clustering / deduplication (spec section 8)
# ----------------------------------------------------------------------
def test_identical_findings_are_deduplicated():
    a = _f()
    b = _f()
    out = cluster_findings([a, b])
    assert len(out) == 1


def test_overlapping_findings_merge_with_related_categories():
    security = _f()
    correctness = _f(
        category="correctness",
        title="Query built from untrusted input",
        description="SQL is constructed from untrusted input.",
        confidence=0.7,
        severity="medium",
    )
    data_integrity = _f(
        category="data_integrity",
        title="User-controlled SQL modifies state",
        description="User input can alter database state.",
        confidence=0.6,
        severity="medium",
    )
    out = cluster_findings([security, correctness, data_integrity])
    assert len(out) == 1
    canonical = out[0]
    assert canonical.category == "security"  # strongest stays canonical
    assert set(canonical.related_categories) == {"correctness", "data_integrity"}


def test_related_but_distinct_findings_stay_separate():
    sql_injection = _f(start_line=11)
    off_by_one = _f(
        category="correctness",
        start_line=42,
        title="Off-by-one in pagination",
        description="Loop boundary off by one in pagination.",
        confidence=0.8,
    )
    out = cluster_findings([sql_injection, off_by_one])
    assert len(out) == 2


def test_same_file_different_lines_not_merged():
    a = _f(start_line=10)
    b = _f(start_line=10, category="performance", title="N+1 query", description="Query per row")
    # Performance shares no tokens with the SQLi description -> distinct.
    out = cluster_findings([a, b])
    assert len(out) == 2


# ----------------------------------------------------------------------
# Interaction analysis (spec section 11)
# ----------------------------------------------------------------------
def test_interaction_security_plus_performance():
    security = _f()
    performance = _f(
        category="performance",
        start_line=12,
        title="Unbounded cache growth",
        description="Cache stores every response keyed by URL.",
        evidence_status=EvidenceStatus.STRONGLY_SUPPORTED,
    )
    out = interaction_findings([security, performance])
    assert any(f.rule_id == "interaction:security-performance" for f in out)


def test_interaction_requires_co_location():
    security = _f()
    performance = _f(
        category="performance",
        start_line=80,
        title="Slow loop",
        description="Quadratic loop in hot path.",
        evidence_status=EvidenceStatus.CONFIRMED,
    )
    assert interaction_findings([security, performance]) == []


def test_interaction_only_strong_evidence_participates():
    weak = _f(evidence_status=EvidenceStatus.INSUFFICIENT_EVIDENCE)
    performance = _f(
        category="performance",
        start_line=12,
        title="Caching",
        description="Adds caching layer.",
        evidence_status=EvidenceStatus.CONFIRMED,
    )
    assert interaction_findings([weak, performance]) == []


def test_interaction_synthetic_finding_has_related_categories():
    security = _f()
    performance = _f(
        category="performance",
        start_line=12,
        title="Caching",
        description="Adds caching layer keyed by URL.",
        evidence_status=EvidenceStatus.CONFIRMED,
    )
    out = interaction_findings([security, performance])
    assert out
    # The synthetic finding keeps the primary category and records the other.
    assert out[0].category == "security"
    assert out[0].related_categories == ["performance"]


# ----------------------------------------------------------------------
# Severity stabilisation (spec section 10)
# ----------------------------------------------------------------------
def test_stabilize_derives_severity_from_factors():
    f = _f(
        severity="low", likelihood=0.95, blast_radius=0.95, impact_factor=0.95,
        exploitability=0.95,
    )
    out = stabilize_severities([f])
    assert out[0].severity == Severity.CRITICAL


def test_stabilize_keeps_llm_severity_without_factors():
    f = _f(severity="low")
    out = stabilize_severities([f])
    assert out[0].severity == Severity.LOW
