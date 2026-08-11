"""Tests for interactive triage (diff extraction, git apply, prompt loop)."""

import io
import subprocess

from agentic_code_reviewer.cli.interactive import _extract_diff, apply_patch, run_triage
from agentic_code_reviewer.models.findings import ReviewFinding, Severity

OLD_DB = (
    'def search_users(query):\n'
    '    sql = "SELECT * FROM users WHERE name = \'%s\'" % query\n'
    '    return _db.execute(sql)\n'
)

PATCH = """diff --git a/db.py b/db.py
--- a/db.py
+++ b/db.py
@@ -1,3 +1,5 @@
 def search_users(query):
-    sql = "SELECT * FROM users WHERE name = '%s'" % query
-    return _db.execute(sql)
+    if query is None:
+        return []
+    sql = f"SELECT * FROM users WHERE name LIKE '%{query}%'"
+    return _db.execute(sql)
"""


def _finding(title="SQL injection", recommendation=f"```diff\n{PATCH}\n```"):
    return ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title=title,
        description="SQL injection",
        file_path="db.py",
        start_line=11,
        recommendation=recommendation,
    )


def test_extract_diff_from_fenced_block():
    diff = _extract_diff(_finding().recommendation)
    assert diff is not None
    assert diff.startswith("diff --git a/db.py b/db.py")
    assert "+    return _db.execute(sql)" in diff


def test_extract_diff_stops_at_next_section():
    recommendation = (
        f"```diff\n{PATCH}\n```\n\n### Alternative\nUse an ORM instead."
    )
    diff = _extract_diff(recommendation)
    assert diff is not None
    assert "Alternative" not in diff


def test_extract_diff_returns_none_without_diff():
    assert _extract_diff("Just use an ORM.") is None
    assert _extract_diff("") is None


def test_apply_patch_rejects_when_no_local_path():
    ok, message = apply_patch(None, PATCH)
    assert not ok
    assert "no local repository" in message


def test_apply_patch_applies_to_clean_tree(tmp_path):
    if not _git_available():
        return
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "db.py").write_text(OLD_DB, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    ok, message = apply_patch(str(tmp_path), PATCH)
    assert ok, message
    content = (tmp_path / "db.py").read_text(encoding="utf-8")
    assert 'f"SELECT * FROM users WHERE name LIKE' in content
    assert "if query is None:" in content


def test_run_triage_skip_default():
    out = io.StringIO()
    counts = run_triage(
        [_finding()],
        input_stream=io.StringIO("\n"),  # empty input -> skip
        console=_console(out),
    )
    assert counts["skip"] == 1
    assert counts["apply"] == 0


def test_run_triage_quit():
    out = io.StringIO()
    counts = run_triage(
        [_finding()],
        input_stream=io.StringIO("q\n"),
        console=_console(out),
    )
    assert counts["quit"] == 1


def test_run_triage_apply_without_diff_dismisses():
    out = io.StringIO()
    counts = run_triage(
        [_finding(recommendation="No diff here.")],
        input_stream=io.StringIO("a\n"),
        local_path=None,
        console=_console(out),
    )
    assert counts["dismiss"] == 1  # no diff to apply -> reported, not applied


def test_run_triage_auto_apply_dismisses_when_no_diff():
    out = io.StringIO()
    counts = run_triage(
        [_finding(recommendation="No diff here.")],
        local_path=None,
        auto_apply=True,
        console=_console(out),
    )
    assert counts["dismiss"] == 1


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def _console(out: io.StringIO):
    from rich.console import Console

    return Console(file=out, force_terminal=False, width=100, highlight=False)
