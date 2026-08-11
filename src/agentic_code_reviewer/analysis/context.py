"""Deliberate context selection.

We never dump the whole repository into prompts. Context is built from, in
order of value: (1) changed-file contents at the reviewed snapshot, (2) top
retrieval hits when a retriever is available, and is capped by a character
budget. If retrieval is unavailable this degrades to the context selector
(changed-file symbol chunks), never to "everything".
"""

from __future__ import annotations

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.models.review import ContextChunk
from agentic_code_reviewer.orchestration.state import ReviewState
from agentic_code_reviewer.retrieval.chunker import chunk_file
from agentic_code_reviewer.retrieval.retriever import Retriever


def build_query(state: ReviewState) -> str:
    """Query used for semantic retrieval  -  emphasises the changed surface."""
    parts = [
        state.request.repository,
        " ".join(state.request.changed_files),
    ]
    if state.plan:
        parts.append(" ".join(state.plan.risk_areas))
        parts.append(state.plan.summary)
    return "\n".join(parts)


def build_context(
    state: ReviewState,
    settings: Settings,
    retriever: Retriever | None = None,
) -> list[ContextChunk]:
    """Return the context chunks for this review run (bounded).

    Changed files are always prioritized (they are what the review is about);
    retrieval hits fill the remaining budget when a retriever is available.
    """
    chunks: list[ContextChunk] = []
    budget = settings.context_char_budget
    used = 0

    def _add(candidate: ContextChunk) -> bool:
        nonlocal used
        if used + len(candidate.text) > budget:
            return False
        used += len(candidate.text)
        chunks.append(candidate)
        return True

    if state.request.repo_files:
        for path in state.request.changed_files:
            content = state.request.repo_files.get(path)
            if content is None:
                continue
            for code_chunk in chunk_file(
                path,
                content,
                max_chars=settings.chunk_max_chars,
                overlap_chars=settings.chunk_overlap_chars,
            ):
                chunk = ContextChunk(
                    repository=state.request.repository,
                    file_path=path,
                    symbol=code_chunk.symbol,
                    kind=code_chunk.kind,
                    start_line=code_chunk.start_line,
                    end_line=code_chunk.end_line,
                    text=code_chunk.text,
                    source="context_selector",
                )
                if not _add(chunk):
                    break

    if retriever is not None and settings.workflow_use_retrieval and retriever.is_ready:
        for chunk in retriever.retrieve(build_query(state)):
            if not _add(chunk):
                break
    return chunks


def render_context(chunks: list[ContextChunk]) -> str:
    """Render context chunks into the text block handed to analysis prompts."""
    if not chunks:
        return "(no repository context available)"
    blocks = []
    for c in chunks:
        loc = f" (lines {c.start_line}-{c.end_line})" if c.start_line else ""
        symbol = f"  -  {c.symbol}" if c.symbol else ""
        blocks.append(f"## {c.file_path}{symbol}{loc}\n{c.text}")
    return "\n\n".join(blocks)
