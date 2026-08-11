"""Typed models for GitHub API responses."""

from __future__ import annotations

from pydantic import BaseModel


class GitHubPR(BaseModel):
    number: int
    title: str
    body: str = ""
    state: str = ""
    head_sha: str = ""
    base_sha: str = ""
    html_url: str = ""

    @property
    def is_merged(self) -> bool:
        return self.state == "merged"


class CommitInfo(BaseModel):
    sha: str
    message: str = ""
    author_name: str = ""
    author_email: str = ""
    date: str = ""


class FileChange(BaseModel):
    filename: str
    status: str = "modified"  # added | removed | modified | renamed
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    raw_url: str = ""


class ReviewComment(BaseModel):
    path: str
    line: int
    body: str
