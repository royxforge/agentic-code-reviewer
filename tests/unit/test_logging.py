import json
import logging

from agentic_code_reviewer.observability.logging import (
    _JsonFormatter,
    new_correlation_id,
    redact,
    set_correlation_id,
)


def _record(message: str = "hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord("agentic_code_reviewer.test", logging.INFO, "m.py", 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_structured_extras():
    formatter = _JsonFormatter()
    record = _record("workflow.start", repository="o/r", source="local", findings=3)
    parsed = json.loads(formatter.format(record))
    assert parsed["repository"] == "o/r"
    assert parsed["source"] == "local"
    assert parsed["findings"] == 3
    assert parsed["message"] == "workflow.start"
    assert "review_id" in parsed


def test_json_formatter_redacts_secrets():
    formatter = _JsonFormatter()
    record = _record("x", api_key="sk-secret-123", token="abc", harmless="ok")
    parsed = json.loads(formatter.format(record))
    assert parsed["api_key"] == "***"
    assert parsed["token"] == "***"
    assert parsed["harmless"] == "ok"


def test_correlation_id_is_bound():
    formatter = _JsonFormatter()
    review_id = new_correlation_id()
    set_correlation_id(review_id)
    parsed = json.loads(formatter.format(_record("x")))
    assert parsed["review_id"] == review_id


def test_redact_recursive():
    assert redact({"nested": {"password": "p", "keep": 1}}) == {
        "nested": {"password": "***", "keep": 1}
    }
