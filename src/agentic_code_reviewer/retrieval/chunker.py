"""Symbol-aware code chunking.

Files are split into chunks anchored on function/class definitions (per
language) so retrieval returns whole, meaningful units instead of arbitrary
line windows. Files without detectable symbols fall back to sliding windows
with overlap. This is deliberately heuristic (no tree-sitter dependency) and
documented as such.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentic_code_reviewer.analysis.diff import _ext_to_lang

# language -> list of (kind, regex) symbol definitions (line-anchored).
_SYMBOL_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "python": [
        ("class", re.compile(r"^\s*class\s+(\w+)")),
        ("function", re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")),
    ],
    "javascript": [
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
        (
            "function",
            re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[^=]+?)\s*=>"),
        ),
    ],
    "typescript": [
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)")),
        ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+(\w+)")),
        ("type", re.compile(r"^\s*(?:export\s+)?type\s+(\w+)\s*=")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
    ],
    "go": [
        ("function", re.compile(r"^func\s+\([^)]*\)\s+(\w+)")),
        ("function", re.compile(r"^func\s+(\w+)")),
        ("type", re.compile(r"^type\s+(\w+)")),
    ],
    "rust": [
        ("function", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?fn\s+(\w+)")),
        ("struct", re.compile(r"^(?:pub\s+)?struct\s+(\w+)")),
        ("enum", re.compile(r"^(?:pub\s+)?enum\s+(\w+)")),
        ("trait", re.compile(r"^(?:pub\s+)?trait\s+(\w+)")),
    ],
    "java": [
        ("class", re.compile(r"^\s*(?:public|protected|private)?\s*(?:abstract\s+|final\s+|static\s+)*class\s+(\w+)")),
        ("interface", re.compile(r"^\s*(?:public\s+)?interface\s+(\w+)")),
        (
            "method",
            re.compile(
                r"^\s*(?:public|protected|private|static|final|synchronized|abstract|\w+\[\]|\w+)"
                r"\s+(\w+)\s*\([^;{]*\)\s*(?:throws\s+[\w, ]+)?\{"
            ),
        ),
    ],
    "c": [
        ("function", re.compile(r"^[A-Za-z_][\w* ]+\([^;]*\)\s*\{")),
        ("function", re.compile(r"^static\s+[A-Za-z_][\w* ]+\([^;]*\)\s*\{")),
    ],
    "cpp": [
        ("class", re.compile(r"^\s*(?:template<.*>)?\s*(?:class|struct)\s+(\w+)")),
        ("function", re.compile(r"^[\w:~<>*& ]+\([^;]*\)\s*(?:const\s*)?\{")),
    ],
    "ruby": [
        ("class", re.compile(r"^\s*class\s+(\w+)")),
        ("module", re.compile(r"^\s*module\s+(\w+)")),
        ("method", re.compile(r"^\s*def\s+(\w+)")),
    ],
    "shell": [
        ("function", re.compile(r"^\s*(\w+)\s*\(\)\s*\{")),
    ],
    "kotlin": [
        ("class", re.compile(r"^\s*(?:data\s+|sealed\s+|abstract\s+)?class\s+(\w+)")),
        ("function", re.compile(r"^\s*(?:fun|private\s+fun|public\s+fun|internal\s+fun|override\s+fun)\s+(\w+)")),
    ],
    "swift": [
        ("class", re.compile(r"^\s*(?:public\s+|internal\s+|private\s+)?(?:final\s+)?class\s+(\w+)")),
        ("struct", re.compile(r"^\s*(?:public\s+|internal\s+|private\s+)?struct\s+(\w+)")),
        ("function", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|fileprivate\s+)?func\s+(\w+)")),
    ],
}


@dataclass
class CodeChunk:
    file_path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    text: str


@dataclass
class _SymbolSpan:
    symbol: str
    kind: str
    start: int
    end: int


def chunk_file(
    file_path: str,
    content: str,
    *,
    max_chars: int = 1500,
    overlap_chars: int = 200,
) -> list[CodeChunk]:
    """Split ``content`` into symbol-anchored chunks."""
    lines = content.splitlines()
    if not lines:
        return []
    language = _ext_to_lang.get(file_path.rsplit(".", 1)[-1], "unknown")
    spans = _extract_spans(lines, language, file_path)

    chunks: list[CodeChunk] = []
    if spans:
        for span in spans:
            body = "\n".join(lines[span.start - 1 : span.end])
            if len(body) <= max_chars:
                chunks.append(
                    CodeChunk(file_path, span.symbol, span.kind, span.start, span.end, body)
                )
            else:
                chunks.extend(
                    _line_windows(file_path, lines, span.start, span.end, max_chars, overlap_chars)
                )
    else:
        chunks = _line_windows(file_path, lines, 1, len(lines), max_chars, overlap_chars)
    chunks.sort(key=lambda c: (c.start_line, c.end_line))
    return _drop_duplicates(chunks)


def _extract_spans(lines: list[str], language: str, path: str) -> list[_SymbolSpan]:
    """Find symbol spans. Python spans end at the next same-or-lower indent;
    brace languages use brace matching; everything else falls back to a cap."""
    patterns = _SYMBOL_PATTERNS.get(language, [])
    spans: list[_SymbolSpan] = []
    for kind, pattern in patterns:
        for idx, line in enumerate(lines, start=1):
            m = pattern.search(line)
            if not m:
                continue
            if language == "python":
                end = _find_indented_end(lines, idx)
            elif kind in ("class", "interface", "struct", "enum", "trait", "type", "module"):
                end = _find_brace_end(lines, idx) or _find_indented_end(lines, idx)
            else:
                end = _find_brace_end(lines, idx) or min(idx + 40, len(lines))
            spans.append(_SymbolSpan(m.group(1) or "anonymous", kind, idx, end))
    return spans


def _find_indented_end(lines: list[str], start: int) -> int:
    """Return the line index where the block at ``start`` ends (next dedent).

    For Python, a block ends just before the next non-blank line whose
    indentation is <= the opening line's indentation (or at EOF).
    """
    indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
    for idx in range(start + 1, len(lines) + 1):
        if idx == len(lines):
            return idx
        line = lines[idx - 1]
        if not line.strip():
            continue
        cur = len(line) - len(line.lstrip())
        if cur <= indent:
            return idx - 1
    return len(lines)


def _find_brace_end(lines: list[str], start: int) -> int | None:
    """Return the index of the line that closes the brace block starting at ``start``."""
    depth = 0
    opened = False
    for idx in range(start - 1, len(lines)):
        line = lines[idx]
        opens = line.count("{")
        closes = line.count("}")
        depth += opens - closes
        opened = opened or bool(opens)
        if opened and depth <= 0:
            return idx + 1
    return None


def _line_windows(
    file_path: str,
    lines: list[str],
    start: int,
    end: int,
    max_chars: int,
    overlap_chars: int,
) -> list[CodeChunk]:
    """Sliding-window chunking with overlap (used when no symbols are found)."""
    chunks: list[CodeChunk] = []
    i = start
    # Approximate overlap in lines (~20 chars per code line).
    overlap_lines = max(1, min(overlap_chars // 20, max_chars // 20))
    while i <= end:
        j = i
        size = 0
        while j <= end and size < max_chars:
            size += len(lines[j - 1]) + 1
            j += 1
        body = "\n".join(lines[i - 1 : j - 1])
        chunks.append(CodeChunk(file_path, "", "snippet", i, j - 1, body))
        if j > end:
            break
        window_len = j - i
        i += max(1, window_len - overlap_lines)
    return chunks


def _drop_duplicates(chunks: list[CodeChunk]) -> list[CodeChunk]:
    seen: set[tuple[str, int, int]] = set()
    out: list[CodeChunk] = []
    for c in chunks:
        key = (c.file_path, c.start_line, c.end_line)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
