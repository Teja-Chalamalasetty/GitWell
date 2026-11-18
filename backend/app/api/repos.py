from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_github_client, require_admin
from app.db import get_db
from app.github.client import GitHubClient
from app.models.scan import ScanStatus, ScanTrigger
from app.models.user import User
from app.schemas.common import ListResponse
from app.schemas.repo import RepoCreate, RepoSummary
from app.schemas.scan import ScanOut
from app.services import repo_service, scan_service

router = APIRouter(tags=["repos"])


@router.post("/repos", response_model=RepoSummary, status_code=201)
def add_repo(
    body: RepoCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: GitHubClient = Depends(get_github_client),
) -> RepoSummary:
    repo = repo_service.add_repo(db, client, body.url, admin)
    # The repo row is committed first; then the on_add scan is queued, committed and dispatched.
    scan = scan_service.queue_scan(db, repo.id, ScanTrigger.on_add, admin.id)
    scan_service.dispatch(request.app.state, scan.id)
    return repo_service.summarize(db, repo)


@router.get("/repos", response_model=ListResponse[RepoSummary])
def list_repos(
    sort: Literal["name", "score", "last_scanned"] = "name",
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ListResponse[RepoSummary]:
    items, total = repo_service.list_summaries(db, sort, order, limit, offset)
    return ListResponse[RepoSummary](items=items, total=total)


@router.get("/repos/{repo_id}", response_model=RepoSummary)
def get_repo(
    repo_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RepoSummary:
    return repo_service.summarize(db, repo_service.get_repo(db, repo_id))


@router.delete("/repos/{repo_id}", status_code=204)
def delete_repo(
    repo_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    repo_service.delete_repo(db, repo_id)
    return Response(status_code=204)


@router.post("/repos/{repo_id}/scan", response_model=ScanOut, status_code=202)
def trigger_scan(
    repo_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ScanOut:
    repo_service.get_repo(db, repo_id)  # 404 first; the unique index still decides the race
    scan = scan_service.queue_scan(db, repo_id, ScanTrigger.manual, admin.id)
    queued = ScanOut.model_validate(scan)  # serialised BEFORE a worker can change the row
    scan_service.dispatch(request.app.state, scan.id)
    return queued


@router.get("/repos/{repo_id}/scans", response_model=ListResponse[ScanOut])
def list_repo_scans(
    repo_id: int,
    status: ScanStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ListResponse[ScanOut]:
    items, total = scan_service.list_scans(db, repo_id, status, limit, offset)
    return ListResponse[ScanOut](items=[ScanOut.model_validate(s) for s in items], total=total)
