"""Large-codebase handling: token-aware diff decomposition.

When a diff exceeds the model context budget we never truncate arbitrarily  -
files are grouped so each analysis agent call receives a coherent subset, and
the aggregator merges findings across groups. Decomposition is logged
explicitly and surfaced in the review plan.
"""

from __future__ import annotations

from agentic_code_reviewer.analysis.diff import DiffFile, diff_to_text


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars / 4). Good enough for budgeting."""
    return max(1, len(text) // 4)


def should_decompose(files: list[DiffFile], token_budget: int) -> bool:
    return estimate_tokens(diff_to_text(files)) > token_budget


def group_files(files: list[DiffFile], token_budget: int) -> list[list[DiffFile]]:
    """Group files so each group fits within ``token_budget`` tokens.

    A single file larger than the budget gets its own group (it cannot be split
    without breaking coherence); the planner records that in decomposition notes.
    """
    groups: list[list[DiffFile]] = []
    current: list[DiffFile] = []
    current_tokens = 0
    for f in files:
        file_tokens = estimate_tokens(diff_to_text([f]))
        if current and current_tokens + file_tokens > token_budget:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(f)
        current_tokens += file_tokens
    if current:
        groups.append(current)
    return groups


def describe_groups(groups: list[list[DiffFile]]) -> str:
    return "\n".join(
        f"group {i + 1}: {', '.join(f.path for f in group)}"
        for i, group in enumerate(groups)
    )
