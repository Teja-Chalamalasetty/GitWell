"""Typed view of GitHub's repository payload (shared by add-repo and the scan worker)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.github.errors import GitHubError

MAX_OWNER = 39
MAX_NAME = 100
MAX_BRANCH = 255


@dataclass(frozen=True)
class RepoMeta:
    github_id: int
    owner: str  # GitHub's casing
    name: str  # GitHub's casing
    default_branch: str
    is_private: bool
    is_archived: bool
    has_issues: bool
    disabled: bool
    size_kb: int

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}".lower()

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


def _bad() -> GitHubError:
    return GitHubError({"reason": "unexpected_repo_shape"})


def parse_repo_payload(data: dict[str, Any]) -> RepoMeta:
    """Validate and normalise GET /repos/{o}/{r} or GET /repositories/{id}."""
    github_id = data.get("id")
    name = data.get("name")
    owner_obj = data.get("owner")
    branch = data.get("default_branch")
    if not isinstance(github_id, int) or isinstance(github_id, bool):
        raise _bad()
    if not isinstance(name, str) or not 0 < len(name) <= MAX_NAME:
        raise _bad()
    login = owner_obj.get("login") if isinstance(owner_obj, dict) else None
    if not isinstance(login, str) or not 0 < len(login) <= MAX_OWNER:
        raise _bad()
    if not isinstance(branch, str) or not 0 < len(branch) <= MAX_BRANCH:
        raise _bad()
    size = data.get("size", 0)
    return RepoMeta(
        github_id=github_id,
        owner=login,
        name=name,
        default_branch=branch,
        is_private=bool(data.get("private", False)),
        is_archived=bool(data.get("archived", False)),
        has_issues=bool(data.get("has_issues", True)),
        disabled=bool(data.get("disabled", False)),
        size_kb=size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else 0,
    )
