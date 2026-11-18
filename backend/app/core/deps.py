"""Authentication and authorization dependencies (PRD S2, S4)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.errors import AppError
from app.core.ratelimit import AuthLimiters
from app.core.security import decode_access_token
from app.db import get_db
from app.github.client import GitHubClient
from app.models.user import User, UserRole

_bearer = HTTPBearer(auto_error=False)


def _unauthenticated(message: str = "Authentication required.") -> AppError:
    return AppError(401, "UNAUTHENTICATED", message, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if creds is None:
        raise _unauthenticated()
    try:
        claims = decode_access_token(creds.credentials, settings)
        user_id = int(claims["sub"])
        version = int(claims["ver"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise _unauthenticated("Invalid or expired token.") from None
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != version:
        raise _unauthenticated("Invalid or expired token.")
    return user


def require_role(role: str) -> Callable[..., User]:
    """Dependency factory. Authorization uses the role stored in the DB, never a claim."""
    needed = UserRole(role)

    def dependency(user: User = Depends(get_current_user)) -> User:
        if needed == UserRole.admin and user.role != UserRole.admin:
            raise AppError(403, "FORBIDDEN", "Administrator access required.")
        return user

    return dependency


# One shared instance so FastAPI evaluates it once per request.
require_admin = require_role("admin")


def get_limiters(request: Request) -> AuthLimiters:
    limiters: AuthLimiters = request.app.state.auth_limiters
    return limiters


def get_github_client(request: Request) -> Iterator[GitHubClient]:
    """A per-request GitHub client from the app's factory (tests inject their own)."""
    client: GitHubClient = request.app.state.github_client_factory()
    try:
        yield client
    finally:
        client.close()


def get_issue_client(request: Request) -> Iterator[GitHubClient]:
    """A client authenticated with the issues token (falls back to the read token)."""
    client: GitHubClient = request.app.state.issue_client_factory()
    try:
        yield client
    finally:
        client.close()
