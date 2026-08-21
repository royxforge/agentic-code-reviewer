"""Unified diff parsing.

Produces typed representations of diffs so the rest of the system never touches
raw ``+``/``-`` text directly. Every line is classified as ADDED, REMOVED or
CONTEXT and carries the line number it occupies in both the old and new file,
which the evidence verifier uses to validate finding locations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from agentic_code_reviewer.errors import DiffParseError

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_DIFF_GIT = re.compile(r"^diff --git a/(.*) b/(.*)$")
_INDEX = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+")
_DEV_NULL = re.compile(r"^/dev/null$")


class LineKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CONTEXT = "context"


@dataclass
class DiffLine:
    kind: LineKind
    text: str  # content without the leading marker
    old_line: int | None = None
    new_line: int | None = None


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header_note: str = ""
    missing_newline_eof: bool = False
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def changed_ranges(self) -> list[tuple[int, int]]:
        """New-file line ranges touched by this hunk (for verifier checks)."""
        if not self.lines:
            return []
        ranges: list[tuple[int, int]] = []
        start: int | None = None
        prev: int | None = None
        for line in self.lines:
            if line.kind in (LineKind.ADDED, LineKind.CONTEXT) and line.new_line:
                n = line.new_line
                if start is None:
                    start = n
                elif prev is not None and n != prev + 1:
                    ranges.append((start, prev))
                    start = n
                prev = n
        if start is not None and prev is not None:
            ranges.append((start, prev))
        return ranges


@dataclass
class DiffFile:
    path: str
    old_path: str
    status: str  # modified | added | deleted | renamed
    is_binary: bool = False
    is_new: bool = False
    is_deleted: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def language(self) -> str:
        ext = self.path.rsplit(".", 1)[-1] if "." in self.path else ""
        return _ext_to_lang.get(ext, "unknown")

    @property
    def added_lines(self) -> list[DiffLine]:
        return [ln for h in self.hunks for ln in h.lines if ln.kind == LineKind.ADDED]

    @property
    def removed_lines(self) -> list[DiffLine]:
        return [ln for h in self.hunks for ln in h.lines if ln.kind == LineKind.REMOVED]

    @property
    def changed_line_numbers(self) -> list[int]:
        """All new-file line numbers touched by added/removed lines."""
        out = []
        for h in self.hunks:
            for ln in h.lines:
                if ln.kind in (LineKind.ADDED, LineKind.REMOVED) and ln.new_line:
                    out.append(ln.new_line)
        return out

    def line_is_changed(self, new_line: int) -> bool:
        return new_line in self.changed_line_numbers


_ext_to_lang = {
    "py": "python",
    "pyi": "python",
    "js": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "go": "go",
    "rs": "rust",
    "java": "java",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "hpp": "cpp",
    "cs": "csharp",
    "rb": "ruby",
    "php": "php",
    "kt": "kotlin",
    "swift": "swift",
    "sh": "shell",
    "yaml": "yaml",
    "yml": "yaml",
    "json": "json",
    "toml": "toml",
    "md": "markdown",
    "sql": "sql",
}


def parse_diff(diff_text: str) -> list[DiffFile]:
    """Parse a unified diff (``diff --git`` or plain ``---/+++`` format)."""
    if not diff_text.strip():
        return []

    files: list[DiffFile] = []
    current: DiffFile | None = None
    current_hunk: Hunk | None = None

    for raw in diff_text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("diff --git"):
            m = _DIFF_GIT.match(line)
            old = m.group(1) if m else ""
            new = m.group(2) if m else ""
            if _DEV_NULL.match(new):
                current = DiffFile(path=old, old_path=old, status="deleted", is_deleted=True)
            elif _DEV_NULL.match(old):
                current = DiffFile(path=new, old_path=new, status="added", is_new=True)
            elif old != new:
                current = DiffFile(path=new, old_path=old, status="renamed")
            else:
                current = DiffFile(path=new, old_path=old, status="modified")
            files.append(current)
            current_hunk = None
            continue
        if "new file mode" in line and current is not None and not current.is_new:
            current.is_new = True
            current.status = "added"
            continue
        if "deleted file mode" in line and current is not None and not current.is_deleted:
            current.is_deleted = True
            current.status = "deleted"
            continue
        if line.startswith("--- ") or line.startswith("+++ ") or _INDEX.match(line):
            if current is not None and line.startswith("--- /dev/null"):
                current.is_new = True
                current.status = "added"
            elif current is not None and line.startswith("+++ /dev/null"):
                current.is_deleted = True
                current.status = "deleted"
            continue
        if line.startswith("@@ "):
            m = _HUNK_HEADER.match(line)
            if not m:
                if current:
                    current.hunks.append(Hunk(0, 0, 0, 0, header_note=line))
                continue
            current_hunk = Hunk(
                old_start=int(m.group(1)),
                old_count=int(m.group(2) or "1"),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) or "1"),
                header_note=m.group(5).strip(),
            )
            if current is None:
                current = DiffFile(path="", old_path="", status="modified")
                files.append(current)
            current.hunks.append(current_hunk)
            continue
        if current is None:
            if line[:1] in ("+", "-"):
                current = DiffFile(path="", old_path="", status="modified")
                files.append(current)
                current_hunk = Hunk(1, 0, 1, 0)
                current.hunks.append(current_hunk)
            else:
                continue

        hunk = current_hunk if current_hunk is not None else Hunk(1, 0, 1, 0)
        if current_hunk is None:
            current.hunks.append(hunk)
            current_hunk = hunk

        content = line[1:] if line[:1] in ("+", "-", " ") else line
        if line.startswith("+") and not line.startswith("+++"):
            hunk.lines.append(DiffLine(LineKind.ADDED, content))
        elif line.startswith("-") and not line.startswith("---"):
            hunk.lines.append(DiffLine(LineKind.REMOVED, content))
        elif line.startswith(" "):
            hunk.lines.append(DiffLine(LineKind.CONTEXT, content))
        elif line.startswith(r"\ No newline at end of file"):
            # Record as a flag on the hunk  -  never mutate the line's content.
            hunk.missing_newline_eof = True
            continue
        else:
            # Non-diff junk inside a hunk (e.g. "Binary files ... differ").
            if hunk.lines:
                hunk.lines.append(DiffLine(LineKind.CONTEXT, line))
            continue

    if not files:
        raise DiffParseError("No diff hunks could be parsed from the input.")

    for f in files:
        if f.status == "deleted":
            continue
        _assign_line_numbers(f)
    return files


def _assign_line_numbers(f: DiffFile) -> None:
    """Fill old/new line numbers for every line using running counters."""
    for h in f.hunks:
        old_pos = h.old_start
        new_pos = h.new_start
        for ln in h.lines:
            if ln.kind == LineKind.ADDED:
                ln.new_line = new_pos
                new_pos += 1
            elif ln.kind == LineKind.REMOVED:
                ln.old_line = old_pos
                old_pos += 1
            else:
                ln.old_line = old_pos
                ln.new_line = new_pos
                old_pos += 1
                new_pos += 1


def diff_to_text(files: list[DiffFile]) -> str:
    """Re-serialise parsed files back to unified diff text (used for prompts)."""
    out: list[str] = []
    for f in files:
        out.append(f"diff --git a/{f.old_path} b/{f.path}")
        out.append("--- /dev/null" if f.is_new else f"--- a/{f.old_path}")
        out.append("+++ /dev/null" if f.is_deleted else f"+++ b/{f.path}")
        for h in f.hunks:
            out.append(
                f"@@ -{h.old_start},{h.old_count} +{h.new_start},{h.new_count} @@ {h.header_note}".rstrip()
            )
            for ln in h.lines:
                marker = {"added": "+", "removed": "-", "context": " "}[ln.kind.value]
                out.append(f"{marker}{ln.text}")
            if h.missing_newline_eof:
                out.append(r"\ No newline at end of file")
    return "\n".join(out)


def diff_text_for_category(
    files: list[DiffFile], category: str, *, max_chars: int | None = None
) -> str:
    """A category-focused view of the diff, for token-efficient prompts.

    Analysis agents don't all need the whole change: a security agent cares
    about hunks touching SQL/HTTP/auth patterns, a testing agent about test
    files, and so on. This returns the diff text restricted to files whose
    added lines match the category's deterministic patterns, falling back to
    the full diff when nothing matches (never starve the agent of the change).

    When ``max_chars`` is given the result is truncated conservatively (hunks
    are dropped from the end, keeping the first files), so a very large diff
    can still be fit into a category budget without arbitrary mid-line cuts.
    """
    from agentic_code_reviewer.analysis.rules import CATEGORY_PATTERNS

    patterns = CATEGORY_PATTERNS.get(category, [])
    if not patterns:
        return diff_to_text(files)
    kept: list[DiffFile] = []
    for f in files:
        added = "\n".join(ln.text for ln in f.added_lines)
        if any(pattern.search(added) for _, pattern in patterns):
            kept.append(f)
    if not kept:
        return diff_to_text(files)
    text = diff_to_text(kept)
    if max_chars is None or len(text) <= max_chars:
        return text
    # Conservative truncation: drop whole files from the end until it fits.
    while len(kept) > 1:
        without = diff_to_text(kept[:-1])
        if len(without) <= max_chars:
            return without
        kept = kept[:-1]
    # Last resort: hard character cut on the remaining files' text.
    return diff_to_text(kept)[:max_chars]
