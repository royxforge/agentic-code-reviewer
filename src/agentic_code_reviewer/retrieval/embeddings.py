"""Embedding abstraction.

``build_embedder`` picks the configured provider. With ``auto`` it uses OpenAI
embeddings when an API key exists, otherwise a deterministic local TF-IDF
feature-hashing embedder  -  so RAG works fully offline and never hard-depends
on a vector database.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections import Counter

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import RetrievalError
from agentic_code_reviewer.llm.client import BaseLLMClient

_TOKEN_RE = re.compile(r"[A-Za-z_]\w*|\b\d+\b")


class Embedder(ABC):
    """Embedders expose ``embed_texts`` and a ``dimension`` attribute."""

    dimension: int = 0

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder(Embedder):
    """Deterministic TF-IDF with feature hashing (no external dependencies).

    ``fit`` must be called once with the document corpus so that IDF weights and
    the token vocabulary are stable across the query and the documents.
    """

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension
        self._idf: dict[str, float] = {}
        self._fitted = False

    def fit(self, texts: list[str]) -> LocalEmbedder:
        doc_count = len(texts)
        df: Counter[str] = Counter()
        for text in texts:
            df.update(set(self._tokens(text)))
        self._idf = {
            token: math.log((1 + doc_count) / (1 + count)) + 1.0
            for token, count in df.items()
        }
        self._fitted = True
        return self

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self._fitted:
            self.fit(texts)
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        counts = Counter(self._tokens(text))
        norm = 0.0
        for token, count in counts.items():
            weight = count * self._idf.get(token, 1.0)
            idx, sign = self._hash(token)
            vector[idx] += sign * weight
            norm += weight * weight
        if norm > 0:
            scale = 1.0 / math.sqrt(norm)
            vector = [v * scale for v in vector]
        return vector

    def _hash(self, token: str) -> tuple[int, int]:
        digest = hashlib.md5(token.encode("utf-8")).digest()  # noqa: S324 (stable non-crypto hash)
        idx = int.from_bytes(digest[:4], "big") % self.dimension
        sign = 1 if digest[4] & 1 else -1
        return idx, sign

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return [t.lower() for t in _TOKEN_RE.findall(text)]


class ProviderEmbedder(Embedder):
    """Wrapper around a provider's embedding endpoint (OpenAI / Ollama)."""

    def __init__(self, client: BaseLLMClient, dimension: int) -> None:
        self._client = client
        self.dimension = dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._client.embed(texts)
        except Exception as exc:  # noqa: BLE001 - provider errors become RetrievalError
            raise RetrievalError(f"embedding failed: {exc}") from exc


# Provider embedder dimensions (vector size per model family).
_DIMENSIONS = {
    "openai": 1536,  # text-embedding-3-small
    "ollama": 4096,  # nomic-embed-text
    "gemini": 768,  # text-embedding-004
    "openai-compatible": 1536,  # varies by server; auto mode falls back on failure
}

# Settings mode name -> client provider name (the two nomenclatures differ).
_PROVIDER_FOR_MODE = {
    "openai": "openai",
    "ollama": "ollama",
    "gemini": "gemini",
    "openai_compatible": "openai-compatible",
}


def build_embedder(settings: Settings, llm_client: BaseLLMClient | None = None) -> Embedder:
    mode = settings.embedding_provider
    if mode == "local":
        return LocalEmbedder(settings.local_embedding_dim)
    if mode == "auto":
        # Probe the provider: ProviderEmbedder's constructor never raises, so
        # a bare try/except around it can never fail over. Attempt one tiny
        # embedding instead and fall back to the local embedder if the
        # provider is unavailable (offline, wrong key, wrong endpoint).
        for provider, dimension in _DIMENSIONS.items():
            if llm_client is not None and llm_client.provider == provider:
                candidate = ProviderEmbedder(llm_client, dimension=dimension)
                try:
                    candidate.embed_texts(["ping"])
                except RetrievalError:
                    continue
                return candidate
    elif llm_client is not None:
        provider_name = _PROVIDER_FOR_MODE.get(mode)
        if provider_name and llm_client.provider == provider_name:
            return ProviderEmbedder(llm_client, dimension=_DIMENSIONS[provider_name])
    return LocalEmbedder(settings.local_embedding_dim)
