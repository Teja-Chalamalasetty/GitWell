from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.schemas.user import Password, UserOut, _normalize_email


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: Password

    _lower = field_validator("email", mode="after")(_normalize_email)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Plain str: login must not leak email-format rules, and must not 422 on odd input.
    email: str
    password: str

    _lower = field_validator("email", mode="after")(_normalize_email)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: Password
