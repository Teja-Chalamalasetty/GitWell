from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.repo import Repo
from app.models.scan import RuleStatus, Scan, ScanResult, ScanStatus, ScanTrigger
from app.rules.registry import get_rules
from app.services.dashboard_service import build_summary, score_trend

TODAY = date(2026, 11, 18)
GRADES = ["A", "B", "C", "D", "F"]


def at(day: str, hour: int = 12) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, tzinfo=UTC)


def grade_for(score: int | None) -> str | None:
    if score is None:
        return None
    return (
        "A"
        if score >= 90
        else "B"
        if score >= 75
        else "C"
        if score >= 60
        else ("D" if score >= 40 else "F")
    )


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
    score: int | None,
    day: str,
    *,
    status: ScanStatus = ScanStatus.completed,
    results: dict[str, RuleStatus] | None = None,
    hour: int = 12,
) -> Scan:
    when = at(day, hour)
    scan = Scan(
        repo_id=repo.id,
        status=status,
        trigger=ScanTrigger.manual,
        score=score,
        grade=grade_for(score),
        created_at=when,
        started_at=when,
        finished_at=when,
    )
    db.add(scan)
    db.commit()
    for order, (rule_id, rule_status) in enumerate((results or {}).items()):
        db.add(
            ScanResult(
                scan_id=scan.id,
                rule_id=rule_id,
                status=rule_status,
                message="m",
                details={},
                weight=1,
                display_order=order,
            )
        )
    db.commit()
    return scan


def trend(db: Session) -> list[tuple[str, float]]:
    return [(p.date, p.average_score) for p in score_trend(db, TODAY)]


# ---- empty and basic shapes ----------------------------------------------------------------


def test_empty_database(db: Session) -> None:
    summary = build_summary(db, TODAY)
    assert summary.repo_count == 0 and summary.scanned_repo_count == 0
    assert summary.average_score is None
    assert summary.grade_distribution == dict.fromkeys(GRADES, 0)
    assert summary.score_trend == []
    # every registered rule is still listed, with zero counts, in display order
    assert [r.rule_id for r in summary.rule_failures] == [r.id for r in get_rules()]
    assert all(r.fail == 0 and r.warn == 0 for r in summary.rule_failures)


def test_grade_distribution_always_has_all_five_keys(db: Session) -> None:
    repo = add_repo(db, 1)
    add_scan(db, repo, 95, "2026-11-17")
    dist = build_summary(db, TODAY).grade_distribution
    assert list(dist) == GRADES and dist == {"A": 1, "B": 0, "C": 0, "D": 0, "F": 0}


def test_repos_without_a_completed_scan_count_as_tracked_but_not_scanned(db: Session) -> None:
    never = add_repo(db, 1)
    failed_only = add_repo(db, 2)
    add_repo(db, 3)
    add_scan(db, failed_only, None, "2026-11-17", status=ScanStatus.failed)
    summary = build_summary(db, TODAY)
    assert (summary.repo_count, summary.scanned_repo_count) == (3, 0)
    assert summary.average_score is None
    assert never.id  # silence unused


def test_running_and_failed_scans_are_ignored(db: Session) -> None:
    repo = add_repo(db, 1)
    add_scan(db, repo, 80, "2026-11-10")
    add_scan(db, repo, 20, "2026-11-17", status=ScanStatus.failed)
    add_scan(db, repo, 10, "2026-11-18", status=ScanStatus.running)
    summary = build_summary(db, TODAY)
    assert summary.average_score == 80.0 and summary.grade_distribution["B"] == 1


# ---- null scores ---------------------------------------------------------------------------


def test_null_scores_count_as_scanned_but_not_in_average_or_grades(db: Session) -> None:
    a, b, c = add_repo(db, 1), add_repo(db, 2), add_repo(db, 3)
    add_scan(db, a, 80, "2026-11-17")
    add_scan(db, b, None, "2026-11-17")  # e.g. the secrets rule errored
    add_scan(db, c, 41, "2026-11-17")
    summary = build_summary(db, TODAY)
    assert summary.scanned_repo_count == 3
    assert summary.average_score == 60.5  # (80 + 41) / 2
    assert summary.grade_distribution == {"A": 0, "B": 1, "C": 0, "D": 1, "F": 0}


def test_average_is_null_when_every_score_is_null(db: Session) -> None:
    add_scan(db, add_repo(db, 1), None, "2026-11-17")
    summary = build_summary(db, TODAY)
    assert summary.scanned_repo_count == 1 and summary.average_score is None
    assert summary.grade_distribution == dict.fromkeys(GRADES, 0)
    assert summary.score_trend == []


def test_average_is_rounded_to_one_decimal(db: Session) -> None:
    for n, score in enumerate((100, 100, 99), start=1):
        add_scan(db, add_repo(db, n), score, "2026-11-17")
    assert build_summary(db, TODAY).average_score == 99.7  # 99.666...


def test_only_each_repos_latest_completed_scan_counts(db: Session) -> None:
    repo = add_repo(db, 1)
    add_scan(db, repo, 10, "2026-11-01")
    add_scan(db, repo, 95, "2026-11-10")
    add_scan(db, repo, 62, "2026-11-17")
    summary = build_summary(db, TODAY)
    assert summary.average_score == 62.0
    assert summary.grade_distribution == {"A": 0, "B": 0, "C": 1, "D": 0, "F": 0}


# ---- rule failures -------------------------------------------------------------------------


def test_rule_failures_count_the_latest_scans_only_and_sort_correctly(db: Session) -> None:
    r1, r2, r3 = add_repo(db, 1), add_repo(db, 2), add_repo(db, 3)
    F, W, P = RuleStatus.fail, RuleStatus.warn, RuleStatus.pass_
    # an OLD scan with failures that must not count
    add_scan(db, r1, 0, "2026-11-01", results={"docs": F, "ci": F, "tests": F})
    add_scan(db, r1, 50, "2026-11-17", results={"tests": F, "ci": W, "readme": P, "docs": P})
    add_scan(db, r2, 50, "2026-11-17", results={"tests": F, "ci": W, "docs": F, "license": W})
    add_scan(db, r3, 50, "2026-11-17", results={"tests": W, "license": W, "secrets": P})
    rows = build_summary(db, TODAY).rule_failures
    got = [(r.rule_id, r.fail, r.warn) for r in rows]
    # sorted by fail desc, then warn desc, then display_order
    assert got[0] == ("tests", 2, 1)
    assert got[1] == ("docs", 1, 0)
    assert [g[0] for g in got[2:4]] == ["ci", "license"]  # both 0 fail / 2 warn: by display_order
    assert all(g[1] == 0 and g[2] == 0 for g in got[4:])
    assert len(got) == len(get_rules())  # all registered rules, including the 0/0 rows


def test_rule_names_come_from_the_registry_and_unknown_rules_are_ignored(db: Session) -> None:
    add_scan(
        db,
        add_repo(db, 1),
        10,
        "2026-11-17",
        results={"tests": RuleStatus.fail, "removed_rule": RuleStatus.fail},
    )
    rows = build_summary(db, TODAY).rule_failures
    assert {r.rule_id for r in rows} == {r.id for r in get_rules()}
    assert next(r for r in rows if r.rule_id == "tests").rule_name == "Tests"


def test_not_applicable_and_error_results_are_not_failures(db: Session) -> None:
    add_scan(
        db,
        add_repo(db, 1),
        50,
        "2026-11-17",
        results={"dependencies": RuleStatus.not_applicable, "tests": RuleStatus.error},
    )
    assert all(r.fail == 0 and r.warn == 0 for r in build_summary(db, TODAY).rule_failures)


# ---- the 30-day trend ----------------------------------------------------------------------


def test_trend_covers_30_days_ending_today_and_omits_days_without_data(db: Session) -> None:
    add_scan(db, add_repo(db, 1), 70, "2026-11-10")
    points = trend(db)
    assert points[0] == ("2026-11-10", 70.0)
    assert points[-1] == ("2026-11-18", 70.0)  # carried forward to today
    assert len(points) == 9
    assert score_trend(db, date(2026, 11, 9)) == []  # nothing yet on that day


def test_trend_window_is_exactly_30_days_inclusive(db: Session) -> None:
    repo = add_repo(db, 1)
    add_scan(db, repo, 40, "2026-10-19")  # D-30: before the window -> the baseline
    points = trend(db)
    assert points[0] == ("2026-10-20", 40.0)  # D-29 is the first day
    assert len(points) == 30 and points[-1][0] == "2026-11-18"


def test_baseline_is_each_repos_latest_scan_before_the_window(db: Session) -> None:
    a, b = add_repo(db, 1), add_repo(db, 2)
    add_scan(db, a, 10, "2026-08-01")
    add_scan(db, a, 90, "2026-10-01")  # this is a's baseline, not the older 10
    add_scan(db, b, 50, "2026-09-15")
    assert trend(db)[0] == ("2026-10-20", 70.0)  # (90 + 50) / 2


def test_scores_are_carried_forward_between_scans(db: Session) -> None:
    a, b = add_repo(db, 1), add_repo(db, 2)
    add_scan(db, a, 60, "2026-10-25")
    add_scan(db, b, 100, "2026-11-01")
    add_scan(db, a, 80, "2026-11-10")
    by_date = dict(trend(db))
    assert by_date["2026-10-25"] == 60.0
    assert by_date["2026-10-31"] == 60.0  # b has no score yet
    assert by_date["2026-11-01"] == 80.0  # (60 + 100) / 2
    assert by_date["2026-11-09"] == 80.0  # carried, nothing new
    assert by_date["2026-11-10"] == 90.0  # a improved: (80 + 100) / 2
    assert by_date["2026-11-18"] == 90.0
    assert "2026-10-24" not in by_date  # no data -> omitted


def test_a_repo_only_counts_from_the_day_of_its_first_scan(db: Session) -> None:
    a, b = add_repo(db, 1), add_repo(db, 2)
    add_scan(db, a, 100, "2026-11-01")
    add_scan(db, b, 0, "2026-11-10")
    by_date = dict(trend(db))
    assert by_date["2026-11-09"] == 100.0 and by_date["2026-11-10"] == 50.0


def test_several_scans_on_one_day_use_the_latest_by_finished_at(db: Session) -> None:
    repo = add_repo(db, 1)
    add_scan(db, repo, 20, "2026-11-15", hour=9)
    add_scan(db, repo, 85, "2026-11-15", hour=18)
    add_scan(db, repo, 30, "2026-11-15", hour=11)  # inserted later but finished earlier
    assert dict(trend(db))["2026-11-15"] == 85.0


def test_a_null_score_scan_removes_the_repo_from_later_averages(db: Session) -> None:
    a, b = add_repo(db, 1), add_repo(db, 2)
    add_scan(db, a, 100, "2026-11-01")
    add_scan(db, b, 50, "2026-11-01")
    add_scan(db, b, None, "2026-11-10")  # b's latest scan has no score
    by_date = dict(trend(db))
    assert by_date["2026-11-09"] == 75.0 and by_date["2026-11-10"] == 100.0


def test_failed_scans_do_not_affect_the_trend(db: Session) -> None:
    repo = add_repo(db, 1)
    add_scan(db, repo, 70, "2026-11-01")
    add_scan(db, repo, None, "2026-11-10", status=ScanStatus.failed)
    assert dict(trend(db))["2026-11-12"] == 70.0


def test_scans_after_today_are_ignored(db: Session) -> None:
    add_scan(db, add_repo(db, 1), 70, "2026-11-19")
    assert trend(db) == []


def test_late_evening_scans_belong_to_their_utc_day(db: Session) -> None:
    repo = add_repo(db, 1)
    add_scan(db, repo, 70, "2026-11-17", hour=23)
    assert dict(trend(db)) == {"2026-11-17": 70.0, "2026-11-18": 70.0}


def test_the_last_trend_point_equals_the_current_average(db: Session) -> None:
    a, b, c = add_repo(db, 1), add_repo(db, 2), add_repo(db, 3)
    add_scan(db, a, 55, "2026-10-01")
    add_scan(db, a, 66, "2026-11-05")
    add_scan(db, b, 77, "2026-11-12")
    add_scan(db, c, None, "2026-11-14")
    summary = build_summary(db, TODAY)
    assert summary.score_trend[-1].average_score == summary.average_score == 71.5


def test_deleting_a_repo_removes_it_from_everything(db: Session) -> None:
    a, b = add_repo(db, 1), add_repo(db, 2)
    add_scan(db, a, 100, "2026-11-10")
    add_scan(db, b, 0, "2026-11-10")
    db.delete(b)
    db.commit()
    summary = build_summary(db, TODAY)
    assert summary.repo_count == 1 and summary.average_score == 100.0
    assert dict(trend(db))["2026-11-10"] == 100.0


# ---- the HTTP endpoint ---------------------------------------------------------------------


def test_endpoint_requires_login_and_works_for_viewers(
    client: TestClient, viewer_headers: dict[str, str]
) -> None:
    assert client.get("/api/v1/dashboard/summary").status_code == 401
    r = client.get("/api/v1/dashboard/summary", headers=viewer_headers)
    assert r.status_code == 200
    assert set(r.json()) == {
        "repo_count",
        "scanned_repo_count",
        "average_score",
        "grade_distribution",
        "rule_failures",
        "score_trend",
    }


def test_endpoint_uses_the_current_utc_date(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    now = datetime.now(UTC)
    repo = add_repo(db, 1)
    for days_ago, score in ((3, 40), (0, 90)):
        when = now - timedelta(days=days_ago)
        scan = Scan(
            repo_id=repo.id,
            status=ScanStatus.completed,
            trigger=ScanTrigger.manual,
            score=score,
            grade=grade_for(score),
            created_at=when,
            finished_at=when,
        )
        db.add(scan)
    db.commit()
    body = client.get("/api/v1/dashboard/summary", headers=admin_headers).json()
    assert body["repo_count"] == 1 and body["average_score"] == 90.0
    assert body["score_trend"][-1] == {"date": now.date().isoformat(), "average_score": 90.0}
    assert body["score_trend"][0]["average_score"] == 40.0
    assert body["grade_distribution"] == {"A": 1, "B": 0, "C": 0, "D": 0, "F": 0}
