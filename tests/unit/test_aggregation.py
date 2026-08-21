from agentic_code_reviewer.analysis.aggregation import (
    cross_check_llm_selection,
    dedupe_findings,
    filter_findings,
    rank_findings,
)
from agentic_code_reviewer.models.findings import ReviewFinding, Severity, VerificationStatus


def _f(**overrides):
    base = dict(
        category="security",
        severity="high",
        confidence=0.9,
        title="SQL injection",
        description="Query is interpolated into SQL.",
        file_path="db.py",
        start_line=11,
        verification_status=VerificationStatus.STRONGLY_INFERRED,
    )
    base.update(overrides)
    return ReviewFinding(**base)


def test_dedupe_keeps_strongest():
    findings = [
        _f(confidence=0.8, severity="medium"),
        _f(confidence=0.95, severity="high"),
        _f(confidence=0.9, severity="high"),
    ]
    out = dedupe_findings(findings)
    assert len(out) == 1
    assert out[0].confidence == 0.95


def test_filter_confidence_threshold():
    findings = [_f(confidence=0.9), _f(confidence=0.4), _f(confidence=0.5)]
    out = filter_findings(findings, min_confidence=0.6)
    assert len(out) == 1
    assert out[0].confidence == 0.9


def test_filter_drops_unverified():
    findings = [_f(verification_status=VerificationStatus.UNVERIFIED, confidence=0.8)]
    out = filter_findings(findings, min_confidence=0.6)
    assert out == []


def test_filter_keeps_verified_low_confidence_above_threshold():
    findings = [_f(verification_status=VerificationStatus.VERIFIED, confidence=0.62)]
    out = filter_findings(findings, min_confidence=0.6)
    assert len(out) == 1


def test_rank_orders_by_severity_then_confidence():
    findings = [
        _f(severity="low", confidence=0.99),
        _f(severity="high", confidence=0.7),
        _f(severity="critical", confidence=0.5),
    ]
    ranked = rank_findings(findings)
    assert [f.severity for f in ranked] == [
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.LOW,
    ]


def test_aggregator_caps_findings_sent_to_llm():
    """AGGREGATOR_MAX_FINDINGS limits what the LLM sees, not the fallback set."""
    import json

    from agentic_code_reviewer.agents.aggregator import AggregatorAgent
    from agentic_code_reviewer.config.settings import Settings
    from agentic_code_reviewer.llm.mock_client import MockLLMClient
    from agentic_code_reviewer.orchestration.state import ReviewRequest, ReviewState

    seen: list[str] = []

    class CapturingMock(MockLLMClient):
        def complete(self, messages, **kwargs):
            seen.append("\n".join(m.get("content", "") for m in messages))
            return super().complete(messages, **kwargs)

    settings = Settings(
        LLM_PROVIDER="mock",
        LOG_FORMAT="text",
        LOG_LEVEL="WARNING",
        AGGREGATOR_MAX_FINDINGS="2",
    )
    state = ReviewState(
        request=ReviewRequest(repository="o/r", diff_text="", changed_files=["db.py"])
    )
    state.findings = [_f(severity=s, start_line=i + 1) for i, s in enumerate(["low", "medium", "high", "critical"])]
    agent = AggregatorAgent(settings, CapturingMock())
    agent.run(state)

    assert seen, "aggregator should have called the LLM"
    payload = seen[0]
    start = payload.index('Candidate findings (JSON):') + len('Candidate findings (JSON):')
    end = payload.index('Produce the final review')
    candidates_json = payload[start:end].strip()
    parsed = json.loads(candidates_json)
    assert isinstance(parsed, list)
    assert len(parsed) <= 2  # capped


def test_cross_check_keeps_evidence_and_drops_invented():
    candidates = [
        _f(start_line=11, evidence="original evidence"),
        _f(start_line=20, category="correctness", title="Off-by-one",
           description="loop boundary", confidence=0.85),
    ]
    llm_invented = ReviewFinding(
        category="security",
        severity="critical",
        confidence=0.99,
        title="Something completely new",
        description="hallucinated",
        file_path="other.py",
        start_line=1,
    )
    llm_list = [candidates[0].model_copy(update={"severity": "critical"}), llm_invented]
    accepted = cross_check_llm_selection(llm_list, candidates)
    files = {f.file_path for f in accepted}
    assert "db.py" in files
    assert "other.py" not in files
    kept = [f for f in accepted if f.file_path == "db.py"]
    assert kept[0].evidence == "original evidence"
    assert kept[0].severity == Severity.CRITICAL
