from __future__ import annotations

from pydantic import BaseModel


class RuleFailure(BaseModel):
    rule_id: str
    rule_name: str
    fail: int
    warn: int


class TrendPoint(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    average_score: float


class DashboardSummary(BaseModel):
    repo_count: int
    scanned_repo_count: int
    average_score: float | None
    grade_distribution: dict[str, int]
    rule_failures: list[RuleFailure]
    score_trend: list[TrendPoint]
