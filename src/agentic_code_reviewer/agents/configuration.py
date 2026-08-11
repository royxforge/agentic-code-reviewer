"""Configuration / deployment agent (spec section 1.5).

Correlates the application code with .env.example, deployment manifests,
Docker/Kubernetes files, Terraform/IaC, CI/CD configuration and configuration
schemas. Detects newly required env vars without configuration, unregistered
config references, type mismatches, production-only failures, missing feature
flags, changed ports/paths, broken health checks, and startup-order /
migration-sequencing problems. Never reports missing docs unless it causes a
concrete defect.
"""

from __future__ import annotations

from agentic_code_reviewer.agents.base import AgentRun, AnalysisAgent
from agentic_code_reviewer.orchestration.state import ReviewState


class ConfigurationAgent(AnalysisAgent):
    name = "configuration"
    category = "configuration"
    prompt_version = "v1"

    def run(self, state: ReviewState, diff_text: str) -> AgentRun:
        return self._collect(state, diff_text)
