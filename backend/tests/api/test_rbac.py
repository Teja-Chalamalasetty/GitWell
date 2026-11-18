"""RBAC matrix (PRD S4): every endpoint x anonymous / viewer / admin.

Later milestones add their endpoints to MATRIX; `test_matrix_covers_every_route` fails if a
route is added without being classified here.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings

V1 = "/api/v1"

# (method, path template, concrete path, json body, minimum role)
#   role: "public" | "any" (any logged-in user) | "admin"
MATRIX: list[tuple[str, str, str, dict[str, Any] | None, str]] = [
    ("GET", "/config", "/config", None, "public"),
    (
        "POST",
        "/auth/register",
        "/auth/register",
        {"email": "r@example.com", "password": "test-pw-pw-pw"},
        "public",
    ),
    (
        "POST",
        "/auth/login",
        "/auth/login",
        {"email": "x@example.com", "password": "test-nope-nope"},
        "public",
    ),
    ("GET", "/auth/me", "/auth/me", None, "any"),
    (
        "POST",
        "/auth/change-password",
        "/auth/change-password",
        {"current_password": "wrong-wrong", "new_password": "new-pass-1234"},
        "any",
    ),
    ("GET", "/users", "/users", None, "admin"),
    (
        "POST",
        "/users",
        "/users",
        {"email": "n@example.com", "password": "test-pw-pw-pw", "role": "viewer"},
        "admin",
    ),
    ("PATCH", "/users/{user_id}", "/users/999", {"is_active": True}, "admin"),
    ("GET", "/rules", "/rules", None, "any"),
    # "not-a-url" is rejected by the service (422) before any GitHub call is made.
    ("POST", "/repos", "/repos", {"url": "not-a-url"}, "admin"),
    ("GET", "/repos", "/repos", None, "any"),
    ("GET", "/repos/{repo_id}", "/repos/999", None, "any"),
    ("DELETE", "/repos/{repo_id}", "/repos/999", None, "admin"),
    ("POST", "/repos/{repo_id}/scan", "/repos/999/scan", None, "admin"),
    ("GET", "/repos/{repo_id}/scans", "/repos/999/scans", None, "any"),
    ("GET", "/scans/{scan_id}", "/scans/999", None, "any"),
    ("GET", "/dashboard/summary", "/dashboard/summary", None, "any"),
    ("POST", "/scans/{scan_id}/issue-draft", "/scans/999/issue-draft", None, "admin"),
    ("GET", "/issue-drafts", "/issue-drafts", None, "any"),
    ("GET", "/issue-drafts/{draft_id}", "/issue-drafts/999", None, "any"),
    ("PATCH", "/issue-drafts/{draft_id}", "/issue-drafts/999", {"title": "t"}, "admin"),
    ("POST", "/issue-drafts/{draft_id}/approve", "/issue-drafts/999/approve", None, "admin"),
    ("POST", "/issue-drafts/{draft_id}/discard", "/issue-drafts/999/discard", None, "admin"),
]


def _call(
    client: TestClient, method: str, path: str, body: Any, headers: dict[str, str] | None
) -> int:
    return client.request(method, V1 + path, json=body, headers=headers).status_code


@pytest.mark.parametrize(
    ("method", "template", "path", "body", "role"), MATRIX, ids=[f"{m} {t}" for m, t, *_ in MATRIX]
)
def test_matrix(
    client: TestClient,
    admin_headers: dict[str, str],
    viewer_headers: dict[str, str],
    method: str,
    template: str,
    path: str,
    body: Any,
    role: str,
) -> None:
    anon = _call(client, method, path, body, None)
    viewer = _call(client, method, path, body, viewer_headers)
    admin = _call(client, method, path, body, admin_headers)

    if role == "public":
        # Public routes may still 403 for business reasons (REGISTRATION_DISABLED);
        # what must never happen is an auth failure.
        r = client.request(method, V1 + path, json=body)
        code = r.json().get("error", {}).get("code") if r.status_code >= 400 else None
        assert code not in ("UNAUTHENTICATED", "FORBIDDEN")
    else:
        assert anon == 401
        assert admin not in (401, 403)
        if role == "admin":
            assert viewer == 403
        else:
            assert viewer not in (401, 403)


def test_viewer_gets_forbidden_error_shape(
    client: TestClient, viewer_headers: dict[str, str]
) -> None:
    r = client.get(f"{V1}/users", headers=viewer_headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_matrix_covers_every_route() -> None:
    app = create_app(make_settings())
    actual = {
        (method.upper(), path.removeprefix(V1))
        for path, ops in app.openapi()["paths"].items()
        if path.startswith(V1)
        for method in ops
    }
    declared = {(m, t) for m, t, *_ in MATRIX}
    assert actual == declared, f"unclassified: {actual - declared}, stale: {declared - actual}"
