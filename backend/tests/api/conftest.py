from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.user import UserRole
from tests.helpers import PASSWORD, auth, make_user


@pytest.fixture
def login(client: TestClient) -> Callable[[str, str], str]:
    def _login(email: str, password: str = PASSWORD) -> str:
        r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        token: str = r.json()["access_token"]
        return token

    return _login


@pytest.fixture
def admin_headers(db: Session, login: Callable[[str, str], str]) -> dict[str, str]:
    make_user(db, "admin@example.com", UserRole.admin)
    return auth(login("admin@example.com"))


@pytest.fixture
def viewer_headers(db: Session, login: Callable[[str, str], str]) -> dict[str, str]:
    make_user(db, "viewer@example.com", UserRole.viewer)
    return auth(login("viewer@example.com"))
