"""Application settings (PRD §19). All values come from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "1.0.0"

_EMPTY_AS_NONE = (
    "admin_email",
    "admin_password",
    "github_token",
    "github_issues_token",
    "metrics_token",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./gitwell.db"
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60
    admin_email: str | None = None
    admin_password: str | None = None
    allow_registration: bool = False
    trust_proxy: bool = False
    github_token: str | None = None
    github_issues_token: str | None = None
    github_api_url: str = "https://api.github.com"
    scan_workers: int = 2
    scan_timeout_seconds: int = 120
    max_archive_mb: int = 50
    max_uncompressed_mb: int = 500
    max_files: int = 100000
    max_file_bytes: int = 524288
    max_text_total_mb: int = 200
    api_snapshot_max_fetches: int = 40
    scheduler_enabled: bool = True
    scheduler_tick_minutes: int = 15
    rescan_interval_hours: int = 24
    scheduler_max_per_tick: int = 10
    cors_origins: str = "http://localhost:5173"
    metrics_enabled: bool = False
    metrics_token: str | None = None
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        # Empty strings (e.g. from a copied .env.example) behave like "unset".
        for name in _EMPTY_AS_NONE:
            if getattr(self, name) == "":
                setattr(self, name, None)
        if self.env != "test" and len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET is required and must be at least 32 characters")
        if self.env == "test" and not self.jwt_secret:
            raise ValueError("JWT_SECRET is required")
        if self.admin_password is not None and len(self.admin_password) < 8:
            raise ValueError("ADMIN_PASSWORD must be at least 8 characters")
        if self.metrics_enabled and not self.metrics_token:
            raise ValueError("METRICS_TOKEN is required when METRICS_ENABLED=true")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
