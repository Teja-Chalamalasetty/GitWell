from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, field_validator

from app.models.user import UserRole
from app.schemas.common import ORMModel, UTCDatetime

Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]


def _normalize_email(value: str) -> str:
    return value.strip().lower()


class UserOut(ORMModel):
    id: int
    email: str
    role: UserRole
    is_active: bool
    created_at: UTCDatetime


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: Password
    role: UserRole = UserRole.viewer

    _lower = field_validator("email", mode="after")(_normalize_email)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole | None = None
    is_active: bool | None = None
