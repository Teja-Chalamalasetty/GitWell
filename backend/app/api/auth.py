from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.deps import get_current_user, get_limiters
from app.core.errors import AppError
from app.core.ratelimit import AuthLimiters, client_ip, too_many_attempts
from app.core.security import burn_dummy_verify, create_access_token, verify_password
from app.db import get_db
from app.models.user import User, UserRole
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
)
from app.schemas.user import UserOut
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiters: AuthLimiters = Depends(get_limiters),
) -> User:
    if not settings.allow_registration:
        raise AppError(403, "REGISTRATION_DISABLED", "Self-registration is disabled.")
    ip = client_ip(request, settings)
    # Limit before any password hashing (S12). Every attempt counts.
    retry = limiters.register_ip.retry_after(ip)
    if retry is not None:
        raise too_many_attempts(retry)
    limiters.register_ip.record(ip)
    return user_service.create_user(db, body.email, body.password, UserRole.viewer)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiters: AuthLimiters = Depends(get_limiters),
) -> TokenResponse:
    ip = client_ip(request, settings)
    pair_key = f"{ip}|{body.email}"
    # Limits are checked before any password hashing (S12).
    retry = max(
        (
            r
            for r in (
                limiters.login_ip_email.retry_after(pair_key),
                limiters.login_ip.retry_after(ip),
            )
            if r is not None
        ),
        default=None,
    )
    if retry is not None:
        raise too_many_attempts(retry)

    user = user_service.get_by_email(db, body.email)
    if user is None:
        burn_dummy_verify(body.password)  # equalise timing for unknown emails (S3)
        ok = False
    else:
        ok = verify_password(body.password, user.password_hash) and user.is_active

    if not ok or user is None:
        limiters.login_ip_email.record(pair_key)
        limiters.login_ip.record(ip)
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid email or password.")

    limiters.login_ip_email.reset(pair_key)
    return TokenResponse(
        access_token=create_access_token(user, settings),
        expires_in=settings.jwt_expire_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    user_service.change_password(db, user, body.current_password, body.new_password)
    return Response(status_code=204)
