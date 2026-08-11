"""Local git repository ingestion.

Uses read-only ``git`` subprocess calls to build a :class:`ReviewRequest` from
the working tree (vs ``HEAD`` by default) or an arbitrary base/range. Only
read-only git commands are executed; nothing is written or pushed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentic_code_reviewer.errors import NoChangesError, NotAGitRepositoryError, ReviewerError
from agentic_code_reviewer.github.adapter import read_local_repository
from agentic_code_reviewer.orchestration.state import ReviewRequest


def _git(path: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            # Explicit UTF-8 + replace: on Windows ``text=True`` defaults to the
            # locale codec (cp1252), which crashes on non-ASCII diff bytes and
            # leaves ``proc.stdout`` as ``None``. ``errors="replace"`` makes it
            # impossible to crash on undecodable bytes, ever.
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ReviewerError(f"git command failed: {exc}") from exc
    if proc.returncode != 0:
        raise ReviewerError(
            f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout


def is_git_repository(path: str) -> bool:
    try:
        return _git(Path(path), "rev-parse", "--is-inside-work-tree").strip() == "true"
    except ReviewerError:
        return False


def local_request(path: str, base: str = "HEAD") -> ReviewRequest:
    root = Path(path).resolve()
    if not is_git_repository(str(root)):
        raise NotAGitRepositoryError(
            f"{path} is not a git repository. Initialize it or use a GitHub flow."
        )
    # ``git diff <base>`` covers both staged and unstaged changes vs <base>.
    combined = _git(root, "diff", base).strip()
    if not combined:
        raise NoChangesError(f"No changes found between working tree and {base}.")

    name_only = _git(root, "diff", "--name-only", base).strip()
    changed = [f for f in name_only.splitlines() if f]
    repo_files = read_local_repository(str(root))

    repo_name = _repository_name(root)
    return ReviewRequest(
        repository=repo_name,
        diff_text=combined,
        changed_files=changed,
        repo_files=repo_files,
        local_path=str(root),
        source="local",
    )


def local_request_with_fallback(
    path: str, base: str | None = None
) -> tuple[ReviewRequest, str]:
    """Build a local review request, auto-falling back to the last commit.

    ``review-local`` reviews the working tree against ``base``. When ``base``
    is ``None`` (the claude-style bare ``acr`` case) and the working tree
    is clean  -  nothing to review  -  we fall back to ``HEAD~1`` so the command
    always has something meaningful to review: the most recent commit's
    changes. An explicit ``base`` (including ``HEAD``) is always honoured and
    never silently falls back.

    Returns ``(request, notice)`` where ``notice`` is a short human-readable
    explanation (empty when reviewing the working tree) for the caller to
    surface in the UI.
    """
    if base is None:
        try:
            return local_request(path, base="HEAD"), ""
        except NoChangesError:
            pass
        try:
            return local_request(path, base="HEAD~1"), (
                "No uncommitted changes in the working tree  -  "
                "reviewing the last commit (HEAD~1)."
            )
        except ReviewerError as exc:
            # Fresh repo (no HEAD~1) or other git failure: explain instead of
            # leaking a raw git error to the user.
            raise NoChangesError(
                "Nothing to review yet: no uncommitted changes and no previous "
                "commit to fall back to.",
                detail=str(exc),
            ) from None
    return local_request(path, base=base), ""


def _repository_name(root: Path) -> str:
    try:
        remote = _git(root, "remote", "get-url", "origin").strip()
        # Normalize ssh/git/https URLs to owner/name.
        remote = remote.replace("git@", "").replace("https://", "").replace("http://", "")
        remote = remote.split(":", 1)[-1] if ":" in remote.split("/")[0] else remote
        parts = [p for p in remote.replace(".git", "").split("/") if p]
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
    except ReviewerError:
        pass
    return root.name or "local-repository"
