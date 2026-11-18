from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.errors import API_PREFIX
from app.github.errors import (
    GitHubEmptyRepo,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubRepoDisabled,
    GitHubRepoTooLarge,
    GitHubUnavailable,
)
from app.github.ratelimit_state import RateLimitState
from app.github.url import InvalidRepoUrl
from app.main import create_app
from tests.conftest import make_settings


def build() -> TestClient:
    app = create_app(make_settings())
    router = APIRouter(prefix=API_PREFIX)
    reset = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    raisers = {
        "not-found": GitHubNotFound({"reason": "sso_required"}),
        "disabled": GitHubRepoDisabled(),
        "rate-limited": GitHubRateLimited(reset),
        "unavailable": GitHubUnavailable(),
        "empty": GitHubEmptyRepo(),
        "too-large": GitHubRepoTooLarge(),
        "generic": GitHubError({"status": 418}),
    }

    def make_endpoint(exc: GitHubError) -> Callable[[], None]:
        def endpoint() -> None:
            raise exc

        return endpoint

    for name, exc in raisers.items():
        router.add_api_route(f"/gh/{name}", make_endpoint(exc))

    @router.get("/gh/bad-url")
    def bad_url() -> None:
        raise InvalidRepoUrl("Enter a URL like https://github.com/owner/repo.")

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("name", "status", "code"),
    [
        ("not-found", 422, "REPO_NOT_FOUND"),
        ("disabled", 422, "REPO_NOT_FOUND"),
        ("rate-limited", 503, "GITHUB_RATE_LIMITED"),
        ("unavailable", 503, "GITHUB_UNAVAILABLE"),
        ("empty", 502, "GITHUB_ERROR"),
        ("too-large", 502, "GITHUB_ERROR"),
        ("generic", 502, "GITHUB_ERROR"),
        ("bad-url", 422, "INVALID_REPO_URL"),
    ],
)
def test_github_errors_map_to_api_errors(name: str, status: int, code: str) -> None:
    with build() as c:
        r = c.get(f"/api/v1/gh/{name}")
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


def test_rate_limited_error_carries_reset_at() -> None:
    with build() as c:
        err = c.get("/api/v1/gh/rate-limited").json()["error"]
    assert err["details"] == {"reset_at": "2026-11-18T12:00:00Z"}


def test_api_error_messages_never_leak_exception_details() -> None:
    with build() as c:
        body = c.get("/api/v1/gh/generic").text
    assert "418" not in body


def test_rate_limit_state_tracks_headers_and_ignores_garbage() -> None:
    state = RateLimitState()
    assert state.snapshot() == (None, None)
    state.update({"x-ratelimit-remaining": "10", "x-ratelimit-reset": "1795000000"})
    assert state.snapshot() == (10, datetime.fromtimestamp(1795000000, UTC))
    state.update({"x-ratelimit-remaining": "banana"})
    assert state.remaining == 10
    state.update({})
    assert state.remaining == 10


def test_is_low_only_when_nearly_exhausted_and_not_yet_reset() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    state = RateLimitState()
    assert not state.is_low(now=now)
    state.remaining, state.reset_at = 49, now + timedelta(minutes=5)
    assert state.is_low(now=now)
    state.remaining = 50
    assert not state.is_low(now=now)
    state.remaining, state.reset_at = 0, now - timedelta(seconds=1)
    assert not state.is_low(now=now)  # the window already reset
    state.clear()
    assert state.snapshot() == (None, None)
