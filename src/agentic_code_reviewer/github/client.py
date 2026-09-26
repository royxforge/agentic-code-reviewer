"""GitHub REST client.

All GitHub access flows through this single abstraction (dependency-injected,
token read from the environment, never hardcoded). Endpoints used: PRs,
commits, diffs, contents, git trees, and review publishing (opt-in).
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.errors import GitHubError
from agentic_code_reviewer.github.models import CommitInfo, FileChange, GitHubPR, ReviewComment

_ACCEPT_JSON = "application/vnd.github+json"
_ACCEPT_DIFF = "application/vnd.github.v3.diff"
_API_VERSION = "2022-11-28"


class GitHubClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = settings.github_token
        self._base_url = settings.github_base_url.rstrip("/")
        headers = {
            "Accept": _ACCEPT_JSON,
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "agentic-code-reviewer",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._client = httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=settings.github_timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token)

    # ------------------------------------------------------------------
    def get_repository(self, repo: str) -> dict:
        return self._request_json(f"/repos/{repo}")

    def get_pull_request(self, repo: str, number: int) -> GitHubPR:
        data = self._request_json(f"/repos/{repo}/pulls/{number}")
        return GitHubPR(
            number=data["number"],
            title=data.get("title", ""),
            body=data.get("body") or "",
            state=data.get("state", ""),
            head_sha=(data.get("head") or {}).get("sha", ""),
            base_sha=(data.get("base") or {}).get("sha", ""),
            html_url=data.get("html_url", ""),
        )

    def get_pull_request_diff(self, repo: str, number: int) -> str:
        return self._request_text(f"/repos/{repo}/pulls/{number}", accept=_ACCEPT_DIFF)

    def get_pull_request_files(self, repo: str, number: int) -> list[FileChange]:
        data = self._request_json_paginated(
            f"/repos/{repo}/pulls/{number}/files", per_page=100
        )
        return [
            FileChange(
                filename=item.get("filename", ""),
                status=item.get("status", "modified"),
                additions=item.get("additions", 0),
                deletions=item.get("deletions", 0),
                changes=item.get("changes", 0),
                raw_url=item.get("raw_url", ""),
            )
            for item in data
        ]

    def get_commit(self, repo: str, sha: str) -> CommitInfo:
        data = self._request_json(f"/repos/{repo}/commits/{sha}")
        commit = data.get("commit") or {}
        author = commit.get("author") or {}
        return CommitInfo(
            sha=data.get("sha", sha),
            message=(commit.get("message") or "").splitlines()[0] if commit.get("message") else "",
            author_name=author.get("name", ""),
            author_email=author.get("email", ""),
            date=author.get("date", ""),
        )

    def get_commit_diff(self, repo: str, sha: str) -> str:
        return self._request_text(f"/repos/{repo}/commits/{sha}", accept=_ACCEPT_DIFF)

    def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """Fetch a file's text content at ``ref`` (contents API, base64)."""
        data = self._request_json(f"/repos/{repo}/contents/{path}?ref={ref}")
        import base64

        content = data.get("content", "")
        if not content:
            return ""
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""

    def get_tree_paths(self, repo: str, sha: str) -> list[str]:
        """Recursive git tree file paths at ``sha``.

        GitHub truncates recursive trees for very large repositories and sets
        ``truncated: true``; surface that instead of silently reviewing a
        partial file list.
        """
        data = self._request_json(f"/repos/{repo}/git/trees/{sha}?recursive=1")
        if data.get("truncated"):
            raise GitHubError(
                f"Git tree for {repo}@{sha} was truncated by GitHub; the "
                "repository is too large for recursive listing. Restrict the "
                "review to a subdirectory instead of reviewing a partial file set."
            )
        paths = []
        for item in data.get("tree", []):
            if item.get("type") == "blob":
                paths.append(item.get("path", ""))
        return paths

    def create_review(
        self,
        repo: str,
        number: int,
        commit_id: str,
        body: str,
        comments: list[ReviewComment] | None = None,
        event: str = "COMMENT",
    ) -> dict:
        """Publish a review on a PR (opt-in; requires a write-scoped token)."""
        if not self.is_authenticated:
            raise GitHubError("Cannot publish a review without a GITHUB_TOKEN")
        payload: dict = {
            "commit_id": commit_id,
            "body": body,
            "event": event,
            "comments": [
                {"path": c.path, "line": c.line, "body": c.body[:65000]}
                for c in (comments or [])
            ],
        }
        return self._request_json(f"/repos/{repo}/pulls/{number}/reviews", method="POST", json=payload)

    # ------------------------------------------------------------------
    def _request_json(self, url: str, **params: Any) -> Any:
        resp = self._client.get(url, params=cast(Any, params) if params else None)
        self._raise_for_status(resp, url)
        try:
            return resp.json()
        except ValueError as exc:
            raise GitHubError(f"Invalid JSON from GitHub for {url}", detail=str(exc)) from exc

    def _request_json_paginated(self, url: str, *, per_page: int = 100, max_pages: int = 40) -> list:
        """Fetch every page of a list endpoint via the ``Link`` header.

        GitHub caps ``per_page`` at 100; a single request on PR files
        (commonly >100 for large PRs) silently reviewed only the first page.
        ``max_pages`` bounds the request count for pathological inputs.
        """
        import re

        results: list = []
        next_url: str | None = url
        next_params: dict[str, Any] | None = {"per_page": per_page}
        pages = 0
        link_re = re.compile(r'<([^>]+)>;\s*rel="next"')

        while next_url and pages < max_pages:
            resp = self._client.get(next_url, params=next_params)
            self._raise_for_status(resp, next_url)
            try:
                page = resp.json()
            except ValueError as exc:
                raise GitHubError(f"Invalid JSON from GitHub for {next_url}", detail=str(exc)) from exc
            if not isinstance(page, list):
                raise GitHubError(f"Expected a JSON list from {next_url}, got {type(page).__name__}")
            results.extend(page)

            link_header = resp.headers.get("Link", "")
            match = link_re.search(link_header)
            next_url = match.group(1) if match else None
            next_params = None  # pagination URLs already carry their query params
            pages += 1

        if next_url:
            raise GitHubError(
                f"Pagination for {url} exceeded {max_pages} pages; refusing to "
                "review a silently truncated file list."
            )
        return results

    def _request_text(self, url: str, *, accept: str) -> str:
        resp = self._client.get(url, headers={"Accept": accept})
        self._raise_for_status(resp, url)
        return resp.text

    def _raise_for_status(self, resp: httpx.Response, url: str) -> None:
        if resp.status_code < 400:
            return
        if resp.status_code == 401:
            raise GitHubError(f"GitHub authentication failed for {url} (401)")
        if resp.status_code == 403:
            raise GitHubError(f"GitHub request forbidden for {url} (403)  -  check token scope / rate limit")
        if resp.status_code == 404:
            raise GitHubError(f"GitHub resource not found: {url} (404)")
        if resp.status_code == 429:
            raise GitHubError(f"GitHub rate limited for {url} (429)")
        raise GitHubError(f"GitHub request failed: {url} ({resp.status_code})")
