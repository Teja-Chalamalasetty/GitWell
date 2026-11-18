from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow

if TYPE_CHECKING:
    from app.models.issue_draft import IssueDraft
    from app.models.scan import Scan


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    url: Mapped[str] = mapped_column(String(300))
    owner: Mapped[str] = mapped_column(String(39))
    name: Mapped[str] = mapped_column(String(100))
    full_name: Mapped[str] = mapped_column(String(140), unique=True)
    default_branch: Mapped[str] = mapped_column(String(255))
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    has_issues: Mapped[bool] = mapped_column(Boolean, default=True)
    added_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    scans: Mapped[list[Scan]] = relationship(
        back_populates="repo", cascade="all, delete-orphan", passive_deletes=True
    )
    issue_drafts: Mapped[list[IssueDraft]] = relationship(
        back_populates="repo", cascade="all, delete-orphan", passive_deletes=True
    )
