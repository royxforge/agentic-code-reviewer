"""Rendering of knowledge entries into the prompt block handed to agents.

The rendered block is deliberately compact: one line per entry (title +
category + one-line content), capped by ``KNOWLEDGE_MAX_CHARS`` so the
knowledgebase can never blow up a prompt budget. Knowledge is context that
helps the agent avoid re-discovering known facts - it must stay small.
"""

from __future__ import annotations

from agentic_code_reviewer.knowledge.models import KnowledgeEntry


def render_knowledge(
    entries: list[KnowledgeEntry],
    *,
    max_chars: int = 1200,
    repository: str = "",
) -> str:
    """Render entries into the ``$KNOWLEDGE$`` block for analysis prompts.

    Returns a header + compact bullet lines, truncated to ``max_chars``. Empty
    when there is nothing relevant (so prompts without a knowledgebase render
    unchanged).
    """
    # Without an explicit repository (e.g. direct render in tests / tools),
    # show everything; the workflow always passes the real repository.
    applicable = entries if not repository else [e for e in entries if e.applies_to(repository)]
    if not applicable:
        return "(no knowledgebase entries for this repository)"

    lines = ["Knowledgebase (past verified findings, rules and decisions for this repository):"]
    for entry in applicable:
        snippet = entry.content.replace("\n", " ").strip()
        if len(snippet) > 200:
            snippet = snippet[:197].rstrip() + "..."
        tag = entry.category or entry.kind.value
        lines.append(f"- [{tag}] {entry.title}: {snippet}")

    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[: max_chars - 3].rstrip() + "..."
    return block
