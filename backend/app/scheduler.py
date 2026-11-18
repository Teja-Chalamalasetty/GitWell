"""In-process re-scan scheduler (PRD 5 US-9, 14).

One APScheduler interval job. Each tick queues `scheduled` scans for repos that are due, then hands
them to the same executor as manual scans. Valid only because there is exactly one process (PRD 6).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.errors import AppError
from app.db import utcnow
from app.github.ratelimit_state import RateLimitState, rate_limit_state
from app.models.repo import Repo
from app.models.scan import Scan, ScanStatus, ScanTrigger
from app.services import scan_service

logger = logging.getLogger("gitwell.scheduler")

JOB_ID = "gitwell-rescan"
RATE_LIMIT_FLOOR = 50  # skip the whole tick below this many remaining requests
UNREACHABLE_AFTER = 3  # consecutive REPO_NOT_FOUND failures
FIRST_TICK_DELAY_SECONDS = 30  # so a fresh start catches up quickly instead of waiting a full tick
ACTIVE = (ScanStatus.queued, ScanStatus.running)


class SchedulerService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        dispatch: Callable[[int], None],
        state: RateLimitState = rate_limit_state,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._dispatch = dispatch
        self._state = state
        self._clock = clock
        self._scheduler: BackgroundScheduler | None = None

    # ---- selection --------------------------------------------------------------------------

    def due_repo_ids(self, db: Session, now: datetime) -> list[int]:
        """Repos to scan now, in the order the PRD specifies, capped per tick.

        Due = never scanned, or the latest scan (any status) is older than RESCAN_INTERVAL_HOURS.
        Excluded: repos with an active scan, and "unreachable" repos (their last 3 scans all
        failed with REPO_NOT_FOUND; a manual scan that succeeds makes them eligible again).
        Order: never-scanned first, then oldest latest scan first, ties by repo id.
        """
        cutoff = now - timedelta(hours=self._settings.rescan_interval_hours)
        recent = self._recent_scans(db)
        active = set(
            db.scalars(select(Scan.repo_id).where(Scan.status.in_(ACTIVE)).distinct()).all()
        )

        due: list[tuple[int, datetime, int]] = []  # (sort group, latest created_at, repo id)
        for repo_id in db.scalars(select(Repo.id)).all():
            if repo_id in active:
                continue
            scans = recent.get(repo_id, [])
            if not scans:
                due.append((0, datetime.min.replace(tzinfo=cutoff.tzinfo), repo_id))
                continue
            if self._unreachable(scans):
                continue
            latest = scans[0]
            if latest.created_at < cutoff:
                due.append((1, latest.created_at, repo_id))
        due.sort()
        return [repo_id for _, _, repo_id in due[: self._settings.scheduler_max_per_tick]]

    @staticmethod
    def _unreachable(newest_first: Sequence[Scan]) -> bool:
        last = newest_first[:UNREACHABLE_AFTER]
        return len(last) == UNREACHABLE_AFTER and all(
            s.status == ScanStatus.failed and s.error_code == "REPO_NOT_FOUND" for s in last
        )

    @staticmethod
    def _recent_scans(db: Session) -> dict[int, list[Scan]]:
        """Each repo's newest few scans (any status), newest first."""
        rank = func.row_number().over(
            partition_by=Scan.repo_id, order_by=(Scan.created_at.desc(), Scan.id.desc())
        )
        ranked = select(Scan.id.label("scan_id"), rank.label("rank")).subquery()
        rows = db.scalars(
            select(Scan)
            .join(ranked, Scan.id == ranked.c.scan_id)
            .where(ranked.c.rank <= UNREACHABLE_AFTER)
            .order_by(Scan.repo_id, ranked.c.rank)
        ).all()
        grouped: dict[int, list[Scan]] = {}
        for scan in rows:
            grouped.setdefault(scan.repo_id, []).append(scan)
        return grouped

    # ---- one tick ------------------------------------------------------------------------------

    def tick(self) -> list[int]:
        """Queue and dispatch due scans. Returns the ids of the scans it queued. Never raises."""
        try:
            return self._tick()
        except Exception as exc:
            logger.error("scheduler_tick_failed", extra={"exc_type": type(exc).__name__})
            return []

    def _tick(self) -> list[int]:
        now = self._clock()
        if self._state.is_low(RATE_LIMIT_FLOOR, now):
            logger.warning("scheduler_tick_skipped", extra={"reason": "github_rate_limit_low"})
            return []

        queued: list[int] = []
        with self._sessions() as db:
            for repo_id in self.due_repo_ids(db, now):
                try:
                    scan = scan_service.queue_scan(db, repo_id, ScanTrigger.scheduled, None)
                except AppError as exc:
                    # The unique index says a scan is active (or the repo vanished): skip it.
                    if exc.code in ("SCAN_IN_PROGRESS", "NOT_FOUND"):
                        continue
                    raise
                queued.append(scan.id)
        for scan_id in queued:  # after the commits, so workers always find their rows
            self._dispatch(scan_id)
        if queued:
            logger.info("scheduler_tick", extra={"queued": len(queued)})
        return queued

    # ---- lifecycle -----------------------------------------------------------------------------

    def start(self) -> None:
        if self._scheduler is not None:
            return
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            self.tick,
            "interval",
            minutes=self._settings.scheduler_tick_minutes,
            id=JOB_ID,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            next_run_time=self._clock() + timedelta(seconds=FIRST_TICK_DELAY_SECONDS),
        )
        scheduler.start()
        self._scheduler = scheduler

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    @property
    def running(self) -> bool:
        return self._scheduler is not None and bool(self._scheduler.running)

    @property
    def job(self) -> object | None:
        return self._scheduler.get_job(JOB_ID) if self._scheduler is not None else None
