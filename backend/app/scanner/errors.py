"""Scan error catalog (PRD 8.5). `error_message` always comes from here, never from an exception."""

from __future__ import annotations

from app.github.errors import (
    GitHubEmptyRepo,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubRepoDisabled,
    GitHubRepoTooLarge,
    GitHubUnavailable,
)
from app.scanner.deadline import ScanTimeout

SCAN_ERRORS: dict[str, str] = {
    "REPO_NOT_FOUND": "Repository not found, or the token cannot access it.",
    "REPO_EMPTY": "Repository has no commits.",
    "REPO_DISABLED": "Repository is disabled on GitHub.",
    "REPO_TOO_LARGE": "Repository is too large to scan without a GitHub token.",
    "GITHUB_RATE_LIMITED": "GitHub rate limit reached; try again after the reset time.",
    "GITHUB_UNAVAILABLE": "GitHub is unavailable or timed out.",
    "GITHUB_ERROR": "Unexpected response from GitHub.",
    "SCAN_TIMEOUT": "Scan exceeded the time limit.",
    "INTERRUPTED": "Scan was interrupted by a server restart.",
    "INTERNAL_ERROR": "Unexpected internal error.",
}


def scan_error_code(exc: BaseException) -> str:
    """Map an exception to a catalog error code."""
    if isinstance(exc, ScanTimeout):
        return "SCAN_TIMEOUT"
    if isinstance(exc, GitHubEmptyRepo):
        return "REPO_EMPTY"
    if isinstance(exc, GitHubRepoDisabled):
        return "REPO_DISABLED"
    if isinstance(exc, GitHubRepoTooLarge):
        return "REPO_TOO_LARGE"
    if isinstance(exc, GitHubNotFound):
        return "REPO_NOT_FOUND"
    if isinstance(exc, GitHubRateLimited):
        return "GITHUB_RATE_LIMITED"
    if isinstance(exc, GitHubUnavailable):
        return "GITHUB_UNAVAILABLE"
    if isinstance(exc, GitHubError):
        return "GITHUB_ERROR"
    return "INTERNAL_ERROR"
