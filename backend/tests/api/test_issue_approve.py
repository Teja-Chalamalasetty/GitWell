"""Approve and post: exactly once (PRD 12.3, S8)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import AppError
from app.db import SessionLocal
from app.github.client import GitHubClient
from app.models.issue_draft import DraftStatus, IssueDraft
from app.models.repo import Repo
from app.models.user import User, UserRole
from app.services import issue_service
from app.services.issue_service import marker
from tests.api.issue_world import World, build_world, db, file_world, world  # noqa: F401
from tests.conftest import make_settings
from tests.dburl import database_url
from tests.fake_github import GOOD_FILES, FakeGitHub
from tests.helpers import login_as

V1 = "/api/v1"
ISSUE_BODY_SECRET = "secret internal GitHub message"


def approve(world: World, draft_id: int) -> Any:
    return world.client.post(f"{V1}/issue-drafts/{draft_id}/approve", headers=world.headers)


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ---- the happy path ------------------------------------------------------------------------


def test_approve_posts_the_issue_once_and_records_it(world: World) -> None:
    draft = world.draft()
    r = approve(world, draft["id"])
    assert r.status_code == 200
    posted = r.json()
    assert posted["status"] == "posted"
    assert posted["github_issue_number"] == 1
    assert posted["github_issue_url"] == "https://github.com/o/r/issues/1"
    assert posted["last_error"] is None and posted["post_attempts"] == 1
    assert posted["approved_at"].endswith("Z") and posted["approved_by_id"] is not None

    assert world.gh.posts() == 1
    issue = world.gh.repo.issues[0]
    assert issue["title"] == draft["title"]
    # the hidden marker is added at post time only
    assert issue["body"] == f"{draft['body']}\n\n<!-- gitwell-draft:{draft['id']} -->"
    assert "gitwell-draft" not in posted["body"] and posted["body"] == draft["body"]


def test_the_issue_is_posted_with_the_configured_token(world: World) -> None:
    draft = world.draft()
    world.gh.requests.clear()
    approve(world, draft["id"])
    methods = {(m, p): auth for m, p, auth in world.gh.requests}
    assert methods[("GET", "/repositories/42")] == "Bearer tkn"
    assert methods[("POST", "/repos/o/r/issues")] == "Bearer tkn"


def test_a_separate_issues_token_is_used_only_for_posting(
    start_app: Callable[..., TestClient], gh: FakeGitHub
) -> None:
    world = build_world(start_app, gh, github_token="read-token", github_issues_token="write-token")
    scan_auth = {a for m, p, a in gh.requests if "tarball" in p or "commits" in p}
    assert scan_auth == {"Bearer read-token"}  # scanning never used the issues token
    draft = world.draft()
    gh.requests.clear()
    assert approve(world, draft["id"]).status_code == 200
    assert {a for _, _, a in gh.requests} == {"Bearer write-token"}


def test_approving_without_any_token_changes_nothing(
    start_app: Callable[..., TestClient], gh: FakeGitHub
) -> None:
    world = build_world(start_app, gh, github_token=None)
    draft = world.draft()
    calls_before = len(gh.calls)
    r = approve(world, draft["id"])
    assert r.status_code == 422 and error_code(r) == "GITHUB_TOKEN_REQUIRED"
    assert world.get(draft["id"])["status"] == "drafted"
    assert world.get(draft["id"])["post_attempts"] == 0
    assert len(gh.calls) == calls_before  # GitHub was not contacted


def test_double_clicking_posts_once(world: World) -> None:
    draft = world.draft()
    assert approve(world, draft["id"]).status_code == 200
    second = approve(world, draft["id"])  # the browser's second click
    assert second.status_code == 409 and error_code(second) == "INVALID_STATE"
    assert world.gh.posts() == 1 and len(world.gh.repo.issues) == 1


@pytest.mark.parametrize("status", [DraftStatus.posted, DraftStatus.discarded, DraftStatus.posting])
def test_only_drafted_or_approved_drafts_can_be_approved(
    world: World, db: Session, status: DraftStatus
) -> None:
    draft = world.draft()
    db.execute(
        text("UPDATE issue_drafts SET status = :s WHERE id = :i"),
        {"s": status.value, "i": draft["id"]},
    )
    db.commit()
    r = approve(world, draft["id"])
    assert r.status_code == 409 and error_code(r) == "INVALID_STATE"
    assert r.json()["error"]["details"]["status"] == status.value
    assert world.gh.posts() == 0


def test_approving_an_unknown_draft_is_404(world: World) -> None:
    assert approve(world, 9999).status_code == 404


def test_scans_and_other_background_work_never_post(world: World) -> None:
    for sha in ("c" * 40, "d" * 40):
        world.rescan(sha=sha)
    world.draft()
    assert world.gh.posts() == 0  # nothing posts without an explicit approval (S8)


def test_a_renamed_repo_is_posted_to_its_current_name(world: World) -> None:
    draft = world.draft()
    world.gh.repo.rename("renamed")
    assert approve(world, draft["id"]).status_code == 200
    assert world.gh.calls.count(("POST", "/repos/o/renamed/issues")) == 1
    assert world.gh.repo.issues[0]["html_url"] == "https://github.com/o/renamed/issues/1"


# ---- pre-flight checks on GitHub -----------------------------------------------------------


@pytest.mark.parametrize("flag", ["archived", "no_issues"])
def test_a_repo_archived_or_issueless_on_github_blocks_posting_and_is_refreshed(
    world: World, db: Session, flag: str
) -> None:
    draft = world.draft()
    if flag == "archived":
        world.gh.repo.archived = True
    else:
        world.gh.repo.has_issues = False
    r = approve(world, draft["id"])
    assert r.status_code == 422 and error_code(r) == "ISSUES_UNAVAILABLE"
    after = world.get(draft["id"])
    assert after["status"] == "approved" and "disabled" in after["last_error"]
    assert world.gh.posts() == 0
    repo = db.get(Repo, world.repo["id"])
    assert repo is not None
    db.refresh(repo)
    assert (repo.is_archived, repo.has_issues) == ((flag == "archived"), (flag != "no_issues"))


def test_no_push_and_no_triage_permission_blocks_posting(world: World) -> None:
    draft = world.draft()
    world.gh.repo.permissions = {"admin": False, "push": False, "triage": False, "pull": True}
    r = approve(world, draft["id"])
    assert r.status_code == 422 and error_code(r) == "NO_REPO_PERMISSION"
    assert world.get(draft["id"])["status"] == "approved"
    assert world.gh.posts() == 0


@pytest.mark.parametrize(
    "perms", [{"push": True, "triage": False}, {"push": False, "triage": True}]
)
def test_push_or_triage_is_enough(world: World, perms: dict[str, bool]) -> None:
    draft = world.draft()
    world.gh.repo.permissions = perms
    assert approve(world, draft["id"]).status_code == 200


def test_a_repo_that_disappeared_from_github(world: World) -> None:
    draft = world.draft()
    world.gh.repo.gone = True
    r = approve(world, draft["id"])
    assert r.status_code == 422 and error_code(r) == "REPO_NOT_FOUND"
    assert world.get(draft["id"])["status"] == "approved" and world.gh.posts() == 0


def test_rate_limited_before_posting(world: World) -> None:
    draft = world.draft()
    world.gh.repo.rate_limited = True
    r = approve(world, draft["id"])
    assert r.status_code == 503 and error_code(r) == "GITHUB_RATE_LIMITED"
    assert "reset_at" in r.json()["error"]["details"]
    assert world.get(draft["id"])["status"] == "approved" and world.gh.posts() == 0


# ---- the POST itself fails -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "status", "code"),
    [
        (410, 422, "ISSUES_UNAVAILABLE"),
        (403, 422, "NO_REPO_PERMISSION"),
        (404, 422, "NO_REPO_PERMISSION"),
        (401, 502, "GITHUB_ERROR"),
        (422, 502, "GITHUB_ERROR"),
        (500, 503, "GITHUB_UNAVAILABLE"),
        (502, 503, "GITHUB_UNAVAILABLE"),
        (503, 503, "GITHUB_UNAVAILABLE"),
        ("rate_limited", 503, "GITHUB_RATE_LIMITED"),
        ("timeout_before_create", 503, "GITHUB_UNAVAILABLE"),
    ],
)
def test_a_failed_post_leaves_the_draft_approved_with_the_reason(
    world: World, mode: str | int, status: int, code: str
) -> None:
    draft = world.draft()
    world.gh.repo.post_mode = mode
    r = approve(world, draft["id"])
    assert r.status_code == status and error_code(r) == code
    assert ISSUE_BODY_SECRET not in r.text  # never echoes GitHub's response body

    after = world.get(draft["id"])
    assert after["status"] == "approved"
    assert after["post_attempts"] == 1  # counted right before the POST
    assert after["last_error"] and ISSUE_BODY_SECRET not in after["last_error"]
    assert world.gh.posts() == 1  # never auto-retried, even on 5xx and timeouts
    assert world.gh.repo.issues == []


def test_retrying_after_a_clean_failure_posts_successfully(world: World) -> None:
    draft = world.draft()
    world.gh.repo.post_mode = 403
    approve(world, draft["id"])
    first_approved_at = world.get(draft["id"])["approved_at"]

    world.gh.repo.post_mode = "ok"
    r = approve(world, draft["id"])
    assert r.status_code == 200 and r.json()["status"] == "posted"
    assert r.json()["post_attempts"] == 2 and r.json()["last_error"] is None
    assert r.json()["approved_at"] == first_approved_at  # the first approval time is kept
    assert len(world.gh.repo.issues) == 1


# ---- lost responses: a retry must adopt, never duplicate -----------------------------------


def test_a_post_timeout_then_retry_adopts_the_issue_without_a_second_post(world: World) -> None:
    draft = world.draft()
    world.gh.repo.post_mode = "timeout_after_create"  # GitHub created it; the reply never came
    first = approve(world, draft["id"])
    assert first.status_code == 503 and error_code(first) == "GITHUB_UNAVAILABLE"
    mid = world.get(draft["id"])
    assert mid["status"] == "approved" and mid["post_attempts"] == 1
    assert len(world.gh.repo.issues) == 1 and world.gh.posts() == 1

    world.gh.repo.post_mode = "ok"  # even if posting would now work, it must not be used
    retry = approve(world, draft["id"])
    assert retry.status_code == 200
    body = retry.json()
    assert body["status"] == "posted"
    assert body["github_issue_number"] == 1
    assert body["github_issue_url"] == "https://github.com/o/r/issues/1"
    assert body["post_attempts"] == 1  # no new attempt was made
    assert world.gh.posts() == 1  # still exactly one POST
    assert len(world.gh.repo.issues) == 1  # and exactly one issue
    assert ("GET", "/repos/o/r/issues") in world.gh.calls  # the lookup happened


def test_a_5xx_after_creation_is_adopted_on_retry_too(world: World) -> None:
    draft = world.draft()
    world.gh.repo.post_mode = "error_after_create"
    assert approve(world, draft["id"]).status_code == 503
    world.gh.repo.post_mode = "ok"
    retry = approve(world, draft["id"])
    assert retry.json()["status"] == "posted" and world.gh.posts() == 1


def test_if_the_first_post_never_reached_github_the_retry_posts_again(world: World) -> None:
    draft = world.draft()
    world.gh.repo.post_mode = "timeout_before_create"
    approve(world, draft["id"])
    assert world.gh.repo.issues == []
    world.gh.repo.post_mode = "ok"
    retry = approve(world, draft["id"])
    assert retry.json()["status"] == "posted" and retry.json()["post_attempts"] == 2
    assert world.gh.posts() == 2 and len(world.gh.repo.issues) == 1


def test_if_the_duplicate_check_fails_nothing_is_posted(world: World) -> None:
    draft = world.draft()
    world.gh.repo.post_mode = "timeout_after_create"
    approve(world, draft["id"])
    world.gh.repo.post_mode = "ok"
    world.gh.repo.list_status = 500  # cannot look for the existing issue
    r = approve(world, draft["id"])
    assert r.status_code == 503
    assert world.gh.posts() == 1  # posting blind could duplicate: it did not
    assert world.get(draft["id"])["status"] == "approved"

    world.gh.repo.list_status = None
    assert approve(world, draft["id"]).json()["status"] == "posted"
    assert world.gh.posts() == 1


def test_the_marker_is_found_on_a_later_page_of_results(world: World) -> None:
    draft = world.draft()
    world.gh.repo.post_mode = "timeout_before_create"
    approve(world, draft["id"])
    noise = [{"number": n, "body": f"other issue {n}", "html_url": f"u{n}"} for n in range(1, 151)]
    ours = {
        "number": 151,
        "body": f"hello\n\n{marker(draft['id'])}",
        "html_url": "https://github.com/o/r/issues/151",
    }
    world.gh.repo.issues = [*noise, ours]  # 151 issues: ours is on page 2
    retry = approve(world, draft["id"])
    assert retry.json()["github_issue_number"] == 151
    assert world.gh.posts() == 1  # only the first (lost) attempt


def test_another_drafts_marker_is_not_adopted(world: World) -> None:
    draft = world.draft()
    world.gh.repo.post_mode = "timeout_before_create"
    approve(world, draft["id"])
    world.gh.repo.issues = [
        {"number": 1, "body": f"x {marker(draft['id'] + 1)}", "html_url": "u1"},
        {"number": 2, "body": None, "html_url": "u2"},
        {"number": 3, "body": f"{marker(draft['id'])[:-4]}", "html_url": "u3"},  # truncated marker
    ]
    world.gh.repo.post_mode = "ok"
    retry = approve(world, draft["id"])
    assert retry.json()["post_attempts"] == 2 and world.gh.posts() == 2  # posted for real


# ---- crashes -------------------------------------------------------------------------------


def test_a_draft_left_posting_by_a_crash_is_recovered_and_adopts_the_issue(
    start_app: Callable[..., TestClient], gh: FakeGitHub, tmp_path: Any
) -> None:
    url = database_url(tmp_path, "crash.db")
    world = build_world(start_app, gh, database_url=url)
    draft = world.draft()
    # The process died right after GitHub created the issue: the row is stuck in `posting`.
    gh.repo.create_issue(draft["title"], f"{draft['body']}\n\n{marker(draft['id'])}")
    with SessionLocal() as s:
        s.execute(
            text(
                "UPDATE issue_drafts SET status='posting', post_attempts=1, "
                "approved_at=:t WHERE id=:i"
            ),
            {"t": "2026-01-01 00:00:00", "i": draft["id"]},
        )
        s.commit()

    restarted = start_app(database_url=url, github_token="tkn")  # startup recovery runs
    headers = login_as(restarted, "second-admin@example.com", UserRole.admin)
    seen = restarted.get(f"{V1}/issue-drafts/{draft['id']}", headers=headers).json()
    assert seen["status"] == "approved"
    assert seen["last_error"] == "Interrupted while posting; retry to continue."

    posts_before = gh.posts()
    r = restarted.post(f"{V1}/issue-drafts/{draft['id']}/approve", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "posted"
    assert r.json()["github_issue_number"] == 1
    assert gh.posts() == posts_before  # adopted: no second issue
    assert len(gh.repo.issues) == 1


def test_a_crash_before_the_post_is_recovered_and_posts_once(
    start_app: Callable[..., TestClient], gh: FakeGitHub, tmp_path: Any
) -> None:
    url = database_url(tmp_path, "crash2.db")
    world = build_world(start_app, gh, database_url=url)
    draft = world.draft()
    with SessionLocal() as s:  # died after the claim, before the POST: attempts still 0
        s.execute(text("UPDATE issue_drafts SET status='posting' WHERE id=:i"), {"i": draft["id"]})
        s.commit()
    restarted = start_app(database_url=url, github_token="tkn")
    headers = login_as(restarted, "second-admin@example.com", UserRole.admin)
    r = restarted.post(f"{V1}/issue-drafts/{draft['id']}/approve", headers=headers)
    assert r.json()["status"] == "posted" and r.json()["post_attempts"] == 1
    assert gh.posts() == 1


def test_an_unexpected_error_never_leaves_a_draft_stuck_in_posting(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft = world.draft()

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(issue_service, "_bump_attempts", boom)
    r = approve(world, draft["id"])
    assert r.status_code == 500 and error_code(r) == "INTERNAL_ERROR"
    after = world.get(draft["id"])
    assert after["status"] == "approved" and after["last_error"] == "Unexpected internal error."
    assert world.gh.posts() == 0
    monkeypatch.undo()
    assert approve(world, draft["id"]).json()["status"] == "posted"


def test_the_posting_deadline_is_reported_as_github_unavailable(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft = world.draft()
    monkeypatch.setattr(issue_service, "POST_TIMEOUT_SECONDS", 0)
    r = approve(world, draft["id"])
    assert r.status_code == 503 and error_code(r) == "GITHUB_UNAVAILABLE"
    assert world.get(draft["id"])["status"] == "approved" and world.gh.posts() == 0


# ---- concurrency (real threads, file database) ---------------------------------------------


def _approve_in_thread(settings: Settings, draft_id: int, admin_id: int) -> Any:
    """Run the service exactly as a request would: own session, own client."""
    client = GitHubClient(settings.github_api_url, "tkn")
    try:
        with SessionLocal() as session:
            admin = session.get(User, admin_id)
            assert admin is not None
            return issue_service.approve(session, settings, client, draft_id, admin)
    except AppError as exc:
        return exc
    finally:
        client.close()


def _admin_id() -> int:
    with SessionLocal() as s:
        return int(s.scalars(select(User.id).where(User.role == UserRole.admin)).first() or 0)


def test_a_second_approve_during_an_in_flight_post_is_rejected_and_posts_once(
    file_world: World,
) -> None:
    world = file_world
    draft = world.draft()
    settings = make_settings(github_token="tkn")
    admin_id = _admin_id()
    in_flight, release = threading.Event(), threading.Event()

    def block() -> None:  # the first approval is now talking to GitHub
        in_flight.set()
        assert release.wait(20)

    world.gh.repo.on_repo_lookup = block
    results: dict[str, Any] = {}
    first = threading.Thread(
        target=lambda: results.__setitem__(
            "first", _approve_in_thread(settings, draft["id"], admin_id)
        )
    )
    first.start()
    assert in_flight.wait(20)

    world.gh.repo.on_repo_lookup = None
    second = _approve_in_thread(settings, draft["id"], admin_id)  # a second admin clicks now
    assert isinstance(second, AppError) and second.code == "INVALID_STATE"
    assert second.details["status"] == "posting"

    release.set()
    first.join(30)
    assert results["first"].status.value == "posted"
    assert world.gh.posts() == 1 and len(world.gh.repo.issues) == 1


def test_many_simultaneous_approves_create_exactly_one_issue(file_world: World) -> None:
    world = file_world
    draft = world.draft()
    settings = make_settings(github_token="tkn")
    admin_id = _admin_id()
    n = 6
    start = threading.Barrier(n, timeout=20)
    outcomes: list[Any] = []
    lock = threading.Lock()

    def worker() -> None:
        start.wait()
        outcome = _approve_in_thread(settings, draft["id"], admin_id)
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    assert len(outcomes) == n
    winners = [o for o in outcomes if not isinstance(o, AppError)]
    losers = [o for o in outcomes if isinstance(o, AppError)]
    assert len(winners) == 1 and winners[0].status.value == "posted"
    assert len(losers) == n - 1 and {e.code for e in losers} == {"INVALID_STATE"}
    assert world.gh.posts() == 1  # the respx call count: exactly one POST
    assert len(world.gh.repo.issues) == 1


def test_two_admins_creating_a_draft_at_once_get_one_draft(file_world: World) -> None:
    world = file_world
    n = 5
    start = threading.Barrier(n, timeout=20)
    outcomes: list[Any] = []
    lock = threading.Lock()
    admin_id = _admin_id()

    def worker() -> None:
        start.wait()
        with SessionLocal() as session:
            admin = session.get(User, admin_id)
            assert admin is not None
            try:
                outcome: Any = issue_service.create_draft(session, admin, world.scan_id)
            except AppError as exc:
                outcome = exc
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    created = [o for o in outcomes if not isinstance(o, AppError)]
    refused = [o for o in outcomes if isinstance(o, AppError)]
    assert len(created) == 1 and {e.code for e in refused} == {"DRAFT_EXISTS"}
    with SessionLocal() as s:
        assert s.scalars(select(IssueDraft)).all().__len__() == 1


# ---- privacy and logging -------------------------------------------------------------------


def test_logs_never_contain_the_token_or_the_issue_text(
    capsys: pytest.CaptureFixture[str], start_app: Callable[..., TestClient], gh: FakeGitHub
) -> None:
    world = build_world(start_app, gh, github_token="super-secret-token-value")
    draft = world.draft()
    world.gh.repo.post_mode = 500
    approve(world, draft["id"])
    world.gh.repo.post_mode = "ok"
    approve(world, draft["id"])
    output = capsys.readouterr().out
    assert "super-secret-token-value" not in output
    assert draft["title"] not in output and "How to fix" not in output
    assert ISSUE_BODY_SECRET not in output
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert any(r.get("message") == "issue_posted" for r in records)


def test_the_s7_leak_check_extends_to_the_issue_body(world: World) -> None:
    secret_values = ["AKIA" + "Q" * 16, "ghp_" + "z" * 36]
    world.rescan(
        {**GOOD_FILES, "src/@org/cfg.py": f"A = '{secret_values[0]}'\nB = '{secret_values[1]}'"}
    )
    draft = world.draft()
    for value in secret_values:
        assert value not in draft["body"] and value not in draft["title"]
    assert "cfg.py" not in draft["body"] and "src/" not in draft["body"]
    approve(world, draft["id"])
    posted = world.gh.repo.issues[0]
    for value in secret_values:
        assert value not in str(posted)
    assert "cfg.py" not in str(posted)
