"""Knowledgebase: persistent, cross-run knowledge injected into analysis prompts.

Three kinds of entries, all stored as JSONL in the user config dir
(``config_dir()/knowledge/``):

- ``rule``     - curated conventions / decisions ("this repo uses SQLAlchemy 2.0
                 style", "never put business logic in routes").
- ``finding``  - auto-captured verified findings from past reviews (repo memory):
                 the tool remembers what it confirmed before so future reviews
                 can reference it instead of re-discovering it.
- ``doc``      - indexed documentation / changelog passages.

Retrieval is deliberately deterministic token-overlap scoring (no LLM call, no
embedding endpoint), so injecting knowledge costs zero extra tokens and works
fully offline.
"""

from agentic_code_reviewer.knowledge.capture import capture_review_findings
from agentic_code_reviewer.knowledge.models import KnowledgeEntry, KnowledgeKind
from agentic_code_reviewer.knowledge.render import render_knowledge
from agentic_code_reviewer.knowledge.store import KnowledgeStore

__all__ = [
    "KnowledgeEntry",
    "KnowledgeKind",
    "KnowledgeStore",
    "capture_review_findings",
    "render_knowledge",
]
