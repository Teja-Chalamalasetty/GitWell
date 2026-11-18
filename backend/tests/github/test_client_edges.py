"""Edge cases that complete the client's coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from freezegun import freeze_time

from app.github.client import GitHubClient
from app.github.errors import GitHubError, GitHubNotFound, GitHubRateLimited
from app.github.ratelimit_state import RateLimitState
from app.scanner.deadline import Deadline
from tests.github.conftest import BASE


def test_client_is_a_context_manager(mock: respx.MockRouter, deadline: Deadline) -> None:
    mock.get(f"{BASE}/repos/o/r").respond(200, json={"id": 1})
    with GitHubClient(BASE, None, state=RateLimitState()) as c:
        assert c.get_repo("o", "r", deadline) == {"id": 1}
    assert c._http.is_closed


@pytest.mark.parametrize("call", ["repo", "by_id", "tree"])
def test_json_that_is_not_an_object_is_an_error(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, call: str
) -> None:
    mock.get(url__regex=rf"{BASE}/.*").respond(200, json=["not", "an", "object"])
    with pytest.raises(GitHubError):
        {
            "repo": lambda: client.get_repo("o", "r", deadline),
            "by_id": lambda: client.get_repo_by_id(1, deadline),
            "tree": lambda: client.get_tree("o", "r", "abc", deadline),
        }[call]()


@pytest.mark.parametrize("call", ["by_id", "commit", "tree", "file"])
def test_errors_are_classified_on_every_endpoint(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline, call: str
) -> None:
    mock.get(url__regex=rf"{BASE}/.*").respond(404)
    with pytest.raises(GitHubNotFound):
        {
            "by_id": lambda: client.get_repo_by_id(1, deadline),
            "commit": lambda: client.get_commit_sha("o", "r", "main", deadline),
            "tree": lambda: client.get_tree("o", "r", "abc", deadline),
            "file": lambda: client.get_file_raw("o", "r", "a.txt", "abc", deadline),
        }[call]()


def test_redirect_with_a_query_string_drops_the_original_params(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(f"{BASE}/repos/o/r/git/trees/abc").respond(
        301, headers={"Location": f"{BASE}/repositories/1/git/trees/abc?recursive=1"}
    )
    final = mock.get(f"{BASE}/repositories/1/git/trees/abc").respond(200, json={"tree": []})
    assert client.get_tree("o", "r", "abc", deadline) == {"tree": []}
    assert final.calls.last.request.url.params.multi_items() == [("recursive", "1")]


@freeze_time("2026-11-18 12:00:00")
def test_non_numeric_retry_after_falls_back_to_60_seconds(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(f"{BASE}/repos/o/r").respond(
        429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    )
    with pytest.raises(GitHubRateLimited) as exc:
        client.get_repo("o", "r", deadline)
    assert exc.value.reset_at == datetime.now(UTC) + timedelta(seconds=60)


@freeze_time("2026-11-18 12:00:00")
def test_remaining_zero_with_a_bad_reset_header_falls_back_to_60_seconds(
    mock: respx.MockRouter, client: GitHubClient, deadline: Deadline
) -> None:
    mock.get(f"{BASE}/repos/o/r").respond(403, headers={"X-RateLimit-Remaining": "0"})
    with pytest.raises(GitHubRateLimited) as exc:
        client.get_repo("o", "r", deadline)
    assert exc.value.reset_at == datetime.now(UTC) + timedelta(seconds=60)


def test_error_body_that_is_not_json_is_handled(client: GitHubClient) -> None:
    resp = httpx.Response(403, content=b"<html>nope</html>")
    assert isinstance(client.classify(resp), GitHubNotFound)
    resp = httpx.Response(403, json=["a list"])
    assert isinstance(client.classify(resp), GitHubNotFound)
    resp = httpx.Response(403, json={"message": 5})
    assert isinstance(client.classify(resp), GitHubNotFound)
