"""JSONL-backed knowledge store.

Entries live in ``<config_dir()>/knowledge/<repository>.jsonl`` (global entries
in ``_global.jsonl``). The store is deliberately dependency-free: plain JSONL,
read into memory, searched with deterministic token overlap. No vector
database, no embedding endpoint, no LLM call - so it works fully offline and
adds zero token cost.

The interface mirrors the retrieval layer's small surface (``Retriever``):
``list`` / ``add`` / ``remove`` / ``search``. Writes are best-effort and never
raise (a broken knowledge store must never break a review).
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from agentic_code_reviewer.knowledge.models import KnowledgeEntry

_TOKEN_RE = re.compile(r"[A-Za-z_]\w*|\b\d+\b")
_WRITE_LOCK = threading.Lock()


def _restrict(path: Path, mode: int) -> None:
    """Best-effort user-only permissions (POSIX chmod; no-op on Windows).

    Mirrors ``llm.cache._restrict`` / ``config.runtime_config._restrict``:
    knowledge entries can quote private repository context, so the store must
    not be world-readable.
    """
    if os.name == "nt":
        return
    try:
        path.chmod(mode)
    except OSError:
        pass


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def _slug(repository: str) -> str:
    """Filesystem-safe id for a repository ("" = the global file)."""
    if not repository:
        return "_global"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", repository) or "_global"


class KnowledgeStore:
    """Persistent knowledge entries keyed by repository."""

    def __init__(self, directory: Path | str | None = None) -> None:
        if directory is None:
            from agentic_code_reviewer.config.runtime_config import config_dir

            directory = config_dir() / "knowledge"
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _restrict(self.directory, 0o700)

    # ------------------------------------------------------------------
    def _path_for(self, repository: str) -> Path:
        return self.directory / f"{_slug(repository)}.jsonl"

    def entries(self, repository: str = "") -> list[KnowledgeEntry]:
        """All entries for a repository, plus the global ones."""
        out: list[KnowledgeEntry] = []
        for path in (self._path_for(""), self._path_for(repository)):
            if not path.exists():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    out.append(KnowledgeEntry.model_validate_json(line))
            except Exception:  # noqa: BLE001 - a corrupt file must never raise
                continue
        return out

    def add(self, entry: KnowledgeEntry) -> None:
        """Append one entry (deduplicated by content-based id)."""
        existing = {e.entry_id() for e in self.entries(entry.repository)}
        if entry.entry_id() in existing:
            return
        path = self._path_for(entry.repository)
        try:
            with _WRITE_LOCK:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(entry.model_dump_json() + "\n")
                _restrict(path, 0o600)
        except OSError:
            pass  # best-effort; never break a review for storage

    def add_many(self, entries: list[KnowledgeEntry]) -> int:
        """Add several entries, returning the number actually written."""
        written = 0
        for entry in entries:
            before = len(self.entries(entry.repository))
            self.add(entry)
            if len(self.entries(entry.repository)) > before:
                written += 1
        return written

    def remove(self, repository: str, entry_id: str) -> bool:
        """Remove one entry; returns True when it existed."""
        path = self._path_for(repository)
        if not path.exists():
            return False
        removed = False
        try:
            with _WRITE_LOCK:
                lines = path.read_text(encoding="utf-8").splitlines()
                kept = [
                    ln
                    for ln in lines
                    if ln.strip()
                    and KnowledgeEntry.model_validate_json(ln).entry_id() != entry_id
                ]
                removed = len(kept) != len(lines)
                if removed:
                    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        except Exception:  # noqa: BLE001
            return False
        return removed

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        repository: str = "",
        *,
        top_k: int = 4,
        category: str = "",
    ) -> list[KnowledgeEntry]:
        """Top-k entries by deterministic token overlap with the query.

        ``category`` (when given) boosts entries whose review category matches.
        The scoring is cheap and offline: it exists to pick *which* entries to
        inject, not to answer anything itself.
        """
        query_tokens = _tokens(query)
        if not query_tokens:
            return []

        scored: list[tuple[float, KnowledgeEntry]] = []
        for entry in self.entries(repository):
            if not entry.applies_to(repository):
                continue
            haystack = _tokens(f"{entry.title} {entry.content} {' '.join(entry.tags)}")
            overlap = len(query_tokens & haystack)
            if overlap == 0:
                continue
            score = float(overlap)
            if category and entry.category == category:
                score += 2.0
            scored.append((score, entry))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    def relevant(
        self,
        query: str,
        repository: str = "",
        *,
        top_k: int = 4,
        category: str = "",
    ) -> list[KnowledgeEntry]:
        """Alias for :meth:`search` (matches the retriever's naming)."""
        return self.search(query, repository, top_k=top_k, category=category)
