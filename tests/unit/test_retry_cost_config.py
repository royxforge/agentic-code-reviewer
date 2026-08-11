import pytest
from pydantic import ValidationError

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import (
    LLMAuthenticationError,
    LLMRateLimitError,
)
from agentic_code_reviewer.llm.client import BaseLLMClient, LLMResponse, LLMUsage
from agentic_code_reviewer.llm.cost import estimate_cost


class _FlakyClient(BaseLLMClient):
    provider = "mock"
    model = "mock-model"

    def __init__(self, fail_times: int, failure: type[Exception]) -> None:
        self.fail_times = fail_times
        self.failure = failure
        self.calls = 0

    def complete(self, messages, *, temperature, max_tokens, timeout):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.failure("boom")
        return LLMResponse(content="ok", usage=LLMUsage(input_tokens=10, output_tokens=5))


def test_retry_recovers_after_transient_failures():
    client = _FlakyClient(fail_times=2, failure=LLMRateLimitError)
    resp = client.complete_with_retry(
        [{"role": "user", "content": "hi"}],
        temperature=0,
        max_tokens=10,
        timeout=1,
        max_retries=5,
        backoff=0.01,
    )
    assert resp.content == "ok"
    assert client.calls == 3


def test_retry_exhausts_without_recording_success():
    client = _FlakyClient(fail_times=10, failure=LLMRateLimitError)
    with pytest.raises(LLMRateLimitError):
        client.complete_with_retry(
            [{"role": "user", "content": "hi"}],
            temperature=0,
            max_tokens=10,
            timeout=1,
            max_retries=2,
            backoff=0.01,
        )
    assert client.calls == 3  # initial + 2 retries


def test_auth_errors_are_not_retried():
    client = _FlakyClient(fail_times=10, failure=LLMAuthenticationError)
    with pytest.raises(LLMAuthenticationError):
        client.complete_with_retry(
            [{"role": "user", "content": "hi"}],
            temperature=0,
            max_tokens=10,
            timeout=1,
            max_retries=5,
        )
    assert client.calls == 1


def test_cost_estimation():
    usage = LLMUsage(input_tokens=1_000_000, output_tokens=0)
    assert estimate_cost("openai", "gpt-4o", usage) == pytest.approx(2.50, abs=0.01)
    assert estimate_cost("ollama", "any", usage) == 0.0
    assert estimate_cost("unknown-provider", "unknown-model", usage) == pytest.approx(3.00, abs=0.01)


def test_cost_estimation_new_providers():
    usage = LLMUsage(input_tokens=1_000_000, output_tokens=0)
    assert estimate_cost("gemini", "gemini-2.5-flash", usage) == pytest.approx(0.15, abs=0.01)
    assert estimate_cost("gemini", "gemini-2.5-pro", usage) == pytest.approx(1.25, abs=0.01)
    assert estimate_cost("openai-compatible", "deepseek-chat", usage) == pytest.approx(0.27, abs=0.01)
    # Unlisted compatible models fall back to the conservative default.
    assert estimate_cost("openai-compatible", "my-local-model", usage) == pytest.approx(3.00, abs=0.01)


def test_settings_defaults_and_validation():
    settings = Settings(LLM_PROVIDER="mock")
    assert settings.llm_provider == "mock"
    assert settings.retrieval_top_k == 6

    with pytest.raises(ValidationError):
        Settings(MIN_FINDING_CONFIDENCE=1.5)
    with pytest.raises(ValidationError):
        Settings(LLM_PROVIDER="nope")


def test_settings_alias_and_field_name_both_work():
    a = Settings(LLM_PROVIDER="mock", RETRIEVAL_TOP_K=3)
    b = Settings(llm_provider="mock", retrieval_top_k=3)
    assert a.retrieval_top_k == b.retrieval_top_k == 3


def test_settings_accept_new_providers():
    Settings(LLM_PROVIDER="gemini")
    Settings(LLM_PROVIDER="openai_compatible")
    # The hyphen spelling documented for users is normalized to the underscore.
    s = Settings(LLM_PROVIDER="openai-compatible")
    assert s.llm_provider == "openai_compatible"
    # Same normalization for the embedding provider (docs use the hyphen).
    e = Settings(EMBEDDING_PROVIDER="openai-compatible")
    assert e.embedding_provider == "openai_compatible"
    with pytest.raises(ValidationError):
        Settings(LLM_PROVIDER="gpt5")


def test_settings_new_provider_fields():
    s = Settings(
        GEMINI_API_KEY="k",
        OPENAI_COMPATIBLE_BASE_URL="http://localhost:8000/v1",
        OPENAI_COMPATIBLE_MODEL="deepseek-chat",
    )
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.gemini_embedding_model == "text-embedding-004"
    assert s.openai_compatible_model == "deepseek-chat"
    assert s.openai_compatible_api_key == ""  # optional
