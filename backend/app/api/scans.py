from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db import get_db
from app.models.user import User
from app.schemas.scan import ScanDetail
from app.services import scan_service

router = APIRouter(tags=["scans"])


@router.get("/scans/{scan_id}", response_model=ScanDetail)
def get_scan(
    scan_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScanDetail:
    return scan_service.get_scan_detail(db, scan_id)
