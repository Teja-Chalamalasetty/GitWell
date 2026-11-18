"""Graceful shutdown (PRD 14) and the single-process guard (PRD 6)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import create_app
from app.models.scan import Scan, ScanStatus
from app.models.user import UserRole
from tests.conftest import make_settings
from tests.fake_github import FakeGitHub
from tests.helpers import InlineExecutor, ManualExecutor, login_as


def test_shutdown_runs_in_the_documented_order(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    executor = ManualExecutor()
    app = create_app(make_settings(scheduler_enabled=True), executor=executor)

    real_stop = app.state.scheduler.stop
    app.state.scheduler.stop = lambda: (order.append("scheduler.stop"), real_stop())
    real_shutdown = executor.shutdown
    executor.shutdown = lambda wait=True, *, cancel_futures=False: (  # type: ignore[method-assign]
        order.append(f"executor.shutdown(wait={wait}, cancel_futures={cancel_futures})"),
        real_shutdown(wait, cancel_futures=cancel_futures),
    )[1]
    real_expire = app.state.scan_runner.expire_all
    app.state.scan_runner.expire_all = lambda: (order.append("runner.expire_all"), real_expire())

    import app.main as main_module

    real_fail = main_module.fail_queued_scans
    monkeypatch.setattr(
        main_module,
        "fail_queued_scans",
        lambda db: (order.append("fail_queued_scans"), real_fail(db))[1],
    )
    with TestClient(app):
        assert app.state.scheduler.running
    assert order == [
        "scheduler.stop",
        "executor.shutdown(wait=False, cancel_futures=True)",
        "fail_queued_scans",
        "runner.expire_all",
    ]
    assert not app.state.scheduler.running


def test_scans_cancelled_at_shutdown_become_interrupted(
    start_app: Callable[..., TestClient], gh: FakeGitHub
) -> None:
    executor = ManualExecutor()
    client = start_app(executor=executor, github_token="tkn")
    headers = login_as(client, "admin@example.com", UserRole.admin)
    client.post("/api/v1/repos", headers=headers, json={"url": "https://github.com/o/r"})
    assert executor.pending  # the on_add scan is queued, not started
    client.__exit__(None, None, None)
    with SessionLocal() as s:
        scan = s.scalars(select(Scan)).one()
    assert (scan.status, scan.error_code) == (ScanStatus.failed, "INTERRUPTED")
    assert executor.pending == []


def test_a_running_scan_is_told_to_abort_at_shutdown(
    start_app: Callable[..., TestClient], gh: FakeGitHub
) -> None:
    client = start_app(github_token="tkn")
    headers = login_as(client, "admin@example.com", UserRole.admin)
    client.post("/api/v1/repos", headers=headers, json={"url": "https://github.com/o/r"})
    runner = client.app.state.scan_runner  # type: ignore[attr-defined]
    gh.repo.on_commit_lookup = runner.expire_all  # what shutdown does, mid-scan
    client.post("/api/v1/repos/1/scan", headers=headers)
    with SessionLocal() as s:
        newest = s.scalars(select(Scan).order_by(Scan.id.desc())).first()
    assert newest is not None
    assert (newest.status, newest.error_code) == (ScanStatus.failed, "SCAN_TIMEOUT")


# ---- the single-process guard ---------------------------------------------------------


def logs(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]


@pytest.mark.parametrize("value", ["2", "4", "8"])
def test_running_more_than_one_worker_logs_a_warning(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", value)
    with TestClient(create_app(make_settings(), executor=InlineExecutor())):
        pass
    warnings = [r for r in logs(capsys) if "multiple_workers_unsupported" in r["message"]]
    assert len(warnings) == 1 and warnings[0]["level"] == "WARNING"
    assert warnings[0]["web_concurrency"] == value


@pytest.mark.parametrize("value", [None, "", "1", " 1 "])
def test_one_worker_is_silent(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("WEB_CONCURRENCY", value)
    with TestClient(create_app(make_settings(), executor=InlineExecutor())):
        pass
    assert not [r for r in logs(capsys) if "multiple_workers" in r["message"]]
