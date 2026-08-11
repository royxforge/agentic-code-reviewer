from __future__ import annotations

from agentic_code_reviewer.agents.dead_code import DeadCodeChecker
from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.llm.mock_client import MockLLMClient
from agentic_code_reviewer.orchestration.state import ReviewRequest, ReviewState


def _checker() -> DeadCodeChecker:
    return DeadCodeChecker(Settings(LLM_PROVIDER="mock"), MockLLMClient())


def _diff(old: list[str], new: list[str], path: str = "app.py") -> str:
    """A minimal unified diff: ``old`` file lines -> ``new`` file lines."""
    lines = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        f"@@ -1,{len(old)} +1,{len(new)} @@",
    ]
    lines.extend(f"-{o}" for o in old)
    lines.extend(f"+{n}" for n in new)
    return "\n".join(lines)


def _run_checker(old: list[str], new: list[str], path: str = "app.py"):
    repo_files = {path: "\n".join(new) + "\n"}
    state = ReviewState(
        request=ReviewRequest(
            repository="o/r",
            diff_text=_diff(old, new, path),
            changed_files=[path],
            repo_files=repo_files,
            source="inline",
        )
    )
    return _checker().run(state, state.request.diff_text).result


def test_unused_import_is_reported():
    result = _run_checker(
        ["old"], ["import os", "import unused_helper", "print(os.name)"]
    )
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.category == "dead_code"
    assert finding.file_path == "app.py"
    assert finding.start_line == 2
    assert finding.rule_id == "dead-code-import"


def test_used_import_is_not_reported():
    result = _run_checker(
        ["old"], ["import unused_helper", "unused_helper.run()"]
    )
    assert result.findings == []


def test_import_used_in_other_file_is_not_reported():
    path = "app.py"
    repo_files = {
        path: "import unused_helper\n",
        "cli.py": "unused_helper.main()\n",
    }
    state = ReviewState(
        request=ReviewRequest(
            repository="o/r",
            diff_text=_diff(["old"], ["import unused_helper"], path),
            changed_files=[path],
            repo_files=repo_files,
            source="inline",
        )
    )
    result = _checker().run(state, state.request.diff_text).result
    assert result.findings == []


def test_from_import_with_alias():
    used = _run_checker(
        ["old"], ["from pkg import helper as h", "print(h.VALUE)"]
    )
    assert used.findings == []  # alias `h` is referenced

    unused = _run_checker(["old"], ["from pkg import helper as h"])
    assert len(unused.findings) == 1
    assert unused.findings[0].start_line == 1


def test_multiple_imports_on_one_line():
    result = _run_checker(["old"], ["import os, sys", "print(os.name)"])
    # `os` is referenced, `sys` is not.
    assert len(result.findings) == 1
    assert "sys" in result.findings[0].title


def test_js_named_import():
    result = _run_checker(
        ["old"],
        [
            "import { useState, useEffect } from 'react'",
            "const [x] = useState(0)",
        ],
        path="ui.tsx",
    )
    assert len(result.findings) == 1
    assert "useEffect" in result.findings[0].title


def test_empty_repo_files_produce_no_findings():
    state = ReviewState(
        request=ReviewRequest(
            repository="o/r",
            diff_text=_diff(["old"], ["import unused_helper"]),
            changed_files=["app.py"],
            repo_files={},
            source="inline",
        )
    )
    result = _checker().run(state, state.request.diff_text).result
    assert result.findings == []


def test_non_import_changes_produce_no_findings():
    result = _run_checker(["old"], ["x = 1"])
    assert result.findings == []
