"""Typed GitHub exceptions and their mapping to API errors (PRD 12).

Messages are fixed strings: never built from response bodies or URLs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import error_response
from app.github.url import InvalidRepoUrl


class GitHubError(Exception):
    """Unexpected response from GitHub."""

    message = "Unexpected response from GitHub."

    def __init__(self, details: dict[str, Any] | None = None) -> None:
        super().__init__(self.message)
        self.details: dict[str, Any] = details or {}


class GitHubNotFound(GitHubError):
    message = "Repository not found, or the token cannot access it."


class GitHubRateLimited(GitHubError):
    message = "GitHub rate limit reached; try again after the reset time."

    def __init__(self, reset_at: datetime, details: dict[str, Any] | None = None) -> None:
        super().__init__(details)
        self.reset_at = reset_at.astimezone(UTC)


class GitHubUnavailable(GitHubError):
    message = "GitHub is unavailable or timed out."


class GitHubEmptyRepo(GitHubError):
    message = "Repository has no commits."


class GitHubRepoDisabled(GitHubError):
    message = "Repository is disabled on GitHub."


class GitHubRepoTooLarge(GitHubError):
    message = "Repository is too large to scan without a GitHub token."


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def github_error_response(exc: GitHubError) -> JSONResponse:
    """Map a GitHub exception to the API error shape (PRD 11.1)."""
    if isinstance(exc, GitHubRateLimited):
        return error_response(
            503, "GITHUB_RATE_LIMITED", exc.message, {"reset_at": _iso_z(exc.reset_at)}
        )
    if isinstance(exc, GitHubUnavailable):
        return error_response(503, "GITHUB_UNAVAILABLE", exc.message)
    if isinstance(exc, GitHubNotFound | GitHubRepoDisabled):
        return error_response(422, "REPO_NOT_FOUND", GitHubNotFound.message, dict(exc.details))
    return error_response(502, "GITHUB_ERROR", GitHubError.message)


def register_github_handlers(app: FastAPI) -> None:
    @app.exception_handler(GitHubError)
    async def _github(_: Request, exc: GitHubError) -> JSONResponse:
        return github_error_response(exc)

    @app.exception_handler(InvalidRepoUrl)
    async def _bad_url(_: Request, exc: InvalidRepoUrl) -> JSONResponse:
        return error_response(422, "INVALID_REPO_URL", str(exc))
