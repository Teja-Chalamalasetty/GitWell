from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.models.scan import RuleStatus, ScanStatus, ScanTrigger
from app.schemas.common import OptUTCDatetime, ORMModel, UTCDatetime


class ScanOut(ORMModel):
    id: int
    repo_id: int
    status: ScanStatus
    trigger: ScanTrigger
    score: int | None
    grade: str | None
    rules_errored: int
    ruleset_version: str | None
    commit_sha: str | None
    snapshot_mode: str | None
    is_partial: bool
    error_code: str | None
    error_message: str | None
    created_at: UTCDatetime
    started_at: OptUTCDatetime
    finished_at: OptUTCDatetime


class ScanResultOut(BaseModel):
    rule_id: str
    rule_name: str
    status: RuleStatus
    weight: int
    display_order: int
    message: str
    details: dict[str, Any]
    fix_tip: str


class ScanDetail(ScanOut):
    results: list[ScanResultOut]
