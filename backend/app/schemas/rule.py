from __future__ import annotations

from pydantic import BaseModel


class RuleOut(BaseModel):
    id: str
    name: str
    description: str
    weight: int
    display_order: int
    fix_tip: str
