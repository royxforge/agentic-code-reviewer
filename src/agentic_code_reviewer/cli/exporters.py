"""Machine-readable review output: markdown, JSON and SARIF 2.1.0.

The JSON form is the canonical machine view of a review (findings, metrics,
usage, model). The SARIF form lets the tool plug into GitHub Advanced
Security, GitLab code scanning, VS Code, and CI dashboards.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentic_code_reviewer.models.findings import Severity
from agentic_code_reviewer.models.schemas import WorkflowResult

# SARIF 2.1.0 requires GitHub's SARIF schema URL to be accepted by the API.
SARIF_SCHEMA = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"

_SEVERITY_TO_LEVEL = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
    Severity.INFO: "note",
}


def review_to_json(result: WorkflowResult) -> str:
    """Serialise the full result (review + usage) as indented JSON."""
    payload = {
        "schema": "agentic-code-reviewer/v1",
        "review": result.review.model_dump(mode="json"),
        "usage": {
            "llm_calls": result.llm_call_count,
            "estimated_cost_usd": round(result.estimated_cost_usd, 6),
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def review_to_sarif(result: WorkflowResult) -> str:
    """Serialise findings as a SARIF 2.1.0 log ({} runs array)."""
    review = result.review
    rules: dict[str, dict] = {}
    results: list[dict] = []

    for finding in review.findings:
        rule_id = finding.rule_id or f"{finding.category}/{finding.title[:40]}"
        rules.setdefault(
            rule_id,
            {
                "id": rule_id,
                "name": finding.title,
                "shortDescription": {"text": finding.title},
                "fullDescription": {"text": finding.description},
                "defaultConfiguration": {"level": _SEVERITY_TO_LEVEL[finding.severity]},
                "properties": {
                    "category": finding.category,
                    "severity": finding.severity.value,
                    "confidence": finding.confidence,
                    "verification": finding.verification_status.value,
                    "evidence_status": finding.evidence_status.value,
                    "evidence_layers": finding.evidence_layers,
                    "related_categories": finding.related_categories,
                    "change_type": finding.change_type,
                },
            },
        )
        region: dict = {}
        if finding.start_line is not None:
            region["startLine"] = finding.start_line
        if finding.end_line is not None:
            region["endLine"] = finding.end_line
        location: dict = {
            "physicalLocation": {
                "artifactLocation": {"uri": finding.file_path or "unknown"},
                "region": region,
            }
        }
        if finding.related_files:
            location["logicalLocations"] = [
                {"fullyQualifiedName": rf} for rf in finding.related_files[:5]
            ]
        sarif_result: dict = {
            "ruleId": rule_id,
            "level": _SEVERITY_TO_LEVEL[finding.severity],
            "message": {"text": finding.description},
            "locations": [location],
        }
        if finding.evidence:
            sarif_result["properties"] = {
                "evidence": finding.evidence,
                "impact": finding.impact,
                "evidence_status": finding.evidence_status.value,
                "evidence_layers": finding.evidence_layers,
                "related_categories": finding.related_categories,
            }
        results.append(sarif_result)

    return json.dumps(
        {
            "$schema": SARIF_SCHEMA,
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "agentic-code-reviewer",
                            "informationUri": "https://github.com/agentic-code-reviewer",
                            "version": review.version,
                            "rules": list(rules.values()),
                        }
                    },
                    "results": results,
                }
            ],
        },
        indent=2,
    ) + "\n"


def export_review(
    result: WorkflowResult, fmt: str, output: Path | None, *, text_stream=None
) -> None:
    """Write a review in ``fmt`` (markdown|json|sarif) to ``output`` or stdout.

    ``text_stream`` is injected for tests; it defaults to sys.stdout and is
    only used when no ``output`` file was given.
    """
    if fmt == "json":
        body = review_to_json(result)
    elif fmt == "sarif":
        body = review_to_sarif(result)
    else:
        body = result.review.to_markdown() + "\n"

    if output is not None:
        output.write_text(body, encoding="utf-8")
        return
    import sys

    stream = text_stream if text_stream is not None else sys.stdout
    stream.write(body)
    stream.flush()
