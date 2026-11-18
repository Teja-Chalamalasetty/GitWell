from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import VERSION, Settings, get_settings

router = APIRouter()


class PublicConfig(BaseModel):
    allow_registration: bool
    github_token_configured: bool
    version: str


@router.get("/config", response_model=PublicConfig)
def public_config(s: Settings = Depends(get_settings)) -> PublicConfig:
    return PublicConfig(
        allow_registration=s.allow_registration,
        github_token_configured=bool(s.github_issues_token or s.github_token),
        version=VERSION,
    )
