
import httpx

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.github.adapter import request_from_commit, request_from_pull_request
from agentic_code_reviewer.github.client import GitHubClient

PR_JSON = {
    "number": 1,
    "title": "Fix search",
    "body": "changes the query building",
    "state": "open",
    "head": {"sha": "headsha123"},
    "base": {"sha": "basesha"},
    "html_url": "https://github.com/o/r/pull/1",
}

FILES_JSON = [
    {"filename": "db.py", "status": "modified", "additions": 2, "deletions": 2, "changes": 4, "raw_url": ""}
]

DIFF_TEXT = """\
diff --git a/db.py b/db.py
--- a/db.py
+++ b/db.py
@@ -10,7 +10,7 @@ def search_users(query):
-    sql = "SELECT * FROM users WHERE name LIKE ?"
+    sql = f"SELECT * FROM users WHERE name LIKE '%{query}%'"
     return _db.execute(sql, params)
"""

TREE_JSON = {"tree": [{"path": "db.py", "type": "blob"}, {"path": "app.py", "type": "blob"}]}

CONTENT_JSON = {
    "content": "aW1wb3J0IHNxbGl0ZTMKX2RiID0gTm9uZQo=",  # "import sqlite3\n_db = None\n"
}


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url.path
        accept = request.headers.get("accept", "")
        if url == "/repos/o/r/pulls/1" and "vnd.github.v3.diff" in accept:
            return httpx.Response(200, text=DIFF_TEXT)
        if url == "/repos/o/r/pulls/1":
            return httpx.Response(200, json=PR_JSON)
        if url == "/repos/o/r/pulls/1/files":
            return httpx.Response(200, json=FILES_JSON)
        if url == "/repos/o/r/git/trees/headsha123" and (
            request.url.params.get("recursive") == "1"
        ):
            return httpx.Response(200, json=TREE_JSON)
        if url == "/repos/o/r/contents/db.py":
            return httpx.Response(200, json=CONTENT_JSON)
        if url.startswith("/repos/o/r/commits/") and "vnd.github.v3.diff" in accept:
            return httpx.Response(200, text=DIFF_TEXT)
        if url.startswith("/repos/o/r/commits/"):
            return httpx.Response(
                200,
                json={
                    "sha": "abc123",
                    "commit": {
                        "message": "fix",
                        "author": {"name": "n", "email": "e", "date": "d"},
                    },
                },
            )
        return httpx.Response(404, text="unhandled")

    return httpx.MockTransport(handler)


def _settings() -> Settings:
    return Settings(LLM_PROVIDER="mock", LOG_LEVEL="WARNING")


def test_pr_to_review_request():
    client = GitHubClient(_settings(), transport=_transport())
    request = request_from_pull_request(client, "o/r", 1, _settings())
    assert request.repository == "o/r"
    assert request.pull_request == 1
    assert request.commit == "headsha123"
    assert "db.py" in request.changed_files
    assert "diff --git a/db.py" in request.diff_text
    assert request.repo_files["db.py"].startswith("import sqlite3")
    # The PR description flows in as the stated requirement.
    assert request.description == "changes the query building"


def test_commit_to_review_request():
    client = GitHubClient(_settings(), transport=_transport())
    request = request_from_commit(client, "o/r", "abc123", _settings())
    assert request.commit == "abc123"
    assert request.source == "github_commit"
    assert "db.py" in request.changed_files


def test_github_error_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    import pytest

    from agentic_code_reviewer.errors import GitHubError

    client = GitHubClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(GitHubError) as excinfo:
        client.get_pull_request("o/r", 999)
    assert "404" in str(excinfo.value)
