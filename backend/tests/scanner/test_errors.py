from datetime import UTC, datetime

import pytest

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
from app.scanner.errors import SCAN_ERRORS, scan_error_code


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (GitHubNotFound(), "REPO_NOT_FOUND"),
        (GitHubEmptyRepo(), "REPO_EMPTY"),
        (GitHubRepoDisabled(), "REPO_DISABLED"),
        (GitHubRepoTooLarge(), "REPO_TOO_LARGE"),
        (GitHubRateLimited(datetime(2026, 1, 1, tzinfo=UTC)), "GITHUB_RATE_LIMITED"),
        (GitHubUnavailable(), "GITHUB_UNAVAILABLE"),
        (GitHubError(), "GITHUB_ERROR"),
        (ScanTimeout(), "SCAN_TIMEOUT"),
        (RuntimeError("anything"), "INTERNAL_ERROR"),
        (KeyError("x"), "INTERNAL_ERROR"),
    ],
)
def test_exceptions_map_to_catalog_codes(exc: BaseException, code: str) -> None:
    assert scan_error_code(exc) == code
    assert code in SCAN_ERRORS


def test_catalog_matches_the_prd_exactly() -> None:
    assert SCAN_ERRORS == {
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


def test_every_github_error_class_message_is_in_the_catalog() -> None:
    for exc in (
        GitHubNotFound(),
        GitHubEmptyRepo(),
        GitHubRepoDisabled(),
        GitHubRepoTooLarge(),
        GitHubUnavailable(),
        GitHubError(),
    ):
        assert str(exc) == SCAN_ERRORS[scan_error_code(exc)]
