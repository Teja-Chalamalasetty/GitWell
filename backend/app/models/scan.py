from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, str_enum, utcnow

if TYPE_CHECKING:
    from app.models.repo import Repo


class ScanStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class ScanTrigger(enum.StrEnum):
    manual = "manual"
    scheduled = "scheduled"
    on_add = "on_add"


class RuleStatus(enum.StrEnum):
    pass_ = "pass"
    warn = "warn"
    fail = "fail"
    not_applicable = "not_applicable"
    error = "error"


_ACTIVE = text("status IN ('queued','running')")


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    status: Mapped[ScanStatus] = mapped_column(str_enum(ScanStatus))
    trigger: Mapped[ScanTrigger] = mapped_column(str_enum(ScanTrigger))
    triggered_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grade: Mapped[str | None] = mapped_column(String(1), nullable=True)
    rules_errored: Mapped[int] = mapped_column(Integer, default=0)
    ruleset_version: Mapped[str | None] = mapped_column(String(12), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    repo: Mapped[Repo] = relationship(back_populates="scans")
    results: Mapped[list[ScanResult]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index(
            "ux_scans_active",
            "repo_id",
            unique=True,
            sqlite_where=_ACTIVE,
            postgresql_where=_ACTIVE,
        ),
        Index("ix_scans_repo_status_created", "repo_id", "status", "created_at"),
        Index("ix_scans_status", "status"),
    )


class ScanResult(Base):
    __tablename__ = "scan_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"))
    rule_id: Mapped[str] = mapped_column(String(50))
    status: Mapped[RuleStatus] = mapped_column(str_enum(RuleStatus))
    message: Mapped[str] = mapped_column(String(1000))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    weight: Mapped[int] = mapped_column(Integer)
    display_order: Mapped[int] = mapped_column(Integer)

    scan: Mapped[Scan] = relationship(back_populates="results")

    __table_args__ = (UniqueConstraint("scan_id", "rule_id"),)
