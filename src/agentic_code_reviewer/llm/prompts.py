"""Versioned prompt management.

Prompts live in ``agentic_code_reviewer/prompts/<stage>/vN.txt`` and are the single
source of truth for agent instructions. They are rendered by substituting
``$UPPER_CASE$`` tokens with run data  -  no prompt text is ever embedded in
Python source. Every stage records its prompt version in the final review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agentic_code_reviewer.errors import ConfigurationError

_PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "prompts"
_TOKEN = re.compile(r"\$([A-Z][A-Z_0-9]*)\$")

_SUPPORTED_STAGES = (
    "planner",
    "change_analyzer",
    "correctness",
    "security",
    "error_handling",
    "testing",
    "regression",
    "performance",
    "maintainability",
    "observability",
    "data_integrity",
    "accessibility",
    "concurrency",
    "dependencies",
    "privacy",
    "i18n",
    "api_contract",
    "requirement_alignment",
    "authorization",
    "reliability",
    "architecture",
    "compatibility",
    "configuration",
    "resource_lifecycle",
    "verifier",
    "aggregator",
    "baseline_single",
    "baseline_context",
    "baseline_rag",
)


@dataclass(frozen=True)
class Prompt:
    stage: str
    version: str
    system: str
    task: str


@lru_cache(maxsize=64)
def load_prompt(stage: str, version: str = "v1") -> Prompt:
    if stage not in _SUPPORTED_STAGES:
        raise ConfigurationError(f"Unknown prompt stage: {stage}")
    path = _PROMPTS_ROOT / stage / f"{version}.txt"
    if not path.exists():
        raise ConfigurationError(f"Prompt file not found: {path}")
    text = path.read_text(encoding="utf-8")
    system = _section(text, "SYSTEM")
    task = _section(text, "TASK")
    return Prompt(stage=stage, version=version, system=system, task=task)


def render(stage: str, version: str, **kwargs: object) -> tuple[str, str]:
    """Return ``(system, task)`` with ``$TOKEN$`` placeholders substituted."""
    prompt = load_prompt(stage, version)

    def _fill(template: str) -> str:
        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in kwargs:
                raise ConfigurationError(
                    f"prompt '{stage}/{version}' requires ${key}$ which was not supplied"
                )
            return str(kwargs[key])

        return _TOKEN.sub(repl, template)

    return _fill(prompt.system), _fill(prompt.task)


def _section(text: str, name: str) -> str:
    marker = f"### {name}"
    if marker not in text:
        raise ConfigurationError(f"prompt file missing '### {name}' section")
    rest = text.split(marker, 1)[1]
    # Sections end at the next '### ' header (or EOF).
    for candidate in ("### TASK", "### SYSTEM", "### OUTPUT"):
        if candidate != marker and candidate in rest:
            rest = rest.split(candidate, 1)[0]
            break
    return rest.strip()


def available_prompt_versions(stage: str) -> list[str]:
    directory = _PROMPTS_ROOT / stage
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.txt"))
