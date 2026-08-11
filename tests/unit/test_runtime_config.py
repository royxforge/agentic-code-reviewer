"""Tests for user-level runtime config (in-app credentials, history)."""


import pytest

from agentic_code_reviewer.config.runtime_config import (
    PROVIDER_FIELDS,
    RuntimeConfig,
    apply_runtime_config,
)
from agentic_code_reviewer.config.settings import Settings


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the runtime config at a temp directory."""
    import agentic_code_reviewer.config.runtime_config as rc

    monkeypatch.setattr(rc, "config_dir", lambda: tmp_path)
    return tmp_path


def _loaded(tmp_path) -> RuntimeConfig:
    """A RuntimeConfig bound to the isolated directory."""
    cfg = RuntimeConfig.load()
    cfg.path = tmp_path / "config.yaml"
    return cfg


def test_save_and_reload_roundtrip(isolated_config):
    cfg = RuntimeConfig.load()
    cfg.set_provider("openai", api_key="sk-test-123", model="gpt-5")
    cfg.save()

    reloaded = RuntimeConfig.load()
    assert reloaded.get("llm_provider") == "openai"
    assert reloaded.get("openai_api_key") == "sk-test-123"
    assert reloaded.get("openai_model") == "gpt-5"
    assert reloaded.provider_configured("openai")
    assert (isolated_config / "config.yaml").exists()


def test_unknown_provider_rejected(isolated_config):
    cfg = RuntimeConfig.load()
    with pytest.raises(ValueError):
        cfg.set_provider("not-a-provider", api_key="x")


def test_env_provider_var_beats_stored(isolated_config, monkeypatch):
    cfg = _loaded(isolated_config)
    cfg.set_provider("gemini", api_key="stored")
    cfg.save()
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    merged = apply_runtime_config(Settings(), cfg)
    assert merged.llm_provider == "anthropic"


def test_provider_configured_requires_key(isolated_config):
    cfg = _loaded(isolated_config)
    assert not cfg.provider_configured("openai")
    cfg.set_provider("gemini", api_key="AIza123")
    assert cfg.provider_configured("gemini")
    assert cfg.get("gemini_api_key") == "AIza123"
    # ollama needs no key
    assert cfg.provider_configured("ollama")


def test_apply_runtime_config_fills_gaps(isolated_config):
    cfg = _loaded(isolated_config)
    cfg.set_provider("anthropic", api_key="sk-ant-secret", model="claude-sonnet-4-5")
    cfg.save()

    merged = apply_runtime_config(Settings(), cfg)
    assert merged.llm_provider == "anthropic"
    assert merged.anthropic_api_key == "sk-ant-secret"
    assert merged.anthropic_model == "claude-sonnet-4-5"


def test_env_wins_over_runtime_config(isolated_config, monkeypatch):
    cfg = _loaded(isolated_config)
    cfg.set_provider("openai", api_key="stored-key", model="gpt-4o")
    cfg.save()

    # Explicit provider selection wins over the stored config.
    merged = apply_runtime_config(Settings(llm_provider="mock"), cfg)
    assert merged.llm_provider == "mock"

    # An env var set for the credential wins over the stored value.
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
    cfg2 = _loaded(isolated_config)
    cfg2.set_provider("gemini", api_key="stored-gemini")
    cfg2.save()
    merged2 = apply_runtime_config(Settings(), cfg2)
    assert merged2.gemini_api_key == "env-gemini-key"


def test_stored_provider_is_active_without_env(isolated_config):
    cfg = _loaded(isolated_config)
    cfg.set_provider("gemini", api_key="AIza123")
    cfg.save()

    merged = apply_runtime_config(Settings(), cfg)
    assert merged.llm_provider == "gemini"
    assert merged.gemini_api_key == "AIza123"


def test_history_record_and_load(isolated_config):
    cfg = RuntimeConfig.load()
    cfg.record_review({"repository": "o/r", "high": 2, "model": "m"})
    cfg.record_review({"repository": "o/r2", "high": 0, "model": "m2"})

    entries = cfg.load_history()
    assert len(entries) == 2
    assert entries[0]["repository"] == "o/r"
    assert entries[0]["high"] == 2
    assert "ts" in entries[0]  # timestamp added
    assert (isolated_config / "history.jsonl").exists()


def test_record_review_result_builds_history_entry(isolated_config):
    """The shared CLI/TUI helper records a consistent summary entry."""
    from agentic_code_reviewer.models.findings import (
        ReviewFinding,
        Severity,
        VerificationStatus,
    )
    from agentic_code_reviewer.models.review import Review
    from agentic_code_reviewer.models.schemas import WorkflowResult

    finding = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection",
        description="User input reaches the SQL query.",
        repository="o/r",
        file_path="db.py",
        start_line=11,
        evidence="query = f\"...{user_input}\"",
        impact="Data exfiltration",
        recommendation="Use parameterised queries.",
        verification_status=VerificationStatus.VERIFIED,
    )
    review = Review(
        repository="o/r",
        summary="Reviewed db.py",
        findings=[finding],
        model="mock/model",
        pull_request=123,
    )
    result = WorkflowResult(review=review, llm_call_count=5, estimated_cost_usd=0.00123)

    RuntimeConfig.load().record_review_result(result)

    entries = RuntimeConfig.load().load_history()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "github_pr"
    assert entry["pull_request"] == 123
    assert entry["model"] == "mock/model"
    assert entry["llm_calls"] == 5
    assert entry["cost_usd"] == 0.00123
    assert entry["high"] == 1
    assert entry["findings"] == 1


def test_record_review_result_derives_source(isolated_config):
    """github_commit vs local source, and never raises on bad input."""
    from agentic_code_reviewer.models.review import Review
    from agentic_code_reviewer.models.schemas import WorkflowResult

    RuntimeConfig.load().record_review_result(
        WorkflowResult(
            review=Review(repository="o/r", summary="s", model="m", commit="abc123"),
            llm_call_count=1,
            estimated_cost_usd=0.0,
        )
    )
    RuntimeConfig.load().record_review_result(
        WorkflowResult(
            review=Review(repository="o/r", summary="s", model="m"),
            llm_call_count=1,
            estimated_cost_usd=0.0,
        )
    )
    # History is best-effort: a garbage object must never raise.
    RuntimeConfig.load().record_review_result(object())  # type: ignore[arg-type]

    sources = [e["source"] for e in RuntimeConfig.load().load_history()]
    assert sources == ["github_commit", "local"]


def test_history_limit(isolated_config):
    cfg = RuntimeConfig.load()
    for i in range(5):
        cfg.record_review({"repository": f"r{i}", "findings": i})
    assert len(cfg.load_history(limit=2)) == 2
    assert cfg.load_history()[-1]["repository"] == "r4"


def test_history_corrupt_lines_skipped(isolated_config):
    (isolated_config / "history.jsonl").write_text(
        '{"repository": "ok"}\nnot-json\n', encoding="utf-8"
    )
    cfg = RuntimeConfig.load()
    entries = cfg.load_history()
    assert len(entries) == 1
    assert entries[0]["repository"] == "ok"


def test_legacy_reviewer_config_migrated(tmp_path):
    """Pre-rename ``reviewer`` config/history migrate into the ``acr`` dir once."""
    from agentic_code_reviewer.config.runtime_config import _migrate_legacy_config

    legacy = tmp_path / "reviewer"
    legacy.mkdir()
    (legacy / "config.yaml").write_text("llm_provider: openai\n", encoding="utf-8")
    (legacy / "history.jsonl").write_text('{"repository": "old"}\n', encoding="utf-8")
    new_dir = tmp_path / "acr"
    new_dir.mkdir()  # config_dir() always creates the new dir before migrating

    _migrate_legacy_config(new_dir, legacy)
    assert (new_dir / "config.yaml").exists()
    assert (new_dir / "history.jsonl").exists()
    assert (new_dir / "config.yaml").read_text(encoding="utf-8").startswith("llm_provider")

    # Idempotent: a second call must not clobber existing new-dir data.
    (new_dir / "config.yaml").write_text("llm_provider: anthropic\n", encoding="utf-8")
    _migrate_legacy_config(new_dir, legacy)
    assert (new_dir / "config.yaml").read_text(encoding="utf-8").startswith("llm_provider: anthropic")


def test_legacy_migration_skipped_when_new_dir_has_data(tmp_path):
    from agentic_code_reviewer.config.runtime_config import _migrate_legacy_config

    legacy = tmp_path / "reviewer"
    legacy.mkdir()
    (legacy / "config.yaml").write_text("llm_provider: openai\n", encoding="utf-8")
    new_dir = tmp_path / "acr"
    new_dir.mkdir()
    (new_dir / "config.yaml").write_text("llm_provider: gemini\n", encoding="utf-8")

    _migrate_legacy_config(new_dir, legacy)
    assert (new_dir / "config.yaml").read_text(encoding="utf-8").startswith("llm_provider: gemini")


def test_active_provider_status_mock_is_ready():
    from agentic_code_reviewer.config.runtime_config import active_provider_status

    provider, model, ready = active_provider_status(Settings(LLM_PROVIDER="mock"))
    assert provider == "mock"
    assert ready is True


def test_active_provider_status_cloud_needs_key(monkeypatch):
    from agentic_code_reviewer.config.runtime_config import active_provider_status

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _, _, ready = active_provider_status(Settings(LLM_PROVIDER="openai"))
    assert ready is False
    provider, model, ready = active_provider_status(
        Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x", OPENAI_MODEL="gpt-5")
    )
    assert provider == "openai"
    assert model == "gpt-5"
    assert ready is True


def test_active_provider_status_openai_compatible_needs_base_url(monkeypatch):
    from agentic_code_reviewer.config.runtime_config import active_provider_status

    monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
    _, _, ready = active_provider_status(Settings(LLM_PROVIDER="openai_compatible"))
    assert ready is False
    _, _, ready = active_provider_status(
        Settings(LLM_PROVIDER="openai_compatible", OPENAI_COMPATIBLE_BASE_URL="http://x/v1")
    )
    assert ready is True


def test_provider_fields_mapping_complete():
    for provider in PROVIDER_FIELDS:
        assert provider in ("openai", "anthropic", "gemini", "openai_compatible", "ollama", "mock")
        for field in PROVIDER_FIELDS[provider].values():
            if field == "llm_provider":
                continue
            assert field in Settings.model_fields, f"{provider}: {field}"
