from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, str_enum, utcnow

if TYPE_CHECKING:
    from app.models.repo import Repo


class DraftStatus(enum.StrEnum):
    drafted = "drafted"
    approved = "approved"
    posting = "posting"
    posted = "posted"
    discarded = "discarded"


# At most one open draft per repo (PRD 10.1), enforced by the database, like one active scan.
_OPEN = text("status IN ('drafted','approved','posting')")


class IssueDraft(Base):
    __tablename__ = "issue_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"))
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[DraftStatus] = mapped_column(str_enum(DraftStatus))
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    post_attempts: Mapped[int] = mapped_column(Integer, default=0)
    github_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_issue_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    repo: Mapped[Repo] = relationship(back_populates="issue_drafts")

    __table_args__ = (
        Index("ix_issue_drafts_repo_status", "repo_id", "status"),
        Index("ix_issue_drafts_scan_id", "scan_id"),
        Index(
            "ux_issue_drafts_open",
            "repo_id",
            unique=True,
            sqlite_where=_OPEN,
            postgresql_where=_OPEN,
        ),
    )
