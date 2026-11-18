"""Shared setup for the issue-draft tests: an app with a GitHub token and one scanned repo."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.user import UserRole
from tests.dburl import database_url
from tests.fake_github import BARE_FILES, FakeGitHub
from tests.helpers import login_as

V1 = "/api/v1"


@dataclass
class World:
    client: TestClient
    headers: dict[str, str]
    gh: FakeGitHub
    repo: dict[str, Any]

    @property
    def scan_id(self) -> int:
        return int(self.repo["latest_completed_scan"]["id"])

    def create(self, scan_id: int | None = None, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        return self.client.post(
            f"{V1}/scans/{scan_id or self.scan_id}/issue-draft", headers=headers or self.headers
        )

    def draft(self) -> dict[str, Any]:
        r = self.create()
        assert r.status_code == 201, r.text
        body: dict[str, Any] = r.json()
        return body

    def get(self, draft_id: int) -> dict[str, Any]:
        body: dict[str, Any] = self.client.get(
            f"{V1}/issue-drafts/{draft_id}", headers=self.headers
        ).json()
        return body

    def rescan(self, files: dict[str, str] | None = None, sha: str = "b" * 40) -> None:
        if files is not None:
            self.gh.repo.files = files
        self.gh.repo.sha = sha
        self.client.post(f"{V1}/repos/{self.repo['id']}/scan", headers=self.headers)
        self.repo = self.client.get(f"{V1}/repos/{self.repo['id']}", headers=self.headers).json()


def build_world(start_app: Callable[..., TestClient], gh: FakeGitHub, **settings: Any) -> World:
    settings.setdefault("github_token", "tkn")
    client = start_app(**settings)
    headers = login_as(client, "admin@example.com", UserRole.admin)
    gh.repo.files = BARE_FILES
    added = client.post(f"{V1}/repos", headers=headers, json={"url": "https://github.com/o/r"})
    assert added.status_code == 201, added.text
    return World(client, headers, gh, added.json())


@pytest.fixture
def world(start_app: Callable[..., TestClient], gh: FakeGitHub) -> World:
    return build_world(start_app, gh)


@pytest.fixture
def file_world(start_app: Callable[..., TestClient], gh: FakeGitHub, tmp_path: Path) -> World:
    """Same, on a file database, for tests that use real threads."""
    url = database_url(tmp_path, "world.db")
    return build_world(start_app, gh, database_url=url)


@pytest.fixture
def db(world: World) -> Iterator[Session]:
    """A session on the `world` app's database (the shared `db` fixture builds another app)."""
    with SessionLocal() as session:
        yield session
