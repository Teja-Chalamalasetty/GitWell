"""Password hashing and JWT helpers (PRD S1-S3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.config import Settings
from app.models.user import User

ALGORITHM = "HS256"
_hasher = PasswordHash.recommended()  # Argon2


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password, password_hash)
    except Exception:
        return False


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash("gitwell-dummy-password")


def burn_dummy_verify(password: str) -> None:
    """Spend the same time as a real verification (S3: equal timing for unknown emails)."""
    verify_password(password, _dummy_hash())


def create_access_token(user: User, settings: Settings, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user.id),
        "ver": user.token_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Raises jwt.PyJWTError on any problem."""
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[ALGORITHM],
        options={"require": ["exp", "iat", "sub"]},
        leeway=30,
    )
