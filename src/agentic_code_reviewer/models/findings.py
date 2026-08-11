"""Canonical structured finding model and enums.

This is the single currency exchanged between stages: every analysis agent
emits :class:`ReviewFinding` objects, the verifier annotates them with a
verification status, and the aggregator filters/ranks them into the final
review. All LLM-generated data is validated against these models before it
can influence the review.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


# Severity is a closed set  -  arbitrary strings are rejected by the schema.
class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {
            Severity.CRITICAL: 4,
            Severity.HIGH: 3,
            Severity.MEDIUM: 2,
            Severity.LOW: 1,
            Severity.INFO: 0,
        }[self]





class EvidenceStatus(StrEnum):
    """How strongly the claim is backed by repository evidence.

    ``confirmed``             -  a deterministic rule (regex, AST, symbol analysis)
                               independently confirms the claim.
    ``strongly_supported``    -  multiple concrete repository facts support the
                               claim but no deterministic rule fully proves it.
    ``insufficient_evidence`` -  the claim is plausible but cannot be established
                               from repository evidence.
    ``rejected``              -  the claim contradicts the actual repository state
                               or fails the evidence requirements.

    Only ``confirmed`` and ``strongly_supported`` findings normally reach the
    user (see ``filter_findings`` / ``verification_strictness``).
    """

    CONFIRMED = "confirmed"
    STRONGLY_SUPPORTED = "strongly_supported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REJECTED = "rejected"

    @property
    def rank(self) -> int:
        return {
            EvidenceStatus.CONFIRMED: 3,
            EvidenceStatus.STRONGLY_SUPPORTED: 2,
            EvidenceStatus.INSUFFICIENT_EVIDENCE: 1,
            EvidenceStatus.REJECTED: 0,
        }[self]


class VerificationStatus(StrEnum):
    """How confident we are that a finding describes a real defect.

    Kept for backward compatibility and external consumers; new code should
    use :class:`EvidenceStatus` instead. Mapping:

    ``verified``            -> confirmed
    ``strongly_inferred``   -> strongly_supported
    ``potential``           -> insufficient_evidence
    ``unverified``          -> rejected / insufficient_evidence
    """

    VERIFIED = "verified"
    STRONGLY_INFERRED = "strongly_inferred"
    POTENTIAL = "potential"
    UNVERIFIED = "unverified"

    @property
    def rank(self) -> int:
        return {
            VerificationStatus.VERIFIED: 3,
            VerificationStatus.STRONGLY_INFERRED: 2,
            VerificationStatus.POTENTIAL: 1,
            VerificationStatus.UNVERIFIED: 0,
        }[self]

    @property
    def evidence_status(self) -> EvidenceStatus:
        return evidence_status_from_verification(self)


def evidence_status_from_verification(status: VerificationStatus) -> EvidenceStatus:
    """Map the legacy verification status onto the evidence-status model."""
    return {
        VerificationStatus.VERIFIED: EvidenceStatus.CONFIRMED,
        VerificationStatus.STRONGLY_INFERRED: EvidenceStatus.STRONGLY_SUPPORTED,
        VerificationStatus.POTENTIAL: EvidenceStatus.INSUFFICIENT_EVIDENCE,
        VerificationStatus.UNVERIFIED: EvidenceStatus.REJECTED,
    }[status]


def verification_from_evidence(status: EvidenceStatus) -> VerificationStatus:
    """Map the evidence-status model back onto the legacy status."""
    return {
        EvidenceStatus.CONFIRMED: VerificationStatus.VERIFIED,
        EvidenceStatus.STRONGLY_SUPPORTED: VerificationStatus.STRONGLY_INFERRED,
        EvidenceStatus.INSUFFICIENT_EVIDENCE: VerificationStatus.POTENTIAL,
        EvidenceStatus.REJECTED: VerificationStatus.UNVERIFIED,
    }[status]


class ReviewFinding(BaseModel):
    """A single, evidence-backed review finding."""

    category: str = Field(min_length=1)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)

    repository: str = ""
    file_path: str = ""
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    evidence: str = ""
    impact: str = ""
    recommendation: str = ""

    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    # New evidence model (see :class:`EvidenceStatus`); kept in sync with
    # ``verification_status`` by the verifier for backward compatibility.
    evidence_status: EvidenceStatus = EvidenceStatus.INSUFFICIENT_EVIDENCE
    # Populated when a claim contradicts repository state (evidence_status=rejected).
    rejection_reason: str = ""
    # Deterministic evidence layers that confirmed the finding.
    evidence_layers: list[str] = Field(default_factory=list)
    # API-change classification (NEW_API / CHANGED_API / REMOVED_API /
    # BEHAVIOR_CHANGE / CALLER_BREAKAGE)  -  used by the api_contract agent.
    change_type: str = ""
    # Cross-category correlation: the canonical cluster keeps the strongest
    # category as ``category`` and lists the others here.
    related_categories: list[str] = Field(default_factory=list)
    # Severity-model factors (optional; used to derive a consistent severity).
    likelihood: float = 0.5
    blast_radius: float = 0.5
    exploitability: float = 0.5
    impact_factor: float = 0.5

    related_files: list[str] = Field(default_factory=list)

    rule_id: str | None = None

    @model_validator(mode="after")
    def _validate_lines(self) -> ReviewFinding:
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must be >= start_line")
        # Keep the legacy verification status and the new evidence status in
        # sync in both directions (the verifier sets both explicitly): a
        # concrete legacy status derives the evidence status, and a concrete
        # evidence status (other than the neutral default) derives the legacy
        # status so external consumers never see them diverge.
        if self.verification_status != VerificationStatus.UNVERIFIED:
            self.evidence_status = self.verification_status.evidence_status
        elif self.evidence_status != EvidenceStatus.INSUFFICIENT_EVIDENCE:
            self.verification_status = verification_from_evidence(self.evidence_status)
        return self

    def dedupe_key(self) -> tuple[str, str, int | None]:
        """Key used by the aggregator for duplicate detection."""
        return (self.file_path, self.category, self.start_line)

    @field_validator("category")
    @classmethod
    def _category_nonempty(cls, value: str) -> str:
        return value.strip().lower() or "general"


class AgentError(BaseModel):
    """Record of a failure inside one workflow stage (never fatal to the review)."""

    agent: str
    stage: str
    error_type: str
    message: str
    retry_count: int = 0
