"""Per-repository configuration (``.reviewer.yaml``).

Teams keep review policy next to the code: quality gates, severity weights,
ignore patterns, and model overrides live in a checked-in config file instead
of environment variables. This module owns the file format:

.. code-block:: yaml

    quality_gate:
      critical: 0      # fail when any critical finding exists
      high: 3          # fail when more than 3 high findings exist
      medium: 20
      low: null        # never fail on low
      info: null

    ignore_patterns:    # fnmatch patterns; findings on these paths are dropped
      - "**/test_*.py"
      - "docs/**"

    severity_weights:
      critical: 1.0
      high: 0.8
      medium: 0.5
      low: 0.2
      info: 0.0

    max_tokens: 8192
    temperature: 0.2

    providers:
      llm_provider: openai
      openai_model: gpt-4o-mini

Config discovery is upward from the reviewed path (CWD by default) and a
single file wins  -  no merging, so behaviour is predictable.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

if TYPE_CHECKING:
    from agentic_code_reviewer.config.settings import Settings

# The keys ``load_repo_config`` understands. Anything else in the file is an
# error (a typo'd key silently doing nothing is worse than a loud failure).
_ALLOWED_TOP_LEVEL = (
    "quality_gate",
    "ignore_patterns",
    "severity_weights",
    "max_tokens",
    "temperature",
    "providers",
)

_SEVERITIES = ("critical", "high", "medium", "low", "info")

_CONFIG_FILENAMES = (".reviewer.yaml", ".reviewer.yml")


class ConfigError(Exception):
    """Raised when a repository config file exists but is invalid."""


class QualityGate(BaseModel):
    """CI quality gate: fail the run when a severity bucket exceeds its limit.

    ``None`` (or absent) means "never fail on this severity". The gate is the
    policy side of ``--fail-on``  -  when both exist, the flag wins because it is
    explicit and ephemeral.
    """

    # ``None`` everywhere = no gate: the default must never fail CI runs.
    critical: int | None = None
    high: int | None = None
    medium: int | None = None
    low: int | None = None
    info: int | None = None

    @property
    def configured(self) -> bool:
        return any(v is not None for v in (self.critical, self.high, self.medium, self.low, self.info))

    def violations(self, counts: dict[str, int]) -> list[str]:
        """Return human-readable violations (empty when the gate passes)."""
        problems: list[str] = []
        for sev in _SEVERITIES:
            limit = getattr(self, sev)
            if limit is not None and counts.get(sev, 0) > limit:
                problems.append(f"{sev}: {counts[sev]} > {limit}")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {sev: getattr(self, sev) for sev in _SEVERITIES}


class RepoConfig(BaseModel):
    """Validated contents of ``.reviewer.yaml``."""

    quality_gate: QualityGate = Field(default_factory=QualityGate)
    ignore_patterns: list[str] = Field(default_factory=list)
    severity_weights: dict[str, float] = Field(
        default_factory=lambda: {"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.2, "info": 0.0}
    )
    max_tokens: int | None = None
    temperature: float | None = None
    providers: dict[str, str] = Field(default_factory=dict)
    source_path: Path | None = None

    @field_validator("ignore_patterns", mode="before")
    @classmethod
    def _list_or_comma(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        return value

    @field_validator("severity_weights", mode="before")
    @classmethod
    def _weights_known_keys(cls, value: Any) -> Any:
        if isinstance(value, dict):
            unknown = set(value) - set(_SEVERITIES)
            if unknown:
                raise ValueError(f"unknown severity in severity_weights: {sorted(unknown)}")
        return value

    def overrides(self) -> dict[str, Any]:
        """Settings-field overrides to apply (``providers`` section only)."""
        return dict(self.providers)

    def to_yaml(self) -> str:
        """Round-trip serialisation used by ``reviewer init`` templates."""
        payload: dict[str, Any] = {
            "quality_gate": self.quality_gate.to_dict(),
            "ignore_patterns": self.ignore_patterns,
            "severity_weights": self.severity_weights,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.providers:
            payload["providers"] = self.providers
        return yaml.safe_dump(payload, sort_keys=False).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def find_config_file(start: str | Path = ".") -> Path | None:
    """Walk up from ``start`` looking for a repository config file."""
    current = Path(start).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        for name in _CONFIG_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_repo_config(start: str | Path = ".") -> RepoConfig:
    """Load the nearest repository config (defaults when none exists).

    Raises :class:`ConfigError` when a file exists but is malformed  -  a broken
    policy file must never be silently ignored.
    """
    path = find_config_file(start)
    if path is None:
        return RepoConfig()
    return load_config_file(path)


def load_config_file(path: str | Path) -> RepoConfig:
    """Load and validate a specific config file (used by tests too)."""
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path}: invalid YAML: {exc}") from exc
    if raw is None:
        return RepoConfig(source_path=config_path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: top level must be a mapping")

    unknown = set(raw) - set(_ALLOWED_TOP_LEVEL)
    if unknown:
        raise ConfigError(
            f"{config_path}: unknown key(s) {sorted(unknown)}; "
            f"allowed: {', '.join(_ALLOWED_TOP_LEVEL)}"
        )
    try:
        return RepoConfig.model_validate(raw | {"source_path": config_path})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ConfigError(
            f"{config_path}: invalid value at {'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
        ) from exc


# ---------------------------------------------------------------------------
# Applying config to settings
# ---------------------------------------------------------------------------


def apply_repo_config(settings: Settings, config: RepoConfig) -> Settings:
    """Merge a repo config into :class:`Settings` (repo file wins per key)."""
    updates: dict[str, Any] = {}
    if config.max_tokens is not None:
        updates["max_tokens"] = config.max_tokens
    if config.temperature is not None:
        updates["temperature"] = config.temperature
    updates.update(config.overrides())
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def should_ignore(config: RepoConfig, file_path: str) -> bool:
    """True when a changed file matches an ``ignore_patterns`` pattern."""
    if not config.ignore_patterns or not file_path:
        return False
    return any(fnmatch(file_path, pattern) for pattern in config.ignore_patterns)


def config_notice(config: RepoConfig) -> str:
    """Short human-readable summary of the active config (empty when default)."""
    if config.source_path is None:
        return ""
    parts = [f"config {config.source_path.name}"]
    if config.quality_gate.configured:
        parts.append("quality gate active")
    if config.ignore_patterns:
        parts.append(f"{len(config.ignore_patterns)} ignore pattern(s)")
    return " · ".join(parts)
