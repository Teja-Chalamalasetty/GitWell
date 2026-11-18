"""App factory, middleware, routers and lifespan (PRD §6, §14)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

import anyio.to_thread
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.api import auth as auth_api
from app.api import config as config_api
from app.api import dashboard as dashboard_api
from app.api import health as health_api
from app.api import issue_drafts as issue_drafts_api
from app.api import metrics as metrics_api
from app.api import repos as repos_api
from app.api import rules as rules_api
from app.api import scans as scans_api
from app.api import users as users_api
from app.config import VERSION, Settings, get_settings
from app.core.errors import API_PREFIX, register_error_handlers
from app.core.logging import configure_logging
from app.core.metrics import Metrics, set_metrics
from app.core.middleware import RequestIdMiddleware
from app.core.ratelimit import AuthLimiters
from app.db import SessionLocal, init_engine
from app.github.client import GitHubClient
from app.github.errors import register_github_handlers
from app.rules.base import Rule
from app.rules.registry import get_rules
from app.scanner.worker import ScanRunner
from app.scheduler import SchedulerService
from app.services.scan_service import dispatch, fail_queued_scans, recover_interrupted
from app.services.user_service import bootstrap_admin

logger = logging.getLogger("gitwell")


def create_app(
    settings: Settings | None = None,
    *,
    executor: Any | None = None,
    github_client_factory: Callable[[], GitHubClient] | None = None,
    rules_provider: Callable[[], Sequence[Rule]] | None = None,
) -> FastAPI:
    """Build the app. `executor`, `github_client_factory` and `rules_provider` are for tests."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    set_metrics(Metrics())  # every app starts from zero
    dev = settings.env == "development"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Sync endpoints run in AnyIO's threadpool; raise the limit (PRD §6).
        anyio.to_thread.current_default_thread_limiter().total_tokens = 40
        workers = os.environ.get("WEB_CONCURRENCY", "1").strip()
        if workers not in ("", "1"):
            # Recovery, executor, scheduler and rate limiters all assume ONE process (PRD 6).
            logger.warning(
                "multiple_workers_unsupported: run exactly one process (uvicorn --workers 1)",
                extra={"web_concurrency": workers},
            )
        if not settings.github_token and not settings.github_issues_token:
            logger.warning("no_github_token: anonymous GitHub access is limited to 60/hour")
        init_engine(settings.database_url)
        try:
            with SessionLocal() as db:
                recover_interrupted(db)  # PRD 8.1: queued/running -> failed, posting -> approved
        except SQLAlchemyError:
            logger.error("startup_recovery_skipped: schema missing? run `alembic upgrade head`")
        if settings.admin_email or settings.admin_password:
            with SessionLocal() as db:
                bootstrap_admin(db, settings)
        if settings.scheduler_enabled:
            app.state.scheduler.start()
        try:
            yield
        finally:
            # PRD 14, in order: stop the scheduler, stop the executor accepting work, mark scans
            # that never started as interrupted, then tell running scans to abort within seconds.
            app.state.scheduler.stop()
            app.state.executor.shutdown(wait=False, cancel_futures=True)
            try:
                with SessionLocal() as db:
                    fail_queued_scans(db)
            except SQLAlchemyError:
                logger.error("shutdown_cleanup_failed")
            app.state.scan_runner.expire_all()

    app = FastAPI(
        title="GitWell",
        version=VERSION,
        lifespan=lifespan,
        docs_url="/docs" if dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.auth_limiters = AuthLimiters()
    app.state.github_client_factory = github_client_factory or (
        lambda: GitHubClient(settings.github_api_url, settings.github_token)
    )
    # Posting issues may use a separate token (Issues: read & write); it is never used to scan.
    app.state.issue_client_factory = lambda: GitHubClient(
        settings.github_api_url, settings.github_issues_token or settings.github_token
    )
    app.state.executor = executor or ThreadPoolExecutor(
        max_workers=settings.scan_workers, thread_name_prefix="scan"
    )
    app.state.scan_runner = ScanRunner(
        settings, SessionLocal, app.state.github_client_factory, rules_provider or get_rules
    )
    app.state.scheduler = SchedulerService(
        settings, SessionLocal, lambda scan_id: dispatch(app.state, scan_id)
    )
    register_error_handlers(app)
    register_github_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    # Added last so it is outermost: every response gets a request id.
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health_api.router)
    if settings.metrics_enabled:  # disabled = the route does not exist (404)
        app.include_router(metrics_api.router)
    v1 = APIRouter(prefix=API_PREFIX)
    v1.include_router(config_api.router)
    v1.include_router(auth_api.router)
    v1.include_router(users_api.router)
    v1.include_router(rules_api.router)
    v1.include_router(repos_api.router)
    v1.include_router(scans_api.router)
    v1.include_router(dashboard_api.router)
    v1.include_router(issue_drafts_api.router)
    app.include_router(v1)
    return app


app = create_app()
