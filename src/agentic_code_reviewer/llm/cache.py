"""Content-addressed LLM response cache.

Repeat reviews of identical diffs (CI, re-runs, benchmark iterations) send the
same prompt to the same model over and over. This cache stores structured LLM
responses on disk keyed by ``(prompt content, model, temperature, max_tokens)``
and serves hits without a provider call.

Design decisions:
- Keyed by content, not by stage name: any prompt edit changes the hash and
  busts the cache automatically (prompts are versioned files).
- Best-effort and never raises: a corrupt or missing cache file degrades to a
  real LLM call.
- Opt-in via ``LLM_CACHE_ENABLED``; off by default because it changes
  reproducibility semantics (cached runs are deterministic).
- A cache hit returns ``LLMResponse(cached=True)`` with zeroed usage, so
  ``llm_call_count`` / ``estimated_cost_usd`` reflect only real calls.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage

_WRITE_LOCK = threading.Lock()


def _restrict(path: Path, mode: int = 0o600) -> None:
    """Best-effort user-only permissions (POSIX chmod; no-op on Windows)."""
    if os.name == "nt":
        return
    try:
        path.chmod(mode)
    except OSError:
        pass


def _ensure_dir(directory: Path) -> None:
    """Create a directory with restrictive permissions (best-effort)."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _restrict(directory, 0o700)


@dataclass
class CacheEntry:
    content: str
    usage: dict[str, int] = field(default_factory=dict)


class ResponseCache:
    """Disk-backed, content-addressed cache of LLM responses."""

    def __init__(self, directory: Path | str | None = None) -> None:
        if directory is None:
            from agentic_code_reviewer.config.runtime_config import config_dir

            directory = config_dir() / "cache" / "llm"
        self.directory = Path(directory)
        _ensure_dir(self.directory)

    # ------------------------------------------------------------------
    @staticmethod
    def key(
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        provider: str = "",
    ) -> str:
        """Stable cache key for a request.

        ``provider`` is part of the key: the same model name on two
        providers (or mock vs. real) must never share a cached response.
        """
        payload = json.dumps(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "provider": provider,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    # ------------------------------------------------------------------
    def get(self, key: str) -> LLMResponse | None:
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            usage = LLMUsage(**raw.get("usage", {}))
            return LLMResponse(
                content=str(raw.get("content", "")),
                usage=usage,
                cached=True,
            )
        except Exception:  # noqa: BLE001 - corrupt cache degrades to a real call
            return None

    def put(self, key: str, response: LLMResponse) -> None:
        if response.cached:
            return  # never re-cache a cache hit
        entry = CacheEntry(
            content=response.content,
            usage=response.usage.to_dict(),
        )
        try:
            with _WRITE_LOCK:
                self._path_for(key).write_text(
                    json.dumps(entry.__dict__), encoding="utf-8"
                )
        except OSError:
            pass  # best-effort; a full disk must never break a review

    def clear(self) -> int:
        """Delete all cached responses; returns the number removed."""
        removed = 0
        try:
            for path in self.directory.glob("*.json"):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        except OSError:
            pass
        return removed
