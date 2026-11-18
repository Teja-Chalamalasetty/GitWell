"""User management: creation, role/active changes, password changes, bootstrap admin."""

from __future__ import annotations

import logging

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.models.user import User, UserRole

logger = logging.getLogger("gitwell.users")


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.lower()))


def create_user(db: Session, email: str, password: str, role: UserRole) -> User:
    email = email.lower()
    if get_by_email(db, email) is not None:
        raise _email_taken()
    user = User(email=email, password_hash=hash_password(password), role=role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _email_taken() from None
    return user


def _email_taken() -> AppError:
    return AppError(409, "EMAIL_TAKEN", "A user with this email already exists.")


def list_users(db: Session, limit: int, offset: int) -> tuple[list[User], int]:
    total = db.scalar(select(func.count()).select_from(User)) or 0
    items = db.scalars(select(User).order_by(User.id).limit(limit).offset(offset)).all()
    return list(items), total


def count_active_admins(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.admin, User.is_active.is_(True))
        )
        or 0
    )


def update_user(
    db: Session,
    actor: User,
    user_id: int,
    role: UserRole | None,
    is_active: bool | None,
) -> User:
    """Change a user's role / active flag, never leaving zero active admins (PRD 4)."""
    target = db.get(User, user_id)
    if target is None:
        raise AppError(404, "NOT_FOUND", "User not found.")

    role_changes = role is not None and role != target.role
    active_changes = is_active is not None and is_active != target.is_active
    if not (role_changes or active_changes):
        return target

    admins = count_active_admins(db)
    if target.id == actor.id:
        # Admins cannot change their own role or active flag.
        if admins <= 1:
            raise AppError(409, "LAST_ADMIN", "Cannot change the last active administrator.")
        raise AppError(409, "INVALID_STATE", "You cannot change your own role or active status.")

    removes_admin = (
        target.role == UserRole.admin
        and target.is_active
        and ((role_changes and role != UserRole.admin) or (active_changes and not is_active))
    )
    if removes_admin and admins <= 1:
        raise AppError(409, "LAST_ADMIN", "Cannot remove the last active administrator.")

    if role_changes and role is not None:
        target.role = role
    if active_changes and is_active is not None:
        target.is_active = is_active
    target.token_version += 1  # invalidates the user's existing tokens
    db.commit()
    return target


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        # 400, not 401: the UI treats any 401 as "session expired" and logs the user out.
        raise AppError(400, "BAD_REQUEST", "Current password is incorrect.")
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    db.commit()


def bootstrap_admin(db: Session, settings: Settings) -> None:
    """Create the configured admin if absent. Never modifies an existing user."""
    email, password = settings.admin_email, settings.admin_password
    if not email and not password:
        return
    if not (email and password):
        logger.warning("bootstrap_admin_skipped: set both ADMIN_EMAIL and ADMIN_PASSWORD")
        return
    try:
        email = TypeAdapter(EmailStr).validate_python(email).lower()
    except ValidationError as exc:
        raise ValueError("ADMIN_EMAIL is not a valid email address") from exc
    if get_by_email(db, email) is not None:
        return
    create_user(db, email, password, UserRole.admin)
    logger.info("bootstrap_admin_created")
