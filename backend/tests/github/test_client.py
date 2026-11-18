from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from freezegun import freeze_time

from app.github.client import GitHubClient
from app.github.errors import (
    GitHubEmptyRepo,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
)
from app.github.ratelimit_state import RateLimitState
from app.scanner.deadline import Deadline, ScanTimeout
from tests.github.conftest import BASE

REPO = {"id": 42, "name": "r", "owner": {"login": "o"}}


def repo_route(mock: respx.MockRouter) -> respx.Route:
    return mock.get(f"{BASE}/repos/o/r")


# ---- success and headers -------------------------------------------------------------------


def test_200_returns_json_and_sends_required_headers(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    route = repo_route(mock).respond(200, json=REPO)
    assert client.get_repo("o", "r", deadline) == REPO
    sent = route.calls.last.request.headers
    assert sent["accept"] == "application/vnd.github+json"
    assert sent["x-github-api-version"] == "2022-11-28"
    assert sent["user-agent"] == "GitWell/1.0"
    assert sent["authorization"] == "Bearer tkn"


def test_no_authorization_header_without_a_token(
    mock: respx.MockRouter, make_client: Callable[..., GitHubClient], deadline: Deadline
) -> None:
    route = repo_route(mock).respond(200, json=REPO)
    anonymous = make_client(token=None)
    assert not anonymous.has_token
    anonymous.get_repo("o", "r", deadline)
    assert "authorization" not in route.calls.last.request.headers


def test_get_repo_by_id(mock: respx.MockRouter, client: GitHubClient, deadline: Deadline) -> None:
    mock.get(f"{BASE}/repositories/42").respond(200, json=REPO)
    assert client.get_repo_by_id(42, deadline)["id"] == 42


def test_rate_limit_state_is_updated_from_every_response(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, state: RateLimitState
) -> None:
    repo_route(mock).respond(
        200, json=REPO, headers={"X-RateLimit-Remaining": "4321", "X-RateLimit-Reset": "1795000000"}
    )
    client.get_repo("o", "r", deadline)
    assert state.remaining == 4321
    assert state.reset_at == datetime.fromtimestamp(1795000000, UTC)


def test_invalid_json_body_is_a_github_error(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(200, content=b"<html>not json</html>")
    with pytest.raises(GitHubError) as exc:
        client.get_repo("o", "r", deadline)
    assert type(exc.value) is GitHubError


# ---- error classification ------------------------------------------------------------------


@pytest.mark.parametrize("status", [404, 451])
def test_404_and_451_are_not_found(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, status: int
) -> None:
    repo_route(mock).respond(status, json={"message": "Not Found"})
    with pytest.raises(GitHubNotFound):
        client.get_repo("o", "r", deadline)


def test_plain_403_on_a_repo_lookup_is_not_found(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(403, json={"message": "Resource not accessible"})
    with pytest.raises(GitHubNotFound):
        client.get_repo("o", "r", deadline)


def test_403_with_remaining_zero_is_rate_limited_with_reset_header(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(
        403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1795000000"}
    )
    with pytest.raises(GitHubRateLimited) as exc:
        client.get_repo("o", "r", deadline)
    assert exc.value.reset_at == datetime.fromtimestamp(1795000000, UTC)


@freeze_time("2026-11-18 12:00:00")
def test_429_with_retry_after_wins_over_other_signals(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(
        429,
        headers={
            "Retry-After": "120",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1795000000",
        },
    )
    with pytest.raises(GitHubRateLimited) as exc:
        client.get_repo("o", "r", deadline)
    assert exc.value.reset_at == datetime(2026, 11, 18, 12, 2, tzinfo=UTC)


@freeze_time("2026-11-18 12:00:00")
def test_403_with_retry_after_is_rate_limited(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(403, headers={"Retry-After": "30"})
    with pytest.raises(GitHubRateLimited) as exc:
        client.get_repo("o", "r", deadline)
    assert exc.value.reset_at == datetime(2026, 11, 18, 12, 0, 30, tzinfo=UTC)


@freeze_time("2026-11-18 12:00:00")
def test_secondary_rate_limit_body_means_60_seconds(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(
        403, json={"message": "You have exceeded a Secondary Rate Limit. Please wait."}
    )
    with pytest.raises(GitHubRateLimited) as exc:
        client.get_repo("o", "r", deadline)
    assert exc.value.reset_at == datetime.now(UTC) + timedelta(seconds=60)


def test_403_with_sso_header_is_not_found_with_a_reason(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(403, headers={"X-GitHub-SSO": "required; url=https://x"})
    with pytest.raises(GitHubNotFound) as exc:
        client.get_repo("o", "r", deadline)
    assert exc.value.details == {"reason": "sso_required"}
    assert "https://x" not in str(exc.value)


@pytest.mark.parametrize("status", [400, 401, 410, 422])
def test_other_4xx_are_generic_errors_and_never_retried(
    mock: respx.MockRouter,
    client: GitHubClient,
    deadline: Deadline,
    sleeps: list[float],
    status: int,
) -> None:
    route = repo_route(mock).respond(status, json={"message": "secret details here"})
    with pytest.raises(GitHubError) as exc:
        client.get_repo("o", "r", deadline)
    assert type(exc.value) is GitHubError
    assert "secret details" not in str(exc.value)
    assert route.call_count == 1 and sleeps == []


def test_403_is_a_generic_error_when_not_a_repo_lookup(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    resp = httpx.Response(403, json={"message": "Forbidden"})
    assert type(client.classify(resp, repo_lookup=False)) is GitHubError
    assert isinstance(client.classify(resp, repo_lookup=True), GitHubNotFound)


def test_error_messages_are_fixed_strings_not_response_bodies(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(404, json={"message": "leaky-body-text"})
    with pytest.raises(GitHubNotFound) as exc:
        client.get_repo("o", "r", deadline)
    assert str(exc.value) == "Repository not found, or the token cannot access it."


# ---- retries -------------------------------------------------------------------------------


def test_503_is_retried_then_succeeds(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, sleeps: list[float]
) -> None:
    route = repo_route(mock)
    route.side_effect = [httpx.Response(503), httpx.Response(200, json=REPO)]
    assert client.get_repo("o", "r", deadline) == REPO
    assert route.call_count == 2
    assert sleeps == [1.0]


def test_two_retries_with_backoff_then_unavailable(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, sleeps: list[float]
) -> None:
    route = repo_route(mock).respond(503)
    with pytest.raises(GitHubUnavailable):
        client.get_repo("o", "r", deadline)
    assert route.call_count == 3  # 1 attempt + 2 retries
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize("status", [502, 504])
def test_502_and_504_are_retried_too(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, status: int
) -> None:
    route = repo_route(mock)
    route.side_effect = [httpx.Response(status), httpx.Response(200, json=REPO)]
    assert client.get_repo("o", "r", deadline) == REPO


def test_500_is_unavailable_but_not_retried(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, sleeps: list[float]
) -> None:
    route = repo_route(mock).respond(500)
    with pytest.raises(GitHubUnavailable):
        client.get_repo("o", "r", deadline)
    assert route.call_count == 1 and sleeps == []


def test_timeouts_are_retried_then_unavailable(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, sleeps: list[float]
) -> None:
    route = repo_route(mock)
    route.side_effect = httpx.ReadTimeout("slow")
    with pytest.raises(GitHubUnavailable):
        client.get_repo("o", "r", deadline)
    assert route.call_count == 3 and sleeps == [1.0, 2.0]


def test_network_error_then_success(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    route = repo_route(mock)
    route.side_effect = [httpx.ConnectError("down"), httpx.Response(200, json=REPO)]
    assert client.get_repo("o", "r", deadline) == REPO


def test_post_style_requests_can_opt_out_of_retries(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, sleeps: list[float]
) -> None:
    route = mock.post(f"{BASE}/repos/o/r/issues").respond(503)
    resp = client.request(
        "POST",
        "/repos/o/r/issues",
        endpoint="/repos/{owner}/{repo}/issues",
        deadline=deadline,
        json={"title": "t"},
        retry=False,
    )
    assert resp.status_code == 503 and route.call_count == 1 and sleeps == []


# ---- redirects and the host allowlist ------------------------------------------------------


def test_rename_301_on_the_api_host_is_followed(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(301, headers={"Location": f"{BASE}/repositories/42"})
    mock.get(f"{BASE}/repositories/42").respond(200, json=REPO)
    assert client.get_repo("o", "r", deadline) == REPO


def test_relative_redirect_locations_are_resolved(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(302, headers={"Location": "/repositories/42"})
    mock.get(f"{BASE}/repositories/42").respond(200, json=REPO)
    assert client.get_repo("o", "r", deadline) == REPO


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/steal",
        "https://api.github.com.evil.example/x",
        "http://api.github.com/repositories/42",  # scheme downgrade
        "https://codeload.github.com/o/r",  # another GitHub host is still not the API host
        "https://user:pw@evil.example/",
    ],
)
def test_redirect_to_a_foreign_host_is_rejected_without_a_request(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, location: str
) -> None:
    repo_route(mock).respond(301, headers={"Location": location})
    with pytest.raises(GitHubError):
        client.get_repo("o", "r", deadline)
    assert len(mock.calls) == 1  # nothing was sent to the foreign host


def test_redirect_without_location_is_an_error(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(301)
    with pytest.raises(GitHubError):
        client.get_repo("o", "r", deadline)


def test_at_most_three_redirects(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(url__regex=rf"{BASE}/hop\d+|{BASE}/repos/o/r").mock(
        side_effect=lambda req: httpx.Response(302, headers={"Location": f"{BASE}/hop1"})
    )
    with pytest.raises(GitHubError):
        client.get_repo("o", "r", deadline)
    assert len(mock.calls) == 4  # the original request plus 3 followed redirects


def test_three_redirects_then_success_is_fine(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    repo_route(mock).respond(301, headers={"Location": f"{BASE}/a"})
    mock.get(f"{BASE}/a").respond(301, headers={"Location": f"{BASE}/b"})
    mock.get(f"{BASE}/b").respond(301, headers={"Location": f"{BASE}/c"})
    mock.get(f"{BASE}/c").respond(200, json=REPO)
    assert client.get_repo("o", "r", deadline) == REPO


def test_send_refuses_hosts_other_than_the_api_host(
    client: GitHubClient, deadline: Deadline
) -> None:
    with pytest.raises(GitHubError):
        client._send("GET", "https://evil.example/x", endpoint="x", deadline=deadline)


def test_custom_api_base_url_host_is_the_allowlist(
    mock: respx.MockRouter, make_client: Callable[..., GitHubClient], deadline: Deadline
) -> None:
    mock.get("https://github.test/api/repos/o/r").respond(200, json=REPO)
    c = make_client(base_url="https://github.test/api")
    assert c.get_repo("o", "r", deadline) == REPO


# ---- other endpoints -----------------------------------------------------------------------


def test_commit_sha_uses_the_sha_media_type_and_quotes_the_branch(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    sha = "a" * 40
    route = mock.get(url__regex=r".*/repos/o/r/commits/.*").respond(200, text=sha + "\n")
    assert client.get_commit_sha("o", "r", "feature/x y", deadline) == sha
    request = route.calls.last.request
    assert request.headers["accept"] == "application/vnd.github.sha"
    assert request.url.raw_path.decode().endswith("/commits/feature%2Fx%20y")


def test_empty_repository_409(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(f"{BASE}/repos/o/r/commits/main").respond(
        409, json={"message": "Git Repository is empty."}
    )
    with pytest.raises(GitHubEmptyRepo):
        client.get_commit_sha("o", "r", "main", deadline)


@pytest.mark.parametrize("body", ["", "not-a-sha", "g" * 40, "a" * 65])
def test_unexpected_commit_body_is_an_error(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, body: str
) -> None:
    mock.get(f"{BASE}/repos/o/r/commits/main").respond(200, text=body)
    with pytest.raises(GitHubError):
        client.get_commit_sha("o", "r", "main", deadline)


def test_get_tree_requests_recursive(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    route = mock.get(f"{BASE}/repos/o/r/git/trees/abc").respond(
        200, json={"tree": [], "truncated": False}
    )
    assert client.get_tree("o", "r", "abc", deadline) == {"tree": [], "truncated": False}
    assert route.calls.last.request.url.params["recursive"] == "1"


def test_get_file_raw_uses_raw_media_type_ref_and_quotes_the_path(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    route = mock.get(url__regex=r".*/contents/.*").respond(200, content=b"raw bytes")
    assert client.get_file_raw("o", "r", "dir with space/a#b.txt", "abc", deadline) == b"raw bytes"
    req = route.calls.last.request
    assert req.headers["accept"] == "application/vnd.github.raw+json"
    assert req.url.params["ref"] == "abc"
    assert req.url.raw_path.decode().startswith("/repos/o/r/contents/dir%20with%20space/a%23b.txt")


# ---- deadline ------------------------------------------------------------------------------


def test_expired_deadline_raises_before_any_request(
    mock: respx.MockRouter, client: GitHubClient
) -> None:
    d = Deadline(60)
    d.expire()
    with pytest.raises(ScanTimeout):
        client.get_repo("o", "r", d)
    assert len(mock.calls) == 0


def test_timeouts_are_clamped_to_the_remaining_deadline(
    mock: respx.MockRouter, client: GitHubClient
) -> None:
    route = repo_route(mock).respond(200, json=REPO)
    client.get_repo("o", "r", Deadline(3))
    t = route.calls.last.request.extensions["timeout"]
    assert t["connect"] <= 3 and t["read"] <= 3
    client.get_repo("o", "r", Deadline(600))
    t = route.calls.last.request.extensions["timeout"]
    assert t["connect"] == 5 and t["read"] == 20


def test_deadline_expiring_during_backoff_is_a_scan_timeout(
    mock: respx.MockRouter, make_client: Callable[..., GitHubClient]
) -> None:
    d = Deadline(60)
    client = GitHubClient(BASE, "t", sleep=lambda s: d.expire(), state=RateLimitState())
    repo_route(mock).respond(503)
    with pytest.raises(ScanTimeout):
        client.get_repo("o", "r", d)
    client.close()


def test_a_timeout_caused_by_the_deadline_is_scan_timeout_not_unavailable(
    mock: respx.MockRouter, client: GitHubClient
) -> None:
    d = Deadline(60)

    def boom(request: httpx.Request) -> httpx.Response:
        d.expire()
        raise httpx.ReadTimeout("deadline hit")

    repo_route(mock).mock(side_effect=boom)
    with pytest.raises(ScanTimeout):
        client.get_repo("o", "r", d)


# ---- logging (S6) --------------------------------------------------------------------------


def test_logs_use_route_templates_and_never_urls_or_tokens(
    mock: respx.MockRouter,
    client: GitHubClient,
    deadline: Deadline,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    repo_route(mock).respond(200, json=REPO)
    client.get_repo("o", "r", deadline)
    text = " ".join(
        f"{r.getMessage()} {getattr(r, 'endpoint', '')} {r.__dict__}" for r in caplog.records
    )
    assert "/repos/{owner}/{repo}" in text
    assert "tkn" not in text and "api.github.com" not in text


def test_httpx_loggers_are_forced_to_warning(client: GitHubClient) -> None:
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
