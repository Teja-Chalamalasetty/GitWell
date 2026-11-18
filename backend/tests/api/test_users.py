from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from tests.helpers import PASSWORD, auth, make_user

USERS = "/api/v1/users"
ME = "/api/v1/auth/me"


def test_create_user_and_duplicate_email(client: TestClient, admin_headers: dict[str, str]) -> None:
    body = {"email": "New@Example.com", "password": PASSWORD, "role": "viewer"}
    r = client.post(USERS, headers=admin_headers, json=body)
    assert r.status_code == 201
    assert r.json()["email"] == "new@example.com"
    assert r.json()["role"] == "viewer"
    assert "password" not in r.text

    dup = client.post(USERS, headers=admin_headers, json={**body, "email": "NEW@example.com"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "EMAIL_TAKEN"


def test_create_admin_role_allowed_for_admin(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    r = client.post(
        USERS,
        headers=admin_headers,
        json={"email": "a2@example.com", "password": PASSWORD, "role": "admin"},
    )
    assert r.status_code == 201 and r.json()["role"] == "admin"


def test_create_user_validation(client: TestClient, admin_headers: dict[str, str]) -> None:
    for body in (
        {"email": "bad", "password": PASSWORD, "role": "viewer"},
        {"email": "a@example.com", "password": "short", "role": "viewer"},
        {"email": "a@example.com", "password": PASSWORD, "role": "root"},
    ):
        r = client.post(USERS, headers=admin_headers, json=body)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_users_paginates(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    for i in range(4):
        make_user(db, f"u{i}@example.com")
    r = client.get(USERS, headers=admin_headers, params={"limit": 2, "offset": 1})
    body = r.json()
    assert r.status_code == 200
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert client.get(USERS, headers=admin_headers, params={"limit": 500}).status_code == 422


# ---- PATCH / lockout protection ------------------------------------------------------------


def test_admin_can_change_another_users_role_and_it_invalidates_their_token(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    login: Callable[[str, str], str],
) -> None:
    viewer = make_user(db, "v@example.com")
    token = login("v@example.com")
    assert client.get(ME, headers=auth(token)).status_code == 200

    r = client.patch(f"{USERS}/{viewer.id}", headers=admin_headers, json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert client.get(ME, headers=auth(token)).status_code == 401  # token_version bumped
    # a fresh login reflects the new role
    assert client.get(USERS, headers=auth(login("v@example.com"))).status_code == 200


def test_deactivating_a_user_blocks_login_and_old_tokens(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    login: Callable[[str, str], str],
) -> None:
    viewer = make_user(db, "v@example.com")
    token = login("v@example.com")
    r = client.patch(f"{USERS}/{viewer.id}", headers=admin_headers, json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get(ME, headers=auth(token)).status_code == 401
    bad = client.post("/api/v1/auth/login", json={"email": "v@example.com", "password": PASSWORD})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_noop_patch_does_not_bump_token_version(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    viewer = make_user(db, "v@example.com")
    r = client.patch(
        f"{USERS}/{viewer.id}", headers=admin_headers, json={"role": "viewer", "is_active": True}
    )
    assert r.status_code == 200
    db.refresh(viewer)
    assert viewer.token_version == 0


def test_demoting_or_deactivating_the_last_admin_is_409(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    admin = db.query(User).filter_by(email="admin@example.com").one()
    for body in ({"role": "viewer"}, {"is_active": False}):
        r = client.patch(f"{USERS}/{admin.id}", headers=admin_headers, json=body)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "LAST_ADMIN"
    db.refresh(admin)
    assert admin.role == UserRole.admin and admin.is_active


def test_admin_cannot_change_own_role_even_with_other_admins(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    make_user(db, "second@example.com", UserRole.admin)
    me = db.query(User).filter_by(email="admin@example.com").one()
    for body in ({"role": "viewer"}, {"is_active": False}):
        r = client.patch(f"{USERS}/{me.id}", headers=admin_headers, json=body)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "INVALID_STATE"


def test_with_two_admins_one_can_demote_the_other_but_then_is_last(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    other = make_user(db, "second@example.com", UserRole.admin)
    me = db.query(User).filter_by(email="admin@example.com").one()
    r = client.patch(f"{USERS}/{other.id}", headers=admin_headers, json={"role": "viewer"})
    assert r.status_code == 200
    # now the caller is the last active admin
    r = client.patch(f"{USERS}/{me.id}", headers=admin_headers, json={"role": "viewer"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LAST_ADMIN"


def test_inactive_admin_does_not_count_as_an_active_admin(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    ghost = make_user(db, "ghost@example.com", UserRole.admin, active=False)
    me = db.query(User).filter_by(email="admin@example.com").one()
    # Only one *active* admin exists, so self-demotion is the last-admin case.
    r = client.patch(f"{USERS}/{me.id}", headers=admin_headers, json={"role": "viewer"})
    assert r.json()["error"]["code"] == "LAST_ADMIN"
    # Demoting an already-inactive admin removes nobody active, so it is allowed.
    r = client.patch(f"{USERS}/{ghost.id}", headers=admin_headers, json={"role": "viewer"})
    assert r.status_code == 200


def test_patch_unknown_user_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.patch(f"{USERS}/9999", headers=admin_headers, json={"role": "admin"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_patch_rejects_unknown_fields_and_bad_values(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    v = make_user(db, "v@example.com")
    assert (
        client.patch(
            f"{USERS}/{v.id}", headers=admin_headers, json={"email": "x@example.com"}
        ).status_code
        == 422
    )
    assert (
        client.patch(f"{USERS}/{v.id}", headers=admin_headers, json={"role": "root"}).status_code
        == 422
    )
