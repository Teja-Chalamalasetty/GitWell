from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.models.user import User
from app.rules.registry import get_rules
from app.schemas.rule import RuleOut

router = APIRouter(tags=["rules"])


@router.get("/rules", response_model=list[RuleOut])
def list_rules(_user: User = Depends(get_current_user)) -> list[RuleOut]:
    return [
        RuleOut(
            id=r.id,
            name=r.name,
            description=r.description,
            weight=r.weight,
            display_order=r.display_order,
            fix_tip=r.fix_tip,
        )
        for r in get_rules()
    ]
