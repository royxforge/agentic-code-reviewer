"""Structured, correlation-ID aware logging.

Every review gets a ``review_id`` (correlation id) that is bound to the current
context; all log records produced while a review is in flight carry it so an
engineer can reconstruct the full lifecycle of a failed review.

Never log secrets: :func:`redact` strips values whose key names look secret.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from typing import Any

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "review_id", default="-"
)

_SECRET_KEYS = (
    "api_key",
    "token",
    "secret",
    "password",
    "authorization",
    "key",
)


def redact(value: Any) -> Any:
    """Recursively redact values whose key looks like a secret."""
    if isinstance(value, dict):
        return {
            k: ("***" if any(s in k.lower() for s in _SECRET_KEYS) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def set_correlation_id(review_id: str) -> None:
    _correlation_id.set(review_id)


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def get_correlation_id() -> str:
    return _correlation_id.get()


# Standard LogRecord attributes; everything else on the record is a structured
# ``extra={...}`` field the caller attached (and should be logged).
_LOG_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "review_id"
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "review_id": get_correlation_id(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        structured: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _LOG_ATTRS:
                continue
            structured[key] = value
        if structured:
            payload.update(redact(structured))
        return json.dumps(payload, default=str)


def _install(level: str, fmt: str, log_file: str | None = None) -> None:
    root = logging.getLogger("agentic_code_reviewer")
    root.handlers.clear()
    root.setLevel(level.upper())
    if log_file:
        handler: logging.Handler = logging.FileHandler(log_file, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_CorrelationFilter())
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s [%(review_id)s] %(message)s"
            )
        )
    root.addHandler(handler)
    root.propagate = False


_installed = False


def setup_logging(level: str = "INFO", fmt: str = "json", log_file: str | None = None) -> None:
    """Configure root logger for the agentic_code_reviewer namespace (idempotent).

    ``log_file`` redirects output away from stderr  -  required by the TUI,
    which owns the terminal and must not have log lines interleaved.
    """
    global _installed
    _install(level, fmt, log_file=log_file)
    _installed = True


def get_logger(name: str) -> logging.Logger:
    if not _installed:
        _install("INFO", "json")
    return logging.getLogger(f"agentic_code_reviewer.{name}")


class _CorrelationFilter(logging.Filter):
    """Attach the current review_id to every record so formatters can use it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.review_id = get_correlation_id()
        return True

