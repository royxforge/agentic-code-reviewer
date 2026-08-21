"""Centralised configuration via pydantic-settings.

All runtime behaviour is driven by :class:`Settings`, loaded from environment
variables or the user config. Providers and API keys are stored in the user
config by ``acr configure`` / the launcher menu, so no secrets file is needed.
Secrets are never logged or written to review artifacts.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal[
    "openai", "anthropic", "ollama", "mock", "gemini", "openai_compatible"
]
EmbeddingProvider = Literal[
    "auto", "openai", "ollama", "local", "gemini", "openai_compatible"
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # ---- LLM provider ----
    llm_provider: Provider = Field(default="openai", alias="LLM_PROVIDER")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL"
    )
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-5", alias="ANTHROPIC_MODEL")

    ollama_base_url: str = Field(
        default="http://localhost:11434", alias="OLLAMA_BASE_URL"
    )
    ollama_model: str = Field(default="qwen2.5-coder:7b", alias="OLLAMA_MODEL")

    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_embedding_model: str = Field(
        default="text-embedding-004", alias="GEMINI_EMBEDDING_MODEL"
    )
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com", alias="GEMINI_BASE_URL"
    )

    # Any OpenAI-compatible endpoint (vLLM, LM Studio, Groq, OpenRouter,
    # DeepSeek, Together, ...). The API key is optional  -  local servers often
    # ignore it.
    openai_compatible_base_url: str = Field(
        default="", alias="OPENAI_COMPATIBLE_BASE_URL"
    )
    openai_compatible_api_key: str = Field(
        default="", alias="OPENAI_COMPATIBLE_API_KEY"
    )
    openai_compatible_model: str = Field(
        default="", alias="OPENAI_COMPATIBLE_MODEL"
    )
    openai_compatible_embedding_model: str = Field(
        default="", alias="OPENAI_COMPATIBLE_EMBEDDING_MODEL"
    )

    # ---- Model behaviour ----
    temperature: float = Field(default=0.2, alias="TEMPERATURE")
    max_tokens: int = Field(default=8192, alias="MAX_TOKENS")
    llm_timeout_seconds: float = Field(default=120.0, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=3, alias="LLM_MAX_RETRIES")
    llm_retry_backoff_seconds: float = Field(
        default=2.0, alias="LLM_RETRY_BACKOFF_SECONDS"
    )
    stage_timeout_seconds: float = Field(default=300.0, alias="STAGE_TIMEOUT_SECONDS")

    # ---- Review behaviour ----
    min_finding_confidence: float = Field(default=0.6, alias="MIN_FINDING_CONFIDENCE")

    # Evidence/verification strictness: which evidence statuses reach the user.
    # strict -> only confirmed; balanced -> confirmed + strongly_supported;
    # lenient -> also insufficient_evidence (never rejected).
    verification_strictness: Literal["strict", "balanced", "lenient"] = Field(
        default="balanced", alias="VERIFICATION_STRICTNESS"
    )

    # Capability switches for the upgraded analysis layers.
    history_analysis: bool = Field(default=False, alias="HISTORY_ANALYSIS")
    taint_analysis: bool = Field(default=True, alias="TAINT_ANALYSIS")
    ast_analysis: bool = Field(default=True, alias="AST_ANALYSIS")

    # Category selection. ``enabled_categories`` (when set) restricts the checks
    # the planner may request; ``disabled_categories`` removes specific checks.
    enabled_categories: list[str] = Field(
        default_factory=list, alias="ENABLED_CATEGORIES"
    )
    disabled_categories: list[str] = Field(
        default_factory=list, alias="DISABLED_CATEGORIES"
    )
    # Optional per-category model override, e.g. {"security": "gpt-4o", ...}.
    category_models: dict[str, str] = Field(default_factory=dict, alias="CATEGORY_MODELS")
    diff_token_budget: int = Field(default=14000, alias="DIFF_TOKEN_BUDGET")
    context_char_budget: int = Field(default=24000, alias="CONTEXT_CHAR_BUDGET")
    github_review_publish: bool = Field(
        default=False, alias="GITHUB_REVIEW_PUBLISH"
    )

    # ---- GitHub ----
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_base_url: str = Field(
        default="https://api.github.com", alias="GITHUB_BASE_URL"
    )
    github_timeout_seconds: float = Field(default=60.0, alias="GITHUB_TIMEOUT_SECONDS")
    github_max_context_files: int = Field(default=60, alias="GITHUB_MAX_CONTEXT_FILES")
    github_max_context_bytes: int = Field(
        default=400_000, alias="GITHUB_MAX_CONTEXT_BYTES"
    )

    # ---- Retrieval / RAG ----
    retrieval_enabled: bool = Field(default=True, alias="RETRIEVAL_ENABLED")
    retrieval_top_k: int = Field(default=6, alias="RETRIEVAL_TOP_K")
    embedding_provider: EmbeddingProvider = Field(
        default="auto", alias="EMBEDDING_PROVIDER"
    )
    chunk_max_chars: int = Field(default=1500, alias="CHUNK_MAX_CHARS")
    chunk_overlap_chars: int = Field(default=200, alias="CHUNK_OVERLAP_CHARS")
    local_embedding_dim: int = Field(default=1024, alias="LOCAL_EMBEDDING_DIM")

    # ---- Knowledgebase ----
    # Persistent, cross-run knowledge injected into analysis prompts: curated
    # rules/conventions, auto-captured verified findings, and indexed docs.
    knowledge_enabled: bool = Field(default=True, alias="KNOWLEDGE_ENABLED")
    # Directory for the JSONL knowledge store; empty -> user config dir.
    knowledge_dir: Path = Field(default=Path(""), alias="KNOWLEDGE_DIR")
    knowledge_top_k: int = Field(default=4, alias="KNOWLEDGE_TOP_K")
    # Hard cap on the rendered knowledge block injected into a prompt.
    knowledge_max_chars: int = Field(default=1200, alias="KNOWLEDGE_MAX_CHARS")
    # Persist confirmed/strongly_supported findings back into the store after
    # each review (repo memory). Off by default; opt in per user or per repo.
    knowledge_auto_capture: bool = Field(
        default=False, alias="KNOWLEDGE_AUTO_CAPTURE"
    )

    # ---- Token usage ----
    # Analysis agents emit compact finding lists; cap their output budget below
    # the general MAX_TOKENS to cut cost without losing findings.
    analysis_max_tokens: int = Field(default=4096, alias="ANALYSIS_MAX_TOKENS")
    # Emit Anthropic cache_control on the (identical) system block so the 22
    # parallel analysis agents share one cached prefix instead of 22 full reads.
    use_prompt_caching: bool = Field(default=True, alias="USE_PROMPT_CACHING")
    # Cap the findings serialized into the aggregator prompt (the LLM can only
    # down-select from what it sees; very large finding sets would otherwise
    # blow the aggregator token budget).
    aggregator_max_findings: int = Field(default=60, alias="AGGREGATOR_MAX_FINDINGS")

    # ---- LLM response cache ----
    # Content-addressed cache of structured LLM responses, keyed by
    # (prompt content, model, temperature, max_tokens). Repeat reviews of
    # identical diffs (CI) skip the LLM entirely. Off by default because it
    # changes reproducibility semantics; enable per repo or per run.
    llm_cache_enabled: bool = Field(default=False, alias="LLM_CACHE_ENABLED")
    # Cache directory (empty -> user config dir / cache).
    llm_cache_dir: Path = Field(default=Path(""), alias="LLM_CACHE_DIR")

    # ---- Evaluation / benchmark ----
    benchmark_dataset: Path = Field(
        default=Path("benchmarks/datasets/fixture_small.json"),
        alias="BENCHMARK_DATASET",
    )
    max_workers: int = Field(default=4, alias="MAX_WORKERS")

    # Ablation switches
    workflow_use_retrieval: bool = Field(default=True, alias="WORKFLOW_USE_RETRIEVAL")
    workflow_use_verifier: bool = Field(default=True, alias="WORKFLOW_USE_VERIFIER")
    workflow_use_security_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_SECURITY_AGENT"
    )
    workflow_use_testing_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_TESTING_AGENT"
    )
    workflow_use_performance_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_PERFORMANCE_AGENT"
    )
    workflow_use_maintainability_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_MAINTAINABILITY_AGENT"
    )
    workflow_use_observability_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_OBSERVABILITY_AGENT"
    )
    workflow_use_data_integrity_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_DATA_INTEGRITY_AGENT"
    )
    workflow_use_accessibility_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_ACCESSIBILITY_AGENT"
    )
    workflow_use_concurrency_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_CONCURRENCY_AGENT"
    )
    workflow_use_dependencies_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_DEPENDENCIES_AGENT"
    )
    workflow_use_privacy_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_PRIVACY_AGENT"
    )
    workflow_use_i18n_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_I18N_AGENT"
    )
    workflow_use_api_contract_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_API_CONTRACT_AGENT"
    )
    workflow_use_requirement_alignment_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_REQUIREMENT_ALIGNMENT_AGENT"
    )
    workflow_use_authorization_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_AUTHORIZATION_AGENT"
    )
    workflow_use_reliability_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_RELIABILITY_AGENT"
    )
    workflow_use_architecture_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_ARCHITECTURE_AGENT"
    )
    workflow_use_compatibility_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_COMPATIBILITY_AGENT"
    )
    workflow_use_configuration_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_CONFIGURATION_AGENT"
    )
    workflow_use_resource_lifecycle_agent: bool = Field(
        default=True, alias="WORKFLOW_USE_RESOURCE_LIFECYCLE_AGENT"
    )
    workflow_use_dead_code_checker: bool = Field(
        default=True, alias="WORKFLOW_USE_DEAD_CODE_CHECKER"
    )

    # ---- Observability ----
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: Literal["json", "text"] = Field(default="json", alias="LOG_FORMAT")

    @field_validator("llm_provider", "embedding_provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        """Accept both "openai-compatible" and "openai_compatible" spellings."""
        return value.replace("-", "_") if isinstance(value, str) else value

    @field_validator("min_finding_confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("MIN_FINDING_CONFIDENCE must be within [0, 1]")
        return value

    @field_validator(
        "max_tokens",
        "llm_max_retries",
        "analysis_max_tokens",
        "knowledge_top_k",
        "aggregator_max_findings",
    )
    @classmethod
    def _validate_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("value must be >= 1")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
