"""Scan queueing, listing, detail and startup/shutdown recovery."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.metrics import get_metrics
from app.db import utcnow
from app.models.issue_draft import DraftStatus, IssueDraft
from app.models.repo import Repo
from app.models.scan import RuleStatus, Scan, ScanResult, ScanStatus, ScanTrigger
from app.rules.registry import get_rule
from app.scanner.errors import SCAN_ERRORS
from app.schemas.scan import ScanDetail, ScanOut, ScanResultOut

logger = logging.getLogger("gitwell.scans")

ACTIVE = (ScanStatus.queued, ScanStatus.running)
INTERRUPTED_DRAFT_MESSAGE = "Interrupted while posting; retry to continue."


def queue_scan(db: Session, repo_id: int, trigger: ScanTrigger, user_id: int | None) -> Scan:
    """Insert a `queued` scan and COMMIT it (the caller then submits it to the executor).

    "One active scan per repo" is enforced by the partial unique index, not by a
    check-then-insert, so two concurrent requests cannot both succeed.
    """
    scan = Scan(repo_id=repo_id, status=ScanStatus.queued, trigger=trigger, triggered_by_id=user_id)
    db.add(scan)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.get(Repo, repo_id) is None:
            raise AppError(404, "NOT_FOUND", "Repository not found.") from None
        active = db.scalar(select(Scan.id).where(Scan.repo_id == repo_id, Scan.status.in_(ACTIVE)))
        raise AppError(
            409,
            "SCAN_IN_PROGRESS",
            "A scan is already queued or running for this repository.",
            {"scan_id": active},
        ) from None
    get_metrics().scans.labels("queued", trigger.value).inc()
    logger.info("scan_queued", extra={"scan_id": scan.id, "repo_id": repo_id, "trigger": trigger})
    return scan


def dispatch(app_state: Any, scan_id: int) -> None:
    """Hand a committed `queued` scan to the executor. Workers get only the scan id."""
    try:
        app_state.executor.submit(app_state.scan_runner.run, scan_id)
    except RuntimeError:
        # The executor is shutting down; the scan stays queued and is recovered at next start.
        logger.warning("scan_not_dispatched", extra={"scan_id": scan_id})


def list_scans(
    db: Session, repo_id: int, status: ScanStatus | None, limit: int, offset: int
) -> tuple[list[Scan], int]:
    if db.get(Repo, repo_id) is None:
        raise AppError(404, "NOT_FOUND", "Repository not found.")
    conditions: list[Any] = [Scan.repo_id == repo_id]
    if status is not None:
        conditions.append(Scan.status == status)
    total = db.scalar(select(func.count()).select_from(Scan).where(*conditions)) or 0
    items = db.scalars(
        select(Scan)
        .where(*conditions)
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), total


def get_scan_detail(db: Session, scan_id: int) -> ScanDetail:
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise AppError(404, "NOT_FOUND", "Scan not found.")
    rows = db.scalars(
        select(ScanResult)
        .where(ScanResult.scan_id == scan_id)
        .order_by(ScanResult.display_order, ScanResult.rule_id)
    ).all()
    results = []
    for row in rows:
        rule = get_rule(row.rule_id)  # a removed rule falls back to its id and no tip
        results.append(
            ScanResultOut(
                rule_id=row.rule_id,
                rule_name=rule.name if rule else row.rule_id,
                status=RuleStatus(row.status),
                weight=row.weight,
                display_order=row.display_order,
                message=row.message,
                details=row.details or {},
                fix_tip=rule.fix_tip if rule else "",
            )
        )
    base = ScanOut.model_validate(scan)
    return ScanDetail(**base.model_dump(), results=results)


def _fail_active(db: Session, statuses: tuple[ScanStatus, ...]) -> int:
    result = db.execute(
        update(Scan)
        .where(Scan.status.in_(statuses))
        .values(
            status=ScanStatus.failed,
            error_code="INTERRUPTED",
            error_message=SCAN_ERRORS["INTERRUPTED"],
            finished_at=utcnow(),
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


def recover_interrupted(db: Session) -> tuple[int, int]:
    """Startup recovery (PRD 8.1). Returns (scans failed, drafts reset)."""
    scans = _fail_active(db, ACTIVE)
    drafts = db.execute(
        update(IssueDraft)
        .where(IssueDraft.status == DraftStatus.posting)
        .values(
            status=DraftStatus.approved,
            last_error=INTERRUPTED_DRAFT_MESSAGE,
            updated_at=utcnow(),
        )
    )
    db.commit()
    reset = int(getattr(drafts, "rowcount", 0) or 0)
    if scans or reset:
        logger.warning("startup_recovery", extra={"scans_failed": scans, "drafts_reset": reset})
    return scans, reset


def fail_queued_scans(db: Session) -> int:
    """Graceful shutdown (PRD 14): scans cancelled before they started become INTERRUPTED."""
    count = _fail_active(db, (ScanStatus.queued,))
    db.commit()
    return count
