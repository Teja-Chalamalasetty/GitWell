from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.scan import Scan, ScanStatus, ScanTrigger
from tests.fake_github import BARE_FILES, GOOD_FILES, FakeGitHub
from tests.helpers import auth

V1 = "/api/v1"
URL = "https://github.com/o/r"


def add(client: TestClient, headers: dict[str, str], url: str = URL):  # type: ignore[no-untyped-def]
    return client.post(f"{V1}/repos", headers=headers, json={"url": url})


def count(db: Session, table: str) -> int:
    return int(db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


# ---- the end-to-end flow (M4 acceptance) ---------------------------------------------------


def test_add_repo_scans_then_rescan_with_a_different_score_gives_the_right_delta(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    gh.repo.files = BARE_FILES
    r = add(client, admin_headers)
    assert r.status_code == 201
    repo = r.json()
    assert repo["full_name"] == "o/r" and repo["github_id"] == 42
    assert repo["url"] == "https://github.com/o/r"
    assert repo["default_branch"] == "main" and repo["is_private"] is False

    # the on_add scan ran (inline executor) and completed
    first = repo["latest_completed_scan"]
    assert first["score"] == 22 and first["grade"] == "F"
    assert first["commit_sha"] == "a" * 40 and first["has_findings"] is True
    assert repo["latest_scan"]["status"] == "completed"
    assert repo["score_delta"] is None  # only one completed scan so far
    assert repo["active_scan_id"] is None and repo["open_draft_id"] is None

    scans = client.get(f"{V1}/repos/{repo['id']}/scans", headers=admin_headers).json()
    assert scans["total"] == 1 and scans["items"][0]["trigger"] == "on_add"

    # results were saved, in rule order, with names and fix tips
    detail = client.get(f"{V1}/scans/{first['id']}", headers=admin_headers).json()
    assert [x["rule_id"] for x in detail["results"]] == [
        "secrets",
        "readme",
        "tests",
        "ci",
        "license",
        "dependencies",
        "gitignore",
        "docs",
    ]
    by_id = {x["rule_id"]: x for x in detail["results"]}
    assert by_id["secrets"]["status"] == "pass" and by_id["secrets"]["rule_name"] == (
        "No committed secrets"
    )
    assert by_id["readme"]["status"] == "fail" and "README" in by_id["readme"]["fix_tip"]
    assert by_id["dependencies"]["status"] == "not_applicable"
    assert detail["ruleset_version"] and len(detail["ruleset_version"]) == 12
    assert detail["snapshot_mode"] == "archive" and detail["is_partial"] is False

    # re-scan using a SECOND fixture with a different score (and commit)
    gh.repo.files, gh.repo.sha = GOOD_FILES, "b" * 40
    queued = client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued" and queued.json()["trigger"] == "manual"

    after = client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).json()
    second = after["latest_completed_scan"]
    assert second["score"] == 100 and second["grade"] == "A"
    assert second["commit_sha"] == "b" * 40 and second["has_findings"] is False
    assert after["score_delta"] == 78  # 100 - 22

    # history is newest first
    history = client.get(f"{V1}/repos/{repo['id']}/scans", headers=admin_headers).json()
    assert [s["score"] for s in history["items"]] == [100, 22]

    # and a regression produces a negative delta
    gh.repo.files, gh.repo.sha = BARE_FILES, "c" * 40
    client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    assert (
        client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).json()["score_delta"] == -78
    )


def test_score_delta_is_null_when_rulesets_differ_or_a_score_is_null(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    repo = add(client, admin_headers).json()
    gh.repo.files, gh.repo.sha = GOOD_FILES, "b" * 40
    client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    assert client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).json()["score_delta"] == 78

    oldest = db.query(Scan).filter_by(repo_id=repo["id"]).order_by(Scan.id).first()
    assert oldest is not None
    oldest.ruleset_version = "000000000000"
    db.commit()
    assert (
        client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).json()["score_delta"] is None
    )

    oldest.ruleset_version = db.query(Scan).order_by(Scan.id.desc()).first().ruleset_version  # type: ignore[union-attr]
    oldest.score = None
    db.commit()
    assert (
        client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).json()["score_delta"] is None
    )


# ---- adding repos --------------------------------------------------------------------------


def test_invalid_url_is_422_invalid_repo_url(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    for bad in ("http://github.com/o/r", "https://github.com/o", "https://gitlab.com/o/r", ""):
        r = add(client, admin_headers, bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_REPO_URL", bad
    assert gh.calls == []  # never reached GitHub


def test_unknown_repo_is_422_repo_not_found(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    r = add(client, admin_headers, "https://github.com/o/missing")
    assert r.status_code == 422 and r.json()["error"]["code"] == "REPO_NOT_FOUND"


def test_inaccessible_repo_is_repo_not_found_too(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    gh.repo.gone = True
    r = add(client, admin_headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "REPO_NOT_FOUND"


def test_github_rate_limit_while_adding_is_503(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    gh.repo.rate_limited = True
    r = add(client, admin_headers)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "GITHUB_RATE_LIMITED"
    assert "reset_at" in r.json()["error"]["details"]


def test_duplicate_repo_is_409(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    assert add(client, admin_headers).status_code == 201
    for variant in (URL, URL + ".git", URL + "/", "https://github.com/O/R"):
        r = add(client, admin_headers, variant)
        assert r.status_code == 409 and r.json()["error"]["code"] == "REPO_ALREADY_EXISTS"
    assert count(db, "repos") == 1


def test_adding_a_renamed_repo_via_its_old_url_is_409_same_github_id(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    assert add(client, admin_headers).status_code == 201
    gh.repo.rename("renamed")  # GitHub now 301s o/r -> the repo by id
    r = add(client, admin_headers, "https://github.com/o/r")
    assert r.status_code == 409 and r.json()["error"]["code"] == "REPO_ALREADY_EXISTS"
    r = add(client, admin_headers, "https://github.com/o/renamed")
    assert r.status_code == 409  # the new name is the same repo too
    assert count(db, "repos") == 1


def test_a_scan_refreshes_a_renamed_repo(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    repo = add(client, admin_headers).json()
    gh.repo.rename("Shiny-New")
    gh.repo.default_branch, gh.repo.archived, gh.repo.private = "trunk", True, True
    client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    after = client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).json()
    assert after["full_name"] == "o/shiny-new"  # lowercased, unique
    assert after["url"] == "https://github.com/o/Shiny-New"  # GitHub's casing
    assert after["default_branch"] == "trunk"
    assert after["is_archived"] is True and after["is_private"] is True


def test_repo_names_keep_githubs_casing_but_full_name_is_lowercase(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    gh.repo.owner, gh.repo.name = "Some-Org", "My_Repo"
    repo = add(client, admin_headers, "https://github.com/some-org/my_repo").json()
    assert repo["url"] == "https://github.com/Some-Org/My_Repo"
    assert repo["full_name"] == "some-org/my_repo"


def test_unexpected_github_payload_is_a_502(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    gh.repo.default_branch = ""  # makes the payload invalid
    r = add(client, admin_headers)
    assert r.status_code == 502 and r.json()["error"]["code"] == "GITHUB_ERROR"


def test_viewers_cannot_add_repos(
    client: TestClient, gh: FakeGitHub, viewer_headers: dict[str, str]
) -> None:
    assert add(client, viewer_headers).status_code == 403
    assert gh.calls == []


# ---- reading repos -------------------------------------------------------------------------


def test_get_repo_and_404(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str]
) -> None:
    repo = add(client, admin_headers).json()
    assert client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).json()["id"] == repo["id"]
    r = client.get(f"{V1}/repos/9999", headers=admin_headers)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_viewers_can_read_everything(
    client: TestClient,
    gh: FakeGitHub,
    admin_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    repo = add(client, admin_headers).json()
    assert client.get(f"{V1}/repos", headers=viewer_headers).json()["total"] == 1
    assert client.get(f"{V1}/repos/{repo['id']}", headers=viewer_headers).status_code == 200
    scan_id = repo["latest_scan"]["id"]
    assert client.get(f"{V1}/scans/{scan_id}", headers=viewer_headers).status_code == 200
    assert client.get(f"{V1}/rules", headers=viewer_headers).status_code == 200


def test_list_repos_sorting_pagination_and_nulls_last(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    specs = [
        ("o", "bravo", 1, GOOD_FILES),
        ("o", "alpha", 2, BARE_FILES),
        ("o", "charlie", 3, BARE_FILES),
    ]
    for owner, name, gid, files in specs:
        fake = gh.add_repo(owner, name, gid)
        fake.files = files
        assert add(client, admin_headers, f"https://github.com/{owner}/{name}").status_code == 201
    # a repo that has never completed a scan: no score and no scans
    gh.add_repo("o", "delta", 4)
    never = add(client, admin_headers, "https://github.com/o/delta").json()
    db.execute(text("DELETE FROM scans WHERE repo_id = :r"), {"r": never["id"]})
    db.commit()

    def names(**params: object) -> list[str]:
        body = client.get(f"{V1}/repos", headers=admin_headers, params=params).json()  # type: ignore[arg-type]
        return [r["full_name"].split("/")[1] for r in body["items"]]

    assert names() == ["alpha", "bravo", "charlie", "delta"]  # default: name asc
    assert names(sort="name", order="desc") == ["delta", "charlie", "bravo", "alpha"]
    # score: bravo=100, alpha=charlie=22 (tie broken by name), delta=null -> always last
    assert names(sort="score", order="desc") == ["bravo", "alpha", "charlie", "delta"]
    assert names(sort="score", order="asc") == ["alpha", "charlie", "bravo", "delta"]
    # last_scanned: delta has no scans -> last in both directions
    assert names(sort="last_scanned", order="asc")[-1] == "delta"
    assert names(sort="last_scanned", order="desc")[-1] == "delta"
    assert names(sort="last_scanned", order="desc")[:3] == ["charlie", "alpha", "bravo"]

    page = client.get(f"{V1}/repos", headers=admin_headers, params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 4 and [r["full_name"] for r in page["items"]] == [
        "o/bravo",
        "o/charlie",
    ]


def test_list_repos_validates_parameters(client: TestClient, admin_headers: dict[str, str]) -> None:
    for params in (
        {"sort": "nope"},
        {"order": "sideways"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
    ):
        r = client.get(f"{V1}/repos", headers=admin_headers, params=params)  # type: ignore[arg-type]
        assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR", params


def test_empty_repo_list(client: TestClient, admin_headers: dict[str, str]) -> None:
    assert client.get(f"{V1}/repos", headers=admin_headers).json() == {"items": [], "total": 0}


# ---- deleting ------------------------------------------------------------------------------


def test_delete_repo_cascades_everything(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    repo = add(client, admin_headers).json()
    client.post(f"{V1}/repos/{repo['id']}/scan", headers=admin_headers)
    assert count(db, "scans") == 2 and count(db, "scan_results") == 16

    r = client.delete(f"{V1}/repos/{repo['id']}", headers=admin_headers)
    assert r.status_code == 204
    assert [count(db, t) for t in ("repos", "scans", "scan_results", "issue_drafts")] == [0] * 4
    assert client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).status_code == 404
    assert client.delete(f"{V1}/repos/{repo['id']}", headers=admin_headers).status_code == 404


def test_repo_can_be_deleted_while_a_scan_is_active(
    client: TestClient, gh: FakeGitHub, admin_headers: dict[str, str], db: Session
) -> None:
    repo = add(client, admin_headers).json()
    db.add(Scan(repo_id=repo["id"], status=ScanStatus.running, trigger=ScanTrigger.manual))
    db.commit()
    assert client.delete(f"{V1}/repos/{repo['id']}", headers=admin_headers).status_code == 204
    assert count(db, "scans") == 0


def test_a_viewer_cannot_delete(
    client: TestClient,
    gh: FakeGitHub,
    admin_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    repo = add(client, admin_headers).json()
    r = client.delete(f"{V1}/repos/{repo['id']}", headers=viewer_headers)
    assert r.status_code == 403
    assert client.get(f"{V1}/repos/{repo['id']}", headers=admin_headers).status_code == 200


def test_auth_header_helper_is_used() -> None:
    assert auth("t") == {"Authorization": "Bearer t"}
