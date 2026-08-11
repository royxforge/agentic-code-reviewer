"""User-level runtime configuration (no configuration file to edit).

``acr get-started`` writes provider credentials here so every later run
works out of the box. Precedence: environment variables win - the runtime
store only fills gaps, it never overrides an environment variable.

Layout (``$XDG_CONFIG_HOME``/``~/.config/acr/`` on POSIX,
``%APPDATA%\\acr\\`` on Windows)::

    config.yaml   provider credentials + active provider (chmod 600)
    history.jsonl one review summary per line (append-only)

Secrets are plaintext on disk by design (local dev tool); the file is
restricted to the current user. Never log or print a stored key.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from agentic_code_reviewer.config.settings import Settings, get_settings
from agentic_code_reviewer.models.schemas import WorkflowResult

# Provider key -> Settings fields that are "credentials or endpoint".
# Used to (a) merge into Settings, (b) report configured status.
# logical name -> Settings field. The provider value is stored separately as
# ``llm_provider``; these mappings only describe credential/endpoint fields.
PROVIDER_FIELDS: dict[str, dict[str, str]] = {
    "openai": {
        "api_key": "openai_api_key",
        "model": "openai_model",
        "base_url": "openai_base_url",
    },
    "anthropic": {
        "api_key": "anthropic_api_key",
        "model": "anthropic_model",
    },
    "gemini": {
        "api_key": "gemini_api_key",
        "model": "gemini_model",
    },
    "openai_compatible": {
        "api_key": "openai_compatible_api_key",
        "model": "openai_compatible_model",
        "base_url": "openai_compatible_base_url",
    },
    "ollama": {
        "model": "ollama_model",
        "base_url": "ollama_base_url",
    },
    "mock": {},
}

HISTORY_LIMIT = 1000
_WRITE_LOCK = threading.Lock()


def config_dir() -> Path:
    """The per-user config directory (created on demand).

    The tool was renamed from ``reviewer`` to ``acr``; if a legacy
    ``reviewer`` config dir exists from before the rename, its ``config.yaml``
    (stored provider keys) and ``history.jsonl`` are migrated once so no user
    data is lost. The new ``acr`` dir is always what the tool uses afterwards.
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        directory = Path(base) / "acr"
        legacy = Path(base) / "reviewer"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        directory = Path(base) / "acr"
        legacy = Path(base) / "reviewer"
    directory.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_config(directory, legacy)
    return directory


def _migrate_legacy_config(directory: Path, legacy: Path) -> None:
    """Copy config.yaml + history.jsonl from a pre-rename ``reviewer`` dir."""
    if not legacy.exists() or not legacy.is_dir():
        return
    if any(directory.iterdir()):
        return  # new dir already has data; do not clobber
    for name in ("config.yaml", "history.jsonl"):
        src = legacy / name
        if src.exists():
            try:
                dst = directory / name
                if not dst.exists():
                    dst.write_bytes(src.read_bytes())
            except OSError:
                pass


def config_path() -> Path:
    return config_dir() / "config.yaml"


def history_path() -> Path:
    return config_dir() / "history.jsonl"


def _restrict(file_path: Path) -> None:
    """Best-effort user-only permissions (POSIX chmod 600)."""
    if os.name == "nt":
        return  # Windows ACLs are out of scope for a dev tool
    try:
        file_path.chmod(0o600)
    except OSError:
        pass


class RuntimeConfig:
    """Read/write handle for the user config file."""

    def __init__(self, data: dict[str, Any] | None = None, path: Path | None = None) -> None:
        self._data = data if data is not None else {}
        self.path = path or config_path()

    # -- serialization -------------------------------------------------
    @classmethod
    def load(cls) -> RuntimeConfig:
        path = config_path()
        if not path.exists():
            return cls(path=path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            raw = None
        if not isinstance(raw, dict):
            raw = {}
        return cls(data=raw, path=path)

    def save(self) -> None:
        with _WRITE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                yaml.safe_dump(self._data, sort_keys=False), encoding="utf-8"
            )
            _restrict(self.path)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # -- accessors -----------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def has(self, key: str) -> bool:
        return key in self._data

    # -- provider helpers ----------------------------------------------
    def set_provider(self, provider: str, **values: str) -> None:
        """Persist the active provider and its credential fields.

        ``values`` uses the logical keys (``api_key``, ``model``, ``base_url``)
        and is written to the Settings field names (``openai_api_key``, ...).
        """
        if provider not in PROVIDER_FIELDS:
            raise ValueError(f"unknown provider: {provider}")
        self.set("llm_provider", provider)
        mapping = PROVIDER_FIELDS[provider]
        for logical, field in mapping.items():
            if logical in values and values[logical] not in (None, ""):
                self.set(field, values[logical])
            elif self.has(field):
                self.delete(field)

    def credentials(self, provider: str) -> dict[str, str]:
        """All stored values for a provider (empty strings when unset)."""
        mapping = PROVIDER_FIELDS[provider]
        return {
            logical: str(self.get(field, "") or "")
            for logical, field in mapping.items()
        }

    def provider_configured(self, provider: str) -> bool:
        """True when the provider's required credential(s) are stored."""
        creds = self.credentials(provider)
        if provider in ("openai", "anthropic", "gemini"):
            return bool(creds.get("api_key"))
        if provider == "openai_compatible":
            return bool(creds.get("base_url"))
        if provider == "ollama":
            return True  # base URL has a default; local server assumed
        return True  # mock needs nothing

    # -- history -------------------------------------------------------
    def record_review(self, entry: dict[str, Any]) -> None:
        """Append one review summary to history.jsonl (best-effort)."""
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "review_id": entry.get("review_id", ""),
            **entry,
        }
        try:
            with _WRITE_LOCK:
                path = history_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                existing = self.load_history()
                existing.append(entry)
                path.write_text(
                    "\n".join(json.dumps(e) for e in existing[-HISTORY_LIMIT:]) + "\n",
                    encoding="utf-8",
                )
        except OSError:
            pass  # history is best-effort; never break a review for it

    def load_history(self, limit: int | None = None) -> list[dict[str, Any]]:
        path = history_path()
        if not path.exists():
            return []
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in lines[-HISTORY_LIMIT:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if limit is not None:
            return entries[-limit:]
        return entries

    def record_review_result(self, result: WorkflowResult) -> None:
        """Append one review summary from a finished workflow result.

        Shared by the CLI and the TUI so history entries look identical no
        matter how the review was run (best-effort, never raises).
        """
        try:
            review = result.review
            m = review.metrics
            source = (
                "github_pr"
                if review.pull_request
                else ("github_commit" if review.commit else "local")
            )
            self.record_review(
                {
                    "repository": review.repository,
                    "source": source,
                    "commit": review.commit or "",
                    "pull_request": review.pull_request,
                    "model": review.model,
                    "llm_calls": result.llm_call_count,
                    "cost_usd": round(result.estimated_cost_usd, 6),
                    "critical": m.critical,
                    "high": m.high,
                    "medium": m.medium,
                    "low": m.low,
                    "info": m.info,
                    "findings": len(review.findings),
                }
            )
        except Exception:  # noqa: BLE001 - history must never break a review
            pass

    def clear_history(self) -> int:
        """Delete the local review history file; returns the number of entries
        that were removed (best-effort, never raises)."""
        # Counted before the unlink; a review finishing in between (also under
        # the lock) is an accepted, negligible loss for a local dev-tool log.
        removed = len(self.load_history())
        try:
            with _WRITE_LOCK:
                path = history_path()
                if path.exists():
                    path.unlink()
        except OSError:
            pass
        return removed


# ---------------------------------------------------------------------------
# Merge into Settings
# ---------------------------------------------------------------------------


def apply_runtime_config(settings: Settings, config: RuntimeConfig | None = None) -> Settings:
    """Merge stored provider credentials into settings (environment always wins).

    A stored value is applied only when the corresponding field is still at
    its declared default  -  i.e. the environment did not provide it.
    ``Settings()`` (bare) is the baseline: any difference from it means the
    value came from the environment and takes precedence.
    """
    cfg = config or RuntimeConfig.load()
    if not cfg.to_dict():
        return settings

    # Environment wins: a field is environment-set when its environment
    # variable exists in the environment (regardless of value), or when the
    # field was constructed explicitly (value differs from the declared
    # default).
    def _env_set(field: str, alias: str) -> bool:
        if field not in Settings.model_fields:
            return True  # unknown field: treat as environment-owned
        if os.environ.get(alias) is not None:
            return True
        default = Settings.model_fields[field].default
        return getattr(settings, field) != default

    updates: dict[str, Any] = {}
    stored = cfg.to_dict()

    # Provider selection: environment wins; stored config is the fallback.
    stored_provider = stored.get("llm_provider")
    if not _env_set("llm_provider", "LLM_PROVIDER") and (
        isinstance(stored_provider, str) and stored_provider in PROVIDER_FIELDS
    ):
        updates["llm_provider"] = stored_provider

    # Credentials for the active provider (only when the environment didn't set them).
    active = str(updates.get("llm_provider") or settings.llm_provider)
    mapping = PROVIDER_FIELDS.get(active, {})
    for field in mapping.values():
        alias = Settings.model_fields[field].alias or field.upper()
        if _env_set(field, str(alias)):
            continue
        value = stored.get(field)
        if value not in (None, ""):
            updates[field] = value

    if not updates:
        return settings
    return settings.model_copy(update=updates)


def provider_help_text() -> str:
    """One-line guidance per provider for the get-started wizard."""
    return (
        "openai:   OpenAI  -  needs an OpenAI API key (sk-…)\n"
        "anthropic: Anthropic  -  needs an Anthropic API key (sk-ant-…)\n"
        "gemini:   Google Gemini  -  needs a Google AI Studio key (AIza…)\n"
        "openai_compatible: any OpenAI-compatible endpoint (vLLM, LM Studio, "
        "Groq, OpenRouter, DeepSeek…)  -  needs a base URL, key optional\n"
        "ollama:   local Ollama server  -  no key, model must be pulled\n"
        "mock:     deterministic test double  -  no key, not a real model"
    )


def is_first_run(settings: Settings | None = None) -> bool:
    """True when no provider has been configured yet.

    A user counts as configured when credentials are stored in the runtime
    config OR any provider credential/selection was provided through the
    environment (a difference from the bare ``Settings()`` baseline means the
    environment set it). Used by the launcher to auto-open the first-run
    onboarding flow.
    """
    if RuntimeConfig.load().to_dict():
        return False
    current = settings or get_settings()
    baseline = Settings()
    provider_fields = {"llm_provider"}
    for mapping in PROVIDER_FIELDS.values():
        provider_fields.update(mapping.values())
    return all(
        getattr(current, field) == getattr(baseline, field)
        for field in provider_fields
    )


def active_provider_status(settings: Settings) -> tuple[str, str, bool]:
    """Resolve the active provider for the UI: ``(provider, model, ready)``.

    ``ready`` means the provider has everything it needs to run a review
    (API key for cloud providers, base URL for OpenAI-compatible endpoints;
    ollama/mock are always ready). Values come from ``settings`` *after*
    runtime-config merge, so environment variables and stored keys are both reflected.
    """
    provider = str(settings.llm_provider).replace("-", "_")
    if provider not in PROVIDER_FIELDS:
        provider = "openai"
    mapping = PROVIDER_FIELDS[provider]
    model = ""
    for logical, field in mapping.items():
        if logical == "model":
            model = str(getattr(settings, field, "") or "")
    if provider in ("openai", "anthropic", "gemini"):
        ready = bool(getattr(settings, mapping["api_key"], "") or "")
    elif provider == "openai_compatible":
        ready = bool(getattr(settings, mapping["base_url"], "") or "")
    else:
        ready = True
    return provider, model, ready
