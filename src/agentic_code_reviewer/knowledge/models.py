"""Knowledge entry model.

An entry is one unit of persistent, cross-run knowledge: a curated rule, an
auto-captured verified finding, or an indexed documentation passage. Entries
are validated pydantic models (like everything else in the system) and are
serialized as JSONL by :class:`KnowledgeStore`.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class KnowledgeKind(StrEnum):
    RULE = "rule"
    FINDING = "finding"
    DOC = "doc"
    # A user-dismissed finding: repo memory that the same class of issue should
    # not be re-reported. Stored separately from guidance entries so it is never
    # injected into prompts as a rule.
    DISMISSED = "dismissed"


class KnowledgeEntry(BaseModel):
    """A single knowledgebase entry."""

    kind: KnowledgeKind
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)

    # Repository the entry applies to ("" = applies to all repositories).
    repository: str = ""
    # Optional review category the entry is relevant to (e.g. "security").
    category: str = ""
    # Optional source file the entry came from (docs indexing / findings).
    source_file: str = ""
    # E.g. "manual", "auto_capture", "docs".
    source: str = "manual"

    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    def entry_id(self) -> str:
        """Stable content-based id (deduplication key)."""
        payload = "\x00".join(
            [
                self.kind.value,
                self.repository,
                self.category,
                self.title,
                self.content,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def applies_to(self, repository: str) -> bool:
        return not self.repository or self.repository == repository
