"""Database engine, sessions, base class and UTC datetime type (PRD §6.1)."""

from __future__ import annotations

import enum
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores UTC; rejects naive datetimes on write; returns aware UTC on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; use timezone-aware datetimes")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def str_enum(cls: type[enum.Enum]) -> Enum:
    """String-stored enum (PRD 6.1): persist enum *values*, no native enum type."""
    return Enum(cls, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e])


def utcnow() -> datetime:
    return datetime.now(UTC)


def _set_sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)
        event.listen(engine, "connect", _set_sqlite_pragmas)
        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=20, max_overflow=20)


SessionLocal: sessionmaker[Session] = sessionmaker(autoflush=False, expire_on_commit=False)
_engine: Engine | None = None


def init_engine(url: str) -> Engine:
    """(Re)bind the global session factory to a fresh engine."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(url)
    SessionLocal.configure(bind=_engine)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database engine not initialised")
    return _engine


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
