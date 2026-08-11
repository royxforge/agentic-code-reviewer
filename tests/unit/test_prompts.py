import pytest

from agentic_code_reviewer.errors import ConfigurationError
from agentic_code_reviewer.llm.prompts import available_prompt_versions, load_prompt, render


def test_all_llm_stages_have_v1_prompts():
    for stage in (
        "planner",
        "change_analyzer",
        "correctness",
        "security",
        "error_handling",
        "testing",
        "regression",
        "performance",
        "maintainability",
        "observability",
        "data_integrity",
        "accessibility",
        "concurrency",
        "dependencies",
        "privacy",
        "i18n",
        "api_contract",
        "requirement_alignment",
        "authorization",
        "reliability",
        "architecture",
        "compatibility",
        "configuration",
        "resource_lifecycle",
        "aggregator",
        "baseline_single",
        "baseline_context",
        "baseline_rag",
    ):
        versions = available_prompt_versions(stage)
        assert "v1" in versions, f"{stage} missing v1 prompt"
        prompt = load_prompt(stage, "v1")
        assert prompt.system
        assert prompt.task


def test_render_substitutes_tokens():
    system, task = render(
        "planner",
        "v1",
        DIFF="d",
        CHANGED_FILES="a.py",
        REPOSITORY="o/r",
        DECOMPOSITION_HINT="",
        REQUIREMENT="",
    )
    assert "d" in task
    assert "a.py" in task


def test_requirement_alignment_prompt_uses_requirement_token():
    system, task = render(
        "requirement_alignment",
        "v1",
        DIFF="d",
        PLAN="{}",
        CHANGE_SUMMARY="{}",
        CONTEXT="",
        CHANGED_FILES="a.py",
        REPOSITORY="o/r",
        REQUIREMENT="make search case-insensitive",
        HISTORY="",
    )
    assert "make search case-insensitive" in task


def test_planner_prompt_rejects_missing_requirement():
    with pytest.raises(ConfigurationError):
        render(
            "planner",
            "v1",
            DIFF="d",
            CHANGED_FILES="a.py",
            REPOSITORY="o/r",
            DECOMPOSITION_HINT="",
        )


def test_render_missing_token_raises():
    with pytest.raises(ConfigurationError):
        render("planner", "v1", DIFF="d")


def test_unknown_stage_raises():
    with pytest.raises(ConfigurationError):
        load_prompt("not_a_stage")
