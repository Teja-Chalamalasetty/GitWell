from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.models.scan import ScanStatus
from app.schemas.common import OptUTCDatetime, UTCDatetime


class RepoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str


class LatestScan(BaseModel):
    id: int
    status: ScanStatus
    error_code: str | None
    created_at: UTCDatetime
    finished_at: OptUTCDatetime


class LatestCompletedScan(BaseModel):
    id: int
    score: int | None
    grade: str | None
    commit_sha: str | None
    finished_at: OptUTCDatetime
    rules_errored: int
    is_partial: bool
    has_findings: bool


class RepoSummary(BaseModel):
    id: int
    github_id: int
    url: str
    full_name: str
    default_branch: str
    is_private: bool
    is_archived: bool
    has_issues: bool
    added_at: UTCDatetime
    latest_scan: LatestScan | None
    latest_completed_scan: LatestCompletedScan | None
    score_delta: int | None
    active_scan_id: int | None
    open_draft_id: int | None
