"""Tests for per-repository config (.reviewer.yaml) loading and quality gates."""

import pytest

from agentic_code_reviewer.config.file_config import (
    ConfigError,
    QualityGate,
    RepoConfig,
    apply_repo_config,
    config_notice,
    find_config_file,
    load_config_file,
    load_repo_config,
    should_ignore,
)
from agentic_code_reviewer.config.settings import Settings

VALID_CONFIG = """
quality_gate:
  critical: 0
  high: 3
  medium: 20
  low: null
  info: null

ignore_patterns:
  - "**/test_*.py"
  - "docs/**"

severity_weights:
  critical: 1.0
  high: 0.8
  medium: 0.5
  low: 0.2
  info: 0.0

max_tokens: 4096
temperature: 0.1

providers:
  llm_provider: mock
  openai_model: gpt-4o-mini
"""


def test_load_valid_config(tmp_path):
    cfg_file = tmp_path / ".reviewer.yaml"
    cfg_file.write_text(VALID_CONFIG, encoding="utf-8")
    config = load_config_file(cfg_file)
    assert config.quality_gate.high == 3
    assert config.quality_gate.low is None
    assert config.ignore_patterns == ["**/test_*.py", "docs/**"]
    assert config.max_tokens == 4096
    assert config.temperature == 0.1
    assert config.providers["llm_provider"] == "mock"
    assert config.source_path == cfg_file


def test_find_config_walks_up(tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (tmp_path / ".reviewer.yaml").write_text("quality_gate:\n  critical: 1\n", encoding="utf-8")
    assert find_config_file(nested) == tmp_path / ".reviewer.yaml"


def test_load_repo_config_defaults_when_missing(tmp_path):
    config = load_repo_config(tmp_path)
    assert config.source_path is None
    assert not config.quality_gate.configured
    assert config.ignore_patterns == []


def test_unknown_top_level_key_rejected(tmp_path):
    cfg_file = tmp_path / ".reviewer.yaml"
    cfg_file.write_text("quality_gate:\n  critical: 0\nbogus_key: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="bogus_key"):
        load_config_file(cfg_file)


def test_invalid_gate_value_rejected(tmp_path):
    cfg_file = tmp_path / ".reviewer.yaml"
    cfg_file.write_text("quality_gate:\n  critical: not-a-number\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="quality_gate"):
        load_config_file(cfg_file)


def test_quality_gate_violations():
    gate = QualityGate(critical=0, high=3)
    assert gate.violations({"critical": 1, "high": 2}) == ["critical: 1 > 0"]
    assert gate.violations({"critical": 0, "high": 2}) == []
    assert gate.violations({"critical": 0, "high": 4}) == ["high: 4 > 3"]


def test_ignore_patterns():
    config = RepoConfig(ignore_patterns=["**/test_*.py", "docs/**"])
    assert should_ignore(config, "src/tests/test_foo.py")
    assert should_ignore(config, "docs/architecture.md")
    assert not should_ignore(config, "src/app.py")


def test_apply_repo_config_overrides_settings():
    config = RepoConfig(max_tokens=2048, temperature=0.9, providers={"openai_model": "gpt-5"})
    settings = Settings()
    merged = apply_repo_config(settings, config)
    assert merged.max_tokens == 2048
    assert merged.temperature == 0.9
    assert merged.openai_model == "gpt-5"
    # Original settings untouched.
    assert settings.max_tokens != 2048


def test_config_notice_empty_for_default():
    assert config_notice(RepoConfig()) == ""


def test_ignore_patterns_comma_string_accepted():
    config = RepoConfig(ignore_patterns="**/test_*.py, docs/**")
    assert config.ignore_patterns == ["**/test_*.py", "docs/**"]
