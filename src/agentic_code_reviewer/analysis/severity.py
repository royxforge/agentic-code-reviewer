"""Deterministic severity model.

``severity`` and ``confidence`` remain the externally visible fields, but the
internal model derives severity from four factors:

* impact         -  how bad the consequence is (0..1)
* likelihood     -  how likely the defect triggers in practice (0..1)
* blast_radius   -  how much of the system is affected (0..1)
* exploitability -  how easily an attacker / bad input can reach it (0..1)

Confidence is deliberately *not* part of the formula: a highly confident
finding can still be low severity (``severity: low, confidence: 0.99``).
"""

from __future__ import annotations

from agentic_code_reviewer.models.findings import Severity

_WEIGHTS = {"impact": 0.4, "likelihood": 0.25, "blast_radius": 0.2, "exploitability": 0.15}


def derive_severity(
    *,
    impact: float = 0.5,
    likelihood: float = 0.5,
    blast_radius: float = 0.5,
    exploitability: float = 0.5,
) -> Severity:
    """Map the four factors onto the severity ladder deterministically."""
    score = (
        _WEIGHTS["impact"] * impact
        + _WEIGHTS["likelihood"] * likelihood
        + _WEIGHTS["blast_radius"] * blast_radius
        + _WEIGHTS["exploitability"] * exploitability
    )
    if score >= 0.82:
        return Severity.CRITICAL
    if score >= 0.6:
        return Severity.HIGH
    if score >= 0.38:
        return Severity.MEDIUM
    if score >= 0.2:
        return Severity.LOW
    return Severity.INFO


def severity_factors_from_finding(finding) -> dict[str, float] | None:
    """Return ``{impact, likelihood, blast_radius, exploitability}`` when the
    finding carries the model factors (values meaningfully set by the agent),
    else ``None`` (keep the LLM-provided severity)."""
    factors = {
        "impact": getattr(finding, "impact_factor", None),
        "likelihood": getattr(finding, "likelihood", None),
        "blast_radius": getattr(finding, "blast_radius", None),
        "exploitability": getattr(finding, "exploitability", None),
    }
    # Treat the defaults (0.5) as "not provided" unless at least one is extreme
    #  -  agents that omit the factors all read 0.5 and would skew every finding.
    extremes = [v for v in factors.values() if v is not None and (v <= 0.15 or v >= 0.85)]
    if not extremes:
        return None
    return {k: float(v) for k, v in factors.items() if v is not None}
