"""Scheduler (PRD 5 US-9, 14): who gets queued, in what order, and what is skipped."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.github.ratelimit_state import RateLimitState
from app.models.repo import Repo
from app.models.scan import Scan, ScanStatus, ScanTrigger
from app.scheduler import JOB_ID, SchedulerService
from tests.conftest import make_settings
from tests.fake_github import GOOD_FILES, FakeGitHub

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


def add_repo(db: Session, n: int) -> Repo:
    repo = Repo(
        github_id=n,
        url=f"https://github.com/o/r{n}",
        owner="o",
        name=f"r{n}",
        full_name=f"o/r{n}",
        default_branch="main",
    )
    db.add(repo)
    db.commit()
    return repo


def add_scan(
    db: Session,
    repo: Repo,
    age: timedelta,
    status: ScanStatus = ScanStatus.completed,
    error: str | None = None,
) -> Scan:
    scan = Scan(
        repo_id=repo.id,
        status=status,
        trigger=ScanTrigger.manual,
        error_code=error,
        created_at=NOW - age,
        finished_at=NOW - age,
    )
    db.add(scan)
    db.commit()
    return scan


class Harness:
    def __init__(self, db: Session, **settings: object) -> None:
        self.db = db
        self.dispatched: list[int] = []
        self.state = RateLimitState()
        self.service = SchedulerService(
            make_settings(**settings),
            SessionLocal,
            self.dispatched.append,
            self.state,
            clock=lambda: NOW,
        )

    def due(self) -> list[int]:
        self.db.expire_all()
        return self.service.due_repo_ids(self.db, NOW)

    def tick(self) -> list[int]:
        self.db.expire_all()
        return self.service.tick()


@pytest.fixture
def h(db: Session) -> Harness:
    return Harness(db)


# ---- who is due ----------------------------------------------------------------------------


def test_only_never_scanned_and_stale_repos_are_due(h: Harness, db: Session) -> None:
    never = add_repo(db, 1)
    stale = add_repo(db, 2)
    fresh = add_repo(db, 3)
    add_scan(db, stale, 25 * HOUR)
    add_scan(db, fresh, 1 * HOUR)
    assert h.due() == [never.id, stale.id]


def test_the_interval_boundary_is_strictly_older_than(h: Harness, db: Session) -> None:
    exactly = add_repo(db, 1)
    just_over = add_repo(db, 2)
    add_scan(db, exactly, 24 * HOUR)
    add_scan(db, just_over, 24 * HOUR + timedelta(seconds=1))
    assert h.due() == [just_over.id]


def test_the_latest_scan_counts_whatever_its_status(h: Harness, db: Session) -> None:
    recently_failed = add_repo(db, 1)
    long_ago_failed = add_repo(db, 2)
    add_scan(db, recently_failed, 2 * HOUR, ScanStatus.failed, "GITHUB_ERROR")
    add_scan(db, long_ago_failed, 30 * HOUR, ScanStatus.failed, "GITHUB_ERROR")
    assert h.due() == [long_ago_failed.id]


def test_only_the_newest_scan_decides(h: Harness, db: Session) -> None:
    repo = add_repo(db, 1)
    add_scan(db, repo, 100 * HOUR)
    add_scan(db, repo, 2 * HOUR)  # a recent scan means it is not due
    assert h.due() == []


def test_the_interval_is_configurable(db: Session) -> None:
    repo = add_repo(db, 1)
    add_scan(db, repo, 3 * HOUR)
    assert Harness(db, rescan_interval_hours=2).due() == [repo.id]
    assert Harness(db, rescan_interval_hours=4).due() == []


# ---- exclusions ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", [ScanStatus.queued, ScanStatus.running])
def test_repos_with_an_active_scan_are_skipped(h: Harness, db: Session, status: ScanStatus) -> None:
    busy = add_repo(db, 1)
    idle = add_repo(db, 2)
    add_scan(db, busy, 30 * HOUR, status)  # even a stale-looking active scan blocks it
    add_scan(db, idle, 30 * HOUR)
    assert h.due() == [idle.id]


def test_unreachable_repos_are_skipped_after_three_repo_not_found_failures(
    h: Harness, db: Session
) -> None:
    unreachable = add_repo(db, 1)
    for hours in (50, 40, 30):
        add_scan(db, unreachable, hours * HOUR, ScanStatus.failed, "REPO_NOT_FOUND")
    two_only = add_repo(db, 2)
    for hours in (40, 30):
        add_scan(db, two_only, hours * HOUR, ScanStatus.failed, "REPO_NOT_FOUND")
    mixed = add_repo(db, 3)
    add_scan(db, mixed, 50 * HOUR, ScanStatus.failed, "REPO_NOT_FOUND")
    add_scan(db, mixed, 40 * HOUR, ScanStatus.failed, "GITHUB_ERROR")
    add_scan(db, mixed, 30 * HOUR, ScanStatus.failed, "REPO_NOT_FOUND")
    assert h.due() == [two_only.id, mixed.id]  # ordered by their latest scan, oldest first


def test_a_repo_that_recovered_is_no_longer_unreachable(h: Harness, db: Session) -> None:
    repo = add_repo(db, 1)
    for hours in (80, 70, 60):
        add_scan(db, repo, hours * HOUR, ScanStatus.failed, "REPO_NOT_FOUND")
    assert h.due() == []  # unreachable
    add_scan(db, repo, 30 * HOUR)  # an admin's manual scan succeeded
    assert h.due() == [repo.id]  # its newest three are no longer all REPO_NOT_FOUND


def test_only_repo_not_found_makes_a_repo_unreachable(h: Harness, db: Session) -> None:
    repo = add_repo(db, 1)
    for hours in (50, 40, 30):
        add_scan(db, repo, hours * HOUR, ScanStatus.failed, "GITHUB_RATE_LIMITED")
    assert h.due() == [repo.id]


# ---- ordering and the per-tick cap ---------------------------------------------------------


def test_never_scanned_first_then_oldest_latest_scan_then_id(db: Session) -> None:
    h = Harness(db, scheduler_max_per_tick=100)
    r1, r2, r3, r4, r5, r6 = (add_repo(db, n) for n in range(1, 7))
    add_scan(db, r1, 30 * HOUR)  # newer
    add_scan(db, r2, 90 * HOUR)  # oldest
    add_scan(db, r3, 30 * HOUR)  # ties with r1: the lower id goes first
    # r4, r5 never scanned
    add_scan(db, r6, 60 * HOUR)
    assert h.due() == [r4.id, r5.id, r2.id, r6.id, r1.id, r3.id]


def test_at_most_max_per_tick_are_queued_and_the_rest_wait(db: Session) -> None:
    repos = [add_repo(db, n) for n in range(1, 16)]
    for n, repo in enumerate(repos[:8]):
        add_scan(db, repo, (100 - n) * HOUR)  # 8 stale repos with distinct ages
    h = Harness(db)  # default cap: 10
    first = h.due()
    assert len(first) == 10
    # 7 never-scanned come first, then the 3 oldest stale ones
    assert first[:7] == [r.id for r in repos[8:]]
    assert first[7:] == [repos[0].id, repos[1].id, repos[2].id]
    assert len(Harness(db, scheduler_max_per_tick=2).due()) == 2


# ---- ticking -------------------------------------------------------------------------------


def test_a_tick_queues_scheduled_scans_and_dispatches_them(h: Harness, db: Session) -> None:
    never, stale, fresh = add_repo(db, 1), add_repo(db, 2), add_repo(db, 3)
    add_scan(db, stale, 25 * HOUR)
    add_scan(db, fresh, 1 * HOUR)
    queued = h.tick()
    assert len(queued) == 2 and h.dispatched == queued

    scans = db.scalars(select(Scan).where(Scan.id.in_(queued)).order_by(Scan.id)).all()
    assert [s.repo_id for s in scans] == [never.id, stale.id]
    assert all(s.status == ScanStatus.queued for s in scans)
    assert all(s.trigger == ScanTrigger.scheduled and s.triggered_by_id is None for s in scans)

    # the next tick finds both repos busy and queues nothing
    assert h.tick() == [] and len(h.dispatched) == 2


def test_a_unique_index_conflict_is_a_silent_skip(
    h: Harness, db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    busy, ok = add_repo(db, 1), add_repo(db, 2)
    add_scan(db, busy, 1 * HOUR, ScanStatus.running)  # an active scan appeared after selection
    # Selection says both are due (as if the scan started a moment later): the index decides.
    monkeypatch.setattr(h.service, "due_repo_ids", lambda session, now: [busy.id, ok.id])
    with caplog.at_level(logging.WARNING):
        queued = h.tick()
    assert len(queued) == 1
    scan = db.get(Scan, queued[0])
    assert scan is not None and scan.repo_id == ok.id
    assert db.scalars(select(Scan).where(Scan.repo_id == busy.id)).all().__len__() == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]  # silent


def test_a_repo_deleted_during_the_tick_is_skipped(
    h: Harness, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = add_repo(db, 2)
    monkeypatch.setattr(h.service, "due_repo_ids", lambda session, now: [9999, ok.id])
    assert len(h.tick()) == 1


def test_rate_limited_ticks_are_skipped_entirely(h: Harness, db: Session) -> None:
    add_repo(db, 1)
    h.state.remaining, h.state.reset_at = 49, NOW + timedelta(minutes=10)
    assert h.tick() == [] and h.dispatched == []
    assert db.scalars(select(Scan)).all() == []

    h.state.remaining = 50  # at the floor is still fine
    assert len(h.tick()) == 1


def test_a_rate_limit_that_already_reset_does_not_block(h: Harness, db: Session) -> None:
    add_repo(db, 1)
    h.state.remaining, h.state.reset_at = 0, NOW - timedelta(seconds=1)
    assert len(h.tick()) == 1


def test_an_unknown_rate_limit_does_not_block(h: Harness, db: Session) -> None:
    add_repo(db, 1)
    assert h.state.snapshot() == (None, None)
    assert len(h.tick()) == 1


def test_a_tick_never_raises(h: Harness, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    add_repo(db, 1)

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(h.service, "due_repo_ids", boom)
    assert h.tick() == []


def test_an_empty_database_is_fine(h: Harness) -> None:
    assert h.tick() == []


# ---- with freezegun: the real clock --------------------------------------------------------


@freeze_time("2026-11-18 12:00:00")
def test_the_default_clock_is_utc_now(db: Session) -> None:
    repo = add_repo(db, 1)
    stale = Scan(
        repo_id=repo.id,
        status=ScanStatus.completed,
        trigger=ScanTrigger.manual,
        created_at=datetime(2026, 11, 17, 11, 0, tzinfo=UTC),  # 25 hours before the frozen now
    )
    db.add(stale)
    db.commit()
    service = SchedulerService(make_settings(), SessionLocal, lambda scan_id: None)
    assert service.tick() != []


# ---- integration ---------------------------------------------------------------------------


def test_a_tick_runs_real_scans_end_to_end(client: TestClient, gh: FakeGitHub, db: Session) -> None:
    """Scheduler -> executor -> worker -> GitHub (faked): the scan really completes."""
    gh.repo.files = GOOD_FILES
    repo = add_repo(db, 42)
    repo.owner, repo.name, repo.full_name = "o", "r", "o/r"
    db.commit()
    scheduler: SchedulerService = client.app.state.scheduler  # type: ignore[attr-defined]
    queued = scheduler.tick()
    assert len(queued) == 1
    db.expire_all()
    scan = db.get(Scan, queued[0])
    assert scan is not None
    assert (scan.status, scan.trigger, scan.score) == (
        ScanStatus.completed,
        ScanTrigger.scheduled,
        100,
    )
    assert gh.posts() == 0  # nothing in the background ever posts to GitHub (S8)


# ---- lifecycle -----------------------------------------------------------------------------


def test_the_job_is_registered_with_the_documented_settings() -> None:
    service = SchedulerService(
        make_settings(scheduler_tick_minutes=7), SessionLocal, lambda s: None
    )
    service.start()
    try:
        assert service.running
        job = service.job
        assert job is not None
        assert job.id == JOB_ID  # type: ignore[attr-defined]
        assert job.max_instances == 1 and job.coalesce is True  # type: ignore[attr-defined]
        assert job.misfire_grace_time == 60  # type: ignore[attr-defined]
        assert job.trigger.interval == timedelta(minutes=7)  # type: ignore[attr-defined]
        service.start()  # idempotent
    finally:
        service.stop()
    assert not service.running and service.job is None
    service.stop()  # idempotent


def test_the_app_starts_and_stops_the_scheduler_with_its_lifespan() -> None:
    from app.main import create_app
    from tests.helpers import InlineExecutor

    app = create_app(make_settings(scheduler_enabled=True), executor=InlineExecutor())
    with TestClient(app):
        assert app.state.scheduler.running
    assert not app.state.scheduler.running

    disabled = create_app(make_settings(scheduler_enabled=False), executor=InlineExecutor())
    with TestClient(disabled):
        assert not disabled.state.scheduler.running
