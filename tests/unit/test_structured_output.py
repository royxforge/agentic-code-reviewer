import pytest
from pydantic import BaseModel

from agentic_code_reviewer.errors import ReviewValidationError
from agentic_code_reviewer.llm.client import current_stage
from agentic_code_reviewer.llm.mock_client import MockLLMClient
from agentic_code_reviewer.llm.structured_output import (
    call_structured,
    extract_json,
    parse_and_validate,
)


class Demo(BaseModel):
    name: str
    count: int


def test_extract_json_fenced():
    text = 'Here is the result:\n```json\n{"name": "x", "count": 1}\n```\nThanks!'
    assert extract_json(text) == {"name": "x", "count": 1}


def test_extract_json_plain():
    assert extract_json('{"name": "y", "count": 2} trailing text') == {
        "name": "y",
        "count": 2,
    }


def test_extract_json_no_json():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_parse_and_validate():
    parsed = parse_and_validate(Demo, '{"name": "a", "count": 3}')
    assert parsed.name == "a"


def test_call_structured_success():
    client = MockLLMClient(responses={"demo": '{"name": "ok", "count": 7}'})
    token = current_stage.set("demo")
    try:
        parsed, usage, calls = call_structured(client, Demo, "sys", "task")
    finally:
        current_stage.reset(token)
    assert parsed.count == 7
    assert calls == 1
    assert usage.output_tokens > 0


def test_call_structured_repairs_then_succeeds():
    class FlakyClient(MockLLMClient):
        def __init__(self) -> None:
            super().__init__(responses={"demo": '{"name": "ok", "count": 4}'})
            self._calls = 0

        def complete(self, messages, **kwargs):
            self._calls += 1
            if self._calls == 1:
                from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage

                return LLMResponse(content="not json at all", usage=LLMUsage())
            return super().complete(messages, **kwargs)

    client = FlakyClient()
    token = current_stage.set("demo")
    try:
        parsed, _, calls = call_structured(client, Demo, "sys", "task")
    finally:
        current_stage.reset(token)
    assert parsed.count == 4
    assert calls == 2


def test_call_structured_raises_after_repair_failure():
    client = MockLLMClient(invalid_json_on={"demo"})
    token = current_stage.set("demo")
    try:
        with pytest.raises(ReviewValidationError):
            call_structured(client, Demo, "sys", "task")
    finally:
        current_stage.reset(token)


def test_call_structured_truncates_repair_prompt():
    """Repair prompt must bound the raw output excerpt to 500 characters."""
    seen_prompts: list[str] = []

    class LongOutputClient(MockLLMClient):
        def __init__(self) -> None:
            super().__init__(responses={"demo": '{"name": "ok", "count": 1}'})
            self._calls = 0

        def complete(self, messages, **kwargs):
            self._calls += 1
            seen_prompts.append(messages[-1]["content"])
            if self._calls == 1:
                from agentic_code_reviewer.llm.client import LLMResponse, LLMUsage

                # 2000 characters of invalid output
                return LLMResponse(content="X" * 2000, usage=LLMUsage())
            return super().complete(messages, **kwargs)

    client = LongOutputClient()
    token = current_stage.set("demo")
    try:
        parsed, _, calls = call_structured(client, Demo, "sys", "task")
    finally:
        current_stage.reset(token)

    assert parsed.count == 1
    assert calls == 2
    repair_prompt = seen_prompts[1]
    # The excerpt in the repair prompt must contain 500 X's, not 1000 or 2000
    assert "X" * 500 in repair_prompt
    assert "X" * 501 not in repair_prompt


def test_call_structured_validates_schema():
    client = MockLLMClient(responses={"demo": '{"name": "x", "count": "not-a-number"}'})
    token = current_stage.set("demo")
    try:
        with pytest.raises(ReviewValidationError):
            call_structured(client, Demo, "sys", "task")
    finally:
        current_stage.reset(token)
