from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.ratelimit import AttemptLimiter
from app.main import create_app
from app.models.user import User, UserRole
from tests.conftest import make_settings
from tests.helpers import PASSWORD, auth, make_user

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"


def test_login_then_me(client: TestClient, db: Session) -> None:
    make_user(db, "Ann@Example.com", UserRole.admin)
    r = client.post(LOGIN, json={"email": "ANN@example.COM", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["user"]["email"] == "ann@example.com"
    assert body["user"]["role"] == "admin"
    assert body["user"]["created_at"].endswith("Z")
    assert "password" not in str(body)

    me = client.get(ME, headers=auth(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "ann@example.com"


def test_token_claims(client: TestClient, db: Session) -> None:
    user = make_user(db)
    token = client.post(LOGIN, json={"email": user.email, "password": PASSWORD}).json()[
        "access_token"
    ]
    claims = jwt.decode(token, "test-secret", algorithms=["HS256"])
    assert claims["sub"] == str(user.id)  # string, per spec
    assert claims["ver"] == 0
    assert {"iat", "exp"} <= set(claims)


@pytest.mark.parametrize(
    ("email", "password", "active"),
    [
        ("user@example.com", "test-wrong-password", True),  # wrong password
        ("nobody@example.com", PASSWORD, True),  # unknown email
        ("user@example.com", PASSWORD, False),  # inactive user
    ],
)
def test_bad_logins_all_look_the_same(
    client: TestClient, db: Session, email: str, password: str, active: bool
) -> None:
    make_user(db, "user@example.com", active=active)
    r = client.post(LOGIN, json={"email": email, "password": password})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert r.json()["error"]["message"] == "Invalid email or password."


def test_unknown_email_still_verifies_a_hash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3: unknown emails burn a dummy verification so timing matches."""
    calls: list[str] = []
    import app.api.auth as auth_api

    monkeypatch.setattr(auth_api, "burn_dummy_verify", lambda pw: calls.append(pw))
    client.post(LOGIN, json={"email": "nobody@example.com", "password": "test-whatever-pw"})
    assert calls == ["test-whatever-pw"]


@pytest.mark.parametrize(
    "header", [None, {"Authorization": "Bearer nonsense"}, {"Authorization": "Basic abc"}]
)
def test_me_requires_valid_token(client: TestClient, header: dict[str, str] | None) -> None:
    r = client.get(ME, headers=header)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


def _token(user: User, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(user.id),
        "ver": user.token_version,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, "test-secret", algorithm="HS256")


def test_expired_token_is_401(client: TestClient, db: Session) -> None:
    user = make_user(db)
    past = datetime.now(UTC) - timedelta(minutes=10)
    token = _token(user, iat=past - timedelta(minutes=60), exp=past)
    assert client.get(ME, headers=auth(token)).status_code == 401


def test_token_within_leeway_is_accepted(client: TestClient, db: Session) -> None:
    user = make_user(db)
    just_expired = datetime.now(UTC) - timedelta(seconds=10)
    token = _token(user, iat=just_expired - timedelta(hours=1), exp=just_expired)
    assert client.get(ME, headers=auth(token)).status_code == 200


def test_token_missing_required_claim_or_wrong_secret_or_alg(
    client: TestClient, db: Session
) -> None:
    user = make_user(db)
    no_exp = jwt.encode(
        {"sub": str(user.id), "ver": 0, "iat": datetime.now(UTC)}, "test-secret", algorithm="HS256"
    )
    wrong_secret = jwt.encode(
        {
            "sub": str(user.id),
            "ver": 0,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "other-secret-other-secret-xx",
        algorithm="HS256",
    )
    none_alg = jwt.encode(
        {
            "sub": str(user.id),
            "ver": 0,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        None,
        algorithm="none",
    )
    for token in (no_exp, wrong_secret, none_alg):
        assert client.get(ME, headers=auth(token)).status_code == 401


def test_deleted_or_inactive_user_token_rejected(client: TestClient, db: Session) -> None:
    user = make_user(db)
    token = _token(user)
    assert client.get(ME, headers=auth(token)).status_code == 200
    user.is_active = False
    db.commit()
    assert client.get(ME, headers=auth(token)).status_code == 401
    db.delete(user)
    db.commit()
    assert client.get(ME, headers=auth(token)).status_code == 401


def test_role_comes_from_db_not_token(client: TestClient, db: Session) -> None:
    user = make_user(db)  # viewer
    token = _token(user, role="admin")
    assert client.get("/api/v1/users", headers=auth(token)).status_code == 403


# ---- change password -----------------------------------------------------------------------


def test_change_password_invalidates_old_tokens(
    client: TestClient, db: Session, login: Callable[[str, str], str]
) -> None:
    make_user(db, "u@example.com")
    old = login("u@example.com")
    r = client.post(
        "/api/v1/auth/change-password",
        headers=auth(old),
        json={"current_password": PASSWORD, "new_password": "brand-new-pass-2"},
    )
    assert r.status_code == 204
    assert client.get(ME, headers=auth(old)).status_code == 401
    # old password no longer works; new one does
    assert (
        client.post(LOGIN, json={"email": "u@example.com", "password": PASSWORD}).status_code == 401
    )
    assert login("u@example.com", "brand-new-pass-2")


def test_change_password_wrong_current_is_not_401(
    client: TestClient, db: Session, login: Callable[[str, str], str]
) -> None:
    make_user(db, "u@example.com")
    r = client.post(
        "/api/v1/auth/change-password",
        headers=auth(login("u@example.com")),
        json={"current_password": "test-nope-nope-nope", "new_password": "brand-new-pass-2"},
    )
    assert r.status_code == 400  # a 401 would log the user out in the UI
    assert r.json()["error"]["code"] == "BAD_REQUEST"


@pytest.mark.parametrize("new_password", ["short", "x" * 129])
def test_change_password_length_rules(
    client: TestClient, db: Session, login: Callable[[str, str], str], new_password: str
) -> None:
    make_user(db, "u@example.com")
    r = client.post(
        "/api/v1/auth/change-password",
        headers=auth(login("u@example.com")),
        json={"current_password": PASSWORD, "new_password": new_password},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---- registration --------------------------------------------------------------------------


def test_register_disabled_by_default(client: TestClient) -> None:
    r = client.post("/api/v1/auth/register", json={"email": "n@example.com", "password": PASSWORD})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "REGISTRATION_DISABLED"


def test_register_enabled_creates_viewer_and_rejects_duplicates() -> None:
    app = create_app(make_settings(allow_registration=True))
    from app.db import Base, get_engine

    with TestClient(app) as c:
        Base.metadata.create_all(get_engine())
        body = {"email": "New@Example.com", "password": PASSWORD}
        r = c.post("/api/v1/auth/register", json=body)
        assert r.status_code == 201
        assert r.json()["role"] == "viewer"
        assert r.json()["email"] == "new@example.com"
        dup = c.post("/api/v1/auth/register", json=body)
        assert dup.status_code == 409
        assert dup.json()["error"]["code"] == "EMAIL_TAKEN"
        # role cannot be smuggled in
        evil = c.post(
            "/api/v1/auth/register",
            json={"email": "x@example.com", "password": PASSWORD, "role": "admin"},
        )
        assert evil.status_code == 422


def test_register_invalid_email_and_short_password() -> None:
    app = create_app(make_settings(allow_registration=True))
    from app.db import Base, get_engine

    with TestClient(app) as c:
        Base.metadata.create_all(get_engine())
        assert (
            c.post(
                "/api/v1/auth/register", json={"email": "nope", "password": PASSWORD}
            ).status_code
            == 422
        )
        assert (
            c.post(
                "/api/v1/auth/register", json={"email": "a@example.com", "password": "short"}
            ).status_code
            == 422
        )


def test_register_is_rate_limited_per_ip() -> None:
    app = create_app(make_settings(allow_registration=True))
    app.state.auth_limiters.register_ip = AttemptLimiter(limit=2)
    from app.db import Base, get_engine

    with TestClient(app) as c:
        Base.metadata.create_all(get_engine())
        for i in range(2):
            assert (
                c.post(
                    "/api/v1/auth/register",
                    json={"email": f"u{i}@example.com", "password": PASSWORD},
                ).status_code
                == 201
            )
        r = c.post("/api/v1/auth/register", json={"email": "u9@example.com", "password": PASSWORD})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"
        assert int(r.headers["Retry-After"]) > 0


# ---- login rate limiting -------------------------------------------------------------------


def test_sixth_failed_login_is_429_with_retry_after(client: TestClient, db: Session) -> None:
    make_user(db, "u@example.com")
    bad = {"email": "u@example.com", "password": "test-wrong-password"}
    for _ in range(5):
        assert client.post(LOGIN, json=bad).status_code == 401
    r = client.post(LOGIN, json=bad)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"
    assert 0 < int(r.headers["Retry-After"]) <= 15 * 60
    # even the right password is refused while limited (and no hashing is done)
    assert (
        client.post(LOGIN, json={"email": "u@example.com", "password": PASSWORD}).status_code == 429
    )


def test_limit_applies_before_hashing(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user(db, "u@example.com")
    bad = {"email": "u@example.com", "password": "test-wrong-password"}
    for _ in range(5):
        client.post(LOGIN, json=bad)
    import app.api.auth as auth_api

    def forbidden(*a: object, **k: object) -> bool:
        raise AssertionError("password was hashed/verified while rate limited")

    monkeypatch.setattr(auth_api, "verify_password", forbidden)
    monkeypatch.setattr(auth_api, "burn_dummy_verify", forbidden)
    assert client.post(LOGIN, json=bad).status_code == 429


def test_successful_login_resets_pair_counter(client: TestClient, db: Session) -> None:
    make_user(db, "u@example.com")
    bad = {"email": "u@example.com", "password": "test-wrong-password"}
    for _ in range(4):
        client.post(LOGIN, json=bad)
    assert (
        client.post(LOGIN, json={"email": "u@example.com", "password": PASSWORD}).status_code == 200
    )
    for _ in range(4):
        assert client.post(LOGIN, json=bad).status_code == 401


def test_per_ip_limit_across_emails(client: TestClient) -> None:
    for i in range(30):
        r = client.post(
            LOGIN, json={"email": f"u{i}@example.com", "password": "test-wrong-password"}
        )
        assert r.status_code == 401
    r = client.post(LOGIN, json={"email": "another@example.com", "password": "test-wrong-password"})
    assert r.status_code == 429


def test_forwarded_for_only_trusted_when_configured(db: Session) -> None:
    def run(trust: bool) -> list[int]:
        app = create_app(make_settings(trust_proxy=trust))
        from app.db import Base, get_engine

        with TestClient(app) as c:
            Base.metadata.create_all(get_engine())
            codes = []
            for i in range(6):
                r = c.post(
                    LOGIN,
                    json={"email": "u@example.com", "password": "test-wrong-password"},
                    headers={"X-Forwarded-For": f"10.0.0.{i}"},
                )
                codes.append(r.status_code)
            return codes

    assert run(trust=False)[-1] == 429  # header ignored: all attempts share one IP
    assert run(trust=True)[-1] == 401  # each spoofed IP gets its own counter
