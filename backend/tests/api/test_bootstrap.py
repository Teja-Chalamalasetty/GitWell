from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.db import Base, SessionLocal, init_engine
from app.main import create_app
from app.models.user import User, UserRole
from app.services.user_service import bootstrap_admin
from tests.conftest import make_settings
from tests.dburl import database_url
from tests.helpers import make_user


def test_bootstrap_creates_admin(db: Session) -> None:
    bootstrap_admin(
        db, make_settings(admin_email="Boss@Example.com", admin_password="test-bootstrap-pw")
    )
    user = db.scalars(select(User)).one()
    assert user.email == "boss@example.com"
    assert user.role == UserRole.admin and user.is_active
    assert verify_password("test-bootstrap-pw", user.password_hash)


def test_bootstrap_never_modifies_existing_user(db: Session) -> None:
    existing = make_user(db, "boss@example.com", UserRole.viewer, password="test-original-password")
    bootstrap_admin(
        db, make_settings(admin_email="boss@example.com", admin_password="different-pw1")
    )
    db.refresh(existing)
    assert existing.role == UserRole.viewer  # not promoted
    assert verify_password("test-original-password", existing.password_hash)  # not reset
    assert len(db.scalars(select(User)).all()) == 1


@pytest.mark.parametrize(
    "kwargs", [{"admin_email": "a@example.com"}, {"admin_password": "password-123"}]
)
def test_bootstrap_with_only_one_variable_warns_and_skips(
    db: Session, kwargs: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="gitwell.users"):
        bootstrap_admin(db, make_settings(**kwargs))
    assert db.scalars(select(User)).all() == []
    assert any("bootstrap_admin_skipped" in r.getMessage() for r in caplog.records)


def test_bootstrap_without_variables_is_a_noop(db: Session) -> None:
    bootstrap_admin(db, make_settings())
    assert db.scalars(select(User)).all() == []


def test_bootstrap_rejects_invalid_email(db: Session) -> None:
    with pytest.raises(ValueError, match="ADMIN_EMAIL"):
        bootstrap_admin(
            db, make_settings(admin_email="not-an-email", admin_password="password-123")
        )


def test_startup_runs_bootstrap_and_admin_can_log_in(tmp_path: Path) -> None:
    url = database_url(tmp_path, "boot.db")
    engine = init_engine(url)
    Base.metadata.create_all(engine)  # stands in for `alembic upgrade head`
    settings = make_settings(
        database_url=url, admin_email="boss@example.com", admin_password="test-bootstrap-pw"
    )
    with TestClient(create_app(settings)) as c:
        r = c.post(
            "/api/v1/auth/login",
            json={"email": "boss@example.com", "password": "test-bootstrap-pw"},
        )
        assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    # a second startup must not fail or duplicate
    with TestClient(create_app(settings)):
        pass
    with SessionLocal() as s:
        assert len(s.scalars(select(User)).all()) == 1
    SessionLocal.kw["bind"].dispose()
