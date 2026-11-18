from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_engine
from app.models import (
    DraftStatus,
    IssueDraft,
    Repo,
    RuleStatus,
    Scan,
    ScanResult,
    ScanStatus,
    ScanTrigger,
    User,
)
from tests.dburl import IS_SQLITE


def make_repo(github_id: int = 1, name: str = "r") -> Repo:
    return Repo(
        github_id=github_id,
        url=f"https://github.com/o/{name}",
        owner="o",
        name=name,
        full_name=f"o/{name}",
        default_branch="main",
    )


def make_scan(repo_id: int, status: ScanStatus = ScanStatus.completed) -> Scan:
    return Scan(repo_id=repo_id, status=status, trigger=ScanTrigger.manual)


def make_result(scan_id: int, rule_id: str = "readme") -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        rule_id=rule_id,
        status=RuleStatus.pass_,
        message="ok",
        details={},
        weight=15,
        display_order=20,
    )


@pytest.mark.skipif(not IS_SQLITE, reason="SQLite pragmas")
def test_sqlite_pragmas_enabled(client: TestClient) -> None:
    with get_engine().connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_sqlite_foreign_keys_cascade_on_delete(db: Session) -> None:
    """Deleting a repo cascades through scans, results and drafts at the DB level."""
    user = User(email="a@b.com", password_hash="x")
    repo = make_repo()
    db.add_all([user, repo])
    db.commit()
    scan = make_scan(repo.id)
    db.add(scan)
    db.commit()
    db.add(make_result(scan.id))
    db.add(
        IssueDraft(
            scan_id=scan.id,
            repo_id=repo.id,
            title="t",
            body="b",
            status=DraftStatus.drafted,
            created_by_id=user.id,
        )
    )
    db.commit()

    # Raw SQL bypasses ORM cascade, so only the database's foreign keys can do the work.
    db.execute(text("DELETE FROM repos WHERE id = :id"), {"id": repo.id})
    db.commit()

    for table in ("scans", "scan_results", "issue_drafts"):
        assert db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0
    assert db.execute(text("SELECT COUNT(*) FROM users")).scalar() == 1


def test_deleting_user_sets_null(db: Session) -> None:
    user = User(email="a@b.com", password_hash="x")
    db.add(user)
    db.commit()
    repo = make_repo()
    repo.added_by_id = user.id
    db.add(repo)
    db.commit()
    db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    db.commit()
    db.refresh(repo)
    assert repo.added_by_id is None


def test_foreign_key_violation_rejected(db: Session) -> None:
    db.add(make_scan(repo_id=999))
    with pytest.raises(IntegrityError):
        db.commit()


def test_one_active_scan_per_repo_enforced_by_index(db: Session) -> None:
    repo = make_repo()
    db.add(repo)
    db.commit()
    db.add(make_scan(repo.id, ScanStatus.queued))
    db.commit()
    db.add(make_scan(repo.id, ScanStatus.running))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # finished scans are unconstrained
    db.add_all([make_scan(repo.id, ScanStatus.completed), make_scan(repo.id, ScanStatus.failed)])
    db.commit()


def test_github_id_unique(db: Session) -> None:
    db.add(make_repo(1, "a"))
    db.commit()
    db.add(make_repo(1, "b"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_enums_stored_as_values(db: Session) -> None:
    repo = make_repo()
    db.add(repo)
    db.commit()
    scan = make_scan(repo.id)
    db.add(scan)
    db.commit()
    db.add(make_result(scan.id))
    db.commit()
    assert db.execute(text("SELECT status FROM scan_results")).scalar() == "pass"
    assert db.execute(text("SELECT status FROM scans")).scalar() == "completed"
    assert db.scalars(select(ScanResult)).one().status is RuleStatus.pass_


def test_scan_result_unique_per_rule(db: Session) -> None:
    repo = make_repo()
    db.add(repo)
    db.commit()
    scan = make_scan(repo.id)
    db.add(scan)
    db.commit()
    db.add_all([make_result(scan.id), make_result(scan.id)])
    with pytest.raises(IntegrityError):
        db.commit()


def test_utc_datetime_roundtrip_and_rejects_naive(db: Session) -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    repo = make_repo()
    repo.added_at = datetime(2026, 11, 18, 15, 30, tzinfo=ist)
    db.add(repo)
    db.commit()
    db.expire_all()
    got = db.scalars(select(Repo)).one().added_at
    assert got == datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    assert got.utcoffset() == timedelta(0)

    bad = make_repo(2, "naive")
    bad.added_at = datetime(2026, 1, 1)
    db.add(bad)
    with pytest.raises(Exception, match="naive datetime"):
        db.commit()
    db.rollback()
