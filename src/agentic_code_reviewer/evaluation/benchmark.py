"""Historical-bug benchmark dataset.

A benchmark entry reconstructs the repository state at the point where a bug
was *introduced*: the reviewer receives only ``diff`` (the bug-introducing
change) and optionally ``base_files`` (the parent snapshot for context). The
solution  -  ``bug_description``, ``bug_fix``, and any future state  -  is held out
unless ``hold_out`` is explicitly set to False for experiments that require it.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from agentic_code_reviewer.errors import BenchmarkError


class BenchmarkEntry(BaseModel):
    entry_id: str
    repository: str
    commit: str
    parent_commit: str | None = None

    # Held out from reviewers (leakage control).
    bug_description: str = ""
    bug_fix: str = ""
    hold_out: bool = True

    # The bug-introducing change (what reviewers actually see).
    diff: str = Field(min_length=1)
    affected_files: list[str] = Field(default_factory=list)
    base_files: dict[str, str] = Field(default_factory=dict)  # parent snapshot

    language: str = ""
    category: str = ""  # correctness | security | error_handling | ...
    severity: str = ""  # critical | high | medium | low

    @model_validator(mode="after")
    def _validate(self) -> BenchmarkEntry:
        if not self.entry_id:
            raise ValueError("entry_id is required")
        if not self.hold_out and not self.bug_description:
            raise ValueError("hold_out=False requires a bug_description")
        return self


class BenchmarkLoader:
    """Load benchmark entries from JSON/JSONL, validating uniqueness."""

    @staticmethod
    def load(path: str | Path) -> list[BenchmarkEntry]:
        path = Path(path)
        if not path.exists():
            raise BenchmarkError(f"Benchmark dataset not found: {path}")
        if path.suffix == ".jsonl":
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            entries = [BenchmarkEntry.model_validate(json.loads(ln)) for ln in lines]
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("entries", data)
            entries = [BenchmarkEntry.model_validate(item) for item in data]
        ids = [e.entry_id for e in entries]
        if len(ids) != len(set(ids)):
            raise BenchmarkError("Benchmark contains duplicate entry_id values")
        return entries

    @staticmethod
    def save(path: str | Path, entries: list[BenchmarkEntry]) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".jsonl":
            path.write_text(
                "\n".join(e.model_dump_json() for e in entries) + "\n",
                encoding="utf-8",
            )
        else:
            path.write_text(
                json.dumps(
                    {"entries": [e.model_dump(mode="json") for e in entries]},
                    indent=2,
                ),
                encoding="utf-8",
            )
