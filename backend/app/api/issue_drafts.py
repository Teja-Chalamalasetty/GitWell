from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.deps import get_current_user, get_issue_client, require_admin
from app.db import get_db
from app.github.client import GitHubClient
from app.models.issue_draft import DraftStatus
from app.models.user import User
from app.schemas.common import ListResponse
from app.schemas.issue_draft import IssueDraftOut, IssueDraftUpdate
from app.services import issue_service

router = APIRouter(tags=["issue-drafts"])


@router.post("/scans/{scan_id}/issue-draft", response_model=IssueDraftOut, status_code=201)
def create_issue_draft(
    scan_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> IssueDraftOut:
    return issue_service.create_draft(db, admin, scan_id)


@router.get("/issue-drafts", response_model=ListResponse[IssueDraftOut])
def list_issue_drafts(
    repo_id: int | None = None,
    status: DraftStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ListResponse[IssueDraftOut]:
    items, total = issue_service.list_drafts(db, repo_id, status, limit, offset)
    return ListResponse[IssueDraftOut](items=items, total=total)


@router.get("/issue-drafts/{draft_id}", response_model=IssueDraftOut)
def get_issue_draft(
    draft_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IssueDraftOut:
    return issue_service.get_draft(db, draft_id)


@router.patch("/issue-drafts/{draft_id}", response_model=IssueDraftOut)
def update_issue_draft(
    draft_id: int,
    body: IssueDraftUpdate,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> IssueDraftOut:
    return issue_service.update_draft(db, draft_id, body.title, body.body)


@router.post("/issue-drafts/{draft_id}/approve", response_model=IssueDraftOut)
def approve_issue_draft(
    draft_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    client: GitHubClient = Depends(get_issue_client),
) -> IssueDraftOut:
    return issue_service.approve(db, settings, client, draft_id, admin)


@router.post("/issue-drafts/{draft_id}/discard", response_model=IssueDraftOut)
def discard_issue_draft(
    draft_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> IssueDraftOut:
    return issue_service.discard_draft(db, draft_id)
