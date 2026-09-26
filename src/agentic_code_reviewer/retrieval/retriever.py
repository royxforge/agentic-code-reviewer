"""Repository retriever.

Builds an in-memory vector index over symbol-aware chunks of a repository
snapshot, then serves cosine-similarity retrieval. The interface is small so a
real vector database (Chroma, Qdrant, pgvector, ...) can replace this module
without touching the workflow.
"""

from __future__ import annotations

import math

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.llm.client import BaseLLMClient
from agentic_code_reviewer.models.review import ContextChunk
from agentic_code_reviewer.retrieval.chunker import chunk_file
from agentic_code_reviewer.retrieval.embeddings import Embedder, build_embedder


class Retriever:
    def __init__(
        self,
        settings: Settings,
        llm_client: BaseLLMClient | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self._settings = settings
        self._embedder = embedder or build_embedder(settings, llm_client)
        self._chunks: list[ContextChunk] = []
        self._vectors: list[list[float]] = []
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready and bool(self._chunks)

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def index_repository(
        self, files: dict[str, str], repository: str = "", batch_size: int = 32
    ) -> None:
        """Chunk and embed a repository snapshot (path -> content)."""
        chunks: list[ContextChunk] = []
        for path, content in files.items():
            for code_chunk in chunk_file(
                path,
                content,
                max_chars=self._settings.chunk_max_chars,
                overlap_chars=self._settings.chunk_overlap_chars,
            ):
                chunks.append(
                    ContextChunk(
                        repository=repository,
                        file_path=path,
                        symbol=code_chunk.symbol,
                        kind=code_chunk.kind,
                        start_line=code_chunk.start_line,
                        end_line=code_chunk.end_line,
                        text=code_chunk.text,
                        source="retrieval",
                    )
                )
        if not chunks:
            # Empty snapshot: not ready (nothing to serve); never mark ready
            # with an empty index — retrieval would silently return nothing
            # while claiming the index was built.
            self._ready = False
            return

        texts = [c.text for c in chunks]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(self._embedder.embed_texts(texts[start : start + batch_size]))
        if len(vectors) != len(chunks):
            # Provider returned fewer embeddings than requested - abandon index.
            self._ready = False
            return
        self._chunks = chunks
        self._vectors = vectors
        self._ready = True

    def retrieve(self, query: str, top_k: int | None = None) -> list[ContextChunk]:
        """Return the top-k chunks most similar to ``query``."""
        if not self.is_ready:
            return []
        top_k = top_k or self._settings.retrieval_top_k
        query_vector = self._embedder.embed_texts([query])[0]
        scored: list[tuple[float, int]] = []
        for idx, vector in enumerate(self._vectors):
            score = _cosine(query_vector, vector)
            scored.append((score, idx))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results: list[ContextChunk] = []
        for score, idx in scored[:top_k]:
            # Copy before setting score: mutating the indexed chunk would leak
            # one query's score into later queries over the same chunk.
            chunk = self._chunks[idx].model_copy(update={"score": round(score, 4)})
            results.append(chunk)
        return results


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
