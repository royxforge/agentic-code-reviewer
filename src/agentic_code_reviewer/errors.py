"""Structured error taxonomy for the reviewer system.

All system errors derive from :class:`ReviewerError`. Errors preserve useful
context for logs and correlation with stages, but must never contain secrets
(e.g. API keys or tokens).
"""

from __future__ import annotations


class ReviewerError(Exception):
    """Base class for all system errors."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        if self.detail:
            return f"{self.message} ({self.detail})"
        return self.message


class ConfigurationError(ReviewerError):
    """Invalid or missing configuration (e.g. no API key, bad enum value)."""


class LLMError(ReviewerError):
    """Base class for LLM provider failures."""


class LLMTimeoutError(LLMError):
    """The LLM provider did not respond within the configured timeout."""


class LLMRateLimitError(LLMError):
    """The LLM provider rate-limited the request."""


class LLMInvalidOutputError(LLMError):
    """The LLM returned output that could not be parsed or validated."""


class LLMAuthenticationError(LLMError):
    """The LLM provider rejected the credentials."""


class GitHubError(ReviewerError):
    """GitHub API failures (auth, rate limit, 404, network)."""


class RetrievalError(ReviewerError):
    """Repository retrieval / indexing / embedding failures."""


class ContextOverflowError(ReviewerError):
    """Input exceeds the model context budget and cannot be decomposed safely."""


class AgentTimeoutError(ReviewerError):
    """A workflow stage exceeded its wall-clock budget."""


class ReviewValidationError(ReviewerError):
    """Structured LLM output failed schema validation after repair attempts."""


class DiffParseError(ReviewerError):
    """A diff could not be parsed."""


class NoChangesError(ReviewerError):
    """There is nothing to review: the working tree matches the base ref."""


class NotAGitRepositoryError(ReviewerError):
    """The path is not inside a git work tree."""


class BenchmarkError(ReviewerError):
    """Benchmark dataset loading or evaluation failures."""
