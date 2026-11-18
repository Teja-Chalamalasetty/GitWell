"""/metrics (PRD 14): access control, label hygiene, and the counters actually moving."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.metrics import Metrics, get_metrics
from app.github.ratelimit_state import rate_limit_state
from app.main import create_app
from app.models.user import UserRole
from tests.api.issue_world import V1
from tests.conftest import make_settings
from tests.fake_github import BARE_FILES, GOOD_FILES, FakeGitHub
from tests.helpers import InlineExecutor, login_as

TOKEN = "metrics-token-for-tests-123"


def scrape(client: TestClient, token: str | None = TOKEN) -> str:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.get("/metrics", headers=headers)
    assert r.status_code == 200, r.text
    return r.text


def sample(text: str, name: str, **labels: str) -> float | None:
    """The value of one series, or None if it does not exist."""
    wanted = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = re.fullmatch(r"([a-z_]+)(?:\{(.*)\})? (\S+)", line)
        if not m or m.group(1) != name:
            continue
        got = ",".join(sorted(m.group(2).split(","))) if m.group(2) else ""
        if got == wanted:
            return float(m.group(3))
    return None


@pytest.fixture
def metrics_app(start_app: Callable[..., TestClient]) -> TestClient:
    return start_app(metrics_enabled=True, metrics_token=TOKEN, github_token="tkn")


# ---- access control ------------------------------------------------------------------------


def test_metrics_is_a_404_by_default(client: TestClient) -> None:
    assert client.get("/metrics").status_code == 404
    assert client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_metrics_requires_the_bearer_token(metrics_app: TestClient) -> None:
    assert metrics_app.get("/metrics").status_code == 401
    for header in (
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
        {"Authorization": "Bearer "},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": f"Bearer {TOKEN[:-1]}"},
    ):
        r = metrics_app.get("/metrics", headers=header)
        assert r.status_code == 401, header
        assert r.headers["www-authenticate"] == "Bearer"
        assert "gitwell_" not in r.text


def test_the_right_token_returns_prometheus_text(metrics_app: TestClient) -> None:
    r = metrics_app.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "# TYPE gitwell_http_requests_total counter" in r.text


def test_a_user_jwt_does_not_open_metrics(metrics_app: TestClient) -> None:
    headers = login_as(metrics_app, "admin@example.com", UserRole.admin)
    assert metrics_app.get("/metrics", headers=headers).status_code == 401


def test_enabling_metrics_without_a_token_refuses_to_start() -> None:
    with pytest.raises(ValueError, match="METRICS_TOKEN"):
        make_settings(metrics_enabled=True)


def test_metrics_never_contain_secrets(metrics_app: TestClient, gh: FakeGitHub) -> None:
    headers = login_as(metrics_app, "admin@example.com", UserRole.admin)
    metrics_app.post(f"{V1}/repos", headers=headers, json={"url": "https://github.com/o/r"})
    text = scrape(metrics_app)
    for secret in (
        TOKEN,
        "tkn",
        "test-secret",
        "api.github.com",
        "codeload.github.com",
        "SHORTLIVED",
    ):
        assert secret not in text, secret


# ---- HTTP metrics --------------------------------------------------------------------------


def test_requests_are_counted_with_route_templates(metrics_app: TestClient) -> None:
    for _ in range(3):
        metrics_app.get("/api/v1/config")
    text = scrape(metrics_app)
    assert (
        sample(
            text, "gitwell_http_requests_total", method="GET", route="/api/v1/config", status="200"
        )
        == 3
    )
    assert (
        sample(
            text,
            "gitwell_http_request_duration_seconds_count",
            method="GET",
            route="/api/v1/config",
        )
        == 3
    )
    assert 'gitwell_http_request_duration_seconds_bucket{le="0.005"' in text


def test_labels_use_templates_never_real_ids_or_paths(metrics_app: TestClient) -> None:
    headers = login_as(metrics_app, "admin@example.com", UserRole.admin)
    for n in (1, 22, 333):
        metrics_app.get(f"{V1}/repos/{n}", headers=headers)  # 404s, but still the same template
    for n in range(40):
        metrics_app.get(f"/some/random/path/{n}")  # unmatched
    text = scrape(metrics_app)
    assert (
        sample(
            text,
            "gitwell_http_requests_total",
            method="GET",
            route="/api/v1/repos/{repo_id}",
            status="404",
        )
        == 3
    )
    assert "/repos/22" not in text and "/random/path" not in text
    routes = set(re.findall(r'route="([^"]*)"', text))
    assert "unmatched" in routes
    assert len([r for r in routes if "random" in r]) == 0  # 40 distinct paths, one series
    assert (
        sample(text, "gitwell_http_requests_total", method="GET", route="unmatched", status="404")
        == 40
    )


def test_statuses_are_recorded(metrics_app: TestClient) -> None:
    metrics_app.post(
        f"{V1}/auth/login", json={"email": "x@example.com", "password": "test-wrong-password"}
    )
    text = scrape(metrics_app)
    assert (
        sample(
            text,
            "gitwell_http_requests_total",
            method="POST",
            route="/api/v1/auth/login",
            status="401",
        )
        == 1
    )


# ---- scan, rule and GitHub metrics ---------------------------------------------------------


def test_a_scan_moves_the_scan_rule_and_github_counters(
    metrics_app: TestClient, gh: FakeGitHub
) -> None:
    headers = login_as(metrics_app, "admin@example.com", UserRole.admin)
    gh.repo.files = BARE_FILES
    metrics_app.post(
        f"{V1}/repos", headers=headers, json={"url": "https://github.com/o/r"}
    )  # on_add
    gh.repo.files = GOOD_FILES
    metrics_app.post(f"{V1}/repos/1/scan", headers=headers)  # manual
    gh.repo.empty = True
    metrics_app.post(f"{V1}/repos/1/scan", headers=headers)  # fails: REPO_EMPTY
    text = scrape(metrics_app)

    assert sample(text, "gitwell_scans_total", status="queued", trigger="on_add") == 1
    assert sample(text, "gitwell_scans_total", status="queued", trigger="manual") == 2
    assert sample(text, "gitwell_scans_total", status="completed", trigger="on_add") == 1
    assert sample(text, "gitwell_scans_total", status="completed", trigger="manual") == 1
    assert sample(text, "gitwell_scans_total", status="failed", trigger="manual") == 1
    assert (
        sample(text, "gitwell_scan_duration_seconds_count") == 3
    )  # one observation per finished scan

    # rule results over two completed scans: secrets passed twice; tests failed once, passed once
    assert sample(text, "gitwell_rule_results_total", rule_id="secrets", status="pass") == 2
    assert sample(text, "gitwell_rule_results_total", rule_id="tests", status="fail") == 1
    assert sample(text, "gitwell_rule_results_total", rule_id="tests", status="pass") == 1
    assert (
        sample(text, "gitwell_rule_results_total", rule_id="dependencies", status="not_applicable")
        == 1
    )

    # GitHub requests are labelled by route template and status
    assert (
        sample(
            text, "gitwell_github_requests_total", endpoint="/repos/{owner}/{repo}", status="200"
        )
        or 0
    ) >= 1
    assert (
        sample(
            text,
            "gitwell_github_requests_total",
            endpoint="/repos/{owner}/{repo}/tarball/{ref}",
            status="302",
        )
        or 0
    ) >= 1
    assert (
        sample(text, "gitwell_github_requests_total", endpoint="codeload tarball", status="200")
        or 0
    ) >= 1
    assert (
        sample(
            text,
            "gitwell_github_requests_total",
            endpoint="/repos/{owner}/{repo}/commits/{ref}",
            status="409",
        )
        or 0
    ) == 1
    assert all(
        "{" in e or e == "codeload tarball"
        for e in re.findall(r'gitwell_github_requests_total\{endpoint="([^"]*)"', text)
    )


def test_orphaned_scans_are_not_counted_as_finished(
    metrics_app: TestClient, gh: FakeGitHub
) -> None:
    from sqlalchemy import text as sql

    from app.db import SessionLocal

    headers = login_as(metrics_app, "admin@example.com", UserRole.admin)
    metrics_app.post(f"{V1}/repos", headers=headers, json={"url": "https://github.com/o/r"})

    def delete_repo() -> None:
        with SessionLocal() as s:
            s.execute(sql("DELETE FROM repos"))
            s.commit()

    gh.repo.on_commit_lookup = delete_repo
    metrics_app.post(f"{V1}/repos/1/scan", headers=headers)  # the repo vanishes mid-scan
    text = scrape(metrics_app)
    assert sample(text, "gitwell_scans_total", status="queued", trigger="manual") == 1
    assert sample(text, "gitwell_scans_total", status="completed", trigger="manual") is None
    assert sample(text, "gitwell_scans_total", status="failed", trigger="manual") is None


def test_github_transport_errors_are_counted(metrics_app: TestClient, gh: FakeGitHub) -> None:
    import httpx

    from app.github.client import GitHubClient
    from app.scanner.deadline import Deadline

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    gh_router = gh  # the fake registers catch-all routes; replace the API one for this client
    del gh_router
    client = GitHubClient("https://unreachable.example", None, sleep=lambda s: None)
    client._http = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(Exception):  # noqa: B017 - GitHubUnavailable after retries
        client.get_repo("o", "r", Deadline(30))
    assert get_metrics().github_requests.labels("/repos/{owner}/{repo}", "error")._value.get() == 3
    client.close()


# ---- the rate-limit gauge ------------------------------------------------------------------


def test_the_rate_limit_gauge_reflects_the_shared_state(metrics_app: TestClient) -> None:
    rate_limit_state.clear()
    assert math.isnan(sample(scrape(metrics_app), "gitwell_github_rate_limit_remaining") or 0.0)
    rate_limit_state.remaining = 4321
    rate_limit_state.reset_at = datetime.now(UTC) + timedelta(minutes=5)
    try:
        assert sample(scrape(metrics_app), "gitwell_github_rate_limit_remaining") == 4321
    finally:
        rate_limit_state.clear()


# ---- isolation -----------------------------------------------------------------------------


def test_each_app_starts_from_zero() -> None:
    first = create_app(
        make_settings(metrics_enabled=True, metrics_token=TOKEN), executor=InlineExecutor()
    )
    with TestClient(first) as c:
        c.get("/health")
        assert (
            sample(
                scrape(c),
                "gitwell_http_requests_total",
                method="GET",
                route="/health",
                status="200",
            )
            == 1
        )
    second = create_app(
        make_settings(metrics_enabled=True, metrics_token=TOKEN), executor=InlineExecutor()
    )
    with TestClient(second) as c:
        assert (
            sample(
                scrape(c),
                "gitwell_http_requests_total",
                method="GET",
                route="/health",
                status="200",
            )
            is None
        )


def test_metrics_object_is_swappable() -> None:
    from app.core.metrics import set_metrics

    fresh = Metrics()
    set_metrics(fresh)
    assert get_metrics() is fresh
