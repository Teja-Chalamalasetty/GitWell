"""Worker transaction rules (PRD 6.1, 8.1), recovery and shutdown, and real-thread concurrency."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import Base, SessionLocal, init_engine
from app.models.issue_draft import DraftStatus, IssueDraft
from app.models.repo import Repo
from app.models.scan import Scan, ScanStatus, ScanTrigger
from app.models.user import UserRole
from app.scanner.errors import SCAN_ERRORS
from app.scanner.worker import ScanRunner
from app.services import scan_service
from tests.conftest import make_settings
from tests.dburl import database_url
from tests.fake_github import GOOD_FILES, FakeGitHub
from tests.helpers import ManualExecutor, login_as


def seed_repo(github_id: int = 42, name: str = "r") -> int:
    with SessionLocal() as s:
        repo = Repo(
            github_id=github_id,
            url=f"https://github.com/o/{name}",
            owner="o",
            name=name,
            full_name=f"o/{name}",
            default_branch="main",
        )
        s.add(repo)
        s.commit()
        return repo.id


def seed_scan(repo_id: int, status: ScanStatus = ScanStatus.queued) -> int:
    with SessionLocal() as s:
        scan = Scan(repo_id=repo_id, status=status, trigger=ScanTrigger.manual)
        s.add(scan)
        s.commit()
        return scan.id


def table_counts() -> dict[str, int]:
    with SessionLocal() as s:
        return {
            t: int(s.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() or 0)
            for t in ("repos", "scans", "scan_results", "issue_drafts")
        }


def logs(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def file_url(tmp_path: Path) -> str:
    return database_url(tmp_path, "worker.db")


# ---- deleting a repo mid-scan --------------------------------------------------------------


def test_deleting_a_repo_mid_scan_leaves_no_orphans_and_raises_nothing(
    capsys: pytest.CaptureFixture[str],
    start_app: Callable[..., TestClient],
    gh: FakeGitHub,
    tmp_path: Path,
) -> None:
    # A file database: if the worker held a write transaction open while talking to GitHub,
    # the DELETE below would block on SQLite's lock and the test would fail.
    client = start_app(database_url=file_url(tmp_path))
    repo_id = seed_repo()
    scan_id = seed_scan(repo_id)

    def delete_the_repo() -> None:
        with SessionLocal() as s:
            s.execute(text("DELETE FROM repos WHERE id = :id"), {"id": repo_id})
            s.commit()

    gh.repo.on_commit_lookup = delete_the_repo
    client.app.state.scan_runner.run(scan_id)  # type: ignore[attr-defined]  # must not raise

    assert table_counts() == {"repos": 0, "scans": 0, "scan_results": 0, "issue_drafts": 0}
    all_logs = logs(capsys)
    events = [e for e in all_logs if e.get("message") == "scan_orphaned"]
    assert len(events) == 1
    assert events[0]["scan_id"] == scan_id and events[0]["level"] == "INFO"
    # the designed path (conditional UPDATE matched 0 rows), not a crash caught by the safety net
    names = {e.get("message") for e in all_logs}
    assert "scan_worker_crashed" not in names and "scan_completed" not in names


def test_a_failing_scan_whose_repo_is_deleted_mid_scan_is_orphaned_too(
    client: TestClient, gh: FakeGitHub
) -> None:
    repo_id = seed_repo()
    scan_id = seed_scan(repo_id)

    def delete_and_fail() -> None:
        with SessionLocal() as s:
            s.execute(text("DELETE FROM repos WHERE id = :id"), {"id": repo_id})
            s.commit()

    gh.repo.on_commit_lookup = delete_and_fail
    gh.repo.empty = True  # the scan would fail with REPO_EMPTY after the deletion
    client.app.state.scan_runner.run(scan_id)  # type: ignore[attr-defined]
    assert table_counts()["scans"] == 0


def test_a_scan_that_is_not_queued_is_ignored(client: TestClient, gh: FakeGitHub) -> None:
    repo_id = seed_repo()
    runner: ScanRunner = client.app.state.scan_runner  # type: ignore[attr-defined]
    for status in (ScanStatus.running, ScanStatus.completed, ScanStatus.failed):
        with SessionLocal() as s:
            scan = Scan(repo_id=repo_id, status=status, trigger=ScanTrigger.manual)
            s.add(scan)
            s.commit()
            before = (scan.id, scan.status)
        runner.run(before[0])
        with SessionLocal() as s:
            assert s.get(Scan, before[0]).status == before[1]  # type: ignore[union-attr]
        with SessionLocal() as s:  # reset so the next iteration has no active-scan conflict
            s.execute(text("DELETE FROM scans"))
            s.commit()
    runner.run(999_999)  # unknown id: nothing happens
    assert gh.calls == []


def test_the_worker_never_raises_even_if_everything_goes_wrong(
    capsys: pytest.CaptureFixture[str], client: TestClient, gh: FakeGitHub
) -> None:
    repo_id = seed_repo()
    scan_id = seed_scan(repo_id)

    def broken_factory() -> None:
        raise RuntimeError("".join(["factory exploded with ", "secret-ish", " text"]))

    runner = ScanRunner(make_settings(), SessionLocal, broken_factory)  # type: ignore[arg-type]
    runner.run(scan_id)
    with SessionLocal() as s:
        scan = s.get(Scan, scan_id)
        assert scan is not None and scan.status == ScanStatus.failed
        assert scan.error_code == "INTERNAL_ERROR"
        assert scan.error_message == SCAN_ERRORS["INTERNAL_ERROR"]
    text_logs = json.dumps(logs(capsys))
    assert "secret-ish text" not in text_logs  # the message is never logged
    assert "scan_internal_error" in text_logs and "RuntimeError" in text_logs


def test_each_scan_gets_exactly_one_result_row_per_rule(client: TestClient, gh: FakeGitHub) -> None:
    gh.repo.files = GOOD_FILES
    repo_id = seed_repo()
    scan_id = seed_scan(repo_id)
    client.app.state.scan_runner.run(scan_id)  # type: ignore[attr-defined]
    with SessionLocal() as s:
        rows = s.execute(
            text("SELECT rule_id, weight, display_order FROM scan_results WHERE scan_id=:i"),
            {"i": scan_id},
        ).all()
    assert len(rows) == 8 and len({r[0] for r in rows}) == 8
    assert sum(r[1] for r in rows) == 100


# ---- shutdown and recovery -----------------------------------------------------------------


def test_expire_all_makes_running_scans_abort_with_a_timeout(
    client: TestClient, gh: FakeGitHub
) -> None:
    runner: ScanRunner = client.app.state.scan_runner  # type: ignore[attr-defined]
    scan_id = seed_scan(seed_repo())
    gh.repo.on_commit_lookup = runner.expire_all  # shutdown happens mid-scan
    runner.run(scan_id)
    with SessionLocal() as s:
        scan = s.get(Scan, scan_id)
        assert scan is not None
        assert (scan.status, scan.error_code) == (ScanStatus.failed, "SCAN_TIMEOUT")
    assert runner._deadlines == {}  # deregistered afterwards


def test_graceful_shutdown_fails_scans_that_never_started(
    start_app: Callable[..., TestClient], gh: FakeGitHub
) -> None:
    executor = ManualExecutor()
    client = start_app(executor=executor)
    headers = login_as(client, "admin@example.com", UserRole.admin)
    repo = client.post("/api/v1/repos", headers=headers, json={"url": "https://github.com/o/r"})
    assert repo.json()["latest_scan"]["status"] == "queued" and executor.pending
    client.__exit__(None, None, None)  # shut the app down with a scan still queued

    with SessionLocal() as s:
        scan = s.scalars(select(Scan)).one()
    assert scan.status == ScanStatus.failed and scan.error_code == "INTERRUPTED"
    assert scan.error_message == "Scan was interrupted by a server restart."
    assert executor.pending == []  # the executor's queue was cancelled


def test_startup_recovery_fails_interrupted_scans_and_resets_posting_drafts(
    start_app: Callable[..., TestClient], tmp_path: Path
) -> None:
    url = file_url(tmp_path)
    engine = init_engine(url)
    Base.metadata.create_all(engine)
    repo_id = seed_repo()
    running = seed_scan(repo_id, ScanStatus.running)
    other_repo = seed_repo(github_id=43, name="r2")
    queued = seed_scan(other_repo, ScanStatus.queued)
    done_repo = seed_repo(github_id=44, name="r3")
    completed = seed_scan(done_repo, ScanStatus.completed)
    with SessionLocal() as s:
        # One open draft per repo is enforced by the database, so use two different repos.
        for repo_for_draft, status in (
            (done_repo, DraftStatus.posting),
            (repo_id, DraftStatus.drafted),
        ):
            s.add(
                IssueDraft(
                    scan_id=completed,
                    repo_id=repo_for_draft,
                    title="t",
                    body="b",
                    status=status,
                    post_attempts=1,
                )
            )
        s.commit()
    del engine

    start_app(database_url=url)  # startup runs recovery

    with SessionLocal() as s:
        assert {s.get(Scan, i).status for i in (running, queued)} == {ScanStatus.failed}  # type: ignore[union-attr]
        for i in (running, queued):
            row = s.get(Scan, i)
            assert row is not None
            assert (row.error_code, row.error_message) == (
                "INTERRUPTED",
                "Scan was interrupted by a server restart.",
            )
            assert row.finished_at is not None
        assert s.get(Scan, completed).status == ScanStatus.completed  # type: ignore[union-attr]
        drafts = s.scalars(select(IssueDraft).order_by(IssueDraft.id)).all()
        assert [d.status for d in drafts] == [DraftStatus.approved, DraftStatus.drafted]
        assert drafts[0].last_error == "Interrupted while posting; retry to continue."
        assert drafts[1].last_error is None


def test_recovery_unblocks_repos_so_they_can_be_scanned_again(
    start_app: Callable[..., TestClient], gh: FakeGitHub, tmp_path: Path
) -> None:
    url = file_url(tmp_path)
    Base.metadata.create_all(init_engine(url))
    repo_id = seed_repo()
    seed_scan(repo_id, ScanStatus.running)  # a crash left this active forever
    client = start_app(database_url=url)
    headers = login_as(client, "admin@example.com", UserRole.admin)
    r = client.post(f"/api/v1/repos/{repo_id}/scan", headers=headers)
    assert r.status_code == 202


def test_recover_interrupted_is_a_noop_on_a_clean_database(client: TestClient) -> None:
    with SessionLocal() as s:
        assert scan_service.recover_interrupted(s) == (0, 0)
        assert scan_service.fail_queued_scans(s) == 0


# ---- real threads --------------------------------------------------------------------------


def test_two_scans_run_concurrently_on_a_real_thread_pool_with_a_file_database(
    start_app: Callable[..., TestClient], gh: FakeGitHub, tmp_path: Path
) -> None:
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scan")
    client = start_app(executor=executor, database_url=file_url(tmp_path), scan_workers=2)
    runner: ScanRunner = client.app.state.scan_runner  # type: ignore[attr-defined]

    second = gh.add_repo("o", "r2", 43, files=GOOD_FILES)
    gh.repo.files = GOOD_FILES
    ids = [seed_repo(42, "r"), seed_repo(43, "r2")]
    scan_ids = [seed_scan(i) for i in ids]

    # Both scans must be inside GitHub calls at the same time, or the barrier breaks and the
    # scans fail: that proves the pool really runs them in parallel.
    barrier = threading.Barrier(2, timeout=20)
    gh.repo.on_commit_lookup = barrier.wait
    second.on_commit_lookup = barrier.wait

    futures = [executor.submit(runner.run, sid) for sid in scan_ids]
    for future in futures:
        future.result(timeout=60)  # raises if the worker raised

    with SessionLocal() as s:
        scans = [s.get(Scan, sid) for sid in scan_ids]
        assert all(sc is not None and sc.status == ScanStatus.completed for sc in scans), [
            (sc.status, sc.error_code) for sc in scans if sc
        ]
        assert [sc.score for sc in scans if sc] == [100, 100]
        assert table_counts()["scan_results"] == 16
    executor.shutdown(wait=True)


def test_many_scans_through_a_small_pool_all_finish_cleanly(
    start_app: Callable[..., TestClient], gh: FakeGitHub, tmp_path: Path
) -> None:
    executor = ThreadPoolExecutor(max_workers=2)
    client = start_app(executor=executor, database_url=file_url(tmp_path))
    runner: ScanRunner = client.app.state.scan_runner  # type: ignore[attr-defined]
    scan_ids = []
    for n in range(8):
        gh.add_repo("o", f"repo{n}", 100 + n, files=GOOD_FILES)
        scan_ids.append(seed_scan(seed_repo(100 + n, f"repo{n}")))
    for future in [executor.submit(runner.run, sid) for sid in scan_ids]:
        future.result(timeout=120)
    with SessionLocal() as s:
        statuses = [s.get(Scan, sid).status for sid in scan_ids]  # type: ignore[union-attr]
    assert statuses == [ScanStatus.completed] * 8
    executor.shutdown(wait=True)


# ---- safety nets and edge cases ------------------------------------------------------------


def test_a_rename_that_would_collide_is_skipped_but_other_fields_still_refresh(
    capsys: pytest.CaptureFixture[str], client: TestClient, gh: FakeGitHub
) -> None:
    repo_id = seed_repo(github_id=42, name="r")
    seed_repo(github_id=43, name="shared")  # another tracked repo already holds o/shared
    scan_id = seed_scan(repo_id)
    gh.repo.rename("shared")
    gh.repo.default_branch = "trunk"
    client.app.state.scan_runner.run(scan_id)  # type: ignore[attr-defined]

    with SessionLocal() as s:
        repo = s.get(Repo, repo_id)
        assert repo is not None
        assert repo.full_name == "o/r"  # the colliding rename was skipped
        assert repo.default_branch == "trunk"  # but the rest was refreshed
        scan = s.get(Scan, scan_id)
        assert scan is not None and scan.status == ScanStatus.completed
    assert any(e.get("message") == "repo_rename_skipped" for e in logs(capsys))


def test_a_crash_outside_the_normal_error_path_is_caught_and_logged(
    capsys: pytest.CaptureFixture[str],
    client: TestClient,
    gh: FakeGitHub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner: ScanRunner = client.app.state.scan_runner  # type: ignore[attr-defined]
    scan_id = seed_scan(seed_repo())

    def broken(*a: object, **k: object) -> None:
        raise RuntimeError("db went away")

    monkeypatch.setattr(runner, "_claim", broken)
    runner.run(scan_id)  # must not raise
    names = {e.get("message") for e in logs(capsys)}
    assert "scan_worker_crashed" in names

    monkeypatch.setattr(runner, "_write_failed", broken)  # even the recovery write fails
    runner.run(scan_id)  # still must not raise
    assert "scan_failure_not_recorded" in {e.get("message") for e in logs(capsys)}


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "42"},  # id not an int
        {"id": True},  # bool is not an id
        {"name": ""},
        {"name": "x" * 101},
        {"owner": {}},
        {"owner": "not-a-dict"},
        {"owner": {"login": "x" * 40}},
        {"default_branch": ""},
        {"default_branch": None},
    ],
)
def test_repo_payload_validation_rejects_malformed_github_data(patch: dict[str, object]) -> None:
    from app.github.errors import GitHubError
    from app.github.repo_meta import parse_repo_payload

    good = {"id": 1, "name": "r", "owner": {"login": "o"}, "default_branch": "main"}
    assert parse_repo_payload(good).full_name == "o/r"
    with pytest.raises(GitHubError):
        parse_repo_payload({**good, **patch})


def test_repo_payload_defaults_and_odd_size_values() -> None:
    from app.github.repo_meta import parse_repo_payload

    base = {"id": 1, "name": "R", "owner": {"login": "O"}, "default_branch": "main"}
    meta = parse_repo_payload(base)
    assert (meta.has_issues, meta.is_private, meta.is_archived, meta.disabled) == (
        True,
        False,
        False,
        False,
    )
    assert meta.size_kb == 0 and meta.url == "https://github.com/O/R" and meta.full_name == "o/r"
    for bad in (-5, "big", True, None):
        assert parse_repo_payload({**base, "size": bad}).size_kb == 0
    assert parse_repo_payload({**base, "size": 123}).size_kb == 123
