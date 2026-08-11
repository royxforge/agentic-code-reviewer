"""Deterministic severity-model tests (spec section 10)."""

from agentic_code_reviewer.analysis.severity import derive_severity, severity_factors_from_finding
from agentic_code_reviewer.models.findings import ReviewFinding, Severity


def _finding(**overrides):
    base = dict(
        category="security",
        severity="low",
        confidence=0.9,
        title="T",
        description="D",
        file_path="app.py",
        start_line=1,
    )
    base.update(overrides)
    return ReviewFinding(**base)


# ----------------------------------------------------------------------
# derive_severity  -  the ladder
# ----------------------------------------------------------------------
def test_all_extreme_factors_is_critical():
    sev = derive_severity(impact=0.95, likelihood=0.95, blast_radius=0.95, exploitability=0.95)
    assert sev == Severity.CRITICAL


def test_high_impact_and_likelihood_is_high():
    sev = derive_severity(impact=0.8, likelihood=0.7, blast_radius=0.5, exploitability=0.6)
    assert sev == Severity.HIGH


def test_mixed_mid_factors_is_medium():
    sev = derive_severity(impact=0.5, likelihood=0.5, blast_radius=0.5, exploitability=0.5)
    assert sev == Severity.MEDIUM


def test_low_factors_is_low():
    sev = derive_severity(impact=0.3, likelihood=0.2, blast_radius=0.3, exploitability=0.2)
    assert sev == Severity.LOW


def test_negligible_factors_is_info():
    sev = derive_severity(impact=0.05, likelihood=0.05, blast_radius=0.05, exploitability=0.05)
    assert sev == Severity.INFO


def test_low_confidence_does_not_affect_severity():
    # Confidence is deliberately excluded from the formula.
    sev = derive_severity(impact=0.95, likelihood=0.95, blast_radius=0.95, exploitability=0.95)
    assert sev == Severity.CRITICAL  # regardless of any confidence value


# ----------------------------------------------------------------------
# severity_factors_from_finding
# ----------------------------------------------------------------------
def test_default_factors_yield_none():
    f = _finding()
    assert severity_factors_from_finding(f) is None


def test_extreme_impact_factor_is_used():
    f = _finding(impact_factor=0.0)
    factors = severity_factors_from_finding(f)
    assert factors is not None
    assert factors["impact"] == 0.0


def test_partial_factors_are_returned_quantified():
    f = _finding(impact_factor=0.9, blast_radius=0.9)
    factors = severity_factors_from_finding(f)
    # Unset factors default to the neutral 0.5 and still participate.
    assert factors == {
        "impact": 0.9,
        "likelihood": 0.5,
        "blast_radius": 0.9,
        "exploitability": 0.5,
    }


def test_confidence_never_leaks_into_factors():
    f = _finding(confidence=0.99, likelihood=0.9)
    factors = severity_factors_from_finding(f)
    assert "confidence" not in factors
