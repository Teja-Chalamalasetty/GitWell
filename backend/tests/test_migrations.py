"""Alembic upgrade/downgrade round trip on SQLite (Postgres is exercised in CI)."""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401
from app.db import Base
from tests.dburl import IS_SQLITE

BACKEND = Path(__file__).resolve().parents[1]

# These round-trip a throwaway SQLite file; CI's Postgres job runs alembic on Postgres itself.
pytestmark = pytest.mark.skipif(not IS_SQLITE, reason="SQLite migration round trip")


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, str]:
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg, url


def test_upgrade_then_downgrade(alembic_cfg: tuple[Config, str]) -> None:
    cfg, url = alembic_cfg
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    insp = inspect(engine)
    tables = {"users", "repos", "scans", "scan_results", "issue_drafts"}
    assert tables <= set(insp.get_table_names())
    indexes = {i["name"]: i for i in insp.get_indexes("scans")}
    assert {"ux_scans_active", "ix_scans_repo_status_created", "ix_scans_status"} <= set(indexes)
    assert indexes["ux_scans_active"]["unique"]
    engine.dispose()

    command.downgrade(cfg, "base")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()

    command.upgrade(cfg, "head")  # and back up again


def test_models_match_migration(alembic_cfg: tuple[Config, str]) -> None:
    """No drift between the SQLAlchemy models and the migration."""
    cfg, url = alembic_cfg
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        assert compare_metadata(ctx, Base.metadata) == []
    engine.dispose()
