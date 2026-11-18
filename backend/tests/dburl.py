"""Which database the tests run on.

By default every test gets a fresh in-memory SQLite database. Set `TEST_DATABASE_URL` to a
PostgreSQL URL (CI's `backend-postgres` job does) to run the SAME tests against Postgres; each test
then starts from empty tables with identity counters reset, so ids behave like they do on SQLite.
"""

from __future__ import annotations

import os
from pathlib import Path

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
IS_SQLITE = not TEST_DATABASE_URL or TEST_DATABASE_URL.startswith("sqlite")
DEFAULT_URL = TEST_DATABASE_URL or "sqlite://"


def database_url(tmp_path: Path, name: str) -> str:
    """A database that several connections/threads can share.

    A SQLite file normally; the shared PostgreSQL database when `TEST_DATABASE_URL` is set.
    """
    if IS_SQLITE:
        return f"sqlite:///{(tmp_path / name).as_posix()}"
    return TEST_DATABASE_URL
