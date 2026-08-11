import shutil
import subprocess

import pytest

from agentic_code_reviewer.errors import ReviewerError
from agentic_code_reviewer.local.git import local_request, local_request_with_fallback


@pytest.fixture
def git_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "db.py").write_text(
        'def search_users(query):\n    sql = "SELECT * FROM users WHERE name LIKE ?"\n    return sql\n',
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_local_request_captures_uncommitted_changes(git_repo):
    (git_repo / "db.py").write_text(
        'def search_users(query):\n    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n    return sql\n',
        encoding="utf-8",
    )
    request = local_request(str(git_repo))
    assert request.source == "local"
    assert "db.py" in request.changed_files
    assert "diff --git a/db.py b/db.py" in request.diff_text
    assert "db.py" in request.repo_files
    assert request.repository  # derived from dir name


def test_local_request_with_non_ascii_diff(git_repo):
    """Windows regression: git output containing bytes invalid in cp1252
    (e.g. CJK/emoji) used to crash the reader thread and return ``None``.
    The diff must be captured losslessly."""
    (git_repo / "db.py").write_text(
        '# 用户搜索  -  non-ASCII comment 🚀\n'
        'def search_users(query):\n'
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        '    return sql\n',
        encoding="utf-8",
    )
    request = local_request(str(git_repo))
    assert "diff --git a/db.py b/db.py" in request.diff_text
    assert "用户搜索" in request.diff_text
    assert "🚀" in request.diff_text


def test_local_request_no_changes_raises(git_repo):
    with pytest.raises(ReviewerError):
        local_request(str(git_repo))


def test_local_request_not_a_repo(tmp_path):
    with pytest.raises(ReviewerError):
        local_request(str(tmp_path))


# ---------------------------------------------------------------------------
# local_request_with_fallback (claude-style bare `acr`)
# ---------------------------------------------------------------------------


def _second_commit(git_repo):
    """Add a second commit that meaningfully changes db.py, so HEAD~1 differs from HEAD."""
    db = "\n".join(
        [
            'def search_users(query):',
            '    sql = "SELECT * FROM users WHERE name LIKE ?"',
            '    return sql',
            '',
            'def sanitize(value):',
            '    return value.replace(";", "")',
        ]
    )
    (git_repo / "db.py").write_text(db + "\n", encoding="utf-8")
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-q", "-m", "second")


def test_fallback_reviews_last_commit_when_clean(git_repo):
    """Clean tree: bare invocation should fall back to reviewing HEAD~1..HEAD."""
    _second_commit(git_repo)
    request, notice = local_request_with_fallback(str(git_repo))
    assert notice  # explains the fallback
    assert "last commit" in notice
    assert request.source == "local"
    assert "diff --git a/db.py b/db.py" in request.diff_text


def test_fallback_prefers_working_tree_changes(git_repo):
    """Dirty tree: the working-tree diff is used and no fallback notice is set."""
    _second_commit(git_repo)
    (git_repo / "db.py").write_text(
        'def search_users(query):\n    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n    return sql\n',
        encoding="utf-8",
    )
    request, notice = local_request_with_fallback(str(git_repo))
    assert notice == ""
    assert "f\"SELECT" in request.diff_text


def test_fallback_respects_explicit_base(git_repo):
    """An explicit --base must never silently fall back."""
    _second_commit(git_repo)
    with pytest.raises(ReviewerError):
        local_request_with_fallback(str(git_repo), base="HEAD")  # explicit, clean tree


def test_fallback_still_raises_on_non_repo(tmp_path):
    with pytest.raises(ReviewerError):
        local_request_with_fallback(str(tmp_path))


def test_fallback_friendly_error_on_single_commit_repo(git_repo):
    """Clean tree + only one commit: explain instead of leaking a git error."""
    with pytest.raises(ReviewerError, match="Nothing to review yet"):
        local_request_with_fallback(str(git_repo))
