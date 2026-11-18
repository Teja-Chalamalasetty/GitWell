"""Test helpers for creating users and authenticated requests."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.rules.base import Rule
from app.rules.registry import get_rules
from app.services import user_service

PASSWORD = "test-correct-horse-1"

# The eight rules GitWell v1 ships. Tests that assert exact scores run scans with THIS set, so
# adding a rule later (one source file + its test) cannot change their expectations.
V1_RULE_IDS = ("secrets", "readme", "tests", "ci", "license", "dependencies", "gitignore", "docs")


def v1_rules() -> tuple[Rule, ...]:
    return tuple(r for r in get_rules() if r.id in V1_RULE_IDS)


def make_user(
    db: Session,
    email: str = "user@example.com",
    role: UserRole = UserRole.viewer,
    password: str = PASSWORD,
    active: bool = True,
) -> User:
    user = user_service.create_user(db, email, password, role)
    if not active:
        user.is_active = False
        db.commit()
    return user


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class InlineExecutor:
    """Runs submitted work immediately in the caller's thread (deterministic API tests)."""

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future[Any]:
        future: Future[Any] = Future()
        try:
            future.set_result(fn(*args))
        except BaseException as exc:  # noqa: BLE001 - mirror a real executor
            future.set_exception(exc)
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        pass


class ManualExecutor:
    """Queues work until `run_all()`, so tests can observe a scan while it is still queued."""

    def __init__(self) -> None:
        self.pending: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        self.pending.append((fn, args))

    def run_all(self) -> None:
        pending, self.pending = self.pending, []
        for fn, args in pending:
            fn(*args)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        if cancel_futures:
            self.pending.clear()


def login_as(client: Any, email: str, role: UserRole) -> dict[str, str]:
    """Create a user directly in the DB and return its auth headers."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        make_user(db, email, role)
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return auth(r.json()["access_token"])
