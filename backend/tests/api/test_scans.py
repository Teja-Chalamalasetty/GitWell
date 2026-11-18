from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.scan import Scan, ScanResult, ScanStatus, ScanTrigger
from app.models.user import UserRole
from app.rules.base import Rule, RuleResult, Status
from app.scanner.errors import SCAN_ERRORS
from tests.fake_github import GOOD_FILES, FakeGitHub
from tests.helpers import V1_RULE_IDS, ManualExecutor, login_as, v1_rules

V1 = "/api/v1"


def add_repo(
    client: TestClient, headers: dict[str, str], url: str = "https://github.com/o/r"
) -> dict[str, Any]:
    r = client.post(f"{V1}/repos", headers=headers, json={"url": url})
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


def latest(client: TestClient, headers: dict[str, str], repo_id: int) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"{V1}/repos/{repo_id}", headers=headers).json()
    return body["latest_scan"]  # type: ignore[no-any-return]


# ---- the one-active-scan guarantee ---------------------------------------------------------


def test_posting_a_scan_while_one_is_queued_is_409_scan_in_progress(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    repo = add_repo(client, admin_headers)
    queued = Scan(repo_id=repo["id"], status=ScanStatus.queued, trigger=ScanTrigger.scheduled)
    db.add(queued)  # inserted directly, as a concurrent request would have
    db.commit()

    r = client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SCAN_IN_PROGRESS"
    assert r.json()["error"]["details"]["scan_id"] == queued.id
    assert db.query(Scan).filter_by(repo_id=repo["id"]).count() == 2  # the on_add scan + queued


def test_a_running_scan_blocks_a_new_one_too(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    repo = add_repo(client, admin_headers)
    db.add(Scan(repo_id=repo["id"], status=ScanStatus.running, trigger=ScanTrigger.manual))
    db.commit()
    r = client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SCAN_IN_PROGRESS"


def test_finished_scans_never_block(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    repo = add_repo(client, admin_headers)
    for _ in range(3):
        assert (
            client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers).status_code == 202
        )


def test_two_quick_triggers_with_a_deferred_executor_only_one_wins(
    start_app: Callable[..., TestClient], gh: FakeGitHub
) -> None:
    executor = ManualExecutor()
    client = start_app(executor=executor)
    headers = login_as(client, "admin@example.com", UserRole.admin)
    repo = add_repo(client, headers)  # the on_add scan is queued, not yet run
    summary = client.get(f"{V1}/repos/{repo['id']}", headers=headers).json()
    assert summary["active_scan_id"] == repo["latest_scan"]["id"]
    assert summary["latest_scan"]["status"] == "queued"
    assert summary["latest_completed_scan"] is None

    r = client.post(f"{V1}/repos/{repo['id']}/scan", headers=headers)
    assert (
        r.status_code == 409
        and r.json()["error"]["details"]["scan_id"] == summary["active_scan_id"]
    )

    executor.run_all()  # the worker now runs the queued scan
    done = client.get(f"{V1}/repos/{repo['id']}", headers=headers).json()
    assert done["active_scan_id"] is None and done["latest_scan"]["status"] == "completed"
    assert client.post(f"{V1}/repos/{repo['id']}/scan", headers=headers).status_code == 202


def test_scan_unknown_repo_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.post(f"{V1}/repos/9999/scan", headers=admin_headers)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_viewers_cannot_trigger_scans(
    client: TestClient,
    gh: FakeGitHub,
    admin_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    repo = add_repo(client, admin_headers)
    assert client.post(f"{V1}/repos/{repo['id']}/scan", headers=viewer_headers).status_code == 403


def test_a_scan_that_was_picked_up_twice_runs_once(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    repo = add_repo(client, admin_headers)
    runner = client.app.state.scan_runner  # type: ignore[attr-defined]
    scan = Scan(repo_id=repo["id"], status=ScanStatus.queued, trigger=ScanTrigger.manual)
    db.add(scan)
    db.commit()
    calls_before = len(gh.calls)
    runner.run(scan.id)
    after_first = len(gh.calls)
    runner.run(scan.id)  # already completed: the conditional claim updates 0 rows
    assert after_first > calls_before and len(gh.calls) == after_first


# ---- scan failures (catalog codes) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("configure", "code"),
    [
        (lambda r: setattr(r, "empty", True), "REPO_EMPTY"),
        (lambda r: setattr(r, "rate_limited", True), "GITHUB_RATE_LIMITED"),
        (lambda r: setattr(r, "gone", True), "REPO_NOT_FOUND"),
        (lambda r: setattr(r, "disabled", True), "REPO_DISABLED"),
    ],
    ids=["empty", "rate-limited", "not-found", "disabled"],
)
def test_failed_scans_record_catalog_codes_and_messages(
    client: TestClient,
    gh: FakeGitHub,
    admin_headers: dict[str, str],
    configure: Callable[[Any], None],
    code: str,
) -> None:
    repo = add_repo(client, admin_headers)
    configure(gh.repo)
    r = client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    assert r.status_code == 202  # the request succeeded; the scan itself fails

    scan = client.get(f"{V1}/repos/{repo['id']}/scans", headers=admin_headers).json()["items"][0]
    assert scan["status"] == "failed"
    assert scan["error_code"] == code
    assert scan["error_message"] == SCAN_ERRORS[code]
    assert scan["score"] is None and scan["finished_at"] is not None
    detail = client.get(f"{V1}/scans/{scan['id']}", headers=admin_headers).json()
    assert detail["results"] == []
    summary = client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).json()
    assert summary["latest_scan"]["status"] == "failed"
    assert summary["latest_scan"]["error_code"] == code
    assert summary["latest_completed_scan"]["score"] == 22  # the earlier good scan is kept
    assert summary["active_scan_id"] is None  # a failed scan never blocks the next one


def test_a_failed_scan_after_the_commit_lookup_still_records_the_sha(
    client: TestClient,
    gh: FakeGitHub,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = add_repo(client, admin_headers)
    runner = client.app.state.scan_runner  # type: ignore[attr-defined]

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("app.scanner.worker.build_snapshot", boom)
    client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    scan = client.get(f"{V1}/repos/{repo['id']}/scans", headers=admin_headers).json()["items"][0]
    assert scan["error_code"] == "INTERNAL_ERROR"
    assert scan["error_message"] == SCAN_ERRORS["INTERNAL_ERROR"]
    assert "secret internal detail" not in str(scan)  # never built from str(exception)
    assert scan["commit_sha"] == "a" * 40
    assert runner is not None


def test_scan_timeout_is_recorded(start_app: Callable[..., TestClient], gh: FakeGitHub) -> None:
    client = start_app(scan_timeout_seconds=0)
    headers = login_as(client, "admin@example.com", UserRole.admin)
    repo = add_repo(client, headers)
    scan = client.get(f"{V1}/repos/{repo['id']}/scans", headers=headers).json()["items"][0]
    assert scan["status"] == "failed" and scan["error_code"] == "SCAN_TIMEOUT"
    assert scan["error_message"] == "Scan exceeded the time limit."


def test_too_large_without_a_token_fails_with_repo_too_large(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    gh.repo.size_kb = 60 * 1024  # over MAX_ARCHIVE_MB, and the test app has no GitHub token
    repo = add_repo(client, admin_headers)
    assert repo["latest_scan"]["status"] == "failed"
    assert repo["latest_scan"]["error_code"] == "REPO_TOO_LARGE"


# ---- rule errors ---------------------------------------------------------------------------


class Crashing(Rule):
    id = "crashing"
    name = "Crashing"
    description = "always raises"
    weight = 10
    display_order = 35
    fix_tip = "n/a"

    def check(self, snap: Any) -> RuleResult:
        raise ValueError("boom")


def test_a_raising_rule_is_recorded_as_error_and_the_scan_still_completes(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    gh.repo.files = GOOD_FILES
    client.app.state.scan_runner._rules = lambda: [*v1_rules(), Crashing()]  # type: ignore[attr-defined]
    repo = add_repo(client, admin_headers)

    done = repo["latest_completed_scan"]
    assert done is not None, repo["latest_scan"]
    assert done["rules_errored"] == 1
    assert done["score"] == 100  # the errored rule is excluded from the score
    detail = client.get(f"{V1}/scans/{done['id']}", headers=admin_headers).json()
    assert detail["rules_errored"] == 1 and detail["status"] == "completed"
    crashed = next(x for x in detail["results"] if x["rule_id"] == "crashing")
    assert crashed["status"] == "error" and crashed["message"] == "Rule failed to run."
    assert crashed["rule_name"] == "crashing"  # not in the registry any more: falls back to its id
    assert crashed["fix_tip"] == ""
    assert [x["rule_id"] for x in detail["results"]][3] == "crashing"  # display_order 35
    row = db.scalars(select(ScanResult).where(ScanResult.rule_id == "crashing")).one()
    assert row.status.value == "error"


def test_secrets_erroring_gives_a_null_score_and_null_delta(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    class BoomSecrets(Crashing):
        id = "secrets"
        display_order = 10

    others = [r for r in v1_rules() if r.id != "secrets"]
    client.app.state.scan_runner._rules = lambda: [BoomSecrets(), *others]  # type: ignore[attr-defined]
    repo = add_repo(client, admin_headers)
    done = repo["latest_completed_scan"]
    assert done["score"] is None and done["grade"] is None and done["rules_errored"] == 1
    assert repo["score_delta"] is None


# ---- listing and details -------------------------------------------------------------------


def test_scan_list_filters_by_status_and_paginates_newest_first(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    repo = add_repo(client, admin_headers)
    gh.repo.empty = True
    client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)  # failed
    gh.repo.empty = False
    client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)  # completed

    url = f"{V1}/repos/{repo['id']}/scans"
    everything = client.get(url, headers=admin_headers).json()
    assert everything["total"] == 3
    assert [s["status"] for s in everything["items"]] == ["completed", "failed", "completed"]
    assert [s["id"] for s in everything["items"]] == sorted(
        (s["id"] for s in everything["items"]), reverse=True
    )
    failed = client.get(url, headers=admin_headers, params={"status": "failed"}).json()
    assert failed["total"] == 1 and failed["items"][0]["error_code"] == "REPO_EMPTY"
    completed = client.get(url, headers=admin_headers, params={"status": "completed"}).json()
    assert completed["total"] == 2
    page = client.get(url, headers=admin_headers, params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and page["items"][0]["status"] == "failed"
    assert client.get(url, headers=admin_headers, params={"status": "nope"}).status_code == 422
    assert client.get(f"{V1}/repos/9999/scans", headers=admin_headers).status_code == 404


def test_scan_detail_404_and_shape(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    assert client.get(f"{V1}/scans/9999", headers=admin_headers).status_code == 404
    repo = add_repo(client, admin_headers)
    detail = client.get(f"{V1}/scans/{repo['latest_scan']['id']}", headers=admin_headers).json()
    assert detail["repo_id"] == repo["id"] and detail["trigger"] == "on_add"
    assert detail["created_at"].endswith("Z") and detail["finished_at"].endswith("Z")
    first = detail["results"][0]
    assert set(first) == {
        "rule_id",
        "rule_name",
        "status",
        "weight",
        "display_order",
        "message",
        "details",
        "fix_tip",
    }


def test_secret_findings_show_only_location_never_the_value(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    key = "AKIA" + "Q" * 16
    gh.repo.files = {"app/config.py": f"x = 1\nKEY = '{key}'\n"}
    repo = add_repo(client, admin_headers)
    detail = client.get(
        f"{V1}/scans/{repo['latest_completed_scan']['id']}", headers=admin_headers
    ).json()
    secrets = next(x for x in detail["results"] if x["rule_id"] == "secrets")
    assert secrets["status"] == "fail"
    assert secrets["details"]["findings"] == [
        {"path": "app/config.py", "line": 2, "pattern": "aws_access_key_id", "confidence": "high"}
    ]
    assert key not in str(detail)


def test_partial_scans_are_labelled(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    gh.repo.files = {f"f{i}.txt": "x" * 300 for i in range(3)}
    client.app.state.scan_runner._limits = type(client.app.state.scan_runner._limits)(  # type: ignore[attr-defined]
        50 * 1024**2, 500 * 1024**2, 100_000, 524_288, max_text_total_bytes=500, api_max_fetches=40
    )
    repo = add_repo(client, admin_headers)
    assert repo["latest_completed_scan"]["is_partial"] is True


# ---- /rules --------------------------------------------------------------------------------


def test_rules_endpoint_returns_a_bare_array_in_display_order(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    r = client.get(f"{V1}/rules", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and len(body) >= 8
    ids = [x["id"] for x in body]
    v1 = [i for i in ids if i in V1_RULE_IDS]
    assert v1 == list(V1_RULE_IDS)  # the shipped rules, in display order (others may exist too)
    assert [x["display_order"] for x in body] == sorted(x["display_order"] for x in body)
    assert sum(x["weight"] for x in body if x["id"] in V1_RULE_IDS) == 100
    assert set(body[0]) == {"id", "name", "description", "weight", "display_order", "fix_tip"}


def test_rules_endpoint_requires_login(client: TestClient) -> None:
    assert client.get(f"{V1}/rules").status_code == 401


def test_scan_rows_in_db_use_value_strings(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    add_repo(client, admin_headers)
    assert db.execute(
        text("SELECT DISTINCT status FROM scan_results ORDER BY 1")
    ).scalars().all() == [
        "fail",
        "not_applicable",
        "pass",
    ]
    assert Status.PASS.value == "pass"
