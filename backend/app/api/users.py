from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import require_admin
from app.db import get_db
from app.models.user import User
from app.schemas.common import ListResponse
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=ListResponse[UserOut])
def list_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ListResponse[UserOut]:
    items, total = user_service.list_users(db, limit, offset)
    return ListResponse[UserOut](items=[UserOut.model_validate(u) for u in items], total=total)


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    return user_service.create_user(db, body.email, body.password, body.role)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    return user_service.update_user(db, admin, user_id, body.role, body.is_active)
