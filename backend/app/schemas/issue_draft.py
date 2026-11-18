from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.models.issue_draft import DraftStatus
from app.schemas.common import OptUTCDatetime, ORMModel, UTCDatetime

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Body = Annotated[str, StringConstraints(min_length=1, max_length=60_000)]
# The dedupe marker is added by GitWell at post time; a user must not be able to forge one.
_RESERVED_MARKER = re.compile(r"gitwell-draft", re.IGNORECASE)


class IssueDraftOut(ORMModel):
    id: int
    scan_id: int
    repo_id: int
    repo_full_name: str
    repo_is_private: bool
    title: str
    body: str
    status: DraftStatus
    post_attempts: int
    github_issue_number: int | None
    github_issue_url: str | None
    last_error: str | None
    created_by_id: int | None
    approved_by_id: int | None
    created_at: UTCDatetime
    updated_at: UTCDatetime
    approved_at: OptUTCDatetime


class IssueDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    body: Body | None = None

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("body must not be blank")
        return value

    @field_validator("title", "body")
    @classmethod
    def _no_reserved_marker(cls, value: str | None) -> str | None:
        if value is not None and _RESERVED_MARKER.search(value):
            raise ValueError("text may not contain the reserved GitWell marker")
        return value
