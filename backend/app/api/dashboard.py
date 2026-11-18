from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db import get_db
from app.models.user import User
from app.schemas.dashboard import DashboardSummary
from app.services import dashboard_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> DashboardSummary:
    return dashboard_service.build_summary(db)
