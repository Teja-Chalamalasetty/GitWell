"""Shared fixtures. Environment is set before any `app` import."""

from __future__ import annotations

import os

os.environ["ENV"] = "test"
os.environ["JWT_SECRET"] = "test-secret"
from tests.dburl import DEFAULT_URL, IS_SQLITE, TEST_DATABASE_URL  # noqa: E402

os.environ["DATABASE_URL"] = DEFAULT_URL
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ.pop("GITHUB_TOKEN", None)

from collections.abc import Callable, Iterator  # noqa: E402
from contextlib import ExitStack  # noqa: E402

import pytest  # noqa: E402
import respx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Base, SessionLocal, get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.fake_github import FakeGitHub  # noqa: E402
from tests.helpers import InlineExecutor, v1_rules  # noqa: E402


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "test",
        "jwt_secret": "test-secret",
        "database_url": DEFAULT_URL,
        "scheduler_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_database() -> Iterator[None]:
    """On PostgreSQL every test starts from empty tables with ids restarting at 1."""
    if not IS_SQLITE:
        engine = create_engine(TEST_DATABASE_URL, poolclass=NullPool)
        Base.metadata.create_all(engine)
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        engine.dispose()
    yield


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, executor=InlineExecutor(), rules_provider=v1_rules)
    with TestClient(app, raise_server_exceptions=False) as c:
        Base.metadata.create_all(get_engine())
        yield c


@pytest.fixture
def db(client: TestClient) -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def gh() -> Iterator[FakeGitHub]:
    """A fake GitHub. Any request it does not handle fails the test (assert_all_mocked)."""
    with respx.mock(assert_all_called=False) as router:
        yield FakeGitHub(router)


@pytest.fixture
def start_app() -> Iterator[Callable[..., TestClient]]:
    """Start an extra app with custom settings/executor; closed automatically."""
    stack = ExitStack()

    def start(executor: object | None = None, **overrides: object) -> TestClient:
        app = create_app(
            make_settings(**overrides),
            executor=executor or InlineExecutor(),
            rules_provider=v1_rules,
        )
        c = stack.enter_context(TestClient(app, raise_server_exceptions=False))
        Base.metadata.create_all(get_engine())
        return c

    yield start
    stack.close()
