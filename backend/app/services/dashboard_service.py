"""Dashboard aggregates (PRD 11.3 "DashboardSummary rules").

Base data is each repo's latest completed scan. The trend is computed in Python.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.repo import Repo
from app.models.scan import RuleStatus, Scan, ScanResult, ScanStatus
from app.rules.registry import get_rules
from app.schemas.dashboard import DashboardSummary, RuleFailure, TrendPoint

GRADES = ("A", "B", "C", "D", "F")
TREND_DAYS = 30


def _latest_completed(db: Session) -> list[Scan]:
    """Each repo's newest completed scan."""
    rank = func.row_number().over(
        partition_by=Scan.repo_id, order_by=(Scan.created_at.desc(), Scan.id.desc())
    )
    ranked = (
        select(Scan.id.label("scan_id"), rank.label("rank"))
        .where(Scan.status == ScanStatus.completed)
        .subquery()
    )
    rows = db.scalars(
        select(Scan).join(ranked, Scan.id == ranked.c.scan_id).where(ranked.c.rank == 1)
    ).all()
    return list(rows)


def _average(scores: list[int]) -> float | None:
    return round(sum(scores) / len(scores), 1) if scores else None


def _rule_failures(db: Session, scan_ids: list[int]) -> list[RuleFailure]:
    counts: dict[tuple[str, RuleStatus], int] = defaultdict(int)
    if scan_ids:
        rows = db.execute(
            select(ScanResult.rule_id, ScanResult.status, func.count())
            .where(
                ScanResult.scan_id.in_(scan_ids),
                ScanResult.status.in_((RuleStatus.fail, RuleStatus.warn)),
            )
            .group_by(ScanResult.rule_id, ScanResult.status)
        )
        for rule_id, status, n in rows:
            counts[(rule_id, RuleStatus(status))] = n
    # Every registered rule appears, including 0/0 rows.
    items = [
        (
            rule.display_order,
            RuleFailure(
                rule_id=rule.id,
                rule_name=rule.name,
                fail=counts[(rule.id, RuleStatus.fail)],
                warn=counts[(rule.id, RuleStatus.warn)],
            ),
        )
        for rule in get_rules()
    ]
    items.sort(key=lambda it: (-it[1].fail, -it[1].warn, it[0]))
    return [item for _, item in items]


def score_trend(db: Session, today: date) -> list[TrendPoint]:
    """Average score per UTC day for D-29 .. D.

    Starts from each repo's latest completed scan BEFORE the window (the baseline), then walks
    forward day by day carrying each repo's most recent score. A scan with a null score replaces
    the carried score with "no score", so the last point always equals `average_score`.
    Days with no scored repo are omitted.
    """
    first_day = today - timedelta(days=TREND_DAYS - 1)
    window_start = datetime.combine(first_day, time.min, tzinfo=UTC)
    window_end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)

    rank = func.row_number().over(
        partition_by=Scan.repo_id, order_by=(Scan.finished_at.desc(), Scan.id.desc())
    )
    before = (
        select(Scan.id.label("scan_id"), rank.label("rank"))
        .where(Scan.status == ScanStatus.completed, Scan.finished_at < window_start)
        .subquery()
    )
    current: dict[int, int | None] = {
        scan.repo_id: scan.score
        for scan in db.scalars(
            select(Scan).join(before, Scan.id == before.c.scan_id).where(before.c.rank == 1)
        )
    }

    by_day: dict[date, list[Scan]] = defaultdict(list)
    for scan in db.scalars(
        select(Scan)
        .where(
            Scan.status == ScanStatus.completed,
            Scan.finished_at >= window_start,
            Scan.finished_at < window_end,
        )
        .order_by(Scan.finished_at, Scan.id)
    ):
        assert scan.finished_at is not None
        by_day[scan.finished_at.astimezone(UTC).date()].append(scan)

    points: list[TrendPoint] = []
    for offset in range(TREND_DAYS):
        day = first_day + timedelta(days=offset)
        for scan in by_day.get(day, []):
            current[scan.repo_id] = scan.score
        average = _average([s for s in current.values() if s is not None])
        if average is not None:
            points.append(TrendPoint(date=day.isoformat(), average_score=average))
    return points


def build_summary(db: Session, today: date | None = None) -> DashboardSummary:
    today = today or datetime.now(UTC).date()
    repo_count = db.scalar(select(func.count()).select_from(Repo)) or 0
    latest = _latest_completed(db)
    scored = [s for s in latest if s.score is not None]
    distribution = dict.fromkeys(GRADES, 0)
    for scan in scored:
        if scan.grade in distribution:
            distribution[scan.grade] += 1
    return DashboardSummary(
        repo_count=repo_count,
        scanned_repo_count=len(latest),
        average_score=_average([s.score for s in scored if s.score is not None]),
        grade_distribution=distribution,
        rule_failures=_rule_failures(db, [s.id for s in latest]),
        score_trend=score_trend(db, today),
    )
