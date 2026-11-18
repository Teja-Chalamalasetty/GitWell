"""Reuse the GitHub client fixtures (respx router, deadline) for scanner tests.

The GitHub client is exposed as `gh_client` so it never shadows the FastAPI `client` fixture.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.github.client import GitHubClient
from tests.github.conftest import (  # noqa: F401
    deadline,
    make_client,
    mock,
    sleeps,
    state,
)


@pytest.fixture
def gh_client(make_client: Callable[..., GitHubClient]) -> GitHubClient:  # noqa: F811
    return make_client()
