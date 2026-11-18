from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
import respx

from app.github.client import GitHubClient
from app.github.ratelimit_state import RateLimitState
from app.scanner.deadline import Deadline

BASE = "https://api.github.com"


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    # assert_all_mocked=True (the default): any unmocked request fails the test.
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def state() -> RateLimitState:
    return RateLimitState()


@pytest.fixture
def make_client(
    sleeps: list[float], state: RateLimitState
) -> Iterator[Callable[..., GitHubClient]]:
    clients: list[GitHubClient] = []

    def factory(token: str | None = "tkn", base_url: str = BASE) -> GitHubClient:
        client = GitHubClient(base_url, token, sleep=sleeps.append, state=state)
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.close()


@pytest.fixture
def client(make_client: Callable[..., GitHubClient]) -> GitHubClient:
    return make_client()


@pytest.fixture
def deadline() -> Deadline:
    return Deadline(60)
