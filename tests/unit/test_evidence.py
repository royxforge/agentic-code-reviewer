"""Evidence-status model tests (spec: evidence status + verification)."""

from agentic_code_reviewer.agents.verifier import VerifierAgent
from agentic_code_reviewer.analysis.aggregation import filter_findings
from agentic_code_reviewer.analysis.snapshot import RepositorySnapshot
from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.llm.mock_client import MockLLMClient
from agentic_code_reviewer.models.findings import (
    EvidenceStatus,
    ReviewFinding,
    VerificationStatus,
    evidence_status_from_verification,
    verification_from_evidence,
)
from agentic_code_reviewer.orchestration.state import ReviewRequest, ReviewState

SQL_DIFF = """\
diff --git a/db.py b/db.py
--- a/db.py
+++ b/db.py
@@ -11,1 +11,1 @@
-    return get_user(name)
+    return db.execute("SELECT * FROM users WHERE name = '" + name + "'")
"""


def _finding(**overrides):
    base = dict(
        category="security",
        severity="high",
        confidence=0.9,
        title="SQL injection",
        description="Query is interpolated into SQL.",
        file_path="db.py",
        start_line=11,
    )
    base.update(overrides)
    return ReviewFinding(**base)


def test_legacy_verification_maps_to_evidence_status():
    assert evidence_status_from_verification(VerificationStatus.VERIFIED) == EvidenceStatus.CONFIRMED
    assert (
        evidence_status_from_verification(VerificationStatus.STRONGLY_INFERRED)
        == EvidenceStatus.STRONGLY_SUPPORTED
    )
    assert (
        evidence_status_from_verification(VerificationStatus.POTENTIAL)
        == EvidenceStatus.INSUFFICIENT_EVIDENCE
    )
    assert evidence_status_from_verification(VerificationStatus.UNVERIFIED) == EvidenceStatus.REJECTED


def test_round_trip_verification_from_evidence():
    for status in EvidenceStatus:
        assert verification_from_evidence(status).evidence_status == status


def test_finding_syncs_evidence_from_verification():
    f = _finding(verification_status=VerificationStatus.VERIFIED)
    assert f.evidence_status == EvidenceStatus.CONFIRMED
    f2 = _finding(verification_status=VerificationStatus.STRONGLY_INFERRED)
    assert f2.evidence_status == EvidenceStatus.STRONGLY_SUPPORTED


def test_filter_balanced_keeps_confirmed_and_strongly_supported():
    confirmed = _finding(evidence_status=EvidenceStatus.CONFIRMED)
    strong = _finding(evidence_status=EvidenceStatus.STRONGLY_SUPPORTED, title="T2", start_line=12)
    weak = _finding(evidence_status=EvidenceStatus.INSUFFICIENT_EVIDENCE, title="T3", start_line=13)
    rejected = _finding(evidence_status=EvidenceStatus.REJECTED, title="T4", start_line=14)
    out = filter_findings([confirmed, strong, weak, rejected], min_confidence=0.6)
    assert len(out) == 2
    assert {f.title for f in out} == {"SQL injection", "T2"}


def test_filter_strict_keeps_only_confirmed():
    confirmed = _finding(evidence_status=EvidenceStatus.CONFIRMED)
    strong = _finding(evidence_status=EvidenceStatus.STRONGLY_SUPPORTED, title="T2", start_line=12)
    out = filter_findings([confirmed, strong], min_confidence=0.6, strictness="strict")
    assert len(out) == 1
    assert out[0].evidence_status == EvidenceStatus.CONFIRMED


def test_filter_lenient_keeps_insufficient_but_never_rejected():
    weak = _finding(evidence_status=EvidenceStatus.INSUFFICIENT_EVIDENCE, title="T3", start_line=13)
    rejected = _finding(evidence_status=EvidenceStatus.REJECTED, title="T4", start_line=14)
    out = filter_findings([weak, rejected], min_confidence=0.6, strictness="lenient")
    assert [f.title for f in out] == ["T3"]


def test_filter_drops_rejected_even_at_high_confidence():
    f = _finding(evidence_status=EvidenceStatus.REJECTED, confidence=0.99)
    assert filter_findings([f], min_confidence=0.6) == []


def _verifier_state(diff: str, files: dict[str, str], snapshot: bool = True) -> ReviewState:
    state = ReviewState(
        request=ReviewRequest(
            repository="o/r", diff_text=diff, changed_files=list(files), repo_files=files
        )
    )
    if snapshot:
        state.snapshot = RepositorySnapshot(files, changed_files=list(files))
    return state


def test_verifier_confirms_with_regex_evidence():
    state = _verifier_state(SQL_DIFF, {"db.py": 'def f():\n    return db.execute("SELECT 1")\n' * 1})
    finding = _finding(file_path="db.py", start_line=11)
    state.findings.append(finding)
    VerifierAgent(Settings(LLM_PROVIDER="mock"), MockLLMClient()).run(state)
    assert finding.evidence_status == EvidenceStatus.CONFIRMED
    assert any("regex" in layer for layer in finding.evidence_layers)


def test_verifier_marks_line_outside_hunks_rejected():
    diff = SQL_DIFF  # line 11 changed; finding points at 20
    state = _verifier_state(diff, {"db.py": "x = 1\n" * 30})
    finding = _finding(file_path="db.py", start_line=20)
    state.findings.append(finding)
    VerifierAgent(Settings(LLM_PROVIDER="mock"), MockLLMClient()).run(state)
    assert finding.evidence_status == EvidenceStatus.REJECTED
    assert finding.rejection_reason


def test_verifier_rejects_claim_naming_nonexistent_symbols():
    # Repository index exists but the named symbol is absent everywhere.
    files = {"db.py": 'def handler():\n    return 1\n' * 5}
    diff = (
        "diff --git a/db.py b/db.py\n--- a/db.py\n+++ b/db.py\n@@ -1,3 +1,3 @@\n"
        " def handler():\n-    return 1\n+    return 2\n"
    )
    state = _verifier_state(diff, files)
    finding = _finding(
        file_path="db.py",
        start_line=2,
        title="TotallyImaginaryFunction bug",
        description="TotallyImaginaryFunction returns wrong value when called with inputs.",
    )
    state.findings.append(finding)
    VerifierAgent(Settings(LLM_PROVIDER="mock"), MockLLMClient()).run(state)
    assert finding.evidence_status == EvidenceStatus.REJECTED
    assert "symbols" in finding.rejection_reason


def test_verifier_does_not_reject_against_empty_index():
    # No Python files -> empty index proves nothing; finding stays supported.
    diff = SQL_DIFF
    files = {"db.py": "x = 1\n" * 20}
    state = _verifier_state(diff, files)
    state.snapshot = RepositorySnapshot({"other.txt": "plain text"}, changed_files=[])
    finding = _finding(file_path="db.py", start_line=11)
    state.findings.append(finding)
    VerifierAgent(Settings(LLM_PROVIDER="mock"), MockLLMClient()).run(state)
    assert finding.evidence_status in (
        EvidenceStatus.CONFIRMED,
        EvidenceStatus.STRONGLY_SUPPORTED,
    )
