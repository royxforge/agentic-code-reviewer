"""Adapters: GitHub PRs/commits -> :class:`ReviewRequest`, and local snapshots.

The adapter fetches only what the reviewer needs: the diff, the changed-file
list, and a bounded repository snapshot (changed files plus a bounded subset of
the tree) used for context/retrieval. Context size is capped by settings to
keep PR-mode cheap.
"""

from __future__ import annotations

from pathlib import Path

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import GitHubError, ReviewerError
from agentic_code_reviewer.github.client import GitHubClient
from agentic_code_reviewer.orchestration.state import ReviewRequest

_TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".rb", ".php", ".kt", ".swift", ".sh", ".yaml",
    ".yml", ".json", ".toml", ".md", ".sql", ".html", ".css", ".xml", ".ini",
    ".cfg", ".txt", ".env", ".lock", ".gradle", ".tf", ".dockerfile",
}


def request_from_pull_request(
    client: GitHubClient, repo: str, number: int, settings: Settings
) -> ReviewRequest:
    pr = client.get_pull_request(repo, number)
    diff = client.get_pull_request_diff(repo, number)
    files = client.get_pull_request_files(repo, number)
    changed = [f.filename for f in files if not f.filename.endswith("/")]
    repo_files = _fetch_snapshot(
        client, repo, pr.head_sha or "HEAD", changed, settings
    )
    return ReviewRequest(
        repository=repo,
        pull_request=number,
        commit=pr.head_sha or None,
        diff_text=diff,
        changed_files=changed,
        repo_files=repo_files,
        source="github_pr",
        description=pr.body or "",
    )


def request_from_commit(
    client: GitHubClient, repo: str, sha: str, settings: Settings
) -> ReviewRequest:
    info = client.get_commit(repo, sha)
    diff = client.get_commit_diff(repo, sha)
    # Derive changed files from the diff headers without extra API calls.
    from agentic_code_reviewer.analysis.diff import parse_diff

    changed = [f.path for f in parse_diff(diff)]
    repo_files = _fetch_snapshot(client, repo, sha, changed, settings)
    return ReviewRequest(
        repository=repo,
        commit=info.sha,
        diff_text=diff,
        changed_files=changed,
        repo_files=repo_files,
        source="github_commit",
    )


def _fetch_snapshot(
    client: GitHubClient,
    repo: str,
    ref: str,
    changed: list[str],
    settings: Settings,
) -> dict[str, str]:
    """Fetch a bounded repository snapshot at ``ref`` for context/retrieval."""
    files: dict[str, str] = {}
    budget_bytes = settings.github_max_context_bytes
    used = 0
    candidates = list(dict.fromkeys(changed))  # dedupe, keep order
    try:
        tree = client.get_tree_paths(repo, ref)
        # Prioritise source-like files, then fill remaining budget from the tree.
        sources = [p for p in tree if p.rsplit(".", 1)[-1].lower() in {e.lstrip(".") for e in _TEXT_EXTENSIONS}]
        candidates.extend(s for s in sources if s not in candidates)
        candidates = candidates[: settings.github_max_context_files]
    except GitHubError:
        pass  # tree unavailable (e.g. anonymous on huge repos)  -  changed files only

    for path in candidates:
        if used >= budget_bytes:
            break
        try:
            content = client.get_file_content(repo, path, ref)
        except GitHubError:
            continue
        if not content.strip():
            continue
        used += len(content)
        files[path] = content
    return files


def read_local_repository(path: str, *, max_bytes: int = 2_000_000) -> dict[str, str]:
    """Read a local repository snapshot (text files only, bounded)."""
    root = Path(path)
    if not root.exists():
        raise ReviewerError(f"Local path does not exist: {path}")
    files: dict[str, str] = {}
    used = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if "/." in f"/{rel}" or rel.startswith(".git/") or rel.startswith(".venv/"):
            continue
        suffix = p.suffix.lower()
        if suffix and suffix not in _TEXT_EXTENSIONS:
            continue
        try:
            if p.stat().st_size > 1_000_000:
                continue
            content = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        if used + len(content) > max_bytes:
            break
        used += len(content)
        files[rel] = content
    return files
